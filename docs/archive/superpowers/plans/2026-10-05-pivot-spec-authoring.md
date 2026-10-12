# Pivot: spec authoring (after Phase 2), plan

> Decided by the user on 2026-10-05, after Phase 2's exit was missed twice (results log,
> "Phase 2 conclusion"). Branch `roadmap`.

## Why

- **Phase 1:** the spec-and-check loop prevents silent failures when the spec states the
  rule.
  - Sonnet with the owner's spec (S\*): 0 silent runs out of 84.
  - With the model's own drafts (S) it still helps: Haiku H4 is confirmed, and Sonnet
    goes from 16 to 9 silent runs, though that is not confirmed.
  - Every S silent run was a rule its draft never stated.
- **Phase 2:** importing existing apps automatically is the weakest link.

So the next lever is **writing good specs**: helping the model (and the user) state the
edge cases.

Import stays as **rules-first assist**: proposals for review, behaviour coverage
reported, and no 90% gate. The direction draft §8 is updated to match.

## Work

### A. Verifier soundness (prerequisite)
The two problems left open by Phase 2:
1. A "shared" T1 verdict must not rest on reads alone. Writes by non-owners must be
   probed, and a decisive probe is required.
2. Writes through ORM relationships (`obj.tags.append`, `user.favorites.add`,
   `relationship` collections) must count as table evidence in the front ends. A route
   whose relationship writes weren't followed is `unread`, so the outside-API veto holds.

Each fix is tested first on a toy app or a dev app. The dev-app gate and all existing
tests stay green.

### B. New held-out tickets (t45 and up)
- **Count:** 12 new tickets on the multi-vendor app, written by an agent that sees
  neither the checklist (C) nor Phase 1's per-ticket misses.
- **Kinds:** state machines, invariants, nested responses, bounds (including exclusive
  ones), role × ownership, atomicity, aggregates, duplicates, and empty/edge collections.
- **Each ticket has:**
  - a reference;
  - oracle cases written from the ticket text (STATUS_ONLY for anything the text leaves
    open);
  - ≥ 3 silent traps;
  - the owner's blind spec delta, written from the text only, as in Phase 0.
- **Gates:** every reference is correct, every trap is silent under the oracle, every
  trap is caught by its owner delta, and the deltas pass approval on the reference.

### C. The drafting aids (arm S+)
Written by an agent that never sees B's tickets:
- a **generic edge-case checklist** for spec drafts:
  - bounds, both inclusive and exclusive;
  - every response field, stated in full;
  - the role × ownership × missing matrix;
  - state edges, including the illegal ones;
  - atomicity: a refused request writes nothing;
  - duplicates;
  - empty collections;
  - aggregates over zero rows;
  - exact error statuses, with the text only where the ticket states it;
- a **self-review step**: the model checks its draft against the checklist and the
  ticket before submitting.

The checklist is derived from general API-defect categories, not from Phase 1's misses
or traps. It is frozen before any run.

### D. Pre-registered experiment
- **Arms:**
  - **S:** as Phase 1;
  - **S+:** S plus C's checklist and self-review;
  - **S\*:** the owner's blind delta.
- **Models:** Sonnet and Haiku.
- **Size:** 12 tickets × 6 reps.
- **Primary test:** on Sonnet, S+ has fewer silent failures than S (one-sided Fisher),
  with a correctness guard and the 25% spec bar.
- **Secondary:** the same test on Haiku; and the S+ vs S\* gap, descriptive.
- **Also measured:** spec coverage, i.e. the share of B's traps each approved draft
  catches, measured offline.
- **Cost estimate and the user's approval come before any run.** The run uses a frozen
  worktree and the CLI only.

## Order
1. A, B and C run in parallel (three agents, isolated worktrees).
2. Merge.
3. Pre-registration, with the estimate shown to the user.
4. Run from a frozen checkout.
5. Oracle review.
6. Results.
