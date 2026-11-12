# Signoff

Signoff gives a coding agent a local decision page. When the agent has several open decisions, it writes
them to a decisions file, with mockups or screenshots, highlight boxes, costs and its recommendation
preselected. Signoff opens that page in your browser. You choose an option for each decision, add a
comment where you want, and press Done. The page shows the project's name, and a Summary quotes your
comments before you finish. The agent reads your answers from a file next to the decisions and carries on.
Earlier rounds stay on the page, read only, and a past-decisions view lists every round in a
project, with a search over the answers. Ask the agent to look back at earlier rounds, and it opens that view.

## Where it works

Claude Code on your own computer only. It needs Python 3.9 or newer and a web browser on that computer.
It does not work on claude.ai on the web or on mobile, or in Cowork. No Python yet? On Windows, install the
Python install manager from python.org; on a Mac, run xcode-select --install or use python.org's installer; on
Linux, install your distribution's python3 package.

## Install

At the prompt of a Claude Code session in a terminal:

```
/plugin install signoff --marketplace lumi-collective/signoff
```

Then the status line shows one monitor: a small process that only sleeps until you use the Signoff inbox. The
rest of this page says in detail what the plugin runs, reads and writes.

## What it runs

The skill tells the agent to run Signoff's Python program in the background. The program starts a small
web server on 127.0.0.1 only, on a free port, with a secret token, new for each run, in every link. It opens
your default browser on a single-use start link. It stops when you press Done, when another program
finishes its answers files with Done, when it sits idle too long, or when you stop it. While the Signoff
inbox is open, it starts no server and opens no page: it waits, with no time limit, for the answers the inbox
writes. The plugin also adds a signoff command that runs the same program.

The plugin also has hooks: in every Claude Code session, when the session starts or ends, when you send a
prompt, when it asks for your permission or an answer, after each tool call and when a turn ends, Claude Code
runs a short shell script. Until the Signoff inbox has run on your
computer, the script only checks for the inbox's lock file and stops, writing nothing and starting no Python. On
a Mac with the Signoff app installed, a new session's start also keeps one link in the inbox's folder, pointing
to the plugin's signoff command, so the app finds the plugin; still no Python, and no link when the plugin runs
from a git checkout. On
Windows, where the inbox does not run yet, it stops at once. Once the inbox has run, it
starts that program for a moment when a session starts, and at the other times only while the inbox runs (its
lock file touched within the last minute); after a tool call, only while some session may also be waiting on
you. It never changes or blocks the session, and without Python it does
nothing. On Windows it runs in Git Bash; without Git Bash, Claude Code runs it in PowerShell, where it does
nothing (Claude Code then says once per session that a hook ran in PowerShell; you can ignore it). One
exception: when a session starts and no other session of that project is open, the hook hands the new session the messages you sent the project from the inbox that are not handled yet.

The plugin also has a monitor: in every interactive Claude Code session, Claude Code keeps one small background
process running, shown as one monitor in the status line. Until the inbox runs, it is a shell loop that only
sleeps and checks the inbox's lock file twice a minute; on Windows a loop that only sleeps. Once
the inbox runs, it is the Python program for the rest of the session, which looks every two seconds for new messages you sent this
project from the inbox and prints each one for the agent. Claude Code ends it with the session (checked on macOS).

The plugin also has a mod, where Claude Code runs plugin mods. It is code that runs inside Claude Code in
interactive sessions, not on Windows. Every five seconds it checks whether the inbox's lock file is there, and
starts no process for that. Once the inbox has run, the mod runs the signoff command once each time Claude Code
loads it, to learn the inbox's folder. From then on it reads three small files there through Claude Code: the lock
file's time (the inbox touches it every two seconds while it runs), the project notes the hooks keep, and the
inbox's count of what waits on you. That count covers open rounds, waiting todo items, and the project's sessions
waiting on a permission or an answer, this one included.

While the inbox runs and something waits on you in the session's project, a line above the prompt says how many,
with Open and Hide (hidden until the number changes). A notice comes once a session when the number rises from
none, and stays up for a second look.

Open and the /signoff:open command open a panel beside the chat. They run the signoff command's inbox rows once,
which asks the running inbox for this project's open rounds and their open questions (numbers and one-line titles;
it never starts the inbox). The panel lists them, with a row for all projects in the inbox (Open the inbox, when no
inbox answered). Each row runs the signoff command's inbox open, which starts the inbox when needed and opens it in
your browser. A question's row opens at that question, a round's row at its first open question (its last when all
are answered), and the row for all projects at the list. The mod never reads what that prints.

On macOS, when the optional Signoff app is in an Applications folder (the mod reads its Info.plist when the panel
opens), each round and question row also has an App button. It runs the signoff command's inbox open with --app,
which leaves that question with the running inbox, in memory only, and runs open -a with the path of the app copy
it checked in those folders. The app, as it comes up, asks the inbox for the question and opens it in its window.

The Waiting hint setting in /config moves the number to the status line, or turns the mod off. Then the mod reads
nothing but what the command reads when you still open the panel (the rows, the app's Info.plist). It writes
nothing.

The Signoff inbox runs only when you start it: ask your agent to open the Signoff inbox (or use the optional macOS
app, built from the repository and not part of the plugin), and until you stop it: one
Python program in the background, serving one page on 127.0.0.1 with the open questions of every project and
their todo items. It hands out its single-use start links only on a socket file readable by you alone; the optional app also asks
there for a notification's recommended answer, and its Accept & send saves that answer with Done. It does
not run on Windows yet. It never runs a command from a todo file; it only offers it to copy. On an open todo item
you can type a message to the project's agent; the agent gets it as your words. On one waiting on you, Later asks
the agent, in a fixed line the inbox writes, to take its waiting mark off and keep it open. An item and the items
indented under it show as one row with its steps counted, and an item that names a decisions round open in the
inbox links to it. When Uptick's vulnerability
scan is on, the inbox also shows each project's known vulnerabilities from Uptick's scan file, can hand a finding
to the project's agent, and offers Scan this project for a project Uptick does not scan yet.

## What it reads and writes

It reads the decisions files and the pictures, live mockups and text attachments they list, inside the topic
folder, and the palette and light or dark the inbox kept, when there is one.
It writes your answers next to them, as JSON and as Markdown, and a .gitignore in that folder if there is
none. An answer you mark Private stays in the JSON for the agent, which is asked not to repeat it; the
Markdown copy leaves it out. It is not encrypted. The past-decisions view reads the decisions folder you
set (by default `.signoff` in the project) and writes nothing. Once the inbox has run on
your computer, a run also writes an empty lock file beside each open round, and a short note of the run in the
inbox's folder: the topic folder, a hash of the project folder's path, the round file names, its process id and
when it started and ended. Once the inbox has run, the hooks also keep small files there: one per
project (its folder's path and name), one per session (its id, the project, the Claude Code process id
except on Windows, whether it is working, idle or ended, with the time, whether it runs in the background, and
since when it waits on you for a permission or an answer), and a flag that says whether any session may be
waiting. They never write your prompts, Claude's replies, Claude Code's messages, the tools' input or output, or
the transcript. The inbox reads each project's todo file (the first of
_bmad-output/todo.md, TODO.md and todo.md) and never writes it. It writes the answers you give there the way a
run does, and in its folder a lock file, a note of its port and socket (no token), a count of what waits on you (numbers and two-letter project marks,
no text), the palette, light or dark, density and list order you kept, the socket itself and a log. Each message you send on a todo item is a small file there (the item's
first line, your text, the time); the agent marks it handled with an empty file beside it. A finding you hand
over is the same kind of file (the package and version, and a line the inbox writes from the finding), and so is
Later (the item's first line and the inbox's fixed line). The inbox
reads Uptick's scan file (vulns.json in Uptick's cache folder) and Uptick's scan list (scan-repos.json in
Uptick's config folder). Only when you click Scan this project does it write that list: it adds the project's
folder path and keeps everything else in it. The monitor writes nothing.
Signoff deletes nothing, except a socket file a stopped inbox left in that folder.

## What leaves your machine

Nothing from Signoff itself. The page loads nothing from the internet, Signoff sends no telemetry, and
the server answers only on 127.0.0.1. A live mockup is the agent's own HTML in a sandboxed frame; its
script could still send data out over WebRTC, so the skill tells the agent to put nothing secret in one.
A project you add with Scan this project is scanned by Uptick, not Signoff: Uptick's next check sends the package
names and versions in its lockfiles to osv.dev.

Full documentation, the file format and source: https://github.com/lumi-collective/signoff
