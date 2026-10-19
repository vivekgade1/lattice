// goextract: the tables and writes of the database/sql statements in an rpc's closure (round 5, E5). A
// lexical reader of the statement text, not a SQL parser: a statement it cannot read to a literal is an
// `unread: sql_not_literal`, never a guess. Called from the gRPC walk's fillRPC only; the HTTP walk never
// reads SQL, so every HTTP output keeps its bytes.
package main

import (
	"go/ast"
	"go/token"
	"regexp"
	"slices"
	"sort"
	"strings"
)

// sqlCalls maps a database/sql method to the index of its query argument.
var sqlCalls = map[string]int{"Exec": 0, "Query": 0, "QueryRow": 0, "Prepare": 0,
	"ExecContext": 1, "QueryContext": 1, "QueryRowContext": 1, "PrepareContext": 1}

const sqlOpaque = "\x00" // a part of a statement that is not a constant

var (
	sqlJunk  = regexp.MustCompile(`(?s)--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'`)
	sqlWord  = regexp.MustCompile("(?:\"[^\"]*\"|`[^`]*`|[A-Za-z_\x00][\\w$\x00]*)(?:\\.(?:\"[^\"]*\"|`[^`]*`|[A-Za-z_]\\w*))*|[(),;]")
	sqlVerb  = regexp.MustCompile(`%[-+# 0-9.]*[a-zA-Z%]`)
	sqlStops = " WHERE GROUP ORDER LIMIT HAVING UNION JOIN LEFT RIGHT INNER OUTER CROSS FULL NATURAL ON USING SET VALUES SELECT RETURNING WINDOW OFFSET FOR "
	sqlDDL   = " CREATE ALTER DROP PRAGMA VACUUM BEGIN COMMIT ROLLBACK "
)

// rpcSQL reads the statements of the closure's bodies, and of the implementers the server builds of an
// interface a body calls (D1.4, depth <= 4): the tables, the writes and the statements it could not read.
func (g *gx) rpcSQL(ci *closureInfo) (tables []string, writes []xWrite, unread []xUnread) {
	type item struct {
		fn *Func
		d  int
	}
	var q []item
	seen := map[*Func]bool{}
	add := func(fn *Func, d int) {
		for _, b := range g.bodies(fn) {
			if !seen[b] {
				seen[b] = true
				q = append(q, item{b, d})
			}
		}
	}
	for _, v := range ci.visits {
		for _, b := range v.bodies {
			add(b, 0)
		}
	}
	for ; len(q) > 0; q = q[1:] {
		it := q[0]
		e := g.m.EnvOf(it.fn)
		ast.Inspect(it.fn.Body, func(n ast.Node) bool {
			if _, lit := n.(*ast.FuncLit); lit {
				return false
			}
			c, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			sel, isSel := unparen(c.Fun).(*ast.SelectorExpr)
			if !isSel {
				return true
			}
			if qi, isSQL := sqlCalls[sel.Sel.Name]; isSQL && qi < len(c.Args) && g.sqlRecv(e, sel) {
				text := g.sqlStr(it.fn.File, e, c.Args[qi], 0)
				ts, op, target, opaque := sqlTables(text)
				tables = append(tables, ts...)
				if op != "" && target != "" {
					writes = append(writes, xWrite{Table: target, Op: op, File: it.fn.File.Rel, Line: g.line(c.Pos())})
				}
				if opaque || text == sqlOpaque {
					unread = append(unread, xUnread{File: it.fn.File.Rel, Line: g.line(c.Pos()), What: "sql_not_literal",
						Reason: "the table of the statement `" + g.src(c.Args[qi]) + "` is not a literal"})
				}
			}
			if it.d < 4 && g.calleeOf(e, c) == nil { // an interface call: the implementers the server builds
				for _, m := range g.m.methods[sel.Sel.Name] {
					if curWalk.inst[m.Recv] {
						add(m, it.d+1)
					}
				}
			} else if callee := g.calleeOf(e, c); callee != nil && it.d > 0 && it.d < 4 {
				add(callee, it.d+1)
			}
			return true
		})
	}
	return
}

// fillSQL adds the closure's SQL tables, writes and unread statements to the rpc.
func (g *gx) fillSQL(r *xRPC, ci *closureInfo) {
	tables, writes, unread := g.rpcSQL(ci)
	for _, t := range tables {
		if !slices.Contains(r.Tables, t) {
			r.Tables = append(r.Tables, t)
		}
	}
	sort.Strings(r.Tables)
	r.Writes = append(r.Writes, writes...)
	sort.SliceStable(r.Writes, func(i, j int) bool {
		a, b := r.Writes[i], r.Writes[j]
		return a.File < b.File || a.File == b.File && (a.Line < b.Line || a.Line == b.Line && a.Op < b.Op)
	})
	for _, u := range unread {
		r.Unread = append(r.Unread, u)
		g.addUnread(u.File, u.Line, u.What, u.Reason)
	}
}

// sqlRecv: the receiver is a *sql.DB, *sql.Tx or *sql.Conn, or a module interface with that method taking a string.
func (g *gx) sqlRecv(e *Env, sel *ast.SelectorExpr) bool {
	t := g.m.Infer(e, sel.X).deref()
	if t.Kind == "ext" {
		return t.Ext == "database/sql.DB" || t.Ext == "database/sql.Tx" || t.Ext == "database/sql.Conn"
	}
	if t.Kind == "named" && t.Named != nil && t.Named.Iface != nil {
		for _, m := range t.Named.Iface.Methods.List {
			ft, _ := m.Type.(*ast.FuncType)
			for _, nm := range m.Names {
				if nm.Name == sel.Sel.Name && ft != nil {
					for _, p := range fieldList(ft.Params) {
						if id, ok := p.typ.(*ast.Ident); ok && id.Name == "string" {
							return true
						}
					}
				}
			}
		}
	}
	return false
}

// sqlStr (E5.2) reads a statement: a constant, a `+` of them, or a fmt.Sprintf with a constant format; a
// part that is not a constant is sqlOpaque, which only matters where a table name is expected.
func (g *gx) sqlStr(f *File, e *Env, x ast.Expr, d int) string {
	switch v := unparen(x).(type) {
	case *ast.BinaryExpr:
		if v.Op == token.ADD {
			return g.sqlStr(f, e, v.X, d) + g.sqlStr(f, e, v.Y, d)
		}
	case *ast.CallExpr:
		if p, nm, ok := pkgCall(f, e, v); ok && p == "fmt" && nm == "Sprintf" && len(v.Args) > 0 {
			args := v.Args[1:]
			return sqlVerb.ReplaceAllStringFunc(g.sqlStr(f, e, v.Args[0], d), func(verb string) string {
				switch last := verb[len(verb)-1]; {
				case last == '%':
					return "%"
				case len(args) == 0:
					return sqlOpaque
				case last == 's' || last == 'v' || last == 'q':
					a := args[0]
					args = args[1:]
					return g.sqlStr(f, e, a, d+1)
				default:
					args = args[1:]
					return "0"
				}
			})
		}
	case *ast.Ident:
		if vals := e.valuesOf(v.Name); len(vals) == 1 && d < 3 {
			return g.sqlStr(f, e, vals[0], d+1)
		}
	}
	if s, ok := g.m.constStringDepth(f, e, x, 3); ok {
		return s
	}
	return sqlOpaque
}

// sqlTables (E5.3) lists the tables a statement names after FROM, JOIN, INTO and UPDATE (quotes and schema
// prefixes stripped, CTE names left out, subqueries scanned), its write op and the table written; opaque
// when a table position holds sqlOpaque. A DDL statement names nothing here.
func sqlTables(text string) (tables []string, op, target string, opaque bool) {
	toks := sqlWord.FindAllString(sqlJunk.ReplaceAllString(text, " ? "), -1)
	up := make([]string, len(toks))
	for i, t := range toks {
		up[i] = strings.ToUpper(t)
	}
	at := func(i int) string {
		if i >= 0 && i < len(up) {
			return up[i]
		}
		return ""
	}
	isName := func(i int) bool {
		return i < len(toks) && toks[i] != "(" && toks[i] != ")" && toks[i] != "," && toks[i] != ";"
	}
	cte, depth, stmt := map[string]bool{}, 0, -1
	for i := range toks {
		switch {
		case toks[i] == "(":
			depth++
		case toks[i] == ")":
			depth--
		case at(i+1) == "AS" && at(i+2) == "(" && (at(i-1) == "WITH" || at(i-1) == ","):
			cte[strings.ToLower(toks[i])] = true
		case depth == 0 && stmt < 0 && strings.Contains(" SELECT INSERT REPLACE UPDATE DELETE"+sqlDDL, " "+up[i]+" "):
			stmt = i
		}
	}
	if stmt < 0 || strings.Contains(sqlDDL, " "+up[stmt]+" ") {
		return
	}
	switch kw := up[stmt]; {
	case kw == "INSERT" && (strings.Contains(" "+strings.Join(up, " ")+" ", " DO UPDATE ") || strings.Contains(strings.Join(up, " "), "DUPLICATE KEY UPDATE")), kw == "REPLACE":
		op = "save"
	case kw == "INSERT":
		op = "create"
	case kw == "UPDATE":
		op = "update"
	case kw == "DELETE":
		op = "delete"
	}
	for i := range toks {
		if !strings.Contains(" FROM JOIN INTO UPDATE ", " "+up[i]+" ") || up[i] == "UPDATE" && strings.Contains(" DO KEY FOR ", " "+at(i-1)+" ") {
			continue
		}
		for j := i + 1; isName(j) && !strings.Contains(sqlStops, " "+up[j]+" "); {
			parts := strings.Split(toks[j], ".")
			name := strings.ToLower(strings.Trim(parts[len(parts)-1], "\"`[]"))
			switch {
			case strings.Contains(name, sqlOpaque):
				opaque = true
			case !cte[name]:
				tables = append(tables, name)
				if target == "" && op != "" && i >= stmt {
					target = name
				}
			}
			j++
			if at(j) == "AS" {
				j += 2
			} else if isName(j) && !strings.Contains(sqlStops, " "+up[j]+" ") {
				j++
			}
			if up[i] != "FROM" || at(j) != "," {
				break
			}
			j++
		}
	}
	return
}
