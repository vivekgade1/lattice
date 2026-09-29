# Option 1 main experiment: does Lattice's atomicity guarantee, checked on Python, prevent silent partial writes? (pre-registered)

> Status: PRE-REGISTERED 2026-09-29, committed before any model run of this experiment.
> It follows the atomicity pilot (`docs/superpowers/specs/2026-09-29-option1b-atomicity-pilot.md`;
> results in `docs/results/2026-09-26-bench-smoke.md`, "Option 1b"). That pilot's
> pre-registered verdict was SILENT-BUT-UNCOVERED; after correcting two oracle cases it
> was GO. The user chose to go on from the corrected verdict.

## Why arm L is not in this experiment

An earlier draft compared P, L and Q. Two findings before any run removed arm L:

- **Nested responses.** Lattice responses are flat rows: no object with a nested list,
  and T2 values of such types cannot cross into a response (L883). Five of the seven
  tickets answer with one (t38, t40, t42, t44: an order with its `items`; t43: a vendor
  with its `products`), so arm L would fail there by construction. The experiment would
  measure a missing feature, not the guarantee.
- **The import.** The CLI import of the app (`bench/imports/multivendor`, $6.49 of a $6.00
  cap, 1h49m) verified only 3 of 17 routes.

The user chose (2026-09-29) to test the guarantee as a check on ordinary Python instead:
arm Q, with a new pycheck rule.

## The new check: PY807 partial writes

PY807 is Lattice's one-transaction-per-write guarantee, stated on Python
(`lattice/pycheck/checks.py`). In a function a route runs, it flags a commit (a
`session.commit()`, or a call to a package helper that commits) that a failure can follow:
a later `raise`, a later call to a helper that can raise, or, within one loop, a failure
the next iteration can hit. It was built and measured before this spec:

| set | PY807 |
|---|---|
| t38–t44 references (all pycheck codes) | 0 findings |
| atomicity trap variants (one per ticket) | 7/7 flagged |
| the other t38–t44 trap variants | 0 flagged |
| the pilots' real Sonnet partial writes (t38 ×3, t40 ×3, t39 ×2) | 8/8 flagged |
| the pilots' other t38–t44 runs | 0 flagged |
| round 4's arm-P runs (earlier tickets) | 1 more Haiku and 3 more Sonnet silent runs flagged; still no correct run flagged (135) |

Arm Q's system prompt states the guarantee: "Writes: a request's writes succeed or fail
together..." (`lattice/pycheck/describe.py`).

## Questions

- **Q1 (primary):** with claude-sonnet-5, do Python edits with pycheck's findings (arm Q)
  produce fewer silent failures than plain Python edits (arm P) on tickets whose correct
  change needs several writes to succeed or fail together?
- **Q2 (secondary):** the same with claude-haiku-4-5.
- **Q3 (secondary, the mechanism):** on each model, does Q have fewer runs that are silent
  on a partial-write case (a case tagged `one-statement/atomic`) than P?

## Scope

- **App:** `bench/import_repos/multivendor`.
- **Tickets:** t38 and t39–t44. Their texts, references and oracles are unchanged since
  the pilots, except for the oracle corrections already recorded in the results log.
- **Arms:**
  - **P:** plain Python edits.
  - **Q:** Python edits whose repair feedback adds pycheck's findings, with the policy of
    `bench/edit_slice/qcheck.py` (derived from the app; ownership facts from the committed
    import's profile).
- **Models:** claude-sonnet-5 (primary) and claude-haiku-4-5 (secondary).
- **Reps and total:** 6 per ticket, arm and model, so 7 × 2 × 6 × 2 = **168 runs**. The
  estimate is about $8.50 list-price equivalent, billed to the plan through the CLI.
- **Setup:**
  - **Transport:** the Claude CLI only (`--backend cli`); no API key anywhere (refused in
    code since `9f1f857`).
  - **Repairs:** a budget of 2 repair rounds, as in every round.
  - **Code:** the commit this spec is committed with.
- **Data:** only this run's data is used; the pilots are not pooled in.
- **Commands:** for each model M in {claude-sonnet-5, claude-haiku-4-5} and arm A in
  {P, Q}:

  ```
  uv run python -m bench.edit_slice.run --backend cli --model M --arm A \
    --tickets t38_reorder,t39_bulk_price,t40_edit_order_items,t41_close_shop,t42_split_order,t43_vendor_onboarding,t44_merge_orders \
    --reps 6 --workers 4 --out bench/results/option1_main_<model>_<arm>.jsonl
  ```

## Tests and decision (fixed now)

- **Unit:** a run, scored correct, silent or loud by the hidden oracle. A timeout or a
  refusal is loud.
- **Q1 and Q2:**
  - **Test:** a one-sided Fisher exact test on silent vs not-silent runs, Q vs P, per
    model, backed by a per-ticket paired sign test on silent counts (ties dropped).
  - **Confirmed** only if Fisher's p < 0.05 **and** the sign test favours Q.
  - **Refuted** if Q's silent rate ≥ P's.
  - **Not confirmed** otherwise.
- **Q3:** a one-sided Fisher exact test on runs silent on a `one-statement/atomic` case,
  Q vs P, per model. Confirmed if p < 0.05.
- **Correctness guard:** a confirmed Q1 or Q2 counts as a safety win only if Q's correct
  rate is not lower than P's by more than 10 points. Otherwise it is reported as a
  trade-off.
- **Also reported:**
  - correct, silent and loud rates with Wilson 95% intervals, per model, arm and ticket;
  - silent runs by mechanism tag;
  - PY807 findings per round, and whether the repair removed them;
  - cost per correct run, tokens, repair rounds, time per run, lines changed.

## Oracle rule

The oracle never compares what a ticket's text does not determine. If a run's diff shows
such a case, the case is corrected, both arms are rescored without new model calls, and
both the pre-registered and the corrected verdicts are reported.

## Known limitations, stated now

- **pycheck's ownership check (PY801) does not look at this app's routes.** Their auth is
  a role set (`Roles(...)`) or `Authenticated`, not `Customer`. Widening PY801 to them
  gave false findings on three references: products are a public catalogue, and a vendor
  closing a shop cancels other buyers' orders. It was reverted. Arm Q therefore gets no
  ownership findings here; the tickets' ownership traps are checked by the oracle only.
- **The app is not fully held out from the tooling.** Its gaps motivated the role-set
  change (`64d7ebe`) and PY807. Both were built on synthetic fixtures and the pilots'
  trap variants, before this experiment's runs.
- **PY807 is syntactic.** It cannot see a failure raised by the database or a library, or
  a helper deeper than three calls.

## What each outcome means

| Outcome | Meaning |
|---|---|
| Q1 confirmed (with the guard) | A Lattice guarantee, checked on ordinary Python, prevents a class of silent bug a frontier model makes. It is the first positive result for the "checks on ordinary code" direction on a strong model, and supports repositioning Lattice as a checker and verified-edit loop. |
| Q1 not confirmed, Q2 confirmed | The check helps a weaker model only, as the confirmation run's Q+ hinted. |
| Neither | A check that recognises the bug is not enough: the model does not act on it, or the slip is rarer than the pilots suggested. |
