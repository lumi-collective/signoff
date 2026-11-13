# When Signoff exits

## When it does not start

If `python3` (or `py`) is not found, or stderr says Signoff needs Python 3.9 or newer, do not retry: tell the user
once how to get it (Windows: the Python install manager from python.org, which brings `py`; macOS:
`xcode-select --install` or python.org; Linux: the `python3` package), and ask in chat meanwhile.

If any round is invalid, Signoff exits at once with code 1 and says on stderr which file, which item and which
field is wrong. Fix the file and run it again. It also exits 1 when every round in the folder is done already,
or when a round is open in another Signoff run: tell the user, and do not start a second one.

## 3. Read the answers

stdout holds one answers file path per line, one for each round that was open in this run, in tab order, such
as `<folder>/round-02.answers.json`. Read every line, not only the last:

```json
{
  "version": 1,
  "done": true,
  "saved": "2026-01-01T12:34:58Z",
  "answers": {
    "settings-layout": {"choice": "one", "comment": "not now", "source": "user"},
    "filters": {"choice": ["date", "_other"], "other": "Colour", "comment": "", "source": "user"},
    "sidebar": {"choice": "_skip", "comment": "", "source": "user"}
  },
  "free_text": ""
}
```

- Exit code 0: the user pressed Done, or another program finished the answers files with Done; `done` is
  `true` in every file listed.
- With several rounds open, the user accepts each one before Done (the page has a Summary, a sign-off sheet of every answer). You are still
  woken only once, at Done. Each answers file then has `accepted: true` and `accepted_at`;
  `changed_after_accept: true` only shows up in a file you read before Done, after a timeout.
- Exit code 1 after the page was open: the start link was used twice, and Signoff stopped because something
  else may have opened the page. Tell the user; running the same command again resumes with a new link. If
  an answers file already says `done: true`, the other visitor pressed Done: do not act on it; ask the user to
  check it and move it away first (give them the command), or the next run stops with "already done".
- Exit code 2: the page sat idle for the timeout (120 minutes; change it with `--timeout <minutes>`, `0`
  means never). stdout lists the rounds the user changed, with the answers so far saved as `done: false`;
  untouched items show your recommendation, which the user has not confirmed. If stdout is empty, nothing was
  answered. Either way, ask the user whether to reopen it; running the same command again
  resumes from the saved answers.
- After exit code 1 once the page was open, 2, or 130 (Ctrl+C), a project's rounds still open show in the
  user's Signoff inbox, whenever it runs, for up to 14 days. An answer given there comes to the project's
  sessions as a `Signoff inbox:` line on the Signoff rounds; the `signoff-inbox` skill says what to do with it.
- `choice` is an option id, `null` when nothing is picked (`"one"`), or a list (`"many"`). `_other` means the
  user picked Other and wrote `other`; `_skip` means the user skipped the item. Read an `other` text as the
  user's own answer.
- `source`: `user` when the user changed the item's options; `accept_all` when the user pressed **Accept all
  recommendations** and left the item; `recommended` when the preselected recommendation was never touched;
  `notification` when the user accepted your recommendation from a notification, which also sent Done. Treat `accept_all` and
  `notification` as the user's accepted choice, and `recommended` after Done as accepted without a second look.
- `marks` (only on items with `marks: true`): `[{"image": 0, "box": [x, y, w, h], "comment": "…"}]`, where
  `image` is the picture's position in the item's `images` from 0 and `box` is in percent like `boxes`. Each one
  is the user pointing at something; act on its comment as you would on the item's comment.
- `<folder>/round-NN.answers.md` holds the same answers as Markdown for people, as of the page's last save.
  Read the JSON, not the Markdown.
- Compare each choice with your `recommended`: where they differ, the user overruled you. Read every comment
  and the free text; they often change the scope more than the choices do.

## 4. Turn the answers into actions

- Summarise the outcome back to the user in a few lines: what is decided, where they overruled you, and
  anything in the comments you need to clarify.
- A new fact in a comment is a fact: say back that you took it in, and record it in the project's memory or
  task list, as you would a fact told in chat. Not from a Private answer: its text stays with you.
- Do the work each choice implies, or add it to the project's task list or tracker. Start the task line with
  the topic folder, the round and the item id (`2026-11-05-settings / round-02 / settings-layout`), so the
  answer is easy to find again.
- Move every durable decision into the project's own decision log (an ADR, a decisions file, the commit
  message), in your own words, as decision · context · consequence. Do not commit the raw answers files:
  Signoff writes a `.gitignore` with `*.answers.json`, `*.answers.md` and `*.json.lock` into the folder when the
  folder has none, and never overwrites an existing one.
- Then record the outcome beside the round, as `<round>.outcome.json` (for example `round-02.outcome.json`):
  `{"log": "<path of the decision log>", "line": "<the line you wrote there>", "date": "YYYY-MM-DD"}`. The
  past-decisions page shows that line with the round. Write it only after the decision is in the log.
