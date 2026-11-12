---
description: List this project's open Signoff rounds in a panel where plugin mods run; elsewhere open the Signoff inbox in the browser
disable-model-invocation: true
---

Signoff's mod answers this command without you. You see it because this Claude Code did not load the mod (mods
need Claude Code 2.1.286 or newer, by its docs, and must be allowed). Run `signoff inbox open 2>/dev/null` once with the Bash tool: it
starts the Signoff inbox when needed and opens it in the browser. Its stderr can hold a single-use link, so keep it
out. Then tell the user in one line whether it exited 0, and that `/signoff:open` answers by itself where Claude
Code runs plugin mods.
