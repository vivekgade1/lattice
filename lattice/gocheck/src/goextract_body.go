// goextract_body: a request body decoded without a Gin bind method in the handler (node GD).
//
//	json.Unmarshal(raw, &x)                 raw read from the request body: c.GetRawData(),
//	                                        io.ReadAll of an expression over c.Request.Body,
//	                                        or a local every assignment of which is one
//	json.NewDecoder(<body>).Decode(&x)      the decoder built over c.Request.Body
//	helper(c, &x)                           a module function that hands one of its
//	                                        parameters to such a decode or to a Gin bind
//	                                        method (a binder): the argument is bound
//
// A destination with no struct type the extractor can read is skipped silently: the
// function that decodes into its own parameter is read at its call sites.
package main

import "go/ast"

// isBodyReader reports whether x is (or wraps) the request body: `c.Request.Body`.
func (g *gx) isBodyReader(e *Env, x ast.Expr) bool {
	found := false
	ast.Inspect(x, func(n ast.Node) bool {
		if found {
			return false
		}
		if s, ok := n.(*ast.SelectorExpr); ok && s.Sel.Name == "Body" {
			if r, ok := unparen(s.X).(*ast.SelectorExpr); ok && r.Sel.Name == "Request" && g.m.isCtx(e, r.X) {
				found = true
			}
		}
		return !found
	})
	return found
}

// isBodyRead reports whether call reads the request body's bytes: c.GetRawData(), or
// io.ReadAll / ioutil.ReadAll of the body.
func (g *gx) isBodyRead(e *Env, call *ast.CallExpr) bool {
	sel, ok := unparen(call.Fun).(*ast.SelectorExpr)
	if !ok {
		return false
	}
	if sel.Sel.Name == "GetRawData" && len(call.Args) == 0 && g.m.isCtx(e, sel.X) {
		return true
	}
	if path, ok := g.m.importOf(e, sel.X); ok && (path == "io" || path == "io/ioutil") && sel.Sel.Name == "ReadAll" && len(call.Args) == 1 {
		return g.isBodyReader(e, call.Args[0])
	}
	return false
}

// fromBody reports whether x holds bytes read from the request body: a body read in x, or a
// local whose every assignment is one (a self-referencing reassignment such as
// `raw = bytes.TrimSpace(raw)` keeps it).
func (g *gx) fromBody(e *Env, x ast.Expr, seen map[string]bool, depth int) bool {
	if depth < 0 {
		return false
	}
	found := false
	ast.Inspect(x, func(n ast.Node) bool {
		if found {
			return false
		}
		switch s := n.(type) {
		case *ast.FuncLit:
			return false
		case *ast.CallExpr:
			if g.isBodyRead(e, s) {
				found = true
			}
		case *ast.Ident:
			if seen[s.Name] || g.localFromBody(e, s.Name, seen, depth-1) {
				found = true
			}
		}
		return !found
	})
	return found
}

func (g *gx) localFromBody(e *Env, name string, seen map[string]bool, depth int) bool {
	de := defEnv(e, name)
	if de == nil || de.fn.Body == nil {
		return false
	}
	for _, p := range de.fn.Params() {
		if p.name == name {
			return false
		}
	}
	var vals []ast.Expr
	ast.Inspect(de.fn.Body, func(n ast.Node) bool {
		switch s := n.(type) {
		case *ast.FuncLit:
			return n == ast.Node(de.fn.Lit)
		case *ast.AssignStmt:
			for i, l := range s.Lhs {
				if id, ok := l.(*ast.Ident); ok && id.Name == name {
					switch {
					case len(s.Rhs) == len(s.Lhs):
						vals = append(vals, s.Rhs[i])
					case len(s.Rhs) == 1 && i == 0:
						vals = append(vals, s.Rhs[0])
					default:
						vals = append(vals, nil)
					}
				}
			}
		case *ast.ValueSpec:
			for i, nm := range s.Names {
				if nm.Name == name {
					switch {
					case len(s.Values) == len(s.Names):
						vals = append(vals, s.Values[i])
					case len(s.Values) == 1 && i == 0:
						vals = append(vals, s.Values[0])
					default:
						vals = append(vals, nil)
					}
				}
			}
		}
		return true
	})
	if len(vals) == 0 {
		return false
	}
	inner := map[string]bool{name: true}
	for k := range seen {
		inner[k] = true
	}
	for _, v := range vals {
		if v == nil || !g.fromBody(de, v, inner, depth) {
			return false
		}
	}
	return true
}

// jsonBodyDest answers the destination of `json.Unmarshal(<body bytes>, dst)` or
// `json.NewDecoder(<body>).Decode(dst)` (the decoder directly or through a local holding it).
func (g *gx) jsonBodyDest(e *Env, call *ast.CallExpr) ast.Expr {
	sel, ok := unparen(call.Fun).(*ast.SelectorExpr)
	if !ok || len(call.Args) == 0 {
		return nil
	}
	if path, ok := g.m.importOf(e, sel.X); ok {
		if path == "encoding/json" && sel.Sel.Name == "Unmarshal" && len(call.Args) == 2 && g.fromBody(e, call.Args[0], map[string]bool{}, 4) {
			return call.Args[1]
		}
		return nil
	}
	if sel.Sel.Name != "Decode" || len(call.Args) != 1 {
		return nil
	}
	dec := unparen(sel.X)
	if id, ok := dec.(*ast.Ident); ok {
		de := defEnv(e, id.Name)
		if de == nil || len(de.values[id.Name]) != 1 {
			return nil
		}
		e, dec = de, unparen(de.values[id.Name][0])
	}
	nc, ok := dec.(*ast.CallExpr)
	if !ok || len(nc.Args) != 1 {
		return nil
	}
	ns, ok := unparen(nc.Fun).(*ast.SelectorExpr)
	if !ok || ns.Sel.Name != "NewDecoder" {
		return nil
	}
	if path, ok := g.m.importOf(e, ns.X); !ok || path != "encoding/json" || !g.isBodyReader(e, nc.Args[0]) {
		return nil
	}
	return call.Args[0]
}

// ctxBindSource answers the source a Gin bind method's fields come from ("" when call is
// not one).
func (g *gx) ctxBindSource(e *Env, call *ast.CallExpr) string {
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || !bindMethods[sel.Sel.Name] || len(call.Args) == 0 || !g.m.isCtx(e, sel.X) {
		return ""
	}
	switch sel.Sel.Name {
	case "ShouldBindQuery", "BindQuery":
		return "query"
	case "ShouldBindUri", "BindUri":
		return "path"
	}
	return "body"
}

// binderParams answers the parameters fn decodes the request into (index -> source),
// through other binders too (a cycle reads as no binder).
func (g *gx) binderParams(fn *Func) map[int]string {
	if fn == nil || fn.Body == nil || fn.Lit != nil {
		return nil
	}
	if g.binders == nil {
		g.binders = map[*Func]map[int]string{}
	}
	if out, ok := g.binders[fn]; ok {
		return out
	}
	g.binders[fn] = nil
	idx := map[string]int{}
	for i, p := range fn.Params() {
		if p.name != "" && p.name != "_" {
			idx[p.name] = i
		}
	}
	out := map[int]string{}
	if len(idx) > 0 {
		e := g.m.EnvOf(fn)
		param := func(x ast.Expr) (int, bool) {
			id, ok := stripAddr(unparen(x)).(*ast.Ident)
			if !ok {
				return 0, false
			}
			i, ok := idx[id.Name]
			return i, ok && defEnv(e, id.Name) == e
		}
		stmtWalk(fn.Body, func(call *ast.CallExpr, _ []ast.Node) {
			if src := g.ctxBindSource(e, call); src != "" {
				if i, ok := param(call.Args[0]); ok {
					out[i] = src
				}
				return
			}
			if dst := g.jsonBodyDest(e, call); dst != nil {
				if i, ok := param(dst); ok {
					out[i] = "body"
				}
				return
			}
			callee := g.m.Callee(e, call)
			if callee == nil || callee == fn {
				return
			}
			ps := g.binderParams(callee)
			for j := range call.Args {
				if src, ok := ps[j]; ok {
					if i, ok := param(call.Args[j]); ok {
						out[i] = src
					}
				}
			}
		})
	}
	if len(out) == 0 {
		out = nil
	}
	g.binders[fn] = out
	return out
}

// boundElsewhere answers the values a call binds the request into other than through a Gin
// bind method on the context: a JSON decode of the body, or a binder helper's arguments.
func (g *gx) boundElsewhere(e *Env, call *ast.CallExpr) []boundArg {
	if dst := g.jsonBodyDest(e, call); dst != nil {
		return []boundArg{{dst, "body"}}
	}
	callee := g.m.Callee(e, call)
	if callee == nil {
		return nil
	}
	ps := g.binderParams(callee)
	var out []boundArg
	for j := 0; j < len(call.Args); j++ {
		if src, ok := ps[j]; ok {
			out = append(out, boundArg{call.Args[j], src})
		}
	}
	return out
}

type boundArg struct {
	x   ast.Expr
	src string
}

// boundStruct answers the struct type of a bound value (a named struct, or one declared in
// place), nil when there is none to read.
func (g *gx) boundStruct(e *Env, x ast.Expr) *TypeInfo {
	if ti := g.m.Infer(e, stripAddr(x)).deref().rowTypeAny(); ti != nil {
		return ti
	}
	return g.anonStructOf(e, x)
}
