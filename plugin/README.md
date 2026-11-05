# Signoff

Signoff gives a coding agent a local decision page. When the agent has several open decisions, it writes
them to a decisions file, with mockups or screenshots, highlight boxes, costs and its recommendation
preselected. Signoff opens that page in your browser. You choose an option for each decision, add a
comment where you want, and press Done. The page shows the project's name, and a Summary quotes your
comments before you finish. The agent reads your answers from a file next to the decisions and carries on.
Earlier rounds stay on the page as read-only tabs, and a past-decisions view lists every round in a
project, with a search over the answers. Ask the agent to look back at earlier rounds, and it opens that view.

## Where it works

Claude Code on your own computer only. It needs Python 3.9 or newer and a web browser on that computer.
It does not work on claude.ai on the web or on mobile, or in Cowork.

## What it runs

The skill tells the agent to run Signoff's Python program in the background. The program starts a small
web server on 127.0.0.1 only, on a free port, with a secret token, new for each run, in every link. It opens
your default browser on a single-use start link. It stops when you press Done, when it sits idle too long, or
when you stop it. The plugin also adds a signoff command that runs the same program.

## What it reads and writes

It reads the decisions files and the pictures, live mockups and text attachments they list, inside the topic
folder.
It writes your answers next to them, as JSON and as Markdown, and a .gitignore in that folder if there is
none. An answer you mark Private stays in the JSON for the agent, which is asked not to repeat it; the
Markdown copy leaves it out. It is not encrypted. The past-decisions view reads the decisions folder you
set (by default `.signoff` in the project) and writes nothing. Signoff deletes nothing.

## What leaves your machine

Nothing from Signoff itself. The page loads nothing from the internet, Signoff sends no telemetry, and
the server answers only on 127.0.0.1. A live mockup is the agent's own HTML in a sandboxed frame; its
script could still send data out over WebRTC, so the skill tells the agent to put nothing secret in one.

## Install

At the prompt of a Claude Code session in a terminal:

```
/plugin install signoff --marketplace lumi-collective/signoff
```

Full documentation, the file format and source: https://github.com/lumi-collective/signoff
