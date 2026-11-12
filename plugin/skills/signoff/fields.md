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
