# Security and privacy

## What runs, and what leaves your machine

**Runs:** one Python process for as long as the page is open, and your browser. Nothing else is started. The
plugin's `signoff` command (`plugin/bin/signoff`, on Windows `signoff.cmd`) starts the same Python program. The
plugin's hooks (`plugin/hooks/`) run a short shell script in every Claude Code session: at its start and end, at
each prompt, when it asks for your permission or an answer, after each tool call and at the end of each turn.
Until the inbox has run, they only look for `inbox.lock` and stop, and the script starts no Python (with
`SIGNOFF_INBOX_HOME` set, the Python program looks). On Windows, where the inbox does not run yet, they stop at
once. One exception on macOS, at a new session's start (not a resumed, cleared or compacted one), while the Signoff app is in `/Applications` or
`~/Applications`: the script, still without Python, makes `launcher` in the Signoff home a link to the plugin's own
`bin/signoff` (the home made 0700 when absent; nothing when the home is a link or not yours), so the app finds
the plugin. It writes only when the link is missing or points elsewhere, as a new link renamed over the old one,
and not when the plugin runs from a git checkout (a `.git` beside the plugin folder: `--plugin-dir` or a
directory marketplace, which runs in place); for that setup, build the app with the fixed launcher. Once the inbox has run, the script starts the Python program for a moment; after a tool
call only while some session may wait on you (`waiting.flag`). They always
exit 0 and print nothing, so they never block or change a session; without Python they do nothing. The one
exception is the hand-over of messages from the inbox at a session's start, described in [the inbox's page](inbox.md#the-inbox). They
run in `sh`: on Windows that is Git Bash, which Claude Code uses for hooks when it is installed; without it Claude
Code uses PowerShell, where each command does nothing (Claude Code then says once per session that a hook ran in
PowerShell; for Signoff's hooks you can ignore it, or install Git for Windows). The plugin's monitor
(`plugin/monitors/`) keeps one process for as long as each interactive session runs (the status line shows "1
monitor"): until the inbox has run, a shell loop that sleeps and looks for `inbox.lock` every 30 seconds (on
Windows a loop that only sleeps); then the Python program, which looks for new messages every two seconds. It never ends by itself, because Claude Code
would give the agent a turn to read that it ended. The plugin's mod (`plugin/hooks/register.tsx`, in a Claude Code
that runs plugin mods, interactive sessions, not on Windows) runs inside Claude Code and starts no process of its own
to look. Every five seconds it checks for `inbox.lock` in the default homes. Once one is there, it runs
`signoff inbox home` once each time Claude Code loads the mod. Then it reads `inbox.lock`'s time, `projects/` and
`state.json` in the home through Claude Code's file reads, and shows the session's project count above the prompt
(or in the status line, or nothing: the `hint` setting; with `off` it looks at nothing, and reads only when you
open the panel).

Its Open button and `/signoff:open` open a panel. They run `signoff inbox rows <session folder>` once. That asks
the running inbox's control socket for the project's open rounds and their open questions (run and item numbers and
one-line titles, with the project's folder and your home replaced; no path, link or token of its own). A URL the
agent wrote in a title stays, and so does the project's folder when the agent wrote it through a link, as the inbox
knows only its real path. The command starts no inbox, and the mod makes each title one line again before it draws
it, so no terminal escape passes. Each row runs `signoff inbox open` (which starts the inbox when none runs), with
only that row's numbers as arguments (the inbox maps them to the question; a stale one opens the list). The mod
never reads its output, so no start link reaches the screen or the conversation. The mod writes nothing. Each
opening of the panel (with `off` too) also reads `Signoff.app/Contents/Info.plist` in `/Applications` and
`~/Applications`, to see whether the app is installed.

A row's App button (macOS, the app installed) runs `signoff inbox open --app` with the same numbers. It checks the
same two folders for the app (a real folder, not a link, owned by you or root, whose `Info.plist` names the app's
bundle id). It leaves the numbers in the inbox's memory (one target, for at most a minute of awake time, used once
by the app's next start or reopen, and taken back when the app does not open) and runs `open -a` with that app's
path. So it starts the copy it checked, and no link is in any process argument. There is no URL scheme, which would
let any web page open the app.

The inbox, when you start it, is one more Python process that runs until you stop it. It touches `inbox.lock` every
two seconds, the mod's sign that it lives. The optional macOS app ([described here](inbox.md#the-macos-app)) is one more process while it runs. It runs
the plugin's `signoff` fixed when it was built, or, built with `SIGNOFF_LAUNCHER=link`, what the home's `launcher`
link points to: only an absolute link to a regular, executable file owned by you or root, in a home that is a
folder of yours closed to others. That command's `signoff inbox home` must then name the same home (a check
that both look at one home, not a limit on what runs). It serves
nothing, and every three seconds it reads the inbox's `state.json` and takes `inbox.lock` for a moment to see that
the inbox still runs.

**Leaves your machine:** nothing from Signoff. The server listens on `127.0.0.1` only. The page loads no fonts,
scripts or styles from anywhere, and neither can a board; their Content-Security-Policies block it. A board's own
script could still send data out over WebRTC, which no page policy blocks, so boards hold synthetic data only.
There is no telemetry and no update check.

**Written to disk:** the answers files, their Markdown copies and, when the folder has none, a `.gitignore`;
once the inbox has run, also the round locks, the run notes and the hooks' project and session notes and
`waiting.flag` described under [the exit codes](running.md#running-it-yourself), and the inbox's own files and requests described in [the inbox's page](inbox.md#the-inbox), with the `.done`
marks agents write; with the macOS app installed, the hooks' `launcher` link in the home; on your click, Uptick's scan list. The monitor writes nothing. The
past-decisions page writes nothing. Nothing is deleted, with two narrow exceptions: the MCP server removes its own
temporary files, and a starting inbox removes the socket a stopped inbox left in the Signoff home, and only that.
The `launcher` link, when it points elsewhere, is replaced by renaming a new link over it.

## Security model

Signoff is a web server on your machine that writes files, and any other web page open in the same browser can
send requests to `127.0.0.1`. The rules, in short:

- **Loopback, a fresh port, a secret path:** it binds `127.0.0.1` on a port the OS picks, and every request must
  carry a random 256-bit token in its path, compared in constant time. The `Host` header must be exactly
  `127.0.0.1:<port>`, which stops DNS rebinding. No cookies.
- **A single-use start link:** the browser is opened with a second token, `/b/<start token>/`, which redirects
  once and is dead from then on; that link is what other programs can see in the browser's command line. A
  second visit gets 410 and stops the run with exit code 1. A Done in the first 5 seconds after the link's
  first use ends the run only when those 5 seconds are up. A program that used the link first therefore cannot
  finish the run before your browser's visit is noticed, as long as your browser opens the link within that time.
- **No cross-site writes:** a save needs `Content-Type: application/json`, the page's own `Origin`, and, when the
  browser sends it, `Sec-Fetch-Site: same-origin`; its body is size-limited and checked against the round's file.
- **No stale writes:** each save carries the round's save count, and the server checks the answers file is the
  one it last read or wrote. When another tab or window of the same run has saved since, this tab's next save gets
  409, Done and acceptance included. The page then asks you to copy any text not saved yet and reload; the
  reloaded page shows the newer answers and saving goes on. A second Signoff run on the same folder is caught the
  same way on its next save, but only best effort, since two processes do not share a lock until the inbox has
  run: run one Signoff per topic at a time. An answers file that another run finished with Done stays done. While it runs, Signoff looks at
  its answers files about once a second: one that another program finished with Done closes that round, and when
  no round is left open the run ends as after Done on the page. A file it cannot check is not taken in: Signoff
  says so on stderr, keeps running and does not write over it.
- **Only the files it names:** pictures by number, never by path; each resolved inside the round's folder; no
  SVG, no directory listing. Attachments follow the same folder rule, `.md` or `.txt` only, and go inside the
  page as text; no route serves them.
- **Agent text is text:** everything reaches the page through `textContent`; a strict Content-Security-Policy
  with a per-request nonce; no inline styles or handlers.
- **Boards are sandboxed:** their own token, which opens nothing else; `Content-Security-Policy: sandbox
  allow-scripts`; an `<iframe sandbox="allow-scripts">` without `allow-same-origin`. A board cannot read the page,
  its storage or its token, and cannot save.

The full model, and what is in and out of scope: [SECURITY.md](../SECURITY.md). Report a vulnerability privately
there.
