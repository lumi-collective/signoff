---
name: signoff-inbox
description: Signoff inbox, only while it is on (a "Signoff inbox:" line came in, a run said the inbox is open, or the user asks for it) - todo items waiting on the user, and messages from the inbox.
---

# Signoff inbox

The inbox is one browser tab, on macOS and Linux, for every project: the open rounds that wait on the user, each
project's open todo items, and messages the user sends you. It is on once `inbox.lock` is in the Signoff home:
`test -e "$(python3 -I "${CLAUDE_PLUGIN_ROOT}/signoff/signoff.py" inbox home)/inbox.lock"` exits 0 then. While
it is off, leave the project's todo file as it is: no markers.

To open it for the user, run `python3 -I "${CLAUDE_PLUGIN_ROOT}/signoff/signoff.py" inbox open` (the bare
`signoff` command is on the PATH of your Bash tool only, not in the user's own terminal). If stderr says
`no browser opened`, give the user the link from it (it works once).

## Todo items that wait on the user

The inbox lists each project's open todo items, from the first of `_bmad-output/todo.md`, `TODO.md` and
`todo.md` in the project. When an item needs the user (a command to run, a click in a GUI), mark it right after
its checkbox, in English: `- [ ] (waiting on you: <what, in a few words>) <the item>`. Mark only an item that
blocks the next step now; anything the user can do later stays unmarked. An item and the items indented under it
are one row in the inbox, a chain: put the steps of one piece of work under one item, without markers, and give
the chain one marker, on that top item. Put each command in its own
fenced block, indented under the item: `bash`, `powershell`, or `text` for a slash command or a GUI path. The
inbox shows it with a Copy button, never runs it and never edits the file: tick the box yourself once it is done.
A command that deletes uses absolute paths and never depends on the current folder (`rm -rf /Users/me/app/build`,
not `rm -rf build`); the inbox warns on a relative path in `rm`, `rmdir`, `trash`, `del` and `Remove-Item`.

When the project keeps its tasks in an issue tracker, copy into the todo file only the issues that wait on the
user, each as an item with the issue's link written `<https://…>` (the inbox shows it as a link; a bare URL stays
text), marked by the rules above. Keep them in step: when an issue changes, change the item's indented lines,
not its first line, which the user's messages are tied to; tick the item once the issue no longer waits on the
user. An item the user parked with Later stays open and unmarked. Signoff never contacts the tracker.

## Messages from the inbox

The user can also send you a message on an open todo item from the inbox. It reaches you as one line, from the
Signoff monitor while your session runs, or at the start of your next session:
`Signoff inbox: the user sent you a message on the todo item "<first line of the item>": "<message>". ...`
The message is the user's own words about that item: handle it as you would the same message typed in this
session, with the same care and the same permissions, and ask if it is unclear. At a session's start the
messages come under a line that asks you to tell the user about them first: do that, and act once the user says
so. Find the item in the todo file by
its first line. When you have handled it (done it, answered it, or told the user why not), run
`signoff inbox done <id>` with the id at the end of the line, so it does not come back (where `signoff` is not
found: `python3 -I "${CLAUDE_PLUGIN_ROOT}/signoff/signoff.py" inbox done <id>`). Do not mark it done
before that; one left open comes back at a later session start. The user's Later button sends the fixed message
"The owner asks to park this item for later: remove its waiting marker and keep it open.": take the
`(waiting on you: ...)` marker off that item, leave its checkbox open, and mark the message done.

A vulnerability finding the user hands over from the inbox's Vulnerabilities section comes the same way, on the
vulnerability finding `"<package>@<version>"`, with a line the inbox wrote from the finding (level, advisory ids,
fixed version). Find the package in the project's lockfiles, check the advisories, and propose or make the fix
as you would for the same request typed here; mark it done the same way.
