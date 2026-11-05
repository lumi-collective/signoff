#!/usr/bin/env python3
"""End to end: the real server and the real page in headless Chrome, on any OS with Chrome.

    python3 tests/e2e_browser.py            (from anywhere; CHROME=<path> if Chrome is not found)

It copies rounds of examples/demo into a temporary folder, starts `signoff.py --no-open` on it, opens the
single-use start link in headless Chrome over the DevTools protocol, and drives the page with real mouse clicks.
First one round: a non-recommended pick and a comment, the summary block above Done quotes the comment and its
filter works, Private (marked by the agent on one item, ticked by the user on the other) hides the comment in the
summary and the Markdown copy, unticking the agent's mark survives a reload, one click on Done. A round done
earlier shows Private without the untick hint. An item with criteria shows its facts as chips; the Cards / Table
toggle shows the table, a pick there (by click and by number key) is saved and shows on the cards, and at 390 px
the table scrolls in its own box. Then two rounds: a non-recommended pick, both rounds accepted, the Summary's
filter counts, Done. Each run checks the exit code, stdout and the answers files.

Not named test_*.py, so `unittest discover -s tests` does not run it: it needs a browser. It proves the server
and the page together in a browser; it is not a Claude Code session. Standard library only, Python 3.9+.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "demo"
TOTAL_TIMEOUT = 240  # seconds for the whole run; a hang fails the job instead of eating minutes
WINDOWS = sys.platform == "win32"
PROCS = []  # everything to kill in every exit path
WORK = []  # the temporary folder


class E2EError(Exception):
    pass


def check(condition, message):
    if not condition:
        raise E2EError(message)


def find_chrome():
    candidates = [os.environ.get("CHROME", "")]
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(var):
            candidates.append(os.path.join(os.environ[var], "Google", "Chrome", "Application", "chrome.exe"))
    candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome",
                   "google-chrome-stable", "chromium", "chromium-browser", "chrome"]
    for c in candidates:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return shutil.which(c) or c
    raise E2EError("Chrome not found; set CHROME to its path")


def kill(proc):
    """Kill a process and its children (Chrome has many); never raises."""
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=20)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
        proc.wait(10)
    except Exception:
        pass


def cleanup():
    for p in PROCS:
        kill(p)
    for w in WORK:
        shutil.rmtree(str(w), ignore_errors=True)


def watchdog():
    print("FAIL: no result after %d s (hang); killing Chrome and the server" % TOTAL_TIMEOUT, file=sys.stderr, flush=True)
    cleanup()
    os._exit(1)


def spawn(argv, **kw):
    if not WINDOWS:
        kw["start_new_session"] = True  # own process group, so kill() takes the children too
    p = subprocess.Popen(argv, **kw)
    PROCS.append(p)
    return p


def pump(stream, sink):
    for line in stream:
        sink.append(line)


def wait_for(what, fn, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.1)
    raise E2EError("timed out waiting for " + what)


class Browser:
    """Just enough of the DevTools protocol: one page, commands and their results (as media/demo/record_page.py)."""

    def __init__(self, profile):
        argv = [find_chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                "--no-default-browser-check", "--lang=en-US", "--window-size=1280,900",
                "--user-data-dir=" + profile, "--remote-debugging-port=0", "about:blank"]
        if sys.platform.startswith("linux"):
            argv.insert(1, "--no-sandbox")  # CI containers and new Ubuntu block the sandbox; the page is our own, local
        self.proc = spawn(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        active = Path(profile) / "DevToolsActivePort"

        def port():
            if self.proc.poll() is not None:
                raise E2EError("Chrome exited before it listened (code %s)" % self.proc.returncode)
            try:
                return int(active.read_text().split()[0])
            except (OSError, ValueError, IndexError):
                return None
        self.port = wait_for("Chrome's DevTools port", port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        pages = json.load(opener.open("http://127.0.0.1:%d/json" % self.port, timeout=15))
        path = urllib.parse.urlparse([p for p in pages if p["type"] == "page"][0]["webSocketDebuggerUrl"]).path
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, self.port, key)).encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            check(chunk, "Chrome closed the DevTools socket")
            self.buf += chunk
        self.buf = self.buf.split(b"\r\n\r\n", 1)[1]
        self.mid = 0

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 20)
            check(chunk, "Chrome closed the DevTools socket")
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def _message(self):
        data = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            data += self._read(n)
            if b1 & 0x80:
                return json.loads(data)

    def call(self, method, **params):
        self.mid += 1
        payload = json.dumps({"id": self.mid, "method": method, "params": params}).encode()
        n, mask = len(payload), os.urandom(4)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
                                if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        while True:
            msg = self._message()
            if msg.get("id") == self.mid:
                check("error" not in msg, "DevTools %s failed: %s" % (method, msg.get("error")))
                return msg.get("result", {})

    def js(self, expression):
        r = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        check("exceptionDetails" not in r, "page script failed: %s" % r.get("exceptionDetails"))
        return r["result"].get("value")

    def center(self, selector):
        """Scroll the element into view and return where its middle is."""
        box = self.js("(function () { var e = document.querySelector(%s); if (!e) return null; "
                      "e.scrollIntoView({block: 'center'}); var r = e.getBoundingClientRect(); "
                      "return [r.x + r.width / 2, r.y + r.height / 2, r.width, r.height]; })()" % json.dumps(selector))
        check(box and box[2] > 0 and box[3] > 0, "no visible element %s" % selector)
        return box[0], box[1]

    def click(self, selector):
        """A real mouse move, press and release, so focus lands where it does for a person."""
        wait_for("%s on the page" % selector, lambda: self.js("!!document.querySelector(%s)" % json.dumps(selector)))
        x, y = self.center(selector)
        time.sleep(0.2)
        x, y = self.center(selector)  # again: scrolling may have moved a sticky bar over it
        hit = self.js("(function () { var e = document.querySelector(%s), h = document.elementFromPoint(%f, %f); "
                      "return !!h && (e === h || e.contains(h) || h.contains(e) || "
                      "(!!h.closest('label') && h.closest('label').contains(e))); })()" % (json.dumps(selector), x, y))
        check(hit, "something else covers %s at (%d, %d)" % (selector, x, y))
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left",
                      buttons=1 if kind == "mousePressed" else 0, clickCount=1)
        time.sleep(0.3)


def serve(target, profile):
    """Start `signoff.py --no-open` on target and open its start link in a new Chrome; return the pieces."""
    server = spawn([sys.executable, "-I", "-B", str(ROOT / "plugin" / "signoff" / "signoff.py"), "--no-open", "--timeout", "3",
                    str(target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                   encoding="utf-8", errors="replace")
    out, err = [], []
    for stream, sink in ((server.stdout, out), (server.stderr, err)):
        threading.Thread(target=pump, args=(stream, sink), daemon=True).start()

    def start_link():
        check(server.poll() is None, "Signoff exited early: %s" % "".join(err))
        return next((m.group(0) for m in (re.search(r"http://\S+", l) for l in err) if m), None)
    try:
        link = wait_for("the start link on stderr", start_link)
        browser = Browser(profile)
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        browser.call("Page.enable")
        browser.call("Page.navigate", url=link)
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    return server, out, err, browser


def finished(server, out, err, expected):
    """Signoff exited 0 and printed exactly the expected answers paths; return each file's answers."""
    try:
        code = server.wait(timeout=60)
    except subprocess.TimeoutExpired:
        raise E2EError("Signoff did not exit after Done; stderr: %s" % "".join(err))
    time.sleep(0.5)  # let the pump threads drain
    check(code == 0, "Signoff exited %s, expected 0; stderr: %s" % (code, "".join(err)))
    listed = [l.strip() for l in out if l.strip()]
    check(len(listed) == len(expected), "stdout should list %d answers paths and nothing else, got %r" % (len(expected), out))
    for line, want in zip(listed, expected):
        check(os.path.basename(line) == want.name and os.path.exists(line) and os.path.samefile(line, str(want)),
              "stdout line %r is not %s" % (line, want.name))
    return [json.loads(want.read_text(encoding="utf-8")) for want in expected]


def filter_labels(browser, holder):
    labels = browser.js("Array.prototype.map.call(document.querySelectorAll(%s), function (b) { return b.textContent; })"
                        % json.dumps(holder + " .summary-filter button"))
    return [l.replace(" ", " ") for l in labels]


def shown_rows(browser, holder):
    return browser.js("Array.prototype.filter.call(document.querySelectorAll(%s), function (li) { return !li.hidden; })"
                      ".map(function (li) { var q = li.querySelector('.summary-note'); return q ? q.textContent : ''; })"
                      % json.dumps(holder + " .summary-list li"))


def run_single(work):
    """One round: the summary block above Done quotes the comment, its filter works, and Done is one click."""
    topic = work / "2026-01-06-wording"
    topic.mkdir()
    target = topic / "decisions.json"
    data = json.loads((DEMO / "round-04.json").read_text(encoding="utf-8"))
    data["items"][0]["sensitive"] = True  # the agent marks the first item; the user marks the second
    target.write_text(json.dumps(data), encoding="utf-8")
    items = data["items"]
    option = next(k for k, o in enumerate(items[0]["options"]) if o["id"] != items[0]["recommended"])
    # About two lines at 1280 px, five or more at 390 px: the quote is cut only on the narrow screen.
    comment = ("Keep it short; the label sits in a narrow column, and a long one wraps under the switch. "
               "Two words would do, as long as they still say what gets synced.\n"
               "The second line of the comment, which a narrow screen wraps once more.")
    note = "#summary-block .summary-note"

    def toggle():
        return browser.js("(function () { var q = document.querySelector(%s), b = q.nextSibling; "
                          "return {hidden: b.hidden, open: q.classList.contains('open'), "
                          "expanded: b.getAttribute('aria-expanded'), cut: q.scrollHeight > q.clientHeight + 1, "
                          "lines: Math.round(q.scrollHeight / parseFloat(getComputedStyle(q).lineHeight))}; })()"
                          % json.dumps(note))

    server, out, err, browser = serve(target, str(work / "profile-single"))
    try:
        wait_for("the page", lambda: browser.js("!!document.getElementById('r0-i1-c')"))
        check(browser.js("document.getElementById('tabs').hidden"), "a single round should show no tabs")
        browser.click("#r0-i0-o%d" % option)
        browser.click("#r0-i1-c")
        browser.call("Input.insertText", text=comment)
        wait_for("the comment quoted in the summary block", lambda: comment in shown_rows(browser, "#summary-block"))
        labels = filter_labels(browser, "#summary-block")
        check(labels == ["All 2", "With comment 1", "Not the recommendation 1"], "summary filter shows %r" % labels)
        state = toggle()
        check(not state["cut"] and state["hidden"], "at 1280 px the quote fits, with no toggle: %r" % state)
        browser.click("#summary-block .summary-filter button:nth-of-type(2)")
        rows = shown_rows(browser, "#summary-block")
        check(rows == [comment], "With comment should show only the commented item, shows %r" % rows)
        browser.click("#summary-block .summary-filter button:nth-of-type(3)")
        rows = shown_rows(browser, "#summary-block")
        check(rows == [""], "Not the recommendation should show only the changed item, shows %r" % rows)

        # Narrow the screen while the quote is filtered out, then show it again: it is cut, and the toggle shows.
        browser.call("Emulation.setDeviceMetricsOverride", width=390, height=900, deviceScaleFactor=1, mobile=False)
        time.sleep(0.5)
        browser.click("#summary-block .summary-filter button:nth-of-type(2)")
        state = toggle()
        check(state["cut"] and state["lines"] >= 4, "at 390 px the quote should be cut from 4 or more lines: %r" % state)
        check(not state["hidden"] and state["expanded"] == "false", "the full-text toggle should show: %r" % state)
        browser.click(note + " + .note-more")
        state = toggle()
        check(state["open"] and state["expanded"] == "true" and not state["cut"],
              "after the toggle the quote should show in full: %r" % state)

        # Private: the agent's mark starts ticked, the user ticks the commented item, and the Summary hides the text.
        ticked = browser.js("[document.getElementById('r0-i0-p').checked, document.getElementById('r0-i1-p').checked, "
                            "document.querySelectorAll('.item .sens').length, "
                            "!!document.querySelector('.item[data-item] .sens + .crow #r0-i0-p')]")
        check(ticked == [True, False, 1, True], "Private should start as the agent marked it: %r" % ticked)
        browser.click("#r0-i1-p")
        wait_for("the Summary to hide the private comment", lambda: browser.js(
            "!document.querySelector('#summary-block .summary-note') && "
            "document.getElementById('summary-block').textContent.indexOf(%s) < 0" % json.dumps(comment[:40])))
        badges = browser.js("document.querySelectorAll('#summary-block .badge.private').length")
        check(badges == 2, "the Summary should show 2 private badges, shows %r" % badges)
        check(not browser.js("document.getElementById('r0-i1-pn').hidden"), "the private note should show")
        check(browser.js("!document.querySelector('#summary-block .summary-private ~ button')"),
              "the Summary should offer no way to show a private comment")

        # Other text on a private item stays out of the Summary too.
        other_text = "Our family photo folder"
        browser.click("#r0-i0-t")
        browser.call("Input.insertText", text=other_text)
        wait_for("the Summary to show Other as private", lambda: browser.js(
            "document.getElementById('summary-block').textContent.indexOf('Other: private') >= 0"))
        check(browser.js("document.getElementById('summary-block').textContent.indexOf(%s) < 0" % json.dumps(other_text)),
              "the Summary shows the private Other text")

        # Untick the agent's mark: after a reload the box stays empty, and the Summary quotes the comment again.
        own = "Short is fine here."
        browser.click("#r0-i0-c")
        browser.call("Input.insertText", text=own)
        browser.click("#r0-i0-p")
        answers_file = topic / "decisions.answers.json"

        def saved_unmarked():
            try:
                a = json.loads(answers_file.read_text(encoding="utf-8"))["answers"][items[0]["id"]]
            except (OSError, ValueError, KeyError):
                return False
            return a.get("comment") == own and "private" not in a
        wait_for("the unticked item saved without private", saved_unmarked)
        browser.call("Page.reload")
        wait_for("the page again", lambda: browser.js("!!document.getElementById('r0-i1-c')"))
        state = browser.js("[document.getElementById('r0-i0-p').checked, document.getElementById('r0-i1-p').checked, "
                           "Array.prototype.map.call(document.querySelectorAll('#summary-block .summary-note'), "
                           "function (q) { return q.textContent; }), "
                           "document.querySelectorAll('#summary-block .summary-list li')[0].querySelectorAll('.badge.private').length, "
                           "document.querySelectorAll('#summary-block .badge.private').length]")
        check(state == [False, True, [own], 0, 1], "after the reload the agent's mark should stay off: %r" % state)
        browser.click("#r0-i0-o%d" % option)  # back to the pick, so Other and its text are not sent
        browser.click("#done-block")  # one click, no acceptance step

        answers = finished(server, out, err, [topic / "decisions.answers.json"])[0]
        check(answers.get("done") is True, "decisions.answers.json: done is %r" % answers.get("done"))
        got = answers["answers"]
        check(got[items[0]["id"]]["choice"] == items[0]["options"][option]["id"], "the pick was not stored: %r" % got)
        check(got[items[1]["id"]]["comment"] == comment, "the comment was not stored: %r" % got)
        check("private" not in got[items[0]["id"]] and got[items[1]["id"]].get("private") is True,
              "only the second item should be stored private: %r" % got)
        md = (topic / "decisions.answers.md").read_text(encoding="utf-8")
        check("narrow column" not in md and "- Comment: (private)" in md and "- Comment: " + own in md,
              "the Markdown copy should hide the private comment and show the other:\n%s" % md)
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise


def run_readonly(work):
    """A round done in an earlier run shows its Private box as answered, without the agent's untick hint."""
    topic = work / "2026-01-07-private"
    topic.mkdir()
    data = json.loads((DEMO / "round-04.json").read_text(encoding="utf-8"))
    data["items"][0]["sensitive"] = True
    for name in ("round-01.json", "round-02.json"):
        (topic / name).write_text(json.dumps(data), encoding="utf-8")
    sha = hashlib.sha256((topic / "round-01.json").read_bytes()).hexdigest()
    first = data["items"][0]
    (topic / "round-01.answers.json").write_text(json.dumps({
        "version": 1, "done": True, "saved": "2026-01-07T10:00:00Z", "free_text": "",
        "decisions": {"file": "round-01.json", "sha256": sha},
        "answers": {first["id"]: {"choice": first["recommended"], "comment": "", "private": True}}}), encoding="utf-8")
    server, out, err, browser = serve(topic, str(work / "profile-readonly"))
    try:
        wait_for("the page", lambda: browser.js("!!document.getElementById('r1-i0-p')"))
        state = browser.js("[document.querySelectorAll('#panel-0 .sens').length, document.getElementById('r0-i0-p').checked, "
                           "document.getElementById('r0-i0-p').disabled, document.querySelectorAll('#panel-1 .sens').length]")
        check(state == [0, True, True, 1], "a done round should show Private without the hint: %r" % state)
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)


def run_table(work):
    """Criteria: chips on the cards, the Cards / Table toggle, a pick in the table is saved and shows on the cards."""
    topic = work / "2026-01-08-storage"
    topic.mkdir()
    target = topic / "decisions.json"
    data = json.loads((DEMO / "round-03.json").read_text(encoding="utf-8"))
    data["items"] = [it for it in data["items"] if it.get("criteria")]
    item = data["items"][0]
    ids = [o["id"] for o in item["options"]]
    rec = ids.index(item["recommended"])
    # The same item as checkboxes with Skip, to check that Skip and the table clear each other.
    many = dict(json.loads(json.dumps(item)), id="storage-many", select="many", skippable=True, recommended=[ids[rec]])
    data["items"].append(many)
    target.write_text(json.dumps(data), encoding="utf-8")
    option = next(k for k in range(len(ids)) if k != rec)
    key = next(k for k in range(len(ids)) if k not in (rec, option))  # the third option, picked with its number key
    answers_file = topic / "decisions.answers.json"
    first = '[data-item="%s"] ' % item["id"]

    def saved_choice(it=item):
        try:
            return json.loads(answers_file.read_text(encoding="utf-8"))["answers"][it["id"]]["choice"]
        except (OSError, ValueError, KeyError):
            return None

    def view():
        return browser.js("(function () { var w = document.querySelector('.cmp-wrap'), o = document.getElementById('r0-i0-o0'); "
                          "return {table: !!w && w.offsetParent !== null, cards: o.offsetParent !== null, "
                          "pressed: Array.prototype.map.call(document.querySelectorAll(%s), "
                          "function (b) { return b.getAttribute('aria-pressed'); })}; })()"
                          % json.dumps(first + ".view-bar [data-view]"))

    server, out, err, browser = serve(target, str(work / "profile-table"))
    try:
        wait_for("the page", lambda: browser.js("!!document.getElementById('r0-i0-o0')"))
        facts = sum(len(o.get("facts", {})) for o in item["options"])
        chips = browser.js("Array.prototype.map.call(document.querySelectorAll(%s), function (f) { "
                           "return f.textContent; })" % json.dumps(first + ".opt .facts .fact"))
        check(len(chips) == facts, "each fact should be a chip on its card: %r" % chips)
        # A tone is its sign plus its word for screen readers, never colour alone; chips go in criteria order.
        words = {"good": "(better)", "mid": "(middling)", "bad": "(worse)"}
        want = [words.get(o["facts"][c["id"]].get("tone")) for o in item["options"] for c in item["criteria"]
                if c["id"] in o.get("facts", {})]
        said = browser.js("Array.prototype.map.call(document.querySelectorAll(%s), function (f) { "
                          "var g = f.querySelector('.glyph'), w = f.querySelector('.visually-hidden'); "
                          "return [g ? g.textContent : null, w ? w.textContent.trim() : null]; })"
                          % json.dumps(first + ".opt .facts .fact"))
        check(any(want) and said == [[{"(better)": "▲", "(middling)": "●", "(worse)": "▼"}.get(w), w] for w in want],
              "each toned chip needs its sign and its word: %r" % said)
        check(view() == {"table": False, "cards": True, "pressed": ["true", "false"]}, "cards first: %r" % view())

        browser.click(first + ".view-bar [data-view=table]")
        check(view() == {"table": True, "cards": False, "pressed": ["false", "true"]}, "the table view: %r" % view())
        cells = browser.js("document.querySelectorAll(%s).length" % json.dumps(first + ".cmp tbody tr"))
        check(cells == len(item["criteria"]), "a row per criterion, found %r" % cells)
        check(browser.js("document.getElementById('r0-i0-x%d').checked" % rec), "the table starts at the recommendation")
        browser.click("#r0-i0-x%d" % option)
        wait_for("the pick in the table to be saved", lambda: saved_choice() == ids[option])
        picked = browser.js("Array.prototype.map.call(document.querySelectorAll(%s), function (c) { "
                            "return c.dataset.col; })" % json.dumps(first + ".cmp td.picked"))
        check(picked == [str(option)] * len(item["criteria"]), "the picked column should be marked: %r" % picked)

        # The number keys still pick, and focus stays in the table.
        browser.js("document.getElementById('r0-i0-x%d').focus()" % option)
        for kind in ("keyDown", "keyUp"):
            browser.call("Input.dispatchKeyEvent", type=kind, key=str(key + 1), text=str(key + 1) if kind == "keyDown" else "")
        state = browser.js("[document.getElementById('r0-i0-x%d').checked, document.getElementById('r0-i0-o%d').checked, "
                           "document.activeElement.id]" % (key, key))
        check(state == [True, True, "r0-i0-x%d" % key], "key %d should pick in both views: %r" % (key + 1, state))
        wait_for("the key's pick to be saved", lambda: saved_choice() == ids[key])
        browser.click("#r0-i0-x%d" % option)
        wait_for("the pick to be saved again", lambda: saved_choice() == ids[option])

        # On a phone the table scrolls in its own box; the page does not.
        browser.call("Emulation.setDeviceMetricsOverride", width=390, height=900, deviceScaleFactor=1, mobile=False)
        time.sleep(0.5)
        fit = "(function () { var w = document.querySelector('.cmp-wrap'), h = w.parentNode.querySelector('.scroll-hint'); " \
              "return [w.scrollWidth > w.clientWidth, document.documentElement.scrollWidth <= window.innerWidth, " \
              "h.offsetParent !== null && !/[0-9]/.test(h.textContent)]; })()"
        check(browser.js(fit) == [True, True, True],
              "at 390 px the table should scroll in its box, with the hint, not the page: %r" % browser.js(fit))
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        time.sleep(0.5)
        check(browser.js(fit) == [False, True, False], "at 1280 px the table fits, without the hint: %r" % browser.js(fit))

        browser.click(first + ".view-bar [data-view=cards]")
        check(view()["cards"] and not view()["table"], "back to the cards: %r" % view())
        check(browser.js("document.getElementById('r0-i0-o%d').checked" % option), "the cards should show the table's pick")

        # Checkboxes: a second box in the table adds to the choice; Skip clears the table; a box in the table clears Skip.
        def boxes():
            return browser.js("[Array.prototype.map.call(document.getElementsByName('r0-i1-x'), function (i) { return i.checked; }), "
                              "document.getElementById('r0-i1-skip').checked]")
        browser.click('[data-item="storage-many"] .view-bar [data-view=table]')
        browser.click("#r0-i1-x%d" % option)
        wait_for("two boxes saved", lambda: saved_choice(many) == [ids[k] for k in sorted((rec, option))])
        browser.click("#r0-i1-skip")
        wait_for("Skip saved alone", lambda: saved_choice(many) == ["_skip"])
        check(boxes() == [[False] * len(ids), True], "Skip should clear the table: %r" % boxes())
        browser.click("#r0-i1-x%d" % key)
        wait_for("a box in the table saved without Skip", lambda: saved_choice(many) == [ids[key]])
        check(boxes() == [[k == key for k in range(len(ids))], False], "a box should clear Skip: %r" % boxes())

        browser.click("#done-block")
        answers = finished(server, out, err, [answers_file])[0]
        check(answers.get("done") is True and answers["answers"][item["id"]]["choice"] == ids[option]
              and answers["answers"][many["id"]]["choice"] == [ids[key]], "the table's picks should be the answers: %r" % answers)
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise


def run():
    work = Path(tempfile.mkdtemp(prefix="signoff-e2e-"))
    WORK.append(work)
    run_single(work)
    run_readonly(work)
    run_table(work)
    topic = work / "2026-01-05-settings"
    topic.mkdir()
    shutil.copytree(str(DEMO / "mockups"), str(topic / "mockups"))
    shutil.copyfile(str(DEMO / "round-02.json"), str(topic / "round-01.json"))
    shutil.copyfile(str(DEMO / "round-04.json"), str(topic / "round-02.json"))
    rounds = [json.loads((topic / ("round-0%d.json" % n)).read_text(encoding="utf-8")) for n in (1, 2)]

    # The pick: the first single-choice item of round 1, and an option that is not the recommended one.
    items = rounds[0]["items"]
    index = next(i for i, it in enumerate(items) if it.get("select", "one") == "one")
    recommended = items[index]["recommended"]
    option = next(k for k, o in enumerate(items[index]["options"]) if o["id"] != recommended)
    pick_id, pick_item = items[index]["options"][option]["id"], items[index]["id"]

    server, out, err, browser = serve(topic, str(work / "profile"))
    try:
        # The page rendered: a tab per round plus the Summary.
        tabs = wait_for("the tabs", lambda: browser.js("document.querySelectorAll('#tabs [role=tab]').length") or None)
        check(tabs == 3, "expected 3 tabs (2 rounds and the Summary), found %s" % tabs)
        check(browser.js("document.getElementById('title').textContent") == rounds[0]["title"],
              "the page title is not round 1's title")

        browser.click("#r0-i%d-o%d" % (index, option))
        check(browser.js("document.getElementById('r0-i%d-o%d').checked" % (index, option)),
              "the picked option %s is not checked after the click" % pick_id)
        comment = "Date and tag are enough for now."
        browser.click("#r0-i0-c")
        browser.call("Input.insertText", text=comment)
        browser.click("#accept-0")
        wait_for("round 2 to open", lambda: browser.js("!document.getElementById('panel-1').hidden"))
        browser.click("#accept-1")
        wait_for("Done to be ready", lambda: browser.js("!document.getElementById('done').disabled"))
        # Both accepted: the Summary is open, its filter counts every item and the one changed pick.
        total = len(rounds[0]["items"]) + len(rounds[1]["items"])
        labels = wait_for("the Summary's filter", lambda: filter_labels(browser, "#panel-2") or None)
        check(labels == ["All %d" % total, "With comment 1", "Not the recommendation 1"], "summary filter shows %r" % labels)
        quotes = browser.js("Array.prototype.map.call(document.querySelectorAll('#panel-2 .summary-note'), "
                            "function (q) { return q.textContent; })")
        check(quotes == [comment], "the Summary should quote the comment, quotes %r" % quotes)
        browser.click("#done")

        expected = [topic / "round-01.answers.json", topic / "round-02.answers.json"]
        for n, (want, answers) in enumerate(zip(expected, finished(server, out, err, expected))):
            check(answers.get("done") is True, "%s: done is %r" % (want.name, answers.get("done")))
            check(answers.get("accepted") is True, "%s: accepted is %r" % (want.name, answers.get("accepted")))
            if n == 0:
                got = answers["answers"][pick_item]
                check(got["choice"] == pick_id and got["source"] == "user",
                      "%s: %s is %r, expected the picked %s from the user" % (want.name, pick_item, got, pick_id))
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise


def main():
    timer = threading.Timer(TOTAL_TIMEOUT, watchdog)
    timer.daemon = True
    timer.start()
    try:
        run()
    except E2EError as e:
        print("FAIL: %s" % e, file=sys.stderr)
        return 1
    finally:
        timer.cancel()
        cleanup()
    print("PASS: 1 round, comment quoted above Done, filter, Private hides it, one-click Done, exit 0; "
          "table view: chips, toggle, a pick in the table saved and shown on the cards, exit 0; "
          "2 rounds, a non-recommended pick, both accepted, Summary filter, Done, exit 0, 2 answers paths on stdout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
