// goextract: error parameters passed through helpers, sentinels and constructor chains (node GA2, 2026-10-30).

package main

import (
	"go/ast"
	"go/token"
	"slices"
)

// sentSet (GA2) is what a bound error parameter can be: one of the package error variables in list, nil,
// or (other) an error from outside the module, which is none of them.
type sentSet struct {
	list  []ast.Expr
	other bool
}

// sentinelsOf (GA2) answers the sentSet of x; ok is false when x can be an error that is neither.
func (g *gx) sentinelsOf(b *Func, e *Env, x ast.Expr, at token.Pos, depth int, run *grpcRun) (out sentSet, ok bool) {
	if depth > 6 {
		return out, false
	}
	switch v := unparen(x).(type) {
	case *ast.Ident:
		if v.Name == "nil" {
			return out, true
		}
		src, tuple, found := g.lastAssign(b, v.Name, at)
		_, local := e.lookup(v.Name)
		switch {
		case found && tuple:
			if c, isCall := unparen(src).(*ast.CallExpr); isCall {
				return g.sentinelsOf(b, e, c, c.Pos(), depth, run)
			}
			return out, false
		case found:
			return g.sentinelsOf(b, e, src, src.Pos(), depth+1, run)
		case local:
			ss, bound := run.esent[v.Name]
			return ss, bound && b == run.efn && paramAt(b, v) >= 0
		}
	case *ast.CallExpr:
		if callee := g.m.Callee(e, v); callee != nil {
			if !callee.ErrorResult() {
				return out, false
			}
			inner := &grpcRun{esent: map[string]sentSet{}, efn: callee} // the error arguments this call passes
			for i, p := range callee.Params() {
				if id, isID := p.typ.(*ast.Ident); isID && id.Name == "error" && i < len(v.Args) {
					if ss, sok := g.sentinelsOf(b, e, v.Args[i], v.Pos(), depth+1, run); sok {
						inner.esent[p.name] = ss
					}
				}
			}
			ce, nres, ok := g.m.EnvOf(callee), len(callee.Results()), true
			ast.Inspect(callee.Body, func(n ast.Node) bool {
				r, isR := n.(*ast.ReturnStmt)
				switch {
				case !isR || !ok:
				case len(r.Results) == nres || len(r.Results) == 1:
					x := r.Results[len(r.Results)-1] // `return f()`: the call's last result
					ss, sok := g.sentinelsOf(callee, ce, x, r.Pos(), depth+1, inner)
					out.list, out.other, ok = append(out.list, ss.list...), out.other || ss.other, sok
				default:
					ok = false
				}
				_, lit := n.(*ast.FuncLit)
				return !lit
			})
			return out, ok
		}
		// outside the module: never a module sentinel, unless it wraps one (fmt.Errorf %w, errors.Join)
		if path, nm, isPkg := pkgCall(b.File, e, v); isPkg {
			wraps := path == "fmt" && nm == "Errorf" || path == "errors" && nm != "New"
			return sentSet{other: true}, g.m.ByImport[path] == nil && !wraps
		}
		if sel, isSel := unparen(v.Fun).(*ast.SelectorExpr); isSel && len(g.m.methods[sel.Sel.Name]) == 0 {
			return sentSet{other: true}, true // a method no module type has
		}
		return out, false
	}
	if vi := g.pkgVar(b.File, e, x); vi.value != nil {
		return sentSet{list: []ast.Expr{vi.value}}, true
	}
	return out, false
}

// deadBranches (GA2) answers the branches of fn an `errors.Is(p, S)` test never enters when the bound
// parameter p can only be some sentinels or errors from outside the module: an if body or case whose S is
// none of them, and the default of a tagless switch whose cases test every one of them and nothing else.
func (g *gx) deadBranches(fn *Func, esent map[string]sentSet) map[ast.Node]bool {
	e, dead := g.m.EnvOf(fn), map[ast.Node]bool{}
	test := func(x ast.Expr) (p string, s ast.Expr, ok bool) { // `errors.Is(p, S)` over a bound p
		c, isCall := unparen(x).(*ast.CallExpr)
		if !isCall || len(c.Args) != 2 {
			return "", nil, false
		}
		if path, nm, isPkg := pkgCall(fn.File, e, c); !isPkg || path != "errors" || nm != "Is" {
			return "", nil, false
		}
		if _, bound := esent[g.src(c.Args[0])]; !bound {
			return "", nil, false
		}
		if _, _, set := g.lastAssign(fn, g.src(c.Args[0]), x.Pos()); set { // reassigned: no longer what the call passed
			return "", nil, false
		}
		sv := g.pkgVar(fn.File, e, c.Args[1]).value
		return g.src(c.Args[0]), sv, sv != nil // a sentinel outside the module (sql.ErrNoRows): not read
	}
	in := func(p string, s ast.Expr) bool { return slices.Contains(esent[p].list, s) }
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		switch v := n.(type) {
		case *ast.FuncLit:
			return false
		case *ast.IfStmt:
			if p, s, ok := test(v.Cond); ok && !in(p, s) {
				dead[v.Body] = true
			}
		case *ast.SwitchStmt:
			if v.Tag != nil {
				return true
			}
			covered, param := map[ast.Expr]bool{}, ""
			var def ast.Node
			for _, st := range v.Body.List {
				cc := st.(*ast.CaseClause)
				if cc.List == nil {
					def = cc
				}
				live := false
				for _, x := range cc.List {
					p, s, ok := test(x)
					switch {
					case !ok:
						live = true
					case param != "" && p != param:
						return true
					default:
						param, live = p, live || in(p, s)
						covered[s] = covered[s] || in(p, s)
					}
				}
				if !live && cc.List != nil {
					dead[cc] = true
				}
			}
			all := param != "" && !esent[param].other
			for _, s := range esent[param].list {
				all = all && covered[s]
			}
			if def != nil && all {
				dead[def] = true
			}
		}
		return true
	})
	return dead
}

// passOn (GA2) answers what a bound error parameter returns at at: the caller's errors; under
// `errors.As(p, ..)` or `status.FromError(p)` (and for the errors.As target, at == NoPos) only the status
// errors, as a plain error satisfies neither.
func (g *gx) passOn(b *Func, p string, at token.Pos, bound *errSrc) *errSrc {
	guarded := at == token.NoPos
	ast.Inspect(b.Body, func(n ast.Node) bool {
		if s, ok := n.(*ast.IfStmt); ok && at >= s.Body.Pos() && at < s.Body.End() {
			ast.Inspect(s.Cond, func(m ast.Node) bool {
				if c, ok := m.(*ast.CallExpr); ok && len(c.Args) > 0 && g.src(c.Args[0]) == p {
					if path, nm, ok := pkgCall(b.File, g.m.EnvOf(b), c); ok && (path == "errors" && nm == "As" || path == grpcStatus && nm == "FromError") {
						guarded = true
					}
				}
				return true
			})
		}
		_, lit := n.(*ast.FuncLit)
		return !lit
	})
	out := &errSrc{unread: bound.unread}
	for _, c := range bound.codes {
		if !guarded || c.Kind != "plain_error" {
			out.addCode(c)
		}
	}
	return out
}

// asTarget (GA2): when x is `t.GRPCStatus()` and t the target of `errors.As(p, &t)` in b, p a bound error
// parameter, the parameter and its errors.
func (g *gx) asTarget(b *Func, x ast.Expr, run *grpcRun) (string, *errSrc) {
	c, ok := unparen(x).(*ast.CallExpr)
	if !ok || b != run.efn || run.ebind == nil {
		return "", nil
	}
	sel, ok := unparen(c.Fun).(*ast.SelectorExpr)
	if !ok || sel.Sel.Name != "GRPCStatus" || len(c.Args) != 0 {
		return "", nil
	}
	t, p := g.src(sel.X), ""
	ast.Inspect(b.Body, func(n ast.Node) bool {
		if as, ok := n.(*ast.CallExpr); ok && len(as.Args) == 2 && g.src(as.Args[1]) == "&"+t {
			if path, nm, ok := pkgCall(b.File, g.m.EnvOf(b), as); ok && path == "errors" && nm == "As" {
				p = g.src(as.Args[0])
			}
		}
		return true
	})
	if run.ebind[p] == nil {
		return "", nil
	}
	return p, run.ebind[p]
}

// argv is an argument of the outermost constructor call, read in the caller's file and environment.
type argv struct {
	f *File
	e *Env
	x ast.Expr
}

// siteValue answers the expression field gets in the value x builds, the function (and environment) it is
// written in, and the arguments of the constructor chain that reached it; v is nil when not read.
func (g *gx) siteValue(b *Func, e *Env, x ast.Expr, ti *TypeInfo, field string) (*Func, *Env, ast.Expr, map[string]argv) {
	strip := func(x ast.Expr) ast.Expr {
		x = unparen(x)
		if u, ok := x.(*ast.UnaryExpr); ok && u.Op == token.AND {
			x = unparen(u.X)
		}
		return x
	}
	cb, ce, args, set := b, e, map[string]argv{}, ast.Expr(nil)
	x = strip(x)
	for depth := 0; depth < 4; depth++ {
		if id, ok := x.(*ast.Ident); ok && cb != b {
			if set = g.fieldSet(cb, id.Name, field); set != nil {
				break
			}
			if vals := ce.valuesOf(id.Name); len(vals) == 1 && !multiAssigned(ce, id.Name) {
				x = strip(vals[0])
			}
		}
		c, ok := x.(*ast.CallExpr)
		if !ok {
			break
		}
		ctor := g.calleeOf(ce, c)
		if ctor == nil {
			return nil, nil, nil, nil
		}
		next := map[string]argv{}
		for i, p := range ctor.Params() {
			if i < len(c.Args) {
				a := argv{cb.File, ce, c.Args[i]}
				if id, ok := unparen(c.Args[i]).(*ast.Ident); ok && cb != b && paramAt(cb, id) >= 0 && args[id.Name].x != nil {
					a = args[id.Name]
				}
				next[p.name] = a
			}
		}
		x, cb, ce, args = nil, ctor, g.m.EnvOf(ctor), next
		ast.Inspect(ctor.Body, func(nd ast.Node) bool {
			if r, ok := nd.(*ast.ReturnStmt); ok && len(r.Results) == 1 && x == nil {
				x = strip(r.Results[0])
			}
			_, lit := nd.(*ast.FuncLit)
			return x == nil && !lit
		})
		if x == nil {
			return nil, nil, nil, nil
		}
	}
	v := set
	if v == nil {
		cl, ok := x.(*ast.CompositeLit)
		if !ok {
			return nil, nil, nil, nil
		}
		v = fieldExpr(cl, ti, field)
	}
	return cb, ce, v, args
}

// siteFieldV5 is siteField as round 5 read it (one constructor level, a literal or a parameter), for the auth readers.
func (g *gx) siteFieldV5(b *Func, e *Env, x ast.Expr, ti *TypeInfo, field string, run *grpcRun) (int, bool) {
	x = unparen(x)
	if u, ok := x.(*ast.UnaryExpr); ok && u.Op == token.AND {
		x = unparen(u.X)
	}
	cb, ce, args := b, e, map[string]ast.Expr{}
	if c, ok := x.(*ast.CallExpr); ok { // a constructor
		ctor := g.calleeOf(e, c)
		if ctor == nil {
			return 0, false
		}
		x, cb, ce = nil, ctor, g.m.EnvOf(ctor)
		for i, p := range ctor.Params() {
			if i < len(c.Args) {
				args[p.name] = c.Args[i]
			}
		}
		ast.Inspect(ctor.Body, func(nd ast.Node) bool {
			if r, ok := nd.(*ast.ReturnStmt); ok && len(r.Results) == 1 && x == nil {
				x = unparen(r.Results[0])
				if u, ok := x.(*ast.UnaryExpr); ok && u.Op == token.AND {
					x = unparen(u.X)
				}
			}
			return x == nil
		})
	}
	cl, ok := x.(*ast.CompositeLit)
	if !ok {
		return 0, false
	}
	v := fieldExpr(cl, ti, field)
	if v == nil {
		return 0, false
	}
	if n, ok := g.codeOf(cb.File, ce, v, 0); ok {
		return n, true
	}
	if id, ok := unparen(v).(*ast.Ident); ok && args[id.Name] != nil {
		return g.codeArg(b.File, e, args[id.Name], run)
	}
	return 0, false
}

// fieldSet answers the value a constructor assigns to `name.field` after building name; nil when none
// (a BadExpr, never read to a code, when several).
func (g *gx) fieldSet(cb *Func, name, field string) (out ast.Expr) {
	ast.Inspect(cb.Body, func(nd ast.Node) bool {
		if as, ok := nd.(*ast.AssignStmt); ok && len(as.Lhs) == len(as.Rhs) {
			for i, l := range as.Lhs {
				if s, ok := l.(*ast.SelectorExpr); ok && s.Sel.Name == field && g.src(s.X) == name {
					if out != nil {
						out = &ast.BadExpr{}
					} else {
						out = as.Rhs[i]
					}
				}
			}
		}
		_, lit := nd.(*ast.FuncLit)
		return !lit
	})
	return
}

// ctorCode reads the code expression v of constructor cb: a literal, a parameter the call binds, or a
// lookup `m[k]` (directly or through a local `v, ok := m[k]` whose fallback is set under `!ok`) in a
// package map literal with string keys, the key a constant or a parameter the call binds to one.
func (g *gx) ctorCode(b, cb *Func, ce *Env, v ast.Expr, args map[string]argv, run *grpcRun) (int, bool) {
	if n, ok := g.codeOf(cb.File, ce, v, 0); ok {
		return n, true
	}
	arg := func(x ast.Expr) (argv, bool) {
		id, ok := unparen(x).(*ast.Ident)
		if !ok || cb == b || paramAt(cb, id) < 0 {
			return argv{}, false
		}
		a := args[id.Name]
		return a, a.x != nil
	}
	if a, ok := arg(v); ok {
		return g.codeArg(a.f, a.e, a.x, run)
	}
	ix, _ := unparen(v).(*ast.IndexExpr)
	var fallback []ast.Expr
	if id, ok := unparen(v).(*ast.Ident); ok {
		ix, fallback = g.commaOk(cb, id.Name)
	}
	if ix == nil {
		return 0, false
	}
	var k string
	var ok bool
	if a, isArg := arg(ix.Index); isArg {
		k, ok = g.m.constStringDepth(a.f, a.e, a.x, 3)
	} else {
		k, ok = g.m.constStringDepth(cb.File, ce, ix.Index, 3)
	}
	vi := g.pkgVar(cb.File, ce, ix.X)
	lit, _ := unparen(vi.value).(*ast.CompositeLit)
	if !ok || lit == nil {
		return 0, false
	}
	for _, el := range lit.Elts {
		kv, isKV := el.(*ast.KeyValueExpr)
		if !isKV {
			return 0, false
		}
		if s, isStr := g.m.constStringDepth(vi.file, nil, kv.Key, 3); isStr && s == k {
			return g.codeOf(vi.file, nil, kv.Value, 0)
		}
	}
	if len(fallback) == 1 { // the key is not in the map: the value set under `!ok`
		return g.codeOf(cb.File, ce, fallback[0], 0)
	}
	return 0, false
}

// commaOk answers the lookup of a local `name, ok := m[k]` and the values name is given under `if !ok`;
// nil when name has another assignment.
func (g *gx) commaOk(fn *Func, name string) (ix *ast.IndexExpr, fallback []ast.Expr) {
	okName, bad := "", false
	var guarded []*ast.BlockStmt
	ast.Inspect(fn.Body, func(nd ast.Node) bool {
		as, isAs := nd.(*ast.AssignStmt)
		if isAs && len(as.Lhs) == 2 && len(as.Rhs) == 1 && g.src(as.Lhs[0]) == name {
			ix, _ = unparen(as.Rhs[0]).(*ast.IndexExpr)
			okName = g.src(as.Lhs[1])
		}
		_, lit := nd.(*ast.FuncLit)
		return !lit
	})
	if ix == nil || okName == "_" {
		return nil, nil
	}
	ast.Inspect(fn.Body, func(nd ast.Node) bool {
		if s, ok := nd.(*ast.IfStmt); ok && g.src(s.Cond) == "!"+okName {
			guarded = append(guarded, s.Body)
		}
		if as, ok := nd.(*ast.AssignStmt); ok && len(as.Lhs) == len(as.Rhs) {
			for i, l := range as.Lhs {
				if g.src(l) != name {
					continue
				}
				in := false
				for _, gb := range guarded {
					in = in || as.Pos() >= gb.Pos() && as.End() <= gb.End()
				}
				if in {
					fallback = append(fallback, as.Rhs[i])
				} else {
					bad = true
				}
			}
		}
		_, lit := nd.(*ast.FuncLit)
		return !lit
	})
	if bad {
		return nil, nil
	}
	return ix, fallback
}

// multiAssigned: a local some multi-value assignment (`v, ok := m[k]`, `c, err := f()`) or a range sets,
// which Env records no value for: never read as single-valued.
func multiAssigned(e *Env, name string) bool {
	for x := e; x != nil; x = x.parent {
		if _, ok := x.vars[name]; !ok {
			continue
		}
		found := false
		ast.Inspect(x.fn.Body, func(n ast.Node) bool {
			is := func(l ast.Expr) bool { id, ok := l.(*ast.Ident); return ok && id.Name == name }
			switch s := n.(type) {
			case *ast.FuncLit:
				return false
			case *ast.AssignStmt:
				for _, l := range s.Lhs {
					found = found || len(s.Lhs) != len(s.Rhs) && is(l)
				}
			case *ast.RangeStmt:
				found = found || s.Key != nil && is(s.Key) || s.Value != nil && is(s.Value)
			case *ast.ValueSpec:
				for _, n := range s.Names {
					found = found || len(s.Values) > 0 && len(s.Values) != len(s.Names) && n.Name == name
				}
			}
			return !found
		})
		return found
	}
	return false
}
