#!/usr/bin/env python3
"""Signoff for agents other than Claude Code: an MCP server over stdio (newline-delimited JSON-RPC 2.0).

    python3 -I mcp_server.py

Two tools, neither of which blocks:
  signoff_open({path, open_browser=true})  starts a Signoff run in its own process and returns {run_id, state}
  signoff_status({run_id})                 returns {state: waiting|done|timeout|error, answers_paths: [...]}

A run outlives the call and this server. Tool results never carry the page's token or the start link: they would
end up in a transcript. The browser opens itself; with open_browser=false the result names a file that
carries the single-use start link, for the user to open; with the Signoff inbox open there is no link, and the
result says the questions are in the inbox. Run ids live as long as this server process; the answers files
on disk are the lasting record. Python 3.9+, standard library only.
"""
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIGNOFF = HERE / "signoff.py"
PROTOCOL = "2025-06-18"
VERSION = "0.4.0"

TOOLS = [
    {"name": "signoff_open",
     "description": "Open a Signoff page for the user: a decisions JSON file, or a folder of round-NN.json files "
                    "(see docs/decisions-file.md in the Signoff repository for the format). Returns at once with a run_id; call signoff_status "
                    "later to learn whether the user pressed Done, and where the answers files are.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["path"], "properties": {
         "path": {"type": "string", "description": "Absolute path of the decisions file or the folder of rounds."},
         "open_browser": {"type": "boolean", "default": True,
                          "description": "Open the user's browser. False when there is no browser on this machine."}}}},
    {"name": "signoff_status",
     "description": "The state of a run from signoff_open: waiting (the page is open), done (the user pressed "
                    "Done), timeout (idle for 120 minutes; answers so far are kept) or error. answers_paths lists the "
                    "answers JSON files to read, in tab order. An answer with \"private\": true holds text the user "
                    "asked you not to repeat in a commit, your memory, a cloud service or a decision log.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["run_id"], "properties": {
         "run_id": {"type": "string"}}}},
]

runs = {}  # run_id -> {"proc", "dir", "result"}


class ToolError(Exception):
    pass


def _spawn(args, out, err):
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": out, "stderr": err, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # not killed with this server or by a hangup of its terminal
    return subprocess.Popen(args, **kwargs)


def open_run(args):
    path = args.get("path")
    if not isinstance(path, str) or not path:
        raise ToolError("path is required: the decisions file or the folder of rounds")
    try:
        target = Path(path).expanduser().resolve()
        exists = target.exists()
    except (OSError, ValueError, RuntimeError):  # a NUL byte, a link loop
        raise ToolError("path is not a usable file name")
    if not exists:
        raise ToolError("%s does not exist" % target)
    open_browser = args.get("open_browser", True)
    if not isinstance(open_browser, bool):
        raise ToolError("open_browser must be true or false")
    run_dir = Path(tempfile.mkdtemp(prefix="signoff-run-"))  # 0700: the start link is written here
    command = [sys.executable, "-I", "-B", str(SIGNOFF), str(target)] + ([] if open_browser else ["--no-open"])
    with open(run_dir / "stdout.txt", "wb") as out, open(run_dir / "link.txt", "wb") as err:
        os.chmod(str(run_dir / "link.txt"), 0o600)
        proc = _spawn(command, out, err)
    run_id = secrets.token_hex(8)
    runs[run_id] = {"proc": proc, "dir": run_dir, "result": None}
    # Wait briefly for the link, or the waiter's line saying the inbox is open (a warning may come before either),
    # so an invalid decisions file is reported at once and the note below says the right thing.
    for _ in range(100):
        said = (run_dir / "link.txt").read_text(encoding="utf-8", errors="replace")
        if proc.poll() is not None or "http://" in said or "the inbox is open" in said:
            break
        time.sleep(0.05)
    result = status({"run_id": run_id})
    said = ""
    if result["state"] == "waiting":  # once a run is over its files are gone
        said = (run_dir / "link.txt").read_text(encoding="utf-8", errors="replace")
    # A page run may say something else before its link; only a waiter says the inbox is open and prints none.
    if result["state"] == "waiting" and "the inbox is open" in said and "http://" not in said:
        # With the Signoff inbox open the run only waits for the answers: there is no link of its own.
        result["note"] = ("The questions are in the Signoff inbox, which is open; there is no link to open. The user "
                          "finds them there (signoff inbox open shows the inbox).")
    elif result["state"] == "waiting" and not open_browser:
        result["link_file"] = str(run_dir / "link.txt")
        result["note"] = ("No browser was opened. Ask the user to open the http:// link in link_file (usually its first "
                          "line) in a browser on this machine. It works once. Do not read it into the conversation.")
    return result


def _cleanup(run):
    # The run's own temporary files: its stdout copy and the file that held the start link.
    for name in ("stdout.txt", "link.txt"):
        try:
            os.unlink(str(run["dir"] / name))
        except OSError:
            pass
    try:
        os.rmdir(str(run["dir"]))
    except OSError:
        pass


def status(args):
    run_id = args.get("run_id")
    run = runs.get(run_id) if isinstance(run_id, str) else None
    if run is None:
        raise ToolError("unknown run_id %r; run ids last as long as this server process" % (run_id,))
    if run["result"]:
        return dict(run["result"])
    code = run["proc"].poll()
    if code is None:
        return {"run_id": run_id, "state": "waiting"}
    out = (run["dir"] / "stdout.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    err = (run["dir"] / "link.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    paths = [line for line in out if line.strip()]
    result = {"run_id": run_id, "answers_paths": paths}
    if code == 0:
        result["state"] = "done"
    elif code == 2:
        result["state"] = "timeout"
    else:
        # Never pass on a line with the link in it; the last other line says what went wrong.
        reasons = [line for line in err if "http://" not in line and line.strip()]
        result.update(state="error", message=(reasons[-1] if reasons else "Signoff stopped (exit code %d)" % code))
    run["result"] = result
    _cleanup(run)
    return dict(result)


def handle(msg):
    """One JSON-RPC message in, the reply out (None for a notification)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "invalid request"}}
    if "id" not in msg:
        return None  # notifications/initialized and the like need no reply
    method, params, reply = msg["method"], msg.get("params") or {}, {"jsonrpc": "2.0", "id": msg["id"]}
    if not isinstance(params, dict):
        reply["error"] = {"code": -32602, "message": "params must be an object"}
        return reply
    if method == "initialize":
        version = params.get("protocolVersion") if isinstance(params, dict) else None
        reply["result"] = {"protocolVersion": version if isinstance(version, str) else PROTOCOL,
                           "capabilities": {"tools": {"listChanged": False}},
                           "serverInfo": {"name": "signoff", "version": VERSION}}
    elif method == "ping":
        reply["result"] = {}
    elif method == "tools/list":
        reply["result"] = {"tools": TOOLS}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        tool = {"signoff_open": open_run, "signoff_status": status}.get(name) if isinstance(name, str) else None
        if tool is None or not isinstance(args, dict):
            reply["error"] = {"code": -32602, "message": "unknown tool %r" % (name,)}
            return reply
        try:
            body, failed = tool(args), False
        except ToolError as e:
            body, failed = {"error": str(e)}, True
        except OSError as e:
            body, failed = {"error": "cannot start Signoff: %s" % (e.strerror or e)}, True
        except Exception as e:  # one bad call must not end the server and lose track of the runs
            body, failed = {"error": "unexpected error: %s" % type(e).__name__}, True
        reply["result"] = {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body,
                           "isError": failed}
    else:
        reply["error"] = {"code": -32601, "message": "unknown method %r" % method}
    return reply


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except (ValueError, RecursionError):
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    for run in runs.values():  # runs still open keep their files; they are needed until the user is done
        if run["result"] is None and run["proc"].poll() is not None:
            _cleanup(run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
