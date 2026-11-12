# The decisions file

JSON, one file per round. [`plugin/signoff/schema.json`](../plugin/signoff/schema.json) is its JSON Schema;
[`examples/demo/`](../examples/demo/) holds a complete single round (`decisions.json`) and four rounds
(`round-01.json` to `round-04.json`), with mockups and a board.

```json
{
  "version": 1,
  "title": "What goes into the settings redesign?",
  "tab": "Layout",
  "intro": "My recommendation is preselected on each item.",
  "notice": "Synthetic data only",
  "lang": "en",
  "items": [
    {
      "id": "settings-layout",
      "group": "Layout",
      "title": "Settings in two columns",
      "description": "The settings page is one long column today.",
      "cost": "About 60 lines of CSS.",
      "images": [
        {"src": "mockups/settings.png", "caption": "Proposed", "boxes": [[20, 10, 40, 50]]}
      ],
      "options": [
        {"id": "two", "label": "Two columns"},
        {"id": "one", "label": "Keep one column", "detail": "no work"}
      ],
      "recommended": "two",
      "other": true,
      "marks": true
    }
  ],
  "free_text": {"title": "Anything else", "placeholder": "Anything the list does not ask about"}
}
```

| Key | Where | Required | Meaning |
|---|---|---|---|
| `version` | top | yes | always `1` |
| `title`, `intro` | top | `title` | the page heading and the line under it |
| `tab` | top | no | a short label for the round's tab; default the title |
| `notice` | top | no | a short banner under the header |
| `lang` | top | no | language tag of your text, default `en`; the page's own labels are Hungarian for `hu`, English otherwise |
| `items` | top | yes | one or more decisions |
| `free_text` | top | no | `title` and `placeholder` of the box at the end; the box is always shown |
| `id` | item, option | yes | letters, digits, `-`, `_`, `.`; unique; the answers are keyed by it |
| `group` | item | no | a heading appears whenever it changes |
| `title`, `description`, `cost` | item | `title` | plain text; line breaks kept |
| `images` | item | no | pictures: `src` (inside the round's folder; PNG, JPEG, WebP or GIF), `caption`, `alt`, `boxes`. For one picture per option, list them in option order with `caption` = the option's label. An entry with `html` instead of `src` is a board |
| `boxes` | picture | no | `[x, y, w, h]` in percent of the picture, from the top left |
| `html` | board | no | one self-contained HTML file inside the folder, up to 2 MiB: inline CSS and JS, `data:` images; it reads `?theme=light` or `?theme=dark` from its URL |
| `select` | item | no | `one` (radio buttons, default) or `many` (checkboxes) |
| `options` | item | yes | each: `id`, `label`, optional `detail`; at least two for `one` |
| `recommended` | item | no | an option id (`one`) or a list of them (`many`); preselected and labelled |
| `comment` | item | no | a comment box, default `true` |
| `other`, `skippable`, `marks` | item | no | add **Other** with a text field, add **Skip**, let the user draw marks; each default `false` |
| `sensitive` | item | no | the answer may hold personal data: the item starts marked **Private**, which the user can untick; only on an item with a text field (`comment`, `other` or `marks`); default `false` |
| `criteria` | item | no | what the options are compared on: a list of `id` and `label`, in row order. Adds the facts as chips on the cards and a **Cards / Table** toggle |
| `facts` | option | no | the option's value per criterion, keyed by criterion `id`: `text`, and optional `tone` `good`, `mid` or `bad` (shown as ▲, ● or ▼ with the text); only the item's own criteria; a missing one shows "–" in the table |
| `attachments` | item | no | text files shown under the options, each in a box that starts closed: `src` (inside the round's folder; `.md` or `.txt`, UTF-8, up to 256 KiB) and optional `caption`; up to 20 an item. Read at start and shown as plain text; the Markdown copy names them only |

Unknown keys are refused, so a typo shows up at once instead of being ignored. Text is plain: no Markdown, links
or HTML.
