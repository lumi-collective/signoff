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

    def test_the_settings_are_folder_hint_and_plan(self):
        self.assertEqual(sorted(SETTINGS), ["folder", "hint", "plan"])

    def test_short_questions_follow_a_fixed_rule(self):
        # No setting for it: at most 4 short text-only questions go to chat, a bigger batch goes whole on the page.
        skill = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("a batch of at most 4 short questions, each text only and not `sensitive` (no picture, board, "
                      "attachment or marks) with 2 to 4 options. A bigger batch, or one with a picture, goes whole on "
                      "the page.", skill)
        self.assertIn("The user's own instructions win", skill)

    def test_the_skill_reads_no_removed_setting(self):
        for path in sorted(SKILL.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for gone in ("${user_config.chat}", "${user_config.notes}", "--notes", "signoff: notes:"):
                self.assertNotIn(gone, text, path.name)

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
        for words in ("`.diff`", "`.patch`", '"open": true', '"lines": true', "`git diff >", "never typed out"):
            self.assertIn(words, fields)  # the shell writes the diff: a typed one costs tokens and can drift
        answers = " ".join((SKILL / "answers.md").read_text(encoding="utf-8").split())
        for words in ("`lines`", "`att`", "`quote`"):
            self.assertIn(words, answers)


if __name__ == "__main__":
    unittest.main()
