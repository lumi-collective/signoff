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
| `context` | top | no | one line on which session asks and what it was working on, up to 120 characters, no line break, tab or other control or invisible character (a longer or multi-line value fails to load); shown at the top of the round and on its inbox row. Never a prompt, a transcript, a secret or a path |
| `items` | top | yes | one or more decisions |
| `free_text` | top | no | `title` and `placeholder` of the box at the end; the box is always shown |
| `id` | item, option | yes | letters, digits, `-`, `_`, `.`; unique; the answers are keyed by it |
| `group` | item | no | a heading appears whenever it changes |
| `title`, `description`, `cost` | item | `title` | plain text; line breaks kept |
| `images` | item | no | pictures: `src` (inside the round's folder; PNG, JPEG, WebP or GIF), `caption`, `alt`, `boxes`. For one picture per option, list them in option order with `caption` = the option's label. An entry with `html` instead of `src` is a board |
| `boxes` | picture | no | `[x, y, w, h]` in percent of the picture, from the top left |
| `html` | board | no | one self-contained HTML file inside the folder, up to 2 MiB: inline CSS and JS, `data:` images; it reads `?theme=light` or `?theme=dark` from its URL |
| `select` | item | no | `one` (radio buttons, default), `many` (checkboxes) or `number` (a slider and a number field) |
| `options` | item | `one`, `many` | each: `id`, `label`, optional `detail`; at least two for `one`. Refused on a `number` item |
| `range` | item | `number` | a number item's values: `min` and `max` (`min` less than `max`, both from -10^15 to 10^15), optional `step` (more than 0, default 1; at most 10,000 steps from `min` to `max`, and both within 10^12 steps of 0) and `unit` (shown after the value, up to 16 characters, one line). Only on a `number` item, which takes no `other`, `marks` or `criteria` |
| `recommended` | item | `number` | an option id (`one`), a list of them (`many`), or a number on the range's step (`number`, required there; taken as its grid point, so `0.30000000000000004` on a step of 0.1 is 0.3); preselected and labelled |
| `comment` | item | no | a comment box, default `true` |
| `other`, `skippable`, `marks` | item | no | add **Other** with a text field, add **Skip**, let the user draw marks; each default `false` |
| `sensitive` | item | no | the answer may hold personal data: the item starts marked **Private**, which the user can untick; only on an item with a text field (`comment`, `other`, `marks` or `lines`); default `false` |
| `criteria` | item | no | what the options are compared on: a list of `id` and `label`, in row order. Adds the facts as chips on the cards and a **Cards / Table** toggle |
| `facts` | option | no | the option's value per criterion, keyed by criterion `id`: `text`, and optional `tone` `good`, `mid` or `bad` (shown as ▲, ● or ▼ with the text); only the item's own criteria; a missing one shows "–" in the table |
| `attachments` | item | no | text files shown under the options, each in a box that starts closed: `src` (inside the round's folder; `.md`, `.txt`, `.diff` or `.patch`, UTF-8, up to 256 KiB), optional `caption`, and optional `open` (`true`: the box starts open; default `false`); up to 20 an item. Read at start and shown as plain text, a `.diff` or `.patch` as a numbered unified diff (numbered plain text when it is not one); the Markdown copy names them, and quotes only a line the user noted |
| `lines` | item | no | let the user note lines of the item's attachments, each with a comment, recorded in the answer's `lines` (see [answers-file.md](answers-file.md)); only on an item with at least one attachment; default `false` |
| `commands` | item | no | commands for the user to run before deciding, 1 to 10: each `text`, optional `lang` (shown in the block's bar: letters, digits and `+#._-`, up to 20) and `caption` (a line above it). Shown under the description as wrapped code blocks with Copy; Signoff never runs them. A round fails to load when a `text` starts or ends with a space or line break (a trailing one runs on paste), has more than 4000 characters or 60 lines, or when any of the three holds a known invisible or blank character (see below) |
| `blocking` | item | no | your next step waits on this item: the page marks it **Agent waits on this** in its title and the sidebar, and the Summary gets an **Agent waits** filter; default `false`. Not in the answers file |
| `stakes` | item | no | how much rides on the decision: `low`, `medium` or `high`. The margin shows it with a glyph and a word (▲ **High stakes**, ● **Medium stakes**, ▼ **Low stakes**), and the sidebar marks a high one. In the Markdown copy, not in the answers file |
| `irreversible` | item | no | the decision cannot be undone, and why: one line of 1 to 200 characters, with no line break, tab or other control or invisible character, as for `context`. The margin shows **Cannot be undone** and the line, and the sidebar marks the item. Leave it out when it can be undone. A notification never offers it. In the Markdown copy, not in the answers file |
| `settled` | item | no | where and when the user decided this before, such as `round-01, 11-14`: one line as for `irreversible`, and only with `recommended`, which is that decision. The item starts folded: **Settled earlier**, the line and its choice, and **Change**, which shows the options. It counts as decided while its choice is `recommended`, so the inbox's panel and a notification do not wait on it; but a notification offers its round only once the item has a saved answer (the page saves every item). Never marked with `signoff elsewhere`. In the Markdown copy, not in the answers file |

Unknown keys are refused, so a typo shows up at once instead of being ignored. Text is plain: no Markdown, links
or HTML. `commands`, `blocking`, `stakes`, `irreversible`, `settled`, `select: "number"` with `range`, `context`, `lines`, an attachment's `open`, and a `.diff` or `.patch` attachment need Signoff 0.5.0 or newer; an
older one refuses the round, and so does an inbox an older one started, which keeps running after an update. A
run waiting on such an inbox (0.4.0 or later) notices within a few seconds, says on stderr to restart the inbox
(`signoff inbox stop`, then `signoff inbox open`) and run the same command again, and exits 1. With an inbox
from 0.3.0 it goes on waiting, so after updating from 0.3.0, restart the inbox first.

An invisible or blank character in a command is a control character other than tab and newline; a format, line
or paragraph separator, surrogate, private-use or unassigned one; one of Unicode's default-ignorable code points
(a bidi override, a zero-width mark, a variation selector, a Hangul filler, a tag); or U+2800 (the Braille blank)
or U+FFFC, which draw nothing. A `caption` may hold an emoji: U+FE0F and U+200D are allowed there. "Unassigned"
is as the Python running Signoff knows it, so an older Python refuses a character that a newer Unicode assigned.
These checks catch the known tricks, not every one: the block wraps and is capped, and the user should read a
command before running it.
