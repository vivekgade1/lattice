# Lattice sub-project 5 — Go target and the Go benchmark (design, for review)

> Status: DRAFT for review · 2026-09-28. Direction chosen by the user: add Go as a compiled target of the existing plan IRs and benchmark it against Go written directly by the model, on API-profile programs (main experiment) and T2 scripts (control). Scope **C**: benchmark-first, but built so a production Go target can follow without redesign. Database: SQLite, with performance measured under real HTTP load; Postgres is a separate later sub-project. Context: `docs/lattice-vision.md` §7, `docs/results/2026-09-26-bench-smoke.md`.

## 1. Goal

One Lattice program, one checker, one plan; Go becomes another emitter beside Python/FastAPI, SQL and polars. The model writes the same Lattice regardless of target.

Questions the benchmark answers:

1. **Main:** when the target is Go, does writing Lattice beat writing Go directly on cost per correct task and silent failures, at equal runtime performance?
2. **Control:** on general script logic with no domain rules (T2), how much does Lattice cost against writing Go directly?
3. **Equivalence:** does one Lattice program behave identically when compiled to Python and to Go?

Non-goals: a faster-than-Python claim (Go vs Python measures the compiler, not Lattice); production deployment features; lifting Python apps into Go.

## 2. Architecture

Nothing before the checker changes. The one new layer is **T2 IR**.

```
source ─► parse ─► p1–p7 + T2 checker ─► p8_target ─► CheckedProgram
                                                         │
                  ├─► api/ir.lower_api ─► EndpointPlan ─┬─► api/codegen     (Python, unchanged)
                  │                                     └─► gogen/endpoints (new)
                  └─► t2ir/lower ─► T2 IR ─┬─► t2/emit_py   (new; replaces AST instrumentation)
                                           └─► gogen/t2     (new)
```

| Unit | Job | Depends on |
|---|---|---|
| `lattice/t2ir/nodes.py` | Frozen dataclasses: typed expressions and statements, explicit `Fuel(cost)` and size-check nodes, calls, records. Every node carries its `T2Type`. No Python `ast` inside. | `check/t2/types.py` |
| `lattice/t2ir/lower.py` | Checked T2 AST → T2 IR, using the checker's inferred types. Decides every fuel charge and size check (today decided while instrumenting the Python AST). | T2 checker |
| `lattice/t2/emit_py.py` | T2 IR → Python source running on the existing `t2/support.py` | T2 IR |
| `lattice/targets.py` | The v1.1 semantics table (§3) and `TargetCapabilities` for `python` and `go`, mirroring `api/backend.Capabilities` | — |
| `lattice/check/p8_target.py` | Rejects programs the chosen target cannot run, in the style of `p7_backend` (§3.2) | `targets.py` |
| `lattice/gogen/prelude/` | Hand-written Go module: request pipeline (parameter parsing and constraints, token auth, error bodies, UUID/DateTime wire formats, transactions, outbox), T2 support (fuel, checked Int, ordered map, slicing, formatting), the SQLite functions the Python runtime registers (`modernc.org/sqlite`, pure Go) | — |
| `lattice/gogen/endpoints.py` | `EndpointPlan` → one `net/http` handler per endpoint: each plan step becomes a straight-line block (no runtime interpreter); rows scan into structs generated from each step's `Schema`, pointer fields for nullable columns | `api/ir.py` |
| `lattice/gogen/t2.py` | T2 IR → Go functions | T2 IR |
| `lattice/gogen/build.py` | Writes a Go module (prelude + generated files), runs `go build`, returns the binary path; caches by content hash | Go toolchain |
| `lattice/importer/verify.py` (extended) | HTTP adapter: start a binary against a copy of the seeded DB and drive cases over a socket; comparison logic unchanged | — |

Unchanged: surface syntax, prompts, diagnostics, `p1`–`p7`, `EndpointPlan`, the SQL layer, the tabular profile. Programs marked `# lattice: v1` keep working (§3.3).

Removal: once `emit_py` passes the differential test against the current instrumentation path (§6), the instrumentation path in `lattice/t2/compile.py` is deleted, leaving one path.

## 3. Semantics v1.1

Lattice's meaning is currently whatever Python does. v1.1 writes it down once, in `lattice/targets.py`, and both targets implement it.

### 3.1 Rules (every target)

| Area | Rule | Enforced by |
|---|---|---|
| `Int` | Signed 64-bit. Overflow raises **R623** (new). Today T2 allows up to 65,536 bits (`T2_MAX_INT_BITS`). | Checked arithmetic in both emitters |
| `Float` | IEEE-754 double; text is the shortest round-trip form in Python's `repr` format | Prelude formatter per target |
| Division | `/` gives Float; `//` and `%` floor as in Python | Go helpers (Go truncates) |
| `Str` | `len`, indexing and slicing are over code points | Go helpers on `[]rune` |
| `list` | Negative indexes and Python slice rules (clamped bounds, steps); every slice read is a **new list** (Go copies, never aliases); slice assignment stays rejected (L913) | Go slice helpers |
| `dict` | Insertion-ordered iteration | Generated Go `OrderedMap[K, V]` |
| `set` | Insertion-ordered iteration on every target (Python's string hashing is seeded per process) | Generated Go `OrderedSet[T]` |
| `Counter.most_common` | Ties in insertion order | Prelude |
| Regex | RE2 subset: lookaround and backreferences rejected on every target; also rejects possessive repeats and atomic groups; `\d \w \s \b` are ASCII (Python compiles with `re.ASCII`) | New checker rule next to `check/redos.py` |
| `str.upper/lower/casefold/title` | Full Unicode case mapping (`'ß'.upper() == 'SS'`) | Prelude |
| `str()` / f-string of a `list`, `dict`, `set` or `tuple` | L892 on Go in this sub-project | `p8_target` |
| `DateTime` / `Duration` | UTC, microsecond precision, the existing wire format | Prelude |
| `IndexError` / `KeyError` | Catchable Lattice errors on both targets; Go returns typed errors, never `panic` for them | Go T2 support |
| Fuel, depth, size | Charged exactly where T2 IR places `Fuel` and size-check nodes; R620/R621/R622 at the same step on both targets | T2 IR |

### 3.2 Target capabilities (`p8_target`)

| Code | Rule |
|---|---|
| L890 | A `native.<name>` call (or `NativeToken` auth) needs a native implementation for the target; none exist for `go` in this sub-project |
| L891 | A tabular-profile program has no `go` target yet |
| L892 | A T2 call (builtin, method or stdlib function) the target does not implement yet — the staging mechanism |

The target is a check option (`--target python|go`, default `python`). A program passes for Go only if it passes for Python and `p8_target` finds nothing.

### 3.3 Versioning

v1.1 only makes Lattice stricter. A v1 program either behaves the same or is rejected at check time, with one exception: `Int` values beyond 64 bits now fail at runtime with R623. That change is recorded in the version notes. The header `# lattice: v1.1` is accepted; `v1` programs run under v1.1 rules.

## 4. Runtime behaviour on Go

Request flow: parse and constrain parameters → authenticate (one statement) → `BEGIN` → plan steps in order (reads scan into typed structs; `one()`/`require()` raise their `ErrorSpec`; T2 steps call generated procedures with the request's `*Fuel`; writes use `RETURNING`; `for_each` inserts outbox rows) → `COMMIT` → JSON response.

| Failure | Response (same status, code and body on both targets) |
|---|---|
| Parameter or constraint violation | 422, existing validation envelope |
| Unknown token | 401 |
| Declared `ErrorSpec` | Its status and JSON body |
| `IndexError`/`KeyError` | Raised as a Lattice error value (`*rt.Error`) that `try` blocks catch and the handler turns into a 500; never an unrecovered panic |
| R310, R620, R621, R622, R623 | 500 with the located message, logged |
| Unexpected Go error or panic | 500 `internal`; handlers `recover()`, the server keeps running |

Every failure rolls back. Outbox rows exist only if the transaction commits.

## 5. Benchmark

### 5.1 Transport

All model calls go through `claude -p` via the existing `lattice/llm/cli_client.ClaudeCliClient`, now also wired into the API benchmark (`bench/cli.py`) and the new Go benchmark (`--transport cli`, the default; `--transport api` stays available). The client's isolation flags are unchanged (no tools, no MCP, no settings, slash commands and skills disabled, empty working directory), so every arm sees exactly its own prompt.

- **Resumable:** each finished run is saved; a re-run skips it. `CliUsageLimitError` stops the run cleanly.
- **Reporting:** CLI cost is a list-price estimate (`cost_basis = "cli_list_estimate"`). Model-call latency includes process start-up and is not a performance metric.

### 5.2 Part A: API profile (main)

The Go baseline gets `bench/api_kit_go/`, the Go counterpart of `bench/api_kit/platform_kit.py`: it opens the DB through the counting driver wrapper, finds the caller from the bearer token and gates by role. Ownership, validation, error mapping and pagination are the model's job, exactly as they are the Lattice program's.

| Arm | Model writes | Repair (budget 2) |
|---|---|---|
| G1 | `handlers.go` against the Go kit | none |
| G | the same | `go build` / `go vet` output plus one smoke request |
| B | Lattice, compiled to Go | checker diagnostics plus one smoke request |
| C | Lattice, compiled to Go | none |
| X | no model: each correct B program compiled to Python and Go and run on the task's cases | — |

- **Tasks:** the existing 40 API tasks under the explicit and implicit-requirements conditions, plus about 6 new Go-specific traps (a nullable column scanned into a non-pointer field; a multi-write endpoint that must roll back; an ignored `rows.Err()`; string-built SQL; an unchecked `err` on a write; a missing `rows.Close()` under load).
- **Models:** haiku-4-5, sonnet-5, opus-5; hints and no-hints; at least 3 reps on trap tasks.
- **Query counting:** both arms' binaries open SQLite through the same counting wrapper.

### 5.3 Part B: T2 scripts (control)

- **Tasks:** about 30 new business-logic tasks (pricing, date and duration rules, validation, text normalisation, record aggregation), each with a reference solution and hidden generated tests; plus about 20 MultiPL-E Go tasks that T2 can express, for comparison with published numbers.
- **Arms:** GS (model writes a Go function) and LS (model writes a Lattice `@procedure`, compiled to Go), each one-shot and with repair (budget 2).

### 5.4 Metrics

- Correctness: correct, silent and loud, by trap family
- Cost: cost per correct task, input and output tokens, repair rounds
- Program size of what the model writes: lines, AST nodes (Go via `go/ast`, Lattice via Python's `ast`), characters
- Binary: p50/p99 latency and requests per second at concurrency 1, 8 and 32 (load driver in `bench/load/`, a small Go program using vegeta as a library, pinned in its `go.mod`); RSS memory; binary size; cold start; queries per request
- Cross-target: X mismatches

### 5.5 Criteria (fixed before the run)

- **Continue** if all hold: B ≤ 0.6× G cost per correct task on Sonnet and Opus; B has fewer silent failures than G on traps and the implicit condition; X has 0 mismatches; the median over tasks of B's p99 latency is within ±10% of the same median for G.
- **Stop** if G matches B on silent failures and B is not at least 30% cheaper.
- **Control:** LS reaches ≥ 90% of GS's accuracy; otherwise the write-up records T2 as a cost that domain wins must outweigh.

## 6. Testing

| Layer | Test |
|---|---|
| T2 IR lowering | Golden IR dumps, one per T2 construct in the existing T2 tests |
| `emit_py` vs instrumentation path | Differential over every existing T2 test and fixture: same values and the same R620/R621/R622 at the same points |
| Semantics v1.1 | Shared fixture suite (~150 cases: overflow → R623, floor division, float text, rune slicing, negative indexes, copy-on-slice, dict order, RE2 rejection, datetime wire format), each run on both targets against the expected value |
| `p8_target` | One positive and one negative test per rule |
| Go prelude | `go test` unit tests; Python and Go prelude helpers compared on the same inputs |
| Equivalence gate | The API codegen differential registry (`tests/api/test_codegen_differential.py` `PROGRAMS`: the 40 task programs with probes, plus the t2/hardening/loops/gaps/mine/round2/partb/n1b/solo programs) — every program that passes `check_source(..., target="go")`; programs rejected only with L890 are listed and asserted as the expected exclusions. The FastAPI template suite is excluded: its auth is a native (`NativeToken("decode_token")`). Run against Lattice→Python and Lattice→Go: 0 mismatches, equal SQL counts |
| Fuel parity | Procedures built to exhaust fuel at a known step fail at the same step on both targets |
| Harness | Go kit and runner unit tests; dry run of every arm with canned responses before any paid run |

## 7. Build order

1. `targets.py`, `p8_target` (L890–L892), the RE2 rule, R623 in the Python runtime
2. T2 IR: nodes, lowering, `emit_py`; differential against the instrumentation path, then delete it
3. Go prelude and SQLite functions, with `go test`
4. Go T2 emitter, through the shared semantics suite
5. Go endpoint emitter, `build.py`, verifier HTTP adapter; **equivalence gate: 0 mismatches**
6. Benchmark: CLI transport in `bench/cli.py`, Go kit, arms G1/G/B/C/X and GS/LS, new trap tasks, T2 script suite, MultiPL-E subset, load driver, dry runs, cost estimate
7. Live runs and results in `docs/results/`, judged against §5.5

**Stop point:** if step 5 cannot reach 0 mismatches on the template suite, the semantics are not yet defined well enough; fix that before any model runs.

## 8. Out of scope (follow-ups)

- **Lattice skill and agent arm.** A Claude Code skill that loads only the needed sections of the language guide (the Lattice system prompt is ~15k characters, a main cost driver in the thin slice), plus `lattice check` / `lattice build --target go` as tools, compared against Claude Code writing Go with `go build` and `go test`. This measures the real agent setting; it is kept out of this controlled comparison.
- Postgres/MySQL dialects (roadmap sub-project 4), for both targets.
- Natives in Go, the tabular profile on Go (via SQL on DuckDB), Java and Rust targets.
- Production features: configuration, TLS, graceful shutdown, metrics, migrations, containers.
