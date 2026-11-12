package main

import "testing"

// TestNavigationPolicyInstalls fails when Wails renames its webview delegate, grows its own navigation policy or
// new-window handler (either variant), or drops its wails:// handler: the app's policy would not run, or its
// scheme would stay open, and the app refuses to open a window. Not covered: that Wails still makes this class the
// webview's navigation and UI delegate (webview_window_darwin.go, windowNew); the app's run shows that.
func TestNavigationPolicyInstalls(t *testing.T) {
	if !installNav() || !installNav() {
		t.Fatal("the navigation policy could not be added to Wails' webview delegate")
	}
	if navAllowC() != navAllow {
		t.Fatalf("nav_darwin.h says %d is allow, nav.go %d", navAllowC(), navAllow)
	}
}
