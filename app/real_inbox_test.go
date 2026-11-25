package main

import (
	"bufio"
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

	got, err := signoffHome(launcher)
	if err != nil || got != home {
		t.Fatalf("home %q, err %v", got, err)
	}
	if inboxAlive(home) {
		t.Fatal("alive before the start")
	}
	t.Cleanup(func() { stopInbox(home) })
	link, port, err := openInbox(launcher, home) // starts it
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
	again, port2, err := openInbox(launcher, home) // finds it running
	if err != nil || port2 != port || again == link {
		t.Fatalf("second open: port %d, same link %v, err %v", port2, again == link, err)
	}
	if err := startInbox(launcher, home); err != nil { // a second serve finds the lock held and gives up
		t.Fatal(err)
	}
	time.Sleep(time.Second)
	if _, _, err := askInbox(home); err != nil || !inboxAlive(home) {
		t.Fatalf("the first inbox is gone: %v", err)
	}
	oldAppOps(t, launcher, home, dir)
}

// oldAppOps sends what a 0.4.0 panel's App press and its app's start sent, app_target with the numbers of a live
// round's item and then app_open, and gets the unknown-request error for both, with no link; the app's plain open
// then gives the list, as it does when it comes up.
func oldAppOps(t *testing.T, launcher, home, dir string) {
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
	target := fmt.Sprintf(`"run": %d, "round": %d, "item": 1, "inbox": %d`, rows.Rounds[0].Run, rows.Rounds[0].Round, rows.Inbox)
	for _, line := range []string{"{\"op\": \"app_target\", " + target + "}\n", "{\"op\": \"app_open\"}\n"} {
		if reply := rawControl(t, home, line); !strings.HasPrefix(reply, "{\"error\": \"unknown request") || strings.Contains(reply, "http") {
			t.Fatalf("%s: %q", strings.TrimSpace(line), reply)
		}
	}
	plain, _, err := openInbox(launcher, home)
	if err != nil || strings.Contains(landing(t, plain), "/run/") {
		t.Fatalf("the plain open: %v", err)
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
