# platformkit — the Go platform kit (5b pilot, arm G)

The Go counterpart of `bench/api_kit/platform_kit.py`. Arm G's model writes
`handlers.go` (package `main`) against this kit; arm P's model writes a FastAPI
script against the Python kit. The two kits give the same capability and nothing
more: they open the database, find the caller from the bearer token and gate by
role. Ownership checks, input validation, error responses and pagination are the
handler's job on both arms.

The part between the `prompt` markers is the model-facing kit section; Task 3 builds
the arm G prompt from it. Everything after it is for the harness.

<!-- prompt:begin -->
## platformkit

Write `handlers.go` in `package main`, importing `"platformkit"` and the standard
library only. Define

```go
func Register(mux *http.ServeMux, k *platformkit.Kit)
```

and add every route to `mux` with Go's method-and-path patterns
(`mux.HandleFunc("GET /orders/{id}", ...)`, `r.PathValue("id")`). The kit's `main`
calls `Register`; never write `main` or start a server yourself.

The kit gives you:

- `k.DB`: a `*sql.DB` (database/sql) on the application's SQLite database. Run every
  statement through it, with `?` placeholders for every value (`k.DB.Query`,
  `k.DB.QueryRow`, `k.DB.Exec`, `k.DB.Begin` for a transaction). Each statement
  commits on its own unless you run it in a transaction.
- `k.Authenticate(w, r)`: the caller, as `*platformkit.Caller`
  `{ID int64; Role, Email, Name string}`, and `true`; for a missing or unknown
  `Authorization: Bearer <token>` it answers 401 `{"error": "unauthorized"}` and
  returns `false` — then return from the handler.
- `k.Authenticate(w, r, "Customer")`: the same, and it also answers 403
  `{"error": "forbidden"}` (returning `false`) unless the caller has one of the given
  roles (Admin may call every endpoint). Public endpoints do not call it.

```go
c, ok := k.Authenticate(w, r, "Customer")
if !ok {
	return
}
```

- The same in two steps, if you need them: `k.Caller(r)` returns `(*Caller, error)`
  and `k.RequireRole(c, roles...)` returns an `error`; `platformkit.WriteError(w, err)`
  answers such an error (401 / 403 as above; any other error is a 500).

The kit only authenticates: ownership checks, input validation, error responses and
pagination are your code's job. An unrouted path answers 404
`{"detail": "Not Found"}`, a method the path does not serve 405
`{"detail": "Method Not Allowed"}`, and a handler that panics 500.
<!-- prompt:end -->

## For the harness

**Wiring.** The harness writes the model's `handlers.go` next to this `main.go`:

```go
package main

import "platformkit"

func main() { platformkit.Serve(Register) }
```

with a `go.mod` that reaches the kit through a `replace`, and the kit's `go.sum`:

```
module app

go 1.26.0

require platformkit v0.0.0

replace platformkit => /abs/path/to/bench/api_kit_go
```

Build offline as `lattice.gogen.build` does (`GOFLAGS=-mod=mod GOPROXY=off GOWORK=off
GOTOOLCHAIN=local go build -trimpath -buildvcs=false -o app .`); `-mod=mod` fills in the
indirect requirements from the kit's go.mod. The kit pins `lattice/gogen/rt`'s module
versions (modernc.org/sqlite v1.59.0, modernc.org/libc v1.75.7), so the module cache
that `cd lattice/gogen/rt && go mod download` fills is all it needs.
`tests/bench/test_api_kit_go.py::build_probe_app` is a working example.

**Command line.** The same as a generated Lattice Go app, so
`lattice.gogen.proxy.GoServer` / `go_app_factory` drive both arms: `app --db <path>
--addr <host:port>` prints one line `LISTENING <host:port>` on stdout and serves until
killed. The database connection is Python's `sqlite3.connect`'s: busy timeout 5 s, no
other pragma.

**SQL counting.** With `LATTICE_TRACE_SQL=1` every response carries
`X-Lattice-Sql-Count`: the data statements the request ran through `k.DB`, by
`bench.api_runner._is_counted_statement` (first word SELECT / INSERT / UPDATE / DELETE /
WITH / REPLACE, split as Python splits), the caller lookup included — what the Python
arm's trace callback counts, where `current_user`'s SELECT is counted too. The kit's
database driver wraps modernc's and prepares every statement, counting each execution:
a statement that fails to prepare is not counted, one that fails while running is (as
SQLite's trace fires when a statement starts). With tracing on, requests are served one
at a time so each count is its own. `LATTICE_TRACE_SQL_LOG` (the statement log of the
Lattice binaries) is not supported.

**Framework parity.** The kit buffers each response. Where FastAPI answers without the
script, the kit answers the same: unrouted 404 `{"detail":"Not Found"}`, 405
`{"detail":"Method Not Allowed"}`, and a panic becomes Starlette's 500
`Internal Server Error` (`text/plain; charset=utf-8`), with the panic and its stack
logged to stderr. The `Authorization` header is read as Latin-1 and the token stripped
as Python's `str.strip()` does, as Starlette and the Python kit do.

**Tests.** `go vet ./... && go test ./...` here (offline env as above);
`tests/bench/test_api_kit_go.py` serves one handler set on both kits and compares
status, Content-Type, body and SQL count over the probe cases.
