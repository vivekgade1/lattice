// goextract_workflow: the opt-in facts the workflow layer reads (`gocheck extract` with
// {"workflow": true}). The default output is untouched; these are extra top-level keys:
//
//	param_uses     per route: a request value (a path parameter or a body key) reaches a
//	               column, as a comparison (`id = ?`) or an assignment (`ID: id`)
//	creates        per route: a Create/Save of a new literal of a table, and where its key
//	               comes from (a body parameter or the server)
//	param_literals per route: the string constants a request field is compared to
//	status_facts   per constant write to a string column: create or update, and the states
//	               it is written from
//	workflow_unread what these analyses did not read
//
// The values are followed (taint) through locals, tuple assignments, struct fields and
// into module functions (depth 4), as Spike A's owner evidence follows the caller's id.
// Nothing is inferred from a name: a flow the walker cannot follow is an unread item.
package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"sort"
	"strings"
)

type xParamUse struct {
	Route  string `json:"route"`
	Param  string `json:"param"`
	Source string `json:"source"`
	Table  string `json:"table"`
	Model  string `json:"model"`
	Column string `json:"column"`
	How    string `json:"how"`
	File   string `json:"file"`
	Line   int    `json:"line"`
	In     string `json:"in"`
}

type xCreate struct {
	Route   string `json:"route"`
	Table   string `json:"table"`
	Model   string `json:"model"`
	Key     string `json:"key"`
	KeyFrom string `json:"key_from"`
	File    string `json:"file"`
	Line    int    `json:"line"`
	In      string `json:"in"`
}

type xLiteral struct {
	Route  string   `json:"route"`
	Param  string   `json:"param"`
	Source string   `json:"source"`
	Values []string `json:"values"`
	File   string   `json:"file"`
	Line   int      `json:"line"`
}

type xStatusFact struct {
	Table  string   `json:"table"`
	Model  string   `json:"model"`
	Column string   `json:"column"`
	File   string   `json:"file"`
	Line   int      `json:"line"`
	Kind   string   `json:"kind"`
	From   []string `json:"from"`
	FromAt []xLoc   `json:"from_at"`
	In     string   `json:"in"`
}

// xWorkflow is the default result plus the workflow keys (the embedded fields come first,
// so the default part of the document is the default document).
type xWorkflow struct {
	*xResult
	ParamUses  []xParamUse   `json:"param_uses"`
	Creates    []xCreate     `json:"creates"`
	Literals   []xLiteral    `json:"param_literals"`
	Facts      []xStatusFact `json:"status_facts"`
	WfUnread   []xUnread     `json:"workflow_unread"`
	WfNotes    []string      `json:"workflow_notes"`
	wfUnreadOK map[string]bool
}

// extractWorkflow is extractModule followed by the workflow analyses (the same steps in
// the same order, so the default part is the default document).
func extractWorkflow(root string) (*xWorkflow, error) {
	m, dropped, err := loadForExtract(root)
	if err != nil {
		return nil, err
	}
	g := newGx(m)
	for _, d := range dropped {
		g.res.Excluded = append(g.res.Excluded, xExcluded{
			Kind: "file", What: "file", File: d, Line: 1,
			Reason: "dropped by a build constraint (GOOS=linux, GOARCH=amd64, no cgo)"})
	}
	for _, f := range m.Errors {
		g.addUnread(f.File, f.Line, "file", f.Message)
	}
	g.readGoMod(root)
	m.AllFuncs()
	g.computeWiring()
	g.indexCalls()
	g.indexStructLits()
	g.findSentinels()
	g.findTables()
	g.findRoutes()
	g.finish()
	out := &xWorkflow{xResult: g.res, wfUnreadOK: map[string]bool{}}
	out.ParamUses, out.Creates, out.Literals = []xParamUse{}, []xCreate{}, []xLiteral{}
	out.Facts, out.WfUnread, out.WfNotes = []xStatusFact{}, []xUnread{}, []string{}
	g.workflowFacts(out)
	return out, nil
}

// ---- taint ------------------------------------------------------------------------------

// wfTaint maps a field path ("" is the value itself, ".F" its field F) to the origins that
// reach it: "path:id", "body:name", "query:limit".
type wfTaint map[string]map[string]bool

func (t wfTaint) add(field string, os map[string]bool) bool {
	changed := false
	for o := range os {
		if t[field] == nil {
			t[field] = map[string]bool{}
		}
		if !t[field][o] {
			t[field][o] = true
			changed = true
		}
	}
	return changed
}

func (t wfTaint) merge(u wfTaint) bool {
	changed := false
	for f, os := range u {
		if t.add(f, os) {
			changed = true
		}
	}
	return changed
}

func (t wfTaint) all() map[string]bool {
	out := map[string]bool{}
	for _, os := range t {
		for o := range os {
			out[o] = true
		}
	}
	return out
}

func (t wfTaint) copy() wfTaint {
	out := wfTaint{}
	out.merge(t)
	return out
}

func (t wfTaint) sig() string {
	var parts []string
	for f, os := range t {
		parts = append(parts, f+"="+strings.Join(sortedKeys(os), ","))
	}
	sort.Strings(parts)
	return strings.Join(parts, ";")
}

func taintSig(ts []wfTaint) string {
	parts := make([]string, len(ts))
	for i, t := range ts {
		parts[i] = t.sig()
	}
	return strings.Join(parts, "|")
}

// selOf is the taint of `x.F`.
func selOf(t wfTaint, field string) wfTaint {
	if t == nil {
		return nil
	}
	out := wfTaint{}
	out.add("", t[""])
	out.add("", t["."+field])
	if len(out) == 0 {
		return nil
	}
	return out
}

func one(os map[string]bool) wfTaint {
	if len(os) == 0 {
		return nil
	}
	return wfTaint{"": os}
}

// originSet filters the origins of a taint to the request sources of a kind.
func originSet(t wfTaint, kinds ...string) []string {
	var out []string
	for o := range t.all() {
		src, _, _ := strings.Cut(o, ":")
		for _, k := range kinds {
			if src == k {
				out = append(out, o)
			}
		}
	}
	sort.Strings(out)
	return out
}

// ---- the walker -------------------------------------------------------------------------

type wfVisit struct {
	fn     *Func
	binds  binds
	env    map[string]wfTaint
	entry  *entryPos
	depth  int
	bodies []*Func
}

type wfCtx struct {
	g          *gx
	out        *xWorkflow
	route      string
	memo       map[string][]wfTaint
	busy       map[string]bool
	scanned    map[string]bool
	useSeen    map[string]bool
	createSeen map[string]bool
	litIdx     map[string]*xLiteral
	lits       []*xLiteral
}

func (g *gx) wfUnreadAdd(out *xWorkflow, file string, line int, what, reason string) {
	key := fmt.Sprintf("%s|%d|%s|%s", file, line, what, reason)
	if out.wfUnreadOK[key] {
		return
	}
	out.wfUnreadOK[key] = true
	out.WfUnread = append(out.WfUnread, xUnread{File: file, Line: line, What: what, Reason: reason})
}

// workflowFacts runs the analyses over every route and every constant string write.
func (g *gx) workflowFacts(out *xWorkflow) {
	for _, rec := range g.routeList {
		if rec.h.fn == nil || rec.out == nil {
			continue
		}
		w := &wfCtx{g: g, out: out, route: rec.out.Method + " " + rec.out.PathTemplate,
			memo: map[string][]wfTaint{}, busy: map[string]bool{}, scanned: map[string]bool{},
			useSeen: map[string]bool{}, createSeen: map[string]bool{}, litIdx: map[string]*xLiteral{}}
		func() {
			defer func() {
				if r := recover(); r != nil {
					g.wfUnreadAdd(out, rec.out.File, rec.out.Line, "workflow",
						fmt.Sprintf("internal error reading %s: %v", w.route, r))
				}
			}()
			w.visit(rec.h.fn, binds{}, nil, nil, 0)
		}()
		for _, l := range w.lits {
			out.Literals = append(out.Literals, *l)
		}
	}
	func() {
		defer func() {
			if r := recover(); r != nil {
				g.wfUnreadAdd(out, "", 0, "workflow", fmt.Sprintf("internal error reading status writes: %v", r))
			}
		}()
		g.statusFacts(out)
	}()
	sort.SliceStable(out.ParamUses, func(i, j int) bool {
		a, b := out.ParamUses[i], out.ParamUses[j]
		ka := fmt.Sprintf("%s|%s|%s|%s|%s|%s|%s|%06d", a.Route, a.Source, a.Param, a.Table, a.Column, a.How, a.File, a.Line)
		kb := fmt.Sprintf("%s|%s|%s|%s|%s|%s|%s|%06d", b.Route, b.Source, b.Param, b.Table, b.Column, b.How, b.File, b.Line)
		return ka < kb
	})
	sort.SliceStable(out.Creates, func(i, j int) bool {
		a, b := out.Creates[i], out.Creates[j]
		ka := fmt.Sprintf("%s|%s|%s|%06d", a.Route, a.Table, a.File, a.Line)
		kb := fmt.Sprintf("%s|%s|%s|%06d", b.Route, b.Table, b.File, b.Line)
		return ka < kb
	})
	sort.SliceStable(out.Literals, func(i, j int) bool {
		a, b := out.Literals[i], out.Literals[j]
		return a.Route+"|"+a.Source+"|"+a.Param < b.Route+"|"+b.Source+"|"+b.Param
	})
	sort.SliceStable(out.Facts, func(i, j int) bool {
		a, b := out.Facts[i], out.Facts[j]
		ka := fmt.Sprintf("%s|%06d|%s", a.File, a.Line, a.Column)
		kb := fmt.Sprintf("%s|%06d|%s", b.File, b.Line, b.Column)
		return ka < kb
	})
	sort.SliceStable(out.WfUnread, func(i, j int) bool {
		a, b := out.WfUnread[i], out.WfUnread[j]
		if a.File != b.File {
			return a.File < b.File
		}
		if a.Line != b.Line {
			return a.Line < b.Line
		}
		return a.What+a.Reason < b.What+b.Reason
	})
}

// visit reads fn with its parameters carrying args; the taint of its results.
func (w *wfCtx) visit(fn *Func, b binds, args []wfTaint, entry *entryPos, depth int) []wfTaint {
	key := w.g.funcKey(fn) + "|" + b.sig() + "|" + taintSig(args)
	if r, ok := w.memo[key]; ok {
		return r
	}
	if w.busy[key] || depth > closureDepth {
		return nil
	}
	w.busy[key] = true
	defer delete(w.busy, key)
	v := &wfVisit{fn: fn, binds: b, env: map[string]wfTaint{}, entry: entry, depth: depth,
		bodies: w.g.bodies(fn)}
	for i, p := range fn.Params() {
		if p.name != "" && i < len(args) && args[i] != nil {
			v.env[p.name] = args[i].copy()
		}
	}
	for round := 0; round < 4; round++ {
		changed := false
		for _, body := range v.bodies {
			if w.flow(v, body) {
				changed = true
			}
		}
		if !changed {
			break
		}
	}
	// the scan reads the settled environment, once per distinct visit
	for _, body := range v.bodies {
		w.scan(v, body)
	}
	res := w.returns(v)
	w.memo[key] = res
	return res
}

func (v *wfVisit) merge(name string, t wfTaint) bool {
	if name == "_" || name == "" || len(t) == 0 {
		return false
	}
	if v.env[name] == nil {
		v.env[name] = wfTaint{}
	}
	return v.env[name].merge(t)
}

// assignTo stores a taint into an assignment target.
func (w *wfCtx) assignTo(v *wfVisit, lhs ast.Expr, t wfTaint) bool {
	if len(t) == 0 {
		return false
	}
	switch l := unparen(lhs).(type) {
	case *ast.Ident:
		return v.merge(l.Name, t)
	case *ast.SelectorExpr:
		// a.F = x, a.F.G = x: field F of a's root
		var path []string
		var x ast.Expr = l
		for {
			s, ok := unparen(x).(*ast.SelectorExpr)
			if !ok {
				break
			}
			path = append(path, s.Sel.Name)
			x = s.X
		}
		id, ok := unparen(x).(*ast.Ident)
		if !ok || id.Name == "_" || len(path) == 0 {
			return false
		}
		if v.env[id.Name] == nil {
			v.env[id.Name] = wfTaint{}
		}
		return v.env[id.Name].add("."+path[len(path)-1], t.all())
	case *ast.IndexExpr:
		if id, ok := unparen(l.X).(*ast.Ident); ok {
			if v.env[id.Name] == nil {
				v.env[id.Name] = wfTaint{}
			}
			return v.env[id.Name].add("", t.all())
		}
	}
	return false
}

// flow propagates taint through the assignments of one body.
func (w *wfCtx) flow(v *wfVisit, b *Func) bool {
	g, m := w.g, w.g.m
	e := m.EnvOf(b)
	changed := false
	ast.Inspect(b.Body, func(n ast.Node) bool {
		switch s := n.(type) {
		case *ast.FuncLit:
			return false
		case *ast.AssignStmt:
			switch {
			case len(s.Rhs) == len(s.Lhs):
				for i, l := range s.Lhs {
					if w.assignTo(v, l, w.ev(v, e, s.Rhs[i])) {
						changed = true
					}
				}
			case len(s.Rhs) == 1:
				rs := w.evMulti(v, e, s.Rhs[0], len(s.Lhs))
				for i, l := range s.Lhs {
					if i < len(rs) && w.assignTo(v, l, rs[i]) {
						changed = true
					}
				}
			}
		case *ast.ValueSpec:
			switch {
			case len(s.Values) == len(s.Names):
				for i, nm := range s.Names {
					if v.merge(nm.Name, w.ev(v, e, s.Values[i])) {
						changed = true
					}
				}
			case len(s.Values) == 1:
				rs := w.evMulti(v, e, s.Values[0], len(s.Names))
				for i, nm := range s.Names {
					if i < len(rs) && v.merge(nm.Name, rs[i]) {
						changed = true
					}
				}
			}
		case *ast.RangeStmt:
			t := w.ev(v, e, s.X)
			if id, ok := s.Value.(*ast.Ident); ok && len(t) > 0 {
				if v.merge(id.Name, one(t.all())) {
					changed = true
				}
			}
		case *ast.CallExpr:
			if w.bindCall(v, e, s) {
				changed = true
			}
			// every call is read, so a callee in a condition or a statement is visited too
			w.evCall(v, e, s)
		}
		return true
	})
	_ = g
	return changed
}

// bindCall: c.ShouldBindJSON(&x) taints x's fields with the body keys they bind;
// ShouldBindUri with the path parameters of their `uri` tags.
func (w *wfCtx) bindCall(v *wfVisit, e *Env, call *ast.CallExpr) bool {
	g, m := w.g, w.g.m
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || len(call.Args) == 0 || !m.isCtx(e, sel.X) {
		return false
	}
	src := ""
	switch sel.Sel.Name {
	case "ShouldBind", "ShouldBindJSON", "BindJSON", "Bind", "ShouldBindBodyWith", "ShouldBindBodyWithJSON":
		src = "body"
	case "ShouldBindUri", "BindUri":
		src = "path"
	default:
		return false
	}
	id, ok := stripAddr(call.Args[0]).(*ast.Ident)
	if !ok {
		return false
	}
	ti := m.Infer(e, stripAddr(call.Args[0])).deref().rowTypeAny()
	if ti == nil {
		ti = g.anonStructOf(e, call.Args[0])
	}
	if ti == nil {
		return false
	}
	changed := false
	var walk func(t *TypeInfo, depth int)
	walk = func(t *TypeInfo, depth int) {
		for _, f := range m.Fields(t) {
			if f.embedded {
				if et := m.TypeExpr(f.file, f.typ).rowTypeAny(); et != nil && depth > 0 {
					walk(et, depth-1)
				}
				continue
			}
			if !ast.IsExported(f.name) || jsonHidden(f.tag) {
				continue
			}
			name := jsonName(f)
			if src == "path" {
				q := reflectTag(f.tag).Get("uri")
				if q == "" {
					continue
				}
				name, _, _ = strings.Cut(q, ",")
			}
			if v.env[id.Name] == nil {
				v.env[id.Name] = wfTaint{}
			}
			if v.env[id.Name].add("."+f.name, map[string]bool{src + ":" + name: true}) {
				changed = true
			}
		}
	}
	walk(ti, 2)
	return changed
}

// ev is the taint of an expression.
func (w *wfCtx) ev(v *wfVisit, e *Env, x ast.Expr) wfTaint {
	m := w.g.m
	switch n := unparen(x).(type) {
	case *ast.Ident:
		if t := v.env[n.Name]; t != nil {
			return t.copy()
		}
	case *ast.SelectorExpr:
		if _, ok := m.importOf(e, n.X); ok {
			return nil
		}
		return selOf(w.ev(v, e, n.X), n.Sel.Name)
	case *ast.StarExpr:
		return w.ev(v, e, n.X)
	case *ast.UnaryExpr:
		if n.Op == token.AND {
			return w.ev(v, e, n.X)
		}
	case *ast.BinaryExpr:
		switch n.Op {
		case token.ADD, token.SUB, token.MUL, token.QUO, token.REM:
			l, r := w.ev(v, e, n.X), w.ev(v, e, n.Y)
			out := wfTaint{}
			out.add("", l.all())
			out.add("", r.all())
			if len(out) == 0 {
				return nil
			}
			return out
		}
	case *ast.IndexExpr:
		return one(w.ev(v, e, n.X).all())
	case *ast.SliceExpr:
		return w.ev(v, e, n.X)
	case *ast.TypeAssertExpr:
		return w.ev(v, e, n.X)
	case *ast.CompositeLit:
		out := wfTaint{}
		for _, el := range n.Elts {
			if kv, ok := el.(*ast.KeyValueExpr); ok {
				t := w.ev(v, e, kv.Value).all()
				if k, ok := kv.Key.(*ast.Ident); ok && n.Type != nil {
					out.add("."+k.Name, t)
				} else {
					out.add("", t)
				}
				continue
			}
			out.add("", w.ev(v, e, el).all())
		}
		if len(out) == 0 {
			return nil
		}
		return out
	case *ast.CallExpr:
		if r := w.evCall(v, e, n); len(r) > 0 {
			return r[0]
		}
	}
	return nil
}

// evMulti is the taints of the n values of a right-hand side.
func (w *wfCtx) evMulti(v *wfVisit, e *Env, x ast.Expr, n int) []wfTaint {
	if c, ok := unparen(x).(*ast.CallExpr); ok {
		rs := w.evCall(v, e, c)
		out := make([]wfTaint, n)
		copy(out, rs)
		return out
	}
	// v, ok := m[k] / x.(T) / <-ch: the first value
	out := make([]wfTaint, n)
	out[0] = w.ev(v, e, x)
	return out
}

// evCall is the taints of a call's results.
func (w *wfCtx) evCall(v *wfVisit, e *Env, call *ast.CallExpr) []wfTaint {
	g, m := w.g, w.g.m
	fun := unparen(call.Fun)
	if id, ok := fun.(*ast.Ident); ok {
		if _, local := e.lookup(id.Name); !local {
			switch {
			case basics[id.Name] && len(call.Args) == 1:
				return []wfTaint{w.ev(v, e, call.Args[0])}
			case id.Name == "append":
				out := wfTaint{}
				for _, a := range call.Args {
					out.add("", w.ev(v, e, a).all())
				}
				return []wfTaint{out}
			case id.Name == "len" || id.Name == "make" || id.Name == "new" || id.Name == "cap":
				return nil
			}
		}
	}
	if acc := g.accessorOf(e, call, v.binds, map[string][]string{}, func(string) {}); acc != nil {
		if acc.am.shape == "bool" || acc.source == "form" {
			return nil
		}
		os := map[string]bool{}
		for _, k := range acc.keys {
			os[acc.source+":"+k] = true
		}
		return []wfTaint{one(os)}
	}
	if sel, ok := fun.(*ast.SelectorExpr); ok {
		if m.isCtx(e, sel.X) {
			if sel.Sel.Name == "Param" && len(call.Args) == 1 {
				if c, ok := g.evalConst(e, call.Args[0], v.binds); ok && c.isStr {
					return []wfTaint{one(map[string]bool{"path:" + c.s: true})}
				}
			}
			return nil
		}
		if m.isGorm(e, sel.X) {
			return nil
		}
	}
	callee := m.Callee(e, call)
	if callee != nil && callee.Lit == nil && g.ctorOf(callee) == nil && !g.isAccessorMethod(callee) {
		if v.depth >= closureDepth {
			return nil
		}
		args := make([]wfTaint, len(call.Args))
		nb := binds{}
		for i, p := range callee.Params() {
			if i >= len(call.Args) || p.name == "" {
				continue
			}
			args[i] = w.ev(v, e, call.Args[i])
			if c, ok := g.evalConst(e, call.Args[i], v.binds); ok {
				nb[p.name] = c
			}
		}
		ent := v.entry
		if ent == nil {
			ent = &entryPos{e.fn.File.Rel, g.line(call.Pos())}
		}
		return w.visit(callee, nb, args, ent, v.depth+1)
	}
	if callee != nil {
		return nil
	}
	// outside the module: the value comes from the arguments (and a tainted receiver)
	os := map[string]bool{}
	for _, a := range call.Args {
		for o := range w.ev(v, e, a).all() {
			os[o] = true
		}
	}
	if sel, ok := fun.(*ast.SelectorExpr); ok {
		if _, isPkg := m.importOf(e, sel.X); !isPkg {
			for o := range w.ev(v, e, sel.X).all() {
				os[o] = true
			}
		}
	}
	return []wfTaint{one(os)}
}

// returns is the taint of fn's results (named results and bare returns included).
func (w *wfCtx) returns(v *wfVisit) []wfTaint {
	fn := v.fn
	e := w.g.m.EnvOf(fn)
	results := fn.Results()
	out := make([]wfTaint, len(results))
	put := func(i int, t wfTaint) {
		if i >= len(out) || len(t) == 0 {
			return
		}
		if out[i] == nil {
			out[i] = wfTaint{}
		}
		out[i].merge(t)
	}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		switch s := n.(type) {
		case *ast.FuncLit:
			return false
		case *ast.ReturnStmt:
			switch {
			case len(s.Results) == 0:
				for i, r := range results {
					if r.name != "" {
						put(i, v.env[r.name])
					}
				}
			case len(s.Results) == len(results):
				for i, x := range s.Results {
					put(i, w.ev(v, e, x))
				}
			case len(s.Results) == 1:
				if c, ok := unparen(s.Results[0]).(*ast.CallExpr); ok {
					for i, t := range w.evCall(v, e, c) {
						put(i, t)
					}
				}
			}
		}
		return true
	})
	return out
}

// ---- the scan: uses, creates, literals --------------------------------------------------

func (w *wfCtx) pos(v *wfVisit, b *Func, p token.Pos) (string, int) {
	if v.entry != nil {
		return v.entry.file, v.entry.line
	}
	return b.File.Rel, w.g.line(p)
}

func (w *wfCtx) noteUse(v *wfVisit, b *Func, p token.Pos, origin string, t *xTable, col, how string) {
	src, name, _ := strings.Cut(origin, ":")
	if src != "path" && src != "body" {
		return
	}
	file, line := w.pos(v, b, p)
	key := fmt.Sprintf("%s|%s|%s|%s|%s|%d", origin, t.Table, col, how, file, line)
	if w.useSeen[key] {
		return
	}
	w.useSeen[key] = true
	w.out.ParamUses = append(w.out.ParamUses, xParamUse{Route: w.route, Param: name, Source: src,
		Table: t.Table, Model: t.Struct, Column: col, How: how, File: file, Line: line, In: qual(topFunc(b))})
}

func (w *wfCtx) unread(v *wfVisit, b *Func, p token.Pos, what, reason string) {
	file, line := w.pos(v, b, p)
	w.g.wfUnreadAdd(w.out, file, line, what, reason)
}

// condSlot is one `[qual.]col <op> ?` of a SQL condition string.
type condSlot struct{ qual, col, op string }

func condSlots(text string) map[int]condSlot {
	out := map[int]condSlot{}
	k := 0
	for i := 0; i < len(text); i++ {
		if text[i] != '?' {
			continue
		}
		if mm := placeRE.FindStringSubmatch(text[:i]); mm != nil {
			op := strings.ToLower(strings.Join(strings.Fields(mm[3]), " "))
			out[k] = condSlot{mm[1], mm[2], op}
		}
		k++
	}
	return out
}

func (w *wfCtx) scan(v *wfVisit, b *Func) {
	g, m := w.g, w.g.m
	e := m.EnvOf(b)
	skey := g.funcKey(b) + "|" + v.binds.sig() + "|" + fmt.Sprint(v.depth)
	for _, name := range sortedKeys(envNames(v.env)) {
		skey += "|" + name + "=" + v.env[name].sig()
	}
	if w.scanned[skey] {
		return
	}
	w.scanned[skey] = true
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
	stmtWalk(b.Body, func(call *ast.CallExpr, stack []ast.Node) {
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
			// a query-builder local (`q := db.Model(&T{})`), never a handle (`tx`)
			if name := rootIdent(root); name != "" && len(rooted[name]) > 0 && isQueryLocal(e, name) {
				tbl = rooted[name][0]
			}
		}
		for _, l := range links {
			args := l.call.Args
			condAt := -1
			switch l.name {
			case "Where", "Or", "Not", "Having":
				condAt = 0
			case "First", "Take", "Last", "Find", "Delete":
				if len(args) >= 2 {
					condAt = 1
				}
			}
			if condAt >= 0 && condAt < len(args) {
				w.cond(v, b, e, l, args, condAt, tbl)
			}
			if gormWrites[l.name] || l.name == "Where" {
				for _, a := range args {
					cl, ok := stripAddr(unparen(a)).(*ast.CompositeLit)
					if !ok {
						continue
					}
					if _, isMap := cl.Type.(*ast.MapType); !isMap {
						continue
					}
					how := "assign"
					if l.name == "Where" {
						how = "compare"
					}
					for _, el := range cl.Elts {
						kv, ok := el.(*ast.KeyValueExpr)
						if !ok {
							continue
						}
						k, ok := m.constString(e, kv.Key)
						if !ok {
							continue
						}
						for _, o := range originSet(w.ev(v, e, kv.Value), "path", "body") {
							if tbl == nil {
								w.unread(v, b, kv.Pos(), "param_use", "table of the map condition on `"+k+"` is not resolved")
								continue
							}
							w.noteUse(v, b, kv.Pos(), o, tbl, k, how)
						}
					}
				}
			}
			if (l.name == "Update" || l.name == "UpdateColumn") && len(args) == 2 {
				if col, ok := m.constString(e, args[0]); ok && tbl != nil {
					for _, o := range originSet(w.ev(v, e, args[1]), "path", "body") {
						w.noteUse(v, b, l.call.Pos(), o, tbl, col, "assign")
					}
				}
			}
			if (l.name == "Create" || l.name == "Save" || l.name == "FirstOrCreate" || l.name == "CreateInBatches") && len(args) >= 1 && l == links[len(links)-1] {
				w.noteCreate(v, b, e, l.call, args[0])
			}
		}
	})
	// model literals and field assignments carry request values into columns
	ast.Inspect(b.Body, func(n ast.Node) bool {
		switch s := n.(type) {
		case *ast.FuncLit:
			return false
		case *ast.CompositeLit:
			if s.Type == nil {
				return true
			}
			t := g.tables[m.Infer(e, s).deref().rowTypeAny()]
			if t == nil {
				return true
			}
			for _, el := range s.Elts {
				kv, ok := el.(*ast.KeyValueExpr)
				if !ok {
					continue
				}
				k, ok := kv.Key.(*ast.Ident)
				if !ok {
					continue
				}
				col := t.fieldCols[k.Name]
				if col == "" {
					continue
				}
				for _, o := range originSet(w.ev(v, e, kv.Value), "path", "body") {
					w.noteUse(v, b, kv.Pos(), o, t, col, "assign")
				}
			}
		case *ast.AssignStmt:
			if len(s.Lhs) != len(s.Rhs) {
				return true
			}
			for i, l := range s.Lhs {
				sel, ok := l.(*ast.SelectorExpr)
				if !ok {
					continue
				}
				t := g.tables[m.Infer(e, sel.X).deref().rowTypeAny()]
				if t == nil {
					continue
				}
				col := t.fieldCols[sel.Sel.Name]
				if col == "" {
					continue
				}
				for _, o := range originSet(w.ev(v, e, s.Rhs[i]), "path", "body") {
					w.noteUse(v, b, s.Pos(), o, t, col, "assign")
				}
			}
		case *ast.BinaryExpr:
			if s.Op != token.EQL && s.Op != token.NEQ {
				return true
			}
			for _, pair := range [][2]ast.Expr{{s.X, s.Y}, {s.Y, s.X}} {
				c, ok := g.evalConst(e, pair[1], v.binds)
				if !ok || !c.isStr {
					continue
				}
				for _, o := range originSet(w.ev(v, e, pair[0]), "path", "body", "query") {
					w.addLiteral(v, b, s.Pos(), o, c.s)
				}
			}
		case *ast.SwitchStmt:
			if s.Tag == nil {
				return true
			}
			os := originSet(w.ev(v, e, s.Tag), "path", "body", "query")
			if len(os) == 0 {
				return true
			}
			for _, st := range s.Body.List {
				cc, ok := st.(*ast.CaseClause)
				if !ok {
					continue
				}
				for _, x := range cc.List {
					if c, ok := g.evalConst(e, x, v.binds); ok && c.isStr {
						for _, o := range os {
							w.addLiteral(v, b, x.Pos(), o, c.s)
						}
					}
				}
			}
		case *ast.IndexExpr:
			os := originSet(w.ev(v, e, s.Index), "path", "body", "query")
			if len(os) == 0 {
				return true
			}
			for _, k := range w.mapKeys(e, s.X) {
				for _, o := range os {
					w.addLiteral(v, b, s.Pos(), o, k)
				}
			}
		}
		return true
	})
}

// isQueryLocal: every value assigned to name is a GORM chain call.
func isQueryLocal(e *Env, name string) bool {
	vals := e.valuesOf(name)
	if len(vals) == 0 {
		return false
	}
	for _, x := range vals {
		c, ok := unparen(x).(*ast.CallExpr)
		if !ok {
			return false
		}
		if _, ok := c.Fun.(*ast.SelectorExpr); !ok {
			return false
		}
	}
	return true
}

func envNames(env map[string]wfTaint) map[string]bool {
	out := map[string]bool{}
	for k := range env {
		out[k] = true
	}
	return out
}

// mapKeys answers the constant keys of a map literal (local or package-level) named by x.
func (w *wfCtx) mapKeys(e *Env, x ast.Expr) []string {
	g, m := w.g, w.g.m
	var lit *ast.CompositeLit
	switch n := unparen(x).(type) {
	case *ast.CompositeLit:
		lit = n
	case *ast.Ident:
		if vals := e.valuesOf(n.Name); len(vals) == 1 {
			lit, _ = unparen(vals[0]).(*ast.CompositeLit)
		} else if _, local := e.lookup(n.Name); !local {
			if vi, ok := e.fn.File.Pkg.Vars[n.Name]; ok && vi.value != nil {
				lit, _ = unparen(vi.value).(*ast.CompositeLit)
			}
		}
	}
	if lit == nil {
		return nil
	}
	if _, isMap := lit.Type.(*ast.MapType); !isMap {
		return nil
	}
	var out []string
	for _, el := range lit.Elts {
		kv, ok := el.(*ast.KeyValueExpr)
		if !ok {
			continue
		}
		if s, ok := m.constString(e, kv.Key); ok {
			out = append(out, s)
		}
	}
	_ = g
	return out
}

func (w *wfCtx) addLiteral(v *wfVisit, b *Func, p token.Pos, origin, value string) {
	if value == "" {
		return // `x == ""` tests emptiness; it names no state a value can take
	}
	src, name, _ := strings.Cut(origin, ":")
	key := src + "|" + name
	l := w.litIdx[key]
	if l == nil {
		file, line := w.pos(v, b, p)
		l = &xLiteral{Route: w.route, Param: name, Source: src, Values: []string{}, File: file, Line: line}
		w.litIdx[key] = l
		w.lits = append(w.lits, l)
	}
	for _, x := range l.Values {
		if x == value {
			return
		}
	}
	l.Values = append(l.Values, value)
}

// cond reads one condition of a chain for request values.
func (w *wfCtx) cond(v *wfVisit, b *Func, e *Env, l chainLink, args []ast.Expr, at int, tbl *xTable) {
	g, m := w.g, w.g.m
	text := g.foldString(e, args[at], v.binds)
	if strings.Contains(text, "?") {
		slots := condSlots(text)
		for k := 0; ; k++ {
			idx := at + 1 + k
			if idx >= len(args) {
				break
			}
			os := originSet(w.ev(v, e, args[idx]), "path", "body")
			if len(os) == 0 {
				continue
			}
			s, ok := slots[k]
			if !ok || (s.op != "=" && s.op != "in") {
				continue
			}
			t := tbl
			if s.qual != "" {
				t = g.byTable[s.qual]
				if t == nil {
					w.unread(v, b, l.call.Pos(), "param_use", "qualifier `"+s.qual+"` of `"+s.col+"` is not a table of the module")
					continue
				}
			}
			if t == nil {
				w.unread(v, b, l.call.Pos(), "param_use", "table of the condition on `"+s.col+"` is not resolved")
				continue
			}
			for _, o := range os {
				w.noteUse(v, b, l.call.Pos(), o, t, s.col, "compare")
			}
		}
		return
	}
	if cl, ok := stripAddr(unparen(args[at])).(*ast.CompositeLit); ok {
		for _, el := range cl.Elts {
			kv, ok := el.(*ast.KeyValueExpr)
			if !ok {
				continue
			}
			os := originSet(w.ev(v, e, kv.Value), "path", "body")
			if len(os) == 0 {
				continue
			}
			col := ""
			t := tbl
			switch key := kv.Key.(type) {
			case *ast.Ident:
				if ti := m.Infer(e, cl).deref().rowTypeAny(); g.tables[ti] != nil {
					t = g.tables[ti]
					col = t.fieldCols[key.Name]
				}
			case *ast.BasicLit:
				col, _ = stringLit(key)
			}
			if col == "" || t == nil {
				w.unread(v, b, kv.Pos(), "param_use", "column or table of a struct/map condition is not resolved")
				continue
			}
			for _, o := range os {
				w.noteUse(v, b, l.call.Pos(), o, t, col, "compare")
			}
		}
		return
	}
	// First(&x, id): GORM reads a non-string second argument as the primary key
	if at == 1 && text == "\x00" {
		ty := m.Infer(e, args[at])
		if ty.Kind == "basic" && ty.Basic != "string" {
			os := originSet(w.ev(v, e, args[at]), "path", "body")
			if len(os) > 0 {
				t := tbl
				if t == nil {
					w.unread(v, b, l.call.Pos(), "param_use", "table of the primary-key condition is not resolved")
					return
				}
				col, _ := pkOf(t)
				if col == "" {
					w.unread(v, b, l.call.Pos(), "param_use", "table "+t.Table+" has no single primary key")
					return
				}
				for _, o := range os {
					w.noteUse(v, b, l.call.Pos(), o, t, col, "compare")
				}
			}
		}
	}
}

// pkOf answers a table's single primary-key column and its Go field.
func pkOf(t *xTable) (string, string) {
	col := ""
	for _, c := range t.Columns {
		if c.PrimaryKey {
			if col != "" {
				return "", ""
			}
			col = c.Name
		}
	}
	if col == "" {
		return "", ""
	}
	field := ""
	for f, c := range t.fieldCols {
		if c == col && (field == "" || f < field) {
			field = f
		}
	}
	return col, field
}

// noteCreate reads a Create/Save of a new literal of a table.
func (w *wfCtx) noteCreate(v *wfVisit, b *Func, e *Env, call *ast.CallExpr, arg ast.Expr) {
	g, m := w.g, w.g.m
	arg = stripAddr(arg)
	var lit *ast.CompositeLit
	varName := ""
	switch n := arg.(type) {
	case *ast.CompositeLit:
		lit = n
	case *ast.Ident:
		varName = n.Name
		for _, body := range v.bodies {
			be := m.EnvOf(body)
			ast.Inspect(body.Body, func(x ast.Node) bool {
				if lit != nil {
					return false
				}
				switch s := x.(type) {
				case *ast.FuncLit:
					return false
				case *ast.AssignStmt:
					if len(s.Lhs) == len(s.Rhs) {
						for i, l := range s.Lhs {
							if id, ok := l.(*ast.Ident); ok && id.Name == varName {
								if cl, ok := stripAddr(s.Rhs[i]).(*ast.CompositeLit); ok && cl.Type != nil {
									if g.tables[m.Infer(be, cl).deref().rowTypeAny()] != nil {
										lit = cl
									}
								}
							}
						}
					}
				case *ast.ValueSpec:
					for i, nm := range s.Names {
						if nm.Name == varName && i < len(s.Values) {
							if cl, ok := stripAddr(s.Values[i]).(*ast.CompositeLit); ok && cl.Type != nil {
								if g.tables[m.Infer(be, cl).deref().rowTypeAny()] != nil {
									lit = cl
								}
							}
						}
					}
				}
				return true
			})
		}
	}
	if lit == nil || lit.Type == nil {
		return
	}
	t := g.tables[m.Infer(e, lit).deref().rowTypeAny()]
	if t == nil {
		// the literal may sit in another body of the visit (a captured variable)
		for _, body := range v.bodies {
			if tt := g.tables[m.Infer(m.EnvOf(body), lit).deref().rowTypeAny()]; tt != nil {
				t = tt
				break
			}
		}
	}
	if t == nil {
		return
	}
	keyCol, keyField := pkOf(t)
	keyFrom := "server"
	if keyField != "" {
		var keyExpr ast.Expr
		for _, el := range lit.Elts {
			if kv, ok := el.(*ast.KeyValueExpr); ok {
				if k, ok := kv.Key.(*ast.Ident); ok && k.Name == keyField {
					keyExpr = kv.Value
				}
			}
		}
		var t0 wfTaint
		if keyExpr != nil {
			t0 = w.ev(v, e, keyExpr)
		}
		if varName != "" {
			if kt := v.env[varName]; kt != nil && keyExpr == nil {
				t0 = selOf(kt, keyField)
			}
		}
		bodies := originSet(t0, "body")
		switch {
		case len(bodies) == 1:
			_, keyFrom, _ = strings.Cut(bodies[0], ":")
		case len(bodies) > 1:
			_, keyFrom, _ = strings.Cut(bodies[0], ":")
			w.unread(v, b, call.Pos(), "create", fmt.Sprintf("the key of %s comes from %d body fields: %s", t.Table, len(bodies), strings.Join(bodies, ", ")))
		}
	}
	file, line := w.pos(v, b, call.Pos())
	key := fmt.Sprintf("%s|%s|%d", t.Table, file, line)
	if w.createSeen[key] {
		return
	}
	w.createSeen[key] = true
	w.out.Creates = append(w.out.Creates, xCreate{Route: w.route, Table: t.Table, Model: t.Struct,
		Key: keyCol, KeyFrom: keyFrom, File: file, Line: line, In: qual(topFunc(b))})
}

// ---- status writes: create or update, and the states written from -----------------------

var constCondRE = placeRE

// colOfSel answers the column of a `x.Field` selector of table t.
func (g *gx) colOfSel(e *Env, x ast.Expr, t *xTable) string {
	sel, ok := unparen(x).(*ast.SelectorExpr)
	if !ok {
		return ""
	}
	if g.tables[g.m.Infer(e, sel.X).deref().rowTypeAny()] != t {
		return ""
	}
	return t.fieldCols[sel.Sel.Name]
}

// eqConsts answers the constants a condition pins the column col of t to: `x.Col == "a"`
// (or several joined by ||), within a conjunction.
func (g *gx) eqConsts(e *Env, cond ast.Expr, t *xTable, col string) []string {
	cond = unparen(cond)
	be, ok := cond.(*ast.BinaryExpr)
	if !ok {
		return nil
	}
	switch be.Op {
	case token.LAND:
		if c := g.eqConsts(e, be.X, t, col); len(c) > 0 {
			return c
		}
		return g.eqConsts(e, be.Y, t, col)
	case token.LOR:
		l, r := g.eqConsts(e, be.X, t, col), g.eqConsts(e, be.Y, t, col)
		if len(l) == 0 || len(r) == 0 {
			return nil
		}
		return append(l, r...)
	case token.EQL:
		for _, pair := range [][2]ast.Expr{{be.X, be.Y}, {be.Y, be.X}} {
			if g.colOfSel(e, pair[0], t) == col {
				if s, ok := g.m.constString(e, pair[1]); ok {
					return []string{s}
				}
			}
		}
	}
	return nil
}

type boundConst struct {
	v    string
	file string
	line int
}

// chainBound answers the constants a GORM chain pins the column col of t to in a
// condition (`[qual.]col = ?` with a constant argument, a map or struct condition).
func (g *gx) chainBound(e *Env, links []chainLink, t *xTable, col string) []boundConst {
	var out []boundConst
	for _, l := range links {
		switch l.name {
		case "Where", "Or", "Not", "Having", "First", "Take", "Last", "Find":
		default:
			continue
		}
		args := l.call.Args
		at := 0
		if l.name == "First" || l.name == "Take" || l.name == "Last" || l.name == "Find" {
			at = 1
		}
		if at >= len(args) {
			continue
		}
		text := g.foldString(e, args[at], nil)
		if strings.Contains(text, "?") {
			slots := condSlots(text)
			for k, s := range slots {
				idx := at + 1 + k
				if idx >= len(args) || s.col != col || s.op != "=" {
					continue
				}
				if s.qual != "" && g.byTable[s.qual] != t {
					continue
				}
				if c, ok := g.evalConst(e, args[idx], nil); ok && c.isStr {
					out = append(out, boundConst{c.s, e.fn.File.Rel, g.line(g.placeholderPos(e, args[at], k))})
				}
			}
			continue
		}
		cl, ok := stripAddr(unparen(args[at])).(*ast.CompositeLit)
		if !ok {
			continue
		}
		for _, el := range cl.Elts {
			kv, ok := el.(*ast.KeyValueExpr)
			if !ok {
				continue
			}
			c := ""
			switch key := kv.Key.(type) {
			case *ast.Ident:
				if ti := g.m.Infer(e, cl).deref().rowTypeAny(); g.tables[ti] == t {
					c = t.fieldCols[key.Name]
				}
			case *ast.BasicLit:
				c, _ = stringLit(key)
			}
			if c != col {
				continue
			}
			if s, ok := g.m.constString(e, kv.Value); ok {
				out = append(out, boundConst{s, e.fn.File.Rel, g.line(kv.Pos())})
			}
		}
	}
	return out
}

// loadedFrom answers the constants the rows reachable through name were loaded under: a
// chain in top whose destination is name binds the column to a constant.
func (g *gx) loadedFrom(top *Func, name string, t *xTable, col string, seen map[string]bool) []boundConst {
	if seen[name] {
		return nil
	}
	seen[name] = true
	var out []boundConst
	for _, b := range g.bodies(top) {
		e := g.m.EnvOf(b)
		ast.Inspect(b.Body, func(n ast.Node) bool {
			switch s := n.(type) {
			case *ast.FuncLit:
				return false
			case *ast.CallExpr:
				links, _ := g.m.gormChain(e, s)
				if links == nil {
					return true
				}
				last := links[len(links)-1]
				switch last.name {
				case "Find", "First", "Take", "Last":
				default:
					return true
				}
				if len(last.call.Args) == 0 {
					return true
				}
				if id, ok := stripAddr(last.call.Args[0]).(*ast.Ident); ok && id.Name == name {
					out = append(out, g.chainBound(e, links, t, col)...)
				}
			case *ast.RangeStmt:
				// for _, r := range S: r is loaded as S was
				if id, ok := s.Value.(*ast.Ident); ok && id.Name == name {
					if x, ok := unparen(s.X).(*ast.Ident); ok {
						out = append(out, g.loadedFrom(top, x.Name, t, col, seen)...)
					}
				}
			case *ast.AssignStmt:
				if len(s.Lhs) == len(s.Rhs) {
					for i, l := range s.Lhs {
						if id, ok := l.(*ast.Ident); ok && id.Name == name {
							if ix, ok := unparen(s.Rhs[i]).(*ast.IndexExpr); ok {
								if x, ok := unparen(ix.X).(*ast.Ident); ok {
									out = append(out, g.loadedFrom(top, x.Name, t, col, seen)...)
								}
							}
						}
					}
				}
			}
			return true
		})
	}
	return out
}

// placeholderPos answers the position of the string fragment of a `+`-joined condition
// that holds its k-th `?` (the whole expression when the fragments are not constants).
func (g *gx) placeholderPos(e *Env, x ast.Expr, k int) token.Pos {
	var leaves []ast.Expr
	var flat func(y ast.Expr)
	flat = func(y ast.Expr) {
		if be, ok := unparen(y).(*ast.BinaryExpr); ok && be.Op == token.ADD {
			flat(be.X)
			flat(be.Y)
			return
		}
		leaves = append(leaves, y)
	}
	flat(x)
	seen := 0
	for _, lf := range leaves {
		c, ok := g.evalConst(e, lf, nil)
		if !ok || !c.isStr {
			continue
		}
		n := strings.Count(c.s, "?")
		if k < seen+n {
			return lf.Pos()
		}
		seen += n
	}
	return x.Pos()
}

// rowRoots answers the identifiers a chain's conditions read fields of (`n.ID` -> n).
func rowRoots(links []chainLink) []string {
	var out []string
	seen := map[string]bool{}
	for _, l := range links {
		for i, a := range l.call.Args {
			if i == 0 && (l.name == "Update" || l.name == "UpdateColumn") {
				continue
			}
			ast.Inspect(a, func(n ast.Node) bool {
				if sel, ok := n.(*ast.SelectorExpr); ok {
					if id, ok := unparen(sel.X).(*ast.Ident); ok && !seen[id.Name] {
						seen[id.Name] = true
						out = append(out, id.Name)
					}
				}
				return true
			})
		}
	}
	return out
}

func (g *gx) statusFacts(out *xWorkflow) {
	m := g.m
	seen := map[string]bool{}
	// the sites Spike A's status_writes name, and the columns with two or more values
	sites := map[string]bool{}
	values := map[string]map[string]bool{}
	for _, sw := range g.res.StatusWrites {
		sites[fmt.Sprintf("%s|%d|%s", sw.File, sw.Line, sw.Column)] = true
		k := sw.Table + "." + sw.Column
		if values[k] == nil {
			values[k] = map[string]bool{}
		}
		values[k][sw.Value] = true
	}
	for _, top := range m.Funcs {
		for _, b := range g.bodies(top) {
			e := m.EnvOf(b)
			var stack []ast.Node
			createVars := map[string]bool{}
			ast.Inspect(b.Body, func(n ast.Node) bool {
				if c, ok := n.(*ast.CallExpr); ok {
					if links, _ := m.gormChain(e, c); links != nil {
						last := links[len(links)-1]
						if (last.name == "Create" || last.name == "CreateInBatches") && len(last.call.Args) > 0 {
							if id, ok := stripAddr(last.call.Args[0]).(*ast.Ident); ok {
								createVars[id.Name] = true
							}
						}
					}
				}
				return true
			})
			add := func(t *xTable, col string, pos token.Pos, kind string, links []chainLink, recv ast.Expr, stack []ast.Node) {
				if t == nil || col == "" || t.colTypes[col] != "Str" {
					return
				}
				file, line := b.File.Rel, g.line(pos)
				key := fmt.Sprintf("%s|%d|%s", file, line, col)
				if seen[key] || !sites[key] {
					return
				}
				seen[key] = true
				f := xStatusFact{Table: t.Table, Model: t.Struct, Column: col, File: file, Line: line,
					Kind: kind, From: []string{}, FromAt: []xLoc{}, In: qual(top)}
				if kind == "update" {
					add := func(v string, file string, line int) {
						for _, x := range f.From {
							if x == v {
								return
							}
						}
						f.From = append(f.From, v)
						f.FromAt = append(f.FromAt, xLoc{File: file, Line: line})
					}
					for i := len(stack) - 2; i >= 0; i-- {
						switch s := stack[i].(type) {
						case *ast.IfStmt:
							if stack[i+1] == ast.Node(s.Body) {
								for _, c := range g.eqConsts(e, s.Cond, t, col) {
									add(c, b.File.Rel, g.line(s.Cond.Pos()))
								}
							}
						case *ast.CaseClause:
							if i >= 2 {
								if sw, ok := stack[i-2].(*ast.SwitchStmt); ok && sw.Tag != nil && g.colOfSel(e, sw.Tag, t) == col {
									for _, x := range s.List {
										if c, ok := m.constString(e, x); ok {
											add(c, b.File.Rel, g.line(x.Pos()))
										}
									}
								} else if ok && sw.Tag == nil {
									for _, x := range s.List {
										for _, c := range g.eqConsts(e, x, t, col) {
											add(c, b.File.Rel, g.line(x.Pos()))
										}
									}
								}
							}
						}
					}
					for _, bc := range g.chainBound(e, links, t, col) {
						add(bc.v, bc.file, bc.line)
					}
					roots := rowRoots(links)
					if id, ok := unparen(recv).(*ast.Ident); ok && recv != nil {
						roots = append(roots, id.Name)
					}
					for _, r := range roots {
						for _, bc := range g.loadedFrom(top, r, t, col, map[string]bool{}) {
							add(bc.v, bc.file, bc.line)
						}
					}
					if len(f.From) == 0 {
						f.From = []string{"*"}
						if len(values[t.Table+"."+col]) < 2 {
							out.Facts = append(out.Facts, f)
							return
						}
						g.wfUnreadAdd(out, file, line, "status_write",
							"the state "+t.Table+"."+col+" is written from is not read: an update with no enclosing test of the column and no query pinning it")
					}
				}
				out.Facts = append(out.Facts, f)
			}
			ast.Inspect(b.Body, func(n ast.Node) bool {
				if n == nil {
					stack = stack[:len(stack)-1]
					return true
				}
				if _, ok := n.(*ast.FuncLit); ok {
					return false
				}
				stack = append(stack, n)
				switch s := n.(type) {
				case *ast.CompositeLit:
					if s.Type == nil {
						return true
					}
					if t := g.tables[m.Infer(e, s).deref().rowTypeAny()]; t != nil {
						for _, el := range s.Elts {
							if kv, ok := el.(*ast.KeyValueExpr); ok {
								if k, ok := kv.Key.(*ast.Ident); ok {
									add(t, t.fieldCols[k.Name], kv.Pos(), "create", nil, nil, stack)
								}
							}
						}
					}
				case *ast.AssignStmt:
					if len(s.Lhs) == len(s.Rhs) {
						for _, l := range s.Lhs {
							if sel, ok := l.(*ast.SelectorExpr); ok {
								if t := g.tables[m.Infer(e, sel.X).deref().rowTypeAny()]; t != nil {
									kind := "update"
									if id, ok := unparen(sel.X).(*ast.Ident); ok && createVars[id.Name] {
										kind = "create"
									}
									add(t, t.fieldCols[sel.Sel.Name], s.Pos(), kind, nil, sel.X, stack)
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
								add(t, col, s.Pos(), "update", links, nil, stack)
							}
						}
					case "Updates", "UpdateColumns":
						if len(last.call.Args) == 1 && t != nil {
							if cl, ok := stripAddr(unparen(last.call.Args[0])).(*ast.CompositeLit); ok {
								if _, isMap := cl.Type.(*ast.MapType); isMap {
									for _, el := range cl.Elts {
										if kv, ok := el.(*ast.KeyValueExpr); ok {
											if col, ok := m.constString(e, kv.Key); ok {
												add(t, col, kv.Pos(), "update", links, nil, stack)
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
}
