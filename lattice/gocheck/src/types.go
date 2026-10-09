// A syntactic reading of types: enough to know which names hold a *gorm.DB, a
// *gin.Context or a row of a model struct. No go/types: the app's dependencies (Gin,
// GORM) need not be in the module cache, and a checker that cannot load them must not
// fall silent on the code it can read.
package main

import (
	"go/ast"
	"go/token"
	"strings"
)

// T is a type as the checker reads it.
type T struct {
	Kind  string // "named", "ext", "basic", "slice", "map", "func", "error", "unknown"
	Named *TypeInfo
	Ext   string // "gin.Context", "gorm.DB", "<import path>.<Name>" for other packages
	Basic string
	Elem  *T
	Ptr   bool
	Func  *ast.FuncType
	File  *File
}

var unknownT = &T{Kind: "unknown"}

func (t *T) deref() *T {
	if t == nil {
		return unknownT
	}
	if !t.Ptr {
		return t
	}
	c := *t
	c.Ptr = false
	return &c
}

func (t *T) is(ext string) bool { return t != nil && t.Kind == "ext" && t.Ext == ext }

// underNamed answers the named struct of a value, a pointer, or (with slices) its elements.
func (t *T) rowType(slices bool) *TypeInfo {
	for t != nil {
		switch t.Kind {
		case "named":
			if t.Named.Struct != nil {
				return t.Named
			}
			return nil
		case "slice":
			if !slices {
				return nil
			}
			t = t.Elem
		default:
			return nil
		}
	}
	return nil
}

var basics = map[string]bool{
	"string": true, "bool": true, "byte": true, "rune": true, "int": true, "int8": true,
	"int16": true, "int32": true, "int64": true, "uint": true, "uint8": true, "uint16": true,
	"uint32": true, "uint64": true, "uintptr": true, "float32": true, "float64": true,
	"any": false, "complex64": true, "complex128": true,
}

// TypeExpr reads a type expression of file.
func (m *Module) TypeExpr(f *File, e ast.Expr) *T {
	switch t := e.(type) {
	case nil:
		return unknownT
	case *ast.ParenExpr:
		return m.TypeExpr(f, t.X)
	case *ast.StarExpr:
		c := *m.TypeExpr(f, t.X)
		c.Ptr = true
		return &c
	case *ast.ArrayType:
		return &T{Kind: "slice", Elem: m.TypeExpr(f, t.Elt)}
	case *ast.MapType:
		return &T{Kind: "map", Elem: m.TypeExpr(f, t.Value)}
	case *ast.FuncType:
		return &T{Kind: "func", Func: t, File: f}
	case *ast.Ident:
		if t.Name == "error" {
			return &T{Kind: "error"}
		}
		if basics[t.Name] {
			return &T{Kind: "basic", Basic: t.Name}
		}
		if ti := f.Pkg.Types[t.Name]; ti != nil {
			return &T{Kind: "named", Named: ti}
		}
		return unknownT
	case *ast.SelectorExpr:
		x, ok := t.X.(*ast.Ident)
		if !ok {
			return unknownT
		}
		path, ok := f.Imports[x.Name]
		if !ok {
			return unknownT
		}
		if pkg := m.ByImport[path]; pkg != nil {
			if ti := pkg.Types[t.Sel.Name]; ti != nil {
				return &T{Kind: "named", Named: ti}
			}
			return unknownT
		}
		switch path {
		case ginPath:
			return &T{Kind: "ext", Ext: "gin." + t.Sel.Name}
		case gormPath:
			return &T{Kind: "ext", Ext: "gorm." + t.Sel.Name}
		}
		return &T{Kind: "ext", Ext: path + "." + t.Sel.Name}
	case *ast.IndexExpr:
		return m.TypeExpr(f, t.X)
	}
	return unknownT
}

// Env is what a function's names hold (flow-insensitive: a Go name has one type).
type Env struct {
	parent *Env
	fn     *Func
	vars   map[string]*T
	lits   map[string]*ast.FuncLit  // name := func(...) {...}
	values map[string][]ast.Expr    // every value assigned to a name, in source order
	groups map[string]*ast.CallExpr // name := router.Group(...)
}

func (e *Env) lookup(name string) (*T, bool) {
	for x := e; x != nil; x = x.parent {
		if t, ok := x.vars[name]; ok {
			return t, true
		}
	}
	return nil, false
}

func (e *Env) lit(name string) *ast.FuncLit {
	for x := e; x != nil; x = x.parent {
		if _, ok := x.vars[name]; ok {
			return x.lits[name]
		}
	}
	return nil
}

func (e *Env) valuesOf(name string) []ast.Expr {
	for x := e; x != nil; x = x.parent {
		if _, ok := x.vars[name]; ok {
			return x.values[name]
		}
	}
	return nil
}

func (e *Env) group(name string) *ast.CallExpr {
	for x := e; x != nil; x = x.parent {
		if _, ok := x.vars[name]; ok {
			return x.groups[name]
		}
	}
	return nil
}

// EnvOf answers fn's environment (built once).
func (m *Module) EnvOf(fn *Func) *Env {
	if e, ok := m.envs[fn]; ok {
		return e
	}
	e := &Env{fn: fn, vars: map[string]*T{}, lits: map[string]*ast.FuncLit{},
		values: map[string][]ast.Expr{}, groups: map[string]*ast.CallExpr{}}
	if fn.Parent != nil {
		e.parent = m.EnvOf(fn.Parent)
	}
	m.envs[fn] = e
	f := fn.File
	if fn.RecvVar != "" {
		e.vars[fn.RecvVar] = &T{Kind: "named", Named: f.Pkg.Types[fn.Recv], Ptr: true}
		if f.Pkg.Types[fn.Recv] == nil {
			e.vars[fn.RecvVar] = unknownT
		}
	}
	for _, p := range fn.Params() {
		if p.name != "" {
			e.vars[p.name] = m.TypeExpr(f, p.typ)
		}
	}
	for _, p := range fn.Results() {
		if p.name != "" {
			e.vars[p.name] = m.TypeExpr(f, p.typ)
		}
	}
	define := func(name string, t *T) {
		if name == "_" || name == "" {
			return
		}
		if old, ok := e.vars[name]; !ok || old.Kind == "unknown" {
			e.vars[name] = t
		}
	}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		switch s := n.(type) {
		case *ast.FuncLit:
			return false
		case *ast.AssignStmt:
			ts := m.assignTypes(e, s.Rhs, len(s.Lhs))
			for i, l := range s.Lhs {
				id, ok := l.(*ast.Ident)
				if !ok {
					continue
				}
				if s.Tok == token.DEFINE || s.Tok == token.ASSIGN {
					if _, known := e.lookup(id.Name); !known || s.Tok == token.DEFINE {
						define(id.Name, ts[i])
					}
				}
				if len(s.Rhs) == len(s.Lhs) {
					rhs := s.Rhs[i]
					e.values[id.Name] = append(e.values[id.Name], rhs)
					if lit, ok := rhs.(*ast.FuncLit); ok {
						e.lits[id.Name] = lit
					}
					if c, ok := rhs.(*ast.CallExpr); ok {
						if sel, ok := c.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "Group" {
							e.groups[id.Name] = c
						}
					}
				}
			}
		case *ast.ValueSpec:
			var t *T
			if s.Type != nil {
				t = m.TypeExpr(f, s.Type)
			}
			var ts []*T
			if t == nil {
				ts = m.assignTypes(e, s.Values, len(s.Names))
			}
			for i, n := range s.Names {
				if t != nil {
					define(n.Name, t)
				} else {
					define(n.Name, ts[i])
				}
				if i < len(s.Values) && len(s.Values) == len(s.Names) {
					e.values[n.Name] = append(e.values[n.Name], s.Values[i])
				}
			}
		case *ast.RangeStmt:
			xt := m.Infer(e, s.X)
			if id, ok := s.Key.(*ast.Ident); ok && s.Tok == token.DEFINE {
				if xt.Kind == "map" {
					define(id.Name, unknownT)
				} else {
					define(id.Name, &T{Kind: "basic", Basic: "int"})
				}
			}
			if id, ok := s.Value.(*ast.Ident); ok && s.Tok == token.DEFINE {
				if xt.Kind == "slice" || xt.Kind == "map" {
					define(id.Name, xt.Elem)
				} else {
					define(id.Name, unknownT)
				}
			}
		case *ast.TypeSwitchStmt:
			// v := x.(type): v's type differs per clause; leave it unknown
			if a, ok := s.Assign.(*ast.AssignStmt); ok {
				for _, l := range a.Lhs {
					if id, ok := l.(*ast.Ident); ok {
						define(id.Name, unknownT)
					}
				}
			}
		}
		return true
	})
	return e
}

// assignTypes answers the types n names get from rhs.
func (m *Module) assignTypes(e *Env, rhs []ast.Expr, n int) []*T {
	out := make([]*T, n)
	for i := range out {
		out[i] = unknownT
	}
	if len(rhs) == n {
		for i, r := range rhs {
			out[i] = m.Infer(e, r)
		}
		return out
	}
	if len(rhs) != 1 {
		return out
	}
	switch r := unparen(rhs[0]).(type) {
	case *ast.CallExpr:
		ts := m.callResults(e, r)
		for i := 0; i < n && i < len(ts); i++ {
			out[i] = ts[i]
		}
	case *ast.TypeAssertExpr:
		out[0] = m.TypeExpr(e.fn.File, r.Type)
		if n > 1 {
			out[1] = &T{Kind: "basic", Basic: "bool"}
		}
	case *ast.IndexExpr:
		out[0] = m.Infer(e, r)
		if n > 1 {
			out[1] = &T{Kind: "basic", Basic: "bool"}
		}
	}
	return out
}

func unparen(e ast.Expr) ast.Expr {
	for {
		p, ok := e.(*ast.ParenExpr)
		if !ok {
			return e
		}
		e = p.X
	}
}

// importOf answers the import path when e is a package name of f (not shadowed).
func (m *Module) importOf(e *Env, x ast.Expr) (string, bool) {
	id, ok := x.(*ast.Ident)
	if !ok {
		return "", false
	}
	if _, local := e.lookup(id.Name); local {
		return "", false
	}
	if _, pkgLevel := e.fn.File.Pkg.Vars[id.Name]; pkgLevel {
		return "", false
	}
	path, ok := e.fn.File.Imports[id.Name]
	return path, ok
}

// gormChainMethods answer a *gorm.DB (every builder and finisher but these).
var gormNotDB = map[string]*T{
	"Transaction": {Kind: "error"},
	"Rows":        unknownT, "Row": unknownT, "DB": unknownT, "ScanRows": {Kind: "error"},
	"Association": unknownT, "ToSQL": {Kind: "basic", Basic: "string"}, "Migrator": unknownT,
	"AutoMigrate": {Kind: "error"}, "SetupJoinTable": {Kind: "error"}, "Use": {Kind: "error"},
	"Get": unknownT, "InstanceGet": unknownT,
}

var gormDB = &T{Kind: "ext", Ext: "gorm.DB", Ptr: true}

// callResults answers the result types of a call.
func (m *Module) callResults(e *Env, c *ast.CallExpr) []*T {
	if sel, ok := c.Fun.(*ast.SelectorExpr); ok {
		if path, ok := m.importOf(e, sel.X); ok {
			switch {
			case path == gormPath && sel.Sel.Name == "Open":
				return []*T{gormDB, {Kind: "error"}}
			case path == ginPath && (sel.Sel.Name == "Default" || sel.Sel.Name == "New"):
				return []*T{{Kind: "ext", Ext: "gin.Engine", Ptr: true}}
			case path == "strconv":
				switch sel.Sel.Name {
				case "Atoi":
					return []*T{{Kind: "basic", Basic: "int"}, {Kind: "error"}}
				case "ParseInt":
					return []*T{{Kind: "basic", Basic: "int64"}, {Kind: "error"}}
				case "ParseUint":
					return []*T{{Kind: "basic", Basic: "uint64"}, {Kind: "error"}}
				case "ParseFloat":
					return []*T{{Kind: "basic", Basic: "float64"}, {Kind: "error"}}
				case "ParseBool":
					return []*T{{Kind: "basic", Basic: "bool"}, {Kind: "error"}}
				}
			}
		}
		xt := m.Infer(e, sel.X)
		if xt.is("gorm.DB") {
			if t, ok := gormNotDB[sel.Sel.Name]; ok {
				return []*T{t}
			}
			return []*T{gormDB}
		}
		if xt.is("gin.Engine") || xt.is("gin.RouterGroup") || xt.is("gin.IRouter") {
			if sel.Sel.Name == "Group" {
				return []*T{{Kind: "ext", Ext: "gin.RouterGroup", Ptr: true}}
			}
		}
		if xt.is("gin.Context") {
			switch sel.Sel.Name {
			case "Query", "DefaultQuery", "Param", "PostForm", "DefaultPostForm", "GetHeader",
				"GetString", "ClientIP", "FullPath", "ContentType":
				return []*T{{Kind: "basic", Basic: "string"}}
			case "GetQuery", "GetPostForm":
				return []*T{{Kind: "basic", Basic: "string"}, {Kind: "basic", Basic: "bool"}}
			case "GetInt":
				return []*T{{Kind: "basic", Basic: "int"}}
			case "GetInt64":
				return []*T{{Kind: "basic", Basic: "int64"}}
			case "GetUint":
				return []*T{{Kind: "basic", Basic: "uint"}}
			case "GetUint64":
				return []*T{{Kind: "basic", Basic: "uint64"}}
			case "GetBool":
				return []*T{{Kind: "basic", Basic: "bool"}}
			case "Get":
				return []*T{unknownT, {Kind: "basic", Basic: "bool"}}
			case "ShouldBind", "ShouldBindJSON", "ShouldBindQuery", "ShouldBindUri", "Bind",
				"BindJSON", "BindQuery", "ShouldBindWith", "ShouldBindBodyWith", "BindUri":
				return []*T{{Kind: "error"}}
			}
		}
	}
	if id, ok := c.Fun.(*ast.Ident); ok {
		switch id.Name {
		case "new":
			if len(c.Args) == 1 {
				t := *m.TypeExpr(e.fn.File, c.Args[0])
				t.Ptr = true
				return []*T{&t}
			}
		case "make":
			if len(c.Args) >= 1 {
				return []*T{m.TypeExpr(e.fn.File, c.Args[0])}
			}
		case "append":
			if len(c.Args) >= 1 {
				return []*T{m.Infer(e, c.Args[0])}
			}
		case "len", "cap":
			return []*T{{Kind: "basic", Basic: "int"}}
		case "min", "max":
			if len(c.Args) >= 1 {
				return []*T{m.Infer(e, c.Args[0])}
			}
		}
		if _, local := e.lookup(id.Name); !local {
			if basics[id.Name] {
				return []*T{{Kind: "basic", Basic: id.Name}}
			}
			if ti := e.fn.File.Pkg.Types[id.Name]; ti != nil {
				return []*T{{Kind: "named", Named: ti}} // a conversion
			}
		}
	}
	if sel, ok := c.Fun.(*ast.SelectorExpr); ok {
		if path, ok := m.importOf(e, sel.X); ok {
			if pkg := m.ByImport[path]; pkg != nil && pkg.Types[sel.Sel.Name] != nil {
				return []*T{{Kind: "named", Named: pkg.Types[sel.Sel.Name]}}
			}
		}
	}
	if callee := m.Callee(e, c); callee != nil {
		var out []*T
		for _, r := range callee.Results() {
			out = append(out, m.TypeExpr(callee.File, r.typ))
		}
		return out
	}
	return nil
}

// Infer answers the type of an expression.
func (m *Module) Infer(e *Env, x ast.Expr) *T {
	switch v := x.(type) {
	case *ast.ParenExpr:
		return m.Infer(e, v.X)
	case *ast.Ident:
		if t, ok := e.lookup(v.Name); ok {
			return t
		}
		pkg := e.fn.File.Pkg
		if vi, ok := pkg.Vars[v.Name]; ok {
			if vi.typ != nil {
				return m.TypeExpr(vi.file, vi.typ)
			}
			if vi.value != nil {
				return m.inferPkgValue(vi)
			}
		}
		if ci, ok := pkg.Consts[v.Name]; ok {
			if ci.typ != nil {
				return m.TypeExpr(ci.file, ci.typ)
			}
			if bl, ok := ci.value.(*ast.BasicLit); ok && bl.Kind == token.STRING {
				return &T{Kind: "basic", Basic: "string"}
			}
		}
		return unknownT
	case *ast.BasicLit:
		switch v.Kind {
		case token.STRING:
			return &T{Kind: "basic", Basic: "string"}
		case token.INT:
			return &T{Kind: "basic", Basic: "int"}
		case token.FLOAT:
			return &T{Kind: "basic", Basic: "float64"}
		}
	case *ast.UnaryExpr:
		if v.Op == token.AND {
			c := *m.Infer(e, v.X)
			c.Ptr = true
			return &c
		}
		return m.Infer(e, v.X)
	case *ast.StarExpr:
		return m.Infer(e, v.X).deref()
	case *ast.CompositeLit:
		if v.Type != nil {
			return m.TypeExpr(e.fn.File, v.Type)
		}
	case *ast.CallExpr:
		if rs := m.callResults(e, v); len(rs) > 0 {
			return rs[0]
		}
	case *ast.SelectorExpr:
		if path, ok := m.importOf(e, v.X); ok {
			if pkg := m.ByImport[path]; pkg != nil {
				if vi, ok := pkg.Vars[v.Sel.Name]; ok {
					if vi.typ != nil {
						return m.TypeExpr(vi.file, vi.typ)
					}
					if vi.value != nil {
						return m.inferPkgValue(vi)
					}
				}
				if ci, ok := pkg.Consts[v.Sel.Name]; ok && ci.typ != nil {
					return m.TypeExpr(ci.file, ci.typ)
				}
			}
			return unknownT
		}
		xt := m.Infer(e, v.X)
		if xt.is("gorm.DB") {
			switch v.Sel.Name {
			case "Error":
				return &T{Kind: "error"}
			case "RowsAffected":
				return &T{Kind: "basic", Basic: "int64"}
			}
			return unknownT
		}
		if ti := xt.deref().rowTypeAny(); ti != nil {
			if fld := m.FieldOf(ti, v.Sel.Name, 2); fld != nil {
				return m.TypeExpr(fld.file, fld.typ)
			}
		}
	case *ast.IndexExpr:
		xt := m.Infer(e, v.X)
		if xt.Kind == "slice" || xt.Kind == "map" {
			return xt.Elem
		}
	case *ast.SliceExpr:
		return m.Infer(e, v.X)
	case *ast.TypeAssertExpr:
		if v.Type != nil {
			return m.TypeExpr(e.fn.File, v.Type)
		}
	case *ast.FuncLit:
		return &T{Kind: "func", Func: v.Type, File: e.fn.File}
	}
	return unknownT
}

func (t *T) rowTypeAny() *TypeInfo {
	if t != nil && t.Kind == "named" && t.Named.Struct != nil {
		return t.Named
	}
	return nil
}

func (m *Module) inferPkgValue(vi varInfo) *T {
	// a package-level `var x = expr`: read it in a scratch environment of its file
	fn := &Func{Name: "<pkg>", Type: &ast.FuncType{}, Body: &ast.BlockStmt{}, File: vi.file}
	e := &Env{fn: fn, vars: map[string]*T{}}
	return m.Infer(e, vi.value)
}

// fieldInfo is one struct field (embedded fields are named by their type).
type fieldInfo struct {
	name     string
	typ      ast.Expr
	tag      string
	embedded bool
	file     *File
	owner    *TypeInfo
}

// Fields answers ti's own fields in order.
func (m *Module) Fields(ti *TypeInfo) []fieldInfo {
	var out []fieldInfo
	if ti == nil || ti.Struct == nil {
		return out
	}
	for _, fld := range ti.Struct.Fields.List {
		tag := ""
		if fld.Tag != nil {
			if v, ok := stringLit(fld.Tag); ok {
				tag = v
			}
		}
		if len(fld.Names) == 0 {
			out = append(out, fieldInfo{name: typeName(fld.Type), typ: fld.Type, tag: tag,
				embedded: true, file: ti.File, owner: ti})
			continue
		}
		for _, n := range fld.Names {
			out = append(out, fieldInfo{name: n.Name, typ: fld.Type, tag: tag, file: ti.File,
				owner: ti})
		}
	}
	return out
}

func typeName(e ast.Expr) string {
	switch t := e.(type) {
	case *ast.StarExpr:
		return typeName(t.X)
	case *ast.Ident:
		return t.Name
	case *ast.SelectorExpr:
		return t.Sel.Name
	}
	return ""
}

// FieldOf answers the field `name` of ti, promoted through embedded module structs up to
// depth levels.
func (m *Module) FieldOf(ti *TypeInfo, name string, depth int) *fieldInfo {
	fs := m.Fields(ti)
	for i := range fs {
		if fs[i].name == name && !fs[i].embedded {
			return &fs[i]
		}
	}
	for i := range fs {
		if fs[i].name == name {
			return &fs[i]
		}
	}
	if depth <= 0 {
		return nil
	}
	for _, f := range fs {
		if f.embedded {
			if et := m.TypeExpr(f.file, f.typ).rowTypeAny(); et != nil {
				if got := m.FieldOf(et, name, depth-1); got != nil {
					return got
				}
			}
		}
	}
	return nil
}

// MethodOf answers ti's method `name` (or an embedded module struct's).
func (m *Module) MethodOf(ti *TypeInfo, name string, depth int) *Func {
	if ti == nil {
		return nil
	}
	if fn := ti.Pkg.Methods[ti.Name][name]; fn != nil {
		return fn
	}
	if depth <= 0 {
		return nil
	}
	for _, f := range m.Fields(ti) {
		if f.embedded {
			if et := m.TypeExpr(f.file, f.typ); et.Kind == "named" {
				if fn := m.MethodOf(et.Named, name, depth-1); fn != nil {
					return fn
				}
			}
		}
	}
	return nil
}

func nParams(fn *Func) (int, bool) {
	ps := fn.Params()
	variadic := false
	if fn.Type.Params != nil && len(fn.Type.Params.List) > 0 {
		_, variadic = fn.Type.Params.List[len(fn.Type.Params.List)-1].Type.(*ast.Ellipsis)
	}
	return len(ps), variadic
}

func arityOK(fn *Func, n int) bool {
	k, variadic := nParams(fn)
	if variadic {
		return n >= k-1
	}
	return n == k
}

// uniqueMethod answers the module's only method `name` taking n arguments.
func (m *Module) uniqueMethod(name string, n int) *Func {
	var found *Func
	for _, fn := range m.methods[name] {
		if !arityOK(fn, n) {
			continue
		}
		if found != nil {
			return nil
		}
		found = fn
	}
	return found
}

// External reports whether a call goes to code outside the module that the checker
// knows does not reach the module's own functions (Gin, GORM, the standard library).
func (m *Module) knownExternal(e *Env, c *ast.CallExpr) bool {
	switch f := c.Fun.(type) {
	case *ast.Ident:
		switch f.Name {
		case "len", "cap", "append", "make", "new", "delete", "panic", "print", "println",
			"min", "max", "copy", "clear", "close", "recover", "complex", "real", "imag":
			return true
		}
		if basics[f.Name] || f.Name == "error" || f.Name == "any" {
			return true
		}
		return false
	case *ast.SelectorExpr:
		if path, ok := m.importOf(e, f.X); ok {
			return m.ByImport[path] == nil && (isStd(path) || path == ginPath || path == gormPath)
		}
		xt := m.Infer(e, f.X)
		if xt.Kind == "ext" {
			return strings.HasPrefix(xt.Ext, "gin.") || strings.HasPrefix(xt.Ext, "gorm.") ||
				isStd(xt.Ext[:max(0, strings.LastIndex(xt.Ext, "."))])
		}
		if xt.Kind == "basic" || xt.Kind == "error" {
			return true
		}
	}
	return false
}

func isStd(path string) bool {
	first := path
	if i := strings.Index(path, "/"); i >= 0 {
		first = path[:i]
	}
	return first != "" && !strings.Contains(first, ".")
}

// Callee answers the module function a call runs, or nil.
func (m *Module) Callee(e *Env, c *ast.CallExpr) *Func {
	switch f := unparen(c.Fun).(type) {
	case *ast.FuncLit:
		return m.LitFunc(f, e.fn)
	case *ast.Ident:
		if _, local := e.lookup(f.Name); local {
			if lit := e.lit(f.Name); lit != nil {
				return m.LitFunc(lit, m.litParent(e, f.Name))
			}
			return nil
		}
		return e.fn.File.Pkg.Funcs[f.Name]
	case *ast.SelectorExpr:
		if path, ok := m.importOf(e, f.X); ok {
			if pkg := m.ByImport[path]; pkg != nil {
				return pkg.Funcs[f.Sel.Name]
			}
			return nil
		}
		xt := m.Infer(e, f.X)
		switch xt.Kind {
		case "named":
			if xt.Named.Iface != nil {
				return m.uniqueMethod(f.Sel.Name, len(c.Args))
			}
			return m.MethodOf(xt.Named, f.Sel.Name, 2)
		case "unknown":
			if m.rootIsExternal(e, f.X) {
				return nil
			}
			return m.uniqueMethod(f.Sel.Name, len(c.Args))
		}
	}
	return nil
}

// litParent answers the function whose environment defines the literal `name`.
func (m *Module) litParent(e *Env, name string) *Func {
	for x := e; x != nil; x = x.parent {
		if _, ok := x.vars[name]; ok {
			return x.fn
		}
	}
	return e.fn
}

// rootIsExternal reports whether a selector chain starts at a package name or a value
// of an external type.
func (m *Module) rootIsExternal(e *Env, x ast.Expr) bool {
	for {
		switch v := x.(type) {
		case *ast.SelectorExpr:
			if _, ok := m.importOf(e, v.X); ok {
				return true
			}
			if t := m.Infer(e, v.X); t.Kind == "ext" {
				return true
			}
			x = v.X
		case *ast.CallExpr:
			x = v.Fun
		case *ast.Ident:
			t := m.Infer(e, v)
			return t.Kind == "ext" || t.Kind == "basic" || t.Kind == "error"
		default:
			return false
		}
	}
}

// FuncRef answers the function a value expression names (a handler argument).
func (m *Module) FuncRef(e *Env, x ast.Expr) *Func {
	switch v := unparen(x).(type) {
	case *ast.FuncLit:
		return m.LitFunc(v, e.fn)
	case *ast.Ident:
		if _, local := e.lookup(v.Name); local {
			if lit := e.lit(v.Name); lit != nil {
				return m.LitFunc(lit, m.litParent(e, v.Name))
			}
			// h := makeHandler(db)
			if vals := e.valuesOf(v.Name); len(vals) == 1 {
				return m.FuncRef(e, vals[0])
			}
			return nil
		}
		return e.fn.File.Pkg.Funcs[v.Name]
	case *ast.SelectorExpr:
		if path, ok := m.importOf(e, v.X); ok {
			if pkg := m.ByImport[path]; pkg != nil {
				return pkg.Funcs[v.Sel.Name]
			}
			return nil
		}
		xt := m.Infer(e, v.X)
		if xt.Kind == "named" {
			if xt.Named.Iface != nil {
				return m.uniqueMethod(v.Sel.Name, 1)
			}
			return m.MethodOf(xt.Named, v.Sel.Name, 2)
		}
		if xt.Kind == "unknown" && !m.rootIsExternal(e, v.X) {
			return m.uniqueMethod(v.Sel.Name, 1)
		}
	case *ast.CallExpr:
		// a handler factory: makeHandler(db) returning func(c *gin.Context) {...}
		callee := m.Callee(e, v)
		if callee == nil {
			return nil
		}
		var lit *ast.FuncLit
		count := 0
		ast.Inspect(callee.Body, func(n ast.Node) bool {
			if r, ok := n.(*ast.ReturnStmt); ok && len(r.Results) == 1 {
				if l, ok := r.Results[0].(*ast.FuncLit); ok {
					lit = l
					count++
				}
			}
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			return true
		})
		if count == 1 {
			return m.LitFunc(lit, callee)
		}
	}
	return nil
}

// ctxParams answers the names of fn's *gin.Context parameters.
func (m *Module) ctxParams(fn *Func) []string {
	var out []string
	for _, p := range fn.Params() {
		if p.name != "" && m.TypeExpr(fn.File, p.typ).is("gin.Context") {
			out = append(out, p.name)
		}
	}
	return out
}

func (m *Module) hasCtxParam(fn *Func) bool {
	for _, p := range fn.Params() {
		if m.TypeExpr(fn.File, p.typ).is("gin.Context") {
			return true
		}
	}
	return false
}

// isGorm reports whether x holds a *gorm.DB.
func (m *Module) isGorm(e *Env, x ast.Expr) bool { return m.Infer(e, x).is("gorm.DB") }

// isCtx reports whether x holds a *gin.Context.
func (m *Module) isCtx(e *Env, x ast.Expr) bool { return m.Infer(e, x).is("gin.Context") }

// chainLink is one call of a GORM method chain, innermost (nearest the root) first.
type chainLink struct {
	name string
	call *ast.CallExpr
}

// gormChain answers the method calls of a GORM chain ending at c (c included, last) and
// its root expression, or nil when c is not a GORM method call.
func (m *Module) gormChain(e *Env, c *ast.CallExpr) ([]chainLink, ast.Expr) {
	sel, ok := c.Fun.(*ast.SelectorExpr)
	if !ok || !m.isGorm(e, sel.X) {
		return nil, nil
	}
	links := []chainLink{{sel.Sel.Name, c}}
	x := sel.X
	for {
		inner, ok := unparen(x).(*ast.CallExpr)
		if !ok {
			break
		}
		s, ok := inner.Fun.(*ast.SelectorExpr)
		if !ok || !m.isGorm(e, s.X) {
			break
		}
		links = append([]chainLink{{s.Sel.Name, inner}}, links...)
		x = s.X
	}
	return links, x
}

// rootIdent answers the identifier a chain root starts with ("" for another expression).
func rootIdent(x ast.Expr) string {
	if id, ok := unparen(x).(*ast.Ident); ok {
		return id.Name
	}
	return ""
}

// stringLit answers a string literal's value.
func stringLit(e ast.Expr) (string, bool) {
	bl, ok := unparen(e).(*ast.BasicLit)
	if !ok || bl.Kind != token.STRING {
		return "", false
	}
	s := bl.Value
	if len(s) >= 2 && s[0] == '`' {
		return s[1 : len(s)-1], true
	}
	out, err := unquote(s)
	return out, err == nil
}
