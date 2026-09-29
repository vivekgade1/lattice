package rt

import (
	"encoding/json"
	"os"
	"strconv"
	"testing"
)

type mathArg [2]string

func (a mathArg) isInt() bool { return a[0] == "i" }

func (a mathArg) i64() int64 {
	v, err := strconv.ParseInt(a[1], 10, 64)
	if err != nil {
		panic(err)
	}
	return v
}

func (a mathArg) f64() float64 {
	if a.isInt() {
		return float64(a.i64())
	}
	v, err := strconv.ParseFloat(a[1], 64)
	if err != nil {
		panic(err)
	}
	return v
}

type mathAnswer struct {
	Fn   string    `json:"fn"`
	Args []mathArg `json:"args"`
	Out  string    `json:"out"`
	CR   string    `json:"cr"`
}

// outcome runs f as T2 would: its rendered result, or "!Kind: message" ("!R623").
func mathOutcome(f func() string) (out string) {
	defer func() {
		if r := recover(); r != nil {
			e := r.(*Error)
			if e.Code != "" {
				out = "!" + e.Code
				return
			}
			out = "!" + e.Kind + ": " + e.Msg
		}
	}()
	return f()
}

func logOf(a mathArg, base10 bool) float64 {
	switch {
	case a.isInt() && base10:
		return MathLog10I(a.i64())
	case a.isInt():
		return MathLogI(a.i64())
	case base10:
		return MathLog10(a.f64())
	}
	return MathLog(a.f64())
}

// TestMathAnswers: every `math` function T2 calls answers as Python 3.14 does
// (testdata/gen_math_answers.py).
func TestMathAnswers(t *testing.T) {
	data, err := os.ReadFile("testdata/math_answers.json")
	if err != nil {
		t.Fatal(err)
	}
	var answers []mathAnswer
	if err := json.Unmarshal(data, &answers); err != nil {
		t.Fatal(err)
	}
	if len(answers) < 20000 {
		t.Fatal("testdata/math_answers.json is incomplete")
	}
	bad, libm := 0, 0
	for _, a := range answers {
		x := a.Args[0]
		var f func() string
		fl := func(v float64) string { return ReprFloat(v) }
		switch a.Fn {
		case "floor", "ceil", "trunc":
			fn := map[string]func(float64) int64{"floor": MathFloor, "ceil": MathCeil, "trunc": MathTrunc}[a.Fn]
			f = func() string {
				if x.isInt() {
					return StrInt(x.i64())
				}
				return StrInt(fn(x.f64()))
			}
		case "sqrt":
			f = func() string { return fl(MathSqrt(x.f64())) }
		case "exp":
			f = func() string { return fl(MathExp(x.f64())) }
		case "fabs":
			f = func() string { return fl(MathFabs(x.f64())) }
		case "log", "log10":
			f = func() string {
				num := logOf(x, a.Fn == "log10")
				if len(a.Args) == 1 {
					return fl(num)
				}
				return fl(MathLogBase(num, logOf(a.Args[1], false)))
			}
		case "pow":
			f = func() string { return fl(MathPow(x.f64(), a.Args[1].f64())) }
		case "isclose":
			f = func() string { return StrBool(MathIsClose(x.f64(), a.Args[1].f64())) }
		case "gcd":
			f = func() string { return StrInt(MathGcd(x.i64(), a.Args[1].i64())) }
		default:
			t.Fatalf("unknown fn %s", a.Fn)
		}
		want := a.Out
		if a.CR != "" {
			want = a.CR // correctly rounded (Python's libm may be one ulp off: counted below)
			if a.CR != a.Out {
				libm++
			}
		}
		if got := mathOutcome(f); got != want {
			bad++
			if bad <= 10 {
				t.Errorf("math.%s%v = %s, want %s", a.Fn, a.Args, got, want)
			}
		}
	}
	if bad > 0 {
		t.Fatalf("%d of %d answers differ", bad, len(answers))
	}
	// the platform's libm (which CPython calls) is not correctly rounded everywhere; the
	// known limitation is documented (docs/lattice-language-rules.md §6); it stays rare
	if libm*1000 > len(answers) {
		t.Fatalf("%d of %d libm answers are not correctly rounded", libm, len(answers))
	}
	t.Logf("%d of %d libm answers are not correctly rounded", libm, len(answers))
}
