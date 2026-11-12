package main

import (
	"net"
	"net/url"
)

// What the webview does with a navigation. The numbers are shared with nav_darwin.m: allow must stay 1.
const (
	navRefuse  = 0 // cancelled, nothing opens
	navAllow   = 1 // loads where it was meant to
	navInApp   = 2 // cancelled, opened in a new window of the app (an inbox URL holds a page token: no browser)
	navBrowser = 3 // cancelled, opened in the default browser
)

// decide keeps the webview on the inbox: origin is "http://127.0.0.1:<port>", newWindow a link meant for a new
// window (target=_blank), click a navigation started by a link, fromMain one that comes from the inbox page itself
// rather than a frame in it (a board: agent HTML, which in a browser can open nothing outside).
func decide(origin, raw string, newWindow, click, fromMain bool) int {
	u, err := url.Parse(raw)
	if err != nil {
		return navRefuse
	}
	if (raw == "about:blank" || raw == "about:srcdoc") && !newWindow {
		return navAllow // a frame's empty first document loads nothing
	}
	if origin != "" && u.Scheme == "http" && u.User == nil && u.Opaque == "" && "http://"+u.Host == origin {
		if !newWindow {
			return navAllow
		}
		if fromMain {
			return navInApp
		}
		return navRefuse
	}
	// An inbox binds 127.0.0.1 only; another inbox's URL, such as one from before a restart, holds a page token:
	// never the browser. shortcut: a todo link to a dev server on 127.0.0.1 opens nothing either (localhost does).
	if ip := net.ParseIP(u.Hostname()); ip != nil && ip.Equal(net.IPv4(127, 0, 0, 1)) {
		return navRefuse
	}
	// The page's own outside links all open a new window; nothing else may reach the browser.
	if click && newWindow && fromMain && (u.Scheme == "http" || u.Scheme == "https") && u.Host != "" {
		return navBrowser
	}
	return navRefuse
}
