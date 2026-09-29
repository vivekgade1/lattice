# Option 1b: atomicity pilot (pre-registered)

> Status: PRE-REGISTERED 2026-09-29, before any model run on these tickets. It follows the
> first option-1 pilot (`docs/superpowers/specs/2026-09-29-option1-pilot.md`; results in
> `docs/results/2026-09-26-bench-smoke.md`, "Option 1 pilot"). After its oracle was
> corrected, that pilot left one real slip: t38, where a refused reorder left a partial
> write, in all 3 reps. The user chose this small follow-up pilot over launching the
> main experiment on t38 alone.

## Question

When the correct change needs several writes to succeed or fail together, and the app's
own helpers commit partway, does claude-sonnet-5 editing plain Python (arm P) leave partial
or wrong writes often enough to justify the main experiment? And would Lattice's
guarantees catch those writes?

## Scope

- **App:** the multi-vendor marketplace (`bench/import_repos/multivendor`), as in the first
  pilot: same seed, same arm-P context files.
- **Tickets:** t39–t44 (`OPTION1B_TICKETS` in `bench/edit_slice/tickets.py`), one family,
  **T**. They were written as texts first and approved before any reference or oracle work:

  | Ticket | Change |
  |---|---|
  | t39 | bulk price update |
  | t40 | edit a pending order's items |
  | t41 | close a vendor's shop |
  | t42 | split an order |
  | t43 | onboard a vendor with products |
  | t44 | merge pending orders |

  Every error message the oracle compares is stated in the ticket text; that is the first
  pilot's lesson.
- **Arm, model, reps:** P only, claude-sonnet-5, 3 reps, so **18 runs**.
- **Transport:** the Claude CLI only (`--backend cli`). No API key is used anywhere; the
  code refuses the API-key transport since `9f1f857`.
- **Command:** `uv run python -m bench.edit_slice.run --backend cli --model
  claude-sonnet-5 --arm P --tickets option1b --reps 3 --workers 4 --out
  bench/results/option1b_pilot_sonnet_P.jsonl`
- **Code:** the commit this spec is committed with.

## The oracle

It works as in the first pilot: hand-written cases on the app's seed and a few database
variants, each compared with the ticket's reference. Before this spec was committed,
`tests/bench/test_edit_slice_option1.py` established the following for t39–t44:

- every reference is correct, with every raise site reached;
- no reference ever answers 5xx;
- the unmodified app is not correct on any ticket;
- 19 trap variants (3–4 per ticket) all score silent, each on the case named for its trap;
- the smoke run and arm P's dry run pass on every reference;
- no trap note or case name reaches the prompt.

Each trap case is tagged with the Lattice guarantee that would catch its violation
(`MECHANISMS` in `bench/edit_slice/repos/multivendor.py`). The `*_writes_nothing` cases are
tagged `one-statement/atomic`.

## Gates and decision (fixed now)

Unit: a run. For family T (`bench/edit_slice/pilot_gate.py --pilot 2`):

1. **Silent-rate gate:** at least 3 of the 18 runs are silent (≥ 15%, the same rate as
   the first pilot).
2. **Mechanism gate:** a silent run is *covered* if every case it fails silently has a
   tag other than `none`. The gate passes if at least half the silent runs are covered.

Before the gate is applied, every silent run's diff is read, as it was after the first
pilot. A mismatch on something the ticket text does not determine is an oracle defect.
It is corrected and rescored without new model calls, and **both** the as-registered and
the corrected verdicts are reported.

**Decision:**

| Outcome | Next step |
|---|---|
| T passes both gates | Pre-register the main experiment on t38 and t39–t44: arms P, L and Q; claude-sonnet-5 primary, claude-haiku-4-5 secondary; ≥ 6 reps; CLI only; one-sided Fisher on silent runs plus a per-ticket sign test. Before it runs, arm L needs the importer's output for this app, with start-state fidelity checked. |
| Silent-rate gate passes, mechanism gate fails | Sonnet slips here, but not where Lattice reaches. Report it; do not run the main experiment. |
| Fewer than 3 silent runs | t38 was an isolated slip. Report a **no-go** on option 1: Sonnet editing plain Python handled aggregations, ownership and atomicity on this app. |

## Reported

- **Rates:** correct, silent and loud runs, with Wilson 95% intervals.
- **For each silent run:** its cases, their mechanism tags and its diff.
- **Efficiency:** tokens in and out, cost per correct run (list-price equivalent; billed to
  the plan through the CLI), repair rounds, time per run and lines changed.
