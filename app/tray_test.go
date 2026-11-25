package main

import (
	"bytes"
	"go/ast"
	"go/parser"
	"go/token"
	"image/png"
	"path/filepath"
	"strings"
	"testing"
)

func TestCountLabelIsTheNumberOnly(t *testing.T) {
	cases := []struct {
		n    int
		ok   bool
		want string
	}{
		{0, true, ""}, {3, true, "3"}, {120, true, "120"},
		{3, false, ""}, // no inbox holds the lock: the count is not trusted
	}
	for _, c := range cases {
		if got := countLabel(c.n, c.ok); got != c.want {
			t.Errorf("countLabel(%d, %v) = %q, want %q", c.n, c.ok, got, c.want)
		}
	}
}

// appSource parses the app's Go files, tests included when tests is true.
func appSource(t *testing.T, tests bool) map[string]*ast.File {
	files, _ := filepath.Glob("*.go")
	out := map[string]*ast.File{}
	for _, name := range files {
		if !tests && strings.HasSuffix(name, "_test.go") {
			continue
		}
		f, err := parser.ParseFile(token.NewFileSet(), name, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		out[name] = f
	}
	if len(out) == 0 {
		t.Fatal("no source parsed")
	}
	return out
}

// Every window comes from newWindow: another call site would be another webview, with start links of its own.
func TestOneWebviewWindow(t *testing.T) {
	makers := map[string]bool{"New": true, "NewWithOptions": true, "NewNotchWindow": true, "Add": true} // Wails' WindowManager
	var sites []string
	for _, f := range appSource(t, false) {
		for _, d := range f.Decls {
			fn, ok := d.(*ast.FuncDecl)
			if !ok || fn.Body == nil {
				continue
			}
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				sel, ok := n.(*ast.SelectorExpr)
				if !ok {
					return true
				}
				if x, ok := sel.X.(*ast.SelectorExpr); ok && x.Sel.Name == "Window" && makers[sel.Sel.Name] {
					sites = append(sites, fn.Name.Name+": Window."+sel.Sel.Name)
				}
				if x, ok := sel.X.(*ast.Ident); ok && x.Name == "application" && sel.Sel.Name == "NewWindow" {
					sites = append(sites, fn.Name.Name+": application.NewWindow") // the package-level constructor
				}
				return true
			})
		}
	}
	if len(sites) != 1 || sites[0] != "newWindow: Window.NewWithOptions" {
		t.Errorf("windows are made at %v, want only newWindow", sites)
	}
}

// A left click on the menu bar item shows the main window, as Open Signoff and a Dock click do.
func TestMenuBarClickShowsTheWindow(t *testing.T) {
	found := 0
	for _, f := range appSource(t, false) {
		ast.Inspect(f, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			sel, ok := call.Fun.(*ast.SelectorExpr)
			if !ok || sel.Sel.Name != "OnClick" {
				return true
			}
			if recv, ok := sel.X.(*ast.SelectorExpr); !ok || recv.Sel.Name != "tray" {
				return true // a menu item's OnClick
			}
			found++
			shows := false
			ast.Inspect(call, func(n ast.Node) bool {
				if s, ok := n.(*ast.SelectorExpr); ok && s.Sel.Name == "show" {
					shows = true
				}
				return true
			})
			if !shows {
				t.Error("the menu bar item's OnClick does not call show")
			}
			return true
		})
	}
	if found != 1 {
		t.Errorf("found %d OnClick handlers on the menu bar item, want 1", found)
	}
}

// The menu bar panel is gone: no name in the app's code, tests included, may still speak of it.
func TestNoNameOfTheMenuBarWindowLeft(t *testing.T) {
	for name, f := range appSource(t, true) {
		ast.Inspect(f, func(n ast.Node) bool {
			if id, ok := n.(*ast.Ident); ok && strings.Contains(strings.ToLower(id.Name), "panel") {
				t.Errorf("%s: %s", name, id.Name)
			}
			return true
		})
	}
}

// The template image must be one colour on clear: macOS draws it in the menu bar's own colour from its alpha.
func TestMenubarIconIsATemplate(t *testing.T) {
	img, err := png.Decode(bytes.NewReader(menubarIcon))
	if err != nil {
		t.Fatal(err)
	}
	if b := img.Bounds(); b.Dx() != 44 || b.Dy() != 44 {
		t.Fatalf("menubar.png is %v, want 44 by 44", b)
	}
	clear, ink := 0, 0
	for y := 0; y < 44; y++ {
		for x := 0; x < 44; x++ {
			r, g, b, a := img.At(x, y).RGBA()
			switch {
			case a == 0:
				clear++
			case r|g|b != 0:
				t.Fatalf("pixel %d,%d is not black", x, y)
			default:
				ink++
			}
		}
	}
	if clear == 0 || ink == 0 {
		t.Fatalf("clear %d, ink %d", clear, ink)
	}
}
