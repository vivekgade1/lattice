package platformkit

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

const schema = `
CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL, name TEXT NOT NULL,
                    role TEXT NOT NULL, token TEXT NOT NULL UNIQUE);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL,
                     total INTEGER NOT NULL CHECK (total > 0));
INSERT INTO users VALUES (1, 'a@x', 'Alice', 'Customer', 'tok-a');
INSERT INTO users VALUES (2, 's@x', 'Sam', 'Seller', 'tok-s');
INSERT INTO users VALUES (3, 'r@x', 'Root', 'Admin', 'tok-admin');
INSERT INTO users VALUES (4, 'l@x', 'Lat', 'Customer', 'tok-` + "é" + `');
INSERT INTO orders VALUES (1, 1, 10);
`

// loadSchema runs the schema script past the kit (which refuses multiple statements).
func loadSchema(t *testing.T, path string) {
	t.Helper()
	raw, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = raw.Close() }()
	if _, err := raw.Exec(schema); err != nil {
		t.Fatal(err)
	}
}

func testKit(t *testing.T, trace bool) *Kit {
	t.Helper()
	path := filepath.Join(t.TempDir(), "app.db")
	loadSchema(t, path)
	k, err := open(path, trace)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = k.DB.Close() })
	return k
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func register(mux *http.ServeMux, k *Kit) {
	mux.HandleFunc("GET /me", func(w http.ResponseWriter, r *http.Request) {
		c, ok := k.Authenticate(w, r)
		if !ok {
			return
		}
		writeJSON(w, 200, c)
	})
	mux.HandleFunc("GET /seller", func(w http.ResponseWriter, r *http.Request) {
		if _, ok := k.Authenticate(w, r, "Seller"); ok {
			writeJSON(w, 200, "ok")
		}
	})
	mux.HandleFunc("POST /bad", func(w http.ResponseWriter, r *http.Request) {
		// a statement that fails to prepare (not counted), one that fails while it runs
		// (counted), a transaction (BEGIN / COMMIT not counted), a statement prepared
		// once and run twice (counted twice)
		_, err1 := k.DB.Exec("SELECT * FROM nowhere")
		_, err2 := k.DB.Exec("INSERT INTO orders (customer_id, total) VALUES (1, -1)")
		tx, _ := k.DB.Begin()
		_, _ = tx.Exec("UPDATE orders SET total = total + 1")
		_ = tx.Commit()
		st, _ := k.DB.Prepare("SELECT total FROM orders WHERE id = ?")
		var a, b int64
		_ = st.QueryRow(1).Scan(&a)
		_ = st.QueryRow(1).Scan(&b)
		_ = st.Close()
		writeJSON(w, 200, []any{err1 != nil, err2 != nil, a, b})
	})
	mux.HandleFunc("GET /boom", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Partial", "1")
		w.WriteHeader(201)
		_, _ = w.Write([]byte("partial"))
		panic("boom")
	})
	mux.HandleFunc("GET /empty", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(204)
	})
}

type got struct {
	status int
	ctype  string
	body   string
	count  string
	header http.Header
}

func do(t *testing.T, k *Kit, method, path, auth string) got {
	t.Helper()
	mux := http.NewServeMux()
	register(mux, k)
	r := httptest.NewRequest(method, path, nil)
	if auth != "" {
		r.Header.Set("Authorization", auth)
	}
	w := httptest.NewRecorder()
	k.handler(mux).ServeHTTP(w, r)
	res := w.Result()
	b, _ := io.ReadAll(res.Body)
	return got{res.StatusCode, res.Header.Get("Content-Type"), string(b),
		res.Header.Get("X-Lattice-Sql-Count"), res.Header}
}

func TestCountedStatementIsTheHarnessRule(t *testing.T) {
	data, err := os.ReadFile("testdata/counted.json")
	if err != nil {
		t.Fatal(err)
	}
	var table [][2]any
	if err := json.Unmarshal(data, &table); err != nil {
		t.Fatal(err)
	}
	for _, row := range table {
		want, sql := row[0].(bool), row[1].(string)
		if countedStatement(sql) != want {
			t.Errorf("countedStatement(%q) = %v, want %v", sql, !want, want)
		}
	}
}

func TestAuthentication(t *testing.T) {
	k := testKit(t, true)
	const unauthorized, forbidden = `{"error":"unauthorized"}`, `{"error":"forbidden"}`
	cases := []struct {
		name, path, auth string
		status           int
		body, count      string
	}{
		{"no header", "/me", "", 401, unauthorized, "0"},
		{"other scheme", "/me", "Basic tok-a", 401, unauthorized, "0"},
		{"lower-case scheme", "/me", "bearer tok-a", 401, unauthorized, "0"},
		{"no token", "/me", "Bearer", 401, unauthorized, "0"},
		{"blank token", "/me", "Bearer \t ", 401, unauthorized, "0"},
		{"unknown token", "/me", "Bearer nope", 401, unauthorized, "1"},
		// the byte 0xa0 is Latin-1's no-break space, which str.strip() strips; the
		// UTF-8 spelling of U+00A0 reads as "Â" + no-break space, and "Â" is kept
		{"padded token", "/me", "Bearer  \xa0tok-a\x1f", 200, "", "1"},
		{"utf-8 nbsp", "/me", "Bearer  tok-a", 401, unauthorized, "1"},
		{"latin-1 token", "/me", "Bearer tok-\xe9", 200, "", "1"},
		{"customer", "/me", "Bearer tok-a", 200,
			`{"ID":1,"Role":"Customer","Email":"a@x","Name":"Alice"}` + "\n", "1"},
		{"gate, no token", "/seller", "", 401, unauthorized, "0"},
		{"gate, unknown token", "/seller", "Bearer nope", 401, unauthorized, "1"},
		{"gate, wrong role", "/seller", "Bearer tok-a", 403, forbidden, "1"},
		{"gate, role", "/seller", "Bearer tok-s", 200, `"ok"` + "\n", "1"},
		{"gate, Admin", "/seller", "Bearer tok-admin", 200, `"ok"` + "\n", "1"},
	}
	for _, c := range cases {
		g := do(t, k, "GET", c.path, c.auth)
		if g.status != c.status || (c.body != "" && g.body != c.body) || g.count != c.count {
			t.Errorf("%s: got %d %q count %s, want %d %q count %s",
				c.name, g.status, g.body, g.count, c.status, c.body, c.count)
		}
		if g.status != 200 && g.ctype != "application/json" {
			t.Errorf("%s: Content-Type %q", c.name, g.ctype)
		}
	}
}

func TestCountingFollowsTheTraceCallback(t *testing.T) {
	k := testKit(t, true)
	g := do(t, k, "POST", "/bad", "")
	// prepare failure 0, constraint failure 1, UPDATE in a transaction 1, SELECT twice 2
	if g.status != 200 || g.body != "[true,true,11,11]\n" || g.count != "4" {
		t.Errorf("got %d %q count %s", g.status, g.body, g.count)
	}
}

func TestNoCountHeaderWithoutTrace(t *testing.T) {
	k := testKit(t, false)
	if g := do(t, k, "GET", "/me", "Bearer tok-a"); g.status != 200 || g.header.Get("X-Lattice-Sql-Count") != "" {
		t.Errorf("got %d, header %v", g.status, g.header)
	}
}

func TestMultipleStatementsAreRefused(t *testing.T) {
	refused := []string{
		"UPDATE orders SET total = 1; DELETE FROM orders",
		"SELECT 1;SELECT 2",
		"SELECT 1; -- c\n SELECT 2",
		"SELECT 1; 'x'",
	}
	allowed := []string{
		"SELECT 1",
		"SELECT 1;",
		"SELECT 1; ;  ",
		"SELECT 1; -- trailing comment",
		"SELECT 1; /* trailing */",
		"SELECT ';' || \"a;b\" || `c;d` || [e;f]",
		"SELECT 1 -- ; SELECT 2",
		"SELECT 1 /* ; SELECT 2 */",
	}
	for _, q := range refused {
		if !multipleStatements(q) {
			t.Errorf("not refused: %q", q)
		}
	}
	for _, q := range allowed {
		if multipleStatements(q) {
			t.Errorf("refused: %q", q)
		}
	}
	// through k.DB: the handler sees the error; nothing ran, nothing was counted
	k := testKit(t, true)
	k.queries.Store(0)
	if _, err := k.DB.Exec("UPDATE orders SET total = 99; DELETE FROM orders"); !errors.Is(err, ErrMultipleStatements) {
		t.Fatalf("Exec: %v, want ErrMultipleStatements", err)
	}
	if _, err := k.DB.Query("SELECT 1; SELECT 2"); !errors.Is(err, ErrMultipleStatements) {
		t.Fatalf("Query: %v, want ErrMultipleStatements", err)
	}
	if n := k.queries.Load(); n != 0 {
		t.Errorf("counted %d", n)
	}
	var total, rows int
	if err := k.DB.QueryRow("SELECT total, (SELECT COUNT(*) FROM orders) FROM orders WHERE id = 1").Scan(&total, &rows); err != nil {
		t.Fatal(err)
	}
	if total != 10 || rows != 1 {
		t.Errorf("the refused statements ran: total %d, rows %d", total, rows)
	}
}

func TestFastAPIsOwnAnswers(t *testing.T) {
	k := testKit(t, true)
	cases := []struct {
		method, path string
		status       int
		ctype, body  string
	}{
		{"GET", "/nowhere", 404, "application/json", `{"detail":"Not Found"}`},
		{"DELETE", "/me", 405, "application/json", `{"detail":"Method Not Allowed"}`},
		{"GET", "/boom", 500, "text/plain; charset=utf-8", "Internal Server Error"},
		{"GET", "/empty", 204, "", ""},
	}
	for _, c := range cases {
		g := do(t, k, c.method, c.path, "")
		if g.status != c.status || g.ctype != c.ctype || g.body != c.body || g.count != "0" {
			t.Errorf("%s %s: got %d %q %q count %s", c.method, c.path, g.status, g.ctype, g.body, g.count)
		}
		if c.path == "/boom" && g.header.Get("X-Partial") != "" {
			t.Errorf("a crashed handler's headers leaked: %v", g.header)
		}
	}
}

// TestServeCLI runs Serve in a child process: `--db`, `--addr`, one LISTENING line.
func TestServeCLI(t *testing.T) {
	if os.Getenv("KIT_SERVE_CHILD") == "1" {
		os.Args = append([]string{"app"}, strings.Fields(os.Getenv("KIT_SERVE_ARGS"))...)
		Serve(register)
		return
	}
	dir := t.TempDir()
	db := filepath.Join(dir, "cli.db")
	loadSchema(t, db)
	cmd := exec.Command(os.Args[0], "-test.run=^TestServeCLI$")
	cmd.Env = append(os.Environ(), "KIT_SERVE_CHILD=1", "LATTICE_TRACE_SQL=1",
		"KIT_SERVE_ARGS=--db "+db+" --addr 127.0.0.1:0")
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait() })
	line, err := bufio.NewReader(out).ReadString('\n')
	if err != nil || !strings.HasPrefix(line, "LISTENING 127.0.0.1:") {
		t.Fatalf("first line %q (%v)", line, err)
	}
	addr := strings.TrimSpace(strings.TrimPrefix(line, "LISTENING "))
	req, _ := http.NewRequest("GET", "http://"+addr+"/seller", nil)
	req.Header.Set("Authorization", "Bearer tok-a")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 403 || string(b) != `{"error":"forbidden"}` ||
		resp.Header.Get("X-Lattice-Sql-Count") != "1" || resp.Header.Get("Content-Length") != "21" {
		t.Errorf("got %d %q %v", resp.StatusCode, b, resp.Header)
	}
}
