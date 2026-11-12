package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// launcher is the plugin's signoff command, set at build time (build.sh: -ldflags "-X main.launcher=..."), since
// an app started from the Dock sees no shell PATH. Empty (SIGNOFF_LAUNCHER=link ./build.sh, or a bare `go build`):
// the app runs what <home>/launcher points to, the link the plugin's SessionStart hook keeps (linkedLauncher).
var launcher = ""

const (
	maxReply  = 1 << 16          // a reply line: an offer's four texts, JSON-escaped, pass the inbox's 4 KiB request cap
	maxJSON   = 1 << 20          // inbox.json and state.json are a few hundred bytes; anything this big is not ours
	startWait = 15 * time.Second // as `signoff inbox open` waits for a fresh inbox
)

var controlWait = 5 * time.Second // the inbox's own limit for a control connection; a var for the tests

var startPath = regexp.MustCompile(`^/b/[A-Za-z0-9_-]{16,128}/$`)

// findLauncher returns the launcher set at build time, once it is there and can run.
func findLauncher(path string) (string, error) {
	if !filepath.IsAbs(path) {
		return "", fmt.Errorf("the signoff command %s is not an absolute path", path)
	}
	st, err := os.Stat(path)
	if err != nil || !st.Mode().IsRegular() || st.Mode()&0o111 == 0 {
		return "", fmt.Errorf("the signoff command %s is not there or cannot run; is the plugin still there?", path)
	}
	return path, nil
}

var errNoLink = errors.New("the app does not know where the Signoff plugin is yet: with the app in /Applications " +
	"or ~/Applications, start a Claude Code session with the Signoff plugin installed from its marketplace once, " +
	"then open the app again. A plugin run from a git checkout keeps no link: build the app with app/build.sh instead")

// appHome is the Signoff home as the plugin's inbox_home() has it on macOS. The app works it out only to find the
// launcher link; the command found must then name the same home (resolveLauncher).
func appHome() (string, error) {
	if home := os.Getenv("SIGNOFF_INBOX_HOME"); home != "" {
		if !filepath.IsAbs(home) {
			return "", fmt.Errorf("the Signoff home %s is not an absolute path", home)
		}
		return home, nil
	}
	user, err := os.UserHomeDir()
	if err != nil || !filepath.IsAbs(user) {
		return "", errors.New("there is no home folder to find the Signoff home in")
	}
	return filepath.Join(user, "Library", "Application Support", "signoff"), nil
}

// linkedLauncher returns what <home>/launcher points to: an absolute link to a regular, executable file of the
// user's or root's, in a home of the user's own. A missing or dangling link (a plugin update moves the plugin
// until the next session start) gets the sentence that says how to make one.
func linkedLauncher(home string) (string, error) {
	if err := ownFolder(home); err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return "", errNoLink
		}
		return "", err
	}
	link := filepath.Join(home, "launcher")
	target, err := os.Readlink(link)
	if errors.Is(err, fs.ErrNotExist) {
		return "", errNoLink
	}
	if err != nil || !filepath.IsAbs(target) {
		return "", fmt.Errorf("%s is not a link to an absolute path", link)
	}
	path, err := filepath.EvalSymlinks(link)
	if errors.Is(err, fs.ErrNotExist) {
		return "", errNoLink
	}
	var st os.FileInfo
	if err == nil {
		st, err = os.Stat(path)
	}
	if err != nil {
		return "", fmt.Errorf("%s points to no command the app can check", link)
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !st.Mode().IsRegular() || st.Mode()&0o111 == 0 || !ok || (int(sys.Uid) != os.Getuid() && sys.Uid != 0) {
		return "", fmt.Errorf("%s points to %s, which is not a command of yours that can run", link, path)
	}
	return path, nil
}

// resolveLauncher finds the plugin's signoff command and asks it for the home: the fixed launcher when the build
// has one, else the home's link. That command must name the home its link is in, or the app would look at one
// home and start the inbox of another.
func resolveLauncher(fixed string) (string, string, error) {
	launcher, want, err := "", "", error(nil)
	if fixed != "" {
		launcher, err = findLauncher(fixed)
	} else if want, err = appHome(); err == nil {
		launcher, err = linkedLauncher(want)
	}
	if err != nil {
		return "", "", err
	}
	home, err := signoffHome(launcher)
	if err != nil {
		return "", "", err
	}
	if want != "" && filepath.Clean(home) != filepath.Clean(want) {
		return "", "", fmt.Errorf("the signoff command %s names the Signoff home %s, not %s", launcher, home, want)
	}
	return launcher, home, nil
}

// signoffHome asks the plugin for the Signoff home (AD-7), the one resolver.
func signoffHome(launcher string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, launcher, "inbox", "home").Output()
	if err != nil {
		return "", fmt.Errorf("signoff inbox home failed (%v)", err)
	}
	home := strings.TrimSuffix(string(out), "\n")
	if !filepath.IsAbs(home) || strings.ContainsAny(home, "\r\n\x00") {
		return "", errors.New("signoff inbox home gave no absolute path")
	}
	return home, nil
}

// inboxAlive says whether an inbox holds inbox.lock, the plugin's own way (inbox_alive): take the lock for a moment
// and let go. A starting inbox tries for about half a second, so a look never keeps it out. A run looking at the
// same moment can seem to hold the lock, so only a lock held at three looks in a row counts.
func inboxAlive(home string) bool {
	for i := 0; i < 3; i++ {
		fd, err := syscall.Open(filepath.Join(home, "inbox.lock"), syscall.O_RDWR|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
		if err != nil {
			return false
		}
		err = syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			syscall.Flock(fd, syscall.LOCK_UN)
		}
		syscall.Close(fd)
		if err != syscall.EWOULDBLOCK {
			return false
		}
		if i < 2 {
			time.Sleep(50 * time.Millisecond)
		}
	}
	return true
}

// startInbox starts `signoff inbox serve` in a session of its own, as `signoff inbox open` does: it outlives the
// app, and its messages (never a link) go to logs/inbox.log in the home.
func startInbox(launcher, home string) error {
	if err := os.MkdirAll(home, 0o700); err != nil {
		return err
	}
	// As the inbox does: a home of the user's own made by hand may be open to others; it is closed first.
	if st, err := os.Lstat(home); err == nil && st.IsDir() {
		if sys, ok := st.Sys().(*syscall.Stat_t); ok && int(sys.Uid) == os.Getuid() {
			os.Chmod(home, 0o700)
		}
	}
	if err := ownFolder(home); err != nil { // before anything is written in it
		return err
	}
	logs := filepath.Join(home, "logs")
	if err := os.Mkdir(logs, 0o700); err != nil && !os.IsExist(err) {
		return err
	}
	out, err := os.OpenFile(filepath.Join(logs, "inbox.log"), os.O_WRONLY|os.O_APPEND|os.O_CREATE|syscall.O_NOFOLLOW, 0o600)
	if err != nil {
		return err
	}
	defer out.Close()
	cmd := exec.Command(launcher, "inbox", "serve")
	cmd.Stdout, cmd.Stderr = out, out
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	closeOnExec() // the inbox outlives the app: none of the app's files go with it (as close_fds does)
	if err := cmd.Start(); err != nil {
		return err
	}
	go cmd.Wait() // reaps it should it end while the app runs
	return nil
}

// closeOnExec marks every open descriptor past stderr close-on-exec: Go's own are, but AppKit's and WebKit's may
// not be, and exec on macOS hands on the rest.
func closeOnExec() {
	names, err := os.ReadDir("/dev/fd")
	if err != nil {
		return
	}
	for _, name := range names {
		if fd, err := strconv.Atoi(name.Name()); err == nil && fd > 2 {
			syscall.CloseOnExec(fd)
		}
	}
}

// openInbox starts the inbox when no inbox holds its lock, then asks it for a start link, as `signoff inbox open`
// does: with an offer id, to that offer's item (the inbox gives the list's for one it no longer has). It returns the
// link and the inbox's port.
func openInbox(launcher, home, offer string) (string, int, error) {
	if !inboxAlive(home) {
		if err := startInbox(launcher, home); err != nil {
			return "", 0, fmt.Errorf("cannot start the inbox (%v)", err)
		}
	}
	deadline := time.Now().Add(startWait)
	for {
		link, port, err := askInbox(home, offer)
		if err == nil {
			return link, port, nil
		}
		if time.Now().After(deadline) {
			return "", 0, fmt.Errorf("the inbox does not answer (%v); see %s", err, filepath.Join(home, "logs", "inbox.log"))
		}
		time.Sleep(100 * time.Millisecond)
	}
}

// askInbox checks the home and inbox.json before it asks the control socket for a start link, then checks the link.
// A file written halfway through a restart fails the port check, and the caller asks again.
func askInbox(home, offer string) (string, int, error) {
	return checkedLink(home, func(sock string) (string, error) { return controlOpen(sock, offer) })
}

// pendingLink asks the running inbox for the start link to the target `signoff inbox open --app` left for this app,
// with askInbox's checks: "" (and no error) when none waits.
func pendingLink(home string) (string, int, error) {
	link, port, err := checkedLink(home, controlPending)
	if err == errNone {
		return "", 0, nil
	}
	return link, port, err
}

func checkedLink(home string, ask func(sock string) (string, error)) (string, int, error) {
	if err := ownFolder(home); err != nil {
		return "", 0, err
	}
	port, err := inboxPort(home)
	if err != nil {
		return "", 0, err
	}
	link, err := ask(filepath.Join(home, "inbox.sock"))
	if err != nil {
		return "", 0, err
	}
	if err := checkLink(link, port); err != nil {
		return "", 0, err
	}
	return link, port, nil
}

// ownFolder holds the home to the inbox's own rule: a folder of the user's own, not a link, closed to others. Only
// then are its socket and files the user's inbox and not someone else's.
func ownFolder(home string) error {
	st, err := os.Lstat(home)
	if err != nil {
		return err
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !st.IsDir() || !ok || int(sys.Uid) != os.Getuid() || st.Mode().Perm()&0o077 != 0 {
		return fmt.Errorf("%s is not a folder of your own, closed to others", home)
	}
	return nil
}

// control sends one JSON line to the inbox's control socket and reads the one line back (AD-9). Its errors hold
// neither the request nor the reply: an offer id or a link must not reach a message.
func control(sock string, req map[string]string) ([]byte, error) {
	conn, err := net.DialTimeout("unix", sock, controlWait)
	if err != nil {
		return nil, errors.New("the inbox's control socket does not answer")
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(controlWait))
	data, _ := json.Marshal(req) // a map of strings always marshals
	if _, err := conn.Write(append(data, '\n')); err != nil {
		return nil, errors.New("the inbox's control socket took no request")
	}
	line, err := bufio.NewReader(io.LimitReader(conn, maxReply)).ReadBytes('\n')
	if err != nil {
		return nil, errors.New("the inbox gave no answer line")
	}
	return line, nil
}

// controlOpen asks the inbox's control socket for a new start link; with an offer id, one to that offer's item.
func controlOpen(sock, offer string) (string, error) {
	req := map[string]string{"op": "open"}
	if offer != "" {
		req["offer"] = offer
	}
	line, err := control(sock, req)
	if err != nil {
		return "", err
	}
	var reply struct {
		Link *string `json:"link"`
	}
	if err := json.Unmarshal(line, &reply); err != nil || reply.Link == nil {
		return "", errors.New("the inbox gave no link")
	}
	return *reply.Link, nil
}

var errNone = errors.New("no target waits for the app")

// controlPending takes the app's pending target from the inbox (app_open): its start link, to that item in Focus or
// to the list when it is gone. errNone when none waits, and from an inbox from before app targets (its answer is an
// error): the app then does what it did before.
func controlPending(sock string) (string, error) {
	line, err := control(sock, map[string]string{"op": "app_open"})
	if err != nil {
		return "", err
	}
	var reply map[string]json.RawMessage
	if json.Unmarshal(line, &reply) != nil {
		return "", errors.New("the inbox gave no answer this app knows")
	}
	if _, old := reply["error"]; old {
		return "", errNone
	}
	raw, ok := reply["link"]
	if ok && string(raw) == "null" {
		return "", errNone
	}
	var link string
	if !ok || json.Unmarshal(raw, &link) != nil || link == "" {
		return "", errors.New("the inbox gave no link")
	}
	return link, nil
}

// offer is an item a notification may answer, as the inbox names it: its id and four texts, one line each.
type offer struct {
	ID       string `json:"id"`
	Project  string `json:"project"`
	Round    string `json:"round"`
	Question string `json:"question"`
	Answer   string `json:"answer"`
}

var offerID = regexp.MustCompile(`^[0-9a-f]{32}$`) // the inbox's OFFER_ID

// controlOffer asks the inbox for an offer: nil when it has none or gives one this app does not know.
func controlOffer(sock string) (*offer, error) {
	line, err := control(sock, map[string]string{"op": "offer"})
	if err != nil {
		return nil, err
	}
	var reply struct {
		Offer *offer `json:"offer"`
	}
	if err := json.Unmarshal(line, &reply); err != nil {
		return nil, errors.New("the inbox gave no offer")
	}
	o := reply.Offer
	if o == nil || !offerID.MatchString(o.ID) || o.Project == "" || o.Round == "" || o.Question == "" || o.Answer == "" {
		return nil, nil
	}
	return o, nil
}

// controlAccept asks the inbox to save an offer's recommendation with Done. Any reply but {"ok": true} is a refusal
// (gone, changed, held, done, failed): the caller opens the window on the item instead.
func controlAccept(sock, id string) error {
	line, err := control(sock, map[string]string{"op": "accept", "offer": id})
	if err != nil {
		return err
	}
	var reply struct {
		OK bool `json:"ok"`
	}
	if json.Unmarshal(line, &reply) != nil || !reply.OK {
		return errors.New("the inbox did not take the answer")
	}
	return nil
}

// inboxPort reads inbox.json and returns the inbox's port, once its schema is one this app knows.
func inboxPort(home string) (int, error) {
	var info struct {
		Schema *int `json:"schema"`
		Port   int  `json:"port"`
	}
	if err := readJSON(filepath.Join(home, "inbox.json"), &info); err != nil {
		return 0, err
	}
	if info.Schema == nil || *info.Schema != 1 {
		return 0, errors.New("inbox.json has a schema this app does not know; update the app")
	}
	if info.Port < 1 || info.Port > 65535 {
		return 0, errors.New("inbox.json has no port")
	}
	return info.Port, nil
}

// inboxServer returns the server_id in inbox.json, new at each inbox start, or "" when there is none: a page
// token from another inbox, on the same port or not, opens nothing.
func inboxServer(home string) string {
	var info struct {
		ServerID string `json:"server_id"`
	}
	if readJSON(filepath.Join(home, "inbox.json"), &info) != nil {
		return ""
	}
	return info.ServerID
}

// checkLink makes sure a start link points at the inbox and nowhere else. Its error never holds the link.
func checkLink(link string, port int) error {
	u, err := url.Parse(link)
	if err != nil || u.Scheme != "http" || u.Host != fmt.Sprintf("127.0.0.1:%d", port) || u.User != nil ||
		u.Opaque != "" || u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" || !startPath.MatchString(u.Path) {
		return errors.New("the inbox gave a link that is not its own start link")
	}
	return nil
}

// waiting reads the count in state.json (AGENTS.md: numbers only, schema 1).
func waiting(home string) (int, error) {
	var state struct {
		Schema  *int `json:"schema"`
		Waiting *int `json:"waiting"`
	}
	if err := readJSON(filepath.Join(home, "state.json"), &state); err != nil {
		return 0, err
	}
	if state.Schema == nil || *state.Schema != 1 || state.Waiting == nil || *state.Waiting < 0 {
		return 0, errors.New("state.json is not one this app knows")
	}
	return *state.Waiting, nil
}

func readJSON(path string, v any) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, maxJSON+1))
	if err != nil {
		return err
	}
	if len(data) > maxJSON {
		return fmt.Errorf("%s is too big", filepath.Base(path))
	}
	if err := json.Unmarshal(data, v); err != nil {
		return fmt.Errorf("%s is not JSON this app knows", filepath.Base(path))
	}
	return nil
}

// rise remembers the last trusted count and says when a new one is higher: one notification per rise. The first
// trusted count is the baseline; an unknown count (no inbox) keeps the last one.
type rise struct {
	last  int
	known bool
}

func (r *rise) next(n int, ok bool) bool {
	if !ok {
		return false
	}
	up := r.known && n > r.last
	r.last, r.known = n, true
	return up
}
