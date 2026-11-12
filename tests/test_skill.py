"""The signoff skill: SKILL.md loads whole on every use, so it stays small, and the files it sends the agent to are there."""
import re
import unittest

from support import ROOT

SKILL = ROOT / "plugin" / "skills" / "signoff"
MAX_SKILL = 9 * 1024


def linked():
    return re.findall(r"\]\(([^)]+\.md)\)", (SKILL / "SKILL.md").read_text(encoding="utf-8"))


class SkillTest(unittest.TestCase):
    def test_skill_md_stays_small(self):
        # Text, not raw bytes: a Windows checkout with CRLF line ends must not count more.
        self.assertLess(len((SKILL / "SKILL.md").read_text(encoding="utf-8").encode("utf-8")), MAX_SKILL)

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


if __name__ == "__main__":
    unittest.main()
