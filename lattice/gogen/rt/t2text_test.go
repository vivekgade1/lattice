package rt

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func rawStr(r json.RawMessage) string {
	var s string
	if err := json.Unmarshal(r, &s); err != nil {
		panic(err)
	}
	return s
}

func rawInt(r json.RawMessage) int64 {
	var n int64
	if err := json.Unmarshal(r, &n); err != nil {
		panic(err)
	}
	return n
}

// strCall runs one Str method answer's call on Go.
func strCall(t *testing.T, fn string, args []json.RawMessage) any {
	s := func(i int) string { return rawStr(args[i]) }
	n := func(i int) int64 { return rawInt(args[i]) }
	p := func(i int) *string { return PStr(s(i)) }
	switch fn {
	case "lower":
		return Lower(s(0))
	case "upper":
		return Upper(s(0))
	case "casefold":
		return Casefold(s(0))
	case "title":
		return Title(s(0))
	case "capitalize":
		return Capitalize(s(0))
	case "splitlines":
		return SplitLines(s(0))
	case "isdigit":
		return IsDigit(s(0))
	case "isalpha":
		return IsAlpha(s(0))
	case "isalnum":
		return IsAlnum(s(0))
	case "isspace":
		return IsSpace(s(0))
	case "islower":
		return IsLower(s(0))
	case "isupper":
		return IsUpper(s(0))
	case "strip":
		return Strip(s(0), nil)
	case "lstrip":
		return LStrip(s(0), nil)
	case "rstrip":
		return RStrip(s(0), nil)
	case "strip_chars":
		return Strip(s(0), p(1))
	case "lstrip_chars":
		return LStrip(s(0), p(1))
	case "rstrip_chars":
		return RStrip(s(0), p(1))
	case "split":
		return Split(s(0), nil, -1)
	case "rsplit":
		return RSplit(s(0), nil, -1)
	case "split_sep":
		return Split(s(0), p(1), -1)
	case "rsplit_sep":
		return RSplit(s(0), p(1), -1)
	case "split_sep_max":
		return Split(s(0), p(1), n(2))
	case "rsplit_sep_max":
		return RSplit(s(0), p(1), n(2))
	case "startswith":
		return StartsWith(s(0), s(1))
	case "endswith":
		return EndsWith(s(0), s(1))
	case "find":
		return Find(s(0), s(1))
	case "rfind":
		return RFind(s(0), s(1))
	case "count":
		return CountStr(s(0), s(1))
	case "partition":
		a, b, c := Partition(s(0), s(1))
		return []string{a, b, c}
	case "rpartition":
		a, b, c := RPartition(s(0), s(1))
		return []string{a, b, c}
	case "replace":
		return Replace(s(0), s(1), s(2), -1)
	case "replace_count":
		return Replace(s(0), s(1), s(2), n(3))
	case "zfill":
		return ZFill(s(0), n(1))
	case "ljust":
		return LJust(s(0), n(1), nil)
	case "rjust":
		return RJust(s(0), n(1), nil)
	case "center":
		return Center(s(0), n(1), nil)
	case "ljust_fill":
		return LJust(s(0), n(1), p(2))
	case "rjust_fill":
		return RJust(s(0), n(1), p(2))
	case "center_fill":
		return Center(s(0), n(1), p(2))
	case "join":
		var items []string
		_ = json.Unmarshal(args[1], &items)
		return JoinStr(NewFuel(1<<40), s(0), IterSlice(items))
	}
	t.Fatalf("unknown Str answer %q", fn)
	return nil
}

func TestStrAnswers(t *testing.T) {
	a := loadText(t)
	seen := map[string]int{}
	for _, c := range a.Str {
		seen[c.Fn]++
		var got any
		exc := caught(func() { got = strCall(t, c.Fn, c.Args) })
		if c.Out.Exc != nil {
			if exc != *c.Out.Exc {
				t.Errorf("%s%s: Go %q, Python %q", c.Fn, c.Args, exc, *c.Out.Exc)
			}
			continue
		}
		if exc != "" {
			t.Errorf("%s%s: Go raised %q", c.Fn, c.Args, exc)
			continue
		}
		var want any
		_ = json.Unmarshal(c.Out.V, &want)
		gotJSON, _ := json.Marshal(got)
		var gotAny any
		_ = json.Unmarshal(gotJSON, &gotAny)
		if want == nil {
			want = []any{}
		}
		if !reflect.DeepEqual(gotAny, want) {
			t.Errorf("%s%s: Go %s, Python %s", c.Fn, c.Args, gotJSON, c.Out.V)
		}
	}
	if len(seen) < 41 {
		t.Fatalf("only %d methods answered", len(seen))
	}
}

func TestStrCenterBias(t *testing.T) {
	if got := Center("ab", 5, nil); got != "  ab " {
		t.Fatalf("center: %q", got)
	}
	if got := Center("ab", 6, PStr("*")); got != "**ab**" {
		t.Fatalf("center: %q", got)
	}
	if got := ZFill("-42", 6); got != "-00042" {
		t.Fatalf("zfill: %q", got)
	}
}

// T2Fuel.replace: the scan, then (when the result grows) its size.
func TestSizedReplaceCharges(t *testing.T) {
	f := NewFuel(100)
	s := strings.Repeat("a", 32)
	if got := SizedReplace(f, s, "a", "bb", -1); got != strings.Repeat("bb", 32) {
		t.Fatalf("replace: %q", got)
	}
	// 32 // 16 = 2, then (32 + 32 * 1) // 16 = 4
	if f.Left != 94 {
		t.Fatalf("fuel left %d", f.Left)
	}
	f = NewFuel(100)
	SizedReplace(f, s, "a", "", 2)
	if f.Left != 98 {
		t.Fatalf("fuel left %d (a shrinking replace is charged its scan only)", f.Left)
	}
}

// T2Fuel.join: the items (a sized argument up front), then the result's size.
func TestJoinCharges(t *testing.T) {
	f := NewFuel(100)
	items := []string{strings.Repeat("a", 20), strings.Repeat("b", 20)}
	if got := JoinStr(f, ", ", IterSlice(items)); Len(got) != 42 {
		t.Fatalf("join: %q", got)
	}
	// 42 // 16 = 2 (the items are charged by the cursor the emitter passes)
	if f.Left != 98 {
		t.Fatalf("fuel left %d", f.Left)
	}
}

func TestWidthCharges(t *testing.T) {
	f := NewFuel(100)
	if Width(f, 40) != 40 || f.Left != 98 {
		t.Fatalf("width: left %d", f.Left)
	}
	if Width(f, -5) != -5 || f.Left != 98 {
		t.Fatalf("a negative width is not charged: left %d", f.Left)
	}
}
