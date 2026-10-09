// Which Go structs are the spec's tables (GORM's naming, or a TableName method).
package main

import (
	"go/ast"
	"strings"
)

// Model is a struct mapped to a spec table.
type Model struct {
	TI    *TypeInfo
	Table string // the spec's table (schema) name
	SQL   string
	Cols  map[string]fieldInfo // column -> field (embedded structs flattened)
}

// FieldFor answers the field of a column.
func (md *Model) FieldFor(col string) (fieldInfo, bool) {
	f, ok := md.Cols[col]
	return f, ok
}

// ColumnOfField answers the column a field name is mapped to ("" for none).
func (md *Model) ColumnOfField(name string) string {
	for col, f := range md.Cols {
		if f.name == name {
			return col
		}
	}
	return ""
}

// tableNameMethod answers the literal a `TableName() string` method returns.
func (m *Module) tableNameMethod(ti *TypeInfo) (string, bool) {
	fn := ti.Pkg.Methods[ti.Name]["TableName"]
	if fn == nil || len(fn.Body.List) != 1 {
		return "", fn != nil
	}
	r, ok := fn.Body.List[0].(*ast.ReturnStmt)
	if !ok || len(r.Results) != 1 {
		return "", true
	}
	s, ok := stringLit(r.Results[0])
	if !ok {
		return "", true
	}
	return s, true
}

// Models maps the module's structs to the policy's tables.
func (m *Module) Models(pol *Policy) map[*TypeInfo]*Model {
	out := map[*TypeInfo]*Model{}
	bySQL := map[string]string{}
	for name, t := range pol.Tables {
		sql := t.SQL
		if sql == "" {
			sql = name
		}
		bySQL[sql] = name
	}
	for _, pkg := range m.Pkgs {
		for _, ti := range pkg.Types {
			if ti.Struct == nil {
				continue
			}
			sql, has := m.tableNameMethod(ti)
			table := ""
			if has {
				if sql == "" {
					continue // a computed table name: unknown
				}
				table = bySQL[sql]
			} else {
				sql = plural(toDBName(ti.Name))
				table = bySQL[sql]
				if table == "" {
					if t, ok := pol.Tables[ti.Name]; ok {
						table = ti.Name
						sql = t.SQL
					}
				}
			}
			if table == "" {
				continue
			}
			md := &Model{TI: ti, Table: table, SQL: sql, Cols: map[string]fieldInfo{}}
			m.flatten(ti, "", md.Cols, 3)
			out[ti] = md
		}
	}
	return out
}

// gormModelFields are the fields gorm.Model embeds.
var gormModelFields = []string{"ID", "CreatedAt", "UpdatedAt", "DeletedAt"}

func (m *Module) flatten(ti *TypeInfo, prefix string, into map[string]fieldInfo, depth int) {
	for _, f := range m.Fields(ti) {
		g := gormTag(f.tag)
		if _, skip := g["-"]; skip {
			continue
		}
		if f.embedded || g["EMBEDDED"] != "" || hasKey(g, "EMBEDDED") {
			t := m.TypeExpr(f.file, f.typ)
			if t.is("gorm.Model") {
				for _, n := range gormModelFields {
					into[prefix+toDBName(n)] = fieldInfo{name: n, file: f.file, owner: ti}
				}
				continue
			}
			if et := t.rowTypeAny(); et != nil && depth > 0 {
				p := prefix
				if ep, ok := g["EMBEDDEDPREFIX"]; ok {
					p += ep
				}
				m.flatten(et, p, into, depth-1)
				continue
			}
			if !f.embedded {
				continue
			}
		}
		if !ast.IsExported(f.name) {
			continue
		}
		into[prefix+columnOf(f)] = f
	}
}

func hasKey(g map[string]string, k string) bool {
	_, ok := g[k]
	return ok
}

// ModelOf answers the model of a type (a value, a pointer, a slice of either).
func (c *checker) ModelOf(t *T) *Model {
	if ti := t.rowType(true); ti != nil {
		return c.models[ti]
	}
	return nil
}

// modelByTable answers the model of a spec table (by schema or SQL name).
func (c *checker) modelByTable(name string) *Model {
	for _, md := range c.models {
		if md.Table == name || strings.EqualFold(md.SQL, name) {
			return md
		}
	}
	return nil
}

// modelsOfTable answers every struct mapped to a spec table.
func (c *checker) modelsOfTable(table string) []*Model {
	var out []*Model
	for _, md := range c.models {
		if md.Table == table {
			out = append(out, md)
		}
	}
	return out
}
