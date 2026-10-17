# gRPC in import and build, unary first (Spike I): pre-registration

## 1. Status, goal and the exact gap

2026-10-17, from `next-phases` 6a318e5 (Spikes A-D, F and G merged; H pre-registered, not built). **Written BEFORE any
code.** Results: `2026-10-17-grpc-results.md`, data in `docs/results/data/2026-10-17-grpc/`. Source: the plan's Addendum
"Spike I" (`docs/design/lattice-implemented-logic-spikes-plan.md`). Labels: **(m)** measured during recon in this
container, **(r)** a recon script's output (a blind expectation), everything else a **prediction (p)**. Recon: code
reading at 6a318e5; `go build` of `tests/spec/goapp` into scratch; `go mod download` of the pinned modules below into
the user module cache (nothing in the repository); `gocheck extract` on two fixtures, 3 runs; `pytest
tests/gocheck/test_goextract.py tests/spec/test_process_runtime.py`; HTTP HEADs to the Go proxy, PyPI and GitHub. No
model was called.

**Goal (plan, binding).** Every front end (`FrontEnd.detect/extract/runtime`) and the checker runtime (harness,
access matrix, `Step`) assume HTTP: a method, a path, a JSON body, a status code. Spike I adds gRPC, **unary only**:
(1) the `.proto` files as the declared contract, read deterministically; (2) a grpc-go front end over `goextract` that
yields the same framework-neutral `RepoModel`, rpcs as endpoints `Service/Method`, gRPC codes as outcomes, grpc-gateway
bindings listed once; (3) an rpc form of a spec step and of the access matrix; (4) a runtime that drives rpc steps
through a gRPC client, with layers C, D and the workflow layer on throwaway databases; (5) the build loop taking the
proto as contract; (6) report and app map listing rpcs beside routes. **HTTP specs and every HTTP output stay
byte-identical; a spec with no rpc step behaves exactly as before.** Out of scope, listed `not supported`, never
guessed: client, server and bidirectional streaming; non-Go servers; gRPC-Web; deadlines and retries as checked
behaviour; load balancing; TLS; `google.rpc.Status` details.

**Environment (m), binding on how this spike is scored.** No Streak checkout and no Docker daemon (`docker info`
answers the client only). `go` 1.24.7 with `GOTOOLCHAIN=auto` (the goapp build fetched go1.25.0, as its `go.mod`
asks), `uv`, `python3` 3.12, `node`, `gcc` and the `claude` CLI exist. The Go module proxy is reachable (HTTP 200;
`go mod download` of `google.golang.org/grpc@v1.71.0`, `google.golang.org/protobuf@v1.36.5`,
`github.com/bufbuild/protocompile@v0.14.1`, `github.com/fullstorydev/grpcurl@v1.9.3` took 1.5 s), PyPI is reachable,
**GitHub release downloads are refused (HTTP 403)**, so a prebuilt `protoc` or `grpcurl` release binary cannot be
fetched here. `grpcio` is not in `uv.lock` and not installed. A pure-Go SQLite Go app already runs under the process
runtime with no Docker: `tests/spec/goapp` (modernc.org/sqlite) built cold in 31.6 s, and the 43 tests of
`test_goextract.py` + `test_process_runtime.py` pass in 45 s. **So every arm that needs only Go and SQLite runs here;
only the Streak / MySQL arm cannot** (section 8).

**What exists, and what does not (m).**
1. **Nothing speaks gRPC.** `grep -i 'grpc|protobuf|\.proto'` over `lattice/` and `pyproject.toml` finds one string:
   `"ProtoBuf"` in `gocheck/src/partial.go:29` (a Gin render method name). There is no proto parser, no gRPC client,
   no rpc step.
2. **The front-end protocol is transport-neutral; its contents are not.** `frontends/__init__.py` `FrontEnd` has
   `detect/extract/runtime/graph_hints`; Go front ends load lazily (`_load_go`), after every Python one declined.
   `appmodel.Route` is `method, path, params, ..., raises (RaiseSite.status: int), auth, owner_uses, ...`, and new
   fields for other frameworks already use `OMIT_DEFAULT` so older JSON stays byte-identical (the precedent this spike
   follows).
3. **`goextract` is stdlib-only and offline** (`lattice/gocheck/src/go.mod`: `module lattice.dev/gocheck`, `go 1.22`, no
   requirements; `build_gocheck` builds once per source hash into `~/.cache/lattice/gocheck`). It emits
   `lattice.goextract/1` (`schema, module, go, dialect, main, tables, routes, middleware, constructors, sentinels,
   error_writer(s), auth, models, status_writes, excluded, unread, notes`). On `tests/gocheck/fixtures/goextract`: 9
   routes, 4 tables, 1 unread; output sha256 `f74727094a25cc1d...`, identical over 3 runs; `goextract_workflow`:
   `ee3b906bd4e4e799...` (m).
4. **The runtime is HTTP end to end.** `transport.Transport.request(method, path, *, params, json, form, headers) ->
   Answer(status: int, headers, body)`; `process.ProcessApp` waits for `GET health_path` == 200 and serves one port;
   `check._run_scenario` calls `client.request(step.method, step.path, ...)` and compares `status_code` with
   `step.expect_status`, `subset(expect_body, body)`, then `_expect_db`.
5. **`Step` is HTTP-shaped and `Scenario.steps` is `Step | Await`.** 49 `isinstance(..., Step|Await)` sites in 15
   modules (`lint.py` 11, `ask.py` 6, `impact_tier3.py` 6, `check.py` 5, `impact_tier2.py` 5, ...) assume a non-`Await`
   step is a `Step` (m). Any new step class must be handled, or provably skipped, at each.
6. **The access matrix** (`review._build_access`) has rows `(*spec.users, "no token")`, columns `"METHOD {norm path}"`
   grouped by first path segment, cells the joined `expect_status` values or `·`.
7. **Language rules** (`docs/lattice-language-rules.md` R12): a gated construct names its exit condition; nothing is cut
   to the lowest common denominator.
8. **Last free codes (m):** validate `S025`, lint `L012`, gocheck `GO808`. New codes start at `S026`, `L013`; no new
   `GO8xx` (layer A is not extended, D9).

**Rulings requested.**
- **R1 (client):** a pinned Go bridge binary built from a Lattice-owned Go module with a checked-in `go.sum`, **not**
  `grpcio` (D5 gives the reasons). `pyproject.toml` and `uv.lock` stay byte-identical.
- **R2 (scope of round 1):** items 1-4 plus rpc rows in the implementation report and the app map (item 6, listing
  only). Item 5 (the build round on a toy proto) is registered here as Arm L but **recommended as its own node I2**,
  after round 1 is merged. D's tier 3 and F's chat consume rpcs only through the report (no edit to `impact_*.py`,
  `ask.py`, `mcp/tools.py`, Studio).
- **R3 (front end):** a new front end `grpc-go` (`frontends/go_grpc.py`) that reuses `go_gin`'s mapping read-only and
  also carries the module's Gin routes; it detects only on a server registration (D2), so every Gin app keeps `gin`.
- **R4 (step form):** a new step class `Rpc` beside `Await`, not a `Step` with `method="RPC"` (D3).
- **R5 (message JSON):** Lattice's canonical message form `lattice.grpcjson/1` deviates from protojson in one place:
  64-bit integers within +/-2^53 are JSON numbers, not strings (D4).
- **R6 (layers A, R, X, B for rpcs):** `not supported` this round, each with an exit condition (D9).
- **R7 (held out):** a separate agent writes a held-out grpc-go app over pure-Go SQLite, with ground truth, before the
  build; it is scored once after the freeze, **here** (section 5).

## 2. Decisions (fixed before coding)

**D1 The contract source: a stdlib-only `.proto` reader in `goextract`.** New file `goextract_proto.go`. Input: the
module's `*.proto` files (outside `vendor/`, `testdata/`, `third_party/` unless listed) plus `studio.toml [grpc]
proto_paths` include roots. Grammar accepted, nothing else: `syntax = "proto3"`; `package`; `import` (resolved against
the file's dir and the include roots; `google/protobuf/{empty,timestamp,duration,wrappers,struct,field_mask}.proto`,
`google/api/annotations.proto`, `google/api/http.proto`, `validate/validate.proto`, `buf/validate/validate.proto` are
known by name and need not be present); `option go_package`; `message` (scalar, message and enum fields, `repeated`,
proto3 `optional`, `map<K,V>`, `oneof`, nested messages and enums, `reserved`); `enum`; `service` with `rpc` (request
and response types; the `stream` keyword marks the rpc `excluded: streaming_not_supported`); rpc option
`(google.api.http)` with `get|put|post|delete|patch` and `body` (`custom`, `response_body` and `additional_bindings`
are read and listed, the first binding is used); field options `(validate.rules).<t>.{gt,gte,lt,lte,const,min_len,
max_len,in}` (PGV) and `(buf.validate.field).<t>.{...}` (protovalidate), same subset, `required` for messages, `enum
.defined_only`, `repeated.{min_items,max_items}`. Mapped to bounds: `gte -> ge`, `gt -> gt`, `lte -> le`, `lt -> lt`,
`min_len/max_len -> min_length/max_length`, `in -> choices`. **Anything else** (`syntax = "proto2"`, `edition`,
`extend`, `group`, custom options other than the two above, `import public|weak`, other validate rules, `pattern`)
becomes an `unread` item `{kind: proto_<what>, file, line}`; the enclosing element is kept without the construct, never
approximated. Output: a key **`grpc`** in `lattice.goextract/1` with `{protos[{file, package, go_package, imports}],
services[{name (full), file, line, rpcs[{name, input, output, client_streaming, server_streaming, http{method, path,
body}, file, line}]}], messages[{name (full), fields[{name, number, type, label (single|optional|repeated|map), key,
value, oneof, bounds{...}, line}]}], enums[{name, values[{name, number}]}]}`. **The key is omitted (`omitempty`)
when the module has no `.proto` file and no service registration**, so every HTTP-only extract keeps its bytes.

**D2 Import: grpc-go extraction.** New file `goextract_grpc.go` (stdlib, `go/ast`, reusing `Load`, the closure,
`owner`, `status_writes` and raise machinery read-only by call). It reads:
- **Registrations:** a call `X.RegisterFooServer(s, impl)` whose callee is the generated function of a `*_grpc.pb.go`
  file in the module (its body calls `s.RegisterService(&Foo_ServiceDesc, srv)`), or a direct `s.RegisterService(&desc,
  impl)`. The `grpc.ServiceDesc` literal gives `ServiceName`, `Methods[].MethodName` (unary) and `Streams` (excluded).
  Registrations under a feature slice or a `Register` func are followed as Spike A does for Gin.
- **Handlers:** the type of `impl` (a `&T{...}` literal, a `var`, or a constructor's single `*T` return) and its method
  named after each rpc with the generated signature `(ctx, *In) (*Out, error)`. An rpc served only by an embedded
  `UnimplementedFooServer` is **`unimplemented`** (the gRPC analogue of `spec_only`); an rpc of the proto with no
  registration at all is `not_registered`.
- **Outcomes:** `status.Error(codes.X, ...)`, `status.Errorf(codes.X, ...)`, `status.New(codes.X, ...).Err()` and
  project error constructors recognised by call graph (a function whose every return is one of those forms), in the
  handler and its closure (depth 4, as now); `return nil, err` of a callee's error inherits the callee's codes; a
  returned non-status error is `UNKNOWN` with `kind: plain_error` (grpc-go's behaviour); a code chosen at run time
  (a variable, a map, an `errors.Is` switch with non-literal arms) is `unread: grpc_code_not_literal`. Codes are the
  canonical names `OK ... UNAUTHENTICATED` (`google.rpc.Code`), with the number.
- **Interceptors as auth/guard evidence:** `grpc.NewServer(grpc.UnaryInterceptor(f) | grpc.ChainUnaryInterceptor(f, ...))`.
  Inside `f`: a literal set, map or `switch` over `info.FullMethod` that skips the auth check gives `Public` for the
  listed rpcs and `Member` for the others, when the rest of `f` returns `codes.Unauthenticated` on a missing or bad
  `metadata.FromIncomingContext` value; anything else is `auth: unclear` with `unread: interceptor_not_literal`.
  A handler-level call to a helper that returns `codes.Unauthenticated` is `Member` evidence too.
- **Tables, owners, state writes:** unchanged machinery over the handler's closure (GORM models, `Where("... user_id
  = ?")`, `Owned*`, status writes). **grpc-gateway:** `RegisterFooHandlerServer|FromEndpoint` plus the proto's
  `google.api.http` options give each rpc `http_bindings[{method, path}]`; the rpc is **one** endpoint, never a second
  route.
- **Shim:** `go_grpc.py` maps each rpc to a `Route` with `method = "RPC"`, `path = "/<pkg.Service>/<Method>"` (the
  gRPC wire path), and new `OMIT_DEFAULT` fields `Route.transport = "grpc"` (default `"http"`, omitted),
  `Route.rpc {service, method, input, output, state: implemented|unimplemented}`, `Route.http_bindings`,
  `RaiseSite.code` (a code name; `status` None for gRPC sites, `kind: "grpc_status"`). Request fields come from the
  input message (`RouteParam.source = "message"`, bounds from D1). `detect`: `go.mod` requires
  `google.golang.org/grpc` **and** some non-test `.go` file contains a server registration (`Register\w+Server(` with a
  generated callee, or `.RegisterService(`); a Gin app that only imports a gRPC client keeps `gin`. Registered in
  `_load_go` **after** `go_gin` is imported and **before** it in detection order only through that predicate.

**D3 The spec form: `Rpc` steps.** In `lattice/spec/model.py`:

```python
@dataclass(frozen=True)
class Rpc:
    service: str                         # full name, "notes.v1.Notes"
    method: str                          # "GetNote"
    user: str | None = None              # a Spec.users handle; None: no caller
    message: Any = field(default=None, hash=False)              # lattice.grpcjson/1 object
    metadata: Mapping[str, str] | None = field(default=None, hash=False)
    expect_code: str = "OK"              # a google.rpc.Code name
    expect_message: Any | None = field(default=None, hash=False)  # subset of the response (OK) or of
                                                                  # {"code", "message"} (an error)
    expect_db: Mapping[str, Any] | None = field(default=None, hash=False)  # Step.expect_db's forms
```

`Scenario.steps: Step | Await | Rpc`; `Spec.protos: tuple[str, ...] = ()` (project-relative `.proto` paths; default
empty, so every existing spec is unchanged). Endpoint text `RPC notes.v1.Notes/GetNote`; step line `RPC
notes.v1.Notes/GetNote as ann -> NOT_FOUND`. **Validate:** `S026` an `Rpc` whose service/method is not in the specs'
protos (only when `protos` is non-empty), `S027` an `expect_code` that is not a code name, `S028` an `Rpc` naming a
streaming method, `S029` a `Contract` or program on an `RPC` endpoint (R6). **Lint:** `L013` the rpc analogue of
L011 (same `given`, same first rpc and message, different `expect_code`). L011 and L012 are unchanged on `Step`s. **Access
matrix:** an `Rpc` adds column `RPC <Service>/<Method>` in a group named by the service's full name; cells are code
names joined by `/`. A spec with no `Rpc` yields the same groups, rows, columns and cells as today. Every one of the 49
`isinstance` sites either handles `Rpc` explicitly or is proven to skip it by a unit test (no silent `else: Step`).

**D4 Runtime: the rpc transport.** New module `lattice/spec/grpc.py`: `GrpcTransport` (one bridge process per check,
D5), `GrpcAnswer(code, code_num, message, body)` and `rpc_auth` (log in by an rpc: `AuthDriver.rpc(service, method,
users, token_field)`, token presented as metadata `authorization: Bearer <t>`; or by an HTTP route on a gateway).
`check._run_scenario` dispatches `Rpc` steps to it; `Step`s keep the HTTP transport, so one scenario may mix gateway
HTTP and rpc steps. After each rpc answer, `expect_db` and layer C's invariants run exactly as after an HTTP step.
`ProcessApp` gains additive fields `ready: "http" | "grpc" | "tcp" = "http"` (`grpc`: `grpc.health.v1.Health/Check`
answers `SERVING`) and `grpc_port_env: str | None = None` (a second free port for apps serving HTTP and gRPC on two
ports); defaults reproduce today's behaviour exactly. **Message form `lattice.grpcjson/1`:** protojson with proto field
names, unpopulated fields emitted, enums by name, and (R5) int64/uint64/fixed64 within +/-2^53 as numbers; outside it,
strings. Errors: body `{"code": <name>, "message": <text>}`. A fixed 10 s deadline per call, not checked behaviour.

**D5 The client dependency: a pinned Go bridge, not `grpcio`.** New Go module `lattice/grpcbridge/` (`go.mod`,
`go.sum` checked in; `google.golang.org/grpc v1.71.0`, `google.golang.org/protobuf v1.36.5`,
`github.com/bufbuild/protocompile v0.14.1`, all released before 2025-04 and downloaded here (m)). Built like
`build_gocheck`: `go build -trimpath -buildvcs=false -mod=readonly`, once per source hash, into
`$LATTICE_GRPCBRIDGE_CACHE` (default `~/.cache/lattice/grpcbridge`). Protocol: JSON lines on stdin/stdout; requests
`describe {files, proto_paths}` (compile with protocompile; answer the D1-shaped JSON and a descriptor set), `call
{target, service, method, message, metadata}` (dynamicpb; answer `GrpcAnswer`), `health {target}`, `quit`. It dials
**only** `127.0.0.1`/`[::1]` with insecure credentials (any other target: error `non_loopback`), refuses streaming
methods (`streaming_not_supported`), and canonicalises its JSON (protojson's whitespace is deliberately unstable).

| criterion | `grpcio` (+ `protobuf`, + `grpcio-tools` for `.proto`) | pinned Go bridge |
|---|---|---|
| new Python dependency | yes: `pyproject.toml`, `uv.lock` change; C-extension wheel 5.7-6.3 MB (cp312 manylinux, 1.71.0) + tools 2.4-2.7 MB (m) | none; Python side byte-identical |
| `.proto` without `protoc` | no: needs `grpcio-tools`' bundled protoc, or server reflection | protocompile, pure Go |
| an **independent** proto oracle | no (our parser would feed the descriptors) | yes: protocompile vs D1's parser (probe P0-PARSE) |
| prerequisite already present | Python only | Go, already required by every Go target (`gocheck`) |
| same codec as the servers in scope | different implementation | grpc-go's, as the servers (a shared-bug risk, section 7) |
| per-call cost | in process | one long-lived process, a JSON line per call (p: 0.5-3 ms) |
| network at check time | none | none after the first `go mod download` (`-mod=readonly`); a failed build is `NOT RUN: bridge_build_failed`, never a silent skip |

`grpcurl` (also a pinned binary) is rejected: a process per call, its text output and exit codes are not a stable
contract, and the GitHub release is not fetchable here (403). If the orchestrator prefers `grpcio` (Q1), D1-D4 stand;
only the bridge and P0-PARSE's oracle change.

**D6 Workflows over rpcs.** `importer/workflows.py` treats an rpc as a request step: a response field `id` (or
`<x>_id`) feeding a later request field of the same name or `<table>_id` is a dependency edge, exactly as a response id
feeds a path parameter today; ownership chains and precondition guards as now. Flows are `Rpc` step lists, replayed on a
throwaway SQLite file; `verified` only when every step passes.

**D7 Report and map (listing only).** `implementation.build_report` and `appmap.build_appmap` list rpc endpoints beside
routes (key `impl:rpc:<Service>/<Method>`), with codes, auth, `http_bindings`, `state`, and `code:<path>:<line>`
evidence; `unimplemented` rpcs read like `spec_only` endpoints. With no rpc in the inventory, both outputs are
byte-identical.

**D8 Never:** a network target beyond loopback; a Docker container; `go build` or any write under the target root
(binaries go to scratch or the cache); git on the target; an LLM in extraction, checking or the bridge; an API key;
an edit to `.proto` or spec files by any Lattice command; a guess where D1 or D2 says `unread`.

**D9 Gated, not cut (language rules R12).** Each with its exit condition: layer A (gocheck rules) does not read rpc
handlers (exit: GO8xx rules take a handler set, not Gin routes); layer R / contracts on rpcs (exit: the probe oracle
gets an rpc transport); layer X races over rpcs (exit: the bridge supports concurrent calls); layer B / programs on rpc
endpoints (exit: the spec runtime gets an rpc transport); streaming (exit: a stream step form). Each refusal is a
stated code (`S029`, or a check note `rpc_layer_not_supported:<layer>`), never a pass.

## 3. Arms and corpora

| arm | needs | runs here | what it scores |
|---|---|---|---|
| **0 static** | `go` (gocheck, bridge `describe`), Python | **yes** | proto reading, grpc-go extraction, `RepoModel`, the `Rpc` form, validate/lint/review, candidate spec emission, determinism, **byte-compat of every HTTP output** |
| **G go-run** | `go`, pure-Go SQLite, the bridge, the process runtime | **yes** (no Docker) | the rpc transport, layers C and D, auth, reconcile, workflows, leaks, the **held-out score** |
| **M MySQL / Streak** | a Streak checkout (a0679e9, 1d00ede), Docker MySQL 8.4 | **NOT RUN here** | Streak reproduced as gRPC; HTTP byte-compat of Streak's inventory, report, map and impact |
| **L build** | the `claude` CLI, the rebuild loop, `buf` + `protoc-gen-go(-grpc)` via `go install` | yes if the CLI has quota (else NOT RUN) | build-from-spec on a toy proto (recommended: node I2, R2) |

**Corpora.**
- **N (dev, built by the build node):** `tests/grpc/fixtures/notes/`, a grpc-go app over GORM + `github.com/glebarez/
  sqlite` (pure Go) with **this shape, fixed now:** services `notes.v1.Accounts` {`Register`, `Login`, `Me`} and
  `notes.v1.Notes` {`CreateNote`, `GetNote`, `ListNotes`, `UpdateNote`, `PublishNote`, `ArchiveNote`, `DeleteNote`,
  `ShareNote` (proto only: `Unimplemented`), `WatchNotes` (server streaming)}; 11 rpcs; tables `users`, `notes`;
  `notes.state` machine `draft -> published -> archived`, `draft -> archived`; owner `Where("id = ? AND user_id = ?")`;
  a chained unary interceptor with a literal public set {`Register`, `Login`, `grpc.health.v1.Health/Check`};
  `(validate.rules)` bounds on 4 fields; `google.api.http` on `CreateNote` and `GetNote`, served by grpc-gateway on a
  second port; one handler returning a callee's `status.Error` (`return nil, err`); one plain `errors.New` (UNKNOWN);
  one code from a map (`unread`). `BUG=<k>` env variants (as `tests/spec/goapp`): 8 named bugs, one per D-row of PG-D.
- **T (proto torture set):** `tests/grpc/fixtures/protos/`, >= 30 constructs: each D1-supported construct at least once,
  and 10 unsupported ones (proto2, edition, extend, group, custom option, `import public`, `import weak`,
  `additional_bindings` (read, listed), validate `pattern`, client streaming), each with its expected `unread` kind and
  line in `expected_unread.json`.
- **H (held out, section 5).**
- **HTTP fallback corpus for byte-compat:** the 13 `tests/gocheck/fixtures/*` dirs, `tests/spec/goapp` + `go_harness`
  (DCX), `tests/spec/minishop.py` (CD, ok and the 10 broken variants), `tests/spec/fixtures/{implementation,impact}`,
  `tests/spec/golden/`, `dogfood/studio/check.sh`, and **G-mixed**: a Gin fixture that imports a gRPC client only.

## 4. Probes (runner `dogfood/grpc_probes.py`; blind pass rules)

**Arm 0 (static; runs here).**

| id | probe | prediction | pass |
|---|---|---|---|
| P0-PARSE | D1 vs the bridge's protocompile descriptors on T and N: services, rpcs (names, types, streaming flags), messages, fields (number, type, label, oneof, map key/value), enums, http options | supported constructs 100% equal; 0 disagreements (p) | 100% equal |
| P0-UNREAD | T's unsupported constructs: kind, file, line vs `expected_unread.json`; no element gains a field from an unread construct | 10/10 (p) | exact |
| P0-VAL | bounds from PGV and protovalidate on T and N's 4 fields -> `FieldSpec`/`RouteParam` bounds | exact | exact |
| P0-EXT | `gocheck extract` + `go_grpc` on N: 9 unary rpcs registered (8 implemented, `ShareNote` unimplemented), `WatchNotes` excluded, codes per rpc (written by the build node in `tests/grpc/fixtures/notes/truth.json` **before** the extractor exists, hashed in `freeze.sha256`), auth per rpc, 2 tables, owner uses, the 3 machine edges in `status_writes`, 2 `http_bindings`, 1 `plain_error`, 1 `grpc_code_not_literal` unread | exact on N (dev corpus) | exact |
| P0-MODEL | `RepoModel` of N: rpcs as `Route(method="RPC", path="/notes.v1.Notes/GetNote", transport="grpc")`; gateway bindings on the rpc, not as routes: 9 endpoints, not 11 | exact | exact |
| P0-SPEC | `Rpc` unit facts: hash/eq; S026-S029 on 8 crafted specs (2 each); L013 on 2 pairs; review step line; access matrix of a mixed spec (groups, columns, cells) | exact | 100% |
| P0-SITES | each of the 49 `isinstance` sites has a test that feeds a scenario with an `Rpc` and asserts the stated behaviour (handled or skipped, no exception, no `Step` attribute read) | 49/49 | 49/49 |
| P0-EMIT | the candidate spec emitted from N loads, validates with 0 errors, has >= 1 `Rpc` per implemented rpc, 0 for `ShareNote` and `WatchNotes` | as stated | exact |
| P0-DET | extract, model, emitted spec and bridge `describe` on N and T: 10 processes, random `PYTHONHASHSEED`, a second scratch root; scan for absolute paths, ISO times, ports | identical; none | 10/10 |
| P0-NW | no-write: snapshot (sha256, mode, mtime, listing incl. hidden files) of N, T and H's roots around import x10 and describe x10; a recording `claude` shim first on `PATH` | 0 changed, 0 new, 0 calls | all 0 |
| P0-BC | byte-compat, the promise in section 9 | all | all |

**Arm G (Go servers over SQLite; runs here, no Docker).**

| id | probe | prediction | pass |
|---|---|---|---|
| PG-BR | bridge: builds from `go.sum` with `-mod=readonly` and no network after the first download (`GOFLAGS=-mod=readonly GOPROXY=off` rebuild); a non-loopback target refused; a streaming method refused; `health` on N; one call x10 gives byte-identical canonical answers | all | all |
| PG-REC | reconcile on N: each `implemented` rpc called with an empty message answers a code != `UNIMPLEMENTED`; `ShareNote` answers `UNIMPLEMENTED`; an rpc absent from the proto answers `UNIMPLEMENTED` | 8/8, 1/1, 1/1 | exact |
| PG-D | N's hand-written dev spec (>= 12 scenarios with `Rpc` steps, written by the builder): 0 findings on `ok`; each of the 8 `BUG` variants gives >= 1 layer D finding on its named scenario and none elsewhere (p: 8/8 localised) | 0; 8/8 | 0 on ok; 8/8 caught |
| PG-C | an invariant (`notes.user_id` references `users`; a count bound) broken by one variant is caught by layer C after the rpc; quiet on ok | caught; quiet | exact |
| PG-J | `lattice.grpcjson/1`: int64 2^53-1 a number, 2^53+1 a string; enums by name; unpopulated fields present; `expect_message` subset on each | exact | exact |
| PG-MIX | one scenario mixing gateway HTTP (`POST /v1/notes`) and rpc steps on one process with `grpc_port_env`; the HTTP and rpc answers for the same note agree field by field | agree | exact |
| PG-AUTH | `AuthDriver.rpc` login as ann and ben; the access matrix of N's spec vs observed codes: every cell's stated codes equal the observed ones | 100% | 100% |
| PG-WF | workflow layer on N: >= 3 flows recovered (register -> login -> create -> publish -> archive; create -> get as other user -> NOT_FOUND; list after delete); all replayed; 0 flows `verified` that fail on replay | 3-6 flows (p); 0 | 0 verified-but-failing; >= 3 flows |
| PG-LEAK | `process.live_processes() == []` and no bridge process after every check, after a startup-failure run (`ready: grpc` never `SERVING`) and after a check that raises mid-scenario | 0 | 0 |
| PG-X | wall time on N: bridge call p50; the dev spec (layers CD); a cold bridge build | 0.5-3 ms; 3-12 s; 20-90 s (p) | <= 5 ms; <= 30 s; <= 180 s |
| PG-HO | the held-out score (section 5) | section 5 | section 5 |

**Arm M (NOT RUN here; gated when run on a host with a Streak checkout and Docker).**

| id | probe | pass when run |
|---|---|---|
| PM-S | `lattice import` on Streak a0679e9 and 1d00ede: `extract.json`, the inventory, `build_report`, `build_appmap` and D's impact (T2+T3) on the 6 F dev candidates byte-identical to `git archive 6a318e5`; `select()` still answers `gin` | identical |
| PM-SG | Streak reproduced as grpc-go by a separate agent against Streak's spec (MySQL): rpc recall/precision, codes, flows replayed, ACD on its gRPC spec | as PG-HO's rules |

**Arm L (build round; recommended as node I2).** A toy proto (`tests/grpc/fixtures/toybuild/`: 1 service, 4 unary rpcs,
1 table) plus its spec with `Rpc` scenarios; `studio.toml [grpc] protos, generate = "<project command>"` (buf and the
two plugins installed by `go install` at pinned versions into scratch). The rebuild loop, through the `claude` CLI only,
`sonnet`, `--max-turns 40`, **USD 5.00 cap** on the CLI's list estimate. Gates: the proto and spec sha256 unchanged
after the loop (hard); stubs produced only by the declared command; the final app passes the spec's CD check (p: within
<= 3 rounds). A usage limit makes Arm L **NOT RUN** (or *partial*), never a pass.

## 5. The held-out app (Arm G, scored once, here)

**Authoring (R7).** A separate agent (neither the builder nor this author), dispatched by the orchestrator after this
commit and before the build node runs a gated probe, writes `$SCRATCH/heldout/<app>/`: a grpc-go app (pure-Go SQLite, no
Docker, buildable with the pinned module versions or others it pins in its own `go.sum`), its protos, a harness
(`harness.py`: `make_app`, `build_db`, `RUNTIME`), and `truth.json`: rpcs (full name, state implemented | unimplemented
| streaming), codes each implemented handler can return (static truth, with file:line), auth per rpc (Public | Member),
tables, owner checks, and >= 3 flows as rpc sequences with expected codes. It may use idioms the dev corpus lacks (it
is told the D1/D2 scope, not N's code). The orchestrator records the sha256 of the sorted file list in
`freeze.sha256`; the builder never opens `heldout/`; the runner reads it.

| metric | prediction | pass |
|---|---|---|
| rpc recall / precision (state included) | 0.90-1.00 / 0.95-1.00 (p) | >= 0.90 / >= 0.95 |
| codes per rpc, micro-averaged, recall / precision | 0.70-0.90 / 0.90-1.00 (p) | >= 0.70 / >= 0.90 |
| auth: rpcs labelled `Public`/`Member` that contradict truth | 0 (p); `unclear` share 0-0.3 | **0 wrong** (hard); unclear reported |
| tables recall | 1.0 (p) | >= 0.9 |
| reconcile (PG-REC on H) | 100% | 100% |
| flows: truth flows replayed as `Rpc` scenarios pass on H; recovered flows verified-but-failing | all pass; 0 | 0 verified-but-failing; recall reported |
| determinism (P0-DET on H) | identical | 10/10 |

## 6. Mutants (`dogfood/grpc_mutants.py`, on a scratch copy; the control passes first; predicted 16/16)

| id | mutant | killed by |
|---|---|---|
| GX1 | the proto reader treats `repeated` / proto3 `optional` as a single field | P0-PARSE |
| GX2 | a streaming rpc kept as unary | P0-UNREAD, P0-EXT |
| GX3 | an rpc served by `Unimplemented*Server` reported `implemented` | P0-EXT, PG-REC |
| GX4 | `status.Errorf`'s code ignored (every site `UNKNOWN`) | P0-EXT |
| GX5 | the interceptor's public set ignored (every rpc `Member`) | P0-EXT, PG-AUTH |
| GX6 | `return nil, err` not followed through the closure | P0-EXT (N's passthrough rpc) |
| GX7 | a non-literal code guessed as the first map value instead of `unread` | P0-EXT |
| GX8 | `goextract` emits `"grpc": null` for an HTTP-only module | P0-BC (fixture hashes) |
| GX9 | `Route.transport` serialised for HTTP routes (`OMIT_DEFAULT` dropped) | P0-BC (RepoModel JSON) |
| GX10 | `go_grpc.detect` true on G-mixed (client import only) | P0-BC (`select` answers `gin`) |
| GX11 | a gateway binding added as a second endpoint | P0-MODEL (9, not 11) |
| GX12 | the checker passes an `Rpc` whatever its code (code not compared) | PG-D |
| GX13 | int64 kept as protojson strings | PG-J |
| GX14 | `expect_db` / layer C skipped after an rpc | PG-C |
| GX15 | the bridge dials a non-loopback target | PG-BR |
| GX16 | no process-group stop after a `ready: grpc` startup failure | PG-LEAK |

## 7. Risks, and what this cannot show

1. **Idiom overfitting to N.** The dev corpus is written alongside the extractor. Mitigation: the held-out app by a
   separate agent, scored once; the `unread` list instead of guesses; the 0-wrong-auth hard gate.
2. **Shared-library correlation.** The bridge and the servers both use grpc-go and protobuf-go: a codec bug shared by
   both would be invisible. P0-PARSE compares two parsers (D1 vs protocompile), not two codecs.
3. **Interceptor-heavy auth will be partial.** Only literal allowlists are read; JWT claims, per-method options and
   reflection-driven policies become `unclear`. Codes chosen at run time are `unread`.
4. **The `Rpc` form touches 49 sites in 15 modules.** The risk is a site that silently treats an `Rpc` as a `Step`.
   P0-SITES covers each; any missed site is a finding.
5. **R5 is a deviation from protojson.** A client comparing raw protojson would see strings; Lattice's form is
   documented and versioned (`lattice.grpcjson/1`).
6. **New surface:** a Go module with third-party dependencies (`go.sum` pinned), a long-lived bridge process, a second
   port. A Go proxy outage blocks the first bridge build: reported NOT RUN, never skipped.
7. **Cannot show:** Streak behaviour over gRPC and MySQL (Arm M, NOT RUN here); Streak HTTP byte-compat (only the
   fallback corpus here); non-Go servers; streaming; TLS; deadlines; layer A/R/X/B over rpcs (D9); the build round if
   Arm L is deferred or hits a usage limit.

## 8. What cannot be run here

- **No Streak checkout:** PM-S and PM-SG are **NOT RUN**; HTTP byte-compat is measured on the fallback corpus only
  (section 3), and the results doc says so in its first lines. The Streak-as-gRPC development corpus of the plan is
  **NOT RUN** (it also needs Streak's spec, which is not in this container).
- **No Docker daemon:** nothing in Arms 0 and G uses Docker (pure-Go SQLite, `ready: grpc`); `go_gin.reconcile`
  (Docker MySQL) is not run and not needed. Any row that would need MySQL is Arm M.
- **No GitHub release downloads (403):** no prebuilt `protoc` or `grpcurl`; everything comes from the Go proxy (Arm L's
  `buf` and plugins via `go install`).
- **Model use:** only Arm L, only through the `claude` CLI, capped; no API key anywhere. A usage limit is NOT RUN.

## 9. Byte-compat promise and frozen hashes

**Promise.** For an app with no `.proto` file and no gRPC server registration, and for a spec with no `Rpc` step and no
`protos`, every existing output is byte-identical to `git archive 6a318e5`: `gocheck extract` JSON on the 13
`tests/gocheck/fixtures/*` dirs (baseline sha256 recorded by the runner; recon: `goextract` `f74727094a25cc1d...`,
`goextract_workflow` `ee3b906bd4e4e799...`); the `go_gin` `RepoModel` JSON on them; `select()` answers; the
`go_harness` DCX check report and minishop's CD reports on ok and its 10 variants (timings excluded); review Markdown,
`access_groups`, `requirements_markdown`, validate and lint output on every spec under `tests/spec` and
`tests/spec/golden/`; `build_report` and `build_appmap` on `tests/spec/fixtures/implementation`; D's impact JSON on
`tests/spec/fixtures/impact`; `lattice --help`, `python -m lattice.spec check --help`; `tools/list` and MCP
instructions; `dogfood/studio/check.sh` with the same step count; `pytest tests/spec tests/importer tests/gocheck
tests/mcp tests/studio` with only the same pre-existing failure ids (baseline taken before the first edit).

**Frozen (empty diff; sha256 prefixes at 6a318e5, the runner checks the full hashes):**

```
6f1112bbb66e lattice/gocheck/src/bounds.go        c73bd244f27c lattice/gocheck/src/boundset.go
72a35ec417e5 lattice/gocheck/src/load.go          1fd313df23e1 lattice/gocheck/src/machine.go
ed20d501a321 lattice/gocheck/src/models.go        c060a0dd2787 lattice/gocheck/src/owner.go
b6b3e29f7f47 lattice/gocheck/src/partial.go       b49cc8259d6b lattice/gocheck/src/private.go
891a8052a913 lattice/gocheck/src/reach.go         005614c24c6f lattice/gocheck/src/routes.go
8ff4243874ef lattice/gocheck/src/txflag.go        84427378785c lattice/gocheck/src/types.go
c1ff7ff0b711 lattice/gocheck/src/util.go          ed95d0fb9d93 lattice/gocheck/src/goextract_closure.go
bd358798d985 lattice/gocheck/src/goextract_owner.go 1f8db0956e87 lattice/gocheck/src/goextract_raise.go
4441d0e3f0ff lattice/gocheck/src/goextract_routes.go 45339c5eb1c9 lattice/gocheck/src/goextract_tables.go
3eb32761adfb lattice/gocheck/src/goextract_workflow.go d3cdb84ddd40 lattice/gocheck/__init__.py
9f24e9bb5ac8 lattice/importer/frontends/go_gin.py 6f86fc6cfb2b lattice/spec/impact_tier2.py
22228513beba lattice/spec/impact_tier3.py         6b0f1cbdd857 pyproject.toml
0b63bde995f1 uv.lock
```

plus `lattice/gocheck/src/go.mod`, `lattice/spec/ask.py`, `lattice/spec/impact_report.py`, `lattice/mcp/tools.py`,
`lattice/studio/**`, existing tests, goldens and fixtures. (`impact_tier2/3.py` and `ask.py` hold `isinstance` sites:
they are frozen, so P0-SITES must show they skip an `Rpc` safely as they are; if one cannot, the build stops and
reports, it does not edit them.) **Additive edits, hashes recorded at the freeze:** `goextract.go` (the `grpc` key and
one call), `goextract_assemble.go`, `main.go` (only if a `describe` subcommand is needed; preferred: none),
`appmodel.py`, `frontends/__init__.py`, `transport.py`, `spec/model.py`, `spec/__init__.py`, `check.py`, `process.py`,
`review.py`, `validate.py`, `lint.py`, `preconditions.py`, `implementation.py`, `appmap.py`, `importer/workflows.py`,
`importer/emit_spec.py`, and the other modules among the 15 whose `isinstance` site needs an `Rpc` arm.

## 10. Blind vs post hoc, and commands

All predictions and pass rules are fixed now. N's `truth.json` is written and hashed before the extractor code; the
held-out set is written and hashed by a separate agent before any gated probe and scored **once** after the freeze. A
missed prediction is a **finding**; fixes are separate post-hoc commits, rerun with deltas reported, and a fix made
after seeing H is never re-scored as blind. **Pass:** every gated row of Arms 0 and G, section 5, mutants 16/16 (by
probes or named unit tests, disclosed), and P0-BC; Arm M reported NOT RUN in the first line of the results; Arm L passed,
NOT RUN, or deferred to I2 as ruled.

```sh
export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/user/lattice
SCRATCH=/tmp/claude-0/-home-user-lattice/<session>/scratchpad/grpc
export LATTICE_GOCHECK_CACHE=$SCRATCH/gocache LATTICE_GRPCBRIDGE_CACHE=$SCRATCH/bridgecache
git -C /home/user/lattice archive 6a318e5 | tar -x -C "$SCRATCH/base"                      # byte-compat baseline
uv run python -m pytest tests/spec tests/importer tests/gocheck tests/mcp tests/studio -q -rs # baseline, before edits
sha256sum tests/grpc/fixtures/notes/truth.json > "$SCRATCH/freeze-truth.sha256"            # before extractor code
uv run python dogfood/grpc_probes.py --arm 0 --scratch "$SCRATCH/p0" --base "$SCRATCH/base" --out docs/results/data/2026-10-17-grpc
uv run python dogfood/grpc_probes.py --arm G --scratch "$SCRATCH/pg" --out docs/results/data/2026-10-17-grpc
uv run python dogfood/grpc_mutants.py --scratch "$SCRATCH/m" $(seq -f GX%g 1 16)
uv run python -m pytest tests/grpc -q
sha256sum lattice/gocheck/src/goextract_proto.go lattice/gocheck/src/goextract_grpc.go lattice/grpcbridge/*.go \
    lattice/importer/frontends/go_grpc.py lattice/spec/grpc.py > "$SCRATCH/freeze.sha256"   # BEFORE H is scored
uv run python dogfood/grpc_probes.py --arm H --heldout "$SCRATCH/heldout" --out .../heldout  # orchestrator, once
uv run ruff check <changed .py>; uv run ruff format --check <created .py>; gofmt -l lattice/gocheck/src lattice/grpcbridge
```

## 11. File ownership (build node)

**Owns (new):** `lattice/gocheck/src/goextract_proto.go`, `goextract_grpc.go`; `lattice/grpcbridge/` (`go.mod`, `go.sum`,
`*.go`); `lattice/importer/frontends/go_grpc.py`; `lattice/spec/grpc.py`; `tests/grpc/` (tests, `fixtures/notes/`,
`fixtures/protos/`, `fixtures/gin_client_only/`, `fixtures/toybuild/` if Arm L runs); `dogfood/grpc_probes.py`,
`dogfood/grpc_mutants.py`; the results data directory. **Additive edits only:** the list in section 9. **Not touched:**
the frozen list; Streak, `job-scheduler*`, the owner's checkout outside the worktree; `$SCRATCH/heldout/`. The builder
makes no commits; the orchestrator stages explicit paths only (never `git add -A`). No API key; model use only via the
`claude` CLI, only in Arm L.

## 12. Questions for the orchestrator

- **Q1 (R1).** Approve the pinned Go bridge (grpc-go v1.71.0, protobuf-go v1.36.5, protocompile v0.14.1) over `grpcio`?
- **Q2 (R2).** Round 1 = items 1-4 + report/map listing; Arm L (build round) as a separate node I2?
- **Q3 (R3).** A separate `grpc-go` front end that also carries Gin routes, detected only on a server registration?
- **Q4 (R4).** A new `Rpc` step class (49 sites to cover) rather than `Step(method="RPC")` (fewer sites, but every HTTP
  consumer would read `expect_status` of an rpc)?
- **Q5 (R5).** Accept `lattice.grpcjson/1`'s int64-as-number deviation from protojson?
- **Q6 (R6).** Layers A, R, X and B `not supported` for rpcs this round, with the exit conditions of D9?
- **Q7 (R7).** Dispatch the held-out author after this commit; is a pure-Go-SQLite held-out acceptable as the gated
  held-out while Streak-as-gRPC (Arm M) waits for a host with Streak and Docker?
- **Q8.** `impact_tier2.py`, `impact_tier3.py` and `ask.py` are frozen: if one of their `isinstance` sites cannot skip
  an `Rpc` safely, may the build add a one-line `Rpc` guard there (recorded as a finding), or must it stop?

## Amendment 1 (orchestrator rulings, 2026-10-17; defaults, overturnable)

- Q1: approved, the pinned Go bridge. Q2: round 1 = items 1-4 plus report/map listing; Arm L (build round) is a separate node I2, not started. Q3: approved, detected only on a server registration. Q4: a new `Rpc` step class. Q5: accepted, recorded as a documented deviation. Q6: accepted. Q7: a pure-Go-SQLite held-out is the gated held-out; Arm M waits for a host with Streak and Docker and is NOT RUN here.
- Q8: if an `isinstance` site in a frozen file cannot skip an `Rpc` safely, the build may add a one-line `Rpc` guard, recorded as a finding and listed in the results; it does not stop.
