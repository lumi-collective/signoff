# AGENTS.md

Instructions for coding agents working on Signoff. The README says what it does for users.

## Layout

- `plugin/`: the plugin, and all an install gets. Nothing outside it may be needed at run time.
- `plugin/.claude-plugin/plugin.json`: the manifest, with the listing icon beside it. `.claude-plugin/marketplace.json`
  (repo root): this repo as its own marketplace, `source: ./plugin`.
- `plugin/README.md`: the directory listing's text: where it works, what it runs, reads and sends. Text
  only: no images, and no file of the plugin named in a code block. Keep it in step with the main README.
- `plugin/bin/signoff`, `plugin/bin/signoff.cmd`: launchers that run `signoff.py` with the given arguments.
  The `bin/` folder also keeps the directory listing to Claude Code.
- `plugin/skills/signoff/SKILL.md`: the skill that tells an agent when to use Signoff, where to put the decisions
  folder, how to run it in the background and what to do with the answers.
- `plugin/signoff/signoff.py`: the server and command line. Python 3.9+, standard library only. Importable:
  `load_decisions`, `check_answers`, `make_server`, `main`.
- `plugin/signoff/page.html`: the page. One static file, no build step, no external requests. The server fills in
  `__NONCE__` and `__DATA__` on each request.
- `plugin/signoff/schema.json`: the decisions file's JSON Schema. A test keeps it in step with `signoff.py`.
- `plugin/signoff/mcp_server.py`: the MCP server for other agents (stdio JSON-RPC, two non-blocking tools). Not
  registered by the plugin.
- `tests/`: unittest suites. `support.py` makes a temporary topic folder (one file, or `round-NN.json`
  rounds) and runs a server on a thread.
- `examples/demo/`: synthetic decisions with mockups: `decisions.json` as one round, and the same questions as
  `round-01.json` to `round-04.json`, the source of the README screenshots; `boards/` holds a board.
- `examples/history/`: a synthetic project for `--history`, its answers and outcome files committed.

## Tests

Run all of them before you finish:

```
claude plugin validate plugin
claude plugin validate .claude-plugin/marketplace.json
python3 -I -B -m unittest discover -s tests
python3 -I -B tests/e2e_browser.py
```

`python3 -I -B tests/e2e_browser.py` drives the server and the page in headless Chrome (real mouse clicks, start
link to Done) and checks the exit code, stdout and the answers files. It is not a `test_*.py`, so discovery skips
it. CI runs it on Linux and Windows; run it yourself after a change to the page or the run flow (needs Chrome,
`CHROME=<path>` if not found). It proves the server and the page in a browser, not a Claude Code session.

They must also pass on Python 3.9. Write the failing test first, then the fix. Do not weaken a test to make
it pass.

## Invariants

- **Security rules are not optional.** Bind `127.0.0.1` on port 0; a fresh token in the path on every
  request; `Host` equal to `127.0.0.1:<port>`; a save (`POST /<token>/save/<round>`) needs `application/json`,
  the page's own `Origin`, and `Sec-Fetch-Site: same-origin` when present; a body within its round's limit,
  checked against that round's decisions file. Each rule has a test that sends the bad request; a new route
  gets the same tests.
- **The browser opens a single-use start link** (`/b/<start token>/`, a second token), never the page URL. Its
  first use redirects to the page; a second use gets 410, stores nothing more and stops the run with exit code
  1. A Done within `DONE_GRACE` seconds of the first use ends the run only when that time is up. No cookies.
- **Only listed files are served.** Pictures and boards by number, each resolved inside the decisions file's
  folder; read into memory at start for a decisions run, read on request (with the same link check) for the
  past-decisions page. No SVG, no directory listing, no path from the request ever touches the disk.
  Attachments (`.md`, `.txt`) follow the same folder rule, are read at start, on both pages, and go inside
  the page data as text; no route serves them. Every such path is checked as text before it is resolved: no
  control character, and no drive-relative or network path in Windows syntax (`C:foo`, `\\host`, `//host`), on
  every OS. An absolute local path still works for pictures and boards inside the folder, as in 0.1.0; attachments
  take only relative paths.
- **Boards stay sandboxed.** Boards are served under their own token (`board_token`), which opens nothing else.
  Every board response carries `BOARD_CSP` (`sandbox allow-scripts`, nothing loaded from outside),
  and the page embeds it with `sandbox="allow-scripts"` and never `allow-same-origin`.
- **The past-decisions page is read only.** No POST route answers there (405), nothing is written.
- **The MCP server never returns a token or a start link**, and removes only its own temporary files.
- **Agent text is never HTML.** The page uses `textContent` and DOM methods only: no `innerHTML`, no inline
  `style` attribute, no inline event handler. A test greps the page for these.
- **The CSP stays strict.** Every script and style element carries the nonce. Nothing loads from outside.
- **Nothing is deleted.** Signoff writes the answers file and its Markdown copy (both atomically) and, only
  when absent, a `.gitignore`.
- **The Markdown copy escapes everything.** Agent and user text go through `md_text`; nothing in it may render
  as Markdown or HTML.
- **stdout is answers paths and nothing else**, one per line, in tab order. Agents parse it. Messages go to
  stderr.
- **Exit codes are a contract:** 0 Done, 1 invalid input (a usage error, every round already done, or the
  start link used twice), 2 idle timeout, 130 Ctrl+C.
- **Done waits for acceptance.** With several rounds open, a Done save is refused (409) until every other open
  round is accepted; Done accepts its own round, so a single round stays one click.
- **No stale writes.** Every save from the page carries `rev`, the round's save count it last saw; an older one
  gets 409 and the page asks for a reload. The server also refuses (409) to write over an answers file that changed
  on disk since it last read or wrote it (the hash of the bytes it wrote, never a read-back), for example from a
  second run on the same folder; loading the page takes such a file in, so a reload lets saving go on, and a file
  that says done closes the round. Within a run this is exact; across runs it is best effort (no shared lock). A
  round closed by Done shows read only on reload, even while the run goes on.
- **Done is final.** Once a round's save with `done: true` is stored, later saves to it get 409, and a later
  run shows it read only. The run ends when every round that was open is done.
- **The file formats only grow.** Add optional keys; never rename or remove one. Update `schema.json`, the
  README table and the skill in the same change.

## Style

Plain, short sentences in docs and comments. A comment says why, not what. No new dependencies.
