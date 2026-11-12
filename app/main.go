// Signoff: a thin macOS shell around the inbox of the Signoff plugin. It starts the inbox through the plugin's
// own command, shows its page in a window and in a panel under its menu bar item, and keeps a Dock badge, the count
// beside the menu bar item and a notification from state.json. It holds no
// server, binds no Go method to the webview, and never writes a link or a token anywhere.
package main

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"

	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/events"
	"github.com/wailsapp/wails/v3/pkg/services/dock"
	"github.com/wailsapp/wails/v3/pkg/services/notifications"
)

const badgeEvery = 3 * time.Second

type appShell struct {
	app     *application.App
	notes   *notifications.NotificationService
	notesOK atomic.Bool // only once the notification centre started: without a bundle it would throw
	offers  held        // the offer the last banner shows
	dock    *dock.DockService
	navOK   bool // set before Run

	showing    sync.Mutex // one show at a time: the start, a Dock click and a menu bar click may come together
	launcher   string     // these four under showing
	home       string
	winInbox   string // the server_id of the inbox whose page the window holds
	panelInbox string // the same for the panel; "" before its first load
	badging    sync.Once

	tray  *application.SystemTray // these two set before Run
	panel *application.WebviewWindow

	mu       sync.Mutex // never held across a call into Wails: navDecide takes it on the main thread
	inbox    string     // the inbox's origin, http://127.0.0.1:<port>, from the last start link
	win      *application.WebviewWindow
	hidden   bool
	panelHid time.Time // when the panel last hid
}

var shell = &appShell{}

func main() {
	shell.navOK = installNav() // before any window: WebKit looks at the delegate's methods when a webview gets it
	shell.notes = notifications.New()
	// Wails calls this from the notification delegate before its completion handler: it must return at once.
	shell.notes.OnNotificationResponse(func(r notifications.NotificationResult) { go shell.answer(r) })
	shell.dock = dock.New()
	// Neither is registered as a service: that would expose their methods to the webview (AD-2).
	shell.app = application.New(application.Options{
		Name:   "Signoff",
		Logger: slog.New(slog.DiscardHandler), // Wails' own log may name a URL; a start link must not reach a log
		Assets: application.AssetOptions{Handler: http.NotFoundHandler(), DisableLogging: true},
	})
	shell.newTray()
	shell.app.Event.OnApplicationEvent(events.Common.ApplicationStarted, func(*application.ApplicationEvent) {
		go shell.activate()
		go shell.askNotifications()
	})
	shell.app.Event.OnApplicationEvent(events.Mac.ApplicationShouldHandleReopen, func(*application.ApplicationEvent) {
		go shell.activate() // a Dock click, and `open -a` while the app runs
	})
	if err := shell.app.Run(); err != nil {
		fmt.Fprintln(os.Stderr, "signoff inbox app:", err)
		os.Exit(1)
	}
	// Quitting leaves the inbox running: browser tabs, waiting runs and the CLI may use it.
}

// askNotifications asks macOS once for leave to notify; the question waits on the user, so it runs on its own.
func (s *appShell) askNotifications() {
	if s.notes.ServiceStartup(context.Background(), application.ServiceOptions{}) == nil {
		// Without the category an offer comes as a plain banner; a tap on it still opens the item.
		s.notes.RegisterNotificationCategory(offerActions)
		s.notesOK.Store(true)
		s.notes.RequestNotificationAuthorization() // a no leaves the badge
	}
}

// notify tells of a rise: the inbox's offer when it has one, with Accept & send and Other answer…; else the number.
func (s *appShell) notify(home string, n int) {
	if s.offerBanner(home) {
		return
	}
	// state.json has no text: the number and nothing else.
	s.notes.SendNotification(notifications.NotificationOptions{
		ID: "waiting", Title: "Signoff inbox", Body: fmt.Sprintf("%d waiting", n),
	})
}

// offerBanner sends the inbox's offer, when it has one, and says whether it did. The banner before it goes, so one
// offer shows at a time.
func (s *appShell) offerBanner(home string) bool {
	o, _ := controlOffer(filepath.Join(home, "inbox.sock"))
	if o == nil {
		return false
	}
	note, prev := s.offers.put(o.ID, time.Now())
	if prev != "" {
		s.notes.RemoveDeliveredNotification(prev)
	}
	return s.notes.SendNotificationWithActions(noteFor(o, note)) == nil
}

// answer acts on a response to a banner, off the delegate's thread.
func (s *appShell) answer(r notifications.NotificationResult) {
	if r.Error != nil {
		return
	}
	id := s.offers.take(r.Response.ID, time.Now())
	if id != "" {
		s.notes.RemoveDeliveredNotification(r.Response.ID) // its text stays nowhere once answered
	}
	s.showing.Lock()
	home := s.home // set once, under showing, before the badge (and so any offer) starts
	s.showing.Unlock()
	accept := func(id string) error {
		if err := ownFolder(home); err != nil { // also refuses "", a home not found yet
			return err
		}
		return controlAccept(filepath.Join(home, "inbox.sock"), id)
	}
	// After a saved accept the count drops, so no rise names what waits next: offer it now. None: the badge shows it.
	respond(id, r.Response.ActionIdentifier, accept, s.show, func() { s.offerBanner(home) })
}

// show brings the window up. A hidden one (closed by the user) or none gets a fresh start link; so does a window
// asked to open an offer's item (offer, its id), which the link's first use opens in Focus. A start that failed is
// tried again here, so a Dock click after a fix works.
func (s *appShell) show(offer string) {
	s.bringUp(offer, false)
}

// activate is show for the app coming up (its start, a Dock click, `open -a`): the window first takes the target
// `signoff inbox open --app` left with the inbox, when one waits, and loads its start link. Only here: the menu
// bar's panel, its menu and a banner never take it.
func (s *appShell) activate() {
	s.bringUp("", true)
}

func (s *appShell) bringUp(offer string, pending bool) {
	s.showing.Lock()
	defer s.showing.Unlock()
	if !s.ready() {
		return
	}
	link, port := "", 0
	alive := func() bool { return inboxAlive(s.home) }
	if takesPending(pending, alive) {
		link, port, _ = pendingLink(s.home) // a failed ask: as if none waits
	}
	s.mu.Lock()
	win, hidden := s.win, s.hidden
	s.mu.Unlock()
	if windowStays(link != "", offer != "", win != nil, hidden, alive, func() string { return inboxServer(s.home) }, s.winInbox) {
		win.Show() // a minimised one comes back
		win.Focus()
		return
	}
	if link == "" {
		var err error
		if link, port, err = openInbox(s.launcher, s.home, offer); err != nil {
			s.fail(err.Error())
			return
		}
	}
	s.mu.Lock()
	s.inbox, s.hidden = fmt.Sprintf("http://127.0.0.1:%d", port), false // before the load: the policy reads it
	s.mu.Unlock()
	s.winInbox = inboxServer(s.home)
	if win != nil {
		win.SetURL(link)
		win.Show()
		win.Focus()
		return
	}
	win = s.newWindow(link)
	win.RegisterHook(events.Common.WindowClosing, func(e *application.WindowEvent) {
		// Hidden, not closed: the next Dock click loads a fresh start link into it, with no window to race. The
		// page lives on, so the saves it sends as it is hidden all finish; its page token stays open meanwhile, in
		// the app's memory only.
		s.mu.Lock()
		s.hidden = true
		s.mu.Unlock()
		win.Hide()
		e.Cancel()
	})
	s.mu.Lock()
	s.win = win
	s.mu.Unlock()
}

// takesPending says whether to ask the inbox for the target it keeps: only on activate, and only from a live
// inbox (no inbox, no target: one kept in a stopped inbox's memory is gone). alive is a func so that it runs only
// when pending holds: it takes the inbox's lock for a moment.
func takesPending(pending bool, alive func() bool) bool {
	return pending && alive()
}

// windowStays says whether the window is only shown and focused: no link to load, no offer, a window the user did
// not close, and its page from the inbox that runs now. On a stopped inbox a fresh link starts it again; on a
// restarted one (same port or not) it opens its page. The probes are funcs, asked only when the rest holds.
func windowStays(link, offer, win, hidden bool, alive func() bool, server func() string, loaded string) bool {
	return !link && !offer && win && !hidden && alive() && server() == loaded
}

// ready finds the launcher and the home once, and starts the badge; false (after a dialog) when it cannot. The
// caller holds showing.
func (s *appShell) ready() bool {
	if !s.navOK {
		s.fail("this Wails version changed its webview delegate, so the app cannot keep the window on the inbox")
		return false
	}
	if s.home == "" {
		launcher, home, err := resolveLauncher(launcher)
		if err != nil {
			s.fail(err.Error())
			return false
		}
		s.launcher, s.home = launcher, home
		s.badging.Do(func() { go s.badge(home) })
	}
	return true
}

func (s *appShell) origin() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.inbox
}

// newWindow makes a window and shows it. Hidden at first: else Wails shows a window again whenever a load in it
// finishes, the blank page a closed one gets included.
func (s *appShell) newWindow(url string) *application.WebviewWindow {
	win := s.app.Window.NewWithOptions(application.WebviewWindowOptions{
		Title: "Signoff", Width: 1100, Height: 800, URL: url, Hidden: true,
	})
	win.Show()
	win.Focus() // a window opened from a notification comes to the front with the app
	return win
}

// badge shows the count of state.json on the Dock and beside the menu bar item while an inbox holds its lock, and
// notifies when it rises.
func (s *appShell) badge(home string) {
	var seen rise
	shown := ""
	for {
		n, err := waiting(home)
		ok := err == nil && ownFolder(home) == nil && inboxAlive(home)
		label := countLabel(n, ok)
		if label != shown {
			s.tray.SetLabel(label)
			if label == "" {
				err = s.dock.RemoveBadge()
			} else {
				err = s.dock.SetBadge(label)
			}
			if err == nil {
				shown = label // else the next look tries again
			}
		}
		if seen.next(n, ok) && s.notesOK.Load() {
			s.notify(home, n)
		}
		time.Sleep(badgeEvery)
	}
}

func (s *appShell) fail(msg string) {
	s.app.Dialog.Error().SetTitle("Signoff").SetMessage(msg).Show()
}
