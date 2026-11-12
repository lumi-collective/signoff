package main

import (
	_ "embed"
	"fmt"
	"strconv"
	"time"

	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/events"
)

//go:embed icon/menubar.png
var menubarIcon []byte

// newTray makes the menu bar item and its panel before the app runs. The panel is a webview window like the main
// one: Wails makes every window with the same WebviewWindowDelegate (webview_window_darwin.go, windowNew), so
// nav_darwin.m's policy and the shut wails:// scheme hold in it too.
func (s *appShell) newTray() {
	s.panel = s.app.Window.NewWithOptions(application.WebviewWindowOptions{
		Name: "panel", Width: 420, Height: 600, URL: "about:blank", // the start link comes on the first click
		Frameless: true, AlwaysOnTop: true, Hidden: true, DisableResize: true, HideOnFocusLost: true,
	})
	s.panel.RegisterHook(events.Common.WindowClosing, func(e *application.WindowEvent) {
		s.panel.Hide() // Cmd+W: the tray keeps this window
		e.Cancel()
	})
	s.panel.OnWindowEvent(events.Common.WindowHide, func(*application.WindowEvent) {
		s.mu.Lock()
		s.panelHid = time.Now()
		s.mu.Unlock()
	})
	menu := application.NewMenu()
	menu.Add("Open Signoff").OnClick(func(*application.Context) { go s.show("") })
	menu.Add("Quit Signoff").OnClick(func(*application.Context) { s.app.Quit() })
	s.tray = s.app.SystemTray.New()
	s.tray.SetTemplateIcon(menubarIcon).SetMenu(menu)
	// Clicks come on the main thread, and a toggle may wait on the inbox.
	s.tray.OnClick(func() { go s.togglePanel() })
	s.tray.OnRightClick(func() {
		s.panel.Hide()
		s.tray.OpenMenu()
	})
}

// togglePanel hides the panel, or shows it under the menu bar item with the inbox's page, from a start link of its
// own: never the main window's.
func (s *appShell) togglePanel() {
	s.showing.Lock()
	defer s.showing.Unlock()
	if s.panel.IsVisible() {
		s.panel.Hide()
		return
	}
	if !s.ready() {
		return
	}
	s.mu.Lock()
	hidden := time.Since(s.panelHid)
	s.mu.Unlock()
	if panelNeedsLink(s.panelInbox, inboxAlive(s.home), inboxServer(s.home), hidden) {
		link, port, err := openInbox(s.launcher, s.home, "")
		if err != nil {
			s.fail(err.Error())
			return
		}
		s.mu.Lock()
		s.inbox = fmt.Sprintf("http://127.0.0.1:%d", port) // before the load: the policy reads it
		s.mu.Unlock()
		s.panel.SetURL(link)
		s.panelInbox = inboxServer(s.home)
	}
	s.tray.PositionWindow(s.panel, 5)
	s.panel.Show()
	s.panel.Focus()
}

// panelFresh: a panel hidden this long gets a fresh link. The list page's state poll keeps its page token open, but
// a page that asks nothing (a round's page, or a list whose poll stopped at an error) is closed after the inbox's
// PAGE_IDLE (12 hours awake); an hour leaves a wide margin.
const panelFresh = time.Hour

// panelNeedsLink says whether the panel needs a fresh start link: it has no page yet (loaded is ""), the inbox that
// gave its page (its server_id) is gone, or it was hidden for panelFresh or more. Its rows change only on a reload,
// as in the main window; the key bar says when the count differs and offers Reload.
func panelNeedsLink(loaded string, alive bool, server string, hidden time.Duration) bool {
	return loaded == "" || !alive || server != loaded || hidden >= panelFresh
}

// countLabel is the text for the Dock badge and beside the menu bar item: the number waiting, else nothing.
func countLabel(n int, ok bool) string {
	if ok && n > 0 {
		return strconv.Itoa(n)
	}
	return ""
}
