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
	bind map[string]int // E3b: the codes the call being read passes for its codes.Code parameters
	tab  *sentTab       // E1.4: the translation of one service's wrapped descriptor (else the module's)
}

// table is the translation sentinels take: this service's, else the module's.
func (r *grpcRun) table() *sentTab {
	if r.tab != nil {
		return r.tab
	}
	return curWalk.sentTable()
}

// errOfFunc answers every error fn can return: the union over its return statements.
func (g *gx) errOfFunc(fn *Func, depth int, run *grpcRun) *errSrc {
	if s, ok := run.memo[fn]; ok && run.bind == nil {
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
	if run.bind == nil {
		run.memo[fn] = out
	}
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
	if _, isID := unparen(x).(*ast.Ident); !isID {
		if ti := g.statusType(e, x); ti != nil {
			return g.statusOf(b, e, x, ti, run)
		}
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
	if host == nil { // a file of variables only (an errors.go): a synthetic host, as the walk's package-variable parents
		host = &Func{Name: "var.sentinel", Short: "var.sentinel", Type: &ast.FuncType{}, Body: &ast.BlockStmt{}, File: vi.file, Pos: vi.value.Pos()}
	}
	res := g.evalErr(host, g.m.EnvOf(host), vi.value, vi.value.Pos(), depth+1, run)
	if t := run.table(); len(t.code) > 0 && len(res.codes) == 1 && res.codes[0].Kind == "plain_error" && len(res.unread) == 0 {
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
			if n, ok := g.codeArg(f, e, c.Args[0], run); ok {
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
						if strings.Contains(strings.ToLower(nm), "err") || g.statusType(e, a) != nil {
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
	// status.New(code, msg)[.WithDetails(..)].Err(), directly or through locals (E3c)
	if sel, ok := unparen(c.Fun).(*ast.SelectorExpr); ok && sel.Sel.Name == "Err" {
		ca := func(a ast.Expr) (int, bool) { return g.codeArg(f, e, a, run) }
		if n, ok, seen := g.statusCode(b, e, sel.X, c.Pos(), ca, 0); seen {
			if ok {
				out.addCode(site(n, "status_error"))
			} else {
				out.addUnread(xUnread{File: file, Line: line, What: "grpc_code_not_literal",
					Reason: "the code of `" + g.src(c) + "` is chosen at run time"})
			}
			return out
		}
	}
	// a module function: its errors (a project error constructor, a helper); a code it takes as a
	// parameter is read at this call (E3b)
	if callee := g.m.Callee(e, c); callee != nil && callee.ErrorResult() {
		saved := run.bind
		run.bind = nil
		for i, p := range callee.Params() {
			if g.src(p.typ) == "codes.Code" {
				if run.bind == nil {
					run.bind = map[string]int{}
				}
				if n, ok := g.codeArgAt(f, e, c, i, saved, run); ok {
					run.bind[p.name] = n
				}
			}
		}
		out.merge(g.errOfFunc(callee, depth+1, run))
		run.bind = saved
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
func (w *grpcWalk) translators(extra ...*Func) (out []*Func) {
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
	for _, fn := range extra {
		add(fn, 0)
	}
	return
}

// sentTable is the module's translation; with extra, that of a service whose wrapped descriptor adds translators (E1.4).
func (w *grpcWalk) sentTable(extra ...*Func) *sentTab {
	if w.tab != nil && len(extra) == 0 {
		return w.tab
	}
	g, t := w.g, &sentTab{code: map[ast.Expr]int{}, def: map[int]bool{}}
	if len(extra) == 0 {
		w.tab = t
	}
	for _, fn := range w.translators(extra...) {
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
func (g *gx) translated(es *errSrc, run *grpcRun) *errSrc {
	t := run.table()
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

// codeArg answers a status code argument: a literal, or a parameter the call being read binds (E3b).
func (g *gx) codeArg(f *File, e *Env, x ast.Expr, run *grpcRun) (int, bool) {
	if n, ok := g.codeOf(f, e, x, 0); ok {
		return n, true
	}
	if id, ok := unparen(x).(*ast.Ident); ok {
		n, ok := run.bind[id.Name]
		return n, ok
	}
	return 0, false
}

// codeArgAt is codeArg for argument i of call c, read with the caller's bindings.
func (g *gx) codeArgAt(f *File, e *Env, c *ast.CallExpr, i int, caller map[string]int, run *grpcRun) (int, bool) {
	if i >= len(c.Args) {
		return 0, false
	}
	saved := run.bind
	run.bind = caller
	defer func() { run.bind = saved }()
	return g.codeArg(f, e, c.Args[i], run)
}

// statusCode answers the code of a *status.Status expression: status.New / Newf, through WithDetails
// (the (s, err) split included) and through locals; seen is false when x is none of these. ca reads the
// code argument.
func (g *gx) statusCode(b *Func, e *Env, x ast.Expr, at token.Pos, ca func(ast.Expr) (int, bool), depth int) (n int, ok, seen bool) {
	switch v := unparen(x).(type) {
	case *ast.CallExpr:
		if p, nm, isPkg := pkgCall(b.File, e, v); isPkg && p == grpcStatus && (nm == "New" || nm == "Newf") && len(v.Args) > 0 {
			n, ok = ca(v.Args[0])
			return n, ok, true
		}
		if s, isSel := unparen(v.Fun).(*ast.SelectorExpr); isSel && s.Sel.Name == "WithDetails" {
			return g.statusCode(b, e, s.X, at, ca, depth)
		}
	case *ast.Ident:
		if src, _, found := g.lastAssign(b, v.Name, at); found && depth < 4 {
			return g.statusCode(b, e, src, src.Pos(), ca, depth+1)
		}
	}
	return 0, false, false
}

// statusType answers the module struct type of x when it has a GRPCStatus method (E3a).
func (g *gx) statusType(e *Env, x ast.Expr) *TypeInfo {
	if t := g.m.Infer(e, x).deref(); t.Kind == "named" && t.Named != nil && t.Named.Struct != nil && g.m.MethodOf(t.Named, "GRPCStatus", 2) != nil {
		return t.Named
	}
	return nil
}

// statusOf (E3a) reads the code a returned value of a GRPCStatus type answers: the literal of each
// `status.New` the method returns, or its field read at the site that built x (never the union over sites).
func (g *gx) statusOf(b *Func, e *Env, x ast.Expr, ti *TypeInfo, run *grpcRun) *errSrc {
	out := &errSrc{}
	m := g.m.MethodOf(ti, "GRPCStatus", 2)
	me := g.m.EnvOf(m)
	file, line := b.File.Rel, g.line(x.Pos())
	ca := func(a ast.Expr) (int, bool) {
		if n, ok := g.codeOf(m.File, me, a, 0); ok {
			return n, true
		}
		if s, ok := unparen(a).(*ast.SelectorExpr); ok {
			if id, isID := s.X.(*ast.Ident); isID && id.Name == m.RecvVar {
				return g.siteField(b, e, x, ti, s.Sel.Name, run)
			}
		}
		return 0, false
	}
	read := false
	ast.Inspect(m.Body, func(nd ast.Node) bool {
		if r, ok := nd.(*ast.ReturnStmt); ok && len(r.Results) == 1 {
			n, ok, seen := g.statusCode(m, me, r.Results[0], r.Pos(), ca, 0)
			switch {
			case ok:
				out.addCode(xCode{Name: codeNameList[n], Number: n, Kind: "status_error", File: file, Line: line, In: qual(topFunc(b))})
			case seen:
				out.addUnread(xUnread{File: file, Line: line, What: "grpc_code_not_literal", Reason: "the code of `" + g.src(x) + "` is chosen at run time"})
			}
			read = read || seen
		}
		_, lit := nd.(*ast.FuncLit)
		return !lit
	})
	if !read {
		out.addUnread(xUnread{File: file, Line: line, What: "grpc_error_not_traced", Reason: "the GRPCStatus of `" + g.src(x) + "` is not traced"})
	}
	return out
}

// siteField answers the code assigned to field of the value x builds: a composite literal `&T{..}`, or the
// literal a constructor returns with its parameters bound to the call's arguments.
func (g *gx) siteField(b *Func, e *Env, x ast.Expr, ti *TypeInfo, field string, run *grpcRun) (int, bool) {
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

// fieldExpr answers the expression a struct literal gives field: keyed, or (with the struct's type) by position.
func fieldExpr(cl *ast.CompositeLit, ti *TypeInfo, field string) ast.Expr {
	if cl == nil {
		return nil
	}
	for _, el := range cl.Elts {
		if kv, ok := el.(*ast.KeyValueExpr); ok {
			if id, ok := kv.Key.(*ast.Ident); ok && id.Name == field {
				return kv.Value
			}
		}
	}
	if ti == nil || ti.Struct == nil || len(cl.Elts) == 0 {
		return nil
	}
	idx := 0
	for _, fl := range ti.Struct.Fields.List {
		for _, nm := range fl.Names {
			if _, keyed := cl.Elts[min(idx, len(cl.Elts)-1)].(*ast.KeyValueExpr); idx < len(cl.Elts) && !keyed && nm.Name == field {
				return cl.Elts[idx]
			}
			idx++
		}
	}
	return nil
}

// ---- decorated descriptors (E1) --------------------------------------------------------------

// descFromFunc (E1.1) reads the descriptor a module function returns: the generated one, or a value copy of
// it that the function modifies (ServiceName, Methods) and wraps (Handler). Every return must agree.
func (g *gx) descFromFunc(e *Env, c *ast.CallExpr, depth int) (got *grpcDesc) {
	h := g.calleeOf(e, c)
	if h == nil || depth > 3 {
		return nil
	}
	he, ok := g.m.EnvOf(h), true
	ast.Inspect(h.Body, func(n ast.Node) bool {
		if r, isR := n.(*ast.ReturnStmt); isR && len(r.Results) == 1 {
			d := g.descRet(h, he, r.Results[0], depth)
			ok = ok && d != nil && (got == nil || got.name == d.name)
			got = d
		}
		_, lit := n.(*ast.FuncLit)
		return !lit
	})
	if !ok {
		return nil
	}
	return got
}

// descRet reads one returned descriptor: through the locals it was copied into (`out := src; src := pkg.X_ServiceDesc`).
func (g *gx) descRet(h *Func, he *Env, x ast.Expr, depth int) *grpcDesc {
	strip := func(x ast.Expr) ast.Expr {
		x = unparen(x)
		if u, ok := x.(*ast.UnaryExpr); ok && u.Op == token.AND {
			return unparen(u.X)
		}
		if st, ok := x.(*ast.StarExpr); ok {
			return unparen(st.X)
		}
		return x
	}
	x, names := strip(x), map[string]bool{}
	for id, ok := x.(*ast.Ident); ok; id, ok = x.(*ast.Ident) {
		if _, local := he.lookup(id.Name); !local {
			break
		}
		if vals := he.valuesOf(id.Name); len(vals) == 1 && len(names) < 4 {
			names[id.Name], x = true, strip(vals[0])
		} else {
			return nil
		}
	}
	var d *grpcDesc
	if c, ok := x.(*ast.CallExpr); ok {
		d = g.descFromFunc(he, c, depth+1)
	} else {
		d = g.descOf(h.File, he, x)
	}
	if d == nil || len(names) == 0 {
		return d
	}
	cp := *d
	rangeVals, reset, elems, bad := map[string]bool{}, false, 0, false
	ast.Inspect(h.Body, func(n ast.Node) bool {
		switch v := n.(type) {
		case *ast.RangeStmt: // for _, m := range <desc>.Methods
			if s, ok := unparen(v.X).(*ast.SelectorExpr); ok && s.Sel.Name == "Methods" {
				rangeVals[g.src(v.Value)] = true
			}
		case *ast.KeyValueExpr: // Handler: wrapper (E1.4)
			if g.src(v.Key) == "Handler" {
				if w := g.funcRef(he, v.Value, 0); w != nil {
					cp.wrap = append(cp.wrap, w)
				} else if l, ok := v.Value.(*ast.FuncLit); ok {
					cp.wrap = append(cp.wrap, g.m.LitFunc(l, h))
				}
			}
		}
		return true
	})
	ast.Inspect(h.Body, func(n ast.Node) bool {
		as, ok := n.(*ast.AssignStmt)
		for i := 0; ok && i < len(as.Lhs); i++ {
			lhs, rhs := as.Lhs[i], as.Rhs[min(i, len(as.Rhs)-1)]
			ix, isIx := lhs.(*ast.IndexExpr)
			if isIx {
				lhs = ix.X
			}
			sel, isSel := lhs.(*ast.SelectorExpr)
			if !isSel {
				continue
			}
			if id, isID := sel.X.(*ast.Ident); !isID || !names[id.Name] {
				continue
			}
			elem := func(el ast.Expr) { // a MethodDesc literal that copies a ranged method's name
				cl, _ := unparen(el).(*ast.CompositeLit)
				mn := fieldExpr(cl, nil, "MethodName")
				if ms, isSel := mn.(*ast.SelectorExpr); cl != nil && isSel && ms.Sel.Name == "MethodName" && rangeVals[g.src(ms.X)] {
					elems++
				} else {
					bad = true
				}
			}
			switch {
			case sel.Sel.Name == "ServiceName":
				if cp.name, ok = g.strOf(h.File, he, rhs); !ok {
					g.unreadAt(h.File, as.Pos(), "descriptor_name_not_literal", "the ServiceName of the copied descriptor is `"+g.src(rhs)+"`")
					bad, cp.name = true, ""
				}
			case sel.Sel.Name != "Methods":
			case isIx:
				elem(rhs)
			default:
				switch r := unparen(rhs).(type) {
				case *ast.CompositeLit:
					cp.methods, cp.setLit = nil, len(r.Elts) > 0
					for _, el := range r.Elts {
						if mn, ok := fieldExpr(unparen(el).(*ast.CompositeLit), nil, "MethodName").(*ast.BasicLit); ok && cp.setLit {
							cp.methods = append(cp.methods, strings.Trim(mn.Value, "\"`"))
						} else {
							bad = true
						}
					}
					reset = reset || !cp.setLit
				case *ast.CallExpr:
					if f := g.src(r.Fun); f == "append" && len(r.Args) > 1 {
						elem(r.Args[1])
					} else {
						reset = reset || f == "make"
						bad = bad || f != "make"
					}
				default:
					reset = reset || g.src(rhs) == "nil"
					bad = bad || g.src(rhs) != "nil"
				}
			}
		}
		return true
	})
	cp.setBad = bad || reset && elems == 0 && !cp.setLit
	if cp.name == "" {
		return nil
	}
	return &cp
}
