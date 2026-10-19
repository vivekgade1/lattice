`streak/` holds the requirements (`REQUIREMENTS.md`), the behavioural spec (`spec.py`, an HTTP spec) and the context (`CONTEXT.md`) of a
habit-tracking service. Re-implement the same behaviour as a gRPC service in Go (grpc-go, unary rpcs) over MySQL 8.4, in `./app/`:
`.proto` files, the server, `harness.py` and `studio.toml` per `HARNESS_API.md` (A.5 MySQL), a gRPC form of the spec (`spec.py` using `Rpc`
steps, A.6) covering every rule you can express and listing the others in `NOT_EXPRESSED.md`, `truth.json` per `TRUTH_SCHEMA.md`, a Go
program `cmd/replay` that runs every truth flow and exits 0 only if all pass, `FILES.txt`, `freeze.sha256` at the app root. Implement it the
way you would naturally implement such a service. When everything is complete, write `IDIOMS.md` (one line per service's wiring, error
reporting, authentication, and where the schema lives). Work only inside this directory.
