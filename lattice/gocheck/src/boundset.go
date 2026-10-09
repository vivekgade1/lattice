// GO803's dominating checks: a request value reached only where it is one of a fixed set
// of constants, or matches an anchored regular expression of finite length.
package main

import (
	"go/ast"
	"go/token"
	"regexp/syntax"
)

// maxRegexLen is the longest match an anchored pattern may allow and still bound a value.
const maxRegexLen = 100000

// setBounded reports whether the code at pos in fn is reached only where the value match
// reads is bounded by a check that dominates it: a refusal (a branch that returns) taken
// whenever the value is not one of a fixed set of constants or does not match a finite
// anchored regex, or a branch, switch case or else that is entered only when it is.
func (b *boundScan) setBounded(fn *Func, e *Env, pos token.Pos, match func(ast.Expr) bool) bool {
	if !pos.IsValid() {
		return false
	}
	// the nodes enclosing pos, outermost first, up to the innermost function literal
	var path []ast.Node
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if n == nil || pos < n.Pos() || pos >= n.End() {
			return false
		}
		if _, ok := n.(*ast.FuncLit); ok {
			path = nil
		}
		path = append(path, n)
		return true
	})
	for i, n := range path {
		var child ast.Node
		if i+1 < len(path) {
			child = path[i+1]
		}
		switch v := n.(type) {
		case *ast.BlockStmt:
			if b.precededByCheck(e, v.List, child, match) {
				return true
			}
		case *ast.CaseClause:
			if b.precededByCheck(e, v.Body, child, match) {
				return true
			}
			// a case of `switch x` listing only constants
			if i > 1 {
				if sw, ok := path[i-2].(*ast.SwitchStmt); ok && v.List != nil && containsNode(v.Body, child) &&
					b.switchOn(e, sw, match) && b.allConst(e, v.List) {
					return true
				}
			}
		case *ast.CommClause:
			if b.precededByCheck(e, v.Body, child, match) {
				return true
			}
		case *ast.IfStmt:
			if child == nil {
				continue
			}
			thenIn, elseIn := b.setCond(e, v.Cond, match, 2)
			if child == v.Body && thenIn || v.Else != nil && child == v.Else && elseIn {
				return true
			}
		}
	}
	return false
}

func containsNode(list []ast.Stmt, n ast.Node) bool {
	for _, s := range list {
		if s == n {
			return true
		}
	}
	return false
}

// precededByCheck reports whether a statement of list before child is a check after
// which only bounded values go on.
func (b *boundScan) precededByCheck(e *Env, list []ast.Stmt, child ast.Node, match func(ast.Expr) bool) bool {
	for _, st := range list {
		if st == child {
			return false
		}
		switch v := st.(type) {
		case *ast.IfStmt:
			thenIn, elseIn := b.setCond(e, v.Cond, match, 2)
			// `if x != A && x != B { refuse; return }`
			if elseIn && terminates(b.c.m, e, v.Body.List) {
				return true
			}
			// `if x == A || x == B { ... } else { refuse; return }`
			if thenIn && v.Else != nil && terminatesStmt(b.c.m, e, v.Else) {
				return true
			}
		case *ast.SwitchStmt:
			// `switch x { case A, B: ...; default: refuse; return }`
			if v.Init == nil && b.switchOn(e, v, match) && b.switchRefusesOthers(e, v) {
				return true
			}
		}
	}
	return false
}

// terminates reports whether a statement list always leaves the function: it ends in a
// return, a panic or a process exit.
func terminates(m *Module, e *Env, list []ast.Stmt) bool {
	if len(list) == 0 {
		return false
	}
	return terminatesStmt(m, e, list[len(list)-1])
}

func terminatesStmt(m *Module, e *Env, st ast.Stmt) bool {
	switch v := st.(type) {
	case *ast.ReturnStmt:
		return true
	case *ast.ExprStmt:
		return isTerminal(m, e, v.X)
	case *ast.BlockStmt:
		return terminates(m, e, v.List)
	case *ast.IfStmt:
		return v.Else != nil && terminates(m, e, v.Body.List) && terminatesStmt(m, e, v.Else)
	}
	return false
}

// switchOn reports whether a switch's tag is the value.
func (b *boundScan) switchOn(e *Env, sw *ast.SwitchStmt, match func(ast.Expr) bool) bool {
	return sw.Tag != nil && match(sw.Tag)
}

// switchRefusesOthers reports whether a value switch has a default that leaves the
// function and only constant cases that go on.
func (b *boundScan) switchRefusesOthers(e *Env, sw *ast.SwitchStmt) bool {
	hasDefault := false
	for _, cl := range sw.Body.List {
		cc := cl.(*ast.CaseClause)
		if cc.List == nil {
			if !terminates(b.c.m, e, cc.Body) {
				return false
			}
			hasDefault = true
			continue
		}
		if !b.allConst(e, cc.List) && !terminates(b.c.m, e, cc.Body) {
			return false
		}
		if n := len(cc.Body); n > 0 {
			if br, ok := cc.Body[n-1].(*ast.BranchStmt); ok && br.Tok == token.FALLTHROUGH {
				return false
			}
		}
	}
	return hasDefault
}

func (b *boundScan) allConst(e *Env, xs []ast.Expr) bool {
	for _, x := range xs {
		if !isConstExpr(b.c.m, e, x) {
			return false
		}
	}
	return len(xs) > 0
}

// isConstExpr reports whether x is a constant the checker can read: a literal, a module
// constant, a conversion of one.
func isConstExpr(m *Module, e *Env, x ast.Expr) bool {
	x = unparen(x)
	if u, ok := x.(*ast.UnaryExpr); ok && (u.Op == token.SUB || u.Op == token.ADD) {
		x = unparen(u.X)
	}
	if _, ok := x.(*ast.BasicLit); ok {
		return true
	}
	if _, ok := m.constString(e, x); ok {
		return true
	}
	return m.constInt(e, x) >= 0
}

// setCond answers whether cond's truth (thenIn) or its falsehood (elseIn) implies the
// value is bounded: equal to one of a fixed set of constants, or matching a finite
// anchored regex.
func (b *boundScan) setCond(e *Env, cond ast.Expr, match func(ast.Expr) bool, depth int) (thenIn, elseIn bool) {
	m := b.c.m
	switch v := unparen(cond).(type) {
	case *ast.UnaryExpr:
		if v.Op == token.NOT {
			t, el := b.setCond(e, v.X, match, depth)
			return el, t
		}
	case *ast.BinaryExpr:
		switch v.Op {
		case token.LAND:
			t1, e1 := b.setCond(e, v.X, match, depth)
			t2, e2 := b.setCond(e, v.Y, match, depth)
			return t1 || t2, e1 && e2
		case token.LOR:
			t1, e1 := b.setCond(e, v.X, match, depth)
			t2, e2 := b.setCond(e, v.Y, match, depth)
			return t1 && t2, e1 || e2
		case token.EQL, token.NEQ:
			if match(v.X) && isConstExpr(m, e, v.Y) || match(v.Y) && isConstExpr(m, e, v.X) {
				return v.Op == token.EQL, v.Op == token.NEQ
			}
		}
	case *ast.CallExpr:
		if b.inSetCall(e, v, match, depth) {
			return true, false
		}
	}
	return false, false
}

// inSetCall reports whether a call is true only for a bounded value: `slices.Contains`
// of a constant list, a finite anchored regex's `MatchString`, or a module helper whose
// body is such a check of its parameter.
func (b *boundScan) inSetCall(e *Env, call *ast.CallExpr, match func(ast.Expr) bool, depth int) bool {
	m := b.c.m
	if sel, ok := call.Fun.(*ast.SelectorExpr); ok {
		if path, pkg := m.importOf(e, sel.X); pkg {
			switch {
			case path == "slices" && sel.Sel.Name == "Contains" && len(call.Args) == 2:
				return match(call.Args[1]) && b.constList(e, call.Args[0])
			case path == "regexp" && sel.Sel.Name == "MatchString" && len(call.Args) == 2:
				pat, ok := m.constString(e, call.Args[0])
				return ok && match(call.Args[1]) && finiteAnchored(pat)
			}
		}
		if sel.Sel.Name == "MatchString" && len(call.Args) == 1 && match(call.Args[0]) {
			return b.finiteRegexValue(e, sel.X)
		}
	}
	if depth <= 0 {
		return false
	}
	callee := m.Callee(e, call)
	if callee == nil || callee.Lit != nil || callee == e.fn {
		return false
	}
	rs := callee.Results()
	if len(rs) != 1 {
		return false
	}
	if id, ok := rs[0].typ.(*ast.Ident); !ok || id.Name != "bool" {
		return false
	}
	ps := callee.Params()
	for i, a := range call.Args {
		if i >= len(ps) || ps[i].name == "" || !match(a) {
			continue
		}
		if b.helperChecks(callee, ps[i].name, depth-1) {
			return true
		}
	}
	return false
}

// helperChecks reports whether a bool helper returns true only for a bounded value of its
// parameter p: `return p == A || p == B` (or any check setCond reads), or
// `switch p { case A, B: return true }; return false`.
func (b *boundScan) helperChecks(fn *Func, p string, depth int) bool {
	if fn.Body == nil {
		return false
	}
	e := b.c.m.EnvOf(fn)
	isP := func(x ast.Expr) bool { return isName(x, p) }
	list := fn.Body.List
	if len(list) == 1 {
		if r, ok := list[0].(*ast.ReturnStmt); ok && len(r.Results) == 1 {
			t, _ := b.setCond(e, r.Results[0], isP, depth)
			return t
		}
	}
	// the switch form, with an optional `return false` after it
	if len(list) < 1 || len(list) > 2 {
		return false
	}
	sw, ok := list[0].(*ast.SwitchStmt)
	if !ok || sw.Init != nil || sw.Tag == nil || !isP(sw.Tag) {
		return false
	}
	retFalse := func(st ast.Stmt) bool {
		r, ok := st.(*ast.ReturnStmt)
		return ok && len(r.Results) == 1 && isName(r.Results[0], "false")
	}
	retTrue := func(st ast.Stmt) bool {
		r, ok := st.(*ast.ReturnStmt)
		return ok && len(r.Results) == 1 && isName(r.Results[0], "true")
	}
	hasDefault := false
	for _, cl := range sw.Body.List {
		cc := cl.(*ast.CaseClause)
		if len(cc.Body) != 1 {
			return false
		}
		if cc.List == nil {
			hasDefault = true
			if !retFalse(cc.Body[0]) {
				return false
			}
			continue
		}
		if retFalse(cc.Body[0]) {
			continue
		}
		if !retTrue(cc.Body[0]) || !b.allConst(e, cc.List) {
			return false
		}
	}
	if len(list) == 2 {
		return retFalse(list[1])
	}
	return hasDefault
}

// constList reports whether x is a slice or array literal of constants (inline, or a
// package-level var of this module holding one).
func (b *boundScan) constList(e *Env, x ast.Expr) bool {
	m := b.c.m
	x = unparen(x)
	ctx := e
	if id, ok := x.(*ast.Ident); ok {
		if _, local := e.lookup(id.Name); local {
			return false
		}
		vi, ok := e.fn.File.Pkg.Vars[id.Name]
		if !ok || vi.value == nil {
			return false
		}
		x, ctx = unparen(vi.value), fileEnv(m, vi.file)
	}
	cl, ok := x.(*ast.CompositeLit)
	if !ok {
		return false
	}
	switch cl.Type.(type) {
	case *ast.ArrayType:
	default:
		return false
	}
	for _, el := range cl.Elts {
		if _, kv := el.(*ast.KeyValueExpr); kv || !isConstExpr(m, ctx, el) {
			return false
		}
	}
	return len(cl.Elts) > 0
}

// fileEnv answers an environment with no locals for reading a file's package-level
// expressions.
func fileEnv(m *Module, f *File) *Env {
	return &Env{fn: &Func{File: f, Body: &ast.BlockStmt{}}}
}

// finiteRegexValue reports whether x is a compiled regex whose pattern is finite and
// anchored: `regexp.MustCompile("...")` inline, through a package var of this module, or a
// local assigned exactly once.
func (b *boundScan) finiteRegexValue(e *Env, x ast.Expr) bool {
	m := b.c.m
	x = unparen(x)
	ctx := e
	switch v := x.(type) {
	case *ast.Ident:
		if _, local := e.lookup(v.Name); local {
			vals := e.valuesOf(v.Name)
			if len(vals) != 1 {
				return false
			}
			x = unparen(vals[0])
		} else if vi, ok := e.fn.File.Pkg.Vars[v.Name]; ok && vi.value != nil {
			x, ctx = unparen(vi.value), fileEnv(m, vi.file)
		} else {
			return false
		}
	case *ast.SelectorExpr:
		path, ok := m.importOf(e, v.X)
		if !ok {
			return false
		}
		pkg := m.ByImport[path]
		if pkg == nil {
			return false
		}
		vi, ok := pkg.Vars[v.Sel.Name]
		if !ok || vi.value == nil {
			return false
		}
		x, ctx = unparen(vi.value), fileEnv(m, vi.file)
	}
	call, ok := x.(*ast.CallExpr)
	if !ok || len(call.Args) != 1 {
		return false
	}
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || sel.Sel.Name != "MustCompile" {
		return false
	}
	if path, pkg := m.importOf(ctx, sel.X); !pkg || path != "regexp" {
		return false
	}
	pat, ok := m.constString(ctx, call.Args[0])
	return ok && finiteAnchored(pat)
}

// finiteAnchored reports whether a pattern (Go's RE2 syntax) matches only whole strings
// (anchored at both ends: `^...$`, `\A...\z`) of at most maxRegexLen characters.
func finiteAnchored(pat string) bool {
	re, err := syntax.Parse(pat, syntax.Perl)
	if err != nil {
		return false
	}
	if !beginsText(re) || !endsText(re) {
		return false
	}
	n := maxLen(re)
	return n >= 0 && n <= maxRegexLen
}

func beginsText(re *syntax.Regexp) bool {
	switch re.Op {
	case syntax.OpBeginText:
		return true
	case syntax.OpCapture:
		return beginsText(re.Sub[0])
	case syntax.OpConcat:
		return len(re.Sub) > 0 && beginsText(re.Sub[0])
	case syntax.OpAlternate:
		for _, s := range re.Sub {
			if !beginsText(s) {
				return false
			}
		}
		return len(re.Sub) > 0
	}
	return false
}

func endsText(re *syntax.Regexp) bool {
	switch re.Op {
	case syntax.OpEndText:
		return true
	case syntax.OpCapture:
		return endsText(re.Sub[0])
	case syntax.OpConcat:
		return len(re.Sub) > 0 && endsText(re.Sub[len(re.Sub)-1])
	case syntax.OpAlternate:
		for _, s := range re.Sub {
			if !endsText(s) {
				return false
			}
		}
		return len(re.Sub) > 0
	}
	return false
}

// maxLen answers the longest match of re in characters, -1 when it is unbounded (or
// beyond maxRegexLen).
func maxLen(re *syntax.Regexp) int {
	capped := func(n int) int {
		if n > maxRegexLen {
			return -1
		}
		return n
	}
	switch re.Op {
	case syntax.OpNoMatch, syntax.OpEmptyMatch, syntax.OpBeginLine, syntax.OpEndLine,
		syntax.OpBeginText, syntax.OpEndText, syntax.OpWordBoundary, syntax.OpNoWordBoundary:
		return 0
	case syntax.OpLiteral:
		return len(re.Rune)
	case syntax.OpCharClass, syntax.OpAnyCharNotNL, syntax.OpAnyChar:
		return 1
	case syntax.OpCapture, syntax.OpQuest:
		return maxLen(re.Sub[0])
	case syntax.OpStar, syntax.OpPlus:
		if maxLen(re.Sub[0]) == 0 {
			return 0
		}
		return -1
	case syntax.OpRepeat:
		n := maxLen(re.Sub[0])
		if n < 0 {
			return -1
		}
		if n == 0 {
			return 0
		}
		if re.Max < 0 {
			return -1
		}
		if re.Max > maxRegexLen/n {
			return -1
		}
		return capped(re.Max * n)
	case syntax.OpConcat:
		total := 0
		for _, s := range re.Sub {
			n := maxLen(s)
			if n < 0 {
				return -1
			}
			total += n
			if total > maxRegexLen {
				return -1
			}
		}
		return total
	case syntax.OpAlternate:
		best := 0
		for _, s := range re.Sub {
			n := maxLen(s)
			if n < 0 {
				return -1
			}
			if n > best {
				best = n
			}
		}
		return best
	}
	return -1
}
