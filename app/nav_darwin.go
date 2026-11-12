package main

/*
#cgo LDFLAGS: -framework WebKit
#include "nav_darwin.h"
*/
import "C"

// installNav adds the navigation policy to Wails' webview delegate; false when it could not (see nav_darwin.m).
func installNav() bool {
	return C.installNavigationPolicy() == 1
}

// navAllowC is the number nav_darwin.m reads as allow; a test keeps it equal to navAllow.
func navAllowC() int {
	return int(C.NAV_ALLOW)
}

//export navDecide
func navDecide(raw *C.char, newWindow, click, fromMain C.int) C.int {
	url := C.GoString(raw)
	what := decide(shell.origin(), url, newWindow != 0, click != 0, fromMain != 0)
	switch what {
	case navInApp:
		go shell.newWindow(url) // WebKit called on the main thread; Wails makes windows from any goroutine
	case navBrowser:
		go shell.app.Browser.OpenURL(url)
	}
	return C.int(what)
}
