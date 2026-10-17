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
	return g.evalErr(host, g.m.EnvOf(host), vi.value, vi.value.Pos(), depth+1, run)
}

// evalCallErr answers the errors a call expression yields as an error.
func (g *gx) evalCallErr(b *Func, e *Env, c *ast.CallExpr, depth int, run *grpcRun) *errSrc {
	out := &errSrc{}
	f := b.File
	file, line := f.Rel, g.line(c.Pos())
	site := func(code int, kind string) xCode {
		return xCode{Name: codeNameList[code], Number: code, Kind: kind, File: file, Line: line, In: qual(topFunc(b))}
	}
	if path, name, ok := pkgCall(f, e, c); ok {
		switch {
		case path == grpcStatus && (name == "Error" || name == "Errorf") && len(c.Args) >= 1:
			if n, ok := g.codeOf(f, e, c.Args[0], 0); ok {
				out.addCode(site(n, "status_error"))
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
						if id, ok := unparen(a).(*ast.Ident); ok && strings.Contains(strings.ToLower(id.Name), "err") {
							out.merge(g.evalErr(b, e, id, c.Pos(), depth, run))
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
}

var registerRE = regexp.MustCompile(`^Register(\w+)Server$`)
var gatewayRE = regexp.MustCompile(`^Register(\w+)Handler(Server|FromEndpoint|Client)?$`)

// descOf reads a grpc.ServiceDesc: `&Foo_ServiceDesc`, `Foo_ServiceDesc`, or a literal.
func (g *gx) descOf(f *File, e *Env, x ast.Expr) *grpcDesc {
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

// findGrpcRegistrations lists the server registrations of the wired functions.
func (g *gx) findGrpcRegistrations() ([]*grpcReg, []xExcluded) {
	var regs []*grpcReg
	var dropped []xExcluded
	for _, fn := range g.m.AllFuncs() {
		if fn.Lit != nil && fn.Parent != nil {
			// literals are walked as part of their parent's body below
		}
		e := g.m.EnvOf(fn)
		f := fn.File
		if strings.HasSuffix(f.Rel, "_grpc.pb.go") || strings.HasSuffix(f.Rel, ".pb.go") || strings.HasSuffix(f.Rel, ".pb.gw.go") {
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
					if d := g.descOf(f, e, call.Args[0]); d != nil {
						r = &grpcReg{desc: d, via: "RegisterService"}
					}
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
			if !g.isWired(topFunc(fn)) {
				name := r.via
				if r.desc != nil {
					name = r.desc.name
				}
				dropped = append(dropped, xExcluded{Kind: "unwired", What: "rpc_registration", Method: "RPC",
					Path: name, File: f.Rel, Line: g.line(call.Pos()), Reason: "not reachable from main"})
				return
			}
			r.impl, r.fn, r.e, r.call = call.Args[1], fn, e, call
			regs = append(regs, r)
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
	return uniq, dropped
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
	return nil
}

// isStub reports whether a method comes from a generated Unimplemented*Server.
func isStub(fn *Func) bool {
	return fn != nil && (strings.HasPrefix(fn.Recv, "Unimplemented") || strings.HasSuffix(fn.File.Rel, "_grpc.pb.go"))
}

// ---- interceptors ----------------------------------------------------------------------

// interceptorFuncs resolves the unary interceptors of every grpc.NewServer call.
func (g *gx) interceptorFuncs() []xInterceptor {
	var out []xInterceptor
	seen := map[*Func]bool{}
	for _, fn := range g.m.AllFuncs() {
		if !g.isWired(topFunc(fn)) {
			continue
		}
		e := g.m.EnvOf(fn)
		f := fn.File
		stmtWalk(fn.Body, func(call *ast.CallExpr, _ []ast.Node) {
			path, name, ok := pkgCall(f, e, call)
			if !ok || path != grpcPath || name != "NewServer" {
				return
			}
			for _, opt := range call.Args {
				oc, ok := unparen(opt).(*ast.CallExpr)
				if !ok {
					continue
				}
				opath, oname, ok := pkgCall(f, e, oc)
				if !ok || opath != grpcPath {
					continue
				}
				if oname != "UnaryInterceptor" && oname != "ChainUnaryInterceptor" {
					continue
				}
				for _, a := range oc.Args {
					ic := g.resolveInterceptor(e, a)
					if ic == nil {
						out = append(out, xInterceptor{Func: g.src(a), File: f.Rel, Line: g.line(a.Pos()),
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
		return x
	}
	ps := fn.Params()
	infoName, handlerName := "", ""
	if len(ps) >= 4 {
		infoName, handlerName = ps[2].name, ps[3].name
	}
	literal := true
	why := ""
	public := map[string]bool{}
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
				if !passes(s.Body) {
					// a condition on the method that does not pass the call through
					// (logging, metrics): not an allowlist
					return true
				}
				if !ok {
					literal, why = false, "the method test `"+g.src(s.Cond)+"` is not a literal set"
					return true
				}
				for _, m := range set {
					public[m] = true
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
						if str, ok := g.m.constStringDepth(b.File, be, c, 3); ok {
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
	if literal {
		x.Kind = "auth_literal"
	} else {
		x.Kind, x.Reason = "auth_not_literal", why
	}
	return x
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
				if str, ok := g.m.constStringDepth(f, e, r, 3); ok {
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
		k, ok := g.m.constStringDepth(lf, nil, kv.Key, 3)
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
	regs, unwired := g.findGrpcRegistrations()
	if len(u.files) == 0 && len(regs) == 0 && len(unwired) == 0 {
		return
	}
	gr := &xGrpc{Registrations: []xRegistration{}, Interceptors: []xInterceptor{}, Gateway: []xGateway{},
		StatusWrites: []*xStatusW{}, Notes: []string{}}
	gr.Protos, gr.Services, gr.Messages, gr.Enums = u.output()
	g.res.Excluded = append(g.res.Excluded, unwired...)

	ics := g.interceptorFuncs()
	gr.Interceptors = append(gr.Interceptors, ics...)
	g.seedCallerFuncs(ics)

	// registrations by service name
	byService := map[string]*grpcReg{}
	var order []string
	for _, r := range regs {
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

// fillRPC fills the closure-derived facts of one implemented rpc.
func (g *gx) fillRPC(r *xRPC, h *Func, ci *closureInfo, es *errSrc, run *grpcRun) {
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
			switch {
			case len(auth) == 1 && auth[0].Kind == "auth_literal":
				pub := false
				for _, m := range auth[0].Public {
					if m == full {
						pub = true
					}
				}
				if pub {
					r.Auth, r.AuthReason = "Public", fmt.Sprintf("%s lists %s as a method that skips authentication (%s:%d)", auth[0].Func, full, auth[0].File, auth[0].Line)
				} else {
					r.Auth, r.AuthReason = "Member", fmt.Sprintf("%s refuses callers without a valid token and does not list %s (%s:%d)", auth[0].Func, full, auth[0].File, auth[0].Line)
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
