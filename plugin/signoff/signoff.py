#!/usr/bin/env python3
"""Signoff: serve a decisions file as a local page, autosave the answers, exit when the user presses Done.

    python3 -I signoff.py <decisions.json | folder> [--no-open] [--timeout <minutes>]

A folder holds one round-NN.json per round; each round is a tab. The answers go to <name>.answers.json beside
each decisions file. On Done the absolute path of each answers file written in this run is printed on stdout,
one per line, and the exit code is 0; after the idle timeout they are printed too and the exit code is 2.
Invalid input exits 1 with a message on stderr. Python 3.9+, standard library only.
"""
import argparse
import datetime
import hashlib
import http.server
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path, PureWindowsPath

HERE = Path(__file__).resolve().parent
MAX_BODY = 256 * 1024
MAX_TEXT = 20000
MAX_DECISIONS = 2 * 1024 * 1024
MAX_ROUND_BODY = 32 * 1024 * 1024
# After the start link is used, a Done ends the run only this many seconds later, so a program that used the link
# before the browser cannot finish the run before the browser arrives and finds the link spent.
DONE_GRACE = 5.0
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif"}
BOARD_TYPES = (".html", ".htm")
MAX_BOARD = 2 * 1024 * 1024
ATTACHMENT_TYPES = (".md", ".txt")
MAX_ATTACHMENT = 256 * 1024
# Attachments go inside the page, so these count their size there, escaped (a "<" takes 6 bytes).
MAX_ATTACHMENTS = 2 * 1024 * 1024  # one round's together
MAX_ITEM_ATTACHMENTS = 20
MAX_ROUND_ATTACHMENTS = 200  # each one is a file read at start
MAX_MARKS = 50
HISTORY_BUDGET = 200 * 1024 * 1024
HISTORY_TEXT_BUDGET = 16 * 1024 * 1024  # all attachments on the past-decisions page
DEFAULT_FOLDER = ".signoff"  # where topic folders live under a project root; the plugin's folder setting can change it
# A board is the agent's own HTML with scripts. Served with this header it runs in a sandbox with an opaque
# origin even when its URL is opened directly: it cannot read the page or send a save, and loads nothing from outside.
# WebRTC is beyond any page policy, so boards must hold nothing secret.
BOARD_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
             "img-src data:; font-src data:; media-src data:; base-uri 'none'; form-action 'none'")
THEME_QUERIES = ("theme=light", "theme=dark")
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
IMG_ROUTE = re.compile(r"^img/(0|[1-9][0-9]{0,3})$")
SAVE_ROUTE = re.compile(r"^save/(0|[1-9][0-9]{0,2})$")
# Boards live under their own token, which opens nothing else: a board can read its own URL.
BOARD_ROUTE = re.compile(r"^(0|[1-9][0-9]{0,3})/(0|[1-9][0-9]{0,3})$")
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}")
ROUND_FILE = re.compile(r"^round-([0-9]{1,4})\.json$")
SAVED_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
LEADING_UNC = re.compile(r"^[\\/]{2}")
# Option ids start with a letter or digit, so these two choices can never clash with one of them.
OTHER, SKIP = "_other", "_skip"

TOP_KEYS = ("version", "title", "tab", "intro", "notice", "lang", "items", "free_text")
TOP_REQUIRED = ("version", "title", "items")
ITEM_KEYS = ("id", "group", "title", "description", "cost", "images", "select", "options", "recommended", "comment",
             "other", "skippable", "marks", "sensitive", "criteria", "attachments")
ITEM_REQUIRED = ("id", "title", "options")
IMAGE_KEYS = ("src", "html", "caption", "alt", "boxes")
ATTACHMENT_KEYS = ("src", "caption")
OUTCOME_KEYS = ("log", "line", "date")
OPTION_KEYS = ("id", "label", "detail", "facts")
CRITERION_KEYS = ("id", "label")
FACT_KEYS = ("text", "tone")
TONE_VALUES = ("good", "mid", "bad")  # the page uses them as class names, so the list stays closed
FREE_TEXT_KEYS = ("title", "placeholder")
SELECT_VALUES = ("one", "many")
SOURCE_VALUES = ("recommended", "accept_all", "user")
ANSWER_KEYS = ("answers", "free_text", "done", "accepted")
STORED_KEYS = ANSWER_KEYS + ("version", "saved", "decisions", "accepted_at", "changed_after_accept")

EXIT_DONE, EXIT_ERROR, EXIT_TIMEOUT = 0, 1, 2


class DecisionsError(ValueError):
    """The decisions file, or the answers file beside it, cannot be used. The message says where."""


def _keys(obj, allowed, required, where):
    if not isinstance(obj, dict):
        raise DecisionsError("%s: must be an object" % where)
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise DecisionsError("%s: unknown key %s (allowed: %s)" % (where, ", ".join(map(repr, unknown)), ", ".join(allowed)))
    missing = [k for k in required if k not in obj]
    if missing:
        raise DecisionsError("%s: missing %s" % (where, ", ".join(map(repr, missing))))


def _text(obj, key, where):
    if key in obj and not isinstance(obj[key], str):
        raise DecisionsError("%s: %r must be a string" % (where, key))


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _box_ok(box):
    """[x, y, w, h] in percent of the picture, inside it."""
    if not (isinstance(box, list) and len(box) == 4 and all(_number(v) for v in box)):
        return False
    x, y, w, h = box
    return 0 <= x <= 100 and 0 <= y <= 100 and 0 < w and 0 < h and x + w <= 100.01 and y + h <= 100.01


def _inside(folder, src, where):
    """Resolve a file named in the decisions inside their folder. The text is checked first, on every OS: on
    Windows a UNC path (\\\\host\\share) would open a network connection and send the user's NTLM hash, and a
    drive-relative one (C:foo) reaches another folder. An absolute local path passes, as in 0.1.0, and must then
    lead inside the folder. The message never repeats a refused path."""
    if CONTROL_CHARS.search(src):
        raise DecisionsError("%s: the path holds a control character" % where)
    # A drive without a root is drive-relative (C:foo); C:\x is an absolute path, left to the folder check. Two
    # leading separators cover every UNC form, also the one-part \\host that Python 3.9 gives no drive.
    win = PureWindowsPath(src)
    if LEADING_UNC.match(src) or (win.drive and not win.root):
        raise DecisionsError("%s: the path must be relative to the decisions file's folder; a drive or network "
                             "path points outside it" % where)
    try:
        path = (folder / src).resolve()
    except (ValueError, RuntimeError, OSError) as e:  # a link loop on Python before 3.13, a name too long
        raise DecisionsError("%s: %r cannot be resolved (%s)" % (where, src, type(e).__name__))
    if path != folder and folder not in path.parents:
        raise DecisionsError("%s: %r is outside the decisions file's folder (links are followed)" % (where, src))
    return path


def _image(img, folder, where, lenient=False):
    """Check one entry of an item's images: a picture (src) or a board (html). Returns its resolved path, or None
    when lenient and the file is missing."""
    _keys(img, IMAGE_KEYS, (), where)
    if ("src" in img) == ("html" in img):
        raise DecisionsError("%s: needs exactly one of 'src' (a picture) or 'html' (a board)" % where)
    for key in ("src", "html", "caption", "alt"):
        _text(img, key, where)
    board = "html" in img
    if board and "boxes" in img:
        raise DecisionsError("%s: 'boxes' are for pictures; a board draws its own highlights" % where)
    boxes = img.get("boxes", [])
    if not isinstance(boxes, list):
        raise DecisionsError("%s: 'boxes' must be a list of [x, y, w, h] in percent" % where)
    for box in boxes:
        if not _box_ok(box):
            raise DecisionsError("%s: 'boxes' entry %r must be [x, y, w, h] in percent of the image, inside 0-100"
                                 % (where, box))
    src = img["html" if board else "src"]
    path = _inside(folder, src, where)
    suffix = path.suffix.lower()
    if board:
        if suffix not in BOARD_TYPES:
            raise DecisionsError("%s: %r must be an HTML file (.html)" % (where, src))
    elif suffix == ".svg":
        raise DecisionsError("%s: %r is SVG, which can carry scripts; render it to PNG first" % (where, src))
    elif suffix not in IMAGE_TYPES:
        raise DecisionsError("%s: %r must be PNG, JPEG, WebP or GIF" % (where, src))
    if not path.is_file():
        if lenient:
            return None
        raise DecisionsError("%s: %r not found" % (where, src))
    return path


def _attachment(att, folder, where, used, lenient=False):
    """Check one attachment and read its text into it, as 'text'. A path rule broken fails in both modes, as for
    pictures; a file that cannot be read fails too, except with lenient (the history page), where it gets
    'missing', the reason. used[0] counts the round's bytes so far, as they go into the page. Messages name src
    as written, never a local path."""
    _keys(att, ATTACHMENT_KEYS, ("src",), where)
    for key in ATTACHMENT_KEYS:
        _text(att, key, where)
    src = att["src"]
    if PureWindowsPath(src).anchor:  # a new key, and the page shows src: no local path, not even a rooted one
        raise DecisionsError("%s: the path must be relative to the decisions file's folder" % where)
    path = _inside(folder, src, where)
    if path.suffix.lower() not in ATTACHMENT_TYPES:
        raise DecisionsError("%s: %r must be a text file, .md or .txt" % (where, src))
    reason = None
    try:
        if not path.exists():
            reason = "not found"
        elif not path.is_file():
            reason = "not a file"
        elif path.stat().st_size > MAX_ATTACHMENT:
            reason = "larger than %d KiB" % (MAX_ATTACHMENT >> 10)
        else:
            text = read_image(path, MAX_ATTACHMENT).decode("utf-8-sig")
            size = _page_size(text)
            if used[0] + size > MAX_ATTACHMENTS:
                reason = "over the %d MiB limit for one round's attachments in the page" % (MAX_ATTACHMENTS >> 20)
            else:
                att["text"] = text
                used[0] += size
    except OSError as e:
        reason = "cannot be read (%s)" % (e.strerror or type(e).__name__)  # str(e) would hold the path
    except UnicodeDecodeError:
        reason = "not UTF-8 text"
    except (DecisionsError, RuntimeError):  # RuntimeError: a link loop swapped in, on Python before 3.13
        reason = "changed while Signoff was reading it"
    if reason is None:
        return
    if not lenient:
        raise DecisionsError("%s: %r: %s" % (where, src, reason))
    att["missing"] = reason


def _facts(facts, criteria, where):
    """An option's facts: {criterion id: {text, tone}}, only for the item's own criteria."""
    if not criteria:
        raise DecisionsError("%s: 'facts' needs the item's 'criteria'" % where)
    if not isinstance(facts, dict):
        raise DecisionsError("%s: 'facts' must be an object keyed by criterion id" % where)
    for cid, fact in facts.items():
        if cid not in criteria:
            raise DecisionsError("%s: 'facts': %r is not one of this item's criteria (%s)"
                                 % (where, cid, ", ".join(criteria)))
        fwhere = "%s, fact %r" % (where, cid)
        _keys(fact, FACT_KEYS, ("text",), fwhere)
        _text(fact, "text", fwhere)
        if "tone" in fact and fact["tone"] not in TONE_VALUES:
            raise DecisionsError("%s: 'tone' must be one of %s" % (fwhere, ", ".join(TONE_VALUES)))


def _item(item, n, folder, images, used, lenient=False):
    where = "item %d" % n
    if isinstance(item, dict) and isinstance(item.get("id"), str):
        where = "item %d (%s)" % (n, item["id"])
    _keys(item, ITEM_KEYS, ITEM_REQUIRED, where)
    if not isinstance(item["id"], str) or not ID_PATTERN.fullmatch(item["id"]):
        raise DecisionsError("%s: 'id' must match %s" % (where, ID_PATTERN.pattern))
    for key in ("group", "title", "description", "cost"):
        _text(item, key, where)
    item.setdefault("select", "one")
    item.setdefault("comment", True)
    item.setdefault("other", False)
    item.setdefault("skippable", False)
    item.setdefault("marks", False)
    item.setdefault("sensitive", False)
    if item["select"] not in SELECT_VALUES:
        raise DecisionsError("%s: 'select' must be \"one\" or \"many\"" % where)
    for key in ("comment", "other", "skippable", "marks", "sensitive"):
        if not isinstance(item[key], bool):
            raise DecisionsError("%s: %r must be true or false" % (where, key))
    criteria = {}  # criterion ids as keys: in order for the error text, and a fast lookup for each fact
    if "criteria" in item:
        if not isinstance(item["criteria"], list) or not item["criteria"]:
            raise DecisionsError("%s: 'criteria' must be a non-empty list of {id, label}" % where)
        for k, crit in enumerate(item["criteria"], 1):
            cwhere = "%s, criterion %d" % (where, k)
            _keys(crit, CRITERION_KEYS, CRITERION_KEYS, cwhere)
            if not isinstance(crit["id"], str) or not ID_PATTERN.fullmatch(crit["id"]):
                raise DecisionsError("%s: 'id' must match %s" % (cwhere, ID_PATTERN.pattern))
            if crit["id"] in criteria:
                raise DecisionsError("%s: duplicate criterion id %r" % (cwhere, crit["id"]))
            criteria[crit["id"]] = None
            _text(crit, "label", cwhere)
    options = item["options"]
    if not isinstance(options, list) or len(options) < (2 if item["select"] == "one" else 1):
        raise DecisionsError("%s: 'options' needs at least %s" % (where, "two" if item["select"] == "one" else "one"))
    seen = set()
    for k, opt in enumerate(options, 1):
        owhere = "%s, option %d" % (where, k)
        _keys(opt, OPTION_KEYS, ("id", "label"), owhere)
        if not isinstance(opt["id"], str) or not ID_PATTERN.fullmatch(opt["id"]):
            raise DecisionsError("%s: 'id' must match %s" % (owhere, ID_PATTERN.pattern))
        if opt["id"] in seen:
            raise DecisionsError("%s: duplicate option id %r" % (owhere, opt["id"]))
        seen.add(opt["id"])
        _text(opt, "label", owhere)
        _text(opt, "detail", owhere)
        if "facts" in opt:
            _facts(opt["facts"], criteria, owhere)
    if "recommended" in item:
        rec = item["recommended"]
        if item["select"] == "one":
            if not isinstance(rec, str) or rec not in seen:
                raise DecisionsError("%s: 'recommended' must be one option id (%s)" % (where, ", ".join(sorted(seen))))
        elif not (isinstance(rec, list) and all(isinstance(r, str) and r in seen for r in rec)
                  and len(set(rec)) == len(rec)):
            raise DecisionsError("%s: 'recommended' must be a list of option ids (%s)" % (where, ", ".join(sorted(seen))))
    shots = item.get("images", [])
    if not isinstance(shots, list):
        raise DecisionsError("%s: 'images' must be a list" % where)
    for k, img in enumerate(shots, 1):
        images.append(_image(img, folder, "%s, image %d" % (where, k), lenient))
    attachments = item.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > MAX_ITEM_ATTACHMENTS:
        raise DecisionsError("%s: 'attachments' must be a list of up to %d" % (where, MAX_ITEM_ATTACHMENTS))
    used[1] += len(attachments)
    if used[1] > MAX_ROUND_ATTACHMENTS:
        raise DecisionsError("%s: more than %d attachments in this round" % (where, MAX_ROUND_ATTACHMENTS))
    for k, att in enumerate(attachments, 1):
        _attachment(att, folder, "%s, attachment %d" % (where, k), used, lenient)


def load_decisions(path, lenient=False):
    """Read and check a decisions file. Returns (decisions with defaults filled, the paths of its pictures and
    boards in page order). Attachments are read into the decisions. With lenient=True a missing picture or board
    is None, and an attachment that cannot be read is marked missing, instead of an error."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as e:
        raise DecisionsError("%s: cannot read it (%s)" % (path, e.strerror or e))
    if size > MAX_DECISIONS:
        raise DecisionsError("%s: larger than %d bytes" % (path, MAX_DECISIONS))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise DecisionsError("%s: cannot read it (%s)" % (path, e.strerror or e))
    except ValueError as e:
        raise DecisionsError("%s: not valid JSON (%s)" % (path, e))
    except RecursionError:
        raise DecisionsError("%s: not valid JSON (nested too deeply)" % path)
    try:
        json.dumps(data, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        raise DecisionsError("%s: contains an invalid character (a lone UTF-16 surrogate escape)" % path)
    folder = path.resolve().parent
    _keys(data, TOP_KEYS, TOP_REQUIRED, "decisions file")
    if data["version"] != 1 or isinstance(data["version"], bool):
        raise DecisionsError("decisions file: 'version' must be 1")
    for key in ("title", "tab", "intro", "notice", "lang"):
        _text(data, key, "decisions file")
    free = data.setdefault("free_text", {})
    _keys(free, FREE_TEXT_KEYS, (), "free_text")
    for key in FREE_TEXT_KEYS:
        _text(free, key, "free_text")
    items = data["items"]
    if not isinstance(items, list) or not items:
        raise DecisionsError("decisions file: 'items' must be a non-empty list")
    images, ids, used = [], set(), [0, 0]  # attachments: bytes in the page, count
    for n, item in enumerate(items, 1):
        _item(item, n, folder, images, used, lenient)
        if item["id"] in ids:
            raise DecisionsError("item %d: duplicate id %r" % (n, item["id"]))
        ids.add(item["id"])
    return data, images


def check_answers(state, decisions, stored=False):
    """Check a save body (or, with stored=True, the answers file) against the decisions. Raises ValueError."""
    if not isinstance(state, dict):
        raise ValueError("answers must be an object")
    extra = set(state) - set(STORED_KEYS if stored else ANSWER_KEYS)
    if extra:
        raise ValueError("unknown key %s" % ", ".join(sorted(map(repr, extra))))
    if stored:
        if state.get("version") != 1 or isinstance(state.get("version"), bool):
            raise ValueError("'version' must be 1")
        if not isinstance(state.get("saved"), str) or not SAVED_PATTERN.fullmatch(state["saved"]):
            raise ValueError("'saved' must be a time like 2026-01-01T12:00:00Z")
        link = state.get("decisions", {"file": "", "sha256": "0" * 64})
        if not (isinstance(link, dict) and set(link) == {"file", "sha256"} and isinstance(link["file"], str)
                and isinstance(link["sha256"], str) and SHA256_PATTERN.fullmatch(link["sha256"])):
            raise ValueError("'decisions' must be {file, sha256}")
        at = state.get("accepted_at", "2026-01-01T00:00:00Z")
        if not isinstance(at, str) or not SAVED_PATTERN.fullmatch(at) \
                or not isinstance(state.get("changed_after_accept", False), bool):
            raise ValueError("'accepted_at' must be a time and 'changed_after_accept' true or false")
    if not isinstance(state.get("accepted", False), bool):
        raise ValueError("'accepted' must be true or false")
    answers, free, done = state.get("answers", {}), state.get("free_text", ""), state.get("done", False)
    if not isinstance(answers, dict) or not isinstance(free, str) or not isinstance(done, bool):
        raise ValueError("'answers' must be an object, 'free_text' a string, 'done' true or false")
    if len(free) > MAX_TEXT:
        raise ValueError("'free_text' is longer than %d characters" % MAX_TEXT)
    items = {it["id"]: it for it in decisions["items"]}
    clean = {}
    for key, ans in answers.items():
        item = items.get(key)
        if item is None:
            raise ValueError("answer for unknown item %r" % key)
        if not isinstance(ans, dict) or set(ans) - {"choice", "comment", "source", "other", "marks", "private"}:
            raise ValueError("answer %r must be {choice, comment, source, other, marks, private}" % key)
        if not isinstance(ans.get("private", False), bool):
            raise ValueError("answer %r: private must be true or false" % key)
        choice, comment = ans.get("choice"), ans.get("comment", "")
        if "source" in ans and ans["source"] not in SOURCE_VALUES:
            raise ValueError("answer %r: source must be one of %s" % (key, ", ".join(SOURCE_VALUES)))
        valid = {o["id"] for o in item["options"]}
        if item["other"]:
            valid.add(OTHER)
        if item["skippable"]:
            valid.add(SKIP)
        if item["select"] == "one":
            ok = choice is None or (isinstance(choice, str) and choice in valid)
        else:
            ok = isinstance(choice, list) and all(isinstance(c, str) and c in valid for c in choice) \
                and len(set(choice)) == len(choice)
        if not ok:
            raise ValueError("answer %r: choice %r is not an option of this item" % (key, choice))
        if not isinstance(comment, str) or len(comment) > MAX_TEXT:
            raise ValueError("answer %r: comment must be a string up to %d characters" % (key, MAX_TEXT))
        picked = choice if isinstance(choice, list) else [choice]
        if SKIP in picked and len(picked) > 1:
            raise ValueError("answer %r: skip cannot be combined with another choice" % key)
        clean[key] = {"choice": choice, "comment": comment}
        if OTHER in picked:
            other = ans.get("other", "")
            if not isinstance(other, str) or len(other) > MAX_TEXT:
                raise ValueError("answer %r: other must be a string up to %d characters" % (key, MAX_TEXT))
            clean[key]["other"] = other
        elif "other" in ans:
            raise ValueError("answer %r: other text without the Other choice" % key)
        if "marks" in ans:
            clean[key]["marks"] = _check_marks(ans["marks"], item, key)
        if "source" in ans:
            clean[key]["source"] = ans["source"]
        if "private" in ans:
            clean[key]["private"] = ans["private"]
    result = {"answers": clean, "free_text": free, "done": done, "accepted": state.get("accepted", False)}
    try:
        json.dumps(result, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("text contains an invalid character (a lone UTF-16 surrogate)")
    return result


def _check_marks(marks, item, key):
    if not item["marks"]:
        raise ValueError("answer %r: this item takes no marks" % key)
    if not isinstance(marks, list) or len(marks) > MAX_MARKS:
        raise ValueError("answer %r: marks must be a list of up to %d" % (key, MAX_MARKS))
    pictures = [i for i, img in enumerate(item.get("images", [])) if "src" in img]
    for mark in marks:
        ok = isinstance(mark, dict) and set(mark) == {"image", "box", "comment"}
        ok = ok and isinstance(mark["image"], int) and not isinstance(mark["image"], bool) and mark["image"] in pictures
        ok = ok and _box_ok(mark["box"]) and isinstance(mark["comment"], str) and len(mark["comment"]) <= MAX_TEXT
        if not ok:
            raise ValueError("answer %r: a mark must be {image: <picture index>, box: [x, y, w, h] in percent, "
                             "comment: <text>}" % key)
    return marks


def load_outcome(path):
    """The agent's note of what a round led to: {log, line, date}, all text."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not (isinstance(data, dict) and set(data) == set(OUTCOME_KEYS)
            and all(isinstance(data[k], str) and len(data[k]) <= MAX_TEXT for k in OUTCOME_KEYS)):
        raise ValueError("must be {log, line, date}, all text")
    return data


def answers_path_for(decisions_path):
    p = Path(decisions_path).resolve()
    return p.with_name(p.stem + ".answers.json")


def ensure_gitignore(folder):
    """Keep raw answers out of git: write a .gitignore only when the folder has none. Never overwrites."""
    try:
        with open(Path(folder) / ".gitignore", "x", encoding="utf-8") as f:
            f.write("*.answers.json\n*.answers.md\n")
        return True
    except FileExistsError:
        return False


def write_atomic(path, data):
    """Write text or bytes to path through a temporary file and a rename. Bytes go out as given: no line-ending
    translation, so a hash of them is the hash of the file."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    # The temp name ends like the target (.answers.json or .answers.md), so the .gitignore covers a leftover one.
    suffix = ".answers.md" if path.name.endswith(".md") else ".answers.json"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


MAX_IMAGE = 20 * 1024 * 1024


def read_image(path, limit=MAX_IMAGE):
    """Read a picture or board and make sure the file read is the one that was checked: a link swapped in
    between the check and the read would point outside the folder. Non-blocking, and only a regular file: a FIFO
    swapped in after the checks would otherwise block the open or the read for good."""
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise DecisionsError("%s: not a regular file" % path)
        try:
            real = path.resolve()
        except RuntimeError:  # a link loop swapped in after the open, on Python before 3.13
            real = None
        same = os.stat(str(real)) if real == path else None
        if same is None or (st.st_dev, st.st_ino) != (same.st_dev, same.st_ino):
            raise DecisionsError("%s: changed while Signoff was reading it" % path)
        with os.fdopen(fd, "rb") as f:
            fd = None
            data = f.read(limit + 1)
        if len(data) > limit:
            raise DecisionsError("%s: larger than %d MiB" % (path, limit >> 20))
        return data
    finally:
        if fd is not None:
            os.close(fd)


def read_board(path):
    data = read_image(path, MAX_BOARD)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise DecisionsError("%s: a board must be UTF-8 text" % path)
    return data


_MD_INLINE = re.compile(r"([\\`*_\[\]<>|])")
# At the start of a line: a heading, a quote, a list item, a thematic break or setext underline, a code fence.
_MD_LINE_START = re.compile(r"^(#|[-+](?=\s)|[-=~](?=[-=~]{2})|\d+(?=[.)]))")


def md_text(text):
    """Plain text for Markdown. Inline markup characters and HTML brackets are backslash-escaped everywhere;
    block markers only at the start of a line. Leading spaces are dropped (no code blocks), and line breaks
    become hard breaks inside the same block."""
    out = []
    for line in text.splitlines():
        line = _MD_INLINE.sub(r"\\\1", line.strip())
        m = _MD_LINE_START.match(line)
        if m:
            # Escape the marker itself; for "1." or "1)" the escape goes before the dot or bracket.
            cut = m.end() if m.group(1)[0].isdigit() else 0
            line = line[:cut] + "\\" + line[cut:]
        if line:
            out.append(line)
    return "  \n  ".join(out) or "(none)"


def answers_markdown(decisions, record):
    """The answers as a Markdown page for people; the JSON stays the source of truth."""
    out = ["# " + md_text(decisions["title"].replace("\n", " ")), ""]
    group = None
    for n, item in enumerate(decisions["items"], 1):
        if item.get("group") and item["group"] != group:
            group = item["group"]
            out += ["## " + md_text(group.replace("\n", " ")), ""]
        ans = record["answers"].get(item["id"], {})
        choice = ans.get("choice")
        picked = choice if isinstance(choice, list) else ([] if choice is None else [choice])
        rec = item.get("recommended", [])
        rec = rec if isinstance(rec, list) else [rec]
        # A private answer keeps its text in the JSON for the agent; this copy for people only says it is there.
        private = ans.get("private") is True

        def text(value):
            return "(private)" if private and value.strip() else md_text(value)
        labels = {o["id"]: md_text(o["label"]) for o in item["options"]}
        labels[SKIP] = "Skipped"
        labels[OTHER] = "Other: " + text(ans.get("other", ""))
        # Whether a choice was the recommendation only means something when there was one.
        shown = ", ".join(labels[c] + ("" if not rec else " (recommended)" if c in rec else " (not recommended)")
                          for c in picked) or "nothing chosen"
        out += ["### %d. %s" % (n, md_text(item["title"].replace("\n", " "))), "",
                "- Choice: " + shown,
                "- Source: " + {"recommended": "recommendation, not changed", "accept_all": "Accept all",
                                "user": "the user"}.get(ans.get("source"), "unknown"),
                "- Comment: " + text(ans.get("comment", ""))]
        for k, mark in enumerate(ans.get("marks", []), 1):
            out.append("- Mark %d, picture %d at %s (x, y, w, h in %%): %s" % (
                k, mark["image"] + 1, ", ".join("%g" % v for v in mark["box"]), text(mark["comment"])))
        for att in item.get("attachments", []):  # its name only: the text is the agent's, and stays in its file
            name = md_text(att["src"].replace("\n", " "))
            caption = att.get("caption", "").replace("\n", " ")
            out.append("- Attachment: " + ("%s (%s)" % (md_text(caption), name) if caption.strip() else name))
        out.append("")
    title = decisions["free_text"].get("title") or "Anything else"
    out += ["## " + md_text(title.replace("\n", " ")), "", md_text(record["free_text"]), "",
            "Saved: %s  " % record["saved"], "Accepted: %s  " % _accepted_text(record),
            "Done: %s" % ("yes" if record["done"] else "no"), ""]
    return "\n".join(out)


def _accepted_text(record):
    if record.get("accepted"):
        return "yes, %s" % record["accepted_at"]
    if record.get("changed_after_accept"):
        return "no, changed after it was accepted"
    return "no"


def _script_json(obj):
    # Inside <script type="application/json"> only "</" could end the block; escaping <, > and & rules it out.
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e") \
        .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _page_size(text):
    """The bytes a text takes in the page's data, escaped."""
    return len(_script_json(text).encode("utf-8"))


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "signoff"
    sys_version = ""
    timeout = 60  # a connection that sends nothing is dropped

    def log_message(self, *args):
        pass  # stderr carries only the URL and errors

    def send_error(self, code, message=None, explain=None):
        # http.server's own errors (unknown method, bad request line) get the same headers and no HTML.
        self.close_connection = True
        self._send(code, ("%d %s\n" % (code, message or self.responses.get(code, ("",))[0])).encode("utf-8", "replace"))

    def _refuse(self, code, text):
        """Answer a refused POST, then read and drop up to 1 MiB of the unread body, so the client gets the
        answer instead of a connection reset."""
        self._send(code, text)
        try:
            self.connection.settimeout(0.2)
            left = 1 << 20
            while left > 0:
                chunk = self.rfile.read1(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:
            pass

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _route(self):
        """Host and token checks for every request. Returns the path after /<token>/, or None after an error."""
        srv = self.server
        if self.headers.get("Host") != srv.host:
            self._send(403, ("wrong host: Signoff answers only to %s; over SSH, forward the same port number\n"
                             % srv.host).encode("utf-8"))
            return None
        path, mark, query = self.path.partition("?")
        if not path.startswith("/") or "#" in self.path or (mark and query not in THEME_QUERIES):
            self._send(404, b"not found\n")
            return None
        self.query = query if mark else None  # only a board takes one: ?theme=light or ?theme=dark
        token, slash, rest = path[1:].partition("/")
        given = token.encode("utf-8", "surrogateescape")
        self.board_request = bool(slash) and secrets.compare_digest(given, srv.board_token.encode())
        if self.board_request:
            return rest  # not activity: a board could reload itself to keep the server alive
        if not slash or not secrets.compare_digest(given, srv.token.encode()):
            self._send(404, b"not found\n")
            return None
        srv.touch()  # only requests that carry the token keep the server alive
        return rest

    def _start(self):
        """The single-use start link: the first visit is sent on to the page, any later one is refused and stops
        the run, since Signoff cannot tell whose visit was first."""
        srv = self.server
        given = self.path[3:]
        token = given[:-1] if given.endswith("/") else ""
        if not token or "/" in token or not secrets.compare_digest(token.encode("utf-8", "surrogateescape"),
                                                                    srv.start_token.encode()):
            return self._send(404, b"not found\n")
        with srv.lock:
            used, srv.start_used = srv.start_used, True
            if used:
                srv.start_reused = True  # from now on nothing is stored
            else:
                srv.start_used_at = time.monotonic()
        if not used:
            srv.touch()
            return self._send(303, b"", headers=[("Location", "/%s/" % srv.token)])
        self._send(410, b"This start link was already used, so Signoff has stopped. Run it again for a new link.\n")
        srv.finish(now=True)

    def do_GET(self):
        if self.headers.get("Host") == self.server.host and self.path.startswith("/b/"):
            return self._start()
        rest = self._route()
        if rest is None:
            return
        srv = self.server
        if self.board_request:
            board = BOARD_ROUTE.fullmatch(rest)
            html = srv.board(int(board.group(1)), int(board.group(2))) if board else None
            if html is None:
                return self._send(404, b"not found\n")
            return self._send(200, html, "text/html; charset=utf-8", [("Content-Security-Policy", BOARD_CSP)])
        if self.query is not None:
            return self._send(404, b"not found\n")
        if rest == "":
            nonce = secrets.token_urlsafe(18)
            body = srv.page.replace("__NONCE__", nonce).replace("__DATA__", _script_json(srv.view())).encode("utf-8")
            csp = ("default-src 'none'; script-src 'nonce-%s'; style-src 'nonce-%s'; img-src 'self'; "
                   "connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                   % (nonce, nonce))
            return self._send(200, body, "text/html; charset=utf-8", [("Content-Security-Policy", csp)])
        m = IMG_ROUTE.fullmatch(rest)
        found = srv.picture(int(m.group(1))) if m else None
        if found:
            return self._send(200, found[1], found[0])
        self._send(404, b"not found\n")

    def do_POST(self):
        rest = self._route()
        if rest is None:
            return
        srv = self.server
        if self.board_request:
            return self._refuse(404, b"not found\n")
        if srv.read_only:
            return self._refuse(405, b"read only\n")
        m = SAVE_ROUTE.fullmatch(rest)
        if self.query is not None or not m or int(m.group(1)) >= len(srv.rounds):
            return self._refuse(404, b"not found\n")
        index = int(m.group(1))
        round_ = srv.rounds[index]
        if self.headers.get("Origin") != srv.origin:
            return self._refuse(403, b"wrong origin\n")
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site != "same-origin":
            return self._refuse(403, b"not same-origin\n")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self._refuse(415, b"JSON only\n")
        length = self.headers.get("Content-Length", "")
        if self.headers.get("Transfer-Encoding") or not (length.isascii() and length.isdigit()):
            return self._refuse(411, b"length required\n")
        if len(length) > 9 or int(length) > round_.max_body:  # 9 digits: int() stays cheap and in range
            return self._refuse(413, b"too large\n")
        try:
            body = json.loads(self.rfile.read(int(length)).decode("utf-8"))
            # rev: how many saves of this round the page has seen. The page always sends it; absent, nothing is checked.
            rev = body.pop("rev", None) if isinstance(body, dict) else None
            if rev is not None and (not isinstance(rev, int) or isinstance(rev, bool) or rev < 0):
                raise ValueError("'rev' must be a count of saves")
            state = check_answers(body, round_.decisions)
        except (ValueError, RecursionError) as e:  # bad JSON, bad UTF-8, too deep, bad answers
            return self._send(400, ("bad answers: %s\n" % str(e)[:200]).encode("utf-8", "replace"))
        try:
            result = srv.store(index, state, rev)
        except OSError as e:
            return self._send(500, ("could not write the answers file (%s); is it open in another program?\n"
                                    % (e.strerror or e)).encode("utf-8", "replace"))
        if isinstance(result, tuple):
            return self._send(result[0], (result[1] + "\n").encode("utf-8", "replace"))
        self._send(200, json.dumps({"ok": True, "rev": result}).encode("utf-8"), "application/json")
        if state["done"] and srv.finished.is_set():
            srv.finish()


class Round:
    """One decisions file, its answers file beside it, and whether it is still open."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        try:
            self._load()
        except DecisionsError as e:
            msg = str(e)
            raise DecisionsError(msg if msg.startswith(str(self.path)) else "%s: %s" % (self.path.name, msg))

    def _load(self):
        self.decisions, self.images = load_decisions(self.path)
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.answers_path = answers_path_for(self.path)
        self.markdown_path = self.answers_path.with_name(self.answers_path.stem + ".md")
        self.saved = self.saved_at = None
        self.meta = {}  # accepted, accepted_at, changed_after_accept as last written
        self.rev = 0  # saves stored in this run; a page that saw fewer is out of date
        self.disk = None  # SHA-256 of the answers file as this run last read or wrote it
        if self.answers_path.exists():
            try:
                raw = self.answers_path.read_bytes()
                self.disk = hashlib.sha256(raw).digest()
                stored = json.loads(raw.decode("utf-8"))
                self.saved = check_answers(stored, self.decisions, stored=True)
                self.saved_at = stored["saved"]
                self.meta = {k: stored[k] for k in ("accepted", "accepted_at", "changed_after_accept") if k in stored}
            except (OSError, ValueError, RecursionError) as e:
                raise DecisionsError("%s: cannot resume from this answers file (%s); move it away to start fresh"
                                     % (self.answers_path, e))
            link = stored.get("decisions")
            if self.saved["done"] and link and link != {"file": self.path.name, "sha256": self.sha256}:
                raise DecisionsError("%s changed after it was answered with Done (the answers in %s belong to an "
                                     "earlier version); restore it, or ask again in a new round"
                                     % (self.path.name, self.answers_path.name))
        # Done is final: a round answered with Done is shown read only and never written again.
        self.readonly = self.finished = bool(self.saved and self.saved["done"])
        items = self.decisions["items"]
        texts = 1 + sum(it["comment"] for it in items) + sum(it["other"] for it in items) \
            + MAX_MARKS * sum(it["marks"] for it in items)
        # Room for every text field full: up to 6 bytes a character once JSON-escaped, plus the rest of the body.
        self.max_body = min(MAX_ROUND_BODY, max(MAX_BODY, texts * MAX_TEXT * 6 + 64 * 1024))


def _reread(r):
    """Take in an answers file that another run, or an editor, wrote since this run last read or wrote it, so a
    reloaded page shows it and saving can go on. A file that says done closes the round here too."""
    if r.finished:
        return
    try:
        raw = r.answers_path.read_bytes()
    except OSError:
        return
    digest = hashlib.sha256(raw).digest()
    if digest == r.disk:
        return
    try:
        stored = json.loads(raw.decode("utf-8"))
        saved = check_answers(stored, r.decisions, stored=True)
    except (ValueError, RecursionError):
        return  # unreadable: saves keep being refused rather than writing over it
    r.saved, r.saved_at, r.disk = saved, stored["saved"], digest
    r.meta = {k: stored[k] for k in ("accepted", "accepted_at", "changed_after_accept") if k in stored}
    r.rev += 1  # every page of this run that saw the old state is out of date now
    r.finished = saved["done"]


def round_files(folder):
    found = []
    for p in Path(folder).iterdir():
        m = ROUND_FILE.fullmatch(p.name)
        if m:
            found.append((int(m.group(1)), p.name, p))
    if not found:
        raise DecisionsError("%s: no round files; name them round-01.json, round-02.json and so on" % folder)
    return [p for _, _, p in sorted(found)]


class _Server(http.server.ThreadingHTTPServer):
    """What every Signoff server shares: loopback, the tokens, the start link, the idle clock."""
    daemon_threads = True
    read_only = False

    def _listen(self):
        self.page = (HERE / "page.html").read_text(encoding="utf-8")
        self.token = secrets.token_urlsafe(32)
        self.board_token = getattr(self, "board_token", None) or secrets.token_urlsafe(32)
        self.start_token = secrets.token_urlsafe(32)
        self.start_used = self.start_reused = False
        self.start_used_at = None
        self.lock = threading.Lock()
        self.finished = threading.Event()
        self.last_activity = time.monotonic()
        http.server.ThreadingHTTPServer.__init__(self, ("127.0.0.1", 0), Handler)
        port = self.server_address[1]
        self.host = "127.0.0.1:%d" % port
        self.origin = "http://" + self.host
        self.url = "http://%s/%s/" % (self.host, self.token)
        self.start_url = "http://%s/b/%s/" % (self.host, self.start_token)

    def touch(self):
        self.last_activity = time.monotonic()

    def finish(self, now=False):
        # shutdown() waits for serve_forever(), so it must run on another thread than the request.
        wait = 0.0
        if not now and self.start_used_at is not None:
            wait = max(0.0, self.start_used_at + DONE_GRACE - time.monotonic())
        timer = threading.Timer(wait, self.shutdown)
        timer.daemon = True
        timer.start()


def _media_view(items, media, picture, board):
    """The items as the page gets them: each picture or board path swapped for a route by number, or for a note
    when it is missing. picture(path) and board(path) return the route, or a reason it is not shown."""
    shown_items, k = [], 0
    for item in items:
        shown = dict(item, images=[])
        for img in item.get("images", []):
            path = media[k]
            k += 1
            entry = {key: img[key] for key in ("caption", "alt", "boxes") if key in img}
            route = "not found" if path is None else (board(path) if "html" in img else picture(path))
            if route.startswith(("img/", "/")):
                entry["board" if "html" in img else "src"] = route
            else:
                entry["missing"] = route
            shown["images"].append(entry)
        shown_items.append(shown)
    return shown_items


class SignoffServer(_Server):
    def __init__(self, target):
        target = Path(target)
        paths = round_files(target) if target.is_dir() else [target]
        self.rounds = [Round(p) for p in paths]
        if all(r.readonly for r in self.rounds):
            raise DecisionsError("nothing to answer: %s already done (Done is final). Put new questions in a new "
                                 "round-NN.json or decisions file" % ", ".join(str(r.answers_path) for r in self.rounds))
        self.folder = self.rounds[0].path.parent
        # Pictures and boards are read once, here, and served from memory.
        self.image_data, self.board_data = [], []
        for r in self.rounds:
            boards = []
            for path in r.images:
                if path.suffix.lower() in BOARD_TYPES:
                    boards.append(read_board(path))
                else:
                    self.image_data.append((IMAGE_TYPES[path.suffix.lower()], read_image(path)))
            self.board_data.append(boards)
        self._listen()

    def picture(self, n):
        return self.image_data[n] if n < len(self.image_data) else None

    def board(self, r, k):
        return self.board_data[r][k] if r < len(self.board_data) and k < len(self.board_data[r]) else None

    def editable_answers(self):
        return [r.answers_path for r in self.rounds if not r.readonly]

    def view(self):
        """What the page gets: each round's decisions with picture and board paths swapped for routes by number
        (one picture index across all rounds), its saved answers, and whether it is read only."""
        rounds, count = [], [0]

        def picture(path):
            count[0] += 1
            return "img/%d" % (count[0] - 1)

        with self.lock:
            for r in self.rounds:
                _reread(r)
            if all(r.finished for r in self.rounds) and not self.finished.is_set():
                self.finished.set()  # another run finished the last open round: nothing is left to answer here
                self.finish()
            for ri, r in enumerate(self.rounds):
                boards = iter(range(len(self.board_data[ri])))
                items = _media_view(r.decisions["items"], r.images, picture,
                                    lambda path, ri=ri, boards=boards: "/%s/%d/%d" % (self.board_token, ri,
                                                                                      next(boards)))
                # A round closed by Done in this run is read only too, even when the run goes on.
                closed = r.readonly or r.finished
                rounds.append({"decisions": dict(r.decisions, items=items), "answers": r.saved, "saved": r.saved_at,
                               "readonly": closed, "accepted": bool(r.meta.get("accepted")) or closed,
                               "changed": bool(r.meta.get("changed_after_accept")), "rev": r.rev})
        return {"mode": "rounds", "rounds": rounds}

    def store(self, index, state, rev=None):
        """Write one round's answers. Returns the round's new save count, or (status, message) when the save is
        refused. rev, when given, is the save count the page last saw: an older one means another tab saved since."""
        r = self.rounds[index]
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record = {"version": 1, "done": state["done"], "saved": now,
                  "decisions": {"file": r.path.name, "sha256": r.sha256},
                  "answers": state["answers"], "free_text": state["free_text"]}
        with self.lock:
            if r.finished or self.start_reused:
                return 409, "already done"  # Done is final: a late save from another tab must not undo it
            stale = "changed in another tab or window since this page last saved; reload the page"
            if rev is not None and rev != r.rev:
                return 409, stale
            # Another run on the same folder may have written the file since: never write over what it wrote.
            try:
                on_disk = hashlib.sha256(r.answers_path.read_bytes()).digest()
            except FileNotFoundError:
                on_disk = None  # nothing there to overwrite
            if on_disk is not None and on_disk != r.disk:
                return 409, stale
            if state["done"]:
                # The page gates Done on every round being accepted; the server holds to that too. Done accepts its
                # own round, which keeps a single round to one click.
                missing = [x for x in self.rounds if x is not r and not x.finished and not x.meta.get("accepted")]
                if missing:
                    return 409, "not accepted yet: %s; accept every round before Done" % ", ".join(
                        x.decisions.get("tab") or x.decisions["title"] for x in missing)
            if state["accepted"] or state["done"]:
                # Accepting again without a change keeps the first time; a change in between started over.
                at = (r.meta.get("accepted_at") if r.meta.get("accepted") else None) or now
                meta = {"accepted": True, "accepted_at": at, "changed_after_accept": False}
            else:
                meta = {"accepted": False,
                        "changed_after_accept": bool(r.meta.get("accepted") or r.meta.get("changed_after_accept"))}
            record.update(meta)
            # The JSON file is the record agents trust, so it goes last: if the Markdown copy fails, nothing is final.
            write_atomic(r.markdown_path, answers_markdown(r.decisions, record))
            data = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            write_atomic(r.answers_path, data)
            r.disk = hashlib.sha256(data).digest()  # the bytes written, not a read-back
            r.saved, r.saved_at, r.meta = state, record["saved"], meta
            r.rev += 1
            if state["done"]:
                r.finished = True
                if all(x.finished for x in self.rounds):
                    self.finished.set()
            return r.rev



class HistoryServer(_Server):
    """A read-only page of every round in a project's decisions folder (a topic folder per subfolder). Nothing is
    written. Broken files are listed with their error; pictures and boards are read when the page asks for them."""
    read_only = True

    def __init__(self, root, since=None, budget=HISTORY_BUDGET, timeout=None, folder=DEFAULT_FOLDER):
        root = Path(root).resolve()
        if not root.is_dir():
            raise DecisionsError("%s: not a folder" % root)
        self.root, self.timeout = root, timeout
        self.board_token = secrets.token_urlsafe(32)
        self.rounds = []
        self.pictures, self.boards, used = [], [], [0]
        topics = []
        base = root / folder
        real_base = base.resolve()
        for topic in (base.iterdir() if base.is_dir() else ()):
            try:
                real = topic.resolve()
            except RuntimeError:  # a link loop, on Python before 3.13 (later ones fail is_dir below)
                continue
            if topic.is_dir() and real_base in real.parents:  # a link out of the decisions folder is skipped
                m = DATE_PATTERN.match(topic.name)
                date = m.group(0) if m else datetime.date.fromtimestamp(topic.stat().st_mtime).isoformat()
                if since is None or date >= since:
                    topics.append((date, topic.name, real))
        entries = []
        for date, name, folder in sorted(topics, reverse=True):
            files = []
            for p in folder.iterdir():
                if p.suffix == ".json" and not p.name.endswith((".answers.json", ".outcome.json")) and p.is_file():
                    m = ROUND_FILE.fullmatch(p.name)
                    files.append((int(m.group(1)) if m else 0, p.name, p))
            for _, _, p in sorted(files):
                entries.append(self._entry(name, date, p))

        def admit(path, kind):
            size = path.stat().st_size
            if used[0] + size > budget:
                return "over the image budget; narrow the list with --since"
            used[0] += size
            if kind == "board":
                self.boards[-1].append(path)
                return "/%s/%d/%d" % (self.board_token, len(self.boards) - 1, len(self.boards[-1]) - 1)
            self.pictures.append(path)
            return "img/%d" % (len(self.pictures) - 1)

        text_used, text_budget = 0, HISTORY_TEXT_BUDGET
        for e in entries:
            self.boards.append([])
            media = e.pop("media")
            if e["decisions"] is not None:
                e["decisions"]["items"] = _media_view(e["decisions"]["items"], media,
                                                      lambda path: admit(path, "picture"),
                                                      lambda path: admit(path, "board"))
                # Attachments go inside the page, every copy of them: their own budget, at their size there.
                for item in e["decisions"]["items"]:
                    for att in item.get("attachments", []):
                        if "text" in att:
                            size = _page_size(att["text"])
                            if text_used + size > text_budget:
                                del att["text"]
                                att["missing"] = "over the attachment budget; narrow the list with --since"
                                note = "Attachment %r not shown: %s." % (att["src"], att["missing"])
                                e["warning"] = "%s %s" % (e["warning"], note) if e["warning"] else note
                            else:
                                text_used += size
        self.entries = entries
        self._listen()

    def _entry(self, topic, date, path):
        e = {"topic": topic, "file": path.name, "date": date, "title": None, "tab": None, "status": "new",
             "error": None, "warning": None, "outcome": None, "readonly": True, "decisions": None, "answers": None,
             "saved": None, "media": []}
        if path.resolve().parent != path.parent.resolve():
            e.update(status="error", error="%s: a link to a file outside this folder" % path.name)
            return e
        try:
            decisions, media = load_decisions(path, lenient=True)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
        except (DecisionsError, OSError) as err:
            e.update(status="error", error=str(err).replace(str(path), path.name))  # no local paths on the page
            return e
        e.update(decisions=decisions, media=media, title=decisions["title"], tab=decisions.get("tab"))
        warnings = ["Attachment %r not shown: %s." % (att["src"], att["missing"])
                    for item in decisions["items"] for att in item.get("attachments", []) if "missing" in att]
        folder = path.parent.resolve()

        def local(err):  # error text without local paths
            return str(err).replace(str(folder) + os.sep, "").replace(str(path.parent) + os.sep, "")

        def inside(side):
            if side.resolve().parent != folder:
                warnings.append("%s links outside its folder and was not read." % side.name)
                return False
            return True

        answers = answers_path_for(path)
        if answers.exists() and inside(answers):
            try:
                stored = json.loads(answers.read_text(encoding="utf-8"))
                e["answers"] = check_answers(stored, decisions, stored=True)
                e["saved"] = stored["saved"]
                e["status"] = "done" if e["answers"]["done"] else "open"
                link = stored.get("decisions")
                if link and link != {"file": path.name, "sha256": sha}:
                    warnings.append("The decisions changed after these answers were saved; they may not match.")
            except (OSError, ValueError, RecursionError) as err:
                warnings.append("The answers file cannot be read: %s" % local(err))
        outcome = path.with_name(path.stem + ".outcome.json")
        if outcome.exists() and inside(outcome):
            try:
                e["outcome"] = load_outcome(outcome)
            except (OSError, ValueError, RecursionError) as err:
                warnings.append("The outcome file cannot be read: %s" % local(err))
        e["warning"] = " ".join(warnings) or None
        return e

    def picture(self, n):
        if n >= len(self.pictures):
            return None
        path = self.pictures[n]
        try:
            return IMAGE_TYPES[path.suffix.lower()], read_image(path)
        except (DecisionsError, OSError):
            return None

    def board(self, r, k):
        if r >= len(self.boards) or k >= len(self.boards[r]):
            return None
        try:
            return read_board(self.boards[r][k])
        except (DecisionsError, OSError):
            return None

    def view(self):
        return {"mode": "history", "root": self.root.name, "timeout": self.timeout, "entries": self.entries}


def make_server(target):
    """A server for one decisions file, or for a folder of round-NN.json files."""
    return SignoffServer(target)


def make_history_server(root, since=None, budget=HISTORY_BUDGET, timeout=None, folder=DEFAULT_FOLDER):
    """A read-only server for every round in the decisions folder under a project root."""
    return HistoryServer(root, since, budget, timeout, folder)


def _minutes(value):
    try:
        m = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number of minutes")
    if m < 0 or m != m:
        raise argparse.ArgumentTypeError("must be 0 (no timeout) or more")
    return m


def _date(value):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise argparse.ArgumentTypeError("must be a date like 2026-01-31")
    return value


def _serve(server, args, what):
    """Run until the page is done, the idle timeout passes or Ctrl+C. Returns 'done', 'timeout', 'reused' or
    'stopped'."""
    def watch():
        limit = args.timeout * 60
        while not server.finished.wait(min(1.0, limit / 4 or 1.0)):
            if time.monotonic() - server.last_activity > limit:
                server.shutdown()
                return

    if args.timeout:
        threading.Thread(target=watch, daemon=True).start()
    print("signoff: open %s  (the link works once; Ctrl+C stops it; %s)" % (server.start_url, what),
          file=sys.stderr, flush=True)
    if not args.no_open:
        webbrowser.open(server.start_url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return "stopped"
    finally:
        server.server_close()
    if server.start_reused:
        print("signoff: the start link was used twice, so another program may have opened the page; stopped.",
              file=sys.stderr, flush=True)
        return "reused"
    return "done" if server.finished.is_set() else "timeout"


def _history(args):
    if args.folder and not (Path(args.history) / args.folder).is_dir():
        print("signoff: %s: not a folder" % (Path(args.history) / args.folder), file=sys.stderr)
        return EXIT_ERROR
    try:
        server = make_history_server(args.history, args.since, timeout=args.timeout or None,
                                     folder=args.folder or DEFAULT_FOLDER)
    except DecisionsError as e:
        print("signoff: %s" % e, file=sys.stderr)
        return EXIT_ERROR
    except OSError as e:
        print("signoff: cannot start the server (%s)" % e, file=sys.stderr)
        return EXIT_ERROR
    result = _serve(server, args, "read only, %d rounds" % len(server.entries))
    if result == "stopped":
        print("signoff: stopped", file=sys.stderr)
        return 130
    if result == "timeout":
        print("signoff: stopped after %g idle minutes" % args.timeout, file=sys.stderr)
        return EXIT_TIMEOUT
    return EXIT_ERROR


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse would exit 2, which here means "timed out"; a usage error is invalid input.
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, "signoff: %s\n" % message)


def main(argv=None):
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # a redirected stdout may use a code page that cannot hold the path
    parser = _Parser(prog="signoff", description="Serve a decisions file, or a folder of round-NN.json files, as a "
                                     "local page and save the answers beside them. Prints the answers paths on exit.")
    parser.add_argument("decisions", nargs="?", help="a decisions JSON file, or a folder of round-NN.json files")
    parser.add_argument("--history", metavar="ROOT", help="instead: show every past round under this project root, "
                        "read only")
    parser.add_argument("--folder", metavar="PATH", help="with --history: the decisions folder, relative to ROOT "
                        "or absolute (default %s)" % DEFAULT_FOLDER)
    parser.add_argument("--since", type=_date, metavar="YYYY-MM-DD", help="with --history: only topics from this date")
    parser.add_argument("--no-open", action="store_true", help="print the start link but do not open a browser")
    parser.add_argument("--timeout", type=_minutes, default=120.0, metavar="MINUTES",
                        help="stop after this many idle minutes, exit code 2 (default 120; 0 = never)")
    args = parser.parse_args(argv)
    if (args.decisions is None) == (args.history is None):
        parser.error("give a decisions file or folder, or --history ROOT")
    if args.since and not args.history:
        parser.error("--since goes with --history")
    if args.folder and not args.history:
        parser.error("--folder goes with --history")
    if args.history:
        return _history(args)
    try:
        server = make_server(args.decisions)
    except DecisionsError as e:
        print("signoff: %s" % e, file=sys.stderr)
        return EXIT_ERROR
    except OSError as e:
        print("signoff: cannot start the server (%s)" % e, file=sys.stderr)
        return EXIT_ERROR
    try:
        ensure_gitignore(server.folder)
    except OSError as e:
        print("signoff: could not write .gitignore (%s); keep *.answers.json and *.answers.md in %s out of git"
              % (e, server.folder), file=sys.stderr)
    result = _serve(server, args, "answers go to %s" % server.folder)
    if result == "stopped":
        print("signoff: stopped; the answers so far are kept", file=sys.stderr)
        return 130
    paths = server.editable_answers()
    if result == "reused":
        print("signoff: the answers so far are kept, but read them before you trust them. If an answers file now says "
              "done: true, the first visitor pressed Done: check it, and move it away before running the same command "
              "again for a new link", file=sys.stderr, flush=True)
        return EXIT_ERROR
    if result == "done":
        for path in paths:
            print(path, flush=True)
        return EXIT_DONE
    paths = [p for p in paths if p.exists()]
    print("signoff: timed out after %g idle minutes; %s" % (args.timeout, "the answers so far are kept" if paths
                                                            else "nothing was answered"), file=sys.stderr, flush=True)
    for path in paths:
        print(path, flush=True)
    return EXIT_TIMEOUT


if __name__ == "__main__":
    sys.exit(main())
