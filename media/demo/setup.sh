#!/usr/bin/env bash
# Source this before recording media/demo-agent.tape: it builds a throwaway folder, /tmp/signoff-example, with two
# rounds of the synthetic demo, and starts media/demo/answer.py in the background to play the person at the
# browser (vhs has no browser). Usage, from the repository root: source media/demo/setup.sh
demo=${SIGNOFF_DEMO:-/tmp/signoff-example}
topic=$demo/decisions/settings
if [ -e "$demo" ]; then echo "$demo exists from an earlier recording; remove it first"; return 1; fi
mkdir -p "$topic"
cp -R plugin/signoff "$demo"/
cp -R examples/demo/mockups "$topic"/
cp examples/demo/round-02.json "$topic"/round-01.json
cp examples/demo/round-04.json "$topic"/round-02.json
python3 -I media/demo/answer.py "$demo/log" > /dev/null 2>&1 &
disown
export PS1='$ '
cd "$demo" || return
