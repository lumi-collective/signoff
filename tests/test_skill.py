"""The signoff skill: SKILL.md loads whole on every use, so it stays small, and the files it sends the agent to are there."""
import json
import re
import unittest

from support import ROOT

SKILL = ROOT / "plugin" / "skills" / "signoff"
MAX_SKILL = 9 * 1024
SETTINGS = json.loads((ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["userConfig"]


def linked():
    return re.findall(r"\]\(([^)]+\.md)\)", (SKILL / "SKILL.md").read_text(encoding="utf-8"))


class SkillTest(unittest.TestCase):
    def test_skill_md_stays_small(self):
        # Text, not raw bytes: a Windows checkout with CRLF line ends must not count more.
        self.assertLess(len((SKILL / "SKILL.md").read_text(encoding="utf-8").encode("utf-8")), MAX_SKILL)

    def test_short_questions_follow_the_chat_setting(self):
        # A string, as hint is: a number type is not known to load on Claude Code 2.1.200. The skill reads it as it
        # reads the folder, and a value it cannot use falls back to the default.
        self.assertEqual((SETTINGS["chat"]["type"], SETTINGS["chat"]["default"]), ("string", "4"))
        skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("${user_config.chat}", skill)
        self.assertIn("any other value means %s." % SETTINGS["chat"]["default"], skill)

    def test_every_run_passes_the_notes_setting(self):
        # Off by default: an empty value writes no note. The skill names the setting in prose and has the agent add
        # its value to every run command, the per-round form too, only when it is an absolute path.
        self.assertEqual((SETTINGS["notes"]["type"], SETTINGS["notes"]["default"]), ("string", ""))
        skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("`${user_config.notes}`", skill)
        self.assertIn('add `--notes "<it>"` to every run command', skill)

    def test_no_setting_placeholder_sits_in_a_command(self):
        # Claude Code (2.1.325) fills ${user_config.X} only from saved options: an unsaved one stays as typed, and a
        # shell stops on it ("bad substitution"). So a placeholder stands alone in a code span, never in a command.
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for block in re.findall(r"```.*?```", text, re.S):
            self.assertNotIn("${user_config.", block)
        for span in re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", text, flags=re.S)):
            if "${user_config." in span:
                self.assertRegex(span, r"^\$\{user_config\.[a-z]+\}$")

    def test_every_setting_is_in_the_listing(self):
        # The directory listing's text says what the plugin does, so it names each setting by its title.
        listing = " ".join((ROOT / "plugin" / "README.md").read_text(encoding="utf-8").split())
        for name, field in SETTINGS.items():
            self.assertIn(field["title"] + " setting", listing, name)

    def test_every_linked_file_is_beside_it(self):
        # An install gets only plugin/, so each file the skill names must be in its own folder.
        for name in linked():
            path = (SKILL / name).resolve()
            self.assertEqual(path.parent, SKILL.resolve(), name)
            self.assertTrue(path.is_file(), name)

    def test_every_file_beside_it_is_linked(self):
        # A moved paragraph the agent is never sent to is lost.
        beside = {p.name for p in SKILL.glob("*.md")} - {"SKILL.md"}
        self.assertEqual(set(linked()), beside)
        self.assertTrue(beside)

    def test_linked_files_need_no_substitution(self):
        # Claude Code fills in ${...} in SKILL.md only; a file the agent reads beside it stays as written.
        for name in linked():
            self.assertNotIn("${", (SKILL / name).read_text(encoding="utf-8"), name)

    def test_line_notes_are_taught(self):
        # SKILL.md lists the key and sends the agent on; the rest is in the two files it reads.
        skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("`attachments`, `lines`, `commands`", skill)
        fields = " ".join((SKILL / "fields.md").read_text(encoding="utf-8").split())
        for words in ("`.diff`", "`.patch`", '"open": true', '"lines": true'):
            self.assertIn(words, fields)
        answers = " ".join((SKILL / "answers.md").read_text(encoding="utf-8").split())
        for words in ("`lines`", "`att`", "`quote`"):
            self.assertIn(words, answers)


if __name__ == "__main__":
    unittest.main()
