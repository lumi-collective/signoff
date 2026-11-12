package main

import "testing"

func TestTakesPendingOnlyOnActivateWithALiveInbox(t *testing.T) {
	cases := []struct {
		pending, alive   bool
		want, wantProbed bool
	}{
		{true, true, true, true},
		{true, false, false, true},  // a stopped inbox's target is gone
		{false, true, false, false}, // pending false (as show() passes): no take, and alive is never asked
		{false, false, false, false},
	}
	for _, c := range cases {
		probed := false
		alive := func() bool { probed = true; return c.alive }
		if got := takesPending(c.pending, alive); got != c.want || probed != c.wantProbed {
			t.Errorf("takesPending(%v, alive %v) = %v probed %v, want %v probed %v",
				c.pending, c.alive, got, probed, c.want, c.wantProbed)
		}
	}
}

func TestWindowStaysOnlyOnItsOwnLiveInbox(t *testing.T) {
	cases := []struct {
		name                         string
		link, offer, win, hidden     bool
		alive                        bool
		server, loaded               string
		want, aliveAsked, serverRead bool
	}{
		{"same live inbox", false, false, true, false, true, "a1", "a1", true, true, true},
		{"pending link wins", true, false, true, false, true, "a1", "a1", false, false, false},
		{"offer loads a link", false, true, true, false, true, "a1", "a1", false, false, false},
		{"hidden window", false, false, true, true, true, "a1", "a1", false, false, false},
		{"no window", false, false, false, false, true, "a1", "a1", false, false, false},
		{"inbox dead", false, false, true, false, false, "a1", "a1", false, true, false},
		{"another server_id", false, false, true, false, true, "b2", "a1", false, true, true},
		{"inbox.json unreadable", false, false, true, false, true, "", "a1", false, true, true},
	}
	for _, c := range cases {
		asked, read := false, false
		alive := func() bool { asked = true; return c.alive }
		server := func() string { read = true; return c.server }
		got := windowStays(c.link, c.offer, c.win, c.hidden, alive, server, c.loaded)
		if got != c.want || asked != c.aliveAsked || read != c.serverRead {
			t.Errorf("%s: windowStays = %v (alive asked %v, server read %v), want %v (%v, %v)",
				c.name, got, asked, read, c.want, c.aliveAsked, c.serverRead)
		}
	}
}
