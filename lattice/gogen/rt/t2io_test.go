package rt

import (
	"net/http/httptest"
	"path/filepath"
	"testing"
)

// raised is the *Error a call panics with (nil: none).
func raised(f func()) (e *Error) {
	defer func() {
		if r := recover(); r != nil {
			e = r.(*Error)
		}
	}()
	f()
	return nil
}

// The answers below are lattice.t2.support.t2_decode / t2_result / sql_value's, as
// Python gives them.
func TestT2Decoders(t *testing.T) {
	if T2Int("12") != 12 || T2Int(1.9) != 1 || T2Int(true) != 1 || T2Int(int64(-3)) != -3 {
		t.Error("T2Int")
	}
	if T2Float(int64(3)) != 3 || T2Float("2.5") != 2.5 || T2Float(1.25) != 1.25 {
		t.Error("T2Float")
	}
	if T2Bool(int64(0)) || !T2Bool("x") || !T2Bool(true) {
		t.Error("T2Bool")
	}
	if T2Str("a") != "a" || T2Str(UUID("u")) != "u" {
		t.Error("T2Str")
	}
	if T2Date("2000-01-05").ISO() != "2000-01-05" {
		t.Error("T2Date")
	}
	dt := T2DateTime("2000-01-01 10:00:00+02:00")
	if dt.Text() != "2000-01-01T08:00:00.000000Z" {
		t.Errorf("T2DateTime: %s", dt.Text())
	}
	if T2DateTime(dt) != dt || T2Date(Date(730120)) != Date(730120) {
		t.Error("parameter values pass through")
	}
	want := "12345678-1234-5678-1234-567812345678"
	for _, in := range []any{
		"{12345678-1234-5678-1234-567812345678}",
		"urn:uuid:12345678123456781234567812345678",
		"12345678123456781234567812345678",
		UUID(want),
	} {
		if got := T2Uuid(in); got != want {
			t.Errorf("T2Uuid(%v) = %s", in, got)
		}
	}
	for _, c := range []struct {
		f    func()
		kind string
		msg  string
	}{
		{func() { T2Uuid("x") }, "ValueError", "badly formed hexadecimal UUID string"},
		{func() { T2Date("x") }, "ValueError", "Invalid isoformat string: 'x'"},
		{func() { T2DateTime("x") }, "ValueError", "Invalid isoformat string: 'x'"},
		{func() { T2Int("x") }, "ValueError", "invalid literal for int() with base 10: 'x'"},
	} {
		e := raised(c.f)
		if e == nil || e.Kind != c.kind || e.Msg != c.msg {
			t.Errorf("got %+v, want %s: %s", e, c.kind, c.msg)
		}
	}
	if o := T2Opt(nil, T2Int); o.Ok {
		t.Error("None")
	}
	if o := T2Opt(Unset, T2Int); o.Ok {
		t.Error("an unsent optional parameter is None")
	}
	if o := T2Opt(int64(4), T2Int); !o.Ok || o.V != 4 {
		t.Error("Some")
	}
}

func TestT2StoreAndValue(t *testing.T) {
	cases := []struct {
		v      any
		scalar string
		want   any
	}{
		{int64(3), "Float", 3.0},
		{int64(3), "Int", int64(3)},
		{true, "Bool", int64(1)},
		{Some(Date(730124)), "Date", "2000-01-05"},
		{None[Date](), "Date", nil},
		{DateTime(0), "DateTime", "1970-01-01T00:00:00.000000Z"},
		{"12345678-1234-5678-1234-567812345678", "Uuid", "12345678-1234-5678-1234-567812345678"},
		{"s", "Str", "s"},
	}
	for _, c := range cases {
		if got := T2Store(c.v, c.scalar); got != c.want {
			t.Errorf("T2Store(%v, %s) = %#v, want %#v", c.v, c.scalar, got, c.want)
		}
	}
	if v := T2Value("12345678-1234-5678-1234-567812345678", "Uuid"); v != UUID("12345678-1234-5678-1234-567812345678") {
		t.Errorf("T2Value Uuid: %#v", v)
	}
	if v := T2Value(Some(int64(2)), "Float"); v != 2.0 {
		t.Errorf("T2Value widening: %#v", v)
	}
	if !T2Holds(true) || T2Holds(false) || T2Holds(None[bool]()) || !T2Holds(Some(true)) {
		t.Error("T2Holds")
	}
}

func TestT2RowBound(t *testing.T) {
	T2RowBound("p", 100, 3) // the minimum bound
	e := raised(func() { T2RowBound("p", 101, 3) })
	if e == nil || e.Code != "R621" || e.Msg != "procedure 'p' returned 101 rows; its input bounds the result to 100" {
		t.Errorf("%+v", e)
	}
	e = raised(func() { T2RowBound("p", 201, 200) })
	if e == nil || e.Msg != "procedure 'p' returned 201 rows; its input bounds the result to 200" {
		t.Errorf("%+v", e)
	}
}

func TestCells(t *testing.T) {
	c := NewCells([]string{"a", "b"}, []any{int64(1), "x"})
	c.Set("c", 2.5)
	c.Set("a", int64(9))
	if got := c.Row([]string{"c", "a"}); got[0] != 2.5 || got[1] != int64(9) {
		t.Errorf("Row: %v", got)
	}
	r := c.Rename([][2]string{{"a", "z"}}).Select([]string{"z", "b"})
	if got := r.Row([]string{"z", "b"}); got[0] != int64(9) || got[1] != "x" {
		t.Errorf("Rename/Select: %v", got)
	}
	if e := raised(func() { r.Get("a") }); e == nil || e.Kind != "KeyError" || e.Msg != "'a'" {
		t.Errorf("KeyError: %+v", e)
	}
}

// A 204 is Starlette's Response(status_code=204): no body, no Content-Type, no
// Content-Length (recorded with Starlette's TestClient: headers {}).
func TestNoContent(t *testing.T) {
	route := Route{
		EP:     Endpoint{Name: "del", Method: "DELETE", Path: "/x", Auth: "Public"},
		Handle: func(*Ctx, map[string]any) Response { return Response{Status: 204, Body: NoContent} },
	}
	app, err := NewApp(filepath.Join(t.TempDir(), "app.db"), []Route{route}, Settings{})
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(app)
	defer srv.Close()
	r := do(t, srv, "DELETE", "/x", "", "")
	if r.status != 204 || r.body != "" {
		t.Errorf("%d %q", r.status, r.body)
	}
	for _, h := range []string{"Content-Type", "Content-Length"} {
		if _, ok := r.headers[h]; ok {
			t.Errorf("header %s: %v", h, r.headers[h])
		}
	}
}
