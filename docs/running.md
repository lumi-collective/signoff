# Running it yourself

```
python3 -I plugin/signoff/signoff.py <decisions.json | folder> [--no-open] [--timeout <minutes>] [--port <n>]
python3 -I plugin/signoff/signoff.py --history <project root> [--folder <path>] [--since YYYY-MM-DD] [--no-open] [--timeout <minutes>] [--port <n>]
```

A folder serves every `round-NN.json` in it as rounds, in number order; a single file is one round. On Windows
write `py -3` for `python3`.

| Option | Does |
|---|---|
| `--no-open` | print the start link on stderr but do not open a browser. In an SSH session (`SSH_CONNECTION` or `SSH_TTY` set) with no `BROWSER` set, Signoff does this by itself; see [Over SSH or in a devcontainer](#over-ssh-or-in-a-devcontainer). The link works once: if it was spent, stop Signoff and run it again. |
| `--port <n>` | listen on this port of `127.0.0.1`, 1024 to 65535, instead of a random one; the default is `SIGNOFF_PORT` (empty: unset), else `0`, a random port. A port another program listens on exits 1 (best effort, see below). See [Over SSH or in a devcontainer](#over-ssh-or-in-a-devcontainer). |
| `--timeout <minutes>` | stop after this many minutes without a request from the page. Default 120; `0` means never. |
| `--history <root>`, `--folder <path>`, `--since <date>` | the [past-decisions page](history.md#past-decisions) instead of a round. `--folder` is the decisions folder, relative to the root or absolute; default `.signoff`. |

| Exit code | Meaning | stdout |
|---|---|---|
| 0 | Done: `done` is `true` in every round that was open, from the page or from another program | each open round's answers file, one absolute path per line, in tab order |
| 1 | invalid input (stderr names the file, the item and the field), a bad `--port` or `SIGNOFF_PORT`, a port in use (stderr names it), every round already done, a round open in another Signoff run (once the inbox has run), the start link used twice, a running inbox that did not take the round in (see below), or a Python older than 3.9 | nothing |
| 2 | the idle timeout passed; the answers so far are kept with `done: false` | the answers files written so far, one per line, or nothing |
| 130 | stopped with Ctrl+C, or with SIGTERM or SIGHUP on macOS and Linux (a signal ignored at the start, as under `nohup`, stays ignored); the answers so far are kept | nothing |

With `--history`, stdout stays empty: 2 after the idle timeout, 130 after Ctrl+C, 1 for a bad root, a bad or
taken port, or a start link used twice.

## Over SSH or in a devcontainer

Signoff listens on `127.0.0.1` of the machine it runs on, never on another address. To open its page from your
own computer, forward that port. Each run picks a random port, so set one port once instead, in the shell
profile of the remote machine or the container:

```bash
export SIGNOFF_PORT=8765
```

Then every run, `--history`, and the inbox (`signoff inbox serve`, and the one `signoff inbox open` starts) listen
on that port (1024 to 65535); `--port <n>` on a run wins over it. `0`, or an empty `SIGNOFF_PORT`, is a random
port again. One server holds a port at a time. Before it binds, Signoff tries a connection to
`127.0.0.1:<port>`: when something answers there (a second run, `--history` while the inbox holds the port, a dev
server listening on every address), or the bind fails, it exits 1 with one line that names the port, and a
`serve` leaves no `inbox.lock`. This is best effort: a program that starts listening between that look and the
bind is not seen. While the inbox runs, a run opens no page and binds nothing, so a run then never takes the
port.

- **SSH:** forward the same port number, either for one session with
  `ssh -L 8765:127.0.0.1:8765 <host>`, or for good in `~/.ssh/config`:

  ```
  Host <host>
      LocalForward 8765 127.0.0.1:8765
  ```

  Another local port number gets 403. In an SSH session (`SSH_CONNECTION` or `SSH_TTY` set and not empty) with
  no `BROWSER` set, a run and `signoff inbox open` open no browser on the remote machine: they print the start
  link on stderr, as with `--no-open`, and you open it on your computer (they also say `no browser opened`, the
  agent's cue to give you the link).
- **VS Code devcontainer or Remote - SSH:** add the port in the Ports tab once (VS Code may also forward it by
  itself), and open the start link with that port on your computer. The local port number must be the same as
  the remote one: when VS Code picks another, because the first is taken, the page gets 403. VS Code's Remote -
  SSH terminal sets `BROWSER` to a helper that opens a link on your own computer, so there Signoff opens the
  browser as on a local run (not tried in a real Remote - SSH session).
- **Not covered:** a Docker `-p` mapping without host networking (Signoff listens on the container's own
  loopback, which `-p` does not reach), and GitHub Codespaces' `https://…github.dev` addresses (another host name,
  which gets 403).

Open the link as Signoff prints it, with `127.0.0.1`. The page also answers to `localhost:<port>`, for a forward
that opens it that way, but a browser may try `localhost` as `::1` (IPv6) first. Signoff never listens there,
and a forward may not either; another program on your computer listening there would get the link and its
token.

Known limit: over SSH the panel of the Claude Code mod runs `signoff inbox open` and never reads its stderr, so
a row there opens nothing you can see, as on any machine without a browser; ask the agent for the link instead.
A fixed port is a name any process of the machine can take first, and a page another program served on it can
leave code in your browser that sees later Signoff pages there: pick a port no other web app has used in that
browser, and see [the security model](security.md#security-model).

## Plan review

While the inbox runs, when Claude is about to leave plan mode, the plugin's mod holds the plan back and puts it on
a Plan review round in the inbox. The agent is told the plan waits and ends its turn; Claude Code shows that as a
refused ExitPlanMode. You approve it there, or ask for changes with a comment and line notes, and press Done. The
agent then gets a message that says it comes from the Signoff plugin: after an approval it calls ExitPlanMode again
with the same plan, the mod answers Claude Code's permission request for that call, and plan mode ends with no
second dialog, in the mode Claude Code picks: the one from before plan mode (default, accept edits and auto in the
terminal, auto in the desktop app; bypass permissions as that mode was not tried). After anything else it reads
your notes and revises. If you
stopped the agent with Esc meanwhile, a notice says your answer is saved: send any message and the agent gets it.

The mod answers only one permission request: for that exact plan, from the main conversation, while the call it
let through waits, and only when no hook of yours or of another mod beneath it decides or blocks. How it binds that
request is in [the security model](security.md#what-runs-and-what-leaves-your-machine). Any other request gets
Claude Code's own dialog, as does that call after a reload of the mod while it waits (an approval itself is kept in
Claude Code's session state and survives a reload, except the reload that turning the Plan review setting off
causes), and under an organization's managed guard that runs no `classic` hooks of a mod (Claude Code's dialog then
asks once more; this was not measured). After a `/cd` into
another project the same plan is a new review there (Claude Code's own dialog where the inbox knows no project),
and when the round's plan file no longer holds the plan, the
answer is no approval: the agent is told it could not be read, and a new review follows. A rejection in Claude
Code's own dialog (not an Esc there), a prompt you send outside plan mode, or turning the Plan review setting off
ends an approval that waits; a `/rewind` does not. An approval waits as long as plan mode does: to take it back,
leave plan mode and send a message, or use `/clear`.

Whoever can write your answers or plan file, or get a start link of the inbox, can approve, and that approval ends
plan mode with no dialog; in plan mode Claude Code by default leaves a Bash write to its auto mode classifier. The
details: [the security model](security.md#what-runs-and-what-leaves-your-machine).

A plan you approve in Claude Code's own dialog instead (the inbox had stopped, say) ends the review. So do
`/clear`, `/resume`, quitting or restarting Claude Code, and turning the Plan review setting off (which reloads the
mod): the round then stays in the inbox and no longer
approves anything. When you answer it
then, the answer reaches the project's sessions as a message from the inbox and approves nothing. An approval is
good for one call: rejected in Claude Code's own dialog, or interrupted, it is used up. A review that could not
run says so to the agent, and plans then get Claude Code's own dialog until the mod loads again or the inbox
restarts. Claude Code's own dialog is also what you get with no inbox, in a folder that is no project, for a
subagent's plan, for a plan over 256 KiB, with `claude -p`, on Windows, when the session's folder or the decisions
folder setting holds an invisible format or control character (a zero-width non-joiner, as in some Persian
names), and with the Plan review setting in `/config` set to off (the default is inbox).

The command the mod runs for it:

```
signoff plan --topic <YYYY-MM-DD-plan-id> --folder <decisions folder> [--resume] < plan.md
```

The plugin's mod runs this when Claude Code leaves plan mode, with the plan on stdin; the agent does not. It
needs a running inbox. It takes the plan as UTF-8, cuts the white space at its end (the same characters
JavaScript's `trimEnd()` cuts), and needs 1 byte to 256 KiB of it. The folder is absolute, or relative to the
project the current folder is in. Each value is the word after its flag, as the mod passes them; a
`--topic=<name>` form is not taken (it reaches the run's own arguments and fails there).

In `<folder>/<topic>/` it closes every older open round (no outcome file, no answers file that says done) with
`round-NN.outcome.json` ("replaced by round-MM"), then writes `plan-MM.md` (the plan's bytes) and `round-MM.json`, a
fixed round titled Plan review: one question, **Approve the plan** or **Ask for changes**, a comment and notes on
the plan's lines, the plan attached and open, and no recommendation. MM comes
after every file of a round's number there (answers, Markdown copies, outcome files, locks), so an answers file
left behind, as `git clean -fd` keeps the ignored ones, never answers a new round. Each file is created new, never
over one and never through a link. It then waits on that round as any run waits beside the inbox, and the run puts
the `.gitignore` in the topic folder. With `--resume`, only the newest round is looked at: it is reused when it is
the fixed round as written for its number, its `plan-NN.md` holds the same bytes, neither is a link, and its
answers load as a run loads them. A done one prints its answers path at once, an open one is waited on; anything
else gets a new round.

It exits 0 with the answers path on stdout once the round is done, and 130 on Ctrl+C. It exits 1 with one line on
stderr and nothing written for a usage error, a plan that is not UTF-8, empty or too big, a bad topic, a relative
folder outside any project, a topic folder that is a link or a file, no running inbox, or an older open round of
the topic that another live run still waits on. When the running inbox is older than this Signoff and does not
take the round in, it closes the round ("not shown: the inbox is older than this plugin; restart it") and exits 1
with a line that says to restart the inbox. When it cannot make the topic folder (a read-only decisions folder,
say) or open it as a folder of its own, it exits 1 and writes no file, but a folder it made on the way can stay
behind, empty. When another program creates a file at the new round's name in the
moment between its look and its write, it exits 1; the older rounds are closed by then, and `plan-MM.md` may be
there.

## Once the inbox has run

Once the inbox has run on this machine, its lock file `inbox.lock` is in the Signoff home
(`~/Library/Application Support/signoff` on macOS, `${XDG_STATE_HOME:-~/.local/state}/signoff` on Linux (`XDG_STATE_HOME` only when absolute),
`%LOCALAPPDATA%\signoff` on Windows). A run never creates it, and without it nothing below happens. With it, a
run locks each open round (an empty `round-NN.json.lock`, or `<file name>.lock` for a single file, beside it),
so a second run on the same round exits 1, and notes itself in `runs/` in the home: the topic folder, a hash of the project
folder's path, the round file names, page or waiter, its process id, when it started and, unless it was
killed some other way or could not print its answers paths, when it ended and with which exit code. No token or link goes there. The lock files stay. A `.gitignore` that Signoff
writes covers them; where the topic folder already had its own (one an older Signoff wrote too), they show up
in git as untracked files until you add `*.json.lock` to it. While the
inbox is open, a run opens no page and starts no server: it
waits, with no idle timeout, until the answers the inbox writes are done, then exits with the same codes and
stdout. It reads its pictures and boards at start, as a page run does, so a bad one fails there with its own error.
Once the same inbox has looked at the runs twice since the run started (it does every two seconds), the run
checks that some process holds its rounds' locks: the inbox locks each round it takes in, unless another run holds
it already (the inbox then shows it read only) or the file system cannot lock. When no one holds any, the inbox did
not take them in, as an inbox an older Signoff started (it keeps running after an update) refuses a round with a
key it does not know: the run says so on stderr, to restart the inbox (`signoff inbox stop`, then `signoff inbox
open`) and run the same command again, and exits 1. An inbox that does not look, a round lock another run holds, a
file system that cannot lock, or an entry in `runs/` that could not be written leaves the run waiting.

With `inbox.lock` there, the plugin's hooks also note each Claude Code session in the home, so the inbox can show
whether a session is working, idle or waiting on you, from what the session itself reported. `projects/<id>.json`
holds a project's root folder and name (written once; an optional `todo` key, which you add and the hooks never
write, names the project's todo file, see [the inbox](inbox.md#the-inbox)); `sessions/<session id>.json` holds the project's id, the
Claude Code process id (none on Windows), the state (`idle`, `working` or `ended`) and when it was set, `bg: true`
for a background session (`claude --bg`), and, while the session waits on you for a permission or an answer to
its question, `waiting: "permission"` and since when. The wait ends at your next prompt, when the tool has run
(so a long command you allowed still shows as waiting until it finishes), or when the turn or the session ends. `waiting.flag` in the home holds a byte while some session may wait and is
empty otherwise, so the hook at each tool call can stop before Python. While no inbox runs (it touches
`inbox.lock` every two seconds; a lock a minute old or more counts as stopped), only a session's start, resume,
clear or compaction notes it: the other hooks stop before Python. So when you open the inbox, a session's row may be out
of date (a wait that began or ended meanwhile) until that session's next prompt, tool call or turn end. A session
that ended meanwhile is never marked ended; it drops out because its process is gone. A session outside a
project (no `.git` or `.signoff` folder in its folder or above it, the home folder aside) is not noted. No prompt, reply, message, tool input or output,
transcript path or token is written. The files are readable by you only. `signoff inbox home` prints the home's path (exit 1 when it would
not be an absolute path, for example with a relative `LOCALAPPDATA`).
