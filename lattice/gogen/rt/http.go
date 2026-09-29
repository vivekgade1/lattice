package rt

import (
	"context"
	"database/sql"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"net"
	"net/http"
	"os"
	"runtime/debug"
	"strconv"
	"strings"
	"time"
)

// ---- endpoints, routes, settings -------------------------------------------------------

// Endpoint is one endpoint as the prelude's Endpoint describes it, plus its route.
// Auth is the role allowed to call it ("Public": anyone, without a token); Writes: the
// transaction is BEGIN IMMEDIATE, else BEGIN; AuthSQL: the caller's row by token (one
// positional parameter), its columns UserColumns; RoleColumn ("" means "role"),
// AdminFlag/FlagRole and FixedRole are the role source; Form: body parameters are
// URL-encoded form fields; Effects: the handler collects outbox rows (Ctx.Pending),
// dispatched after COMMIT; Ambiguous: R311's 500 message when the token matches several
// users rows ("" means "error R311: ambiguous caller").
type Endpoint struct {
	Name, Method, Path string
	Auth               string
	Params             []ParamSpec
	Writes             bool
	AuthSQL            string
	UserColumns        []string
	UserKey, AdminRole string
	RoleColumn         string
	AdminFlag          *string
	FlagRole           string
	FixedRole          *string
	Form, Effects      bool
	Ambiguous          string
}

// Response is a handler's answer: a status and a JSON body (see WriteJSON), or
// NoContent.
type Response struct {
	Status int
	Body   any
}

type noContentT struct{}

// NoContent is the body of a 204 answer: Starlette's Response(status_code=204) has no
// body and no Content-Type or Content-Length header.
var NoContent any = noContentT{}

// Route is an endpoint with its handler (the generated steps function). The handler
// runs inside the request's transaction; it answers an error by panicking with an
// *ErrorResp (Abort), a data-dependent failure with a *Failure; a panic rolls back.
type Route struct {
	EP     Endpoint
	Handle func(c *Ctx, p map[string]any) Response
}

// Settings are the prelude's profile-wide constants: ErrorBody ("lattice" or "fastapi";
// "" means "lattice"), the automatic 401/403 details (nil: none), the active check
// (ActiveColumn nil: none; InactiveStatus 0 means 400, InactiveDetail "" means "Inactive
// user"), the Uuid/DateTime storage and wire spellings ("" means canonical / iso_z /
// iso_z), the effect natives, and the T2 budget per request (Fuel 0: no fuel; MaxSize 0:
// T2DefaultMaxSize).
type Settings struct {
	ErrorBody                           string
	UnauthorizedDetail, ForbiddenDetail *string
	ActiveColumn                        *string
	InactiveStatus                      int
	InactiveDetail                      string
	UUIDStorage, DateTimeStorage        string
	DateTimeWire                        string
	Effects                             map[string]Effect
	Fuel, MaxSize                       int64
}

// App serves routes over one SQLite database.
type App struct {
	DB       *sql.DB
	Settings Settings
	Routes   []Route
	// Trace: every response carries X-Lattice-Sql-Count (LATTICE_TRACE_SQL=1).
	Trace bool
}

// ---- errors ------------------------------------------------------------------------------

// ErrorResp is the prelude's ErrorResponse: status, JSON error code and detail (nil:
// none; a string or a JSON value), rendered in the error body style.
type ErrorResp struct {
	Status int
	Code   string
	Detail any
}

func (e *ErrorResp) Error() string { return fmt.Sprintf("%d %s: %v", e.Status, e.Code, e.Detail) }

// NewErrorResp is an error answer with any detail (nil: none).
func NewErrorResp(status int, code string, detail any) *ErrorResp {
	return &ErrorResp{Status: status, Code: code, Detail: detail}
}

// ErrorResponse is the prelude's error(status, code, detail).
func ErrorResponse(status int, code, detail string) *ErrorResp {
	return &ErrorResp{Status: status, Code: code, Detail: detail}
}

// Abort answers err (the handler's transaction rolls back).
func Abort(err *ErrorResp) { panic(err) }

// Failure is the prelude's Failure: a data-dependent failure (one() matched several
// rows, a T2 runtime error): 500 with the message.
type Failure struct{ Msg string }

func (f *Failure) Error() string { return f.Msg }

// T2Report is t2_report: the located message of a T2 runtime diagnostic, logged.
func T2Report(where string, e *Error) string {
	text := where + ": error " + e.Code + ": " + e.Msg
	log.Printf("lattice.t2: %s", text)
	return text
}

// T2Run is the prelude's t2_run: fn runs a T2 step; a runtime diagnostic it raises
// (R620/R621/R622/R623) becomes a Failure with the located message.
func T2Run(where string, fn func()) {
	defer func() {
		if r := recover(); r != nil {
			if e, ok := r.(*Error); ok && e.Code != "" {
				panic(&Failure{Msg: T2Report(where, e)})
			}
			panic(r)
		}
	}()
	fn()
}

var statusPhrases = map[int]string{
	100: "Continue", 101: "Switching Protocols", 102: "Processing", 103: "Early Hints",
	200: "OK", 201: "Created", 202: "Accepted", 203: "Non-Authoritative Information",
	204: "No Content", 205: "Reset Content", 206: "Partial Content", 207: "Multi-Status",
	208: "Already Reported", 226: "IM Used", 300: "Multiple Choices", 301: "Moved Permanently",
	302: "Found", 303: "See Other", 304: "Not Modified", 305: "Use Proxy",
	307: "Temporary Redirect", 308: "Permanent Redirect", 400: "Bad Request",
	401: "Unauthorized", 402: "Payment Required", 403: "Forbidden", 404: "Not Found",
	405: "Method Not Allowed", 406: "Not Acceptable", 407: "Proxy Authentication Required",
	408: "Request Timeout", 409: "Conflict", 410: "Gone", 411: "Length Required",
	412: "Precondition Failed", 413: "Content Too Large", 414: "URI Too Long",
	415: "Unsupported Media Type", 416: "Range Not Satisfiable", 417: "Expectation Failed",
	418: "I'm a Teapot", 421: "Misdirected Request", 422: "Unprocessable Content",
	423: "Locked", 424: "Failed Dependency", 425: "Too Early", 426: "Upgrade Required",
	428: "Precondition Required", 429: "Too Many Requests",
	431: "Request Header Fields Too Large", 451: "Unavailable For Legal Reasons",
	500: "Internal Server Error", 501: "Not Implemented", 502: "Bad Gateway",
	503: "Service Unavailable", 504: "Gateway Timeout", 505: "HTTP Version Not Supported",
	506: "Variant Also Negotiates", 507: "Insufficient Storage", 508: "Loop Detected",
	510: "Not Extended", 511: "Network Authentication Required",
}

// StatusPhrase is HTTPStatus(status).phrase.
func StatusPhrase(status int) string { return statusPhrases[status] }

// ErrorBody is the prelude's error_body in a style: "lattice" ({"error": code} plus
// "detail" when there is one) or "fastapi" ({"detail": ...}, the status phrase when
// there is no detail).
func ErrorBody(style string, status int, code string, detail any) Obj {
	if style == "fastapi" {
		if detail == nil {
			detail = StatusPhrase(status)
		}
		return Obj{{"detail", detail}}
	}
	body := Obj{{"error", code}}
	if detail != nil {
		body = append(body, KV{"detail", detail})
	}
	return body
}

// UniqueConflict is the 409 detail of a UNIQUE violation no write declared.
const UniqueConflict = "a row with the same unique value already exists"

// ---- steps --------------------------------------------------------------------------------

// Col is a response column: its name and Lattice scalar.
type Col struct{ Name, Scalar string }

func colIndex(cols []string, name string) int {
	i := indexOfCol(cols, name)
	if i < 0 {
		Raise("ValueError", ReprStr(name)+" is not in list")
	}
	return i
}

// Project is the prelude's project: the rows restricted to columns, in that order.
func Project(r Result, columns []string) [][]any {
	idx := make([]int, len(columns))
	for i, c := range columns {
		idx[i] = colIndex(r.Cols, c)
	}
	out := make([][]any, len(r.Rows))
	for k, row := range r.Rows {
		cells := make([]any, len(idx))
		for i, j := range idx {
			cells[i] = row[j]
		}
		out[k] = cells
	}
	return out
}

// CheckOne is the prelude's check_one: exactly one row; none -> err; several -> 500 (R310).
func CheckOne(r Result, err *ErrorResp, where string) {
	n := len(r.Rows)
	if n == 0 {
		panic(err)
	}
	if n > 1 {
		panic(&Failure{Msg: fmt.Sprintf("%s: error R310: one() matched %d rows; expected exactly one", where, n)})
	}
}

// CheckRequire is the prelude's check_require: the flag column holds on every row
// (false or null fails).
func CheckRequire(r Result, flag string, err *ErrorResp) {
	for _, f := range Project(r, []string{flag}) {
		if !pyTruth(f[0]) {
			panic(err)
		}
	}
}

// CheckWritten is the prelude's check_written: an owner transfer wrote fewer rows than
// it was given -> err.
func CheckWritten(source Result, returned [][]any, err *ErrorResp) {
	if len(returned) < len(source.Rows) {
		panic(err)
	}
}

// MergeUpdate is the prelude's merge_update: source with the new values RETURNING gave
// (key..., values...).
func MergeUpdate(source Result, returned [][]any, columns, key, setCols []string) Result {
	k := len(key)
	keyText := func(cells []any) string { return DumpsJSON(cellsAny(cells)) }
	updated := map[string][]any{}
	for _, r := range returned {
		updated[keyText(r[:k])] = r[k:]
	}
	keyIdx := make([]int, k)
	for i, c := range key {
		keyIdx[i] = colIndex(columns, c)
	}
	var rows [][]any
	for _, row := range Project(source, columns) {
		kc := make([]any, k)
		for i, j := range keyIdx {
			kc[i] = row[j]
		}
		vals, ok := updated[keyText(kc)]
		if !ok {
			rows = append(rows, row)
			continue
		}
		cells := append([]any(nil), row...)
		for i, c := range setCols {
			if j := indexOfCol(columns, c); j >= 0 {
				cells[j] = vals[i]
			}
		}
		rows = append(rows, cells)
	}
	if rows == nil {
		rows = [][]any{}
	}
	return Result{Cols: columns, Rows: rows}
}

func cellsAny(cells []any) []any {
	out := make([]any, len(cells))
	for i, c := range cells {
		if b, ok := c.([]byte); ok {
			out[i] = "b:" + string(b)
		} else {
			out[i] = c
		}
	}
	return out
}

// SplitTotal is the prelude's split_total: a page(total=True) read's rows end with
// (__in_page, __total); (the page's rows, the total).
func SplitTotal(rows [][]any) ([][]any, int64) {
	var total int64
	if len(rows) > 0 {
		total = pyInt(rows[0][len(rows[0])-1])
	}
	out := [][]any{}
	for _, r := range rows {
		if pyTruth(r[len(r)-2]) {
			out = append(out, r[:len(r)-2])
		}
	}
	return out, total
}

// pyInt is int() of a stored value.
func pyInt(v any) int64 {
	switch x := v.(type) {
	case int64:
		return x
	case float64:
		if math.IsNaN(x) || math.IsInf(x, 0) {
			Raise("ValueError", "cannot convert float NaN to integer")
		}
		return int64(x)
	case string:
		return ParseInt(x)
	case bool:
		if x {
			return 1
		}
		return 0
	}
	Raise("TypeError", "int() argument must be a string, a bytes-like object or a real number")
	return 0
}

// RowsJSON is the prelude's rows_json: rows of an earlier step, restricted to columns,
// bound as one JSON parameter (json.dumps).
func RowsJSON(r Result, columns []string) string {
	rows := Project(r, columns)
	out := make([]any, len(rows))
	for i, row := range rows {
		out[i] = row
	}
	return DumpsJSON(out)
}

// Decode is the prelude's _decode: a stored value as the response carries it.
func Decode(v any, scalar string) any {
	if v == nil {
		return nil
	}
	switch scalar {
	case "Int":
		return pyInt(v)
	case "Float":
		switch x := v.(type) {
		case int64:
			return float64(x)
		case string:
			f, err := strconv.ParseFloat(Strip(x, nil), 64)
			if err != nil {
				Raise("ValueError", "could not convert string to float: "+ReprStr(x))
			}
			return f
		}
		return v
	case "Bool":
		return pyTruth(v)
	case "Uuid":
		if s, ok := v.(string); ok {
			if u, ok := UUIDText(s); ok {
				return u
			}
		}
		return v
	case "DateTime":
		if s, ok := v.(string); ok {
			if t, ok := ParseDateTime(s); ok {
				return WireDateTime(t)
			}
		}
		return v
	}
	return v
}

// Finite is the prelude's _finite: a non-finite float serializes as JSON null.
func Finite(v any) any {
	if f, ok := v.(float64); ok && (math.IsNaN(f) || math.IsInf(f, 0)) {
		return nil
	}
	return v
}

// Items is the prelude's _items: each row as an object of its decoded columns.
func Items(r Result, columns []Col) []any {
	names := make([]string, len(columns))
	for i, c := range columns {
		names[i] = c.Name
	}
	rows := Project(r, names)
	out := make([]any, len(rows))
	for k, row := range rows {
		obj := make(Obj, len(columns))
		for i, c := range columns {
			obj[i] = KV{c.Name, Finite(Decode(row[i], c.Scalar))}
		}
		out[k] = obj
	}
	return out
}

// RespondOne is the prelude's respond_one: the single returned row as an object.
func RespondOne(r Result, columns []Col, status int, where string) Response {
	items := Items(r, columns)
	if len(items) == 0 {
		panic(ErrorResponse(404, "not_found", "no such row"))
	}
	if len(items) > 1 {
		panic(&Failure{Msg: fmt.Sprintf("%s: error R310: the response has %d rows; expected exactly one", where, len(items))})
	}
	return Response{Status: status, Body: items[0]}
}

// RespondPage is the prelude's respond_page: the Page envelope in the profile's shape
// ((key, JSON field) pairs; an empty field is omitted). total nil: no total.
func RespondPage(r Result, columns []Col, shape [][2]string, limit, offset any, total *int64) Response {
	parts := map[string]any{"items": Items(r, columns), "limit": limit, "offset": offset}
	if total != nil {
		parts["total"] = *total
	}
	body := Obj{}
	for _, kv := range shape {
		if v, ok := parts[kv[0]]; ok && kv[1] != "" {
			body = append(body, KV{kv[1], v})
		}
	}
	return Response{Status: 200, Body: body}
}

// RespondList is the prelude's respond_list: the returned rows as a JSON array.
func RespondList(r Result, columns []Col) Response {
	return Response{Status: 200, Body: Items(r, columns)}
}

// ---- the app -----------------------------------------------------------------------------

// NewApp opens the database (busy_timeout 5000 ms, as Python's sqlite3.connect's
// default timeout of 5 s; no other pragma, as the prelude's connect sets none), with the
// SQL functions registered, and applies the storage settings.
func NewApp(dbPath string, routes []Route, s Settings) (*App, error) {
	RegisterFunctions()
	Storage.UUID = orDefault(s.UUIDStorage, "canonical")
	Storage.DateTime = orDefault(s.DateTimeStorage, "iso_z")
	Storage.Wire = orDefault(s.DateTimeWire, "iso_z")
	db, err := sql.Open("sqlite", "file:"+dbPath+"?_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, err
	}
	db.SetMaxIdleConns(16)
	return &App{DB: db, Settings: s, Routes: routes, Trace: os.Getenv("LATTICE_TRACE_SQL") == "1"}, nil
}

func orDefault(s, d string) string {
	if s == "" {
		return d
	}
	return s
}

// Serve is a generated app's main: `app --db <path> --addr <host:port>` opens the
// database, listens, prints "LISTENING <host:port>" and serves until killed.
func Serve(routes []Route, settings Settings) {
	fs := flag.NewFlagSet(os.Args[0], flag.ExitOnError)
	dbPath := fs.String("db", "app.db", "the SQLite database file")
	addr := fs.String("addr", "127.0.0.1:0", "the address to listen on")
	_ = fs.Parse(os.Args[1:])
	app, err := NewApp(*dbPath, routes, settings)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	ln, err := net.Listen("tcp", *addr)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Printf("LISTENING %s\n", ln.Addr().String())
	_ = os.Stdout.Sync()
	srv := &http.Server{Handler: app, ReadHeaderTimeout: time.Minute}
	if err := srv.Serve(ln); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

type answer struct {
	status  int
	body    []byte
	headers map[string]string
	queries int
	// noLength: no Content-Length header (a 204)
	noLength bool
}

func (a *App) style() string { return orDefault(a.Settings.ErrorBody, "lattice") }

func (a *App) jsonAnswer(status int, body any) answer {
	b, err := WriteJSON(body)
	if err != nil {
		b, _ = WriteJSON(ErrorBody(a.style(), 500, "internal", "ValueError: "+err.Error()))
		status = 500
	}
	return answer{status: status, body: b, headers: map[string]string{"Content-Type": "application/json"}}
}

func (a *App) errorAnswer(status int, code string, detail any) answer {
	return a.jsonAnswer(status, ErrorBody(a.style(), status, code, detail))
}

func (a *App) internal(detail string) answer {
	return a.errorAnswer(500, "internal", truncRunes(detail, 500))
}

// ServeHTTP routes a request as Starlette does (the first route whose path and method
// match; a path matching only with another method is 405; a path matching only with
// the trailing slash toggled is a 307 redirect; else 404) and serves it.
func (a *App) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	ans := a.route(r)
	h := w.Header()
	for k, v := range ans.headers {
		h.Set(k, v)
	}
	if !ans.noLength {
		h.Set("Content-Length", strconv.Itoa(len(ans.body)))
	}
	if a.Trace {
		h.Set("X-Lattice-Sql-Count", strconv.Itoa(ans.queries))
	}
	w.WriteHeader(ans.status)
	_, _ = w.Write(ans.body)
}

func splitPath(p string) []string { return strings.Split(p, "/") }

func matchPath(pattern, path []string) (map[string]string, bool) {
	if len(pattern) != len(path) {
		return nil, false
	}
	params := map[string]string{}
	for i, seg := range pattern {
		if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
			if path[i] == "" {
				return nil, false
			}
			params[seg[1:len(seg)-1]] = path[i]
		} else if seg != path[i] {
			return nil, false
		}
	}
	return params, true
}

func (a *App) route(r *http.Request) answer {
	path := Unquote(r.URL.EscapedPath())
	segs := splitPath(path)
	partial := false
	for i := range a.Routes {
		rt := &a.Routes[i]
		params, ok := matchPath(splitPath(rt.EP.Path), segs)
		if !ok {
			continue
		}
		if rt.EP.Method != r.Method {
			partial = true
			continue
		}
		return a.serve(rt, r, params)
	}
	if partial {
		return a.errorAnswer(405, "method_not_allowed", nil)
	}
	if path != "/" {
		toggled := path + "/"
		if strings.HasSuffix(path, "/") {
			toggled = path[:len(path)-1]
		}
		tsegs := splitPath(toggled)
		for i := range a.Routes {
			if _, ok := matchPath(splitPath(a.Routes[i].EP.Path), tsegs); ok {
				url := "http://" + r.Host + toggled
				if r.URL.RawQuery != "" {
					url += "?" + r.URL.RawQuery
				}
				return answer{status: 307, headers: map[string]string{"Location": quoteURL(url)}}
			}
		}
	}
	return a.errorAnswer(404, "not_found", nil)
}

// quoteURL is urllib.parse.quote(url, safe=":/%#?=@[]!$&'()*+,;") (Starlette's
// RedirectResponse).
func quoteURL(s string) string {
	const safe = ":/%#?=@[]!$&'()*+,;_.-~"
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c < 0x80 && (isDigit(c) || (c|0x20 >= 'a' && c|0x20 <= 'z') || strings.IndexByte(safe, c) >= 0) {
			b.WriteByte(c)
		} else {
			fmt.Fprintf(&b, "%%%02X", c)
		}
	}
	return b.String()
}

// serve is the prelude's serve: authenticate, validate, then run the handler in one
// transaction; any error answer rolls back. fn.now() is one instant per request.
func (a *App) serve(rt *Route, r *http.Request, path map[string]string) (ans answer) {
	ep := &rt.EP
	now := Now()
	body, _ := io.ReadAll(r.Body)
	var db *dbConn
	inTxn := false
	defer func() {
		if db != nil {
			if inTxn {
				_, _ = db.conn.ExecContext(context.Background(), "ROLLBACK")
			}
			ans.queries = db.queries
			_ = db.conn.Close()
		}
	}()
	defer func() {
		if p := recover(); p != nil {
			ans = a.failure(p)
		}
	}()
	open := func() {
		conn, err := a.DB.Conn(context.Background())
		if err != nil {
			panic(&SQLError{Kind: "OperationalError", Msg: err.Error()})
		}
		db = &dbConn{conn: conn}
	}
	var caller *Caller
	if ep.Auth != "Public" {
		auth, present := r.Header["Authorization"]
		header := ""
		if present {
			header = auth[0]
		}
		token, ok := Token(header, present)
		if !ok {
			return a.errorAnswer(401, "unauthorized", strOrNil(a.Settings.UnauthorizedDetail))
		}
		open()
		caller = a.authenticate(ep, db, token)
	}
	ctype, hasCtype := r.Header["Content-Type"]
	ct := ""
	if hasCtype {
		ct = ctype[0]
	}
	values, perr := ParseParams(ep.Params, path, ParseQS(r.URL.RawQuery), body, ep.Form, ct)
	if perr != nil {
		return a.errorAnswer(perr.Status, perr.Code, perr.Detail)
	}
	if db == nil {
		open()
	}
	if ep.Writes {
		db.exec("BEGIN IMMEDIATE")
	} else {
		db.exec("BEGIN")
	}
	inTxn = true
	c := &Ctx{db: db, EP: ep, Caller: caller, Values: values, Now: now}
	if a.Settings.Fuel > 0 {
		c.Fuel = NewFuel(a.Settings.Fuel)
		if a.Settings.MaxSize > 0 {
			c.Fuel.MaxSize = a.Settings.MaxSize
		}
	}
	resp := rt.Handle(c, values)
	var out []byte
	_, empty := resp.Body.(noContentT)
	if !empty {
		var err error
		if out, err = WriteJSON(resp.Body); err != nil {
			panic(&Failure{Msg: "ValueError: " + err.Error()})
		}
	}
	db.exec("COMMIT")
	inTxn = false
	if len(c.Pending) > 0 { // after COMMIT: the answer is already decided
		func() {
			defer func() { _ = recover() }()
			a.DispatchOutbox(db, c.Pending, nil, OutboxGrace)
		}()
	}
	if empty {
		return answer{status: resp.Status, headers: map[string]string{}, noLength: true}
	}
	return answer{status: resp.Status, body: out, headers: map[string]string{"Content-Type": "application/json"}}
}

// failure is the answer to a panic in serve: an error answer, a Failure's 500, a
// UNIQUE violation's 409, else a 500 naming the error.
func (a *App) failure(p any) answer {
	switch e := p.(type) {
	case *ErrorResp:
		return a.errorAnswer(e.Status, e.Code, e.Detail)
	case *Failure:
		return a.internal(e.Msg)
	case *SQLError:
		if IsUniqueViolation(e) {
			return a.errorAnswer(409, "conflict", UniqueConflict)
		}
		return a.internal(e.Error())
	case *Error:
		if e.Code != "" { // a T2 diagnostic outside T2Run: not located
			return a.internal("error " + e.Code + ": " + e.Msg)
		}
		return a.internal(e.Kind + ": " + e.Msg)
	}
	log.Printf("lattice: unexpected panic: %v\n%s", p, debug.Stack())
	return a.internal(fmt.Sprintf("InternalError: %v", p))
}
