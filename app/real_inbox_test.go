package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestRealInbox runs the plugin's own inbox (plugin/bin/signoff, in a home of its own) through the app's steps:
// the home from the launcher, the start, the lock look, a start link from the control socket, inbox.json and
// state.json. It keeps the Go side in step with signoff.py.
func TestRealInbox(t *testing.T) {
	launcher, err := filepath.Abs(filepath.Join("..", "plugin", "bin", "signoff"))
	if err != nil {
		t.Fatal(err)
	}
	dir := shortDir(t)
	home := filepath.Join(dir, "home")
	t.Setenv("SIGNOFF_INBOX_HOME", home)
	t.Setenv("XDG_CACHE_HOME", filepath.Join(dir, "cache")) // no Uptick files of the user's
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(dir, "config"))

	got, err := signoffHome(launcher)
	if err != nil || got != home {
		t.Fatalf("home %q, err %v", got, err)
	}
	if inboxAlive(home) {
		t.Fatal("alive before the start")
	}
	t.Cleanup(func() { stopInbox(home) })
	link, port, err := openInbox(launcher, home, "") // starts it
	if err != nil {
		t.Fatalf("no link: %v", err)
	}
	if err := checkLink(link, port); err != nil {
		t.Fatal(err)
	}
	if !inboxAlive(home) {
		t.Fatal("not alive while the inbox runs")
	}
	if id := inboxServer(home); len(id) != 32 {
		t.Fatalf("server_id %q", id)
	}
	if n, err := waiting(home); err != nil || n != 0 {
		t.Fatalf("waiting %d, err %v", n, err)
	}
	again, port2, err := openInbox(launcher, home, "") // finds it running
	if err != nil || port2 != port || again == link {
		t.Fatalf("second open: port %d, same link %v, err %v", port2, again == link, err)
	}
	if err := startInbox(launcher, home); err != nil { // a second serve finds the lock held and gives up
		t.Fatal(err)
	}
	time.Sleep(time.Second)
	if _, _, err := askInbox(home, ""); err != nil || !inboxAlive(home) {
		t.Fatalf("the first inbox is gone: %v", err)
	}
	appTarget(t, launcher, home, dir)
	answerFromANotification(t, launcher, home, dir)
}

// appTarget keeps a panel row's numbers as the app's target, as `signoff inbox open --app` does (over the socket
// here: the command runs `open -a`, which would start the installed app against the user's own home), and takes it
// as the app does when it comes up: that item's start link once, then none, so the app shows what it showed. A
// stale target gives the list. Expiry (APP_TARGET_TTL, a minute) is proved in-process in test_inbox.py.
func appTarget(t *testing.T, launcher, home, dir string) {
	project := filepath.Join(dir, "project")
	folder := filepath.Join(project, "topic")
	if err := os.MkdirAll(filepath.Join(project, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(folder, 0o700); err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(folder, "round-01.json"), `{"version": 1, "title": "Two questions", "items": [
		{"id": "first", "title": "First?", "select": "one", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]},
		{"id": "second", "title": "Second?", "select": "one", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}]}`)
	waiter := exec.Command(launcher, filepath.Join(folder, "round-01.json"), "--no-open")
	if err := waiter.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		waiter.Process.Signal(os.Interrupt) // it notes its end, so the inbox lets go of its round at once
		waiter.Wait()
	}()
	var rows struct {
		Rounds []struct{ Run, Round int } `json:"rounds"`
		Inbox  int                        `json:"inbox"`
	}
	for deadline := time.Now().Add(20 * time.Second); len(rows.Rounds) == 0; time.Sleep(200 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("the waiter's round was never listed")
		}
		out, _ := exec.Command(launcher, "inbox", "rows", folder).Output()
		json.Unmarshal(out, &rows)
	}
	keep := func(inbox int) {
		t.Helper()
		line := fmt.Sprintf("{\"op\": \"app_target\", \"run\": %d, \"round\": %d, \"item\": 1, \"inbox\": %d}\n",
			rows.Rounds[0].Run, rows.Rounds[0].Round, inbox)
		if reply := rawControl(t, home, line); reply != "{\"ok\": true}\n" {
			t.Fatalf("app_target: %q", reply)
		}
	}
	keep(rows.Inbox)
	link, port, err := pendingLink(home)
	if err != nil || checkLink(link, port) != nil {
		t.Fatalf("no start link for the target: %v", err)
	}
	if to := landing(t, link); !strings.HasSuffix(to, fmt.Sprintf("/run/%d/#focus=0/second", rows.Rounds[0].Run)) {
		t.Fatalf("the target's link goes to %q", to)
	}
	if link, _, err := pendingLink(home); link != "" || err != nil { // used once
		t.Fatalf("a second take: link %v, err %v", link != "", err)
	}
	plain, _, err := openInbox(launcher, home, "") // what the app then does: the list
	if err != nil || strings.Contains(landing(t, plain), "/run/") {
		t.Fatalf("the plain open: %v", err)
	}
	keep(rows.Inbox%(1<<31-1) + 1) // another inbox's number, as after a restart
	if link, _, err := pendingLink(home); err != nil || strings.Contains(landing(t, link), "/run/") {
		t.Fatalf("a stale target: %v", err)
	}
}

// rawControl sends one line to the control socket as it is (control() sends strings only) and returns the reply.
func rawControl(t *testing.T, home, line string) string {
	t.Helper()
	conn, err := net.DialTimeout("unix", filepath.Join(home, "inbox.sock"), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(5 * time.Second))
	conn.Write([]byte(line))
	reply, _ := bufio.NewReader(conn).ReadString('\n')
	return reply
}

// landing is where a start link's first use redirects: the page's path and hash.
func landing(t *testing.T, link string) string {
	t.Helper()
	client := http.Client{Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
	}}
	resp, err := client.Get(link)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusSeeOther {
		t.Fatalf("start link: %d", resp.StatusCode)
	}
	return resp.Header.Get("Location")
}

// answerFromANotification runs a waiter on a round of one item with a recommendation, takes the inbox's offer for it,
// opens it, and accepts it: the waiter wakes as on the page's Done.
func answerFromANotification(t *testing.T, launcher, home, dir string) {
	folder := filepath.Join(dir, "topic")
	if err := os.Mkdir(folder, 0o700); err != nil {
		t.Fatal(err)
	}
	round := filepath.Join(folder, "round-01.json")
	write(t, round, `{"version": 1, "title": "Pick the settings layout", "items": [{"id": "layout",
		"title": "Two columns or one?", "select": "one", "recommended": "two",
		"options": [{"id": "two", "label": "Two columns"}, {"id": "one", "label": "Keep one"}]}]}`)
	var out bytes.Buffer
	waiter := exec.Command(launcher, round, "--no-open")
	waiter.Stdout = &out
	if err := waiter.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if waiter.ProcessState == nil {
			waiter.Process.Kill()
			waiter.Wait()
		}
	})
	sock := filepath.Join(home, "inbox.sock")
	var o *offer
	for deadline := time.Now().Add(20 * time.Second); o == nil; time.Sleep(200 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("the waiter's round was never offered")
		}
		o, _ = controlOffer(sock) // the waiter registers itself first
	}
	n := noteFor(o, "offer-1")
	if n.Title != "topic · Pick the settings layout" || n.Body != "Two columns or one?\nRecommended: Two columns" {
		t.Fatalf("title %q, body %q", n.Title, n.Body)
	}
	port, err := inboxPort(home)
	if err != nil {
		t.Fatal(err)
	}
	link, err := controlOpen(sock, o.ID)
	if err != nil || checkLink(link, port) != nil {
		t.Fatalf("no start link for the offer: %v", err)
	}
	if err := controlAccept(sock, o.ID); err != nil {
		t.Fatal(err)
	}
	if err := controlAccept(sock, o.ID); err == nil {
		t.Fatal("an offer accepted twice")
	}
	done := make(chan error, 1)
	go func() { done <- waiter.Wait() }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("the waiter: %v", err)
		}
	case <-time.After(20 * time.Second):
		t.Fatal("the waiter did not wake")
	}
	real, err := filepath.EvalSymlinks(folder) // /tmp is a link on macOS; the run prints the real path
	if err != nil {
		t.Fatal(err)
	}
	answers := filepath.Join(real, "round-01.answers.json")
	if out.String() != answers+"\n" {
		t.Fatalf("stdout %q", out.String())
	}
	var saved struct {
		Done    bool `json:"done"`
		Answers map[string]struct {
			Choice string `json:"choice"`
			Source string `json:"source"`
		} `json:"answers"`
	}
	if err := readJSON(answers, &saved); err != nil {
		t.Fatal(err)
	}
	if a := saved.Answers["layout"]; !saved.Done || a.Choice != "two" || a.Source != "notification" {
		t.Fatalf("saved %+v", saved)
	}
}

// stopInbox asks the inbox to stop and waits until it has let go of its lock, before the folder goes.
func stopInbox(home string) {
	conn, err := net.DialTimeout("unix", filepath.Join(home, "inbox.sock"), time.Second)
	if err != nil {
		return
	}
	conn.SetDeadline(time.Now().Add(5 * time.Second))
	conn.Write([]byte("{\"op\": \"stop\"}\n"))
	conn.Read(make([]byte, 64))
	conn.Close()
	for deadline := time.Now().Add(5 * time.Second); inboxAlive(home) && time.Now().Before(deadline); {
		time.Sleep(50 * time.Millisecond)
	}
}
