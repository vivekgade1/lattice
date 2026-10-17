// impact_truth: the ground truth of Tier 3 (docs/results/2026-10-16-change-impact-prereg.md,
// section 3). Independent of the engine: it uses go/parser, not the extract and not the engine's
// lexical resolver.
//
//	impact_truth diff  ROOT DIFF   the existing non-test .go files under cmd/ and internal/
//	                               that the unified diff (git diff -U0) edits on the old side,
//	                               and the top-level func or type declarations those lines touch
//	impact_truth decls ROOT FILE.. the top-level func and type declarations of the files
//
// An addition between two declarations edits no declaration (the file only); an addition
// inside a declaration's range edits that declaration.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type decl struct {
	File  string `json:"file"`
	Name  string `json:"name"`
	Kind  string `json:"kind"`
	Start int    `json:"start"`
	End   int    `json:"end"`
}

type hunk struct{ a, b int }

func decls(root, rel string) ([]decl, error) {
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, filepath.Join(root, rel), nil, parser.ParseComments)
	if err != nil {
		return nil, err
	}
	var out []decl
	for _, d := range f.Decls {
		switch x := d.(type) {
		case *ast.FuncDecl:
			name := x.Name.Name
			if x.Recv != nil && len(x.Recv.List) > 0 {
				name = recvName(x.Recv.List[0].Type) + "." + name
			}
			start := fset.Position(x.Pos()).Line
			if x.Doc != nil {
				start = fset.Position(x.Doc.Pos()).Line
			}
			out = append(out, decl{rel, name, "func", start, fset.Position(x.End()).Line})
		case *ast.GenDecl:
			if x.Tok != token.TYPE {
				continue
			}
			for _, s := range x.Specs {
				ts := s.(*ast.TypeSpec)
				start := fset.Position(x.Pos()).Line
				if x.Doc != nil {
					start = fset.Position(x.Doc.Pos()).Line
				}
				out = append(out, decl{rel, ts.Name.Name, "type", start, fset.Position(x.End()).Line})
			}
		}
	}
	return out, nil
}

func recvName(e ast.Expr) string {
	switch x := e.(type) {
	case *ast.StarExpr:
		return recvName(x.X)
	case *ast.Ident:
		return x.Name
	case *ast.IndexExpr:
		return recvName(x.X)
	case *ast.IndexListExpr:
		return recvName(x.X)
	}
	return "?"
}

var hunkRe = regexp.MustCompile(`^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@`)

func relevant(p string) bool {
	return strings.HasSuffix(p, ".go") && !strings.HasSuffix(p, "_test.go") &&
		(strings.HasPrefix(p, "cmd/") || strings.HasPrefix(p, "internal/"))
}

func main() {
	if len(os.Args) < 4 {
		fmt.Fprintln(os.Stderr, "usage: impact_truth diff ROOT DIFF | decls ROOT FILE...")
		os.Exit(2)
	}
	mode, root := os.Args[1], os.Args[2]
	switch mode {
	case "decls":
		var all []decl
		for _, rel := range os.Args[3:] {
			ds, err := decls(root, rel)
			if err != nil {
				fmt.Fprintln(os.Stderr, rel+":", err)
				os.Exit(1)
			}
			all = append(all, ds...)
		}
		emit(map[string]any{"decls": all})
	case "diff":
		diffMode(root, os.Args[3])
	default:
		os.Exit(2)
	}
}

func diffMode(root, diffPath string) {
	fh, err := os.Open(diffPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer fh.Close()
	hunks := map[string][]hunk{}
	var added, removed []string
	oldP, newP := "", ""
	sc := bufio.NewScanner(fh)
	sc.Buffer(make([]byte, 1<<20), 1<<26)
	for sc.Scan() {
		line := sc.Text()
		switch {
		case strings.HasPrefix(line, "--- "):
			oldP = strings.TrimPrefix(strings.TrimPrefix(line, "--- "), "a/")
		case strings.HasPrefix(line, "+++ "):
			newP = strings.TrimPrefix(strings.TrimPrefix(line, "+++ "), "b/")
			if oldP == "/dev/null" && relevant(newP) {
				added = append(added, newP)
			}
			if newP == "/dev/null" && relevant(oldP) {
				removed = append(removed, oldP)
				hunks[oldP] = append(hunks[oldP], hunk{1, 1 << 30})
			}
		case strings.HasPrefix(line, "@@"):
			m := hunkRe.FindStringSubmatch(line)
			if m == nil || oldP == "/dev/null" || newP == "/dev/null" || !relevant(oldP) {
				continue
			}
			a, _ := strconv.Atoi(m[1])
			b := 1
			if m[2] != "" {
				b, _ = strconv.Atoi(m[2])
			}
			hunks[oldP] = append(hunks[oldP], hunk{a, b})
		}
	}
	var files []string
	for f := range hunks {
		files = append(files, f)
	}
	sort.Strings(files)
	var edited []decl
	for _, f := range files {
		ds, err := decls(root, f)
		if err != nil {
			fmt.Fprintln(os.Stderr, f+":", err)
			os.Exit(1)
		}
		for _, d := range ds {
			for _, h := range hunks[f] {
				hit := false
				if h.b == 0 {
					hit = h.a >= d.Start && h.a < d.End
				} else {
					hit = h.a <= d.End && h.a+h.b-1 >= d.Start
				}
				if hit {
					edited = append(edited, d)
					break
				}
			}
		}
	}
	sort.Strings(added)
	sort.Strings(removed)
	emit(map[string]any{
		"edited_files": files, "added_files": added, "removed_files": removed,
		"edited_decls": edited,
	})
}

func emit(v any) {
	b, _ := json.MarshalIndent(v, "", " ")
	fmt.Println(string(b))
}
