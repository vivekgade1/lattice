package main

import "go/ast"

// reach answers the functions that start from `start` run: each of them and the module
// functions they call, to depth levels; complete is false when some call goes to code the
// checker cannot read that could matter (a method of an unknown or multiply-implemented
// receiver, or an outside package handed the request, the database or a row).
func (c *checker) reach(start []*Func, depth int) ([]*Func, bool) {
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
	case *ast.SelectorExpr:
		if _, pkg := m.importOf(e, f.X); pkg {
			// another module's function: opaque only when handed the request, the database
			// or a row
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
