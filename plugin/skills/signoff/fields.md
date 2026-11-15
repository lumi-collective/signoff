# More fields for a round

- `tab`: a short label for the round in the page's sidebar, one or two words. Default: the `title`.
- `boxes`: `[x, y, w, h]` in percent of the image (0 to 100), measured from the top left. Each box is drawn
  in orange and the rest of the image is dimmed. Several boxes per image are fine. From pixels:
  x = left ÷ image width × 100, y = top ÷ image height × 100, w = box width ÷ image width × 100,
  h = box height ÷ image height × 100.
- `other: true` adds an **Other** choice with a text field; `skippable: true` adds **Skip**. Offer Other when
  your options may not cover what the user wants, and Skip when not answering is a valid answer.
- **A picture or a board.** Use a picture (`src`, a PNG) for a screen as it is or as it will look. Use a board
  (`{"html": "boards/x.html", "caption": "…"}` in `images`) when the user should try it: resize it, switch light
  and dark, click a toggle. A board is one self-contained HTML file inside the folder, up to 2 MiB: inline CSS
  and JS, images as `data:` URLs (it runs sandboxed and cannot load anything). Put nothing secret in it: a
  script in a board could still send data out over WebRTC. The page passes the
  theme as `?theme=light` or `?theme=dark`; read it with
  `new URLSearchParams(location.search).get("theme")`. The user can view it 1280, 768 or 390 pixels wide, so
  make it responsive. Boards take no `boxes`; highlight inside the HTML.
- `marks: true` lets the user draw rectangles on the item's pictures, each with a comment. Turn it on when
  "point at what you mean" is a likely answer, for example on a screenshot with several problems.
- `group`: a heading appears whenever it changes from the item before.
- `lang`: the language of your text, for example `"en"` or `"hu"`. The page's own labels follow it (English
  and Hungarian are built in; anything else gets English).
- `notice`: optional, a short banner under the header. Use it to say what the user must know before
  answering, for example that the mockups hold synthetic data only.
- `comment`: a comment box under the options; default `true`.
- `criteria` and `facts` when there is no picture to judge by but the options differ on several criteria
  (effort, risk, the way back, offline use): `"criteria": [{"id": "effort", "label": "Effort"}, …]` on the item,
  and on each option `"facts": {"effort": {"text": "M · 3 days", "tone": "mid"}, …}`. `tone` is `good`, `mid` or
  `bad`, how this value compares with the others; leave it out when there is no better or worse. The page shows
  the facts as chips on each card, and a Cards / Table toggle shows them side by side. Keep each text short. For a single
  criterion, `detail` is enough.
- `attachments` when the detail behind a choice is too long for `description`, such as a cut list or notes:
  `"attachments": [{"src": "notes/cut-list.md", "caption": "Cut list"}]`. Each is a `.md` or `.txt` file
  inside the folder, UTF-8, up to 256 KiB, at most 20 an item and 200 a round. A round's attachments together take up to
  2 MiB in the page, where `<`, `>` and `&` count 6 bytes each. The page shows it under the
  options in a box that starts closed, as plain text. The whole file goes on the page, and the past-decisions
  view shows it again later, so attach nothing secret. `sensitive` does not hide it: it is your text.
- `commands` when the user should run something before deciding (a check, a preview):
  `"commands": [{"text": "make docs && open build/index.html", "lang": "bash", "caption": "Build the docs"}]`,
  1 to 10 an item. The page shows each under the description as a wrapped code block with Copy (it copies `text`
  exactly) and a hint under one that deletes a relative path; Signoff never runs it. `text` keeps line breaks
  and tabs. The round fails to load when `text` starts or ends with a space or line break, has more than 4000
  characters or 60 lines, or when `text`, `caption` or `lang` holds a known invisible or blank character
  (another control character, a bidi override, a zero-width mark, a variation selector, the Braille blank; a
  `caption` may still hold an emoji). `lang` is a short word: up to 20 letters, digits or `+#._-`. The user
  should read a command before running it, so keep it short. A command to run **after Done** goes in a todo item
  instead, where the inbox shows it with Copy.
- `blocking: true` on an item your next step waits on. The page marks it ("Agent waits on this") and the Summary
  can show only those. Mark few, or the mark means nothing.
- `"select": "number"` when the answer is a number (a budget, a share, a count), not made-up buckets:
  `"range": {"min": 0, "max": 50, "step": 5, "unit": "%"}` and `"recommended": 20`, with no `options`. The page
  shows a slider and a number field. `min` is less than `max`, both from -10^15 to 10^15; `step` is more than 0
  (default 1), with at most 10,000 steps between them; `unit` is up to 16 characters, one line. `recommended`
  (a number) is required, in the range and on its step. `skippable` and `comment` work as on any item; `other`, `marks` and `criteria` do not. The
  answer is a JSON number (see [answers.md](answers.md)). Needs Signoff 0.5.0 or newer; an inbox an older one
  started refuses the round, and the user restarts it (see [answers.md](answers.md)).
- `context` (top level) when several sessions or projects may ask at once: one line saying which session asks
  and what it was working on, such as `"context": "feat/parser session: splitting the tokenizer"`. At most 120
  characters, no line break or tab; a longer value makes the round fail to load. The round's page shows it at
  the top and its inbox row after the tab. Never a prompt, a transcript, a secret or a path. Needs Signoff 0.5.0
  or newer; an inbox an older one started refuses the round, and the user restarts it.
- `stakes`: `"low"`, `"medium"` or `"high"`, how much rides on the decision. The margin shows it with a glyph
  and a word; the sidebar marks a high one. Give it where the items differ in weight. Needs Signoff 0.5.0 or newer.
- `irreversible` when the decision cannot be undone: one line saying why, such as `"irreversible": "Drops the old
  table"`, 1 to 200 characters, no line break or tab. The margin shows "Cannot be undone" and the line; the
  sidebar marks the item. Leave it out when it can be undone. Needs Signoff 0.5.0 or newer.
- `settled` when the user decided this in an earlier round: one line saying where and when (`"round-01, 11-14"`),
  1 to 200 characters, and `recommended` set to that decision (required with it). The item starts folded with
  that choice and a Change button, and counts as decided while it holds that choice. Never use `signoff
  elsewhere` on it. Needs Signoff 0.5.0 or newer.
