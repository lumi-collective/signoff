package main

import (
	_ "embed"
	"strconv"

	"github.com/wailsapp/wails/v3/pkg/application"
)

//go:embed icon/menubar.png
var menubarIcon []byte

// newTray makes the menu bar item before the app runs. It opens no webview of its own: a left click shows the main
// window, as Open Signoff and a Dock click do.
func (s *appShell) newTray() {
	menu := application.NewMenu()
	menu.Add("Open Signoff").OnClick(func(*application.Context) { go s.show() })
	menu.Add("Quit Signoff").OnClick(func(*application.Context) { s.app.Quit() })
	s.tray = s.app.SystemTray.New()
	s.tray.SetTemplateIcon(menubarIcon).SetMenu(menu)
	// Clicks come on the main thread, and a show may wait on the inbox.
	s.tray.OnClick(func() { go s.show() }) // a right click opens the menu: Wails' default when a menu is set
}

// countLabel is the text for the Dock badge and beside the menu bar item: the number waiting, else nothing.
func countLabel(n int, ok bool) string {
	if ok && n > 0 {
		return strconv.Itoa(n)
	}
	return ""
}
