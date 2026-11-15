# Running it yourself

```
python3 -I plugin/signoff/signoff.py <decisions.json | folder> [--no-open] [--timeout <minutes>] [--notes <folder>] [--port <n>]
python3 -I plugin/signoff/signoff.py --history <project root> [--folder <path>] [--since YYYY-MM-DD] [--no-open] [--timeout <minutes>] [--port <n>]
```

A folder serves every `round-NN.json` in it as rounds, in number order; a single file is one round. On Windows
write `py -3` for `python3`.

| Option | Does |
|---|---|
| `--no-open` | print the start link on stderr but do not open a browser. In an SSH session (`SSH_CONNECTION` or `SSH_TTY` set) with no `BROWSER` set, Signoff does this by itself; see [Over SSH or in a devcontainer](#over-ssh-or-in-a-devcontainer). The link works once: if it was spent, stop Signoff and run it again. |
| `--port <n>` | listen on this port of `127.0.0.1`, 1024 to 65535, instead of a random one; the default is `SIGNOFF_PORT` (empty: unset), else `0`, a random port. A port another program listens on exits 1 (best effort, see below). See [Over SSH or in a devcontainer](#over-ssh-or-in-a-devcontainer). |
| `--timeout <minutes>` | stop after this many minutes without a request from the page. Default 120; `0` means never. |
| `--notes <folder>` | at Done, also put each round's Markdown copy in this folder of yours; empty means off. See [Notes folder](#notes-folder). |
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

## An item settled in chat

```
python3 -I plugin/signoff/signoff.py elsewhere <round file> <item id> [<note>]
```

While a run waits, an item you and the agent settled in chat can be marked without a restart: the item gets
`source: "elsewhere"`, no choice, and the note (one line of at most 500 characters, with no control, invisible or
line-separator character; an emoji with its variation selector or joiner is fine) as its comment, and the round
must be accepted again, as after any change. An item that starts Private keeps Private. The page then shows the
item as **Decided elsewhere** with the note, keeps it through your other saves and **Accept all**, and makes it
yours again once you pick one of its options or type in it. It never marks an item that already holds your own
answer: a choice (unticking every recommended option too), a comment, Other text, marks, line notes, or Private
set otherwise than the item starts.

It writes the way the round's own writer does:

- No inbox has run on this machine (no `inbox.lock`, see below): there are no round locks, and it writes the
  answers file itself. It checks the file has not changed since it read it, once at the start of the write and
  again just before each of its two renames (the Markdown copy, then the JSON), and starts over when it has (three
  tries); a copy it renamed before a change landed is made again from the file that won. A save from the page between that
  last check and the rename is still lost, a Done too: the run then exits 0 while the file says `done: false`.
  The other way round, a save from the page that had already passed its own check can write over the mark after
  the command exited 0: if the mark is gone from the page, run the command again. This is the best effort every
  outside writer gets before the inbox.
- An inbox has run, and nothing holds the round's lock: it holds the lock for the write only. A page run started
  before the inbox first ran, or on a file system that cannot lock, holds no lock, so the window above stays open
  beside it.
- The inbox runs and holds the round's lock (a waiting run): it asks the inbox on its control socket, and the inbox
  writes the mark as it writes a save from its page. The inbox finds the round by its path as the run was given
  it: spell it the same, letter case too.
- A page run holds the round's lock (it started before the inbox, or no inbox runs now): exit 1, nothing written.
  Ask the user to answer on that page.

The run, or the inbox, shows the mark within a few seconds; a tab that saved before must reload. It prints nothing
on stdout and exits 0 once written (or when the item already has that mark and note), else 1 with one line on
stderr: an invalid round or answers file, an unknown item, a round already done, a bad note, your own answer on the
item, a page run holding the round, an inbox that refused it or did not answer, a lock file it cannot open, an
answers file that kept changing, or a write that failed. An inbox from before this command answers that it does
not know the request: the line says to restart it (`signoff inbox stop`, then `signoff inbox open`) and run the
command again. An inbox on an older Python may refuse a note character its Python does not know yet: the line
says to use plainer characters. Restart a run an older Signoff started
before you use it too: that run refuses the new `source`.

## Notes folder

The plugin's Notes folder setting (`/config`; empty by default) is passed as `--notes`: the skill has the agent add
it to the run command when it is an absolute path. Nothing binds the argument to the setting, so the checks below
keep a misled agent's note out of dot folders (such as `.claude`) and out of the rounds' own project; another
project's folder is not refused. A value that is still the setting's
`${user_config.…}` placeholder is off, as an empty one, with no message. When a run exits 0, after
its answers paths are on stdout, it writes one note for each round that was open when it started, from the round's
answers file: the [Markdown copy](answers-file.md), except that a private answer's item has only its title,
`(private)` and the agent's own stakes, cannot be undone and settled lines, and a line note names its attachment,
the line's number and the comment, never the line it quotes (the agent's file text). The round's free text (Anything else) always goes in: it has no Private mark. One round whose answers
file cannot be read or used gets one stderr line and no note; the others still get theirs. A waiter whose rounds were answered in the inbox writes them too; the inbox itself never does. A run
that exits 1, 2 or 130, a run killed after Done, and a round finished while no run waited on it (an orphan round, or
one another program finished) get no note. A later run shows done rounds read only and writes none for them.

The note is `<project> - <topic folder> - <round stem>.md`, such as
`signoff - 2026-11-14-release-050-scope - round-03.md` (outside a project: `<topic folder> - <round stem>.md`).
Every character but `A-Z`, `a-z`, `0-9`, space, `.`, `_` and `-` becomes `_`, a leading `.` too, and the name is
cut to 200 bytes before `.md`: the project and topic part is cut, never the round stem, so the rounds of a long
topic keep names of their own.

The folder must be an absolute path to a folder of your own, not a link (a link above it is fine; a trailing `/`
or `/.` does not hide one), with no part of its path or real path starting with a dot (`.claude`, `.config`,
`.git`), not writable by group or others, and not inside the rounds' project (the folder `.git` or `.signoff`
marks; outside a project, their topic folder) or the Signoff home. A vault in a git work tree of its own is fine.
The note is created there with mode
0600 and never written over: when a file (or a link) already has its name, it stays as it is and the round gets no
note. Nothing is renamed or removed. A note is created in one step and written at once, but not through a temporary
file, which would need a rename over the name or a removal: a write that fails midway can leave it incomplete.

Every refusal or failure is one stderr line starting `signoff: notes:`; the answers, stdout and the exit code stay as
they are. A Ctrl+C, SIGTERM or SIGHUP while the notes are written acts as the signal does, after the paths are
on stdout, so the exit code may then not be 0. On Windows `--notes` says it is not supported yet and writes nothing. Nothing goes over the network; a
folder another app syncs is that app's business.

Once the inbox has run on this machine, its lock file `inbox.lock` is in the Signoff home
(`~/Library/Application Support/signoff` on macOS, `${XDG_STATE_HOME:-~/.local/state}/signoff` on Linux (`XDG_STATE_HOME` only when absolute),
`%LOCALAPPDATA%\signoff` on Windows). A run never creates it, and without it nothing below happens. With it, a
run locks each open round (an empty `round-NN.json.lock`, or `<file name>.lock` for a single file, beside it),
so a second run on the same round exits 1, and notes itself in `runs/` in the home: the topic folder, a hash of the project
folder's path, the round file names, page or waiter, its process id, when it started and, unless it was
killed some other way or could not print its answers paths, when it ended and with which exit code. No token or link goes there. The lock files stay. A `.gitignore` that Signoff 0.3.0 or
later writes covers them; where the topic folder already had its own (one an older Signoff wrote too), they show up
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
file system that cannot lock, or an entry in `runs/` that could not be written leaves the run waiting, and so does
an inbox from 0.3.0, which does not touch `inbox.lock`.

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
clear or compaction notes it: the other hooks stop before Python, and the monitor of a session that has not seen
an inbox run sleeps in the shell, looking every 30 seconds. So when you open the inbox, a session's row may be out
of date (a wait that began or ended meanwhile) until that session's next prompt, tool call or turn end. A session
that ended meanwhile is never marked ended; it drops out because its process is gone. A session outside a
project (no `.git` or `.signoff` folder in its folder or above it, the home folder aside) is not noted. No prompt, reply, message, tool input or output,
transcript path or token is written. The files are readable by you only. `signoff inbox home` prints the home's path (exit 1 when it would
not be an absolute path, for example with a relative `LOCALAPPDATA`).
