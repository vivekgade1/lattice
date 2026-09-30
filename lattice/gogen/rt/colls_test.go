package rt

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

type dictScenario struct {
	Make   []int     `json:"-"`
	Kind   string    `json:"-"`
	Raw    []any     `json:"make"`
	Pre    [][]any   `json:"pre"`
	Body   [][][]any `json:"body"`
	Answer struct {
		Yielded []int   `json:"yielded"`
		Error   *string `json:"error"`
		After   []int   `json:"after"`
	} `json:"answer"`
}

func jnum(v any) int { return int(v.(float64)) }

// litChunks is CPython's codegen_dict for n plain entries: the BUILD_MAP chunks
// (size, big) -- a big chunk is BUILD_MAP 0 then MAP_ADD per entry; the first chunk is
// the dict, each later one is merged into it (DICT_UPDATE).
func litChunks(n int) (sizes []int, big []bool) {
	elements := 0
	flush := func(k int) {
		sizes = append(sizes, k)
		big = append(big, k*2 > 30)
	}
	for i := 0; i < n; i++ {
		if elements*2 > 30 {
			flush(elements + 1)
			elements = 0
		} else {
			elements++
		}
	}
	if elements > 0 {
		flush(elements)
	}
	return sizes, big
}

func testLiteral(keys []int) *Dict[int, int] {
	sizes, big := litChunks(len(keys))
	var d *Dict[int, int]
	at := 0
	for c, n := range sizes {
		var chunk *Dict[int, int]
		if big[c] {
			chunk = NewDict[int, int]()
		} else {
			chunk = NewDictSized[int, int](n)
		}
		for _, k := range keys[at : at+n] {
			chunk.Set(k, 0)
		}
		at += n
		if d == nil {
			d = chunk
		} else {
			d.Merge(chunk)
		}
	}
	if d == nil {
		d = NewDict[int, int]()
	}
	return d
}

func testMake(raw []any) *Dict[int, int] {
	if raw[0] == "empty" {
		return NewDict[int, int]()
	}
	n := jnum(raw[1])
	keys := make([]int, n)
	for i := range keys {
		keys[i] = i
	}
	switch raw[0] {
	case "lit":
		return testLiteral(keys)
	case "comp":
		d := NewDict[int, int]()
		for _, k := range keys {
			d.Set(k, 0)
		}
		return d
	case "set", "fromsetlit":
		s := NewSet(keys...)
		return setAsDict(s)
	case "setcopy":
		return setAsDict(NewSet(keys...).Copy())
	}
	panic(raw[0])
}

// setAsDict views a set's dict as a Dict[int, int] (the layout is what is compared).
func setAsDict(s *Set[int]) *Dict[int, int] {
	d := &Dict[int, int]{index: s.d.index, log2: s.d.log2, usable: s.d.usable}
	for _, e := range s.d.entries {
		d.entries = append(d.entries, entry[int, int]{key: e.key, live: e.live})
	}
	return d
}

func testApply(d *Dict[int, int], op []any) *Dict[int, int] {
	switch op[0] {
	case "add":
		d.Set(jnum(op[1]), 0)
	case "del":
		d.Delete(jnum(op[1]))
	case "copy":
		return d.Copy()
	case "clear":
		d.Clear()
	case "merge", "mergelit":
		n, base := jnum(op[1]), jnum(op[2])
		keys := make([]int, n)
		for i := range keys {
			keys[i] = base + i
		}
		var o *Dict[int, int]
		if op[0] == "merge" {
			o = NewDict[int, int]()
			for _, k := range keys {
				o.Set(k, 0)
			}
		} else {
			o = testLiteral(keys)
		}
		d.Update(o)
	}
	return d
}

func runScenario(sc dictScenario) (yielded []int, errMsg *string, after []int) {
	d := testMake(sc.Raw)
	for _, op := range sc.Pre {
		d = testApply(d, op)
	}
	yielded = []int{}
	func() {
		defer func() {
			if r := recover(); r != nil {
				e := r.(*Error)
				msg := e.Msg
				errMsg = &msg
			}
		}()
		next := IterDict(d)
		for i := 0; ; i++ {
			k, ok := next()
			if !ok {
				return
			}
			yielded = append(yielded, k)
			if i >= 200 {
				return
			}
			if i < len(sc.Body) {
				for _, op := range sc.Body[i] {
					testApply(d, op)
				}
			}
		}
	}()
	return yielded, errMsg, d.Keys()
}

// TestDictLayoutAnswers: a dict (or set) changed while iterated yields the same keys
// and raises at the same point as CPython 3.14 (testdata/gen_dict_answers.py).
func TestDictLayoutAnswers(t *testing.T) {
	data, err := os.ReadFile("testdata/dict_answers.json")
	if err != nil {
		t.Fatal(err)
	}
	var scs []dictScenario
	if err := json.Unmarshal(data, &scs); err != nil {
		t.Fatal(err)
	}
	if len(scs) < 3000 {
		t.Fatal("testdata/dict_answers.json is incomplete")
	}
	bad := 0
	for i, sc := range scs {
		y, e, a := runScenario(sc)
		if !reflect.DeepEqual(y, sc.Answer.Yielded) || !reflect.DeepEqual(e, sc.Answer.Error) ||
			!reflect.DeepEqual(a, sc.Answer.After) {
			bad++
			if bad <= 5 {
				t.Errorf("scenario %d %v pre=%v:\n got  %v %v %v\n want %v %v %v", i, sc.Raw, sc.Pre,
					y, deref(e), a, sc.Answer.Yielded, deref(sc.Answer.Error), sc.Answer.After)
			}
		}
	}
	if bad > 0 {
		t.Fatalf("%d of %d scenarios differ", bad, len(scs))
	}
}

func deref(s *string) string {
	if s == nil {
		return "<nil>"
	}
	return *s
}
