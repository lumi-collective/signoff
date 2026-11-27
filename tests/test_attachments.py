"""Attachments: text files an item lists, read at start and sent with the page as plain text (a .md one with its
tree too). No route serves them."""
import json
import os
import re
import tempfile
import time
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


def page_size(text, depth=0):
    """Bytes in the page's indented data, with every line but the first indented depth more."""
    data = signoff._script_json(text)
    return len(data.encode("utf-8")) + depth * data.count("\n")


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
        self.assertEqual(items[1]["attachments"], [{"src": "notes/cut.md", "caption": "Cut list", "text": TEXT,
                                                    "blocks": signoff.markdown_blocks(TEXT, marks=False)}])
        self.assertNotIn("attachments", items[0])
        self.assertNotIn("<b>", body)  # escaped inside the data block

    def test_only_markdown_gets_a_tree(self):
        for name in ("cut.txt", "cut.diff", "cut.patch", "cut.MD"):
            (self.folder.path / "notes" / name).write_bytes(TEXT.encode("utf-8"))
        data = with_attachment()
        data["items"][1]["attachments"] = [{"src": "notes/" + n} for n in ("cut.txt", "cut.diff", "cut.patch",
                                                                             "cut.MD", "cut.md")]
        self.folder.write(data)
        shown = view_of(self.serve())[0]["rounds"][0]["decisions"]["items"][1]["attachments"]
        self.assertEqual([("blocks" in a, a["text"]) for a in shown],
                         [(False, TEXT), (False, TEXT), (False, TEXT), (True, TEXT), (True, TEXT)])

    def test_the_tree_marks_no_wait_and_has_heading_levels(self):
        (self.folder.path / "notes" / "cut.md").write_bytes(b"## Plan\n\n- [ ] (waiting on you: run it) Run it\n")
        blocks = view_of(self.serve())[0]["rounds"][0]["decisions"]["items"][1]["attachments"][0]["blocks"]
        self.assertEqual(blocks[0], {"t": "h", "level": 2, "c": [{"t": "text", "v": "Plan"}]})
        self.assertNotIn("wait", blocks[1]["items"][0])

    def test_the_tree_is_built_once_at_the_first_page(self):
        with mock.patch.object(signoff, "markdown_blocks", wraps=signoff.markdown_blocks) as built:
            run = self.serve()
            self.assertEqual(built.call_count, 0)  # not at start: a run whose page is never opened pays nothing
            first = view_of(run)[0]
            second = view_of(run)[0]
        self.assertEqual(built.call_count, 1)
        self.assertEqual(first["rounds"][0]["decisions"]["items"][1]["attachments"],
                         second["rounds"][0]["decisions"]["items"][1]["attachments"])

    def two_trees(self, **limits):
        (self.folder.path / "notes" / "two.md").write_bytes(TEXT.encode("utf-8"))
        data = with_attachment()
        data["items"][1]["attachments"].append({"src": "notes/two.md"})
        self.folder.write(data)
        with mock.patch.multiple(signoff, **limits):
            view, body = view_of(self.serve())
        return view["rounds"][0]["decisions"]["items"][1]["attachments"], body

    def test_the_parse_budget_is_per_page(self):
        size = len(TEXT.encode("utf-8"))
        for limit, has in ((2 * size, [True, True]), (2 * size - 1, [True, False]), (size - 1, [False, False])):
            shown = self.two_trees(MAX_MD_INPUT=limit)[0]
            self.assertEqual([("blocks" in a, a["text"]) for a in shown], [(h, TEXT) for h in has], limit)

    def test_the_parse_budget_spans_the_rounds(self):
        rounds = Rounds(2)
        self.addCleanup(rounds.cleanup)
        (rounds.path / "notes").mkdir()
        (rounds.path / "notes" / "cut.md").write_bytes(TEXT.encode("utf-8"))
        for n in (1, 2):
            rounds.write(n, with_attachment())
        with mock.patch.object(signoff, "MAX_MD_INPUT", 2 * len(TEXT.encode("utf-8")) - 1):
            server = signoff.make_server(rounds.path)
            self.addCleanup(server.server_close)
            view = server.view()
        self.assertEqual(["blocks" in r["decisions"]["items"][1]["attachments"][0] for r in view["rounds"]],
                         [True, False])

    def test_the_tree_budget_spans_the_rounds(self):
        rounds = Rounds(2)
        self.addCleanup(rounds.cleanup)
        (rounds.path / "notes").mkdir()
        (rounds.path / "notes" / "cut.md").write_bytes(TEXT.encode("utf-8"))
        for n in (1, 2):
            rounds.write(n, with_attachment())
        one = page_size(signoff.markdown_blocks(TEXT, marks=False), 8)
        for limit, has in ((2 * one, [True, True]), (2 * one - 1, [True, False])):
            with mock.patch.object(signoff, "MAX_MD_TREES", limit):
                server = signoff.make_server(rounds.path)
                view = server.view()
                server.server_close()
            self.assertEqual(["blocks" in r["decisions"]["items"][1]["attachments"][0] for r in view["rounds"]], has)

    def test_what_a_tree_let_go_made_is_charged(self):
        # A hostile file's tree, given up past the room, spends the build's room: the next file gets none.
        deep = ("+ " * signoff.MD_DEPTH + "x\n") * 15000  # 210 KB, under MAX_ATTACHMENT
        (self.folder.path / "notes" / "deep.md").write_bytes(deep.encode("utf-8"))
        one = page_size(signoff.markdown_blocks(TEXT, marks=False), 8)
        for first, has in (("notes/cut.md", [True, False]), ("notes/deep.md", [False, False])):
            data = with_attachment(first)
            data["items"][1]["attachments"].append({"src": "notes/cut.md" if first.endswith("deep.md")
                                                    else "notes/deep.md"})
            self.folder.write(data)
            with mock.patch.object(signoff, "MAX_MD_TREES", 100 * one):
                shown = view_of(self.serve())[0]["rounds"][0]["decisions"]["items"][1]["attachments"]
            self.assertEqual(["blocks" in a for a in shown], has, first)

    def test_a_tree_let_go_is_charged_too(self):
        # A parse that gives its tree up (None) still spends its text: the second file is not parsed.
        size = len(TEXT.encode("utf-8"))
        with mock.patch.object(signoff, "markdown_blocks", return_value=None) as built:
            shown = self.two_trees(MAX_MD_INPUT=2 * size - 1)[0]
        self.assertEqual((built.call_count, ["blocks" in a for a in shown]), (1, [False, False]))

    def test_the_tree_budget_counts_the_trees_as_sent(self):
        data = re.compile(r'id="signoff-data">(.*?)</script>', re.S)
        bare = len(data.search(self.two_trees(MAX_MD_TREES=0)[1]).group(1).encode("utf-8"))
        full = len(data.search(self.two_trees(MAX_MD_TREES=signoff.MAX_MD_TREES)[1]).group(1).encode("utf-8"))
        # Each tree goes in after its "text" as ',\n' + the attachment's indentation + '"blocks": '.
        key = len(',\n' + " " * 8 + '"blocks": ')
        one = (full - bare) // 2 - key
        self.assertEqual(full - bare, 2 * (one + key))
        for limit, has in ((2 * one, [True, True]), (2 * one - 1, [True, False]), (one - 1, [False, False])):
            shown = self.two_trees(MAX_MD_TREES=limit)[0]
            self.assertEqual(["blocks" in a for a in shown], has, limit)

    def test_the_indentation_is_counted_exactly(self):
        demo = (Path(__file__).resolve().parent.parent / "examples" / "demo" / "notes" / "export-formats.md")
        values = [signoff.markdown_blocks(t, marks=False) for t in (TEXT, demo.read_text(encoding="utf-8"),
                  "> [!NOTE]\n> a\n\n| a | b |\n|---|---|\n| `x` |\n\n- [ ] t\n  1. n\n\n```sh\nls\n```\n---\n")]
        values += [[], {}, [[], {}, {"a": []}], "x", [None, True, 1]]
        for value in values:
            for depth in (0, 1, 7, 8):
                compact = len(signoff._script_json(value, compact=True).encode("utf-8"))
                exact = page_size(value, depth) - compact
                self.assertEqual(signoff._indent_bytes(value, depth, exact), exact)
                self.assertIsNone(signoff._indent_bytes(value, depth, exact - 1) if exact else None)

    def test_a_full_hostile_round_builds_in_time(self):
        """The trees are built on the page's request thread; no round of agent text may hold it up."""
        line_kinds = ["```" + "a" * 30000 + "`", "[" * 30000, "".join("`" * k + "x" for k in range(1, 240)),
                      "|" + " " * 30000 + "x", "- [ ] " + "[" * 30000, "- [ ] " + "`" * 3 + "a" * 30000 + "`"]
        size = signoff.MAX_ATTACHMENT
        shapes = ["\n".join(line_kinds * 3)[:size],  # the todo reader's hostile lines
                  (("+ " * signoff.MD_DEPTH + "x\n") * size)[:size],  # the slowest shape measured: deep lists
                  ("x\n\n" * size)[:size], ("- [ ] a\n" * size)[:size], (("`a` " * 60 + "\n") * size)[:size]]
        data = sample_decisions()
        data["items"][1]["attachments"] = []
        for k in range(8):  # all the round's attachments take: 2 MiB in the page
            name, text = "notes/h%d.md" % k, shapes[k % len(shapes)]
            while page_size(text) > signoff.MAX_ATTACHMENTS // 8:
                text = text[:len(text) * (signoff.MAX_ATTACHMENTS // 8) // page_size(text) - 8]
            (self.folder.path / name).write_bytes(text.encode("utf-8"))
            data["items"][1]["attachments"].append({"src": name})
        self.folder.write(data)
        decisions = signoff.load_decisions(self.folder.decisions)[0]
        start = time.monotonic()
        trees = signoff._md_trees(decisions["items"], [signoff.MAX_MD_INPUT, signoff.MAX_MD_TREES])
        # Measured on an M4 Pro: under 1 s on Python 3.9 (see MAX_MD_INPUT); CI machines are slower.
        self.assertLess(time.monotonic() - start, 5.0)
        self.assertLessEqual(sum(sent for _, sent in trees.values()), signoff.MAX_MD_TREES)

    def test_a_tree_past_its_room_is_given_up_early(self):
        """One hostile file must not build its whole tree only to drop it: memory follows the room, not the file."""
        import tracemalloc
        deep = (("+ " * signoff.MD_DEPTH + "x\n") * signoff.MAX_ATTACHMENT)[:signoff.MAX_ATTACHMENT]
        tracemalloc.start()
        try:
            tree = signoff.markdown_blocks(deep, marks=False, room=[signoff.MAX_MD_TREES])
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertIsNone(tree)
        self.assertLess(peak, 40 * KIB * KIB)  # 100 MiB and more when built whole

    def test_the_room_never_drops_a_tree_that_fits(self):
        demo = (Path(__file__).resolve().parent.parent / "examples" / "demo" / "notes" / "export-formats.md")
        for text in (TEXT, demo.read_text(encoding="utf-8"), "\\*" * 3000, "**a** " * 500, "`a` " * 800,
                     "|" + "a|" * 64 + "\n|" + "-|" * 64 + "\n" + ("|" + "a|" * 64 + "\n") * 50,
                     "> [!NOTE]\n> a\n\n- [ ] t\n  1. n\n\n```sh\nls\n```\n---\n# h\n", "x\n\n" * 500,
                     ("+ " * signoff.MD_DEPTH + "x\n") * 200, "[a](https://example.com) " * 300, "<" * 2000):
            tree = signoff.markdown_blocks(text, marks=False)
            sent = page_size(tree, 8)  # as it sits in the page
            self.assertEqual(signoff.markdown_blocks(text, marks=False, room=[sent]), tree, text[:40])

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

    def test_a_markdown_file_gets_its_tree(self):
        self.assertEqual(self.shown(self.entry("notes/cut.md"))["blocks"], signoff.markdown_blocks(TEXT, marks=False))
        write(self.topic / "notes" / "cut.txt", TEXT)
        self.assertNotIn("blocks", self.shown(self.entry("notes/cut.txt")))

    def test_the_tree_counts_in_the_text_budget(self):
        tree = page_size(signoff.markdown_blocks(TEXT, marks=False), 8)  # entries > ... > attachment
        for budget, shown in ((page_size(TEXT) + tree, True), (page_size(TEXT) + tree - 1, False)):
            with mock.patch.object(signoff, "HISTORY_TEXT_BUDGET", budget):
                att = self.shown(self.entry("notes/cut.md"))
            self.assertEqual((att["text"], "blocks" in att), (TEXT, shown), budget)  # no tree: the text still shows
        # That is the tree's size as the page gets it.
        write(self.topic / "round-01.json", with_attachment("notes/cut.md"))
        sizes = []
        for limit in (signoff.MAX_MD_TREES, 0):
            with mock.patch.object(signoff, "MAX_MD_TREES", limit):
                server = signoff.make_history_server(self.root.path)
                sizes.append(len(signoff._script_json(server.view()).encode("utf-8")))
                server.server_close()
        self.assertEqual(sizes[0] - sizes[1], tree + len(',\n' + " " * 8 + '"blocks": '))

    def history(self, rounds):
        """Rounds of one .md each, newest first as the page lists them; returns their shown attachments."""
        for n in range(rounds):
            topic = self.root.path / ".signoff" / ("2026-03-%02d-plan" % (10 + n))
            write(topic / "notes" / "cut.md", TEXT)
            write(topic / "round-01.json", with_attachment("notes/cut.md"))
        server = signoff.make_history_server(self.root.path)
        self.addCleanup(server.server_close)
        return [self.shown(e) for e in server.view()["entries"] if e["topic"].endswith("-plan")]

    def test_every_text_comes_before_any_tree(self):
        # As in 0.5.0, the texts take the budget first: a newer round's tree never pushes an older round's text out.
        tree = page_size(signoff.markdown_blocks(TEXT, marks=False), 8)
        with mock.patch.object(signoff, "HISTORY_TEXT_BUDGET", 2 * page_size(TEXT) + tree - 1):
            newer, older = self.history(2)  # newest first: 03-11, then 03-10
        self.assertEqual((older["text"], newer["text"]), (TEXT, TEXT))
        self.assertNotIn("blocks", newer)
        with mock.patch.object(signoff, "HISTORY_TEXT_BUDGET", 2 * page_size(TEXT) + tree):
            shown = self.history(2)
        self.assertEqual(["blocks" in a for a in shown], [True, False])  # the room left goes to the newest

    def test_the_parse_budget_is_per_build(self):
        with mock.patch.object(signoff, "MAX_MD_INPUT", 2 * len(TEXT.encode("utf-8"))), \
                mock.patch.object(signoff, "markdown_blocks", wraps=signoff.markdown_blocks) as built:
            shown = self.history(3)
        self.assertEqual(["blocks" in a for a in shown], [True, True, False])  # the newest two
        self.assertEqual(built.call_count, 2)

    def test_the_round_budgets_hold_there_too(self):
        for limit in ({"MAX_MD_INPUT": len(TEXT) - 1}, {"MAX_MD_TREES": 10}):
            with mock.patch.multiple(signoff, **limit):
                att = self.shown(self.entry("notes/cut.md"))
            self.assertEqual((att["text"], "blocks" in att), (TEXT, False), limit)

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
    """One rule for a line note's line and quote, pinned by tests/line_rule.json; the page's count must match it:
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
        self.assertTrue(text.startswith(chr(0xFEFF)))
        self.assertIn("\r\n", text)
        self.assertIn(chr(0x2028), text)
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
        self.bad([line_note(1, quote=chr(0xFEFF) + "first", att=1)])  # the BOM is not in the text
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
                    dict(good, side="new"), {k: v for k, v in good.items() if k != "quote"}, [good], "x",
                    dict(good, line=signoff.MAX_ATTACHMENT + 1), dict(good, line=2 ** 53 + 1)):
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

    def test_a_stored_read_checks_the_shape_only(self):
        stale = line_note(99, quote="gone", att=1)
        stored = {"version": 1, "saved": "2026-01-01T12:00:00Z", "done": False, "free_text": "",
                  "answers": {"filters": {"choice": ["date"], "lines": [stale]}}}
        self.assertEqual(signoff.check_answers(stored, self.decisions, stored=True)["answers"]["filters"]["lines"],
                         [stale])
        stored["answers"]["filters"]["lines"] = [dict(stale, line=signoff.MAX_ATTACHMENT)]  # no file has more lines
        self.assertEqual(len(signoff.check_answers(stored, self.decisions, stored=True)["answers"]["filters"]["lines"]),
                         1)
        # An index past the item's attachments; a line past any file's, which a page would round past 2^53.
        for bad in (dict(stale, att=2), dict(stale, line=signoff.MAX_ATTACHMENT + 1), dict(stale, line=2 ** 53 + 1)):
            stored["answers"]["filters"]["lines"] = [bad]
            with self.assertRaises(ValueError):
                signoff.check_answers(stored, self.decisions, stored=True)


if __name__ == "__main__":
    unittest.main()
