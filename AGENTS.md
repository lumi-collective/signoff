# AGENTS.md

Instructions for coding agents working on Signoff. The README says what it does for users.

## Layout

- `plugin/`: the plugin, and all an install gets. Nothing outside it may be needed at run time.
- `plugin/.claude-plugin/plugin.json`: the manifest, with the listing icon beside it. `.claude-plugin/marketplace.json`
  (repo root): this repo as its own marketplace, `source: ./plugin`.
- `plugin/README.md`: the directory listing's text: where it works, what it runs, reads and sends. Text
  only: no images, and no file of the plugin named in a code block. Keep it in step with the main README.
- `plugin/bin/signoff`, `plugin/bin/signoff.cmd`: launchers that run `signoff.py` with the given arguments
  (`signoff` with `py -3` in Git Bash when `py` is there, else `python3`). The `bin/` folder also keeps the
  directory listing to Claude Code.
- `plugin/hooks/hooks.json`, `plugin/hooks/hook.sh`: the plugin's hooks for the inbox. Claude Code runs them in
  every session (SessionStart, UserPromptSubmit, Notification with the matcher `permission_prompt`, PostToolUse,
  PostToolUseFailure, Stop, StopFailure, SessionEnd). The command exits 0 when
  `hook.sh` is not there (a plugin folder swapped out under a running session), else execs it; `hook.sh` runs
  `signoff.py inbox hook` with `python3` and always exits 0. They need `sh` (Git Bash on Windows). On Windows
  without Git Bash, Claude Code runs a hook's command in PowerShell: each command (and the monitor's) opens with
  ``echo `# >$null <#` >/dev/null;`` and closes the sh part with `#>`, so PowerShell echoes into `$null` and skips
  the rest as a block comment (the monitor then sleeps); `test_hooks.py` checks the commands token by token (no
  PowerShell runs in CI). On Windows `hook.sh` exits before Python (the monitor sleeps): the inbox does not run
  there. On macOS and Linux, without `SIGNOFF_INBOX_HOME`, `hook.sh` exits before Python when the default home
  has no live inbox (see the hooks rule), SessionStart only when it has no `inbox.lock`; it mirrors only those
  defaults of `inbox_home()`, and a test keeps the two in step.
  PostToolUse and PostToolUseFailure run `hook.sh clear`: there it also exits before Python when that home's
  `waiting.flag` is empty or absent, tested first. SessionStart has two groups: `startup` runs `hook.sh start` (on macOS with
  `/Applications/Signoff.app` or `~/Applications/Signoff.app` there, it first keeps the home's `launcher` link, see
  the hooks rule, in `sh`); `resume|clear|compact` runs `hook.sh session`.
- `plugin/hooks/register.tsx`: the plugin's mod, the hooks module `hooks.json` names under `modules` (Claude Code's
  docs give 2.1.286 as the first to run mods; its test runs on 2.1.309 in CI and 2.1.322; 2.1.200 skips the key
  and runs the hooks, checked). The band (`Signoff: N waiting`, Open, Hide),
  the toast, the status line entry, the panel (the `signoff` pane) and the answer to `/signoff:open`
  (`plugin/commands/open.md`: a mod cannot
  register a name with a colon, so the file declares it and the mod's `command.run` hook answers it; its body is
  the prompt the model gets when no mod runs). Its session values are declared in `plugin/types/index.d.ts`
  (`types` in `plugin.json`); the `hint` option in `plugin.json` is `band`, `status` or `off`, without an
  `options` list: Claude Code 2.1.200 refuses the whole plugin, hooks and skills too, over that key (a test checks).
  `plugin/hooks/register.test.tsx` is its test, run by `claude plugin test plugin`.
- `plugin/monitors/monitors.json`: the plugin's monitor for the inbox. Claude Code starts it in every interactive
  session (not with `-p`) and hands each line it prints to the model. It runs `hook.sh monitor`, which runs
  `signoff.py inbox monitor`. A monitor that ends costs the session a model turn, so it never ends while its
  session lives (see the monitor rule).
- `plugin/skills/signoff/SKILL.md`: the skill that tells an agent when to use Signoff, where to put the decisions
  folder, how to run it in the background and what to do with the answers. Its description is in every
  session's skill list, so it stays short, and the inbox's text is in `plugin/skills/signoff-inbox/SKILL.md`,
  whose description loads it only while the inbox is in play (todo markers, messages, `inbox done`).
  SKILL.md loads whole on every use, so it stays under 9 KiB (`test_skill.py`): the less used fields are in
  `fields.md` beside it, and what to do once Signoff exits (the exit codes, the answers file, the actions) in
  `answers.md`. It links each by a relative path (a test checks that every `.md` beside it is linked), and they
  hold no `${...}`, which only SKILL.md gets filled in. It reads two options of `plugin.json`, strings as `hint`
  is: `folder` (the decisions folder) and `chat` (the largest batch of short text-only questions that goes to
  AskUserQuestion instead, 0 to 4; any other value counts as 4); a test checks that the directory listing names
  each option as a setting.
- `plugin/signoff/signoff.py`: the server and command line. Python 3.9+, standard library only. Importable:
  `load_decisions`, `check_answers`, `make_server`, `main`. Also the inbox (`signoff inbox serve|open|stop`,
  `InboxServer`), its `todo.md` reader (`parse_todos`, `markdown_blocks`), its control socket client
  (`inbox_request`, which also takes an op's other fields; `signoff inbox rows`), and the request files (`read_request`, `request_line`; `signoff inbox monitor|done`). The
  inbox needs Unix sockets, so it does not run on Windows yet.
- `plugin/signoff/page.html`: the page. One static file, no build step, no external requests. The server fills in
  `__NONCE__` and `__DATA__` on each request. Its colours sit between the `<!-- theme: -->` and `<!-- /theme -->`
  markers, the output of `tests/theme_tokens.py`, pasted in by hand.
- `THIRD_PARTY_NOTICES` and `plugin/THIRD_PARTY_NOTICES`: the same text, the credits and MIT text for the palettes'
  colours. The plugin copy is there because an install gets only `plugin/`; a test keeps the two equal.
- `plugin/signoff/schema.json`: the decisions file's JSON Schema. A test keeps it in step with `signoff.py`.
- `plugin/signoff/mcp_server.py`: the MCP server for other agents (stdio JSON-RPC, two non-blocking tools). Not
  registered by the plugin.
- `tests/theme_tokens.py`: the palettes' upstream colours mapped onto the page's tokens, with the contrast
  fixes; `python3 -I -B tests/theme_tokens.py` prints the page's theme part. Not needed at run time.
  `test_theme.py` fails while the page and its output differ, and measures its list of pairs on the page itself.
- `tests/`: unittest suites. `support.py` makes a temporary topic folder (one file, or `round-NN.json`
  rounds) and runs a server on a thread. It points `SIGNOFF_INBOX_HOME` at a missing folder for every test and
  child process, so a run is the plain run unless a test passes its own home; `XDG_CACHE_HOME` and
  `XDG_CONFIG_HOME` likewise, so no test reads or writes the user's Uptick files. `test_contract_http.py` checks
  the security and run contract against the command line as a separate process, over HTTP, its exit code and
  stdout only; `SIGNOFF_CONTRACT_CMD` (POSIX shell words: forward slashes in a Windows path) swaps in another
  implementation. It must take the same arguments (`--no-open`, `--timeout`, `--history`), print its
  `http://` start link on stderr, and honour `SIGNOFF_INBOX_HOME` (and `XDG_CACHE_HOME`, `XDG_CONFIG_HOME` for
  Uptick's files). Its inbox must touch `inbox.lock` at each look, as the mod reads that time. It must be the
  server process itself (a
  launcher that execs, as `plugin/bin/signoff` does): the suite stops it with kill, SIGINT, SIGTERM and SIGHUP. Its `Inbox` class
  starts `<command> inbox serve` and takes start links from the control socket; its `InboxOffer` class starts
  another for a `runs/` entry it writes by hand (a waiter with the suite's own pid) and asks it for `offer`,
  `open` with the offer and `accept`; its `InboxRows` class starts a third for a project's `runs/` entry and asks
  it for `rows`, on the socket and through `<command> inbox rows <folder>`, and for `open` with a row's target,
  on the socket and through `<command> inbox open --run <n> --focus <round>/<item> --inbox <k>`, and for
  `app_target` and `app_open` on the socket only (`--app` runs `open -a`, and an app started that way gets none
  of the suite's environment). `test_inbox.py` covers the
  inbox in process: the `todo.md` reader and Markdown tree, routes, pairs, locks, the waiter it wakes, messages,
  the OSV section, the theme it keeps, the answer from a notification, the panel's rows and their targets, and
  `inbox open --app` with its app opener (`APP_OPEN`) swapped for a stand-in and its app folders (`APP_DIRS`)
  for temporary ones: no test runs the real `open` or finds an installed app.
  `test_hooks.py` covers the hooks, the SessionStart hand-over, the monitor, `inbox done` and `hook.sh`.
- `examples/demo/`: synthetic decisions with mockups: `decisions.json` as one round, and the same questions as
  `round-01.json` to `round-04.json`, the source of the screenshots in the README and `docs/`; `boards/` holds a board.
- `examples/history/`: a synthetic project for `--history`, its answers and outcome files committed.
- `app/`: Signoff, an optional macOS app (Go, Wails v3.1.0 pinned in `go.mod`, cgo) around the inbox. Not
  part of the plugin; the plugin never needs it. `build.sh` builds `app/bin/Signoff.app` (git-ignored),
  ad-hoc signed, without npm: the window loads the inbox's page. The Homebrew cask (its own repo,
  `lumi-collective/homebrew-signoff`) runs `build.sh` with `SIGNOFF_LAUNCHER=link` on the user's Mac from a
  release's source tarball; no prebuilt app is shipped anywhere. The app runs the plugin's `signoff` command,
  fixed at build time (`SIGNOFF_LAUNCHER`, default this checkout's `plugin/bin/signoff`), since an app started
  from the Dock sees no shell PATH. `SIGNOFF_LAUNCHER=link` (and a bare `go build`) fixes none: the app then
  works out the home as `inbox_home()` does on macOS, holds it to its home rule, and runs what the home's
  `launcher` link points to (an absolute link to a regular, executable file of the user's or root's; without one
  it says to start a Claude Code session with the plugin installed from its marketplace once, or to build with
  the fixed launcher for a plugin run from a git checkout, which keeps no link). It asks `signoff inbox home` for the home (with
  the link, it must be the same home),
  starts `signoff inbox serve` in its own session when no inbox holds `inbox.lock` (output to `logs/inbox.log`,
  as `inbox open` does), takes a start link from the control socket, and shows the count of `state.json` as a
  Dock badge and a notification when it rises. That notification is the inbox's `offer` when it has one
  (`notify.go`: its four texts, Accept & send and Other answer…, in the `signoff.offer` category), else the
  number. Wails runs the response callback in the delegate before its completion handler, so it only starts a
  goroutine. Closing the window hides it (its page, still asking for `state`,
  keeps its page token open while the app runs); a Dock click loads a fresh start link into it. `tray.go` adds a
  menu bar item: the icon `icon/menubar.png` (a template image, embedded), the same count as the badge beside it,
  a left click toggling a 420×600 frameless panel under it that hides when it loses focus, and a right-click menu
  (Open Signoff, Quit Signoff) that hides the panel first. The panel loads a start link of its own, taken on its
  first click and again only when the inbox it came from is gone (stopped, or another `server_id` in
  `inbox.json`) or the panel was hidden for an hour or more: a page that asks nothing (a round's page, or a list
  whose `state` poll stopped at an error) is closed after `PAGE_IDLE` of awake time. A Dock click likewise reloads
  an open window whose inbox has another `server_id`. `nav_darwin.m` adds the navigation policy to Wails' webview delegate, which has none, and shuts
  its `wails://` scheme. macOS only; notifications work only when the app runs from an Applications folder
  (`~/Applications` was checked), the badge from anywhere. The window is a plain macOS window with its own title
  bar. The icon is `app/icon/Signoff.icns`, made from `icon/icon.svg` by `icon/build-icon.sh icns` (headless
  Chrome, `iconutil`), and `icon/menubar.png` from `icon/menubar.svg` by `icon/build-icon.sh menubar`; run the one
  whose SVG changed, by hand. The page's header and sidebar mark is a CSS sketch of it
  (no check mark).

## Tests

Run all of them before you finish:

```
claude plugin validate plugin
claude plugin validate .claude-plugin/marketplace.json
claude plugin test plugin
python3 -I -B -m unittest discover -s tests
python3 -I -B tests/e2e_browser.py
```

`python3 -I -B tests/e2e_browser.py` drives the server and the page in headless Chrome (real mouse clicks, start
link to Done) and checks the exit code, stdout and the answers files. It is not a `test_*.py`, so discovery skips
it. CI runs it on Linux and Windows; run it yourself after a change to the page or the run flow (needs Chrome,
`CHROME=<path>` if not found). It proves the server and the page in a browser, not a Claude Code session.

To run the contract suite alone against another command:
`SIGNOFF_CONTRACT_CMD='<command>' python3 -I -B -m unittest discover -s tests -p test_contract_http.py`.

They must also pass on Python 3.9. Write the failing test first, then the fix. Do not weaken a test to make
it pass.

After a change to `app/` (macOS, Go 1.26 or newer), also:

```
cd app && go vet ./... && go test ./...
```

`TestRealInbox` runs the plugin's own inbox in a temporary home through the app's steps, so a change to the
control socket, `inbox.json` or `state.json` shows there too. CI does not run the Go tests.

## Invariants

- **Security rules are not optional.** Bind `127.0.0.1` on port 0; a fresh token in the path on every
  request; `Host` equal to `127.0.0.1:<port>`; a save (`POST /<token>/save/<round>`) needs `application/json`,
  the page's own `Origin`, and `Sec-Fetch-Site: same-origin` when present; a body within its round's limit,
  checked against that round's decisions file. Each rule has a test that sends the bad request, in process and
  in `tests/test_contract_http.py` against a separate process; a new route gets the same tests in both.
- **The browser opens a single-use start link** (`/b/<start token>/`, a second token), never the page URL. Its
  first use redirects to the page; a second use gets 410 (its text tells the user to ask the agent to open
  Signoff again), stores nothing more and stops the run with exit code 1 (stderr tells the agent to run the same
  command again). A Done within `DONE_GRACE` seconds of the first use ends the run only when that time is up. No cookies.
- **The inbox hands out start links only on its control socket.** `inbox.sock` in the Signoff home: a Unix
  socket, 0600, in the 0700 home; one JSON line in (`{"op": "open"}`, `{"op": "stop"}`, `{"op": "offer"}`,
  `{"op": "accept", "offer": <id>}`, `{"op": "open", "offer": <id>}`, `{"op": "rows", "project": <id>}`,
  `{"op": "open", "run": <n>, "round": <n>, "item": <n>, "inbox": <n>}`, the same four with `"op": "app_target"`,
  or `{"op": "app_open"}`; at
  most 4 KiB), one out. Each `open`
  makes a pair: a start link that works once within `START_TTL` seconds, and the page token it
  opens, closed after `PAGE_IDLE` seconds without a request. A second use, or a late first one, gets 410 (404 once
  the inbox has dropped the closed pair) and closes that pair only (its page token opens nothing more); the inbox
  and other pairs go on. Each connection gets its own thread and 5 seconds in all. The home must be a folder of
  the user's own, not a link. No route, log line or file ever holds a link or a token. Its routes,
  all under a live page token with the same Host rule: `GET /<page>/` (the list), `GET /<page>/state` (the
  count as in `state.json`, JSON), `GET /<page>/run/<n>/` (a
  run's rounds on today's page), `GET /<page>/run/<n>/img/<k>`, `POST /<page>/run/<n>/save/<round>` (today's
  save rules), `POST /<page>/request` (a message on a todo item or a finding handed over, today's save rules with
  `MAX_REQUEST_BODY`; see the request rule), `POST /<page>/scan` (Scan this project, the same rules; see the OSV
  rule), `POST /<page>/prefs` (the theme kept, the same rules with `MAX_PREFS_BODY`; see the theme rule), and each
  run's boards under that run's own board token. The list page asks for `state` every
  5 seconds, so an open tab keeps its page token alive; a closed one expires after `PAGE_IDLE`.
- **An answer from a notification is its round's Done, asked for on the control socket.** `offer` names at most
  one item, in the first round of Waiting on you that: belongs to a run whose round locks the inbox holds; is that
  run's last open round; has exactly one item not yet decided (decided: a saved `choice` that is not null or an
  empty list, with a `source` other than `recommended`); and that item has `recommended` (not an empty list) and
  is not `sensitive`. The reply is `{offer: null}` or `{offer: {id, project, round, question, answer}}`: `id` 32
  hex digits; `project` the project's name as the list shows it (a run outside any project: its decisions
  folder's name, the login name when that folder is the home itself), the other three the decisions file's own
  texts. Each is one line (every control, format and line-separator character a space), cut to
  `MAX_OFFER_TEXT`. Signoff adds no path, link or token of its own, and replaces the project's folder with its
  name and the user's home with `~` (as the OSV section does), but does not filter the agent's text: a URL in it
  stays, and so does the folder written through a link (the inbox knows only its real path). The inbox keeps an offer in memory only (its run, round,
  item and the round's `rev`), at most `MAX_OFFERS`, for `OFFER_TTL` seconds; no log line or file holds its id.
  `accept` uses it once: a second use, a late one or an unknown id gets `gone`. It saves through the run's own
  writer the saved answers, with the item's `recommended` as its choice and `source: "notification"` (its
  comment, marks and privacy kept), `accepted` and `done` true, at the offer's `rev`. Every save rule of the page
  holds (`rev`, the file on disk, Done is final, the Done gate, the round lock), and each refusal is one word
  (`changed`, `held`, `done`, `failed`) with nothing written (a `failed` write may leave a new Markdown copy, as a
  failed save from the page does: the JSON goes last). `accept` and `open` with an offer look at the runs first, so a round
  that left its group since the offer (its run ended and it is no orphan round, see the round-lock rule) makes it
  `gone` (and `open`'s link the list's); an orphan round keeps its offer while its round file is unchanged. The client never sends a choice. `open` with an
  offer gives a start link whose first use opens that run's page at that item in Focus (`#focus=<round>/<item>`);
  with an unknown offer, the list's.
- **The mod's panel list is numbers and one-line titles.** `rows` takes `project`, a project id (16 hex digits;
  anything else gets `{error}`), and answers `{rounds: [{run, round, title, item, items: [{item, title}]}], more,
  inbox}`: each
  open round of that project's live runs and orphan rounds (held ones too), oldest run first, `run` its number (`run/<n>/`),
  `round` its index in the run, `title` its `tab` or else its `title`, `item` the index its own row opens (its
  first item not yet decided, else its last, whose Focus shows the round's tail with its Accept and Done), and its
  items not yet decided (as for `offer`) by their index in the round. Titles follow the `offer` rule: one line, cut to `MAX_OFFER_TEXT`, the
  project's folder and the user's home replaced, the agent's text otherwise as written. At most `MAX_ROWS` rounds
  and items in all, so the reply fits the 64 KiB line `inbox_request` reads (a test checks the worst case);
  `more` counts the rest. No path, link, token or item id of its own (a URL the agent wrote in a title stays).
  `signoff inbox rows <folder>` asks it for the project `project_root()` finds for that absolute folder (the
  hooks' project) and prints the reply as one JSON line; for a folder outside any project it prints the empty
  list without asking an inbox. It never starts an inbox. `inbox` is the inbox's own number, drawn at its start
  (1 to 2^31 - 1): a run's number is never reused while an inbox lives, but a restart numbers the runs anew.
- **A panel row opens its item and nothing else.** `open` with `run`, `round`, `item` and `inbox` (all four or
  none; each a whole number from 0 below 2^31, never a bool, and no `offer` beside them; else `{error}`) gives a
  start link whose first use opens `run/<run>/#focus=<round>/<item id>` when `inbox` is this inbox's number, that
  run is live or holds orphan rounds, that round is open in it (held too) and the item index exists; the server maps the index to the id.
  Otherwise, the list's link, as an unknown offer gets. It waits for the inbox's look, as `open` with an offer
  does; a plain one never does. `signoff inbox open --run <n> --focus <round>/<item> --inbox <k>` (with `--no-open` or not, each
  once, in any order; plain decimal numbers) asks for it, starting an inbox as `open` does (a new one has another
  number, so its link is the list's). The mod runs it for a row:
  an item's row opens that item, a round's row its `item`, All projects the plain `open`. A rows reply that lists
  a round without `item`, or rounds without `inbox`, is another shape to the mod. An inbox from 0.3.0 ignores
  the four keys and gives the list's link.
- **A panel row for the app is kept in the inbox's memory, never in a URL.** `app_target` takes exactly `run`,
  `round`, `item` and `inbox` (the panel row rule's numbers, and no other key; else `{error}`) and answers
  `{ok: true}`: the inbox keeps that one target for the app, in memory only, the last one kept winning, for
  `APP_TARGET_TTL` (60) seconds of awake time, as a launch takes seconds. `app_open` (no other key) takes it
  once: `{link}`, a start link as `open` with those
  numbers gives (that item in Focus, or the list when it is gone, done or from another inbox), or `{link: null}`
  when none waits (a late one, a used one, an inbox restarted since). Nothing else takes it. `signoff inbox open
  --app --run <n> --focus <round>/<item> --inbox <k>` (macOS only; the four in any order, without `--no-open`)
  first finds the app where the mod looks (`APP_DIRS`): the first `Signoff.app` in `/Applications`, then
  `~/Applications`, that is a folder (not a link) owned by the user or root, whose `Contents/Info.plist` has
  `CFBundleIdentifier` `io.github.lumi-collective.signoff.inbox`; with none it exits 1 and keeps nothing. Then it
  keeps the target, and runs `/usr/bin/open -a <that app's path>` (`APP_OPEN`) with no other argument, so it
  starts the copy it checked, not another one Launch Services knows. When that fails or runs past
  `APP_OPEN_TIMEOUT` (20) seconds, within the mod's 30 for the whole command, it takes the target back with
  `app_open` (the link is dropped, never printed; it takes whatever target waits). Its check is stricter than the
  mod's (which follows a link and checks no owner), so a linked or another user's copy gets an App button whose
  press says the app did not open. With no inbox answering, or one
  from before these ops, it opens the app all the same, which shows what it showed. It never starts an inbox.
  There is no URL scheme: it would let any web page open the app.
- **`state.json` counts what waits, with no text.** The inbox alone writes it in the home, atomically, 0600:
  `schema, at, waiting, projects[{id, monogram, waiting}]`. `waiting` is the number of rows in Waiting on you
  (open rounds of live runs, orphan rounds, todo chains whose top item or a step is marked waiting, each once, and live sessions waiting on the
  user; never an Idle session), the same list the page shows; `id` is null for a run
  outside any project. It is written at start and whenever the count changes, from the inbox's own look every
  `STATE_EVERY` seconds; `at` says when. Never a title, path, todo text, link or token. It stays when the inbox
  stops: a reader trusts it only while `inbox.lock` is held. A reader that cannot lock (the mod) trusts it only
  while `inbox.lock`'s time is within `LIVE_WITHIN` (15 s) of now: the inbox touches it at each look, every
  `STATE_EVERY` seconds, and a stopped, killed or stuck inbox does not.
- **The mod costs a session nothing and shows no link.** It runs in every interactive session of everyone who
  installs the plugin, so it starts no process to look: every `EVERY` (5) seconds it checks `inbox.lock` in the
  homes `hook.sh` mirrors (or `SIGNOFF_INBOX_HOME`; no test keeps this copy in step, and a wrong one only keeps
  the band empty) through `$.fs`, and only once one is there it runs
  `${$.plugin.root}/bin/signoff inbox home`, once per load of the mod (its module state: a reload asks again).
  Then it reads, through `$.fs` only, `inbox.lock`'s
  time, `projects/*.json` (again only when the folder's time changed; the session's project is the longest
  `root` holding its folder) and `state.json` (again only when its time or size changed; `schema` 1, a whole
  `waiting` for its project id, files over 64 KiB skipped). Any failure shows nothing. It shows that number,
  fixed text and the panel's titles, nothing else; it writes no file. It starts looking once per load, from `session.start` or the band's
  first drawing (a reload after an options change may fire no `session.start`). With `hint` `off`, with
  `claude -p` and on Windows (`OS` is `Windows_NT`) it looks at nothing. Only a live count (an inbox that lives,
  a project found) moves Hide (cleared by any other live count) and the toast (once a session, after a live 0 and
  two live counts above it in a row). Open and `/signoff:open` (typed at the prompt, also with `hint` `off`) open
  the panel: `$.ui.open` of the `signoff` pane before anything else is awaited (so it is the person's own press
  or command, placed at any width), with `focus` only on a press of Open (after the command the person may type
  on, and a digit would press a row), then one run of `signoff inbox rows <session folder>`, once per opening and
  never on a look; only its stdout is read, as JSON of that shape (anything else, a non-zero exit or a cut
  stdout: the panel says no inbox answered, and its last row is "Open the inbox", which starts one). The panel's
  heading counts the rounds listed and `more`; with none listed while the band's count is above 0 it says those
  wait in the inbox (todo items, sessions). It lists each round, then its items, one row each (each title one
  line again, no control, format or line-separator character, at most 200 characters, cut to the width; past 24
  rows the rest goes into `more`), and "All projects in the inbox"; digits 1 to 9 press the first rows, `a` All
  projects, `q` Close. A pane that is not placed draws the same tree in the band, with no hotkey and Close on its
  heading row, until the engine places it (a wider terminal): one surface draws the list at a time, and the count
  row steps aside while the pane is shown. Each row runs `signoff inbox open` with its target (see the panel row
  rule) and All projects the plain one. When the opening found the app (`Signoff.app` in `/Applications` or
  `~/Applications` whose `Info.plist` names the bundle id, read through `$.fs`, never on a look), each round
  and item row ends in an App button with no hotkey (Tab and Enter, or a click),
  which runs `signoff inbox open --app` with the row's target. All of these run one at a time (a
  press while one runs does nothing), and read only its exit code, never its stdout or
  stderr (a start link may be there), and an error only by its name; the command's line goes to a toast, not the
  conversation (the command's output row would go into the model's requests). For origin `sdk`, as with
  `claude -p`, the command opens the inbox and its line is the output; on Windows it says the inbox does not run
  there. A test gives `inbox open` and `inbox rows` a link on stderr and terminal escapes in the titles, and
  checks every text the mod shows.
- **The inbox writes a round only while it holds its lock.** It takes `<round file>.lock` for the open rounds
  of each live run in `runs/` (no `ended`, the pid alive, the file not older than `RUNS_MAX_AGE`), each file once
  however many runs wait on it (flock is per open file); when another process holds one, that run's rounds are
  shown read only and saves get 409 (a file system that cannot lock: one stderr line, best effort, as for a
  run). Each run keeps its own page and Done gate, as on its own page; runs on the same rounds share one. It
  lets go at its next look after the run ended or died (it looks every `STATE_EVERY` seconds and at each load
  of the list), unless they are orphan rounds. An orphan round is open (no answers file with `done: true`), its
  file is there, a regular file of the user's own, with no `<round>.outcome.json` beside it, a recent `runs/`
  entry of a project (`project` not null; ended or dead alike) lists it, and no live run does. A group whose run
  ended or died stays, as orphan, under its number, while it has an open round, each open round is an orphan
  round and no round file's time changed since the group read it; else it goes (its finished rounds to the sent
  list) and lets go, and a changed round is read again in a new group, also when a live run comes on its rounds,
  so its offers are `gone` and its old tab's saves get 404. Orphan rounds in no group go into one
  group per newest gone run that lists them, locked and saved as a live run's, its Done gate over them; its row
  in Waiting on you has `orphan: true` and `since` that run's `started`. A live run whose rounds are exactly a
  group's (as `runs/` lists them: the open ones) takes that group on, live, with its number and open tabs; a
  live run that lists any of its rounds otherwise ends it, so its old tab's saves get 404. A page run whose round lock the
  inbox took after the run's own look (the inbox started in between) looks again and is a waiter. Saves go through
  `check_answers` and the run's own writer, so a waiter wakes on the answers file. One inbox per home: it holds `inbox.lock` while it lives, trying for about half a second first (a run's look takes the
  lock for a moment).
- **The inbox reads a project's todo file and never writes it.** The first of `_bmad-output/todo.md`, `TODO.md`,
  `todo.md` under the root in `projects/<id>.json` that is a file inside the root, at most `MAX_TODO` bytes. An
  open item is `- [ ] text` (`*` and `+` too) with its indented lines; one waiting on the user starts
  `- [ ] (waiting on you: <what>) text`, exactly so (English only). A chain is one row: a top-level item that is
  open, or done with an open item under it, with every item under it (`TODO_DEPTH` deep) as its steps, never a row
  of their own. The row is in Waiting on you when the item or any open step is marked; it carries its steps as
  `[done, all]`, its hint and message ref from one item (the first marked item with a ref, the top item first; else
  the first marked one's hint and its own ref when open, else the first open step's), `park` (Later is offered only
  when that ref is a marked item's), and the requests on every open item's ref of the chain. When its text names a round, `<topic folder> / round-NN` or
  a path ending `<topic folder>/round-NN.json` (at most `MAX_ROUND_LINKS`), that is open in a live run of the same
  project or is an orphan round of it, the row gets that run's page path (`run/<n>/`), never the round's path. The server turns the Markdown
  into a tree of plain data (`markdown_blocks`), with patterns that stay linear on a hostile line and tables cut at
  `MAX_COLUMNS`, short rows not padded, lists and quotes rendered `MD_DEPTH` deep, and a row's item past
  `MAX_ITEM`, or past what is left of `MAX_TODO` for all rows together, shown by the start of its first line only.
  The list shows a row's first line, its hint and steps; its body only when opened, where the page marks each open
  step that starts with the marker (`wait` on its list item in the tree, set by the server). The list
  page's data is compact JSON. A test bounds what one todo file adds to it at 28 times `MAX_TODO` for the worst
  shapes found (about 22 times, measured on the parsed rows, which hold more than the page gets), and another
  times hostile lines in a full-size file. The
  page builds it with DOM methods: HTML stays text, only `http(s)` links become links
  (`rel="noopener noreferrer"`), and a code block's Copy button copies its text exactly; so does the Copy beside
  an inline code span, and Copy text takes the whole item as plain text (what the page shows, not the Markdown
  source). A code block (of no language, `text` or a shell: `bash`, `sh`, `zsh`, `shell`, `console`, `powershell`,
  `pwsh`, `cmd`, `bat`) or inline code span, never one inside a link, whose command is `rm`, `rmdir`, `trash`, `del` or `Remove-Item` with a path that
  is not absolute gets a warning line, only a hint: a word starting with `~`, `$`, `%`, `/`, `\` or a drive letter
  counts as absolute. The inbox never runs a
  command. It reads `sessions/` files no older than `SESSIONS_MAX_AGE` and shows "working" only when the
  project's session said so at or after the answer.
- **A message on a todo item is a request file, never a todo file write.** `POST /<page>/request` takes exactly
  `{project, ref, text}` as strings: `text` one line of 1 to `MAX_REQUEST_TEXT` characters after trimming (no
  control, line-separator or lone surrogate character), `project` a project in `projects/`, `ref` the ref of an
  item open in its todo file now, a chain's step too (409 otherwise). An item's `ref` is its first line as written, after the waiting
  marker, trimmed and cut to `MAX_REF` characters: it survives edits to other lines and to the item's body, not to
  that line (the page then no longer shows the request on the item; the agent still has the text), and two open
  items with the same first line share their requests. The same route takes exactly `{kind: "vuln", project,
  ref}` for a finding: `ref` (`<name>@<version>`) must be a finding the list shows now (409 otherwise), and the
  text is the server's own, built from the finding's level, advisory ids and fixed version, each checked against a
  plain pattern; no free text from Uptick's file goes into it. It takes exactly `{kind: "later", project, ref}`
  for Later on a waiting row: `ref` must be an open item marked waiting now (409 otherwise), and the text is the
  server's own `LATER_TEXT` (park the item: take its marker off, keep it open). The inbox
  writes `requests/<project>/<id>.json` (`schema, id, kind, ref, text, at`; `id` 16 hex digits, `kind` `todo`,
  `vuln` or `later`; a `later` reaches the agent as a message on the todo item), atomically, 0600 in 0700 folders. The list page shows each item's and finding's recent
  requests (text, time, done) and never an id or a path. The agent marks one handled with
  `signoff inbox done <id>`, which writes an empty `<id>.done` beside it; nothing else writes there.
- **The OSV section reads Uptick's files and writes one only on a click.** Uptick's `vulns.json`
  (`$XDG_CACHE_HOME`, else `~/.cache`, then `uptick/`; at most `MAX_VULNS` bytes) and `scan-repos.json`
  (`$XDG_CONFIG_HOME`, else `~/.config`, then `uptick/`), found as Uptick finds them; a relative base counts as
  absent. Without a readable `vulns.json`, or for a project whose folder is gone, there is no section (and a scan
  of that project gets 404). A project matches an entry and a list line by
  `os.path.samefile`. Its window: twice Uptick's `interval.ok` (a positive integer of seconds, the entry's own before
  the file's top-level one; at most `OSV_FRESH_MAX`), else `OSV_FRESH`. Its state: not on the list `off`; no entry
  `stale`; an `error` tried within the window `error`; `at` (Unix seconds) older than the window, 0 or ahead of now
  `stale`; `gaps` (a non-empty list) `partial`; a count `found`;
  else `clean` (the page's "No known vulnerabilities"). Findings (at most `MAX_FINDINGS`) only from a fresh `at`.
  Text from the file reaches the page one line, capped, with the project's folder and the user's home replaced
  by names; `sources` never. Findings never count in Waiting on you or `state.json`. `POST /<page>/scan` takes
  exactly `{project}`: it adds the project's real path to `scan-repos.json`, keeping every other entry and key,
  and makes `uptick/` (0700) when absent; the file it writes is 0600, its JSON ASCII-escaped. It leaves the file
  alone (409) when it is a link, not JSON with a `repos` list, or over `MAX_SCAN_LIST` bytes; it reads the file again just before the atomic replace and starts over when it changed
  (409 after three tries). A project already listed is a 200 with no write.
- **A request reaches the model as one line.** The monitor and the SessionStart hook print it with
  `request_line()`: a fixed frame, the ref and the text as JSON strings (cut to `MAX_REF` and `MAX_REQUEST_TEXT`,
  every control, format and line-separator character a space), and the id. Neither string can end its quotes,
  start a line or change the id. Never a path, a token or another project's request.
- **The monitor never ends by itself and prints only requests.** It runs in every interactive session of
  everyone who installs the plugin (the status line shows it), and Claude Code turns both its lines and its ending
  into model turns, so `inbox monitor` never ends while its session lives and prints only request lines. While
  `inbox_on()` is false it sleeps (in `hook.sh`, without Python, for the default homes and on Windows), prints nothing, writes
  nothing. For the default homes `hook.sh` also sleeps while the inbox is not live (the hooks' rule), looking every
  30 seconds, and then execs Python, which stays for the session even after the inbox stops. With the inbox on, it reads `requests/<project>/` of its folder's project every `REQUEST_EVERY` seconds
  and prints one line for each request written since `SINCE_MARGIN` seconds before its session started
  (`SIGNOFF_SINCE` from `hook.sh`; a request near the start may also come in the hand-over: twice beats never; the
  margin is a guess, the gap between the hook and the monitor's start is not measured), not
  marked done and not printed before; what it printed it keeps in memory only. It writes nothing. Every live
  session of the project gets each request. It ends when its parent (Claude Code) is gone or stdout breaks; on an
  error it says one stderr line (which reaches neither the user nor the model) and sleeps. Known limit: a live
  session without a working monitor (`claude -p`, a monitor stopped by an error) still counts as live, so new
  sessions of the project get no hand-over while it runs.
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
- **The CSP stays strict.** Every executable script and style element carries the nonce (the inert JSON data
  block is the exception). Nothing loads from outside.
- **Colours are theme tokens.** The page's colours are CSS custom properties, set only in the theme part: one
  nonce'd style element per palette and mode, keyed on `data-palette` and `data-mode` on `<html>`, never an inline
  style or `cssText`. Every block sets the same tokens. No `data-palette` means Catppuccin; no `data-mode` means
  the system's `prefers-color-scheme`; the `#light` and `#dark` page hashes set `data-mode`. The palette ids are a
  closed list that only grows: `catppuccin`, `signoff`, `rose-pine`, `gruvbox`, `tokyo-night`, `dracula`. Each
  palette and mode passes WCAG 2.x contrast for the pairs in `test_theme.py` (text 4.5:1; the focus ring, the
  marks and the orange frame 3:1), its upstream colours moved within the palette where they miss. A new rule
  that puts a token on a new background adds that pair. Every token is used, and only these are used. `prefers-contrast: more` and
  `forced-colors: active` put one high-contrast set over every palette (`!important`; text 7:1). Rule text
  outside the theme part sets no token and no hex colour (the product icon in the header and the sidebar aside). A palette's
  selectors are `[data-palette=…]`, not only on `:root`, so the picker's swatches (with `data-mode` copied from
  `<html>`) show each palette in its own colours. Boards get only `?theme=light|dark`, never a palette.
- **The theme is the inbox's to keep.** `POST /<page>/prefs` on the inbox takes only the keys `theme`,
  `palette`, `density` and `sort`, at least one, `theme` and `palette` together or neither (a picker sends `density`
  only when it was changed there; the list's sort buttons send `{sort}` alone, so the stored theme, palette and
  density stay): `theme` one of `system`, `light`, `dark` (`THEMES`), `palette` one of the palette ids
  (`PALETTES`, kept in step with the page and `theme_tokens.py` by a test), `density` one of `comfortable`,
  `compact` (`DENSITIES`), `sort` one of `oldest`, `newest`, `project` (`SORTS`: the order of Waiting on you),
  else 400. It writes `prefs.json` in the home (`schema, theme, palette, density, sort, at`;
  a key left out keeps the stored value), atomically, 0600; no text of the request goes into it. Only the
  list page's route takes it (404 under `run/<n>/`, on a run, on a board token; 405 on the past-decisions page).
  Every page gets `prefs` (`theme, palette, density, sort, keep`) in its data, read from the home on each load: `keep`
  is true on the inbox's pages only. A run and the past-decisions page read `prefs.json` when it is there and
  write nothing; a missing or broken file, a value outside its list, or a relative home gives `system`,
  `catppuccin`, `comfortable` and `oldest`, value by value. The page sets `data-palette`, `data-mode` and (for compact)
  `data-density` from it before the rest of its script (the `#light`/`#dark` hash still wins, and the picker
  starts from what the page shows). The picker (the brush button, Cmd+, or Ctrl+,) is a modal `dialog` with a radiogroup for the mode, one for the density and a
  listbox for the palettes, each with its credit; the arrows preview on the page, Enter or Keep keeps, Esc,
  Cancel or a click outside goes back, and focus returns to where it was. Without `keep` it previews in that tab
  only and says so.
- **The inbox list is one screen.** A sidebar (views: Waiting on you, To do, Vulnerabilities; then each project
  with a row) filters the list, a second click or Show all shows everything; the list's rows open in the pane
  beside it, each row's view built once and kept until a reload (typed text, statuses); a round's row is a link
  to its page. Under 1000 px the sidebar is a drawer, under 760 px the open row is a bottom sheet; while either
  is open the rest of the page is `inert`. Keys: j and k (the arrows on a row) move the open row, Enter goes into
  it (a round's opens its page), f is Focus (one item at a time), Esc closes the sheet, the drawer or Focus, ?
  lists them; none of them act with Ctrl, Alt or Cmd held, nor while typing (Esc and the search's keys aside, see below). With nothing waiting and
  no filter, the empty state stands in for the list (with a button to the Idle rows when there are any). The rows
  on the page change only on a reload, which the page makes itself: the 5-second `state` poll keeps the tab title's
  count (in all and per project, as `state.json` gives it) and when it differs from the rows the page reloads its
  own URL (never the single-use start link) if that loses nothing: no typed text in any row's view, no sheet,
  drawer, picker, help or Focus, no selected text, the focus not in a field, and not within 15 seconds of this
  page's own load. The open row, the filter, the search and both scrolls come back from `sessionStorage`, once. Otherwise a bar at the top of the list says how many are new, with a Reload button, and
  the key bar says it too; the poll tries again each time. Each Waiting on you row says how long ago it changed
  (`since`: a round's run start, a session's `waiting_at`, a todo item's file time; the exact time in the tooltip),
  and its rows sit under one heading per project (a run outside any project under its folder's name), the
  header's line saying how many wait in how many projects (a run outside any project counts in no project). Two buttons order the rows within each project: oldest
  first (the default), newest first; the choice is `sort` in `prefs.json` (a stored `project`, the old By project,
  reads as oldest first), the order only changes on screen (j and k follow it). To do shows each project's rows
  behind one count row (a button with `aria-expanded`), closed until clicked; a project's filter opens its own.
  j, k and Focus walk only the rows shown, never a closed group's. **The search** is a `role=search` field in the
  header (a phone gets it on a second line), live as you type, over every view and past the sidebar's filter: a
  row matches when each word of the query (split on spaces, folded with NFD, combining marks dropped, lower case)
  is somewhere in its text, which is the project's name, the row's title, kind and short line, plus, per row, a
  round's item titles (`items` on a round's row: at most `MAX_ROW_ITEMS` of `MAX_ROW_TEXT` characters each, titles
  only, never a file or folder name), a todo item's body and its requests' texts, a finding's package, version,
  ids and fixed version, and the project names behind the Not scanned row. The rows not matching are hidden,
  the groups holding a match open (a toggle click does nothing meanwhile), a sidebar pick ends the search, an
  empty field gives the list back as it was. `aria-live` on the count (`N matches`, `No matches`) and a Clear
  button. `/` (not while typing in a field) and Cmd+K (Ctrl+K off a Mac; from a field too) focus it (typing in
  it leaves Focus); Esc in it empties it and goes to
  the open (else first) shown row; Enter or the down arrow in it go to that row, then j, k and Enter walk the
  results. The query goes through a reload with the rest (`query` in the kept state). The groups of Waiting on you follow their first
  row in the chosen order, so the oldest (or newest) row is always at the top. A self-reload keeps which To do
  groups were open. A live session (its `sessions/` file no older than `SESSIONS_MAX_AGE`, not `ended`, its
  `pid` alive) that waits on the user is a row in Waiting on you, ordered like the others (its `since` is its
  `waiting_at`): the project, how long, and for a `bg` session `claude attach <short id>` in a code block with Copy
  (the short id is the session id's first 8 characters, sent only when they are lowercase hex), else "Go to its
  terminal" with no command. A live session that does not wait, with `state` `idle` and `at` `IDLE_AFTER` (60)
  seconds ago or more (a new session with no turn yet too: SessionStart writes `idle`), is a row in Idle, a dimmer
  section under Answered, counted nowhere (not in `state.json`, the
  tab title, the sidebar or the Dock badge). The page data gets the short id, never the session id, a message or
  the prompt.
- **A round's page uses the same shell** (`shell()` in the page: sidebar, drawer, key bar, Focus's bar; one copy
  for both pages). The rounds stay a WAI-ARIA tablist, vertical, in the sidebar (Up and Down as well as Left and
  Right), each with its state as a glyph and a word; the open round's items are listed under them. Each item is
  its question, text and pictures, a margin with the recommendation (the recommended labels, and the option's detail when one is recommended)
  and the cost (between the question and the options under 1100 px), then the options, numbered for the 1 to 9
  keys. The key bar holds the save state, why Done waits, and the open round's Accept this round; the header keeps
  the progress, Focus, Accept all recommendations and Done. The Summary is the sign-off sheet. f is Focus (one
  item at a time; the round's tail after its last item), Esc leaves it or closes the drawer; a page loaded at
  760 px or less opens in Focus. A `#focus=<round>/<item>` hash opens that round's item in Focus; one that names
  no round and item of the page is ignored. The past-decisions page puts its list in the sidebar and has no Focus. The page
  data gets `mono` (`mono`, `color`: the inbox's monogram for the project's name) on a run and the past-decisions
  page, never more. Every label a round's page uses has English and Hungarian (a test reads the page for each `L.name`; a
  key built at run time, such as `L['st_' + s]`, it does not see).
- **Nothing is deleted.** Signoff writes the answers file and its Markdown copy (both atomically) and, only
  when absent, a `.gitignore`. Once an inbox has run, it also writes an empty `<round file>.lock` (such as
  `round-01.json.lock`) beside each open round and `runs/<run>.json` in the Signoff home (see the next rule),
  and its hooks write `projects/<id>.json`, `sessions/<session>.json` and `waiting.flag` there (see the hooks
  rule). With the macOS app installed, a new session's SessionStart keeps `launcher` in the home, a link it replaces by a rename
  of a new one (see the hooks rule). The inbox
  writes, in the home, `inbox.lock` (empty; its time touched at each look), `inbox.json` (`schema, server_id, port, control`; 0600, no token),
  `state.json` (see the state rule), `inbox.sock`, `requests/<project>/<id>.json` (see the request rule),
  `prefs.json` (see the theme rule), and
  `logs/inbox.log` when `signoff inbox open` or the app starts it (the app makes the home and `logs/` for it,
  0700, when absent); `signoff inbox done` writes `<id>.done`. Outside the
  home, on the user's click only, the inbox writes Uptick's `scan-repos.json` (see the OSV rule). The one thing Signoff removes is a
  dead `inbox.sock`: only when it is a socket in the inbox's own home, the starting inbox holds `inbox.lock`, and
  a connect to it is refused. Anything else at that path stops the inbox with an error. A test checks both.
- **One writer per round, once an inbox has run.** The switch is `inbox.lock` in the Signoff home
  (`inbox_home()`; `SIGNOFF_INBOX_HOME` is for tests), which only the inbox creates; a relative home counts as
  off for runs and hooks, and `signoff inbox home` exits 1 for one. Without it a run is the plain
  run, with no new file. With it, a page run holds an exclusive lock on `<round file>.lock` for each open round until
  it ends, and exits 1 when another process holds one, unless a second look finds the inbox alive: it is then a
  waiter (a file system that cannot lock: a stderr line, no lock).
  While the inbox holds `inbox.lock`, a run is a waiter: no port, no browser, no idle timeout, no lock; it looks
  at its answers files once a second and exits as a run does. Every run and waiter writes `runs/<run>.json`
  (`schema, run, project, folder, rounds, mode, pid, started`; never a token or a link) at start and adds
  `ended` when it exits by itself, on Ctrl+C, SIGTERM or SIGHUP; one killed some other way leaves none, so readers check
  `pid` and `started`, and skip `.tmp-*` files.
- **The hooks cost a session nothing.** They run in every Claude Code session of everyone who installs the
  plugin, so `inbox hook` always exits 0 (exit 2 would block a prompt or a stop), prints nothing (SessionStart and
  UserPromptSubmit stdout goes into the model's context) but the hand-over below, and writes nothing while
  `inbox_on()` is false, nor for
  input it cannot read, a session id that is not a plain id, or a `cwd` outside any project. Otherwise it writes,
  atomically, 0600 in 0700 folders, `projects/<id>.json` (`schema, id, root, name`; when absent) and
  `sessions/<session>.json` (`schema, session, project, pid, state, at`; `state` is `idle`, `working` or
  `ended`; `pid` is Claude Code's, from `hook.sh`, or null; and, only when they hold, `bg: true` when Claude
  Code's environment has `CLAUDE_CODE_SESSION_ATTENDED=0`, or a `CLAUDE_JOB_DIR` when
  `CLAUDE_CODE_SESSION_ATTENDED` is unset or empty, and `waiting: "permission"` with
  `waiting_at` while the session waits on the user). Never the prompt, the transcript path, a message, a tool's
  input or output, or a token. A compaction (SessionStart `source: compact`) leaves the state alone. The project id is `project_id()`,
  the same as in `runs/`. Readers skip `.tmp-*` files (a hook stopped at its timeout can leave one).
  A wait: a Notification of type `permission_prompt` (Claude Code sends it 6 s after a permission prompt or an
  AskUserQuestion still open; any other type writes nothing) adds `waiting` and `waiting_at`, keeping `state` and
  `at`. The next UserPromptSubmit, Stop, StopFailure or SessionEnd writes the file without them; PostToolUse and
  PostToolUseFailure write it without them, keeping `state` and `at`, and write nothing when there is no wait (they
  run at every tool call). After a deny nothing comes, so the wait stays until the next prompt. Known limits: no
  hook comes between an approval and the tool's end, so an allowed long tool stays a wait until it finishes; the
  Notification names no tool, so any tool result of the session ends its wait (with parallel subagents, possibly
  another's); a session's hooks do not lock its file, so a result within the Notification hook's own run can
  leave the wait standing until the session's next tool result or Stop, and a Stop within it until the next
  prompt; and a live pid is taken as the session's, so a session killed while it waited, its pid reused, stays a
  counted wait (and keeps the flag on) until its file is `SESSIONS_MAX_AGE` old. `waiting.flag` in
  the home (0600) holds one byte while some session may wait (a recent file with `waiting`, not `ended`, its pid
  alive or null) and is empty otherwise; it is replaced atomically, never removed. A wait writes it before and
  after its session file; a hook that ends one empties it only when no session may wait, then looks again and
  writes the byte back if one began meanwhile, so it is never empty under a wait (a hook killed between the two
  writes aside). A stale byte costs a Python start at a tool call, nothing more. With the inbox on and the flag
  empty, `hook.sh clear` costs a session about what `hook.sh` costs with the inbox off (an `sh` start and a stat).
  The live gate: for the default homes, `hook.sh` runs `inbox hook` at SessionStart (every source; `hook.sh start`
  or `hook.sh session`) while `inbox.lock` is there, and at the other events only while the inbox is live:
  `inbox.lock` touched within a minute (`find -mmin -1`; the inbox touches it every `STATE_EVERY` seconds, an inbox
  from 0.3.0 never; a `find` that fails leaves the lock alone to decide), tested after `waiting.flag` for `clear`.
  Known limit: while the inbox is not live, those events write nothing, so a session file may be stale when an
  inbox starts (a wait that began or ended meanwhile, and `waiting.flag` with it) until the session's next event;
  a session that ended meanwhile is never marked `ended`, so only its pid tells, and a reused pid keeps it a live
  session (a counted wait, if it waited, and no hand-over to new sessions) until its file is `SESSIONS_MAX_AGE`
  old. `inbox hook` itself has no such gate: with `SIGNOFF_INBOX_HOME`, a relative `HOME` or no `uname`, Python
  has started already.
  The hand-over: at SessionStart (not a compaction), with the inbox on and no other live session of the project (a
  `sessions/` file no older than `SESSIONS_MAX_AGE`, not `ended`, its `pid` alive and not this Claude Code's), the
  hook prints the project's pending requests (no `<id>.done`), oldest first: one head line (`HANDOVER_HEAD`: tell
  the user first, act once the user says so; a model takes hook output for someone else's words), at most
  `MAX_HANDOVER` request lines and `MAX_HANDOVER_TEXT` characters of them, then one line with how many more wait.
  Nothing else: no path, no token, no other
  project's request.
  The launcher link: one write in the home that does not wait for the inbox. At a new session's SessionStart
  (source `startup`, `hook.sh start`; a resumed, cleared or compacted session may run an older plugin), on macOS (`uname -s` is `Darwin`) with `/Applications/Signoff.app` or `~/Applications/Signoff.app` a folder,
  `hook.sh` makes `<home>/launcher` a link to the plugin's own `bin/signoff` (an absolute path, from its own
  folder), in `sh`, before the inbox-off exit, starting no Python. Never when a `.git` (a folder or
  a file) sits beside the plugin folder: a plugin run from a git checkout (`--plugin-dir`, or a directory
  marketplace, which runs in place) keeps no link, and its app is built with the fixed launcher. Only that `.git`
  is looked at: a copy outside git, or a repo rooted at the plugin folder, still writes the link. The home is `SIGNOFF_INBOX_HOME` when absolute
  (a relative one: nothing), else the macOS default; it is made 0700 when absent, and nothing is written when it
  is a link or not the user's own folder. It writes only when the link is missing or points elsewhere: a new link
  `.tmp-launcher-<pid>` beside it, renamed over it. Anything there but a link to something not a folder is left
  alone. It prints nothing and fails silently. Without the app it costs two `test`s; other events never do it. A
  home made for it does not turn the inbox on (`inbox.lock` does).
- **The Markdown copy escapes everything.** Agent and user text go through `md_text`; nothing in it may render
  as Markdown or HTML.
- **The app is a client of the inbox and nothing more.** It binds no Go method to the webview: no Wails service,
  raw message handler or transport of its own (`nobind_test.go` parses the source). What Wails keeps is its
  `external` message handler, which takes only drag, resize and ready messages, and from the main frame only; its
  `wails://` scheme, behind which sits Wails' runtime (browser, clipboard, dialogs) with no origin check, is shut
  (`nav_darwin.m`), and the inbox's CSP (`connect-src 'self'`, boards `default-src 'none'`) keeps pages from it
  as well. The app runs the plugin's command, never a copy of `signoff.py`: the one fixed at build time, else the
  target of the home's `launcher` link, taken only as an absolute link to a regular, executable file owned by the
  user or root, in the user's own home; that command's `signoff inbox home` must then name the same home (it has
  run by then: this check keeps one home). The file checks look at the file that is executed, not at the
  `signoff.py` beside it or the `python3` on PATH it runs (the user's own files). It trusts the home only as a folder of
  the user's own, not a link, closed to others; `inbox.json` only with `schema` 1; and a start link only as
  `http://127.0.0.1:<that port>/b/<token>/`. The window and the menu bar panel each get start links of their own
  (never one another's), and every one of its webviews, the panel's included, has the navigation policy and the
  shut scheme: Wails makes each with the same delegate class. The link goes to its webview and nowhere else: no log (Wails' logger
  is discarded), file, stdout, window title or process argument, and no error message holds it. The webview stays
  on the inbox's origin. Only the inbox page itself (not a board) opens anything outside: an http(s) link for a
  new window, clicked, goes to the default browser; a `127.0.0.1` URL never does (it may hold a page token), and
  anything else loads nothing. A link of the inbox's own for a new window opens in a window of the app. The badge
  and the count beside the menu bar item (the number and nothing else) come from `state.json`'s count only, read
  only while an inbox holds `inbox.lock` (looked at as `inbox_alive()` does, so a starting inbox is never kept
  out). A rise of that count triggers a notification, and a saved Accept & send asks for the next offer. Its text is
  the number, or the control socket's offer: project, round, question and recommended answer, one line each, and
  nothing else (after an accept, only an offer; without one, nothing). Those texts are the agent's own as the inbox gives them (it replaces the project's folder and
  the user's home, nothing more: a URL in them stays); the app adds no link, token, offer id or path of Signoff's
  in the title, body, identifier (`offer-<k>`, its own counter) or user info (no `Data`). The offer id stays in the
  app's memory, one at a time (a new offer's banner replaces the one before), for the inbox's `OFFER_TTL` at most,
  and is used once. A response to any other identifier, or after a restart, only opens the window. Accept & send
  is the control socket's `accept`; Other answer…, a tap on the banner or a refused accept loads the start link of
  `open` with the offer into the main window (the item in Focus; the list when the inbox no longer has the offer).
  When the app comes up (its start, and a reopen: a Dock click or `open -a` while it runs), and only then, the
  main window first asks a live inbox for `app_open`; a link (checked as any start link) loads into it, before
  the shown window's no-reload path, and none, an error reply (an inbox from before the op) or a failed ask leaves
  today's behaviour. The menu bar panel, its menu and a banner never take the target.
  The app never sends a choice. Quitting the app leaves the inbox running.
- **stdout is answers paths and nothing else**, one per line, in tab order. Agents parse it. Messages go to
  stderr. The exceptions: `signoff inbox home` prints the Signoff home (`inbox_home()`, never creating it),
  `signoff inbox hook` prints nothing but the SessionStart hand-over, `signoff inbox monitor` prints request
  lines only, and `signoff inbox rows <folder>` prints one JSON line, the `rows` reply (for a folder outside any
  project `{"rounds": [], "more": 0}`), for the mod. `signoff inbox serve|open|stop|done` print nothing on stdout
  (`open --app` no link on stderr either); `open --no-open` prints its start
  link on stderr, as a run does, and so does `open` when no browser opened (`webbrowser.open` False; a run then
  says `no browser opened` under its link). A browser a run or `open` starts gets `/dev/null` for each of
  stdout and stderr that is not a terminal (`_open_browser`), so it writes nothing there and holds no caller's
  pipe open (on Linux the stdlib starts `xdg-open` or `gio` with them).
- **Exit codes are a contract:** 0 Done, 1 invalid input (a usage error, every round already done, a round
  locked by another run, the start link used twice, or a Python older than 3.9: one stderr line, from the first
  lines of `main()`; `inbox hook` then exits 0 silently and `inbox monitor` sleeps while its parent lives; the
  file must keep compiling on 3.7 for that, which a test checks), 2 idle timeout (never for a waiter), 130 Ctrl+C,
  SIGTERM or SIGHUP (a run and a waiter, on macOS and Linux: they stop as on Ctrl+C; a signal ignored at start
  stays ignored; on Windows a stopped process gets no signal it can catch; `--history` and the `inbox` commands
  keep the default for both). `signoff inbox home` exits 0, or 1 for a relative home; `signoff inbox hook` always
  exits 0. `signoff inbox
  serve` exits 0 when stopped, 1 when it cannot start (another inbox, no Unix sockets), 130 on Ctrl+C; `open`
  and `stop` exit 0, or 1 when no inbox answers; `open` with a target also exits 1 for a malformed target, and
  `open --app` exits 1 for one, off macOS, for a relative home, when no app is found where the mod looks, or
  when `open -a` fails, else 0. `signoff inbox done <id>` exits 0 (also when marked before), or
  1 when there is no such request. `signoff inbox rows <folder>` exits 0 with its line (outside any project
  without asking an inbox, which need not run), or 1 with nothing on
  stdout for a relative folder or home, when no inbox answers, or when the inbox answers without `rounds` (one
  from before the op). `signoff inbox monitor` ends (0) only once its session is gone.
- **Done waits for acceptance.** With several rounds open, a Done save is refused (409) until every other open
  round is accepted; Done accepts its own round, so a single round stays one click.
- **No stale writes.** Every save from the page carries `rev`, the round's save count it last saw; an older one
  gets 409 and the page asks for a reload. The server also refuses (409) to write over an answers file that changed
  on disk since it last read or wrote it (the hash of the bytes it wrote, never a read-back), for example from a
  second run on the same folder; loading the page, and the run's own look about once a second, take such a file in,
  so a reload lets saving go on, and a file that says done closes the round. A file that fails `check_answers` (or
  says done for another version of the round) is not taken in: one message on stderr, and saves stay refused.
  Within a run this is exact; across runs the round lock keeps a second writer out once an inbox has run, and
  before that it is best effort. A round closed by Done shows read
  only on reload, even while the run goes on.
- **Done is final.** Once a round's save with `done: true` is stored, later saves to it get 409, and a later
  run shows it read only. The run ends when every round that was open is done, by Done on the page or by an
  answers file another program wrote with `done: true`.
- **The file formats only grow.** Add optional keys; never rename or remove one. Update `schema.json`, the
  table in `docs/decisions-file.md` and the skill in the same change.

## Style

Plain, short sentences in docs and comments. A comment says why, not what. No new dependencies.
