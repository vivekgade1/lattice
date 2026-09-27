# Lattice — Vision

> Status: living document · 2026-09-27 · Companion to the feasibility report (`docs/LLM-Native Coding Language Feasibility v1.md`), the language spec (`docs/superpowers/specs/2026-09-26-lattice-design.md`) and the API spec (`docs/superpowers/specs/2026-09-27-lattice-api-design.md`). Evidence: `docs/results/2026-09-26-bench-smoke.md`.

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
| Lifting existing code into Lattice | **Not built** (§4) |
| Self-improvement loop | **Partly manual** (§5): every language fix so far came from benchmark failures, done by hand |

## 3. Design principles (learned, not assumed)

- **Constrain for guarantees, expand for expressiveness — one construct at a time.** Every measured Lattice win came from a rule the language enforces; every expressiveness gap (missing count-distinct, `first`/`last` naming, per-column sort directions, keys with different names) caused failures until the construct was added. New constructs are added when evidence shows models need them, each with its guarantee and tests.
- **Familiar surface, strict core.** Python syntax and SQL/dataframe vocabulary for the model; a precise checked semantics underneath.
- **Diagnostics are part of the interface.** Every rejection says what is wrong and the exact fix; a model should fix any single mistake in one repair round.
- **One plan, many targets.** Correctness is established once on the plan; each code target is a generator proven equal to the reference semantics by differential tests.
- **Escape hatches are typed.** Logic Lattice cannot express lives in native functions whose signatures (inputs, outputs, errors, effects) are checked; only their bodies are trusted.

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

| Phase | Deliverable | Proves |
|---|---|---|
| Now | API benchmark live runs (Haiku/Sonnet/Opus, hints/no-hints) | guarantees reduce silent API bugs at acceptable cost |
| 1 | **Typed native functions** (escape hatch) | "any complex logic" without losing guarantees around it |
| 2 | **Lifters**: SQL → Lattice reads; FastAPI+SQL CRUD → endpoints; pandas chains → pipelines; LLM-assisted lift + differential verification | existing code can enter Lattice with tested equivalence |
| 3 | **Edit-and-emit workflow**: LLM edits lifted Lattice, emit a minimal diff to the original repo | Lattice as the interface for changing existing code |
| 4 | **Learning loop v1**: verified-program corpus + retrieval few-shots; failure clustering dashboard proposing constructs | fluency and language improve with use |
| 5 | Bounded iteration & window functions; optimizer pass; more targets (SQL for tabular, TypeScript/Go for APIs) | expressiveness and optimality grow under guarantees |
| 6 | Fine-tuned Lattice models from the corpus (optional) | shorter prompts, lower cost, higher first-shot accuracy |
