# Phase 1: the spec loop on Python (pre-registration draft)

> Roadmap: `docs/lattice-direction-executable-spec.md` §8, Phase 1. Branch `roadmap`.
> Status: **DRAFT, awaiting the user's approval. No model call is made before it is
> approved and committed.** All model calls go through the CLI (`--backend cli`),
> plan-billed; no API key.

## Question

Does writing a ticket's requirement as a spec change, then checking the LLM's Python
against the spec (layers A–D) and repairing, reduce silent failures compared with:
- writing Python from the ticket (arm P); and
- Python with pycheck findings only (arm Q, option 1's confirmed winner)?

## Arms

All arms get the same ticket text and the same app, and all are scored by the same
held-out oracle (`bench/edit_slice/oracle.py` with the multi-vendor cases, STATUS_ONLY
and EXCLUDED as fixed after option 1). The repair budget is `MAX_REPAIRS = 2` in every
checked arm.

| arm | what the model does | checks fed back |
|---|---|---|
| **P** | ticket → Python | none |
| **Q** | ticket → Python | pycheck (layer A only), as in option 1 |
| **S** | ticket → **draft spec delta** → simulated approval → Python | `lattice.spec.check`, layers A–D, against base spec + approved draft |
| **S\*** | ticket + **the owner's spec delta** (the blind `DELTAS`, written from the ticket before any check ran) → Python | layers A–D, against base spec + that delta |

- **S, the spec step:**
  - The model writes a `SpecDelta` (scenarios, invariants, machines, unchecked) as a
    Python module, from the ticket and the base spec's source.
  - Validation errors (S001–S016) are fed back for up to 2 rounds. These are not
    counted as spec errors.
- **Simulated approval** (the direction draft's §10 question 4, default taken: a
  held-out set of approved behaviour). Each drafted scenario and invariant is run on
  the ticket's **reference** implementation:
  - the ones that hold are approved;
  - the ones that fail are rejected and dropped. The model is not told why.
  - **Spec error rate** = rejected / drafted rules.
  - This is optimistic: a real owner would also miss errors, and would add the rules
    the draft leaves out. That is a stated threat.
- **S\*** is the upper bound: what the loop does when the owner has written the spec.
  - It uses the blind deltas, not the post-run additions (see the Phase 0 results
    entry). So S\* does not get the six edge cases added after seeing the traps.

## Scope

- **Tickets:** t31–t44 on the multi-vendor app (14). Every ticket has a reference, a
  spec delta, and Phase 0's 50 recorded traps.
- **Models:**
  - claude-sonnet-5 is primary: all four arms;
  - claude-haiku-4-5 is secondary: P and S.
- **Reps:** 6.
- **Total runs:** Sonnet 4 × 14 × 6 = 336, Haiku 2 × 14 × 6 = 168, so **504 runs**.
- **Cost estimate:**
  - Option 1's main run cost $0.095 per run in list-price equivalent (P/Q, about 1.2
    rounds).
  - Assume S costs 2.5× that (a spec step plus more repair rounds), and S\* 1.5×.
  - Sonnet: P 84 × 0.10 + Q 84 × 0.10 + S 84 × 0.25 + S\* 84 × 0.15 ≈ **$50**.
  - Haiku: P 84 × 0.11 + S 84 × 0.28 ≈ **$33**.
  - **Total ≈ $85 list-price equivalent, plan-billed through the CLI.**
  - Wall time: about 5–6 hours at 4 parallel runs.

## Measures

- correct / silent / loud runs against the oracle;
- **silent failures against intent** (primary);
- the spec error rate (S);
- spec coverage: the share of a ticket's Phase 0 traps the approved draft would catch,
  measured offline by running the traps against base spec + approved draft, with no
  model calls;
- $ per correct change, tokens, repair rounds, wall time;
- catches by layer: on the round that changed a candidate, which layers' findings were
  fed back;
- **false findings:** findings on a final candidate the oracle scores correct.

## Pre-registered hypotheses and analysis

`bench/edit_slice/main_analysis.py`, extended before any run.

- **H1 (primary, Sonnet):** S has fewer silent runs than P.
  - Test: one-sided Fisher exact test, p < 0.05.
  - Correctness guard: S's correct rate ≥ P's.
  - **Spec bar:** S's spec error rate ≤ **25%** (rejected / drafted).
  - H1 is CONFIRMED only if all three hold.
- **H2 (Sonnet):** S has fewer silent runs than Q (one-sided Fisher, p < 0.05). This
  is what says the spec adds value beyond pycheck.
- **H3 (Sonnet, descriptive):** S\* vs S, the cost of the model drafting the spec. It
  is reported, not tested.
- **H4 (Haiku):** S vs P, as H1.
- **Sign tests per ticket** are reported for H1, H2 and H4.
- **Oracle review**, as in option 1:
  - every silent run's final candidate is re-run and its diff read;
  - an oracle defect is fixed and rescored without new model calls, and both verdicts
    are reported.

### Decision rules, fixed with the analysis code (`bench/edit_slice/phase1_analysis.py`)

1. **The correctness guard is strict:** S's correct rate must be ≥ P's, with no
   tolerance. Option 1 allowed 10 points; Phase 1 does not.
2. **The verdicts are CONFIRMED or NOT CONFIRMED,** and a failed verdict names every
   failed condition. Sign tests are reported and never decide. There is no REFUTED.
3. **The spec error rate is pooled:** Σ rejected / Σ drafted over all S runs of the
   model, `spec_failed` runs included.
   - If S drafted no rules at all, the rate is undefined and the spec bar counts as
     failed.
   - H4 applies the spec bar to Haiku's S runs.
4. **Catches by layer** count every repair round's fed-back layers, each layer once per
   round.
5. **Spec coverage** is computed offline from the recorded approved drafts
   (`sdraft.coverage`), not by the analysis.
6. **Oracle review:** the analysis runs on the original outcomes and on the rescored
   ones, and both are reported.

## Threats

1. **Optimistic approval:** filtering drafted rules by the reference uses held-out
   knowledge. S never sees it, but its spec is cleaner than a real owner's would be. S\*
   bounds the other side.
2. **Same tickets as Phase 0:**
   - the base spec and deltas were reviewed on these tickets;
   - S\* uses the blind deltas to limit the leak;
   - S's draft is the model's own.
3. **Weak layer B:** 10 of 14 tickets have no program (missing list bodies, per-row
   writes, non-literal details). The loop mostly runs on scenarios (D) and static
   checks (A), so B's contribution is under-measured.
4. **One app, one machine, one CLI version.**

## Build (no model calls)

| task | what | files |
|---|---|---|
| P1-A | arm S/S\* prompts: the spec DSL reference for the model, the base-spec excerpt, the delta module format; parse and validate the drafted delta | `bench/edit_slice/prompts.py`, new `bench/edit_slice/sdraft.py` |
| P1-B | simulated approval: run each drafted rule on the reference; record approved/rejected | `bench/edit_slice/sdraft.py`, `bench/specs/exit.py` |
| P1-C | the S/S\* check round: `lattice.spec.check` rendered as repair feedback (rule first, a capped number of findings, no oracle data) | `bench/edit_slice/run.py` |
| P1-D | analysis: H1–H4, spec error rate, coverage, catches by layer, false findings | `bench/edit_slice/main_analysis.py` |
| P1-E | dry run with a scripted model (reference code, then a trap variant to exercise repair): the whole pipeline end to end, at no cost | `tests/bench/test_phase1_dry.py` |

Then: the cost estimate re-done from prompt sizes, the user's approval, a commit of this
spec, and the run.
