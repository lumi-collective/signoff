package main

import (
	"go/ast"
	"go/parser"
	"go/token"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// shortDir is a temporary folder short enough for a Unix socket path (104 bytes on macOS); t.TempDir() under
// /var/folders can be longer.
func shortDir(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("/tmp", "so9a.")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	return dir
}

// fakeControl answers one connection on dir/inbox.sock with reply, after reading the request line into got.
func fakeControl(t *testing.T, dir, reply string, got chan<- string) string {
	t.Helper()
	sock := filepath.Join(dir, "inbox.sock")
	l, err := net.Listen("unix", sock)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Close() })
	go func() {
		conn, err := l.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		buf := make([]byte, 4096)
		n, _ := conn.Read(buf)
		if got != nil {
			got <- string(buf[:n])
		}
		if reply != "" {
			conn.Write([]byte(reply))
		} else {
			time.Sleep(2 * time.Second) // says nothing
		}
	}()
	return sock
}

func TestControlOpenSendsOneLineAndReadsTheLink(t *testing.T) {
	got := make(chan string, 1)
	sock := fakeControl(t, shortDir(t), "{\"link\": \"http://127.0.0.1:5/b/abc/\"}\n", got)
	link, err := controlOpen(sock)
	if err != nil || link != "http://127.0.0.1:5/b/abc/" {
		t.Fatalf("link %q, err %v", link, err)
	}
	if req := <-got; req != "{\"op\":\"open\"}\n" {
		t.Fatalf("request %q", req)
	}
}

func TestControlOpenRefusesBadAnswers(t *testing.T) {
	for name, reply := range map[string]string{
		"junk":       "not json\n",
		"no newline": "{\"link\": \"http://127.0.0.1:5/b/abc/\"}",
		"error":      "{\"error\": \"unknown request\"}\n",
		"not object": "[\"http://127.0.0.1:5/b/abc/\"]\n",
		"link null":  "{\"link\": null}\n",
		"link num":   "{\"link\": 5}\n",
		"too long":   "{\"link\": \"" + strings.Repeat("a", maxReply) + "\"}\n",
	} {
		t.Run(name, func(t *testing.T) {
			sock := fakeControl(t, shortDir(t), reply, nil)
			if link, err := controlOpen(sock); err == nil {
				t.Fatalf("no error, link %q", link)
			}
		})
	}
}

func TestControlOpenTimesOut(t *testing.T) {
	old := controlWait
	controlWait = 300 * time.Millisecond
	t.Cleanup(func() { controlWait = old })
	sock := fakeControl(t, shortDir(t), "", nil)
	began := time.Now()
	if _, err := controlOpen(sock); err == nil {
		t.Fatal("no error from a silent inbox")
	}
	if took := time.Since(began); took > time.Second {
		t.Fatalf("took %v", took)
	}
}

// The app asks the inbox for one thing, a start link: coming up (its start, a Dock click, `open -a`) sends no other
// op, such as the app_open an inbox no longer answers.
func TestTheAppSendsOnlyOpen(t *testing.T) {
	files, _ := filepath.Glob("*.go")
	ops, checked := map[string]bool{}, 0
	for _, name := range files {
		if strings.HasSuffix(name, "_test.go") {
			continue
		}
		f, err := parser.ParseFile(token.NewFileSet(), name, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		checked++
		ast.Inspect(f, func(n ast.Node) bool {
			if kv, ok := n.(*ast.KeyValueExpr); ok {
				if k, ok := kv.Key.(*ast.BasicLit); ok && k.Value == `"op"` {
					v, _ := kv.Value.(*ast.BasicLit)
					if v == nil {
						t.Errorf("%s: an op that is not a literal", name)
					} else {
						ops[v.Value] = true
					}
				}
			}
			if lit, ok := n.(*ast.BasicLit); ok && strings.Contains(lit.Value, "app_") {
				t.Errorf("%s: %s", name, lit.Value)
			}
			return true
		})
	}
	if checked == 0 || len(ops) != 1 || !ops[`"open"`] {
		t.Fatalf("ops %v in %d files", ops, checked)
	}
}

func TestControlOpenWithoutInbox(t *testing.T) {
	if _, err := controlOpen(filepath.Join(shortDir(t), "inbox.sock")); err == nil {
		t.Fatal("no error without a socket")
	}
}

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestInboxPort(t *testing.T) {
	cases := map[string]struct {
		text string
		port int
	}{
		"ok":         {`{"schema": 1, "server_id": "x", "port": 53111, "control": "/x/inbox.sock"}`, 53111},
		"newer":      {`{"schema": 2, "port": 53111}`, 0},
		"no schema":  {`{"port": 53111}`, 0},
		"port 0":     {`{"schema": 1, "port": 0}`, 0},
		"port text":  {`{"schema": 1, "port": "53111"}`, 0},
		"port big":   {`{"schema": 1, "port": 70000}`, 0},
		"junk":       {`port=53111`, 0},
		"not object": {`[1]`, 0},
		"too big":    {`{"schema": 1, "port": 53111, "x": "` + strings.Repeat("a", maxJSON) + `"}`, 0},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			dir := t.TempDir()
			write(t, filepath.Join(dir, "inbox.json"), c.text)
			port, err := inboxPort(dir)
			if port != c.port || (err == nil) != (c.port != 0) {
				t.Fatalf("port %d, err %v", port, err)
			}
		})
	}
	if _, err := inboxPort(t.TempDir()); err == nil {
		t.Fatal("no error for a missing inbox.json")
	}
}

func TestCheckLink(t *testing.T) {
	tok := "Abc_def-ghijklmnopqrstuvwxyz0123456789ABCDE"
	ok := "http://127.0.0.1:53111/b/" + tok + "/"
	if err := checkLink(ok, 53111); err != nil {
		t.Fatalf("refused its own link: %v", err)
	}
	for _, bad := range []string{
		"http://127.0.0.1:53112/b/" + tok + "/",
		"https://127.0.0.1:53111/b/" + tok + "/",
		"http://localhost:53111/b/" + tok + "/",
		"http://evil.example/b/" + tok + "/",
		"http://u@127.0.0.1:53111/b/" + tok + "/",
		"http://127.0.0.1:53111/" + tok + "/",
		"http://127.0.0.1:53111/b/" + tok + "/?x=1",
		"http://127.0.0.1:53111/b/" + tok + "/#x",
		"http://127.0.0.1:53111/b/" + tok + "/../x/",
		"http://127.0.0.1:53111/b/%2e%2e/",
		"http://127.0.0.1:53111/b/short/",
		"javascript:alert(1)",
		"",
	} {
		err := checkLink(bad, 53111)
		if err == nil {
			t.Errorf("took %q", bad)
		} else if strings.Contains(err.Error(), tok) {
			t.Errorf("the error holds the link: %v", err)
		}
	}
}

func TestWaiting(t *testing.T) {
	cases := map[string]struct {
		text  string
		n     int
		backs int
		ok    bool
	}{
		"ok":         {`{"schema": 1, "at": "2026-11-07T10:00:00Z", "waiting": 3, "projects": []}`, 3, -1, true},
		"zero":       {`{"schema": 1, "waiting": 0}`, 0, -1, true},
		"backs":      {`{"schema": 1, "waiting": 3, "projects": [], "backs": 2}`, 3, 2, true},
		"backs zero": {`{"schema": 1, "waiting": 3, "backs": 0}`, 3, 0, true},
		// A backs that is not a whole number from 0 is no backs: the count reads as in 0.4.0, every rise notifies.
		// Only schema and waiting can fail the file.
		"backs below":    {`{"schema": 1, "waiting": 3, "backs": -1}`, 3, -1, true},
		"backs text":     {`{"schema": 1, "waiting": 3, "backs": "2"}`, 3, -1, true},
		"backs bool":     {`{"schema": 1, "waiting": 3, "backs": true}`, 3, -1, true},
		"backs fraction": {`{"schema": 1, "waiting": 3, "backs": 1.5}`, 3, -1, true},
		"backs array":    {`{"schema": 1, "waiting": 3, "backs": [2]}`, 3, -1, true},
		"backs object":   {`{"schema": 1, "waiting": 3, "backs": {"n": 2}}`, 3, -1, true},
		"backs huge":     {`{"schema": 1, "waiting": 3, "backs": 99999999999999999999}`, 3, -1, true},
		"backs null":     {`{"schema": 1, "waiting": 3, "backs": null}`, 3, -1, true},
		"newer":          {`{"schema": 2, "waiting": 3}`, 0, -1, false},
		"negative":       {`{"schema": 1, "waiting": -1}`, 0, -1, false},
		"fraction":       {`{"schema": 1, "waiting": 1.5}`, 0, -1, false},
		"text":           {`{"schema": 1, "waiting": "3"}`, 0, -1, false},
		"no waiting":     {`{"schema": 1}`, 0, -1, false},
		"junk":           {`{"schema": 1, "waiting": 3`, 0, -1, false},
		"empty":          {``, 0, -1, false},
		"json null":      {`null`, 0, -1, false},
		"not object":     {`[3]`, 0, -1, false},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			dir := t.TempDir()
			write(t, filepath.Join(dir, "state.json"), c.text)
			n, backs, err := waiting(dir)
			if n != c.n || backs != c.backs || (err == nil) != c.ok {
				t.Fatalf("n %d, backs %d, err %v", n, backs, err)
			}
		})
	}
	if _, _, err := waiting(t.TempDir()); err == nil {
		t.Fatal("no error for a missing state.json")
	}
}

// TestWaitingOf040ReadsBacks reads a state.json with backs through the 0.4.0 app's parser (waiting() as released):
// the key is skipped, the count is the same.
func TestWaitingOf040ReadsBacks(t *testing.T) {
	old := func(home string) (int, error) {
		var state struct {
			Schema  *int `json:"schema"`
			Waiting *int `json:"waiting"`
		}
		if err := readJSON(filepath.Join(home, "state.json"), &state); err != nil {
			return 0, err
		}
		if state.Schema == nil || *state.Schema != 1 || state.Waiting == nil || *state.Waiting < 0 {
			return 0, os.ErrInvalid
		}
		return *state.Waiting, nil
	}
	dir := t.TempDir()
	write(t, filepath.Join(dir, "state.json"), `{"schema": 1, "at": "2026-11-26T10:00:00Z", "waiting": 4, "projects": [], "backs": 3}`)
	if n, err := old(dir); err != nil || n != 4 {
		t.Fatalf("0.4.0: n %d, err %v", n, err)
	}
	if n, backs, err := waiting(dir); err != nil || n != 4 || backs != 3 {
		t.Fatalf("now: n %d, backs %d, err %v", n, backs, err)
	}
}

func TestRiseNotifiesOncePerRise(t *testing.T) {
	var r rise
	steps := []struct {
		n      int
		ok     bool
		notify bool
	}{
		{2, true, false}, // the first trusted count is the baseline
		{2, true, false},
		{3, true, true},
		{3, true, false},
		{1, true, false},
		{0, false, false}, // no inbox: unknown, the last count stays
		{2, true, true},
		{5, true, true},
	}
	for i, s := range steps {
		if got := r.next(s.n, -1, s.ok); got != s.notify {
			t.Fatalf("step %d (%d, %v): notify %v", i, s.n, s.ok, got)
		}
	}
	var fresh rise
	if fresh.next(0, -1, false) || fresh.next(4, -1, true) {
		t.Fatal("notified before a baseline")
	}
}

// TestRiseLeavesOutTheUsersOwnBacks: within one read window, the count rises by the user's own Backs (backs) and by
// anything else; only a rise past what backs rose by notifies.
func TestRiseLeavesOutTheUsersOwnBacks(t *testing.T) {
	type read struct{ n, backs int }
	cases := map[string]struct {
		reads  []read
		notify bool
	}{
		"Back alone":                {[]read{{2, 0}, {3, 1}}, false},
		"Back, then a new round":    {[]read{{2, 0}, {4, 1}}, true},
		"a new round, then Back":    {[]read{{2, 4}, {4, 5}}, true},
		"Back, then an answer":      {[]read{{2, 0}, {2, 1}}, false},
		"two Backs":                 {[]read{{2, 0}, {4, 2}}, false},
		"a restart, then a rise":    {[]read{{2, 3}, {3, 0}}, true}, // backs lower: an inbox started anew
		"no key, a rise":            {[]read{{2, -1}, {3, -1}}, true},
		"the key comes, a rise":     {[]read{{2, -1}, {3, 1}}, true}, // an inbox from before the key, then this one
		"Back, later a new round":   {[]read{{2, 0}, {3, 1}, {4, 1}}, true},
		"Back after a quiet read":   {[]read{{2, 0}, {2, 0}, {3, 1}}, false},
		"a drop, then Back":         {[]read{{3, 0}, {2, 0}, {3, 1}}, false},
		"a restart, the same count": {[]read{{2, 3}, {2, 0}}, false},
		"a restart, a drop":         {[]read{{3, 3}, {2, 0}}, false},
		// Known limit: backs counts a Back's rise, not a Park's fall, so a new round in the same read as a Park and
		// its Back is left out. The badge still shows the count, and the list page its own bar.
		"known limit: Park, Back and a new round in one read": {[]read{{2, 0}, {3, 1}}, false},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			var r rise
			got := false
			for _, x := range c.reads {
				got = r.next(x.n, x.backs, true)
			}
			if got != c.notify {
				t.Fatalf("notify %v", got)
			}
		})
	}
}

func TestInboxAliveLooksWithoutKeepingTheLock(t *testing.T) {
	dir := t.TempDir()
	if inboxAlive(dir) {
		t.Fatal("alive without inbox.lock")
	}
	lock := filepath.Join(dir, "inbox.lock")
	write(t, lock, "")
	if inboxAlive(dir) {
		t.Fatal("alive with a free lock")
	}
	// The look let go: another open file can take the lock now, as the inbox does.
	fd, err := syscall.Open(lock, syscall.O_RDWR, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer syscall.Close(fd)
	if err := syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatalf("the look kept the lock: %v", err)
	}
	if !inboxAlive(dir) {
		t.Fatal("not alive while the lock is held")
	}
	syscall.Flock(fd, syscall.LOCK_UN)
	if inboxAlive(dir) {
		t.Fatal("alive after the lock was let go")
	}
	link := filepath.Join(t.TempDir(), "inbox.lock")
	os.Symlink(lock, link)
	syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB)
	if inboxAlive(filepath.Dir(link)) {
		t.Fatal("followed a link")
	}
}

func TestOwnFolder(t *testing.T) {
	dir := t.TempDir()
	home := filepath.Join(dir, "home")
	if err := ownFolder(home); err == nil {
		t.Fatal("took a missing folder")
	}
	os.Mkdir(home, 0o700)
	if err := ownFolder(home); err != nil {
		t.Fatalf("refused a folder of its own: %v", err)
	}
	os.Symlink(home, filepath.Join(dir, "link"))
	if err := ownFolder(filepath.Join(dir, "link")); err == nil {
		t.Fatal("took a link")
	}
	os.Chmod(home, 0o750)
	if err := ownFolder(home); err == nil {
		t.Fatal("took a folder open to others")
	}
	write(t, filepath.Join(dir, "file"), "")
	if err := ownFolder(filepath.Join(dir, "file")); err == nil {
		t.Fatal("took a file")
	}
}

func TestStartInboxWritesNothingInAFolderNotItsOwn(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "target")
	os.Mkdir(target, 0o700)
	os.Symlink(target, filepath.Join(dir, "home"))
	if err := startInbox("/usr/bin/true", filepath.Join(dir, "home")); err == nil {
		t.Fatal("started in a home that is a link")
	}
	if _, err := os.Lstat(filepath.Join(target, "logs")); !os.IsNotExist(err) {
		t.Fatalf("wrote logs/ through the link: %v", err)
	}
	// A home of the user's own, open to others, is closed first, as the inbox does.
	own := filepath.Join(dir, "own")
	os.Mkdir(own, 0o755)
	if err := startInbox("/usr/bin/true", own); err != nil {
		t.Fatal(err)
	}
	if st, _ := os.Stat(own); st.Mode().Perm() != 0o700 {
		t.Fatalf("home mode %o", st.Mode().Perm())
	}
}

func TestFindLauncher(t *testing.T) {
	dir := t.TempDir()
	run := filepath.Join(dir, "signoff")
	write(t, run, "#!/bin/sh\n")
	os.Chmod(run, 0o700)
	write(t, filepath.Join(dir, "plain"), "")
	if got, err := findLauncher(run); err != nil || got != run {
		t.Fatalf("got %q, err %v", got, err)
	}
	for _, bad := range []string{"", "signoff", filepath.Join(dir, "missing"), filepath.Join(dir, "plain"), dir} {
		if _, err := findLauncher(bad); err == nil {
			t.Errorf("took %q", bad)
		}
	}
}

func TestAppHome(t *testing.T) {
	t.Setenv("HOME", "/Users/someone")
	t.Setenv("SIGNOFF_INBOX_HOME", "")
	if got, err := appHome(); err != nil || got != "/Users/someone/Library/Application Support/signoff" {
		t.Fatalf("got %q, err %v", got, err)
	}
	t.Setenv("SIGNOFF_INBOX_HOME", "/tmp/elsewhere")
	if got, err := appHome(); err != nil || got != "/tmp/elsewhere" {
		t.Fatalf("got %q, err %v", got, err)
	}
	t.Setenv("SIGNOFF_INBOX_HOME", "relative") // off for the plugin too: inbox home exits 1
	if _, err := appHome(); err == nil {
		t.Fatal("took a relative home")
	}
}

// TestResolveLauncher: a build with no fixed launcher runs what <home>/launcher points to, once it is an absolute
// link to a regular, executable file and that command names the same home.
func TestResolveLauncher(t *testing.T) {
	dir := t.TempDir()
	home := filepath.Join(dir, "home")
	t.Setenv("SIGNOFF_INBOX_HOME", home)
	run := filepath.Join(dir, "signoff") // the plugin's command as far as the app sees: it names the home
	write(t, run, "#!/bin/sh\nprintf '%s\\n' \"$SIGNOFF_INBOX_HOME\"\n")
	os.Chmod(run, 0o700)
	other := filepath.Join(dir, "other")
	write(t, other, "#!/bin/sh\necho /tmp/another-home\n")
	os.Chmod(other, 0o700)
	write(t, filepath.Join(dir, "plain"), "")
	link := filepath.Join(home, "launcher")
	pointTo := func(target string) {
		t.Helper()
		os.Remove(link)
		if err := os.Symlink(target, link); err != nil {
			t.Fatal(err)
		}
	}
	once := "start a Claude Code session with the Signoff plugin installed from its marketplace once"

	// A fixed launcher wins: the home's link is not looked at.
	if l, h, err := resolveLauncher(run); err != nil || l != run || h != home {
		t.Fatalf("fixed: %q %q %v", l, h, err)
	}
	if _, _, err := resolveLauncher(""); err == nil || !strings.Contains(err.Error(), once) {
		t.Fatalf("no home: %v", err)
	}
	if err := os.Mkdir(home, 0o700); err != nil {
		t.Fatal(err)
	}
	if _, _, err := resolveLauncher(""); err == nil || !strings.Contains(err.Error(), once) {
		t.Fatalf("no link: %v", err)
	}
	pointTo(filepath.Join(dir, "gone")) // as after a plugin update, until the next session start
	if _, _, err := resolveLauncher(""); err == nil || !strings.Contains(err.Error(), once) {
		t.Fatalf("dangling: %v", err)
	}
	pointTo("../signoff") // the right file, but not by an absolute path
	if _, _, err := resolveLauncher(""); err == nil {
		t.Fatal("took a relative link")
	}
	for _, bad := range []string{filepath.Join(dir, "plain"), dir, other} {
		pointTo(bad)
		if _, _, err := resolveLauncher(""); err == nil {
			t.Errorf("took %q", bad)
		}
	}
	os.Remove(link)
	write(t, link, "#!/bin/sh\n") // a file, not a link
	os.Chmod(link, 0o700)
	if _, _, err := resolveLauncher(""); err == nil {
		t.Fatal("took a file for the link")
	}
	pointTo(run)
	want, _ := filepath.EvalSymlinks(run) // macOS temp folders sit behind /var -> /private/var
	if l, h, err := resolveLauncher(""); err != nil || l != want || h != home {
		t.Fatalf("good: %q %q %v", l, h, err)
	}
	os.Chmod(home, 0o755) // open to others: not the user's own home
	if _, _, err := resolveLauncher(""); err == nil {
		t.Fatal("took a home open to others")
	}
}

func TestCloseOnExecKeepsFilesFromTheInbox(t *testing.T) {
	fd, err := syscall.Open(filepath.Join(t.TempDir(), "f"), syscall.O_RDWR|syscall.O_CREAT, 0o600) // no O_CLOEXEC
	if err != nil {
		t.Fatal(err)
	}
	defer syscall.Close(fd)
	seen := func() bool {
		return exec.Command("/bin/sh", "-c", "[ -e /dev/fd/"+strconv.Itoa(fd)+" ]").Run() == nil
	}
	if !seen() {
		t.Skip("this system hands no plain descriptor on; nothing to check")
	}
	closeOnExec()
	if seen() {
		t.Fatal("the descriptor still goes with a started process")
	}
}
