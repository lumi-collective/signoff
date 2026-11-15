"""Attachments: text files an item lists, read at start and sent with the page as plain text. No route serves them."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from support import Folder, Rounds, Running, png_bytes, sample_decisions, signoff
from test_extras import HistoryRoot, view_of, write

TEXT = "# Cut list\n\nKeep <b>bold</b> as text.\n- `legacy/sync_v1.py`\n"
KIB = 1024
# UNC (with a port, a device or a pipe) and a drive-relative path, in Windows syntax; refused on every OS.
NETWORK_AND_DRIVE = ("\\\\host\\share\\x.md", "//host/share/x.md", "\\\\host@SSL@443\\x.md", "C:foo.md",
                     "\\\\?\\C:\\x.md", "\\\\.\\pipe\\x.md", "\\\\host", "//host")


def page_size(text):
    return len(signoff._script_json(text).encode("utf-8"))


def with_attachment(src="notes/cut.md", data=None, **extra):
    data = data or sample_decisions()
    data["items"][1]["attachments"] = [dict({"src": src}, **extra)]
    return data


class AttachmentFile(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        self.addCleanup(self.folder.cleanup)
        (self.folder.path / "notes").mkdir()
        (self.folder.path / "notes" / "cut.md").write_bytes(TEXT.encode("utf-8"))
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        self.outside = Path(outside.name).resolve()
        (self.outside / "secret.md").write_text("SECRET", encoding="utf-8")

    def load(self, data):
        self.folder.write(data)
        return signoff.load_decisions(self.folder.decisions)[0]["items"][1]["attachments"]

    def broken(self, src=None, data=None):
        self.folder.write(data or with_attachment(src))
        with self.assertRaises(signoff.DecisionsError) as ctx:
            signoff.load_decisions(self.folder.decisions)
        msg = str(ctx.exception)
        self.assertNotIn(str(self.folder.path), msg)
        self.assertNotIn("SECRET", msg)
        return msg

    def note(self, name, data):
        (self.folder.path / "notes" / name).write_bytes(data)

    def test_text_is_read(self):
        got = self.load(with_attachment(caption="Cut list"))
        self.assertEqual(got, [{"src": "notes/cut.md", "caption": "Cut list", "text": TEXT}])

    def test_old_items_have_none(self):
        self.assertNotIn("attachments", signoff.load_decisions(self.folder.decisions)[0]["items"][1])
        self.assertEqual(self.load(with_attachment())[0]["text"], TEXT)

    def test_keys(self):
        self.assertIn("unknown key", self.broken(data=with_attachment(html="x")))
        no_src = with_attachment(caption="x")
        del no_src["items"][1]["attachments"][0]["src"]
        self.assertIn("missing 'src'", self.broken(data=no_src))
        self.assertIn("string", self.broken(src=["notes/cut.md"]))
        self.assertIn("string", self.broken(data=with_attachment(caption=3)))
        data = sample_decisions()
        data["items"][1]["attachments"] = {"src": "notes/cut.md"}
        self.assertIn("list", self.broken(data=data))

    def test_outside_the_folder(self):
        self.assertIn("outside", self.broken("../" + self.outside.name + "/secret.md"))
        self.assertIn("outside", self.broken("notes/../../" + self.outside.name + "/secret.md"))

    def test_dotdot_that_stays_inside_is_fine(self):
        self.assertEqual(self.load(with_attachment("notes/../notes/cut.md"))[0]["text"], TEXT)

    def test_absolute_path(self):
        msg = self.broken(str(self.outside / "secret.md"))
        self.assertIn("relative", msg)
        self.assertNotIn(str(self.outside), msg)  # the page must not show a local path
        msg = self.broken(str(self.folder.path / "notes" / "cut.md"))  # even one inside the folder
        self.assertIn("relative", msg)
        self.assertIn("relative", self.broken("\\notes\\cut.md"))  # rooted, in Windows syntax

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_link_to_a_file_outside(self):
        (self.folder.path / "notes" / "link.md").symlink_to(self.outside / "secret.md")
        self.assertIn("outside", self.broken("notes/link.md"))

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_linked_folder_outside(self):
        (self.folder.path / "linked").symlink_to(self.outside, target_is_directory=True)
        self.assertIn("outside", self.broken("linked/secret.md"))

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_link_inside_the_folder_is_fine(self):
        (self.folder.path / "notes" / "alias.md").symlink_to(self.folder.path / "notes" / "cut.md")
        self.assertEqual(self.load(with_attachment("notes/alias.md"))[0]["text"], TEXT)

    def test_missing_file(self):
        self.assertIn("not found", self.broken("notes/none.md"))

    def test_folder_is_not_a_file(self):
        (self.folder.path / "notes" / "dir.md").mkdir()
        self.assertIn("not a file", self.broken("notes/dir.md"))

    def test_size_limit(self):
        self.note("big.txt", b"x" * (256 * KIB + 1))
        self.assertIn("256 KiB", self.broken("notes/big.txt"))
        self.note("full.txt", b"x" * (256 * KIB))
        self.assertEqual(len(self.load(with_attachment("notes/full.txt"))[0]["text"]), 256 * KIB)

    def test_round_limit(self):
        data = sample_decisions()
        data["items"][1]["attachments"] = []
        for n in range(9):  # 9 x 250 KiB is over 2 MiB
            self.note("part-%d.txt" % n, b"x" * (250 * KIB))
            data["items"][1]["attachments"].append({"src": "notes/part-%d.txt" % n})
        self.assertIn("2 MiB", self.broken(data=data))
        del data["items"][1]["attachments"][8]
        self.assertEqual(len(self.load(data)), 8)

    def test_must_be_utf8(self):
        self.note("latin.txt", "café".encode("latin-1"))
        self.assertIn("UTF-8", self.broken("notes/latin.txt"))

    def test_only_the_text_types(self):
        for name in ("page.html", "pic.svg", "run.js", "README", "x.diff.html", "x.patches"):
            self.note(name, b"text")
            self.assertIn(".md, .txt, .diff or .patch", self.broken("notes/" + name))
        for name in ("UPPER.TXT", "change.diff", "fix.patch", "OLD.DIFF"):
            self.note(name, b"text")
            self.assertEqual(self.load(with_attachment("notes/" + name))[0]["text"], "text", name)

    def test_a_diff_keeps_the_path_and_size_rules(self):
        (self.outside / "secret.diff").write_text("SECRET", encoding="utf-8")
        self.assertIn("outside", self.broken("../" + self.outside.name + "/secret.diff"))
        self.assertIn("relative", self.broken(str(self.folder.path / "notes" / "x.diff")))
        for src in ("\\\\host\\share\\x.diff", "//host/share/x.patch", "C:foo.diff"):
            self.assertIn("relative", self.broken(src), src)
        self.assertIn("control character", self.broken("notes/a\x01.diff"))
        self.note("big.diff", b"x" * (256 * KIB + 1))
        self.assertIn("256 KiB", self.broken("notes/big.diff"))
        self.assertIn("not found", self.broken("notes/none.patch"))
        if os.name != "nt":  # symlinks need extra rights on Windows
            (self.folder.path / "notes" / "out.patch").symlink_to(self.outside / "secret.diff")
            self.assertIn("outside", self.broken("notes/out.patch"))

    def test_open_is_an_optional_bool(self):
        self.assertNotIn("open", self.load(with_attachment())[0])  # an old round's page data stays as it was
        self.assertIs(self.load(with_attachment(open=True))[0]["open"], True)
        self.assertIs(self.load(with_attachment(open=False))[0]["open"], False)
        for bad in ("true", 1, None, []):
            self.assertIn("'open' must be true or false", self.broken(data=with_attachment(open=bad)), bad)

    def test_item_lines_needs_an_attachment(self):
        data = with_attachment()
        data["items"][1]["lines"] = True
        self.folder.write(data)
        self.assertIs(signoff.load_decisions(self.folder.decisions)[0]["items"][1]["lines"], True)
        for attachments in (None, []):
            data = sample_decisions()
            data["items"][1]["lines"] = True
            if attachments is not None:
                data["items"][1]["attachments"] = attachments
            self.assertIn("'lines' needs at least one attachment", self.broken(data=data))
        data = sample_decisions()
        data["items"][1]["lines"] = False  # the default, said out loud
        self.folder.write(data)
        self.assertIs(signoff.load_decisions(self.folder.decisions)[0]["items"][1]["lines"], False)
        self.assertNotIn("lines", signoff.load_decisions(self.folder.decisions)[0]["items"][0])  # no default added
        for bad in ("yes", 1, None):
            data = with_attachment()
            data["items"][1]["lines"] = bad
            self.assertIn("'lines' must be true or false", self.broken(data=data), bad)

    def test_control_character_in_the_name(self):
        for src in ("notes/cut\x00.md", "notes/cut\x01.md", "notes/cut\n.md", "notes/cut\x7f.md"):
            self.assertIn("control character", self.broken(src))

    def test_network_and_drive_paths(self):
        # Refused as text before anything touches the disk: on Windows these would open a network connection.
        for src in NETWORK_AND_DRIVE:
            self.assertIn("relative", self.broken(src), src)

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_link_loop(self):
        (self.folder.path / "notes" / "loop.md").symlink_to("loop.md")
        self.broken("notes/loop.md")

    def test_at_most_20_an_item(self):
        data = sample_decisions()
        data["items"][1]["attachments"] = [{"src": "notes/cut.md"}] * 21
        self.assertIn("20", self.broken(data=data))
        data["items"][1]["attachments"] = [{"src": "notes/cut.md"}] * 20
        self.assertEqual(len(self.load(data)), 20)

    def test_byte_order_mark_is_dropped(self):
        self.note("bom.txt", b"\xef\xbb\xbfhello")
        self.assertEqual(self.load(with_attachment("notes/bom.txt"))[0]["text"], "hello")

    def test_round_limit_counts_the_page_size(self):
        # 2 x 200 KiB of "<" is 400 KiB on disk but 2.4 MiB once escaped into the page.
        data = sample_decisions()
        data["items"][1]["attachments"] = []
        for n in range(2):
            self.note("lt-%d.txt" % n, b"<" * (200 * KIB))
            data["items"][1]["attachments"].append({"src": "notes/lt-%d.txt" % n})
        self.assertIn("2 MiB", self.broken(data=data))

    def test_at_most_200_a_round(self):
        data = sample_decisions()
        base = data["items"][1]
        data["items"] = [dict(base, id="item-%d" % n, attachments=[{"src": "notes/cut.md"}] * 20) for n in range(11)]
        self.assertIn("200", self.broken(data=data))
        data["items"] = data["items"][:10]
        self.folder.write(data)
        self.assertEqual(len(signoff.load_decisions(self.folder.decisions)[0]["items"]), 10)

    def test_link_loop_while_reading_is_refused(self):
        # A link swapped into a parent folder after the open may loop when the read resolves it again.
        path = self.folder.path / "notes" / "cut.md"
        with mock.patch.object(Path, "resolve", side_effect=RuntimeError("Symlink loop")):
            with self.assertRaises(signoff.DecisionsError):
                signoff.read_image(path, signoff.MAX_ATTACHMENT)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
    def test_a_fifo_never_blocks_the_read(self):
        # Swapped in after the checks, a FIFO with no writer would block the read for good.
        fifo = self.folder.path / "notes" / "pipe.md"
        os.mkfifo(str(fifo))
        result = []

        def read():
            try:
                signoff.read_image(fifo, signoff.MAX_ATTACHMENT)
                result.append("read")
            except signoff.DecisionsError:
                result.append("refused")
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        thread.join(5)
        self.assertEqual(result, ["refused"])


class AttachmentOnPage(unittest.TestCase):
    def setUp(self):
        self.folder = Folder(with_attachment(caption="Cut list"))
        self.addCleanup(self.folder.cleanup)
        (self.folder.path / "notes").mkdir()
        (self.folder.path / "notes" / "cut.md").write_bytes(TEXT.encode("utf-8"))

    def serve(self):
        run = Running(self.folder)
        self.addCleanup(run.stop)
        return run

    def test_text_goes_with_the_page(self):
        run = self.serve()
        view, body = view_of(run)
        items = view["rounds"][0]["decisions"]["items"]
        self.assertEqual(items[1]["attachments"], [{"src": "notes/cut.md", "caption": "Cut list", "text": TEXT}])
        self.assertNotIn("attachments", items[0])
        self.assertNotIn("<b>", body)  # escaped inside the data block

    def test_read_at_start(self):
        run = self.serve()
        (self.folder.path / "notes" / "cut.md").write_bytes(b"changed")
        self.assertEqual(view_of(run)[0]["rounds"][0]["decisions"]["items"][1]["attachments"][0]["text"], TEXT)

    def test_no_route_serves_it(self):
        run = self.serve()
        for path in ("notes/cut.md", "att/0", "attachment/0", "img/1"):
            self.assertEqual(run.request("GET", "/%s/%s" % (run.token, path))[0].status, 404, path)

    def test_markdown_names_it_only(self):
        decisions, _ = signoff.load_decisions(self.folder.decisions)
        record = {"answers": {}, "free_text": "", "done": False, "saved": "2026-01-01T12:00:00Z"}
        md = signoff.answers_markdown(decisions, record)
        self.assertIn("- Attachment: Cut list (notes/cut.md)", md)
        self.assertNotIn("Keep", md)
        del decisions["items"][1]["attachments"][0]["caption"]
        self.assertIn("- Attachment: notes/cut.md\n", signoff.answers_markdown(decisions, record))

    def test_sensitive_item_shows_it_too(self):
        # The text is the agent's, not the user's; Private covers only what the user writes.
        data = with_attachment(caption="Cut list")
        data["items"][1]["sensitive"] = True
        self.folder.write(data)
        run = Running(self.folder)
        self.addCleanup(run.stop)
        self.assertEqual(view_of(run)[0]["rounds"][0]["decisions"]["items"][1]["attachments"][0]["text"], TEXT)

    def test_bad_attachment_stops_the_run(self):
        self.folder.write(with_attachment("notes/none.md"))
        self.assertEqual(signoff.main([str(self.folder.decisions), "--no-open"]), signoff.EXIT_ERROR)

    def test_limit_is_per_round(self):
        rounds = Rounds(2)
        self.addCleanup(rounds.cleanup)
        (rounds.path / "notes").mkdir()
        for n in (1, 2):
            data = sample_decisions()
            data["items"][1]["attachments"] = []
            for k in range(6):  # 1.5 MiB a round
                name = "notes/r%d-%d.txt" % (n, k)
                (rounds.path / name).write_bytes(b"x" * (250 * KIB))
                data["items"][1]["attachments"].append({"src": name})
            rounds.write(n, data)
        server = signoff.make_server(rounds.path)
        server.server_close()


class AttachmentInHistory(unittest.TestCase):
    def setUp(self):
        self.root = HistoryRoot()
        self.addCleanup(self.root.cleanup)
        self.topic = self.root.path / ".signoff" / "2026-03-04-cut"
        write(self.topic / "notes" / "cut.md", TEXT)

    def entry(self, src, budget=signoff.HISTORY_BUDGET):
        if src is not None:  # None: the test wrote round-01.json itself
            write(self.topic / "round-01.json", with_attachment(src))
        server = signoff.make_history_server(self.root.path, budget=budget)
        self.addCleanup(server.server_close)
        e = [e for e in server.view()["entries"] if e["topic"] == "2026-03-04-cut"][0]
        text = json.dumps(e)
        self.assertNotIn(str(self.root.path), text)
        self.assertNotIn("SECRET", text)
        return e

    def shown(self, e):
        return e["decisions"]["items"][1]["attachments"][0]

    def test_text_is_read(self):
        e = self.entry("notes/cut.md")
        self.assertEqual((e["status"], e["warning"]), ("new", None))
        self.assertEqual(self.shown(e)["text"], TEXT)

    def test_read_problems_are_warnings(self):
        (self.topic / "notes" / "latin.txt").write_bytes("café".encode("latin-1"))
        (self.topic / "notes" / "big.txt").write_bytes(b"x" * (256 * KIB + 1))
        (self.topic / "notes" / "dir.md").mkdir()
        for src, why in (("notes/latin.txt", "UTF-8"), ("notes/big.txt", "256 KiB"), ("notes/none.md", "not found"),
                         ("notes/dir.md", "not a file")):
            e = self.entry(src)
            self.assertEqual(e["status"], "new", src)
            self.assertNotIn("text", self.shown(e))
            self.assertIn(why, self.shown(e)["missing"])
            self.assertIn(src, e["warning"])

    def test_path_problems_are_errors(self):
        outside = self.root.path / "elsewhere"
        write(outside / "secret.md", "SECRET")
        for src in ("../../elsewhere/secret.md", str(outside / "secret.md"), "notes/page.html", "notes/a\x00.md") \
                + NETWORK_AND_DRIVE:
            e = self.entry(src)
            self.assertEqual(e["status"], "error", src)
            self.assertIsNone(e["decisions"])

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_links_out_are_errors(self):
        outside = self.root.path / "elsewhere"
        write(outside / "secret.md", "SECRET")
        (self.topic / "notes" / "link.md").symlink_to(outside / "secret.md")
        (self.topic / "linked").symlink_to(outside, target_is_directory=True)
        for src in ("notes/link.md", "linked/secret.md"):
            e = self.entry(src)
            self.assertEqual(e["status"], "error", src)
            self.assertIn("outside", e["error"])

    def test_budget(self):
        # Room for the first copy and a few bytes: the second must not fit, so the first must have been counted.
        data = with_attachment("notes/cut.md")
        data["items"][1]["attachments"].append({"src": "notes/cut.md", "caption": "again"})
        write(self.topic / "round-01.json", data)
        with mock.patch.object(signoff, "HISTORY_TEXT_BUDGET", page_size(TEXT) + 5):
            e = self.entry(None)
        first, second = e["decisions"]["items"][1]["attachments"]
        self.assertEqual(first["text"], TEXT)
        self.assertNotIn("text", second)
        self.assertIn("--since", second["missing"])
        self.assertIn("Attachment 'notes/cut.md' not shown: " + second["missing"], e["warning"])

    def test_budget_counts_the_page_size(self):
        write(self.topic / "notes" / "lt.md", "<" * 1000)  # 1000 bytes on disk, 6000 in the page
        with mock.patch.object(signoff, "HISTORY_TEXT_BUDGET", 3000):
            e = self.entry("notes/lt.md")
        self.assertIn("--since", self.shown(e)["missing"])

    def test_budget_is_not_the_image_budget(self):
        self.assertEqual(self.shown(self.entry("notes/cut.md", budget=10))["text"], TEXT)

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_link_loop_leaves_the_page_up(self):
        (self.topic / "notes" / "loop.md").symlink_to("loop.md")
        e = self.entry("notes/loop.md")
        self.assertTrue(e["status"] == "error" or "missing" in self.shown(e))

    def test_absolute_picture_inside_the_folder_shows_as_before(self):
        (self.topic / "a.png").write_bytes(png_bytes())
        data = sample_decisions()
        data["items"][0]["images"][0]["src"] = str(self.topic.resolve() / "a.png")
        write(self.topic / "round-01.json", data)
        e = self.entry(None)
        self.assertEqual((e["status"], e["error"]), ("new", None))
        self.assertIn("src", e["decisions"]["items"][0]["images"][0])

    def test_bad_picture_path_leaves_the_page_up(self):
        for src in ("a\x00.png",) + NETWORK_AND_DRIVE:
            data = sample_decisions()
            data["items"][0]["images"][0]["src"] = src
            write(self.topic / "round-01.json", data)
            e = self.entry(None)
            self.assertEqual(e["status"], "error", src)

    def test_line_notes_on_a_changed_file_still_show(self):
        # The past-decisions page reads the answers as stored: a note whose line changed since is kept, not checked.
        data = with_attachment("notes/cut.md")
        data["items"][1]["lines"] = True
        write(self.topic / "round-01.json", data)
        note = {"att": 0, "line": 99, "quote": "a line long gone", "comment": "was wrong"}
        write(self.topic / "round-01.answers.json", {"version": 1, "done": True, "saved": "2026-03-04T10:00:00Z",
                                                     "answers": {"filters": {"choice": ["date"], "lines": [note]}},
                                                     "free_text": ""})
        e = self.entry(None)
        self.assertEqual(e["status"], "done", e.get("error"))
        self.assertEqual(e["answers"]["answers"]["filters"]["lines"], [note])


LINE_RULE = json.loads((Path(__file__).resolve().parent / "line_rule.json").read_text(encoding="ascii"))


def line_note(line, quote=None, att=0, comment="look here"):
    return {"att": att, "line": line, "quote": LINE_RULE["quotes"][line - 1] if quote is None else quote,
            "comment": comment}


class LineRule(unittest.TestCase):
    """One rule for a line note's line and quote, pinned by tests/line_rule.json, which the page's test reads too:
    the text split on LF, one CR cut off each line, no line after a final newline, a quote cut to 300 code points."""

    def setUp(self):
        self.folder = Folder()
        self.addCleanup(self.folder.cleanup)
        (self.folder.path / "notes").mkdir()
        (self.folder.path / "notes" / "rule.diff").write_bytes(LINE_RULE["text"].encode("utf-8"))  # its BOM too
        (self.folder.path / "notes" / "cut.md").write_bytes(TEXT.encode("utf-8"))
        data = sample_decisions()
        data["items"][1]["attachments"] = [{"src": "notes/cut.md"}, {"src": "notes/rule.diff"}]
        data["items"][1]["lines"] = True
        self.folder.write(data)
        self.decisions = signoff.load_decisions(self.folder.decisions)[0]

    def check(self, notes, previous=None):
        state = {"answers": {"filters": {"choice": ["date"], "comment": "", "lines": notes}}, "free_text": "",
                 "done": False}
        return signoff.check_answers(state, self.decisions, previous=previous)

    def bad(self, notes, previous=None):
        with self.assertRaises(ValueError) as ctx:
            self.check(notes, previous)
        return str(ctx.exception)

    def test_the_fixture_holds_every_case(self):
        text = LINE_RULE["text"]
        self.assertTrue(text.startswith("﻿"))
        self.assertIn("\r\n", text)
        self.assertIn(" ", text)
        self.assertRegex(text, "\r[^\n]")
        self.assertFalse(text.endswith("\n"))
        astral = [q for q in LINE_RULE["quotes"] if len(q) == 300]
        self.assertEqual(len(astral), 1)
        self.assertGreater(ord(astral[0][-1]), 0xFFFF)  # a UTF-16 cut would split it

    def test_every_line_of_the_fixture_matches_and_nothing_past_it(self):
        lines, quotes = LINE_RULE["lines"], LINE_RULE["quotes"]
        self.assertEqual(signoff.attachment_lines(self.decisions["items"][1]["attachments"][1]["text"]), lines)
        self.assertEqual([line[:signoff.MAX_QUOTE] for line in lines], quotes)
        notes = [line_note(n, att=1) for n in range(1, len(lines) + 1)]
        self.assertEqual(self.check(notes)["answers"]["filters"]["lines"], notes)
        self.assertIn("line note 1 ", self.bad([line_note(len(lines) + 1, quote="", att=1)]))
        for n, line in enumerate(lines, 1):
            if line != quotes[n - 1]:  # the long line: the whole of it is not its quote
                self.bad([line_note(n, quote=line, att=1)])
                self.bad([line_note(n, quote=line[:299], att=1)])
            self.bad([line_note(n, quote=quotes[n - 1] + "x", att=1)])
        self.bad([line_note(1, quote="﻿first", att=1)])  # the BOM is not in the text
        self.bad([line_note(2, quote="lone", att=1)])  # a lone CR ends no line
        self.bad([line_note(5, quote="trail", att=1)])  # only one CR is cut off

    def test_a_note_names_its_attachment_by_index(self):
        self.assertEqual(len(self.check([line_note(1, quote="# Cut list", att=0)])["answers"]["filters"]["lines"]), 1)
        self.bad([line_note(1, quote="# Cut list", att=1)])
        for att in (2, -1, True, 0.0, "0", None):
            self.assertIn("line note 1 must be", self.bad([line_note(1, quote="# Cut list", att=att)]), att)

    def test_a_note_is_exactly_four_fields(self):
        good = line_note(1, att=1)
        for bad in (dict(good, line=0), dict(good, line=True), dict(good, line=1.0), dict(good, line="1"),
                    dict(good, quote=None), dict(good, quote="x" * 301), dict(good, comment=""),
                    dict(good, comment=None), dict(good, comment="x" * (signoff.MAX_TEXT + 1)),
                    dict(good, side="new"), {k: v for k, v in good.items() if k != "quote"}, [good], "x"):
            self.assertIn("line note 1 must be", self.bad([bad]), bad)
        self.assertIn("list of up to 50", self.bad(good))
        self.assertEqual(self.check([dict(good, comment="x" * signoff.MAX_TEXT)])["answers"]["filters"]["lines"][0]
                         ["comment"], "x" * signoff.MAX_TEXT)

    def test_at_most_50(self):
        self.assertEqual(len(self.check([line_note(1, att=1)] * 50)["answers"]["filters"]["lines"]), 50)
        self.assertIn("list of up to 50", self.bad([line_note(1, att=1)] * 51))

    def test_the_error_names_the_position_only(self):
        msg = self.bad([line_note(1, att=1), line_note(2, quote="SECRET QUOTE", att=1, comment="SECRET NOTE")])
        self.assertIn("line note 2 ", msg)
        self.assertNotIn("SECRET", msg)
        msg = self.bad([line_note(1, att=1), dict(line_note(2, att=1), comment=7, extra="SECRET")])
        self.assertNotIn("SECRET", msg)

    def test_only_on_an_item_with_lines(self):
        state = {"answers": {"layout": {"choice": "two", "comment": "", "lines": []}}, "free_text": "", "done": False}
        with self.assertRaises(ValueError) as ctx:
            signoff.check_answers(state, self.decisions)
        self.assertIn("takes no line notes", str(ctx.exception))
        self.assertEqual(self.check([])["answers"]["filters"]["lines"], [])

    def test_an_unchanged_note_is_not_checked_again(self):
        stale = line_note(3, quote="what line 3 said before", att=1)
        previous = {"answers": {"filters": {"choice": ["date"], "comment": "", "lines": [stale]}}}
        self.assertEqual(self.check([stale, line_note(1, att=1)], previous)["answers"]["filters"]["lines"][0], stale)
        self.assertIn("line note 1 ", self.bad([dict(stale, comment="changed")], previous))
        self.assertIn("line note 1 ", self.bad([dict(stale, line=4)], previous))
        # The same note on another item of the stored answers is not this item's.
        self.bad([stale], {"answers": {"layout": {"choice": "two", "lines": [stale]}}})
        self.bad([stale])

    def test_a_note_is_the_users_own_and_sensitive_acts_on_an_item_with_lines(self):
        item = self.decisions["items"][1]  # comment false, no other, no marks: lines is its only text field
        self.assertFalse(signoff._private_default(dict(item, sensitive=True, lines=False)))
        self.assertTrue(signoff._private_default(dict(item, sensitive=True)))
        self.assertFalse(signoff._private_default(item))
        self.assertFalse(signoff._users({"choice": [], "source": "recommended", "lines": []}, item))
        self.assertTrue(signoff._users({"choice": [], "source": "recommended", "lines": [line_note(1, att=1)]}, item))

    def test_a_stored_read_checks_the_shape_only(self):
        stale = line_note(99, quote="gone", att=1)
        stored = {"version": 1, "saved": "2026-01-01T12:00:00Z", "done": False, "free_text": "",
                  "answers": {"filters": {"choice": ["date"], "lines": [stale]}}}
        self.assertEqual(signoff.check_answers(stored, self.decisions, stored=True)["answers"]["filters"]["lines"],
                         [stale])
        stored["answers"]["filters"]["lines"] = [dict(stale, att=2)]  # an index past the item's attachments
        with self.assertRaises(ValueError):
            signoff.check_answers(stored, self.decisions, stored=True)


if __name__ == "__main__":
    unittest.main()
