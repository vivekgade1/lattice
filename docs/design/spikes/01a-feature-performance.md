# Spike 1a: performance improvement of a feature, steered by the spec

Status: research and plan. No code.

## Question

Lattice holds the business spec and builds the app from it. Can the spec be used to improve the
performance of one feature (find the bottleneck, change the code, prove it is faster and still correct)?

## What exists (evidence)

| Piece | Where | Relevance |
|---|---|---|
| Correctness oracle | layers A (static), B (endpoint programs), C (invariants after every request), D (scenarios), X (races): `lattice/spec/check.py`, `invariants.py`, `race.py` | A perf change is only acceptable if behavior is unchanged. This is the hard half of perf work and it is already solved. |
| Safe change path | rebuild loop: worktree branch `rebuild/<id>`, agent rounds, Studio-run gates, never merges (`lattice/studio/rebuild.py`) | Where an optimizing agent would run. |
| Process runtime | `lattice/spec/process.py`: starts the app on a free port with a throwaway DB, waits for `/health` | Gives a real running target to measure. |
| Clone timing | `lattice/spec/dbserver.py` (`timings`, `timing_note`) | Only database clone time is recorded. |
| Gate profile | `docs/results/2026-10-11-gate-profile.md`: full gate 412 s, layer D 290 s (70%), X 83 s (20%) | Shows the *gate* is slow, mostly per-scenario app start, not the app's request latency. |
| Planned perf phase | `docs/design/lattice-build-performance-phase.md` (planned, not started) | Is about *build* speed, not *app* speed. Different problem, shared plumbing (timings, scoped gates). |

## What is missing

1. **No performance clauses in the spec.** There is no way to say "list check-ins p95 < 150 ms" or "at most
   3 queries per request". Without a target, an optimizer has nothing to be judged against.
2. **No per-request timing in the check report** (G1 in the planned phase adds it for scenarios).
3. **No load model.** Scenarios are functional, one request at a time (layer X is the only concurrent
   layer and tests correctness, not throughput).
4. **No perf gate** of the form "faster than baseline and all behavior gates green".

## Findings

- The spec is an excellent *guard*, not yet a *target*. The honest claim is: Lattice can make a perf change
  **safe**; it cannot yet say what "better" means.
- Query count is the cheapest, most deterministic perf signal for a DB-backed API (N+1, missing index,
  unbounded read). It does not depend on machine noise. Latency percentiles need repeated runs and a quiet
  machine. Recommend query count and rows touched as the first budget, latency second.
- The app is built in Go + GORM (Streak) or Python; the profiler differs per target, so measurement should
  be **black-box** (HTTP timings + DB statement log), which is language-agnostic and fits the
  multi-target rule (no drift).
- Perf work belongs **outside the build gates** by the same reasoning as the security module: a flaky
  latency threshold must never block a correct build.

## Design (recommended)

Two additions, both optional, neither a build gate:

1. **Budget metadata in the spec** (documented, linted, not enforced by A to X): per endpoint or per
   scenario `max_queries`, `p95_ms`, `max_rows_scanned`. Changes to it go through `change_propose`.
2. **A `perf` run** (CLI + Studio action, separate from `check`): for a chosen scope (feature or system),
   start the app on a throwaway DB, replay the scope's scenarios (and optionally a scaled-up seed) with
   timing and a statement log, and report each endpoint vs its budget and vs the last baseline.

Improvement loop (reuses what exists): perf run finds an endpoint over budget -> a change set "make X meet
its budget" -> rebuild loop -> full gates green **and** perf run better -> owner merges.

## Plan

| Phase | Work | Exit (objective) |
|---|---|---|
| P0 | Hand-run: pick one real list endpoint (Streak check-ins or progress), measure queries and latency per request with a statement log, no Lattice change | A written baseline with variance across 10 runs; shows whether noise allows latency budgets |
| P1 | Seeded-regression probe: remove an index / introduce N+1 by hand in a throwaway branch | The measurement from P0 detects it every time, no false alarm on the clean tip |
| P2 | Optimizer probe: one agent in a rebuild worktree told only the budget and the failing numbers | All behavior gates pass, budget met, in <= 3 rounds, report cost and time |
| P3 | Decide whether budgets become spec syntax (language rule: all targets or gated, see `docs/lattice-language-rules.md`) | Owner decision; if yes, a separate design doc |

Pre-register P1 and P2 (hypothesis, number of runs, pass criteria) before running them.

## Risks

- Latency noise on a laptop gives false verdicts; mitigate with query counts first and repeated runs.
- An optimizer can "pass" by caching or by weakening behavior that the spec does not state. The spec only
  protects what it says; unstated behavior can regress. Mitigate: the diff is shown to the owner and the
  change never merges itself.
- Test seeds are tiny; real perf problems appear at scale. A scaled seed option is needed from P1.

## Open decisions for the owner

1. Budgets in the spec (new language construct) or kept in a side file read only by the perf run?
2. First budget metric: query count (recommended) or latency?
3. Target feature for P0.
