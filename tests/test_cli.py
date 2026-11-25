"""The command line, run as a real process: exit codes, stdout, the .gitignore."""
import argparse
import errno
import hashlib
import io
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
from unittest import mock
import urllib.request
from pathlib import Path

from support import ROOT, Folder, Rounds, done_record, free_port, png_bytes, sample_decisions, signoff, write_outside

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


DONE = {"answers": {"layout": {"choice": "one", "comment": "a secret reason"}}, "free_text": "", "done": True}


@unittest.skipIf(sys.platform == "win32", "the notes folder is POSIX only (its checks need owners and modes)")
class CliNotes(unittest.TestCase):
    """--notes: at Done, each round's Markdown copy goes to the user's notes folder, never over a file there."""

    def setUp(self):
        self.folder = Folder()
        self._tmp = tempfile.TemporaryDirectory()
        self.notes = Path(self._tmp.name) / "vault"  # under /var on macOS, a link: only the folder itself counts
        self.notes.mkdir(mode=0o700)
        self.name = "%s - decisions.md" % self.folder.path.name  # outside a project: topic folder and round stem

    def tearDown(self):
        stop_all()
        self.folder.cleanup()
        self._tmp.cleanup()

    def run_done(self, notes, body=DONE, env=None):
        proc = start(self.folder, "--notes", str(notes), env=env)
        self.assertEqual(post(url_of(proc), body), 200)
        out, err = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0, err)  # a note never changes the exit code
        self.assertEqual(out, str(self.folder.answers) + "\n")  # nor stdout
        return err

    def write_notes(self, notes, rounds=None, home=None):
        """write_notes at a run's Done, in process (each run waits DONE_GRACE). Returns its stderr."""
        if rounds is None:
            if not self.folder.answers.exists():
                write_outside(self.folder.answers, done_record())
            rounds = [signoff.Round(self.folder.decisions)]
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            signoff.write_notes(str(notes), rounds, home or signoff.inbox_home())
        return err.getvalue()

    def project_rounds(self, *topics):
        """Done rounds in a project: <tmp>/proj (a git work tree), its decisions under decisions/<topic>/."""
        found = []
        for topic in topics:
            folder = Path(self._tmp.name) / "proj" / "decisions" / topic
            (folder / "mockups").mkdir(parents=True)
            (folder / "mockups" / "a.png").write_bytes(png_bytes())
            (folder / "round-01.json").write_text(json.dumps(sample_decisions()), encoding="utf-8")
            write_outside(folder / "round-01.answers.json", done_record())
            found.append(signoff.Round(folder / "round-01.json"))
        (Path(self._tmp.name) / "proj" / ".git").mkdir(exist_ok=True)
        return found

    def refused(self, notes, why, home=None, rounds=None):
        """One stderr line, nothing written."""
        before = sorted(p.name for p in self.notes.iterdir())
        lines = self.write_notes(notes, rounds, home).splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("signoff: notes: ", lines[0])
        self.assertIn(why, lines[0])
        self.assertEqual(sorted(p.name for p in self.notes.iterdir()), before)

    def test_a_refused_folder_changes_nothing_of_the_run(self):
        err = self.run_done(self.notes / "missing")
        self.assertEqual([line for line in err.splitlines() if "notes:" in line],
                         ["signoff: notes: %s is missing; no note written" % (self.notes / "missing")])
        self.assertEqual(list(self.notes.iterdir()), [])

    def test_a_done_run_writes_one_note(self):
        err = self.run_done(self.notes)
        self.assertNotIn("notes:", err)
        [note] = self.notes.iterdir()
        self.assertEqual(note.name, self.name)
        self.assertEqual(stat.S_IMODE(note.stat().st_mode), 0o600)
        beside = self.folder.path / "decisions.answers.md"
        self.assertEqual(note.read_text(encoding="utf-8"), beside.read_text(encoding="utf-8"))  # nothing private

    def test_a_private_answer_shows_only_private_there(self):
        body = {"answers": {"layout": {"choice": "one", "comment": "a secret reason", "private": True}},
                "free_text": "", "done": True}
        self.run_done(self.notes, body)
        note = (self.notes / self.name).read_text(encoding="utf-8")
        item = note.split("Two columns or one?")[1].split("###")[0]
        self.assertEqual(item.split(), ["(private)"])
        self.assertIn("Which filters?", note)  # the other items as usual
        beside = (self.folder.path / "decisions.answers.md").read_text(encoding="utf-8")
        self.assertIn("Keep one", beside)  # the copy beside the round as before
        self.assertNotIn("secret", beside)

    def test_a_private_line_note_shows_only_private_there(self):
        (self.folder.path / "change.diff").write_text("--- a/x\n+++ b/x\n", encoding="utf-8")
        data = sample_decisions()
        data["items"][0].update(attachments=[{"src": "change.diff"}], lines=True, sensitive=True, comment=False)
        self.folder.write(data)
        line_note = {"att": 0, "line": 2, "quote": "+++ b/x", "comment": "a secret reason"}
        body = {"answers": {"layout": {"choice": "one", "comment": "", "lines": [line_note], "private": True}},
                "free_text": "", "done": True}
        self.run_done(self.notes, body)
        note = (self.notes / self.name).read_text(encoding="utf-8")
        item = note.split("Two columns or one?")[1].split("###")[0]
        self.assertEqual(item.split(), ["(private)"])
        self.assertNotIn("+++", note)
        beside = (self.folder.path / "decisions.answers.md").read_text(encoding="utf-8")
        self.assertIn("- Line note, change.diff line 2, “+++ b/x”: (private)\n", beside)
        self.assertNotIn("secret", beside)

    def test_a_line_note_goes_there_without_the_line_it_quotes(self):
        # The noted line is the agent's file text, which a synced notes folder must not get: name, number, comment.
        (self.folder.path / "change.diff").write_text("--- a/x\n+API_KEY=abc\n", encoding="utf-8")
        data = sample_decisions()
        data["items"][0].update(attachments=[{"src": "change.diff"}], lines=True)
        self.folder.write(data)
        line_note = {"att": 0, "line": 2, "quote": "+API_KEY=abc", "comment": "not in git"}
        body = {"answers": {"layout": {"choice": "one", "comment": "", "lines": [line_note]}}, "free_text": "",
                "done": True}
        self.run_done(self.notes, body)
        note = (self.notes / self.name).read_text(encoding="utf-8")
        self.assertIn("- Line note, change.diff line 2: not in git\n", note)
        self.assertNotIn("API", note)
        beside = (self.folder.path / "decisions.answers.md").read_text(encoding="utf-8")
        self.assertIn("- Line note, change.diff line 2, “+API\\_KEY=abc”: not in git\n", beside)

    def test_an_empty_setting_writes_nothing(self):
        err = self.run_done("")
        self.assertNotIn("notes:", err)
        self.assertEqual(list(self.notes.iterdir()), [])

    def test_a_setting_placeholder_left_as_typed_writes_nothing(self):
        # An unsaved option reaches a command as its placeholder: off, as an empty value is, with no line.
        err = self.run_done("${user_config.notes}")
        self.assertNotIn("notes:", err)
        self.assertEqual(list(self.notes.iterdir()), [])

    def test_every_round_of_the_run_gets_its_note(self):
        rounds = Rounds(2)
        self.addCleanup(rounds.cleanup)
        proc = start(rounds, "--notes", str(self.notes))
        url = url_of(proc)
        empty = {"answers": {}, "free_text": "", "done": False}
        for n in (0, 1):
            self.assertEqual(post(url, dict(empty, accepted=True), n), 200)
        for n in (0, 1):
            self.assertEqual(post(url, dict(empty, done=True), n), 200)
        out, err = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0, err)
        self.assertNotIn("notes:", err)
        self.assertEqual(sorted(p.name for p in self.notes.iterdir()),
                         ["%s - round-%02d.md" % (rounds.path.name, n) for n in (1, 2)])

    def test_one_bad_answers_file_costs_only_its_own_note(self):
        rounds = Rounds(2)
        self.addCleanup(rounds.cleanup)
        record = done_record()
        del record["free_text"]  # check_answers lets it go; the copy takes it as empty
        write_outside(rounds.answers(1), record)
        write_outside(rounds.answers(2), done_record())
        err = self.write_notes(self.notes, [signoff.Round(rounds.round(n)) for n in (1, 2)])
        self.assertEqual(err, "")
        self.assertEqual(sorted(p.name for p in self.notes.iterdir()),
                         ["%s - round-%02d.md" % (rounds.path.name, n) for n in (1, 2)])
        broken = [signoff.Round(rounds.round(n)) for n in (1, 2)]
        write_outside(rounds.answers(1), "not json")  # after the run took it in
        other = self.notes / "other"
        other.mkdir(mode=0o700)
        lines = self.write_notes(other, broken).splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("round-01", lines[0])
        self.assertEqual([p.name for p in other.iterdir()], ["%s - round-02.md" % rounds.path.name])

    def test_a_relative_path_is_refused(self):
        self.refused("vault", "absolute")

    def test_a_missing_folder_is_refused(self):
        self.refused(self.notes / "missing", "missing")

    def test_a_linked_folder_is_refused(self):
        link = Path(self._tmp.name) / "link"
        link.symlink_to(self.notes)
        self.refused(link, "link")
        for tail in ("/", "/.", "/./", "//./"):  # each makes the system follow it
            self.refused(str(link) + tail, "link")

    def test_a_folder_with_a_dot_part_is_refused(self):
        # Tool and config folders (.claude, .config, .git) are where an injected agent would aim a note.
        hidden = Path(self._tmp.name) / ".config" / "rules"
        hidden.mkdir(parents=True, mode=0o700)
        self.refused(hidden, "starts with a dot")
        alias = Path(self._tmp.name) / "plain"
        alias.symlink_to(Path(self._tmp.name) / ".config")  # a link above the folder, to a dot folder
        self.refused(alias / "rules", "starts with a dot")

    def test_a_folder_inside_the_project_of_the_rounds_is_refused(self):
        rounds = self.project_rounds("2026-11-14-a", "2026-11-14-b")
        proj = Path(self._tmp.name) / "proj"
        for inside in (proj, proj / "docs", proj / "decisions", proj / "decisions" / "2026-11-14-b"):
            inside.mkdir(mode=0o700, exist_ok=True)
            self.refused(inside, "inside", rounds=rounds[:1])  # a sibling topic too: git add -A would take it
            self.assertFalse(any(p.suffix == ".md" for p in inside.iterdir()), inside)

    def test_a_folder_others_can_write_is_refused(self):
        self.notes.chmod(0o777)
        self.refused(self.notes, "other users")
        self.notes.chmod(0o770)
        self.refused(self.notes, "other users")

    @unittest.skipIf(hasattr(os, "getuid") and os.getuid() == 0, "root owns the folder used here")
    def test_another_users_folder_is_refused(self):
        self.refused("/", "not your own")

    def test_a_folder_inside_the_decisions_folder_or_the_home_is_refused(self):
        inside = self.folder.path / "notes"
        inside.mkdir(mode=0o700)
        self.refused(inside, "inside")
        self.refused(self.folder.path, "inside")
        if os.path.isdir(str(inside).upper()):  # a file system that ignores case (macOS): another spelling, same folder
            self.refused(str(inside).upper(), "inside")
        self.assertEqual(list(inside.iterdir()), [])
        self.refused(self.notes, "inside", home=Path(self._tmp.name))

    def test_a_note_already_there_is_never_replaced(self):
        mine = self.notes / self.name
        mine.write_bytes(b"my own note\n")
        mine.chmod(0o644)
        self.refused(self.notes, "already there; not replaced")
        self.assertEqual(mine.read_bytes(), b"my own note\n")
        self.assertEqual(stat.S_IMODE(mine.stat().st_mode), 0o644)

    def test_a_link_at_the_name_is_never_followed(self):
        target = Path(self._tmp.name) / "elsewhere.md"
        (self.notes / self.name).symlink_to(target)
        self.refused(self.notes, "already there")
        self.assertFalse(os.path.lexists(str(target)))

    def test_a_run_that_does_not_end_with_done_writes_no_note(self):
        proc = start(self.folder, "--notes", str(self.notes), "--timeout", "0.02")
        post(url_of(proc), dict(DONE, done=False))
        out, err = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 2, err)
        proc = start(self.folder, "--notes", str(self.notes))
        post(url_of(proc), dict(DONE, done=False))
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 130, err)
        self.assertEqual(list(self.notes.iterdir()), [])

    def test_a_later_folder_run_writes_only_its_own_rounds(self):
        rounds = Rounds(1)
        self.addCleanup(rounds.cleanup)
        empty = {"answers": {}, "free_text": "", "done": True}
        for n in (1, 2):
            if n == 2:
                rounds.write(2, sample_decisions())
            proc = start(rounds, "--notes", str(self.notes))
            self.assertEqual(post(url_of(proc), empty, n - 1), 200)  # round 1 shows read only in the second run
            out, err = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 0, err)
            self.assertEqual(out, "%s\n" % rounds.answers(n))
            self.assertNotIn("notes:", err)
        self.assertEqual(sorted(p.name for p in self.notes.iterdir()),
                         ["%s - round-%02d.md" % (rounds.path.name, n) for n in (1, 2)])

    def test_the_name_is_project_topic_and_round_in_plain_characters(self):
        project = Path(self._tmp.name) / "my project"
        topic = project / ".signoff" / "2026-11-14-ünnep*?"
        topic.mkdir(parents=True)
        (project / ".git").mkdir()
        self.assertEqual(signoff.note_name(topic / "round-03.json"), "my project - 2026-11-14-_nnep__ - round-03.md")
        self.assertEqual(signoff.note_name(Path(self._tmp.name) / ".hidden" / "round-01.json"),
                         "_hidden - round-01.md")
        # The cut takes the topic and project, never the round: two rounds of a long topic keep two names.
        names = [signoff.note_name(Path(self._tmp.name) / ("t" * 250) / ("round-%02d.json" % n)) for n in (1, 2)]
        self.assertEqual(names, ["t" * 189 + " - round-%02d.md" % n for n in (1, 2)])  # 200 bytes, then .md
        self.assertEqual(signoff.note_name(Path(self._tmp.name) / ("ő" * 120) / ("x" * 250 + ".json")),
                         "x" * 200 + ".md")  # a stem that alone is too long is cut too

    def test_on_windows_it_says_so_and_writes_nothing(self):
        rounds = [signoff.Round(self.folder.decisions)]
        err = io.StringIO()
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(sys, "stderr", err):
            signoff.write_notes(str(self.notes), rounds, Path(self._tmp.name) / "home")
        self.assertIn("not supported on Windows yet", err.getvalue())
        self.assertEqual(list(self.notes.iterdir()), [])



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

    def test_a_taken_port_exits_1_in_one_line_and_leaves_no_open_entry(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            port = taken.getsockname()[1]
            for args, env in ((["--port", str(port)], {}), ([], {"SIGNOFF_PORT": str(port)})):
                proc = subprocess.run([sys.executable, "-I", "-B", SCRIPT, str(self.rounds.target), "--no-open"] + args,
                                      capture_output=True, universal_newlines=True, encoding="utf-8",
                                      env=dict(self.env, **env), timeout=20)
                self.assertEqual((proc.returncode, proc.stdout), (1, ""), proc.stderr)
                self.assertIn("port %d" % port, proc.stderr)
                self.assertEqual(len(proc.stderr.strip().splitlines()), 1, proc.stderr)
        self.assertEqual([e for e in self.entries() if "ended" not in e], [])

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
        self.assertEqual(self.entry_of(proc)["exit"], 0)  # it handed the answers over
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
        self.assertEqual(self.entry_of(proc)["exit"], 0)
        self.assertIn("ended", self.entry_of(proc))
        self.assertFalse((self.rounds.path / "round-02.json.lock").exists())

    def looking(self, every=0.3, lock_after=None):
        """An inbox's looks: inbox.lock's time changes every `every` seconds, as a live inbox touches it at the start
        of each. With lock_after, a slow current inbox: a look that met an entry in runs/ at its touch locks the
        open rounds that many seconds later, as the inbox reads runs/ after the touch and locks what it takes in."""
        stop = threading.Event()

        def touch():
            while not stop.wait(every):
                os.utime(str(self.home / "inbox.lock"))
                runs = self.home / "runs"
                met = runs.is_dir() and any(runs.glob("[!.]*.json"))
                if lock_after is not None and met and not locked and not stop.wait(lock_after):
                    # Held until the test ends, as the inbox holds them while the run lives.
                    locked.extend(signoff.try_lock(self.rounds.path / ("round-%02d.json.lock" % n)) for n in (1, 2))
        locked = []
        t = threading.Thread(target=touch, daemon=True)
        t.start()
        self.addCleanup(lambda: [signoff.unlock(fd) for fd in locked if fd is not None])
        self.addCleanup(t.join)
        self.addCleanup(stop.set)  # cleanups run last first: stop, join, then let go of the locks

    def refused(self, proc, began):
        out, err = self.finish(proc)
        self.assertEqual((proc.returncode, out), (1, ""), err)
        self.assertLess(time.monotonic() - began, 15)
        self.assertIn("did not take", err)
        self.assertIn("signoff inbox stop", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.entry_of(proc)["exit"], 1)
        self.assertFalse(self.rounds.answers(1).exists())

    @unittest.skipIf(sys.platform == "win32", "the inbox does not run on Windows")
    def test_a_waiter_whose_rounds_a_looking_inbox_never_locks_exits_1(self):
        # An inbox from before a key the rounds use refuses them, says so in its own log only and locks nothing.
        self.hold(self.home / "inbox.lock")
        self.looking()
        began = time.monotonic()
        self.refused(self.start(), began)
        self.assertEqual(list(self.rounds.path.glob("*.lock")), [])  # it made no lock file of its own

    @unittest.skipIf(sys.platform == "win32", "the inbox does not run on Windows")
    def test_a_waiter_keeps_waiting_while_a_round_lock_is_held(self):
        # The inbox took the rounds in (it holds every lock), or another run holds one and it shows them read only.
        self.hold(self.home / "inbox.lock")
        self.looking()
        for held in ((2,), (1, 2)):
            with self.subTest(held=held):
                fds = [signoff.try_lock(self.rounds.path / ("round-%02d.json.lock" % n)) for n in held]
                proc = self.start()
                self.assertIn("inbox", proc.stderr.readline())
                time.sleep(3.5)  # many looks: it judged after two
                self.assertIsNone(proc.poll(), "a waiter whose rounds are held keeps waiting")
                if held == (1, 2):
                    self.finish_outside()
                    out, err = self.finish(proc)
                    self.assertEqual((proc.returncode, out), (0, self.paths()), err)
                else:
                    proc.kill()
                    proc.communicate()
                for fd in fds:
                    signoff.unlock(fd)

    def test_a_waiter_on_an_inbox_that_does_not_look_keeps_waiting(self):
        # No look, no verdict: an inbox that holds its lock but never touches it (stuck, or these tests' stand-in).
        self.hold(self.home / "inbox.lock")
        proc = self.start()
        self.assertIn("inbox", proc.stderr.readline())
        time.sleep(3.5)
        self.assertIsNone(proc.poll())

    @unittest.skipIf(sys.platform == "win32", "the inbox does not run on Windows")
    def test_a_waiter_on_a_slow_current_inbox_keeps_waiting(self):
        # Its look locks the rounds 1.5 s after its touch: judged after one touch, they would seem refused.
        self.hold(self.home / "inbox.lock")
        self.looking(every=2.0, lock_after=1.5)
        proc = self.start()
        self.assertIn("inbox", proc.stderr.readline())
        time.sleep(9)  # the second touch comes 3.5 s after the first, the verdict within a second of it
        self.assertIsNone(proc.poll(), "a waiter on a slow current inbox keeps waiting")
        self.finish_outside()
        out, err = self.finish(proc)
        self.assertEqual((proc.returncode, out), (0, self.paths()), err)

    def test_a_waiter_checks_its_pictures_and_boards_as_the_inbox_does(self):
        # The inbox refuses a round whose board is not UTF-8 at its look: the waiter says why, up front, as a run does.
        self.hold(self.home / "inbox.lock")
        (self.rounds.path / "mockups" / "bad.html").write_bytes(b"<p>\xff\xfe</p>")
        data = json.loads(self.rounds.round(1).read_text(encoding="utf-8"))
        data["items"][0]["images"].append({"html": "mockups/bad.html"})
        self.rounds.write(1, data)
        out, err = self.finish(self.start())
        self.assertEqual(out, "")
        self.assertIn("UTF-8", err)
        self.assertNotIn("restart", err.lower())
        self.assertEqual(self.entries(), [])  # it never noted itself, as a run that fails to load

    @unittest.skipIf(sys.platform == "win32" or (hasattr(os, "getuid") and os.getuid() == 0),
                     "the inbox does not run on Windows; root writes into a 0500 folder")
    def test_a_waiter_whose_entry_could_not_be_written_keeps_waiting(self):
        # The inbox cannot see such a run, so its locks say nothing about the inbox's age: no verdict.
        self.hold(self.home / "inbox.lock")
        self.looking()
        (self.home / "runs").mkdir(mode=0o500)
        try:
            proc = self.start()
            self.assertIn("will not see this run", proc.stderr.readline())
            time.sleep(3.5)
            self.assertIsNone(proc.poll(), "no verdict for a run the inbox cannot see")
        finally:
            os.chmod(str(self.home / "runs"), 0o700)  # before tearDown removes the home

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
        self.assertEqual(self.entry_of(proc)["exit"], 1)

    @unittest.skipIf(sys.platform == "win32", "Ctrl+C is sent as SIGINT, which is POSIX only")
    def test_ctrl_c_sigterm_and_sighup_close_the_entry_in_both_modes(self):
        # A hangup may be followed by a SIGTERM: the second must not cut the first one's cleanup short.
        for waiter in (False, True):
            if waiter:
                self.hold(self.home / "inbox.lock")
            for sigs in ((signal.SIGINT,), (signal.SIGTERM,), (signal.SIGHUP,), (signal.SIGHUP, signal.SIGTERM)):
                with self.subTest(waiter=waiter, sigs=sigs):
                    proc = self.start()
                    proc.stderr.readline()
                    time.sleep(0.5)  # into the serve or wait loop
                    for s in sigs:
                        proc.send_signal(s)
                    out, err = self.finish(proc)
                    self.assertEqual(proc.returncode, 130, err)
                    self.assertEqual(out, "")
                    self.assertIn("stopped", err)
                    self.assertNotIn("Traceback", err)
                    self.assertIn("ended", self.entry_of(proc))
                    self.assertEqual(self.entry_of(proc)["exit"], 130)  # stopped: nothing handed over

    @unittest.skipIf(sys.platform == "win32", "SIGHUP is POSIX only")
    def test_a_run_started_with_sighup_ignored_keeps_ignoring_it(self):
        # nohup signoff ...: a hangup did not stop it before, and still does not.
        proc = track(subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", "import signal, sys, runpy; signal.signal(signal.SIGHUP, "
             "signal.SIG_IGN); sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')",
             SCRIPT, str(self.rounds.target), "--no-open"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, encoding="utf-8", env=self.env))
        proc.stderr.readline()
        time.sleep(0.5)
        proc.send_signal(signal.SIGHUP)
        time.sleep(0.5)
        self.assertIsNone(proc.poll())
        proc.send_signal(signal.SIGTERM)
        out, err = self.finish(proc)
        self.assertEqual((proc.returncode, out), (130, ""), err)
        self.assertIn("ended", self.entry_of(proc))

    @unittest.skipIf(sys.platform == "win32", "os.kill with SIGTERM ends the process there")
    def test_a_signal_during_the_cleanup_of_a_run_that_ended_by_itself_changes_nothing(self):
        real = signoff.register_run

        def register(*args):
            end, noted = real(*args)

            def ending(*code):
                os.kill(os.getpid(), signal.SIGTERM)  # a SIGTERM just as the run ends by itself
                end(*code)
            return ending, noted

        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)  # not one an earlier test left behind
        with mock.patch.dict(os.environ, {"SIGNOFF_INBOX_HOME": str(self.home)}), \
                mock.patch.object(signoff, "register_run", register), \
                mock.patch.object(sys, "stdout", io.StringIO()), mock.patch.object(sys, "stderr", io.StringIO()):
            code = signoff.main([str(self.rounds.target), "--no-open", "--timeout", "0.002"])
        self.assertEqual(code, signoff.EXIT_TIMEOUT)
        self.assertIn("ended", self.entries()[0])
        self.assertEqual(self.entries()[0]["exit"], signoff.EXIT_TIMEOUT)  # the code it ended with, not 130
        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)

    def test_an_unexpected_error_ends_the_entry_without_an_exit_code(self):
        # As before the key came: the inbox then takes the run as one that handed over, and writes no request.
        def boom(*args):
            raise RuntimeError("boom")

        with mock.patch.dict(os.environ, {"SIGNOFF_INBOX_HOME": str(self.home)}), \
                mock.patch.object(signoff, "_serve", boom), \
                mock.patch.object(sys, "stdout", io.StringIO()), mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(RuntimeError):
                signoff.main([str(self.rounds.target), "--no-open"])
        [entry] = self.entries()
        self.assertIn("ended", entry)
        self.assertNotIn("exit", entry)

    @unittest.skipIf(sys.platform == "win32", "the inbox, which reads the entry, does not run on Windows")
    def test_a_waiter_whose_paths_cannot_reach_stdout_never_says_it_handed_over(self):
        # nohup'd, its session gone: nobody reads the paths, so its entry must not say exit 0 (the inbox then tells).
        self.hold(self.home / "inbox.lock")
        proc = self.start()
        self.assertIn("inbox", proc.stderr.readline())
        proc.stdout.close()
        self.finish_outside()
        proc.wait(timeout=20)
        err = proc.stderr.read()
        self.assertNotEqual(proc.returncode, 0, err)
        entry = self.entry_of(proc)
        self.assertNotEqual(entry.get("exit"), 0, entry)
        self.assertNotIn("ended", entry)  # as a killed run: its pid is gone, so the inbox takes it as stopped

    @unittest.skipIf(sys.platform == "win32", "os.kill with SIGTERM ends the process there")
    def test_a_signal_before_the_serve_loop_still_stops_the_run(self):
        def browser(url):
            os.kill(os.getpid(), signal.SIGTERM)  # while the browser starts, before serve_forever
            return True

        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)  # not one an earlier test left behind
        with mock.patch.dict(os.environ, {"SIGNOFF_INBOX_HOME": str(self.home)}), \
                mock.patch.object(signoff, "_open_browser", browser), \
                mock.patch.object(sys, "stdout", io.StringIO()) as out, mock.patch.object(sys, "stderr", io.StringIO()):
            code = signoff.main([str(self.rounds.target)])
        self.assertEqual((code, out.getvalue()), (130, ""))
        self.assertIn("ended", self.entries()[0])
        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)

    def test_main_off_the_main_thread_still_runs(self):
        # Python sets signal handlers only on the main thread; a program calling main() from another gets none.
        codes = []
        with mock.patch.dict(os.environ, {"SIGNOFF_INBOX_HOME": str(self.home)}), \
                mock.patch.object(sys, "stdout", io.StringIO()), mock.patch.object(sys, "stderr", io.StringIO()):
            t = threading.Thread(target=lambda: codes.append(
                signoff.main([str(self.rounds.target), "--no-open", "--timeout", "0.002"])))
            t.start()
            t.join(30)
        self.assertEqual(codes, [signoff.EXIT_TIMEOUT])


PLAN_TOPIC = "2026-11-15-plan-abcd1234"
# JavaScript's trimEnd() takes the white space at the end (U+3000, U+FEFF, U+2028 too) but not U+001C, which
# Python's rstrip() would take: the mod and the command must cut the same bytes.
PLAN_KEPT = ("# Plan\n\n1. " + chr(0xC4) + "ndern  \r\n2. Ship\x1c").encode("utf-8")
PLAN_TEXT = PLAN_KEPT + (" \t\n" + chr(0x2028) + chr(0x3000) + chr(0xFEFF) + "\r\n").encode("utf-8")


def round_bytes(n):
    """round-NN.json as signoff plan writes it: the bytes --resume asks a round to have."""
    return (json.dumps(signoff.plan_round(n), indent=2) + "\n").encode("utf-8")


def outcome_of(line):
    return {"log": "signoff plan", "line": line, "date": datetime_today()}


class CliPlan(unittest.TestCase):
    """`signoff plan --topic <t> --folder <f> [--resume]`, the plan on stdin: the mod's one fixed round, written
    beside the plan and waited on beside a live inbox. The tests hold the inbox's lock themselves."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir(mode=0o700)
        (self.home / "inbox.lock").write_bytes(b"")
        self.project = self.base / "project"
        (self.project / ".git").mkdir(parents=True)
        self.topic = self.project / ".signoff" / PLAN_TOPIC
        self.env = dict(os.environ, SIGNOFF_INBOX_HOME=str(self.home))
        self.held = []

    def tearDown(self):
        stop_all()
        while self.held:
            signoff.unlock(self.held.pop())
        self._tmp.cleanup()

    def live(self):
        fd = signoff.try_lock(self.home / "inbox.lock")
        self.assertIsNotNone(fd)
        self.held.append(fd)

    def argv(self, *more, topic=PLAN_TOPIC, folder=".signoff"):
        return ["plan", "--topic", topic, "--folder", folder] + list(more)

    def start(self, argv, stdin=PLAN_TEXT, cwd=None):
        proc = track(subprocess.Popen([sys.executable, "-I", "-B", SCRIPT] + argv, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env,
                                      cwd=str(cwd or self.project)))
        proc.stdin.write(stdin)  # small: fits the pipe whether or not the command reads it
        proc.stdin.close()
        proc.stdin = None  # Python 3.9's communicate() would flush the closed pipe
        return proc

    def run_plan(self, argv, stdin=PLAN_TEXT, cwd=None):
        proc = subprocess.run([sys.executable, "-I", "-B", SCRIPT] + argv, input=stdin, capture_output=True,
                              env=self.env, cwd=str(cwd or self.project), timeout=30)
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def files(self, folder=None):
        found = {}
        for base, dirs, names in os.walk(str(folder or self.base)):
            for n in dirs + names:
                p = os.path.join(base, n)
                # A lock file is empty, and on Windows a held one cannot be read: its name is enough.
                skip = os.path.isdir(p) or os.path.islink(p) or n.endswith(".lock")
                found[p] = None if skip else Path(p).read_bytes()
        return found

    def refused(self, argv, stdin=PLAN_TEXT, cwd=None, words=()):
        before = self.files()
        code, out, err = self.run_plan(argv, stdin, cwd)
        self.assertEqual((code, out, err.count("\n")), (1, "", 1), (argv, err))
        self.assertTrue(err.startswith("signoff: "), err)
        for w in words:
            self.assertIn(w, err, argv)
        self.assertEqual(self.files(), before, argv)

    def seed(self, n, text=PLAN_KEPT, done=False, outcome=False, topic=None):
        """A round of an earlier `signoff plan`: its plan, its round file, and what came of it."""
        topic = topic or self.topic
        topic.mkdir(parents=True, exist_ok=True)
        if not (topic / ".gitignore").exists():
            signoff.ensure_gitignore(topic)
        (topic / ("plan-%02d.md" % n)).write_bytes(text)
        (topic / ("round-%02d.json" % n)).write_bytes(round_bytes(n))
        if done:
            self.answer(n, topic)
        if outcome:
            (topic / ("round-%02d.outcome.json" % n)).write_text('{"log": "", "line": "x", "date": "2026-01-01"}',
                                                                 encoding="utf-8")

    def answer(self, n, topic=None, choice="approve", **extra):
        record = done_record({"plan": {"choice": choice, "comment": "", "source": "user"}})
        record.update(extra)
        write_outside((topic or self.topic) / ("round-%02d.answers.json" % n), record)

    def stays_waiting(self, proc, n, topic=None):
        """Waits on round n with no answers of its own, then is stopped: it never exited 0 or printed a path."""
        self.waiting(proc, n, topic)
        time.sleep(1.5)
        self.assertIsNone(proc.poll(), proc.poll() is not None and proc.communicate())
        self.assertFalse(((topic or self.topic) / ("round-%02d.answers.json" % n)).exists())
        proc.kill()
        self.assertEqual(proc.communicate(timeout=20)[0], b"")

    def in_process(self, argv, stdin=PLAN_TEXT):
        """signoff.main in this process, with this home: (exit code, stderr)."""
        err = io.StringIO()
        with mock.patch.dict(os.environ, SIGNOFF_INBOX_HOME=str(self.home)), \
                mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin))), \
                mock.patch.object(sys, "stderr", err):
            code = signoff.main(argv)
        return code, err.getvalue()

    def entry(self, run, folder, rounds, pid=None, **extra):
        (self.home / "runs").mkdir(mode=0o700, exist_ok=True)
        e = {"schema": 1, "run": run, "project": None, "folder": str(folder), "rounds": rounds, "mode": "waiter",
             "pid": os.getpid() if pid is None else pid, "started": signoff.utc_now()}
        e.update(extra)
        (self.home / "runs" / (run + ".json")).write_text(json.dumps(e), encoding="utf-8")

    def entries(self):
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((self.home / "runs").glob("[!.]*.json"))]

    def waiting(self, proc, n, topic=None):
        """Once the command waits on round n: its runs/ entry lists that round, as a waiter."""
        topic = topic or self.topic
        deadline = time.monotonic() + 20
        while True:
            mine = [e for e in self.entries() if e["pid"] == proc.pid] if (self.home / "runs").is_dir() else []
            if mine:
                break
            self.assertIsNone(proc.poll(), proc.poll() is not None and proc.communicate())
            self.assertLess(time.monotonic(), deadline, "the command did not start waiting")
            time.sleep(0.05)
        self.assertEqual((mine[0]["mode"], mine[0]["folder"], mine[0]["rounds"]),
                         ("waiter", str(topic), ["round-%02d.json" % n]))

    def handed_over(self, proc, n, topic=None):
        out, err = proc.communicate(timeout=20)
        err = err.decode("utf-8")
        self.assertEqual((proc.returncode, out.decode("utf-8")),
                         (0, "%s\n" % ((topic or self.topic) / ("round-%02d.answers.json" % n))), err)
        self.assertNotIn("http://", err)

    def outcome(self, n, topic=None):
        return signoff.load_outcome((topic or self.topic) / ("round-%02d.outcome.json" % n))

    def test_without_a_live_inbox_it_exits_1_and_writes_nothing(self):
        self.refused(self.argv(), words=("inbox",))  # inbox.lock there but held by nobody
        (self.home / "inbox.lock").unlink()
        self.refused(self.argv(), words=("inbox",))  # no inbox has ever run

    def test_bad_input_exits_1_in_one_line_and_writes_nothing(self):
        self.live()
        for topic in ("2026-11-15-plan-", "2026-11-15-plan-abcdefghi", "2026-11-15-plan-ab_c", "x2026-11-15-plan-a",
                      "2026-11-15-plan-a\n", chr(0x662) + "026-11-15-plan-a", "../2026-11-15-plan-a", "2026-11-15-Plan-a"):
            self.refused(self.argv(topic=topic), words=("--topic",))
        good = self.argv()
        for argv in (good + ["--bogus"], good + ["--resume", "--resume"], good + ["--topic", PLAN_TOPIC],
                     good[:3], good + ["--folder"], good + ["extra"], self.argv(folder=""), ["plan", "--resume"],
                     ["plan", "--folder", ".signoff"], ["plan", "--resume", "--folder", ".signoff", PLAN_TOPIC],
                     ["plan", "--topic=" + PLAN_TOPIC, "--topic", PLAN_TOPIC, "--folder", ".signoff"]):
            self.refused(argv)
        # The flags in any order, --resume anywhere, are the same command: here refused only for its stdin.
        for argv in (["plan", "--folder", ".signoff", "--resume", "--topic", PLAN_TOPIC], good + ["--resume"]):
            self.refused(argv, stdin=b"\xff\xfe# Plan", words=("UTF-8",))
        for stdin in (b"# Plan \xc3", b"\xed\xa0\x80", b"", (" \t\r\n" + chr(0x3000) + chr(0xFEFF) + chr(0x2028)).encode("utf-8"),
                      b"x" * (signoff.MAX_ATTACHMENT + 1), b"x" * (2 * signoff.MAX_DECISIONS)):
            self.refused(good, stdin=stdin)
        loose = self.base / "loose"
        loose.mkdir()
        self.refused(good, cwd=loose, words=("project",))  # a relative folder needs a project root

    @unittest.skipIf(sys.platform == "win32", "links need extra rights on Windows")
    def test_a_topic_folder_that_is_a_link_or_a_file_is_refused(self):
        self.live()
        (self.base / "elsewhere").mkdir()
        self.topic.parent.mkdir()
        self.topic.symlink_to(self.base / "elsewhere", target_is_directory=True)
        self.refused(self.argv())
        self.topic.unlink()
        self.topic.write_bytes(b"")
        self.refused(self.argv())

    def test_the_first_round_its_files_and_one_answers_path_on_stdout(self):
        self.live()
        proc = self.start(self.argv())
        self.waiting(proc, 1)
        self.assertEqual((self.topic / "plan-01.md").read_bytes(), PLAN_KEPT)
        self.assertEqual((self.topic / "round-01.json").read_bytes(), round_bytes(1))
        self.assertEqual(sorted(p.name for p in self.topic.iterdir()), [".gitignore", "plan-01.md", "round-01.json"])
        os.mkdir(str(self.base / "made"))  # as an agent's mkdir makes a topic folder
        self.assertEqual(stat.S_IMODE(self.topic.stat().st_mode), stat.S_IMODE((self.base / "made").stat().st_mode))
        time.sleep(2.5)
        self.assertIsNone(proc.poll(), "a waiter has no idle timeout")
        self.answer(1)
        self.handed_over(proc, 1)
        self.assertEqual([e["exit"] for e in self.entries()], [0])

    def test_an_absolute_folder_needs_no_project(self):
        self.live()
        folder = self.base / "decisions"
        proc = self.start(self.argv(folder=str(folder)), cwd=self.base)
        self.waiting(proc, 1, folder / PLAN_TOPIC)
        self.answer(1, folder / PLAN_TOPIC)
        self.handed_over(proc, 1, folder / PLAN_TOPIC)

    def test_a_new_call_closes_the_open_older_rounds_and_takes_the_next_number(self):
        self.seed(1)
        self.seed(2, done=True)
        self.seed(3, outcome=True)
        self.seed(4)
        (self.topic / "plan-05.md").write_bytes(b"left by a run that stopped before its round")
        # A run that waited on round 4 and ended: it holds nothing now.
        ended = {"schema": 1, "run": "ab" * 8, "project": None, "folder": str(self.topic),
                 "rounds": ["round-04.json"], "mode": "waiter", "pid": os.getpid(), "started": signoff.utc_now(),
                 "ended": signoff.utc_now()}
        (self.home / "runs").mkdir(mode=0o700)
        (self.home / "runs" / "ended.json").write_text(json.dumps(ended), encoding="utf-8")
        # A live run in another folder waits on a round-01.json of its own; one killed with no `ended` (its pid gone)
        # once waited on round 4. Neither holds a round of this topic.
        other = self.base / "other"
        other.mkdir()
        self.entry("ef" * 8, other, ["round-01.json"])
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        self.entry("0123" * 4, self.topic, ["round-04.json"], pid=gone.pid)
        outcome3 = (self.topic / "round-03.outcome.json").read_bytes()
        self.live()
        proc = self.start(self.argv())
        self.waiting(proc, 6)
        for n in (1, 4):
            self.assertEqual(self.outcome(n), outcome_of("replaced by round-06"))
        self.assertFalse((self.topic / "round-02.outcome.json").exists())  # done: nothing to close
        self.assertEqual((self.topic / "round-03.outcome.json").read_bytes(), outcome3)
        self.assertEqual((self.topic / "plan-06.md").read_bytes(), PLAN_KEPT)
        self.answer(6, choice="revise")
        self.handed_over(proc, 6)

    @unittest.skipIf(sys.platform == "win32", "_alive() takes every pid there for alive, so the killed call still holds")
    def test_the_past_decisions_page_opens_a_plan_topic(self):
        # Old rounds stay readable: a topic as two plan reviews leave it, the first replaced, the second answered.
        self.live()
        first = self.start(self.argv())
        self.waiting(first, 1)
        first.kill()
        first.communicate(timeout=20)  # its pid gone, so the next call closes its round
        revised = b"# Plan\n\n1. Read\n2. Write"
        second = self.start(self.argv(), stdin=revised + b"\n")
        self.waiting(second, 2)
        note = {"att": 0, "line": 3, "quote": "1. Read", "comment": "Read the tests first"}
        record = done_record({"plan": {"choice": "revise", "comment": "Smaller steps", "source": "user",
                                       "lines": [note]}})
        write_outside(self.topic / "round-02.answers.json", record)
        self.handed_over(second, 2)
        server = signoff.make_history_server(self.project)
        self.addCleanup(server.server_close)
        view = server.view()
        self.assertNotIn(str(self.base), json.dumps(view))
        e = {x["file"]: x for x in view["entries"] if x["topic"] == PLAN_TOPIC}
        self.assertEqual(sorted(e), ["round-01.json", "round-02.json"])
        for name, plan in (("round-01.json", PLAN_KEPT), ("round-02.json", revised)):
            self.assertEqual((e[name]["title"], e[name]["error"], e[name]["warning"]), ("Plan review", None, None), name)
            attachment = e[name]["decisions"]["items"][0]["attachments"][0]
            self.assertEqual((attachment["src"], attachment["text"]),
                             ("plan-%s.md" % name[6:8], plan.decode("utf-8")), name)
        self.assertEqual((e["round-01.json"]["status"], e["round-01.json"]["outcome"]),
                         ("new", outcome_of("replaced by round-02")))
        answered = e["round-02.json"]
        self.assertEqual((answered["status"], answered["outcome"]), ("done", None))
        plan = answered["answers"]["answers"]["plan"]
        self.assertEqual((plan["choice"], plan["comment"], plan["lines"]), ("revise", "Smaller steps", [note]))

    def test_an_older_round_a_live_run_waits_on_is_left_alone(self):
        self.seed(1)
        self.seed(2)
        self.entry("cd" * 8, self.topic, ["round-01.json"])
        self.live()
        self.refused(self.argv(), words=("a plan review is still open in another process",))

    @unittest.skipIf(sys.platform == "win32", "links need extra rights on Windows")
    def test_a_live_run_on_the_topic_spelled_another_way_still_holds_it(self):
        # The folder is matched as the same folder, not as the same text: here through a link to its parent.
        self.seed(1)
        (self.base / "alias").symlink_to(self.topic.parent, target_is_directory=True)
        self.entry("cd" * 8, self.base / "alias" / PLAN_TOPIC, ["round-01.json"])
        self.live()
        self.refused(self.argv(), words=("a plan review is still open in another process",))

    def test_resume_waits_on_the_same_open_round_and_writes_nothing(self):
        self.seed(1, outcome=True)
        self.seed(2)
        self.live()
        before = self.files(self.topic)
        proc = self.start(self.argv("--resume"))
        self.waiting(proc, 2)
        self.assertEqual(self.files(self.topic), before)
        self.answer(2)
        self.handed_over(proc, 2)

    def test_resume_on_the_same_done_round_prints_its_path_at_once(self):
        self.seed(1, done=True)
        self.live()
        before = self.files()
        code, out, err = self.run_plan(self.argv("--resume"))
        self.assertEqual((code, out), (0, "%s\n" % (self.topic / "round-01.answers.json")), err)
        self.assertEqual(self.files(), before)

    def test_resume_writes_a_new_round_for_other_bytes_none_or_a_closed_one(self):
        self.live()
        cases = {"a": lambda t: self.seed(1, text=PLAN_KEPT + b"\nmore", topic=t),  # other bytes, open
                 "b": lambda t: self.seed(1, text=PLAN_KEPT + b"\nmore", done=True, topic=t),  # other bytes, done
                 "c": lambda t: self.seed(1, outcome=True, topic=t),  # the same bytes, closed without answers
                 "d": lambda t: None}  # no round yet
        for name, make in cases.items():
            topic = self.project / ".signoff" / ("2026-11-15-plan-" + name)
            make(topic)
            proc = self.start(self.argv("--resume", topic="2026-11-15-plan-" + name))
            n = 1 if name == "d" else 2
            self.waiting(proc, n, topic)
            self.assertEqual((topic / ("plan-%02d.md" % n)).read_bytes(), PLAN_KEPT)
            if name == "a":
                self.assertEqual(self.outcome(1, topic)["line"], "replaced by round-02")
            if name == "b":
                self.assertFalse((topic / "round-01.outcome.json").exists())
            if name == "c":
                self.assertEqual(self.outcome(1, topic)["line"], "x")
            self.answer(n, topic)
            self.handed_over(proc, n, topic)

    def plant_approval(self, n, topic):
        """A done answers file approving round n, as `git clean -fd` leaves one (ignored, so kept) with no round
        beside it, or as another writer plants one: linked to the fixed round's bytes, so a round written at n
        would take it in."""
        topic.mkdir(parents=True, exist_ok=True)
        self.answer(n, topic, decisions={"file": "round-%02d.json" % n,
                                         "sha256": hashlib.sha256(round_bytes(n)).hexdigest()})

    def test_files_left_at_the_next_number_never_answer_the_new_round(self):
        self.live()
        topic = self.project / ".signoff" / "2026-11-15-plan-a"  # only what git clean -fd keeps
        self.plant_approval(1, topic)
        (topic / "round-01.answers.md").write_text("# done\n", encoding="utf-8")
        (topic / "round-01.json.lock").write_bytes(b"")
        self.stays_waiting(self.start(self.argv(topic="2026-11-15-plan-a")), 2, topic)
        self.assertFalse((topic / "round-01.json").exists())
        topic = self.project / ".signoff" / "2026-11-15-plan-b"  # the same, then --resume with the same plan
        self.plant_approval(1, topic)
        self.stays_waiting(self.start(self.argv("--resume", topic="2026-11-15-plan-b")), 2, topic)
        topic = self.project / ".signoff" / "2026-11-15-plan-c"  # an open round, then files at the next number
        self.seed(1, topic=topic)
        self.plant_approval(2, topic)
        (topic / "round-03.outcome.json").write_text('{"log": "", "line": "x", "date": "2026-01-01"}',
                                                     encoding="utf-8")
        self.stays_waiting(self.start(self.argv(topic="2026-11-15-plan-c")), 4, topic)
        self.assertEqual(self.outcome(1, topic)["line"], "replaced by round-04")

    def test_resume_reuses_only_the_fixed_round_with_answers_a_run_takes(self):
        self.live()
        changed = signoff.plan_round(1)
        changed["items"][0]["recommended"] = "approve"  # not the fixed round any more
        cases = {
            "a": lambda t: (t / "round-01.json").write_bytes(json.dumps(changed, indent=2).encode() + b"\n"),
            "b": lambda t: self.answer(1, t, choice="nope"),  # check_answers refuses it
            # answers for another version of the round
            "c": lambda t: self.answer(1, t, decisions={"file": "round-01.json", "sha256": "0" * 64}),
        }
        for name, spoil in cases.items():
            topic = self.project / ".signoff" / ("2026-11-15-plan-" + name)
            self.seed(1, done=True, topic=topic)
            spoil(topic)
            self.stays_waiting(self.start(self.argv("--resume", topic="2026-11-15-plan-" + name)), 2, topic)

    @unittest.skipIf(sys.platform == "win32", "links need extra rights on Windows")
    def test_resume_never_reuses_a_round_or_plan_through_a_link(self):
        self.live()
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "round-01.json").write_bytes(round_bytes(1))
        (elsewhere / "plan-01.md").write_bytes(PLAN_KEPT)
        self.answer(1, elsewhere)
        before = self.files(elsewhere)
        topic = self.project / ".signoff" / "2026-11-15-plan-a"  # the round is a link to a done one elsewhere
        topic.mkdir(parents=True)
        (topic / "plan-01.md").write_bytes(PLAN_KEPT)
        (topic / "round-01.json").symlink_to(elsewhere / "round-01.json")
        self.stays_waiting(self.start(self.argv("--resume", topic="2026-11-15-plan-a")), 2, topic)
        topic = self.project / ".signoff" / "2026-11-15-plan-b"  # the plan is a link to the same bytes
        topic.mkdir(parents=True)
        (topic / "round-01.json").write_bytes(round_bytes(1))
        (topic / "plan-01.md").symlink_to(elsewhere / "plan-01.md")
        self.stays_waiting(self.start(self.argv("--resume", topic="2026-11-15-plan-b")), 2, topic)
        self.assertEqual(self.files(elsewhere), before)

    @unittest.skipIf(sys.platform == "win32", "links need extra rights on Windows")
    def test_a_topic_folder_linked_in_after_the_check_gets_nothing(self):
        self.live()
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()

        def linked_in(path, *args, **kwargs):  # the folder appears as a link between the check and the write
            os.makedirs(str(path.parent), exist_ok=True)
            path.symlink_to(elsewhere, target_is_directory=True)

        with mock.patch.object(Path, "mkdir", linked_in), mock.patch.object(signoff, "_run", return_value=0):
            code, err = self.in_process(self.argv(folder=str(self.project / ".signoff")))
        self.assertEqual((code, err.count("\n")), (1, 1), err)
        self.assertIn("cannot open", err)
        self.assertEqual(os.listdir(str(elsewhere)), [])

    def test_a_topic_folder_it_cannot_make_says_it_cannot_write(self):
        self.live()
        denied = PermissionError(errno.EACCES, "Permission denied")
        with mock.patch.object(Path, "mkdir", side_effect=denied), mock.patch.object(signoff, "_run", return_value=0):
            code, err = self.in_process(self.argv(folder=str(self.project / ".signoff")))
        self.assertEqual((code, err.count("\n")), (1, 1), err)
        self.assertIn("cannot write in", err)
        self.assertNotIn("not a link", err)  # a read-only decisions folder is no link; its path may hold the word

    @unittest.skipIf(sys.platform == "win32", "the folder is opened as a descriptor on POSIX only")
    def test_a_file_that_appears_at_the_new_name_is_never_written_over(self):
        self.live()
        self.seed(1)
        opened = signoff._open_folder

        def planted(path):  # another writer creates plan-02.md between the look and the create
            (path / "plan-02.md").write_bytes(b"theirs")
            return opened(path)

        with mock.patch.object(signoff, "_open_folder", planted), mock.patch.object(signoff, "_run", return_value=0):
            code, err = self.in_process(self.argv(folder=str(self.project / ".signoff")))
        self.assertEqual((code, err.count("\n")), (1, 1), err)
        self.assertIn("cannot write", err)
        self.assertEqual((self.topic / "plan-02.md").read_bytes(), b"theirs")
        self.assertFalse((self.topic / "round-02.json").exists())
        self.assertEqual(self.outcome(1), outcome_of("replaced by round-02"))  # closed first, as AC5 orders

    def test_past_round_9999_there_is_no_number_left(self):
        self.live()
        self.topic.mkdir(parents=True)
        (self.topic / "round-9999.answers.json").write_text("{}", encoding="utf-8")
        self.refused(self.argv(), words=("no round number left",))

    @unittest.skipIf(sys.platform == "win32", "the inbox does not run on Windows")
    def test_resume_on_an_open_round_an_older_inbox_refuses_closes_it(self):
        self.seed(1)
        self.live()
        CliInbox.looking(self)
        proc = self.start(self.argv("--resume"))
        out, err = proc.communicate(timeout=30)
        self.assertEqual((proc.returncode, out), (1, b""), err)
        self.assertEqual(self.outcome(1), outcome_of("not shown: the inbox is older than this plugin; restart it"))
        self.assertFalse((self.topic / "round-02.json").exists())

    def test_a_forced_waiter_never_takes_the_page_path(self):
        # signoff plan checked the inbox; if it stops before the run looks, the run still waits, with no page.
        self.seed(1)  # inbox.lock there, held by nobody
        out, codes = io.StringIO(), []
        with mock.patch.dict(os.environ, SIGNOFF_INBOX_HOME=str(self.home)), \
                mock.patch.object(signoff, "make_server", side_effect=AssertionError("a page run")), \
                mock.patch.object(sys, "stdout", out):
            t = threading.Thread(target=lambda: codes.append(signoff._run(
                argparse.Namespace(decisions=str(self.topic / "round-01.json"), notes=""), waiter=True)))
            t.start()
            deadline = time.monotonic() + 20
            while not ((self.home / "runs").is_dir() and self.entries()):
                self.assertTrue(t.is_alive(), codes)
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.05)
            self.assertEqual([e["mode"] for e in self.entries()], ["waiter"])
            self.answer(1)
            t.join(20)
        self.assertEqual((codes, out.getvalue()), ([0], "%s\n" % (self.topic / "round-01.answers.json")))

    @unittest.skipIf(sys.platform == "win32", "Ctrl+C is sent as SIGINT, which is POSIX only")
    def test_ctrl_c_exits_130_with_nothing_on_stdout(self):
        self.live()
        proc = self.start(self.argv())
        self.waiting(proc, 1)
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, out), (130, b""), err)

    def older_inbox_refused(self, proc):
        out, err = proc.communicate(timeout=30)
        err = err.decode("utf-8")
        self.assertEqual((proc.returncode, out), (1, b""), err)
        self.assertIn("signoff inbox stop", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.outcome(1), outcome_of("not shown: the inbox is older than this plugin; restart it"))
        self.assertEqual((self.topic / "plan-01.md").read_bytes(), PLAN_KEPT)
        self.assertFalse((self.topic / "round-01.answers.json").exists())

    @unittest.skipIf(sys.platform == "win32", "the inbox does not run on Windows")
    def test_an_inbox_that_never_takes_the_round_in_gets_it_closed(self):
        self.live()
        CliInbox.looking(self)  # touches inbox.lock as an inbox's looks do, and locks no round
        self.older_inbox_refused(self.start(self.argv()))


def datetime_today():
    import datetime
    return datetime.date.today().isoformat()


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


class WaiterVerdict(unittest.TestCase):
    """The waiter's one check, poll by poll and with no real sleep: inbox.lock's time changes at every poll, as an
    inbox that looks each second would; inbox.json and the locks change as each test says."""

    def setUp(self):
        self.rounds = Rounds(1)
        self.addCleanup(self.rounds.cleanup)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        (self.home / "inbox.lock").write_bytes(b"")
        self.held = []
        self.addCleanup(lambda: [signoff.unlock(fd) for fd in self.held])
        self.inbox(1)

    def inbox(self, n):
        (self.home / "inbox.json").write_text(json.dumps({"schema": 1, "server_id": "%032x" % n}), encoding="utf-8")

    def hold(self, path):
        self.held.append(signoff.try_lock(path))

    def wait(self, finish_at, at=None):
        """_wait's result: its rounds are answered at poll finish_at; at maps a poll to what happens before it."""
        rounds, polls = signoff.load_rounds(self.rounds.target), [0]

        def sleep(_):
            polls[0] += 1
            (at or {}).get(polls[0], lambda: None)()

        def reread(r):
            if polls[0] >= finish_at:
                r.finished = True

        with mock.patch.object(signoff, "_lock_time", lambda home: polls[0]), \
                mock.patch.object(signoff, "_reread", reread), mock.patch.object(signoff.time, "sleep", sleep):
            return signoff._wait(rounds, self.home)

    def test_two_looks_and_no_lock_held_refuse(self):
        self.hold(self.home / "inbox.lock")
        self.assertEqual(self.wait(finish_at=10), "refused")

    def test_rounds_answered_in_the_polls_of_the_verdict_end_as_done(self):
        self.hold(self.home / "inbox.lock")
        self.assertEqual(self.wait(finish_at=2), "done")

    def test_an_inbox_gone_after_its_second_look_gives_no_verdict(self):
        self.assertEqual(self.wait(finish_at=3), "done")  # nobody holds inbox.lock: the inbox stopped

    def test_an_inbox_started_again_between_the_looks_is_counted_afresh(self):
        # Inbox 1 looks once and stops; inbox 2 starts, looks, and its slow first look locks the round a poll later.
        self.hold(self.home / "inbox.lock")
        lock = self.rounds.path / "round-01.json.lock"
        self.assertEqual(self.wait(finish_at=6, at={2: lambda: self.inbox(2), 3: lambda: self.hold(lock)}), "done")

    def test_an_inbox_started_again_that_never_locks_is_still_judged(self):
        self.hold(self.home / "inbox.lock")
        self.assertEqual(self.wait(finish_at=10, at={2: lambda: self.inbox(2)}), "refused")


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


def marking_decisions():
    """sample_decisions with an item for each kind of the user's own text: Other, marks, Skip, a number."""
    data = sample_decisions()
    two = [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
    data["items"] += [
        {"id": "other", "title": "Other?", "options": two, "other": True},
        {"id": "pic", "title": "Point at it", "images": [{"src": "mockups/a.png"}], "marks": True, "options": two},
        {"id": "skip", "title": "Skip?", "options": two, "skippable": True},
        {"id": "num", "title": "How many?", "select": "number", "range": {"min": 0, "max": 10}, "recommended": 3},
        {"id": "sens", "title": "Your setup?", "options": two, "recommended": "a", "sensitive": True},
        {"id": "tags", "title": "Tags?", "select": "many", "options": two},
    ]
    return data


class CliElsewhere(unittest.TestCase):
    """signoff elsewhere <round file> <item id> [<note>]: the agent marks one item as settled in chat while the run
    waits. Without inbox.lock it writes the answers file itself, best effort; with it, it holds the round lock for
    the write, or asks the inbox that holds it, or refuses when a page run does. It never writes over the user's
    own answer, and a mark resets the round's acceptance as a page change does."""

    def setUp(self):
        self.folder = Folder(marking_decisions())
        self.md = self.folder.path / "decisions.answers.md"
        self.lock = self.folder.path / "decisions.json.lock"
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()  # no inbox.lock unless a test writes one
        self.held = []

    def tearDown(self):
        stop_all()
        while self.held:
            signoff.unlock(self.held.pop())
        self.folder.cleanup()
        self._tmp.cleanup()

    def mark(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err), \
                mock.patch.dict(os.environ, SIGNOFF_INBOX_HOME=str(self.home)):
            code = signoff.main(["elsewhere"] + [str(a) for a in args])
        return code, out.getvalue(), err.getvalue()

    def saved(self):
        return json.loads(self.folder.answers.read_text(encoding="utf-8"))

    def files(self, folder):
        return {p.name: p.read_bytes() for p in folder.path.iterdir() if p.is_file()}

    def refused(self, args, folder=None, words=()):
        folder = folder or self.folder
        before = self.files(folder)
        code, out, err = self.mark(*args)
        self.assertEqual((code, out), (1, ""), err)
        self.assertEqual(err.count("\n"), 1, err)
        self.assertTrue(err.startswith("signoff: "), err)
        self.assertNotIn("Traceback", err)
        for w in words:
            self.assertIn(w, err)
        self.assertEqual(self.files(folder), before)
        return err

    def test_it_marks_the_item_resets_acceptance_and_prints_nothing(self):
        write_outside(self.folder.answers, {"version": 1, "done": False, "saved": "2026-01-01T12:00:00Z",
                                            "accepted": True, "accepted_at": "2026-01-01T12:00:00Z",
                                            "answers": {"layout": {"choice": "two", "comment": "mine",
                                                                   "source": "user"}},
                                            "free_text": "kept"})
        order = []
        real = signoff.write_atomic
        with mock.patch.object(signoff, "write_atomic",
                               side_effect=lambda p, d, **kw: (order.append(p.name), real(p, d, **kw))[1]):
            code, out, err = self.mark(self.folder.decisions, "filters", "settled in chat")
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(order, ["decisions.answers.md", "decisions.answers.json"])  # the JSON goes last
        saved = self.saved()
        self.assertEqual(saved["answers"]["filters"], {"choice": [], "comment": "settled in chat",
                                                       "source": "elsewhere"})
        self.assertEqual(saved["answers"]["layout"], {"choice": "two", "comment": "mine", "source": "user"})
        # A change after Accept undoes it, as on the page: the user accepts the round again.
        self.assertEqual((saved["version"], saved["done"], saved["free_text"], saved["accepted"],
                          saved["changed_after_accept"]), (1, False, "kept", False, True))
        self.assertNotIn("accepted_at", saved)
        sha = hashlib.sha256(self.folder.decisions.read_bytes()).hexdigest()
        self.assertEqual(saved["decisions"], {"file": "decisions.json", "sha256": sha})
        signoff.check_answers(saved, signoff.load_decisions(self.folder.decisions)[0], stored=True)
        self.assertIn("- Source: decided elsewhere, marked by the agent\n- Comment: settled in chat",
                      self.md.read_text(encoding="utf-8"))
        self.assertTrue((self.folder.path / ".gitignore").is_file())  # as a run writes it
        self.assertFalse(self.lock.exists())  # no inbox has run: no lock file either

    def test_without_a_note_and_without_an_answers_file(self):
        for item, choice in (("layout", None), ("num", None)):
            self.assertEqual(self.mark(self.folder.decisions, item), (0, "", ""))
            self.assertEqual(self.saved()["answers"][item], {"choice": choice, "comment": "", "source": "elsewhere"})
        self.assertEqual((self.saved()["free_text"], self.saved()["done"], self.saved()["accepted"]),
                         ("", False, False))

    def test_the_same_mark_again_writes_nothing_and_a_new_note_replaces_it(self):
        self.assertEqual(self.mark(self.folder.decisions, "layout", "x")[0], 0)
        before = self.files(self.folder)
        with mock.patch.object(signoff, "write_atomic") as write:
            self.assertEqual(self.mark(self.folder.decisions, "layout", "x"), (0, "", ""))
        write.assert_not_called()
        self.assertEqual(self.files(self.folder), before)
        self.assertEqual(self.mark(self.folder.decisions, "layout", "another note")[0], 0)
        self.assertEqual(self.saved()["answers"]["layout"]["comment"], "another note")
        # The page sends the mark back with an empty marks list on an item that takes marks: still the same mark.
        write_outside(self.folder.answers, {"version": 1, "done": False, "saved": "2026-01-01T12:00:00Z",
                                            "accepted": True, "accepted_at": "2026-01-01T12:00:00Z", "free_text": "",
                                            "answers": {"pic": {"choice": None, "comment": "x", "source": "elsewhere",
                                                                "marks": []}}})
        before = self.files(self.folder)
        with mock.patch.object(signoff, "write_atomic") as write:
            self.assertEqual(self.mark(self.folder.decisions, "pic", "x"), (0, "", ""))
        write.assert_not_called()  # no new rev, and the round stays accepted
        self.assertEqual(self.files(self.folder), before)

    def test_the_note_is_one_plain_line_up_to_the_cap(self):
        self.assertEqual(self.mark(self.folder.decisions, "layout", "x" * signoff.MAX_NOTE)[0], 0)
        self.assertEqual(self.mark(self.folder.decisions, "layout", "\U0001F600" * signoff.MAX_NOTE)[0], 0)
        # An emoji built with variation selector 16 or the zero-width joiner, as people type them.
        for note in ("ok ✔️", "by \U0001F469‍\U0001F4BB"):
            self.assertEqual(self.mark(self.folder.decisions, "layout", note), (0, "", ""), note)
            self.assertEqual(self.saved()["answers"]["layout"]["comment"], note)
        for label, note in (("too long", "x" * (signoff.MAX_NOTE + 1)), ("a line break", "two\nlines"),
                            ("a tab", "a\tb"), ("a line separator", "one two"), ("a next line", "one\x85two"),
                            ("an escape", "red\x1b[31m"), ("a lone surrogate", "x\udcff"),
                            ("a zero-width space", "a​b"), ("a bidi override", "a‮b")):
            with self.subTest(label):
                self.refused([self.folder.decisions, "layout", note], words=("note",))

    def test_each_refusal_is_exit_1_with_one_line_and_writes_nothing(self):
        bad = Folder()
        self.addCleanup(bad.cleanup)
        data = sample_decisions()
        data["items"][0]["recommended"] = "three"
        bad.write(data)
        broken = Folder()
        self.addCleanup(broken.cleanup)
        broken.answers.write_text('{"version": 1, "answers": {"nope": {}}}', encoding="utf-8")
        done = Folder()
        self.addCleanup(done.cleanup)
        done.answers.write_text(json.dumps(done_record()), encoding="utf-8")
        cases = [
            ("an invalid round file", bad, [bad.decisions, "layout"]),
            ("a missing round file", self.folder, [self.folder.path / "nope.json", "layout"]),
            ("a folder", self.folder, [self.folder.path, "layout"]),
            ("an answers file that cannot be checked", broken, [broken.decisions, "layout"]),
            ("an unknown item", self.folder, [self.folder.decisions, "nope"]),
            ("a done round", done, [done.decisions, "layout"]),
        ]
        for label, folder, args in cases:
            with self.subTest(label):
                self.refused(args, folder)

    def test_a_settled_item_is_never_marked(self):
        # The recommendation of a settled item is the user's earlier answer: a mark would replace it unseen.
        data = marking_decisions()
        data["items"][0]["settled"] = "round-01, 11-14"
        self.folder.write(data)
        self.refused([self.folder.decisions, "layout", "n"], words=("settled", '"layout"'))
        (self.home / "inbox.lock").touch()  # with the switch on, a refusal still leaves no lock file
        self.refused([self.folder.decisions, "layout", "n"], words=("settled",))
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.mark(self.folder.decisions, "filters", "n")[0], 0)  # another item is marked as before

    def test_the_users_own_answer_is_never_overwritten(self):
        theirs = [
            ("a choice", "layout", {"choice": "one", "comment": "", "source": "user"}),
            ("an Accept all", "layout", {"choice": "two", "comment": "", "source": "accept_all"}),
            ("an accept from a notification", "layout", {"choice": "two", "comment": "", "source": "notification"}),
            ("a choice with no source", "layout", {"choice": "two", "comment": ""}),
            ("a comment on the recommendation", "layout", {"choice": "two", "comment": "mine",
                                                            "source": "recommended"}),
            ("Private on the recommendation", "layout", {"choice": "two", "comment": "", "source": "recommended",
                                                          "private": True}),
            ("Other text", "other", {"choice": "_other", "other": "mine", "comment": "", "source": "user"}),
            ("a mark", "pic", {"choice": None, "comment": "", "source": "user",
                               "marks": [{"image": 0, "box": [1, 1, 10, 10], "comment": ""}]}),
            ("a skip", "skip", {"choice": "_skip", "comment": "", "source": "user"}),
            ("a number, 0 too", "num", {"choice": 0, "comment": "", "source": "user"}),
            ("a mark of the agent's the user made Private", "layout", {"choice": None, "comment": "n",
                                                                       "source": "elsewhere", "private": True}),
            ("none of a recommended many item, by the user", "filters", {"choice": [], "comment": "",
                                                                          "source": "user"}),
            ("Private unticked on a sensitive item", "sens", {"choice": "a", "comment": "", "source": "recommended"}),
        ]
        for label, item, answer in theirs:
            with self.subTest(label):
                write_outside(self.folder.answers, {"version": 1, "done": False, "saved": "2026-01-01T12:00:00Z",
                                                    "answers": {item: answer}, "free_text": ""})
                self.refused([self.folder.decisions, item, "n"], words=("user",))
        ours = [
            ("the recommendation untouched", "layout", {"choice": "two", "comment": "", "source": "recommended"}),
            ("nothing picked", "other", {"choice": None, "comment": "", "source": "user"}),
            ("a many item with no recommendation, untouched", "tags", {"choice": [], "comment": "",
                                                                        "source": "user"}),
            ("the agent's own mark", "layout", {"choice": None, "comment": "old", "source": "elsewhere"}),
            ("a sensitive item untouched, Private by default", "sens", {"choice": "a", "comment": "",
                                                                         "source": "recommended", "private": True}),
        ]
        for label, item, answer in ours:
            with self.subTest(label):
                write_outside(self.folder.answers, {"version": 1, "done": False, "saved": "2026-01-01T12:00:00Z",
                                                    "answers": {item: answer}, "free_text": ""})
                self.assertEqual(self.mark(self.folder.decisions, item, "n"), (0, "", ""))
                self.assertEqual(self.saved()["answers"][item]["source"], "elsewhere")

    def test_a_file_changed_before_the_rename_is_read_again(self):
        # The temp file is written and synced, then the file is hashed again, then renamed: a write landing before
        # that look makes it start over, three tries in all.
        real, changes = signoff.write_atomic, []

        def write(path, data, times, unchanged=None):
            if unchanged is None:
                return real(path, data)

            def look():
                if len(changes) < times:  # another writer, after the temp file and before the rename
                    changes.append(path)
                    real(self.folder.answers, json.dumps({"version": 1, "done": False, "saved":
                                                          "2026-01-01T12:00:0%dZ" % len(changes), "answers": {},
                                                          "free_text": "theirs"}))
                return unchanged()
            return real(path, data, unchanged=look)
        with mock.patch.object(signoff, "write_atomic", side_effect=lambda p, d, **kw: write(p, d, 2, **kw)):
            code, out, err = self.mark(self.folder.decisions, "layout", "n")
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(len(changes), 2)
        self.assertEqual((self.saved()["free_text"], self.saved()["answers"]["layout"]["source"]),
                         ("theirs", "elsewhere"))  # built on what the other writer wrote
        changes.clear()
        with mock.patch.object(signoff, "write_atomic", side_effect=lambda p, d, **kw: write(p, d, 3, **kw)):
            code, out, err = self.mark(self.folder.decisions, "filters", "n")
        self.assertEqual((code, out, err.count("\n")), (1, "", 1), err)
        self.assertIn("kept changing", err)
        self.assertNotIn("filters", self.saved()["answers"])  # the other writer's last file stays
        self.assertEqual([p.name for p in self.folder.path.iterdir() if p.name.startswith(".tmp-")], [])

    def test_write_atomic_syncs_before_its_look_and_a_changed_file_is_left_alone(self):
        target = self.folder.path / "x.answers.json"
        target.write_bytes(b"before")
        calls = []
        real_fsync = os.fsync
        with mock.patch.object(signoff.os, "fsync", side_effect=lambda fd: (calls.append("fsync"), real_fsync(fd))):
            self.assertFalse(signoff.write_atomic(target, b"new", unchanged=lambda: calls.append("look") and False))
            self.assertEqual((calls, target.read_bytes()), (["fsync", "look"], b"before"))
            self.assertTrue(signoff.write_atomic(target, b"new", unchanged=lambda: True))
        self.assertEqual(target.read_bytes(), b"new")
        self.assertEqual([p.name for p in self.folder.path.iterdir() if p.name.startswith(".tmp-")], [])

    def test_a_write_that_fails_is_exit_1_with_one_line(self):
        with mock.patch.object(signoff, "write_atomic", side_effect=PermissionError(13, "Permission denied")):
            code, out, err = self.mark(self.folder.decisions, "layout")
        self.assertEqual((code, out, err.count("\n")), (1, "", 1), err)
        self.assertIn("cannot write", err)
        self.assertFalse(self.folder.answers.exists())

    def test_a_round_that_became_done_meanwhile_is_left_alone(self):
        real = signoff.write_atomic

        def write(path, data, **kw):
            done = real(path, data, **kw)
            if path.suffix == ".md":
                real(self.folder.answers, json.dumps(done_record()))
            return done
        with mock.patch.object(signoff, "write_atomic", side_effect=write):
            code, out, err = self.mark(self.folder.decisions, "layout")
        self.assertEqual((code, out, err.count("\n")), (1, "", 1), err)
        self.assertIn("done", err)
        self.assertTrue(self.saved()["done"])
        self.assertEqual(self.saved()["answers"], {})
        # Done is final, so no later save repairs the copy: it shows the file that won, not the refused mark.
        md = self.md.read_text(encoding="utf-8")
        self.assertIn("Done: yes", md)
        self.assertNotIn("decided elsewhere", md)

    def test_a_copy_written_before_a_change_lands_is_never_left_behind(self):
        # The change lands between the copy's rename and the JSON's: the copy is made again from the file on disk.
        write_outside(self.folder.answers, {"version": 1, "done": False, "saved": "2026-01-01T12:00:00Z",
                                            "answers": {}, "free_text": "before"})
        real = signoff.write_atomic

        def write(path, data, **kw):
            done = real(path, data, **kw)
            if path.suffix == ".md":
                real(self.folder.answers, json.dumps(done_record()))
            return done
        with mock.patch.object(signoff, "write_atomic", side_effect=write):
            self.assertEqual(self.mark(self.folder.decisions, "layout")[0], 1)
        self.assertNotIn("decided elsewhere", self.md.read_text(encoding="utf-8"))
        # A change before the copy's own rename leaves no copy of the refused mark at all.
        other = Folder(marking_decisions())
        self.addCleanup(other.cleanup)

        def look(path, data, **kw):
            if path.suffix == ".md":
                real(other.answers, json.dumps(done_record()))
            return real(path, data, **kw)
        with mock.patch.object(signoff, "write_atomic", side_effect=look):
            self.assertEqual(self.mark(other.decisions, "layout")[0], 1)
        self.assertFalse((other.path / "decisions.answers.md").exists())

    def test_a_running_page_takes_the_mark_in(self):
        proc = start(self.folder)
        url = url_of(proc)
        self.assertEqual(post(url, {"answers": {"layout": {"choice": "one", "comment": "", "source": "user"}},
                                    "free_text": "", "done": False, "rev": 0}), 200)
        done = subprocess.run([sys.executable, "-I", "-B", SCRIPT, "elsewhere", str(self.folder.decisions),
                               "filters", "settled in chat"], capture_output=True, universal_newlines=True,
                              encoding="utf-8", timeout=20)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        # The page that saved before the mark is stale: nothing it sends overwrites the mark.
        self.assertEqual(post_status(url, {"answers": {}, "free_text": "stale", "done": False, "rev": 1}), 409)
        self.assertEqual(self.saved()["answers"]["filters"]["source"], "elsewhere")
        body = {"answers": {"layout": {"choice": "two", "comment": "", "source": "user"},
                            "filters": {"choice": [], "comment": "settled in chat", "source": "elsewhere"}},
                "free_text": "", "done": True, "rev": 2}
        deadline = time.monotonic() + 10
        while post_status(url, body) != 200:  # the run takes the file in within about a second: rev 2
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.3)
        out, err = Cli.finish(self, proc)
        self.assertEqual((proc.returncode, out), (0, str(self.folder.answers) + "\n"), err)
        self.assertEqual(self.saved()["answers"]["filters"], {"choice": [], "comment": "settled in chat",
                                                              "source": "elsewhere"})

    def test_a_decisions_folder_named_elsewhere_still_runs(self):
        rounds = Rounds(1)
        self.addCleanup(rounds.cleanup)
        target = rounds.path / "elsewhere"
        (target / "mockups").mkdir(parents=True)
        (target / "round-01.json").write_bytes(rounds.round(1).read_bytes())
        (target / "mockups" / "a.png").write_bytes((rounds.path / "mockups" / "a.png").read_bytes())
        for args in (["--no-open"], ["--no-open", "--timeout", "5"], ["--timeout", "5", "--no-open"]):
            with self.subTest(args):
                proc = track(subprocess.Popen([sys.executable, "-I", "-B", SCRIPT, "elsewhere"] + args,
                                              cwd=str(rounds.path), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              universal_newlines=True, encoding="utf-8"))
                self.assertIn("http://127.0.0.1:", proc.stderr.readline())  # a run, not the command
                stop_all()

    # Once an inbox has run (inbox.lock in the home), the round lock decides who writes.

    def test_with_the_home_and_a_free_lock_it_holds_the_lock_for_the_write_only(self):
        (self.home / "inbox.lock").write_bytes(b"")
        real, held = signoff.write_atomic, []

        def write(path, data, **kw):
            fd = signoff.try_lock(self.lock, create=False)  # taken by the command while it writes
            held.append(fd is None)
            if fd is not None:
                signoff.unlock(fd)
            return real(path, data, **kw)
        with mock.patch.object(signoff, "write_atomic", side_effect=write):
            self.assertEqual(self.mark(self.folder.decisions, "layout", "n"), (0, "", ""))
        self.assertEqual(held, [True, True])
        self.assertEqual(self.saved()["answers"]["layout"]["source"], "elsewhere")
        fd = signoff.try_lock(self.lock, create=False)  # and let go after it
        self.assertIsNotNone(fd)
        signoff.unlock(fd)

    def test_a_refusal_leaves_no_lock_file(self):
        (self.home / "inbox.lock").write_bytes(b"")
        self.refused([self.folder.decisions, "nope"])
        self.assertFalse(self.lock.exists())

    def test_a_lock_held_for_a_moment_is_waited_for(self):
        # Another mark, or a waiter's look at whether the inbox took its rounds in, holds it briefly.
        (self.home / "inbox.lock").write_bytes(b"")
        fd = signoff.try_lock(self.lock)
        timer = threading.Timer(0.2, signoff.unlock, (fd,))
        timer.start()
        self.addCleanup(timer.join)
        self.assertEqual(self.mark(self.folder.decisions, "layout", "n"), (0, "", ""))
        self.assertEqual(self.saved()["answers"]["layout"]["source"], "elsewhere")

    def test_a_lock_held_by_a_page_run_is_exit_1_and_writes_nothing(self):
        (self.home / "inbox.lock").write_bytes(b"")  # an inbox has run, and none runs now: the lock is a page run's
        self.held.append(signoff.try_lock(self.lock))
        err = self.refused([self.folder.decisions, "layout", "n"], words=("decisions.json",))
        self.assertIn("run", err)
        self.assertNotIn("inbox stop", err)

    def test_a_page_run_that_holds_the_lock_keeps_its_done(self):
        (self.home / "inbox.lock").write_bytes(b"")
        proc = start(self.folder, env=dict(os.environ, SIGNOFF_INBOX_HOME=str(self.home)))
        url = url_of(proc)
        self.refused([self.folder.decisions, "layout", "n"])
        self.assertEqual(post(url, {"answers": {}, "free_text": "", "done": True, "rev": 0}), 200)
        out, err = Cli.finish(self, proc)
        self.assertEqual((proc.returncode, out), (0, str(self.folder.answers) + "\n"), err)
        self.assertTrue(self.saved()["done"])

    @unittest.skipIf(sys.platform == "win32", "flock: the inbox's lock, on macOS and Linux")
    def test_a_file_system_that_cannot_lock_writes_as_before_locks(self):
        # flock's own refusal on a regular lock file, as on a network mount: the one case that goes on without it.
        import fcntl
        (self.home / "inbox.lock").write_bytes(b"")
        for code_ in (errno.ENOLCK, errno.EOPNOTSUPP):
            with self.subTest(code_), mock.patch.object(fcntl, "flock", side_effect=OSError(code_, "no locks")):
                code, out, err = self.mark(self.folder.decisions, "layout", "n%d" % code_)
            self.assertEqual((code, out, err.count("\n")), (0, "", 1), err)
            self.assertIn("without the lock", err)
            self.assertEqual(self.saved()["answers"]["layout"]["comment"], "n%d" % code_)

    @unittest.skipIf(sys.platform == "win32", "flock: the inbox's lock, on macOS and Linux")
    def test_a_lock_that_is_no_regular_file_or_cannot_be_opened_is_exit_1_and_writes_nothing(self):
        # Something planted at the lock's name must not turn the lock off: a FIFO makes flock fail with ENOTSUP and
        # a socket makes open fail with EOPNOTSUPP on macOS, errors a file system without locks gives too.
        import fcntl
        (self.home / "inbox.lock").write_bytes(b"")

        def fifo():
            os.mkfifo(str(self.lock))

        def sock():
            s = socket.socket(socket.AF_UNIX)
            self.addCleanup(s.close)
            s.bind(str(self.lock))

        def link():
            os.symlink(str(self.folder.path / "elsewhere.lock"), str(self.lock))

        def closed():
            self.lock.write_bytes(b"")
            os.chmod(str(self.lock), 0o400)  # open for writing is refused
        for label, plant in (("a FIFO", fifo), ("a socket", sock), ("a link", link), ("no permission", closed)):
            with self.subTest(label):
                plant()
                self.refused([self.folder.decisions, "layout", "n"], words=("lock",))
                self.assertFalse(self.folder.answers.exists())
                # Out of the way for the next one, kept in the test's own folder.
                os.rename(str(self.lock), str(self.folder.path / ("planted-" + label.replace(" ", "-"))))
        self.lock.write_bytes(b"")  # a regular lock file, as a run leaves one
        with mock.patch.object(fcntl, "flock", side_effect=OSError(errno.EIO, "Input/output error")):
            self.refused([self.folder.decisions, "layout", "n"], words=("lock",))  # flock failing otherwise
        self.assertFalse(self.folder.answers.exists())


class OldPython(unittest.TestCase):
    """On a Python older than 3.9 Signoff says so in one line, instead of failing somewhere later."""

    def guarded(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "version_info", (3, 8, 20, "final", 0)), mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err):
            code = signoff.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_a_run_says_which_python_it_needs(self):
        for argv in (["somewhere"], ["--history", "."], ["inbox", "open"], ["inbox", "done", "0" * 16],
                     ["elsewhere", "round-01.json", "q", "note"], ["inbox", "monitor"]):
            self.assertEqual(self.guarded(argv), (1, "", "signoff: needs Python 3.9 or newer; this is Python 3.8\n"),
                             argv)

    def test_the_hook_stays_silent(self):
        # A hook always exits 0 and prints nothing.
        self.assertEqual(self.guarded(["inbox", "hook"]), (0, "", ""))

    def test_monitor_is_a_usage_error(self):
        # The plugin has no monitor: one an older plugin started that runs this file gets a usage error, as any
        # unknown word does.
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err), \
                mock.patch.object(signoff, "inbox_home", side_effect=AssertionError("looked at the home")):
            with self.assertRaises(SystemExit) as stop:
                signoff.main(["inbox", "monitor"])
        self.assertEqual((stop.exception.code, out.getvalue()), (1, ""))
        self.assertTrue(err.getvalue().endswith("signoff: unrecognized arguments: monitor\n"), err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

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

    SSH = ({"SSH_CONNECTION": "192.0.2.1 50000 192.0.2.2 22"}, {"SSH_TTY": "/dev/ttys001"})

    def test_over_ssh_a_run_opens_no_browser(self):
        # The browser would open on the remote machine, where the user is not: as with --no-open.
        for env in self.SSH:
            err = io.StringIO()
            with mock.patch.dict(os.environ, env), mock.patch.object(signoff.webbrowser, "open") as browser, \
                    mock.patch.object(sys, "stderr", err):
                code = signoff.main([str(self.folder.target), "--timeout", "0.002"])
            browser.assert_not_called()
            self.assertEqual(code, 2)
            self.assertIn("signoff: open http://127.0.0.1:", err.getvalue())
            # The skills key on this line to hand the user the link.
            self.assertIn("signoff: no browser opened; give the user the link above (it works once)", err.getvalue())

    def test_empty_ssh_variables_are_not_ssh(self):
        with mock.patch.dict(os.environ, {"SSH_CONNECTION": "", "SSH_TTY": ""}):
            code, err = self.run_main(True)  # the browser is asked once
        self.assertEqual(code, 2)

    def test_over_ssh_a_set_browser_still_opens(self):
        # VS Code's Remote-SSH terminal sets BROWSER to a helper that opens the link on the user's own computer.
        for env in self.SSH:
            with mock.patch.dict(os.environ, dict(env, BROWSER="code-helper")):
                code, err = self.run_main(True)  # the browser is asked once
            self.assertEqual(code, 2)
            self.assertNotIn("no browser", err)
        with mock.patch.dict(os.environ, dict(self.SSH[0], BROWSER="")):  # empty: as unset
            self.test_over_ssh_a_run_opens_no_browser()

    def inbox_open(self, env):
        link = "http://127.0.0.1:1/b/%s/" % ("a" * 43)
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, dict(env, SIGNOFF_INBOX_HOME=tmp)), \
                mock.patch.object(signoff, "inbox_alive", return_value=True), \
                mock.patch.object(signoff, "inbox_request", return_value={"link": link}), \
                mock.patch.object(signoff.webbrowser, "open", return_value=True) as browser, \
                mock.patch.object(sys, "stderr", err):
            self.assertEqual(signoff.main(["inbox", "open"]), 0)
        return link, browser, err.getvalue()

    @unittest.skipIf(sys.platform == "win32", "no inbox on Windows")
    def test_over_ssh_inbox_open_prints_the_link(self):
        for env in self.SSH + (dict(self.SSH[0], BROWSER=""),):
            link, browser, err = self.inbox_open(env)
            browser.assert_not_called()
            self.assertIn("no browser opened; open %s yourself (it works once)" % link, err)
        # Empty SSH variables are no SSH, and a set BROWSER wins over SSH: the browser is asked.
        for env in ({"SSH_CONNECTION": "", "SSH_TTY": ""}, dict(self.SSH[1], BROWSER="code-helper")):
            link, browser, err = self.inbox_open(env)
            browser.assert_called_once()
            self.assertIn("the inbox is open in your browser", err)


class FixedPort(unittest.TestCase):
    """--port and SIGNOFF_PORT, in process: the flag wins, 0 or unset is a random port, a bad value is a usage
    error with nothing on stdout. The contract suite checks the same against a separate process."""
    # Below 1024 too: browsers leave :80 out of Host and Origin, and the low ports are the system's.
    BAD = ("-1", "65536", "x", "1.5", " 80", "+80", "8_080", "٨٠", "0x50", "9" * 30, "1", "80", "443", "1023")

    def setUp(self):
        self.folder = Folder()

    def tearDown(self):
        self.folder.cleanup()

    def main(self, args, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err), \
                mock.patch.object(signoff.webbrowser, "open", side_effect=AssertionError("a browser")):
            try:
                code = signoff.main([str(self.folder.target), "--no-open", "--timeout", "0.002"] + args)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def port_of(self, err):
        found = re.search(r"http://127\.0\.0\.1:([0-9]+)/b/", err)
        self.assertTrue(found, err)
        return int(found.group(1))

    def test_the_flag_the_variable_and_the_flag_winning(self):
        a, b = free_port(), free_port()
        while b == a:
            b = free_port()
        for args, env, want in ((["--port", str(a)], None, a), ([], {"SIGNOFF_PORT": str(b)}, b),
                                (["--port", str(a)], {"SIGNOFF_PORT": str(b)}, a)):
            code, out, err = self.main(args, env)
            self.assertEqual(code, signoff.EXIT_TIMEOUT, err)
            self.assertEqual(self.port_of(err), want, (args, env))

    def test_zero_unset_or_empty_is_a_random_port(self):
        for args, env in ((["--port", "0"], None), ([], {"SIGNOFF_PORT": "0"}), ([], {"SIGNOFF_PORT": ""}),
                          (["--port", "0"], {"SIGNOFF_PORT": "x"})):
            code, out, err = self.main(args, env)
            self.assertEqual(code, signoff.EXIT_TIMEOUT, (args, env, err))
            self.assertGreater(self.port_of(err), 0)

    def test_bad_values_are_usage_errors(self):
        for value in self.BAD:
            code, out, err = self.main(["--port", value])
            self.assertEqual((code, out), (1, ""), (value, err))
            self.assertEqual(err.strip().splitlines(), ["signoff: --port must be a port number from 1024 to 65535, "
                                                        "or 0 for a random one"], value)
            code, out, err = self.main([], {"SIGNOFF_PORT": value})
            self.assertEqual((code, out), (1, ""), (value, err))
            self.assertEqual(err.strip().splitlines(), ["signoff: SIGNOFF_PORT must be a port number from 1024 to "
                                                        "65535, or 0 for a random one"], value)
        self.assertFalse(self.folder.answers.exists())


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
