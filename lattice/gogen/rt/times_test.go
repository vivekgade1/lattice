package rt

import (
	"bytes"
	"encoding/json"
	"os"
	"strconv"
	"testing"
)

type timeAnswer struct {
	Op   string  `json:"op"`
	Args [][]any `json:"args"`
	Out  string  `json:"out"`
}

func tInt(a []any) int64 {
	v, err := strconv.ParseInt(a[1].(string), 10, 64)
	if err != nil {
		panic(err)
	}
	return v
}

func tFloat(a []any) float64 {
	v, err := strconv.ParseFloat(a[1].(string), 64)
	if err != nil {
		panic(err)
	}
	return v
}

func tDur(a []any) Duration {
	return Duration{jn(a[1]), jn(a[2]), jn(a[3])}
}

func jn(v any) int64 {
	n, err := v.(json.Number).Int64()
	if err != nil {
		panic(err)
	}
	return n
}

func tPart(a []any) *DurationPart {
	if a == nil {
		return nil
	}
	var p DurationPart
	if a[0] == "i" {
		p = DI(tInt(a))
	} else {
		p = DF(tFloat(a))
	}
	return &p
}

func renderTime(v any) string {
	switch x := v.(type) {
	case Duration:
		return "D:" + strconv.FormatInt(x.D, 10) + "," + strconv.FormatInt(x.S, 10) + "," + strconv.FormatInt(x.Us, 10)
	case Date:
		return "d:" + strconv.FormatInt(int64(x), 10)
	case DateTime:
		return "t:" + strconv.FormatInt(int64(x), 10)
	case int64:
		return strconv.FormatInt(x, 10)
	case float64:
		return ReprFloat(x)
	case bool:
		return StrBool(x)
	case string:
		return x
	}
	panic("renderTime")
}

// TestTimeAnswers: Date, DateTime and Duration answer as Python 3.14's datetime does
// (testdata/gen_time_answers.py).
func TestTimeAnswers(t *testing.T) {
	data, err := os.ReadFile("testdata/time_answers.json")
	if err != nil {
		t.Fatal(err)
	}
	var answers []timeAnswer
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber() // microseconds pass 2**53
	if err := dec.Decode(&answers); err != nil {
		t.Fatal(err)
	}
	if len(answers) < 9000 {
		t.Fatal("testdata/time_answers.json is incomplete")
	}
	bad := 0
	for _, a := range answers {
		args := a.Args
		d := func(i int) Duration { return tDur(args[i]) }
		date := func(i int) Date { return Date(jn(args[i][1])) }
		tm := func(i int) DateTime { return DateTime(jn(args[i][1])) }
		var f func() any
		switch a.Op {
		case "timedelta":
			f = func() any {
				return Timedelta(tPart(args[0]), tPart(args[1]), tPart(args[2]), tPart(args[3]), tPart(args[4]))
			}
		case "add":
			f = func() any { return d(0).Add(d(1)) }
		case "sub":
			f = func() any { return d(0).Sub(d(1)) }
		case "neg":
			f = func() any { return d(0).Neg() }
		case "mulint":
			f = func() any { return d(0).MulInt(tInt(args[1])) }
		case "mulfloat":
			f = func() any { return d(0).MulFloat(tFloat(args[1])) }
		case "divint":
			f = func() any { return d(0).DivInt(tInt(args[1])) }
		case "divfloat":
			f = func() any { return d(0).DivFloat(tFloat(args[1])) }
		case "div":
			f = func() any { return d(0).Div(d(1)) }
		case "floordiv":
			f = func() any { return d(0).FloorDiv(d(1)) }
		case "floordivint":
			f = func() any { return d(0).FloorDivInt(tInt(args[1])) }
		case "mod":
			f = func() any { return d(0).Mod(d(1)) }
		case "cmp":
			f = func() any { return int64(d(0).Cmp(d(1))) }
		case "total_seconds":
			f = func() any { return d(0).TotalSeconds() }
		case "str":
			f = func() any {
				switch args[0][0] {
				case "D":
					return d(0).Str()
				}
				return tm(0).Str()
			}
		case "repr":
			f = func() any {
				switch args[0][0] {
				case "D":
					return d(0).PyRepr()
				case "d":
					return date(0).PyRepr()
				}
				return tm(0).PyRepr()
			}
		case "bool":
			f = func() any { return d(0).Truth() }
		case "date":
			f = func() any {
				if args[0][0] == "t" {
					return tm(0).Date()
				}
				return NewDate(tInt(args[0]), tInt(args[1]), tInt(args[2]))
			}
		case "dadd":
			f = func() any { return date(0).AddDur(d(1)) }
		case "dsub":
			f = func() any { return date(0).SubDur(d(1)) }
		case "ddiff":
			f = func() any { return date(0).Sub(date(1)) }
		case "weekday":
			f = func() any {
				if args[0][0] == "t" {
					return tm(0).Weekday()
				}
				return date(0).Weekday()
			}
		case "isoweekday":
			f = func() any { return date(0).IsoWeekday() }
		case "iso":
			f = func() any {
				if args[0][0] == "t" {
					return tm(0).Isoformat()
				}
				return date(0).ISO()
			}
		case "strftime":
			f = func() any {
				if args[0][0] == "t" {
					return tm(0).Strftime(args[1][1].(string))
				}
				return date(0).Strftime(args[1][1].(string))
			}
		case "attrs":
			f = func() any {
				if args[0][0] == "t" {
					x := tm(0)
					return strconv.FormatInt(x.Year(), 10) + "," + strconv.FormatInt(x.Month(), 10) + "," +
						strconv.FormatInt(x.Day(), 10) + "," + strconv.FormatInt(x.Hour(), 10) + "," +
						strconv.FormatInt(x.Minute(), 10) + "," + strconv.FormatInt(x.Second(), 10) + "," +
						strconv.FormatInt(x.Microsecond(), 10)
				}
				x := date(0)
				return strconv.FormatInt(x.Year(), 10) + "," + strconv.FormatInt(x.Month(), 10) + "," +
					strconv.FormatInt(x.Day(), 10)
			}
		case "tadd":
			f = func() any { return tm(0).AddDur(d(1)) }
		case "tsub":
			f = func() any { return tm(0).SubDur(d(1)) }
		case "tdiff":
			f = func() any { return tm(0).Sub(tm(1)) }
		case "timestamp":
			f = func() any { return tm(0).Timestamp() }
		default:
			t.Fatalf("unknown op %s", a.Op)
		}
		got := mathOutcome(func() string { return renderTime(f()) })
		if got != a.Out {
			bad++
			if bad <= 10 {
				t.Errorf("%s%v = %s, want %s", a.Op, a.Args, got, a.Out)
			}
		}
	}
	if bad > 0 {
		t.Fatalf("%d of %d answers differ", bad, len(answers))
	}
}
