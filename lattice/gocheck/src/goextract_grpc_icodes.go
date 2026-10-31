// goextract: interceptor codes per rpc (E3e), typed-error maps and descriptor tables (node GA2, 2026-10-30).

package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"slices"
	"strings"
)

// errType (GA2) answers the module struct type of x when it is an error type without GRPCStatus.
func (g *gx) errType(e *Env, x ast.Expr) *TypeInfo {
	t := g.m.Infer(e, x).deref()
	if readV5 || t.Kind != "named" || t.Named == nil || t.Named.Struct == nil || g.m.MethodOf(t.Named, "Error", 2) == nil {
		return nil
	}
	if g.m.MethodOf(t.Named, "GRPCStatus", 2) != nil {
		return nil
	}
	return t.Named
}

// asMapCodes (GA2, E3e) reads `status.Error(m[t.F], ..)` where t is the errors.As target of the handler's
// answer and m a package map literal: the code of each value of t's type the rpc's handler builds, by the
// constant its F is set to. ok is false when any of them is not read.
func (g *gx) asMapCodes(b *Func, e *Env, x ast.Expr, run *grpcRun) (out []int, ok bool) {
	ix, isIx := unparen(x).(*ast.IndexExpr)
	if !isIx || run.hand == nil {
		return nil, false
	}
	sel, isSel := unparen(ix.Index).(*ast.SelectorExpr)
	vi := g.pkgVar(b.File, e, ix.X)
	lit, _ := unparen(vi.value).(*ast.CompositeLit)
	if !isSel || lit == nil {
		return nil, false
	}
	t, v := g.src(sel.X), ""
	ast.Inspect(b.Body, func(n ast.Node) bool {
		if c, isC := n.(*ast.CallExpr); isC && len(c.Args) == 2 && g.src(c.Args[1]) == "&"+t {
			if path, nm, isPkg := pkgCall(b.File, e, c); isPkg && path == "errors" && nm == "As" {
				v = g.src(c.Args[0])
			}
		}
		return true
	})
	src, tuple, found := g.lastAssign(b, v, x.Pos())
	hc, isCall := unparen(src).(*ast.CallExpr)
	if v == "" || !found || !tuple || !isCall {
		return nil, false
	}
	fid, isID := unparen(hc.Fun).(*ast.Ident)
	handler := false
	for _, p := range b.Params() {
		handler = handler || isID && p.name == fid.Name && g.src(p.typ) == "grpc.UnaryHandler"
	}
	ti := g.m.Infer(e, sel.X).deref().Named
	if !handler || ti == nil {
		return nil, false
	}
	kid := func(k ast.Expr) string { s := g.src(k); return s[strings.LastIndex(s, ".")+1:] }
	for _, te := range run.hand.typed {
		if te.ti != ti {
			continue
		}
		cb, _, fv, args := g.siteValue(te.b, te.e, te.x, ti, sel.Sel.Name)
		if fv == nil {
			return nil, false
		}
		if id, isID := unparen(fv).(*ast.Ident); isID && cb != te.b && paramAt(cb, id) >= 0 && args[id.Name].x != nil {
			fv = args[id.Name].x
		}
		hit := false
		for _, el := range lit.Elts {
			if kv, isKV := el.(*ast.KeyValueExpr); isKV && kid(kv.Key) == kid(fv) {
				n, lok := g.codeOf(vi.file, nil, kv.Value, 0)
				if !lok {
					return nil, false
				}
				hit = true
				if !slices.Contains(out, n) {
					out = append(out, n)
				}
			}
		}
		if !hit {
			return nil, false
		}
	}
	slices.Sort(out)
	return out, true
}

// interceptorCodes (E3e) adds to each implemented rpc the status codes an interceptor that is not a literal
// auth gate (E3d reads those) answers on the paths its literal method tests allow for that rpc's method. A
// code under a method test that is not literal, after such a test that may return, or out of a module
// function the method name decides, reaches no rpc.
func (g *gx) interceptorCodes(gr *xGrpc, ics []xInterceptor, hand map[string]*errSrc) {
	for _, ic := range ics {
		if ic.fn == nil || ic.Kind == "auth_literal" || len(ic.fn.Params()) < 4 {
			continue
		}
		run := &grpcRun{memo: map[*Func]*errSrc{}, busy: map[*Func]bool{}}
		w := &icWalk{g: g, fn: ic.fn, e: g.m.EnvOf(ic.fn), info: ic.fn.Params()[2].name, run: run}
		for _, svc := range gr.Services {
			for _, r := range svc.Rpcs {
				if r.State != "implemented" {
					continue
				}
				w.method, w.codes, run.hand = "/"+rpcKey(svc.Name, r.Name), nil, hand[rpcKey(svc.Name, r.Name)]
				w.block(ic.fn.Body.List)
				for _, c := range w.codes {
					c.Kind, c.File, c.Line, c.In = "interceptor", ic.File, ic.Line, ic.Func
					have := false
					for _, o := range r.Codes {
						have = have || o.Name == c.Name && o.Kind == c.Kind
					}
					if !have {
						r.Codes = append(r.Codes, c)
					}
				}
				sortCodes(r.Codes)
			}
		}
	}
}

// icWalk walks one interceptor's body for one method.
type icWalk struct {
	g      *gx
	fn     *Func
	e      *Env
	info   string   // the *grpc.UnaryServerInfo parameter
	method string   // the full method name being read
	run    *grpcRun // the error reader's state
	codes  []xCode  // the status codes answered so far
}

const (
	condFalse = iota
	condTrue
	condAny    // not a test of the method: either way
	condUnread // a test of the method the walk cannot decide
)

// names: n mentions info, or a local whose last value before the use does.
func (w *icWalk) names(n ast.Node) bool { return w.namesAt(n, 0) }

func (w *icWalk) namesAt(n ast.Node, depth int) (found bool) {
	ast.Inspect(n, func(m ast.Node) bool {
		if id, ok := m.(*ast.Ident); ok && !found {
			if id.Name == w.info {
				found = true
			} else if _, local := w.e.lookup(id.Name); local && depth < 4 {
				if src, _, ok := w.g.lastAssign(w.fn, id.Name, id.Pos()); ok {
					found = w.namesAt(src, depth+1)
				}
			}
		}
		return !found
	})
	return
}

func (w *icWalk) isM(x ast.Expr) bool {
	s, ok := unparen(x).(*ast.SelectorExpr)
	return ok && s.Sel.Name == "FullMethod" && w.g.src(s.X) == w.info
}

// str reads a constant string, `+` concatenations included.
func (w *icWalk) str(x ast.Expr) (string, bool) {
	if b, ok := unparen(x).(*ast.BinaryExpr); ok && b.Op == token.ADD {
		l, lok := w.str(b.X)
		r, rok := w.str(b.Y)
		return l + r, lok && rok
	}
	return w.g.strOf(w.fn.File, w.e, x)
}

// cond decides a condition for w.method.
func (w *icWalk) cond(x ast.Expr) int {
	if x == nil || !w.names(x) {
		return condAny
	}
	of := func(b bool) int {
		if b {
			return condTrue
		}
		return condFalse
	}
	switch v := unparen(x).(type) {
	case *ast.UnaryExpr:
		if c := w.cond(v.X); v.Op == token.NOT && (c == condTrue || c == condFalse) {
			return of(c == condFalse)
		} else if v.Op == token.NOT {
			return c
		}
	case *ast.BinaryExpr:
		switch v.Op {
		case token.LAND, token.LOR:
			l, r := w.cond(v.X), w.cond(v.Y)
			stop := condFalse
			if v.Op == token.LOR {
				stop = condTrue
			}
			switch {
			case l == stop || r == stop:
				return stop
			case l == condUnread || r == condUnread:
				return condUnread
			case l == condAny || r == condAny:
				return condAny
			}
			return l
		case token.EQL, token.NEQ:
			m, o := v.X, v.Y
			if w.isM(o) {
				m, o = o, m
			}
			if s, ok := w.str(o); ok && w.isM(m) {
				return of((s == w.method) == (v.Op == token.EQL))
			}
		}
	case *ast.CallExpr:
		if p, nm, ok := pkgCall(w.fn.File, w.e, v); ok && p == "strings" && len(v.Args) == 2 && w.isM(v.Args[0]) {
			if s, ok := w.str(v.Args[1]); ok {
				switch nm {
				case "HasPrefix":
					return of(strings.HasPrefix(w.method, s))
				case "HasSuffix":
					return of(strings.HasSuffix(w.method, s))
				}
			}
		}
	case *ast.IndexExpr: // a literal set: map[string]bool{...}[info.FullMethod]
		if keys, ok := w.g.mapKeys(w.fn.File, w.e, v.X); ok && w.isM(v.Index) {
			return of(slices.Contains(keys, w.method))
		}
	}
	return condUnread
}

// block walks statements; done: every path through them returns (for w.method).
func (w *icWalk) block(list []ast.Stmt) (done bool) {
	for _, st := range list {
		if w.stmt(st) {
			return true
		}
	}
	return false
}

func (w *icWalk) stmt(st ast.Stmt) (done bool) {
	switch v := st.(type) {
	case *ast.ReturnStmt:
		if len(v.Results) > 0 {
			w.ret(v.Results[len(v.Results)-1], v.Pos())
		}
		return true
	case *ast.BlockStmt:
		return w.block(v.List)
	case *ast.IfStmt:
		c := w.cond(v.Cond)
		if v.Init != nil && w.names(v.Init) && c == condAny {
			c = condUnread
		}
		switch c {
		case condTrue:
			return w.block(v.Body.List)
		case condFalse:
			return v.Else != nil && w.stmt(v.Else)
		case condAny:
			a := w.block(v.Body.List)
			return a && v.Else != nil && w.stmt(v.Else)
		}
		// undecided: no code from either branch; a branch that may return ends what is known
		saved := w.codes
		a := w.block(v.Body.List)
		b := v.Else != nil && w.stmt(v.Else)
		w.codes = saved
		return a || b || returns(v.Body) || v.Else != nil && returns(v.Else)
	case *ast.SwitchStmt:
		if v.Tag != nil && w.names(v.Tag) || v.Init != nil && w.names(v.Init) {
			saved := w.codes
			defer func() { w.codes = saved }()
			return returns(v.Body)
		}
		all, def := true, false
		for _, cc := range v.Body.List {
			c := cc.(*ast.CaseClause)
			def = def || c.List == nil
			for _, x := range c.List {
				if w.cond(x) != condAny {
					saved := w.codes
					defer func() { w.codes = saved }()
					return returns(v.Body)
				}
			}
			all = w.block(c.Body) && all
		}
		return all && def
	case *ast.ForStmt, *ast.RangeStmt, *ast.SelectStmt, *ast.TypeSwitchStmt:
		saved := len(w.codes)
		ast.Inspect(st, func(n ast.Node) bool {
			if r, ok := n.(*ast.ReturnStmt); ok && len(r.Results) > 0 && !w.names(st) {
				w.ret(r.Results[len(r.Results)-1], r.Pos())
			}
			_, lit := n.(*ast.FuncLit)
			return !lit
		})
		if w.names(st) {
			w.codes = w.codes[:saved]
		}
	}
	return false
}

// returns: n has a return statement (outside function literals).
func returns(n ast.Node) (found bool) {
	ast.Inspect(n, func(m ast.Node) bool {
		_, r := m.(*ast.ReturnStmt)
		_, lit := m.(*ast.FuncLit)
		found = found || r
		return !found && !lit
	})
	return
}

// ret adds the status codes of a returned error, unless the method name decides them: a returned value
// that names the method or a local made from it, except a call that only logs the method.
func (w *icWalk) ret(x ast.Expr, at token.Pos) {
	if w.names(x) {
		c, ok := unparen(x).(*ast.CallExpr)
		callee := (*Func)(nil)
		if ok {
			callee = w.g.calleeOf(w.e, c)
		}
		if callee == nil {
			return
		}
		ps := callee.Params()
		for i, a := range c.Args {
			if w.names(a) && (i >= len(ps) || !w.g.onlyLogged(callee, ps[i].name)) {
				return
			}
		}
	}
	// a translation of the handler's answer: the rpc's own errors (D3), only a typed-error map is the interceptor's
	w.run.typed = w.fromHandler(x, 0)
	defer func() { w.run.typed = false }()
	for _, c := range w.g.evalErr(w.fn, w.e, x, at, 0, w.run).codes {
		if c.Kind == "status_error" {
			w.codes = append(w.codes, c)
		}
	}
}

// fromHandler: x is, or is made from, the answer of the interceptor's handler call.
func (w *icWalk) fromHandler(x ast.Node, depth int) (found bool) {
	ast.Inspect(x, func(n ast.Node) bool {
		switch v := n.(type) {
		case *ast.CallExpr:
			if id, ok := unparen(v.Fun).(*ast.Ident); ok {
				for _, p := range w.fn.Params() {
					found = found || p.name == id.Name && w.g.src(p.typ) == "grpc.UnaryHandler"
				}
			}
		case *ast.Ident:
			if _, local := w.e.lookup(v.Name); local && depth < 4 {
				if src, _, ok := w.g.lastAssign(w.fn, v.Name, v.Pos()); ok {
					found = found || w.fromHandler(src, depth+1)
				}
			}
		}
		return !found
	})
	return
}

// onlyLogged: every use of parameter p in fn is an argument of a call to a function outside the module,
// made as a statement (a log line), never a condition or a value.
func (g *gx) onlyLogged(fn *Func, p string) bool {
	if p == "" || p == "_" {
		return true
	}
	uses, logged := 0, 0
	e := g.m.EnvOf(fn)
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if id, ok := n.(*ast.Ident); ok && id.Name == p {
			uses++
		}
		if es, ok := n.(*ast.ExprStmt); ok {
			if c, ok := unparen(es.X).(*ast.CallExpr); ok && g.calleeOf(e, c) == nil {
				for _, a := range c.Args {
					if id, ok := unparen(a).(*ast.Ident); ok && id.Name == p {
						logged++
					}
				}
			}
		}
		return true
	})
	return uses == logged
}

// tableRegs (GA2) reads `RegisterService(v.desc, v.impl)` (or `Register<X>Server(r, v.impl)`) in a loop
// `for _, v := range T` where T is a slice literal, a local holding one, or a call of a module function
// whose every return is one: a registration per row, descriptor and implementation read where the row is
// written. nil: the call has no such shape.
func (g *gx) tableRegs(r *grpcReg, call *ast.CallExpr, fn *Func) (rs []*grpcReg) {
	e := g.m.EnvOf(fn)
	impl, _ := unparen(call.Args[1]).(*ast.SelectorExpr)
	desc, _ := unparen(call.Args[0]).(*ast.SelectorExpr)
	if impl == nil || r.svc && (desc == nil || g.src(desc.X) != g.src(impl.X)) {
		return nil
	}
	var rng *ast.RangeStmt
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if s, ok := n.(*ast.RangeStmt); ok && s.Value != nil && g.src(s.Value) == g.src(impl.X) && call.Pos() >= s.Body.Pos() && call.End() <= s.Body.End() {
			rng = s
		}
		return rng == nil
	})
	if rng == nil {
		return nil
	}
	et := g.m.Infer(e, impl.X).deref().rowTypeAny()
	if et == nil {
		return nil
	}
	type row struct {
		f  *File
		e  *Env
		cl *ast.CompositeLit
	}
	var rows []row
	var lits func(f *File, e *Env, x ast.Expr, depth int) bool
	lits = func(f *File, e *Env, x ast.Expr, depth int) bool {
		x = unparen(x)
		if id, ok := x.(*ast.Ident); ok && depth < 4 {
			if vals := e.valuesOf(id.Name); len(vals) == 1 && !multiAssigned(e, id.Name) {
				return lits(f, e, vals[0], depth+1)
			}
		}
		switch v := x.(type) {
		case *ast.CompositeLit:
			if _, isSlice := v.Type.(*ast.ArrayType); !isSlice {
				return false
			}
			for _, el := range v.Elts {
				cl, ok := unparen(el).(*ast.CompositeLit)
				if !ok {
					return false
				}
				rows = append(rows, row{f, e, cl})
			}
			return true
		case *ast.CallExpr:
			callee := g.calleeOf(e, v)
			if callee == nil || depth >= 4 {
				return false
			}
			ce, ok, any := g.m.EnvOf(callee), true, false
			ast.Inspect(callee.Body, func(n ast.Node) bool {
				if ret, isR := n.(*ast.ReturnStmt); isR && ok {
					ok, any = len(ret.Results) == 1 && lits(callee.File, ce, ret.Results[0], depth+1), true
				}
				_, lit := n.(*ast.FuncLit)
				return !lit
			})
			return ok && any
		}
		return false
	}
	if !lits(fn.File, e, rng.X, 0) || len(rows) == 0 {
		return nil
	}
	for _, rw := range rows {
		ix := fieldExpr(rw.cl, et, impl.Sel.Name)
		if ix == nil {
			return nil
		}
		c := *r
		c.impl, c.e = ix, rw.e
		c.via = fmt.Sprintf("%s via %s:%d", r.via, rw.f.Rel, g.line(rw.cl.Pos()))
		if r.svc {
			dx := fieldExpr(rw.cl, et, desc.Sel.Name)
			if dx == nil {
				return nil
			}
			if c.desc = g.descOf(rw.f, rw.e, dx); c.desc == nil {
				return nil
			}
		}
		rs = append(rs, &c)
	}
	return rs
}
