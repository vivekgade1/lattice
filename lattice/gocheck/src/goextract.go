// goextract: the importer's Go (Gin + GORM) front end. `gocheck extract` reads the module
// under {"root"} (stdin, JSON) and answers lattice.goextract/1 (stdout, JSON): tables,
// routes (full path, handler, middleware, auth, request fields, raise sites, owner use),
// constructors, sentinels, the error writer, status writes and everything it did not
// read. It shares Load, Env and Infer with the checker and changes none of them (FuncRef
// recurses on `x := x`, so funcRef below carries a depth). Nothing is guessed: a construct the extractor cannot read is an `unread` item,
// and the output has no absolute path, time or host (determinism).
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/build"
	"go/printer"
	"go/token"
	"io"
	"os"
	"path/filepath"
	"runtime/debug"
	"sort"
	"strings"
)

const goextractSchema = "lattice.goextract/1"

type xLoc struct {
	File string `json:"file"`
	Line int    `json:"line"`
}

// xUnread is something the extractor did not read, and why.
type xUnread struct {
	File   string `json:"file"`
	Line   int    `json:"line"`
	What   string `json:"what"`
	Reason string `json:"reason"`
}

// xExcluded is something found and set apart: a framework handler, an unwired function,
// a file a build constraint drops.
type xExcluded struct {
	Kind   string `json:"kind"`
	What   string `json:"what"`
	Method string `json:"method,omitempty"`
	Path   string `json:"path,omitempty"`
	File   string `json:"file"`
	Line   int    `json:"line"`
	Reason string `json:"reason"`
}

type xResult struct {
	Schema       string       `json:"schema"`
	Module       string       `json:"module"`
	Go           string       `json:"go"`
	Dialect      string       `json:"dialect"`
	Main         []string     `json:"main"`
	Tables       []*xTable    `json:"tables"`
	Routes       []*xRoute    `json:"routes"`
	Middleware   []*xMw       `json:"middleware"`
	Constructors []*xCtor     `json:"constructors"`
	Sentinels    []*xSentinel `json:"sentinels"`
	ErrorWriter  *xWriter     `json:"error_writer"`
	Writers      []*xWriter   `json:"error_writers"`
	Auth         *xAuth       `json:"auth"`
	Models       []*xModel    `json:"models"`
	StatusWrites []*xStatusW  `json:"status_writes"`
	Excluded     []xExcluded  `json:"excluded"`
	Unread       []xUnread    `json:"unread"`
	Notes        []string     `json:"notes"`
	Grpc         *xGrpc       `json:"grpc,omitempty"` // Spike I: the gRPC contract and servers (omitted without any)
}

// gx is one extraction.
type gx struct {
	m   *Module
	ck  *checker
	res *xResult

	calls      map[*Func][]callSite
	structLits map[*TypeInfo][]litSite
	wired      map[*Func]bool
	hasMain    bool
	mainFns    []*Func

	unreadSeen map[string]bool
	unread     []xUnread

	tables    map[*TypeInfo]*xTable
	tableList []*xTable
	byTable   map[string]*xTable // SQL name -> table
	byStruct  map[string]*xTable // struct name -> table (first)

	ctorMemo   map[*Func]*ctorSum
	ctorBusy   map[*Func]bool
	sentinels  map[string]*xSentinel
	writers    map[*Func]*xWriter
	wrapWriter map[*Func]*Func // wrapper -> the function its literal hands the error to

	mws map[*Func]*xMw

	routeList   []*routeRec
	mainParent  map[*Func]*Func
	routerSteps int
	useIdx      *[]useCall
	accTypes    map[*TypeInfo]map[string]accMethod
	wrapMemo    map[*Func]int
	pendingFK   []pendingFK

	callerKeys  map[string]string // context keys the authenticating middleware sets: struct | scalar
	callerFuncs map[*Func]string  // module functions reading one of them
}

type litSite struct {
	lit *ast.CompositeLit
	fn  *Func
	e   *Env
}

func newGx(m *Module) *gx {
	return &gx{
		m: m, ck: &checker{m: m, pol: &Policy{HelperDepth: 3}},
		res:        &xResult{Schema: goextractSchema},
		structLits: map[*TypeInfo][]litSite{}, wired: map[*Func]bool{},
		unreadSeen: map[string]bool{}, tables: map[*TypeInfo]*xTable{},
		byTable: map[string]*xTable{}, byStruct: map[string]*xTable{},
		ctorMemo: map[*Func]*ctorSum{}, ctorBusy: map[*Func]bool{},
		sentinels: map[string]*xSentinel{}, writers: map[*Func]*xWriter{},
		wrapWriter: map[*Func]*Func{}, mws: map[*Func]*xMw{},
		callerKeys: map[string]string{}, callerFuncs: map[*Func]string{}, wrapMemo: map[*Func]int{},
	}
}

// runExtract is `gocheck extract`: exit 0 on success, 2 when the input cannot be read, 3
// when the extractor itself fails.
func runExtract(stdin io.Reader, stdout, stderr io.Writer) int {
	raw, err := io.ReadAll(stdin)
	if err != nil {
		fmt.Fprintln(stderr, "gocheck extract: cannot read the input:", err)
		return 2
	}
	var in struct {
		Root     string `json:"root"`
		Workflow bool   `json:"workflow"`
	}
	if err := json.Unmarshal(raw, &in); err != nil || in.Root == "" {
		fmt.Fprintln(stderr, `gocheck extract: the input must be JSON {"root": <directory>}`)
		return 2
	}
	if st, err := os.Stat(in.Root); err != nil || !st.IsDir() {
		fmt.Fprintln(stderr, "gocheck extract: the root is not a directory:", in.Root)
		return 2
	}
	var res any
	func() {
		defer func() {
			if r := recover(); r != nil {
				err = fmt.Errorf("internal error: %v\n%s", r, debug.Stack())
			}
		}()
		if in.Workflow { // the opt-in workflow facts (goextract_workflow.go)
			res, err = extractWorkflow(in.Root)
		} else {
			res, err = extractModule(in.Root)
		}
	}()
	if err != nil {
		fmt.Fprintln(stderr, "gocheck extract:", err)
		return 3
	}
	enc := json.NewEncoder(stdout)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(res); err != nil {
		fmt.Fprintln(stderr, "gocheck extract:", err)
		return 3
	}
	return 0
}

// extractModule reads the module under root.
func extractModule(root string) (*xResult, error) {
	m, dropped, err := loadForExtract(root)
	if err != nil {
		return nil, err
	}
	g := newGx(m)
	for _, d := range dropped {
		g.res.Excluded = append(g.res.Excluded, xExcluded{
			Kind: "file", What: "file", File: d, Line: 1,
			Reason: "dropped by a build constraint (GOOS=linux, GOARCH=amd64, no cgo)"})
	}
	for _, f := range m.Errors {
		g.addUnread(f.File, f.Line, "file", f.Message)
	}
	g.readGoMod(root)
	m.AllFuncs() // numbers every function literal in source order, once
	g.computeWiring()
	g.indexCalls()
	g.indexStructLits()
	g.findSentinels()
	g.findTables()
	g.findRoutes()
	g.findGrpc()
	g.finish()
	return g.res, nil
}

// loadForExtract is Load with the files a build constraint drops left out (Load itself
// is unchanged: gocheck's findings must not move).
func loadForExtract(root string) (*Module, []string, error) {
	m, err := Load(root)
	if err != nil {
		return nil, nil, err
	}
	ctx := build.Default
	ctx.GOOS, ctx.GOARCH, ctx.CgoEnabled = "linux", "amd64", false
	var dropped []string
	drop := map[*File]bool{}
	for _, pkg := range m.Pkgs {
		for _, f := range pkg.Files {
			ok, err := ctx.MatchFile(filepath.Dir(f.Path), filepath.Base(f.Path))
			if err == nil && !ok {
				drop[f] = true
				dropped = append(dropped, f.Rel)
			}
		}
	}
	if len(drop) == 0 {
		return m, nil, nil
	}
	m.Funcs, m.methods = nil, map[string][]*Func{}
	for _, pkg := range m.Pkgs {
		var keep []*File
		for _, f := range pkg.Files {
			if !drop[f] {
				keep = append(keep, f)
			}
		}
		pkg.Files = keep
		pkg.Funcs, pkg.Methods = map[string]*Func{}, map[string]map[string]*Func{}
		pkg.Types, pkg.Consts, pkg.Vars = map[string]*TypeInfo{}, map[string]constInfo{}, map[string]varInfo{}
		for _, f := range keep {
			m.declare(f)
		}
	}
	for _, fn := range m.Funcs {
		if fn.isMethod {
			m.methods[fn.Short] = append(m.methods[fn.Short], fn)
		}
	}
	sort.Strings(dropped)
	return m, dropped, nil
}

// readGoMod answers the go line and the database dialect from go.mod.
func (g *gx) readGoMod(root string) {
	g.res.Module = g.m.Path
	raw, err := os.ReadFile(filepath.Join(root, "go.mod"))
	if err != nil {
		g.res.Notes = append(g.res.Notes, "no go.mod: the module path and Go version are unknown")
		return
	}
	driver := func(path string) string {
		p := strings.ToLower(path)
		switch {
		case strings.HasPrefix(p, "gorm.io/driver/mysql"), strings.Contains(p, "go-sql-driver/mysql"):
			return "mysql"
		case strings.HasPrefix(p, "gorm.io/driver/postgres"), strings.Contains(p, "jackc/pgx"),
			strings.Contains(p, "lib/pq"):
			return "postgres"
		case strings.HasPrefix(p, "gorm.io/driver/sqlite"), strings.Contains(p, "sqlite"):
			return "sqlite"
		}
		return ""
	}
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "go ") && g.res.Go == "" {
			g.res.Go = strings.TrimSpace(strings.TrimPrefix(line, "go "))
		}
		f := strings.Fields(strings.TrimPrefix(line, "require "))
		if len(f) > 0 && g.res.Dialect == "" {
			g.res.Dialect = driver(f[0])
		}
	}
}

// ---- small shared helpers ---------------------------------------------------------------

func (g *gx) line(p token.Pos) int { return g.m.Line(p) }

func (g *gx) addUnread(file string, line int, what, reason string) {
	key := fmt.Sprintf("%s|%d|%s|%s", file, line, what, reason)
	if g.unreadSeen[key] {
		return
	}
	g.unreadSeen[key] = true
	g.unread = append(g.unread, xUnread{File: file, Line: line, What: what, Reason: reason})
}

func (g *gx) unreadAt(f *File, p token.Pos, what, reason string) {
	g.addUnread(f.Rel, g.line(p), what, reason)
}

// src prints a node on one line (a condition, a bound), bounded in length.
func (g *gx) src(n ast.Node) string {
	var b strings.Builder
	if err := printer.Fprint(&b, g.m.Fset, n); err != nil {
		return ""
	}
	s := strings.Join(strings.Fields(b.String()), " ")
	if len(s) > 160 {
		s = s[:157] + "..."
	}
	return s
}

// qual answers a function's qualified name: `httpapi.Handle`, `habits.handler.create`,
// `NewRouter.func4` under its package.
func qual(fn *Func) string {
	if fn == nil {
		return ""
	}
	return fn.Pkg().Name + "." + fn.Name
}

func funcImport(fn *Func) string {
	p := fn.Pkg()
	if p.ImportPath != "" {
		return p.ImportPath
	}
	return p.Rel
}

// funcKey is a sortable, unique identity of a function.
func (g *gx) funcKey(fn *Func) string {
	return fmt.Sprintf("%s:%06d:%s", fn.File.Rel, g.line(fn.Pos), fn.Name)
}

// topFunc answers the declared function a literal sits in.
func topFunc(fn *Func) *Func {
	for fn.Parent != nil {
		fn = fn.Parent
	}
	return fn
}

// envOf answers the environment that holds `name` (the function that declares it).
func defEnv(e *Env, name string) *Env {
	for x := e; x != nil; x = x.parent {
		if _, ok := x.vars[name]; ok {
			return x
		}
	}
	return nil
}

// stmtWalk visits every call expression of root outside nested function literals with
// its stack of enclosing nodes (outermost first, the call last).
func stmtWalk(root ast.Node, visit func(call *ast.CallExpr, stack []ast.Node)) {
	var stack []ast.Node
	ast.Inspect(root, func(n ast.Node) bool {
		if n == nil {
			stack = stack[:len(stack)-1]
			return true
		}
		if _, ok := n.(*ast.FuncLit); ok && n != root {
			return false
		}
		stack = append(stack, n)
		if c, ok := n.(*ast.CallExpr); ok {
			visit(c, stack)
		}
		return true
	})
}

// conditionOf answers the source of the conditions a stack sits under (innermost last,
// joined by &&); a registration under an `if` keeps its condition.
func (g *gx) conditionOf(stack []ast.Node) string {
	var parts []string
	for i, n := range stack {
		if i+1 >= len(stack) {
			break
		}
		child := stack[i+1]
		switch s := n.(type) {
		case *ast.IfStmt:
			if child == ast.Node(s.Body) {
				parts = append(parts, g.src(s.Cond))
			} else if s.Else != nil && child == ast.Node(s.Else) {
				parts = append(parts, "!("+g.src(s.Cond)+")")
			}
		}
	}
	return strings.Join(parts, " && ")
}

// ---- wiring, call and literal indexes ---------------------------------------------------

// computeWiring marks the functions reachable from a main (by calls, by function values
// named in a reachable function, and the literals inside), plus every init of a package
// a main imports.
func (g *gx) computeWiring() {
	m := g.m
	var roots []*Func
	mainPkgs := map[*Pkg]bool{}
	for _, p := range m.Pkgs {
		if p.Name == "main" {
			if fn := p.Funcs["main"]; fn != nil {
				roots = append(roots, fn)
				mainPkgs[p] = true
				g.mainFns = append(g.mainFns, fn)
			}
		}
	}
	g.hasMain = len(roots) > 0
	if !g.hasMain {
		g.res.Notes = append(g.res.Notes, "no main package: every function counts as wired")
		return
	}
	for _, fn := range g.mainFns {
		g.res.Main = append(g.res.Main, fn.File.Rel)
	}
	sort.Strings(g.res.Main)
	inClosure := map[*Pkg]bool{}
	var visitPkg func(p *Pkg)
	visitPkg = func(p *Pkg) {
		if inClosure[p] {
			return
		}
		inClosure[p] = true
		for _, f := range p.Files {
			for _, path := range f.Imports {
				if q := m.ByImport[path]; q != nil {
					visitPkg(q)
				}
			}
		}
	}
	for p := range mainPkgs {
		visitPkg(p)
	}
	for _, fn := range m.Funcs {
		if !fn.isMethod && fn.Short == "init" && inClosure[fn.Pkg()] {
			roots = append(roots, fn)
		}
	}
	queue := append([]*Func{}, roots...)
	add := func(fn *Func) {
		if fn != nil && !g.wired[fn] {
			g.wired[fn] = true
			queue = append(queue, fn)
		}
	}
	for _, r := range roots {
		g.wired[r] = true
	}
	for len(queue) > 0 {
		fn := queue[0]
		queue = queue[1:]
		e := m.EnvOf(fn)
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			switch v := n.(type) {
			case *ast.FuncLit:
				add(m.LitFunc(v, fn))
				return false
			case *ast.CallExpr:
				if callee := m.Callee(e, v); callee != nil {
					add(callee)
				}
			case *ast.Ident:
				if f := g.funcRef(e, v, 0); f != nil && f.Lit == nil {
					add(f)
				}
			case *ast.SelectorExpr:
				if f := g.funcRef(e, v, 0); f != nil && f.Lit == nil {
					add(f)
				}
			}
			return true
		})
	}
}

// isWired reports whether fn runs from a main (every function does without one).
func (g *gx) isWired(fn *Func) bool { return !g.hasMain || g.wired[fn] }

func (g *gx) indexCalls() { g.calls = g.ck.callIndex() }

func (g *gx) indexStructLits() {
	for _, fn := range g.m.AllFuncs() {
		e := g.m.EnvOf(fn)
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			if cl, ok := n.(*ast.CompositeLit); ok && cl.Type != nil {
				if ti := g.m.Infer(e, cl).deref().rowTypeAny(); ti != nil {
					g.structLits[ti] = append(g.structLits[ti], litSite{cl, fn, e})
				}
			}
			return true
		})
	}
}

// ---- the end ----------------------------------------------------------------------------

func (g *gx) finish() {
	sort.SliceStable(g.unread, func(i, j int) bool {
		a, b := g.unread[i], g.unread[j]
		if a.File != b.File {
			return a.File < b.File
		}
		if a.Line != b.Line {
			return a.Line < b.Line
		}
		if a.What != b.What {
			return a.What < b.What
		}
		return a.Reason < b.Reason
	})
	g.res.Unread = g.unread
	if g.res.Unread == nil {
		g.res.Unread = []xUnread{}
	}
	sort.SliceStable(g.res.Excluded, func(i, j int) bool {
		a, b := g.res.Excluded[i], g.res.Excluded[j]
		if a.File != b.File {
			return a.File < b.File
		}
		if a.Line != b.Line {
			return a.Line < b.Line
		}
		return a.Kind+a.What < b.Kind+b.What
	})
	if g.res.Excluded == nil {
		g.res.Excluded = []xExcluded{}
	}
	if g.res.Notes == nil {
		g.res.Notes = []string{}
	}
	if g.res.Main == nil {
		g.res.Main = []string{}
	}
}

// funcRef is Module.FuncRef with a depth bound: FuncRef follows a local's only value
// without limit, so `tt := tt` (or two locals naming each other) never returns.
func (g *gx) funcRef(e *Env, x ast.Expr, depth int) *Func {
	m := g.m
	if depth > 8 {
		return nil
	}
	if id, ok := unparen(x).(*ast.Ident); ok {
		if _, local := e.lookup(id.Name); local {
			if lit := e.lit(id.Name); lit != nil {
				return m.LitFunc(lit, m.litParent(e, id.Name))
			}
			if vals := e.valuesOf(id.Name); len(vals) == 1 {
				return g.funcRef(e, vals[0], depth+1)
			}
			return nil
		}
		return e.fn.File.Pkg.Funcs[id.Name]
	}
	return m.FuncRef(e, x)
}
