# Experiment D: does a drafting checklist close the spec-authoring gap? (pre-registration)

> Plan: `docs/archive/superpowers/plans/2026-10-05-pivot-spec-authoring.md`, item D. Branch
> `roadmap`.
>
> Status: **APPROVED by the user (2026-10-07), frozen with this commit before any run.** Every call goes through the CLI (`--backend cli`), plan-billed,
> with no API key. The runs use a checkout frozen at the commit that approves this spec.

## Question

In Phase 1, the spec-and-check loop prevented silent failures when the spec stated the
rule. Sonnet with the owner's spec (S\*) had 0 silent runs out of 84. With its own drafts
(S) it still had 9, and every one was a rule the draft never stated.

**Does a generic edge-case checklist plus one self-review step (S+) make the model's own
drafts state more of those rules, and so cut silent failures?**

## Tickets (held out)

- t45–t56: 12 new tickets on the multi-vendor app. They were written after Phase 1 by an
  agent that saw neither the checklist nor Phase 1's misses.
- Each ticket has a reference, oracle cases written from its text, and 52 traps in total.
- The owner's deltas (S\*) are in `bench/specs/pivot_blind.py`. They were written blind,
  from the ticket text only, by a separate agent. Offline, they catch **45 of the 52
  traps**, and all 12 references check clean.

## Arms

| arm | spec step | code step |
|---|---|---|
| **S** | The model drafts a spec delta (Phase 1's prompt). Simulated approval keeps the rules the reference satisfies. | Python, checked on layers A–D against base + approved delta, up to 2 repairs |
| **S+** | As S, plus the frozen generic checklist in the draft prompt (`bench/edit_slice/spec_checklist.py`) and one self-review call. If the revision is invalid, the first draft is kept. | as S |
| **S\*** | The owner's blind delta (`BLIND_PIVOT_DELTAS`). No model spec step. | as S |

The owner's approvals for a ticket apply in every arm. That is t55's public directory,
stated in its text.

## Scope and usage

- **Models:** claude-sonnet-5 (primary) and claude-haiku-4-5. Both run all three arms.
- **Size:** 12 tickets × 6 reps × 3 arms × 2 models = **432 runs**.
- **Billing:** every call goes through the user's logged-in `claude` CLI on their Claude
  plan. No API key is used, and nothing is charged beyond the plan. The dollar figures
  below are **list-price equivalents only**: the CLI's per-call cost report, recorded as
  a benchmark metric ($/correct), never charged.
- **Expected list-price equivalent:**
  - `run.py --estimate` gives $20.66, but it counts only the first prompt of each run.
  - From Phase 1's observed cost per run (Sonnet S $0.25, S\* $0.12; Haiku S $0.16),
    plus S+'s review call (+30%), the expected total is **about $85, list-price
    equivalent, plan-billed**.
  - Wall time is about 4–6 hours at 4 workers. A plan usage limit stops the run cleanly,
    and it resumes with `--resume`.

## Measures

- correct / silent / loud runs against the oracle, with silent the primary measure;
- the spec error rate (rejected / drafted);
- S+ rules drafted before vs after review, and `spec_review_failed`;
- spec coverage: the share of the 52 traps each approved draft catches, measured offline
  after the run;
- catches by layer;
- false findings: runs the oracle scores correct but that still have check findings;
- $ per correct run, rounds, s/run, tokens.

## Pre-registered hypotheses (`bench/edit_slice/pivot_analysis.py`)

- **H1′ (Sonnet):** S+ has fewer silent runs than S.
  - Test: one-sided Fisher exact test, p < 0.05.
  - Correctness guard: S+'s correct rate ≥ S's, with no tolerance.
  - Spec bar: S+'s pooled spec error rate ≤ 25%.
  - CONFIRMED only if all three hold; otherwise NOT CONFIRMED, naming each failed
    condition.
- **H2′ (Haiku):** the same as H1′.
- **H3′ (descriptive):** the S+ vs S\* gap and the S vs S\* gap, per model.
- Per-ticket sign tests are reported and never decide.
- **Records:** the last record per (model, arm, ticket, rep) is used, because resume
  redoes error records.
- **A harness error counts as loud.**

## Oracle review

As in Phase 1:
- every silent run's final candidate is re-run and its diff read;
- each run is classified as genuine or an oracle defect (the oracle compares what the
  ticket doesn't state);
- defects are corrected and every arm rescored offline;
- both verdicts are reported, and the pre-registered one is the headline.

## Threats

1. **Simulated approval** against the reference is optimistic, as in Phase 1.
2. **The ticket author knew the general Phase 2 lessons, but not Phase 1's misses.** The
   checklist author knew neither the tickets nor the misses. A test enforces that the
   checklist uses no app-specific names.
3. **One app,** and the same base spec as Phase 1.
4. **S+ makes one more model call per run.** That cost is reported, and it is part of
   what is being tested.

## Commands (frozen)

Runs go in this order, one at a time, 4 workers each, from the frozen checkout:

```
for M in sonnet-5 haiku-4-5; for A in S S+ S*:
  uv run python -m bench.edit_slice.run --backend cli --model claude-$M --arm $A \
    --tickets pivot --reps 6 --workers 4 --resume \
    --out <repo>/bench/results/pivot_<M>_<A>.jsonl
```

Analysis: `python -m bench.edit_slice.pivot_analysis bench/results/pivot_*.jsonl`. It is
run again on the rescored files after the oracle review.
