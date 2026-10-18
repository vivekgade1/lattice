// goextract: what a request runs. A closure is a handler (or middleware) and the module
// functions it calls (depth 4, parameters bound to the constants call sites pass); the
// raise sites (D6), request fields (D7) and owner evidence (D8) are read from it.
package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

const closureDepth = 4

type xRaise struct {
	Status   *int    `json:"status"`
	Code     *string `json:"code"`
	Line     int     `json:"line"`
	File     string  `json:"file"`
	Kind     string  `json:"kind"`
	In       string  `json:"in"`
	Sentinel string  `json:"sentinel,omitempty"`
	Bind     bool    `json:"bind,omitempty"` // opt-in: the refusal of a failed c.ShouldBind*
}

type xParam struct {
	Name       string   `json:"name"`
	Annotation string   `json:"annotation"`
	Type       *string  `json:"type"`
	Source     string   `json:"source"`
	Required   bool     `json:"required"`
	Default    *string  `json:"default"`
	Ge         *int     `json:"ge"`
	Le         *int     `json:"le"`
	MinLength  *int     `json:"min_length"`
	MaxLength  *int     `json:"max_length"`
	Choices    []string `json:"choices"`
	File       string   `json:"file"`
	Line       int      `json:"line"`
	Parse      string   `json:"parse,omitempty"` // opt-in: "Int" when the handler parses the path value
	ParseAt    string   `json:"parse_at,omitempty"`
}

type xOwner struct {
	Table  string `json:"table"`
	Model  string `json:"model"`
	Column string `json:"column"`
	How    string `json:"how"`
	Line   int    `json:"line"`
	File   string `json:"file"`
}

type xModel struct {
	Name    string   `json:"name"`
	Package string   `json:"package"`
	File    string   `json:"file"`
	Line    int      `json:"line"`
	Fields  []xParam `json:"fields"`
	ti      *TypeInfo
}

type entryPos struct {
	file string
	line int
}

type gxVisit struct {
	fn         *Func
	binds      binds
	entry      *entryPos
	depth      int
	bodies     []*Func
	callerVals map[string]bool
	callerIDs  map[string]bool
	retNames   map[string]bool
}

type setCall struct {
	key    string
	scalar bool
}

type closureInfo struct {
	root     *Func
	visits   []*gxVisit
	funcs    map[*Func]bool
	raises   []xRaise
	rseen    map[string]bool
	params   map[string]*xParam
	porder   []string
	owners   []xOwner
	oseen    map[string]bool
	tables   map[string]bool
	tx       bool
	calls    map[string]bool
	ok2xx    map[int]bool
	unread   []xUnread
	models   []*xModel
	mseen    map[*TypeInfo]bool
	sets     []setCall
	usersTbl string
	bearer   bool
	authHdr  bool
	bcrypt   *bcryptUse
	jsonTok  string
	encoding string
	admin    bool
	parsed   map[string]entryPos // path parameters parsed to an integer and refused 4xx, where (opt-in)
}

type bcryptUse struct {
	table  string
	column string
	line   int
}

func newClosure(root *Func) *closureInfo {
	return &closureInfo{root: root, funcs: map[*Func]bool{}, rseen: map[string]bool{},
		params: map[string]*xParam{}, oseen: map[string]bool{}, tables: map[string]bool{},
		calls: map[string]bool{}, ok2xx: map[int]bool{}, mseen: map[*TypeInfo]bool{}}
}

func (ci *closureInfo) addUnread(f string, line int, what, reason string) {
	for _, u := range ci.unread {
		if u.File == f && u.Line == line && u.What == what && u.Reason == reason {
			return
		}
	}
	ci.unread = append(ci.unread, xUnread{File: f, Line: line, What: what, Reason: reason})
}

// bodies answers fn and every function literal inside it, in source order.
func (g *gx) bodies(fn *Func) []*Func {
	out := []*Func{fn}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if lit, ok := n.(*ast.FuncLit); ok {
			out = append(out, g.bodies(g.m.LitFunc(lit, fn))...)
			return false
		}
		return true
	})
	return out
}

// analyze reads the closure of root.
func (g *gx) analyze(root *Func) *closureInfo {
	ci := newClosure(root)
	seen := map[string]bool{}
	var walk func(fn *Func, b binds, vals, ids map[string]bool, entry *entryPos, depth int)
	walk = func(fn *Func, b binds, vals, ids map[string]bool, entry *entryPos, depth int) {
		key := g.funcKey(fn) + "|" + b.sig() + "|v:" + joinSet(vals) + "|i:" + joinSet(ids)
		if seen[key] {
			return
		}
		seen[key] = true
		v := &gxVisit{fn: fn, binds: b, entry: entry, depth: depth, callerVals: vals, callerIDs: ids,
			retNames: map[string]bool{}}
		ci.visits = append(ci.visits, v)
		ci.funcs[topFunc(fn)] = true
		g.scanVisit(ci, v)
		if depth >= closureDepth {
			return
		}
		for _, body := range v.bodies {
			e := g.m.EnvOf(body)
			stmtWalk(body.Body, func(call *ast.CallExpr, _ []ast.Node) {
				callee := g.m.Callee(e, call)
				if callee == nil {
					if why := g.unreadCall(e, call); why != "" {
						ci.addUnread(body.File.Rel, g.line(call.Pos()), "call", why)
						g.unreadAt(body.File, call.Pos(), "call", why)
					}
					return
				}
				if callee.Lit != nil || callee == fn {
					return
				}
				if g.ctorOf(callee) != nil || g.isAccessorMethod(callee) {
					return
				}
				nb := binds{}
				nv, ni := map[string]bool{}, map[string]bool{}
				for i, p := range callee.Params() {
					if i >= len(call.Args) || p.name == "" {
						continue
					}
					if c, ok := g.evalConst(e, call.Args[i], v.binds); ok {
						nb[p.name] = c
					}
					if g.isCallerValue(e, v, call.Args[i]) {
						nv[p.name] = true
					}
					if g.isCallerID(e, v, call.Args[i]) {
						ni[p.name] = true
					}
				}
				ent := entry
				if ent == nil {
					ent = &entryPos{body.File.Rel, g.line(call.Pos())}
				}
				ci.calls[funcImport(callee)+":"+callee.Name] = true
				walk(callee, nb, nv, ni, ent, depth+1)
			})
		}
	}
	walk(root, binds{}, map[string]bool{}, map[string]bool{}, nil, 0)
	if g.policy {
		g.policyFacts(ci)
	}
	return ci
}

// policyFacts (opt-in): `n, err := strconv.Atoi(c.Param("p"))` whose error branch refuses with a
// constant 4xx, and the refusals of a failed c.ShouldBind*. Any other shape adds no key.
func (g *gx) policyFacts(ci *closureInfo) {
	ci.parsed = map[string]entryPos{}
	for _, b := range ci.visits[0].bodies {
		e := g.m.EnvOf(b)
		ast.Inspect(b.Body, func(n ast.Node) bool {
			switch s := n.(type) {
			case *ast.FuncLit:
				return false
			case *ast.IfStmt:
				if a, ok := s.Init.(*ast.AssignStmt); ok {
					g.branchFact(ci, e, b, a, s)
				}
			case *ast.BlockStmt:
				for i := 0; i+1 < len(s.List); i++ {
					a, ok := s.List[i].(*ast.AssignStmt)
					if ifs, ok2 := s.List[i+1].(*ast.IfStmt); ok && ok2 && ifs.Init == nil {
						g.branchFact(ci, e, b, a, ifs)
					}
				}
			}
			return true
		})
	}
}

func (g *gx) branchFact(ci *closureInfo, e *Env, b *Func, as *ast.AssignStmt, ifs *ast.IfStmt) {
	last, _ := as.Lhs[len(as.Lhs)-1].(*ast.Ident)
	if len(as.Rhs) != 1 || last == nil || last.Name == "_" || g.src(ifs.Cond) != last.Name+" != nil" {
		return
	}
	call, _ := unparen(as.Rhs[0]).(*ast.CallExpr)
	if call == nil {
		return
	}
	sel, ok := unparen(call.Fun).(*ast.SelectorExpr)
	if !ok {
		return
	}
	var in []int
	all4xx := true
	for i, r := range ci.raises {
		if r.File == b.File.Rel && r.Line >= g.line(ifs.Body.Pos()) && r.Line <= g.line(ifs.Body.End()) {
			in = append(in, i)
			all4xx = all4xx && r.Status != nil && *r.Status >= 400 && *r.Status < 500
		}
	}
	if bindMethods[sel.Sel.Name] && g.m.isCtx(e, sel.X) {
		for _, i := range in {
			ci.raises[i].Bind = true
		}
	} else if path, _ := g.m.importOf(e, sel.X); path == "strconv" && parseFns[sel.Sel.Name] && len(in) > 0 && all4xx && len(as.Lhs) == 2 {
		if name := g.paramName(e, call.Args[0]); name != "" {
			ci.parsed[name] = entryPos{b.File.Rel, g.line(call.Pos())}
		}
	}
}

var parseFns = map[string]bool{"Atoi": true, "ParseInt": true, "ParseUint": true}

// paramName answers p when x is c.Param("p"), or a local assigned once from it.
func (g *gx) paramName(e *Env, x ast.Expr) string {
	if id, ok := unparen(x).(*ast.Ident); ok {
		if de := defEnv(e, id.Name); de != nil && len(de.values[id.Name]) == 1 {
			e, x = de, de.values[id.Name][0]
		}
	}
	if call, ok := unparen(x).(*ast.CallExpr); ok && len(call.Args) == 1 {
		if sel, ok := unparen(call.Fun).(*ast.SelectorExpr); ok && sel.Sel.Name == "Param" && g.m.isCtx(e, sel.X) {
			name, _ := g.m.constString(e, call.Args[0])
			return name
		}
	}
	return ""
}

func (g *gx) isAccessorMethod(fn *Func) bool {
	if !fn.isMethod {
		return false
	}
	ti := fn.Pkg().Types[fn.Recv]
	return ti != nil && g.accessorTypes()[ti] != nil
}

func (g *gx) at(v *gxVisit, b *Func, pos token.Pos) (string, int) {
	if v.entry != nil {
		return v.entry.file, v.entry.line
	}
	return b.File.Rel, g.line(pos)
}

func (g *gx) scanVisit(ci *closureInfo, v *gxVisit) {
	m := g.m
	v.bodies = g.bodies(v.fn)
	for _, b := range v.bodies {
		ast.Inspect(b.Body, func(n ast.Node) bool {
			if r, ok := n.(*ast.ReturnStmt); ok {
				for _, res := range r.Results {
					if id, ok := unparen(res).(*ast.Ident); ok {
						v.retNames[id.Name] = true
					}
				}
			}
			return true
		})
	}
	g.computeCallerNames(v)
	for _, b := range v.bodies {
		e := m.EnvOf(b)
		g.scanRaises(ci, v, b, e)
		g.scanParams(ci, v, b, e)
		g.scanOwner(ci, v, b, e)
		g.scanMisc(ci, v, b, e)
	}
}

// ---- raise sites (D6) -------------------------------------------------------------------

func (g *gx) addRaise(ci *closureInfo, v *gxVisit, b *Func, pos token.Pos, status int, code string, codeOK bool, kind, sentinel string) {
	file, line := g.at(v, b, pos)
	if kind != "handler" && v.entry == nil {
		file, line = b.File.Rel, g.line(pos)
	}
	key := fmt.Sprintf("%s|%d|%d|%s|%s|%s", file, line, status, code, kind, sentinel)
	if ci.rseen[key] {
		return
	}
	ci.rseen[key] = true
	st := status
	r := xRaise{Status: &st, Line: line, File: file, Kind: kind, In: qual(v.fn), Sentinel: sentinel}
	if codeOK {
		c := code
		r.Code = &c
	}
	ci.raises = append(ci.raises, r)
}

// statusSet answers the constant statuses an expression can hold: a constant, or a local
// whose every assignment is one.
func (g *gx) statusSet(e *Env, x ast.Expr, b binds) ([]int, bool) {
	if c, ok := g.evalConst(e, x, b); ok && c.isInt {
		return []int{c.n}, true
	}
	id, ok := unparen(x).(*ast.Ident)
	if !ok {
		return nil, false
	}
	de := defEnv(e, id.Name)
	if de == nil {
		return nil, false
	}
	vals := de.values[id.Name]
	if len(vals) == 0 {
		return nil, false
	}
	var out []int
	seen := map[int]bool{}
	for _, val := range vals {
		c, ok := g.evalConst(de, val, b)
		if !ok || !c.isInt {
			return nil, false
		}
		if !seen[c.n] {
			seen[c.n] = true
			out = append(out, c.n)
		}
	}
	sort.Ints(out)
	return out, true
}

// codeOfArgs reads a literal error code out of a response's arguments: a gin.H / map
// literal's code/error/message key, a struct literal's Code field.
func (g *gx) codeOfArgs(e *Env, args []ast.Expr, b binds) (string, bool) {
	for _, a := range args {
		code, ok := "", false
		ast.Inspect(a, func(n ast.Node) bool {
			if ok {
				return false
			}
			cl, isLit := n.(*ast.CompositeLit)
			if !isLit {
				return true
			}
			prefer := map[string]int{"code": 5, "Code": 5, "detail": 4, "Detail": 4, "error": 3, "Error": 3,
				"message": 2, "Message": 2, "msg": 1}
			best := 0
			for _, el := range cl.Elts {
				kv, isKV := el.(*ast.KeyValueExpr)
				if !isKV {
					continue
				}
				var k string
				switch kk := kv.Key.(type) {
				case *ast.Ident:
					k = kk.Name
				case *ast.BasicLit:
					k, _ = stringLit(kk)
				}
				if p := prefer[k]; p > best {
					if c, isC := g.evalConst(e, kv.Value, b); isC && c.isStr {
						best, code = p, c.s
					}
				}
			}
			if best > 0 {
				ok = true
			}
			return true
		})
		if ok {
			return code, true
		}
	}
	return "", false
}

func (g *gx) scanRaises(ci *closureInfo, v *gxVisit, b *Func, e *Env) {
	m := g.m
	stmtWalk(b.Body, func(call *ast.CallExpr, stack []ast.Node) {
		if callee := m.Callee(e, call); callee != nil && callee.Lit == nil {
			if sum := g.ctorOf(callee); sum != nil {
				h := g.ctorCall(e, call, sum, v.binds)
				if !h.statusOK {
					g.unreadAt(b.File, call.Pos(), "raise", "status of `"+g.src(call)+"` is not a constant")
					ci.addUnread(b.File.Rel, g.line(call.Pos()), "raise", "status of `"+g.src(call)+"` is not a constant")
					return
				}
				if !g.returned(stack, v) {
					ci.addUnread(b.File.Rel, g.line(call.Pos()), "raise", "constructor value `"+g.src(call)+"` is not returned")
					return
				}
				if h.status >= 400 {
					g.addRaise(ci, v, b, call.Pos(), h.status, h.code, h.codeOK, "return", "")
				}
				return
			}
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if !ok || !refusalMethods[sel.Sel.Name] || len(call.Args) < 1 || !m.isCtx(e, sel.X) {
			return
		}
		if sel.Sel.Name == "AbortWithError" || sel.Sel.Name == "Render" && false {
			return
		}
		sts, ok := g.statusSet(e, call.Args[0], v.binds)
		if !ok {
			ci.addUnread(b.File.Rel, g.line(call.Pos()), "raise", "status of `"+g.src(call)+"` is not a constant")
			g.unreadAt(b.File, call.Pos(), "raise", "status of `"+g.src(call)+"` is not a constant")
			return
		}
		code, codeOK := g.codeOfArgs(e, call.Args[1:], v.binds)
		if !codeOK {
			code, codeOK = g.codeInBody(e, b, v.binds)
		}
		for _, s := range sts {
			switch {
			case s >= 400:
				g.addRaise(ci, v, b, call.Pos(), s, code, codeOK, "abort", "")
			case s >= 200 && s < 300:
				ci.ok2xx[s] = true
			}
		}
	})
	// a struct literal carrying a status, returned
	stmtLits(b.Body, func(lit *ast.CompositeLit, ret bool) {
		if h, ok := g.ctorLit(e, lit, v.binds); ok && ret {
			if h.statusOK && h.status >= 400 {
				g.addRaise(ci, v, b, lit.Pos(), h.status, h.code, h.codeOK, "return", "")
			}
		}
	})
	// a sentinel the error writer maps
	ast.Inspect(b.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok && n != ast.Node(b.Lit) {
			return false
		}
		r, ok := n.(*ast.ReturnStmt)
		if !ok {
			return true
		}
		for _, res := range r.Results {
			ast.Inspect(res, func(x ast.Node) bool {
				ex, ok := x.(ast.Expr)
				if !ok {
					return true
				}
				switch ex.(type) {
				case *ast.Ident, *ast.SelectorExpr:
					if key := g.sentinelKey(e, ex); key != "" {
						if mp, w, ok := g.writerTarget(key); ok {
							_ = w
							code := ""
							codeOK := mp.Code != nil
							if codeOK {
								code = *mp.Code
							}
							g.addRaise(ci, v, b, r.Pos(), mp.Status, code, codeOK, "handler", key)
						}
					}
				}
				return true
			})
		}
		return true
	})
}

// codeInBody reads a `Code: x` literal of a forwarding function (httpapi.Fail).
func (g *gx) codeInBody(e *Env, b *Func, bd binds) (string, bool) {
	code, ok := "", false
	ast.Inspect(b.Body, func(n ast.Node) bool {
		if ok {
			return false
		}
		if _, isLit := n.(*ast.FuncLit); isLit {
			return false
		}
		if kv, isKV := n.(*ast.KeyValueExpr); isKV {
			if k, isID := kv.Key.(*ast.Ident); isID && (k.Name == "Code") {
				if c, isC := g.evalConst(e, kv.Value, bd); isC && c.isStr {
					code, ok = c.s, true
				}
			}
		}
		return true
	})
	return code, ok
}

// returned reports whether a constructor call (the last of the stack) is returned, its
// value is returned through a local, or it is handed to another call.
func (g *gx) returned(stack []ast.Node, v *gxVisit) bool {
	for i := len(stack) - 2; i >= 0; i-- {
		switch p := stack[i].(type) {
		case *ast.ParenExpr, *ast.UnaryExpr:
			continue
		case *ast.ReturnStmt:
			return true
		case *ast.CallExpr:
			return true
		case *ast.AssignStmt:
			for _, l := range p.Lhs {
				if id, ok := l.(*ast.Ident); ok && v.retNames[id.Name] {
					return true
				}
			}
			return false
		case *ast.ValueSpec:
			for _, n := range p.Names {
				if v.retNames[n.Name] {
					return true
				}
			}
			return false
		default:
			return false
		}
	}
	return false
}

// stmtLits visits the composite literals of root outside function literals; ret is true
// for one that is (or is the address of) a return value.
func stmtLits(root ast.Node, f func(lit *ast.CompositeLit, ret bool)) {
	ast.Inspect(root, func(n ast.Node) bool {
		switch v := n.(type) {
		case *ast.FuncLit:
			return n == root
		case *ast.ReturnStmt:
			for _, r := range v.Results {
				x := unparen(r)
				if u, ok := x.(*ast.UnaryExpr); ok && u.Op == token.AND {
					x = unparen(u.X)
				}
				if cl, ok := x.(*ast.CompositeLit); ok {
					f(cl, true)
				}
			}
		}
		return true
	})
}

// ---- request fields (D7) ----------------------------------------------------------------

type accMethod struct {
	shape string // v-ok-err, v-ok, v-err, v, bool
	typ   *string
	annot string
}

// accessorTypes answers the module's map types with key accessors (Body's Str, Int...).
func (g *gx) accessorTypes() map[*TypeInfo]map[string]accMethod {
	if g.accTypes != nil {
		return g.accTypes
	}
	g.accTypes = map[*TypeInfo]map[string]accMethod{}
	for _, pkg := range g.m.Pkgs {
		for tn, ti := range pkg.Types {
			if _, isMap := ti.Under.(*ast.MapType); !isMap {
				continue
			}
			ms := pkg.Methods[tn]
			for name, fn := range ms {
				ps := fn.Params()
				if len(ps) < 1 {
					continue
				}
				if t := g.m.TypeExpr(fn.File, ps[0].typ); t.Kind != "basic" || t.Basic != "string" {
					continue
				}
				rs := fn.Results()
				var am accMethod
				isBool := func(i int) bool {
					t := g.m.TypeExpr(fn.File, rs[i].typ)
					return t.Kind == "basic" && t.Basic == "bool"
				}
				isErr := func(i int) bool { return g.m.TypeExpr(fn.File, rs[i].typ).Kind == "error" }
				switch len(rs) {
				case 1:
					if isBool(0) {
						am.shape = "bool"
					} else {
						am.shape = "v"
					}
				case 2:
					switch {
					case isBool(1):
						am.shape = "v-ok"
					case isErr(1):
						am.shape = "v-err"
					}
				case 3:
					if isBool(1) && isErr(2) {
						am.shape = "v-ok-err"
					}
				}
				if am.shape == "" {
					continue
				}
				if am.shape != "bool" {
					am.typ = g.latticeType(fn.File, rs[0].typ, nil)
					am.annot = g.src(rs[0].typ)
				}
				if g.accTypes[ti] == nil {
					g.accTypes[ti] = map[string]accMethod{}
				}
				g.accTypes[ti][name] = am
			}
		}
	}
	return g.accTypes
}

type accSpec struct {
	keys   []string
	source string
	am     accMethod
	pos    token.Pos
}

// keysOf reads an accessor's key argument: a constant, a bound parameter, or the
// variable of a range over a literal of constants.
func (g *gx) keysOf(e *Env, x ast.Expr, b binds, rk map[string][]string) []string {
	if c, ok := g.evalConst(e, x, b); ok && c.isStr {
		return []string{c.s}
	}
	if id, ok := unparen(x).(*ast.Ident); ok {
		if ks, ok := rk[id.Name]; ok {
			return ks
		}
	}
	return nil
}

func (g *gx) accessorOf(e *Env, call *ast.CallExpr, b binds, rk map[string][]string, report func(string)) *accSpec {
	m := g.m
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || len(call.Args) < 1 {
		return nil
	}
	if m.isCtx(e, sel.X) {
		var am accMethod
		src := "query"
		switch sel.Sel.Name {
		case "Query", "DefaultQuery":
			am = accMethod{shape: "v"}
		case "GetQuery":
			am = accMethod{shape: "v-ok"}
		case "PostForm", "DefaultPostForm":
			am, src = accMethod{shape: "v"}, "form"
		case "GetPostForm":
			am, src = accMethod{shape: "v-ok"}, "form"
		default:
			return nil
		}
		am.typ, am.annot = strp("Str"), "string"
		keys := g.keysOf(e, call.Args[0], b, rk)
		if len(keys) == 0 {
			report("the key of `" + g.src(call) + "` is not a constant")
			return nil
		}
		return &accSpec{keys: keys, source: src, am: am, pos: call.Pos()}
	}
	t := m.Infer(e, sel.X).deref()
	if t.Kind != "named" {
		return nil
	}
	methods := g.accessorTypes()[t.Named]
	am, ok := methods[sel.Sel.Name]
	if !ok {
		return nil
	}
	keys := g.keysOf(e, call.Args[0], b, rk)
	if len(keys) == 0 {
		report("the key of `" + g.src(call) + "` is not a constant")
		return nil
	}
	return &accSpec{keys: keys, source: "body", am: am, pos: call.Pos()}
}

func (g *gx) param(ci *closureInfo, name, source string, f *File, pos token.Pos) *xParam {
	key := source + "|" + name
	p := ci.params[key]
	if p == nil {
		p = &xParam{Name: name, Source: source, File: f.Rel, Line: g.line(pos), Choices: []string{}}
		ci.params[key] = p
		ci.porder = append(ci.porder, key)
	}
	return p
}

func (g *gx) scanParams(ci *closureInfo, v *gxVisit, b *Func, e *Env) {
	m := g.m
	report := func(pos token.Pos) func(string) {
		return func(reason string) {
			ci.addUnread(b.File.Rel, g.line(pos), "field", reason)
			g.unreadAt(b.File, pos, "field", reason)
		}
	}
	// every accessor call registers its key
	stmtWalk(b.Body, func(call *ast.CallExpr, stack []ast.Node) {
		rk := g.rangeKeys(stack)
		if acc := g.accessorOf(e, call, v.binds, rk, report(call.Pos())); acc != nil {
			for _, k := range acc.keys {
				p := g.param(ci, k, acc.source, b.File, call.Pos())
				if acc.am.typ != nil && p.Type == nil {
					p.Type, p.Annotation = acc.am.typ, acc.am.annot
				}
			}
		}
		// a bound struct
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if !ok || !bindMethods[sel.Sel.Name] || len(call.Args) == 0 || !m.isCtx(e, sel.X) {
			return
		}
		src := "body"
		switch sel.Sel.Name {
		case "ShouldBindQuery", "BindQuery":
			src = "query"
		case "ShouldBindUri", "BindUri":
			src = "path"
		}
		t := m.Infer(e, stripAddr(call.Args[0])).deref()
		ti := t.rowTypeAny()
		if ti == nil {
			ti = g.anonStructOf(e, call.Args[0])
		}
		if ti == nil {
			report(call.Pos())("bound value `" + g.src(call.Args[0]) + "` has no struct type the extractor can read")
			return
		}
		g.bindStruct(ci, ti, src)
	})
	g.scanBlock(ci, v, b, e, b.Body.List, map[string][]string{})
}

// rangeKeys answers the constants a `for _, k := range []string{...}` variable takes
// under the stack.
func (g *gx) rangeKeys(stack []ast.Node) map[string][]string {
	out := map[string][]string{}
	for _, n := range stack {
		if rs, ok := n.(*ast.RangeStmt); ok {
			g.addRange(out, rs)
		}
	}
	return out
}

func (g *gx) addRange(rk map[string][]string, rs *ast.RangeStmt) {
	id, ok := rs.Value.(*ast.Ident)
	if !ok {
		return
	}
	cl, ok := unparen(rs.X).(*ast.CompositeLit)
	if !ok {
		return
	}
	var ks []string
	for _, el := range cl.Elts {
		s, ok := stringLit(el)
		if !ok {
			return
		}
		ks = append(ks, s)
	}
	rk[id.Name] = ks
}

type tracked struct {
	vals map[string]string // name -> kind: val, len, same, num
	oks  map[string]bool
	errs map[string]bool
	typ  *string
	keys []string
	src  string
}

func (g *gx) scanBlock(ci *closureInfo, v *gxVisit, b *Func, e *Env, list []ast.Stmt, rk map[string][]string) {
	for i, st := range list {
		switch s := st.(type) {
		case *ast.AssignStmt:
			g.assignAccessor(ci, v, b, e, s, list[i+1:], nil, rk)
		case *ast.IfStmt:
			g.scanIf(ci, v, b, e, s, rk)
		case *ast.BlockStmt:
			g.scanBlock(ci, v, b, e, s.List, rk)
		case *ast.ForStmt:
			g.scanBlock(ci, v, b, e, s.Body.List, rk)
		case *ast.RangeStmt:
			nrk := map[string][]string{}
			for k, x := range rk {
				nrk[k] = x
			}
			g.addRange(nrk, s)
			g.scanBlock(ci, v, b, e, s.Body.List, nrk)
		case *ast.SwitchStmt:
			for _, c := range s.Body.List {
				if cc, ok := c.(*ast.CaseClause); ok {
					g.scanBlock(ci, v, b, e, cc.Body, rk)
				}
			}
		case *ast.TypeSwitchStmt:
			for _, c := range s.Body.List {
				if cc, ok := c.(*ast.CaseClause); ok {
					g.scanBlock(ci, v, b, e, cc.Body, rk)
				}
			}
		}
	}
}

func (g *gx) scanIf(ci *closureInfo, v *gxVisit, b *Func, e *Env, s *ast.IfStmt, rk map[string][]string) {
	if as, ok := s.Init.(*ast.AssignStmt); ok {
		g.assignAccessor(ci, v, b, e, as, nil, s, rk)
	}
	g.scanBlock(ci, v, b, e, s.Body.List, rk)
	switch el := s.Else.(type) {
	case *ast.BlockStmt:
		g.scanBlock(ci, v, b, e, el.List, rk)
	case *ast.IfStmt:
		g.scanIf(ci, v, b, e, el, rk)
	}
}

func mentions(n ast.Node, names map[string]bool) bool {
	hit := false
	ast.Inspect(n, func(x ast.Node) bool {
		if id, ok := x.(*ast.Ident); ok && names[id.Name] {
			hit = true
		}
		return !hit
	})
	return hit
}

func (g *gx) assignAccessor(ci *closureInfo, v *gxVisit, b *Func, e *Env, as *ast.AssignStmt, following []ast.Stmt, guard *ast.IfStmt, rk map[string][]string) {
	if len(as.Rhs) != 1 {
		return
	}
	call, ok := unparen(as.Rhs[0]).(*ast.CallExpr)
	if !ok {
		return
	}
	acc := g.accessorOf(e, call, v.binds, rk, func(string) {})
	if acc == nil {
		return
	}
	names := make([]string, len(as.Lhs))
	for i, l := range as.Lhs {
		if id, ok := l.(*ast.Ident); ok && id.Name != "_" {
			names[i] = id.Name
		}
	}
	tr := &tracked{vals: map[string]string{}, oks: map[string]bool{}, errs: map[string]bool{},
		typ: acc.am.typ, keys: acc.keys, src: acc.source}
	at := func(i int) string {
		if i < len(names) {
			return names[i]
		}
		return ""
	}
	switch acc.am.shape {
	case "v":
		tr.vals[at(0)] = "val"
	case "v-ok":
		tr.vals[at(0)], tr.oks[at(1)] = "val", true
	case "v-err":
		tr.vals[at(0)], tr.errs[at(1)] = "val", true
	case "v-ok-err":
		tr.vals[at(0)], tr.oks[at(1)], tr.errs[at(2)] = "val", true, true
	default:
		return
	}
	delete(tr.vals, "")
	delete(tr.oks, "")
	delete(tr.errs, "")
	if acc.am.shape == "v-err" {
		// RequiredUUID-like: the value or a refusal
		for _, k := range acc.keys {
			g.param(ci, k, acc.source, b.File, acc.pos).Required = true
		}
	}
	if guard != nil {
		g.applyGuard(ci, v, b, e, guard, tr)
		// the body of `if s, ok := c.GetQuery("k"); ok { n, err := strconv.Atoi(s); ... }`
		following = guard.Body.List
	}
	names2 := func() map[string]bool {
		out := map[string]bool{}
		for n := range tr.vals {
			out[n] = true
		}
		for n := range tr.oks {
			out[n] = true
		}
		for n := range tr.errs {
			out[n] = true
		}
		return out
	}
	for _, st := range following {
		switch s := st.(type) {
		case *ast.AssignStmt:
			if len(s.Rhs) == 1 {
				if kind, ok := derive(s.Rhs[0], tr.vals); ok {
					for i, l := range s.Lhs {
						id, isID := l.(*ast.Ident)
						if !isID || id.Name == "_" {
							continue
						}
						if i == 0 {
							tr.vals[id.Name] = kind
							if kind == "num" {
								tr.typ = strp("Int")
								if c, isCall := unparen(s.Rhs[0]).(*ast.CallExpr); isCall && callName(c) == "ParseFloat" {
									tr.typ = strp("Float")
								}
							}
						} else {
							tr.errs[id.Name] = true
						}
					}
					continue
				}
			}
			redefined := false
			for _, l := range s.Lhs {
				if id, ok := l.(*ast.Ident); ok && (tr.oks[id.Name] || tr.errs[id.Name] || tr.vals[id.Name] != "") {
					redefined = true
				}
			}
			if redefined {
				return
			}
		case *ast.IfStmt:
			if mentions(s.Cond, names2()) {
				if g.refuses422(e, s.Body, v) {
					g.applyGuard(ci, v, b, e, s, tr)
					return
				}
			}
		}
	}
}

func callName(c *ast.CallExpr) string {
	switch f := c.Fun.(type) {
	case *ast.Ident:
		return f.Name
	case *ast.SelectorExpr:
		return f.Sel.Name
	}
	return ""
}

// derive reads `n := utf8.RuneCountInString(s)`, `n, err := strconv.Atoi(s)`,
// `t := strings.TrimSpace(s)`: the kind of the tracked value it makes.
func derive(rhs ast.Expr, vals map[string]string) (string, bool) {
	switch r := unparen(rhs).(type) {
	case *ast.Ident:
		if _, ok := vals[r.Name]; ok {
			return "same", true
		}
	case *ast.CallExpr:
		if len(r.Args) < 1 {
			return "", false
		}
		inner := unparen(r.Args[0])
		ok := false
		if id, isID := inner.(*ast.Ident); isID {
			_, ok = vals[id.Name]
		} else if c, isC := inner.(*ast.CallExpr); isC {
			_, ok = derive(c, vals)
		}
		if !ok {
			return "", false
		}
		switch callName(r) {
		case "len", "RuneCountInString", "RuneCount":
			return "len", true
		case "Atoi", "ParseInt", "ParseFloat", "ParseUint":
			return "num", true
		case "TrimSpace", "ToLower", "ToUpper", "Trim", "string", "int", "int64", "float64", "uint":
			return "same", true
		}
	}
	return "", false
}

// refuses422 reports whether a block returns a 422 refusal.
func (g *gx) refuses422(e *Env, blk *ast.BlockStmt, v *gxVisit) bool {
	found := false
	ast.Inspect(blk, func(n ast.Node) bool {
		if found {
			return false
		}
		switch s := n.(type) {
		case *ast.FuncLit:
			return false
		case *ast.ReturnStmt:
			for _, r := range s.Results {
				if st, ok := g.valueStatus(e, r, v.binds, 3); ok && st == 422 {
					found = true
				}
			}
		case *ast.CallExpr:
			if sel, ok := s.Fun.(*ast.SelectorExpr); ok && refusalMethods[sel.Sel.Name] && len(s.Args) > 0 && g.m.isCtx(e, sel.X) {
				if c, ok := g.evalConst(e, s.Args[0], v.binds); ok && c.isInt && c.n == 422 {
					found = true
				}
			}
		}
		return true
	})
	return found
}

// valueStatus answers the status a returned value carries (a constructor call, a
// literal, a local holding one).
func (g *gx) valueStatus(e *Env, x ast.Expr, b binds, depth int) (int, bool) {
	if depth < 0 {
		return 0, false
	}
	x = unparen(x)
	if u, ok := x.(*ast.UnaryExpr); ok && u.Op == token.AND {
		x = unparen(u.X)
	}
	switch v := x.(type) {
	case *ast.CallExpr:
		if callee := g.m.Callee(e, v); callee != nil {
			if sum := g.ctorOf(callee); sum != nil {
				if h := g.ctorCall(e, v, sum, b); h.statusOK {
					return h.status, true
				}
			}
		}
	case *ast.CompositeLit:
		if h, ok := g.ctorLit(e, v, b); ok && h.statusOK {
			return h.status, true
		}
	case *ast.Ident:
		if de := defEnv(e, v.Name); de != nil {
			if vals := de.values[v.Name]; len(vals) == 1 {
				return g.valueStatus(de, vals[0], b, depth-1)
			}
		}
	}
	return 0, false
}

func flattenOr(x ast.Expr, out *[]ast.Expr) {
	x = unparen(x)
	if be, ok := x.(*ast.BinaryExpr); ok && be.Op == token.LOR {
		flattenOr(be.X, out)
		flattenOr(be.Y, out)
		return
	}
	*out = append(*out, x)
}

// applyGuard reads a refusing `if`: `!ok` makes the field required; comparisons of the
// value, its length or its parsed number with a constant give its bounds.
func (g *gx) applyGuard(ci *closureInfo, v *gxVisit, b *Func, e *Env, ifs *ast.IfStmt, tr *tracked) {
	all := map[string]bool{}
	for n := range tr.vals {
		all[n] = true
	}
	for n := range tr.oks {
		all[n] = true
	}
	for n := range tr.errs {
		all[n] = true
	}
	if !mentions(ifs.Cond, all) || !g.refuses422(e, ifs.Body, v) {
		return
	}
	var ops []ast.Expr
	flattenOr(ifs.Cond, &ops)
	for _, op := range ops {
		if u, ok := op.(*ast.UnaryExpr); ok && u.Op == token.NOT {
			if id, ok := unparen(u.X).(*ast.Ident); ok && tr.oks[id.Name] {
				for _, k := range tr.keys {
					g.param(ci, k, tr.src, b.File, ifs.Pos()).Required = true
				}
			}
			continue
		}
		be, ok := op.(*ast.BinaryExpr)
		if !ok {
			continue
		}
		switch be.Op {
		case token.LSS, token.LEQ, token.GTR, token.GEQ:
		default:
			continue
		}
		kind, other, flip := "", ast.Expr(nil), false
		if k, ok := g.trackedKind(be.X, tr); ok {
			kind, other = k, be.Y
		} else if k, ok := g.trackedKind(be.Y, tr); ok {
			kind, other, flip = k, be.X, true
		} else {
			continue
		}
		opTok := be.Op
		if flip {
			switch opTok {
			case token.LSS:
				opTok = token.GTR
			case token.GTR:
				opTok = token.LSS
			case token.LEQ:
				opTok = token.GEQ
			case token.GEQ:
				opTok = token.LEQ
			}
		}
		c, isConst := g.evalConst(e, other, v.binds)
		if !isConst || !c.isInt {
			reason := "the bound `" + g.src(other) + "` of `" + strings.Join(tr.keys, ",") + "` is not a constant"
			ci.addUnread(b.File.Rel, g.line(ifs.Pos()), "bound", reason)
			g.unreadAt(b.File, ifs.Pos(), "bound", reason)
			continue
		}
		for _, k := range tr.keys {
			p := g.param(ci, k, tr.src, b.File, ifs.Pos())
			if tr.typ != nil && p.Type == nil {
				p.Type = tr.typ
			}
			n := c.n
			set := func(dst **int, val int) {
				if *dst == nil {
					*dst = &val
				}
			}
			switch opTok {
			case token.LSS: // refused below n: n is the least
				if kind == "len" {
					set(&p.MinLength, n)
				} else {
					set(&p.Ge, n)
				}
			case token.LEQ:
				if kind == "len" {
					set(&p.MinLength, n+1)
				} else {
					set(&p.Ge, n+1)
				}
			case token.GTR:
				if kind == "len" {
					set(&p.MaxLength, n)
				} else {
					set(&p.Le, n)
				}
			case token.GEQ:
				if kind == "len" {
					set(&p.MaxLength, n-1)
				} else {
					set(&p.Le, n-1)
				}
			}
		}
	}
}

// trackedKind answers what a comparison operand is: the value, its length, its number.
func (g *gx) trackedKind(x ast.Expr, tr *tracked) (string, bool) {
	switch r := unparen(x).(type) {
	case *ast.Ident:
		if k, ok := tr.vals[r.Name]; ok {
			return k, true
		}
	case *ast.CallExpr:
		if k, ok := derive(r, tr.vals); ok {
			return k, true
		}
	}
	return "", false
}

var ruleNum = regexp.MustCompile(`^(min|max|gte|lte|gt|lt|len)=(-?\d+)$`)

// bindStruct adds the fields of a bound struct (binding / validate tags).
func (g *gx) bindStruct(ci *closureInfo, ti *TypeInfo, source string) {
	if ci.mseen[ti] {
		return
	}
	ci.mseen[ti] = true
	md := &xModel{Name: ti.Name, Package: ti.Pkg.ImportPath, File: ti.File.Rel, Line: g.line(ti.Struct.Pos()), ti: ti, Fields: []xParam{}}
	if md.Package == "" {
		md.Package = ti.Pkg.Rel
	}
	var walk func(t *TypeInfo, depth int)
	walk = func(t *TypeInfo, depth int) {
		for _, f := range g.m.Fields(t) {
			if f.embedded {
				if et := g.m.TypeExpr(f.file, f.typ).rowTypeAny(); et != nil && depth > 0 {
					walk(et, depth-1)
				}
				continue
			}
			if !ast.IsExported(f.name) || jsonHidden(f.tag) {
				continue
			}
			name := jsonName(f)
			st := reflectTag(f.tag)
			if source == "query" {
				if q := st.Get("form"); q != "" && q != "-" {
					name, _, _ = strings.Cut(q, ",")
				}
			} else if source == "path" {
				if q := st.Get("uri"); q != "" {
					name, _, _ = strings.Cut(q, ",")
				}
			}
			ty := g.latticeType(f.file, f.typ, nil)
			if _, rel := g.relationship(f); rel {
				ty = nil
			}
			p := xParam{Name: name, Annotation: g.src(f.typ), Type: ty, Source: source, Choices: []string{},
				File: f.file.Rel, Line: g.fieldLine(f.owner, f.name)}
			numeric := ty != nil && (*ty == "Int" || *ty == "Float")
			rules := st.Get("binding") + "," + st.Get("validate")
			omit := false
			for _, r := range strings.Split(rules, ",") {
				r = strings.TrimSpace(r)
				switch {
				case r == "omitempty":
					omit = true
				case r == "required":
					p.Required = true
				case strings.HasPrefix(r, "oneof="):
					p.Choices = strings.Fields(strings.TrimPrefix(r, "oneof="))
				}
				if mm := ruleNum.FindStringSubmatch(r); mm != nil {
					n, _ := strconv.Atoi(mm[2])
					setp := func(dst **int, val int) { *dst = &val }
					switch mm[1] {
					case "min", "gte":
						if numeric {
							setp(&p.Ge, n)
						} else {
							setp(&p.MinLength, n)
						}
					case "max", "lte":
						if numeric {
							setp(&p.Le, n)
						} else {
							setp(&p.MaxLength, n)
						}
					case "gt":
						if numeric {
							setp(&p.Ge, n+1)
						}
					case "lt":
						if numeric {
							setp(&p.Le, n-1)
						}
					case "len":
						setp(&p.MinLength, n)
						n2 := n
						p.MaxLength = &n2
					}
				}
			}
			low := &p.MinLength // omitempty: the zero value passes, so L == 1 is exact (0), L > 1 is no interval
			if numeric {
				low = &p.Ge
			}
			if omit && *low != nil && **low > 0 {
				if **low > 1 {
					ci.addUnread(f.file.Rel, g.fieldLine(f.owner, f.name), "field", fmt.Sprintf("omitempty: the valid set {zero} ∪ [%d, ...] is not an interval", **low))
				}
				if **low == 1 && numeric {
					zero := 0
					*low = &zero
				} else {
					*low = nil
				}
			}
			md.Fields = append(md.Fields, p)
			key := source + "|" + name
			if ci.params[key] == nil {
				pp := p
				ci.params[key] = &pp
				ci.porder = append(ci.porder, key)
			}
		}
	}
	walk(ti, 2)
	ci.models = append(ci.models, md)
}

// unreadCall answers why a call the extractor cannot follow matters: an interface method
// with no single implementation, or a function value called. Calls into outside packages
// and conversions are not (D10).
func (g *gx) unreadCall(e *Env, call *ast.CallExpr) string {
	switch f := unparen(call.Fun).(type) {
	case *ast.SelectorExpr:
		if path, ok := g.m.importOf(e, f.X); ok && path == "reflect" {
			return "reflection: `" + g.src(call) + "` is not read"
		}
		if t := g.m.Infer(e, f.X).deref(); t.Kind == "named" && t.Named.Iface != nil {
			return "interface dispatch: `" + g.src(call) + "` has no single implementation in the module"
		}
	case *ast.Ident:
		if t, ok := e.lookup(f.Name); ok && t.Kind == "func" && e.lit(f.Name) == nil {
			return "function value `" + f.Name + "` is called: its target is not read"
		}
	}
	return ""
}

func joinSet(m map[string]bool) string {
	var ks []string
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return strings.Join(ks, ",")
}

// anonStructOf reads `var req struct{...}` / `req := struct{...}{}`: a bound value whose
// type is declared in place.
func (g *gx) anonStructOf(e *Env, x ast.Expr) *TypeInfo {
	id, ok := stripAddr(unparen(x)).(*ast.Ident)
	if !ok {
		return nil
	}
	de := defEnv(e, id.Name)
	if de == nil {
		return nil
	}
	var st *ast.StructType
	ast.Inspect(de.fn.Body, func(n ast.Node) bool {
		switch s := n.(type) {
		case *ast.FuncLit:
			return n == ast.Node(de.fn.Lit)
		case *ast.ValueSpec:
			for _, nm := range s.Names {
				if t, ok := s.Type.(*ast.StructType); ok && nm.Name == id.Name {
					st = t
				}
			}
		case *ast.AssignStmt:
			for i, l := range s.Lhs {
				if li, ok := l.(*ast.Ident); ok && li.Name == id.Name && i < len(s.Rhs) {
					if cl, ok := unparen(s.Rhs[i]).(*ast.CompositeLit); ok {
						if t, ok := cl.Type.(*ast.StructType); ok {
							st = t
						}
					}
				}
			}
		}
		return true
	})
	if st == nil {
		return nil
	}
	name := fmt.Sprintf("struct@%s:%d", de.fn.File.Rel, g.line(st.Pos()))
	return &TypeInfo{Name: name, Pkg: de.fn.File.Pkg, File: de.fn.File, Struct: st, Under: st}
}
