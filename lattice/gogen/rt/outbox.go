package rt

import (
	"context"
	"fmt"
	"log"
	"math/big"
	"sort"
	"strings"
	"time"
)

// The outbox (for_each; a port of lattice/api/outbox.py, as the prelude copies it).
// The statements are lattice.api.outbox's, verbatim.
const (
	OutboxTable       = "lattice_outbox"
	OutboxDDL         = `CREATE TABLE IF NOT EXISTS "lattice_outbox" ("id" INTEGER PRIMARY KEY, "endpoint" TEXT NOT NULL, "native" TEXT NOT NULL, "args" TEXT NOT NULL, "status" TEXT NOT NULL, "error" TEXT, "created_at" TEXT NOT NULL, "attempts" INTEGER NOT NULL, "claimed_at" TEXT)`
	OutboxColumns     = `SELECT "name" FROM pragma_table_info('lattice_outbox')`
	OutboxAddClaimed  = `ALTER TABLE "lattice_outbox" ADD COLUMN "claimed_at" TEXT`
	outboxStale       = `julianday(COALESCE("claimed_at", "created_at")) <= julianday(?)`
	OutboxClaim       = `UPDATE "lattice_outbox" SET "status" = 'dispatching', "attempts" = "attempts" + 1, "claimed_at" = ? WHERE "id" = ? AND ("status" IN ('pending', 'failed') OR ("status" = 'dispatching' AND ` + outboxStale + `))`
	OutboxDone        = `UPDATE "lattice_outbox" SET "status" = 'done', "error" = NULL WHERE "id" = ? AND "status" = 'dispatching' AND "claimed_at" = ?`
	OutboxFailed      = `UPDATE "lattice_outbox" SET "status" = 'failed', "error" = ? WHERE "id" = ? AND "status" = 'dispatching' AND "claimed_at" = ?`
	OutboxExists      = `SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'lattice_outbox'`
	OutboxRetry       = `SELECT "id", "native", "args" FROM "lattice_outbox" WHERE "status" = 'failed' OR ("status" IN ('pending', 'dispatching') AND ` + outboxStale + `) ORDER BY "id"`
	OutboxRetryAll    = `SELECT "id", "native", "args" FROM "lattice_outbox" WHERE "status" IN ('failed', 'pending') OR ("status" = 'dispatching' AND ` + outboxStale + `) ORDER BY "id"`
	OutboxErrorLimit  = 500
	OutboxGrace       = 300 * time.Second // a younger claim (or unclaimed row) may still be in flight
	OutboxMarkTries   = 3                 // a failing done / failed mark is tried this often, then logged
	OutboxMarkBackoff = 50 * time.Millisecond
)

// Effect is an effect native: called once per outbox row after COMMIT with the row's
// arguments (nil, bool, int64, float64, string, Date, DateTime or UUID). A returned
// error (or a panic) marks the row failed with its text.
type Effect func(args []any) error

// outboxArgs is the prelude's _outbox_args: the arguments of an outbox row's
// {"types": [...], "values": [...]} JSON, as the native receives them.
func outboxArgs(args string) ([]any, error) {
	v, err := LoadsJSON([]byte(args))
	if err != nil {
		return nil, err
	}
	obj, ok := v.(*JSONObj)
	if !ok {
		return nil, fmt.Errorf("outbox args are not an object")
	}
	types, _ := obj.Vals["types"].([]any)
	values, _ := obj.Vals["values"].([]any)
	if len(types) != len(values) {
		return nil, fmt.Errorf("zip() argument 2 is shorter than argument 1")
	}
	out := make([]any, len(values))
	for i, raw := range values {
		t, _ := types[i].(JSONStr)
		out[i] = nativeArg(raw, t.S)
	}
	return out, nil
}

// nativeArg is the prelude's _native_arg (a null stays nil).
func nativeArg(v any, scalar string) any {
	if v == nil {
		return nil
	}
	if s, ok := v.(JSONStr); ok {
		v = s.S
	}
	if b, ok := v.(*big.Int); ok {
		f, _ := new(big.Float).SetInt(b).Float64()
		v = f
	}
	switch scalar {
	case "Bool":
		return pyTruth(v)
	case "Int":
		switch x := v.(type) {
		case float64:
			return int64(x)
		case string:
			return ParseInt(x)
		}
	case "Float":
		if x, ok := v.(int64); ok {
			return float64(x)
		}
	case "Date":
		if d, ok := ParseISODate(pyStr(v)); ok {
			return d
		}
		Raise("ValueError", "Invalid isoformat string: "+ReprStr(pyStr(v)))
	case "DateTime":
		if t, ok := ParseDateTime(pyStr(v)); ok {
			return t
		}
		return nil
	case "Uuid":
		if u, ok := UUIDText(pyStr(v)); ok {
			return UUID(u)
		}
		Raise("ValueError", "badly formed hexadecimal UUID string")
	}
	return v
}

func outboxCutoff(now time.Time, grace time.Duration) string {
	return DateTimeOf(now.Add(-grace)).isoNaive("T", false) + "+00:00"
}

// outboxClaimedAt is the prelude's _outbox_claimed_at: add the claimed_at column to an
// outbox table created without it.
func outboxClaimedAt(db *dbConn) {
	for _, row := range db.query(OutboxColumns).Rows {
		if row[0] == "claimed_at" {
			return
		}
	}
	func() {
		defer func() {
			if r := recover(); r != nil {
				if e, ok := r.(*SQLError); !ok || !strings.Contains(e.Msg, "duplicate column") {
					panic(r)
				}
			}
		}()
		db.exec(OutboxAddClaimed)
	}()
}

// outboxMark is the prelude's _outbox_mark: a done / failed mark, tried again briefly;
// still failing, it is logged (the row stays claimed; a retry re-sends it later).
func outboxMark(db *dbConn, sqlText string, args []any, rowID any) {
	for attempt := 0; attempt < OutboxMarkTries; attempt++ {
		var failure any
		func() {
			defer func() { failure = recover() }()
			db.exec(sqlText, args...)
		}()
		if failure == nil {
			return
		}
		if _, ok := failure.(*SQLError); !ok {
			panic(failure)
		}
		if attempt+1 == OutboxMarkTries {
			log.Printf("lattice.outbox: could not mark outbox row %v (%v); a retry re-sends it after the grace period", rowID, failure)
			return
		}
		time.Sleep(OutboxMarkBackoff << attempt)
	}
}

// DispatchOutbox is the prelude's dispatch_outbox: claim each (id, native, args) row,
// in id order, then call its effect and mark the row done or failed with the error.
// A row another dispatcher holds is skipped. Never panics for a failing effect or
// mark. Returns (done, failed).
func (a *App) DispatchOutbox(db *dbConn, rows [][]any, now *time.Time, grace time.Duration) (done, failed int) {
	if len(rows) == 0 {
		return 0, 0
	}
	outboxClaimedAt(db)
	sorted := append([][]any(nil), rows...)
	sort.SliceStable(sorted, func(i, j int) bool { return toInt64(sorted[i][0]) < toInt64(sorted[j][0]) })
	for _, row := range sorted {
		rowID, native := row[0], pyStr(row[1])
		args, _ := row[2].(string)
		stamp := time.Now().UTC()
		if now != nil {
			stamp = *now
		}
		claimedAt := DateTimeOf(stamp).Text()
		var claimed int64
		var claimErr any
		func() {
			defer func() { claimErr = recover() }()
			claimed = db.exec(OutboxClaim, claimedAt, rowID, outboxCutoff(stamp, grace))
		}()
		if claimErr != nil {
			if _, ok := claimErr.(*SQLError); !ok {
				panic(claimErr)
			}
			log.Printf("lattice.outbox: could not claim outbox row %v (%v)", rowID, claimErr)
			continue
		}
		if claimed != 1 {
			continue // another dispatcher holds it, or it is done
		}
		errText := ""
		fn := a.Settings.Effects[native]
		if fn == nil {
			errText = "no implementation for native " + ReprStr(native)
		} else {
			errText = runEffect(fn, args)
		}
		if errText == "" {
			outboxMark(db, OutboxDone, []any{rowID, claimedAt}, rowID)
			done++
		} else {
			outboxMark(db, OutboxFailed, []any{errText, rowID, claimedAt}, rowID)
			failed++
		}
	}
	return done, failed
}

// runEffect calls an effect with a row's arguments: "" on success, else the error as
// f"{type(err).__name__}: {err}" (at most OutboxErrorLimit characters).
func runEffect(fn Effect, args string) (errText string) {
	defer func() {
		if r := recover(); r != nil {
			errText = truncRunes(panicText(r), OutboxErrorLimit)
		}
	}()
	decoded, err := outboxArgs(args)
	if err == nil {
		err = fn(decoded)
	}
	if err != nil {
		return truncRunes(errorText(err), OutboxErrorLimit)
	}
	return ""
}

func errorText(err error) string {
	switch e := err.(type) {
	case *Error, *SQLError:
		return e.Error()
	}
	return "Exception: " + err.Error()
}

func panicText(r any) string {
	if err, ok := r.(error); ok {
		return errorText(err)
	}
	return fmt.Sprintf("Exception: %v", r)
}

func truncRunes(s string, n int) string {
	rs := []rune(s)
	if len(rs) <= n {
		return s
	}
	return string(rs[:n])
}

func toInt64(v any) int64 {
	switch x := v.(type) {
	case int64:
		return x
	case int:
		return int64(x)
	}
	return 0
}

// RetryOutbox is the prelude's retry_outbox: re-send, in id order, the failed outbox
// rows and the pending / dispatching rows claimed (or created) more than grace ago;
// with pending, every pending row. Returns (done, failed).
func (a *App) RetryOutbox(pending bool, grace time.Duration, now *time.Time) (done, failed int, err error) {
	stamp := time.Now().UTC()
	if now != nil {
		stamp = *now
	}
	conn, err := a.DB.Conn(context.Background())
	if err != nil {
		return 0, 0, err
	}
	defer conn.Close()
	db := &dbConn{conn: conn}
	defer func() {
		if r := recover(); r != nil {
			if e, ok := r.(*SQLError); ok {
				err = e
				return
			}
			panic(r)
		}
	}()
	if len(db.query(OutboxExists).Rows) == 0 {
		return 0, 0, nil
	}
	outboxClaimedAt(db)
	q := OutboxRetry
	if pending {
		q = OutboxRetryAll
	}
	rows := db.query(q, outboxCutoff(stamp, grace)).Rows
	done, failed = a.DispatchOutbox(db, rows, &stamp, grace)
	return done, failed, nil
}
