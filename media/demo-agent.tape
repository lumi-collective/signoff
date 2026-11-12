# The README's agent-side demo: vhs media/demo-agent.tape, from the repository root.
# media/demo/setup.sh builds /tmp/signoff-example from the synthetic demo; media/demo/answer.py stands in for the
# person at the browser, sending the same requests the page sends (vhs has no browser).
Output media/demo-agent.gif
Set Shell bash
Set FontSize 16
Set Width 1200
Set Height 420
Set Padding 20
Set TypingSpeed 40ms
Set Theme "Catppuccin Mocha"

Hide
Type "source media/demo/setup.sh && clear"
Enter
Sleep 1s
Show

Type "ls decisions/settings"
Enter
Sleep 1500ms
Type "python3 -I signoff/signoff.py decisions/settings --no-open > answers.txt 2> log &"
Enter
Sleep 1500ms
Type "# The user answers in the browser. The agent waits for the exit:"
Enter
Sleep 800ms
Type "wait; cat answers.txt"
Enter
Sleep 9s
Type "grep -A2 'filters on' decisions/settings/round-01.answers.md"
Enter
Sleep 4s
