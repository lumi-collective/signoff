#!/usr/bin/env python3
"""Record the README's browser demo, media/demo-page.gif, from the real page.

    python3 media/demo/record_page.py            (from the repository root)

It copies two rounds of examples/demo into a temporary folder, starts Signoff on them, and drives a headless
Chrome over the DevTools protocol: pick an option, draw a mark, accept both rounds, look at the Summary, press
Done. Each step is a screenshot; ffmpeg turns them into a GIF. The data is the synthetic demo, the browser runs
in en-US and UTC, and the dot that moves around is drawn by this script to show where the pointer is.
Needs Google Chrome or Chromium (set CHROME to its path if it is not found) and ffmpeg.
"""
import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "media" / "demo-page.gif"
WIDTH, HEIGHT, GIF_WIDTH = 1280, 800, 1280
CHROMES = [os.environ.get("CHROME", ""), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/Applications/Chromium.app/Contents/MacOS/Chromium", "google-chrome", "chromium", "chromium-browser"]


def find_chrome():
    for c in CHROMES:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return shutil.which(c) or c
    sys.exit("Chrome not found; set CHROME to its path")


class Browser:
    """Just enough of the DevTools protocol: one page, commands and their results."""

    def __init__(self, profile):
        self.proc = subprocess.Popen([find_chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                                      "--no-first-run", "--no-default-browser-check", "--lang=en-US",
                                      "--user-data-dir=" + profile, "--remote-debugging-port=0", "about:blank"],
                                     stderr=subprocess.PIPE, universal_newlines=True,
                                     env=dict(os.environ, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8"))
        port = None
        while port is None:
            line = self.proc.stderr.readline()
            if not line:
                sys.exit("Chrome exited before it listened")
            m = re.search(r"ws://127.0.0.1:(\d+)/", line)
            port = int(m.group(1)) if m else None
        pages = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % port))
        path = [p for p in pages if p["type"] == "page"][0]["webSocketDebuggerUrl"].split(str(port), 1)[1]
        self.sock = socket.create_connection(("127.0.0.1", port))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, port, key)).encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            self.buf += self.sock.recv(4096)
        self.buf = self.buf.split(b"\r\n\r\n", 1)[1]
        self.mid = 0

    def _read(self, n):
        while len(self.buf) < n:
            self.buf += self.sock.recv(1 << 20)
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
                return msg.get("result", {})

    def js(self, expression):
        return self.call("Runtime.evaluate", expression=expression, returnByValue=True)["result"].get("value")

    def close(self):
        self.proc.terminate()
        self.proc.wait(10)


# The pointer dot: drawn by the recorder, not part of Signoff.
POINTER = """(function () {
  var p = document.getElementById('demo-pointer');
  if (!p) {
    p = document.createElement('div');
    p.id = 'demo-pointer';
    document.body.append(p);
    var s = p.style;
    s.position = 'fixed'; s.zIndex = 99; s.width = '22px'; s.height = '22px'; s.borderRadius = '50%%';
    s.background = 'rgba(232,89,12,.55)'; s.border = '2px solid #fff'; s.boxShadow = '0 0 0 1px rgba(0,0,0,.35)';
    s.pointerEvents = 'none'; s.transform = 'translate(-50%%,-50%%)';
  }
  p.style.left = %f + 'px'; p.style.top = %f + 'px';
})()"""


def main():
    work = Path(tempfile.mkdtemp(prefix="signoff-demo-"))
    demo = ROOT / "examples" / "demo"
    topic = work / "2026-01-05-settings"
    topic.mkdir()
    shutil.copytree(str(demo / "mockups"), str(topic / "mockups"))
    shutil.copyfile(str(demo / "round-02.json"), str(topic / "round-01.json"))
    shutil.copyfile(str(demo / "round-04.json"), str(topic / "round-02.json"))
    frames = work / "frames"
    frames.mkdir()
    server = subprocess.Popen([sys.executable, "-I", "-B", str(ROOT / "plugin" / "signoff" / "signoff.py"), str(topic),
                               "--no-open", "--timeout", "3"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)
    start = re.search(r"http://\S+", server.stderr.readline()).group(0)
    b = Browser(str(work / "profile"))
    shots = []

    def shot(hold):
        data = base64.b64decode(b.call("Page.captureScreenshot", format="png")["data"])
        name = frames / ("%03d.png" % len(shots))
        name.write_bytes(data)
        shots.append((name, hold))

    def point(x, y, hold=0.0):
        b.js(POINTER % (x, y))
        if hold:
            shot(hold)

    def box(selector):
        return json.loads(b.js("JSON.stringify(document.querySelector(%r).getBoundingClientRect())" % selector))

    def center(selector):
        r = box(selector)
        return r["x"] + r["width"] / 2, r["y"] + r["height"] / 2

    def click(selector, hold):
        """A real mouse click, so focus moves the way it does for a person."""
        x, y = center(selector)
        r = box(selector)
        shown = (r["x"] + r["width"] - 10, r["y"] + r["height"] - 6) if r["width"] > 60 else (x, y)  # off the label
        point(shown[0], shown[1], 0.5)
        for kind in ("mousePressed", "mouseReleased"):
            b.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", buttons=1 if kind == "mousePressed"
                   else 0, clickCount=1)
        time.sleep(0.4)
        point(shown[0], shown[1], hold)

    def scroll_to(selector, block="center"):
        b.js("document.querySelector(%r).scrollIntoView({block: %r})" % (selector, block))
        time.sleep(0.3)

    try:
        b.call("Emulation.setLocaleOverride", locale="en-US")
        b.call("Emulation.setTimezoneOverride", timezoneId="UTC")
        b.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
        b.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)
        b.call("Page.enable")
        b.call("Page.navigate", url=start)
        time.sleep(1.5)
        point(640, 400, 1.8)                                   # the page: two rounds, a Summary, Done waiting
        click("#r0-i0-o2", 1.0)                                # tick Owner too
        x, y = center("#panel-0 svg.marks")                    # draw a mark over the Filter button
        layer = json.loads(b.js("JSON.stringify(document.querySelector('#panel-0 svg.marks').getBoundingClientRect())"))
        x0, y0 = layer["x"] + layer["width"] * 0.49, layer["y"] + layer["height"] * 0.09
        x1, y1 = layer["x"] + layer["width"] * 0.56, layer["y"] + layer["height"] * 0.15
        point(x0, y0, 0.6)
        b.call("Input.dispatchMouseEvent", type="mousePressed", x=x0, y=y0, button="left", buttons=1, clickCount=1)
        for k in range(1, 5):
            xi, yi = x0 + (x1 - x0) * k / 4, y0 + (y1 - y0) * k / 4
            b.call("Input.dispatchMouseEvent", type="mouseMoved", x=xi, y=yi, button="left", buttons=1)
            point(xi, yi, 0.15)
        b.call("Input.dispatchMouseEvent", type="mouseReleased", x=x1, y=y1, button="left", buttons=0, clickCount=1)
        time.sleep(0.3)
        point(x1, y1, 0.8)
        b.js("document.querySelector('#panel-0 .mark-comment').focus()")
        for part in ("Drop ", "the Filter ", "button."):       # the mark's comment, typed
            b.call("Input.insertText", text=part)
            shot(0.35)
        time.sleep(1.0)                                        # let the autosave land
        scroll_to("#accept-0")
        click("#accept-0", 1.6)                                # accept: on to the next round
        click("#r1-i0-o1", 1.0)                                # Wording: the other label
        b.call("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13,
               modifiers=2)                                    # Ctrl+Enter accepts this round too
        b.call("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13,
               modifiers=2)
        time.sleep(0.8)
        b.js("scrollTo(0, 0)")
        point(1180, 32, 2.6)                                   # the Summary, and Done is ready
        click("#done", 3.2)                                    # Done: the agent wakes up
    finally:
        b.close()
    out, err = server.communicate(timeout=30)
    if server.returncode != 0:
        sys.exit("signoff exited %d: %s" % (server.returncode, err[-300:]))
    listing = work / "frames.txt"
    with open(str(listing), "w") as f:
        for name, hold in shots:
            f.write("file 'frames/%s'\nduration %.2f\n" % (name.name, hold))
        f.write("file 'frames/%s'\n" % shots[-1][0].name)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing.name, "-vf",
                    "fps=12,scale=%d:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=192:stats_mode=diff[p];"
                    "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" % GIF_WIDTH, str(OUT)],
                   cwd=str(frames.parent), check=True)
    for line in out.splitlines():
        print("answers:", Path(line).name)
    print("wrote %s (%d KiB, %d frames); the frames stay in %s" % (OUT, OUT.stat().st_size // 1024, len(shots), work))


if __name__ == "__main__":
    main()
