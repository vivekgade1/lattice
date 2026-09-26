# Lattice Phase 0–3 — verification results (2026-09-26)

Branch `lattice-design`. Default model `claude-opus-5` (no server-side fallback — it silently rerouted Lattice-arm calls to claude-opus-4-8).

## What was verified

| Check | Result |
|---|---|
| Unit + integration suite (`uv run pytest -q`) | 649 passed, 1 deselected (live) |
| Lint (`uv run ruff check .`) | clean |
| Offline end-to-end (`tests/test_e2e_offline.py`): CLI → scripted model → check/repair → execute → compare → report, all 30 seed tasks, arms B and C | 30/30 correct in both arms; B repairs a shadowing error in round 2 on every task; C = 1 round |
| Offline baseline arm A (3 tasks, hand-written pandas through the sandbox) | 3/3 correct |
| Checker soundness fuzzing (review probes, ~4,900 random accepted programs at HEAD~1 plus 1,225 more at HEAD) | every accepted program executed: 0 internal errors, 0 schema-assertion failures, 0 leaked exceptions |
| Hand-written Lattice solutions vs polars references | all 30 seed tasks + 9 of the harder added tasks match |
| Independent review (2 rounds) | round 1: 1 blocker + 5 majors, all fixed; round 2: 1 new major (engine panic from out-of-range dates), fixed and re-verified via the reviewer's reproduction |

## Full benchmark (130 tasks: 33 single, 40 multi, 27 join, 30 deep with 5–8 dependent steps)

Before the run: Lattice-prompt refusals fixed (8/40 → 0/80 by rewording the intro; 0 refused attempts across all 1,040 full-run calls), deep tier added, `fn.*` semantics documented (a missing argument-order note for `fn.date_diff` had caused every Lattice wrong result in a first Opus run).

### claude-opus-5

| Arm | single | multi | join | deep | all |
|---|---|---|---|---|---|
| A — pandas | 100% | 100% | 96.3% | 100% | **99.2%** |
| B — Lattice + repair | 100% | 100% | 100% | 100% | **100%** |
| C — Lattice first-shot | 93.9% | 92.5% | 100% | 83.3% | **92.3%** |

Gate: FAIL only on `multi_step_improvement` (+0.0 pts; pandas is already at 100% on multi+deep). All other checks pass: check-pass rate 100%, ≤ 2 repairs, same model. Every arm-C miss was a static error the repair loop fixed; 7 of 10 were `L306` (unused import).

### claude-haiku-4-5

| Arm | single | multi | join | deep | all |
|---|---|---|---|---|---|
| A — pandas | 100% | 100% | 96.3% | 100% | **99.2%** |
| B — Lattice + repair | 97.0% | 95.0% | 85.2% | 63.3% | **86.2%** |
| C — Lattice first-shot | 75.8% | 57.5% | 48.1% | 6.7% | **48.5%** |

Gate: FAIL (multi+deep −18.6 pts; deep −36.7 pts). Most first-shot static failures are bookkeeping rules rather than the branching-factor rules the design targets: `L203` unknown/unimported name (18 tasks), `L405` derive re-using an existing column name (15), `L306` unused import (11); the targeted rules (`L201` shadowing, `L301/L302` chaining) account for only ~8.

### claude-haiku-4-5 after step 1 (ambient names + derive overwrite; j08 wording fixed)

| Arm | single | multi | join | deep | all |
|---|---|---|---|---|---|
| A — pandas | 100% | 100% | 100% | 100% | **100%** |
| B — Lattice + repair | 97.0% (was 97.0) | 100% (95.0) | 96.3% (85.2) | 83.3% (63.3) | **94.6%** (86.2) |
| C — Lattice first-shot | 93.9% (75.8) | 95.0% (57.5) | 92.6% (48.1) | 73.3% (6.7) | **89.2%** (48.5) |

First-shot pass 50.8% → 92.3%; mean rounds for B 1.62 → 1.13; B output tokens 564 → 251 (now close to pandas' 227). Gate still FAIL (multi+deep −7.1 pts vs pandas' 100%). Remaining first-shot static failures: `agg.count(x)` with an argument (3 tasks), `join(on=[])` attempted as a cross join for share-of-total (3), named scalar constants like `cutoff = fn.to_date(...)` (2), misc (3); plus 4–5 wrong results.

### claude-haiku-4-5 after step 1b (agg.count(x), single-row cross join, named constants; all arms re-run because the pandas prompt gained matching guidance)

| Arm | single | multi | join | deep | all |
|---|---|---|---|---|---|
| A — pandas | 100% | 100% | 100% | 93.3% | **98.5%** |
| B — Lattice + repair | 100% | 97.5% | 92.6% | 90.0% | **95.4%** |
| C — Lattice first-shot | 97.0% | 95.0% | 92.6% | 76.7% | **90.8%** |

Checker pass rate: B 100%, first-shot 94.6%; B mean rounds 1.06; B output tokens 231 vs pandas 226; 0 refusals. Gate: FAIL only on `multi_step_improvement` (−2.9 pts on multi+deep; deep −3.3 = one task). Every remaining B miss is a logic error that passed the checker (6 tasks; d05 was also missed by pandas), not language friction. Differences of 1–4 tasks are within run-to-run noise at this sample size.

### Conclusion

- The thesis ("a constrained language beats a flexible one on multi-step tasks") is **not supported** on this benchmark. Pandas is at 99.2% for both models, including the 5–8-step deep tier, so there is no multi-step failure mode left for Lattice to fix; the report's 60%-on-multi-step Python baseline does not reproduce with current models.
- For a strong model, Lattice + repair reaches parity (100%) at ~1.5× output tokens.
- For a smaller model, the cost of an unfamiliar language dominates (the report's own distribution-shift argument): 48.5% first-shot, 86.2% with repair, vs 99.2% for pandas.
- The checker + repair loop is the component that works: +8 pts (Opus) and +38 pts (Haiku) over first-shot Lattice.
- If Lattice continues, the data points at removing bookkeeping friction first (ambient imports would eliminate L203/L306/L112 outright; allowing derive to overwrite a column would remove L405), then re-measuring on a model/task mix where pandas actually fails.

Raw results: `results/final-opus5/`, `results/full-haiku45/` (gitignored).

## Live smoke run (claude-opus-5, 12 tasks × 3 arms)

Tasks: s01,s05,s09,s20,m02,m03,m07,m13,m25,j02,j05,j12. All requests served by `claude-opus-5` (no fallback).

| Arm | single (4) | multi (5) | join (3) | all (12) |
|---|---|---|---|---|
| A — pandas | 100% | 100% | 100% | 100% |
| B — Lattice + repair | 75% | 80% | 100% | 83% |
| C — Lattice first-shot | 100% | 80% | 100% | 92% |

- All B misses (s01, m07) were **refusals, category `cyber`** — a false positive on the Lattice prompt. Excluding refusals B was 10/10, every program passing the checker on round 1.
- The one C miss (m07) was a real model error (used `fn.*` without importing `fn`, L203) that arm B's repair loop is designed to fix.
- Gate: FAIL (−20 pts multi-step). Not a meaningful gate result: n=12, and arm A is at 100%, so there is no headroom for a +15 improvement (ceiling effect).

### What this implies before the full 100-task gate run

1. **Refusals**: intermittent `cyber` refusals on Lattice-arm prompts (2 of 24 Lattice calls here; 1 of 3 earlier; 0 of 10 in a direct probe). Rewording the Lattice system prompt and measuring refusal rate is required first, or the gate measures the classifier rather than the language.
2. **Ceiling effect**: Opus 5 solves these pandas tasks perfectly. To test the thesis, the suite needs harder multi-step tasks (5–8 dependent steps) and/or a smaller model (e.g. `--model claude-haiku-4-5`), where the report's multi-step failure modes actually occur.

## Commands

- Live smoke test: `uv run pytest -m live` (key read from `.env`)
- Smoke benchmark: `uv run python -m bench.cli run --arms A,B,C --suite all --tasks s01,s05,s09,s20,m02,m03,m07,m13,m25,j02,j05,j12 --out results/smoke`
- Phase-3 gate (100 tasks × 3 arms ≈ 300+ calls): `uv run python -m bench.cli run --arms A,B,C --suite all --out results/gate`

The gate checks: multi-step accuracy B − A ≥ +15 points; single-step no regression (2-point tolerance); arm B check-pass rate ≥ 99% (harness errors and refusals count as failures); arm B ≤ 2 repairs; all arms served by the same model.

## Known open minors (accepted for now)

- Unordered float comparison sorts by 6-decimal-rounded keys; values straddling a rounding boundary can misorder (rare false negative).
- Int64 arithmetic overflow wraps silently (spec doesn't define it yet).
- `L003` (nesting too deep) reports at 1:1 instead of the offending line.
- Arm A output with string-typed dates where a Date is expected now scores as a mismatch (arm A is told to use `parse_dates`).
- Sandbox (macOS `sandbox-exec`) blocks network and writes outside the workdir but allows reads; on Linux run the harness in a container with `--network=none`.
- System prompt (~1.8k tokens) is below Opus's minimum cacheable prefix, so prompt caching likely won't engage.
