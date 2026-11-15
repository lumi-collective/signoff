"""The decisions file and the answers are checked before anything is served or written."""
import copy
import json
import os
import time
import unittest
from decimal import Decimal

from support import ROOT, Folder, sample_decisions, signoff


class DecisionsFile(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()

    def tearDown(self):
        self.folder.cleanup()

    def broken(self, change):
        data = sample_decisions()
        change(data)
        self.folder.write(data)
        with self.assertRaises(signoff.DecisionsError) as ctx:
            signoff.load_decisions(self.folder.decisions)
        return str(ctx.exception)

    def test_sample_loads(self):
        decisions, images = signoff.load_decisions(self.folder.decisions)
        self.assertEqual(decisions["title"], "Pick the settings layout")
        self.assertEqual(images, [self.folder.path / "mockups" / "a.png"])

    def test_defaults_are_filled(self):
        data = sample_decisions()
        del data["items"][0]["select"]
        del data["free_text"]
        self.folder.write(data)
        decisions, _ = signoff.load_decisions(self.folder.decisions)
        self.assertEqual(decisions["items"][0]["select"], "one")
        self.assertTrue(decisions["items"][0]["comment"])
        self.assertFalse(decisions["items"][0]["other"])
        self.assertFalse(decisions["items"][0]["skippable"])
        self.assertEqual(decisions["free_text"], {})

    def test_other_and_skippable_must_be_booleans(self):
        self.assertIn("other", self.broken(lambda d: d["items"][0].update(other="yes")))
        self.assertIn("skippable", self.broken(lambda d: d["items"][1].update(skippable=1)))

    def test_sensitive_is_an_optional_boolean(self):
        self.assertFalse(signoff.load_decisions(self.folder.decisions)[0]["items"][0]["sensitive"])
        data = sample_decisions()
        data["items"][1]["sensitive"] = True
        self.folder.write(data)
        self.assertTrue(signoff.load_decisions(self.folder.decisions)[0]["items"][1]["sensitive"])
        self.assertIn("sensitive", self.broken(lambda d: d["items"][0].update(sensitive="yes")))
        self.assertIn("sensitive", self.broken(lambda d: d["items"][0].update(sensitive=1)))

    def with_criteria(self, data):
        item = data["items"][1]
        item["criteria"] = [{"id": "effort", "label": "Effort"}, {"id": "risk", "label": "Risk"}]
        item["options"][0]["facts"] = {"effort": {"text": "S", "tone": "good"}, "risk": {"text": "Medium · conflicts"}}
        item["options"][1]["facts"] = {"risk": {"text": "High", "tone": "bad"}}
        return item

    def test_criteria_and_facts_load(self):
        data = sample_decisions()
        self.with_criteria(data)
        self.folder.write(data)
        item = signoff.load_decisions(self.folder.decisions)[0]["items"][1]
        self.assertEqual([c["id"] for c in item["criteria"]], ["effort", "risk"])
        self.assertEqual(item["options"][0]["facts"]["effort"], {"text": "S", "tone": "good"})
        self.assertNotIn("facts", item["options"][2])  # an option may have no facts; its cells show a dash
        self.assertNotIn("criteria", signoff.load_decisions(self.folder.decisions)[0]["items"][0])

    def test_criteria_must_be_a_list_of_id_and_label(self):
        def change(fn):
            return lambda d: fn(self.with_criteria(d))
        self.assertIn("'criteria'", self.broken(change(lambda it: it.update(criteria={"effort": "Effort"}))))
        self.assertIn("'criteria'", self.broken(change(lambda it: it.update(criteria=[]))))
        self.assertIn("criterion 1", self.broken(change(lambda it: it["criteria"][0].pop("label"))))
        self.assertIn("criterion 2", self.broken(change(lambda it: it["criteria"][1].update(label=["Risk"]))))
        self.assertIn("weight", self.broken(change(lambda it: it["criteria"][0].update(weight=2))))
        self.assertIn("'id' must match", self.broken(change(lambda it: it["criteria"][0].update(id="bad id"))))
        self.assertIn("duplicate criterion id 'effort'",
                      self.broken(change(lambda it: it["criteria"][1].update(id="effort"))))

    def test_facts_name_only_the_items_criteria(self):
        def change(fn):
            return lambda d: fn(self.with_criteria(d))
        self.assertIn("'cost' is not one of this item's criteria (effort, risk)",
                      self.broken(change(lambda it: it["options"][0]["facts"].update(cost={"text": "1 day"}))))
        msg = self.broken(lambda d: d["items"][0]["options"][0].update(facts={"effort": {"text": "S"}}))
        self.assertIn("'facts' needs the item's 'criteria'", msg)
        self.assertIn("option 1", msg)
        self.assertIn("'facts'", self.broken(change(lambda it: it["options"][0].update(facts=[]))))

    def test_a_fact_is_text_and_an_optional_tone(self):
        def change(fn):
            return lambda d: fn(self.with_criteria(d))
        facts = lambda it: it["options"][0]["facts"]  # noqa: E731
        self.assertIn("fact 'effort': must be an object", self.broken(change(lambda it: facts(it).update(effort="S"))))
        self.assertIn("missing 'text'", self.broken(change(lambda it: facts(it).update(effort={"tone": "good"}))))
        self.assertIn("'text' must be a string", self.broken(change(lambda it: facts(it).update(effort={"text": 3}))))
        self.assertIn("score", self.broken(change(lambda it: facts(it)["effort"].update(score=4))))
        for tone in ("better", "GOOD", "", None, 1):
            self.assertIn("'tone' must be one of good, mid, bad",
                          self.broken(change(lambda it, tone=tone: facts(it)["effort"].update(tone=tone))))

    def test_many_criteria_load_fast(self):
        # Criterion lookups must not grow with the number of criteria: --history loads every round.
        data = sample_decisions()
        item = data["items"][1]
        item["criteria"] = [{"id": "c%d" % k, "label": "C"} for k in range(10000)]
        item["options"] = [{"id": "o%d" % n, "label": "O", "facts": {"c%d" % k: {"text": "x"} for k in range(10000)}}
                           for n in range(6)]
        del item["recommended"]
        self.folder.write(data)
        self.assertLess(self.folder.decisions.stat().st_size, signoff.MAX_DECISIONS)
        start = time.perf_counter()
        signoff.load_decisions(self.folder.decisions)
        self.assertLess(time.perf_counter() - start, 1.0)

    def test_commands_load_as_written(self):
        data = sample_decisions()
        cmds = [{"text": "make docs\n  open build/index.html", "lang": "bash", "caption": "Build the docs"},
                {"text": "rm -rf build"}]
        data["items"][0]["commands"] = cmds
        self.folder.write(data)
        items = signoff.load_decisions(self.folder.decisions)[0]["items"]
        self.assertEqual(items[0]["commands"], cmds)
        self.assertNotIn("commands", items[1])  # no default: an item without it is as before
        data["items"][1]["commands"] = [{"text": "x"}] * signoff.MAX_ITEM_COMMANDS
        self.folder.write(data)
        self.assertEqual(len(signoff.load_decisions(self.folder.decisions)[0]["items"][1]["commands"]), 10)

    def test_commands_bad_shapes_name_the_item_and_entry(self):
        def cmds(value):
            return lambda d: d["items"][1].update(commands=value)
        for value in ("make docs", {"text": "x"}, [], [{"text": "x"}] * (signoff.MAX_ITEM_COMMANDS + 1)):
            msg = self.broken(cmds(value))
            self.assertIn("item 2 (filters)", msg)
            self.assertIn("'commands'", msg)
        for entry, word in (({"lang": "bash"}, "missing 'text'"), ({"text": ""}, "empty"), ({"text": "  \n"}, "empty"),
                            ({"text": 3}, "string"), ({"text": "x", "lang": 1}, "string"),
                            ({"text": "x", "caption": ["a"]}, "string"), ({"text": "x", "run": True}, "unknown key"),
                            ("ls", "object")):
            msg = self.broken(cmds([{"text": "ok"}, entry]))
            self.assertIn("item 2 (filters), command 2", msg, entry)
            self.assertIn(word, msg, entry)

    # Characters that show as nothing, or that a browser may draw differently from the terminal: controls (CR,
    # ESC, NUL, DEL, NEL), format characters (bidi, zero-width), Default_Ignorable_Code_Point (a combining grapheme
    # joiner, a variation selector, Hangul fillers, tags), line and paragraph separators, private use, and
    # unassigned code points (U+0890 is unassigned on Python 3.9 and a format character on 3.13), and two that draw
    # nothing without being white space (the Braille blank, the object replacement character).
    INVISIBLE = (0x202E, 0x200B, 0x2066, 0xFEFF, 0x00AD, 0x0D, 0x1B, 0x00, 0x7F, 0x85, 0x034F, 0xFE0F, 0xE0100,
                 0x115F, 0x3164, 0xFFA0, 0xE0002, 0x2065, 0xFFF0, 0x2028, 0x2029, 0xE000, 0x0890, 0x1BCA0, 0x1D173,
                 0x200D, 0x2800, 0xFFFC)
    EMOJI_JOINERS = (0xFE0F, 0x200D)  # a caption may hold an emoji: the warning sign is U+26A0 U+FE0F

    def test_a_command_with_an_invisible_character_is_refused(self):
        # The user pastes it into a terminal: what the page shows must be what runs. Tab and newline are fine.
        for key in ("text", "caption", "lang"):
            for code in self.INVISIBLE:
                if key == "caption" and code in self.EMOJI_JOINERS:
                    continue
                char = chr(code)
                cmd = {"text": "ls -l", "lang": "bash", "caption": "List"}
                cmd[key] = "ls" + char + "x"
                msg = self.broken(lambda d: d["items"][0].update(commands=[cmd]))
                self.assertIn("item 1 (layout), command 1", msg, (key, hex(code)))
                self.assertIn("U+%04X" % code, msg, (key, hex(code)))
                self.assertNotIn(char, msg, (key, hex(code)))  # named, never written raw to a terminal
        msg = self.broken(lambda d: d["items"][0].update(commands=[{"text": "make\r\nls"}]))  # CRLF too
        self.assertIn("U+000D", msg)
        data = sample_decisions()
        data["items"][0]["commands"] = [{"text": "if true;\n\tthen ls; fi", "caption": "Tab\tand line"}]
        self.folder.write(data)
        self.assertEqual(signoff.load_decisions(self.folder.decisions)[0]["items"][0]["commands"][0]["text"],
                         "if true;\n\tthen ls; fi")

    def test_a_caption_may_hold_an_emoji(self):
        # U+FE0F and U+200D join an emoji's parts; in a caption they are only shown, never pasted.
        data = sample_decisions()
        captions = [chr(0x26A0) + chr(0xFE0F) + " Destructive", "Team " + chr(0x1F469) + chr(0x200D) + chr(0x1F4BB)]
        data["items"][0]["commands"] = [{"text": "ls", "caption": c} for c in captions]
        self.folder.write(data)
        self.assertEqual([c["caption"] for c in signoff.load_decisions(self.folder.decisions)[0]["items"][0]["commands"]],
                         captions)

    def test_a_command_is_capped_and_has_no_white_space_at_its_ends(self):
        # A trailing line break runs it on paste. The block wraps, so long runs of spaces and blank lines show
        # (a heredoc, indented YAML, aligned columns load); the length and line caps keep it to a readable block.
        def refused(text):
            return self.broken(lambda d: d["items"][0].update(commands=[{"text": text}]))
        for text in ("ls\n", "ls ", "ls\t", "ls\n\n", " ls", "\nls", "ls" + chr(0xA0)):
            self.assertIn("start or end", refused(text), repr(text))
        self.assertIn("more than %d characters" % signoff.MAX_COMMAND_TEXT,
                      refused("x" * (signoff.MAX_COMMAND_TEXT + 1)))
        self.assertIn("more than %d lines" % signoff.MAX_COMMAND_LINES,
                      refused("\n".join(["ls"] * (signoff.MAX_COMMAND_LINES + 1))))
        self.assertEqual((signoff.MAX_COMMAND_TEXT, signoff.MAX_COMMAND_LINES), (4000, 60))
        data = sample_decisions()
        ok = ["cat <<'EOF'\nline one\n\n\n\nline five\nEOF", "services:\n  web:\n" + " " * 20 + "image: nginx",
              "a" + "\t" * 30 + "b", "npm test" + " " * 150 + "; echo the tail shows",
              "x" * signoff.MAX_COMMAND_TEXT, "\n".join(["ls"] * signoff.MAX_COMMAND_LINES)]
        data["items"][0]["commands"] = [{"text": t} for t in ok]
        self.folder.write(data)
        self.assertEqual([c["text"] for c in signoff.load_decisions(self.folder.decisions)[0]["items"][0]["commands"]],
                         ok)

    def test_a_commands_lang_is_a_short_plain_word(self):
        data = sample_decisions()
        langs = ["bash", "c++", "c#", "objective-c", "x.y_z", "a" * 20, "PowerShell"]
        data["items"][0]["commands"] = [{"text": "ls", "lang": lang} for lang in langs]
        self.folder.write(data)
        self.assertEqual([c["lang"] for c in signoff.load_decisions(self.folder.decisions)[0]["items"][0]["commands"]],
                         langs)
        for lang in ("", "a b", "<b>", "a" * 21, "bash\n", "sh;", "é"):
            msg = self.broken(lambda d: d["items"][0].update(commands=[{"text": "ls", "lang": lang}]))
            self.assertIn("'lang'", msg, repr(lang))

    def test_context_is_one_short_line(self):
        data = sample_decisions()
        self.assertNotIn("context", signoff.load_decisions(self.folder.decisions)[0])  # no default: as before
        line = "feat/parser session: splitting the tokenizer " + "é" * (signoff.MAX_CONTEXT - 45)
        self.assertEqual(len(line), 120)
        data["context"] = line
        self.folder.write(data)
        self.assertEqual(signoff.load_decisions(self.folder.decisions)[0]["context"], line)
        for value in ("x" * 121, "two\nlines", "a\ttab", "", "   ", 3, ["x"], None, "bidi‮ override", "zero​width",
                      "line sep"):
            msg = self.broken(lambda d: d.update(context=value))
            self.assertIn("'context'", msg, repr(value))
        # What a command refuses as drawn as nothing: the Braille blank, a Hangul filler, private use, a variation
        # selector, an unassigned code point.
        for n in (0x2800, 0x3164, 0xE000, 0xFE0F, 0x0378):
            msg = self.broken(lambda d, n=n: d.update(context="feat/x " + chr(n) + " session"))
            self.assertIn("'context'", msg, "U+%04X" % n)

    def test_blocking_defaults_to_false_and_must_be_a_bool(self):
        data = sample_decisions()
        data["items"][0]["blocking"] = True
        self.folder.write(data)
        items = signoff.load_decisions(self.folder.decisions)[0]["items"]
        self.assertEqual((items[0]["blocking"], items[1]["blocking"]), (True, False))
        for value in ("yes", 1, None, []):
            msg = self.broken(lambda d, value=value: d["items"][1].update(blocking=value))
            self.assertIn("item 2 (filters)", msg, value)
            self.assertIn("'blocking' must be true or false", msg, value)

    def test_tab_is_optional_text(self):
        data = sample_decisions()
        data["tab"] = "Layout"
        self.folder.write(data)
        self.assertEqual(signoff.load_decisions(self.folder.decisions)[0]["tab"], "Layout")
        self.assertIn("tab", self.broken(lambda d: d.update(tab=["x"])))

    def test_not_json(self):
        self.folder.decisions.write_text("{nope", encoding="utf-8")
        with self.assertRaises(signoff.DecisionsError) as ctx:
            signoff.load_decisions(self.folder.decisions)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_too_large_says_so(self):
        self.folder.decisions.write_text(" " * (signoff.MAX_DECISIONS + 1), encoding="utf-8")
        with self.assertRaises(signoff.DecisionsError) as ctx:
            signoff.load_decisions(self.folder.decisions)
        self.assertIn("larger than", str(ctx.exception))
        self.assertNotIn("JSON", str(ctx.exception))

    def test_missing_file(self):
        with self.assertRaises(signoff.DecisionsError):
            signoff.load_decisions(self.folder.path / "absent.json")

    def test_wrong_version(self):
        self.assertIn("version", self.broken(lambda d: d.update(version=2)))

    def test_missing_title(self):
        self.assertIn("title", self.broken(lambda d: d.pop("title")))

    def test_notice_is_optional_text(self):
        data = sample_decisions()
        data["notice"] = "Synthetic data only"
        self.folder.write(data)
        self.assertEqual(signoff.load_decisions(self.folder.decisions)[0]["notice"], "Synthetic data only")
        self.assertIn("notice", self.broken(lambda d: d.update(notice=["x"])))

    def test_unknown_top_key(self):
        self.assertIn("colour", self.broken(lambda d: d.update(colour="red")))

    def test_no_items(self):
        self.assertIn("items", self.broken(lambda d: d.update(items=[])))

    def test_unknown_item_key_names_the_item(self):
        msg = self.broken(lambda d: d["items"][1].update(defaultValue="date"))
        self.assertIn("filters", msg)
        self.assertIn("defaultValue", msg)

    def test_duplicate_item_id(self):
        def dup(d):
            d["items"][1]["id"] = "layout"
        self.assertIn("duplicate", self.broken(dup))

    def test_bad_item_id(self):
        self.assertIn("id", self.broken(lambda d: d["items"][0].update(id="has space")))

    def test_id_with_trailing_newline(self):
        self.assertIn("id", self.broken(lambda d: d["items"][0].update(id="layout\n")))

    def test_deeply_nested_json(self):
        self.folder.decisions.write_text("[" * 100000, encoding="utf-8")
        with self.assertRaises(signoff.DecisionsError) as ctx:
            signoff.load_decisions(self.folder.decisions)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_deeply_nested_answers_stop_the_run_with_a_message(self):
        self.folder.answers.write_text("[" * 100000, encoding="utf-8")
        with self.assertRaises(signoff.DecisionsError) as ctx:
            signoff.make_server(self.folder.decisions)
        self.assertIn("cannot resume", str(ctx.exception))

    def test_gate_reads_windows_paths_as_text(self):
        # On every OS: a drive with its root (an absolute Windows path) passes to the folder check; a
        # drive-relative or network path never gets that far.
        def refused(src):
            try:
                signoff._inside(self.folder.path, src, "x")
            except signoff.DecisionsError as e:
                return "drive or network" in str(e)
            return False
        self.assertFalse(refused("C:\\proj\\x.png"))
        self.assertFalse(refused("C:/proj/x.png"))
        for src in ("C:foo.png", "\\\\host", "\\\\host\\share\\x.png", "//host/share/x.png", "\\\\?\\C:\\x.png",
                    "\\\\.\\pipe\\x.png"):
            self.assertTrue(refused(src), src)

    def test_lone_surrogate(self):
        self.folder.decisions.write_text(self.folder.decisions.read_text(encoding="utf-8").replace(
            '"Pick the settings layout"', '"\\ud800 x"'), encoding="utf-8")
        with self.assertRaises(signoff.DecisionsError) as ctx:
            signoff.load_decisions(self.folder.decisions)
        self.assertIn("character", str(ctx.exception))

    def test_bad_select(self):
        self.assertIn("select", self.broken(lambda d: d["items"][0].update(select="some")))

    def test_too_few_options(self):
        self.assertIn("options", self.broken(lambda d: d["items"][0].update(options=[{"id": "x", "label": "X"}])))

    def test_duplicate_option(self):
        msg = self.broken(lambda d: d["items"][0].update(options=[{"id": "x", "label": "X"}, {"id": "x", "label": "Y"}]))
        self.assertIn("duplicate", msg)

    def test_recommended_not_an_option(self):
        self.assertIn("recommended", self.broken(lambda d: d["items"][0].update(recommended="three")))

    def test_recommended_one_must_be_a_string(self):
        self.assertIn("recommended", self.broken(lambda d: d["items"][0].update(recommended=["two"])))

    def test_recommended_many_must_be_a_list(self):
        self.assertIn("recommended", self.broken(lambda d: d["items"][1].update(recommended="date")))

    def test_text_must_be_a_string(self):
        self.assertIn("description", self.broken(lambda d: d["items"][0].update(description=["x"])))

    def test_box_out_of_range(self):
        msg = self.broken(lambda d: d["items"][0]["images"][0].update(boxes=[[80, 0, 30, 10]]))
        self.assertIn("boxes", msg)

    def test_box_needs_four_numbers(self):
        self.assertIn("boxes", self.broken(lambda d: d["items"][0]["images"][0].update(boxes=[[1, 2, 3]])))

    def test_box_rejects_booleans(self):
        self.assertIn("boxes", self.broken(lambda d: d["items"][0]["images"][0].update(boxes=[[True, 0, 10, 10]])))

    def test_image_missing(self):
        self.assertIn("not found", self.broken(lambda d: d["items"][0]["images"][0].update(src="mockups/none.png")))

    def test_image_outside_folder(self):
        outside = self.folder.path.parent / ("outside-%d.png" % os.getpid())
        outside.write_bytes(b"x")
        try:
            msg = self.broken(lambda d: d["items"][0]["images"][0].update(src="../" + outside.name))
        finally:
            outside.unlink()
        self.assertIn("outside", msg)

    def test_absolute_image_outside_folder(self):
        msg = self.broken(lambda d: d["items"][0]["images"][0].update(src=str(ROOT / "README.md")))
        self.assertIn("outside", msg)

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_symlink_pointing_outside(self):
        (self.folder.path / "mockups" / "link.png").symlink_to(ROOT / "plugin" / "signoff" / "page.html")
        msg = self.broken(lambda d: d["items"][0]["images"][0].update(src="mockups/link.png"))
        self.assertIn("outside", msg)

    def test_image_control_character(self):
        for src in ("mockups/a\x00.png", "mockups/a\x1b.png"):
            self.assertIn("control character", self.broken(lambda d: d["items"][0]["images"][0].update(src=src)))

    def test_network_and_drive_paths(self):
        # Refused as text, on every OS, before anything touches the disk: on Windows each of these would
        # reach out over the network (and send the user's NTLM hash) or to another drive.
        for src in ("\\\\host\\share\\a.png", "//host/share/a.png", "\\\\host@SSL@443\\a.png", "C:a.png",
                    "\\\\?\\C:\\a.png", "\\\\.\\pipe\\a.png", "\\\\host", "//host"):
            self.assertIn("relative", self.broken(lambda d: d["items"][0]["images"][0].update(src=src)), src)
            board = src.replace(".png", ".html")
            self.assertIn("relative", self.broken(lambda d: d["items"][0]["images"].append({"html": board})), board)

    def test_absolute_image_inside_folder_loads_as_before(self):
        # Rounds written for 0.1.0 may name a picture by its absolute path; they must still load.
        data = sample_decisions()
        data["items"][0]["images"][0]["src"] = str(self.folder.path / "mockups" / "a.png")
        self.folder.write(data)
        _, images = signoff.load_decisions(self.folder.decisions)
        self.assertEqual(images, [self.folder.path / "mockups" / "a.png"])

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_image_link_loop(self):
        (self.folder.path / "mockups" / "loop.png").symlink_to("loop.png")
        self.broken(lambda d: d["items"][0]["images"][0].update(src="mockups/loop.png"))

    def test_svg_rejected(self):
        (self.folder.path / "mockups" / "a.svg").write_text("<svg/>", encoding="utf-8")
        msg = self.broken(lambda d: d["items"][0]["images"][0].update(src="mockups/a.svg"))
        self.assertIn("SVG", msg)

    def test_other_types_rejected(self):
        (self.folder.path / "mockups" / "a.html").write_text("<p>", encoding="utf-8")
        msg = self.broken(lambda d: d["items"][0]["images"][0].update(src="mockups/a.html"))
        self.assertIn("PNG", msg)


class Answers(unittest.TestCase):
    def setUp(self):
        folder = Folder()
        self.decisions, _ = signoff.load_decisions(folder.decisions)
        folder.cleanup()

    def ok(self, state):
        return signoff.check_answers(state, self.decisions)

    def bad(self, state):
        with self.assertRaises(ValueError):
            signoff.check_answers(state, self.decisions)

    def good_state(self):
        return {"answers": {"layout": {"choice": "one", "comment": "fine"},
                            "filters": {"choice": ["date"], "comment": ""}},
                "free_text": "", "done": False}

    def test_good(self):
        self.assertEqual(self.ok(self.good_state())["answers"]["layout"]["choice"], "one")

    def test_partial_is_fine(self):
        self.ok({"answers": {}, "free_text": "", "done": False})

    def test_empty_many_and_no_one_choice(self):
        state = self.good_state()
        state["answers"]["filters"]["choice"] = []
        state["answers"]["layout"]["choice"] = None
        self.ok(state)

    def test_unknown_item(self):
        state = self.good_state()
        state["answers"]["ghost"] = {"choice": "one", "comment": ""}
        self.bad(state)

    def test_unknown_option(self):
        state = self.good_state()
        state["answers"]["layout"]["choice"] = "three"
        self.bad(state)

    def test_list_for_single_choice(self):
        state = self.good_state()
        state["answers"]["layout"]["choice"] = ["one"]
        self.bad(state)

    def test_duplicates_in_many(self):
        state = self.good_state()
        state["answers"]["filters"]["choice"] = ["date", "date"]
        self.bad(state)

    def test_long_comment(self):
        state = self.good_state()
        state["answers"]["layout"]["comment"] = "x" * (signoff.MAX_TEXT + 1)
        self.bad(state)

    def test_long_free_text(self):
        state = self.good_state()
        state["free_text"] = "x" * (signoff.MAX_TEXT + 1)
        self.bad(state)

    def test_extra_key(self):
        state = self.good_state()
        state["script"] = "x"
        self.bad(state)

    def test_done_must_be_bool(self):
        state = self.good_state()
        state["done"] = "yes"
        self.bad(state)

    def test_not_an_object(self):
        self.bad(["x"])

    def test_source_is_recorded(self):
        state = self.good_state()
        state["answers"]["layout"]["source"] = "accept_all"
        state["answers"]["filters"]["source"] = "user"
        clean = self.ok(state)
        self.assertEqual(clean["answers"]["layout"]["source"], "accept_all")
        self.assertEqual(clean["answers"]["filters"]["source"], "user")

    def test_source_values(self):
        for value in signoff.SOURCE_VALUES:
            state = self.good_state()
            state["answers"]["layout"]["source"] = value
            self.ok(state)
        state = self.good_state()
        state["answers"]["layout"]["source"] = "agent"
        self.bad(state)

    def test_stored_file_may_carry_version_and_saved(self):
        state = self.good_state()
        state.update(version=1, saved="2026-01-01T00:00:00Z")
        signoff.check_answers(state, self.decisions, stored=True)
        self.bad(copy.deepcopy(state))


class OtherAndSkip(unittest.TestCase):
    def setUp(self):
        folder = Folder()
        data = sample_decisions()
        for item in data["items"]:
            item.update(other=True, skippable=True)
        folder.write(data)
        self.decisions, _ = signoff.load_decisions(folder.decisions)
        folder.cleanup()

    def check(self, layout=None, filters=None):
        answers = {}
        if layout is not None:
            answers["layout"] = dict({"comment": ""}, **layout)
        if filters is not None:
            answers["filters"] = dict({"comment": ""}, **filters)
        return signoff.check_answers({"answers": answers, "free_text": "", "done": False}, self.decisions)

    def bad(self, **kw):
        with self.assertRaises(ValueError):
            self.check(**kw)

    def test_other_with_its_text(self):
        clean = self.check(layout={"choice": "_other", "other": "three columns"},
                           filters={"choice": ["date", "_other"], "other": "Colour"})
        self.assertEqual(clean["answers"]["layout"], {"choice": "_other", "other": "three columns", "comment": ""})
        self.assertEqual(clean["answers"]["filters"]["other"], "Colour")

    def test_other_without_text_is_empty_text(self):
        self.assertEqual(self.check(layout={"choice": "_other"})["answers"]["layout"]["other"], "")

    def test_skip(self):
        clean = self.check(layout={"choice": "_skip"}, filters={"choice": ["_skip"]})
        self.assertEqual(clean["answers"]["layout"]["choice"], "_skip")
        self.assertEqual(clean["answers"]["filters"]["choice"], ["_skip"])

    def test_skip_is_alone(self):
        self.bad(filters={"choice": ["_skip", "date"]})
        self.bad(filters={"choice": ["_skip", "_other"], "other": "x"})

    def test_other_text_needs_other_chosen(self):
        self.bad(layout={"choice": "one", "other": "x"})
        self.bad(layout={"choice": "_skip", "other": "x"})

    def test_other_text_limits(self):
        self.bad(layout={"choice": "_other", "other": 3})
        self.bad(layout={"choice": "_other", "other": "x" * (signoff.MAX_TEXT + 1)})

    def test_only_when_the_item_allows_it(self):
        folder = Folder()
        try:
            decisions, _ = signoff.load_decisions(folder.decisions)
        finally:
            folder.cleanup()
        for choice in ("_other", "_skip"):
            with self.assertRaises(ValueError):
                signoff.check_answers({"answers": {"layout": {"choice": choice, "comment": ""}}, "free_text": "",
                                       "done": False}, decisions)

    def test_markdown_shows_other_and_skip(self):
        record = {"answers": {"layout": {"choice": "_other", "other": "three *columns*", "comment": ""},
                              "filters": {"choice": ["_skip"], "comment": ""}},
                  "free_text": "", "done": False, "saved": "2026-01-01T12:00:00Z"}
        md = signoff.answers_markdown(self.decisions, record)
        self.assertIn("- Choice: Other: three \\*columns\\* (not recommended)", md)
        self.assertIn("- Choice: Skipped (not recommended)", md)

    def test_markdown_without_a_recommendation_says_nothing_about_it(self):
        decisions = dict(self.decisions, items=[dict(self.decisions["items"][0])])
        del decisions["items"][0]["recommended"]
        record = {"answers": {"layout": {"choice": "one", "comment": ""}}, "free_text": "", "done": False,
                  "saved": "2026-01-01T12:00:00Z"}
        md = signoff.answers_markdown(decisions, record)
        self.assertIn("- Choice: Keep one\n", md)
        self.assertNotIn("recommended)", md)


def number_item(**extra):
    item = {"id": "budget", "title": "How much of the budget?", "select": "number",
            "range": {"min": -10, "max": 10, "step": 0.5, "unit": "%"}, "recommended": 2.5}
    item.update(extra)
    return {k: v for k, v in item.items() if v is not None}


class NumberItem(unittest.TestCase):
    """select "number": a value in range, on its step, with the recommendation preselected; the answer is a number."""

    def setUp(self):
        self.folder = Folder()

    def tearDown(self):
        self.folder.cleanup()

    def load(self, item):
        data = sample_decisions()
        data["items"].append(item)
        self.folder.write(data)
        return signoff.load_decisions(self.folder.decisions)[0]["items"][2]

    def broken(self, item):
        with self.assertRaises(signoff.DecisionsError) as ctx:
            self.load(item)
        return str(ctx.exception)

    def test_a_number_item_loads_with_its_step_filled(self):
        item = self.load(number_item(range={"min": 0, "max": 40}, recommended=12))
        self.assertEqual(item["range"], {"min": 0, "max": 40, "step": 1})
        self.assertEqual((item["select"], item["recommended"], item["skippable"], item["comment"]),
                         ("number", 12, False, True))
        self.assertNotIn("options", item)
        item = self.load(number_item(range={"min": 0.1, "max": 1, "step": 0.1, "unit": "x"}, recommended=0.7))
        self.assertEqual(item["range"]["unit"], "x")
        self.load(number_item(range={"min": 0, "max": 100000, "step": 10}, recommended=0))  # 10,000 steps

    def test_options_belong_to_one_and_many_and_range_to_number(self):
        opts = [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
        msg = self.broken(number_item(options=opts))
        self.assertIn("item 3 (budget)", msg)
        self.assertIn("'options'", msg)
        msg = self.broken({"id": "plain", "title": "No options"})
        self.assertIn("item 3 (plain)", msg)
        self.assertIn("missing 'options'", msg)
        msg = self.broken({"id": "both", "title": "x", "options": opts, "recommended": "a", "range": {"min": 0, "max": 1}})
        self.assertIn("item 3 (both)", msg)
        self.assertIn("'range'", msg)
        msg = self.broken(number_item(range=None))
        self.assertIn("missing 'range'", msg)

    def test_bad_shapes_name_the_item(self):
        cases = [
            (number_item(recommended=None), "'recommended'"),
            (number_item(range={"min": 5, "max": 5}, recommended=5), "'min'"),
            (number_item(range={"min": 6, "max": 5}, recommended=5), "'min'"),
            (number_item(range={"min": 0, "max": 10, "step": 0}, recommended=5), "'step'"),
            (number_item(range={"min": 0, "max": 10, "step": -1}, recommended=5), "'step'"),
            (number_item(range={"min": 0, "max": 100001, "step": 10}, recommended=0), "steps"),
            (number_item(range={"min": -1e308, "max": 1e308}, recommended=0), "steps"),  # max - min is inf
            (number_item(range={"min": float("nan"), "max": 10}, recommended=5), "finite"),
            (number_item(range={"min": 0, "max": float("inf")}, recommended=5), "finite"),
            (number_item(range={"min": -10 ** 400, "max": 10}, recommended=5), "finite"),
            (number_item(range={"min": 0, "max": 10, "step": float("inf")}, recommended=5), "finite"),
            (number_item(range={"min": False, "max": 10}, recommended=5), "number"),
            (number_item(range={"min": "0", "max": 10}, recommended=5), "number"),
            (number_item(range={"min": 0, "max": 10, "step": True}, recommended=5), "number"),
            # A float cannot tell 1e15 from 1e15 + 0.5 apart well enough to keep a step of 1.
            (number_item(range={"min": 1e15, "max": 1e15 + 10, "step": 1}, recommended=1e15), "'step'"),
            (number_item(range={"min": -5e12, "max": -5e12 + 3, "step": 1}, recommended=-5e12), "'step'"),
            # Within MAX_SCALE steps, yet past what a float holds: the grid's arithmetic would overflow.
            (number_item(range={"min": -10 ** 308, "max": 10 ** 308, "step": 10 ** 305}, recommended=9e307), "1e15"),
            (number_item(range={"min": 0, "max": 2 * 10 ** 15, "step": 10 ** 12}, recommended=0), "1e15"),
            (number_item(range=[0, 10]), "'range'"),
            (number_item(range={"min": 0}), "missing 'max'"),
            (number_item(range={"min": 0, "max": 10, "scale": "log"}), "unknown key"),
            (number_item(recommended=10.5), "'recommended'"),
            (number_item(recommended=-11), "'recommended'"),
            (number_item(recommended=2.25), "'recommended'"),
            (number_item(recommended="2.5"), "'recommended'"),
            (number_item(recommended=True), "'recommended'"),
            (number_item(recommended=[2.5]), "'recommended'"),
            (number_item(recommended=float("nan")), "'recommended'"),
            (number_item(recommended=10 ** 400), "'recommended'"),
            (number_item(other=True), "'other'"),
            (number_item(marks=True), "'marks'"),
            (number_item(criteria=[{"id": "effort", "label": "Effort"}]), "'criteria'"),
            (number_item(range={"min": 0, "max": 10, "unit": 3}), "'unit'"),
            (number_item(range={"min": 0, "max": 10, "unit": "x" * (signoff.MAX_UNIT + 1)}), "'unit'"),
            (number_item(range={"min": 0, "max": 10, "unit": "per\nday"}), "'unit'"),
            (number_item(range={"min": 0, "max": 10, "unit": "x‮"}), "'unit'"),
            (number_item(select="numbers"), "'select'"),
        ]
        for item, word in cases:
            with self.subTest(item=repr(item)[:120]):
                msg = self.broken(item)
                self.assertIn("item 3 (budget)", msg)
                self.assertIn(word, msg)

    def test_a_unit_of_its_full_length_and_a_skippable_item_load(self):
        item = self.load(number_item(range={"min": 0, "max": 10, "unit": "x" * signoff.MAX_UNIT}, recommended=0,
                                     skippable=True, sensitive=True, comment=False))
        self.assertTrue(item["skippable"])
        item = self.load(number_item(range={"min": -10 ** 15, "max": -10 ** 15 + 10 ** 7, "step": 1000},
                                     recommended=-999999999999000))  # the bound itself
        self.assertEqual(item["recommended"], -999999999999000)

    def test_recommended_is_kept_as_its_grid_point(self):
        # The page saves the slider's value, which the browser writes as min + k * step in decimal: a noisy
        # recommendation would then never equal the answer.
        for rng, rec, want in (({"min": 0.1, "max": 1, "step": 0.1}, 0.30000000000000004, 0.3),
                               ({"min": 0.1, "max": 1, "step": 0.1}, 0.7, 0.7),
                               ({"min": -10, "max": 10, "step": 0.5}, 2.5000000000000004, 2.5),
                               ({"min": 0, "max": 1e-3, "step": 1e-7}, 3 * 1e-7, 3e-7),
                               ({"min": 0, "max": 100, "step": 5}, 40, 40)):
            item = self.load(number_item(range=rng, recommended=rec))
            self.assertEqual(item["recommended"], want, rng)
            self.assertIs(type(item["recommended"]), type(want), rng)

    def test_a_recommendation_whose_grid_point_is_out_of_range_is_kept_as_written(self):
        # A max off the grid: the nearest grid point lies past it, so the snap would make an answer that
        # check_answers refuses (an Accept from a notification would end as failed).
        for rng, rec in (({"min": 0, "max": 9.9999999999}, 9.9999999999),
                         ({"min": 0.1, "max": 0.2999999999999999, "step": 0.1}, 0.2999999999999999)):
            item = self.load(number_item(range=rng, recommended=rec))
            self.assertEqual(item["recommended"], rec, rng)
            decisions = {"items": [item]}
            state = {"answers": {"budget": {"choice": item["recommended"], "source": "notification"}},
                     "free_text": "", "done": True, "accepted": True}
            self.assertEqual(signoff.check_answers(state, decisions)["answers"]["budget"]["choice"], rec, rng)


class NumberAnswers(unittest.TestCase):
    def setUp(self):
        folder = Folder()
        data = sample_decisions()
        data["items"].append(number_item(skippable=True))
        data["items"].append(number_item(id="share", range={"min": 0.1, "max": 1, "step": 0.1}, recommended=0.3))
        folder.write(data)
        self.decisions, _ = signoff.load_decisions(folder.decisions)
        folder.cleanup()

    def check(self, choice, key="budget", stored=False, **extra):
        state = {"answers": {key: dict({"choice": choice, "comment": ""}, **extra)}, "free_text": "", "done": False}
        if stored:
            state.update(version=1, saved="2026-01-01T12:00:00Z")
        return signoff.check_answers(state, self.decisions, stored=stored)["answers"][key]

    def test_a_number_on_the_grid_null_or_skip(self):
        for choice in (2.5, -10, 10, 0, 0.0, -0.5, 7, None, "_skip"):
            with self.subTest(choice=choice):
                self.assertEqual(self.check(choice)["choice"], choice)
                self.assertEqual(self.check(choice, stored=True)["choice"], choice)
        for choice in (0.1, 0.3, 0.7, 1, 0.30000000000000004):  # float steps, as a browser sends them
            self.assertEqual(self.check(choice, key="share")["choice"], choice)
        self.assertEqual(self.check(4, source="notification", private=True),
                         {"choice": 4, "comment": "", "source": "notification", "private": True})

    def test_anything_else_is_refused(self):
        for choice in ("2.5", "", True, False, [2.5], [], {"v": 1}, 2.25, 10.5, -10.5, 1e999, -1e999,
                       float("nan"), 10 ** 400, "_other", "two"):
            with self.subTest(choice=repr(choice)[:40]):
                with self.assertRaises(ValueError):
                    self.check(choice)
                with self.assertRaises(ValueError):
                    self.check(choice, stored=True)
        with self.assertRaises(ValueError):
            self.check("_skip", key="share")  # not skippable
        # Large values keep the grid: half a step off is refused near the largest values a range may hold.
        self.decisions["items"][3]["range"] = {"min": 1e11, "max": 1e11 + 100, "step": 1}
        self.assertEqual(self.check(1e11 + 7, key="share")["choice"], 1e11 + 7)
        for off in (0.5, 0.01):
            with self.assertRaises(ValueError):
                self.check(1e11 + 7 + off, key="share")
        with self.assertRaises(ValueError):
            self.check(2.5, other="x")
        with self.assertRaises(ValueError):
            self.check(2.5, marks=[])

    def test_values_a_browser_sends_far_from_0_are_on_the_grid(self):
        # The slider's value is min + k * step worked out in decimal, then read as a float: far from 0 it is
        # off the float grid by more than a millionth of a step, which the check must take.
        lo = 1e8 + 0.1  # without the tolerance's term for the value's size, 8000 of these are refused
        self.decisions["items"][3]["range"] = {"min": lo, "max": lo + 1000, "step": 0.1}
        refused = []
        for k in range(10001):
            v = float(Decimal(repr(lo)) + k * Decimal("0.1"))
            try:
                self.check(v, key="share")
            except ValueError:
                refused.append(v)
        self.assertEqual(refused, [])

    def test_bounds_near_a_floats_limit_never_raise_anything_but_value_error(self):
        # A range past the loader's bound, as an older file or a hand-built one could hold: the check must refuse,
        # not overflow.
        self.decisions["items"][3]["range"] = {"min": -10 ** 308, "max": 10 ** 308, "step": 10 ** 305}
        for choice in (9e307, -9e307, 1.7e308, 10 ** 308):
            with self.assertRaises(ValueError):
                self.check(choice, key="share")
        # Inside the value bound, a hand-built range can still break the arithmetic (a step of 0): the guard in
        # _on_grid turns that into a refusal too.
        self.decisions["items"][3]["range"] = {"min": 0, "max": 10, "step": 0}
        with self.assertRaises(ValueError):
            self.check(5, key="share")

    def test_a_saved_body_with_nan_from_json_is_refused(self):
        # json.loads reads NaN and Infinity, which JSON itself does not have.
        for raw in ('{"choice": NaN}', '{"choice": Infinity}', '{"choice": -Infinity}', '{"choice": 1e999}'):
            ans = json.loads(raw)
            with self.assertRaises(ValueError):
                signoff.check_answers({"answers": {"budget": ans}, "free_text": "", "done": False}, self.decisions)

    def test_zero_is_decided_unless_only_preselected(self):
        self.assertTrue(signoff._decided({"choice": 0, "source": "user"}))
        self.assertTrue(signoff._decided({"choice": 0.0, "source": "notification"}))
        self.assertFalse(signoff._decided({"choice": 0, "source": "recommended"}))
        self.assertFalse(signoff._decided({"choice": None, "source": "user"}))

    def md(self, choice, lang=None, key="budget"):
        decisions = dict(self.decisions) if lang is None else dict(self.decisions, lang=lang)
        record = {"answers": {key: {"choice": choice, "comment": "", "source": "user"}}, "free_text": "",
                  "done": True, "saved": "2026-01-01T12:00:00Z"}
        return signoff.answers_markdown(decisions, record)

    def test_markdown_shows_the_value_and_unit(self):
        self.assertIn("- Choice: 2.5 % (recommended)\n", self.md(2.5))
        self.assertIn("- Choice: -3 % (not recommended)\n", self.md(-3))
        self.assertIn("- Choice: 0 % (not recommended)\n", self.md(0))
        self.assertIn("- Choice: Skipped (not recommended)\n", self.md("_skip"))
        self.assertIn("- Choice: nothing chosen\n", self.md(None))
        self.assertIn("- Choice: 0.3 (recommended)\n", self.md(0.3, key="share"))  # no unit, no trailing space
        self.assertIn("- Választás: 2.5 % (javasolt)\n", self.md(2.5, lang="hu"))
        self.assertIn("- Választás: 7 % (nem javasolt)\n", self.md(7, lang="hu"))
        self.assertIn("- Választás: Kihagyva (nem javasolt)\n", self.md("_skip", lang="hu"))

    def test_markdown_escapes_the_unit_and_keeps_big_numbers_whole(self):
        item = self.decisions["items"][2]
        item["range"] = {"min": 0, "max": 2000000, "step": 500, "unit": "<b>*HUF*</b>"}
        item["recommended"] = 1234500
        md = self.md(1234500)
        self.assertIn("- Choice: 1234500 \\<b\\>\\*HUF\\*\\</b\\> (recommended)\n", md)
        self.assertNotIn("<b>", md)
        self.assertNotIn("e+06", md)


class StoredAnswers(unittest.TestCase):
    def setUp(self):
        folder = Folder()
        self.decisions, _ = signoff.load_decisions(folder.decisions)
        folder.cleanup()

    def stored(self, **change):
        record = {"version": 1, "done": True, "saved": "2026-01-01T12:00:00Z", "answers": {}, "free_text": ""}
        record.update(change)
        return signoff.check_answers(record, self.decisions, stored=True)

    def test_good(self):
        self.stored()
        self.stored(decisions={"file": "decisions.json", "sha256": "0" * 64})

    def test_acceptance_is_checked(self):
        self.stored(accepted=True, accepted_at="2026-01-01T12:00:00Z", changed_after_accept=False)
        for change in ({"accepted": "yes"}, {"accepted_at": "noon"}, {"changed_after_accept": 1}):
            with self.assertRaises(ValueError, msg=change):
                self.stored(**change)
        with self.assertRaises(ValueError):
            signoff.check_answers({"answers": {}, "free_text": "", "done": False, "accepted": 1}, self.decisions)
        self.assertTrue(signoff.check_answers({"answers": {}, "free_text": "", "done": False, "accepted": True},
                                              self.decisions)["accepted"])

    def test_version_and_saved_are_checked(self):
        for change in ({"version": 2}, {"version": True}, {"saved": "<b>yesterday</b>"}, {"saved": 5},
                       {"decisions": {"file": "x.json"}}, {"decisions": {"file": "x.json", "sha256": "zz"}}):
            with self.assertRaises(ValueError, msg=change):
                self.stored(**change)
        with self.assertRaises(ValueError):
            signoff.check_answers({"done": True, "answers": {}, "free_text": ""}, self.decisions, stored=True)


class Private(unittest.TestCase):
    """The user marks an answer private: the JSON keeps the text for the agent, the Markdown copy hides it."""

    def setUp(self):
        folder = Folder()
        data = sample_decisions()
        data["items"][0].update(other=True, marks=True, sensitive=True)
        folder.write(data)
        self.decisions, _ = signoff.load_decisions(folder.decisions)
        folder.cleanup()

    def check(self, answer, item="layout", stored=False):
        state = {"answers": {item: answer}, "free_text": "", "done": False}
        if stored:
            state.update(version=1, saved="2026-01-01T12:00:00Z")
        return signoff.check_answers(state, self.decisions, stored=stored)["answers"][item]

    def test_private_is_kept(self):
        self.assertIs(self.check({"choice": "one", "comment": "my own reason", "private": True})["private"], True)
        self.assertIs(self.check({"choice": "one", "comment": "", "private": False})["private"], False)
        self.assertIs(self.check({"choice": "one", "comment": "x", "private": True}, stored=True)["private"], True)

    def test_any_item_can_be_private(self):
        self.assertFalse(self.decisions["items"][1]["sensitive"])
        self.assertIs(self.check({"choice": ["date"], "comment": "", "private": True}, item="filters")["private"], True)

    def test_without_the_key_nothing_changes(self):
        self.assertEqual(self.check({"choice": "one", "comment": "fine"}), {"choice": "one", "comment": "fine"})

    def test_private_must_be_a_boolean(self):
        for value in (1, 0, "yes", "true", None, [], {}):
            with self.assertRaises(ValueError, msg=value):
                self.check({"choice": "one", "comment": "", "private": value})
            with self.assertRaises(ValueError, msg=value):
                self.check({"choice": "one", "comment": "", "private": value}, stored=True)

    def record(self, answer):
        return {"answers": {"layout": answer, "filters": {"choice": ["date"], "comment": ""}},
                "free_text": "", "done": False, "saved": "2026-01-01T12:00:00Z"}

    def test_markdown_hides_private_text(self):
        md = signoff.answers_markdown(self.decisions, self.record({
            "choice": "_other", "other": "my *home* folder", "comment": "my health\n# not a heading", "private": True,
            "marks": [{"image": 0, "box": [10, 20, 30, 40], "comment": "my desk"}]}))
        for text in ("home", "health", "heading", "desk"):
            self.assertNotIn(text, md)
        self.assertIn("- Choice: Other: (private) (not recommended)", md)
        self.assertIn("- Comment: (private)", md)
        self.assertIn("- Mark 1, picture 1 at 10, 20, 30, 40 (x, y, w, h in %): (private)", md)

    def test_markdown_private_without_text_has_no_comment_line(self):
        md = signoff.answers_markdown(self.decisions, self.record({"choice": "one", "comment": "", "private": True}))
        self.assertIn("- Choice: Keep one (not recommended)\n", md)
        self.assertNotIn("Comment", md)

    def test_markdown_shows_text_when_not_private(self):
        # sensitive is only the agent's suggestion: what counts is the user's answer.
        for answer in ({"choice": "one", "comment": "plain *note*"},
                       {"choice": "one", "comment": "plain *note*", "private": False}):
            self.assertIn("- Comment: plain \\*note\\*", signoff.answers_markdown(self.decisions, self.record(answer)))


class MarkdownLanguage(unittest.TestCase):
    """The Markdown copy has no empty comment line, and its labels follow the round's lang (en, hu; else English)."""

    def setUp(self):
        folder = Folder()
        data = sample_decisions()
        data["items"][0].update(other=True, marks=True)
        folder.write(data)
        self.decisions, _ = signoff.load_decisions(folder.decisions)
        folder.cleanup()
        self.attached = [{"src": "notes/cut.md", "caption": "Cut list", "text": "x"}]

    def md(self, comment="", lang=None, **answer):
        decisions = dict(self.decisions) if lang is None else dict(self.decisions, lang=lang)
        record = {"answers": {"layout": dict({"choice": "one", "comment": comment, "source": "user"}, **answer),
                              "filters": {"choice": ["date"], "comment": "", "source": "recommended"}},
                  "free_text": "", "done": True, "accepted": True, "accepted_at": "2026-01-01T12:00:00Z",
                  "saved": "2026-01-01T12:00:00Z"}
        return signoff.answers_markdown(decisions, record)

    def test_empty_comment_leaves_no_line(self):
        for comment in ("", "  \n "):
            md = self.md(comment)
            self.assertNotIn("Comment", md)
            self.assertNotIn("(none)\n- ", md)
            self.assertIn("- Source: the user\n\n", md)
        self.assertIn("- Comment: a note\n", self.md("a note"))

    def test_english_labels(self):
        md = self.md("a note", lang="en-GB")
        for text in ("- Choice: Keep one (not recommended)", "- Source: the user", "- Comment: a note",
                     "- Source: recommendation, not changed", "## Anything else", "Saved: 2026-01-01T12:00:00Z",
                     "Accepted: yes, 2026-01-01T12:00:00Z", "Done: yes"):
            self.assertIn(text, md)

    def test_hungarian_labels(self):
        for lang in ("hu", "HU", "hu-HU", "hu_HU"):
            md = self.md("egy jegyzet", lang=lang)
            for text in ("- Választás: Keep one (nem javasolt)", "- Forrás: a felhasználó", "- Megjegyzés: egy jegyzet",
                         "- Forrás: a javaslat, változatlanul", "- Választás: Date (javasolt)",
                         "Mentve: 2026-01-01T12:00:00Z", "Elfogadva: igen, 2026-01-01T12:00:00Z", "Kész: igen"):
                self.assertIn(text, md, lang)
            for text in ("Choice", "Comment", "Source", "Saved", "Done"):
                self.assertNotIn(text, md, lang)

    def test_every_source_has_a_label_in_every_language(self):
        for code, labels in signoff.MD_LABELS.items():
            for value in signoff.SOURCE_VALUES:
                self.assertIn(value, labels, code)
        self.assertIn("- Source: recommendation, accepted from a notification", self.md(source="notification"))
        self.assertIn("- Forrás: a javaslat, értesítésből elfogadva", self.md(lang="hu", source="notification"))

    def test_other_languages_get_english(self):
        for lang in ("de", "fr-CA", "", "hux"):
            self.assertIn("- Choice: Keep one (not recommended)", self.md("x", lang=lang), lang)

    def test_default_free_text_title(self):
        record = {"answers": {}, "free_text": "", "done": False, "saved": "2026-01-01T12:00:00Z"}
        for lang, title in (("en", "## Anything else"), ("hu", "## Bármi más")):
            decisions = dict(self.decisions, lang=lang, free_text={})
            self.assertIn(title, signoff.answers_markdown(decisions, record))

    def test_hungarian_empty_comment_leaves_no_line(self):
        self.assertNotIn("Megjegyzés", self.md("", lang="hu"))

    def test_hungarian_other_skip_marks_attachment_and_states(self):
        self.decisions["items"][0]["attachments"] = self.attached
        md = self.md("", lang="hu", choice="_other", other="három oszlop",
                     marks=[{"image": 0, "box": [10, 20, 30, 40], "comment": "itt"}])
        self.assertIn("- Választás: Egyéb: három oszlop (nem javasolt)", md)
        self.assertIn("- Jelölés 1, 1. kép, 10, 20, 30, 40 (x, y, sz, m, %-ban): itt", md)
        self.assertIn("- Melléklet: Cut list (notes/cut.md)", md)
        self.assertIn("Kihagyva", self.md("", lang="hu", choice="_skip").replace("(nem javasolt)", ""))
        self.assertIn("nincs választás", self.md("", lang="hu", choice=[]))
        record = {"answers": {}, "free_text": "", "done": False, "saved": "2026-01-01T12:00:00Z"}
        decisions = dict(self.decisions, lang="hu")
        self.assertIn("Elfogadva: nem  \n", signoff.answers_markdown(decisions, record))
        self.assertIn("Kész: nem", signoff.answers_markdown(decisions, record))
        self.assertIn("nem, elfogadás után módosítva", signoff.answers_markdown(
            decisions, dict(record, changed_after_accept=True)))
        self.assertIn("\n(nincs)\n", signoff.answers_markdown(decisions, record))  # an empty free text

    def test_commands_are_one_escaped_line_each(self):
        # The copy is for reading: what the user was told to run, nothing rendered as Markdown or HTML.
        self.decisions["items"][0]["commands"] = [
            {"text": "echo <b>x</b> `id` | tee"}, {"text": "# note\n1. step", "caption": "Set *up* <i>"}]
        md = self.md("")
        self.assertIn("- Command: echo \\<b\\>x\\</b\\> \\`id\\` \\| tee\n", md)
        self.assertIn("- Command (Set \\*up\\* \\<i\\>): \\# note  \n  1\\. step\n", md)
        self.assertNotIn("<b>", md)
        hu = self.md("", lang="hu")
        self.assertIn("- Parancs: echo \\<b\\>", hu)
        self.assertIn("- Parancs (Set \\*up\\* \\<i\\>): ", hu)
        self.assertNotIn("Command", hu)
        del self.decisions["items"][0]["commands"]
        self.assertNotIn("Command", self.md(""))

    def test_private_stays_hidden_in_every_language(self):
        self.decisions["items"][0]["attachments"] = self.attached
        for lang, hidden in (("en", "(private)"), ("hu", "(privát)")):
            md = self.md("my health", lang=lang, choice="_other", other="my home", private=True,
                         marks=[{"image": 0, "box": [10, 20, 30, 40], "comment": "my desk"}])
            for text in ("health", "home", "desk"):
                self.assertNotIn(text, md)
            self.assertEqual(md.count(hidden), 3, lang)
            self.assertIn("notes/cut.md", md)


class SchemaFile(unittest.TestCase):
    """schema.json documents the format; this keeps it in step with the checks in signoff.py."""

    def setUp(self):
        self.schema = json.loads((ROOT / "plugin" / "signoff" / "schema.json").read_text(encoding="utf-8"))
        self.defs = self.schema["$defs"]

    def test_id_is_the_repository_address(self):
        self.assertTrue(self.schema["$id"].startswith("https://github.com/lumi-collective/signoff/"), self.schema["$id"])

    def test_top_level(self):
        self.assertEqual(set(self.schema["properties"]), set(signoff.TOP_KEYS))
        self.assertEqual(set(self.schema["required"]), set(signoff.TOP_REQUIRED))
        self.assertFalse(self.schema["additionalProperties"])

    def test_item(self):
        item = self.defs["item"]
        self.assertEqual(set(item["properties"]), set(signoff.ITEM_KEYS))
        self.assertEqual(set(item["required"]), set(signoff.ITEM_REQUIRED))
        self.assertEqual(item["properties"]["select"]["enum"], list(signoff.SELECT_VALUES))
        self.assertEqual(item["properties"]["id"]["pattern"], signoff.ID_PATTERN.pattern)
        self.assertEqual((item["properties"]["blocking"]["type"], item["properties"]["blocking"]["default"]),
                         ("boolean", False))

    def test_number(self):
        rng = self.defs["range"]
        self.assertEqual(set(rng["properties"]), set(signoff.RANGE_KEYS))
        self.assertEqual(rng["required"], ["min", "max"])
        self.assertFalse(rng["additionalProperties"])
        self.assertEqual(rng["properties"]["unit"]["maxLength"], signoff.MAX_UNIT)
        self.assertEqual(rng["properties"]["step"]["default"], 1)
        for key in ("min", "max"):
            self.assertEqual((rng["properties"][key]["minimum"], rng["properties"][key]["maximum"]),
                             (-signoff.MAX_VALUE, signoff.MAX_VALUE))
        item = self.defs["item"]
        self.assertEqual(item["properties"]["range"], {"$ref": "#/$defs/range"})
        self.assertIn({"type": "number"}, item["properties"]["recommended"]["oneOf"])
        # options for one and many; range and recommended for number
        (rule,) = item["allOf"]
        self.assertEqual(rule["if"], {"properties": {"select": {"const": "number"}}, "required": ["select"]})
        self.assertEqual(rule["then"]["required"], ["range", "recommended"])
        self.assertEqual(rule["then"]["properties"]["recommended"], {"type": "number"})
        self.assertEqual(rule["else"]["required"], ["options"])

    def test_image_and_option(self):
        self.assertEqual(set(self.defs["image"]["properties"]), set(signoff.IMAGE_KEYS))
        self.assertNotIn("required", self.defs["image"])
        self.assertEqual([alt["required"] for alt in self.defs["image"]["oneOf"]], [["src"], ["html"]])
        self.assertEqual(set(self.defs["option"]["properties"]), set(signoff.OPTION_KEYS))
        self.assertEqual(set(self.defs["option"]["required"]), {"id", "label"})
        self.assertEqual(set(self.schema["properties"]["free_text"]["properties"]), set(signoff.FREE_TEXT_KEYS))

    def test_attachment(self):
        att = self.defs["attachment"]
        self.assertEqual(set(att["properties"]), set(signoff.ATTACHMENT_KEYS))
        self.assertEqual(att["required"], ["src"])
        self.assertFalse(att["additionalProperties"])
        self.assertEqual(self.defs["item"]["properties"]["attachments"]["items"], {"$ref": "#/$defs/attachment"})
        self.assertEqual(self.defs["item"]["properties"]["attachments"]["maxItems"], signoff.MAX_ITEM_ATTACHMENTS)

    def test_command(self):
        cmd = self.defs["command"]
        self.assertEqual(set(cmd["properties"]), set(signoff.COMMAND_KEYS))
        self.assertEqual(cmd["required"], ["text"])
        self.assertFalse(cmd["additionalProperties"])
        self.assertEqual(cmd["properties"]["lang"]["pattern"], signoff.COMMAND_LANG.pattern)
        self.assertEqual(cmd["properties"]["text"]["maxLength"], signoff.MAX_COMMAND_TEXT)
        self.assertIn("%d lines" % signoff.MAX_COMMAND_LINES, cmd["properties"]["text"]["description"])

    def test_the_docs_promise_no_more_than_the_checks_keep(self):
        # Known invisible and blank characters are refused and the block wraps and is capped; the user still
        # reads a command before running it. No text may say the screen shows all that runs.
        for path in ("plugin/signoff/schema.json", "docs/page.md", "docs/decisions-file.md", "docs/inbox.md",
                     "AGENTS.md", "plugin/skills/signoff/fields.md", "CHANGELOG.md"):
            text = (ROOT / path).read_text(encoding="utf-8")
            for words in ("all that runs", "all that Copy takes", "what you see is what you paste", "in sight"):
                self.assertNotIn(words, text, path)
        for path in ("plugin/signoff/schema.json", "docs/page.md", "plugin/skills/signoff/fields.md", "CHANGELOG.md"):
            self.assertIn("before running", (ROOT / path).read_text(encoding="utf-8"), path)
        listed = self.defs["item"]["properties"]["commands"]
        self.assertEqual(listed["items"], {"$ref": "#/$defs/command"})
        self.assertEqual((listed["minItems"], listed["maxItems"]), (1, signoff.MAX_ITEM_COMMANDS))

    def test_criteria_and_facts(self):
        criterion, fact = self.defs["criterion"], self.defs["fact"]
        self.assertEqual(set(criterion["properties"]), set(signoff.CRITERION_KEYS))
        self.assertEqual(set(criterion["required"]), {"id", "label"})
        self.assertFalse(criterion["additionalProperties"])
        self.assertEqual(set(fact["properties"]), set(signoff.FACT_KEYS))
        self.assertEqual(fact["required"], ["text"])
        self.assertFalse(fact["additionalProperties"])
        self.assertEqual(fact["properties"]["tone"]["enum"], list(signoff.TONE_VALUES))
        self.assertEqual(self.defs["option"]["properties"]["facts"]["additionalProperties"], {"$ref": "#/$defs/fact"})

    def test_sample_follows_the_schema_shape(self):
        # The demo file must load, so the README's example is a real one.
        decisions, images = signoff.load_decisions(ROOT / "examples" / "demo" / "decisions.json")
        self.assertTrue(images)
        self.assertGreaterEqual(len(decisions["items"]), 4)
        # One item compares its options on criteria, so the demo shows the table view too.
        table = [it for it in decisions["items"] if it.get("criteria")]
        self.assertEqual(len(table), 1)
        self.assertTrue(all(o.get("facts") for o in table[0]["options"]))
        # One item has a text attachment, with markup that must stay text on the page.
        attached = [a for it in decisions["items"] for a in it.get("attachments", [])]
        self.assertEqual(len(attached), 1)
        self.assertIn("<b>", attached[0]["text"])
        self.assertTrue(attached[0]["text"].startswith("# "))

    def test_history_demo(self):
        server = signoff.make_history_server(ROOT / "examples" / "history")
        try:
            got = [(e["file"], e["status"], bool(e["outcome"]), bool(e["warning"])) for e in server.entries]
        finally:
            server.server_close()
        self.assertEqual(got, [("decisions.json", "done", False, True), ("round-02.json", "error", False, False),
                               ("round-01.json", "done", True, False), ("round-02.json", "done", True, False),
                               ("round-03.json", "open", False, False)])

    def test_demo_rounds_load_as_tabs(self):
        server = signoff.make_server(ROOT / "examples" / "demo")
        try:
            self.assertEqual([r.path.name for r in server.rounds], ["round-01.json", "round-02.json", "round-03.json", "round-04.json"])
            items = [it for r in server.rounds for it in r.decisions["items"]]
            self.assertTrue(any(it["other"] for it in items) and any(it["skippable"] for it in items))
            self.assertTrue(any(len(it.get("images", [])) == len(it["options"]) > 1 for it in items))
            self.assertTrue(any(it.get("criteria") for it in items))
            self.assertTrue(any(it.get("attachments") for it in items))
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
