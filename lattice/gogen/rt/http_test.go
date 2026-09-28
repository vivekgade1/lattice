package rt

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
)

// The HTTP answers of the FastAPI app generated from testdata/httpapp.py
// (testdata/gen_http_answers.py): the Go app below serves the same endpoints -- the
// generated Endpoint specs and SQL, run by handlers that transliterate the generated
// steps functions -- over the same database, and must answer every request alike.

type httpAnswers struct {
	Schema string `json:"schema"`
	Styles map[string]struct {
		Endpoints map[string]struct {
			Auth        string   `json:"auth"`
			Writes      bool     `json:"writes"`
			AuthSQL     string   `json:"auth_sql"`
			UserColumns []string `json:"user_columns"`
			UserKey     string   `json:"user_key"`
			AdminRole   string   `json:"admin_role"`
			Params      []struct {
				Name    string   `json:"name"`
				Source  string   `json:"source"`
				Scalar  string   `json:"scalar"`
				Ge      *float64 `json:"ge"`
				Le      *float64 `json:"le"`
				MaxLen  *int64   `json:"max_len"`
				Default any      `json:"default"`
			} `json:"params"`
		} `json:"endpoints"`
		Constants map[string]json.RawMessage            `json:"constants"`
		Routes    []struct{ Path, Method, Name string } `json:"routes"`
		Replies   []struct {
			Name          string  `json:"name"`
			Method        string  `json:"method"`
			Path          string  `json:"path"`
			Auth          *string `json:"auth"`
			Body          *string `json:"body"`
			Status        int     `json:"status"`
			Reply         string  `json:"reply"`
			ContentType   *string `json:"content_type"`
			ContentLength *string `json:"content_length"`
			Location      *string `json:"location"`
			Queries       int     `json:"queries"`
			Items         [][]any `json:"items"`
		} `json:"replies"`
	} `json:"styles"`
}

func loadHTTPAnswers(t *testing.T) *httpAnswers {
	t.Helper()
	data, err := os.ReadFile("testdata/http_answers.json")
	if err != nil {
		t.Fatal(err)
	}
	var a httpAnswers
	if err := json.Unmarshal(data, &a); err != nil {
		t.Fatal(err)
	}
	if len(a.Styles) != 2 || len(a.Styles["lattice"].Replies) < 30 {
		t.Fatalf("testdata/http_answers.json is incomplete")
	}
	return &a
}

// styleSettings are the profile settings of testdata/httpapp.py's two profiles.
var styleSettings = map[string]Settings{
	"lattice": {ActiveColumn: S("active")},
	"fastapi": {ErrorBody: "fastapi", UnauthorizedDetail: S("Not authenticated"),
		ForbiddenDetail: S("Not enough privileges"), ActiveColumn: S("active"),
		InactiveStatus: 403, InactiveDetail: "Inactive"},
}

// consts are the generated module's constants.
type consts map[string]json.RawMessage

func (c consts) str(name string) string {
	var s string
	if err := json.Unmarshal(c[name], &s); err != nil {
		panic(name)
	}
	return s
}

func (c consts) strs(name string) []string {
	var s []string
	if err := json.Unmarshal(c[name], &s); err != nil {
		panic(name)
	}
	return s
}

func (c consts) cols(name string) []Col {
	var pairs [][2]string
	if err := json.Unmarshal(c[name], &pairs); err != nil {
		panic(name)
	}
	out := make([]Col, len(pairs))
	for i, p := range pairs {
		out[i] = Col{p[0], p[1]}
	}
	return out
}

func (c consts) shape(name string) [][2]string {
	var pairs [][2]string
	if err := json.Unmarshal(c[name], &pairs); err != nil {
		panic(name)
	}
	return pairs
}

// httpappHandlers transliterate the generated steps functions of testdata/httpapp.py.
func httpappHandlers(k consts) map[string]func(c *Ctx, p map[string]any) Response {
	return map[string]func(c *Ctx, p map[string]any) Response{
		"list_items": func(c *Ctx, p map[string]any) Response {
			r := c.Query(k.str("LIST_ITEMS_RESULT_SQL"), B("p_n", p["n"]), B("p_skip", p["skip"]))
			rows, total := SplitTotal(r.Rows)
			result := Result{Cols: k.strs("LIST_ITEMS_RESULT_COLUMNS"), Rows: rows}
			return RespondPage(result, k.cols("LIST_ITEMS_RESPONSE"), k.shape("LIST_ITEMS_PAGE_SHAPE"),
				p["n"], p["skip"], &total)
		},
		"create_item": func(c *Ctx, p map[string]any) Response {
			r := c.Write(k.str("CREATE_ITEM_ITEM_SQL"),
				ErrorResponse(409, "conflict", "an item with this name exists"),
				B("p_name", p["name"]), B("p_price", p["price"]), B("__user_id", c.Caller.ID),
				B("__is_admin", c.Caller.IsAdminInt()), B("__now", c.Now))
			item := Result{Cols: k.strs("CREATE_ITEM_ITEM_COLUMNS"), Rows: r.Rows}
			return RespondOne(item, k.cols("CREATE_ITEM_RESPONSE"), 201, "34:1")
		},
		"my_items": func(c *Ctx, p map[string]any) Response {
			r := c.Query(k.str("MY_ITEMS_FEW_SQL"), B("__l0", int64(100)), B("__user_id", c.Caller.ID),
				B("__is_admin", c.Caller.IsAdminInt()))
			few := Result{Cols: k.strs("MY_ITEMS_FEW_COLUMNS"), Rows: r.Rows}
			return RespondList(few, k.cols("MY_ITEMS_RESPONSE"))
		},
		"admin_ping": func(c *Ctx, p map[string]any) Response {
			user := Result{Cols: c.EP.UserColumns, Rows: [][]any{c.Caller.Row}}
			return RespondOne(user, k.cols("ADMIN_PING_RESPONSE"), 200, "50:1")
		},
		"get_item": func(c *Ctx, p map[string]any) Response {
			r := c.Query(k.str("GET_ITEM_FOUND_SQL"), B("p_id", p["id"]))
			found := Result{Cols: k.strs("GET_ITEM_FOUND_COLUMNS"), Rows: r.Rows}
			CheckOne(found, ErrorResponse(404, "not_found", "item not found"), "28:12")
			return RespondOne(found, k.cols("GET_ITEM_RESPONSE"), 200, "25:1")
		},
	}
}

// httpappRoutes are the generated app's routes, in its order, plus extra.
func httpappRoutes(t *testing.T, a *httpAnswers, style string, extra ...Route) []Route {
	t.Helper()
	st := a.Styles[style]
	handlers := httpappHandlers(consts(st.Constants))
	var routes []Route
	for _, r := range st.Routes {
		e := st.Endpoints[r.Name]
		ep := Endpoint{Name: r.Name, Method: r.Method, Path: r.Path, Auth: e.Auth, Writes: e.Writes,
			AuthSQL: e.AuthSQL, UserColumns: e.UserColumns, UserKey: e.UserKey, AdminRole: e.AdminRole}
		for _, p := range e.Params {
			spec := ParamSpec{Name: p.Name, Source: p.Source, Scalar: p.Scalar, Ge: p.Ge, Le: p.Le, MaxLen: p.MaxLen}
			if f, ok := p.Default.(float64); ok && p.Scalar == "Int" {
				spec.Default = int64(f)
			} else if p.Default != nil {
				t.Fatalf("unexpected default %v", p.Default)
			}
			ep.Params = append(ep.Params, spec)
		}
		h := handlers[r.Name]
		if h == nil {
			t.Fatalf("no handler for %s", r.Name)
		}
		routes = append(routes, Route{EP: ep, Handle: h})
	}
	return append(routes, extra...)
}

// newHTTPApp serves the httpapp endpoints (plus extra routes) over a fresh database.
func newHTTPApp(t *testing.T, a *httpAnswers, style string, extra ...Route) (*App, *httptest.Server) {
	t.Helper()
	s := styleSettings[style]
	s.Fuel = 1000
	app, err := NewApp(filepath.Join(t.TempDir(), "app.db"), httpappRoutes(t, a, style, extra...), s)
	if err != nil {
		t.Fatal(err)
	}
	for _, stmt := range strings.Split(a.Schema, ";") {
		if strings.TrimSpace(stmt) != "" {
			if _, err := app.DB.Exec(stmt); err != nil {
				t.Fatal(err)
			}
		}
	}
	srv := httptest.NewServer(app)
	t.Cleanup(func() { srv.Close(); _ = app.DB.Close() })
	return app, srv
}

type reply struct {
	status  int
	body    string
	headers http.Header
}

func do(t *testing.T, srv *httptest.Server, method, path, auth, body string, hdr ...string) reply {
	t.Helper()
	var rd io.Reader
	if body != "" {
		rd = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, srv.URL+path, rd)
	if err != nil {
		t.Fatal(err)
	}
	if auth != "" {
		req.Header.Set("Authorization", auth)
	}
	for i := 0; i+1 < len(hdr); i += 2 {
		req.Header.Set(hdr[i], hdr[i+1])
	}
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return reply{resp.StatusCode, string(b), resp.Header}
}

var reNow = regexp.MustCompile(`\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z`)

// normalizeNow is gen_http_answers.normalize: a request's instant becomes <now>.
func normalizeNow(s string) string {
	return reNow.ReplaceAllStringFunc(s, func(m string) string {
		if strings.HasPrefix(m, "2000-") {
			return m
		}
		return "<now>"
	})
}

func opt(h http.Header, k string) string {
	if v, ok := h[http.CanonicalHeaderKey(k)]; ok {
		return v[0]
	}
	return "<absent>"
}

func optp(s *string) string {
	if s == nil {
		return "<absent>"
	}
	return *s
}

// TestHTTPAnswers replays the recorded requests: status, body, content-type,
// content-length, location (Python's TestClient host "testserver" is this server's
// host), the SQL statement count and the items table after each request.
func TestHTTPAnswers(t *testing.T) {
	t.Setenv("LATTICE_TRACE_SQL", "1")
	a := loadHTTPAnswers(t)
	for style, st := range a.Styles {
		app, srv := newHTTPApp(t, a, style)
		for _, c := range st.Replies {
			auth, body := "", ""
			var hdr []string
			if c.Auth != nil {
				auth = *c.Auth
			}
			if c.Body != nil {
				body = *c.Body
				hdr = []string{"Content-Type", "application/json"}
			}
			r := do(t, srv, c.Method, c.Path, auth, body, hdr...)
			where := style + " / " + c.Name
			if r.status != c.Status || normalizeNow(r.body) != c.Reply {
				t.Errorf("%s: got %d %s, want %d %s", where, r.status, r.body, c.Status, c.Reply)
			}
			if got := opt(r.headers, "Content-Type"); got != optp(c.ContentType) {
				t.Errorf("%s: content-type %q, want %q", where, got, optp(c.ContentType))
			}
			if got := opt(r.headers, "Content-Length"); got != optp(c.ContentLength) {
				t.Errorf("%s: content-length %q, want %q", where, got, optp(c.ContentLength))
			}
			wantLoc := strings.Replace(optp(c.Location), "http://testserver", srv.URL, 1)
			if got := opt(r.headers, "Location"); got != wantLoc {
				t.Errorf("%s: location %q, want %q", where, got, wantLoc)
			}
			if got := opt(r.headers, "X-Lattice-Sql-Count"); got != fmt.Sprint(c.Queries) {
				t.Errorf("%s: %s SQL statements, want %d", where, got, c.Queries)
			}
			rows, err := app.DB.Query(`SELECT id, name, price, owner_id FROM items ORDER BY id`)
			if err != nil {
				t.Fatal(err)
			}
			var items [][]any
			for rows.Next() {
				var id, owner int64
				var name string
				var price float64
				_ = rows.Scan(&id, &name, &price, &owner)
				items = append(items, []any{float64(id), name, price, float64(owner)})
			}
			rows.Close()
			if fmt.Sprint(items) != fmt.Sprint(c.Items) {
				t.Errorf("%s: items %v, want %v", where, items, c.Items)
			}
		}
	}
}

func TestSQLCountHeaderNeedsEnv(t *testing.T) {
	t.Setenv("LATTICE_TRACE_SQL", "")
	_, srv := newHTTPApp(t, loadHTTPAnswers(t), "lattice")
	if _, ok := do(t, srv, "GET", "/items/1", "", "").headers["X-Lattice-Sql-Count"]; ok {
		t.Errorf("the header is only sent with LATTICE_TRACE_SQL=1")
	}
	for _, c := range []struct {
		sql  string
		want bool
	}{{"select 1", true}, {"\n INSERT INTO t", true}, {"UPDATE t", true}, {"DELETE FROM t", true},
		{"WITH x AS (SELECT 1) SELECT 1", true}, {"REPLACE INTO t", true}, {"BEGIN IMMEDIATE", false},
		{"COMMIT", false}, {"PRAGMA x", false}, {"CREATE TABLE t (x)", false}, {"", false}, {"SELECT(1)", false}} {
		if CountedStatement(c.sql) != c.want {
			t.Errorf("CountedStatement(%q) != %v", c.sql, c.want)
		}
	}
}

func TestConcurrentWritesDoNotFail(t *testing.T) {
	// 32 goroutines each POST an insert; all must answer 2xx (busy_timeout, IMMEDIATE tx)
	app, srv := newHTTPApp(t, loadHTTPAnswers(t), "lattice")
	var wg sync.WaitGroup
	results := make([]reply, 32)
	for i := range 32 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results[i] = do(t, srv, "POST", "/items", "Bearer tok-ann",
				fmt.Sprintf(`{"name": "n%d", "price": %d}`, i, i), "Content-Type", "application/json")
		}()
	}
	wg.Wait()
	for i, r := range results {
		if r.status != 201 {
			t.Errorf("request %d: %d %s", i, r.status, r.body)
		}
	}
	var n int
	if err := app.DB.QueryRow(`SELECT count(*) FROM items`).Scan(&n); err != nil || n != 35 {
		t.Errorf("items: %d %v", n, err)
	}
}

// Go-only paths (no Python counterpart to record): a Go runtime panic in a handler, a
// failed write, a T2 runtime error through T2Run.
func goOnlyRoutes() []Route {
	public := func(name, method, path string, writes bool, h func(c *Ctx, p map[string]any) Response) Route {
		return Route{EP: Endpoint{Name: name, Method: method, Path: path, Auth: "Public", Writes: writes}, Handle: h}
	}
	return []Route{
		public("boom", "GET", "/boom", false, func(c *Ctx, p map[string]any) Response {
			var m map[string]int
			m["x"] = 1 // a Go runtime panic
			return Response{}
		}),
		public("boom_write", "POST", "/boom", true, func(c *Ctx, p map[string]any) Response {
			c.Query(`INSERT INTO items (name, price, owner_id, created_at) VALUES ('ghost', 1, 1, 'x') RETURNING id`)
			panic(errors.New("kaput"))
		}),
		public("fuel", "GET", "/fuel", false, func(c *Ctx, p map[string]any) Response {
			T2Run("3:4", func() {
				for {
					c.Fuel.Tick()
				}
			})
			return Response{}
		}),
		public("nan", "GET", "/nan", false, func(c *Ctx, p map[string]any) Response {
			var zero float64
			return Response{Status: 200, Body: Obj{{"x", zero / zero}}}
		}),
	}
}

func TestPanicIs500AndServerSurvives(t *testing.T) {
	app, srv := newHTTPApp(t, loadHTTPAnswers(t), "lattice", goOnlyRoutes()...)
	r := do(t, srv, "GET", "/boom", "", "")
	if r.status != 500 || !strings.HasPrefix(r.body, `{"error":"internal","detail":"InternalError: assignment to entry in nil map`) {
		t.Errorf("boom: %d %s", r.status, r.body)
	}
	if r := do(t, srv, "POST", "/boom", "", ""); r.status != 500 || r.body != `{"error":"internal","detail":"InternalError: kaput"}` {
		t.Errorf("boom write: %d %s", r.status, r.body)
	}
	var n int
	if err := app.DB.QueryRow(`SELECT count(*) FROM items WHERE name = 'ghost'`).Scan(&n); err != nil || n != 0 {
		t.Fatalf("the failed write was not rolled back: %d %v", n, err)
	}
	// a T2 runtime error: 500 with the located message, as t2_report formats it
	if r := do(t, srv, "GET", "/fuel", "", ""); r.status != 500 || r.body != `{"error":"internal","detail":"3:4: error R620: procedure ran out of fuel: the request's budget of 1000 steps is spent (every loop iteration, call and comprehension element costs one)"}` {
		t.Errorf("fuel: %d %s", r.status, r.body)
	}
	// JSONResponse refuses NaN (allow_nan=False): a 500
	if r := do(t, srv, "GET", "/nan", "", ""); r.status != 500 || r.body != `{"error":"internal","detail":"ValueError: Out of range float values are not JSON compliant: nan"}` {
		t.Errorf("nan: %d %s", r.status, r.body)
	}
	// the write lock is free again: a write still succeeds
	if r := do(t, srv, "POST", "/items", "Bearer tok-ann", `{"name": "kiwi", "price": 1}`); r.status != 201 {
		t.Errorf("after panics: %d %s", r.status, r.body)
	}
}

// TestServeCLI runs Serve in a child process: `--db`, `--addr`, one LISTENING line.
func TestServeCLI(t *testing.T) {
	if os.Getenv("RT_SERVE_CHILD") == "1" {
		os.Args = append([]string{"app"}, strings.Fields(os.Getenv("RT_SERVE_ARGS"))...)
		Serve(nil, Settings{})
		return
	}
	dir := t.TempDir()
	cmd := exec.Command(os.Args[0], "-test.run=^TestServeCLI$")
	cmd.Env = append(os.Environ(), "RT_SERVE_CHILD=1", "LATTICE_TRACE_SQL=1",
		"RT_SERVE_ARGS=--db "+filepath.Join(dir, "cli.db")+" --addr 127.0.0.1:0")
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait() }()
	line, err := bufio.NewReader(out).ReadString('\n')
	if err != nil || !strings.HasPrefix(line, "LISTENING 127.0.0.1:") {
		t.Fatalf("first line %q (%v)", line, err)
	}
	addr := strings.TrimSpace(strings.TrimPrefix(line, "LISTENING "))
	resp, err := http.Get("http://" + addr + "/nowhere")
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 404 || string(b) != `{"error":"not_found"}` || resp.Header.Get("X-Lattice-Sql-Count") != "0" {
		t.Errorf("CLI app: %d %s %v", resp.StatusCode, b, resp.Header)
	}
}

func TestStepHelpers(t *testing.T) {
	r := Result{Cols: []string{"id", "name", "ok"}, Rows: [][]any{{int64(1), "a", int64(1)}, {int64(2), "b", nil}}}
	func() {
		defer func() {
			if e, ok := recover().(*ErrorResp); !ok || e.Code != "denied" {
				t.Errorf("CheckRequire: %v", e)
			}
		}()
		CheckRequire(r, "ok", ErrorResponse(403, "denied", "no"))
	}()
	func() {
		defer func() {
			f, ok := recover().(*Failure)
			if !ok || f.Msg != "5:6: error R310: one() matched 2 rows; expected exactly one" {
				t.Errorf("CheckOne: %v", f)
			}
		}()
		CheckOne(r, ErrorResponse(404, "nf", "x"), "5:6")
	}()
	merged := MergeUpdate(r, [][]any{{int64(2), "B"}}, []string{"id", "name"}, []string{"id"}, []string{"name"})
	if fmt.Sprint(merged.Rows) != "[[1 a] [2 B]]" {
		t.Errorf("MergeUpdate: %v", merged.Rows)
	}
	rows, total := SplitTotal([][]any{{"a", int64(1), int64(7)}, {nil, int64(0), int64(7)}})
	if fmt.Sprint(rows, total) != "[[a]] 7" {
		t.Errorf("SplitTotal: %v %d", rows, total)
	}
	if Sent(map[string]any{"a": Unset, "b": nil}, "a") != 0 || Sent(map[string]any{"b": nil}, "b") != 1 {
		t.Errorf("Sent")
	}
	for _, c := range []struct {
		v    any
		want any
	}{{Unset, nil}, {true, int64(1)}, {UUID("a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"), "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"},
		{MakeDateTime(2026, 9, 28, 1, 2, 3, 4), "2026-09-28T01:02:03.000004Z"}} {
		if got := SQLValue(c.v); got != c.want {
			t.Errorf("SQLValue(%v) = %v", c.v, got)
		}
	}
}
