package main

import (
	"fmt"
	"sync"
	"time"
	"unicode"

	"github.com/wailsapp/wails/v3/pkg/services/notifications"
)

// An answer from a notification (AD-19, round-13): the inbox offers at most one item a tap may answer, and the banner
// shows its four texts with two actions.
const (
	offerCategory = "signoff.offer"
	actionAccept  = "accept"
	actionOther   = "other"
	offerTTL      = time.Hour // the inbox's OFFER_TTL: an older offer is gone there too
	maxOfferText  = 200       // the inbox's MAX_OFFER_TEXT
)

var offerActions = notifications.NotificationCategory{ID: offerCategory, Actions: []notifications.NotificationAction{
	{ID: actionAccept, Title: "Accept & send"},
	{ID: actionOther, Title: "Other answer…"},
}}

// held is the one offer a banner shows: the banner's identifier (offer-<k>, the app's own counter) and the offer's
// id. Memory only: macOS keeps a delivered notification's content and user info, so the id never goes there. One
// offer at a time, as one banner shows at a time.
type held struct {
	mu   sync.Mutex
	k    int
	note string
	id   string
	at   time.Time
}

// put keeps a new offer and returns its banner's identifier and the one of the banner it replaces ("" for none).
func (h *held) put(id string, now time.Time) (note, prev string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	prev = h.note
	if h.k == 0 {
		// A banner from an earlier run of the app may still be in Notification Center: its offer-<k> must never
		// name an offer of this run, so the counter starts at the time of the first offer.
		h.k = int(now.UnixMilli())
	}
	h.k++
	h.note, h.id, h.at = fmt.Sprintf("offer-%d", h.k), id, now
	return h.note, prev
}

// take gives the offer id of the banner note once, and only while the inbox may still hold it; else "".
func (h *held) take(note string, now time.Time) string {
	h.mu.Lock()
	defer h.mu.Unlock()
	if note == "" || note != h.note {
		return ""
	}
	id, fresh := h.id, now.Sub(h.at) <= offerTTL
	h.note, h.id = "", ""
	if !fresh {
		return ""
	}
	return id
}

// noteFor is the banner for an offer: its texts as the inbox gives them (the agent's own, with the project's folder
// and the user's home replaced there), one line each, and nothing of Signoff's own: no id, link, token or path, and
// no Data, which macOS would keep as the notification's user info.
func noteFor(o *offer, note string) notifications.NotificationOptions {
	return notifications.NotificationOptions{
		ID:         note,
		CategoryID: offerCategory,
		Title:      oneLine(o.Project) + " · " + oneLine(o.Round),
		Body:       oneLine(o.Question) + "\nRecommended: " + oneLine(o.Answer),
	}
}

// oneLine is the inbox's _one_line again, as the app does not trust a reply blindly: at most maxOfferText
// characters, each control, format (bidi), surrogate or line-separator one a space.
func oneLine(s string) string {
	r := []rune(s)
	if len(r) > maxOfferText {
		r = r[:maxOfferText]
	}
	for i, c := range r {
		if unicode.In(c, unicode.Cc, unicode.Cf, unicode.Cs, unicode.Zl, unicode.Zp) {
			r[i] = ' '
		}
	}
	return string(r)
}

// respond routes a response to a banner, given the offer id it held ("" for none: an old banner, the number one, or
// one from before a restart). Accept & send saves the recommendation, then next offers what waits after it (the count
// drops, so no rise would name it); when the inbox refuses, Other answer… or a tap on the banner, the window opens at
// the item (the inbox gives the list for an offer it no longer has).
func respond(id, action string, accept func(string) error, open func(string), next func()) {
	switch {
	case id == "":
		open("")
	case action == actionAccept && accept(id) == nil:
		next()
	default:
		open(id)
	}
}
