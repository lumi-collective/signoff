---
description: Try Signoff with a small synthetic round in a temporary folder
disable-model-invocation: true
---

Show the user what Signoff looks like. Load the `signoff` skill first and use its round format:

1. Make a new temporary folder with `mktemp -d`. Never write into the user's project.
2. Write one `round-01.json` there with `"notice": "Synthetic data only"` and three made-up items, all about a
   fictional notes app: a single choice with a recommendation, a multiple choice (`"select": "many"`, `recommended` a list of option ids), and one that
   invites a comment.
3. Run Signoff on that folder in the background, as the skill says.
4. Tell the user in two lines: this is a demo, nothing in it is real; and when done, the folder can be removed
   with `rm -r <the folder>`. Give the exact command with the real path, and do not run it. If stderr says the
   inbox is open, say instead that the round waits in the Signoff inbox, under the folder's name, outside any
   project.
