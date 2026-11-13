---
name: signoff
description: Put your open decisions on a local web page for the user - mockups, highlight boxes, costs, your recommendation preselected; the answers come back as a file. Use for open decisions and tasks that wait on the user, batched in one round. A go or skip that blocks your next step, a few short text questions, or a remote session, stays AskUserQuestion. The user's own instructions win.
---

# Signoff

Signoff turns JSON files of decisions into a page on `127.0.0.1`, its rounds listed in a sidebar. The user answers in the
browser; every change is saved to an answers file beside each decisions file. When the user presses **Done**,
Signoff exits and prints the answers files' absolute paths. You run it in the background, so the exit wakes
you. Rounds answered earlier stay on the page, read only, so the user can look back at them.

## When to use it

- **Signoff:** every other open decision, and every task that waits on the user (a command to run, a click in a
  GUI), goes on one round: collect them, one page per batch, not one per question. Put a picture on each option
  where you can.
- **AskUserQuestion:** a gate that blocks your next step (go or skip on a commit, a push, a publish); a remote or
  mobile session where no browser page can open; and a batch of at most `${user_config.chat}` short questions,
  each text only (no picture, board, attachment or marks) with 2 to 4 options. A bigger batch, or one with a
  picture, goes whole on the page. That limit is 0 to 4 (0: no batch in chat); any other value means 4.
- Nothing at all when the answer is obvious: do the obvious step and say so.
- The user's own instructions win: if they would rather answer in chat, or set another threshold, follow that.
- A todo item that waits on the user gets a marker while the Signoff inbox is on: the `signoff-inbox` skill says how.

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

- `id` (item and option): letters, digits, `-`, `_`, `.`; unique and stable. The answers are keyed by them.
- `select`: `"one"` (radio buttons, at least two options) or `"many"` (checkboxes). Default `"one"`.
- `recommended`: an option id for `"one"`, a list of option ids for `"many"`. It is preselected and labelled
  "Recommended". Always give one when you have a view, and say why in `description` or `cost`.
- **One picture per option** whenever the options look different: put one image per option in `images`, in
  option order, each with `caption` set to the option's `label`. Two or more visual options get one picture
  each, never a single picture for all.
- `sensitive: true` when the answer may hold personal data (health, money, family, someone else's details, the
  user's own setup). The item starts marked Private; the user can untick it, or tick Private on any item. It
  only acts on an item with a text field: `comment`, `other` or `marks`.
- `tab`, `other`, `skippable`, `comment`, `boxes`, boards, `marks`, `group`, `lang`, `notice`, `criteria`,
  `facts` and `attachments` are in [fields.md](fields.md) beside this file: read it before you use one.
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

A background command started by a subagent ends when that subagent ends, so the page would die with it. A
subagent may write the round files and hand back; the main session runs Signoff.

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

When the command ends, whatever its exit code, or fails to start, read [answers.md](answers.md) beside this
file before you act.

- `private: true`: the user marked the item Private. Use its comment, `other` text and mark comments to decide
  and act, but follow the private-text rule below.

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
  Put nothing secret in a board or an attachment either.
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
