Write a gRPC service in Go (grpc-go, unary and at least one server-streaming rpc) over pure-Go SQLite (no cgo, no Docker; buildable with
the Go module proxy) in `./app/` inside this directory, plus `harness.py` and `studio.toml` exactly as `HARNESS_API.md` says, `truth.json`
exactly as `TRUTH_SCHEMA.md` says, a Go program `cmd/replay` (its own client, no Python) that runs every truth flow on a freshly built
database and also all flows in order on one database and exits 0 only if all pass, `FILES.txt` (every file, one per line), and
`freeze.sha256` at the app root. Minimums: at least 3 registered services, at least 15 unary rpcs, at least 1 rpc answered `Unimplemented`,
at least 1 declared service that is never registered, at least 2 users of different roles, at least 2 request fields that name a seeded row
by a natural key, at least 6 truth flows of at least 3 steps that copy values between steps with links, and a login rpc. Organise the server
the way a team maintaining a mid-sized production service would: packages per area, construction code separate from handlers, and the
indirection such codebases typically have. No two services may be wired into the server by the same code idiom; no two services may report
errors by the same idiom; the authentication decision is written in an idiom of your own choice. Put the database schema wherever such a
team would. Do not copy an example from any document. When code and truth are complete and every check you can run passes, write
`IDIOMS.md`: one line per service's wiring, per service's error reporting, per authentication mechanism, and where the schema lives. Work
only inside this directory.
