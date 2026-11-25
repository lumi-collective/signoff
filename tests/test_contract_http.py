"""The security and run contract in AGENTS.md, checked against a separate process. Each test starts the command
line as a child and talks to it only over HTTP, through its exit code and its stdout, so the same suite can run
against another implementation (the inbox mode, a port): set SIGNOFF_CONTRACT_CMD to its command, which gets the
same arguments (<decisions file or folder> or --history ROOT, --timeout, --no-open, --port) and SIGNOFF_PORT, and
prints its start link on stderr. The fixtures from support.py and test_extras.py are files on disk; nothing here
calls into signoff.py."""
import hashlib
import http.client
import json
import os
import queue
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from support import ROOT, Folder, Rounds, free_port, png_bytes, sample_decisions
from test_extras import BOARD, HistoryRoot, board_folder

# shortcut: shlex splits like a POSIX shell, so a Windows path needs forward slashes or quotes; split it by hand
# if that bites.
COMMAND = shlex.split(os.environ["SIGNOFF_CONTRACT_CMD"]) if os.environ.get("SIGNOFF_CONTRACT_CMD") else [
    sys.executable, "-I", "-B", str(ROOT / "plugin" / "signoff" / "signoff.py")]
LINK = re.compile(r"(http://([^/:\s]+):([0-9]+))(/\S*)")
ANSWER = {"answers": {"layout": {"choice": "one", "comment": "from the contract"}}, "free_text": "", "done": False}
PREFS = {"theme": "dark", "palette": "gruvbox"}


class Run:
    """One Signoff process, its start link read from stderr, and plain HTTP to it."""

    def __init__(self, *args, env=None):
        self.proc = subprocess.Popen(COMMAND + [str(a) for a in args] + ["--no-open"], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8",
                                     env=dict(os.environ, **env) if env else None,
                                     errors="replace")
        # Both pipes are read as they come, so a process that logs every request or prints a lot never stalls on
        # a full pipe; stderr lines also go to a queue, to wait for the link with a deadline.
        self.out, self.err, lines = [], [], queue.Queue()

        def drain(pipe, sink, notify=None):
            for line in pipe:
                sink.append(line)
                if notify:
                    notify.put(line)
            if notify:
                notify.put(None)

        self.readers = [threading.Thread(target=drain, args=(self.proc.stdout, self.out), daemon=True),
                        threading.Thread(target=drain, args=(self.proc.stderr, self.err, lines), daemon=True)]
        for t in self.readers:
            t.start()
        try:
            deadline = time.monotonic() + 30
            while True:
                line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
                if line is None:
                    raise AssertionError("no start link on stderr: %r" % "".join(self.err))
                found = LINK.search(line)
                if found:
                    break
        except queue.Empty:
            self.stop()
            raise AssertionError("no start link on stderr within 30 s: %r" % "".join(self.err))
        except BaseException:
            self.stop()
            raise
        self.origin, self.hostname, self.start = found.group(1), found.group(2), found.group(4)
        self.port = int(found.group(3))
        self.host = "%s:%d" % (self.hostname, self.port)
        self.page = None

    def request(self, method, path, body=None, headers=None):
        """A request with exactly these headers: Host is this run's unless given; a header set to None is left out,
        Content-Length too."""
        hdrs = {"Host": self.host}
        hdrs.update(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        if body is not None and "Content-Length" not in hdrs:
            hdrs["Content-Length"] = str(len(body))
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in hdrs.items():
                for one in v if isinstance(v, list) else [v]:  # a list: the header once per value
                    if one is not None:
                        conn.putheader(k, one)
            conn.endheaders(body)
            resp = conn.getresponse()
            return resp, resp.read()
        finally:
            conn.close()

    def open(self):
        """Use the start link once, as the browser does, and keep the page path it sends on to."""
        resp, _ = self.request("GET", self.start)
        assert resp.status == 303, resp.status  # a redirect, not followed, so the test sees it
        self.page = resp.getheader("Location")
        assert re.fullmatch(r"/[^/?#]+/", self.page or ""), self.page
        return self.page

    def save_headers(self, headers=None):
        """What the page sends with a save, changed by headers."""
        hdrs = {"Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"}
        hdrs.update(headers or {})
        return hdrs

    def save(self, round_, body, headers=None):
        return self.request("POST", "%ssave/%d" % (self.page, round_), body, self.save_headers(headers))

    def finish(self):
        """Wait for the exit; return stdout and stderr."""
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.stop()
            raise AssertionError("the run did not end")
        for t in self.readers:
            t.join(5)
        return "".join(self.out), "".join(self.err)

    def stop(self):
        # kill() reaches this process only: the command must be the server itself (a launcher that execs), or a
        # child it left would keep the pipes open.
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        for t in self.readers:
            t.join(5)
        if not any(t.is_alive() for t in self.readers):  # closing a pipe a thread still reads would block
            self.proc.stdout.close()
            self.proc.stderr.close()


def policy(resp):
    """The Content-Security-Policy header as {directive: [sources]}."""
    header = resp.getheader("Content-Security-Policy") or ""
    return {d.split()[0]: d.split()[1:] for d in header.split(";") if d.strip()}


def contents(folder):
    """Every file under folder and its bytes, to show nothing was written."""
    return {str(p): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


class Refusals(unittest.TestCase):
    """Bad requests to one running page with a picture and a board. Each must be refused with its status and leave
    the answers as they were; one good save shows the refusals are not all the server can do."""

    @classmethod
    def setUpClass(cls):
        cls.folder = board_folder()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.run_ = Run(cls.folder.target)
        cls.addClassCleanup(cls.run_.stop)
        cls.page = cls.run_.open()
        resp, body = cls.run_.request("GET", cls.page)
        assert resp.status == 200, resp.status
        cls.board = re.search(r'"board":\s*"(/[^"]+)"', body.decode("utf-8")).group(1)
        cls.board_token = cls.board.split("/")[1]

    def answers(self):
        try:
            return self.folder.answers.read_bytes()
        except FileNotFoundError:
            return None

    def refused(self, status, method, path, body=None, headers=None):
        before = self.answers()
        resp, data = self.run_.request(method, path, body, headers)
        self.assertEqual(resp.status, status, (method, path, headers))
        self.assertEqual(self.answers(), before, (method, path, headers))
        return data

    def save_refused(self, status, body=ANSWER, headers=None, round_=0):
        return self.refused(status, "POST", "%ssave/%d" % (self.page, round_), body, self.run_.save_headers(headers))

    def test_binds_loopback_only(self):
        self.assertEqual(self.run_.hostname, "127.0.0.1")
        try:  # this machine's address toward the network; a UDP connect sends nothing
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.0.2.1", 9))
                ip = s.getsockname()[0]
        except OSError:
            ip = None
        if not ip or ip.startswith("127.") or ip == "0.0.0.0":
            self.skipTest("no address besides loopback to try")
        with self.assertRaises(OSError, msg="the port answers on %s" % ip):
            socket.create_connection((ip, self.run_.port), timeout=2).close()

    def test_every_request_needs_the_token(self):
        token = self.page.strip("/")
        for path in ("/", "/" + token, "/%s/" % ("A" * len(token)), "/x/img/0", "/%s/" % self.run_.start.split("/")[2]):
            body = self.refused(404, "GET", path)
            self.assertNotIn(b"signoff-data", body, path)
        self.refused(404, "POST", "/%s/save/0" % ("A" * len(token)), ANSWER,
                     {"Content-Type": "application/json", "Origin": self.run_.origin})

    def test_a_run_has_no_state_route(self):
        self.refused(404, "GET", self.page + "state")  # the count is the inbox's alone

    def test_a_run_has_no_prefs_route(self):
        # The theme is kept by the inbox alone: a plain run writes no new file.
        self.refused(404, "POST", self.page + "prefs", PREFS, self.run_.save_headers())
        self.assertFalse(os.path.exists(os.environ["SIGNOFF_INBOX_HOME"]))

    def test_host_must_be_exact(self):
        port = self.run_.port
        for host in ("localhost.example:%d" % port, "127.0.0.1", "127.0.0.1:%d.evil.example" % port, "evil.example",
                     "[::1]:%d" % port, "127.0.0.1:%d" % (port + 1), "localhost", "localhost:%d" % (port + 1),
                     "localhost:%d.evil.example" % port, "", None, "LOCALHOST:%d" % port, "Localhost:%d" % port,
                     "localhost.:%d" % port, "127.0.0.1.:%d" % port, "127.1:%d" % port, "0x7f000001:%d" % port,
                     "127.0.0.2:%d" % port, "0.0.0.0:%d" % port, [self.run_.host, "evil.example"],
                     ["evil.example", self.run_.host], [self.run_.host, self.run_.host]):
            hdrs = {"Host": host}
            self.refused(403, "GET", self.page, headers=hdrs)
            self.refused(403, "GET", self.board, headers=hdrs)
            self.save_refused(403, headers=hdrs)
            if not isinstance(host, list):
                self.save_refused(403, headers=dict(hdrs, Origin="http://%s" % host))

    def test_a_localhost_save_needs_exactly_its_own_origin(self):
        port = self.run_.port
        local = "localhost:%d" % port
        for origin in ("null", None, "http://%s/" % local, "https://" + local, "http://localhost:%d" % (port + 1),
                       "http://LOCALHOST:%d" % port, ""):
            self.save_refused(403, headers={"Host": local, "Origin": origin})

    def test_localhost_names_the_same_page(self):
        # A forwarded port or a devcontainer's Ports tab may open the page as localhost: the same rules hold.
        local = "localhost:%d" % self.run_.port
        for path in (self.page, self.board):
            self.assertEqual(self.run_.request("GET", path, headers={"Host": local})[0].status, 200, path)
        self.save_refused(403, headers={"Host": local})  # its Origin must be its own Host: here 127.0.0.1's
        self.save_refused(403, headers={"Origin": "http://" + local})  # and the other way round
        resp, data = self.run_.save(0, dict(ANSWER, free_text="through localhost"),
                                    {"Host": local, "Origin": "http://" + local})
        self.assertEqual(resp.status, 200, data)
        saved = json.loads(self.folder.answers.read_text(encoding="utf-8"))
        self.assertEqual(saved["free_text"], "through localhost")

    def test_save_needs_json(self):
        for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", None):
            self.save_refused(415, headers={"Content-Type": ctype})

    def test_save_needs_the_pages_own_origin(self):
        port = self.run_.port
        own = self.run_.origin
        for origin in ("http://evil.example", "http://127.0.0.1:%d" % (port + 1), "null", "http://localhost:%d" % port,
                       "https://127.0.0.1:%d" % port, own + "0", own + ".evil.example", own + "/", None):
            self.save_refused(403, headers={"Origin": origin})

    def test_sec_fetch_site_must_be_same_origin_when_present(self):
        for site in ("cross-site", "same-site", "none"):
            self.save_refused(403, headers={"Sec-Fetch-Site": site})

    def test_a_good_save_is_stored(self):
        for site in (None, "same-origin"):  # Sec-Fetch-Site may be absent: not every client sends it
            body = dict(ANSWER, free_text="site %s" % site)
            resp, data = self.run_.save(0, body, {"Sec-Fetch-Site": site})
            self.assertEqual(resp.status, 200, data)
            saved = json.loads(self.folder.answers.read_text(encoding="utf-8"))
            self.assertEqual((saved["answers"]["layout"]["choice"], saved["free_text"], saved["done"]),
                             ("one", "site %s" % site, False))

    def test_body_within_the_rounds_limit(self):
        for length in ("41943040", "9" * 5000):  # declared only: refused before the body is read
            self.save_refused(413, b"{}", {"Content-Length": length})
        # Sent: twice this small round's limit (about 300 KB; a fixture with more text fields would raise it, and this
        # would get 400), and within the 1 MiB a refused body is read and dropped.
        self.save_refused(413, dict(ANSWER, free_text="x" * 600000))
        for hdrs in ({"Content-Length": None}, {"Content-Length": "abc"},
                     {"Content-Length": None, "Transfer-Encoding": "chunked"}):
            self.save_refused(411, b"0\r\n\r\n" if "Transfer-Encoding" in hdrs else json.dumps(ANSWER).encode(), hdrs)

    def test_save_is_checked_against_its_rounds_decisions(self):
        for body in ({"answers": {"layout": {"choice": "three", "comment": ""}}, "free_text": "", "done": False},
                     {"answers": {"nope": {"choice": "one", "comment": ""}}, "free_text": "", "done": False},
                     dict(ANSWER, done="yes"), dict(ANSWER, extra=1), b"{nope", b"[" * 100000):
            self.save_refused(400, body)
        for round_ in (1, 99):
            self.save_refused(404, round_=round_)
        self.refused(404, "GET", self.page + "save/0")

    def test_board_token_opens_nothing_else(self):
        boards = "/%s/" % self.board_token
        for path in ("", "img/0", "save/0", "0/1", "1/0", "0/0/", self.page.strip("/") + "/"):
            self.refused(404, "GET", boards + path)
        self.refused(404, "POST", boards + "save/0", ANSWER,
                     {"Content-Type": "application/json", "Origin": self.run_.origin})
        self.refused(404, "GET", self.page + "0/0")  # and the page token opens no board
        self.refused(404, "GET", "/b/%s/" % self.board_token)

    def test_board_is_sandboxed(self):
        for query in ("", "?theme=dark"):
            resp, body = self.run_.request("GET", self.board + query)
            self.assertEqual(resp.status, 200, query)
            self.assertEqual(body, BOARD)
            csp = policy(resp)
            self.assertEqual(csp.pop("sandbox", None), ["allow-scripts"])  # never allow-same-origin
            for name in ("default-src", "base-uri", "form-action"):
                self.assertEqual(csp.get(name), ["'none'"], name)
            for name, sources in csp.items():  # an allowlist: nothing is loaded from anywhere
                self.assertLessEqual(set(sources), {"'none'", "'unsafe-inline'", "data:"}, name)
            self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")

    def test_page_csp_is_strict(self):
        nonces = []
        for _ in range(2):
            resp, body = self.run_.request("GET", self.page)
            self.assertEqual(resp.status, 200)
            csp = policy(resp)
            nonce = re.fullmatch(r"'nonce-([^']+)'", (csp.get("script-src") or [""])[0]).group(1)
            wanted = {"default-src": "'none'", "base-uri": "'none'", "form-action": "'none'",
                      "frame-ancestors": "'none'", "script-src": "'nonce-%s'" % nonce,
                      "style-src": "'nonce-%s'" % nonce, "img-src": "'self'", "connect-src": "'self'",
                      "frame-src": "'self'"}
            for name, source in wanted.items():
                self.assertEqual(csp.get(name), [source], name)
            for name, sources in csp.items():  # an allowlist: only this page and its own nonce, never inline
                self.assertLessEqual(set(sources), {"'none'", "'self'", "'nonce-%s'" % nonce}, name)
            # Every script and style that can run; a JSON data block is never executed, so it needs none.
            tags = [t for t in re.findall(rb"<(?:script|style)\b[^>]*>", body) if b'type="application/json"' not in t]
            self.assertGreaterEqual(len(tags), 2)
            for tag in tags:
                self.assertIn(('nonce="%s"' % nonce).encode(), tag)
            nonces.append(nonce)
            self.assertIn("no-store", resp.getheader("Cache-Control") or "")
            self.assertIsNone(resp.getheader("Set-Cookie"))
        self.assertNotEqual(nonces[0], nonces[1])

    def test_only_listed_files_are_served(self):
        resp, body = self.run_.request("GET", self.page + "img/0")
        self.assertEqual((resp.status, resp.getheader("Content-Type"), body), (200, "image/png", png_bytes()))
        for path in ("img/1", "img/-1", "img/00", "img/0/", "mockups/a.png", "decisions.json",
                     "decisions.answers.json", "boards/b.html", ".gitignore", "img/../decisions.json",
                     "img/..%2fdecisions.json", "%2e%2e/decisions.json", "../decisions.json", "img/0?../x"):
            body = self.refused(404, "GET", self.page + path)
            self.assertNotIn(b'"version"', body, path)

    def test_no_request_reads_the_disk(self):
        # Pictures and boards were read at start: a file changed or added since is not what the routes give.
        (self.folder.path / "mockups" / "a.png").write_bytes(b"CHANGED")
        (self.folder.path / "mockups" / "c.png").write_bytes(png_bytes())
        (self.folder.path / "boards" / "b.html").write_bytes(b"CHANGED")
        try:
            self.assertEqual(self.run_.request("GET", self.page + "img/0")[1], png_bytes())
            self.assertEqual(self.run_.request("GET", self.board)[1], BOARD)
            self.refused(404, "GET", self.page + "img/1")
        finally:
            (self.folder.path / "mockups" / "a.png").write_bytes(png_bytes())
            (self.folder.path / "boards" / "b.html").write_bytes(BOARD)


class StartLink(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        self.addCleanup(self.folder.cleanup)

    def run_once(self):
        run = Run(self.folder.target)
        self.addCleanup(run.stop)
        return run

    def test_second_use_is_gone_and_stops_the_run_with_1(self):
        run = self.run_once()
        resp, _ = run.request("GET", run.start)
        self.assertEqual(resp.status, 303)
        self.assertIsNone(resp.getheader("Set-Cookie"))
        page = resp.getheader("Location")
        self.assertEqual(run.request("GET", page)[0].status, 200)
        resp, body = run.request("GET", run.start)
        self.assertEqual(resp.status, 410)
        self.assertNotIn(page.strip("/").encode(), body)
        out, err = run.finish()
        self.assertEqual((run.proc.returncode, out), (1, ""), err)

    def test_done_waits_out_the_grace_so_a_late_second_use_still_stops_the_run_with_1(self):
        # Whoever used the link first may press Done before the browser arrives; the browser must still be told.
        run = self.run_once()
        run.open()
        self.assertEqual(run.save(0, dict(ANSWER, done=True))[0].status, 200)
        self.assertEqual(run.request("GET", run.start)[0].status, 410)
        out, err = run.finish()
        self.assertEqual((run.proc.returncode, out), (1, ""), err)

    def test_bad_requests_do_not_spend_it(self):
        run = self.run_once()
        token = run.start.split("/")[2]
        port = run.port
        for host in ("evil.example", "localhost.example:%d" % port, "[::1]:%d" % port, "localhost:%d" % (port + 1),
                     "LOCALHOST:%d" % port, "127.0.0.1.:%d" % port, "", None, [run.host, "evil.example"],
                     ["evil.example", run.host], [run.host, run.host]):
            self.assertEqual(run.request("GET", run.start, headers={"Host": host})[0].status, 403, host)
        for path in ("/b/%s/" % ("A" * len(token)), "/b/%s" % token, "/b/", "/b/%s/x" % token):
            self.assertEqual(run.request("GET", path)[0].status, 404, path)
        resp, _ = run.request("POST", run.start, ANSWER, {"Content-Type": "application/json", "Origin": run.origin})
        self.assertEqual(resp.status, 404)
        self.assertEqual(run.request("GET", run.start)[0].status, 303)

    def test_localhost_may_use_it(self):
        run = self.run_once()
        self.assertEqual(run.hostname, "127.0.0.1")  # the link Signoff prints names 127.0.0.1
        local = "localhost:%d" % run.port
        resp, _ = run.request("GET", run.start, headers={"Host": local})
        self.assertEqual(resp.status, 303)
        self.assertEqual(run.request("GET", resp.getheader("Location"), headers={"Host": local})[0].status, 200)
        self.assertEqual(run.request("GET", run.start, headers={"Host": local})[0].status, 410)  # once, by either name
        out, err = run.finish()
        self.assertEqual((run.proc.returncode, out), (1, ""), err)

    def test_each_run_gets_its_own_port_and_tokens(self):
        a, b = self.run_once(), self.run_once()
        self.assertNotEqual(a.port, b.port)
        self.assertNotEqual(a.start, b.start)
        self.assertNotEqual(a.open(), b.open())
        self.assertEqual(a.request("GET", b.page)[0].status, 404)


class FixedPort(unittest.TestCase):
    """--port N and SIGNOFF_PORT (the flag wins): a port of the user's choosing, on 127.0.0.1 only. 0 or unset is
    a random port; a bad value or a taken port exits 1 with nothing on stdout."""

    def setUp(self):
        self.folder = Folder()
        self.addCleanup(self.folder.cleanup)

    def run_on(self, *args, env=None):
        run = Run(self.folder.target, *args, env=env)
        self.addCleanup(run.stop)
        return run

    def refused(self, *args, env=None):
        proc = subprocess.run(COMMAND + [str(a) for a in args] + ["--no-open"], capture_output=True,
                              universal_newlines=True, encoding="utf-8", errors="replace",
                              env=dict(os.environ, **(env or {})), timeout=30)
        self.assertEqual((proc.returncode, proc.stdout), (1, ""), (args, env, proc.stderr))
        self.assertFalse(self.folder.answers.exists())
        return proc.stderr

    def test_the_flag_the_variable_and_the_flag_winning(self):
        a, b = free_port(), free_port()
        while b == a:
            b = free_port()
        for args, env, want in ((("--port", a), None, a), ((), {"SIGNOFF_PORT": str(b)}, b),
                                (("--port", a), {"SIGNOFF_PORT": str(b)}, a)):
            run = self.run_on(*args, env=env)
            self.assertEqual((run.hostname, run.port), ("127.0.0.1", want), (args, env))
            run.open()
            self.assertEqual(run.request("GET", run.page)[0].status, 200)
            run.stop()

    def test_zero_or_empty_is_a_random_port(self):
        for args, env in ((("--port", 0), None), ((), {"SIGNOFF_PORT": "0"}), ((), {"SIGNOFF_PORT": ""})):
            run = self.run_on(*args, env=env)
            self.assertGreater(run.port, 0)
            run.stop()

    def test_the_history_page_takes_it_too(self):
        root = HistoryRoot()
        self.addCleanup(root.cleanup)
        port = free_port()
        run = Run("--history", root.path, env={"SIGNOFF_PORT": str(port)})
        self.addCleanup(run.stop)
        self.assertEqual(run.port, port)

    def test_bad_values_are_usage_errors(self):
        # Below 1024 too: browsers leave :80 out of Host and Origin, and the low ports are the system's.
        for value in ("-1", "65536", "x", "1.5", "+80", " 80", "0x50", "", "1", "80", "1023"):
            # The line names the setting: where low ports are refused, the bind's own error exits 1 as well.
            err = self.refused(self.folder.target, "--port", value)
            self.assertEqual(len(err.strip().splitlines()), 1, (value, err))
            self.assertIn("--port", err, value)
            if value:
                err = self.refused(self.folder.target, env={"SIGNOFF_PORT": value})
                self.assertEqual(len(err.strip().splitlines()), 1, (value, err))
                self.assertIn("SIGNOFF_PORT", err, value)

    def test_a_taken_port_exits_1_in_one_line_that_names_it(self):
        root = HistoryRoot()
        self.addCleanup(root.cleanup)
        # 0.0.0.0 too: on macOS a SO_REUSEADDR bind of 127.0.0.1 would sit beside it and take its loopback traffic.
        for address in ("127.0.0.1", "0.0.0.0"):
            with socket.socket() as taken:
                taken.bind((address, 0))
                taken.listen()
                port = taken.getsockname()[1]
                for args, env in (((self.folder.target, "--port", port), None),
                                  ((self.folder.target,), {"SIGNOFF_PORT": str(port)}),
                                  (("--history", root.path, "--port", port), None)):
                    err = self.refused(*args, env=env)
                    self.assertIn(str(port), err)
                    self.assertEqual(len(err.strip().splitlines()), 1, err)


class Exits(unittest.TestCase):
    """Exit codes and stdout: the answers paths, one per line in tab order, and nothing else."""

    def test_done_exits_0_and_prints_the_paths_in_tab_order(self):
        rounds = Rounds(2)
        self.addCleanup(rounds.cleanup)
        run = Run(rounds.target)
        self.addCleanup(run.stop)
        run.open()
        done = dict(ANSWER, done=True)
        self.assertEqual(run.save(0, done)[0].status, 409)  # Done waits until the other round is accepted
        self.assertEqual(run.save(1, dict(ANSWER, accepted=True, rev=0))[0].status, 200)
        self.assertEqual(run.save(1, dict(ANSWER, rev=0))[0].status, 409)  # a page that missed a save is stale
        self.assertEqual(run.save(0, done)[0].status, 200)
        self.assertEqual(run.save(0, ANSWER)[0].status, 409)  # Done is final
        self.assertEqual(run.save(1, done)[0].status, 200)
        out, err = run.finish()
        self.assertEqual(run.proc.returncode, 0, err)
        self.assertEqual(out, "%s\n%s\n" % (rounds.answers(1), rounds.answers(2)))
        for n in (1, 2):
            self.assertTrue(json.loads(rounds.answers(n).read_text(encoding="utf-8"))["done"])

    def test_done_on_one_of_two_rounds_does_not_end_the_run(self):
        rounds = Rounds(2)
        self.addCleanup(rounds.cleanup)
        run = Run(rounds.target, "--timeout", "0.05")  # 3 s idle: room for three requests on a slow runner
        self.addCleanup(run.stop)
        run.open()
        self.assertEqual(run.save(1, dict(ANSWER, accepted=True))[0].status, 200)
        self.assertEqual(run.save(0, dict(ANSWER, done=True))[0].status, 200)
        out, err = run.finish()
        self.assertEqual(run.proc.returncode, 2, err)  # the idle timeout, not Done
        self.assertEqual(out, "%s\n%s\n" % (rounds.answers(1), rounds.answers(2)))

    def test_each_round_is_checked_against_its_own_decisions(self):
        rounds = Rounds(2)
        self.addCleanup(rounds.cleanup)
        other = sample_decisions()
        other["items"][0]["id"] = "density"
        for n in range(10):  # ten more items with a comment: round 2's body limit grows past 1 MB
            other["items"].append({"id": "x%d" % n, "title": "X",
                                   "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]})
        rounds.write(2, other)
        run = Run(rounds.target)
        self.addCleanup(run.stop)
        run.open()
        self.assertEqual(run.save(1, ANSWER)[0].status, 400)  # round 2 has no "layout"
        density = {"answers": {"density": {"choice": "one", "comment": ""}}, "free_text": "", "done": False}
        self.assertEqual(run.save(1, density)[0].status, 200)
        self.assertEqual(run.save(0, density)[0].status, 400)
        big = dict(density, free_text="x" * 600000)  # over round 1's limit, within round 2's
        self.assertEqual(run.save(0, big)[0].status, 413)
        self.assertEqual(run.save(1, big)[0].status, 400)  # read, then refused as too long a text

    def test_a_number_items_save_is_checked_against_its_range(self):
        folder = Folder()
        self.addCleanup(folder.cleanup)
        data = sample_decisions()
        data["items"].append({"id": "budget", "title": "How much?", "select": "number",
                              "range": {"min": -10, "max": 10, "step": 0.5, "unit": "%"}, "recommended": 2.5})
        folder.write(data)
        run = Run(folder.target)
        self.addCleanup(run.stop)
        run.open()

        def body(choice):
            return {"answers": {"budget": {"choice": choice, "comment": "", "source": "user"}}, "free_text": "",
                    "done": False}
        for choice in ("2.5", True, [2.5], 2.25, 10.5, -11, "_skip", "_other"):
            self.assertEqual(run.save(0, body(choice))[0].status, 400, choice)
        for raw in (b'{"answers": {"budget": {"choice": NaN}}, "free_text": "", "done": false}',
                    b'{"answers": {"budget": {"choice": Infinity}}, "free_text": "", "done": false}',
                    b'{"answers": {"budget": {"choice": 1e999}}, "free_text": "", "done": false}'):
            self.assertEqual(run.save(0, raw)[0].status, 400, raw)
        self.assertFalse(folder.answers.exists())
        for choice in (0, -7.5, 10):
            self.assertEqual(run.save(0, body(choice))[0].status, 200, choice)
            saved = json.loads(folder.answers.read_text(encoding="utf-8"))
            self.assertEqual(saved["answers"]["budget"]["choice"], choice)
            self.assertNotIsInstance(saved["answers"]["budget"]["choice"], bool)

    def test_a_line_note_is_checked_against_its_attachment(self):
        folder = Folder()
        self.addCleanup(folder.cleanup)
        (folder.path / "change.patch").write_bytes(b"--- a/x\r\n+++ b/x\r\n@@ -1 +1 @@\r\n-old\r\n+new")
        data = sample_decisions()
        data["items"][0].update(attachments=[{"src": "change.patch", "open": True}], lines=True)
        folder.write(data)
        run = Run(folder.target)
        self.addCleanup(run.stop)
        run.open()

        def body(*notes):
            return {"answers": {"layout": {"choice": "two", "comment": "", "lines": list(notes)}}, "free_text": "",
                    "done": False}
        good = {"att": 0, "line": 5, "quote": "+new", "comment": "why?"}
        for bad in (dict(good, line=6), dict(good, quote="+new\r"), dict(good, att=1), dict(good, att=True),
                    dict(good, comment=""), dict(good, side="new")):
            self.assertEqual(run.save(0, body(good, bad))[0].status, 400, bad)
        other = {"answers": {"filters": {"choice": [], "lines": []}}, "free_text": "", "done": False}
        self.assertEqual(run.save(0, other)[0].status, 400)  # an item without lines takes no notes
        self.assertEqual(run.save(0, body(*[good] * 51))[0].status, 400)
        self.assertFalse(folder.answers.exists())
        self.assertEqual(run.save(0, body(good, dict(good, line=1, quote="--- a/x")))[0].status, 200)
        saved = json.loads(folder.answers.read_text(encoding="utf-8"))
        self.assertEqual(saved["answers"]["layout"]["lines"], [good, dict(good, line=1, quote="--- a/x")])
        self.assertEqual(run.save(0, body(dict(good, line=262145)))[0].status, 400)  # past any file's lines

    def test_a_stored_line_note_past_any_files_lines_is_refused(self):
        # A page would round a line past 2^53 and then fail every save of the round: such a file does not load.
        folder = Folder()
        self.addCleanup(folder.cleanup)
        (folder.path / "change.diff").write_bytes(b"--- a/x\n+++ b/x\n")
        data = sample_decisions()
        data["items"][0].update(attachments=[{"src": "change.diff"}], lines=True)
        folder.write(data)
        stored = {"version": 1, "done": False, "saved": "2026-01-01T12:00:00Z", "free_text": "",
                  "answers": {"layout": {"choice": "two", "lines": [
                      {"att": 0, "line": 2 ** 53 + 1, "quote": "+++ b/x", "comment": "here"}]}}}
        folder.answers.write_text(json.dumps(stored), encoding="utf-8")
        done = subprocess.run(COMMAND + [str(folder.target), "--no-open"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8", timeout=30)
        self.assertEqual((done.returncode, done.stdout), (1, ""), done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        stored["answers"]["layout"]["lines"][0]["line"] = 262144  # at the ceiling: it loads
        folder.answers.write_text(json.dumps(stored), encoding="utf-8")
        run = Run(folder.target)
        self.addCleanup(run.stop)
        run.open()
        body = {"answers": stored["answers"], "free_text": "", "done": False}
        self.assertEqual(run.save(0, body)[0].status, 200)  # sent back unchanged: kept as stored, not matched

    def test_invalid_input_exits_1(self):
        rounds = Rounds(1)
        self.addCleanup(rounds.cleanup)
        rounds.finish(1)
        svg = Folder()
        self.addCleanup(svg.cleanup)
        (svg.path / "mockups" / "a.svg").write_text("<svg/>", encoding="utf-8")
        data = sample_decisions()
        data["items"][0]["images"][0]["src"] = "mockups/a.svg"  # never served: it can carry scripts
        svg.write(data)
        bad_commands = []
        # The last three: a bidi override, a trailing line break (it runs on paste), and a text over the length cap.
        for commands in ([], [{"text": "ls", "run": True}], [{"text": "ls" + chr(0x202E) + " -l"}],
                         [{"text": "ls -l\n"}], [{"text": "x" * 4001}]):
            folder = Folder()
            self.addCleanup(folder.cleanup)
            data = sample_decisions()
            data["items"][0]["commands"] = commands
            folder.write(data)
            bad_commands.append([str(folder.target)])
        folder = Folder()
        self.addCleanup(folder.cleanup)
        data = sample_decisions()
        data["items"][0]["blocking"] = "yes"
        folder.write(data)
        bad_commands.append([str(folder.target)])  # not a command, but checked the same way: exit 1 with a message
        # stakes outside its list, irreversible on two lines, settled with no recommended (the earlier decision).
        for change in ({"stakes": "huge"}, {"irreversible": "drops\nthe table"}, {"settled": "round-01"}):
            folder = Folder()
            self.addCleanup(folder.cleanup)
            data = sample_decisions()
            data["items"][0].update(change)
            if "settled" in change:
                del data["items"][0]["recommended"]
            folder.write(data)
            bad_commands.append([str(folder.target)])
        folder = Folder()
        self.addCleanup(folder.cleanup)
        data = sample_decisions()
        data["context"] = "feat/parser session\nsplitting the tokenizer"  # one line only
        folder.write(data)
        bad_commands.append([str(folder.target)])
        number = {"id": "budget", "title": "How much?", "select": "number", "range": {"min": 0, "max": 10},
                  "recommended": 5}
        for bad in (dict(number, recommended=5.5), dict(number, range={"min": 10, "max": 0}),
                    {k: v for k, v in number.items() if k != "recommended"},
                    dict(number, options=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]),
                    # Bounds a float cannot do the grid's arithmetic on: a message, never a crash.
                    dict(number, range={"min": -10 ** 308, "max": 10 ** 308, "step": 10 ** 305}, recommended=9e307)):
            folder = Folder()
            self.addCleanup(folder.cleanup)
            data = sample_decisions()
            data["items"].append(bad)
            folder.write(data)
            bad_commands.append([str(folder.target)])
        for args in [[], ["--no-such-option"], [str(rounds.path / "missing.json")], [str(rounds.target)],
                     [str(svg.target)]] + bad_commands:
            done = subprocess.run(COMMAND + args + ["--no-open"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  universal_newlines=True, encoding="utf-8", timeout=30)
            self.assertEqual((done.returncode, done.stdout), (1, ""), (args, done.stderr))
            if args in bad_commands:
                self.assertTrue(done.stderr.strip(), args)
                self.assertNotIn("Traceback", done.stderr, args)

    def test_an_items_commands_go_to_the_page_as_written(self):
        folder = Folder()
        self.addCleanup(folder.cleanup)
        data = sample_decisions()
        commands = [{"text": "echo '<b>x</b>'\n\tls", "lang": "bash", "caption": "Look"}, {"text": "rm -rf build"}]
        data["items"][0]["commands"] = commands
        folder.write(data)
        run = Run(folder.target)
        self.addCleanup(run.stop)
        body = run.request("GET", run.open())[1]
        page = json.loads(re.search(rb'id="signoff-data">(.*?)</script>', body, re.S).group(1))
        self.assertEqual(page["rounds"][0]["decisions"]["items"][0]["commands"], commands)
        self.assertNotIn(b"<b>x</b>", body)  # inside the data block it is escaped, never markup

    def test_an_item_marked_elsewhere_reaches_the_page_and_the_answers(self):
        # <command> elsewhere <round file> <item id> <note> while a plain run waits (no inbox has run): the command
        # writes the answers file itself, best effort, and the run takes it in as any outside write.
        folder = Folder()
        self.addCleanup(folder.cleanup)
        run = Run(folder.target)
        self.addCleanup(run.stop)
        page = run.open()
        layout = {"choice": "two", "comment": "", "source": "user"}
        self.assertEqual(run.save(0, {"answers": {"layout": layout}, "free_text": "", "done": False, "rev": 0})[0]
                         .status, 200)
        mark = subprocess.run(COMMAND + ["elsewhere", str(folder.target), "filters", "settled in chat"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              encoding="utf-8", timeout=30)
        self.assertEqual((mark.returncode, mark.stdout, mark.stderr), (0, "", ""))
        self.assertEqual(run.save(0, {"answers": {}, "free_text": "", "done": False, "rev": 1})[0].status, 409)
        marked = {"choice": [], "comment": "settled in chat", "source": "elsewhere"}
        deadline = time.monotonic() + 10
        while True:  # the run takes the file in within about a second
            resp, body = run.request("GET", page)
            data = json.loads(re.search(r'id="signoff-data">(.*?)</script>', body.decode("utf-8"), re.S).group(1))
            if data["rounds"][0]["rev"] == 2:
                break
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.3)
        self.assertEqual(data["rounds"][0]["answers"]["answers"]["filters"], marked)
        other = dict(layout, choice="one")
        self.assertEqual(run.save(0, {"answers": {"layout": other, "filters": marked}, "free_text": "", "done": False,
                                      "rev": 2})[0].status, 200)
        self.assertEqual(json.loads(folder.answers.read_text(encoding="utf-8"))["answers"]["filters"], marked)
        self.assertEqual(run.save(0, {"answers": {"layout": other, "filters": marked}, "free_text": "", "done": True,
                                      "rev": 3})[0].status, 200)
        out, err = run.finish()
        self.assertEqual((run.proc.returncode, out), (0, "%s\n" % folder.answers), err)
        saved = json.loads(folder.answers.read_text(encoding="utf-8"))
        self.assertEqual((saved["done"], saved["answers"]["filters"]), (True, marked))

    def test_idle_timeout_exits_2(self):
        for saved in (False, True):
            with self.subTest(saved=saved):
                folder = Folder()
                self.addCleanup(folder.cleanup)
                run = Run(folder.target, "--timeout", "0.02")
                self.addCleanup(run.stop)
                if saved:
                    run.open()
                    self.assertEqual(run.save(0, ANSWER)[0].status, 200)
                out, err = run.finish()
                self.assertEqual(run.proc.returncode, 2, err)
                self.assertEqual(out, "%s\n" % folder.answers if saved else "")

    @unittest.skipIf(sys.platform == "win32", "Ctrl+C is sent as SIGINT, SIGHUP is POSIX only")
    def test_ctrl_c_sigterm_and_sighup_exit_130(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sig=sig):
                folder = Folder()
                self.addCleanup(folder.cleanup)
                run = Run(folder.target)
                self.addCleanup(run.stop)
                run.open()  # a round trip: the process is serving, past its start, when the signal comes
                run.proc.send_signal(sig)
                out, err = run.finish()
                self.assertEqual((run.proc.returncode, out), (130, ""), err)


class History(unittest.TestCase):
    def test_the_past_decisions_page_is_read_only(self):
        root = HistoryRoot()
        self.addCleanup(root.cleanup)
        before = contents(root.path)
        run = Run("--history", root.path)
        self.addCleanup(run.stop)
        run.open()
        self.assertEqual(run.request("GET", run.page)[0].status, 200)
        self.assertEqual(run.request("GET", run.page, headers={"Host": "localhost:%d" % run.port})[0].status, 200)
        for host in ("evil.example", "localhost.example:%d" % run.port, "[::1]:%d" % run.port, "",
                     None, "127.0.0.1:%d" % (run.port + 1), "localhost:%d" % (run.port + 1)):
            self.assertEqual(run.request("GET", run.page, headers={"Host": host})[0].status, 403, host)
        for round_ in (0, 1):
            self.assertEqual(run.save(round_, ANSWER)[0].status, 405)
        resp, _ = run.request("POST", run.page + "prefs", PREFS, run.save_headers())
        self.assertEqual(resp.status, 405)  # the theme is kept by the inbox alone
        self.assertEqual(contents(root.path), before)

    def test_the_past_decisions_page_reads_listed_pictures_inside_their_folder_only(self):
        root = HistoryRoot()
        self.addCleanup(root.cleanup)
        run = Run("--history", root.path)
        self.addCleanup(run.stop)
        run.open()
        listed = re.findall(r'"src":\s*"(img/[0-9]+)"', run.request("GET", run.page)[1].decode("utf-8"))
        self.assertTrue(listed)
        self.assertEqual(run.request("GET", run.page + listed[0])[1], png_bytes())
        for path in ("img/999", "img/../x", ".signoff/2026-01-05-settings/mockups/a.png"):
            self.assertEqual(run.request("GET", run.page + path)[0].status, 404, path)
        if sys.platform == "win32":
            return  # symlinks need extra rights on Windows
        # Read on request here, so a picture swapped for a link out of its folder must not be followed.
        picture = root.path / ".signoff" / "2026-01-05-settings" / "mockups" / "a.png"
        (root.path / "secret.png").write_bytes(png_bytes() + b"SECRET")
        picture.rename(root.path / "moved.png")
        os.symlink(str(root.path / "secret.png"), str(picture))
        resp, body = run.request("GET", run.page + listed[0])
        self.assertEqual(resp.status, 404)
        self.assertNotIn(b"SECRET", body)


TODO = "- [ ] Ship the release\n- [x] done\n- [ ] Tag 1.0\n  - [ ] (waiting on you: sign it) Sign the tag\n"


@unittest.skipIf(sys.platform == "win32", "the inbox's control socket is a Unix socket; no inbox on Windows yet")
class Inbox(unittest.TestCase):
    """The inbox (`inbox serve`) as a separate process: start links only from its control socket, a pair that dies
    alone on reuse, and today's rules on its new routes, for a round a waiting run registered."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.home = os.path.join(os.path.realpath(cls._tmp.name), "home")
        cls.folder = board_folder()
        cls.addClassCleanup(cls.folder.cleanup)
        os.makedirs(os.path.join(cls.home, "runs"), mode=0o700)
        entry = {"schema": 1, "run": "0123456789abcdef", "project": None, "folder": str(cls.folder.path),
                 "rounds": ["decisions.json"], "mode": "waiter", "pid": os.getpid(), "started": "2026-01-01T00:00:00Z"}
        with open(os.path.join(cls.home, "runs", "0123456789abcdef.json"), "w", encoding="utf-8") as f:
            json.dump(entry, f)
        # A project the hooks noted, with an open todo item and a chain whose step waits: messages and Later on them
        # go to requests/<project id>/.
        cls.project = os.path.join(os.path.realpath(cls._tmp.name), "project")
        os.makedirs(os.path.join(cls.project, ".git"))
        with open(os.path.join(cls.project, "TODO.md"), "w", encoding="utf-8") as f:
            f.write(TODO)
        cls.pid = hashlib.sha256(cls.project.encode("utf-8")).hexdigest()[:16]
        os.makedirs(os.path.join(cls.home, "projects"), mode=0o700)
        with open(os.path.join(cls.home, "projects", cls.pid + ".json"), "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "id": cls.pid, "root": cls.project, "name": "project"}, f)
        # Throwaway XDG folders: an older implementation's scan route reads and writes Uptick's files there.
        xdg = {k: os.path.join(os.path.realpath(cls._tmp.name), d)
               for k, d in (("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config"))}
        cls.proc = subprocess.Popen(COMMAND + ["inbox", "serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=dict(os.environ, SIGNOFF_INBOX_HOME=cls.home, **xdg))

        def stop():
            if cls.proc.poll() is None:
                cls.proc.kill()
            cls.proc.communicate()
        cls.addClassCleanup(stop)
        deadline = time.monotonic() + 30
        while True:
            try:
                link = cls.link()
                break
            except OSError:
                assert cls.proc.poll() is None and time.monotonic() < deadline, "the inbox did not start"
                time.sleep(0.1)
        found = LINK.search(link)
        cls.origin, cls.port = found.group(1), int(found.group(3))
        cls.host = "127.0.0.1:%d" % cls.port

    @classmethod
    def link(cls):
        """A fresh start link from the control socket, the only place one comes from."""
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(10)
            s.connect(os.path.join(cls.home, "inbox.sock"))
            s.sendall(b'{"op": "open"}\n')
            return json.loads(s.makefile("rb").readline())["link"]

    def request(self, method, path, body=None, headers=None):
        run = Run.__new__(Run)  # only its plain HTTP: this process prints no link
        run.host, run.port = self.host, self.port
        return run.request(method, path, body, headers)

    def open(self):
        start = LINK.search(self.link()).group(4)
        resp, _ = self.request("GET", start)
        self.assertEqual(resp.status, 303)
        page = resp.getheader("Location")
        self.assertEqual(self.request("GET", page)[0].status, 200)  # the inbox page reads the run registry
        return start, page

    def answers(self):
        try:
            return self.folder.answers.read_bytes()
        except FileNotFoundError:
            return None

    def save(self, page, body=ANSWER, headers=None, path="run/0/save/0"):
        hdrs = {"Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"}
        hdrs.update(headers or {})
        return self.request("POST", page + path, body, hdrs)

    def save_refused(self, status, page, body=ANSWER, headers=None, path="run/0/save/0"):
        before = self.answers()
        resp, data = self.save(page, body, headers, path)
        self.assertEqual(resp.status, status, (headers, path, data))
        self.assertEqual(self.answers(), before)

    def test_a_reused_start_link_kills_only_its_pair(self):
        start_a, page_a = self.open()
        start_b, page_b = self.open()
        self.assertEqual(self.request("GET", start_a)[0].status, 410)
        self.assertEqual(self.request("GET", page_a)[0].status, 404)
        self.assertEqual(self.request("GET", page_b)[0].status, 200)
        self.assertEqual(self.request("GET", start_b)[0].status, 410)
        self.assertIsNone(self.proc.poll())

    def test_every_request_needs_a_token_and_the_exact_host(self):
        start, page = self.open()
        token = page.strip("/")
        for path in ("/", "/" + token, "/%s/" % ("A" * len(token)), "/%s/" % start.split("/")[2], page + "run/9/",
                     page + "run/0", page + "img/0"):
            self.assertEqual(self.request("GET", path)[0].status, 404, path)
        for host in ("localhost.example:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, "",
                     None):
            for path in (page, page + "run/0/", start):
                self.assertEqual(self.request("GET", path, headers={"Host": host})[0].status, 403, (host, path))
            self.save_refused(403, page, headers={"Host": host})

    def test_localhost_names_the_same_inbox(self):
        local = "localhost:%d" % self.port
        own = {"Host": local, "Origin": "http://" + local}
        start = LINK.search(self.link()).group(4)
        resp, _ = self.request("GET", start, headers={"Host": local})
        self.assertEqual(resp.status, 303)
        page = resp.getheader("Location")
        for path in (page, page + "state", page + "run/0/"):
            self.assertEqual(self.request("GET", path, headers={"Host": local})[0].status, 200, path)
        self.save_refused(403, page, headers={"Host": local})  # an Origin of the other name is not its own
        self.save_refused(403, page, headers={"Origin": "http://" + local})
        resp, data = self.save(page, headers=own)
        self.assertEqual(resp.status, 200, data)
        resp, data = self.save(page, {"sort": "oldest"}, own, path="prefs")  # the default: other tests see no change
        self.assertEqual(resp.status, 200, data)

    def test_saves_keep_todays_rules(self):
        start, page = self.open()
        port = self.port
        for origin in ("http://evil.example", "null", "http://localhost:%d" % port, self.origin + "/", None):
            self.save_refused(403, page, headers={"Origin": origin})
        for site in ("cross-site", "same-site", "none"):
            self.save_refused(403, page, headers={"Sec-Fetch-Site": site})
        for ctype in ("text/plain", "application/x-www-form-urlencoded", None):
            self.save_refused(415, page, headers={"Content-Type": ctype})
        self.save_refused(411, page, json.dumps(ANSWER).encode(), {"Content-Length": None})
        self.save_refused(413, page, b"{}", {"Content-Length": "41943040"})
        for body in (dict(ANSWER, done="yes"), {"answers": {"nope": {}}, "free_text": "", "done": False}, b"{x"):
            self.save_refused(400, page, body)
        for path in ("run/0/save/1", "run/1/save/0", "save/0", "run/0/save/0?theme=dark"):
            self.save_refused(404, page, path=path)
        resp, data = self.save(page)
        self.assertEqual(resp.status, 200, data)
        self.assertEqual(json.loads(self.folder.answers.read_text(encoding="utf-8"))["answers"]["layout"]["choice"],
                         "one")

    def test_pages_and_boards_keep_todays_csp(self):
        start, page = self.open()
        for path in (page, page + "run/0/"):
            resp, body = self.request("GET", path)
            csp = policy(resp)
            nonce = re.fullmatch(r"'nonce-([^']+)'", (csp.get("script-src") or [""])[0]).group(1)
            for name, sources in csp.items():
                self.assertLessEqual(set(sources), {"'none'", "'self'", "'nonce-%s'" % nonce}, (path, name))
            self.assertEqual(csp.get("frame-ancestors"), ["'none'"])
            self.assertIsNone(resp.getheader("Set-Cookie"))
        board = re.search(r'"board":\s*"(/[^"]+)"', self.request("GET", page + "run/0/")[1].decode("utf-8")).group(1)
        resp, body = self.request("GET", board)
        self.assertEqual((resp.status, body, policy(resp).get("sandbox")), (200, BOARD, ["allow-scripts"]))
        token = board.split("/")[1]
        for path in ("/%s/" % token, "/%s/run/0/" % token, "/%s/0/9" % token):
            self.assertEqual(self.request("GET", path)[0].status, 404, path)
        self.save_refused(404, "/%s/" % token)

    def test_inbox_lock_is_touched_while_the_inbox_lives(self):
        # The plugin's mod reads liveness from inbox.lock's time (it cannot flock): touched every few seconds.
        lock = os.path.join(self.home, "inbox.lock")
        os.utime(lock, (0, 0))
        deadline = time.monotonic() + 15
        while os.stat(lock).st_mtime < time.time() - 60:
            self.assertLess(time.monotonic(), deadline, "inbox.lock was not touched")
            time.sleep(0.1)
        self.assertEqual(os.stat(lock).st_size, 0)

    def test_the_state_route_counts_without_text_and_keeps_todays_rules(self):
        start, page = self.open()
        resp, body = self.request("GET", page + "state")
        self.assertEqual((resp.status, resp.getheader("Content-Type")), (200, "application/json"), body)
        data = json.loads(body)
        # The round, and the chain whose step waits: one row each.
        self.assertEqual((sorted(data), data["waiting"]), (["at", "projects", "schema", "waiting"], 2))
        for text in (str(self.folder.path), "decisions.json", self.home, "0123456789abcdef", page.strip("/")):
            self.assertNotIn(text.encode(), body)
        with open(os.path.join(self.home, "state.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["waiting"], data["waiting"])
        token = page.strip("/")
        board = re.search(r'"board":\s*"(/[^"]+)"', self.request("GET", page + "run/0/")[1].decode("utf-8")).group(1)
        for path in ("/state", "/%s/state" % ("A" * len(token)), "/%s/state" % start.split("/")[2],
                     page + "state/", page + "state?theme=dark", page + "run/0/state", "/%s/state" % board.split("/")[1]):
            self.assertEqual(self.request("GET", path)[0].status, 404, path)
        for host in ("localhost.example:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, "",
                     None):
            self.assertEqual(self.request("GET", page + "state", headers={"Host": host})[0].status, 403, host)
        self.save_refused(404, page, path="state")
        self.assertEqual(self.request("GET", start)[0].status, 410)
        self.assertEqual(self.request("GET", page + "state")[0].status, 404)

    def requests(self):
        found = []
        for folder, _, names in os.walk(os.path.join(self.home, "requests")):
            found += [os.path.join(folder, n) for n in names]
        return sorted(found)

    def test_a_message_on_a_todo_item_keeps_todays_rules(self):
        start, page = self.open()
        good = {"project": self.pid, "ref": "Ship the release", "text": "please split it"}
        before = self.requests()

        def refused(status, body=good, headers=None, path="request"):
            resp, data = self.save(page, body, headers, path)
            self.assertEqual(resp.status, status, (body, headers, path, data))
            self.assertEqual(self.requests(), before)

        for host in ("localhost.example:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, "",
                     None):
            refused(403, headers={"Host": host})
        for origin in ("http://evil.example", "null", "http://localhost:%d" % self.port, self.origin + "/", None):
            refused(403, headers={"Origin": origin})
        for site in ("cross-site", "same-site", "none"):
            refused(403, headers={"Sec-Fetch-Site": site})
        for ctype in ("text/plain", "application/x-www-form-urlencoded", None):
            refused(415, headers={"Content-Type": ctype})
        refused(411, json.dumps(good).encode(), {"Content-Length": None})
        refused(413, b"{}", {"Content-Length": "41943040"})
        for body in (b"{x", b"[]", dict(good, extra=1), dict(good, text=""), dict(good, text="two\nlines"),
                     dict(good, text="x" * 1001), dict(good, kind="answer"),
                     {"kind": "answer", "project": self.pid, "ref": "Ship the release"}):  # only the inbox writes one
            refused(400, body)
        refused(404, dict(good, project="f" * 16))
        refused(409, dict(good, ref="done"))
        board = re.search(r'"board":\s*"(/[^"]+)"', self.request("GET", page + "run/0/")[1].decode("utf-8")).group(1)
        for path in ("request/", "request?theme=dark", "run/0/request"):
            refused(404, path=path)
        resp, data = self.request("POST", "/%s/request" % board.split("/")[1], good, {
            "Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(resp.status, 404)
        self.assertEqual(self.request("GET", page + "request")[0].status, 404)
        resp, data = self.save(page, good, path="request")
        self.assertEqual(resp.status, 200, data)
        rid = json.loads(data)["id"]
        path = os.path.join(self.home, "requests", self.pid, rid + ".json")
        self.assertEqual(self.requests(), [path])
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual({k: saved[k] for k in ("schema", "id", "kind", "ref", "text")},
                         {"schema": 1, "id": rid, "kind": "todo", "ref": "Ship the release", "text": "please split it"})
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(os.path.join(self.project, "TODO.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), TODO)
        self.assertEqual(self.request("GET", start)[0].status, 410)
        before = self.requests()
        refused(404)  # a closed pair writes nothing more

    def test_later_on_a_waiting_step_keeps_todays_rules(self):
        start, page = self.open()
        later = {"kind": "later", "project": self.pid, "ref": "Sign the tag"}
        before = self.requests()

        def refused(status, body=later, headers=None, path="request"):
            resp, data = self.save(page, body, headers, path)
            self.assertEqual(resp.status, status, (body, headers, path, data))
            self.assertEqual(self.requests(), before)

        for host in ("localhost.example:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, "",
                     None):
            refused(403, headers={"Host": host})
        for origin in ("http://evil.example", "null", "http://localhost:%d" % self.port, self.origin + "/", None):
            refused(403, headers={"Origin": origin})
        for site in ("cross-site", "same-site", "none"):
            refused(403, headers={"Sec-Fetch-Site": site})
        for ctype in ("text/plain", "application/x-www-form-urlencoded", None):
            refused(415, headers={"Content-Type": ctype})
        refused(411, json.dumps(later).encode(), {"Content-Length": None})
        refused(413, b"{}", {"Content-Length": "41943040"})
        for body in (b"{x", b"[]", dict(later, extra="1"), dict(later, text="park it"), dict(later, kind="Later"),
                     dict(later, ref=1)):
            refused(400, body)
        refused(404, dict(later, project="f" * 16))
        for ref in ("Ship the release", "Tag 1.0", "done", ""):  # open but not waiting, done, or none
            refused(409, dict(later, ref=ref))
        for path in ("request/", "request?theme=dark", "run/0/request"):
            refused(404, path=path)
        resp, data = self.save(page, later, path="request")
        self.assertEqual(resp.status, 200, data)
        rid = json.loads(data)["id"]
        with open(os.path.join(self.home, "requests", self.pid, rid + ".json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((saved["kind"], saved["ref"], saved["text"]), ("later", "Sign the tag", (
            "The owner asks to park this item for later: remove its waiting marker and keep it open.")))
        with open(os.path.join(self.project, "TODO.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), TODO)
        self.assertEqual(self.request("GET", start)[0].status, 410)
        before = self.requests()
        refused(404)  # a closed pair writes nothing more

    def test_no_scan_route_and_no_finding_shape(self):
        """Scan this project and a finding handed over are gone: /scan gets what any unknown route gets, and a vuln
        or vulns request what any unknown shape gets. Nothing is written."""
        start, page = self.open()
        before = self.requests()
        for path in ("scan", "scan/", "run/0/scan"):
            resp, data = self.save(page, {"project": self.pid}, path=path)
            self.assertEqual(resp.status, 404, (path, data))
        self.assertEqual(self.request("GET", page + "scan")[0].status, 404)
        for body in ({"kind": "vuln", "project": self.pid, "ref": "lodash@4.17.20"},
                     {"kind": "vuln", "project": self.pid, "ref": "jinja2@2.10.0", "ecosystem": "PyPI"},
                     {"kind": "vulns", "project": self.pid, "count": 1}):
            resp, data = self.save(page, body, path="request")
            self.assertEqual(resp.status, 400, (body, data))
        self.assertEqual(self.requests(), before)

    def prefs(self):
        try:
            with open(os.path.join(self.home, "prefs.json"), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def test_the_theme_choice_keeps_todays_rules(self):
        start, page = self.open()
        board = re.search(r'"board":\s*"(/[^"]+)"', self.request("GET", page + "run/0/")[1].decode("utf-8")).group(1)

        def refused(status, body=PREFS, headers=None, path="prefs"):
            before = self.prefs()
            resp, data = self.save(page, body, headers, path)
            self.assertEqual(resp.status, status, (body, headers, path, data))
            self.assertEqual(self.prefs(), before)

        for host in ("localhost.example:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, "",
                     None):
            refused(403, headers={"Host": host})
        for origin in ("http://evil.example", "null", "http://localhost:%d" % self.port, self.origin + "/", None):
            refused(403, headers={"Origin": origin})
        for site in ("cross-site", "same-site", "none"):
            refused(403, headers={"Sec-Fetch-Site": site})
        for ctype in ("text/plain", "application/x-www-form-urlencoded", None):
            refused(415, headers={"Content-Type": ctype})
        refused(411, json.dumps(PREFS).encode(), {"Content-Length": None})
        refused(413, b"{}", {"Content-Length": "41943040"})
        refused(413, dict(PREFS, pad="x" * 1024))
        for bad in (b"{x", b"[]", dict(PREFS, extra=1), {"theme": "dark"}, dict(PREFS, theme="auto"),
                    dict(PREFS, palette="rosepine"), dict(PREFS, palette="</script>"), dict(PREFS, theme=["dark"]),
                    dict(PREFS, density="Compact"), dict(PREFS, density=None), dict(PREFS, density=1),
                    dict(PREFS, density="compact", extra=1), {"palette": "gruvbox", "density": "compact"},
                    dict(PREFS, sort="Newest"), dict(PREFS, sort=None), dict(PREFS, sort=["newest"]),
                    dict(PREFS, sort="newest", extra=1), {}, {"sort": "latest"}, {"sort": "newest", "extra": 1},
                    {"palette": "gruvbox", "sort": "newest"}):
            refused(400, bad)
        for other in ("prefs/", "prefs?theme=dark", "run/0/prefs", "Prefs"):
            refused(404, path=other)
        resp, _ = self.request("POST", "/%s/prefs" % board.split("/")[1], PREFS, {
            "Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(resp.status, 404)
        self.assertEqual(self.request("GET", page + "prefs")[0].status, 404)
        resp, data = self.save(page, PREFS, path="prefs")
        self.assertEqual(resp.status, 200, data)
        saved = json.loads(self.prefs())
        self.assertEqual(sorted(saved), ["at", "density", "palette", "schema", "sort", "theme"])
        self.assertEqual((saved["density"], saved["sort"]), ("comfortable", "oldest"))
        self.assertEqual((saved["schema"], saved["theme"], saved["palette"]), (1, "dark", "gruvbox"))
        self.assertEqual(os.stat(os.path.join(self.home, "prefs.json")).st_mode & 0o777, 0o600)
        for text in (page.strip("/"), start.split("/")[2], "http"):
            self.assertNotIn(text.encode(), self.prefs())
        for path in (page, page + "run/0/"):  # every page opens in it
            body = self.request("GET", path)[1].decode("utf-8")
            data = json.loads(re.search(r'id="signoff-data">(.*?)</script>', body, re.S).group(1))
            self.assertEqual((data["prefs"]["theme"], data["prefs"]["palette"]), ("dark", "gruvbox"), path)
            self.assertEqual(data["prefs"]["density"], "comfortable", path)
        # The inbox list's density: an optional third key, kept when a later body leaves it out.
        resp, data = self.save(page, dict(PREFS, density="compact"), path="prefs")
        self.assertEqual(resp.status, 200, data)
        self.assertEqual(json.loads(self.prefs())["density"], "compact")
        resp, data = self.save(page, PREFS, path="prefs")
        self.assertEqual(resp.status, 200, data)
        self.assertEqual(json.loads(self.prefs())["density"], "compact")
        # The Waiting on you order: the same, an optional key kept when a later body leaves it out.
        resp, data = self.save(page, dict(PREFS, sort="newest"), path="prefs")
        self.assertEqual(resp.status, 200, data)
        resp, data = self.save(page, dict(PREFS, density="comfortable"), path="prefs")
        self.assertEqual(resp.status, 200, data)
        saved = json.loads(self.prefs())
        self.assertEqual((saved["sort"], saved["density"]), ("newest", "comfortable"))
        resp, data = self.save(page, {"sort": "project"}, path="prefs")  # the sort alone keeps the rest
        self.assertEqual(resp.status, 200, data)
        saved = json.loads(self.prefs())
        self.assertEqual((saved["theme"], saved["palette"], saved["density"], saved["sort"]),
                         ("dark", "gruvbox", "comfortable", "project"))
        self.assertEqual(self.request("GET", start)[0].status, 410)
        refused(404)  # a closed pair writes nothing more


@unittest.skipIf(sys.platform == "win32", "the inbox's control socket is a Unix socket; no inbox on Windows yet")
class InboxPort(unittest.TestCase):
    """`inbox serve` with SIGNOFF_PORT: it listens there and says so in inbox.json; a taken port stops it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.join(os.path.realpath(tmp.name), "home")

    def serve(self, port):
        return subprocess.Popen(COMMAND + ["inbox", "serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, encoding="utf-8", errors="replace",
                                env=dict(os.environ, SIGNOFF_INBOX_HOME=self.home, SIGNOFF_PORT=str(port)))

    def test_the_inbox_takes_signoff_port(self):
        port = free_port()
        proc = self.serve(port)

        def stop():
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
        self.addCleanup(stop)
        deadline = time.monotonic() + 30
        while True:
            try:
                with socket.socket(socket.AF_UNIX) as s:
                    s.settimeout(10)
                    s.connect(os.path.join(self.home, "inbox.sock"))
                    s.sendall(b'{"op": "open"}\n')
                    link = json.loads(s.makefile("rb").readline())["link"]
                break
            except OSError:
                self.assertTrue(proc.poll() is None and time.monotonic() < deadline, "the inbox did not start")
                time.sleep(0.1)
        found = LINK.search(link)
        self.assertEqual((found.group(2), int(found.group(3))), ("127.0.0.1", port))
        with open(os.path.join(self.home, "inbox.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["port"], port)
        local = "localhost:%d" % port
        run = Run.__new__(Run)  # only its plain HTTP
        run.host, run.port = local, port
        resp, _ = run.request("GET", found.group(4))
        self.assertEqual(resp.status, 303)
        self.assertEqual(run.request("GET", resp.getheader("Location"))[0].status, 200)

    def test_a_taken_port_stops_it_in_one_line(self):
        for address in ("127.0.0.1", "0.0.0.0"):
            with socket.socket() as taken:
                taken.bind((address, 0))
                taken.listen()
                port = taken.getsockname()[1]
                proc = self.serve(port)
                out, err = proc.communicate(timeout=30)
            self.assertEqual((proc.returncode, out), (1, ""), (address, err))
            self.assertIn(str(port), err)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            # No inbox ran: the switch that puts runs and hooks in the inbox's mode stays off.
            self.assertFalse(os.path.exists(os.path.join(self.home, "inbox.lock")), address)


@unittest.skipIf(sys.platform == "win32", "the inbox's control socket is a Unix socket; no inbox on Windows yet")
class InboxTodoPath(unittest.TestCase):
    """The `todo` key of a project file in the home, for an inbox in a separate process: the file it names inside
    the root is the todo file; one out of the root (by `..`, an absolute path or a link), too big or not a file puts
    no text on the page, gives the project an error, and takes no message. The fixed names are not read then."""

    GOOD = "notes/tasks.md"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        base = os.path.realpath(cls._tmp.name)
        cls.home = os.path.join(base, "home")
        cls.secret = os.path.join(base, "secret.md")
        with open(cls.secret, "w", encoding="utf-8") as f:
            f.write("- [ ] SECRET item\n")
        os.makedirs(os.path.join(cls.home, "projects"), mode=0o700)
        cls.ids = {}
        for name, key in (("good", cls.GOOD), ("dotdot", "../secret.md"), ("absolute", cls.secret),
                          ("link", "out.md"), ("big", "big.md"), ("folder", "notes")):
            root = os.path.join(base, name)
            os.makedirs(os.path.join(root, "notes"))
            os.makedirs(os.path.join(root, ".git"))
            with open(os.path.join(root, "TODO.md"), "w", encoding="utf-8") as f:
                f.write("- [ ] Fixed item\n")
            with open(os.path.join(root, "notes", "tasks.md"), "w", encoding="utf-8") as f:
                f.write("- [ ] Custom task\n")
            os.symlink(cls.secret, os.path.join(root, "out.md"))
            with open(os.path.join(root, "big.md"), "w", encoding="utf-8") as f:
                f.write("- [ ] SECRET item\n" + "y" * (2 << 20))
            pid = cls.ids[name] = hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]
            with open(os.path.join(cls.home, "projects", pid + ".json"), "w", encoding="utf-8") as f:
                json.dump({"schema": 1, "id": pid, "root": root, "name": name, "todo": key}, f)
        cls.proc = subprocess.Popen(COMMAND + ["inbox", "serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=dict(os.environ, SIGNOFF_INBOX_HOME=cls.home))

        def stop():
            if cls.proc.poll() is None:
                cls.proc.kill()
            cls.proc.communicate()
        cls.addClassCleanup(stop)
        deadline = time.monotonic() + 30
        while True:
            try:
                link = cls.link()
                break
            except OSError:
                assert cls.proc.poll() is None and time.monotonic() < deadline, "the inbox did not start"
                time.sleep(0.1)
        found = LINK.search(link)
        cls.origin, cls.port = found.group(1), int(found.group(3))
        cls.host = "127.0.0.1:%d" % cls.port

    link = Inbox.__dict__["link"]
    request, open, save = Inbox.request, Inbox.open, Inbox.save

    def test_only_the_file_the_key_names_inside_the_root_is_read(self):
        page = self.open()[1]
        resp, html = self.request("GET", page)
        self.assertEqual(resp.status, 200)
        for text in (b"SECRET", self.secret.encode(), b"Fixed item"):
            self.assertNotIn(text, html)
        data = json.loads(re.search(rb'id="signoff-data">(.*?)</script>', html, re.S).group(1))
        projects = {p["name"]: p for p in data["projects"]}
        self.assertEqual(sorted(projects), sorted(self.ids))
        good = projects.pop("good")
        self.assertEqual((good["file"], good["error"], [t["ref"] for t in good["todos"]]),
                         (self.GOOD, None, ["Custom task"]))
        for name, p in projects.items():
            self.assertTrue(p["error"], name)
            self.assertEqual(p["todos"], [], name)
        for name, pid in self.ids.items():
            for ref in ("Custom task", "SECRET item", "Fixed item"):
                body = {"project": pid, "ref": ref, "text": "please split it"}
                resp, data = self.save(page, body, path="request")
                self.assertEqual(resp.status, 200 if (name, ref) == ("good", "Custom task") else 409, (name, ref))


@unittest.skipIf(sys.platform == "win32", "the inbox's control socket is a Unix socket; no inbox on Windows yet")
class InboxOldOps(unittest.TestCase):
    """Requests an older app sends to an inbox in a separate process: `offer` and `accept` are unknown requests and
    write nothing, and a plain `open` with an `offer` key (or any other key it does not know) gives the list's start
    link."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        base = os.path.realpath(cls._tmp.name)
        cls.home, cls.folder = os.path.join(base, "home"), os.path.join(base, "topic")
        os.makedirs(os.path.join(cls.home, "runs"), mode=0o700)
        os.makedirs(cls.folder)
        data = sample_decisions()
        data["items"] = [dict(data["items"][0], images=[])]  # one item, with a recommendation
        with open(os.path.join(cls.folder, "round-01.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        cls.answers = os.path.join(cls.folder, "round-01.answers.json")
        entry = {"schema": 1, "run": "fedcba9876543210", "project": None, "folder": cls.folder,
                 "rounds": ["round-01.json"], "mode": "waiter", "pid": os.getpid(), "started": "2026-01-01T00:00:00Z"}
        with open(os.path.join(cls.home, "runs", "fedcba9876543210.json"), "w", encoding="utf-8") as f:
            json.dump(entry, f)
        cls.proc = subprocess.Popen(COMMAND + ["inbox", "serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=dict(os.environ, SIGNOFF_INBOX_HOME=cls.home))

        def stop():
            if cls.proc.poll() is None:
                cls.proc.kill()
            cls.proc.communicate()
        cls.addClassCleanup(stop)
        deadline = time.monotonic() + 30
        while True:
            try:
                cls.ask({"op": "ping"})  # an unknown op: only an error back, so the socket answers
                break
            except OSError:
                assert cls.proc.poll() is None and time.monotonic() < deadline, "the inbox did not start"
                time.sleep(0.1)

    @classmethod
    def ask(cls, request):
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(10)
            s.connect(os.path.join(cls.home, "inbox.sock"))
            s.sendall(json.dumps(request).encode("utf-8") + b"\n")
            return json.loads(s.makefile("rb").readline())

    def get(self, link):
        found = LINK.search(link)
        conn = http.client.HTTPConnection("127.0.0.1", int(found.group(3)), timeout=10)
        conn.request("GET", found.group(4))
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp

    def test_offer_and_accept_are_unknown_and_write_nothing(self):
        unknown = self.ask({"op": "ping"})
        self.assertEqual(sorted(unknown), ["error"])
        for request in ({"op": "offer"}, {"op": "accept", "offer": "0" * 32},
                        {"op": "accept", "offer": "0" * 32, "choice": "one"}, {"op": "accept"}):
            self.assertEqual(self.ask(request), unknown, request)
        self.assertNotIn("http", unknown["error"])
        self.assertFalse(os.path.exists(self.answers))
        self.assertIsNone(self.proc.poll())

    def test_a_plain_open_with_keys_it_does_not_know_gives_the_list(self):
        for extra in ({"offer": "f" * 32}, {"offer": 12}, {"offer": "f" * 32, "x": 1}):
            resp = self.get(self.ask(dict({"op": "open"}, **extra))["link"])
            self.assertEqual(resp.status, 303, extra)
            self.assertRegex(resp.getheader("Location"), r"^/[A-Za-z0-9_-]+/$", extra)
        self.assertFalse(os.path.exists(self.answers))


@unittest.skipIf(sys.platform == "win32", "the inbox's control socket is a Unix socket; no inbox on Windows yet")
class InboxRows(unittest.TestCase):
    """The mod's panel list from an inbox in a separate process: `rows` on the control socket and `inbox rows
    <cwd>` on stdout, for a project's run the suite registers by hand. Numbers and one-line titles, nothing more."""

    TOKEN = re.compile(r"[A-Za-z0-9_-]{32,}")  # a start, page or board token

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        base = os.path.realpath(cls._tmp.name)
        cls.home, cls.project = os.path.join(base, "home"), os.path.join(base, "project")
        cls.folder = os.path.join(cls.project, ".signoff", "topic")
        os.makedirs(os.path.join(cls.home, "runs"), mode=0o700)
        os.makedirs(os.path.join(cls.project, ".git"))
        os.makedirs(cls.folder)
        cls.pid = hashlib.sha256(cls.project.encode("utf-8")).hexdigest()[:16]
        data = sample_decisions()
        data.pop("tab", None)
        data["title"] = "Panel \x1b]8;;\x07check\n%s" % cls.folder  # a terminal escape and a path from the agent
        data["items"] = [dict(it, images=[]) for it in data["items"]]
        with open(os.path.join(cls.folder, "round-01.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        cls.titles, cls.ids = [it["title"] for it in data["items"]], [it["id"] for it in data["items"]]
        entry = {"schema": 1, "run": "0123456789abcdef", "project": cls.pid, "folder": cls.folder,
                 "rounds": ["round-01.json"], "mode": "waiter", "pid": os.getpid(), "started": "2026-01-01T00:00:00Z"}
        with open(os.path.join(cls.home, "runs", "0123456789abcdef.json"), "w", encoding="utf-8") as f:
            json.dump(entry, f)
        cls.env = dict(os.environ, SIGNOFF_INBOX_HOME=cls.home)
        cls.proc = subprocess.Popen(COMMAND + ["inbox", "serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=cls.env)

        def stop():
            if cls.proc.poll() is None:
                cls.proc.kill()
            cls.proc.communicate()
        cls.addClassCleanup(stop)
        deadline = time.monotonic() + 30
        while True:
            try:
                cls.ask({"op": "open"})  # a start link nobody uses: a token that must not leak into the list
                break
            except OSError:
                assert cls.proc.poll() is None and time.monotonic() < deadline, "the inbox did not start"
                time.sleep(0.1)

    ask = InboxOldOps.__dict__["ask"]  # the classmethod itself, so cls is this class and its home

    def cli(self, *args):
        return subprocess.run(COMMAND + ["inbox", "rows"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, encoding="utf-8", env=self.env, timeout=30)

    def test_rows_on_the_socket_and_on_stdout(self):
        reply = self.ask({"op": "rows", "project": self.pid})
        number = reply.get("inbox")
        self.assertTrue(type(number) is int and 0 < number < 2 ** 31, reply)  # the inbox's own, for open's target
        want = {"rounds": [{"run": 0, "round": 0, "title": "Panel  ]8;; check project/.signoff/topic", "item": 0,
                            "items": [{"item": k, "title": t} for k, t in enumerate(self.titles)]}], "more": 0,
                "inbox": number}
        self.assertEqual(reply, want)
        got = self.cli(os.path.join(self.project, ".signoff"))
        self.assertEqual((got.returncode, json.loads(got.stdout)), (0, want), got.stderr)
        self.assertEqual(got.stdout.count("\n"), 1)
        for text in (json.dumps(reply), got.stdout, got.stderr):
            for leak in ("127.0.0.1", "/b/", "http", self.home, "0123456789abcdef"):
                self.assertNotIn(leak, text)
            self.assertIsNone(self.TOKEN.search(text), text)
            self.assertFalse(any(ord(c) < 32 for c in text.rstrip("\n")), text)
        self.assertEqual(self.ask({"op": "rows", "project": "f" * 16}), {"rounds": [], "more": 0, "inbox": number})
        self.assertIn("error", self.ask({"op": "rows", "project": None}))
        self.assertEqual(self.cli("relative/path").returncode, 1)
        self.assertIsNone(self.proc.poll())

    get = InboxOldOps.get

    def landing(self, link):
        resp = self.get(link)
        self.assertEqual(resp.status, 303)
        return resp.getheader("Location")

    def test_open_with_a_target_lands_on_that_item_in_focus(self):
        number = self.ask({"op": "rows", "project": self.pid})["inbox"]
        good = {"op": "open", "run": 0, "round": 0, "item": 1, "inbox": number}
        reply = self.ask(good)
        self.assertEqual(sorted(reply), ["link"])
        self.assertRegex(self.landing(reply["link"]), r"^/[A-Za-z0-9_-]+/run/0/#focus=0/%s$" % re.escape(self.ids[1]))
        # Gone, out of range or another inbox's number: the list's link, as a plain open gets.
        for stale in ({"run": 1}, {"round": 1}, {"item": len(self.ids)}, {"inbox": number % (2 ** 31 - 1) + 1}):
            self.assertRegex(self.landing(self.ask(dict(good, **stale))["link"]), r"^/[A-Za-z0-9_-]+/$", stale)
        # Malformed: an error, and no link.
        for bad in ({"run": True}, {"round": -1}, {"item": 1.0}, {"inbox": "1"}, {"run": 2 ** 31}, {"offer": "f" * 32}):
            self.assertEqual(sorted(self.ask(dict(good, **bad))), ["error"], bad)
        self.assertEqual(sorted(self.ask({"op": "open", "run": 0, "round": 0, "item": 0})), ["error"])  # not all four
        # The command: the same target, its start link on stderr with --no-open, nothing on stdout.
        got = subprocess.run(COMMAND + ["inbox", "open", "--run", "0", "--focus", "0/1", "--inbox", str(number),
                                        "--no-open"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, encoding="utf-8", env=self.env, timeout=30)
        self.assertEqual((got.returncode, got.stdout), (0, ""), got.stderr)
        self.assertRegex(self.landing(LINK.search(got.stderr).group(0)), r"/run/0/#focus=0/%s$" % re.escape(self.ids[1]))
        for bad in (["--run", "0"], ["--run", "x", "--focus", "0/1", "--inbox", "1"], ["--run", "0", "--focus", "0",
                    "--inbox", "1"], ["--run", "-1", "--focus", "0/1", "--inbox", "1"], ["--more"]):
            got = subprocess.run(COMMAND + ["inbox", "open"] + bad + ["--no-open"], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8", env=self.env,
                                 timeout=30)
            self.assertEqual((got.returncode, got.stdout), (1, ""), bad)
            self.assertNotIn("http", got.stderr, bad)
        self.assertIsNone(self.proc.poll())

    def test_a_target_kept_for_the_app_is_its_next_open_once(self):
        # Only the socket: the command's --app runs `open -a`, and an app started that way gets none of this
        # suite's environment (it would start against the user's own home).
        number = self.ask({"op": "rows", "project": self.pid})["inbox"]
        good = {"op": "app_target", "run": 0, "round": 0, "item": 1, "inbox": number}
        self.assertEqual(self.ask({"op": "app_open"}), {"link": None})
        self.assertEqual(self.ask(good), {"ok": True})
        self.assertEqual(sorted(self.ask({"op": "open"})), ["link"])  # a browser's open leaves it
        reply = self.ask({"op": "app_open"})
        self.assertEqual(sorted(reply), ["link"])
        self.assertRegex(self.landing(reply["link"]), r"^/[A-Za-z0-9_-]+/run/0/#focus=0/%s$" % re.escape(self.ids[1]))
        self.assertEqual(self.ask({"op": "app_open"}), {"link": None})  # used once
        self.ask(dict(good, inbox=number % (2 ** 31 - 1) + 1))  # another inbox's number: the list
        self.assertRegex(self.landing(self.ask({"op": "app_open"})["link"]), r"^/[A-Za-z0-9_-]+/$")
        for bad in ({"run": True}, {"round": -1}, {"item": 1.0}, {"inbox": "1"}, {"run": 2 ** 31}, {"run": 10 ** 400},
                    {"offer": "f" * 32}, {"focus": "0/1"}):
            self.assertEqual(sorted(self.ask(dict(good, **bad))), ["error"], bad)
        self.assertEqual(sorted(self.ask({"op": "app_target", "run": 0, "round": 0, "item": 0})), ["error"])
        self.assertEqual(sorted(self.ask({"op": "app_open", "offer": "f" * 32})), ["error"])
        self.assertEqual(self.ask({"op": "app_open"}), {"link": None})  # nothing malformed was kept
        self.assertIsNone(self.proc.poll())


class InboxRequests(unittest.TestCase):
    """`<command> inbox requests <folder> <project> <id>…`, whose stdout the mod hands to its session: one JSON line
    of the pending requests' lines and the ids gone, for request files the suite writes by hand, when the session
    folder is in that project (its id: the first 16 hex digits of the SHA-256 of its root's real path). No inbox
    runs."""

    R = ["%016x" % n for n in range(1, 13)]
    FRAME = re.compile(r"Signoff inbox: [^\x00-\x1f\x7f  ]* run: signoff inbox done ([0-9a-f]{16})")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = os.path.realpath(tmp.name)
        self.home = os.path.join(self.base, "home")
        self.root = os.path.join(self.base, "proj")
        os.makedirs(os.path.join(self.root, ".git"))
        os.makedirs(os.path.join(self.root, "src"))
        self.project = hashlib.sha256(self.root.encode("utf-8")).hexdigest()[:16]
        self.folder = os.path.join(self.home, "requests", self.project)
        self.env = dict(os.environ, SIGNOFF_INBOX_HOME=self.home)

    def write(self, rid, at, text="please split it", kind="todo"):
        os.makedirs(self.folder, exist_ok=True)
        with open(os.path.join(self.folder, rid + ".json"), "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "id": rid, "kind": kind, "ref": "Ship it", "text": text, "at": at}, f)

    def cli(self, *args, folder=None, env=None):
        """The session folder goes first: the project's src/ unless given; folder="" leaves it out."""
        folder = [os.path.join(self.root, "src") if folder is None else folder] if folder != "" else []
        return subprocess.run(COMMAND + ["inbox", "requests"] + folder + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env or self.env, timeout=30)

    def test_one_line_of_the_pending_lines_oldest_first_and_the_ids_gone(self):
        R = self.R
        self.write(R[0], "2026-11-07T10:00:02Z", text='a "quote"\nnext line\x1b[31m')
        self.write(R[1], "2026-11-07T10:00:01Z")
        self.write(R[2], "2026-11-07T10:00:00Z")
        open(os.path.join(self.folder, R[2] + ".done"), "wb").close()
        self.write(R[3], "2026-11-07T10:00:00Z", kind="no-such-kind")
        got = self.cli(self.project, R[0], R[2], R[1], R[3], R[4])
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual((got.stdout.count(b"\n"), got.stdout[-1:]), (1, b"\n"))
        reply = json.loads(got.stdout.decode("ascii"))
        self.assertEqual(sorted(reply), ["gone", "requests"])
        self.assertEqual([r["id"] for r in reply["requests"]], [R[1], R[0]])
        self.assertEqual(reply["gone"], [R[2], R[3], R[4]])
        for r in reply["requests"]:
            m = self.FRAME.fullmatch(r["line"])
            self.assertTrue(m and m.group(1) == r["id"], r["line"])
        self.assertNotIn(self.home.encode("utf-8"), got.stdout)
        self.assertEqual(sorted(os.listdir(self.folder)), sorted([R[0] + ".json", R[1] + ".json", R[2] + ".json",
                                                                   R[2] + ".done", R[3] + ".json"]))  # nothing written

    def test_no_folder_has_every_id_gone(self):
        got = self.cli(self.project, self.R[0])
        self.assertEqual((got.returncode, json.loads(got.stdout)), (0, {"requests": [], "gone": [self.R[0]]}), got.stderr)

    def test_refusals_print_nothing_on_stdout(self):
        self.write(self.R[0], "2026-11-07T10:00:00Z")
        for args in ((), (self.project,), (self.project,) + tuple(self.R[:11]), (self.project, self.R[0], self.R[0]),
                     (self.project.upper(), self.R[0]), (self.project[1:], self.R[0]), (self.project, self.R[0] + "0")):
            got = self.cli(*args)
            self.assertEqual((got.returncode, got.stdout), (1, b""), args)
        got = self.cli(self.project, self.R[0], env=dict(self.env, SIGNOFF_INBOX_HOME="relative/home"))
        self.assertEqual((got.returncode, got.stdout), (1, b""))

    def test_only_for_the_project_its_session_folder_is_in(self):
        # A nested project (its own .git) is another project, though the mod may not know its root yet.
        self.write(self.R[0], "2026-11-07T10:00:00Z")
        nested = os.path.join(self.root, "sub")
        os.makedirs(os.path.join(nested, ".git"))
        outside = os.path.join(self.base, "else")
        os.makedirs(outside)
        for folder in (nested, os.path.join(nested, "deep"), outside, "proj/src", ""):
            got = self.cli(self.project, self.R[0], folder=folder)
            self.assertEqual((got.returncode, got.stdout), (1, b""), folder)
        got = self.cli(self.project, self.R[0], folder=self.root)
        self.assertEqual((got.returncode, [r["id"] for r in json.loads(got.stdout)["requests"]]), (0, [self.R[0]]),
                         got.stderr)


@unittest.skipIf(sys.platform == "win32", "the inbox's control socket is a Unix socket; no inbox on Windows yet")
class InboxWaiter(unittest.TestCase):
    """A run while an inbox lives is a waiter. Once inbox.lock's time has changed twice since the run noted itself
    (the inbox touches it at each look, before it reads runs/), a round lock nobody holds means the inbox did not
    take the round in, as one from before a key the round uses: the run says so on stderr and exits 1."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = os.path.realpath(tmp.name)
        self.home, self.folder = os.path.join(base, "home"), os.path.join(base, "topic")
        os.makedirs(self.home, mode=0o700)
        os.makedirs(self.folder)
        with open(os.path.join(self.folder, "round-01.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "title": "Commands", "items": [{
                "id": "pick", "title": "Which one?", "select": "one", "recommended": "a",
                "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                "commands": [{"text": "ls -l"}]}]}, f)
        self.lock = os.path.join(self.folder, "round-01.json.lock")
        self.env = dict(os.environ, SIGNOFF_INBOX_HOME=self.home)

    def child(self, *args):
        proc = subprocess.Popen(COMMAND + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, encoding="utf-8", errors="replace", env=self.env)

        def stop():
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
        self.addCleanup(stop)
        return proc

    def stand_in(self, every=0.3, lock_after=None):
        """An inbox that lives here (it holds inbox.lock) and touches inbox.lock every `every` seconds. Without
        lock_after it locks no round, as an inbox that refused it; with it, a look that met an entry in runs/ at its
        touch locks the round that many seconds later, as a slow current inbox does."""
        import fcntl
        fd = os.open(os.path.join(self.home, "inbox.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop, held = threading.Event(), []

        def look():
            while not stop.wait(every):
                os.utime(os.path.join(self.home, "inbox.lock"))
                runs = os.path.join(self.home, "runs")
                met = os.path.isdir(runs) and any(not n.startswith(".") for n in os.listdir(runs))
                if lock_after is not None and met and not held and not stop.wait(lock_after):
                    f = os.open(self.lock, os.O_RDWR | os.O_CREAT, 0o600)
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held.append(f)
        looks = threading.Thread(target=look, daemon=True)
        looks.start()
        self.addCleanup(lambda: [os.close(f) for f in held])
        self.addCleanup(looks.join)
        self.addCleanup(stop.set)

    def test_a_waiter_whose_round_the_inbox_does_not_take_in_exits_1(self):
        self.stand_in()
        proc = self.child(self.folder, "--no-open")
        out, err = proc.communicate(timeout=30)
        self.assertEqual((proc.returncode, out), (1, ""), err)
        self.assertIn("signoff inbox stop", err)
        self.assertNotIn("http://", err)
        self.assertFalse(os.path.exists(self.lock))
        self.assertFalse(os.path.exists(os.path.join(self.folder, "round-01.answers.json")))

    def test_a_waiter_on_an_inbox_whose_look_locks_late_keeps_waiting(self):
        # The round is locked 1.5 s after the touch: judged on one touch, it would seem refused.
        self.stand_in(every=2.0, lock_after=1.5)
        proc = self.child(self.folder, "--no-open")
        time.sleep(9)
        self.assertIsNone(proc.poll(), proc.poll() is not None and proc.communicate())

    def test_a_waiter_whose_round_a_live_inbox_took_in_keeps_waiting(self):
        self.child("inbox", "serve")
        deadline = time.monotonic() + 30
        while True:
            try:
                with socket.socket(socket.AF_UNIX) as s:
                    s.connect(os.path.join(self.home, "inbox.sock"))
                break
            except OSError:
                self.assertLess(time.monotonic(), deadline, "the inbox did not start")
                time.sleep(0.1)
        proc = self.child(self.folder, "--no-open")
        seen, deadline = set(), time.monotonic() + 30
        while len(seen) < 5:  # four looks since the run started, then one more poll of the waiter's
            seen.add(os.stat(os.path.join(self.home, "inbox.lock")).st_mtime_ns)
            self.assertLess(time.monotonic(), deadline, "the inbox did not look")
            self.assertIsNone(proc.poll(), proc.poll() is not None and proc.communicate())
            time.sleep(0.1)
        time.sleep(1.5)
        self.assertIsNone(proc.poll(), proc.poll() is not None and proc.communicate())
        import fcntl
        fd = os.open(self.lock, os.O_RDWR)
        try:
            with self.assertRaises(BlockingIOError):  # the inbox holds it: it took the round in
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


MARKED_ROUND = {"version": 1, "title": "Settled in chat", "items": [
    {"id": i, "title": "Question " + i, "select": "one", "recommended": "a",
     "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]} for i in ("q", "r")]}
REFUSALS = ("gone", "done", "changed", "held", "failed", "answered")


@unittest.skipIf(sys.platform == "win32", "the inbox's control socket is a Unix socket; no inbox on Windows yet")
class Elsewhere(unittest.TestCase):
    """<command> elsewhere <round file> <item id> [<note>] once an inbox has run: with a live inbox holding the round
    lock, the `elsewhere` op on its control socket, which writes through the run's own writer; with a page run
    holding it, exit 1. A page Done and a mark at the same moment never end with a run's exit 0 over an answers file
    that says done: false: one of them wins, the other is refused, and nothing the winner wrote is lost."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = os.path.realpath(tmp.name)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home, mode=0o700)
        self.env = dict(os.environ, SIGNOFF_INBOX_HOME=self.home)
        self.topics = 0

    def child(self, *args):
        proc = subprocess.Popen(COMMAND + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, encoding="utf-8", errors="replace", env=self.env)

        def stop():
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
        self.addCleanup(stop)
        return proc

    def topic(self, answers=None):
        self.topics += 1
        folder = os.path.join(self.base, "topic-%d" % self.topics)
        os.makedirs(folder)
        with open(os.path.join(folder, "round-01.json"), "w", encoding="utf-8") as f:
            json.dump(MARKED_ROUND, f)
        if answers is not None:
            with open(os.path.join(folder, "round-01.answers.json"), "w", encoding="utf-8") as f:
                json.dump({"version": 1, "done": False, "saved": "2026-01-01T12:00:00Z", "answers": answers,
                           "free_text": ""}, f)
        return folder

    def files(self, folder):
        found = {}
        for n in sorted(os.listdir(folder)):
            with open(os.path.join(folder, n), "rb") as f:
                found[n] = f.read()
        return found

    def saved(self, folder):
        with open(os.path.join(folder, "round-01.answers.json"), encoding="utf-8") as f:
            return json.load(f)

    def mark(self, folder, item, *note):
        return subprocess.run(COMMAND + ["elsewhere", os.path.join(folder, "round-01.json"), item] + list(note),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              encoding="utf-8", errors="replace", env=self.env, timeout=30)

    def ask(self, request):
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(10)
            s.connect(os.path.join(self.home, "inbox.sock"))
            s.sendall(json.dumps(request).encode("utf-8") + b"\n")
            return json.loads(s.makefile("rb").readline())

    def inbox(self):
        proc = self.child("inbox", "serve")
        deadline = time.monotonic() + 30
        while True:
            try:
                link = self.ask({"op": "open"})["link"]
                break
            except OSError:
                self.assertIsNone(proc.poll(), "the inbox did not start")
                self.assertLess(time.monotonic(), deadline, "the inbox did not start")
                time.sleep(0.1)
        found = LINK.search(link)
        self.web = Run.__new__(Run)  # only its plain HTTP
        self.web.host, self.web.port = "127.0.0.1:%s" % found.group(3), int(found.group(3))
        self.origin = found.group(1)
        resp, _ = self.web.request("GET", found.group(4))
        self.assertEqual(resp.status, 303)
        self.page = resp.getheader("Location")
        return proc

    def held(self, folder):
        import fcntl
        fd = os.open(os.path.join(folder, "round-01.json.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        finally:
            os.close(fd)  # closing lets go of a lock this test took
        return False

    def waiter(self, folder):
        """A run while the inbox lives, once the inbox took its round in (it holds the round's lock)."""
        proc = self.child(folder, "--no-open")
        deadline = time.monotonic() + 30
        while not self.held(folder):
            self.assertIsNone(proc.poll(), proc.poll() is not None and proc.communicate())
            self.assertLess(time.monotonic(), deadline, "the inbox did not take the round in")
            time.sleep(0.1)
        return proc

    def rev(self, run):
        resp, body = self.web.request("GET", "%srun/%d/" % (self.page, run))
        self.assertEqual(resp.status, 200)
        return json.loads(re.search(r'id="signoff-data">(.*?)</script>', body.decode("utf-8"), re.S)
                          .group(1))["rounds"][0]["rev"]

    def done(self, run, rev, out, answers=None):
        resp, _ = self.web.request("POST", "%srun/%d/save/0" % (self.page, run), {"answers": answers or {},
                                   "free_text": "", "done": True, "rev": rev},
                                   {"Content-Type": "application/json", "Origin": self.origin})
        out.append(resp.status)

    def refused(self, folder, item, *note, words=()):
        before = self.files(folder)
        mark = self.mark(folder, item, *note)
        self.assertEqual((mark.returncode, mark.stdout, mark.stderr.count("\n")), (1, "", 1), mark.stderr)
        self.assertTrue(mark.stderr.startswith("signoff: "), mark.stderr)
        for w in words:
            self.assertIn(w, mark.stderr)
        self.assertEqual(self.files(folder), before)

    def test_the_op_its_refusals_and_the_commands(self):
        self.inbox()
        folder = self.topic({"r": {"choice": "b", "comment": "", "source": "user"}})
        proc = self.waiter(folder)
        round_file = os.path.join(folder, "round-01.json")
        good = {"op": "elsewhere", "round": round_file, "item": "q", "note": "n"}
        before = self.files(folder)
        for req in (dict(good, round="round-01.json"), dict(good, item=["q"]), dict(good, note="a\nb"),
                    dict(good, note="x" * 501), dict(good, more=True), {"op": "elsewhere"}):
            reply = self.ask(req)
            self.assertEqual(sorted(reply), ["error"], req)
            self.assertNotIn(reply["error"], REFUSALS, req)
        self.assertEqual(self.ask(dict(good, round=os.path.join(folder, "round-02.json"))), {"error": "gone"})
        self.assertEqual(self.ask(dict(good, item="r")), {"error": "answered"})  # the user's own answer stays
        self.assertEqual(self.files(folder), before)
        self.refused(folder, "nope", "n", words=("nope",))
        self.refused(folder, "q", "two\nlines", words=("note",))
        self.refused(folder, "r", "n", words=("user",))
        rev = self.rev(0)
        mark = self.mark(folder, "q", "settled in chat")
        self.assertEqual((mark.returncode, mark.stdout, mark.stderr), (0, "", ""))
        self.assertEqual(self.saved(folder)["answers"]["q"], {"choice": None, "comment": "settled in chat",
                                                              "source": "elsewhere"})
        self.assertTrue(self.held(folder))  # written by the inbox, which holds the lock still
        self.assertEqual(self.rev(0), rev + 1)
        self.assertEqual(self.ask(dict(good, note="settled in chat")), {"ok": True})  # marked so already
        status = []
        self.done(0, rev + 1, status, self.saved(folder)["answers"])  # the reloaded page sends the mark back
        self.assertEqual(status, [200])
        out, err = proc.communicate(timeout=30)
        self.assertEqual((proc.returncode, out), (0, os.path.join(folder, "round-01.answers.json") + "\n"), err)
        saved = self.saved(folder)
        self.assertEqual((saved["done"], saved["answers"]["q"]["source"]), (True, "elsewhere"))
        self.refused(folder, "q", "late", words=("done",))

    def test_a_page_done_and_a_mark_at_once_through_the_inbox(self):
        self.inbox()
        warm = self.topic()
        self.waiter(warm)
        began = time.monotonic()
        self.assertEqual(self.mark(warm, "q", "n").returncode, 0)
        took = time.monotonic() - began  # how long a mark takes to land: the Done is sent around that time
        outcomes = set()
        # The ends are far apart, so each arm comes up whatever the machine's pace (a Done sent at once lands
        # before a mark, which waits half a second on the inbox's lock first; one sent at three times a mark's
        # time lands after it); the middle ones meet the mark where they may.
        for run, share in enumerate((0.0, 0.6, 0.85, 0.95, 1.05, 1.2, 1.6, 3.0), 1):
            folder = self.topic()
            proc = self.waiter(folder)
            rev, status = self.rev(run), []
            page = threading.Timer(took * share, self.done, (run, rev, status))
            page.start()
            mark = self.mark(folder, "q", "settled in chat")
            page.join(30)
            self.assertEqual(mark.stdout, "")
            if status == [200]:  # the Done first: final, so the mark is refused and the run hands the answers over
                self.assertEqual(mark.returncode, 1, mark.stderr)
                out, err = proc.communicate(timeout=30)
                self.assertEqual((proc.returncode, out), (0, os.path.join(folder, "round-01.answers.json") + "\n"),
                                 err)
                self.assertEqual((self.saved(folder)["done"], self.saved(folder)["answers"]), (True, {}))
            else:  # the mark first: the page's rev is stale, and the run waits on
                self.assertEqual((status, mark.returncode), ([409], 0), mark.stderr)
                saved = self.saved(folder)
                self.assertEqual((saved["done"], saved["answers"]["q"]["source"]), (False, "elsewhere"))
                time.sleep(1.5)
                self.assertIsNone(proc.poll(), "a run must not end while its round is open")
            outcomes.add(status[0])
        self.assertEqual(outcomes, {200, 409}, "both the Done-first and the mark-first arm must have run")

    def test_a_page_done_and_a_mark_at_once_beside_a_page_run(self):
        # An inbox has run (inbox.lock), none runs now: the page run holds the round lock, so the mark is refused.
        with open(os.path.join(self.home, "inbox.lock"), "w"):
            pass
        for delay in (0.0, 0.05, 0.2):
            folder = self.topic()
            saved_env = os.environ.get("SIGNOFF_INBOX_HOME")
            os.environ["SIGNOFF_INBOX_HOME"] = self.home
            try:
                run = Run(os.path.join(folder, "round-01.json"))
            finally:
                os.environ["SIGNOFF_INBOX_HOME"] = saved_env
            self.addCleanup(run.stop)
            run.open()
            status = []
            page = threading.Timer(delay, lambda: status.append(run.save(0, {"answers": {}, "free_text": "",
                                                                               "done": True, "rev": 0})[0].status))
            page.start()
            mark = self.mark(folder, "q", "n")
            page.join(30)
            self.assertEqual((mark.returncode, mark.stdout, mark.stderr.count("\n")), (1, "", 1), mark.stderr)
            self.assertEqual(status, [200])
            out, err = run.finish()
            self.assertEqual((run.proc.returncode, out), (0, os.path.join(folder, "round-01.answers.json") + "\n"),
                             err)
            self.assertEqual((self.saved(folder)["done"], self.saved(folder)["answers"]), (True, {}))


@unittest.skipIf(sys.platform == "win32", "the inbox's control socket is a Unix socket; no inbox on Windows yet")
class Plan(unittest.TestCase):
    """<command> plan --topic <t> --folder <f> [--resume], the plan on stdin: one fixed round beside the plan, a
    waiter on it beside <command> inbox serve, and the one answers path on stdout once the user's Done is saved
    through the inbox's page. Without a live inbox it exits 1 and writes nothing."""

    ask, inbox, held, rev = Elsewhere.ask, Elsewhere.inbox, Elsewhere.held, Elsewhere.rev
    TOPIC = "2026-11-15-plan-abcd1234"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = os.path.realpath(tmp.name)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home, mode=0o700)
        self.env = dict(os.environ, SIGNOFF_INBOX_HOME=self.home)
        self.folder = os.path.join(self.base, "decisions")
        self.topic = os.path.join(self.folder, self.TOPIC)

    def child(self, *args):  # Elsewhere's, with a pipe on stdin
        proc = subprocess.Popen(COMMAND + list(args), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8", errors="replace",
                                env=self.env)

        def stop():
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
        self.addCleanup(stop)
        return proc

    def plan(self, text):
        proc = self.child("plan", "--topic", self.TOPIC, "--folder", self.folder)
        proc.stdin.write(text)
        proc.stdin.close()
        proc.stdin = None  # Python 3.9's communicate() would flush the closed pipe
        return proc

    def test_without_a_live_inbox_it_exits_1_and_writes_nothing(self):
        with open(os.path.join(self.home, "inbox.lock"), "w"):
            pass  # an inbox has run here, none runs now
        proc = self.plan("# Plan\n")
        out, err = proc.communicate(timeout=30)
        self.assertEqual((proc.returncode, out, err.count("\n")), (1, "", 1), err)
        self.assertFalse(os.path.exists(self.folder))
        self.assertEqual(sorted(os.listdir(self.home)), ["inbox.lock"])

    def test_an_approval_left_at_the_next_number_is_never_taken(self):
        # What `git clean -fd` keeps of an earlier review (answers files are ignored): a done approval, no round.
        self.inbox()
        os.makedirs(self.topic)
        with open(os.path.join(self.topic, "round-01.answers.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "done": True, "saved": "2026-01-01T12:00:00Z", "free_text": "",
                       "answers": {"plan": {"choice": "approve", "comment": "", "source": "user"}}}, f)
        for resume in ((), ("--resume",)):
            proc = self.child("plan", "--topic", self.TOPIC, "--folder", self.folder, *resume)
            proc.stdin.write("# Plan\n")
            proc.stdin.close()
            proc.stdin = None
            import fcntl
            lock, deadline = os.path.join(self.topic, "round-02.json.lock"), time.monotonic() + 30
            while True:  # the inbox took round 2 in: it holds its lock
                self.assertIsNone(proc.poll(), proc.poll() is not None and proc.communicate())
                self.assertLess(time.monotonic(), deadline, "the inbox did not take round 2 in")
                if os.path.exists(lock):
                    fd = os.open(lock, os.O_RDWR)
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except BlockingIOError:
                        break
                    finally:
                        os.close(fd)
                time.sleep(0.1)
            time.sleep(1.5)
            self.assertIsNone(proc.poll(), proc.poll() is not None and proc.communicate())
            self.assertFalse(os.path.exists(os.path.join(self.topic, "round-01.json")))
            self.assertFalse(os.path.exists(os.path.join(self.topic, "round-02.answers.json")))
            proc.kill()
            self.assertEqual(proc.communicate(timeout=20)[0], "")

    def test_approve_through_the_inbox_page_exits_0_with_the_one_answers_path(self):
        self.inbox()
        proc = self.plan("# Plan\n\n1. One step\n\n")
        round_file = os.path.join(self.topic, "round-01.json")
        deadline = time.monotonic() + 30
        while not (os.path.exists(round_file) and self.held(self.topic)):
            self.assertIsNone(proc.poll(), proc.poll() is not None and proc.communicate())
            self.assertLess(time.monotonic(), deadline, "the inbox did not take the plan round in")
            time.sleep(0.1)
        with open(os.path.join(self.topic, "plan-01.md"), "rb") as f:
            self.assertEqual(f.read(), b"# Plan\n\n1. One step")
        resp, data = self.web.request("POST", "%srun/0/save/0" % self.page, {
            "answers": {"plan": {"choice": "approve", "comment": "", "source": "user"}}, "free_text": "",
            "done": True, "rev": self.rev(0)}, {"Content-Type": "application/json", "Origin": self.origin,
                                                "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(resp.status, 200, data)
        out, err = proc.communicate(timeout=30)
        self.assertEqual((proc.returncode, out), (0, os.path.join(self.topic, "round-01.answers.json") + "\n"), err)
        self.assertNotIn("http://", err)
        with open(os.path.join(self.topic, "round-01.answers.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((saved["done"], saved["answers"]["plan"]["choice"]), (True, "approve"))


if __name__ == "__main__":
    unittest.main()
