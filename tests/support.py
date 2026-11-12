"""Shared fixtures for the tests: temporary decision folders with tiny PNGs, and a server running in a thread."""
import http.client
import json
import os
import struct
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin" / "signoff"))

# Every run a test starts, in this process or a child, gets a Signoff home that does not exist: it runs as with no
# inbox set up, and never touches the user's real home or meets a real inbox. A test that needs a home makes one.
_NO_HOME = tempfile.TemporaryDirectory()
os.environ["SIGNOFF_INBOX_HOME"] = str(Path(_NO_HOME.name) / "home")
# The same for Uptick's files (vulns.json, scan-repos.json): never the user's real ones.
os.environ["XDG_CACHE_HOME"] = str(Path(_NO_HOME.name) / "cache")
os.environ["XDG_CONFIG_HOME"] = str(Path(_NO_HOME.name) / "config")

import signoff  # noqa: E402


def png_bytes():
    """A valid 2x2 PNG, made here so no binary fixture is needed."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x80\x40" * 2 for _ in range(2))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def sample_decisions():
    return {
        "version": 1,
        "title": "Pick the settings layout",
        "intro": "My recommendation is preselected.",
        "items": [
            {
                "id": "layout",
                "group": "Layout",
                "title": "Two columns or one?",
                "description": "The settings page today is one long column.",
                "cost": "About 40 lines.",
                "images": [{"src": "mockups/a.png", "caption": "Two columns", "boxes": [[10, 10, 40, 30], [55, 10, 40, 30]]}],
                "select": "one",
                "options": [{"id": "two", "label": "Two columns"}, {"id": "one", "label": "Keep one", "detail": "no work"}],
                "recommended": "two",
            },
            {
                "id": "filters",
                "title": "Which filters?",
                "select": "many",
                "options": [{"id": "date", "label": "Date"}, {"id": "owner", "label": "Owner"}, {"id": "tag", "label": "Tag"}],
                "recommended": ["date", "tag"],
                "comment": False,
            },
        ],
        "free_text": {"title": "Anything else", "placeholder": "e.g. naming"},
    }


class Folder:
    """A temporary decision folder: decisions.json plus mockups/a.png."""

    def __init__(self, decisions=None):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name).resolve()
        (self.path / "mockups").mkdir()
        (self.path / "mockups" / "a.png").write_bytes(png_bytes())
        self.decisions = self.path / "decisions.json"
        self.write(sample_decisions() if decisions is None else decisions)

    def write(self, data):
        self.decisions.write_text(json.dumps(data), encoding="utf-8")

    @property
    def target(self):
        return self.decisions

    @property
    def answers(self):
        return self.path / "decisions.answers.json"

    def cleanup(self):
        self._tmp.cleanup()


def done_record(answers=None):
    """An answers file as Signoff writes it after Done."""
    return {"version": 1, "done": True, "saved": "2026-01-01T12:00:00Z", "answers": answers or {}, "free_text": ""}


def write_outside(path, record):
    """An answers file written by another program, the way Signoff writes one."""
    data = record if isinstance(record, str) else json.dumps(record)
    for _ in range(50):
        try:
            return signoff.write_atomic(path, data)
        except PermissionError:  # Windows: the run may have the file open for its once-a-second look
            time.sleep(0.05)
    raise AssertionError("could not write %s" % path)


class Rounds:
    """A temporary topic folder: round-NN.json files sharing mockups/a.png and mockups/b.png."""

    def __init__(self, count=2):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name).resolve()
        (self.path / "mockups").mkdir()
        (self.path / "mockups" / "a.png").write_bytes(png_bytes())
        (self.path / "mockups" / "b.png").write_bytes(png_bytes() + b"b")
        for n in range(1, count + 1):
            data = sample_decisions()
            data["title"] = "Round %d" % n
            data["tab"] = "R%d" % n
            if n % 2 == 0:
                data["items"][0]["images"][0]["src"] = "mockups/b.png"
            self.write(n, data)

    target = property(lambda self: self.path)

    def round(self, n):
        return self.path / ("round-%02d.json" % n)

    def write(self, n, data):
        self.round(n).write_text(json.dumps(data), encoding="utf-8")

    def answers(self, n):
        return self.path / ("round-%02d.answers.json" % n)

    def finish(self, n, answers=None):
        self.answers(n).write_text(json.dumps(done_record(answers)), encoding="utf-8")

    def cleanup(self):
        self._tmp.cleanup()


class Running:
    """A Signoff server on a background thread, as the tests drive it."""

    def __init__(self, folder):
        self.server = signoff.make_server(folder.target)
        self.port = self.server.server_address[1]
        self.token = self.server.token
        self.host = "127.0.0.1:%d" % self.port
        self.origin = "http://" + self.host
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = {"Host": host or self.host}
        hdrs.update(headers or {})
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        # skip_host: the Host header always comes from hdrs, so a test can send a wrong one.
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in hdrs.items():
            conn.putheader(k, v)
        if body is not None and "Content-Length" not in hdrs:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def save(self, state, round_=0, **extra):
        headers = {"Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin"}
        headers.update(extra)
        return self.request("POST", "/%s/save/%d" % (self.token, round_), state, headers)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
