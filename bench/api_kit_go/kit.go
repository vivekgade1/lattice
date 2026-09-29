// Package platformkit is the Go platform kit of the 5b pilot's arm G: the Go counterpart
// of bench/api_kit/platform_kit.py, which arm P's FastAPI scripts use.
//
// It authenticates only, exactly as the Python kit does: it opens the database, finds
// the caller from the bearer token (401 {"error":"unauthorized"}) and gates by role
// (403 {"error":"forbidden"}; Admin passes every gate). Ownership checks, input
// validation, error mapping and pagination are the handler's job, as they are the
// FastAPI script's job on arm P. Where FastAPI itself answers without the script -- an
// unrouted path (404 {"detail":"Not Found"}), a method the path does not serve (405
// {"detail":"Method Not Allowed"}), a handler that crashed (500 "Internal Server Error",
// text/plain) -- the kit answers the same way, so the two arms' frameworks agree.
//
// The model's handlers.go (package main) defines Register; the harness's main.go is
//
//	func main() { platformkit.Serve(Register) }
//
// and the binary's command line is a generated Lattice app's: `app --db <path> --addr
// <host:port>` prints one line "LISTENING <host:port>" and serves until killed.
//
// SQL counting: with LATTICE_TRACE_SQL=1 every response carries X-Lattice-Sql-Count,
// the number of data statements (SELECT / INSERT / UPDATE / DELETE / WITH / REPLACE,
// bench.api_runner._is_counted_statement) the request ran through Kit.DB -- the
// caller lookup included, as the Python harness's trace callback counts it. Requests
// are then served one at a time, so each count is its own request's.
//
// README.md documents the API for the model's prompt.
package platformkit

import (
	"bytes"
	"database/sql"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"runtime/debug"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

// Caller is the authenticated user: the users row whose token is the request's bearer
// token (platform_kit.py's current_user: id, role, email, name).
type Caller struct {
	ID    int64
	Role  string
	Email string
	Name  string
}

// AuthError is an authentication or authorization failure: the status and the error
// code of its JSON body {"error": Code} (platform_kit.py's AuthError).
type AuthError struct {
	Status int
	Code   string
}

func (e *AuthError) Error() string { return strconv.Itoa(e.Status) + " " + e.Code }

// Kit is the platform the handlers are registered with.
type Kit struct {
	// DB is the application database (SQLite, database/sql). Run every statement
	// through it: it is what counts them.
	DB *sql.DB

	trace   bool         // LATTICE_TRACE_SQL=1: send X-Lattice-Sql-Count
	serial  sync.Mutex   // with trace: one request at a time
	queries atomic.Int64 // data statements run through DB since the request began
}

// Caller finds the caller from `Authorization: Bearer <token>`. The error is a 401
// *AuthError when the header is missing, is not "Bearer <token>" or names no user;
// any other error is a database failure.
func (k *Kit) Caller(r *http.Request) (*Caller, error) {
	unauthorized := &AuthError{Status: 401, Code: "unauthorized"}
	// Starlette decodes a header value as Latin-1; the kit reads it the same way.
	auth := latin1(r.Header.Get("Authorization"))
	const scheme = "Bearer "
	if len(auth) < len(scheme) || auth[:len(scheme)] != scheme {
		return nil, unauthorized
	}
	token := pyStrip(auth[len(scheme):])
	if token == "" {
		return nil, unauthorized
	}
	var c Caller
	var role, email, name sql.NullString
	err := k.DB.QueryRowContext(r.Context(),
		"SELECT id, role, email, name FROM users WHERE token = ?", token,
	).Scan(&c.ID, &role, &email, &name)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, unauthorized
	}
	if err != nil {
		return nil, err
	}
	c.Role, c.Email, c.Name = role.String, email.String, name.String
	return &c, nil
}

// RequireRole is nil when the caller's role is one of roles or the caller is an Admin
// (Admin may call every endpoint); otherwise a 403 *AuthError.
func (k *Kit) RequireRole(c *Caller, roles ...string) error {
	if c.Role == "Admin" {
		return nil
	}
	for _, role := range roles {
		if c.Role == role {
			return nil
		}
	}
	return &AuthError{Status: 403, Code: "forbidden"}
}

// Authenticate is Caller followed, when roles are given, by RequireRole. On failure it
// has written the error response (WriteError) and returns false: the handler returns.
//
//	c, ok := k.Authenticate(w, r)            // any signed-in caller
//	c, ok := k.Authenticate(w, r, "Seller")  // a Seller (or an Admin)
func (k *Kit) Authenticate(w http.ResponseWriter, r *http.Request, roles ...string) (*Caller, bool) {
	c, err := k.Caller(r)
	if err == nil && len(roles) > 0 {
		err = k.RequireRole(c, roles...)
	}
	if err != nil {
		WriteError(w, err)
		return nil, false
	}
	return c, true
}

// WriteError answers an *AuthError as its JSON body ({"error":"unauthorized"} with
// 401, {"error":"forbidden"} with 403); any other error is a server error (500), as an
// exception is in the FastAPI arm.
func WriteError(w http.ResponseWriter, err error) {
	var ae *AuthError
	if errors.As(err, &ae) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(ae.Status)
		_, _ = fmt.Fprintf(w, `{"error":%s}`, strconv.Quote(ae.Code))
		return
	}
	log.Printf("platformkit: %v", err)
	internalError(w)
}

// Serve is the binary's main: `app --db <path> --addr <host:port>` opens the database,
// lets register add the handlers to a ServeMux, listens, prints "LISTENING
// <host:port>" and serves until killed.
func Serve(register func(mux *http.ServeMux, k *Kit)) {
	fs := flag.NewFlagSet(os.Args[0], flag.ExitOnError)
	dbPath := fs.String("db", "app.db", "the SQLite database file")
	addr := fs.String("addr", "127.0.0.1:0", "the address to listen on")
	_ = fs.Parse(os.Args[1:])
	k, err := open(*dbPath, os.Getenv("LATTICE_TRACE_SQL") == "1")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	mux := http.NewServeMux()
	register(mux, k)
	ln, err := net.Listen("tcp", *addr)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Printf("LISTENING %s\n", ln.Addr().String())
	_ = os.Stdout.Sync()
	srv := &http.Server{Handler: k.handler(mux), ReadHeaderTimeout: time.Minute}
	if err := srv.Serve(ln); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

// open is the kit on the database at path. Its connections are as Python's
// sqlite3.connect makes them: busy timeout 5 s, no other pragma.
func open(path string, trace bool) (*Kit, error) {
	k := &Kit{trace: trace}
	k.DB = sql.OpenDB(newCounter("file:"+path+"?_pragma=busy_timeout(5000)", &k.queries))
	k.DB.SetMaxIdleConns(16)
	if err := k.DB.Ping(); err != nil {
		_ = k.DB.Close()
		return nil, err
	}
	return k, nil
}

// handler serves mux: each response is buffered, so the count header can follow the
// handler; FastAPI's own answers stand in for the mux's (404, 405) and for a panic.
func (k *Kit) handler(mux *http.ServeMux) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if k.trace {
			k.serial.Lock()
			defer k.serial.Unlock()
		}
		k.queries.Store(0)
		buf := &buffered{header: http.Header{}}
		_, pattern := mux.Handler(r)
		if crashed := serveRecovered(mux, buf, r); crashed {
			buf = &buffered{header: http.Header{}}
			internalError(buf)
		} else if pattern == "" {
			fastAPIFallback(buf)
		}
		h := w.Header()
		for name, values := range buf.header {
			h[name] = values
		}
		status := buf.status
		if status == 0 {
			status = http.StatusOK
		}
		if bodyAllowed(status) {
			h.Set("Content-Length", strconv.Itoa(buf.body.Len()))
		}
		if k.trace {
			h.Set("X-Lattice-Sql-Count", strconv.FormatInt(k.queries.Load(), 10))
		}
		w.WriteHeader(status)
		if bodyAllowed(status) {
			_, _ = w.Write(buf.body.Bytes())
		}
	})
}

// serveRecovered runs the mux; true when the handler panicked (the panic and its
// stack go to stderr, as a Python traceback does).
func serveRecovered(mux *http.ServeMux, w http.ResponseWriter, r *http.Request) (crashed bool) {
	defer func() {
		if p := recover(); p != nil {
			log.Printf("platformkit: panic serving %s %s: %v\n%s", r.Method, r.URL.Path, p, debug.Stack())
			crashed = true
		}
	}()
	mux.ServeHTTP(w, r)
	return false
}

// fastAPIFallback turns the mux's own 404 and 405 into FastAPI's JSON bodies (a
// redirect, and anything else, is left as the mux wrote it).
func fastAPIFallback(b *buffered) {
	var detail string
	switch b.status {
	case http.StatusNotFound:
		detail = "Not Found"
	case http.StatusMethodNotAllowed:
		detail = "Method Not Allowed"
	default:
		return
	}
	b.header.Del("X-Content-Type-Options")
	b.header.Set("Content-Type", "application/json")
	b.body.Reset()
	fmt.Fprintf(&b.body, `{"detail":%q}`, detail)
}

// internalError is Starlette's answer to an unhandled exception.
func internalError(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(http.StatusInternalServerError)
	_, _ = w.Write([]byte("Internal Server Error"))
}

func bodyAllowed(status int) bool {
	return status >= 200 && status != http.StatusNoContent && status != http.StatusNotModified
}

// buffered is a response held until the handler returns.
type buffered struct {
	header http.Header
	status int
	body   bytes.Buffer
}

func (b *buffered) Header() http.Header { return b.header }

func (b *buffered) WriteHeader(status int) {
	if b.status == 0 {
		b.status = status
	}
}

func (b *buffered) Write(p []byte) (int, error) {
	if b.status == 0 {
		b.status = http.StatusOK
	}
	if b.header.Get("Content-Type") == "" {
		b.header.Set("Content-Type", http.DetectContentType(p))
	}
	return b.body.Write(p)
}

// latin1 is s's bytes as Latin-1 characters (how Starlette decodes header values).
func latin1(s string) string {
	ascii := true
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			ascii = false
			break
		}
	}
	if ascii {
		return s
	}
	rs := make([]rune, len(s))
	for i := 0; i < len(s); i++ {
		rs[i] = rune(s[i])
	}
	return string(rs)
}

// pyStrip is Python's str.strip(): leading and trailing characters for which
// str.isspace() is true.
func pyStrip(s string) string {
	rs := []rune(s)
	i, j := 0, len(rs)
	for i < j && pyIsSpace(rs[i]) {
		i++
	}
	for j > i && pyIsSpace(rs[j-1]) {
		j--
	}
	return string(rs[i:j])
}

func pyIsSpace(r rune) bool {
	switch {
	case r >= 0x09 && r <= 0x0d, r >= 0x1c && r <= 0x20, r == 0x85, r == 0xa0, r == 0x1680,
		r >= 0x2000 && r <= 0x200a, r == 0x2028, r == 0x2029, r == 0x202f, r == 0x205f,
		r == 0x3000:
		return true
	}
	return false
}
