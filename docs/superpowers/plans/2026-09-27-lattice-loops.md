# Lattice 1b — Loop-shaped constructs Implementation Plan

> **For agentic workers:** execute node by node. Each node is TDD: write a failing test for each rule or semantic, then implement, then run the full suite. Agents never run tree-modifying git commands; the orchestrator commits after each node passes its gate.

**Goal:** implement spec `docs/superpowers/specs/2026-09-27-lattice-loops-design.md` (Part A constructs + Part B real-app gaps).

**Architecture:** each construct touches the same layers in the same order:
1. catalog entry (`lattice/catalog.py`);
2. P1/P3 shape rules;
3. P4 typing + key/nullability (`lattice/check/p4_types.py`, `exprs.py`);
4. P5 effects / P6 API rules / P7 capabilities;
5. plan IR (`lattice/api/ir.py`, tabular `lattice/runtime/`);
6. targets: polars executor, SQL compiler (`lattice/api/sql.py` via the `Dialect`), runtime (`lattice/api/runtime.py`), codegen (`lattice/api/codegen.py`);
7. prompt guides (`lattice/llm/`).

**Tech stack:** Python 3.12, polars, sqlite3, FastAPI, pytest + hypothesis, ruff, `uv run`.

## Global constraints
- Every new diagnostic has a stable code (L430–L439, L874–L876, R610), a span and a fix hint. Every code has one positive and one negative test.
- The runtime and generated FastAPI must be identical: the codegen differential stays at 0 mismatches, with new cases for every API-visible feature.
- API reads remain one SQL statement.
- `uv run pytest -q` green and `uv run ruff check .` clean at every node gate.
- Don't `ruff format` files you did not create.

## Review focus
1. `window` with ties in `order`: `row_number`/`lag` must be rejected in the API profile (L431) or be deterministic in tests.
2. `traverse` on cyclic or diamond graphs: each node exactly once, at minimum depth, in both SQL and polars.
3. `for_each` when the endpoint fails after the outbox insert: nothing is dispatched.
4. `iterate` that oscillates between two states: R610 at the cap, never a silent answer.
5. B1 active check ordering: inactive + bad params → the configured inactive status, before 422.

## Nodes (sequential; each gated on the full suite)

| Node | Deliverable | Gate |
|---|---|---|
| N1 Part B | B1 `active_column`, B2 `error_body`, B3 `datetime_wire`, B4 `Email`/`pattern=`, B5 `unique_or(Conflict)`, B6 list constants; template profile uses them | suite green; template differential re-run: only the 5 policy deviations remain (or each remainder named with a reason); codegen differential 0 mismatches |
| N2 window + traverse | `window` + `win.*`, `traverse`; `Capabilities.recursive_queries`; L430–L434, L874, L875; SQL, polars, runtime, codegen | suite green; hypothesis SQL-vs-polars differential for both; codegen differential cases |
| N3 for_each + iterate | effect natives, outbox table + dispatcher + `lattice outbox retry` in both the runtime and the generated app; `iterate` in the tabular profile; L435–L439, L876, R610 | suite green; rollback / failure / retry tests; outbox contents equal between the runtime and codegen; iterate fixpoint and R610 tests |
| N4 corpus + prompts | about 12 real-style Python loops in `tests/fixtures/loops/` (original Python + Lattice + equivalence test on generated inputs); prompt guide sections; `for`/`while` rejection hints naming the constructs; results doc section | suite green; every corpus item equivalent |
| N5 review | independent review of 1b; fixes | no open critical or important findings |
