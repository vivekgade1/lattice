# Perf budgets: "a constant may rise when the per-item term falls": results

Date: 2026-10-13. Branch `np-perfrule` (worktree `lattice-perfrule`, from `next-phases` 7358be2).
Pre-registration: `docs/results/2026-10-13-perf-rule-prereg.md` (1d35462, written before any code
of the change). Raw data: `docs/results/data/2026-10-13-perf-rule/` (`summary.json`, one JSON per
probe, `mutants.json`, `mutants.txt`). No LLM, no API key, no database server, no Docker: the rule
is judged on counts, so every probe is pure arithmetic on `judge()`.

## What changed

`lattice/perf/budget.py` `judge()`: a fitted constant above the budget's no longer fails on its own
when the fitted per-item term is below the budget's AND every measured size is within the budget's
allowed count. The notes then carry the trade and its crossover N* = (c' - c) / (p - p'); when
c' > c and p' < p but a measured size is over its allowed count, a "no trade" note says why. The
report's JSON keys are unchanged (the trade is explained in `notes`). The module docstring and
`docs/lattice-reference.md` (performance-budgets paragraph) describe the new rule. The owner decision
it implements: perf budgets stay in the side file; the constant may rise when the per-item term
falls (decided 2026-10-13).

## Blind results (predictions in the prereg, section 3)

| Probe | Predicted | Observed | Pass |
|---|---|---|---|
| J1 replay of the recorded spike-1a numbers (6 cases) | old rule = recorded 6/6; new: only J1a (7 / 7 against 6 + 1N) changes, fail -> pass, with the trade note and N* = 1 | exactly that; J1b-J1f `to_dict()` byte-identical old vs new | yes |
| J2 grid, 45 budgets x 78 exact fits at sizes 5 and 25 (3510 cells) | (a) 0 cells pass-then-fail; (b) newly passing = {c' > c, p' < p, both sizes within}; (c) old 675, new-only 599, new 1274, 121 trade cells still fail | all exact | yes |
| J2d / J2e | `to_dict()` identical in 2790 cells, verdict+notes change in 599, notes-only in 121; the two equivalent mutants PE1/PE2 identical in 3510/3510 | exact | yes |
| J3 suite | `tests/perf` 68 tests: 1 rewritten, 60 unchanged, 7 new | 68 passed; AST probe: 0 changed functions, the old trade test removed, 8 added | yes |
| J4 schema and CLI | report keys identical for pass, fail, unmeasured, trade; `check` exits 0 on the trade case (old: 1) | exact | yes |
| J5 neighbours (a count off the line, latency, per-item over, constant over with p' == p) | 11 verdicts as tabled | all as predicted | yes |

**24 of 24 probes** (the prereg's J1-J5 plus one extra AST probe, J3ast), each 10 runs with
identical output. **13 of 13 mutants killed** (PM1-PM4 existing, PR1-PR9 new; the control passes).

## Findings and post hoc (not pre-registered)

1. **The rule reduces to a simpler statement.** Because the fit passes through the largest counts at
   the smallest and largest sizes, "every size within its allowed count" already forces p' < p
   whenever c' > c. So the verdict is: per-item not above the budget's, every measured size within
   its allowed count, and latency. The prereg proved this and brute force by the orchestrator
   (3510 cells, 0 counter-examples) agrees. Consequence: two mutants (PE1, PE2: "trade without the
   per-item falling") are equivalent and cannot be killed by any test; J2e checks them on the grid.
2. **Decision 2 vs decision 6 of the prereg conflicted, found by the builder.** The old code printed
   its "the constant grew" note on any failure with p' < p and every size within allowed, including a
   latency-only failure where c' <= c, where the note was false. The new code does not print it. The
   verdict is unchanged; `to_dict()` differs in that note only. Recorded in `summary.json`
   (`post_hoc_observations`); the prereg's "byte-identical for c' <= c" wording is amended by this
   finding, not edited.
3. **Script corrections** (post hoc): the mutant runner applied every patch cumulatively to one
   scratch copy, which refused PR3 because it overlaps PM2; it now applies each patch to a fresh
   copy (PM1-PM4's old texts still occur exactly once). The J4(ii) probe had over-specified the
   report's notes as an exact list; the CLI prepends its measurement notes, so the check is now
   "the trade note is present with N* = 1" (behaviour was correct).
4. **`docs/lattice-reference.md` was edited by the orchestrator**, not the builder (outside the
   builder's file list; prereg decision 14).

## Orchestrator verification

| Check | How | Result |
|---|---|---|
| J2 predicted counts | brute force of the rule's arithmetic before the prereg commit, with no import of Lattice | 3510 / 675 / 599 / 1274 / 121 all equal; 0 cases violating the redundancy claim |
| Code change | read the `budget.py` diff | the decided rule only; `to_dict()` untouched; docstrings updated |
| Re-run from scratch | `tests/perf`, `tests/test_cli.py`, `dogfood/perf_rule_probes.py --runs 10`, `dogfood/perf_mutants.py` | 68 passed; 21 passed; probes 24/24; mutants 13/13 killed |
| Lint | `ruff check` and `ruff format --check` on the 10 touched files | clean |

## What this does not show

- **Small N.** The accepted trade is worse than the budget below N* (always at most the smallest
  measured size). A real regression at tiny N is accepted; the note shows N* so a reader can
  see it.
- **Only two measured sizes** per budget in the recorded runs; more sizes constrain the trade more.
- **J1 rests on the recorded tables** of `2026-10-12-spike1a-results.md`: the raw JSONs of that run
  were lost with a deleted worktree (stated there). The rule has not been run against a live Streak
  measurement; nothing here touched Docker or MySQL.
- The builder (and the pre-registration author) knew the rule being implemented; the predictions are
  about its arithmetic, which is exhaustively checked on the grid.

## Not done

- Not merged, not pushed. Streak's own `perf.budget.json` was not changed.
- `lattice perf` still counts only MySQL and in-process SQLite apps (spike 1a's other open items).
