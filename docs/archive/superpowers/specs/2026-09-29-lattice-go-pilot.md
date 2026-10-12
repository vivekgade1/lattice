# Sub-project 5b, stage 1: Go cost pilot (pre-registered)

> Status: **PRE-REGISTERED 2026-09-29, frozen** at the commit that adds this line (parent 4b078d8), immediately before the live run. The user approved the freeze after being shown Revisions 1–3, the dry run and the cost estimate. No code or rule may change for this run after this commit. Live command: `python -m bench.cli run --suite api --arms G,B --lattice-backend go --models claude-sonnet-5,claude-haiku-4-5 --reps 3 --workers 4 --out results/go-pilot`, with `--resume` after any stop. It follows the Go target equivalence gate (5a: Lattice→Go equals the Python runtime on 49 programs and 2,478 cases; `docs/results/2026-09-26-bench-smoke.md`, "Go target — equivalence gate") and the confirmation run ("Confirmation run", 2026-09-29), which refuted "Lattice reduces silent bugs" for edits on a strong model. Design context: `docs/archive/superpowers/specs/2026-09-28-lattice-go-target-design.md` §5.

## Why a pilot, and what it can still show

The confirmation run tested **edits to an existing Python app** and found no safety advantage for Lattice. This pilot tests the one open claim from earlier evidence that the confirmation run did not touch: on **greenfield API tasks**, Lattice programs are much shorter than the hand-written equivalent (API runs: 0.52–0.56× AST size and 0.76–0.84× cost per correct task vs FastAPI on Sonnet/Opus). When the hand-written target is Go, which is more verbose than FastAPI, that gap could be larger. The pilot measures only that: whether writing Lattice compiled to Go is **substantially cheaper per correct task** than writing Go directly, without losing correctness. It makes no safety claim.

## Questions

- **P1 (primary):** on claude-sonnet-5, is the cost per correct task of arm B (Lattice→Go) at most **0.6×** that of arm G (Go written directly)?
- **P2 (co-primary, non-inferiority):** on claude-sonnet-5, over runs counted after harness exclusion, is arm B's correct rate no more than **10 points** below arm G's, at a one-sided 95% bound? (Margin and confidence both revised before any run; see "Revisions before freeze" below.)
- **S1 (secondary):** the same two measurements on claude-haiku-4-5.
- **S2 (secondary, descriptive):** silent failures per arm; program size (lines, AST nodes); repair rounds; output tokens.

## Scope

- **Tasks:** the existing 40 API tasks (`bench/api_tasks.py`), explicit condition, with hints, exactly as in the API runs. These are greenfield: the model writes the endpoints from the ticket against a given schema.
- **Arms:**
  - **G:** the model writes `handlers.go` against a Go platform kit that only authenticates (the Go counterpart of `bench/api_kit/platform_kit.py`: opens the DB through the counting wrapper, finds the caller from the bearer token, gates by role). Ownership, validation, errors and pagination are the model's job. Repair budget 2, with feedback from `go build` / `go vet` output plus one smoke request.
  - **B:** the model writes Lattice (the existing API prompt), compiled with `build_go_app`. Repair budget 2, with feedback from checker diagnostics plus one smoke request.
  - Both arms are scored by the same hidden cases (`bench.api_runner` semantics), run against the built binary through the Go proxy (`lattice/gogen/proxy.py`), with SQL counts from `X-Lattice-Sql-Count`.
- **Models:** claude-sonnet-5 (primary), claude-haiku-4-5 (secondary).
- **Reps:** 3 per task per arm per model, which gives 120 runs per cell.
- **Total:** 40 × 2 arms × 2 models × 3 = **480 runs**.
- **Transport:** the Claude CLI only (project policy since 9f1f857), with the existing isolation flags; resumable runs; cost from the CLI's list-price estimate (`cost_basis = "cli_list_estimate"`), the same for both arms.
- **Code:** frozen at the commit that registers this spec, plus the harness commits listed in the 5b stage-1 plan. Any later language or harness change voids the run.

## Tests and decision (fixed now)

- **Unit:** a run, scored correct/silent/loud by the hidden cases, as in every API round.
- **Model identity:** every outcome record's "cell" (arm × model, for grouping and for P1/P2) is keyed by the REQUESTED model -- the CLI's `--model` argument -- never by the served model, which a repair loop's retries or a fallback can change mid-run. The served model is recorded separately (`model`, comma-joined over the run's calls) for reporting only.
- **Harness dedup and exclusion:** `--resume` retries a harness-error run; the analysis keeps the LAST record per (requested model, arm, task, rep) -- a later attempt supersedes an earlier one. Any harness errors still present after this dedup are EXCLUDED from both cost and n for P1 and P2 (but their count and rate are reported). If harness errors exceed 5% of either Sonnet cell (of the expected 40 × 3 = 120 runs), the stage-1 verdict is **INVALID** (neither GO nor STOP) and must be investigated before any conclusion is drawn. A failure of the Lattice→Go pipeline (lowering, emission or `go build`) on a program that already passed the checker is never a harness error, even though it is a gogen bug rather than the model's fault: it is scored as a loud, final failure of arm B (counted in cost and n), because excluding it like a harness error would silently drop a real B failure and bias the cost comparison toward B (see "Revisions before freeze" #3). Only genuine harness infrastructure failures -- the Go toolchain missing or too old, a CLI transport error, a disk error before any model output -- count as harness errors.
- **Completeness:** after dedup and harness exclusion, each Sonnet cell (arm B, arm G) must correspond to exactly 40 tasks × 3 reps of raw (deduped) runs; a missing run, or an unexpected duplicate that survives dedup, fails the analysis loudly rather than silently scoring a partial cell.
- **cost_basis:** every counted (non-harness) Sonnet run must have `cost_basis == "cli_list_estimate"`; any other value fails the analysis loudly.
- **Cost per correct task (per arm and model):** total CLI cost of the arm's counted runs ÷ number of correct runs.
- **P1:** the ratio R = cost/correct(B) ÷ cost/correct(G) on Sonnet, with a 95% percentile bootstrap interval (10,000 resamples of tasks, keeping each task's runs together). The number of resamples dropped for an undefined ratio (an arm with zero correct runs in that resample) is reported.
  - **Confirmed** if R ≤ 0.6 AND the interval's upper bound < 1.0.
  - **Refuted** if R ≥ 1.0.
  - Otherwise, **not confirmed** (this includes R ≤ 0.6 with an upper bound ≥ 1.0: a good point estimate on too wide an interval to trust).
- **P2:** a one-sided non-inferiority test on the correct rates (Sonnet), margin 10 points at a one-sided 95% bound: the lower limit of the **two-sided 90%** Newcombe-Wilson interval for correct(B) − correct(G) must be > −0.10.
- **Stage-1 verdict:**
  - **INVALID:** the harness-error-rate check above fires for either Sonnet cell. Investigate before drawing any conclusion; do not treat as GO or STOP.
  - **GO:** (not INVALID and) P1 confirmed AND P2 holds. Proceed to stage 2 (the rest of spec §5: the implicit-requirements condition, the Go-specific trap tasks, the T2 script control, and binary latency/throughput under load), with its own pre-registration.
  - **STOP:** anything else. Write up the result and do not run stage 2.
- **Also reported:** correct, silent and loud rates with Wilson 95% intervals (loud includes a harness-error note); S1/S2 measurements; per-family breakdown (control, ownership, validation, errors, efficiency).

## Revisions before freeze

### Revision 1: P2's margin and confidence (2026-09-29)

Controller ruling, made before this pre-registration froze (the Task 8 freeze commit) and before any live run: P2 as originally stated combined two conservative choices that, together, made the test very hard to pass at this pilot's sample size (n = 120 runs/arm/model) even for arms with **identical** correct rates:

- a margin of 5 points, and
- a **two-sided 95%** Newcombe-Wilson interval (equivalently, a one-sided **97.5%** bound).

The Newcombe-Wilson lower bound for a true tie's difference at n = 120/arm, by confidence level:

| tied correct rate | two-sided 95% (one-sided 97.5%) | two-sided 90% (one-sided 95%) |
|---|---:|---:|
| 0.95 | −0.061 | −0.050 |
| 0.90 | −0.079 | −0.065 |
| 0.85 | −0.092 | −0.077 |
| 0.80 | −0.101 | −0.085 |

Under the original rule (margin 5, one-sided 97.5%), a true tie fails at every plausible correct rate shown above -- but this does not mean P2 could *never* hold: it means P2 could hold only if arm B's observed correct rate exceeded arm G's by enough to overcome that asymmetry, i.e. B needed to look measurably *better* than G, not merely equal, for the test to pass. That is a materially harder bar than a standard non-inferiority test is meant to set, so both choices are revised together, before any run: **margin 10 points, at a one-sided 95% bound** (the lower limit of the two-sided 90% Newcombe-Wilson interval). Under the revised rule, a true tie passes at every correct rate shown above. This is reflected in Questions and Tests and decision, above, and in `bench/analysis/go_pilot.py` (`P2_MARGIN`, `P2_Z`).

### Revision 2: model identity, harness handling, completeness, cost_basis (2026-09-29)

Controller ruling, made before the freeze and before any live run, in response to review findings on the analysis script:

- **Model identity.** An outcome record's served model (which call actually answered) can differ from the model the run requested, e.g. across a repair loop's retries. The cell key for every grouping (and so for P1/P2) is the REQUESTED model; the served model is recorded separately, for reporting only. The API bench's CLI-transport work must write a `requested_model` field distinct from `model` on every outcome record; the analysis fails loudly if it's absent.
- **Harness errors.** `--resume` retries a harness-error run; the analysis keeps the LAST record per (requested model, arm, task, rep), so a retried run's final attempt supersedes the harness-error attempt it replaced. Any harness errors still present after this dedup are excluded from cost and n for P1/P2 (but reported); if they exceed 5% of either Sonnet cell, the stage-1 verdict is INVALID rather than GO/STOP, and must be investigated before any conclusion.
- **Completeness.** After dedup, each Sonnet cell must correspond to exactly 40 tasks × 3 reps of runs; a missing run, or a duplicate that survives dedup, fails the analysis loudly.
- **cost_basis.** Every counted Sonnet run must have `cost_basis == "cli_list_estimate"`, matching the spec's Scope ("cost from the CLI's list-price estimate"); anything else fails the analysis loudly, since a differently-priced call would not be comparable.

This is reflected in Tests and decision, above, and in `bench/analysis/go_pilot.py` (`load`'s dedup, `_validate_sonnet_cell`, `EXPECTED_TASKS`/`EXPECTED_REPS`/`HARNESS_INVALID_RATE`).

### Revision 3: a gogen pipeline failure counts against arm B, not as a harness error (2026-09-29)

Controller ruling, made before the freeze and before any live run, in response to a code review finding on Task 5's arm-B Go backend: its initial implementation classified any `go build` failure on an already-checked program (or any other exception raised while lowering or emitting it) as a `harness_error`, indistinguishable from a genuinely missing Go toolchain. Because harness errors are EXCLUDED from cost and n (Revision 2), that classification would have silently dropped a real B failure -- a bug in the Lattice→Go compiler triggered by the model's specific program -- from both the numerator and denominator of B's cost-per-correct-task, biasing the pilot's cost comparison toward B.

Ruling: **a failure of the Lattice→Go pipeline (lowering, emission or `go build`) on a checked program counts as a loud failure of arm B, not a harness error.** It is scored (`correct=False`, `failure="loud"`), counted in cost and n exactly like any other loud failure, and is never fed back for repair -- unlike a checker rejection or a runtime start error, the model cannot fix a gogen bug by rewriting its Lattice source, so the task ends immediately rather than spending a repair round on it. A missing or too-old Go toolchain remains a genuine harness infrastructure failure: it is detected before any Go-specific processing of the model's program even begins, and is unrelated to whether that program is correct, so it stays excluded from cost and n as before.

This is reflected in Tests and decision, above, and in `bench/runners.py` (`_run_go_cases`, `_GogenBuildFailure`). `bench.safety.outcome_class`'s existing correct/loud/silent/harness taxonomy already classifies any `failure` other than `"harness_error"`/`"wrong_result"` as loud, so `bench/analysis/go_pilot.py` needed no code change -- only a test confirming a `failure="loud"` record is counted like any other loud failure, not excluded like a harness error.

## What each outcome means

| Outcome | Meaning |
|---|---|
| GO | Lattice→Go is a substantially cheaper way to get correct Go services from a strong model, with no correctness loss. That is a narrow but real value claim, independent of the refuted safety claim. |
| STOP, P1 refuted | Writing Go directly is at least as cheap: the Go target has no LLM-cost value. Its value is limited to the engineering artefacts (verified multi-target compilation). |
| STOP, P1 not confirmed or P2 fails | Any cost advantage is too small or costs correctness. Report honestly; no further Go benchmark investment. |

## Threats to validity (stated in advance)

- The 40 tasks were written for the Lattice/FastAPI comparison; they may suit Lattice's constructs. This is the same bias the earlier API runs had, and it favours B, so a STOP is a strong result and a GO needs stage 2's Go-specific tasks to confirm.
- The Go platform kit's design affects G's size and cost. It provides exactly what the Python kit provides (auth only), no more.
- Prompt size: B's system prompt (the Lattice guide) is much larger than G's. That cost is real and is counted, not excluded.
- One CLI version and one machine (arm64 macOS).
- The P2 margin was widened from 5 to 10 points, and the confidence relaxed from one-sided 97.5% to one-sided 95%, before any run (see "Revisions before freeze" #1), because the original combination made a true tie fail at every plausible correct rate. Both changes make GO easier for B: a GO's correctness claim is weaker evidence than the original rule's GO would have been, and a real several-point deficit for B could still pass the revised P2.
- The harness-error exclusion and the 5%-cell INVALID check (see "Revisions before freeze" #2) are themselves judgment calls: excluding harness errors from cost/n assumes a `--resume` retry's final attempt is representative, and the 5% threshold is not derived from a power calculation.
