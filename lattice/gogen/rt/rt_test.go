package rt

import (
	"math"
	"testing"
)

func mustFail(t *testing.T, code string, f func()) {
	t.Helper()
	defer func() {
		r := recover()
		e, ok := r.(*Error)
		if !ok || (e.Code != code && e.Kind != code) {
			t.Fatalf("want %s, got %#v", code, r)
		}
	}()
	f()
}

func TestIntOverflow(t *testing.T) {
	mustFail(t, "R623", func() { Add(math.MaxInt64, 1) })
	mustFail(t, "R623", func() { Neg(math.MinInt64) })
	mustFail(t, "R623", func() { Abs(math.MinInt64) })
	mustFail(t, "R623", func() { FloorDiv(math.MinInt64, -1) })
	mustFail(t, "R623", func() { Pow(2, 63) })
	mustFail(t, "ZeroDivisionError", func() { FloorDiv(1, 0) })
	if FloorDiv(-7, 2) != -4 || Mod(-7, 2) != 1 || Mod(7, -2) != -1 {
		t.Fatal("floor semantics")
	}
}

func TestParseInt(t *testing.T) {
	if ParseInt(" 42 ") != 42 || ParseInt("-1_000") != -1000 {
		t.Fatal("parse")
	}
	mustFail(t, "ValueError", func() { ParseInt("4x") })
	mustFail(t, "R623", func() { ParseInt("9223372036854775808") })
}

func TestReprFloat(t *testing.T) {
	tenth, fifth := 0.1, 0.2 // variables: Go folds the constant 0.1 + 0.2 exactly to 0.3
	for f, want := range map[float64]string{
		1.0: "1.0", 1e16: "1e+16", 1e15: "1000000000000000.0", 1000000.0: "1000000.0",
		0.0001: "0.0001", 1e-05: "1e-05", tenth + fifth: "0.30000000000000004",
		math.Inf(1): "inf", math.Inf(-1): "-inf",
	} {
		if got := ReprFloat(f); got != want {
			t.Errorf("ReprFloat(%v) = %q, want %q", f, got, want)
		}
	}
	if ReprFloat(math.Copysign(0, -1)) != "-0.0" || ReprFloat(math.NaN()) != "nan" {
		t.Error("signed zero / nan")
	}
}

func TestStrings(t *testing.T) {
	s := "héllo👋"
	if Len(s) != 6 || IndexStr(s, -1) != "👋" {
		t.Fatal("runes")
	}
	one, four := int64(1), int64(4)
	if SliceStr(s, &one, &four, nil) != "éll" {
		t.Fatal("slice")
	}
	if Upper("straße") != "STRASSE" || Casefold("Straße") != "strasse" {
		t.Fatal("case mapping")
	}
	if Title("hello wORLD o'neil") != "Hello World O'Neil" {
		t.Fatal("title")
	}
	if !IsDigit("²") {
		t.Fatal("isdigit")
	}
}

func TestListSliceCopies(t *testing.T) {
	a := NewList[int64](1, 2, 3)
	zero, two := int64(0), int64(2)
	b := a.Slice(&zero, &two, nil)
	b.Append(9)
	if a.Len() != 3 || a.Get(2) != 3 {
		t.Fatal("slice must copy")
	}
	mustFail(t, "IndexError", func() { a.Get(5) })
}

func TestDictOrder(t *testing.T) {
	d := NewDict[string, int64]()
	d.Set("b", 1)
	d.Set("a", 2)
	d.Set("b", 3) // keeps position
	d.Delete("a")
	d.Set("a", 4) // moves to end
	if got := d.Keys(); got[0] != "b" || got[1] != "a" {
		t.Fatalf("order %v", got)
	}
	mustFail(t, "KeyError", func() { d.Get("zz") })
}

func TestTryCatchesOnlyMatchingKinds(t *testing.T) {
	caught := ""
	Try(func() { Raise("KeyError", "k") },
		Handler{Kinds: []string{"KeyError"}, Fn: func(e *Error) { caught = e.Kind }})
	if caught != "KeyError" {
		t.Fatal("not caught")
	}
	mustFail(t, "R620", func() {
		Try(func() { Fail("R620", "fuel") }, Handler{Kinds: []string{"ValueError"}, Fn: func(*Error) {}})
	})
}
