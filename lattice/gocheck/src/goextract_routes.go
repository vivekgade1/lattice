// goextract: route registrations (D4): the full path (the router chain read back to the
// engine), the handler (through wrappers and factories) and the middleware chain.
package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"sort"
	"strings"
)

type xHandler struct {
	Func     string   `json:"func"`
	Module   string   `json:"module"`
	File     string   `json:"file"`
	Line     int      `json:"line"`
	Wrappers []string `json:"wrappers"`
}

// xMwRef is one middleware of a route's chain.
type xMwRef struct {
	Func     string   `json:"func"`
	External string   `json:"external,omitempty"`
	File     string   `json:"file,omitempty"`
	Line     int      `json:"line,omitempty"`
	Wrappers []string `json:"wrappers,omitempty"`
	Via      string   `json:"via"`
	Resolved bool     `json:"resolved"`
}

// reg is one registration call: `router.POST("/jobs", mw..., handler)`.
type reg struct {
	call     *ast.CallExpr
	fn       *Func
	e        *Env
	methods  []string
	method   string
	path     string
	routerX  ast.Expr
	handlerX ast.Expr
	mwX      []ast.Expr
	cond     string
	unread   []string
}

// mwItem is a middleware expression with the environment it is read in.
type mwItem struct {
	e   *Env
	x   ast.Expr
	via string
}

// routerRes is one reading of a router expression (a parameter called from several
// places has one per call site).
type routerRes struct {
	prefix string
	exact  bool
	engine bool
	mws    []mwItem
	chain  []string
	unread []string
}

func (r routerRes) clone() routerRes {
	c := r
	c.mws = append([]mwItem(nil), r.mws...)
	c.chain = append([]string(nil), r.chain...)
	c.unread = append([]string(nil), r.unread...)
	return c
}

// hres is a resolved handler or middleware expression.
type hres struct {
	fn       *Func
	wrappers []*Func
	external string
	unread   string
	factory  bool
}

var externalOK = map[string]bool{
	ginPath + ".Logger": true, ginPath + ".Recovery": true, ginPath + ".CustomRecovery": true,
	ginPath + ".LoggerWithConfig": true, ginPath + ".LoggerWithWriter": true,
	ginPath + ".RecoveryWithWriter": true, ginPath + ".CustomRecoveryWithWriter": true,
	ginPath + ".LoggerWithFormatter":      true,
	"github.com/gin-contrib/cors.Default": true, "github.com/gin-contrib/cors.New": true,
	"github.com/gin-contrib/gzip.Gzip": true, "github.com/gin-contrib/requestid.New": true,
}

func (g *gx) findRegistrations() []*reg {
	m := g.m
	var out []*reg
	for _, fn := range m.AllFuncs() {
		e := m.EnvOf(fn)
		stmtWalk(fn.Body, func(call *ast.CallExpr, stack []ast.Node) {
			sel, ok := call.Fun.(*ast.SelectorExpr)
			if !ok {
				return
			}
			name := sel.Sel.Name
			xt := m.Infer(e, sel.X)
			router := xt.is("gin.Engine") || xt.is("gin.RouterGroup") || xt.is("gin.IRouter") ||
				xt.is("gin.IRoutes") || g.routerChainCall(sel.X)
			switch name {
			case "NoRoute", "NoMethod":
				if router && g.isWired(fn) {
					g.res.Excluded = append(g.res.Excluded, xExcluded{Kind: "framework", What: "route",
						Method: "ANY", Path: "*", File: fn.File.Rel, Line: g.line(call.Pos()),
						Reason: "gin " + name + " handler"})
				}
				return
			case "Static", "StaticFS", "StaticFile", "StaticFileFS":
				if router {
					p := ""
					if len(call.Args) > 0 {
						p, _ = m.constString(e, call.Args[0])
					}
					g.res.Excluded = append(g.res.Excluded, xExcluded{Kind: "framework", What: "route",
						Method: "GET", Path: p, File: fn.File.Rel, Line: g.line(call.Pos()),
						Reason: "gin " + name + " serves files"})
				}
				return
			}
			method, known := routeMethods[name]
			if !known {
				return
			}
			args := call.Args
			var methods []string
			switch name {
			case "Handle":
				if len(args) < 3 {
					return
				}
				s, ok := m.constString(e, args[0])
				if !ok {
					if router {
						g.unreadAt(fn.File, call.Pos(), "route", "Handle with a method that is not a constant")
					}
					return
				}
				method = strings.ToUpper(s)
				args = args[1:]
			case "Match":
				if len(args) < 3 {
					return
				}
				if cl, ok := unparen(args[0]).(*ast.CompositeLit); ok {
					for _, el := range cl.Elts {
						if s, ok := m.constString(e, el); ok {
							methods = append(methods, strings.ToUpper(s))
						}
					}
				}
				if len(methods) == 0 {
					if router {
						g.unreadAt(fn.File, call.Pos(), "route", "Match with methods that are not constants")
					}
					return
				}
				args = args[1:]
			}
			if len(args) < 2 {
				return
			}
			handlerX := args[len(args)-1]
			handler := g.funcRef(e, handlerX, 0)
			if !router && (handler == nil || !m.hasCtxParam(handler)) {
				return
			}
			rg := &reg{call: call, fn: fn, e: e, method: method, methods: methods,
				routerX: sel.X, handlerX: handlerX, mwX: args[1 : len(args)-1],
				cond: g.conditionOf(stack)}
			p, ok := m.constString(e, args[0])
			if !ok {
				g.unreadAt(fn.File, call.Pos(), "route",
					"the path of a "+name+" registration is not a constant: `"+g.src(args[0])+"`")
				return
			}
			rg.path = p
			out = append(out, rg)
		})
	}
	return out
}

// routerChainCall reports whether x is `y.Group(...)` / `y.Use(...)`.
func (g *gx) routerChainCall(x ast.Expr) bool {
	if c, ok := unparen(x).(*ast.CallExpr); ok {
		if s, ok := c.Fun.(*ast.SelectorExpr); ok {
			return s.Sel.Name == "Group" || s.Sel.Name == "Use"
		}
	}
	return false
}

// usesBefore answers the middleware `name.Use(...)` adds in the function that declares
// `name`, for registrations after pos.
func (g *gx) usesBefore(e *Env, name string, pos token.Pos) []mwItem {
	de := defEnv(e, name)
	if de == nil {
		return nil
	}
	var out []mwItem
	ast.Inspect(de.fn.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		call, ok := n.(*ast.CallExpr)
		if !ok || call.Pos() >= pos {
			return true
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if ok && sel.Sel.Name == "Use" && rootIdent(sel.X) == name {
			for _, a := range call.Args {
				out = append(out, mwItem{de, a, fmt.Sprintf("%s.Use at %s:%d", name, de.fn.File.Rel, g.line(call.Pos()))})
			}
		}
		return true
	})
	return out
}

func (g *gx) unreadRouter(reason string) []routerRes {
	return []routerRes{{unread: []string{reason}}}
}

// router reads a router expression: the path prefix, the middleware and whether the
// prefix is exact. A parameter is read through its call sites (up to 4).
func (g *gx) router(e *Env, x ast.Expr, pos token.Pos, depth int) []routerRes {
	m := g.m
	g.routerSteps++
	if depth < 0 || g.routerSteps > 400 {
		return g.unreadRouter("router chain is too deep to read")
	}
	switch v := unparen(x).(type) {
	case *ast.CallExpr:
		if sel, ok := v.Fun.(*ast.SelectorExpr); ok {
			switch sel.Sel.Name {
			case "Group":
				alts := g.router(e, sel.X, v.Pos(), depth)
				seg, ok := "", false
				if len(v.Args) > 0 {
					seg, ok = m.constString(e, v.Args[0])
				}
				for i := range alts {
					a := &alts[i]
					a.prefix = joinPath(a.prefix, seg)
					if !ok {
						a.exact = false
						a.unread = append(a.unread, "group path is not a constant: `"+g.src(v)+"`")
					}
					for _, mw := range v.Args[min(1, len(v.Args)):] {
						a.mws = append(a.mws, mwItem{e, mw, fmt.Sprintf("Group at %s:%d", e.fn.File.Rel, g.line(v.Pos()))})
					}
					a.chain = append(a.chain, fmt.Sprintf("Group(%q)", seg))
				}
				return alts
			case "Use":
				alts := g.router(e, sel.X, v.Pos(), depth)
				for i := range alts {
					for _, mw := range v.Args {
						alts[i].mws = append(alts[i].mws, mwItem{e, mw, fmt.Sprintf("Use at %s:%d", e.fn.File.Rel, g.line(v.Pos()))})
					}
				}
				return alts
			}
		}
		if m.Infer(e, v).is("gin.Engine") {
			return []routerRes{{exact: true, engine: true, chain: []string{"engine"}}}
		}
		// a module function returning a router: read its only return
		if callee := m.Callee(e, v); callee != nil && callee.Lit == nil {
			ce := m.EnvOf(callee)
			var rets []ast.Expr
			ast.Inspect(callee.Body, func(n ast.Node) bool {
				if _, ok := n.(*ast.FuncLit); ok {
					return false
				}
				if r, ok := n.(*ast.ReturnStmt); ok && len(r.Results) >= 1 {
					rets = append(rets, r.Results[0])
				}
				return true
			})
			if len(rets) == 1 {
				return g.router(ce, rets[0], rets[0].Pos(), depth-1)
			}
		}
		return g.unreadRouter("router expression `" + g.src(v) + "` is not readable")
	case *ast.Ident:
		return g.routerIdent(e, v, pos, depth)
	case *ast.SelectorExpr:
		return g.routerField(e, v, pos, depth)
	}
	return g.unreadRouter("router expression `" + g.src(x) + "` is not readable")
}

func (g *gx) routerIdent(e *Env, v *ast.Ident, pos token.Pos, depth int) []routerRes {
	m := g.m
	de := defEnv(e, v.Name)
	if de == nil {
		// a package-level router
		if vi, ok := e.fn.File.Pkg.Vars[v.Name]; ok {
			var alts []routerRes
			t := m.Infer(e, v)
			switch {
			case t.is("gin.Engine"):
				alts = []routerRes{{exact: true, engine: true, chain: []string{"engine"}}}
			case vi.value != nil:
				fn := &Func{Name: "<pkg>", Type: &ast.FuncType{}, Body: &ast.BlockStmt{}, File: vi.file}
				alts = g.router(&Env{fn: fn, vars: map[string]*T{}}, vi.value, vi.value.Pos(), depth-1)
			}
			if alts != nil {
				mws, foreign := g.useCallsOn(e, v, pos)
				for i := range alts {
					alts[i].mws = append(alts[i].mws, mws...)
					alts[i].unread = append(alts[i].unread, foreign...)
				}
				return alts
			}
		}
		return g.unreadRouter("router `" + v.Name + "` is not declared in the module")
	}
	uses := g.usesBefore(e, v.Name, pos)
	if vals := de.values[v.Name]; len(vals) == 1 {
		alts := g.router(de, vals[0], vals[0].Pos(), depth) // a local: not a hop
		for i := range alts {
			alts[i].mws = append(alts[i].mws, uses...)
		}
		return alts
	} else if len(vals) > 1 {
		return g.unreadRouter("router `" + v.Name + "` is assigned more than once")
	}
	// a parameter
	fn := de.fn
	for i, p := range fn.Params() {
		if p.name != v.Name {
			continue
		}
		engine := m.TypeExpr(fn.File, p.typ).is("gin.Engine")
		sites := g.calls[fn]
		if len(sites) == 0 {
			if engine {
				return []routerRes{{exact: true, engine: true, mws: uses, chain: []string{"engine"}}}
			}
			return g.unreadRouter("router parameter `" + v.Name + "` of " + fn.Name + " has no call site in the module")
		}
		if len(sites) > 4 {
			return g.unreadRouter(fmt.Sprintf("router parameter `%s` of %s has %d call sites (more than 4)", v.Name, fn.Name, len(sites)))
		}
		var out []routerRes
		for _, s := range sites {
			if i >= len(s.call.Args) {
				out = append(out, routerRes{unread: []string{"router argument missing at a call site"}})
				continue
			}
			for _, a := range g.router(s.e, s.call.Args[i], s.call.Pos(), depth-1) {
				a.mws = append(a.mws, uses...)
				out = append(out, a)
			}
		}
		return out
	}
	// a variable holding a router that is neither assigned once nor a parameter
	if m.Infer(e, v).is("gin.Engine") {
		return []routerRes{{exact: true, engine: true, mws: uses, chain: []string{"engine"}}}
	}
	return g.unreadRouter("router `" + v.Name + "` is not readable")
}

// routerField reads `x.F`: the field of a module struct (its composite literals in the
// module), or a package variable of another package.
func (g *gx) routerField(e *Env, v *ast.SelectorExpr, pos token.Pos, depth int) []routerRes {
	alts := g.routerFieldBase(e, v, pos, depth)
	mws, foreign := g.useCallsOn(e, v, pos)
	for i := range alts {
		alts[i].mws = append(alts[i].mws, mws...)
		alts[i].unread = append(alts[i].unread, foreign...)
	}
	return alts
}

func (g *gx) routerFieldBase(e *Env, v *ast.SelectorExpr, pos token.Pos, depth int) []routerRes {
	m := g.m
	if path, ok := m.importOf(e, v.X); ok {
		if pkg := m.ByImport[path]; pkg != nil {
			if vi, ok := pkg.Vars[v.Sel.Name]; ok && vi.value != nil {
				fn := &Func{Name: "<pkg>", Type: &ast.FuncType{}, Body: &ast.BlockStmt{}, File: vi.file}
				return g.router(&Env{fn: fn, vars: map[string]*T{}}, vi.value, vi.value.Pos(), depth-1)
			}
		}
		return g.unreadRouter("router `" + g.src(v) + "` is not readable")
	}
	if m.Infer(e, v).is("gin.Engine") {
		return []routerRes{{exact: true, engine: true, chain: []string{"engine"}}}
	}
	ti := m.Infer(e, v.X).deref().rowTypeAny()
	if ti == nil {
		return g.unreadRouter("router `" + g.src(v) + "` has no struct type the extractor can read")
	}
	type found struct {
		x    ast.Expr
		site litSite
	}
	var all []found
	for _, ls := range g.structLits[ti] {
		for _, el := range ls.lit.Elts {
			if kv, ok := el.(*ast.KeyValueExpr); ok {
				if k, ok := kv.Key.(*ast.Ident); ok && k.Name == v.Sel.Name {
					all = append(all, found{kv.Value, ls})
				}
			}
		}
	}
	if len(all) == 0 {
		return g.unreadRouter(fmt.Sprintf("no composite literal of %s sets %s", ti.Name, v.Sel.Name))
	}
	distinct := map[string]bool{}
	for _, f := range all {
		distinct[g.src(f.x)] = true
	}
	if len(distinct) > 1 {
		var locs []string
		for _, f := range all {
			locs = append(locs, fmt.Sprintf("%s:%d", f.site.fn.File.Rel, g.line(f.x.Pos())))
		}
		sort.Strings(locs)
		return g.unreadRouter(fmt.Sprintf("%d composite literals of %s set %s differently (%s)", len(distinct), ti.Name, v.Sel.Name, strings.Join(locs, ", ")))
	}
	f := all[0]
	alts := g.router(f.site.e, f.x, f.x.Pos(), depth-1)
	for i := range alts {
		alts[i].chain = append(alts[i].chain, fmt.Sprintf("%s.%s from %s:%d", ti.Name, v.Sel.Name, f.site.fn.File.Rel, g.line(f.x.Pos())))
	}
	return alts
}

// ---- handlers and middleware ------------------------------------------------------------

func (g *gx) funcTyped(f *File, typ ast.Expr) bool {
	switch t := typ.(type) {
	case *ast.FuncType:
		return true
	case *ast.Ident:
		if ti := f.Pkg.Types[t.Name]; ti != nil {
			_, ok := ti.Under.(*ast.FuncType)
			return ok
		}
	case *ast.SelectorExpr:
		if x, ok := t.X.(*ast.Ident); ok {
			if f.Imports[x.Name] == ginPath && t.Sel.Name == "HandlerFunc" {
				return true
			}
			if pkg := g.m.ByImport[f.Imports[x.Name]]; pkg != nil {
				if ti := pkg.Types[t.Sel.Name]; ti != nil {
					_, ok := ti.Under.(*ast.FuncType)
					return ok
				}
			}
		}
	case *ast.IndexExpr:
		return g.funcTyped(f, t.X)
	}
	return false
}

// wrapperParam answers the index of the func-typed parameter the only function literal W
// returns calls (W is then a wrapper: the handler is the argument), else -1.
func (g *gx) wrapperParam(w *Func) int {
	if v, ok := g.wrapMemo[w]; ok {
		return v
	}
	idx := -1
	var lits []*ast.FuncLit
	ast.Inspect(w.Body, func(n ast.Node) bool {
		switch v := n.(type) {
		case *ast.FuncLit:
			return false
		case *ast.ReturnStmt:
			for _, r := range v.Results {
				if l, ok := unparen(r).(*ast.FuncLit); ok {
					lits = append(lits, l)
				}
			}
		}
		return true
	})
	if len(lits) == 1 {
		params := w.Params()
		called := map[int]bool{}
		ast.Inspect(lits[0].Body, func(n ast.Node) bool {
			if call, ok := n.(*ast.CallExpr); ok {
				if id, ok := unparen(call.Fun).(*ast.Ident); ok {
					for i, p := range params {
						if p.name == id.Name && g.funcTyped(w.File, p.typ) {
							called[i] = true
						}
					}
				}
			}
			return true
		})
		if len(called) == 1 {
			for i := range called {
				idx = i
			}
			g.findWrapperWriter(w, lits[0], params[idx].name)
		}
	}
	g.wrapMemo[w] = idx
	return idx
}

// findWrapperWriter reads `if err := fn(c); err != nil { Write(c, err) }` and
// `out, err := fn(c); if err != nil { Write(c, err) }`: the function the literal hands the
// handler's error to.
func (g *gx) findWrapperWriter(w *Func, lit *ast.FuncLit, param string) {
	m := g.m
	lf := m.LitFunc(lit, w)
	le := m.EnvOf(lf)
	names := map[string]bool{}
	ast.Inspect(lit.Body, func(n ast.Node) bool {
		as, ok := n.(*ast.AssignStmt)
		if !ok || len(as.Rhs) != 1 {
			return true
		}
		call, ok := unparen(as.Rhs[0]).(*ast.CallExpr)
		if !ok || rootIdent(call.Fun) != param {
			return true
		}
		for _, l := range as.Lhs {
			if id, ok := l.(*ast.Ident); ok && id.Name != "_" {
				names[id.Name] = true
			}
		}
		return true
	})
	if len(names) == 0 {
		return
	}
	ast.Inspect(lit.Body, func(n ast.Node) bool {
		c, ok := n.(*ast.CallExpr)
		if !ok || g.wrapWriter[w] != nil {
			return true
		}
		callee := m.Callee(le, c)
		if callee == nil || callee.Lit != nil {
			return true
		}
		ps := callee.Params()
		for i, a := range c.Args {
			if ai, ok := a.(*ast.Ident); ok && names[ai.Name] && i < len(ps) &&
				m.TypeExpr(callee.File, ps[i].typ).Kind == "error" {
				g.wrapWriter[w] = callee
			}
		}
		return true
	})
}

// resolveFn reads a handler or middleware expression through locals, parameters,
// wrappers and factories.
func (g *gx) resolveFn(e *Env, x ast.Expr, depth int) hres {
	m := g.m
	var res hres
	if depth < 0 {
		res.unread = "handler expression is too deep to read"
		return res
	}
	for step := 0; step < 12; step++ {
		x = unparen(x)
		switch v := x.(type) {
		case *ast.FuncLit:
			res.fn = m.LitFunc(v, e.fn)
			return res
		case *ast.Ident:
			if de := defEnv(e, v.Name); de != nil {
				if lit := de.lits[v.Name]; lit != nil {
					res.fn = m.LitFunc(lit, de.fn)
					return res
				}
				vals := de.values[v.Name]
				if len(vals) == 1 {
					x, e = vals[0], de
					continue
				}
				if len(vals) > 1 {
					res.unread = "`" + v.Name + "` is assigned more than once"
					return res
				}
				return g.resolveParam(de, v.Name, depth, res)
			}
			if fn := e.fn.File.Pkg.Funcs[v.Name]; fn != nil {
				res.fn = fn
				return res
			}
			if vi, ok := e.fn.File.Pkg.Vars[v.Name]; ok && vi.value != nil {
				pe := &Env{fn: &Func{Name: "<pkg>", Type: &ast.FuncType{}, Body: &ast.BlockStmt{}, File: vi.file}, vars: map[string]*T{}}
				x, e = vi.value, pe
				continue
			}
			res.unread = "`" + v.Name + "` is not a function the extractor can read"
			return res
		case *ast.SelectorExpr:
			if path, ok := m.importOf(e, v.X); ok && m.ByImport[path] == nil {
				res.external = path + "." + v.Sel.Name
				return res
			}
			if fn := g.funcRef(e, v, 0); fn != nil {
				res.fn = fn
				return res
			}
			res.unread = "`" + g.src(v) + "` is not a function the extractor can read"
			return res
		case *ast.CallExpr:
			fun := unparen(v.Fun)
			for {
				if ix, ok := fun.(*ast.IndexExpr); ok {
					fun = ix.X
				} else if il, ok := fun.(*ast.IndexListExpr); ok {
					fun = il.X
				} else {
					break
				}
			}
			if sel, ok := fun.(*ast.SelectorExpr); ok {
				if path, ok := m.importOf(e, sel.X); ok && m.ByImport[path] == nil {
					res.external = path + "." + sel.Sel.Name
					return res
				}
			}
			callee := m.Callee(e, &ast.CallExpr{Fun: fun, Args: v.Args})
			if callee == nil || callee.Lit != nil {
				res.unread = "the callee of `" + g.src(v) + "` is not readable"
				return res
			}
			if idx := g.wrapperParam(callee); idx >= 0 && idx < len(v.Args) {
				res.wrappers = append(res.wrappers, callee)
				x = v.Args[idx]
				continue
			}
			// a factory: the function literal it returns, or the call it returns
			ce := m.EnvOf(callee)
			var lits []*ast.FuncLit
			var rets []ast.Expr
			ast.Inspect(callee.Body, func(n ast.Node) bool {
				switch r := n.(type) {
				case *ast.FuncLit:
					return false
				case *ast.ReturnStmt:
					if len(r.Results) == 1 {
						rets = append(rets, r.Results[0])
						if l, ok := unparen(r.Results[0]).(*ast.FuncLit); ok {
							lits = append(lits, l)
						}
					}
				}
				return true
			})
			switch {
			case len(lits) == 1:
				res.fn = m.LitFunc(lits[0], callee)
				res.factory = true
				return res
			case len(rets) == 1:
				x, e = rets[0], ce
				continue
			}
			res.unread = "factory " + callee.Name + " returns more than one function"
			return res
		case *ast.IndexExpr:
			x = v.X
			continue
		default:
			res.unread = "`" + g.src(x) + "` is not a function the extractor can read"
			return res
		}
	}
	res.unread = "handler expression is too deep to read"
	return res
}

// resolveParam reads a function parameter through the call sites of its function.
func (g *gx) resolveParam(de *Env, name string, depth int, res hres) hres {
	fn := de.fn
	idx := -1
	for i, p := range fn.Params() {
		if p.name == name {
			idx = i
		}
	}
	sites := g.calls[fn]
	if idx < 0 || len(sites) == 0 {
		res.unread = "parameter `" + name + "` of " + fn.Name + " has no call site in the module"
		return res
	}
	if len(sites) > 4 {
		res.unread = fmt.Sprintf("parameter `%s` of %s has %d call sites", name, fn.Name, len(sites))
		return res
	}
	var first *hres
	for _, s := range sites {
		if idx >= len(s.call.Args) {
			res.unread = "argument missing at a call site of " + fn.Name
			return res
		}
		r := g.resolveFn(s.e, s.call.Args[idx], depth-1)
		if r.unread != "" || (r.fn == nil && r.external == "") {
			res.unread = r.unread
			if res.unread == "" {
				res.unread = "parameter `" + name + "` is not readable at a call site"
			}
			return res
		}
		if first == nil {
			rr := r
			first = &rr
			continue
		}
		if r.fn != first.fn || r.external != first.external {
			res.unread = fmt.Sprintf("parameter `%s` of %s differs between its %d call sites", name, fn.Name, len(sites))
			return res
		}
	}
	out := *first
	out.wrappers = append(append([]*Func(nil), res.wrappers...), first.wrappers...)
	return out
}

func pathTemplate(p string) string {
	segs := strings.Split(p, "/")
	for i, s := range segs {
		if strings.HasPrefix(s, ":") || strings.HasPrefix(s, "*") {
			segs[i] = "{" + s[1:] + "}"
		}
	}
	return strings.Join(segs, "/")
}

func pathParams(p string) []string {
	var out []string
	for _, s := range strings.Split(p, "/") {
		if (strings.HasPrefix(s, ":") || strings.HasPrefix(s, "*")) && len(s) > 1 {
			out = append(out, s[1:])
		}
	}
	return out
}

// useCall is one `x.Use(...)` call of the module.
type useCall struct {
	fn   *Func
	e    *Env
	call *ast.CallExpr
	recv ast.Expr
}

func (g *gx) allUseCalls() []useCall {
	if g.useIdx != nil {
		return *g.useIdx
	}
	out := []useCall{}
	for _, fn := range g.m.AllFuncs() {
		e := g.m.EnvOf(fn)
		stmtWalk(fn.Body, func(call *ast.CallExpr, _ []ast.Node) {
			if sel, ok := call.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "Use" {
				out = append(out, useCall{fn, e, call, sel.X})
			}
		})
	}
	g.useIdx = &out
	return out
}

// useCallsOn answers the middleware `x.Use(...)` adds to a router held in a struct field
// or a package variable: the calls in the registering function before pos apply; one in
// another function has an order the extractor cannot know, so it is reported.
func (g *gx) useCallsOn(e *Env, x ast.Expr, pos token.Pos) ([]mwItem, []string) {
	m := g.m
	var match func(u useCall) bool
	switch v := x.(type) {
	case *ast.SelectorExpr:
		if _, ok := m.importOf(e, v.X); ok {
			want := g.src(v)
			match = func(u useCall) bool { return g.src(u.recv) == want }
		} else {
			ti := m.Infer(e, v.X).deref().rowTypeAny()
			if ti == nil {
				return nil, nil
			}
			match = func(u useCall) bool {
				sel, ok := u.recv.(*ast.SelectorExpr)
				return ok && sel.Sel.Name == v.Sel.Name && m.Infer(u.e, sel.X).deref().rowTypeAny() == ti
			}
		}
	case *ast.Ident:
		pkg := e.fn.File.Pkg
		match = func(u useCall) bool {
			id, ok := u.recv.(*ast.Ident)
			if !ok || id.Name != v.Name || u.fn.File.Pkg != pkg {
				return false
			}
			_, local := u.e.lookup(id.Name)
			return !local
		}
	default:
		return nil, nil
	}
	var mws []mwItem
	var foreign []string
	for _, u := range g.allUseCalls() {
		if !match(u) {
			continue
		}
		where := fmt.Sprintf("%s:%d", u.fn.File.Rel, g.line(u.call.Pos()))
		if u.fn == e.fn && u.call.Pos() < pos {
			for _, a := range u.call.Args {
				mws = append(mws, mwItem{u.e, a, "Use at " + where})
			}
		} else if u.fn != e.fn {
			foreign = append(foreign, "middleware is added to `"+g.src(x)+"` in another function ("+where+"): its order against the routes is not read")
		}
	}
	return mws, foreign
}
