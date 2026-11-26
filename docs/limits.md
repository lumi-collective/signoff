# Limits

- The page's own labels are in English and Hungarian.
- Text is plain: no Markdown, links or code blocks in descriptions.
- Attachments: up to 256 KiB each, 20 an item and 200 a round. A round's attachments together take up to 2 MiB of the page
  and the past-decisions page holds up to 16 MiB of them, both counted as sent, where `<`, `>` and `&` take
  6 bytes each; past that an attachment is not shown.
- Commands: up to 10 an item, each at most 4000 characters and 60 lines. A command's text must not start or end
  with a space or line break. Its `lang` is up to 20 letters, digits or `+#._-`.
- A number item: one value, from -10^15 to 10^15, at most 10,000 steps from its lowest to its highest, both within
  10^12 steps of 0, and a unit of up to 16 characters. There is no range answer (from–to) and no logarithmic scale.
- A drive-relative or network path in a decisions file (`C:foo`, `\\host`, `//host`) is refused, on every OS.
  Attachments take only relative paths; a picture or board may still use an absolute path inside the folder.
- Pictures, boards and attachments are read at start; changing a round's files while the page is open needs a restart.
- The answers say what was chosen, not what you looked at: after a timeout, an untouched item shows the
  recommendation.
- Every round in a folder must be valid, done ones included: a broken earlier round stops the run. The
  past-decisions page is the lenient view.
- Marks are rectangles on pictures, not on boards.

Plan review (how it works, and every case that gets Claude Code's own dialog: [Plan review](running.md#plan-review)):

- It takes only the main conversation's plan. A call that carries an agent id gets Claude Code's own dialog: a
  subagent's does, and so does a teammate's that runs inside the lead's session. A teammate in a split pane is a
  `claude` process of its own, so its plan can be taken over as that session's own. That was not tried.
- Whoever can write your answers or plan file, or get a start link of the inbox, can approve, and that approval
  ends plan mode with no dialog. In plan mode Claude Code by default leaves a Bash write to its auto mode
  classifier. See [the security model](security.md#what-runs-and-what-leaves-your-machine).
- Under an organization's managed guard that runs no `classic` hooks of a mod, Claude Code's dialog asks once more
  after an approval. How plan review behaves there was not measured.
- A Done that lands while the mod reloads (its waiting command gone, the next one not started yet) can reach the
  agent twice: as a message from the inbox and as the review's own message.
- A session that ends during a review leaves its round in the inbox. Your answer to it then reaches the project's
  sessions as a message from the inbox, and approves nothing.
- A `/rewind` does not end an approval that waits.
