// goextract: status constructors, sentinels and the error writer (D6).
package main

import (
	"go/ast"
	"go/token"
	"sort"
	"strconv"
	"strings"
)

type cval struct {
	isStr, isInt bool
	s            string
	n            int
}

type binds map[string]cval

func (b binds) sig() string {
	if len(b) == 0 {
		return ""
	}
	keys := make([]string, 0, len(b))
	for k := range b {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var sb strings.Builder
	for _, k := range keys {
		v := b[k]
		sb.WriteString(k)
		if v.isStr {
			sb.WriteString("=s:" + v.s)
		} else {
			sb.WriteString("=i:" + strconv.Itoa(v.n))
		}
		sb.WriteByte(';')
	}
	return sb.String()
}

// evalConst reads an expression as a string or integer constant, with a function's
// parameters bound to the constants a call site passed.
func (g *gx) evalConst(e *Env, x ast.Expr, b binds) (cval, bool) {
	x = unparen(x)
	if id, ok := x.(*ast.Ident); ok && b != nil {
		if v, ok := b[id.Name]; ok {
			return v, true
		}
	}
	if be, ok := x.(*ast.BinaryExpr); ok && be.Op == token.ADD {
		l, ok1 := g.evalConst(e, be.X, b)
		r, ok2 := g.evalConst(e, be.Y, b)
		if ok1 && ok2 && l.isStr && r.isStr {
			return cval{isStr: true, s: l.s + r.s}, true
		}
		return cval{}, false
	}
	if s, ok := g.m.constString(e, x); ok {
		return cval{isStr: true, s: s}, true
	}
	if n := g.m.constInt(e, x); n >= 0 {
		return cval{isInt: true, n: n}, true
	}
	return cval{}, false
}

// foldString folds a string expression of constants and the bound parameters; unknown
// pieces become \x00.
func (g *gx) foldString(e *Env, x ast.Expr, b binds) string {
	x = unparen(x)
	if be, ok := x.(*ast.BinaryExpr); ok && be.Op == token.ADD {
		return g.foldString(e, be.X, b) + g.foldString(e, be.Y, b)
	}
	if v, ok := g.evalConst(e, x, b); ok && v.isStr {
		return v.s
	}
	return "\x00"
}

// ---- constructors -----------------------------------------------------------------------

type ctorArg struct {
	kind int // 0 unknown, 1 constant, 2 parameter
	n    int
	s    string
	idx  int
}

type ctorSum struct {
	status ctorArg
	code   ctorArg
}

type xCtor struct {
	Func        string  `json:"func"`
	File        string  `json:"file"`
	Line        int     `json:"line"`
	Status      *int    `json:"status"`
	StatusParam *int    `json:"status_param"`
	Code        *string `json:"code"`
	CodeParam   *int    `json:"code_param"`
}

var statusFieldNames = []string{"Status", "HTTPStatus", "StatusCode", "Code"}

// statusField answers the int field of a struct holding an HTTP status.
func (g *gx) statusField(ti *TypeInfo) string {
	if ti == nil || ti.Struct == nil {
		return ""
	}
	fs := g.m.Fields(ti)
	for _, want := range statusFieldNames {
		for _, f := range fs {
			if f.name != want {
				continue
			}
			if t := g.m.TypeExpr(f.file, f.typ); t.Kind == "basic" && strings.HasPrefix(t.Basic, "int") {
				return want
			}
		}
	}
	return ""
}

func (g *gx) hasStringCode(ti *TypeInfo) bool {
	for _, f := range g.m.Fields(ti) {
		if f.name == "Code" {
			t := g.m.TypeExpr(f.file, f.typ)
			return t.Kind == "basic" && t.Basic == "string"
		}
	}
	return false
}

func (g *gx) argOf(fn *Func, e *Env, x ast.Expr) ctorArg {
	x = unparen(x)
	if id, ok := x.(*ast.Ident); ok {
		for i, p := range fn.Params() {
			if p.name == id.Name {
				if de := defEnv(e, id.Name); de != nil && de.fn == fn {
					return ctorArg{kind: 2, idx: i}
				}
			}
		}
	}
	if v, ok := g.evalConst(e, x, nil); ok {
		return ctorArg{kind: 1, n: v.n, s: v.s}
	}
	return ctorArg{}
}

// ctorOf answers fn's constructor summary: every return is a struct value with a status
// (and code), a constant or a parameter, or a call of another constructor.
func (g *gx) ctorOf(fn *Func) *ctorSum {
	if fn == nil || fn.Lit != nil {
		return nil
	}
	if s, ok := g.ctorMemo[fn]; ok {
		return s
	}
	if g.ctorBusy[fn] {
		return nil
	}
	g.ctorBusy[fn] = true
	defer delete(g.ctorBusy, fn)
	var rets []*ast.ReturnStmt
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		if r, ok := n.(*ast.ReturnStmt); ok {
			rets = append(rets, r)
		}
		return true
	})
	var sum *ctorSum
	ok := len(rets) > 0
	for _, r := range rets {
		if len(r.Results) != 1 {
			ok = false
			break
		}
		s := g.ctorValue(fn, g.m.EnvOf(fn), r.Results[0], 3)
		if s == nil {
			ok = false
			break
		}
		if sum == nil {
			sum = s
		} else if *sum != *s {
			ok = false
			break
		}
	}
	if !ok {
		sum = nil
	}
	g.ctorMemo[fn] = sum
	return sum
}

func (g *gx) ctorValue(fn *Func, e *Env, x ast.Expr, depth int) *ctorSum {
	if depth < 0 {
		return nil
	}
	x = unparen(x)
	if u, ok := x.(*ast.UnaryExpr); ok && u.Op == token.AND {
		x = unparen(u.X)
	}
	switch v := x.(type) {
	case *ast.CompositeLit:
		if v.Type == nil {
			return nil
		}
		ti := g.m.Infer(e, v).deref().rowTypeAny()
		sf := g.statusField(ti)
		if sf == "" {
			return nil
		}
		var sum ctorSum
		for _, el := range v.Elts {
			kv, ok := el.(*ast.KeyValueExpr)
			if !ok {
				continue
			}
			k, ok := kv.Key.(*ast.Ident)
			if !ok {
				continue
			}
			switch {
			case k.Name == sf:
				sum.status = g.argOf(fn, e, kv.Value)
			case k.Name == "Code" && sf != "Code" && g.hasStringCode(ti):
				sum.code = g.argOf(fn, e, kv.Value)
			}
		}
		if sum.status.kind == 0 {
			return nil
		}
		return &sum
	case *ast.CallExpr:
		callee := g.m.Callee(e, v)
		inner := g.ctorOf(callee)
		if inner == nil {
			return nil
		}
		var sum ctorSum
		mapArg := func(a ctorArg) ctorArg {
			if a.kind != 2 {
				return a
			}
			if a.idx < len(v.Args) {
				return g.argOf(fn, e, v.Args[a.idx])
			}
			return ctorArg{}
		}
		sum.status, sum.code = mapArg(inner.status), mapArg(inner.code)
		if sum.status.kind == 0 {
			return nil
		}
		return &sum
	case *ast.Ident:
		de := defEnv(e, v.Name)
		if de != nil {
			if vals := de.values[v.Name]; len(vals) == 1 {
				return g.ctorValue(fn, de, vals[0], depth-1)
			}
		}
	}
	return nil
}

// ctorHit is a constructor call read at a call site.
type ctorHit struct {
	status   int
	statusOK bool
	code     string
	codeOK   bool
}

func (g *gx) ctorCall(e *Env, call *ast.CallExpr, sum *ctorSum, b binds) ctorHit {
	var h ctorHit
	get := func(a ctorArg) (cval, bool) {
		switch a.kind {
		case 1:
			return cval{isInt: true, n: a.n, isStr: a.s != "", s: a.s}, true
		case 2:
			if a.idx < len(call.Args) {
				return g.evalConst(e, call.Args[a.idx], b)
			}
		}
		return cval{}, false
	}
	if v, ok := get(sum.status); ok && v.isInt {
		h.status, h.statusOK = v.n, true
	}
	if v, ok := get(sum.code); ok && v.isStr {
		h.code, h.codeOK = v.s, true
	}
	return h
}

// ctorLit reads a struct literal carrying a status (`&httpapi.Error{Status: 404}`).
func (g *gx) ctorLit(e *Env, lit *ast.CompositeLit, b binds) (ctorHit, bool) {
	var h ctorHit
	if lit.Type == nil {
		return h, false
	}
	ti := g.m.Infer(e, lit).deref().rowTypeAny()
	sf := g.statusField(ti)
	if sf == "" {
		return h, false
	}
	found := false
	for _, el := range lit.Elts {
		kv, ok := el.(*ast.KeyValueExpr)
		if !ok {
			continue
		}
		k, ok := kv.Key.(*ast.Ident)
		if !ok {
			continue
		}
		switch {
		case k.Name == sf:
			found = true
			if v, ok := g.evalConst(e, kv.Value, b); ok && v.isInt {
				h.status, h.statusOK = v.n, true
			}
		case k.Name == "Code" && sf != "Code":
			if v, ok := g.evalConst(e, kv.Value, b); ok && v.isStr {
				h.code, h.codeOK = v.s, true
			}
		}
	}
	return h, found
}

func (g *gx) findConstructors() {
	var fns []*Func
	for _, fn := range g.m.Funcs {
		if g.ctorOf(fn) != nil {
			fns = append(fns, fn)
		}
	}
	sort.SliceStable(fns, func(i, j int) bool { return g.funcKey(fns[i]) < g.funcKey(fns[j]) })
	for _, fn := range fns {
		s := g.ctorOf(fn)
		c := &xCtor{Func: qual(fn), File: fn.File.Rel, Line: g.line(fn.Pos)}
		if s.status.kind == 1 {
			n := s.status.n
			c.Status = &n
		} else {
			i := s.status.idx
			c.StatusParam = &i
		}
		switch s.code.kind {
		case 1:
			v := s.code.s
			c.Code = &v
		case 2:
			i := s.code.idx
			c.CodeParam = &i
		}
		g.res.Constructors = append(g.res.Constructors, c)
	}
	if g.res.Constructors == nil {
		g.res.Constructors = []*xCtor{}
	}
}

// ---- sentinels and the error writer -----------------------------------------------------

type xSentinel struct {
	Name    string  `json:"name"`
	Package string  `json:"package"`
	File    string  `json:"file"`
	Line    int     `json:"line"`
	Message *string `json:"message"`
	Status  *int    `json:"status"`
	Code    *string `json:"code"`
	Writer  string  `json:"writer,omitempty"`
	key     string
}

type xWriteMap struct {
	Sentinel string  `json:"sentinel"`
	Status   int     `json:"status"`
	Code     *string `json:"code"`
	Line     int     `json:"line"`
}

type xWriter struct {
	Func     string      `json:"func"`
	File     string      `json:"file"`
	Line     int         `json:"line"`
	Wrappers []string    `json:"wrappers"`
	Maps     []xWriteMap `json:"maps"`
	AsTypes  []string    `json:"as_types"`
	CatchAll *xWriteMap  `json:"catch_all"`
}

func (g *gx) findSentinels() {
	for _, pkg := range g.m.Pkgs {
		var names []string
		for n := range pkg.Vars {
			names = append(names, n)
		}
		sort.Strings(names)
		for _, n := range names {
			vi := pkg.Vars[n]
			call, ok := vi.value.(*ast.CallExpr)
			if !ok {
				continue
			}
			sel, ok := call.Fun.(*ast.SelectorExpr)
			if !ok {
				continue
			}
			x, ok := sel.X.(*ast.Ident)
			if !ok {
				continue
			}
			path := vi.file.Imports[x.Name]
			if !(path == "errors" && sel.Sel.Name == "New") && !(path == "fmt" && sel.Sel.Name == "Errorf") {
				continue
			}
			s := &xSentinel{Name: n, Package: pkg.ImportPath, File: vi.file.Rel, Line: g.line(call.Pos())}
			if s.Package == "" {
				s.Package = pkg.Rel
			}
			if len(call.Args) > 0 {
				if msg, ok := stringLit(call.Args[0]); ok {
					s.Message = &msg
				}
			}
			s.key = s.Package + "." + n
			g.sentinels[s.key] = s
		}
	}
}

// sentinelKey answers the key of a sentinel error expression (a module one or an
// external package's, like gorm.ErrRecordNotFound), "" when x is not one.
func (g *gx) sentinelKey(e *Env, x ast.Expr) string {
	switch v := unparen(x).(type) {
	case *ast.Ident:
		if _, local := e.lookup(v.Name); local {
			return ""
		}
		pkg := e.fn.File.Pkg
		if _, ok := pkg.Vars[v.Name]; ok {
			p := pkg.ImportPath
			if p == "" {
				p = pkg.Rel
			}
			if s := g.sentinels[p+"."+v.Name]; s != nil {
				return s.key
			}
		}
	case *ast.SelectorExpr:
		path, ok := g.m.importOf(e, v.X)
		if !ok {
			return ""
		}
		if pkg := g.m.ByImport[path]; pkg != nil {
			if s := g.sentinels[path+"."+v.Sel.Name]; s != nil {
				return s.key
			}
			return ""
		}
		if strings.HasPrefix(v.Sel.Name, "Err") {
			return path + "." + v.Sel.Name
		}
	}
	return ""
}

// writerOf reads an error writer: `errors.Is(err, ErrX)` clauses and the constructor
// each answers with; the clause that catches the rest.
func (g *gx) writerOf(fn *Func) *xWriter {
	if w, ok := g.writers[fn]; ok {
		return w
	}
	m := g.m
	w := &xWriter{Func: qual(fn), File: fn.File.Rel, Line: g.line(fn.Pos), Wrappers: []string{},
		Maps: []xWriteMap{}, AsTypes: []string{}}
	g.writers[fn] = w
	e := m.EnvOf(fn)
	mapped := map[ast.Node]bool{}
	type hit struct {
		call *ast.CallExpr
		h    ctorHit
		def  bool
	}
	var hits []hit
	// the status a clause body answers with
	clauseStatus := func(body []ast.Stmt) (ctorHit, bool) {
		var out ctorHit
		found := false
		for _, st := range body {
			ast.Inspect(st, func(n ast.Node) bool {
				if found {
					return false
				}
				if _, ok := n.(*ast.FuncLit); ok {
					return false
				}
				switch c := n.(type) {
				case *ast.CallExpr:
					if callee := m.Callee(e, c); callee != nil {
						if sum := g.ctorOf(callee); sum != nil {
							if h := g.ctorCall(e, c, sum, nil); h.statusOK {
								out, found = h, true
								return false
							}
						}
					}
					if sel, ok := c.Fun.(*ast.SelectorExpr); ok && refusalMethods[sel.Sel.Name] && len(c.Args) >= 1 {
						if v, ok := g.evalConst(e, c.Args[0], nil); ok && v.isInt {
							out, found = ctorHit{status: v.n, statusOK: true}, true
							if code, ok := g.codeOfArgs(e, c.Args[1:], nil); ok {
								out.code, out.codeOK = code, true
							}
							return false
						}
					}
				case *ast.CompositeLit:
					if h, ok := g.ctorLit(e, c, nil); ok && h.statusOK {
						out, found = h, true
						return false
					}
				}
				return true
			})
		}
		return out, found
	}
	stmtWalk(fn.Body, func(call *ast.CallExpr, stack []ast.Node) {
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if !ok || len(call.Args) != 2 {
			return
		}
		path, ok := m.importOf(e, sel.X)
		if !ok || path != "errors" {
			return
		}
		// the innermost clause the call is a condition of
		var body []ast.Stmt
		var clause ast.Node
		for i := len(stack) - 2; i >= 0; i-- {
			switch c := stack[i].(type) {
			case *ast.CaseClause:
				body, clause = c.Body, c
			case *ast.IfStmt:
				if i+1 < len(stack) && stack[i+1] != ast.Node(c.Body) {
					body, clause = c.Body.List, c
				}
			}
			if clause != nil {
				break
			}
		}
		switch sel.Sel.Name {
		case "Is":
			key := g.sentinelKey(e, call.Args[1])
			if key == "" || clause == nil {
				return
			}
			if h, ok := clauseStatus(body); ok {
				mapped[clause] = true
				wm := xWriteMap{Sentinel: key, Status: h.status, Line: g.line(call.Pos())}
				if h.codeOK {
					c := h.code
					wm.Code = &c
				}
				w.Maps = append(w.Maps, wm)
			}
		case "As":
			if u, ok := unparen(call.Args[1]).(*ast.UnaryExpr); ok {
				if t := m.Infer(e, u.X).deref(); t.rowTypeAny() != nil {
					w.AsTypes = append(w.AsTypes, t.rowTypeAny().Name)
				}
			}
			if clause != nil {
				mapped[clause] = true
			}
		}
	})
	// the catch-all: the default clause, else the last answer outside the mapped clauses
	var defaultBody []ast.Stmt
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if cc, ok := n.(*ast.CaseClause); ok && cc.List == nil && !mapped[cc] {
			defaultBody = cc.Body
		}
		return true
	})
	if defaultBody != nil {
		if h, ok := clauseStatus(defaultBody); ok {
			hits = append(hits, hit{h: h, def: true})
		}
	}
	if len(hits) == 0 {
		var last *ctorHit
		stmtWalk(fn.Body, func(call *ast.CallExpr, stack []ast.Node) {
			for _, n := range stack {
				if mapped[n] {
					return
				}
			}
			if callee := m.Callee(e, call); callee != nil {
				if sum := g.ctorOf(callee); sum != nil {
					if h := g.ctorCall(e, call, sum, nil); h.statusOK {
						hh := h
						last = &hh
					}
				}
			}
		})
		if last != nil {
			hits = append(hits, hit{h: *last})
		}
	}
	if len(hits) > 0 {
		wm := &xWriteMap{Sentinel: "*", Status: hits[0].h.status}
		if hits[0].h.codeOK {
			c := hits[0].h.code
			wm.Code = &c
		}
		w.CatchAll = wm
	}
	sort.SliceStable(w.Maps, func(i, j int) bool { return w.Maps[i].Line < w.Maps[j].Line })
	return w
}

// writerTarget answers what the error writers answer for a sentinel.
func (g *gx) writerTarget(key string) (xWriteMap, *xWriter, bool) {
	var ws []*xWriter
	for _, w := range g.writers {
		ws = append(ws, w)
	}
	sort.Slice(ws, func(i, j int) bool { return ws[i].File+ws[i].Func < ws[j].File+ws[j].Func })
	for _, w := range ws {
		for _, mp := range w.Maps {
			if mp.Sentinel == key {
				return mp, w, true
			}
		}
	}
	return xWriteMap{}, nil, false
}
