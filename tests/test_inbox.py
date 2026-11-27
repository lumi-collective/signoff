"""The inbox: its todo.md reader and Markdown tree, its routes and their refusals, the start-link pairs from the
control socket, one instance per home, round locks, and the answer that wakes a waiting run."""
import contextlib
import hashlib
import http.client
import io
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import webbrowser
from pathlib import Path
from unittest import mock

from support import (ROOT, Folder, Rounds, Running, capitals, done_record, free_port, png_bytes, sample_decisions,
                     write_outside)
from test_extras import BOARD, HistoryRoot, with_board

import signoff

SCRIPT = str(ROOT / "plugin" / "signoff" / "signoff.py")
ANSWER = {"answers": {"layout": {"choice": "one", "comment": "from the inbox"}}, "free_text": "", "done": False}
POSIX = sys.platform != "win32"


def walk(nodes):
    """Every node of a Markdown tree, blocks and inlines."""
    for n in nodes:
        yield n
        for key in ("c", "head"):
            if isinstance(n.get(key), list):
                yield from walk([x for x in n[key] if isinstance(x, dict)] if key == "c" else
                                [x for cell in n[key] for x in cell])
        for item in n.get("items", []):
            yield from walk(item["c"])
        for row in n.get("rows", []):
            for cell in row:
                yield from walk(cell)


def texts(nodes):
    return "".join(n.get("v", "") for n in walk(nodes))


class MarkdownTree(unittest.TestCase):
    """The page renders todo items from this tree with DOM methods; HTML in it can only ever be text."""

    def test_html_is_text(self):
        src = 'Run <b>this</b> <script>alert(1)</script> <img src=x onerror="alert(1)"> &amp; <a href="https://x">y</a>'
        tree = signoff.markdown_blocks(src)
        self.assertEqual([n["t"] for n in walk(tree)], ["p", "text"])
        self.assertEqual(texts(tree), src)

    def test_only_http_links_become_links(self):
        for href in ("javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,<script>1</script>", "vbscript:x",
                     "//evil.example/x", "/local/path", "file:///etc/passwd", "mailto:a@b.example", "http:x",
                     "java\tscript:x"):
            with self.subTest(href=href):
                for src in ("[click](%s)" % href, "<%s>" % href):
                    tree = signoff.markdown_blocks(src)
                    self.assertNotIn("link", [n["t"] for n in walk(tree)], src)
                    self.assertEqual(texts(tree), src.replace("\\", ""), src)
        tree = signoff.markdown_blocks("see [the docs](https://example.com/a?b=1#c) and <http://example.com/x>")
        links = [n for n in walk(tree) if n["t"] == "link"]
        self.assertEqual([(n["href"], texts(n["c"])) for n in links],
                         [("https://example.com/a?b=1#c", "the docs"), ("http://example.com/x", "http://example.com/x")])

    def test_code_block_text_is_the_block_exactly(self):
        body = ["sudo du -sh /System/Volumes/Data  # 170 GB?", "echo \"$HOME\" '<b>' `date` \\", "",
                "  indented   with trailing spaces   ", "printf '%s\\n' *.md"]
        for fence in ("```", "~~~~"):
            with self.subTest(fence=fence):
                tree = signoff.markdown_blocks("Run this:\n\n%sbash\n%s\n%s\n\nThen this." % (fence, "\n".join(body), fence))
                code = [n for n in tree if n["t"] == "code"]
                self.assertEqual(len(code), 1)
                self.assertEqual((code[0]["lang"], code[0]["text"]), ("bash", "\n".join(body)))
        # Indented under a list item: the item's indentation is not part of the command.
        tree = signoff.markdown_blocks("- step one\n\n  ```powershell\n  Get-Item C:\\x\n    deeper\n  ```\n")
        code = [n for n in walk(tree) if n["t"] == "code"]
        self.assertEqual((code[0]["lang"], code[0]["text"]), ("powershell", "Get-Item C:\\x\n  deeper"))
        # No closing fence: the block runs to the end.
        self.assertEqual(signoff.markdown_blocks("```text\n/plugin update x\n")[0],
                         {"t": "code", "lang": "text", "text": "/plugin update x"})

    def test_lists_nest_with_their_checkboxes(self):
        tree = signoff.markdown_blocks("- [ ] open\n  - [x] done child\n  - plain child\n    more of it\n- [X] done\n1. first\n2. second")
        self.assertEqual([n["t"] for n in tree], ["list", "list"])
        outer = tree[0]
        self.assertFalse(outer["ordered"])
        self.assertEqual([i["check"] for i in outer["items"]], [False, True])
        inner = outer["items"][0]["c"][1]
        self.assertEqual(inner["t"], "list")
        self.assertEqual([i["check"] for i in inner["items"]], [True, None])
        self.assertEqual(texts(inner["items"][1]["c"]), "plain child\nmore of it")
        self.assertTrue(tree[1]["ordered"])
        self.assertEqual(texts(tree[1]["items"][1]["c"]), "second")

    def test_inline_code_bold_and_escapes(self):
        tree = signoff.markdown_blocks("**Bold `code` here** and `` a`b `` and \\*not bold\\*")
        nodes = tree[0]["c"]
        self.assertEqual(nodes[0]["t"], "b")
        self.assertEqual([n["t"] for n in nodes[0]["c"]], ["text", "code", "text"])
        self.assertIn({"t": "code", "v": "a`b"}, nodes)
        self.assertEqual(texts(tree), "Bold code here and a`b and *not bold*")

    def test_alerts_tables_quotes_headings(self):
        tree = signoff.markdown_blocks("> [!WARNING]\n> Back up first.\n\n> [!NOTE]\n> fine\n\n> [!FOO]\n> plain\n\n"
                                       "### Steps\n\n| a | b |\n|---|:-:|\n| `x` | y \\| z |\n| only |\n\n---")
        self.assertEqual([(n["t"], n.get("kind")) for n in tree],
                         [("alert", "warning"), ("alert", "note"), ("quote", None), ("h", None), ("table", None),
                          ("hr", None)])
        self.assertEqual(texts(tree[2]["c"]), "[!FOO]\nplain")
        table = tree[4]
        self.assertEqual([texts(c) for c in table["head"]], ["a", "b"])
        self.assertEqual([[texts(c) for c in row] for row in table["rows"]], [["x", "y | z"], ["only"]])
        self.assertEqual(table["rows"][0][0], [{"t": "code", "v": "x"}])

    def test_deep_nesting_is_capped(self):
        for src, shown in (("> " * 3000 + "x", "x"), ("- " * 3000 + "x", "x"), ("x " + "**" * 3000, "*"),
                           ("[" * 3000 + "](https://x)", "[")):
            tree = signoff.markdown_blocks(src)
            json.dumps(tree)
            self.assertIn(shown, texts(tree))

    def test_headings_carry_their_level(self):
        tree = signoff.markdown_blocks("# One\n## Two ##\n   ###### Six\n####### seven\n#\n")
        self.assertEqual([(n["t"], n.get("level"), texts(n.get("c", []))) for n in tree],
                         [("h", 1, "One"), ("h", 2, "Two"), ("h", 6, "Six"), ("p", None, "####### seven"),
                          ("h", 1, "")])

    def test_marks_off_marks_no_waiting_step(self):
        # An attachment is not the todo file: a step marked waiting there is only text.
        src = "- [ ] (waiting on you: run it) Run it\n  - [ ] (waiting on you: sub) deeper\n"
        on, off = signoff.markdown_blocks(src), signoff.markdown_blocks(src, marks=False)
        self.assertTrue(on[0]["items"][0]["wait"])
        self.assertTrue(on[0]["items"][0]["c"][1]["items"][0]["wait"])
        self.assertNotIn('"wait"', json.dumps(off))
        self.assertEqual(texts(on), texts(off))


class Adversarial(unittest.TestCase):
    def test_a_full_size_hostile_file_parses_in_time(self):
        """todo.md is agent-written: no line may make the parser quadratic (it runs under the inbox's lock)."""
        line_kinds = ["```" + "a" * 30000 + "`", "[" * 30000, "".join("`" * k + "x" for k in range(1, 240)),
                      "|" + " " * 30000 + "x", "- [ ] " + "[" * 30000, "- [ ] " + "`" * 3 + "a" * 30000 + "`",
                      "```" + " " * 30000 + "`", "~" * 30000 + "`", "~~~" + "\t" * 30000 + "`",
                      "- [ ] x\n  ```" + " " * 30000 + "`", "- [ ] " + "a" * 30000 + " / round-01",
                      "- [ ] " + "a/" * 15000 + "round-01"]
        text = "\n".join(line_kinds * 6)[:signoff.MAX_TODO]
        start = time.monotonic()
        signoff.parse_todos(text)
        signoff.markdown_blocks(text)
        self.assertLess(time.monotonic() - start, 5.0)

    def test_a_wide_table_is_cut_to_its_column_limit(self):
        text = "|" + "a|" * 20000 + "\n|" + "-|" * 20000 + "\n" + "|x|\n" * 5000
        start = time.monotonic()
        table = signoff.markdown_blocks(text)[0]
        self.assertLess(time.monotonic() - start, 5.0)
        self.assertEqual(len(table["head"]), signoff.MAX_COLUMNS)
        self.assertTrue(all(len(row) == 1 for row in table["rows"]))  # short rows stay short: the page fills them

    def test_the_tree_stays_near_the_size_of_the_file(self):
        """The tree is kept for as long as the inbox runs and goes into the page: no shape may multiply it."""
        table = "  |" + "a|" * 64 + "\n  |" + "-|" * 64 + "\n"
        shapes = ["- [ ] (waiting on you: x) t\n" + table + "  |\n" * 250000,  # one item, one huge table
                  ("- [ ] (waiting on you: x) t\n" + table + "  |\n" * 15000) * 20,  # many items just under the cap
                  "- [ ] y\n" + "  x\n\n" * (signoff.MAX_TODO // 5),  # one item of tiny paragraphs
                  # nested waiting items: each a step of its chain, in every parent's body
                  "".join("  " * d + "- [ ] (waiting on you: w) t\n" for d in range(12)) * 2
                  + ("  " * 12 + "`a` " * 4000 + "\n") * 2,
                  # lists nested as deep as allowed on every line, in items just under MAX_ITEM
                  ("- [ ] y\n" + ("  " + "+ " * signoff.MD_DEPTH + "x\n") * (signoff.MAX_ITEM // 16)) * 40,
                  # parents with many waiting children whose first lines are hostile (each a ref of its chain)
                  ("- [ ] p\n" + ("  - [ ] (waiting on you: w) " + "`a`" * 1200 + "\n") * 17) * 16,
                  # a step per line: each counted and its ref kept
                  "- [ ] p\n" + "  - [ ] (waiting on you: w) s\n" * (signoff.MAX_TODO // 30)]
        shapes[3] = shapes[3] * (signoff.MAX_TODO // len(shapes[3]))
        for text in shapes:
            text = text[:signoff.MAX_TODO]
            # parse_todos' rows as compact JSON, with the server's own keys (refs, waits, rounds) the page never gets:
            # an upper bound of what the page is sent. Measured worst ~22x (MAX_TODO is a budget for all rows: a
            # smaller file can go higher, never past this in total).
            sent = signoff._script_json({"todos": signoff.parse_todos(text)}, compact=True)
            self.assertLess(len(sent.encode("utf-8")), 28 * signoff.MAX_TODO)
        long_item = signoff.parse_todos(shapes[0][:signoff.MAX_TODO])[0]
        self.assertIn("not shown here", texts(long_item["body"]))


class TodoReader(unittest.TestCase):
    def todos(self, text):
        return signoff.parse_todos(text)

    def test_the_marker_is_exactly_as_agreed(self):
        cases = {
            "- [ ] (waiting on you: run the backup) Check the backup": ("run the backup", "Check the backup"),
            "- [ ] (waiting on you: decide) **Bold** title": ("decide", "Bold title"),
            "- [ ] (waiting on you: only the hint)": ("only the hint", "only the hint"),
            "* [ ] (waiting on you: star bullet) x": ("star bullet", "x"),
            "- [ ] (Waiting on you: capital) x": (None, "(Waiting on you: capital) x"),
            "- [ ]  (waiting on you: two spaces) x": (None, "(waiting on you: two spaces) x"),
            "- [ ] x (waiting on you: not first)": (None, "x (waiting on you: not first)"),
            "- [ ] (waiting on you:) empty": (None, "(waiting on you:) empty"),
            "- [ ] (waiting on you:   ) blank": (None, "(waiting on you:   ) blank"),
            "- [ ] (waiting on you: a) b) c": ("a", "b) c"),
            "- [ ] (waiting on you: nincs)x": (None, "(waiting on you: nincs)x"),
            "- [ ] (rád vár: magyar) x": (None, "(rád vár: magyar) x"),
        }
        for line, (hint, title) in cases.items():
            with self.subTest(line=line):
                got = self.todos(line)
                self.assertEqual(len(got), 1)
                self.assertEqual((got[0]["waiting"], texts(got[0]["title"])), (hint, title))
        self.assertEqual(self.todos("- [x] (waiting on you: done already) x\n- [X] y\n- not a task\n"), [])

    def test_items_their_bodies_and_nesting(self):
        text = "\n".join([
            "# Todo", "", "- [ ] **Release 0.3:** tag it and", "  push the tag.", "  - [x] changelog",
            "  - [ ] (waiting on you: sign the tag) sign it", "    ```bash", "    git tag -s v0.3.0", "    ```",
            "  - [ ] a plain child", "", "  Notes after a blank line.", "",
            "- [x] done parent", "  - [ ] open child of a done parent", "",
            "```text", "- [ ] (waiting on you: not an item) inside a fence", "```", "",
            "- [ ] (waiting on you: run it) Last", "",
            "  ```bash", "  brew upgrade", "  ```"])
        got = self.todos(text)
        # One row per chain: a nested item, marked or not, is a step of its top-level item, never a row of its own.
        self.assertEqual([(t["waiting"], texts(t["title"]), t["open"], t["steps"]) for t in got], [
            ("sign the tag", "Release 0.3: tag it and", True, [1, 3]),
            (None, "done parent", False, [0, 1]), ("run it", "Last", True, [0, 0])])
        release = got[0]
        self.assertEqual([n["t"] for n in release["body"]], ["p", "list", "p"])
        self.assertEqual(texts(release["body"][:1]), "push the tag.")  # the rest of the first paragraph
        nested = release["body"][1]["items"]
        self.assertEqual([i["check"] for i in nested], [True, False, False])
        code = [n for n in walk(nested[1]["c"]) if n["t"] == "code"]
        self.assertEqual(code[0]["text"], "git tag -s v0.3.0")  # the marked step's body is in its parent's body
        self.assertEqual((release["ref"], release["target"], release["waits"]),
                         ("**Release 0.3:** tag it and", "sign it", ["sign it"]))
        self.assertEqual(release["refs"], ["**Release 0.3:** tag it and", "sign it", "a plain child"])
        self.assertEqual((got[1]["ref"], got[1]["target"], got[1]["refs"]),
                         ("done parent", "open child of a done parent", ["open child of a done parent"]))
        self.assertEqual(got[2]["body"], [{"t": "code", "lang": "bash", "text": "brew upgrade"}])

    def test_a_chain_is_one_row_with_its_steps_counted(self):
        got = self.todos("- [ ] Ship 1.0\n  - [x] changelog\n  - [ ] (waiting on you: sign the tag) Sign the tag\n"
                         "    - [ ] push it\n  - [ ] announce\n- [ ] Next\n")
        self.assertEqual([(texts(t["title"]), t["waiting"], t["steps"], t["target"]) for t in got], [
            ("Ship 1.0", "sign the tag", [1, 4], "Sign the tag"), ("Next", None, [0, 0], "Next")])
        self.assertEqual((got[0]["ref"], got[0]["refs"], got[0]["waits"]),
                         ("Ship 1.0", ["Ship 1.0", "Sign the tag", "push it", "announce"], ["Sign the tag"]))
        # The parent's own marker comes first; every marked step still counts as waiting for a request.
        top = self.todos("- [ ] (waiting on you: decide) Pick a name\n  - [ ] (waiting on you: run it) Run\n")[0]
        self.assertEqual((top["waiting"], top["target"], top["waits"]), ("decide", "Pick a name", ["Pick a name", "Run"]))
        # A done parent without an open step is no row; a marker on a done step waits on no one.
        self.assertEqual(self.todos("- [x] all done\n  - [x] (waiting on you: x) c\n"), [])
        # A step with only the marker waits, with no ref to send a message on.
        bare = self.todos("- [ ] Parent\n  - [ ] (waiting on you: only this)\n")[0]
        self.assertEqual((bare["waiting"], bare["target"], bare["waits"], bare["steps"]),
                         ("only this", "Parent", [], [0, 1]))
        # The hint and the ref a message or Later goes to are one step's: the first marked one with a ref.
        mixed = self.todos("- [ ] Parent\n  - [ ] (waiting on you: a) \n  - [ ] (waiting on you: b) Step B\n")[0]
        self.assertEqual((mixed["waiting"], mixed["target"], mixed["waits"]), ("b", "Step B", ["Step B"]))

    def test_the_body_marks_each_waiting_step(self):
        body = self.todos("- [ ] Build\n  - [ ] (waiting on you: run `make`) Build it\n  - [x] (waiting on you: x) done\n"
                          "  - [ ] plain (waiting on you: not first)\n  - (waiting on you: no box) y\n")[0]["body"]
        self.assertEqual([i.get("wait", False) for i in body[0]["items"]], [True, False, False, False])

    def test_a_row_names_the_rounds_its_text_points_to(self):
        got = self.todos("- [ ] Answer 2026-11-07-repo-hygiene / round-07 first\n"
                         "  then .signoff/2026-11-08-x/round-02.json, not round-3, a/round-xx or /round-04.json\n"
                         "  - [ ] step about 2026-11-09-y/round-11 and 2026-11-07-repo-hygiene/round-07.json\n"
                         "- [ ] Nothing here\n")
        self.assertEqual([t["rounds"] for t in got], [
            ["2026-11-07-repo-hygiene/round-07.json", "2026-11-08-x/round-02.json", "2026-11-09-y/round-11.json"], []])
        many = self.todos("- [ ] x\n" + "".join("  t/round-%02d\n" % n for n in range(1, 30)))[0]
        self.assertEqual(len(many["rounds"]), signoff.MAX_ROUND_LINKS)

    def test_the_title_is_the_first_line_and_the_rest_opens_the_body(self):
        """The row shows the line the ref names; the rest of that paragraph (CommonMark's lazy continuation) is the
        start of the body, its links working there."""
        cases = [
            ("- [ ] Hero text\n  for the landing page\n", None, "Hero text", ["p"], "for the landing page"),
            ("- [ ] Hero text\n  See [the draft](https://example.com/draft).\n", None, "Hero text", ["p"],
             "See the draft."),
            ("- [ ] Hero text\n\n  Notes after a blank line.\n", None, "Hero text", ["p"], "Notes after a blank line."),
            ("- [ ] (waiting on you: approve the copy) Hero text for the landing page\n"
             "  See [the draft](https://example.com/draft).\n  ```bash\n  ls\n  ```\n",
             "approve the copy", "Hero text for the landing page", ["p", "code"], "See the draft."),
            ("- [ ] Hero text\n  - first step\n  - second step\n", None, "Hero text", ["list"], "first stepsecond step"),
            ("- [ ] Only one line\n", None, "Only one line", [], ""),
            # Markup that opens on the first line and closes on the next is text on both sides of the split.
            ("- [ ] **Bold `code\n  ` ends** here\n", None, "**Bold `code", ["p"], "` ends** here"),
        ]
        for text, hint, title, kinds, first_block in cases:
            with self.subTest(text=text):
                got = self.todos(text)
                self.assertEqual(len(got), 1)
                t = got[0]
                self.assertEqual((t["waiting"], texts(t["title"]), t["ref"]), (hint, title, title))
                self.assertEqual([n["t"] for n in t["body"]], kinds)
                self.assertEqual(texts(t["body"][:1]), first_block)
                self.assertNotIn("link", [n["t"] for n in walk(t["title"])])
        links = [n for n in walk(self.todos(cases[1][0])[0]["body"]) if n["t"] == "link"]
        self.assertEqual([(n["href"], texts(n["c"])) for n in links], [("https://example.com/draft", "the draft")])
        # A first line that is not a paragraph keeps today's title: the line as text, the whole tree as the body.
        heading = self.todos("- [ ] # Heading item\n  more\n")[0]
        self.assertEqual((texts(heading["title"]), [n["t"] for n in heading["body"]]), ("# Heading item", ["h", "p"]))
        table = self.todos("- [ ] a | b\n  --|--\n  1 | 2\n")[0]
        self.assertEqual((texts(table["title"]), [n["t"] for n in table["body"]]), ("a | b", ["table"]))

    def test_a_long_first_line(self):
        line = "x" * (signoff.MAX_REF + 50)
        t = self.todos("- [ ] %s\n  more\n" % line)[0]
        self.assertEqual((texts(t["title"]), t["ref"], texts(t["body"])), (line, line[:signoff.MAX_REF], "more"))
        huge = "y" * (signoff.MAX_ITEM + 10)
        t = self.todos("- [ ] %s\n  more\n" % huge)[0]
        self.assertEqual((texts(t["title"]), t["ref"]), (huge[:512], huge[:signoff.MAX_REF]))
        self.assertIn("not shown here", texts(t["body"]))
        self.assertNotIn("more", texts(t["body"]))

    def test_a_nested_waiting_item_shows_in_its_parents_body(self):
        got = self.todos("- [ ] Ship\n  - [ ] (waiting on you: run it) Release\n    ```bash\n    make release\n    ```\n")
        self.assertEqual(len(got), 1)
        code = [n for n in walk(got[0]["body"]) if n["t"] == "code"]
        self.assertEqual(code, [{"t": "code", "lang": "bash", "text": "make release"}])

    def test_items_are_found_below_the_markdown_depth(self):
        text = "".join("  " * d + "- [x] done\n" for d in range(10)) + "  " * 10 + "- [ ] (waiting on you: w) deep\n"
        self.assertEqual([(t["waiting"], t["target"], t["steps"]) for t in self.todos(text)], [("w", "deep", [9, 10])])

    def test_line_endings_and_tabs(self):
        got = self.todos("- [ ] (waiting on you: x) crlf\r\n\t```bash\r\n\tls\r\n\t```\r\n")
        self.assertEqual((got[0]["waiting"], got[0]["body"]), ("x", [{"t": "code", "lang": "bash", "text": "ls"}]))

    def test_the_todo_file_is_found_in_the_agreed_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(signoff.todo_file(root))
            (root / "_bmad-output" / "todo.md").mkdir(parents=True)  # a folder by that name is not the file
            self.assertIsNone(signoff.todo_file(root))
            (root / "TODO.md").write_text("- [ ] x\n", encoding="utf-8")
            self.assertEqual(signoff.todo_file(root), root / "TODO.md")
            (root / "_bmad-output" / "todo.md").rename(root / "_bmad-output" / "moved")
            (root / "_bmad-output" / "todo.md").write_text("- [ ] x\n", encoding="utf-8")
            self.assertEqual(signoff.todo_file(root), root / "_bmad-output" / "todo.md")

    def test_a_todo_file_out_of_bounds_is_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "TODO.md").write_bytes(b"- [ ] x\n" + b"y" * signoff.MAX_TODO)
            todos, error = signoff.read_todo_file(root / "TODO.md")
            self.assertEqual(todos, [])
            self.assertIn("larger than", error)
            (root / "TODO.md").write_bytes(b"- [ ] \xff\xfe broken utf-8\n")
            todos, error = signoff.read_todo_file(root / "TODO.md")
            self.assertEqual((texts(todos[0]["title"]), error), ("\ufffd\ufffd broken utf-8", None))


@unittest.skipUnless(POSIX, "links and a FIFO; the inbox does not run on Windows yet")
class TodoPathKey(unittest.TestCase):
    """The `todo` key of a project file: a path relative to the root, checked as a decisions file's pictures are,
    with no fallback to TODO_FILES, so a typo shows an error instead of another file."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / "project"
        (self.root / "notes").mkdir(parents=True)
        (self.root / "notes" / "tasks.md").write_text("- [ ] custom\n", encoding="utf-8")
        (self.root / "TODO.md").write_text("- [ ] fixed\n", encoding="utf-8")
        self.outside = self.base / "outside.md"
        self.outside.write_text("- [ ] SECRET\n", encoding="utf-8")

    def get(self, value, root=None):
        return signoff.project_todo({"root": str(root or self.root), "todo": value})

    def test_without_the_key_the_fixed_names_are_looked_for(self):
        self.assertEqual(signoff.project_todo({"root": str(self.root)}), (self.root / "TODO.md", "TODO.md", None))
        self.assertEqual(signoff.project_todo({"root": str(self.base / "empty")}), (None, None, None))

    def test_a_path_inside_the_root_is_the_todo_file_alone(self):
        (self.root / "tasks.md").write_text("- [ ] top\n", encoding="utf-8")
        (self.root / "link.md").symlink_to(self.root / "notes" / "tasks.md")
        (self.base / "linked").symlink_to(self.root)  # the root as the hooks wrote it may go through a link
        tasks = self.root / "notes" / "tasks.md"
        for value, root, path, file in (
                ("notes/tasks.md", None, tasks, "notes/tasks.md"),
                ("notes/../tasks.md", None, self.root / "tasks.md", "tasks.md"),
                (str(tasks), None, tasks, "notes/tasks.md"),
                ("link.md", None, tasks, "notes/tasks.md"),  # links are followed: the file read is shown
                ("notes/tasks.md", self.base / "linked", tasks, "notes/tasks.md"),
                (str(self.base / "linked" / "notes" / "tasks.md"), None, tasks, "notes/tasks.md")):
            self.assertEqual(self.get(value, root), (path, file, None), value)
        todos, error = signoff.read_todo_file(self.get("notes/tasks.md")[0])
        self.assertEqual(([texts(t["title"]) for t in todos], error), (["custom"], None))

    def test_a_bad_key_gives_the_fixed_error_and_no_other_file(self):
        (self.root / "out.md").symlink_to(self.outside)
        os.mkfifo(str(self.root / "fifo.md"))  # no writer: looking at it must not hang
        for value in ("../outside.md", str(self.outside), "out.md", "notes/\x01tasks.md", "C:tasks.md",
                      "//host/share/tasks.md", "\\\\host\\share\\tasks.md", "notes", "fifo.md", "missing.md",
                      "", ".", "a" * 300, 5, None, ["notes/tasks.md"], {"path": "notes/tasks.md"}):
            self.assertEqual(self.get(value), (None, signoff.TODO_KEY_FILE, signoff.TODO_KEY_ERROR), repr(value))
        for text in (signoff.TODO_KEY_FILE, signoff.TODO_KEY_ERROR):
            self.assertNotIn("/", text)

    def test_a_key_on_a_project_whose_folder_is_gone_shows_nothing(self):
        self.assertEqual(self.get("notes/tasks.md", self.base / "gone"), (None, None, None))

    def test_the_name_shown_is_one_line_of_plain_characters(self):
        # The key's text is checked; the name on disk it leads to (a link's target) is not, so it is cleaned.
        odd = self.root / "notes" / "a\x1bb‮c d.md"
        odd.write_text("- [ ] odd\n", encoding="utf-8")
        (self.root / "link.md").symlink_to(odd)
        self.assertEqual(self.get("link.md"), (odd, "notes/a b c d.md", None))
        self.assertEqual(self.get("notes/a‮b.md")[1:], (signoff.TODO_KEY_FILE, signoff.TODO_KEY_ERROR))
        (self.root / "notes" / "a‮b.md").write_text("- [ ] odd\n", encoding="utf-8")
        self.assertEqual(self.get("notes/a‮b.md")[1], "notes/a b.md")
        surrogate = self.root / "notes" / "x\udcff.md"  # a lone surrogate, as Linux gives a name not UTF-8
        with mock.patch.object(signoff, "_inside", lambda f, s, w: surrogate), \
                mock.patch.object(Path, "stat", lambda p, *a, **k: os.stat(str(odd))):
            path, file, error = self.get("whatever.md")
        self.assertEqual((path, file, error), (surrogate, "notes/x .md", None))
        file.encode("utf-8")  # the list page encodes its data as UTF-8


def entry(folder, rounds, project=None, mode="waiter", pid=None, **extra):
    data = {"schema": 1, "run": os.urandom(8).hex(), "project": project, "folder": str(folder), "rounds": rounds,
            "mode": mode, "pid": os.getpid() if pid is None else pid, "started": signoff.utc_now()}
    data.update(extra)
    return data


@unittest.skipUnless(POSIX, "the control socket is a Unix socket; the inbox does not run on Windows yet")
class InboxTest(unittest.TestCase):
    """A home, a project with a todo.md and a topic folder of two rounds (a picture and a board), and an inbox
    server on a thread. Runs are registered by writing their entry, as a waiter does."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.home = base / "home"
        self.root = base / "project"
        (self.root / ".git").mkdir(parents=True)
        self.topic = self.root / ".signoff" / "2026-11-06-inbox"
        (self.topic / "mockups").mkdir(parents=True)
        (self.topic / "boards").mkdir()
        (self.topic / "mockups" / "a.png").write_bytes(png_bytes())
        (self.topic / "boards" / "b.html").write_bytes(BOARD)
        for n in (1, 2):
            data = with_board()
            data["title"], data["tab"] = "Round %d" % n, "R%d" % n
            (self.topic / ("round-%02d.json" % n)).write_text(json.dumps(data), encoding="utf-8")
        (self.root / "TODO.md").write_text(
            "- [ ] (waiting on you: run the backup) Check <b>backups</b>\n  ```bash\n  tmutil startbackup\n  ```\n"
            "- [ ] Plain todo with [a link](https://example.com) and [bad](javascript:alert(1))\n- [x] done\n",
            encoding="utf-8")
        self.pid = signoff.project_id(self.root)
        self.home.mkdir(mode=0o700)
        for sub in ("projects", "runs", "sessions"):
            (self.home / sub).mkdir(mode=0o700)
        self.write(self.home / "projects" / (self.pid + ".json"),
                   {"schema": 1, "id": self.pid, "root": str(self.root), "name": self.root.name})
        self.server = None

    def tearDown(self):
        if self.server:
            self.stop()
        self._tmp.cleanup()

    def write(self, path, data):
        signoff.write_atomic(path, json.dumps(data))

    def register(self, **kw):
        e = entry(self.topic, ["round-01.json", "round-02.json"], project=self.pid, **kw)
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        return e

    def start(self):
        self.server = signoff.InboxServer(self.home)
        self.port = self.server.server_address[1]
        self.host = "127.0.0.1:%d" % self.port
        self.origin = "http://" + self.host
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()
        return self.server

    def stop(self):
        self.server.shutdown()
        self.server.close()
        self.thread.join(5)
        self.server = None

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Host": self.host}
        hdrs.update(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        if body is not None and "Content-Length" not in hdrs:
            hdrs["Content-Length"] = str(len(body))
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in hdrs.items():
            if v is not None:
                conn.putheader(k, v)
        conn.endheaders(body)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def open(self):
        """A new pair from the server and the page path its start link sends on to."""
        link = self.server.new_pair()
        start = link.split(self.host, 1)[1]
        resp, _ = self.request("GET", start)
        self.assertEqual(resp.status, 303)
        return start, resp.getheader("Location")

    def view(self, path):
        resp, body = self.request("GET", path)
        self.assertEqual(resp.status, 200, body)
        return json.loads(re.search(r'id="signoff-data">(.*?)</script>', body.decode("utf-8"), re.S).group(1)), body

    def save(self, page, body, group=0, round_=0, headers=None):
        hdrs = {"Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"}
        hdrs.update(headers or {})
        return self.request("POST", "%srun/%d/save/%d" % (page, group, round_), body, hdrs)


class InboxPage(InboxTest):
    def test_pairs_die_alone_on_reuse(self):
        self.start()
        start_a, page_a = self.open()
        start_b, page_b = self.open()
        self.assertNotEqual(page_a, page_b)
        self.assertEqual(self.request("GET", page_a)[0].status, 200)
        resp, body = self.request("GET", start_a)
        self.assertEqual(resp.status, 410)
        self.assertNotIn(page_a.strip("/").encode(), body)
        self.assertEqual(self.request("GET", page_a)[0].status, 404)  # the pair is gone, page token and all
        self.assertEqual(self.request("GET", page_b)[0].status, 200)  # the other pair and the server live on
        self.assertEqual(self.request("GET", self.open()[1])[0].status, 200)

    def test_an_unused_start_link_expires(self):
        self.start()
        with mock.patch.object(signoff, "START_TTL", 0.0):
            link = self.server.new_pair()
            time.sleep(0.05)
            resp, _ = self.request("GET", link.split(self.host, 1)[1])
        self.assertEqual(resp.status, 410)

    def test_every_request_needs_a_live_token_and_the_exact_host(self):
        self.register()
        self.start()
        start, page = self.open()
        token = page.strip("/")
        for path in ("/", "/" + token, "/%s/" % ("A" * len(token)), "/x/run/0/", start.replace("/b/", "/"),
                     page + "run/0", page + "run/9/", page + "run/00/", page + "img/0", page + "?theme=dark",
                     page + "run/0/?x=1"):
            self.assertEqual(self.request("GET", path)[0].status, 404, path)
        for host in ("localhost.example:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, "", None):
            for path in (page, page + "run/0/", start):
                self.assertEqual(self.request("GET", path, headers={"Host": host})[0].status, 403, (host, path))
            self.assertEqual(self.save(page, ANSWER, headers={"Host": host})[0].status, 403)

    def test_localhost_names_the_same_inbox(self):
        # A forwarded port or a devcontainer's Ports tab may open the inbox as localhost: the same rules hold.
        self.register()
        self.start()
        local = "localhost:%d" % self.port
        own = {"Host": local, "Origin": "http://" + local}
        link = self.server.new_pair()
        self.assertTrue(link.startswith("http://127.0.0.1:%d/b/" % self.port), link)  # the link it hands out
        resp, _ = self.request("GET", link.split(self.host, 1)[1], headers={"Host": local})
        self.assertEqual(resp.status, 303)
        page = resp.getheader("Location")
        for path in (page, page + "state", page + "run/0/", page + "run/0/img/0"):
            self.assertEqual(self.request("GET", path, headers={"Host": local})[0].status, 200, path)
        prefs = {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}
        resp, out = self.request("POST", page + "prefs", {"theme": "dark", "palette": "gruvbox"}, dict(prefs, **own))
        self.assertEqual(resp.status, 200, out)
        resp, out = self.save(page, ANSWER, headers=own)
        self.assertEqual(resp.status, 200, out)
        # An Origin of the other name is not this request's own.
        for host, origin in ((local, self.origin), (self.host, "http://" + local)):
            self.assertEqual(self.save(page, ANSWER, headers={"Host": host, "Origin": origin})[0].status, 403, host)

    def test_a_fixed_port_on_request(self):
        port = free_port()
        self.server = signoff.InboxServer(self.home, port=port)
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()
        self.assertEqual(self.server.server_address[:2], ("127.0.0.1", port))
        self.assertEqual(json.loads((self.home / "inbox.json").read_text(encoding="utf-8"))["port"], port)

    def test_the_inbox_page_lists_what_waits_and_holds_no_paths(self):
        e = self.register()
        self.start()
        start, page = self.open()
        data, body = self.view(page)
        self.assertEqual(data["mode"], "inbox")
        for text in (str(self.root), str(self.home), str(self.topic), "round-01.json", e["run"]):
            self.assertNotIn(text.encode(), body)
        kinds = [(w["kind"], w.get("title") if w["kind"] == "round" else w["waiting"]) for w in data["waiting"]]
        self.assertEqual(kinds, [("round", "Round 1"), ("round", "Round 2"), ("todo", "run the backup")])
        self.assertEqual([w.get("group") for w in data["waiting"][:2]], [0, 0])
        project = data["projects"][0]
        self.assertEqual((project["id"], project["name"], project["mono"]), (self.pid, "project", "PR"))
        self.assertEqual([texts(t["title"]) for t in project["todos"]],
                         ["Plain todo with a link and [bad](javascript:alert(1))"])
        self.assertNotIn(b"<b>backups", body)  # the agent's HTML only ever travels as escaped JSON text
        csp = self.request("GET", page)[0].getheader("Content-Security-Policy")
        self.assertIn("default-src 'none'", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    def test_a_round_row_carries_its_item_titles_for_the_search_and_no_more(self):
        many = one_round(*[question("q%d" % n, title="Item %d %s" % (n, "x" * 300)) for n in range(signoff.MAX_ROW_ITEMS + 5)])
        (self.topic / "round-01.json").write_text(json.dumps(many), encoding="utf-8")
        self.register()
        self.start()
        start, page = self.open()
        data, body = self.view(page)
        first, second = [w for w in data["waiting"] if w["kind"] == "round"]
        self.assertEqual(len(first["items"]), signoff.MAX_ROW_ITEMS)
        self.assertEqual(first["items"][0], ("Item 0 " + "x" * 300)[:signoff.MAX_ROW_TEXT])
        self.assertEqual(second["items"], [it["title"] for it in with_board()["items"]])
        for text in ("round-01.json", "round-02.json", self.topic.name):  # a title, never a name on disk
            self.assertNotIn(text.encode(), body)

    def test_a_round_page_and_the_save_rules(self):
        self.register()
        self.start()
        start, page = self.open()
        self.view(page)
        data, body = self.view(page + "run/0/")
        self.assertEqual((data["mode"], data["inbox"], len(data["rounds"])), ("rounds", True, 2))
        self.assertEqual(self.request("GET", page + "run/0/img/0")[1], png_bytes())
        for origin in ("http://evil.example", "null", "http://localhost:%d" % self.port, None):
            self.assertEqual(self.save(page, ANSWER, headers={"Origin": origin})[0].status, 403, origin)
        for site in ("cross-site", "same-site", "none"):
            self.assertEqual(self.save(page, ANSWER, headers={"Sec-Fetch-Site": site})[0].status, 403)
        self.assertEqual(self.save(page, ANSWER, headers={"Content-Type": "text/plain"})[0].status, 415)
        self.assertEqual(self.save(page, ANSWER, headers={"Content-Length": None})[0].status, 411)
        self.assertEqual(self.save(page, b"{}", headers={"Content-Length": "41943040"})[0].status, 413)
        self.assertEqual(self.save(page, dict(ANSWER, done="yes"))[0].status, 400)
        self.assertEqual(self.save(page, ANSWER, round_=2)[0].status, 404)
        self.assertEqual(self.save(page, ANSWER, group=1)[0].status, 404)
        self.assertEqual(self.request("POST", page + "save/0", ANSWER, {"Content-Type": "application/json",
                                                                          "Origin": self.origin})[0].status, 404)
        self.assertFalse((self.topic / "round-01.answers.json").exists())
        resp, out = self.save(page, ANSWER)
        self.assertEqual(resp.status, 200, out)
        saved = json.loads((self.topic / "round-01.answers.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["answers"]["layout"]["comment"], "from the inbox")
        self.assertEqual(self.save(page, dict(ANSWER, done=True), round_=0)[0].status, 409)  # round 2 not accepted

    def test_boards_have_their_own_token_and_sandbox(self):
        self.register()
        self.start()
        start, page = self.open()
        self.view(page)
        data, body = self.view(page + "run/0/")
        board = data["rounds"][0]["decisions"]["items"][0]["images"][1]["board"]
        resp, html = self.request("GET", board)
        self.assertEqual((resp.status, html), (200, BOARD))
        self.assertEqual(resp.getheader("Content-Security-Policy"), signoff.BOARD_CSP)
        token = board.split("/")[1]
        for path in ("/%s/" % token, "/%s/run/0/" % token, "/%s/0/9" % token, "/%s/b/" % token):
            self.assertEqual(self.request("GET", path)[0].status, 404, path)
        self.assertEqual(self.request("POST", "/%s/run/0/save/0" % token, ANSWER, {
            "Content-Type": "application/json", "Origin": self.origin})[0].status, 404)
        self.assertEqual(self.request("GET", page + board.split("/", 2)[2])[0].status, 404)

    def test_a_round_held_by_another_run_is_read_only(self):
        self.register(mode="page")
        held = signoff.try_lock(self.topic / "round-01.json.lock")
        self.addCleanup(signoff.unlock, held)
        self.start()
        start, page = self.open()
        data, _ = self.view(page)
        self.assertTrue(all(w["held"] for w in data["waiting"] if w["kind"] == "round"))
        data, _ = self.view(page + "run/0/")
        self.assertTrue(data["held"])
        self.assertTrue(all(r["readonly"] for r in data["rounds"]))
        self.assertEqual(self.save(page, ANSWER)[0].status, 409)
        self.assertFalse((self.topic / "round-01.answers.json").exists())

    def test_a_waiting_todo_item_carries_the_time_of_its_file(self):
        # The list shows when each row last changed and sorts on it; a todo item has only its file's time.
        when = 1700000000
        os.utime(str(self.root / "TODO.md"), (when, when))
        self.start()
        data = self.view(self.open()[1])[0]
        rows = [w for w in data["waiting"] if w["kind"] == "todo"]
        self.assertEqual([w["since"] for w in rows], ["2023-11-14T22:13:20Z"])
        self.assertTrue(all(signoff.SAVED_PATTERN.fullmatch(w["since"]) for w in data["waiting"]))

    def test_a_waiting_round_carries_its_file_time_as_opened_and_its_run_start_as_since(self):
        # The 2-day warning reads the round file's time; the row's clock and sort stay on the run's start.
        when = int(time.time()) - 3 * 86400
        os.utime(str(self.topic / "round-01.json"), (when, when))
        e = self.register()
        self.start()
        rows = [w for w in self.view(self.open()[1])[0]["waiting"] if w["kind"] == "round"]
        self.assertEqual(rows[0]["opened"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when)))
        self.assertTrue(signoff.SAVED_PATTERN.fullmatch(rows[1]["opened"]))
        self.assertEqual([w["since"] for w in rows], [e["started"]] * 2)

    def test_runs_outside_a_project_that_ended_or_died_are_not_waiting(self):
        # A project run that ended or died leaves orphan rounds instead (InboxOrphan); an old one leaves nothing.
        def register(**kw):
            e = entry(self.topic, ["round-01.json", "round-02.json"], **kw)
            self.write(self.home / "runs" / (e["run"] + ".json"), e)
            return e

        register(ended=signoff.utc_now())
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        register(pid=dead.pid)
        old = self.register()
        stale = self.home / "runs" / (old["run"] + ".json")
        os.utime(str(stale), (time.time() - signoff.RUNS_MAX_AGE - 60,) * 2)
        self.start()
        start, page = self.open()
        data, _ = self.view(page)
        self.assertEqual([w["kind"] for w in data["waiting"]], ["todo"])
        # The inbox locks nothing it does not show: a new run may still lock these rounds.
        fd = signoff.try_lock(self.topic / "round-01.json.lock")
        self.assertIsNotNone(fd)
        signoff.unlock(fd)

    def test_sent_then_working_only_from_the_session(self):
        rounds = Rounds(1)
        self.addCleanup(rounds.cleanup)
        e = entry(rounds.path, ["round-01.json"], project=self.pid)
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        self.start()
        start, page = self.open()
        self.view(page)
        self.assertEqual(self.save(page, dict(ANSWER, done=True))[0].status, 200)
        data, _ = self.view(page)
        self.assertEqual([w["kind"] for w in data["waiting"]], ["todo"])
        self.assertEqual([(s["title"], s["state"]) for s in data["sent"]], [("Round 1", "sent")])
        saved = data["sent"][0]["saved"]
        session = self.home / "sessions" / "s1.json"
        self.write(session, {"schema": 1, "session": "s1", "project": self.pid, "pid": None, "state": "working",
                             "at": "2026-01-01T00:00:00Z"})
        self.assertEqual(self.view(page)[0]["sent"][0]["state"], "sent")  # before the answer: not about it
        later = "2099-01-01T00:00:00Z"
        self.write(session, {"schema": 1, "session": "s1", "project": self.pid, "pid": None, "state": "working",
                             "at": later})
        self.assertGreater(later, saved)
        self.assertEqual(self.view(page)[0]["sent"][0]["state"], "working")
        os.utime(str(session), (time.time() - signoff.SESSIONS_MAX_AGE - 60,) * 2)
        self.assertEqual(self.view(page)[0]["sent"][0]["state"], "sent")  # too old to read


class InboxReview(InboxTest):
    """What the first review found."""

    def test_an_answers_file_with_an_impossible_date_does_not_break_the_list(self):
        rounds = Rounds(1)
        self.addCleanup(rounds.cleanup)
        e = entry(rounds.path, ["round-01.json"], project=self.pid)
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        rounds.answers(1).write_text(json.dumps(dict(done_record(), saved="2026-99-99T99:99:99Z")), encoding="utf-8")
        self.start()
        start, page = self.open()
        data, _ = self.view(page)
        self.assertEqual([w["kind"] for w in data["waiting"]], ["todo"])

    def test_a_round_that_could_not_be_read_shows_for_a_new_run(self):
        good = (self.topic / "round-02.json").read_text(encoding="utf-8")
        (self.topic / "round-02.json").write_text("{broken", encoding="utf-8")
        self.register()
        self.start()
        start, page = self.open()
        self.assertEqual([w["kind"] for w in self.view(page)[0]["waiting"]], ["todo"])
        (self.topic / "round-02.json").write_text(good, encoding="utf-8")
        self.register()
        self.assertEqual([w["kind"] for w in self.view(page)[0]["waiting"]], ["round", "round", "todo"])

    def test_a_page_token_left_unused_expires(self):
        self.start()
        start, page = self.open()
        with mock.patch.object(signoff, "PAGE_IDLE", 0.0):
            time.sleep(0.05)
            self.assertEqual(self.request("GET", page)[0].status, 404)

    def test_runs_on_overlapping_rounds_do_not_lock_each_other_out(self):
        self.register()
        e = entry(self.topic, ["round-01.json"], project=self.pid)
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        self.start()
        start, page = self.open()
        data, _ = self.view(page)
        rounds = [w for w in data["waiting"] if w["kind"] == "round"]
        # Each run's own rows: the round both wait on shows under each, writable in both.
        self.assertEqual(sorted((w["title"], w["held"]) for w in rounds),
                         [("Round 1", False), ("Round 1", False), ("Round 2", False)])
        for group in sorted({w["group"] for w in rounds}):
            resp, body = self.save(page, ANSWER, group=group)
            # The second group has not read the file the first just wrote: a stale write, refused; never a lock.
            self.assertEqual((resp.status, b"another tab" in body), (409, True) if group else (200, False), body)

    def run_on(self, *names, **extra):
        e = entry(self.topic, list(names), project=self.pid, **extra)
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        return e

    def test_a_round_held_elsewhere_leaves_another_runs_round_open(self):
        held = signoff.try_lock(self.topic / "round-01.json.lock")  # a page run started before the inbox
        self.addCleanup(signoff.unlock, held)
        self.run_on("round-01.json")
        self.run_on("round-02.json")
        self.start()
        start, page = self.open()
        rounds = {w["title"]: w for w in self.view(page)[0]["waiting"] if w["kind"] == "round"}
        self.assertEqual((rounds["Round 1"]["held"], rounds["Round 2"]["held"]), (True, False))
        resp, body = self.save(page, dict(ANSWER, done=True), group=rounds["Round 2"]["group"])
        self.assertEqual(resp.status, 200, body)  # and Done there: its run's own gate, one round

    def test_another_run_ending_on_the_folder_keeps_an_open_round_page(self):
        a = self.run_on("round-01.json", "round-02.json", started="2026-11-06T00:00:00Z")
        self.run_on("round-01.json", started="2026-11-06T00:00:01Z")
        self.start()
        start, page = self.open()
        rows = [w for w in self.view(page)[0]["waiting"] if w["title"] == "Round 1"]
        mine = max(w["group"] for w in rows)  # the later run's page
        self.write(self.home / "runs" / (a["run"] + ".json"), dict(a, ended=signoff.utc_now()))
        self.view(page)
        self.assertEqual(self.request("GET", page + "run/%d/" % mine)[0].status, 200)
        self.assertEqual(self.save(page, ANSWER, group=mine)[0].status, 200)

    def test_an_entry_without_a_run_id_is_not_shown(self):
        e = entry(self.topic, ["round-01.json"], project=self.pid)
        del e["run"]  # register_run always writes one; without it a failed load could never be retried
        self.write(self.home / "runs" / "norun.json", e)
        self.start()
        start, page = self.open()
        self.assertEqual([w["kind"] for w in self.view(page)[0]["waiting"]], ["todo"])

    def test_a_slow_control_client_does_not_hold_up_the_others(self):
        self.start()
        slow = socket.socket(socket.AF_UNIX)
        self.addCleanup(slow.close)
        slow.connect(str(self.home / "inbox.sock"))
        slow.sendall(b"{")  # and then nothing
        began = time.monotonic()
        self.assertIn("link", signoff.inbox_request(self.home, "open"))
        self.assertLess(time.monotonic() - began, 2.0)


class InboxRequests(InboxTest):
    """A message on an open todo item: POST <page>/request writes requests/<project>/<id>.json in the home, for the
    project's session (its mod, or the next SessionStart). The todo file is never written."""
    WAITING_REF = "Check <b>backups</b>"
    PLAIN_REF = "Plain todo with [a link](https://example.com) and [bad](javascript:alert(1))"

    def send(self, page, body, headers=None, path="request"):
        hdrs = {"Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"}
        hdrs.update(headers or {})
        return self.request("POST", page + path, body, hdrs)

    def message(self, text="please split it", ref=PLAIN_REF, project=None):
        return {"project": project or self.pid, "ref": ref, "text": text}

    def requests(self):
        folder = self.home / "requests"
        return sorted(p for p in folder.rglob("*") if p.is_file()) if folder.exists() else []

    def todos(self, data):
        """ref -> requests of every todo item on the page: Waiting on you, then the project lists."""
        items = [w for w in data["waiting"] if w["kind"] == "todo"] + data["projects"][0]["todos"]
        return {t["ref"]: t["requests"] for t in items}

    def test_each_todo_item_has_its_ref_and_no_requests_yet(self):
        self.start()
        data, _ = self.view(self.open()[1])
        self.assertEqual(self.todos(data), {self.WAITING_REF: [], self.PLAIN_REF: []})

    def test_a_message_becomes_a_request_file_and_shows_on_its_item(self):
        self.start()
        page = self.open()[1]
        todo = (self.root / "TODO.md").read_bytes()
        resp, body = self.send(page, self.message("  please split it  "))
        self.assertEqual(resp.status, 200, body)
        reply = json.loads(body)
        files = self.requests()
        self.assertEqual([p.relative_to(self.home).as_posix() for p in files],
                         ["requests/%s/%s.json" % (self.pid, reply["id"])])
        saved = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(sorted(saved), ["at", "id", "kind", "ref", "schema", "text"])
        self.assertEqual((saved["schema"], saved["id"], saved["kind"], saved["ref"], saved["text"]),
                         (1, reply["id"], "todo", self.PLAIN_REF, "please split it"))
        self.assertRegex(saved["id"], r"^[0-9a-f]{16}$")
        self.assertRegex(saved["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(reply["at"], saved["at"])
        if POSIX:
            self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
            for folder in (self.home / "requests", files[0].parent):
                self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertEqual((self.root / "TODO.md").read_bytes(), todo)
        resp, _ = self.send(page, self.message("and this one", ref=self.WAITING_REF))
        self.assertEqual(resp.status, 200)
        shown = self.todos(self.view(page)[0])
        self.assertEqual([(r["text"], r["done"]) for r in shown[self.PLAIN_REF]], [("please split it", False)])
        self.assertEqual([r["text"] for r in shown[self.WAITING_REF]], ["and this one"])
        (files[0].parent / (saved["id"] + ".done")).write_bytes(b"")  # the agent marked it handled
        self.assertEqual(self.todos(self.view(page)[0])[self.PLAIN_REF][0]["done"], True)
        _, html = self.view(page)
        for text in (str(self.home), str(self.root), saved["id"]):
            self.assertNotIn(text.encode(), html)

    def test_only_the_inbox_writes_an_answer_request(self):
        self.start()
        page = self.open()[1]
        for body in ({"kind": "answer", "project": self.pid, "ref": "2026-11-06-inbox / round-01"},
                     {"kind": "answer", "project": self.pid, "ref": "2026-11-06-inbox / round-01", "text": "x"}):
            self.assertEqual(self.send(page, body)[0].status, 400, body)
        self.assertEqual(self.requests(), [])

    def test_the_request_route_keeps_todays_rules(self):
        self.register()
        self.start()
        start, page = self.open()
        good = self.message()
        for host in ("localhost.example:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, "", None):
            self.assertEqual(self.send(page, good, {"Host": host})[0].status, 403, host)
        for origin in ("http://evil.example", "null", "http://localhost:%d" % self.port, self.origin + "/", None):
            self.assertEqual(self.send(page, good, {"Origin": origin})[0].status, 403, origin)
        for site in ("cross-site", "same-site", "none"):
            self.assertEqual(self.send(page, good, {"Sec-Fetch-Site": site})[0].status, 403, site)
        for ctype in ("text/plain", "application/x-www-form-urlencoded", None):
            self.assertEqual(self.send(page, good, {"Content-Type": ctype})[0].status, 415, ctype)
        self.assertEqual(self.send(page, good, {"Content-Length": None})[0].status, 411)
        self.assertEqual(self.send(page, b"{}", {"Content-Length": "41943040"})[0].status, 413)
        self.assertEqual(self.send(page, {"text": "x" * signoff.MAX_REQUEST_BODY})[0].status, 413)
        for body in (b"{x", b"[]", json.dumps([good]).encode(), b"\xff", {"project": self.pid, "ref": self.PLAIN_REF},
                     dict(good, extra=1), dict(good, text=7), dict(good, ref=None), dict(good, project=1),
                     dict(good, text=""), dict(good, text="   "), dict(good, text="x" * (signoff.MAX_REQUEST_TEXT + 1)),
                     dict(good, text="two\nlines"), dict(good, text="a\x00b"), dict(good, text="a\x85b"),
                     dict(good, text="a\u2028b"), b'{"project": "%s", "ref": "x", "text": "\\ud800"}' % self.pid.encode(),
                     dict(good, kind="vuln")):
            self.assertEqual(self.send(page, body)[0].status, 400, body)
        for project in ("f" * 16, "../" + self.pid, capitals(self.pid), ""):
            self.assertEqual(self.send(page, dict(good, project=project))[0].status, 404, project)
        for ref in ("done", "Plain todo", self.PLAIN_REF + " ", "", "x" * 400):
            self.assertEqual(self.send(page, dict(good, ref=ref))[0].status, 409, ref)
        board = self.view(page + "run/0/")[0]["rounds"][0]["decisions"]["items"][0]["images"][1]["board"]
        for path in ("request/", "request?theme=dark", "run/0/request", "/request", "requests", "Request"):
            self.assertEqual(self.send(page, good, path=path)[0].status, 404, path)
        for token in ("A" * 43, start.split("/")[2], board.split("/")[1]):
            self.assertEqual(self.send("/%s/" % token, good)[0].status, 404, token)
        self.assertEqual(self.request("GET", page + "request")[0].status, 404)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.request("GET", start)[0].status, 410)  # a closed pair saves nothing either
        self.assertEqual(self.send(page, good)[0].status, 404)
        self.assertEqual(self.requests(), [])

    def test_a_run_page_has_no_request_route(self):
        folder = Folder()
        self.addCleanup(folder.cleanup)
        run = Running(folder)
        self.addCleanup(run.stop)
        resp, _ = run.request("POST", "/%s/request" % run.token, self.message(), {
            "Content-Type": "application/json", "Origin": run.origin, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(resp.status, 404)
        self.assertFalse((self.home / "requests").exists())

    def test_no_vulnerability_section_whatever_files_are_there(self):
        """Uptick's vulns.json and scan-repos.json, and osv-scanner.json in the home, each naming the project, as an
        older inbox read them: the list has no vulnerability key, a project without a todo file is no row, Scan this
        project gets what any unknown route gets, and a finding handed over what any unknown shape gets."""
        base = self.root.parent
        env = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(base / "cache"),
                                           "XDG_CONFIG_HOME": str(base / "config")})
        env.start()
        self.addCleanup(env.stop)
        bare = base / "bare"  # a project the hooks noted, with no todo file
        (bare / ".git").mkdir(parents=True)
        bid = signoff.project_id(bare)
        self.write(self.home / "projects" / (bid + ".json"), {"schema": 1, "id": bid, "root": str(bare), "name": "bare"})
        found = {"name": "lodash", "version": "4.17.20", "ecosystem": "npm", "ids": ["GHSA-aaaa-bbbb-cccc"],
                 "level": "high", "fixed": "4.17.21"}
        scanned = {"at": time.time() - 60, "tried": time.time() - 60, "count": 1, "level": "high", "details": [found],
                   "gaps": [], "error": "", "hint": ""}
        package = {"package": {"name": "jinja2", "version": "2.10.0", "ecosystem": "PyPI"},
                   "groups": [{"ids": ["GHSA-462w-v97r-4m45"], "aliases": [], "max_severity": "8.6"}],
                   "vulnerabilities": [{"id": "GHSA-462w-v97r-4m45", "affected": [{
                       "package": {"name": "jinja2", "ecosystem": "PyPI"},
                       "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.10.1"}]}]}]}]}
        scan_list = base / "config" / "uptick" / "scan-repos.json"
        for path, data in ((base / "cache" / "uptick" / "vulns.json",
                            {"schema": 1, "targets": {}, "repos": {str(r): scanned for r in (self.root, bare)}}),
                           (scan_list, {"repos": [str(self.root), str(bare)]}),
                           (self.home / "osv-scanner.json", {"results": [
                               {"source": {"path": str(r / "requirements.txt"), "type": "lockfile"},
                                "packages": [package]} for r in (self.root, bare)]})):
            path.parent.mkdir(parents=True, exist_ok=True)
            self.write(path, data)
        listed = scan_list.read_bytes()
        self.start()
        page = self.open()[1]
        data, html = self.view(page)
        self.assertEqual([p["id"] for p in data["projects"]], [self.pid])
        self.assertNotIn("osv", data["projects"][0])
        for text in (b"lodash", b"jinja2", b"GHSA", b"osv", b"uptick"):
            self.assertNotIn(text, html.split(b'id="signoff-data">', 1)[1].split(b"</script>", 1)[0])
        for path in ("scan", "scan/", "run/0/scan"):
            self.assertEqual(self.send(page, {"project": self.pid}, path=path)[0].status, 404, path)
        self.assertEqual(self.request("GET", page + "scan")[0].status, 404)
        for body in ({"kind": "vuln", "project": self.pid, "ref": "lodash@4.17.20"},
                     {"kind": "vuln", "project": self.pid, "ref": "jinja2@2.10.0", "ecosystem": "PyPI"},
                     {"kind": "vulns", "project": self.pid, "count": 1}):
            self.assertEqual(self.send(page, body)[0].status, 400, body)
        self.assertEqual((scan_list.read_bytes(), self.requests()), (listed, []))


class InboxTodoKey(InboxTest):
    """A project file's `todo` key on the list and for messages: its file alone, and for a bad key the project with
    a fixed error, its sessions still there, no other file's text and no path."""
    WAITING_REF, PLAIN_REF = InboxRequests.WAITING_REF, InboxRequests.PLAIN_REF
    send, message, requests = InboxRequests.send, InboxRequests.message, InboxRequests.requests

    def key(self, value):
        self.write(self.home / "projects" / (self.pid + ".json"), {"schema": 1, "id": self.pid, "root": str(self.root),
                                                                   "name": self.root.name, "todo": value})

    def test_the_key_names_the_todo_file_for_the_list_and_messages(self):
        (self.root / "notes").mkdir()
        (self.root / "notes" / "tasks.md").write_text("- [ ] Custom task\n", encoding="utf-8")
        self.key("notes/tasks.md")
        self.start()
        page = self.open()[1]
        project = self.view(page)[0]["projects"][0]
        self.assertEqual((project["file"], project["error"]), ("notes/tasks.md", None))
        self.assertEqual([t["ref"] for t in project["todos"]], ["Custom task"])
        self.assertEqual(self.send(page, self.message(ref="Custom task"))[0].status, 200)
        self.assertEqual(self.send(page, self.message())[0].status, 409)  # TODO.md is not read while the key is set
        self.assertEqual(self.send(page, {"kind": "later", "project": self.pid, "ref": self.WAITING_REF})[0].status,
                         409)

    def test_a_bad_key_keeps_the_project_with_a_fixed_error(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("- [ ] SECRET item\n", encoding="utf-8")
        (self.root / "out.md").symlink_to(outside / "secret.md")
        (self.root / "big.md").write_bytes(b"- [ ] SECRET item\n" + b"y" * signoff.MAX_TODO)
        self.write(self.home / "sessions" / "s1.json", {
            "schema": 1, "session": "s1", "project": self.pid, "pid": os.getpid(), "state": "working",
            "at": stamp(5), "waiting": "permission", "waiting_at": stamp(5)})
        self.start()
        for value, error in (("../outside/secret.md", signoff.TODO_KEY_ERROR),
                             (str(outside / "secret.md"), signoff.TODO_KEY_ERROR),
                             ("out.md", signoff.TODO_KEY_ERROR), ("missing.md", signoff.TODO_KEY_ERROR),
                             ("big.md", "larger than 1024 KiB")):
            self.key(value)
            page = self.open()[1]
            data, html = self.view(page)
            project = data["projects"][0]
            self.assertEqual((project["id"], project["error"], project["todos"]), (self.pid, error, []), value)
            # A file inside the root that is too big is named, as a fixed name is; a bad key's value never.
            bad = error == signoff.TODO_KEY_ERROR
            self.assertEqual(project["file"], signoff.TODO_KEY_FILE if bad else value)
            self.assertEqual([w["kind"] for w in data["waiting"]], ["session"], value)  # TODO.md is not read either
            for text in (b"SECRET", str(outside).encode()) + ((value.encode(),) if bad else ()):
                self.assertNotIn(text, html, value)
            for ref in ("SECRET item", self.PLAIN_REF):
                self.assertEqual(self.send(page, self.message(ref=ref))[0].status, 409, (value, ref))
        self.assertEqual(self.requests(), [])

    def test_a_name_that_is_not_utf8_keeps_the_list_loading(self):
        # Linux gives a name that is not UTF-8 a lone surrogate; macOS makes no such file, so the name is put in at
        # the key's check and the file's stat, for the key only.
        (self.root / "notes").mkdir()
        real = self.root / "notes" / "tasks.md"
        real.write_text("- [ ] Custom task\n", encoding="utf-8")
        odd = self.root / "notes" / "x\udcff.md"
        inside, stat_ = signoff._inside, Path.stat
        self.key("notes/odd.md")
        self.start()
        with mock.patch.object(signoff, "_inside", lambda f, s, w: odd if w == "todo" else inside(f, s, w)), \
                mock.patch.object(Path, "stat", lambda p, *a, **k: stat_(real if p == odd else p, *a, **k)):
            data, _ = self.view(self.open()[1])
        self.assertEqual(data["projects"][0]["file"], "notes/x .md")


class InboxChains(InboxTest):
    """A chain (a top-level item and the items under it) is one row, in Waiting on you when any step waits and
    counted once in state.json. Messages and Later go to a step's own ref; an item naming a round open in a live
    run of its project links to that run's page."""
    TODO = ("- [ ] Ship 1.0\n  - [x] changelog\n  - [ ] (waiting on you: sign the tag) Sign the tag\n"
            "  - [ ] (waiting on you: post it) announce\n- [x] Migrate\n  - [ ] check the logs\n- [ ] Plain\n")
    send, requests, todos = InboxRequests.send, InboxRequests.requests, InboxRequests.todos

    def setUp(self):
        super().setUp()
        (self.root / "TODO.md").write_text(self.TODO, encoding="utf-8")

    def message(self, ref):
        return {"project": self.pid, "ref": ref, "text": "please"}

    def test_a_chain_is_one_row_and_counts_once(self):
        self.start()
        data, _ = self.view(self.open()[1])
        rows = [w for w in data["waiting"] if w["kind"] == "todo"]
        self.assertEqual([(texts(w["title"]), w["waiting"], w["ref"], w["steps"]) for w in rows],
                         [("Ship 1.0", "sign the tag", "Sign the tag", [1, 3])])
        self.assertEqual([(texts(t["title"]), t["ref"], t["steps"]) for t in data["projects"][0]["todos"]],
                         [("Migrate", "check the logs", [0, 1]), ("Plain", "Plain", [0, 0])])
        self.assertEqual(json.loads((self.home / "state.json").read_text(encoding="utf-8"))["waiting"], 1)
        self.assertEqual([w["park"] for w in rows] + [t["park"] for t in data["projects"][0]["todos"]],
                         [True, False, False])
        for row in rows + data["projects"][0]["todos"]:
            self.assertFalse({"refs", "waits", "rounds", "target", "open"} & set(row))  # the server's alone

    def test_later_shows_only_where_a_marked_item_has_a_ref(self):
        (self.root / "TODO.md").write_text(
            "- [ ] Parent\n  - [ ] (waiting on you: only this)\n- [ ] (waiting on you: decide)\n  - [ ] child\n"
            "- [ ] Two\n  - [ ] (waiting on you: a) \n  - [ ] (waiting on you: b) Step B\n", encoding="utf-8")
        self.start()
        page = self.open()[1]
        rows = [w for w in self.view(page)[0]["waiting"] if w["kind"] == "todo"]
        self.assertEqual([(w["waiting"], w["ref"], w["park"]) for w in rows],
                         [("only this", "Parent", False), ("decide", "child", False), ("b", "Step B", True)])
        for w in rows:  # what the page offers is what the server takes
            status = self.send(page, {"kind": "later", "project": self.pid, "ref": w["ref"]})[0].status
            self.assertEqual(status, 200 if w["park"] else 409, w)

    def test_requests_on_any_step_show_on_the_chain_and_a_step_takes_messages(self):
        folder = self.home / "requests" / self.pid
        folder.mkdir(parents=True, mode=0o700)
        for rid, ref in (("a" * 16, "announce"), ("b" * 16, "Sign the tag"), ("c" * 16, "Ship 1.0")):
            self.write(folder / (rid + ".json"), {"schema": 1, "id": rid, "kind": "todo", "ref": ref,
                                                  "text": "old " + ref, "at": "2026-11-01T00:00:00Z"})
        self.start()
        page = self.open()[1]
        shown = self.todos(self.view(page)[0])
        self.assertEqual(sorted((r["text"], r["later"]) for r in shown["Sign the tag"]),
                         [("old Ship 1.0", False), ("old Sign the tag", False), ("old announce", False)])
        for ref in ("announce", "Sign the tag", "Ship 1.0", "check the logs"):
            self.assertEqual(self.send(page, self.message(ref))[0].status, 200, ref)
        for ref in ("changelog", "Migrate"):  # a done step, a done parent
            self.assertEqual(self.send(page, self.message(ref))[0].status, 409, ref)

    def test_later_asks_the_agent_to_park_a_waiting_item(self):
        self.start()
        page = self.open()[1]
        todo = (self.root / "TODO.md").read_bytes()
        later = {"kind": "later", "project": self.pid, "ref": "Sign the tag"}
        for body in (dict(later, text="x"), dict(later, kind="Later"), dict(later, kind="todo"), dict(later, extra="1"),
                     {"kind": "later", "project": self.pid}, dict(later, ref=1), dict(later, ref=None)):
            self.assertEqual(self.send(page, body)[0].status, 400, body)
        self.assertEqual(self.send(page, dict(later, project="f" * 16))[0].status, 404)
        for ref in ("Ship 1.0", "Plain", "check the logs", "changelog", "nope", ""):  # open but not waiting, or none
            self.assertEqual(self.send(page, dict(later, ref=ref))[0].status, 409, ref)
        self.assertEqual(self.requests(), [])
        resp, body = self.send(page, later)
        self.assertEqual(resp.status, 200, body)
        path = self.home / "requests" / self.pid / (json.loads(body)["id"] + ".json")
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((saved["kind"], saved["ref"], saved["text"]), ("later", "Sign the tag", signoff.LATER_TEXT))
        self.assertEqual(signoff.LATER_TEXT, "The owner asks to park this item for later: remove its waiting marker "
                                             "and keep it open.")
        self.assertIn('on the todo item "Sign the tag": "The owner asks to park',
                      signoff.request_line(signoff.read_request(path)))
        row = [w for w in self.view(page)[0]["waiting"] if w["kind"] == "todo"][0]
        self.assertEqual([(r["text"], r["later"]) for r in row["requests"]], [(signoff.LATER_TEXT, True)])
        self.assertEqual(self.send(page, dict(later, ref="announce"))[0].status, 200)  # the other marked step
        self.assertEqual((self.root / "TODO.md").read_bytes(), todo)

    def test_an_item_naming_a_live_open_round_links_to_its_run(self):
        (self.root / "TODO.md").write_text(
            "- [ ] (waiting on you: answer it) Answer 2026-11-06-inbox / round-02\n"
            "- [ ] Not open: .signoff/2026-11-06-inbox/round-09.json\n- [ ] Elsewhere other-topic/round-01\n",
            encoding="utf-8")
        self.start()
        page = self.open()[1]

        def links():
            data = self.view(page)[0]
            return ([w.get("round") for w in data["waiting"] if w["kind"] == "todo"]
                    + [t.get("round") for t in data["projects"][0]["todos"]])
        self.assertEqual(links(), [None, None, None])  # no run
        e = entry(self.topic, ["round-01.json", "round-02.json"], project=None)  # a run outside the project
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        self.assertEqual(links(), [None, None, None])
        self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, ended=signoff.utc_now()))
        self.assertEqual(links(), [None, None, None])
        self.register()
        self.assertEqual(links(), ["run/1/", None, None])
        _, body = self.view(page)
        self.assertNotIn(b"round-02.json", body)  # a link, never the path


class InboxPrefs(InboxTest):
    """The theme: POST <page>/prefs writes prefs.json in the home, and every page gets it in its data."""
    GOOD = {"theme": "dark", "palette": "gruvbox"}
    DEFAULT = {"theme": "system", "palette": "catppuccin"}

    def send(self, page, body, headers=None, path="prefs"):
        hdrs = {"Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"}
        hdrs.update(headers or {})
        return self.request("POST", page + path, body, hdrs)

    def stored(self):
        path = self.home / "prefs.json"
        return path.read_bytes() if path.exists() else None

    @staticmethod
    def shown(data):
        return {k: data["prefs"][k] for k in ("theme", "palette")}

    def test_density_is_an_optional_third_key(self):
        # The inbox list's Comfortable or Compact rows. A body without it keeps the stored one, so a page that sends
        # only {theme, palette} never resets it.
        self.start()
        page = self.open()[1]
        self.assertEqual(self.view(page)[0]["prefs"]["density"], "comfortable")
        resp, body = self.send(page, dict(self.GOOD, density="compact"))
        self.assertEqual(resp.status, 200, body)
        saved = json.loads((self.home / "prefs.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(saved), ["at", "density", "palette", "schema", "sort", "theme"])
        self.assertEqual(saved["density"], "compact")
        self.assertEqual(self.view(page)[0]["prefs"]["density"], "compact")
        self.assertEqual(self.send(page, {"theme": "light", "palette": "dracula"})[0].status, 200)
        data = self.view(page)[0]["prefs"]
        self.assertEqual((data["theme"], data["palette"], data["density"]), ("light", "dracula", "compact"))
        self.assertEqual(self.send(page, dict(self.GOOD, density="comfortable"))[0].status, 200)
        self.assertEqual(self.view(page)[0]["prefs"]["density"], "comfortable")
        before = self.stored()
        for density in ("Compact", "dense", "", None, 1, ["compact"], "compact "):
            self.assertEqual(self.send(page, dict(self.GOOD, density=density))[0].status, 400, density)
        self.assertEqual(self.send(page, dict(self.GOOD, density="compact", extra=1))[0].status, 400)
        self.assertEqual(self.send(page, {"palette": "gruvbox", "density": "compact"})[0].status, 400)
        self.assertEqual(self.stored(), before)
        for raw in (b'{"theme": "dark", "palette": "gruvbox", "density": "tiny"}',
                    b'{"theme": "dark", "palette": "gruvbox", "density": ["compact"]}'):
            (self.home / "prefs.json").write_bytes(raw)
            self.assertEqual(self.view(page)[0]["prefs"]["density"], "comfortable", raw)

    def test_sort_is_an_optional_key_like_density(self):
        # How the Waiting on you rows are ordered. Left out, the stored one stays; a bad value is a 400.
        self.start()
        page = self.open()[1]
        self.assertEqual(self.view(page)[0]["prefs"]["sort"], "oldest")
        resp, body = self.send(page, dict(self.GOOD, sort="newest"))
        self.assertEqual(resp.status, 200, body)
        saved = json.loads((self.home / "prefs.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(saved), ["at", "density", "palette", "schema", "sort", "theme"])
        self.assertEqual((saved["sort"], saved["density"]), ("newest", "comfortable"))
        self.assertEqual(self.view(page)[0]["prefs"]["sort"], "newest")
        self.assertEqual(self.send(page, dict(self.GOOD, density="compact"))[0].status, 200)
        self.assertEqual(self.send(page, self.GOOD)[0].status, 200)
        data = self.view(page)[0]["prefs"]
        self.assertEqual((data["sort"], data["density"]), ("newest", "compact"))
        self.assertEqual(self.send(page, dict(self.GOOD, density="compact", sort="project"))[0].status, 200)
        self.assertEqual(self.view(page)[0]["prefs"]["sort"], "project")
        before = self.stored()
        for sort in ("Newest", "latest", "", None, 1, ["newest"], "newest "):
            self.assertEqual(self.send(page, dict(self.GOOD, sort=sort))[0].status, 400, sort)
        self.assertEqual(self.send(page, dict(self.GOOD, sort="newest", extra=1))[0].status, 400)
        self.assertEqual(self.send(page, {"palette": "gruvbox", "sort": "newest"})[0].status, 400)
        self.assertEqual(self.send(page, {})[0].status, 400)
        self.assertEqual(self.send(page, {"sort": "latest"})[0].status, 400)
        self.assertEqual(self.send(page, {"sort": "newest", "extra": 1})[0].status, 400)
        self.assertEqual(self.stored(), before)
        # The list's sort buttons send the sort alone: the stored theme, palette and density stay (another tab or the
        # panel may have changed them since this page loaded).
        self.assertEqual(self.send(page, dict(self.GOOD, density="compact", sort="oldest"))[0].status, 200)
        resp, body = self.send(page, {"sort": "newest"})
        self.assertEqual(resp.status, 200, body)
        saved = json.loads((self.home / "prefs.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["theme"], saved["palette"], saved["density"], saved["sort"]),
                         ("dark", "gruvbox", "compact", "newest"))
        for raw in (b'{"theme": "dark", "palette": "gruvbox", "sort": "latest"}',
                    b'{"theme": "dark", "palette": "gruvbox", "sort": ["newest"]}'):
            (self.home / "prefs.json").write_bytes(raw)
            self.assertEqual(self.view(page)[0]["prefs"]["sort"], "oldest", raw)

    def test_the_lists_are_the_pages_and_the_generators(self):
        page = signoff.page_source()
        self.assertEqual(tuple(re.findall(r"\{id: '([a-z-]+)'", page)), signoff.PALETTES)  # the picker's, in order
        self.assertIn("var THEMES = %s;" % json.dumps(list(signoff.THEMES)).replace('"', "'"), page)
        self.assertIn("var DENSITIES = %s;" % json.dumps(list(signoff.DENSITIES)).replace('"', "'"), page)
        self.assertIn("var SORTS = %s;" % json.dumps(list(signoff.SORTS)).replace('"', "'"), page)
    def test_a_choice_is_kept_and_every_page_opens_in_it(self):
        self.register()
        self.start()
        page = self.open()[1]
        list_data, _ = self.view(page)
        self.assertEqual(self.shown(list_data), self.DEFAULT)
        self.assertTrue(list_data["prefs"]["keep"])
        self.assertTrue(self.view(page + "run/0/")[0]["prefs"]["keep"])
        resp, body = self.send(page, self.GOOD)
        self.assertEqual((resp.status, json.loads(body)), (200, {"ok": True}), body)
        path = self.home / "prefs.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(saved), ["at", "density", "palette", "schema", "sort", "theme"])
        self.assertEqual((saved["schema"], saved["theme"], saved["palette"], saved["density"]),
                         (1, "dark", "gruvbox", "comfortable"))
        self.assertRegex(saved["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o700)
        self.assertEqual([p.name for p in self.home.iterdir() if p.name.startswith(".tmp-")], [])
        for path_ in (page, page + "run/0/"):
            self.assertEqual(self.shown(self.view(path_)[0]), self.GOOD, path_)
        resp, _ = self.send(page, {"theme": "system", "palette": "dracula"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.shown(self.view(page)[0]), {"theme": "system", "palette": "dracula"})

    def test_the_prefs_route_keeps_todays_rules(self):
        self.register()
        self.start()
        start, page = self.open()
        for host in ("localhost.example:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, "", None):
            self.assertEqual(self.send(page, self.GOOD, {"Host": host})[0].status, 403, host)
        for origin in ("http://evil.example", "null", "http://localhost:%d" % self.port, self.origin + "/", None):
            self.assertEqual(self.send(page, self.GOOD, {"Origin": origin})[0].status, 403, origin)
        for site in ("cross-site", "same-site", "none"):
            self.assertEqual(self.send(page, self.GOOD, {"Sec-Fetch-Site": site})[0].status, 403, site)
        for ctype in ("text/plain", "application/x-www-form-urlencoded", None):
            self.assertEqual(self.send(page, self.GOOD, {"Content-Type": ctype})[0].status, 415, ctype)
        self.assertEqual(self.send(page, self.GOOD, {"Content-Length": None})[0].status, 411)
        self.assertEqual(self.send(page, b"{}", {"Content-Length": "41943040"})[0].status, 413)
        self.assertEqual(self.send(page, dict(self.GOOD, pad="x" * signoff.MAX_PREFS_BODY))[0].status, 413)
        for body in (b"{x", b"[]", b"\xff", b"null", json.dumps([self.GOOD]).encode(), {"theme": "dark"},
                     {"palette": "gruvbox"}, dict(self.GOOD, extra=1), dict(self.GOOD, theme="auto"),
                     dict(self.GOOD, theme="Dark"), dict(self.GOOD, theme=None), dict(self.GOOD, theme=["dark"]),
                     dict(self.GOOD, palette="rosepine"), dict(self.GOOD, palette="gruvbox "),
                     dict(self.GOOD, palette="</script><script>"), dict(self.GOOD, palette=1)):
            self.assertEqual(self.send(page, body)[0].status, 400, body)
        board = self.view(page + "run/0/")[0]["rounds"][0]["decisions"]["items"][0]["images"][1]["board"]
        for path in ("prefs/", "prefs?theme=dark", "run/0/prefs", "run/0/run/0/prefs", "Prefs", "prefs.json"):
            self.assertEqual(self.send(page, self.GOOD, path=path)[0].status, 404, path)
        for token in ("A" * 43, start.split("/")[2], board.split("/")[1]):
            self.assertEqual(self.send("/%s/" % token, self.GOOD)[0].status, 404, token)
        self.assertEqual(self.request("GET", page + "prefs")[0].status, 404)
        self.assertIsNone(self.stored())
        self.assertEqual(self.request("GET", start)[0].status, 410)  # a closed pair saves nothing either
        self.assertEqual(self.send(page, self.GOOD)[0].status, 404)
        self.assertIsNone(self.stored())

    def test_a_bad_prefs_file_falls_back_to_the_default(self):
        self.start()
        page = self.open()[1]
        path = self.home / "prefs.json"
        for raw, want in ((b"{x", self.DEFAULT), (b"[]", self.DEFAULT), (b"\xff\xfe", self.DEFAULT),
                          (b'{"theme": "auto", "palette": "rosepine"}', self.DEFAULT),
                          (b'{"theme": ["dark"], "palette": {"a": 1}}', self.DEFAULT),
                          (b'{"theme": "light", "palette": "nope"}', {"theme": "light", "palette": "catppuccin"}),
                          (b'{"palette": "tokyo-night"}', {"theme": "system", "palette": "tokyo-night"}),
                          (b'{"theme": "dark", "palette": "rose-pine", "extra": "</script>"}',
                           {"theme": "dark", "palette": "rose-pine"}),
                          (b"[" * 4000 + b"]" * 4000, self.DEFAULT),
                          (json.dumps(dict(self.GOOD, pad="x" * signoff.MAX_PREFS_FILE)).encode(), self.DEFAULT)):
            path.write_bytes(raw)
            data, html = self.view(page)
            self.assertEqual(self.shown(data), want, raw)
            self.assertNotIn(b"</script><", html.split(b'id="signoff-data">', 1)[1].split(b"</script>", 1)[0])
        path.unlink()
        os.mkfifo(str(path))  # no writer: reading it must not hang the page
        self.assertEqual(self.shown(self.view(page)[0]), self.DEFAULT)
        path.unlink()
        path.mkdir()  # not a file
        self.assertEqual(self.shown(self.view(page)[0]), self.DEFAULT)

    def test_a_link_at_prefs_json_is_replaced_and_its_target_left_alone(self):
        self.start()
        page = self.open()[1]
        other = self.home.parent / "other.json"
        other.write_text('{"keep": "me"}', encoding="utf-8")
        (self.home / "prefs.json").symlink_to(other)
        self.assertEqual(self.send(page, self.GOOD)[0].status, 200)
        self.assertFalse((self.home / "prefs.json").is_symlink())
        self.assertEqual(other.read_text(encoding="utf-8"), '{"keep": "me"}')
        self.assertEqual(self.shown(self.view(page)[0]), self.GOOD)

    def test_a_page_without_the_inbox_reads_prefs_and_writes_nothing(self):
        # No inbox.lock: a run is the plain run and the past-decisions page is read only, so both may only read.
        (self.home / "prefs.json").write_text('{"schema": 1, "theme": "light", "palette": "dracula"}',
                                              encoding="utf-8")
        before = sorted(p.relative_to(self.home).as_posix() for p in self.home.rglob("*"))
        folder = Folder()
        self.addCleanup(folder.cleanup)
        with mock.patch.dict(os.environ, {"SIGNOFF_INBOX_HOME": str(self.home)}):
            run = Running(folder)
            self.addCleanup(run.stop)
            resp, body = run.request("GET", "/%s/" % run.token)
            self.assertEqual(resp.status, 200)
            data = json.loads(re.search(r'id="signoff-data">(.*?)</script>', body.decode("utf-8"), re.S).group(1))
            self.assertEqual(self.shown(data), {"theme": "light", "palette": "dracula"})
            self.assertFalse(data["prefs"]["keep"])
            resp, _ = run.request("POST", "/%s/prefs" % run.token, self.GOOD, {
                "Content-Type": "application/json", "Origin": run.origin, "Sec-Fetch-Site": "same-origin"})
            self.assertEqual(resp.status, 404)
            root = HistoryRoot()
            self.addCleanup(root.cleanup)
            history = signoff.make_history_server(root.path)
            threading.Thread(target=history.serve_forever, args=(0.05,), daemon=True).start()
            self.addCleanup(history.server_close)
            self.addCleanup(history.shutdown)
            past = Running.__new__(Running)
            past.port, past.host = history.server_address[1], history.host
            resp, body = past.request("GET", "/%s/" % history.token)
            data = json.loads(re.search(r'id="signoff-data">(.*?)</script>', body.decode("utf-8"), re.S).group(1))
            self.assertEqual((data["mode"], self.shown(data), data["prefs"]["keep"]),
                             ("history", {"theme": "light", "palette": "dracula"}, False))
        # A relative home would be read against whatever folder the run starts in: here one with a prefs.json.
        cwd = Path(self._tmp.name) / "cwd"
        (cwd / "relative" / "home").mkdir(parents=True)
        (cwd / "relative" / "home" / "prefs.json").write_text('{"theme": "dark", "palette": "gruvbox"}',
                                                             encoding="utf-8")
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(str(cwd))
        with mock.patch.dict(os.environ, {"SIGNOFF_INBOX_HOME": "relative/home"}):
            resp, body = run.request("GET", "/%s/" % run.token)
            data = json.loads(re.search(r'id="signoff-data">(.*?)</script>', body.decode("utf-8"), re.S).group(1))
            self.assertEqual(self.shown(data), self.DEFAULT)  # a relative home is read against nothing
        self.assertEqual(sorted(p.relative_to(self.home).as_posix() for p in self.home.rglob("*")), before)
        self.assertEqual(json.loads((self.home / "prefs.json").read_text(encoding="utf-8"))["palette"], "dracula")


class InboxState(InboxTest):
    """state.json in the home and the list page's state route: the number of rows in Waiting on you, per project,
    with no text; it follows runs, answers and todo files without a page load."""

    def state(self):
        return json.loads((self.home / "state.json").read_text(encoding="utf-8"))

    def wait_state(self, waiting):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if self.state()["waiting"] == waiting:
                    return self.state()
            except (OSError, ValueError):
                pass
            time.sleep(0.02)
        self.fail("state.json never said %d waiting" % waiting)

    def test_the_count_is_the_rows_waiting_on_you(self):
        e = self.register()
        with mock.patch.object(signoff, "STATE_EVERY", 0.05):
            self.start()
            state = self.state()  # written at start, before any page load
            start, page = self.open()
            rows = self.view(page)[0]["waiting"]
            self.assertEqual((state["waiting"], len(rows)), (3, 3))
            self.assertEqual(state["projects"], [{"id": self.pid, "monogram": "PR", "waiting": 3}])
            resp, body = self.request("GET", page + "state")
            self.assertEqual((resp.status, resp.getheader("Content-Type")), (200, "application/json"))
            self.assertEqual(json.loads(body)["waiting"], 3)
            # No page load from here on: the inbox looks by itself.
            InboxControl.end(self, e)
            self.assertEqual(self.wait_state(1)["projects"], [{"id": self.pid, "monogram": "PR", "waiting": 1}])
            with open(str(self.root / "TODO.md"), "a", encoding="utf-8") as f:
                f.write("- [ ] (waiting on you: sign the release) Release 1.0\n")
            self.wait_state(2)
            rounds = Rounds(1)
            self.addCleanup(rounds.cleanup)
            one = entry(rounds.path, ["round-01.json"], project=None)
            self.write(self.home / "runs" / (one["run"] + ".json"), one)
            state = self.wait_state(3)
            self.assertEqual(state["projects"][0]["id"], None)  # a run outside any project: no id, its monogram
            self.assertEqual(len(self.view(page)[0]["waiting"]), 3)
            group = [w for w in self.view(page)[0]["waiting"] if w["kind"] == "round"][0]["group"]
            self.assertEqual(self.save(page, dict(ANSWER, done=True), group=group)[0].status, 200)
            self.wait_state(2)
            self.assertEqual(json.loads(self.request("GET", page + "state")[1])["waiting"], 2)

    def test_state_json_holds_no_text(self):
        e = self.register()
        self.start()
        start, page = self.open()
        raw = (self.home / "state.json").read_bytes()
        route = self.request("GET", page + "state")[1]
        self.assertEqual(stat.S_IMODE((self.home / "state.json").stat().st_mode), 0o600)
        for body in (raw, route):
            data = json.loads(body)
            self.assertEqual(sorted(data), ["at", "backs", "projects", "schema", "waiting"])
            self.assertEqual((data["schema"], data["waiting"], data["backs"]), (1, 3, 0))
            self.assertRegex(data["at"], signoff.SAVED_PATTERN)
            for p in data["projects"]:
                self.assertEqual(sorted(p), ["id", "monogram", "waiting"])
                self.assertLessEqual(len(p["monogram"]), 2)
            for text in ("Round", "R1", "backup", "Check", "Plain todo", str(self.root), str(self.home), str(self.topic),
                         "round-01", e["run"], page.strip("/"), start.split("/")[2], "http"):
                self.assertNotIn(text.encode(), body, text)

    def test_the_state_route_keeps_todays_rules(self):
        self.register()
        self.start()
        start, page = self.open()
        token = page.strip("/")
        board = self.view(page + "run/0/")[0]["rounds"][0]["decisions"]["items"][0]["images"][1]["board"]
        resp, body = self.request("GET", page + "state")
        self.assertEqual(resp.status, 200, body)
        self.assertIsNone(resp.getheader("Set-Cookie"))
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        for path in ("/state", "/%s/state" % ("A" * len(token)), "/%s/state" % start.split("/")[2], page + "state/",
                     page + "state?theme=dark", page + "run/0/state", "/%s/state" % board.split("/")[1],
                     "/b/%s/state" % start.split("/")[2]):
            self.assertEqual(self.request("GET", path)[0].status, 404, path)
        for host in ("localhost.example:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, "", None):
            self.assertEqual(self.request("GET", page + "state", headers={"Host": host})[0].status, 403, host)
        hdrs = {"Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(self.request("POST", page + "state", ANSWER, hdrs)[0].status, 404)
        self.assertEqual(self.request("GET", start)[0].status, 410)  # the pair closes: its state goes with it
        self.assertEqual(self.request("GET", page + "state")[0].status, 404)

    def test_a_run_page_has_no_state_route(self):
        folder = Folder()
        self.addCleanup(folder.cleanup)
        run = Running(folder)
        self.addCleanup(run.stop)
        self.assertEqual(run.request("GET", "/%s/state" % run.token)[0].status, 404)

    def test_a_stopped_inbox_writes_no_more(self):
        with mock.patch.object(signoff, "STATE_EVERY", 0.02):
            self.start()
            self.stop()
            before = (self.home / "state.json").stat().st_mtime_ns
            with open(str(self.root / "TODO.md"), "a", encoding="utf-8") as f:
                f.write("- [ ] (waiting on you: one more) Another\n")
            time.sleep(0.2)
        self.assertEqual((self.home / "state.json").stat().st_mtime_ns, before)

    def test_inbox_lock_is_touched_at_each_look_while_the_inbox_lives(self):
        # The plugin's mod cannot flock: an inbox.lock touched within LIVE_WITHIN seconds is its sign of a live inbox.
        lock = self.home / "inbox.lock"
        with mock.patch.object(signoff, "STATE_EVERY", 0.02):
            self.start()
            os.utime(str(lock), (0, 0))
            deadline = time.monotonic() + 5
            while lock.stat().st_mtime < time.time() - 60 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertGreater(lock.stat().st_mtime, time.time() - 60)
            self.assertEqual(lock.stat().st_size, 0)
            self.stop()
            os.utime(str(lock), (0, 0))
            time.sleep(0.2)
        self.assertEqual(lock.stat().st_mtime, 0)  # a stopped inbox touches nothing

    def test_a_look_after_close_takes_no_round_lock(self):
        self.register()
        server = self.start()
        self.stop()
        server.view()  # a page request still running when the inbox stopped
        fd = signoff.try_lock(self.topic / "round-01.json.lock")
        self.assertIsNotNone(fd)
        signoff.unlock(fd)

    def test_a_run_registered_after_close_gets_no_group_and_no_lock(self):
        server = self.start()  # no run yet, so no group
        self.stop()
        self.register()
        server.view()
        fd = signoff.try_lock(self.topic / "round-01.json.lock")
        self.assertIsNotNone(fd)
        signoff.unlock(fd)
        self.assertEqual([g for g in server.groups if g], [])

    def test_a_state_json_that_cannot_be_written_still_counts_and_says_why(self):
        (self.home / "state.json").mkdir()  # in the way of the write
        self.register()
        err = io.StringIO()
        with mock.patch.object(signoff, "STATE_EVERY", 0.05), mock.patch.object(sys, "stderr", err):
            self.start()
            start, page = self.open()
            resp, body = self.request("GET", page + "state")
            self.assertEqual((resp.status, json.loads(body)["waiting"]), (200, 3))
            time.sleep(0.3)  # several looks while the write fails
            (self.home / "state.json").rename(self.home / "moved")  # out of the way; the next look writes
            self.wait_state(3)
        lines = [line for line in err.getvalue().splitlines() if line.startswith("signoff inbox: could not")]
        self.assertEqual(lines, ["signoff inbox: could not write state.json (IsADirectoryError)"])  # once
        self.assertEqual([p.name for p in self.home.iterdir() if p.name.startswith(".tmp-")], [])


def stamp(ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))


def dead_pid():
    p = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, timeout=30)
    return int(p.stdout)


class InboxLive(InboxTest):
    """Live sessions from the hooks' sessions/ files: one waiting on the user (a permission prompt or a question) is
    a row in Waiting on you and counts; one idle for IDLE_AFTER seconds or more is a row in Idle and counts nowhere."""

    BG = "8d4e0402-151b-464f-bceb-89da0de14e2c"

    def session(self, name, state="working", at=None, **extra):
        data = {"schema": 1, "session": name, "project": self.pid, "pid": os.getpid(), "state": state,
                "at": at or stamp(5)}
        data.update(extra)
        self.write(self.home / "sessions" / (name + ".json"), data)
        return data

    def waits(self, name, ago=90, **extra):
        return self.session(name, waiting="permission", waiting_at=stamp(ago), **extra)

    def page(self):
        start, page = self.open()
        data, body = self.view(page)
        return data, body

    def test_a_waiting_session_waits_on_you_and_counts(self):
        w = self.waits(self.BG, bg=True)
        with mock.patch.object(signoff, "STATE_EVERY", 0.05):
            self.start()
            state = json.loads((self.home / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["waiting"], 2)  # the todo item marked waiting, and the session
            self.assertEqual(state["projects"], [{"id": self.pid, "monogram": "PR", "waiting": 2}])
            data, body = self.page()
        rows = [r for r in data["waiting"] if r["kind"] == "session"]
        self.assertEqual(rows, [{"project": self.pid, "name": "project", "mono": "PR", "color": rows[0]["color"],
                                 "kind": "session", "since": w["waiting_at"], "attach": "8d4e0402"}])
        self.assertEqual(data["idle"], [])
        self.assertNotIn(self.BG.encode(), body)  # the short id only, and only for a background session

    def test_the_attach_command_only_for_a_background_session_with_a_hex_id(self):
        for name, extra, attach in ((self.BG, {}, None), (self.BG, {"bg": "yes"}, None),
                                    ("ZZZZZZZZ-151b-464f", {"bg": True}, None), ("8D4E0402-aaaa", {"bg": True}, None),
                                    ("8d4e04", {"bg": True}, None), ("abcdef01", {"bg": True}, "abcdef01")):
            for path in (self.home / "sessions").glob("*.json"):
                self.write(path, {})  # out of the way, not removed: an empty object is no session
            self.waits(name, **extra)
            if not self.server:
                self.start()
            rows = [r for r in self.page()[0]["waiting"] if r["kind"] == "session"]
            self.assertEqual([r["attach"] for r in rows], [attach], (name, extra))

    def test_an_idle_session_is_in_idle_and_counts_nowhere(self):
        i = self.session("idle-1", state="idle", at=stamp(signoff.IDLE_AFTER + 5), bg=True)
        self.session("fresh-1", state="idle", at=stamp(5))  # its turn ended a moment ago: not idle yet
        self.session("busy-1", state="working", at=stamp(600))  # working for ten minutes: not idle
        with mock.patch.object(signoff, "STATE_EVERY", 0.05):
            self.start()
            state = json.loads((self.home / "state.json").read_text(encoding="utf-8"))
            data, body = self.page()
        self.assertEqual(state["waiting"], 1)  # the todo item alone
        self.assertEqual([r["kind"] for r in data["waiting"]], ["todo"])
        self.assertEqual(data["idle"], [{"project": self.pid, "name": "project", "mono": "PR",
                                         "color": data["idle"][0]["color"], "since": i["at"], "attach": None}])

    def test_a_waiting_session_is_not_idle_too(self):
        self.session("both-1", state="idle", at=stamp(600), waiting="permission", waiting_at=stamp(300))
        self.start()
        data = self.page()[0]
        self.assertEqual(([r["kind"] for r in data["waiting"]], data["idle"]), (["todo", "session"], []))

    def test_dead_old_ended_and_unknown_sessions_show_nowhere(self):
        self.waits("dead-1", pid=dead_pid())
        self.waits("nopid-1", pid=None)
        self.waits("ended-1", state="ended")
        self.waits("other-1", project="0" * 16)  # a project the hooks never noted
        old = self.waits("old-1")
        os.utime(str(self.home / "sessions" / "old-1.json"), (time.time() - signoff.SESSIONS_MAX_AGE - 60,) * 2)
        self.session("idle-dead", state="idle", at=stamp(600), pid=dead_pid())
        self.session("idle-ended", state="ended", at=stamp(600))
        self.assertTrue(old)
        self.start()
        data = self.page()[0]
        self.assertEqual(([r["kind"] for r in data["waiting"]], data["idle"]), (["todo"], []))

    def test_broken_session_files_are_skipped(self):
        for n, extra in enumerate(({"waiting": "other", "waiting_at": stamp(5)}, {"waiting": "permission"},
                                   {"waiting": "permission", "waiting_at": "yesterday"},
                                   {"waiting": "permission", "waiting_at": 7}, {"waiting": ["permission"]},
                                   {"at": "2026-99-99T00:00:00Z", "state": "idle"}, {"pid": "1"}, {"pid": True})):
            self.session("bad-%d" % n, **extra)
        (self.home / "sessions" / "junk.json").write_bytes(b"\xff not json")
        self.start()
        data = self.page()[0]
        self.assertEqual(([r["kind"] for r in data["waiting"]], data["idle"]), (["todo"], []))

    def test_no_message_prompt_or_session_text_reaches_the_page(self):
        self.waits(self.BG, bg=True, message="SENTINEL-MSG", prompt="SENTINEL-PROMPT", transcript_path="SENTINEL-PATH",
                   tool_input={"command": "SENTINEL-INPUT"})
        self.session("idle-1", state="idle", at=stamp(600), message="SENTINEL-MSG")
        self.start()
        data, body = self.page()
        self.assertEqual(len(data["idle"]), 1)
        for s in (b"SENTINEL", self.BG.encode(), b"idle-1"):
            self.assertNotIn(s, body)
        for row in data["waiting"][1:] + data["idle"]:
            self.assertLessEqual(set(row), {"project", "name", "mono", "color", "kind", "since", "attach"})


def question(qid, rec="a", **extra):
    item = {"id": qid, "title": "Question " + qid,
            "options": [{"id": "a", "label": "Alpha"}, {"id": "b", "label": "Beta"}, {"id": "c", "label": "Gamma"}]}
    if rec is not None:
        item["recommended"] = rec
    item.update(extra)
    return item


def one_round(*items, **extra):
    data = {"version": 1, "title": "Ship it", "tab": "R3", "items": list(items)}
    data.update(extra)
    return {k: v for k, v in data.items() if v is not None}


def record(answers, done=False):
    return {"version": 1, "done": done, "saved": "2026-01-01T12:00:00Z", "answers": answers, "free_text": "kept"}


class InboxControl(InboxTest):
    """The control socket with runs waiting on rounds: ops it no longer knows get the unknown-request reply, a plain
    open ignores keys it does not know, and a bad line gets an error. Its helpers serve the other socket tests."""

    cases = 0

    def case(self, rounds, answers=None):
        """A topic folder of these rounds (answers files by round number), and a run waiting on it."""
        InboxControl.cases += 1
        topic = self.root / ".signoff" / ("case-%d" % InboxControl.cases)
        topic.mkdir(parents=True)
        names = []
        for n, data in enumerate(rounds, 1):
            names.append("round-%02d.json" % n)
            (topic / names[-1]).write_text(json.dumps(data), encoding="utf-8")
        for n, rec in (answers or {}).items():
            (topic / ("round-%02d.answers.json" % n)).write_text(json.dumps(rec), encoding="utf-8")
        e = entry(topic, names, project=self.pid)
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        return topic, e

    def end(self, e):
        """The run ends and its agent takes the answers in (an outcome file per round): nothing of it waits now. A
        run that ends without them leaves orphan rounds (InboxOrphan)."""
        self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, ended=signoff.utc_now()))
        for n in e["rounds"]:
            (Path(e["folder"]) / n).with_suffix(".outcome.json").write_text("{}", encoding="utf-8")

    def ctl(self, op, **fields):
        return signoff.inbox_request(self.home, op, **fields)

    def files(self, *folders):
        """Every file's bytes under these folders, by path."""
        found = {}
        for folder in folders:
            for base, _, names in os.walk(str(folder)):
                for n in names:
                    p = os.path.join(base, n)
                    if not stat.S_ISSOCK(os.lstat(p).st_mode):
                        found[p] = Path(p).read_bytes()
        return found

    def test_offer_and_accept_are_unknown_requests(self):
        topic, e = self.case([one_round(question("q"))])
        self.start()
        unknown = self.ctl("ping")
        self.assertEqual(sorted(unknown), ["error"])
        self.assertTrue(unknown["error"].startswith("unknown request"), unknown)
        for op, fields in (("offer", {}), ("accept", {"offer": "0" * 32}),
                           ("accept", {"offer": "0" * 32, "choice": "a"})):
            self.assertEqual(self.ctl(op, **fields), unknown, op)
        for word in ("offer", "accept", "http", "127.0.0.1"):
            self.assertNotIn(word, unknown["error"])
        self.assertFalse((topic / "round-01.answers.json").exists())

    def test_app_target_and_app_open_are_unknown_requests(self):
        # A 0.4.0 panel's App press keeps a row's numbers, then its app takes them: here with a live round the
        # numbers name, so an inbox that still kept them would hand out that item's link.
        topic, e = self.case([one_round(question("q"), question("r"))])
        self.start()
        rows = self.ctl("rows", project=self.pid)
        self.assertEqual([(r["run"], r["round"]) for r in rows["rounds"]], [(0, 0)])
        unknown = self.ctl("ping")
        target = {"run": 0, "round": 0, "item": 1, "inbox": rows["inbox"]}
        for op, fields in (("app_target", target), ("app_open", {}), ("app_target", {}),
                           ("app_open", {"offer": "f" * 32})):
            self.assertEqual(self.ctl(op, **fields), unknown, (op, fields))
        for word in ("app_target", "app_open", "http", "127.0.0.1"):
            self.assertNotIn(word, unknown["error"])
        self.assertFalse((topic / "round-01.answers.json").exists())

    def test_elsewhere_is_an_unknown_request(self):
        # The op signoff elsewhere sent, naming a live round: an inbox that still knew it would mark the item.
        topic, e = self.case([one_round(question("q"), question("r"))])
        self.start()
        unknown = self.ctl("ping")
        before = self.files(topic)
        good = {"round": str(topic / "round-01.json"), "item": "q", "note": "said in chat"}
        for fields in (good, {}, dict(good, note="x" * 600), dict(good, more=True)):
            self.assertEqual(self.ctl("elsewhere", **fields), unknown, fields)
        for word in ("elsewhere", "note", "http", "127.0.0.1"):
            self.assertNotIn(word, unknown["error"])
        self.assertEqual(self.files(topic), before)
        self.assertFalse((topic / "round-01.answers.json").exists())

    def test_a_plain_open_ignores_keys_it_does_not_know(self):
        # A 0.3.0 or 0.4.0 app opens a banner's item with {"op": "open", "offer": <id>}: it gets the list.
        self.case([one_round(question("q"))])
        self.start()
        for fields in ({"offer": "f" * 32}, {"offer": 7}, {"offer": None}, {"offer": "f" * 32, "x": [1]}, {"x": 1}):
            reply = self.ctl("open", **fields)
            self.assertEqual(sorted(reply), ["link"], fields)
            resp, _ = self.request("GET", reply["link"].split(self.host, 1)[1])
            self.assertEqual(resp.status, 303)
            self.assertRegex(resp.getheader("Location"), r"^/[A-Za-z0-9_-]+/$", fields)

    def test_an_inbox_save_checks_line_notes(self):
        attached = {"attachments": [{"src": "change.diff"}], "lines": True}
        topic, e = self.case([one_round(question("q", **attached))])
        (topic / "change.diff").write_text("--- a/x\n+++ b/x\n", encoding="utf-8")
        self.start()
        start, page = self.open()
        good = {"att": 0, "line": 2, "quote": "+++ b/x", "comment": "why here?"}

        def body(*notes):
            return {"answers": {"q": {"choice": "a", "comment": "", "source": "user", "lines": list(notes)}},
                    "free_text": "", "done": False}
        resp, data = self.save(page, body(good, dict(good, line=3)))
        self.assertEqual(resp.status, 400)
        self.assertIn(b"line note 2 ", data)
        self.assertFalse((topic / "round-01.answers.json").exists())
        self.assertEqual(self.save(page, body(good))[0].status, 200)
        saved = json.loads((topic / "round-01.answers.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["answers"]["q"]["lines"], [good])

    def test_rows_titles_are_one_line_and_capped(self):
        hostile = "Line one\nline two ‮evil\u0000 " + "x" * 500
        item = question("q", title=hostile)
        self.case([one_round(item, title=hostile, tab=None)])
        self.start()
        (r,) = self.ctl("rows", project=self.pid)["rounds"]
        for key, text in (("round", r["title"]), ("question", r["items"][0]["title"])):
            self.assertLessEqual(len(text), signoff.MAX_PANEL_TEXT, key)
            self.assertFalse(any(c in text for c in "\n ‮\u0000"), (key, text))
        self.assertTrue(r["items"][0]["title"].startswith("Line one line two  evil"))

    def test_hostile_requests_get_an_error(self):
        self.case([one_round(question("q"))])
        self.start()
        for raw in (b'{"op": "open"' + b" " * 5000 + b"}\n", b'{"op": ["open"]}\n', b'{"op": "rows", "project": '
                    + json.dumps(self.pid).encode() + b", " + b'"x": "' + b"y" * 5000 + b'"}\n'):
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(5)
                s.connect(str(self.home / "inbox.sock"))
                s.sendall(raw)
                reply = json.loads(s.makefile("rb").readline())
            self.assertEqual(sorted(reply), ["error"], raw[:30])
        self.assertEqual(len(self.ctl("rows", project=self.pid)["rounds"]), 1)  # nothing written, the inbox goes on
        self.assertFalse(any(p.name.endswith(".answers.json") for p in (self.root / ".signoff").rglob("*")))


class InboxRows(InboxTest):
    """The mod's panel list, asked for on the control socket: a project's open rounds of live runs and their items
    not decided yet, by number, with one-line titles. No path, link or token."""

    case, end, ctl = InboxControl.case, InboxControl.end, InboxControl.ctl

    def rows(self, project=None):
        return self.ctl("rows", project=self.pid if project is None else project)

    def test_open_rounds_and_their_undecided_items_by_number(self):
        _, other = self.case([one_round(question("x"), title="Elsewhere", tab=None)])
        self.write(self.home / "runs" / (other["run"] + ".json"),
                   dict(other, project="f" * 16, started="2026-01-01T00:00:00Z"))  # another project's, group 0
        topic, e = self.case([one_round(question("q")), one_round(question("q"), question("r"), question("s"),
                                                                  title="Second", tab=None)],
                             {1: record({}, done=True), 2: record({"q": {"choice": "b", "source": "user"},
                                                                   "s": {"choice": "a", "source": "recommended"}})})
        self.start()
        start, page = self.open()
        reply = self.rows()
        self.assertEqual(reply, {"rounds": [{"run": 1, "round": 1, "title": "Second", "item": 1, "items": [
            {"item": 1, "title": "Question r"}, {"item": 2, "title": "Question s"}]}], "more": 0,
            "inbox": self.server.number})
        raw = json.dumps(reply)
        for text in (str(self.home), str(self.root), str(topic), "round-0", e["run"], page.strip("/"), start,
                     "127.0.0.1", "/b/", "http"):
            self.assertNotIn(text, raw)
        empty = {"rounds": [], "more": 0, "inbox": self.server.number}
        self.assertEqual(self.rows("0" * 16), empty)  # a project with no run
        self.end(e)
        self.assertEqual(self.rows(), empty)  # a run that ended is gone

    def test_a_settled_item_is_listed_only_once_it_holds_another_choice(self):
        self.case([one_round(question("s", settled="round-01"), question("q"), question("t", settled="round-01"))],
                  {1: record({"t": {"choice": "b", "source": "recommended"}})})
        self.start()
        (r,) = self.rows()["rounds"]
        self.assertEqual((r["item"], r["items"]), (1, [{"item": 1, "title": "Question q"},
                                                       {"item": 2, "title": "Question t"}]))

    def test_a_number_item_is_listed_until_decided(self):
        num = {"id": "n", "title": "How many?", "select": "number", "range": {"min": 0, "max": 5}, "recommended": 2}
        self.case([one_round(question("q"), dict(num), dict(num, id="z", title="Zero"))],
                  {1: record({"n": {"choice": 2, "source": "recommended"}, "z": {"choice": 0, "source": "user"}})})
        self.start()
        (r,) = self.rows()["rounds"]
        self.assertEqual(r["items"], [{"item": 0, "title": "Question q"}, {"item": 1, "title": "How many?"}])

    def test_which_items_are_decided(self):
        # Decided: a choice that is not null or an empty list, from a source other than recommended; a settled item
        # while it holds its recommendation; a recommendation the user unticked.
        cases = [
            ("decided by the user", [question("q"), question("r")],
             {"q": {"choice": "b", "comment": "", "source": "user"}}, ["r"]),
            ("decided by Accept all, or with no source", [question("q"), question("r"), question("s")],
             {"q": {"choice": "a", "source": "accept_all"}, "r": {"choice": "a"}}, ["s"]),
            ("skipped is decided", [question("q", skippable=True), question("r")],
             {"q": {"choice": "_skip", "source": "user"}}, ["r"]),
            ("only preselected is not", [question("q"), question("r")],
             {"q": {"choice": "a", "source": "recommended"}}, ["q", "r"]),
            ("an untouched item without a recommendation", [question("q"), question("n", rec=None)],
             {"n": {"choice": None, "source": "user"}}, ["q", "n"]),
            ("none picked by the user on a recommended many item",
             [question("m", select="many", rec=["a"]), question("q")], {"m": {"choice": [], "source": "user"}}, ["q"]),
            ("the only item, none picked by the user", [question("m", select="many", rec=["a"])],
             {"m": {"choice": [], "source": "user"}}, []),
            ("an untouched many item without a recommendation",
             [question("m", select="many", rec=None), question("q")], {"m": {"choice": [], "source": "user"}},
             ["m", "q"]),
            ("a settled item, unsaved", [question("s", settled="round-01"), question("q")], None, ["q"]),
            ("a settled item left at its recommendation", [question("s", settled="round-01"), question("q")],
             {"s": {"choice": "a", "source": "recommended"}}, ["q"]),
            ("a settled many item, the same options in another order",
             [question("s", select="many", rec=["a", "c"], settled="round-01"), question("q")],
             {"s": {"choice": ["c", "a"], "source": "recommended"}}, ["q"]),
            ("a settled number item at its recommendation",
             [{"id": "n", "title": "How many?", "select": "number", "range": {"min": 0, "max": 5}, "recommended": 2,
               "settled": "round-01"}, question("q")], {"n": {"choice": 2.0, "source": "recommended"}}, ["q"]),
            ("a settled item changed but only preselected", [question("s", settled="round-01"), question("q")],
             {"s": {"choice": "b", "source": "recommended"}}, ["s", "q"]),
            ("only settled items", [question("s", settled="round-01")], None, []),
            ("an old file's mark of the agent's, no choice", [question("q"), question("r")],
             {"q": {"choice": None, "comment": "said in chat", "source": "elsewhere"}}, ["q", "r"]),
        ]
        self.start()
        for label, items, answers, want in cases:
            with self.subTest(label):
                topic, e = self.case([one_round(*items)], answers and {1: record(answers)})
                (r,) = self.rows()["rounds"]
                self.assertEqual([items[i["item"]]["id"] for i in r["items"]], want)
                self.end(e)

    def test_oldest_run_first_and_a_held_round_listed(self):
        topic, older = self.case([one_round(question("o"), tab="Older")])
        self.write(self.home / "runs" / (older["run"] + ".json"), dict(older, started="2026-01-01T00:00:00Z"))
        self.case([one_round(question("n"), tab="Newer")])
        held = signoff.try_lock(topic / "round-01.json.lock")  # another run holds the older one's round
        self.addCleanup(signoff.unlock, held)
        self.start()
        self.assertEqual([r["title"] for r in self.rows()["rounds"]], ["Older", "Newer"])
        self.assertEqual([g.writable for g in self.server.groups], [False, True])

    def test_titles_are_one_line_capped_with_the_folder_and_home_replaced(self):
        home = os.path.expanduser("~")
        self.case([one_round(question("q", title="See %s/notes and %s/x%s%s" % (self.root, home, chr(0x202E), chr(0x2028))),
                             question("r", title="z" * 300), question("u", title="See https://example.com/x"),
                             tab="Tab\x1b[31m\nred")])
        self.start()
        (r,) = self.rows()["rounds"]
        self.assertEqual(r["title"], "Tab [31m red")
        self.assertEqual([i["title"] for i in r["items"]], ["See project/notes and ~/x  ", "z" * 200,
                                                            "See https://example.com/x"])  # a URL stays

    def test_a_waiting_round_row_carries_its_context_as_one_line_and_nothing_else_does(self):
        # The agent's own line, shown on its round's row the way the rows' titles are: folder and home replaced (the
        # load already refused a format character). Answered rows and the mod's rows do not get it.
        home = os.path.expanduser("~")
        line = "feat/x in %s, notes in %s/n" % (self.root, home)
        signoff.MAX_CONTEXT, kept = 1000, signoff.MAX_CONTEXT  # a long temporary folder must not trip the load
        self.addCleanup(setattr, signoff, "MAX_CONTEXT", kept)
        _, first = self.case([one_round(question("q"), context="answered on its page")])
        self.write(self.home / "runs" / (first["run"] + ".json"), dict(first, started="2026-01-01T00:00:00Z"))
        self.case([one_round(question("r"), context=line)])
        self.start()
        page = self.open()[1]
        self.view(page)
        resp, body = self.save(page, {"answers": {"q": {"choice": "a", "source": "user"}}, "free_text": "",
                                      "done": True})  # group 0: the first run
        self.assertEqual(resp.status, 200, body)
        data = self.view(page)[0]
        (w,) = [w for w in data["waiting"] if w["kind"] == "round"]
        self.assertEqual(w["context"], "feat/x in project, notes in ~/n")
        self.assertTrue(data["sent"])
        self.assertTrue(all("context" not in s for s in data["sent"]))
        self.assertNotIn("feat/x", json.dumps(self.rows()))

    def test_a_bad_project_is_refused(self):
        self.case([one_round(question("q"))])
        self.start()
        for bad in (None, 12, "", "0" * 15, "0" * 17, "A" * 16, ["0" * 16]):
            self.assertEqual(sorted(self.ctl("rows", project=bad)), ["error"], bad)
        self.assertEqual(sorted(self.ctl("rows")), ["error"])

    def test_the_reply_fits_one_control_line_however_many_items(self):
        astral = "\U0001F600" * 300  # 12 bytes each once JSON-escaped
        self.case([one_round(*[question("q%d" % k, title=astral) for k in range(80)], tab=astral)] * 3)
        self.start()
        reply = self.rows()  # inbox_request reads one line of 64 KiB at most
        listed = len(reply["rounds"]) + sum(len(r["items"]) for r in reply["rounds"])
        self.assertEqual(listed, signoff.MAX_ROWS)
        self.assertEqual(listed + reply["more"], 3 + 3 * 80)
        self.assertLess(len(json.dumps(reply)), 65536 - 1024)


class InboxPlan(InboxTest):
    """signoff plan's fixed round, as the inbox shows it: a row in Waiting on you and in the panel's rows, with no
    recommendation."""

    ctl = InboxControl.ctl

    def plan_topic(self, data):
        topic = self.root / ".signoff" / "2026-11-15-plan-abcd1234"
        topic.mkdir(parents=True)
        (topic / "plan-01.md").write_bytes(b"# Plan\n\n1. Do the thing\n")
        (topic / "round-01.json").write_text(json.dumps(data), encoding="utf-8")
        e = entry(topic, ["round-01.json"], project=self.pid)
        self.write(self.home / "runs" / (e["run"] + ".json"), e)

    def test_the_plan_round_waits_on_you_and_is_listed(self):
        self.assertNotIn("recommended", signoff.plan_round(1)["items"][0])
        self.plan_topic(signoff.plan_round(1))
        self.start()
        data, _ = self.view(self.open()[1])
        self.assertIn(("round", "Plan review"), [(w["kind"], w.get("title")) for w in data["waiting"]])
        reply = self.ctl("rows", project=self.pid)
        self.assertEqual([(r["title"], r["item"], r["items"]) for r in reply["rounds"]],
                         [("Plan", 0, [{"item": 0, "title": "Approve this plan?"}])])


class InboxHeld(InboxTest):
    """A round another process holds the lock of is shown read only, and taken in at the inbox's next look once that
    process lets go."""

    case = InboxControl.case

    def test_the_inbox_takes_in_a_round_lock_let_go_at_its_next_look(self):
        topic, _ = self.case([one_round(question("q"))])
        fd = signoff.try_lock(topic / "round-01.json.lock")  # another process's, for a moment
        self.start()
        start, page = self.open()
        view, _ = self.view(page + "run/0/")
        self.assertTrue(view["held"])  # shown read only for now
        signoff.unlock(fd)
        self.server._look()
        view, _ = self.view(page + "run/0/")
        self.assertFalse(view["held"])  # never read only for good


class InboxTarget(InboxTest):
    """The mod's panel row, opened: `open` with run, round, item (numbers from `rows`) and the inbox's own number
    gives a start link to that item in Focus on its run's page. Anything gone, done, out of range or from another
    inbox gives the list's link; a malformed target is refused."""

    case, end, ctl = InboxControl.case, InboxControl.end, InboxControl.ctl

    def at(self, e, started):
        self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, started=started))

    def landing(self, **target):
        """Where a start link from `open` with these fields sends its first visit, after the page token."""
        link = self.ctl("open", **target)["link"]
        resp, _ = self.request("GET", link.split(self.host, 1)[1])
        self.assertEqual(resp.status, 303)
        page, _, rest = resp.getheader("Location").partition("/run/")
        self.assertRegex(page, r"^/[A-Za-z0-9_-]+")
        return rest or None  # None: the list

    def test_a_target_opens_that_item_in_focus_and_anything_else_the_list(self):
        _, done = self.case([one_round(question("d"))], {1: record({}, done=True)})  # group 0: its only round is done
        self.at(done, "2026-01-01T00:00:00Z")
        topic, e = self.case([one_round(question("q")), one_round(question("p"), question("r"), tab=None)])
        held = signoff.try_lock(topic / "round-02.json.lock")  # another run holds round 2: listed, so it opens
        self.addCleanup(signoff.unlock, held)
        self.start()
        k = self.ctl("rows", project=self.pid)["inbox"]
        self.assertEqual(k, self.server.number)
        self.assertEqual(self.landing(run=1, round=1, item=1, inbox=k), "1/#focus=1/r")
        self.assertEqual(self.landing(run=1, round=0, item=0, inbox=k), "1/#focus=0/q")
        for gone in ({"run": 1, "round": 1, "item": 2}, {"run": 1, "round": 2, "item": 0},
                     {"run": 2, "round": 0, "item": 0}, {"run": 0, "round": 0, "item": 0},
                     {"run": 1, "round": 0, "item": 0, "inbox": k % (2 ** 31 - 1) + 1}):
            self.assertIsNone(self.landing(**dict({"inbox": k}, **gone)), gone)
        (topic / "round-01.answers.json").write_text(json.dumps(record({}, done=True)), encoding="utf-8")
        self.assertIsNone(self.landing(run=1, round=0, item=0, inbox=k))  # done since the inbox started
        self.end(e)
        self.assertIsNone(self.landing(run=1, round=0, item=0, inbox=k))  # its run ended
        self.case([one_round(question("q"))])  # a new run: a new number, never the old one again
        self.assertIsNone(self.landing(run=1, round=0, item=0, inbox=k))
        self.assertEqual(self.landing(run=2, round=0, item=0, inbox=k), "2/#focus=0/q")

    def test_a_number_from_an_inbox_before_a_restart_gives_the_list(self):
        _, first = self.case([one_round(question("a"))])
        self.at(first, "2026-01-01T00:00:00Z")
        _, second = self.case([one_round(question("b"))])
        self.at(second, "2026-01-02T00:00:00Z")
        self.start()
        k = self.ctl("rows", project=self.pid)["inbox"]
        self.assertEqual(self.landing(run=1, round=0, item=0, inbox=k), "1/#focus=0/b")
        self.stop()
        self.end(first)
        self.case([one_round(question("c"))])
        self.start()  # the same numbers now name other runs: 0 is b's, 1 is c's
        self.assertNotEqual(self.server.number, k)
        self.assertIsNone(self.landing(run=1, round=0, item=0, inbox=k))
        k = self.ctl("rows", project=self.pid)["inbox"]
        self.assertEqual(self.landing(run=1, round=0, item=0, inbox=k), "1/#focus=0/c")

    def test_a_malformed_target_is_refused(self):
        self.case([one_round(question("q"))])
        self.start()
        good = {"run": 0, "round": 0, "item": 0, "inbox": self.server.number}
        bad = [{"run": 0}, {"run": 0, "round": 0, "item": 0}, {"round": 0, "item": 0, "inbox": good["inbox"]},
               dict(good, offer="f" * 32)]
        for key in good:
            for value in (True, False, -1, 0.0, 1.5, "0", None, [0], 2 ** 31, 10 ** 400):
                bad.append(dict(good, **{key: value}))
        for target in bad:
            self.assertEqual(sorted(self.ctl("open", **target)), ["error"], target)
        self.assertEqual(self.landing(**good), "0/#focus=0/q")
        self.assertIsNone(self.landing())  # a plain open, as before

    def test_a_round_row_opens_its_first_open_item_else_its_last(self):
        self.case([one_round(question("p"), question("q"), question("r"), tab=None),
                   one_round(question("s"), question("t"), tab=None)],
                  {1: record({"p": {"choice": "a", "source": "user"}}),
                   2: record({"s": {"choice": "a", "source": "user"}, "t": {"choice": "b", "source": "user"}})})
        self.start()
        first, second = self.ctl("rows", project=self.pid)["rounds"]
        self.assertEqual((first["item"], [i["item"] for i in first["items"]]), (1, [1, 2]))
        # All decided, not Done: the last item, whose Focus shows the round's tail (its Accept and Done) under it.
        self.assertEqual((second["item"], second["items"]), (1, []))
        k = self.server.number
        self.assertEqual(self.landing(run=0, round=1, item=second["item"], inbox=k), "0/#focus=1/t")


class InboxLife(InboxTest):
    def test_one_inbox_per_home(self):
        self.start()
        with self.assertRaises(signoff.DecisionsError):
            signoff.InboxServer(self.home)

    def test_a_runs_brief_probe_does_not_stop_the_inbox(self):
        self.write(self.home / "inbox.lock", {})
        fd = signoff.try_lock(self.home / "inbox.lock")
        threading.Timer(0.2, signoff.unlock, args=(fd,)).start()  # a run's probe holds it for a moment
        self.start()

    def test_inbox_json_and_the_control_socket(self):
        self.start()
        info = json.loads((self.home / "inbox.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(info), ["control", "port", "schema", "server_id"])
        self.assertEqual((info["schema"], info["port"], info["control"]), (1, self.port, str(self.home / "inbox.sock")))
        self.assertEqual(stat.S_IMODE((self.home / "inbox.json").stat().st_mode), 0o600)
        sock = self.home / "inbox.sock"
        self.assertTrue(stat.S_ISSOCK(sock.lstat().st_mode))
        self.assertEqual(stat.S_IMODE(sock.lstat().st_mode), 0o600)
        link = signoff.inbox_request(self.home, "open")["link"]
        self.assertTrue(link.startswith("http://%s/b/" % self.host), link)
        self.assertEqual(self.request("GET", link.split(self.host, 1)[1])[0].status, 303)
        for raw in (b"nope\n", b'{"op": "token"}\n', b'["open"]\n', b"x" * 5000):
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(5)
                s.connect(str(sock))
                s.sendall(raw)
                reply = s.recv(65536)
            self.assertNotIn(b"http", reply, raw[:20])
            self.assertIn(b"error", reply, raw[:20])

    def test_a_dead_socket_is_replaced_and_nothing_else_is(self):
        dead = socket.socket(socket.AF_UNIX)
        dead.bind(str(self.home / "inbox.sock"))
        dead.close()  # a crashed inbox leaves its socket file: nothing listens on it
        self.start()
        self.assertIn("link", signoff.inbox_request(self.home, "open"))
        self.stop()
        (self.home / "inbox.sock").rename(self.home / "moved.sock")
        (self.home / "inbox.sock").write_bytes(b"mine")  # not a socket: never removed
        with self.assertRaises(signoff.DecisionsError):
            signoff.InboxServer(self.home)
        self.assertEqual((self.home / "inbox.sock").read_bytes(), b"mine")


class InboxOrphan(InboxTest):
    """An orphan round (AD-24): open, listed by a recent project run that is gone, and by no live run. The inbox
    shows, counts and writes it as it does a live run's, under one group number for as long as it is orphan."""

    case, ctl = InboxControl.case, InboxControl.ctl

    def rounds_waiting(self, page):
        return [w for w in self.view(page)[0]["waiting"] if w["kind"] == "round"]

    def locked(self, name="round-01.json"):
        """Whether another open file holds the round's lock (flock is per open file, so this process counts)."""
        fd = signoff.try_lock(self.topic / (name + ".lock"))
        if fd is not None:
            signoff.unlock(fd)
        return fd is None

    def shown_counted_and_writable(self, e):
        start, page = self.open()
        rows = self.rounds_waiting(page)
        self.assertEqual([(w["title"], w["group"], w["orphan"], w["held"]) for w in rows],
                         [("Round 1", 0, True, False), ("Round 2", 0, True, False)])
        self.assertEqual(rows[0]["since"], e["started"])
        state = json.loads((self.home / "state.json").read_text(encoding="utf-8"))
        self.assertEqual((state["waiting"], state["projects"]),
                         (3, [{"id": self.pid, "monogram": "PR", "waiting": 3}]))  # two rounds and the todo item
        self.assertTrue(self.locked())
        resp, out = self.save(page, ANSWER)
        self.assertEqual(resp.status, 200, out)
        saved = json.loads((self.topic / "round-01.answers.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["answers"]["layout"]["comment"], "from the inbox")
        self.stop()
        self.assertFalse(self.locked())  # the inbox lets go when it stops

    def test_a_dead_project_run_leaves_its_open_rounds_waiting_counted_and_writable(self):
        e = self.register(pid=dead_pid())
        self.start()
        self.shown_counted_and_writable(e)

    def test_an_ended_project_run_leaves_them_too(self):
        e = self.register(ended=signoff.utc_now())  # Ctrl+C or the idle timeout: the round still waits
        self.start()
        self.shown_counted_and_writable(e)

    def test_no_orphan_without_a_project_with_an_outcome_done_old_or_gone(self):
        def runs(folder, names, **kw):
            e = entry(folder, names, pid=dead_pid(), **kw)
            self.write(self.home / "runs" / (e["run"] + ".json"), e)
            return e

        runs(self.topic, ["round-01.json", "round-02.json"], project=None)  # outside any project
        (self.topic / "round-01.outcome.json").write_text("{}", encoding="utf-8")
        runs(self.topic, ["round-01.json"], project=self.pid)  # its outcome is there
        (self.topic / "round-02.answers.json").write_text(json.dumps(done_record()), encoding="utf-8")
        runs(self.topic, ["round-02.json"], project=self.pid)  # answered with Done
        runs(self.root / ".signoff" / "moved-away", ["round-01.json"], project=self.pid)  # its folder is gone
        old = self.topic.parent / "old"
        old.mkdir()
        (old / "round-01.json").write_bytes((self.topic / "round-01.json").read_bytes())
        stale = runs(old, ["round-01.json"], project=self.pid)
        os.utime(str(self.home / "runs" / (stale["run"] + ".json")), (time.time() - signoff.RUNS_MAX_AGE - 60,) * 2)
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            server = self.start()
            start, page = self.open()
            for _ in range(3):  # several looks: nothing is made, so no group number is used up
                self.assertEqual(self.rounds_waiting(page), [])
        self.assertEqual(server.groups, [])
        self.assertEqual(err.getvalue(), "")  # a folder that is gone is not said
        self.assertFalse(self.locked())
        fd = signoff.try_lock(old / "round-01.json.lock")
        self.assertIsNotNone(fd)
        signoff.unlock(fd)

    def test_a_live_run_on_the_same_rounds_keeps_the_group_number(self):
        (self.topic / "round-01.answers.json").write_text(json.dumps(done_record()), encoding="utf-8")
        dead = entry(self.topic, ["round-01.json", "round-02.json"], project=self.pid, pid=dead_pid())
        self.write(self.home / "runs" / (dead["run"] + ".json"), dead)
        server = self.start()
        start, page = self.open()
        self.assertEqual([(w["title"], w["group"], w["orphan"]) for w in self.rounds_waiting(page)],
                         [("Round 2", 0, True)])
        # A new run on the folder lists its open rounds only, as register_run does: the orphan group's key.
        again = entry(self.topic, ["round-02.json"], project=self.pid)
        self.write(self.home / "runs" / (again["run"] + ".json"), again)
        self.assertEqual([(w["group"], w["orphan"]) for w in self.rounds_waiting(page)], [(0, False)])
        self.assertEqual(self.save(page, ANSWER, round_=0)[0].status, 200)  # its one round, round-02
        self.assertTrue(self.locked("round-02.json"))
        # That run is gone too: the group goes on as orphan, still number 0.
        self.write(self.home / "runs" / (again["run"] + ".json"), dict(again, ended=signoff.utc_now()))
        self.assertEqual([(w["group"], w["orphan"]) for w in self.rounds_waiting(page)], [(0, True)])
        self.assertEqual(len(server.groups), 1)

    def test_a_round_file_changed_while_orphan_is_read_again(self):
        e = entry(self.topic, ["round-01.json"], project=self.pid, pid=dead_pid())
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        server = self.start()
        start, page = self.open()
        self.assertEqual([(w["title"], w["group"]) for w in self.rounds_waiting(page)], [("Round 1", 0)])
        data = json.loads((self.topic / "round-01.json").read_text(encoding="utf-8"))
        data["title"] = "Round 1, asked again"
        (self.topic / "round-01.json").write_text(json.dumps(data), encoding="utf-8")
        os.utime(str(self.topic / "round-01.json"), (time.time() + 5,) * 2)  # a new time, however coarse the clock
        self.assertEqual([(w["title"], w["group"]) for w in self.rounds_waiting(page)], [("Round 1, asked again", 1)])
        # A re-run on it takes the new group, so a Done there is for the version that run waits on.
        again = entry(self.topic, ["round-01.json"], project=self.pid)
        self.write(self.home / "runs" / (again["run"] + ".json"), again)
        self.assertEqual([(w["group"], w["orphan"]) for w in self.rounds_waiting(page)], [(1, False)])
        self.assertEqual(self.save(page, dict(ANSWER, done=True), group=1)[0].status, 200)
        saved = json.loads((self.topic / "round-01.answers.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["decisions"]["sha256"],
                         hashlib.sha256((self.topic / "round-01.json").read_bytes()).hexdigest())
        self.assertEqual(len(server.groups), 2)

    def test_a_round_file_changed_while_orphan_then_rerun_gets_a_fresh_group(self):
        e = entry(self.topic, ["round-01.json"], project=self.pid, pid=dead_pid())
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        server = self.start()
        start, page = self.open()
        self.assertEqual(len(self.rounds_waiting(page)), 1)
        data = json.loads((self.topic / "round-01.json").read_text(encoding="utf-8"))
        data["title"] = "Round 1, asked again"
        (self.topic / "round-01.json").write_text(json.dumps(data), encoding="utf-8")
        os.utime(str(self.topic / "round-01.json"), (time.time() + 5,) * 2)
        again = entry(self.topic, ["round-01.json"], project=self.pid)  # no look in between
        self.write(self.home / "runs" / (again["run"] + ".json"), again)
        self.assertEqual([(w["title"], w["group"], w["orphan"]) for w in self.rounds_waiting(page)],
                         [("Round 1, asked again", 1, False)])
        self.assertEqual(len(server.groups), 2)

    def test_a_round_file_of_another_user_is_no_orphan(self):
        server = self.start()  # the home is the user's; the round files then count as another's
        pid = dead_pid()
        with mock.patch.object(signoff.os, "getuid", return_value=os.getuid() + 1):
            self.register(pid=pid)
            server.view()
        self.assertEqual(server.groups, [])
        self.assertFalse(self.locked())

    def test_a_listed_name_that_is_a_link_uses_one_number(self):
        os.symlink("round-01.json", str(self.topic / "round-05.json"))
        e = entry(self.topic, ["round-05.json"], project=self.pid, pid=dead_pid())
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        server = self.start()
        start, page = self.open()
        for _ in range(3):
            self.assertEqual([w["group"] for w in self.rounds_waiting(page)], [0])
        self.assertEqual(len(server.groups), 1)

    def test_a_live_group_whose_run_goes_becomes_orphan_with_its_number(self):
        e = self.register()
        server = self.start()
        start, page = self.open()
        self.assertEqual([(w["group"], w["orphan"]) for w in self.rounds_waiting(page)], [(0, False)] * 2)
        self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, ended=signoff.utc_now()))
        self.assertEqual([(w["group"], w["orphan"]) for w in self.rounds_waiting(page)], [(0, True)] * 2)
        self.assertEqual(self.save(page, ANSWER)[0].status, 200)  # an open tab saves on
        self.assertEqual(len(server.groups), 1)

    def test_the_round_page_says_whether_its_rounds_are_orphan(self):
        e = self.register()
        self.start()
        start, page = self.open()
        self.assertIs(self.view(page + "run/0/")[0]["orphan"], False)
        self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, ended=signoff.utc_now()))
        self.view(page)  # the look
        data = self.view(page + "run/0/")[0]
        self.assertEqual((data["orphan"], data["held"]), (True, False))

    def test_a_live_run_on_other_rounds_ends_the_orphan_group(self):
        self.register(pid=dead_pid())
        server = self.start()
        start, page = self.open()
        self.assertEqual({w["group"] for w in self.rounds_waiting(page)}, {0})
        other = entry(self.topic, ["round-02.json"], project=self.pid)
        self.write(self.home / "runs" / (other["run"] + ".json"), other)
        rows = self.rounds_waiting(page)
        # round-02 is the live run's; round-01, still orphan, is a group of its own.
        self.assertEqual(sorted((w["title"], w["orphan"]) for w in rows), [("Round 1", True), ("Round 2", False)])
        self.assertNotIn(0, {w["group"] for w in rows})
        self.assertEqual(self.save(page, ANSWER, group=0)[0].status, 404)  # the old tab reloads
        for _ in range(3):
            self.rounds_waiting(page)
        self.assertEqual(len(server.groups), 3)  # no number used up per look

    def test_an_orphan_round_answered_with_done_leaves_the_list_and_its_lock(self):
        e = entry(self.topic, ["round-01.json"], project=self.pid, pid=dead_pid())
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        server = self.start()
        start, page = self.open()
        self.assertEqual(len(self.rounds_waiting(page)), 1)
        self.assertTrue(self.locked())
        self.assertEqual(self.save(page, dict(ANSWER, done=True))[0].status, 200)
        self.assertEqual(self.rounds_waiting(page), [])
        self.assertEqual(self.save(page, ANSWER)[0].status, 404)  # the group went with its last open round
        self.assertEqual([s["title"] for s in self.view(page)[0]["sent"]], ["Round 1"])
        self.assertFalse(self.locked())
        for _ in range(3):
            self.rounds_waiting(page)
        self.assertEqual(server.groups, [None])
        self.assertTrue(json.loads((self.topic / "round-01.answers.json").read_text(encoding="utf-8"))["done"])
        deadline = time.monotonic() + 10
        while json.loads((self.home / "state.json").read_text(encoding="utf-8"))["waiting"] != 1:
            self.assertLess(time.monotonic(), deadline, "state.json never counted the round down")
            time.sleep(0.05)

    def test_a_round_file_that_goes_ends_its_orphan_group(self):
        self.register(pid=dead_pid())
        server = self.start()
        start, page = self.open()
        self.assertEqual(len(self.rounds_waiting(page)), 2)
        (self.topic / "round-02.json").rename(self.topic / "moved.json")
        self.assertEqual([(w["title"], w["group"]) for w in self.rounds_waiting(page)], [("Round 1", 1)])
        self.assertFalse(self.locked("round-02.json"))
        for _ in range(3):
            self.rounds_waiting(page)
        self.assertEqual(len(server.groups), 2)

    def test_the_newest_gone_run_owns_a_round(self):
        older = entry(self.topic, ["round-01.json", "round-02.json"], project=self.pid, pid=dead_pid(),
                      started="2026-01-01T00:00:00Z")
        newer = entry(self.topic, ["round-01.json"], project=self.pid, pid=dead_pid(), started="2026-01-02T00:00:00Z")
        for e in (older, newer):
            self.write(self.home / "runs" / (e["run"] + ".json"), e)
        self.start()
        rows = self.rounds_waiting(self.open()[1])
        self.assertEqual(sorted((w["title"], w["since"]) for w in rows),
                         [("Round 1", newer["started"]), ("Round 2", older["started"])])
        self.assertEqual(len({w["group"] for w in rows}), 2)

    def test_a_round_that_cannot_be_read_is_said_once_and_uses_no_number(self):
        (self.topic / "round-02.json").write_text("{", encoding="utf-8")
        self.register(pid=dead_pid())
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            server = self.start()
            start, page = self.open()
            for _ in range(3):
                self.assertEqual(self.rounds_waiting(page), [])
        self.assertEqual(server.groups, [])
        self.assertEqual(len(err.getvalue().splitlines()), 1, err.getvalue())

    def test_a_look_after_close_takes_no_orphan_lock(self):
        self.register(pid=dead_pid())
        server = self.start()
        self.stop()
        server.view()  # a page request still running when the inbox stopped
        self.assertFalse(self.locked())

    def test_a_run_gone_after_close_gets_no_group_and_no_lock(self):
        server = self.start()
        self.stop()
        self.register(pid=dead_pid())
        server.view()
        self.assertFalse(self.locked())
        self.assertEqual([g for g in server.groups if g], [])

    def test_rows_carry_an_orphan_round(self):
        topic, e = self.case([one_round(question("q"))])
        self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, pid=dead_pid()))
        self.start()
        rows = self.ctl("rows", project=self.pid)
        self.assertEqual([(r["run"], r["title"]) for r in rows["rounds"]], [(0, "R3")])
        start, page = self.open()
        self.assertEqual(self.save(page, {"answers": {"q": {"choice": "a", "source": "user"}}, "free_text": "",
                                          "done": True})[0].status, 200)
        self.assertTrue(json.loads((topic / "round-01.answers.json").read_text(encoding="utf-8"))["done"])
        self.assertEqual(self.ctl("rows", project=self.pid)["rounds"], [])

    def test_a_target_outlives_its_run_while_its_round_is_orphan(self):
        topic, e = self.case([one_round(question("q"))])
        self.start()
        k = self.ctl("rows", project=self.pid)["inbox"]
        self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, ended=signoff.utc_now()))  # no outcome
        self.assertEqual([r["run"] for r in self.ctl("rows", project=self.pid)["rounds"]], [0])
        resp, _ = self.request("GET", self.ctl("open", run=0, round=0, item=0, inbox=k)["link"].split(self.host, 1)[1])
        self.assertTrue(resp.getheader("Location").endswith("/run/0/#focus=0/q"), resp.getheader("Location"))
        page = resp.getheader("Location").partition("run/")[0]
        self.assertEqual(self.save(page, {"answers": {"q": {"choice": "a", "source": "user"}}, "free_text": "",
                                          "done": True})[0].status, 200)
        self.assertTrue(json.loads((topic / "round-01.answers.json").read_text(encoding="utf-8"))["done"])

    def test_the_orphan_row_holds_no_path_link_or_token(self):
        e = self.register(pid=dead_pid())
        self.start()
        start, page = self.open()
        data, body = self.view(page)
        for text in (str(self.root), str(self.home), str(self.topic), "round-01.json", e["run"]):
            self.assertNotIn(text.encode(), body)
        row = self.rounds_waiting(page)[0]
        self.assertEqual(sorted(row), ["color", "group", "held", "items", "kind", "mono", "name", "opened", "orphan",
                                       "project", "round", "since", "tab", "title"])
        self.assertTrue(signoff.SAVED_PATTERN.fullmatch(row["opened"]))  # a time, never a path
        self.assertEqual((row["group"], row["round"]), (0, 0))  # numbers for Park, never a path
        state = (self.home / "state.json").read_bytes()
        for text in (str(self.root), self.topic.name, "Round", e["run"]):
            self.assertNotIn(text.encode(), state)

    def test_a_run_that_meets_the_inbox_lock_on_its_rounds_becomes_a_waiter(self):
        # The race: inbox_alive said no inbox, the inbox started, and its orphan group took the round locks.
        self.register(pid=dead_pid())
        self.start()
        self.assertTrue(self.locked())
        err, waited = io.StringIO(), []
        with mock.patch.dict(os.environ, {"SIGNOFF_INBOX_HOME": str(self.home)}), \
                mock.patch.object(signoff, "inbox_alive", side_effect=[False, True]), \
                mock.patch.object(signoff, "_wait", side_effect=lambda rounds, home: waited.append(rounds) or "stopped"), \
                mock.patch.object(sys, "stderr", err):
            code = signoff.main([str(self.topic), "--no-open"])
        self.assertEqual((code, len(waited)), (130, 1), err.getvalue())
        self.assertIn("the inbox is open", err.getvalue())
        self.assertNotIn("open in another Signoff run", err.getvalue())
        # With no inbox at the second look either, the refusal stays as it was.
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"SIGNOFF_INBOX_HOME": str(self.home)}), \
                mock.patch.object(signoff, "inbox_alive", side_effect=[False, False]), \
                mock.patch.object(sys, "stderr", err):
            code = signoff.main([str(self.topic), "--no-open"])
        self.assertEqual(code, 1, err.getvalue())
        self.assertIn("open in another Signoff run or in the inbox", err.getvalue())

    def test_a_rerun_of_the_folder_while_the_inbox_lives_waits_and_takes_the_group(self):
        self.register(pid=dead_pid())
        self.start()
        start, page = self.open()
        self.assertEqual({(w["group"], w["orphan"]) for w in self.rounds_waiting(page)}, {(0, True)})
        run = subprocess.Popen([sys.executable, "-I", "-B", SCRIPT, str(self.topic), "--no-open"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                               encoding="utf-8", env=dict(os.environ, SIGNOFF_INBOX_HOME=str(self.home)))
        self.addCleanup(run.communicate)
        self.addCleanup(run.kill)
        self.assertIn("the inbox is open", run.stderr.readline())
        deadline = time.monotonic() + 20
        while {(w["group"], w["orphan"]) for w in self.rounds_waiting(page)} != {(0, False)}:
            self.assertLess(time.monotonic(), deadline, "the re-run never took the group")
            self.assertIsNone(run.poll())
            time.sleep(0.1)
        run.kill()
        run.wait(10)
        self.assertEqual({(w["group"], w["orphan"]) for w in self.rounds_waiting(page)}, {(0, True)})

    # AD-28: once every round of an orphan group is done, one request file hands the answer to the project.

    def answers(self):
        folder = self.home / "requests" / self.pid
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] \
            if folder.is_dir() else []

    def one_orphan_round(self):
        e = entry(self.topic, ["round-01.json"], project=self.pid, pid=dead_pid())
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        self.start()
        start, page = self.open()
        self.assertEqual(len(self.rounds_waiting(page)), 1)
        return page

    def test_done_on_an_orphan_group_hands_its_answer_over_once_before_the_answers_json(self):
        self.register(pid=dead_pid())
        self.start()
        start, page = self.open()
        self.assertEqual(self.save(page, dict(ANSWER, accepted=True), round_=1)[0].status, 200)
        self.assertEqual(self.save(page, dict(ANSWER, done=True))[0].status, 200)
        self.assertEqual(self.answers(), [])  # round-02 is still open
        order, write = [], signoff.write_atomic

        def spy(path, data):  # the round's files and requests only: the inbox's own look writes state.json too
            if path.parent.name in (self.topic.name, self.pid):
                order.append(path.parent.parent.name if path.parent.name == self.pid else path.name)
            return write(path, data)

        with mock.patch.object(signoff, "write_atomic", spy):
            self.assertEqual(self.save(page, dict(ANSWER, done=True), round_=1)[0].status, 200)
        self.assertEqual(order, ["round-02.answers.md", "requests", "round-02.answers.json"])
        (r,) = self.answers()
        self.assertEqual(sorted(r), ["at", "id", "kind", "ref", "schema", "text"])
        self.assertEqual((r["schema"], r["kind"], r["ref"], r["text"]),
                         (1, "answer", "2026-11-06-inbox / round-01, round-02", signoff.ANSWER_TEXT))
        path = self.home / "requests" / self.pid / (r["id"] + ".json")
        self.assertEqual(signoff.read_request(path), r)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        for folder in (self.home / "requests", path.parent):
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        for text in ("from the inbox", str(self.root), str(self.home), "round-01.json", "http"):
            self.assertNotIn(text, path.read_text(encoding="utf-8"))
        for _ in range(3):  # later looks hand nothing over again
            self.assertEqual(self.rounds_waiting(page), [])
        self.assertEqual(len(self.answers()), 1)
        # Nothing of it on the page, in the panel's rows or in state.json: the todo item alone waits.
        data, body = self.view(page)
        self.assertNotIn(r["id"].encode(), body)
        self.assertEqual([w["kind"] for w in data["waiting"]], ["todo"])
        self.assertEqual(self.ctl("rows", project=self.pid)["rounds"], [])
        deadline = time.monotonic() + 10
        while json.loads((self.home / "state.json").read_text(encoding="utf-8"))["waiting"] != 1:
            self.assertLess(time.monotonic(), deadline, "state.json never counted the rounds down")
            time.sleep(0.05)

    def test_a_folder_name_that_is_not_utf_8_can_be_handed_over(self):
        # On Linux such a name is a lone surrogate in Python: the request is written ASCII-escaped, so it cannot fail.
        r = signoff.write_request(self.home, self.pid, "answer", "topic-\udcff / round-01", signoff.ANSWER_TEXT)
        path = self.home / "requests" / self.pid / (r["id"] + ".json")
        self.assertEqual(signoff.read_request(path)["ref"], "topic-\udcff / round-01")
        self.assertNotIn("\n", signoff.request_line(r))

    def test_the_answer_names_the_runs_rounds_also_one_answered_before_the_group(self):
        # Round 1 was done before this inbox made the group (a restart in between): its answer goes over too.
        (self.topic / "round-01.answers.json").write_text(json.dumps(done_record()), encoding="utf-8")
        self.register(pid=dead_pid())
        self.start()
        start, page = self.open()
        self.assertEqual([w["title"] for w in self.rounds_waiting(page)], ["Round 2"])
        self.assertEqual(self.save(page, dict(ANSWER, done=True))[0].status, 200)
        self.assertEqual([r["ref"] for r in self.answers()], ["2026-11-06-inbox / round-01, round-02"])

    def test_a_done_just_after_the_run_ended_hands_the_answer_over(self):
        # The save looks first, so the group's orphan flag is not the one from the last look.
        e = self.register()
        self.start()
        start, page = self.open()
        self.assertEqual(self.save(page, dict(ANSWER, accepted=True), round_=1)[0].status, 200)
        self.assertEqual(self.save(page, dict(ANSWER, done=True))[0].status, 200)
        self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, ended=signoff.utc_now()))
        self.assertEqual(self.save(page, dict(ANSWER, done=True), round_=1)[0].status, 200)
        self.assertEqual([r["kind"] for r in self.answers()], ["answer"])

    def test_a_done_whose_look_lets_the_group_go_gets_404_and_writes_nothing(self):
        e = self.register()
        self.start()
        start, page = self.open()
        self.assertEqual(self.save(page, dict(ANSWER, accepted=True), round_=1)[0].status, 200)
        InboxControl.end(self, e)  # the run ended and its agent took the rounds in: nothing of it waits now
        resp, body = self.save(page, dict(ANSWER, done=True))
        self.assertEqual((resp.status, body), (404, b"this round is no longer in the inbox; reload the page\n"))
        self.assertFalse((self.topic / "round-01.answers.json").exists())
        self.assertFalse((self.home / "requests").exists())

    def test_a_live_runs_done_hands_nothing_over(self):
        self.register()
        self.start()
        start, page = self.open()
        self.assertEqual(self.save(page, dict(ANSWER, accepted=True), round_=1)[0].status, 200)
        for n in (0, 1):
            self.assertEqual(self.save(page, dict(ANSWER, done=True), round_=n)[0].status, 200)
        self.assertFalse((self.home / "requests").exists())

    def done_then(self, then=None, **left):
        """Done on both rounds while the waiter lives, then its entry as the run left it (left: the keys it got;
        then: what else happens to the entry before the inbox's next look)."""
        e = self.register()
        self.start()
        start, page = self.open()
        self.assertEqual(self.save(page, dict(ANSWER, accepted=True), round_=1)[0].status, 200)
        for n in (0, 1):
            self.assertEqual(self.save(page, dict(ANSWER, done=True), round_=n)[0].status, 200)
        with self.server.scan_lock:  # no look in between, from the inbox's own thread either
            self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, **left))
            if then:
                then(self.home / "runs" / (e["run"] + ".json"))
        for _ in range(3):  # later looks write nothing more
            self.assertEqual(self.rounds_waiting(page), [])
        return page

    def assert_one_answer(self):
        self.assertEqual([(r["kind"], r["ref"], r["text"]) for r in self.answers()],
                         [("answer", "2026-11-06-inbox / round-01, round-02", signoff.ANSWER_TEXT)])

    def test_a_waiter_stopped_after_a_done_in_the_inbox_hands_the_answer_over_once(self):
        # SIGTERM or SIGHUP before the waiter's next look: it exits 130 with nothing on stdout, so the inbox tells.
        self.done_then(ended=signoff.utc_now(), exit=130)
        self.assert_one_answer()
        self.stop()  # a restart: the done rounds are no orphan rounds, so nothing is handed over again
        self.start()
        start, page = self.open()
        for _ in range(3):
            self.assertEqual(self.rounds_waiting(page), [])
        self.assert_one_answer()

    def test_a_killed_waiter_after_a_done_counts_as_stopped(self):
        self.done_then(pid=dead_pid())
        self.assert_one_answer()

    def test_a_waiter_that_exited_0_after_a_done_hands_nothing_over(self):
        self.done_then(ended=signoff.utc_now(), exit=0)
        self.assertFalse((self.home / "requests").exists())

    def test_an_entry_with_no_exit_code_hands_nothing_over_as_before(self):
        self.done_then(ended=signoff.utc_now())  # an older plugin's run: it may have handed over
        self.assertFalse((self.home / "requests").exists())

    def outside_after_the_end(self, code, look_first):
        """Another program finishes both rounds after the run ended with code: before the inbox's next look, or
        after that look made the group orphan."""
        e = self.register(mode="page" if code == 2 else "waiter")
        server = self.start()
        start, page = self.open()
        self.assertEqual(len(self.rounds_waiting(page)), 2)
        with server.scan_lock:  # no look in between, from the inbox's own thread either
            self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, ended=signoff.utc_now(), exit=code))
            if not look_first:
                for n in (1, 2):
                    write_outside(self.topic / ("round-%02d.answers.json" % n), done_record())
        if look_first:
            self.assertEqual([w["orphan"] for w in self.rounds_waiting(page)], [True, True])
            for n in (1, 2):
                write_outside(self.topic / ("round-%02d.answers.json" % n), done_record())
        for _ in range(3):
            self.assertEqual(self.rounds_waiting(page), [])
        self.assert_one_answer()

    def test_a_done_file_from_outside_before_the_next_look_hands_a_stopped_runs_answer_over(self):
        self.outside_after_the_end(130, look_first=False)

    def test_a_done_file_from_outside_after_the_group_went_orphan_hands_it_over_once(self):
        self.outside_after_the_end(130, look_first=True)

    def test_a_timed_out_page_runs_rounds_finished_from_outside_are_handed_over(self):
        self.outside_after_the_end(2, look_first=False)

    def test_a_stopped_waiters_answer_that_cannot_be_written_is_said_and_the_list_goes_on(self):
        e = self.register()
        self.start()
        start, page = self.open()
        self.assertEqual(self.save(page, dict(ANSWER, accepted=True), round_=1)[0].status, 200)
        for n in (0, 1):
            self.assertEqual(self.save(page, dict(ANSWER, done=True), round_=n)[0].status, 200)
        (self.home / "requests").write_text("not a folder", encoding="utf-8")
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, ended=signoff.utc_now(), exit=130))
            for _ in range(3):
                self.assertEqual(self.rounds_waiting(page), [])
        self.assertEqual(err.getvalue().count("cannot hand over"), 1, err.getvalue())

    def test_a_reused_start_link_hands_nothing_over(self):
        # Exit 1 after a Done within DONE_GRACE: the run's stderr tells its agent to doubt the answers. A request
        # would tell it to act on them.
        self.done_then(ended=signoff.utc_now(), exit=1)
        self.assertFalse((self.home / "requests").exists())

    def test_an_exit_code_of_no_known_stop_hands_nothing_over(self):
        self.done_then(ended=signoff.utc_now(), exit=120)  # only 2 and 130 say the run stopped without its answers
        self.assertFalse((self.home / "requests").exists())

    def test_an_exit_code_that_is_not_a_number_hands_nothing_over(self):
        self.done_then(ended=signoff.utc_now(), exit="130")
        self.assertFalse((self.home / "requests").exists())

    def test_the_newest_run_on_the_rounds_decides(self):
        # An older plugin's run on the same rounds ended with no exit code; the run of today stopped with 130.
        older = entry(self.topic, ["round-01.json", "round-02.json"], project=self.pid, started=stamp(120),
                      ended=stamp(100))
        self.write(self.home / "runs" / (older["run"] + ".json"), older)
        self.done_then(ended=signoff.utc_now(), exit=130)
        self.assert_one_answer()

    def test_an_older_run_that_handed_over_while_the_newer_lived_hands_nothing_more_over(self):
        # Two waiters on the same rounds at once: the older one exits 0 with the paths, the newer one stops.
        older = entry(self.topic, ["round-01.json", "round-02.json"], project=self.pid, started=stamp(120),
                      ended=stamp(30), exit=0)
        self.write(self.home / "runs" / (older["run"] + ".json"), older)
        self.done_then(started=stamp(60), ended=signoff.utc_now(), exit=130)
        self.assertFalse((self.home / "requests").exists())

    def test_an_older_run_that_handed_over_before_the_newer_started_does_not_count(self):
        older = entry(self.topic, ["round-01.json", "round-02.json"], project=self.pid, started=stamp(120),
                      ended=stamp(100), exit=0)  # its answers were other ones: these rounds were run again since
        self.write(self.home / "runs" / (older["run"] + ".json"), older)
        self.done_then(ended=signoff.utc_now(), exit=130)
        self.assert_one_answer()

    def test_a_newer_run_that_handed_over_wins_over_an_older_stopped_one(self):
        older = entry(self.topic, ["round-01.json", "round-02.json"], project=self.pid, started=stamp(120),
                      ended=stamp(100), exit=130)
        self.write(self.home / "runs" / (older["run"] + ".json"), older)
        self.done_then(ended=signoff.utc_now(), exit=0)
        self.assertFalse((self.home / "requests").exists())

    def test_a_stopped_run_let_go_with_a_round_still_open_hands_nothing_over(self):
        e = self.register()
        self.start()
        start, page = self.open()
        self.assertEqual(self.save(page, dict(ANSWER, accepted=True), round_=1)[0].status, 200)
        self.assertEqual(self.save(page, dict(ANSWER, done=True))[0].status, 200)  # round-02 stays open
        (self.topic / "round-02.outcome.json").write_text("{}", encoding="utf-8")  # so it is no orphan round
        self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, ended=signoff.utc_now(), exit=130))
        for _ in range(3):
            self.assertEqual(self.rounds_waiting(page), [])
        self.assertEqual(self.server.groups, [None])  # let go
        self.assertFalse((self.home / "requests").exists())

    def test_an_expired_entry_hands_nothing_over(self):
        old = time.time() - signoff.RUNS_MAX_AGE - 60
        self.done_then(ended=signoff.utc_now(), exit=130, then=lambda path: os.utime(str(path), (old, old)))
        self.assertEqual(self.server.groups, [None])
        self.assertFalse((self.home / "requests").exists())

    def test_an_invalid_entry_hands_nothing_over(self):
        self.done_then(ended=signoff.utc_now(), exit=130, pid="not a pid")
        self.assertEqual(self.server.groups, [None])
        self.assertFalse((self.home / "requests").exists())

    def test_a_done_that_lands_while_a_look_makes_the_group_orphan_is_handed_over_at_the_let_go(self):
        # The look finds round-02 open and makes the group orphan; a Done there ran closing() just before, while
        # the group was not orphan yet, so closing() wrote nothing. The let-go still hands it over, once.
        e = self.register()
        server = self.start()
        start, page = self.open()
        self.assertEqual(self.save(page, dict(ANSWER, accepted=True), round_=1)[0].status, 200)
        self.assertEqual(self.save(page, dict(ANSWER, done=True))[0].status, 200)
        with server.scan_lock:
            self.write(self.home / "runs" / (e["run"] + ".json"), dict(e, ended=signoff.utc_now(), exit=130))
            server._scan()  # round-02 still open: orphan now
            g = server.groups[0]
            self.assertTrue(g.orphan)
            g.orphan = False  # the store as it ran: before the look set the flag
            self.assertIsInstance(signoff._Rounds.store(g, 1, dict(ANSWER, done=True, accepted=True)), int)
            g.orphan = True
            self.assertFalse((self.home / "requests").exists())
        for _ in range(3):
            self.assertEqual(self.rounds_waiting(page), [])
        self.assert_one_answer()

    def test_a_done_file_another_program_wrote_hands_an_orphan_answer_over(self):
        page = self.one_orphan_round()
        write_outside(self.topic / "round-01.answers.json", done_record())
        for _ in range(3):
            self.assertEqual(self.rounds_waiting(page), [])
        self.assertEqual([(r["kind"], r["ref"]) for r in self.answers()], [("answer", "2026-11-06-inbox / round-01")])

    def test_a_done_file_from_outside_whose_answer_cannot_be_written_is_said_and_the_list_goes_on(self):
        page = self.one_orphan_round()
        (self.home / "requests").write_text("not a folder", encoding="utf-8")
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):  # before the file: the inbox's own look may take it in first
            write_outside(self.topic / "round-01.answers.json", done_record())
            self.assertEqual(self.rounds_waiting(page), [])
        # Said at the take-in, and once more when the let-go of the group tries again; then the group is gone.
        self.assertEqual(err.getvalue().count("cannot hand over"), 2, err.getvalue())

    def test_a_done_whose_answer_cannot_be_written_is_not_final(self):
        page = self.one_orphan_round()
        (self.home / "requests").write_text("not a folder", encoding="utf-8")
        self.assertEqual(self.save(page, dict(ANSWER, done=True))[0].status, 500)
        self.assertFalse((self.topic / "round-01.answers.json").exists())
        self.assertEqual(len(self.rounds_waiting(page)), 1)

    def test_a_done_whose_answers_json_fails_after_the_request_hands_over_again_on_retry(self):
        # The first request names a round not done yet: its text tells the agent it then needs nothing.
        page = self.one_orphan_round()
        write = signoff.write_atomic

        def no_json(path, data):
            if path.name.endswith(".answers.json"):
                raise OSError(28, "No space left on device")
            return write(path, data)

        with mock.patch.object(signoff, "write_atomic", no_json):
            self.assertEqual(self.save(page, dict(ANSWER, done=True))[0].status, 500)
        self.assertEqual(len(self.answers()), 1)
        self.assertEqual(self.save(page, dict(ANSWER, done=True))[0].status, 200)
        self.assertEqual(len(self.answers()), 2)


class InboxParked(InboxTest):
    """Park and Back: POST park writes parked.json in the home, a parked round leaves Waiting on you for the Parked
    list and counts nowhere, and it stays parked until Back, with or without a run that lists it."""

    rows, ctl = InboxRows.rows, InboxControl.ctl

    def park(self, page, run=0, round_=0, park=True, headers=None, body=None):
        hdrs = {"Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"}
        hdrs.update(headers or {})
        return self.request("POST", page + "park", body if body is not None else
                            {"run": run, "round": round_, "park": park}, hdrs)

    def stored(self):
        try:
            return (self.home / "parked.json").read_bytes()
        except FileNotFoundError:
            return None

    def lists(self, page):
        data = self.view(page)[0]
        return ([w["title"] for w in data["waiting"] if w["kind"] == "round"],
                [(w["title"], w["group"], w["round"], w["orphan"]) for w in data["parked"]], data)

    def count(self):
        return json.loads((self.home / "state.json").read_text(encoding="utf-8"))["waiting"]

    def test_park_moves_a_round_to_parked_counted_nowhere_and_back_returns_it(self):
        self.register()
        self.start()
        start, page = self.open()
        self.assertEqual(self.lists(page)[:2], (["Round 1", "Round 2"], []))
        self.assertEqual(self.count(), 3)  # two rounds and the waiting todo item
        resp, out = self.park(page)
        self.assertEqual((resp.status, json.loads(out)), (200, {"ok": True}))
        self.assertEqual(self.count(), 2)  # at once, not at the next look
        saved = json.loads(self.stored())
        self.assertEqual(sorted(saved), ["rounds", "schema"])
        self.assertEqual(saved["schema"], 1)
        self.assertEqual([sorted(e) for e in saved["rounds"]], [["at", "path"]])
        self.assertEqual(saved["rounds"][0]["path"], str(self.topic / "round-01.json"))
        self.assertTrue(signoff.SAVED_PATTERN.fullmatch(saved["rounds"][0]["at"]))
        self.assertEqual(os.stat(str(self.home / "parked.json")).st_mode & 0o777, 0o600)
        waiting, parked, data = self.lists(page)
        self.assertEqual((waiting, parked), (["Round 2"], [("Round 1", 0, 0, False)]))  # its run lives: agent waiting
        self.assertEqual(data["parked"][0]["since"], saved["rounds"][0]["at"])
        self.assertEqual([r["title"] for r in self.rows()["rounds"]], ["R2"])
        self.assertEqual(self.request("GET", page + "state")[0].status, 200)
        self.assertEqual(json.loads(self.request("GET", page + "state")[1])["waiting"], 2)
        # A repeat Park, and a Back on a round not parked, write nothing.
        before = os.stat(str(self.home / "parked.json")).st_mtime_ns, self.stored()
        self.assertEqual(self.park(page)[0].status, 200)
        self.assertEqual(self.park(page, round_=1, park=False)[0].status, 200)
        self.assertEqual((os.stat(str(self.home / "parked.json")).st_mtime_ns, self.stored()), before)
        # It still opens, saves and takes Done on its own page; the run is left as it was.
        self.assertEqual(self.view(page + "run/0/")[0]["mode"], "rounds")
        self.assertEqual(self.save(page, ANSWER)[0].status, 200)
        self.assertEqual(self.park(page, park=False)[0].status, 200)
        self.assertEqual((self.lists(page)[:2], self.count()), ((["Round 1", "Round 2"], []), 3))
        (kept,) = json.loads(self.stored())["rounds"]  # Back keeps the entry, with its back time
        self.assertEqual((sorted(kept), kept["path"]), (["at", "back", "path"], str(self.topic / "round-01.json")))
        self.assertEqual([r["title"] for r in self.rows()["rounds"]], ["R1", "R2"])

    def test_state_counts_the_rises_the_users_own_backs_made(self):
        def state():
            return json.loads((self.home / "state.json").read_text(encoding="utf-8"))
        self.register()
        self.start()
        start, page = self.open()
        self.assertEqual((state()["waiting"], state()["backs"]), (3, 0))
        self.assertEqual(self.park(page)[0].status, 200)
        self.assertEqual((state()["waiting"], state()["backs"]), (2, 0))  # a Park adds nothing
        self.assertEqual(self.park(page, park=False)[0].status, 200)
        self.assertEqual((state()["waiting"], state()["backs"]), (3, 1))  # in the same write as the rise
        self.assertEqual(self.park(page, park=False)[0].status, 200)  # a repeat Back: no rise, nothing added
        self.assertEqual(state()["backs"], 1)
        (self.root / "TODO.md").write_text("- [ ] (waiting on you: a pick) Check\n- [ ] (waiting on you: b) More\n",
                                           encoding="utf-8")
        self.server.refresh_state()  # the look's write keeps it
        self.assertEqual((state()["waiting"], state()["backs"]), (4, 1))
        self.assertEqual(sorted(state()), ["at", "backs", "projects", "schema", "waiting"])
        self.assertEqual(type(state()["backs"]), int)
        self.stop()
        self.start()  # a restart starts at 0
        self.assertEqual(state()["backs"], 0)

    def test_back_on_a_never_parked_round_writes_no_file(self):
        self.register()
        self.start()
        start, page = self.open()
        self.assertEqual(self.park(page, park=False)[0].status, 200)
        self.assertIsNone(self.stored())

    def test_the_park_route_keeps_todays_rules(self):
        self.register()
        self.start()
        start, page = self.open()
        self.view(page)

        def refused(status, **kw):
            resp, out = self.park(page, **kw)
            self.assertEqual(resp.status, status, (kw, out))
            self.assertIsNone(self.stored())
        for host in ("localhost.example:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, "", None):
            refused(403, headers={"Host": host})
        for origin in ("http://evil.example", "null", "http://localhost:%d" % self.port, self.origin + "/", None):
            refused(403, headers={"Origin": origin})
        for site in ("cross-site", "same-site", "none"):
            refused(403, headers={"Sec-Fetch-Site": site})
        for ctype in ("text/plain", "application/x-www-form-urlencoded", None):
            refused(415, headers={"Content-Type": ctype})
        refused(411, headers={"Content-Length": None}, body=b'{"run": 0, "round": 0, "park": true}')
        refused(413, headers={"Content-Length": "41943040"}, body=b"{}")
        refused(413, body={"run": 0, "round": 0, "park": True, "pad": "x" * 1024})
        good = {"run": 0, "round": 0, "park": True}
        for bad in (b"{x", b"[]", b"null", {}, dict(good, extra=1), {"run": 0, "park": True}, dict(good, run=True),
                    dict(good, run="0"), dict(good, round=False), dict(good, round=0.0), dict(good, run=-1),
                    dict(good, run=2 ** 31), dict(good, park="yes"), dict(good, park=1), dict(good, park=None),
                    dict(good, path=str(self.topic / "round-01.json"))):
            refused(400, body=bad)
        for run, round_ in ((5, 0), (0, 2), (0, 9)):  # no such run, no such round
            refused(409, run=run, round_=round_)
        for path in ("park/", "park?theme=dark", "run/0/park", "Park"):
            resp, _ = self.request("POST", page + path, good, {"Content-Type": "application/json",
                                                               "Origin": self.origin})
            self.assertEqual(resp.status, 404, path)
        self.assertEqual(self.request("GET", page + "park")[0].status, 404)
        board = self.view(page + "run/0/")[0]["rounds"][0]["decisions"]["items"][0]["images"][1]["board"]
        resp, _ = self.request("POST", "/%s/park" % board.split("/")[1], good, {"Content-Type": "application/json",
                                                                                "Origin": self.origin})
        self.assertEqual(resp.status, 404)
        self.assertIsNone(self.stored())
        # A round answered with Done is listed no more.
        self.assertEqual(self.save(page, dict(ANSWER, done=True), round_=1)[0].status, 409)  # round 1 first
        (self.topic / "round-02.answers.json").write_text(json.dumps(done_record()), encoding="utf-8")
        self.view(page)
        refused(409, round_=1)
        self.assertEqual(self.request("GET", start)[0].status, 410)
        refused(404)  # a closed pair writes nothing

    def test_two_parks_at_once_keep_both(self):
        self.register()
        self.start()
        start, page = self.open()
        self.view(page)
        write, gate = signoff.write_atomic, threading.Barrier(2, timeout=1)

        def slow(path, data):  # both posts reach the write together unless one lock covers the read and the write
            if path.name == "parked.json":
                try:
                    gate.wait()
                except threading.BrokenBarrierError:
                    pass
            return write(path, data)
        codes = []
        with mock.patch.object(signoff, "write_atomic", slow):
            posts = [threading.Thread(target=lambda n=n: codes.append(self.park(page, round_=n)[0].status))
                     for n in (0, 1)]
            for t in posts:
                t.start()
            for t in posts:
                t.join(10)
        self.assertEqual(codes, [200, 200])
        self.assertEqual(sorted(e["path"] for e in json.loads(self.stored())["rounds"]),
                         [str(self.topic / "round-01.json"), str(self.topic / "round-02.json")])
        self.assertEqual(self.lists(page)[0], [])

    def test_a_parked_file_the_inbox_did_not_write_is_left_alone(self):
        self.register()
        target = self.root / "elsewhere.json"
        target.write_text('{"schema": 1, "rounds": []}', encoding="utf-8")
        cases = [("link", None), ("text", b"not json"), ("list", b"[]"), ("schema", b'{"schema": 2, "rounds": []}'),
                 ("rounds", b'{"schema": 1, "rounds": {}}'), ("entry", b'{"schema": 1, "rounds": [{"path": 1}]}'),
                 ("big", b'{"schema": 1, "rounds": [], "pad": "' + b"x" * signoff.MAX_PARKED_FILE + b'"}')]
        self.start()
        start, page = self.open()
        for name, raw in cases:
            path = self.home / "parked.json"
            if path.exists() or path.is_symlink():
                os.unlink(str(path))
            if raw is None:
                os.symlink(str(target), str(path))
            else:
                path.write_bytes(raw)
            before = path.read_bytes()
            resp, out = self.park(page)
            self.assertEqual(resp.status, 409, (name, out))
            self.assertEqual((path.read_bytes(), path.is_symlink()), (before, raw is None), name)
            self.assertEqual(self.lists(page)[:2], (["Round 1", "Round 2"], []), name)  # read: nothing parked
        self.assertEqual(target.read_text(encoding="utf-8"), '{"schema": 1, "rounds": []}')

    def test_the_file_has_a_cap_on_its_entries(self):
        self.register()
        self.start()
        start, page = self.open()
        self.view(page)
        with mock.patch.object(signoff, "MAX_PARKED", 1):
            self.assertEqual(self.park(page)[0].status, 200)
            resp, out = self.park(page, round_=1)
            self.assertEqual(resp.status, 409, out)
            self.assertIn(b"move it out of the Signoff home", out)
        self.assertEqual(len(json.loads(self.stored())["rounds"]), 1)

    def test_back_still_works_on_a_file_past_the_cap(self):
        self.register()
        self.parked_by_hand(self.topic / "round-01.json", self.topic / "round-02.json")
        self.start()
        start, page = self.open()
        self.view(page)
        with mock.patch.object(signoff, "MAX_PARKED", 1):
            resp, out = self.park(page, park=False)
            self.assertEqual(resp.status, 200, out)
        self.assertEqual([("back" in e, e["path"]) for e in json.loads(self.stored())["rounds"]],
                         [(True, str(self.topic / "round-01.json")), (False, str(self.topic / "round-02.json"))])

    def test_a_round_in_no_known_project_is_not_parked(self):
        outside = Path(self._tmp.name).resolve() / "loose"
        (outside / "mockups").mkdir(parents=True)
        (outside / "mockups" / "a.png").write_bytes(png_bytes())
        (outside / "boards").mkdir()
        (outside / "boards" / "b.html").write_bytes(BOARD)
        (outside / "round-01.json").write_bytes((self.topic / "round-01.json").read_bytes())
        e = entry(outside, ["round-01.json"])  # a run outside any project
        self.write(self.home / "runs" / (e["run"] + ".json"), e)
        self.start()
        start, page = self.open()
        (row,) = [w for w in self.view(page)[0]["waiting"] if w["kind"] == "round"]
        self.assertIsNone(row["project"])
        resp, out = self.park(page, run=row["group"], round_=row["round"])
        self.assertEqual(resp.status, 409, out)
        self.assertIsNone(self.stored())

    def parked_by_hand(self, *paths):
        self.write(self.home / "parked.json", {"schema": 1, "rounds": [{"path": str(p), "at": "2026-01-02T03:04:05Z"}
                                                                       for p in paths]})

    def stays_parked(self):
        """The parked round with no live run: in Parked (no agent waiting), not counted, its page opens, saves and
        takes Done. Back puts it in Waiting on you as a Run ended row that counts, until back + RUNS_MAX_AGE; a
        Park after Back parks it again."""
        self.start()
        start, page = self.open()
        waiting, parked, data = self.lists(page)
        self.assertEqual((waiting, parked), ([], [("Round 1", 0, 0, True)]))
        self.assertEqual(data["parked"][0]["since"], "2026-01-02T03:04:05Z")
        self.assertEqual(self.count(), 1)  # the todo item only
        self.assertEqual(self.rows()["rounds"], [])
        rounds = self.view(page + "run/0/")[0]
        self.assertEqual((rounds["mode"], len(rounds["rounds"]), rounds["orphan"]), ("rounds", 1, True))
        self.assertEqual(self.save(page, ANSWER)[0].status, 200)
        # Back: the entry stays with its back time, and the round waits on you as a Run ended row.
        self.assertEqual(self.park(page, park=False)[0].status, 200)
        (kept,) = json.loads(self.stored())["rounds"]
        self.assertEqual((kept["path"], kept["at"]), (str(self.topic / "round-01.json"), "2026-01-02T03:04:05Z"))
        self.assertTrue(signoff.SAVED_PATTERN.fullmatch(kept["back"]))
        rows = [w for w in self.view(page)[0]["waiting"] if w["kind"] == "round"]
        self.assertEqual([(w["title"], w["group"], w["orphan"]) for w in rows], [("Round 1", 0, True)])
        self.assertEqual(self.lists(page)[1], [])
        self.assertEqual(self.count(), 2)
        self.assertEqual([r["title"] for r in self.rows()["rounds"]], ["R1"])
        # A Park after Back parks it again: the back time goes.
        self.assertEqual(self.park(page)[0].status, 200)
        (kept,) = json.loads(self.stored())["rounds"]
        self.assertNotIn("back", kept)
        self.assertEqual((self.lists(page)[:2], self.count()), (([], [("Round 1", 0, 0, True)]), 1))
        self.assertEqual(self.park(page, park=False)[0].status, 200)
        back = signoff._epoch(json.loads(self.stored())["rounds"][0]["back"])
        # Once back + RUNS_MAX_AGE is past, it drops out, as an orphan round does.
        with mock.patch.object(signoff.time, "time", lambda: back + signoff.RUNS_MAX_AGE + 60):
            with self.server.scan_lock:
                late = self.server.view()
        self.assertEqual(([w for w in late["waiting"] if w["kind"] == "round"], late["parked"]), ([], []))
        history = self.view(page + "history/%s/" % self.pid)[0]
        self.assertIn("Round 1", [e["title"] for e in history["entries"]])
        # Within the window again (the clock back to now): it waits, and takes Done there.
        (row,) = [w for w in self.view(page)[0]["waiting"] if w["kind"] == "round"]
        self.assertEqual(self.save(page, dict(ANSWER, done=True), group=row["group"])[0].status, 200)
        data = self.view(page)[0]
        self.assertEqual(([w for w in data["waiting"] if w["kind"] == "round"], data["parked"],
                          [s["title"] for s in data["sent"]]), ([], [], ["Round 1"]))

    def test_a_parked_round_whose_run_is_old_stays_parked_and_back_makes_it_wait(self):
        e = self.register(pid=dead_pid())
        os.utime(str(self.home / "runs" / (e["run"] + ".json")), (time.time() - signoff.RUNS_MAX_AGE - 60,) * 2)
        self.parked_by_hand(self.topic / "round-01.json")
        self.stays_parked()

    def test_a_parked_round_no_run_ever_listed_stays_parked_and_back_makes_it_wait(self):
        self.parked_by_hand(self.topic / "round-01.json")
        self.stays_parked()

    def test_a_round_put_back_before_a_restart_waits_since_its_back_time(self):
        back = signoff.utc_now()
        self.write(self.home / "parked.json", {"schema": 1, "rounds": [
            {"path": str(self.topic / "round-01.json"), "at": "2026-01-02T03:04:05Z", "back": back}]})
        self.start()
        rows = [w for w in self.view(self.open()[1])[0]["waiting"] if w["kind"] == "round"]
        self.assertEqual([(w["title"], w["since"]) for w in rows], [("Round 1", back)])

    def test_a_back_time_it_cannot_read_drops_the_round(self):
        self.write(self.home / "parked.json", {"schema": 1, "rounds": [
            {"path": str(self.topic / "round-01.json"), "at": "2026-01-02T03:04:05Z", "back": "yesterday"}]})
        self.start()
        waiting, parked, data = self.lists(self.open()[1])
        self.assertEqual((waiting, parked), ([], []))

    def test_the_parked_list_is_oldest_first(self):
        self.register()  # one live run on both rounds: its group lists them in their own order
        self.write(self.home / "parked.json", {"schema": 1, "rounds": [
            {"path": str(self.topic / "round-01.json"), "at": "2026-01-03T00:00:00Z"},
            {"path": str(self.topic / "round-02.json"), "at": "2026-01-02T00:00:00Z"}]})
        self.start()
        self.assertEqual([p[0] for p in self.lists(self.open()[1])[1]], ["Round 2", "Round 1"])

    def test_each_parked_path_gets_the_checks_of_a_round(self):
        outside = Path(self._tmp.name).resolve() / "outside"
        outside.mkdir()
        (outside / "round-01.json").write_bytes((self.topic / "round-01.json").read_bytes())
        linked = self.topic.parent / "linked"
        os.symlink(str(self.topic), str(linked))
        gone = self.topic.parent / "gone" / "round-01.json"
        done = self.topic.parent / "done"
        done.mkdir()
        for name in ("round-01.json", "round-02.json"):
            (done / name).write_bytes((self.topic / name).read_bytes())
        (done / "round-01.answers.json").write_text(json.dumps(done_record()), encoding="utf-8")
        (done / "round-02.outcome.json").write_text("{}", encoding="utf-8")
        self.parked_by_hand("round-01.json", str(self.topic / "round-0\n1.json"), outside / "round-01.json",
                            linked / "round-01.json", gone, done / "round-01.json", done / "round-02.json",
                            self.topic / "round-01.answers.json", self.root / "TODO.md")
        (self.topic / "round-01.answers.json").write_text(json.dumps(record({})), encoding="utf-8")
        self.start()
        start, page = self.open()
        waiting, parked, data = self.lists(page)
        self.assertEqual((waiting, parked, self.count()), ([], [], 1))
        self.assertEqual([g for g in self.server.groups if g], [])  # nothing read in, no lock taken
        self.assertFalse((outside / "round-01.json.lock").exists())

    def test_a_parked_round_answered_done_shows_as_answered(self):
        self.register()
        self.parked_by_hand(self.topic / "round-01.json")
        (self.topic / "round-01.answers.json").write_text(json.dumps(dict(done_record(), saved=signoff.utc_now())),
                                                          encoding="utf-8")  # answered lately: listed in Answered
        self.start()
        waiting, parked, data = self.lists(self.open()[1])
        self.assertEqual((waiting, parked), (["Round 2"], []))
        self.assertEqual([s["title"] for s in data["sent"]], ["Round 1"])

    def test_the_parked_rows_hold_no_path_link_or_token(self):
        self.register()
        self.parked_by_hand(self.topic / "round-01.json")
        self.start()
        start, page = self.open()
        data, body = self.view(page)
        self.assertEqual(len(data["parked"]), 1)
        for text in (str(self.root), str(self.home), self.topic.name, "round-01.json", "parked.json"):
            self.assertNotIn(text.encode(), body)


class InboxHistory(InboxTest):
    """GET /<page>/history/<project id>/: the past-decisions page of one project, read only, inside the inbox."""

    def history(self, page, pid=None):
        return self.view(page + "history/%s/" % (pid or self.pid))

    def runs_on(self, folder, ago=0, project=None):
        e = entry(folder, ["round-01.json"], project=project or self.pid, pid=dead_pid(), ended=signoff.utc_now())
        path = self.home / "runs" / (e["run"] + ".json")
        self.write(path, e)
        if ago:
            os.utime(str(path), (time.time() - ago,) * 2)
        return e

    def topic_in(self, base, name, title):
        topic = self.root / base / name
        topic.mkdir(parents=True)
        data = sample_decisions()
        data["title"] = title
        (topic / "round-01.json").write_text(json.dumps(data), encoding="utf-8")
        (topic / "mockups").mkdir()
        (topic / "mockups" / "a.png").write_bytes(png_bytes())
        return topic

    def test_the_route_serves_the_projects_past_decisions_read_only(self):
        self.start()
        start, page = self.open()
        before = self.files(self.root)
        data, body = self.history(page)
        self.assertEqual((data["mode"], data["root"], data["inbox"]), ("history", "project", True))
        self.assertEqual(sorted(e["title"] for e in data["entries"]), ["Round 1", "Round 2"])
        self.assertEqual(data["prefs"]["keep"], False)
        self.assertEqual(data["mono"], {k: signoff._who(None, "project")[k] for k in ("mono", "color")})
        self.assertIsNone(data["timeout"])
        for text in (str(self.root), str(self.home), str(self.topic)):
            self.assertNotIn(text.encode(), body)
        csp = self.request("GET", page + "history/%s/" % self.pid)[0].getheader("Content-Security-Policy")
        self.assertIn("frame-ancestors 'none'", csp)
        hdrs = {"Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"}
        for path in ("", "save/0", "prefs", "park", "request", "img/0"):
            resp, _ = self.request("POST", page + "history/%s/%s" % (self.pid, path), ANSWER, hdrs)
            self.assertEqual(resp.status, 405, path)
        self.assertEqual(self.request("POST", page + "history/%s/" % ("f" * 16), ANSWER, hdrs)[0].status, 405)
        self.assertEqual(self.files(self.root), before)

    files = InboxControl.files

    def test_the_route_keeps_the_host_and_token_rules(self):
        self.start()
        start, page = self.open()
        path = page + "history/%s/" % self.pid
        self.assertEqual(self.request("GET", path)[0].status, 200)
        for host in ("localhost.example:%d" % self.port, "127.0.0.1", "evil.example", "[::1]:%d" % self.port, "", None):
            self.assertEqual(self.request("GET", path, headers={"Host": host})[0].status, 403, host)
        token = page.strip("/")
        for bad in ("/%s/history/%s/" % ("A" * len(token), self.pid), page + "history/%s/" % ("f" * 16),
                    page + "history/%s" % self.pid, page + "history/%s/" % capitals(self.pid), page + "history/x/",
                    page + "history/", page + "history/%s/?theme=dark" % self.pid, page + "history/%s/state" % self.pid,
                    page + "run/0/history/%s/" % self.pid):
            self.assertEqual(self.request("GET", bad)[0].status, 404, bad)
        self.assertEqual(self.request("GET", start)[0].status, 410)
        self.assertEqual(self.request("GET", path)[0].status, 404)  # a closed pair opens nothing

    def test_pictures_and_boards_by_number_inside_the_folder(self):
        self.start()
        start, page = self.open()
        hist = page + "history/%s/" % self.pid
        self.assertEqual(self.request("GET", hist + "img/0")[0].status, 404)  # nothing listed before the page
        data, _ = self.history(page)
        images = [img for e in data["entries"] for it in e["decisions"]["items"] for img in it["images"]]
        src = [img["src"] for img in images if "src" in img]
        board = [img["board"] for img in images if "board" in img][0]
        self.assertEqual(self.request("GET", hist + src[0])[1], png_bytes())
        for bad in ("img/999", "img/../x", "img/00"):
            self.assertEqual(self.request("GET", hist + bad)[0].status, 404, bad)
        resp, html = self.request("GET", board)
        self.assertEqual((resp.status, html, resp.getheader("Content-Security-Policy")), (200, BOARD, signoff.BOARD_CSP))
        token = board.split("/")[1]
        for path in ("/%s/" % token, "/%s/run/0/" % token, "/%s/history/%s/" % (token, self.pid), "/%s/0/9" % token):
            self.assertEqual(self.request("GET", path)[0].status, 404, path)
        # Read on request: a picture swapped for a link out of its folder is not followed.
        (self.root / "secret.png").write_bytes(png_bytes() + b"SECRET")
        picture = self.topic / "mockups" / "a.png"
        picture.rename(self.root / "moved.png")
        os.symlink(str(self.root / "secret.png"), str(picture))
        resp, body = self.request("GET", hist + src[0])
        self.assertEqual(resp.status, 404)
        self.assertNotIn(b"SECRET", body)
        # And a board swapped for a link out of its folder.
        (self.root / "secret.html").write_bytes(BOARD + b"SECRET")
        drawn = self.topic / "boards" / "b.html"
        drawn.rename(self.root / "moved.html")
        os.symlink(str(self.root / "secret.html"), str(drawn))
        resp, body = self.request("GET", board)
        self.assertEqual(resp.status, 404)
        self.assertNotIn(b"SECRET", body)

    def test_an_older_tabs_numbers_name_the_same_files_after_a_new_load(self):
        self.start()
        start, page = self.open()
        hist = page + "history/%s/" % self.pid

        def media(data):
            return [img.get("src") or img.get("board") for e in data["entries"] for it in e["decisions"]["items"]
                    for img in it["images"]]
        before = media(self.history(page)[0])
        newer = self.topic.parent / "2026-12-01-newer"  # listed first: every older round moves down
        (newer / "mockups").mkdir(parents=True)
        (newer / "mockups" / "a.png").write_bytes(png_bytes() + b"NEWER")
        data = sample_decisions()
        data["title"] = "Newer"
        (newer / "round-01.json").write_text(json.dumps(data), encoding="utf-8")
        after = media(self.history(page)[0])
        self.assertEqual(after[1:], before)  # the old rounds keep their numbers; the new picture takes a new one
        self.assertNotIn(after[0], before)
        self.assertEqual(self.request("GET", hist + after[0])[1], png_bytes() + b"NEWER")
        self.assertEqual(self.request("GET", hist + before[0])[1], png_bytes())
        self.assertEqual(self.request("GET", [m for m in before if m.startswith("/")][0])[1], BOARD)

    def test_a_folder_it_cannot_read_gives_404_and_the_inbox_goes_on(self):
        self.start()
        start, page = self.open()
        self.topic.chmod(0)
        try:
            with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertEqual(self.request("GET", page + "history/%s/" % self.pid)[0].status, 404)
        finally:
            self.topic.chmod(0o755)
        self.assertIn("cannot list the past decisions of project", err.getvalue())
        self.assertEqual(self.request("GET", page + "history/%s/" % self.pid)[0].status, 200)

    def test_a_non_default_folder_of_old_runs_is_still_listed(self):
        topic = self.topic_in("docs/decisions", "2026-03-01-custom", "Custom")
        self.runs_on(topic, ago=signoff.RUNS_MAX_AGE + 60)
        self.start()
        titles = [e["title"] for e in self.history(self.open()[1])[0]["entries"]]
        self.assertEqual(sorted(titles), ["Custom", "Round 1", "Round 2"])

    def test_a_one_off_newest_run_elsewhere_keeps_the_main_folder_listed(self):
        main = self.topic_in("docs/decisions", "2026-03-01-custom", "Custom")
        demo = self.topic_in("examples", "demo", "Demo")
        self.runs_on(main, ago=600)
        self.runs_on(demo)
        self.start()
        titles = [e["title"] for e in self.history(self.open()[1])[0]["entries"]]
        self.assertEqual(sorted(titles), ["Custom", "Demo", "Round 1", "Round 2"])

    def test_only_folders_inside_the_root_of_this_project(self):
        self.topic_in("docs/decisions", "2026-03-01-custom", "Custom")
        self.runs_on(self.topic_in("", "round-topic", "At the root"))  # its parent is the root: never listed
        outside = Path(self._tmp.name).resolve() / "other" / "decisions" / "t"
        (outside / "mockups").mkdir(parents=True)
        (outside / "mockups" / "a.png").write_bytes(png_bytes())
        (outside / "boards").mkdir()
        (outside / "boards" / "b.html").write_bytes(BOARD)
        (outside / "round-01.json").write_bytes((self.topic / "round-01.json").read_bytes())
        self.runs_on(outside)
        self.runs_on(self.root / "docs" / "decisions" / "2026-03-01-custom", project="f" * 16)  # another project's
        self.start()
        titles = [e["title"] for e in self.history(self.open()[1])[0]["entries"]]
        self.assertEqual(sorted(titles), ["Round 1", "Round 2"])

    def away(self):
        """The default folder moved aside, so a test can put a link in its place."""
        (self.root / ".signoff").rename(self.root / "moved")

    def test_a_default_folder_linked_out_of_the_root_is_not_listed(self):
        self.topic_in("docs/decisions", "2026-03-01-custom", "Custom")
        self.runs_on(self.root / "docs" / "decisions" / "2026-03-01-custom")
        self.away()
        outside = Path(self._tmp.name).resolve() / "outside"
        topic = outside / "2026-04-01-out"
        (topic / "mockups").mkdir(parents=True)
        (topic / "mockups" / "a.png").write_bytes(png_bytes())
        data = sample_decisions()
        data["title"] = "OUTSIDE"
        (topic / "round-01.json").write_text(json.dumps(data), encoding="utf-8")
        os.symlink(str(outside), str(self.root / ".signoff"))
        self.start()
        start, page = self.open()
        data, body = self.history(page)
        self.assertEqual([e["title"] for e in data["entries"]], ["Custom"])
        self.assertNotIn(b"OUTSIDE", body)
        # The one picture listed is the custom round's; nothing names one outside.
        self.assertEqual(self.request("GET", page + "history/%s/img/0" % self.pid)[1], png_bytes())
        self.assertEqual(self.request("GET", page + "history/%s/img/1" % self.pid)[0].status, 404)

    def test_a_default_folder_link_loop_gives_404_and_one_stderr_line(self):
        self.away()
        os.symlink(".signoff", str(self.root / ".signoff"))
        self.start()
        start, page = self.open()
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(self.request("GET", page + "history/%s/" % self.pid)[0].status, 404)
        self.assertEqual(err.getvalue().count("\n"), 1, err.getvalue())
        self.assertIn("cannot list the past decisions of project", err.getvalue())
        self.assertEqual(self.request("GET", page)[0].status, 200)

    def test_one_folder_lists_a_topic_under_each_of_its_names(self):
        os.symlink(self.topic.name, str(self.topic.parent / "2026-11-07-again"))
        self.start()
        topics = sorted(e["topic"] for e in self.history(self.open()[1])[0]["entries"])
        self.assertEqual(topics, ["2026-11-06-inbox"] * 2 + ["2026-11-07-again"] * 2)

    def test_a_topic_two_folders_reach_is_listed_once_under_the_first_folders_name(self):
        # docs/p and docs/p/q are both decisions folders; docs/p/q/t is a topic of the second and, through a link,
        # of the first. The folders go by path, whichever run is newer: the first folder's name wins.
        inner = self.topic_in("docs/p/q", "t", "Inner")
        os.symlink("q/t", str(self.root / "docs" / "p" / "y"))
        self.runs_on(self.root / "docs" / "p" / "q", ago=600)  # docs/p is the parent of this topic folder
        self.runs_on(inner)  # newer: docs/p/q
        self.start()
        topics = [e["topic"] for e in self.history(self.open()[1])[0]["entries"] if e["title"] == "Inner"]
        self.assertEqual(topics, ["y"])

    def test_rounds_past_the_data_budget_are_not_read(self):
        big = self.root / ".signoff" / "2026-12-01-big"
        big.mkdir()
        for n in range(1, 21):
            data = sample_decisions()
            data["title"] = "Big %d" % n
            data["items"][0]["description"] = "x" * 15000
            (big / ("round-%02d.json" % n)).write_text(json.dumps(data), encoding="utf-8")
        self.start()
        page = self.open()[1]
        read = []
        real = signoff.load_decisions

        def counted(path, *a, **kw):
            if Path(path).parent == big:
                read.append(Path(path).name)
            return real(path, *a, **kw)
        with mock.patch.object(signoff, "HISTORY_DATA_BUDGET", 64 * 1024), \
                mock.patch.object(signoff, "load_decisions", counted):
            entries = self.history(page)[0]["entries"]
        shown = [e["file"] for e in entries if e["topic"] == big.name and e["status"] != "error"]
        self.assertEqual(len(shown), 3)  # three of about 16 KiB each fit in 64 KiB
        self.assertEqual(read, shown)  # and no other round of them was read
        over = [e for e in entries if e["topic"] == big.name and e["status"] == "error"]
        self.assertEqual(len(over), 17)
        self.assertTrue(all("over the text budget" in e["error"] and e["decisions"] is None for e in over))
        self.assertEqual(sorted(e["title"] for e in entries if e["topic"] == self.topic.name),
                         ["Round 1", "Round 2"])  # a smaller round after them still fits

    def test_it_reads_at_most_the_newest_runs_files(self):
        main = self.topic_in("docs/decisions", "2026-03-01-custom", "Custom")
        demo = self.topic_in("examples", "demo", "Demo")
        self.runs_on(main, ago=600)
        self.runs_on(demo)
        self.start()
        with mock.patch.object(signoff, "MAX_HISTORY_RUNS", 1):
            titles = [e["title"] for e in self.history(self.open()[1])[0]["entries"]]
        self.assertEqual(sorted(titles), ["Demo", "Round 1", "Round 2"])


@unittest.skipUnless(POSIX, "the control socket is a Unix socket; the inbox does not run on Windows yet")
class InboxCli(unittest.TestCase):
    """The commands as the user and the hooks run them: serve, open, stop; and the waiter woken by an inbox answer."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve() / "home"
        self.env = dict(os.environ, SIGNOFF_INBOX_HOME=str(self.home))
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.communicate()
        self._tmp.cleanup()

    def popen(self, *args):
        p = subprocess.Popen([sys.executable, "-I", "-B", SCRIPT] + list(args), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8", env=self.env)
        self.procs.append(p)
        return p

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", "-B", SCRIPT] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8", env=self.env,
                              timeout=30)

    def serve(self):
        proc = self.popen("inbox", "serve")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                signoff.inbox_request(self.home, "open")
                return proc
            except OSError:
                self.assertIsNone(proc.poll(), proc.stderr.read() if proc.poll() is not None else "")
                time.sleep(0.1)
        raise AssertionError("the inbox did not start")

    def test_serve_open_stop(self):
        proc = self.serve()
        again = self.run_cli("inbox", "serve")
        self.assertEqual((again.returncode, again.stdout), (1, ""), again.stderr)
        self.assertIn("already", again.stderr)
        opened = self.run_cli("inbox", "open", "--no-open")
        self.assertEqual((opened.returncode, opened.stdout), (0, ""), opened.stderr)
        self.assertRegex(opened.stderr, r"http://127\.0\.0\.1:[0-9]+/b/\S+/")
        stopped = self.run_cli("inbox", "stop")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, out), (0, ""), err)
        self.assertNotIn("/b/", err)  # the server's own output (its log when detached) never holds a link
        self.assertEqual(self.run_cli("inbox", "stop").returncode, 1)

    def test_serve_takes_signoff_port(self):
        port = free_port()
        self.env["SIGNOFF_PORT"] = str(port)
        self.serve()
        self.assertEqual(json.loads((self.home / "inbox.json").read_text(encoding="utf-8"))["port"], port)
        opened = self.run_cli("inbox", "open", "--no-open")
        self.assertEqual(opened.returncode, 0, opened.stderr)
        self.assertIn("http://127.0.0.1:%d/b/" % port, opened.stderr)
        # While the inbox holds the port, --history on it cannot bind: one line, as for any taken port.
        root = Path(self._tmp.name).resolve() / "history"
        root.mkdir()
        taken = self.run_cli("--history", str(root), "--no-open")
        self.assertEqual((taken.returncode, taken.stdout), (1, ""), taken.stderr)
        self.assertIn("port %d" % port, taken.stderr)
        self.assertEqual(len(taken.stderr.strip().splitlines()), 1, taken.stderr)

    def test_serve_on_a_taken_or_bad_port_says_so_in_one_line(self):
        for address in ("127.0.0.1", "0.0.0.0"):
            with socket.socket() as taken:
                taken.bind((address, 0))
                taken.listen()
                port = taken.getsockname()[1]
                for value, said in ((str(port), "port %d" % port), ("http", "SIGNOFF_PORT"),
                                    ("65536", "SIGNOFF_PORT"), ("80", "SIGNOFF_PORT")):
                    self.env["SIGNOFF_PORT"] = value
                    served = self.run_cli("inbox", "serve")
                    self.assertEqual((served.returncode, served.stdout), (1, ""), (address, served.stderr))
                    self.assertIn(said, served.stderr)
                    self.assertEqual(len(served.stderr.strip().splitlines()), 1, served.stderr)
                    # No inbox ran, so the switch stays off: runs and hooks go on as with no inbox.
                    self.assertFalse((self.home / "inbox.lock").exists(), (address, value))
        # inbox open checks the value before it starts an inbox; the hooks never look at it (see test_hooks).
        self.env["SIGNOFF_PORT"] = "http"
        opened = self.run_cli("inbox", "open", "--no-open")
        self.assertEqual((opened.returncode, opened.stdout), (1, ""), opened.stderr)
        self.assertIn("SIGNOFF_PORT", opened.stderr)
        self.assertFalse((self.home / "logs").exists())  # no inbox was started

    def test_open_on_a_taken_port_says_so_at_once(self):
        # The inbox it starts ends at once; open sees that, rather than waiting out its 15 s for an answer.
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            port = taken.getsockname()[1]
            self.env["SIGNOFF_PORT"] = str(port)
            began = time.monotonic()
            opened = self.run_cli("inbox", "open", "--no-open")
            self.assertLess(time.monotonic() - began, 10)
        self.assertEqual((opened.returncode, opened.stdout), (1, ""), opened.stderr)
        self.assertIn("did not start", opened.stderr)
        self.assertIn("port %d" % port, (self.home / "logs" / "inbox.log").read_text(encoding="utf-8"))
        self.assertFalse((self.home / "inbox.lock").exists())

    def test_rows_prints_the_project_rounds_as_json(self):
        root = Path(self._tmp.name).resolve() / "project"
        topic = root / ".signoff" / "topic"
        (root / ".git").mkdir(parents=True)
        topic.mkdir(parents=True)
        (topic / "round-01.json").write_text(json.dumps(one_round(question("q"))), encoding="utf-8")
        pid = signoff.project_id(root)
        none = self.run_cli("inbox", "rows", str(topic))
        self.assertEqual((none.returncode, none.stdout), (1, ""), none.stderr)  # no inbox: it is not started
        self.assertFalse((self.home / "inbox.lock").exists())
        self.serve()
        (self.home / "runs").mkdir(mode=0o700)
        e = entry(topic, ["round-01.json"], project=pid)
        signoff.write_atomic(self.home / "runs" / (e["run"] + ".json"), json.dumps(e))
        want = {"rounds": [{"run": 0, "round": 0, "title": "R3", "item": 0, "items": [{"item": 0, "title": "Question q"}]}],
                "more": 0, "inbox": signoff.inbox_request(self.home, "rows", project=pid)["inbox"]}
        got = self.run_cli("inbox", "rows", str(topic))
        self.assertEqual((got.returncode, json.loads(got.stdout), got.stdout.count("\n")), (0, want, 1), got.stderr)
        outside = Path(self._tmp.name).resolve() / "elsewhere"
        outside.mkdir()
        got = self.run_cli("inbox", "rows", str(outside))
        self.assertEqual((got.returncode, json.loads(got.stdout)), (0, {"rounds": [], "more": 0}), got.stderr)
        for bad in (["topic"], [], [str(topic), "more"]):
            got = self.run_cli("inbox", "rows", *bad)
            self.assertEqual((got.returncode, got.stdout), (1, ""), bad)
        self.env["SIGNOFF_INBOX_HOME"] = "relative/home"  # a relative home is off, as for every inbox command
        got = self.run_cli("inbox", "rows", str(topic))
        self.assertEqual((got.returncode, got.stdout), (1, ""), got.stderr)
        self.env["SIGNOFF_INBOX_HOME"] = str(self.home)
        self.assertEqual(self.run_cli("inbox", "stop").returncode, 0)

    def test_open_with_a_target_starts_an_inbox_and_takes_whole_numbers(self):
        target = ["inbox", "open", "--run", "0", "--focus", "0/0", "--inbox", "1", "--no-open"]
        got = self.run_cli(*target)  # no inbox: one starts, as for a plain open; its own number differs: the list
        self.assertEqual((got.returncode, got.stdout), (0, ""), got.stderr)
        self.assertRegex(got.stderr, r"http://127\.0\.0\.1:[0-9]+/b/\S+/")
        got = self.run_cli(*target)  # a stale target on a running inbox: the list's link too
        self.assertEqual((got.returncode, got.stdout), (0, ""), got.stderr)
        self.assertRegex(got.stderr, r"http://127\.0\.0\.1:[0-9]+/b/\S+/")
        for bad in (["--run", "0", "--focus", "0/0"], ["--focus", "0/0", "--inbox", "1", "--run", "0", "--run", "1"],
                    ["--run", "0", "--focus", "0/0/0", "--inbox", "1"], ["--run", "+1", "--focus", "0/0", "--inbox", "1"],
                    ["--run", "0", "--focus", "0/0", "--inbox", "2147483648"], ["--run", "0", "--focus", "/0",
                    "--inbox", "1"], ["--run", "0", "--focus", "0/0", "--inbox"], ["--browser"]):
            got = self.run_cli("inbox", "open", *bad)
            self.assertEqual((got.returncode, got.stdout), (1, ""), bad)
            self.assertNotIn("/b/", got.stderr, bad)
        self.assertEqual(self.run_cli("inbox", "stop").returncode, 0)

    def test_open_app_is_a_usage_error_before_anything_runs(self):
        # In process, with every way out stubbed: a signoff.py from before would run `open -a` on an installed app.
        started = []
        target = ["--run", "0", "--focus", "0/0", "--inbox", "1"]
        for args in (["--app"], ["--app"] + target, target + ["--app"], ["--app", "--no-open"], ["--app", "--app"]):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(subprocess, "run", side_effect=lambda *a, **k: started.append(a)), \
                    mock.patch.object(subprocess, "Popen", side_effect=lambda *a, **k: started.append(a)), \
                    mock.patch.object(webbrowser, "open", side_effect=lambda *a, **k: started.append(a)), \
                    mock.patch.dict(os.environ, SIGNOFF_INBOX_HOME=str(self.home)), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = signoff.main(["inbox", "open"] + args)
            self.assertEqual((code, out.getvalue()), (1, ""), args)
            self.assertIn("usage", err.getvalue(), args)
            self.assertNotIn("--app", err.getvalue(), args)  # the usage line names it no more
        self.assertEqual(started, [])  # nothing opened, no inbox started
        self.assertFalse(self.home.exists())

    def test_open_starts_a_detached_inbox_with_its_log_in_the_home(self):
        opened = self.run_cli("inbox", "open", "--no-open")
        try:
            self.assertEqual(opened.returncode, 0, opened.stderr)
            self.assertTrue((self.home / "logs" / "inbox.log").is_file())
            self.assertEqual(stat.S_IMODE((self.home / "logs" / "inbox.log").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o700)
            self.assertIn("link", signoff.inbox_request(self.home, "open"))
        finally:
            self.assertEqual(self.run_cli("inbox", "stop").returncode, 0)  # before the home goes: it is detached

    def test_an_answer_in_the_inbox_wakes_the_waiting_run(self):
        self.serve()
        rounds = Rounds(1)
        self.addCleanup(rounds.cleanup)
        waiter = self.popen(str(rounds.target), "--no-open")
        self.assertIn("inbox is open", waiter.stderr.readline())
        link = signoff.inbox_request(self.home, "open")["link"]
        origin, start = link.split("/b/")
        host = origin[len("http://"):]
        port = int(host.split(":")[1])

        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request(method, path, body=json.dumps(body) if body else None, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            return resp, data

        page = request("GET", "/b/" + start)[0].getheader("Location")
        deadline = time.monotonic() + 20
        while b'"kind":"round"' not in request("GET", page)[1]:  # the waiter registers itself first
            self.assertLess(time.monotonic(), deadline, "the waiter's round never showed")
            time.sleep(0.2)
        resp, body = request("POST", page + "run/0/save/0", dict(ANSWER, done=True), {
            "Content-Type": "application/json", "Origin": origin, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(resp.status, 200, body)
        out, err = waiter.communicate(timeout=20)
        self.assertEqual((waiter.returncode, out), (0, "%s\n" % rounds.answers(1)), err)


if __name__ == "__main__":
    unittest.main()
