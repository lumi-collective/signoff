# Limits

- The page's own labels are in English and Hungarian.
- Text is plain: no Markdown, links or code blocks in descriptions.
- Attachments: up to 256 KiB each, 20 an item and 200 a round. A round's attachments together take up to 2 MiB of the page
  and the past-decisions page holds up to 16 MiB of them, both counted as sent, where `<`, `>` and `&` take
  6 bytes each; past that an attachment is not shown.
- Commands: up to 10 an item, each at most 4000 characters and 60 lines. A command's text may not start or end
  with a space or line break; its `lang` is up to 20 letters, digits or `+#._-`.
- A number item: one value, at most 10,000 steps from its lowest to its highest, both within 10^12 steps of 0, a
  unit of up to 16 characters; no range answer (from–to) and no logarithmic scale.
- A drive-relative or network path in a decisions file (`C:foo`, `\\host`, `//host`) is refused, on every OS.
  Attachments take only relative paths; a picture or board may still use an absolute path inside the folder.
- Pictures, boards and attachments are read at start; changing a round's files while the page is open needs a restart.
- The answers say what was chosen, not what you looked at: after a timeout, an untouched item shows the
  recommendation.
- Every round in a folder must be valid, done ones included: a broken earlier round stops the run. The
  past-decisions page is the lenient view.
- Marks are rectangles on pictures, not on boards.
