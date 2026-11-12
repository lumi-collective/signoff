package main

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

const anID = "0123456789abcdef0123456789abcdef"

func TestHeldKeepsOneOfferForOneUse(t *testing.T) {
	var h held
	now := time.Unix(1000, 0)
	note1, prev := h.put(anID, now)
	if note1 != "offer-1000001" || prev != "" { // after a restart, never the earlier run's offer-1
		t.Fatalf("put: %q, %q", note1, prev)
	}
	other := strings.Repeat("f", 32)
	note2, prev := h.put(other, now)
	if note2 != "offer-1000002" || prev != note1 {
		t.Fatalf("second put: %q, %q", note2, prev)
	}
	if got := h.take(note1, now); got != "" {
		t.Fatalf("a replaced banner still answers: %q", got)
	}
	if got := h.take("waiting", now); got != "" {
		t.Fatalf("the number banner answers: %q", got)
	}
	if got := h.take("", now); got != "" {
		t.Fatalf("an empty id answers: %q", got)
	}
	if got := h.take(note2, now.Add(offerTTL)); got != other {
		t.Fatalf("take %q", got)
	}
	if got := h.take(note2, now); got != "" {
		t.Fatalf("a second take: %q", got)
	}
	note3, prev := h.put(anID, now)
	if note3 != "offer-1000003" || prev != "" { // an answered banner is already gone
		t.Fatalf("third put: %q, %q", note3, prev)
	}
	if got := h.take(note3, now.Add(offerTTL+time.Second)); got != "" {
		t.Fatalf("an expired offer answers: %q", got)
	}
	if got := h.take(note3, now); got != "" {
		t.Fatalf("an expired offer stays: %q", got)
	}
}

func TestNoteForShowsTheFourTextsAndNoID(t *testing.T) {
	o := &offer{ID: anID, Project: "shop", Round: "Pick the layout", Question: "Two columns\nor one?\xe2\x80\xae", // U+202E
		Answer: "Two columns + Date"}
	n := noteFor(o, "offer-7")
	if n.ID != "offer-7" || n.CategoryID != offerCategory {
		t.Fatalf("id %q, category %q", n.ID, n.CategoryID)
	}
	if n.Title != "shop · Pick the layout" || n.Body != "Two columns or one? \nRecommended: Two columns + Date" {
		t.Fatalf("title %q, body %q", n.Title, n.Body)
	}
	if n.Data != nil || n.Subtitle != "" || n.Attachments != nil || n.Schedule != nil {
		t.Fatalf("more than the texts: %+v", n)
	}
	data, _ := json.Marshal(n)
	if strings.Contains(string(data), anID) {
		t.Fatalf("the offer id reaches the notification: %s", data)
	}
}

func TestOneLine(t *testing.T) {
	cases := map[string]string{
		"plain":          "plain",
		"a\r\nb\tc\x00d": "a  b c d",
		"x\xe2\x80\xa8y\xe2\x80\xa9z\xe2\x80\x8bw\xe2\x80\xaev": "x y z w v", // U+2028, U+2029, U+200B, U+202E
		"Árvíztűrő tükörfúrógép":                                "Árvíztűrő tükörfúrógép",
	}
	for in, want := range cases {
		if got := oneLine(in); got != want {
			t.Errorf("oneLine(%q) = %q, want %q", in, got, want)
		}
	}
	if got := oneLine(strings.Repeat("é", 300)); got != strings.Repeat("é", maxOfferText) {
		t.Errorf("not cut at %d characters: %d", maxOfferText, len([]rune(got)))
	}
}

func TestRespondRoutesEachAction(t *testing.T) {
	refused := func(string) error { return errTest }
	cases := []struct {
		name, id, action string
		accept           func(string) error
		accepted, opened []string
		next             int // asks for the next offer: after a saved accept only
	}{
		{"accepted", anID, actionAccept, nil, []string{anID}, nil, 1},
		{"refused", anID, actionAccept, refused, []string{anID}, []string{anID}, 0},
		{"other", anID, actionOther, nil, nil, []string{anID}, 0},
		{"tap", anID, "DEFAULT_ACTION", nil, nil, []string{anID}, 0},
		{"no offer, accept", "", actionAccept, nil, nil, []string{""}, 0},
		{"no offer, tap", "", "DEFAULT_ACTION", nil, nil, []string{""}, 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			var accepted, opened []string
			next := 0
			respond(c.id, c.action, func(id string) error {
				accepted = append(accepted, id)
				if c.accept != nil {
					return c.accept(id)
				}
				return nil
			}, func(id string) { opened = append(opened, id) }, func() { next++ })
			if !reflect.DeepEqual(accepted, c.accepted) || !reflect.DeepEqual(opened, c.opened) || next != c.next {
				t.Fatalf("accepted %q, opened %q, next %d", accepted, opened, next)
			}
		})
	}
}

type testError string

func (e testError) Error() string { return string(e) }

const errTest = testError("refused")

func TestControlOffer(t *testing.T) {
	long, escaped := strings.Repeat("ő", maxOfferText), strings.Repeat(`\`+`u0151`, maxOfferText) // as the inbox sends it
	line := []byte(`{"offer": {"id": "` + anID + `", "project": "` + escaped + `", "round": "` + escaped +
		`", "question": "` + escaped + `", "answer": "` + escaped + `"}}`)
	if len(line) <= 4096 { // the inbox's MAX_CONTROL, the app's reply cap until 9b-2b
		t.Fatalf("the reply is only %d bytes", len(line))
	}
	got := make(chan string, 1)
	o, err := controlOffer(fakeControl(t, shortDir(t), string(line)+"\n", got))
	if err != nil || o == nil || o.ID != anID || o.Question != long {
		t.Fatalf("offer %+v, err %v", o, err)
	}
	if req := <-got; req != "{\"op\":\"offer\"}\n" {
		t.Fatalf("request %q", req)
	}
	for name, reply := range map[string]string{
		"none":       `{"offer": null}`,
		"short id":   `{"offer": {"id": "0123", "project": "p", "round": "r", "question": "q", "answer": "a"}}`,
		"upper id":   `{"offer": {"id": "` + strings.ToUpper(anID) + `", "project": "p", "round": "r", "question": "q", "answer": "a"}}`,
		"no answer":  `{"offer": {"id": "` + anID + `", "project": "p", "round": "r", "question": "q"}}`,
		"id number":  `{"offer": {"id": 5, "project": "p", "round": "r", "question": "q", "answer": "a"}}`,
		"error":      `{"error": "unknown request"}`,
		"not object": `["offer"]`,
	} {
		t.Run(name, func(t *testing.T) {
			if o, _ := controlOffer(fakeControl(t, shortDir(t), reply+"\n", nil)); o != nil {
				t.Fatalf("an offer from %s: %+v", reply, o)
			}
		})
	}
}

func TestControlAcceptSendsOnlyTheID(t *testing.T) {
	got := make(chan string, 1)
	if err := controlAccept(fakeControl(t, shortDir(t), "{\"ok\": true}\n", got), anID); err != nil {
		t.Fatal(err)
	}
	if req := <-got; req != "{\"offer\":\""+anID+"\",\"op\":\"accept\"}\n" {
		t.Fatalf("request %q", req)
	}
	for _, reply := range []string{`{"error": "gone"}`, `{"error": "changed"}`, `{"error": "held"}`,
		`{"error": "done"}`, `{"error": "failed"}`, `{"ok": false}`, `junk`, ``} {
		err := controlAccept(fakeControl(t, shortDir(t), reply+"\n", nil), anID)
		if err == nil {
			t.Fatalf("no refusal from %q", reply)
		}
		if strings.Contains(err.Error(), anID) {
			t.Fatalf("the error holds the offer id: %v", err)
		}
	}
	err := controlAccept(filepath.Join(shortDir(t), "inbox.sock"), anID)
	if err == nil || strings.Contains(err.Error(), anID) {
		t.Fatalf("without an inbox: %v", err)
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
