// goextract: tables (D3). A struct is a table when it is an argument of a reachable
// AutoMigrate / Migrator().CreateTable, has a TableName method, or is a GORM chain's
// model or destination. Columns follow GORM's tags; no foreign key is invented from a
// name (only foreignKey / references association tags).
package main

import (
	"go/ast"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type xColumn struct {
	Name       string  `json:"name"`
	Field      string  `json:"field"`
	GoType     string  `json:"go_type"`
	Type       *string `json:"type"`
	Nullable   bool    `json:"nullable"`
	Required   bool    `json:"required"`
	Default    *string `json:"default"`
	PrimaryKey bool    `json:"primary_key"`
	Unique     bool    `json:"unique"`
	Index      bool    `json:"index"`
	MaxLength  *int    `json:"max_length"`
	ForeignKey *string `json:"foreign_key"`
	Line       int     `json:"line"`
}

type xTable struct {
	Struct         string     `json:"struct"`
	Table          string     `json:"table"`
	NameUnread     bool       `json:"name_unread"`
	Package        string     `json:"package"`
	File           string     `json:"file"`
	Line           int        `json:"line"`
	Source         string     `json:"source"`
	MigratedAt     []xLoc     `json:"migrated_at"`
	Columns        []xColumn  `json:"columns"`
	UniqueTogether [][]string `json:"unique_together"`
	Checks         []string   `json:"checks"`

	ti        *TypeInfo
	srcRank   int
	fieldCols map[string]string // Go field name -> column
	colTypes  map[string]string // column -> Lattice type ("" none)
}

var sourceRank = map[string]int{"migrate": 3, "TableName": 2, "model": 1}

// chainDestinations are the GORM methods whose first argument is a row (or rows).
var chainDestinations = map[string]bool{
	"First": true, "Last": true, "Take": true, "Find": true, "Create": true, "Save": true,
	"FirstOrCreate": true, "FirstOrInit": true, "CreateInBatches": true, "Delete": true,
}

func (g *gx) tableFor(ti *TypeInfo, source string) *xTable {
	if ti == nil || ti.Struct == nil {
		return nil
	}
	t := g.tables[ti]
	if t == nil {
		t = &xTable{Struct: ti.Name, Package: ti.Pkg.ImportPath, File: ti.File.Rel,
			ti: ti, MigratedAt: []xLoc{}, Columns: []xColumn{}, UniqueTogether: [][]string{},
			Checks: []string{}, fieldCols: map[string]string{}, colTypes: map[string]string{}}
		if t.Package == "" {
			t.Package = ti.Pkg.Rel
		}
		g.tables[ti] = t
		g.tableList = append(g.tableList, t)
	}
	if sourceRank[source] > t.srcRank {
		t.srcRank, t.Source = sourceRank[source], source
	}
	return t
}

// modelsIn answers the module structs a migration argument names: `&T{}`, `new(T)`, a
// local holding one, `append(...)`, a `[]any{...}` literal, and a module function
// returning such a literal.
func (g *gx) modelsIn(e *Env, x ast.Expr, depth int) []*TypeInfo {
	m := g.m
	if depth < 0 {
		return nil
	}
	var out []*TypeInfo
	add := func(t *T) {
		if ti := t.rowType(true); ti != nil {
			out = append(out, ti)
		}
	}
	switch v := unparen(x).(type) {
	case *ast.UnaryExpr:
		return g.modelsIn(e, v.X, depth)
	case *ast.CompositeLit:
		if at, ok := v.Type.(*ast.ArrayType); ok {
			_ = at
			for _, el := range v.Elts {
				out = append(out, g.modelsIn(e, el, depth)...)
			}
			return out
		}
		if v.Type != nil {
			add(m.Infer(e, v))
		}
	case *ast.CallExpr:
		if id, ok := v.Fun.(*ast.Ident); ok {
			switch id.Name {
			case "new":
				if len(v.Args) == 1 {
					add(m.TypeExpr(e.fn.File, v.Args[0]))
				}
				return out
			case "append":
				for _, a := range v.Args {
					out = append(out, g.modelsIn(e, a, depth)...)
				}
				return out
			}
		}
		if callee := m.Callee(e, v); callee != nil && callee.Lit == nil {
			ce := m.EnvOf(callee)
			ast.Inspect(callee.Body, func(n ast.Node) bool {
				if _, ok := n.(*ast.FuncLit); ok {
					return false
				}
				if r, ok := n.(*ast.ReturnStmt); ok {
					for _, res := range r.Results {
						out = append(out, g.modelsIn(ce, res, depth-1)...)
					}
				}
				return true
			})
		}
	case *ast.Ident:
		if vals := e.valuesOf(v.Name); len(vals) == 1 {
			if de := defEnv(e, v.Name); de != nil {
				return g.modelsIn(de, vals[0], depth-1)
			}
		}
		add(m.Infer(e, v))
	case *ast.SelectorExpr, *ast.IndexExpr:
		add(m.Infer(e, v.(ast.Expr)))
	}
	return out
}

func (g *gx) findTables() {
	m := g.m
	// 1. reachable AutoMigrate / CreateTable
	for _, fn := range m.AllFuncs() {
		e := m.EnvOf(fn)
		stmtWalk(fn.Body, func(call *ast.CallExpr, _ []ast.Node) {
			sel, ok := call.Fun.(*ast.SelectorExpr)
			if !ok || (sel.Sel.Name != "AutoMigrate" && sel.Sel.Name != "CreateTable") {
				return
			}
			isDB := m.isGorm(e, sel.X)
			if !isDB {
				if inner, ok := unparen(sel.X).(*ast.CallExpr); ok {
					if is, ok := inner.Fun.(*ast.SelectorExpr); ok && is.Sel.Name == "Migrator" {
						isDB = m.isGorm(e, is.X)
					}
				}
			}
			if !isDB || !g.isWired(topFunc(fn)) && !g.isWired(fn) {
				return
			}
			for _, a := range call.Args {
				for _, ti := range g.modelsIn(e, a, 4) {
					if t := g.tableFor(ti, "migrate"); t != nil {
						t.MigratedAt = append(t.MigratedAt, xLoc{fn.File.Rel, g.line(call.Pos())})
					}
				}
			}
		})
	}
	// 2. TableName methods
	for _, pkg := range m.Pkgs {
		var names []string
		for n, ti := range pkg.Types {
			if ti.Struct != nil && pkg.Methods[n]["TableName"] != nil {
				names = append(names, n)
			}
		}
		sort.Strings(names)
		for _, n := range names {
			g.tableFor(pkg.Types[n], "TableName")
		}
	}
	// 3. a GORM chain's model or destination
	for _, fn := range m.AllFuncs() {
		e := m.EnvOf(fn)
		stmtWalk(fn.Body, func(call *ast.CallExpr, _ []ast.Node) {
			links, _ := m.gormChain(e, call)
			if links == nil {
				return
			}
			last := links[len(links)-1]
			if (last.name == "Model" || chainDestinations[last.name]) && len(last.call.Args) >= 1 {
				if last.name == "Model" && len(last.call.Args) != 1 {
					return
				}
				if ti := m.Infer(e, last.call.Args[0]).rowType(true); ti != nil {
					g.tableFor(ti, "model")
				}
			}
		})
	}
	sort.SliceStable(g.tableList, func(i, j int) bool {
		a, b := g.tableList[i], g.tableList[j]
		if a.File != b.File {
			return a.File < b.File
		}
		if la, lb := g.line(a.ti.Struct.Pos()), g.line(b.ti.Struct.Pos()); la != lb {
			return la < lb
		}
		return a.Struct < b.Struct
	})
	for _, t := range g.tableList {
		g.buildTable(t)
		g.res.Tables = append(g.res.Tables, t)
		if _, ok := g.byTable[t.Table]; !ok {
			g.byTable[t.Table] = t
		}
		if _, ok := g.byStruct[t.Struct]; !ok {
			g.byStruct[t.Struct] = t
		}
	}
	if g.res.Tables == nil {
		g.res.Tables = []*xTable{}
	}
}

// colSrc is one column's source: a struct field, or a field of gorm.Model.
type colSrc struct {
	f     fieldInfo
	col   string
	synth string
	line  int
}

var varcharRE = regexp.MustCompile(`(?i)(?:var)?char\s*\(\s*(\d+)\s*\)`)

func (g *gx) fieldLine(ti *TypeInfo, name string) int {
	if ti.Struct != nil {
		for _, fld := range ti.Struct.Fields.List {
			for _, n := range fld.Names {
				if n.Name == name {
					return g.line(n.Pos())
				}
			}
			if len(fld.Names) == 0 && typeName(fld.Type) == name {
				return g.line(fld.Pos())
			}
		}
	}
	return g.line(ti.Struct.Pos())
}

// relationship reports whether a field is an association (a struct or a slice of
// structs of the module that is not itself a column type).
func (g *gx) relationship(f fieldInfo) (*TypeInfo, bool) {
	t := g.m.TypeExpr(f.file, f.typ)
	for t.Kind == "slice" && t.Elem != nil {
		t = t.Elem
	}
	if t.Kind == "named" && t.Named.Struct != nil {
		ti := t.Named
		if ti.Pkg.Methods[ti.Name]["Scan"] != nil || ti.Pkg.Methods[ti.Name]["Value"] != nil {
			return nil, false
		}
		return ti, true
	}
	return nil, false
}

func (g *gx) flattenCols(ti *TypeInfo, prefix string, depth int, out *[]colSrc, rels *[]fieldInfo) {
	for _, f := range g.m.Fields(ti) {
		tag := gormTag(f.tag)
		if _, skip := tag["-"]; skip {
			continue
		}
		if f.embedded || hasKey(tag, "EMBEDDED") {
			t := g.m.TypeExpr(f.file, f.typ)
			if t.is("gorm.Model") {
				for _, n := range gormModelFields {
					*out = append(*out, colSrc{f: fieldInfo{name: n, file: f.file, owner: ti},
						col: prefix + toDBName(n), synth: n, line: g.fieldLine(ti, "Model")})
				}
				continue
			}
			if et := t.rowTypeAny(); et != nil && depth > 0 {
				p := prefix
				if ep, ok := tag["EMBEDDEDPREFIX"]; ok {
					p += ep
				}
				g.flattenCols(et, p, depth-1, out, rels)
			}
			continue
		}
		if !ast.IsExported(f.name) {
			continue
		}
		if _, rel := g.relationship(f); rel {
			*rels = append(*rels, f)
			continue
		}
		*out = append(*out, colSrc{f: f, col: prefix + columnOf(f), line: g.fieldLine(ti, f.name)})
	}
}

// goTypeText prints a field's declared type.
func (g *gx) goTypeText(f *File, typ ast.Expr) string {
	return g.src(typ)
}

// latticeType maps a column's Go type to a Lattice type name (nil: none).
func (g *gx) latticeType(f *File, typ ast.Expr, tag map[string]string) *string {
	s := func(v string) *string { return &v }
	for {
		if st, ok := typ.(*ast.StarExpr); ok {
			typ = st.X
			continue
		}
		break
	}
	switch t := typ.(type) {
	case *ast.Ident:
		return g.identType(f, t.Name, 0)
	case *ast.SelectorExpr:
		x, ok := t.X.(*ast.Ident)
		if !ok {
			return nil
		}
		path := f.Imports[x.Name]
		switch {
		case path == "time" && t.Sel.Name == "Time":
			if strings.HasPrefix(strings.ToLower(tag["TYPE"]), "date") &&
				!strings.HasPrefix(strings.ToLower(tag["TYPE"]), "datetime") {
				return s("Date")
			}
			return s("DateTime")
		case t.Sel.Name == "UUID" && strings.Contains(path, "uuid"):
			return s("Uuid")
		case path == gormPath && t.Sel.Name == "DeletedAt":
			return s("DateTime")
		case path == "database/sql":
			switch t.Sel.Name {
			case "NullString":
				return s("Str")
			case "NullInt64", "NullInt32", "NullInt16":
				return s("Int")
			case "NullFloat64":
				return s("Float")
			case "NullBool":
				return s("Bool")
			case "NullTime":
				return s("DateTime")
			}
		}
		if pkg := g.m.ByImport[path]; pkg != nil && pkg.Types[t.Sel.Name] != nil {
			return g.identTypeIn(pkg, t.Sel.Name, 0)
		}
	}
	return nil
}

func (g *gx) identType(f *File, name string, depth int) *string {
	return g.identTypeIn(f.Pkg, name, depth)
}

func (g *gx) identTypeIn(pkg *Pkg, name string, depth int) *string {
	s := func(v string) *string { return &v }
	switch {
	case name == "string":
		return s("Str")
	case name == "bool":
		return s("Bool")
	case name == "float32" || name == "float64":
		return s("Float")
	case strings.HasPrefix(name, "int") || strings.HasPrefix(name, "uint") || name == "byte" || name == "rune":
		return s("Int")
	}
	if ti := pkg.Types[name]; ti != nil && depth < 3 {
		if id, ok := ti.Under.(*ast.Ident); ok {
			return g.identTypeIn(ti.Pkg, id.Name, depth+1)
		}
	}
	return nil
}

func (g *gx) buildTable(t *xTable) {
	ti := t.ti
	// the name
	t.Table = plural(toDBName(ti.Name))
	if fn := ti.Pkg.Methods[ti.Name]["TableName"]; fn != nil {
		name := ""
		ok := false
		if len(fn.Body.List) == 1 {
			if r, isRet := fn.Body.List[0].(*ast.ReturnStmt); isRet && len(r.Results) == 1 {
				name, ok = g.m.constString(g.m.EnvOf(fn), r.Results[0])
			}
		}
		if ok && name != "" {
			t.Table = name
		} else {
			t.NameUnread = true
			g.unreadAt(fn.File, fn.Pos, "table", "TableName of "+ti.Name+" is not a string constant; the GORM default name is used")
		}
	}
	t.Line = g.line(ti.Struct.Pos())
	var srcs []colSrc
	var rels []fieldInfo
	g.flattenCols(ti, "", 3, &srcs, &rels)
	// primary key: a tagged field, else a field named ID
	anyPK := false
	for _, cs := range srcs {
		tag := gormTag(cs.f.tag)
		if cs.synth == "ID" || hasKey(tag, "PRIMARYKEY") || hasKey(tag, "PRIMARY_KEY") {
			anyPK = true
		}
	}
	// the composite unique indexes: a named uniqueIndex that several fields share
	shared := map[string][]string{}
	var sharedOrder []string
	for _, cs := range srcs {
		tag := gormTag(cs.f.tag)
		if v, ok := tag["UNIQUEINDEX"]; ok {
			name, _, _ := strings.Cut(v, ",")
			name = strings.TrimSpace(name)
			if name == "" {
				continue
			}
			if _, seen := shared[name]; !seen {
				sharedOrder = append(sharedOrder, name)
			}
			shared[name] = append(shared[name], cs.col)
		}
	}
	seen := map[string]bool{}
	for _, cs := range srcs {
		if seen[cs.col] {
			continue
		}
		seen[cs.col] = true
		tag := gormTag(cs.f.tag)
		col := xColumn{Name: cs.col, Field: cs.f.name, Line: cs.line}
		switch cs.synth {
		case "ID":
			col.GoType, col.Type = "uint", strp("Int")
			col.PrimaryKey = true
		case "CreatedAt", "UpdatedAt":
			col.GoType, col.Type = "time.Time", strp("DateTime")
		case "DeletedAt":
			col.GoType, col.Type = "gorm.DeletedAt", strp("DateTime")
			col.Index = true
		default:
			col.GoType = g.goTypeText(cs.f.file, cs.f.typ)
			col.Type = g.latticeType(cs.f.file, cs.f.typ, tag)
			col.PrimaryKey = hasKey(tag, "PRIMARYKEY") || hasKey(tag, "PRIMARY_KEY") ||
				(!anyPK && cs.f.name == "ID")
			if v, ok := tag["DEFAULT"]; ok {
				d := v
				col.Default = &d
			}
			if hasKey(tag, "UNIQUE") {
				col.Unique = true
			}
			if v, ok := tag["UNIQUEINDEX"]; ok {
				name, _, _ := strings.Cut(v, ",")
				name = strings.TrimSpace(name)
				if name == "" || len(shared[name]) < 2 {
					col.Unique = true
				}
				col.Index = true
			}
			if hasKey(tag, "INDEX") {
				col.Index = true
			}
			if v, ok := tag["SIZE"]; ok {
				if n, err := strconv.Atoi(strings.TrimSpace(v)); err == nil {
					col.MaxLength = &n
				}
			}
			if col.MaxLength == nil {
				if mm := varcharRE.FindStringSubmatch(tag["TYPE"]); mm != nil {
					n, _ := strconv.Atoi(mm[1])
					col.MaxLength = &n
				}
			}
			if v, ok := tag["CHECK"]; ok && v != "" {
				expr := v
				if i := strings.Index(v, ","); i > 0 && !strings.ContainsAny(v[:i], " <>=()") {
					expr = strings.TrimSpace(v[i+1:])
				}
				t.Checks = append(t.Checks, expr)
			}
		}
		col.Nullable = !hasKey(tag, "NOT NULL") && !col.PrimaryKey
		if cs.synth != "" {
			col.Nullable = cs.synth == "DeletedAt"
		}
		col.Required = !col.Nullable && col.Default == nil
		t.Columns = append(t.Columns, col)
		t.fieldCols[cs.f.name] = cs.col
		if col.Type != nil {
			t.colTypes[cs.col] = *col.Type
		} else {
			t.colTypes[cs.col] = ""
		}
	}
	for _, name := range sharedOrder {
		if cols := shared[name]; len(cols) >= 2 {
			t.UniqueTogether = append(t.UniqueTogether, cols)
		}
	}
	// association tags: foreignKey / references name the key columns
	for _, f := range rels {
		tag := gormTag(f.tag)
		fk := tag["FOREIGNKEY"]
		if fk == "" {
			continue
		}
		rt, _ := g.relationship(f)
		isMany := g.m.TypeExpr(f.file, f.typ).Kind == "slice"
		ref := tag["REFERENCES"]
		if isMany {
			// the key is a column of the related table pointing at this one
			g.pendingFK = append(g.pendingFK, pendingFK{child: rt, field: fk, parent: t, refField: ref})
		} else {
			g.pendingFK = append(g.pendingFK, pendingFK{child: ti, field: fk, parent: g.tableFor2(rt), refField: ref})
		}
	}
}

func strp(s string) *string { return &s }

func (g *gx) tableFor2(ti *TypeInfo) *xTable {
	if ti == nil {
		return nil
	}
	return g.tables[ti]
}

type pendingFK struct {
	child    *TypeInfo
	field    string
	parent   *xTable
	refField string
}

// resolveForeignKeys fills FieldSpec.foreign_key from the association tags read above.
func (g *gx) resolveForeignKeys() {
	for _, p := range g.pendingFK {
		ct := g.tables[p.child]
		if ct == nil || p.parent == nil {
			continue
		}
		col, ok := ct.fieldCols[p.field]
		if !ok {
			continue
		}
		target := ""
		if p.refField != "" {
			target = p.parent.fieldCols[p.refField]
		}
		if target == "" {
			for _, c := range p.parent.Columns {
				if c.PrimaryKey {
					target = c.Name
					break
				}
			}
		}
		if target == "" {
			continue
		}
		for i := range ct.Columns {
			if ct.Columns[i].Name == col {
				v := p.parent.Table + "." + target
				ct.Columns[i].ForeignKey = &v
			}
		}
	}
}
