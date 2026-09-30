package rt

import (
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
)

// Json values of T2 procedures (plan 5a Task 10): json.dumps and json.loads as Python
// 3.14 computes them, and the operations a Json allows (index, ==, truth, str()).
//
// A Json is an `any` holding what Python would hold: nil (None), bool, int64, a
// *big.Int (an integer json.loads read beyond the 64-bit range), float64, string (a
// str json.loads read with a lone surrogate holds it as WTF-8, wtf8.go), or a
// container -- a *List[T], a *Dict[string, V] (a Counter or defaultdict included), a
// tuple (a generated struct
// with PyTuple) or an Opt[T] -- shared, not copied, as Python shares the object a Json
// was made from.

// PyTuple is implemented by the generated tuple types: their items.
type PyTuple interface{ PyTuple() []any }

// PyList is l's items, boxed.
func (l *List[T]) PyList() []any {
	out := make([]any, len(l.Items))
	for i, v := range l.Items {
		out[i] = v
	}
	return out
}

// PyDict is d's items, boxed, and d's kind.
func (d *Dict[K, V]) PyDict() (keys []any, vals []any, kind uint8, factory string) {
	for _, e := range d.entries {
		if e.live {
			keys = append(keys, e.key)
			vals = append(vals, e.val)
		}
	}
	return keys, vals, d.kind, d.fname
}

// PyOpt is o's value (nil when absent).
func (o Opt[T]) PyOpt() any {
	if !o.Ok {
		return nil
	}
	return o.V
}

type pyList interface{ PyList() []any }
type pyDict interface {
	PyDict() ([]any, []any, uint8, string)
	PyIndex(k any) any
}

// PyIndex is d[k] for a key of any type: a key of another type is missing (a Counter
// reads 0, a defaultdict stores factory(), a dict raises KeyError).
func (d *Dict[K, V]) PyIndex(k any) any {
	kk, ok := k.(K)
	if !ok {
		switch d.kind {
		case counterDict:
			return int64(0)
		case defaultDict:
			panic("rt: a defaultdict key of another type")
		}
		Raise("KeyError", Repr(k))
	}
	return d.GetP(kk, k)
}

type pyOpt interface{ PyOpt() any }

// jnorm is a Json value with optional values and None resolved.
func jnorm(v any) any {
	for {
		switch x := v.(type) {
		case pyOpt:
			v = x.PyOpt()
			continue
		case struct{}:
			return nil
		}
		return v
	}
}

// ---- json.dumps --------------------------------------------------------------------------

// JsonDumps is json.dumps(v, sort_keys=sortKeys, indent=indent) (indent nil: none):
// byte-identical to Python's (ensure_ascii, ", " and ": ", or "," with an indent;
// floats as repr, NaN / Infinity / -Infinity).
func JsonDumps(v any, sortKeys bool, indent *int64) string {
	var b strings.Builder
	e := jsonEnc{b: &b, sort: sortKeys, itemSep: ", "}
	if indent != nil {
		e.indent = strings.Repeat(" ", int(max(*indent, 0)))
		e.itemSep = ","
		e.pretty = true
	}
	e.value(v, 0)
	return b.String()
}

type jsonEnc struct {
	b       *strings.Builder
	sort    bool
	pretty  bool
	indent  string
	itemSep string
}

func (e *jsonEnc) newline(level int) {
	e.b.WriteByte('\n')
	for range level {
		e.b.WriteString(e.indent)
	}
}

func (e *jsonEnc) value(v any, level int) {
	if level > JsonMaxDepth {
		jsonDeep("encoding a JSON object")
	}
	v = jnorm(v)
	switch x := v.(type) {
	case nil:
		e.b.WriteString("null")
	case bool:
		if x {
			e.b.WriteString("true")
		} else {
			e.b.WriteString("false")
		}
	case int64:
		e.b.WriteString(strconv.FormatInt(x, 10))
	case *big.Int:
		e.b.WriteString(x.String())
	case float64:
		switch {
		case math.IsNaN(x):
			e.b.WriteString("NaN")
		case math.IsInf(x, 1):
			e.b.WriteString("Infinity")
		case math.IsInf(x, -1):
			e.b.WriteString("-Infinity")
		default:
			e.b.WriteString(ReprFloat(x))
		}
	case string:
		writeJSONRunes(e.b, pyRunes(x))
	case pyList:
		e.list(x.PyList(), level)
	case PyTuple:
		e.list(x.PyTuple(), level)
	case pyDict:
		keys, vals, _, _ := x.PyDict()
		e.dict(keys, vals, level)
	default:
		panic("rt.JsonDumps: not a Json value")
	}
}

func (e *jsonEnc) list(items []any, level int) {
	if len(items) == 0 {
		e.b.WriteString("[]")
		return
	}
	e.b.WriteByte('[')
	for i, x := range items {
		if i > 0 {
			e.b.WriteString(e.itemSep)
		}
		if e.pretty {
			e.newline(level + 1)
		}
		e.value(x, level+1)
	}
	if e.pretty {
		e.newline(level)
	}
	e.b.WriteByte(']')
}

func (e *jsonEnc) dict(keys, vals []any, level int) {
	if len(keys) == 0 {
		e.b.WriteString("{}")
		return
	}
	order := make([]int, len(keys))
	for i := range order {
		order[i] = i
	}
	if e.sort {
		sort.SliceStable(order, func(i, j int) bool {
			return jsonKeyLess(keys[order[i]], keys[order[j]])
		})
	}
	e.b.WriteByte('{')
	for n, i := range order {
		if n > 0 {
			e.b.WriteString(e.itemSep)
		}
		if e.pretty {
			e.newline(level + 1)
		}
		e.value(keys[i], level+1)
		e.b.WriteString(": ")
		e.value(vals[i], level+1)
	}
	if e.pretty {
		e.newline(level)
	}
	e.b.WriteByte('}')
}

// jsonKeyLess orders str keys as Python does (by code point).
func jsonKeyLess(a, b any) bool { return runeLess(jsonRunes(a), jsonRunes(b)) }

func jsonRunes(v any) []rune { return pyRunes(v.(string)) }

func runeLess(a, b []rune) bool {
	for i := 0; i < len(a) && i < len(b); i++ {
		if a[i] != b[i] {
			return a[i] < b[i]
		}
	}
	return len(a) < len(b)
}

// writeJSONRunes is encode_basestring_ascii of code points (lone surrogates as \uXXXX).
func writeJSONRunes(b *strings.Builder, rs []rune) {
	var plain []rune
	flush := func() {
		if len(plain) > 0 {
			var sb strings.Builder
			writeJSONString(&sb, string(plain), true)
			s := sb.String()
			b.WriteString(s[1 : len(s)-1])
			plain = plain[:0]
		}
	}
	b.WriteByte('"')
	for _, r := range rs {
		if r >= 0xD800 && r < 0xE000 {
			flush()
			writeU(b, r)
			continue
		}
		plain = append(plain, r)
	}
	flush()
	b.WriteByte('"')
}

// jsonCount is T2Fuel.dumps's walk: the value's nodes, its depth, and the text of its
// str values and dict keys (code points).
func jsonCount(f *Fuel, v any, d int) (nodes, depth, text int64) {
	type item struct {
		v any
		d int
	}
	stack := []item{{v, d}}
	for len(stack) > 0 {
		it := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		nodes++
		f.charge(1) // as counted: a value holding itself runs out of fuel
		depth = max(depth, int64(it.d))
		switch x := jnorm(it.v).(type) {
		case pyDict:
			keys, vals, _, _ := x.PyDict()
			for _, k := range keys {
				text += int64(len(jsonRunes(k)))
			}
			for _, y := range vals {
				stack = append(stack, item{y, it.d + 1})
			}
		case pyList:
			for _, y := range x.PyList() {
				stack = append(stack, item{y, it.d + 1})
			}
		case PyTuple:
			for _, y := range x.PyTuple() {
				stack = append(stack, item{y, it.d + 1})
			}
		case string:
			text += int64(len(pyRunes(x)))
		}
	}
	return nodes, depth, text
}

// JsonMaxDepth bounds the nesting json.loads, json.dumps, ==, and str() walk: CPython
// raises RecursionError at a depth its C stack sets (about 50,000 on macOS); Go raises
// it past this fixed depth (documented as a known limitation), never overflowing its
// own stack.
const JsonMaxDepth = 100_000

func jsonDeep(what string) {
	Raise("RecursionError", "maximum recursion depth exceeded while "+what)
}

// SizedDumps is `<fuel>.dumps(json.dumps, v, ...)`: the value's nodes are charged one
// each, the indentation's size (nodes x depth x indent) is capped before it runs, the
// text after.
func SizedDumps(f *Fuel, v any, sortKeys bool, indent *int64) string {
	nodes, depth, text := jsonCount(f, v, 1)
	step := int64(0)
	if indent != nil {
		step = max(*indent, 0)
	}
	f.Grow(text + nodes*(step*depth+4))
	out := JsonDumps(v, sortKeys, indent)
	f.Grow(int64(len(out))) // ASCII: bytes are code points
	return out
}

// ---- json.loads --------------------------------------------------------------------------

// JsonLoads is json.loads(s) (the C scanner): ints by the text (an int beyond the
// 64-bit range is a *big.Int), floats, objects as *Dict[string, any] in key order,
// arrays as *List[any]; invalid text raises ValueError with Python's message.
func JsonLoads(s string) any {
	rs := []rune(s)
	if len(rs) > 0 && rs[0] == 0xFEFF {
		jsonErr("Unexpected UTF-8 BOM (decode using utf-8-sig)", rs, 0)
	}
	p := &jsonScan{s: rs}
	i := p.ws(0)
	v, end := p.scanOnce(i)
	end = p.ws(end)
	if end != len(rs) {
		jsonErr("Extra data", rs, end)
	}
	return v
}

type jsonScan struct {
	s     []rune
	depth int
}

// jsonErr is JSONDecodeError(msg, doc, pos) (a ValueError).
func jsonErr(msg string, doc []rune, pos int) {
	line, col := 1, pos+1
	for i := 0; i < pos && i < len(doc); i++ {
		if doc[i] == '\n' {
			line++
			col = pos - i
		}
	}
	Raise("ValueError", msg+": line "+strconv.Itoa(line)+" column "+strconv.Itoa(col)+
		" (char "+strconv.Itoa(pos)+")")
}

func isJSONSpace(r rune) bool { return r == ' ' || r == '\t' || r == '\n' || r == '\r' }

func (p *jsonScan) ws(i int) int {
	for i < len(p.s) && isJSONSpace(p.s[i]) {
		i++
	}
	return i
}

func (p *jsonScan) at(i int) rune {
	if i < len(p.s) {
		return p.s[i]
	}
	return -1
}

func (p *jsonScan) lit(i int, word string) bool {
	rs := []rune(word)
	if i+len(rs)-1 >= len(p.s) {
		return false
	}
	for k, r := range rs {
		if p.s[i+k] != r {
			return false
		}
	}
	return true
}

// scanOnce is scan_once_unicode: the value at i and the index after it. A missing
// value is "Expecting value" at i (StopIteration in CPython).
func (p *jsonScan) scanOnce(i int) (any, int) {
	if i >= len(p.s) {
		jsonErr("Expecting value", p.s, i)
	}
	switch p.s[i] {
	case '"':
		return p.str(i + 1)
	case '{', '[':
		p.depth++
		if p.depth > JsonMaxDepth {
			if p.s[i] == '{' {
				jsonDeep("decoding a JSON object from a unicode string")
			}
			jsonDeep("decoding a JSON array from a unicode string")
		}
		defer func() { p.depth-- }()
		if p.s[i] == '{' {
			return p.object(i + 1)
		}
		return p.array(i + 1)
	case 'n':
		if p.lit(i, "null") {
			return nil, i + 4
		}
	case 't':
		if p.lit(i, "true") {
			return true, i + 4
		}
	case 'f':
		if p.lit(i, "false") {
			return false, i + 5
		}
	case 'N':
		if p.lit(i, "NaN") {
			return math.NaN(), i + 3
		}
	case 'I':
		if p.lit(i, "Infinity") {
			return math.Inf(1), i + 8
		}
	case '-':
		if p.lit(i, "-Infinity") {
			return math.Inf(-1), i + 9
		}
	}
	return p.number(i)
}

func isDigitRune(r rune) bool { return r >= '0' && r <= '9' }

// number is _match_number_unicode.
func (p *jsonScan) number(start int) (any, int) {
	end := len(p.s) - 1
	i := start
	if p.s[i] == '-' {
		i++
		if i > end {
			jsonErr("Expecting value", p.s, start)
		}
	}
	switch c := p.s[i]; {
	case c >= '1' && c <= '9':
		i++
		for i <= end && isDigitRune(p.s[i]) {
			i++
		}
	case c == '0':
		i++
	default:
		jsonErr("Expecting value", p.s, start)
	}
	isFloat := false
	if i < end && p.s[i] == '.' && isDigitRune(p.s[i+1]) {
		isFloat = true
		i += 2
		for i <= end && isDigitRune(p.s[i]) {
			i++
		}
	}
	if i < end && (p.s[i] == 'e' || p.s[i] == 'E') {
		eStart := i
		i++
		if i < end && (p.s[i] == '-' || p.s[i] == '+') {
			i++
		}
		for i <= end && isDigitRune(p.s[i]) {
			i++
		}
		if isDigitRune(p.s[i-1]) {
			isFloat = true
		} else {
			i = eStart
		}
	}
	text := string(p.s[start:i])
	if isFloat {
		f, _ := strconv.ParseFloat(text, 64) // correctly rounded; ±inf past the range
		return f, i
	}
	if digits := len(strings.TrimPrefix(text, "-")); digits > IntMaxStrDigits {
		Raise("ValueError", "Exceeds the limit (4300 digits) for integer string conversion: "+
			"value has "+strconv.Itoa(digits)+" digits; use sys.set_int_max_str_digits() to "+
			"increase the limit")
	}
	if n, err := strconv.ParseInt(text, 10, 64); err == nil {
		return n, i
	}
	n, _ := new(big.Int).SetString(text, 10)
	return n, i
}

func hexVal(r rune) (rune, bool) {
	switch {
	case r >= '0' && r <= '9':
		return r - '0', true
	case r >= 'a' && r <= 'f':
		return r - 'a' + 10, true
	case r >= 'A' && r <= 'F':
		return r - 'A' + 10, true
	}
	return 0, false
}

// str is scanstring_unicode from end (after the opening quote): the str and the index
// after the closing quote.
func (p *jsonScan) str(end int) (any, int) {
	begin := end - 1
	n := len(p.s)
	var out []rune
	surrogate := false
	for {
		next := end
		var c rune = -1
		for ; next < n; next++ {
			d := p.s[next]
			if d == '"' || d == '\\' {
				c = d
				break
			}
			if d <= 0x1f {
				jsonErr("Invalid control character at", p.s, next)
			}
		}
		if c != '"' && c != '\\' {
			jsonErr("Unterminated string starting at", p.s, begin)
		}
		out = append(out, p.s[end:next]...)
		next++
		if c == '"' {
			end = next
			break
		}
		if next == n {
			jsonErr("Unterminated string starting at", p.s, begin)
		}
		c = p.s[next]
		if c != 'u' {
			end = next + 1
			switch c {
			case '"', '\\', '/':
			case 'b':
				c = '\b'
			case 'f':
				c = '\f'
			case 'n':
				c = '\n'
			case 'r':
				c = '\r'
			case 't':
				c = '\t'
			default:
				jsonErr("Invalid \\escape", p.s, end-2)
			}
		} else {
			c = 0
			next++
			end = next + 4
			if end >= n {
				jsonErr("Invalid \\uXXXX escape", p.s, next-1)
			}
			for ; next < end; next++ {
				h, ok := hexVal(p.s[next])
				if !ok {
					jsonErr("Invalid \\uXXXX escape", p.s, end-5)
				}
				c = c<<4 | h
			}
			if c >= 0xD800 && c < 0xDC00 && end+6 < n && p.s[next] == '\\' && p.s[next+1] == 'u' {
				next += 2
				end += 6
				var c2 rune
				for ; next < end; next++ {
					h, ok := hexVal(p.s[next])
					if !ok {
						jsonErr("Invalid \\uXXXX escape", p.s, end-5)
					}
					c2 = c2<<4 | h
				}
				if c2 >= 0xDC00 && c2 < 0xE000 {
					c = utf16.DecodeRune(c, c2)
				} else {
					end -= 6
				}
			}
		}
		if c >= 0xD800 && c < 0xE000 {
			surrogate = true
		}
		out = append(out, c)
	}
	if surrogate {
		return wtf8(out), end
	}
	return string(out), end
}

func (p *jsonScan) object(i int) (any, int) {
	d := NewDict[string, any]()
	end := len(p.s) - 1
	i = p.ws(i)
	if i > end || p.s[i] != '}' {
		for {
			if i > end || p.s[i] != '"' {
				jsonErr("Expecting property name enclosed in double quotes", p.s, i)
			}
			k, next := p.str(i + 1)
			i = p.ws(next)
			if i > end || p.s[i] != ':' {
				jsonErr("Expecting ':' delimiter", p.s, i)
			}
			i = p.ws(i + 1)
			v, next := p.scanOnce(i)
			d.Set(k.(string), v)
			i = p.ws(next)
			if i <= end && p.s[i] == '}' {
				break
			}
			if i > end || p.s[i] != ',' {
				jsonErr("Expecting ',' delimiter", p.s, i)
			}
			comma := i
			i = p.ws(i + 1)
			if i <= end && p.s[i] == '}' {
				jsonErr("Illegal trailing comma before end of object", p.s, comma)
			}
		}
	}
	return d, i + 1
}

func (p *jsonScan) array(i int) (any, int) {
	l := NewList[any]()
	end := len(p.s) - 1
	i = p.ws(i)
	if i > end || p.s[i] != ']' {
		for {
			v, next := p.scanOnce(i)
			l.Append(v)
			i = p.ws(next)
			if i <= end && p.s[i] == ']' {
				break
			}
			if i > end || p.s[i] != ',' {
				jsonErr("Expecting ',' delimiter", p.s, i)
			}
			comma := i
			i = p.ws(i + 1)
			if i <= end && p.s[i] == ']' {
				jsonErr("Illegal trailing comma before end of array", p.s, comma)
			}
		}
	}
	return l, i + 1
}

// ---- the operations on a Json ----------------------------------------------------------

func pyTypeName(v any) string {
	switch x := jnorm(v).(type) {
	case nil:
		return "NoneType"
	case bool:
		return "bool"
	case int64, *big.Int:
		return "int"
	case float64:
		return "float"
	case string:
		return "str"
	case pyList:
		return "list"
	case PyTuple:
		return "tuple"
	case pyDict:
		_, _, kind, _ := x.PyDict()
		switch kind {
		case counterDict:
			return "T2Counter"
		case defaultDict:
			return "collections.defaultdict"
		}
		return "dict"
	}
	return "object"
}

// JsonIndex is j[k] (k a str or an Int): a dict's value (KeyError), a list's, tuple's
// or str's item (IndexError), TypeError otherwise -- as Python.
func JsonIndex(j any, k any) any {
	j = jnorm(j)
	switch x := j.(type) {
	case pyDict:
		return x.PyIndex(k)
	case pyList, PyTuple:
		var items []any
		name := "list"
		if l, ok := x.(pyList); ok {
			items = l.PyList()
		} else {
			items, name = x.(PyTuple).PyTuple(), "tuple"
		}
		n, ok := k.(int64)
		if !ok {
			Raise("TypeError", name+" indices must be integers or slices, not str")
		}
		return IndexSlice(items, n, name+" index out of range")
	case string:
		n, ok := k.(int64)
		if !ok {
			Raise("TypeError", "string indices must be integers, not 'str'")
		}
		return wtf8([]rune{IndexSlice(pyRunes(x), n, "string index out of range")})
	}
	Raise("TypeError", "'"+pyTypeName(j)+"' object is not subscriptable")
	return nil
}

// JsonIsNone is `j is None`.
func JsonIsNone(j any) bool { return jnorm(j) == nil }

// JsonTruth is bool(j).
func JsonTruth(j any) bool {
	switch x := jnorm(j).(type) {
	case nil:
		return false
	case bool:
		return x
	case int64:
		return x != 0
	case *big.Int:
		return x.Sign() != 0
	case float64:
		return x != 0
	case string:
		return x != ""
	case pyList:
		return len(x.PyList()) != 0
	case PyTuple:
		return len(x.PyTuple()) != 0
	case pyDict:
		keys, _, _, _ := x.PyDict()
		return len(keys) != 0
	}
	return true
}

// numeric is a Json number as (int, float, kind): kind 0 not a number, 1 int, 2 big
// int, 3 float. A bool is the int 0 or 1 (Python: True == 1).
func numeric(v any) (int64, *big.Int, float64, int) {
	switch x := v.(type) {
	case bool:
		return int64(B2I(x)), nil, 0, 1
	case int64:
		return x, nil, 0, 1
	case *big.Int:
		return 0, x, 0, 2
	case float64:
		return 0, nil, x, 3
	}
	return 0, nil, 0, 0
}

// JsonEq is a == b for Json values (and a Json against a plain value): Python's ==.
func JsonEq(a, b any) bool { return jsonEq(a, b, 0) }

func jsonEq(a, b any, depth int) bool {
	if depth > JsonMaxDepth {
		jsonDeep("in comparison")
	}
	a, b = jnorm(a), jnorm(b)
	if sameObject(a, b) {
		return true // a container compared with itself: its items are identical
	}
	ai, ab, af, ak := numeric(a)
	bi, bb, bf, bk := numeric(b)
	if ak != 0 || bk != 0 {
		if ak == 0 || bk == 0 {
			return false
		}
		return numEq(ai, ab, af, ak, bi, bb, bf, bk)
	}
	switch x := a.(type) {
	case nil:
		return b == nil
	case string:
		y, ok := b.(string)
		return ok && x == y
	case pyList:
		y, ok := b.(pyList)
		return ok && seqEq(x.PyList(), y.PyList(), depth)
	case PyTuple:
		y, ok := b.(PyTuple)
		return ok && seqEq(x.PyTuple(), y.PyTuple(), depth)
	case pyDict:
		y, ok := b.(pyDict)
		if !ok {
			return false
		}
		return dictAnyEq(x, y, depth)
	}
	return false
}

// sameObject reports whether a and b are the same list or dict (Python compares
// container items with `is` first: [nan] == itself).
func sameObject(a, b any) bool {
	switch a.(type) {
	case pyList, pyDict:
		switch b.(type) {
		case pyList, pyDict:
			return a == b // pointers: comparable
		}
	}
	return false
}

func seqEq(a, b []any, depth int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if !jsonEq(a[i], b[i], depth+1) {
			return false
		}
	}
	return true
}

func dictAnyEq(x, y pyDict, depth int) bool {
	xk, xv, xkind, _ := x.PyDict()
	yk, yv, ykind, _ := y.PyDict()
	find := func(keys, vals []any, k any) (any, bool) {
		for i, kk := range keys {
			if kk == k {
				return vals[i], true
			}
		}
		return nil, false
	}
	if xkind == counterDict && ykind == counterDict {
		zero := func(keys, vals []any, k any) any {
			if v, ok := find(keys, vals, k); ok {
				return v
			}
			return int64(0)
		}
		for _, k := range append(append([]any{}, xk...), yk...) {
			if !jsonEq(zero(xk, xv, k), zero(yk, yv, k), depth+1) {
				return false
			}
		}
		return true
	}
	if len(xk) != len(yk) {
		return false
	}
	for i, k := range xk {
		v, ok := find(yk, yv, k)
		if !ok || !jsonEq(xv[i], v, depth+1) {
			return false
		}
	}
	return true
}

func numEq(ai int64, ab *big.Int, af float64, ak int, bi int64, bb *big.Int, bf float64, bk int) bool {
	toBig := func(i int64, b *big.Int, k int) *big.Int {
		if k == 2 {
			return b
		}
		return big.NewInt(i)
	}
	switch {
	case ak != 3 && bk != 3:
		return toBig(ai, ab, ak).Cmp(toBig(bi, bb, bk)) == 0
	case ak == 3 && bk == 3:
		return af == bf
	case ak == 3:
		return floatEqBig(af, toBig(bi, bb, bk))
	}
	return floatEqBig(bf, toBig(ai, ab, ak))
}

func floatEqBig(f float64, n *big.Int) bool {
	if math.IsNaN(f) || math.IsInf(f, 0) || f != math.Trunc(f) {
		return false
	}
	v, _ := new(big.Float).SetFloat64(f).Int(nil)
	return v.Cmp(n) == 0
}

// JsonRepr is repr(j).
func JsonRepr(j any) string {
	var b strings.Builder
	jsonRepr(&b, j, 0)
	return b.String()
}

// JsonStr is str(j): a str is itself, anything else its repr.
func JsonStr(j any) string {
	switch x := jnorm(j).(type) {
	case string:
		return x
	}
	return JsonRepr(j)
}

func jsonRepr(b *strings.Builder, v any, depth int) {
	if depth > JsonMaxDepth {
		jsonDeep("getting the repr of an object")
	}
	v = jnorm(v)
	switch x := v.(type) {
	case nil:
		b.WriteString("None")
	case bool:
		b.WriteString(StrBool(x))
	case int64:
		b.WriteString(strconv.FormatInt(x, 10))
	case *big.Int:
		b.WriteString(x.String())
	case float64:
		b.WriteString(ReprFloat(x))
	case string:
		b.WriteString(ReprStr(x))
	case pyList:
		reprSeq(b, "[", x.PyList(), "]", depth)
	case PyTuple:
		items := x.PyTuple()
		if len(items) == 1 {
			reprSeq(b, "(", items, ",)", depth)
		} else {
			reprSeq(b, "(", items, ")", depth)
		}
	case pyDict:
		keys, vals, kind, fname := x.PyDict()
		switch kind {
		case counterDict:
			if len(keys) == 0 {
				b.WriteString("T2Counter()")
				return
			}
			order := make([]int, len(keys))
			for i := range order {
				order[i] = i
			}
			sort.SliceStable(order, func(i, j int) bool {
				return vals[order[i]].(int64) > vals[order[j]].(int64)
			})
			ks, vs := make([]any, len(keys)), make([]any, len(keys))
			for n, i := range order {
				ks[n], vs[n] = keys[i], vals[i]
			}
			b.WriteString("T2Counter(")
			reprDict(b, ks, vs, depth)
			b.WriteString(")")
		case defaultDict:
			b.WriteString("defaultdict(<class '" + fname + "'>, ")
			reprDict(b, keys, vals, depth)
			b.WriteString(")")
		default:
			reprDict(b, keys, vals, depth)
		}
	default:
		b.WriteString(Repr(v))
	}
}

func reprSeq(b *strings.Builder, open string, items []any, close string, depth int) {
	b.WriteString(open)
	for i, x := range items {
		if i > 0 {
			b.WriteString(", ")
		}
		jsonRepr(b, x, depth+1)
	}
	b.WriteString(close)
}

func reprDict(b *strings.Builder, keys, vals []any, depth int) {
	b.WriteByte('{')
	for i := range keys {
		if i > 0 {
			b.WriteString(", ")
		}
		jsonRepr(b, keys[i], depth+1)
		b.WriteString(": ")
		jsonRepr(b, vals[i], depth+1)
	}
	b.WriteByte('}')
}

// FmtJson is format(j, spec) with its size check: by the value's Python type.
func FmtJson(f *Fuel, j any, spec string) string {
	if spec == "" {
		SpecGrow(f, spec)
		return JsonStr(j)
	}
	switch x := jnorm(j).(type) {
	case nil:
		return FmtNone(f, spec)
	case bool:
		return FmtBool(f, x, spec)
	case int64:
		return FmtInt(f, x, spec)
	case float64:
		return FmtFloat(f, x, spec)
	case string:
		return FmtStr(f, x, spec)
	}
	return FmtObj(f, spec, pyTypeName(j))
}

// JsonTagged is a Json value as the tagged tree the test runners compare with Python
// (tests/semantics_v11/gorun.py: {"t": "int", "v": "12"}, {"t": "list", "v": [...]}, ...).
func JsonTagged(v any) any {
	tag := func(t string, x any) any { return map[string]any{"t": t, "v": x} }
	seq := func(t string, items []any) any {
		out := make([]any, len(items))
		for i, x := range items {
			out[i] = JsonTagged(x)
		}
		return tag(t, out)
	}
	switch x := jnorm(v).(type) {
	case nil:
		return map[string]any{"t": "none"}
	case bool:
		return tag("bool", x)
	case int64:
		return tag("int", strconv.FormatInt(x, 10))
	case *big.Int:
		return tag("int", x.String())
	case float64:
		return tag("float", ReprFloat(x))
	case string:
		return tag("str", x)
	case pyList:
		return seq("list", x.PyList())
	case PyTuple:
		return seq("tuple", x.PyTuple())
	case pyDict:
		keys, vals, _, _ := x.PyDict()
		out := make([]any, len(keys))
		for i := range keys {
			out[i] = []any{JsonTagged(keys[i]), JsonTagged(vals[i])}
		}
		return tag("dict", out)
	}
	panic("rt.JsonTagged: not a Json value")
}
