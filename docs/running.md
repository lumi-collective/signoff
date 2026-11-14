# Running it yourself

```
python3 -I plugin/signoff/signoff.py <decisions.json | folder> [--no-open] [--timeout <minutes>] [--notes <folder>]
python3 -I plugin/signoff/signoff.py --history <project root> [--folder <path>] [--since YYYY-MM-DD] [--no-open] [--timeout <minutes>]
```

A folder serves every `round-NN.json` in it as rounds, in number order; a single file is one round. On Windows
write `py -3` for `python3`.

| Option | Does |
|---|---|
| `--no-open` | print the start link on stderr but do not open a browser. Over SSH, forward the same port number (`ssh -L <port>:127.0.0.1:<port> host`) and open the link locally; another local port gets 403. The link works once: if it was spent, stop Signoff and run it again. |
| `--timeout <minutes>` | stop after this many minutes without a request from the page. Default 120; `0` means never. |
| `--notes <folder>` | at Done, also put each round's Markdown copy in this folder of yours; empty means off. See [Notes folder](#notes-folder). |
| `--history <root>`, `--folder <path>`, `--since <date>` | the [past-decisions page](history.md#past-decisions) instead of a round. `--folder` is the decisions folder, relative to the root or absolute; default `.signoff`. |

| Exit code | Meaning | stdout |
|---|---|---|
| 0 | Done: `done` is `true` in every round that was open, from the page or from another program | each open round's answers file, one absolute path per line, in tab order |
| 1 | invalid input (stderr names the file, the item and the field), every round already done, a round open in another Signoff run (once the inbox has run), the start link used twice, or a Python older than 3.9 | nothing |
| 2 | the idle timeout passed; the answers so far are kept with `done: false` | the answers files written so far, one per line, or nothing |
| 130 | stopped with Ctrl+C, or with SIGTERM or SIGHUP on macOS and Linux (a signal ignored at the start, as under `nohup`, stays ignored); the answers so far are kept | nothing |

With `--history`, stdout stays empty: 2 after the idle timeout, 130 after Ctrl+C, 1 for a bad root or a start
link used twice.

## Notes folder

The plugin's Notes folder setting (`/config`; empty by default) is passed as `--notes`: the skill has the agent add
it to the run command when it is an absolute path. Nothing binds the argument to the setting, so the checks below
also keep a misled agent's note out of your tool and project folders. A value that is still the setting's
`${user_config.…}` placeholder is off, as an empty one, with no message. When a run exits 0, after
its answers paths are on stdout, it writes one note for each round that was open when it started, from the round's
answers file: the [Markdown copy](answers-file.md), except that a private answer's item has only its title and
`(private)`. The round's free text (Anything else) always goes in: it has no Private mark. One round whose answers
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
stdout.

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
