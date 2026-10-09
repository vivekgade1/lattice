// GO807's "nothing written" flag: a local bool a transaction function sets to true only on
// paths that wrote nothing, then return; the refusal under `if flag` follows no commit.
package main

import (
	"go/ast"
	"go/token"
)

// noteTxFlags records, for a transaction call, the flags its function sets only where it
// wrote nothing: refusing where such a flag is true follows none of the commits the call
// added (success).
func (w *pw) noteTxFlags(call *ast.CallExpr, lit *ast.FuncLit, success []int) {
	for _, name := range w.txFlagCandidates(lit) {
		if !w.flagDeclaredOnly(name, call, lit) {
			continue
		}
		f := &flagWalk{w: w, name: name, ok: true}
		f.block(lit.Body.List, false)
		if !f.ok || f.seen != countFlagSets(lit.Body, name) {
			continue
		}
		if w.flags == nil {
			w.flags = map[string][]int{}
		}
		w.flags[name] = append([]int{}, success...)
	}
}

// isFlagSet reports whether st is `name = true` (any name when name is "").
func isFlagSet(st ast.Stmt, name string) (string, bool) {
	a, ok := st.(*ast.AssignStmt)
	if !ok || a.Tok != token.ASSIGN || len(a.Lhs) != 1 || len(a.Rhs) != 1 {
		return "", false
	}
	id, ok := a.Lhs[0].(*ast.Ident)
	if !ok || name != "" && id.Name != name || !isName(a.Rhs[0], "true") {
		return "", false
	}
	return id.Name, true
}

// txFlagCandidates answers the names a transaction function sets to true that it does not
// declare itself: the enclosing function's locals.
func (w *pw) txFlagCandidates(lit *ast.FuncLit) []string {
	declared := map[string]bool{}
	for _, p := range fieldList(lit.Type.Params) {
		declared[p.name] = true
	}
	var out []string
	seen := map[string]bool{}
	ast.Inspect(lit.Body, func(n ast.Node) bool {
		switch v := n.(type) {
		case *ast.AssignStmt:
			if v.Tok == token.DEFINE {
				for _, l := range v.Lhs {
					if id, ok := l.(*ast.Ident); ok {
						declared[id.Name] = true
					}
				}
			}
			if name, ok := isFlagSet(v, ""); ok && !seen[name] {
				seen[name] = true
				out = append(out, name)
			}
		case *ast.ValueSpec:
			for _, id := range v.Names {
				declared[id.Name] = true
			}
		}
		return true
	})
	var keep []string
	for _, name := range out {
		if !declared[name] {
			keep = append(keep, name)
		}
	}
	return keep
}

// flagDeclaredOnly reports whether name is a bool the enclosing function declares false
// before the transaction and never assigns, or takes the address of, anywhere else (the
// transaction's function only sets it to true), with the transaction outside any loop.
func (w *pw) flagDeclaredOnly(name string, call *ast.CallExpr, lit *ast.FuncLit) bool {
	declared, other := false, false
	inLoop := false
	var visit func(n ast.Node) bool
	visit = func(n ast.Node) bool {
		if other {
			return false
		}
		switch v := n.(type) {
		case *ast.FuncLit:
			if v == lit {
				// inside the transaction: only `name = true`, no address taken
				ast.Inspect(v.Body, func(x ast.Node) bool {
					switch y := x.(type) {
					case *ast.AssignStmt:
						for _, l := range y.Lhs {
							if isName(l, name) {
								if _, ok := isFlagSet(y, name); !ok {
									other = true
								}
							}
						}
					case *ast.UnaryExpr:
						if y.Op == token.AND && isName(y.X, name) {
							other = true
						}
					}
					return true
				})
				return false
			}
		case *ast.ForStmt, *ast.RangeStmt:
			if n.Pos() <= call.Pos() && call.End() <= n.End() {
				inLoop = true
			}
		case *ast.ValueSpec:
			for i, id := range v.Names {
				if id.Name != name {
					continue
				}
				if declared || v.Pos() > call.Pos() {
					other = true
					break
				}
				tid, ok := v.Type.(*ast.Ident)
				switch {
				case len(v.Values) == 0 && ok && tid.Name == "bool":
					declared = true
				case i < len(v.Values) && isName(v.Values[i], "false") && (v.Type == nil || ok && tid.Name == "bool"):
					declared = true
				default:
					other = true
				}
			}
		case *ast.AssignStmt:
			for i, l := range v.Lhs {
				if !isName(l, name) {
					continue
				}
				if v.Tok == token.DEFINE && !declared && v.Pos() < call.Pos() && len(v.Lhs) == len(v.Rhs) &&
					isName(v.Rhs[i], "false") {
					declared = true
					continue
				}
				other = true
			}
		case *ast.IncDecStmt:
			if isName(v.X, name) {
				other = true
			}
		case *ast.UnaryExpr:
			if v.Op == token.AND && isName(v.X, name) {
				other = true
			}
		}
		return true
	}
	ast.Inspect(w.fn.Body, visit)
	return declared && !other && !inLoop
}

func countFlagSets(body *ast.BlockStmt, name string) int {
	n := 0
	ast.Inspect(body, func(x ast.Node) bool {
		if st, ok := x.(ast.Stmt); ok {
			if _, ok := isFlagSet(st, name); ok {
				n++
			}
		}
		return true
	})
	return n
}

// flagWalk follows a transaction function's paths, tracking whether one may have written,
// and checks that each `name = true` sits on a path that wrote nothing and is followed by
// a return.
type flagWalk struct {
	w    *pw
	name string
	ok   bool
	seen int
}

// block answers whether the paths reaching the list's end may have written, and whether
// any does.
func (f *flagWalk) block(list []ast.Stmt, wrote bool) (bool, bool) {
	for i, st := range list {
		if _, ok := isFlagSet(st, f.name); ok {
			f.seen++
			if wrote || i+1 >= len(list) {
				f.ok = false
				return wrote, false
			}
			r, ok := list[i+1].(*ast.ReturnStmt)
			if !ok || f.writes(r, 3) {
				f.ok = false
			}
			return wrote, false
		}
		var reach bool
		wrote, reach = f.stmt(st, wrote)
		if !reach {
			return wrote, false
		}
	}
	return wrote, true
}

func (f *flagWalk) stmt(st ast.Stmt, wrote bool) (bool, bool) {
	switch v := st.(type) {
	case *ast.BlockStmt:
		return f.block(v.List, wrote)
	case *ast.ReturnStmt:
		return wrote, false
	case *ast.ExprStmt:
		if isTerminal(f.w.m, f.w.env, v.X) {
			return wrote, false
		}
	case *ast.IfStmt:
		if v.Init != nil {
			if countFlagSetsIn(v.Init, f.name) > 0 {
				f.ok = false
			}
			wrote = wrote || f.writes(v.Init, 3)
		}
		wrote = wrote || f.writes(v.Cond, 3)
		w1, r1 := f.block(v.Body.List, wrote)
		w2, r2 := wrote, true
		if v.Else != nil {
			w2, r2 = f.stmt(v.Else, wrote)
		}
		return r1 && w1 || r2 && w2, r1 || r2
	}
	// anything else (a loop, a switch, ...) must not set the flag; it may write
	if countFlagSetsIn(st, f.name) > 0 {
		f.ok = false
	}
	return wrote || f.writes(st, 3), true
}

func countFlagSetsIn(n ast.Node, name string) int {
	c := 0
	ast.Inspect(n, func(x ast.Node) bool {
		if st, ok := x.(ast.Stmt); ok {
			if _, ok := isFlagSet(st, name); ok {
				c++
			}
		}
		return true
	})
	return c
}

// writes reports whether n may write: a call of a GORM write method's name (on any
// receiver), a commit, or a module function whose body may (to depth; deeper: assumed).
func (f *flagWalk) writes(n ast.Node, depth int) bool {
	return writesIn(f.w.m, f.w.env, n, depth)
}

func writesIn(m *Module, e *Env, n ast.Node, depth int) bool {
	if n == nil {
		return false
	}
	found := false
	ast.Inspect(n, func(x ast.Node) bool {
		if found {
			return false
		}
		call, ok := x.(*ast.CallExpr)
		if !ok {
			return true
		}
		if sel, ok := call.Fun.(*ast.SelectorExpr); ok && (gormWrites[sel.Sel.Name] || sel.Sel.Name == "Commit") {
			found = true
			return false
		}
		callee := m.Callee(e, call)
		if callee == nil || callee.Body == nil || callee == e.fn {
			return true
		}
		if depth <= 0 || writesIn(m, m.EnvOf(callee), callee.Body, depth-1) {
			found = true
			return false
		}
		return true
	})
	return found
}

// flagTrueIn answers whether cond's truth (then) or falsehood (els) implies the bool name
// is true.
func flagTrueIn(cond ast.Expr, name string) (then, els bool) {
	switch v := unparen(cond).(type) {
	case *ast.Ident:
		if v.Name == name {
			return true, false
		}
	case *ast.UnaryExpr:
		if v.Op == token.NOT {
			t, e := flagTrueIn(v.X, name)
			return e, t
		}
	case *ast.BinaryExpr:
		switch v.Op {
		case token.EQL, token.NEQ:
			for _, p := range [][2]ast.Expr{{v.X, v.Y}, {v.Y, v.X}} {
				if isName(p[0], name) {
					if lit, ok := unparen(p[1]).(*ast.Ident); ok && (lit.Name == "true" || lit.Name == "false") {
						isTrue := (lit.Name == "true") == (v.Op == token.EQL)
						return isTrue, !isTrue
					}
				}
			}
		case token.LAND:
			t1, e1 := flagTrueIn(v.X, name)
			t2, e2 := flagTrueIn(v.Y, name)
			return t1 || t2, e1 && e2
		case token.LOR:
			t1, e1 := flagTrueIn(v.X, name)
			t2, e2 := flagTrueIn(v.Y, name)
			return t1 && t2, e1 || e2
		}
	}
	return false, false
}
