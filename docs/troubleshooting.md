# Troubleshooting

Signoff prints what went wrong on stderr, starting with `signoff:`. Its exit codes are in [the table](running.md#running-it-yourself).

- **`python3: command not found`, or "signoff: needs Python 3.9 or newer".** Install Python 3.9 or newer (see
  [Requirements](../README.md#requirements)). macOS and Linux need it as `python3`; the plugin's `signoff` command calls that
  name, and in Git Bash on Windows the `py` launcher when there is one. On Windows use the `py` launcher:
  `py -3 -I plugin/signoff/signoff.py <folder>`. The plugin's `signoff.cmd` does the same.
- **No browser opens.** Signoff prints the start link on stderr before it tries to open a browser, as
  `signoff: open http://127.0.0.1:<port>/b/…`, and says `no browser opened` when it found none. Open that link in a
  browser on the same machine, or see `--no-open` in [the options table](running.md#running-it-yourself).
- **Closed the tab?** Reopen it with your browser's Reopen closed tab (Ctrl+Shift+T, on a Mac Cmd+Shift+T). The
  start link works only once, so clicking it again stops Signoff (see 410 below).
- **Over SSH.** Use `--no-open`, as described in [the options table](running.md#running-it-yourself).
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
