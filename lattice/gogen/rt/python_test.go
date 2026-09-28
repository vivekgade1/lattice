package rt

import (
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
	"testing"
)

func ptr[T any](v T) *T { return &v }

// outcome runs f and renders its result like the generator does: the value, or
// "!<Kind|Code>: <message>" for an *Error.
func outcome(f func() string) (out string) {
	defer func() {
		if r := recover(); r != nil {
			e, ok := r.(*Error)
			if !ok {
				panic(r)
			}
			tag := e.Kind
			if e.Code != "" {
				tag = e.Code
			}
			out = "!" + tag + ": " + e.Msg
		}
	}()
	return f()
}

func TestFormatFloatMatchesPython(t *testing.T) {
	for _, c := range formatFloatCases {
		if got := outcome(func() string { return FormatFloat(c.v, c.spec) }); got != c.want {
			t.Errorf("format(%v, %q) = %q, want %q", c.v, c.spec, got, c.want)
		}
	}
}

func TestReprFloatMatchesPython(t *testing.T) {
	for _, c := range reprFloatCases {
		if got := ReprFloat(c.v); got != c.want {
			t.Errorf("repr(%v) = %q, want %q", c.v, got, c.want)
		}
	}
}

func TestRoundHalfEvenMatchesPython(t *testing.T) {
	for _, c := range roundCases {
		got := outcome(func() string { return strconv.FormatInt(RoundHalfEven(c.v), 10) })
		if got != c.want {
			t.Errorf("round(%v) = %q, want %q", c.v, got, c.want)
		}
	}
}

func TestFloatFloorDivModMatchPython(t *testing.T) {
	for _, c := range floatDivCases {
		div := outcome(func() string { return ReprFloat(FloorDivF(c.a, c.b)) })
		mod := outcome(func() string { return ReprFloat(ModF(c.a, c.b)) })
		if div != c.div || mod != c.mod {
			t.Errorf("%v //,%% %v = %q, %q; want %q, %q", c.a, c.b, div, mod, c.div, c.mod)
		}
	}
}

func TestIntOpsMatchPython(t *testing.T) {
	ops := map[string]func(a, b int64) int64{
		"Add": Add, "Sub": Sub, "Mul": Mul, "FloorDiv": FloorDiv, "Mod": Mod,
		"Pow": Pow, "Shl": Shl, "Shr": Shr,
	}
	for _, c := range intCases {
		got := outcome(func() string { return strconv.FormatInt(ops[c.op](c.a, c.b), 10) })
		if got != c.want {
			t.Errorf("%s(%d, %d) = %q, want %q", c.op, c.a, c.b, got, c.want)
		}
	}
	unary := map[string]func(a int64) int64{"Neg": Neg, "Abs": Abs}
	for _, c := range unaryCases {
		got := outcome(func() string { return strconv.FormatInt(unary[c.op](c.a), 10) })
		if got != c.want {
			t.Errorf("%s(%d) = %q, want %q", c.op, c.a, got, c.want)
		}
	}
}

func TestPowSizeCapIsR622(t *testing.T) {
	// Python charges an Int power's bits before computing it (T2Fuel.pow): a result of
	// more than T2_MAX_INT_BITS bits is R622, not R623.
	got := outcome(func() string { return strconv.FormatInt(Pow(2, 70000), 10) })
	want := "!R622: a procedure would build an Int of about 140000 bits; the largest allowed has 65536"
	if got != want {
		t.Fatalf("got %q", got)
	}
	got = outcome(func() string { return strconv.FormatInt(Shl(1, 70000), 10) })
	want = "!R622: a procedure would build an Int of about 70001 bits; the largest allowed has 65536"
	if got != want {
		t.Fatalf("got %q", got)
	}
	// 10 ** 4300 has 4301 digits: Python cannot render it in the R623 message.
	got = outcome(func() string { return strconv.FormatInt(Pow(10, 4300), 10) })
	want = "!ValueError: Exceeds the limit (4300 digits) for integer string conversion; use sys.set_int_max_str_digits() to increase the limit"
	if got != want {
		t.Fatalf("got %q", got)
	}
}

func TestParseIntMatchesPython(t *testing.T) {
	for _, c := range parseIntCases {
		got := outcome(func() string { return strconv.FormatInt(ParseInt(c.s), 10) })
		if got != c.want {
			t.Errorf("int(%q) = %q, want %q", c.s, got, c.want)
		}
	}
}

func TestCaseMappingAndPredicatesMatchPython(t *testing.T) {
	for _, c := range caseCases {
		got := [5]string{Upper(c.s), Lower(c.s), Casefold(c.s), Title(c.s), Capitalize(c.s)}
		want := [5]string{c.upper, c.lower, c.casefold, c.title, c.capitalize}
		if got != want {
			t.Errorf("%q: upper/lower/casefold/title/capitalize = %q, want %q", c.s, got, want)
		}
		flags := ""
		for _, p := range []struct {
			k string
			f func(string) bool
		}{{"s", IsSpace}, {"d", IsDigit}, {"a", IsAlpha}, {"n", IsAlnum}, {"l", IsLower}, {"u", IsUpper}} {
			if p.f(c.s) {
				flags += p.k
			}
		}
		if flags != c.flags {
			t.Errorf("%q: predicates %q, want %q", c.s, flags, c.flags)
		}
	}
}

func TestSplitMatchesPython(t *testing.T) {
	for _, c := range splitCases {
		got := outcome(func() string { // json.dumps(parts, ensure_ascii=False)
			parts := []string{}
			for _, p := range Split(c.s, c.sep, c.max) {
				b, _ := json.Marshal(p)
				parts = append(parts, string(b))
			}
			return "[" + strings.Join(parts, ", ") + "]"
		})
		if got != c.want {
			t.Errorf("%q.split(%v, %d) = %s, want %s", c.s, c.sep, c.max, got, c.want)
		}
	}
}

func TestStripMatchesPython(t *testing.T) {
	for _, c := range stripCases {
		var chars *string
		if !c.noChars {
			chars = &c.chars
		}
		if Strip(c.s, chars) != c.both || LStrip(c.s, chars) != c.l || RStrip(c.s, chars) != c.r {
			t.Errorf("strip(%q, %q) = %q %q %q", c.s, c.chars, Strip(c.s, chars), LStrip(c.s, chars), RStrip(c.s, chars))
		}
	}
}

func show(p *int64) string {
	if p == nil {
		return "None"
	}
	return strconv.FormatInt(*p, 10)
}

func TestSliceStrMatchesPython(t *testing.T) {
	for _, c := range sliceCases {
		got := outcome(func() string { return SliceStr(c.s, c.lo, c.hi, c.step) })
		if got != c.want {
			t.Errorf("%q[%s:%s:%s] = %q, want %q", c.s, show(c.lo), show(c.hi), show(c.step), got, c.want)
		}
	}
}

func TestListSliceMatchesPython(t *testing.T) {
	// The list slice shares the index rules of str slicing: compare on the rune list.
	for _, c := range sliceCases {
		l := NewList([]rune(c.s)...)
		got := outcome(func() string { return string(l.Slice(c.lo, c.hi, c.step).Items) })
		if got != c.want {
			t.Errorf("list(%q)[%s:%s:%s] = %q, want %q", c.s, show(c.lo), show(c.hi), show(c.step), got, c.want)
		}
	}
}

func TestIndexStrMatchesPython(t *testing.T) {
	for _, c := range indexCases {
		if got := outcome(func() string { return IndexStr(c.s, c.i) }); got != c.want {
			t.Errorf("%q[%d] = %q, want %q", c.s, c.i, got, c.want)
		}
	}
}

func TestReprStrMatchesPython(t *testing.T) {
	for _, c := range reprStrCases {
		if got := ReprStr(c.s); got != c.want {
			t.Errorf("repr(%q) = %q, want %q", c.s, got, c.want)
		}
	}
}

// ---- fuel (lattice/t2/support.py T2Fuel) ---------------------------------------------------

func TestFuelTickAndMessage(t *testing.T) {
	f := NewFuel(3)
	f.Tick()
	f.Tick()
	f.Tick()
	got := outcome(func() string { f.Tick(); return "" })
	want := "!R620: procedure ran out of fuel: the request's budget of 3 steps is spent (every loop iteration, call and comprehension element costs one)"
	if got != want || f.Left != -1 {
		t.Fatalf("got %q, left %d", got, f.Left)
	}
}

func TestFuelEnterDepth(t *testing.T) {
	f := NewFuel(1000)
	for range 200 {
		f.Enter()
	}
	got := outcome(func() string { f.Enter(); return "" })
	want := "!R620: procedure calls nest deeper than 200 (recursion is bounded)"
	if got != want || f.Left != 1000-201 || f.Depth != 201 {
		t.Fatalf("got %q left %d depth %d", got, f.Left, f.Depth)
	}
	f = NewFuel(1000)
	f.Enter()
	f.Leave()
	if f.Depth != 0 || f.Left != 999 {
		t.Fatal("leave")
	}
	f = NewFuel(0)
	if got := outcome(func() string { f.Enter(); return "" }); !strings.HasPrefix(got, "!R620: procedure ran out of fuel") || f.Depth != 0 {
		t.Fatalf("enter charges before it deepens: %q depth %d", got, f.Depth)
	}
}

func TestFuelEachBulkGrowBits(t *testing.T) {
	f := NewFuel(100)
	f.Each(3)
	f.Bulk(10)
	f.Grow(15) // 15 // 16 == 0
	f.Grow(32) // 2
	f.Bits(127)
	f.Bits(256) // 2
	if f.Left != 100-3-10-2-2 {
		t.Fatalf("left %d", f.Left)
	}
	got := outcome(func() string { f.Grow(T2DefaultMaxSize + 1); return "" })
	want := "!R622: a procedure would build a value of 10000001 characters or elements; the largest allowed is 10000000 (ApiProfile.max_value_size)"
	if got != want {
		t.Fatalf("got %q", got)
	}
	got = outcome(func() string { f.Bits(T2MaxIntBits + 1); return "" })
	want = "!R622: a procedure would build an Int of about 65537 bits; the largest allowed has 65536"
	if got != want {
		t.Fatalf("got %q", got)
	}
	f = NewFuel(5)
	if got := outcome(func() string { f.Bulk(6); return "" }); !strings.HasPrefix(got, "!R620") || f.Left != -1 {
		t.Fatalf("bulk: %q left %d", got, f.Left)
	}
	f = NewFuel(5)
	f.MaxSize = 100
	if got := outcome(func() string { f.Grow(101); return "" }); !strings.Contains(got, "the largest allowed is 100 ") {
		t.Fatalf("max size: %q", got)
	}
}

// ---- lists ---------------------------------------------------------------------------------

func TestListMethodsMatchPython(t *testing.T) {
	l := NewList[int64](1, 2, 3)
	l.Set(-1, 30)
	l.Insert(-100, 0) // [0 1 2 30]
	l.Insert(100, 99) // [0 1 2 30 99]
	l.Insert(-1, 7)   // [0 1 2 30 7 99]
	l.Extend([]int64{5, 6})
	if fmt.Sprint(l.Items) != "[0 1 2 30 7 99 5 6]" {
		t.Fatalf("insert/extend %v", l.Items)
	}
	if l.Pop(nil) != 6 || l.Pop(ptr(int64(0))) != 0 || l.Pop(ptr(int64(-2))) != 99 {
		t.Fatal("pop")
	}
	eq := func(a, b int64) bool { return a == b }
	l.Remove(30, eq)
	l.Reverse()
	if fmt.Sprint(l.Items) != "[5 7 2 1]" || l.Len() != 4 {
		t.Fatalf("remove/reverse %v", l.Items)
	}
	l.SortStable(func(a, b int64) bool { return a < b })
	if fmt.Sprint(l.Items) != "[1 2 5 7]" {
		t.Fatalf("sort %v", l.Items)
	}
	for _, c := range []struct {
		f    func()
		want string
	}{
		{func() { l.Get(4) }, "!IndexError: list index out of range"},
		{func() { l.Get(-5) }, "!IndexError: list index out of range"},
		{func() { l.Set(9, 1) }, "!IndexError: list assignment index out of range"},
		{func() { l.Pop(ptr(int64(9))) }, "!IndexError: pop index out of range"},
		{func() { NewList[int64]().Pop(nil) }, "!IndexError: pop from empty list"},
		{func() { l.Remove(42, eq) }, "!ValueError: list.remove(x): x not in list"},
		{func() { l.Slice(nil, nil, ptr(int64(0))) }, "!ValueError: slice step cannot be zero"},
	} {
		if got := outcome(func() string { c.f(); return "" }); got != c.want {
			t.Errorf("got %q, want %q", got, c.want)
		}
	}
	l.Clear()
	if l.Len() != 0 {
		t.Fatal("clear")
	}
	// Python's sort is stable.
	p := NewList("b1", "a1", "b2", "a2")
	p.SortStable(func(x, y string) bool { return x[0] < y[0] })
	if fmt.Sprint(p.Items) != "[a1 a2 b1 b2]" {
		t.Fatalf("stable %v", p.Items)
	}
}

// ---- dict and set --------------------------------------------------------------------------

func TestDictMatchesPython(t *testing.T) {
	d := NewDict[string, int64]()
	for i, k := range []string{"x", "y", "z", "w"} {
		d.Set(k, int64(i))
	}
	d.Delete("y")
	d.Set("x", 10)
	d.Set("y", 11)
	if fmt.Sprint(d.Keys()) != "[x z w y]" || fmt.Sprint(d.Values()) != "[10 2 3 11]" || d.Len() != 4 {
		t.Fatalf("order %v %v", d.Keys(), d.Values())
	}
	items := d.Items()
	if items[0].Key != "x" || items[0].Value != 10 || items[3].Key != "y" {
		t.Fatalf("items %v", items)
	}
	c := d.Copy()
	c.Set("q", 1)
	c.Delete("x")
	if d.Len() != 4 || c.Len() != 4 || fmt.Sprint(c.Keys()) != "[z w y q]" {
		t.Fatalf("copy %v %v", d.Keys(), c.Keys())
	}
	if v, ok := d.Lookup("z"); !ok || v != 2 {
		t.Fatal("lookup")
	}
	if _, ok := d.Lookup("nope"); ok || d.Delete("nope") {
		t.Fatal("missing")
	}
	// Many deletions (tombstones) keep order and lookups right.
	big := NewDict[int64, int64]()
	for i := range int64(1000) {
		big.Set(i, i)
	}
	for i := range int64(990) {
		big.Delete(i)
	}
	big.Set(5, 5)
	if fmt.Sprint(big.Keys()) != "[990 991 992 993 994 995 996 997 998 999 5]" || big.Get(995) != 995 {
		t.Fatalf("tombstones %v", big.Keys())
	}
	if got := outcome(func() string { d.Get("zz"); return "" }); got != "!KeyError: 'zz'" {
		t.Fatalf("str key: %q", got)
	}
	if got := outcome(func() string { big.Get(1); return "" }); got != "!KeyError: 1" {
		t.Fatalf("int key: %q", got)
	}
	var none *Dict[string, int64]
	_ = none
}

func TestSetMatchesT2Set(t *testing.T) {
	s := NewSet("pear", "apple", "fig", "kiwi", "apple")
	if fmt.Sprint(s.Items()) != "[pear apple fig kiwi]" || s.Len() != 4 || !s.Has("fig") {
		t.Fatalf("order %v", s.Items())
	}
	s.Discard("apple")
	s.Discard("nope")
	s.Add("apple")
	s.Add("pear")
	if fmt.Sprint(s.Items()) != "[pear fig kiwi apple]" {
		t.Fatalf("re-add %v", s.Items())
	}
	o := NewSet("kiwi", "plum", "pear")
	if fmt.Sprint(s.Union(o).Items()) != "[pear fig kiwi apple plum]" ||
		fmt.Sprint(s.Intersection(o).Items()) != "[pear kiwi]" ||
		fmt.Sprint(s.Difference(o).Items()) != "[fig apple]" ||
		fmt.Sprint(s.SymmetricDifference(o).Items()) != "[fig apple plum]" {
		t.Fatal("algebra")
	}
	if !NewSet("kiwi").IsSubset(s) || !s.IsSuperset(NewSet("fig", "kiwi")) || s.IsSubset(o) {
		t.Fatal("subset")
	}
	if !NewSet(1, 2).Equal(NewSet(2, 1)) || NewSet(1).Equal(NewSet(1, 2)) {
		t.Fatal("equal ignores order")
	}
	c := s.Copy()
	c.Update("z")
	c.Remove("pear")
	if s.Len() != 4 || fmt.Sprint(c.Items()) != "[fig kiwi apple z]" {
		t.Fatal("copy")
	}
	if got := outcome(func() string { c.Remove("q"); return "" }); got != "!KeyError: 'q'" {
		t.Fatalf("remove: %q", got)
	}
	c.Clear()
	if c.Len() != 0 {
		t.Fatal("clear")
	}
}

func TestOpt(t *testing.T) {
	if o := Some[int64](3); !o.Ok || o.V != 3 {
		t.Fatal("some")
	}
	if o := None[string](); o.Ok || o.V != "" {
		t.Fatal("none")
	}
}

func TestReprValues(t *testing.T) {
	for _, c := range []struct {
		v    any
		want string
	}{
		{"a'b", `"a'b"`}, {int64(-3), "-3"}, {1.0, "1.0"}, {true, "True"}, {false, "False"},
	} {
		if got := Repr(c.v); got != c.want {
			t.Errorf("Repr(%#v) = %q, want %q", c.v, got, c.want)
		}
	}
}

// ---- try -----------------------------------------------------------------------------------

func TestTryHandlerOrderAndRethrow(t *testing.T) {
	got := ""
	Try(func() { Raise("ValueError", "bad") },
		Handler{Kinds: []string{"KeyError"}, Fn: func(e *Error) { got = "key" }},
		Handler{Kinds: []string{"IndexError", "ValueError"}, Fn: func(e *Error) { got = "value:" + e.Msg }},
		Handler{Kinds: []string{"ValueError"}, Fn: func(e *Error) { got = "second" }})
	if got != "value:bad" {
		t.Fatalf("got %q", got)
	}
	// No handler matches: the error propagates unchanged.
	if out := outcome(func() string {
		Try(func() { Raise("IndexError", "i") }, Handler{Kinds: []string{"KeyError"}, Fn: func(*Error) {}})
		return ""
	}); out != "!IndexError: i" {
		t.Fatalf("got %q", out)
	}
	// An error raised in a handler propagates.
	if out := outcome(func() string {
		Try(func() { Raise("KeyError", "k") }, Handler{Kinds: []string{"KeyError"}, Fn: func(*Error) { Raise("ValueError", "v") }})
		return ""
	}); out != "!ValueError: v" {
		t.Fatalf("got %q", out)
	}
	// A body that does not fail runs no handler.
	ran := false
	Try(func() {}, Handler{Kinds: []string{"KeyError"}, Fn: func(*Error) { ran = true }})
	if ran {
		t.Fatal("handler ran")
	}
	// Integer overflow is a Lattice error: never caught.
	if out := outcome(func() string {
		Try(func() { Add(math.MaxInt64, 1) }, Handler{Kinds: []string{"ValueError", "KeyError", "IndexError", "ZeroDivisionError"}, Fn: func(*Error) {}})
		return ""
	}); !strings.HasPrefix(out, "!R623") {
		t.Fatalf("got %q", out)
	}
	// Go runtime panics are not Lattice errors and propagate.
	defer func() {
		if r := recover(); r == nil {
			t.Fatal("go panic swallowed")
		}
	}()
	Try(func() {
		var m map[string]int
		m["x"] = 1
	}, Handler{Kinds: []string{"KeyError"}, Fn: func(*Error) {}})
}
