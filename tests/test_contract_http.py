"""The security and run contract in AGENTS.md, checked against a separate process. Each test starts the command
line as a child and talks to it only over HTTP, through its exit code and its stdout, so the same suite can run
against another implementation (the inbox mode, a port): set SIGNOFF_CONTRACT_CMD to its command, which gets the
same arguments (<decisions file or folder> or --history ROOT, --timeout, --no-open) and prints its start link on
stderr. The fixtures from support.py and test_extras.py are files on disk; nothing here calls into signoff.py."""
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

from support import ROOT, Folder, Rounds, png_bytes, sample_decisions
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

    def __init__(self, *args):
        self.proc = subprocess.Popen(COMMAND + [str(a) for a in args] + ["--no-open"], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8",
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
                if v is not None:
                    conn.putheader(k, v)
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
        for host in ("localhost:%d" % port, "127.0.0.1", "127.0.0.1:%d.evil.example" % port, "evil.example",
                     "[::1]:%d" % port, "127.0.0.1:%d" % (port + 1), None):
            hdrs = {"Host": host}
            self.refused(403, "GET", self.page, headers=hdrs)
            self.refused(403, "GET", self.board, headers=hdrs)
            self.save_refused(403, headers=hdrs)

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
        self.assertEqual(run.request("GET", run.start, headers={"Host": "evil.example"})[0].status, 403)
        for path in ("/b/%s/" % ("A" * len(token)), "/b/%s" % token, "/b/", "/b/%s/x" % token):
            self.assertEqual(run.request("GET", path)[0].status, 404, path)
        resp, _ = run.request("POST", run.start, ANSWER, {"Content-Type": "application/json", "Origin": run.origin})
        self.assertEqual(resp.status, 404)
        self.assertEqual(run.request("GET", run.start)[0].status, 303)

    def test_each_run_gets_its_own_port_and_tokens(self):
        a, b = self.run_once(), self.run_once()
        self.assertNotEqual(a.port, b.port)
        self.assertNotEqual(a.start, b.start)
        self.assertNotEqual(a.open(), b.open())
        self.assertEqual(a.request("GET", b.page)[0].status, 404)


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
        for args in ([], ["--no-such-option"], [str(rounds.path / "missing.json")], [str(rounds.target)],
                     [str(svg.target)]):
            done = subprocess.run(COMMAND + args + ["--no-open"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  universal_newlines=True, encoding="utf-8", timeout=30)
            self.assertEqual((done.returncode, done.stdout), (1, ""), (args, done.stderr))

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
        self.assertEqual(run.request("GET", run.page, headers={"Host": "evil.example"})[0].status, 403)
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
        # Uptick's files: a fresh scan of the project with one finding, and a scan list without it yet.
        cls.cache, cls.config = (os.path.join(os.path.realpath(cls._tmp.name), d) for d in ("cache", "config"))
        os.makedirs(os.path.join(cls.cache, "uptick"))
        os.makedirs(os.path.join(cls.config, "uptick"))
        with open(os.path.join(cls.cache, "uptick", "vulns.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "targets": {}, "repos": {cls.project: {
                "at": time.time(), "tried": time.time(), "count": 1, "max": "7.5", "level": "high", "gaps": [],
                "error": "", "hint": "", "prev": None, "details": [{
                    "name": "lodash", "version": "4.17.20", "ecosystem": "npm", "ids": ["GHSA-aaaa-bbbb-cccc"],
                    "advisories": 1, "severity": "7.5", "level": "high", "fixed": "4.17.21",
                    "sources": ["package-lock.json"]}]}}}, f)
        cls.scan_file = os.path.join(cls.config, "uptick", "scan-repos.json")
        with open(cls.scan_file, "w", encoding="utf-8") as f:
            json.dump({"repos": ["/elsewhere"], "kept": True}, f)
        cls.proc = subprocess.Popen(COMMAND + ["inbox", "serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=dict(os.environ, SIGNOFF_INBOX_HOME=cls.home, XDG_CACHE_HOME=cls.cache,
                                             XDG_CONFIG_HOME=cls.config))

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
        for host in ("localhost:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, None):
            for path in (page, page + "run/0/", start):
                self.assertEqual(self.request("GET", path, headers={"Host": host})[0].status, 403, (host, path))
            self.save_refused(403, page, headers={"Host": host})

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
        for host in ("localhost:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, None):
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

        for host in ("localhost:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, None):
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
                     dict(good, text="x" * 1001)):
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

        for host in ("localhost:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, None):
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

    def scan_list(self):
        with open(self.scan_file, "rb") as f:
            return f.read()

    def test_scan_this_project_and_hand_a_finding_over_keep_todays_rules(self):
        start, page = self.open()
        scan, vuln = {"project": self.pid}, {"kind": "vuln", "project": self.pid, "ref": "lodash@4.17.20"}
        board = re.search(r'"board":\s*"(/[^"]+)"', self.request("GET", page + "run/0/")[1].decode("utf-8")).group(1)

        def refused(status, body, path, headers=None):
            before, asked = self.scan_list(), self.requests()
            resp, data = self.save(page, body, headers, path)
            self.assertEqual(resp.status, status, (body, headers, path, data))
            self.assertEqual((self.scan_list(), self.requests()), (before, asked))

        for body, path in ((scan, "scan"), (vuln, "request")):
            for host in ("localhost:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, None):
                refused(403, body, path, {"Host": host})
            for origin in ("http://evil.example", "null", "http://localhost:%d" % self.port, self.origin + "/", None):
                refused(403, body, path, {"Origin": origin})
            for site in ("cross-site", "same-site", "none"):
                refused(403, body, path, {"Sec-Fetch-Site": site})
            for ctype in ("text/plain", "application/x-www-form-urlencoded", None):
                refused(415, body, path, {"Content-Type": ctype})
            refused(411, json.dumps(body).encode(), path, {"Content-Length": None})
            refused(413, b"{}", path, {"Content-Length": "41943040"})
            for bad in (b"{x", b"[]", dict(body, extra=1)):
                refused(400, bad, path)
            refused(404, dict(body, project="f" * 16), path)
            for other in (path + "/", path + "?theme=dark", "run/0/" + path):
                refused(404, body, other)
            resp, _ = self.request("POST", "/%s/%s" % (board.split("/")[1], path), body, {
                "Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"})
            self.assertEqual(resp.status, 404)
            self.assertEqual(self.request("GET", page + path)[0].status, 404)
        refused(409, vuln, "request")  # not on Uptick's list yet: its old data offers nothing to hand over
        resp, data = self.save(page, scan, path="scan")
        self.assertEqual(resp.status, 200, data)
        self.assertEqual(json.loads(self.scan_list()), {"repos": ["/elsewhere", self.project], "kept": True})
        refused(409, dict(vuln, ref="lodash@4.17.21"), "request")
        resp, data = self.save(page, vuln, path="request")
        self.assertEqual(resp.status, 200, data)
        rid = json.loads(data)["id"]
        with open(os.path.join(self.home, "requests", self.pid, rid + ".json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((saved["kind"], saved["ref"]), ("vuln", "lodash@4.17.20"))
        self.assertEqual(self.request("GET", start)[0].status, 410)
        refused(404, scan, "scan")  # a closed pair writes nothing more

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

        for host in ("localhost:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, None):
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
class InboxOffer(unittest.TestCase):
    """An answer from a notification, over the control socket of an inbox in a separate process: offer, open with
    the offer (its start link lands on the item in Focus), accept once (the recommendation, with Done), and the
    refusals, which write nothing."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        base = os.path.realpath(cls._tmp.name)
        cls.home, cls.folder = os.path.join(base, "home"), os.path.join(base, "topic")
        os.makedirs(os.path.join(cls.home, "runs"), mode=0o700)
        os.makedirs(cls.folder)
        data = sample_decisions()
        data["items"] = [dict(data["items"][0], images=[])]  # one item, with a recommendation: the one a tap may answer
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

    def test_offer_open_accept_once(self):
        for bad in (None, 12, "0" * 32, "0" * 33, "Z" * 32, ["0" * 32]):
            self.assertEqual(self.ask({"op": "accept", "offer": bad}), {"error": "gone"}, bad)
        self.assertFalse(os.path.exists(self.answers))
        offer = self.ask({"op": "offer"})["offer"]
        self.assertEqual((offer["round"], offer["question"], offer["answer"]),
                         ("Pick the settings layout", "Two columns or one?", "Two columns"))
        self.assertRegex(offer["id"], r"^[0-9a-f]{32}$")
        for text in (self.home, self.folder, "round-01", "fedcba9876543210", "http"):
            self.assertNotIn(text, json.dumps(offer))
        resp = self.get(self.ask({"op": "open", "offer": offer["id"]})["link"])
        self.assertEqual(resp.status, 303)
        self.assertRegex(resp.getheader("Location"), r"^/[A-Za-z0-9_-]+/run/0/#focus=0/layout$")
        resp = self.get(self.ask({"op": "open", "offer": "f" * 32})["link"])
        self.assertRegex(resp.getheader("Location"), r"^/[A-Za-z0-9_-]+/$")
        self.assertEqual(self.ask({"op": "accept", "offer": offer["id"], "choice": "one"}), {"ok": True})
        with open(self.answers, "rb") as f:
            before = f.read()
        saved = json.loads(before.decode("utf-8"))
        self.assertEqual((saved["done"], saved["accepted"]), (True, True))
        self.assertEqual((saved["answers"]["layout"]["choice"], saved["answers"]["layout"]["source"]),
                         ("two", "notification"))
        self.assertEqual(self.ask({"op": "accept", "offer": offer["id"]}), {"error": "gone"})
        with open(self.answers, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(self.ask({"op": "offer"}), {"offer": None})
        self.assertIsNone(self.proc.poll())


@unittest.skipIf(sys.platform == "win32", "the inbox's control socket is a Unix socket; no inbox on Windows yet")
class InboxRows(unittest.TestCase):
    """The mod's panel list from an inbox in a separate process: `rows` on the control socket and `inbox rows
    <cwd>` on stdout, for a project's run the suite registers by hand. Numbers and one-line titles, nothing more."""

    TOKEN = re.compile(r"[A-Za-z0-9_-]{32,}")  # a start, page or board token, an offer id

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

    ask = InboxOffer.__dict__["ask"]  # the classmethod itself, so cls is this class and its home

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

    get = InboxOffer.get

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
        # Gone, out of range or another inbox's number: the list's link, as an unknown offer gets.
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


if __name__ == "__main__":
    unittest.main()
