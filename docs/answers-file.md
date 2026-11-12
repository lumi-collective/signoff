# The answers file

Beside each round, `round-NN.answers.json`, from the first change on the page:

```json
{
  "version": 1,
  "done": true,
  "saved": "2026-01-01T12:34:58Z",
  "accepted": true,
  "accepted_at": "2026-01-01T12:34:40Z",
  "changed_after_accept": false,
  "decisions": {"file": "round-01.json", "sha256": "9f2c…"},
  "answers": {
    "settings-layout": {"choice": "one", "comment": "not now", "source": "user",
                        "marks": [{"image": 0, "box": [49.4, 9, 6.6, 6], "comment": "Drop this button."}]},
    "filters": {"choice": ["date", "_other"], "other": "Colour", "comment": "", "source": "user"},
    "sidebar": {"choice": "_skip", "comment": "", "source": "user"},
    "theme": {"choice": "system", "comment": "", "source": "recommended"}
  },
  "free_text": ""
}
```

- `choice` is an option id or `null` for `one`, a list for `many`. Where it differs from `recommended`, you
  overruled the agent. `_other` means you picked Other and wrote `other`; `_skip` means you skipped the item.
  Neither can clash with an option id, which starts with a letter or digit.
- `source`: `recommended` (the preselected recommendation, never touched), `accept_all` (set by **Accept all
  recommendations**), `user`, or `notification` (the recommendation, accepted with Done through the inbox's
  control socket, as a tap on a notification asks).
- `marks`: your rectangles, `image` being the picture's position in the item's `images` (from 0) and `box` in
  percent like `boxes`.
- `private`: `true` when you marked the item Private. Its comment, Other text and mark comments stay in this
  file; the Markdown copy shows `(private)` in their place (`(privát)` in a Hungarian round).
- `accepted`, `accepted_at`: you accepted this round, and when; Done counts as accepting a single round.
  `changed_after_accept` is `true` while the round was changed after an earlier acceptance and not accepted
  again. The agent sees nothing of this until Done.
- `decisions` names the round file the answers belong to and its SHA-256 when they were saved.

Every save from the page also writes `round-NN.answers.md`, the same answers as Markdown for people, all text
escaped; an answers file another program wrote has no fresh copy. Its
labels follow the round's `lang` (`en` or `hu`; anything else gets English), and an empty comment leaves no
line. Agents read the JSON.

**Done is final:** a round whose answers say `done: true` is shown read only from then on and never written
again. If its round file has changed since (the SHA-256 no longer matches), Signoff refuses to start (exit code
1). An existing answers file that is not finished is resumed, so running the same command again after a timeout
or a closed terminal loses nothing.

## Keeping answers out of git

Comments can hold things that should not be committed. When a topic folder has no `.gitignore`, Signoff
writes one with `*.answers.json`, `*.answers.md` and `*.json.lock`; it never changes an existing one. On macOS
and Linux the answers files are readable by your user only. The decision files and pictures are not covered: in a shared
repository the skill has the agent check with `git check-ignore` and tell you when the folder would be
committed. The skill also tells the agent to move durable decisions into the project's own decision log, in its
own words, rather than committing raw answers.

A private answer is no safer on disk: its text is in the answers file in plain text, because the agent needs
it. What Private changes is what the agent is told: the skill forbids it to put that text into a commit, its
memory, a cloud service or a decision log, and the Markdown copy leaves it out.
