package rt

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
	"testing"
	"time"
)

func memoryConn(t *testing.T) *dbConn {
	t.Helper()
	RegisterFunctions()
	db, err := sql.Open("sqlite", "file::memory:")
	if err != nil {
		t.Fatal(err)
	}
	conn, err := db.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close(); _ = db.Close() })
	return &dbConn{conn: conn}
}

// TestStoredSpellings: a value stored in a column declared DATE / DATETIME / TIMESTAMP
// comes back as the stored text (Python's sqlite3 does not parse it), so its wire value
// and its rows_json rebind are Python's.
func TestStoredSpellings(t *testing.T) {
	c := memoryConn(t)
	c.exec(`CREATE TABLE s (k INTEGER PRIMARY KEY, d DATE, dt DATETIME, ts TIMESTAMP, x TEXT)`)
	for _, sp := range loadAnswers(t).Spellings {
		c.exec(`INSERT INTO s (d, dt, ts, x) VALUES (?, ?, ?, ?)`, sp.In, sp.In, sp.In, sp.In)
		r := c.query(`SELECT d, dt, ts, x FROM s ORDER BY k DESC LIMIT 1`)
		row := r.Rows[0]
		for i, want := range sp.Row {
			if got := tagOf(row[i]); got != want.spelled() {
				t.Errorf("%q column %s: got %q, want %q", sp.In, r.Cols[i], got, want.spelled())
			}
			scalar := "DateTime"
			if i == 0 {
				scalar = "Date"
			}
			if got := tagOf(Finite(Decode(row[i], scalar))); got != sp.Wire[i].spelled() {
				t.Errorf("%q column %s wire: got %q, want %q", sp.In, r.Cols[i], got, sp.Wire[i].spelled())
			}
		}
		if got := RowsJSON(r, []string{"d", "dt", "ts", "x"}); got != sp.RowsJSON {
			t.Errorf("%q rows_json: got %s, want %s", sp.In, got, sp.RowsJSON)
		}
	}
}

func cellsOf(tags []tagged) []any {
	out := make([]any, len(tags))
	for i, tg := range tags {
		out[i] = tg.value()
	}
	return out
}

func toTime(t DateTime) time.Time { return time.UnixMicro(int64(t)).UTC() }

// TestOutboxDispatch replays the prelude's dispatch_outbox: the same rows, instants and
// effects give the same statement count, outcome and outbox rows.
func TestOutboxDispatch(t *testing.T) {
	ob := loadAnswers(t).Outbox
	for name, want := range map[string]string{"OUTBOX_DDL": OutboxDDL, "OUTBOX_COLUMNS": OutboxColumns,
		"OUTBOX_ADD_CLAIMED": OutboxAddClaimed, "OUTBOX_CLAIM": OutboxClaim, "OUTBOX_DONE": OutboxDone,
		"OUTBOX_FAILED": OutboxFailed, "OUTBOX_EXISTS": OutboxExists, "OUTBOX_RETRY": OutboxRetry,
		"OUTBOX_RETRY_PENDING": OutboxRetryAll} {
		if ob.Constants[name] != want {
			t.Errorf("%s differs from lattice.api.outbox:\n%s\n%s", name, want, ob.Constants[name])
		}
	}
	c := memoryConn(t)
	c.exec(strings.Replace(OutboxDDL, `, "claimed_at" TEXT`, "", 1)) // a table from before claimed_at
	var calls []string
	flaky := func(args []any) error { return &Error{Kind: "ValueError", Msg: "flaky failed"} }
	app := &App{Settings: Settings{Effects: map[string]Effect{
		"send": func(args []any) error {
			parts := make([]string, len(args))
			for i, a := range args {
				parts[i] = tagOf(a)
			}
			calls = append(calls, strings.Join(parts, " "))
			return nil
		},
		"flaky": func(args []any) error { return flaky(args) },
	}}}
	t0, _ := ParseDateTime(ob.Steps[0].Now)
	args := `{"types": ["Str", "Int", "Uuid", "DateTime", "Bool", "Date", "Float"], "values": ["ann", 1, "a0eebc999c0b4ef8bb6d6bb9bd380a11", "2026-09-28 10:00:00", 1, "2026-09-28", 2]}`
	for _, native := range []string{"send", "flaky", "missing"} {
		c.exec(`INSERT INTO "lattice_outbox" ("endpoint", "native", "args", "status", "error", "created_at", "attempts") VALUES (?, ?, ?, ?, NULL, ?, 0)`,
			"notify", native, args, "pending", t0.Text())
	}
	if got := outboxCutoff(toTime(t0), OutboxGrace); got != ob.Cutoff {
		t.Errorf("cutoff %q, want %q", got, ob.Cutoff)
	}
	for i, st := range ob.Steps {
		now, _ := ParseDateTime(st.Now)
		rows := make([][]any, len(st.Rows))
		for k, r := range st.Rows {
			rows[k] = cellsOf(r)
		}
		if i == 2 { // the retry: the rows Go selects are the rows Python selected
			got := c.query(OutboxRetry, outboxCutoff(toTime(now), OutboxGrace)).Rows
			if fmt.Sprint(got) != fmt.Sprint(rows) {
				t.Errorf("retry rows %v, want %v", got, rows)
			}
			flaky = app.Settings.Effects["send"] // as the Python side swaps in _send
		}
		before := c.queries
		tm := toTime(now)
		done, failed := app.DispatchOutbox(c, rows, &tm, OutboxGrace)
		if done != st.Done || failed != st.Failed || c.queries-before != st.Queries {
			t.Errorf("%s: %d done, %d failed, %d statements; want %d, %d, %d", st.Label, done, failed,
				c.queries-before, st.Done, st.Failed, st.Queries)
		}
		after := c.query(`SELECT "id", "native", "status", "error", "attempts", "claimed_at" FROM "lattice_outbox" ORDER BY "id"`).Rows
		for k, r := range st.After {
			for j, want := range r {
				if got := tagOf(after[k][j]); got != want.spelled() {
					t.Errorf("%s row %d col %d: got %q, want %q", st.Label, k, j, got, want.spelled())
				}
			}
		}
	}
	// the effect receives the arguments as Python's _native_arg decodes them:
	// ('ann', 1, UUID(...), datetime(2026, 9, 28, 10, 0, tzinfo=UTC), True, date(2026, 9, 28), 2.0)
	want := "str:ann int:1 uuid:a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11 datetime:2026-09-28T10:00:00.000000Z bool:true date:2026-09-28 float:2.0"
	if len(calls) != len(ob.Calls) || calls[0] != want || calls[1] != want {
		t.Errorf("effect calls %v, want %d like %s (Python: %v)", calls, len(ob.Calls), want, ob.Calls)
	}
}
