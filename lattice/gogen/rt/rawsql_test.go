package rt

import (
	"context"
	"database/sql"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"testing"

	sqlite3 "modernc.org/sqlite/lib"
)

// TestHandleOnRealConnection fails when modernc.org/sqlite changes the connection
// layout rawsql.go relies on (handle panics) or hands back an unusable handle.
func TestHandleOnRealConnection(t *testing.T) {
	RegisterFunctions()
	db, err := sql.Open("sqlite", "file::memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	conn, err := db.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.Raw(func(dc any) error {
		h, tls := handle(dc)
		if h == 0 || tls == nil {
			t.Fatalf("handle() = %#x, %v", h, tls)
		}
		if sqlite3.Xsqlite3_get_autocommit(tls, h) != 1 {
			t.Errorf("sqlite3_get_autocommit on the handle is not 1")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	defer func() {
		r, _ := recover().(string)
		if !strings.Contains(r, "modernc.org/sqlite connection layout changed; update rawsql.go") {
			t.Errorf("handle(non-conn) must panic loudly, got %q", r)
		}
	}()
	handle(&struct{ db, tls uintptr }{})
}

// TestLibcMatchesSqlite: go.mod's modernc.org/libc must be the version
// modernc.org/sqlite requires (rawsql.go calls both with the same TLS and memory).
func TestLibcMatchesSqlite(t *testing.T) {
	goMod, err := exec.Command("go", "list", "-m", "-f", "{{.GoMod}}", "modernc.org/sqlite").Output()
	if err != nil {
		t.Skipf("go list: %v", err)
	}
	data, err := os.ReadFile(strings.TrimSpace(string(goMod)))
	if err != nil {
		t.Fatal(err)
	}
	m := regexp.MustCompile(`(?m)^\s*modernc\.org/libc (v\S+)`).FindSubmatch(data)
	if m == nil {
		t.Fatal("modernc.org/sqlite's go.mod names no modernc.org/libc")
	}
	ours, err := exec.Command("go", "list", "-m", "-f", "{{.Version}}", "modernc.org/libc").Output()
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.TrimSpace(string(ours)); got != string(m[1]) {
		t.Errorf("modernc.org/libc is %s; modernc.org/sqlite requires %s", got, m[1])
	}
}

// TestSQLTraceLogsExpandedCountedStatements: with a trace writer set (the app's
// LATTICE_TRACE_SQL_LOG), every counted data statement is written once, as SQLite
// expands it with its bound values (what Python's sqlite3 trace callback sees), one
// JSON string per line; transaction control and DDL are not written.
func TestSQLTraceLogsExpandedCountedStatements(t *testing.T) {
	RegisterFunctions()
	db, err := sql.Open("sqlite", "file::memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	conn, err := db.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	var buf strings.Builder
	SetSQLTrace(&buf)
	defer SetSQLTrace(nil)
	c := &dbConn{conn: conn}
	c.exec("CREATE TABLE t (x, y)")
	c.exec("BEGIN")
	c.exec("INSERT INTO t VALUES (?, ?)", int64(5), "it's")
	c.query("SELECT x FROM t WHERE y = ?", "it's")
	c.exec("COMMIT")
	want := "\"INSERT INTO t VALUES (5, 'it''s')\"\n\"SELECT x FROM t WHERE y = 'it''s'\"\n"
	if buf.String() != want {
		t.Errorf("trace = %q, want %q", buf.String(), want)
	}
	if c.queries != 2 {
		t.Errorf("queries = %d, want 2", c.queries)
	}
}
