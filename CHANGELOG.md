# Changelog

## Unreleased

- **A project without git:** a folder that holds a `.signoff` folder is now a project root, as a folder with `.git` is, the nearer one winning. A session started in such a project's own folder was not noted, so the inbox showed no session row, band or todo for it, and a decisions folder outside its `.signoff` got no project name on the page. In a git repo with a `sub/.signoff/` folder, a session started in `sub` now belongs to `sub`, as the decisions under `sub/.signoff/` already did, not to the repo, and so does a decisions folder elsewhere under `sub`: its band counts `sub`'s rows, the inbox reads `sub`'s todo file, and messages on the repo's todo items no longer reach it. Likewise a `.signoff` folder in a parent folder without git (not the home folder) now takes in every folder below it that has no nearer `.git` or `.signoff`. A `.signoff` that is a file or a link marks nothing.

## 0.4.0

- **The band:** in a Claude Code that runs plugin mods (its tests run in Claude Code's plugin test kit on 2.1.309 in CI and on 2.1.322), the plugin's mod shows `Signoff: N waiting` above the prompt while the inbox runs. N is the number of rows of Waiting on you that belong to the session's project. The band has **Open** and **Hide** (hidden until the number changes), and a notice comes once a session when the number rises from none. The mod reads the inbox's `state.json` every five seconds and starts no process for that. The new `hint` setting (`band`, `status`, `off`) moves the number to the status line or turns the mod off. It does not run on Windows, where the inbox does not run yet. An older Claude Code runs the hooks as before and skips the mod.
- **The panel:** **Open** and the new `/signoff:open` open a side panel. It has one row for each open round and each undecided question of the project, and a row for all projects. A question's row opens that question in Focus in the browser. A round's row opens its first undecided question. The all-projects row opens the inbox's list, and so does a row whose round is gone, done, or from before an inbox restart. Digits press the first nine rows, `a` all projects, `q` closes. Where the pane does not fit, the list draws in the band. The panel starts no inbox. With none running (or one from 0.3.0) it says so, and its **Open the inbox** row starts one. With `claude -p`, `/signoff:open` runs `signoff inbox open` instead. The list comes from the new `signoff inbox rows <folder>` (numbers and one-line titles, no path, link or token of Signoff's). A row opens through `signoff inbox open --run <n> --focus <round>/<item> --inbox <k>`.
- **The App button (macOS):** with the Signoff app in `/Applications` or `~/Applications`, each round and question row also has an **App** button. It opens the same question in the app's window. `signoff inbox open --app …` keeps the target in the inbox's memory for a minute, to be used once, and opens the copy of the app it checked with `open -a`. There is no URL scheme and no link in any argument. The app takes the target when it comes up (its start, a Dock click, or `open -a` while it runs), never from the menu bar panel.
- **The app finds the plugin by itself:** with the app installed, a new session's start keeps `launcher` in the Signoff home, a link to the plugin's `signoff` (not for a plugin run from a git checkout). An app built with `SIGNOFF_LAUNCHER=link app/build.sh` runs that, so it needs no checkout and follows plugin updates once restarted; the default build still runs the checkout's command.
- **Homebrew:** `brew tap lumi-collective/signoff`, `brew trust --cask lumi-collective/signoff/signoff` (Homebrew 7 asks for it for a tap outside its own), then `brew install --cask lumi-collective/signoff/signoff` builds the app on your Mac from the release's source, in that link mode, and puts it in `/Applications`; `brew upgrade --cask signoff` builds the next release. No prebuilt or notarized app: the cask removes Homebrew's quarantine mark from the app it built.
- **Vulnerabilities follow Uptick's schedule:** with Uptick 0.4.0, whose `vulns.json` says how often it scans (`interval`), a project's scan counts as recent for two of its runs (at most 30 days), so one missed run is no gap. An older Uptick's file keeps the two days.
- **The inbox touches `inbox.lock`** every two seconds while it runs, so the mod can tell a live inbox from a stopped one.
- **A browser Signoff starts** gets `/dev/null` for stdout and stderr when they are not a terminal. On Linux it inherited them, which held a caller's pipe open.
- **An inbox closed and started again in one process** (the tests do this) no longer finds its control socket still listening on Linux.
- **Older installs:** an inbox started by 0.3.0 keeps the band empty, and the panel says to open the inbox, until you restart the inbox (`signoff inbox stop`, then open it again). The Signoff app from 0.3.0 or older only comes to the front on **App**, showing what it showed before. Install it with Homebrew, or rebuild it from this release (`app/build.sh`), to open the question. File formats are unchanged.

## 0.3.0

- **The inbox:** `signoff inbox serve|open|stop` runs one local page for every project: the open rounds of runs that wait in the background, and each project's todo items. A round opens on the usual page in the same tab; Done there writes the answers file and wakes the waiting run. While an inbox is open, a run opens no page and starts no server: it waits, with no idle timeout, and exits with the same codes and stdout once the answers are done. `signoff inbox home` prints the Signoff home (exit 1 when it would not be an absolute path). The inbox needs Unix sockets, so it does not run on Windows yet. Start links come only from its control socket, `inbox.sock` in the home: each works once, within two minutes, and opens its own page.
- **One writer per round:** once an inbox has run (`inbox.lock` in the home), a run locks each open round in an empty `round-NN.json.lock` beside it, so a second run on the same round exits 1, and notes itself in `runs/` in the home (no token or link). The `.gitignore` Signoff writes now also covers `*.json.lock`. A run also notices an answers file another program finished with `done: true`, and ends as after Done on the page; a file it cannot check is not taken in and never overwritten.
- **Waiting on you:** the list opens on what waits on you, under one heading per project, oldest first or newest first (kept as `sort` in `prefs.json`), each row saying how long ago it changed. Every other open item sits behind one closed count row per project. The tab title shows the count, as `(3) Inbox`, and `state.json` in the home holds the same count, with no text. The list reloads itself when the rows change and nothing can be lost; otherwise a bar offers Reload.
- **Layout and search:** a sidebar (views, then projects), a two-line list, and the open row beside it (a bottom sheet on a phone). `j`, `k`, Enter, `f` (Focus: one item at a time), Esc and `?` work, with a key bar. A search field in the header filters every view as you type, ignoring case and accents; `/` and Cmd+K (Ctrl+K) focus it. A code block or inline code span gets a Copy button, an item gets Copy text, and a delete command with a relative path gets a warning.
- **Todo items:** the inbox reads the first of `_bmad-output/todo.md`, `TODO.md` and `todo.md` under a project's root and never writes it. An item marked `- [ ] (waiting on you: <what>) text` waits on you. An item with steps under it is one row, with "n of m steps done". A row that names an open round (`<topic folder> / round-NN`) gets **Open round**.
- **Messages to the agent:** an opened todo item takes a one-line message, and **Later** asks the agent to park the item. Each is a file in `requests/<project>/` in the home. A session open in the project gets it within seconds from the plugin's monitor; with none open, the next session start hands it over, at most ten, with a line telling the agent to tell you first. The agent marks one handled with `signoff inbox done <id>`.
- **Vulnerabilities:** when [Uptick](https://github.com/lumi-collective/uptick) keeps its `vulns.json`, the inbox shows each project's findings (package, version, level, advisory ids, fixed version) with **Hand to agent**, and never counts them as waiting. Projects Uptick does not scan are one closed *Not scanned (N)* row, each with **Scan this project**, which on your click adds the project to Uptick's `scan-repos.json` and keeps everything else in it.
- **Live sessions:** a Claude Code session waiting on you for a permission or an answer (from about six seconds after it asks) is a row in Waiting on you and counts in `state.json`; a background session gives `claude attach <id>` to copy. Sessions idle for a minute or more are listed in a dimmer **Idle** group that counts nowhere. No prompt, message or tool input is stored.
- **Appearance:** Catppuccin is the default palette; Signoff's earlier colours, Rosé Pine, Gruvbox, Tokyo Night and Dracula are built in, each tested for contrast in light and dark, and Increase contrast or forced colours give one high-contrast set. The brush button or Cmd+, (Ctrl+,) opens a picker: arrows preview, Enter keeps, Esc goes back; it also switches Comfortable and Compact. The inbox keeps the choice in `prefs.json` in the home (`theme`, `palette`, `density`, `sort`) and every Signoff page opens in it. Palette credits are in `THIRD_PARTY_NOTICES`.
- **The round's page has a new face:** the rounds sit in a sidebar with their state, and the open round's items under them. The recommendation and the cost stand in a margin beside the question. A bottom bar shows the save state, what holds Done back and **Accept this round**; `f` opens Focus, which is the default at phone width. The Summary is a sign-off sheet, and the past-decisions page uses the same sidebar, read only. Formats, routes, saving and the Done gate are unchanged.
- **Hooks and monitor:** the plugin now ships hooks and a monitor, which run in every Claude Code session. Until an inbox has run, a hook is a short `sh` script that looks for `inbox.lock` and stops, and the monitor is a shell loop that sleeps (the status line shows "1 monitor"). Hooks always exit 0 and print nothing into the session, except the hand-over of messages at a session start. After a tool call, Python starts only while some session may wait on you. Neither writes anything until the inbox has run. Monitors do not run with `claude -p`.
- **The macOS app (optional, not part of the plugin):** `app/build.sh` builds Signoff, a small app (Go, Wails) around the inbox: its page in a window, the number waiting as a Dock badge, and a menu bar item with the same number whose click opens the inbox in a 420 by 600 panel. A notification comes when the number rises. When the one round left of a run has a single open question with a recommendation, and it is not marked sensitive, the notification shows the project, round, question and recommended answer, and **Accept & send** answers it with the recommendation and Done. **Other answer…** opens that question in Focus. Notifications work only from an Applications folder. It binds no code to its webview, and quitting it leaves the inbox running.
- **Answers from a notification:** the control socket's `offer` and `accept` save the recommendation with Done and `source: "notification"` in the answers file, through the same checks as the page; the client never sends a choice. The Markdown copy says "recommendation, accepted from a notification".
- **MCP server:** with the inbox open, `signoff_open` says the questions are in the inbox, and returns no link.
- **Skills:** every open decision, and every task that waits on you, now goes on one Signoff round; `AskUserQuestion` stays for a go or skip that blocks the next step and for remote or mobile sessions, and your own instructions win. The inbox's instructions moved to a `signoff-inbox` skill, loaded only while the inbox is in play, so the always-loaded skill is shorter. The skill tells the agent to hand you the link when no browser opened, what to do when Python is missing, to mark one todo item per chain, and to give delete commands absolute paths.
- **Start link and Python:** the page and stderr now say what to do after a second click on the single-use link (still 410, exit 1). The past-decisions page says when its server has stopped. A Python older than 3.9 prints one line naming the version Signoff needs. The README says how to get Python on each OS.
- **Files that are not files:** decisions, answers and outcome files are read without blocking and must be regular files, so a FIFO where one belongs no longer hangs a run, a save, the past-decisions page or the inbox's look.
- **Past decisions:** the rounds' own text (decisions, answers, outcome) takes up to 32 MiB of the page, counted as sent; the newest topics come first, and a round past that is listed with an error that says to narrow the list with `--since`.
- **Windows:** the bare `signoff` command in Git Bash uses the `py` launcher when there is one, and stderr is written as UTF-8 on every code page. The new hooks and monitor read the same in sh and in PowerShell: without Git Bash, Claude Code runs them in PowerShell, where they do nothing (checked by hand with PowerShell 7 and Windows PowerShell 5.1). The inbox does not run on Windows yet.
- **Icon:** the plugin's listing icon is the app's mark.
- **Docs:** the README is short; the reference (the page, the file formats, running it, the inbox and the app, security, the MCP server, troubleshooting, limits, development) moved to `docs/`.
- An outcome file with a lone surrogate escape is refused when read, as decisions and answers files are.
- **Older installs:** decisions files and answers files written by 0.2.0 load as before, and the decisions file format has no new keys. An answers file with `source: "notification"` is refused by a 0.2.0 install ("cannot resume from this answers file"), so update the plugin (`claude plugin update signoff@signoff`, then `/reload-plugins`) and do not move the file away. A 0.2.0 run knows nothing of the inbox: it takes no round lock and opens its own page. The `round-NN.json.lock` files are not rounds, so 0.2.0 ignores them; where a topic folder's own `.gitignore` does not cover them they show in git as untracked files.

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
