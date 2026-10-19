// GO801 ownership: a route that reads or writes an owned table's rows with no owner
// check anywhere in what it runs.
package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"sort"
	"strings"
)

type touch struct {
	table string
	line  int
	fn    *Func
	text  string
}

// tablesOf answers the spec tables a GORM chain reads or writes.
func (c *checker) chainTables(e *Env, links []chainLink) []string {
	var out []string
	add := func(t *T) {
		if md := c.ModelOf(t); md != nil {
			out = append(out, md.Table)
		}
	}
	for _, l := range links {
		switch l.name {
		case "Model":
			if len(l.call.Args) == 1 {
				add(c.m.Infer(e, l.call.Args[0]))
			}
		case "Table":
			if len(l.call.Args) >= 1 {
				if name, ok := c.m.constString(e, l.call.Args[0]); ok {
					if md := c.modelByTable(strings.Fields(name + " ")[0]); md != nil {
						out = append(out, md.Table)
					}
				}
			}
		case "First", "Last", "Take", "Find", "Create", "Save", "Delete", "FirstOrCreate",
			"FirstOrInit", "CreateInBatches", "Scan":
			if len(l.call.Args) >= 1 {
				add(c.m.Infer(e, l.call.Args[0]))
			}
		}
	}
	return out
}

// mentionsColumn reports whether fns filter, compare or set a column of a table: the
// column named in a GORM call's arguments (a condition string, a map or struct
// condition), its Go field compared (== / !=), assigned, or set in a model literal.
func (c *checker) mentionsColumn(fns []*Func, table, column string) bool {
	fields := map[string]bool{}
	for _, md := range c.modelsOfTable(table) {
		if f, ok := md.FieldFor(column); ok {
			fields[f.name] = true
		}
	}
	col := strings.ToLower(column)
	isField := func(x ast.Expr) bool {
		sel, ok := unparen(x).(*ast.SelectorExpr)
		return ok && fields[sel.Sel.Name]
	}
	// a value read from the owner field and carried to the comparison: `owner := row.UserID`,
	// or `_, owner, _ := helper(...)` where `helper` returns the owner field at that result
	// (`return book, shelf.UserID, nil`); a comparison of that name counts as a comparison of
	// the field
	returns := map[*Func]map[int]bool{}
	returnsField := func(callee *Func) map[int]bool {
		if got, ok := returns[callee]; ok {
			return got
		}
		returns[callee] = nil
		if callee.Body == nil {
			return nil
		}
		ce := c.m.EnvOf(callee)
		var out map[int]bool
		ast.Inspect(callee.Body, func(n ast.Node) bool {
			switch v := n.(type) {
			case *ast.FuncLit:
				return false
			case *ast.ReturnStmt:
				for i, r := range v.Results {
					if isField(r) {
						if out == nil {
							out = map[int]bool{}
						}
						out[i] = true
						continue
					}
					if id, ok := unparen(r).(*ast.Ident); ok {
						for _, val := range ce.valuesOf(id.Name) {
							if isField(val) {
								if out == nil {
									out = map[int]bool{}
								}
								out[i] = true
							}
						}
					}
				}
			}
			return true
		})
		returns[callee] = out
		return out
	}
	found := false
	for _, fn := range fns {
		e := c.m.EnvOf(fn)
		carried := map[string]bool{}
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			switch v := n.(type) {
			case *ast.FuncLit:
				return false
			case *ast.AssignStmt:
				if len(v.Lhs) == len(v.Rhs) {
					for i, l := range v.Lhs {
						if id, ok := l.(*ast.Ident); ok && isField(v.Rhs[i]) {
							carried[id.Name] = true
						}
					}
				} else if len(v.Rhs) == 1 {
					if call, ok := unparen(v.Rhs[0]).(*ast.CallExpr); ok {
						if callee := c.m.Callee(e, call); callee != nil {
							got := returnsField(callee)
							for i, l := range v.Lhs {
								if id, ok := l.(*ast.Ident); ok && got[i] {
									carried[id.Name] = true
								}
							}
						}
					}
				}
			}
			return true
		})
		isCarried := func(x ast.Expr) bool {
			id, ok := unparen(x).(*ast.Ident)
			return ok && carried[id.Name]
		}
		inArgs := func(args []ast.Expr) {
			for _, a := range args {
				ast.Inspect(a, func(n ast.Node) bool {
					switch v := n.(type) {
					case *ast.FuncLit:
						return false
					case *ast.BasicLit:
						if s, ok := stringLit(v); ok && containsWord(strings.ToLower(s), col) {
							found = true
						}
					case *ast.Ident:
						if s, ok := c.m.constString(e, v); ok && containsWord(strings.ToLower(s), col) {
							found = true
						}
						if fields[v.Name] {
							found = true
						}
					case *ast.SelectorExpr:
						if s, ok := c.m.constString(e, v); ok && containsWord(strings.ToLower(s), col) {
							found = true
						}
					}
					return true
				})
			}
		}
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if found {
				return false
			}
			switch v := n.(type) {
			case *ast.FuncLit:
				return false
			case *ast.CallExpr:
				if links, _ := c.m.gormChain(e, v); links != nil {
					inArgs(v.Args)
				}
			case *ast.BinaryExpr:
				if (v.Op == token.EQL || v.Op == token.NEQ) && (isField(v.X) || isField(v.Y) || isCarried(v.X) || isCarried(v.Y)) {
					found = true
				}
			case *ast.AssignStmt:
				for _, l := range v.Lhs {
					if isField(l) {
						found = true
					}
				}
			case *ast.CompositeLit:
				if md := c.ModelOf(c.m.Infer(e, v)); md != nil && md.Table == table {
					for _, el := range v.Elts {
						if kv, ok := el.(*ast.KeyValueExpr); ok {
							if id, ok := kv.Key.(*ast.Ident); ok && fields[id.Name] {
								found = true
							}
						}
					}
				}
			}
			return true
		})
		if found {
			return true
		}
	}
	return false
}

// ownerEvidence reports whether fns scope `table` to its owner: its owner column named,
// or (owned through a parent) the parent's evidence.
func (c *checker) ownerEvidence(fns []*Func, table string, depth int) bool {
	if depth < 0 {
		return true // a cycle of parents: do not judge
	}
	if col, ok := c.pol.Owners[table]; ok {
		return c.mentionsColumn(fns, table, col)
	}
	if via, ok := c.pol.OwnerVia[table]; ok {
		return c.ownerEvidence(fns, via[1], depth-1)
	}
	return true
}

func (c *checker) owned(table string) bool {
	if table == c.pol.UsersTable {
		return false
	}
	_, a := c.pol.Owners[table]
	_, b := c.pol.OwnerVia[table]
	return a || b
}

func (c *checker) checkOwnership() {
	if len(c.pol.Owners) == 0 && len(c.pol.OwnerVia) == 0 {
		return
	}
	for _, root := range c.roots {
		r := root.Route
		if r == nil || r.Unreadable || c.adminOnly(root) || c.openRoute(root) {
			continue
		}
		fns, complete := c.reach(append([]*Func{root.Fn}, r.Wraps...), c.pol.HelperDepth, r.Wraps...)
		if !complete {
			continue
		}
		mw, mwComplete := c.reach(append(append([]*Func{}, r.Middleware...), r.Wraps...), c.pol.HelperDepth, r.Wraps...)
		if !mwComplete {
			continue
		}
		var touches []touch
		for _, fn := range fns {
			e := c.m.EnvOf(fn)
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				if _, ok := n.(*ast.FuncLit); ok {
					return false
				}
				call, ok := n.(*ast.CallExpr)
				if !ok {
					return true
				}
				links, root := c.m.gormChain(e, call)
				if links == nil {
					return true
				}
				last := links[len(links)-1].name
				if !gormReads[last] && !gormWrites[last] && last != "FirstOrInit" {
					return true
				}
				for _, t := range c.chainTables(e, links) {
					if c.owned(t) {
						touches = append(touches, touch{t, c.m.Line(call.Pos()), fn, chainText(links, root)})
					}
				}
				return false
			})
		}
		if len(touches) == 0 {
			continue
		}
		all := append(append([]*Func{}, fns...), mw...)
		reported := map[string]bool{}
		sort.SliceStable(touches, func(i, j int) bool { return touches[i].line < touches[j].line })
		for _, t := range touches {
			if reported[t.table] || c.ownerEvidence(all, t.table, 4) {
				continue
			}
			reported[t.table] = true
			c.ownershipFinding(root, t)
		}
	}
}

func (c *checker) ownershipFinding(root *Root, t touch) {
	owner, via := c.pol.Owners[t.table], ""
	table := t.table
	for depth := 0; owner == "" && depth < 4; depth++ {
		v, ok := c.pol.OwnerVia[table]
		if !ok {
			break
		}
		via += fmt.Sprintf(" through %s.%s", table, v[0])
		table = v[1]
		owner = c.pol.Owners[table]
	}
	field := owner
	for _, md := range c.modelsOfTable(table) {
		if f, ok := md.FieldFor(owner); ok {
			field = f.name
		}
	}
	where := ""
	if t.fn != root.Fn {
		where = " (in " + t.fn.Name + ")"
	}
	message := fmt.Sprintf("%s reads or writes %s rows at line %d%s (`%s`), and nothing it runs -- "+
		"the handler, its middleware or the helpers it calls -- filters, compares or sets "+
		"%s.%s (`%s`)%s: another user's rows reach the caller",
		root.Label(), t.table, t.line, where, t.text, table, owner, field, via)
	hint := fmt.Sprintf("scope the query to the caller: `.Where(\"%s = ?\", <the caller's id>)`, "+
		"or load the row and refuse when `row.%s != <the caller's id>`; on create, set `%s` "+
		"from the caller", owner, field, field)
	if via != "" {
		hint = fmt.Sprintf("load the owning %s row and refuse unless its `%s` is the caller "+
			"(`.Where(\"%s = ?\", <the caller's id>)`) before reaching %s rows", table, field,
			owner, t.table)
	}
	c.add("GO801", t.fn.File, t.line, message, hint, root.Key()+"|"+t.table)
}
