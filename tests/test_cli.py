"""The command line, run as a real process: exit codes, stdout, the .gitignore."""
import hashlib
import io
import json
import os
import shutil
import signal
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

from support import ROOT, Folder, Rounds, done_record, png_bytes, sample_decisions, signoff, write_outside

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

    @unittest.skipIf(sys.platform == "win32", "the inbox does not run on Windows")
    def test_a_waiter_on_a_real_0_4_0_inbox_that_refuses_its_round_exits_1(self):
        # The inbox the user started before updating keeps running: 0.4.0's own code, from git, refuses `commands`.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = Path(tmp.name)
        for name in ("signoff.py", "page.html", "schema.json"):
            try:
                found = subprocess.run(["git", "-C", str(ROOT), "show", "v0.4.0:plugin/signoff/" + name],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
            except (OSError, subprocess.SubprocessError):
                found = None
            if found is None or found.returncode:
                self.skipTest("needs git and the v0.4.0 tag (a shallow clone has neither)")
            (old / name).write_bytes(found.stdout)
        data = json.loads(self.rounds.round(1).read_text(encoding="utf-8"))
        data["items"][1]["commands"] = [{"text": "ls -l"}]
        self.rounds.write(1, data)
        inbox = track(subprocess.Popen([sys.executable, "-I", "-B", str(old / "signoff.py"), "inbox", "serve"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, universal_newlines=True,
                                       encoding="utf-8", env=self.env))
        deadline = time.monotonic() + 30
        while not signoff.inbox_alive(self.home):
            self.assertIsNone(inbox.poll(), "the 0.4.0 inbox did not start")
            self.assertLess(time.monotonic(), deadline, "the 0.4.0 inbox did not start")
            time.sleep(0.1)
        began = time.monotonic()
        self.refused(self.start(), began)
        signoff.inbox_request(self.home, "stop")
        self.assertIn("unknown key 'commands'", inbox.communicate(timeout=20)[1])

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
