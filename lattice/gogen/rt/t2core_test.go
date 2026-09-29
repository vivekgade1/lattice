package rt

import (
	"encoding/json"
	"math"
	"math/big"
	"os"
	"strconv"
	"testing"
)

// The answers of Python 3.14 (testdata/gen_t2core_answers.py) for the core operations
// compiled T2 procedures call.
type coreAnswer struct {
	Fn   string   `json:"fn"`
	Args []tagged `json:"args"`
	Out  string   `json:"out"`
}

func (tg tagged) i64() int64 {
	var s string
	_ = json.Unmarshal(tg.V, &s)
	n, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		panic(err)
	}
	return n
}

func (tg tagged) f64() float64 {
	var s string
	_ = json.Unmarshal(tg.V, &s)
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		switch s {
		case "inf":
			return math.Inf(1)
		case "-inf":
			return math.Inf(-1)
		case "nan":
			return math.NaN()
		}
		panic(err)
	}
	return f
}

func (tg tagged) boolean() bool {
	var b bool
	_ = json.Unmarshal(tg.V, &b)
	return b
}

func renderFloat(f float64) string { return ReprFloat(f) }

func TestCoreAnswers(t *testing.T) {
	data, err := os.ReadFile("testdata/t2core_answers.json")
	if err != nil {
		t.Fatal(err)
	}
	var answers []coreAnswer
	if err := json.Unmarshal(data, &answers); err != nil {
		t.Fatal(err)
	}
	if len(answers) < 1000 {
		t.Fatalf("testdata/t2core_answers.json is incomplete")
	}
	seen := map[string]int{}
	for _, a := range answers {
		seen[a.Fn]++
		var f func() string
		switch a.Fn {
		case "format_int":
			f = func() string { return FormatInt(a.Args[0].i64(), a.Args[1].str()) }
		case "format_bool":
			f = func() string { return FormatBool(a.Args[0].boolean(), a.Args[1].str()) }
		case "format_str":
			f = func() string { return FormatStr(a.Args[0].str(), a.Args[1].str()) }
		case "format_none":
			f = func() string { return FormatNone(a.Args[0].str()) }
		case "ascii":
			f = func() string { return AsciiStr(a.Args[0].str()) }
		case "parse_float":
			f = func() string { return renderFloat(ParseFloat(a.Args[0].str())) }
		case "round_f":
			f = func() string { return renderFloat(RoundF(a.Args[0].f64(), a.Args[1].i64())) }
		case "round0":
			f = func() string { return strconv.FormatInt(RoundHalfEven(a.Args[0].f64()), 10) }
		case "sum_float":
			var texts []string
			_ = json.Unmarshal([]byte(a.Args[0].str()), &texts)
			xs := make([]float64, len(texts))
			for i, s := range texts {
				xs[i] = tagged{T: "float", V: json.RawMessage(strconv.Quote(s))}.f64()
			}
			f = func() string {
				switch a.Args[1].T {
				case "none":
					r, isInt := SumFloat(IterSlice(xs), 0, true)
					if isInt {
						return strconv.FormatInt(int64(r), 10)
					}
					return renderFloat(r)
				case "int":
					r, isInt := SumFloat(IterSlice(xs), float64(a.Args[1].i64()), true)
					if isInt {
						return strconv.FormatInt(int64(r), 10)
					}
					return renderFloat(r)
				default:
					r, _ := SumFloat(IterSlice(xs), a.Args[1].f64(), false)
					return renderFloat(r)
				}
			}
		case "truediv_int":
			f = func() string { return renderFloat(TrueDivInt(a.Args[0].i64(), a.Args[1].i64())) }
		case "pow_f":
			f = func() string { return renderFloat(PowF(a.Args[0].f64(), a.Args[1].f64())) }
		case "cmp_if":
			f = func() string {
				c := CmpIF(a.Args[0].i64(), a.Args[1].f64())
				if c == Unordered {
					return "unordered"
				}
				return strconv.Itoa(c)
			}
		case "int_of_float":
			f = func() string { return strconv.FormatInt(IntOfFloat(a.Args[0].f64()), 10) }
		case "div_f":
			f = func() string { return renderFloat(DivF(a.Args[0].f64(), a.Args[1].f64())) }
		case "div_mixed":
			f = func() string {
				x, y := a.Args[0], a.Args[1]
				if x.T == "int" {
					return renderFloat(DivF(float64(x.i64()), y.f64()))
				}
				return renderFloat(DivF(x.f64(), float64(y.i64())))
			}
		default:
			t.Fatalf("unknown answer kind %q", a.Fn)
		}
		want := a.Out
		if a.Fn == "round0" || a.Fn == "int_of_float" {
			// Int-typed results are range-checked in T2 (R623); Python's own int() is not
			if v, ok := new(big.Int).SetString(want, 10); ok && !v.IsInt64() {
				want = "!R623: integer " + want + " is outside the 64-bit range"
			}
		}
		if a.Fn == "pow_f" && len(want) > 0 && want[0] == '(' {
			// Python returns a complex number: Go refuses (see PowF).
			want = "!ValueError: a negative number raised to a fractional power has no Float value"
		}
		if got := outcome(f); got != want {
			t.Errorf("%s%v: got %q, want %q", a.Fn, a.Args, got, want)
		}
	}
	if len(seen) < 15 {
		t.Fatalf("answer kinds: %v", seen)
	}
}

func drain[T any](c Cursor[T]) []T {
	var out []T
	for {
		v, ok := c()
		if !ok {
			return out
		}
		out = append(out, v)
	}
}

func TestSumInt(t *testing.T) {
	if SumInt(IterSlice([]int64{math.MaxInt64, 1, -1}), 0) != math.MaxInt64 {
		t.Fatal("intermediate overflow must not fail")
	}
	mustFail(t, "R623", func() { SumInt(IterSlice([]int64{math.MaxInt64, 1}), 0) })
	mustFail(t, "R623", func() { SumInt(IterSlice([]int64{math.MinInt64}), -1) })
	if SumInt(IterSlice([]int64{math.MinInt64, math.MinInt64, math.MaxInt64, math.MaxInt64}), 2) != 0 {
		t.Fatal("big accumulation")
	}
	if SumInt(IterSlice([]int64{}), 5) != 5 {
		t.Fatal("start")
	}
}

func TestDictCursorDetectsMutation(t *testing.T) {
	d := NewDict[string, int64]()
	d.Set("a", 1)
	d.Set("b", 2)
	c := IterDict(d)
	if k, _ := c(); k != "a" {
		t.Fatal("first key")
	}
	d.Set("a", 10) // an existing key: no change of size
	if k, _ := c(); k != "b" {
		t.Fatal("second key")
	}
	d.Set("c", 3)
	mustFail(t, "RuntimeError", func() { c() })
	s := NewSet[int64](1, 2)
	sc := IterSet(s)
	sc()
	s.Add(3)
	mustFail(t, "RuntimeError", func() { sc() })
}

func TestListCursorIsLiveAndLatched(t *testing.T) {
	l := NewList[int64](1, 2)
	c := IterList(l)
	var got []int64
	for {
		v, ok := c()
		if !ok {
			break
		}
		got = append(got, v)
		if len(l.Items) < 5 {
			l.Append(v * 10)
		}
	}
	if len(got) != 5 || got[4] != 100 {
		t.Fatalf("live iteration: %v", got)
	}
	l.Append(7)
	if _, ok := c(); ok {
		t.Fatal("an exhausted list iterator stays exhausted")
	}
	r := Reversed(NewList[int64](1, 2, 3))
	if xs := drain(r.Cursor()); len(xs) != 3 || xs[0] != 3 {
		t.Fatalf("reversed: %v", xs)
	}
	if xs := drain(r.Cursor()); len(xs) != 0 {
		t.Fatal("reversed is one-shot")
	}
}

func TestRangeIsReiterableAndSized(t *testing.T) {
	r := Range(0, 10, 3)
	if xs := drain(r.Cursor()); len(xs) != 4 || xs[3] != 9 {
		t.Fatalf("range: %v", xs)
	}
	if xs := drain(r.Cursor()); len(xs) != 4 {
		t.Fatal("a range iterates again")
	}
	if n, ok := r.Sized(); !ok || n != 4 {
		t.Fatal("len(range)")
	}
	if n, _ := Range(10, 0, -3).Sized(); n != 4 {
		t.Fatal("negative step")
	}
	if !r.Contains(6, nil) || r.Contains(7, nil) || r.Contains(12, nil) {
		t.Fatal("in range")
	}
	mustFail(t, "ValueError", func() { Range(0, 1, 0) })
	if n, _ := Range(math.MinInt64, math.MaxInt64, math.MaxInt64).Sized(); n != 3 {
		t.Fatalf("huge range: %d", n)
	}
}

func TestEachChargesPerElement(t *testing.T) {
	f := NewFuel(2)
	c := Each(f, IterSlice([]int64{1, 2, 3}))
	c()
	c()
	mustFail(t, "R620", func() { c() })
	f = NewFuel(3)
	it := BulkIter(f, Range(0, 3, 1))
	if f.Left != 0 || len(drain(it.Cursor())) != 3 {
		t.Fatal("a range is charged its length up front")
	}
	f = NewFuel(10)
	one := NewIter(IterSlice([]int64{1, 2, 3}))
	b := BulkIter(f, one)
	b.Cursor()()
	if f.Left != 9 {
		t.Fatal("a one-shot iterator is charged as it is consumed")
	}
}

func TestMinMaxSorted(t *testing.T) {
	lt := func(a, b float64) bool { return a < b }
	gt := func(a, b float64) bool { return a > b }
	nan := math.NaN()
	if !math.IsNaN(MinMax(IterSlice([]float64{nan, 1}), gt, "max")) {
		t.Fatal("max keeps the first when nothing compares greater")
	}
	if MinMax(IterSlice([]float64{1, nan, 3}), gt, "max") != 3 {
		t.Fatal("max")
	}
	if MinMax(IterSlice([]float64{2, 1, 3}), lt, "min") != 1 {
		t.Fatal("min")
	}
	mustFail(t, "ValueError", func() { MinMax(IterSlice([]float64{}), lt, "min") })
	byLen := func(s string) int64 { return Len(s) }
	ilt := func(a, b int64) bool { return a < b }
	got := SortedKey(IterSlice([]string{"bb", "a", "cc", "d"}), byLen, ilt, true)
	if len(got.Items) != 4 || got.Items[0] != "bb" || got.Items[1] != "cc" || got.Items[2] != "a" {
		t.Fatalf("reverse stable: %v", got.Items)
	}
}

func TestSizes(t *testing.T) {
	f := NewFuel(100)
	mustFail(t, "R622", func() { GrowMulStr(f, "x", 20000000) })
	if GrowMulStr(f, "ab", -1) != "" || GrowMulStr(f, "ab", 2) != "abab" {
		t.Fatal("str * n")
	}
	f = NewFuel(100)
	if GrowAddStr(f, "héllo", "x") != "héllox" || f.Left != 100 {
		t.Fatal("small concatenations cost nothing")
	}
	mustFail(t, "R622", func() { GrowPowInt(f, 2, 70000) })
	mustFail(t, "R623", func() { GrowPowInt(f, 2, 64) })
	mustFail(t, "R622", func() { GrowShlInt(f, 1, math.MaxInt64) })
	if GrowMulInt(f, 6, 7) != 42 {
		t.Fatal("int *")
	}
}

func TestFormatValueSpecCharge(t *testing.T) {
	f := NewFuel(100)
	if FmtStr(f, "ab", ">40") != "                                      ab" || f.Left != 98 {
		t.Fatalf("width 40 costs 40 // 16 = 2 (left %d)", f.Left)
	}
	mustFail(t, "R622", func() { FmtStr(NewFuel(100), "ab", ">20000000") })
}

//go:noinline
func mulAdd(x, y, z float64) float64 { return float64(x*y) + z }

//go:noinline
func mulThenSub(x, y, z float64) float64 {
	a := float64(x * y)
	return a - z
}

// Go may fuse x*y + z into one multiply-add (it does on arm64); Python never does. The
// emitter (lattice/gogen/t2.py) wraps every Float + - * in an explicit float64(...),
// which the Go spec says rounds and prevents fusion: these are the semantics suite's
// float_mul_add_not_fused and float_mul_then_sub_not_fused, in the emitted form.
func TestExplicitConversionPreventsFusedMultiplyAdd(t *testing.T) {
	if got := mulAdd(0.1, 10.0, -1.0); got != 0 {
		t.Fatalf("float64(x*y) + z = %v, want 0 (Python)", got)
	}
	if got := mulThenSub(0.1, 10.0, 1.0); got != 0 {
		t.Fatalf("a := float64(x*y); a - z = %v, want 0 (Python)", got)
	}
}
