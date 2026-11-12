package main

import (
	"bytes"
	"image/png"
	"testing"
	"time"
)

func TestCountLabelIsTheNumberOnly(t *testing.T) {
	cases := []struct {
		n    int
		ok   bool
		want string
	}{
		{0, true, ""}, {3, true, "3"}, {120, true, "120"},
		{3, false, ""}, // no inbox holds the lock: the count is not trusted
	}
	for _, c := range cases {
		if got := countLabel(c.n, c.ok); got != c.want {
			t.Errorf("countLabel(%d, %v) = %q, want %q", c.n, c.ok, got, c.want)
		}
	}
}

func TestPanelNeedsLinkOnlyWithoutALivePage(t *testing.T) {
	cases := []struct {
		loaded string
		alive  bool
		server string
		hidden time.Duration
		want   bool
	}{
		{"", true, "a1", 0, true},                 // first click: no page yet
		{"a1", true, "a1", time.Minute, false},    // the same inbox: the page's token is still open
		{"a1", false, "a1", time.Minute, true},    // the inbox stopped: a fresh link starts it again
		{"a1", true, "b2", time.Minute, true},     // a new inbox, on the same port or not: the old token is gone
		{"a1", true, "", time.Minute, true},       // inbox.json unreadable
		{"a1", true, "a1", panelFresh - 1, false}, // hidden a while
		{"a1", true, "a1", panelFresh, true},      // a page that asks nothing nears PAGE_IDLE
		{"a1", true, "a1", 48 * time.Hour, true},
	}
	for _, c := range cases {
		if got := panelNeedsLink(c.loaded, c.alive, c.server, c.hidden); got != c.want {
			t.Errorf("panelNeedsLink(%q, %v, %q, %v) = %v, want %v", c.loaded, c.alive, c.server, c.hidden, got, c.want)
		}
	}
	if id := inboxServer(t.TempDir()); id != "" { // no inbox.json: never equal to a page's inbox
		t.Errorf("no inbox.json gave %q", id)
	}
}

// The template image must be one colour on clear: macOS draws it in the menu bar's own colour from its alpha.
func TestMenubarIconIsATemplate(t *testing.T) {
	img, err := png.Decode(bytes.NewReader(menubarIcon))
	if err != nil {
		t.Fatal(err)
	}
	if b := img.Bounds(); b.Dx() != 44 || b.Dy() != 44 {
		t.Fatalf("menubar.png is %v, want 44 by 44", b)
	}
	clear, ink := 0, 0
	for y := 0; y < 44; y++ {
		for x := 0; x < 44; x++ {
			r, g, b, a := img.At(x, y).RGBA()
			switch {
			case a == 0:
				clear++
			case r|g|b != 0:
				t.Fatalf("pixel %d,%d is not black", x, y)
			default:
				ink++
			}
		}
	}
	if clear == 0 || ink == 0 {
		t.Fatalf("clear %d, ink %d", clear, ink)
	}
}
