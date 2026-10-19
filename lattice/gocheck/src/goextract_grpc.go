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
