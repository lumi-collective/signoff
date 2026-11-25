package main

import "testing"

func TestWindowStaysOnlyOnItsOwnLiveInbox(t *testing.T) {
	cases := []struct {
		name                         string
		win, hidden                  bool
		alive                        bool
		server, loaded               string
		want, aliveAsked, serverRead bool
	}{
		{"same live inbox", true, false, true, "a1", "a1", true, true, true},
		{"hidden window", true, true, true, "a1", "a1", false, false, false},
		{"no window", false, false, true, "a1", "a1", false, false, false},
		{"inbox dead", true, false, false, "a1", "a1", false, true, false},
		{"another server_id", true, false, true, "b2", "a1", false, true, true},
		{"inbox.json unreadable", true, false, true, "", "a1", false, true, true},
	}
	for _, c := range cases {
		asked, read := false, false
		alive := func() bool { asked = true; return c.alive }
		server := func() string { read = true; return c.server }
		got := windowStays(c.win, c.hidden, alive, server, c.loaded)
		if got != c.want || asked != c.aliveAsked || read != c.serverRead {
			t.Errorf("%s: windowStays = %v (alive asked %v, server read %v), want %v (%v, %v)",
				c.name, got, asked, read, c.want, c.aliveAsked, c.serverRead)
		}
	}
	if id := inboxServer(t.TempDir()); id != "" { // no inbox.json: never equal to a page's inbox
		t.Errorf("no inbox.json gave %q", id)
	}
}
