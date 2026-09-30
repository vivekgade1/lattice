// Command goast measures and screens one Go source file for the 5b pilot's arm G
// (bench/go_arm.py): `goast <file.go>` prints one JSON object on stdout.
//
//	{"parse_error": "", "lines": 0, "statements": 0, "ast_nodes": 0, "violations": []}
//
// Size is measured as bench.metrics.program_metrics measures a Python program:
// lines are the non-blank lines that are not whole-line `//` comments, statements
// are the ast.Stmt nodes at every nesting level, and ast_nodes is every node
// ast.Inspect visits (the file node included). A file that does not parse gets
// lines only (statements and ast_nodes 0) and its parse error.
//
// Violations are the constructs arm G's handlers must not use because they open
// another database connection or bypass the kit's counted k.DB (the kit review,
// task-2-report.md; the pilot's Task 4 rulings):
//
//   - calling or referencing database/sql's Open, OpenDB or Register;
//   - any `.Driver()` call (k.DB.Driver() reaches the uncounted SQLite driver);
//   - importing database/sql/driver, or any non-standard-library package whose
//     path mentions "sql" (a SQL driver such as modernc.org/sqlite);
//   - a constant SQL string holding more than one statement (a `;` followed by
//     more SQL): the counting driver would see it as one statement.
//
// Each violation is {"line": n, "message": "..."}; the messages are written for the
// model (they become repair feedback). Only constant strings are screened: SQL
// assembled at run time is not.
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"sort"
	"strconv"
	"strings"
)

type violation struct {
	Line    int    `json:"line"`
	Message string `json:"message"`
}

type report struct {
	ParseError string      `json:"parse_error"`
	Lines      int         `json:"lines"`
	Statements int         `json:"statements"`
	ASTNodes   int         `json:"ast_nodes"`
	Violations []violation `json:"violations"`
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: goast <file.go>")
		os.Exit(2)
	}
	src, err := os.ReadFile(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	out, _ := json.Marshal(analyze(src))
	fmt.Println(string(out))
}

func analyze(src []byte) report {
	r := report{Lines: countLines(string(src)), Violations: []violation{}}
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "handlers.go", src, parser.ParseComments)
	if err != nil {
		r.ParseError = err.Error()
		return r
	}
	ast.Inspect(file, func(n ast.Node) bool {
		if n == nil {
			return false
		}
		r.ASTNodes++
		if _, ok := n.(ast.Stmt); ok {
			r.Statements++
		}
		return true
	})
	r.Violations = screen(fset, file)
	return r
}

func countLines(src string) int {
	n := 0
	for _, line := range strings.Split(src, "\n") {
		s := strings.TrimSpace(line)
		if s != "" && !strings.HasPrefix(s, "//") {
			n++
		}
	}
	return n
}

const kitRule = "run every statement through the kit's k.DB (it is what counts them)"

func screen(fset *token.FileSet, file *ast.File) []violation {
	vs := []violation{}
	add := func(pos token.Pos, msg string) {
		vs = append(vs, violation{Line: fset.Position(pos).Line, Message: msg})
	}

	// The names database/sql is imported under ("." for a dot import).
	sqlNames := map[string]bool{}
	for _, imp := range file.Imports {
		path, err := strconv.Unquote(imp.Path.Value)
		if err != nil {
			continue
		}
		if path == "database/sql" {
			name := "sql"
			if imp.Name != nil {
				name = imp.Name.Name
			}
			sqlNames[name] = true
		}
		if path == "database/sql/driver" {
			add(imp.Pos(), fmt.Sprintf("import %q is not allowed: handlers must not use a "+
				"database driver directly; %s", path, kitRule))
		} else if !stdlib(path) && path != "platformkit" &&
			strings.Contains(strings.ToLower(path), "sql") {
			add(imp.Pos(), fmt.Sprintf("import %q is not allowed: handlers must not open "+
				"their own database connection; %s", path, kitRule))
		}
	}

	opens := map[string]bool{"Open": true, "OpenDB": true, "Register": true}
	folded := map[ast.Node]bool{}
	ast.Inspect(file, func(n ast.Node) bool {
		switch x := n.(type) {
		case *ast.SelectorExpr:
			if id, ok := x.X.(*ast.Ident); ok && sqlNames[id.Name] && opens[x.Sel.Name] {
				add(x.Pos(), fmt.Sprintf("%s.%s is not allowed: handlers must not open "+
					"another database connection; %s", id.Name, x.Sel.Name, kitRule))
			}
		case *ast.CallExpr:
			if sel, ok := x.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "Driver" &&
				len(x.Args) == 0 {
				add(x.Pos(), "calling .Driver() is not allowed: it reaches the database "+
					"driver behind the kit's counter; "+kitRule)
			}
			if id, ok := x.Fun.(*ast.Ident); ok && sqlNames["."] && opens[id.Name] {
				add(x.Pos(), fmt.Sprintf("%s is not allowed: handlers must not open "+
					"another database connection; %s", id.Name, kitRule))
			}
		case *ast.BinaryExpr, *ast.BasicLit:
			if folded[n] {
				return false
			}
			if s, ok := constString(n.(ast.Expr)); ok {
				markFolded(n, folded)
				if multiStatement(s) {
					add(n.Pos(), "this SQL string holds more than one statement: send one "+
						"statement per call (the kit counts each call as one statement)")
				}
				return false
			}
		}
		return true
	})
	sort.SliceStable(vs, func(i, j int) bool { return vs[i].Line < vs[j].Line })
	return vs
}

// stdlib: the first path element of a standard-library import has no dot.
func stdlib(path string) bool {
	first, _, _ := strings.Cut(path, "/")
	return !strings.Contains(first, ".")
}

// constString folds a string literal or a `+` chain of string literals.
func constString(e ast.Expr) (string, bool) {
	switch x := e.(type) {
	case *ast.BasicLit:
		if x.Kind != token.STRING {
			return "", false
		}
		s, err := strconv.Unquote(x.Value)
		return s, err == nil
	case *ast.ParenExpr:
		return constString(x.X)
	case *ast.BinaryExpr:
		if x.Op != token.ADD {
			return "", false
		}
		a, ok := constString(x.X)
		if !ok {
			return "", false
		}
		b, ok := constString(x.Y)
		if !ok {
			return "", false
		}
		return a + b, true
	}
	return "", false
}

func markFolded(n ast.Node, folded map[ast.Node]bool) {
	ast.Inspect(n, func(m ast.Node) bool {
		if m != nil {
			folded[m] = true
		}
		return true
	})
}

var sqlWords = map[string]bool{
	"SELECT": true, "INSERT": true, "UPDATE": true, "DELETE": true, "WITH": true,
	"REPLACE": true, "CREATE": true, "DROP": true, "ALTER": true, "PRAGMA": true,
	"BEGIN": true, "COMMIT": true, "END": true, "ROLLBACK": true, "SAVEPOINT": true,
	"RELEASE": true, "ATTACH": true, "DETACH": true, "VACUUM": true, "ANALYZE": true,
	"REINDEX": true, "EXPLAIN": true,
}

// multiStatement: s starts like SQL and, outside quotes and comments, has a `;`
// followed by anything but whitespace, comments and more `;`.
func multiStatement(s string) bool {
	fields := strings.FieldsFunc(s, func(r rune) bool {
		return r == ' ' || r == '\t' || r == '\n' || r == '\r' || r == '(' || r == ';'
	})
	if len(fields) == 0 || !sqlWords[strings.ToUpper(fields[0])] {
		return false
	}
	seenSemi := false
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c == '\'' || c == '"' || c == '`' || c == '[':
			end := c
			if c == '[' {
				end = ']'
			}
			if seenSemi {
				return true
			}
			j := i + 1
			for j < len(s) && s[j] != end {
				j++
			}
			i = j
		case c == '-' && i+1 < len(s) && s[i+1] == '-':
			for i < len(s) && s[i] != '\n' {
				i++
			}
		case c == '/' && i+1 < len(s) && s[i+1] == '*':
			j := strings.Index(s[i+2:], "*/")
			if j < 0 {
				return false
			}
			i += j + 3
		case c == ';':
			seenSemi = true
		case c == ' ' || c == '\t' || c == '\n' || c == '\r':
		default:
			if seenSemi {
				return true
			}
		}
	}
	return false
}
