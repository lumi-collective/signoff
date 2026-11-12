# Customize it with Claude

Signoff is three files that matter, `plugin/signoff/signoff.py`, `plugin/signoff/page.html` and the skill. Fork it,
then:

```
git clone https://github.com/<you>/signoff && cd signoff
claude --plugin-dir plugin
```

The session loads your copy. `AGENTS.md` lists the rules the code keeps. A prompt to start from:

> Read AGENTS.md. Add an optional `deadline` text to the decisions file, shown in the page header. Add it to
> schema.json and the table in docs/decisions-file.md, write the failing tests first, then run them on Python 3.9.

# Development and tests

```
claude plugin validate plugin
claude plugin validate .claude-plugin/marketplace.json
python3 -I -B -m unittest discover -s tests
```

`plugin/` is the plugin: what an install gets. The tests, examples, media and the macOS app stay outside it.
The app has Go tests of its own (macOS):

```
cd app && go vet ./... && go test ./...
```

The tests use the standard library only. They start real servers on `127.0.0.1` and run the command line and
the MCP server as real processes. CI runs them on Linux, macOS and Windows with Python 3.9 and 3.13, and
validates the plugin on the pinned Claude Code.

`tests/e2e_browser.py` is a separate check that needs Chrome (set `CHROME` to its path if it is not found); the
unit-test discovery does not pick it up. It opens the start link in headless Chrome over the DevTools protocol, drives
the page with real mouse clicks, and checks the exit code, stdout and the answers files. The cases: every round
in `examples/` (its items, options, recommendations and answers, light and dark); a phone opening in Focus; one
round (the comment quoted above Done, the filter, Private, one-click Done); the project name in the header and the
tab title; the `--history` search; the table view; an attachment; and two rounds (the sidebar, the margin
recommendation, Focus, a non-recommended pick, both accepted, the Summary's filter and sign-off sheet, Done, exit 0,
exactly the two answers paths on stdout). CI runs it on Linux and Windows.
That proves the server and the page together in headless Chrome on those systems. It does not prove a real Claude
Code session, and it does not run on macOS in CI.

```
python3 -I -B tests/e2e_browser.py
```

Everything in `media/` is synthetic. The screenshots come from `examples/demo/` and `examples/history/` in
headless Chrome, en-US, UTC. The two demos are made from the repository root:

```
python3 media/demo/record_page.py      # media/demo-page.gif: the real page, driven over the DevTools protocol
vhs media/demo-agent.tape              # media/demo-agent.gif: needs vhs; media/demo/setup.sh builds its folder
```
