package rt

import (
	"database/sql/driver"
	"fmt"
	"reflect"
	"strconv"
	"strings"
	"unsafe"

	"modernc.org/libc"
	"modernc.org/libc/sys/types"
	sqlite3 "modernc.org/sqlite/lib"
)

// Statements run through SQLite's C API (modernc's transpilation) on the driver
// connection's own handle, not through the driver's Rows: the driver turns TEXT in a
// column declared DATE / DATETIME / TIMESTAMP into a time.Time, losing the stored
// spelling, and offers no switch to turn that off. Python's sqlite3 returns the stored
// value as it is (int, float, str, bytes, None), and so does rawStatement.

// handle is the sqlite3* and TLS of a modernc driver connection (its unexported db and
// tls fields; the module version is pinned by go.sum).
// It panics when the layout is not the expected one, rather than misuse memory.
func handle(dc any) (uintptr, *libc.TLS) {
	t := reflect.TypeOf(dc)
	if t == nil || t.Kind() != reflect.Pointer || t.Elem().Kind() != reflect.Struct ||
		t.Elem().PkgPath() != "modernc.org/sqlite" || t.Elem().Name() != "conn" {
		panic(layoutChanged(fmt.Sprintf("the driver connection is %v, not *sqlite.conn", t)))
	}
	v := reflect.ValueOf(dc).Elem()
	dbField, tlsField := v.FieldByName("db"), v.FieldByName("tls")
	if !dbField.IsValid() || dbField.Kind() != reflect.Uintptr {
		panic(layoutChanged("conn.db is not a uintptr"))
	}
	if !tlsField.IsValid() || tlsField.Type() != reflect.TypeFor[*libc.TLS]() {
		panic(layoutChanged("conn.tls is not a *libc.TLS"))
	}
	return uintptr(dbField.Uint()), (*libc.TLS)(unsafe.Pointer(tlsField.Pointer()))
}

func layoutChanged(what string) string {
	return "modernc.org/sqlite connection layout changed; update rawsql.go (" + what + ")"
}

// rawError is the Python sqlite3 exception for the connection's last error.
func rawError(tls *libc.TLS, db uintptr) *SQLError {
	code := int(sqlite3.Xsqlite3_extended_errcode(tls, db))
	return &SQLError{Kind: pyErrorKind(code), Msg: libc.GoString(sqlite3.Xsqlite3_errmsg(tls, db)), Code: code}
}

// pyErrorKind is the sqlite3 exception class Python raises for a result code.
func pyErrorKind(code int) string {
	switch code & 0xff {
	case 1, 3, 4, 5, 6, 8, 9, 10, 13, 14, 15, 16, 17:
		return "OperationalError"
	case 2, 12:
		return "InternalError"
	case 7:
		return "MemoryError"
	case 18:
		return "DataError"
	case 19, 20:
		return "IntegrityError"
	case 21, 25:
		return "InterfaceError"
	}
	return "DatabaseError"
}

// rawStatement prepares, binds, steps and finalizes one statement: its columns, rows
// (int64, float64, string, []byte or nil per cell) and sqlite3_changes().
func rawStatement(dc any, sqlText string, args []driver.NamedValue) (Result, int64, *SQLError) {
	db, tls := handle(dc)
	res := Result{Cols: []string{}, Rows: [][]any{}}
	var allocs []uintptr
	defer func() {
		for _, p := range allocs {
			libc.Xfree(tls, p)
		}
	}()
	alloc := func(n int) uintptr {
		p := libc.Xmalloc(tls, types.Size_t(n))
		allocs = append(allocs, p)
		return p
	}
	cstr := func(s string) uintptr {
		p, err := libc.CString(s)
		if err != nil {
			panic(err)
		}
		allocs = append(allocs, p)
		return p
	}
	zSQL := cstr(sqlText)
	pp, ptail := alloc(8), alloc(8)
	if rc := sqlite3.Xsqlite3_prepare_v2(tls, db, zSQL, -1, pp, ptail); rc != sqlite3.SQLITE_OK {
		return res, 0, rawError(tls, db)
	}
	pstmt := libc.AtomicLoadPUintptr(pp)
	tail := libc.GoString(libc.AtomicLoadPUintptr(ptail))
	if pstmt == 0 { // only whitespace or comments
		return res, 0, nil
	}
	defer sqlite3.Xsqlite3_finalize(tls, pstmt)
	if strings.TrimSpace(tail) != "" {
		return res, 0, &SQLError{Kind: "ProgrammingError", Msg: "You can only execute one statement at a time."}
	}
	if e := rawBind(tls, db, pstmt, args, cstr); e != nil {
		return res, 0, e
	}
	if sqlTraceOn() && CountedStatement(sqlText) {
		p := sqlite3.Xsqlite3_expanded_sql(tls, pstmt)
		traceStatement(libc.GoString(p))
		sqlite3.Xsqlite3_free(tls, p)
	}
	n := int(sqlite3.Xsqlite3_column_count(tls, pstmt))
	for i := 0; i < n; i++ {
		res.Cols = append(res.Cols, libc.GoString(sqlite3.Xsqlite3_column_name(tls, pstmt, int32(i))))
	}
	for {
		switch rc := sqlite3.Xsqlite3_step(tls, pstmt); rc {
		case sqlite3.SQLITE_ROW:
			row := make([]any, n)
			for i := range row {
				row[i] = rawColumn(tls, pstmt, int32(i))
			}
			res.Rows = append(res.Rows, row)
		case sqlite3.SQLITE_DONE:
			return res, int64(sqlite3.Xsqlite3_changes(tls, db)), nil
		default:
			return res, 0, rawError(tls, db)
		}
	}
}

func rawColumn(tls *libc.TLS, pstmt uintptr, i int32) any {
	switch sqlite3.Xsqlite3_column_type(tls, pstmt, i) {
	case sqlite3.SQLITE_INTEGER:
		return int64(sqlite3.Xsqlite3_column_int64(tls, pstmt, i))
	case sqlite3.SQLITE_FLOAT:
		return float64(sqlite3.Xsqlite3_column_double(tls, pstmt, i))
	case sqlite3.SQLITE_TEXT:
		p := sqlite3.Xsqlite3_column_text(tls, pstmt, i)
		n := int(sqlite3.Xsqlite3_column_bytes(tls, pstmt, i))
		if p == 0 || n == 0 {
			return ""
		}
		return string(libc.GoBytes(p, n))
	case sqlite3.SQLITE_BLOB:
		p := sqlite3.Xsqlite3_column_blob(tls, pstmt, i)
		n := int(sqlite3.Xsqlite3_column_bytes(tls, pstmt, i))
		if p == 0 || n == 0 {
			return []byte{}
		}
		return append([]byte(nil), libc.GoBytes(p, n)...)
	}
	return nil
}

// rawBind binds the arguments as Python's sqlite3 does with a mapping (named
// parameters, extra names ignored) or a sequence (positional parameters).
func rawBind(tls *libc.TLS, db, pstmt uintptr, args []driver.NamedValue, cstr func(string) uintptr) *SQLError {
	count := int(sqlite3.Xsqlite3_bind_parameter_count(tls, pstmt))
	named := len(args) > 0 && args[0].Name != ""
	if !named && count != len(args) {
		return &SQLError{Kind: "ProgrammingError", Msg: "Incorrect number of bindings supplied. The current statement uses " +
			itoa(count) + ", and there are " + itoa(len(args)) + " supplied."}
	}
	for i := 1; i <= count; i++ {
		var v any
		if named {
			name := libc.GoString(sqlite3.Xsqlite3_bind_parameter_name(tls, pstmt, int32(i)))
			found := false
			for _, a := range args {
				if name != "" && a.Name == name[1:] {
					v, found = a.Value, true
					break
				}
			}
			if !found {
				return &SQLError{Kind: "ProgrammingError", Msg: "You did not supply a value for binding parameter " + name + "."}
			}
		} else {
			v = args[i-1].Value
		}
		var rc int32
		switch x := v.(type) {
		case nil:
			rc = sqlite3.Xsqlite3_bind_null(tls, pstmt, int32(i))
		case int64:
			rc = sqlite3.Xsqlite3_bind_int64(tls, pstmt, int32(i), x)
		case float64:
			rc = sqlite3.Xsqlite3_bind_double(tls, pstmt, int32(i), x)
		case string:
			p := cstr(x)
			rc = sqlite3.Xsqlite3_bind_text(tls, pstmt, int32(i), p, int32(len(x)), 0)
		case []byte:
			p := cstr(string(x))
			rc = sqlite3.Xsqlite3_bind_blob(tls, pstmt, int32(i), p, int32(len(x)), 0)
		default:
			return &SQLError{Kind: "ProgrammingError", Msg: "Error binding parameter " + itoa(i) + ": type '" +
				reflect.TypeOf(v).String() + "' is not supported"}
		}
		if rc != sqlite3.SQLITE_OK {
			return rawError(tls, db)
		}
	}
	return nil
}

func itoa(n int) string { return strconv.Itoa(n) }
