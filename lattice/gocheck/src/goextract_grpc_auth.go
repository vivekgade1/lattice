// goextract: grpc-go servers, interceptor classification, allowlists, builder chains and method sets (Spike I, D2; split from goextract_grpc.go, a pure move).

package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"sort"
	"strings"
)

// isStub reports whether a method comes from a generated Unimplemented*Server.
func isStub(fn *Func) bool {
	return fn != nil && (strings.HasPrefix(fn.Recv, "Unimplemented") || strings.HasSuffix(fn.File.Rel, "_grpc.pb.go"))
}

// ---- interceptors ----------------------------------------------------------------------

// interceptorFuncs resolves the unary interceptors of every grpc.NewServer call.
func (g *gx) interceptorFuncs(w *grpcWalk) []xInterceptor {
	var out []xInterceptor
	seen := map[*Func]bool{}
	for _, fn := range w.funcs() {
		if !w.isWired(fn) {
			continue
		}
		e := g.m.EnvOf(fn)
		f := fn.File
		stmtWalk(fn.Body, func(call *ast.CallExpr, _ []ast.Node) {
			for _, oc := range []*ast.CallExpr{call} { // a server option call, wherever it sits
				opath, oname, ok := pkgCall(f, e, oc)
				if !ok || opath != grpcPath {
					continue
				}
				if oname != "UnaryInterceptor" && oname != "ChainUnaryInterceptor" {
					continue
				}
				for _, v := range g.expandIC(w, e, oc.Args, 0) {
					a := v.x
					ic := g.resolveInterceptor(v.e, a)
					if ic == nil {
						out = append(out, xInterceptor{Func: g.src(a), File: v.e.fn.File.Rel, Line: g.line(a.Pos()),
							Kind: "auth_not_literal", Reason: "the interceptor `" + g.src(a) + "` is not a function the extractor can resolve",
							Chained: oname == "ChainUnaryInterceptor"})
						continue
					}
					if seen[ic] {
						continue
					}
					seen[ic] = true
					x := g.classifyInterceptor(ic)
					x.Chained = oname == "ChainUnaryInterceptor"
					out = append(out, x)
				}
			}
		})
	}
	return out
}

// expandIC (D2.2): an argument reading a field of a module struct stands for the values that reach it.
func (g *gx) expandIC(w *grpcWalk, e *Env, args []ast.Expr, depth int) (out []ee) {
	for _, a := range args {
		n := len(out)
		if s, ok := unparen(a).(*ast.SelectorExpr); ok && depth < 4 {
			if ti := g.m.Infer(e, s.X).deref().rowTypeAny(); ti != nil && g.m.FieldOf(ti, s.Sel.Name, 0) != nil {
				for _, v := range w.fieldFlows(ti, s.Sel.Name) {
					out = append(out, g.expandIC(w, v.e, []ast.Expr{v.x}, depth+1)...)
				}
			}
		}
		if len(out) == n {
			out = append(out, ee{e, a})
		}
	}
	return
}

// resolveInterceptor answers the function an interceptor expression is: a function, a
// literal, or the literal a constructor returns.
func (g *gx) resolveInterceptor(e *Env, x ast.Expr) *Func {
	switch v := unparen(x).(type) {
	case *ast.FuncLit:
		return g.m.LitFunc(v, e.fn)
	case *ast.Ident, *ast.SelectorExpr:
		return g.funcRef(e, v, 0)
	case *ast.CallExpr:
		callee := g.m.Callee(e, v)
		if callee == nil {
			return nil
		}
		var found *Func
		ast.Inspect(callee.Body, func(n ast.Node) bool {
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			if ret, ok := n.(*ast.ReturnStmt); ok && len(ret.Results) == 1 {
				if lit, ok := unparen(ret.Results[0]).(*ast.FuncLit); ok && found == nil {
					found = g.m.LitFunc(lit, callee)
				}
			}
			return true
		})
		return found
	}
	return nil
}

// classifyInterceptor reads one unary interceptor: does it refuse unauthenticated callers,
// and which methods does it let through (a literal set, a map or a switch).
func (g *gx) classifyInterceptor(fn *Func) xInterceptor {
	x := xInterceptor{Func: qual(fn), File: fn.File.Rel, Line: g.line(fn.Pos), Kind: "other"}
	run := &grpcRun{memo: map[*Func]*errSrc{}, busy: map[*Func]bool{}}
	es := g.errOfFunc(fn, 0, run)
	if !es.has("UNAUTHENTICATED") {
		if why := g.untracedReturn(fn, es, run); why != "" {
			x.Kind, x.Reason = "auth_unknown", why
		}
		return x
	}
	ps := fn.Params()
	infoName, handlerName := "", ""
	if len(ps) >= 4 {
		infoName, handlerName = ps[2].name, ps[3].name
	}
	literal := true
	why := ""
	public, prefix := map[string]bool{}, map[string]bool{}
	e := g.m.EnvOf(fn)
	passes := func(body ast.Node) bool {
		ok := false
		ast.Inspect(body, func(n ast.Node) bool {
			if _, isLit := n.(*ast.FuncLit); isLit {
				return false
			}
			if ret, isRet := n.(*ast.ReturnStmt); isRet && len(ret.Results) >= 1 {
				if c, isCall := unparen(ret.Results[0]).(*ast.CallExpr); isCall {
					if id, isID := unparen(c.Fun).(*ast.Ident); isID && id.Name == handlerName {
						ok = true
					}
				}
			}
			return true
		})
		return ok
	}
	mentionsMethod := func(n ast.Node) bool {
		found := false
		ast.Inspect(n, func(m ast.Node) bool {
			if sel, ok := m.(*ast.SelectorExpr); ok && sel.Sel.Name == "FullMethod" {
				if id, ok := sel.X.(*ast.Ident); ok && id.Name == infoName {
					found = true
				}
			}
			return !found
		})
		return found
	}
	for _, b := range g.bodies(fn) {
		be := g.m.EnvOf(b)
		ast.Inspect(b.Body, func(n ast.Node) bool {
			switch s := n.(type) {
			case *ast.IfStmt:
				if !mentionsMethod(s.Cond) && (s.Init == nil || !mentionsMethod(s.Init)) {
					return true
				}
				set, ok := g.methodSet(b.File, be, s, infoName)
				var pre []string
				if !passes(s.Body) {
					// a condition on the method that does not pass the call through
					// (logging, metrics): not an allowlist
					return true
				}
				if !ok {
					set, pre, ok = g.builderSet(be, s.Cond, infoName)
				}
				if !ok {
					literal, why = false, "the method test `"+g.src(s.Cond)+"` is not a literal set"
					return true
				}
				for _, m := range set {
					public[m] = true
				}
				for _, m := range pre {
					prefix[m] = true
				}
			case *ast.SwitchStmt:
				if s.Tag == nil || !mentionsMethod(s.Tag) {
					return true
				}
				for _, cc := range s.Body.List {
					clause, ok := cc.(*ast.CaseClause)
					if !ok || !passes(&ast.BlockStmt{List: clause.Body}) {
						continue
					}
					if len(clause.List) == 0 {
						literal, why = false, "a default clause of the method switch passes the call through"
						continue
					}
					for _, c := range clause.List {
						if str, ok := g.strOf(b.File, be, c); ok {
							public[str] = true
						} else {
							literal, why = false, "the case `"+g.src(c)+"` of the method switch is not a constant"
						}
					}
				}
			}
			return true
		})
	}
	_ = e
	for m := range public {
		x.Public = append(x.Public, m)
	}
	sort.Strings(x.Public)
	for m := range prefix {
		x.Prefix = append(x.Prefix, m)
	}
	sort.Strings(x.Prefix)
	if literal {
		x.Kind = "auth_literal"
	} else {
		x.Kind, x.Reason = "auth_not_literal", why
	}
	return x
}

const healthPath = "google.golang.org/grpc/health/grpc_health_v1"

var healthStr = map[string]string{"Health_Check_FullMethodName": "/grpc.health.v1.Health/Check",
	"Health_Watch_FullMethodName": "/grpc.health.v1.Health/Watch", "Health_ServiceDesc": "grpc.health.v1.Health"}

// strOf (D2.3) is constStringDepth that also reads `<pkg>.<Svc>_ServiceDesc.ServiceName` and the
// standard health constants.
func (g *gx) strOf(f *File, e *Env, x ast.Expr) (string, bool) {
	if s, ok := g.m.constStringDepth(f, e, x, 3); ok {
		return s, true
	}
	v, ok := unparen(x).(*ast.SelectorExpr)
	if !ok {
		return "", false
	}
	if in, isSel := v.X.(*ast.SelectorExpr); isSel && v.Sel.Name == "ServiceName" {
		if d := g.descOf(f, e, in); d != nil {
			return d.name, true
		}
		v = in
	}
	if p, ok := impPath(f, e, v.X); ok && p == healthPath {
		s, ok := healthStr[v.Sel.Name]
		return s, ok
	}
	return "", false
}

// builderSet (D2.4) reads `<x>.<field>.<test>(info.FullMethod)`: the names and prefixes the builder
// chains reaching the field add.
func (g *gx) builderSet(e *Env, cond ast.Expr, info string) (ex, pre []string, ok bool) {
	c, _ := unparen(cond).(*ast.CallExpr)
	if c == nil || len(c.Args) != 1 || g.src(c.Args[0]) != info+".FullMethod" {
		return
	}
	m, _ := unparen(c.Fun).(*ast.SelectorExpr)
	if m == nil {
		return
	}
	fs, _ := unparen(m.X).(*ast.SelectorExpr)
	if fs == nil || g.m.Infer(e, fs.X).deref().rowTypeAny() == nil {
		return
	}
	flows := curWalk.fieldFlows(g.m.Infer(e, fs.X).deref().rowTypeAny(), fs.Sel.Name)
	for _, v := range flows {
		if !g.chain(v.e, v.x, &ex, &pre, 0) {
			return nil, nil, false
		}
	}
	return ex, pre, len(flows) > 0
}

// chain reads a builder chain `build(a).Add(b)`: each link adds its string arguments to a field of
// the value (an append, a map set); a field some range tests with strings.HasPrefix holds prefixes.
func (g *gx) chain(e *Env, x ast.Expr, ex, pre *[]string, depth int) bool {
	c, _ := unparen(x).(*ast.CallExpr)
	var fn *Func
	if c != nil && depth < 8 {
		fn = g.calleeOf(e, c)
	}
	if fn == nil {
		return false
	}
	args, good := map[string][]ast.Expr{}, true
	for i, p := range fn.Params() {
		if _, v := p.typ.(*ast.Ellipsis); v && i < len(c.Args) {
			args[p.name] = c.Args[i:]
		} else if !v && i < len(c.Args) {
			args[p.name] = c.Args[i : i+1]
		}
	}
	var val func(v ast.Expr) []string
	val = func(v ast.Expr) (out []string) {
		switch t := unparen(v).(type) {
		case *ast.Ident:
			if as, ok := args[t.Name]; ok {
				for _, a := range as {
					s, ok := g.strOf(e.fn.File, e, a)
					good = good && ok
					out = append(out, s)
				}
				return
			}
		case *ast.BinaryExpr:
			good = good && t.Op == token.ADD
			for _, a := range val(t.X) {
				for _, b := range val(t.Y) {
					out = append(out, a+b)
				}
			}
			return
		}
		s, ok := g.strOf(fn.File, nil, v)
		good = good && ok
		return []string{s}
	}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		var field string
		var vals []ast.Expr
		switch s := n.(type) {
		case *ast.RangeStmt:
			if v, ok := s.Value.(*ast.Ident); ok {
				args[v.Name] = args[g.src(s.X)]
			}
		case *ast.AssignStmt:
			if len(s.Lhs) != 1 || len(s.Rhs) != 1 {
				break
			}
			switch l := s.Lhs[0].(type) {
			case *ast.SelectorExpr: // X.F = append(X.F, v...)
				if r, ok := s.Rhs[0].(*ast.CallExpr); ok && len(r.Args) > 1 && g.src(r.Fun) == "append" {
					field, vals = l.Sel.Name, r.Args[1:]
				}
			case *ast.IndexExpr: // X.F[v] = ...
				if f, ok := l.X.(*ast.SelectorExpr); ok {
					field, vals = f.Sel.Name, []ast.Expr{l.Index}
				}
			}
		}
		for _, v := range vals {
			if g.prefixField(field) {
				*pre = append(*pre, val(v)...)
			} else {
				*ex = append(*ex, val(v)...)
			}
		}
		return true
	})
	if fn.isMethod { // the earlier links
		sel, ok := unparen(c.Fun).(*ast.SelectorExpr)
		return good && ok && g.chain(e, sel.X, ex, pre, depth+1)
	}
	return good
}

// prefixField: some range over a field of that name tests its elements with strings.HasPrefix.
func (g *gx) prefixField(field string) (found bool) {
	for _, fn := range g.m.AllFuncs() {
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if r, ok := n.(*ast.RangeStmt); ok && strings.HasSuffix(g.src(r.X), "."+field) {
				ast.Inspect(r.Body, func(m ast.Node) bool {
					if c, ok := m.(*ast.CallExpr); ok {
						found = found || strings.HasSuffix(g.src(c.Fun), "strings.HasPrefix")
					}
					return true
				})
			}
			return true
		})
	}
	return
}

// methodSet reads the methods an `if` test names: `set[info.FullMethod]`, `_, ok :=
// set[info.FullMethod]; ok`, or `info.FullMethod == "a" || info.FullMethod == "b"`.
func (g *gx) methodSet(f *File, e *Env, s *ast.IfStmt, infoName string) ([]string, bool) {
	isMethod := func(x ast.Expr) bool {
		sel, ok := unparen(x).(*ast.SelectorExpr)
		if !ok || sel.Sel.Name != "FullMethod" {
			return false
		}
		id, ok := sel.X.(*ast.Ident)
		return ok && id.Name == infoName
	}
	var fromMap func(x ast.Expr) ([]string, bool)
	fromMap = func(x ast.Expr) ([]string, bool) {
		idx, ok := unparen(x).(*ast.IndexExpr)
		if !ok || !isMethod(idx.Index) {
			return nil, false
		}
		return g.mapKeys(f, e, idx.X)
	}
	// `if _, ok := set[info.FullMethod]; ok`
	if as, ok := s.Init.(*ast.AssignStmt); ok && len(as.Rhs) == 1 {
		if keys, ok := fromMap(as.Rhs[0]); ok {
			if id, ok := unparen(s.Cond).(*ast.Ident); ok && len(as.Lhs) == 2 {
				if lid, ok := as.Lhs[1].(*ast.Ident); ok && lid.Name == id.Name {
					return keys, true
				}
			}
		}
		return nil, false
	}
	if keys, ok := fromMap(s.Cond); ok {
		return keys, true
	}
	var eq func(x ast.Expr) ([]string, bool)
	eq = func(x ast.Expr) ([]string, bool) {
		be, ok := unparen(x).(*ast.BinaryExpr)
		if !ok {
			return nil, false
		}
		switch be.Op {
		case token.LOR:
			a, ok1 := eq(be.X)
			b, ok2 := eq(be.Y)
			return append(a, b...), ok1 && ok2
		case token.EQL:
			l, r := be.X, be.Y
			if isMethod(r) {
				l, r = r, l
			}
			if isMethod(l) {
				if str, ok := g.strOf(f, e, r); ok {
					return []string{str}, true
				}
			}
		}
		return nil, false
	}
	return eq(s.Cond)
}

// mapKeys answers the keys (with a true value) of a map variable's literal.
func (g *gx) mapKeys(f *File, e *Env, x ast.Expr) ([]string, bool) {
	x = unparen(x)
	var lit *ast.CompositeLit
	var lf = f
	switch v := x.(type) {
	case *ast.CompositeLit:
		lit = v
	case *ast.Ident:
		if e != nil {
			if _, local := e.lookup(v.Name); local {
				if vals := e.valuesOf(v.Name); len(vals) == 1 {
					if cl, ok := unparen(vals[0]).(*ast.CompositeLit); ok {
						lit = cl
					}
				}
				break
			}
		}
		if vi, ok := f.Pkg.Vars[v.Name]; ok {
			if cl, ok := unparen(vi.value).(*ast.CompositeLit); ok {
				lit, lf = cl, vi.file
			}
		}
	case *ast.SelectorExpr:
		if path, ok := impPath(f, e, v.X); ok {
			if pkg := g.m.ByImport[path]; pkg != nil {
				if vi, ok := pkg.Vars[v.Sel.Name]; ok {
					if cl, ok := unparen(vi.value).(*ast.CompositeLit); ok {
						lit, lf = cl, vi.file
					}
				}
			}
		}
	}
	if lit == nil {
		return nil, false
	}
	if _, isMap := lit.Type.(*ast.MapType); !isMap {
		return nil, false
	}
	var keys []string
	for _, el := range lit.Elts {
		kv, ok := el.(*ast.KeyValueExpr)
		if !ok {
			return nil, false
		}
		k, ok := g.strOf(lf, nil, kv.Key)
		if !ok {
			return nil, false
		}
		if id, ok := kv.Value.(*ast.Ident); ok && id.Name == "false" {
			continue
		}
		keys = append(keys, k)
	}
	return keys, true
}

// ---- caller ----------------------------------------------------------------------------

// seedCallerFuncs finds the functions that read the authenticated caller back from the
// context (`ctx.Value(key).(*User)`) after an interceptor stored it
// (`context.WithValue(ctx, key, &user)`), so the owner machinery recognises `u.ID`.
func (g *gx) seedCallerFuncs(ics []xInterceptor) {
	keys := map[string]bool{}
	for _, ic := range ics {
		if ic.Kind == "other" {
			continue
		}
		for _, fn := range g.m.AllFuncs() {
			if fn.File.Rel != ic.File {
				continue
			}
			e := g.m.EnvOf(fn)
			stmtWalk(fn.Body, func(call *ast.CallExpr, _ []ast.Node) {
				if path, name, ok := pkgCall(fn.File, e, call); ok && path == "context" && name == "WithValue" && len(call.Args) == 3 {
					keys[g.src(call.Args[1])] = true
				}
			})
		}
	}
	if len(keys) == 0 {
		return
	}
	// ctx.Value(key).(*T) in a function that returns it
	for _, fn := range g.m.AllFuncs() {
		if fn.Lit != nil || len(fn.Results()) == 0 {
			continue
		}
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			ta, ok := n.(*ast.TypeAssertExpr)
			if !ok || ta.Type == nil {
				return true
			}
			c, ok := unparen(ta.X).(*ast.CallExpr)
			if !ok || len(c.Args) != 1 {
				return true
			}
			sel, ok := c.Fun.(*ast.SelectorExpr)
			if !ok || sel.Sel.Name != "Value" || !keys[g.src(c.Args[0])] {
				return true
			}
			t := g.m.TypeExpr(fn.File, ta.Type)
			if t.deref().Kind == "named" {
				g.callerFuncs[fn] = "struct"
			} else if t.Kind == "basic" {
				g.callerFuncs[fn] = "scalar"
			}
			return true
		})
	}
	// a function returning a caller function's result as its first value
	for round := 0; round < 3; round++ {
		for _, fn := range g.m.AllFuncs() {
			if fn.Lit != nil || g.callerFuncs[fn] != "" || len(fn.Results()) == 0 {
				continue
			}
			e := g.m.EnvOf(fn)
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				ret, ok := n.(*ast.ReturnStmt)
				if !ok || len(ret.Results) == 0 {
					return true
				}
				first := unparen(ret.Results[0])
				if id, ok := first.(*ast.Ident); ok {
					if src, tuple, found := g.lastAssign(fn, id.Name, ret.Pos()); found {
						if !tuple {
							first = unparen(src)
						} else if c, ok := unparen(src).(*ast.CallExpr); ok {
							first = c
						}
					}
				}
				if c, ok := first.(*ast.CallExpr); ok {
					if callee := g.m.Callee(e, c); callee != nil && g.callerFuncs[callee] != "" {
						g.callerFuncs[fn] = g.callerFuncs[callee]
					}
				}
				return true
			})
		}
	}
}

// rpcAuth decides Public | Member | unclear for every implemented or unimplemented rpc.
func (g *gx) rpcAuth(gr *xGrpc, ics []xInterceptor, run *grpcRun) {
	var auth []xInterceptor
	for _, ic := range ics {
		if ic.Kind != "other" {
			auth = append(auth, ic)
		}
	}
	for _, svc := range gr.Services {
		for _, r := range svc.Rpcs {
			if r.State != "implemented" && r.State != "unimplemented" {
				continue
			}
			full := "/" + rpcKey(svc.Name, r.Name)
			helper := false
			if r.State == "implemented" && r.Handler != nil {
				for _, fn := range g.m.Funcs {
					if fn.File.Rel == r.Handler.File && qual(fn) == r.Handler.Func {
						ci := g.analyze(fn)
						var fns []*Func
						for f := range ci.funcs {
							if f != fn {
								fns = append(fns, f)
							}
						}
						sort.Slice(fns, func(i, j int) bool { return g.funcKey(fns[i]) < g.funcKey(fns[j]) })
						for _, f := range fns {
							if g.errOfFunc(f, 1, run).has("UNAUTHENTICATED") {
								helper = true
							}
						}
						break
					}
				}
			}
			var lits []xInterceptor
			for _, ic := range auth {
				if ic.Kind == "auth_literal" {
					lits = append(lits, ic)
				}
			}
			pub := false
			for _, m := range append(lits, xInterceptor{})[0].Public {
				pub = pub || m == full
			}
			for _, m := range append(lits, xInterceptor{})[0].Prefix {
				pub = pub || strings.HasPrefix(full, m)
			}
			switch {
			case len(lits) == 1 && (len(auth) == 1 || !pub):
				a := lits[0]
				if pub {
					r.Auth, r.AuthReason = "Public", fmt.Sprintf("%s lists %s as a method that skips authentication (%s:%d)", a.Func, full, a.File, a.Line)
				} else {
					r.Auth, r.AuthReason = "Member", fmt.Sprintf("%s refuses callers without a valid token and does not list %s (%s:%d)", a.Func, full, a.File, a.Line)
				}
			case len(auth) > 0:
				why := "several interceptors refuse unauthenticated callers"
				if len(auth) == 1 {
					why = auth[0].Reason
				}
				if helper {
					r.Auth, r.AuthReason = "Member", "the handler calls a helper that answers UNAUTHENTICATED"
				} else {
					r.Auth, r.AuthReason = "unclear", why
					g.addUnread(auth[0].File, auth[0].Line, "interceptor_not_literal", why)
					r.Unread = append(r.Unread, xUnread{File: auth[0].File, Line: auth[0].Line, What: "interceptor_not_literal", Reason: why})
				}
			case helper:
				r.Auth, r.AuthReason = "Member", "the handler calls a helper that answers UNAUTHENTICATED"
			default:
				r.Auth, r.AuthReason = "Public", "no interceptor refuses unauthenticated callers and no handler-level check was found"
			}
		}
	}
}
