// goextract: grpc-go servers, errors, translators, sentinels and code maps (Spike I, D2; split from goextract_grpc.go, a pure move).

package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"slices"
	"strings"
)

var codeConsts = map[string]int{
	"OK": 0, "Canceled": 1, "Unknown": 2, "InvalidArgument": 3, "DeadlineExceeded": 4,
	"NotFound": 5, "AlreadyExists": 6, "PermissionDenied": 7, "ResourceExhausted": 8,
	"FailedPrecondition": 9, "Aborted": 10, "OutOfRange": 11, "Unimplemented": 12,
	"Internal": 13, "Unavailable": 14, "DataLoss": 15, "Unauthenticated": 16,
}

var codeNameList = []string{
	"OK", "CANCELLED", "UNKNOWN", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED", "NOT_FOUND",
	"ALREADY_EXISTS", "PERMISSION_DENIED", "RESOURCE_EXHAUSTED", "FAILED_PRECONDITION",
	"ABORTED", "OUT_OF_RANGE", "UNIMPLEMENTED", "INTERNAL", "UNAVAILABLE", "DATA_LOSS",
	"UNAUTHENTICATED",
}

// impPath answers the import path an identifier names when it is a package of f (not
// shadowed by a local or a package-level name).
func impPath(f *File, e *Env, x ast.Expr) (string, bool) {
	id, ok := unparen(x).(*ast.Ident)
	if !ok {
		return "", false
	}
	if e != nil {
		if _, local := e.lookup(id.Name); local {
			return "", false
		}
	}
	if _, pkgLevel := f.Pkg.Vars[id.Name]; pkgLevel {
		return "", false
	}
	path, ok := f.Imports[id.Name]
	return path, ok
}

// pkgCall answers (import path, function name) of a call `pkg.Name(...)`.
func pkgCall(f *File, e *Env, c *ast.CallExpr) (string, string, bool) {
	sel, ok := unparen(c.Fun).(*ast.SelectorExpr)
	if !ok {
		return "", "", false
	}
	path, ok := impPath(f, e, sel.X)
	if !ok {
		return "", "", false
	}
	return path, sel.Sel.Name, true
}

// ---- codes -----------------------------------------------------------------------------

// codeOf answers the code a `codes.X` expression names; ok is false when it is not a
// literal (a variable, a map lookup, a function result).
func (g *gx) codeOf(f *File, e *Env, x ast.Expr, depth int) (int, bool) {
	if depth > 4 {
		return 0, false
	}
	switch v := unparen(x).(type) {
	case *ast.SelectorExpr:
		if path, ok := impPath(f, e, v.X); ok {
			if path == grpcCodes {
				n, ok := codeConsts[v.Sel.Name]
				return n, ok
			}
			if pkg := g.m.ByImport[path]; pkg != nil {
				if ci, ok := pkg.Consts[v.Sel.Name]; ok && ci.value != nil {
					return g.codeOf(ci.file, nil, ci.value, depth+1)
				}
			}
		}
	case *ast.Ident:
		if e != nil {
			if _, local := e.lookup(v.Name); local {
				if vals := e.valuesOf(v.Name); len(vals) == 1 {
					return g.codeOf(f, e, vals[0], depth+1)
				}
				return 0, false
			}
		}
		if ci, ok := f.Pkg.Consts[v.Name]; ok && ci.value != nil {
			return g.codeOf(ci.file, nil, ci.value, depth+1)
		}
	case *ast.CallExpr: // codes.Code(5)
		if path, name, ok := pkgCall(f, e, v); ok && path == grpcCodes && name == "Code" && len(v.Args) == 1 {
			if bl, ok := v.Args[0].(*ast.BasicLit); ok && bl.Kind == token.INT {
				n := atoi(bl.Value)
				if n >= 0 && n < len(codeNameList) {
					return n, true
				}
			}
		}
	}
	return 0, false
}

// errSrc is what a function's error results can be: the codes (with sites), whether a
// plain (non-status) error can come out, and what could not be traced.
type errSrc struct {
	codes  []xCode
	unread []xUnread
}

func (s *errSrc) addCode(c xCode) {
	for _, o := range s.codes {
		if o.Name == c.Name && o.File == c.File && o.Line == c.Line && o.Kind == c.Kind {
			return
		}
	}
	s.codes = append(s.codes, c)
}

func (s *errSrc) addUnread(u xUnread) {
	for _, o := range s.unread {
		if o == u {
			return
		}
	}
	s.unread = append(s.unread, u)
}

func (s *errSrc) merge(o *errSrc) {
	for _, c := range o.codes {
		s.addCode(c)
	}
	for _, u := range o.unread {
		s.addUnread(u)
	}
}

func (s *errSrc) has(name string) bool {
	for _, c := range s.codes {
		if c.Name == name {
			return true
		}
	}
	return false
}

type grpcRun struct {
	memo map[*Func]*errSrc
	busy map[*Func]bool
}

// errOfFunc answers every error fn can return: the union over its return statements.
func (g *gx) errOfFunc(fn *Func, depth int, run *grpcRun) *errSrc {
	if s, ok := run.memo[fn]; ok {
		return s
	}
	out := &errSrc{}
	if depth > closureDepth || run.busy[fn] {
		return out
	}
	run.busy[fn] = true
	defer func() { delete(run.busy, fn) }()
	for _, b := range g.bodies(fn) {
		if !b.ErrorResult() {
			continue
		}
		e := g.m.EnvOf(b)
		nres := len(b.Results())
		ast.Inspect(b.Body, func(n ast.Node) bool {
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			ret, ok := n.(*ast.ReturnStmt)
			if !ok || len(ret.Results) == 0 {
				return true
			}
			var x ast.Expr
			switch {
			case len(ret.Results) == nres:
				x = ret.Results[nres-1]
			case len(ret.Results) == 1 && nres > 1:
				x = ret.Results[0] // return f(): the call's last result
			default:
				return true
			}
			out.merge(g.evalErr(b, e, x, ret.Pos(), depth, run))
			return true
		})
	}
	run.memo[fn] = out
	return out
}

// evalErr answers the errors an expression (an error result) can be.
func (g *gx) evalErr(b *Func, e *Env, x ast.Expr, at token.Pos, depth int, run *grpcRun) *errSrc {
	out := &errSrc{}
	f := b.File
	posOf := func(p token.Pos) (string, int) { return f.Rel, g.line(p) }
	notTraced := func(p token.Pos, why string) {
		file, line := posOf(p)
		out.addUnread(xUnread{File: file, Line: line, What: "grpc_error_not_traced", Reason: why})
	}
	plain := func(p token.Pos, why string) {
		file, line := posOf(p)
		out.addCode(xCode{Name: "UNKNOWN", Number: 2, Kind: "plain_error", File: file, Line: line, In: qual(topFunc(b))})
		_ = why
	}
	switch v := unparen(x).(type) {
	case *ast.Ident:
		if v.Name == "nil" {
			return out
		}
		src, tuple, found := g.lastAssign(b, v.Name, at)
		if _, local := e.lookup(v.Name); !found && !local {
			if vi, ok := f.Pkg.Vars[v.Name]; ok && vi.value != nil {
				return g.evalSentinel(vi, depth, run) // D2.3: a package-level sentinel
			}
		}
		if !found {
			notTraced(v.Pos(), "the error `"+v.Name+"` is not assigned in the function")
			return out
		}
		if tuple {
			// `a, err := call()`: the call's last result
			if c, ok := unparen(src).(*ast.CallExpr); ok {
				return g.evalCallErr(b, e, c, depth, run)
			}
			notTraced(v.Pos(), "the error `"+v.Name+"` comes from a multi-value expression")
			return out
		}
		return g.evalErr(b, e, src, src.Pos(), depth, run)
	case *ast.CallExpr:
		return g.evalCallErr(b, e, v, depth, run)
	case *ast.SelectorExpr:
		// `<gorm chain>.Error`: a database error is a plain error
		if v.Sel.Name == "Error" {
			if c, ok := unparen(v.X).(*ast.CallExpr); ok {
				if links, _ := g.m.gormChain(e, c); links != nil {
					plain(v.Pos(), "gorm error")
					return out
				}
			}
			if g.m.Infer(e, v.X).is("gorm.DB") {
				plain(v.Pos(), "gorm error")
				return out
			}
		}
		// a package-level sentinel
		if path, ok := impPath(f, e, v.X); ok {
			if pkg := g.m.ByImport[path]; pkg != nil {
				if vi, ok := pkg.Vars[v.Sel.Name]; ok && vi.value != nil {
					return g.evalSentinel(vi, depth, run)
				}
			}
		}
		notTraced(v.Pos(), "the error expression `"+g.src(v)+"` is not traced")
		return out
	}
	notTraced(x.Pos(), "the error expression `"+g.src(x)+"` is not traced")
	return out
}

func (g *gx) evalSentinel(vi varInfo, depth int, run *grpcRun) *errSrc {
	out := &errSrc{}
	if depth > closureDepth {
		return out
	}
	// evaluate the initialiser in the context of any function of its file
	var host *Func
	for _, fn := range g.m.Funcs {
		if fn.File == vi.file && fn.Lit == nil {
			host = fn
			break
		}
	}
	if host == nil {
		out.addUnread(xUnread{File: vi.file.Rel, Line: g.line(vi.value.Pos()), What: "grpc_error_not_traced", Reason: "a package-level error has no function to read it in"})
		return out
	}
	res := g.evalErr(host, g.m.EnvOf(host), vi.value, vi.value.Pos(), depth+1, run)
	if t := curWalk.sentTable(); len(t.code) > 0 && len(res.codes) == 1 && res.codes[0].Kind == "plain_error" && len(res.unread) == 0 {
		file, line := vi.file.Rel, g.line(vi.value.Pos())
		n, ok := t.code[vi.value]
		if !ok && len(t.def) == 1 && !t.nodef { // the translation's one literal default
			for d := range t.def {
				n, ok = d, true
			}
		}
		if ok {
			return &errSrc{codes: []xCode{{Name: codeNameList[n], Number: n, Kind: "status_error", File: file, Line: line, In: "sentinel"}}}
		}
		return &errSrc{unread: []xUnread{{File: file, Line: line, What: "grpc_code_not_literal", Reason: "the code is chosen by a translation the extractor cannot read to a literal"}}}
	}
	return res
}

// evalCallErr answers the errors a call expression yields as an error.
func (g *gx) evalCallErr(b *Func, e *Env, c *ast.CallExpr, depth int, run *grpcRun) *errSrc {
	out := &errSrc{}
	f := b.File
	file, line := f.Rel, g.line(c.Pos())
	site := func(code int, kind string) xCode {
		return xCode{Name: codeNameList[code], Number: code, Kind: kind, File: file, Line: line, In: qual(topFunc(b))}
	}
	if id, ok := unparen(c.Fun).(*ast.Ident); ok {
		for _, p := range b.Params() {
			if p.name == id.Name && g.src(p.typ) == "grpc.UnaryHandler" {
				return out // the rpc's own answer, not the interceptor's
			}
		}
	}
	if path, name, ok := pkgCall(f, e, c); ok {
		switch {
		case path == grpcStatus && (name == "Error" || name == "Errorf") && len(c.Args) >= 1:
			if n, ok := g.codeOf(f, e, c.Args[0], 0); ok {
				out.addCode(site(n, "status_error"))
			} else if ns := g.codesFrom(b, e, c.Args[0]); ns != nil {
				for _, n := range ns {
					out.addCode(site(n, "status_error"))
				}
			} else {
				out.addUnread(xUnread{File: file, Line: line, What: "grpc_code_not_literal",
					Reason: "the code of `" + g.src(c) + "` is chosen at run time"})
			}
			return out
		case path == "errors" && name == "New":
			out.addCode(site(2, "plain_error"))
			return out
		case path == "fmt" && name == "Errorf":
			if len(c.Args) > 0 {
				if s, ok := g.m.constStringDepth(f, e, c.Args[0], 3); ok && strings.Contains(s, "%w") {
					for _, a := range c.Args[1:] {
						nm := ""
						switch v := unparen(a).(type) {
						case *ast.Ident:
							nm = v.Name
						case *ast.SelectorExpr: // a package's sentinel
							if _, pkg := impPath(f, e, v.X); pkg {
								nm = v.Sel.Name
							}
						}
						if strings.Contains(strings.ToLower(nm), "err") {
							out.merge(g.evalErr(b, e, a, c.Pos(), depth, run))
							return out
						}
					}
				}
			}
			out.addCode(site(2, "plain_error"))
			return out
		case isStd(path):
			out.addCode(site(2, "plain_error"))
			return out
		}
	}
	// status.New(code, msg).Err()
	if sel, ok := unparen(c.Fun).(*ast.SelectorExpr); ok && sel.Sel.Name == "Err" {
		if inner, ok := unparen(sel.X).(*ast.CallExpr); ok {
			if path, name, ok := pkgCall(f, e, inner); ok && path == grpcStatus && (name == "New" || name == "Newf") && len(inner.Args) >= 1 {
				if n, ok := g.codeOf(f, e, inner.Args[0], 0); ok {
					out.addCode(site(n, "status_error"))
				} else {
					out.addUnread(xUnread{File: file, Line: line, What: "grpc_code_not_literal",
						Reason: "the code of `" + g.src(c) + "` is chosen at run time"})
				}
				return out
			}
		}
	}
	// a module function: its errors (a project error constructor, a helper)
	if callee := g.m.Callee(e, c); callee != nil && callee.Lit == nil && callee.ErrorResult() {
		out.merge(g.errOfFunc(callee, depth+1, run))
		return out
	}
	if callee := g.m.Callee(e, c); callee != nil && callee.Lit != nil && callee.ErrorResult() {
		out.merge(g.errOfFunc(callee, depth+1, run))
		return out
	}
	// a gorm call's error
	if links, _ := g.m.gormChain(e, c); links != nil {
		out.addCode(site(2, "plain_error"))
		return out
	}
	out.addUnread(xUnread{File: file, Line: line, What: "grpc_error_not_traced",
		Reason: "the error of `" + g.src(c) + "` is not traced"})
	return out
}

// lastAssign answers the value assigned to name last before at, in b: the right-hand side
// expression and whether it is a multi-value call (`a, err := f()`).
func (g *gx) lastAssign(b *Func, name string, at token.Pos) (ast.Expr, bool, bool) {
	var best ast.Expr
	var bestPos token.Pos
	tuple := false
	ast.Inspect(b.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		as, ok := n.(*ast.AssignStmt)
		if !ok || as.Pos() >= at {
			return true
		}
		for i, l := range as.Lhs {
			id, ok := l.(*ast.Ident)
			if !ok || id.Name != name {
				continue
			}
			if as.Pos() < bestPos {
				continue
			}
			switch {
			case len(as.Rhs) == len(as.Lhs):
				best, tuple, bestPos = as.Rhs[i], false, as.Pos()
			case len(as.Rhs) == 1:
				best, tuple, bestPos = as.Rhs[0], true, as.Pos()
			}
		}
		return true
	})
	return best, tuple, best != nil
}

// sentTab (D3.1) is the module's translation of plain domain errors to codes: the code each sentinel
// takes (a literal table, or an errors.Is test whose branch answers a status error) and the default.
type sentTab struct {
	code  map[ast.Expr]int
	def   map[int]bool
	nodef bool
}

// pkgVar answers the package variable x names.
func (g *gx) pkgVar(f *File, e *Env, x ast.Expr) varInfo {
	switch v := unparen(x).(type) {
	case *ast.Ident:
		if _, local := e.lookup(v.Name); !local {
			return f.Pkg.Vars[v.Name]
		}
	case *ast.SelectorExpr:
		if p, ok := impPath(f, e, v.X); ok && g.m.ByImport[p] != nil {
			return g.m.ByImport[p].Vars[v.Sel.Name]
		}
	}
	return varInfo{}
}

// translators lists the unary interceptors and the module functions they call (three deep): the code
// every handler error passes through.
func (w *grpcWalk) translators() (out []*Func) {
	g, seen := w.g, map[*Func]bool{}
	var add func(fn *Func, d int)
	add = func(fn *Func, d int) {
		if fn == nil || seen[fn] || d > 3 {
			return
		}
		seen[fn] = true
		out = append(out, fn)
		e := g.m.EnvOf(fn)
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if c, ok := n.(*ast.CallExpr); ok {
				add(g.calleeOf(e, c), d+1)
			}
			return true
		})
	}
	for _, fn := range w.funcs() {
		e := g.m.EnvOf(fn)
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if c, ok := n.(*ast.CallExpr); ok && w.isWired(fn) {
				if p, nm, ok := pkgCall(fn.File, e, c); ok && p == grpcPath && (nm == "UnaryInterceptor" || nm == "ChainUnaryInterceptor") {
					for _, v := range g.expandIC(w, e, c.Args, 0) {
						add(g.resolveInterceptor(v.e, v.x), 0)
					}
				}
			}
			return true
		})
	}
	return
}

func (w *grpcWalk) sentTable() *sentTab {
	if w.tab != nil {
		return w.tab
	}
	g, t := w.g, &sentTab{code: map[ast.Expr]int{}, def: map[int]bool{}}
	w.tab = t
	for _, fn := range w.translators() {
		e, host := g.m.EnvOf(fn), false
		rows := func(v *ast.CompositeLit, f *File, e *Env) { // table rows: `sentinel: code` or `{sentinel, code}`
			for _, el := range v.Elts {
				var k, c ast.Expr
				switch r := el.(type) {
				case *ast.KeyValueExpr:
					k, c = r.Key, r.Value
				case *ast.CompositeLit:
					if len(r.Elts) == 2 {
						k, c = r.Elts[0], r.Elts[1]
					}
				}
				if sv := g.pkgVar(f, e, k).value; sv != nil && c != nil {
					if n, ok := g.codeOf(f, e, c, 0); ok {
						t.code[sv] = n
					}
				}
			}
		}
		call := func(n ast.Node, pkg, names string) *ast.CallExpr { // a call of pkg.<one of names>
			if c, ok := n.(*ast.CallExpr); ok {
				if p, nm, ok := pkgCall(fn.File, e, c); ok && p == pkg && strings.Contains(names, nm) && len(c.Args) > 0 {
					return c
				}
			}
			return nil
		}
		guard := func(cond ast.Node, body []ast.Stmt) { // errors.Is(err, S) guarding a status error
			var sv ast.Expr
			ast.Inspect(cond, func(n ast.Node) bool {
				if c := call(n, "errors", "Is"); c != nil && len(c.Args) == 2 {
					sv = g.pkgVar(fn.File, e, c.Args[1]).value
				}
				return true
			})
			for _, st := range body {
				ast.Inspect(st, func(n ast.Node) bool {
					if c := call(n, grpcStatus, "Error Errorf"); c != nil && sv != nil {
						if k, ok := g.codeOf(fn.File, e, c.Args[0], 0); ok {
							t.code[sv] = k
						}
					}
					return true
				})
			}
		}
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			host = host || call(n, "errors", "Is") != nil
			switch v := n.(type) {
			case *ast.IfStmt:
				guard(v.Cond, v.Body.List)
			case *ast.CaseClause:
				for _, x := range v.List {
					guard(x, v.Body)
				}
			case *ast.CompositeLit:
				rows(v, fn.File, e)
			case *ast.Ident, *ast.SelectorExpr: // a package table the translator reads
				if vi := g.pkgVar(fn.File, e, n.(ast.Expr)); vi.value != nil {
					if l, ok := unparen(vi.value).(*ast.CompositeLit); ok {
						rows(l, vi.file, nil)
					}
				}
			}
			return true
		})
		if !host || fn.Lit != nil || len(fn.Body.List) == 0 {
			continue
		}
		ok := false // a translator's default: its last statement answers a status error with a literal code
		if r, isR := fn.Body.List[len(fn.Body.List)-1].(*ast.ReturnStmt); isR && len(r.Results) > 0 {
			if c := call(r.Results[len(r.Results)-1], grpcStatus, "Error Errorf"); c != nil {
				if k, lit := g.codeOf(fn.File, e, c.Args[0], 0); lit {
					t.def[k], ok = true, true
				}
			}
		}
		t.nodef = t.nodef || !ok
	}
	return t
}

// codesFrom (D3.1, D3.2) answers the codes of a code expression that is not a literal: `m[k]` over a
// package map literal with a constant key (or a parameter the calls pass constants for), or a value
// ranged out of a package table.
func (g *gx) codesFrom(b *Func, e *Env, x ast.Expr) (out []int) {
	lit := func(y ast.Expr) *ast.CompositeLit {
		l, _ := unparen(g.pkgVar(b.File, e, y).value).(*ast.CompositeLit)
		return l
	}
	kid := func(k ast.Expr) string { s := g.src(k); return s[strings.LastIndex(s, ".")+1:] }
	if ix, ok := unparen(x).(*ast.IndexExpr); ok && lit(ix.X) != nil {
		keys := []ee{{e, ix.Index}}
		if id, ok := unparen(ix.Index).(*ast.Ident); ok && paramAt(b, id) >= 0 {
			keys = curWalk.argsFor(b, id.Name, true)
		}
		for _, k := range keys {
			found := false
			for _, el := range lit(ix.X).Elts {
				if kv, ok := el.(*ast.KeyValueExpr); ok && kid(kv.Key) == kid(k.x) {
					c, ok := g.codeOf(b.File, nil, kv.Value, 0)
					if !ok {
						return nil
					}
					found = true
					if !slices.Contains(out, c) {
						out = append(out, c)
					}
				}
			}
			if !found {
				return nil
			}
		}
	} else {
		root, _, _ := strings.Cut(g.src(x), ".")
		ast.Inspect(b.Body, func(n ast.Node) bool {
			if r, ok := n.(*ast.RangeStmt); ok && (g.src(r.Key) == root || g.src(r.Value) == root) && lit(r.X) != nil {
				ast.Inspect(lit(r.X), func(m ast.Node) bool {
					if s, ok := m.(*ast.SelectorExpr); ok {
						if c, ok := g.codeOf(b.File, nil, s, 0); ok && !slices.Contains(out, c) {
							out = append(out, c)
						}
					}
					return true
				})
			}
			return true
		})
	}
	slices.Sort(out)
	return
}

// untracedReturn (D2.1): "" when an interceptor that refuses nobody returns only the handler's
// answer or errors traced to other codes, else file:line and why.
func (g *gx) untracedReturn(fn *Func, es *errSrc, run *grpcRun) string {
	rs := fn.Results()
	refusal := func(r *errSrc, f string, line int) string {
		if len(r.unread) > 0 {
			return fmt.Sprintf("%s:%d %s", r.unread[0].File, r.unread[0].Line, r.unread[0].Reason)
		} else if r.has("UNAUTHENTICATED") || r.has("PERMISSION_DENIED") {
			return fmt.Sprintf("%s:%d returns an auth refusal the extractor did not read as the interceptor's own", f, line)
		}
		return ""
	}
	why := refusal(es, fn.File.Rel, g.line(fn.Pos))
	for _, b := range g.bodies(fn)[1:] {
		ast.Inspect(b.Body, func(n ast.Node) bool {
			as, ok := n.(*ast.AssignStmt)
			for i := 0; ok && why == "" && len(rs) > 0 && len(as.Lhs) == len(as.Rhs) && i < len(as.Lhs); i++ {
				if id, isID := as.Lhs[i].(*ast.Ident); isID && id.Name == rs[len(rs)-1].name {
					why = refusal(g.evalErr(b, g.m.EnvOf(b), as.Rhs[i], as.Pos(), 0, run), b.File.Rel, g.line(as.Pos()))
				}
			}
			return why == ""
		})
	}
	return why
}

// translated (D3.3): where the module translates errors, a handler's plain error takes the translation's
// literal default, else it is unread -- never an UNKNOWN placeholder.
func (g *gx) translated(es *errSrc) *errSrc {
	t := curWalk.sentTable()
	if len(t.code) == 0 {
		return es
	}
	out := &errSrc{unread: es.unread}
	for _, c := range es.codes {
		switch {
		case c.Kind != "plain_error":
			out.addCode(c)
		case len(t.def) == 1 && !t.nodef:
			for d := range t.def {
				c.Name, c.Number, c.Kind = codeNameList[d], d, "status_error"
				out.addCode(c)
			}
		default:
			out.addUnread(xUnread{File: c.File, Line: c.Line, What: "grpc_code_not_literal", Reason: "the code of a plain error is chosen by a translation the extractor cannot read to a literal"})
		}
	}
	return out
}
