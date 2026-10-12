# Handoff — Lattice for UI: JavaScript/TypeScript, HTML and React

> For: a new Claude session picking up this work cold · Written 2026-09-28 · Owner: the user (vivekgade) · Status: **idea stage, not designed**. Start with brainstorming, not code.

## 1. The request

The user said, verbatim: *"similar to go, lattice should support Javascript html for ui development especially react."*

The Go target (sub-project 5, in progress) makes a checked Lattice program compile to a Go binary that behaves identically to the Python output. The user wants the same for the front end: Lattice programs becoming JavaScript/TypeScript, HTML and **React** code, with Lattice's guarantees.

"Support UI development" can mean three different things. Settle which one(s) with the user before designing anything (§5).

## 2. What Lattice is (read these first, in order)

1. `docs/archive/lattice-vision.md`: the idea, what's built, design principles, roadmap.
2. `docs/lattice-language-rules.md`: **binding**. One meaning for every target, gates instead of removals, a decision log, CI checks. Every UI decision must follow R1–R14.
3. `docs/archive/superpowers/specs/2026-09-28-lattice-go-target-design.md`: the Go target design. The UI target should reuse its architecture: the shared T2 IR, the semantics table, `p8_target` gates, and differential equivalence gates.
4. `docs/archive/superpowers/plans/2026-09-28-lattice-go-target-5a.md` and its ledger `.superpowers/sdd/2026-09-28-lattice-go-target-5a/progress.md` (git-ignored, in the go-target worktree): Go progress and every ruling made so far.
5. `docs/results/2026-09-26-bench-smoke.md`: the evidence so far. Read it honestly. Lattice's measured wins come from **guarantees enforced by the language** (policy bugs such as the page cap, and shorter programs, which make it cheaper per correct answer). Silent-bug reduction on *edits* is **not yet shown**.

Key code:
- `lattice/check/` (checker passes p1–p8)
- `lattice/api/ir.py` (`EndpointPlan`: params with constraints, auth, response schemas, errors)
- `lattice/t2ir/` (language-neutral IR for T2 procedures)
- `lattice/targets.py` (semantics v1.1 and target capabilities)
- `lattice/gogen/` (the Go emitter and runtime, as a pattern to copy)

## 3. Current state you inherit

- **Branch to build on: `go-target`** (worktree `.claude/worktrees/go-target`). It contains the targets module, the T2 IR, the language rules and the Go runtime.
  - Another session is actively executing plan 5a on that branch.
  - **Do not work in that worktree.** Create your own worktree and branch **from `go-target`** (e.g. `ui-target`), and rebase as 5a lands.
- **Plan 5a progress at handoff:** Tasks 0–4, 7 and 11 are done. Task 5 is running: the Python emitter from the IR, v1.1 runtime rules, and removal of the old instrumentation. Tasks 6, 8–10, 12–14 remain. A JS target should not start implementation until **Task 5** has landed, since the IR shape settles there, and ideally until **Task 13** (the Go gate) proves the multi-target approach.
- The user's other uncommitted work sits on branch `lattice-design` in the main checkout. Don't touch it.

## 4. Constraints you must respect

- **Language rules (binding).** Anything JS can't reproduce is either a per-target gate (L89x-style, with a decision-log row and an exit condition) or, if the targets would diverge, a documented language-wide rule. **Never cut the language down to fit JS** (R5).
- **Evidence first (vision §3, R8).** Every UI construct must be justified by a measurable win for LLM-written code, not by appeal.
- **Workflow.** The user uses the superpowers skills: brainstorming → spec in `docs/archive/superpowers/specs/` → the user's approval → plan in `docs/archive/superpowers/plans/` → subagent-driven execution with task reviews. Record decisions as rulings. The user expects honest, critical assessment, not cheerleading.
- **Benchmarks report more than accuracy:** tokens, cost per correct task, program size, rounds and timing (user memory `benchmark-metrics`).

## 5. The three readings of "UI support"

Put these to the user as options. My recommendation follows.

| # | What it is | Reuses | New guarantees it could offer | Risk |
|---|---|---|---|---|
| **A** | **Typed client + React hooks generated from the API profile.** Each `EndpointPlan` becomes a TypeScript client, TanStack Query hooks and form schemas. Parameter constraints (ge/le/min/max length/pattern/enum) become client-side validation **identical** to the server's. Error kinds become typed unions. | `EndpointPlan`, param specs, response schemas | No client/server validation drift. Every error case typed and forced to be handled. No hand-written fetch code. | Low. Overlaps with OpenAPI codegen (orval, openapi-typescript, tRPC, zod), so the distinctive part is a *verified* match with the server. |
| **B** | **A TypeScript target for T2 procedures** (like Go). The same `@procedure` runs in the browser: pricing previews, validation, date rules, bit-identical to the server. | T2 IR, semantics suite, gates (exactly the Go pattern) | The same answer client- and server-side, proven by differential tests. | Medium. JS semantics diverge a lot (see §6). |
| **C** | **A UI profile.** Components, state, events and data bindings written in Lattice, compiled to React (plus HTML/CSS). | Checker architecture; A for data; B for logic | Possible: no XSS (no raw HTML), accessibility rules (labels, alt text, focus), exhaustive loading/error/empty states for every data binding, ownership-scoped data only, typed props, no unbounded renders or effects. | High. It's a new profile. Models already write React fluently (distribution shift, per the feasibility doc), and the competition includes v0, Lovable, Bolt and Wasp. It is only worth it if C's guarantees reduce real UI bugs. |

**Recommendation to put to the user:** do **A first**. It is cheap, reuses what exists, and its guarantee (the client can't drift from the server) is concrete and testable. Then **B**, which follows the proven Go pattern once 5a's gate passes. Treat **C** as a separate evidence-gated bet, with a benchmark before much building: a model writes React directly vs writes Lattice UI, counting silent UI bugs (missing error or loading states, XSS sinks, a11y violations, stale data after mutations, unvalidated forms). Don't build C on enthusiasm alone; the earlier Lattice benchmarks show frontier models often avoid the obvious traps.

## 6. JS/TS semantic divergences to settle (they apply to B, and partly to A)

Each must become a rule under R3, a gate under R4, or runtime work, plus decision-log rows and semantics cases run on JS:

| Area | Lattice v1.1 | JavaScript | Likely handling |
|---|---|---|---|
| `Int` | signed 64-bit, overflow is R623 | `number` is a float64; exact only up to 2^53 | represent Int as `BigInt` in the JS runtime (slower but exact), or gate Int values beyond 2^53. JSON wire ints above 2^53 need care on the client (A too). |
| `Float` text | Python repr (`1.0`, `1e+16`) | `String(1.0) == "1"`, exponent form at 1e21 | a runtime `reprFloat` port (as in Go's `rt.ReprFloat`) |
| `Str` length and indexing | code points | UTF-16 code units (`"👋".length == 2`) | helpers over `Array.from(s)` / code points |
| Case mapping | full Unicode (`'ß'.upper() == 'SS'`) | `toUpperCase()` also gives `SS`, but locale and version differences exist | check against the semantics suite |
| Regex | RE2 subset; `\d \w \s \b` ASCII | JS supports more (lookbehind), so the RE2 subset is accepted, **but JS `\s` is Unicode** | translate `\s` to an explicit ASCII class; use the `u` flag carefully |
| `dict` / `set` order | insertion order | `Map`/`Set` are insertion-ordered ✅; plain objects reorder integer-like keys ❌ | always `Map`/`Set` |
| `//`, `%` | floor | `%` truncates | helpers, as in Go |
| DateTime | µs, UTC | `Date` is ms | a runtime DateTime type with µs (as in Go's `dates.go`) |
| Fuel | charged at IR fuel nodes | — | port `Fuel` exactly; the same R620/R621/R622 text |
| Errors | catchable `KeyError`/`IndexError`/…; R-codes are not catchable | exceptions | runtime error class plus a `Try` helper (as in Go's `try.go`) |
| JSON | Python `json.dumps` byte format | `JSON.stringify` differs (no spaces, no `ensure_ascii`) | a runtime `dumps` port (as in Go's `jsonv.go`, Task 10) |

The Go runtime (`lattice/gogen/rt/*.go` and its Python-generated testdata) is the template. Its testdata generators already produce Python-reference answers that a JS runtime can reuse.

## 7. Suggested first session plan

1. Read §2 in order, then skim `lattice/gogen/` and the Go spec.
2. Invoke **superpowers:brainstorming**. Classify the work as architectural. Ask the user **one question at a time**, starting with which of A/B/C (or which order) they want and why: what UI they want to build with Lattice and what success looks like.
3. For the chosen scope, propose 2–3 approaches with a recommendation. For A, for example:
   - generate code directly from `EndpointPlan`;
   - or emit OpenAPI plus zod and reuse existing tooling;
   - or run the JS T2 runtime for validation.
4. Present the design in sections. Write the spec (`docs/archive/superpowers/specs/<date>-lattice-ui-<scope>-design.md`) with: a decision-log section for every JS rule or gate; an equivalence gate (for A: every request the server accepts, the client accepts, and vice versa, on the generated suites; for B: Python ≡ Go ≡ JS on the semantics suite and T2 programs); and benchmark criteria fixed before any live run.
5. Only after the user approves the spec: a plan, then execution.

## 8. Open questions for the user

1. Which of A, B and C, and in what order?
2. Who writes the UI code: the LLM through Lattice (as in the vision), or humans consuming generated clients?
3. Is React the only target, or should the design leave room for others (Vue, Svelte, React Native)?
4. TypeScript output, or plain JavaScript? (Recommend TypeScript: types are part of the guarantee.)
5. Styling: out of scope, Tailwind, or plain CSS modules? (Recommend out of scope for A and B.)
6. Is there a real app to prove it against? The Go work used the official FastAPI template. The full-stack FastAPI template has a React front end, which is a natural proof target for A and C.

## 9. Don'ts

- Don't start coding before a spec is approved.
- Don't work in the `go-target` worktree, and don't modify `lattice/gogen/` or the 5a plan.
- Don't relax any v1.1 rule to fit JS; gate it instead (R4–R5).
- Don't claim a UI win without a benchmark that has fair arms and silent-bug classification (see the bench harness in `bench/`).
