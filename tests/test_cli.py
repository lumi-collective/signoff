"""The command line, run as a real process: exit codes, stdout, the .gitignore."""
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.error
from unittest import mock
import urllib.request
from pathlib import Path

from support import ROOT, Folder, Rounds, done_record, sample_decisions, signoff, write_outside

SCRIPT = str(ROOT / "plugin" / "signoff" / "signoff.py")


RUNNING = []  # every process a test starts; stopped after each test even when the test fails midway


def stop_all():
    while RUNNING:
        proc = RUNNING.pop()
        if proc.poll() is None:
            proc.kill()
        proc.communicate()


def start(folder, *args, env=None):
    return track(subprocess.Popen([sys.executable, "-I", "-B", SCRIPT, str(folder.target), "--no-open"] + list(args),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8",
                            env=env))


def track(proc):
    RUNNING.append(proc)
    return proc


def start_url_of(proc):
    line = proc.stderr.readline()
    start_ = line.find("http://")
    if start_ < 0:
        raise AssertionError("no URL on stderr: %r" % line)
    return line[start_:].split()[0]


def url_of(proc):
    """Open the single-use start link the way a browser does and return the page URL it redirects to."""
    with urllib.request.urlopen(start_url_of(proc), timeout=5) as resp:
        return resp.geturl()


def post(url, body, round_=0):
    origin = url.split("/", 3)
    req = urllib.request.Request(url + "save/%d" % round_, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json", "Origin": origin[0] + "//" + origin[2]})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status


class Cli(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()

    def tearDown(self):
        stop_all()
        self.folder.cleanup()

    def finish(self, proc):
        try:
            out, err = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        return out, err

    def test_done_exits_0_and_prints_the_answers_path(self):
        proc = start(self.folder)
        url = url_of(proc)
        self.assertTrue(url.startswith("http://127.0.0.1:"))
        self.assertEqual(post(url, {"answers": {}, "free_text": "", "done": False}), 200)
        self.assertEqual(post(url, {"answers": {"layout": {"choice": "one", "comment": ""}}, "free_text": "",
                                    "done": True}), 200)
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(out, str(self.folder.answers) + "\n")
        saved = json.loads(self.folder.answers.read_text(encoding="utf-8"))
        self.assertTrue(saved["done"])
        self.assertEqual(saved["answers"]["layout"]["choice"], "one")

    def test_timeout_exits_2_and_keeps_the_answers(self):
        proc = start(self.folder, "--timeout", "0.02")
        url = url_of(proc)
        post(url, {"answers": {"layout": {"choice": "one", "comment": "half"}}, "free_text": "", "done": False})
        began = time.monotonic()
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 2, err)
        self.assertLess(time.monotonic() - began, 15)
        self.assertIn("timed out", err)
        self.assertEqual(out, str(self.folder.answers) + "\n")
        self.assertFalse(json.loads(self.folder.answers.read_text(encoding="utf-8"))["done"])

    def test_bad_decisions_file_exits_1_with_a_message(self):
        data = sample_decisions()
        data["items"][0]["recommended"] = "three"
        self.folder.write(data)
        proc = start(self.folder)
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(out, "")
        self.assertIn("layout", err)
        self.assertIn("recommended", err)
        self.assertNotIn("Traceback", err)

    def test_missing_decisions_file(self):
        proc = subprocess.run([sys.executable, "-I", "-B", SCRIPT, str(self.folder.path / "nope.json"), "--no-open"],
                              capture_output=True, universal_newlines=True, encoding="utf-8", timeout=20)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("nope.json", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_windows_messages_are_utf8(self):
        # A non-English Windows gives stderr its code page (cp1250 in Hungarian) and a localized strerror; agents and
        # the tests read stderr as UTF-8, as they read stdout.
        out, err = (io.TextIOWrapper(io.BytesIO(), encoding="cp1250") for _ in range(2))
        missing = str(self.folder.path / "nő ű.json")
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err):
            try:
                signoff.main([missing, "--no-open"])
            except SystemExit:
                pass
            err.flush()
        self.assertIn("nő ű.json", err.buffer.getvalue().decode("utf-8"))

    def test_bad_timeout(self):
        proc = subprocess.run([sys.executable, "-I", "-B", SCRIPT, str(self.folder.decisions), "--no-open",
                               "--timeout", "-1"], capture_output=True, universal_newlines=True, timeout=20)
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(self.folder.answers.exists())

    def test_unknown_option_exits_1(self):
        proc = subprocess.run([sys.executable, "-I", "-B", SCRIPT, str(self.folder.decisions), "--no-opn"],
                              capture_output=True, universal_newlines=True, timeout=20)
        self.assertEqual(proc.returncode, 1)

    def test_timeout_without_any_save_prints_nothing(self):
        proc = start(self.folder, "--timeout", "0.005")
        url_of(proc)
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 2, err)
        self.assertEqual(out, "")
        self.assertFalse(self.folder.answers.exists())

    def test_demo_runs_end_to_end(self):
        demo = Folder()
        try:
            shutil.copytree(str(ROOT / "examples" / "demo" / "mockups"), str(demo.path / "demo-mockups"))
            shutil.copytree(str(ROOT / "examples" / "demo" / "notes"), str(demo.path / "notes"))
            demo.decisions.write_text((ROOT / "examples" / "demo" / "decisions.json").read_text(encoding="utf-8")
                                      .replace('"mockups/', '"demo-mockups/'), encoding="utf-8")
            proc = start(demo)
            url = url_of(proc)
            self.assertEqual(post(url, {"answers": {"theme": {"choice": "dark", "comment": ""}}, "free_text": "",
                                        "done": True}), 200)
            out, err = self.finish(proc)
            self.assertEqual(proc.returncode, 0, err)
            self.assertEqual(out, str(demo.answers) + "\n")
        finally:
            demo.cleanup()

    def test_gitignore_is_created_when_absent(self):
        proc = start(self.folder, "--timeout", "0.005")
        url_of(proc)
        self.finish(proc)
        self.assertEqual((self.folder.path / ".gitignore").read_text(encoding="utf-8"),
                         "*.answers.json\n*.answers.md\n*.json.lock\n")  # round locks, once an inbox has run

    def test_existing_gitignore_is_never_touched(self):
        mine = "# mine\nnode_modules/\n"
        (self.folder.path / ".gitignore").write_text(mine, encoding="utf-8")
        proc = start(self.folder, "--timeout", "0.005")
        url_of(proc)
        self.finish(proc)
        self.assertEqual((self.folder.path / ".gitignore").read_text(encoding="utf-8"), mine)

    def test_start_link_is_single_use_and_a_second_use_stops_the_run(self):
        proc = start(self.folder)
        link = start_url_of(proc)
        self.assertIn("/b/", link)
        with urllib.request.urlopen(link, timeout=5) as resp:
            page = resp.geturl()
        self.assertNotEqual(page, link)
        self.assertEqual(post(page, {"answers": {}, "free_text": "", "done": False}), 200)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(link, timeout=5)
        self.assertEqual(ctx.exception.code, 410)
        self.assertIn(b"ask your agent to open Signoff again", ctx.exception.read())  # what the user can do
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 1, err)
        self.assertEqual(out, "")
        self.assertIn("used twice", err)
        self.assertIn("run the same command again", err)  # what the agent does

    def test_finished_round_is_not_reopened(self):
        self.folder.answers.write_text(json.dumps({"version": 1, "done": True, "saved": "2026-01-01T12:00:00Z",
                                                   "answers": {}, "free_text": ""}), encoding="utf-8")
        before = self.folder.answers.read_text(encoding="utf-8")
        proc = start(self.folder)
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 1, err)
        self.assertIn("done", err)
        self.assertEqual(self.folder.answers.read_text(encoding="utf-8"), before)

    def test_help(self):
        proc = subprocess.run([sys.executable, "-I", "-B", SCRIPT, "--help"], capture_output=True,
                              universal_newlines=True, timeout=20)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--timeout", proc.stdout)



def post_status(url, body, round_=0):
    try:
        return post(url, body, round_)
    except urllib.error.HTTPError as e:
        return e.code


class CliOutsideWriter(unittest.TestCase):
    """Another program writes a round's answers file while the run is open (the run looks about once a second)."""

    def setUp(self):
        self.rounds = Rounds(2)

    def tearDown(self):
        stop_all()
        self.rounds.cleanup()

    def write(self, n, record):
        write_outside(self.rounds.answers(n), record)

    def still_running(self, proc):
        time.sleep(2.5)
        self.assertIsNone(proc.poll())

    def finish(self, proc):
        try:
            out, err = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        return out, err

    def test_an_outside_done_on_every_round_exits_0_with_the_paths(self):
        proc = start(self.rounds)
        start_url_of(proc)  # the server is up; the link stays unused, as when nobody opened the page
        began = time.monotonic()
        self.write(1, done_record())
        self.write(2, done_record({"layout": {"choice": "one", "comment": "from elsewhere"}}))
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 0, err)
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(out, "%s\n%s\n" % (self.rounds.answers(1), self.rounds.answers(2)))
        saved = json.loads(self.rounds.answers(2).read_text(encoding="utf-8"))
        self.assertEqual(saved["answers"]["layout"]["comment"], "from elsewhere")  # taken as written

    def test_an_outside_done_on_one_round_waits_for_the_other(self):
        proc = start(self.rounds)
        url = url_of(proc)
        self.write(1, done_record())
        self.still_running(proc)
        # Refused (409, round 1 not accepted) until the run has looked at the disk; a slow runner may need another try.
        deadline = time.monotonic() + 10
        while post_status(url, {"answers": {}, "free_text": "", "done": True}, 1) != 200:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.5)
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(out, "%s\n%s\n" % (self.rounds.answers(1), self.rounds.answers(2)))

    def test_an_outside_write_without_done_is_taken_in_and_a_stale_page_gets_409(self):
        proc = start(self.rounds)
        url = url_of(proc)
        self.write(1, dict(done_record(), done=False, free_text="written elsewhere"))
        self.still_running(proc)
        self.assertEqual(post_status(url, {"answers": {}, "free_text": "stale", "done": False, "rev": 0}), 409)
        self.assertEqual(json.loads(self.rounds.answers(1).read_text(encoding="utf-8"))["free_text"],
                         "written elsewhere")
        # Taken in without a page load: the run's save count moved on to 1, and the file is the one it knows.
        self.assertEqual(post_status(url, {"answers": {}, "free_text": "next", "done": False, "rev": 1}), 200)

    def test_an_invalid_outside_file_is_reported_and_the_run_goes_on(self):
        proc = start(self.rounds)
        start_url_of(proc)
        self.write(1, "{not json")
        self.still_running(proc)
        self.assertEqual(self.rounds.answers(1).read_text(encoding="utf-8"), "{not json")
        self.write(1, done_record())  # fixed: now it counts
        self.write(2, done_record())
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(err.count("round-01.answers.json"), 1, err)
        self.assertNotIn("Traceback", err)

    def test_its_own_saves_are_not_taken_for_outside_ones(self):
        proc = start(self.rounds)
        url = url_of(proc)
        empty = {"answers": {}, "free_text": "", "done": False}
        self.assertEqual(post_status(url, dict(empty, accepted=True, rev=0), 1), 200)
        self.still_running(proc)
        # Taken for an outside write, the run's own save would have moved the count on, and this would get 409.
        self.assertEqual(post_status(url, dict(empty, accepted=True, free_text="again", rev=1), 1), 200)
        self.assertEqual(post_status(url, dict(empty, done=True, rev=0), 0), 200)
        self.assertEqual(post_status(url, dict(empty, done=True, rev=2), 1), 200)
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 0, err)
        self.assertNotIn(".answers.json", err)


class CliInbox(unittest.TestCase):
    """With a Signoff home where an inbox has run (its inbox.lock is there): the run registry, the round locks, and
    waiter mode while the inbox holds its lock. The tests hold the inbox's lock themselves; there is no inbox yet."""

    def setUp(self):
        self.rounds = Rounds(2)
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        (self.home / "inbox.lock").write_bytes(b"")
        self.env = dict(os.environ, SIGNOFF_INBOX_HOME=str(self.home))
        self.held = []

    def tearDown(self):
        stop_all()
        while self.held:
            signoff.unlock(self.held.pop())
        self.rounds.cleanup()
        self._tmp.cleanup()

    def start(self, *args):
        return start(self.rounds, *args, env=self.env)

    def hold(self, path):
        fd = signoff.try_lock(path)
        self.assertIsNotNone(fd, path)
        self.held.append(fd)

    def entries(self):
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((self.home / "runs").glob("[!.]*.json"))]

    def entry_of(self, proc):
        return next(e for e in self.entries() if e["pid"] == proc.pid)

    def finish_outside(self):
        for n in (1, 2):
            write_outside(self.rounds.answers(n), done_record())

    def finish(self, proc):
        try:
            out, err = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        return out, err

    def paths(self):
        return "%s\n%s\n" % (self.rounds.answers(1), self.rounds.answers(2))

    def test_try_lock_is_exclusive_until_closed(self):
        path = self.home / "x.lock"
        fd = signoff.try_lock(path)
        self.assertIsNotNone(fd)
        self.assertIsNone(signoff.try_lock(path))
        signoff.unlock(fd)
        fd = signoff.try_lock(path)
        self.assertIsNotNone(fd)
        signoff.unlock(fd)
        with self.assertRaises(FileNotFoundError):
            signoff.try_lock(self.home / "none.lock", create=False)

    def plain_run(self, home):
        proc = start(self.rounds, env=dict(self.env, SIGNOFF_INBOX_HOME=str(home)))
        self.assertIn("http://", start_url_of(proc))
        self.finish_outside()
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(out, self.paths())
        self.assertEqual(list(home.iterdir()) if home.exists() else [], [])
        self.assertEqual(list(self.rounds.path.glob("*.lock")), [])

    def test_without_a_home_a_run_is_todays_and_writes_nothing_new(self):
        self.plain_run(self.home / "missing")
        self.assertFalse((self.home / "missing").exists())

    def test_a_home_folder_alone_turns_nothing_on(self):
        (self.home / "never-ran").mkdir()  # perhaps another tool's folder: no inbox.lock, no inbox has run
        self.plain_run(self.home / "never-ran")

    def test_an_unusable_inbox_lock_gives_the_page_run(self):
        self.home = self.home / "broken"
        (self.home / "inbox.lock").mkdir(parents=True)
        self.env["SIGNOFF_INBOX_HOME"] = str(self.home)
        proc = self.start()
        self.assertIn("inbox.lock", proc.stderr.readline())
        self.assertIn("http://", start_url_of(proc))
        self.finish_outside()
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(self.entry_of(proc)["mode"], "page")

    def test_with_the_home_a_run_registers_and_locks_its_rounds_until_it_exits(self):
        (self.rounds.path / ".git").mkdir()  # the topic folder is a project root
        proc = self.start()
        link = start_url_of(proc)  # a page run, as today
        [entry] = self.entries()
        self.assertEqual(entry["project"], hashlib.sha256(str(self.rounds.path).encode("utf-8")).hexdigest()[:16])
        self.assertNotIn(link.rstrip("/").rsplit("/", 1)[-1], (self.home / "runs" / (entry["run"] + ".json"))
                         .read_text(encoding="utf-8"))  # no token in the home
        self.assertEqual(entry["schema"], 1)
        self.assertEqual(entry["mode"], "page")
        self.assertEqual(entry["rounds"], ["round-01.json", "round-02.json"])
        self.assertEqual(entry["folder"], str(self.rounds.path))
        self.assertEqual(entry["pid"], proc.pid)
        self.assertRegex(entry["started"], signoff.SAVED_PATTERN)
        self.assertTrue((self.home / "runs" / (entry["run"] + ".json")).is_file())
        self.assertNotIn("ended", entry)
        for n in (1, 2):
            self.assertIsNone(signoff.try_lock(self.rounds.path / ("round-%02d.json.lock" % n)), "the run holds it")
        self.finish_outside()
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(out, self.paths())
        self.assertRegex(self.entry_of(proc)["ended"], signoff.SAVED_PATTERN)
        self.hold(self.rounds.path / "round-01.json.lock")  # let go when the run ended

    def test_a_second_run_on_a_held_round_exits_1(self):
        first = self.start()
        start_url_of(first)
        second = self.start()
        out, err = self.finish(second)
        self.assertEqual(second.returncode, 1, err)
        self.assertEqual(out, "")
        self.assertIn("round-01.json", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(len(self.entries()), 1)  # the refused run never registered
        self.assertIsNone(first.poll())

    def test_with_the_inbox_alive_the_run_is_a_waiter(self):
        self.hold(self.home / "inbox.lock")
        self.hold(self.rounds.path / "round-01.json.lock")  # the inbox writes round 1; the waiter takes no lock
        proc = self.start("--timeout", "0.005")  # a page run would stop idle within a second
        line = proc.stderr.readline()
        self.assertNotIn("http://", line)
        self.assertIn("inbox", line)
        [entry] = self.entries()
        self.assertEqual(entry["mode"], "waiter")
        self.assertEqual(entry["rounds"], ["round-01.json", "round-02.json"])
        time.sleep(2.5)
        self.assertIsNone(proc.poll(), "a waiter has no idle timeout")
        self.finish_outside()
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(out, self.paths())
        self.assertNotIn("http://", err)
        self.assertIn("ended", self.entry_of(proc))
        self.assertFalse((self.rounds.path / "round-02.json.lock").exists())

    def test_the_lock_never_meets_a_lock_file_of_another_tool(self):
        # A decisions file named yarn.json sits beside yarn.lock: the round's lock is yarn.json.lock.
        yarn = self.rounds.path / "yarn.json"
        yarn.write_bytes(self.rounds.round(1).read_bytes())
        (self.rounds.path / "yarn.lock").write_bytes(b"# yarn lockfile v1\n")
        proc = start(types.SimpleNamespace(target=yarn), env=self.env)
        start_url_of(proc)
        self.assertIsNone(signoff.try_lock(self.rounds.path / "yarn.json.lock"), "the run holds it")
        fd = signoff.try_lock(self.rounds.path / "yarn.lock", create=False)
        self.assertIsNotNone(fd, "the run must not lock yarn.lock")
        signoff.unlock(fd)  # let go now: on Windows a held lock would block the read below
        write_outside(self.rounds.path / "yarn.answers.json", done_record())
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual((self.rounds.path / "yarn.lock").read_bytes(), b"# yarn lockfile v1\n")
        self.assertEqual((self.rounds.path / ".gitignore").read_text(encoding="utf-8"),
                         "*.answers.json\n*.answers.md\n*.json.lock\n")

    def test_a_waiter_with_nothing_left_to_answer_exits_1(self):
        self.hold(self.home / "inbox.lock")
        self.rounds.finish(1)
        self.rounds.finish(2)
        proc = self.start()
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 1, err)
        self.assertEqual(out, "")
        self.assertIn("done", err)
        self.assertEqual(self.entries(), [])

    def test_a_reused_start_link_still_closes_the_entry(self):
        proc = self.start()
        link = start_url_of(proc)
        urllib.request.urlopen(link, timeout=5).close()
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(link, timeout=5)
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 1, err)
        self.assertIn("ended", self.entry_of(proc))

    @unittest.skipIf(sys.platform == "win32", "Ctrl+C is sent as SIGINT, which is POSIX only")
    def test_ctrl_c_closes_the_entry_in_both_modes(self):
        for waiter in (False, True):
            with self.subTest(waiter=waiter):
                if waiter:
                    self.hold(self.home / "inbox.lock")
                proc = self.start()
                proc.stderr.readline()
                time.sleep(0.5)  # into the serve or wait loop
                proc.send_signal(signal.SIGINT)
                out, err = self.finish(proc)
                self.assertEqual(proc.returncode, 130, err)
                self.assertEqual(out, "")
                self.assertIn("ended", self.entry_of(proc))


class CliRounds(unittest.TestCase):
    def setUp(self):
        self.rounds = Rounds(3)

    def tearDown(self):
        stop_all()
        self.rounds.cleanup()

    def finish(self, proc):
        try:
            out, err = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        return out, err

    def test_one_done_closes_every_editable_round_and_prints_their_paths(self):
        self.rounds.finish(1)
        proc = start(self.rounds)
        url = url_of(proc)
        empty = {"answers": {}, "free_text": "", "done": False}
        self.assertEqual(post(url, dict(empty, accepted=True), 1), 200)
        self.assertEqual(post(url, dict(empty, accepted=True), 2), 200)
        self.assertEqual(post(url, dict(empty, done=True), 1), 200)
        self.assertEqual(post(url, dict(empty, done=True), 2), 200)
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(out, "%s\n%s\n" % (self.rounds.answers(2), self.rounds.answers(3)))
        for n in (2, 3):
            self.assertTrue(json.loads(self.rounds.answers(n).read_text(encoding="utf-8"))["done"])

    def test_timeout_prints_only_rounds_with_answers(self):
        proc = start(self.rounds, "--timeout", "0.02")
        url = url_of(proc)
        post(url, {"answers": {}, "free_text": "half", "done": False}, 1)
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 2, err)
        self.assertEqual(out, "%s\n" % self.rounds.answers(2))

    def test_bad_round_stops_before_serving(self):
        data = sample_decisions()
        data["items"][1]["select"] = "several"
        self.rounds.write(2, data)
        proc = start(self.rounds)
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(out, "")
        for part in ("round-02.json", "filters", "select"):
            self.assertIn(part, err)

    def test_every_round_done_exits_1(self):
        for n in (1, 2, 3):
            self.rounds.finish(n)
        proc = start(self.rounds)
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("done", err)
        self.assertNotIn("Traceback", err)


class CliHistory(unittest.TestCase):
    def setUp(self):
        from test_extras import HistoryRoot
        self.root = HistoryRoot()

    def tearDown(self):
        stop_all()
        self.root.cleanup()

    def run_history(self, *args):
        return track(subprocess.Popen([sys.executable, "-I", "-B", SCRIPT, "--history", str(self.root.path),
                                       "--no-open"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      universal_newlines=True, encoding="utf-8"))

    def test_history_is_read_only_and_stops_when_idle(self):
        proc = self.run_history("--timeout", "0.02", "--since", "2026-01-01")
        url = url_of(proc)
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertIn(b'"mode": "history"', resp.read())
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post(url, {"answers": {}, "free_text": "", "done": True})
        self.assertEqual(ctx.exception.code, 405)
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 2, err)
        self.assertEqual(out, "")

    def test_history_folder(self):
        proc = self.run_history("--timeout", "0.02", "--folder", "docs/decisions")
        with urllib.request.urlopen(url_of(proc), timeout=5) as resp:
            page = resp.read()
        self.assertIn(b"2026-03-01-custom", page)
        self.assertNotIn(b"2026-01-05-settings", page)
        proc.communicate(timeout=30)

    def test_history_arguments(self):
        for args in (["--history", str(self.root.path), str(self.root.path)], ["--since", "2026-01-01", "x.json"],
                     ["--folder", "docs/decisions", "x.json"], ["--history", str(self.root.path), "--folder", "missing"],
                     ["--history", str(self.root.path), "--since", "January"], []):
            proc = subprocess.run([sys.executable, "-I", "-B", SCRIPT, "--no-open"] + args, capture_output=True,
                                  universal_newlines=True, timeout=20)
            self.assertEqual(proc.returncode, 1, args)
            self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()


class Launcher(unittest.TestCase):
    """plugin/bin/signoff, the sh launcher, finds signoff.py through a symlink to it too."""

    @unittest.skipIf(sys.platform == "win32", "the sh launcher is for macOS and Linux; Windows has signoff.cmd")
    def test_runs_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = os.path.join(tmp, "signoff")
            os.symlink(str(ROOT / "plugin" / "bin" / "signoff"), link)
            done = subprocess.run([link, "--help"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  universal_newlines=True, timeout=30)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("usage: signoff", done.stdout)

    @unittest.skipIf(sys.platform == "win32", "the sh launcher is tested on POSIX")
    def test_in_git_bash_it_runs_py(self):
        # python.org's Windows Python brings py, not python3, and the Store's python3 is a stub that fails.
        with tempfile.TemporaryDirectory() as tmp:
            got = Path(tmp) / "args"
            (Path(tmp) / "py").write_text('#!/bin/sh\necho "$@" > "%s"\n' % got)
            (Path(tmp) / "py").chmod(0o755)
            launcher = str(ROOT / "plugin" / "bin" / "signoff")
            for os_name, uses_py in ((None, False), ("Windows_NT", True)):
                env = {"PATH": tmp + os.pathsep + os.path.dirname(sys.executable) + os.pathsep + "/usr/bin:/bin"}
                if os_name:
                    env["OS"] = os_name
                done = subprocess.run(["/bin/sh", launcher, "--help"], capture_output=True, universal_newlines=True,
                                      env=env, timeout=30)
                self.assertEqual(got.exists(), uses_py, done.stderr)
                if uses_py:
                    args = got.read_text().split()
                    self.assertEqual((args[:2], args[-1]), (["-3", "-I"], "--help"))
                    self.assertTrue(args[2].endswith("signoff.py"), args)


class OldPython(unittest.TestCase):
    """On a Python older than 3.9 Signoff says so in one line, instead of failing somewhere later."""

    def guarded(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "version_info", (3, 8, 20, "final", 0)), mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err), mock.patch.object(signoff, "_lives", return_value=False):
            code = signoff.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_a_run_says_which_python_it_needs(self):
        for argv in (["somewhere"], ["--history", "."], ["inbox", "open"], ["inbox", "done", "0" * 16]):
            self.assertEqual(self.guarded(argv), (1, "", "signoff: needs Python 3.9 or newer; this is Python 3.8\n"),
                             argv)

    def test_the_hook_and_the_monitor_stay_silent(self):
        # A hook always exits 0 and prints nothing. A monitor that ends costs its session a model turn, so it sleeps
        # while Claude Code lives (here _lives says it is gone at once).
        for argv in (["inbox", "hook"], ["inbox", "monitor"]):
            self.assertEqual(self.guarded(argv), (0, "", ""), argv)

    def test_the_monitor_sleeps_while_its_session_lives(self):
        with mock.patch.object(sys, "version_info", (3, 8, 20, "final", 0)), \
                mock.patch.object(signoff, "_lives", side_effect=[True, False]), \
                mock.patch.object(signoff.time, "sleep") as sleep:
            self.assertEqual(signoff.main(["inbox", "monitor"]), 0)
        sleep.assert_called_once_with(60)

    def test_the_file_parses_as_python_3_7(self):
        # The guard speaks only if the old Python compiles the file first; 3.8 was also run by hand (NOTES).
        import ast
        ast.parse(Path(SCRIPT).read_text(encoding="utf-8"), feature_version=(3, 7))


class NoBrowser(unittest.TestCase):
    """When no browser opens (a headless Linux, WSL), stderr says so, so the agent hands the user the link."""

    def setUp(self):
        self.folder = Folder()

    def tearDown(self):
        self.folder.cleanup()

    def run_main(self, opened):
        err = io.StringIO()
        with mock.patch.object(signoff.webbrowser, "open", return_value=opened) as browser, \
                mock.patch.object(sys, "stderr", err):
            code = signoff.main([str(self.folder.target), "--timeout", "0.002"])
        browser.assert_called_once()
        return code, err.getvalue()

    def test_a_run_says_no_browser_opened(self):
        code, err = self.run_main(False)
        self.assertEqual(code, 2)
        self.assertIn("signoff: no browser opened; give the user the link above (it works once)", err)
        code, err = self.run_main(True)
        self.assertNotIn("no browser", err)

    @unittest.skipIf(sys.platform == "win32", "no inbox on Windows")
    def test_inbox_open_prints_the_link_when_no_browser_opened(self):
        link = "http://127.0.0.1:1/b/%s/" % ("a" * 43)
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"SIGNOFF_INBOX_HOME": tmp}), \
                mock.patch.object(signoff, "inbox_alive", return_value=True), \
                mock.patch.object(signoff, "inbox_request", return_value={"link": link}), \
                mock.patch.object(signoff.webbrowser, "open", return_value=False), mock.patch.object(sys, "stderr", err):
            self.assertEqual(signoff.main(["inbox", "open"]), 0)
        self.assertIn("no browser opened; open %s yourself (it works once)" % link, err.getvalue())
        self.assertNotIn("is open in your browser", err.getvalue())


@unittest.skipIf(sys.platform == "win32", "no inbox, and no pty to stand in for a terminal")
class BrowserFds(unittest.TestCase):
    """A browser webbrowser.open starts (xdg-open on Linux) inherits fds 1 and 2: on a pipe it would hold the caller's
    pipe open (the mod waits for its end) or write on a run's stdout. Each that is not a terminal points at /dev/null
    for the call, and is back after it; a terminal stays, for a console browser."""

    def setUp(self):
        self.folder = Folder()

    def tearDown(self):
        self.folder.cleanup()

    def opened(self, out, err, call):
        seen = []

        def browser(url):
            seen.append([os.path.samestat(os.fstat(fd), os.stat(os.devnull)) for fd in (1, 2)])
            return True

        saved = [os.dup(1), os.dup(2)]
        try:
            os.dup2(out, 1)
            os.dup2(err, 2)
            before = [os.fstat(1), os.fstat(2)]
            with mock.patch.object(signoff.webbrowser, "open", side_effect=browser), \
                    mock.patch.object(sys, "stdout", io.StringIO()), mock.patch.object(sys, "stderr", io.StringIO()):
                code = call()
            after = [os.fstat(1), os.fstat(2)]
        finally:
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            for fd in saved:
                os.close(fd)
        self.assertTrue(all(os.path.samestat(a, b) for a, b in zip(before, after)))
        return code, seen

    def pipe(self):
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        self.addCleanup(os.close, w)
        return w

    def test_a_run_and_inbox_open_give_the_browser_no_pipe(self):
        def run():
            return signoff.main([str(self.folder.target), "--timeout", "0.002"])

        self.assertEqual(self.opened(self.pipe(), self.pipe(), run), (2, [[True, True]]))
        link = "http://127.0.0.1:1/b/%s/" % ("a" * 43)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"SIGNOFF_INBOX_HOME": tmp}), \
                mock.patch.object(signoff, "inbox_alive", return_value=True), \
                mock.patch.object(signoff, "inbox_request", return_value={"link": link}):
            self.assertEqual(self.opened(self.pipe(), self.pipe(), lambda: signoff.main(["inbox", "open"])),
                             (0, [[True, True]]))
            terminal, other = os.openpty()
            self.addCleanup(os.close, terminal)
            self.addCleanup(os.close, other)
            self.assertEqual(self.opened(other, self.pipe(), lambda: signoff.main(["inbox", "open"])),
                             (0, [[False, True]]))
