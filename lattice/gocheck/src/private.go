// GO802 private columns: a private column reaches a response.
package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"strings"
)

// jsonResponders are the gin.Context methods that serialize their second argument.
var jsonResponders = map[string]bool{
	"JSON": true, "IndentedJSON": true, "SecureJSON": true, "JSONP": true, "AsciiJSON": true,
	"PureJSON": true, "AbortWithStatusJSON": true, "AbortWithStatusPureJSON": true,
	"XML": true, "YAML": true, "TOML": true,
}

type leak struct {
	table, column string
	path          string // the serialized field path ("PasswordHash", "User.PasswordHash")
	ti            *TypeInfo
}

func (c *checker) hasMarshaler(ti *TypeInfo) bool {
	return ti.Pkg.Methods[ti.Name]["MarshalJSON"] != nil
}

// structLeak answers a private column a struct serializes (encoding/json: an exported
// field without `json:"-"`), through its own fields or a nested struct, or nil.
func (c *checker) structLeak(ti *TypeInfo, depth int, seen map[*TypeInfo]bool) *leak {
	if ti == nil || seen[ti] || depth < 0 || c.hasMarshaler(ti) {
		return nil
	}
	seen[ti] = true
	defer delete(seen, ti)
	if md := c.models[ti]; md != nil {
		for _, col := range c.pol.Private[md.Table] {
			f, ok := md.FieldFor(col)
			if !ok || !ast.IsExported(f.name) || jsonHidden(f.tag) {
				continue
			}
			if f.owner != ti && c.hiddenEmbedding(ti, f.owner) {
				continue
			}
			return &leak{table: md.Table, column: col, path: f.name, ti: ti}
		}
	}
	for _, f := range c.m.Fields(ti) {
		if jsonHidden(f.tag) || (!f.embedded && !ast.IsExported(f.name)) {
			continue
		}
		t := c.m.TypeExpr(f.file, f.typ)
		for t != nil && (t.Kind == "slice" || t.Kind == "map") {
			t = t.Elem
		}
		if et := t.rowTypeAny(); et != nil {
			if l := c.structLeak(et, depth-1, seen); l != nil {
				out := *l
				if !f.embedded {
					out.path = f.name + "." + l.path
				}
				out.ti = ti
				return &out
			}
		}
	}
	return nil
}

// hiddenEmbedding reports whether ti embeds `inner` under a `json:"-"` field.
func (c *checker) hiddenEmbedding(ti, inner *TypeInfo) bool {
	for _, f := range c.m.Fields(ti) {
		if f.embedded {
			if et := c.m.TypeExpr(f.file, f.typ).rowTypeAny(); et == inner {
				return jsonHidden(f.tag)
			}
		}
	}
	return false
}

func (c *checker) typeLeak(t *T) *leak {
	for t != nil && (t.Kind == "slice" || t.Kind == "map") {
		t = t.Elem
	}
	if ti := t.rowTypeAny(); ti != nil {
		return c.structLeak(ti, 3, map[*TypeInfo]bool{})
	}
	return nil
}

// privateValue answers the private column a `row.Field` selector reads, or nil.
func (c *checker) privateValue(e *Env, x ast.Expr) *leak {
	sel, ok := unparen(x).(*ast.SelectorExpr)
	if !ok {
		return nil
	}
	if _, pkg := c.m.importOf(e, sel.X); pkg {
		return nil
	}
	t := c.m.Infer(e, sel.X)
	if ti := t.deref().rowTypeAny(); ti != nil {
		if md := c.models[ti]; md != nil {
			col := md.ColumnOfField(sel.Sel.Name)
			for _, p := range c.pol.Private[md.Table] {
				if p == col {
					return &leak{table: md.Table, column: col, path: sel.Sel.Name, ti: ti}
				}
			}
		}
		return nil
	}
	if t.Kind != "unknown" {
		return nil
	}
	// an untyped row: the field name of exactly one model's private column
	var found *leak
	for _, md := range c.models {
		col := md.ColumnOfField(sel.Sel.Name)
		for _, p := range c.pol.Private[md.Table] {
			if p == col {
				if found != nil && found.table != md.Table {
					return nil
				}
				found = &leak{table: md.Table, column: col, path: sel.Sel.Name, ti: md.TI}
			}
		}
	}
	return found
}

type leakAt struct {
	leak
	how string
}

// bodyLeak answers a private column a response body carries.
func (c *checker) bodyLeak(e *Env, x ast.Expr, depth int) *leakAt {
	if depth < 0 || x == nil {
		return nil
	}
	x = unparen(x)
	if l := c.privateValue(e, x); l != nil {
		return &leakAt{*l, fmt.Sprintf("the response carries `%s`", exprText(x))}
	}
	switch v := x.(type) {
	case *ast.UnaryExpr:
		if v.Op == token.AND {
			return c.bodyLeak(e, v.X, depth)
		}
	case *ast.CompositeLit:
		if l := c.typeLeak(c.m.Infer(e, v)); l != nil {
			return &leakAt{*l, fmt.Sprintf("the response serializes a `%s` whose field `%s` has no `json:\"-\"` tag", l.ti.Name, l.path)}
		}
		for _, el := range v.Elts {
			val := el
			if kv, ok := el.(*ast.KeyValueExpr); ok {
				val = kv.Value
			}
			if got := c.bodyLeak(e, val, depth-1); got != nil {
				return got
			}
		}
		return nil
	case *ast.Ident:
		if l := c.typeLeak(c.m.Infer(e, v)); l != nil {
			return &leakAt{*l, fmt.Sprintf("the response serializes `%s`, a `%s` whose field `%s` has no `json:\"-\"` tag", v.Name, l.ti.Name, l.path)}
		}
		for _, val := range e.valuesOf(v.Name) {
			switch unparen(val).(type) {
			case *ast.CompositeLit, *ast.CallExpr, *ast.UnaryExpr:
				if got := c.bodyLeak(e, val, depth-1); got != nil {
					return got
				}
			}
		}
		// resp["secret"] = user.Secret
		var got *leakAt
		ast.Inspect(e.fn.Body, func(n ast.Node) bool {
			if got != nil {
				return false
			}
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			a, ok := n.(*ast.AssignStmt)
			if !ok || len(a.Lhs) != len(a.Rhs) {
				return true
			}
			for i, l := range a.Lhs {
				if ix, ok := l.(*ast.IndexExpr); ok && rootIdent(ix.X) == v.Name {
					if pv := c.privateValue(e, a.Rhs[i]); pv != nil {
						got = &leakAt{*pv, fmt.Sprintf("the response's `%s` is given `%s`", v.Name, exprText(a.Rhs[i]))}
					}
				}
			}
			return true
		})
		return got
	case *ast.CallExpr:
		if l := c.typeLeak(c.m.Infer(e, v)); l != nil {
			return &leakAt{*l, fmt.Sprintf("the response serializes a `%s` (from `%s`) whose field `%s` has no `json:\"-\"` tag", l.ti.Name, exprText(v.Fun), l.path)}
		}
		callee := c.m.Callee(e, v)
		if callee == nil {
			return nil
		}
		ce := c.m.EnvOf(callee)
		var got *leakAt
		ast.Inspect(callee.Body, func(n ast.Node) bool {
			if got != nil {
				return false
			}
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			if r, ok := n.(*ast.ReturnStmt); ok && len(r.Results) > 0 {
				if g := c.bodyLeak(ce, r.Results[0], depth-1); g != nil {
					g.how = fmt.Sprintf("%s (in %s)", g.how, callee.Name)
					got = g
				}
			}
			return true
		})
		return got
	default:
		if l := c.typeLeak(c.m.Infer(e, v)); l != nil {
			return &leakAt{*l, fmt.Sprintf("the response serializes a `%s` whose field `%s` has no `json:\"-\"` tag", l.ti.Name, l.path)}
		}
	}
	return nil
}

func (c *checker) checkPrivate() {
	if len(c.pol.Private) == 0 {
		return
	}
	for _, root := range c.roots {
		if c.adminOnly(root) {
			continue
		}
		fns, _ := c.reach([]*Func{root.Fn}, c.pol.HelperDepth)
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
				sel, ok := call.Fun.(*ast.SelectorExpr)
				if !ok || !jsonResponders[sel.Sel.Name] || len(call.Args) < 2 || !c.m.isCtx(e, sel.X) {
					return true
				}
				l := c.bodyLeak(e, call.Args[1], 3)
				if l == nil {
					return true
				}
				where := ""
				if fn != root.Fn {
					where = " (in " + fn.Name + ")"
				}
				message := fmt.Sprintf("%s answers %s.%s, a private column%s: %s at line %d",
					root.Label(), l.table, l.column, where, l.how, c.m.Line(call.Pos()))
				var hint string
				if strings.Contains(l.how, "json:") {
					hint = fmt.Sprintf("tag the field `json:\"-\"` (`%s` of `%s`, the %s.%s column) "+
						"or answer a response struct without it", lastPart(l.path), fieldOwner(c, l),
						l.table, l.column)
				} else {
					hint = fmt.Sprintf("leave %s.%s out of the response (drop the `%s` value)",
						l.table, l.column, lastPart(l.path))
				}
				c.add("GO802", fn.File, c.m.Line(call.Pos()), message, hint,
					root.Key()+"|"+l.table+"."+l.column)
				return true
			})
		}
	}
}

func lastPart(p string) string {
	if i := strings.LastIndex(p, "."); i >= 0 {
		return p[i+1:]
	}
	return p
}

// fieldOwner names the struct declaring a leak's field.
func fieldOwner(c *checker, l *leakAt) string {
	for _, md := range c.models {
		if md.Table == l.table {
			if f, ok := md.FieldFor(l.column); ok && f.owner != nil {
				return f.owner.Name
			}
		}
	}
	return l.ti.Name
}
