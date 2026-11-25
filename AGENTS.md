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
  without Git Bash, Claude Code runs a hook's command in PowerShell: each command opens with
  ``echo `# >$null <#` >/dev/null;`` and closes the sh part with `#>`, so PowerShell echoes into `$null` and skips
  the rest as a block comment; `test_hooks.py` checks the commands token by token (no
  PowerShell runs in CI). On Windows `hook.sh` exits before Python: the inbox does not run
  there. On macOS and Linux, without `SIGNOFF_INBOX_HOME`, `hook.sh` exits before Python when the default home
  has no live inbox (see the hooks rule), SessionStart only when it has no `inbox.lock`; it mirrors only those
  defaults of `inbox_home()`, and a test keeps the two in step.
  PostToolUse and PostToolUseFailure run `hook.sh clear`: there it also exits before Python when that home's
  `waiting.flag` is empty or absent, tested first. SessionStart has two groups: `startup` runs `hook.sh start` (on macOS with
  `/Applications/Signoff.app` or `~/Applications/Signoff.app` there, it first keeps the home's `launcher` link, see
  the hooks rule, in `sh`); `resume|clear|compact` runs `hook.sh session`. The plugin declares no monitor (Claude
  Code's `/restart` refuses while one runs).
- `plugin/hooks/register.tsx`: the plugin's mod, the hooks module `hooks.json` names under `modules` (its test runs in
  CI on Claude Code's stable and latest channels). The band (`Signoff: N waiting`, Open, Hide),
  the toast, the status line entry, the hand-over of the inbox's requests (`$.prompt.submit`), the plan review
  (the `tool.call` hook on `ExitPlanMode`, see the mod rule), the panel (the
  `signoff` pane) and the answer to `/signoff:open`
  (`plugin/commands/open.md`: a mod cannot
  register a name with a colon, so the file declares it and the mod's `command.run` hook answers it; its body is
  the prompt the model gets when no mod runs). Its session values are declared in `plugin/types/index.d.ts`
  (`types` in `plugin.json`); the `hint` option in `plugin.json` lists `band`, `status` and `off`, `plan`
  lists `inbox` and `off`, and `chat` lists `0` to `4`, under `options`, the values the mod and the skill accept (a test checks).
  `plugin/hooks/register.test.tsx` is its test, run by `claude plugin test plugin`.
- `plugin/commands/demo.md`: `/signoff:demo`, typed only (`disable-model-invocation: true`); its body asks the model
  to write one synthetic round in a `mktemp -d` folder, run Signoff on it, and give the user the `rm -r` command
  without running it.
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
  (`inbox_request`, which also takes an op's other fields; `signoff inbox rows`), and the request files (`read_request`, `request_line`; `signoff inbox requests|done`). Also
  `signoff plan` (the plan-review rule) and its fixed round, `plan_round`. The
  inbox needs Unix sockets, so it does not run on Windows yet.
- `plugin/signoff/page.html`: the page's template: its markup, the theme part, and a line `__PART:<name>__` for
  each part, inside the template's own style and script elements. The parts are `page.css`, then the script in
  `page-common.js` (labels, helpers, Appearance, the shell), `page-inbox.js` (the inbox list), `page-items.js` (a
  round's page and the pieces of an item) and `page-rounds.js` (an item, the rounds, Focus, Done, past decisions,
  keys). They are cut where braces balance but do not parse alone: together they are one script. The server
  puts the page together once at start (`page_source()`; the list and its order fixed in `PAGE_PARTS`), each
  placeholder line replaced by its file's text. The template must hold exactly those lines, once each and in that
  order, each a line of its own, and no part a placeholder, `__NONCE__` or `__DATA__`: else, or with a part missing
  or unreadable, the server stops at start (`cannot start`, exit 1). Tests read the page through `page_source()`
  too; a test keeps each of the page's files under 256 KiB, as the plugin catalog holds a larger file for a
  reviewer. No build step, no external requests. The server fills in `__NONCE__` and `__DATA__` in the template on
  each request. The page's colours sit in `page.html`, between the `<!-- theme: -->` and `<!-- /theme -->` markers, the
  output of `tests/theme_tokens.py`, pasted in by hand.
- `THIRD_PARTY_NOTICES` and `plugin/THIRD_PARTY_NOTICES`: the same text, the credits and MIT text for the palettes'
  colours. The plugin copy is there because an install gets only `plugin/`; a test keeps the two equal.
- `plugin/signoff/schema.json`: the decisions file's JSON Schema. A test keeps it in step with `signoff.py`.
- `tests/theme_tokens.py`: the palettes' upstream colours mapped onto the page's tokens, with the contrast
  fixes; `python3 -I -B tests/theme_tokens.py` prints the page's theme part. Not needed at run time.
  `test_theme.py` fails while the page and its output differ, and measures its list of pairs on the page itself.
- `tests/`: unittest suites. `support.py` makes a temporary topic folder (one file, or `round-NN.json`
  rounds) and runs a server on a thread. It points `SIGNOFF_INBOX_HOME` at a missing folder for every test and
  child process, so a run is the plain run unless a test passes its own home. `line_rule.json` pins how a
  line note counts an attachment's lines (`attachment_lines`), read by the server's tests; the page's count must match it. `test_contract_http.py` checks
  the security and run contract against the command line as a separate process, over HTTP, its exit code and
  stdout only; `SIGNOFF_CONTRACT_CMD` (POSIX shell words: forward slashes in a Windows path) swaps in another
  implementation. It must take the same arguments (`--no-open`, `--timeout`, `--history`, `--port`,
  `elsewhere <round file> <item id> [<note>]`, and `plan --topic <t> --folder <f>` with the plan on stdin), print its
  `http://` start link on stderr, and honour `SIGNOFF_INBOX_HOME` and `SIGNOFF_PORT`. `support.py` also drops `SIGNOFF_PORT`, `SSH_CONNECTION` and `SSH_TTY`
  from the environment, so every run gets a random port and may open a browser unless a test says otherwise. Its inbox must touch `inbox.lock` at each look, as the mod reads that time. It must be the
  server process itself (a
  launcher that execs, as `plugin/bin/signoff` does): the suite stops it with kill, SIGINT, SIGTERM and SIGHUP. Its `Inbox` class
  starts `<command> inbox serve` and takes start links from the control socket; its `InboxOffer` class starts
  another for a `runs/` entry it writes by hand (a waiter with the suite's own pid) and asks it for `offer`,
  `open` with the offer and `accept`; its `InboxRows` class starts a third for a project's `runs/` entry and asks
  it for `rows`, on the socket and through `<command> inbox rows <folder>`, and for `open` with a row's target,
  on the socket and through `<command> inbox open --run <n> --focus <round>/<item> --inbox <k>`, and for
  `app_target` and `app_open` on the socket only (`--app` runs `open -a`, and an app started that way gets none
  of the suite's environment); its `InboxTodoPath` class starts a fourth for projects whose `todo` key names a
  file inside the root, or one out of it (`..`, an absolute path, a link), too big or not a file, and checks the
  list page and messages; its `InboxRequests` class writes request files into the home by hand and runs `<command>
  inbox requests <folder> <project> <id>…` with no inbox (its JSON line, the ids gone, its refusals, a folder in
  another project, a nested one too); its `InboxWaiter` class runs `<command> <folder>` as a waiter, once beside a stand-in
  inbox that holds `inbox.lock`, touches it and locks no round (exit 1, nothing on stdout, no lock file made), once
  beside one whose look locks the round 1.5 s after its touch (still waiting), and once beside `<command> inbox
  serve` (still waiting after several looks, the round's lock held); its `Elsewhere` class runs `<command>
  elsewhere` beside `<command> inbox serve` and a waiter (the `elsewhere` op and its refusals, on the socket and
  through the command) and races a page Done against a mark, through the inbox and beside a page run that holds
  the round lock: a run never exits 0 over an answers file that says `done: false`, and the loser is refused;
  `Exits` marks an item beside a plain run; its `Plan` class runs `<command> plan` with no live inbox (exit 1,
  nothing written) and beside `<command> inbox serve`, where an Approve saved with Done through the inbox's page
  ends it with exit 0 and the one answers path, and where an approval left at the next number (no round beside it)
  never ends it. `test_cli.py`'s `CliPlan` class covers `signoff plan`'s refusals, its files and their bytes, the
  rounds it closes and the runs that hold them, files left at the next number, `--resume` (only the fixed round,
  answers a run takes, no link), links at the topic folder, a create that meets a file, Ctrl+C, a forced waiter
  with no inbox, and an older inbox (a stand-in). Its `WaiterVerdict`
  class steps the check poll by poll (rounds answered in the poll of the verdict, an inbox gone or started again
  between the looks); its `CliElsewhere` class covers `signoff elsewhere` in each of its writer's cases, the user's
  answer it never replaces, the note's checks and the hash before the rename. `test_inbox.py` covers the
  inbox in process: the `todo.md` reader and the `todo` key, the Markdown tree, routes, pairs, locks, the waiter it wakes, messages,
  the theme it keeps, the answer from a notification, the panel's rows and their targets, the
  `elsewhere` op (its refusals, any other error, offer and rows with a mark, a round lock the command held), and
  `inbox open --app` with its app opener (`APP_OPEN`) swapped for a stand-in and its app folders (`APP_DIRS`)
  for temporary ones: no test runs the real `open` or finds an installed app.
  `test_hooks.py` covers the hooks, the SessionStart hand-over, `inbox requests`,
  `inbox done` and `hook.sh`. It also runs the mod's case table of project roots (the strict JSON between the two
  `project_root cases` lines of `register.test.tsx`, which the kit runs against the mod's `projectRoot`) against
  `project_root()`, so the copy and the original stay in step: a change to one needs the same change to the
  other and to the table. The top folder's row pins only Python (in the kit's file table the mod would ask for
  `//.git` there).
- `examples/demo/`: synthetic decisions with mockups: `decisions.json` as one round, and the same questions as
  `round-01.json` to `round-04.json`, the source of the screenshots in the README and `docs/`; `boards/` holds a board.
- `examples/history/`: a synthetic project for `--history`, its answers and outcome files committed.
- `scripts/release.py`: the release step, run on the pushed release branch. It pushes main and the signed tag
  `v<version>` together only when the newest `test` run of that branch on that commit succeeded (read with `gh`),
  then makes the GitHub Release; `tests/test_release.py` covers it with a local origin and a stand-in `gh`.
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

- **Security rules are not optional.** Bind `127.0.0.1` only, on port 0 unless the user asks for one: `--port N`
  on a run or `--history`, else `SIGNOFF_PORT` (also for `inbox serve`), N plain ASCII digits from 1024 to 65535
  (below, a browser leaves `:80` out of Host and Origin); `0`, or an empty `SIGNOFF_PORT`, a random port
  (`--port ""` is a bad value); it is read only by what binds (a run, `--history`, `inbox serve`, and `inbox open`
  before it starts one), never by the hooks or `inbox requests`. Never a fixed port by default, never another address.
  Before a fixed port's bind, `port_in_use()` tries a connection to `127.0.0.1:<port>` (it catches a same-user
  `0.0.0.0` listener that macOS's `SO_REUSEADDR` would bind beside); best effort, a listener that starts between
  the look and the bind is not seen. `inbox serve` looks before it makes `inbox.lock`. On Windows without
  `SO_REUSEADDR`, which there lets a second socket bind a port in use; `SO_REUSEPORT` nowhere (a test pins both).
  Every link Signoff prints or hands out names `127.0.0.1`. A fresh token in the path on every
  request; exactly one `Host` header, equal to `127.0.0.1:<port>` or `localhost:<port>` (no other name, case,
  port or form: not `LOCALHOST`, `localhost.`, `127.1`, `0.0.0.0`, `localhost.example`, `[::1]`, a missing or
  empty Host, nor two Host headers in either order); a save (`POST /<token>/save/<round>`) needs
  `application/json`, the page's own `Origin` (`http://` and the request's own Host, so a page loaded by either
  name saves and an Origin of the other name is refused), and `Sec-Fetch-Site: same-origin` when present; a body within its round's limit,
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
  `{"op": "app_open"}`, or `{"op": "elsewhere", "round": <path>, "item": <id>, "note": <text>}` (see the
  elsewhere rule); at
  most 4 KiB), one out. Each `open`
  makes a pair: a start link that works once within `START_TTL` seconds, and the page token it
  opens, closed after `PAGE_IDLE` seconds without a request. A second use, or a late first one, gets 410 (404 once
  the inbox has dropped the closed pair) and closes that pair only (its page token opens nothing more); the inbox
  and other pairs go on. Each connection gets its own thread and 5 seconds in all. The home must be a folder of
  the user's own, not a link. No route, log line or file ever holds a link or a token. Its routes,
  all under a live page token with the same Host rule: `GET /<page>/` (the list), `GET /<page>/state` (the
  count as in `state.json`, JSON), `GET /<page>/run/<n>/` (a
  run's rounds on today's page), `GET /<page>/run/<n>/img/<k>`, `POST /<page>/run/<n>/save/<round>` (today's
  save rules), `POST /<page>/request` (a message on a todo item or Later on one, today's save rules with
  `MAX_REQUEST_BODY`; see the request rule), `POST /<page>/prefs` (the theme kept, the same rules with `MAX_PREFS_BODY`; see the theme rule), and each
  run's boards under that run's own board token. The list page asks for `state` every
  5 seconds, so an open tab keeps its page token alive; a closed one expires after `PAGE_IDLE`.
- **An answer from a notification is its round's Done, asked for on the control socket.** `offer` names at most
  one item, in the first round of Waiting on you that: belongs to a run whose round locks the inbox holds; is that
  run's last open round; has exactly one item not yet decided (decided, `_decided`: a saved `choice` that is not
  null or an empty list, with a `source` other than `recommended`; an empty list with `source` `user` on an item
  with `recommended`, the recommendation the user unticked; a `source` of `elsewhere`; or an item with `settled`
  whose saved choice (or, unsaved, its recommendation) equals `recommended`, a `many` list in any order), no item marked
  `elsewhere` and no item with `settled` without a saved answer (a tap's Done would send what the user never saw:
  a page save sends every item, so the round is offered once the user saved on its page); and that item has
  `recommended` (not an empty list) and is neither `sensitive` nor `irreversible` (the banner cannot say it cannot
  be undone; `stakes` does not matter). The reply is `{offer: null}` or `{offer: {id, project, round, question, answer}}`: `id` 32
  hex digits; `project` the project's name as the list shows it (a run outside any project: its decisions
  folder's name, the login name when that folder is the home itself), the other three the decisions file's own
  texts (a number item's answer: its `recommended` as its grid point, a whole value without a point or an
  exponent, any other as Python's shortest repr, then a space and the unit when it has one; with the range's
  bounds of 1e15 and a 16-character unit it never reaches the cut). Each is one line (every control, format and line-separator character a space), cut to
  `MAX_OFFER_TEXT`. Signoff adds no path, link or token of its own, and replaces the project's folder with its
  name and the user's home with `~`, but does not filter the agent's text: a URL in it
  stays, and so does the folder written through a link (the inbox knows only its real path). The inbox keeps an offer in memory only (its run, round,
  item and the round's `rev`), at most `MAX_OFFERS`, for `OFFER_TTL` seconds; no log line or file holds its id.
  `accept` uses it once: a second use, a late one or an unknown id gets `gone`. It saves through the run's own
  writer the saved answers, with the item's `recommended` as its choice and `source: "notification"` (its
  comment, marks, line notes and privacy kept), `accepted` and `done` true, at the offer's `rev`. Every save rule of the page
  holds (`rev`, the file on disk, Done is final, the Done gate, the round lock), and each refusal is one word
  (`changed`, `held`, `done`, `failed`) with nothing written (a `failed` write may leave a new Markdown copy, and
  on an orphan round an answer request, as a failed save from the page does: the JSON goes last). `accept` and `open` with an offer look at the runs first, so a round
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
  a round without `item`, or rounds without `inbox`, is another shape to the mod.
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
  that refuses the target (one stderr line), it opens the app all the same, which shows what it showed. It never starts an inbox.
  `signoff inbox open --app` with no target (macOS only; not with `--no-open`) finds the app the same way, asks a
  live inbox for `app_open` and drops the link (so a target an earlier press kept is not used; no inbox answering
  is ignored), keeps nothing, and runs the same `open -a`; the app then shows what it showed.
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
  installs the plugin, so it starts no process to look (the one it starts for a request it found new is the
  mod's hand-over rule's, and `signoff plan` is the plan review's, below): every `EVERY` (5) seconds it checks `inbox.lock` in the
  homes `hook.sh` mirrors (or `SIGNOFF_INBOX_HOME`; no test keeps this copy in step, and a wrong one only keeps
  the band empty) through `$.fs`, and only once one is there it runs
  `${$.plugin.root}/bin/signoff inbox home`, once per load of the mod (again only when an interrupt killed it; its
  module state: a reload asks again).
  Then it reads, through `$.fs` only, `inbox.lock`'s
  time, `projects/*.json` (again only when the folder's time or `$.session.root()` changed; the session's project
  is the longest `root` holding the session's root: where it started, or where `/cd` or a worktree move took it,
  never a Bash `cd`, as the monitor kept its project) and `state.json` (again only when its time or size changed; `schema` 1, a whole
  `waiting` for its project id, files over 64 KiB skipped). Any failure shows nothing. It shows that number,
  fixed text and the panel's titles, nothing else; it writes no file; to the model it sends only the request lines
  (the mod's hand-over rule) and the plan review's fixed denies and messages, each message with the answers path
  at most. It starts looking once per load, from `session.start` or the band's
  first drawing (a reload after an options change may fire no `session.start`; the desktop app and the VS Code
  extension send it with `isInteractive` false, tried on 2026-11-15, so there the band starts it). With `claude -p`
  (the mod loads there, but `session.start` says `isInteractive` false and no band is drawn) and on Windows (`OS`
  is `Windows_NT`) it looks at nothing but for the plan review's state (a native success reads it, a `/clear` or a
  `/resume` writes it: see below); with `hint` `off` it shows nothing and looks only for requests (the mod's
  hand-over rule), but for the plan-review check at an `ExitPlanMode` call (not with `claude -p` or on
  Windows) and that review's toast. Only a live count (an inbox that lives,
  a project found) moves Hide (cleared by any other live count) and the toast (once a session, after a live 0 and
  two live counts above it in a row). Open and `/signoff:open` (typed at the prompt, also with `hint` `off`) open
  the panel: `$.ui.open` of the `signoff` pane before anything else is awaited (so it is the person's own press
  or command, placed at any width), with `focus` only on a press of Open (after the command the person may type
  on, and a digit would press a row), then one run of `signoff inbox rows <session root>`, once per opening and
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
  which runs `signoff inbox open --app` with the row's target, and so does All projects (or Open the inbox),
  whose App button runs it with no target. All of these run one at a time (a
  press while one runs does nothing), and read only its exit code, never its stdout or
  stderr (a start link may be there), and an error only by its name; the command's line goes to a toast, not the
  conversation (the command's output row would go into the model's requests). For origin `sdk`, as with
  `claude -p`, the command opens the inbox and its line is the output; on Windows it says the inbox does not run
  there. A test gives `inbox open` and `inbox rows` a link on stderr and terminal escapes in the titles, and
  checks every text the mod shows.

  Plan review (AD-29): its tool hooks are `tool.call` on `ExitPlanMode`, whose `.catch` is `next(e)` and, after a
  main-loop success, the end of the plan state, not awaited (past the hook's 10 seconds it is the only one that
  sees that success), and `classic.PermissionRequest`, which answers that call's permission request for an
  approved plan (below; its `.catch` is `next(e)`). Its allow carries `updatedInput` and no mode. It takes a
  call over only when the `plan` option is not `off`, it has armed, not on Windows, the inbox is live and the
  session's project found (checked at the call, also with `hint` `off`; a call during the load's first
  `inbox home` run waits for it; a run that this call's own interrupt killed is asked again at the next need, any
  other failure is asked once per load), the session id's first 8 characters are letters, digits or hyphens, the
  session root and the `folder` setting hold no control, format or separator character (the answers path would be
  refused there), the call is not interrupted before the spawn, and the call is in the main loop and carries a plan (`e.plan`, not empty once
  `trimEnd()` cut it) of at most `MAX_ATTACHMENT` in UTF-8; else `next(e)`, also with a review open. It then denies
  with fixed text (no path), keeps `{topic, text, phase, wake}` in `$.state` (`plan`), in review also `since` (the
  clock at the take-over, before the child), `after` (the highest round or plan number in the topic folder then, as
  `signoff plan` numbers them, the folder found as it finds a relative one, by a copy of `project_root()` that one
  case table keeps in step (see `test_hooks.py` above); absent when it cannot be known) and
  `root` (that session root), all as they were through every respawn, an approval keeping `root`, the topic `<local date>-plan-<those 8>` from the session's first take-over, and starts `signoff plan`
  in that root, as every respawn does, with the plan on stdin and never in argv. The decisions, a child's end and a respawn run one at a time. While the
  child runs, any call gets the deny and that a review is already open, with no new round; with a review and no
  child in this load it starts `signoff plan --resume` with the kept text, as it does at a load while that review
  waits and an inbox lives (with `plan` `off` at that load the review ends instead, and nothing is sent). A respawn
  and a pass need a topic that ends with this session id's first 8 characters. `--resume` takes the newest round,
  an older review's when this one's round was not written yet: so an old answer never answers a later review. A
  resumed child's answer from a round not above `after` (no clock decides that), or with no `after`, is no answer,
  nor is its answers file when older than `since`, or when its `saved` (the inbox's clock, to the second, so with
  1 second of tolerance) is older or no time (a `saved` that is not text is none), or any with no `since`: no
  answer, no failure and no message, and
  the review starts once more without `--resume`, with the held text and topic, in its root. A child without
  `--resume` always writes a new round and is never checked, so a slow file clock refuses no normal answer.
  On exit 0 its stdout must be one line, an absolute path ending `/<topic>/round-NN.answers.json` with no control,
  format or separator character, at most 4096 characters; stderr is never kept. Of that file (at most 4 MiB, what
  `$.fs.read` takes) it uses only `done`, `saved` (a resumed child's, as above) and the `plan` item's `choice` and
  `source`: `done`, `approve` and `user` are an approval when that round's `plan-NN.md` holds exactly the kept
  text, what the user saw (else the unread message), and anything else is none (revise, another source, a file
  too large, not done or unreadable), which is no failure either. An `answer` request never moves the phase.
  It sends `$.prompt.submit` (never `asUser`) with fixed text that names Signoff as its sender and the user in the
  third person, and that path only, as a JSON string: never the user's text or the plan's. That wake is in
  `$.state` before its first submit and until it entered. One submit of this mod is out at a time, as prompts of
  one plugin queued behind one turn become one turn and only the last submit's promise settles, the others never
  (measured on 2.1.323 and 2.1.330; another plugin's and the user's own do not merge with them, nor do the
  notifications of a background command and of a subagent, measured headless; peers and schedules were not
  measured): the hand-over waits, before it clears the turn's note or
  marks, while a wake is out, a wake waits while a hand-over runs or another wake is out, and the one that settles
  starts the other, with its checks again. So in a live load the promise is the only release: with one out, nothing
  that is measured to merge can join it. A wake's `isOut` is written before each submit and cleared when that was
  refused. A load that finds it set (the engine may still hold that prompt) sends no copy at its first main turn,
  and a main turn whose `text` holds that wake's text means it entered.
  Refused, it stays: one fixed toast with no path (a second text for did-not-run, which saved no answer), and one
  submit at each next main turn, which never waits for it; no timer, no other channel. With none kept, a turn
  costs the plan review no `$` call. While a wake is kept, the answered plan's text opens no second review (a
  fixed deny after revise and after any other answer that is no approval); another text is a new review and drops
  the wake, except such an answer's wake that still waits its turn behind a hand-over or another wake (this load's
  `isWakeDue`): another text then gets that fixed deny too, and the wake follows. A load takes no wake kept with
  another session's topic. An approval lets that exact text through once, in its root only (after a `/cd` into
  another project the same text is a new review there; Claude Code's own dialog where the inbox knows no project): the state is made idle and the wake dropped before
  `await next(e)`, so two calls or a reload during the dialog get no second pass, and a rejected pass ends and
  stops nothing else (a review begun meanwhile stays). It passes only when that write took the approval and no
  interrupt came after it; else the call takes Claude Code's own path. While the passing call's `next(e)` runs,
  its window is open, in the module's memory only (after a reload the dialog asks). The binding is by time, not by
  id: the permission request carries no `tool_use_id` (measured on 2.1.323 and 2.1.330), and each call sends one,
  inside its own window. The window's first `classic.PermissionRequest` for `ExitPlanMode` with no `agent_id`
  takes the window, matching or not. It gets the `allow` only with the keys `plan` and `planFilePath` and no
  other, the plan equal to the approved text after the same trim and `planFilePath` as the passing call carried
  it, and only when the hooks beneath it (mods below, the user's settings hooks) give no `decision` and no
  `block` of their own, which go back unchanged; anything else they return (a `preventContinuation`) stays beside
  the allow (Claude Code did not stop on a `continue: false` there, measured headless on 2.1.323 and 2.1.330). Any other request, a later one and one with no window open get `next(e)`, Claude
  Code's own dialog. Two same-text calls whose windows overlap cannot be told apart (measured once in three runs
  on 2.1.323), and either gets the same approved plan once. A call past its hook budget (the `.catch`) closes an
  open window, and a decision under way meanwhile opens none. A kept approval ends at a rejection in Claude Code's
  own dialog (not at an Esc there: that call is interrupted, and an interrupted call ends nothing), at a `classic.UserPromptSubmit` whose `permission_mode` is not `plan` (the user left plan mode by
  hand; until a load has seen an approval, a prompt costs no `$` call), and at a load with `plan` `off`.
  Any native success of a main-loop `ExitPlanMode` (no `deny`, not `isError`) is the user's later word and ends
  the whole plan state: the child stopped, nothing sent, nothing written back by a submit that settles later. A
  rejection changes nothing else, and a subagent's call nothing. A `/clear` and a `/resume` (`session.end`) stop the child, clear
  the state and forget the topic. Both hold armed or not, so also with `claude -p` and on Windows such a success
  reads `$.state` once and such an end writes it once. A child the mod stopped is no failure. An unload is none: the first `$` call
  at a child's end is the state's read, which an unloaded module's `$` refuses (measured on 2.1.323 and 2.1.330),
  so it writes and sends nothing.
  Any other end (a non-zero exit, a signal, a failed start, another stdout) sends the fixed did-not-run message,
  ends the review, and takes no plan over for the rest of the load or until `inbox.json`'s `server_id` changes.
  It writes no file and shows no link. Known limits: a submit the engine already holds cannot be taken back (so
  a message may follow a plan that went through, a `/clear` or a new review); the hook's 10 seconds include its
  wait for another call's decision, and past them the call takes Claude Code's own path; an interrupt after the
  approval was used up loses it, as it is never restored (one seen before that write uses nothing up); a second
  call at the same time as a pass opens a round, which that pass's success leaves in the inbox; Claude Code's exit
  ends the child before `session.end`, so there it counts as did-not-run, whose message and toast reach no
  session, and the review ends; a topic folder the mod cannot list at a take-over, or no project root found up
  from the session's root, gives no `after`, so after a reload that review's resumed answer is refused and a new
  round opens, while a root that `project_root()` would find elsewhere gives `after` 0 (the topic folder is not
  there), so only the clock checks hold there (no ordinary case of it is known); a root or folder reached through a link to a path with a control, format
  or separator character is taken over and its answer lost, as the command prints the resolved path; a submit that
  never settles holds both the hand-over and the wake until a reload, and with an answer that is no approval
  waiting, every plan gets its deny until a reload, a `/clear` or a `/resume`; a reload forgets that an answer
  waited, so another text then drops it; a `/rewind`, and leaving plan mode with no prompt before the next plan,
  end no approval (no event says so); under a guard that runs no `classic.*` hooks (`sec-default`) Claude Code's
  own dialog asks after the pass (not measured: the probe machine had no guard); other installed mods are trusted,
  as they can write `$.state` and rewrite the tool's input; whoever can write the answers or plan file, or get a
  start link of the inbox, can approve, and that approval ends plan mode with no dialog. In plan mode Claude Code
  by default leaves a Bash write to the auto mode classifier (`useAutoModeDuringPlan`); with that off, a Bash
  command the user allowed by a rule may still write (measured on 2.1.323 and 2.1.330, for a write the prompt said
  the user asked for). Write and Edit did not write there, but that run answered no prompt, so an ask was not told
  from a deny; Claude Code's own plan file is written in plan mode with no ask.
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
  in Waiting on you has `orphan: true` and `since` that run's `started`, and its page's data `orphan: true`. The
  row's kind is `L.orphanKind` (a held round's stays Open elsewhere), the opened row and the round's page (its
  banner, in the round's language) say `L.orphan`: the run that asked has ended (its session may live on), and the
  answer goes to the project's sessions; its Done banner is `L.doneOrphan`. Once every round of an orphan group is
  done, the inbox writes its answer request (see the request rule), and so it does when it lets go of a group with
  every round done whose newest run stopped without handing over. A live run whose rounds are exactly a
  group's (as `runs/` lists them: the open ones) takes that group on, live, with its number and open tabs; a
  live run that lists any of its rounds otherwise ends it, so its old tab's saves get 404. A page run whose round lock the
  inbox took after the run's own look (the inbox started in between) looks again and is a waiter. Saves go through
  `check_answers` and the run's own writer, so a waiter wakes on the answers file. One inbox per home: it holds `inbox.lock` while it lives, trying for about half a second first (a run's look takes the
  lock for a moment).
- **The inbox reads a project's todo file and never writes it.** The first of `_bmad-output/todo.md`, `TODO.md`,
  `todo.md` under the root in `projects/<id>.json` that is a file inside the root, at most `MAX_TODO` bytes; or,
  when that file has a `todo` key (set by hand), the file it names and no other (`project_todo`): a string, checked
  as a picture's path is (`_inside`: no control character, no drive or network path, inside the resolved root
  with links followed, an absolute path too), and a regular file. Its `file` on the page is the resolved name
  relative to the root, each control, format, line-separator or lone surrogate character a space (`_one_line`:
  a link's target skipped the key's check). A bad key, or one naming no file, keeps the
  project on the list with fixed text for `file` and `error` (`TODO_KEY_FILE`, `TODO_KEY_ERROR`), never the
  value; a message or Later on that project gets 409. A project whose root is gone shows no todo file. An
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
  items with the same first line share their requests. The same route takes exactly `{kind: "later", project, ref}`
  for Later on a waiting row: `ref` must be an open item marked waiting now (409 otherwise), and the text is the
  server's own `LATER_TEXT` (park the item: take its marker off, keep it open). Any other shape gets 400, a
  `kind` of `vuln` or `vulns` too. The route takes no `kind:
  "answer"` (400): the inbox alone writes one, once every round of an orphan group is done (by Done, `accept`, or
  an answers file another program wrote that it takes in), one per group; and at a look that lets go of a group
  that has every round done and handed over none yet (orphan or not), when the newest (by `started`) gone
  `runs/` entry listing exactly its rounds stopped: a dead pid without `ended`, or `ended` with `exit` 2 or 130,
  never 1 (a reused start link: its stderr tells the agent to doubt the answers), 0, another value or none; and no
  entry on them `ended` with `exit` 0 at or after that one's `started` (two runs at once: the other handed over). That
  is a waiter stopped just after a Done in the inbox, rounds finished from outside after the run stopped or timed
  out, before that look, or a Done that ran just before that look made the group orphan. One that cannot be
  written there is one stderr line. `ref` `<topic folder> / <round>, <round>`
  (the group's run's folder and rounds as `runs/` lists them, names without `.json`, so a round done before the
  group was made is named too; cut to `MAX_REF`), the text the server's own `ANSWER_TEXT` (tell the user first; no
  answer, path, link or token), the file ASCII-escaped. It writes it after the round's Markdown copy and before
  its answers JSON, so a request that cannot be written fails the save (500, `failed`) and a JSON that fails
  after it leaves a request whose text says the round then needs nothing (a retry writes another); one for a file
  taken in from outside that cannot be written is one stderr line, and the let-go of its group tries once more
  (one more line when that fails too). A live run's Done writes none (its run hands
  over, unless it stops first: then the let-go look above does). A Done save to
  a group looks at the runs first, as `accept` does, so a run that ended since the last look counts as gone; a
  group that look lets go refuses it with 404 (`gone` for `accept`), writing nothing. The inbox
  writes `requests/<project>/<id>.json` (`schema, id, kind, ref, text, at`; `id` 16
  hex digits, `kind` `todo`, `later` or `answer`; a `later` reaches the agent as a message on the
  todo item, an `answer` as one on the Signoff rounds), atomically, 0600 in 0700 folders. A reader also takes
  `vuln`, the kind 0.3.0 and 0.4.0 wrote for a vulnerability finding handed over, which reaches the agent as a
  message on the vulnerability finding (the inbox writes none and shows none), and skips a file of a kind it does
  not know (so a session on an older plugin drops an `answer`
  request, which reaches the project only through a session on a plugin that knows it (its mod, or before 0.5.0 its
  monitor), running when it is written, or at a later SessionStart hand-over on such a plugin). The list page shows each item's recent
  requests (text, time, done) and never an id or a path. The agent marks one handled with
  `signoff inbox done <id>`, which writes an empty `<id>.done` beside it; nothing else writes there.
- **A request reaches the model as one line.** `signoff inbox requests` and the SessionStart hook build it with
  `request_line()`, and the mod sends it as the command printed it: a fixed frame, the ref and the text as JSON strings (cut to `MAX_REF` and `MAX_REQUEST_TEXT`,
  every control, format and line-separator character a space), and the id. Neither string can end its quotes,
  start a line or change the id. Never a path, a token or another project's request.
- **The mod hands each request to its session once.** It runs in every interactive session (see the mod
  rule), so a request costs no process until there is one to hand over. At each look while the inbox lives and
  the session's project is found, also with `hint` `off`, it reads the time of `requests/<project>/` in the home
  through `$.fs` (no folder: no request). Only when it changed since its last listing, when that listing was
  less than 2 seconds after that time (a coarse clock gives a write right after it the same time), while a batch
  it cut waits, at a retry, or at a turn's resend (below), it lists the folder through `$.fs`. A request is new when its `<id>.json` (16 hex
  digits, a regular file, not a link) has a time at or after the session's mark, has no `<id>.done` beside it,
  and is not among the ids it handed over (an id leaves them only when a listing shows its `<id>.done`: one not in
  the folder may be another project's, handed over before a `/cd`; past 1000 the oldest is dropped, which can
  bring a duplicate, never a loss). The mark, the project it is for, and those ids are in `$.state` (`requests`:
  `since`, `project`, `sent`): the mark is set at its first live look without one, `SINCE_MARGIN` (10 s) before it
  (a request near the start may also come in the hand-over: twice beats never; the margin is a guess), so a reload
  hands nothing over again, and a `/clear`, which empties `$.state`, sets a new mark (a later session start with no
  other live session of the project hands over what waits). When the session root's project is not the one held
  beside the mark (a `/cd`, also across a reload), it sets a new mark the same way and keeps the ids; a held mark
  with no project (a build before it) takes the current one and keeps its time. Only with a new
  request it runs `signoff inbox requests <folder> <project> <id>…` (the session root the project was found for:
  the command checks it is in that project) with at most `MAX_HANDOVER` ids, oldest first, one at
  a time, and reads only its stdout, as one JSON line `{requests: [{id, line}], gone: [id]}`. A non-zero exit, a
  cut stdout, another shape, an id it did not ask for, or a line that does not start `Signoff inbox: ` or does not
  end with its id: nothing is sent, and it tries again after 60 seconds, three times in a row per load, then at
  the folder's next change or the next load. A line in that frame with its own id that holds a control, format or
  line or paragraph separator character or a lone surrogate by the engine's Unicode table, or is over 4096
  characters, is marked and not sent (an
  older Python's table misses a few format characters), so it holds up no other line; a later session start with
  no other live session of the project hands it over, as it is not done. It marks the reply's ids handed over first, then sends the lines, one per line, as one
  `$.prompt.submit` (framed as the plugin's, never `asUser`), which Claude Code runs as a turn of its own once
  the session is idle, as this mod has one submit out at a time (the mod rule's plan review: while a wake is out
  the hand-over waits, and the wake's end starts it); a submit that did not enter (a `drop`, or an error) unmarks them, as a failure, and this
  load keeps those checked lines, the last batch refused: after an Esc Claude Code drops every plugin prompt until
  the user sends input, which may be long after the last retry. At the next main turn (`turn.start` without
  `agentId`), also with the retries used up, it looks at once without the turn waiting for it, or at the look right
  after it (a look or a hand-over ran, or the folder's time could not be read; the inbox not live: its first live
  look): one listing, then the kept lines,
  less those done or gone since, go as the first time, marked first, with no process; what else is new goes at the
  next look. Refused again is a failure again, and they stay for the turn after. With none kept, a turn costs the
  hand-over no process, `$.fs` call or submit. A `/clear`, a project change and a changed session root forget the
  kept lines, and a submit refused after any of them keeps none: they were checked for that root only, so after a
  root change the next look lists again and asks the command, which checks the new one. So does one after a root
  move that a look saw while `inbox requests` ran: that run's reply is neither marked nor sent (a faster move: see
  the known limits). A hand-over that a `/clear`
  overtook writes no mark back and sends nothing (a submit the engine already holds runs in the cleared
  conversation too: it cannot be taken back). A reload loses the kept lines, and the normal look fetches
  them again. It never works around the Stop: no `asUser`, no tighter timer, no other channel. `gone` ids (done,
  unreadable, or of a kind this
  plugin does not know) are marked and never sent; what the command cut at `MAX_HANDOVER_TEXT` goes at the next
  look. Every live session of the project gets each request. Nothing else goes into the prompt: no path, token,
  link or other project's request; it writes no file. Known limits: a session whose mod does not look
  (`claude -p`, where it loads but is not armed; the SDK; `allowManagedModsOnly`) gets requests only at a later session start and
  still counts as live, so new sessions of the project get no hand-over while it runs; a project the
  session's root moves into gets its older requests at a later session start with no other live session of the
  project; a root move while `inbox requests` runs that no look saw before it ended (the usual case, as the
  command takes far less than a look's 5 seconds) still has that reply, checked for the old root, sent; and the
  hooks note a session by each event's `cwd`, so after a shell `cd` into another project the inbox may count it
  there while its mod hands over the root's project's requests.
- **Only listed files are served.** Pictures and boards by number, each resolved inside the decisions file's
  folder; read into memory at start for a decisions run, read on request (with the same link check) for the
  past-decisions page. No SVG, no directory listing, no path from the request ever touches the disk.
  Attachments (`.md`, `.txt`, `.diff`, `.patch`) follow the same folder rule, are read at start, on both pages, and go inside
  the page data as text; no route serves them. Every such path is checked as text before it is resolved: no
  control character, and no drive-relative or network path in Windows syntax (`C:foo`, `\\host`, `//host`), on
  every OS. An absolute local path still works for pictures and boards inside the folder, as in 0.1.0; attachments
  take only relative paths.
- **Boards stay sandboxed.** Boards are served under their own token (`board_token`), which opens nothing else.
  Every board response carries `BOARD_CSP` (`sandbox allow-scripts`, nothing loaded from outside),
  and the page embeds it with `sandbox="allow-scripts"` and never `allow-same-origin`.
- **The past-decisions page is read only.** No POST route answers there (405), nothing is written.
- **Agent text is never HTML.** The page uses `textContent` and DOM methods only: no `innerHTML`, no inline
  `style` attribute, no inline event handler. A test greps the assembled page, every part in, for these.
- **The CSP stays strict.** Every executable script and style element carries the nonce (the inert JSON data
  block is the exception). These elements are in `page.html` itself, never in a part (a test checks). Nothing
  loads from outside.
- **Colours are theme tokens.** The page's colours are CSS custom properties, set only in the theme part (in
  `page.html`): one
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
- **The inbox list is one screen.** A sidebar (views: Waiting on you, To do; then each project
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
  (`since`: a round's run start, a session's `waiting_at`, a todo item's file time; the exact time in the tooltip);
  a round's row whose `opened` (its round file's time, as on its page) is `OLD_ROUND` (2 days) old or more also
  gets the pill "Open 2+ days" inside its link, and its open row a note saying how long and why, judged at render
  only, never moving `since`, the sort or the search. A round's row whose round has `context` gets it as
  `context` (as `rows` titles are: one line, cut to `MAX_CONTEXT`, the project's folder and the user's home
  replaced), shown after the tab on its short line (` · ` between them) and as a paragraph in its open row;
  Answered rows, `rows`, `offer`, `state.json` and the app never get it. Its rows sit under one heading per project (a run outside any project under its folder's name), the
  header's line saying how many wait in how many projects (a run outside any project counts in no project). Two buttons order the rows within each project: oldest
  first (the default), newest first; the choice is `sort` in `prefs.json` (a stored `project`, the old By project,
  reads as oldest first), the order only changes on screen (j and k follow it). To do shows each project's rows
  behind one count row (a button with `aria-expanded`), closed until clicked; a project's filter opens its own.
  Answered (the rounds answered, under Waiting on you) is one such group: its heading is the count row, closed
  until clicked, and a project's filter opens it (with that project's rows only). No `prefs.json` key keeps any
  of them.
  j, k and Focus walk only the rows shown, never a closed group's. **The search** is a `role=search` field in the
  header (a phone gets it on a second line), live as you type, over every view and past the sidebar's filter: a
  row matches when each word of the query (split on spaces, folded with NFD, combining marks dropped, lower case)
  is somewhere in its text, which is the project's name, the row's title, kind and short line (a round's tab
  and context), plus, per row, a
  round's item titles (`items` on a round's row: at most `MAX_ROW_ITEMS` of `MAX_ROW_TEXT` characters each, titles
  only, never a file or folder name), and a todo item's body and its requests' texts. The rows not matching are hidden,
  the groups holding a match open (a toggle click does nothing meanwhile), a sidebar pick ends the search, an
  empty field gives the list back as it was. `aria-live` on the count (`N matches`, `No matches`) and a Clear
  button. `/` (not while typing in a field) and Cmd+K (Ctrl+K off a Mac; from a field too) focus it (typing in
  it leaves Focus); Esc in it empties it and goes to
  the open (else first) shown row; Enter or the down arrow in it go to that row, then j, k and Enter walk the
  results. The query goes through a reload with the rest (`query` in the kept state). The groups of Waiting on you follow their first
  row in the chosen order, so the oldest (or newest) row is always at the top. A self-reload keeps which To do
  groups were open, and whether Answered was. A live session (its `sessions/` file no older than `SESSIONS_MAX_AGE`, not `ended`, its
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
  its question, text and pictures, a margin with the `stakes` chip (glyph and word), the recommendation (the recommended labels, and the option's detail when one is recommended),
  the cost and the `irreversible` line under **Cannot be undone** (between the question and the options under 1100 px; in Focus at 760 px or less, CSS order puts it
  right after the text, then the commands, the pictures as thumbnails at most 180 px high, and a picture that takes
  marks small beside its caption with a Larger / Smaller button, `aria-expanded`, its name ending in the picture's
  number), then the options, numbered for the 1 to 9
  keys. A number item has instead a native range and number input bound together (each with a name, the range
  with `aria-valuetext` and the unit), the range holding the value that is saved, and Skip as a checkbox with no
  digit key; the digits do nothing there, and j, k and f still work while the range has focus. A `settled` item
  starts folded (its **Settled earlier** line, the choice it holds and Change, `aria-expanded`; the margin still
  shows), a view never saved, and the digits do nothing while it is folded; in a read-only round it shows the line
  only. The sidebar lists an item's marks (agent waits, high stakes, irreversible, each a glyph and a word) under its
  title. An item's commands (between its text and pictures) are the inbox's code blocks (`codeBlock`, above the
  inbox's part of the script), with Copy and the relative-delete hint whatever their `lang`; every code block
  wraps (`pre-wrap`, `overflow-wrap:anywhere`), and `_command()` refuses the known invisible and blank characters,
  white space at a text's ends and a text over `MAX_COMMAND_TEXT` or `MAX_COMMAND_LINES`. This catches the known
  tricks, not every one: the user reads a command before running it. The key bar holds the save state, why Done waits, and the open round's Accept this round; the header keeps
  the progress, Focus, Accept all recommendations and Done. The Summary is the sign-off sheet. f is Focus (one
  item at a time; the round's tail after its last item), Esc leaves it or closes the drawer (in a line note's field,
  Esc first goes back to the line's number); a page loaded at
  760 px or less opens in Focus. A `#focus=<round>/<item>` hash opens that round's item in Focus; one that names
  no round and item of the page is ignored. The header's project line also says when the open round was asked
  (`opened` in each round of the page data: its round file's time, read with its hash at load, `null` when not
  `0 < mtime <= now`) as a `<time>` with the full time as its title; an open round (not read only) that old by
  `OLD_ROUND` or more adds the pill there and a `readonly-note warn` at the top of its panel. A round's `context`
  is its panel's first line (`p.context`, as text; after the outcome on the past-decisions page); at 760 px or less in Focus it shows with the first item only
  (`at-start` on the panel, beside `at-end`). The Summary and the
  past-decisions page show no time. The past-decisions page puts its list in the sidebar and has no Focus. The page
  data gets `mono` (`mono`, `color`: the inbox's monogram for the project's name) on a run and the past-decisions
  page, never more. Every label a round's page uses has English and Hungarian (a test reads the page for each `L.name`; a
  key built at run time, such as `L['st_' + s]`, it does not see).
- **Nothing is deleted.** Signoff writes the answers file and its Markdown copy (both atomically) and, only
  when absent, a `.gitignore`; so does `signoff elsewhere` (see the elsewhere rule), whose temporary file goes
  again when the hash before its rename changed. `signoff plan` writes `plan-NN.md`, `round-NN.json` and, for a
  round it replaces or the inbox did not take in, `round-NN.outcome.json` in its topic folder
  (`<YYYY-MM-DD>-plan-<session>`), each created exclusively, never over a file or through a link, and its run writes the
  `.gitignore` there as a run does. Once an inbox has run, it also writes an empty `<round file>.lock` (such as
  `round-01.json.lock`) beside each open round and `runs/<run>.json` in the Signoff home (see the next rule),
  and its hooks write `projects/<id>.json`, `sessions/<session>.json` and `waiting.flag` there (see the hooks
  rule). With the macOS app installed, a new session's SessionStart keeps `launcher` in the home, a link it replaces by a rename
  of a new one (see the hooks rule). The inbox
  writes, in the home, `inbox.lock` (empty; its time touched at each look), `inbox.json` (`schema, server_id, port, control`; 0600, no token),
  `state.json` (see the state rule), `inbox.sock`, `requests/<project>/<id>.json` (see the request rule),
  `prefs.json` (see the theme rule), and
  `logs/inbox.log` when `signoff inbox open` or the app starts it (the app makes the home and `logs/` for it,
  0700, when absent); `signoff inbox done` writes `<id>.done`. With `--notes`
  (POSIX), a run that exits 0 creates one note per round open at its start in that folder (`write_notes`). The
  agent writes that argument: the skill takes it from the Notes folder setting, but nothing binds the two, so the
  checks keep a misled agent's note out of dot folders and the rounds' own project only (another project's folder
  is accepted). The folder: an absolute
  path, normalised (`normpath`) before it is looked at, not a link itself, no part of it or of its real path
  starting with a dot, the user's own, not writable by group or others, outside the rounds' project root (else
  their topic folder) and the home, its real path checked against the descriptor the note is created through. A
  value that is still a `${user_config.…}` placeholder is off, as an empty one. The note `O_CREAT | O_EXCL |
  O_NOFOLLOW`, 0600, never over a file or link already at its name (one stderr line, nothing replaced), its
  private answers left out (`answers_markdown(..., hide_private=True)`), and a line note's `quote` too (the
  agent's file text, which a synced folder must not get); one round that fails costs only its own
  note. A SIGINT, SIGTERM or SIGHUP while the notes are written acts as the signal does (the run's handlers are put
  back by then), so the exit code may not be 0 although the paths are on stdout. The inbox never writes one. The one thing Signoff removes is a
  dead `inbox.sock`: only when it is a socket in the inbox's own home, the starting inbox holds `inbox.lock`, and
  a connect to it is refused. Anything else at that path stops the inbox with an error. A test checks both.
- **One writer per round, once an inbox has run.** The switch is `inbox.lock` in the Signoff home
  (`inbox_home()`; `SIGNOFF_INBOX_HOME` is for tests), which only the inbox creates; a relative home counts as
  off for runs and hooks, and `signoff inbox home` exits 1 for one. Without it a run is the plain
  run, with no new file. With it, a page run holds an exclusive lock on `<round file>.lock` for each open round until
  it ends, and exits 1 when another process holds one, unless a second look finds the inbox alive: it is then a
  waiter (a file system that cannot lock: a stderr line, no lock).
  While the inbox holds `inbox.lock`, a run is a waiter: no port, no browser, no idle timeout, no lock; it looks
  at its answers files once a second and exits as a run does. It reads its pictures and boards at start, as a
  page run does (the pictures of rounds already done too; the inbox reads only the open rounds its run entry names),
  so a bad one is a load error (exit 1). It also checks
  once that the inbox took its rounds in: once its entry is written (a failed write: no check), and once
  `inbox.lock`'s time has changed twice while `inbox.json`'s `server_id` stayed the same (the inbox touches it at
  each look, before it reads `runs/`, so a whole look of one inbox saw the entry; another `server_id` counts
  afresh), it tries each open round's lock without creating the file and lets go at once. When every one is free or absent and the inbox is still alive, the inbox did not take
  them in (an inbox an older Signoff started refuses a round with a key it does not know, and locks nothing): one
  stderr line saying to restart the inbox and run the command again, and exit 1 with nothing on stdout. Any lock
  held (the inbox's, or another run's while the inbox shows the rounds read only), a file system that cannot
  lock, or an inbox gone meanwhile: it waits as before. No look, no verdict, so a slow inbox only makes it later.
  Every run and waiter writes `runs/<run>.json`
  (`schema, run, project, folder, rounds, mode, pid, started`; never a token or a link) at start and adds
  `ended` when it exits by itself, on Ctrl+C, SIGTERM or SIGHUP, with `exit`, its exit code (1 start link reused
  or rounds not taken in;
  2 timeout; 130 stopped; 0 Done, written only once its answers paths are on stdout), unless an unexpected error
  ended it; an entry without `exit` (that, or an older plugin) counts as handed over. One killed some other way,
  or whose paths could not reach stdout (no reader: `BrokenPipeError`), leaves none, and so does a Done run that
  gets SIGTERM or SIGHUP while it prints its paths (its handlers are back to the defaults by then, so the signal
  kills it: the inbox may then hand over answers the agent got, never lose them). Readers check
  `pid` and `started`, and skip `.tmp-*` files. `signoff elsewhere` follows the same lock (see the elsewhere
  rule): with `inbox.lock` it writes only under the round's lock, through the inbox that holds it, or not at all.
- **`signoff elsewhere` marks one item through the round's one writer.** `signoff elsewhere <round file> <item
  id> [<note>]` (3 or 4 words, the second not starting with `-`, so a decisions folder named `elsewhere` still
  runs) loads the round as a run does (`Round`) and builds the save that marks the item (`_mark_state`): the saved
  answers with that item as `{choice: null` (`[]` for `many`), `comment: <note>`, `source: "elsewhere"}`, plus
  `private: true` when the item starts Private on the page (`_private_default`: `sensitive` with a text field), the
  free text kept, `accepted` false, so the round's acceptance resets as a change on the page does. It refuses
  (exit 1, one stderr line, nothing written) a bad round or answers file, an unknown item, a done round, an item
  with `settled` (its recommendation is the user's earlier answer; `check_answers` refuses `source: "elsewhere"`
  on one too, from the page or a file), and an item that holds anything of the user's (`_users`): a choice with a `source` other than `recommended`, an
  unticked recommendation (as `_decided` reads it), a comment, `other` text, marks, line notes, or `private` other
  than the item starts (an earlier mark of the agent's, with nothing else, may be replaced). The note is one line of at
  most `MAX_NOTE` (500) characters with no tab, line break or character `_hidden` finds (an emoji's variation
  selector and joiner aside), checked in the command and again by the inbox (on the inbox's own Python, which may
  know fewer characters). `check_answers` holds a `source: "elsewhere"` comment, from the page or a file, to the
  part of that rule every Python reads alike: at most `MAX_NOTE` characters, no control, line-separator or lone
  surrogate character. The same mark again (choice, note, source and Private as the mark has them) writes nothing
  (exit 0). Who writes:
  - no `inbox.lock` in the home: no locks exist. It writes through `_Rounds.store` (`_Marker`): the Markdown copy
    first, then the JSON, each from a synced temporary file, with the answers file hashed again just before each
    rename. A hash changed since the load (at the save's own check or at either of those) starts over, three tries
    in all; when it changed between the copy's rename and the JSON's, the copy is made again from the answers file
    on disk (`_copy_from_disk`, best effort), so a refused mark leaves no copy of itself. This is
    best effort, as "No stale writes" says before an inbox, and the window is open both ways: a page save landing
    between that last hash and the rename is lost, a Done included (the run then exits 0 with its path while the
    file says `done: false`); and a page save already past its own disk check renames over the mark after the
    command exited 0 (a page run's save has no hash before its rename), so the mark is gone from the page: run it
    again.
  - `inbox.lock` there and the round's lock free (tried for half a second: another mark, or a waiter's look,
    holds it for a moment): it holds the lock for the write only and writes as above (a file changed before it
    took the lock fails the save's own check, and the next try reads it again). The inbox, whose look meets that lock for a round it takes in, shows it read only until its next look
    takes the lock (`take_locks` at every look), never for good. A page run starting in that moment exits 1 as
    beside another run; run it again. This holds a second writer out only when every writer locks: a page run
    started before `inbox.lock` existed, or one on a file system that cannot lock, holds no lock, and the first
    case's window is open beside it.
  - the lock held and an inbox alive (`inbox_alive`): the `elsewhere` op on the control socket, `{op, round, item,
    note}` and no other key (`round` the round file's resolved absolute path, `item` an id, `note` as above; else
    `{error}` with its shape). The line is UTF-8 and not escaped to ASCII (`control_line`), and the command checks it
    fits `MAX_CONTROL` before sending. The inbox matches `round` as text against the rounds its groups hold (it
    never opens it), looks first (`_scan`), and writes through the run's own writer (`_Group.store`) at the round's
    `rev`, under the group's lock, as `accept` does. Replies: `{ok: true}`, or `{error}` with one word:
    `gone` (no group holds that round, or no such item), `done`, `held` (another run holds the round: its group is
    read only), `answered`, `changed` (a save landed first; the command asks again, three times in all), `failed`
    (a write failed; it may leave a new Markdown copy, as a failed save does). `gone` for a round the command found
    says the path must be spelled as the run's (the match is by text, so another letter case or Unicode form of
    the same file misses), not to try again. Any other error (an op the inbox does not know, as one started
    before an update gives, or the shape refused, as an inbox on an older Python may refuse a note's character):
    exit 1, nothing written, and one stderr line with the inbox's text that says to restart the inbox (`signoff
    inbox stop`, then `signoff inbox open`) and run the same command again, then, if it still refuses, to use
    plainer characters.
  - the lock held and no inbox alive (a page run holds it): exit 1, nothing written, as for a second run.
  The lock file is opened non-blocking and never through a link (`_hold_round`). A file system that cannot lock:
  `flock` refusing with one of `NO_LOCKS` (`ENOLCK`, `EOPNOTSUPP`, `ENOSYS`, `EINVAL`) on a descriptor `fstat`
  shows is a regular file: one stderr line, then the first case. Anything else at the lock (the open failing with
  any error, a link, a FIFO, socket, device or folder at its name, another `flock` error): exit 1, nothing
  written, as such a file fails with the same errors. A run
  or the inbox takes a direct write
  in as any outside write (open tabs get 409 and reload); an op's write bumps the round's `rev` as a save does.
  The item counts as decided (`_decided`): `rows` leaves it out of a round's items, as the page shows it decided,
  and `offer` skips the round (see the notification rule). The page shows **Decided elsewhere** and the note
  (`textContent`) above the item's options, sends the saved choice and note back with every save, Accept all
  neither sets nor undoes it, and the user's own pick, typed text, number or rectangle on it makes it `user` (the
  note goes).
- **`signoff plan` puts a plan on one fixed round and waits on it beside the inbox.** The mod runs it (AD-29),
  never the agent: `signoff plan --topic <t> --folder <f> [--resume]`, each flag once in any order, each value as
  the next word (the mod's argv; a `--topic=…` form is no flag of this command), the plan on stdin. It is taken as
  this command when the words after `plan` hold one of the three: a run takes neither `--topic` nor `--resume`, and
  `--folder` only with `--history`, which takes no decisions folder, so a decisions folder named `plan` still runs.
  `--topic` matches `[0-9]{4}-[0-9]{2}-[0-9]{2}-plan-[A-Za-z0-9-]{1,8}` in full; `--folder` is absolute or relative
  to `project_root()` of the current folder (none: exit 1). The plan: at most `MAX_DECISIONS` read, strict UTF-8,
  the white space at its end cut by `PLAN_TRIM` (what JavaScript's `trimEnd()` cuts, so the mod holds the same
  bytes; not Python's `rstrip()`), then 1 byte to `MAX_ATTACHMENT`. All of that, a live inbox (`inbox_on` and
  `inbox_alive`), a topic folder that is no link or file, and no older open round of the topic a live run waits on
  (a recent `runs/` entry with no `ended`, its pid alive, its folder the same folder by `os.path.samefile`: "a plan
  review is still open in another process") come before the first write; any refusal is exit 1, one stderr line,
  nothing written. Then it makes the topic folder as `mkdir -p` does (one it cannot make: exit 1, "cannot write
  in") and opens it with `O_DIRECTORY | O_NOFOLLOW`
  (a link swapped in since the check: exit 1; either exit writes no file, but folders the `mkdir` made can stay
  behind, empty), and through that descriptor (`dir_fd`; on Windows by path) creates,
  each `O_EXCL | O_NOFOLLOW`: for every older open round (no outcome file, no answers file that says done)
  `round-NN.outcome.json` `{log: "signoff plan", line: "replaced by round-MM", date}`, then `plan-MM.md` (the cut
  bytes) and `round-MM.json` (`plan_round`: every text a `PLAN_*` constant; one item `plan`, Approve and Ask for
  changes, `blocking`, `comment`, `lines`, the plan attached with `open`, and no `recommended`, so `offer` never
  names it). MM is the next number after every `round-NN.*` and `plan-NN.*` name there (answers files, Markdown
  copies, outcome files and locks too), so a new round never meets an answers or outcome file left at its number,
  as `git clean -fd` keeps the ignored answers files. Without `--resume` it always writes a new round, so no
  approval is ever reused. With it, only the newest round is looked at, and reused only when its `round-NN.json`
  holds the bytes `plan_round` gives for its number, its `plan-NN.md` the cut bytes, both regular files read with
  `O_NOFOLLOW`, and it loads as a run loads it (`Round`: the answers checked, a done one linked to this version):
  done, its answers path at once (exit 0); open, waited on; closed, anything else or no round: a new round. It waits
  as a waiter on that one round (`_run` with `waiter=True`: never a page run, even when the inbox stops meanwhile).
  When the inbox does not take the round in, it closes it (`not shown: the inbox is older than this plugin;
  restart it`) and exits 1.
- **The hooks cost a session nothing.** They run in every Claude Code session of everyone who installs the
  plugin, so `inbox hook` always exits 0 (exit 2 would block a prompt or a stop), prints nothing (SessionStart and
  UserPromptSubmit stdout goes into the model's context) but the hand-over below, and writes nothing while
  `inbox_on()` is false, nor for
  input it cannot read, a session id that is not a plain id, or a `cwd` outside any project. Otherwise it writes,
  atomically, 0600 in 0700 folders, `projects/<id>.json` (`schema, id, root, name`; when absent, so an optional `todo` the user set,
  which the hooks never write, stays) and
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
  `inbox.lock` touched within a minute (`find -mmin -1`; the inbox touches it every `STATE_EVERY` seconds; a `find` that fails leaves the lock alone to decide), tested after `waiting.flag` for `clear`.
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
  as Markdown or HTML (GFM: no element, link, strikethrough or character reference), and a control (but tab),
  format or lone surrogate character shows as `U+XXXX`; a `%` after a `%` is escaped (Obsidian's comment). Known
  limits, which no Markdown escape stops: a GFM viewer still links an email address, and GitHub's own extensions
  (`$math$`, `:+1:`, `@user`) still render. Its test pins the output, checked against GitHub's renderer in `gfm`
  mode.
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
  the shown window's no-reload path, and none, an error reply or a failed ask leaves
  today's behaviour. The menu bar panel, its menu and a banner never take the target.
  The app never sends a choice. Quitting the app leaves the inbox running.
- **stdout is answers paths and nothing else**, one per line, in tab order. Agents parse it. Messages go to
  stderr (`--notes` too: its notes print nothing on stdout, and each refusal or failure is one stderr line starting
  `signoff: notes:` that changes neither stdout nor the exit code). The exceptions: `signoff inbox home` prints the Signoff home (`inbox_home()`, never creating it),
  `signoff inbox hook` prints nothing but the SessionStart hand-over, `signoff inbox requests <folder> <project> <id>…` prints one JSON line, its
  reply, for the mod, and `signoff inbox rows <folder>` prints one JSON line, the `rows` reply (for a folder outside any
  project `{"rounds": [], "more": 0}`), for the mod. `signoff elsewhere` and `signoff inbox serve|open|stop|done`
  print nothing on stdout
  (`open --app` no link on stderr either); `open --no-open` prints its start
  link on stderr, as a run does, and so does `open` when no browser opened (`webbrowser.open` False; a run then
  says `no browser opened` under its link). In an SSH session (`SSH_CONNECTION` or `SSH_TTY` set and not empty)
  with no `BROWSER` set (VS Code's Remote-SSH terminal sets one that opens the link on the user's computer),
  a run, `--history` and `inbox open` try no browser (`ssh_without_browser()`) and print the link on stderr with
  the `no browser opened` line the skills key on. Nothing else changes there; the mod's panel rows never read `inbox open`'s stderr, so over SSH a row opens
  nothing visible (a known limit, as on any machine without a browser). A browser a run or `open` starts gets `/dev/null` for each of
  stdout and stderr that is not a terminal (`_open_browser`), so it writes nothing there and holds no caller's
  pipe open (on Linux the stdlib starts `xdg-open` or `gio` with them). `signoff plan` is no exception: its one
  answers path, or nothing.
- **Exit codes are a contract:** 0 Done, 1 invalid input (a usage error, a bad `--port` or `SIGNOFF_PORT` (one
  line, checked after argparse, without the usage), a port in use (one line that names the port, before any
  `runs/` entry or round lock: something answered a connection to `127.0.0.1:<port>`, or the bind failed; best
  effort, see the security rules), every round already done, a round
  locked by another run, the start link used twice, a waiter whose rounds the running inbox did not take in (see
  the one writer rule), or a Python older than 3.9: one stderr line, from the first
  lines of `main()`; `inbox hook` then exits 0 silently; the
  file must keep compiling on 3.7 for that, which a test checks), 2 idle timeout (never for a waiter), 130 Ctrl+C,
  SIGTERM or SIGHUP (a run and a waiter, on macOS and Linux: they stop as on Ctrl+C; a signal ignored at start
  stays ignored; on Windows a stopped process gets no signal it can catch; `--history` and the `inbox` commands
  keep the default for both). `signoff inbox home` exits 0, or 1 for a relative home; `signoff inbox hook` always
  exits 0. `signoff inbox
  serve` exits 0 when stopped, 1 when it cannot start (another inbox, no Unix sockets, a bad `SIGNOFF_PORT` or a
  port in use, each one line), 130 on Ctrl+C; `open`
  and `stop` exit 0, or 1 when no inbox answers (`open` also for a bad `SIGNOFF_PORT` when it would start one, and at once,
  not after its 15 s, when the inbox it started has ended and none other holds `inbox.lock`, as on a taken port); `open` with a target also exits 1 for a malformed target, and
  `open --app` (with a target or none) exits 1 for a malformed one, off macOS, for a relative home, when no app is found where the mod looks, or
  when `open -a` fails, else 0. `signoff inbox done <id>` exits 0 (also when marked before), or
  1 when there is no such request. `signoff inbox rows <folder>` exits 0 with its line (outside any project
  without asking an inbox, which need not run), or 1 with nothing on
  stdout for a relative folder or home, when no inbox answers, or when the inbox answers without `rounds`.
  `signoff inbox monitor` is no command any more: a usage error, exit 1. `signoff inbox requests
  <folder> <project> <id>…` exits 0 with its line (no requests folder: every id gone), or 1 with nothing on stdout
  for a relative folder or home, a folder whose project (`project_id(project_root(folder))`) is not that one, a
  project or id that is not 16 hex digits, no id, more than `MAX_HANDOVER`, or one given twice. `signoff elsewhere` exits 0
  when written or already marked with the same note, else 1 with one stderr line: a bad note, round or answers
  file, an unknown item, a done round, the user's own answer on the item, the round's lock held by a page run, an
  inbox refusal or no answer, any other error the inbox answers (such as a request shape it refused), a lock file that
  cannot be opened (not a file system without locks), a line too long for the control socket, a file that
  kept changing through three tries, or a write that failed (a file system that cannot lock adds a line to an
  exit 0). `signoff plan` exits as a waiter does (0 Done, 130 Ctrl+C, SIGTERM or SIGHUP; also 0 at once for a
  `--resume` whose round is done), and 1 for bad input (a usage error, stdin not UTF-8, empty or over
  `MAX_ATTACHMENT` once cut, a `--topic` off its pattern, a relative `--folder` outside any project, a topic folder
  that is a link or a file), when no inbox is live, or when a live run waits on an older open round of the topic,
  each with one stderr line and nothing written; 1 when the topic folder cannot be made (a read-only decisions
  folder, say) or opened as a folder of its own (a link swapped in, say), with no file written, though an empty
  folder the `mkdir` made can stay behind; 1 when a file it creates is already there (another writer since its
  look), leaving the older rounds' outcome files and, when the round file was the one there, `plan-MM.md`; and 1
  when the inbox does not take its round in, which it then closes with an outcome file.
- **Done waits for acceptance.** With several rounds open, a Done save is refused (409) until every other open
  round is accepted; Done accepts its own round, so a single round stays one click.
- **No stale writes.** Every save from the page carries `rev`, the round's save count it last saw; an older one
  gets 409 and the page asks for a reload. The server also refuses (409) to write over an answers file that changed
  on disk since it last read or wrote it (the hash of the bytes it wrote, never a read-back), for example from a
  second run on the same folder; loading the page, and the run's own look about once a second, take such a file in,
  so a reload lets saving go on, and a file that says done closes the round. A file that fails `check_answers` (or
  says done for another version of the round) is not taken in: one message on stderr, and saves stay refused.
  Within a run this is exact; across runs the round lock keeps a second writer out once an inbox has run, and
  before that it is best effort. `signoff elsewhere` writes through the same checks: before an inbox, by itself,
  with one more hash just before its rename; after, under the round's lock or through the inbox that holds it (see
  the elsewhere rule). A round closed by Done shows read
  only on reload, even while the run goes on.
- **Done is final.** Once a round's save with `done: true` is stored, later saves to it get 409, and a later
  run shows it read only. The run ends when every round that was open is done, by Done on the page or by an
  answers file another program wrote with `done: true`.
- **The file formats only grow.** Add optional keys; never rename or remove one. Update `schema.json`, the
  table in `docs/decisions-file.md` and the skill in the same change.

## Style

Plain, short sentences in docs and comments. A comment says why, not what. No new dependencies.
