package rt

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

const authSQL = `SELECT "id", "token", "role", "active", "name" FROM "users" WHERE "token" = ?`

var userCols = []string{"id", "token", "role", "active", "name"}

func ep(name, method, path, auth string, writes bool, params ...ParamSpec) Endpoint {
	return Endpoint{Name: name, Method: method, Path: path, Auth: auth, Params: params, Writes: writes,
		AuthSQL: authSQL, UserColumns: userCols, UserKey: "id", AdminRole: "admin"}
}

var itemCols = []Col{{"id", "Int"}, {"name", "Str"}, {"price", "Float"}, {"created_at", "DateTime"}}

var sent []string // effect calls, in order
var sentMu sync.Mutex

func testRoutes() []Route {
	return []Route{
		{ep("list_items", "GET", "/items", "Public", false,
			ParamSpec{Name: "limit", Source: "query", Scalar: "Int", Ge: F(0), Le: F(100), Default: int64(10)}),
			func(c *Ctx, p map[string]any) Response {
				r := c.Query(`SELECT "id", "name", "price", "created_at" FROM "items" ORDER BY "id" LIMIT :limit`,
					B("limit", p["limit"]))
				return RespondList(r, itemCols)
			}},
		{ep("create_item", "POST", "/items", "user", true,
			ParamSpec{Name: "name", Source: "body", Scalar: "Str", MaxLen: I(20)},
			ParamSpec{Name: "price", Source: "body", Scalar: "Float", Ge: F(0)}),
			func(c *Ctx, p map[string]any) Response {
				r := c.Write(`INSERT INTO "items" ("name", "price", "owner", "created_at") VALUES (:name, :price, :__user_id, :__now) RETURNING "id", "name", "price", "created_at"`,
					ErrorResponse(409, "name_taken", "an item with this name exists"),
					B("name", p["name"]), B("price", p["price"]), B("__user_id", c.Caller.ID), B("__now", c.Now))
				return RespondOne(r, itemCols, 201, "7:5")
			}},
		{ep("create_item_plain", "POST", "/plain", "Public", true,
			ParamSpec{Name: "name", Source: "body", Scalar: "Str", MaxLen: I(20)}),
			func(c *Ctx, p map[string]any) Response {
				r := c.Query(`INSERT INTO "items" ("name", "price") VALUES (:name, 1.5) RETURNING "id", "name", "price", "created_at"`,
					B("name", p["name"]))
				return RespondOne(r, itemCols, 201, "9:5")
			}},
		{ep("mine", "GET", "/items/mine", "user", false),
			func(c *Ctx, p map[string]any) Response {
				r := c.Query(`SELECT "id", "name", "price", "created_at" FROM "items" WHERE "owner" = :u ORDER BY "id"`, B("u", c.Caller.ID))
				return RespondList(r, itemCols)
			}},
		{ep("get_item", "GET", "/items/{id}", "Public", false,
			ParamSpec{Name: "id", Source: "path", Scalar: "Int"}),
			func(c *Ctx, p map[string]any) Response {
				r := c.Query(`SELECT "id", "name", "price", "created_at" FROM "items" WHERE "id" = :id`, B("id", p["id"]))
				CheckOne(r, ErrorResponse(404, "not_found", "item not found"), "12:3")
				return RespondOne(r, itemCols, 200, "12:3")
			}},
		{ep("boom", "GET", "/boom", "Public", false),
			func(c *Ctx, p map[string]any) Response {
				var m map[string]int
				m["x"] = 1 // a Go runtime panic
				return Response{}
			}},
		{ep("boom_write", "POST", "/boom", "Public", true),
			func(c *Ctx, p map[string]any) Response {
				c.Query(`INSERT INTO "items" ("name", "price") VALUES ('ghost', 1) RETURNING "id"`)
				panic(errors.New("kaput"))
			}},
		{ep("fuel", "GET", "/fuel", "Public", false),
			func(c *Ctx, p map[string]any) Response {
				T2Run("3:4", func() {
					for {
						c.Fuel.Tick()
					}
				})
				return Response{}
			}},
		{ep("count", "GET", "/count", "user", false),
			func(c *Ctx, p map[string]any) Response {
				c.Query(`SELECT 1`)
				c.Query(`  with x AS (SELECT 2) SELECT * FROM x`)
				c.Exec(`CREATE TABLE IF NOT EXISTS "scratch" ("x" INTEGER)`)
				return Response{Status: 200, Body: Obj{{"queries", int64(c.Queries())}}}
			}},
		{ep("admin_only", "GET", "/admin", "admin", false),
			func(c *Ctx, p map[string]any) Response {
				return Response{Status: 200, Body: Obj{{"id", c.Caller.ID}, {"role", c.Caller.Role}, {"admin", c.Caller.IsAdmin}}}
			}},
		{ep("notify", "POST", "/notify", "Public", true,
			ParamSpec{Name: "to", Source: "body", Scalar: "Str"}),
			func(c *Ctx, p map[string]any) Response {
				c.Exec(OutboxDDL)
				r := c.Query(`INSERT INTO "lattice_outbox" ("endpoint", "native", "args", "status", "error", "created_at", "attempts") SELECT 'notify', j.value ->> '$[0]', json_object('types', json('["Str", "Int"]'), 'values', json(j.value ->> '$[1]')), 'pending', NULL, :now, 0 FROM json_each(:rows) AS j ORDER BY j.key RETURNING "id", "native", "args"`,
					B("now", c.Now), B("rows", `[["send", "[\"`+p["to"].(string)+`\", 1]"], ["flaky", "[\"x\", 2]"], ["missing", "[\"y\", 3]"]]`))
				c.Pending = append(c.Pending, r.Rows...)
				return Response{Status: 200, Body: Obj{{"queued", int64(len(r.Rows))}}}
			}},
		{ep("form", "POST", "/form", "Public", false,
			ParamSpec{Name: "n", Source: "body", Scalar: "Int"}),
			func(c *Ctx, p map[string]any) Response {
				return Response{Status: 200, Body: Obj{{"n", p["n"]}}}
			}},
	}
}

func newTestApp(t *testing.T, s Settings) (*App, *httptest.Server) {
	t.Helper()
	routes := testRoutes()
	for i := range routes {
		if routes[i].EP.Name == "form" {
			routes[i].EP.Form = true
		}
	}
	if s.Effects == nil {
		s.Effects = map[string]Effect{
			"send": func(args []any) error {
				sentMu.Lock()
				defer sentMu.Unlock()
				sent = append(sent, fmt.Sprintf("%v", args))
				return nil
			},
			"flaky": func(args []any) error { Raise("ValueError", "flaky failed"); return nil },
		}
	}
	if s.Fuel == 0 {
		s.Fuel = 1000
	}
	dbPath := filepath.Join(t.TempDir(), "app.db")
	app, err := NewApp(dbPath, routes, s)
	if err != nil {
		t.Fatal(err)
	}
	for _, stmt := range []string{
		`CREATE TABLE "users" ("id" INTEGER PRIMARY KEY, "token" TEXT, "role" TEXT, "active" INTEGER, "name" TEXT)`,
		`CREATE TABLE "items" ("id" INTEGER PRIMARY KEY, "name" TEXT UNIQUE, "price" REAL, "owner" INTEGER, "created_at" DATETIME)`,
		`INSERT INTO "users" VALUES (1, 'tok-user', 'user', 1, 'Ann'), (2, 'tok-admin', 'admin', 1, 'Bob'), (3, 'tok-off', 'user', 0, 'Cy'), (4, 'dup', 'user', 1, 'D1'), (5, 'dup', 'user', 1, 'D2')`,
		`INSERT INTO "items" ("name", "price", "owner", "created_at") VALUES ('apple', 1.5, 1, '2026-09-28T10:11:12.000000Z'), ('pear', 1e16, 2, '2026-09-28 10:11:12+02:00')`,
	} {
		if _, err := app.DB.Exec(stmt); err != nil {
			t.Fatal(err)
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

func do(t *testing.T, srv *httptest.Server, method, path, token, body string, hdr ...string) reply {
	t.Helper()
	req, err := http.NewRequest(method, srv.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if token != "" {
		req.Header.Set("Authorization", token)
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

func expect(t *testing.T, got reply, status int, body string) {
	t.Helper()
	if got.status != status || got.body != body {
		t.Errorf("got %d %s, want %d %s", got.status, got.body, status, body)
	}
}

func TestListAndOne(t *testing.T) {
	_, srv := newTestApp(t, Settings{})
	r := do(t, srv, "GET", "/items", "", "")
	expect(t, r, 200, `[{"id":1,"name":"apple","price":1.5,"created_at":"2026-09-28T10:11:12.000000Z"},{"id":2,"name":"pear","price":1e+16,"created_at":"2026-09-28T08:11:12.000000Z"}]`)
	if ct := r.headers.Get("Content-Type"); ct != "application/json" {
		t.Errorf("content-type %q", ct)
	}
	expect(t, do(t, srv, "GET", "/items?limit=1", "", ""), 200,
		`[{"id":1,"name":"apple","price":1.5,"created_at":"2026-09-28T10:11:12.000000Z"}]`)
	expect(t, do(t, srv, "GET", "/items/2", "", ""), 200,
		`{"id":2,"name":"pear","price":1e+16,"created_at":"2026-09-28T08:11:12.000000Z"}`)
	expect(t, do(t, srv, "GET", "/items/9", "", ""), 404, `{"error":"not_found","detail":"item not found"}`)
	// /items/mine is routed before /items/{id} (the generator orders routes)
	expect(t, do(t, srv, "GET", "/items/mine", "Bearer tok-user", ""), 200,
		`[{"id":1,"name":"apple","price":1.5,"created_at":"2026-09-28T10:11:12.000000Z"}]`)
}

func TestNaiveWire(t *testing.T) {
	saved := Storage
	defer func() { Storage = saved }()
	_, srv := newTestApp(t, Settings{DateTimeWire: "naive"})
	expect(t, do(t, srv, "GET", "/items/1", "", ""), 200,
		`{"id":1,"name":"apple","price":1.5,"created_at":"2026-09-28T10:11:12"}`)
}

func TestValidation422(t *testing.T) {
	_, srv := newTestApp(t, Settings{})
	expect(t, do(t, srv, "GET", "/items?limit=abc", "", ""), 422,
		`{"error":"validation","detail":[{"loc":["query","limit"],"msg":"must be an integer"}]}`)
	expect(t, do(t, srv, "GET", "/items?limit=101", "", ""), 422,
		`{"error":"validation","detail":[{"loc":["query","limit"],"msg":"must be <= 100"}]}`)
	expect(t, do(t, srv, "GET", "/items?limit=99999999999999999999", "", ""), 422,
		`{"error":"validation","detail":[{"loc":["query","limit"],"msg":"must be an integer between -9223372036854775808 and 9223372036854775807"}]}`)
	expect(t, do(t, srv, "GET", "/items/x", "", ""), 422,
		`{"error":"validation","detail":[{"loc":["path","id"],"msg":"must be an integer"}]}`)
	expect(t, do(t, srv, "POST", "/items", "Bearer tok-user", `{"price": -1}`), 422,
		`{"error":"validation","detail":[{"loc":["body","name"],"msg":"field required"},{"loc":["body","price"],"msg":"must be >= 0"}]}`)
	expect(t, do(t, srv, "POST", "/items", "Bearer tok-user", "\xff"), 422,
		`{"error":"validation","detail":[{"loc":["body"],"msg":"the body must be a JSON object"}]}`)
	expect(t, do(t, srv, "POST", "/form", "", "n=5&x", "Content-Type", "application/x-www-form-urlencoded"), 200, `{"n":5}`)
	expect(t, do(t, srv, "POST", "/form", "", "n=5", "Content-Type", "application/json"), 422,
		`{"error":"validation","detail":[{"loc":["body","n"],"msg":"field required"}]}`)
	_, fsrv := newTestApp(t, Settings{ErrorBody: "fastapi"})
	expect(t, do(t, fsrv, "GET", "/items?limit=-1", "", ""), 422,
		`{"detail":[{"loc":["query","limit"],"msg":"must be >= 0"}]}`)
}

func TestAuth(t *testing.T) {
	_, srv := newTestApp(t, Settings{ActiveColumn: S("active")})
	expect(t, do(t, srv, "GET", "/count", "", ""), 401, `{"error":"unauthorized"}`)
	expect(t, do(t, srv, "GET", "/count", "Bearer nobody", ""), 401, `{"error":"unauthorized"}`)
	expect(t, do(t, srv, "GET", "/count", "Basic tok-user", ""), 401, `{"error":"unauthorized"}`)
	expect(t, do(t, srv, "GET", "/count", "  bearer   tok-user ", ""), 200, `{"queries":3}`)
	expect(t, do(t, srv, "GET", "/count", "Bearer tok-off", ""), 400, `{"error":"bad_request","detail":"Inactive user"}`)
	expect(t, do(t, srv, "GET", "/admin", "Bearer tok-user", ""), 403, `{"error":"forbidden"}`)
	expect(t, do(t, srv, "GET", "/admin", "Bearer tok-admin", ""), 200, `{"id":2,"role":"admin","admin":true}`)
	expect(t, do(t, srv, "GET", "/count", "Bearer tok-admin", ""), 200, `{"queries":3}`) // admin calls every endpoint
	expect(t, do(t, srv, "GET", "/count", "Bearer dup", ""), 500, `{"error":"internal","detail":"error R311: ambiguous caller"}`)
	_, fsrv := newTestApp(t, Settings{ErrorBody: "fastapi", ForbiddenDetail: S("Not enough permissions"),
		ActiveColumn: S("active"), InactiveStatus: 403, InactiveDetail: "Inactive"})
	expect(t, do(t, fsrv, "GET", "/count", "Bearer nobody", ""), 401, `{"detail":"Unauthorized"}`)
	expect(t, do(t, fsrv, "GET", "/admin", "Bearer tok-user", ""), 403, `{"detail":"Not enough permissions"}`)
	expect(t, do(t, fsrv, "GET", "/count", "Bearer tok-off", ""), 403, `{"detail":"Inactive"}`)
	_, dsrv := newTestApp(t, Settings{UnauthorizedDetail: S("Could not validate credentials")})
	expect(t, do(t, dsrv, "GET", "/count", "", ""), 401, `{"error":"unauthorized","detail":"Could not validate credentials"}`)
}

func TestWritesAndConflicts(t *testing.T) {
	app, srv := newTestApp(t, Settings{})
	r := do(t, srv, "POST", "/items", "Bearer tok-user", `{"name": "fig", "price": 2}`)
	if r.status != 201 || !strings.HasPrefix(r.body, `{"id":3,"name":"fig","price":2.0,"created_at":"`) {
		t.Fatalf("create: %d %s", r.status, r.body)
	}
	expect(t, do(t, srv, "POST", "/items", "Bearer tok-user", `{"name": "fig", "price": 3}`), 409,
		`{"error":"name_taken","detail":"an item with this name exists"}`)
	expect(t, do(t, srv, "POST", "/plain", "", `{"name": "fig"}`), 409,
		`{"error":"conflict","detail":"a row with the same unique value already exists"}`)
	var n int
	if err := app.DB.QueryRow(`SELECT count(*) FROM "items"`).Scan(&n); err != nil || n != 3 {
		t.Fatalf("items: %d %v", n, err)
	}
}

func TestPanicIs500AndServerSurvives(t *testing.T) {
	app, srv := newTestApp(t, Settings{})
	r := do(t, srv, "GET", "/boom", "", "")
	if r.status != 500 || !strings.HasPrefix(r.body, `{"error":"internal","detail":"InternalError: assignment to entry in nil map`) {
		t.Errorf("boom: %d %s", r.status, r.body)
	}
	expect(t, do(t, srv, "POST", "/boom", "", ""), 500, `{"error":"internal","detail":"InternalError: kaput"}`)
	var n int
	if err := app.DB.QueryRow(`SELECT count(*) FROM "items" WHERE "name" = 'ghost'`).Scan(&n); err != nil || n != 0 {
		t.Fatalf("the failed write was not rolled back: %d %v", n, err)
	}
	expect(t, do(t, srv, "GET", "/items/1", "", ""), 200,
		`{"id":1,"name":"apple","price":1.5,"created_at":"2026-09-28T10:11:12.000000Z"}`)
	// a T2 runtime error: 500 with the located message, as t2_report formats it
	expect(t, do(t, srv, "GET", "/fuel", "", ""), 500,
		`{"error":"internal","detail":"3:4: error R620: procedure ran out of fuel: the request's budget of 1000 steps is spent (every loop iteration, call and comprehension element costs one)"}`)
	// the write lock is free again: a write still succeeds
	if r := do(t, srv, "POST", "/plain", "", `{"name": "kiwi"}`); r.status != 201 {
		t.Errorf("after panics: %d %s", r.status, r.body)
	}
}

func TestSQLCountHeader(t *testing.T) {
	t.Setenv("LATTICE_TRACE_SQL", "1")
	_, srv := newTestApp(t, Settings{})
	r := do(t, srv, "GET", "/count", "Bearer tok-user", "")
	// auth SELECT + SELECT + WITH; BEGIN/COMMIT and CREATE are not counted
	if got := r.headers.Get("X-Lattice-Sql-Count"); got != "3" || r.body != `{"queries":3}` {
		t.Errorf("count: header %q body %s", got, r.body)
	}
	if got := do(t, srv, "GET", "/count", "", "").headers.Get("X-Lattice-Sql-Count"); got != "0" {
		t.Errorf("401 before any statement: %q", got)
	}
	if got := do(t, srv, "GET", "/nowhere", "", "").headers.Get("X-Lattice-Sql-Count"); got != "0" {
		t.Errorf("404: %q", got)
	}
	if got := do(t, srv, "GET", "/items/1", "", "").headers.Get("X-Lattice-Sql-Count"); got != "1" {
		t.Errorf("one read: %q", got)
	}
	t.Setenv("LATTICE_TRACE_SQL", "")
	_, quiet := newTestApp(t, Settings{})
	if _, ok := do(t, quiet, "GET", "/items/1", "", "").headers["X-Lattice-Sql-Count"]; ok {
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

func TestRouting(t *testing.T) {
	_, srv := newTestApp(t, Settings{})
	expect(t, do(t, srv, "GET", "/nowhere", "", ""), 404, `{"error":"not_found"}`)
	expect(t, do(t, srv, "DELETE", "/items", "", ""), 405, `{"error":"method_not_allowed"}`)
	expect(t, do(t, srv, "HEAD", "/items", "", ""), 405, ``)
	expect(t, do(t, srv, "GET", "/items/a%2Fb", "", ""), 404, `{"error":"not_found"}`)
	r := do(t, srv, "GET", "/items/1/?x=1", "", "")
	if r.status != 307 || r.headers.Get("Location") != srv.URL+"/items/1?x=1" || r.body != "" {
		t.Errorf("redirect: %d %q %q", r.status, r.headers.Get("Location"), r.body)
	}
	_, fsrv := newTestApp(t, Settings{ErrorBody: "fastapi"})
	expect(t, do(t, fsrv, "GET", "/nowhere", "", ""), 404, `{"detail":"Not Found"}`)
	expect(t, do(t, fsrv, "PUT", "/items", "", ""), 405, `{"detail":"Method Not Allowed"}`)
}

func TestConcurrentWritesDoNotFail(t *testing.T) {
	// 32 goroutines each POST an insert; all must answer 2xx (busy_timeout, IMMEDIATE tx)
	app, srv := newTestApp(t, Settings{})
	var wg sync.WaitGroup
	results := make([]reply, 32)
	for i := range 32 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results[i] = do(t, srv, "POST", "/items", "Bearer tok-user", fmt.Sprintf(`{"name": "n%d", "price": %d}`, i, i))
		}()
	}
	wg.Wait()
	for i, r := range results {
		if r.status != 201 {
			t.Errorf("request %d: %d %s", i, r.status, r.body)
		}
	}
	var n int
	if err := app.DB.QueryRow(`SELECT count(*) FROM "items"`).Scan(&n); err != nil || n != 34 {
		t.Errorf("items: %d %v", n, err)
	}
}

func TestOutbox(t *testing.T) {
	app, srv := newTestApp(t, Settings{})
	sentMu.Lock()
	sent = nil
	sentMu.Unlock()
	t.Setenv("LATTICE_TRACE_SQL", "1")
	app.Trace = true
	r := do(t, srv, "POST", "/notify", "", `{"to": "ann"}`)
	expect(t, r, 200, `{"queued":3}`)
	// the INSERT, then per row: claim + mark (the column check is one SELECT)
	if got := r.headers.Get("X-Lattice-Sql-Count"); got != "8" {
		t.Errorf("outbox statements: %q", got)
	}
	if fmt.Sprint(sent) != "[[ann 1]]" {
		t.Errorf("sent: %v", sent)
	}
	rows, err := app.DB.Query(`SELECT "id", "native", "status", COALESCE("error", ''), "attempts" FROM "lattice_outbox" ORDER BY "id"`)
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for rows.Next() {
		var id, attempts int
		var native, status, e string
		_ = rows.Scan(&id, &native, &status, &e, &attempts)
		got = append(got, fmt.Sprintf("%d %s %s %q %d", id, native, status, e, attempts))
	}
	rows.Close()
	want := []string{`1 send done "" 1`, `2 flaky failed "ValueError: flaky failed" 1`,
		`3 missing failed "no implementation for native 'missing'" 1`}
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Errorf("outbox rows:\n%s\nwant\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
	// retry re-sends the failed rows (now with a working flaky)
	app.Settings.Effects["flaky"] = func(args []any) error { return nil }
	done, failed, err := app.RetryOutbox(false, OutboxGrace, nil)
	if err != nil || done != 1 || failed != 1 {
		t.Errorf("retry: %d done, %d failed, %v", done, failed, err)
	}
	// a rolled-back request leaves no outbox rows and sends nothing
	expect(t, do(t, srv, "POST", "/notify", "", `{}`), 422,
		`{"error":"validation","detail":[{"loc":["body","to"],"msg":"field required"}]}`)
}

func TestDispatchSkipsRowsHeldElsewhere(t *testing.T) {
	app, _ := newTestApp(t, Settings{})
	conn, _ := app.DB.Conn(context.Background())
	defer conn.Close()
	db := &dbConn{conn: conn}
	db.exec(OutboxDDL)
	now := time.Now().UTC()
	stamp := DateTimeOf(now).Text()
	db.exec(`INSERT INTO "lattice_outbox" VALUES (1, 'e', 'send', '{"types": ["Int"], "values": [5]}', 'dispatching', NULL, ?, 1, ?)`, stamp, stamp)
	done, failed := app.DispatchOutbox(db, [][]any{{int64(1), "send", `{"types": ["Int"], "values": [5]}`}}, &now, OutboxGrace)
	if done != 0 || failed != 0 {
		t.Errorf("a live claim must be skipped: %d %d", done, failed)
	}
	later := now.Add(OutboxGrace + time.Second)
	done, _ = app.DispatchOutbox(db, [][]any{{int64(1), "send", `{"types": ["Int"], "values": [5]}`}}, &later, OutboxGrace)
	if done != 1 {
		t.Errorf("a stale claim is taken over: %d", done)
	}
}

// TestServeCLI runs Serve in a child process: `--db`, `--addr`, one LISTENING line.
func TestServeCLI(t *testing.T) {
	if os.Getenv("RT_SERVE_CHILD") == "1" {
		os.Args = append([]string{"app"}, strings.Fields(os.Getenv("RT_SERVE_ARGS"))...)
		Serve(testRoutes(), Settings{})
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
	merged := MergeUpdate(r, [][]any{{int64(2), "B"}}, []string{"id", "name"}, []string{"id"}, []string{"name", "missing"[:4]})
	if fmt.Sprint(merged.Rows) != "[[1 a] [2 B]]" {
		t.Errorf("MergeUpdate: %v", merged.Rows)
	}
	rows, total := SplitTotal([][]any{{"a", int64(1), int64(7)}, {nil, int64(0), int64(7)}})
	if fmt.Sprint(rows, total) != "[[a]] 7" {
		t.Errorf("SplitTotal: %v %d", rows, total)
	}
	if _, total := SplitTotal(nil); total != 0 {
		t.Errorf("SplitTotal(empty) = %d", total)
	}
	page := RespondPage(r, []Col{{"id", "Int"}}, [][2]string{{"items", "data"}, {"total", "count"}, {"limit", ""}, {"offset", "skip"}}, int64(10), int64(0), nil)
	if b, _ := WriteJSON(page.Body); string(b) != `{"data":[{"id":1},{"id":2}],"skip":0}` {
		t.Errorf("RespondPage: %s", b)
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
