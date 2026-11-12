package main

import "testing"

func TestDecideKeepsTheWebviewOnTheInbox(t *testing.T) {
	inbox := "http://127.0.0.1:53111"
	cases := []struct {
		url                        string
		newWindow, click, fromMain bool
		want                       int
	}{
		{"http://127.0.0.1:53111/b/tok/", false, false, false, navAllow}, // the app's own load
		{"http://127.0.0.1:53111/page/", false, true, true, navAllow},
		{"http://127.0.0.1:53111/page/board/1", false, false, true, navAllow}, // a board's frame
		{"http://127.0.0.1:53111/page/board/1", false, true, false, navAllow}, // the board's own link, in its frame
		{"http://127.0.0.1:53111/page/run/1/img/0", true, true, true, navInApp},
		{"http://127.0.0.1:53111/page/run/1/img/0", true, true, false, navRefuse},
		{"about:blank", false, false, false, navAllow},
		{"about:srcdoc", false, false, false, navAllow},
		{"about:blank", true, false, true, navRefuse},
		{"https://example.com/doc", true, true, true, navBrowser}, // the page's outside links: target=_blank
		{"http://example.com/", true, true, true, navBrowser},
		{"https://example.com/doc", false, true, true, navRefuse},  // not for a new window
		{"https://example.com/doc", true, false, true, navRefuse},  // not a click: a script
		{"https://example.com/doc", false, true, false, navRefuse}, // a board's click: no way out
		{"https://example.com/doc", true, true, false, navRefuse},
		{"http://127.0.0.1:53112/page/", true, true, true, navRefuse}, // another inbox's page token
		{"http://127.0.0.1/page/", true, true, true, navRefuse},
		{"http://[::ffff:127.0.0.1]:53112/page/", true, true, true, navRefuse},
		{"http://localhost:3000/", true, true, true, navBrowser}, // a dev server a todo item links to
		{"http://[::1]:3000/", true, true, true, navBrowser},
		{"https://127.0.0.1:53111/page/", false, false, true, navRefuse},
		{"http://user@127.0.0.1:53111/page/", false, false, true, navRefuse},
		{"http://127.0.0.1:53111@evil.example/", false, false, true, navRefuse},
		{"javascript:alert(1)", true, true, true, navRefuse},
		{"file:///etc/passwd", true, true, true, navRefuse},
		{"wails://localhost/", true, true, true, navRefuse},
		{"data:text/html,x", true, true, true, navRefuse},
		{"http:opaque", true, true, true, navRefuse},
		{"", true, true, true, navRefuse},
		{"%zz", true, true, true, navRefuse},
	}
	for _, c := range cases {
		if got := decide(inbox, c.url, c.newWindow, c.click, c.fromMain); got != c.want {
			t.Errorf("decide(%q, newWindow %v, click %v, fromMain %v) = %d, want %d",
				c.url, c.newWindow, c.click, c.fromMain, got, c.want)
		}
	}
	// Before the app has an inbox, nothing of 127.0.0.1 loads.
	if got := decide("", "http://127.0.0.1:53111/page/", false, false, true); got != navRefuse {
		t.Errorf("no inbox yet: %d", got)
	}
}
