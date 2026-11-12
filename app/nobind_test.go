package main

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestNoGoMethodReachesTheWebview fails when the app binds anything to the webview (AD-2): a Wails service, a
// raw message handler or a transport. The page is the inbox's, and it gets no way into the app.
func TestNoGoMethodReachesTheWebview(t *testing.T) {
	banned := map[string]bool{
		"Services": true, "RawMessageHandler": true, "Transport": true, // application.Options fields
		"RegisterService": true, "NewService": true, "NewServiceWithOptions": true,
	}
	files, _ := filepath.Glob("*.go")
	checked := 0
	for _, name := range files {
		if strings.HasSuffix(name, "_test.go") {
			continue
		}
		src, err := os.ReadFile(name)
		if err != nil {
			t.Fatal(err)
		}
		f, err := parser.ParseFile(token.NewFileSet(), name, src, 0)
		if err != nil {
			t.Fatal(err)
		}
		checked++
		ast.Inspect(f, func(n ast.Node) bool {
			var id *ast.Ident
			switch n := n.(type) {
			case *ast.KeyValueExpr:
				id, _ = n.Key.(*ast.Ident)
			case *ast.SelectorExpr:
				id = n.Sel
			}
			if id != nil && banned[id.Name] {
				t.Errorf("%s: %s binds Go code to the webview", name, id.Name)
			}
			return true
		})
	}
	if checked == 0 {
		t.Fatal("no source checked")
	}
}
