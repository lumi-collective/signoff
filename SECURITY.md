# Security

Report a vulnerability privately through GitHub: the repository's **Security** tab → **Report a vulnerability**. Please do not open a public issue.

In scope: anything that lets another web page, another local program without the token, or the content of a decisions file do more than the [security model](docs/security.md#security-model) allows. For example: reading the page, an image or the answers without the token; writing the answers from another origin; reading a file the decisions file does not list, or one outside its folder; running script through text or an image in a decisions file; Signoff sending anything off the machine.

In scope too: a board (the agent's HTML in its sandboxed frame) that reads the page, its token or storage, sends a save, or reaches any route other than boards; anything the past-decisions page writes, or a file it serves that its rounds do not name; an MCP tool result that carries a token or a start link. A board that sends data out over WebRTC is a known limit, documented in [docs/security.md](docs/security.md): boards must hold nothing secret.

In scope too: a way to reach the page through the start link (`/b/<start token>/`) after it was used once, or to use it twice without Signoff stopping.

Out of scope: a person or program that already has the page URL with its main token while the run is open, and anything that already runs as your user on your machine. The start link is what other programs can see in the browser's command line; it works once. A program that reads it and uses it before your browser does can see the page and save answers until your browser arrives; then the run stops with exit code 1 and a warning. A Done sent in the first 5 seconds after the link's first use ends the run only after those seconds, so such a program cannot end the run before your browser gets there, as long as your browser opens the link within those 5 seconds. A browser slower than that can lose the race without a warning; that limit is known. Any other way to win the race silently is in scope.
