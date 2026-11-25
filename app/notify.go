package main

import "github.com/wailsapp/wails/v3/pkg/services/notifications"

// respond acts on a response to a banner, which only says how many wait: whatever its identifier and action, it
// opens the window and nothing else. A banner a 0.3.0 or 0.4.0 app sent (offer-<k>, with Accept & send and Other
// answer…) may still sit in Notification Center; its actions open the window too.
func respond(r notifications.NotificationResult, open func()) {
	if r.Error == nil {
		open()
	}
}
