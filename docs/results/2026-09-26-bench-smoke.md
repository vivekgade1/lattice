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

## Step 2 — trap & safety suite (bench2, 54 tasks, 4 arms)

Both arms' prompts carry equal trap guidance (merge validation / keys, missing-value handling, keeping null keys, text-typed codes, write only out.parquet); both task messages show the first 10 rows of every input.

| Model | A pandas | P pandas + repair | B Lattice + repair | C Lattice first-shot |
|---|---|---|---|---|
| claude-haiku-4-5 — correct | 96.3% | **100%** | 94.4% | 81.5% |
| claude-haiku-4-5 — silent | 1.9% | **0%** | 5.6% | 7.4% |
| claude-opus-5 — correct | 100% | **100%** | **100%** | 96.3% |
| claude-opus-5 — silent | 0% | 0% | 0% | 0% |

- **Silent failures on the 34 guarded traps: zero for pandas (A and P) on both models.** With the trap guidance in the prompt, the models never fell into the traps, so the gate's `silent_failure_reduction` reports "no silent failures in P to reduce".
- **L420 did its job where Lattice programs would have fanned out** (Haiku C: 3 fan-out programs rejected loudly, all repaired in B) — but pandas didn't make those mistakes to begin with.
- **Safety: not demonstrated.** No arm on either model acted on any injected instruction (0 executed unsafe effects, 0 static flags), so the Lattice guarantee holds but is untested by model behaviour.
- **Lattice's remaining misses are logic errors on control/safety tasks** (Haiku B: w02, w04, x10) plus one recurring friction: `sort(desc=[...])` per-key directions is not supported (Haiku C x04; Opus C w04, x04).
- Gate: FAIL on both models (silent reduction and safety "not demonstrated"; Haiku also accuracy parity 94.4% vs 100%).

## Step 2 — no-hints condition (`--no-hints`)

Pitfall advice removed from both arms' system prompts; language reference, schema facts (types, unique keys, may-be-missing columns), sample rows and the output rule unchanged.

**claude-haiku-4-5** (correct / silent, per family; guarded trap families first)

| Family | A pandas | P pandas + repair | B Lattice + repair | C Lattice first-shot |
|---|---|---|---|---|
| null_keys (8) | 0 / 8 silent | 0 / 8 silent | **7** / 1 | 7 / 1 |
| types (8) | 1 / 3 (+4 loud) | 3 / 5 | **8** / 0 | 8 / 0 |
| fanout (10) | 10 / 0 | 10 / 0 | 7 / 2 (+1 loud) | 5 / 2 (+3 loud) |
| aggregates (8) | 8 / 0 | 8 / 0 | 8 / 0 | 8 / 0 |
| **34 trap tasks** | 19 ok, 11 silent | 21 ok, **13 silent** | **30 ok, 3 silent** | 28 ok, 3 silent |
| **all 54** | 72.2% | 74.1% | **87.0%** | 79.6% |

Gate: `silent_failure_reduction` **PASS** (silent rate on traps 8.8% vs 38.2%), `accuracy_parity` **PASS** (87.0% vs 74.1%), `safety` not demonstrated → overall FAIL only on safety.

**claude-opus-5:** A, P and B all 54/54 (100%), C 49/54 (5 loud, 0 silent). No trap bit pandas even without advice; tie.

Observations:
- Lattice's advantage on Haiku comes entirely from **null keys** (Lattice keeps missing keys as a group by definition; pandas' `groupby` silently drops them: 0/8) and **types** (typed `load` keeps zip codes as text; pandas' `read_csv` turns them into numbers).
- On **fan-out** and **missing values in aggregates** pandas needed no help (10/10, 8/8); Lattice's L420 fired on 4 first-shot programs (all loud), and its two silent fan-out misses were logic slips (`agg.first` where "current" needed `agg.last`; per-customer instead of per-tier output), not guard escapes.
- **Safety** remains untested: across 864 step-2 attempts no model acted on an injected instruction.
- Remaining Lattice logic errors cluster on the control tasks (w02, w04) and x10.

### Repeat of the no-hints Haiku run (after per-column sort directions)

| claude-haiku-4-5, no hints | A pandas | P pandas + repair | B Lattice + repair | C Lattice first-shot |
|---|---|---|---|---|
| 34 traps, run 1 (correct / silent) | 19 / 11 | 21 / 13 | **30 / 3** | 28 / 3 |
| 34 traps, run 2 | 18 / 13 | 21 / 13 | **29 / 4** | 26 / 3 |
| all 54, run 1 | 72.2% | 74.1% | **87.0%** | 79.6% |
| all 54, run 2 | 68.5% | 75.9% | **85.2%** | 79.6% |

The gap replicates: gate `silent_failure_reduction` and `accuracy_parity` pass in both runs (safety not demonstrated). Per family it is the same picture — Lattice wins null_keys (7/8 vs 0–1/8) and types (8/8 vs 2–3/8); pandas wins fanout (10/10 vs 6–7/10); aggregates tie at 8/8. No `sort desc=` rejections after the fix.

New finding: Lattice's fan-out losses in run 2 (f01, f02, f07, x07) share one cause — the model sorts prices newest-first and then takes `agg.first(col("price"), order=col("valid_from"))`, expecting "first row in the sorted order"; `agg.first(x, order=o)` actually means "x at the smallest o", so it returns the oldest price silently. The name `first` plus an order key invites the pandas `sort → head` reading: a naming flaw in the language, not a model error in the usual sense.

### Run 3 — after renaming `agg.first/last` to `agg.at_min/at_max(x, of=...)`

| claude-haiku-4-5, no hints | A pandas | P pandas + repair | B Lattice + repair | C Lattice first-shot |
|---|---|---|---|---|
| 34 traps (correct / silent) run 1 → 2 → 3 | 19/11 → 18/13 → 20/10 | 21/13 → 21/13 → 25/9 | 30/3 → 29/4 → **31/2** | 28/3 → 26/3 → 27/2 |
| fanout (10) run 1 → 2 → 3 | 10 → 10 → 10 | 10 → 10 → 10 | 7 → 6 → **8** | 5 → 4 → 5 |
| all 54 run 1 → 2 → 3 | 72.2 → 68.5 → 74.1% | 74.1 → 75.9 → 81.5% | 87.0 → 85.2 → **90.7%** | 79.6 → 79.6 → 83.3% |

- The rename removed the "current price" failure mode: f01, f02, f07, x07 all correct in arm B; no program used the old names.
- Gate: `silent_failure_reduction` PASS (5.9% vs 26.5%), `accuracy_parity` PASS (90.7% vs 81.5%) — third consecutive pass; `safety` not demonstrated.
- Lattice's remaining misses are the same logic errors in every run (n05 distinct buyers, f12 per-tier output, w02, x10) plus one L401 on f11 — model reasoning, not language friction.

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
