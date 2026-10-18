// goextract: grpc-go servers (Spike I, D2). Reads the registrations of generated services
// (`pb.RegisterFooServer(s, impl)`, `s.RegisterService(&desc, impl)`), the handler of each
// rpc, the gRPC codes a handler can answer, the interceptors as auth evidence, the
// grpc-gateway registrations, and the tables, owner uses and state writes of the handlers'
// closures (by calling the existing machinery, read-only). The result is the `grpc` key of
// lattice.goextract/1; the key is omitted when the module has no .proto file and no server
// registration, so every HTTP-only extract keeps its bytes.
//
// Nothing is guessed: a code chosen at run time is an `unread` item (grpc_code_not_literal),
// an error the extractor cannot trace is `grpc_error_not_traced`, an interceptor it cannot
// read is `interceptor_not_literal` and the auth of the rpcs it guards is `unclear`.
package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"regexp"
	"slices"
	"sort"
	"strings"
)

const (
	grpcPath     = "google.golang.org/grpc"
	grpcCodes    = "google.golang.org/grpc/codes"
	grpcStatus   = "google.golang.org/grpc/status"
	grpcMetadata = "google.golang.org/grpc/metadata"
)

// xCode is one gRPC code a handler can answer, with the site that constructs it.
type xCode struct {
	Name   string `json:"name"`
	Number int    `json:"number"`
	Kind   string `json:"kind"` // status_error | plain_error
	File   string `json:"file"`
	Line   int    `json:"line"`
	In     string `json:"in"`
}

// xWrite is a GORM write in an rpc's closure: op create | save | update | delete.
type xWrite struct {
	Table string `json:"table"`
	Op    string `json:"op"`
	File  string `json:"file"`
	Line  int    `json:"line"`
}

var writeOps = map[string]string{
	"Create": "create", "CreateInBatches": "create", "Save": "save", "Update": "update",
	"Updates": "update", "UpdateColumn": "update", "UpdateColumns": "update", "Delete": "delete",
}

// rpcWrites lists the GORM writes of a closure (the tables they name, by the same rules as
// the state-write finder).
func (g *gx) rpcWrites(ci *closureInfo) []xWrite {
	seen := map[string]bool{}
	var out []xWrite
	for _, v := range ci.visits {
		for _, b := range v.bodies {
			e := g.m.EnvOf(b)
			ast.Inspect(b.Body, func(n ast.Node) bool {
				if _, ok := n.(*ast.FuncLit); ok && n != ast.Node(b.Lit) {
					return false
				}
				call, ok := n.(*ast.CallExpr)
				if !ok {
					return true
				}
				links, root := g.m.gormChain(e, call)
				if links == nil {
					return true
				}
				last := links[len(links)-1]
				op, ok := writeOps[last.name]
				if !ok || last.call != call {
					return true
				}
				t := g.rootTable(e, links, root, 3)
				if t == nil {
					return true
				}
				w := xWrite{Table: t.Table, Op: op, File: b.File.Rel, Line: g.line(call.Pos())}
				key := fmt.Sprintf("%s|%s|%s|%d", w.Table, w.Op, w.File, w.Line)
				if !seen[key] {
					seen[key] = true
					out = append(out, w)
				}
				return true
			})
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		a, b := out[i], out[j]
		if a.File != b.File {
			return a.File < b.File
		}
		if a.Line != b.Line {
			return a.Line < b.Line
		}
		return a.Op < b.Op
	})
	return out
}

type xHTTPBind struct {
	Method string `json:"method"`
	Path   string `json:"path"`
}

type xInterceptor struct {
	Func    string   `json:"func"`
	File    string   `json:"file"`
	Line    int      `json:"line"`
	Kind    string   `json:"kind"` // auth_literal | auth_not_literal | other
	Public  []string `json:"public,omitempty"`
	Prefix  []string `json:"public_prefix,omitempty"`
	Reason  string   `json:"reason,omitempty"`
	Chained bool     `json:"chained"`
}

type xRegistration struct {
	Service string `json:"service"`
	Impl    string `json:"impl"`
	Via     string `json:"via"`
	File    string `json:"file"`
	Line    int    `json:"line"`
	Streams int    `json:"streams"`
	// External: a service of a package outside the module (health, reflection): not part of
	// the module's contract, listed for completeness.
	External bool `json:"external,omitempty"`
}

type xGateway struct {
	Service string `json:"service"`
	Via     string `json:"via"`
	File    string `json:"file"`
	Line    int    `json:"line"`
}

type xGrpc struct {
	Protos        []xProtoFile     `json:"protos"`
	Services      []*xService      `json:"services"`
	Messages      []*xProtoMessage `json:"messages"`
	Enums         []*xProtoEnum    `json:"enums"`
	Registrations []xRegistration  `json:"registrations"`
	Interceptors  []xInterceptor   `json:"interceptors"`
	Gateway       []xGateway       `json:"gateway"`
	StatusWrites  []*xStatusW      `json:"status_writes"`
	Notes         []string         `json:"notes"`
}

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

// ---- registrations ---------------------------------------------------------------------

type grpcDesc struct {
	name    string
	methods []string
	streams []string
	file    string
	line    int
}

type grpcReg struct {
	desc  *grpcDesc
	impl  ast.Expr
	fn    *Func
	e     *Env
	call  *ast.CallExpr
	via   string
	short string // the service's short name when the descriptor is outside the module
	lost  bool   // referenced, but not reached from main (R6)
	svc   bool   // read from RegisterService: no descriptor means unresolved (D1.3)
}

func paramAt(fn *Func, x ast.Expr) int {
	for i, p := range fn.Params() {
		if id, ok := unparen(x).(*ast.Ident); ok && p.name == id.Name {
			return i
		}
	}
	return -1
}

var registerRE = regexp.MustCompile(`^Register(\w+)Server$`)
var gatewayRE = regexp.MustCompile(`^Register(\w+)Handler(Server|FromEndpoint|Client)?$`)

// followDesc (R5): through a local's single value or a non-literal package variable.
func (g *gx) followDesc(f *File, e *Env, x ast.Expr) (*File, *Env, ast.Expr) {
	for i := 0; i < 4; i++ {
		x = unparen(x)
		if u, ok := x.(*ast.UnaryExpr); ok && u.Op == token.AND {
			x = unparen(u.X)
		}
		var vi varInfo
		switch v := x.(type) {
		case *ast.Ident:
			if vals := e.valuesOf(v.Name); len(vals) == 1 {
				x = vals[0]
				continue
			}
			vi = f.Pkg.Vars[v.Name]
		case *ast.SelectorExpr:
			if path, ok := impPath(f, e, v.X); ok && g.m.ByImport[path] != nil {
				vi = g.m.ByImport[path].Vars[v.Sel.Name]
			}
		}
		if _, lit := unparen(vi.value).(*ast.CompositeLit); vi.value == nil || lit {
			break
		}
		f, e, x = vi.file, nil, vi.value
	}
	return f, e, x
}

// descOf reads a grpc.ServiceDesc: `&Foo_ServiceDesc`, `Foo_ServiceDesc`, or a literal.
func (g *gx) descOf(f *File, e *Env, x ast.Expr) *grpcDesc {
	f, e, x = g.followDesc(f, e, x)
	x = unparen(x)
	if u, ok := x.(*ast.UnaryExpr); ok && u.Op == token.AND {
		x = unparen(u.X)
	}
	var lit *ast.CompositeLit
	var lf *File
	switch v := x.(type) {
	case *ast.CompositeLit:
		lit, lf = v, f
	case *ast.Ident:
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
		return nil
	}
	d := &grpcDesc{file: lf.Rel, line: g.line(lit.Pos())}
	for _, el := range lit.Elts {
		kv, ok := el.(*ast.KeyValueExpr)
		if !ok {
			continue
		}
		key, _ := kv.Key.(*ast.Ident)
		if key == nil {
			continue
		}
		switch key.Name {
		case "ServiceName":
			d.name, _ = g.m.constStringDepth(lf, nil, kv.Value, 3)
		case "Methods", "Streams":
			arr, ok := unparen(kv.Value).(*ast.CompositeLit)
			if !ok {
				continue
			}
			want := "MethodName"
			if key.Name == "Streams" {
				want = "StreamName"
			}
			for _, item := range arr.Elts {
				cl, ok := item.(*ast.CompositeLit)
				if !ok {
					continue
				}
				for _, ie := range cl.Elts {
					if ikv, ok := ie.(*ast.KeyValueExpr); ok {
						if id, ok := ikv.Key.(*ast.Ident); ok && id.Name == want {
							if s, ok := g.m.constStringDepth(lf, nil, ikv.Value, 3); ok {
								if key.Name == "Methods" {
									d.methods = append(d.methods, s)
								} else {
									d.streams = append(d.streams, s)
								}
							}
						}
					}
				}
			}
		}
	}
	if d.name == "" {
		return nil
	}
	return d
}

// grpcWalk (D1.0-D1.1): g.wired plus the package variables a wired function reads (g.wired is not).
type grpcWalk struct {
	g     *gx
	wired map[*Func]bool
	queue []*Func
	vars  map[string]*Func
	refs  map[string]int // identifier -> uses outside declarations
	vrefs map[string]int // the same without field names and literal keys (for variables)
	sites map[*Func][]site
	inst  map[string]bool // D1.4: types built in walked code
	dyn   map[string]bool // D1.4: methods called on a value of unknown type
	okM   map[string]bool // D1.4: methods some built type answers
	tab   *sentTab
}

var curWalk *grpcWalk

func isGenFile(f *File) bool {
	return strings.HasSuffix(f.Rel, ".pb.go") || strings.HasSuffix(f.Rel, ".pb.gw.go")
}

func (g *gx) newGrpcWalk() *grpcWalk {
	w := &grpcWalk{g: g, wired: map[*Func]bool{}, vars: map[string]*Func{}, refs: map[string]int{}, vrefs: map[string]int{},
		inst: map[string]bool{}, dyn: map[string]bool{}, okM: map[string]bool{}}
	curWalk = w
	decl, field := map[*ast.Ident]bool{}, map[*ast.Ident]bool{}
	for _, p := range g.m.Pkgs {
		for _, f := range p.Files {
			if isGenFile(f) {
				continue
			}
			ast.Inspect(f.AST, func(n ast.Node) bool {
				switch v := n.(type) {
				case *ast.FuncDecl:
					decl[v.Name] = true
				case *ast.ValueSpec:
					for _, id := range v.Names {
						decl[id] = true
					}
				case *ast.Field:
					for _, id := range v.Names {
						field[id] = true
					}
				case *ast.SelectorExpr:
					field[v.Sel] = true
				case *ast.KeyValueExpr:
					if id, ok := v.Key.(*ast.Ident); ok {
						field[id] = true
					}
				case *ast.Ident:
					if !decl[v] {
						w.refs[v.Name]++
						if !field[v] {
							w.vrefs[v.Name]++
						}
					}
				}
				return true
			})
		}
	}
	if g.hasMain {
		for _, fn := range g.m.AllFuncs() {
			if g.wired[fn] {
				w.add(fn)
			}
		}
		w.addInits()
		for changed := true; changed; changed = w.reachImpls() {
			for ; len(w.queue) > 0; w.queue = w.queue[1:] {
				w.scan(w.queue[0])
			}
		}
		for _, p := range g.m.Pkgs { // unwalked package variables, listed unwired (round-3 deviation 8.7)
			for n := range p.Vars {
				w.varFunc(p, n)
			}
		}
	}
	return w
}

// addInits wires the init functions of every package a main imports, blank imports included.
func (w *grpcWalk) addInits() {
	seen := map[*Pkg]bool{}
	var visit func(p *Pkg)
	visit = func(p *Pkg) {
		seen[p] = true
		for _, f := range p.Files {
			for _, im := range f.AST.Imports {
				if q := w.g.m.ByImport[strings.Trim(im.Path.Value, "`\"")]; q != nil && !seen[q] {
					visit(q)
				}
			}
		}
		for _, fn := range w.g.m.Funcs {
			if fn.Short == "init" && !fn.isMethod && fn.Pkg() == p {
				w.add(fn)
			}
		}
	}
	for _, p := range w.g.m.Pkgs {
		if p.Name == "main" && p.Funcs["main"] != nil && !seen[p] {
			visit(p)
		}
	}
}

// reachImpls (D1.4): a method called on an interface value runs on every built type that has it.
func (w *grpcWalk) reachImpls() (changed bool) {
	for n := range w.dyn {
		for _, fn := range w.g.m.methods[n] {
			if w.inst[fn.Recv] {
				w.okM[n], changed = true, changed || !w.wired[fn]
				w.add(fn)
			}
		}
	}
	return
}

func (w *grpcWalk) add(fn *Func) {
	if fn != nil && !w.wired[fn] {
		w.wired[fn] = true
		w.queue = append(w.queue, fn)
	}
}

func (w *grpcWalk) isWired(fn *Func) bool { return !w.g.hasMain || w.wired[topFunc(fn)] }

// varFunc is package variable name's synthetic parent: its body is the initializer.
func (w *grpcWalk) varFunc(pkg *Pkg, name string) *Func {
	vi, ok := pkg.Vars[name]
	if !ok || vi.value == nil || isGenFile(vi.file) {
		return nil
	}
	key := pkg.Rel + "." + name
	if w.vars[key] == nil {
		body := &ast.BlockStmt{List: []ast.Stmt{&ast.ExprStmt{X: vi.value}}}
		w.vars[key] = &Func{Name: "var." + name, Short: "var." + name, Type: &ast.FuncType{}, Body: body, File: vi.file, Pos: vi.value.Pos()}
	}
	return w.vars[key]
}

func (w *grpcWalk) useVar(pkg *Pkg, name string) { w.add(w.varFunc(pkg, name)) }

// scan applies the HTTP walker's rules to fn's body and reads the package variables it names.
func (w *grpcWalk) scan(fn *Func) {
	m, e := w.g.m, w.g.m.EnvOf(fn)
	skip := map[*ast.Ident]bool{} // a field selector or key is no variable
	ref := func(x ast.Expr) {
		if f := w.g.funcRef(e, x, 0); f != nil && f.Lit == nil {
			w.add(f)
		}
	}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		switch v := n.(type) {
		case *ast.FuncLit:
			w.add(m.LitFunc(v, fn))
			return false
		case *ast.CallExpr:
			callee := w.g.calleeOf(e, v)
			w.add(callee)
			w.built(fn, e, v.Fun, callee == nil)
		case *ast.CompositeLit:
			w.built(fn, e, v.Type, false)
		case *ast.KeyValueExpr:
			if id, ok := v.Key.(*ast.Ident); ok {
				skip[id] = true
			}
		case *ast.SelectorExpr:
			skip[v.Sel] = true
			ref(v)
			if path, ok := impPath(fn.File, e, v.X); ok && m.ByImport[path] != nil {
				w.useVar(m.ByImport[path], v.Sel.Name)
			}
		case *ast.Ident:
			ref(v)
			if _, local := e.lookup(v.Name); !local && !skip[v] {
				w.useVar(fn.File.Pkg, v.Name)
			}
		}
		return true
	})
}

// built records a type the walked code builds (a literal or a conversion) and a method it calls on a
// value of unknown type (D1.4).
func (w *grpcWalk) built(fn *Func, e *Env, x ast.Expr, call bool) {
	switch t := unparen(x).(type) {
	case *ast.Ident:
		if _, local := e.lookup(t.Name); !local && fn.File.Pkg.Types[t.Name] != nil {
			w.inst[t.Name] = true
		}
	case *ast.SelectorExpr:
		if path, ok := impPath(fn.File, e, t.X); ok && w.g.m.ByImport[path] != nil {
			w.inst[t.Sel.Name] = w.g.m.ByImport[path].Types[t.Sel.Name] != nil
		} else if call && !ok {
			w.dyn[t.Sel.Name] = true
		}
	}
}

// funcs lists every function and literal of the module, then the synthetic parents of the
// package variables that were walked (sorted) with the literals in their initializers.
func (w *grpcWalk) funcs() []*Func {
	out, keys := w.g.m.AllFuncs(), []string{}
	for k := range w.vars {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		out = append(out, w.g.bodies(w.vars[k])...)
	}
	return out
}

// ee is an expression with the environment it is read in; site a call with its caller.
type ee struct {
	e *Env
	x ast.Expr
}

type site struct {
	fn    *Func
	e     *Env
	c     *ast.CallExpr
	wired bool
}

// calleeOf is Callee that sees through an explicit instantiation `f[T](...)`.
func (g *gx) calleeOf(e *Env, c *ast.CallExpr) *Func {
	switch f := unparen(c.Fun).(type) {
	case *ast.IndexExpr:
		c = &ast.CallExpr{Fun: f.X, Args: c.Args}
	case *ast.IndexListExpr:
		c = &ast.CallExpr{Fun: f.X, Args: c.Args}
	}
	return g.m.Callee(e, c)
}

// callSites lists the calls of fn: the wired ones, or all (the handlers are not wired from main).
func (w *grpcWalk) callSites(fn *Func, all bool) (out []site) {
	if w.sites == nil {
		w.sites = map[*Func][]site{}
		for _, f := range w.funcs() {
			e := w.g.m.EnvOf(f)
			ast.Inspect(f.Body, func(n ast.Node) bool {
				if c, ok := n.(*ast.CallExpr); ok {
					if k := w.g.calleeOf(e, c); k != nil {
						w.sites[k] = append(w.sites[k], site{f, e, c, w.isWired(f)})
					}
				}
				_, lit := n.(*ast.FuncLit)
				return !lit
			})
		}
	}
	for _, s := range w.sites[fn] {
		if all || s.wired {
			out = append(out, s)
		}
	}
	return
}

// argsFor answers what the calls pass for parameter name of fn (or of the function fn is a literal in).
func (w *grpcWalk) argsFor(fn *Func, name string, all bool) (out []ee) {
	for o := fn; o != nil; o = o.Parent {
		for i, p := range o.Params() {
			if p.name != name {
				continue
			}
			_, variadic := p.typ.(*ast.Ellipsis)
			out = []ee{} // a parameter nobody passes is no value
			for _, s := range w.callSites(o, all) {
				for j := i; j < len(s.c.Args) && (variadic || j == i); j++ {
					out = append(out, ee{s.e, s.c.Args[j]})
				}
			}
			return
		}
	}
	return
}

// fieldFlows answers the values assigned, appended or put in a literal for field of ti in wired code.
func (w *grpcWalk) fieldFlows(ti *TypeInfo, field string) (out []ee) {
	for _, fn := range w.funcs() {
		e := w.g.m.EnvOf(fn)
		isTi := func(x ast.Expr) bool { return w.g.m.Infer(e, x).deref().rowTypeAny() == ti }
		add := func(xs ...ast.Expr) {
			for _, x := range xs {
				if id, ok := unparen(x).(*ast.Ident); ok && w.argsFor(fn, id.Name, false) != nil {
					out = append(out, w.argsFor(fn, id.Name, false)...)
				} else {
					out = append(out, ee{e, x})
				}
			}
		}
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			switch v := n.(type) {
			case *ast.AssignStmt:
				if s, ok := v.Lhs[0].(*ast.SelectorExpr); ok && len(v.Rhs) == 1 && s.Sel.Name == field && isTi(s.X) {
					if c, ok := v.Rhs[0].(*ast.CallExpr); ok && len(c.Args) > 1 && w.g.src(c.Fun) == "append" {
						add(c.Args[1:]...)
					} else {
						add(v.Rhs[0])
					}
				}
			case *ast.CompositeLit:
				for _, el := range v.Elts {
					if kv, ok := el.(*ast.KeyValueExpr); ok && w.g.src(kv.Key) == field && isTi(v) {
						add(kv.Value)
					}
				}
			}
			_, lit := n.(*ast.FuncLit)
			return !lit
		})
	}
	return
}

// findGrpcRegistrations lists the server registrations of the wired functions; unknown are
// those in a function that is referenced but not reached (R6).
func (g *gx) findGrpcRegistrations(w *grpcWalk) (regs []*grpcReg, dropped []xExcluded, unknown []*grpcReg) {
	for _, fn := range w.funcs() {
		e := g.m.EnvOf(fn)
		f := fn.File
		if isGenFile(f) {
			continue // generated code is not where a server is wired
		}
		stmtWalk(fn.Body, func(call *ast.CallExpr, _ []ast.Node) {
			if len(call.Args) != 2 {
				return
			}
			var r *grpcReg
			switch fun := unparen(call.Fun).(type) {
			case *ast.SelectorExpr:
				if fun.Sel.Name == "RegisterService" {
					r = &grpcReg{desc: g.descOf(f, e, call.Args[0]), via: "RegisterService", svc: true}
					break
				}
				if m := registerRE.FindStringSubmatch(fun.Sel.Name); m != nil {
					r = &grpcReg{via: fun.Sel.Name, short: m[1]}
					if callee := g.m.Callee(e, call); callee != nil {
						// the generated function's body registers the descriptor
						ce := g.m.EnvOf(callee)
						stmtWalk(callee.Body, func(inner *ast.CallExpr, _ []ast.Node) {
							if isel, ok := unparen(inner.Fun).(*ast.SelectorExpr); ok && isel.Sel.Name == "RegisterService" && len(inner.Args) == 2 && r.desc == nil {
								r.desc = g.descOf(callee.File, ce, inner.Args[0])
							}
						})
					}
				}
			case *ast.Ident:
				if m := registerRE.FindStringSubmatch(fun.Name); m != nil {
					r = &grpcReg{via: fun.Name, short: m[1]}
					if callee := g.m.Callee(e, call); callee != nil {
						ce := g.m.EnvOf(callee)
						stmtWalk(callee.Body, func(inner *ast.CallExpr, _ []ast.Node) {
							if isel, ok := unparen(inner.Fun).(*ast.SelectorExpr); ok && isel.Sel.Name == "RegisterService" && len(inner.Args) == 2 && r.desc == nil {
								r.desc = g.descOf(callee.File, ce, inner.Args[0])
							}
						})
					}
				}
			}
			if r == nil {
				return
			}
			r.impl, r.fn, r.e, r.call = call.Args[1], fn, e, call
			rs := []*grpcReg{r}
			if dp, ip := paramAt(fn, call.Args[0]), paramAt(fn, call.Args[1]); fn.Lit == nil && (ip >= 0 || dp >= 0 && r.desc == nil) {
				rs = nil // D1.1: a descriptor or impl that is a parameter is read at each wired call of the helper
				for _, s := range w.callSites(fn, false) {
					c := *r
					c.fn, c.e, c.call, c.via = s.fn, s.e, s.c, fmt.Sprintf("%s via %s:%d", r.via, f.Rel, g.line(call.Pos()))
					if ip >= 0 && ip < len(s.c.Args) {
						c.impl = s.c.Args[ip]
					}
					if dp >= 0 && r.desc == nil && dp < len(s.c.Args) {
						c.desc = g.descOf(s.fn.File, s.e, s.c.Args[dp])
					}
					rs = append(rs, &c)
				}
				if rs == nil {
					rs = []*grpcReg{r}
				}
			}
			for _, r := range rs {
				if w.isWired(r.fn) {
					regs = append(regs, r)
					continue
				}
				t, nm := topFunc(r.fn), strings.TrimPrefix(topFunc(r.fn).Short, "var.")
				r.lost = w.refs[nm] > 0 && (t.Recv == "" || !w.okM[t.Short] || w.inst[t.Recv])
				if nm != t.Short {
					r.lost = w.vrefs[nm] > 0
				}
				if r.desc == nil && r.svc && !r.lost {
					continue
				}
				name := r.via
				if r.desc != nil {
					name = r.desc.name
				}
				dropped = append(dropped, xExcluded{Kind: "unwired", What: "rpc_registration", Method: "RPC",
					Path: name, File: r.fn.File.Rel, Line: g.line(r.call.Pos()), Reason: "not reachable from main"})
				if r.lost {
					unknown = append(unknown, r)
				}
			}
		})
	}
	// literals appear in AllFuncs as well: drop a registration seen from both
	seen := map[token.Pos]bool{}
	var uniq []*grpcReg
	for _, r := range regs {
		if seen[r.call.Pos()] {
			continue
		}
		seen[r.call.Pos()] = true
		uniq = append(uniq, r)
	}
	return uniq, dropped, unknown
}

// implType answers the struct type a registration's second argument is.
func (g *gx) implType(e *Env, x ast.Expr, depth int) *TypeInfo {
	if depth > 4 {
		return nil
	}
	t := g.m.Infer(e, x).deref()
	if t.Kind == "named" && t.Named != nil && t.Named.Struct != nil {
		return t.Named
	}
	if id, ok := unparen(x).(*ast.Ident); ok {
		if vals := e.valuesOf(id.Name); len(vals) == 1 {
			return g.implType(e, vals[0], depth+1)
		}
	}
	if c, ok := unparen(x).(*ast.CallExpr); ok {
		if callee := g.m.Callee(e, c); callee != nil {
			set := map[*TypeInfo]bool{}
			ast.Inspect(callee.Body, func(nd ast.Node) bool {
				if ret, ok := nd.(*ast.ReturnStmt); ok && len(ret.Results) == 1 {
					set[g.implType(g.m.EnvOf(callee), ret.Results[0], depth+1)] = true
				}
				_, lit := nd.(*ast.FuncLit)
				return !lit
			})
			delete(set, nil)
			for ti := range set {
				if len(set) == 1 {
					return ti
				}
			}
		}
	}
	return nil
}

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

// ---- the extraction --------------------------------------------------------------------

func rpcKey(svc, name string) string { return svc + "/" + name }

// findGrpc reads the module's gRPC contract and servers into g.res.Grpc.
func (g *gx) findGrpc() {
	u := g.readProtos(g.m.Root)
	w := g.newGrpcWalk()
	regs, unwired, unknown := g.findGrpcRegistrations(w)
	if len(u.files) == 0 && len(regs) == 0 && len(unwired) == 0 {
		return
	}
	gr := &xGrpc{Registrations: []xRegistration{}, Interceptors: []xInterceptor{}, Gateway: []xGateway{},
		StatusWrites: []*xStatusW{}, Notes: []string{}}
	gr.Protos, gr.Services, gr.Messages, gr.Enums = u.output()
	g.res.Excluded = append(g.res.Excluded, unwired...)

	ics := g.interceptorFuncs(w)
	gr.Interceptors = append(gr.Interceptors, ics...)
	g.seedCallerFuncs(ics)

	// registrations by service name
	byService := map[string]*grpcReg{}
	var order, unres []string // unres: file:line of each registration not resolved to a service (D1.3)
	var first *grpcReg
	for _, r := range append(regs[:len(regs):len(regs)], unknown...) {
		name := ""
		if r.desc != nil {
			name = r.desc.name
		} else {
			// outside the module: match the proto service by its short name
			var hit []string
			for _, s := range gr.Services {
				if strings.HasSuffix(s.Name, "."+r.short) || s.Name == r.short {
					hit = append(hit, s.Name)
				}
			}
			if len(hit) == 1 {
				name = hit[0]
			}
		}
		if r.lost || r.svc && r.desc == nil {
			unres = append(unres, fmt.Sprintf("%s:%d", r.fn.File.Rel, g.line(r.call.Pos())))
			if first == nil {
				first = r
			}
			continue
		}
		if name == "" {
			if r.desc == nil {
				gr.Registrations = append(gr.Registrations, xRegistration{Service: r.short, Impl: g.src(r.impl), Via: r.via,
					File: r.fn.File.Rel, Line: g.line(r.call.Pos()), External: true})
				continue
			}
			g.unreadAt(r.fn.File, r.call.Pos(), "grpc_registration", "the service registered by `"+g.src(r.call)+"` is not resolved")
			continue
		}
		if _, dup := byService[name]; !dup {
			byService[name] = r
			order = append(order, name)
		}
		streams := 0
		if r.desc != nil {
			streams = len(r.desc.streams)
		}
		gr.Registrations = append(gr.Registrations, xRegistration{Service: name, Impl: g.src(r.impl), Via: r.via,
			File: r.fn.File.Rel, Line: g.line(r.call.Pos()), Streams: streams})
	}
	sort.Slice(gr.Registrations, func(i, j int) bool {
		a, b := gr.Registrations[i], gr.Registrations[j]
		if a.Service != b.Service {
			return a.Service < b.Service
		}
		return a.Line < b.Line
	})

	gr.Gateway = g.findGateways(gr.Services)

	run := &grpcRun{memo: map[*Func]*errSrc{}, busy: map[*Func]bool{}}
	var recs []*routeRec
	for _, svc := range gr.Services {
		reg := byService[svc.Name]
		var ti *TypeInfo
		if reg != nil {
			ti = g.implType(reg.e, reg.impl, 0)
			if ti == nil {
				g.unreadAt(reg.fn.File, reg.call.Pos(), "grpc_registration", "the type of `"+g.src(reg.impl)+"` is not resolved: the handlers of "+svc.Name+" are not read")
			}
		}
		for _, r := range svc.Rpcs {
			key := rpcKey(svc.Name, r.Name)
			switch {
			case r.ClientStreaming || r.ServerStreaming:
				r.State = "streaming"
				g.res.Excluded = append(g.res.Excluded, xExcluded{Kind: "streaming", What: "rpc", Method: "RPC",
					Path: "/" + key, File: r.File, Line: r.Line, Reason: "streaming_not_supported"})
				continue
			case reg == nil && first != nil:
				x := xUnread{File: first.fn.File.Rel, Line: g.line(first.call.Pos()), What: "registration_reachability_unknown",
					Reason: "`" + g.src(first.call) + "` is not resolved to a service (" + strings.Join(unres, ", ") + ")"}
				r.State, r.Unread = "unread", append(r.Unread, x)
				g.addUnread(x.File, x.Line, x.What, x.Reason)
				continue
			case reg == nil:
				r.State = "not_registered"
				continue
			case ti == nil:
				r.State = "unread"
				continue
			}
			h := g.m.MethodOf(ti, r.Name, 3)
			if h == nil || isStub(h) {
				r.State = "unimplemented"
				r.Codes = []xCode{{Name: "UNIMPLEMENTED", Number: 12, Kind: "unimplemented_stub", File: r.File, Line: r.Line, In: key}}
			} else {
				r.State = "implemented"
				r.Handler = &xHandler{Func: qual(h), Module: funcImport(h), File: h.File.Rel, Line: g.line(h.Pos), Wrappers: []string{}}
			}
			for _, gw := range gr.Gateway {
				if gw.Service == svc.Name && r.HTTP != nil {
					r.HTTPBindings = append(r.HTTPBindings, xHTTPBind{Method: strings.ToUpper(r.HTTP.Method), Path: r.HTTP.Path})
					for _, a := range r.HTTP.Additional {
						r.HTTPBindings = append(r.HTTPBindings, xHTTPBind{Method: strings.ToUpper(a.Method), Path: a.Path})
					}
					break
				}
			}
			if r.State != "implemented" {
				continue
			}
			ci := g.analyze(h)
			recs = append(recs, &routeRec{closure: ci, out: &xRoute{Method: "RPC", PathTemplate: "/" + key}})
			es := g.errOfFunc(h, 0, run)
			g.fillRPC(r, h, ci, es, run)
		}
	}
	g.rpcAuth(gr, ics, run)

	// state writes rooted at the rpcs: the existing finder, run once more with the rpcs as the
	// route list, then merged in place of the roots the HTTP run gave the same writes
	savedList, savedWrites := g.routeList, g.res.StatusWrites
	g.routeList, g.res.StatusWrites = recs, nil
	g.findStatusWrites()
	rpcWrites := g.res.StatusWrites
	g.routeList, g.res.StatusWrites = savedList, savedWrites
	var keep []*xStatusW
	for _, w := range rpcWrites {
		if strings.HasPrefix(w.Root, "RPC ") {
			keep = append(keep, w)
		}
	}
	gr.StatusWrites = keep
	if gr.StatusWrites == nil {
		gr.StatusWrites = []*xStatusW{}
	}
	seen := map[string]bool{}
	for _, w := range keep {
		seen[fmt.Sprintf("%s|%d|%s", w.File, w.Line, w.Value)] = true
	}
	var rest []*xStatusW
	for _, w := range g.res.StatusWrites {
		if seen[fmt.Sprintf("%s|%d|%s", w.File, w.Line, w.Value)] && !strings.HasPrefix(w.Root, "RPC ") {
			continue
		}
		rest = append(rest, w)
	}
	g.res.StatusWrites = rest
	if g.res.StatusWrites == nil {
		g.res.StatusWrites = []*xStatusW{}
	}
	g.res.Grpc = gr
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

// fillRPC fills the closure-derived facts of one implemented rpc.
func (g *gx) fillRPC(r *xRPC, h *Func, ci *closureInfo, es *errSrc, run *grpcRun) {
	es = g.translated(es)
	sort.SliceStable(es.codes, func(i, j int) bool {
		a, b := es.codes[i], es.codes[j]
		if a.Number != b.Number {
			return a.Number < b.Number
		}
		if a.File != b.File {
			return a.File < b.File
		}
		return a.Line < b.Line
	})
	r.Codes = es.codes
	for _, c := range es.codes {
		if c.Kind == "plain_error" {
			r.PlainError = true
		}
	}
	for _, u := range es.unread {
		r.Unread = append(r.Unread, u)
		g.addUnread(u.File, u.Line, u.What, u.Reason)
	}
	for t := range ci.tables {
		name := t
		if xt := g.byStruct[t]; xt != nil {
			name = xt.Table
		}
		r.Tables = append(r.Tables, name)
	}
	sort.Strings(r.Tables)
	r.OwnerUses = ci.owners
	r.Writes = g.rpcWrites(ci)
	for c := range ci.calls {
		r.Calls = append(r.Calls, c)
	}
	sort.Strings(r.Calls)
	for _, u := range ci.unread {
		r.Unread = append(r.Unread, u)
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

// findGateways lists the grpc-gateway registrations (`RegisterFooHandlerFromEndpoint`...).
func (g *gx) findGateways(services []*xService) []xGateway {
	var out []xGateway
	seen := map[string]bool{}
	for _, fn := range g.m.AllFuncs() {
		if !g.isWired(topFunc(fn)) || strings.HasSuffix(fn.File.Rel, ".pb.gw.go") {
			continue
		}
		stmtWalk(fn.Body, func(call *ast.CallExpr, _ []ast.Node) {
			var name string
			switch f := unparen(call.Fun).(type) {
			case *ast.SelectorExpr:
				name = f.Sel.Name
			case *ast.Ident:
				name = f.Name
			}
			m := gatewayRE.FindStringSubmatch(name)
			if m == nil || registerRE.MatchString(name) {
				return
			}
			var hit []string
			for _, s := range services {
				if strings.HasSuffix(s.Name, "."+m[1]) || s.Name == m[1] {
					hit = append(hit, s.Name)
				}
			}
			if len(hit) != 1 || seen[hit[0]] {
				return
			}
			seen[hit[0]] = true
			out = append(out, xGateway{Service: hit[0], Via: name, File: fn.File.Rel, Line: g.line(call.Pos())})
		})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Service < out[j].Service })
	if out == nil {
		out = []xGateway{}
	}
	return out
}
