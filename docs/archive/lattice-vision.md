# Lattice — Vision

> Status: living document · 2026-09-27 · Companion to the feasibility report (`docs/LLM-Native Coding Language Feasibility v1.md`), the language spec (`docs/archive/superpowers/specs/2026-09-26-lattice-design.md`) and the API spec (`docs/archive/superpowers/specs/2026-09-27-lattice-api-design.md`). Evidence: `docs/results/2026-09-26-bench-smoke.md`.

> **Status 2026-09-29:** the pre-registered confirmation run did not support the central claim (LLM edits through Lattice, or with its checks, are safer than plain Python edits on CRUD tickets). See `docs/lattice-reference.md` §6 and the next step, `docs/archive/option1-handoff.md`.

## 1. The idea

**Lattice is the native interface through which LLMs write and change software.**

1. **Easy for an LLM to write.** Lattice programs are written in Python syntax the model already knows, with a small, regular vocabulary and precise diagnostics that tell the model exactly how to fix a mistake.
2. **Expressive.** Complex logic — multi-step analytics, CRUD APIs with business rules, and (roadmap) workflows and custom algorithms — is expressed as a composition of checked constructs, not free-form code.
3. **Correct by construction.** Every program is parsed to an AST and checked before anything runs; each construct carries a guarantee (types and schemas, no silent row duplication, missing values handled explicitly, ownership/no IDOR, validated inputs, declared errors, bounded work, confined effects).
4. **Optimally converted to code.** The checked AST is lowered to a language-neutral plan (IR) and compiled to efficient code for real platforms: polars today, one SQL statement per read, a FastAPI application, and more targets over time.
5. **The interface for changing existing code.** Existing code is *lifted* into Lattice, the LLM edits the Lattice form under its guarantees, and code is emitted back — so every LLM change to a codebase passes through the same checks.
6. **Self-improving.** The language, its library, and the models' fluency in it improve as Lattice sees more code — through a governed loop, never by silently changing its own guarantees.

```
 existing code ──lift──►                                   ┌──► polars / SQL
 (Python, SQL, FastAPI…)   Lattice source ─parse─► AST ─check─► plan (IR) ─compile─┼──► FastAPI app
 LLM (write / edit) ─────►  (Python syntax)            ▲                            └──► future targets
                                 ▲                     │ diagnostics + fixes
                                 └──── repair loop ────┘
            corpus of verified programs ──► learning loop (§5) ──► better prompts, library, language
```

## 2. Where we are (built and measured)

| Part | Status |
|---|---|
| DSL as a profile over Python's AST, six checker passes, plan IR | Built |
| Tabular profile (pipelines) executed on polars | Built |
| API profile (endpoints with auth/ownership, validation, errors, pagination) — Lattice runtime compiling each read to one SQL statement | Built |
| FastAPI code generation from the same plan, proven equal to the runtime by differential testing (1,460 compared requests, 0 mismatches) | Built |
| LLM write loop: in-context language guide + checker + repair | Built |
| Benchmark harness with fair arms, silent/loud classification, cost and program metrics | Built |
| **Evidence:** on claude-haiku-4-5 without pitfall advice Lattice cut silent wrong answers on trap tasks from 9–15 to 2–4 and raised accuracy from ~72–82% to 85–94% at equal cost per correct answer; on frontier models accuracy is at parity at 1.4–1.5× cost | Measured |
| Real-app language extensions (Uuid/DateTime, page totals, response models, partial updates, natives, pluggable auth, backend capability model) | Built — the official FastAPI template's routes match on 49/53 requests; the other 4 are Lattice policy deviations (page cap, 401 for bad tokens) |
| Loop constructs (`window`, `traverse`, `iterate`, `for_each` with outbox) | Built — 11/11 real-style loops equivalent, 1 documented native island |
| Lifting existing code into Lattice | **In progress** (§4): extraction + profile generation and the generated-suite verifier are built; lifters, LLM close-the-gap loop and method experiment are under way |
| Self-improvement loop | **Partly manual** (§5): every language fix so far came from benchmark failures, done by hand |
| Go target: a second compiler for the T2 (procedure) profile, own runtime (regex, math, datetime, JSON, collections matching CPython's own algorithms), shown equal to the Python runtime by differential testing (tested, not proven) | Built — 49 programs, 2,478 requests compared (status, first hop, content type, full body, SQL statements and order, after_sql rows, DB digest), 0 mismatches; 385/391 shared semantics cases pass on Go strictly; known limitations (amd64 unverified, natives/`for_each`/tabular not yet on Go) in `docs/results/2026-09-26-bench-smoke.md`, "Go target — equivalence gate" |

## 3. Design principles (learned, not assumed)

- **Constrain for guarantees, expand for expressiveness — one construct at a time.** Every measured Lattice win came from a rule the language enforces; every expressiveness gap (missing count-distinct, `first`/`last` naming, per-column sort directions, keys with different names) caused failures until the construct was added. New constructs are added when evidence shows models need them, each with its guarantee and tests.
- **Familiar surface, strict core.** Python syntax and SQL/dataframe vocabulary for the model; a precise checked semantics underneath.
- **Diagnostics are part of the interface.** Every rejection says what is wrong and the exact fix; a model should fix any single mistake in one repair round.
- **One plan, many targets.** Correctness is established once on the plan; each code target is a generator proven equal to the reference semantics by differential tests.
- **The plan IR is the core, not Python.** Lattice source uses Python syntax only as its input format (parsed by Python's own parser, then checked). The checked program is lowered to a language-neutral plan; SQL, polars and generated FastAPI are targets of that plan.
- **Escape hatches are typed.** Logic Lattice cannot express lives in native functions whose signatures (inputs, outputs, errors, effects) are checked; only their bodies are trusted.

### 3.1 AST-first: what is valid in the idea

The model does not write raw ASTs or a binary encoding. Source text is already a lossless serialization of the AST, and tree formats cost roughly 10× more tokens and are out of distribution. What does hold:

- **A canonical AST as the source of truth.** Unison stores code as content-addressed ASTs. That gives rename-free refactors and no merge conflicts on formatting, which is useful for agents.
- **Structured edits.** Having the model emit tree operations ("replace node X") instead of text diffs is an open research area and could beat text patches for large refactors.
- **Checking on the AST.** Types, effects and policies are checked against the tree, and that's where the value is.
- **Compactness through abstraction, not encoding.** Lattice API programs were about 55% smaller than the equivalent FastAPI because `page()`, `owned()` and friends say more per token, not because of the file format.

## 4. Lattice as the interface for changing existing code

### 4.1 The workflow

```
  existing code ──lift──► Lattice (+ native islands) ──LLM edits──► checked Lattice ──emit──► code diff
        ▲                                                                                  │
        └──────────────────── differential tests prove behaviour preserved ◄────────────────┘
```

1. **Lift** the relevant part of the codebase into Lattice: logic that maps to Lattice constructs becomes Lattice; logic that does not becomes **native islands** (typed native functions wrapping the original code, unchanged).
2. **Verify the lift**: run the original and the lifted program on the same inputs/requests (the benchmark's differential machinery) — the lift is accepted only if behaviour is identical on generated and recorded cases.
3. **Edit**: the LLM changes the Lattice form. The checker enforces the guarantees on every edit (an edit that drops an ownership filter or adds an unbounded list is rejected before it reaches the codebase).
4. **Emit** code for the target and present it as a normal diff to the original files; non-lifted code and native islands are preserved byte-for-byte.

### 4.2 Is lifting achievable? — Yes, partially, with evidence-backed guarantees

- **Deterministic lifters** for recognizable, high-value patterns: SQL queries → Lattice read pipelines; FastAPI/Flask routes doing SQL CRUD → `@endpoint`s; pandas/polars method chains → tabular pipelines. These are AST pattern matchers (source AST → Lattice AST), the same machinery the checker already uses.
- **LLM-assisted lifting with verification** for everything else: the model translates code to Lattice; the translation is accepted only after differential testing against the original. The model is good at this translation, and the check makes it trustworthy.
- **Limits (honest):** exact equivalence of arbitrary programs is undecidable, so lifting guarantees are *tested equivalence*, not proofs; dynamic Python (reflection, monkey-patching, hidden side effects) stays in native islands; comments/formatting of lifted regions are regenerated, not preserved. Lifting coverage grows with the language (§5).

## 5. Self-improving language

### 5.1 Is it achievable? — Yes, as a governed learning loop; not as a language that rewrites its own rules unsupervised

What can learn, and how:

| What improves | Signal | Mechanism | Gate |
|---|---|---|---|
| **The models' fluency** | every verified (task, Lattice program) pair | retrieval of similar verified programs as few-shot examples; later, fine-tuning on the corpus | benchmark must not regress |
| **The step/module library** | recurring sub-programs in lifted and written code | mine repeated plan fragments → propose reusable typed `@step`s / modules | tests + human review |
| **The language itself** | checker rejections that models repeat, lifting failures (code that had to become a native island), repair loops that fail | cluster recurring failures → propose a construct (as we did by hand for `count_distinct`, `desc=[...]`, `on={...}`) with its guarantee, codegen and tests | human approval + benchmark gate + soundness review |
| **Diagnostics** | which hints lead to a successful repair | rank and rewrite hints by repair success rate | automatic, bounded to hint text |
| **Optimizer** | observed query counts/latency across programs | learn which plan rewrites pay off per target | differential equality + perf improvement |

What must *not* self-modify: the guarantees. A construct never enters the language without its checker rule, its semantics in every target, differential tests, and a soundness review — otherwise "learning" could quietly remove the reason Lattice exists.

### 5.2 The loop

```
observe (rejections, lift failures, repairs, native islands, perf)
   → propose (new construct / step / hint / rewrite, with spec + tests)
   → verify (checker soundness tests, differential tests on all targets, benchmark gate)
   → adopt (versioned: programs declare `# lattice: vN`; old versions keep working)
```

Versioning (`# lattice: v1` header) already exists, so the language can evolve without breaking existing programs.

## 6. Roadmap

### Done
Tabular + API profiles, checker, plan IR, polars/SQL runtimes, FastAPI code generation (differentially verified), fair benchmark with cost/size/latency metrics, implicit-requirements evidence.

### Import → Lattice → emit programme (in progress)

| # | Sub-project | Delivers |
|---|---|---|
| 1 | **Language extensions for real apps** (done) | `Uuid`/`DateTime`, page + total and app page shapes, declared response models, partial updates (`UNSET`), faithful 403 ownership (`owner_is`, policy warning), typed pure native functions, pluggable auth, backend capability model + dialect-neutral SQL layer; proven on the official FastAPI template's items/users/login routes |
| 1b | **Loop-shaped constructs** (done) | window functions (running totals, rank, lag/lead), bounded `iterate(step, until=…, max=N)`, `traverse(edges, start=…, max_depth=N)` (recursive CTE / `$graphLookup`), batched side-effect natives (`for_each`, run after commit); each compiled to SQL, polars and generated FastAPI with differential tests. Unbounded loops stay out of checked Lattice by design — they live in native functions. **Evidence:** a 12-item loop corpus of real-style Python loops (`tests/fixtures/loops/`): all 11 expressible items are equivalent to their originals on generated inputs (polars and SQL for tabular items, runtime vs the original FastAPI + SQLModel app for API items), and the 12th, a state machine, is a documented native island (`docs/results/2026-09-26-bench-smoke.md`, "Loop constructs (1b)") |
| 2 | **Importer** (in progress) | FastAPI + SQLModel → Lattice + native islands; LLM-assisted with differential equivalence verification, deterministic lifters for recurring idioms; loop patterns mapped to 1b constructs; per-repo coverage + policy-deviation report |
| 3 | **SQLModel emitter + edit loop** | idiomatic SQLModel/FastAPI in the repo's style; import → LLM edits Lattice → emit minimal diff; emitter proven equal to the runtime |
| 4 | **Backends** | Postgres + MySQL, then MongoDB (aggregation pipelines), then ClickHouse (reads first); capability model rejects programs a backend cannot run with full guarantees |
| 5a | **Go target — compiler and equivalence gate** (done) | the T2 (procedure) profile compiles to a standalone Go binary (`net/http`, pure-Go SQLite, no cgo), with its own runtime matching CPython's own algorithms (a full SRE regex port, CPython's dict/set layout) rather than approximating them; shown equal to the Python runtime by a differential gate (49 programs, 2,478 requests, 0 mismatches) and a shared semantics suite (385/391 cases on Go, strict) — tested, not proven. v1.1 language changes (64-bit `Int`, insertion-ordered sets, ASCII regex classes, the RE2 subset, explicit numeric widening, exact Float membership/lookup, the Float power domain) apply to both targets. Known limitations: amd64 unverified (no Rosetta), natives/`for_each`/`NativeToken`/tabular not yet on Go, last-ulp transcendental differences, the JSON depth limit, Unicode 15 vs 16 (`docs/results/2026-09-26-bench-smoke.md`, "Go target — equivalence gate") |
| 5b | **Go target — benchmark** (stage 1 done: STOP (P1 not confirmed, R = 0.69)) | ran the existing benchmark harness against the Go target (CLI transport, Go platform kit, 40 API tasks, 480 runs) to measure what 5a only proved equivalent: cost and correctness of Lattice→Go vs hand-written Go on claude-sonnet-5/claude-haiku-4-5. Pre-registered verdict: STOP — B is ~30% cheaper per correct task on Sonnet under this harness's warm-cache batching (~20% cold; R = 0.689 [0.591, 0.803]), short of the ≤0.6 threshold; repair rounds are ~46% of B's cost; correctness parity holds; every cell hit a 120/120 correctness ceiling (`docs/results/2026-09-26-bench-smoke.md`, "Go cost pilot (5b stage 1, pre-registered, 2026-09-29)") |

### Later
Learning loop (verified-program corpus, retrieval few-shots, failure clustering proposing constructs), optimizer pass, more targets (TypeScript), optional fine-tuned Lattice models.

## 7. Long-term direction: agents own the code, humans own the intent

**Humans specify and observe, agents change a checked IR, compilers produce whatever runs.**

In the long run the user talks only to an agent. Source code becomes a hidden intermediate, as assembly did. The agent changes the Lattice IR with structured edits, and deterministic compilers produce the code that runs.

```
User ──intent, policies, examples──► Agent
                                       │ structured edits
                                       ▼
                        Lattice IR (source of truth, checked:
                        types, effects, ownership, bounds)
                                       │ compilers (deterministic, differentially tested)
                ┌──────────────┬───────┴───────┬──────────────┐
             Python/FastAPI   SQL          Wasm/LLVM       (future)
                                       │
                        readable projection + explanations on demand
```

Principles:
- **The agent's job ends at the IR.** It never generates machine code. Compilers are deterministic, tested and optimized; an LLM writing machine code is none of these. Wasm and LLVM are future targets of the same plan IR, added as ordinary compiler work.
- **With no human reviewer, the checker is the reviewer.** Guarantees matter more, not less, when code is not read.
- **Correctness depends on intent, not on the code.** Every silent failure measured so far was an intent failure (null ordering, what "my items" means for an admin, the page cap). The interface that matters is how users state intent: specs, policies, examples and acceptance tests.
- **Integration is most of the work.** Programs still call databases, HTTP APIs, cloud SDKs and auth providers. Targets must link against existing ecosystems, which is why Python and SQL come first.
- **Hidden by default, never impossible to inspect.** Incidents, audits and regulated domains need a readable projection of the IR and explanations of its behaviour on demand.

**Prerequisite (not yet shown):** the checker must measurably reduce silent bugs in *unattended* changes. The thin slice so far shows equal silent failures in both arms (§2, `docs/results/2026-09-26-bench-smoke.md`). The deciding experiment compares Lattice with direct Python edits guarded by the same policies as a Python checker, on implicit-policy tickets in an untouched repo. New targets wait until this holds.
