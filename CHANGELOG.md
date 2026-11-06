# Changelog

## 0.2.0

- **Summary:** each row quotes its comment, cut to three lines, with a **Full text** button. A filter shows **All**, **With comment** or **Not the recommendation**, each with its count. With one round open, the same list sits above a second **Done** at the end of the round; Done stays one click.
- **Private:** an item's optional `sensitive: true` starts it marked Private, and the user can tick or untick Private on any item with a comment, Other or marks. The answers file records `private: true` and keeps the text for the agent. The Markdown copy writes `(private)` in place of the comment, Other text and mark comments; the Summary shows "Private comment, hidden here." with a badge; the past-decisions search skips that text. The skill and the MCP server's `signoff_status` description tell the agent not to repeat it in a commit, its memory, a cloud service or a decision log. It is a request to the agent, not encryption.
- **Table view:** optional `criteria` on an item and `facts` on its options (`text`, and an optional `tone`, `good`, `mid` or `bad`, shown as ▲, ● or ▼ with a word for screen readers). Facts show as chips on the cards; a **Cards / Table** toggle lays the options side by side, a row per criterion, with one choice for both views. The view is not saved. On a phone the table scrolls sideways in its own box.
- **Attachments:** an item's optional `attachments` lists `.md` or `.txt` files inside the round's folder, by relative path, with an optional `caption`. They are read at start and shown as plain text in a box that starts closed; no route serves them. Up to 256 KiB each, 20 an item, 200 a round, and 2 MiB a round in the page, counted as sent (`<`, `>` and `&` take 6 bytes each); the past-decisions page holds up to 16 MiB of them. The Markdown copy names them only.
- **Paths:** the path of a picture, board or attachment is checked as text before it is resolved, on every OS. A control character, a leading `//` or `\\` (UNC, `\\?\`, `\\.\pipe`) or a drive without a root (`C:foo`) is refused. An absolute path inside the folder still works for pictures and boards. A path that cannot be resolved, such as a link loop, is an error that names the item.
- Pictures, boards and attachments are opened without blocking where the OS allows it, and must be regular files, so a FIFO cannot hang the start.
- The past-decisions page keeps going past a looping topic link and deeply nested JSON in a decisions, answers or outcome file; it lists that file with its error or warning.
- **Project name:** the header and the browser tab's title start with the project's folder name: the nearest folder with a `.git`, or the parent of a `.signoff` folder; never the home folder, never a path. The past-decisions page shows its root folder's name there.
- The past-decisions search also covers the answers: chosen options, comments, Other text, mark comments and the free text, never the text of a Private answer.
- The Markdown copy uses the round's `lang` for its labels (`en`, `hu`; any other language gets English) and leaves out an empty comment.
- The skill: when to open `--history`; a fact the user puts in a comment is recorded like one told in chat, never from a Private answer; a name or e-mail address goes into a log or commit only with the user's yes; decision log entries as decision · context · consequence; task lines start with the topic folder, the round and the item id.
- README: a Troubleshooting section. `plugin.json` says Signoff is local only. The repository moved to `github.com/lumi-collective/signoff`; `schema.json`'s `$id` follows.
- **Older installs:** decisions and answers files from 0.1.0 load in 0.2.0 as before. A 0.1.0 install refuses a round that uses `sensitive`, `criteria`, `facts` or `attachments` ("unknown key"), and an answers file with `private: true`, which 0.2.0 writes for any item the user marks Private ("cannot resume from this answers file"). Update the plugin (`claude plugin update signoff@signoff`, then `/reload-plugins`); do not move the answers file away.

## 0.1.0

First release.

- `plugin/signoff/signoff.py`: serves a decisions file, or a folder of `round-NN.json` rounds as tabs, on
  `127.0.0.1`; autosaves the answers beside each; on Done exits and prints one answers path per line. Resumes
  from existing answers files. Idle timeout (`--timeout`, default 120 minutes) with exit code 2; `--no-open`
  for SSH.
- Rounds are accepted one by one (**Accept this round**, Ctrl/Cmd+Enter), with a progress count, a ↺ mark when an
  accepted round changes, and a **Summary** tab; Done waits for every round, on the page and on the server. The
  answers file records `accepted`, `accepted_at` and `changed_after_accept`.
- Rounds: a round answered with Done is final and shows read only in later runs, so the user can look back. Done
  closes every open round at once. A broken round stops the run before anything is served, naming the file, the item
  and the field.
- The page: items with pictures and orange highlight boxes, costs, groups, single or multiple choice with the
  recommendation preselected and labelled, a comment per item, a free-text box at the end. Light and dark,
  keyboard accessible, phone width. Text and controls meet WCAG AA contrast in both themes, a Done button
  that is still waiting included.
- Security: a per-run token in the path, a single-use start link for the browser, exact `Host` check,
  JSON-only same-origin saves, size and content checks on every save, images served by index from an allowlist,
  no SVG, a nonce-based Content-Security-Policy.
- Per item, optional **Other** with a text field (`other: true`) and **Skip** (`skippable: true`), recorded as
  the choices `_other` and `_skip`.
- Keyboard shortcuts: `1` to `9` pick an option, `j` and `k` move between items, `?` lists them.
- The page's own labels in English and Hungarian, chosen by `lang`.
- Each answers file names its decisions file and that file's SHA-256.
- A second tab or window cannot overwrite newer answers: each save carries the round's save count, and an
  out-of-date save, acceptance or Done is refused with a note to reload; the reloaded page shows the newer
  answers. The server also checks the answers file is unchanged on disk, which catches a second run on the same
  folder on its next save (best effort across runs).
- Boards: an item's `images` entry may be `{"html": ...}`, the agent's own HTML shown live in a sandboxed frame
  (`sandbox allow-scripts`, under a token of its own, nothing loaded from outside) with light/dark and 1280/768/390 px switches.
- Marks: with `marks: true` the user draws rectangles on pictures, each with a comment, by pointer or keyboard;
  stored in the answers as `marks`.
- `--history <root>`: a read-only page of every past round in a project, with filters, outcomes
  (`<round>.outcome.json`), warnings when decisions changed after they were answered, `--folder`, `--since` and
  an image budget.
- A **Decisions folder** setting (default `.signoff`): where the skill puts topic folders and where the
  past-decisions page looks.
- `plugin/signoff/mcp_server.py`: an MCP server for other agents, with `signoff_open` and `signoff_status`; not
  registered by the plugin.
- A plugin icon.
- **Accept all recommendations** in the header, undoable; each answer records its `source` (`recommended`,
  `accept_all` or `user`).
- An optional `notice` in the decisions file, shown as a banner under the header.
- A Markdown copy of the answers, `<name>.answers.md`, written with every save, all text escaped.
- Writes a `.gitignore` for `*.answers.json` and `*.answers.md` into the topic folder when it has none.
- `plugin/signoff/schema.json`: the decisions file's JSON Schema.
- The `signoff` skill: when to use Signoff, where the decisions folder goes, how to batch rounds, one picture
  per option, how to run it in the background and act on the answers.
- `plugin/` is the whole plugin, with its own README and LICENSE; tests, examples and media stay outside it.
- A `signoff` command (`plugin/bin/signoff`, `signoff.cmd` on Windows) that runs the same program. The
  plugin's `bin/` folder keeps it to Claude Code on your own computer.
