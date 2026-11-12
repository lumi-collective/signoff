# Export formats: what each one keeps

Measured on the 40 sample notes (synthetic data).

## Markdown
- Keeps headings, lists, links and `code`.
- Loses note colours and the pinned flag.

## PDF
- Keeps the layout as printed; needs the print stylesheet.
- 3 of 40 notes break a table across pages.

## HTML
- Keeps everything, but a note with <b>raw tags</b> must be escaped,
  or it renders as bold. This box shows it as text.

[a link](javascript:alert(1)) stays text here, too.
