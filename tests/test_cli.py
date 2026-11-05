"""The command line, run as a real process: exit codes, stdout, the .gitignore."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from support import ROOT, Folder, Rounds, sample_decisions

SCRIPT = str(ROOT / "plugin" / "signoff" / "signoff.py")


RUNNING = []  # every process a test starts; stopped after each test even when the test fails midway


def stop_all():
    while RUNNING:
        proc = RUNNING.pop()
        if proc.poll() is None:
            proc.kill()
        proc.communicate()


def start(folder, *args):
    return track(subprocess.Popen([sys.executable, "-I", "-B", SCRIPT, str(folder.target), "--no-open"] + list(args),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8"))


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
        self.assertEqual((self.folder.path / ".gitignore").read_text(encoding="utf-8"), "*.answers.json\n*.answers.md\n")

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
        out, err = self.finish(proc)
        self.assertEqual(proc.returncode, 1, err)
        self.assertEqual(out, "")
        self.assertIn("used twice", err)

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
