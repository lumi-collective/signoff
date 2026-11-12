# Running it yourself

```
python3 -I plugin/signoff/signoff.py <decisions.json | folder> [--no-open] [--timeout <minutes>]
python3 -I plugin/signoff/signoff.py --history <project root> [--folder <path>] [--since YYYY-MM-DD] [--no-open] [--timeout <minutes>]
```

A folder serves every `round-NN.json` in it as rounds, in number order; a single file is one round. On Windows
write `py -3` for `python3`.

| Option | Does |
|---|---|
| `--no-open` | print the start link on stderr but do not open a browser. Over SSH, forward the same port number (`ssh -L <port>:127.0.0.1:<port> host`) and open the link locally; another local port gets 403. The link works once: if it was spent, stop Signoff and run it again. |
| `--timeout <minutes>` | stop after this many minutes without a request from the page. Default 120; `0` means never. |
| `--history <root>`, `--folder <path>`, `--since <date>` | the [past-decisions page](history.md#past-decisions) instead of a round. `--folder` is the decisions folder, relative to the root or absolute; default `.signoff`. |

| Exit code | Meaning | stdout |
|---|---|---|
| 0 | Done: `done` is `true` in every round that was open, from the page or from another program | each open round's answers file, one absolute path per line, in tab order |
| 1 | invalid input (stderr names the file, the item and the field), every round already done, a round open in another Signoff run (once the inbox has run), the start link used twice, or a Python older than 3.9 | nothing |
| 2 | the idle timeout passed; the answers so far are kept with `done: false` | the answers files written so far, one per line, or nothing |
| 130 | stopped with Ctrl+C; the answers so far are kept | nothing |

With `--history`, stdout stays empty: 2 after the idle timeout, 130 after Ctrl+C, 1 for a bad root or a start
link used twice.

Once the inbox has run on this machine, its lock file `inbox.lock` is in the Signoff home
(`~/Library/Application Support/signoff` on macOS, `${XDG_STATE_HOME:-~/.local/state}/signoff` on Linux (`XDG_STATE_HOME` only when absolute),
`%LOCALAPPDATA%\signoff` on Windows). A run never creates it, and without it nothing below happens. With it, a
run locks each open round (an empty `round-NN.json.lock`, or `<file name>.lock` for a single file, beside it),
so a second run on the same round exits 1, and notes itself in `runs/` in the home: the topic folder, a hash of the project
folder's path, the round file names, page or waiter, its process id, when it started and, unless it was
killed, when it ended. No token or link goes there. The lock files stay. A `.gitignore` that Signoff 0.3.0 or
later writes covers them; where the topic folder already had its own (one an older Signoff wrote too), they show up
in git as untracked files until you add `*.json.lock` to it. While the
inbox is open, a run opens no page and starts no server: it
waits, with no idle timeout, until the answers the inbox writes are done, then exits with the same codes and
stdout.

With `inbox.lock` there, the plugin's hooks also note each Claude Code session in the home, so the inbox can show
whether a session is working, idle or waiting on you, from what the session itself reported. `projects/<id>.json`
holds a project's root folder and name (written once); `sessions/<session id>.json` holds the project's id, the
Claude Code process id (none on Windows), the state (`idle`, `working` or `ended`) and when it was set, `bg: true`
for a background session (`claude --bg`), and, while the session waits on you for a permission or an answer to
its question, `waiting: "permission"` and since when. The wait ends at your next prompt, when the tool has run
(so a long command you allowed still shows as waiting until it finishes), or when the turn or the session ends. `waiting.flag` in the home holds a byte while some session may wait and is
empty otherwise, so the hook at each tool call can stop before Python. A session outside a
project (no `.git` or `.signoff` folder in its folder or above it, the home folder aside) is not noted. No prompt, reply, message, tool input or output,
transcript path or token is written. The files are readable by you only. `signoff inbox home` prints the home's path (exit 1 when it would
not be an absolute path, for example with a relative `LOCALAPPDATA`).
