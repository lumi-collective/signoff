#!/usr/bin/env python3
"""Signoff: serve a decisions file as a local page, autosave the answers, exit when the user presses Done.

    python3 -I signoff.py <decisions.json | folder> [--no-open] [--timeout <minutes>] [--port <n>]

A folder holds one round-NN.json per round; each round is a tab. The answers go to <name>.answers.json beside
each decisions file. On Done (on the page, or in answers files another program wrote) the absolute path of each
answers file of a round open in this run is printed on stdout, one per line, and the exit code is 0; after the idle
timeout the ones written so far are printed and the exit code is 2.
Invalid input exits 1 with a message on stderr. Python 3.9+, standard library only.
"""
import argparse
import bisect
import datetime
import errno
import hashlib
import http.server
import json
import math
import os
import re
import secrets
import signal
import socket
import socketserver
import stat
import sys
import tempfile
import threading
import time
import unicodedata
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
ATTACHMENT_TYPES = (".md", ".txt", ".diff", ".patch")
MAX_ATTACHMENT = 256 * 1024
# Attachments go inside the page, so these count their size there, escaped (a "<" takes 6 bytes).
MAX_ATTACHMENTS = 2 * 1024 * 1024  # one round's together
# A .md attachment also goes as a tree (_md_trees), built at a round page's first request, on its thread, and at
# each past-decisions build. A build's time follows the trees it makes: a plan-like file's is 6.3 times its size as
# sent, the worst shape's (lists nested MD_DEPTH deep on every line) 148 times. So both budgets are for the whole
# build, kept or not, and a tree stops being made once past what is left. Measured on an M4 Pro, 2026-11-28, with
# 12 shapes in files of 4 to 256 KiB, one a round: the worst build 0.08 s on Python 3.13, 0.12 s on 3.9 (with a
# budget per round instead, 3.0 and 3.8 s); 60 past rounds of hostile files 0.17 to 0.25 s (0.5.0: 0.14 to 0.16 s).
# The lower bound counts no empty table cell and no inline node inside another, so those shapes run to MAX_MD_INPUT:
# 1 MiB of empty table cells took 0.35 s on 3.13 and 0.51 s on 3.9 (the security review's probe).
# One hostile 256 KiB file took 12 to 37 MiB more while parsed (5 shapes; deep lists 12 to 14), was 33 to 97 MiB.
MAX_MD_INPUT = 1024 * 1024  # .md bytes one page build parses, charged before each parse
MAX_MD_TREES = 3 * 1024 * 1024  # bytes of trees one page build makes, as sent: room for 480 KiB of plan-like text
MAX_ITEM_ATTACHMENTS = 20
MAX_ROUND_ATTACHMENTS = 200  # each one is a file read at start
MAX_ITEM_COMMANDS = 10
MAX_COMMAND_TEXT = 4000  # characters; with the lines cap, a block the user can read before running it
MAX_COMMAND_LINES = 60
COMMAND_LANG = re.compile(r"^[A-Za-z0-9+#._-]{1,20}$")
HIDDEN_CATEGORIES = ("Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn")
BLANK_CHARS = (0x2800, 0xFFFC)  # draw nothing, yet are not white space: the Braille blank, the object replacement
# Variation selector 16 and the zero-width joiner build an emoji (the warning sign is U+26A0 U+FE0F). They are
# allowed in a caption only, which is shown and never pasted.
EMOJI_JOINERS = (chr(0xFE0F), chr(0x200D))
# Default_Ignorable_Code_Point from https://www.unicode.org/Public/16.0.0/ucd/DerivedCoreProperties.txt: drawn as
# nothing, yet still in the text (a combining grapheme joiner, variation selectors, Hangul fillers, tags).
# unicodedata has no such property.
DEFAULT_IGNORABLE = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
                     (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
                     (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
                     (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))
MAX_CONTEXT = 120  # characters in a round's context line: room for a session and its task, not for a transcript
MAX_ITEM_LINE = 200  # characters in an item's irreversible and settled lines
MAX_STEPS = 10000  # a number item's values from min to max
MAX_SCALE = 10 ** 12  # a number item's largest value, in steps: a float holds the grid to a 500th of a step
MAX_VALUE = 10 ** 15  # a number item's largest value: a float holds every whole number up to it, and no sum overflows
MAX_UNIT = 16  # characters in a number item's unit
MAX_MARKS = 50
MAX_LINE_NOTES = 50  # an answer's notes on its item's attachment lines
MAX_QUOTE = 300  # code points of the noted line kept with a note, its anchor when the file changes
LINE_NOTE_KEYS = {"att", "line", "quote", "comment"}
HISTORY_BUDGET = 200 * 1024 * 1024
HISTORY_TEXT_BUDGET = 16 * 1024 * 1024  # all attachments on the past-decisions page
HISTORY_DATA_BUDGET = 32 * 1024 * 1024  # every round's own text there (decisions, answers, outcome), as sent
DEFAULT_FOLDER = ".signoff"  # where topic folders live under a project root; the plugin's folder setting can change it
# A board is the agent's own HTML with scripts. Served with this header it runs in a sandbox with an opaque
# origin even when its URL is opened directly: it cannot read the page or send a save, and loads nothing from outside.
# WebRTC is beyond any page policy, so boards must hold nothing secret.
BOARD_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
             "img-src data:; font-src data:; media-src data:; base-uri 'none'; form-action 'none'")
THEME_QUERIES = ("theme=light", "theme=dark")
# The page's look, kept by the inbox in prefs.json. Closed lists: an id may be added, never renamed or removed.
PALETTES = ("catppuccin", "signoff", "rose-pine", "gruvbox", "tokyo-night", "dracula")
THEMES = ("system", "light", "dark")
DENSITIES = ("comfortable", "compact")  # the inbox list's rows
SORTS = ("oldest", "newest", "project")  # the order of the rows in Waiting on you
MAX_PREFS_BODY = 256  # {"theme", "palette", "density", "sort"} as the page sends it
MAX_PREFS_FILE = 4096
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

TOP_KEYS = ("version", "title", "tab", "intro", "notice", "lang", "items", "free_text", "context")
TOP_REQUIRED = ("version", "title", "items")
ITEM_KEYS = ("id", "group", "title", "description", "cost", "images", "select", "options", "recommended", "comment",
             "other", "skippable", "marks", "sensitive", "criteria", "attachments", "commands",
             "blocking", "range", "stakes", "irreversible", "settled", "lines")
ITEM_REQUIRED = ("id", "title")  # and options for one and many, range and recommended for number
RANGE_KEYS = ("min", "max", "step", "unit")
IMAGE_KEYS = ("src", "html", "caption", "alt", "boxes")
ATTACHMENT_KEYS = ("src", "caption", "open")
COMMAND_KEYS = ("text", "lang", "caption")
OUTCOME_KEYS = ("log", "line", "date")
OPTION_KEYS = ("id", "label", "detail", "facts")
CRITERION_KEYS = ("id", "label")
FACT_KEYS = ("text", "tone")
TONE_VALUES = ("good", "mid", "bad")  # the page uses them as class names, so the list stays closed
FREE_TEXT_KEYS = ("title", "placeholder")
SELECT_VALUES = ("one", "many", "number")
STAKES_VALUES = ("low", "medium", "high")
SOURCE_VALUES = ("recommended", "accept_all", "user", "notification", "elsewhere")
ANSWER_KEYS = ("answers", "free_text", "done", "accepted")
STORED_KEYS = ANSWER_KEYS + ("version", "saved", "decisions", "accepted_at", "changed_after_accept")

EXIT_DONE, EXIT_ERROR, EXIT_TIMEOUT = 0, 1, 2

# signoff plan (AD-29): the mod's plan review. Every text of its round is here, in one place; the model gives only
# the plan.
PLAN_TOPIC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-plan-[A-Za-z0-9-]{1,8}")  # fullmatch: the mod's topic name
# Every file of a round's number (answers, Markdown copy, outcome, lock), so a new round never meets one left there.
PLAN_NUMBERED = re.compile(r"(?:round|plan)-([0-9]+)\.")
# The white space JavaScript's trimEnd() cuts (ECMAScript WhiteSpace and LineTerminator), which the mod cuts too, so
# both hold the same bytes. Python's rstrip() would cut U+001C to U+001F and U+0085 as well, and keep U+FEFF.
PLAN_TRIM = "".join(map(chr, (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680) + tuple(range(0x2000, 0x200B))
                         + (0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF)))
PLAN_USAGE = "usage: signoff plan --topic <YYYY-MM-DD-plan-id> --folder <decisions folder> [--resume], plan on stdin"
PLAN_TITLE, PLAN_TAB, PLAN_CONTEXT, PLAN_QUESTION, PLAN_CAPTION = (
    "Plan review", "Plan", "Plan mode", "Approve this plan?", "The plan")
PLAN_DESCRIPTION = ("The agent's plan is attached below. Approve it, or choose \"Ask for changes\" and say what in the "
                    "comment or in notes on the plan's lines: they go back to the agent. After an approval, Claude "
                    "Code leaves plan mode with no dialog and, where tried, goes back to the mode you were in before "
                    "plan mode, auto mode too. On some setups Claude Code may ask once more.")
PLAN_OPTIONS = (("approve", "Approve the plan"), ("revise", "Ask for changes"))
PLAN_LOG = "signoff plan"  # an outcome file's log: the past-decisions page shows "logged in <log>"
PLAN_REPLACED = "replaced by round-%02d"
PLAN_NOT_SHOWN = "not shown: the inbox is older than this plugin; restart it"


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


def _finite(v):
    """A number that is a value: json.loads also reads NaN, Infinity and 1e999 (inf), and an int past a float's
    range makes math.isfinite raise."""
    try:
        return _number(v) and math.isfinite(v)
    except OverflowError:
        return False


def _on_grid(rng, v):
    """v is a finite number in the range and a whole number of steps from min, give or take float rounding: the
    page sends the value its slider holds, as a decimal string turned into a float."""
    if not _finite(v) or abs(v) > MAX_VALUE or not rng["min"] <= v <= rng["max"]:
        return False
    try:  # the loader's bounds keep these finite; a range built some other way must still only say no
        k = round((v - rng["min"]) / rng["step"])
        return abs(v - rng["min"] - k * rng["step"]) <= 1e-9 * rng["step"] + 2e-15 * max(abs(rng["min"]), abs(v))
    except (ArithmeticError, ValueError):
        return False


def _snap(rng, v):
    """The grid point nearest v, as a decimal reader of min and step has it: 0.1 + 2 * 0.1 is 0.30000000000000004
    in floats, so a float is rounded to the decimal places min and step are written with."""
    k = round((v - rng["min"]) / rng["step"])
    if isinstance(rng["min"], int) and isinstance(rng["step"], int):
        return rng["min"] + k * rng["step"]
    from decimal import Decimal  # here, not at the top: the hooks run in every session and never get here

    places = max(-Decimal(repr(float(x))).as_tuple().exponent for x in (rng["min"], rng["step"]))
    return round(rng["min"] + k * rng["step"], max(places, 0))


def _num_text(item, v, unit_text=str):
    """A number item's value as people read it, then the unit: a whole number without a point or an exponent,
    within MAX_VALUE, so at most 16 digits and a sign."""
    text = "%d" % v if float(v).is_integer() else repr(float(v))
    unit = item["range"].get("unit")
    return text + " " + unit_text(unit) if unit else text


def _number_item(item, where):
    """select "number": a range, and a recommendation on it, which the page's slider always holds. No options."""
    for key in ("options", "criteria"):
        if key in item:
            raise DecisionsError("%s: a number item takes no %r" % (where, key))
    for key in ("other", "marks"):
        if item[key]:
            raise DecisionsError("%s: a number item takes no %r" % (where, key))
    if "range" not in item:
        raise DecisionsError("%s: missing 'range'" % where)
    rng, rwhere = item["range"], where + ", 'range'"
    _keys(rng, RANGE_KEYS, ("min", "max"), rwhere)
    rng.setdefault("step", 1)
    for key in ("min", "max", "step"):
        if not _finite(rng[key]):
            raise DecisionsError("%s: %r must be a finite number" % (rwhere, key))
    if not rng["min"] < rng["max"]:
        raise DecisionsError("%s: 'min' must be less than 'max'" % rwhere)
    if not rng["step"] > 0:
        raise DecisionsError("%s: 'step' must be more than 0" % rwhere)
    # _on_grid's float slack grows with the values: past this it is no longer a small part of a step.
    if max(abs(rng["min"]), abs(rng["max"])) > MAX_SCALE * rng["step"]:
        raise DecisionsError("%s: 'step' is too small for values this large: 'min' and 'max' must be within %d "
                             "steps of 0" % (rwhere, MAX_SCALE))
    try:
        steps = (rng["max"] - rng["min"]) / rng["step"]  # floats overflow to inf, ints raise
    except OverflowError:
        steps = math.inf
    if steps > MAX_STEPS:
        raise DecisionsError("%s: more than %d steps from 'min' to 'max'" % (rwhere, MAX_STEPS))
    if max(abs(rng["min"]), abs(rng["max"])) > MAX_VALUE:
        raise DecisionsError("%s: 'min' and 'max' must lie between -1e15 and 1e15" % rwhere)
    _text(rng, "unit", rwhere)
    unit = rng.get("unit", "")
    if len(unit) > MAX_UNIT or any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in unit):
        raise DecisionsError("%s: 'unit' must be one line of up to %d characters" % (rwhere, MAX_UNIT))
    if "recommended" not in item:
        raise DecisionsError("%s: a number item needs 'recommended'" % where)
    if not _on_grid(rng, item["recommended"]):
        raise DecisionsError("%s: 'recommended' must be a number from %s to %s, on a step of %s"
                             % (where, rng["min"], rng["max"], rng["step"]))
    snapped = _snap(rng, item["recommended"])  # what the page's slider saves for it
    if _on_grid(rng, snapped):  # past a max that is off the grid it is not: the value stays as written
        item["recommended"] = snapped


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
    lead inside the folder. The control-character and drive or network messages leave the path out; the resolve and
    folder errors show it as written."""
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
    for key in ("src", "caption"):
        _text(att, key, where)
    if not isinstance(att.get("open", False), bool):
        raise DecisionsError("%s: 'open' must be true or false" % where)
    src = att["src"]
    if PureWindowsPath(src).anchor:  # a new key, and the page shows src: no local path, not even a rooted one
        raise DecisionsError("%s: the path must be relative to the decisions file's folder" % where)
    path = _inside(folder, src, where)
    if path.suffix.lower() not in ATTACHMENT_TYPES:
        raise DecisionsError("%s: %r must be a text file, .md, .txt, .diff or .patch" % (where, src))
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
    item.setdefault("blocking", False)
    if item["select"] not in SELECT_VALUES:
        raise DecisionsError("%s: 'select' must be \"one\", \"many\" or \"number\"" % where)
    for key in ("comment", "other", "skippable", "marks", "sensitive", "blocking"):
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
    if item["select"] == "number":
        _number_item(item, where)
    elif "range" in item:
        raise DecisionsError("%s: 'range' is only for select \"number\"" % where)
    elif "options" not in item:
        raise DecisionsError("%s: missing 'options'" % where)
    else:
        options = item["options"]
        if not isinstance(options, list) or len(options) < (2 if item["select"] == "one" else 1):
            raise DecisionsError("%s: 'options' needs at least %s"
                                 % (where, "two" if item["select"] == "one" else "one"))
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
                    raise DecisionsError("%s: 'recommended' must be one option id (%s)"
                                         % (where, ", ".join(sorted(seen))))
            elif not (isinstance(rec, list) and all(isinstance(r, str) and r in seen for r in rec)
                      and len(set(rec)) == len(rec)):
                raise DecisionsError("%s: 'recommended' must be a list of option ids (%s)"
                                     % (where, ", ".join(sorted(seen))))
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
    if not isinstance(item.get("lines", False), bool):
        raise DecisionsError("%s: 'lines' must be true or false" % where)
    if item.get("lines") and not attachments:
        raise DecisionsError("%s: 'lines' needs at least one attachment, whose lines the user notes" % where)
    if "commands" in item:
        commands = item["commands"]
        if not isinstance(commands, list) or not 1 <= len(commands) <= MAX_ITEM_COMMANDS:
            raise DecisionsError("%s: 'commands' must be a list of 1 to %d" % (where, MAX_ITEM_COMMANDS))
        for k, cmd in enumerate(commands, 1):
            _command(cmd, "%s, command %d" % (where, k))
    if "stakes" in item and item["stakes"] not in STAKES_VALUES:
        raise DecisionsError("%s: 'stakes' must be \"low\", \"medium\" or \"high\"" % where)
    for key in ("irreversible", "settled"):
        if key in item and not _line_ok(item[key], MAX_ITEM_LINE):
            raise DecisionsError("%s: %r must be one line of 1 to %d characters, with no control or invisible "
                                 "character" % (where, key, MAX_ITEM_LINE))
    if "settled" in item and "recommended" not in item:
        raise DecisionsError("%s: 'settled' needs 'recommended', the choice settled before" % where)


def _line_ok(line, limit):
    """One line the page shows whole: not blank, at most limit characters, no line break or tab, and none of the
    characters a command refuses as invisible. Never cut, so the agent learns at once that it is too long."""
    return isinstance(line, str) and bool(line.strip()) and len(line) <= limit and "\t" not in line \
        and "\n" not in line and _hidden(line) is None


def _hidden(text, allowed=()):
    """The first character of text that may show as nothing, or not as it runs: a control character but tab and
    newline, a format, separator, surrogate, private-use or unassigned one, a default-ignorable one, or a blank
    one. Unassigned is as this Python's Unicode knows it, so an older Python refuses a character a newer Unicode
    assigned. allowed: characters let through all the same."""
    for c in text:
        if c in "\t\n" or c in allowed:
            continue
        n = ord(c)
        if (unicodedata.category(c) in HIDDEN_CATEGORIES or n in BLANK_CHARS
                or any(lo <= n <= hi for lo, hi in DEFAULT_IGNORABLE)):
            return c
    return None


def _command(cmd, where):
    """A command the user pastes into a terminal. The page wraps it; the known invisible and blank characters are
    refused here, and its size is capped, so the user can read it before running it."""
    _keys(cmd, COMMAND_KEYS, ("text",), where)
    for key in COMMAND_KEYS:
        _text(cmd, key, where)
        hidden = _hidden(cmd.get(key, ""), EMOJI_JOINERS if key == "caption" else ())
        if hidden is not None:
            raise DecisionsError("%s: %r holds an invisible character, U+%04X" % (where, key, ord(hidden)))
    text = cmd["text"]
    if not text.strip():
        raise DecisionsError("%s: 'text' is empty" % where)
    if text != text.strip():  # a trailing line break would run it on paste
        raise DecisionsError("%s: 'text' must not start or end with a space or line break" % where)
    if len(text) > MAX_COMMAND_TEXT:
        raise DecisionsError("%s: 'text' has more than %d characters" % (where, MAX_COMMAND_TEXT))
    if text.count("\n") >= MAX_COMMAND_LINES:
        raise DecisionsError("%s: 'text' has more than %d lines" % (where, MAX_COMMAND_LINES))
    if "lang" in cmd and not COMMAND_LANG.fullmatch(cmd["lang"]):
        raise DecisionsError("%s: 'lang' must match %s" % (where, COMMAND_LANG.pattern))


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
        data = json.loads(read_regular(path, MAX_DECISIONS).decode("utf-8"))
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
    if "context" in data:
        if not _line_ok(data["context"], MAX_CONTEXT):  # the inbox row shows it whole
            raise DecisionsError("decisions file: 'context' must be one line of 1 to %d characters, with no control "
                                 "or invisible character" % MAX_CONTEXT)
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


def check_answers(state, decisions, stored=False, previous=None):
    """Check a save body (or, with stored=True, the answers file) against the decisions. Raises ValueError.
    previous: the round's stored answers, for a save: a line note found there unchanged is not matched against its
    attachment's text again, so a file changed since never blocks a save. A stored read matches no note."""
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
        if not isinstance(ans, dict) or set(ans) - {"choice", "comment", "source", "other", "marks", "private",
                                                     "lines"}:
            raise ValueError("answer %r must be {choice, comment, source, other, marks, private, lines}" % key)
        if not isinstance(ans.get("private", False), bool):
            raise ValueError("answer %r: private must be true or false" % key)
        choice, comment = ans.get("choice"), ans.get("comment", "")
        if "source" in ans and ans["source"] not in SOURCE_VALUES:
            raise ValueError("answer %r: source must be one of %s" % (key, ", ".join(SOURCE_VALUES)))
        valid = {o["id"] for o in item.get("options", [])}
        if item["other"]:
            valid.add(OTHER)
        if item["skippable"]:
            valid.add(SKIP)
        if item["select"] == "number":
            ok = choice is None or (isinstance(choice, str) and choice in valid) or _on_grid(item["range"], choice)
        elif item["select"] == "one":
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
        if "lines" in ans:
            old = None if stored else ((previous or {}).get("answers") or {}).get(key, {}).get("lines", [])
            clean[key]["lines"] = _check_lines(ans["lines"], item, key, old)
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


def attachment_lines(text):
    """An attachment's lines as a line note counts them (the page counts alike): split on LF only, one CR cut off
    each line's end, no line after a final LF. A lone CR or U+2028 stays inside its line, so not splitlines()."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _check_lines(notes, item, key, old):
    """An answer's line notes. Each names an attachment of the item by index, a line from 1 and that line's first
    MAX_QUOTE code points. old: the notes stored for this item, whose unchanged ones are not matched again; None
    (a stored read) matches none. Errors name a note's position only, never its text."""
    if not item.get("lines"):
        raise ValueError("answer %r: this item takes no line notes" % key)
    if not isinstance(notes, list) or len(notes) > MAX_LINE_NOTES:
        raise ValueError("answer %r: lines must be a list of up to %d" % (key, MAX_LINE_NOTES))
    atts = item.get("attachments", [])
    for n, note in enumerate(notes, 1):
        ok = isinstance(note, dict) and set(note) == LINE_NOTE_KEYS
        ok = ok and type(note["att"]) is int and 0 <= note["att"] < len(atts)
        # No file within MAX_ATTACHMENT bytes has more lines; a larger line a page would round past 2^53.
        ok = ok and type(note["line"]) is int and 1 <= note["line"] <= MAX_ATTACHMENT
        ok = ok and isinstance(note["quote"], str) and len(note["quote"]) <= MAX_QUOTE
        ok = ok and isinstance(note["comment"], str) and 1 <= len(note["comment"]) <= MAX_TEXT
        if not ok:
            raise ValueError("answer %r: line note %d must be {att: <attachment index>, line: <line from 1>, quote: "
                             "<the line, up to %d characters>, comment: <text>}" % (key, n, MAX_QUOTE))
    if old is None:
        return notes
    found = {}
    for n, note in enumerate(notes, 1):
        if note in old:  # the types are checked above, so True never equals 1 here
            continue
        if note["att"] not in found:
            found[note["att"]] = attachment_lines(atts[note["att"]].get("text", ""))  # no text: a file not read
        lines = found[note["att"]]
        if note["line"] > len(lines) or lines[note["line"] - 1][:MAX_QUOTE] != note["quote"]:
            raise ValueError("answer %r: line note %d does not match its line in the attachment" % (key, n))
    return notes


def load_outcome(path):
    """The agent's note of what a round led to: {log, line, date}, all text."""
    data = json.loads(read_regular(path, MAX_DECISIONS).decode("utf-8"))
    if not (isinstance(data, dict) and set(data) == set(OUTCOME_KEYS)
            and all(isinstance(data[k], str) and len(data[k]) <= MAX_TEXT for k in OUTCOME_KEYS)):
        raise ValueError("must be {log, line, date}, all text")
    try:
        json.dumps(data, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("text contains an invalid character (a lone UTF-16 surrogate)")
    return data


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def answers_path_for(decisions_path):
    p = Path(decisions_path).resolve()
    return p.with_name(p.stem + ".answers.json")


def ensure_gitignore(folder):
    """Keep raw answers and round locks out of git: write a .gitignore only when the folder has none. Never
    overwrites."""
    try:
        with open(Path(folder) / ".gitignore", "x", encoding="utf-8") as f:
            f.write("*.answers.json\n*.answers.md\n*.json.lock\n")
        return True
    except FileExistsError:
        return False


def write_atomic(path, data):
    """Write text or bytes to path through a temporary file and a rename. Bytes go out as given: no line-ending
    translation, so a hash of them is the hash of the file."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    # The temp name ends like the target (.answers.json or .answers.md), so the .gitignore covers a leftover one.
    suffix = "".join(path.suffixes[-2:])
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for wait in (0.05, 0.1, 0.2, 0.4, None):
            try:
                os.replace(tmp, str(path))
                break
            except PermissionError:  # Windows: a reader (a run, the inbox) has the file open for a moment
                if wait is None or sys.platform != "win32":
                    raise
                time.sleep(wait)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def inbox_home():
    """The Signoff home, where the inbox keeps its lock and runs register (SIGNOFF_INBOX_HOME is for tests). A run
    never creates it, nor the inbox.lock in it: without that file no inbox has run, and a run is the plain run.
    The hooks and `signoff inbox home` use this too: one resolver."""
    if os.environ.get("SIGNOFF_INBOX_HOME"):
        return Path(os.environ["SIGNOFF_INBOX_HOME"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "signoff"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "signoff"
    xdg = os.environ.get("XDG_STATE_HOME", "")
    # The XDG spec ignores a relative path; taken as is, it would put the home inside whatever folder a hook runs in.
    return (Path(xdg) if os.path.isabs(xdg) else Path.home() / ".local" / "state") / "signoff"


def inbox_on(home):
    """Whether the inbox is on. shortcut: an inbox.lock (the inbox has run here) stands in for the inbox's on/off
    setting until it has one (config.json); without it a run is the plain run and the hooks write nothing (but the
    macOS app's launcher link, in hook.sh). A relative
    home is off: it would be read against whatever folder a run or a hook starts in."""
    return home.is_absolute() and (home / "inbox.lock").exists()


def try_lock(path, create=True):
    """Take an exclusive advisory lock on path without waiting. Returns the descriptor that holds it (unlock() lets
    go, as does the process ending; it is not inherited by child processes), or None when another one holds it.
    Lock files are empty files of their own: on Windows a lock also blocks reading the bytes it covers."""
    fd = os.open(str(path), os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | (os.O_CREAT if create else 0), 0o600)
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError as e:
        os.close(fd)
        if isinstance(e, BlockingIOError) or e.errno == errno.EACCES:
            return None
        raise


def unlock(fd):
    """Let go of a lock from try_lock. Unlocked first: on Windows a closed handle's lock may linger for a while."""
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass  # closing lets go too
    os.close(fd)


def inbox_alive(home):
    """Whether the inbox runs: it holds inbox.lock for as long as it lives. Each look takes the lock for a moment,
    so another run looking at the same time can seem to hold it: only a lock held at three looks in a row counts.
    A lock file that cannot be locked at all is held by no inbox either."""
    for wait in (0.05, 0.05, None):
        try:
            fd = try_lock(home / "inbox.lock", create=False)
        except FileNotFoundError:
            return False
        except OSError as e:
            print("signoff: cannot check %s (%s); going on without the inbox" % (home / "inbox.lock", e),
                  file=sys.stderr, flush=True)
            return False
        if fd is not None:
            unlock(fd)
            return False
        if wait:
            time.sleep(wait)
    return True


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


def read_regular(path, limit, nofollow=False):
    """The bytes of a regular file of at most limit bytes, else OSError: never blocking, so a FIFO where a round or
    its answers should be cannot stall a run, a save or the inbox's look at every project. nofollow: not through a
    link at its name either (POSIX)."""
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
                 | (getattr(os, "O_NOFOLLOW", 0) if nofollow else 0))
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        raw = f.read(limit + 1)
    if len(raw) > limit:
        raise OSError(errno.EFBIG, "larger than %d bytes" % limit)
    return raw


def read_board(path):
    data = read_image(path, MAX_BOARD)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise DecisionsError("%s: a board must be UTF-8 text" % path)
    return data


# Inline markup, HTML, a quote, a code fence, strikethrough, an & that could start a character reference, a #
# run that would close a heading, what makes a bare URL a link in GFM: the colon of "<scheme>://" and the dot of
# "www.", and a % after a %: Obsidian (a notes vault) hides the text between two %% as a comment.
_MD_INLINE = re.compile(
    r"([\\`*_\[\]<>|~]|&(?=[#A-Za-z0-9])|(?<=\s)#(?=#*$)|(?<=[A-Za-z0-9]):(?=//)|(?<=www)\.|(?<=%)%)")
# At the start of a line: a heading, a list item (an empty one too), a thematic break, setext underline or a
# table's delimiter row (only -, : and spaces, with a -), an ordered list item.
_MD_LINE_START = re.compile(r"^(#|[-+](?=\s|$)|(?=[-:\s]*-)[-:](?=[-:\s]*$)|=(?==*$)|\d+(?=[.)]))")


MD_LABELS = {
    "en": {"choice": "Choice", "source": "Source", "comment": "Comment", "mark": "Mark %d, picture %d at %s "
           "(x, y, w, h in %%): %s", "attachment": "Attachment", "saved": "Saved", "accepted": "Accepted",
           "done": "Done", "yes": "yes", "no": "no", "skipped": "Skipped", "other": "Other", "rec": "recommended",
           "not_rec": "not recommended", "nothing": "nothing chosen", "none": "(none)", "private": "(private)",
           "anything": "Anything else", "recommended": "recommendation, not changed", "accept_all": "Accept all",
           "user": "the user", "notification": "recommendation, accepted from a notification", "unknown": "unknown", "yes_at": "yes, %s", "changed": "no, changed after it was accepted",
           "command": "Command", "stakes": "Stakes",
           "low": "low", "medium": "medium", "high": "high", "irreversible": "Cannot be undone",
           "settled": "Settled earlier, says the agent", "line": "Line note, %s line %d, “%s”: %s"},
    "hu": {"choice": "Választás", "source": "Forrás", "comment": "Megjegyzés", "mark": "Jelölés %d, %d. kép, %s "
           "(x, y, sz, m, %%-ban): %s", "attachment": "Melléklet", "saved": "Mentve", "accepted": "Elfogadva",
           "done": "Kész", "yes": "igen", "no": "nem", "skipped": "Kihagyva", "other": "Egyéb", "rec": "javasolt",
           "not_rec": "nem javasolt", "nothing": "nincs választás", "none": "(nincs)", "private": "(privát)",
           "anything": "Bármi más", "recommended": "a javaslat, változatlanul", "accept_all": "Javaslatok elfogadása",
           "user": "a felhasználó", "notification": "a javaslat, értesítésből elfogadva", "unknown": "ismeretlen", "yes_at": "igen, %s",
           "changed": "nem, elfogadás után módosítva", "command": "Parancs", "stakes": "Tét", "low": "alacsony",
           "medium": "közepes", "high": "magas", "irreversible": "Nem vonható vissza",
           "settled": "Korábban eldőlt, az ágens szerint", "line": "Sor-megjegyzés, %s %d. sora, „%s”: %s"},
}


def md_labels(decisions):
    """The Markdown copy's labels in the round's language (the page's rule: the part before "-" or "_"; a language
    without labels gets English)."""
    code = re.split(r"[-_]", decisions.get("lang", "en").strip().lower())[0]
    return MD_LABELS.get(code, MD_LABELS["en"])


def md_text(text, none="(none)"):
    """Plain text for Markdown. Inline markup characters and HTML brackets are backslash-escaped everywhere;
    block markers only at the start of a line. Leading spaces are dropped (no code blocks), and line breaks
    become hard breaks inside the same block. A control (but tab), format or lone surrogate character shows as
    U+XXXX: a bidi override would reverse the rest of the line, and a zero-width one would hide."""
    out = []
    for line in text.splitlines():
        line = "".join("U+%04X" % ord(c) if c != "\t" and unicodedata.category(c) in ("Cc", "Cf", "Cs") else c
                       for c in line.strip())
        line = _MD_INLINE.sub(r"\\\1", line)
        m = _MD_LINE_START.match(line)
        if m:
            # Escape the marker itself; for "1." or "1)" the escape goes before the dot or bracket.
            cut = m.end() if m.group(1)[0].isdigit() else 0
            line = line[:cut] + "\\" + line[cut:]
        if line:
            out.append(line)
    return "  \n  ".join(out) or none


def md_line(text, none="(none)"):
    """md_text for a heading or list line: every line break that splitlines() honours (CR, U+2028, NEL, ...) is a
    space, so the field cannot split its line and leave a second part as body text."""
    return md_text(" ".join(text.splitlines()), none)


def answers_markdown(decisions, record):
    """The answers as a Markdown page for people, its labels in the round's language; the JSON stays the source of
    truth. Agent text is never translated."""
    lb = md_labels(decisions)
    out = ["# " + md_line(decisions["title"]), ""]
    group = None
    for n, item in enumerate(decisions["items"], 1):
        if item.get("group") and item["group"] != group:
            group = item["group"]
            out += ["## " + md_line(group), ""]
        ans = record["answers"].get(item["id"], {})
        choice = ans.get("choice")
        picked = choice if isinstance(choice, list) else ([] if choice is None else [choice])
        rec = item.get("recommended", [])
        rec = rec if isinstance(rec, list) else [rec]
        # A private answer keeps its text in the JSON for the agent; this copy for people only says it is there.
        private = ans.get("private") is True

        def text(value):
            return lb["private"] if private and value.strip() else md_text(value, lb["none"])
        labels = {o["id"]: md_line(o["label"]) for o in item.get("options", [])}
        if item["select"] == "number" and _number(choice):  # the value is ours to write; the unit is the agent's
            labels[choice] = _num_text(item, choice, lambda unit: md_line(unit, ""))
        labels[SKIP] = lb["skipped"]
        labels[OTHER] = lb["other"] + ": " + text(ans.get("other", ""))
        # Whether a choice was the recommendation only means something when there was one.
        shown = ", ".join(labels[c] + ("" if not rec else " (%s)" % (lb["rec"] if c in rec else lb["not_rec"]))
                          for c in picked) or lb["nothing"]
        out += ["### %d. %s" % (n, md_line(item["title"])), "",
                "- %s: %s" % (lb["choice"], shown),
                "- %s: %s" % (lb["source"], lb.get(ans["source"], lb["unknown"]) if ans.get("source") in SOURCE_VALUES
                              else lb["unknown"])]  # elsewhere: a source kept only for an old file, with no label
        if "stakes" in item:
            out.append("- %s: %s" % (lb["stakes"], lb[item["stakes"]]))
        out += ["- %s: %s" % (lb[k], md_line(item[k])) for k in ("irreversible", "settled") if k in item]
        if ans.get("comment", "").strip():  # no empty line for a comment nobody wrote
            out.append("- %s: %s" % (lb["comment"], text(ans["comment"])))
        for k, mark in enumerate(ans.get("marks", []), 1):
            out.append("- " + lb["mark"] % (k, mark["image"] + 1, ", ".join("%g" % v for v in mark["box"]),
                                            text(mark["comment"])))
        for note in ans.get("lines", []):
            # The quote is the agent's file text (a stored one unchecked), so md_line.
            out.append("- " + lb["line"] % (md_line(item["attachments"][note["att"]]["src"]), note["line"],
                                            md_line(note["quote"], ""), text(note["comment"])))
        # Each attachment by its name only: its text stays in its file, but for a noted line's quote above.
        for att in item.get("attachments", []):
            name = md_line(att["src"])
            caption = att.get("caption", "")
            out.append("- %s: %s" % (lb["attachment"], "%s (%s)" % (md_line(caption), name) if caption.strip() else name))
        for cmd in item.get("commands", []):  # for reading, not pasting: md_text drops indentation and blank lines
            caption = cmd.get("caption", "")
            out.append("- %s%s: %s" % (lb["command"], " (%s)" % md_line(caption) if caption.strip() else "",
                                       md_text(cmd["text"])))
        out.append("")
    title = decisions["free_text"].get("title") or lb["anything"]
    out += ["## " + md_line(title), "", md_text(record["free_text"], lb["none"]), "",
            "%s: %s  " % (lb["saved"], record["saved"]), "%s: %s  " % (lb["accepted"], _accepted_text(record, lb)),
            "%s: %s" % (lb["done"], lb["yes"] if record["done"] else lb["no"]), ""]
    return "\n".join(out)


def _accepted_text(record, lb):
    if record.get("accepted"):
        return lb["yes_at"] % record["accepted_at"]
    if record.get("changed_after_accept"):
        return lb["changed"]
    return lb["no"]


def _script_json(obj, compact=False):
    # Inside <script type="application/json"> only "</" could end the block; escaping <, > and & rules it out.
    # compact: the inbox list, whose todo trees are many small nodes, which indentation would make 4 times larger.
    text = json.dumps(obj, ensure_ascii=False, **({"separators": (",", ":")} if compact else {"indent": 1}))
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e") \
        .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _page_size(text):
    """The bytes a text takes in the page's data, escaped."""
    return len(_script_json(text).encode("utf-8"))


# ---- todo.md: the agent's file, read only (AD-5), shown in the inbox as a tree the page builds with DOM methods ----
# The file is the agent's: every pattern here must stay linear on a long hostile line (no nested or adjacent
# quantifiers over the same characters), since the inbox reads it while holding its lock.
_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")  # .* never fails, so nothing backtracks; _fence checks the rest
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*))?$")
_HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
_BULLET = re.compile(r"^( {0,3})([-*+]|[0-9]{1,9}[.)])(?:( +)(.*))?$")
_TASK = re.compile(r"^\[([ xX])\](?: |$)")
_DELIM_CELL = re.compile(r":?-+:?")
_ALERT = re.compile(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$")
_SPECIAL = re.compile(r"[\\`*\[<]")
_LINK = re.compile(r"\[([^\[\]\n]*)\]\(<?([^\s()<>]+)>?\)")
_AUTOLINK = re.compile(r"<([^\s<>]+)>")
# Only http(s) becomes a link: javascript:, data:, file: and relative paths stay text.
_SAFE_HREF = re.compile(r"^https?://[^\s\x00-\x1f\x7f]+$", re.I)
_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
# The agent's open item: "- [ ] text"; an item waiting on the user starts "- [ ] (waiting on you: <what>) text".
_TODO_ITEM = re.compile(r"^( *)[-*+] \[([ xX])\](?: (.*))?$")
_WAITING = re.compile(r"^\(waiting on you: ([^)\n]+)\)(?= |$)")
MD_DEPTH = 6  # nested quotes and lists; past it the rest is text (each level adds nodes for the same few bytes)
MAX_COLUMNS = 64  # a table's columns
TODO_FILES = ("_bmad-output/todo.md", "TODO.md", "todo.md")  # where a project's todo.md is looked for, in order
# A bad `todo` key on the page ("<file> not read: <error>"): fixed text, as the key's value may be an agent's.
TODO_KEY_FILE, TODO_KEY_ERROR = "the todo path in its project file", "it names no file inside the project"
MAX_TODO = 1024 * 1024
MAX_TODOS = 300  # per project
MAX_ITEM = 64 * 1024  # an item's text turned into a tree; past it only the start of its first line
TODO_DEPTH = 12  # how deep todo items are looked for under other items (MD_DEPTH is how deep they render)
MAX_REF = 300  # characters of a todo item's first line that name it in a request
MAX_ROUND_LINKS = 5  # decisions rounds one todo item's text may name for its Open round link
# A round named in an item: "<topic folder> / round-NN", or a path ending "<topic folder>/round-NN.json". The
# lookbehind starts a name only where a run of name characters starts, so a long run is tried once, not per offset.
_ROUND_NAME = re.compile(r"(?<![\w.-])([\w.-]{1,200})(?: / |/)(round-\d{2,4})(?:\.json)?(?![\w.-])")


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _leading_tabs(line):
    """Tabs in the indentation count as 4 spaces; the rest of the line is kept as it is."""
    body = line.lstrip(" \t")
    return line[:len(line) - len(body)].expandtabs(4) + body


def _fence(line):
    """(indent, fence, language) for a line that opens a code block, or None. A backtick fence's info may hold no
    backtick."""
    m = _FENCE.match(line)
    if not m or (m.group(2)[0] == "`" and "`" in m.group(3)):
        return None
    words = m.group(3).split()
    return len(m.group(1)), m.group(2), words[0] if words else ""


def _closes(fence):
    return re.compile(r"^ {0,3}%s{%d,}[ \t]*$" % (re.escape(fence[0]), len(fence)))


def _run_of(text, i):
    """How many backticks start at i."""
    j = i
    while j < len(text) and text[j] == "`":
        j += 1
    return j - i


def _inline(text, depth=0):
    """Inline Markdown as nodes: text, code, b (bold) and link (http and https only). Anything else, HTML
    included, stays text."""
    out, buf, i, n, runs = [], [], 0, len(text), None

    def flush():
        if "".join(buf):
            out.append({"t": "text", "v": "".join(buf)})
        del buf[:]
    while i < n:
        m = _SPECIAL.search(text, i)
        if not m:
            buf.append(text[i:])
            break
        buf.append(text[i:m.start()])
        i = m.start()
        c = text[i]
        if c == "\\" and i + 1 < n and text[i + 1] in _PUNCT:
            buf.append(text[i + 1])
            i += 2
            continue
        if c == "`":
            if runs is None:  # every backtick run, by length, found once: a scan per run would be quadratic
                runs = {}
                for r in re.finditer(r"`+", text):
                    runs.setdefault(r.end() - r.start(), []).append(r.start())
            run = _run_of(text, i)
            later = runs.get(run, [])
            k = bisect.bisect_right(later, i)
            end = later[k] if k < len(later) else -1  # the closing run is exactly as long as the opening one
            if end >= 0:
                code = text[i + run:end].replace("\n", " ")
                if len(code) > 1 and code[0] == code[-1] == " " and code.strip():
                    code = code[1:-1]
                flush()
                out.append({"t": "code", "v": code})
                i = end + run
                continue
            buf.append("`" * run)
            i += run
            continue
        if c == "*" and text.startswith("**", i) and depth < 4:
            end = text.find("**", i + 2)
            if end > i + 2:
                flush()
                out.append({"t": "b", "c": _inline(text[i + 2:end], depth + 1)})
                i = end + 2
                continue
        if c == "[" and depth < 4:
            m = _LINK.match(text, i)
            if m and _SAFE_HREF.match(m.group(2)):
                flush()
                out.append({"t": "link", "href": m.group(2), "c": _inline(m.group(1), depth + 1)})
                i = m.end()
                continue
        if c == "<":
            m = _AUTOLINK.match(text, i)
            if m and _SAFE_HREF.match(m.group(1)):
                flush()
                out.append({"t": "link", "href": m.group(1), "c": [{"t": "text", "v": m.group(1)}]})
                i = m.end()
                continue
        buf.append(c)
        i += 1
    flush()
    return out


def _cells(line):
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


class _TooLarge(Exception):
    """A tree past the room it was given, given up while it was built."""


def _blocks(lines, depth, marks, room=None):
    out, para, i, n = [], [], 0, len(lines)

    def spend(nodes, text=""):
        # room[0]: bytes left in the page's indented data, at 8 deeper. A node there takes 3 lines at least, each
        # indented 9 and more, 4 more each level down; a text half its length at least (an escape's \ goes).
        if room is not None:
            room[0] -= nodes * (40 + 12 * depth) + len(text) // 2
            if room[0] < 0:
                raise _TooLarge

    def flush():
        if para:
            text = "\n".join(para)
            spend(1, text)  # before its inline nodes are made: a paragraph can be the whole file
            out.append({"t": "p", "c": _inline(text)})
            spend(len(out[-1]["c"]))
            del para[:]
    while i < n:
        line = lines[i]
        if not line.strip():
            flush()
            i += 1
            continue
        if depth >= MD_DEPTH:
            para.append(line.strip())
            i += 1
            continue
        f = _fence(line)
        if f:
            flush()
            ind, close, lang, code = f[0], _closes(f[1]), f[2], []
            i += 1
            while i < n and not close.match(lines[i]):
                code.append(lines[i][min(ind, _indent(lines[i])):])
                i += 1
            if i == n:  # no closing fence: the block runs to the end, without the blank lines it ends with
                while code and not code[-1].strip():
                    code.pop()
            out.append({"t": "code", "lang": lang, "text": "\n".join(code)})
            spend(1, out[-1]["text"])
            i += 1
            continue
        m = _HEADING.match(line)
        if m:
            flush()
            text = (m.group(2) or "").rstrip()
            bare = text.rstrip("#")
            if bare != text and (not bare or bare[-1] in " \t"):  # a closing run of #s is not part of the heading
                text = bare.rstrip()
            spend(1, text)
            out.append({"t": "h", "level": len(m.group(1)), "c": _inline(text)})
            spend(len(out[-1]["c"]))
            i += 1
            continue
        if _HR.match(line):
            flush()
            out.append({"t": "hr"})
            spend(1)
            i += 1
            continue
        if _QUOTE.match(line):
            flush()
            inner = []
            while i < n and _QUOTE.match(lines[i]):
                inner.append(_QUOTE.match(lines[i]).group(1))
                i += 1
            alert = _ALERT.match(inner[0].strip())
            spend(1)
            if alert:
                out.append({"t": "alert", "kind": alert.group(1).lower(),
                            "c": _blocks(inner[1:], depth + 1, marks, room)})
            else:
                out.append({"t": "quote", "c": _blocks(inner, depth + 1, marks, room)})
            continue
        m = _BULLET.match(line)
        if m:
            flush()
            ordered = m.group(2)[-1] in ".)"
            items = []
            while i < n:
                m = _BULLET.match(lines[i])
                if not m or (m.group(2)[-1] in ".)") != ordered or _HR.match(lines[i]):
                    break
                spaces, first = m.group(3) or "", m.group(4) or ""
                if len(spaces) > 4:  # a code-like indent after the marker: the content starts after one space
                    first, spaces = spaces[1:] + first, " "
                col = len(m.group(1)) + len(m.group(2)) + max(1, len(spaces))
                j = i + 1
                while j < n and (not lines[j].strip() or _indent(lines[j]) >= col):
                    j += 1
                end = j
                while end > i + 1 and not lines[end - 1].strip():
                    end -= 1
                check, task = None, _TASK.match(first)
                if task:
                    check, first = task.group(1) != " ", first[task.end():]
                spend(1 + (not items))  # the item, and the list with its first
                item ={"check": check, "c": _blocks([first] + [x[col:] for x in lines[i + 1:end]], depth + 1, marks,
                                                     room)}
                if marks and check is False and _marker(first):
                    item["wait"] = True  # an open todo step marked waiting on the user: the page marks it
                items.append(item)
                i = j
            out.append({"t": "list", "ordered": ordered, "items": items})
            continue
        if ("|" in line and i + 1 < n and "|" in lines[i + 1] and _indent(lines[i + 1]) < 4
                and all(_DELIM_CELL.fullmatch(c) for c in _cells(lines[i + 1]))):
            flush()
            head = _cells(line)[:MAX_COLUMNS]
            i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                cells = _cells(lines[i])[:len(head)]  # not padded: a short row just ends early on the page
                spend(1, lines[i])
                rows.append([_inline(c) for c in cells])
                spend(sum(len(c) for c in rows[-1]))
                i += 1
            out.append({"t": "table", "head": [_inline(c) for c in head], "rows": rows})
            spend(1 + len(head), line)
            continue
        para.append(line.strip())
        i += 1
    flush()
    return out


def markdown_blocks(text, marks=True, room=None):
    """Markdown as a tree of plain data the page turns into elements with textContent: paragraphs, headings (with
    their level), fenced code (its text exactly the lines between the fences, without a final newline, so a pasted
    command does not run by itself), lists with checkboxes, quotes, GitHub alerts, tables, rules. HTML is never
    parsed: it stays text. marks: a todo file's tree, whose open steps marked waiting on the user get 'wait'.
    room: an attachment's [bytes left in the page], charged a lower bound of each node as it is made; past them the
    build stops and the tree is None."""
    lines = [_leading_tabs(x) for x in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    try:
        return _blocks(lines, 0, marks, room)
    except _TooLarge:
        return None


def _md_trees(items, left, room=MAX_MD_TREES):
    """The tree of each .md attachment of one round that the budgets leave room for, as {(item, attachment): (tree,
    bytes as sent)}. left: [bytes of text, bytes of trees] the page build may still parse and make, shared by its
    rounds: the text is charged before its parse, the tree as it is made, both whether it is kept or not, so a
    build's time and memory follow them. room: what else the round's trees may add (the past-decisions page's text
    budget). Bytes of trees are as the page's indented data holds them, a tree's lines 8 deeper (under view >
    rounds or entries > decisions > items > attachments)."""
    trees = {}
    for i, item in enumerate(items):
        for k, att in enumerate(item.get("attachments", [])):
            if "text" not in att or not att["src"].lower().endswith(".md"):
                continue
            size, cap = len(att["text"].encode("utf-8")), min(left[1], room)
            if size > left[0] or cap <= 0:
                continue
            left[0] -= size
            made = [cap]
            tree = markdown_blocks(att["text"], marks=False, room=made)
            left[1] -= min(cap - made[0], left[1])  # what it made, kept or not
            if tree is None:  # past the room while it was built
                continue
            # Its size indented, without that encoding, which json does in Python (2.3 s for a round measured on
            # 3.9): the compact one in C, then what the indentation adds, given up past the room left.
            sent = len(_script_json(tree, compact=True).encode("utf-8"))
            extra = _indent_bytes(tree, 8, cap - sent)
            if extra is not None:
                left[1] -= sent + extra - (cap - made[0])  # the rest of its size
                room -= sent + extra
                trees[i, k] = (tree, sent + extra)
            tree = None  # a tree let go is freed before the next parse: Python's collector would walk it there
    return trees


def _indent_bytes(obj, level, limit):
    """The bytes indent=1 adds to obj's compact JSON with obj at that nesting level, or None past limit: a space
    after each key's colon, a line break and indent before each member, and before each closing bracket."""
    extra, stack = 0, [(obj, level)]
    while extra <= limit and stack:
        o, at = stack.pop()
        members = o.values() if isinstance(o, dict) else o if isinstance(o, list) else ()
        if members:
            extra += (len(o) if isinstance(o, dict) else 0) + len(members) * (at + 2) + at + 1
            stack.extend((x, at + 1) for x in members)
    return extra if extra <= limit else None


def _with_trees(items, trees):
    """The items with each tree in its attachment's copy, as 'blocks'."""
    out = []
    for i, item in enumerate(items):
        if any(key[0] == i for key in trees):
            item = dict(item, attachments=[dict(att, blocks=trees[i, k][0]) if (i, k) in trees else att
                                           for k, att in enumerate(item["attachments"])])
        out.append(item)
    return out


def _todo(first, rest, waiting, left):
    hint = None
    if waiting and waiting.group(1).strip():
        hint = waiting.group(1).strip()
        first = first[waiting.end() + 1:]
    # The tree is kept and sent: each row's item has a limit, and all rows together MAX_TODO (left[0] is what is
    # still to spend), so the total stays bounded.
    size = len(first) + sum(len(x) + 1 for x in rest)
    over = size > MAX_ITEM or size > left[0]
    if over:
        blocks = markdown_blocks(first[:512]) + [{"t": "p", "c": [{"t": "text", "v": "(The rest of this item is "
                                                                   "not shown here; read it in the file.)"}]}]
    else:
        left[0] -= size
        # The title is the first line only, the line the ref names; the rest of its paragraph (a lazy continuation)
        # opens the body. Only a table looks past a paragraph's first line, so two lines tell whether the first is
        # one, and the other lines then parse as they would after it. Only those two lines are parsed twice.
        if first.strip() and markdown_blocks("\n".join([first] + rest[:1]))[0]["t"] == "p":
            blocks = [{"t": "p", "c": _inline(first.strip())}] + markdown_blocks("\n".join(rest))
        else:
            blocks = markdown_blocks("\n".join([first] + rest))
    if blocks and blocks[0]["t"] == "p" and first.strip():
        title, body = blocks[0]["c"], blocks[1:]
    else:
        title, body = [{"t": "text", "v": hint or first[:512].strip()}], blocks
    # ref: the item's first line as written, so the agent can find it in the file; None for an empty one.
    return {"title": title, "body": body, "waiting": hint, "ref": first.strip()[:MAX_REF] or None}


def _task_items(lines):
    """Each task item among lines, outside code blocks and not inside another item: (its first line, its indented
    lines with that indent taken off, whether it is open)."""
    i, n, fence = 0, len(lines), None
    while i < n:
        line = lines[i]
        if fence:
            if fence.match(line):
                fence = None
            i += 1
            continue
        f = _fence(line)
        if f:
            fence = _closes(f[1])
            i += 1
            continue
        m = _TODO_ITEM.match(line)
        if not m:
            i += 1
            continue
        col = len(m.group(1)) + 2
        j = i + 1
        while j < n and (not lines[j].strip() or _indent(lines[j]) >= col):
            j += 1
        end = j
        while end > i + 1 and not lines[end - 1].strip():
            end -= 1
        yield m.group(3) or "", [x[col:] for x in lines[i + 1:end]], m.group(2) == " "
        i = j


def _marker(first):
    """The (waiting on you: ...) marker at the start of an item's first line, or None."""
    m = _WAITING.match(first)
    return m if m and m.group(1).strip() else None


def _steps(lines, depth, chain):
    """The items under a chain's top item, TODO_DEPTH deep: counted [done, all], the open ones' refs kept, and the
    marked ones as (hint, ref or None). A step is never parsed on its own: its text is in the top item's body."""
    for first, rest, is_open in _task_items(lines):
        chain["steps"][1] += 1
        if not is_open:
            chain["steps"][0] += 1
        else:
            w = _marker(first)
            ref = (first[w.end() + 1:] if w else first).strip()[:MAX_REF]
            if ref:
                chain["refs"].append(ref)
            if w:
                chain["marks"].append((w.group(1).strip(), ref or None))
        if depth < TODO_DEPTH:
            _steps(rest, depth + 1, chain)


def parse_todos(text):
    """The chains of a todo.md: one row per top-level item that is open or has an open item under it, each {title,
    body, waiting, ref, open, steps, target, refs, waits, rounds}. The title is the item's first line as inline
    nodes (its first 512 characters past the item limit, the marker's text when the line holds only that), the
    body a markdown_blocks tree of its indented lines, the items under it included (and of the first line too when
    that line opens no paragraph, such as a heading). ref is its first line after its marker, trimmed and cut to
    MAX_REF characters (None when empty), as for every item. waiting is the (waiting on you: ...) hint of the first
    marked open item, itself first, that has a ref, else of the first marked one, or None; steps is [done, all] of
    the items under it. target is the ref a message or Later goes to: that marked item's, else its own when open,
    else the first open one's under it. refs are the refs of the chain's open items, waits those of its marked ones. rounds are
    the decisions rounds its text names, as "<topic folder>/round-NN.json"."""
    lines = [_leading_tabs(x) for x in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out, left = [], [MAX_TODO]
    for first, rest, is_open in _task_items(lines):
        if len(out) >= MAX_TODOS:
            break
        chain = {"steps": [0, 0], "refs": [], "marks": []}
        _steps(rest, 1, chain)
        if not is_open and chain["steps"][0] == chain["steps"][1]:
            continue  # done, with nothing open under it
        w = _marker(first) if is_open else None
        t = _todo(first, rest, w, left)
        own = [t["ref"]] if is_open and t["ref"] else []
        marks = ([(t["waiting"], t["ref"])] if w else []) + chain["marks"]
        waits = [ref for _, ref in marks if ref]
        # The hint shown and the ref a message or Later goes to are one item's: the first marked one with a ref.
        hint = next((h for h, ref in marks if ref), marks[0][0] if marks else None)
        rounds = []
        for m in _ROUND_NAME.finditer("\n".join([first] + rest)):
            name = "%s/%s.json" % m.groups()
            if name not in rounds and len(rounds) < MAX_ROUND_LINKS:
                rounds.append(name)
        t.update(waiting=hint, open=is_open,
                 steps=chain["steps"], refs=own + chain["refs"], waits=waits, rounds=rounds,
                 target=(waits + own + chain["refs"] + [None])[0])
        out.append(t)
    return out


def todo_file(root):
    """The project's todo file: the first of TODO_FILES under its root that is a file, or None."""
    for name in TODO_FILES:
        path = Path(root) / name
        # A link out of the project is not followed: the page would show whatever file it points to.
        if path.is_file() and Path(root).resolve() in path.resolve().parents:
            return path
    return None


def project_todo(p):
    """(path, file, error) for a project file from the home: the file its `todo` key names, a path inside its root,
    with no fallback to TODO_FILES (a typo shows an error, not another file); without the key, todo_file. `file` is
    the path the page shows, relative to the root. The key's value never goes into the error."""
    if "todo" not in p:
        path = todo_file(p["root"])
        return path, path.relative_to(p["root"]).as_posix() if path else None, None
    if not os.path.isdir(p["root"]):
        return None, None, None  # a project whose folder is gone shows no todo file, as without the key
    try:
        root = Path(p["root"]).resolve()
        path = _inside(root, p["todo"], "todo") if isinstance(p["todo"], str) else None
        # shortcut: the check and read_todo_file's open are two steps, as for TODO_FILES: a link swapped in between
        # can point out of the root. Open each part with O_NOFOLLOW if that bites.
        if path and stat.S_ISREG(path.stat().st_mode):
            # The name on disk (a link's target) skipped the key's check: a lone surrogate (a name not UTF-8 on
            # Linux) would fail the list page's UTF-8, and a control or bidi character is no use there.
            file = path.relative_to(root).as_posix()
            return path, _one_line(file, len(file)), None
    except (ValueError, OSError, RuntimeError):  # DecisionsError is a ValueError; a name too long for stat, OSError
        pass
    return None, TODO_KEY_FILE, TODO_KEY_ERROR


def read_todo_file(path):
    """(todos, error): the open items of a todo file, or none and why it was not read. Bounded and non-blocking
    like every other file Signoff reads; a byte that is not UTF-8 shows as a replacement character."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return [], "not a file"
            data = f.read(MAX_TODO + 1)
    except OSError as e:
        return [], "cannot be read (%s)" % (e.strerror or type(e).__name__)
    if len(data) > MAX_TODO:
        return [], "larger than %d KiB" % (MAX_TODO >> 10)
    return parse_todos(data.decode("utf-8-sig", "replace")), None


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

    def _host(self):
        """The request's Host when it is exactly one of this server's names, sent once (two Host headers are
        refused, whichever comes first), else None."""
        given = self.headers.get_all("Host") or []
        return given[0] if len(given) == 1 and given[0] in self.server.hosts else None

    def _route(self):
        """Host and token checks for every request. Returns the path after /<token>/, or None after an error."""
        srv = self.server
        if self._host() is None:
            self._send(403, ("wrong host: Signoff answers only to %s or %s; over SSH, forward the same port number\n"
                             % srv.hosts).encode("utf-8"))
            return None
        path, mark, query = self.path.partition("?")
        if not path.startswith("/") or "#" in self.path or (mark and query not in THEME_QUERIES):
            self._send(404, b"not found\n")
            return None
        self.query = query if mark else None  # only a board takes one: ?theme=light or ?theme=dark
        token, slash, rest = path[1:].partition("/")
        given = token.encode("utf-8", "surrogateescape")
        self.board_request = srv.board_for(given) if slash else None
        if self.board_request:
            return rest  # not activity: a board could reload itself to keep the server alive
        if not slash or not srv.page_for(given):
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
        if srv.inbox:
            used = srv.use_start(token.encode("utf-8", "surrogateescape")) if token and "/" not in token else None
            if used is None:
                return self._send(404, b"not found\n")
            if used:
                return self._send(303, b"", headers=[("Location", "/" + used)])
            # Only this pair dies: the inbox and every other open tab go on.
            return self._send(410, b"This start link was already used or has expired, so the page it opened is "
                                   b"closed. For a new link, ask your agent to open the Signoff inbox again.\n")
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
        self._send(410, b"This start link was already used, so Signoff has stopped to be safe. Your saved answers are "
                        b"kept: ask your agent to open Signoff again for a new link.\n")
        srv.finish(now=True)

    def do_GET(self):
        if self._host() and self.path.startswith("/b/"):
            return self._start()
        rest = self._route()
        if rest is None:
            return
        srv = self.server
        if self.board_request:
            board = BOARD_ROUTE.fullmatch(rest)
            html = self.board_request.board(int(board.group(1)), int(board.group(2))) if board else None
            if html is None:
                return self._send(404, b"not found\n")
            return self._send(200, html, "text/html; charset=utf-8", [("Content-Security-Policy", BOARD_CSP)])
        if self.query is not None:  # only a board takes one; checked first, so no page is built for it
            return self._send(404, b"not found\n")
        target, rest = srv.rounds_at(rest)
        if target is None:
            return self._send(404, b"not found\n")
        if rest == "":
            nonce = secrets.token_urlsafe(18)
            view = target.view()
            # Every page opens in the kept theme; only the inbox can keep a new one (POST prefs), and not from its
            # past-decisions page, where no POST answers.
            view["prefs"] = dict(read_prefs(srv.home if srv.inbox else inbox_home()),
                                 keep=srv.inbox and view["mode"] != "history")
            name = view.get("project") if view.get("mode") == "rounds" else view.get("root")
            if name and view.get("mode") in ("rounds", "history"):  # the header's monogram, as the inbox shows it
                who = _who(None, name)
                view["mono"] = {"mono": who["mono"], "color": who["color"]}
            data = _script_json(view, compact=target is srv and srv.inbox)
            body = srv.page.replace("__NONCE__", nonce).replace("__DATA__", data).encode("utf-8")
            csp = ("default-src 'none'; script-src 'nonce-%s'; style-src 'nonce-%s'; img-src 'self'; "
                   "connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                   % (nonce, nonce))
            return self._send(200, body, "text/html; charset=utf-8", [("Content-Security-Policy", csp)])
        if rest == "state" and target is srv and srv.inbox:  # the list page's count, as in state.json
            if srv.state is None:
                return self._send(503, b"not counted yet\n")
            return self._send(200, json.dumps(srv.state).encode("utf-8"), "application/json")
        m = IMG_ROUTE.fullmatch(rest)
        found = target.picture(int(m.group(1))) if m else None
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
        if srv.read_only or srv.inbox and HISTORY_ROUTE.fullmatch(rest):  # past decisions, here or in the inbox
            return self._refuse(405, b"read only\n")
        target, rest = srv.rounds_at(rest)
        m = SAVE_ROUTE.fullmatch(rest) if target is not None else None
        # The list page: a message on a todo item or Later on one; the theme kept; a round parked or put back.
        message = srv.inbox and target is srv and rest in ("request", "prefs", "park")
        if self.query is not None or not (m or message) or (m and int(m.group(1)) >= len(target.rounds)):
            return self._refuse(404, b"not found\n")
        index = int(m.group(1)) if m else None
        limit = target.rounds[index].max_body if m else {"prefs": MAX_PREFS_BODY, "park": MAX_PARK_BODY}.get(
            rest, MAX_REQUEST_BODY)
        # The page's own origin is the name it was loaded by, which _route took as one of this server's.
        if self.headers.get("Origin") != "http://" + self._host():
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
        if len(length) > 9 or int(length) > limit:  # 9 digits: int() stays cheap and in range
            return self._refuse(413, b"too large\n")
        if message:
            add = {"request": srv.add_request, "prefs": srv.add_prefs, "park": srv.park}[rest]
            code, reply = add(self.rfile.read(int(length)))
            return self._send(code, reply, "application/json" if code == 200 else "text/plain; charset=utf-8")
        round_ = target.rounds[index]
        try:
            body = json.loads(self.rfile.read(int(length)).decode("utf-8"))
            # rev: how many saves of this round the page has seen. The page always sends it; absent, nothing is checked.
            rev = body.pop("rev", None) if isinstance(body, dict) else None
            if rev is not None and (not isinstance(rev, int) or isinstance(rev, bool) or rev < 0):
                raise ValueError("'rev' must be a count of saves")
            state = check_answers(body, round_.decisions, previous=round_.saved)
        except (ValueError, RecursionError) as e:  # bad JSON, bad UTF-8, too deep, bad answers
            return self._send(400, ("bad answers: %s\n" % str(e)[:200]).encode("utf-8", "replace"))
        try:
            result = target.store(index, state, rev)
        except OSError as e:
            return self._send(500, ("could not write the answers file (%s); is it open in another program?\n"
                                    % (e.strerror or e)).encode("utf-8", "replace"))
        if isinstance(result, tuple):
            return self._send(result[0], (result[1] + "\n").encode("utf-8", "replace"))
        self._send(200, json.dumps({"ok": True, "rev": result}).encode("utf-8"), "application/json")
        if state["done"] and target.finished.is_set():
            target.finish()


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
        self.sha256 = hashlib.sha256(read_regular(self.path, MAX_DECISIONS)).hexdigest()
        # When the round was asked: its file's time, which no re-run moves (Signoff never writes a round file).
        at = (_mtime(self.path) or 0) / 1e9
        self.opened = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)) if 0 < at <= time.time() else None
        self.answers_path = answers_path_for(self.path)
        self.markdown_path = self.answers_path.with_name(self.answers_path.stem + ".md")
        self.saved = self.saved_at = None
        self.meta = {}  # accepted, accepted_at, changed_after_accept as last written
        self.rev = 0  # saves stored in this run; a page that saw fewer is out of date
        self.disk = None  # SHA-256 of the answers file as this run last read or wrote it
        self.bad = None  # the last outside content (or read error) reported as not taken in, so it is said once
        if self.answers_path.exists():
            try:
                raw = read_regular(self.answers_path, MAX_ROUND_BODY)
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
        notes = MAX_LINE_NOTES * (MAX_TEXT + MAX_QUOTE) * sum(bool(it.get("lines")) for it in items)
        # Room for every text field full: up to 6 bytes a character once JSON-escaped, plus the rest of the body.
        self.max_body = min(MAX_ROUND_BODY, max(MAX_BODY, (texts * MAX_TEXT + notes) * 6 + 64 * 1024))


def _not_taken_in(r, key, why):
    if r.bad != key:
        r.bad = key
        print("signoff: %s is not taken in: %s" % (r.answers_path, why), file=sys.stderr, flush=True)


def _reread(r):
    """Take in an answers file that another run, an editor or another program wrote since this run last read or
    wrote it, so a reloaded page shows it and saving can go on. A file that says done closes the round here too."""
    if r.finished:
        return
    try:
        # This runs every second under the lock: a FIFO or a huge file at this path must not stall the saves.
        raw = read_image(r.answers_path, MAX_ROUND_BODY)
    except FileNotFoundError:
        return
    except (OSError, DecisionsError) as e:
        return _not_taken_in(r, str(e), "cannot read it (%s); trying again" % (getattr(e, "strerror", None) or e))
    digest = hashlib.sha256(raw).digest()
    if digest in (r.disk, r.bad):  # unchanged, or already found invalid: not parsed again
        return
    try:
        stored = json.loads(raw.decode("utf-8"))
        saved = check_answers(stored, r.decisions, stored=True)
        link = stored.get("decisions")
        if saved["done"] and link and link != {"file": r.path.name, "sha256": r.sha256}:
            raise ValueError("done for another version of %s" % r.path.name)
    except (ValueError, RecursionError) as e:
        # Saves keep being refused rather than writing over it.
        return _not_taken_in(r, digest, "%s; saves to this round are refused until it is valid" % str(e)[:200])
    r.saved, r.saved_at, r.disk, r.bad = saved, stored["saved"], digest, None
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


def load_rounds(target):
    """The rounds of a decisions file or a folder of round-NN.json files; DecisionsError when none is open."""
    target = Path(target)
    paths = round_files(target) if target.is_dir() else [target]
    rounds = [Round(p) for p in paths]
    if all(r.readonly for r in rounds):
        raise DecisionsError("nothing to answer: %s already done (Done is final). Put new questions in a new "
                             "round-NN.json or decisions file" % ", ".join(str(r.answers_path) for r in rounds))
    return rounds


def lock_rounds(rounds):
    """Take the lock of each open round, so this run is its one writer. Returns the descriptors that hold them;
    DecisionsError, holding none, when another run or the inbox holds one."""
    held = []
    for r in rounds:
        if r.readonly:
            continue
        # round-01.json.lock: the full name, so a yarn.json never meets yarn.lock, and *.json.lock ignores only these.
        path = r.path.with_name(r.path.name + ".lock")
        try:
            fd = try_lock(path)
        except OSError as e:
            # Some file systems cannot lock (network mounts): the round is then as it was before locks, best effort.
            print("signoff: cannot lock %s (%s); going on without the lock" % (path, e.strerror or e),
                  file=sys.stderr, flush=True)
            continue
        if fd is None:
            for f in held:
                unlock(f)
            raise DecisionsError("%s is open in another Signoff run or in the inbox; answer it there, or stop that "
                                 "run first" % r.path.name)
        held.append(fd)
    return held


# The page's source: page.html holds the markup, the theme part and a line __PART:<name>__ for each of these, once
# and in this order; each line is replaced by that file's text. Fixed here, never taken from a request or a folder.
PAGE_PARTS = ("page.css", "page-common.js", "page-inbox.js", "page-items.js", "page-rounds.js")


def page_source():
    """The page as served, before __NONCE__ and __DATA__. OSError when it cannot be put together: a server then
    says it cannot start, as for any file it cannot read."""
    page = (HERE / "page.html").read_text(encoding="utf-8")
    marks = ["__PART:%s__\n" % name for name in PAGE_PARTS]
    # Each line holding a placeholder, whole: checked on the template, before any part is in.
    if re.findall(r"[^\n]*__PART:[^\n]*\n?", page) != marks:
        raise OSError("page.html must hold the lines %s, once each and in this order"
                      % " ".join(m.strip() for m in marks))
    for name, mark in zip(PAGE_PARTS, marks):
        text = (HERE / name).read_text(encoding="utf-8")
        for spot in ("__PART:", "__NONCE__", "__DATA__"):  # the nonce and the agent's data go only where page.html says
            if spot in text:
                raise OSError("%s holds %s, which only page.html may" % (name, spot))
        page = page.replace(mark, text)
    return page


class _Server(http.server.ThreadingHTTPServer):
    """What every Signoff server shares: loopback, the tokens, the start link, the idle clock."""
    daemon_threads = True
    read_only = False
    inbox = False
    # On Windows SO_REUSEADDR lets a second socket bind a port that is in use, so a fixed port could be shared.
    # Elsewhere it lets a run take a fixed port that the run before left in TIME_WAIT, and on macOS also bind
    # 127.0.0.1 beside another program's 0.0.0.0 listener: port_in_use() looks for that first (best effort).
    allow_reuse_address = sys.platform != "win32"
    allow_reuse_port = False  # SO_REUSEPORT would share the port anywhere; pinned against a new stdlib default

    def server_bind(self):
        # http.server would look up 127.0.0.1's name here, which nothing uses; on a macOS CI machine that lookup
        # took 35 s in every new process.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def _listen(self, port=0):
        """Bind 127.0.0.1 on port (0: a random one, the default)."""
        self.page = page_source()
        self.token = secrets.token_urlsafe(32)
        self.board_token = getattr(self, "board_token", None) or secrets.token_urlsafe(32)
        self.start_token = secrets.token_urlsafe(32)
        self.start_used = self.start_reused = False
        self.start_used_at = None
        self.lock = threading.Lock()
        self.finished = threading.Event()
        self.last_activity = time.monotonic()
        if port and port_in_use(port):
            raise taken_port(port)
        try:
            http.server.ThreadingHTTPServer.__init__(self, ("127.0.0.1", port), Handler)
        except OSError as e:
            if not port:
                raise
            raise OSError(e.errno, "port %d: %s" % (port, e.strerror or e)) from None  # say which port is taken
        port = self.server_address[1]
        self.host = "127.0.0.1:%d" % port  # the name every link Signoff hands out uses
        # A forwarded port or a devcontainer's Ports tab may name the machine localhost: the same machine and port.
        self.hosts = (self.host, "localhost:%d" % port)
        self.url = "http://%s/%s/" % (self.host, self.token)
        self.start_url = "http://%s/b/%s/" % (self.host, self.start_token)

    def touch(self):
        self.last_activity = time.monotonic()

    def page_for(self, given):
        """Whether a path's first part is this page's token."""
        return secrets.compare_digest(given, self.token.encode())

    def board_for(self, given):
        """What serves the boards under this token, or None."""
        return self if secrets.compare_digest(given, self.board_token.encode()) else None

    def rounds_at(self, rest):
        """The rounds a path under the page token is about, and the rest of the path."""
        return self, rest

    def finish(self, now=False):
        # shutdown() waits for serve_forever(), so it must run on another thread than the request.
        wait = 0.0
        if not now and self.start_used_at is not None:
            wait = max(0.0, self.start_used_at + DONE_GRACE - time.monotonic())
        timer = threading.Timer(wait, self.shutdown)
        timer.daemon = True
        timer.start()


def project_root(path):
    """The root of the project a decisions file or folder belongs to, or None. The server knows no project root, so
    it looks up from the decisions folder: the nearest folder that has a .git (a folder or a file) or holds a .signoff
    folder (not a link: decisions under a link resolve to its target, so they would not map here). The home folder is
    never one (a dotfiles repo there would show the login name). A decisions folder with neither marker has no
    project."""
    path = Path(path).resolve()
    home = Path.home().resolve()
    for up in [path] + list(path.parents):
        marker = up / DEFAULT_FOLDER
        if ((up / ".git").exists() or marker.is_dir() and not marker.is_symlink()) and up != home and up.name:
            return up
    return None


def project_name(path):
    """The project's folder name, or None. Only the name: the page never gets a path."""
    root = project_root(path)
    return root.name if root else None


def project_id(root):
    """The project's id in the Signoff home: the first 16 hex digits of the SHA-256 of its root's real path."""
    return hashlib.sha256(str(root).encode("utf-8", "surrogateescape")).hexdigest()[:16]


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


class _Rounds:
    """Rounds shown on one page and saved from it: a run's own server, and in the inbox the rounds of each run."""
    writable = True  # in the inbox: False while another run holds a round's lock, so the rounds are only shown

    def _read_media(self, rounds):
        self.rounds = rounds
        self.md_trees = None  # each round's .md trees, built at the page's first request (view)
        self.folder = self.rounds[0].path.parent
        self.project = project_name(self.folder)
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

    def picture(self, n):
        return self.image_data[n] if n < len(self.image_data) else None

    def board(self, r, k):
        return self.board_data[r][k] if r < len(self.board_data) and k < len(self.board_data[r]) else None

    def editable_answers(self):
        return [r.answers_path for r in self.rounds if not r.readonly]

    def closing(self):
        pass  # the last open round is being done, under self.lock: the inbox hands an orphan answer over here

    def check_disk(self):
        """Take in answers files written from outside this run; end the run when that leaves no round open."""
        with self.lock:  # with store(), so a file it is writing is never mistaken for an outside one
            for r in self.rounds:
                _reread(r)
            if all(r.finished for r in self.rounds) and not self.finished.is_set():
                try:
                    self.closing()
                except OSError as e:  # the done file is final already: say so, and go on
                    print("signoff inbox: cannot hand over the answers of %s (%s)" % (self.folder.name,
                                                                                     e.strerror or e),
                          file=sys.stderr, flush=True)
                self.finished.set()  # another program finished the last open round: nothing is left to answer here
                self.finish()

    def view(self):
        """What the page gets: each round's decisions with picture and board paths swapped for routes by number
        (one picture index across all rounds), its saved answers, and whether it is read only."""
        rounds, count = [], [0]

        def picture(path):
            count[0] += 1
            return "img/%d" % (count[0] - 1)

        self.check_disk()
        if self.md_trees is None:  # on this request's thread, not in the inbox's look; two at once just build twice
            left = [MAX_MD_INPUT, MAX_MD_TREES]  # for all the rounds of the page
            self.md_trees = [_md_trees(r.decisions["items"], left) for r in self.rounds]
        with self.lock:
            for ri, r in enumerate(self.rounds):
                boards = iter(range(len(self.board_data[ri])))
                items = _media_view(_with_trees(r.decisions["items"], self.md_trees[ri]), r.images, picture,
                                    lambda path, ri=ri, boards=boards: "/%s/%d/%d" % (self.board_token, ri,
                                                                                      next(boards)))
                # A round closed by Done in this run is read only too, even when the run goes on.
                closed = r.readonly or r.finished or not self.writable
                rounds.append({"decisions": dict(r.decisions, items=items), "answers": r.saved, "saved": r.saved_at,
                               "readonly": closed, "accepted": bool(r.meta.get("accepted")) or closed,
                               "changed": bool(r.meta.get("changed_after_accept")), "rev": r.rev,
                               "opened": r.opened})
        return {"mode": "rounds", "project": self.project, "rounds": rounds}

    def store(self, index, state, rev=None):
        """Write one round's answers. Returns the round's new save count, or (status, message) when the save is
        refused. rev, when given, is the save count the page last saw: an older one means another tab saved since."""
        r = self.rounds[index]
        now = utc_now()
        record = {"version": 1, "done": state["done"], "saved": now,
                  "decisions": {"file": r.path.name, "sha256": r.sha256},
                  "answers": state["answers"], "free_text": state["free_text"]}
        with self.lock:
            if r.finished or self.start_reused:
                return 409, "already done"  # Done is final: a late save from another tab must not undo it
            if not self.writable:
                return 409, "open in another Signoff run; answer it there"
            stale = "changed in another tab or window since this page last saved; reload the page"
            if rev is not None and rev != r.rev:
                return 409, stale
            # Another run on the same folder may have written the file since: never write over what it wrote.
            try:
                on_disk = hashlib.sha256(read_regular(r.answers_path, MAX_ROUND_BODY)).digest()
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
            if state["done"] and all(x.finished for x in self.rounds if x is not r):
                self.closing()  # before the JSON: once that says done, the round is final and no longer orphan
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


class SignoffServer(_Rounds, _Server):
    def __init__(self, target, port=0):
        self._read_media(load_rounds(target))
        self._listen(port)


OVER_TEXT_BUDGET = "over the text budget for this page; narrow the list with --since"


def _files_size(path):
    """The bytes of a round's decisions, answers and outcome files on disk, by stat alone (0 for one not there)."""
    total = 0
    for p in (path, answers_path_for(path), path.with_name(path.stem + ".outcome.json")):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


class _History:
    """The past-decisions listing: every round in the topic folders of the given decisions folders, newest first.
    Nothing is written. Broken files are listed with their error; pictures and boards are read when the page asks for
    them. `--history` lists one folder; the inbox lists a project's (see InboxServer.history)."""
    read_only = True
    inbox = False  # the inbox's copy drops its entries once the page has them: it lives on

    def _list(self, name, bases, since=None, budget=HISTORY_BUDGET):
        self.name = name
        self.board_token = getattr(self, "board_token", None) or secrets.token_urlsafe(32)
        self.rounds = []
        used = [0]
        if not self.inbox:  # the inbox's lists outlive a build: a number names one file while the inbox lives
            self.pictures, self.boards = [], []
        topics, seen = [], set()
        for base in bases:
            real_base, here = base.resolve(), set()
            for topic in (base.iterdir() if base.is_dir() else ()):
                try:
                    real = topic.resolve()
                except RuntimeError:  # a link loop, on Python before 3.13 (later ones fail is_dir below)
                    continue
                # A link out of the decisions folder is skipped. One folder lists a topic under each of its names; a
                # topic an earlier folder listed is not listed again (only the inbox passes more than one folder).
                if topic.is_dir() and real_base in real.parents and real not in seen:
                    here.add(real)
                    m = DATE_PATTERN.match(topic.name)
                    date = m.group(0) if m else datetime.date.fromtimestamp(topic.stat().st_mtime).isoformat()
                    if since is None or date >= since:
                        topics.append((date, topic.name, real))
            seen |= here

        def rounds():
            for date, name, folder in sorted(topics, reverse=True):
                files = []
                for p in folder.iterdir():
                    if p.suffix == ".json" and not p.name.endswith((".answers.json", ".outcome.json")) and p.is_file():
                        m = ROUND_FILE.fullmatch(p.name)
                        files.append((int(m.group(1)) if m else 0, p.name, p))
                for _, _, p in sorted(files):
                    yield name, date, p

        def admit(path, kind):
            size = path.stat().st_size
            if used[0] + size > budget:
                return "over the image budget; narrow the list with --since"
            used[0] += size
            if self.inbox:  # an older tab's numbers keep naming the same files after a new build
                got = self.boards[0] if kind == "board" else self.pictures
                n = self.numbers.setdefault((kind, str(path)), len(got))
                if n == len(got):
                    got.append(path)
                if n > 9999:  # shortcut: past what IMG_ROUTE and BOARD_ROUTE take; a restart numbers anew
                    return "not shown: restart the inbox"
                return "/%s/0/%d" % (self.board_token, n) if kind == "board" else "img/%d" % n
            if kind == "board":
                self.boards[-1].append(path)
                return "/%s/%d/%d" % (self.board_token, len(self.boards) - 1, len(self.boards[-1]) - 1)
            self.pictures.append(path)
            return "img/%d" % (len(self.pictures) - 1)

        text_used, text_budget = 0, HISTORY_TEXT_BUDGET
        data_used = 0
        entries = []
        # Each round is read, then held to the budgets at once, so one over them is let go before the next is read.
        for topic, date, p in rounds():
            # The inbox lives on and keeps what a build took from the OS, so it reads no round whose files would
            # pass the budget: by their sizes, half again for the page's escaping and keys (1.0 to 1.5 measured on
            # the examples). Text heavy in "<", ">" or "&", or long lists of small numbers, can take 6 times its size
            # or more: such a round is read and then dropped by the exact check below, one at a time, so memory stays bounded; later, smaller rounds still
            # show. An item's attachments are known only once its file is read; one round's are capped
            # (MAX_ATTACHMENTS).
            fits = not self.inbox or data_used + _files_size(p) * 3 // 2 <= HISTORY_DATA_BUDGET
            e = self._entry(topic, date, p, read=fits)
            entries.append(e)
            if not self.inbox:
                self.boards.append([])
            media = e.pop("media")
            # Newest topic first, so the newest topics fit; attachments are left out here, they have their own budget.
            items = e["decisions"]["items"] if e["decisions"] is not None else []
            texts = [(att, att.pop("text")) for item in items for att in item.get("attachments", []) if "text" in att]
            size = _page_size(e)
            for att, text in texts:
                att["text"] = text
            if data_used + size > HISTORY_DATA_BUDGET:
                e.update(status="error", error=OVER_TEXT_BUDGET, title=None, tab=None, decisions=None, answers=None,
                         saved=None, outcome=None, warning=None)
                continue
            data_used += size
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
        # Then the .md trees, newest first, in what every text left of that budget: as in 0.5.0, no tree takes an
        # older round's text. One budget for the whole build, as for a round's page, so its time and memory follow it.
        left = [MAX_MD_INPUT, MAX_MD_TREES]
        for e in entries:
            items = e["decisions"]["items"] if e["decisions"] is not None else []
            for (i, k), (tree, size) in sorted(_md_trees(items, left, text_budget - text_used).items()):
                text_used += size
                items[i]["attachments"][k]["blocks"] = tree
        self.entries = entries

    def _entry(self, topic, date, path, read=True):
        e = {"topic": topic, "file": path.name, "date": date, "title": None, "tab": None, "status": "new",
             "error": None, "warning": None, "outcome": None, "readonly": True, "decisions": None, "answers": None,
             "saved": None, "media": []}
        if not read:
            e.update(status="error", error=OVER_TEXT_BUDGET)
            return e
        if path.resolve().parent != path.parent.resolve():
            e.update(status="error", error="%s: a link to a file outside this folder" % path.name)
            return e
        try:
            decisions, media = load_decisions(path, lenient=True)
            sha = hashlib.sha256(read_regular(path, MAX_DECISIONS)).hexdigest()
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
                stored = json.loads(read_regular(answers, MAX_ROUND_BODY).decode("utf-8"))
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
        entries = self.entries
        if self.inbox:
            self.entries = None  # held only while one page is built: pictures and boards stay listed by number
            return {"mode": "history", "root": self.name, "timeout": None, "entries": entries, "inbox": True}
        return {"mode": "history", "root": self.name, "timeout": self.timeout, "entries": entries}


class HistoryServer(_History, _Server):
    """`--history`: the past-decisions page of one decisions folder, on a server of its own."""

    def __init__(self, root, since=None, budget=HISTORY_BUDGET, timeout=None, folder=DEFAULT_FOLDER, port=0):
        root = Path(root).resolve()
        if not root.is_dir():
            raise DecisionsError("%s: not a folder" % root)
        self.root, self.timeout = root, timeout
        self._list(root.name, [root / folder], since, budget)
        self._listen(port)


# ---- The inbox: one page for the open rounds of every run and the todo items of every project ----
START_TTL = 120.0  # seconds a start link from the control socket waits for its first use
PAGE_IDLE = 12 * 3600.0  # a page token no request used for this long is closed: it may sit in browser history
RUNS_MAX_AGE = 14 * 86400  # runs/ and sessions/ are never deleted, so only recent entries are read
SESSIONS_MAX_AGE = 2 * 86400
IDLE_AFTER = 60  # seconds after its turn ended that a live session shows as Idle (never counted as waiting)
SENT_MAX_AGE = 86400  # how long an answered round stays listed as sent
STATE_EVERY = 2.0  # seconds between the inbox's own looks at runs, answers and todo files, for state.json
# Group numbers are never reused (an old tab must not save into another run's round), so they only grow.
RUN_ROUTE = re.compile(r"^run/(0|[1-9][0-9]{0,8})/(.*)$", re.S)
HISTORY_ROUTE = re.compile(r"^history/([0-9a-f]{16})/(.*)$", re.S)
MAX_HISTORY_RUNS = 1000  # newest runs/ files a project's past-decisions page reads for its decisions folders
MAX_PARK_BODY = 256  # {"run", "round", "park"} as the page sends it
MAX_PARKED = 200  # rounds in parked.json
MAX_PARKED_FILE = 256 * 1024
MAX_SOCKET_PATH = 100  # macOS takes 104 bytes for a Unix socket's path, Linux 108
MAX_CONTROL = 4096
MAX_PANEL_TEXT = 200  # characters in each title of a rows reply (the mod's MAX_TEXT)
MAX_ROWS = 24  # rounds and items in a rows reply: at 12 bytes a character JSON-escaped, within one 64 KiB line
TARGET = ("run", "round", "item", "inbox")  # an open's target: the numbers a rows reply gives, each below MAX_NUMBER
MAX_NUMBER = 2 ** 31
MAX_REQUEST_BODY = 32 * 1024  # a message on a todo item, as the page sends it
MAX_REQUEST_TEXT = 1000  # characters in one message
MAX_HANDOVER = 10  # pending requests a SessionStart hands over, and the mod asks `inbox requests` for at once
MAX_SHOWN_REQUESTS = 200  # recent request files the list page reads per project
MAX_HANDOVER_TEXT = 8000  # characters of request lines a SessionStart hands over, and `inbox requests` prints
# An older plugin's read_request drops a kind it does not know: its sessions never print a new kind's request.
REQUEST_KINDS = {"todo": "todo item", "vuln": "vulnerability finding", "later": "todo item",
                 "answer": "Signoff rounds"}  # vuln: only 0.3.0 and 0.4.0 wrote one; it still reaches the agent
LATER_TEXT = "The owner asks to park this item for later: remove its waiting marker and keep it open."
# AD-28: the text the inbox hands over once an orphan group's rounds are done, or when it lets go of a group whose
# newest run stopped before handing them over. It asks the agent to tell the user first, since the mod's line
# has no hand-over head and the session may be at other work.
ANSWER_TEXT = ("These rounds are answered, but the Signoff run that asked them ended before it could hand the answers "
               "over (they came in the Signoff inbox, or from another program). Tell the user that the answers came "
               "in, and once the user says so, read each "
               "round's answers file in that topic folder under your decisions folder and act on it as the signoff "
               "skill says. A round that already has its outcome file (<round>.outcome.json), or whose answers file "
               "does not say done: true, needs nothing: just mark this done. Do not run Signoff on these rounds "
               "again.")
HANDOVER_HEAD = ("Signoff inbox: the user left the messages below for this project in the Signoff inbox, and they are "
                 "not handled yet. Tell the user about them in your first reply, and handle each once the user says "
                 "so.")
HEX16 = re.compile(r"[0-9a-f]{16}")  # a project id, a request id
MAX_ROW_ITEMS = 60  # item titles a round's row carries for the list's search
MAX_ROW_TEXT = 200  # characters of each


def _read_file(path, limit):
    """The bytes of a regular file of at most limit bytes, or None: never blocking."""
    try:
        return read_regular(path, limit)
    except OSError:
        return None


def _json_object(raw):
    try:
        data = json.loads(raw.decode("utf-8")) if raw is not None else None
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _read_json(path, limit=65536):
    """A small JSON object from the Signoff home, or None: bounded, regular files only, never blocking."""
    return _json_object(_read_file(path, limit))


def read_prefs(home):
    """The theme, palette, density and sort every page opens in, from prefs.json in the Signoff home. Each value
    outside its closed list, a missing or broken file, and a relative home give the default."""
    stored = (_read_json(home / "prefs.json", MAX_PREFS_FILE) if home.is_absolute() else None) or {}
    theme, palette, density, sort = (stored.get(k) for k in ("theme", "palette", "density", "sort"))
    return {"theme": theme if theme in THEMES else THEMES[0], "palette": palette if palette in PALETTES else PALETTES[0],
            "density": density if density in DENSITIES else DENSITIES[0], "sort": sort if sort in SORTS else SORTS[0]}


def _same(paths, root):
    """The first of paths that names root's folder, or None: samefile also meets a link or a path in another case
    on a case-blind disk."""
    if root in paths:
        return root
    for p in paths:
        try:
            if os.path.samefile(p, root):
                return p
        except (OSError, ValueError):  # gone, or a NUL in it
            pass
    return None


def _recent(path, max_age):
    try:
        return time.time() - path.stat().st_mtime <= max_age
    except OSError:
        return False


def _alive(pid):
    """Whether a run's process is still there. A run killed some other way than Ctrl+C, SIGTERM or SIGHUP leaves no
    ended in its entry."""
    if sys.platform == "win32":
        return True  # os.kill would end the process there; the inbox does not run on Windows yet
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _entry_ok(e):
    """A runs/ entry as register_run writes it."""
    names = e.get("rounds") if isinstance(e, dict) else None
    return (isinstance(e, dict) and e.get("schema") == 1 and isinstance(e.get("run"), str)
            and re.fullmatch(r"[0-9a-f]{1,64}", e["run"]) is not None and isinstance(e.get("folder"), str)
            and os.path.isabs(e["folder"]) and not CONTROL_CHARS.search(e["folder"])
            and isinstance(names, list) and names and all(
                isinstance(n, str) and n == Path(n).name and n.endswith(".json") and not n.startswith(".")
                and not CONTROL_CHARS.search(n) for n in names)
            and isinstance(e.get("pid"), int) and not isinstance(e["pid"], bool) and e["pid"] > 0
            and isinstance(e.get("started"), str) and SAVED_PATTERN.fullmatch(e["started"]) is not None
            and (e.get("project") is None or (isinstance(e["project"], str)
                                              and re.fullmatch(r"[0-9a-f]{16}", e["project"]) is not None)))


def _mtime(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _epoch(stamp):
    """Seconds since 1970 for a time like 2026-01-01T12:00:00Z, or None: the pattern alone lets 2026-99-99 through."""
    try:
        return datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return None


def _who(project, name):
    """How a project shows on a row: its id, its name, two letters, and a colour that only adds to them."""
    letters = [c for c in name if c.isalnum()]
    return {"project": project, "name": name, "mono": "".join(letters[:2]).upper() or "?",
            "color": int(hashlib.sha256(name.encode("utf-8", "surrogateescape")).hexdigest()[:4], 16) % 6}


def _typed_line(text):
    """Whether text is one line as a person types it: no control, line-separator or lone surrogate character."""
    return not any(unicodedata.category(c) in ("Cc", "Cs", "Zl", "Zp") for c in text)


def _one_line(text, limit):
    """Text for a model's context: at most limit characters, each control, format (bidi) or line-separator one a
    space."""
    return "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Cs", "Zl", "Zp") else c for c in text[:limit])


def _unpathed(g, text):
    """A run's text as the list shows it: the project's folder by its name, the user's home as ~. A run
    outside any project has its decisions folder's name (the list's name for it), which for a folder that is the home
    itself is the login name; the home is then only ~."""
    root, home = str(project_root(g.folder) or g.folder), os.path.expanduser("~")
    if root != home:
        text = text.replace(root, g.name)
    if len(home) > 1:
        text = text.replace(home, "~")
    return text


def _none_picked(answer, item):
    """Whether the user unticked a many item's recommendation: an empty choice that is an answer, "none of these".
    The page saves an untouched item without a recommendation as an empty or null choice from the user too."""
    return answer.get("choice") == [] and answer.get("source") == "user" and item.get("recommended") is not None


def _roots(projects):
    """The known projects' real roots with their ids, a nested root before the one that holds it."""
    return sorted(((os.path.realpath(q["root"]), q["id"]) for q in projects.values()), reverse=True)


def _project_in(path, roots):
    """The id of the project whose root holds the real path (the innermost), or None."""
    return next((i for r, i in roots if path.startswith(r + os.sep)), None)


def _decided(answer, item):
    """Whether an item is decided: a choice that is not null or an empty list, and not the preselected
    recommendation left untouched (the page saves an untouched item without a recommendation as a null choice); or a
    recommendation the user unticked. A settled item is decided while its choice (unsaved: its recommendation) is
    the recommendation, the user's earlier answer."""
    if "settled" in item:
        choice, rec = (answer or {}).get("choice", item["recommended"]), item["recommended"]
        if choice == rec or isinstance(rec, list) and isinstance(choice, list) and sorted(choice) == sorted(rec):
            return True
    if not answer:
        return False
    if _none_picked(answer, item):
        return True
    return answer.get("choice") not in (None, []) and answer.get("source") != "recommended"


def _whole(n):
    """One of a target's numbers (TARGET) as JSON gave it: an int below MAX_NUMBER, never a bool (an int too)."""
    return type(n) is int and 0 <= n < MAX_NUMBER


def read_request(path):
    """A request file (requests/<project>/<id>.json) as the inbox writes it, or None."""
    r = _read_json(path, 32768)
    ok = (r is not None and r.get("schema") == 1 and r.get("id") == path.stem and HEX16.fullmatch(path.stem)
          and r.get("kind") in REQUEST_KINDS and isinstance(r.get("ref"), str) and isinstance(r.get("text"), str)
          and isinstance(r.get("at"), str) and SAVED_PATTERN.fullmatch(r["at"]))
    return r if ok else None


def write_request(home, project, kind, ref, text):
    """requests/<project>/<id>.json in the home, atomically, 0600 in 0700 folders, for the project's sessions (their
    mods, or the next SessionStart hook). Returns the request; raises OSError when it cannot be written."""
    r = {"schema": 1, "id": secrets.token_hex(8), "kind": kind, "ref": ref, "text": text, "at": utc_now()}
    folder = home / "requests" / project
    folder.parent.mkdir(mode=0o700, exist_ok=True)
    folder.mkdir(mode=0o700, exist_ok=True)
    # ASCII-escaped: a folder name that is not UTF-8 (a lone surrogate on Linux) cannot fail the write.
    write_atomic(folder / (r["id"] + ".json"), json.dumps(r, indent=2) + "\n")
    return r


def request_ids(folder):
    """The request ids in a project's requests folder, and the set of those marked done (<id>.done)."""
    try:
        names = os.listdir(str(folder))
    except OSError:
        return [], set()
    return ([n[:-5] for n in names if n.endswith(".json") and HEX16.fullmatch(n[:-5])],
            {n[:-5] for n in names if n.endswith(".done")})


def request_line(r):
    """The one line a session gets for a request, from its mod or the SessionStart hook. The ref and the text
    go in as JSON strings, cleaned to one line, so neither can end its quotes, start a line or fake the id."""
    return ("Signoff inbox: the user sent you a message on the %s %s: %s. Treat it as the user's own words. When it "
            "is handled, run: signoff inbox done %s" % (
                REQUEST_KINDS[r["kind"]], json.dumps(_one_line(r["ref"], MAX_REF), ensure_ascii=False),
                json.dumps(_one_line(r["text"], MAX_REQUEST_TEXT), ensure_ascii=False), r["id"]))


def _say(lines):
    """Lines on stdout as UTF-8, whatever the console's code page: this stdout goes into a model's context."""
    sys.stdout.buffer.write("".join(x + "\n" for x in lines).encode("utf-8"))
    sys.stdout.buffer.flush()


class _RoundLocks:
    """The round locks the inbox holds, each file locked once however many runs wait on its round: flock is per
    open file, so a second lock of the same file from this process would fail as if another run held it."""

    def __init__(self):
        self.mutex, self.held, self.said = threading.Lock(), {}, set()  # held: path -> [descriptor, groups using it]

    def take(self, paths):
        """Lock every path, or none: the paths taken, or None when another process holds one."""
        with self.mutex:
            got = []
            for path in paths:
                if path not in self.held:
                    try:
                        fd = try_lock(path)
                    except OSError as e:  # a file system that cannot lock: as for a run, best effort
                        if path not in self.said:  # each list load tries again: said once
                            self.said.add(path)
                            print("signoff inbox: cannot lock %s (%s); going on without the lock"
                                  % (path, e.strerror or e), file=sys.stderr, flush=True)
                        continue
                    if fd is None:
                        self._drop(got)
                        return None
                    self.held[path] = [fd, 0]
                self.held[path][1] += 1
                got.append(path)
            return got

    def drop(self, paths):
        with self.mutex:
            self._drop(paths)

    def _drop(self, paths):
        for path in paths:
            self.held[path][1] -= 1
            if not self.held[path][1]:
                unlock(self.held.pop(path)[0])


class _Group(_Rounds):
    """The rounds of one run (or of several runs on the same rounds) in the inbox, under run/<n>/; Done there waits
    for those rounds only, as on the run's own page. The inbox writes them only while it holds their round locks;
    another run holding one leaves them shown read only. An orphan group's rounds are listed by no live run (AD-24)."""
    start_reused = False
    orphan = gone = handed = False

    def __init__(self, entry, rounds, name, locks):
        self.entry, self.name, self.locks, self.held = entry, name, locks, []
        self.lock, self.finished = threading.Lock(), threading.Event()
        self.board_token = secrets.token_urlsafe(32)  # its boards open with it, and nothing else does
        self._read_media(rounds)
        self.project = name
        self.take_locks()

    def take_locks(self):
        # round-01.json.lock: the full name, as lock_rounds() takes it for a run.
        got = self.locks.take([r.path.with_name(r.path.name + ".lock") for r in self.rounds if not r.readonly])
        self.held, self.writable = got or [], got is not None

    def release(self):
        with self.lock:  # after a save in progress, and none after it
            self.writable = False
            self.locks.drop(self.held)
            self.held = []

    def finish(self, now=False):
        pass  # a Done ends the run that waits on it, never the inbox

    def store(self, index, state, rev=None):
        if state["done"]:
            self.look()  # whether the group is orphan as of now, not of the last look: a run may have ended since
            if self.gone:  # that look let the group go, as an old tab's next save would find
                return 404, "this round is no longer in the inbox; reload the page"
        return _Rounds.store(self, index, state, rev)

    def closing(self):
        """No run waits on an orphan group: its answers go to the project's sessions as one request (AD-28), with
        the topic folder and round names only. The run's rounds as runs/ lists them, so a round answered before
        this group was made (an inbox restarted in between) is named too."""
        if self.orphan:
            self.hand_over()

    def hand_over(self):
        if self.entry.get("project"):
            write_request(self.home, self.entry["project"], "answer", ("%s / %s" % (
                Path(self.entry["folder"]).name, ", ".join(Path(n).stem for n in self.entry["rounds"])))[:MAX_REF],
                ANSWER_TEXT)
            self.handed = True

    def view(self):
        data = _Rounds.view(self)
        data.update(inbox=True, held=not self.writable, orphan=self.orphan)
        return data


def _control_socket(path):
    """The inbox's control socket: a Unix socket, 0600 in the 0700 home. Whoever can open it may ask for a start
    link; TCP never hands one out."""
    if len(os.fsencode(str(path))) > MAX_SOCKET_PATH:
        raise DecisionsError("%s is too long for a Unix socket (at most %d bytes); use a shorter Signoff home"
                             % (path, MAX_SOCKET_PATH))
    try:
        st = os.lstat(str(path))
    except FileNotFoundError:
        st = None
    if st is not None:
        if not stat.S_ISSOCK(st.st_mode):
            raise DecisionsError("%s is there and is not the inbox's socket; move it away" % path)
        with socket.socket(socket.AF_UNIX) as probe:
            probe.settimeout(2)
            try:
                probe.connect(str(path))
            except ConnectionRefusedError:
                # The one file Signoff removes: its own socket, in its own home, left by an inbox that is gone. This
                # inbox holds inbox.lock, and nothing listens on it.
                os.unlink(str(path))
            else:
                raise DecisionsError("something listens on %s already" % path)
    sock = socket.socket(socket.AF_UNIX)
    try:
        old = os.umask(0o077)
        try:
            sock.bind(str(path))
        finally:
            os.umask(old)
        os.chmod(str(path), 0o600)
        sock.listen(8)
    except BaseException:
        sock.close()
        raise
    return sock


def inbox_request(home, op, timeout=5.0, **fields):
    """Ask the running inbox over its control socket: "open" gives {"link": a new start link}, "stop" ends it;
    "rows" (with project=<id>) gives the mod's panel list, and "open" with its run, round, item and inbox numbers
    opens one of its rows. OSError when no inbox answers."""
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout)
        s.connect(str(Path(home) / "inbox.sock"))
        s.sendall((json.dumps(dict(fields, op=op)) + "\n").encode("utf-8"))
        line = s.makefile("rb").readline(65536)
    try:
        reply = json.loads(line.decode("utf-8"))
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        raise ConnectionError("the inbox gave no answer")
    return reply


class InboxServer(_Server):
    """The inbox (`signoff inbox serve`): one page with what waits on the user in every project: the open rounds of
    the runs in the registry, and the todo items marked (waiting on you: ...); then each project's open todo items.
    One per Signoff home, decided by inbox.lock. Start links come only from the control socket; each opens its own
    page token, and a second use of one closes only that pair. Answers go through the run's own checks and writer."""
    inbox = True
    rounds = ()

    def __init__(self, home, port=0):
        home = Path(home)
        if not home.is_absolute():
            raise DecisionsError("the Signoff home %s is not an absolute path" % home)
        home.mkdir(parents=True, mode=0o700, exist_ok=True)
        st = os.lstat(str(home))
        if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid():  # the inbox runs on POSIX only
            raise DecisionsError("%s is a link or is not yours; the inbox keeps its socket only in a folder of your "
                                 "own" % home)
        os.chmod(str(home), 0o700)  # one that already was there, made by hand, may be open to others
        self.home, self.held, self.control = home, None, None
        self.scan_lock, self.closed = threading.RLock(), threading.Event()
        self.state = self.written = self.state_failed = None  # the count now, and the one state.json last got
        self.backs = 0  # rises of the count the user's own Back made, in state.json: a reader does not notify them
        # Before inbox.lock is made: it turns runs and hooks to the inbox's ways for good, and no inbox would run.
        if port and port_in_use(port):
            raise taken_port(port)
        # A run looks at inbox.lock by taking it for a moment, so a held lock is tried for a while before giving up.
        for wait in (0.05,) * 12 + (None,):
            self.held = try_lock(home / "inbox.lock")
            if self.held is not None:
                break
            if wait is None:
                raise DecisionsError("another inbox is already running for %s" % home)
            time.sleep(wait)
        try:
            self.pairs = {}  # start token -> {page token (None once the pair is closed), made or last used at, used,
            # the path after the page token its first use goes to}
            self.groups, self.group_index, self.sent, self.failed, self.todo_cache = [], {}, {}, {}, {}
            # A group's number is never reused while this inbox lives, but a restart numbers the runs anew: rows
            # gives this number with them and an open's target sends it back, so an old row opens the list.
            self.number = secrets.randbelow(MAX_NUMBER - 1) + 1
            self.done_cache = {}  # answers path -> (stat key, whether it says done), for the orphan look
            self.parked = {}  # parked.json's rounds as of the last look: real path -> parked at
            # Each project's last past-decisions page, for its pictures and boards by number; one built at a time.
            self.histories, self.history_lock = {}, threading.Lock()
            self.round_locks = _RoundLocks()
            self.control = _control_socket(home / "inbox.sock")
            self._listen(port)
            write_atomic(home / "inbox.json", json.dumps({
                "schema": 1, "server_id": secrets.token_hex(16), "port": self.server_address[1],
                "control": str(home / "inbox.sock")}, indent=2) + "\n")
        except BaseException:
            self.close()
            raise
        threading.Thread(target=self.serve_control, daemon=True).start()
        self.refresh_state()
        threading.Thread(target=self._watch_state, daemon=True).start()

    def refresh_state(self, back=False):
        """Count the rows of Waiting on you, as the list page shows them, and write state.json when the count
        changed: the one number for the tab title and later the shell, the push and the phone. Numbers, project ids
        and monograms only: no title, path, todo text or token. With back (a Back just written), a rise of the count
        adds one to backs in the same write."""
        doing = "count what waits"
        try:
            with self.scan_lock:  # view() takes it too; close() waits for it. After close() no count is made, and
                # a page's look locks nothing (see _scan).
                if self.closed.is_set():
                    return
                # The plugin's mod cannot flock, and a dead inbox changes no file: inbox.lock's time is its sign of
                # life. Under scan_lock, so no touch comes after close().
                try:
                    os.utime(str(self.home / "inbox.lock"))
                except OSError:
                    pass  # the mod then shows no count, the safe side
                rows = self.view()["waiting"]
                projects = {}
                for w in rows:  # a run outside any project has no id: it counts under its monogram
                    p = projects.setdefault((w["project"], w["mono"]), {"id": w["project"], "monogram": w["mono"],
                                                                        "waiting": 0})
                    p["waiting"] += 1
                if back and self.state and len(rows) > self.state["waiting"]:
                    self.backs += 1
                counted = {"schema": 1, "waiting": len(rows), "projects": list(projects.values()), "backs": self.backs}
                if not self.state or dict(self.state, at=None) != dict(counted, at=None):
                    self.state = dict(counted, at=utc_now())  # the route serves it even when the write fails
                if self.state is self.written:
                    return
                doing = "write state.json"
                write_atomic(self.home / "state.json", json.dumps(self.state, indent=2) + "\n")
                self.written, self.state_failed = self.state, None
        except Exception as e:  # the next look tries again; said once per kind, and never the details
            why = "could not %s (%s)" % (doing, type(e).__name__)
            if why != self.state_failed:
                self.state_failed = why
                print("signoff inbox: " + why, file=sys.stderr, flush=True)

    def _watch_state(self):
        while not self.closed.wait(STATE_EVERY):
            self.refresh_state()

    def close(self):
        self.closed.set()
        with self.scan_lock:  # a look in progress ends first; a later one locks nothing (see _scan)
            for g in getattr(self, "groups", ()):
                if g:
                    g.release()
        if self.control is not None:
            try:  # on Linux, close() alone leaves the socket listening while serve_control waits in accept()
                self.control.shutdown(socket.SHUT_RDWR)
            except OSError:  # macOS: a listening socket is not connected
                pass
            self.control.close()
        if getattr(self, "socket", None) is not None:
            self.server_close()
        if self.held is not None:
            unlock(self.held)
            self.held = None

    def new_pair(self, to=""):
        """A new start link and the page token it opens; its first use goes to `to` under that token."""
        start, now = secrets.token_urlsafe(32), time.monotonic()
        with self.lock:
            for key, pair in list(self.pairs.items()):  # closed and stale pairs go, so the list stays short
                if not pair["page"] or now - pair["at"] > (PAGE_IDLE if pair["used"] else START_TTL):
                    del self.pairs[key]
            self.pairs[start] = {"page": secrets.token_urlsafe(32), "at": now, "used": False, "to": to}
        return "http://%s/b/%s/" % (self.host, start)

    def use_start(self, given):
        """A start link's visit: the path (page token first) it goes to the first time, "" once it was used or has
        expired (the pair is closed from then on), None for no such link."""
        with self.lock:
            for start, pair in self.pairs.items():
                if secrets.compare_digest(given, start.encode()):
                    if pair["used"] or time.monotonic() - pair["at"] > START_TTL:
                        pair["page"] = None
                        return ""
                    pair["used"], pair["at"] = True, time.monotonic()
                    return pair["page"] + "/" + pair["to"]
        return None

    def rows(self, project):
        """The mod's panel list for one project id: each open round of its live runs and orphan groups, oldest run
        first, by its run's number (run/<n>/) and its index there, with the items not decided yet by index. Titles
        are the decisions file's own, one line each, capped, with the project's folder and the user's home replaced
        (a URL in them stays); at most MAX_ROWS rounds and items in all, so the reply fits the control socket's line,
        and `more` counts the rest."""
        rounds, left, more = [], MAX_ROWS, 0
        with self.scan_lock:
            if self.closed.is_set():
                return {"rounds": [], "more": 0, "inbox": self.number}
            self._scan()
            for n, g in sorted([(n, g) for n, g in enumerate(self.groups) if g], key=lambda x: x[1].entry["started"]):
                if g.entry.get("project") != project:
                    continue
                with g.lock:
                    for ri, r in enumerate(g.rounds):
                        if r.finished or str(r.path) in self.parked:  # the panel lists what waits
                            continue
                        saved = (r.saved or {}).get("answers", {})
                        items = [(k, it["title"]) for k, it in enumerate(r.decisions["items"])
                                 if not _decided(saved.get(it["id"]), it)]
                        if left < 1:
                            more += 1 + len(items)
                            continue
                        shown = items[:left - 1]
                        more += len(items) - len(shown)
                        left -= 1 + len(shown)
                        # item: what the round's row opens, its first item not decided, else its last, whose Focus
                        # shows the round's tail (Accept, Done) under it.
                        rounds.append({"run": n, "round": ri, "title": _one_line(_unpathed(
                            g, r.decisions.get("tab") or r.decisions["title"]), MAX_PANEL_TEXT),
                            "item": items[0][0] if items else len(r.decisions["items"]) - 1, "items": [
                            {"item": k, "title": _one_line(_unpathed(g, t), MAX_PANEL_TEXT)} for k, t in shown]})
        return {"rounds": rounds, "more": more, "inbox": self.number}

    def _look(self):
        with self.scan_lock:
            if not self.closed.is_set():
                self._scan()

    def open_target(self, run, ri, k, number):
        """A start link: to item k of round ri in Focus on run/<run>/, the numbers a rows reply gave, while that
        round is open there and number is this inbox's own; else the list. The server maps the index to the item's
        id; the client sends no text."""
        to = ""
        if number == self.number:
            with self.scan_lock:  # the look lets go of a run that ended since
                if not self.closed.is_set():
                    self._scan()
                    g = self.groups[run] if run < len(self.groups) else None
                    if g:
                        with g.lock:
                            r = g.rounds[ri] if ri < len(g.rounds) else None
                            if r and not r.finished and k < len(r.decisions["items"]):
                                to = "run/%d/#focus=%d/%s" % (run, ri, r.decisions["items"][k]["id"])
        return self.new_pair(to)

    def page_for(self, given):
        now = time.monotonic()
        with self.lock:
            for pair in self.pairs.values():
                if pair["used"] and pair["page"] and secrets.compare_digest(given, pair["page"].encode()):
                    if now - pair["at"] > PAGE_IDLE:
                        pair["page"] = None
                        return False
                    pair["at"] = now
                    return True
        return False

    def board_for(self, given):
        for g in list(self.groups) + list(self.histories.values()):
            if g and secrets.compare_digest(given, g.board_token.encode()):
                return g
        return None

    def rounds_at(self, rest):
        h = HISTORY_ROUTE.fullmatch(rest)
        if h:  # the page is built anew on each load; its pictures come from the last one built
            return (self.history(h.group(1)) if h.group(2) == "" else self.histories.get(h.group(1))), h.group(2)
        m = RUN_ROUTE.fullmatch(rest)
        if not m:
            return self, rest
        n = int(m.group(1))
        return (self.groups[n] if n < len(self.groups) else None), m.group(2)

    def history(self, pid):
        """A project's past-decisions page: every round under each decisions folder its runs used, and the default
        one. A decisions folder is the parent of a runs/ entry's topic folder, strictly inside the project's root;
        the newest MAX_HISTORY_RUNS files of runs/ are read, of any age. None for a project the hooks did not note."""
        p = self._projects().get(pid)
        if p is None:
            return None
        root = os.path.realpath(p["root"])
        folders, found = [os.path.join(root, DEFAULT_FOLDER)], []
        try:
            for e in os.scandir(str(self.home / "runs")):
                if e.name.endswith(".json") and not e.name.startswith("."):
                    found.append((_mtime(Path(e.path)) or 0, e.path))
        except OSError:
            pass
        for _, path in sorted(found, reverse=True)[:MAX_HISTORY_RUNS]:
            e = _read_json(Path(path))
            if _entry_ok(e) and e["project"] == pid:
                folders.append(os.path.dirname(e["folder"]))
        # Each folder resolved once, kept only strictly inside the root (the default one too: it may be a link), and
        # listed by path, so the folder that lists a topic two of them reach is always the same one.
        bases = sorted({Path(b) for b in map(os.path.realpath, folders) if b.startswith(root + os.sep)})
        with self.history_lock:  # one listing built at a time, however many tabs load it
            h, old = _History(), self.histories.get(pid)
            h.inbox = True
            # The project's board token and numbers stay, so an older tab's pictures and boards still load as they
            # were listed; a file gone or changed since is read as it is now, with the same checks.
            h.board_token, h.pictures, h.boards, h.numbers = ((old.board_token, old.pictures, old.boards, old.numbers)
                                                             if old else (None, [], [[]], {}))
            try:
                for b in bases:  # a link loop fails here on every Python (resolve() raises RuntimeError before 3.13)
                    if os.path.lexists(str(b)):
                        os.stat(str(b))
                h._list(p["name"], bases)
            except (OSError, RuntimeError) as e:  # a folder it may not read: the page is not there, the inbox goes on
                # RuntimeError: a link loop swapped in after the stat above still reaches resolve() (before 3.13).
                why = "a link loop" if isinstance(e, RuntimeError) else e.strerror or e
                print("signoff inbox: cannot list the past decisions of %s (%s)" % (p["name"], why),
                      file=sys.stderr, flush=True)
                return None
            self.histories[pid] = h
        return h

    def picture(self, n):
        return None

    def serve_control(self):
        """Answer the control socket until it is closed, each connection on its own thread, so a client that sends
        slowly holds up no other."""
        while True:
            try:
                conn, _ = self.control.accept()
            except OSError:
                return
            threading.Thread(target=self._control_request, args=(conn,), daemon=True).start()

    def _control_request(self, conn):
        """One JSON line in, one out, within 5 seconds all told."""
        with conn:
            op, req, deadline, data = None, None, time.monotonic() + 5, b""
            try:
                while b"\n" not in data and len(data) <= MAX_CONTROL:
                    conn.settimeout(max(0.01, deadline - time.monotonic()))
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                if len(data) <= MAX_CONTROL:
                    req = json.loads(data.split(b"\n")[0].decode("utf-8"))
                    op = req.get("op") if isinstance(req, dict) else None
            except (OSError, ValueError, RecursionError):
                pass
            if op == "open" and not any(k in req for k in TARGET):  # a plain open ignores keys it does not know
                reply = {"link": self.new_pair()}
            elif op == "open":
                target = [req.get(k) for k in TARGET]
                reply = ({"link": self.open_target(*target)} if "offer" not in req and all(map(_whole, target))
                         else {"error": "open takes run, round, item and inbox as whole numbers, all four or none"})
            elif op == "rows":
                project = req.get("project")
                reply = (self.rows(project) if isinstance(project, str) and HEX16.fullmatch(project)
                         else {"error": "rows needs a project id"})
            elif op == "stop":
                reply = {"ok": True}
            else:
                reply = {"error": 'unknown request; send {"op": "open"}, {"op": "stop"}, '
                                  '{"op": "rows", "project": <id>}, or {"op": "open", '
                                  '"run": <n>, "round": <n>, "item": <n>, "inbox": <n>}'}
            try:
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            except OSError:
                pass
        if op == "stop":
            self.shutdown()  # this thread is not the one serve_forever runs on

    def handle_error(self, request, client_address):
        # The default prints a traceback to the log; a request's details have no place there.
        print("signoff inbox: a request failed (%s)" % sys.exc_info()[0].__name__, file=sys.stderr, flush=True)

    def _projects(self):
        found = {}
        folder = self.home / "projects"
        for path in sorted(folder.glob("[!.]*.json")) if folder.is_dir() else ():
            p = _read_json(path)
            if (p and p.get("schema") == 1 and p.get("id") == path.stem and isinstance(p.get("root"), str)
                    and os.path.isabs(p["root"]) and isinstance(p.get("name"), str)):
                found[p["id"]] = p
        return found

    def _sessions(self):
        """The hooks' session files, recent and well formed ones only."""
        found = []
        folder = self.home / "sessions"
        for path in folder.glob("[!.]*.json") if folder.is_dir() else ():
            s = _read_json(path, 4096) if _recent(path, SESSIONS_MAX_AGE) else None
            if (s and s.get("schema") == 1 and isinstance(s.get("project"), str) and isinstance(s.get("state"), str)
                    and isinstance(s.get("at"), str) and SAVED_PATTERN.fullmatch(s["at"])
                    and isinstance(s.get("session"), str)):
                found.append(s)
        return found

    @staticmethod
    def _live(sessions, projects):
        """Rows for the live sessions (not ended, their Claude Code process there): (waiting, idle). Waiting is one
        that said it waits on the user, oldest first; idle is one whose turn ended IDLE_AFTER seconds ago or more
        and that does not wait. `attach` is the session id's first 8 hex digits, for a background session only:
        `claude attach` reaches no other (CLI 2.1.319)."""
        waiting, idle, now = [], [], time.time()
        # shortcut: a live pid is taken as the session's; a session killed while it waited, its pid reused, stays a
        # counted row until its file is SESSIONS_MAX_AGE old. Compare `at` with the process's start time if that bites.
        for s in sessions:
            p, pid = projects.get(s["project"]), s.get("pid")
            if not p or s["state"] == "ended" or type(pid) is not int or pid <= 0 or not _alive(pid):
                continue
            short = s["session"][:8]
            row = dict(_who(s["project"], p["name"]),
                       attach=short if s.get("bg") is True and re.fullmatch(r"[0-9a-f]{8}", short) else None)
            stamp = s.get("waiting_at")
            if s.get("waiting") == "permission":
                if isinstance(stamp, str) and _epoch(stamp) is not None:
                    waiting.append(dict(row, kind="session", since=stamp))
            elif s["state"] == "idle" and (_epoch(s["at"]) or now) <= now - IDLE_AFTER:
                idle.append(dict(row, since=s["at"]))
        return sorted(waiting, key=lambda r: r["since"]), sorted(idle, key=lambda r: r["since"])

    def _todos(self, path):
        try:
            st = path.stat()
        except OSError:
            return [], "cannot be read"
        key = (st.st_mtime_ns, st.st_size)
        cached = self.todo_cache.get(path)
        if not cached or cached[0] != key:
            cached = self.todo_cache[path] = (key, read_todo_file(path))
        return cached[1]

    def add_prefs(self, raw):
        """The theme the user kept in the picker: prefs.json in the home, which every page reads. Every value comes
        from a closed list, so no text of the request reaches the file or a page. A body without density keeps the
        stored one, and likewise a body without sort; a body of only density and/or sort keeps the stored theme and
        palette. Returns (status, body)."""
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            return 400, b"bad request: not JSON\n"
        if (not isinstance(body, dict) or not body or not set(body) <= {"theme", "palette", "density", "sort"}
                or ("theme" in body) != ("palette" in body)
                or body.get("theme", THEMES[0]) not in THEMES or body.get("palette", PALETTES[0]) not in PALETTES
                or body.get("density", DENSITIES[0]) not in DENSITIES or body.get("sort", SORTS[0]) not in SORTS):
            return 400, ('bad request: send "theme" with "palette", and/or "density" and "sort"; theme one of %s, '
                         'palette one of %s, density one of %s, sort one of %s\n'
                         % ("|".join(THEMES), "|".join(PALETTES), "|".join(DENSITIES), "|".join(SORTS))).encode("utf-8")
        prefs = dict(read_prefs(self.home), **body, schema=1, at=utc_now())
        try:
            write_atomic(self.home / "prefs.json", json.dumps(prefs, indent=2) + "\n")
        except OSError as e:
            return 500, ("could not write prefs.json (%s)\n" % (e.strerror or type(e).__name__)).encode("utf-8")
        return 200, b'{"ok": true}'

    def _requests(self, project):
        """(kind, ref) -> the project's recent requests on that todo item, oldest first: text, at, and done once the
        agent wrote <id>.done. shortcut: each look reads up to MAX_SHOWN_REQUESTS small files per project; cache
        them by id (they never change) if a project gathers many."""
        folder = self.home / "requests" / project
        ids, done = request_ids(folder)
        cutoff = (time.time() - RUNS_MAX_AGE) * 1e9
        recent = sorted(((_mtime(folder / (i + ".json")) or 0, i) for i in ids), reverse=True)
        found = {}
        for mtime, i in recent[:MAX_SHOWN_REQUESTS]:
            r = read_request(folder / (i + ".json")) if mtime >= cutoff else None
            if r:
                found.setdefault((r["kind"], r["ref"]), []).append({"text": r["text"], "at": r["at"],
                                                                    "done": i in done})
        for shown in found.values():
            shown.sort(key=lambda r: r["at"])
        return found

    def add_request(self, raw):
        """A message on an open todo item, or Later on one marked waiting, from the list page:
        requests/<project>/<id>.json in the home, for the project's session (its mod, or the next SessionStart
        hook). The todo file is never written. The text of Later is the server's own. Returns (status, body)."""
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            return 400, b"bad request: not JSON\n"
        shape = sorted(body) if isinstance(body, dict) and all(isinstance(v, str) for v in body.values()) else None
        kind = "later" if shape == ["kind", "project", "ref"] and body["kind"] == "later" else "todo"
        if kind == "todo" and shape != ["project", "ref", "text"]:
            return 400, (b'bad request: send {"project", "ref", "text"}, or {"kind": "later", "project", "ref"}, '
                         b'as strings\n')
        text = body["text"].strip() if kind == "todo" else LATER_TEXT
        if kind == "todo" and (not text or len(text) > MAX_REQUEST_TEXT or not _typed_line(text)):
            return 400, b"bad request: a message is one line of 1 to 1000 characters\n"
        with self.scan_lock:  # _projects and the todo cache, as a look uses them
            p = self._projects().get(body["project"]) if HEX16.fullmatch(body["project"]) else None
            if p is None:
                return 404, b"no such project\n"
            # Any open item of a chain takes a message, its steps too; Later only one marked waiting.
            path = project_todo(p)[0]
            key = "waits" if kind == "later" else "refs"
            refs = {r for t in (self._todos(path)[0] if path else []) for r in t[key]}
            if body["ref"] not in refs:
                return 409, (b"that todo item no longer waits on you; reload the page\n" if kind == "later"
                             else b"that todo item is no longer open; reload the page\n")
            try:
                r = write_request(self.home, p["id"], kind, body["ref"], text)
            except OSError as e:
                return 500, ("could not write the request (%s)\n" % (e.strerror or type(e).__name__)).encode("utf-8")
        return 200, json.dumps({"ok": True, "id": r["id"], "at": r["at"]}).encode("utf-8")

    def _parked_file(self):
        """parked.json as the inbox writes it ({schema: 1, rounds: [{path, at, back?}]}), {} when absent, None when it
        is anything else: a link, not that JSON, or over MAX_PARKED_FILE bytes."""
        try:
            data = _json_object(read_regular(self.home / "parked.json", MAX_PARKED_FILE, nofollow=True))
        except FileNotFoundError:
            return {}
        except OSError:  # a link (ELOOP), a folder, a FIFO, too large
            return None
        rounds = data.get("rounds") if data else None
        ok = (data is not None and data.get("schema") == 1 and isinstance(rounds, list) and all(
            isinstance(e, dict) and isinstance(e.get("path"), str) and isinstance(e.get("at"), str)
            and isinstance(e.get("back", ""), str) for e in rounds))
        return data if ok else None

    def _read_parked(self, projects):
        """The rounds parked.json names that may be parked: an absolute real path with no control character, a round
        file's name (not an answers or outcome file), inside a known project's root, each as (at, back, project id);
        back is None while it is parked. Whether the file is there, the user's own and still open is the orphan
        look's (_open_round)."""
        data = self._parked_file() or {}
        roots = None
        found = {}
        for e in data.get("rounds", ())[:MAX_PARKED]:
            p = e["path"]
            if (p in found or not os.path.isabs(p) or CONTROL_CHARS.search(p) or not p.endswith(".json")
                    or p.endswith((".answers.json", ".outcome.json")) or os.path.basename(p).startswith(".")):
                continue
            try:
                if os.path.realpath(p) != p:  # a link on the way could lead out of the project
                    continue
            except (OSError, ValueError):
                continue
            if roots is None:  # read only when something is parked
                roots = _roots(projects)
            pid = _project_in(p, roots)
            if pid is not None:
                found[p] = (e["at"] if SAVED_PATTERN.fullmatch(e["at"]) else None, e.get("back"), pid)
        return found

    def park(self, raw):
        """Park or Back from the list page: {run, round, park}, the numbers of a round the list shows now. The server
        maps them to the round file's real path and keeps it in parked.json in the home, with the time; the round's
        files, its lock and its run are left as they were. Returns (status, body)."""
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            return 400, b"bad request: not JSON\n"
        if (not isinstance(body, dict) or sorted(body) != ["park", "round", "run"] or not _whole(body["run"])
                or not _whole(body["round"]) or type(body["park"]) is not bool):
            return 400, b'bad request: send {"run": <n>, "round": <n>, "park": true or false}\n'
        with self.scan_lock:  # the look, and parked.json's read and write, one request at a time
            if self.closed.is_set():
                return 409, b"the inbox is stopping\n"
            projects = self._scan()
            g = self.groups[body["run"]] if body["run"] < len(self.groups) else None
            r = g.rounds[body["round"]] if g and body["round"] < len(g.rounds) else None
            if r is None or r.finished:
                return 409, b"that round is no longer open here; reload the page\n"
            if body["park"] and _project_in(str(r.path), _roots(projects)) is None:  # a look would drop the entry
                return 409, b"that round is in no project the inbox knows, so it cannot be parked\n"
            data = self._parked_file()
            if data is None:
                return 409, b"parked.json in the Signoff home is not the inbox's; move it away\n"
            rounds = data.get("rounds", [])
            path = str(r.path)
            # Parked while its entry has no back time. Back keeps the entry and adds one: a round no live run lists
            # then waits as a Run ended row for RUNS_MAX_AGE after it; a Park again takes it off.
            entry = next((e for e in rounds if e["path"] == path), None)
            if body["park"] == (entry is not None and "back" not in entry):
                return 200, b'{"ok": true}'  # as asked already: nothing to write
            now = utc_now()
            if entry is None:
                rounds = rounds + [{"path": path, "at": now}]
            else:
                kept = {k: v for k, v in entry.items() if k != "back"}
                rounds = [(dict(kept, at=now) if body["park"] else dict(entry, back=now)) if e is entry else e
                          for e in rounds]
            text = json.dumps(dict(data, schema=1, rounds=rounds), indent=2) + "\n"
            # Entries of rounds done or gone count too (nothing prunes the file yet), so the text says how to start
            # over. Back adds no entry, so only the size can refuse it.
            if len(rounds) > MAX_PARKED and entry is None or len(text) > MAX_PARKED_FILE:
                return 409, (b"parked.json is full (rounds answered or gone since stay in it); move it out of the "
                             b"Signoff home to start over\n")
            try:
                write_atomic(self.home / "parked.json", text)
            except OSError as e:
                return 500, ("could not write parked.json (%s)\n" % (e.strerror or type(e).__name__)).encode("utf-8")
            self.refresh_state(back=not body["park"])  # the count now: the page that sent this reloads to it
        return 200, b'{"ok": true}'

    def _sent(self, g, r):
        if r.saved_at:
            self.sent[str(r.answers_path)] = dict(_who(g.entry.get("project"), g.name), title=r.decisions["title"],
                                                   saved=r.saved_at)

    def _open_round(self, path):
        """Whether a round file is there with no outcome file and no answers file that says done: an orphan round
        needs all three before the inbox reads it in (AD-24)."""
        try:
            st = path.stat()
        except OSError:
            return False
        # The user's own file only: a folder gone and made again by another user in a shared place gets no answers.
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() \
                or path.with_name(path.stem + ".outcome.json").exists():
            return False
        answers = answers_path_for(path)
        try:
            st = answers.stat()
        except OSError:
            return True
        # shortcut: the cache keeps every answers path the look met while the inbox lives; prune it if a home
        # gathers thousands of runs.
        k = (st.st_mtime_ns, st.st_size)
        if self.done_cache.get(answers, (None,))[0] != k:
            data = _json_object(_read_file(answers, MAX_ROUND_BODY))
            self.done_cache[answers] = (k, bool(data) and data.get("done") is True)
        return not self.done_cache[answers][1]

    def _scan(self):
        """Match the groups to the runs that live now: a new run's rounds are read in (and locked), a run that ended
        or died lets go of its rounds' locks. Orphan rounds (AD-24: open, listed by a recent project run that is gone
        and by no live run) stay in the group they were in, else go in a group of the newest gone run listing them;
        a parked round no recent run lists is an orphan round of its own. Returns the projects."""
        live, gone = {}, []  # (folder, rounds) -> the live runs on them, oldest first; the project runs gone
        folder = self.home / "runs"
        for path in sorted(folder.glob("[!.]*.json")) if folder.is_dir() else ():
            e = _read_json(path) if _recent(path, RUNS_MAX_AGE) else None
            if not _entry_ok(e):
                continue
            # shortcut: a live pid is taken as the run's; a pid reused by another process keeps a killed run listed
            # until its entry is RUNS_MAX_AGE old. Compare `started` with the process's start time if that bites.
            if "ended" not in e and _alive(e["pid"]):
                live.setdefault((e["folder"], tuple(e["rounds"])), []).append(e)
            elif e["project"] is not None:
                gone.append(e)
        for runs in live.values():
            runs.sort(key=lambda e: e["started"])
        listed = {(f, n) for f, names in live for n in names}
        owner = {}  # (folder, round) of each orphan round -> the newest gone run that lists it
        for e in sorted(gone, key=lambda e: e["started"]):
            for n in e["rounds"]:
                if (e["folder"], n) not in listed and self._open_round(Path(e["folder"]) / n):
                    owner[e["folder"], n] = e
        projects = self._projects()
        parked = self._read_parked(projects)
        self.parked = {p: at for p, (at, back, _) in parked.items() if back is None}
        for p, (at, back, pid) in sorted(parked.items()):
            f, n = os.path.split(p)
            # A parked round stays until Back, also when no recent run lists it: an orphan of its own, by its path.
            # Put back, it waits as one for RUNS_MAX_AGE after Back, as a gone run's orphan round would.
            if back is not None and time.time() - (_epoch(back) or 0) > RUNS_MAX_AGE:
                continue
            if (f, n) not in listed and (f, n) not in owner and self._open_round(Path(p)):
                owner[f, n] = {"run": "parked:" + p, "project": pid, "folder": f, "rounds": [n],
                               "started": back or at or "1970-01-01T00:00:00Z"}
        for key, n in list(self.group_index.items()):
            g = self.groups[n]
            # An orphan group outlives its run, so it is read again once a round file changed or went: a Done there
            # must be for the version on disk, which a new run waits on. shortcut: by the file's time, so an edit
            # that keeps it (cp -p) is missed; compare the hash Round keeps if that bites.
            same = tuple(_mtime(Path(key[0]) / k) for k in key[1]) == g.stamp
            if key in live and (same or not g.orphan):
                g.orphan = False  # a live run on the same rounds goes on with the group, its number and its tabs
                continue
            g.check_disk()
            left = [k for k, r in zip(key[1], g.rounds) if not r.finished]
            if same and key not in live and left and all((key[0], k) in owner for k in left):
                g.orphan = True
                if not g.writable and not self.closed.is_set():  # a request still running at close() locks nothing
                    g.take_locks()
                continue
            runs = [e for e in gone if (e["folder"], tuple(e["rounds"])) == key]
            ran = max(runs, key=lambda e: e["started"], default=None)
            # A run that handed over while the newest lived (two waiters at once) did it for these answers too; one
            # that ended before the newest started handed over other ones (the times compare as text: one format).
            if ran and any("ended" in e and type(e.get("exit")) is int and e["exit"] == EXIT_DONE
                           and isinstance(e["ended"], str) and e["ended"] >= ran["started"] for e in runs):
                ran = None
            # Every round done and the newest run of them stopped without handing the answers over (a waiter stopped
            # just after a Done here, rounds finished from outside after the run ended or timed out): the inbox does,
            # once. Exit 1 never: a reused start link's answers are for the agent to doubt, not to act on.
            if not left and not g.handed and ran and ("ended" not in ran or (
                    type(ran.get("exit")) is int and ran["exit"] in (EXIT_TIMEOUT, 130))):
                try:
                    g.hand_over()
                except OSError as err:
                    print("signoff inbox: cannot hand over the answers of %s (%s)" % (g.folder.name,
                                                                                     err.strerror or err),
                          file=sys.stderr, flush=True)
            for r in g.rounds:
                if r.finished:
                    self._sent(g, r)
            g.gone = True
            g.release()
            self.groups[n] = None
            del self.group_index[key]

        def add(key, runs, orphan=False):
            e, folder = runs[0], Path(key[0])
            tried = ({x["run"] for x in runs}, tuple(_mtime(folder / n) for n in key[1]))
            failed = self.failed.get(key)
            if self.closed.is_set() or (failed and tried[0] <= failed[0] and tried[1] == failed[1]):
                return  # said once; a new run on these rounds, or a round file changed since, tries again
            name = (projects.get(e.get("project")) or {}).get("name") or project_name(folder) or folder.name
            try:
                g = _Group(e, [Round(folder / n) for n in key[1]], name, self.round_locks)
            except (DecisionsError, OSError) as err:
                self.failed[key] = tried
                print("signoff inbox: cannot show %s: %s" % (folder, err), file=sys.stderr, flush=True)
                return
            g.orphan, g.stamp, g.home, g.look = orphan, tried[1], self.home, self._look
            self.group_index[key] = len(self.groups)
            self.groups.append(g)

        for key, runs in sorted(live.items(), key=lambda kv: kv[1][0]["started"]):
            if key in self.group_index:
                g = self.groups[self.group_index[key]]
                if not g.writable and not self.closed.is_set():  # a request still running at close() locks nothing
                    g.take_locks()
                g.check_disk()
                continue
            add(key, runs)
        # A round in a group already joins no other. shortcut: by the folder as runs/ spells it (register_run writes
        # it resolved), so a folder reached through a link may show a round twice; compare real paths if that bites.
        claimed = {(k[0], n) for k in self.group_index for n in k[1]}
        found = {}  # gone run -> its orphan rounds in no group yet
        for (f, n), e in owner.items():
            if (f, n) not in claimed:
                found.setdefault(e["run"], (e, set()))[1].add(n)
        for e, names in sorted(found.values(), key=lambda x: x[0]["started"]):
            add((e["folder"], tuple(n for n in e["rounds"] if n in names)), [e], orphan=True)
        return projects

    def view(self):
        """What the inbox page gets: the rows waiting on the user (open rounds, oldest first, then todo items marked
        waiting), the rounds answered lately with what their session did since, each project's open todo items, and
        the parked rounds (out of the count). Names, never paths."""
        with self.scan_lock:
            projects = self._scan()
            found = self._sessions()
            sessions = {}
            for s in found:
                sessions.setdefault(s["project"], []).append((s["at"], s["state"]))
            waiting, listed, opened, parked = [], [], {}, []
            for n, g in sorted([(n, g) for n, g in enumerate(self.groups) if g], key=lambda x: x[1].entry["started"]):
                for ri, r in enumerate(g.rounds):
                    if r.finished:
                        self._sent(g, r)
                    else:
                        row = dict(_who(g.entry.get("project"), g.name), kind="round", group=n, round=ri,
                                   title=r.decisions["title"], tab=r.decisions.get("tab"),
                                   since=g.entry["started"], opened=r.opened, held=not g.writable,
                                   orphan=g.orphan,
                                   **({"context": _one_line(_unpathed(g, r.decisions["context"]),
                                                            MAX_CONTEXT)} if "context" in r.decisions else {}),
                                   items=[it["title"][:MAX_ROW_TEXT] for it in r.decisions["items"][:MAX_ROW_ITEMS]])
                        if str(r.path) in self.parked:  # counted nowhere, until Back
                            parked.append(dict(row, since=self.parked[str(r.path)]))
                        else:
                            waiting.append(row)
                        # What a todo item may name to link here: its topic folder and file, in its own project.
                        opened.setdefault((g.entry.get("project"), r.path.parent.name + "/" + r.path.name),
                                          "run/%d/" % n)
            for pid, p in sorted(projects.items(), key=lambda kv: kv[1]["name"].lower()):
                path, file, bad = project_todo(p)
                if file is None:
                    continue
                todos, error = self._todos(path) if path else ([], bad)
                who, asked = _who(pid, p["name"]), self._requests(pid)
                # An item has no time of its own: the list shows and sorts on when its file last changed.
                changed = datetime.datetime.fromtimestamp((_mtime(path) or 0) / 1e9, datetime.timezone.utc
                                                          ).strftime("%Y-%m-%dT%H:%M:%SZ") if path else None

                def row(t):  # a chain as the page gets it: ref is where a message goes; the requests of all its refs
                    shown = {"title": t["title"], "body": t["body"], "ref": t["target"], "steps": t["steps"],
                             "park": bool(t["waits"]),  # Later goes to ref: only a marked item's
                             "requests": sorted((dict(q, later=k == "later") for ref in set(t["refs"])
                                                 for k in ("todo", "later") for q in asked.get((k, ref), [])),
                                                key=lambda q: q["at"])}
                    link = next((opened[pid, x] for x in t["rounds"] if (pid, x) in opened), None)
                    return dict(shown, round=link) if link else shown
                waiting += [dict(who, kind="todo", waiting=t["waiting"], since=changed, **row(t))
                            for t in todos if t["waiting"]]
                listed.append(dict(who, id=pid, file=file, error=error, todos=[row(t) for t in todos if not t["waiting"]]))
            asking, idle = self._live(found, projects)
            waiting += asking
            sent = []
            for key, s in sorted(self.sent.items(), key=lambda kv: kv[1]["saved"], reverse=True):
                at = _epoch(s["saved"])
                if at is None or time.time() - at > SENT_MAX_AGE:
                    del self.sent[key]  # in memory only: an answered round listed long enough
                    continue
                working = any(state == "working" and t >= s["saved"] for t, state in sessions.get(s["project"], ()))
                sent.append(dict(s, state="working" if working else "sent"))
        return {"mode": "inbox", "waiting": waiting, "sent": sent, "projects": listed, "idle": idle,
                "parked": sorted(parked, key=lambda w: w["since"] or "")}


def _inbox_serve():
    if not hasattr(socket, "AF_UNIX"):
        print("signoff: the inbox needs Unix sockets, which this Python has not (Windows); runs work as before",
              file=sys.stderr)
        return EXIT_ERROR
    home = inbox_home()
    port = env_port()
    if port is None:
        print(BAD_ENV_PORT, file=sys.stderr)
        return EXIT_ERROR
    try:
        server = InboxServer(home, port)
    except DecisionsError as e:
        print("signoff: %s" % e, file=sys.stderr)
        return EXIT_ERROR
    except OSError as e:
        print("signoff: cannot start the inbox (%s)" % e, file=sys.stderr)
        return EXIT_ERROR
    # No link here: this goes to the log. Links come from the control socket (signoff inbox open).
    print("signoff inbox: serving %s at %s (pid %d); signoff inbox open shows it, signoff inbox stop ends it"
          % (home, server.host, os.getpid()), file=sys.stderr, flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return 130
    finally:
        server.close()
    print("signoff inbox: stopped", file=sys.stderr, flush=True)
    return EXIT_DONE


def _inbox_open_args(args):
    """`inbox open [--no-open] [--run <n> --focus <round>/<item> --inbox <k>]`, each once, in any order: the
    numbers of a rows reply, or none of them."""
    no_open, given, args = False, {}, list(args)
    while args:
        arg = args.pop(0)
        if arg == "--no-open" and not no_open:
            no_open = True
        elif arg in ("--run", "--focus", "--inbox") and arg not in given and args:
            given[arg] = args.pop(0)
        else:
            given = None
            break
    if given == {}:
        return _inbox_open(no_open)
    number = r"(0|[1-9][0-9]{0,9})"
    found = given and len(given) == 3 and [re.fullmatch(number, given["--run"]),
                                           re.fullmatch(number + "/" + number, given["--focus"]),
                                           re.fullmatch(number, given["--inbox"])]
    target = found and all(found) and [int(found[0].group(1)), int(found[1].group(1)), int(found[1].group(2)),
                                       int(found[2].group(1))]
    if not target or max(target) >= MAX_NUMBER:
        print("signoff: usage: signoff inbox open [--no-open] [--run <n> --focus <round>/<item> --inbox <k>]",
              file=sys.stderr)
        return EXIT_ERROR
    return _inbox_open(no_open, dict(zip(TARGET, target)))


PORT_HELP = "a port number from 1024 to 65535, or 0 for a random one"
BAD_ENV_PORT = "signoff: SIGNOFF_PORT must be " + PORT_HELP


def port_number(text):
    """A port from --port or SIGNOFF_PORT (0: a random one), or None. Plain ASCII digits only: int() would also take
    ' 80', '+80', '8_0' and other scripts' digits. Not below 1024: a browser leaves :80 out of Host and Origin, so
    every page there would get 403, and the low ports are the system's."""
    if text.isascii() and text.isdigit() and len(text) <= 5 and (int(text) == 0 or 1024 <= int(text) <= 65535):
        return int(text)
    return None


def port_in_use(port):
    """Whether something accepts connections on 127.0.0.1:port. A listener on 0.0.0.0 counts: on macOS the
    SO_REUSEADDR bind would sit beside it and take its loopback traffic. A port in TIME_WAIT refuses, so a rerun
    still binds. Best effort: a listener that starts between this look and the bind is not seen."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def taken_port(port):
    """The error a run, --history and inbox serve stop on, the one line the bind's own refusal gives."""
    return OSError(errno.EADDRINUSE, "port %d: %s" % (port, os.strerror(errno.EADDRINUSE)))


def env_port():
    """SIGNOFF_PORT, read only by what binds (a run, --history, inbox serve): unset or empty is 0; None when it is
    not a port. The hooks never read it, so a bad value in a shell profile costs a session nothing."""
    return port_number(os.environ.get("SIGNOFF_PORT") or "0")


def ssh_without_browser():
    """In an SSH session a browser would open on the remote machine, away from the user: as with --no-open. A set
    BROWSER wins: VS Code's Remote-SSH terminal sets it to a helper that opens the link on the user's computer."""
    return bool((os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY")) and not os.environ.get("BROWSER"))


def _open_browser(url):
    """webbrowser.open with each of fds 1 and 2 that is not a terminal on /dev/null for the call. A browser it starts
    (xdg-open or gio on Linux) inherits them: on a pipe it would hold the caller's pipe open (the mod waits for its
    end) or write on a run's stdout, which holds answers paths only. A terminal stays, for a console browser. The swap is
    for the whole process: a line another thread writes to stderr during the call is lost."""
    import webbrowser  # here, not at the top: the hooks run in every session and never get here

    for stream in (sys.stdout, sys.stderr):
        if stream:
            stream.flush()
    saved, null = [], os.open(os.devnull, os.O_WRONLY)
    try:
        for fd in (1, 2):
            if not os.isatty(fd):
                try:
                    saved.append((fd, os.dup(fd)))  # not inheritable: the browser never gets the copy
                except OSError:  # closed: nothing to inherit
                    continue
                os.dup2(null, fd)
        return webbrowser.open(url)
    finally:
        for fd, copy in saved:
            os.dup2(copy, fd)
            os.close(copy)
        os.close(null)


def _inbox_open(no_open, target=None):
    """Start the inbox when it does not run (detached, its output to logs/inbox.log in the home), then open a new
    start link from it in the browser, or print it on stderr with --no-open. With a target (a rows reply's numbers)
    its link goes to that item, or to the list (a new inbox has another number, so it gives the list)."""
    home = inbox_home()
    if not hasattr(socket, "AF_UNIX"):
        print("signoff: the inbox needs Unix sockets, which this Python has not (Windows)", file=sys.stderr)
        return EXIT_ERROR
    if not home.is_absolute():
        print("signoff: the Signoff home %s is not an absolute path" % home, file=sys.stderr)
        return EXIT_ERROR
    log = home / "logs" / "inbox.log"
    child = None
    if not inbox_alive(home):
        if env_port() is None:  # the inbox it starts would only say so in its log
            print(BAD_ENV_PORT, file=sys.stderr)
            return EXIT_ERROR
        import subprocess  # here, not at the top: the hooks run in every session and never get here

        try:
            home.mkdir(parents=True, mode=0o700, exist_ok=True)
            log.parent.mkdir(mode=0o700, exist_ok=True)
            fd = os.open(str(log), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "ab") as out:
                child = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "inbox", "serve"],
                                         stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True,
                                         start_new_session=True)  # not stopped with this command or its terminal
        except OSError as e:
            print("signoff: cannot start the inbox (%s)" % e, file=sys.stderr)
            return EXIT_ERROR
    deadline = time.monotonic() + 15
    while True:
        try:
            link = inbox_request(home, "open", **(target or {})).get("link")
            break
        except OSError:
            # The one it started has ended (a taken SIGNOFF_PORT, say) and none other came up: no use waiting.
            if child is not None and child.poll() is not None and not inbox_alive(home):
                print("signoff: the inbox did not start; see %s" % log, file=sys.stderr)
                return EXIT_ERROR
            if time.monotonic() > deadline:
                print("signoff: the inbox does not answer; see %s" % log, file=sys.stderr)
                return EXIT_ERROR
            time.sleep(0.1)
    if not isinstance(link, str) or not link.startswith("http://127.0.0.1:"):
        print("signoff: the inbox gave no link", file=sys.stderr)
        return EXIT_ERROR
    if no_open:
        print("signoff: open %s  (the link works once)" % link, file=sys.stderr, flush=True)
    elif not ssh_without_browser() and _open_browser(link):
        print("signoff: the inbox is open in your browser", file=sys.stderr, flush=True)
    else:
        print("signoff: no browser opened; open %s yourself (it works once)" % link, file=sys.stderr, flush=True)
    return EXIT_DONE


def _inbox_rows(cwd):
    """The mod's panel list for the project a folder belongs to, as one JSON line on stdout. It never starts an
    inbox: without one answering, exit 1 and nothing on stdout."""
    home = inbox_home()
    if not os.path.isabs(cwd) or not home.is_absolute():
        print("signoff: inbox rows needs an absolute folder and an absolute Signoff home", file=sys.stderr)
        return EXIT_ERROR
    root = project_root(cwd)
    reply = {"rounds": [], "more": 0}
    if root is not None:
        try:
            reply = inbox_request(home, "rows", project=project_id(root))
        except (OSError, AttributeError):  # AttributeError: no AF_UNIX
            print("signoff: no inbox is running", file=sys.stderr)
            return EXIT_ERROR
        if not isinstance(reply.get("rounds"), list):  # an error, said by its kind only
            print("signoff: the running inbox does not list rows; restart it", file=sys.stderr)
            return EXIT_ERROR
    # Only these keys: a folder outside any project gets no inbox number, as no inbox was asked.
    print(json.dumps({k: reply[k] for k in ("rounds", "more", "inbox") if k in reply}), flush=True)
    return EXIT_DONE


def _inbox_stop():
    try:
        inbox_request(inbox_home(), "stop")
    except (OSError, AttributeError):  # AttributeError: no AF_UNIX
        print("signoff: no inbox is running", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_DONE


def make_server(target, port=0):
    """A server for one decisions file, or for a folder of round-NN.json files, on 127.0.0.1 (port 0: a random
    one)."""
    return SignoffServer(target, port)


def make_history_server(root, since=None, budget=HISTORY_BUDGET, timeout=None, folder=DEFAULT_FOLDER, port=0):
    """A read-only server for every round in the decisions folder under a project root."""
    return HistoryServer(root, since, budget, timeout, folder, port)


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
    # The line below is the agent's cue to hand the user the link: over SSH too, though no browser is tried there.
    if not args.no_open and (ssh_without_browser() or not _open_browser(server.start_url)):  # False: headless, WSL
        print("signoff: no browser opened; give the user the link above (it works once)", file=sys.stderr, flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return "stopped"
    finally:
        server.server_close()
    if server.start_reused:
        print("signoff: the start link was used twice, so another program may have opened the page; stopped. To go "
              "on, run the same command again: it opens a new link and keeps the saved answers.",
              file=sys.stderr, flush=True)
        return "reused"
    return "done" if server.finished.is_set() else "timeout"


def _lock_time(home):
    try:
        return os.stat(str(home / "inbox.lock")).st_mtime_ns
    except OSError:
        return None


def _server_id(home):
    """The running inbox's id in inbox.json. An inbox writes it before its first touch of inbox.lock, so read after
    _lock_time it never pairs a new inbox's touch with the inbox before it."""
    return (_read_json(home / "inbox.json", 4096) or {}).get("server_id")


def _inbox_refused(rounds, home):
    """Whether the live inbox shows none of these open rounds: it locks every round of a run it takes in, and a
    round it refuses (one from before a key the round uses) gets no lock. Any lock held, a file system
    that cannot lock, or an inbox gone since: no, as the waiter then waits as before."""
    for r in rounds:
        try:
            fd = try_lock(r.path.with_name(r.path.name + ".lock"), create=False)
        except FileNotFoundError:
            continue
        except OSError:
            return False
        if fd is None:
            return False
        unlock(fd)  # held for a moment only: the inbox took its locks at the look that saw this run, or never
    return bool(rounds) and inbox_alive(home)


def _wait(rounds, home):
    """Waiter mode: the inbox serves the page and writes the answers, so this run only looks at its answers files,
    once a second, with no idle timeout. With the inbox's home (None when the run's entry could not be written, which
    no inbox can see), once inbox.lock's time has changed twice since the run noted itself, with the same inbox in
    inbox.json (the inbox touches it at each look, before it reads runs/, so a whole look has seen the entry), it
    checks once that the inbox took the rounds in. No look, no verdict: a slow inbox only makes it later. Returns
    'done', 'refused' or 'stopped'."""
    stamp, looks = (_lock_time(home), 0) if home else (None, None)
    owner = _server_id(home) if home else None
    try:
        while not all(r.finished for r in rounds):
            time.sleep(1.0)
            for r in rounds:
                _reread(r)
            if looks is not None:
                now, sid = _lock_time(home), _server_id(home)  # in this order: see _server_id
                if sid != owner:  # another inbox started since: only its own looks count
                    owner, stamp, looks = sid, now, 0
                elif now is not None and now != stamp:
                    stamp, looks = now, looks + 1
                if looks >= 2:
                    looks = None
                    if _inbox_refused([r for r in rounds if not r.finished], home):
                        return "refused"
    except KeyboardInterrupt:
        return "stopped"
    return "done"


def _stop_on_signals():
    """Make SIGTERM and SIGHUP stop this run as Ctrl+C does (on macOS and Linux: Windows stops a process with no
    signal it can catch), so a closed terminal, for one, still gets its entry ended, its locks freed and exit code
    130. Returns the handlers to put back, and a list: once it holds anything, the handler does nothing (the cleanup
    has begun). A signal already ignored (nohup) stays ignored; off
    the main thread (main() called by another program) nothing changes, as Python allows none."""
    stopping = []
    if threading.current_thread() is not threading.main_thread():
        return {}, stopping

    def stop(signum, frame):
        # Only the first of SIGTERM and SIGHUP raises, so a second (SIGHUP, then SIGTERM) does not cut the cleanup
        # short; a second Ctrl+C still does, as before.
        if not stopping:
            stopping.append(signum)
            raise KeyboardInterrupt

    sigs = [s for s in (signal.SIGTERM, getattr(signal, "SIGHUP", None))
            if s is not None and signal.getsignal(s) == signal.SIG_DFL]
    return {s: signal.signal(s, stop) for s in sigs}, stopping


def register_run(home, rounds, mode):
    """Write runs/<run>.json in the Signoff home, so the inbox knows which rounds this run waits on, and whether on
    its own page or as a waiter. Returns a function that marks the entry ended, with the exit code when given (0: its
    answers paths are on stdout), and whether the entry was written. A failed write is said on stderr. A run killed some other
    way, or whose paths could not reach stdout, leaves no ended: a reader checks pid and started."""
    folder = rounds[0].path.parent
    root = project_root(folder)
    entry = {"schema": 1, "run": secrets.token_hex(8), "project": project_id(root) if root else None,
             "folder": str(folder), "rounds": [r.path.name for r in rounds if not r.readonly], "mode": mode,
             "pid": os.getpid(), "started": utc_now()}
    path = home / "runs" / (entry["run"] + ".json")

    def write():
        try:
            path.parent.mkdir(mode=0o700, exist_ok=True)
            write_atomic(path, json.dumps(entry, indent=2) + "\n")
            return True
        except OSError as e:
            print("signoff: could not write %s (%s); the inbox will not see this run" % (path, e), file=sys.stderr,
                  flush=True)
            return False

    def end(code=None):
        entry["ended"] = utc_now()
        if code is not None:
            entry["exit"] = code
        write()

    return end, write()


# "working" comes from UserPromptSubmit alone, no PreToolUse: it also fires on the turn that wakes the agent when its
# background Signoff run exits (verified 2026-11-06, CLI 2.1.319, background-exit wake fires UserPromptSubmit).
HOOK_STATES = {"SessionStart": "idle", "UserPromptSubmit": "working", "Stop": "idle", "StopFailure": "idle",
               "SessionEnd": "ended"}
# A wait on the user is a Notification of this type, 6 s after a permission prompt or an AskUserQuestion that is still
# open (CLI 2.1.319, probes/notification-hook). Every event that writes a state ends it, and so does a tool's result;
# after a deny nothing comes, and the session does wait on the user until its next prompt.
WAIT_NOTICE = "permission_prompt"
HOOK_CLEARS = ("PostToolUse", "PostToolUseFailure")  # end a wait, leave the state as it was
WAITING_FLAG = "waiting.flag"  # in the home: a byte while some session may wait, else empty (hook.sh reads its size)


def _may_wait(s):
    """Whether a session file says its session waits on the user and may still live. No pid (Windows) counts: a
    stale "yes" costs a Python start at a tool call, a stale "no" would keep a row that is no longer true."""
    # shortcut: a reused pid keeps a killed session's wait (and the flag) on until its file is SESSIONS_MAX_AGE old;
    # compare the process's start time with the file's if that bites.
    pid = s.get("pid") if isinstance(s, dict) else None
    return (isinstance(s, dict) and s.get("waiting") == "permission" and s.get("state") != "ended"
            and (pid is None or (type(pid) is int and pid > 0 and _alive(pid))))


def _any_waits(home):
    folder = home / "sessions"
    return any(_may_wait(_read_json(p, 4096)) for p in (folder.glob("[!.]*.json") if folder.is_dir() else ())
               if _recent(p, SESSIONS_MAX_AGE))


def _flag_off(home):
    """Empty waiting.flag once no session may wait. Each empty write is followed by a look of its own, so a wait
    that began meanwhile (its flag written before ours) turns it back on: it never stays off under a waiting row."""
    flag = home / WAITING_FLAG
    try:
        if flag.stat().st_size == 0:
            return
    except OSError:
        return  # no flag: no session ever waited here
    if _any_waits(home):
        return
    write_atomic(flag, b"")
    if _any_waits(home):
        write_atomic(flag, b"1")


def inbox_hook():
    """The plugin's hook, run by Claude Code at these events in every session: note in the Signoff home which project
    a session runs in and what it last did, so the inbox shows "working" or "idle" from the session itself, never a
    guess, and whether it waits on the user (a permission prompt or a question). Nothing at all while the inbox is
    off. Of the hook's input it keeps the session id, the event, the notification's type and the project root (from
    cwd): the prompt, the transcript path, the messages and the tools' input and output are never written. Claude
    Code adds a SessionStart or UserPromptSubmit hook's stdout to the model's context, so it prints only the
    hand-over: at SessionStart, with no other live session of the project, its pending requests (_handover)."""
    home = inbox_home()
    if not inbox_on(home):
        return
    try:
        event = json.loads(sys.stdin.buffer.read())
    except ValueError:
        return
    if not isinstance(event, dict):
        return
    name, session, cwd = event.get("hook_event_name"), event.get("session_id"), event.get("cwd")
    # The session id becomes a file name, so only plain ids pass (Claude Code's are UUIDs).
    if (not isinstance(name, str) or name not in HOOK_STATES and name not in HOOK_CLEARS and name != "Notification"
            or not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session)
            or not isinstance(cwd, str) or not os.path.isabs(cwd)):
        return
    if name == "Notification" and event.get("notification_type") != WAIT_NOTICE:
        return  # hooks.json asks for permission prompts only; any other type is no wait
    root = project_root(cwd)
    if root is None:
        return
    pid = project_id(root)
    file = home / "sessions" / (session + ".json")
    old = _read_json(file, 4096) if name not in HOOK_STATES else None
    if name in HOOK_CLEARS and not (old and "waiting" in old):
        return  # a tool call with no wait to end: nothing is written
    for folder in ("projects", "sessions"):
        (home / folder).mkdir(mode=0o700, exist_ok=True)
    path = home / "projects" / (pid + ".json")
    if not path.exists():  # on any event: an inbox turned on mid-session still gets the project
        write_atomic(path, json.dumps({"schema": 1, "id": pid, "root": str(root), "name": root.name}, indent=2) + "\n")
    if name == "SessionStart" and event.get("source") == "compact":
        return  # a compaction happens mid-turn: the session is still what it was
    # hooks/hook.sh passes Claude Code's pid; called without it (directly, or on Windows): no pid, not a wrong one.
    claude = os.environ.get("SIGNOFF_CLAUDE_PID", "")
    s = {"schema": 1, "session": session, "project": pid, "pid": int(claude) if claude.isdecimal() else None,
         "state": HOOK_STATES.get(name, "working"), "at": utc_now()}
    if name not in HOOK_STATES and old and old.get("state") in HOOK_STATES.values() and isinstance(old.get("at"), str) \
            and SAVED_PATTERN.fullmatch(old["at"]):
        s["state"], s["at"] = old["state"], old["at"]  # a wait starts or ends mid-turn: the state stays
    # Claude Code's environment, inherited through hook.sh, says a session runs in the background (claude --bg), the
    # only kind `claude attach` reaches (CLI 2.1.319: ATTENDED 0 and a job folder there, 1 and none interactively).
    # ATTENDED decides when it is set: a claude started from a background session's shell inherits its job folder.
    attended = os.environ.get("CLAUDE_CODE_SESSION_ATTENDED")
    if attended == "0" or (attended in (None, "") and os.environ.get("CLAUDE_JOB_DIR")):
        s["bg"] = True
    if name == "Notification":
        s["waiting"], s["waiting_at"] = "permission", utc_now()
        write_atomic(home / WAITING_FLAG, b"1")  # first: no waiting session file without the flag on
    write_atomic(file, json.dumps(s, indent=2) + "\n")
    if name == "Notification":
        write_atomic(home / WAITING_FLAG, b"1")  # again: a clearing hook may have emptied it in between
    else:
        _flag_off(home)
    if name == "SessionStart" and not _session_elsewhere(home, pid, session, claude):
        _say(_handover(home, pid))


def _session_elsewhere(home, project, session, claude):
    """Whether another session of the project lives, so its mod gets new requests: its file is recent, it has
    not ended, and its Claude Code process is there and is not this one (a /clear starts a new session in it)."""
    folder = home / "sessions"
    for path in folder.glob("[!.]*.json") if folder.is_dir() else ():
        s = _read_json(path, 4096) if path.stem != session and _recent(path, SESSIONS_MAX_AGE) else None
        pid = s.get("pid") if s else None
        if (s and s.get("project") == project and s.get("state") != "ended" and type(pid) is int and pid > 0
                and str(pid) != claude and _alive(pid)):
            return True
    return False


def _handover(home, project):
    """The lines a new session gets for the project's pending requests (not marked done): the oldest, at most
    MAX_HANDOVER of them and MAX_HANDOVER_TEXT characters, then how many more wait. Oldest by file time, which is when
    the inbox wrote them."""
    folder = home / "requests" / project
    ids, done = request_ids(folder)
    left = sorted((_mtime(folder / (i + ".json")) or 0, i) for i in ids if i not in done)
    lines, size = [], 0
    while left and len(lines) < MAX_HANDOVER:
        r = read_request(folder / (left[0][1] + ".json"))
        line = request_line(r) if r else ""
        if lines and size + len(line) > MAX_HANDOVER_TEXT:
            break
        left.pop(0)
        if line:
            lines.append(line)
            size += len(line)
    if left:
        lines.append("Signoff inbox: %d more messages from the user wait for this project; they come at a later "
                     "session start, once these are done." % len(left))
    # A model takes hook output for someone else's words (seen 2026-11-07, haiku): it is asked to tell the user first.
    return [HANDOVER_HEAD] + lines if lines else []


def _inbox_requests(args):
    """`signoff inbox requests <folder> <project> <id>…`: the lines the mod hands its session for the requests it
    found new, as one JSON line. Each pending one's line as request_line() builds it, oldest first, within
    MAX_HANDOVER_TEXT (what that cuts is in neither list: the mod asks again at its next look); the ids done,
    unreadable or of a kind this plugin does not know are gone. Only when the folder (the mod's session root) is in
    that project: the mod picks it from the roots the hooks noted, which may not hold a nested one yet. It asks no
    inbox and writes
    nothing."""
    home, (cwd, project), ids = inbox_home(), (args[:2] + ["", ""])[:2], args[2:]
    if not (home.is_absolute() and os.path.isabs(cwd) and HEX16.fullmatch(project) and 0 < len(ids) <= MAX_HANDOVER
            and len(set(ids)) == len(ids) and all(HEX16.fullmatch(i) for i in ids)):
        print("signoff: inbox requests takes an absolute folder, a project id and 1 to %d request ids, each 16 hex "
              "digits and given once, and needs an absolute Signoff home" % MAX_HANDOVER, file=sys.stderr)
        return EXIT_ERROR
    root = project_root(cwd)
    if root is None or project_id(root) != project:
        print("signoff: inbox requests: that folder is not in that project", file=sys.stderr)
        return EXIT_ERROR
    folder = home / "requests" / project
    found, gone = [], []
    for i in ids:
        r = None if os.path.lexists(str(folder / (i + ".done"))) else read_request(folder / (i + ".json"))
        if r:
            found.append(r)
        else:
            gone.append(i)
    lines, size = [], 0
    for r in sorted(found, key=lambda r: (r["at"], r["id"])):
        line = request_line(r)
        if lines and size + len(line) > MAX_HANDOVER_TEXT:  # the first always goes, as in _handover
            break
        lines.append({"id": r["id"], "line": line})
        size += len(line)
    print(json.dumps({"requests": lines, "gone": gone}), flush=True)  # ASCII-escaped: no code page can change it
    return EXIT_DONE


def _inbox_done(rid):
    """`signoff inbox done <id>`: the agent marks a request handled with an empty <id>.done beside it."""
    home = inbox_home()
    found = (sorted(p for p in (home / "requests").glob("*/%s.json" % rid) if HEX16.fullmatch(p.parent.name))
             if home.is_absolute() and HEX16.fullmatch(rid) else [])
    if not found:
        print("signoff: no request %s in the Signoff inbox" % rid, file=sys.stderr)
        return EXIT_ERROR
    try:
        os.close(os.open(str(found[0].with_suffix(".done")),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600))
    except FileExistsError:
        pass  # marked before
    except OSError as e:
        print("signoff: cannot mark %s done (%s)" % (rid, e.strerror or e), file=sys.stderr)
        return EXIT_ERROR
    return EXIT_DONE


def plan_round(n):
    """signoff plan's round n: fixed texts around plan-NN.md, Approve or Ask for changes, and no recommendation."""
    return {"version": 1, "title": PLAN_TITLE, "tab": PLAN_TAB, "context": PLAN_CONTEXT, "lang": "en",
            "items": [{"id": "plan", "title": PLAN_QUESTION, "description": PLAN_DESCRIPTION, "select": "one",
                       "options": [{"id": i, "label": label} for i, label in PLAN_OPTIONS],
                       "blocking": True, "comment": True, "lines": True,
                       "attachments": [{"src": "plan-%02d.md" % n, "caption": PLAN_CAPTION, "open": True}]}]}


def _said_done(path):
    """Whether a round's answers file says done, as the inbox reads it for an orphan round."""
    data = _json_object(_read_file(answers_path_for(path), MAX_ROUND_BODY))
    return bool(data) and data.get("done") is True


def _waited_on(home, folder):
    """The round files in folder that a live run waits on: a recent runs/ entry with no ended and its pid alive."""
    names, runs = set(), home / "runs"
    for path in runs.glob("[!.]*.json") if runs.is_dir() else ():
        e = _read_json(path) if _recent(path, RUNS_MAX_AGE) else None
        if _entry_ok(e) and "ended" not in e and _alive(e["pid"]) and _same([e["folder"]], str(folder)):
            names.update(e["rounds"])
    return names


def _plan_round_bytes(n):
    return (json.dumps(plan_round(n), indent=2) + "\n").encode("utf-8")


def _plan_reusable(path, n, plan):
    """Round n as --resume may take it again, or None: its round file holds the fixed round's bytes and its plan
    file the plan's, both regular files read not through a link (POSIX), and it loads as a run loads it (its answers
    checked, and linked to this version when done)."""
    try:
        if read_regular(path, MAX_DECISIONS, nofollow=True) != _plan_round_bytes(n) \
                or read_regular(path.with_name("plan-%02d.md" % n), MAX_ATTACHMENT, nofollow=True) != plan:
            return None
        return Round(path)
    except (OSError, DecisionsError):
        return None


def _open_folder(path):
    """A descriptor of the folder at path, opened not through a link at its name, or None where a folder cannot be
    opened so. shortcut: on Windows (no dir_fd) the files are created by path after the earlier link check alone;
    the inbox does not run there, so signoff plan never gets that far but in tests."""
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_DIRECTORY"):
        return None
    return os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _create(fd, folder, name, data):
    """Create name in folder with data: never over a file, never through a link, inside the folder's descriptor
    when there is one."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    out = os.open(name, flags, 0o666, dir_fd=fd) if fd is not None else os.open(str(folder / name), flags, 0o666)
    with os.fdopen(out, "wb") as f:
        f.write(data)


def _close_round(fd, path, line):
    """Close an open round with an outcome file of Signoff's own (AD-24: no longer an orphan). Never over a file:
    one there already closed it."""
    record = {"log": PLAN_LOG, "line": line, "date": datetime.date.today().isoformat()}
    try:
        _create(fd, path.parent, path.stem + ".outcome.json", (json.dumps(record) + "\n").encode("utf-8"))
    except FileExistsError:
        pass


def _plan(argv):
    """`signoff plan --topic <t> --folder <f> [--resume]`, the plan on stdin: the mod's plan review (AD-29). Closes
    the topic's older open rounds, writes plan-NN.md and the fixed round-NN.json in <folder>/<topic>/, and waits on
    that one round beside the live inbox, as a waiter. Every check comes before the first write; each file is
    created, never written over."""
    def say(text):
        print("signoff: plan: " + text, file=sys.stderr, flush=True)
        return EXIT_ERROR

    opts, words = {}, list(argv)
    while words:
        w = words.pop(0)
        if w in opts or w not in ("--topic", "--folder", "--resume") or (w != "--resume" and not words):
            return say(PLAN_USAGE)
        opts[w] = True if w == "--resume" else words.pop(0)
    if "--topic" not in opts or not opts.get("--folder"):
        return say(PLAN_USAGE)
    if not PLAN_TOPIC.fullmatch(opts["--topic"]):
        return say("--topic must be a date, -plan- and 1 to 8 letters, digits or hyphens (YYYY-MM-DD-plan-id)")
    stream = getattr(sys.stdin, "buffer", None)
    raw = stream.read(MAX_DECISIONS + 1) if stream else b""
    try:
        plan = raw.decode("utf-8").rstrip(PLAN_TRIM).encode("utf-8") if len(raw) <= MAX_DECISIONS else None
    except UnicodeDecodeError:
        return say("the plan on stdin must be UTF-8 text")
    if not plan or len(plan) > MAX_ATTACHMENT:
        return say("the plan on stdin must be 1 byte to %d KiB once the white space at its end is cut"
                   % (MAX_ATTACHMENT >> 10))
    home = inbox_home()
    if not (inbox_on(home) and inbox_alive(home)):
        return say("no Signoff inbox is running, so the plan has nowhere to show; Claude Code's own dialog asks")
    folder = Path(opts["--folder"])
    if not folder.is_absolute():
        root = project_root(Path.cwd())
        if root is None:
            return say("a relative --folder needs a project around the current folder; give an absolute one")
        folder = root / folder
    topic = folder / opts["--topic"]
    if os.path.lexists(str(topic)) and (topic.is_symlink() or not topic.is_dir()):
        return say("%s must be a folder, not a link or a file" % topic)
    nums, rounds = [0], []
    try:
        for p in topic.iterdir() if topic.is_dir() else ():
            m = PLAN_NUMBERED.match(p.name)
            if m:
                nums.append(int(m.group(1)))
            m = ROUND_FILE.match(p.name)
            if m:
                rounds.append((int(m.group(1)), p))
    except OSError as e:
        return say("cannot read %s (%s)" % (topic, e.strerror or e))
    rounds.sort()

    def closed(p):
        return p.with_name(p.stem + ".outcome.json").exists()

    def refused(p):  # the inbox did not take the round in: nobody will answer it
        fd = None
        try:
            fd = _open_folder(p.parent)
            _close_round(fd, p, PLAN_NOT_SHOWN)
        except OSError as e:
            say("could not close %s (%s)" % (p.name, e.strerror or e))
        finally:
            if fd is not None:
                os.close(fd)

    if opts.get("--resume") and rounds:
        n, newest = rounds[-1]
        r = _plan_reusable(newest, n, plan)
        if r is not None and r.finished:
            print(r.answers_path, flush=True)
            return EXIT_DONE
        if r is not None and not closed(newest):
            return _run(argparse.Namespace(decisions=str(newest)), waiter=True,
                        on_refused=lambda: refused(newest))
    n = max(nums) + 1
    if n > 9999:
        return say("%s has no round number left" % topic)
    older = [p for _, p in rounds if not closed(p) and not _said_done(p)]
    held = _waited_on(home, topic.resolve()) if older else ()
    if any(p.name in held for p in older):
        return say("a plan review is still open in another process; answer it there, or stop that process first")
    path = topic / ("round-%02d.json" % n)
    try:
        topic.mkdir(parents=True, exist_ok=True)
    except OSError as e:  # a read-only decisions folder, say: no link
        return say("cannot write in %s (%s)" % (topic, e.strerror or e))
    try:
        fd = _open_folder(topic)  # the folder written in is this one, with no link swapped in since the check
    except OSError as e:
        return say("cannot open %s as a folder of its own, not a link (%s)" % (topic, e.strerror or e))
    try:
        for p in older:
            _close_round(fd, p, PLAN_REPLACED % n)
        _create(fd, topic, "plan-%02d.md" % n, plan)
        _create(fd, topic, path.name, _plan_round_bytes(n))
    except OSError as e:
        return say("cannot write in %s (%s)" % (topic, e.strerror or e))
    finally:
        if fd is not None:
            os.close(fd)
    return _run(argparse.Namespace(decisions=str(path)), waiter=True, on_refused=lambda: refused(path))


def _history(args):
    if args.folder and not (Path(args.history) / args.folder).is_dir():
        print("signoff: %s: not a folder" % (Path(args.history) / args.folder), file=sys.stderr)
        return EXIT_ERROR
    try:
        server = make_history_server(args.history, args.since, timeout=args.timeout or None,
                                     folder=args.folder or DEFAULT_FOLDER, port=args.port)
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
    if sys.platform == "win32":
        # A redirected stream gets the console's code page, which cannot hold every path; a non-English Windows also
        # localizes error text. Agents read both streams as UTF-8.
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    if sys.version_info < (3, 9):  # the file still compiles on 3.7, so this line can speak (test_cli.OldPython)
        if argv != ["inbox", "hook"]:  # a hook always exits 0 and prints nothing
            print("signoff: needs Python 3.9 or newer; this is Python %d.%d" % sys.version_info[:2], file=sys.stderr)
            return EXIT_ERROR
        return EXIT_DONE
    # Two words were never a valid run, so a decisions folder named "inbox" still runs.
    if argv == ["inbox", "home"]:
        home = inbox_home()
        if not home.is_absolute():  # inbox_on() takes it as off: an inbox there would meet no run
            print("signoff: the Signoff home %s is not an absolute path; check HOME, LOCALAPPDATA or SIGNOFF_INBOX_HOME"
                  % home, file=sys.stderr)
            return EXIT_ERROR
        print(home, flush=True)
        return EXIT_DONE
    if argv == ["inbox", "serve"]:
        return _inbox_serve()
    if argv[:2] == ["inbox", "open"]:
        return _inbox_open_args(argv[2:])
    if argv == ["inbox", "stop"]:
        return _inbox_stop()
    if argv == ["inbox", "hook"]:
        try:
            inbox_hook()
        except BaseException as e:  # a hook must never fail or hold up the session
            print("signoff: hook: %s" % e, file=sys.stderr, flush=True)
        return EXIT_DONE
    if argv[:2] == ["inbox", "requests"]:
        return _inbox_requests(argv[2:])
    if len(argv) == 3 and argv[:2] == ["inbox", "done"]:
        return _inbox_done(argv[2])
    if len(argv) == 3 and argv[:2] == ["inbox", "rows"]:
        return _inbox_rows(argv[2])
    # A run takes none of these (--folder only with --history, which takes no decisions folder), so a decisions folder
    # named "plan" (with options after it) still runs.
    if argv[:1] == ["plan"] and any(w in argv for w in ("--topic", "--folder", "--resume")):
        return _plan(argv[1:])
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
    parser.add_argument("--port", metavar="N", help="listen on this port of 127.0.0.1, for a tunnel "
                        "set up once (default: SIGNOFF_PORT, else 0 = a random port)")
    args = parser.parse_args(argv)
    if (args.decisions is None) == (args.history is None):
        parser.error("give a decisions file or folder, or --history ROOT")
    if args.since and not args.history:
        parser.error("--since goes with --history")
    if args.folder and not args.history:
        parser.error("--folder goes with --history")
    # Checked here, not by argparse, so a bad value is one line without the usage, as a bad SIGNOFF_PORT is.
    if args.port is not None:
        args.port, bad = port_number(args.port), "signoff: --port must be " + PORT_HELP
    else:
        args.port, bad = env_port(), BAD_ENV_PORT
    if args.port is None:
        print(bad, file=sys.stderr)
        return EXIT_ERROR
    if args.history:
        return _history(args)
    return _run(args)


def _run(args, waiter=False, on_refused=None):
    """A run on args.decisions: a page run, or a waiter while the inbox lives. waiter=True (signoff plan, which
    checked the inbox) is a waiter even if the inbox stops meanwhile, never a page run; on_refused is called when the
    inbox did not take the rounds in."""
    home = inbox_home()
    inbox = inbox_on(home)
    locks = []
    try:
        # While the inbox lives, it serves the page and is the one writer: this run only waits for the answers.
        waiter = waiter or inbox and inbox_alive(home)
        if waiter:
            rounds = load_rounds(args.decisions)
            # The inbox reads the pictures and boards when it takes the rounds in: a bad one fails here, as on a page.
            _Rounds()._read_media(rounds)
        else:
            server = make_server(args.decisions, args.port)
            rounds = server.rounds
    except DecisionsError as e:
        print("signoff: %s" % e, file=sys.stderr)
        return EXIT_ERROR
    except OSError as e:
        print("signoff: cannot start the server (%s)" % e, file=sys.stderr)
        return EXIT_ERROR
    if inbox and not waiter:
        try:
            locks = lock_rounds(rounds)  # held until this run ends, so the inbox only reads these rounds
        except DecisionsError as e:
            server.server_close()
            # The inbox may have started since the look above and locked these rounds (orphan rounds): wait on it.
            waiter = inbox_alive(home)
            if not waiter:
                print("signoff: %s" % e, file=sys.stderr)
                return EXIT_ERROR
    folder = rounds[0].path.parent
    try:
        ensure_gitignore(folder)
    except OSError as e:
        print("signoff: could not write .gitignore (%s); keep *.answers.json, *.answers.md and *.json.lock in %s "
              "out of git" % (e, folder), file=sys.stderr)
    end, noted = (lambda code=None: None), False
    if inbox:
        end, noted = register_run(home, rounds, "waiter" if waiter else "page")
    previous, stopping = _stop_on_signals()
    result = None  # an unexpected error ends the entry with no exit code, as before the key came
    try:
        if waiter:
            print("signoff: the inbox is open, so the questions are there; waiting for the answers in %s (Ctrl+C "
                  "stops it)" % folder, file=sys.stderr, flush=True)
            result = _wait(rounds, home if noted else None)  # no inbox sees an entry never written: no verdict
        else:
            def poll():  # an answers file another program finished with Done ends the run, as a Done on the page does
                while not server.finished.wait(1.0):
                    server.check_disk()

            threading.Thread(target=poll, daemon=True).start()
            result = _serve(server, args, "answers go to %s" % folder)
    except KeyboardInterrupt:  # one that came before _serve's or _wait's own loop
        result = "stopped"
    finally:
        stopping.append(None)
        if result != "done":  # a Done ends the entry only once its paths are out, below
            end({"reused": EXIT_ERROR, "refused": EXIT_ERROR, "timeout": EXIT_TIMEOUT, "stopped": 130}.get(result))
        if locks:
            server.lock.acquire()  # kept until exit: a save in flight ends, and none starts, before the inbox may write
        for fd in locks:
            unlock(fd)
        for s, handler in previous.items():
            signal.signal(s, handler)
    if result == "stopped":
        print("signoff: stopped; the answers so far are kept", file=sys.stderr)
        return 130
    if result == "refused":
        if on_refused:
            on_refused()
        print("signoff: the Signoff inbox that is running did not take this round in, so the user cannot see it; it "
              "is likely older than this Signoff. Restart it (signoff inbox stop, then signoff inbox open) and run "
              "the same command again", file=sys.stderr, flush=True)
        return EXIT_ERROR
    paths = [r.answers_path for r in rounds if not r.readonly]
    if result == "reused":
        print("signoff: the answers so far are kept, but read them before you trust them. If an answers file now says "
              "done: true, the first visitor pressed Done: check it, and move it away before running the same command "
              "again for a new link", file=sys.stderr, flush=True)
        return EXIT_ERROR
    if result == "done":
        for path in paths:
            print(path, flush=True)
        # Not before: a stdout nobody reads (BrokenPipeError) leaves no ended, as a killed run, so the inbox tells.
        end(EXIT_DONE)
        return EXIT_DONE
    paths = [p for p in paths if p.exists()]
    print("signoff: timed out after %g idle minutes; %s" % (args.timeout, "the answers so far are kept" if paths
                                                            else "nothing was answered"), file=sys.stderr, flush=True)
    for path in paths:
        print(path, flush=True)
    return EXIT_TIMEOUT


if __name__ == "__main__":
    sys.exit(main())
