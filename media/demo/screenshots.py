#!/usr/bin/env python3
"""Make the screenshots in media/ from the real page: page-light, page-dark, page-phone, marks, board, summary and
history (.png).

    python3 media/demo/screenshots.py            (from the repository root)

It copies the four rounds of examples/demo and examples/history into a temporary folder, starts Signoff on them,
and drives a headless Chrome with record_page.py's DevTools driver: accept round 1, tick Owner and draw a mark in
round 2, shoot light, dark and phone width, the board in round 3, then accept the rest, shoot the Summary and press
Done; last, the --history page. The data is synthetic, the browser runs in en-US and UTC.
Needs Google Chrome or Chromium (set CHROME to its path if it is not found).
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from record_page import ROOT, Browser

MEDIA = ROOT / "media"
SIGNOFF = str(ROOT / "plugin" / "signoff" / "signoff.py")


def serve(args, work):
    # A home of its own, so a live inbox on this machine neither takes the run nor lends it its theme.
    env = dict(os.environ, SIGNOFF_INBOX_HOME=str(work / "no-home"))
    server = subprocess.Popen([sys.executable, "-I", "-B", SIGNOFF, "--no-open"] + args, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, env=env)
    link = re.search(r"http://\S+", server.stderr.readline())
    if not link:
        server.kill()
        sys.exit("signoff printed no start link")
    return server, link.group(0)


def main():
    work = Path(tempfile.mkdtemp(prefix="signoff-shots-"))
    demo = ROOT / "examples" / "demo"
    topic = work / "demo" / "notebook-app" / ".signoff" / "2026-01-05-settings"  # a project: its name in the header
    for name in ("mockups", "boards", "notes"):
        shutil.copytree(str(demo / name), str(topic / name))
    for n in range(1, 5):
        shutil.copyfile(str(demo / ("round-%02d.json" % n)), str(topic / ("round-%02d.json" % n)))
    history = work / "notebook-app"
    shutil.copytree(str(ROOT / "examples" / "history"), str(history))
    servers = []
    b = Browser(str(work / "profile"))

    def size(width, height, scale=1, mobile=False):
        b.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=scale, mobile=mobile)

    def scheme(value):
        b.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": value}])
        time.sleep(0.4)

    def box(selector):
        return json.loads(b.js("JSON.stringify(document.querySelector(%r).getBoundingClientRect())" % selector))

    def shot(name, selector=None):
        params = {"format": "png"}
        if selector:  # just that element: a window tall enough for all of it, the element at the top
            size(1280, 2000)
            b.js("document.querySelector(%r).scrollIntoView({block: 'start'})" % selector)
            time.sleep(0.5)
            r = box(selector)
            params["clip"] = {"x": r["x"], "y": r["y"], "width": r["width"], "height": r["height"], "scale": 1}
        (MEDIA / name).write_bytes(base64.b64decode(b.call("Page.captureScreenshot", **params)["data"]))
        if selector:
            size(1280, 1000)
        print("wrote media/%s" % name)

    def press(holder, pattern):  # the first element under holder whose text matches, such as a board's switch
        if not b.js("(e => e ? (e.click(), true) : false)([...document.querySelectorAll(%r)].find(e => /%s/"
                    ".test(e.textContent)))" % (holder, pattern)):
            sys.exit("nothing to press: %s %s" % (holder, pattern))
        time.sleep(0.6)

    def mouse(kind, x, y):
        b.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1,
               buttons=0 if kind == "mouseReleased" else 1)

    def click(selector):
        b.js("document.querySelector(%r).scrollIntoView({block: 'center'})" % selector)
        r = box(selector)
        if not r["width"]:  # hidden: the click would land on nothing
            sys.exit("not shown: %s" % selector)
        x, y = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
        mouse("mousePressed", x, y)
        mouse("mouseReleased", x, y)
        time.sleep(0.6)

    def top():
        b.js("scrollTo(0, 0); document.querySelector('body > main').scrollTop = 0")
        time.sleep(0.3)

    try:
        b.call("Emulation.setLocaleOverride", locale="en-US")
        b.call("Emulation.setTimezoneOverride", timezoneId="UTC")
        server, start = serve([str(topic), "--timeout", "5"], work)
        servers.append(server)
        size(1280, 1000)
        scheme("light")
        b.call("Page.navigate", url=start)
        time.sleep(1.5)
        click("#accept-0")                                    # round 1 accepted; on to round 2
        click("#r1-i0-o2")                                    # tick Owner, not recommended
        layer = "#panel-1 svg.marks"                          # a mark over the Filter button, with a comment
        b.js("document.querySelector(%r).scrollIntoView({block: 'center'})" % layer)
        r = box(layer)
        x0, y0 = r["x"] + r["width"] * 0.49, r["y"] + r["height"] * 0.09
        x1, y1 = r["x"] + r["width"] * 0.56, r["y"] + r["height"] * 0.15
        mouse("mousePressed", x0, y0)
        mouse("mouseMoved", (x0 + x1) / 2, (y0 + y1) / 2)
        mouse("mouseReleased", x1, y1)
        time.sleep(0.3)
        b.js("document.querySelector('#panel-1 .mark-comment').focus()")
        b.call("Input.insertText", text="Drop the Filter button; the pills are enough.")
        time.sleep(1.2)                                       # let the autosave land
        shot("marks.png", "#panel-1 section.item")
        top()
        shot("page-light.png")
        scheme("dark")
        shot("page-dark.png")
        click("#tab-2")                                       # round 3: the live board, dark at 768 px
        press("#panel-2 .board-bar button", "^Dark$")
        press("#panel-2 .board-bar button", "^768 px$")
        time.sleep(1.0)                                       # the board loads again in its new theme
        shot("board.png", "#panel-2 section.item")
        scheme("light")
        size(390, 844, 2, True)                               # a phone opens in Focus; the hash picks round 2
        page = b.js("location.href.split('#')[0]")
        b.js("location.hash = 'focus=1/filters'")
        b.call("Page.reload")
        time.sleep(1.5)
        top()
        shot("page-phone.png")
        size(1280, 1000)                                      # the same page wide again, out of Focus
        b.call("Page.navigate", url=page)
        time.sleep(1.5)
        for ri in (1, 2, 3):
            click("#tab-%d" % ri)
            if ri == 3:
                click("#r3-i0-o1")                            # Wording: the other label
            click("#accept-%d" % ri)
        click("#tab-4")
        shot("summary.png")
        click("#done")
        _, err = server.communicate(timeout=30)
        if server.returncode != 0:
            sys.exit("signoff exited %d: %s" % (server.returncode, err[-300:]))
        server, start = serve(["--history", str(history)], work)
        servers.append(server)
        b.call("Page.navigate", url=start)
        time.sleep(1.5)
        press("#h-list .h-entry", "round 2:")
        top()
        shot("history.png")
    finally:
        for server in servers:
            if server.poll() is None:
                server.terminate()
                server.wait(10)
        b.close()
    print("the run's folder stays in %s" % work)


if __name__ == "__main__":
    main()
