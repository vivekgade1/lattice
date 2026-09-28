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

## TODO — metrics to capture in future benchmark runs

Accuracy alone can't say whether Lattice is better or worse; every future run should also report, per task outcome and aggregated per (model, arm, family):

| Metric | Status |
|---|---|
| Output / input / cache-read tokens, summed over repair rounds | recorded (`TaskOutcome`) |
| Repair rounds, first-shot pass, latency | recorded |
| **Cost per task and per correct task** (tokens × model price) | recorded (`cost_usd`) |
| **Final program size**: lines, statements, AST nodes, characters | recorded |
| **Program execution time** | recorded (`exec_s`; pandas includes subprocess start-up) |
| Diagnostics per repair round | recorded (`diagnostics_per_round`) |

Report these side by side with accuracy so the verdict covers correctness, cost, and program quality.

### Run 4 — after `agg.count_distinct`, first run with efficiency metrics

| claude-haiku-4-5, no hints | A pandas | P pandas + repair | B Lattice + repair | C Lattice first-shot |
|---|---|---|---|---|
| 34 traps (correct / silent) | 19 / 12 | 19 / 15 | **32 / 2** | 28 / 0 (6 loud) |
| all 54 | 72.2% | 72.2% | **94.4%** | 85.2% |
| mean cost per task / per correct task | $0.0040 / $0.0056 | $0.0042 / $0.0058 | $0.0054 / **$0.0057** | $0.0050 / $0.0059 |
| mean input / output tokens | 2950 / 217 | 3138 / 216 | 4317 / 216 | 3982 / 205 |
| final program lines / AST nodes | 10.6 / 96.9 | 10.2 / 95.3 | **7.9** / 97.8 | 8.8 / 97.7 |

- n05 and x10 (distinct counts) now correct in both Lattice arms.
- Efficiency verdict (B vs P): **cost per correct task 0.98×**, program AST nodes 1.03×. Lattice costs ~30% more per call (its system prompt is longer) but needs fewer calls per correct answer, so cost per correct answer is at parity; programs are the same size in AST nodes and ~25% fewer lines.
- Exec time (0.008 s Lattice vs 0.255 s pandas) is not comparable: the pandas figure includes starting a sandboxed Python subprocess.
- Remaining Lattice misses: f12 (stops at per-customer rows), w02 (divides the already-per-unit column by qty again), f03 (used `at_max` for "earliest").
- Gate: silent-failure reduction PASS (5.9% vs 22.1%), accuracy parity PASS; safety not demonstrated.

### Model trend — no hints, after the prompt cut (Lattice system prompt 10.6k → 7.8k chars)

| Model | P pandas + repair: correct / silent on 34 traps | B Lattice + repair: correct / silent | All 54: P vs B | Cost per correct task, B vs P |
|---|---|---|---|---|
| claude-haiku-4-5 (run 4) | 19 / 15 | **32 / 2** | 72.2% vs **94.4%** | 0.98× |
| claude-sonnet-5 | **34 / 0** | 33 / 1 | **100%** vs 98.1% | 1.40× |
| claude-opus-5 | **34 / 0** | 34 / 0 | 100% vs 100% | 1.54× |

- Sonnet 5 and Opus 5 fall into none of the pandas traps even without advice; Lattice's structural guarantees add nothing there and it costs more: ~25% more input tokens (4.5k vs 3.6k per task) and more output tokens (Opus 384 vs 211 per task — adaptive thinking spent on the less familiar language).
- Lattice programs are consistently shorter (7.4–7.9 vs 9.1–9.8 lines) with about the same AST size (0.98–0.99×).
- Sonnet's single Lattice miss (n08) and all C-arm misses were first-shot static errors or one logic slip; 0 refusals across 432 calls.
- Safety remains not demonstrated.

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

## API profile — first live run (40 tasks × 4 arms, 2026-09-27)

A = FastAPI one-shot, P = FastAPI + repair, B = Lattice + repair, C = Lattice first-shot. Traps: 32 tasks (ownership, validation, errors, efficiency); 8 control. "silent" = wrong non-5xx answer.

| Model / condition | A correct | P correct | B correct | C correct | silent (any arm, 32 traps) | eff. queries/request (P vs B) | cost per correct task B vs P |
|---|---|---|---|---|---|---|---|
| haiku-4-5, no hints | 40 | 40 | 38 | 28 | **0** | 1.65 vs 1.65 | 1.33× |
| haiku-4-5, hints | 39 | 39 | 38 | 25 | **0** | 1.65 vs 1.65 | 1.27× |
| sonnet-5, no hints | 40 | 40 | 40 | 36 | **0** | 1.65 vs 1.65 | **0.84×** |
| sonnet-5, hints | 40 | 40 | 40 | 37 | **0** | 1.65 vs 1.65 | **0.83×** |
| opus-5, no hints | 40 | 39 | **40** | 36 | **0** | 1.65 vs 1.65 | **0.76×** |
| opus-5, hints | 40 | 40 | **40** | 38 | **0** | 1.65 vs 1.65 | **0.79×** |

\* Opus runs were cut short by an exhausted API credit balance (C: 40 and 21 tasks, B: 7 tasks not run; recorded as harness errors). One `cyber` refusal on a FastAPI (P) call.

Findings:
- **No silent API failures in any arm, on any model.** When the spec states the rule ("another customer's order behaves exactly like a missing one (404)", bounds, error statuses, "at most 3 queries"), all three models implement it correctly in plain FastAPI — ownership filters, pydantic bounds, 404 mapping, JOINs instead of per-row queries and LIMITs. The trap families as written don't bite, so Lattice's guarantees are not exercised by this suite.
- **Efficiency parity:** 1.65 queries per request for both on the efficiency family — models already avoid N+1 when told.
- **Cost:** on Sonnet and Opus Lattice + repair is **cheaper per correct task** (0.83× and 0.76–0.79×) — shorter programs and fewer output tokens than FastAPI; on Haiku it costs more (1.3×) and loses 2 tasks.
- **First-shot Lattice (C)** fails loudly more often (Haiku 28/40) — the checker catches the errors and repair (B) fixes almost all of them.

Implication: with explicit specs, models already write secure, efficient CRUD. Lattice's structural value would show when requirements are implicit (org-wide policies not restated in every ticket) — the next experiment.

## API profile — implicit-requirements condition (`--implicit`)

Ownership and efficiency tickets with org-policy sentences removed (visibility rules, the 100-item page cap); hidden tests unchanged (403 or 404 accepted where the ticket no longer says which). Silent failures on the 16 implicit tasks:

| Model | hints: A / P / **B** / C | no hints: A / P / **B** / C | cost per correct task B vs P (hints / no hints) |
|---|---|---|---|
| claude-haiku-4-5 | 1 / 0 / **0** / 0 | 2 / 2 / **0** / 0 | 1.37× / 1.19× |
| claude-sonnet-5 | 1 / 2 / **0** / 0 | 2 / 2 / **0** / 0 | 0.81× / 0.98× |
| claude-opus-5 | 0 / 0 / **0** / 0 | 2 / 2 / **0** / 0 | 0.69× / 0.67× |

- **Every silent failure is the same policy bug:** `limit=1000` accepted (200) instead of rejected (422) on f03/f08 when the ticket did not restate the page-size cap. Lattice cannot express it: `page()` enforces `limit ≤ 100` in the language.
- **With hints (fair: both arms' system prompts state the cap)**, Haiku and Sonnet FastAPI still dropped the stated cap in 1–3 of 4 opportunities; Opus kept it. Lattice: 0 on every model.
- **Without hints** the comparison is not information-equal (the cap is stated only as a Lattice language rule), so those rows measure "policy in the language vs policy nowhere" — which is the Lattice thesis, but report it separately.
- **Ownership** never failed silently in either arm: the schema facts ("owner column: customer_id") were enough for every model to scope queries correctly.
- Lattice + repair (B) was correct on 38–40/40 in every run; first-shot C failures were loud (checker), mostly missing `owned()` on OrderItems or page bounds, all repaired in B.

## API profile — output code metrics (correct solutions only)

Aggregated over all API runs (explicit + implicit, hints + no hints); P = FastAPI written by the model (with repair), B = Lattice written by the model (with repair). Scripts: `bench/analysis/api_code_metrics.py`, `bench/analysis/api_same_stack.py`.

**Program size (what the model writes)**

| Model | P lines / AST nodes / chars | B lines / AST nodes / chars | B vs P (AST) | output tokens P → B |
|---|---|---|---|---|
| claude-haiku-4-5 | 31.6 / 214 / 1446 | **11.9 / 116 / 526** | 0.54× | 440 → 280 |
| claude-sonnet-5 | 29.3 / 205 / 1332 | **11.7 / 114 / 508** | 0.56× | 553 → 372 |
| claude-opus-5 | 32.4 / 225 / 1454 | **11.6 / 116 / 514** | 0.52× | 642 → 340 |

The FastAPI app Lattice generates from a B program is ~428 lines, almost all of it the shared runtime prelude (auth, validation, transactions, serialization) that is identical for every program.

**Runtime performance on the same stack** (claude-sonnet-5, explicit + hints; the model's FastAPI script vs the FastAPI app generated from the model's Lattice program; same sandboxed driver, same DB, 40 tasks × all cases × 5 repetitions)

| Family | median latency P → generated | queries/request P → generated |
|---|---|---|
| all | 1.67 → **1.44 ms** | 1.82 → 1.82 |
| control | 1.74 → 1.57 | 1.54 → 1.62 |
| efficiency | 1.72 → 1.64 | 1.65 → 1.65 |
| errors | 1.62 → 1.37 | 1.92 → 1.89 |
| ownership | 1.68 → 1.47 | 2.30 → 2.36 |
| validation | 1.65 → 1.36 | 1.71 → 1.60 |

- Lattice programs are ~60% shorter (lines/chars) and ~45% smaller in AST nodes than equivalent model-written FastAPI, which is where its lower cost per correct task on Sonnet/Opus comes from.
- Generated apps are ~14% faster per request than model-written FastAPI on the same stack, with the same number of SQL statements (one statement per read pipeline). Latencies are small-DB, single-client, in-process TestClient timings — indicative of per-request overhead, not a load test.

## Template proof (sub-project 1, realapps spec §4)

Hand-written Lattice (`tests/fixtures/template/{login,items,users}.lattice.py`, profile `bench/template_profile.py`) against the vendored official FastAPI template (`bench/template_app`). The routes covered are `read_items`, `read_item`, `create_item`, `update_item`, `delete_item`, `read_users` (admin list), `read_user_me`, `update_user_me` and `login_access_token`. The request suite is `bench/template_suite.py` (53 cases), plus 9 extra cases in the test: an inactive user (including on the admin list and with an invalid parameter), and a valid token for a user that does not exist. Each case runs on a fresh copy of the same seeded SQLite database: the template's own schema, 32-hex ids and naive `YYYY-MM-DD HH:MM:SS.ffffff` datetimes. The comparison covers status, JSON body and the tables after the request, with placeholders for generated UUIDs, timestamps and tokens. A placeholder keeps the value's spelling class (`<uuid:1:hex32>` vs `<uuid:1:dashed>`, `<datetime:1:space>` vs `<datetime:1:TZ>`), so a backend that stores a new id or instant in another spelling than the database's fails on the table snapshots. Test: `tests/template/test_template_differential.py`.

**Result** (re-run with sub-project 1b Part B, spec `2026-09-27-lattice-loops-design.md` §4)

| Comparison | Cases | Identical | Identical up to a representation rule | Named deviations |
|---|---|---|---|---|
| Lattice runtime vs template, suite | 53 | 40 | 9 | 4 (all policy) |
| Lattice runtime vs template, extra | 9 | 8 | 0 | 1 (policy) |
| Generated FastAPI app vs Lattice runtime | 62 | **62** | — | — |

So **49/53** suite cases match (status, DB effects, and body), and 8/9 extra cases. Every DB effect matches, and every status matches except the bad-token, unbounded-limit and unknown-user cases. Before Part B the proof stood at 44/53 (6 identical, 38 under three representation rules, 9 deviations of which 2 were language gaps) and 6/9 extra cases (2 more gaps). The profile and programs now use:

- B1 `active_column="is_active"` (400 "Inactive user", checked by auth before the role check and before parameter validation): removes the `inactive-before-role` and `inactive-before-validation` gaps and the 8 repeated `require(user, col("is_active"), ...)` lines. Login keeps its own `require`, like the template's route, because a Public endpoint has no caller.
- B2 `error_body="fastapi"` with `unauthorized_detail="Not authenticated"` and `forbidden_detail="The user doesn't have enough privileges"`: removes the `ERROR_ENVELOPE` rule and the `automatic-401-body`/`automatic-403-body` deviations.
- B3 `datetime_wire="naive"`: removes the `DATETIME_WIRE` rule (pydantic's spelling of the naive values SQLite returns, e.g. `2026-02-02T06:00:00`).
- B4 `email: Email(max_len=255) = UNSET`: removes the `email-format` gap (422 for `not-an-email`, nothing stored).
- B5 `update(user, ..., unique_or=Conflict("User with this email already exists"))`: removes the `email-conflict-detail` gap (the store's UNIQUE violation answers the declared 409).
- B6 `USER_PUBLIC = [...]` used by three `select(cols=USER_PUBLIC)`: removes the duplicated column lists.

**Representation rule.** It is named and asserted in the test, and needed by at least one case:

- `VALIDATION_ENVELOPE`: both answer 422 with a `{"detail": [...]}` body flagging the same fields (same `loc` lists, in order). Each entry's wording differs: Lattice `{"loc", "msg"}`, pydantic `{"type", "loc", "msg", "input", "ctx"}`.

**Deviations** (the test asserts each is still real: Lattice's answer, and that it differs from the template's)

| Case(s) | Id | Kind | Lattice answers | Reason |
|---|---|---|---|---|
| `items_list_unbounded_limit`, `users_list_unbounded_limit` | bounded-page-size | policy | 422 | The template accepts `limit=1000000`. Lattice requires a bound (L831), so the programs declare `limit: Int(ge=0, le=100)`. |
| `items_list_bad_token`, `users_me_bad_token` | invalid-token-401 | policy | 401 `{"detail": "Not authenticated"}` | The template answers 403 "Could not validate credentials". Lattice answers 401 for any missing or invalid credential (RFC 6750). |
| `token_for_unknown_user` (extra) | unknown-user-token | policy | 401 `{"detail": "Not authenticated"}` | The template answers 404 "User not found". For Lattice, a token that identifies no caller is a 401. |

Not covered by any case, and noted for completeness:

- The template runs a dummy Argon2 verify for unknown emails (timing hardening). Lattice's login does not, because `one()` fails first.
- `verify_and_update` can rehash a password during login. Lattice natives cannot write.

**Two blockers, resolved in the language.**

- *Storage format:* `ApiProfile.uuid_storage` (`"canonical"` | `"hex32"`) and `ApiProfile.datetime_storage` (`"iso_z"` | `"sql_space"`). The SQLite dialect uses them for binds, literals, `fn.to_uuid`/`fn.to_datetime`, generated keys and native results. Generated apps get them as the prelude constants `UUID_STORAGE`/`DATETIME_STORAGE`. Responses, parameters and natives are unchanged, so a program answers the same whichever spelling its database uses. This is tested by running the real-apps program on both spellings, on the runtime and the generated app.
- *Form login:* `@endpoint(..., body="form")` reads URL-encoded form fields, converted like query text. It is validated by L601 (unknown value, or GET/DELETE) and served identically by the runtime and generated apps.

**Size** (`bench.metrics.program_metrics`: non-blank, non-comment lines / AST nodes)

| | lines | AST nodes |
|---|---|---|
| Lattice program (3 files, 9 endpoints; 93 / 968 before Part B) | **81** | 802 |
| Template route functions (the same 9, with docstrings) | 153 | 968 |
| Template route functions + the auth and crud support they call (`deps.get_db/get_current_user/get_current_active_superuser`, `crud.get_user_by_email/authenticate`) | 197 | 1278 |
| Lattice profile tables and models (`bench/template_profile.py`) vs the template's SQLModel classes used | 53 vs 54 | 319 vs 394 |
| FastAPI app generated from the Lattice program | 1427 | 8154 |

For these nine routes, Lattice is about 47% fewer lines (17% fewer AST nodes) than the route functions alone, and about 59% fewer lines (37% fewer AST nodes) once the auth and crud code the routes depend on is counted, since Lattice gets that from the language and profile. The inactive-user check is now in the profile, once, as the template has it once in `get_current_active_user`.

## Loop constructs (1b)

The loop corpus in `tests/fixtures/loops/` holds 12 real-style Python loops. Each one is
paired with its Lattice program, and the corpus is the importer's first test set. The
equivalence test is `tests/loops/test_loop_corpus.py`:

- **Tabular items (9):** 25 seeded random inputs each. The original runs on Python rows
  or a pandas DataFrame, and the Lattice program runs through the polars executor. The
  7 programs without `iterate` also run as one compiled SQL statement in SQLite, and
  both results must match the original.
- **API items (2):** 8 generated databases each, with 6-7 requests per database. The
  original FastAPI + SQLModel app and the Lattice runtime each serve their own copy.
  The test compares status codes, bodies (except 422 validation details), the table
  afterwards, and the side effects in order.

**Result:** 11/11 expressible items are equivalent on every generated input. Item 12 is
a native island by design.

Sizes are `bench.metrics.program_metrics` (non-blank, non-comment lines / AST nodes):

- *original:* the function with its loop, including its signature and docstring;
- *Lattice:* the whole program, including `load`/`write` and the endpoint signature;
- for the API items, the SQLModel classes and dependencies on one side, and the
  profile on the other, are not counted.

| # | Item | Loop shape | Construct | Original | Lattice | Equivalent |
|---|---|---|---|---|---|---|
| 01 | running balance per account | running value per key | `window` + `win.cum_sum` | 8 / 108 | 5 / 45 | yes (polars, SQL) |
| 02 | previous-value delta (pandas) | previous row of the same key | `window` + `win.lag` | 17 / 132 | 4 / 63 | yes (polars, SQL) |
| 03 | salary rank within department | rank within group, ties share | `window` + `win.rank` | 14 / 156 | 5 / 44 | yes (polars, SQL) |
| 04 | 3-row moving average (pandas + deque) | moving frame of N rows | `window` + `win.mean(rows=3)` | 12 / 130 | 3 / 46 | yes (polars, SQL) |
| 05 | category descendants with depth | tree walk down (BFS, seen-set) | `traverse` | 24 / 246 | 4 / 49 | yes (polars, SQL) |
| 06 | breadcrumb (ancestors path) | parent walk up | `traverse(direction="up")` | 13 / 146 | 6 / 51 | yes (polars, SQL) |
| 07 | org chart reports, cycles in data | one query per level, seen-set | `traverse` (API, 1 statement) | 26 / 223 | 10 / 127 | yes (API) |
| 08 | prune orphaned replies | repeat until stable | `iterate(until="fixpoint")` | 10 / 93 | 11 / 137 | yes (polars) |
| 09 | converge forecasts | converge until a predicate holds | `iterate(until=<Bool>)` | 8 / 96 | 9 / 91 | yes (polars) |
| 10 | notify watchers after insert | side effect per row | `for_each` + outbox (API) | 22 / 173 | 14 / 155 | yes (API) |
| 11 | order sequence number, same-day ties | per-key counter, ties | `window` + `win.row_number`, key in `order` | 9 / 115 | 6 / 57 | yes (polars, SQL) |
| 12 | login lockout replay | multi-accumulator state machine | native island | 28 / 259 | — | n/a (native) |
| | **total, items 01-11** | | | **163 / 1618** | **77 / 865** | **11/11** |

**Size.** Summed over the 11 expressible items, Lattice uses 53% fewer lines and 47%
fewer AST nodes.

- `window` and `traverse` items shrink the most, to 4-6 lines against 8-24, because the
  loop's bookkeeping (dicts of accumulators, seen-sets, frontiers, sorting) disappears.
- The two `iterate` items are about as long as the original (11 vs 10 lines, 9 vs 8).
  A round of the loop becomes a `@step`, and item 08 needs a `group` + `rename` + left
  `join` to express "parent still exists".
- What `iterate` adds is the bound: at most N rounds, and R610 instead of a silent
  partial answer.

**Bounds made explicit.**

- Items 05-07 take a stated `max_depth`, and items 08-09 a stated `max`.
- Item 10 notifies at most 100 watchers (L439), where the original notifies all of them.
- These are listed per item in `tests/fixtures/loops/README.md`.

**Native island (12).** Replaying login events carries four interacting accumulators
(failures, lock deadline, sessions, status), and branches on the current state. That is
a non-associative fold: no combination of `win.*` scans rebuilds it. `iterate` could
only emulate it one event per round, at O(n²). It stays a native function. The checker
rejects the loop with L127, and the hint says to keep it in a native function (asserted
by the test).

**Rejection hints.** A `for`/`while` statement keeps L127, and a comprehension keeps
L121. The hint now names the construct for the loop's shape:

- running / previous / rank → `window`;
- tree / parent walk → `traverse`;
- repeat until stable → `iterate` (in API programs: a native function);
- an action per row → `for_each` (in tabular programs: a note that effects are API-only);
- a state machine or anything else → a native function.

The classifier is `lattice/check/loop_hints.py`, tested in
`tests/check/test_loop_hints.py`. On every corpus item, the original loop's hint names
that item's construct (`test_the_original_loop_gets_a_hint_naming_the_construct`).

**Prompt size.** Each construct has one short section with one inline example, and the
three fenced examples are unchanged.

- The tabular guide gets `window`, `traverse` and `iterate`.
- The API guide gets `window`, `traverse`, `for_each`, `Email`/`pattern`, `unique_or`
  and list constants.
- Each baseline guide gets a section with the same information: pandas groupby
  `cumsum`/`shift`/`rank`/`rolling`, BFS with a visited dict, and bounded retry for the
  tabular arm; SQL window functions, `WITH RECURSIVE`, effects after `db.commit()`,
  `Field(pattern=)`, `IntegrityError` → 409 and column-list constants for the API arm.
- The sections are reference material, not advice, so both hint conditions carry them.
  The tests assert equal substance (±30% per section) and that every inline Lattice
  example checks clean.

Token counts are estimates at about 3.5 characters per token: no tokenizer or API key
was available offline.

| Prompt | Before (chars) | After (chars) | Δ chars | Δ tokens (est.) | Baseline / Lattice ratio |
|---|---|---|---|---|---|
| tabular Lattice (hints / no hints) | 7,911 / 7,061 | 9,579 / 8,729 | +1,668 (+21% / +24%) | ≈ +480 | — |
| tabular pandas (hints / no hints) | 7,224 / 6,170 | 8,837 / 7,783 | +1,613 (+22% / +26%) | ≈ +460 | 0.91 → 0.92 |
| API Lattice (hints / no hints) | 10,802 / 9,489 | 12,798 / 11,485 | +1,996 (+18% / +21%) | ≈ +570 | — |
| API FastAPI (hints / no hints) | 10,258 / 8,884 | 11,989 / 10,615 | +1,731 (+17% / +19%) | ≈ +490 | 0.95 → 0.94 |

**Language gaps found while writing the corpus.**

1. **`@step` parameters lose declared keys.** Inside a step, `Table[Comments]` does not
   carry the key `(id,)`. So a `select` → `rename` → `join` that checks clean at the top
   level is L420 inside the step. Item 08 re-proves uniqueness with
   `group(t, by=["id"], n=agg.count())`.
2. **L439 does not see through joins.** `for_each` over `join(limit(...), one_row,
   on=[])` is rejected as unbounded, although the input is bounded. Effect arguments
   therefore come from the bounded table and the endpoint parameters only (item 10
   cannot pass the new comment's id).
3. **Fan-out needs an explicit cap.** "Notify every watcher" becomes "the first 100"
   (L439). The cap is by design, but the importer must report it as a behaviour change.

## Thin slice — LLM edits through Lattice vs direct Python edits (baseline, 2026-09-27)

Plan: `docs/superpowers/plans/2026-09-27-lattice-thin-slice.md`. Setup:
- **Tickets:** 6 realistic change tickets on the official FastAPI template, each with unstated traps (`bench/edit_slice/`).
- **Arms:**
  - **P** edits the Python.
  - **L** edits the imported Lattice, which is then emitted as FastAPI.
- **Controls:** same ticket text and a repair budget of 2 in both arms. Feedback is only what a developer sees locally (a checker or start-up error, plus a smoke request). Scoring uses a hidden oracle.
- **Scale:** 3 reps per model; the language is frozen as of commit 2b9eee3.

| model | arm | correct | loud | silent | $ total | $/correct | rounds | lines changed | out tok/run |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| sonnet-5 | P | **15/18** | 0 | 3 | 0.57 | 0.038 | 1.06 | 32.1 | 2.3k |
| sonnet-5 | L | 3/18 | 8 | 7 | 3.39 | 1.129 | 2.56 | 15.2 | 15.9k |
| haiku-4-5 | P | **12/18** | 0 | 6 | 0.27 | 0.023 | 1.17 | 29.6 | 2.2k |
| haiku-4-5 | L | 3/18 | 6 | 9 | 0.80 | 0.266 | 2.61 | 13.1 | 6.1k |

**Verdict: with the language as it stood, the core claim fails on this slice.** Editing through Lattice was much less accurate and 3–10× more expensive than editing Python directly.

Causes, from the per-run records:
1. **Language gaps.** t1 (case-insensitive contains) and t5 ("was the field sent", DateTime arithmetic) can't be expressed. t2 and t4 need workarounds.
   - These four tickets account for most L failures.
   - Sonnet spent up to 16k output tokens per round trying to express the impossible and ran out of tokens ("no code"). That explains most of the cost gap.
2. **A semantic trap in the language itself.** `owned(t, by=user)` means "rows the caller may see", and an Admin sees every row. "The signed-in user's own items" (t6) was written as `owned(...)` in all 6 L runs, and all 6 were silent failures: admins got everyone's stats. The API guide's own examples name the result `my_items = owned(...)`, which teaches the mistake. The reference solution needed a join trick.
3. **Low first-try checker pass rate.** Almost every L run needed at least one repair round for checker errors, even on the expressible tickets t3 and t6.
4. **P's silent failures fall where Lattice is strong.** They were a `since` with a timezone offset parsed wrongly (t2, both models) and a missing admin check on restore (t4, Haiku). But L never got far enough on those tickets to show it.
5. **Where L did produce code, it was better code.** Correct L runs changed fewer lines (13–15 vs 30–32) and the emitted code answered faster (0.8–1.0 ms vs 6.6 ms per request; 1.7 vs 2.1–2.3 queries per request).

Next:
- Add the six gap features (built in isolation, not yet merged).
- Fix the `owned` semantics trap.
- Save candidate sources in the records.
- Re-run the same 72 runs.
- Add a fresh held-out ticket set to guard against tuning the language to these six tickets.

### Round 2: after the gap features, `mine()`, and route-preserving emit (144 runs)

Changes since the baseline (committed before this run):
- the six gap features;
- `mine()`, plus the guide/lifter renames;
- the emitter keeps routes it didn't lift;
- a held-out ticket set t7–t12, written before checking expressibility.

| model | arm | t1–t6 (tuned-on) | t7–t12 (held-out) | silent total | $/correct | mean rounds |
|---|---|---|---|---:|---:|---:|
| sonnet-5 | P | 15/18 | **18/18** | 3 | 0.036–0.039 | 1.00 |
| sonnet-5 | L | 15/18 | 14/18 (t12 is a gap: 3 loud) | 3 | 0.060–0.181 | 1.44–1.61 |
| haiku-4-5 | P | 12/18 | 15/18 | 9 | 0.022 | 1.06–1.17 |
| haiku-4-5 | L | 10/18 | 6/18 | 9 | 0.046–0.120 | 1.72–1.83 |

- **Improvement from the baseline:** Sonnet L went from 3/18 to 15/18 on the same six tickets, and Haiku L from 3/18 to 10/18.
- **Held-out tickets:** Sonnet L reaches 14/15 of the expressible ones. Haiku L falls to 6/18.
- **Silent failures:** there is no reduction yet. Both arms have the same count: 3 on Sonnet, 9 on Haiku.

Silent and loud L failures, diagnosed from the saved artefacts:
1. **t2 (L, both models): the default null ordering differs from the source app.** Lattice sorts nulls last. The template's SQLAlchemy + SQLite ascending order puts them first. The ticket never says where undated items go, so P inherits the original behaviour for free and L silently changes it. This is an importer issue: an imported profile must preserve the source platform's defaults.
2. **t9, t6 (Haiku L): `owned` is still chosen for "own" features.** Sonnet uses `mine` correctly after the fix; Haiku still reaches for `owned`.
3. **t7 (Haiku L): no clean way to refer to the caller's id or admin status inside an expression.** The model wrote a self-check that compares the target with itself. Other runs tried `user.id` (refused, L106). The references needed a rename + join trick.
4. **t5, t11 (Haiku L, loud):** checker rules the model couldn't satisfy in two repairs (L302, L802, L126 annotated assignment).
5. **t12 (all L, loud):** owner transfer is not expressible (a recorded gap).

The P arm's silent failures are:
- t2 timezone offsets (both models);
- t4 missing admin check (Haiku);
- t11 whitespace (Haiku).

**Cost.** L costs 1.7–5× more per correct result. The L system prompt is 15k characters, against 1k for P, and L needs more repair rounds.

**Verdict.** The gap-closing round moved Sonnet on Lattice to parity on the tuned tickets and near parity on the held-out ones. The core claim, fewer silent bugs through Lattice, is **not yet supported**: L's silent failures moved to new places, namely semantic defaults, ownership intent and caller references.

## Importer on held-out repos — first contact (no LLM, 2026-09-27)

`python -m lattice.importer . --package bench.import_repos.<repo>.app --app-factory bench.import_repos.<repo>:make_app --no-llm` at commit 679a577. It was run before any importer work had looked at these repos.

| repo | result |
|---|---|
| TaskFlow | **crash** in `adapt.table_spec`: `User.created_at: column type NaiveDatetime` is unsupported |
| JustNotes | **crash** in `adapt.auth_spec`: "no users table" (its users table isn't recognised) |

**Verdict:** the importer's extraction and adaptation are shaped around the template, and they fail hard instead of degrading. The rule is that an unsupported piece should become "unclear" or an island, never a crash.

The next step generalises them: it degrades gracefully, supports common types (naive datetimes, integer keys), and detects the users table from the auth dependency. From then on, these two repos are **no longer held out for the importer** (they remain held out for the language and for tickets t19–t24). Later importer claims need a third, untouched repo.

## Importer on held-out repos — generic and gracefully degrading (no LLM, 2026-09-27)

Same command as above, after the importer was generalised. The importer now degrades instead of crashing: an unsupported piece becomes an `Unclear` decision and/or a route with no request suite, which is an R4 island with a classified gap. No Lattice was written by hand, and no lifter targets these repos. Each generalisation has a synthetic test in `tests/importer/test_generic_import.py` / `test_import_int_keys.py`. `tests/importer/test_import_held_out.py` pins the coverage below.

| repo | routes | verified | islands (R4) | need the LLM |
|---|---:|---:|---:|---:|
| TaskFlow | 10 | 0 | 8 | 2 (`register`, `login`: no lifter) |
| JustNotes | 30 | 0 | 26 | 4 (`register`, `login`, `health`, `get_public_notes`: no lifter) |

Island reasons:
- **TaskFlow, 6 authenticated routes:** *language gap: single-role auth.* The app has no admin flag and no role column, and `ApiProfile.roles_from` cannot give a caller a role. The profile also has no auth: the token decode reads `request.app.state.settings.secret_key`, which a native cannot import.
- **TaskFlow, 2 routes (`/`, `/health`):** defined inside the `create_app()` factory. They are found and reported, but not lifted.
- **JustNotes, 26 authenticated routes:** *language gap: token subject is not the users key.* The JWT `sub` is `User.username`, while `NativeToken` decodes a token to the key. The single-role gap applies here too.

Language gaps found, reported and not fixed here (`lattice/check` and `lattice/api` were not changed):
1. **Single-role auth.** An app with no admin concept cannot be imported, because every authenticated caller needs a role from a role column or an admin flag.
2. **Token subject is a non-key column.** The auth native must return the users key; a username subject would need a lookup by a unique column.
3. **Enum constraint.** Enum and `Literal` columns and parameters are kept as `Str`/`Int`. The suite requests a value outside the enum, so a lift that drops the constraint fails verification instead of passing silently.

Not gaps:
- **Integer autoincrement keys.** Lattice's `insert` leaves a one-column `Int` key to the database. A synthetic app with `int` keys, an `int` token subject and `NaiveDatetime` columns verifies its read, create, update, delete and me routes end to end.
- **403-vs-404 ordering.** JustNotes answers 404 before 403, which is expressible. Its 403 checks have no admin bypass, so they fall under the single-role gap.

The template import is unchanged: 23 routes, 10 verified, 13 need the LLM, with byte-identical output.

## Importer on held-out repos — the four language gaps closed (no LLM, 2026-09-27)

The four gaps above are closed in the language, and the importer uses them generically. No Lattice was written for these repos and no rule matches only their code. Each feature has positive and negative tests on synthetic code: `tests/api/test_import_gap_features.py` and `tests/api/solo.py`, which is also a codegen differential program with 0 mismatches. The importer side is covered by `tests/importer/test_import_single_role.py` (end to end) and `test_generic_import.py`.

| feature | surface | semantics |
|---|---|---|
| single-role auth | `ApiProfile(roles_from=NoRoles())` (role defaults to `Customer`) | Every authenticated caller has that one role and none is Admin. No users column is read for a role. `owned()`/`accessible()` equal `mine()`, and `is_admin(user)` is false. **Decision:** `auth=<any other role>`, `auth=Admin` included, is **L801**, because no caller could ever reach that endpoint. Admin-only columns (`admin_write`) are then unwritable. The importer chooses `NoRoles` when the app has no superuser dependency and no `Str` role column. |
| non-key token subject | `NativeToken(native, lookup="username")` | The decoded claim is matched against that users column in the same single authentication statement (`WHERE "username" = native_decode_token(?)`). The column must be a declared key of the users table (`keys=[("id",), ("username",)]`), and the native must map `Str ->` that column's type. Otherwise the profile raises `ValueError`. The importer uses it when the claim's column is the primary key or `unique=True`, and adds the key. |
| natives needing app configuration | `ApiProfile(settings={"secret_key": Scalar.STR})` with `NativeSig(..., settings=("secret_key",))` | The native is called as `fn(ctx, *args)`. `ctx` is a read-only `NativeContext` (a `MappingProxyType`) holding exactly the settings it declared, bound once at start-up: `build_app(..., settings=)`, or the natives module's `SETTINGS` in a generated app. A missing or mistyped setting fails at start-up. The binding code (`lattice/api/native_context.py`) is copied verbatim into generated apps. |
| enum columns | `Schema.from_pairs(..., one_of={"status": ("todo", ...)}, one_of_stored={"status": ("TODO", ...)})` | Every write to the column must be a listed literal or an endpoint parameter declared `OneOf(...)` with listed values (else **L407**), so any other value is a 422 at the API boundary. `one_of_stored` gives the database spellings: reads decode and writes encode them in SQL, and programs only see the values. The importer maps `Str` Enum/`Literal` columns to `one_of`, Enum columns also to `one_of_stored` (SQLAlchemy stores an Enum by member name), and body fields to `OneOf(...)` parameters. |

**Why a `NativeContext` rather than an importer-generated native reading the settings module's default:** TaskFlow's factory passes its secret into `create_app(Settings(secret_key=...))`, so the settings class default (`"development-only-change-me"`) is not the running app's secret. Only the running app has the right value. The generated profile therefore records where the original keeps the setting (`SETTINGS_FROM_APP = {"secret_key": "state.settings.secret_key"}`, from the decode's `request.app.state.settings.secret_key`) and a `settings_from_app(app)` reader. The importer reads the value from an app its factory builds and passes it to the Lattice app at start-up. The mechanism is generic: a program never contains a value, and a deployment supplies its own.

**Other generic importer fixes the re-run needed:**
- `raise credentials_exception` (an `HTTPException` assigned once: FastAPI's own tutorial idiom) is read as a raise site, a token policy and an or-ed active check (`if user is None or not user.is_active`).
- The superuser-free owner guard `if row.owner_id != caller.id: raise 403` lifts to `owner_is(user)` under `NoRoles`, where no Admin exists.
- An Enum member default (`Shelf.UNREAD`) is a literal default.
- With no single-argument token mint, the Lattice side is logged in with the token the original's own login route returns. This is the same credential, decoded by the import's auth.

| repo | routes | verified | islands (R4) | need the LLM |
|---|---:|---:|---:|---:|
| TaskFlow | 10 | **1** (`read_current_user`, 1 declared token-policy deviation) | 2 | 7 |
| JustNotes | 30 | 0 | 26 | 4 |

**Remaining gap classes:**
- **TaskFlow, 7 needing the LLM: no lifter, not a language gap.**
  - `register` and `login` have no lifter for their shapes.
  - The five task routes go through package helpers the lifters do not follow: `crud.create_task`, `require_task` → `crud.get_task(…, owner_id)`, and `crud.list_tasks` with an `ilike` search and optional enum filters.
  - For the same reason, owner inference leaves `Task.owner_id` unset (an unclear decision).
- **TaskFlow, 2 islands:** `/` and `/health` are defined inside the `create_app()` factory, an unsupported framework feature.
- **JustNotes, 26 islands: *language gap: the token subject is not a unique users column.*** `User.username` is the JWT subject, but it has no unique constraint: `create_user` checks for duplicates in code only. `NativeToken(lookup=)` requires a declared unique column, because the authentication statement must find at most one row. This is by design, not relaxed.
- **JustNotes counterfactual (scratch copy only, not the record):** with `username` declared unique, all 26 routes stop being islands and become needs-LLM. 25 have no matching lifter (repository classes, `dict` responses, nested owner objects). `read_users_me` lifts, but the original's login fails on the seed (500), because `password=hashed_password` hides the hasher from `_hash_native`, so the seed stores the raw password.
- **JustNotes, 4 needing the LLM:** no lifter for `register`, `login`, `health` and `get_public_notes`.

The template import is unchanged: 23 routes, 10 verified, and byte-identical `profile.py`, `natives.py` and `routes/` compared with HEAD.

## Held-out import (live)

Full importer, LLM loop on: `python -m lattice.importer . --package bench.import_repos.<repo>.app --app-factory bench.import_repos.<repo>:make_app --out bench/imports/<repo> --method M2 --model claude-sonnet-5 --max-cost-usd <cap>` (effort high, strong effort max, 16k output tokens). The outputs in `bench/imports/<repo>/` (profile, natives, `routes/`, `report.md/json`) are the importer's, unedited. The round artefacts (prompts, responses, candidates) are in the gitignored `bench/results/imports/`.

**JustNotes auth decision (language):** `NativeToken(native, lookup="username", unique="runtime")`. The token subject may be a column with no unique constraint. The authentication statement fetches up to two rows: none → 401; more than one → a loud 500, `R311 ambiguous caller`, never one of them picked. It behaves the same in the runtime and the generated app (differential program `solo_runtime`, with two users named `dave`). `unique="runtime"` without `lookup` is refused, since the key is unique anyway. The importer uses it when the claim's column is not declared unique and records an `Unclear` (auth) note saying so. The earlier "token subject is not a unique users column" gap is closed.

| repo | routes | R0 | R1 | R2 | R3-procedure | R3-native | R4 (islands) | tier mix | cost |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|
| TaskFlow | 10 | 1 | 4 | 0 | 0 | 0 | 5 | T0 5, island 5 | $2.94 |
| JustNotes | 30 | 1 | 1 | 0 | 0 | 0 | 28 | T0 2, island 28 | $2.71 |

Verified (Lattice) routes:
- **TaskFlow:** `register`, `read_current_user`, `create_task`, `read_tasks`, `read_task`.
- **JustNotes:** `read_users_me`, `health`.

**Island reasons:**
- **TaskFlow `login`, `update_task`, `delete_task`:** every R2/R3 attempt was cut off at the 16k output limit (`missing construct` / "cut off"). `update_task` and `delete_task` are also victims of an importer bug found in this run (fix 5 below). Their R1 candidates were checked against the original with a stale 30-minute token. Offline, the R1 candidate for `delete_task` verifies after the fix.
- **TaskFlow `/`, `/health`:** defined inside `create_app()` (unchanged).
- **JustNotes `register`, `login`:** cut off at the output limit in R1/R2/R3.
- **JustNotes, 26 routes:** `budget`. The repo's cap was reached after `register` and `login` ($1.98 of the $2.71). `get_notes` ran out mid-ladder ($0.66), and 25 routes were never attempted. No other gap class was reached.

**Spend.** Total recorded spend was **$8.78**, within the $10 authorisation:
- final TaskFlow run: $2.94;
- final JustNotes run: $2.71;
- aborted runs: $3.13 (TaskFlow ×2: $0.31; JustNotes ×1: $2.82).

Each run was stopped when an importer bug was found that invalidated its verification. The in-flight calls of the three killed runs are not in the artefacts, at most ~$0.2 each.

**Importer fixes the live run needed** (generic, each with a synthetic test):
1. **Verification normalised the template's `hashed_password` only.** A route storing a salted hash in `password_hash`/`password` could never verify. It now normalises the suite's own password column (`tests/importer/test_import_hash_columns.py`).
2. **The hasher was not found through a local name.** In `h = hash_password(p); User(password=h)`, JustNotes' seed stored raw passwords and its login answered 500 (`test_the_hasher_is_found_through_a_local_name`).
3. **Token cases with no single-argument mint.** A claims minter is derived from the token decode: the library, key and algorithm, followed into a decode helper. The suite now requests a valid token for no user, one without the claim, and (with an active column) one for the inactive user. The token policies now read a decode helper's `if payload is None`, a claim guard, and any caller lookup the dependency returns (`or`-ed active checks included). Before this, every authenticated JustNotes route had two unreachable raise sites.
4. **Suite requests hit the wrong rows:**
   - Unique-conflict cases now carry the rest of a valid body (TaskFlow `register`'s 409 was answered 422).
   - The seed's "own row" for a table the profile gives no owner is its one FK to the users table. Seed only; the profile is unchanged. Before this, TaskFlow's task routes were requested on another user's task and only saw 404s.
5. **Cached login tokens expired.** The Lattice side reused the original's login token for the whole run, so after 30 minutes every case answered 401. Tokens are now fetched again after 5 minutes; minted test tokens last 7 days.

The no-LLM record above changes: JustNotes goes from 30 routes, 0 verified, 26 islands to **30 / 1 verified (`read_users_me`) / 0 islands / 29 needing the LLM**. TaskFlow's is unchanged.

**Arm L on the held-out tickets.** `run.start_lattice(repo)` reads `bench/imports/<repo>`. `run.emit_lattice` mounts the programs into the original app with `lattice.emit.thin.emit_into_app`, the template's route-preserving merge generalised to any app factory:
- all routes, full paths;
- included routers re-wrapped one by one;
- `settings_from_app` for NativeContext natives;
- schema evolution once the app's tables exist.

The routes the import did not lift stay the original code. A fake-LLM run (the start programs returned unchanged) runs end to end on all six tickets.

| ticket | touched routes in the start state |
|---|---|
| t19_task_summary | `GET /api/v1/tasks/summary`: new |
| t20_bulk_status | `POST /api/v1/tasks/bulk-status`: new |
| t21_completed_at | POST/GET list/GET one: lifted; `PATCH /api/v1/tasks/{task_id}`: island |
| t22_copy_note | `POST /notes/{id}/copy`: new |
| t23_note_filter | `GET /notes`: island (budget) |
| t24_note_stats | `GET /notes/stats`: new |

**Remaining gaps:**
- **Output limit.** Many R1–R3 answers were cut off at 16k tokens, with high/max effort. This decided 5 islands; a larger limit or lower effort is the next knob.
- **JustNotes' cost per route.** About $0.07–1.2 per route; 25 routes were never attempted within the cap.
- **JustNotes' nested-resource 403s** (6 tag/keyword routes: `/notes/{note_id}/tags/{tag_id}`). The suite never requests another user's note there, so those routes would end as suite-gap islands even with budget.
- **t21 and t23 touch islands.** Arm L can add routes but not change those.

## Thin slice round 3: T2 language, 24 tickets across 3 apps (288 runs, 2026-09-27)

Setup:
- The language now has T2 procedures, the round-2 fixes and the review fixes.
- Tickets:
  - t1–t6 were **tuned on** (the language was fixed after seeing them);
  - t7–t18 are **held-out template** tickets;
  - t19–t24 are **held-out apps** (TaskFlow, JustNotes). Their arm-L start state is the live import, which verified 5/10 and 2/30 routes.
- Total cost is $18.67.

| model | arm | t1–t6 tuned | t7–t18 held-out template | t19–t24 held-out apps | silent total | $/correct |
|---|---|---|---|---|---:|---:|
| sonnet-5 | P | 15/18 (3 S) | **33/36** (3 S) | **12/18** (6 S) | 12 | 0.064 |
| sonnet-5 | L | **18/18** (0 S) | 26/36 (4 L, 6 S) | 0/18 (14 L, 4 S) | 10 | 0.237 |
| haiku-4-5 | P | 12/18 (6 S) | **27/36** (9 S) | **12/18** (6 S) | 21 | 0.032 |
| haiku-4-5 | L | **16/18** (2 S) | 15/36 (11 L, 10 S) | 0/18 (17 L, 1 S) | 13 | 0.089 |

**On tuned tickets, Lattice now beats Python on both correctness and silent bugs.** Sonnet scores 18/18 with 0 silent (P: 15/18, 3 silent); Haiku 16/18 with 2 silent (P: 12/18, 6 silent). t2 (timezone offsets) and t4 (the admin check) are exactly the traps Lattice's semantics catch.

**On held-out template tickets, Lattice loses.** Sonnet scores 26/36 vs 33/36, and Haiku 15/36 vs 27/36; silent failures are about even. Two tickets fail identically in both arms because the oracle over-specifies:
- t14: bounds on `current_password`;
- t17: a missing field.

These should be treated as oracle defects. Most of the other L failures are loud checker refusals of ordinary Python idioms:
- `row.title` attribute access (L106);
- `a + b` on strings (L402, "use fn.concat");
- several ops in one statement (L302);
- keyword forms of `when`.

**On held-out apps, Lattice scores 0/18.** The causes, from the saved artefacts:
- **Sonnet:** truncated at the 16k output limit in 9/18 runs while writing whole imported files for an unfamiliar profile.
- **Haiku:** checker refusals (L821, L402, L803).
- **Imported auth policy:** on t24 (Sonnet, silent) the imported auth policy answers bad tokens differently from the original, which is an importer defect.

These results say more about how ready the import is than about the language.

**Verdict.**
1. Lattice's guarantees work: wherever the language fits the task, it removes silent bugs and beats Python.
2. Generalisation is the weak point. On new tickets, and especially on new apps, the strict canonical surface (one op per statement, no attribute access, no `+` on strings, `col()` everywhere) makes models fail loudly far more often than Python does, and the import pipeline is not yet good enough to start edits from on unfamiliar apps.
3. The "familiar surface, strict core" principle is under-delivered: the surface is still too far from Python. The next leverage is to **accept ordinary Python spellings in T0 and normalise them in the checker** (canonicalise rather than reject), while keeping the guarantees. Separately, fix the import-readiness issues:
   - output-size-aware editing (edit only the touched endpoint, not whole files);
   - the imported auth-policy mismatch;
   - more lifter coverage.

## Thin slice round 4: familiar surface, scoped edits, import fixes (360 runs, 2026-09-28)

Changes since round 3:
- **Language:** normalisation of Python spellings (N1/N1b).
- **Harness:** partial answers are merged by name in both arms (N2).
- **Import:** token-answer fidelity, and a re-import at TaskFlow 6/10 and JustNotes 6/30 (N3).
- **Benchmark:** oracle hygiene for t14/t17, and fresh held-out tickets t25–t30 (N4).
- **Review fixes:** 43537da.

Total cost is $23.21.

| model | arm | t1–t6 tuned | t7–t18 held-out template | t19–t24 held-out apps | t25–t30 fresh | **silent** | correct | $/correct |
|---|---|---|---|---|---|---:|---:|---:|
| sonnet-5 | P | 15/18 (3S) | **36/36** | **13/18** (5S) | **12/18** (6S) | 14 | **76/90** | 0.061 |
| sonnet-5 | L | 15/18 (3L) | 32/36 (4S) | 0/18 (18L) | 9/18 (9L, **0S**) | **4** | 56/90 | 0.225 |
| haiku-4-5 | P | 11/18 (7S) | **28/36** (8S) | **11/18** (7S) | **9/18** (9S) | 31 | **59/90** | 0.037 |
| haiku-4-5 | L | **16/18** | 16/36 (15L, 5S) | 0/18 (11L, 7S) | 6/18 (10L, 2S) | **15** | 38/90 | 0.099 |

**Round-3 rescoring under the corrected t14/t17 oracle:** 17 of 24 runs changed from silent to correct. The changes were in both arms, which confirms those cases were oracle defects.

**Findings**

1. **The core safety claim now holds on every ticket group, on both models.** Lattice's silent (wrong but plausible) answers are 4 vs 14 on Sonnet and 15 vs 31 on Haiku. On the fresh held-out tickets Sonnet-L produced **0** silent answers, against 6 for Python.
2. **The price is loud failures.** Arm P never fails loudly. Arm L's loud failures, from the saved artefacts, are:
   - **Truncation** (Sonnet): 18 runs hit the 16k output limit in some round, and 7 ended that way. The model's long thinking on Lattice consumes the budget. Arm P never truncates.
   - **Checker refusals for real guarantee violations** (L802 ownership, L803 private columns, L404, L834 response shape). The checker is doing its job, but models don't reach a passing program within 2 repairs. First-round checker pass rate is 37/90 (Sonnet) and 27/90 (Haiku).
   - **The held-out apps (0/18 for L on both models) are an import problem.** The imported TaskFlow and JustNotes profiles leave table **owners unset**: owner inference reports them "unclear" rather than guessing. As a result, every `accessible`/`mine`/`owner=` the model writes is refused (L404 "needs an owner column"), and existing programs trip L802 once edited.
3. **Excluding the held-out apps** (72 runs per arm):

   | model | arm | correct | silent |
   |---|---|---:|---:|
   | sonnet-5 | L | 56/72 | 4 |
   | sonnet-5 | P | 63/72 | 9 |
   | haiku-4-5 | L | 38/72 | 8 |
   | haiku-4-5 | P | 48/72 | 24 |
4. **Normalisation had a small effect.** Accepting ordinary Python spellings moved round-3 refusals little (2/37). The remaining refusals are about guarantees and the shape of the data, not about spelling.

**Verdict.** Lattice now delivers what it promises: far fewer silent bugs, on tickets and apps it wasn't tuned for. It does not yet deliver equal-or-better correctness. It trades silent failures for loud ones at 2.7–3.7× the cost per correct answer.

The next levers, in order of expected impact:
1. **Import ownership:** infer owners from foreign keys to the users table plus route usage, or let the edit declare them in the profile delta. This unblocks the held-out apps.
2. **Output budget:** raise `max_tokens` in both arms equally (e.g. 32k) and use lower effort for edits.
3. **More repair rounds with better checker feedback:** each guarantee error should point at the exact fix in the model's own code.
4. **Cost:** prompt caching of the static Lattice guide. The guide is identical across runs and is ~60% of arm L's input tokens.
