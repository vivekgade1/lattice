// GO803 unbounded inputs: a request value written to the database, or used as a limit,
// with no bound.
package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"reflect"
	"strings"
)

var bindMethods = map[string]bool{
	"ShouldBind": true, "ShouldBindJSON": true, "ShouldBindQuery": true, "ShouldBindWith": true,
	"ShouldBindBodyWith": true, "ShouldBindBodyWithJSON": true, "Bind": true, "BindJSON": true,
	"BindQuery": true, "BindWith": true, "MustBindWith": true, "ShouldBindXML": true,
	"ShouldBindYAML": true, "ShouldBindTOML": true, "ShouldBindForm": true,
}

var scalarReaders = map[string]bool{
	"Query": true, "DefaultQuery": true, "PostForm": true, "DefaultPostForm": true,
}

// bounded validator rules (go-playground/validator, Gin's binding tag)
var strBounds = []string{"max=", "len=", "lte=", "lt=", "oneof=", "eq=", "email", "uuid",
	"ulid", "datetime=", "e164", "iso3166", "iso4217", "hexcolor", "boolean", "ip", "mac",
	"cidr", "number", "numeric"}
var numBounds = []string{"max=", "len=", "lte=", "lt=", "oneof=", "eq="}

func tagBounded(tag string, num bool) bool {
	st := reflect.StructTag(tag)
	rules := st.Get("binding") + "," + st.Get("validate")
	marks := strBounds
	if num {
		marks = numBounds
	}
	for _, r := range strings.Split(rules, ",") {
		r = strings.TrimSpace(r)
		for _, m := range marks {
			if strings.HasPrefix(r, m) || r == strings.TrimSuffix(m, "=") && !strings.HasSuffix(m, "=") {
				return true
			}
		}
	}
	return false
}

// scalarKind answers "str" or "num" for a builtin string or number type (a pointer to
// one too), "" otherwise. A named type (an enum) is not judged.
func scalarKind(t *T) string {
	if t == nil || t.Kind != "basic" {
		return ""
	}
	switch {
	case t.Basic == "string":
		return "str"
	case strings.HasPrefix(t.Basic, "int") || strings.HasPrefix(t.Basic, "uint") ||
		strings.HasPrefix(t.Basic, "float"):
		return "num"
	}
	return ""
}

// reqVal is a request value: a bound struct's field, or a query/form string.
type reqVal struct {
	desc  string // `req.Name`, `c.Query("name")`
	name  string // the name holding it (a bound struct or a local)
	field string // "" for a scalar
	kind  string
	key   string
}

type boundScan struct {
	c    *checker
	root *Root
	fns  []*Func
	// a helper's parameters handed an unbounded request number: name -> how it reads
	seeds map[*Func]map[string]string
}

func (c *checker) checkBounds() {
	for _, root := range c.roots {
		fns, _ := c.reach([]*Func{root.Fn}, c.pol.HelperDepth)
		b := &boundScan{c: c, root: root, fns: fns, seeds: map[*Func]map[string]string{}}
		for _, fn := range fns {
			b.function(fn)
		}
	}
}

// boundVars answers the names a function binds the request into (`c.ShouldBindJSON(&req)`)
// and the bind call of each.
func (b *boundScan) boundVars(fn *Func, e *Env) map[string]*ast.CallExpr {
	out := map[string]*ast.CallExpr{}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if !ok || !bindMethods[sel.Sel.Name] || len(call.Args) == 0 || !b.c.m.isCtx(e, sel.X) {
			return true
		}
		if id, ok := stripAddr(call.Args[0]).(*ast.Ident); ok {
			out[id.Name] = call
		}
		return true
	})
	return out
}

// handedOn reports whether a bound name is passed to code other than the bind, a GORM
// write or a response (it may be validated there), or has a method called on it.
func (b *boundScan) handedOn(fn *Func, e *Env, name string, bind *ast.CallExpr) bool {
	found := false
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if found {
			return false
		}
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		call, ok := n.(*ast.CallExpr)
		if !ok || call == bind {
			return true
		}
		if sel, ok := call.Fun.(*ast.SelectorExpr); ok {
			if isName(sel.X, name) {
				found = true // a method of the request (req.Validate())
				return false
			}
			if b.c.m.isGorm(e, sel.X) || b.c.m.isCtx(e, sel.X) {
				return true
			}
		}
		for _, a := range call.Args {
			if id, ok := stripAddr(a).(*ast.Ident); ok && id.Name == name {
				if b.c.m.knownExternal(e, call) {
					continue
				}
				found = true
			}
		}
		return true
	})
	return found
}

// handBounded reports whether the code compares a request value: `len(x)` compared, or
// x itself in an ordering comparison or a min/max.
func (b *boundScan) handBounded(match func(ast.Expr) bool) bool {
	found := false
	for _, fn := range b.fns {
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if found {
				return false
			}
			switch v := n.(type) {
			case *ast.BinaryExpr:
				switch v.Op {
				case token.LSS, token.GTR, token.LEQ, token.GEQ, token.EQL, token.NEQ:
					for i, side := range []ast.Expr{v.X, v.Y} {
						other := []ast.Expr{v.Y, v.X}[i]
						if lowerBound(other) {
							continue // `n <= 0`, `len(s) == 0`: not an upper bound
						}
						side = unparen(side)
						if call, ok := side.(*ast.CallExpr); ok && len(call.Args) == 1 {
							if lenCall(call) && match(call.Args[0]) {
								found = true
							}
						}
						if v.Op != token.EQL && v.Op != token.NEQ && match(side) {
							found = true
						}
					}
				}
			case *ast.CallExpr:
				if id, ok := v.Fun.(*ast.Ident); ok && (id.Name == "min" || id.Name == "max") {
					for _, a := range v.Args {
						if match(a) {
							found = true
						}
					}
				}
			}
			return true
		})
	}
	return found
}

func lenCall(c *ast.CallExpr) bool {
	switch f := c.Fun.(type) {
	case *ast.Ident:
		return f.Name == "len"
	case *ast.SelectorExpr:
		return f.Sel.Name == "RuneCountInString"
	}
	return false
}

// requestValue answers the request value an expression carries.
func (b *boundScan) requestValue(e *Env, x ast.Expr, bound map[string]*ast.CallExpr, scalars map[string]reqVal) *reqVal {
	x = unparen(x)
	if s, ok := x.(*ast.StarExpr); ok {
		x = unparen(s.X)
	}
	switch v := x.(type) {
	case *ast.SelectorExpr:
		id, ok := v.X.(*ast.Ident)
		if !ok || bound[id.Name] == nil {
			return nil
		}
		t := b.c.m.Infer(e, id).deref()
		ti := t.rowTypeAny()
		if ti == nil {
			return nil
		}
		f := b.c.m.FieldOf(ti, v.Sel.Name, 2)
		if f == nil || f.embedded {
			return nil
		}
		kind := scalarKind(b.c.m.TypeExpr(f.file, f.typ).deref())
		if kind == "" || jsonHidden(f.tag) || tagBounded(f.tag, kind == "num") {
			return nil
		}
		return &reqVal{desc: id.Name + "." + v.Sel.Name, name: id.Name, field: v.Sel.Name,
			kind: kind, key: ti.Name + "." + v.Sel.Name}
	case *ast.Ident:
		if r, ok := scalars[v.Name]; ok {
			return &r
		}
	case *ast.CallExpr:
		// strings.TrimSpace(req.Name), string(req.Name)
		if len(v.Args) >= 1 {
			if sel, ok := v.Fun.(*ast.SelectorExpr); ok {
				if path, ok := b.c.m.importOf(e, sel.X); ok && path == "strings" {
					return b.requestValue(e, v.Args[0], bound, scalars)
				}
			}
			if id, ok := v.Fun.(*ast.Ident); ok && basics[id.Name] && len(v.Args) == 1 {
				return b.requestValue(e, v.Args[0], bound, scalars)
			}
		}
	}
	return nil
}

type written struct {
	rv     reqVal
	target string // "Job.Name" / "jobs.name"
	line   int
	fn     *Func
	pos    token.Pos // where the value is written (a dominating check bounds it)
}

func (b *boundScan) function(fn *Func) {
	c := b.c
	e := c.m.EnvOf(fn)
	bound := b.boundVars(fn, e)
	for name, call := range bound {
		if b.handedOn(fn, e, name, call) {
			delete(bound, name)
		}
	}
	// scalars: name := c.Query("x")
	scalars := map[string]reqVal{}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		a, ok := n.(*ast.AssignStmt)
		if !ok || len(a.Lhs) != 1 || len(a.Rhs) != 1 {
			return true
		}
		id, ok := a.Lhs[0].(*ast.Ident)
		call, ok2 := unparen(a.Rhs[0]).(*ast.CallExpr)
		if !ok || !ok2 {
			return true
		}
		if sel, ok := call.Fun.(*ast.SelectorExpr); ok && scalarReaders[sel.Sel.Name] && c.m.isCtx(e, sel.X) && len(call.Args) > 0 {
			k, _ := c.m.constString(e, call.Args[0])
			scalars[id.Name] = reqVal{desc: fmt.Sprintf("c.%s(%q)", sel.Sel.Name, k), name: id.Name,
				kind: "str", key: "query:" + k}
		}
		return true
	})
	var found []written
	isWriteArg := func(name string) bool { return b.writtenName(fn, e, name, 2) }
	modelField := func(t *T, field string) string {
		if md := c.ModelOf(t); md != nil {
			if col := md.ColumnOfField(field); col != "" {
				return md.Table + "." + col
			}
		}
		return ""
	}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		switch v := n.(type) {
		case *ast.AssignStmt:
			// job.Name = req.Name, with job written
			if len(v.Lhs) != len(v.Rhs) {
				return true
			}
			for i, l := range v.Lhs {
				sel, ok := l.(*ast.SelectorExpr)
				if !ok {
					continue
				}
				row := rootIdent(sel.X)
				if row == "" || bound[row] != nil && c.ModelOf(c.m.Infer(e, sel.X)) == nil {
					continue
				}
				target := modelField(c.m.Infer(e, sel.X), sel.Sel.Name)
				if target == "" || !isWriteArg(row) {
					continue
				}
				if rv := b.requestValue(e, v.Rhs[i], bound, scalars); rv != nil {
					found = append(found, written{*rv, target, c.m.Line(v.Pos()), fn, v.Pos()})
				}
			}
		case *ast.CompositeLit:
			t := c.m.Infer(e, v)
			md := c.ModelOf(t)
			if md == nil {
				return true
			}
			for _, el := range v.Elts {
				kv, ok := el.(*ast.KeyValueExpr)
				if !ok {
					continue
				}
				key, ok := kv.Key.(*ast.Ident)
				if !ok {
					continue
				}
				col := md.ColumnOfField(key.Name)
				if col == "" {
					continue
				}
				if rv := b.requestValue(e, kv.Value, bound, scalars); rv != nil && b.literalWritten(fn, e, v) {
					found = append(found, written{*rv, md.Table + "." + col, c.m.Line(kv.Pos()), fn, kv.Pos()})
				}
			}
		case *ast.CallExpr:
			links, _ := c.m.gormChain(e, v)
			if links == nil {
				return true
			}
			last := links[len(links)-1]
			table := ""
			for _, ts := range c.chainTables(e, links) {
				table = ts
			}
			switch last.name {
			case "Update", "UpdateColumn":
				if len(last.call.Args) == 2 {
					col, _ := c.m.constString(e, last.call.Args[0])
					if rv := b.requestValue(e, last.call.Args[1], bound, scalars); rv != nil && col != "" {
						found = append(found, written{*rv, strings.TrimPrefix(table+"."+col, "."), c.m.Line(v.Pos()), fn, v.Pos()})
					}
				}
			case "Updates", "UpdateColumns":
				if len(last.call.Args) == 1 {
					if cl, ok := stripAddr(last.call.Args[0]).(*ast.CompositeLit); ok && c.ModelOf(c.m.Infer(e, cl)) == nil {
						for _, el := range cl.Elts {
							kv, ok := el.(*ast.KeyValueExpr)
							if !ok {
								continue
							}
							col, ok := c.m.constString(e, kv.Key)
							if !ok {
								continue
							}
							if rv := b.requestValue(e, kv.Value, bound, scalars); rv != nil {
								found = append(found, written{*rv, strings.TrimPrefix(table+"."+col, "."), c.m.Line(kv.Pos()), fn, kv.Pos()})
							}
						}
					}
				}
			}
			// the bound struct itself is a model, written whole
			if gormWrites[last.name] && len(last.call.Args) >= 1 {
				arg := stripAddr(last.call.Args[len(last.call.Args)-1])
				if last.name == "Create" || last.name == "Save" || last.name == "FirstOrCreate" {
					arg = stripAddr(last.call.Args[0])
				}
				if id, ok := arg.(*ast.Ident); ok && bound[id.Name] != nil {
					found = append(found, b.wholeModel(fn, e, id.Name, bound[id.Name], v)...)
				}
			}
		}
		return true
	})
	// request numbers handed to helpers: their parameters are request numbers there
	b.seed(fn, e, bound)
	// the limit: .Limit(n) of a request number
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if !ok || sel.Sel.Name != "Limit" || len(call.Args) != 1 || !c.m.isGorm(e, sel.X) {
			return true
		}
		b.limit(fn, e, call, bound)
		return true
	})
	reported := map[string]bool{}
	for _, w := range found {
		if reported[w.rv.key] {
			continue
		}
		rv := w.rv
		var match func(ast.Expr) bool
		if rv.field != "" {
			match = func(x ast.Expr) bool {
				x = unparen(x)
				if s, ok := x.(*ast.StarExpr); ok {
					x = unparen(s.X)
				}
				sel, ok := x.(*ast.SelectorExpr)
				return ok && rootIdent(sel.X) == rv.name && sel.Sel.Name == rv.field
			}
		} else {
			match = func(x ast.Expr) bool { return isName(x, rv.name) }
		}
		if b.handBounded(match) || b.setBounded(w.fn, e, w.pos, match) {
			continue
		}
		reported[w.rv.key] = true
		b.writeFinding(w)
	}
}

// literalWritten reports whether a model literal reaches a GORM write: directly as its
// argument, or through the name it is assigned to.
func (b *boundScan) literalWritten(fn *Func, e *Env, lit *ast.CompositeLit) bool {
	hit := false
	var holder string
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if hit {
			return false
		}
		switch v := n.(type) {
		case *ast.AssignStmt:
			for i, r := range v.Rhs {
				if stripAddr(r) == lit && i < len(v.Lhs) {
					holder = rootIdent(v.Lhs[i])
				}
			}
		case *ast.ValueSpec:
			for i, r := range v.Values {
				if stripAddr(r) == lit && i < len(v.Names) {
					holder = v.Names[i].Name
				}
			}
		case *ast.CallExpr:
			sel, ok := v.Fun.(*ast.SelectorExpr)
			if ok && gormWrites[sel.Sel.Name] && b.c.m.isGorm(e, sel.X) {
				for _, a := range v.Args {
					if stripAddr(a) == lit {
						hit = true
					}
				}
			}
		}
		return true
	})
	if hit {
		return true
	}
	if holder == "" {
		return false
	}
	return b.writtenName(fn, e, holder, 2)
}

// wholeModel answers the unbounded fields of a bound request struct that is itself a
// model written whole (`c.ShouldBindJSON(&job); db.Create(&job)`), minus the fields the
// code sets after binding.
func (b *boundScan) wholeModel(fn *Func, e *Env, name string, bind, write *ast.CallExpr) []written {
	c := b.c
	t := c.m.Infer(e, ast.NewIdent(name))
	if v, ok := e.lookup(name); ok {
		t = v
	}
	md := c.ModelOf(t)
	if md == nil {
		return nil
	}
	set := map[string]bool{}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		a, ok := n.(*ast.AssignStmt)
		if !ok || a.Pos() < bind.Pos() {
			return true
		}
		for _, l := range a.Lhs {
			if sel, ok := l.(*ast.SelectorExpr); ok && rootIdent(sel.X) == name {
				set[sel.Sel.Name] = true
			}
		}
		return true
	})
	var out []written
	for _, f := range c.m.Fields(md.TI) {
		if f.embedded || set[f.name] || !ast.IsExported(f.name) || jsonHidden(f.tag) {
			continue
		}
		g := gormTag(f.tag)
		if hasKey(g, "-") || hasKey(g, "PRIMARYKEY") || hasKey(g, "PRIMARY_KEY") || f.name == "ID" ||
			hasKey(g, "->") {
			continue
		}
		kind := scalarKind(c.m.TypeExpr(f.file, f.typ).deref())
		if kind == "" || tagBounded(f.tag, kind == "num") {
			continue
		}
		col := columnOf(f)
		out = append(out, written{
			rv:     reqVal{desc: name + "." + f.name, name: name, field: f.name, kind: kind, key: md.TI.Name + "." + f.name},
			target: md.Table + "." + col, line: c.m.Line(write.Pos()), fn: fn, pos: write.Pos(),
		})
	}
	return out
}

func (b *boundScan) writeFinding(w written) {
	rv := w.rv
	where := ""
	if w.fn != b.root.Fn {
		where = " (in " + w.fn.Name + ")"
	}
	what := "the request's `" + rv.desc + "`"
	if rv.field == "" {
		what = "the request value `" + rv.name + "` (" + rv.desc + ")"
	}
	need := "`max` or `len`"
	example := "`binding:\"required,max=200\"`"
	if rv.kind == "num" {
		need = "`max` or `lte`"
		example = "`binding:\"min=0,max=1000\"`"
	}
	message := fmt.Sprintf("%s writes %s to %s%s with no bound: nothing limits it (no %s rule, no length or range check)",
		b.root.Label(), what, w.target, where, need)
	hint := fmt.Sprintf("bound it where the request is read: %s on the field `%s`, or refuse "+
		"the request when `len(%s) > N` (a number: out of range) before the write", example,
		rv.field, rv.desc)
	if rv.field == "" {
		hint = fmt.Sprintf("refuse the request when `len(%s) > N` before the write, or bind it "+
			"into a struct field with %s", rv.name, example)
	}
	b.c.add("GO803", w.fn.File, w.line, message, hint, b.root.Key()+"|"+rv.key)
}

// limit judges `.Limit(n)` of a request number with no upper bound.
func (b *boundScan) limit(fn *Func, e *Env, call *ast.CallExpr, bound map[string]*ast.CallExpr) {
	c := b.c
	arg := unparen(call.Args[0])
	if conv, ok := arg.(*ast.CallExpr); ok && len(conv.Args) == 1 {
		if id, ok := conv.Fun.(*ast.Ident); ok && basics[id.Name] {
			arg = unparen(conv.Args[0])
		}
	}
	desc, key := "", ""
	var match func(ast.Expr) bool
	switch v := arg.(type) {
	case *ast.Ident:
		// n, _ := strconv.Atoi(c.Query("limit")), possibly through a string name, or a
		// parameter a caller handed one
		if from, ok := b.seeds[fn][v.Name]; ok {
			desc, key = from, "limit:"+from
		} else if b.fromQueryNumber(e, v.Name, 2) {
			desc, key = v.Name, "limit:"+v.Name
		} else {
			return
		}
		names := copiesInto(e, v.Name, 3)
		match = func(x ast.Expr) bool {
			id, ok := unparen(x).(*ast.Ident)
			return ok && names[id.Name]
		}
	case *ast.SelectorExpr:
		id, ok := v.X.(*ast.Ident)
		if !ok || bound[id.Name] == nil {
			return
		}
		ti := c.m.Infer(e, id).deref().rowTypeAny()
		if ti == nil {
			return
		}
		f := c.m.FieldOf(ti, v.Sel.Name, 2)
		if f == nil || tagBounded(f.tag, true) {
			return
		}
		desc, key = id.Name+"."+v.Sel.Name, "limit:"+ti.Name+"."+v.Sel.Name
		match = func(x ast.Expr) bool {
			sel, ok := unparen(x).(*ast.SelectorExpr)
			return ok && rootIdent(sel.X) == id.Name && sel.Sel.Name == v.Sel.Name
		}
	default:
		return
	}
	if b.handBounded(match) || b.setBounded(fn, e, call.Pos(), match) {
		return
	}
	where := ""
	if fn != b.root.Fn {
		where = " (in " + fn.Name + ")"
	}
	message := fmt.Sprintf("%s uses the request's `%s` as a `.Limit(...)`%s with no upper bound: a caller can ask for every row",
		b.root.Label(), desc, where)
	hint := fmt.Sprintf("clamp it before the query (`if %s > 100 { %s = 100 }`), or bind it into a "+
		"query struct field with `binding:\"min=1,max=100\"`", desc, desc)
	c.add("GO803", fn.File, c.m.Line(call.Pos()), message, hint, b.root.Key()+"|"+key)
}

// fromQueryNumber reports whether a name holds a number parsed from a query or form value.
func (b *boundScan) fromQueryNumber(e *Env, name string, depth int) bool {
	if depth < 0 {
		return false
	}
	vals := e.valuesOf(name)
	if len(vals) == 0 {
		// n, err := strconv.Atoi(...): a multi-value assignment
		return b.multiAssignedFromQuery(e, name, depth)
	}
	for _, v := range vals {
		if b.queryNumberExpr(e, v, depth) {
			return true
		}
		// size = n, with n a parsed query value
		if id, ok := unparen(v).(*ast.Ident); ok && id.Name != name && b.fromQueryNumber(e, id.Name, depth-1) {
			return true
		}
	}
	return b.multiAssignedFromQuery(e, name, depth)
}

func (b *boundScan) multiAssignedFromQuery(e *Env, name string, depth int) bool {
	found := false
	ast.Inspect(e.fn.Body, func(n ast.Node) bool {
		if found {
			return false
		}
		a, ok := n.(*ast.AssignStmt)
		if !ok || len(a.Rhs) != 1 || len(a.Lhs) < 2 {
			return true
		}
		if isName(a.Lhs[0], name) && b.queryNumberExpr(e, a.Rhs[0], depth) {
			found = true
		}
		return true
	})
	return found
}

func (b *boundScan) queryNumberExpr(e *Env, x ast.Expr, depth int) bool {
	call, ok := unparen(x).(*ast.CallExpr)
	if !ok {
		return false
	}
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || len(call.Args) == 0 {
		return false
	}
	path, pkg := b.c.m.importOf(e, sel.X)
	if !pkg || path != "strconv" || !strings.HasPrefix(sel.Sel.Name, "Atoi") && !strings.HasPrefix(sel.Sel.Name, "Parse") {
		return false
	}
	src := unparen(call.Args[0])
	if inner, ok := src.(*ast.CallExpr); ok {
		if s, ok := inner.Fun.(*ast.SelectorExpr); ok && scalarReaders[s.Sel.Name] && b.c.m.isCtx(e, s.X) {
			return true
		}
	}
	if id, ok := src.(*ast.Ident); ok {
		for _, v := range e.valuesOf(id.Name) {
			if inner, ok := unparen(v).(*ast.CallExpr); ok {
				if s, ok := inner.Fun.(*ast.SelectorExpr); ok && scalarReaders[s.Sel.Name] && b.c.m.isCtx(e, s.X) {
					return true
				}
			}
		}
	}
	return false
}

// requestNumber answers how an argument reads when it is an unbounded request number
// ("" otherwise): a parsed query value, a bound struct's int field, a seeded parameter.
func (b *boundScan) requestNumber(fn *Func, e *Env, x ast.Expr, bound map[string]*ast.CallExpr) (string, func(ast.Expr) bool) {
	x = unparen(x)
	if conv, ok := x.(*ast.CallExpr); ok && len(conv.Args) == 1 {
		if id, ok := conv.Fun.(*ast.Ident); ok && basics[id.Name] {
			x = unparen(conv.Args[0])
		}
	}
	switch v := x.(type) {
	case *ast.Ident:
		match := func(y ast.Expr) bool { return isName(y, v.Name) }
		if from, ok := b.seeds[fn][v.Name]; ok {
			return from, match
		}
		if b.fromQueryNumber(e, v.Name, 2) {
			return v.Name, match
		}
	case *ast.SelectorExpr:
		id, ok := v.X.(*ast.Ident)
		if !ok || bound[id.Name] == nil {
			return "", nil
		}
		ti := b.c.m.Infer(e, id).deref().rowTypeAny()
		if ti == nil {
			return "", nil
		}
		f := b.c.m.FieldOf(ti, v.Sel.Name, 2)
		if f == nil || scalarKind(b.c.m.TypeExpr(f.file, f.typ).deref()) != "num" || tagBounded(f.tag, true) {
			return "", nil
		}
		return id.Name + "." + v.Sel.Name, func(y ast.Expr) bool {
			sel, ok := unparen(y).(*ast.SelectorExpr)
			return ok && rootIdent(sel.X) == id.Name && sel.Sel.Name == v.Sel.Name
		}
	}
	return "", nil
}

// seed marks the parameters of the helpers fn hands an unbounded request number.
func (b *boundScan) seed(fn *Func, e *Env, bound map[string]*ast.CallExpr) {
	in := map[*Func]bool{}
	for _, f := range b.fns {
		in[f] = true
	}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		callee := b.c.m.Callee(e, call)
		if callee == nil || !in[callee] || callee == fn {
			return true
		}
		ps := callee.Params()
		for i, a := range call.Args {
			if i >= len(ps) || ps[i].name == "" {
				continue
			}
			desc, match := b.requestNumber(fn, e, a, bound)
			if desc == "" || b.handBounded(match) {
				continue
			}
			if b.seeds[callee] == nil {
				b.seeds[callee] = map[string]string{}
			}
			b.seeds[callee][ps[i].name] = desc
		}
		return true
	})
}

// writtenName reports whether the row a name holds reaches a GORM write in fn: as a
// write's (or its Model's) argument, or handed to a module helper that writes it.
func (b *boundScan) writtenName(fn *Func, e *Env, name string, depth int) bool {
	hit := false
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if hit {
			return false
		}
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		if sel, ok := call.Fun.(*ast.SelectorExpr); ok && b.c.m.isGorm(e, sel.X) {
			if gormWrites[sel.Sel.Name] || sel.Sel.Name == "Model" {
				for _, a := range call.Args {
					if rootIdent(stripAddr(a)) == name {
						hit = true
					}
				}
			}
			return true
		}
		if depth <= 0 {
			return true
		}
		callee := b.c.m.Callee(e, call)
		if callee == nil || callee == fn {
			return true
		}
		ps := callee.Params()
		for i, a := range call.Args {
			if i < len(ps) && ps[i].name != "" && rootIdent(stripAddr(a)) == name &&
				b.writtenName(callee, b.c.m.EnvOf(callee), ps[i].name, depth-1) {
				hit = true
			}
		}
		return true
	})
	return hit
}

// lowerBound reports whether a comparison's other side is 0, 1 or negative: a check of a
// minimum (`n < 1`, `len(s) == 0`), not a bound.
func lowerBound(x ast.Expr) bool {
	x = unparen(x)
	if u, ok := x.(*ast.UnaryExpr); ok && u.Op == token.SUB {
		return true
	}
	n := intLit(x)
	return n == 0 || n == 1
}

// copiesInto answers name and the names whose values it is assigned (size = n).
func copiesInto(e *Env, name string, depth int) map[string]bool {
	out := map[string]bool{name: true}
	if depth <= 0 {
		return out
	}
	for _, v := range e.valuesOf(name) {
		if id, ok := unparen(v).(*ast.Ident); ok && !out[id.Name] {
			for k := range copiesInto(e, id.Name, depth-1) {
				out[k] = true
			}
		}
	}
	return out
}
