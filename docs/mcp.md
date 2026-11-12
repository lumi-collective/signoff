# Other agents: the MCP server

**Experimental:** the plugin does not register this server, and the snippets below are untested.

Claude Code uses the command line and the skill. For other agents, `plugin/signoff/mcp_server.py` is an MCP server over
stdio, standard library only, with two tools that never block:

- `signoff_open({path, open_browser})` starts a Signoff run for a decisions file or a folder of rounds and returns
  at once with `{run_id, state}`. The run is its own process and outlives the call and the server.
- `signoff_status({run_id})` returns `{state, answers_paths}`, `state` being `waiting`, `done`, `timeout` or
  `error`.

No tool result ever contains the page's token or the start link, since results end up in transcripts. The
browser opens itself; with `open_browser: false` the result names a file whose first line carries the start
link, for you to open. Run ids last as long as the server process; the answers files are the lasting record.

The plugin does not register this server: that would add its tools to every Claude Code session. Point your
agent at a clone of this repository. These snippets follow each tool's documented format but are **untested**:

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.signoff]
command = "python3"
args = ["-I", "/path/to/signoff/plugin/signoff/mcp_server.py"]
```

Cursor (`~/.cursor/mcp.json`) and Cline (`cline_mcp_settings.json`):

```json
{"mcpServers": {"signoff": {"command": "python3", "args": ["-I", "/path/to/signoff/plugin/signoff/mcp_server.py"]}}}
```
