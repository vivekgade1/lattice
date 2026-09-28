package rt

import (
	"errors"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

// ---- JSON values ------------------------------------------------------------------------
//
// A response body is a JSON value built from: nil (null), bool, int, int64, float64,
// string, UUID, Date, DateTime, Obj (an object with its keys in order), []any, []Obj,
// []string. Obj keeps insertion order, as a Python dict does.

// KV is one member of an Obj.
type KV struct {
	K string
	V any
}

// Obj is a JSON object whose members keep their order.
type Obj []KV

// Get is the value of key k (the last one, as Python's dict keeps), and whether it is
// present.
func (o Obj) Get(k string) (any, bool) {
	for i := len(o) - 1; i >= 0; i-- {
		if o[i].K == k {
			return o[i].V, true
		}
	}
	return nil, false
}

// ErrNotJSONCompliant is Starlette's JSONResponse refusing a non-finite float
// (json.dumps(allow_nan=False)).
var ErrNotJSONCompliant = errors.New("Out of range float values are not JSON compliant")

// WriteJSON renders v as Starlette's JSONResponse does: json.dumps(v,
// ensure_ascii=False, allow_nan=False, separators=(",", ":")).
func WriteJSON(v any) ([]byte, error) {
	var b strings.Builder
	if err := writeJSON(&b, v, false, ",", ":"); err != nil {
		return nil, err
	}
	return []byte(b.String()), nil
}

// DumpsJSON is Python's json.dumps(v) with its defaults (ensure_ascii=True, NaN and
// Infinity spelled as Python spells them, separators ", " and ": ").
func DumpsJSON(v any) string {
	var b strings.Builder
	if err := writeJSON(&b, v, true, ", ", ": "); err != nil {
		panic(err)
	}
	return b.String()
}

func writeJSON(b *strings.Builder, v any, ascii bool, itemSep, keySep string) error {
	switch x := v.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		if x {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case int:
		b.WriteString(strconv.Itoa(x))
	case int64:
		b.WriteString(strconv.FormatInt(x, 10))
	case *big.Int:
		b.WriteString(x.String())
	case float64:
		switch {
		case math.IsNaN(x) || math.IsInf(x, 0):
			if !ascii {
				return fmt.Errorf("%w: %s", ErrNotJSONCompliant, pyFloatName(x))
			}
			switch {
			case math.IsNaN(x):
				b.WriteString("NaN")
			case x > 0:
				b.WriteString("Infinity")
			default:
				b.WriteString("-Infinity")
			}
		default:
			b.WriteString(ReprFloat(x))
		}
	case string:
		writeJSONString(b, x, ascii)
	case UUID:
		writeJSONString(b, string(x), ascii)
	case DateTime:
		writeJSONString(b, x.Text(), ascii)
	case Date:
		writeJSONString(b, x.ISO(), ascii)
	case Obj:
		b.WriteByte('{')
		for i, kv := range x {
			if i > 0 {
				b.WriteString(itemSep)
			}
			writeJSONString(b, kv.K, ascii)
			b.WriteString(keySep)
			if err := writeJSON(b, kv.V, ascii, itemSep, keySep); err != nil {
				return err
			}
		}
		b.WriteByte('}')
	case []any:
		b.WriteByte('[')
		for i, e := range x {
			if i > 0 {
				b.WriteString(itemSep)
			}
			if err := writeJSON(b, e, ascii, itemSep, keySep); err != nil {
				return err
			}
		}
		b.WriteByte(']')
	case []Obj:
		items := make([]any, len(x))
		for i := range x {
			items[i] = x[i]
		}
		return writeJSON(b, items, ascii, itemSep, keySep)
	case []string:
		items := make([]any, len(x))
		for i := range x {
			items[i] = x[i]
		}
		return writeJSON(b, items, ascii, itemSep, keySep)
	default:
		return fmt.Errorf("Object of type %T is not JSON serializable", v)
	}
	return nil
}

func pyFloatName(x float64) string {
	switch {
	case math.IsNaN(x):
		return "nan"
	case x > 0:
		return "inf"
	}
	return "-inf"
}

const hexDigits = "0123456789abcdef"

func writeJSONString(b *strings.Builder, s string, ascii bool) {
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			switch {
			case r < 0x20 || (ascii && r > 0x7e && r < 0x10000):
				writeU(b, r)
			case ascii && r >= 0x10000:
				hi, lo := utf16.EncodeRune(r)
				writeU(b, hi)
				writeU(b, lo)
			default:
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
}

func writeU(b *strings.Builder, r rune) {
	b.WriteString(`\u`)
	for shift := 12; shift >= 0; shift -= 4 {
		b.WriteByte(hexDigits[(r>>shift)&0xF])
	}
}

// ---- json.loads over a request body -------------------------------------------------------

// JSONStr is a decoded JSON string. Bad: it holds a lone surrogate, which Python keeps
// in a str but cannot encode as UTF-8 (S has U+FFFD in its place).
type JSONStr struct {
	S   string
	Bad bool
}

// JSONObj is a decoded JSON object (a repeated key keeps its last value).
type JSONObj struct {
	Keys []string
	Vals map[string]any
}

// errJSON is any failure of json.loads (a ValueError in Python).
var errJSON = errors.New("invalid JSON")

// LoadsJSON is json.loads(body) for request bytes: the encoding detected as Python's
// json.detect_encoding does (UTF-8, UTF-8 with a BOM, UTF-16/32 with or without one),
// decoded with "surrogatepass". Values: nil, bool, int64 or *big.Int (an integer beyond
// int64), float64, JSONStr, []any, *JSONObj.
func LoadsJSON(body []byte) (any, error) {
	text, err := decodeJSONBytes(body)
	if err != nil {
		return nil, err
	}
	p := &jsonParser{s: text}
	p.ws()
	v, err := p.value()
	if err != nil {
		return nil, err
	}
	p.ws()
	if p.i != len(p.s) {
		return nil, errJSON // "Extra data"
	}
	return v, nil
}

func decodeJSONBytes(b []byte) ([]rune, error) {
	has := func(prefix ...byte) bool {
		if len(b) < len(prefix) {
			return false
		}
		for i, c := range prefix {
			if b[i] != c {
				return false
			}
		}
		return true
	}
	switch {
	case has(0, 0, 0xFE, 0xFF):
		return decodeUTF32(b[4:], true)
	case has(0xFF, 0xFE, 0, 0):
		return decodeUTF32(b[4:], false)
	case has(0xFE, 0xFF):
		return decodeUTF16(b[2:], true)
	case has(0xFF, 0xFE):
		return decodeUTF16(b[2:], false)
	case has(0xEF, 0xBB, 0xBF):
		return decodeUTF8Pass(b[3:])
	}
	if len(b) >= 4 {
		if b[0] == 0 {
			if b[1] != 0 {
				return decodeUTF16(b, true)
			}
			return decodeUTF32(b, true)
		}
		if b[1] == 0 {
			if b[2] != 0 || b[3] != 0 {
				return decodeUTF16(b, false)
			}
			return decodeUTF32(b, false)
		}
	} else if len(b) == 2 {
		if b[0] == 0 {
			return decodeUTF16(b, true)
		}
		if b[1] == 0 {
			return decodeUTF16(b, false)
		}
	}
	return decodeUTF8Pass(b)
}

// decodeUTF8Pass decodes UTF-8 where an encoded surrogate (ED A0..BF xx) is a lone
// surrogate code point ("surrogatepass"); anything else invalid is an error.
func decodeUTF8Pass(b []byte) ([]rune, error) {
	out := make([]rune, 0, len(b))
	for i := 0; i < len(b); {
		r, size := utf8.DecodeRune(b[i:])
		if r == utf8.RuneError && size <= 1 {
			if i+2 < len(b) && b[i] == 0xED && b[i+1] >= 0xA0 && b[i+1] <= 0xBF &&
				b[i+2] >= 0x80 && b[i+2] <= 0xBF {
				out = append(out, rune(0xD000|rune(b[i+1]&0x3F)<<6|rune(b[i+2]&0x3F)))
				i += 3
				continue
			}
			return nil, errJSON
		}
		out = append(out, r)
		i += size
	}
	return out, nil
}

func decodeUTF16(b []byte, be bool) ([]rune, error) {
	if len(b)%2 != 0 {
		return nil, errJSON
	}
	units := make([]uint16, len(b)/2)
	for i := range units {
		if be {
			units[i] = uint16(b[2*i])<<8 | uint16(b[2*i+1])
		} else {
			units[i] = uint16(b[2*i+1])<<8 | uint16(b[2*i])
		}
	}
	out := make([]rune, 0, len(units))
	for i := 0; i < len(units); i++ {
		u := rune(units[i])
		if u >= 0xD800 && u < 0xDC00 && i+1 < len(units) {
			if lo := rune(units[i+1]); lo >= 0xDC00 && lo < 0xE000 {
				out = append(out, utf16.DecodeRune(u, lo))
				i++
				continue
			}
		}
		out = append(out, u)
	}
	return out, nil
}

func decodeUTF32(b []byte, be bool) ([]rune, error) {
	if len(b)%4 != 0 {
		return nil, errJSON
	}
	out := make([]rune, 0, len(b)/4)
	for i := 0; i < len(b); i += 4 {
		var u uint32
		if be {
			u = uint32(b[i])<<24 | uint32(b[i+1])<<16 | uint32(b[i+2])<<8 | uint32(b[i+3])
		} else {
			u = uint32(b[i+3])<<24 | uint32(b[i+2])<<16 | uint32(b[i+1])<<8 | uint32(b[i])
		}
		if u > 0x10FFFF {
			return nil, errJSON
		}
		out = append(out, rune(u))
	}
	return out, nil
}

type jsonParser struct {
	s []rune
	i int
}

func (p *jsonParser) ws() {
	for p.i < len(p.s) {
		switch p.s[p.i] {
		case ' ', '\t', '\n', '\r':
			p.i++
		default:
			return
		}
	}
}

func (p *jsonParser) lit(word string) bool {
	rs := []rune(word)
	if p.i+len(rs) > len(p.s) {
		return false
	}
	for k, r := range rs {
		if p.s[p.i+k] != r {
			return false
		}
	}
	p.i += len(rs)
	return true
}

func (p *jsonParser) value() (any, error) {
	if p.i >= len(p.s) {
		return nil, errJSON
	}
	switch c := p.s[p.i]; {
	case c == '{':
		return p.object()
	case c == '[':
		return p.array()
	case c == '"':
		p.i++
		return p.str()
	case c == 'n' && p.lit("null"):
		return nil, nil
	case c == 't' && p.lit("true"):
		return true, nil
	case c == 'f' && p.lit("false"):
		return false, nil
	case c == 'N' && p.lit("NaN"):
		return math.NaN(), nil
	case c == 'I' && p.lit("Infinity"):
		return math.Inf(1), nil
	case c == '-' && p.i+1 < len(p.s) && p.s[p.i+1] == 'I':
		p.i++
		if p.lit("Infinity") {
			return math.Inf(-1), nil
		}
		return nil, errJSON
	case c == '-' || (c >= '0' && c <= '9'):
		return p.number()
	}
	return nil, errJSON
}

func (p *jsonParser) digitsRun() int {
	start := p.i
	for p.i < len(p.s) && p.s[p.i] >= '0' && p.s[p.i] <= '9' {
		p.i++
	}
	return p.i - start
}

func (p *jsonParser) number() (any, error) {
	start := p.i
	if p.s[p.i] == '-' {
		p.i++
	}
	if p.i >= len(p.s) {
		return nil, errJSON
	}
	if p.s[p.i] == '0' {
		p.i++
	} else if p.digitsRun() == 0 {
		return nil, errJSON
	}
	isFloat := false
	if p.i+1 < len(p.s) && p.s[p.i] == '.' && p.s[p.i+1] >= '0' && p.s[p.i+1] <= '9' {
		p.i++
		p.digitsRun()
		isFloat = true
	}
	if p.i < len(p.s) && (p.s[p.i] == 'e' || p.s[p.i] == 'E') {
		save := p.i
		p.i++
		if p.i < len(p.s) && (p.s[p.i] == '+' || p.s[p.i] == '-') {
			p.i++
		}
		if p.digitsRun() == 0 {
			p.i = save
		} else {
			isFloat = true
		}
	}
	text := string(p.s[start:p.i])
	if isFloat {
		f, err := strconv.ParseFloat(text, 64)
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			return nil, errJSON
		}
		return f, nil
	}
	digits := len(strings.TrimLeft(text, "-"))
	if digits > 4300 { // CPython's int-from-string digit limit
		return nil, errJSON
	}
	if n, err := strconv.ParseInt(text, 10, 64); err == nil {
		return n, nil
	}
	n, _ := new(big.Int).SetString(text, 10)
	return n, nil
}

func (p *jsonParser) str() (any, error) {
	var out []rune
	bad := false
	for {
		if p.i >= len(p.s) {
			return nil, errJSON
		}
		c := p.s[p.i]
		p.i++
		switch {
		case c == '"':
			if bad {
				for k, r := range out {
					if r >= 0xD800 && r < 0xE000 {
						out[k] = utf8.RuneError
					}
				}
			}
			return JSONStr{S: string(out), Bad: bad}, nil
		case c < 0x20:
			return nil, errJSON // strict: an unescaped control character
		case c == '\\':
			if p.i >= len(p.s) {
				return nil, errJSON
			}
			e := p.s[p.i]
			p.i++
			switch e {
			case '"', '\\', '/':
				out = append(out, e)
			case 'b':
				out = append(out, '\b')
			case 'f':
				out = append(out, '\f')
			case 'n':
				out = append(out, '\n')
			case 'r':
				out = append(out, '\r')
			case 't':
				out = append(out, '\t')
			case 'u':
				u, ok := p.hex4()
				if !ok {
					return nil, errJSON
				}
				if u >= 0xD800 && u < 0xDC00 && p.i+1 < len(p.s) && p.s[p.i] == '\\' && p.s[p.i+1] == 'u' {
					save := p.i
					p.i += 2
					lo, ok := p.hex4()
					if ok && lo >= 0xDC00 && lo < 0xE000 {
						out = append(out, utf16.DecodeRune(u, lo))
						continue
					}
					p.i = save
				}
				if u >= 0xD800 && u < 0xE000 {
					bad = true
				}
				out = append(out, u)
			default:
				return nil, errJSON
			}
		default:
			if c >= 0xD800 && c < 0xE000 {
				bad = true
			}
			out = append(out, c)
		}
	}
}

func (p *jsonParser) hex4() (rune, bool) {
	if p.i+4 > len(p.s) {
		return 0, false
	}
	var u rune
	for k := 0; k < 4; k++ {
		c := p.s[p.i+k]
		var d rune
		switch {
		case c >= '0' && c <= '9':
			d = c - '0'
		case c >= 'a' && c <= 'f':
			d = c - 'a' + 10
		case c >= 'A' && c <= 'F':
			d = c - 'A' + 10
		default:
			return 0, false
		}
		u = u<<4 | d
	}
	p.i += 4
	return u, true
}

func (p *jsonParser) array() (any, error) {
	p.i++ // [
	out := []any{}
	p.ws()
	if p.i < len(p.s) && p.s[p.i] == ']' {
		p.i++
		return out, nil
	}
	for {
		p.ws()
		v, err := p.value()
		if err != nil {
			return nil, err
		}
		out = append(out, v)
		p.ws()
		if p.i >= len(p.s) {
			return nil, errJSON
		}
		switch p.s[p.i] {
		case ',':
			p.i++
		case ']':
			p.i++
			return out, nil
		default:
			return nil, errJSON
		}
	}
}

func (p *jsonParser) object() (any, error) {
	p.i++ // {
	obj := &JSONObj{Vals: map[string]any{}}
	p.ws()
	if p.i < len(p.s) && p.s[p.i] == '}' {
		p.i++
		return obj, nil
	}
	for {
		p.ws()
		if p.i >= len(p.s) || p.s[p.i] != '"' {
			return nil, errJSON
		}
		p.i++
		k, err := p.str()
		if err != nil {
			return nil, err
		}
		key := k.(JSONStr).S
		p.ws()
		if p.i >= len(p.s) || p.s[p.i] != ':' {
			return nil, errJSON
		}
		p.i++
		p.ws()
		v, err := p.value()
		if err != nil {
			return nil, err
		}
		if _, seen := obj.Vals[key]; !seen {
			obj.Keys = append(obj.Keys, key)
		}
		obj.Vals[key] = v
		p.ws()
		if p.i >= len(p.s) {
			return nil, errJSON
		}
		switch p.s[p.i] {
		case ',':
			p.i++
		case '}':
			p.i++
			return obj, nil
		default:
			return nil, errJSON
		}
	}
}

// ---- query strings and forms (urllib.parse.parse_qsl, as Starlette calls it) -------------

// ParseQS is dict(parse_qsl(raw.decode("latin-1"), keep_blank_values=True)): Starlette's
// query parameters (and the prelude's form fields); a repeated name keeps its last value.
func ParseQS(raw string) map[string]string {
	out := map[string]string{}
	latin := latin1(raw)
	for _, field := range strings.Split(latin, "&") {
		if field == "" {
			continue
		}
		name, value, _ := strings.Cut(field, "=")
		out[Unquote(strings.ReplaceAll(name, "+", " "))] = Unquote(strings.ReplaceAll(value, "+", " "))
	}
	return out
}

// latin1 is bytes.decode("latin-1"): each byte is the code point of the same value.
func latin1(raw string) string {
	var b strings.Builder
	for i := 0; i < len(raw); i++ {
		b.WriteRune(rune(raw[i]))
	}
	return b.String()
}

// Unquote is urllib.parse.unquote(s) (UTF-8, errors="replace"): each run of ASCII
// characters has its %XX escapes decoded to bytes, then decoded as UTF-8 with
// U+FFFD for each maximal invalid subpart; other characters pass through.
func Unquote(s string) string {
	if !strings.Contains(s, "%") {
		return s
	}
	var out strings.Builder
	rs := []rune(s)
	for i := 0; i < len(rs); {
		j := i
		if rs[i] < 0x80 {
			for j < len(rs) && rs[j] < 0x80 {
				j++
			}
			out.WriteString(DecodeUTF8Replace(unquoteToBytes(string(rs[i:j]))))
		} else {
			for j < len(rs) && rs[j] >= 0x80 {
				j++
			}
			out.WriteString(string(rs[i:j]))
		}
		i = j
	}
	return out.String()
}

func unquoteToBytes(s string) []byte {
	parts := strings.Split(s, "%")
	out := []byte(parts[0])
	for _, item := range parts[1:] {
		if len(item) >= 2 && isHex(item[0]) && isHex(item[1]) {
			v, _ := strconv.ParseUint(item[:2], 16, 8)
			out = append(out, byte(v))
			out = append(out, item[2:]...)
		} else {
			out = append(out, '%')
			out = append(out, item...)
		}
	}
	return out
}

// DecodeUTF8Replace is bytes.decode("utf-8", "replace"): one U+FFFD per maximal
// subpart of an ill-formed sequence.
func DecodeUTF8Replace(b []byte) string {
	var out strings.Builder
	for i := 0; i < len(b); {
		c := b[i]
		if c < 0x80 {
			out.WriteByte(c)
			i++
			continue
		}
		need, lo, hi := 0, byte(0x80), byte(0xBF)
		switch {
		case c >= 0xC2 && c <= 0xDF:
			need = 1
		case c == 0xE0:
			need, lo = 2, 0xA0
		case c >= 0xE1 && c <= 0xEC, c == 0xEE, c == 0xEF:
			need = 2
		case c == 0xED:
			need, hi = 2, 0x9F
		case c == 0xF0:
			need, lo = 3, 0x90
		case c >= 0xF1 && c <= 0xF3:
			need = 3
		case c == 0xF4:
			need, hi = 3, 0x8F
		default:
			out.WriteRune(utf8.RuneError)
			i++
			continue
		}
		k := 1
		for ; k <= need; k++ {
			if i+k >= len(b) {
				break
			}
			d := b[i+k]
			l, h := byte(0x80), byte(0xBF)
			if k == 1 {
				l, h = lo, hi
			}
			if d < l || d > h {
				break
			}
		}
		if k == need+1 {
			out.Write(b[i : i+k])
		} else {
			out.WriteRune(utf8.RuneError)
		}
		i += k
	}
	return out.String()
}
