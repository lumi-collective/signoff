---
name: signoff
description: Put your open decisions on a local web page for the user - mockups, highlight boxes, costs, your recommendation preselected; the answers come back as a file. Use for open decisions and tasks that wait on the user, batched in one round. A go or skip that blocks your next step, or a remote session, stays AskUserQuestion. The user's own instructions win.
---

# Signoff

Signoff turns JSON files of decisions into a page on `127.0.0.1`, its rounds listed in a sidebar. The user answers in the
browser; every change is saved to an answers file beside each decisions file. When the user presses **Done**,
Signoff exits and prints the answers files' absolute paths. You run it in the background, so the exit wakes
you. Rounds answered earlier stay on the page, read only, so the user can look back at them.

## When to use it

- **Signoff:** every open decision, and every task that waits on the user (a command to run, a click in a GUI),
  goes on one round: collect them, one page per batch, not one per question. Put a picture on each option where
  you can.
- **AskUserQuestion:** only for a gate that blocks your next step (go or skip on a commit, a push, a publish), and in
  a remote or mobile session where no browser page can open.
- Nothing at all when the answer is obvious: do the obvious step and say so.
- The user's own instructions win: if they would rather answer in chat, or set another threshold, follow that.
- A todo item that waits on the user gets a marker while the Signoff inbox is on: the `signoff-inbox` skill says how.
- **`--history`:** when the user wants to look back at earlier rounds, open the read-only past-decisions page
  (see Past decisions).

## 1. Write the round

Keep one folder per topic, `<decisions folder>/<YYYY-MM-DD>-<slug>/`, with the date the topic started and a short
English slug. The decisions folder is `${user_config.folder}`, from the plugin's settings; when that is empty or not
a path, it is `.signoff`. It is relative to the project root unless it is an absolute path.

Each round of questions is one file in that folder: `round-01.json`, `round-02.json` and so on. Put every image
in the same folder or below it, shared by all rounds (for example `mockups/`). Use English file names. Images
must be PNG, JPEG, WebP or GIF; render an SVG or an HTML mockup to PNG first.

How to batch:

- Put independent rounds in the same run: they open on the same page, one after another in its sidebar. At most
  3 open rounds per run, and about 12 items per round; split anything larger.
- A round that depends on the answers to another is a new run, after the first one has finished, on the same
  folder with the next number. The finished rounds then show read only.
- Never edit a round that is done (its answers file says `done: true`); ask again in a new round.

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
      "select": "one",
      "options": [
        {"id": "two", "label": "Two columns"},
        {"id": "one", "label": "Keep one column", "detail": "no work"}
      ],
      "recommended": "two",
      "comment": true,
      "other": false,
      "skippable": false
    }
  ],
  "free_text": {"title": "Anything else", "placeholder": "Anything the list does not ask about"}
}
```

- `tab`: a short label for the round in the page's sidebar, one or two words. Default: the `title`.
- `id` (item and option): letters, digits, `-`, `_`, `.`; unique and stable. The answers are keyed by them.
- `select`: `"one"` (radio buttons, at least two options) or `"many"` (checkboxes). Default `"one"`.
- `recommended`: an option id for `"one"`, a list of option ids for `"many"`. It is preselected and labelled
  "Recommended". Always give one when you have a view, and say why in `description` or `cost`.
- `boxes`: `[x, y, w, h]` in percent of the image (0 to 100), measured from the top left. Each box is drawn
  in orange and the rest of the image is dimmed. Several boxes per image are fine. From pixels:
  x = left ÷ image width × 100, y = top ÷ image height × 100, w = box width ÷ image width × 100,
  h = box height ÷ image height × 100.
- **One picture per option** whenever the options look different: put one image per option in `images`, in
  option order, each with `caption` set to the option's `label`. Two or more visual options get one picture
  each, never a single picture for all.
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
- `sensitive: true` when the answer may hold personal data (health, money, family, someone else's details, the
  user's own setup). The item starts marked Private; the user can untick it, or tick Private on any item. It
  only acts on an item with a text field: `comment`, `other` or `marks`.
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
- All text is plain text. Line breaks are kept; HTML and Markdown are shown as typed.
- The full format is in `${CLAUDE_PLUGIN_ROOT}/signoff/schema.json`.

## 2. Run it in the background

Run this from the main session, with the Bash tool and `run_in_background: true`:

```
python3 -I "${CLAUDE_PLUGIN_ROOT}/signoff/signoff.py" "<folder>"
```

It serves every `round-NN.json` in the folder. On Windows write `py -3` for `python3`. Signoff opens the browser
itself, with a link that works once. Add `--no-open` when there is no browser on this machine (an SSH
session); the link is on stderr then, and the user forwards the same port number.

If `python3` (or `py`) is not found, or stderr says Signoff needs Python 3.9 or newer, do not retry: tell the user
once how to get it (Windows: the Python install manager from python.org, which brings `py`; macOS:
`xcode-select --install` or python.org; Linux: the `python3` package), and ask in chat meanwhile.

A background command started by a subagent ends when that subagent ends, so the page would die with it. A
subagent may write the round files and hand back; the main session runs Signoff.

If any round is invalid, Signoff exits at once with code 1 and says on stderr which file, which item and which
field is wrong. Fix the file and run it again. It also exits 1 when every round in the folder is done already,
or when a round is open in another Signoff run: tell the user, and do not start a second one.

If stderr says the inbox is open, no page opens: the questions wait in the user's Signoff inbox, with no idle
timeout, and you are woken the same way. The `signoff-inbox` skill covers the inbox.

In a repository other people share, when the decisions folder is inside it, check whether the folder would be
committed: `git check-ignore -q "<folder>"`. Exit 1 means it is not ignored: tell the user and suggest the line
`<decisions folder>/` for the project's `.gitignore`; do not add it yourself. Any other non-zero exit: skip this.

Read the task's output once: if it says `no browser opened`, give the user the link from it (it works once).
Then tell the user, in one or two lines, that the page is open and what it asks. Do not wait in a loop and do
not poll the answers file: keep working on what does not depend on the answers, or end your turn. You are
woken when Signoff exits.

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
- `private: true`: the user marked the item Private. Use its comment, `other` text and mark comments to decide
  and act, but follow the private-text rule below.
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

## Past decisions

When the user wants to look back at earlier rounds, or asks what was decided before, run this in the
background and tell the user the page is open:

```
python3 -I "${CLAUDE_PLUGIN_ROOT}/signoff/signoff.py" --history "<project root>" --folder "<decisions folder>"
```

It lists every round in the decisions folder, read only, with outcomes and warnings.
Add `--since YYYY-MM-DD` for a long history. It writes nothing and has no Done: the user closes it with Ctrl+C,
or it stops after the idle timeout, and stdout stays empty. To answer the question yourself, read the answers
and outcome files directly instead.

## Rules

- **Never delete anything**, including old decision folders and answers files. If something should go,
  tell the user and give them the exact command to run.
- **Sensitive data stays local.** The page and the answers never leave the machine. Do not copy a decisions
  file, its images or its answers into a cloud service, an issue, a pull request or a chat that leaves the
  machine. Screenshots of real systems may show secrets or personal data: crop them or use a mockup.
- **Private text stays with you.** The text of an answer with `private: true` (its comment, `other` text and
  mark comments) never goes into a commit, your memory, a cloud service or the decision log, and not into
  the outcome file's `line`. Record only the
  decision, in your own words, without the private details. Signoff does not enforce this: the answers file
  holds the text in plain JSON, and keeping it there is up to you.
- **Names stay out unless the user says yes.** A person's name or e-mail address goes into a decision log or a
  commit only after the user agrees, whether the item was Private or not.
- One topic per folder, one round per file. Follow-up questions go in the next `round-NN.json` of the same
  folder, never into a finished round.
- Keep the recommendation honest: preselecting it makes it easy to accept, so put the downside in `cost`.
