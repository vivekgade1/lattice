package rt

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
)

// The answers of Python 3.14 (testdata/gen_t2text_answers.py) for regular expressions
// and the Str methods.
type textAnswers struct {
	SRE struct {
		Opcodes   map[string]uint32 `json:"opcodes"`
		Atcodes   map[string]uint32 `json:"atcodes"`
		Chcodes   map[string]uint32 `json:"chcodes"`
		MaxRepeat uint32            `json:"maxrepeat"`
	} `json:"sre"`
	Patterns map[string]struct {
		Code   []uint32       `json:"code"`
		Groups int            `json:"groups"`
		Names  map[string]int `json:"names"`
	} `json:"patterns"`
	Regex []struct {
		Pattern   string          `json:"pattern"`
		S         string          `json:"s"`
		Search    [][]int         `json:"search"`
		Match     [][]int         `json:"match"`
		Fullmatch [][]int         `json:"fullmatch"`
		Finditer  [][][]int       `json:"finditer"`
		Findall   json.RawMessage `json:"findall"`
		Split     json.RawMessage `json:"split"`
		Sub       []subCase       `json:"sub"`
		SubCount  int             `json:"sub_count"`
		SubLong   int             `json:"sub_longest"`
	} `json:"regex"`
	Str []struct {
		Fn   string            `json:"fn"`
		Args []json.RawMessage `json:"args"`
		Out  outcomeJSON       `json:"out"`
	} `json:"str"`
}

type subCase struct {
	Repl      string      `json:"repl"`
	Out       outcomeJSON `json:"out"`
	TmplError bool        `json:"tmpl_error"`
}

type outcomeJSON struct {
	V   json.RawMessage `json:"v"`
	Exc *string         `json:"exc"`
}

var (
	textOnce sync.Once
	textData textAnswers
)

func loadText(t *testing.T) *textAnswers {
	textOnce.Do(func() {
		data, err := os.ReadFile("testdata/t2text_answers.json")
		if err != nil {
			panic(err)
		}
		if err := json.Unmarshal(data, &textData); err != nil {
			panic(err)
		}
	})
	if len(textData.Regex) < 2000 || len(textData.Str) < 5000 {
		t.Fatal("testdata/t2text_answers.json is incomplete")
	}
	return &textData
}

// compiled is the pattern as the Go emitter embeds it (lattice.gogen.regex).
func compiled(t *testing.T, pattern string) *Regex {
	p, ok := loadText(t).Patterns[pattern]
	if !ok {
		t.Fatalf("pattern %q is not in the answers", pattern)
	}
	return NewRegex(pattern, p.Code, p.Groups, p.Names)
}

// caught runs f and returns "Kind: message" for a raised Python exception.
func caught(f func()) (exc string) {
	defer func() {
		if r := recover(); r != nil {
			e, ok := r.(*Error)
			if !ok {
				panic(r)
			}
			if e.Code != "" {
				exc = e.Code + ": " + e.Msg
			} else {
				exc = e.Kind + ": " + e.Msg
			}
		}
	}()
	f()
	return ""
}

func spansOf(m Opt[*Match]) [][]int {
	if !m.Ok {
		return nil
	}
	out := [][]int{}
	for g := 0; g <= m.V.re.Groups; g++ {
		a, b := m.V.span(g)
		if a < 0 {
			out = append(out, nil)
		} else {
			out = append(out, []int{a, b})
		}
	}
	return out
}

func TestSreConstantsArePythons(t *testing.T) {
	a := loadText(t)
	want := map[string]uint32{
		"FAILURE": opFAILURE, "SUCCESS": opSUCCESS, "ANY": opANY, "ANY_ALL": opANY_ALL,
		"AT": opAT, "BRANCH": opBRANCH, "CATEGORY": opCATEGORY, "CHARSET": opCHARSET,
		"BIGCHARSET": opBIGCHARSET, "IN": opIN, "INFO": opINFO, "JUMP": opJUMP,
		"LITERAL": opLITERAL, "MARK": opMARK, "MAX_UNTIL": opMAX_UNTIL,
		"MIN_UNTIL": opMIN_UNTIL, "NOT_LITERAL": opNOT_LITERAL, "NEGATE": opNEGATE,
		"RANGE": opRANGE, "REPEAT": opREPEAT, "REPEAT_ONE": opREPEAT_ONE,
		"MIN_REPEAT_ONE": opMIN_REPEAT_ONE, "IN_IGNORE": opIN_IGNORE,
		"LITERAL_IGNORE": opLITERAL_IGNORE, "NOT_LITERAL_IGNORE": opNOT_LITERAL_IGNORE,
	}
	for name, v := range want {
		if a.SRE.Opcodes[name] != v {
			t.Errorf("opcode %s: Python %d, Go %d", name, a.SRE.Opcodes[name], v)
		}
	}
	at := map[string]uint32{
		"AT_BEGINNING": atBEGINNING, "AT_BEGINNING_LINE": atBEGINNING_LINE,
		"AT_BEGINNING_STRING": atBEGINNING_STRING, "AT_BOUNDARY": atBOUNDARY,
		"AT_NON_BOUNDARY": atNON_BOUNDARY, "AT_END": atEND, "AT_END_LINE": atEND_LINE,
		"AT_END_STRING": atEND_STRING,
	}
	for name, v := range at {
		if a.SRE.Atcodes[name] != v {
			t.Errorf("at code %s: Python %d, Go %d", name, a.SRE.Atcodes[name], v)
		}
	}
	ch := map[string]uint32{
		"CATEGORY_DIGIT": catDIGIT, "CATEGORY_NOT_DIGIT": catNOT_DIGIT,
		"CATEGORY_SPACE": catSPACE, "CATEGORY_NOT_SPACE": catNOT_SPACE,
		"CATEGORY_WORD": catWORD, "CATEGORY_NOT_WORD": catNOT_WORD,
		"CATEGORY_LINEBREAK": catLINEBREAK, "CATEGORY_NOT_LINEBREAK": catNOT_LINEBREAK,
	}
	for name, v := range ch {
		if a.SRE.Chcodes[name] != v {
			t.Errorf("category %s: Python %d, Go %d", name, a.SRE.Chcodes[name], v)
		}
	}
	if a.SRE.MaxRepeat != reMaxRepeat {
		t.Errorf("MAXREPEAT: Python %d, Go %d", a.SRE.MaxRepeat, reMaxRepeat)
	}
}

// The brief's cases, spelled out.
func TestRegexNamedGroup(t *testing.T) {
	m := ReFullmatch(compiled(t, `(?P<word>[a-z]+)`), "hello")
	if !m.Ok || m.V.GroupName("word") != Some("hello") || m.V.Group0() != "hello" {
		t.Fatalf("named group: %+v", m)
	}
	if got := caught(func() { m.V.GroupName("nope") }); got != "IndexError: no such group" {
		t.Fatalf("unknown name: %q", got)
	}
	if got := caught(func() { m.V.Group(2) }); got != "IndexError: no such group" {
		t.Fatalf("bad index: %q", got)
	}
	if got := caught(func() { m.V.Group(-1) }); got != "IndexError: no such group" {
		t.Fatalf("negative index: %q", got)
	}
}

func TestRegexUnmatchedOptionalGroupIsNone(t *testing.T) {
	m := ReSearch(compiled(t, `(a)(b)?`), "xa")
	if !m.Ok || m.V.Group(1) != Some("a") || m.V.Group(2).Ok {
		t.Fatalf("optional group: %+v", spansOf(m))
	}
}

func TestRegexSubBackrefAndDollar(t *testing.T) {
	fuel := NewFuel(1000)
	tm := &Template{Repl: `\2@\1`, Lits: []string{"", "@", ""}, Groups: []int{2, 1}}
	if got := ReSub(fuel, compiled(t, `(\w+)@(\w+)`), tm, "alice@example"); got != "example@alice" {
		t.Fatalf("\\1 replacement: %q", got)
	}
	lit := &Template{Repl: "$1", Lits: []string{"$1"}}
	if got := ReSub(fuel, compiled(t, `x`), lit, "x"); got != "$1" {
		t.Fatalf("$ in the replacement: %q", got)
	}
}

func TestRegexFindallOneGroup(t *testing.T) {
	got := ReFindall(compiled(t, `([a-z]+)\d+`), "a1 bb22 ccc333").Items
	if !reflect.DeepEqual(got, []string{"a", "bb", "ccc"}) {
		t.Fatalf("findall: %q", got)
	}
}

func TestRegexStartIsCodePoints(t *testing.T) {
	m := ReSearch(compiled(t, `wor`), "héllo world")
	if !m.Ok || m.V.Start() != 6 || m.V.End() != 9 {
		t.Fatalf("start/end: %+v", spansOf(m))
	}
	m = ReSearch(compiled(t, `(?P<word>[a-z]+)`), "é llo")
	if !m.Ok || m.V.Start() != 2 {
		t.Fatalf("start after é: %+v", spansOf(m))
	}
}

func TestRegexSplitKeepsEmptyStrings(t *testing.T) {
	got := ReSplit(compiled(t, `,`), "a,,b").Items
	if !reflect.DeepEqual(got, []string{"a", "", "b"}) {
		t.Fatalf("split: %q", got)
	}
}

func TestRegexMatchRepr(t *testing.T) {
	m := ReSearch(compiled(t, `wor`), "héllo world")
	if got := m.V.PyRepr(); got != "<re.Match object; span=(6, 9), match='wor'>" {
		t.Fatalf("repr: %s", got)
	}
}

func TestRegexInvalidPatternRaisesOnEveryUse(t *testing.T) {
	re := BadRegex("(?u)a", "ValueError", "ASCII and UNICODE flags are incompatible")
	if got := caught(func() { ReSearch(re, "a") }); got != "ValueError: ASCII and UNICODE flags are incompatible" {
		t.Fatalf("bad pattern: %q", got)
	}
}

// Every answer of Python: search, match, fullmatch, finditer, findall, split and sub,
// over the fixed patterns and random ones.
func TestRegexAnswers(t *testing.T) {
	a := loadText(t)
	for _, c := range a.Regex {
		re := compiled(t, c.Pattern)
		where := fmt.Sprintf("%q on %q", c.Pattern, c.S)
		if got := spansOf(ReSearch(re, c.S)); !reflect.DeepEqual(got, c.Search) {
			t.Errorf("search %s: Go %v, Python %v", where, got, c.Search)
		}
		if got := spansOf(ReMatch(re, c.S)); !reflect.DeepEqual(got, c.Match) {
			t.Errorf("match %s: Go %v, Python %v", where, got, c.Match)
		}
		if got := spansOf(ReFullmatch(re, c.S)); !reflect.DeepEqual(got, c.Fullmatch) {
			t.Errorf("fullmatch %s: Go %v, Python %v", where, got, c.Fullmatch)
		}
		got := [][][]int{}
		longest := 0
		for _, m := range re.finditer(c.S) {
			got = append(got, spansOf(Some(m)))
			if w := int(m.End() - m.Start()); w > longest {
				longest = w
			}
		}
		if !reflect.DeepEqual(got, c.Finditer) {
			t.Errorf("finditer %s: Go %v, Python %v", where, got, c.Finditer)
		}
		if len(got) != c.SubCount || longest != c.SubLong {
			t.Errorf("sub's count/longest %s: Go %d %d, Python %d %d", where, len(got), longest, c.SubCount, c.SubLong)
		}
		var findall any
		if re.Groups > 1 {
			findall = ReFindallGroups(re, c.S)
		} else {
			findall = ReFindall(re, c.S).Items
		}
		if got, want := asJSON(findall), asJSON(c.Findall); !reflect.DeepEqual(got, want) {
			t.Errorf("findall %s: Go %v, Python %v", where, got, want)
		}
		var split any
		if re.Groups > 0 {
			split = optItems(ReSplitGroups(re, c.S).Items)
		} else {
			split = ReSplit(re, c.S).Items
		}
		if got, want := asJSON(split), asJSON(c.Split); !reflect.DeepEqual(got, want) {
			t.Errorf("split %s: Go %v, Python %v", where, got, want)
		}
	}
}

func TestRegexSubAnswers(t *testing.T) {
	a := loadText(t)
	n := 0
	for _, c := range a.Regex {
		re := compiled(t, c.Pattern)
		for _, sc := range c.Sub {
			tm := testTemplate(t, re, sc.Repl)
			var out string
			exc := caught(func() { out = ReSub(NewFuel(1<<40), re, tm, c.S) })
			if sc.Out.Exc != nil {
				if exc != *sc.Out.Exc {
					t.Errorf("sub %q %q on %q: Go %q, Python %q", c.Pattern, sc.Repl, c.S, exc, *sc.Out.Exc)
				}
				continue
			}
			var want string
			_ = json.Unmarshal(sc.Out.V, &want)
			if exc != "" || out != want {
				t.Errorf("sub %q %q on %q: Go %q %q, Python %q", c.Pattern, sc.Repl, c.S, out, exc, want)
			}
			n++
		}
	}
	if n < 1000 {
		t.Fatalf("only %d sub answers", n)
	}
}

// testTemplate compiles a replacement the way lattice.gogen.regex.compile_template does
// for the few the answers use (the emitter's compiler is tested on the Python side).
func testTemplate(t *testing.T, re *Regex, repl string) *Template {
	switch repl {
	case "", "-", "$1":
		return &Template{Repl: repl, Lits: []string{repl}}
	case `<\g<0>>`:
		return &Template{Repl: repl, Lits: []string{"<", ">"}, Groups: []int{0}}
	case `\1`:
		if re.Groups < 1 {
			return &Template{Repl: repl, ErrKind: "PatternError", ErrMsg: "invalid group reference 1 at position 1"}
		}
		return &Template{Repl: repl, Lits: []string{"", ""}, Groups: []int{1}}
	case `[\\]`:
		return &Template{Repl: repl, Lits: []string{`[\]`}}
	case `\n`:
		return &Template{Repl: repl, Lits: []string{"\n"}}
	}
	t.Fatalf("no template for %q", repl)
	return nil
}

func TestRegexSubChargesPython(t *testing.T) {
	// T2Fuel.sub: the input's length, then len(s) + matches * len(repl) * max(longest, 1)
	fuel := NewFuel(100)
	re := compiled(t, `x`)
	s := ""
	for i := 0; i < 40; i++ {
		s += "x"
	}
	got := ReSub(fuel, re, &Template{Repl: "yyyy", Lits: []string{"yyyy"}}, s)
	if len(got) != 160 {
		t.Fatalf("sub result %d", len(got))
	}
	// 40 // 16 = 2, then (40 + 40 * 4 * 1) // 16 = 12
	if fuel.Left != 100-2-12 {
		t.Fatalf("fuel left %d", fuel.Left)
	}
}

// asJSON is v as encoding/json decodes it (a Go value, or raw JSON), for comparisons.
func asJSON(v any) any {
	raw, ok := v.(json.RawMessage)
	if !ok {
		raw, _ = json.Marshal(v)
	}
	var out any
	if err := json.Unmarshal(raw, &out); err != nil {
		panic(err)
	}
	return out
}

func optItems(xs []Opt[string]) []*string {
	out := make([]*string, len(xs))
	for i, x := range xs {
		if x.Ok {
			v := x.V
			out[i] = &v
		}
	}
	return out
}

// A repeat nests one engine context per iteration; the contexts live on the heap, as
// CPython's do, so millions of iterations match (the first version recursed on the Go
// stack and died with "stack overflow" at about a million).
func TestRegexDeepRepeats(t *testing.T) {
	const n = 10_000_000
	cases := []struct {
		pattern, unit string
		group1        Opt[string] // of the fullmatch
	}{
		{`(?:ab)*`, "ab", None[string]()},
		{`(a|b)*`, "ab", Some("b")},
		{`(?:(?:a|b)c)*`, "ac", None[string]()},
		{`((?:ab)+c)*`, "ababc", Some("ababc")}, // a repeat nested in a repeat
	}
	for _, c := range cases {
		s := strings.Repeat(c.unit, n/len(c.unit))
		re := compiled(t, c.pattern)
		m := ReFullmatch(re, s)
		if !m.Ok || m.V.Start() != 0 || m.V.End() != n {
			t.Fatalf("%s over %d characters: %v", c.pattern, n, spansOf(m))
		}
		if re.Groups > 0 && m.V.Group(1) != c.group1 {
			t.Fatalf("%s: group 1 differs", c.pattern)
		}
		// a failing end backtracks through every level
		if ReFullmatch(re, s+"!").Ok {
			t.Fatalf("%s matched a trailing '!'", c.pattern)
		}
		if got := ReSearch(re, s); !got.Ok || got.V.End() != n {
			t.Fatalf("%s search: %v", c.pattern, spansOf(got))
		}
	}
	lazy := compiled(t, `(?:ab)*?$`)
	if m := ReMatch(lazy, strings.Repeat("ab", n/2)); !m.Ok || m.V.End() != n {
		t.Fatalf("lazy repeat: %v", spansOf(m))
	}
}

// CPython's Match repr formats the match with %.50R: its repr cut to 50 code points.
func TestRegexMatchReprIsCutAt50(t *testing.T) {
	re := compiled(t, `(?s).+`)
	cases := map[string]string{
		strings.Repeat("x", 60): "<re.Match object; span=(0, 60), match='" + strings.Repeat("x", 49) + ">",
		strings.Repeat("x", 48): "<re.Match object; span=(0, 48), match='" + strings.Repeat("x", 48) + "'>",
		strings.Repeat("x", 49): "<re.Match object; span=(0, 49), match='" + strings.Repeat("x", 49) + ">",
		strings.Repeat("é", 55): "<re.Match object; span=(0, 55), match='" + strings.Repeat("é", 49) + ">",
		"it's\n" + strings.Repeat("y", 50): "<re.Match object; span=(0, 55), match=\"it's\\n" +
			strings.Repeat("y", 43) + ">",
	}
	for s, want := range cases {
		if got := ReSearch(re, s).V.PyRepr(); got != want {
			t.Errorf("repr of a %d-character match:\n  Go %s\nwant %s", Len(s), got, want)
		}
	}
}
