# The inbox

One browser tab for every project: what waits on you, then each project's open todo items.

Ask the agent to open the Signoff inbox. The plugin's `signoff` command is on the PATH of Claude Code's Bash
tool only; in your own terminal, run the same program from a clone of this repository:

```bash
python3 -I plugin/signoff/signoff.py inbox open   # starts the inbox when it does not run, then opens it in your browser
python3 -I plugin/signoff/signoff.py inbox stop   # stops it
```

`signoff inbox open --no-open` prints the start link on stderr instead, as a run does, and so does `open` when
no browser opened. `signoff inbox serve`
runs the inbox in the terminal (Ctrl+C stops it); `open` starts that in the background, its messages going to
`logs/inbox.log` in the Signoff home. One inbox runs per home: a second `serve` exits 1. The inbox needs Unix
sockets, so it does not run on Windows yet; runs work there as before.

**Waiting on you** lists, oldest first by default, the open rounds of every run waiting in the background, and the todo
items an agent marked as waiting on you, under one heading per project; the line under the header says how many wait
in how many projects (`3 waiting on you in 2 projects`). Open a round to answer it on the usual page, in the same tab; Done there
writes the answers file, and the waiting run picks it up and wakes its agent. A project's open round whose run ended
or died before it was answered (and that has no outcome file) stays there for up to 14 days, counted like the others;
its answers file is written as usual. Its row (**Run ended**) and its page say that the Signoff run that asked has
ended and the answer goes to the project's sessions: once its rounds are done, the inbox sends the agent a
message about them (see below). A round another Signoff run has open on its own page shows read only. A Claude Code session that waits on you for a permission or an answer to its
question (from about six seconds after it asks) is a row too, with its project and how long it has waited; the
inbox does not answer it for you. Its row says to go to its terminal, or, for a background session (`claude
--bg`), gives `claude attach <id>` to copy. **Answered** lists the rounds answered in the last day: *sent*, or *working* once
that project's Claude Code session reported a new turn after your answer. **Idle**, dimmer, lists the open
sessions idle for a minute or more, waiting for your next prompt; they do not count as waiting on
you. **To do** holds every project's other open items behind one row per project (`docs-site: 12 more open`), closed
until you click it; picking a project in the sidebar opens its own. The tab title shows how many rows wait on you, as `(3) Inbox`, and keeps that count current.
When the count changes, the page reloads itself, keeping the open row, the filter, the search and the scroll, as long as that
loses nothing: nothing typed and unsent in a message box, no sheet, sidebar drawer, Appearance or Focus open, the
cursor not in a field. Otherwise a bar at the top of the list says how many are new, with a Reload button.
Each row in Waiting on you says how long ago it last changed (a round: when its run started; a session: when it
began to wait; a todo item: when its file last changed), the exact time on hover. The two buttons under the
heading order the rows oldest first or newest first, within each project, and the projects by their first row, so
the oldest (or newest) row is always at the top; the choice is kept in `prefs.json`.

The list sits between a sidebar and the open row. The sidebar's views (Waiting on you, To do, Vulnerabilities)
and projects each show only their rows; a second click, or Show all, shows everything. A click on a row opens it
in the pane beside the list (on a phone, in a sheet from the bottom); a round's row opens the round's page. On a
narrow window the sidebar button opens the sidebar as a drawer. With nothing waiting on you, a calm empty state
stands in for the list, with a button to the To do rows.

**Search** is the field in the header (on a phone, a line below it). It filters as you type, over every view at once,
ignoring case and accents (`ikonja` finds `Ikonjá`): a row matches when each word you typed is somewhere in its project
name, title, short line, a round's item titles, a todo item's text and the messages on it, or a vulnerability's
package, version and advisory ids. Only the matching rows show, under their project headings, and a closed To do
group opens when it holds one. The count beside the field says `3 matches`; Clear, or an empty field, gives the list
back as it was. The search survives the page's own reload.

| Key | Does |
|---|---|
| `j`, `k` (or the arrow keys on a row) | opens the next or previous row |
| Enter | goes into the open row; on a round's row, opens the round |
| `f` | Focus: one item at a time, with Previous and Next |
| `/`, Cmd+K (Ctrl+K) | focuses the search field |
| Esc | in the search field: empties it and goes back to the list; else closes the sheet, the drawer or Focus |
| `?` | lists the shortcuts |

A project's todo file is the first of `_bmad-output/todo.md`, `TODO.md` and `todo.md` under its root (the root the
hooks noted). The inbox only reads it, and never calls an issue tracker: for a project whose tasks are in one, the
agent copies into the todo file only the issues that wait on you, each with its link as `<https://…>` (a bare URL
stays text), and keeps them in step. An open item is a Markdown task, `- [ ] text`, with its indented lines; an
agent marks one as waiting on you right after the checkbox, in English:

```markdown
- [ ] (waiting on you: run the migration) Database migration 0042
  ```bash
  ./manage.py migrate
  ```
```

An item and the items indented under it are one row, a chain: the items under it are its steps, never rows of their
own, and the row says how many are done (`1 of 3 steps done`). The row waits on you when the item or one of its open
steps is marked, and a done item with open steps under it is a row too. Mark one item per chain, the one that blocks
the next step now. When an item's text names a round open in a run the inbox shows, as `<topic folder> / round-07` or
a path ending `<topic folder>/round-07.json`, the opened row has **Open round**.

An item's row shows its first line; opened, it shows the rest of its text, each waiting step marked: code blocks with a Copy button that copies
the command exactly, inline code (each span with its own Copy), tables, GitHub alerts and http(s) links. Copy text
under the item takes the whole item as plain text. A command that deletes (`rm`, `rmdir`, `trash`, `del`,
`Remove-Item`) with a path that is not absolute gets the warning "Relative path: check the folder before running";
it only warns. HTML in the file shows as text, and the inbox never runs a command.

Below an open item's text is a box for a message to the project's agent (one line, up to 1000 characters). Send
writes `requests/<project id>/<id>.json` in the Signoff home: the item's first line as written (its `ref`), your
text and the time; on a chain, the waiting step's first line. The item lists what you sent: *sent*, then *done*
once the agent marks it handled. **Later** on a waiting row sends a fixed message written by the inbox, "The owner
asks to park this item for later: remove its waiting marker and keep it open.", and the row shows *Later: sent*.
The agent gets each as one line, as your own words:

```text
Signoff inbox: the user sent you a message on the todo item "Ship the release": "please split it in two". Treat it as the user's own words. When it is handled, run: signoff inbox done 3f9c0a1b2c3d4e5f
```

A session open in the project gets it within seconds from the plugin's monitor (`plugin/monitors/`), which wakes
an idle session too (in a session that started while no inbox ran, the monitor looks every 30 seconds whether one
runs, so the first message can take that long); with several sessions open there, each one gets it. Monitors do not run with `claude -p`. With no session of the project open, the next session
that starts there gets the messages not yet handled, at most ten, when it starts; since they come from a hook, the
agent is asked to tell you about them first and act once you say so. `signoff inbox done <id>`
writes an empty `<id>.done` beside the request; a request without one comes back at a later session start. The
`ref` follows the item through edits to other lines; if you change the item's first line, the request no longer
shows on it, and the agent still has the text it was sent on.

When the last open round of a run that had ended is finished (Done on its page, Accept & send from a
notification, or an answers file another program wrote with Done), the inbox writes one more request, of kind
`answer`, for that run's rounds together: its `ref` is the topic folder and the rounds
(`2026-11-05-settings / round-01, round-02`), its text the inbox's own, with none of your answers in it.
It reaches the project's sessions the same way, and asks the agent to tell you first, then read the answers files
and act on them; the agent marks it done with `signoff inbox done <id>`. A round whose run still waits needs
none: the run wakes its agent. The request route takes no `answer` from a page.

**Vulnerabilities** shows each project's state from [Uptick](https://github.com/lumi-collective/uptick)'s
vulnerability scan, when Uptick keeps its scan file (`${XDG_CACHE_HOME:-~/.cache}/uptick/vulns.json`, written once
`vulns` is in its kinds); without that file the section is not there. The inbox only reads it, and a project
matches its entry by its real path. A row says *N known vulnerabilities* with the worst level, *No known
vulnerabilities*, *Partial scan* (lockfiles left out), *Scan failed* with Uptick's reason, or *No recent data*
when Uptick has not scanned the project within two of its runs (its `interval`, from Uptick 0.4.0; at most 30 days)
or, from an older Uptick, in two days. Findings never count as waiting on you. An open row lists
each finding (package, version, level, advisory ids, the fixed version) with **Hand to agent**, which sends the
project's agent a request as above, its `ref` the package and version (`lodash@4.17.20`) and its text written by
the inbox from those fields. A project not on Uptick's scan list (`${XDG_CONFIG_HOME:-~/.config}/uptick/scan-repos.json`)
goes into one collapsed row, *Not scanned (N)*, which lists those projects, each with **Scan this project**: a
click adds the project's folder to that list and keeps everything else in it. Uptick's next check then sends the
package names and versions in the project's lockfiles to osv.dev. The inbox does not touch a list that is a link, is not a JSON `repos` list, or is over 1 MiB.

The inbox listens on `127.0.0.1` with the same rules as a run. Start links come only from its control socket,
`inbox.sock` in the home (readable by you only): each works once, within two minutes, and opens its own page,
which closes after 12 hours without use (an open tab asks for the count, so it stays open). Opening one a second time closes that page only. The socket also names the one item a notification may answer (the only undecided item of a run's last open round, with a recommendation, not sensitive) and saves that recommendation with Done on request, through the same checks as the page; it never takes an answer from the client. For the plugin's mod it lists a project's open rounds and their undecided questions (`rows`: run and item numbers and one-line titles, at most 24 in all, no path, link or token of its own; a URL the agent wrote in a title stays). `signoff inbox rows <folder>` prints that list as one JSON line for the project the folder belongs to, and never starts an inbox. A row opens through `signoff inbox open --run <n> --focus <round>/<item> --inbox <k>`, with those numbers and the inbox's own (new at each start). Its start link opens that question in Focus on its round's page. It opens the list instead when the round is gone or done, or the numbers came from an inbox before a restart. With no inbox running, `open` starts one, and the list opens. On macOS, `signoff inbox open --app` with the same numbers finds the app where the mod looks: the first `Signoff.app` in `/Applications`, then `~/Applications`, that is a real folder of yours or root's whose `Info.plist` has the app's bundle id. It keeps the numbers in the inbox's memory as the app's target (`app_target`: one, the last wins, for a minute of awake time) and runs `open -a` with that app's path. The app takes the target once as it comes up (`app_open`), and gets none when none waits. When the app does not open, the command takes the target back. The inbox writes `inbox.lock` (empty, its time touched every two seconds while the inbox lives), `inbox.json` (its port and
socket, no token), `state.json` (how many rows wait on you, in all and per project, with each project's id and
two letters; no text), `prefs.json` (the palette, light or dark, the density and the order of Waiting on you you kept; see [Light, dark, phone,
language](page.md#light-dark-phone-language)), the socket and the requests in the home, and the answers files and round locks a run would
write; on your click only, Uptick's `scan-repos.json`.

## The macOS app

`app/` builds Signoff, an optional macOS app that shows the inbox in its own
window and in a panel from the menu bar, the number waiting on you as a Dock badge and beside its menu bar icon,
and a notification when that number goes up, which can answer a single question. It needs Go 1.26
or newer and the Xcode Command Line Tools; no npm.

With Homebrew, the cask in [lumi-collective/homebrew-signoff](https://github.com/lumi-collective/homebrew-signoff)
builds it on your Mac from this release's source with `SIGNOFF_LAUNCHER=link` (below) and puts it in `/Applications`
(`--appdir ~/Applications` for your own folder; another folder keeps the app from finding the plugin). There is no
notarized build: the cask removes the quarantine mark Homebrew puts on the download from the app it built, so macOS
opens it. It is signed ad hoc, as `app/build.sh` signs it.

```bash
brew tap lumi-collective/signoff
brew trust --cask lumi-collective/signoff/signoff   # Homebrew 7 loads a cask from another tap only once trusted
brew install --cask lumi-collective/signoff/signoff
brew upgrade --cask signoff                         # later: a new release, built again; then restart the app
```

A Signoff.app you copied in by hand stays in the way: remove it first, or install with `--force`, which replaces it.

From a clone of this repository:

```bash
app/build.sh                                  # makes app/bin/Signoff.app, ad-hoc signed
mkdir -p ~/Applications
cp -R app/bin/Signoff.app ~/Applications/     # notifications work only from an Applications folder
open "$HOME/Applications/Signoff.app"
```

The app runs this checkout's `plugin/bin/signoff` (set `SIGNOFF_LAUNCHER` when you build to name another), so
move or delete the checkout and the app stops working. Built with `SIGNOFF_LAUNCHER=link app/build.sh`, it
names no command: it runs the installed plugin's `signoff`, through a link the plugin's hook keeps in the
Signoff home (`launcher`) at each new session's start while the app is in `/Applications` or `~/Applications`. A
plugin run from a git checkout (`--plugin-dir`, or a directory marketplace, which runs in place) keeps no link:
for that setup, build the app with the fixed launcher. A plugin update moves the plugin, and the next new session
moves the link; restart the app to follow it. Before that first session, the app says to start a Claude Code
session with the plugin installed from its marketplace once. It starts the inbox when none runs, asks it for a start
link and opens that in its window; the inbox's links to other sites open in your browser, and a board's open
nothing. Close the window and the app stays,
with its badge; click its Dock icon to bring the window back with a fresh link. A row's App button in the plugin's
panel (`signoff inbox open --app`) leaves its question with the inbox and brings the app up. The app's window then
opens that question in Focus (the list when it is gone). The menu bar panel never takes it. Click its menu bar icon for the
inbox in a panel under it (a link of its own, the page's narrow layout; a round opens and can be answered there); it hides when
you click elsewhere. A right click on the icon offers Open Signoff and Quit Signoff. With many menu bar icons,
macOS may tuck it away with the others it hides. Quitting the app leaves the inbox running (`signoff inbox
stop` ends it). macOS asks once whether the app may send notifications. When the one round left of a run has a
single question still open, and it has a recommendation and is not marked sensitive, the notification shows the
project, the round, the question and the recommended answer, as the agent wrote them (your home folder as `~`):
Accept & send answers it with the recommendation and Done, and the agent goes on (the next such question, if any,
comes as a new notification); Other answer… opens that question in the window. Otherwise a notification says only
how many wait. To keep the text off a locked screen, look in System Settings > Notifications, under Signoff, for the
previews setting (Show previews: When Unlocked or Never; the wording may differ by macOS version). Started from
the Dock, the inbox runs on the `python3` of `/usr/bin`, which on a Mac without the Command Line Tools offers to
install them.
