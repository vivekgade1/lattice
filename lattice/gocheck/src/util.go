package main

import (
	"go/ast"
	"go/token"
	"reflect"
	"strconv"
	"strings"
	"unicode"
)

func unquote(s string) (string, error) { return strconv.Unquote(s) }

// httpStatus is net/http's status constants.
var httpStatus = map[string]int{
	"StatusContinue": 100, "StatusSwitchingProtocols": 101, "StatusProcessing": 102,
	"StatusEarlyHints": 103, "StatusOK": 200, "StatusCreated": 201, "StatusAccepted": 202,
	"StatusNonAuthoritativeInfo": 203, "StatusNoContent": 204, "StatusResetContent": 205,
	"StatusPartialContent": 206, "StatusMultiStatus": 207, "StatusAlreadyReported": 208,
	"StatusIMUsed": 226, "StatusMultipleChoices": 300, "StatusMovedPermanently": 301,
	"StatusFound": 302, "StatusSeeOther": 303, "StatusNotModified": 304, "StatusUseProxy": 305,
	"StatusTemporaryRedirect": 307, "StatusPermanentRedirect": 308, "StatusBadRequest": 400,
	"StatusUnauthorized": 401, "StatusPaymentRequired": 402, "StatusForbidden": 403,
	"StatusNotFound": 404, "StatusMethodNotAllowed": 405, "StatusNotAcceptable": 406,
	"StatusProxyAuthRequired": 407, "StatusRequestTimeout": 408, "StatusConflict": 409,
	"StatusGone": 410, "StatusLengthRequired": 411, "StatusPreconditionFailed": 412,
	"StatusRequestEntityTooLarge": 413, "StatusRequestURITooLong": 414,
	"StatusUnsupportedMediaType": 415, "StatusRequestedRangeNotSatisfiable": 416,
	"StatusExpectationFailed": 417, "StatusTeapot": 418, "StatusMisdirectedRequest": 421,
	"StatusUnprocessableEntity": 422, "StatusLocked": 423, "StatusFailedDependency": 424,
	"StatusTooEarly": 425, "StatusUpgradeRequired": 426, "StatusPreconditionRequired": 428,
	"StatusTooManyRequests": 429, "StatusRequestHeaderFieldsTooLarge": 431,
	"StatusUnavailableForLegalReasons": 451, "StatusInternalServerError": 500,
	"StatusNotImplemented": 501, "StatusBadGateway": 502, "StatusServiceUnavailable": 503,
	"StatusGatewayTimeout": 504, "StatusHTTPVersionNotSupported": 505,
	"StatusVariantAlsoNegotiates": 506, "StatusInsufficientStorage": 507,
	"StatusLoopDetected": 508, "StatusNotExtended": 510,
	"StatusNetworkAuthenticationRequired": 511,
}

// constInt answers the value of an integer constant expression the checker can read: a
// literal, `http.StatusX`, or a module constant of one. -1 otherwise.
func (m *Module) constInt(e *Env, x ast.Expr) int {
	return m.constIntDepth(e.fn.File, e, x, 3)
}

func (m *Module) constIntDepth(f *File, e *Env, x ast.Expr, depth int) int {
	if depth < 0 {
		return -1
	}
	switch v := unparen(x).(type) {
	case *ast.BasicLit:
		if v.Kind == token.INT {
			n, err := strconv.Atoi(v.Value)
			if err == nil {
				return n
			}
		}
	case *ast.Ident:
		if e != nil {
			if _, local := e.lookup(v.Name); local {
				return -1
			}
		}
		if ci, ok := f.Pkg.Consts[v.Name]; ok && ci.value != nil {
			return m.constIntDepth(ci.file, nil, ci.value, depth-1)
		}
	case *ast.SelectorExpr:
		x, ok := v.X.(*ast.Ident)
		if !ok {
			return -1
		}
		if e != nil {
			if _, local := e.lookup(x.Name); local {
				return -1
			}
		}
		path := f.Imports[x.Name]
		if path == httpPath {
			if n, ok := httpStatus[v.Sel.Name]; ok {
				return n
			}
			return -1
		}
		if pkg := m.ByImport[path]; pkg != nil {
			if ci, ok := pkg.Consts[v.Sel.Name]; ok && ci.value != nil {
				return m.constIntDepth(ci.file, nil, ci.value, depth-1)
			}
		}
	}
	return -1
}

// constString answers the value of a string constant expression: a literal, a module
// constant of one, or a conversion of one (`JobStatus("queued")`, `string(StatusQueued)`).
func (m *Module) constString(e *Env, x ast.Expr) (string, bool) {
	return m.constStringDepth(e.fn.File, e, x, 4)
}

func (m *Module) constStringDepth(f *File, e *Env, x ast.Expr, depth int) (string, bool) {
	if depth < 0 {
		return "", false
	}
	switch v := unparen(x).(type) {
	case *ast.BasicLit:
		return stringLit(v)
	case *ast.Ident:
		if e != nil {
			if _, local := e.lookup(v.Name); local {
				return "", false
			}
		}
		if ci, ok := f.Pkg.Consts[v.Name]; ok && ci.value != nil {
			return m.constStringDepth(ci.file, nil, ci.value, depth-1)
		}
	case *ast.SelectorExpr:
		x, ok := v.X.(*ast.Ident)
		if !ok {
			return "", false
		}
		if e != nil {
			if _, local := e.lookup(x.Name); local {
				return "", false
			}
		}
		if pkg := m.ByImport[f.Imports[x.Name]]; pkg != nil {
			if ci, ok := pkg.Consts[v.Sel.Name]; ok && ci.value != nil {
				return m.constStringDepth(ci.file, nil, ci.value, depth-1)
			}
		}
	case *ast.CallExpr:
		// a conversion: string(X), JobStatus("x"), models.JobStatus("x")
		if len(v.Args) != 1 {
			return "", false
		}
		conv := false
		switch fn := v.Fun.(type) {
		case *ast.Ident:
			conv = fn.Name == "string" || f.Pkg.Types[fn.Name] != nil
		case *ast.SelectorExpr:
			if x, ok := fn.X.(*ast.Ident); ok {
				if pkg := m.ByImport[f.Imports[x.Name]]; pkg != nil {
					conv = pkg.Types[fn.Sel.Name] != nil
				}
			}
		}
		if conv {
			return m.constStringDepth(f, e, v.Args[0], depth-1)
		}
	}
	return "", false
}

// toDBName is GORM's default column naming (NamingStrategy.ColumnName): snake case,
// an initialism kept together (UserID -> user_id, APIKey -> api_key).
func toDBName(name string) string {
	rs := []rune(name)
	var b strings.Builder
	for i, r := range rs {
		if unicode.IsUpper(r) {
			if i > 0 {
				prev := rs[i-1]
				nextLower := i+1 < len(rs) && unicode.IsLower(rs[i+1])
				if unicode.IsLower(prev) || unicode.IsDigit(prev) || (unicode.IsUpper(prev) && nextLower) {
					b.WriteByte('_')
				}
			}
			b.WriteRune(unicode.ToLower(r))
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// plural is GORM's default table naming for the common English endings.
func plural(s string) string {
	switch {
	case s == "":
		return s
	case strings.HasSuffix(s, "person"):
		return strings.TrimSuffix(s, "person") + "people"
	case strings.HasSuffix(s, "child"):
		return s + "ren"
	case strings.HasSuffix(s, "s") || strings.HasSuffix(s, "x") || strings.HasSuffix(s, "z") ||
		strings.HasSuffix(s, "ch") || strings.HasSuffix(s, "sh"):
		return s + "es"
	case strings.HasSuffix(s, "y") && len(s) > 1 && !strings.ContainsRune("aeiou", rune(s[len(s)-2])):
		return s[:len(s)-1] + "ies"
	case strings.HasSuffix(s, "lf") || strings.HasSuffix(s, "rf"):
		// jinzhu/inflection `([lr])f$` -> `${1}ves`: shelf -> shelves, wolf -> wolves
		return s[:len(s)-1] + "ves"
	case strings.HasSuffix(s, "fe") && len(s) > 2 && s[len(s)-3] != 'f':
		// `([^f])fe$` -> `${1}ves`: knife -> knives, life -> lives
		return s[:len(s)-2] + "ves"
	}
	return s + "s"
}

// gormTag answers the GORM tag's settings (keys upper-cased).
func gormTag(tag string) map[string]string {
	out := map[string]string{}
	g := reflect.StructTag(tag).Get("gorm")
	for _, part := range strings.Split(g, ";") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		k, v, _ := strings.Cut(part, ":")
		out[strings.ToUpper(strings.TrimSpace(k))] = strings.TrimSpace(v)
	}
	return out
}

// jsonHidden reports whether encoding/json leaves a field out (`json:"-"`).
func jsonHidden(tag string) bool {
	j, ok := reflect.StructTag(tag).Lookup("json")
	return ok && j == "-"
}

// jsonName answers the key encoding/json writes a field under.
func jsonName(f fieldInfo) string {
	j := reflect.StructTag(f.tag).Get("json")
	name, _, _ := strings.Cut(j, ",")
	if name == "" {
		return f.name
	}
	return name
}

// columnOf answers the database column of a field (GORM's tag, else its naming).
func columnOf(f fieldInfo) string {
	g := gormTag(f.tag)
	if c, ok := g["COLUMN"]; ok && c != "" {
		return c
	}
	return toDBName(f.name)
}

// normPath answers a route path's segments with parameters as "{}".
func normPath(p string) []string {
	p = strings.Trim(p, "/")
	if p == "" {
		return nil
	}
	segs := strings.Split(p, "/")
	for i, s := range segs {
		if strings.HasPrefix(s, ":") || strings.HasPrefix(s, "*") ||
			(strings.HasPrefix(s, "{") && strings.HasSuffix(s, "}")) || strings.HasPrefix(s, "<") {
			segs[i] = "{}"
		}
	}
	return segs
}

// samePath reports whether a route path of the code matches a spec path (exactly, or by
// its tail when the code's prefix could not be read).
func samePath(code string, exact bool, spec string) bool {
	a, b := normPath(code), normPath(spec)
	if exact {
		if len(a) != len(b) {
			return false
		}
	} else if len(a) > len(b) {
		return false
	} else {
		b = b[len(b)-len(a):]
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func exprString(fset *token.FileSet, e ast.Expr) string {
	var b strings.Builder
	writeExpr(&b, e)
	return b.String()
}

// writeExpr prints the common expression forms compactly (for messages).
func writeExpr(b *strings.Builder, e ast.Expr) {
	switch v := e.(type) {
	case *ast.Ident:
		b.WriteString(v.Name)
	case *ast.SelectorExpr:
		writeExpr(b, v.X)
		b.WriteByte('.')
		b.WriteString(v.Sel.Name)
	case *ast.CallExpr:
		writeExpr(b, v.Fun)
		b.WriteString("(")
		if len(v.Args) > 0 {
			b.WriteString("...")
		}
		b.WriteString(")")
	case *ast.StarExpr:
		b.WriteByte('*')
		writeExpr(b, v.X)
	case *ast.UnaryExpr:
		b.WriteString(v.Op.String())
		writeExpr(b, v.X)
	case *ast.BasicLit:
		b.WriteString(v.Value)
	case *ast.ParenExpr:
		writeExpr(b, v.X)
	case *ast.IndexExpr:
		writeExpr(b, v.X)
		b.WriteString("[...]")
	case *ast.CompositeLit:
		if v.Type != nil {
			writeExpr(b, v.Type)
		}
		b.WriteString("{...}")
	case *ast.ArrayType:
		b.WriteString("[]")
		writeExpr(b, v.Elt)
	case *ast.MapType:
		b.WriteString("map[...]")
	default:
		b.WriteString("...")
	}
}

func identsIn(n ast.Node) map[string]bool {
	out := map[string]bool{}
	if n == nil {
		return out
	}
	ast.Inspect(n, func(x ast.Node) bool {
		if _, ok := x.(*ast.FuncLit); ok {
			return false
		}
		if id, ok := x.(*ast.Ident); ok {
			out[id.Name] = true
		}
		return true
	})
	return out
}

func sortedKeys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sortStrings(out)
	return out
}

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}

func quoteList(s []string, sep string) string {
	q := make([]string, len(s))
	for i, x := range s {
		q[i] = "'" + x + "'"
	}
	return strings.Join(q, sep)
}

// callLine answers the line of a call's method name (a chain's link, not its start).
func (m *Module) callLine(c *ast.CallExpr) int {
	if sel, ok := c.Fun.(*ast.SelectorExpr); ok {
		return m.Line(sel.Sel.Pos())
	}
	return m.Line(c.Pos())
}

// intersect is the names in both a and b, in a's order.
func intersect(a, b []string) []string {
	var out []string
	for _, x := range a {
		for _, y := range b {
			if x == y {
				out = append(out, x)
				break
			}
		}
	}
	return out
}
