#!/usr/bin/env python3
"""Ship a Signoff release: main and its signed tag go out only when the CI is green on that very commit.

Run it on the release branch's tip, once the branch is pushed and its `test` run has finished:

    python3 -I -B scripts/release.py --title "Signoff X.Y.Z: <headline>" --notes <notes file>

The marketplace serves main's tip, so pushing main is the release. Before that the script checks the commit (not
main, the same as origin/<branch>, a `## X.Y.Z` section in its CHANGELOG.md, the tag `vX.Y.Z` absent or signed on
this commit), then asks gh for the `test` runs of this branch on this commit. Only when every one succeeded does it
sign the tag when it is missing and push main and the tag together, fast-forward only. Anything else sends nothing. Then it makes
the GitHub Release when there is none; a rerun after a failed Release pushes nothing new and makes it.
"""
import argparse
import json
import os
import re
import subprocess
import sys

GH = ["gh"]
WORKFLOW = "test"


class Refused(Exception):
    pass


def git(*args, env=None):
    p = subprocess.run(["git", *args], capture_output=True, text=True, env=env)
    if p.returncode:
        raise Refused("git %s failed: %s" % (args[0], p.stderr.strip()))
    return p.stdout.strip()


def gh(*args):
    try:
        return subprocess.run(GH + list(args), capture_output=True, text=True)
    except OSError as e:
        raise Refused("could not run gh: %s" % e)


def ci_conclusion(body, branch):
    # The branch's own runs only: the push to main starts a run on the same commit, which would hold back (or, red
    # from a flake, block) a rerun of the Release. Every one must be green: the branch's push run and its PR run
    # share head_branch and head_sha, and a green PR run must not hide an unfinished or red push run.
    mine = [r for r in body.get("workflow_runs") or []
            if r.get("name") == WORKFLOW and r.get("head_branch") == branch]
    bad = [r.get("conclusion") or "none" for r in mine if r.get("conclusion") != "success"]
    return bad[0] if bad else "success" if mine else "none"


def ship(title, notes):
    head = git("rev-parse", "HEAD")
    # The commit's own files, not the working tree's: the tag goes on the commit.
    version = json.loads(git("show", head + ":plugin/.claude-plugin/plugin.json"))["version"]
    tag = "v" + version
    if not re.search(r"^## " + re.escape(version) + r"\s*$", git("show", head + ":CHANGELOG.md"), re.M):
        raise Refused("CHANGELOG.md has no '## %s' section in %s" % (version, head[:7]))
    if not os.path.isfile(notes):
        raise Refused("no notes file at %s" % notes)
    try:
        branch = git("symbolic-ref", "--short", "-q", "HEAD")
    except Refused:
        raise Refused("not on a branch: run it on the release branch")
    if branch == "main":
        raise Refused("run it on the release branch, not from main")
    if git("ls-remote", "origin", "refs/heads/" + branch).split("\t")[0] != head:
        raise Refused("origin/%s is not this commit: push the branch and let its CI run first" % branch)
    m = re.search(r"([^/:]+/[^/:]+?)(?:\.git)?/?$", git("remote", "get-url", "origin"))
    if not m:
        raise Refused("cannot tell the GitHub repo from origin's URL")
    slug = m.group(1)
    p = gh("api", "repos/%s/actions/runs?head_sha=%s&per_page=50" % (slug, head))
    try:
        if p.returncode:
            raise ValueError
        body = json.loads(p.stdout)
        if not isinstance(body, dict):
            raise ValueError
        ci = ci_conclusion(body, branch)
    except ValueError:
        raise Refused("could not read the CI runs of %s with gh" % slug)
    if ci != "success":
        raise Refused("the %s run of %s on %s is '%s', not success: wait for a green run (or rerun a flake with "
                      "gh run rerun --failed), then run this again" % (WORKFLOW, branch, head[:7], ci))
    if git("tag", "-l", tag):
        if git("rev-parse", tag + "^{commit}") != head:
            raise Refused("the tag %s points at another commit: if it never went out, remove it with "
                          "git tag -d %s and run this again" % (tag, tag))
        obj = git("cat-file", "-p", "refs/tags/" + tag) if git("cat-file", "-t", "refs/tags/" + tag) == "tag" else ""
        if not re.search(r"^-----BEGIN [A-Z ]*SIGNATURE-----$", obj, re.M):
            raise Refused("the tag %s is not signed: remove it with git tag -d %s and run this again" % (tag, tag))
    else:
        git("tag", "-s", tag, "-m", "Signoff " + version, head, env=dict(os.environ, TZ="UTC"))
    print("→ %s is green on %s; pushing main and %s (fast-forward only)" % (WORKFLOW, head[:7], tag), file=sys.stderr)
    # The commit the gate checked, not a ref read again; both or neither, so no tag without its main.
    git("push", "--atomic", "origin", head + ":refs/heads/main", "refs/tags/%s:refs/tags/%s" % (tag, tag))
    if gh("release", "view", tag, "-R", slug).returncode:
        p = gh("release", "create", tag, "-R", slug, "--verify-tag", "--title", title, "--notes-file", notes)
        if p.returncode:
            print("✗ main and %s are out, but the GitHub Release failed (%s): run it again to make it"
                  % (tag, p.stderr.strip()), file=sys.stderr)
            return 1
    print("✓ released %s" % tag, file=sys.stderr)
    return 0


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--title", required=True, help="the GitHub Release's title")
    a.add_argument("--notes", required=True, help="a Markdown file with the GitHub Release's text")
    args = a.parse_args(argv)
    try:
        return ship(args.title, args.notes)
    except Refused as e:
        print("✗ %s. Nothing was pushed." % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
