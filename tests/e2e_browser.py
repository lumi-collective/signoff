#!/usr/bin/env python3
"""End to end: the real server and the real page in headless Chrome, on any OS with Chrome.

    python3 tests/e2e_browser.py            (from anywhere; CHROME=<path> if Chrome is not found)

It copies rounds of examples/demo into a temporary folder, starts `signoff.py --no-open` on it, opens the
single-use start link in headless Chrome over the DevTools protocol, and drives the page with real mouse clicks.
First one round: a non-recommended pick and a comment, the summary block above Done quotes the comment and its
filter works, Private (marked by the agent on one item, ticked by the user on the other) hides the comment in the
summary and the Markdown copy, unticking the agent's mark survives a reload, one click on Done. A round done
earlier shows Private without the untick hint. On a phone in Focus, the question, the recommendation and the first
option of each demo item show before any scrolling. An item with criteria shows its facts as chips; the Cards / Table
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
TOTAL_TIMEOUT = 420  # seconds for the whole run; a hang fails the job instead of eating minutes
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
        # Headless Chrome 158 leaves the page unfocused, and the clipboard then refuses writeText; element focus stays real.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)

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
# A round's context line at its longest (MAX_CONTEXT), with markup that must stay text.
CONTEXT = ("feat/parser session: splitting the tokenizer <b>into</b> a lexer and a parser, then the error messages "
           "for both, step 34")
CONTEXT_SHOWN = ("(function () { var p = document.querySelector('.rp:not([hidden]) > .context');"
                 " return !!p && p.checkVisibility() && p.textContent; })()")
# The open round's age as shown: the header's time, its 2-day pill and the note at the top of the round's panel (each
# false when not shown).
ROUND_AGE = """(function () {
  var t = document.getElementById('opened'), p = document.getElementById('old-round');
  var n = document.querySelector('.rp:not([hidden]) > .old-note');
  return {time: t.checkVisibility() && t.textContent, datetime: t.dateTime, title: t.title,
    pill: p.checkVisibility() && p.textContent, note: !!n && n.checkVisibility() && n.textContent};
})()"""
# A round's header: the title's shown width and whether all of it fits, the buttons and the progress pill whose
# text takes more than one line or that end past the header's right edge, the pairs of the time, the 2-day pill, the
# title and the controls whose boxes meet, the header's text lines that start with a separator, and its rows.
HEADER = """(function () {
  var h = document.querySelector('body > header'), t = document.getElementById('title'), hr = h.getBoundingClientRect();
  function shown(e) { return e.checkVisibility(); }
  var controls = Array.prototype.filter.call(h.querySelectorAll('button, .progress'), shown), broken = [];
  controls.forEach(function (e) {
    var r = document.createRange(); r.selectNodeContents(e);
    var rects = Array.prototype.filter.call(r.getClientRects(), function (x) { return x.width > 0; });
    if (rects.some(function (x) { return x.top >= rects[0].bottom - 1; })
        || e.getBoundingClientRect().right > hr.right + 0.5) broken.push(e.id || e.className);
  });
  var boxes = [document.getElementById('opened'), document.getElementById('old-round'), t].filter(shown)
    .concat(controls);
  var overlaps = [];
  boxes.forEach(function (a, i) { boxes.slice(i + 1).forEach(function (b) {
    var p = a.getBoundingClientRect(), q = b.getBoundingClientRect();
    if (p.left < q.right - 0.5 && q.left < p.right - 0.5 && p.top < q.bottom - 0.5 && q.top < p.bottom - 0.5)
      overlaps.push([a.id || a.className, b.id || b.className]);
  }); });
  // Every shown character and every ::before text, with its box and its block: one starts a line when nothing of
  // its block sits left of it on the same line.
  function block(e) { while (getComputedStyle(e).display.indexOf('inline') === 0) e = e.parentElement; return e; }
  var marks = [], w = document.createTreeWalker(h, NodeFilter.SHOW_TEXT), n;
  while ((n = w.nextNode())) {
    if (!shown(n.parentElement)) continue;
    for (var i = 0; i < n.data.length; i++) {
      if (!n.data[i].trim()) continue;
      var r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + 1);
      var b = r.getClientRects()[0];
      if (b && b.width) marks.push({c: n.data[i], r: b, b: block(n.parentElement)});
    }
  }
  Array.prototype.forEach.call(h.querySelectorAll('*'), function (e) {
    var c = getComputedStyle(e, '::before').content.replace(/^"|"$/g, '').trim(), b = e.getClientRects()[0];
    if (c && c !== 'none' && c !== 'normal' && shown(e) && b) marks.push({c: c[0], r: b, b: block(e)});
  });
  var dots = marks.filter(function (m) {
    return '·•|'.indexOf(m.c) >= 0 && !marks.some(function (o) {
      return o !== m && o.b === m.b && o.r.left < m.r.left - 0.5 && o.r.top < m.r.bottom && m.r.top < o.r.bottom; });
  }).map(function (m) { return m.b.id || m.b.className; });
  var parts = Array.prototype.filter.call(h.children, shown).map(function (e) { return e.getBoundingClientRect(); });
  var rows = parts.filter(function (a, i) { return !parts.slice(0, i).some(function (b) {
    return a.top < b.bottom && b.top < a.bottom; }); }).length;
  return {title: t.clientWidth, fits: t.scrollWidth <= t.clientWidth, broken: broken, overlaps: overlaps, dots: dots,
    rows: rows};
})()"""
# The item in Focus as it shows before any scrolling: main's box from the header's foot to the key bar's top, and
# where the item's parts sit in it (null when the item has none).
FIRST_SCREEN = """(function () {
  var main = document.querySelector('body > main'), c = document.querySelector('.item.cur');
  main.scrollTop = 0;
  function box(e) { if (!e) return null; var r = e.getBoundingClientRect(); return {top: r.top, bottom: r.bottom}; }
  return {item: c.dataset.item, top: main.getBoundingClientRect().top, foot: document.querySelector('.bar').getBoundingClientRect().top,
    legend: box(c.querySelector('legend')), desc: box(c.querySelector('.desc')), margin: box(c.querySelector('.margin')),
    frame: box(c.querySelector('.frame')), choices: box(c.querySelector('.choices')), option: box(c.querySelector('.choices .opt')),
    links: Array.prototype.map.call(c.querySelectorAll('a.frame img'), function (i) { return i.getBoundingClientRect().height; }),
    wide: document.documentElement.scrollWidth <= innerWidth};
})()"""


def in_order(b):
    """The phone's reading order in Focus: the description, the recommendation, the pictures, then the options."""
    seq = [b["legend"]["bottom"], b["desc"]["bottom"], b["margin"]["top"], b["margin"]["bottom"]]
    seq += [b["frame"]["top"]] if b["frame"] else []
    return all(x <= y + 0.5 for x, y in zip(seq + [b["choices"]["top"]], (seq + [b["choices"]["top"]])[1:]))


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
    data["context"] = CONTEXT
    (topic / "decisions.json").write_text(json.dumps(data), encoding="utf-8")
    old = time.time() - 3 * 86400  # asked three days ago: the header's time, its pill and the panel's note
    os.utime(str(topic / "decisions.json"), (old, old))
    shown = ("Array.prototype.filter.call(document.querySelectorAll('#panel-0 .item[data-item]'), "
             "function (c) { return c.checkVisibility(); }).length")
    server, out, err, browser = serve(topic / "decisions.json", str(work / "profile-phone"), size=(390, 844))
    try:
        wait_for("the page", lambda: browser.js("!!document.getElementById('r0-i0-o0')"))
        state = browser.js("[document.body.classList.contains('focusing'), %s, document.querySelector('.ib-fbar span')"
                           ".textContent, document.querySelector('.tb-btn').getAttribute('aria-pressed')]" % shown)
        check(state == [True, 1, "1. tétel / %d" % len(data["items"]), "true"],
              "a phone should open in Focus on the first item: %r" % state)
        place = browser.js(FIRST_SCREEN)
        check(in_order(place), "on a phone in Focus: the text, the margin, the pictures, the options: %r" % place)
        text = browser.js("document.body.innerText")
        check("undefined" not in text and "Fókusz" in text and "Javasolt".upper() in text.upper(),
              "a Hungarian round should show Hungarian labels: %r" % text[:400])
        age = browser.js(ROUND_AGE)
        check(age["time"].startswith("Megnyitva: ") and age["time"].endswith(" · 3 napja") and age["datetime"]
              == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(old))) and age["title"]
              and age["pill"] == "2+ napja nyitva" and age["note"].startswith("A kérdései elavulhattak"),
              "an old Hungarian round in Focus: its time, pill and note: %r" % age)
        check(browser.js(CONTEXT_SHOWN) == CONTEXT, "the context line shows with the first item in Focus")
        browser.click(".ib-fbar button:nth-of-type(2)")  # Next
        check(browser.js("document.querySelector('.item.cur') === document.querySelectorAll('#panel-0 .item')[1] && %s"
                         % shown) == 1, "Next should show the second item alone")
        check(browser.js(CONTEXT_SHOWN) is False, "on a phone in Focus the context stays off the later items")
        press(browser, "Escape", "Escape", 27)
        check(browser.js("!document.body.classList.contains('focusing') && " + shown) == len(data["items"]),
              "Esc should leave Focus and show every item")
        check(browser.js(CONTEXT_SHOWN) == CONTEXT, "out of Focus the context line shows")
        check(browser.js("document.documentElement.scrollWidth <= innerWidth"), "nothing wider than the phone")
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)


HEADER_WIDTHS = (761, 768, 800, 900, 1000, 1001, 1100, 1280, 1440)


def run_header(work):
    """A round's header from 761 to 1440 px, the sidebar a drawer up to 1000 px and beside it above, on the demo's
    round 2 with a long title, opened three days ago in a project, in English and Hungarian: the title keeps its
    room, each control and the progress pill one line inside the header, nothing covers anything, no line starts with
    a separator, and at 1440 px it is one row (at 1280 px too with macOS's font; with a wider one such as Verdana,
    two). Returns {lang: {width: header}} for a report."""
    seen = {}
    for lang in ("en", "hu"):
        topic = work / "notebook-app" / ".signoff" / ("2026-01-06-header-" + lang)
        (work / "notebook-app" / ".git").mkdir(parents=True, exist_ok=True)
        for name in ("mockups", "boards", "notes"):
            shutil.copytree(str(DEMO / name), str(topic / name))
        old = time.time() - 3 * 86400
        for n in range(1, 5):
            data = json.loads((DEMO / ("round-%02d.json" % n)).read_text(encoding="utf-8"))
            data["lang"] = lang
            if n == 2:  # too long for any width, so the title's room is measured
                data["title"] += (", with the empty state, the filters and the sort order of every screen, and what "
                                  "each one says while the notes load")
            path = topic / ("round-%02d.json" % n)
            path.write_text(json.dumps(data), encoding="utf-8")
            os.utime(str(path), (old, old))
        server, out, err, browser = serve(topic, str(work / ("profile-header-" + lang)))
        try:
            wait_for("the page", lambda: browser.js("!!document.getElementById('tab-1')"))
            browser.click("#tab-1")
            age = browser.js(ROUND_AGE)
            ago, pill = {"en": ("3 days ago", "Open 2+ days"), "hu": ("3 napja", "2+ napja nyitva")}[lang]
            check(age["time"] and age["time"].endswith(ago) and age["pill"] == pill
                  and browser.js("document.getElementById('project').textContent") == "notebook-app",
                  "%s: the header shows the project, its time and the pill: %r" % (lang, age))
            seen[lang] = {}
            for width in HEADER_WIDTHS:
                browser.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=1,
                             mobile=False)
                time.sleep(0.3)
                head = seen[lang][width] = browser.js(HEADER)
                check((head["fits"] or head["title"] >= 200) and head["broken"] == [] and head["overlaps"] == []
                      and head["dots"] == [] and (width != 1440 or head["rows"] == 1),
                      "%s: the header at %d px: %r" % (lang, width, head))
        except E2EError:
            sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
            raise
        finally:
            kill(server)
            kill(browser.proc)
    return seen


def run_first_screen(work):
    """On a phone in Focus, before any scrolling, each item of the demo's round 2 shows its question, the agent's
    recommendation and its first option between the header and the key bar: a picture that takes marks, one without
    a picture, two pictures that open full size. In English and in Hungarian, whose labels are longer. With a context
    line, the agent's mark and a command on the first item, the question and the recommendation still do."""
    for lang, more in (("en", False), ("hu", False), ("en", True), ("hu", True)):
        topic = work / ("2026-01-04-first-screen-%s%s" % (lang, "-more" if more else ""))
        for name in ("mockups", "boards", "notes"):
            shutil.copytree(str(DEMO / name), str(topic / name))
        for n in range(1, 5):
            data = json.loads((DEMO / ("round-%02d.json" % n)).read_text(encoding="utf-8"))
            data["lang"] = lang
            if n == 2:
                items = data["items"]
                if more:
                    data["context"] = CONTEXT
                    items[0].update(blocking=True, commands=COMMANDS[:1])
                    del items[1:]
            (topic / ("round-%02d.json" % n)).write_text(json.dumps(data), encoding="utf-8")
        server, out, err, browser = serve(topic, str(work / ("profile-first-%s%s" % (lang, more))), size=(390, 844))
        try:
            wait_for("the page", lambda: browser.js("!!document.getElementById('r0-i0-o0')"))
            browser.js("location.hash = 'focus=1/%s'" % items[0]["id"])
            browser.call("Page.reload")
            for k, item in enumerate(items):
                if k:
                    browser.click(".ib-fbar button:nth-of-type(2)")  # Next
                wait_for("item %s in Focus" % item["id"], lambda: browser.js(
                    "(function (c) { return !!c && c.dataset.item; })(document.querySelector('#panel-1 .item.cur'))")
                    == item["id"])
                wait_for("the pictures of %s" % item["id"], lambda: browser.js(
                    "Array.prototype.every.call(document.querySelectorAll('.item.cur .frame img'), "
                    "function (i) { return i.complete && i.naturalHeight > 0; })"))  # an unloaded picture is 0 px high
                box = browser.js(FIRST_SCREEN)
                # The options may start below the first screen only under commands and a long context.
                end = box["margin"]["bottom"] if more else box["option"]["bottom"]
                check(in_order(box) and box["legend"]["top"] >= box["top"] and end <= box["foot"],
                      "%s, %s%s: the question, the recommendation%s on the first screen: %r"
                      % (lang, item["id"], " with more" if more else "", "" if more else " and the first option", box))
                if more:
                    check(browser.js(CONTEXT_SHOWN) == CONTEXT and browser.js(
                        "!!document.querySelector('.item.cur legend .badge.warn') && "
                        "!!document.querySelector('.item.cur .cmds .md-code')"), "the context, the mark and a command show")
                    continue
                check(all(h <= 180 for h in box["links"]), "%s: a picture that opens full size is a thumbnail: %r"
                      % (item["id"], box["links"]))
                check(box["wide"], "nothing wider than the phone")
                if item.get("marks"):
                    # A picture that takes marks starts small; Larger gives it the item's width to draw on.
                    size = ("(function () { var b = document.querySelector('.item.cur .pic-size'), f = b.closest('figure')"
                            ".querySelector('.frame'), s = b.closest('.shots'); return [b.textContent, "
                            "b.getAttribute('aria-expanded'), Math.round(f.getBoundingClientRect().width), "
                            "Math.round(s.getBoundingClientRect().width)]; })()")
                    labels = {"en": ("Larger", "Smaller", ": picture 1"), "hu": ("Nagyobb", "Kisebb", ": 1. kép")}[lang]
                    small = browser.js(size)
                    check(small[:2] == [labels[0], "false"] and small[2] < small[3] / 2,
                          "%s: the picture with marks starts small: %r" % (lang, small))
                    check(ax_name(browser, ".item.cur .pic-size") == labels[0] + labels[2],
                          "%s: Larger names its picture: %r" % (lang, ax_name(browser, ".item.cur .pic-size")))
                    # A mark drawn on the small picture is a mark like any other.
                    layer = browser.js("(function () { var r = document.querySelector('.item.cur svg.marks')"
                                       ".getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })()")
                    x0, y0 = layer[0] + layer[2] * 0.3, layer[1] + layer[3] * 0.3
                    x1, y1 = layer[0] + layer[2] * 0.7, layer[1] + layer[3] * 0.7
                    for kind, x, y in (("mousePressed", x0, y0), ("mouseMoved", x1, y1), ("mouseReleased", x1, y1)):
                        browser.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1,
                                     buttons=0 if kind == "mouseReleased" else 1)
                    wait_for("the mark drawn on the thumbnail", lambda: browser.js(
                        "document.querySelectorAll('.item.cur .mark-row').length === 1 && "
                        "document.querySelectorAll('.item.cur svg.marks .mark').length === 1"))
                    browser.click(".item.cur .pic-size")
                    big = browser.js(size)
                    check(big[:2] == [labels[1], "true"] and big[2] >= big[3] - 2
                          and ax_name(browser, ".item.cur .pic-size") == labels[1] + labels[2],
                          "%s: Larger gives the picture the item's width: %r" % (lang, big))
                    check(browser.js("document.activeElement === document.querySelector('.item.cur .pic-size')"),
                          "the toggle keeps focus")
                    press(browser, "Enter", "Enter", 13, text="\r")
                    check(browser.js(size) == small, "%s: Smaller gives the thumbnail back: %r" % (lang, browser.js(size)))
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
        # The line under the project's name says when the round was asked: a fresh one, with no pill and no note.
        age = browser.js(ROUND_AGE)
        check(re.fullmatch(r"Opened .+ · (just now|\d+ min ago)", age["time"]) and re.fullmatch(
              r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", age["datetime"]) and age["title"] and age["pill"] is False
              and age["note"] is False and browser.js("document.getElementById('project-line').innerText")
              == work.name + "\n" + age["time"], "a fresh round's opening time in the header: %r" % age)
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
    old = time.time() - 3 * 86400
    for name in ("round-01.json", "round-02.json"):
        os.utime(str(topic / name), (old, old))
    server, out, err, browser = serve(topic, str(work / "profile-readonly"))
    try:
        wait_for("the page", lambda: browser.js("!!document.getElementById('r1-i0-p')"))
        state = browser.js("[document.querySelectorAll('#panel-0 .sens').length, document.getElementById('r0-i0-p').checked, "
                           "document.getElementById('r0-i0-p').disabled, document.querySelectorAll('#panel-1 .sens').length]")
        check(state == [0, True, True, 1], "a done round should show Private without the hint: %r" % state)
        # Both asked three days ago: the open round warns, the done one shows only its time.
        age = browser.js(ROUND_AGE)
        check(age["pill"] == "Open 2+ days" and age["note"], "the old open round should warn: %r" % age)
        browser.click("#tab-0")
        age = browser.js(ROUND_AGE)
        check(re.fullmatch(r"Opened .+ · 3 days ago", age["time"]) and age["pill"] is False and age["note"] is False,
              "a done round shows its time and no warning: %r" % age)
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)


def run_old_mark(work):
    """An open round whose answers file holds a mark `signoff elsewhere` wrote before it went: the page shows the
    agent's note in the item's comment box, as any saved comment; a save of another item sends the mark back as it
    was; a pick on it makes it the user's."""
    topic = work / "2026-01-08-old-mark"
    topic.mkdir()
    target = topic / "decisions.json"

    def item(qid, select="one"):
        return {"id": qid, "title": "Question " + qid, "select": select, "recommended": ["a"] if select == "many"
                else "a", "options": [{"id": "a", "label": "Alpha"}, {"id": "b", "label": "Beta"}]}
    target.write_text(json.dumps({"version": 1, "title": "Settled in chat", "items": [
        item("q"), item("m", "many"), item("r")]}), encoding="utf-8")
    answers_file = topic / "decisions.answers.json"
    note = "Settled in chat: <b>Alpha</b>"
    marks = {"q": {"choice": None, "comment": note, "source": "elsewhere"},
             "m": {"choice": [], "comment": "", "source": "elsewhere"}}
    answers_file.write_text(json.dumps({"version": 1, "done": False, "saved": "2026-01-08T10:00:00Z",
                                        "free_text": "", "answers": marks}), encoding="utf-8")

    def saved():
        try:
            return json.loads(answers_file.read_text(encoding="utf-8"))["answers"]
        except (OSError, ValueError, KeyError):
            return {}
    server, out, err, browser = serve(target, str(work / "profile-old-mark"))
    try:
        wait_for("the page", lambda: browser.js("!!document.getElementById('r0-i0-c')"))
        shown = browser.js("[document.getElementById('r0-i0-c').value, document.getElementById('r0-i1-c').value, "
                           "document.querySelectorAll('#panel-0 input:checked').length, "
                           "document.body.textContent.indexOf('Decided elsewhere')]")
        check(shown == [note, "", 1, -1], "the note is in the comment box, nothing else shows the mark: %r" % shown)
        browser.click("#r0-i2-o1")  # another item's option
        wait_for("the other item saved", lambda: saved().get("r", {}).get("choice") == "b")
        check({k: saved()[k] for k in marks} == marks, "a save sends the marks back as they were: %r" % saved())
        browser.click("#r0-i0-o1")  # a pick on it is the user's
        wait_for("the marked item saved as the user's", lambda: saved().get("q", {}).get("source") == "user")
        check(saved()["q"] == {"choice": "b", "comment": note, "source": "user"}, "the user's answer: %r" % saved())
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
    second.update(other=True, marks=True, images=picture, attachments=[{"src": "notes/later.patch", "open": True}])
    (old / "notes").mkdir()
    (old / "notes" / "later.patch").write_text(PLAIN_PATCH, encoding="utf-8")
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
        # Its rows come with the toggle event its open attribute fires, a task after the round is shown.
        shown = wait_for("the open box's rows", lambda: (lambda b: any(x["rows"] for x in b) and b)(
            browser.js(ATTACH_BOXES % json.dumps("details.attach"))))
        # Each round holds it; the quiet round's file is not there, so it says so, open all the same.
        drawn = [b for b in shown if b["rows"]]
        check(len(shown) == 2 and all(b["open"] for b in shown) and len(drawn) == 1
              and len(drawn[0]["rows"]) == len(PLAIN_PATCH.splitlines()),
              "an open attachment should start open on the past-decisions page too: %r" % shown)
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


# A synthetic unified diff and the rows the page must draw from it: class, old number, new number, sign (with its
# word for screen readers), text. A header or hunk line is one row with its whole line as its text.
DIFF_ROWS = [("file", "", "", "", "diff --git a/export.py b/export.py"), ("file", "", "", "", "index 1111111..2222222 100644"),
             ("file", "", "", "", "--- a/export.py"), ("file", "", "", "", "+++ b/export.py"),
             ("hunk", "", "", "", "@@ -1,3 +1,5 @@ def export(rows):"), ("ctx", "1", "1", " ", "def export(rows):"),
             ("del", "2", "", "-removed", "    return to_csv(rows)"), ("add", "", "2", "+added", "    if not rows:"),
             ("add", "", "3", "+added", '        return ""'), ("add", "", "4", "+added", "    return to_csv(rows)  # <b>kept</b>"),
             ("ctx", "3", "5", " ", ""), ("hunk", "", "", "", "@@ -10,2 +12,2 @@"), ("del", "10", "", "-removed", '    sep = ";"'),
             ("add", "", "12", "+added", '    sep = ","'), ("ctx", "11", "13", " ", "    return sep.join(rows)"),
             ("hunk", "", "", "", "\\ No newline at end of file")]
DIFF_TEXT = "".join(r[3][:1] + r[4] + "\n" if r[0] in ("add", "del", "ctx") else r[4] + "\n" for r in DIFF_ROWS)
# Not a diff: a hunk header without its numbers, a valid hunk with no +++ line before it, and after one a header of
# long digit runs that never closes (a pattern that backtracks would hang on it). It shows as numbered plain text.
PLAIN_PATCH = ("Not a diff, only notes.\n- a dash line\n+ a plus line\n@@ looks like a hunk @@\n@@ -1 +1 @@\n-a\n+b\n"
               "+++ b/notes\n@@ -%s +%s x\n" % ("1" * 10000, "2" * 10000))
# Each fails only the count rule and shows as numbered plain text: a hunk that claims more lines than it has, one
# with a stray line inside, one that claims fewer (its extra line comes straight after it), one whose stray line is
# its last, and one that claims fewer with a "\ No newline" line before the extra one.
NOT_DIFFS = ["--- a/x\n+++ b/x\n@@ -1,3 +1,3 @@\n a\n-b\n+c\n", "--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n a\nx stray\n-b\n+c\n",
             "--- a\n+++ b\n@@ -1 +1 @@\n-a\n+b\n+c\n", "--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n a\n-b\nx stray\n",
             "--- a\n+++ b\n@@ -1 +1 @@\n-a\n+b\n\\ No newline at end of file\n+c\n"]
# Two diff -u outputs back to back, with no diff line between: the --- line after a full hunk starts the next file.
CONCAT_DIFF = "--- a\n+++ b\n@@ -1 +1 @@\n-a\n+b\n--- c\n+++ d\n@@ -1 +1 @@\n-x\n+y\n"
CONCAT_CLASSES = ["file", "file", "hunk", "del", "add"] * 2
# Six-digit numbers and lines as in a lockfile.
FAR_DIFF = ('--- a/lock\n+++ b/lock\n@@ -123456,3 +123456,3 @@\n   "lodash": {\n-    "version": "4.17.20",\n'
            '+    "version": "4.17.21",\n     "dev": false\n')
# git format-patch output: a mail head, a diffstat, two files and the "-- " trailer. It is a diff, and its blank lines
# outside a hunk keep their height.
MAIL_PATCH = ("From 0000000 Mon Sep 17 00:00:00 2001\nSubject: [PATCH] Skip empty exports\n\n---\n export.py | 2 +-\n"
              " 1 file changed, 1 insertion(+), 1 deletion(-)\n\ndiff --git a/export.py b/export.py\n--- a/export.py\n"
              "+++ b/export.py\n@@ -1,2 +1,2 @@\n def export(rows):\n-    pass\n+    return rows\n"
              "diff --git a/notes.md b/notes.md\n--- a/notes.md\n+++ b/notes.md\n@@ -1 +1 @@\n-old\n+new\n-- \n2.39.0\n")
MAIL_CLASSES = ["file"] * 10 + ["hunk", "ctx", "del", "add"] + ["file"] * 3 + ["hunk", "del", "add", "file", "file"]
LONG_TEXT = "".join("line %d\n" % n for n in range(1, 302))  # 301 rows: a second row group, drawn later
BIG_ROWS = (256 * 1024 - 64) // 2  # one-character lines: the worst case within the 256 KiB limit
BIG_DIFF = "--- a/big\n+++ b/big\n@@ -0,0 +1,%d @@\n" % BIG_ROWS + "+\n" * BIG_ROWS


def marked(line):
    """A line as its text cell shows it: what would break it in pre-wrap without ending it by the line rule is a mark."""
    return re.sub("[\r\v\f\x85\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}]", "↵", line)


# Each attachment box of an item: open or not, the summary, the headers, and each row as class and cell texts.
ATTACH_BOXES = """(function () {
  return Array.prototype.map.call(document.querySelectorAll(%s), function (d) {
    var t = d.querySelector('[role=table]'), pre = d.querySelector('pre');
    var rows = t && t.querySelectorAll('[role=rowgroup] > [role=row]');
    return {open: d.open, label: d.querySelector('summary').textContent, pre: pre && pre.textContent, high: t && t.scrollHeight,
      heads: t && Array.prototype.map.call(t.querySelectorAll('[role=columnheader]'), function (c) { return c.textContent; }),
      words: t && t.querySelectorAll('[role=cell] > .visually-hidden').length,
      rows: t && rows.length <= 100 ? Array.prototype.map.call(rows, function (r) {
        return [r.className].concat(Array.prototype.map.call(r.querySelectorAll('[role=cell]'), function (c) { return c.textContent; }));
      }) : null};
  });
})()"""


def run_attachment(work):
    """An attachment shows closed, opens on a click, and holds the file's text exactly, nothing rendered. A diff
    draws as numbered, marked rows at its first opening, one that does not parse as numbered text, an open box starts
    open, an item with lines numbers a text file by the server's line rule, and the largest one draws its first
    screen at once and the rest while the page still answers."""
    topic = work / "2026-01-09-export"
    topic.mkdir()
    shutil.copytree(str(DEMO / "notes"), str(topic / "notes"))
    rule = json.loads((ROOT / "tests" / "line_rule.json").read_text(encoding="ascii"))
    for name, body in (("change.diff", DIFF_TEXT), ("plain.patch", PLAIN_PATCH), ("rule.txt", rule["text"]),
                       ("big.diff", BIG_DIFF), ("over.diff", NOT_DIFFS[0]), ("stray.diff", NOT_DIFFS[1]),
                       ("under.diff", NOT_DIFFS[2]), ("mail.patch", MAIL_PATCH), ("long.txt", LONG_TEXT),
                       ("stray-end.diff", NOT_DIFFS[3]), ("eof.diff", NOT_DIFFS[4]), ("concat.diff", CONCAT_DIFF),
                       ("far.diff", FAR_DIFF)):
        (topic / "notes" / name).write_bytes(body.encode("utf-8"))
    check((topic / "notes" / "big.diff").stat().st_size <= 256 * 1024, "the big diff must stay within the limit")
    data = json.loads((DEMO / "round-03.json").read_text(encoding="utf-8"))
    data["items"] = [it for it in data["items"] if it.get("attachments")]
    data["items"].append({"id": "change", "title": "Ship the export change?", "select": "one", "recommended": "ok",
                          "options": [{"id": "ok", "label": "Ship it"}, {"id": "no", "label": "Not yet"}], "lines": True,
                          "attachments": [{"src": "notes/change.diff", "caption": "The change"},
                                          {"src": "notes/plain.patch", "open": True}, {"src": "notes/rule.txt"},
                                          {"src": "notes/big.diff", "caption": "Everything"}]
                          + [{"src": "notes/" + name, "open": True}
                             for name in ("over.diff", "stray.diff", "under.diff", "mail.patch", "long.txt",
                                          "stray-end.diff", "eof.diff", "concat.diff", "far.diff")]})
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
        boxes = '[data-item="change"] details.attach'
        shown = wait_for("the open boxes' rows", lambda: (lambda b: b[-1]["high"] is not None and b)(
            browser.js(ATTACH_BOXES % json.dumps(boxes))))
        diff, plain, ruled, big, over, stray, under, mail, long_, stray_end, eof, concat, far = shown
        check([r[2] for r in far["rows"]][3:] == ["123456", "123457", "", "123458"], "a far hunk's numbers: %r" % far)
        check(not diff["open"] and diff["rows"] is None and not ruled["open"] and ruled["rows"] is None
              and big["rows"] is None, "a closed box should have no rows until it opens: %r" % [diff, ruled, big])
        for got, want in ((plain, PLAIN_PATCH), (over, NOT_DIFFS[0]), (stray, NOT_DIFFS[1]), (under, NOT_DIFFS[2]),
                          (stray_end, NOT_DIFFS[3]), (eof, NOT_DIFFS[4])):
            check(got["open"] and got["rows"] == [["", str(n), line] for n, line in enumerate(want.splitlines(), 1)]
                  and got["heads"] == ["Line", "Text"], "an open box should start open, a non-diff as numbered text: %r" % got)
        check([r[0] for r in mail["rows"]] == MAIL_CLASSES, "git format-patch output is a diff: %r" % mail["rows"])
        check([r[0] for r in concat["rows"]] == CONCAT_CLASSES, "two diff -u outputs back to back are a diff: %r"
              % concat["rows"])
        # Drawn where they are, on the screen or not: a box below the fold takes its rows' height, not a stand-in's,
        # its later row groups too.
        check(browser.js("document.querySelectorAll(%s)[1].getBoundingClientRect().top > innerHeight" % json.dumps(boxes))
              and plain["high"] < 20000, "an open box below the fold should take its rows' height: %r" % plain["high"])
        # 301 rows measure 5890 px; one stand-in for every group would make them 3920.
        check(5000 < long_["high"] < 20000, "a box of 301 rows below the fold should take about their height: %r"
              % long_["high"])
        # A blank line outside a hunk keeps a line's height, on a desktop and on a phone.
        blank = ("Array.prototype.filter.call(document.querySelectorAll(%s)[7].querySelectorAll('[role=row].file'), "
                 "function (r) { return !r.lastChild.textContent; }).map(function (r) { return r.offsetHeight; })"
                 % json.dumps(boxes))
        for width in (400, 1280):
            browser.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=1, mobile=False)
            time.sleep(0.3)
            high = browser.js(blank)
            check(len(high) == 2 and min(high) >= 15, "at %d px a blank line should keep its height: %r" % (width, high))
            head = browser.js("document.querySelectorAll(%s)[7].querySelector('[role=table] > [role=row]').offsetHeight"
                              % json.dumps(boxes))
            check(head == 0, "at %d px the hidden column names should take no height: %r" % (width, head))
            # Six-digit numbers beside the file's own line stay on one line: a wrapped number reads as a row.
            far_high = browser.js("Array.prototype.map.call(document.querySelectorAll(%s)[12].querySelectorAll("
                                  "'[role=rowgroup] > [role=row]:not(.hunk)'), function (r) { return r.offsetHeight; })"
                                  % json.dumps(boxes))  # the hunk line's own text may wrap at 400 px
            check(len(far_high) == 6 and max(far_high) < 25, "at %d px each row of a far hunk should be one line: %r"
                  % (width, far_high))
            # On a phone a noted diff keeps the file's line (the note's anchor) and drops the diff's own numbers,
            # headers too, so the text has room; on a desktop all three show.
            shown_n = browser.js("(function (b) { function on(list) { return Array.prototype.filter.call(list, function (c) { "
                                 "return getComputedStyle(c).display !== 'none'; }); } return [on(b.querySelectorAll("
                                 "'[role=rowgroup] > .del > .n')).map(function (c) { return c.textContent; }), "
                                 "on(b.querySelectorAll('[role=columnheader]')).length]; })(document.querySelectorAll(%s)[12])"
                                 % json.dumps(boxes))
            check(shown_n == ([["5"], 3] if width == 400 else [["5", "123457", ""], 5]),
                  "at %d px a noted diff's number columns: %r" % (width, shown_n))
        browser.click(boxes + ":nth-of-type(1) > summary")
        diff = browser.js(ATTACH_BOXES % json.dumps(boxes))[0]
        # Its item takes line notes: each row leads with its line in the file, the number a note anchors on.
        check(diff["open"] and diff["rows"] == [[r[0], str(n)] + list(r[1:]) for n, r in enumerate(DIFF_ROWS, 1)],
              "the diff's rows: %r" % diff["rows"])
        check(diff["heads"] == ["Line", "Old line", "New line", "Change", "Text"]
              and diff["words"] == sum(r[0] in ("add", "del") for r in DIFF_ROWS),
              "the diff's headers and its words for screen readers: %r" % diff)
        check("16 lines" in diff["label"] and browser.js("document.querySelectorAll('.attach b').length") == 0,
              "the diff's count, and nothing in it rendered: %r" % diff["label"])
        browser.click(boxes + ":nth-of-type(3) > summary")
        ruled = browser.js(ATTACH_BOXES % json.dumps(boxes))[2]
        check(ruled["rows"] == [["", str(n), marked(line)] for n, line in enumerate(rule["lines"], 1)]
              and "%d lines" % len(rule["lines"]) in ruled["label"], "the server's line rule on the page: %r" % ruled)

        # The worst case: the first screen at once, a key and a click answered while the rest draws.
        browser.js("""(function () {
          var b = document.querySelectorAll(%s)[3], s = window.bigBox = {};
          function rows() {
            return Array.prototype.reduce.call(b.querySelectorAll('[role=rowgroup]'), function (n, g) { return n + g.childElementCount; }, 0);
          }
          b.querySelector('summary').addEventListener('click', function () {
            s.t0 = performance.now();
            var last = s.t0; s.gap = 0;
            requestAnimationFrame(function frame() {
              var now = performance.now(), n = rows();
              s.gap = Math.max(s.gap, now - last); last = now;
              if (n && s.first === undefined) { s.first = now - s.t0; s.firstRows = n; }
              if (n === %d) { s.all = now - s.t0; return; }
              requestAnimationFrame(frame);
            });
          });
          window.bigRows = rows;
          document.addEventListener('keydown', function () { s.keyRows = rows(); }, true);
          document.addEventListener('mousedown', function (e) { if (!b.contains(e.target)) s.clickRows = rows(); }, true);
        })()""" % (json.dumps(boxes), BIG_ROWS + 3))
        x, y = browser.center(boxes + ":nth-of-type(4) > summary")
        # A click on the line-rule box's last row, above it on the screen: it does nothing but be answered.
        cx, cy = browser.js("(function () { var r = document.querySelectorAll(%s)[2].querySelector('[role=row]:last-child > :last-child')"
                            ".getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()" % json.dumps(boxes))
        check(0 < cy < y, "the line-rule box should be on the screen above the big one: %r" % [cy, y])
        for kind in ("mousePressed", "mouseReleased"):
            browser.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", buttons=1 if kind == "mousePressed" else 0,
                         clickCount=1)
        wait_for("the big diff's first rows", lambda: browser.js("window.bigRows()") > 0)
        t = time.monotonic()
        press(browser, "Shift", "ShiftLeft", 16)
        key_ms = (time.monotonic() - t) * 1000
        t = time.monotonic()
        for kind in ("mousePressed", "mouseReleased"):
            browser.call("Input.dispatchMouseEvent", type=kind, x=cx, y=cy, button="left", buttons=1 if kind == "mousePressed" else 0,
                         clickCount=1)
        click_ms = (time.monotonic() - t) * 1000
        s = wait_for("every row of the big diff", lambda: browser.js("window.bigBox.all !== undefined && window.bigBox"))
        check(0 < s["firstRows"] < BIG_ROWS + 3, "the first screen should come before the rest: %r" % s)
        check(0 < s.get("keyRows", 0) < BIG_ROWS + 3 and 0 < s.get("clickRows", 0) < BIG_ROWS + 3,
              "a key and a click should be answered while the rows draw: %r" % s)
        big = browser.js(ATTACH_BOXES % json.dumps(boxes))[3]
        check(big["open"] and big["words"] == BIG_ROWS, "every added row should carry its word: %r" % big["words"])
        last = browser.js("(function (r) { return [r.firstChild.textContent, r.offsetHeight]; })(document.querySelectorAll("
                          "%s)[3].querySelector('[role=rowgroup]:last-child > [role=row]:last-child'))" % json.dumps(boxes))
        check(last[0] == str(BIG_ROWS + 3) and last[1] < 25, "a six-digit line number should stay on one line: %r" % last)
        print("attachment of %d rows: first screen %.0f ms (%d rows), all rows %.0f ms, longest frame %.0f ms; "
              "a key answered in %.0f ms, a click in %.0f ms, both while it drew" % (BIG_ROWS + 3, s["first"], s["firstRows"],
                                                                                  s["all"], s["gap"], key_ms, click_ms), flush=True)
        browser.click("#done")
        finished(server, out, err, [topic / "decisions.answers.json"])
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)


def run_open_rounds(work):
    """Two open boxes of line feeds only, the most rows a file can make, in the second round: the page answers at load
    without drawing them, and they draw when their round is shown, the page answering while they do. A third such
    round holds 49 notes: leaving a round drops its rows (a box still drawing stops), and coming back draws them anew
    with a note typed and not yet saved; a round left still saves its notes; after both big rounds the page holds about one round's nodes."""
    topic = work / "2026-01-10-feeds"
    (topic / "notes").mkdir(parents=True)
    feeds = 256 * 1024  # one row per byte
    for name in ("a.txt", "b.txt"):
        (topic / "notes" / name).write_bytes(b"\n" * feeds)
    item = {"id": "pick", "title": "Pick one", "select": "one", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}
    (topic / "round-01.json").write_text(json.dumps({"version": 1, "title": "First", "items": [item]}), encoding="utf-8")
    for n in (2, 3):
        (topic / ("round-0%d.json" % n)).write_text(json.dumps({"version": 1, "title": "Feeds %d" % n, "items": [dict(
            item, lines=True, attachments=[{"src": "notes/a.txt", "open": True}, {"src": "notes/b.txt", "open": True}])]}),
            encoding="utf-8")
    third = topic / "round-03.answers.json"
    third.write_text(json.dumps({
        "version": 1, "done": False, "saved": "2026-01-10T10:00:00Z", "free_text": "",
        "decisions": {"file": "round-03.json", "sha256": hashlib.sha256((topic / "round-03.json").read_bytes()).hexdigest()},
        "answers": {"pick": {"choice": "a", "comment": "", "lines": [
            {"att": 0, "line": n, "quote": "", "comment": "n%d" % n} for n in range(1, 50)]}}}), encoding="utf-8")
    rows = ("Array.prototype.reduce.call(document.querySelectorAll('#panel-1 [role=rowgroup]'), "
            "function (n, g) { return n + g.childElementCount; }, 0)")
    rows3 = ("Array.prototype.reduce.call(document.querySelectorAll('#panel-2 [role=rowgroup]'), "
             "function (n, g) { return n + g.querySelectorAll(':scope > [role=row]:not(.lnote)').length; }, 0)")
    line50 = "document.querySelector('#panel-2 [data-att=\"0\"][data-line=\"50\"]')"

    def nodes():
        browser.call("HeapProfiler.collectGarbage")
        return browser.call("Memory.getDOMCounters")["nodes"]
    server, out, err, browser = serve(topic, str(work / "profile-feeds"))
    try:
        wait_for("the first round", lambda: browser.js("!!document.querySelector('#panel-0:not([hidden]) .item')"))
        time.sleep(1)  # time enough to draw rows it should not draw
        t = time.monotonic()
        press(browser, "Shift", "ShiftLeft", 16)
        load_ms = (time.monotonic() - t) * 1000
        check(browser.js(rows) == 0 and browser.js("document.querySelectorAll('#panel-1 details[open]').length") == 2,
              "the hidden round's open boxes should wait for their round: %r rows" % browser.js(rows))
        browser.js("""(function () {
          var s = window.feeds = {t0: performance.now(), gap: 0}, last = s.t0;
          function count() { return %s; }
          document.addEventListener('keydown', function () { s.keyRows = count(); }, true);
          requestAnimationFrame(function frame() {
            var now = performance.now(), n = count();
            s.gap = Math.max(s.gap, now - last); last = now;
            if (n && s.first === undefined) { s.first = now - s.t0; s.firstRows = n; }
            if (n === %d) { s.all = now - s.t0; return; }
            requestAnimationFrame(frame);
          });
          document.querySelectorAll('[role=tab]')[1].click();
        })()""" % (rows, 2 * feeds))
        wait_for("the second round's first rows", lambda: browser.js(rows) > 0)
        t = time.monotonic()
        press(browser, "Shift", "ShiftLeft", 16)
        key_ms = (time.monotonic() - t) * 1000
        s = wait_for("every row of both boxes", lambda: browser.js("window.feeds.all !== undefined && window.feeds"),
                     timeout=180)
        check(0 < s.get("keyRows", 0) < 2 * feeds, "a key should be answered while the rows draw: %r" % s)
        print("two open boxes of %d rows in a hidden round: a key at load answered in %.0f ms with none drawn; shown, "
              "first rows %.0f ms (%d rows), all rows %.0f ms, longest frame %.0f ms, a key answered in %.0f ms while they "
              "drew" % (feeds, load_ms, s["first"], s["firstRows"], s["all"], s["gap"], key_ms), flush=True)
        one = wait_for("one round's nodes", lambda: nodes() or None)
        # A round left while its rows draw stops drawing them.
        drop = browser.js("""(function () {
          var tabs = document.querySelectorAll('[role=tab]');
          tabs[2].click();
          window.left = document.querySelector('#panel-2 .att-lines');
          tabs[1].click();
          return window.left.querySelectorAll('[role=row]').length;
        })()""")
        time.sleep(1)
        rows_later = browser.js("window.left.querySelectorAll('[role=row]').length")
        browser.js("window.left = null")
        check(rows_later == drop, "a box dropped while it drew should stop drawing: %d rows, then %d" % (drop, rows_later))

        browser.js("document.querySelectorAll('[role=tab]')[2].click()")
        wait_for("the third round's rows", lambda: browser.js(rows3) == 2 * feeds, timeout=180)
        browser.js(line50 + ".click()")
        check(browser.js("document.activeElement.tagName === 'TEXTAREA' && document.querySelectorAll('#panel-2 .lnote')"
                         ".length === 50"), "the 50th press should open its field")
        browser.call("Input.insertText", text="typed, not saved")
        # Away and back in one task, before any save could go: the rows are drawn anew, the typed text under its line.
        back = browser.js("""(function () {
          document.activeElement.dataset.old = '1';
          var tabs = document.querySelectorAll('[role=tab]');
          tabs[1].click();
          tabs[2].click();
          var t = %s.closest('[role=row]').nextElementSibling.querySelector('textarea');
          return [t.value, t.dataset.old === undefined, document.querySelectorAll('#panel-2 .lnote').length];
        })()""" % line50)
        check(back == ["typed, not saved", True, 50], "a round shown again should draw its rows anew, the note typed "
              "and not yet saved under its line: %r" % back)
        # Drawn whole again, so a field kept from the page's notes would hold the whole box once it is left.
        wait_for("the third round's rows drawn again", lambda: browser.js(rows3) == 2 * feeds, timeout=180)
        browser.js("document.querySelector('#panel-2 .lnote button').click()")  # Remove line 1's note
        # Round 3 left, its rows dropped: its notes still save, the typed one with them.
        browser.js("document.querySelectorAll('[role=tab]')[1].click()")
        want = [{"att": 0, "line": n, "quote": "", "comment": "n%d" % n} for n in range(2, 50)]
        want.append({"att": 0, "line": 50, "quote": "", "comment": "typed, not saved"})
        wait_for("round 3's notes saved from round 2", lambda: json.loads(third.read_text(encoding="utf-8"))
                 ["answers"]["pick"].get("lines") == want, timeout=15)
        wait_for("the second round drawn again", lambda: browser.js(rows) == 2 * feeds, timeout=180)
        two = wait_for("the nodes after both rounds", lambda: (lambda n: n < 1.25 * one and n)(nodes()), timeout=30)
        check(browser.js("document.querySelectorAll('#panel-2 [role=row]').length") == 0,
              "a round left should hold no rows")
        print("a round of %d rows: %d DOM nodes, %d after the next round and back" % (2 * feeds, one, two), flush=True)
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)


# Each line note's field or text under the given item as [its line's number, the field's value or the text], and
# the "changed since" notes kept apart.
NOTES_SHOWN = """(function (sel) {
  var card = document.querySelector(sel);
  if (!card) return {under: [], apart: []};  // not drawn yet
  return {under: Array.prototype.map.call(card.querySelectorAll('[role=rowgroup] > .lnote'), function (n) {
      var row = n.previousElementSibling;
      while (row.classList.contains('lnote')) row = row.previousElementSibling;
      var t = n.querySelector('textarea'), p = n.querySelector('.mark-text');
      return [row.firstChild.textContent, t ? t.value : p.textContent];
    }),
    apart: Array.prototype.map.call(card.querySelectorAll('.lnote:not([role])'), function (n) {
      return [n.querySelector('.readonly-note').textContent, n.querySelector('.mark-text').textContent];
    }),
    fields: card.querySelectorAll('.lnote textarea').length, buttons: card.querySelectorAll('.att-lines button').length,
    hint: !!card.querySelector('.lfull')};
})(%s)"""


def run_line_notes(work):
    """Line notes: a press on a line's number opens a field under it, saved with the round in attachment and line
    order, its quote cut by code point as the server cuts it (the fixture's awkward lines too); at most 50 an item;
    Private hides them in the Summary; a reload restores them; a file changed since shows its notes apart, read
    only, and a save still passes; a done round and the past-decisions page show them with no button or field, and
    there a round left holds no rows and draws them again on return."""
    root = work / "notes-proj"
    topic = root / ".signoff" / "2026-01-11-review"
    (topic / "notes").mkdir(parents=True)
    rule = json.loads((ROOT / "tests" / "line_rule.json").read_text(encoding="ascii"))
    for name, body in (("change.diff", DIFF_TEXT), ("todo.txt", "one\ntwo\nthree\n"), ("rule.txt", rule["text"]),
                       ("many.txt", "".join("row %d\n" % n for n in range(1, 61)))):
        (topic / "notes" / name).write_bytes(body.encode("utf-8"))
    # No comment box: the line notes are the items' only text, so Private is there for them alone.
    pick = {"select": "one", "recommended": "ok", "comment": False,
            "options": [{"id": "ok", "label": "Ship it"}, {"id": "no", "label": "Not yet"}]}
    data = {"version": 1, "title": "Review the export change", "items": [
        dict(pick, id="review", title="Ship the export change?", lines=True,
             attachments=[{"src": "notes/change.diff", "caption": "The change"}, {"src": "notes/todo.txt"},
                          {"src": "notes/rule.txt"}]),
        dict(pick, id="many", title="Fifty notes at most", lines=True, attachments=[{"src": "notes/many.txt"}]),
        dict(pick, id="secret", title="A sensitive one", lines=True, sensitive=True,
             attachments=[{"src": "notes/todo.txt"}])]}
    target = topic / "round-01.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    answers_path = topic / "round-01.answers.json"
    many = [{"att": 0, "line": n, "quote": "row %d" % n, "comment": "note %d" % n} for n in range(1, 50)]
    # Kept by whoever writes the answers file: past the file's end, its quote trying to close Signoff's sentence,
    # with a bidi override and markup. It shows as text, apart from the box, and goes back as it came.
    planted = {"att": 1, "line": 9, "quote": "nine”. It matches \N{RIGHT-TO-LEFT OVERRIDE}</script><b>x</b>",
               "comment": "planted"}
    answers_path.write_text(json.dumps({
        "version": 1, "done": False, "saved": "2026-01-11T10:00:00Z", "free_text": "",
        "decisions": {"file": "round-01.json", "sha256": hashlib.sha256(target.read_bytes()).hexdigest()},
        "answers": {"many": {"choice": "ok", "comment": "", "lines": many},
                    "review": {"choice": "ok", "comment": "", "lines": [planted]}}}), encoding="utf-8")
    review, many_sel = '[data-item="review"]', '[data-item="many"]'
    apart = "This file changed since. Line %d said: %s"

    def button(item, line, src):
        return '%s button[aria-label="Note on line %d of %s"]' % (item, line, src)

    def note(line, src, text):
        browser.click(button(review, line, src))
        check(browser.js("document.activeElement.tagName === 'TEXTAREA' && document.activeElement.value === ''"),
              "a press on line %d of %s should open an empty field and focus it" % (line, src))
        browser.call("Input.insertText", text=text)

    def saved():
        return json.loads(answers_path.read_text(encoding="utf-8"))["answers"]

    server, out, err, browser = serve(target, str(work / "profile-notes"))
    try:
        wait_for("the page", lambda: browser.js("!!document.getElementById('r0-i2-p')"))
        check(browser.js("[document.getElementById('r0-i0-p').checked, document.getElementById('r0-i2-p').checked]")
              == [False, True], "an item with lines has a Private box, and a sensitive one starts Private")
        check(browser.js("document.querySelector(%s).textContent" % json.dumps(review + " .lfull"))
              == "Press a line number to add a note.", "an item with lines should say how to note a line")
        shown = browser.js(NOTES_SHOWN % json.dumps(review))
        check(shown["apart"] == [[apart % (9, planted["quote"]), "planted"]] and browser.js(
              "[document.querySelectorAll('main b, main script').length, document.querySelector(%s).textContent]"
              % json.dumps(review + " .lnote code")) == [0, planted["quote"]],
              "a planted note should show apart, its quote as text in an element of its own: %r" % shown["apart"])
        # 49 notes saved before: restored under their lines in a box opened for them. The 50th opens, the 51st is refused.
        shown = wait_for("the restored notes", lambda: (lambda s: len(s["under"]) == 49 and s)(
            browser.js(NOTES_SHOWN % json.dumps(many_sel))))
        check(shown["under"] == [[str(n), "note %d" % n] for n in range(1, 50)], "the 49 notes under their lines: %r"
              % shown["under"][:3])
        browser.click(button(many_sel, 50, "notes/many.txt"))
        browser.call("Input.insertText", text="fiftieth")
        full = browser.js("(function () { var c = document.querySelector(%s); return [c.querySelector('.lfull').textContent, "
                          "Array.prototype.filter.call(c.querySelectorAll('.att-lines button'), function (b) { "
                          "return b.disabled; }).length]; })()" % json.dumps(many_sel))
        check(full == ["At most 50 line notes on an item.", 10], "past 50 notes the free lines' buttons should be off, with the reason: %r" % full)
        browser.click(button(many_sel, 51, "notes/many.txt"))
        browser.click(button(many_sel, 3, "notes/many.txt"))  # a noted line still takes a press: its field
        check(browser.js("document.activeElement.value") == "note 3"
              and browser.js(NOTES_SHOWN % json.dumps(many_sel))["fields"] == 50, "the 51st press should be refused")

        # Notes made out of order go back in attachment and line order, the awkward lines of the fixture included.
        browser.click(review + " details.attach:nth-of-type(3) > summary")
        cells = browser.js("Array.prototype.map.call(document.querySelectorAll(%s), function (c) { return c.textContent; })"
                           % json.dumps(review + " details.attach:nth-of-type(3) [role=rowgroup] > [role=row] > :last-child"))
        check(cells == [marked(l) for l in rule["lines"]] and "↵" in cells[1] and "↵" in cells[2],
              "a lone CR or U+2028 should show as a mark in its row: %r" % cells[:3])
        for line in (6, 1, 2, 3, 5):  # the astral cut, the BOM and CRLF, a lone CR, U+2028, a kept CR
            note(line, "notes/rule.txt", "rule %d" % line)
        browser.click(review + " details.attach:nth-of-type(2) > summary")
        note(3, "notes/todo.txt", "three note")
        note(2, "notes/todo.txt", "two note")
        browser.click(review + " details.attach:nth-of-type(1) > summary")
        note(8, "notes/change.diff", "Why not raise?")
        # A second press goes back to the field; single keys type there; Esc goes back to the number.
        browser.click(button(review, 8, "notes/change.diff"))
        press(browser, "f", "KeyF", 70, text="f")
        press(browser, "j", "KeyJ", 74, text="j")
        check(browser.js("[document.activeElement.value, document.body.classList.contains('focusing')]")
              == ["Why not raise?fj", False], "keys typed in a note should be its text: %r"
              % browser.js("document.activeElement.value"))
        press(browser, "Escape", "Escape", 27)
        check(browser.js("[document.activeElement.tagName, document.activeElement.getAttribute('aria-label')]")
              == ["BUTTON", "Note on line 8 of notes/change.diff"], "Esc in a note should go back to its line's number")
        # By key: Enter on a number goes to its field; Tab past the field and its Remove reaches the next number,
        # and Space there opens that line's field (left empty: it saves nothing).
        press(browser, "Enter", "Enter", 13, text="\r")
        check(browser.js("document.activeElement.value") == "Why not raise?fj", "Enter on a number should go to its note")
        press(browser, "Tab", "Tab", 9)
        press(browser, "Tab", "Tab", 9)
        press(browser, " ", "Space", 32, text=" ")
        check(browser.js("[document.activeElement.tagName, document.activeElement.value, "
                         "document.activeElement.getAttribute('aria-label')]")
              == ["TEXTAREA", "", "Note on line 9 of notes/change.diff"], "Space on the next number should open its field: %r"
              % browser.js("document.activeElement.outerHTML.slice(0, 200)"))
        # Private hides the notes in the Summary; another item's notes show.
        browser.click("#r0-i0-p")
        summary = browser.js("(function () { var s = document.getElementById('summary-rows'); return [s.textContent, "
                             "s.querySelectorAll('.summary-private').length]; })()")
        check("Why not raise" not in summary[0] and "planted" not in summary[0] and summary[1] == 9
              and "Note on line 50 of notes/many.txt: fiftieth" in summary[0],
              "Private should hide the notes in the Summary: %r" % summary)
        want = [(0, 8, DIFF_ROWS[7][3][:1] + DIFF_ROWS[7][4], "Why not raise?fj"), (1, 2, "two", "two note"),
                (1, 3, "three", "three note")] + [(2, n, rule["quotes"][n - 1], "rule %d" % n) for n in (1, 2, 3, 5, 6)]
        want = [dict(zip(("att", "line", "quote", "comment"), w)) for w in want]
        want.insert(3, planted)  # after (1, 3): the list goes out in attachment and line order
        got = wait_for("the notes saved", lambda: (lambda a: a.get("review", {}).get("lines") == want and a)(saved()))
        check(got["review"].get("private") is True and len(got["many"]["lines"]) == 50
              and got["many"]["lines"][-1]["comment"] == "fiftieth", "Private and the 50th note saved: %r" % got["many"]["lines"][-1])

        browser.call("Page.reload")
        shown = wait_for("the notes after a reload", lambda: (lambda s: len(s["under"]) == 8 and s)(
            browser.js(NOTES_SHOWN % json.dumps(review))))
        check(shown["under"] == [[str(w["line"]), w["comment"]] for w in want if w is not planted]
              and shown["apart"] == [[apart % (9, planted["quote"]), "planted"]] and browser.js("document.getElementById('r0-i0-p').checked"), "a reload should restore them: %r" % shown)
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)

    # The file changed since: its notes show apart, unchanged, and a save of another note still passes.
    (topic / "notes" / "todo.txt").write_bytes(b"one\nTWO changed\n")
    server, out, err, browser = serve(target, str(work / "profile-notes-2"))
    try:
        shown = wait_for("the changed file's notes", lambda: (lambda s: len(s["apart"]) == 3 and s)(
            browser.js(NOTES_SHOWN % json.dumps(review))))
        check(shown["apart"] == [[apart % (2, "two"), "two note"], [apart % (3, "three"), "three note"],
                                 [apart % (9, planted["quote"]), "planted"]]
              and [u[0] for u in shown["under"]] == ["8", "1", "2", "3", "5", "6"], "notes on a changed file: %r" % shown)
        # A line whose saved note went stale takes a new note; both go out, the old one as it came.
        browser.click(review + " details.attach:nth-of-type(2) > summary")
        note(2, "notes/todo.txt", "new two")
        want.insert(2, {"att": 1, "line": 2, "quote": "TWO changed", "comment": "new two"})
        browser.click(review + " details.attach:nth-of-type(1) .lnote textarea")
        browser.js("(function (t) { t.setSelectionRange(t.value.length, t.value.length); })(document.activeElement)")
        browser.call("Input.insertText", text=" Fine.")
        want[0]["comment"] += " Fine."
        wait_for("a save past the changed file", lambda: saved()["review"]["lines"] == want)
        # Remove: a changed file's note (focus to its item), then one under its line (focus to the number).
        browser.click(review + ' .lnote:not([role]) [aria-label="Remove: Note on line 3 of notes/todo.txt"]')
        check(browser.js("[document.querySelectorAll(%s).length, document.activeElement.dataset.item]"
                         % json.dumps(review + " .lnote:not([role])")) == [2, "review"],
              "Remove should drop a changed file's note and leave focus on its item")
        browser.click(review + ' .lnote [aria-label="Remove: Note on line 1 of notes/rule.txt"]')
        check(browser.js("document.activeElement.getAttribute('aria-label')") == "Note on line 1 of notes/rule.txt",
              "Remove under a line should leave focus on its number")
        want = [w for w in want if (w["att"], w["line"]) not in ((1, 3), (2, 1))]
        wait_for("the removals saved", lambda: saved()["review"]["lines"] == want)
        browser.click("#done")
        check(finished(server, out, err, [answers_path])[0]["answers"]["review"]["lines"] == want,
              "Done should keep the notes")
        # A box first opened after Done draws its numbers with nothing to press.
        later_box = '[data-item="secret"] details.attach'
        browser.click(later_box + " > summary")
        rows = wait_for("the rows drawn after Done", lambda: browser.js(
            "document.querySelectorAll(%s).length" % json.dumps(later_box + " [role=rowgroup] > [role=row]")))
        check(rows == 2 and browser.js("document.querySelectorAll(%s).length" % json.dumps(later_box + " button")) == 0,
              "after Done a box's numbers take no press")
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)

    # Read only: a done round beside an open one, and the past-decisions page.
    pair = work / "2026-01-12-done-notes"
    shutil.copytree(str(topic), str(pair))
    (pair / "round-02.json").write_text(json.dumps(dict(data, items=[data["items"][1]])), encoding="utf-8")
    older = root / ".signoff" / "2026-01-01-older"  # a second past round to switch to
    older.mkdir()
    (older / "round-01.json").write_text(json.dumps({"version": 1, "title": "Older", "items": [dict(pick, id="old", title="Old")]}),
                                         encoding="utf-8")
    for history in (False, True):
        server, out, err, browser = serve(root if history else pair, str(work / ("profile-notes-ro%d" % history)),
                                          history=history)
        try:
            if history:
                wait_for("the list", lambda: browser.js("!!document.querySelector('#h-list li button, #h-list li a')"))
                # The search finds a line note's words (not a Private item's: review's are hidden).
                browser.click("#h-text")
                for text, n in (("fiftieth", 1), ("planted", 0)):  # no title says "fiftieth"
                    browser.js("document.getElementById('h-text').value = ''")
                    browser.call("Input.insertText", text=text)
                    found = browser.js("Array.prototype.filter.call(document.querySelectorAll('#h-list li'), "
                                       "function (li) { return !li.hidden; }).length")
                    check(found == n, "the past-decisions search for %r should find %d: %d" % (text, n, found))
                browser.js("(function (h) { h.value = ''; h.dispatchEvent(new Event('input')); })(document.getElementById('h-text'))")
                browser.js("document.querySelector('#h-list li button, #h-list li a').click()")
            else:
                browser.click("#tab-0")
            sel = '.rp:not([hidden]) ' + review
            shown = wait_for("the read-only notes", lambda: (lambda s: len(s["under"]) == 6 and s)(
                browser.js(NOTES_SHOWN % json.dumps(sel))))
            check(shown["fields"] == 0 and shown["buttons"] == 0 and not shown["hint"] and len(shown["apart"]) == 2
                  and [u[1] for u in shown["under"]] == [w["comment"] for w in want
                                                         if w is not planted and w["quote"] != "two"],
                  "%s should show the notes with no button or field: %r" % ("--history" if history else "a done round", shown))
            if history:  # another past round, then this one again: the one left holds no rows; back, they draw again
                left = browser.js("""(function () {
                  var p = document.querySelector('.rp:not([hidden])'), b = document.querySelectorAll('#h-list li button');
                  window.back = document.querySelector('#h-list li button[aria-current]');
                  (b[0] === window.back ? b[1] : b[0]).click();
                  return [p.hidden, p.querySelectorAll('.att-lines').length];
                })()""")
                check(left == [True, 0], "a past round left should hold no rows: %r" % left)
                browser.js("window.back.click()")
                again = wait_for("the notes drawn again", lambda: (lambda s: len(s["under"]) == 6 and s)(
                    browser.js(NOTES_SHOWN % json.dumps(sel))))
                check(again == shown, "a past round shown again should draw its notes as before: %r" % again)
                # The round shown, chosen again: its boxes stay as they are, scrolled where they were.
                same = browser.js("""(function () {
                  var all = Array.prototype.slice.call(document.querySelectorAll('.rp:not([hidden]) .att-lines'));
                  var m = document.querySelector('.rp:not([hidden]) [data-item="many"] .att-lines');
                  m.scrollTop = 200;
                  var top = m.scrollTop;
                  window.back.click();
                  return [top > 0, all.every(function (e) { return e.isConnected; }), m.scrollTop === top];
                })()""")
                check(same == [True, True, True], "the past round shown, chosen again, should keep its boxes: %r" % same)
        except E2EError:
            sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
            raise
        finally:
            kill(server)
            kill(browser.proc)

    # One round's Done stored while the run goes on (the next round's refused): its numbers and Remove buttons go
    # off with its fields, and a box opened then draws numbers with nothing to press.
    two = work / "2026-01-13-two-rounds"
    (two / "notes").mkdir(parents=True)
    (two / "notes" / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    look = dict(pick, id="look", title="Look at a", lines=True, attachments=[{"src": "notes/a.txt"}, {"src": "notes/a.txt"}])
    for n in (1, 2):
        (two / ("round-0%d.json" % n)).write_text(json.dumps({"version": 1, "title": "Round %d" % n, "items": [look]}),
                                                  encoding="utf-8")
    (two / "round-01.answers.json").write_text(json.dumps({
        "version": 1, "done": False, "saved": "2026-01-13T10:00:00Z", "free_text": "",
        "decisions": {"file": "round-01.json", "sha256": hashlib.sha256((two / "round-01.json").read_bytes()).hexdigest()},
        "answers": {"look": {"choice": "ok", "comment": "", "lines": [{"att": 0, "line": 1, "quote": "one",
                                                                      "comment": "kept"}]}}}), encoding="utf-8")
    server, out, err, browser = serve(two, str(work / "profile-notes-two"))
    try:
        wait_for("round 1's note", lambda: browser.js("!!document.querySelector('#panel-0 .lnote textarea')"))
        browser.click("#accept-0")
        wait_for("round 2", lambda: browser.js("!document.getElementById('panel-1').hidden"))
        browser.click("#accept-1")
        wait_for("Done to be ready", lambda: browser.js("!document.getElementById('done').disabled"))
        # The server refuses round 2's Done, as it would a file changed on disk; round 1's is stored.
        browser.js("(function (f) { window.fetch = function (u, o) { return u === 'save/1' && JSON.parse(o.body).done "
                   "? Promise.resolve(new Response('{}', {status: 409})) : f.apply(this, arguments); }; })(window.fetch)")
        browser.click("#tab-0")  # its rows drawn again: a round left holds none
        browser.click("#done")
        wait_for("round 1 done", lambda: json.loads((two / "round-01.answers.json").read_text(encoding="utf-8"))["done"])
        off = wait_for("round 1's buttons off", lambda: (lambda s: s[0] and s)(browser.js(
            "[document.querySelector('#panel-0 [data-line]').disabled, "
            "document.querySelector('#panel-0 .lnote button').disabled, document.getElementById('done').disabled, "
            "document.querySelector('#panel-0 .lfull').hidden]")))
        check(off == [True, True, False, True], "a round closed by its Done should take its buttons and hint out: %r" % off)
        browser.click("#tab-0")
        browser.click("#panel-0 details.attach:nth-of-type(2) > summary")
        rows = wait_for("the rows drawn after round 1's Done", lambda: browser.js(
            "document.querySelectorAll('#panel-0 details.attach:nth-of-type(2) [role=rowgroup] > [role=row]').length"))
        check(rows == 2 and browser.js("document.querySelectorAll('#panel-0 details.attach:nth-of-type(2) button').length")
              == 0, "a box opened in a round closed by its Done should have nothing to press")
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)


COMMANDS = [{"text": "rm -rf build && make docs\n  echo '<b>done</b>'", "lang": "bash", "caption": "Rebuild the docs"},
            {"text": "rm -rf /tmp/build"},
            {"text": "rm -rf cache && echo " + "a" * 300, "lang": "python"}]  # warns whatever its lang; wraps


def clipboard_text(browser):
    """The clipboard as text. Blink on Windows may write a line break as CRLF; the check stays exact elsewhere."""
    clip = browser.call("Runtime.evaluate", expression="navigator.clipboard.readText()", awaitPromise=True,
                        returnByValue=True).get("result", {}).get("value")
    return clip.replace("\r\n", "\n") if WINDOWS and isinstance(clip, str) else clip


def copy_with(browser, button):
    """Press a Copy button and return the clipboard. A placeholder goes on the clipboard first and the button must
    say Copied, so text left there by an earlier step cannot pass for this Copy."""
    put = browser.call("Runtime.evaluate", expression="navigator.clipboard.writeText('(placeholder)')",
                       awaitPromise=True, returnByValue=True)
    check("exceptionDetails" not in put and clipboard_text(browser) == "(placeholder)",
          "the placeholder should be on the clipboard: %r" % put)
    browser.click(button)
    said = browser.js("document.querySelector(%s).textContent" % json.dumps(button))
    check(said == "Copied", "the button should say it copied: %r" % said)
    return clipboard_text(browser)


# Each command block under the item: where it sits, its caption, bar, Copy's name, the text and its warning, and
# whether no line runs past the block's edge.
COMMAND_BLOCKS = """(function () {
  var q = document.querySelector('.rp:not([hidden]) .item .q'), kids = Array.prototype.slice.call(q.children);
  var cmds = q.querySelector('.cmds'), at = kids.indexOf(cmds);
  return {after: kids[at - 1] && kids[at - 1].className, before: kids[at + 1] && kids[at + 1].className,
    blocks: Array.prototype.map.call(cmds.querySelectorAll('.md-code'), function (c) {
      var cap = c.previousElementSibling, pre = c.querySelector('pre'), w = c.querySelector('.md-warn');
      return {caption: cap && cap.classList.contains('cmd-cap') ? cap.textContent : null,
        lang: c.querySelector('.md-lang').textContent, copy: c.querySelector('.md-bar button').textContent,
        name: c.querySelector('.md-bar button').getAttribute('aria-label'), text: pre.textContent,
        children: pre.firstChild.children.length, tab: pre.tabIndex, warn: w && w.textContent,
        warnBox: !!w && getComputedStyle(w).paddingTop !== '0px', wraps: pre.scrollWidth <= pre.clientWidth};
    }), bold: q.querySelectorAll('b').length, undef: document.body.innerText.indexOf('undefined') >= 0};
})()"""


def run_commands(work):
    """An item's commands: each a code block under the description and above the pictures, its text exactly (no
    markup), Copy puts it on the clipboard, a relative rm warns and an absolute one does not; a Hungarian round has
    Hungarian labels; Done exits 0 and the Markdown copy holds the command escaped."""
    for lang in ("en", "hu"):
        topic = work / ("2026-01-09-commands-" + lang)
        topic.mkdir()
        shutil.copytree(str(DEMO / "mockups"), str(topic / "mockups"))
        data = json.loads((DEMO / "round-01.json").read_text(encoding="utf-8"))
        data["items"] = data["items"][:1]
        data["items"][0]["commands"] = COMMANDS
        data["lang"] = lang
        target = topic / "decisions.json"
        target.write_text(json.dumps(data), encoding="utf-8")
        server, out, err, browser = serve(target, str(work / ("profile-commands-" + lang)))
        try:
            browser.call("Browser.grantPermissions", origin=wait_for("the page", lambda: browser.js(
                "document.querySelector('.cmds') && location.origin")),
                permissions=["clipboardReadWrite", "clipboardSanitizedWrite"])
            got = browser.js(COMMAND_BLOCKS)
            check((got["after"], got["before"]) == ("desc", "shots"),
                  "the commands should sit after the description and before the pictures: %r" % got)
            check(not got["bold"] and not got["undef"], "nothing may render, and no label may be missing: %r" % got)
            copy, code, warn = (("Copy", "code", "⚠ Relative path: check the folder before running") if lang == "en"
                                else ("Másolás", "kód", "⚠ Relatív útvonal: futtatás előtt ellenőrizd a mappát"))
            name = "Copy the {l} block" if lang == "en" else "A(z) {l} blokk másolása"
            want = [{"caption": "Rebuild the docs", "lang": "bash", "copy": copy, "name": name.format(l="Rebuild the docs"),
                     "text": COMMANDS[0]["text"], "children": 0, "tab": 0, "warn": warn, "warnBox": True, "wraps": True},
                    {"caption": None, "lang": code, "copy": copy, "name": name.format(l=code), "text": COMMANDS[1]["text"],
                     "children": 0, "tab": 0, "warn": None, "warnBox": False, "wraps": True},
                    {"caption": None, "lang": "python", "copy": copy, "name": name.format(l="python"),
                     "text": COMMANDS[2]["text"], "children": 0, "tab": 0, "warn": warn, "warnBox": True, "wraps": True}]
            check(got["blocks"] == want, "the command blocks (%s): %r" % (lang, got["blocks"]))
            if lang == "en":
                clip = copy_with(browser, ".cmds .md-code .md-bar button")
                check(clip == COMMANDS[0]["text"], "Copy should put the command on the clipboard exactly: %r" % clip)
            browser.click("#done")
            finished(server, out, err, [topic / "decisions.answers.json"])
            md = (topic / "decisions.answers.md").read_text(encoding="utf-8")
            label = "Command" if lang == "en" else "Parancs"
            check("- %s (Rebuild the docs): rm -rf build && make docs  \n  echo '\\<b\\>done\\</b\\>'\n" % label in md
                  and "- %s: rm -rf /tmp/build\n" % label in md, "the Markdown copy should hold the commands: %r" % md)
        except E2EError:
            sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
            raise
        finally:
            kill(server)
            kill(browser.proc)


def run_commands_elsewhere(work):
    """A round done in an earlier run, and the same round on the past-decisions page, still show its commands, and
    Copy puts the exact text on the clipboard."""
    root = work / "cmd-proj"
    topic = root / ".signoff" / "2026-01-09-commands"
    topic.mkdir(parents=True)
    shutil.copytree(str(DEMO / "mockups"), str(topic / "mockups"))
    data = json.loads((DEMO / "round-01.json").read_text(encoding="utf-8"))
    data["items"] = data["items"][:1]
    data["items"][0]["commands"] = COMMANDS
    (topic / "round-01.json").write_text(json.dumps(data), encoding="utf-8")
    (topic / "round-02.json").write_text(json.dumps(dict(data, title="Another round", items=[
        dict(data["items"][0], commands=[{"text": "ls"}])])), encoding="utf-8")
    item = data["items"][0]
    (topic / "round-01.answers.json").write_text(json.dumps({
        "version": 1, "done": True, "saved": "2026-01-09T10:00:00Z", "free_text": "",
        "decisions": {"file": "round-01.json", "sha256": hashlib.sha256((topic / "round-01.json").read_bytes()).hexdigest()},
        "answers": {item["id"]: {"choice": item["recommended"], "comment": ""}}}), encoding="utf-8")
    for where, target, history in (("a done round", topic, False), ("past decisions", root, True)):
        server, out, err, browser = serve(target, str(work / ("profile-cmd-" + ("history" if history else "done"))),
                                          history=history)
        try:
            if history:
                wait_for("the list", lambda: browser.js("!!document.querySelector('.h-entry')"))
                browser.js("Array.prototype.filter.call(document.querySelectorAll('.h-entry'), function (b) { "
                           "return b.textContent.indexOf('round-01.json') >= 0; })[0].click()")
            else:
                wait_for("the tabs", lambda: browser.js("!!document.getElementById('tab-0')"))
                browser.click("#tab-0")
                check(browser.js("!!document.querySelector('.rp:not([hidden]) .readonly-note')"),
                      "the done round should show read only")
            browser.call("Browser.grantPermissions", origin=browser.js("location.origin"),
                         permissions=["clipboardReadWrite", "clipboardSanitizedWrite"])
            block = ".rp:not([hidden]) .cmds .md-code"
            wait_for("the commands on " + where, lambda: browser.js("!!document.querySelector(%s)" % json.dumps(block)))
            texts = browser.js("Array.prototype.map.call(document.querySelectorAll(%s), function (c) { "
                               "return c.querySelector('pre').textContent; })" % json.dumps(block))
            check(texts == [c["text"] for c in COMMANDS], "%s should show the commands: %r" % (where, texts))
            k = 2 if history else 1  # each view copies a command of its own
            clip = copy_with(browser, ".rp:not([hidden]) .cmds > div.md-code:nth-of-type(%d) .md-bar button" % k)
            check(clip == COMMANDS[k - 1]["text"], "Copy on %s should put the command on the clipboard exactly: %r"
                  % (where, clip))
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


# The round's marks for the agent's blocking items: each title's badge (its glyph hidden from screen readers), each
# sidebar item's last word, and any "undefined" a missing label would show.
BLOCKING = """(function () {
  var cards = document.querySelectorAll('#panel-0 .item[data-item]');
  return {badges: Array.prototype.map.call(cards, function (c) { var b = c.querySelector('h3 .badge');
      return b ? [b.textContent, b.firstChild.getAttribute('aria-hidden')] : null; }),
    outline: Array.prototype.map.call(document.querySelectorAll('.outline .nav'), function (b) {
      var s = b.querySelector('.st'); return s ? s.textContent : null; }),
    titles: Array.prototype.map.call(document.querySelectorAll('.outline .nl'), function (n) { return n.textContent; }),
    undef: document.body.innerText.indexOf('undefined') >= 0};
})()"""
SHOWN_TITLES = ("Array.prototype.filter.call(document.querySelectorAll(%s), function (li) { return !li.hidden && "
                "!li.closest('[hidden]'); }).map(function (li) { return li.querySelector('.link').textContent; })")


def ax_name(browser, selector):
    """The accessible name Chrome computes for the element, as a screen reader gets it."""
    root = browser.call("DOM.getDocument", depth=0)["root"]["nodeId"]
    node = browser.call("DOM.querySelector", nodeId=root, selector=selector)["nodeId"]
    check(node, "no element %s" % selector)
    return browser.call("Accessibility.getPartialAXTree", nodeId=node, fetchRelatives=False)["nodes"][0]["name"]["value"]


# The sidebar's item rows whose title is under 100 px wide or that overflow their button: their marks squeezed them.
SIDEBAR_SQUEEZED = """Array.prototype.filter.call(document.querySelectorAll('.outline .nav'), function (b) {
  return b.querySelector('.nl').getBoundingClientRect().width < 100 || b.scrollWidth > b.clientWidth;
}).map(function (b) { return b.textContent; })"""


def sidebar_fits(browser, what):
    """Each sidebar row keeps its title wide and its marks whole, at 1280 px and in the drawer at 400 px."""
    check(browser.js(SIDEBAR_SQUEEZED) == [], "%s, the sidebar at 1280 px: %r" % (what, browser.js(SIDEBAR_SQUEEZED)))
    browser.call("Emulation.setDeviceMetricsOverride", width=400, height=900, deviceScaleFactor=1, mobile=False)
    time.sleep(0.5)
    browser.click("header button.icon[aria-controls]")
    time.sleep(0.5)
    check(browser.js(SIDEBAR_SQUEEZED) == [], "%s, the drawer at 400 px: %r" % (what, browser.js(SIDEBAR_SQUEEZED)))
    press(browser, "Escape", "Escape", 27)
    browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
    time.sleep(0.5)


def run_blocking(work):
    """Items the agent waits on: a badge with a word in the title and in the sidebar's list, a fourth Summary filter
    that shows only them, in English and Hungarian, Done exits 0; a done round keeps its marks, and an open round
    without the key keeps three filters."""
    for lang in ("en", "hu"):
        topic = work / ("2026-01-09-blocking-" + lang)
        topic.mkdir()
        for sub in ("mockups", "boards", "notes"):
            shutil.copytree(str(DEMO / sub), str(topic / sub))
        data = json.loads((DEMO / "round-03.json").read_text(encoding="utf-8"))
        data["items"][1]["blocking"] = data["items"][3]["blocking"] = True
        data["items"][3].update(stakes="high", irreversible="Moves every note")  # all three marks on one row
        data["lang"] = lang
        target = topic / "decisions.json"
        target.write_text(json.dumps(data), encoding="utf-8")
        word, short, show = (("Agent waits on this", "agent waits", "Agent waits") if lang == "en"
                             else ("Erre vár az ágens", "erre vár", "Erre vár az ágens"))
        server, out, err, browser = serve(target, str(work / ("profile-blocking-" + lang)))
        try:
            wait_for("the page", lambda: browser.js("!!document.getElementById('summary-rows')"))
            got = browser.js(BLOCKING)
            mark = ["■" + word, "true"]
            check(got["badges"] == [None, mark, None, mark], "the title badges (%s): %r" % (lang, got["badges"]))
            check(got["outline"] == [None, "■" + short, None, "■" + short], "the sidebar marks (%s): %r" % (lang, got))
            check(got["titles"] == [it["title"] for it in data["items"]] and not got["undef"],
                  "the sidebar titles stay as written, and no label is missing: %r" % got)
            sidebar_fits(browser, lang)
            name = ax_name(browser, "#panel-0 .item[data-item=theme] h3")
            check(word in name and "■" not in name, "the title's accessible name: %r" % name)
            name = ax_name(browser, ".outline li:nth-child(2) .nav")
            check(short in name and "■" not in name, "the sidebar button's accessible name: %r" % name)
            labels = filter_labels(browser, "#summary-block")
            check(len(labels) == 4 and labels[0].endswith(" 4") and labels[3] == show + " 2",
                  "the Summary's filters (%s): %r" % (lang, labels))
            check(browser.js("document.querySelector('#summary-block .seg').getAttribute('role')") == "group",
                  "the filters stay a group")
            # Four labels outgrow a phone (Hungarian's most): they wrap as whole buttons, none cut off.
            browser.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=False)
            time.sleep(0.5)
            fit = browser.js("(function () { var s = document.querySelector('#summary-block .seg'); "
                             "return [s.scrollWidth, s.clientWidth]; })()")
            check(fit[0] <= fit[1], "the four filters fit their box at 390 px (%s): %r" % (lang, fit))
            browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
            time.sleep(0.3)
            browser.click("#summary-block .summary-filter button:nth-of-type(4)")
            rows = browser.js(SHOWN_TITLES % json.dumps("#summary-block .summary-list li"))
            check(rows == ["2. " + data["items"][1]["title"], "4. " + data["items"][3]["title"]],
                  "the fourth filter shows only the marked items: %r" % rows)
            check(browser.js("document.querySelector('#summary-block .summary-filter button:nth-of-type(4)')"
                             ".getAttribute('aria-pressed')") == "true", "the fourth filter is pressed")
            badges = browser.js("Array.prototype.map.call(document.querySelectorAll('#summary-block .flags .badge'), "
                                "function (b) { return b.textContent; })")
            check(badges == ["■" + word] * 2, "the Summary rows' badges: %r" % badges)
            browser.click("#done")
            answers = finished(server, out, err, [topic / "decisions.answers.json"])[0]
            check(not any("blocking" in a for a in answers["answers"].values()), "the answers hold no blocking key")
        except E2EError:
            sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
            raise
        finally:
            kill(server)
            kill(browser.proc)

    # Round 1 done with two marked items, round 2 open without: the done round keeps its marks, and round 2's block,
    # which lists only the open round, keeps today's three filters.
    topic = work / "2026-01-09-blocking-mixed"
    topic.mkdir()
    for sub in ("mockups", "boards", "notes"):
        shutil.copytree(str(DEMO / sub), str(topic / sub))
    first = json.loads((DEMO / "round-03.json").read_text(encoding="utf-8"))
    first["items"][1]["blocking"] = first["items"][3]["blocking"] = True
    (topic / "round-01.json").write_text(json.dumps(first), encoding="utf-8")
    shutil.copyfile(str(DEMO / "round-04.json"), str(topic / "round-02.json"))
    sha = hashlib.sha256((topic / "round-01.json").read_bytes()).hexdigest()
    (topic / "round-01.answers.json").write_text(json.dumps({
        "version": 1, "done": True, "saved": "2026-01-09T10:00:00Z", "free_text": "",
        "decisions": {"file": "round-01.json", "sha256": sha}, "answers": {}}), encoding="utf-8")
    server, out, err, browser = serve(topic, str(work / "profile-blocking-mixed"))
    try:
        wait_for("the page", lambda: browser.js("!!document.getElementById('summary-rows')"))
        labels = filter_labels(browser, "#summary-block")
        check(labels == ["All 2", "With comment 0", "Not the recommendation 0"],
              "a round without the key keeps three filters: %r" % labels)
        browser.click("#tab-0")
        got = browser.js(BLOCKING)
        check(got["badges"] == [None, ["■Agent waits on this", "true"], None, ["■Agent waits on this", "true"]],
              "a done round keeps its marks: %r" % got)
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)


# A settled item's state: folded, its line, the choice shown there (and whether it is seen), the Change button's
# state, and whether its options and its stakes chip are seen.
SETTLED = """(function () {
  var c = document.querySelector('#panel-0 .item[data-item=theme]'), b = c.querySelector('.settled button'),
    v = document.getElementById('r0-i1-sv');
  return [c.classList.contains('folded'), c.querySelector('.settled').firstChild.textContent,
    v.textContent, v.checkVisibility(), b.textContent, b.getAttribute('aria-expanded'),
    b.getAttribute('aria-controls'), document.getElementById('r0-i1-o0').closest('.opt').checkVisibility(),
    c.querySelector('.margin .stakes').checkVisibility()];
})()"""


def run_settled(work):
    """Stakes, Cannot be undone and Settled earlier: the margin's chip and line, the sidebar's marks, and a settled
    item that starts folded with its choice; Change opens it, a pick saves, a reload keeps the pick and folds it
    again, the digit keys pick nothing while it is folded, and Done exits 0 with no new key in the answers."""
    topic = work / "2026-01-09-settled"
    topic.mkdir()
    for sub in ("mockups", "boards", "notes"):
        shutil.copytree(str(DEMO / sub), str(topic / sub))
    data = json.loads((DEMO / "round-03.json").read_text(encoding="utf-8"))
    data["items"][0]["stakes"] = "high"
    data["items"][1].update(settled="round-01, 11-14", stakes="low")
    data["items"][3].update(stakes="medium", irreversible="Moves every note to the new store")
    target = topic / "decisions.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    answers_file = topic / "decisions.answers.json"

    def saved():
        try:
            return json.loads(answers_file.read_text(encoding="utf-8"))["answers"]
        except (OSError, ValueError, KeyError):
            return {}
    server, out, err, browser = serve(target, str(work / "profile-settled"))
    try:
        wait_for("the page", lambda: browser.js("!!document.getElementById('r0-i1-sv')"))
        # Folded, the margin stays in sight: it is the agent's (stakes, cannot be undone), not the options.
        folded = [True, "Settled earlier: round-01, 11-14", "Follow the system", True, "Change", "false", "r0-i1-fold",
                  False, True]
        check(browser.js(SETTLED) == folded, "the settled item starts folded with its choice: %r" % browser.js(SETTLED))
        margin = browser.js("Array.prototype.map.call(document.querySelectorAll('#panel-0 .item'), function (c) { "
                            "var s = c.querySelector('.stakes'), u = c.querySelectorAll('.margin .cost'); return "
                            "[s ? s.textContent : null, s ? s.querySelector('.wg').getAttribute('aria-hidden') : null, "
                            "u.length ? u[u.length - 1].textContent : null]; })")
        check(margin[0][:2] == ["▲High stakes", "true"] and margin[1][0] == "▼Low stakes" and margin[3] == [
            "●Medium stakes", "true", "Cannot be undoneMoves every note to the new store"] and margin[2][0] is None,
            "the margins: %r" % margin)
        marks = browser.js("Array.prototype.map.call(document.querySelectorAll('.outline .nav'), function (b) { "
                           "return Array.prototype.map.call(b.querySelectorAll('.st'), function (s) { "
                           "return s.textContent; }); })")
        check(marks == [["▲high stakes"], [], [], ["!irreversible"]], "the sidebar marks: %r" % marks)
        name = ax_name(browser, ".outline li:nth-child(4) .nav")
        check("irreversible" in name and "!" not in name, "the sidebar button's accessible name: %r" % name)
        # A digit key on the folded item picks nothing out of sight.
        browser.js("document.querySelector('#panel-0 .item[data-item=theme]').focus()")
        press(browser, "2", "Digit2", 50, text="2")
        time.sleep(0.5)
        check(browser.js("document.getElementById('r0-i1-o0').checked") and "theme" not in saved(),
              "a digit key on a folded item changed it: %r" % saved())
        browser.click("#panel-0 .item[data-item=theme] .settled button")
        opened = browser.js(SETTLED)
        check(opened[0] is False and opened[3] is False and opened[4:6] == ["Hide options", "true"] and opened[7],
              "Change shows the options and hides the folded choice: %r" % opened)
        browser.click("#r0-i1-o1")
        wait_for("the pick saved", lambda: saved().get("theme", {}).get("choice") == "light")
        check(saved()["theme"]["source"] == "user", "a changed settled item is the user's: %r" % saved()["theme"])
        browser.click("#panel-0 .item[data-item=theme] .settled button")  # folded again, the line names the pick
        check(browser.js(SETTLED)[:4] == [True, folded[1], "Always light", True],
              "folded after a pick: %r" % browser.js(SETTLED))
        browser.call("Page.reload")
        wait_for("the reloaded page", lambda: browser.js("!!document.getElementById('r0-i1-sv') && "
                                                         "document.getElementById('r0-i1-o1').checked"))
        again = browser.js(SETTLED)
        check(again == folded[:2] + ["Always light"] + folded[3:], "a reload folds it again with the pick: %r" % again)
        check(not browser.js("document.body.innerText.indexOf('undefined') >= 0"), "a label is missing")
        browser.click("#done")
        answers = finished(server, out, err, [answers_file])[0]
        check(answers["answers"]["theme"]["choice"] == "light", "the Done file keeps the pick: %r" % answers)
        check(not any(k in a for a in answers["answers"].values() for k in ("stakes", "irreversible", "settled")),
              "the answers hold none of the new keys")
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)
    run_settled_views(work)


# Which parts of the first item a person sees: the settled line, the question's other parts (description, commands,
# pictures), the stakes chip, the options' part, and the Change button.
SETTLED_SEEN = """(function () {
  var c = document.querySelector('#panel-0 .item'), seen = function (e) { return !!e && e.checkVisibility(); };
  return [seen(c.querySelector('.settled')), Array.prototype.filter.call(c.querySelectorAll('.q>:not(.settled)'),
    seen).length, seen(c.querySelector('.stakes')), seen(c.querySelector('.b')), !!c.querySelector('.settled button'),
    c.classList.contains('folded')];
})()"""


def run_settled_views(work):
    """A settled item with a description, a picture, a board and a command, on a phone, where the page opens in Focus:
    folded it shows only its line and its margin. In a done round it shows the line above its answer, unfolded, with
    no Change."""
    topic = work / "2026-01-09-settled-phone"
    topic.mkdir()
    for sub in ("mockups", "boards", "notes"):
        shutil.copytree(str(DEMO / sub), str(topic / sub))
    data = json.loads((DEMO / "round-03.json").read_text(encoding="utf-8"))
    data["items"][0].update(settled="round-01, 11-14", stakes="high", commands=COMMANDS[:1])
    data["items"][0]["images"].append({"src": "mockups/settings-two-columns.png", "caption": "Two columns"})
    (topic / "round-01.json").write_text(json.dumps(data), encoding="utf-8")
    server, out, err, browser = serve(topic, str(work / "profile-settled-phone"), size=(400, 900))
    try:
        wait_for("the page in Focus", lambda: browser.js("!!document.body && document.body.classList.contains('focusing') && "
                                                         "!!document.querySelector('#panel-0 .item .settled')"))
        seen = browser.js(SETTLED_SEEN)
        check(seen == [True, 0, True, False, True, True], "folded on a phone in Focus: %r" % seen)
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)
    # Round 1 done, round 2 open: round 1's settled item is read only.
    shutil.copyfile(str(DEMO / "round-04.json"), str(topic / "round-02.json"))
    sha = hashlib.sha256((topic / "round-01.json").read_bytes()).hexdigest()
    (topic / "round-01.answers.json").write_text(json.dumps({
        "version": 1, "done": True, "saved": "2026-01-09T10:00:00Z", "free_text": "",
        "decisions": {"file": "round-01.json", "sha256": sha}, "answers": {}}), encoding="utf-8")
    server, out, err, browser = serve(topic, str(work / "profile-settled-done"))
    try:
        wait_for("the page", lambda: browser.js("!!document.getElementById('tab-0')"))
        browser.click("#tab-0")
        seen = wait_for("the done round", lambda: browser.js("!!document.querySelector('#panel-0 .item .settled') "
                                                             "&& document.querySelector('#panel-0').checkVisibility() "
                                                             "&& " + SETTLED_SEEN))
        line = browser.js("document.querySelector('#panel-0 .item .settled').textContent")
        check(seen[0] and seen[3] and not seen[4] and not seen[5] and line == "Settled earlier: round-01, 11-14",
              "a done round's settled item: %r, %r" % (seen, line))
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)


NUMBER_ITEMS = [
    {"id": "budget", "title": "How much of the budget?", "description": "A share of this quarter's budget.",
     "select": "number", "range": {"min": -10, "max": 10, "step": 0.5, "unit": "%"}, "recommended": 2.5,
     "skippable": True},
    {"id": "count", "title": "How many workers?", "select": "number", "range": {"min": 0, "max": 100, "step": 5},
     "recommended": 40, "comment": False},
]
# A number item's controls as the page shows them, by item index in round 0.
NUMBER_STATE = """(function (n) {
  var r = document.getElementById('r0-i' + n + '-n'), f = document.getElementById('r0-i' + n + '-v');
  var s = document.getElementById('r0-i' + n + '-skip'), t = document.getElementById('r0-i' + n + '-rt');
  var u = r.closest('.num-pick').querySelector('.num-unit');
  return {range: r.value, field: f.value, min: r.min, max: r.max, step: r.step, fmin: f.min, fmax: f.max, fstep: f.step,
    text: r.getAttribute('aria-valuetext'), skip: s ? s.checked : null,
    tag: getComputedStyle(t).visibility === 'visible' && t.textContent, width: r.getBoundingClientRect().width,
    unset: r.dataset.unset === '1', unit: u ? u.textContent : null,
    keys: r.closest('.item').querySelectorAll('.okey').length, active: document.activeElement.id};
})(%d)"""


def run_number(work):
    """A number item: a slider and a field bound both ways, the recommendation preselected and tagged, typing a value
    (a half-typed one never saved, 0 kept as 0, an off-grid one snapped), the arrows on the slider, j from it, no digit
    key, Skip and a move that takes it back, Accept all and its undo, the Summary's value and its "not the
    recommendation", nothing wider than a phone, a Hungarian copy without a missing label; Done exits 0 with JSON
    numbers in the answers file; a done round shows its value read only, and no value where none was saved."""
    for lang in ("en", "hu"):
        topic = work / ("2026-01-09-number-" + lang)
        topic.mkdir()
        data = {"version": 1, "title": "Size the pilot", "lang": lang, "items": NUMBER_ITEMS}
        target = topic / "decisions.json"
        target.write_text(json.dumps(data), encoding="utf-8")
        answers = topic / "decisions.answers.json"
        server, out, err, browser = serve(target, str(work / ("profile-number-" + lang)))

        def state(n=0):
            return browser.js(NUMBER_STATE % n)

        def saved(want, key="budget"):
            def look():
                try:
                    a = json.loads(answers.read_text(encoding="utf-8"))["answers"].get(key, {})
                except (OSError, ValueError):
                    return None
                return a if (a.get("choice"), a.get("source")) == want else None
            return wait_for("the answer %r for %s" % (want, key), look)

        def type_value(text):  # replaces the field's text, as typing over a selection does
            browser.js("document.getElementById('r0-i0-v').value = ''")
            browser.call("Input.insertText", text=text)
        try:
            wait_for("the page", lambda: browser.js("!!document.getElementById('r0-i0-n')"))
            check(not browser.js("document.body.innerText.indexOf('undefined') >= 0"), "a label is missing (%s)" % lang)
            got = state()
            rec = "Recommended" if lang == "en" else "Javasolt"
            check((got["range"], got["field"], got["min"], got["max"], got["step"], got["fmin"], got["fmax"],
                   got["fstep"], got["text"], got["skip"], got["tag"], got["unset"], got["unit"], got["keys"])
                  == ("2.5", "2.5", "-10", "10", "0.5", "-10", "10", "0.5", "2.5 %", False, rec, False, "%", 0),
                  "the number item as loaded (%s): %r" % (lang, got))
            got = state(1)
            check((got["range"], got["field"], got["text"], got["skip"], got["unit"]) == ("40", "40", "40", None, None),
                  "a number item without a unit or Skip: %r" % got)
            field = "How much of the budget?, " + ("value" if lang == "en" else "érték")
            names = [ax_name(browser, "#r0-i0-n"), ax_name(browser, "#r0-i0-v")]
            check(names == ["How much of the budget?", field], "the inputs' accessible names (%s): %r" % (lang, names))
            margin = browser.js("document.querySelector('#panel-0 .item .margin .rec').textContent")
            check(margin == "2.5 %", "the margin's recommendation: %r" % margin)
            if lang == "hu":
                browser.click("#done")
                got = finished(server, out, err, [answers])[0]["answers"]
                check(got["budget"] == {"choice": 2.5, "comment": "", "source": "recommended"}
                      and got["count"]["choice"] == 40, "the untouched Hungarian round's answers: %r" % got)
                md = (topic / "decisions.answers.md").read_text(encoding="utf-8")
                check("- Választás: 2.5 % (javasolt)\n" in md, "the Hungarian Markdown copy: %r" % md)
                continue
            # Typing: a half-typed "-" changes nothing, "-7" moves the slider and is saved, 0 is saved as 0.
            width = state()["width"]
            browser.click("#r0-i0-v")
            type_value("-")
            check(state()["range"] == "2.5", "a half-typed value leaves the slider: %r" % state())
            browser.call("Input.insertText", text="7")
            check((state()["range"], state()["text"], state()["tag"], state()["width"]) == ("-7", "-7 %", False, width),
                  "typing moves the slider and drops the tag, the slider's width kept: %r" % state())
            saved((-7, "user"))
            type_value("0")
            got = saved((0, "user"))
            check(type(got["choice"]) is int, "0 must stay the number 0: %r" % got)
            # An off-grid value snaps on leaving the field; the slider never holds one.
            type_value("3.3")
            press(browser, "Tab", "Tab", 9)
            check((state()["range"], state()["field"]) == ("3.5", "3.5"), "an off-grid value snaps: %r" % state())
            saved((3.5, "user"))
            # The slider: arrows move it and the field; a digit picks nothing; j goes on to the next item.
            browser.js("document.getElementById('r0-i0-n').focus()")
            press(browser, "ArrowLeft", "ArrowLeft", 37)
            press(browser, "ArrowLeft", "ArrowLeft", 37)
            got = state()
            check((got["range"], got["field"], got["text"], got["tag"]) == ("2.5", "2.5", "2.5 %", "Recommended"),
                  "the arrows move the slider and the field, back on the recommendation the tag shows: %r" % got)
            saved((2.5, "user"))
            press(browser, "1", "Digit1", 49, text="1")
            check(state()["skip"] is False, "a digit picks nothing on a number item: %r" % state())
            press(browser, "j", "KeyJ", 74, text="j")
            check(browser.js("document.activeElement.closest('.item').dataset.item") == "count",
                  "j from the slider goes to the next item: %r" % browser.js("document.activeElement.id"))
            # Skip, then a move takes it back.
            browser.click("label[for=r0-i0-skip]")
            check(state()["skip"] is True, "Skip is ticked")
            saved(("_skip", "user"))
            browser.js("document.getElementById('r0-i0-n').focus()")
            press(browser, "ArrowRight", "ArrowRight", 39)
            check((state()["skip"], state()["range"]) == (False, "3"), "a move unticks Skip: %r" % state())
            saved((3, "user"))
            # Accept all, then its undo.
            browser.click("#accept-all")
            check(state()["range"] == "2.5" and not state()["skip"], "Accept all sets the recommendation: %r" % state())
            saved((2.5, "accept_all"))
            browser.click("#accept-all")
            check(state()["range"] == "3", "the undo brings the value back: %r" % state())
            saved((3, "user"))
            browser.click("#r0-i0-v")
            type_value("0")
            saved((0, "user"))
            # The Summary: the value with its unit, and the flag where it differs.
            summary = ("Array.prototype.map.call(document.querySelectorAll('#summary-block .summary-list li'), "
                       "function (li) { return [li.querySelector('.summary-choice').textContent, Array.prototype.map"
                       ".call(li.querySelectorAll('.flags .badge'), function (b) { return b.textContent; })]; })")
            rows = wait_for("the summary", lambda: (lambda r: r if r and r[0][0] == "0 %" else None)(browser.js(summary)))
            check(rows == [["0 %", ["not the recommendation"]], ["40", []]], "the Summary rows: %r" % rows)
            labels = filter_labels(browser, "#summary-block")
            check(labels == ["All 2", "With comment 0", "Not the recommendation 1"], "the Summary's filters: %r" % labels)
            # A phone: nothing wider than the screen, the slider still usable.
            browser.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=False)
            time.sleep(0.5)
            fit = browser.js("(function () { var r = document.getElementById('r0-i0-n').getBoundingClientRect(); "
                             "return [document.documentElement.scrollWidth, r.width, r.right]; })()")
            check(fit[0] <= 390 and fit[1] >= 120 and fit[2] <= 390, "the number item at 390 px: %r" % fit)
            browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
            time.sleep(0.3)
            browser.click("#done")
            got = finished(server, out, err, [answers])[0]["answers"]
            check(got["budget"] == {"choice": 0, "comment": "", "source": "user"}
                  and got["count"] == {"choice": 40, "comment": "", "source": "recommended"},
                  "the answers file holds JSON numbers: %r" % got)
            md = (topic / "decisions.answers.md").read_text(encoding="utf-8")
            check("- Choice: 0 % (not recommended)\n" in md and "- Choice: 40 (recommended)\n" in md,
                  "the Markdown copy: %r" % md)
        except E2EError:
            sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
            raise
        finally:
            kill(server)
            kill(browser.proc)

    # A done round read only: its value shows, disabled; an item with no saved answer shows no value.
    topic = work / "2026-01-09-number-done"
    topic.mkdir()
    (topic / "round-01.json").write_text(json.dumps({"version": 1, "title": "Done one", "items": NUMBER_ITEMS}),
                                         encoding="utf-8")
    (topic / "round-02.json").write_text(json.dumps({"version": 1, "title": "Open one", "items": NUMBER_ITEMS[1:]}),
                                         encoding="utf-8")
    sha = hashlib.sha256((topic / "round-01.json").read_bytes()).hexdigest()
    (topic / "round-01.answers.json").write_text(json.dumps({
        "version": 1, "done": True, "saved": "2026-01-09T10:00:00Z", "free_text": "",
        "decisions": {"file": "round-01.json", "sha256": sha},
        "answers": {"budget": {"choice": -4.5, "comment": "", "source": "user"}}}), encoding="utf-8")
    # A done round answered Skip: no value and no Recommended tag, only Skip ticked.
    (topic / "round-03.json").write_text(json.dumps({"version": 1, "title": "Skipped one", "items": NUMBER_ITEMS[:1]}),
                                         encoding="utf-8")
    sha = hashlib.sha256((topic / "round-03.json").read_bytes()).hexdigest()
    (topic / "round-03.answers.json").write_text(json.dumps({
        "version": 1, "done": True, "saved": "2026-01-09T10:00:00Z", "free_text": "",
        "decisions": {"file": "round-03.json", "sha256": sha},
        "answers": {"budget": {"choice": "_skip", "comment": "", "source": "user"}}}), encoding="utf-8")
    server, out, err, browser = serve(topic, str(work / "profile-number-done"))
    try:
        wait_for("the page", lambda: browser.js("!!document.getElementById('tab-0')"))
        browser.click("#tab-0")
        got = browser.js("[document.getElementById('r0-i0-n').disabled, document.getElementById('r0-i0-v').disabled, "
                         "document.getElementById('r0-i0-v').value, document.getElementById('r0-i1-v').value, "
                         "document.getElementById('r0-i1-n').dataset.unset, "
                         "getComputedStyle(document.getElementById('r0-i1-n')).visibility, "
                         "getComputedStyle(document.getElementById('r0-i0-n')).visibility]")
        check(got == [True, True, "-4.5", "", "1", "hidden", "visible"],
              "a done round's number items, no slider where nothing was saved: %r" % got)
        browser.click("#tab-2")
        got = browser.js("[document.getElementById('r2-i0-skip').checked, document.getElementById('r2-i0-v').value, "
                         "getComputedStyle(document.getElementById('r2-i0-n')).visibility, "
                         "getComputedStyle(document.getElementById('r2-i0-rt')).visibility]")
        check(got == [True, "", "hidden", "hidden"], "a done round answered Skip shows no value and no tag: %r" % got)
    except E2EError:
        sys.stderr.write("--- Signoff stderr ---\n" + "".join(err))
        raise
    finally:
        kill(server)
        kill(browser.proc)


def run_inbox(work):
    """The inbox: first nothing waits, so the empty state shows, and its To do button and the sidebar filter the list.
    Then a waiting run's round and a todo item marked waiting on you. The item opens beside the list to its Markdown: the
    agent's HTML is text, only the https link is a link, and Copy puts the block's text on the clipboard exactly. A
    message typed on it becomes a request file for the project's session, and shows as sent. The round opens from the list in the same tab, and Done there wakes the waiting run (exit 0, its answers path).
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
    other = work / "inbox-other"  # a second project, with one todo item
    (other / "_bmad-output").mkdir(parents=True)
    (other / "_bmad-output" / "todo.md").write_text("- [ ] An item of the other project\n", encoding="utf-8")
    other_id = signoff.project_id(other.resolve())
    (home / "projects" / (other_id + ".json")).write_text(json.dumps(
        {"schema": 1, "id": other_id, "root": str(other.resolve()), "name": "inbox-other"}), encoding="utf-8")
    env = dict(os.environ, SIGNOFF_INBOX_HOME=str(home))
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
        # Focus walks only the rows shown: the To do rows wait behind closed count rows, so from the empty state f
        # finds no row and stays out of Focus (run_live walks an Idle row from the empty state).
        press(browser, "f", "KeyF", 70, text="f")
        check(browser.js("!document.body.classList.contains('focusing') && !document.querySelector('.ib-zero').hidden"),
              "f should walk only the rows shown, and none is")
        browser.click(".ib-zero button")
        shown = browser.js("(function () { var s = Array.prototype.filter.call(document.querySelectorAll('#panels section'),"
                           " function (x) { return !x.hidden; }); return [s.length, s[0].querySelector('h2').textContent,"
                           " document.querySelector('.ib-zero').hidden, document.getElementById('intro').textContent,"
                           " Array.prototype.filter.call(document.querySelectorAll('.nav'), function (b) {"
                           " return b.getAttribute('aria-pressed') === 'true'; }).map(function (b) { return b.textContent; })]; })()")
        check(shown == [1, "To do 3, by project", True, "Showing To do", ["To do 3"]],
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
        # The sidebar filters by project: the other project's item.
        browser.js("Array.prototype.filter.call(document.querySelectorAll('.nav'), function (b) {"
                   " return b.querySelector('.nl').textContent === 'inbox-other'; })[0].setAttribute('data-e2e', 'other')")
        browser.click(".nav[data-e2e=other]")
        titles = browser.js("Array.prototype.filter.call(document.querySelectorAll('.ib-row'), function (r) {"
                            " return r.offsetParent; }).map(function (r) { return r.querySelector('.ib-title').textContent; })")
        check(titles == ["An item of the other project"], "the project filter: %r" % titles)
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
        check(browser.js("Array.prototype.every.call(document.querySelectorAll('#ib-detail .md-code pre'), function (p) "
                         "{ return getComputedStyle(p).whiteSpace === 'pre-wrap'; })"), "a todo's code block should wrap")
        check(browser.js("document.querySelectorAll('#ib-detail .ib-body a button, #ib-detail .ib-body a .ic-warn').length")
              == 0, "no Copy or warning inside a link")
        got = copy_with(browser, "#ib-detail button.ic-copy")
        check(got == "rm -rf build", "the inline Copy should put the span's text on the clipboard: %r" % got)
        text = ("A plain todo item\n\nSee the draft (https://example.com/draft).\n\nClean up with rm -rf build, not "
                "rm -rf /tmp/build.\n\nrm -rf build && ls\n\nrm -rf /tmp/build\n\nAlso rm x (https://example.com/rm).\n\n"
                "del cache")
        got = copy_with(browser, "#ib-detail .ib-copytext")
        check(got == text, "Copy text should put the whole item on the clipboard: %r" % got)
        browser.click(todo)  # back to the waiting item: its view kept as it was
        got = copy_with(browser, "#ib-detail .md-bar button")
        check(got == command, "Copy should put the block's text on the clipboard: %r" % got)
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
        # Answered is closed by default, as a To do group is: its heading is a button with the count.
        answered = ("(function () { var b = document.querySelector('h2.ib-head .ib-gtoggle'), u = b && document"
                    ".getElementById(b.getAttribute('aria-controls')); return b && [b.textContent, b.getAttribute("
                    "'aria-expanded'), u.querySelectorAll('.ib-row').length, u.querySelector('.ib-row').checkVisibility()]; })()")
        check(browser.js(answered) == ["Answered 1", "false", 1, False], "Answered starts closed: %r" % browser.js(answered))
        in_answered = ("(function () { var b = document.querySelector('h2.ib-head .ib-gtoggle'), c = document.querySelector("
                       "'#panels .ib-main[aria-current=true]'); return !!c && document.getElementById(b.getAttribute("
                       "'aria-controls')).contains(c); })()")
        # j, k and Focus walk only the rows shown, never a closed Answered's.
        browser.js("document.querySelector('#panels .ib-main').focus()")
        for _ in range(8):
            press(browser, "j", "KeyJ", 74, text="j")
            check(not browser.js(in_answered), "j should not walk into a closed Answered")
        press(browser, "f", "KeyF", 70, text="f")
        for _ in range(8):
            press(browser, "k", "KeyK", 75, text="k")
            check(not browser.js(in_answered), "Focus should not walk into a closed Answered")
        press(browser, "Escape", "Escape", 27)
        browser.click("h2.ib-head .ib-gtoggle")
        check(browser.js(answered) == ["Answered 1", "true", 1, True], "a click opens Answered: %r" % browser.js(answered))
        browser.click("h2.ib-head .ib-gtoggle")
        check(browser.js(answered)[1:] == ["false", 1, False], "a second click closes it: %r" % browser.js(answered))
        # A search opens it while a match is inside, and only then.
        answered_title = browser.js("document.querySelector('h2.ib-head .ib-gtoggle').closest('section')"
                                    ".querySelector('.ib-title').textContent")
        browser.js("var f = document.getElementById('ib-search'); f.focus(); f.select()")
        browser.call("Input.insertText", text=answered_title)
        check(browser.js(answered)[1:] == ["true", 1, True], "a match inside opens Answered: %r" % browser.js(answered))
        browser.js("var f = document.getElementById('ib-search'); f.focus(); f.select()")
        browser.call("Input.insertText", text="lodash")
        check(not browser.js("document.querySelector('h2.ib-head .ib-gtoggle').checkVisibility()"),
              "a search with no match inside leaves Answered out")
        press(browser, "Escape", "Escape", 27)
        check(browser.js(answered)[1:] == ["false", 1, False], "a cleared search closes it again: %r" % browser.js(answered))
        # The page's own reload keeps it open; a fresh load starts closed.
        browser.click("h2.ib-head .ib-gtoggle")
        browser.js("window.notReloaded = true; document.activeElement.blur()")
        todo_before = todo_file.read_text(encoding="utf-8")
        todo_file.write_text(todo_before + "- [ ] (waiting on you: look) One more item for the reload\n", encoding="utf-8")
        wait_for("the reload or the bar that offers it", lambda: browser.js(
            "window.notReloaded !== true || (function () { var f = document.querySelector('#panels .ib-fresh'); "
            "return !!f && !f.hidden; })()"), timeout=40)
        if browser.js("window.notReloaded === true"):
            browser.click("#panels .ib-fresh button")
        wait_for("the reload", lambda: browser.js("window.notReloaded !== true && !!document.querySelector('.ib-main')"))
        check(browser.js(answered)[1:] == ["true", 1, True], "the reload keeps Answered open: %r" % browser.js(answered))
        todo_file.write_text(todo_before, encoding="utf-8")  # back to two waiting, for the checks below
        wait_for("the inbox to count two again", lambda: json.loads(
            (home / "state.json").read_text(encoding="utf-8"))["waiting"] == 2)
        browser.call("Page.navigate", url=browser.js("location.href"))
        wait_for("the list again", lambda: browser.js("document.title === '(2) Inbox' && "
                                                      "!!document.querySelector('h2.ib-head .ib-gtoggle')"))
        check(browser.js(answered)[1:] == ["false", 1, False], "a fresh load starts closed: %r" % browser.js(answered))
        # A project filter opens Answered, as it opens that project's To do group; another project's leaves it out,
        # and the list without a filter has it closed again.
        browser.js("Array.prototype.forEach.call(document.querySelectorAll('.nav'), function (b) {"
                   " var n = b.querySelector('.nl'); if (n) b.setAttribute('data-e2e', n.textContent); })")
        browser.click(".nav[data-e2e=inbox-project]")
        check(browser.js(answered)[1:] == ["true", 1, True], "the project's filter opens Answered: %r" % browser.js(answered))
        browser.click(".nav[data-e2e=inbox-other]")
        check(not browser.js("document.querySelector('h2.ib-head .ib-gtoggle').checkVisibility()"),
              "another project's filter leaves Answered out")
        browser.click(".nav[data-e2e=inbox-project]")
        check(browser.js(answered)[1:] == ["true", 1, True], "back on the project, Answered open: %r" % browser.js(answered))
        browser.click(".nav[data-e2e=inbox-project]")  # a second click shows everything again
        check(browser.js(answered)[1:] == ["false", 1, False], "no filter, Answered closed again: %r" % browser.js(answered))
        # Keyboard: Enter on a focused row that is not open opens it, as a click does.
        browser.click(".nav[data-e2e=inbox-project]")  # its To do group open: rows that are not open yet
        title = browser.js("(function () { var b = Array.prototype.filter.call(document.querySelectorAll("
                           "'#panels button.ib-main'), function (x) { return x.offsetParent && "
                           "x.getAttribute('aria-current') !== 'true'; })[0];"
                           " b.setAttribute('data-e2e', 'enter'); return b.querySelector('.ib-title').textContent; })()")
        browser.js("document.querySelector('[data-e2e=enter]').focus()")
        press(browser, "Enter", "Enter", 13, text="\r")
        wait_for("Enter to open the row", lambda: browser.js(
            "document.querySelector('[data-e2e=enter]').getAttribute('aria-current') === 'true' && "
            "document.querySelector('#ib-detail h2').textContent === %s" % json.dumps(title)))
        browser.click(".nav[data-e2e=inbox-project]")  # a second click shows everything again
        files = [json.loads(p.read_text(encoding="utf-8")) for p in (home / "requests" / pid).glob("*.json")]
        check(sorted((f["kind"], f["ref"]) for f in files) == [
            ("later", "Sign the release"), ("todo", "Check <b>the backup</b> before the release")],
              "requests: %r" % files)
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
    span, two more open items, a request on one, a waiting run in a topic whose round has accented
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
    env = dict(os.environ, SIGNOFF_INBOX_HOME=str(home))
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
        check(parts[0] == ["Repo-higiénia: ikonok", "Hagyd jóvá a kiadási jegyzetet"]
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
        check(sorted(found("docs")) == ["Frissítsd a bevezetőt", "Rename the glossary"],
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
                       ["Idle 1, not counted", True, [
                           ["Idle", "Claude Code session", "Go to its terminal", "500"]]]]
              and whens[0][0] in ("5 min ago", "6 min ago") and whens[0][1] in ("1 min ago", "2 min ago")
              and whens[1][0] in ("for 15 min", "for 16 min"),
              "Waiting on you should list both waiting sessions, Idle the idle one, dimmer: %r %r" % (seen, whens))
        counts = browser.js("[document.title, Array.prototype.map.call(document.querySelectorAll('.nav'), function (b) {"
                            " return b.textContent; }), document.body.textContent.indexOf('SENTINEL') < 0]")
        check(counts == ["(2) Inbox", ["Waiting on you 2", "To do", "LIlive-project 2, waiting on you", "Past decisions"],
                         True],
              "the idle session must count nowhere: %r" % counts)
        browser.click("#panels button.ib-main")
        detail = browser.js("(function () { var b = document.querySelector('#ib-detail .ib-body'); return b && ["
                            "Array.prototype.map.call(b.querySelectorAll('pre code'), function (c) { return c.textContent; }),"
                            " b.querySelectorAll('a, input, form').length]; })()")
        check(detail == [["claude attach 8d4e0402"], 0], "the background session's attach command: %r" % detail)
        got = copy_with(browser, "#ib-detail .md-bar button")
        check(got == "claude attach 8d4e0402", "Copy should put the attach command on the clipboard: %r" % got)
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
        # Focus walks only the rows shown: from the empty state it opens on the Idle row, and the bar counts it
        # alone; Esc goes back with focus on the Focus button.
        press(browser, "f", "KeyF", 70, text="f")
        walk = browser.js("[document.body.classList.contains('focusing'), document.activeElement.tagName,"
                          " document.querySelector('#ib-detail h2').textContent === document.querySelector("
                          "'section.idle .ib-title').textContent, document.querySelector('.ib-fbar span').textContent]")
        check(walk == [True, "H2", True, "1 of 1"], "f should walk only the rows shown: %r" % walk)
        press(browser, "Escape", "Escape", 27)
        check(browser.js("!document.querySelector('.ib-zero').hidden && document.activeElement.classList.contains('tb-btn')"
                         " && document.activeElement.getAttribute('aria-pressed') === 'false'"),
              "Esc should go back to the empty state, focus on the Focus button")
        browser.click(".ib-zero button")
        landed = browser.js("[document.querySelector('.ib-zero').hidden, !!document.activeElement.closest('section.idle')]")
        check(landed == [True, True], "the Idle button should show the list, focus on the idle row: %r" % landed)
    except E2EError:
        sys.stderr.write("--- inbox stderr ---\n" + "".join(inbox_err))
        raise
    finally:
        kill(browser.proc)
        end_inbox(inbox, script, env, home)


def parked_fixture(work):
    """A Signoff home and a project for Park, Back and Past decisions, also for screenshots by hand: two open rounds a
    run will ask (topic), a past round answered with Done and another left open, and a parked round no run lists.
    Returns the home, the topic for the waiter, and the inbox's environment and command."""
    sg = _signoff()
    home, root = work / "parked-home", work / "parked-project"
    signoff_dir = root / ".signoff"
    topic, past, left = signoff_dir / "2026-01-12-park", signoff_dir / "2026-01-02-past", signoff_dir / "2026-01-03-left"
    for folder in (topic, past, left):
        folder.mkdir(parents=True)
    (root / ".git").mkdir()
    base = json.loads((DEMO / "round-04.json").read_text(encoding="utf-8"))
    for folder, name, title in ((topic, "round-01.json", "Wording of the export"), (topic, "round-02.json", "Name of the app"),
                                (past, "round-01.json", "An earlier choice"), (left, "round-01.json", "A round no run lists")):
        (folder / name).write_text(json.dumps(dict(base, title=title, tab=title.split()[0])), encoding="utf-8")
    sha = hashlib.sha256((past / "round-01.json").read_bytes()).hexdigest()
    (past / "round-01.answers.json").write_text(json.dumps(
        {"version": 1, "done": True, "saved": "2026-01-02T10:00:00Z", "decisions": {"file": "round-01.json", "sha256": sha},
         "answers": {}, "free_text": "", "accepted": True}), encoding="utf-8")
    pid = sg.project_id(root.resolve())
    for sub in ("projects", "runs"):
        (home / sub).mkdir(parents=True, mode=0o700)
    (home / "projects" / (pid + ".json")).write_text(json.dumps(
        {"schema": 1, "id": pid, "root": str(root.resolve()), "name": "park-project"}), encoding="utf-8")
    (home / "parked.json").write_text(json.dumps({"schema": 1, "rounds": [
        {"path": str((left / "round-01.json").resolve()), "at": "2026-01-03T09:00:00Z"}]}), encoding="utf-8")
    os.chmod(str(home / "parked.json"), 0o600)
    env = dict(os.environ, SIGNOFF_INBOX_HOME=str(home))
    script = [sys.executable, "-I", "-B", str(ROOT / "plugin" / "signoff" / "signoff.py")]
    return home, topic, env, script


# The list's sections as shown: each heading's text, whether its rows show, and each shown row's kind and title.
SECTIONS = """Array.prototype.filter.call(document.querySelectorAll('#panels section.ib'), function (s) { return !s.hidden; })
  .map(function (s) { var t = s.querySelector('.ib-gtoggle'); return [s.querySelector('h2').textContent,
  t ? t.getAttribute('aria-expanded') : null, Array.prototype.filter.call(s.querySelectorAll('.ib-row'), function (r) {
  return r.offsetParent !== null; }).map(function (r) { return [r.querySelector('.ib-kind').textContent,
  r.querySelector('.ib-title').textContent]; })]; })"""


# Each round row's own button: its text, its accessible name, whether it sits inside the row's link, and whether it
# is a sibling right after that link in the row.
ROW_BUTTONS = """Array.prototype.map.call(document.querySelectorAll('#panels .ib-row > .ib-rowact'), function (b) {
  return [b.textContent, b.getAttribute('aria-label'), !!b.closest('a'),
  b.previousElementSibling.classList.contains('ib-main')]; })"""
# At each shown row with a button: the title and the time end left of the button and inside the link.
ROW_FIT = """Array.prototype.filter.call(document.querySelectorAll('#panels .ib-row.has-act'), function (li) {
  return li.offsetParent !== null; }).map(function (li) { var b = li.querySelector('.ib-rowact').getBoundingClientRect(),
  a = li.querySelector('.ib-main').getBoundingClientRect(); return ['.ib-title', '.ib-when'].every(function (s) {
  var r = li.querySelector(s).getBoundingClientRect(); return r.width > 0 && r.right <= b.left + 0.5
  && r.left >= a.left && r.right <= a.right + 0.5; }); })"""


def row_button(browser, name):
    """A real mouse click on the row button with this accessible name, the page marked to see its reload."""
    browser.js("window.notReloaded = true; Array.prototype.forEach.call(document.querySelectorAll('[data-e2e=rowact]'), "
               "function (b) { b.removeAttribute('data-e2e'); }); "
               "Array.prototype.filter.call(document.querySelectorAll('.ib-rowact'), "
               "function (b) { return b.getAttribute('aria-label') === %s; })[0].setAttribute('data-e2e', 'rowact')"
               % json.dumps(name))
    browser.click(".ib-rowact[data-e2e=rowact]")


def run_parked(work):
    """Park and Back from the inbox list, and a project's past decisions from its sidebar row. A waiting round parked
    leaves Waiting on you for Parked under Answered, closed behind its heading, which counts it and the one an agent
    still waits on; the tab title and state.json drop at once, with no "new" bar; Back returns it. Parked opens on a
    click, its project's filter and a search, its rows are out of j, k and Focus while closed, and the page's own
    reload keeps it as it was. Past decisions opens read only in the same tab. At 390 px nothing runs past the edge."""
    home, topic, env, script = parked_fixture(work)
    inbox_err, err = [], []
    inbox = spawn(script + ["inbox", "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                  universal_newlines=True, encoding="utf-8", errors="replace")
    threading.Thread(target=pump, args=(inbox.stderr, inbox_err), daemon=True).start()
    start = wait_for("a start link from the inbox", lambda: inbox_link(inbox, inbox_err, script, env, home))
    waiter = spawn(script + [str(topic), "--no-open"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                   universal_newlines=True, encoding="utf-8", errors="replace")
    threading.Thread(target=pump, args=(waiter.stderr, err), daemon=True).start()
    wait_for("the run to wait on the inbox", lambda: any("inbox is open" in l for l in err))
    browser = Browser(str(work / "profile-parked"))

    def state():
        return json.loads((home / "state.json").read_text(encoding="utf-8"))["waiting"]

    def reloaded():
        return wait_for("the page to reload itself", lambda: browser.js(
            "window.notReloaded !== true && !!document.querySelector('#panels section.ib')"), timeout=20)

    def toggle(head):
        browser.js("Array.prototype.filter.call(document.querySelectorAll('#panels section.ib'), function (s) {"
                   " return s.querySelector('h2').textContent.indexOf(%s) === 0; })[0].querySelector('.ib-gtoggle')"
                   ".setAttribute('data-e2e', 'tog')" % json.dumps(head))
        browser.click(".ib-gtoggle[data-e2e=tog]")
        browser.js("document.querySelector('[data-e2e=tog]').removeAttribute('data-e2e')")
    try:
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        browser.call("Page.enable")
        browser.call("Page.navigate", url=start)
        seen = wait_for("the waiting rounds", lambda: browser.js(
            "document.querySelectorAll('#panels .ib-row').length >= 3 && " + SECTIONS))
        wait_for("the inbox's own count", lambda: state() == 2)  # its look every STATE_EVERY seconds
        check(seen[:2] == [["Waiting on you 2", None, [["Question", "Wording of the export"], ["Question", "Name of the app"]]],
                           ["Parked 1", "false", []]] and browser.js("document.title") == "(2) Inbox",
              "two rounds wait, the round no run lists sits in Parked, closed and counted nowhere: %r" % seen)
        # Park on the open row (the first one, beside the list): the page reloads to its new count, no bar.
        acts = browser.js("Array.prototype.map.call(document.querySelectorAll('#ib-detail .ib-acts > *'), function (e) {"
                          " return e.textContent; })")
        check(acts == ["Open round", "Park"], "a round's open row offers Open round and Park: %r" % acts)
        browser.js("window.notReloaded = true")
        browser.click("#ib-detail .ib-acts button")
        reloaded()
        seen = browser.js(SECTIONS)
        bar = browser.js("[document.title, document.querySelector('#panels .ib-fresh').hidden,"
                         " document.querySelector('.bar-state .dot').classList.contains('new')]")
        check(seen[:2] == [["Waiting on you 1", None, [["Question", "Name of the app"]]],
                           ["Parked 2, 1 with an agent waiting", "false", []]] and bar == ["(1) Inbox", True, False]
              and state() == 1, "Park moves the round to Parked, the count drops, no new bar: %r %r" % (seen, bar))
        nav = browser.js("[Array.prototype.map.call(document.querySelectorAll('#ib-side .nav'), function (b) {"
                         " return b.textContent; }), document.getElementById('intro').textContent,"
                         " getComputedStyle(document.querySelector('#ib-side a.nav')).textDecorationLine]")
        check(nav == [["Waiting on you 1", "To do", "PApark-project 1, waiting on you", "Past decisions"],
                      "1 waiting on you in 1 project", "none"],
              "the sidebar and the header line count what waits, the project row has its Past decisions: %r" % nav)
        # Closed, Parked's rows are out of Focus: it walks the one waiting round.
        press(browser, "f", "KeyF", 70, text="f")
        check(browser.js("document.querySelector('.ib-fbar span').textContent") == "1 of 1",
              "Focus walks only the rows shown: %r" % browser.js("document.querySelector('.ib-fbar span').textContent"))
        press(browser, "Escape", "Escape", 27)
        # A click opens it: the round no run lists, then the parked one whose agent still waits.
        toggle("Parked")
        check(browser.js(SECTIONS)[1] == ["Parked 2, 1 with an agent waiting", "true", [
              ["Parked", "A round no run lists"], ["Agent waiting", "Wording of the export"]]],
              "Parked opens on a click, each row saying whether an agent waits: %r" % browser.js(SECTIONS))
        browser.js("document.querySelector('#panels .ib-main').focus()")  # the waiting round's row; j to each parked
        press(browser, "j", "KeyJ", 74, text="j")
        note = browser.js("[document.querySelector('#ib-detail h2').textContent, Array.prototype.map.call("
                          "document.querySelectorAll('#ib-detail .ib-vuln-note'), function (p) { return p.textContent; })]")
        check(note == ["A round no run lists", ["Parked: it counts nowhere and stays until Back. Its run has ended: "
                                                "Back puts it in Waiting on you as Run ended for 14 days, and your "
                                                "answer goes to the project's sessions."]],
              "a parked round no run lists says what Back does: %r" % note)
        press(browser, "j", "KeyJ", 74, text="j")
        acts = browser.js("[document.querySelector('#ib-detail h2').textContent, Array.prototype.map.call("
                          "document.querySelectorAll('#ib-detail .ib-acts > *'), function (e) { return e.textContent; })]")
        check(acts == ["Wording of the export", ["Open round", "Back"]], "a parked row offers Back: %r" % acts)
        browser.js("window.notReloaded = true")
        browser.click("#ib-detail .ib-acts button")
        reloaded()
        seen = browser.js(SECTIONS)
        check(seen[0] == ["Waiting on you 2", None, [["Question", "Wording of the export"], ["Question", "Name of the app"]]]
              and seen[1] == ["Parked 1", "true", [["Parked", "A round no run lists"]]]
              and browser.js("document.title") == "(2) Inbox" and state() == 2,
              "Back returns the round; the page's own reload keeps Parked open: %r" % seen)
        # The row's own button, beside its link (never inside it), named for its round, reached with Tab.
        check(browser.js(ROW_BUTTONS) == [["Park", "Park Wording of the export", False, True],
                                          ["Park", "Park Name of the app", False, True],
                                          ["Back", "Back A round no run lists", False, True]],
              "each round row ends in its own Park or Back: %r" % browser.js(ROW_BUTTONS))
        browser.js("document.querySelector('#panels .ib-main').focus()")
        press(browser, "Tab", "Tab", 9)
        check(browser.js("document.activeElement.getAttribute('aria-label')") == "Park Wording of the export",
              "Tab goes from a row's link to its button")
        row_button(browser, "Park Name of the app")
        reloaded()
        seen = browser.js(SECTIONS)
        landed = browser.js("[document.activeElement.className, (document.activeElement.querySelector('.ib-title') || {})"
                            ".textContent]")
        check(seen[:2] == [["Waiting on you 1", None, [["Question", "Wording of the export"]]],
                           ["Parked 2, 1 with an agent waiting", "true", [["Parked", "A round no run lists"],
                                                                         ["Agent waiting", "Name of the app"]]]]
              and browser.js("document.title") == "(1) Inbox" and state() == 1
              and landed == ["ib-main", "A round no run lists"],
              "a click on the row's Park parks it, the focus on the row that took its place: %r %r" % (seen, landed))
        row_button(browser, "Back Name of the app")
        reloaded()
        seen = browser.js(SECTIONS)
        check(seen[:2] == [["Waiting on you 2", None, [["Question", "Wording of the export"], ["Question", "Name of the app"]]],
                           ["Parked 1", "true", [["Parked", "A round no run lists"]]]] and state() == 2,
              "a click on the row's Back puts it back: %r" % seen)
        toggle("Parked")
        check(browser.js(SECTIONS)[1][1:] == ["false", []], "a second click closes Parked")
        # Its project's filter opens it with that project's rows; a search opens it on a match.
        browser.js("Array.prototype.filter.call(document.querySelectorAll('#ib-side button.nav'), function (b) {"
                   " return b.textContent.indexOf('park-project') >= 0; })[0].setAttribute('data-e2e', 'proj')")
        browser.click("#ib-side button.nav[data-e2e=proj]")
        check(browser.js(SECTIONS)[-1][1:] == ["true", [["Parked", "A round no run lists"]]],
              "the project's filter opens Parked: %r" % browser.js(SECTIONS))
        browser.click("#ib-side button.nav[data-e2e=proj]")
        browser.click("#ib-search")
        browser.call("Input.insertText", text="no run lists")
        found = wait_for("the search's match", lambda: browser.js(
            "document.getElementById('ib-search-count').textContent === '1 match' && " + SECTIONS))
        check(found == [["Parked 1", "true", [["Parked", "A round no run lists"]]]], "a search opens Parked: %r" % found)
        press(browser, "Escape", "Escape", 27)
        check(browser.js(SECTIONS)[1][1] == "false", "the cleared search gives Parked back closed")
        # Past decisions: the project's rounds, newest first, read only, in this tab; Inbox leads back.
        browser.click("#ib-side a.nav")
        hist = wait_for("the past-decisions page", lambda: browser.js(
            "document.body.classList.contains('hist') && document.querySelectorAll('#h-list li').length > 0 && ["
            "location.pathname.split('/').slice(2).join('/').replace(/[0-9a-f]{16}/, 'ID'), document.getElementById('title').textContent,"
            " document.getElementById('notice').textContent, document.querySelectorAll('#h-list li').length,"
            " (document.querySelector('a.back') || {}).textContent]"))
        check(hist == ["history/ID/", "Past decisions", "Read only.", 4, "← Inbox"],
              "the sidebar's Past decisions opens the project's rounds read only: %r" % hist)
        browser.click("a.back")
        wait_for("the list again", lambda: browser.js("!!document.querySelector('#panels section.ib')"))
        # A phone: Parked and its rows fit, the drawer holds Past decisions.
        browser.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
        browser.call("Page.navigate", url=browser.js("location.href"))
        wait_for("the list at 390", lambda: browser.js("!!document.querySelector('#panels section.ib')"))
        toggle("Parked")
        phone = browser.js("[document.documentElement.scrollWidth, Array.prototype.every.call(document.querySelectorAll("
                           "'#panels .ib-row, #panels .ib-gtoggle'), function (e) { var r = e.getBoundingClientRect();"
                           " return r.right <= 390.5; }), " + SECTIONS + "[1][1]]")
        check(phone == [390, True, "true"], "at 390 px Parked opens and nothing runs past the edge: %r" % phone)
        fits = browser.js(ROW_FIT)
        check(fits and all(fits), "at 390 px each row's title and time stay in their link, left of its button: %r" % fits)
        row_button(browser, "Park Wording of the export")
        reloaded()
        check(browser.js("document.title") == "(1) Inbox" and state() == 1
              and browser.js(SECTIONS)[1][0] == "Parked 2, 1 with an agent waiting",
              "a tap on the row's Park at 390 px parks it: %r" % browser.js(SECTIONS))
        row_button(browser, "Back Wording of the export")
        reloaded()
        check(browser.js("document.title") == "(2) Inbox" and state() == 2, "a tap on the row's Back at 390 px")
        browser.click("header button.icon")
        check(browser.js("document.body.classList.contains('side-open') && document.querySelector('#ib-side a.nav')"
                         ".getBoundingClientRect().width > 0"), "the drawer holds Past decisions")
        press(browser, "Escape", "Escape", 27)
        # Back on the round no run lists: it waits on you as a Run ended row, and counts.
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        browser.call("Page.navigate", url=browser.js("location.href"))
        wait_for("the list at 1280", lambda: browser.js("!!document.querySelector('#panels section.ib')"))
        toggle("Parked")
        browser.js("document.querySelector('#panels .ib-main').focus()")
        for _ in range(2):
            press(browser, "j", "KeyJ", 74, text="j")
        check(browser.js("document.querySelector('#ib-detail h2').textContent") == "A round no run lists",
              "j reaches the parked round no run lists")
        browser.js("window.notReloaded = true")
        browser.click("#ib-detail .ib-acts button")
        reloaded()
        back = browser.js("[document.title, document.querySelector('#panels .ib-fresh').hidden, " + SECTIONS + "]")
        check(back[:2] == ["(3) Inbox", True] and back[2][:2] == [
              # Its group, read in while it was parked, keeps that time as its since: oldest first, it leads.
              ["Waiting on you 3", None, [["Run ended", "A round no run lists"], ["Question", "Wording of the export"],
                                          ["Question", "Name of the app"]]], ["To do 0, by project", None, []]]
              and state() == 3, "Back on a round no run lists makes it a Run ended row that counts: %r" % back)
    except E2EError:
        sys.stderr.write("--- inbox stderr ---\n" + "".join(inbox_err) + "--- run stderr ---\n" + "".join(err))
        raise
    finally:
        kill(browser.proc)
        kill(waiter)
        end_inbox(inbox, script, env, home)


def run_parked_typed(work):
    """Park and Back while a row's view holds typed text: the page does not reload (it would lose the text), so its
    own count does the work. The tab title follows at once, and the state polls after it show no "new" bar. Then the
    past-decisions page under the inbox: once the inbox stops, a picture that fails says to open the inbox again."""
    work = work / "pt"  # short: the inbox's socket path under it must stay within 100 bytes
    work.mkdir()
    home, topic, env, script = parked_fixture(work)
    root = topic.parent.parent
    (root / "TODO.md").write_text("- [ ] (waiting on you: a pick) Check the thing\n", encoding="utf-8")
    pics = root / ".signoff" / "2026-01-04-pics"
    shutil.copytree(str(DEMO / "mockups"), str(pics / "mockups"))
    shutil.copyfile(str(DEMO / "round-02.json"), str(pics / "round-01.json"))
    inbox_err, err = [], []
    inbox = spawn(script + ["inbox", "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                  universal_newlines=True, encoding="utf-8", errors="replace")
    threading.Thread(target=pump, args=(inbox.stderr, inbox_err), daemon=True).start()
    start = wait_for("a start link from the inbox", lambda: inbox_link(inbox, inbox_err, script, env, home))
    waiter = spawn(script + [str(topic), "--no-open"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                   universal_newlines=True, encoding="utf-8", errors="replace")
    threading.Thread(target=pump, args=(waiter.stderr, err), daemon=True).start()
    wait_for("the run to wait on the inbox", lambda: any("inbox is open" in l for l in err))
    browser = Browser(str(work / "profile-parked-typed"))

    def state():
        return json.loads((home / "state.json").read_text(encoding="utf-8"))["waiting"]

    # After two state polls: whether the page reloaded, its title, whether the "new" bar shows, and the status line.
    after = ("[window.notReloaded === true, document.title, document.querySelector('#panels .ib-fresh').hidden,"
             " (document.querySelector('.bar-state') || {}).textContent]")
    try:
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        browser.call("Page.enable")
        browser.call("Page.navigate", url=start)
        wait_for("the rows", lambda: browser.js("document.querySelectorAll('#panels .ib-row').length >= 4"))
        wait_for("the inbox's own count", lambda: state() == 3)  # two rounds and the todo item
        wait_for("the page's title", lambda: browser.js("document.title") == "(3) Inbox")
        browser.js("Array.prototype.filter.call(document.querySelectorAll('#panels section.ib'), function (s) {"
                   " return s.querySelector('h2').textContent.indexOf('Parked') === 0; })[0].querySelector('.ib-gtoggle')"
                   ".click()")  # open, so its row's Back can be pressed
        browser.js("Array.prototype.filter.call(document.querySelectorAll('#panels .ib-main'), function (a) {"
                   " return a.textContent.indexOf('Check the thing') >= 0; })[0].setAttribute('data-e2e', 'todo')")
        browser.click("#panels .ib-main[data-e2e=todo]")
        browser.click("#ib-detail input[type=text]")
        browser.call("Input.insertText", text="half a message")
        row_button(browser, "Park Name of the app")
        check(browser.js("document.title") == "(2) Inbox", "Park drops the tab title at once: %r" % browser.js(after))
        time.sleep(11)  # two state polls, 5 s apart
        got = browser.js(after)
        check(got == [True, "(2) Inbox", True, "Parked. It moves to Parked when this page reloads."] and state() == 2,
              "with typed text, Park keeps the page, its count and no new bar: %r" % got)
        row_button(browser, "Back A round no run lists")
        check(browser.js("document.title") == "(3) Inbox", "Back raises the tab title at once: %r" % browser.js(after))
        time.sleep(11)
        got = browser.js(after)
        check(got == [True, "(3) Inbox", True, "Put back. It moves back when this page reloads."] and state() == 3,
              "with typed text, Back keeps the page, its count and no new bar: %r" % got)
        check(browser.js("document.querySelector('#ib-detail input[type=text]').value") == "half a message",
              "the typed text is still there")
        # Past decisions under the inbox, then the inbox stops: a picture that fails says to open the inbox again.
        browser.js("document.querySelector('#ib-side a.nav').click()")
        wait_for("the past-decisions page", lambda: browser.js(
            "!!document.body && document.body.classList.contains('hist') && document.querySelectorAll('#h-list li')"
            ".length > 0"))
        browser.js("Array.prototype.filter.call(document.querySelectorAll('#h-list li'), function (li) { return "
                   "li.textContent.indexOf('round 2: lists') >= 0; })[0]"
                   ".querySelector('button, a').click()")
        wait_for("a picture of the round", lambda: browser.js("!!document.querySelector('.frame img')"))
        end_inbox(inbox, script, env, home)
        browser.js("(function () { var p = document.querySelector('.frame img'); p.src = p.src + '?again'; })()")
        notice = wait_for("the notice that the inbox stopped", lambda: browser.js(
            "document.getElementById('notice').textContent").startswith("The inbox has stopped") and browser.js(
            "document.getElementById('notice').textContent"))
        check(notice == "The inbox has stopped, so pictures no longer load. Open the inbox again.",
              "under the inbox the stopped notice says to open the inbox again: %r" % notice)
    except E2EError:
        sys.stderr.write("--- inbox stderr ---\n" + "".join(inbox_err) + "--- run stderr ---\n" + "".join(err))
        raise
    finally:
        kill(browser.proc)
        kill(waiter)
        end_inbox(inbox, script, env, home)


def run_focus_link(work):
    """A panel row's start link, as the mod asks for it on the control socket: open with the numbers rows gives
    (run, round, item, inbox) opens that round's item in Focus, and a hash that names nothing on the page is
    ignored."""
    sys.path.insert(0, str(ROOT / "plugin" / "signoff"))
    import signoff
    home, root = work / "focus-home", work / "focus-project"
    (root / ".git").mkdir(parents=True)
    topic = root / ".signoff" / "2026-01-01-focus"
    topic.mkdir(parents=True)
    data = json.loads((DEMO / "round-04.json").read_text(encoding="utf-8"))
    check(len(data["items"]) >= 2, "round-04.json should have two items or more")
    target = data["items"][1]
    (topic / "round-01.json").write_text(json.dumps(data), encoding="utf-8")
    # Every item but the target decided by the user, so the target is the round's one open item.
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
    project = signoff.project_id(root.resolve())
    wait_for("the inbox's control socket", lambda: "rounds" in (ask("rows", project=project) or {}))
    waiter = spawn(script + [str(topic), "--no-open"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   universal_newlines=True, encoding="utf-8", errors="replace")
    for stream, sink in ((waiter.stdout, out), (waiter.stderr, err)):
        threading.Thread(target=pump, args=(stream, sink), daemon=True).start()
    browser = Browser(str(work / "profile-focus"))
    try:
        rows = wait_for("the waiting round's row", lambda: (lambda r: r and r.get("rounds") and r)(
            ask("rows", project=project)))
        row = rows["rounds"][0]
        check([i["title"] for i in row["items"]] == [target["title"]], "the row should name the open item: %r" % row)
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        browser.call("Page.enable")
        browser.call("Page.navigate", url=ask("open", run=row["run"], round=row["round"], item=row["item"],
                                              inbox=rows["inbox"])["link"])
        shown = wait_for("the item in Focus", lambda: browser.js(
            "(function () { var c = document.querySelector('.item.cur[data-item]'); return c && "
            "document.body.classList.contains('focusing') && !c.hidden && c.offsetParent !== null && "
            "[c.dataset.item, location.hash, document.getElementById('panel-0').contains(document.activeElement),"
            " Array.prototype.map.call(document.querySelectorAll('[aria-current=true] .nl'), function (x) {"
            " return x.textContent; })]; })()"))
        check(shown == [target["id"], "#focus=0/" + target["id"], True, [target["title"]]],
              "open with the row's target should land on its item in Focus: %r" % shown)
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
    except E2EError:
        sys.stderr.write("--- inbox stderr ---\n" + "".join(inbox_err) + "--- run stderr ---\n" + "".join(err))
        raise
    finally:
        kill(browser.proc)
        kill(waiter)
        end_inbox(inbox, script, env, home)


PLAN_ANSWER = """(function () {
  var c = document.querySelector('.item[data-item=plan]'), a = c && c.querySelector('details.attach');
  return !!c && [Array.prototype.map.call(c.querySelectorAll('input[type=radio]'), function (i) {
    var l = i.closest('label') || document.querySelector('label[for="' + i.id + '"]');
    return [i.id, i.checked, l ? l.textContent.trim() : null]; }), !!a && a.open,
    !!c.querySelector('.rec')];
})()"""


def run_plan(work):
    """A Plan review round as `signoff plan` writes it (the mod's command), answered in the browser as a person does:
    Approve the plan, then Done. The command prints the answers path and exits 0, and the answers file holds what the
    mod's readAnswer takes as an approval: done true, a saved time, the plan item's choice approve and source user,
    beside a plan-01.md that holds the plan as sent, its end trimmed."""
    sys.path.insert(0, str(ROOT / "plugin" / "signoff"))
    import signoff
    home, root = work / "plan-home", work / "plan-project"
    (root / ".git").mkdir(parents=True)
    topic = root / ".signoff" / "2026-11-24-plan-e2e1"
    plan = "# Plan\n\n1. Read the tests\n2. Change the code"
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
    # An op that stays, and a reply that only a live inbox that knows rows gives (an error would not pass).
    wait_for("the inbox's control socket", lambda: "rounds" in (ask("rows", project="0" * 16) or {}))
    waiter = spawn(script + ["plan", "--topic", topic.name, "--folder", ".signoff"], cwd=str(root), env=env,
                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                   encoding="utf-8", errors="replace")
    waiter.stdin.write(plan + "\n\n")
    waiter.stdin.close()
    for stream, sink in ((waiter.stdout, out), (waiter.stderr, err)):
        threading.Thread(target=pump, args=(stream, sink), daemon=True).start()
    browser = Browser(str(work / "profile-plan"))
    try:
        project = signoff.project_id(root.resolve())
        rows = wait_for("the plan round in the inbox", lambda: (lambda r: r and r.get("rounds") and r)(
            ask("rows", project=project)))
        check([r["title"] for r in rows["rounds"]] == ["Plan"], "the inbox should list the plan round: %r" % rows)
        row = rows["rounds"][0]
        link = ask("open", run=row["run"], round=row["round"], item=row["item"], inbox=rows["inbox"])["link"]
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        browser.call("Page.enable")
        browser.call("Page.navigate", url=link)
        wait_for("the plan round in Focus", lambda: browser.js(
            "!!document.body && document.body.classList.contains('focusing') && "
            "!!document.querySelector('.item.cur[data-item=plan]')"))
        press(browser, "Escape", "Escape", 27)
        shown = wait_for("the plan item", lambda: browser.js(PLAN_ANSWER))
        # Each label leads with its key's digit.
        check(shown == [[["r0-i0-o0", False, "1Approve the plan"], ["r0-i0-o1", False, "2Ask for changes"]], True, False],
              "the plan round: two options, none preselected, the plan open, no recommendation: %r" % shown)
        browser.click("#r0-i0-o0")
        browser.click("#done")
        answers = finished(waiter, out, err, [topic / "round-01.answers.json"])[0]
        got = answers["answers"]["plan"]
        # As register.tsx's readAnswer reads it: done true, saved the inbox's clock, choice approve, source user.
        check(answers["done"] is True and isinstance(answers["saved"], str)
              and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", answers["saved"])
              and got["choice"] == "approve" and got["source"] == "user",
              "the answers the mod takes as an approval: %r" % answers)
        check((topic / "plan-01.md").read_text(encoding="utf-8") == plan, "plan-01.md should hold the plan as sent")
        check((topic / "round-01.answers.json").stat().st_size <= 4 * 1024 * 1024, "the mod reads up to 4 MiB")
    except E2EError:
        sys.stderr.write("--- inbox stderr ---\n" + "".join(inbox_err) + "--- plan stderr ---\n" + "".join(err))
        raise
    finally:
        kill(browser.proc)
        kill(waiter)
        end_inbox(inbox, script, env, home)


def run_round_age(work):
    """A round asked three days ago and a fresh one, each waited on by a run started now: the old row carries the
    2-day pill (from the round file's time, not the run's start, so both rows read "just now") and its open row the
    note; the old round's page shows its time, the pill and the note, in Focus too; the fresh one's only its time."""
    sys.path.insert(0, str(ROOT / "plugin" / "signoff"))
    import signoff
    home, root = work / "age-home", work / "age-project"
    (root / ".git").mkdir(parents=True)
    topics = []
    for name, title in (("2026-01-01-old", "An old round"), ("2026-01-02-fresh", "A fresh round")):
        topic = root / ".signoff" / name
        topic.mkdir(parents=True)
        data = json.loads((DEMO / "round-04.json").read_text(encoding="utf-8"))
        data["title"] = title
        if not topics:
            data["context"] = CONTEXT  # the old round's; the fresh one has none
        (topic / "round-01.json").write_text(json.dumps(data), encoding="utf-8")
        topics.append(topic)
    old = time.time() - 3 * 86400
    os.utime(str(topics[0] / "round-01.json"), (old, old))
    pid = signoff.project_id(root.resolve())
    (home / "projects").mkdir(parents=True, mode=0o700)
    (home / "projects" / (pid + ".json")).write_text(json.dumps(
        {"schema": 1, "id": pid, "root": str(root.resolve()), "name": "age-project"}), encoding="utf-8")
    env = dict(os.environ, SIGNOFF_INBOX_HOME=str(home))
    script = [sys.executable, "-I", "-B", str(ROOT / "plugin" / "signoff" / "signoff.py")]
    inbox_err, err, waiters = [], [], []
    inbox = spawn(script + ["inbox", "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                  universal_newlines=True, encoding="utf-8", errors="replace")
    threading.Thread(target=pump, args=(inbox.stderr, inbox_err), daemon=True).start()
    browser = None
    try:
        start = wait_for("a start link from the inbox", lambda: inbox_link(inbox, inbox_err, script, env, home))
        for topic in topics:  # the old round's run first, so its row is the first (oldest) one
            mine = []
            waiter = spawn(script + [str(topic), "--no-open"], env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8", errors="replace")
            threading.Thread(target=pump, args=(waiter.stderr, mine), daemon=True).start()
            waiters.append(waiter)
            wait_for("the run to wait on the inbox", lambda: any("inbox is open" in l for l in mine))
            err += mine
            time.sleep(1.1)  # `started` is in whole seconds: the second run's must come after the first's
        browser = Browser(str(work / "profile-age"))
        browser.call("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
        browser.call("Page.enable")
        browser.call("Page.navigate", url=start)
        rows = wait_for("both round rows", lambda: browser.js(
            "(function () { var a = document.querySelectorAll('a.ib-main'); return a.length === 2 && "
            "Array.prototype.map.call(a, function (m) { var p = m.querySelector('.pill.old'); return [m.querySelector("
            "'.ib-title').textContent, /^(just now|\\d+ min ago)$/.test(m.querySelector('time.ib-when').textContent), "
            "!!p && p.checkVisibility() && p.textContent, m.textContent.indexOf('Open 2+ days') >= 0]; }); })()"))
        # Both clocks on the runs' start (not 3 days ago); only the old file's row has the pill, in the link's text.
        check(rows == [["An old round", True, "Open 2+ days", True], ["A fresh round", True, False, False]],
              "only the old round's row has the pill, in the link's own text: %r" % rows)
        note = browser.js("Array.prototype.map.call(document.querySelectorAll('#ib-detail .ib-vuln-note'), "
                          "function (p) { return p.textContent; })")
        check(browser.js("document.querySelector('#ib-detail h2').textContent") == "An old round"
              and note == ["This round has been open for 3 days. Its questions may be out of date: if in doubt, "
                           "ask the agent before you answer."], "the old round's open row says why: %r" % note)
        links = browser.js("Array.prototype.map.call(document.querySelectorAll('a.ib-main'), function (a) {"
                           " return a.href; })")
        # The context: on the row's second line after the tab, once in the link's text, and in the open row.
        sub = browser.js("Array.prototype.map.call(document.querySelectorAll('a.ib-main'), function (m) { return ["
                         "m.querySelector('.ib-text').textContent, m.textContent.split(%s).length - 1]; })"
                         % json.dumps(CONTEXT))
        check(sub == [["Wording · " + CONTEXT, 1], ["Wording", 0]], "the rows' second lines: %r" % sub)
        check(browser.js("(function () { var p = document.querySelector('#ib-detail p.context');"
                         " return !!p && p.checkVisibility() && p.textContent; })()") == CONTEXT,
              "the open row shows the context")
        browser.js("document.getElementById('ib-search').focus()")
        browser.call("Input.insertText", text="tokenizer")
        check(wait_for("a search on the context", lambda: browser.js(SEARCH_ROWS) == ["An old round"] or None),
              "a word of the context finds its row only")
        browser.call("Page.navigate", url=links[0])
        wait_for("the old round's page", lambda: browser.js("!!document.getElementById('done')"))
        check(browser.js(CONTEXT_SHOWN) == CONTEXT and browser.js(
              "document.querySelector('.rp:not([hidden])').firstElementChild.className") == "context",
              "the round's page opens its panel with the context, as text")
        age = browser.js(ROUND_AGE)
        check(re.fullmatch(r"Opened .+ · 3 days ago", age["time"]) and age["datetime"] == time.strftime(
              "%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(old))) and age["title"] and age["pill"] == "Open 2+ days"
              and age["note"] == "Its questions may be out of date: if in doubt, ask the agent before you answer.",
              "the old round's header and panel: %r" % age)
        press(browser, "f", "KeyF", 70, text="f")
        check(browser.js("document.body.classList.contains('focusing')") and browser.js(ROUND_AGE)["note"],
              "the note stays in Focus")
        browser.call("Page.navigate", url=links[1])
        wait_for("the fresh round's page", lambda: browser.js("!!document.getElementById('done')"))
        age = browser.js(ROUND_AGE)
        check(re.fullmatch(r"Opened .+ · (just now|\d+ min ago)", age["time"]) and age["pill"] is False
              and age["note"] is False,
              "the fresh round shows its time only: %r" % age)
        check(browser.js(CONTEXT_SHOWN) is False, "a round without a context shows none")
    except E2EError:
        sys.stderr.write("--- inbox stderr ---\n" + "".join(inbox_err) + "--- run stderr ---\n" + "".join(err))
        raise
    finally:
        if browser:
            kill(browser.proc)
        for waiter in waiters:
            kill(waiter)
        end_inbox(inbox, script, env, home)


def run():
    work = Path(tempfile.mkdtemp(prefix="signoff-e2e-"))
    WORK.append(work)
    # A Signoff home that does not exist: page runs as with no inbox, even when the user's own inbox is open.
    os.environ["SIGNOFF_INBOX_HOME"] = str(work / "no-home")
    for name in ("SIGNOFF_PORT", "SSH_CONNECTION", "SSH_TTY", "BROWSER"):  # as on a plain local run
        os.environ.pop(name, None)
    (work / ".git").mkdir()  # the project root, so the header can name it
    run_old_rounds(work)
    run_phone(work)
    run_header(work)
    run_first_screen(work)
    run_single(work)
    run_history(work)
    run_readonly(work)
    run_table(work)
    run_attachment(work)
    run_open_rounds(work)
    run_line_notes(work)
    run_commands(work)
    run_commands_elsewhere(work)
    run_blocking(work)
    run_settled(work)
    run_number(work)
    run_old_mark(work)
    if WINDOWS:
        print("SKIP: the inbox (it needs a Unix socket; not on Windows yet)", flush=True)
    else:
        run_inbox(work)
        run_inbox_search(work)
        run_live(work)
        run_parked(work)
        run_parked_typed(work)
        run_focus_link(work)
        run_plan(work)
        run_round_age(work)
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
        # Focus on a wide screen keeps the margin to the right and the picture at full size: no phone rules.
        wide = browser.js("(function () { var c = document.querySelector('#panel-0 .item.cur'), "
                          "m = c.querySelector('.margin'), b = c.querySelector('.pic-size'); return [m.getBoundingClientRect()"
                          ".left >= c.querySelector('.desc').getBoundingClientRect().right, !!b && b.checkVisibility(), "
                          "c.querySelector('.frame img').getBoundingClientRect().height > 180]; })()")
        check(wide == [True, False, True], "Focus at 1280 px: the margin right, no Larger, the picture full: %r" % wide)
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
          "Esc, Hungarian labels, an old round's time, pill and note in Hungarian, its context with the first item "
          "only; "
          "a round's header from 761 to 1440 px, in English and Hungarian: the title keeps 200 px, each control on one "
          "line inside it, nothing covering anything, no line starting with a separator, one row at 1440 px; "
          "on a phone in Focus each item of round 2 shows its question, recommendation and first option before any "
          "scrolling, in English and Hungarian, its pictures as thumbnails and the one that takes marks small with "
          "Larger and Smaller; "
          "an old done round shows its time with no warning; "
          "1 round, its opening time in the header, comment quoted above Done, filter, Private hides it, one-click Done, exit 0; "
          "project name in the header and the tab title; --history search finds answers and comments, never a private one, "
          "and its picker previews in the tab only, and once it stops a picture that fails says so; "
          "table view: chips, toggle, a pick in the table saved and shown on the cards, exit 0; "
          "an attachment starts closed, opens, shows the file's text unrendered, a diff as numbered marked rows at its "
          "first opening, a non-diff and noted lines numbered by the server's line rule, an open one open (on the "
          "past-decisions page too), 131k rows drawn while the page answers, a hunk that does not add up as plain "
          "text, git format-patch output as a diff, blank rows a line high, no box a stand-in's height, exit 0; "
          "two open boxes of 262k rows in a hidden round drawn only when it is shown, the page answering; "
          "line notes: a press on a line's number opens a field under it, saved in attachment and line order with its "
          "quote cut by code point on the fixture's awkward lines, 50 at most, keys typed there, Esc back, Private "
          "hiding them in the Summary, restored on a reload, a changed file's notes apart and still saved, exit 0, "
          "read only on a done round and --history; "
          "an item's commands sit between its text and pictures as code blocks, unrendered, wrapped, Copy exact, a "
          "relative rm warns whatever the lang, Hungarian labels, exit 0, escaped in the Markdown copy, and on a done "
          "round and --history Copy is exact too; "
          "items the agent waits on: a badge in the title and the sidebar, a fourth Summary filter showing only them, "
          "Hungarian too, exit 0, three filters without the key; "
          "stakes and Cannot be undone in the margin and the sidebar, a settled item folded with its choice, no digit "
          "key on it, Change opens it, a pick saves, a reload folds it again with the pick, exit 0; folded on a "
          "phone in Focus it shows only its line and its margin, and a done round shows the line with no Change; "
          "a number item: slider and field bound both ways, typing (0 kept, off-grid snapped), arrows, j, no digit "
          "key, Skip, Accept all and undo, the Summary's value and flag, 390 px, Hungarian, JSON numbers, exit 0, "
          "read only when done; "
          "an old file's mark of the agent's shows its note in the comment box, another item's save sends it back "
          "as it was, and a pick on it makes it the user's; "
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
             "run, exit 0, then shows as sent in Answered, closed behind its count row (out of j, k and Focus), "
             "opened by a click, a match inside or its project's filter, kept open by the page's own reload; "
             "live sessions: two waiting ones counted, the background one's claude attach copied exactly, the "
             "interactive one pointed to its terminal, an idle one dimmer in Idle and counted nowhere, a third "
             "session of another project reloads the page by itself under its own project's heading, the groups "
             "following the order; "
             "Park on a round's open row moves it to Parked under Answered, closed, the tab title and state.json "
             "down at once with no new bar, Agent waiting on a live run's round, Back returns it, Parked opens on a "
             "click, its project's filter and a search, out of Focus while closed, kept open by the page's own "
             "reload, a project's Past decisions read only from the sidebar, nothing past the edge at 390 px; "
             "a panel row's start link opens its item in Focus (a hash naming nothing is ignored); "
             "a plan round from signoff plan: two options, none preselected, the plan open, Approve and Done, exit 0, "
             "the answers the mod takes as an approval; "
             "a round asked 3 days ago: the 2-day pill on its row (from the file's time, the clock on the run's "
             "start) and the note in its open row, its page's opening time, pill and note, in Focus too; its context "
             "on the row's second line, in its open row, found by the search and at the top of its page; ") +
          "2 rounds in the sidebar with their items, the margin recommendation, Focus with f, j and Esc, "
          "a non-recommended pick, both accepted, Summary filter, the sign-off sheet, Done, exit 0, "
          "2 answers paths on stdout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
