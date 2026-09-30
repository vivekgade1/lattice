package rt

import (
	"strings"
)

// ---- T2 values at the endpoint boundary (lattice/t2/support.py t2_decode, t2_arg,
// t2_result, t2_post, t2_call) ------------------------------------------------------------
//
// A stored cell (int64, float64, string, []byte, nil) or an endpoint parameter value
// (int64, float64, string, bool, Date, DateTime, UUID, nil, Unset, Null) becomes the Go
// value a procedure receives: the T2<Scalar> decoders are t2_decode(value, scalar). A
// value that is None reaches a procedure only through an optional parameter (T2Opt);
// the checker never passes one to a non-optional parameter.

// isNone is a value a procedure sees as None: null, and an optional parameter that was
// not sent (T2Request.param).
func isNone(v any) bool {
	switch v.(type) {
	case nil, unsetT, nullT:
		return true
	}
	return false
}

func t2None(scalar string) {
	Raise("TypeError", "a procedure's "+scalar+" argument is None")
}

// T2Opt is an optional argument: None for a null value, else dec(v).
func T2Opt[T any](v any, dec func(any) T) Opt[T] {
	if isNone(v) {
		return None[T]()
	}
	return Some(dec(v))
}

// T2Int is t2_decode(v, "Int"): int(v).
func T2Int(v any) int64 {
	switch x := v.(type) {
	case int64:
		return x
	case int:
		return int64(x)
	case bool:
		return int64(B2I(x))
	case float64:
		return IntOfFloat(x)
	case string:
		return ParseInt(x)
	case UUID:
		return ParseInt(string(x))
	}
	if isNone(v) {
		t2None("Int")
	}
	Raise("TypeError", "int() argument must be a string, a bytes-like object or a real number")
	return 0
}

// T2Float is t2_decode(v, "Float"): float(v) (an Int widens exactly as float() does).
func T2Float(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case int64:
		return float64(x)
	case int:
		return float64(x)
	case bool:
		if x {
			return 1
		}
		return 0
	case string:
		return ParseFloat(x)
	}
	if isNone(v) {
		t2None("Float")
	}
	Raise("TypeError", "float() argument must be a string or a real number")
	return 0
}

// T2Bool is t2_decode(v, "Bool"): bool(v).
func T2Bool(v any) bool {
	if isNone(v) {
		t2None("Bool")
	}
	return pyTruth(v)
}

// T2Str is t2_decode(v, "Str"): the value itself (a stored non-text value as str()).
func T2Str(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case UUID:
		return string(x)
	}
	if isNone(v) {
		t2None("Str")
	}
	return pyStr(v)
}

// isoText is str(v) of a stored value (date.fromisoformat(str(value))).
func isoText(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return pyStr(v)
}

// T2Date is t2_decode(v, "Date"): date.fromisoformat(str(v)).
func T2Date(v any) Date {
	switch x := v.(type) {
	case Date:
		return x
	case DateTime:
		return x.Date()
	}
	if isNone(v) {
		t2None("Date")
	}
	s := isoText(v)
	d, ok := ParseISODate(s)
	if !ok {
		Raise("ValueError", "Invalid isoformat string: "+ReprStr(s))
	}
	return d
}

// T2DateTime is t2_decode(v, "DateTime"): datetime.fromisoformat(str(v)), a naive
// value being UTC, as UTC.
func T2DateTime(v any) DateTime {
	if x, ok := v.(DateTime); ok {
		return x
	}
	if isNone(v) {
		t2None("DateTime")
	}
	s := isoText(v)
	t, ok := ParseDateTime(s)
	if !ok {
		Raise("ValueError", "Invalid isoformat string: "+ReprStr(s))
	}
	return t
}

// T2Uuid is t2_decode(v, "Uuid"): uuid.UUID(str(v)), as its canonical text.
func T2Uuid(v any) string {
	if x, ok := v.(UUID); ok {
		return string(x)
	}
	if isNone(v) {
		t2None("Uuid")
	}
	// uuid.UUID(hex): "urn:" and "uuid:" removed, then braces stripped and hyphens
	// removed; 32 hex digits remain
	s := strings.ReplaceAll(strings.ReplaceAll(isoText(v), "urn:", ""), "uuid:", "")
	s = strings.ReplaceAll(strings.Trim(s, "{}"), "-", "")
	u, ok := UUIDText(s)
	if !ok || len(s) != 32 {
		Raise("ValueError", "badly formed hexadecimal UUID string")
	}
	return u
}

// ---- values a procedure returns -------------------------------------------------------------

type pyOpter interface{ PyOpt() any }

// plain is a procedure's value as a plain Go value: an optional value's item or nil.
func plain(v any) any {
	if o, ok := v.(pyOpter); ok {
		return o.PyOpt()
	}
	return v
}

// T2Value is t2_result(value, scalar): a procedure's value of a Lattice scalar,
// normalized (an Int where Float is declared widens), as an endpoint parameter value
// (a Uuid as UUID; None as nil). A call(...) bound as a scalar holds it.
func T2Value(v any, scalar string) any {
	v = plain(v)
	switch x := v.(type) {
	case int64:
		if scalar == "Float" {
			return float64(x)
		}
	case string:
		if scalar == "Uuid" {
			if u, ok := UUIDText(x); ok {
				return UUID(u)
			}
			return UUID(x)
		}
	}
	return v
}

// T2Store is encode(t2_result(value, scalar)) with encode = sql_value: a procedure's
// value as a stored cell (a derived column, a call's rows).
func T2Store(v any, scalar string) any { return SQLValue(T2Value(v, scalar)) }

// T2Holds is the truth of a procedure's value where a require()/where() tests it (None
// fails).
func T2Holds(v any) bool {
	v = plain(v)
	if b, ok := v.(bool); ok {
		return b
	}
	return pyTruth(v)
}

// T2MinRowBound is lattice.t2.support.T2_MIN_ROW_BOUND.
const T2MinRowBound = 100

// T2RowBound is t2_call's R621: a procedure returned more rows than its input bounds.
func T2RowBound(proc string, n, bound int) {
	limit := max(bound, T2MinRowBound)
	if n > limit {
		Fail("R621", "procedure '"+proc+"' returned "+itoa(n)+" rows; its input bounds the result to "+itoa(limit))
	}
}

// ---- a row's cells by column (t2_post's `cells` dict) ------------------------------------------

// Cells is one row's cells by column name, in insertion order (a Python dict).
type Cells struct {
	keys []string
	m    map[string]any
}

// NewCells is dict(zip(columns, row)).
func NewCells(cols []string, row []any) *Cells {
	c := &Cells{m: make(map[string]any, len(cols))}
	for i, k := range cols {
		c.Set(k, row[i])
	}
	return c
}

// Get is cells[k] (KeyError when missing).
func (c *Cells) Get(k string) any {
	v, ok := c.m[k]
	if !ok {
		Raise("KeyError", ReprStr(k))
	}
	return v
}

// Set is cells[k] = v.
func (c *Cells) Set(k string, v any) {
	if _, ok := c.m[k]; !ok {
		c.keys = append(c.keys, k)
	}
	c.m[k] = v
}

// Select is {k: cells[k] for k in cols}.
func (c *Cells) Select(cols []string) *Cells {
	out := &Cells{m: make(map[string]any, len(cols))}
	for _, k := range cols {
		out.Set(k, c.Get(k))
	}
	return out
}

// Rename is {mapping.get(k, k): v for k, v in cells.items()} (pairs: old, new).
func (c *Cells) Rename(pairs [][2]string) *Cells {
	mapping := make(map[string]string, len(pairs))
	for _, p := range pairs {
		mapping[p[0]] = p[1]
	}
	out := &Cells{m: make(map[string]any, len(c.keys))}
	for _, k := range c.keys {
		nk, ok := mapping[k]
		if !ok {
			nk = k
		}
		out.Set(nk, c.m[k])
	}
	return out
}

// Row is tuple(cells[k] for k in cols).
func (c *Cells) Row(cols []string) []any {
	out := make([]any, len(cols))
	for i, k := range cols {
		out[i] = c.Get(k)
	}
	return out
}
