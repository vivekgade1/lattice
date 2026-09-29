# Sub-project 5b, stage 1: Go cost pilot (pre-registered)

> Status: DRAFT for the user's approval; it becomes PRE-REGISTERED when committed, before any live run. It follows the Go target equivalence gate (5a: Lattice→Go equals the Python runtime on 49 programs and 2,478 cases; `docs/results/2026-09-26-bench-smoke.md`, "Go target — equivalence gate") and the confirmation run ("Confirmation run", 2026-09-29), which refuted "Lattice reduces silent bugs" for edits on a strong model. Design context: `docs/superpowers/specs/2026-09-28-lattice-go-target-design.md` §5.

## Why a pilot, and what it can still show

The confirmation run tested **edits to an existing Python app** and found no safety advantage for Lattice. This pilot tests the one open claim from earlier evidence that the confirmation run did not touch: on **greenfield API tasks**, Lattice programs are much shorter than the hand-written equivalent (API runs: 0.52–0.56× AST size and 0.76–0.84× cost per correct task vs FastAPI on Sonnet/Opus). When the hand-written target is Go, which is more verbose than FastAPI, that gap could be larger. The pilot measures only that: whether writing Lattice compiled to Go is **substantially cheaper per correct task** than writing Go directly, without losing correctness. It makes no safety claim.

## Questions

- **P1 (primary):** on claude-sonnet-5, is the cost per correct task of arm B (Lattice→Go) at most **0.6×** that of arm G (Go written directly)?
- **P2 (co-primary, non-inferiority):** on claude-sonnet-5, is arm B's correct rate no more than **10 points** below arm G's, at a one-sided 95% bound? (Revised from 5 points before any run; see "Revision before freeze" below.)
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
- **Cost per correct task (per arm and model):** total CLI cost of the arm's runs ÷ number of correct runs.
- **P1:** the ratio R = cost/correct(B) ÷ cost/correct(G) on Sonnet, with a 95% percentile bootstrap interval (10,000 resamples of tasks, keeping each task's runs together).
  - **Confirmed** if R ≤ 0.6 AND the interval's upper bound < 1.0.
  - **Refuted** if R ≥ 1.0.
  - Otherwise, **not confirmed**.
- **P2:** a one-sided non-inferiority test on the correct rates (Sonnet), margin 10 points at a one-sided 95% bound: the lower limit of the **two-sided 90%** Newcombe-Wilson interval for correct(B) − correct(G) must be > −0.10.
- **Stage-1 verdict:**
  - **GO:** P1 confirmed AND P2 holds. Proceed to stage 2 (the rest of spec §5: the implicit-requirements condition, the Go-specific trap tasks, the T2 script control, and binary latency/throughput under load), with its own pre-registration.
  - **STOP:** anything else. Write up the result and do not run stage 2.
- **Also reported:** correct, silent and loud rates with Wilson 95% intervals; S1/S2 measurements; per-family breakdown (control, ownership, validation, errors, efficiency).

## Revision before freeze

Controller ruling, 2026-09-29, made before this pre-registration froze and before any live run: the originally stated P2 margin of 5 points (two-sided 95% Newcombe-Wilson) is unattainable at this pilot's sample size (n = 120 runs/arm/model) even when arm B and arm G have **identical** correct rates. The Newcombe-Wilson lower bound for a true tie's difference, two-sided 95%, n = 120/arm:

| tied correct rate | lower bound |
|---|---:|
| 0.95 | −0.061 |
| 0.90 | −0.079 |
| 0.85 | −0.092 |
| 0.80 | −0.101 |

At every plausible correct rate, a true tie would already fail a 5-point margin — P2 could never hold even with no real difference. P2 is revised to the standard non-inferiority form before any run: **margin 10 points, tested at a one-sided 95% bound**, obtained as the lower limit of the **two-sided 90%** Newcombe-Wilson interval for correct(B) − correct(G). This is reflected above (Questions, Tests and decision) and in `bench/analysis/go_pilot.py`.

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
- The P2 margin was widened from 5 to 10 points before any run (see "Revision before freeze"), because the original margin was unattainable at this sample size for a true tie. The wider margin makes GO easier for B: a GO's correctness claim is weaker evidence than a 5-point-margin GO would have been, and a real 6-9-point deficit for B could still pass P2.
