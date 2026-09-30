# Go cost pilot 2: harder tasks and a practical cost bar (DRAFT pre-registration)

> Status: **DRAFT for the user's review.** It becomes PRE-REGISTERED only at a freeze commit made immediately before the first live run, after the user approves this document, the task suite, the dry run and the cost estimate. Nothing below may change after that freeze. It follows pilot 1 (`docs/superpowers/specs/2026-09-29-lattice-go-pilot.md`; results in `docs/results/2026-09-26-bench-smoke.md`, "Go cost pilot"). Pilot 1's verdict was **STOP** under its own 0.6 bar, with an observed R = 0.69 [0.59, 0.80] and both arms 120/120 correct. This is a **new** experiment with its own bar, chosen before any of its data exists. It does not revise pilot 1's verdict.

## Why a second pilot

Pilot 1 left three things unanswered:
1. **Its cost bar (R ≤ 0.6) was stricter than what would justify using Lattice.** A confident saving of about 15% or more is worth having for a code-generation pipeline.
2. **The tasks were at a ceiling.** Every run was correct, so the pilot says nothing about correctness or silent bugs, which are the reasons one would use Lattice at all.
3. **Checker repair rounds were about 46% of arm B's cost** (31/120 Sonnet runs repaired). Part of the gap is friction, not a property of the language.

## Stage 0 (engineering, before the pilot, no model calls)

Reduce checker friction for the most frequent pilot-1 repair causes (L821, L802, L205, L801, L002):
- sharper diagnostics with exact fix hints;
- where a code reflects a common natural spelling, accept it through normalisation, as `lattice/check/normalise.py` already does for other spellings.

Every change goes through the usual process: tests, the equivalence gate (0 mismatches), and the language rules (`docs/lattice-language-rules.md`: no guarantee weakened). Pilot 1's data is used only to choose WHICH codes to address, never to tune against pilot 2's tasks.

## Questions

- **C1 (primary, cost):** on claude-sonnet-5, is arm B's cost per correct task confidently lower than arm G's by at least 15%? That is, is the 95% bootstrap upper bound of R = cost/correct(B) ÷ cost/correct(G) **below 0.85**?
- **C2 (co-primary, correctness):** on claude-sonnet-5, is arm B's correct rate non-inferior to arm G's within **5 points** (one-sided 95%)? With harder tasks, correctness is now informative, so the margin goes back to 5 points. The sample below is sized for it (see "Power").
- **S1 (secondary, safety):** silent failures, B vs G (one-sided Fisher, B < G), on Sonnet and on Haiku. This is reported, not decisive.
- **S2 (secondary):** the same measurements on claude-haiku-4-5; program size; repair rounds; cost split by cache state.

## Tasks (a new suite, written before any Lattice solution exists)

- **60 new API tasks**, written from realistic product tickets by an agent that has NOT seen Lattice's prompt or constructs. They are committed with their hidden cases before anyone checks whether Lattice can express them.
- **Hardness requirement:** each task has at least one trap that hand-written code plausibly gets wrong. Examples:
  - ownership through a join (a child row's parent belongs to the caller);
  - a multi-write request that must be atomic;
  - pagination with a total and a filter;
  - a unique conflict;
  - a partial update with explicit null;
  - efficiency (no N+1) on a list with related rows;
  - validation of cross-field rules;
  - time windows.
- **Families:** 12 tasks each in ownership, atomicity, validation, efficiency and errors/conflicts.
- **Expressibility:** a task Lattice cannot express is NOT removed. It counts as a failure of arm B (loud, "not expressible") in the analysis, reported separately. That removes the pilot-1 bias of tasks written for Lattice.
- **Ceiling check:** after the dry run, if G's reference-free first-pass correct rate on a 10-task sample is above 95% on Sonnet, harden the suite before the freeze. That is the one allowed pre-freeze change to the tasks, and it is disclosed.

## Arms and setup

Identical to pilot 1 (arms G and B-on-Go, the same kit, prompts, repair budget of 2, sandbox, scorer, CLI transport, harness-error rules), plus:
- **Cache states are reported separately.** Every run records cache-write and cache-read tokens. The primary analysis uses the actual (warm-batched) cost, and a secondary analysis reprices every run with no caching. The primary claim must hold under both to be called "confirmed without qualification". If it holds only warm, the verdict says so.
- **Interleaved order.** The arms alternate per task (G, B, G, B, …), instead of all-G then all-B, to spread any drift or caching effects evenly.

## Power

Correctness rates on hard tasks might be about 0.8. For C2 (a 5-point margin at one-sided 95%) with a true tie at p = 0.8, the Newcombe-Wilson bound needs about **n ≥ 400 per arm**, which is 60 tasks × 7 reps = 420. That run is **60 × 2 arms × 7 reps = 840 runs on Sonnet**, plus **60 × 2 × 3 = 360 on Haiku** (secondary, not powered). Total: **1,200 runs**. The pilot-1 cost scaled to that size is about $25–40 (the exact estimate comes from the dry run, as in pilot 1). If that is too large, the alternative is C2 with a 10-point margin at n = 180 per arm (60 × 3 reps) and a note that correctness is only coarsely tested. The user chooses before the freeze.

## Decision (fixed at the freeze)

- **GO: "Lattice→Go is a confidently cheaper way to get correct Go services":** C1 holds (upper bound < 0.85) AND C2 holds.
  - Qualified as "warm-cache only" if C1 fails under the no-cache repricing.
- **Safety claim (separate):** a "Lattice reduces silent bugs" claim needs S1 at p < 0.05 on Sonnet AND on Haiku. Otherwise it is not supported.
- **STOP:** C1 fails or C2 fails.
- **INVALID:** harness errors above 5% of either Sonnet cell.
- **Reported regardless:** R with its interval (warm and cold), correct/silent/loud rates with Wilson intervals, the not-expressible count, repair rounds and their cost share, and the per-family table.

## Threats to validity (stated in advance)

- Stage 0 changes the checker. If it overfits to pilot 1's tasks, pilot 2's fresh tasks guard against that.
- The task writer may still pick tasks that suit one arm. Mitigation: a writer blind to Lattice; tasks committed before any solution; expressibility failures count against B.
- One machine, one CLI version, one scorer.
- Go is the only baseline language here. A FastAPI baseline is out of scope; it could be run later as a third arm.
