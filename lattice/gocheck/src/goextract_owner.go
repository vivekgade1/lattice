// goextract: owner evidence (D8), transactions and the other facts a closure holds, and
// constant writes to string columns (D9).
package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"reflect"
	"regexp"
	"sort"
	"strings"
)

func reflectTag(s string) reflect.StructTag { return reflect.StructTag(s) }

var idFieldRE = regexp.MustCompile(`(?i)^(user_?id|uid|id)$`)
var placeRE = regexp.MustCompile(`(?i)(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)\s*(!=|<>|<=|>=|=|<|>|not\s+in|in|like)\s*\(?\s*$`)

// ---- the caller -------------------------------------------------------------------------

func (g *gx) ctxCall(e *Env, x ast.Expr) (*ast.CallExpr, string) {
	c, ok := unparen(x).(*ast.CallExpr)
	if !ok {
		return nil, ""
	}
	sel, ok := c.Fun.(*ast.SelectorExpr)
	if !ok || !g.m.isCtx(e, sel.X) {
		return nil, ""
	}
	return c, sel.Sel.Name
}

// callerKeyOf answers the context key a c.Get / MustGet / GetXxx call reads when the
// authenticating middleware sets it, with its kind (struct or scalar).
func (g *gx) callerKeyOf(e *Env, x ast.Expr) (string, string, bool) {
	c, name := g.ctxCall(e, x)
	if c == nil || len(c.Args) < 1 {
		return "", "", false
	}
	switch name {
	case "Get", "MustGet", "GetInt", "GetInt64", "GetUint", "GetUint64", "GetString", "GetBool":
	default:
		return "", "", false
	}
	k, ok := g.m.constString(e, c.Args[0])
	if !ok {
		return "", "", false
	}
	kind, ok := g.callerKeys[k]
	if !ok {
		return "", "", false
	}
	return k, kind, true
}

func (g *gx) isCallerValue(e *Env, v *gxVisit, x ast.Expr) bool {
	switch n := unparen(x).(type) {
	case *ast.Ident:
		return v.callerVals[n.Name]
	case *ast.StarExpr:
		return g.isCallerValue(e, v, n.X)
	case *ast.UnaryExpr:
		return n.Op == token.AND && g.isCallerValue(e, v, n.X)
	case *ast.TypeAssertExpr:
		if g.isCallerValue(e, v, n.X) {
			return true
		}
		_, kind, ok := g.callerKeyOf(e, n.X)
		return ok && kind == "struct"
	case *ast.CallExpr:
		if _, kind, ok := g.callerKeyOf(e, n); ok {
			return kind == "struct"
		}
		if callee := g.m.Callee(e, n); callee != nil {
			return g.callerFuncs[callee] == "struct"
		}
	}
	return false
}

func (g *gx) isCallerID(e *Env, v *gxVisit, x ast.Expr) bool {
	switch n := unparen(x).(type) {
	case *ast.Ident:
		return v.callerIDs[n.Name]
	case *ast.SelectorExpr:
		return idFieldRE.MatchString(n.Sel.Name) && g.isCallerValue(e, v, n.X)
	case *ast.TypeAssertExpr:
		_, kind, ok := g.callerKeyOf(e, n.X)
		return ok && kind == "scalar" || g.isCallerID(e, v, n.X)
	case *ast.CallExpr:
		if _, kind, ok := g.callerKeyOf(e, n); ok {
			return kind == "scalar"
		}
		if callee := g.m.Callee(e, n); callee != nil {
			return g.callerFuncs[callee] == "scalar"
		}
		if id, ok := n.Fun.(*ast.Ident); ok && basics[id.Name] && len(n.Args) == 1 {
			return g.isCallerID(e, v, n.Args[0])
		}
	}
	return false
}

func (g *gx) computeCallerNames(v *gxVisit) {
	if len(g.callerKeys) == 0 && len(g.callerFuncs) == 0 {
		return
	}
	for round := 0; round < 4; round++ {
		changed := false
		for _, b := range v.bodies {
			e := g.m.EnvOf(b)
			ast.Inspect(b.Body, func(n ast.Node) bool {
				switch s := n.(type) {
				case *ast.FuncLit:
					return false
				case *ast.AssignStmt:
					if len(s.Rhs) == 0 {
						return true
					}
					for i, l := range s.Lhs {
						id, ok := l.(*ast.Ident)
						if !ok || id.Name == "_" {
							continue
						}
						var rhs ast.Expr
						switch {
						case len(s.Rhs) == len(s.Lhs):
							rhs = s.Rhs[i]
						case len(s.Rhs) == 1 && i == 0:
							rhs = s.Rhs[0]
						default:
							continue
						}
						if g.isCallerValue(e, v, rhs) && !v.callerVals[id.Name] {
							v.callerVals[id.Name], changed = true, true
						}
						if g.isCallerID(e, v, rhs) && !v.callerIDs[id.Name] {
							v.callerIDs[id.Name], changed = true, true
						}
					}
				}
				return true
			})
		}
		if !changed {
			break
		}
	}
}

// ---- owner evidence ---------------------------------------------------------------------

func (g *gx) addOwner(ci *closureInfo, v *gxVisit, b *Func, pos token.Pos, t *xTable, col, how string) {
	file, line := g.at(v, b, pos)
	if v.entry == nil {
		file, line = b.File.Rel, g.line(pos)
	}
	key := t.Table + "|" + col + "|" + how
	if ci.oseen[key] {
		return
	}
	ci.oseen[key] = true
	ci.owners = append(ci.owners, xOwner{Table: t.Table, Model: t.Struct, Column: col, How: how, Line: line, File: file})
}

// chainModel answers the table a chain's Model(...), Table("...") or destination names.
func (g *gx) chainModel(e *Env, links []chainLink) *xTable {
	for _, l := range links {
		if l.name == "Model" && len(l.call.Args) == 1 {
			if t := g.tables[g.m.Infer(e, l.call.Args[0]).rowType(true)]; t != nil {
				return t
			}
		}
	}
	for _, l := range links {
		if l.name == "Table" && len(l.call.Args) >= 1 {
			if s, ok := g.m.constString(e, l.call.Args[0]); ok {
				if f := strings.Fields(s); len(f) > 0 {
					if t := g.byTable[f[0]]; t != nil {
						return t
					}
				}
			}
		}
	}
	for _, l := range links {
		if chainDestinations[l.name] && len(l.call.Args) >= 1 {
			if t := g.tables[g.m.Infer(e, l.call.Args[0]).rowType(true)]; t != nil {
				return t
			}
		}
	}
	return nil
}

func (g *gx) rootTable(e *Env, links []chainLink, root ast.Expr, depth int) *xTable {
	if t := g.chainModel(e, links); t != nil {
		return t
	}
	if depth <= 0 {
		return nil
	}
	if name := rootIdent(root); name != "" {
		if de := defEnv(e, name); de != nil {
			if vals := de.values[name]; len(vals) == 1 {
				if c, ok := unparen(vals[0]).(*ast.CallExpr); ok {
					if l2, r2 := g.m.gormChain(de, c); l2 != nil {
						return g.rootTable(de, l2, r2, depth-1)
					}
				}
			}
		}
	}
	return nil
}

func (g *gx) scanOwner(ci *closureInfo, v *gxVisit, b *Func, e *Env) {
	if len(g.callerKeys) == 0 && len(g.callerFuncs) == 0 && len(v.callerVals) == 0 && len(v.callerIDs) == 0 {
		return
	}
	m := g.m
	// chains assigned to a local, and the chains rooted at a local
	assigned := map[*ast.CallExpr]string{}
	rooted := map[string][]*xTable{}
	ast.Inspect(b.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		if as, ok := n.(*ast.AssignStmt); ok && len(as.Lhs) == 1 && len(as.Rhs) == 1 {
			if id, ok := as.Lhs[0].(*ast.Ident); ok {
				if c, ok := unparen(as.Rhs[0]).(*ast.CallExpr); ok {
					assigned[c] = id.Name
				}
			}
		}
		if c, ok := n.(*ast.CallExpr); ok {
			if links, root := m.gormChain(e, c); links != nil {
				if name := rootIdent(root); name != "" {
					if t := g.chainModel(e, links); t != nil {
						rooted[name] = append(rooted[name], t)
					}
				}
			}
		}
		return true
	})
	unresolved := func(pos token.Pos, what string) {
		ci.addUnread(b.File.Rel, g.line(pos), "owner", what)
		g.unreadAt(b.File, pos, "owner", what)
	}
	stmtWalk(b.Body, func(call *ast.CallExpr, stack []ast.Node) {
		// only the outermost call of a chain (`.Error` / `.RowsAffected` of a finisher is
		// a field of its result, not a further link)
		if len(stack) >= 3 {
			if sel, ok := stack[len(stack)-2].(*ast.SelectorExpr); ok && sel.X == ast.Expr(call) {
				if outer, ok := stack[len(stack)-3].(*ast.CallExpr); ok && outer.Fun == ast.Expr(sel) {
					return
				}
			}
		}
		links, root := m.gormChain(e, call)
		if links == nil {
			return
		}
		tbl := g.rootTable(e, links, root, 3)
		if tbl == nil {
			if name, ok := assigned[call]; ok && len(rooted[name]) > 0 {
				tbl = rooted[name][0]
			}
		}
		if tbl == nil {
			if name := rootIdent(root); name != "" && len(rooted[name]) > 0 {
				tbl = rooted[name][0]
			}
		}
		for _, l := range links {
			args := l.call.Args
			condAt := -1
			switch l.name {
			case "Where", "Or", "Not", "Having":
				condAt = 0
			case "First", "Take", "Last", "Find", "Delete", "Update", "Updates":
				if len(args) >= 2 {
					condAt = 1
				}
			}
			if condAt >= 0 && condAt < len(args) {
				g.ownerCond(ci, v, b, e, l.call, args, condAt, tbl, unresolved)
			}
			// assignments: a map or struct a write sets
			if gormWrites[l.name] || l.name == "Where" {
				for _, a := range args {
					cl, ok := stripAddr(unparen(a)).(*ast.CompositeLit)
					if !ok {
						continue
					}
					if _, isMap := cl.Type.(*ast.MapType); !isMap {
						continue
					}
					for _, el := range cl.Elts {
						kv, ok := el.(*ast.KeyValueExpr)
						if !ok {
							continue
						}
						k, ok := g.m.constString(e, kv.Key)
						if !ok || !g.isCallerID(e, v, kv.Value) {
							continue
						}
						how := "assign"
						if l.name == "Where" {
							how = "compare"
						}
						if tbl == nil {
							unresolved(kv.Pos(), "table of the map condition on `"+k+"` is not resolved")
							continue
						}
						g.addOwner(ci, v, b, kv.Pos(), tbl, k, how)
					}
				}
			}
		}
	})
	// a model literal, a field assignment, a field comparison
	ast.Inspect(b.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		switch s := n.(type) {
		case *ast.CompositeLit:
			if s.Type == nil {
				return true
			}
			ti := m.Infer(e, s).deref().rowTypeAny()
			t := g.tables[ti]
			if t == nil {
				return true
			}
			for _, el := range s.Elts {
				kv, ok := el.(*ast.KeyValueExpr)
				if !ok {
					continue
				}
				k, ok := kv.Key.(*ast.Ident)
				if !ok || !g.isCallerID(e, v, kv.Value) {
					continue
				}
				if col := t.fieldCols[k.Name]; col != "" {
					g.addOwner(ci, v, b, kv.Pos(), t, col, "assign")
				}
			}
		case *ast.AssignStmt:
			for i, l := range s.Lhs {
				sel, ok := l.(*ast.SelectorExpr)
				if !ok || len(s.Rhs) != len(s.Lhs) || !g.isCallerID(e, v, s.Rhs[i]) {
					continue
				}
				if t := g.tables[m.Infer(e, sel.X).deref().rowTypeAny()]; t != nil {
					if col := t.fieldCols[sel.Sel.Name]; col != "" {
						g.addOwner(ci, v, b, s.Pos(), t, col, "assign")
					}
				}
			}
		case *ast.BinaryExpr:
			if s.Op != token.EQL && s.Op != token.NEQ {
				return true
			}
			for _, pair := range [][2]ast.Expr{{s.X, s.Y}, {s.Y, s.X}} {
				sel, ok := unparen(pair[0]).(*ast.SelectorExpr)
				if !ok || !g.isCallerID(e, v, pair[1]) || g.isCallerValue(e, v, sel.X) {
					continue
				}
				if t := g.tables[m.Infer(e, sel.X).deref().rowTypeAny()]; t != nil {
					if col := t.fieldCols[sel.Sel.Name]; col != "" {
						g.addOwner(ci, v, b, s.Pos(), t, col, "compare")
					}
				}
			}
		}
		return true
	})
}

// ownerCond reads one condition of a chain: a SQL string (`[qual.]col = ?`, `col IN (?)`),
// a map or a struct.
func (g *gx) ownerCond(ci *closureInfo, v *gxVisit, b *Func, e *Env, call *ast.CallExpr, args []ast.Expr, at int, tbl *xTable, unresolved func(token.Pos, string)) {
	text := g.foldString(e, args[at], v.binds)
	if text != "\x00" && strings.Contains(text, "?") {
		k := 0
		for i := 0; i < len(text); i++ {
			if text[i] != '?' {
				continue
			}
			idx := at + 1 + k
			k++
			if idx >= len(args) || !g.isCallerID(e, v, args[idx]) {
				continue
			}
			mm := placeRE.FindStringSubmatch(text[:i])
			if mm == nil {
				continue
			}
			op := strings.ToLower(strings.Join(strings.Fields(mm[3]), " "))
			if op != "=" && op != "in" {
				continue
			}
			t := tbl
			if mm[1] != "" {
				t = g.byTable[mm[1]]
				if t == nil {
					unresolved(call.Pos(), "qualifier `"+mm[1]+"` of `"+mm[2]+"` is not a table of the module")
					continue
				}
			}
			if t == nil {
				unresolved(call.Pos(), "table of the condition on `"+mm[2]+"` is not resolved")
				continue
			}
			g.addOwner(ci, v, b, call.Pos(), t, mm[2], "compare")
		}
		return
	}
	// a struct or map condition
	cl, ok := stripAddr(unparen(args[at])).(*ast.CompositeLit)
	if !ok {
		return
	}
	for _, el := range cl.Elts {
		kv, ok := el.(*ast.KeyValueExpr)
		if !ok || !g.isCallerID(e, v, kv.Value) {
			continue
		}
		col := ""
		t := tbl
		switch key := kv.Key.(type) {
		case *ast.Ident:
			if ti := g.m.Infer(e, cl).deref().rowTypeAny(); g.tables[ti] != nil {
				t = g.tables[ti]
				col = t.fieldCols[key.Name]
			}
		case *ast.BasicLit:
			col, _ = stringLit(key)
		}
		if col == "" {
			continue
		}
		if t == nil {
			unresolved(kv.Pos(), "table of the condition on `"+col+"` is not resolved")
			continue
		}
		g.addOwner(ci, v, b, call.Pos(), t, col, "compare")
	}
}

// ---- tables, transactions, the context --------------------------------------------------

func (g *gx) scanMisc(ci *closureInfo, v *gxVisit, b *Func, e *Env) {
	m := g.m
	addTable := func(ti *TypeInfo) {
		if t := g.tables[ti]; t != nil {
			if ci.usersTbl == "" {
				ci.usersTbl = t.Struct
			}
			ci.tables[t.Struct] = true
		}
	}
	stmtWalk(b.Body, func(call *ast.CallExpr, stack []ast.Node) {
		if links, _ := m.gormChain(e, call); links != nil {
			for _, l := range links {
				if (l.name == "Model" || chainDestinations[l.name]) && len(l.call.Args) >= 1 {
					addTable(m.Infer(e, l.call.Args[0]).rowType(true))
				}
			}
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if !ok {
			return
		}
		name := sel.Sel.Name
		if (name == "Transaction" || name == "Begin") && m.isGorm(e, sel.X) {
			ci.tx = true
		}
		if bindMethods[name] && m.isCtx(e, sel.X) {
			switch name {
			case "ShouldBind", "ShouldBindJSON", "BindJSON", "Bind", "ShouldBindBodyWith", "ShouldBindBodyWithJSON":
				ci.encoding = "json"
			case "ShouldBindForm", "ShouldBindWith":
				if ci.encoding == "" {
					ci.encoding = "form"
				}
			}
		}
		if m.isCtx(e, sel.X) {
			switch name {
			case "PostForm", "DefaultPostForm", "GetPostForm":
				if ci.encoding == "" {
					ci.encoding = "form"
				}
			case "Set":
				if len(call.Args) == 2 {
					if k, ok := m.constString(e, call.Args[0]); ok {
						t := m.Infer(e, call.Args[1]).deref()
						ci.sets = append(ci.sets, setCall{key: k, scalar: t.rowTypeAny() == nil})
					}
				}
			case "GetHeader", "Header":
				if len(call.Args) >= 1 {
					if s, ok := m.constString(e, call.Args[0]); ok && strings.EqualFold(s, "Authorization") {
						ci.authHdr = true
					}
				}
			case "JSON", "IndentedJSON":
				if len(call.Args) == 2 {
					if st, ok := g.statusSet(e, call.Args[0], v.binds); ok && len(st) > 0 && st[0] >= 200 && st[0] < 300 && ci.jsonTok == "" {
						ci.jsonTok = g.tokenKey(e, call.Args[1])
					}
				}
			}
		}
		if path, ok := m.importOf(e, sel.X); ok && strings.Contains(path, "bcrypt") && name == "CompareHashAndPassword" && len(call.Args) >= 1 && ci.bcrypt == nil {
			ast.Inspect(call.Args[0], func(n ast.Node) bool {
				if ci.bcrypt != nil {
					return false
				}
				if s, ok := n.(*ast.SelectorExpr); ok {
					if t := g.tables[m.Infer(e, s.X).deref().rowTypeAny()]; t != nil {
						if col := t.fieldCols[s.Sel.Name]; col != "" {
							file, line := g.at(v, b, call.Pos())
							_ = file
							ci.bcrypt = &bcryptUse{table: t.Struct, column: col, line: line}
						}
					}
				}
				return true
			})
		}
	})
	ast.Inspect(b.Body, func(n ast.Node) bool {
		switch s := n.(type) {
		case *ast.FuncLit:
			return false
		case *ast.BasicLit:
			if str, ok := stringLit(s); ok && strings.Contains(strings.ToLower(str), "bearer") {
				ci.bearer = true
			}
		case *ast.CompositeLit:
			if s.Type != nil {
				addTable(m.Infer(e, s).deref().rowTypeAny())
			}
		case *ast.ValueSpec:
			if s.Type != nil {
				if t := m.TypeExpr(b.File, s.Type); t != nil {
					addTable(t.rowType(true))
				}
			}
		}
		return true
	})
}

// tokenKey answers the token-like key of a login answer: a struct's json tag or a map
// literal's key (access_token, token, jwt).
func (g *gx) tokenKey(e *Env, x ast.Expr) string {
	pick := func(name string) bool {
		n := strings.ToLower(name)
		return n == "access_token" || n == "token" || n == "jwt" || n == "id_token" || n == "accesstoken"
	}
	if cl, ok := stripAddr(unparen(x)).(*ast.CompositeLit); ok {
		for _, el := range cl.Elts {
			if kv, ok := el.(*ast.KeyValueExpr); ok {
				if k, ok := stringLit(kv.Key); ok && pick(k) {
					return k
				}
			}
		}
	}
	if ti := g.m.Infer(e, x).deref().rowTypeAny(); ti != nil {
		for _, f := range g.m.Fields(ti) {
			if n := jsonName(f); pick(n) {
				return n
			}
		}
	}
	return ""
}

// ---- constant writes to string columns (D9) ---------------------------------------------

type xStatusW struct {
	Table     string `json:"table"`
	Model     string `json:"model"`
	Column    string `json:"column"`
	Value     string `json:"value"`
	Root      string `json:"root"`
	File      string `json:"file"`
	Line      int    `json:"line"`
	ValueLine int    `json:"value_line"`
	Guard     string `json:"guard"`
	In        string `json:"in"`
}

type wsite struct {
	table, col string
	file       string
	line       int
	vals       []wval
	nonconst   bool
	expr       string
	fn         *Func
}

type wval struct {
	v     string
	line  int
	guard string
}

// valueAssigns answers every single-valued assignment of the declared function (its
// literals included), by name, with the condition it sits under.
type assign struct {
	x     ast.Expr
	line  int
	guard string
	e     *Env
}

func (g *gx) collectAssigns(top *Func) map[string][]assign {
	out := map[string][]assign{}
	for _, b := range g.bodies(top) {
		e := g.m.EnvOf(b)
		var stack []ast.Node
		ast.Inspect(b.Body, func(n ast.Node) bool {
			if n == nil {
				stack = stack[:len(stack)-1]
				return true
			}
			if _, ok := n.(*ast.FuncLit); ok && n != ast.Node(b.Lit) {
				return false
			}
			stack = append(stack, n)
			switch s := n.(type) {
			case *ast.AssignStmt:
				if (s.Tok == token.ASSIGN || s.Tok == token.DEFINE) && len(s.Lhs) == len(s.Rhs) {
					for i, l := range s.Lhs {
						if id, ok := l.(*ast.Ident); ok {
							out[id.Name] = append(out[id.Name], assign{s.Rhs[i], g.line(s.Pos()), g.guardOf(stack), e})
						}
					}
				}
			case *ast.ValueSpec:
				if len(s.Values) == len(s.Names) {
					for i, nm := range s.Names {
						out[nm.Name] = append(out[nm.Name], assign{s.Values[i], g.line(s.Pos()), g.guardOf(stack), e})
					}
				}
			}
			return true
		})
	}
	return out
}

// guardOf answers the innermost condition a stack sits under, as source text.
func (g *gx) guardOf(stack []ast.Node) string {
	for i := len(stack) - 2; i >= 0; i-- {
		switch s := stack[i].(type) {
		case *ast.CaseClause:
			if len(s.List) == 0 {
				return "default"
			}
			var xs []string
			for _, x := range s.List {
				xs = append(xs, g.src(x))
			}
			return strings.Join(xs, ", ")
		case *ast.IfStmt:
			child := stack[i+1]
			if child == ast.Node(s.Body) {
				return g.src(s.Cond)
			}
			if s.Else != nil && child == ast.Node(s.Else) {
				return "!(" + g.src(s.Cond) + ")"
			}
		}
	}
	return ""
}

func (g *gx) constValues(e *Env, x ast.Expr, assigns map[string][]assign, depth int) ([]wval, bool) {
	x = unparen(x)
	if s, ok := g.m.constString(e, x); ok {
		return []wval{{v: s}}, true
	}
	id, ok := x.(*ast.Ident)
	if !ok || depth <= 0 {
		return nil, false
	}
	as := assigns[id.Name]
	if len(as) == 0 {
		return nil, false
	}
	var out []wval
	seen := map[string]bool{}
	for _, a := range as {
		vs, ok := g.constValues(a.e, a.x, assigns, depth-1)
		if !ok {
			return nil, false
		}
		for _, v := range vs {
			line, guard := v.line, v.guard
			if line == 0 {
				line, guard = a.line, a.guard
			}
			if !seen[v.v] {
				seen[v.v] = true
				out = append(out, wval{v: v.v, line: line, guard: guard})
			}
		}
	}
	return out, true
}

func (g *gx) findStatusWrites() {
	m := g.m
	var sites []*wsite
	for _, top := range m.Funcs {
		assigns := g.collectAssigns(top)
		for _, b := range g.bodies(top) {
			e := m.EnvOf(b)
			add := func(t *xTable, col string, val ast.Expr, pos token.Pos) {
				if t == nil || col == "" || t.colTypes[col] != "Str" {
					return
				}
				vals, ok := g.constValues(e, val, assigns, 3)
				s := &wsite{table: t.Struct, col: col, file: b.File.Rel, line: g.line(pos), fn: top, expr: g.src(val)}
				if ok {
					s.vals = vals
				} else {
					s.nonconst = true
				}
				sites = append(sites, s)
			}
			ast.Inspect(b.Body, func(n ast.Node) bool {
				if _, ok := n.(*ast.FuncLit); ok && n != ast.Node(b.Lit) {
					return false
				}
				switch s := n.(type) {
				case *ast.CompositeLit:
					if s.Type == nil {
						return true
					}
					if t := g.tables[m.Infer(e, s).deref().rowTypeAny()]; t != nil {
						for _, el := range s.Elts {
							if kv, ok := el.(*ast.KeyValueExpr); ok {
								if k, ok := kv.Key.(*ast.Ident); ok {
									add(t, t.fieldCols[k.Name], kv.Value, kv.Pos())
								}
							}
						}
					}
				case *ast.AssignStmt:
					if len(s.Lhs) == len(s.Rhs) {
						for i, l := range s.Lhs {
							if sel, ok := l.(*ast.SelectorExpr); ok {
								if t := g.tables[m.Infer(e, sel.X).deref().rowTypeAny()]; t != nil {
									add(t, t.fieldCols[sel.Sel.Name], s.Rhs[i], s.Pos())
								}
							}
						}
					}
				case *ast.CallExpr:
					links, root := m.gormChain(e, s)
					if links == nil {
						return true
					}
					last := links[len(links)-1]
					t := g.rootTable(e, links, root, 3)
					switch last.name {
					case "Update", "UpdateColumn":
						if len(last.call.Args) == 2 && t != nil {
							if col, ok := m.constString(e, last.call.Args[0]); ok {
								add(t, col, last.call.Args[1], s.Pos())
							}
						}
					case "Updates", "UpdateColumns":
						if len(last.call.Args) == 1 && t != nil {
							if cl, ok := stripAddr(unparen(last.call.Args[0])).(*ast.CompositeLit); ok {
								if _, isMap := cl.Type.(*ast.MapType); isMap {
									for _, el := range cl.Elts {
										if kv, ok := el.(*ast.KeyValueExpr); ok {
											if col, ok := m.constString(e, kv.Key); ok {
												add(t, col, kv.Value, kv.Pos())
											}
										}
									}
								}
							}
						}
					}
				}
				return true
			})
		}
	}
	// a column is a status candidate when some write sets a constant
	hasConst := map[string]bool{}
	for _, s := range sites {
		if len(s.vals) > 0 {
			hasConst[s.table+"."+s.col] = true
		}
	}
	rootsOf := g.routeRoots
	seen := map[string]bool{}
	for _, s := range sites {
		key := s.table + "." + s.col
		if s.nonconst {
			if hasConst[key] {
				g.addUnread(s.file, s.line, "status_write", "value of "+key+" is not a constant: `"+s.expr+"`")
			}
			continue
		}
		t := g.byStruct[s.table]
		roots := rootsOf(s.fn)
		for _, r := range roots {
			for _, val := range s.vals {
				w := &xStatusW{Table: t.Table, Model: s.table, Column: s.col, Value: val.v, Root: r,
					File: s.file, Line: s.line, ValueLine: val.line, Guard: val.guard, In: qual(s.fn)}
				if w.ValueLine == 0 {
					w.ValueLine = s.line
				}
				k := fmt.Sprintf("%s|%s|%s|%s|%d|%d", w.Table, w.Column, w.Value, w.Root, w.Line, w.ValueLine)
				if seen[k] {
					continue
				}
				seen[k] = true
				g.res.StatusWrites = append(g.res.StatusWrites, w)
			}
		}
	}
	sort.SliceStable(g.res.StatusWrites, func(i, j int) bool {
		a, b := g.res.StatusWrites[i], g.res.StatusWrites[j]
		if a.File != b.File {
			return a.File < b.File
		}
		if a.Line != b.Line {
			return a.Line < b.Line
		}
		if a.Value != b.Value {
			return a.Value < b.Value
		}
		return a.Root < b.Root
	})
	if g.res.StatusWrites == nil {
		g.res.StatusWrites = []*xStatusW{}
	}
}

// routeRoots answers the roots reaching a function: the routes whose closure holds it,
// else the first non-main function on the shortest call path from main.
func (g *gx) routeRoots(top *Func) []string {
	var out []string
	for _, r := range g.routeList {
		if r.closure != nil && r.closure.funcs[top] {
			out = append(out, r.out.Method+" "+r.out.PathTemplate)
		}
	}
	if len(out) > 0 {
		sort.Strings(out)
		return dedupStrings(out)
	}
	if root := g.mainRoot(top); root != "" {
		return []string{root}
	}
	return []string{"(not reached from a route or main)"}
}

func dedupStrings(in []string) []string {
	var out []string
	for i, s := range in {
		if i == 0 || s != in[i-1] {
			out = append(out, s)
		}
	}
	return out
}

// mainRoot answers the function reached from main (by direct calls) that leads to top.
func (g *gx) mainRoot(top *Func) string {
	if !g.hasMain {
		return ""
	}
	if g.mainParent == nil {
		g.mainParent = map[*Func]*Func{}
		var queue []*Func
		for _, mf := range g.mainFns {
			g.mainParent[mf] = nil
			queue = append(queue, mf)
		}
		for len(queue) > 0 {
			fn := queue[0]
			queue = queue[1:]
			for _, b := range g.bodies(fn) {
				e := g.m.EnvOf(b)
				stmtWalk(b.Body, func(call *ast.CallExpr, _ []ast.Node) {
					callee := g.m.Callee(e, call)
					if callee == nil || callee.Lit != nil {
						return
					}
					if _, seen := g.mainParent[callee]; !seen {
						g.mainParent[callee] = fn
						queue = append(queue, callee)
					}
				})
			}
		}
	}
	if _, ok := g.mainParent[top]; !ok {
		return ""
	}
	var path []*Func
	for f := top; f != nil; f = g.mainParent[f] {
		path = append([]*Func{f}, path...)
	}
	for _, f := range path[1:] {
		if f.Pkg().Name != "main" {
			return qual(f)
		}
	}
	return qual(path[len(path)-1])
}
