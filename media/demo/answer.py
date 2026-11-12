#!/usr/bin/env python3
"""Play the person at the browser for media/demo-agent.tape: wait for Signoff's start link in the log, open it,
answer both rounds, accept them and press Done, through the same requests the page sends."""
import json
import re
import sys
import time
import urllib.request

log = sys.argv[1]
for _ in range(600):
    try:
        m = re.search(r"http://\S+", open(log, encoding="utf-8").read())
    except OSError:
        m = None
    if m:
        break
    time.sleep(0.1)
time.sleep(3)
with urllib.request.urlopen(m.group(0), timeout=5) as resp:  # the single-use start link, as a browser opens it
    page = resp.geturl()
origin = "/".join(page.split("/")[:3])
rev = [0, 0]


def save(ri, body):
    body = dict(body, rev=rev[ri])
    req = urllib.request.Request(page + "save/%d" % ri, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Origin": origin})
    with urllib.request.urlopen(req, timeout=10) as resp:
        rev[ri] = json.load(resp)["rev"]


lists = {"filters": {"choice": ["date", "tag", "owner"], "comment": "", "source": "user",
                     "marks": [{"image": 0, "box": [49.4, 9, 6.6, 6], "comment": "Drop the Filter button."}]},
         "empty-state": {"choice": "text", "comment": "", "source": "recommended"},
         "polish-first": {"choice": "list", "comment": "", "source": "recommended"}}
wording = {"sync-label": {"choice": "cellular", "comment": "", "source": "user"},
           "digest-label": {"choice": "digest", "comment": "", "source": "recommended"}}
save(0, {"answers": lists, "free_text": "", "done": False, "accepted": True})
time.sleep(1)
save(1, {"answers": wording, "free_text": "", "done": False, "accepted": True})
time.sleep(1)
save(0, {"answers": lists, "free_text": "", "done": True, "accepted": True})
save(1, {"answers": wording, "free_text": "", "done": True, "accepted": True})
