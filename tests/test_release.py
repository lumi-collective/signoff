"""scripts/release.py: main and the tag go out only with a green CI run of the release branch on that commit.

Each test has a local bare `origin` and a stand-in gh (a Python file, so it runs on Windows too). The stand-in
answers only `api` (an API-shaped body, filtered by the script's own code) and `release view|create`; every other
call fails. `release create` fails while the tag is not on origin, as `--verify-tag` does.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

BRANCH = "release/9.9.9"
FAKE_GH = r'''
import os, subprocess, sys
a = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as f:
    f.write(" ".join(a) + "\n")
if a[:1] == ["api"]:
    if os.environ.get("FAKE_GH_EXIT"):
        sys.exit(int(os.environ["FAKE_GH_EXIT"]))
    with open(os.environ["FAKE_GH_BODY"], encoding="utf-8") as f:
        sys.stdout.write(f.read())
    sys.exit(0)
made = os.environ["FAKE_GH_RELEASES"]
done = open(made, encoding="utf-8").read().split() if os.path.exists(made) else []
if a[:2] == ["release", "view"]:
    sys.exit(0 if a[2] in done else 1)
if a[:2] == ["release", "create"]:
    on_origin = subprocess.run(["git", "--git-dir", os.environ["FAKE_GH_ORIGIN"], "rev-parse", "-q", "--verify",
                                "refs/tags/" + a[2]], capture_output=True).returncode == 0
    if not on_origin or os.environ.get("FAKE_GH_CREATE_EXIT"):
        sys.exit(1)
    with open(made, "a", encoding="utf-8") as f:
        f.write(a[2] + "\n")
    sys.exit(0)
sys.exit(1)
'''
FAKE_GPG = """#!/bin/sh
cat >/dev/null
echo '[GNUPG:] SIG_CREATED ' >&2
printf -- '-----BEGIN PGP SIGNATURE-----\\n\\nfake\\n-----END PGP SIGNATURE-----\\n'
"""


def run(name, conclusion, branch=BRANCH, event="push"):
    return {"name": name, "head_branch": branch, "event": event, "conclusion": conclusion}


# Newest first, as the API lists them: the main push's own run and another workflow are red, and do not count.
GREEN = [run("Dependabot Updates", "failure"), run("test", "failure", branch="main"),
         run("test", "success", event="pull_request"), run("test", "success")]
posix_only = unittest.skipIf(os.name == "nt", "the stand-in signer is a shell script")


@unittest.skipUnless(shutil.which("git"), "needs git")
class Release(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = (self.tmp / "o" / "signoff.git").as_posix()
        self.work = self.tmp / "work"
        (self.tmp / "gitconfig").write_bytes(b"")
        env = {"GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
               "FAKE_GH_LOG": str(self.tmp / "gh.log"), "FAKE_GH_BODY": str(self.tmp / "runs.json"),
               "FAKE_GH_RELEASES": str(self.tmp / "releases"), "FAKE_GH_ORIGIN": self.origin}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("FAKE_GH_EXIT", "FAKE_GH_CREATE_EXIT"):
            os.environ.pop(k, None)
        fake = self.tmp / "gh.py"
        fake.write_text(FAKE_GH, encoding="utf-8")
        gh = mock.patch.object(release, "GH", [sys.executable, str(fake)])
        gh.start()
        self.addCleanup(gh.stop)
        self.git("init", "-q", "--bare", "-b", "main", self.origin, cwd=self.tmp)
        self.git("init", "-q", "-b", "main", str(self.work), cwd=self.tmp)
        self.git("remote", "add", "origin", self.origin)
        if os.name != "nt":
            gpg = self.tmp / "gpg.sh"
            gpg.write_text(FAKE_GPG, encoding="utf-8")
            gpg.chmod(0o755)
            self.git("config", "gpg.program", str(gpg))
        self.write("plugin/.claude-plugin/plugin.json", '{"name": "signoff", "version": "9.9.9"}\n')
        self.write("CHANGELOG.md", "# Changelog\n\n## 9.9.9\n\n- A change.\n\n## 9.9.8\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.git("push", "-q", "origin", "main")
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", BRANCH)
        self.write("x.txt", "release\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "release")
        self.git("push", "-q", "origin", BRANCH)
        self.head = self.git("rev-parse", "HEAD")
        self.notes = self.tmp / "notes.md"
        self.notes.write_text("**Highlights**\n", encoding="utf-8")
        self.body(GREEN)
        cwd = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, cwd)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.work, check=True, capture_output=True,
                              text=True).stdout.strip()

    def write(self, rel, text):
        p = self.work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))

    def body(self, runs):
        Path(os.environ["FAKE_GH_BODY"]).write_text(json.dumps({"workflow_runs": runs}), encoding="utf-8")

    def tag_here(self, commit=None):
        self.git("tag", "-s", "v9.9.9", "-m", "Signoff 9.9.9", commit or self.head)

    def gh_log(self):
        return Path(os.environ["FAKE_GH_LOG"]).read_text(encoding="utf-8")

    def ship(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = release.main(["--title", "Signoff 9.9.9: t", "--notes", str(self.notes)])
        return code, err.getvalue()

    def origin_ref(self, ref):
        return subprocess.run(["git", "--git-dir", self.origin, "rev-parse", "-q", "--verify", ref],
                              capture_output=True, text=True).stdout.strip()

    def assertNothingSent(self):
        self.assertEqual(self.origin_ref("refs/heads/main"), self.base)
        self.assertEqual(self.origin_ref("refs/tags/v9.9.9"), "")
        self.assertFalse(Path(os.environ["FAKE_GH_RELEASES"]).exists())

    def refuses(self, needle):
        code, err = self.ship()
        self.assertEqual(code, 1, err)
        self.assertIn(needle, err)
        self.assertNothingSent()

    @posix_only
    def test_green_signs_and_ships_main_tag_and_release_and_a_rerun_is_a_no_op(self):
        code, err = self.ship()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.origin_ref("refs/heads/main"), self.head)
        self.assertEqual(self.origin_ref("refs/tags/v9.9.9^{commit}"), self.head)
        self.assertIn("BEGIN PGP SIGNATURE", self.git("cat-file", "tag", "v9.9.9"))
        self.assertIn("api repos/o/signoff/actions/runs?head_sha=%s&per_page=50\n" % self.head, self.gh_log())
        self.assertIn("release view v9.9.9 -R o/signoff\n", self.gh_log())
        self.assertEqual(Path(os.environ["FAKE_GH_RELEASES"]).read_text(encoding="utf-8").split(), ["v9.9.9"])
        code, err = self.ship()
        self.assertEqual(code, 0, err)
        self.assertEqual(Path(os.environ["FAKE_GH_RELEASES"]).read_text(encoding="utf-8").split(), ["v9.9.9"])

    @posix_only
    def test_a_rejected_tag_keeps_main_back(self):
        self.git("tag", "-s", "v9.9.9", "-m", "x", self.base)
        self.git("push", "-q", "origin", "v9.9.9")
        self.git("tag", "-d", "v9.9.9")
        code, err = self.ship()
        self.assertEqual(code, 1, err)
        self.assertEqual(self.origin_ref("refs/heads/main"), self.base)

    @posix_only
    def test_a_failed_release_is_made_by_a_rerun(self):
        self.tag_here()
        os.environ["FAKE_GH_CREATE_EXIT"] = "1"
        code, err = self.ship()
        self.assertEqual(code, 1)
        self.assertIn("run it again", err)
        self.assertEqual(self.origin_ref("refs/heads/main"), self.head)
        del os.environ["FAKE_GH_CREATE_EXIT"]
        self.assertEqual(self.ship()[0], 0)
        self.assertEqual(Path(os.environ["FAKE_GH_RELEASES"]).read_text(encoding="utf-8").split(), ["v9.9.9"])

    def test_red_unfinished_missing_or_main_only_runs_send_nothing(self):
        for runs, word in (([run("test", "failure")], "'failure'"),
                           ([run("test", "success", event="pull_request"), run("test", None)], "'none'"),
                           ([run("test", "failure", event="pull_request"), run("test", "success")], "'failure'"),
                           ([], "'none'"), ([run("test", "success", branch="main")], "'none'")):
            with self.subTest(runs=runs):
                self.body(runs)
                self.refuses(word)

    def test_a_gh_that_fails_or_answers_no_object_sends_nothing(self):
        for text in ("{}", "[]", "not json"):
            with self.subTest(body=text):
                Path(os.environ["FAKE_GH_BODY"]).write_text(text, encoding="utf-8")
                self.refuses("'none'" if text == "{}" else "could not read the CI runs")
        os.environ["FAKE_GH_EXIT"] = "1"
        self.refuses("could not read the CI runs")
        with mock.patch.object(release, "GH", [str(self.tmp / "no-such-gh")]):
            self.refuses("could not run gh")

    def test_a_changelog_without_the_section_in_the_commit_sends_nothing(self):
        self.write("CHANGELOG.md", "# Changelog\n\n## 9.9.8\n")
        self.git("commit", "-q", "-am", "no section")
        self.git("push", "-q", "origin", BRANCH)
        self.refuses("## 9.9.9")

    def test_the_version_comes_from_the_commit_not_the_working_tree(self):
        self.write("CHANGELOG.md", "# Changelog\n\n## 9.9.8\n")
        self.write("plugin/.claude-plugin/plugin.json", '{"name": "signoff", "version": "9.9.8"}\n')
        self.git("tag", "-a", "v9.9.9", "-m", "x", self.head)
        self.refuses("v9.9.9 is not signed")

    def test_main_an_unpushed_head_or_a_wrong_tag_sends_nothing(self):
        self.git("tag", "-a", "v9.9.9", "-m", "x", self.base)
        self.refuses("git tag -d v9.9.9")
        self.git("tag", "-d", "v9.9.9")
        self.git("tag", "-a", "v9.9.9", "-m", "unsigned", self.head)
        self.refuses("not signed")
        self.git("tag", "-d", "v9.9.9")
        self.git("tag", "v9.9.9", self.head)
        self.refuses("not signed")
        self.git("tag", "-d", "v9.9.9")
        self.write("y.txt", "more\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "not pushed")
        self.refuses("push the branch")
        self.git("checkout", "-q", "main")
        self.refuses("not from main")


if __name__ == "__main__":
    unittest.main()
