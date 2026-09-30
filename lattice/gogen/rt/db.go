package rt

import (
	"database/sql"
	"database/sql/driver"
	"encoding/json"
	"errors"
	"io"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"

	"modernc.org/sqlite"
)

// ---- storage spellings (process-wide, like the prelude's module constants) --------------

// Storage is how the database keeps Uuid and DateTime values and how responses spell a
// DateTime: the prelude's UUID_STORAGE ("canonical" | "hex32"), DATETIME_STORAGE
// ("iso_z" | "sql_space") and DATETIME_WIRE ("iso_z" | "naive"). The SQLite functions
// read it, so it is process-wide; Serve sets it from Settings.
var Storage = struct{ UUID, DateTime, Wire string }{"canonical", "iso_z", "iso_z"}

// StoredUUID is the prelude's stored_uuid: a UUID as the storage keeps it.
func StoredUUID(canonical string) string {
	if Storage.UUID == "canonical" {
		return canonical
	}
	return strings.ReplaceAll(canonical, "-", "")
}

// StoredDateTime is the prelude's stored_datetime.
func StoredDateTime(t DateTime) string {
	if Storage.DateTime == "iso_z" {
		return t.Text()
	}
	return t.isoNaive(" ", true)
}

// WireDateTime is the prelude's wire_datetime: a DateTime as responses spell it.
func WireDateTime(t DateTime) string {
	if Storage.Wire == "naive" {
		return t.isoNaive("T", false)
	}
	return t.Text()
}

// SQLValue is the prelude's sql_value: a parameter value as SQLite stores it (Bool 0/1,
// Date ISO text, DateTime and Uuid in the storage spellings; Unset is null).
func SQLValue(v any) any {
	switch x := v.(type) {
	case unsetT, nullT:
		return nil
	case bool:
		if x {
			return int64(1)
		}
		return int64(0)
	case int:
		return int64(x)
	case DateTime:
		return StoredDateTime(x)
	case Date:
		return x.ISO()
	case UUID:
		return StoredUUID(string(x))
	}
	return v
}

// Sent is the prelude's sent: 1 when the optional parameter was sent.
func Sent(values map[string]any, name string) int64 {
	if _, unset := values[name].(unsetT); unset {
		return 0
	}
	return 1
}

// Bind is a named bind parameter (:name in the SQL). Unlike database/sql's NamedArg
// its name may start with an underscore, as the compiled SQL's :__user_id does.
type Bind struct {
	Name  string
	Value any
}

// B is a named bind of a value (converted by SQLValue when the statement runs).
func B(name string, v any) Bind { return Bind{Name: name, Value: v} }

// ---- SQL errors ----------------------------------------------------------------------------

// SQLError is a failed statement, named as Python's sqlite3 names its exceptions.
type SQLError struct {
	Kind string // IntegrityError, OperationalError, ...
	Msg  string // sqlite3_errmsg, as str() of the Python exception
	Code int    // the (extended) SQLite result code; 0 when not from SQLite
}

func (e *SQLError) Error() string { return e.Kind + ": " + e.Msg }

var reDriverMsg = regexp.MustCompile(`^(?s)[^:]*: (.*) \(\d+\)( \(SQLITE_BUSY\))?$`)

// sqlError is the Python exception of a driver error.
func sqlError(err error) *SQLError {
	var se *sqlite.Error
	if !errors.As(err, &se) {
		return &SQLError{Kind: "ProgrammingError", Msg: err.Error()}
	}
	msg := se.Error()
	if m := reDriverMsg.FindStringSubmatch(msg); m != nil {
		msg = m[1]
	}
	return &SQLError{Kind: pyErrorKind(se.Code()), Msg: msg, Code: se.Code()}
}

// IsUniqueViolation is the prelude's _is_unique_violation.
func IsUniqueViolation(err any) bool {
	e, ok := err.(*SQLError)
	return ok && e.Kind == "IntegrityError" && strings.HasPrefix(e.Msg, "UNIQUE constraint failed")
}

// sqlTrace receives the statement trace (SetSQLTrace); nil: no trace.
var sqlTrace struct {
	sync.Mutex
	w io.Writer
}

// SetSQLTrace makes every counted data statement be written to w (nil: none) as
// SQLite expands it with its bound values (sqlite3_expanded_sql, the text Python's
// sqlite3 trace callback is given), one JSON string per line, before it first steps.
// A test tool (the app's LATTICE_TRACE_SQL_LOG): the equivalence gate compares it with
// the Python runtime's trace, statement by statement.
func SetSQLTrace(w io.Writer) {
	sqlTrace.Lock()
	defer sqlTrace.Unlock()
	sqlTrace.w = w
	sqlTraceEnabled.Store(w != nil)
}

// sqlTraceOn is the per-statement check: one atomic load, no lock (SetSQLTrace, called
// once by NewApp, sets it).
func sqlTraceOn() bool { return sqlTraceEnabled.Load() }

var sqlTraceEnabled atomic.Bool

func traceStatement(text string) {
	line, _ := json.Marshal(text)
	sqlTrace.Lock()
	defer sqlTrace.Unlock()
	if sqlTrace.w != nil {
		_, _ = sqlTrace.w.Write(append(line, '\n'))
	}
}

// CountedStatement is bench.api_runner._is_counted_statement: a data statement;
// transaction control and pragmas are not counted.
func CountedStatement(sqlText string) bool {
	f := strings.Fields(sqlText)
	if len(f) == 0 {
		return false
	}
	switch strings.ToUpper(f[0]) {
	case "SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "REPLACE":
		return true
	}
	return false
}

// ---- the request's connection --------------------------------------------------------------

// Result is a statement's rows: (columns, rows), each cell as SQLite gives it (int64,
// float64, string, []byte or nil).
type Result struct {
	Cols []string
	Rows [][]any
}

// dbConn is one request's connection, counting the data statements it runs.
type dbConn struct {
	conn    *sql.Conn
	queries int
}

func driverArgs(args []any) []driver.NamedValue {
	out := make([]driver.NamedValue, len(args))
	for i, a := range args {
		if b, ok := a.(Bind); ok {
			out[i] = driver.NamedValue{Name: b.Name, Ordinal: i + 1, Value: SQLValue(b.Value)}
		} else {
			out[i] = driver.NamedValue{Ordinal: i + 1, Value: SQLValue(a)}
		}
	}
	return out
}

// run executes one statement on the request's connection through rawStatement.
func (c *dbConn) run(sqlText string, args []any) (Result, int64) {
	if CountedStatement(sqlText) {
		c.queries++
	}
	var res Result
	var n int64
	var serr *SQLError
	if err := c.conn.Raw(func(dc any) error {
		res, n, serr = rawStatement(dc, sqlText, driverArgs(args))
		return nil
	}); err != nil {
		panic(sqlError(err))
	}
	if serr != nil {
		panic(serr)
	}
	return res, n
}

// query runs one statement and returns its rows.
func (c *dbConn) query(sqlText string, args ...any) Result {
	res, _ := c.run(sqlText, args)
	return res
}

// exec runs one statement and returns the number of rows it changed.
func (c *dbConn) exec(sqlText string, args ...any) int64 {
	_, n := c.run(sqlText, args)
	return n
}

// Ctx is one request as a generated handler sees it: its connection (inside the
// request's transaction), the caller (nil for a Public endpoint), the parameters, the
// request's instant (fn.now()), its T2 fuel and the outbox rows it collects.
type Ctx struct {
	db      *dbConn
	EP      *Endpoint
	Caller  *Caller
	Values  map[string]any
	Now     DateTime
	Fuel    *Fuel
	Pending [][]any // outbox rows (id, native, args), dispatched after COMMIT
}

// Query runs one statement and returns its rows (the prelude's run). A failure panics
// with *SQLError (a UNIQUE violation becomes the 409 conflict answer).
func (c *Ctx) Query(sqlText string, args ...any) Result { return c.db.query(sqlText, args...) }

// Exec runs one statement whose rows are not needed (DDL, e.g. OutboxDDL).
func (c *Ctx) Exec(sqlText string, args ...any) { c.db.exec(sqlText, args...) }

// Write is the prelude's write: a write with unique_or=Conflict(...), answering
// conflict on a UNIQUE violation.
func (c *Ctx) Write(sqlText string, conflict *ErrorResp, args ...any) Result {
	var res Result
	func() {
		defer func() {
			if r := recover(); r != nil {
				if IsUniqueViolation(r) {
					panic(conflict)
				}
				panic(r)
			}
		}()
		res = c.db.query(sqlText, args...)
	}()
	return res
}

// Queries is the number of data statements the request has run so far.
func (c *Ctx) Queries() int { return c.db.queries }
