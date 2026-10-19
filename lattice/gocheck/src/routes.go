// Gin route registrations and the functions a request runs.
package main

import (
	"go/ast"
	"sort"
	"strings"
)

var routeMethods = map[string]string{
	"GET": "GET", "POST": "POST", "PUT": "PUT", "PATCH": "PATCH", "DELETE": "DELETE",
	"HEAD": "HEAD", "OPTIONS": "OPTIONS", "Any": "ANY", "Handle": "", "Match": "",
}

// Route is one registration: `router.POST("/jobs", mw..., handler)`.
type Route struct {
	Method, Path string
	Exact        bool // the router's prefix was read (else Path is the tail)
	Handler      *Func
	Middleware   []*Func
	MwExprs      []mwRef
	Unreadable   bool // a handler or middleware the checker cannot resolve
	File         *File
	Line         int
	Wraps        []*Func // K1: the wrapper literals of the handler and middleware
	Bound        bool    // K1: the handler is a wrapper's argument
}

type mwRef struct {
	e *Env
	x ast.Expr
}

// Key answers "METHOD /path".
func (r *Route) Key() string { return r.Method + " " + r.Path }

// Root is a function a request runs first: a route's handler, a middleware, or a
// function taking a *gin.Context that nothing in the module calls.
type Root struct {
	Fn         *Func
	Route      *Route
	Middleware bool
}

// Label answers how a message names the root: `POST /jobs (CreateJob)`.
func (r *Root) Label() string {
	if r.Route != nil {
		return r.Route.Key() + " (" + r.Fn.Name + ")"
	}
	return r.Fn.Name
}

// Key answers the root's part of a finding key.
func (r *Root) Key() string {
	if r.Route != nil {
		return r.Route.Key()
	}
	return r.Fn.File.Rel + ":" + r.Fn.Name
}

type callSite struct {
	e    *Env
	call *ast.CallExpr
}

func (c *checker) callIndex() map[*Func][]callSite {
	idx := map[*Func][]callSite{}
	for _, fn := range c.m.AllFuncs() {
		e := c.m.EnvOf(fn)
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			if call, ok := n.(*ast.CallExpr); ok {
				if callee := c.m.Callee(e, call); callee != nil && callee != fn {
					idx[callee] = append(idx[callee], callSite{e, call})
				}
			}
			return true
		})
	}
	return idx
}

func (c *checker) findRoutes() {
	m := c.m
	calls := c.callIndex()
	registered := map[*Func]bool{}
	for _, fn := range m.AllFuncs() {
		e := m.EnvOf(fn)
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			sel, ok := call.Fun.(*ast.SelectorExpr)
			if !ok {
				return true
			}
			method, known := routeMethods[sel.Sel.Name]
			if !known {
				return true
			}
			args := call.Args
			switch sel.Sel.Name {
			case "Handle":
				if len(args) < 3 {
					return true
				}
				s, ok := m.constString(e, args[0])
				if !ok {
					return true
				}
				method = strings.ToUpper(s)
				args = args[1:]
			case "Match":
				if len(args) < 3 {
					return true
				}
				method = "MATCH"
				args = args[1:]
			}
			if len(args) < 2 {
				return true
			}
			path, ok := m.constString(e, args[0])
			if !ok {
				return true
			}
			xt := m.Infer(e, sel.X)
			router := xt.is("gin.Engine") || xt.is("gin.RouterGroup") || xt.is("gin.IRouter") ||
				xt.is("gin.IRoutes")
			handlerExprs := args[1:]
			last := handlerExprs[len(handlerExprs)-1]
			handler := m.FuncRef(e, last)
			bound, wl := c.bind(e, last)
			if bound != nil {
				handler = bound
			}
			if !router && (handler == nil || !m.hasCtxParam(handler) && wl == nil) {
				return true
			}
			prefix, mws, exact, unreadable := c.routerPrefix(e, sel.X, calls, 2)
			r := &Route{Method: method, Path: joinPath(prefix, path), Exact: exact,
				Handler: handler, File: fn.File, Line: m.Line(call.Pos()), Unreadable: unreadable}
			if wl != nil {
				r.Bound, r.Wraps = true, []*Func{wl}
			}
			for _, h := range handlerExprs[:len(handlerExprs)-1] {
				mws = append(mws, mwRef{e, h})
			}
			r.MwExprs = mws
			for _, mw := range mws {
				if ginNeutral(mw.e, mw.x) {
					continue // owner-neutral library middleware: neither a guard nor unreadable
				}
				f := m.FuncRef(mw.e, mw.x)
				if b, wl := c.bind(mw.e, mw.x); b != nil {
					f, r.Wraps = b, append(r.Wraps, wl)
				}
				if f == nil {
					r.Unreadable = true
					continue
				}
				r.Middleware = append(r.Middleware, f)
			}
			if handler == nil {
				r.Unreadable = true
			} else {
				registered[handler] = true
			}
			for _, f := range r.Middleware {
				registered[f] = true
			}
			c.routes = append(c.routes, r)
			return true
		})
	}
	sort.SliceStable(c.routes, func(i, j int) bool {
		a, b := c.routes[i], c.routes[j]
		if a.File.Rel != b.File.Rel {
			return a.File.Rel < b.File.Rel
		}
		return a.Line < b.Line
	})
	seen := map[*Func]bool{}
	for _, r := range c.routes {
		if r.Handler != nil && (r.Bound || !seen[r.Handler]) {
			seen[r.Handler] = true
			c.roots = append(c.roots, &Root{Fn: r.Handler, Route: r})
		}
	}
	for _, r := range c.routes {
		for _, mw := range r.Middleware {
			if !seen[mw] {
				seen[mw] = true
				c.roots = append(c.roots, &Root{Fn: mw, Middleware: true})
			}
		}
	}
	// a function taking a *gin.Context that nothing calls: registered where the checker
	// cannot read it (a route table, reflection)
	for _, fn := range m.Funcs {
		if seen[fn] || registered[fn] || len(calls[fn]) > 0 || !m.hasCtxParam(fn) {
			continue
		}
		seen[fn] = true
		c.roots = append(c.roots, &Root{Fn: fn})
	}
}

// bind reads K1: x is W(arg, ...) for a wrapper W (the extractor's wrapperParam), or a factory
// whose only return is such a call. It answers arg's function and W's literal. One level: an
// argument that is a wrapper's literal, or does not resolve, binds nothing.
func (c *checker) bind(e *Env, x ast.Expr) (*Func, *Func) {
	g := &gx{m: c.m, wrapMemo: map[*Func]int{}, wrapWriter: map[*Func]*Func{}}
	for range 3 {
		call, ok := unparen(x).(*ast.CallExpr)
		if !ok {
			return nil, nil
		}
		w := c.m.Callee(e, call)
		if w == nil || w.Lit != nil {
			return nil, nil
		}
		var rets []ast.Expr
		ast.Inspect(w.Body, func(n ast.Node) bool {
			if r, ok := n.(*ast.ReturnStmt); ok {
				rets = append(rets, r.Results...)
			}
			_, lit := n.(*ast.FuncLit)
			return !lit
		})
		if i := g.wrapperParam(w); i >= 0 && i < len(call.Args) {
			arg := c.m.FuncRef(e, call.Args[i])
			if arg == nil || arg.Lit != nil && g.wrapperParam(arg.Parent) >= 0 {
				return nil, nil
			}
			for _, r := range rets {
				if l, ok := unparen(r).(*ast.FuncLit); ok {
					return arg, c.m.LitFunc(l, w)
				}
			}
		}
		if len(rets) != 1 {
			return nil, nil
		}
		x, e = rets[0], c.m.EnvOf(w)
	}
	return nil, nil
}

// Gin's own logging and recovery middleware: none of them authenticates, scopes or refuses by
// owner, so a route behind one stays readable. Every other outside middleware is not.
var ginNeutralFuncs = map[string]bool{
	"Recovery": true, "RecoveryWithWriter": true, "CustomRecovery": true, "CustomRecoveryWithWriter": true,
	"Logger": true, "LoggerWithWriter": true, "LoggerWithFormatter": true, "LoggerWithConfig": true,
}

// ginNeutral reports whether x calls one of them (gin.Recovery(), gin.Logger(), ...).
func ginNeutral(e *Env, x ast.Expr) bool {
	call, ok := unparen(x).(*ast.CallExpr)
	if !ok {
		return false
	}
	sel, ok := unparen(call.Fun).(*ast.SelectorExpr)
	if !ok || !ginNeutralFuncs[sel.Sel.Name] {
		return false
	}
	id, ok := sel.X.(*ast.Ident)
	return ok && e.fn.File.Imports[id.Name] == ginPath
}

func joinPath(prefix, path string) string {
	if prefix == "" {
		if path == "" {
			return "/"
		}
		return path
	}
	p := strings.TrimSuffix(prefix, "/")
	if path == "" || path == "/" {
		if p == "" {
			return "/"
		}
		return p
	}
	if !strings.HasPrefix(path, "/") {
		path = "/" + path
	}
	return p + path
}

// routerPrefix answers the path prefix of a router expression, the middleware its groups
// add, whether the prefix was read, and whether some middleware could not be.
func (c *checker) routerPrefix(e *Env, x ast.Expr, calls map[*Func][]callSite, depth int) (string, []mwRef, bool, bool) {
	m := c.m
	switch v := unparen(x).(type) {
	case *ast.CallExpr:
		sel, ok := v.Fun.(*ast.SelectorExpr)
		if !ok {
			return "", nil, false, false
		}
		switch sel.Sel.Name {
		case "Group":
			p, mws, exact, unr := c.routerPrefix(e, sel.X, calls, depth)
			if len(v.Args) == 0 {
				return p, mws, false, unr
			}
			s, ok := m.constString(e, v.Args[0])
			if !ok {
				exact = false
			}
			for _, a := range v.Args[1:] {
				mws = append(mws, mwRef{e, a})
			}
			return joinPath(p, s), mws, exact, unr
		case "Use":
			p, mws, exact, unr := c.routerPrefix(e, sel.X, calls, depth)
			for _, a := range v.Args {
				mws = append(mws, mwRef{e, a})
			}
			return p, mws, exact, unr
		}
		if xt := m.Infer(e, v); xt.is("gin.Engine") {
			return "", nil, true, false
		}
		return "", nil, false, false
	case *ast.Ident:
		mws := c.uses(e, v.Name)
		if g := e.group(v.Name); g != nil {
			p, more, exact, unr := c.routerPrefix(e, g, calls, depth)
			return p, append(more, mws...), exact, unr
		}
		// a router handed in: read it from the function's only caller
		for i, p := range e.fn.Params() {
			if p.name != v.Name {
				continue
			}
			engine := m.TypeExpr(e.fn.File, p.typ).is("gin.Engine")
			sites := calls[e.fn]
			if depth > 0 && len(sites) == 1 && i < len(sites[0].call.Args) {
				s := sites[0]
				pre, more, exact, unr := c.routerPrefix(s.e, s.call.Args[i], calls, depth-1)
				return pre, append(more, mws...), exact || engine, unr
			}
			// the caller's middleware (its Use calls) cannot be read
			return "", mws, engine, true
		}
		for _, val := range e.valuesOf(v.Name) {
			if call, ok := val.(*ast.CallExpr); ok {
				if xt := m.Infer(e, call); xt.is("gin.Engine") {
					return "", mws, true, false
				}
			}
		}
		return "", mws, false, false
	}
	return "", nil, false, false
}

// uses answers the middleware `name.Use(...)` adds anywhere in e's function.
func (c *checker) uses(e *Env, name string) []mwRef {
	var out []mwRef
	ast.Inspect(e.fn.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if ok && sel.Sel.Name == "Use" && rootIdent(sel.X) == name {
			for _, a := range call.Args {
				out = append(out, mwRef{e, a})
			}
		}
		return true
	})
	return out
}

// mentionsAdmin reports whether an expression names an admin concept: an identifier
// containing "admin", or a string literal of the admin role.
func (c *checker) mentionsAdmin(n ast.Node) bool {
	role := strings.ToLower(c.pol.AdminRole)
	found := false
	ast.Inspect(n, func(x ast.Node) bool {
		if found {
			return false
		}
		switch v := x.(type) {
		case *ast.Ident:
			if strings.Contains(strings.ToLower(v.Name), "admin") {
				found = true
			}
		case *ast.BasicLit:
			if s, ok := stringLit(v); ok {
				ls := strings.ToLower(s)
				if ls == "admin" || (role != "" && ls == role) || strings.Contains(ls, "admin") {
					found = true
				}
			}
		}
		return true
	})
	return found
}

// adminOnly reports whether a root is (or may be) admin-only: the spec says so, a
// middleware of its route names an admin concept, or its body tests one.
func (c *checker) adminOnly(r *Root) bool {
	if r.Route != nil {
		for _, ar := range c.pol.AdminRoutes {
			if (ar[0] == r.Route.Method || r.Route.Method == "ANY") && samePath(r.Route.Path, r.Route.Exact, ar[1]) {
				return true
			}
		}
		for _, mw := range r.Route.MwExprs {
			if c.mentionsAdmin(mw.x) {
				return true
			}
		}
		for _, f := range r.Route.Middleware {
			if c.mentionsAdmin(f.Body) && strings.Contains(strings.ToLower(f.Name), "admin") {
				return true
			}
		}
	}
	found := false
	ast.Inspect(r.Fn.Body, func(n ast.Node) bool {
		if found {
			return false
		}
		switch v := n.(type) {
		case *ast.IfStmt:
			if c.mentionsAdmin(v.Cond) {
				found = true
			}
		case *ast.SwitchStmt:
			if v.Tag != nil && c.mentionsAdmin(v.Tag) {
				found = true
			}
			for _, cl := range v.Body.List {
				for _, x := range cl.(*ast.CaseClause).List {
					if c.mentionsAdmin(x) {
						found = true
					}
				}
			}
		}
		return true
	})
	return found
}

// openRoute reports whether the spec states the route's owner scope as something other
// than the plain owner (anyone's rows, a public flag, a link's other side).
func (c *checker) openRoute(r *Root) bool {
	if r.Route == nil {
		return false
	}
	for _, or := range c.pol.OpenRoutes {
		if (or[0] == r.Route.Method || r.Route.Method == "ANY") && samePath(r.Route.Path, r.Route.Exact, or[1]) {
			return true
		}
	}
	return false
}
