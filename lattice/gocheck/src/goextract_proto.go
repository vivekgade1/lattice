// goextract: the .proto reader (Spike I, D1). A stdlib-only reader of the module's *.proto
// files: the declared gRPC contract (services, unary rpcs, messages, enums) as the `grpc`
// key of lattice.goextract/1. Grammar accepted, nothing else: `syntax = "proto3"`;
// `package`; `import`; `option go_package`; `message` (scalar, message and enum fields,
// `repeated`, proto3 `optional`, `map<K,V>`, `oneof`, nested messages and enums,
// `reserved`); `enum`; `service` with `rpc` (the `stream` keyword marks the rpc streaming);
// the rpc option `(google.api.http)` (get|put|post|delete|patch, body; custom,
// response_body and additional_bindings are read and listed, the first binding is used);
// field options `(validate.rules).<t>.<rule>` (PGV) and `(buf.validate.field).<t>.<rule>`
// (protovalidate) for the subset gt gte lt lte const min_len max_len in, `required` for
// messages, enum.defined_only, repeated.min_items / max_items.
//
// Anything else (a syntax other than proto3, `edition`, `extend`, `group`, a custom option
// other than the two above, `import public|weak`, any other validate rule, `pattern`)
// becomes an `unread` item {what: proto_<kind>, file, line}; the enclosing element is kept
// without the construct, never approximated. A file that is not proto3 is read with the
// proto3 grammar and carries the unread item: its contents are not trusted.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// ---- output ----------------------------------------------------------------------------

type xProtoFile struct {
	File      string   `json:"file"`
	Package   string   `json:"package"`
	GoPackage string   `json:"go_package"`
	Imports   []string `json:"imports"`
}

type xHTTPRule struct {
	Method       string      `json:"method"`
	Path         string      `json:"path"`
	Body         string      `json:"body"`
	ResponseBody string      `json:"response_body,omitempty"`
	Custom       bool        `json:"custom,omitempty"`
	Additional   []xHTTPRule `json:"additional_bindings,omitempty"`
}

type xRPC struct {
	Name            string     `json:"name"`
	Input           string     `json:"input"`
	Output          string     `json:"output"`
	ClientStreaming bool       `json:"client_streaming"`
	ServerStreaming bool       `json:"server_streaming"`
	HTTP            *xHTTPRule `json:"http,omitempty"`
	File            string     `json:"file"`
	Line            int        `json:"line"`
	// from the registration reading (goextract_grpc.go)
	State        string        `json:"state,omitempty"`
	Handler      *xHandler     `json:"handler,omitempty"`
	Auth         string        `json:"auth,omitempty"`
	AuthReason   string        `json:"auth_reason,omitempty"`
	Codes        []xCode       `json:"codes,omitempty"`
	PlainError   bool          `json:"plain_error,omitempty"`
	Tables       []string      `json:"tables,omitempty"`
	Writes       []xWrite      `json:"writes,omitempty"`
	OwnerUses    []xOwner      `json:"owner_uses,omitempty"`
	HTTPBindings []xHTTPBind   `json:"http_bindings,omitempty"`
	Unread       []xUnread     `json:"unread,omitempty"`
	Calls        []string      `json:"calls,omitempty"`
	Params       []xProtoField `json:"-"`
	svc          *xService

	// E4c
	CustomOptions map[string]any  `json:"custom_options,omitempty"`
	optLost       map[string]bool // custom options set on the rpc whose value was not recorded
}

type xService struct {
	Name string  `json:"name"`
	File string  `json:"file"`
	Line int     `json:"line"`
	Rpcs []*xRPC `json:"rpcs"`
}

type xProtoField struct {
	Name   string         `json:"name"`
	Number int            `json:"number"`
	Type   string         `json:"type"`
	Label  string         `json:"label"`
	Key    string         `json:"key,omitempty"`
	Value  string         `json:"value,omitempty"`
	Oneof  string         `json:"oneof,omitempty"`
	Bounds map[string]any `json:"bounds,omitempty"`
	Line   int            `json:"line"`
	scope  string
}

type xProtoMessage struct {
	Name   string         `json:"name"`
	File   string         `json:"file"`
	Line   int            `json:"line"`
	Fields []*xProtoField `json:"fields"`
}

type xEnumValue struct {
	Name   string `json:"name"`
	Number int    `json:"number"`
}

type xProtoEnum struct {
	Name   string       `json:"name"`
	File   string       `json:"file"`
	Line   int          `json:"line"`
	Values []xEnumValue `json:"values"`
}

// ---- lexer -----------------------------------------------------------------------------

type pTok struct {
	kind byte // i ident, n number, s string, p punctuation, e end
	text string
	line int
}

func protoLex(src string) []pTok {
	var toks []pTok
	line := 1
	i := 0
	n := len(src)
	for i < n {
		c := src[i]
		switch {
		case c == '\n':
			line++
			i++
		case c == ' ' || c == '\t' || c == '\r':
			i++
		case c == '/' && i+1 < n && src[i+1] == '/':
			for i < n && src[i] != '\n' {
				i++
			}
		case c == '/' && i+1 < n && src[i+1] == '*':
			i += 2
			for i+1 < n && !(src[i] == '*' && src[i+1] == '/') {
				if src[i] == '\n' {
					line++
				}
				i++
			}
			i += 2
		case c == '"' || c == '\'':
			q := c
			start := line
			i++
			var b strings.Builder
			for i < n && src[i] != q {
				if src[i] == '\\' && i+1 < n {
					i++
					switch src[i] {
					case 'n':
						b.WriteByte('\n')
					case 't':
						b.WriteByte('\t')
					case 'r':
						b.WriteByte('\r')
					default:
						b.WriteByte(src[i])
					}
				} else {
					if src[i] == '\n' {
						line++
					}
					b.WriteByte(src[i])
				}
				i++
			}
			i++
			toks = append(toks, pTok{'s', b.String(), start})
		case c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'):
			j := i
			for j < n && (src[j] == '_' || (src[j] >= 'a' && src[j] <= 'z') || (src[j] >= 'A' && src[j] <= 'Z') || (src[j] >= '0' && src[j] <= '9')) {
				j++
			}
			toks = append(toks, pTok{'i', src[i:j], line})
			i = j
		case c >= '0' && c <= '9' || (c == '.' && i+1 < n && src[i+1] >= '0' && src[i+1] <= '9'):
			j := i
			for j < n && (src[j] == '.' || src[j] == '_' || (src[j] >= '0' && src[j] <= '9') || (src[j] >= 'a' && src[j] <= 'z') || (src[j] >= 'A' && src[j] <= 'Z') ||
				((src[j] == '+' || src[j] == '-') && j > i && (src[j-1] == 'e' || src[j-1] == 'E') && !strings.HasPrefix(strings.ToLower(src[i:]), "0x"))) {
				j++
			}
			toks = append(toks, pTok{'n', src[i:j], line})
			i = j
		default:
			toks = append(toks, pTok{'p', string(c), line})
			i++
		}
	}
	toks = append(toks, pTok{'e', "", line})
	return toks
}

// ---- tree of an option's aggregate value -----------------------------------------------

type pKV struct {
	key  string
	val  any // string, int64, float64, bool-as-ident string, []any, pAgg
	line int
}

type pAgg []pKV

func (a pAgg) get(key string) (any, bool) {
	for _, kv := range a {
		if kv.key == key {
			return kv.val, true
		}
	}
	return nil, false
}

// ---- parser ----------------------------------------------------------------------------

type protoFile struct {
	rel       string
	abs       string
	syntax    string
	pkg       string
	goPackage string
	imports   []string
	messages  []*xProtoMessage
	enums     []*xProtoEnum
	services  []*xService
	// declared full names -> "message" | "enum" (for type resolution)
	symbols map[string]string
	// resolution queue
	rpcs   []*xRPC
	fields []*xProtoField
}

type protoParser struct {
	g    *gx
	f    *protoFile
	toks []pTok
	i    int

	rpcOpt *pKV // E4c: the custom option the last rpc-level parseOption read (val nil: not recorded)
}

func (p *protoParser) peek() pTok { return p.toks[p.i] }
func (p *protoParser) next() pTok {
	t := p.toks[p.i]
	if t.kind != 'e' {
		p.i++
	}
	return t
}
func (p *protoParser) is(kind byte, text string) bool {
	t := p.peek()
	return t.kind == kind && (text == "" || t.text == text)
}
func (p *protoParser) accept(kind byte, text string) bool {
	if p.is(kind, text) {
		p.i++
		return true
	}
	return false
}

func (p *protoParser) unread(line int, what, reason string) {
	p.g.addUnread(p.f.rel, line, "proto_"+what, reason)
}

// skipStatement skips to the end of the current statement: past a `;`, or a balanced `{}`.
func (p *protoParser) skipStatement() {
	depth := 0
	for {
		t := p.next()
		switch {
		case t.kind == 'e':
			return
		case t.kind == 'p' && t.text == "{":
			depth++
		case t.kind == 'p' && t.text == "}":
			depth--
			if depth <= 0 {
				return
			}
		case t.kind == 'p' && t.text == ";" && depth == 0:
			return
		}
	}
}

// fullIdent reads `a.b.c` (an identifier path), with an optional leading dot.
func (p *protoParser) fullIdent() string {
	var b strings.Builder
	if p.accept('p', ".") {
		b.WriteByte('.')
	}
	t := p.next()
	b.WriteString(t.text)
	for p.is('p', ".") && p.toks[p.i+1].kind == 'i' {
		p.i++
		b.WriteByte('.')
		b.WriteString(p.next().text)
	}
	return b.String()
}

func (p *protoParser) parseFile() {
	for !p.is('e', "") {
		t := p.peek()
		switch {
		case t.kind == 'i' && t.text == "syntax":
			p.i++
			p.accept('p', "=")
			s := p.next()
			p.accept('p', ";")
			p.f.syntax = s.text
			if s.text != "proto3" {
				p.unread(t.line, "proto2", fmt.Sprintf("syntax %q is not proto3: the file is read with the proto3 grammar and is not trusted", s.text))
			}
		case t.kind == 'i' && t.text == "edition":
			p.i++
			p.accept('p', "=")
			s := p.next()
			p.accept('p', ";")
			p.f.syntax = "edition"
			p.unread(t.line, "edition", fmt.Sprintf("edition %q is not read: the file is read with the proto3 grammar and is not trusted", s.text))
		case t.kind == 'i' && t.text == "package":
			p.i++
			p.f.pkg = p.fullIdent()
			p.accept('p', ";")
		case t.kind == 'i' && t.text == "import":
			p.i++
			mod := ""
			if p.is('i', "public") || p.is('i', "weak") {
				mod = p.next().text
			}
			path := p.next().text
			p.accept('p', ";")
			p.f.imports = append(p.f.imports, path)
			if mod != "" {
				p.unread(t.line, "import_"+mod, fmt.Sprintf("import %s %q is not read", mod, path))
			}
		case t.kind == 'i' && t.text == "option":
			p.i++
			p.parseOption("file", "", t.line)
		case t.kind == 'i' && t.text == "message":
			p.i++
			p.parseMessage(p.f.pkg, t.line)
		case t.kind == 'i' && t.text == "enum":
			p.i++
			p.parseEnum(p.f.pkg, t.line)
		case t.kind == 'i' && t.text == "service":
			p.i++
			p.parseService(t.line)
		case t.kind == 'i' && t.text == "extend":
			p.i++
			p.unread(t.line, "extend", "extend is not read")
			p.skipStatement()
		case t.kind == 'p' && t.text == ";":
			p.i++
		default:
			p.unread(t.line, "statement", fmt.Sprintf("unexpected %q at the top level", t.text))
			p.skipStatement()
		}
	}
}

func qualify(scope, name string) string {
	if scope == "" {
		return name
	}
	return scope + "." + name
}

// parseOption reads `<name> = <value> ;` after the `option` keyword. kind is file, message,
// service, rpc or enum; for rpc the return is the (google.api.http) rule.
func (p *protoParser) parseOption(kind, scope string, line int) *xHTTPRule {
	name, custom := p.optionName()
	if !p.accept('p', "=") {
		p.skipStatement()
		return nil
	}
	val := p.optionValue()
	p.accept('p', ";")
	if !custom {
		if kind == "file" && name == "go_package" {
			if s, ok := val.(string); ok {
				p.f.goPackage = s
			}
		}
		return nil
	}
	if kind == "rpc" && name == "(google.api.http)" {
		if agg, ok := val.(pAgg); ok {
			return p.httpRule(agg, line)
		}
	}
	scalar := true // E4c: an rpc-level scalar or enum identifier is recorded on the rpc
	switch v := val.(type) {
	case pIdent:
		val = string(v)
	case string, int64, float64:
	default:
		val, scalar = nil, false
	}
	if kind == "rpc" {
		p.rpcOpt = &pKV{key: name, val: val, line: line}
		if scalar {
			return nil
		}
	}
	p.unread(line, "custom_option", fmt.Sprintf("the custom option %s is not read", name))
	return nil
}

// optionName reads `(a.b).c.d` or `name`; custom is true for a parenthesised name.
func (p *protoParser) optionName() (string, bool) {
	var b strings.Builder
	custom := false
	if p.accept('p', "(") {
		custom = true
		b.WriteString("(" + p.fullIdent() + ")")
		p.accept('p', ")")
	} else {
		b.WriteString(p.fullIdent())
	}
	for p.is('p', ".") {
		p.i++
		b.WriteByte('.')
		b.WriteString(p.next().text)
	}
	return b.String(), custom
}

// optionValue reads a scalar or an aggregate `{ ... }` or a list `[ ... ]`.
func (p *protoParser) optionValue() any {
	t := p.peek()
	switch {
	case t.kind == 'p' && t.text == "{":
		p.i++
		return p.aggregate()
	case t.kind == 'p' && t.text == "[":
		p.i++
		var out []any
		for !p.is('p', "]") && !p.is('e', "") {
			out = append(out, p.optionValue())
			p.accept('p', ",")
		}
		p.accept('p', "]")
		return out
	case t.kind == 's':
		var b strings.Builder
		for p.is('s', "") {
			b.WriteString(p.next().text)
		}
		return b.String()
	case t.kind == 'p' && t.text == "-":
		p.i++
		return negate(p.optionValue())
	case t.kind == 'n':
		p.i++
		return numberValue(t.text)
	case t.kind == 'i':
		p.i++
		return identValue(t.text)
	}
	p.i++
	return nil
}

type pIdent string

func identValue(s string) any { return pIdent(s) }

func numberValue(s string) any {
	if n, err := strconv.ParseInt(s, 0, 64); err == nil {
		return n
	}
	if n, err := strconv.ParseUint(s, 0, 64); err == nil {
		return float64(n)
	}
	if f, err := strconv.ParseFloat(strings.TrimSuffix(s, "f"), 64); err == nil {
		if f == float64(int64(f)) && !strings.ContainsAny(s, ".eE") {
			return int64(f)
		}
		return f
	}
	return s
}

func negate(v any) any {
	switch x := v.(type) {
	case int64:
		return -x
	case float64:
		return -x
	}
	return v
}

// aggregate reads the body of `{ key: value key { ... } key: [a, b] ... }` (after the `{`).
func (p *protoParser) aggregate() pAgg {
	var out pAgg
	for !p.is('p', "}") && !p.is('e', "") {
		kt := p.next()
		key := kt.text
		if kt.kind == 'p' && kt.text == "[" { // an extension key [pkg.ext]
			key = "[" + p.fullIdent() + "]"
			p.accept('p', "]")
		}
		if kt.kind == 'p' && kt.text != "[" { // stray punctuation
			continue
		}
		p.accept('p', ":")
		out = append(out, pKV{key: key, val: p.optionValue(), line: kt.line})
		if !p.accept('p', ",") {
			p.accept('p', ";")
		}
	}
	p.accept('p', "}")
	return out
}

func (p *protoParser) httpRule(agg pAgg, line int) *xHTTPRule {
	r := &xHTTPRule{}
	found := false
	for _, kv := range agg {
		switch kv.key {
		case "get", "put", "post", "delete", "patch":
			if s, ok := kv.val.(string); ok && !found {
				r.Method, r.Path, found = kv.key, s, true
			}
		case "custom":
			if c, ok := kv.val.(pAgg); ok && !found {
				kind, _ := c.get("kind")
				path, _ := c.get("path")
				r.Method, r.Path, r.Custom, found = strings.ToLower(fmt.Sprint(kind)), fmt.Sprint(path), true, true
			}
		case "body":
			if s, ok := kv.val.(string); ok {
				r.Body = s
			}
		case "response_body":
			if s, ok := kv.val.(string); ok {
				r.ResponseBody = s
			}
		case "additional_bindings":
			if a, ok := kv.val.(pAgg); ok {
				extra := p.httpRule(a, kv.line)
				r.Additional = append(r.Additional, *extra)
			}
		}
	}
	_ = line
	return r
}

func (p *protoParser) parseEnum(scope string, line int) {
	name := p.next().text
	full := qualify(scope, name)
	e := &xProtoEnum{Name: full, File: p.f.rel, Line: line, Values: []xEnumValue{}}
	p.f.symbols[full] = "enum"
	p.f.enums = append(p.f.enums, e)
	p.accept('p', "{")
	for !p.is('p', "}") && !p.is('e', "") {
		t := p.peek()
		switch {
		case t.kind == 'i' && t.text == "option":
			p.i++
			p.parseOption("enum", full, t.line)
		case t.kind == 'i' && t.text == "reserved":
			p.skipStatement()
		case t.kind == 'p' && t.text == ";":
			p.i++
		case t.kind == 'i':
			p.i++
			p.accept('p', "=")
			neg := p.accept('p', "-")
			num := p.next()
			v, _ := numberValue(num.text).(int64)
			if neg {
				v = -v
			}
			e.Values = append(e.Values, xEnumValue{Name: t.text, Number: int(v)})
			if p.accept('p', "[") { // value options
				p.skipUntil("]")
			}
			p.accept('p', ";")
		default:
			p.i++
		}
	}
	p.accept('p', "}")
}

// skipUntil skips tokens through the matching closer of an already-consumed opener.
func (p *protoParser) skipUntil(closer string) {
	depth := 1
	open := map[string]string{"]": "[", "}": "{", ")": "("}[closer]
	for depth > 0 && !p.is('e', "") {
		t := p.next()
		if t.kind == 'p' && t.text == open {
			depth++
		} else if t.kind == 'p' && t.text == closer {
			depth--
		}
	}
}

var protoScalars = map[string]bool{
	"double": true, "float": true, "int32": true, "int64": true, "uint32": true, "uint64": true,
	"sint32": true, "sint64": true, "fixed32": true, "fixed64": true, "sfixed32": true,
	"sfixed64": true, "bool": true, "string": true, "bytes": true,
}

func (p *protoParser) parseMessage(scope string, line int) {
	name := p.next().text
	full := qualify(scope, name)
	m := &xProtoMessage{Name: full, File: p.f.rel, Line: line, Fields: []*xProtoField{}}
	p.f.symbols[full] = "message"
	p.f.messages = append(p.f.messages, m)
	p.accept('p', "{")
	p.messageBody(m, full, "")
	p.accept('p', "}")
}

// messageBody reads fields and nested declarations until the closing brace (not consumed);
// oneof is the enclosing oneof's name.
func (p *protoParser) messageBody(m *xProtoMessage, full, oneof string) {
	for !p.is('p', "}") && !p.is('e', "") {
		t := p.peek()
		switch {
		case t.kind == 'p' && t.text == ";":
			p.i++
		case t.kind == 'i' && t.text == "message" && oneof == "":
			p.i++
			p.parseMessage(full, t.line)
		case t.kind == 'i' && t.text == "enum" && oneof == "":
			p.i++
			p.parseEnum(full, t.line)
		case t.kind == 'i' && t.text == "option":
			p.i++
			p.parseOption("message", full, t.line)
		case t.kind == 'i' && (t.text == "reserved" || t.text == "extensions"):
			p.skipStatement()
		case t.kind == 'i' && t.text == "extend" && oneof == "":
			p.i++
			p.unread(t.line, "extend", "extend is not read")
			p.skipStatement()
		case t.kind == 'i' && t.text == "oneof" && oneof == "":
			p.i++
			on := p.next().text
			p.accept('p', "{")
			p.messageBody(m, full, on)
			p.accept('p', "}")
		case t.kind == 'i' && t.text == "map" && p.toks[p.i+1].text == "<":
			p.i += 2
			key := p.fullIdent()
			p.accept('p', ",")
			val := p.fullIdent()
			p.accept('p', ">")
			p.mapField(m, full, oneof, key, val, t.line)
		default:
			p.parseField(m, full, oneof, t)
		}
	}
}

func (p *protoParser) mapField(m *xProtoMessage, full, oneof, key, val string, line int) {
	name := p.next().text
	p.accept('p', "=")
	num, _ := numberValue(p.next().text).(int64)
	f := &xProtoField{Name: name, Number: int(num), Type: "map", Label: "map", Key: key, Value: val, Oneof: oneof, Line: line, scope: full}
	p.fieldOptions(f)
	p.accept('p', ";")
	m.Fields = append(m.Fields, f)
	p.f.fields = append(p.f.fields, f)
}

func (p *protoParser) parseField(m *xProtoMessage, full, oneof string, first pTok) {
	label := "single"
	switch {
	case first.kind == 'i' && first.text == "repeated":
		p.i++
		label = "repeated"
	case first.kind == 'i' && first.text == "optional":
		p.i++
		label = "optional"
	case first.kind == 'i' && first.text == "required":
		p.i++
	}
	if p.is('i', "group") {
		gl := p.next().line
		p.unread(gl, "group", "group is not read")
		p.skipStatement()
		return
	}
	typ := p.fullIdent()
	nameTok := p.next()
	if nameTok.kind != 'i' {
		p.unread(first.line, "statement", fmt.Sprintf("unexpected %q in a message", nameTok.text))
		p.skipStatement()
		return
	}
	p.accept('p', "=")
	num, _ := numberValue(p.next().text).(int64)
	f := &xProtoField{Name: nameTok.text, Number: int(num), Type: typ, Label: label, Oneof: oneof, Line: first.line, scope: full}
	if first.kind == 'i' && (first.text == "repeated" || first.text == "optional" || first.text == "required") {
		f.Line = first.line
	} else {
		f.Line = first.line
	}
	p.fieldOptions(f)
	p.accept('p', ";")
	m.Fields = append(m.Fields, f)
	p.f.fields = append(p.f.fields, f)
}

// fieldOptions reads `[ opt = value, ... ]` after a field's number.
func (p *protoParser) fieldOptions(f *xProtoField) {
	if !p.accept('p', "[") {
		return
	}
	rules := pAgg{}
	pgv := pAgg{}
	for !p.is('p', "]") && !p.is('e', "") {
		line := p.peek().line
		name, custom := p.optionName()
		p.accept('p', "=")
		val := p.optionValue()
		p.accept('p', ",")
		if !custom {
			continue // json_name, deprecated, packed, ...: standard options
		}
		switch {
		case name == "(validate.rules)" || strings.HasPrefix(name, "(validate.rules)."):
			pgv = append(pgv, pKV{key: strings.TrimPrefix(strings.TrimPrefix(name, "(validate.rules)"), "."), val: val, line: line})
		case name == "(buf.validate.field)" || strings.HasPrefix(name, "(buf.validate.field)."):
			rules = append(rules, pKV{key: strings.TrimPrefix(strings.TrimPrefix(name, "(buf.validate.field)"), "."), val: val, line: line})
		default:
			p.unread(line, "custom_option", fmt.Sprintf("the custom option %s is not read", name))
		}
	}
	p.accept('p', "]")
	bounds := map[string]any{}
	p.readRules(f, "validate.rules", pgv, bounds)
	p.readRules(f, "buf.validate.field", rules, bounds)
	if len(bounds) > 0 {
		f.Bounds = bounds
	}
}

// ruleSets are the supported rules per validate rule family.
var numericRules = map[string]string{"gt": "gt", "gte": "ge", "lt": "lt", "lte": "le", "const": "const", "in": "choices"}
var textRules = map[string]string{"min_len": "min_length", "max_len": "max_length", "const": "const", "in": "choices"}

var ruleFamilies = map[string]map[string]string{
	"double": numericRules, "float": numericRules, "int32": numericRules, "int64": numericRules,
	"uint32": numericRules, "uint64": numericRules, "sint32": numericRules, "sint64": numericRules,
	"fixed32": numericRules, "fixed64": numericRules, "sfixed32": numericRules, "sfixed64": numericRules,
	"string": textRules, "bytes": textRules,
	"bool":     {"const": "const"},
	"enum":     {"defined_only": "defined_only", "const": "const", "in": "choices"},
	"repeated": {"min_items": "min_items", "max_items": "max_items"},
	"message":  {"required": "required"},
}

// readRules folds one family's option entries (the path `string.min_len` or `string` with an
// aggregate) into bounds; a rule outside the supported subset is an unread item.
func (p *protoParser) readRules(f *xProtoField, family string, entries pAgg, bounds map[string]any) {
	set := func(typ, rule string, val any, line int) {
		fam := ruleFamilies[typ]
		if fam == nil {
			p.unread(line, "validate_rule", fmt.Sprintf("%s: rule family %q is not read", family, typ))
			return
		}
		to, ok := fam[rule]
		if !ok {
			p.unread(line, "validate_rule", fmt.Sprintf("%s: %s.%s is not read", family, typ, rule))
			return
		}
		if to == "choices" {
			bounds[to] = choiceList(val)
		} else if to == "defined_only" || to == "required" {
			bounds[to] = fmt.Sprint(val) == "true"
		} else if s, isIdent := val.(pIdent); isIdent {
			switch s {
			case "true":
				bounds[to] = true
			case "false":
				bounds[to] = false
			default:
				bounds[to] = string(s)
			}
		} else {
			bounds[to] = val
		}
	}
	for _, kv := range entries {
		parts := strings.Split(kv.key, ".")
		switch {
		case len(parts) == 1 && parts[0] == "" && family == "buf.validate.field":
			continue
		case len(parts) == 1 && parts[0] == "required" && family == "buf.validate.field":
			bounds["required"] = fmt.Sprint(kv.val) == "true"
		case len(parts) == 1 && ruleFamilies[parts[0]] != nil:
			if agg, ok := kv.val.(pAgg); ok {
				for _, in := range agg {
					set(parts[0], in.key, in.val, in.line)
				}
			} else {
				p.unread(kv.line, "validate_rule", family+": "+parts[0]+" has no rule body")
			}
		case len(parts) == 2:
			set(parts[0], parts[1], kv.val, kv.line)
		default:
			p.unread(kv.line, "validate_rule", fmt.Sprintf("%s: %s is not read", family, kv.key))
		}
	}
}

func choiceList(v any) []any {
	if l, ok := v.([]any); ok {
		out := make([]any, 0, len(l))
		for _, x := range l {
			if s, ok := x.(pIdent); ok {
				out = append(out, string(s))
			} else {
				out = append(out, x)
			}
		}
		return out
	}
	return []any{v}
}

func (p *protoParser) parseService(line int) {
	name := p.next().text
	full := qualify(p.f.pkg, name)
	svc := &xService{Name: full, File: p.f.rel, Line: line, Rpcs: []*xRPC{}}
	p.f.services = append(p.f.services, svc)
	p.accept('p', "{")
	for !p.is('p', "}") && !p.is('e', "") {
		t := p.peek()
		switch {
		case t.kind == 'i' && t.text == "rpc":
			p.i++
			p.parseRPC(svc, t.line)
		case t.kind == 'i' && t.text == "option":
			p.i++
			p.parseOption("service", full, t.line)
		case t.kind == 'p' && t.text == ";":
			p.i++
		default:
			p.unread(t.line, "statement", fmt.Sprintf("unexpected %q in a service", t.text))
			p.skipStatement()
		}
	}
	p.accept('p', "}")
}

func (p *protoParser) parseRPC(svc *xService, line int) {
	name := p.next().text
	r := &xRPC{Name: name, File: p.f.rel, Line: line, svc: svc}
	p.accept('p', "(")
	if p.is('i', "stream") && p.toks[p.i+1].kind == 'i' {
		p.i++
		r.ClientStreaming = true
	}
	r.Input = p.fullIdent()
	p.accept('p', ")")
	p.accept('i', "returns")
	p.accept('p', "(")
	if p.is('i', "stream") && p.toks[p.i+1].kind == 'i' {
		p.i++
		r.ServerStreaming = true
	}
	r.Output = p.fullIdent()
	p.accept('p', ")")
	if p.accept('p', "{") {
		for !p.is('p', "}") && !p.is('e', "") {
			t := p.peek()
			switch {
			case t.kind == 'i' && t.text == "option":
				p.i++
				if rule := p.parseOption("rpc", svc.Name, t.line); rule != nil && r.HTTP == nil {
					r.HTTP = rule
				}
				if o := p.rpcOpt; o != nil {
					p.rpcOpt = nil
					if o.val == nil {
						if r.optLost == nil {
							r.optLost = map[string]bool{}
						}
						r.optLost[o.key] = true
					} else {
						if r.CustomOptions == nil {
							r.CustomOptions = map[string]any{}
						}
						r.CustomOptions[o.key] = o.val
					}
				}
			case t.kind == 'p' && t.text == ";":
				p.i++
			default:
				p.unread(t.line, "statement", fmt.Sprintf("unexpected %q in an rpc", t.text))
				p.skipStatement()
			}
		}
		p.accept('p', "}")
	} else {
		p.accept('p', ";")
	}
	svc.Rpcs = append(svc.Rpcs, r)
	p.f.rpcs = append(p.f.rpcs, r)
}

// ---- the module's protos ---------------------------------------------------------------

// knownProto names the imports that need not be present, with the symbols they declare.
var knownProtoSymbols = map[string]map[string]string{
	"google/protobuf/empty.proto":      {"google.protobuf.Empty": "message"},
	"google/protobuf/timestamp.proto":  {"google.protobuf.Timestamp": "message"},
	"google/protobuf/duration.proto":   {"google.protobuf.Duration": "message"},
	"google/protobuf/any.proto":        {"google.protobuf.Any": "message"},
	"google/protobuf/field_mask.proto": {"google.protobuf.FieldMask": "message"},
	"google/protobuf/wrappers.proto": {
		"google.protobuf.DoubleValue": "message", "google.protobuf.FloatValue": "message",
		"google.protobuf.Int64Value": "message", "google.protobuf.UInt64Value": "message",
		"google.protobuf.Int32Value": "message", "google.protobuf.UInt32Value": "message",
		"google.protobuf.BoolValue": "message", "google.protobuf.StringValue": "message",
		"google.protobuf.BytesValue": "message"},
	"google/protobuf/struct.proto": {
		"google.protobuf.Struct": "message", "google.protobuf.Value": "message",
		"google.protobuf.ListValue": "message", "google.protobuf.NullValue": "enum"},
	"google/protobuf/descriptor.proto": {
		"google.protobuf.FileOptions": "message", "google.protobuf.MessageOptions": "message",
		"google.protobuf.FieldOptions": "message", "google.protobuf.OneofOptions": "message",
		"google.protobuf.EnumOptions": "message", "google.protobuf.EnumValueOptions": "message",
		"google.protobuf.ServiceOptions": "message", "google.protobuf.MethodOptions": "message"},
	"google/api/annotations.proto": {},
	"google/api/http.proto":        {"google.api.HttpRule": "message", "google.api.Http": "message"},
	"validate/validate.proto":      {},
	"buf/validate/validate.proto":  {},
}

var skipProtoDirs = map[string]bool{"vendor": true, "testdata": true, "third_party": true, "node_modules": true, ".git": true}

// grpcConfig is the project's studio.toml [grpc] table (the two keys read).
type grpcConfig struct {
	ProtoPaths []string
	Protos     []string
}

// readGrpcConfig reads `proto_paths = [...]` and `protos = [...]` of the `[grpc]` table:
// a minimal TOML reader (one-line string arrays).
func readGrpcConfig(root string) grpcConfig {
	var cfg grpcConfig
	raw, err := os.ReadFile(filepath.Join(root, "studio.toml"))
	if err != nil {
		return cfg
	}
	in := false
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "[") {
			in = line == "[grpc]"
			continue
		}
		if !in {
			continue
		}
		key, val, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		var items []string
		for _, part := range strings.Split(strings.Trim(strings.TrimSpace(val), "[]"), ",") {
			part = strings.Trim(strings.TrimSpace(part), `"'`)
			if part != "" {
				items = append(items, part)
			}
		}
		switch key {
		case "proto_paths":
			cfg.ProtoPaths = items
		case "protos":
			cfg.Protos = items
		}
	}
	return cfg
}

// protoUniverse is every module .proto file read, with the symbols imports resolve to.
type protoUniverse struct {
	files   []*protoFile
	roots   []string
	symbols map[string]string // every full name declared anywhere read
}

// findProtoFiles lists the module's .proto files, slash-separated and relative to root.
func findProtoFiles(root string, cfg grpcConfig) []string {
	listed := map[string]bool{}
	for _, d := range cfg.ProtoPaths {
		listed[filepath.ToSlash(filepath.Clean(d))] = true
	}
	var out []string
	_ = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		rel, _ := filepath.Rel(root, path)
		rel = filepath.ToSlash(rel)
		if d.IsDir() {
			if rel != "." && skipProtoDirs[d.Name()] && !underListed(rel, listed) {
				return filepath.SkipDir
			}
			if strings.HasPrefix(d.Name(), ".") && rel != "." {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasSuffix(d.Name(), ".proto") {
			out = append(out, rel)
		}
		return nil
	})
	sort.Strings(out)
	return out
}

func underListed(rel string, listed map[string]bool) bool {
	for l := range listed {
		if rel == l || strings.HasPrefix(rel, l+"/") || strings.HasPrefix(l, rel+"/") {
			return true
		}
	}
	return false
}

// readProtos parses every module .proto file and resolves the types.
func (g *gx) readProtos(root string) *protoUniverse {
	cfg := readGrpcConfig(root)
	u := &protoUniverse{symbols: map[string]string{}}
	rels := findProtoFiles(root, cfg)
	// a listed `protos` entry that the walk did not find (outside the module's dirs)
	have := map[string]bool{}
	for _, r := range rels {
		have[r] = true
	}
	for _, r := range cfg.Protos {
		r = filepath.ToSlash(filepath.Clean(r))
		if !have[r] {
			if _, err := os.Stat(filepath.Join(root, r)); err == nil {
				rels = append(rels, r)
			}
		}
	}
	sort.Strings(rels)
	for _, rel := range rels {
		raw, err := os.ReadFile(filepath.Join(root, rel))
		if err != nil {
			g.addUnread(rel, 1, "proto_file", "cannot read the file: "+err.Error())
			continue
		}
		f := &protoFile{rel: rel, abs: filepath.Join(root, rel), symbols: map[string]string{}}
		p := &protoParser{g: g, f: f, toks: protoLex(string(raw))}
		p.parseFile()
		u.files = append(u.files, f)
	}
	// include roots: the module root, each listed path, and the root implied by a
	// package-shaped directory layout (notes/v1/x.proto under proto/)
	u.roots = append(u.roots, root)
	for _, d := range cfg.ProtoPaths {
		u.roots = append(u.roots, filepath.Join(root, d))
	}
	for _, f := range u.files {
		for sym, kind := range f.symbols {
			u.symbols[sym] = kind
		}
	}
	byRel := map[string]*protoFile{}
	for _, f := range u.files {
		byRel[f.rel] = f
	}
	for _, f := range u.files {
		g.resolveProtoFile(f, u, byRel, root)
	}
	return u
}

// importFile finds an import's file among the module's: relative to the importing file's
// directory, the include roots, or the directory the package layout implies.
func importFile(f *protoFile, imp string, u *protoUniverse, byRel map[string]*protoFile, root string) *protoFile {
	cands := []string{filepath.ToSlash(filepath.Join(filepath.Dir(f.rel), imp))}
	for _, r := range u.roots {
		rel, err := filepath.Rel(root, filepath.Join(r, imp))
		if err == nil {
			cands = append(cands, filepath.ToSlash(rel))
		}
	}
	// the implied root: strip the package path from this file's relative path
	pkgDir := strings.ReplaceAll(f.pkg, ".", "/")
	dir := filepath.ToSlash(filepath.Dir(f.rel))
	if pkgDir != "" && strings.HasSuffix(dir, pkgDir) {
		base := strings.TrimSuffix(strings.TrimSuffix(dir, pkgDir), "/")
		cands = append(cands, filepath.ToSlash(filepath.Join(base, imp)))
	}
	for _, c := range cands {
		if x := byRel[c]; x != nil {
			return x
		}
	}
	return nil
}

func (g *gx) resolveProtoFile(f *protoFile, u *protoUniverse, byRel map[string]*protoFile, root string) {
	// the symbols visible to the file: its own, the known imports', the module imports'
	visible := map[string]string{}
	for k, v := range f.symbols {
		visible[k] = v
	}
	for _, imp := range f.imports {
		if syms, ok := knownProtoSymbols[imp]; ok {
			for k, v := range syms {
				visible[k] = v
			}
			continue
		}
		if x := importFile(f, imp, u, byRel, root); x != nil {
			for k, v := range x.symbols {
				visible[k] = v
			}
			continue
		}
		g.addUnread(f.rel, 1, "proto_import_unresolved", fmt.Sprintf("import %q is neither a module file nor a known import", imp))
	}
	resolve := func(scope, name string, line int, what string) string {
		if protoScalars[name] {
			return name
		}
		if strings.HasPrefix(name, ".") {
			return strings.TrimPrefix(name, ".")
		}
		for s := scope; ; {
			cand := qualify(s, name)
			if _, ok := visible[cand]; ok {
				return cand
			}
			if s == "" {
				break
			}
			if i := strings.LastIndexByte(s, '.'); i >= 0 {
				s = s[:i]
			} else {
				s = ""
			}
		}
		g.addUnread(f.rel, line, "proto_type_unresolved", fmt.Sprintf("%s %q is not declared in the file or its imports", what, name))
		return name
	}
	for _, fl := range f.fields {
		if fl.Label == "map" {
			fl.Key = resolve(fl.scope, fl.Key, fl.Line, "map key type")
			fl.Value = resolve(fl.scope, fl.Value, fl.Line, "map value type")
			continue
		}
		fl.Type = resolve(fl.scope, fl.Type, fl.Line, "field type")
	}
	for _, r := range f.rpcs {
		r.Input = resolve(f.pkg, r.Input, r.Line, "rpc input type")
		r.Output = resolve(f.pkg, r.Output, r.Line, "rpc output type")
	}
}

// protoOutput assembles the contract part of the `grpc` key.
func (u *protoUniverse) output() (protos []xProtoFile, services []*xService, messages []*xProtoMessage, enums []*xProtoEnum) {
	protos, services, messages, enums = []xProtoFile{}, []*xService{}, []*xProtoMessage{}, []*xProtoEnum{}
	for _, f := range u.files {
		imps := append([]string{}, f.imports...)
		sort.Strings(imps)
		if imps == nil {
			imps = []string{}
		}
		protos = append(protos, xProtoFile{File: f.rel, Package: f.pkg, GoPackage: f.goPackage, Imports: imps})
		services = append(services, f.services...)
		messages = append(messages, f.messages...)
		enums = append(enums, f.enums...)
	}
	return
}
