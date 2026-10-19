# Context: the Shelf service

A gRPC server (Go, `google.golang.org/grpc`) over a SQLite file. The contract is `proto/toy/v1/shelf.proto`; the
behaviour is `REQUIREMENTS.md` / `spec.py`. You build the server; you do not change the proto, the spec, the harness,
the generator or this file.

* **Stubs.** `gen/toy/v1/*.pb.go` is produced by `./generate.sh` (buf) from the proto and is rebuilt by every
  `./check.sh`. Never write or edit anything under `gen/`. Import it as `toyv1 "example.com/toybuild/gen/toy/v1"`.
* **Entry point.** A `package main` at the repository root (`go build -o bin/toy-server .`).
* **Environment the checker sets.** `PORT`: listen for gRPC on `127.0.0.1:$PORT` (plaintext). `DB_PATH`: the SQLite file;
  the table `items(id integer primary key autoincrement, name text not null unique, qty integer not null)` already
  exists (the harness created it): do not create or migrate tables. Use `database/sql` with the pure-Go driver
  `modernc.org/sqlite` (driver name `"sqlite"`; open `file:<DB_PATH>?_pragma=busy_timeout(10000)` and
  `SetMaxOpenConns(1)`).
* **Readiness.** The checker waits for the standard health service (`google.golang.org/grpc/health`, registered on the
  same server) to answer `SERVING` for the empty service name.
* **Dependencies.** `go.mod` and `go.sum` already list what the build needs (grpc, protobuf, modernc sqlite, health).
  There is no network and no `go mod tidy` in the gate: use only packages those modules provide.
* **Gate.** `./check.sh` (generate, build, then the Lattice check of `spec.py`, layers C and D). Exit 0 = done.
