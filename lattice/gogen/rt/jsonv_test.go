package rt

import (
	"encoding/json"
	"math/big"
	"os"
	"strconv"
	"strings"
	"testing"
)

type jsonAnswer struct {
	Op   string `json:"op"`
	Args []any  `json:"args"`
	Out  any    `json:"out"`
}

// testTuple is a tuple inside a Json value (generated code declares its own).
type testTuple []any

func (t testTuple) PyTuple() []any { return t }

func runesOf(v any) []rune {
	var rs []rune
	for _, c := range v.([]any) {
		rs = append(rs, rune(c.(float64)))
	}
	return rs
}

// untag builds the Go Json value of a tagged tree.
func untag(v any) any {
	t := v.([]any)
	switch t[0] {
	case "n":
		return nil
	case "b":
		return t[1].(bool)
	case "i":
		if n, err := strconv.ParseInt(t[1].(string), 10, 64); err == nil {
			return n
		}
		n, _ := new(big.Int).SetString(t[1].(string), 10)
		return n
	case "f":
		f, err := strconv.ParseFloat(t[1].(string), 64)
		if err != nil {
			panic(err)
		}
		return f
	case "s":
		return wtf8(runesOf(t[1]))
	case "l":
		l := NewList[any]()
		for _, x := range t[1].([]any) {
			l.Append(untag(x))
		}
		return l
	case "t":
		var items testTuple
		for _, x := range t[1].([]any) {
			items = append(items, untag(x))
		}
		return items
	case "d":
		d := NewDict[string, any]()
		for _, kv := range t[1].([]any) {
			p := kv.([]any)
			d.Set(untag(p[0]).(string), untag(p[1]))
		}
		return d
	}
	panic("untag")
}

// retag is a Go Json value as a tagged tree (to compare json.loads' results).
func retag(v any) any {
	switch x := jnorm(v).(type) {
	case nil:
		return []any{"n"}
	case bool:
		return []any{"b", x}
	case int64:
		return []any{"i", strconv.FormatInt(x, 10)}
	case *big.Int:
		return []any{"i", x.String()}
	case float64:
		return []any{"f", strconv.FormatFloat(x, 'x', -1, 64)}
	case string:
		return []any{"s", codepoints(pyRunes(x))}
	case pyList:
		out := []any{}
		for _, y := range x.PyList() {
			out = append(out, retag(y))
		}
		return []any{"l", out}
	case PyTuple:
		out := []any{}
		for _, y := range x.PyTuple() {
			out = append(out, retag(y))
		}
		return []any{"t", out}
	case pyDict:
		keys, vals, _, _ := x.PyDict()
		out := []any{}
		for i := range keys {
			out = append(out, []any{retag(keys[i]), retag(vals[i])})
		}
		return []any{"d", out}
	}
	panic("retag")
}

func codepoints(rs []rune) []any {
	out := []any{}
	for _, r := range rs {
		out = append(out, float64(r))
	}
	return out
}

// sameTagged compares tagged trees, floats by value bits (Python's float.hex and Go's
// 'x' format spell them differently).
func sameTagged(a, b any) bool {
	x, y := a.([]any), b.([]any)
	if len(x) != len(y) || x[0] != y[0] {
		return false
	}
	if len(x) == 1 {
		return true // None
	}
	if x[0] == "d" {
		xs, ys := x[1].([]any), y[1].([]any)
		if len(xs) != len(ys) {
			return false
		}
		for i := range xs {
			p, q := xs[i].([]any), ys[i].([]any)
			if !sameTagged(p[0], q[0]) || !sameTagged(p[1], q[1]) {
				return false
			}
		}
		return true
	}
	switch x[0] {
	case "f":
		f1, _ := strconv.ParseFloat(x[1].(string), 64)
		f2, _ := strconv.ParseFloat(y[1].(string), 64)
		return f1 == f2 || (f1 != f1 && f2 != f2)
	case "l", "t", "d", "s":
		xs, ys := x[1].([]any), y[1].([]any)
		if len(xs) != len(ys) {
			return false
		}
		for i := range xs {
			switch xi := xs[i].(type) {
			case []any:
				if !sameTagged(xi, ys[i]) {
					return false
				}
			default:
				if xs[i] != ys[i] {
					return false
				}
			}
		}
		return true
	}
	return x[1] == y[1]
}

// TestJsonAnswers: json.dumps / json.loads and the Json operations answer as Python
// 3.14 does (testdata/gen_json_answers.py).
func TestJsonAnswers(t *testing.T) {
	data, err := os.ReadFile("testdata/json_answers.json")
	if err != nil {
		t.Fatal(err)
	}
	var answers []jsonAnswer
	if err := json.Unmarshal(data, &answers); err != nil {
		t.Fatal(err)
	}
	if len(answers) < 15000 {
		t.Fatal("testdata/json_answers.json is incomplete")
	}
	bad := 0
	for _, a := range answers {
		var got any
		want := a.Out
		if s, ok := want.(string); ok && strings.HasPrefix(s, "!JSONDecodeError:") {
			want = "!ValueError:" + strings.TrimPrefix(s, "!JSONDecodeError:") // a ValueError in T2
		}
		run := func(f func() string) any { return mathOutcome(f) }
		switch a.Op {
		case "dumps":
			var indent *int64
			if a.Args[2] != nil {
				n := int64(a.Args[2].(float64))
				indent = &n
			}
			got = run(func() string { return JsonDumps(untag(a.Args[0]), a.Args[1].(bool), indent) })
		case "repr":
			got = run(func() string { return JsonRepr(untag(a.Args[0])) })
		case "str":
			v := untag(a.Args[0])
			if s, ok := v.(string); ok && len(pyRunes(s)) != len([]rune(s)) || strings.ContainsRune(JsonRepr(v), 0xFFFD) {
				continue // a lone surrogate: the answer file cannot spell it as text
			}
			got = run(func() string { return JsonStr(v) })
		case "truth":
			got = run(func() string { return StrBool(JsonTruth(untag(a.Args[0]))) })
		case "eq":
			got = run(func() string { return StrBool(JsonEq(untag(a.Args[0]), untag(a.Args[1]))) })
		case "eqself": // v == v: one object (its items compare by identity first)
			if a.Args[0].([]any)[0] == "t" {
				continue // a Go tuple is a value: no identity (documented)
			}
			got = run(func() string { v := untag(a.Args[0]); return StrBool(JsonEq(v, v)) })
		case "index", "loads":
			var r any
			s := run(func() string {
				if a.Op == "index" {
					r = JsonIndex(untag(a.Args[0]), untag(a.Args[1]))
				} else {
					r = JsonLoads(wtf8(runesOf(a.Args[0].([]any)[1])))
				}
				return ""
			})
			if s != "" {
				got = s
			} else {
				got = retag(r)
			}
			if wt, ok := want.([]any); ok {
				if gt, ok := got.([]any); ok && sameTagged(gt, wt) {
					continue
				}
			}
		}
		if ws, ok := want.(string); ok && got == ws {
			continue
		}
		bad++
		if bad <= 10 {
			t.Errorf("%s(%v):\n got  %v\n want %v", a.Op, a.Args, got, want)
		}
	}
	if bad > 0 {
		t.Fatalf("%d of %d answers differ", bad, len(answers))
	}
}

// TestJsonDepthIsBounded: nesting past JsonMaxDepth raises RecursionError (as CPython
// does past its C stack) instead of overflowing Go's stack; a cycle too.
func TestJsonDepthIsBounded(t *testing.T) {
	deep := strings.Repeat("[", 60_000) + strings.Repeat("]", 60_000)
	v := JsonLoads(deep) // CPython's own limit (about 50,000) is not a fixed number
	if got := len(JsonDumps(v, false, nil)); got != 120_000 {
		t.Fatalf("dumps of a deep list: %d", got)
	}
	tooDeep := strings.Repeat("[", JsonMaxDepth+1) + strings.Repeat("]", JsonMaxDepth+1)
	if got := mathOutcome(func() string { JsonLoads(tooDeep); return "" }); !strings.HasPrefix(got, "!RecursionError") {
		t.Fatalf("loads: %s", got)
	}
	cycle, other := NewList[any](), NewList[any]()
	cycle.Append(cycle)
	other.Append(other)
	for _, f := range []func() string{
		func() string { return JsonDumps(cycle, false, nil) },
		func() string { return JsonRepr(cycle) },
		func() string { return StrBool(JsonEq(cycle, other)) },
	} {
		if got := mathOutcome(f); !strings.HasPrefix(got, "!RecursionError") {
			t.Fatalf("a cycle: %s", got)
		}
	}
}
