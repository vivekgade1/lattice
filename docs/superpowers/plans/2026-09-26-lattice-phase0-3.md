# Lattice Phase 0–3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a benchmark harness, a statically checked Python-AST profile for tabular data pipelines (with `@step` functions and a `when`/`fn.*` expression library), a polars executor, and a Claude-driven generate→check→repair loop — then measure whether constrained generation beats plain pandas generation.

**Architecture:** The LLM writes ordinary Python text. We parse it with the stdlib `ast`, run five checker passes (P1 node whitelist → P2 names → P3 canonical form → P4 types/schemas → P5 effects) that collect every diagnostic with a `line:col` span and stable code, lower the checked AST into a step DAG (`Plan`), and execute that DAG with polars through an exhaustive dispatcher — never `eval`/`exec`. The benchmark runs three arms (A: plain pandas, B: Lattice + repair, C: Lattice first-shot) over the same tasks and computes the go/no-go gate.

**Tech Stack:** Python ≥3.12, uv, polars, pyarrow, pandas (baseline arm only), anthropic SDK, pytest, hypothesis, ruff.

**Spec:** `docs/superpowers/specs/2026-09-26-lattice-design.md`

## Global Constraints

- Python `>=3.12`; package manager `uv`; tests `uv run pytest`; lint `uv run ruff check .`.
- Program header: first line exactly `# lattice: v1` (code `L001`).
- Every diagnostic: `Diagnostic(code, severity, span, message, hint)`; rendered `line:col: severity CODE: message` (+ ` (hint: …)`). Lines and columns are **1-based**.
- The checker **collects all** diagnostics; it never raises on user errors. Internal invariants raise `TypeError`/`LatticeInternalError`.
- No `eval`, `exec`, `compile` of model text anywhere in `lattice/`. The only place model-written code runs is the baseline arm's subprocess (`bench/sandbox.py`).
- Types: `Int, Float, Str, Bool, Date`; no implicit coercion except `Int`→`Float` widening where stated.
- Runtime semantics (spec §5.7): `/0` and `%0` → null + warning `R301`; math-domain errors → null + `R302`; failed casts → null + `R303`; NaN → null at `load`; null propagates in arithmetic; `where` drops null predicates; `sum`/`count` over empty → 0, `mean/min/max` → null.
- Default model: `claude-opus-5` (configurable via `--model`). Every live model run costs money — **ask the user before running `bench` against the live API**.
- Never name the source textbook or its author in code, comments, identifiers, or logs.
- Commit after every task with a conventional-commit message ending in the two attribution lines:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01DkfBU2ur5VhLPgqfejszsZ`

### Spec clarifications decided by this plan (Task 9 writes them into the spec)

1. **Imports:** two allowed modules — `from lattice.ops import …` (ops, `col`, `when`, `coalesce`, `is_null`, `agg`, `fn`, `step`, `Table`, scalar type names) and `from lattice.schemas import …` (input schema names). Imports must exactly match use.
2. **Canonical call shapes (refines L303):** each op has one fixed signature saying which parameters are positional and which are keyword. `where(t, pred)` takes its predicate positionally; everything else that is not a table is keyword.
3. **Step return annotation** may be bare `Table` (schema inferred from the body); parameters must be concrete (`Table[Orders]`) or scalar (`Int`, …). A concrete return type is checked exactly (L605).
4. **Nullability:** every column is nullable in v1; the `T?` distinction is deferred.
5. **Output files:** `write` accepts `.csv` or `.parquet`; the benchmark always uses `out.parquet`.

## Review Focus

1. **Empty input tables** (0 rows after `where`) flowing into `group`/`join`/`derive` — expect an empty result with the statically predicted schema; `group(t, by=[], n=agg.count())` → one row with `n = 0`. Test in Task 19.
2. **Column names that aren't identifiers** (`"unit price"`) — `col("unit price")` must work end-to-end; only *new* column names (derive/group keywords) must be identifiers. Test in Task 19.
3. **Messy model output** — prose around code, several code blocks, no code block, a non-`python` fence. First ```` ```python ```` block wins; none → diagnostic `L000` fed to repair. Tests in Task 5 and Task 21.
4. **Invalid data in input files** (bad date string in a `Date` column) — expect runtime error `R101` pointing at the `load` line, not a polars traceback. Test in Task 19.
5. **Source text with CRLF line endings or a UTF-8 BOM** (common from some clients) — header detection and spans must still work. Test in Task 8.

---

## File Structure

```
pyproject.toml                  project metadata, deps, pytest/ruff config
.gitignore                      results/, .venv, caches
lattice/__init__.py
lattice/typesys.py              Scalar, Schema, checker types (TableT, ColT, LitT, …), unify()
lattice/diagnostics.py          Span, Severity, Diagnostic, span_of(), render helpers
lattice/catalog.py              op shapes, importable names, fn/agg signatures, aliases (pure data)
lattice/profile.py              Profile (schemas + version)
lattice/parse.py                header check + ast.parse → ParseResult
lattice/check/__init__.py       check_source() orchestrator → CheckResult / CheckedProgram
lattice/check/context.py        CheckContext (diagnostics list + type side table)
lattice/check/astutil.py        shared AST helpers (annotation shapes, step param kinds)
lattice/check/p1_nodes.py       node whitelist + step signature shape
lattice/check/p2_names.py       scopes, single assignment, use-before-def, purity of steps
lattice/check/p3_canon.py       one-op-per-statement, no nesting, call shapes, no-op steps
lattice/check/exprs.py          column-expression typing (col/when/coalesce/fn.*/operators)
lattice/check/p4_types.py       op schema transfer functions, step signatures
lattice/check/p5_effects.py     load/write placement and paths
lattice/canon.py                canonical pretty-printer
lattice/ir.py                   Plan/Node + lowering with step inlining
lattice/runtime/__init__.py
lattice/runtime/exprs.py        AST expression → polars Expr (+ null guards)
lattice/runtime/ops.py          op implementations
lattice/runtime/exec.py         execute(plan) with schema assertions and limits
lattice/llm/__init__.py
lattice/llm/client.py           Generator protocol, ClaudeGenerator, ScriptedGenerator, extract_code
lattice/llm/prompt.py           system prompts (Lattice + baseline) and task messages
lattice/llm/repair.py           synthesize(): generate → check → repair loop
bench/__init__.py
bench/datasets.py               deterministic fixtures + dataset registry
bench/tasks.py                  Task model + registry of suites
bench/seed_tasks.py             30 seed tasks with polars reference solutions
bench/more_tasks.py             ≥70 additional tasks (Task 23)
bench/compare.py                type-tolerant table comparison
bench/sandbox.py                subprocess runner for baseline code
bench/runners.py                run_baseline / run_lattice → TaskOutcome
bench/report.py                 summary, markdown report, gate evaluation
bench/cli.py                    `python -m bench.cli run …`
tests/…                         mirrors the modules above
```

---
## Task Index (full step-by-step bodies still to be written — see status note)

**Phase 0 — Benchmark baseline**
1. Scaffold (`pyproject.toml`, `.gitignore`, package skeleton) + `lattice/typesys.py` `Scalar`, `Schema`, `unify()`.
2. `bench/datasets.py`: deterministic `customers.csv` / `orders.csv` / `products.csv` (seeded, with nulls and `qty == 0` rows); schemas `Customers`, `Orders`, `Products`.
3. `bench/compare.py`: `frames_match(actual, expected, *, ordered, rtol=1e-6) -> tuple[bool, str]`: normalises numeric columns to Float64 and dates/other columns to strings, compares nulls as equal, and ignores row order unless the task says order matters.
4. `bench/tasks.py` + `bench/seed_tasks.py`: `Task(id, tier, prompt, inputs, reference, ordered)`; 30 tasks (10 single, 12 multi, 8 join) with polars references.
5. `lattice/llm/client.py`: `Generator` protocol, `ClaudeGenerator` (`claude-opus-5`, cached system block, refusal fallback beta, reads token usage), `ScriptedGenerator`, `extract_code()`.
6. `bench/sandbox.py` + baseline prompt + `run_baseline()` (isolated subprocess with a timeout, output to `out.parquet`).
7. `bench/report.py` + `bench/cli.py` for arm A; record baseline numbers (live run only with user approval).

**Phase 1 — Parse and structural checks**
8. `lattice/diagnostics.py` + `lattice/parse.py` (header `L001`, syntax `L002`, CRLF/BOM tolerant).
9. `lattice/catalog.py`, `lattice/profile.py`, `lattice/check/context.py`, `check_source()` skeleton; write the spec clarifications into the spec.
10. P1 node whitelist (`L101–L131`, `L601`, `L602`) + `check/astutil.py`.
11. P2 names (`L201–L205`, `L306`, `L603`, `L604`).
12. P3 canonical form (`L301–L304`, `L701`).

**Phase 2 — Types, IR, execution**
13. Checker types + `check/exprs.py` (`col`, `when`, `coalesce`, `is_null`, `fn.*`, operators; `L401–L413`, `L702`).
14. P4 op schema transfer functions + step signatures (`L405`, `L414–L419`, `L605`).
15. P5 effects (`L501–L505`) + multi-error test (5 errors → 5 diagnostics) + a 300-step program checks in under 1 s.
16. `lattice/canon.py`: round trip is a fixpoint.
17. `lattice/ir.py`: lowering, with `@step` calls inlined as prefixed sub-graphs and scalar parameters substituted.
18. `runtime/exprs.py`: polars compilation, with guards that produce `R301`–`R303`.
19. `runtime/ops.py` + `runtime/exec.py` (runtime schema assertion `E901`, `R101`, row/time limits) + Review Focus tests 1, 2, 4.
20. Differential tests (Lattice solutions vs. polars references) + one test per spec §5.7 row.

**Phase 3 — LLM loop and gate**
21. Lattice system prompt (rules, catalog, examples) + `synthesize()` repair loop (`L000` when there is no code block).
22. `run_lattice()`; arms B and C in the CLI; failure taxonomy (L201 → shadowing, L202/L604 → sequencing, L301/L302 → chaining, L4xx → schema).
23. Grow the suite to ≥100 tasks (`bench/more_tasks.py`), keeping the tier ratios.
24. Gate evaluation (+15 points on multi-step, no regression on single-step, ≥99% pass within 2 repairs); live run with approval; write the results doc.
