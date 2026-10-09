// GO807 partial writes: a committed write that a refusal of the same request can follow.
package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"sort"
	"strings"
)

// gormWrites are the GORM methods that write (outside a transaction they commit).
var gormWrites = map[string]bool{
	"Create": true, "CreateInBatches": true, "Save": true, "Update": true, "Updates": true,
	"UpdateColumn": true, "UpdateColumns": true, "Delete": true, "Exec": true,
	"FirstOrCreate": true,
}

// gormReads are GORM finishers that read.
var gormReads = map[string]bool{
	"First": true, "Last": true, "Take": true, "Find": true, "Scan": true, "Count": true,
	"Pluck": true, "FirstOrInit": true, "FindInBatches": true, "Row": true, "Rows": true,
}

// refusalMethods are the gin.Context methods that answer with a status (the first
// argument).
var refusalMethods = map[string]bool{
	"JSON": true, "IndentedJSON": true, "SecureJSON": true, "JSONP": true, "AsciiJSON": true,
	"PureJSON": true, "XML": true, "YAML": true, "TOML": true, "ProtoBuf": true, "String": true,
	"Data": true, "HTML": true, "Status": true, "AbortWithStatus": true,
	"AbortWithStatusJSON": true, "AbortWithStatusPureJSON": true, "AbortWithError": true,
	"Render": true, "DataFromReader": true,
}

type commit struct {
	id   int
	line int
	what string
	// the write itself (a helper's commit names the write it makes): text and file:line
	text, at string
}

// flow is the commits that may have happened on some path to a point; nil: no path
// reaches it.
type flow map[int]*commit

func (s flow) clone() flow {
	if s == nil {
		return nil
	}
	c := flow{}
	for k, v := range s {
		c[k] = v
	}
	return c
}

func union(a, b flow) flow {
	if a == nil {
		return b.clone()
	}
	if b == nil {
		return a.clone()
	}
	c := a.clone()
	for k, v := range b {
		c[k] = v
	}
	return c
}

func (s flow) without(ids []int) flow {
	if s == nil {
		return nil
	}
	c := s.clone()
	for _, id := range ids {
		delete(c, id)
	}
	return c
}

func (s flow) first() *commit {
	var best *commit
	for _, c := range s {
		if best == nil || c.line < best.line || (c.line == best.line && c.id < best.id) {
			best = c
		}
	}
	return best
}

const (
	oOther = iota
	oWrite // a write's own error or result (`err := db.Create(&x).Error`, `res := db.Save(&x)`)
	oRead  // a read's error (`db.First(&x, id).Error`)
	oCall  // a module function's or a transaction's error
)

type origin struct {
	kind    int
	write   int   // oWrite: the write's commit id
	success []int // oCall: the commits the call added (absent on its error path)
	errFlow flow  // oCall: the commits on its error path
	db      bool  // oCall: its error returns are all database failures
	// oCall with a rows bool: which result it is, and the commits where it is false
	boolIdx   int
	boolFalse flow
}

type refusal struct {
	status int
	param  int // >= 0: the status is this parameter
	// >= 0: the refusal is reached only when this error parameter is not nil
	errParam int
}

// psum is what a function does, for its callers.
type psum struct {
	ok       flow // commits on a normal return
	err      flow // commits before a returned (non-database) error
	errApp   bool
	errDB    bool
	refuses  []refusal
	txWrites bool // writes on a transaction handle
	// the function's bool result (boolIdx >= 0) is false only where boolFalse's commits
	// happened: `return res.RowsAffected == 1, res.Error` of a conditional write
	boolIdx   int
	boolSeen  bool
	boolBad   bool
	boolFalse flow
}

type hit struct {
	commit *commit
	line   int
	what   string
	again  bool
}

type loopCtx struct {
	loop      bool
	brk, cont flow
}

type summarizer struct {
	c     *checker
	memo  map[string]*psum
	busy  map[string]bool
	ids   int
	roots map[*Func]bool
}

type called struct {
	fn   *Func
	mask string
}

// pw walks one function in statement order, tracking the commits that may have
// happened.
type pw struct {
	s        *summarizer
	m        *Module
	fn       *Func
	env      *Env
	tx       map[string]bool
	origins  map[string]*origin
	callOrig map[*ast.CallExpr]*origin
	callW    map[*ast.CallExpr]int // a write call -> its commit id
	sum      *psum
	depth    int
	hits     *[]hit
	called   *[]called
	dbfail   int
	loops    []*loopCtx
	statusP  map[string]int
	pass     int
	// names holding a value read from the request context (c.Get / c.MustGet)
	ctxValues map[string]bool
	// error names a dominating condition knows are not nil (a count per nesting)
	nonNilNames map[string]int
	// names holding a helper's rows bool: the helper call's origin
	bools map[string]*origin
	// error parameters: name -> index
	errParams map[string]int
}

func (s *summarizer) newWalker(fn *Func, mask string, depth int) *pw {
	w := &pw{
		s: s, m: s.c.m, fn: fn, env: s.c.m.EnvOf(fn), tx: map[string]bool{},
		origins: map[string]*origin{}, callOrig: map[*ast.CallExpr]*origin{},
		callW: map[*ast.CallExpr]int{}, sum: &psum{}, depth: depth, hits: &[]hit{},
		called: &[]called{}, statusP: map[string]int{}, ctxValues: map[string]bool{},
		nonNilNames: map[string]int{}, bools: map[string]*origin{}, errParams: map[string]int{},
	}
	w.sum.boolIdx = -1
	nb := 0
	for i, r := range fn.Results() {
		if id, ok := r.typ.(*ast.Ident); ok && id.Name == "bool" {
			w.sum.boolIdx = i
			nb++
		}
	}
	if nb != 1 {
		w.sum.boolIdx = -1
	}
	for i, p := range fn.Params() {
		if id, ok := p.typ.(*ast.Ident); ok && id.Name == "error" && p.name != "" {
			w.errParams[p.name] = i
		}
		if i < len(mask) && mask[i] == '1' && p.name != "" {
			w.tx[p.name] = true
		}
		t := w.m.TypeExpr(fn.File, p.typ)
		if t.Kind == "basic" && strings.HasPrefix(t.Basic, "int") && p.name != "" {
			w.statusP[p.name] = i
		}
	}
	return w
}

// run walks the whole function.
func (w *pw) run() {
	end := w.block(w.fn.Body.List, flow{})
	if end != nil {
		w.sum.ok = union(w.sum.ok, end)
	}
}

// summary answers what fn does for a caller (nil: beyond the depth, or recursive).
func (s *summarizer) summary(fn *Func, mask string, depth int) *psum {
	if depth > s.c.pol.HelperDepth {
		return nil
	}
	key := fmt.Sprintf("%p|%s|%d", fn, mask, depth)
	if got, ok := s.memo[key]; ok {
		return got
	}
	if s.busy[key] {
		return nil
	}
	s.busy[key] = true
	w := s.newWalker(fn, mask, depth)
	w.run()
	delete(s.busy, key)
	s.memo[key] = w.sum
	return w.sum
}

func (w *pw) newCommit(line int, what string) *commit {
	w.s.ids++
	return &commit{id: w.s.ids, line: line, what: what, text: what,
		at: fmt.Sprintf("%s:%d", w.fn.File.Rel, line)}
}

// viaHelper is the commit a call to a helper makes: the helper's first write.
func (w *pw) viaHelper(c *ast.CallExpr, callee *Func, inner *commit, suffix string) *commit {
	cm := w.newCommit(w.line(c), fmt.Sprintf("%s(), which writes %s at %s%s", callee.Name,
		inner.text, inner.at, suffix))
	cm.text, cm.at = inner.text, inner.at
	return cm
}

func (w *pw) addCommit(s flow, c *commit) flow {
	if s == nil {
		return nil
	}
	n := s.clone()
	n[c.id] = c
	return n
}

func (w *pw) line(n ast.Node) int {
	if c, ok := n.(*ast.CallExpr); ok {
		return w.m.callLine(c)
	}
	return w.m.Line(n.Pos())
}

func (w *pw) block(list []ast.Stmt, s flow) flow {
	for _, st := range list {
		if s == nil {
			return nil
		}
		s = w.stmt(st, s)
	}
	return s
}

func (w *pw) breakable() *loopCtx {
	if len(w.loops) == 0 {
		return nil
	}
	return w.loops[len(w.loops)-1]
}

func (w *pw) innerLoop() *loopCtx {
	for i := len(w.loops) - 1; i >= 0; i-- {
		if w.loops[i].loop {
			return w.loops[i]
		}
	}
	return nil
}

func isTerminal(m *Module, e *Env, x ast.Expr) bool {
	call, ok := unparen(x).(*ast.CallExpr)
	if !ok {
		return false
	}
	switch f := call.Fun.(type) {
	case *ast.Ident:
		if _, local := e.lookup(f.Name); !local && f.Name == "panic" {
			return true
		}
	case *ast.SelectorExpr:
		if path, ok := m.importOf(e, f.X); ok {
			switch path {
			case "os":
				return f.Sel.Name == "Exit"
			case "log":
				return strings.HasPrefix(f.Sel.Name, "Fatal") || strings.HasPrefix(f.Sel.Name, "Panic")
			}
		}
	}
	return false
}

func (w *pw) stmt(st ast.Stmt, s flow) flow {
	switch v := st.(type) {
	case *ast.BlockStmt:
		return w.block(v.List, s)
	case *ast.ExprStmt:
		s = w.expr(v.X, s)
		if isTerminal(w.m, w.env, v.X) {
			return nil
		}
		return s
	case *ast.AssignStmt:
		for _, r := range v.Rhs {
			s = w.expr(r, s)
		}
		w.assign(v.Lhs, v.Rhs)
		return s
	case *ast.DeclStmt:
		if g, ok := v.Decl.(*ast.GenDecl); ok {
			for _, sp := range g.Specs {
				if vs, ok := sp.(*ast.ValueSpec); ok {
					for _, x := range vs.Values {
						s = w.expr(x, s)
					}
					var lhs []ast.Expr
					for _, n := range vs.Names {
						lhs = append(lhs, n)
					}
					w.assign(lhs, vs.Values)
				}
			}
		}
		return s
	case *ast.ReturnStmt:
		for _, r := range v.Results {
			s = w.expr(r, s)
		}
		if s != nil {
			w.ret(v, s)
		}
		return nil
	case *ast.IfStmt:
		if v.Init != nil {
			s = w.stmt(v.Init, s)
			if s == nil {
				return nil
			}
		}
		s = w.expr(v.Cond, s)
		if s == nil {
			return nil
		}
		thenS, elseS, thenDB, elseDB := w.branches(v.Cond, s)
		thenNN, elseNN := w.nonNilIn(v.Cond)
		t := w.guarded(thenDB, func() flow {
			return w.knowing(thenNN, func() flow { return w.block(v.Body.List, thenS) })
		})
		e := elseS
		if v.Else != nil {
			e = w.guarded(elseDB, func() flow {
				return w.knowing(elseNN, func() flow { return w.stmt(v.Else, elseS) })
			})
		}
		return union(t, e)
	case *ast.ForStmt:
		if v.Init != nil {
			s = w.stmt(v.Init, s)
		}
		if v.Cond != nil {
			s = w.expr(v.Cond, s)
		}
		return w.loop(v.Body, v.Post, s, v.Cond == nil)
	case *ast.RangeStmt:
		s = w.expr(v.X, s)
		return w.loop(v.Body, nil, s, false)
	case *ast.SwitchStmt:
		if v.Init != nil {
			s = w.stmt(v.Init, s)
		}
		if v.Tag != nil {
			s = w.expr(v.Tag, s)
		}
		return w.clauses(v.Body, s, v.Tag == nil)
	case *ast.TypeSwitchStmt:
		if v.Init != nil {
			s = w.stmt(v.Init, s)
		}
		s = w.stmt(v.Assign, s)
		return w.clauses(v.Body, s, false)
	case *ast.SelectStmt:
		ctx := &loopCtx{}
		w.loops = append(w.loops, ctx)
		var out flow
		for _, cl := range v.Body.List {
			cc := cl.(*ast.CommClause)
			b := s.clone()
			if cc.Comm != nil {
				b = w.stmt(cc.Comm, b)
			}
			out = union(out, w.block(cc.Body, b))
		}
		w.loops = w.loops[:len(w.loops)-1]
		return union(out, ctx.brk)
	case *ast.LabeledStmt:
		return w.stmt(v.Stmt, s)
	case *ast.BranchStmt:
		switch v.Tok {
		case token.BREAK:
			if l := w.breakable(); l != nil && v.Label == nil {
				l.brk = union(l.brk, s)
			} else if l := w.innerLoop(); l != nil {
				l.brk = union(l.brk, s)
			}
			return nil
		case token.CONTINUE:
			if l := w.innerLoop(); l != nil {
				l.cont = union(l.cont, s)
			}
			return nil
		case token.GOTO:
			return nil
		}
		return s
	case *ast.GoStmt, *ast.DeferStmt:
		return s
	case *ast.IncDecStmt:
		return w.expr(v.X, s)
	case *ast.SendStmt:
		return w.expr(v.Value, s)
	}
	return s
}

// nonNilIn answers the names cond's truth (then) and falsehood (else) imply are not nil.
func (w *pw) nonNilIn(cond ast.Expr) (then, els []string) {
	for name := range identsIn(cond) {
		t, e := nonNil(cond, name)
		if t {
			then = append(then, name)
		}
		if e {
			els = append(els, name)
		}
	}
	return then, els
}

func (w *pw) knowing(names []string, f func() flow) flow {
	for _, n := range names {
		w.nonNilNames[n]++
	}
	defer func() {
		for _, n := range names {
			w.nonNilNames[n]--
		}
	}()
	return f()
}

func (w *pw) guarded(db bool, f func() flow) flow {
	if db {
		w.dbfail++
		defer func() { w.dbfail-- }()
	}
	return f()
}

func (w *pw) loop(body *ast.BlockStmt, post ast.Stmt, s flow, forever bool) flow {
	if s == nil {
		return nil
	}
	ctx := &loopCtx{loop: true}
	w.loops = append(w.loops, ctx)
	s1 := union(w.block(body.List, s.clone()), ctx.cont)
	if post != nil && s1 != nil {
		s1 = w.stmt(post, s1)
	}
	ctx.cont = nil
	in2 := union(s, s1)
	w.pass++
	s2 := union(w.block(body.List, in2), ctx.cont)
	w.pass--
	w.loops = w.loops[:len(w.loops)-1]
	if forever {
		return ctx.brk
	}
	return union(union(in2, s2), ctx.brk)
}

func (w *pw) clauses(body *ast.BlockStmt, s flow, tagless bool) flow {
	ctx := &loopCtx{}
	w.loops = append(w.loops, ctx)
	var out flow
	hasDefault := false
	var earlierFalse []string // names an earlier case's falsehood knows are not nil
	for _, cl := range body.List {
		cc := cl.(*ast.CaseClause)
		if cc.List == nil {
			hasDefault = true
		}
		b := s.clone()
		db := false
		for _, x := range cc.List {
			b = w.expr(x, b)
		}
		known := append([]string{}, earlierFalse...)
		if tagless && len(cc.List) == 1 {
			b, _, db, _ = w.branches(cc.List[0], b)
			t, e := w.nonNilIn(cc.List[0])
			known = append(known, t...)
			earlierFalse = append(earlierFalse, e...)
		} else if tagless && len(cc.List) > 1 {
			// `case A, B:` is taken when any of them holds: it knows what every one of
			// them knows is not nil; when it is not taken, none held.
			var common []string
			var taken flow
			rest := b
			allDB := true
			for i, x := range cc.List {
				th, el, dbt, _ := w.branches(x, rest)
				taken = union(taken, th)
				rest = el
				allDB = allDB && dbt
				t, e := w.nonNilIn(x)
				if i == 0 {
					common = append(common, t...)
				} else {
					common = intersect(common, t)
				}
				earlierFalse = append(earlierFalse, e...)
			}
			known = append(known, common...)
			b, db = taken, allDB
		}
		out = union(out, w.guarded(db, func() flow {
			return w.knowing(known, func() flow { return w.block(cc.Body, b) })
		}))
	}
	w.loops = w.loops[:len(w.loops)-1]
	if !hasDefault {
		out = union(out, s)
	}
	return union(out, ctx.brk)
}

// expr runs the calls of an expression in evaluation order (arguments first; function
// literals are not entered).
func (w *pw) expr(x ast.Expr, s flow) flow {
	if x == nil || s == nil {
		return s
	}
	var calls []*ast.CallExpr
	var stack []ast.Node
	ast.Inspect(x, func(n ast.Node) bool {
		if n == nil {
			top := stack[len(stack)-1]
			stack = stack[:len(stack)-1]
			if c, ok := top.(*ast.CallExpr); ok {
				calls = append(calls, c)
			}
			return false
		}
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		stack = append(stack, n)
		return true
	})
	for _, c := range calls {
		if s == nil {
			return nil
		}
		s = w.call(c, s)
	}
	return s
}

// mentionsTx reports whether a call's receiver or arguments name a transaction handle.
func (w *pw) mentionsTx(c *ast.CallExpr) bool { return w.mentionsTxExpr(c) }

func (w *pw) mentionsTxExpr(c ast.Expr) bool {
	for name := range identsIn(c) {
		if w.tx[name] {
			return true
		}
	}
	return false
}

func (w *pw) inTx(root ast.Expr) bool {
	name := rootIdent(root)
	return name != "" && w.tx[name]
}

// mask answers which of a callee's parameters receive a transaction handle.
func (w *pw) mask(callee *Func, c *ast.CallExpr) string {
	var b strings.Builder
	n := len(callee.Params())
	for i := 0; i < n; i++ {
		bit := byte('0')
		if i < len(c.Args) {
			a := c.Args[i]
			if call, ok := unparen(a).(*ast.CallExpr); ok {
				if _, root := w.m.gormChain(w.env, call); root != nil && w.inTx(root) {
					bit = '1'
				}
			} else if w.inTx(a) {
				bit = '1'
			}
		}
		b.WriteByte(bit)
	}
	return b.String()
}

func (w *pw) call(c *ast.CallExpr, s flow) flow {
	m := w.m
	if sel, ok := c.Fun.(*ast.SelectorExpr); ok {
		// a refusal: c.JSON(status, ...) with a known status >= 400
		if refusalMethods[sel.Sel.Name] && len(c.Args) > 0 && m.isCtx(w.env, sel.X) {
			w.answer(c, sel.Sel.Name, c.Args[0], s)
			return s
		}
		if sel.Sel.Name == "WriteHeader" && len(c.Args) == 1 {
			if inner, ok := sel.X.(*ast.SelectorExpr); ok && inner.Sel.Name == "Writer" && m.isCtx(w.env, inner.X) {
				w.answer(c, "Writer.WriteHeader", c.Args[0], s)
				return s
			}
		}
		if m.isGorm(w.env, sel.X) {
			name := sel.Sel.Name
			switch {
			case name == "Commit" && len(c.Args) == 0:
				cm := w.newCommit(w.line(c), exprText(sel.X)+".Commit()")
				w.callW[c] = cm.id
				return w.addCommit(s, cm)
			case gormWrites[name]:
				links, root := m.gormChain(w.env, c)
				if w.inTx(root) {
					w.sum.txWrites = true
					return s
				}
				cm := w.newCommit(w.line(c), chainText(links, root))
				w.callW[c] = cm.id
				return w.addCommit(s, cm)
			case name == "Transaction" && len(c.Args) >= 1:
				return w.transaction(c, s)
			}
			return s
		}
	}
	callee := m.Callee(w.env, c)
	if callee == nil || callee == w.fn {
		return s
	}
	if w.depth >= w.s.c.pol.HelperDepth {
		return s
	}
	mask := w.mask(callee, c)
	*w.called = append(*w.called, called{callee, mask})
	sum := w.s.summary(callee, mask, w.depth+1)
	if sum == nil {
		return s
	}
	for _, r := range sum.refuses {
		st := r.status
		if r.param >= 0 {
			st = -1
			if r.param < len(c.Args) {
				st = m.constInt(w.env, c.Args[r.param])
			}
		}
		if st < 400 || st >= 500 {
			continue
		}
		at, dbfail := s, false
		if r.errParam >= 0 && r.errParam < len(c.Args) {
			// reached only when the error handed in is not nil: its error path
			if id, ok := unparen(c.Args[r.errParam]).(*ast.Ident); ok {
				if o := w.origins[id.Name]; o != nil {
					switch o.kind {
					case oWrite:
						dbfail = true
					case oCall:
						at = union(s.without(o.success), o.errFlow)
						dbfail = o.db
					}
				}
			}
		}
		if dbfail {
			continue
		}
		before := len(*w.hits)
		w.refuse(c, at, fmt.Sprintf("%s() (it refuses with %d)", callee.Name, st), st)
		if len(*w.hits) > before {
			break
		}
	}
	o := &origin{kind: oCall, db: sum.errDB && !sum.errApp}
	captured := false
	if callee.Lit != nil {
		for name := range identsIn(callee.Lit.Body) {
			captured = captured || w.tx[name]
		}
	}
	if (w.mentionsTx(c) || captured) && (len(sum.ok) > 0 || len(sum.err) > 0 || sum.txWrites) {
		// handed a transaction handle (an argument, or a receiver built from one:
		// repo.WithTx(tx).Create(...)), or a local closure using one: its writes are
		// the transaction's
		w.sum.txWrites = true
		w.callOrig[c] = o
		return s
	}
	if len(sum.ok) > 0 {
		first := sum.ok.first()
		cm := w.viaHelper(c, callee, first, "")
		o.success = []int{cm.id}
		s = w.addCommit(s, cm)
	}
	if len(sum.err) > 0 {
		first := sum.err.first()
		cm := w.viaHelper(c, callee, first, ", then returns an error")
		o.errFlow = flow{cm.id: cm}
	}
	if sum.txWrites && strings.Contains(w.mask(callee, c), "1") {
		w.sum.txWrites = true
	}
	o.boolIdx = -1
	if sum.boolIdx >= 0 && sum.boolSeen && !sum.boolBad {
		o.boolIdx = sum.boolIdx
		o.boolFalse = flow{}
		if len(sum.boolFalse) > 0 {
			cm := w.viaHelper(c, callee, sum.boolFalse.first(), "")
			o.boolFalse[cm.id] = cm
		}
	}
	w.callOrig[c] = o
	return s
}

// answer judges a status answer: a refusal when its status is known and >= 400.
func (w *pw) answer(c *ast.CallExpr, method string, status ast.Expr, s flow) {
	st := w.m.constInt(w.env, status)
	if st >= 500 {
		return // a failure (the database's, a library's), not a refusal: not judged
	}
	if st >= 400 {
		w.refuse(c, s, fmt.Sprintf("c.%s(%d, ...)", method, st), st)
		return
	}
	if st < 0 && w.dbfail == 0 {
		if id, ok := unparen(status).(*ast.Ident); ok {
			if p, ok := w.statusP[id.Name]; ok {
				w.sum.refuses = append(w.sum.refuses, refusal{status: -1, param: p, errParam: w.knownErrParam()})
			}
		}
	}
}

// knownErrParam answers an error parameter a dominating condition knows is not nil (-1).
func (w *pw) knownErrParam() int {
	for name, i := range w.errParams {
		if w.nonNilNames[name] > 0 {
			return i
		}
	}
	return -1
}

func (w *pw) refuse(c *ast.CallExpr, s flow, what string, status int) {
	if w.dbfail > 0 {
		return
	}
	w.sum.refuses = append(w.sum.refuses, refusal{status: status, param: -1, errParam: w.knownErrParam()})
	if len(s) == 0 {
		return
	}
	first := s.first()
	*w.hits = append(*w.hits, hit{commit: first, line: w.line(c), what: what,
		again: w.pass > 0 && first.line >= w.line(c)})
}

// transaction walks `db.Transaction(func(tx *gorm.DB) error {...})`: writes on tx do not
// commit; the call commits them when the function returns nil.
func (w *pw) transaction(c *ast.CallExpr, s flow) flow {
	lit, ok := unparen(c.Args[0]).(*ast.FuncLit)
	if !ok {
		return s
	}
	fn := w.m.LitFunc(lit, w.fn)
	sub := w.s.newWalker(fn, "", w.depth)
	for k := range w.tx {
		sub.tx[k] = true
	}
	if ps := fn.Params(); len(ps) > 0 && ps[0].name != "" {
		sub.tx[ps[0].name] = true
	}
	sub.hits, sub.called, sub.dbfail = w.hits, w.called, w.dbfail
	sub.statusP = w.statusP
	end := sub.block(fn.Body.List, s.clone())
	if end != nil {
		sub.sum.ok = union(sub.sum.ok, end)
	}
	w.sum.refuses = append(w.sum.refuses, sub.sum.refuses...)
	success := sub.sum.ok
	if success == nil {
		success = s.clone()
	}
	o := &origin{kind: oCall, db: sub.sum.errDB && !sub.sum.errApp, errFlow: sub.sum.err}
	for id := range success {
		if _, before := s[id]; !before {
			o.success = append(o.success, id)
		}
	}
	if sub.sum.txWrites {
		cm := w.newCommit(w.line(c), exprText(c.Fun)+"(...) (the transaction commits)")
		success = w.addCommit(success, cm)
		o.success = append(o.success, cm.id)
	}
	w.callOrig[c] = o
	return success
}

// originOf answers what an assigned expression's error or result is.
func (w *pw) originOf(x ast.Expr) *origin {
	switch v := unparen(x).(type) {
	case *ast.SelectorExpr:
		if v.Sel.Name == "Error" {
			if call, ok := unparen(v.X).(*ast.CallExpr); ok {
				if id, ok := w.callW[call]; ok {
					return &origin{kind: oWrite, write: id}
				}
				if links, _ := w.m.gormChain(w.env, call); links != nil {
					last := links[len(links)-1].name
					if gormReads[last] {
						return &origin{kind: oRead}
					}
					if last == "Commit" || gormWrites[last] {
						return &origin{kind: oWrite}
					}
				}
			}
			if id, ok := unparen(v.X).(*ast.Ident); ok {
				if o := w.origins[id.Name]; o != nil && o.kind == oWrite {
					return o
				}
			}
		}
	case *ast.CallExpr:
		if o, ok := w.callOrig[v]; ok {
			return o
		}
		if id, ok := w.callW[v]; ok {
			return &origin{kind: oWrite, write: id}
		}
		if links, _ := w.m.gormChain(w.env, v); links != nil {
			last := links[len(links)-1].name
			if gormWrites[last] || last == "Commit" {
				return &origin{kind: oWrite}
			}
			if gormReads[last] {
				return &origin{kind: oRead}
			}
		}
	}
	return nil
}

func (w *pw) assign(lhs, rhs []ast.Expr) {
	set := func(l ast.Expr, o *origin) {
		id, ok := l.(*ast.Ident)
		if !ok || id.Name == "_" {
			return
		}
		if o == nil {
			delete(w.origins, id.Name)
		} else {
			w.origins[id.Name] = o
		}
		delete(w.tx, id.Name)
		delete(w.bools, id.Name)
	}
	for _, l := range lhs {
		if id, ok := l.(*ast.Ident); ok {
			delete(w.ctxValues, id.Name)
		}
	}
	if len(rhs) == 1 && len(lhs) >= 1 {
		if call, ok := unparen(rhs[0]).(*ast.CallExpr); ok {
			if sel, ok := call.Fun.(*ast.SelectorExpr); ok && w.m.isCtx(w.env, sel.X) &&
				(sel.Sel.Name == "Get" || sel.Sel.Name == "MustGet" || sel.Sel.Name == "Value") {
				if id, ok := lhs[0].(*ast.Ident); ok {
					w.ctxValues[id.Name] = true
				}
			}
		}
	}
	if len(lhs) == len(rhs) {
		for i := range lhs {
			set(lhs[i], w.originOf(rhs[i]))
			w.markTx(lhs[i], rhs[i])
		}
		return
	}
	if len(rhs) == 1 {
		for i, l := range lhs {
			if i == len(lhs)-1 {
				set(l, w.originOf(rhs[0]))
			} else {
				set(l, nil)
			}
		}
		if call, ok := unparen(rhs[0]).(*ast.CallExpr); ok {
			if o := w.callOrig[call]; o != nil && o.boolIdx >= 0 && o.boolIdx < len(lhs) {
				if id, ok := lhs[o.boolIdx].(*ast.Ident); ok && id.Name != "_" {
					w.bools[id.Name] = o
				}
			}
		}
		if len(lhs) > 0 {
			w.markTx(lhs[0], rhs[0])
		}
	}
}

// markTx records `tx := db.Begin()`.
func (w *pw) markTx(l, r ast.Expr) {
	id, ok := l.(*ast.Ident)
	if !ok {
		return
	}
	// a handle read from the request (`c.MustGet("db").(*gorm.DB)`, a helper handed the
	// context): a middleware may commit or roll it back, so its writes are not judged
	if w.fromRequest(r) {
		w.tx[id.Name] = true
		return
	}
	// a value built from a transaction handle (repo := NewRepo(tx), st := s.WithTx(tx))
	if w.mentionsTxExpr(r) {
		w.tx[id.Name] = true
		return
	}
	call, ok := unparen(r).(*ast.CallExpr)
	if !ok {
		return
	}
	links, root := w.m.gormChain(w.env, call)
	if links == nil {
		return
	}
	for _, l := range links {
		if l.name == "Begin" {
			w.tx[id.Name] = true
			return
		}
	}
	if w.inTx(root) {
		w.tx[id.Name] = true
	}
}

// fromRequest reports whether a *gorm.DB value is read from the request context.
func (w *pw) fromRequest(r ast.Expr) bool {
	if !w.m.isGorm(w.env, r) {
		return false
	}
	found := false
	ast.Inspect(r, func(n ast.Node) bool {
		if found {
			return false
		}
		switch v := n.(type) {
		case *ast.FuncLit:
			return false
		case *ast.Ident:
			if w.ctxValues[v.Name] {
				found = true
			}
		case *ast.CallExpr:
			if sel, ok := v.Fun.(*ast.SelectorExpr); ok && w.m.isCtx(w.env, sel.X) {
				switch sel.Sel.Name {
				case "Get", "MustGet", "Value":
					found = true
				}
			}
			if callee := w.m.Callee(w.env, v); callee != nil {
				for _, a := range v.Args {
					if w.m.isCtx(w.env, a) {
						found = true
					}
				}
			}
		}
		return true
	})
	return found
}

// ret records a return: a normal one, a database failure, or another error.
// retBool records what a return's bool result says of the function's conditional write.
func (w *pw) retBool(r *ast.ReturnStmt, s flow) {
	i := w.sum.boolIdx
	if i < 0 {
		return
	}
	if len(r.Results) != len(w.fn.Results()) {
		w.sum.boolBad = true
		return
	}
	if w.dbfail > 0 {
		return // an error return: the caller's error branch decides
	}
	x := unparen(r.Results[i])
	if id, ok := x.(*ast.Ident); ok {
		switch id.Name {
		case "true":
			return
		case "false":
			w.sum.boolFalse = union(w.sum.boolFalse, s)
			return
		}
	}
	var write = -1
	ra := func(e ast.Expr) bool {
		sel, ok := unparen(e).(*ast.SelectorExpr)
		if !ok || sel.Sel.Name != "RowsAffected" {
			return false
		}
		id, ok := unparen(sel.X).(*ast.Ident)
		if !ok {
			return false
		}
		if o := w.origins[id.Name]; o != nil && o.kind == oWrite && o.write > 0 {
			write = o.write
			return true
		}
		return false
	}
	t, e := noRows(x, ra)
	if write < 0 || t || !e {
		w.sum.boolBad = true
		return
	}
	w.sum.boolSeen = true
	w.sum.boolFalse = union(w.sum.boolFalse, s.without([]int{write}))
	if w.sum.boolFalse == nil {
		w.sum.boolFalse = flow{}
	}
}

func (w *pw) ret(r *ast.ReturnStmt, s flow) {
	w.retBool(r, s)
	if !w.fn.ErrorResult() || len(r.Results) == 0 {
		if len(r.Results) == 0 && w.fn.ErrorResult() {
			// a naked return of a named error: its last assignment decides
			rs := w.fn.Results()
			name := rs[len(rs)-1].name
			if o := w.origins[name]; o != nil && w.dbfail == 0 {
				w.errReturn(o, s)
				return
			}
		}
		w.sum.ok = union(w.sum.ok, s)
		return
	}
	last := unparen(r.Results[len(r.Results)-1])
	if id, ok := last.(*ast.Ident); ok && id.Name == "nil" {
		w.sum.ok = union(w.sum.ok, s)
		return
	}
	if w.dbfail > 0 {
		w.sum.errDB = true
		return
	}
	var o *origin
	if id, ok := last.(*ast.Ident); ok {
		o = w.origins[id.Name]
	} else {
		o = w.originOf(last)
		if o == nil {
			if call, ok := last.(*ast.CallExpr); ok {
				// fmt.Errorf("...: %w", err) of a database failure is one
				for _, a := range call.Args {
					if id, ok := unparen(a).(*ast.Ident); ok {
						if ao := w.origins[id.Name]; ao != nil && (ao.kind == oWrite || ao.kind == oCall && ao.db) {
							o = ao
						}
					}
				}
			}
		}
	}
	w.errReturn(o, s)
	if w.mayBeNil(last) {
		// `return err` / `return db.Create(x).Error` / `return helper()` returns nil when
		// the work succeeded: the commits so far are the normal return's too
		w.sum.ok = union(w.sum.ok, s)
	}
}

// mayBeNil reports whether a returned error may be nil: a local name not known non-nil
// here, a GORM `.Error`, a call that is not an error constructor.
func (w *pw) mayBeNil(x ast.Expr) bool {
	switch v := unparen(x).(type) {
	case *ast.Ident:
		if _, local := w.env.lookup(v.Name); !local {
			return false // a package error value
		}
		return w.nonNilNames[v.Name] == 0
	case *ast.SelectorExpr:
		if _, pkg := w.m.importOf(w.env, v.X); pkg {
			return false
		}
		return v.Sel.Name == "Error"
	case *ast.CallExpr:
		if sel, ok := v.Fun.(*ast.SelectorExpr); ok {
			if path, pkg := w.m.importOf(w.env, sel.X); pkg && (path == "errors" || path == "fmt") {
				return false
			}
			if sel.Sel.Name == "New" || sel.Sel.Name == "Errorf" || sel.Sel.Name == "Wrap" || sel.Sel.Name == "Wrapf" {
				return false
			}
		}
		return true
	}
	return false
}

func (w *pw) errReturn(o *origin, s flow) {
	switch {
	case o != nil && o.kind == oWrite:
		w.sum.errDB = true
	case o != nil && o.kind == oCall:
		if o.db {
			w.sum.errDB = true
			return
		}
		w.sum.errApp = true
		w.sum.err = union(w.sum.err, union(s.without(o.success), o.errFlow))
	default:
		w.sum.errApp = true
		w.sum.err = union(w.sum.err, s)
	}
}

// nonNil answers, for an error name tested in cond, whether cond's truth (then) or its
// falsehood (else) implies the error is not nil.
func nonNil(cond ast.Expr, name string) (then, els bool) {
	switch v := unparen(cond).(type) {
	case *ast.UnaryExpr:
		if v.Op == token.NOT {
			t, e := nonNil(v.X, name)
			return e, t
		}
	case *ast.BinaryExpr:
		switch v.Op {
		case token.NEQ, token.EQL:
			if isName(v.X, name) && isNil(v.Y) || isName(v.Y, name) && isNil(v.X) {
				if v.Op == token.NEQ {
					return true, false
				}
				return false, true
			}
		case token.LAND:
			t1, e1 := nonNil(v.X, name)
			t2, e2 := nonNil(v.Y, name)
			return t1 || t2, e1 && e2
		case token.LOR:
			t1, e1 := nonNil(v.X, name)
			t2, e2 := nonNil(v.Y, name)
			return t1 && t2, e1 || e2
		}
	case *ast.CallExpr:
		// errors.Is(err, X) / errors.As(err, &x)
		if sel, ok := v.Fun.(*ast.SelectorExpr); ok && (sel.Sel.Name == "Is" || sel.Sel.Name == "As") {
			if x, ok := sel.X.(*ast.Ident); ok && x.Name == "errors" && len(v.Args) > 0 && isName(v.Args[0], name) {
				return true, false
			}
		}
	}
	return false, false
}

// noRows answers whether cond's truth (then) or falsehood (else) implies the write
// whose RowsAffected `ra` reads changed no row.
func noRows(cond ast.Expr, ra func(ast.Expr) bool) (then, els bool) {
	switch v := unparen(cond).(type) {
	case *ast.UnaryExpr:
		if v.Op == token.NOT {
			t, e := noRows(v.X, ra)
			return e, t
		}
	case *ast.BinaryExpr:
		switch v.Op {
		case token.LAND:
			t1, e1 := noRows(v.X, ra)
			t2, e2 := noRows(v.Y, ra)
			return t1 || t2, e1 && e2
		case token.LOR:
			t1, e1 := noRows(v.X, ra)
			t2, e2 := noRows(v.Y, ra)
			return t1 && t2, e1 || e2
		}
		x, y, op := v.X, v.Y, v.Op
		if ra(y) {
			x, y = y, x
			switch op {
			case token.LSS:
				op = token.GTR
			case token.GTR:
				op = token.LSS
			case token.LEQ:
				op = token.GEQ
			case token.GEQ:
				op = token.LEQ
			}
		}
		if !ra(x) {
			return false, false
		}
		n := intLit(y)
		if n < 0 {
			return false, false
		}
		switch {
		case op == token.EQL && n == 0, op == token.LSS && n == 1, op == token.LEQ && n == 0:
			return true, false
		case op == token.NEQ && n == 0, op == token.GTR && n == 0, op == token.GEQ && n == 1:
			return false, true
		case op == token.NEQ:
			return true, false
		case op == token.EQL:
			return false, true
		}
	}
	return false, false
}

func intLit(e ast.Expr) int {
	if bl, ok := unparen(e).(*ast.BasicLit); ok && bl.Kind == token.INT {
		return atoi(bl.Value)
	}
	return -1
}

func isName(e ast.Expr, name string) bool {
	id, ok := unparen(e).(*ast.Ident)
	return ok && id.Name == name
}

func isNil(e ast.Expr) bool { return isName(e, "nil") }

// branches answers the states of an if's two branches and whether each handles a
// database failure (whose refusals are not judged).
func (w *pw) branches(cond ast.Expr, s flow) (flow, flow, bool, bool) {
	thenS, elseS := s.clone(), s.clone()
	thenDB, elseDB := false, false
	// X.Error / X.RowsAffected of a write (a result name, or the chain itself)
	writeOf := func(x ast.Expr) (int, bool) {
		switch v := unparen(x).(type) {
		case *ast.Ident:
			if o := w.origins[v.Name]; o != nil && o.kind == oWrite {
				return o.write, true
			}
		case *ast.CallExpr:
			if id, ok := w.callW[v]; ok {
				return id, true
			}
			if links, _ := w.m.gormChain(w.env, v); links != nil {
				last := links[len(links)-1].name
				if gormWrites[last] || last == "Commit" {
					return 0, true
				}
			}
		}
		return 0, false
	}
	ast.Inspect(cond, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		sel, ok := n.(*ast.SelectorExpr)
		if !ok {
			return true
		}
		id, isWrite := writeOf(sel.X)
		if !isWrite {
			return true
		}
		switch sel.Sel.Name {
		case "Error":
			t, e := nonNilExpr(cond, sel)
			if !t && !e {
				t, e = true, true
			}
			thenDB = thenDB || t
			elseDB = elseDB || e
		case "RowsAffected":
			t, e := noRows(cond, func(x ast.Expr) bool { return sameNode(x, sel) })
			if !t && !e {
				t, e = true, true
			}
			if t {
				thenS = thenS.without([]int{id})
			}
			if e {
				elseS = elseS.without([]int{id})
			}
		}
		return false
	})
	for name := range identsIn(cond) {
		o := w.origins[name]
		if o == nil {
			continue
		}
		switch o.kind {
		case oWrite:
			if !mentionedAlone(cond, name) {
				continue
			}
			t, e := nonNil(cond, name)
			if !t && !e {
				t, e = true, true
			}
			thenDB = thenDB || t
			elseDB = elseDB || e
		case oCall:
			if !mentionedAlone(cond, name) {
				continue
			}
			t, e := nonNil(cond, name)
			errPath := func(b flow) flow { return union(b.without(o.success), o.errFlow) }
			switch {
			case t:
				thenS = errPath(thenS)
				thenDB = thenDB || o.db
			case e:
				elseS = errPath(elseS)
				elseDB = elseDB || o.db
			default:
				thenS = thenS.without(o.success)
				elseS = elseS.without(o.success)
			}
		}
	}
	for name := range identsIn(cond) {
		o := w.bools[name]
		if o == nil {
			continue
		}
		t, e := boolFalseIn(cond, name)
		falsePath := func(b flow) flow { return union(b.without(o.success), o.boolFalse) }
		if t {
			thenS = falsePath(thenS)
		}
		if e {
			elseS = falsePath(elseS)
		}
	}
	return thenS, elseS, thenDB, elseDB
}

// boolFalseIn answers whether cond's truth (then) or falsehood (else) implies the bool
// name is false.
func boolFalseIn(cond ast.Expr, name string) (then, els bool) {
	switch v := unparen(cond).(type) {
	case *ast.Ident:
		if v.Name == name {
			return false, true
		}
	case *ast.UnaryExpr:
		if v.Op == token.NOT {
			t, e := boolFalseIn(v.X, name)
			return e, t
		}
	case *ast.BinaryExpr:
		switch v.Op {
		case token.EQL, token.NEQ:
			for _, p := range [][2]ast.Expr{{v.X, v.Y}, {v.Y, v.X}} {
				if isName(p[0], name) {
					if lit, ok := unparen(p[1]).(*ast.Ident); ok && (lit.Name == "true" || lit.Name == "false") {
						isFalse := (lit.Name == "false") == (v.Op == token.EQL)
						return isFalse, !isFalse
					}
				}
			}
		case token.LAND:
			t1, e1 := boolFalseIn(v.X, name)
			t2, e2 := boolFalseIn(v.Y, name)
			return t1 || t2, e1 && e2
		case token.LOR:
			t1, e1 := boolFalseIn(v.X, name)
			t2, e2 := boolFalseIn(v.Y, name)
			return t1 && t2, e1 || e2
		}
	}
	return false, false
}

// mentionedAlone reports whether name appears in cond as a value of its own (not only
// as the X of a selector: `res.RowsAffected` is not the error `res`).
func mentionedAlone(cond ast.Expr, name string) bool {
	found := false
	var visit func(n ast.Node) bool
	visit = func(n ast.Node) bool {
		if found {
			return false
		}
		switch v := n.(type) {
		case *ast.FuncLit:
			return false
		case *ast.SelectorExpr:
			ast.Inspect(v.X, func(x ast.Node) bool {
				if _, ok := x.(*ast.Ident); ok {
					return false
				}
				return visit(x)
			})
			return false
		case *ast.Ident:
			if v.Name == name {
				found = true
			}
		}
		return true
	}
	ast.Inspect(cond, visit)
	return found
}

func sameNode(a ast.Expr, b ast.Node) bool { return unparen(a) == b }

// nonNilExpr is nonNil for an error-valued selector (`res.Error`).
func nonNilExpr(cond ast.Expr, sel *ast.SelectorExpr) (bool, bool) {
	switch v := unparen(cond).(type) {
	case *ast.UnaryExpr:
		if v.Op == token.NOT {
			t, e := nonNilExpr(v.X, sel)
			return e, t
		}
	case *ast.BinaryExpr:
		switch v.Op {
		case token.NEQ, token.EQL:
			if sameNode(v.X, sel) && isNil(v.Y) || sameNode(v.Y, sel) && isNil(v.X) {
				return v.Op == token.NEQ, v.Op == token.EQL
			}
		case token.LAND:
			t1, e1 := nonNilExpr(v.X, sel)
			t2, e2 := nonNilExpr(v.Y, sel)
			return t1 || t2, e1 && e2
		case token.LOR:
			t1, e1 := nonNilExpr(v.X, sel)
			t2, e2 := nonNilExpr(v.Y, sel)
			return t1 && t2, e1 || e2
		}
	case *ast.CallExpr:
		if s, ok := v.Fun.(*ast.SelectorExpr); ok && (s.Sel.Name == "Is" || s.Sel.Name == "As") && len(v.Args) > 0 && sameNode(v.Args[0], sel) {
			return true, false
		}
	}
	return false, false
}

func exprText(e ast.Expr) string {
	var b strings.Builder
	writeExpr(&b, e)
	return b.String()
}

// chainText prints a GORM chain compactly: `db.Model(...).Update(...)`.
func chainText(links []chainLink, root ast.Expr) string {
	var b strings.Builder
	writeExpr(&b, root)
	for _, l := range links {
		b.WriteString("." + l.name + "(...)")
	}
	return b.String()
}

func (c *checker) checkPartialWrites() {
	s := &summarizer{c: c, memo: map[string]*psum{}, busy: map[string]bool{}, roots: map[*Func]bool{}}
	routeFns := map[*Func]bool{}
	for _, r := range c.roots {
		if r.Route != nil {
			routeFns[r.Fn] = true
		}
	}
	for _, root := range c.roots {
		type item struct {
			fn    *Func
			mask  string
			depth int
		}
		queue := []item{{root.Fn, "", 0}}
		seen := map[string]bool{}
		for len(queue) > 0 {
			it := queue[0]
			queue = queue[1:]
			key := fmt.Sprintf("%p|%s", it.fn, it.mask)
			if seen[key] {
				continue
			}
			seen[key] = true
			w := s.newWalker(it.fn, it.mask, it.depth)
			w.run()
			if len(*w.hits) > 0 {
				hits := *w.hits
				sort.SliceStable(hits, func(i, j int) bool {
					if hits[i].commit.line != hits[j].commit.line {
						return hits[i].commit.line < hits[j].commit.line
					}
					return hits[i].line < hits[j].line
				})
				c.partialFinding(root, it.fn, hits[0])
			}
			if it.depth >= c.pol.HelperDepth {
				continue
			}
			for _, cl := range *w.called {
				if routeFns[cl.fn] && cl.fn != root.Fn {
					continue
				}
				queue = append(queue, item{cl.fn, cl.mask, it.depth + 1})
			}
		}
	}
}

func (c *checker) partialFinding(root *Root, fn *Func, h hit) {
	where := ""
	if fn != root.Fn {
		where = " (in " + fn.Name + ")"
	}
	again := ""
	if h.again {
		again = " on a later pass of the same loop"
	}
	message := fmt.Sprintf("%s commits%s at line %d (%s) and can still refuse the request "+
		"afterwards%s at line %d (%s): a refused request leaves the earlier write in the database",
		root.Label(), where, h.commit.line, h.commit.what, again, h.line, h.what)
	hint := "check everything that can refuse the request first, then write; or run the " +
		"writes in `db.Transaction(func(tx *gorm.DB) error { ... })` and return an error " +
		"to refuse, so they roll back (don't call a helper that writes on `db` before the " +
		"last check)"
	c.add("GO807", fn.File, h.commit.line, message, hint, root.Key()+"|"+fn.Name)
}
