# Troubleshooting

Signoff prints what went wrong on stderr, starting with `signoff:`. Its exit codes are in [the table](running.md#running-it-yourself).

- **`python3: command not found`, or "signoff: needs Python 3.9 or newer".** Install Python 3.9 or newer (see
  [Requirements](../README.md#requirements)). macOS and Linux need it as `python3`; the plugin's `signoff` command calls that
  name, and in Git Bash on Windows the `py` launcher when there is one. On Windows use the `py` launcher:
  `py -3 -I plugin/signoff/signoff.py <folder>`. The plugin's `signoff.cmd` does the same.
- **No browser opens.** Signoff prints the start link on stderr before it tries to open a browser, as
  `signoff: open http://127.0.0.1:<port>/b/…`, and says `no browser opened` when it found none (and in an SSH session with no `BROWSER` set, where it tries none). Open that link in a
  browser on the same machine, or see `--no-open` in [the options table](running.md#running-it-yourself).
- **Closed the tab?** Reopen it with your browser's Reopen closed tab (Ctrl+Shift+T, on a Mac Cmd+Shift+T). The
  start link works only once, so clicking it again stops Signoff (see 410 below).
- **Over SSH or in a devcontainer.** Set `SIGNOFF_PORT` and forward that port, as described in
  [Over SSH or in a devcontainer](running.md#over-ssh-or-in-a-devcontainer). "wrong host" (403) means the page
  was opened on another port number or host name than the one Signoff listens on.
- **"port … Address already in use".** Another program, or another Signoff run, holds the port set with `--port`
  or `SIGNOFF_PORT`. Stop it, or set another port.
- **410, "This start link was already used".** The link was opened twice, so another program may have used it,
  and Signoff stopped with exit code 1. Ask the agent to open Signoff again: the same command gives a new link
  and keeps the saved answers. Check the answers files first: one that now says `done: true` was finished by the
  other visitor; move it away before you run it again.
- **"nothing to answer: … already done (Done is final)".** Every round in the folder has `done: true` in its
  answers file. A finished round is never reopened. Put the new questions in the next `round-NN.json`, or in
  another decisions file.
- **"Changed in another tab or window, so this tab can no longer save" (409).** Another tab or window of the
  same run, or a second run on the same folder, saved first. Copy any text you have not saved, reload, and go
  on. Run one Signoff per topic at a time.
- **A plan went to Claude Code's own dialog, not to Signoff.** Plan review needs a running inbox and the Plan
  review setting at inbox (it is off by default), in an interactive session (not `claude -p`) and not on Windows; it takes only the main
  conversation's plan, so a subagent's always gets the dialog. Ask the agent to open the Signoff inbox, or check the
  setting in `/config`. The other cases: [Plan review](running.md#plan-review).
- **"the Signoff review did not run, so the plan is not approved".** The command the mod starts for a review ended
  without an answer. One cause is an inbox started before you updated the plugin, which does not show the round:
  restart it (`signoff inbox stop`, then `signoff inbox open`). Until the inbox restarts or the mod loads again,
  plans get Claude Code's own dialog.
- **Claude Code asked once more after you approved in Signoff.** The mod answers only the one permission request it
  expects for that plan. The dialog asks when the mod was reloaded while the call waited, when a permission hook of
  yours or another mod blocked the request (a decision of theirs goes as they gave it), or under an organization's managed guard that runs no `classic`
  hooks of a mod (not measured). Answer it as you would without Signoff. The full list:
  [Plan review](running.md#plan-review).
