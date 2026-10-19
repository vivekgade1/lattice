package main

import "go/ast"

// reach answers the functions that start from `start` run: each of them and the module
// functions they call, to depth levels; complete is false when some call goes to code the
// checker cannot read that could matter (a method of an unknown or multiply-implemented
// receiver, or an outside package handed the request, the database or a row).
func (c *checker) reach(start []*Func, depth int, wraps ...*Func) ([]*Func, bool) { // K1: wrapper literals in start; their call of a parameter is the bound argument
	type item struct {
		fn *Func
		d  int
	}
	var out []*Func
	complete := true
	seen := map[*Func]bool{}
	queue := []item{}
	for _, f := range start {
		queue = append(queue, item{f, 0})
	}
	for len(queue) > 0 {
		it := queue[0]
		queue = queue[1:]
		if seen[it.fn] {
			continue
		}
		seen[it.fn] = true
		out = append(out, it.fn)
		e := c.m.EnvOf(it.fn)
		ast.Inspect(it.fn.Body, func(n ast.Node) bool {
			if lit, ok := n.(*ast.FuncLit); ok {
				// a literal runs (or not) with its function: read it too
				queue = append(queue, item{c.m.LitFunc(lit, it.fn), it.d})
				return false
			}
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			for _, w := range wraps {
				if id, isID := call.Fun.(*ast.Ident); isID && w == it.fn && e.vars[id.Name] == nil && e.parent.vars[id.Name] != nil {
					return true
				}
			}
			if callee := c.m.Callee(e, call); callee != nil {
				if callee.Lit == nil || callee.Parent != it.fn {
					if it.d >= depth {
						complete = false
					} else {
						queue = append(queue, item{callee, it.d + 1})
					}
				}
				return true
			}
			if c.opaque(e, call) {
				complete = false
			}
			return true
		})
	}
	return out, complete
}

// opaque reports whether a call the checker cannot resolve could check or change what
// a rule judges.
func (c *checker) opaque(e *Env, call *ast.CallExpr) bool {
	m := c.m
	if m.knownExternal(e, call) {
		return false
	}
	if links, _ := m.gormChain(e, call); links != nil {
		return false
	}
	switch f := call.Fun.(type) {
	case *ast.ArrayType, *ast.MapType, *ast.ChanType, *ast.StructType, *ast.InterfaceType:
		return false // a conversion to a type literal ([]byte(x)) runs no code
	case *ast.SelectorExpr:
		_, pkg := m.importOf(e, f.X)
		xt := m.Infer(e, f.X)
		if pkg || xt.Kind == "ext" || xt.Kind == "unknown" && c.outside(e, f.X, 4) {
			// another module's function, or (K2) a method of an outside type: opaque only
			// when handed the request, the database or a row
			for _, a := range call.Args {
				t := m.Infer(e, a)
				if t.is("gin.Context") || t.is("gorm.DB") || c.ModelOf(t) != nil {
					return true
				}
			}
			return false
		}
		if t := m.Infer(e, f.X); t.Kind == "basic" || t.Kind == "error" {
			return false
		}
		return true
	case *ast.Ident:
		// a local function value we cannot read
		if _, local := e.lookup(f.Name); local {
			return true
		}
		return false
	}
	return true
}

// outside (K2): x is rooted at an outside package or value (through selectors and calls), or at a name bound only to such (`mac := hmac.New(...)`).
func (c *checker) outside(e *Env, x ast.Expr, depth int) bool {
	switch v := unparen(x).(type) {
	case *ast.SelectorExpr:
		if path, pkg := c.m.importOf(e, v.X); pkg {
			return c.m.ByImport[path] == nil
		}
		return c.outside(e, v.X, depth)
	case *ast.CallExpr:
		return c.outside(e, v.Fun, depth)
	case *ast.Ident:
		if c.m.Infer(e, v).Kind == "ext" {
			return true
		}
		vals := e.valuesOf(v.Name)
		if vi, ok := e.fn.File.Pkg.Vars[v.Name]; ok && len(vals) == 0 && vi.value != nil {
			vals = []ast.Expr{vi.value}
		}
		for _, val := range vals {
			if depth == 0 || !c.outside(e, val, depth-1) {
				return false
			}
		}
		return len(vals) > 0
	}
	return false
}
