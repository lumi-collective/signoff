"""The MCP server for other agents: JSON-RPC over stdio, two non-blocking tools, and runs that outlive the call."""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from support import ROOT, Folder, done_record, sample_decisions, write_outside

import signoff

SERVER = str(ROOT / "plugin" / "signoff" / "mcp_server.py")


def children(marker):
    """Process ids of Signoff runs whose command line names marker (a test's own folder): /proc where there is one
    (slim Linux images have no ps), PowerShell's process list on Windows, ps elsewhere."""
    if os.name == "nt":
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process | ForEach-Object { '{0} {1}' -f $_.ProcessId, $_.CommandLine }"],
                             capture_output=True, universal_newlines=True, timeout=120).stdout
        return [int(line.split(None, 1)[0]) for line in out.splitlines()
                if "signoff.py" in line and marker in line and "Get-CimInstance" not in line]
    if os.path.isdir("/proc/self"):
        found = []
        for entry in os.listdir("/proc"):
            try:
                with open("/proc/%s/cmdline" % entry, "rb") as f:
                    args = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
            except (OSError, ValueError):
                continue
            if entry.isdigit() and "signoff.py" in args and marker in args:
                found.append(int(entry))
        return found
    out = subprocess.run(["ps", "-A", "-o", "pid=,args="], capture_output=True, universal_newlines=True).stdout
    return [int(line.split(None, 1)[0]) for line in out.splitlines() if "signoff.py" in line and marker in line]


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


class Client:
    def __init__(self, tmp=None, **env_extra):
        # The server's run folders go to the test's own temporary folder, so nothing is left behind.
        env = dict(os.environ, TMPDIR=tmp, TEMP=tmp, TMP=tmp, **env_extra) if tmp else None
        self.proc = subprocess.Popen([sys.executable, "-I", "-B", SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, universal_newlines=True, encoding="utf-8", env=env)
        self.next_id = 0
        self.seen = []  # every line the server wrote, to check that no link or token ever appears

    def send(self, obj):
        self.proc.stdin.write((obj if isinstance(obj, str) else json.dumps(obj)) + "\n")
        self.proc.stdin.flush()

    def read(self):
        line = self.proc.stdout.readline()
        self.seen.append(line)
        return json.loads(line)

    def call(self, method, params=None):
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        reply = self.read()
        assert reply["id"] == self.next_id, reply
        return reply

    def tool(self, name, **args):
        result = self.call("tools/call", {"name": name, "arguments": args})["result"]
        return result, json.loads(result["content"][0]["text"])

    def close(self):
        if self.proc.stdin.closed:
            return
        self.proc.stdin.close()
        self.proc.wait(10)
        self.proc.stdout.close()
        self.proc.stderr.close()


def spend_link(link_file):
    """Do what the user does with open_browser=false: take the start link from the file and open it."""
    deadline = time.monotonic() + 30  # a slow CI runner can take seconds to start the run
    while True:
        m = re.search(r"http://\S+", Path(link_file).read_text(encoding="utf-8"))
        if m:
            break
        if time.monotonic() > deadline:
            raise AssertionError("no start link in %s after 30 s" % link_file)
        time.sleep(0.05)
    with urllib.request.urlopen(m.group(0), timeout=5) as resp:
        return m.group(0), resp.geturl()


def post_done(url):
    origin = "/".join(url.split("/")[:3])
    req = urllib.request.Request(url + "save/0", method="POST", data=json.dumps(
        {"answers": {}, "free_text": "", "done": True}).encode(), headers={"Content-Type": "application/json",
                                                                          "Origin": origin})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status


class Protocol(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.client = Client(self._tmp.name)

    def tearDown(self):
        self.client.close()

    def test_handshake_and_tools(self):
        init = self.client.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                               "clientInfo": {"name": "test", "version": "1"}})["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertIn("tools", init["capabilities"])
        self.assertEqual(init["serverInfo"]["name"], "signoff")
        self.client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})  # no reply to a notification
        self.assertEqual(self.client.call("ping")["result"], {})
        tools = {t["name"]: t for t in self.client.call("tools/list")["result"]["tools"]}
        self.assertEqual(sorted(tools), ["signoff_open", "signoff_status"])
        self.assertEqual(tools["signoff_open"]["inputSchema"]["required"], ["path"])
        self.assertEqual(tools["signoff_status"]["inputSchema"]["required"], ["run_id"])

    def test_errors(self):
        self.assertEqual(self.client.call("nope")["error"]["code"], -32601)
        self.client.send("{not json")
        self.assertEqual(self.client.read()["error"]["code"], -32700)
        self.assertEqual(self.client.call("tools/call", {"name": "nope", "arguments": {}})["error"]["code"], -32602)
        result, body = self.client.tool("signoff_status", run_id="missing")
        self.assertTrue(result["isError"])
        result, body = self.client.tool("signoff_open", path="/no/such/decisions.json")
        self.assertTrue(result["isError"])
        result, body = self.client.tool("signoff_open")
        self.assertTrue(result["isError"])

    def test_bad_messages_never_stop_the_server(self):
        for params in ([1], {"name": ["x"]}, {"name": "signoff_open", "arguments": [1]}, "x"):
            reply = self.client.call("tools/call", params)
            self.assertEqual(reply["error"]["code"], -32602, params)
        result, body = self.client.tool("signoff_open", path="bad\u0000path")
        self.assertTrue(result["isError"])
        result, body = self.client.tool("signoff_status", run_id=["x"])
        self.assertTrue(result["isError"])
        self.client.send("[" * 100000)
        self.assertEqual(self.client.read()["error"]["code"], -32700)
        self.client.send('{"jsonrpc": "2.0", "id": 99, "method": 5}')
        self.assertEqual(self.client.read()["error"]["code"], -32600)
        self.assertEqual(self.client.call("ping")["result"], {})


class Runs(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        self._tmp = tempfile.TemporaryDirectory()
        # Cleanups, not tearDown: they run after a test's own (an inbox.lock it holds), and Windows removes no file
        # still open.
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self._tmp.cleanup)
        self.client = Client(self._tmp.name)
        self.client.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "test", "version": "1"}})

    def tearDown(self):
        self.client.close()
        # End every run this test started and wait for it; a run still alive after that fails the test.
        for pid in children(str(self.folder.path)):
            os.kill(pid, signal.SIGTERM)  # on Windows: TerminateProcess
        deadline = time.monotonic() + 10
        # On Windows os.kill(pid, 0) would end a process, so there only the process list tells.
        while time.monotonic() < deadline and [pid for pid in children(str(self.folder.path))
                                               if os.name == "nt" or not gone(pid)]:
            time.sleep(0.5 if os.name == "nt" else 0.1)
        survivors = children(str(self.folder.path))
        self.assertEqual(survivors, [], "a Signoff run outlived its test")

    def wait_for(self, run_id, states=("done", "timeout", "error")):
        for _ in range(300):
            result, body = self.client.tool("signoff_status", run_id=run_id)
            if body["state"] in states:
                return result, body
            time.sleep(0.1)
        self.fail("run never finished")

    def test_lifecycle(self):
        result, body = self.client.tool("signoff_open", path=str(self.folder.decisions), open_browser=False)
        self.assertFalse(result["isError"])
        self.assertEqual(body["state"], "waiting")
        link_file = Path(body["link_file"])
        self.assertTrue(link_file.exists())
        _, page = spend_link(link_file)
        self.assertEqual(self.client.tool("signoff_status", run_id=body["run_id"])[1]["state"], "waiting")
        self.assertEqual(post_done(page), 200)
        result, final = self.wait_for(body["run_id"])
        self.assertEqual(final, {"run_id": body["run_id"], "state": "done", "answers_paths": [str(self.folder.answers)]})
        self.assertFalse(link_file.parent.exists())  # its own temp files are gone once the run is over
        self.assertEqual(self.client.tool("signoff_status", run_id=body["run_id"])[1], final)
        self.assertFalse(any("/b/" in line or "127.0.0.1:" in line for line in self.client.seen), self.client.seen)

    def test_invalid_decisions_is_an_error_state(self):
        data = sample_decisions()
        data["items"][0]["recommended"] = "three"
        self.folder.write(data)
        result, body = self.client.tool("signoff_open", path=str(self.folder.decisions), open_browser=False)
        if body["state"] != "error":
            result, body = self.wait_for(body["run_id"])
        self.assertIn("recommended", body["message"])
        self.assertEqual(body["answers_paths"], [])

    def test_run_outlives_the_mcp_server(self):
        result, body = self.client.tool("signoff_open", path=str(self.folder.decisions), open_browser=False)
        link_file = Path(body["link_file"])
        for _ in range(100):
            if "http://" in link_file.read_text(encoding="utf-8"):
                break
            time.sleep(0.05)
        self.client.close()
        link, page = spend_link(link_file)  # the server is gone; the run still answers
        self.assertIn("/b/", link)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(link, timeout=5)  # a second use stops the run
        if os.name != "nt":
            deadline = time.monotonic() + 10
            while children(str(self.folder.path)) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertEqual(children(str(self.folder.path)), [], "the run did not stop after the second use")
        else:
            # No process list here: the run has stopped once it lets go of its log, which Windows will not delete
            # while it is open.
            deadline = time.monotonic() + 10
            while True:
                try:
                    link_file.unlink()
                    break
                except PermissionError:
                    if time.monotonic() > deadline:
                        self.fail("the run did not stop after the second use")
                    time.sleep(0.1)

    def test_with_the_inbox_open_the_questions_are_there_and_no_link_is_named(self):
        home = Path(self._tmp.name) / "home"
        home.mkdir()
        fd = signoff.try_lock(home / "inbox.lock")  # the inbox lives: the run becomes a waiter
        self.addCleanup(signoff.unlock, fd)
        self.client.close()
        self.client = Client(self._tmp.name, SIGNOFF_INBOX_HOME=str(home))
        result, body = self.client.tool("signoff_open", path=str(self.folder.decisions), open_browser=False)
        self.assertEqual(body["state"], "waiting")
        self.assertNotIn("link_file", body)
        self.assertIn("inbox", body["note"])
        write_outside(self.folder.answers, done_record())
        result, final = self.wait_for(body["run_id"])
        self.assertEqual(final["state"], "done")

    def test_a_page_run_that_says_something_before_its_link_still_hands_over_the_link(self):
        home = Path(self._tmp.name) / "home"
        (home / "inbox.lock").mkdir(parents=True)  # unusable: the run says so on stderr, then runs its own page
        self.client.close()
        self.client = Client(self._tmp.name, SIGNOFF_INBOX_HOME=str(home))
        result, body = self.client.tool("signoff_open", path=str(self.folder.decisions), open_browser=False)
        self.assertEqual(body["state"], "waiting")
        self.assertIn("link_file", body)
        self.assertNotIn("inbox", body["note"])

    def test_a_run_left_open_is_ended_by_the_test(self):
        result, body = self.client.tool("signoff_open", path=str(self.folder.decisions), open_browser=False)
        self.assertEqual(body["state"], "waiting")
        self.assertEqual(len(children(str(self.folder.path))), 1)  # tearDown must end it, or it fails


if __name__ == "__main__":
    unittest.main()
