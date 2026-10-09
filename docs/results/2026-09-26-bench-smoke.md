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

## Held-out import (round 5, CLI)

Lever 1 above, closed: sound owner inference (df8c1c5 -- single-user-FK + caller-use, shared tables, `owner_via` for link/child tables) and the Claude Code CLI transport (7931377, `lattice.llm.cli_client.ClaudeCliClient`, identical prompts to the API arm) re-import both held-out apps, replacing the committed `bench/imports/taskflow` and `bench/imports/justnotes` that round 5's arm L starts from:

```
uv run python -m lattice.importer . --package bench.import_repos.<repo>.app \
  --app-factory bench.import_repos.<repo>:make_app --out bench/imports/<repo> \
  --backend cli --method M2 --model claude-sonnet-5 --max-cost-usd 6
```

Other flags at node N3's defaults: 32k max output tokens, effort medium (R1)/medium (R2 relift)/max (R3), per-route budget shares of the repo cap. Round artefacts are in the gitignored `bench/results/imports/<repo>-r5/`; cost is the CLI's own list-price estimate (`total_cost_usd` from `claude -p --output-format json`, `cost_basis="cli_list_estimate"`), the same figure a user's terminal would show.

| repo | routes | R0 | R1 | R2 | R3 | R4 (islands) | tier mix | cost |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| TaskFlow | 10 | 1 | 4 | 1 | 0 | 4 | T0 6, island 4 | $2.02 |
| JustNotes | 30 | 1 | 9 | 0 | 0 | 20 | T0 10, island 20 | $6.01 |

Total spend: **$8.03** (well inside the $6-per-repo cap, $12 combined authorisation).

**Owners now declared in the profile** (previously unset -- every table read "unclear" and every `accessible()`/`owner=` the edit slice wrote was refused with L404):
- **TaskFlow:** `Task.owner = 'owner_id'` -- "its one users foreign key, which create_task, delete_task, read_task, read_tasks, update_task filter, check or set from the caller". `User` carries no owner (it's the users table itself). No unclear ownership decisions remain for TaskFlow.
- **JustNotes:** `Note.owner = 'owner_id'` (also private, since no response model returns it directly), `Tag.owner = 'owner_id'`, `Keyword.owner = 'owner_id'`; the link tables are owned through Note: `NoteTagLink.owner_via = ('note_id', 'Note')`, `NoteKeywordLink.owner_via = ('note_id', 'Note')`. Remaining "Unclear profile decisions" are unrelated to ownership: `NoteTagLink`/`Tag`/`NoteKeywordLink`/`Keyword` have no response model to infer private columns from, and the auth lookup column (`User.username`) is not declared unique so the importer keeps `NativeToken(..., unique="runtime")` (an ambiguous-caller 500, not a 401, on a duplicate username -- unchanged from the previous import).

**Verified routes:**
- **TaskFlow (6):** `register`, `read_current_user`, `create_task`, `read_tasks`, `read_task`, `delete_task`. Islands (4): `login` (checker refuses the lifted program), `update_task` (behaviour not reproduced: `PATCH`'s partial-null semantics), `service_info`/`health_check` (defined inside `create_app()`, no request suite).
- **JustNotes (10):** `read_users_me`, `health`, `get_notes_summarize`, `get_notes_related`, `get_note`, `delete_note`, `delete_tag`, `get_user_keywords`, `delete_keyword`, `list_users`. Islands (20): `register` (behaviour not reproduced: database effects differ), `update_note`/`get_user_tags` (checker refuses the lifted program), `add_tag_to_note`/`remove_tag_from_note`/`add_keyword_to_note`/`remove_keyword_from_note` (a 403 branch no suite case reaches), the rest (`login`, `get_notes`, `create_notes`, `get_public_notes`, `get_notes_extend`, `get_notes_translate`, `get_notes_auto_tag`, `get_notes_keywords`, `get_notes_sentiment`, `create_tag`, `get_note_tags`, `create_keyword`, `get_note_keywords`) budget -- the $6 cap was reached before their turn.

**Lift status of the routes t19-t24 and t28-t30 touch, recomputed against this import** (`bench.edit_slice.run.lift_status`):

| ticket | touched routes in the start state |
|---|---|
| t19_task_summary | `GET /api/v1/tasks/summary`: new |
| t20_bulk_status | `POST /api/v1/tasks/bulk-status`: new |
| t21_completed_at | `POST /api/v1/tasks`, `GET /api/v1/tasks`, `GET /api/v1/tasks/{task_id}`: lifted; `PATCH /api/v1/tasks/{task_id}`: island |
| t22_copy_note | `POST /notes/{id}/copy`: new |
| t23_note_filter | `GET /notes`: island (budget) |
| t24_note_stats | `GET /notes/stats`: new |
| t28_task_reassign | `POST /api/v1/tasks/{task_id}/reassign`: new |
| t29_tag_summary | `GET /tags/summary`: new |
| t30_bulk_delete_notes | `POST /notes/bulk-delete`: new |

t19-t24's status is unchanged from the previous import (TaskFlow's task routes and JustNotes' `/notes` were already lifted/island the same way); t28-t30 touch routes with no prior existence in either app, so they are `new` under any import. A fake-LLM arm-L run (the start programs returned unchanged) builds, emits into the original app and smoke-runs end to end on all nine tickets (`tests/bench/test_edit_slice_imports.py::test_a_fake_llm_arm_l_run_is_end_to_end` for t19/t24, `tests/bench/test_edit_slice_round4.py::test_a_fake_llm_arm_l_run_is_end_to_end` for t28-t30).

**Comparison with the previous import** (TaskFlow 6/10, JustNotes 6/30, both LLM loop on M2/claude-sonnet-5 at the same N3 defaults, API backend):

| repo | verified before | verified now | change |
|---|---:|---:|---|
| TaskFlow | 6/10 | 6/10 | **same routes** verified and island as before (`register`, `read_current_user`, `create_task`, `read_tasks`, `read_task`, `delete_task` verified; `login`, `service_info`, `health_check`, `update_task` islands) -- `create_task`/`read_tasks` swapped R1/R2, and `login`/`update_task`'s island reasons changed from the old run's stale-token/cut-off artefacts to a checker refusal and an unreproduced `PATCH`-partial-null; cost $1.46 -> $2.02. Ownership now sound (`Task.owner='owner_id'`, 0 unclear decisions, was 1 "touch Task without comparing owner_id to the caller") |
| JustNotes | 6/30 | 10/30 | **+4 routes** (`get_notes_summarize`, `get_notes_related`, `get_note`, `get_user_keywords`, `list_users` newly verified; `get_notes_extend` no longer verified, net +4); ownership now sound on 5 tables (was 0, all "unclear"); cost $6.57 -> $6.01 |

TaskFlow's verified/island route *membership* didn't move -- it's still bottlenecked by `login`'s checker refusal and `update_task`'s `PATCH`-partial-null semantics, not by ownership -- but its one ownership decision (`Task`) went from unclear to sound. JustNotes gained 4 routes net, and -- more importantly for round 5's arm L -- every table it touches now has a sound owner or `owner_via`, so edits that write `accessible()`/`owner=` against Note, Tag, Keyword or their link tables are no longer refused for a profile gap (this is exactly the lever the round-4 write-up above flagged: "the imported TaskFlow and JustNotes profiles leave table owners unset... every `accessible`/`mine`/`owner=` the model writes is refused"). No importer bug was hit in this run; both re-imports completed under budget with no CLI usage-limit error.

## Round 5 — pre-registered decision experiment: language vs checks (900 runs, Claude CLI, 2026-09-28)

**Setup.**
- Spec: `docs/superpowers/specs/2026-09-28-lattice-round5-decision-experiment.md`.
- 5 arms × 30 tickets × 2 models × 3 reps, sent through `claude -p` with context isolated and the API key stripped.
- About $59 at list prices, charged to the plan. Two usage-limit pauses; both resumed cleanly, with no error records.

### Results on the decision scope t7–t30 (pre-registered)

| model | P | L | Q | L+ | Q+ |
|---|---|---|---|---|---|
| sonnet-5: correct / silent / loud | **64**/72 · 8 · 0 | 34 · 31 · 7 | 60 · 12 · 0 | 36 · 32 · 4 | 60 · 12 · 0 |
| sonnet-5: $/correct | 0.078 | 0.137 | 0.076 | 0.186 | 0.106 |
| haiku-4-5: correct / silent / loud | 51 · 20 · 1 | 30 · **14** · 28 | 55 · 17 · 0 | 31 · 20 · 21 | **61** · **11** · 0 |
| haiku-4-5: $/correct | 0.063 | 0.200 | 0.071 | 0.211 | 0.075 |

### Decision rule (§5), applied mechanically

- **Rule 1 ("checks are enough")** holds on Sonnet (S_Q 12 ≤ 33, C_Q 60 ≥ 34, $ within 1.5×). It fails on Haiku by one run: S_Q 17 against S_L + 2 = 16.
- **Rule 2 ("the language is needed")** fails on both models.
- **Outcome: MIXED (§5.3).**

### Secondary: L+ and Q+ (§7)

- **L+** closed 2 of the 30-point correctness gap to P on Sonnet, and 1 of 21 on Haiku. The context aids do **not** fix the knowledge problem, so the §7 criterion is not met.
- **Q+** improved Haiku markedly: 61/72 correct with 11 silent, against P's 51 with 20. It made no difference on Sonnet.

### Confound found in the results

The imported **JustNotes** start state (arm L's starting app) is **unfaithful before any edit**:
- `GET /notes/{id}` answers `owner: <id>` where the original returns the nested owner object;
- another user's *public* note answers 404 where the original answers 200.

These lifted routes passed the importer's generated verification, but the ticket oracles cover cases it didn't. So L/L+ on t22–t24 and t29–t30 were scored against an app that was already wrong. That accounts for most of Sonnet-L's 31 silent failures (20 are on held-out apps). The TaskFlow start state checks out.

**Post-hoc views.** These are not pre-registered and are reported for interpretation only.

| scope | model | P | L | Q | Q+ |
|---|---|---|---|---|---|
| t7–t30 without the JustNotes tickets | sonnet | 50/57 · S7 | 34 · S19 | 45 · S12 | 45 · S12 |
| t7–t30 without the JustNotes tickets | haiku | 45 · S11 | 30 · **S7** (20 loud) | 46 · S11 | **52 · S5** |
| template tickets only (t7–t18, t25–t27) | sonnet | **38/45** · **S7** | 32 · S11 | 33 · S12 | 33 · S12 |
| template tickets only (t7–t18, t25–t27) | haiku | 37 · S7 | 29 · **S4** (12 loud) | 36 · S9 | **42 · S3** |

### What this says

1. **Lattice as the editing interface did not earn its keep in round 5.** Its correctness is well below plain Python on every scope and on both models. Its silent-bug advantage **did not replicate on Sonnet**, even on the unconfounded template tickets (11 vs 7). It shows only on Haiku (4 vs 7), and there at the price of many loud failures.
2. **Plain Python on a frontier model is already strong.** Sonnet-P has 64/72 correct and only 8 silent failures on t7–t30. Round 4's P had more silent failures; sampling variance at 3 reps is large.
3. **Checks + aids on ordinary code (Q+) are the most practical configuration for weaker models.** On Haiku, Q+ is the best arm on correctness and on silent failures, at P's cost.
4. **The importer's verification is too weak for real use:** it passed routes that the ticket oracles show to be wrong. The generated suite needs cases for "another user's public row" and for nested response shapes.

### Honest bottom line and recommendation

The pre-registered rule says MIXED. The weight of evidence points to the **checker plus verified-edit loop on ordinary code** as the product, with Lattice's IR kept as an internal representation where it is lossless (SQL compilation, verified generation). Lattice as the language LLMs write is not supported.

Caveats:
- 3 reps per cell is noisy.
- Round 5 changed the transport (the CLI) and several arms at once.
- The JustNotes confound affects the held-out-app half of the scope.

A confirmatory round with more reps on the template tickets alone would settle the Sonnet silent-failure question cheaply.

## Confirmation run (pre-registered, 2026-09-29)

Spec: `docs/superpowers/specs/2026-09-28-lattice-confirmation-run.md`.

**Setup.**
- The 15 unconfounded template tickets (t7–t18, t25–t27).
- 6 reps per cell, through the Claude CLI.
- 607 of 630 runs completed. Sonnet-L has 67/90: every Sonnet-L run on t25 timed out after 600 s, recorded as 7 error records, and the remaining reps were not reached.

| model | arm | correct [95% CI] | silent [95% CI] | loud | $/correct |
|---|---|---|---|---:|---:|
| sonnet-5 | **P** | **89/90** [0.94, 1.00] | **1/90** [0.00, 0.06] | 0 | 0.060 |
| sonnet-5 | L | 52/67 [0.66, 0.86] | 7/67 [0.05, 0.20] | 1 (+7 timeouts) | 0.074 |
| sonnet-5 | Q | 69/90 [0.67, 0.84] | 21/90 [0.16, 0.33] | 0 | 0.075 |
| sonnet-5 | Q+ | 79/90 [0.79, 0.93] | 11/90 [0.07, 0.21] | 0 | 0.089 |
| haiku-4-5 | P | 80/90 [0.81, 0.94] | 10/90 [0.06, 0.19] | 0 | 0.037 |
| haiku-4-5 | L | 54/90 [0.50, 0.70] | 15/90 [0.10, 0.26] | 21 | 0.114 |
| haiku-4-5 | Q+ | 82/90 [0.83, 0.95] | 8/90 [0.05, 0.17] | 0 | 0.055 |

### Pre-registered verdicts

- **Q1: does Lattice reduce silent bugs with a strong model? REFUTED.**
  - Sonnet-L has 7/67 silent against Sonnet-P's 1/90 (one-sided Fisher p = 0.999, per-ticket sign test 0 wins, 2 losses).
  - The missing Sonnet-L runs cannot change this: even with all 23 non-silent, L is 7/90 against 1/90.
- **Q2: do checks plus aids beat plain Python on a weaker model? NOT CONFIRMED.**
  - Haiku-Q+ has 82 correct and 8 silent, against Haiku-P's 80 correct and 10 silent (p = 0.40 for both).
  - Round 5's large Q+ advantage did not replicate.
- **Outcome per the spec:** "neither confirmed". At this scale, the measured value of both the Lattice language and the Lattice checks on Python is small, or negative.

### Additional observations (not pre-registered)

1. **Plain Python on a frontier model was nearly perfect on these tickets:** 89/90 correct and 1 silent.
2. **The checks made Sonnet worse.** Sonnet-Q (checks, no aids) had 21 silent against P's 1. Acting on check findings seems to push a strong model into wrong edits. The likely cause is over-applying the ownership and admin rules where a ticket wants the opposite, e.g. t15, where an admin's duplicate must keep the original owner. The aids reduced this (Q+: 11), but not below P.
3. **Lattice's failures concentrate on specific tickets:**
   - t15 (copying another row's values) and t11 (trimming an optional field) account for most of the silent failures in both models;
   - t25 (bulk delete) makes Sonnet reason past the 600 s timeout.
4. **Earlier rounds overstated effects.** Rounds 4–5 used 3 reps, and their single-digit differences are within this run's confidence intervals.

### Conclusion

The core hypothesis is not supported by the pre-registered evidence: LLMs editing through Lattice, or with Lattice's guarantees checked, do not produce fewer silent bugs than LLMs editing ordinary Python.
- With a frontier model, plain Python edits are already highly reliable on this class of task.
- With a weaker model, the checks add nothing measurable.

The parts of the project that stand on their own evidence are the engineering artefacts:
- the verified import/emit pipeline;
- differential verification;
- trap-based oracles;
- the SQL compilation of the plan IR, whose output code is shorter and fewer-query.

Their value as an *LLM editing interface* is not demonstrated.

## Option 1 pilot (pre-registered gates, 2026-09-29)

Spec: `docs/superpowers/specs/2026-09-29-option1-pilot.md` (committed in `a1cc717` before any run). Arm P only, claude-sonnet-5, CLI, 3 reps × 8 tickets (t31–t38) on the new held-out multi-vendor app (`bench/import_repos/multivendor`). Family A = aggregations with traps (t31–t34); family O = ownership through parent tables (t35 control, t36–t38).

### Results as scored by the pre-registered oracle

| family | correct | silent [95% CI] | loud | silent-rate gate | mechanism gate |
|---|---|---|---|---|---|
| A | 10/12 | 2/12 [5%, 45%] | 0 | PASS | PASS (2/2 covered, `typed-params`) |
| O | 6/12 | 6/12 [25%, 75%] | 0 | PASS | PASS (3/6 covered) |

Mechanical decision: **GO: A + O**.

### Oracle defects found on inspection (the same treatment as t14/t17 after round 3)

Every silent run was re-scored from its saved final candidate and its diff read:

- **t32 (2 runs):** the model answered an invalid month with **422**, as the ticket says, but with its own error text (`"Invalid month format, expected YYYY-MM"`). The ticket states only the status, so the oracle's comparison of the body over-specified.
- **t36 (3 runs):** a shipped, delivered or already-cancelled order got **400**, as the reference does, with different wording (`"Cannot cancel order with status shipped"`). The ticket never gives the message, so this is the same over-specification.
- **t36 rep 1:** the model also let an **admin cancel a shipped order**. "Admins can cancel any order" supports that reading, so the ticket is ambiguous here; the case (`t36.admin_shipped`) is dropped.
- **t38 (3 runs): a real data-integrity bug.** On a reorder that runs out of stock, all three reps answered 409 but **left a new pending order with total 0 and the candle's stock decremented**. They reused upstream's `place_order`, which commits line by line. This is the trap flagged when the tickets were approved: it is inherited from upstream, and the ticket says "answer 409 as `POST /orders` does", and `POST /orders` itself has the same partial write.

The oracle now compares status and database effects, not bodies, on the 8 cases whose error body the ticket never states (`STATUS_ONLY` in `bench/edit_slice/repos/multivendor.py`), and excludes `t36.admin_shipped`. Rescored with `bench/edit_slice/rescore.py`, with no new model calls (5 outcomes changed, silent → correct):

| family | correct | silent [95% CI] | loud | silent-rate gate | mechanism gate |
|---|---|---|---|---|---|
| A | **12/12** | **0/12** [0%, 24%] | 0 | FAIL | FAIL |
| O | 9/12 | 3/12 [9%, 53%] (all t38) | 0 | PASS | PASS (3/3 covered, `one-statement/atomic`) |

Corrected decision: **GO: O**, resting entirely on one ticket (t38) and one trap.

**Efficiency (24 runs):** $2.07 list-price equivalent (CLI, plan-billed); 233k input and 113k output tokens; 1.08 rounds per run; 41.7 s per run; 58.7 lines changed per run.

### What it says

1. **Aggregation traps don't bite Sonnet in an ORM edit either.** Fan-out, null sums, zero-row groups, month and day boundaries, inactive rows and route order were all handled in 12/12 runs. This matches tabular step 2, where frontier models were at parity.
2. **Ownership through parent tables was also handled.** No run leaked another buyer's or vendor's rows, and all ownership-before-status orderings were right.
3. **The one real slip is transactional:** reusing an existing helper that commits partway through, so a refused request leaves partial writes. That is family 3 of the handoff (effect ordering and atomicity), not the ownership family this ticket was filed under. Lattice's one-transaction-per-write guarantee targets exactly that, but the evidence is 1 ticket × 3 reps.

## Option 1b: atomicity pilot (pre-registered gates, 2026-09-29)

Spec: `docs/superpowers/specs/2026-09-29-option1b-atomicity-pilot.md` (committed in `273d87f` before any run). Arm P only, claude-sonnet-5, **CLI only** (no API key; that transport has been refused in code since `9f1f857`), 3 reps × 6 tickets (t39–t44), each needing several writes to succeed or fail together.

| scoring | correct | silent [95% CI] | loud | silent-rate gate | mechanism gate | decision |
|---|---|---|---|---|---|---|
| as pre-registered | 6/18 | 12/18 [44%, 84%] | 0 | PASS | FAIL (4/12 covered) | SILENT-BUT-UNCOVERED |
| corrected (below) | 11/18 | 7/18 [20%, 61%] | 0 | PASS | PASS (4/7 covered) | **GO: T** |

### Oracle defects (same rule as after the first pilot)

The saved final candidate of every silent run was read. Two cases asked for behaviour the ticket text does not determine, and are now excluded (`EXCLUDED` in `bench/edit_slice/repos/multivendor.py`):

- `t42.empty_list` (3 runs): an empty `item_ids` has no stated answer.
- `t44.one_order` (2 runs): "at least two" order ids, but no stated status for one. The model answered 422 through its own validator, with a different error body.

Rescored with `rescore.py`, with no new model calls: 5 runs changed from silent to correct. **The correction flips the decision.** The pre-registered verdict is SILENT-BUT-UNCOVERED; the corrected one is GO.

### The real slips (read in the code)

- **Partial writes when the model reuses a helper that commits midway:**
  - **t40, 3/3 reps:** the model returned the old stock, deleted the old lines, then called `decrement_stock_optimistic`, which commits each line. A 409 left the order half-rewritten.
  - **t39, 2/3 reps:** the model called `update_details`, which commits, once per product. A later 403 or 404 left the earlier prices changed.
  - This matches t38 in the first pilot, where `place_order` commits line by line. That makes **8 of 9 runs** on the three tickets with a committing helper, against **0 of 12** on t41–t44, where the model wrote the multi-step logic itself and validated everything first.
- **A price of 0 accepted:**
  - t39, 3/3 reps: the ticket says "exactly as if changed with `PATCH`", and PATCH rejects prices ≤ 0.
  - t43, 1/3 reps: the ticket says "as for `POST /products`", which also rejects them.
  - The model declared its own body models without `gt=0`.
- **Admin bypass:** in t40 rep 2 an admin could edit another buyer's order, against "only the buyer who placed the order".
- **Count:** t39, 3/3 reps: a repeated product id counted twice in `updated` (the text asks for the number of products changed). No guarantee applies (tag `none`), which is why the three t39 runs are not covered.

**Efficiency (18 runs):** $2.19 list-price equivalent (plan-billed through the CLI); 212k input and 134k output tokens; 1.22 rounds per run; 67.7 s per run; 80.4 lines changed per run.

### What it says

1. **Sonnet's one systematic weakness on this app is transactional.** It shows when an existing helper commits partway, and the model reuses it inside a multi-step change. Lattice's one-transaction-per-write guarantee is aimed at exactly this. The case is still narrow: 3 tickets, all with committing helpers.
2. **Unvalidated parameters on hand-rolled request models** (price 0) are the second pattern, and typed, bounded parameters cover them.
3. **Everything else Sonnet handled:** aggregations, ownership, validate-before-write when writing its own code, route order, and date boundaries.

Per the spec, a GO means pre-registering the main experiment (P / L / Q on t38–t44, ≥ 6 reps, CLI only) after importing the app into Lattice with a verified start state. It needs the user's go-ahead, since the decision rests on a post-hoc correction.

## Option 1 main experiment: Lattice's atomicity guarantee as a check on Python (pre-registered, 2026-09-29)

Spec: `docs/superpowers/specs/2026-09-29-option1-main-experiment.md`, committed in `66be834` before any run.

- **Design:** arm P (plain Python) vs arm Q (Python plus pycheck findings, including the new PY807 partial-writes check) on t38–t44 of the multi-vendor app.
- **Scale:** claude-sonnet-5 (primary) and claude-haiku-4-5, 6 reps: **168 runs, all through the CLI**, no API key.
- **Arm L was dropped before any run:** Lattice's flat responses cannot express 5 of the 7 tickets' nested answers, and the import verified only 3 of 17 routes. The spec records both reasons.

| model | arm | correct [95% CI] | silent [95% CI] | loud | silent on a partial-write case | $/correct | rounds | s/run |
|---|---|---|---|---|---|---|---|---|
| sonnet-5 | P | 24/42 [42%, 71%] | 18/42 [29%, 58%] | 0 | 17 | 0.169 | 1.07 | 53 |
| sonnet-5 | Q | **34/42** [67%, 90%] | **8/42** [10%, 33%] | 0 | **0** | 0.167 | 1.38 | 72 |
| haiku-4-5 | P | 10/42 [13%, 39%] | 30/42 [56%, 83%] | 2 | 14 | 0.253 | 1.33 | 71 |
| haiku-4-5 | Q | 16/42 [25%, 53%] | 23/42 [40%, 69%] | 3 | **0** | 0.231 | 1.83 | 97 |

Per ticket, silent runs (P / Q):

| model | t38 | t39 | t40 | t41 | t42 | t43 | t44 |
|---|---|---|---|---|---|---|---|
| Sonnet | 6/0 | 6/6 | 6/2 | 0/0 | 0/0 | 0/0 | 0/0 |
| Haiku | 6/0 | 6/6 | 6/5 | 2/2 | 2/3 | 5/5 | 3/2 |

Cost: $15.97 list-price equivalent in total, plan-billed through the CLI. Tokens (in/out): Sonnet P 408k/242k, Q 605k/326k; Haiku P 422k/337k, Q 686k/465k.

### Pre-registered verdicts (`bench/edit_slice/main_analysis.py`)

- **Q1 (Sonnet): CONFIRMED.**
  - One-sided Fisher on silent runs: p = **0.016**.
  - The sign test favours Q, 2 tickets to 0; with only two untied tickets its p is 0.25.
  - Correctness guard met: Q's correct rate is higher than P's, 81% against 57%.
- **Q2 (Haiku): NOT CONFIRMED** (Fisher p = 0.087; sign test 3 to 1).
- **Q3 (mechanism): CONFIRMED on both models.** Runs silent on a partial-write case: Sonnet **17 → 0**, Haiku **14 → 0** (Fisher p < 0.0001 for each).

### Oracle review (as pre-registered)

Every silent run's final candidate was re-run and its diff read.

**Genuine on both arms:**
- partial writes (P only);
- a price of 0 accepted, against "exactly as PATCH" (t39) and "as `POST /products` would" (t43);
- a repeated product id counted twice (t39);
- the admin answered with the permission message where the ticket gives "You do not have access to this order" (t40);
- a stock check that ignores what the order already holds (t40);
- response shapes off the stated models: an extra `order_id` in items (t42, t44) and a missing `version` in products (t43);
- a buyer answered 409 instead of the 404 of "same access as `GET /vendors/me`" (t41).

**One oracle defect:** `t43.no_products`. The text neither allows nor refuses an empty `products` list, so the case is now excluded. Rescored without new model calls, it changes no run's outcome (the affected run was also silent on another case). **The corrected verdicts equal the pre-registered ones.**

The review also found a bug in `bench/edit_slice/rescore.py`: it rebuilt arm Q candidates as Lattice. It is fixed and tested; only arms L and L+ are Lattice.

### What it says

1. **This is the project's first confirmed, pre-registered advantage on a frontier model.** The confirmation run had found that checks made Sonnet *worse*. Here, one of Lattice's guarantees, stated as a check on ordinary Python (PY807: one transaction per write), removes a class of silent bug Sonnet makes: every partial write on both models, with correctness up, not down. It comes from the checker applied to Python, not from Lattice as an editing language.
2. **The effect is where the mechanism is.** Q's remaining silent runs are all on traps PY807 does not cover: validation, counting and response shapes. On those, Q and P do about equally, which fits the checks having no effect outside their rule.
3. **Haiku gains the same mechanism but not overall.** Its silent failures are spread over many trap kinds the checks don't cover, so removing partial writes alone did not reach significance.
4. **Scope:** 7 tickets on one app, 3 of which (t38–t40) involve a helper that commits partway. The result is about that class of change, not about edits in general.

## Go target — equivalence gate (sub-project 5a, 2026-09-29)

Plan: `docs/superpowers/plans/2026-09-28-lattice-go-target-5a.md`. Ledger and task reports:
`.superpowers/sdd/2026-09-28-lattice-go-target-5a/`. Branch `go-target`.

### What was built

A second compile target for the T2 (procedure) profile: Lattice programs compile to a
standalone Go binary — `net/http`, a pure-Go SQLite driver (no cgo) — instead of, or
alongside, the generated FastAPI app. The T2 plan IR is shared with the Python path; Go
has its own runtime (`lattice/gogen/rt`) implementing every T2 call, with its own
regex, math, datetime, JSON and collection code written to match CPython's own
algorithms rather than approximate them (notably a full port of CPython 3.14's SRE
regex engine, and a dict/set implementation that reproduces CPython's entry-table
layout and resize policy, because programs can observe iteration order).

### The gate

`tests/gogen/test_go_differential.py` runs every Go-eligible program in the registry
through both the in-process Python runtime and the built Go binary (over HTTP, through
`lattice/gogen/proxy.py`), on the same fresh copy of the seed database per case, and
compares:

- status, and the **first hop's** status and `Location` (so a followed redirect can't
  hide a divergence behind the final 200);
- content type;
- the full JSON body, type-strict (or the raw bytes, for a non-JSON body);
- the SQL statement count (`X-Lattice-Sql-Count`);
- for the two T2 (procedure) programs, the SQL statements themselves — text, bound
  values and order, via a test-only trace that expands each statement exactly as
  SQLite's own C trace callback would;
- `after_sql` rows;
- a whole-database digest.

**Result: 49 programs, 2,478 cases, 0 mismatches** (commit `545d1ea` with 2,458
cases; the first-hop, content-type and raw-body checks were added in a fix round,
commit `a0a283f`, still 0 mismatches; the final review's fix round added 20 `pattern=`
cases, 13 plus their generated probes, to the `edges` program, still 0 mismatches). Two further checks pass: on the two T2 programs, the 478 cases with SQL
steps run identical statements in identical order on both sides, and fuel parity holds
on both (three bisected sizes give 500/200/500 on both targets, with matching R620
bodies).

The 49 programs cover page totals (including a page past the end), trailing-slash 307
redirects (with and without a query, PATCH included), `unique_or=Conflict` (409, DB
unchanged), owner transfer (success and missing-user 404), UNSET partial updates
(including an explicit null written to a nullable column), 204 deletes, R310 (500),
route precedence, `Str(pattern=...)` constraints (`(?i)` without Unicode case folding,
so "ſ" and the Kelvin sign are refused; `\Z`; `{m,n}` and `{,n}` bounds), and, for the T2 programs, derive/where/require compiled to SQL,
`call` bound as a scalar and for `insert_many`, and procedures called from derive over
the caller.

**Exclusions: 11 programs**, each rejected with **L890 only** ("a native (call, effect
or NativeToken auth) on a target without natives"): `realapps`, `partb`, `effects`
(uses `for_each`, a native effect), `gaps`, `mine`, `round2`, `t2`, `solo`,
`solo_runtime`, `solo_answers`, `n1b`. Getting to that exact set required one checker
fix: `p8_target` was also flagging the `native` namespace's own value kind as a second,
duplicate error (L892, "values of type native are not available on target 'go'") for
the same native use L890 already reported. With the fix, every exclusion's error set is
`{L890}` alone. `t2`'s Go-eligible variant, `t2go` (natives removed), is one of the 49
gated programs.

### The semantics suite

391 cases in the shared suite (`tests/semantics_v11`), each verified against real
Python 3.14 before being checked on either target. **385 run on Go and match Python
strictly** — exact type, exact value, exact fuel left — swept across fuel budgets. **6
remain gated**, all L892, in three forms: `str()`/f-string of a container value (list,
dict, set, Counter), a non-literal `re.sub` replacement, and a `strftime` format (or a
Date/DateTime f-string spec) that isn't a literal of the directives Go implements
(`%Y %m %d %H %M %S %f %y %a %A %b %B %j %p %%`, C locale). Fuel parity holds on all
385 Go cases.

### v1.1 behaviour changes (every target, not Go-specific)

These are language-version changes (`# lattice: v1.1`; the `v1` and `v1.1` headers
are both accepted and both run under the v1.1 rules), decided during this
sub-project and implemented identically on Python and Go; see
`docs/lattice-language-rules.md` §6 (decision log) and §8 (version notes) for the
full rationale and dates.

| Change | Effect |
|---|---|
| 64-bit `Int` (R623) | arithmetic outside the signed 64-bit range raises R623 at runtime (was unbounded, up to 65,536 bits) |
| Insertion-ordered sets | sets iterate in insertion order; set algebra keeps the left operand's elements first, in its order; `Counter.most_common` ties keep insertion order |
| ASCII regex classes | `\d \w \s \b` match ASCII only |
| RE2 subset (L926) | lookaround, backreferences, conditionals, possessive repeats and atomic groups are rejected at check time |
| L913, augmented slice assignment | `xs[a:b] += ys` is rejected, as slice assignment already was |
| R-widen | an Int value in a place the checker types Float converts to a float there (assignment, argument, return, container element, an unstarted `sum`'s `0.0`, a tuple index, `min`/`max`, `round(Int, n)`, `Int ** n`); comparisons stay exact at any depth |
| Exact membership and lookup | `in`, `d[k]` (and the read of `d[k] op= v`), `get`, `pop`, `index`, `count`, `remove`, `discard` compare an Int probe against a Float container exactly, at any depth, consistent with `==` (`2**53 + 1 in [2.0**53]` is False) |
| R-pow | a negative, finite Float base raised to a finite, non-integral exponent raises `ValueError: math domain error` on every target, instead of Python's complex-number result |

### Notable engineering findings

- **Fused multiply-add on arm64.** Go's compiler can fuse `x*y + z` into one FMA
  instruction on arm64, giving a different last bit than CPython's separate multiply
  then add. Caught as Critical in the Task 8 review; fixed by wrapping Float arithmetic
  in explicit `float64(...)` conversions in the emitter, which forces separate
  rounding (`TestExplicitConversionPreventsFusedMultiplyAdd`, and semantics cases
  `float_mul_add_not_fused` / `float_mul_then_sub_not_fused`).
- **Go's own regex engine cannot match Python, even on the checked RE2 subset.** `$`
  without `(?m)` before a trailing newline, empty-match handling next to a previous
  match, `must_advance` semantics, ASCII `\s` including `\v` under `re.ASCII`, and
  ASCII-only `(?i)` all diverge from `regexp.MustCompile`. Task 9 ported CPython
  3.14's SRE engine to Go, opcode for opcode, instead of translating patterns to RE2
  syntax — more code than a translator, but Python's own algorithm rather than an
  approximation of it.
- **The Int→Float widening hole.** Python silently kept an exact int where Go's typed
  Float slot held a converted float (`x: Float = n; str(x)` gave `'1'` on Python,
  `'1.0'` on Go). Turned into the R-widen language rule above (2026-09-28): an Int
  value entering a Float-typed place converts at that point, on every target.
- **The checker's `findall`/`split` typing hole, fixed on every target.** `re.findall`
  and `re.split` were typed `list[Str]` regardless of the pattern's capture groups, so
  a checked program could crash at runtime with an uncatchable AttributeError on real
  Python (`re.findall(r"(a)(b)", s)[0].upper()`). Fixed in the checker
  (`lattice/check/t2/body.py::_regex_result`) for both targets: `findall` with ≥2
  groups types as `list[tuple[Str, ...]]`, `split` with groups as `list[Str | None]`.

- **Parameter patterns were still on Go's regexp until the final review.** T2
  regexes ran on the SRE port, but a `Str(pattern=...)` constraint was matched with
  `regexp.MustCompile`: `(?i)[a-z]+` accepted "ſ" and the Kelvin sign on Go (Python
  refuses them under `re.ASCII`), `x\Z` answered 500 on every request, and `a{1001}`,
  `[[:alpha:]]`, `x{,2}` and `\s` vs `\v` differed. No gated program used such a
  pattern, so the gate could not see it. Parameter patterns are now compiled when the
  Go code is generated, as Python compiles them, and matched by the same SRE port with
  `re.fullmatch` semantics; `tests/api/edges.py` covers them in both differentials.

### Prerequisites (building for Go)

- **Go >= 1.26 on PATH.** Builds run with `GOTOOLCHAIN=local` (go never downloads a
  newer toolchain); a missing or older `go` fails with a `GoBuildError` naming the
  minimum.
- **The runtime's dependencies in the module cache.** Builds are offline
  (`GOPROXY=off`), so a fresh machine needs, once and with network access,
  `cd lattice/gogen/rt && go mod download`. Without it the build fails with a
  `GoBuildError` saying exactly that (go's own message is only "module lookup disabled
  by GOPROXY=off").
- Tests that run the Go toolchain carry the `go` pytest marker and are skipped, with
  that reason, when `go` is not on PATH (`tests/conftest.py`).

### Known limitations (honest)

- **amd64 is not verified.** Rosetta 2 is not installed on the build machine (an Apple
  M5 Pro), so the gate ran on arm64 only — the architecture where FMA fusion is a risk,
  so also the more demanding one to have passed. `GOARCH=amd64` builds succeed but
  could not be run here.
- **Only macOS CPython was checked** as the reference. glibc's `libm` and its
  `strftime` `%Y` padding may differ from macOS's; this was not tested on Linux.
- **Last-ulp differences in transcendental functions.** Go's `math.exp/log/log10/pow`
  and Float `**` are correctly rounded; CPython calls the platform's `libm`, which
  differs from the correctly-rounded value in 9 of 22,403 sampled answers on macOS
  arm64 (one ulp), and differs between platforms even for Python itself. The
  semantics suite avoids such inputs.
- **The JSON nesting depth limit is not identical.** CPython's `json.loads`/`dumps`
  raise RecursionError at a stack-dependent depth (about 50,000 on macOS); Go uses a
  fixed 100,000-level limit.
- **Unicode 15 (Go's `x/text`) vs Unicode 16 (Python 3.14)** for case mapping and
  character classes. No semantics case uses a code point new in Unicode 16.
- **NaN in sort keys.** Go's `sort.SliceStable` and Python's timsort give the same
  result under a consistent ordering, but NaN compares false to everything, including
  itself, so once NaN breaks the ordering the two algorithms may order the sequence
  differently; `min`/`max` inherit the same risk. Not reachable by the semantics suite
  (`task-8-report.md` concern 3).
- **Identity shortcuts in container `==`, tuples only.** Python's container `==` and
  `in` treat `x is y` as equal, so `[nan] == [nan]` is True when both hold the same
  float object. Go's lists and dicts get the same shortcut (matching Python), but a
  tuple compared with itself holding a NaN does not — Go compares values there, not
  identity — an unusual program (`task-10-report.md` concern 4).
- **A `Json` string holding a lone surrogate is WTF-8 on Go, not UTF-8.** `"\ud800"`
  decoded from JSON and turned into a T2 `Str` (`str(j)`) is held as WTF-8 text on Go
  so its code point survives `dumps`/`repr`/indexing; further `Str` operations on that
  value would count its bytes as invalid UTF-8, which a Python `str` never has
  (`task-10-report.md` §1 and concern 4; the ledger's Task 7 carry-forward: "no parity
  attempted for ... lone surrogates").
- **A record with a list field used as a dict key or set element.** Python raises
  `TypeError` (unhashable); Go compares it by pointer instead of raising
  (`task-8-report.md` concern 3).
- **A module constant that would overflow the 64-bit `Int` range fails only on Go, at
  process start.** Go evaluates module constants at package init and applies R623
  there; Python loads the same constant unbounded and only raises once it is used in
  Int arithmetic. A program with a module constant like `BIG = 2 ** 70` starts on
  Python and fails to start on Go (`task-8-report.md` concern 3).
- **The R622 error message's bit count is clamped on Go.** When an intermediate Int's
  bit count exceeds Go's `int64` range, the message shows the clamped `MaxInt64`
  instead of the exact (larger) bit count Python prints; only the message text
  differs, not the error code or behaviour (ledger, Task 7 minor).
- **Two small HTTP-layer divergences from Task 11 remain deferred and are not
  exercised by the gate:** header values are decoded as raw UTF-8 on Go where
  Starlette decodes latin-1 (`auth.go:29`); and `MergeUpdate` compares partial-update
  keys by their JSON text on Go where Python compares by tuple equality, so an Int and
  a Float key that are numerically equal (`1` and `1.0`) merge differently
  (`http.go:247`).
- **The Go runtime reads two private fields of the SQLite driver.** To return stored
  values as Python's sqlite3 does (the driver turns DATE/DATETIME TEXT into
  `time.Time`), `rt/rawsql.go` runs statements through SQLite's C API on the handle
  and TLS in modernc.org/sqlite's unexported `conn.db` / `conn.tls`, behind type
  guards that panic rather than misuse memory. Upgrading modernc.org/sqlite therefore
  follows the procedure in `rawsql.go`'s header: pin modernc.org/libc to the exact
  version sqlite requires, then `go test ./...` (`TestHandleOnRealConnection`,
  `TestLibcMatchesSqlite`) and the gate.
- **The HTTP request body is unbounded on both targets.** Neither Python nor Go caps
  it; a production deployment needs a body-size profile setting and a matching 413 on
  both, not yet built.
- **Natives, `for_each`, `NativeToken` auth and the tabular profile are not on Go**
  (L890/L891 in the decision log, §6 of `docs/lattice-language-rules.md`). A program
  using any of them compiles for Python only.

### What is not claimed

This is **tested equivalence, not a proof**: 2,478 request/response comparisons and
385 semantics cases, 0 divergences found — not a formal equivalence proof covering
every Lattice program. It makes **no claim about LLM benefit, cost or runtime
performance** of the Go target; that is sub-project 5b (the benchmark), not yet run.

### Binary size and cold start (measured)

Apple M5 Pro, arm64, macOS. `tests/fixtures/api/c01/lattice.py`, profile
`bench.api_datasets:api_profile`, built with
`uv run python -m lattice.gogen build ... --out <dir>`.

- **Binary size:** 15,266,994 bytes (~14.6 MiB) — one statically linked Mach-O arm64
  executable, no cgo, no external SQLite library.
- **Cold start:** median **6.6 ms** over 20 starts (wall-clock from process launch to
  the `LISTENING <host:port>` line on stdout), range 5.8–10.6 ms; one of the 20 runs
  took 195 ms (evidently a first-run disk-cache effect), which the median is
  insensitive to.

## Go cost pilot (5b stage 1, pre-registered, 2026-09-29)

Spec: `docs/superpowers/specs/2026-09-29-lattice-go-pilot.md`, pre-registered and frozen at
commit `d5df467` (parent `4b078d8`) immediately before the live run.

**Setup.**
- 480 runs: the 40 API tasks × 2 arms (B = Lattice→Go, G = hand-written Go against a
  Go platform kit that only authenticates) × 2 models (claude-sonnet-5 primary,
  claude-haiku-4-5 secondary) × 3 reps.
- Transport: the Claude CLI only, with the existing isolation flags, resumable via
  `--resume`; cost from the CLI's list-price estimate (`cost_basis =
  "cli_list_estimate"`, the same basis for both arms).
- **0 harness errors** in all four cells (120 runs/cell each for B and G, both models)
  — well under the 5% threshold that would have made the run INVALID.
- Raw records: `results/go-pilot/outcomes.jsonl` (gitignored, per `/results/` in
  `.gitignore`). sha256: `a4729b82741b7a61c12d39bcee6ca947a916435ffb98e8ff39560d132c3aa7db`.

| model | arm | n | correct [95% CI] | silent [95% CI] | loud [95% CI] | $/correct |
|---|---|---|---|---|---|---:|
| claude-haiku-4-5 | B | 120 | 120/120 [0.97, 1.00] | 0/120 [0.00, 0.03] | 0/120 [0.00, 0.03] | 0.0156 |
| claude-haiku-4-5 | G | 120 | 120/120 [0.97, 1.00] | 0/120 [0.00, 0.03] | 0/120 [0.00, 0.03] | 0.0259 |
| claude-sonnet-5 | B | 120 | 120/120 [0.97, 1.00] | 0/120 [0.00, 0.03] | 0/120 [0.00, 0.03] | 0.0130 |
| claude-sonnet-5 | G | 120 | 120/120 [0.97, 1.00] | 0/120 [0.00, 0.03] | 0/120 [0.00, 0.03] | 0.0189 |

### S2 (secondary, descriptive): program size, rounds, output tokens

| model | arm | n | mean program lines | mean AST nodes | mean rounds | mean output tokens |
|---|---|---|---:|---:|---:|---:|
| claude-haiku-4-5 | B | 120 | 11.9 | 115 | 1.23 | 2421 |
| claude-haiku-4-5 | G | 120 | 83.8 | 472 | 1.06 | 4571 |
| claude-sonnet-5 | B | 120 | 11.6 | 116 | 1.28 | 370 |
| claude-sonnet-5 | G | 120 | 77.9 | 450 | 1.01 | 1071 |

### Pre-registered verdicts

- **P1** (cost ratio R = B/G on claude-sonnet-5): R = 0.689 [0.591, 0.803] → **not
  confirmed** (the threshold was ≤ 0.6 with an upper bound < 1.0).
- **P2** (non-inferiority of correct(B) on claude-sonnet-5, margin 10 points): **holds**.
- **Stage-1 verdict: STOP.**

Per the spec's "What each outcome means" table, for **"STOP, P1 not confirmed or P2
fails"**:

> Any cost advantage is too small or costs correctness. Report honestly; no further Go
> benchmark investment.

### Additional observations (not pre-registered)

1. **B is cheaper per correct task on both models** (Sonnet R = 0.69, Haiku R = 0.60),
   and the Sonnet interval excludes 1.0. So there is a real cost advantage of about
   30%, but it falls short of the pre-registered 40% threshold. This does not change
   the verdict above.
2. **Every cell was 120/120 correct with 0 silent failures: a ceiling effect.** The
   tasks can't distinguish the arms on correctness or safety at this model strength,
   which echoes the earlier API rounds and the confirmation run above.
3. **Programs: B is about 12 lines vs G's about 80** (S2: Sonnet 11.6 vs 77.9, Haiku
   11.9 vs 83.8 — roughly 6–7× fewer lines), and about 4× fewer AST nodes (Sonnet 116
   vs 450, Haiku 115 vs 472). Output tokens on Sonnet: G is about 2–3× B (1071 vs 370,
   computed from S2). The cost gap is smaller than the size gap. **Correction: the
   frozen spec's threat line ("B's system prompt is much larger") is wrong, and so was
   this write-up's first draft.** `api_go_system_prompt` (G) is 21,303 chars against
   `api_lattice_system_prompt` (B)'s 17,319 — G's is the larger one
   (`bench/offline/go_pilot/ESTIMATE.md`). This shows up in the data too: the first
   (uncached) call of each arm ran 10,383 input tokens for G vs 8,405 for B on Sonnet,
   and 7,938 vs 6,468 on Haiku. The real reason the cost gap is smaller than the size
   gap is that **input dominates B's bill**: on Sonnet, output tokens are only 28% of
   B's total cost against 57% of G's (fit from `outcomes.jsonl`'s per-token cost
   components; see observation 4), and B re-sends its (smaller but still substantial)
   input on every repair round, which G rarely needs. Per-call cost fell sharply as
   prompt caching engaged, but the "$0.05 early, $0.015–0.02 later" pattern from an
   earlier draft of this section fits **Sonnet G only**; Sonnet B's per-call cost went
   from about $0.035 on its first two (cold) calls to about $0.008–0.013 on later
   single-round calls (repaired runs run higher, up to ~$0.019, from the extra round's
   tokens).
4. **Prompt-caching caveat.** Fitting `cost_usd` against `(uncached, cache_write,
   cache_read, output)` tokens recovers exactly Sonnet $2 / $4 / $0.20 / $10 per Mtok
   and Haiku $1 / $2 / $0.10 / $5 (zero residual). Recomputing every run's cost at the
   uncached list price (no cache write or read discount) drops the cost advantage:
   Sonnet R = 0.806, Haiku R = 0.645. Caching helps B more than G because B's cost is
   more input-heavy (observation 3), so **the ~30% advantage reported above holds
   under this harness's warm-cache batching; run cold, it is closer to ~20%.** The
   batching also matters at the start: each model's runs went arm G fully, then arm B
   (G ran before B in the file), so B's cache did not inherit any warmth from G — the
   two arms' system prompts differ, so B's own first record(s) still show
   `cache_read_tokens == 0` (2 of Sonnet-B's 120, 1 of Haiku-B's 120, both the
   earliest records for that arm). Normalizing those handful of cold-start records to
   what a warm call of the same size would have cost moves Sonnet R to ≈0.71 and Haiku
   R to ≈0.60 (close to the review's reproduced 0.706 / 0.599). **STOP is robust to
   all three framings** (actual, uncached, cold-normalized): every one is well above
   the pre-registered ≤ 0.6 threshold on Sonnet.
5. **Checker-repair cost, not pre-registered.** On Sonnet, 31/120 B runs needed at
   least one repair round; the checker codes seen before the final (passing) round,
   summed over those 31 runs, were L821×9, L802×8, L205×7, L801×6, L002×4 (plus a few
   singles/doubles). Repaired runs averaged $0.023 per run against $0.0096 for the 89
   single-round runs, and make up 46% of B's total Sonnet cost. If every B run had
   cost what a single-round run costs on average (i.e., no repair cost at all), R
   would be ≈0.51 — comfortably under the 0.6 threshold. G needed exactly one repair,
   for a `go build` error (task c04). Caveat: this is not a controlled comparison —
   the 31 repaired tasks may simply be the harder ones, on both arms, not tasks where
   B specifically struggles.
6. **Threats that applied** (from the spec): the tasks were written for Lattice; P2
   was relaxed before the freeze; one machine and one CLI version; the estimate
   predicted about $21 (central scenario) — the actual total cost, summed over all 480
   records in `results/go-pilot/outcomes.jsonl`, was **$8.81**. The spec's own threat
   about B's system prompt size was also wrong (observation 3).

### Conclusion

Per the pre-registration, stage 1 is STOP: there is no stage 2. The Go target's
demonstrated value remains what 5a proved — verified multi-target compilation, tested
equivalent to the Python runtime on 2,478 cases — not a demonstrated LLM-cost win
beyond about 30%, which falls short of the pre-registered bar. As with the confirmation
run above, a real but modest effect did not clear the pre-registered threshold, and the
tasks again hit a ceiling on correctness; no further Go benchmark investment is
warranted on this evidence.

## Roadmap Phase 0: spec v0 and the four-layer checker on Python (2026-10-04)

Plan: `docs/superpowers/plans/2026-10-04-phase0-spec-checker.md` (branch `roadmap`).
Built: the spec model (`lattice/spec`: machines, invariants compiled to SQL, scenarios,
unchecked requirements, validation S001-S016), pycheck PY808 (state machines), nested
response models in the language (`Nested`, L835; Go gated, TS supported), the check
engine (layers A static, B endpoint answers vs the spec runtime, C invariants after
every request, D scenarios) with its CLI, accepted baselines and spec-approved findings,
and the multi-vendor spec plus spec deltas for t31-t44. No LLM calls; no cost.

**Exit 1, the spec checks clean against the original app: PASS.** 0 findings on A-D,
15 accepted as the app's baseline, each with its reason (upstream has no length/upper
bounds: 8 PY803 + 5 layer-B cases; a negative `limit` accepted; the PY807 partial write
in `POST /orders`). The check found one bug in the spec itself (a 255-character
password bound, beyond bcrypt's 72 bytes, crashed the spec's own program) and one in
the checker (a public table linked to an owned one was treated as owned).

**Exit 2, every trap caught: PASS** (`python -m bench.specs.exit`,
`bench/specs/phase0-exit.json`). All 14 references pass against spec + delta.

| run | traps caught | any layer: A / B / C / D | only that layer: A / D |
|---|---|---|---|
| blind (deltas from ticket text only) | **44/50** | 12 / 4 / 1 / 41 | 3 / 27 |
| after the six additions | 50/50 | 9 / 4 / 2 / 47 | 3 / 35 |

The blind run (`phase0-exit-blind.json`, also before the order programs and the t33
approval) is the honest number for "a spec written from the ticket". It missed six
requirements the ticket texts state but its scenarios did not exercise: a delisted
product's lines (t35), another buyer's non-cancellable order (t36), a product listed
twice (t39), another buyer's non-pending order (t40), moved lines keeping their price
(t42), names differing only in case (t43). Those were added afterwards, marked in
`multivendor_tickets.py`. The t33 reference also failed blind on PY801: the ticket asks
for a public aggregate over every buyer's orders, now stated as a spec approval.

Observations:
1. **Scenarios (D) carry most of the catching**: 27/44 blind traps were caught by D
   alone. B (endpoint programs) adds little here because 10 of 14 tickets have no
   program: Lattice still lacks per-row write loops (L841), list bodies, and non-literal
   error details (L404), so their endpoints are checked by scenarios only.
2. **Static checks (A) are the only catch for atomicity**: the three A-only traps are
   all PY807 partial writes (t38, t39, t44): no single-client scenario sees them.
3. **The blind miss rate (6/50) is a spec-authoring cost**: catching depends on the
   spec stating the edge case. That is the lever Phase 1 measures.

Order endpoints now with programs: GET /orders/me, GET /orders/{id} (nested items).
Still unchecked by B, with the missing construct named in the spec: place_order,
update_order_status, flash-sale, and the t36/t38/t40/t42/t44 endpoints.

## Roadmap Phase 2: importer frozen before the held-out run (2026-10-05)

The importer, layer R and the exit script are frozen at commit **`1cda54a`**. This was
recorded before any held-out app (H1 drf_blog_api, H2 project-management-api,
H3 conduit-drf, reserve flask-realworld-example-app) was vendored.

Dev-app gate at the freeze (`tests/importer/test_phase2_dev_apps.py`): per app, tables /
routes verified, against the ≥ 90% bar.

| app | framework | tables | routes | fidelity |
|---|---|---|---|---|
| template | FastAPI | 2/2 | 23/23 | pass |
| taskflow | FastAPI | 2/2 | 10/10 | pass |
| multivendor | FastAPI | 5/5 | 17/17 | pass |
| snippets | Django + DRF | 2/2 | 9/9 | pass |
| recipes | Django + DRF | 6/6 | 16/16 | pass |
| microblog | Flask + Alchemical | 4/4 | 25/27 (2 OAuth2 routes need the network) | pass |
| flasky | Flask + SQLAlchemy | 3/3 (2 outside the API) | 12/12 | pass |
| justnotes | FastAPI | 6/6 | **22/30, below the bar** (7 routes call a local LLM server, 1 shadowed upstream) | pass |

The held-out run's numbers will be **the** Phase 2 result. Fixes made after seeing them go
into a separately labelled post-hoc run.

## Roadmap Phase 1: the spec loop on Python (pre-registered, 2026-10-04/05)

Spec: `docs/superpowers/specs/2026-10-04-phase1-loop-experiment.md`, frozen in `7574876`
before any run. Every run went through the CLI, plan-billed (no API key).

**Arms** (t31–t44 multi-vendor, 6 reps each):
- **P:** Python from the ticket.
- **Q:** Python plus pycheck feedback.
- **S:** the model drafts a spec delta, the owner's approval is simulated against the
  reference, then Python is checked on layers A–D and repaired.
- **S\*:** as S, but with the owner's blind delta.

**Protocol incidents, both handled before any analysis:**
1. **Code drift.** The first S run read code from a working tree that changed under
   it, at 19:38. Its 21 records were set aside (`phase1_sonnet-5_S.discarded-codedrift.jsonl`).
   P and Q had finished at 19:34. S, S\* and both Haiku arms were then rerun from a
   checkout pinned to `7574876`.
2. **Usage limit.** The plan's usage limit stopped Haiku S after 11 runs. It resumed
   the next morning with `--resume`.
   - One Haiku S run (t38 rep 0) is a harness error, which counts as loud. The approval
     step crashed on a draft whose `given` rows collided with seed ids (`IntegrityError`).
     The pre-registered rule redid it on resume, and it crashed the same way. Analysis
     keeps the last record per run.

### Outcomes as pre-registered

| model | arm | correct | silent | loud | $/correct | rounds | s/run |
|---|---|---|---|---|---|---|---|
| Sonnet | P | 67/84 | 16/84 | 1 | 0.128 | 1.07 | 32 |
| Sonnet | Q | 67/84 | 15/84 | 2 | 0.139 | 1.19 | 34 |
| Sonnet | S | 74/84 | **9/84** | 1 | 0.288 | 1.11 | 95 |
| Sonnet | S\* | **80/84** | **0/84** | 4 | **0.121** | 1.17 | 51 |
| Haiku | P | 23/84 | 59/84 | 2 | 0.223 | 1.35 | 67 |
| Haiku | S | **47/84** | **29/84** | 8 | 0.285 | 2.20 | 194 |

Spec error rate (rejected / drafted rules): Sonnet S **5.1%** (23/453), Haiku S
**20.2%** (117/579). Both are under the 25% bar, and no draft failed validation.

### Pre-registered verdicts

| test | verdict (pre-registered oracle) | Fisher p | after the oracle review |
|---|---|---|---|
| **H1** Sonnet, S vs P | **NOT CONFIRMED** (fewer silent fails) | 0.096 | NOT CONFIRMED, p = 0.071 |
| **H2** Sonnet, S vs Q | **NOT CONFIRMED** | 0.135 | CONFIRMED, p = 0.047 (post hoc) |
| **H3** Sonnet, S\* vs S | descriptive: S\* 0 silent, 80 correct, cheapest per correct change | — | unchanged |
| **H4** Haiku, S vs P | **CONFIRMED** (sign test 10–1, guard 56% vs 27%, spec bar 20.2%) | < 0.0001 | CONFIRMED (silent 25 vs 54) |

### Oracle review (`docs/results/2026-10-05-phase1-oracle-review-sonnet.md`)

All 40 Sonnet silent runs reproduce; 33 are genuine.
- **The 7 oracle defects** are all on t36. The candidate refused with 403 and left the
  database unchanged, as the reference does; only the message differed. The ticket states
  no message for that refusal.
- **Correction** (`bench/edit_slice/repos/multivendor.py`, STATUS_ONLY): t36/t38
  refusals and unknown orders are compared on status and effects only. The 166
  option-1 oracle tests still pass.
- **Rescore:** offline, every arm and both models, with no new model calls.
  - Sonnet P 16 → 13, Q 15 → 14, S 9 → 6, S\* 0 → 0.
  - Haiku P 59 → 54, S 29 → 25.
- The H2 flip comes from a post-hoc correction with p just under 0.05, so the
  pre-registered verdict is the headline.

**Genuine failure mechanisms (Sonnet):**

| mechanism | P | Q | S | S\* |
|---|---|---|---|---|
| partial write (t38) | 6 | 0 | 0 | 0 |
| price 0 accepted with no bound (t39, t43) | 7 | 0 | 0 | 0 |
| price bound written `ge=0` instead of `gt=0` | 0 | 10 | 5 | 0 |
| `version` missing from a response | 0 | 0 | 2 | 0 |
| stated message replaced by a role-check message | 0 | 3 | 1 | 0 |

**What the drafts missed:** in every S silent run, the candidate passed its own approved
spec. The draft simply did not state the failing rule: a zero-price scenario, or a
`version` field in a subset body. In the 4 S runs whose draft did test price 0, the
candidate got the bound right.

### Observations
1. **When the spec states the rule, the loop catches it.**
   - S\* had no silent runs out of 84 (P had 16), at the lowest cost per correct change.
   - The remaining weakness is spec authoring: the model's own drafts leave edge cases
     unstated (Sonnet S: 9 silent, all rules its draft never stated).
   - This matches Phase 0's 44/50 blind number.
2. **The loop helps the weaker model most.** Haiku's silent runs halve (59 → 29) and its
   correct runs double (23 → 47). That is the strongest pre-registered result in the
   project so far.
3. **On Sonnet the effect is in the right direction but small** (16 → 9). Q's static checks
   alone did not help overall: they removed the partial writes, but bounds came out as
   `ge=0`.
4. **Catches by layer:** on Sonnet, every repair fed back came from scenarios (D). On
   Haiku, all four layers fed back: A 5, B 51, C 47, D 76 repair rounds.
5. **Leftover check findings on correct runs:** 15 Haiku S runs the oracle scores correct
   end with check findings still open (94 in all). There are none on Sonnet. In these
   runs the draft states details the oracle doesn't compare, such as exact messages the
   reference gives. The checker held the model to its own spec, which costs extra rounds:
   Haiku S averaged 2.20 rounds and 194 s per run.
6. **Cost:** S costs about 2.3× P per correct change on Sonnet, because of the spec step and
   larger prompts. S\* costs the same as P.
7. **Threats that applied:**
   - simulated approval against the reference (optimistic);
   - one app and its tickets, with the base spec reviewed on them;
   - the code-drift restart;
   - the Haiku harness error;
   - timing (s/run) was measured while other work ran on the machine.

## Roadmap Phase 2: held-out result (2026-10-05) — exit NOT met

The one frozen run per app (importer `1cda54a`) is recorded in `bench/heldout/RESULTS.md`.
A fresh agent vendored the apps (`bench/import_repos/{drf_blog,projmgmt,conduit}`), ran
each import once, then reviewed the reports by hand.

| app | framework | tables | routes | fidelity | verdict |
|---|---|---|---|---|---|
| H1 drf_blog | Django + DRF | crash | crash | not run | **fail** |
| H2 projmgmt | Flask + SQLAlchemy | 1/3 | 23/23 by the script (review: 8 of 23 R1 verdicts right) | fail | **fail** |
| H3 conduit | Django + DRF | 1/1 by the script (review: 1/6) | 24/26 by the script (review: 21/26) | fail | **fail** |

**What failed:**
1. **H1 crashed in the importer's seeder.** It inserted `blog_blog` rows before the custom
   users table they reference (`django_orm.py:341`, a seed-order bug).
2. **The verifier overstated (soundness bugs).**
   - On H2 the importer found no login (a JSON login, missed). It then ran with no
     callers, and still marked 23 `Public` R1 contracts "verified", although 15
     anonymous probes answered 401.
   - On H3 the front end missed ORM calls inside plain `ViewSet` methods. It then
     dropped 5 used tables as "outside the API", which inflated tables to 1/1.
   - On H3, wrong-credential answers on the public token routes were counted as
     refusals.
3. **Missed facts:**
   - H2's JSON login and its manager/employee role enum;
   - the enum's stored spelling in the importer's own seed;
   - H3's owner-or-staff permissions, which were corrected to admin-only.
4. **What it got right:** 10 of 13 H3 corrections, including all 5 upstream 500s; 21
   correct R1 verdicts on H3; 8 of 9 mined proposals true.
5. **Contamination check:** the importer had a test written "for drf_blog_api's kind" of
   login before the freeze (W3). It didn't help, because H1 crashed first. No other
   held-out identifier appears in the code.

**What it says:** the dev-app gate (7 of 8) did not carry over to unseen apps. The most
serious finding is that the verifier can report "verified" when its own probes contradict
it. That has to be fixed before any number from layer R is trusted. Per the plan, fixes
from here on are a separately labelled post-hoc run, and a fresh held-out app is needed
for the exit to count.

### Phase 2 post-hoc soundness rounds (2026-10-05). POST-HOC: these do not count for the exit

Round 1 (`79991f1`) and round 2 (`cad36e5`) made "verified" something a probe has to earn:
- R1 needs decided callers;
- an ambiguous 403 is no evidence;
- refusals and "reaches" need a witness (the same request from another caller) or a
  control (the same request with plain valid values);
- error envelopes inside a 2xx, and writes with no database effect, count as nothing;
- `check_verdicts` raises on any "verified" item without a decisive probe.

The importer also changed: foreign-key seed order, JSON logins, stored enum spellings,
valid slugs, emails and URLs, and following DRF ORM calls. It gained the
`owner_or_admin` scope.

| app (POST-HOC) | frozen run | round 1 | round 2 |
|---|---|---|---|
| H1 drf_blog | crash | tables 1/6, routes 17/30 | tables 2/6, routes 15/30 |
| H2 projmgmt | tables 1/3, routes 23/23 (overstated) | 2/3, 20/23 | 2/3, 16/23 |
| H3 conduit | tables 1/1, routes 24/26 (overstated) | 4/6, 25/26 | 3/6, 18/26 |

A spot check of 30 random "verified" items across the three apps found all 30 right.
A full read of every verified R1, R2 and T item found no wrong verdict. Most of the
random sample were weak R3 "no unstated 5xx" items.

The dev-app gate still passes: 7 of 8 apps. multivendor dropped from 17/17 to 16/17
routes, because no witness exists for the R2 check on `PATCH /products/{id}`.

Lesson: the frozen run's high numbers on H2 and H3 came from an unsound verifier.
Under sound rules, the honest share of an unseen app's rules this importer verifies
is about 50–70% of routes.

## Roadmap Phase 2: second held-out run (reserve app), importer frozen at `cad36e5`

The importer is frozen at `cad36e5` before the reserve app
(flask-realworld-example-app, never opened) is vendored. Its one run is the Phase 2 exit
attempt; anything after it is post-hoc.

**Reserve result** (`bench/heldout/reserve/RESULTS.md`): one frozen run, no crash, 1.1 s.
**Exit NOT met.**

| | by the exit script | on review |
|---|---|---|
| tables verified | 1/5 (3 excluded as outside the API) | 0/8 |
| routes verified | 0/19 | 0/19 |
| fidelity | fail | — |

- **No logged-in caller.** The importer found no login, so layer R ran with no
  logged-in users. The app reads bodies through flask-apispec/webargs `{"user": {...}}`
  envelopes, which the Flask front end does not read. It has no flask-jwt-extended
  minter and doesn't handle the `Token` scheme.
- **Vendoring confound.** A compatibility shim re-exported the JWT decorators from
  a Lattice module, which hid them from the front end (it matches them by import
  origin). As a result, the 12 login-required routes were predicted Public and refuted.
  The reviewer judges the app would fail without the shim too, but that is unmeasured.
- **"Verified" precision on review: 7 of 8.** The wrong item is `T1 Article`, "shared",
  though only the author may edit or delete.
- **"Outside the API" was wrong again.** 3 link tables were written through ORM
  relationships the front end does not see. The completeness veto from post-hoc round 1
  did not catch this, because relationship writes are not counted as unread evidence.
- **Contamination check:** no identifiers of this app in the importer. H3 conduit
  implements the same RealWorld spec family, and the post-hoc rounds were made after
  seeing H3; it gave no visible benefit (0/19).

### Phase 2 conclusion

The exit was not met on four held-out apps across two frozen runs. Under sound
verification, the importer verifies roughly half of an unseen app's rules after
post-hoc fixes, and close to none on an app whose login it cannot find. The dev-app
gate (7/8) measured fit to the apps it was built on.

Two verifier soundness problems remain open:
1. "Shared" T1 verdicts can rest on reads alone.
2. Relationship writes escape the outside-API veto.

These need fixing before any layer R number is trusted.

## Experiment D: does a drafting checklist close the spec-authoring gap? (pre-registered, 2026-10-07)

Spec: `docs/superpowers/specs/2026-10-07-experiment-d-spec-authoring.md`, frozen in
`647ba94` before any run. 432 runs through the CLI on the user's plan; dollar figures are
list-price equivalents only. Held-out tickets t45–t56, written after Phase 1. The arms:
- **S**: the model drafts the spec;
- **S+**: S plus a generic checklist and one self-review;
- **S\***: the owner's blind spec, which catches 45/52 traps offline.

The run paused twice at the plan's usage limit and resumed with `--resume`.

| model | arm | correct | silent | loud | spec error rate | $/correct | s/run |
|---|---|---|---|---|---|---|---|
| Sonnet | S | 66/72 | 6 | 0 | 7.4% | 0.258 | 148 |
| Sonnet | S+ | 66/72 | 6 | 0 | 7.9% | 0.498 | 378 |
| Sonnet | S\* | 65/72 | 7 | 0 | — | 0.150 | 94 |
| Haiku | S | 61/72 | 10 | 1 | 10.0% | 0.170 | 177 |
| Haiku | S+ | 61/72 | 9 | 2 | 5.7% | 0.233 | 244 |
| Haiku | S\* | 65/72 | 6 | 1 | — | 0.106 | 112 |

### Pre-registered verdicts
- **H1′ (Sonnet, S+ vs S): NOT CONFIRMED.** Silent 6 vs 6, p = 0.62. The guard and the
  spec bar held.
- **H2′ (Haiku, S+ vs S): NOT CONFIRMED.** Silent 9 vs 10, p = 0.50. The guard and the
  spec bar held.
- **H3′ (descriptive):** on Sonnet, S\* is no better than S or S+ (7 silent); on Haiku,
  S\* is best (6 silent, 65 correct). S\* is the cheapest on both.

### Oracle review (`docs/results/2026-10-08-experiment-d-oracle-review.md`)
**43 of the 44 silent runs are genuine.** The one defect:
- **C1 (fixed after the run, `bench/edit_slice/repos/multivendor.py` `mint`):** minted
  test tokens expired after 30 minutes inside a long oracle process. A reference saw a
  live token while its candidate saw an expired one.
- **Effect:** rescored offline, it flips exactly one run, Sonnet S t52 rep 4, from silent
  to correct. Sonnet S becomes 67/5, and both verdicts stand.

**C2 (latent, recorded, not applied):** `t50.repeated_id` accepts only the "collapse the
repeat" reading. The text also allows "refuse with 400". No run's outcome depends on it:
every t50 failure double-restocks, which is wrong under both readings.

**32 of the 44 silent runs, on both models and in every arm, are one bug: t50**
(`POST /orders/cancel-batch` with a repeated id). The candidate cancels and restocks once
per *listing*, so `[7, 7]` returns 2 mugs to stock for an order holding 1. The ticket
text rules that out under any reading.
- No spec in any arm, the owner's included, stated the repeated-id case as a scenario.
  The drafts deferred it to `Unchecked` because the ticket leaves the answer open, and
  the owner did the same.
- One step, "list `[7, 7]`, then assert mug stock is 41", asserting the effect and not
  the status, would have caught every t50 run under either reading.

### Why the checklist didn't help
1. **The self-review mostly failed validation.** It failed in 42/72 Sonnet runs and
   21/71 Haiku runs, so those runs fell back to the first draft.
   - **The cause:** 60 of the 63 failures carry S016. The review named the new endpoint
     (`"METHOD /path"`) as an `Unchecked` concern, but these deltas have no
     program or contract for it.
   - **Who caused it:** this is a harness design defect, not the model's. The review step
     had no repair round, and the validator's hint invites exactly that string.
   - **Valid reviews would not have changed t50,** because the revisions deferred
     repeats the same way.
2. **The checklist told the model to defer ambiguity** ("write an `Unchecked` entry
   rather than guessing"). That is the right rule for the *answer*. But the drafts then
   also skipped the *effect* that every reading shares. Where Haiku's S+ drafts did write
   the edge scenarios (t47's empty list and 0 quantity), every candidate passed them.

### Observations
1. **Spec authoring is still the bottleneck, and it's sharper than "edge cases":** the
   misses are cases where the ticket leaves the response open but fixes the effect. Specs
   (model or owner) skip them entirely.
   - **A concrete next aid:** "for each edge case the ticket leaves open, still assert the
     database effect that every reading shares".
   - **And a harness fix:** allow a delta's new endpoints as `Unchecked` concerns, and
     give the review one repair round.
2. **The loop works when the spec states the rule.** S\* is 0 silent on Haiku's t47/t52
   and on every ticket except t50.
3. **A checklist costs a lot:** about 2× list-price per correct change on Sonnet and
   1.4× on Haiku, with no measured gain.
4. **Threats:** the same app and base spec as Phase 1; the simulated approval is
   optimistic; one ticket dominates the outcome. With 12 tickets, a single hard ticket
   swamps the arm differences.

## Real app, built spec-first, and layer X (concurrency), 2026-10-08

**The app:** a job scheduler (`~/Documents/job-scheduler`), built from scratch against an
owner-approved spec. It has 5 tickets plus 2 follow-ups, one Claude Code agent (Sonnet) per
ticket, and each ticket was gated on `./check.sh N`.

**The comparison:** a baseline (`~/Documents/job-scheduler-baseline`) built from a prose
requirements document by the same model, with no spec and no checker. The spec was then used as
the judge of both. Full write-up: `job-scheduler/metrics/COMPARISON.md`.

**Stated rules:**
- Both builds passed all 129 scenario and invariant cases.
- Prose was as good as executable scenarios for rules a precise document states.

**Concurrency:**
- The checker (layers A–D) could not see races; it runs one request at a time.
- A hand-written probe found the baseline breaking both "at most one" rules (7 live runs, 5 cron
  runs).

**Layer X** (`lattice/spec/race.py`, `3cd8b11`) races the spec's own scenario writes in parallel,
then checks the count-bound invariants and the state machines. It never reports "verified".
Validation:
- **Baseline:** both race bugs caught.
- **A mutant** (the spec-first build with its live-run index removed): caught.
- **The Phase 0 multi-vendor app:** a real, unknown 500 under parallel vendor registration
  (reproduced by hand), and no false alarm.
- **The spec-first build:** 3 real bugs that code review and the hand probes had missed:
  - concurrent duplicates answer 500;
  - two versions end up active;
  - a cancel racing the worker's finish overwrites the result.

  Ticket 6 fixed them, gated with X: it passed 3 times in a row.

**Also found:**
- Both builds failed bcrypt's 72-byte limit; the spec hadn't stated it. Now it does.
- Owner decision: cancelling a running run stops its process.

**Cost:** the spec-first agents used about 1.11 M tokens in all, follow-ups included; the
baseline used 0.55 M. Spec-first adds 764 owner-reviewed spec lines; the baseline writes 4× more
of its own test code.

**What it says:** a spec's invariants are what make concurrency checking automatic. Layer X is the
first checker capability that beat both a careful prose-driven build and human review on a real
app.

## Cost reduction: Haiku under the spec gate (2026-10-08)

A third job-scheduler build used Haiku, with the cost levers applied:
- every owner decision resolved up front;
- layer X in the gate from ticket 1;
- gate output only, never the checker's code;
- per-ticket spec slices;
- a frozen Lattice dependency.

**Result:** it passes the final judge with 0 findings on 141 cases (ABCDX), and the race probes
held 4/4.
- It used **~588k Haiku tokens**, against ~1.0M Sonnet tokens for the Sonnet spec-first build to
  reach the same state.
- At about ⅓ the list price per token, that is about **a fifth of the cost** of the Sonnet
  spec-first build.
- It is cheaper than the Sonnet prose baseline, which still fails 11 checks.

**The trade-off is supervision.** There were 4 send-backs, all on instructions nothing enforced:
a lint flag bypass, missing tests, an incomplete log, and one run per drain (that one a spec gap;
the rule has since been added). The orchestrator also diagnosed one zombie-process bug by hand.

Two new tools made this possible:
- `lattice.spec.access_matrix` and `requirements_markdown`, which cut spec authoring (`7238b30`);
- layer X (`3cd8b11`).

Details: `job-scheduler/metrics/COMPARISON.md`.

## Model ladder on the job scheduler (2026-10-09)

The same spec, the same gate (ABCDX) and the same per-ticket briefs, one build per model.

- **Every spec-first build passed the final judge** (141 cases plus X, race probes 4/4): Opus,
  Fable, Haiku and Sonnet.
- **Opus** got there in 331k tokens, 58 tool calls and 17 minutes, almost every ticket in one round,
  with no supervision.
- **Fable:** 457k tokens, 31 minutes, no supervision.
- **Haiku:** ~588k tokens, 110 minutes, 4 send-backs.
- **Sonnet:** 749k tokens plus follow-ups.
- **The Sonnet prose baseline** failed the judge with 11 findings.

**Conclusion:** the spec gate fixes the quality; the model sets the effort and the supervision.
Opus is the cheapest in tokens and time, and Haiku is likely the cheapest in dollars (its price is
about ⅓ of Sonnet's per token).

Details: `job-scheduler/metrics/COMPARISON.md`.
