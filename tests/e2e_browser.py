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
the table scrolls in its own box. An attachment starts closed, opens on a click and shows the file's text
exactly, with nothing rendered. The header names the project folder and the tab title starts with it; on the
--history page the search finds an answer's comment and free text but not a private comment, and the Appearance
picker previews in that tab only; in the inbox Ctrl+, opens it, the arrows preview, Esc goes back, and Enter keeps
the palette in prefs.json for the list and a round's page. The inbox list: the empty state with nothing waiting, the
sidebar's view and project filters, j/k and Enter, Focus (f, Esc), Compact kept across a reload, the list reloading
itself when it loses nothing (else a bar offers it), the sort of Waiting on you, Copy for inline code and for a whole
item, and the warning on a relative rm. Then two rounds: a non-recommended pick, both rounds accepted, the Summary's
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


STRAYS = []  # homes where an inbox outlived its test; main() fails on them once run() is through


def _signoff():
    here = str(ROOT / "plugin" / "signoff")
    if here not in sys.path:
        sys.path.insert(0, here)
    import signoff
    return signoff


def inbox_link(inbox, inbox_err, script, env, home):
    """A start link from `signoff inbox open`, or None while this test's own inbox does not hold its lock yet: an
    `open` before that starts a detached inbox of its own, which the test would then use and kill() never reach."""
    check(inbox.poll() is None, "the inbox exited: %s" % "".join(inbox_err))
    if not _signoff().inbox_alive(home):
        return None
    done = subprocess.run(script + ["inbox", "open", "--no-open"], env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8", timeout=30)
    m = re.search(r"http://\S+", done.stderr)
    return m.group(0) if done.returncode == 0 and m else None


def end_inbox(inbox, script, env, home):
    """Kill this test's inbox; one still holding the home's lock after that is another inbox the test started by
    mistake: it is stopped, and main() fails. Not raised here: this runs in a finally, and would hide the real error."""
    kill(inbox)
    if _signoff().inbox_alive(home):
        subprocess.run(script + ["inbox", "stop"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30)
        STRAYS.append(str(home))


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


def serve(target, profile, history=False, size=(1280, 900)):
    """Start `signoff.py --no-open` on target (or --history target) and open its start link in a new Chrome of the
    given window size; return the pieces."""
    server = spawn([sys.executable, "-I", "-B", str(ROOT / "plugin" / "signoff" / "signoff.py"), "--no-open", "--timeout", "3"]
                   + (["--history"] if history else []) + [str(target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   universal_newlines=True, encoding="utf-8", errors="replace")
    out, err = [], []
    for stream, sink in ((server.stdout, out), (server.stderr, err)):
        threading.Thread(target=pump, args=(stream, sink), daemon=True).start()

    def start_link():
        check(server.poll() is None, "Signoff exited early: %s" % "".join(err))
        return next((m.group(0) for m in (re.search(r"http://\S+", l) for l in err) if m), None)
    try:
        link = wait_for("the start link on stderr", start_link)
        browser = Browser(profile)
        browser.call("Emulation.setDeviceMetricsOverride", width=size[0], height=size[1], deviceScaleFactor=1,
                     mobile=False)
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


PALETTE = "document.documentElement.getAttribute('data-palette')"


def press(browser, key, code, vk, modifiers=0, text=""):
    """One key, down and up, as a keyboard sends it (modifiers: 2 is Ctrl)."""
    for kind in ("keyDown", "keyUp"):
        browser.call("Input.dispatchKeyEvent", type=kind, key=key, code=code, windowsVirtualKeyCode=vk,
                     modifiers=modifiers, text=text if kind == "keyDown" else "")


def filter_labels(browser, holder):
    labels = browser.js("Array.prototype.map.call(document.querySelectorAll(%s), function (b) { return b.textContent; })"
                        % json.dumps(holder + " .summary-filter button"))
    return [l.replace(" ", " ") for l in labels]


def shown_rows(browser, holder):
    return browser.js("Array.prototype.filter.call(document.querySelectorAll(%s), function (li) { return !li.hidden; })"
                      ".map(function (li) { var q = li.querySelector('.summary-note'); return q ? q.textContent : ''; })"
                      % json.dumps(holder + " .summary-list li"))


# A round's panel against the page's own data: every item's title, every option's label, the Recommended tags and
# the margin's recommendation, the checked inputs (the answer, else the recommendation on an open round), and text
# that reads on its card (4.5:1) in the page's mode. Returns what is wrong, or an empty list.
ROUND_CHECK = """(function (ri) {
  var data = JSON.parse(document.getElementById('signoff-data').textContent);
  var r = (data.rounds || data.entries)[ri], p = document.getElementById('panel-' + ri), bad = [];
  if (!p || p.hidden) return ['panel ' + ri + ' not shown'];
  function lum(c) {
    var v = c.match(/[\\d.]+/g).slice(0, 3).map(function (x) { x = x / 255; return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  }
  function ratio(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  r.decisions.items.forEach(function (it, n) {
    var card = p.querySelectorAll('.item[data-item]')[n], h = card && card.querySelector('h3');
    if (!h || h.textContent.indexOf(it.title) < 0 || !h.checkVisibility()) return bad.push('title ' + it.id);
    if (ratio(getComputedStyle(h).color, getComputedStyle(card).backgroundColor) < 4.5) bad.push('contrast ' + it.id);
    var rec = it.recommended === undefined ? [] : [].concat(it.recommended), labels = {};
    it.options.forEach(function (o, k) {
      var lab = document.querySelector('label[for="r' + ri + '-i' + n + '-o' + k + '"]');
      labels[o.id] = o.label;
      if (!lab || !card.contains(lab) || lab.textContent.indexOf(o.label) < 0) bad.push('option ' + it.id + '/' + o.id);
      else if (!!lab.querySelector('.tag') !== (rec.indexOf(o.id) >= 0)) bad.push('tag ' + it.id + '/' + o.id);
    });
    var m = card.querySelector('.margin .rec');
    if (rec.length && (!m || !m.checkVisibility() || m.textContent !== rec.map(function (id) { return labels[id]; }).join(', '))) {
      bad.push('margin ' + it.id);
    }
    var a = r.answers && r.answers.answers[it.id], want = a ? [].concat(a.choice === null ? [] : a.choice) : (r.readonly ? [] : rec);
    var got = Array.prototype.filter.call(document.getElementsByName('r' + ri + '-i' + n), function (i) { return i.checked; })
      .map(function (i) { return i.value; });
    if (got.slice().sort().join() !== want.slice().sort().join()) bad.push('answer ' + it.id + ': ' + got + ' / ' + want);
  });
  return bad;
})(%d)"""


def check_round(browser, ri, what):
    for mode in ("light", "dark"):
        browser.js("document.documentElement.setAttribute('data-mode', %s)" % json.dumps(mode))
        bad = browser.js(ROUND_CHECK % ri)
        check(bad == [], "%s, %s: %r" % (what, mode, bad))
    browser.js("document.documentElement.removeAttribute('data-mode')")


def run_old_rounds(work):
    """Every round in examples/: the demo's rounds as one run and decisions.json as another, and examples/history
    through --history: each item, option, recommendation and answer is on the page, in light and dark."""
    topic = work / "2026-01-04-demo"
    topic.mkdir()
    shutil.copytree(str(DEMO / "mockups"), str(topic / "mockups"))
    shutil.copytree(str(DEMO / "boards"), str(topic / "boards"))
    shutil.copytree(str(DEMO / "notes"), str(topic / "notes"))
    single = work / "2026-01-04-single"
    shutil.copytree(str(topic), str(single))
    names = sorted(p.name for p in DEMO.glob("round-*.json"))
    for name in names:
        shutil.copyfile(str(DEMO / name), str(topic / name))
    shutil.copyfile(str(DEMO / "decisions.json"), str(single / "decisions.json"))
    for target, count in ((topic, len(names)), (single / "decisions.json", 1)):
        server, out, err, browser = serve(target, str(work / ("profile-old-" + target.name)))
        try:
            wait_for("the page", lambda: browser.js("!!document.getElementById('panel-0')"))
            for ri in range(count):
                if count > 1:
                    browser.click("#tab-%d" % ri)
                check_round(browser, ri, "%s round %d" % (target.name, ri + 1))
        except E2EError:
            sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
            raise
        finally:
            kill(server)
            kill(browser.proc)
    root = work / "history-copy"
    shutil.copytree(str(ROOT / "examples" / "history"), str(root))
    server, out, err, browser = serve(root, str(work / "profile-old-history"), history=True)
    try:
        entries = wait_for("the list", lambda: browser.js(
            "(function () { var d = document.getElementById('signoff-data'); "
            "return !!document.getElementById('h-list') && JSON.parse(d.textContent).entries.length; })()") or None)
        for ri in range(entries):
            browser.click("#h-list li:nth-child(%d) button" % (ri + 1))
            if browser.js("!!JSON.parse(document.getElementById('signoff-data').textContent).entries[%d].decisions" % ri):
                check_round(browser, ri, "history entry %d" % (ri + 1))
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)


def run_phone(work):
    """A phone opens a round in Focus: one item, Next, the margin between the question and the options, Esc shows them
    all; a Hungarian round's labels are Hungarian, never undefined."""
    topic = work / "2026-01-03-phone"
    topic.mkdir()
    shutil.copytree(str(DEMO / "mockups"), str(topic / "mockups"))
    data = json.loads((DEMO / "round-02.json").read_text(encoding="utf-8"))
    data["lang"] = "hu"
    (topic / "decisions.json").write_text(json.dumps(data), encoding="utf-8")
    shown = ("Array.prototype.filter.call(document.querySelectorAll('#panel-0 .item[data-item]'), "
             "function (c) { return c.checkVisibility(); }).length")
    server, out, err, browser = serve(topic / "decisions.json", str(work / "profile-phone"), size=(390, 844))
    try:
        wait_for("the page", lambda: browser.js("!!document.getElementById('r0-i0-o0')"))
        state = browser.js("[document.body.classList.contains('focusing'), %s, document.querySelector('.ib-fbar span')"
                           ".textContent, document.querySelector('.tb-btn').getAttribute('aria-pressed')]" % shown)
        check(state == [True, 1, "1. tétel / %d" % len(data["items"]), "true"],
              "a phone should open in Focus on the first item: %r" % state)
        place = browser.js("(function () { var c = document.querySelector('.item.cur'), q = c.querySelector('.q')"
                           ".getBoundingClientRect(), m = c.querySelector('.margin').getBoundingClientRect(), o = c"
                           ".querySelector('.choices').getBoundingClientRect(); return [m.top >= q.bottom, "
                           "m.bottom <= o.top, m.left < q.right]; })()")
        check(place == [True, True, True], "on a phone the margin sits between the question and the options: %r" % place)
        text = browser.js("document.body.innerText")
        check("undefined" not in text and "Fókusz" in text and "Javasolt".upper() in text.upper(),
              "a Hungarian round should show Hungarian labels: %r" % text[:400])
        browser.click(".ib-fbar button:nth-of-type(2)")  # Next
        check(browser.js("document.querySelector('.item.cur') === document.querySelectorAll('#panel-0 .item')[1] && %s"
                         % shown) == 1, "Next should show the second item alone")
        press(browser, "Escape", "Escape", 27)
        check(browser.js("!document.body.classList.contains('focusing') && " + shown) == len(data["items"]),
              "Esc should leave Focus and show every item")
        check(browser.js("document.documentElement.scrollWidth <= innerWidth"), "nothing wider than the phone")
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)


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
        check(browser.js("document.getElementById('project').textContent") == work.name
              and browser.js("document.getElementById('project').checkVisibility()"),
              "the header should name the project folder %r" % work.name)
        check(browser.js("document.title") == work.name + " · " + data["title"], "the tab title should start with the "
              "project name: %r" % browser.js("document.title"))
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
        # Reloaded at 390 px, the page opens in Focus; Esc shows the whole round, the summary and its Done again.
        check(browser.js("document.body.classList.contains('focusing') && "
                         "!document.getElementById('done-block').checkVisibility()"), "a reload at 390 px opens in Focus")
        press(browser, "Escape", "Escape", 27)
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


def run_history(work):
    """--history: the header names the project, and the search finds an answer's comment but not a private one."""
    root = work / "hist-proj"
    old = root / ".signoff" / "2026-01-08-search"
    old.mkdir(parents=True)
    (root / ".signoff" / "2026-01-09-quiet").mkdir()
    data = json.loads((DEMO / "round-04.json").read_text(encoding="utf-8"))
    first, second = data["items"][0], data["items"][1]
    picture = [{"src": "mockups/list-filters.png"}]
    first.update(select="many", recommended=[first["recommended"]], marks=True, images=picture)  # a pick with Other
    second.update(other=True, marks=True, images=picture)
    for target in (old / "round-01.json", root / ".signoff" / "2026-01-09-quiet" / "round-01.json"):
        target.write_text(json.dumps(dict(data, title="Wording for %s" % target.parent.name)), encoding="utf-8")
    shutil.copytree(str(DEMO / "mockups"), str(old / "mockups"))
    box = [10, 10, 20, 20]
    (old / "round-01.answers.json").write_text(json.dumps({
        "version": 1, "done": True, "saved": "2026-01-08T10:00:00Z", "free_text": "a giraffe remark",
        "answers": {first["id"]: {"choice": ["cellular", "_other"], "other": "a pelican answer", "private": True,
                                  "comment": "walrus secret", "marks": [{"image": 0, "box": box, "comment": "otter mark"}]},
                    second["id"]: {"choice": "_other", "other": "a heron answer", "comment": "A zebra comment.",
                                   "marks": [{"image": 0, "box": box, "comment": "ibis mark"}]}}}), encoding="utf-8")
    server, out, err, browser = serve(root, str(work / "profile-history"), history=True)

    def rows():
        return browser.js("Array.prototype.filter.call(document.querySelectorAll('#h-list li'), "
                          "function (li) { return !li.hidden; }).length")

    def search(text):
        browser.js("(function () { var i = document.getElementById('h-text'); i.value = %s; "
                   "i.dispatchEvent(new Event('input')); })()" % json.dumps(text))
        return rows()
    try:
        wait_for("the list", lambda: rows() or None)
        check(browser.js("document.getElementById('project').textContent") == root.name
              and browser.js("document.getElementById('project').checkVisibility()"),
              "the header should name the project %r" % root.name)
        check(browser.js("document.title").startswith(root.name + " · "), "the tab title should start with the project name: %r"
              % browser.js("document.title"))
        check(rows() == 2, "both rounds should be listed first")
        browser.click("#h-text")
        browser.call("Input.insertText", text="ZEBRA")
        check(rows() == 1, "typing a comment's word, in any case, should leave the one round with it: %r" % rows())
        check(search("giraffe") == 1, "the search should find the free text")
        check(search("walrus") == 0 and browser.js("document.getElementById('h-count').textContent") == "No round matches.",
              "a private comment must not be searchable")
        check(search("pelican") == 0, "a private Other text must not be searchable")
        check(search("otter") == 0, "a private mark's comment must not be searchable")
        check(search("heron") == 1, "the Other text of an answer that is not private should be found")
        check(search("ibis") == 1, "the mark comment of an answer that is not private should be found")
        check(search("cellular") == 1, "the chosen option of a private answer is not private text")
        check(search("") == 2, "an empty search lists every round again")
        # Without the inbox the picker previews only: the brush opens it, it says so, and Enter keeps it in this tab.
        browser.click("#appearance-open")
        check(browser.js("document.getElementById('appearance').open && document.activeElement.getAttribute('role')")
              == "option", "the brush should open the picker on the selected palette")
        check(browser.js("document.getElementById('ap-note').textContent").startswith("This tab only"),
              "the picker should say the choice is kept only in the inbox")
        press(browser, "ArrowUp", "ArrowUp", 38)
        check(browser.js(PALETTE) == "catppuccin", "↑ on the first palette stays there")
        press(browser, "ArrowDown", "ArrowDown", 40)
        press(browser, "Enter", "Enter", 13, text="\r")
        check(browser.js(PALETTE) == "signoff" and not browser.js("document.getElementById('appearance').open")
              and browser.js("document.activeElement.id") == "appearance-open",
              "Enter should keep Signoff in this tab and give focus back to the brush")
        check(not os.path.exists(os.environ["SIGNOFF_INBOX_HOME"]), "a page without the inbox writes no prefs")
        # At 900 px the list is a drawer: the round fills the window, and the drawer opens on the round shown.
        browser.call("Emulation.setDeviceMetricsOverride", width=900, height=700, deviceScaleFactor=1, mobile=False)
        time.sleep(0.3)
        box = browser.js("(function () { var m = document.querySelector('main').getBoundingClientRect(); "
                         "return [m.left, m.width, document.getElementById('h-list').checkVisibility()]; })()")
        check(box == [0, 900, False], "at 900 px the round should fill the window, the list in a drawer: %r" % box)
        browser.click("header button.icon")
        check(browser.js("document.activeElement.getAttribute('aria-current')") == "true",
              "the drawer should open on the round shown")
        press(browser, "Escape", "Escape", 27)
        # In a short window only the palette list scrolls: the modes, Cancel and Keep stay inside the viewport.
        for width, height in ((1312, 682), (1280, 600)):
            browser.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1,
                         mobile=False)
            browser.click("#appearance-open")
            box = browser.js("(function () { function r(id) { var b = document.getElementById(id)"
                             ".getBoundingClientRect(); return b.top >= 0 && b.left >= 0 && b.bottom <= innerHeight "
                             "&& b.right <= innerWidth; } var d = document.getElementById('appearance')"
                             ".getBoundingClientRect(); return [r('ap-keep'), r('ap-cancel'), r('ap-modes'), "
                             "d.bottom <= innerHeight, document.getElementById('ap-keep').getBoundingClientRect()"
                             ".bottom <= d.bottom && document.getElementById('appearance').scrollHeight <= "
                             "document.getElementById('appearance').clientHeight, "
                             "document.activeElement.getAttribute('aria-selected'), "
                             "Array.prototype.every.call(document.querySelectorAll('.pi'), function (li) { "
                             "return li.scrollHeight <= li.clientHeight + 1; })]; })()")  # rows not squeezed
            check(box == [True, True, True, True, True, "true", True],
                  "at %dx%d the picker should fit the window with Keep in view: %r" % (width, height, box))
            press(browser, "Escape", "Escape", 27)
        # Shorter than the popover at its least (a phone held sideways): the popover scrolls, so Keep is reachable.
        browser.call("Emulation.setDeviceMetricsOverride", width=844, height=390, deviceScaleFactor=1, mobile=False)
        browser.click("#appearance-open")
        x, y = browser.center("#ap-title")  # a real wheel over the popover's head, outside the palette list
        browser.call("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=0, deltaY=1000)
        time.sleep(0.3)
        reach = browser.js("(function () { var d = document.getElementById('appearance'); "
                           "var k = document.getElementById('ap-keep').getBoundingClientRect(); "
                           "return k.bottom <= Math.min(innerHeight, d.getBoundingClientRect().bottom) && k.top >= 0; })()")
        check(reach, "in an 844x390 window Keep should scroll into view")
        press(browser, "Escape", "Escape", 27)
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        # Once Signoff has stopped (idle, Ctrl+C), a picture that fails to load says so instead of a silent gap.
        browser.js("Array.prototype.filter.call(document.querySelectorAll('#h-list li'), function (li) { return "
                   "li.textContent.indexOf('search') >= 0; })[0].querySelector('button, a').click()")  # its pictures
        wait_for("a picture of the round", lambda: browser.js("!!document.querySelector('.frame img')"))
        # While Signoff runs, a picture it cannot serve (404) is not taken for a stop.
        check(browser.js("(function () { var p = document.querySelector('.frame img'), was = p.src; "
                         "p.src = was.replace(/img\\/[0-9]+$/, 'img/999'); return p.src !== was; })()"),
              "the picture's URL should end in img/<n>")
        time.sleep(1.5)
        check(browser.js("document.getElementById('notice').textContent").startswith("Read only"),
              "a 404 picture must not say Signoff stopped")
        kill(server)
        server.wait(timeout=20)
        browser.js("(function () { var p = document.querySelector('.frame img'); p.src = p.src + '?again'; })()")
        wait_for("the notice that Signoff stopped", lambda: browser.js(
            "document.getElementById('notice').textContent").startswith("Signoff has stopped") or None)
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


def run_attachment(work):
    """An attachment shows closed, opens on a click, and holds the file's text exactly, nothing rendered."""
    topic = work / "2026-01-09-export"
    topic.mkdir()
    shutil.copytree(str(DEMO / "notes"), str(topic / "notes"))
    data = json.loads((DEMO / "round-03.json").read_text(encoding="utf-8"))
    data["items"] = [it for it in data["items"] if it.get("attachments")]
    target = topic / "decisions.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    item = data["items"][0]
    text = (topic / item["attachments"][0]["src"]).read_bytes().decode("utf-8")
    check("<b>" in text and text.startswith("# "), "the demo attachment should hold a <b> and a # heading")
    box = '[data-item="%s"] details.attach' % item["id"]

    def state():
        return browser.js("(function () { var d = document.querySelectorAll(%s), p = d[0] && d[0].querySelector('pre'); "
                          "return p && {count: d.length, open: d[0].open, shown: p.checkVisibility(), "
                          "text: p.textContent, children: p.children.length, label: d[0].querySelector('summary').textContent}; })()"
                          % json.dumps(box))

    server, out, err, browser = serve(target, str(work / "profile-attach"))
    try:
        closed = wait_for("the attachment", state)
        check(closed["count"] == 1 and not closed["open"] and not closed["shown"], "the attachment should start closed: %r"
              % {k: closed[k] for k in ("count", "open", "shown")})
        check(item["attachments"][0]["caption"] in closed["label"], "the summary should name it: %r" % closed["label"])
        browser.click(box + " > summary")
        opened = state()
        check(opened["open"] and opened["shown"], "a click should open the attachment")
        check(opened["text"] == text, "the attachment should be the file's text exactly")
        check(opened["children"] == 0 and browser.js("document.querySelectorAll('.attach b, .attach h1').length") == 0,
              "nothing in the attachment may render")
        browser.click("#done")
        finished(server, out, err, [topic / "decisions.answers.json"])
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)


RELEASE = "Release 1.0 for 2026-01-10-inbox / round-01"
INBOX_TODO = """# Todo

- [ ] (waiting on you: run the backup) Check <b>the backup</b> before the release
  ```bash
  tmutil startbackup --block && echo "$HOME" '<b>'
  ```
  Read [the guide](https://example.com/guide) first, not [this](javascript:alert(1)).
  <img src=x onerror="document.title='pwned'">
  - [x] stop the sync
  - [ ] check the log
- [ ] A plain todo item
  See [the draft](https://example.com/draft).

  Clean up with `rm -rf build`, not `rm -rf /tmp/build`.

  ```bash
  rm -rf build && ls
  ```

  ```bash
  rm -rf /tmp/build
  ```

  Also [**`rm x`**](https://example.com/rm).

  ```python
  del cache
  ```
"""


def run_inbox(work):
    """The inbox: first nothing waits, so the empty state shows, and its To do button and the sidebar filter the list.
    Then a waiting run's round and a todo item marked waiting on you. The item opens beside the list to its Markdown: the
    agent's HTML is text, only the https link is a link, and Copy puts the block's text on the clipboard exactly. A
    message typed on it becomes a request file for the project's session, and shows as sent. The round opens from the list in the same tab, and Done there wakes the waiting run (exit 0, its answers path). Then the OSV
    section: Scan this project adds the project to Uptick's scan list, and a finding goes to the agent as a request.
    Waiting on you is grouped by project under a summary line, the other open items closed behind a count row per
    project; a chain is one row with its steps, Later and Open round."""
    sys.path.insert(0, str(ROOT / "plugin" / "signoff"))
    import signoff
    home, root = work / "inbox-home", work / "inbox-project"
    topic = root / ".signoff" / "2026-01-10-inbox"
    topic.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "_bmad-output").mkdir()
    (root / "_bmad-output" / "todo.md").write_text(INBOX_TODO, encoding="utf-8")
    shutil.copyfile(str(DEMO / "round-04.json"), str(topic / "round-01.json"))
    pid = signoff.project_id(root.resolve())
    (home / "projects").mkdir(parents=True, mode=0o700)
    (home / "projects" / (pid + ".json")).write_text(json.dumps(
        {"schema": 1, "id": pid, "root": str(root.resolve()), "name": "inbox-project"}), encoding="utf-8")
    # Uptick's files, never the user's: a fresh scan of the project with one finding, and a scan list without it.
    cache, config = work / "xdg-cache", work / "xdg-config"
    (cache / "uptick").mkdir(parents=True)
    (config / "uptick").mkdir(parents=True)
    (cache / "uptick" / "vulns.json").write_text(json.dumps({"schema": 1, "targets": {}, "repos": {str(root.resolve()): {
        "at": time.time(), "tried": time.time(), "count": 1, "max": "7.5", "level": "high", "gaps": [], "error": "",
        "hint": "", "prev": None, "details": [{
            "name": "lodash", "version": "4.17.20", "ecosystem": "npm", "ids": ["GHSA-35jh-r3h4-6jhm"], "advisories": 1,
            "severity": "7.2", "level": "high", "fixed": "4.17.21", "sources": ["package-lock.json"]}]}}}),
        encoding="utf-8")
    other = work / "inbox-other"  # a second project, never scanned, with one todo item
    (other / "_bmad-output").mkdir(parents=True)
    (other / "_bmad-output" / "todo.md").write_text("- [ ] An item of the other project\n", encoding="utf-8")
    other_id = signoff.project_id(other.resolve())
    (home / "projects" / (other_id + ".json")).write_text(json.dumps(
        {"schema": 1, "id": other_id, "root": str(other.resolve()), "name": "inbox-other"}), encoding="utf-8")
    scan_list = config / "uptick" / "scan-repos.json"
    scan_list.write_text(json.dumps({"repos": ["/elsewhere"], "kept": True}), encoding="utf-8")
    env = dict(os.environ, SIGNOFF_INBOX_HOME=str(home), XDG_CACHE_HOME=str(cache), XDG_CONFIG_HOME=str(config))
    script = [sys.executable, "-I", "-B", str(ROOT / "plugin" / "signoff" / "signoff.py")]
    inbox_err = []
    inbox = spawn(script + ["inbox", "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                  universal_newlines=True, encoding="utf-8", errors="replace")
    threading.Thread(target=pump, args=(inbox.stderr, inbox_err), daemon=True).start()

    def link():
        return inbox_link(inbox, inbox_err, script, env, home)
    start = wait_for("a start link from the inbox", link)
    out, err = [], []
    waiter = None
    browser = Browser(str(work / "profile-inbox"))
    try:
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        browser.call("Page.enable")
        origin = start.split("/b/")[0]
        browser.call("Browser.grantPermissions", origin=origin,
                     permissions=["clipboardReadWrite", "clipboardSanitizedWrite"])
        # Nothing waits yet: the empty state stands in for the list, and its To do button shows the list.
        todo_file = root / "_bmad-output" / "todo.md"
        todo_file.write_text(INBOX_TODO.replace("(waiting on you: run the backup) ", ""), encoding="utf-8")
        browser.call("Page.navigate", url=start)
        zero = wait_for("the empty state", lambda: browser.js(
            "(function () { var z = document.querySelector('.ib-zero'); return z && !z.hidden && [z.querySelector('h2')"
            ".textContent, Array.prototype.map.call(z.querySelectorAll('button'), function (b) { return b.textContent; }),"
            " document.getElementById('panels').hidden, document.title]; })()"))
        check(zero == ["Nothing waits on you", ["To do (3)"], True, "Inbox"],
              "the empty state when nothing waits: %r" % zero)
        # Focus walks only the rows shown: the To do rows wait behind closed count rows, so from the empty state it
        # opens on the Vulnerabilities row, and the bar counts it alone; Esc goes back with focus on the Focus button.
        press(browser, "f", "KeyF", 70, text="f")
        walk = browser.js("[document.body.classList.contains('focusing'), document.activeElement.tagName,"
                          " document.querySelector('#ib-detail h2').textContent, document.querySelector('.ib-fbar span')"
                          ".textContent]")
        check(walk == [True, "H2", "Not scanned (2)", "1 of 1"], "f should walk only the rows shown: %r" % walk)
        press(browser, "Escape", "Escape", 27)
        check(browser.js("!document.querySelector('.ib-zero').hidden && document.activeElement.classList.contains('tb-btn')"
                         " && document.activeElement.getAttribute('aria-pressed') === 'false'"),
              "Esc should go back to the empty state, focus on the Focus button")
        browser.click(".ib-zero button")
        shown = browser.js("(function () { var s = Array.prototype.filter.call(document.querySelectorAll('#panels section'),"
                           " function (x) { return !x.hidden; }); return [s.length, s[0].querySelector('h2').textContent,"
                           " document.querySelector('.ib-zero').hidden, document.getElementById('intro').textContent,"
                           " Array.prototype.filter.call(document.querySelectorAll('.nav'), function (b) {"
                           " return b.getAttribute('aria-pressed') === 'true'; }).map(function (b) { return b.textContent; })]; })()")
        check(shown == [1, "To do 3by project", True, "Showing To do", ["To do 3"]],
              "To do (3) should show the To do rows only: %r" % shown)
        # Each project's other open items sit behind one count row, closed; the button lands on the first of them, and
        # a click shows that project's rows.
        groups = ("Array.prototype.map.call(document.querySelectorAll('.ib-gtoggle'), function (b) { return ["
                  "b.querySelector('.nl').textContent,"
                  " b.getAttribute('aria-expanded'), document.getElementById(b.getAttribute('aria-controls')).querySelectorAll("
                  "'.ib-row').length, document.getElementById(b.getAttribute('aria-controls')).offsetParent !== null]; })")
        check(browser.js(groups) == [["inbox-other: 1 more open", "false", 1, False],
                                     ["inbox-project: 2 more open", "false", 2, False]],
              "the To do rows should be closed per project: %r" % browser.js(groups))
        check(browser.js("document.activeElement === document.querySelector('.ib-gtoggle')"),
              "the To do button should land on the first count row")
        browser.click(".ib-group:nth-child(2) .ib-gtoggle")
        check(browser.js(groups)[1] == ["inbox-project: 2 more open", "true", 2, True],
              "a click should show the project's rows: %r" % browser.js(groups))
        browser.click(".ib-group:nth-child(2) .ib-gtoggle")
        check(browser.js(groups)[1][1:] == ["false", 2, False], "a second click closes them: %r" % browser.js(groups))
        # Now an item waits on you and a run waits on the inbox.
        todo_file.write_text(INBOX_TODO, encoding="utf-8")
        waiter = spawn(script + [str(topic), "--no-open"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, encoding="utf-8", errors="replace")
        for stream, sink in ((waiter.stdout, out), (waiter.stderr, err)):
            threading.Thread(target=pump, args=(stream, sink), daemon=True).start()
        wait_for("the run to wait on the inbox", lambda: any("inbox is open" in l for l in err))
        # Rows are ordered by when they last changed, a todo item by its file's time: touch the file so the item is
        # not older than the run that started after it was written.
        os.utime(str(todo_file), None)
        browser.call("Page.navigate", url=browser.js("location.href"))
        rows = wait_for("the waiting rows", lambda: browser.js(
            "Array.prototype.map.call(document.querySelectorAll('.ib-list')[0] ? "
            "document.querySelectorAll('.ib-list')[0].querySelectorAll('.ib-kind') : [], "
            "function (k) { return k.textContent; })") or None)
        check(rows == ["Question", "Waiting on you"], "Waiting on you should list the round, then the todo item: %r" % rows)
        check(browser.js("document.querySelector('.ib-main').getAttribute('aria-current') === 'true' && "
                         "document.querySelector('#ib-detail h2').textContent === "
                         "document.querySelector('.ib-main .ib-title').textContent"),
              "the first row should be open beside the list")
        # By default: what waits, under one heading per project, and one summary line; the item's steps counted.
        summary = browser.js("[document.getElementById('intro').textContent, Array.prototype.map.call("
                             "document.querySelectorAll('section.ib')[0].querySelectorAll('.ib-ghead .nl'), function (h) {"
                             " return h.textContent; }),"
                             " document.querySelectorAll('section.ib')[0].querySelector('button.ib-main .ib-text').textContent]")
        check(summary == ["2 waiting on you in 1 project", ["inbox-project"], "run the backup · 1 of 2 steps done"],
              "the grouped view and its summary: %r" % summary)
        # The sidebar filters by project: the other project's item, and the Not scanned row that names it.
        browser.js("Array.prototype.filter.call(document.querySelectorAll('.nav'), function (b) {"
                   " return b.querySelector('.nl').textContent === 'inbox-other'; })[0].setAttribute('data-e2e', 'other')")
        browser.click(".nav[data-e2e=other]")
        titles = browser.js("Array.prototype.filter.call(document.querySelectorAll('.ib-row'), function (r) {"
                            " return r.offsetParent; }).map(function (r) { return r.querySelector('.ib-title').textContent; })")
        check(titles == ["An item of the other project", "Not scanned (2)"], "the project filter: %r" % titles)
        browser.click(".nav[data-e2e=other]")  # a second click shows everything again
        check(browser.js("Array.prototype.every.call(document.querySelectorAll('#panels section'), function (s) {"
                         " return !s.hidden; }) && document.querySelector('.tb-btn').hidden"), "every row again")
        todo = "#panels section:first-of-type button.ib-main"
        browser.click(todo)
        body = browser.js("(function () { var b = document.querySelector('#ib-detail .ib-body'); return b && {"
                          "code: Array.prototype.map.call(b.querySelectorAll('pre code'), function (c) { return c.textContent; }),"
                          "links: Array.prototype.map.call(b.querySelectorAll('a'), function (a) { return [a.href, a.rel, a.target, a.textContent]; }),"
                          "bad: b.querySelectorAll('b, img, script, [onerror]').length, text: b.textContent,"
                          "tasks: Array.prototype.map.call(b.querySelectorAll('.md-check'), function (c) { return c.getAttribute('aria-label'); }),"
                          "title: document.title}; })()")
        check(body, "a click should open the todo item")
        command = 'tmutil startbackup --block && echo "$HOME" \'<b>\''
        check(body["code"] == [command], "the code block should hold the command exactly: %r" % body["code"])
        check(body["links"] == [["https://example.com/guide", "noopener noreferrer", "_blank", "the guide"]],
              "only the https link may be a link: %r" % body["links"])
        check(body["bad"] == 0 and "[this](javascript:alert(1))" in body["text"] and "<img src=x" in body["text"]
              and body["title"] == "(2) Inbox", "the agent's HTML must show as text, never run: %r" % body)
        check(body["tasks"] == ["done", "open"], "the nested checkboxes should show: %r" % body["tasks"])
        # The row's title is the item's first line only; the line after it opens the body, its link working there.
        plain = browser.js(
            "(function () { var t = Array.prototype.filter.call(document.querySelectorAll('.ib-title'), function (x) {"
            " return x.textContent.indexOf('A plain todo item') === 0; });"
            " if (t.length === 1) t[0].closest('button').setAttribute('data-e2e', 'plain');"
            " return t.map(function (x) { return x.textContent; }); })()")
        check(plain == ["A plain todo item"], "the row title should be the first line only: %r" % plain)
        check(browser.js("document.querySelector('button[data-e2e=plain]').offsetParent === null"),
              "the plain item should wait behind its project's count row")
        browser.click(".ib-group:nth-child(2) .ib-gtoggle")  # inbox-project: 1 more open
        browser.click("button[data-e2e=plain]")
        plain = browser.js(
            "(function () { var b = document.querySelector('#ib-detail .ib-body'); return b && "
            "document.querySelector('button[data-e2e=plain]').getAttribute('aria-current') === 'true' && "
            "[b.querySelector('p').textContent,"
            " Array.prototype.map.call(b.querySelectorAll('a'), function (a) { return [a.href, a.rel, a.textContent]; })]; })()")
        check(plain == ["See the draft.", [["https://example.com/draft", "noopener noreferrer", "the draft"],
                                           ["https://example.com/rm", "noopener noreferrer", "rm x"]]],
              "the opened item should start with the line after the title, its link working: %r" % plain)
        # Inline code gets a Copy button (the span's text exactly) and the whole item a Copy text. A command that
        # deletes with a relative path gets a warning line, in a block and inline; an absolute path none.
        def clipboard():
            got = browser.call("Runtime.evaluate", expression="navigator.clipboard.readText()", awaitPromise=True,
                               returnByValue=True)
            return got.get("result", {}).get("value")
        marks = browser.js(
            "(function () { var b = document.querySelector('#ib-detail .ib-body'); return {"
            "spans: Array.prototype.map.call(b.querySelectorAll('code.ic'), function (c) { return c.textContent; }),"
            "copies: b.querySelectorAll('button.ic-copy').length,"
            "inline: Array.prototype.map.call(b.querySelectorAll('.ic-warn'), function (w) { return w.textContent; }),"
            "blocks: Array.prototype.map.call(b.querySelectorAll('.md-code'), function (c) {"
            " var w = c.querySelector('.md-warn'); return [c.querySelector('pre').textContent, w && w.textContent]; })}; })()")
        warning = "⚠ Relative path: check the folder before running"
        # The span inside a link (`rm x`, bold, in a link) has no Copy and no warning: no button inside an <a>; a
        # `del` in a python block is no shell command, so no warning there.
        check(marks == {"spans": ["rm -rf build", "rm -rf /tmp/build", "rm x"], "copies": 2, "inline": [warning],
                        "blocks": [["rm -rf build && ls", warning], ["rm -rf /tmp/build", None], ["del cache", None]]},
              "a relative delete warns, an absolute one does not: %r" % marks)
        check(browser.js("document.querySelectorAll('#ib-detail .ib-body a button, #ib-detail .ib-body a .ic-warn').length")
              == 0, "no Copy or warning inside a link")
        browser.click("#ib-detail button.ic-copy")
        check(clipboard() == "rm -rf build", "the inline Copy should put the span's text on the clipboard: %r" % clipboard())
        browser.click("#ib-detail .ib-copytext")
        text = ("A plain todo item\n\nSee the draft (https://example.com/draft).\n\nClean up with rm -rf build, not "
                "rm -rf /tmp/build.\n\nrm -rf build && ls\n\nrm -rf /tmp/build\n\nAlso rm x (https://example.com/rm).\n\n"
                "del cache")
        check(clipboard() == text, "Copy text should put the whole item on the clipboard: %r" % clipboard())
        browser.click(todo)  # back to the waiting item: its view kept as it was
        browser.click("#ib-detail .md-bar button")
        got = browser.call("Runtime.evaluate", expression="navigator.clipboard.readText()", awaitPromise=True,
                           returnByValue=True)
        check(got.get("result", {}).get("value") == command, "Copy should put the block's text on the clipboard: %r" % got)
        check(browser.js("document.querySelector('#ib-detail .md-bar button').textContent") == "Copied",
              "the button should say it copied")
        # A message on the item: typed into its box and sent, it is a request file for the project's session.
        browser.click("#ib-detail .ib-form input")
        check(browser.js("document.querySelector('#ib-detail .ib-lab').textContent") == "Message inbox-project's agent",
              "the message box has a visible label naming the project")
        for c in "jk please split it":
            browser.call("Input.dispatchKeyEvent", type="keyDown", key=c, text=c)
            browser.call("Input.dispatchKeyEvent", type="keyUp", key=c)
        check(browser.js("document.activeElement === document.querySelector('#ib-detail .ib-form input')"),
              "typing j and k in the box should not move between rows")
        browser.click("#ib-detail .ib-form button")
        sent = wait_for("the message shown as sent", lambda: browser.js(
            "(function () { var s = document.querySelector('#ib-detail .ib-sent li'); return s && [s.firstChild."
            "textContent, s.lastChild.textContent.split(' ')[0], document.querySelector('#ib-detail .ib-form input')."
            "value]; })()"))
        check(sent == ["jk please split it", "sent", ""], "the message should show as sent, the box empty: %r" % sent)
        files = list((home / "requests" / pid).glob("*.json"))
        check(len(files) == 1, "one request file: %r" % files)
        saved = json.loads(files[0].read_text(encoding="utf-8"))
        check((saved["kind"], saved["ref"], saved["text"]) == (
            "todo", "Check <b>the backup</b> before the release", "jk please split it"), "the request: %r" % saved)
        # An item an agent marks now shows in the tab title. With unsent text in a box the page must not reload: a bar at
        # the top of the list offers it. Once the box is empty and unfocused, the page reloads itself, keeping the open row.
        browser.js("window.notReloaded = true")
        browser.click("#ib-detail .ib-form input")
        for c in "draft":
            browser.call("Input.dispatchKeyEvent", type="keyDown", key=c, text=c)
            browser.call("Input.dispatchKeyEvent", type="keyUp", key=c)
        browser.click("button[data-e2e=plain]")  # the text stays in the first row's view, which is out of sight now
        check(browser.js("document.activeElement.tagName !== 'INPUT'"), "the focus should have left the box")
        # A chain: two of its steps wait, and it is still one row, counted once; it names the round open here.
        with open(str(root / "_bmad-output" / "todo.md"), "a", encoding="utf-8") as f:
            f.write("- [ ] %s\n  - [x] write the notes\n  - [ ] (waiting on you: sign the release) Sign the release\n"
                    "  - [ ] (waiting on you: post it) Post the notes\n" % RELEASE)
        wait_for("the tab title to count the new item", lambda: browser.js("document.title") == "(3) Inbox")
        bar = wait_for("the bar at the top of the list", lambda: browser.js(
            "(function () { var f = document.querySelector('#panels .ib-fresh'); return f && !f.hidden && "
            "[f.firstChild.textContent, f.lastChild.textContent, document.getElementById('panels').firstChild === f]; })()"))
        check(bar == ["1 new waiting on you", "Reload", True], "the bar should offer the reload: %r" % bar)
        check(browser.js("document.querySelector('.bar-state').textContent").startswith("Changed since this page loaded"),
              "the key bar should say there is more to see after a reload")
        time.sleep(11)  # two more polls: typed text keeps the page where it is
        check(browser.js("window.notReloaded === true"),
              "a page with unsent text in another row's view must not reload")
        browser.click(todo)  # back to the row with the text: its view is kept as it was
        check(browser.js("document.querySelector('#ib-detail .ib-form input').value === 'draft'"),
              "the typed text should still be in the box")
        browser.js("var i = document.querySelector('#ib-detail .ib-form input'); i.value = ''; i.blur()")
        wait_for("the page to reload itself", lambda: browser.js("window.notReloaded !== true && !!document.querySelector('.ib-main')"),
                 timeout=20)
        mine = browser.js("(function () { var c = document.querySelector('.ib-main[aria-current=true]');"
                          " return [c && c.querySelector('.ib-title').textContent, document.title,"
                          " Array.prototype.map.call(document.querySelectorAll('#panels .ib-title'), function (t) {"
                          " return t.textContent; }).slice(0, 4), document.querySelector('#panels .ib-fresh').hidden]; })()")
        check(mine[0] == "Check <b>the backup</b> before the release" and mine[1] == "(3) Inbox"
              and RELEASE in mine[2] and mine[3] is True,
              "the reload should show the new row and keep the open one: %r" % mine)
        groups_open = ("Array.prototype.map.call(document.querySelectorAll('.ib-gtoggle'), function (b) {"
                       " return b.getAttribute('aria-expanded'); })")
        check(browser.js(groups_open) == ["false", "true"], "the reload keeps inbox-project's To do rows open: %r"
              % browser.js(groups_open))
        # The chain's row: its first line, the first waiting step's hint and the steps done; opened, the waiting steps
        # marked in its body, Open round and Later.
        browser.js("Array.prototype.filter.call(document.querySelectorAll('button.ib-main'), function (b) {"
                   " return b.querySelector('.ib-title').textContent === %s; })[0].setAttribute('data-e2e', 'chain')"
                   % json.dumps(RELEASE))
        browser.click("button[data-e2e=chain]")
        chain = browser.js(
            "(function () { var d = document.querySelector('#ib-detail'); return ["
            " document.querySelector('button[data-e2e=chain] .ib-text').textContent, d.querySelector('.ib-hint').textContent,"
            " d.querySelector('.ib-steps').textContent, Array.prototype.map.call(d.querySelectorAll('li.task.wait'),"
            " function (l) { return l.textContent; }), d.querySelector('a.ib-go').getAttribute('href'),"
            " d.querySelector('.ib-acts button').textContent]; })()")
        check(chain == ["sign the release · 1 of 3 steps done", "Waiting on you:sign the release", "1 of 3 steps done",
                        ["(waiting on you: sign the release) Sign the release", "(waiting on you: post it) Post the notes"],
                        "run/0/", "Later"], "the chain's row and its view: %r" % chain)
        browser.click("#ib-detail .ib-acts button")
        parked = wait_for("Later shown as sent", lambda: browser.js(
            "(function () { var p = document.querySelector('button[data-e2e=chain] .ib-sub .pill'); return p && ["
            " p.textContent, document.querySelector('#ib-detail .ib-msg-status').textContent]; })()"))
        check(parked == ["Later: sent", "Sent. The agent is asked to take the waiting marker off and keep the item open."],
              "Later should show as sent on the row: %r" % parked)
        later = [json.loads(p.read_text(encoding="utf-8")) for p in (home / "requests" / pid).glob("*.json")]
        check(sorted((f["kind"], f["ref"]) for f in later) == [
            ("later", "Sign the release"), ("todo", "Check <b>the backup</b> before the release")],
              "Later is a request on the waiting step: %r" % later)
        inbox_url = browser.js("location.href")
        browser.click("#ib-detail a.ib-go")
        wait_for("Open round to open the round's page", lambda: browser.js(
            "!!document.getElementById('done') && location.pathname.indexOf('/run/0/') > 0"))
        browser.call("Page.navigate", url=inbox_url)
        kept = wait_for("the list again", lambda: browser.js(
            "(function () { var b = Array.prototype.filter.call(document.querySelectorAll('button.ib-main'), function (b) {"
            " return b.querySelector('.ib-title').textContent === %s; })[0]; return b && b.querySelector('.ib-sub .pill')"
            " && b.querySelector('.ib-sub .pill').textContent; })()" % json.dumps(RELEASE)))
        check(kept == "Later: sent", "after a reload the row still shows Later as sent: %r" % kept)
        # Closing the group that holds the open row leaves no row open out of sight; j starts from the top again.
        browser.click(".ib-group:nth-child(2) .ib-gtoggle")
        browser.js("Array.prototype.filter.call(document.querySelectorAll('button.ib-main'), function (b) {"
                   " return b.querySelector('.ib-title').textContent === 'A plain todo item'; })[0]"
                   ".setAttribute('data-e2e', 'plain')")
        browser.click("button[data-e2e=plain]")
        browser.click(".ib-group:nth-child(2) .ib-gtoggle")
        closed = browser.js("[!!document.querySelector('.ib-main[aria-current]'), !!document.querySelector('#ib-detail "
                            ".ib-empty'), document.activeElement === document.querySelectorAll('.ib-gtoggle')[1]]")
        check(closed == [False, True, True], "closing the open row's group should close the row: %r" % closed)
        # Appearance: Ctrl+, opens the picker; arrows preview live, Esc goes back and returns focus, Enter keeps the
        # palette in prefs.json, and every page opens in it.
        check(browser.js(PALETTE) == "catppuccin", "the default palette is Catppuccin")
        browser.js("document.querySelector('button.ib-main').focus(); window.opener_ = document.activeElement")
        press(browser, ",", "Comma", 188, modifiers=2)
        wait_for("the picker", lambda: browser.js("document.getElementById('appearance').open && "
                                                  "document.activeElement.getAttribute('aria-selected') === 'true'"))
        press(browser, "ArrowDown", "ArrowDown", 40)
        check(browser.js(PALETTE) == "signoff", "↓ should show the next palette on the page: %r" % browser.js(PALETTE))
        press(browser, "Escape", "Escape", 27)
        check(browser.js(PALETTE) == "catppuccin" and not browser.js("document.getElementById('appearance').open"),
              "Esc should close the picker and go back: %r" % browser.js(PALETTE))
        check(browser.js("document.activeElement === window.opener_"), "Esc should give focus back")
        check(not (home / "prefs.json").exists(), "a preview keeps nothing")
        press(browser, ",", "Comma", 188, modifiers=2)
        wait_for("the picker again", lambda: browser.js("document.getElementById('appearance').open"))
        press(browser, "ArrowDown", "ArrowDown", 40)
        press(browser, "ArrowDown", "ArrowDown", 40)
        press(browser, "Enter", "Enter", 13, text="\r")
        wait_for("the picker to close", lambda: not browser.js("document.getElementById('appearance').open"))
        kept = json.loads((home / "prefs.json").read_text(encoding="utf-8"))
        check((kept["theme"], kept["palette"]) == ("system", "rose-pine"), "prefs.json: %r" % kept)
        browser.call("Page.navigate", url=browser.js("location.href"))
        wait_for("the reloaded list in Rose Pine", lambda: browser.js(
            "!!document.querySelector('a.ib-main') && %s === 'rose-pine'" % PALETTE))
        # Density: Compact in the picker, kept with Enter, and the list opens compact after a reload.
        press(browser, ",", "Comma", 188, modifiers=2)
        wait_for("the picker for density", lambda: browser.js("document.getElementById('appearance').open"))
        browser.click("#ap-density button:nth-child(2)")
        check(browser.js("document.documentElement.getAttribute('data-density')") == "compact", "Compact previews live")
        press(browser, "Enter", "Enter", 13, text="\r")
        wait_for("the picker to close after Compact", lambda: not browser.js("document.getElementById('appearance').open"))
        kept = json.loads((home / "prefs.json").read_text(encoding="utf-8"))
        check((kept["palette"], kept["density"]) == ("rose-pine", "compact"), "prefs.json: %r" % kept)
        browser.call("Page.navigate", url=browser.js("location.href"))
        wait_for("the list, compact", lambda: browser.js(
            "!!document.querySelector('a.ib-main') && document.documentElement.getAttribute('data-density') === 'compact' "
            "&& getComputedStyle(document.querySelector('.ib-main')).paddingTop === '6px'"))
        # Keys: j and k move the open row, Enter goes into it, f shows one item at a time, Esc leaves.
        current = ("(function () { var c = document.querySelector('.ib-main[aria-current=true]'); "
                   "return c && c.querySelector('.ib-title').textContent; })()")
        browser.js("document.querySelector('.ib-main').focus()")
        press(browser, "j", "KeyJ", 74, text="j")
        check(browser.js(current) == "Check <b>the backup</b> before the release" and browser.js(
            "document.activeElement.getAttribute('aria-current') === 'true'"), "j should open the next row: %r"
              % browser.js(current))
        press(browser, "Enter", "Enter", 13, text="\r")
        check(browser.js("document.activeElement.tagName === 'H2' && !!document.activeElement.closest('#ib-detail')"),
              "Enter on a todo row should go into the open item")
        press(browser, "f", "KeyF", 70, text="f")
        focus = browser.js("[document.body.classList.contains('focusing'), document.getElementById('panels')"
                           ".offsetParent === null, document.querySelector('.ib-fbar span').textContent,"
                           " document.activeElement.tagName]")
        check(focus[0] and focus[1] and focus[2].startswith("2 of ") and focus[3] == "H2", "f: Focus mode %r" % focus)
        press(browser, "j", "KeyJ", 74, text="j")
        check(browser.js("document.querySelector('.ib-fbar span').textContent").startswith("3 of ")
              and browser.js(current) == RELEASE, "j in Focus shows the next item: %r" % browser.js(current))
        press(browser, "Escape", "Escape", 27)
        check(browser.js("!document.body.classList.contains('focusing') && "
                         "document.activeElement === document.querySelector('.ib-main[aria-current=true]')"),
              "Esc should leave Focus, back on the open row")
        press(browser, "k", "KeyK", 75, text="k")
        press(browser, "k", "KeyK", 75, text="k")
        check(browser.js("document.activeElement === document.querySelector('a.ib-main')"), "k back to the round")
        # Every Waiting on you row says how long ago it changed (the exact time in the tooltip); Newest first puts the
        # round, the oldest, last, and is kept in prefs.json; j follows the order on screen.
        first_rows = ("Array.prototype.map.call(document.querySelectorAll('section.ib')[0].querySelectorAll('.ib-main'),"
                      " function (m) { return m.tagName + ':' + m.querySelector('.ib-title').textContent; })")
        times = browser.js("Array.prototype.map.call(document.querySelectorAll('section.ib')[0].querySelectorAll("
                           "'time.ib-when'), function (t) { return [t.textContent, t.getAttribute('datetime'), t.title]; })")
        check(len(times) == 3 and all(re.fullmatch(r"(just now|\d+ min ago|\d+ h ago)", t[0]) and re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", t[1]) and t[2] for t in times), "every waiting row has a time: %r" % times)
        check(browser.js(first_rows)[0].startswith("A:"), "oldest first starts with the round: %r" % browser.js(first_rows))
        browser.click(".ib-sort button:nth-child(2)")
        order = browser.js(first_rows)
        check(order[-1].startswith("A:") and not order[0].startswith("A:") and browser.js(
            "document.querySelector('.ib-sort button[aria-pressed=true]').textContent") == "Newest first",
              "Newest first should put the round last: %r" % order)
        wait_for("the sort kept", lambda: json.loads((home / "prefs.json").read_text(encoding="utf-8"))["sort"] == "newest")
        press(browser, "k", "KeyK", 75, text="k")  # the round is last now: k lands on the row above it
        check(browser.js("document.querySelectorAll('section.ib')[0].querySelectorAll('.ib-main')[1]"
                         ".getAttribute('aria-current')") == "true", "k should follow the new order")
        browser.call("Page.navigate", url=browser.js("location.href"))
        wait_for("the list in Newest first", lambda: browser.js(
            "!!document.querySelector('.ib-main') && document.querySelector('.ib-sort button[aria-pressed=true]')"
            ".textContent === 'Newest first' && document.querySelector('section.ib .ib-main').tagName === 'BUTTON'"))
        browser.click(".ib-sort button:nth-child(1)")
        browser.js("document.querySelector('a.ib-main').focus()")
        wait_for("the sort kept again", lambda: json.loads((home / "prefs.json").read_text(encoding="utf-8"))["sort"] == "oldest")
        press(browser, "Enter", "Enter", 13, text="\r")  # a round's row opens its page
        wait_for("the round's page", lambda: browser.js("!!document.getElementById('done') && "
                                                        "location.pathname.indexOf('/run/0/') > 0"))
        check(browser.js("document.querySelector('a.back') && document.querySelector('a.back').textContent") == "← Inbox",
              "the round's page should lead back to the inbox")
        check(browser.js(PALETTE) == "rose-pine", "the round's page should open in the kept palette")
        # Another tab keeps Comfortable meanwhile; the round's page, loaded when it was Compact, leaves it alone.
        prefs = json.loads((home / "prefs.json").read_text(encoding="utf-8"))
        (home / "prefs.json").write_text(json.dumps(dict(prefs, density="comfortable")), encoding="utf-8")
        press(browser, ",", "Comma", 188, modifiers=2)  # the round's page keeps a choice too, at the inbox's route
        wait_for("the picker on the round's page", lambda: browser.js("document.getElementById('appearance').open"))
        check(browser.js("document.getElementById('ap-density').checkVisibility()"),
              "the round's page offers Compact too")
        press(browser, "ArrowDown", "ArrowDown", 40)
        press(browser, "Enter", "Enter", 13, text="\r")
        wait_for("Gruvbox kept from the round's page", lambda: json.loads(
            (home / "prefs.json").read_text(encoding="utf-8"))["palette"] == "gruvbox")
        check(json.loads((home / "prefs.json").read_text(encoding="utf-8"))["density"] == "comfortable",
              "a choice kept on the round's page must not write the density it loaded with")
        browser.click("#done")
        finished(waiter, out, err, [topic / "round-01.answers.json"])
        browser.call("Page.navigate", url=browser.js("document.querySelector('a.back').href"))
        state = wait_for("the answered row", lambda: browser.js(
            "(function () { var s = document.querySelector('.state'); return s && s.textContent; })()"))
        check(state == "sent", "the answered round should show as sent, not working: %r" % state)
        title = browser.js("document.title")
        check(title == "(2) Inbox", "the answered round should leave the count: %r" % title)
        # The OSV section: projects off Uptick's scan list share one collapsed row, each with its own Scan button;
        # a click adds that project to the list.
        osv = "section.ib:last-of-type "
        rows = browser.js("document.querySelectorAll('%s.ib-row').length" % osv)
        label = browser.js("document.querySelector('%s.ib-title').textContent" % osv)
        check(rows == 1 and label == "Not scanned (2)", "one row for both unscanned projects: %r %r" % (rows, label))
        check(not browser.js("document.querySelector('%sbutton.ib-main').hasAttribute('aria-current')" % osv),
              "the Not scanned row starts closed")
        # Keyboard: Enter on the focused row opens it, like the other rows.
        browser.js("document.querySelector('%sbutton.ib-main').focus()" % osv)
        browser.call("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", text="\r")
        browser.call("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter")
        wait_for("Enter to open the row", lambda: browser.js(
            "!!document.querySelector('#ib-detail .ib-vuln-name') && "
            "document.querySelector('%sbutton.ib-main').getAttribute('aria-current') === 'true'" % osv))
        names = browser.js("Array.prototype.map.call(document.querySelectorAll('#ib-detail .ib-vuln-name'), "
                           "function (n) { return n.textContent; })")
        check(sorted(names) == ["inbox-other", "inbox-project"], "the unscanned projects, by name: %r" % names)
        item = "#ib-detail .ib-vulns li:nth-of-type(%d) " % (1 + names.index("inbox-project"))
        browser.click(item + "button")
        wait_for("the project added to Uptick's scan list", lambda: browser.js(
            "document.querySelector('%s.ib-msg-status').textContent" % item).startswith("Added"))
        check(json.loads(scan_list.read_text(encoding="utf-8")) == {
            "repos": ["/elsewhere", str(root.resolve())], "kept": True}, "the scan list: %r" % scan_list.read_text())
        label = browser.js("document.querySelector('%s.ib-title').textContent" % osv)
        check(label == "Not scanned (1)", "the count should drop without a reload: %r" % label)
        # Listed now, the fresh finding shows; Hand to agent writes a vuln request. Findings never count as waiting.
        browser.call("Page.navigate", url=browser.js("location.href"))
        label = wait_for("the finding's row", lambda: browser.js(
            "(function () { var t = document.querySelector('%s.ib-title'); return t && !/^Not scanned/.test(t.textContent) "
            "&& t.textContent; })()" % osv))
        check(label == "1 known vulnerability, high", "the row should count the finding: %r" % label)
        last = browser.js("var t = document.querySelectorAll('%s.ib-title'); t[t.length - 1].textContent" % osv)
        check(last == "Not scanned (1)", "the other project stays in the Not scanned row, last: %r" % last)
        browser.click(osv + "button.ib-main")
        browser.click("#ib-detail .ib-vuln-sent button")
        handed = wait_for("the finding handed over", lambda: browser.js(
            "(function () { var p = document.querySelector('#ib-detail .ib-vuln-sent .pill'); return p && p.textContent; })()"))
        check(handed.startswith("handed over"), "the finding should show as handed over: %r" % handed)
        files = [json.loads(p.read_text(encoding="utf-8")) for p in (home / "requests" / pid).glob("*.json")]
        check(sorted((f["kind"], f["ref"]) for f in files) == [
            ("later", "Sign the release"), ("todo", "Check <b>the backup</b> before the release"),
            ("vuln", "lodash@4.17.20")], "requests: %r" % files)
        check(browser.js("document.title") == "(2) Inbox", "a finding must not count as waiting")
        # The last unscanned project: at 0 the row stays open with its note until a reload.
        off = "%s.ib-row:last-of-type " % osv
        browser.click(off + "button.ib-main")
        browser.click("#ib-detail .ib-vuln-sent button")
        wait_for("the other project added", lambda: browser.js(
            "document.querySelector('#ib-detail .ib-msg-status').textContent").startswith("Added"))
        label = browser.js("document.querySelector('%s.ib-title').textContent + '|' + "
                           "document.querySelector('#ib-detail h2').textContent + '|' + "
                           "document.querySelector('%sbutton.ib-main').getAttribute('aria-current')" % (off, off))
        check(label == "Not scanned (0)|Not scanned (0)|true", "the row should stay open at 0: %r" % label)
        # A phone: a row opens as a bottom sheet with the rest inert, Esc closes it and focus goes back to the row;
        # the sidebar is a drawer, Esc closes it and focus goes back to its button.
        browser.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
        browser.call("Page.navigate", url=browser.js("location.href"))
        wait_for("the list at 390", lambda: browser.js("!!document.querySelector('#panels button.ib-main') && "
                                                       "!document.querySelector('.ib-main[aria-current]')"))
        phone = browser.js("[document.getElementById('intro').textContent, document.getElementById('intro').offsetParent"
                           " !== null]")
        check(phone == ["2 waiting on you in 1 project", True], "the summary line shows on a phone too: %r" % phone)
        browser.click("#panels button.ib-main")
        check(browser.js("document.body.classList.contains('sheet') && document.getElementById('panels').inert && "
                         "document.activeElement.tagName === 'H2' && !!document.activeElement.closest('#ib-detail')"),
              "a row at 390 should open as a sheet")
        press(browser, "Escape", "Escape", 27)
        check(browser.js("!document.body.classList.contains('sheet') && !document.getElementById('panels').inert && "
                         "document.activeElement === document.querySelector('#panels button.ib-main')"),
              "Esc should close the sheet, focus back on the row")
        browser.click("header button.icon")
        check(browser.js("document.body.classList.contains('side-open') && document.querySelector('main').inert && "
                         "document.querySelector('header button.icon').getAttribute('aria-expanded') === 'true' && "
                         "!!document.activeElement.closest('#ib-side')"), "the sidebar button opens the drawer")
        press(browser, "Escape", "Escape", 27)
        check(browser.js("!document.body.classList.contains('side-open') && !document.querySelector('main').inert && "
                         "document.activeElement === document.querySelector('header button.icon') && "
                         "document.activeElement.getAttribute('aria-expanded') === 'false'"),
              "Esc should close the drawer, focus back on its button")
    except E2EError:
        sys.stderr.write("--- inbox stderr ---\n" + "".join(inbox_err) + "--- run stderr ---\n" + "".join(err))
        raise
    finally:
        kill(browser.proc)
        if waiter:
            kill(waiter)
        end_inbox(inbox, script, env, home)


SEARCH_OPS_TODO = """# Todo

- [ ] (waiting on you: jóváhagyás) Hagyd jóvá a kiadási jegyzetet
  A régi ágak listája a `branches.txt` fájlban van.
- [ ] Rendezd át a régi ágakat
- [ ] Írd át a README-t
"""
SEARCH_DOCS_TODO = "- [ ] Frissítsd a bevezetőt\n- [ ] Rename the glossary\n"
# What the list shows: the rows on screen, the groups' state, the sections and the summary line. A search that is
# cleared must give back exactly this.
LIST_STATE = """(function () {
  var rows = Array.prototype.filter.call(document.querySelectorAll('#panels li.ib-row'), function (l) { return l.checkVisibility(); });
  return JSON.stringify([rows.map(function (l) { return l.querySelector('.ib-title').textContent; }),
    Array.prototype.map.call(document.querySelectorAll('.ib-gtoggle'), function (b) { return b.getAttribute('aria-expanded'); }),
    Array.prototype.filter.call(document.querySelectorAll('#panels h2.ib-head'), function (h) { return h.checkVisibility(); })
      .map(function (h) { return h.firstChild.textContent; }),
    document.getElementById('intro').textContent,
    Array.prototype.map.call(document.querySelectorAll('.nav'), function (b) { return b.getAttribute('aria-pressed'); })]);
})()"""
SEARCH_ROWS = """(function () {
  return Array.prototype.filter.call(document.querySelectorAll('#panels li.ib-row'), function (l) { return l.checkVisibility(); })
    .map(function (l) { return l.querySelector('.ib-title').textContent; });
})()"""


def search_fixture(work):
    """A synthetic Signoff home for the list's search: project ops (a waiting todo item that names a file in a code
    span, two more open items, a request on one, a finding, a waiting run in a topic whose round has accented
    titles) and project docs (two open items). Returns what a test needs to start the inbox and the run."""
    sys.path.insert(0, str(ROOT / "plugin" / "signoff"))
    import signoff
    home, ops, docs = work / "search-home", work / "search-ops", work / "search-docs"
    topic = ops / ".signoff" / "2026-11-06-repo-hygiene"
    topic.mkdir(parents=True)
    (ops / ".git").mkdir()
    (ops / "_bmad-output").mkdir()
    (ops / "_bmad-output" / "todo.md").write_text(SEARCH_OPS_TODO, encoding="utf-8")
    (docs / "_bmad-output").mkdir(parents=True)
    (docs / ".git").mkdir()
    (docs / "_bmad-output" / "todo.md").write_text(SEARCH_DOCS_TODO, encoding="utf-8")
    data = json.loads((DEMO / "round-04.json").read_text(encoding="utf-8"))
    data["title"], data["tab"] = "Repo-higiénia: ikonok", "Ikonok"
    data["items"][0]["title"], data["items"][1]["title"] = "Az ensemble ikonja", "Melyik színt válasszuk?"
    (topic / "round-08.json").write_text(json.dumps(data), encoding="utf-8")
    ids = {}
    (home / "projects").mkdir(parents=True, mode=0o700)
    for name, root in (("ops", ops), ("docs", docs)):
        ids[name] = signoff.project_id(root.resolve())
        (home / "projects" / (ids[name] + ".json")).write_text(json.dumps(
            {"schema": 1, "id": ids[name], "root": str(root.resolve()), "name": name}), encoding="utf-8")
    asked = home / "requests" / ids["ops"]
    asked.mkdir(parents=True, mode=0o700)
    (asked / ("0123456789abcdef.json")).write_text(json.dumps({
        "schema": 1, "id": "0123456789abcdef", "kind": "todo", "ref": "Rendezd át a régi ágakat",
        "text": "Kérlek előbb a stabil ágat", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}), encoding="utf-8")
    cache, config = work / "search-cache", work / "search-config"
    (cache / "uptick").mkdir(parents=True)
    (config / "uptick").mkdir(parents=True)
    (cache / "uptick" / "vulns.json").write_text(json.dumps({"schema": 1, "targets": {}, "repos": {str(ops.resolve()): {
        "at": time.time(), "tried": time.time(), "count": 1, "max": "7.5", "level": "high", "gaps": [], "error": "",
        "hint": "", "prev": None, "details": [{
            "name": "lodash", "version": "4.17.20", "ecosystem": "npm", "ids": ["GHSA-35jh-r3h4-6jhm"], "advisories": 1,
            "severity": "7.2", "level": "high", "fixed": "4.17.21", "sources": ["package-lock.json"]}]}}}),
        encoding="utf-8")
    (config / "uptick" / "scan-repos.json").write_text(json.dumps({"repos": [str(ops.resolve())]}), encoding="utf-8")
    env = dict(os.environ, SIGNOFF_INBOX_HOME=str(home), XDG_CACHE_HOME=str(cache), XDG_CONFIG_HOME=str(config))
    script = [sys.executable, "-I", "-B", str(ROOT / "plugin" / "signoff" / "signoff.py")]
    return {"home": home, "env": env, "script": script, "topic": topic, "ops": ops, "ids": ids}


def run_inbox_search(work):
    """The list's search: a field in the header that filters as you type, ignoring case and accents, over every view
    (closed To do groups open on a match), with a count and Clear. / and Cmd+K (Ctrl+K) focus it, Esc clears it and
    goes back to the list, j, k and Enter walk the results, and an empty field gives back exactly the list as it was.
    The query survives the page's own reload."""
    fx = search_fixture(work)
    home, env, script = fx["home"], fx["env"], fx["script"]
    inbox_err, out, err = [], [], []
    inbox = spawn(script + ["inbox", "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                  universal_newlines=True, encoding="utf-8", errors="replace")
    threading.Thread(target=pump, args=(inbox.stderr, inbox_err), daemon=True).start()

    def link():
        return inbox_link(inbox, inbox_err, script, env, home)
    start = wait_for("a start link from the inbox", link)
    waiter = spawn(script + [str(fx["topic"]), "--no-open"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   universal_newlines=True, encoding="utf-8", errors="replace")
    for stream, sink in ((waiter.stdout, out), (waiter.stderr, err)):
        threading.Thread(target=pump, args=(stream, sink), daemon=True).start()
    browser = Browser(str(work / "profile-search"))
    field = "document.getElementById('ib-search')"

    def type_query(text):  # replaces what is in the field, as typing over a selection does
        browser.js("%s.focus(); %s.select()" % (field, field))
        browser.call("Input.insertText", text=text)

    def found(text):
        type_query(text)
        return browser.js(SEARCH_ROWS)
    try:
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        browser.call("Page.enable")
        wait_for("the run to wait on the inbox", lambda: any("inbox is open" in l for l in err))
        os.utime(str(fx["ops"] / "_bmad-output" / "todo.md"), None)  # not older than the run: the round sorts first
        browser.call("Page.navigate", url=start)
        wait_for("the list", lambda: browser.js("document.querySelectorAll('#panels li.ib-row').length > 3"))
        base = browser.js(LIST_STATE)
        parts = json.loads(base)
        check(parts[0][:2] == ["Repo-higiénia: ikonok", "Hagyd jóvá a kiadási jegyzetet"] and len(parts[0]) == 4
              and parts[1] == ["false", "false"], "the list before any search: %r" % parts)
        # A search form with a name, a labelled field and a count a screen reader hears.
        shape = browser.js("(function () { var f = document.querySelector('form[role=search]'), i = " + field + ", c = "
                           "document.getElementById('ib-search-count'); return [!!f, f.contains(i), "
                           "i.labels.length === 1 && i.labels[0].textContent.length > 0 || i.hasAttribute('aria-label'), "
                           "c.getAttribute('aria-live'), document.getElementById('ib-search-clear').hidden, i.type]; })()")
        check(shape == [True, True, True, "polite", True, "search"], "the search form: %r" % shape)
        # "/" and Cmd+K and Ctrl+K focus the field.
        press(browser, "/", "Slash", 191, text="/")
        check(browser.js("document.activeElement === " + field + " && " + field + ".value === ''"),
              "/ should focus the field and not type into it")
        browser.js(field + ".blur()")
        mac = browser.js("/Mac|iPhone|iPad/.test(navigator.platform)")
        press(browser, "k", "KeyK", 75, modifiers=4 if mac else 2)  # Cmd+K on a Mac, Ctrl+K elsewhere
        check(browser.js("document.activeElement === " + field), "the platform's K chord should focus the field")
        browser.js(field + ".blur()")
        press(browser, "k", "KeyK", 75, modifiers=2 if mac else 4)  # the other platform's: nothing
        check(browser.js("document.activeElement !== " + field), "the other platform's chord should leave the field alone")
        # Typing filters live; case and accents do not matter, in the query or in the text.
        check(found("ikonja") == ["Repo-higiénia: ikonok"], "an item title of the round: %r" % browser.js(SEARCH_ROWS))
        check(found("HIGIENIA") == ["Repo-higiénia: ikonok"], "the round's title, accents and case ignored")
        check(found("higiénia") == ["Repo-higiénia: ikonok"], "an accented query matches too")
        check(found("szint valasszuk") == ["Repo-higiénia: ikonok"], "an item title, every word needed anywhere in it")
        check(found("ikonok") == ["Repo-higiénia: ikonok"], "the round's tab label")
        shown = browser.js("[document.getElementById('ib-search-count').textContent, "
                           "document.getElementById('ib-search-clear').hidden, document.getElementById('intro').textContent]")
        check(shown[0] == "1 match" and shown[1] is False, "the count and Clear while a query is active: %r" % shown)
        check(found("jovahagy") == ["Hagyd jóvá a kiadási jegyzetet"], "the waiting hint of a todo item")
        check(found("branches.txt") == ["Hagyd jóvá a kiadási jegyzetet"], "a code span in a todo item's body")
        # Closed groups open on a match and show only the matching rows under their project heading.
        check(found("rendezd") == ["Rendezd át a régi ágakat"], "a todo item in a closed group")
        opened = browser.js("[Array.prototype.map.call(document.querySelectorAll('.ib-gtoggle'), function (b) { return "
                            "b.getAttribute('aria-expanded'); }), Array.prototype.filter.call(document.querySelectorAll("
                            "'#panels .ib-ghead'), function (h) { return h.checkVisibility(); }).length]")
        check(opened[0][1] == "true" and opened[1] == 1, "the matching project's group opens, the others stay out: %r" % opened)
        check(found("kerlek elobb") == ["Rendezd át a régi ágakat"], "the text of a request on an item")
        check(found("lodash") == ["1 known vulnerability, high"], "a vulnerability's package name: %r" % browser.js(SEARCH_ROWS))
        check(found("ghsa-35jh") == ["1 known vulnerability, high"], "a vulnerability's advisory id")
        check(sorted(found("docs")) == ["Frissítsd a bevezetőt", "Not scanned (1)", "Rename the glossary"],
              "a project name matches its rows: %r" % browser.js(SEARCH_ROWS))
        check(found("zzzz") == [] and browser.js("document.getElementById('ib-search-count').textContent") == "No matches"
              and browser.js("document.querySelector('.ib-nomatch').hidden") is False,
              "no match says so")
        # Esc clears the field and puts the focus on the list, which is exactly as it was.
        press(browser, "Escape", "Escape", 27)
        check(browser.js("document.activeElement.classList.contains('ib-main') && " + field + ".value === '' && "
                         "document.getElementById('ib-search-clear').hidden && document.getElementById('ib-search-count')"
                         ".textContent === ''"), "Esc should clear the field and focus the list")
        check(browser.js(LIST_STATE) == base, "an empty query gives back the list as it was: %s" % browser.js(LIST_STATE))
        # Clear does the same by mouse.
        type_query("regi")
        browser.click("#ib-search-clear")
        check(browser.js(LIST_STATE) == base and browser.js("document.activeElement === " + field),
              "Clear should empty the field, keep the focus in it and restore the list")
        # A sidebar pick during a search ends the search and shows that view, also when the view was already picked.
        browser.js("document.querySelector('nav.side button.nav').click()")
        picked = browser.js("[document.querySelector('nav.side button.nav').getAttribute('aria-pressed'), " + field
                            + ".value]")
        type_query("regi")
        browser.js("document.querySelector('nav.side button.nav').click()")
        again_pick = browser.js("[document.querySelector('nav.side button.nav').getAttribute('aria-pressed'), " + field
                                + ".value, document.getElementById('ib-search-count').textContent]")
        check(picked[0] == "true" and again_pick == ["true", "", ""],
              "a sidebar pick during a search should end it and keep that view: %r %r" % (picked, again_pick))
        browser.js("document.querySelector('nav.side button.nav').click()")  # back to no filter
        browser.js(field + ".focus()")
        # The results are walked with Enter, j and k.
        check(found("regi") == ["Hagyd jóvá a kiadási jegyzetet", "Rendezd át a régi ágakat"],
              "two results: %r" % browser.js(SEARCH_ROWS))
        press(browser, "Enter", "Enter", 13, text="\r")
        check(browser.js("document.activeElement.getAttribute('aria-current') === 'true' && document.activeElement"
                         ".querySelector('.ib-title').textContent === 'Hagyd jóvá a kiadási jegyzetet' && "
                         "document.querySelector('#ib-detail h2').textContent === 'Hagyd jóvá a kiadási jegyzetet'"),
              "Enter in the field should land on the first result")
        press(browser, "j", "KeyJ", 74, text="j")
        check(browser.js("document.activeElement.querySelector('.ib-title').textContent") == "Rendezd át a régi ágakat",
              "j should move to the next result")
        press(browser, "j", "KeyJ", 74, text="j")
        check(browser.js("document.activeElement.querySelector('.ib-title').textContent") == "Rendezd át a régi ágakat",
              "j should stop at the last result, not walk into rows the search hides")
        press(browser, "k", "KeyK", 75, text="k")
        check(browser.js("document.activeElement.querySelector('.ib-title').textContent") == "Hagyd jóvá a kiadási jegyzetet",
              "k should move back")
        press(browser, "Enter", "Enter", 13, text="\r")
        check(browser.js("document.activeElement.tagName === 'H2' && !!document.activeElement.closest('#ib-detail')"),
              "Enter on a result should go into the item")
        # "/" typed in another field is a slash, not a jump to the search.
        browser.click("#ib-detail .ib-form input")
        press(browser, "/", "Slash", 191, text="/")
        check(browser.js("document.activeElement.closest('.ib-form') !== null && "
                         "document.activeElement.value === '/'"), "/ typed in another field is a slash")
        browser.js("document.activeElement.value = ''")
        # The query survives the page's own reload (Reload keeps what the page keeps): the field, the results, the row.
        with open(str(fx["ops"] / "_bmad-output" / "todo.md"), "a", encoding="utf-8") as f:
            f.write("- [ ] (waiting on you: nézd meg) Új régi feladat\n")
        os.utime(str(fx["ops"] / "_bmad-output" / "todo.md"), None)
        browser.js("window.notReloaded = true; document.activeElement.blur()")
        # Either the page reloads by itself (it is calm) or its bar offers the reload; both keep the query.
        wait_for("the reload or the bar that offers it", lambda: browser.js(
            "window.notReloaded !== true || (function () { var f = document.querySelector('#panels .ib-fresh'); "
            "return !!f && !f.hidden; })()"), timeout=40)
        if browser.js("window.notReloaded === true"):
            browser.click("#panels .ib-fresh button")
        wait_for("the reload", lambda: browser.js("window.notReloaded !== true && !!document.querySelector('.ib-main')"))
        again = browser.js("[" + field + ".value, document.getElementById('ib-search-count').textContent, " + SEARCH_ROWS
                           + ".length]")
        check(again[0] == "regi" and again[1] == "3 matches" and again[2] == 3, "the query across a reload: %r" % again)
        # A phone: the field is in the header and filters there too.
        browser.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
        browser.call("Page.navigate", url=browser.js("location.href"))
        wait_for("the list at 390", lambda: browser.js("!!document.querySelector('#panels button.ib-main, #panels a.ib-main')"))
        check(browser.js("(function () { var r = " + field + ".getBoundingClientRect(); return r.width > 120 && "
                         "r.right <= window.innerWidth && r.left >= 0; })()"), "the field fits the phone")
        check(found("ikonja") == ["Repo-higiénia: ikonok"], "the phone's search")
    except E2EError:
        sys.stderr.write("--- inbox stderr ---\n" + "".join(inbox_err) + "--- run stderr ---\n" + "".join(err))
        raise
    finally:
        kill(browser.proc)
        kill(waiter)
        end_inbox(inbox, script, env, home)


def run_live(work):
    """Live sessions, from synthetic sessions/ files as the hooks write them: a background and an interactive session
    waiting on the user are rows in Waiting on you and count in the tab title; one idle for minutes is a dimmer row in
    Idle that counts nowhere. The background one offers `claude attach` with its short id, copied exactly; the
    interactive one says to go to its terminal."""
    sys.path.insert(0, str(ROOT / "plugin" / "signoff"))
    import signoff
    home, root = work / "live-home", work / "live-project"
    (root / ".git").mkdir(parents=True)
    pid = signoff.project_id(root.resolve())
    for sub in ("projects", "sessions"):
        (home / sub).mkdir(parents=True, mode=0o700)
    (home / "projects" / (pid + ".json")).write_text(json.dumps(
        {"schema": 1, "id": pid, "root": str(root.resolve()), "name": "live-project"}), encoding="utf-8")

    def stamp(ago):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))

    def session(name, **extra):
        data = {"schema": 1, "session": name, "project": pid, "pid": os.getpid(), "state": "working", "at": stamp(30)}
        data.update(extra)
        (home / "sessions" / (name + ".json")).write_text(json.dumps(data), encoding="utf-8")
    session("8d4e0402-151b-464f-bceb-89da0de14e2c", waiting="permission", waiting_at=stamp(330), bg=True)
    session("1b2c3d4e-0000-4000-8000-000000000002", waiting="permission", waiting_at=stamp(70))
    session("5f6a7b8c-0000-4000-8000-000000000003", state="idle", at=stamp(900), message="SENTINEL-MSG")
    env = dict(os.environ, SIGNOFF_INBOX_HOME=str(home))
    script = [sys.executable, "-I", "-B", str(ROOT / "plugin" / "signoff" / "signoff.py")]
    inbox_err = []
    inbox = spawn(script + ["inbox", "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                  universal_newlines=True, encoding="utf-8", errors="replace")
    threading.Thread(target=pump, args=(inbox.stderr, inbox_err), daemon=True).start()

    def link():
        return inbox_link(inbox, inbox_err, script, env, home)
    start = wait_for("a start link from the inbox", link)
    browser = Browser(str(work / "profile-live"))
    try:
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        browser.call("Page.enable")
        browser.call("Browser.grantPermissions", origin=start.split("/b/")[0],
                     permissions=["clipboardReadWrite", "clipboardSanitizedWrite"])
        browser.call("Page.navigate", url=start)
        seen = wait_for("the live session rows", lambda: browser.js(
            "(function () { var s = document.querySelectorAll('#panels section'); if (s.length < 2) return null;"
            " return Array.prototype.map.call(s, function (x) { return [x.querySelector('h2').textContent,"
            " x.classList.contains('idle'), Array.prototype.map.call(x.querySelectorAll('.ib-row'), function (r) {"
            " return [r.querySelector('.ib-kind').textContent, r.querySelector('.ib-title').textContent,"
            " (r.querySelector('.ib-text') || {}).textContent, r.querySelector('.ib-when').textContent,"
            " getComputedStyle(r.querySelector('.ib-title')).fontWeight]; })]; }); })()"))
        # How long, from stamps written at the start: a slow run may have moved on by a minute.
        whens = [[r.pop(3) for r in sec[2]] for sec in seen[:2]]
        check(seen[:2] == [["Waiting on you 2", False, [
                           ["Waiting on you", "Claude Code session", "claude attach 8d4e0402", "600"],
                           ["Waiting on you", "Claude Code session", "Go to its terminal", "600"]]],
                       ["Idle 1not counted", True, [
                           ["Idle", "Claude Code session", "Go to its terminal", "500"]]]]
              and whens[0][0] in ("5 min ago", "6 min ago") and whens[0][1] in ("1 min ago", "2 min ago")
              and whens[1][0] in ("for 15 min", "for 16 min"),
              "Waiting on you should list both waiting sessions, Idle the idle one, dimmer: %r %r" % (seen, whens))
        counts = browser.js("[document.title, Array.prototype.map.call(document.querySelectorAll('.nav'), function (b) {"
                            " return b.textContent; }), document.body.textContent.indexOf('SENTINEL') < 0]")
        check(counts == ["(2) Inbox", ["Waiting on you 2", "To do", "LIlive-project 2, waiting on you"], True],
              "the idle session must count nowhere: %r" % counts)
        browser.click("#panels button.ib-main")
        detail = browser.js("(function () { var b = document.querySelector('#ib-detail .ib-body'); return b && ["
                            "Array.prototype.map.call(b.querySelectorAll('pre code'), function (c) { return c.textContent; }),"
                            " b.querySelectorAll('a, input, form').length]; })()")
        check(detail == [["claude attach 8d4e0402"], 0], "the background session's attach command: %r" % detail)
        browser.click("#ib-detail .md-bar button")
        got = browser.call("Runtime.evaluate", expression="navigator.clipboard.readText()", awaitPromise=True,
                           returnByValue=True)
        check(got.get("result", {}).get("value") == "claude attach 8d4e0402",
              "Copy should put the attach command on the clipboard: %r" % got)
        browser.js("document.querySelectorAll('#panels button.ib-main')[1].setAttribute('data-e2e', 'term')")
        browser.click("button.ib-main[data-e2e=term]")
        detail = browser.js("(function () { var b = document.querySelector('#ib-detail .ib-body'); return b && ["
                            "b.querySelectorAll('pre, button').length, b.textContent.indexOf('Go to its terminal') >= 0]; })()")
        check(detail == [0, True], "an interactive session gets no command, only where to answer: %r" % detail)
        browser.click("section.idle button.ib-main")
        text = browser.js("document.querySelector('#ib-detail .ib-body p').textContent")
        check(re.match(r"Idle for 1[56] min; it waits for your next prompt\. An idle session does not count", text),
              "the idle session's text: %r" % text)
        # A session of another project starts waiting. Nothing is typed, so the page reloads itself within a few polls
        # and shows the third row, under its own project's heading.
        browser.js("window.notReloaded = true")
        first = work / "aaa-project"
        (first / ".git").mkdir(parents=True)
        first_id = signoff.project_id(first.resolve())
        (home / "projects" / (first_id + ".json")).write_text(json.dumps(
            {"schema": 1, "id": first_id, "root": str(first.resolve()), "name": "aaa-project"}), encoding="utf-8")
        data = {"schema": 1, "session": "2c3d4e5f-0000-4000-8000-000000000004", "project": first_id, "pid": os.getpid(),
                "state": "working", "at": stamp(5), "waiting": "permission", "waiting_at": stamp(10)}
        (home / "sessions" / "2c3d4e5f-0000-4000-8000-000000000004.json").write_text(json.dumps(data), encoding="utf-8")
        names = ("Array.prototype.map.call(document.querySelectorAll('section.ib')[0].querySelectorAll('.ib-row'),"
                 " function (r) { return r.querySelector('.ib-name').textContent; })")
        got = wait_for("the page to reload itself with the third session", lambda: browser.js(
            "window.notReloaded !== true && document.title === '(3) Inbox' && " + names + ".length === 3 && " + names), timeout=20)
        # Grouped by project, each group where its first row falls in the order: oldest first puts the new project's
        # group last; no By project button.
        check(got == ["live-project", "live-project", "aaa-project"], "grouped by project, oldest first: %r" % got)
        heads = ("[Array.prototype.map.call(document.querySelectorAll('section.ib')[0].querySelectorAll("
                 "'.ib-ghead .nl'), function (h) { return h.textContent; }), document.querySelectorAll("
                 "'.ib-sort button').length, document.getElementById('intro').textContent]")
        check(browser.js(heads) == [["live-project", "aaa-project"], 2, "3 waiting on you in 2 projects"],
              "one heading per project and the summary: %r" % browser.js(heads))
        browser.click(".ib-sort button:nth-child(2)")  # newest first: the new project's group comes to the top
        check(browser.js(names) == ["aaa-project", "live-project", "live-project"]
              and browser.js(heads)[0] == ["aaa-project", "live-project"], "newest first: %r" % browser.js(names))
        waits = browser.js("Array.prototype.map.call(document.querySelectorAll('section.ib')[0].querySelectorAll('.ib-row"
                           " .ib-title + .ib-when'), function (t) { return t.textContent; })")
        check(waits[0] == "just now" or waits[0].endswith("min ago"), "the newest session's time: %r" % waits)
        # Both answered: nothing waits, the empty state stands in, and its Idle button lands on the idle row.
        session("8d4e0402-151b-464f-bceb-89da0de14e2c", bg=True)
        session("1b2c3d4e-0000-4000-8000-000000000002")
        data.pop("waiting"), data.pop("waiting_at")
        (home / "sessions" / "2c3d4e5f-0000-4000-8000-000000000004.json").write_text(json.dumps(data), encoding="utf-8")
        browser.call("Page.navigate", url=browser.js("location.href"))
        zero = wait_for("the empty state with Idle", lambda: browser.js(
            "(function () { var z = document.querySelector('.ib-zero'); return z && !z.hidden && [document.title,"
            " Array.prototype.map.call(z.querySelectorAll('button'), function (b) { return b.textContent; })]; })()"))
        check(zero == ["Inbox", ["Idle (1)"]], "nothing waiting: the empty state, with a button to Idle: %r" % zero)
        browser.click(".ib-zero button")
        landed = browser.js("[document.querySelector('.ib-zero').hidden, !!document.activeElement.closest('section.idle')]")
        check(landed == [True, True], "the Idle button should show the list, focus on the idle row: %r" % landed)
    except E2EError:
        sys.stderr.write("--- inbox stderr ---\n" + "".join(inbox_err))
        raise
    finally:
        kill(browser.proc)
        end_inbox(inbox, script, env, home)


def run_offer(work):
    """An answer from a notification, as the app will ask for it on the control socket: offer names the round's one
    undecided item; open with the offer gives a start link whose page opens that round's item in Focus; accept saves
    the recommendation with Done, and the waiting run exits 0 with its answers path, the source "notification"."""
    sys.path.insert(0, str(ROOT / "plugin" / "signoff"))
    import signoff
    home, topic = work / "offer-home", work / "offer-topic"
    topic.mkdir()
    data = json.loads((DEMO / "round-04.json").read_text(encoding="utf-8"))
    check(len(data["items"]) >= 2, "round-04.json should have two items or more")
    target = data["items"][1]
    check("recommended" in target and not target.get("sensitive"), "the offered item needs a recommendation")
    first = data["items"][0]
    first_choice = first["options"][0]["id"]
    (topic / "round-01.json").write_text(json.dumps(data), encoding="utf-8")
    # Every item but the target decided by the user, so the target is the one a tap may answer.
    answers = {it["id"]: {"choice": [it["options"][0]["id"]] if it.get("select") == "many" else it["options"][0]["id"],
                          "comment": "", "source": "user"} for it in data["items"] if it is not target}
    (topic / "round-01.answers.json").write_text(json.dumps({
        "version": 1, "done": False, "saved": "2026-01-01T12:00:00Z", "answers": answers, "free_text": ""}),
        encoding="utf-8")
    env = dict(os.environ, SIGNOFF_INBOX_HOME=str(home))
    script = [sys.executable, "-I", "-B", str(ROOT / "plugin" / "signoff" / "signoff.py")]
    inbox_err, out, err = [], [], []
    inbox = spawn(script + ["inbox", "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                  universal_newlines=True, encoding="utf-8", errors="replace")
    threading.Thread(target=pump, args=(inbox.stderr, inbox_err), daemon=True).start()

    def ask(op, **fields):
        check(inbox.poll() is None, "the inbox exited: %s" % "".join(inbox_err))
        try:
            return signoff.inbox_request(home, op, **fields)
        except OSError:
            return None
    wait_for("the inbox's control socket", lambda: ask("offer"))
    waiter = spawn(script + [str(topic), "--no-open"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   universal_newlines=True, encoding="utf-8", errors="replace")
    for stream, sink in ((waiter.stdout, out), (waiter.stderr, err)):
        threading.Thread(target=pump, args=(stream, sink), daemon=True).start()
    browser = Browser(str(work / "profile-offer"))
    try:
        offer = wait_for("the waiting round's offer", lambda: (ask("offer") or {}).get("offer"))
        check(offer["question"] == target["title"], "the offer should name the undecided item: %r" % offer)
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        browser.call("Page.enable")
        browser.call("Page.navigate", url=ask("open", offer=offer["id"])["link"])
        shown = wait_for("the item in Focus", lambda: browser.js(
            "(function () { var c = document.querySelector('.item.cur[data-item]'); return c && "
            "document.body.classList.contains('focusing') && !c.hidden && c.offsetParent !== null && "
            "[c.dataset.item, location.hash, document.getElementById('panel-0').contains(document.activeElement),"
            " Array.prototype.map.call(document.querySelectorAll('[aria-current=true] .nl'), function (x) {"
            " return x.textContent; })]; })()"))
        check(shown == [target["id"], "#focus=0/" + target["id"], True, [target["title"]]],
              "open with the offer should land on its item in Focus: %r" % shown)
        # A hash that names nothing on the page is ignored: the page opens as usual, not in Focus.
        page = browser.js("location.href").split("#")[0]
        for bad in ("#focus=0/nope", "#focus=9/" + target["id"], "#focus=x/y", "#focus=0/<b>"):
            browser.call("Page.navigate", url=page + bad)  # a hash change only: the reload loads the page with it
            browser.js("window.e2eOld = true")
            browser.call("Page.reload")
            ready = wait_for("the page with %s" % bad, lambda: browser.js(
                "!window.e2eOld && document.querySelector('.item[data-item]') ? [document.body.classList.contains("
                "'focusing'), location.hash] : null"))
            check(ready[0] is False, "a hash naming nothing should not open Focus: %r %r" % (bad, ready))
        check(ask("accept", offer=offer["id"]) == {"ok": True}, "accept should save the recommendation")
        expected = [topic / "round-01.answers.json"]
        saved = finished(waiter, out, err, expected)[0]
        got = saved["answers"][target["id"]]
        check(saved["done"] is True and saved["accepted"] is True and got["source"] == "notification"
              and got["choice"] == target["recommended"], "the answers after accept: %r" % saved)
        kept = saved["answers"][first["id"]]["choice"]
        check(kept in (first_choice, [first_choice]), "the user's other answer should stay: %r" % kept)
        check(ask("accept", offer=offer["id"]) == {"error": "gone"}, "a second accept should be refused")
    except E2EError:
        sys.stderr.write("--- inbox stderr ---\n" + "".join(inbox_err) + "--- run stderr ---\n" + "".join(err))
        raise
    finally:
        kill(browser.proc)
        kill(waiter)
        end_inbox(inbox, script, env, home)


def run():
    work = Path(tempfile.mkdtemp(prefix="signoff-e2e-"))
    WORK.append(work)
    # A Signoff home that does not exist: page runs as with no inbox, even when the user's own inbox is open.
    os.environ["SIGNOFF_INBOX_HOME"] = str(work / "no-home")
    os.environ["XDG_CACHE_HOME"], os.environ["XDG_CONFIG_HOME"] = str(work / "no-cache"), str(work / "no-config")
    (work / ".git").mkdir()  # the project root, so the header can name it
    run_old_rounds(work)
    run_phone(work)
    run_single(work)
    run_history(work)
    run_readonly(work)
    run_table(work)
    run_attachment(work)
    if WINDOWS:
        print("SKIP: the inbox (it needs a Unix socket; not on Windows yet)", flush=True)
    else:
        run_inbox(work)
        run_inbox_search(work)
        run_live(work)
        run_offer(work)
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
        # The rounds are a vertical tablist in the sidebar; the open round's items are listed under them.
        side = browser.js("(function () { var t = document.getElementById('tabs'); return [!!t.closest('nav.side'), "
                          "t.getAttribute('aria-orientation'), t.checkVisibility(), Array.prototype.map.call("
                          "document.querySelectorAll('.outline .nl'), function (n) { return n.textContent; })]; })()")
        check(side == [True, "vertical", True, [it["title"] for it in rounds[0]["items"]]],
              "the sidebar should hold the rounds and round 1's items: %r" % side)
        # The recommendation sits in the card's margin, right of the question, at 1280 px.
        margin = browser.js("(function () { var c = document.querySelector('#panel-0 .item.has-margin'), "
                            "m = c.querySelector('.margin'), q = c.querySelector('.q'); return [m.checkVisibility(), "
                            "m.getBoundingClientRect().left >= q.getBoundingClientRect().right, "
                            "m.querySelector('.m-head').textContent]; })()")
        check(margin == [True, True, "Agent recommends"], "the margin recommendation at 1280 px: %r" % margin)
        browser.click("#tab-1")
        check(browser.js("!document.getElementById('panel-1').hidden && document.getElementById('panel-0').hidden && "
                         "document.getElementById('title').textContent") == rounds[1]["title"]
              and browser.js("document.querySelectorAll('.outline li').length") == len(rounds[1]["items"]),
              "a round in the sidebar should open it, with its items listed")
        browser.click("#tab-0")
        # Focus: f shows one item, j the next, Esc all of them again.
        cards = ("Array.prototype.filter.call(document.querySelectorAll('#panel-0 .item[data-item]'), "
                 "function (c) { return c.checkVisibility(); }).map(function (c) { return c.dataset.item; })")
        press(browser, "f", "KeyF", 70, text="f")
        check(browser.js(cards) == [items[0]["id"]] and browser.js("document.querySelector('.ib-fbar span').textContent")
              == "Item 1 of %d · Round 1 of 2" % len(items), "f should show the first item alone: %r" % browser.js(cards))
        press(browser, "j", "KeyJ", 74, text="j")
        check(browser.js(cards) == [items[1]["id"]] and browser.js(
              "document.querySelector('.outline [aria-current=true] .nl').textContent") == items[1]["title"],
              "j in Focus should show the second item, marked in the sidebar: %r" % browser.js(cards))
        press(browser, "Escape", "Escape", 27)
        check(browser.js(cards) == [it["id"] for it in items] and not browser.js(
              "document.body.classList.contains('focusing')"), "Esc should leave Focus")

        browser.click("#r0-i%d-o%d" % (index, option))
        check(browser.js("document.getElementById('r0-i%d-o%d').checked" % (index, option)),
              "the picked option %s is not checked after the click" % pick_id)
        comment = "Date and tag are enough for now."
        browser.click("#r0-i0-c")
        browser.call("Input.insertText", text=comment)
        # At 900 px the rounds are in a drawer: accepting still leaves focus in the next round, not on the page.
        browser.call("Emulation.setDeviceMetricsOverride", width=900, height=900, deviceScaleFactor=1, mobile=False)
        time.sleep(0.3)
        browser.click("#accept-0")
        wait_for("round 2 to open", lambda: browser.js("!document.getElementById('panel-1').hidden"))
        check(browser.js("document.getElementById('panel-1').contains(document.activeElement)"),
              "after Accept at 900 px focus should be in round 2: %r" % browser.js("document.activeElement.tagName"))
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        time.sleep(0.3)
        browser.click("#accept-1")
        wait_for("Done to be ready", lambda: browser.js("!document.getElementById('done').disabled"))
        # Both accepted: the Summary is open, its filter counts every item and the one changed pick.
        total = len(rounds[0]["items"]) + len(rounds[1]["items"])
        labels = wait_for("the Summary's filter", lambda: filter_labels(browser, "#panel-2") or None)
        check(labels == ["All %d" % total, "With comment 1", "Not the recommendation 1"], "summary filter shows %r" % labels)
        quotes = browser.js("Array.prototype.map.call(document.querySelectorAll('#panel-2 .summary-note'), "
                            "function (q) { return q.textContent; })")
        check(quotes == [comment], "the Summary should quote the comment, quotes %r" % quotes)
        # The sign-off sheet: each round's state as a word, the answer beside its question, and Done under a rule.
        sheet = browser.js("(function () { var s = document.querySelector('#panel-2 .ss'), li = s.querySelector("
                           "'.summary-list li'); return [Array.prototype.map.call(s.querySelectorAll('.ss-st'), "
                           "function (c) { return c.textContent; }), li.querySelector('.summary-choice')"
                           ".getBoundingClientRect().left > li.querySelector('.link').getBoundingClientRect().left, "
                           "s.querySelector('.summary-done .ss-note').textContent, "
                           "!!s.querySelector('.summary-done button.primary')]; })()")
        check(sheet == [["✓accepted", "✓accepted"], True, "2/2 accepted · Done sends every answer to the agent.", True],
              "the Summary should be the sign-off sheet: %r" % sheet)
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
        check(not STRAYS, "an inbox outlived its test in %s" % ", ".join(STRAYS))
    except E2EError as e:
        print("FAIL: %s" % e, file=sys.stderr)
        return 1
    finally:
        timer.cancel()
        cleanup()
    print("PASS: every round in examples/ (demo rounds, decisions.json, --history) shows its items, options, "
          "recommendations and answers in light and dark; a phone opens in Focus, Next, the margin above the options, "
          "Esc, Hungarian labels; "
          "1 round, comment quoted above Done, filter, Private hides it, one-click Done, exit 0; "
          "project name in the header and the tab title; --history search finds answers and comments, never a private one, "
          "and its picker previews in the tab only, and once it stops a picture that fails says so; "
          "table view: chips, toggle, a pick in the table saved and shown on the cards, exit 0; "
          "an attachment starts closed, opens, shows the file's text unrendered, exit 0; "
          + ("" if WINDOWS else "inbox: the empty state, the sidebar's view and project filters, "
             "each project's other open items closed behind a count row that opens them, "
             "a waiting round and a waiting todo item grouped by project under the summary line, its steps counted, "
             "its Markdown as text with one https "
             "link, Copy puts the exact command on the clipboard, a message typed on it is a request file and shows "
             "as sent, a plain item's row shows its first line and the next line opens its body with a working link, "
             "inline code has its own Copy and Copy text takes the whole item, a relative rm warns and an absolute one "
             "does not, "
             "a new waiting item shows in the tab title and, with text typed, a bar at the top of the list offers the "
             "reload (nothing reloads), then the page reloads itself keeping the open row, "
             "a chain with two waiting steps is one row counted once, its steps marked, Later shows as sent and is a "
             "later request, Open round opens the round it names, a reload keeps the open To do groups, closing the "
             "open row's group closes the row, Focus walks only the rows shown, "
             "every waiting row says how long ago, Newest first is kept in prefs.json, "
             "the search field filters every view as you type, ignoring case and accents, opens a closed To do group "
             "on a match, counts, clears with Esc or Clear back to the exact list, takes / and Cmd+K, walks the results "
             "with Enter, j and k, and survives a reload, on a phone too, "
             "Ctrl+, opens the Appearance picker: arrows preview, "
             "Esc goes back, Enter keeps the palette for the list and the round's page; Compact kept across a reload; "
             "j/k and Enter, Focus with f and Esc; at 390 px the sheet and the drawer, Esc and focus back; "
             "the round answered from the inbox wakes the waiting "
             "run, exit 0, then shows as sent; Scan this project adds it to Uptick's list and counts Not scanned down "
             "to 0 without a reload, a finding handed to the agent is a vuln request and not counted; "
             "live sessions: two waiting ones counted, the background one's claude attach copied exactly, the "
             "interactive one pointed to its terminal, an idle one dimmer in Idle and counted nowhere, a third "
             "session of another project reloads the page by itself under its own project's heading, the groups "
             "following the order; "
             "an answer from a notification: the socket's offer, its start link opens the item in Focus (a hash naming "
             "nothing is ignored), accept wakes the waiting run, exit 0, source notification; ") +
          "2 rounds in the sidebar with their items, the margin recommendation, Focus with f, j and Esc, "
          "a non-recommended pick, both accepted, Summary filter, the sign-off sheet, Done, exit 0, "
          "2 answers paths on stdout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
