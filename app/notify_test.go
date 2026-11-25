package main

import (
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/wailsapp/wails/v3/pkg/services/notifications"
)

// TestRespondOnlyOpensTheWindow: a banner only says how many wait, so every response opens the window, the
// banners and actions of an older app included (offer-<k>, Accept & send, Other answer…).
func TestRespondOnlyOpensTheWindow(t *testing.T) {
	cases := []struct {
		name, id, action string
		err              error
		opened           int
	}{
		// Wails v3.1.0 gives a tap as DefaultActionIdentifier, and reports no dismissal.
		{"the number, tapped", "waiting", "DefaultActionIdentifier", nil, 1},
		{"an old offer, tapped", "offer-1700000000001", "DefaultActionIdentifier", nil, 1},
		{"an old Accept & send", "offer-7", "accept", nil, 1},
		{"an old Other answer…", "offer-7", "other", nil, 1},
		{"an error", "offer-7", "accept", errors.New("no response"), 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			opened := 0
			respond(notifications.NotificationResult{Error: c.err, Response: notifications.NotificationResponse{
				ID: c.id, ActionIdentifier: c.action, CategoryID: "signoff.offer"}}, func() { opened++ })
			if opened != c.opened {
				t.Fatalf("opened %d times", opened)
			}
		})
	}
}

// TestNoBannerAnswers: no source asks the inbox for an offer or an accept, or sends a banner with actions.
func TestNoBannerAnswers(t *testing.T) {
	files, _ := filepath.Glob("*.go")
	for _, name := range files {
		if strings.HasSuffix(name, "_test.go") {
			continue
		}
		f, err := parser.ParseFile(token.NewFileSet(), name, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		ast.Inspect(f, func(n ast.Node) bool {
			if lit, ok := n.(*ast.BasicLit); ok && lit.Kind == token.STRING {
				s, _ := strconv.Unquote(lit.Value)
				if s == "offer" || s == "accept" || strings.Contains(s, "signoff.offer") || strings.Contains(s, "Accept & send") {
					t.Errorf("%s holds %q", name, s)
				}
			}
			if sel, ok := n.(*ast.SelectorExpr); ok && (sel.Sel.Name == "SendNotificationWithActions" ||
				sel.Sel.Name == "RegisterNotificationCategory") {
				t.Errorf("%s uses %s", name, sel.Sel.Name)
			}
			return true
		})
	}
}

// TestNoNotificationCarriesData: macOS keeps a notification's Data as its user info, so no source sets it.
func TestNoNotificationCarriesData(t *testing.T) {
	files, _ := filepath.Glob("*.go")
	for _, name := range files {
		if strings.HasSuffix(name, "_test.go") {
			continue
		}
		f, err := parser.ParseFile(token.NewFileSet(), name, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		ast.Inspect(f, func(n ast.Node) bool {
			if kv, ok := n.(*ast.KeyValueExpr); ok {
				if id, ok := kv.Key.(*ast.Ident); ok && id.Name == "Data" {
					t.Errorf("%s sets Data", name)
				}
			}
			if sel, ok := n.(*ast.SelectorExpr); ok && sel.Sel.Name == "Data" {
				t.Errorf("%s uses .Data", name)
			}
			return true
		})
	}
}
