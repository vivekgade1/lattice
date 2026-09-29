package rt

import (
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"regexp"
	"strconv"
	"testing"
)

// The answers of the Python prelude (testdata/gen_prelude_answers.py).
type tagged struct {
	T string          `json:"t"`
	V json.RawMessage `json:"v"`
}

type answers struct {
	ErrorBodies []struct {
		Style  string          `json:"style"`
		Status int             `json:"status"`
		Code   string          `json:"code"`
		Detail json.RawMessage `json:"detail"`
		Body   string          `json:"body"`
	} `json:"error_bodies"`
	Params []struct {
		Name  string            `json:"name"`
		Specs []map[string]any  `json:"specs"`
		Path  map[string]string `json:"path"`
		Query string            `json:"query"`
		Body  string            `json:"body"`
		Form  bool              `json:"form"`
		CType *string           `json:"ctype"`
		Vals  map[string]tagged `json:"values"`
		Stat  int               `json:"status"`
		Lat   string            `json:"lattice"`
		Fast  string            `json:"fastapi"`
	} `json:"params"`
	Tokens []struct {
		Header *string `json:"header"`
		Token  *string `json:"token"`
	} `json:"tokens"`
	Datetimes []struct {
		In  string  `json:"in"`
		Out *string `json:"out"`
	} `json:"datetimes"`
	Dates []struct {
		In  string  `json:"in"`
		Out *string `json:"out"`
	} `json:"dates"`
	UUIDs []struct {
		In  string  `json:"in"`
		Out *string `json:"out"`
	} `json:"uuids"`
	SQLFns []struct {
		Storage []string        `json:"storage"`
		SQL     string          `json:"sql"`
		Row     json.RawMessage `json:"row"`
	} `json:"sqlfns"`
	Wire []struct {
		Style  string `json:"style"`
		Scalar string `json:"scalar"`
		In     tagged `json:"in"`
		Out    tagged `json:"out"`
	} `json:"wire"`
	Renders []struct {
		Label string          `json:"label"`
		Value json.RawMessage `json:"value"`
		Body  string          `json:"body"`
	} `json:"renders"`
	RowsJSON []struct {
		Cols []string   `json:"cols"`
		Rows [][]tagged `json:"rows"`
		Pick []string   `json:"pick"`
		Out  string     `json:"out"`
	} `json:"rows_json"`
	SQLErrors []struct {
		SQL    string `json:"sql"`
		Error  string `json:"error"`
		Unique bool   `json:"unique"`
	} `json:"sql_errors"`
	Spellings []struct {
		In       string   `json:"in"`
		Row      []tagged `json:"row"`
		Wire     []tagged `json:"wire"`
		RowsJSON string   `json:"rows_json"`
	} `json:"spellings"`
	Outbox struct {
		Constants map[string]string `json:"constants"`
		Cutoff    string            `json:"cutoff"`
		Calls     []string          `json:"calls"`
		Steps     []struct {
			Label   string     `json:"label"`
			Now     string     `json:"now"`
			Rows    [][]tagged `json:"rows"`
			Queries int        `json:"queries"`
			Done    int        `json:"done"`
			Failed  int        `json:"failed"`
			After   [][]tagged `json:"after"`
		} `json:"steps"`
	} `json:"outbox"`
	Phrases  map[string]string `json:"phrases"`
	Internal []struct {
		Detail string `json:"detail"`
		Body   string `json:"body"`
	} `json:"internal"`
}

func loadAnswers(t *testing.T) *answers {
	t.Helper()
	data, err := os.ReadFile("testdata/prelude_answers.json")
	if err != nil {
		t.Fatal(err)
	}
	var a answers
	if err := json.Unmarshal(data, &a); err != nil {
		t.Fatal(err)
	}
	if len(a.ErrorBodies) < 20 || len(a.Params) < 150 || len(a.Tokens) < 10 || len(a.Datetimes) < 50 ||
		len(a.Dates) < 20 || len(a.UUIDs) < 5 || len(a.SQLFns) < 50 || len(a.Wire) < 30 ||
		len(a.Renders) < 5 || len(a.RowsJSON) < 3 || len(a.Phrases) < 50 || len(a.Internal) < 2 ||
		len(a.Spellings) < 10 || len(a.Outbox.Steps) != 3 {
		t.Fatalf("testdata/prelude_answers.json is incomplete")
	}
	return &a
}

func (tg tagged) str() string {
	var s string
	_ = json.Unmarshal(tg.V, &s)
	return s
}

// value is the Go value of a tagged Python value.
func (tg tagged) value() any {
	switch tg.T {
	case "none":
		return nil
	case "unset":
		return Unset
	case "bool":
		var b bool
		_ = json.Unmarshal(tg.V, &b)
		return b
	case "int":
		n, _ := strconv.ParseInt(tg.str(), 10, 64)
		return n
	case "float":
		s := tg.str()
		switch s {
		case "nan":
			return math.NaN()
		case "inf":
			return math.Inf(1)
		case "-inf":
			return math.Inf(-1)
		}
		f, _ := strconv.ParseFloat(s, 64)
		return f
	case "date":
		d, _ := ParseISODate(tg.str())
		return d
	case "datetime":
		t, _ := ParseDateTime(tg.str())
		return t
	case "uuid":
		return UUID(tg.str())
	case "bytes":
		b, _ := hex.DecodeString(tg.str())
		return b
	}
	return tg.str()
}

// tagOf is the tagged spelling of a Go value (as the generator script writes it).
func tagOf(v any) string {
	switch x := v.(type) {
	case nil:
		return "none"
	case unsetT:
		return "unset"
	case bool:
		return fmt.Sprintf("bool:%v", x)
	case int64:
		return "int:" + strconv.FormatInt(x, 10)
	case float64:
		if math.IsNaN(x) {
			return "float:nan"
		}
		if math.IsInf(x, 0) {
			return "float:" + pyFloatName(x)
		}
		return "float:" + ReprFloat(x)
	case string:
		return "str:" + x
	case Date:
		return "date:" + x.ISO()
	case DateTime:
		return "datetime:" + x.Text()
	case UUID:
		return "uuid:" + string(x)
	case []byte:
		return "bytes:" + hex.EncodeToString(x)
	}
	return fmt.Sprintf("?%T", v)
}

func (tg tagged) spelled() string {
	switch tg.T {
	case "none", "unset":
		return tg.T
	case "bool":
		return fmt.Sprintf("bool:%v", tg.value())
	}
	return tg.T + ":" + tg.str()
}

func TestErrorBodies(t *testing.T) {
	for _, c := range loadAnswers(t).ErrorBodies {
		got, err := WriteJSON(ErrorBody(c.Style, c.Status, c.Code, decodeOrdered(t, c.Detail)))
		if err != nil || string(got) != c.Body {
			t.Errorf("%s %d %s %v: got %s (%v), want %s", c.Style, c.Status, c.Code, string(c.Detail), got, err, c.Body)
		}
	}
}

func specOf(t *testing.T, m map[string]any) ParamSpec {
	t.Helper()
	s := ParamSpec{Name: m["name"].(string), Source: m["source"].(string), Scalar: m["scalar"].(string)}
	if v, ok := m["ge"].(float64); ok {
		s.Ge = F(v)
	}
	if v, ok := m["le"].(float64); ok {
		s.Le = F(v)
	}
	if v, ok := m["min_len"].(float64); ok {
		s.MinLen = I(int64(v))
	}
	if v, ok := m["max_len"].(float64); ok {
		s.MaxLen = I(int64(v))
	}
	if v, ok := m["one_of"].([]any); ok {
		s.OneOf = []string{}
		for _, o := range v {
			s.OneOf = append(s.OneOf, o.(string))
		}
	}
	if v, ok := m["pattern"].(string); ok {
		pc := m["pattern_code"].(map[string]any)
		var code []uint32
		for _, x := range pc["code"].([]any) {
			code = append(code, uint32(x.(float64)))
		}
		names := map[string]int{}
		for k, x := range pc["names"].(map[string]any) {
			names[k] = int(x.(float64))
		}
		s.Pattern = NewRegex(v, code, int(pc["groups"].(float64)), names)
	}
	s.Email, _ = m["email"].(bool)
	s.Optional, _ = m["optional"].(bool)
	s.Nullable, _ = m["nullable"].(bool)
	if d, ok := m["default"].(map[string]any); ok {
		raw, _ := json.Marshal(d["v"])
		tg := tagged{T: d["t"].(string), V: raw}
		switch tg.T {
		case "none":
			s.Default = Null
		case "str":
			s.Default = tg.str()
		default:
			s.Default = tg.value()
		}
	}
	return s
}

func TestParseParams(t *testing.T) {
	for _, c := range loadAnswers(t).Params {
		specs := make([]ParamSpec, len(c.Specs))
		for i, m := range c.Specs {
			specs[i] = specOf(t, m)
		}
		body, _ := base64.StdEncoding.DecodeString(c.Body)
		ct := ""
		if c.CType != nil {
			ct = *c.CType
		}
		qb := make([]byte, 0, len(c.Query)) // the generator's query text is latin-1: back to bytes
		for _, r := range c.Query {
			qb = append(qb, byte(r))
		}
		values, perr := ParseParams(specs, c.Path, ParseQS(string(qb)), body, c.Form, ct)
		if c.Vals != nil {
			if perr != nil {
				b, _ := WriteJSON(ErrorBody("lattice", perr.Status, perr.Code, perr.Detail))
				t.Errorf("%s: got error %s, want values %v", c.Name, b, c.Vals)
				continue
			}
			if len(values) != len(c.Vals) {
				t.Errorf("%s: got %d values, want %d", c.Name, len(values), len(c.Vals))
			}
			for k, want := range c.Vals {
				if got := tagOf(values[k]); got != want.spelled() {
					t.Errorf("%s: %s = %q, want %q", c.Name, k, got, want.spelled())
				}
			}
			continue
		}
		if perr == nil {
			t.Errorf("%s: got values %v, want %s", c.Name, values, c.Lat)
			continue
		}
		for _, st := range []struct{ style, want string }{{"lattice", c.Lat}, {"fastapi", c.Fast}} {
			b, _ := WriteJSON(ErrorBody(st.style, perr.Status, perr.Code, perr.Detail))
			if string(b) != st.want || perr.Status != c.Stat {
				t.Errorf("%s (%s): got %d %s, want %d %s", c.Name, st.style, perr.Status, b, c.Stat, st.want)
			}
		}
	}
}

func TestTokens(t *testing.T) {
	for _, c := range loadAnswers(t).Tokens {
		h := ""
		if c.Header != nil {
			h = *c.Header
		}
		got, ok := Token(h, c.Header != nil)
		if ok != (c.Token != nil) || (ok && got != *c.Token) {
			t.Errorf("Token(%q) = %q, %v; want %v", h, got, ok, c.Token)
		}
	}
}

func TestParseDateTimeAndDate(t *testing.T) {
	a := loadAnswers(t)
	for _, c := range a.Datetimes {
		got, ok := ParseDateTime(c.In)
		if ok != (c.Out != nil) || (ok && got.Text() != *c.Out) {
			t.Errorf("parse_datetime(%q) = %s, %v; want %v", c.In, got.Text(), ok, strp(c.Out))
		}
	}
	for _, c := range a.Dates {
		got, ok := ParseISODate(c.In)
		if ok != (c.Out != nil) || (ok && got.ISO() != *c.Out) {
			t.Errorf("date.fromisoformat(%q) = %s, %v; want %v", c.In, got.ISO(), ok, strp(c.Out))
		}
	}
	for _, c := range a.UUIDs {
		got, ok := UUIDText(c.In)
		if ok != (c.Out != nil) || (ok && got != *c.Out) {
			t.Errorf("uuid_text(%q) = %q, %v; want %v", c.In, got, ok, strp(c.Out))
		}
	}
}

func strp(s *string) string {
	if s == nil {
		return "None"
	}
	return *s
}

func TestSQLFunctions(t *testing.T) {
	RegisterFunctions()
	db, err := sql.Open("sqlite", "file::memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	saved := Storage
	defer func() { Storage = saved }()
	for _, c := range loadAnswers(t).SQLFns {
		Storage.UUID, Storage.DateTime = c.Storage[0], c.Storage[1]
		var want []tagged
		var wantErr string
		if err := json.Unmarshal(c.Row, &want); err != nil {
			_ = json.Unmarshal(c.Row, &wantErr)
		}
		conn, _ := db.Conn(context.Background())
		var res Result
		gotErr := ""
		func() {
			defer func() {
				if r := recover(); r != nil {
					gotErr = "!" + r.(*SQLError).Error()
				}
			}()
			res = (&dbConn{conn: conn}).query(c.SQL)
		}()
		_ = conn.Close()
		if wantErr != "" || gotErr != "" {
			if gotErr != wantErr {
				t.Errorf("%v %s: got error %q, want %q", c.Storage, c.SQL, gotErr, wantErr)
			}
			continue
		}
		for i, w := range want {
			if got := tagOf(res.Rows[0][i]); got != w.spelled() {
				t.Errorf("%v %s [%d]: got %q, want %q", c.Storage, c.SQL, i, got, w.spelled())
			}
		}
	}
}

func TestSQLErrors(t *testing.T) {
	RegisterFunctions()
	db, err := sql.Open("sqlite", "file::memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	conn, _ := db.Conn(context.Background())
	defer conn.Close()
	c := &dbConn{conn: conn}
	c.exec("CREATE TABLE t (x NOT NULL, u UNIQUE)")
	for _, e := range loadAnswers(t).SQLErrors {
		var got any
		func() {
			defer func() { got = recover() }()
			c.query(e.SQL)
		}()
		se, ok := got.(*SQLError)
		if !ok || se.Error() != e.Error || IsUniqueViolation(se) != e.Unique {
			t.Errorf("%s: got %v, want %q (unique %v)", e.SQL, got, e.Error, e.Unique)
		}
	}
}

func TestUUID4(t *testing.T) {
	re := regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
	for range 100 {
		if u := NewUUID4(); !re.MatchString(u) {
			t.Fatalf("NewUUID4() = %q", u)
		}
	}
}

func TestWireValues(t *testing.T) {
	saved := Storage
	defer func() { Storage = saved }()
	for _, c := range loadAnswers(t).Wire {
		Storage.Wire = c.Style
		in := c.In.value()
		if s, ok := in.(UUID); ok { // a stored value is plain text
			in = string(s)
		}
		got := tagOf(Finite(Decode(in, c.Scalar)))
		if got != c.Out.spelled() {
			t.Errorf("%s %s %s: got %q, want %q", c.Style, c.Scalar, c.In.spelled(), got, c.Out.spelled())
		}
	}
}

// decodeOrdered decodes JSON keeping object member order (as Obj).
func decodeOrdered(t *testing.T, raw []byte) any {
	t.Helper()
	v, err := LoadsJSON(raw)
	if err != nil {
		t.Fatal(err)
	}
	var conv func(v any) any
	conv = func(v any) any {
		switch x := v.(type) {
		case *JSONObj:
			o := Obj{}
			for _, k := range x.Keys {
				o = append(o, KV{k, conv(x.Vals[k])})
			}
			return o
		case []any:
			out := make([]any, len(x))
			for i := range x {
				out[i] = conv(x[i])
			}
			return out
		case JSONStr:
			return x.S
		}
		return v
	}
	return conv(v)
}

func TestWriteJSON(t *testing.T) {
	for _, c := range loadAnswers(t).Renders {
		got, err := WriteJSON(decodeOrdered(t, c.Value))
		if err != nil || string(got) != c.Body {
			t.Errorf("%s: got %s (%v), want %s", c.Label, got, err, c.Body)
		}
	}
	if _, err := WriteJSON([]any{math.NaN()}); err == nil {
		t.Errorf("WriteJSON(NaN) must fail (allow_nan=False)")
	}
}

func TestRowsJSON(t *testing.T) {
	for _, c := range loadAnswers(t).RowsJSON {
		r := Result{Cols: c.Cols, Rows: [][]any{}}
		for _, row := range c.Rows {
			cells := make([]any, len(row))
			for i, tg := range row {
				cells[i] = tg.value()
			}
			r.Rows = append(r.Rows, cells)
		}
		if got := RowsJSON(r, c.Pick); got != c.Out {
			t.Errorf("rows_json: got %s, want %s", got, c.Out)
		}
	}
}

func TestStatusPhrasesAndInternal(t *testing.T) {
	a := loadAnswers(t)
	for code, phrase := range a.Phrases {
		n, _ := strconv.Atoi(code)
		if got := StatusPhrase(n); got != phrase {
			t.Errorf("phrase %d = %q, want %q", n, got, phrase)
		}
	}
	app := &App{}
	for _, c := range a.Internal {
		if got := app.internal(c.Detail); string(got.body) != c.Body {
			t.Errorf("internal: got %s, want %s", got.body, c.Body)
		}
	}
}
