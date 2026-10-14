# Spike 3, second half: `lattice improve verify` and `lattice improve draft`: results

Date: 2026-10-13. Branch `np-improve2` (worktree `lattice-improve2`, from `next-phases` 7358be2).
Pre-registration: `docs/results/2026-10-13-improve2-prereg.md` (5ce6637, written before any code;
the `--at` override was added by the orchestrator before that commit). Raw data:
`docs/results/data/2026-10-13-improve2/` (`summary.json`, one JSON per probe, `mutants.json`,
`mutants.txt`). No LLM, no API key, no network, no Docker: both commands are deterministic.

## What was added

- **`lattice improve verify ID [--at TS] [--project DIR] [--state DIR] [--json]`** (library
  `lattice/improve/verify.py`): the before/after report for a CLOSED ledger entry. It splits the
  project's event log at the closing point (the ledger `ts`, or `--at TS` for the time the change
  took effect), reports the signal's cost per occurrence before and after, rebuilds the prediction
  from the before window alone, and states a verdict (`improved`, `not_improved`, `recurred`,
  `not_enough_data`; exit 0 only for `improved`) with sample sizes, evidence event ids and blind
  spots. Costs of different units are never converted.
- **`lattice improve draft ID [--out FILE] ...`** (`lattice/improve/draft.py`): a reviewable draft
  for an OPEN, non-protected proposal (change request / config snippet with the `verify` command to
  run afterwards / module skeleton / pre-registration stub). `needs_owner` (protected) proposals,
  closed ones and unknown ids are refused; the draft is never applied, never commits, never edits
  the ledger, and `--out` inside the project or state directory is refused.
- Log and ledger formats unchanged (schema `v: 1`); `report`, `import-plan`, `ledger` outputs
  byte-identical to 7358be2. `lattice/cli.py` usage lines list the two new subcommands.

## Blind results

| Probe | Predicted | Observed | Pass |
|---|---|---|---|
| V1a real I-3 data, close 23:08:00 | improved, 3/3, 119.51 -> 58.50 s, -61.01 s (-51.0%), predicted -71.7 s, **error 14.9%** | exact | yes |
| V1b/V1c close at the second of the 3rd before-run / 1st after-run | not_enough_data (2/3 and 3/2) | exact | yes |
| V1d the ledger as really closed (23:12:00) | not_enough_data, 6/0 | exact | yes |
| V1e layer-D control | not_improved, flat (-0.9%) | exact | yes |
| V1f pattern over 4 keys | improved, 142.46 -> 81.19 s | exact | yes |
| V1g real ledger line + `--at 23:08:00` | = V1a; source `--at`; override warning | numbers equal V1a; see finding 1 | yes |
| V1h malformed `--at`; `--at` on an open entry | 3 `bad_at`; 3 `not_closed` | exact | yes |
| V2 synthetic truth (improved, not improved, worse, recurred, none after, one after, interleaved signals, units) | 11/11 rows | 12/12 sub-rows | yes |
| V3 ledger edge cases | 7/7 | 7/7 | yes |
| D1 one draft per kind | 6/6 headings in order + key lines | 6/6 | yes |
| D2 refusals (checker target, closed, unknown) | 5/5, no file written | 5/5 | yes |
| D3 safety | 0 file changes over every call; `--out` adds exactly one file; stdlib + `lattice.improve` imports; no literal copied from the data | 0 changes over 993 guarded calls | yes |
| D4 determinism | 0 differing bytes (time gap, two cwds, a state copy) | 0 over 12 cases x 10 runs | yes |
| D5 no regression | `report|import-plan|ledger` byte-identical to 7358be2 | 12 comparisons identical | yes |

**15 of 15 probes**, each deterministic probe 10 runs identical. **21 of 21 mutants killed**
(IM1-IM6 existing, IM7-IM21 new; control passes with 122 tests). **Tests:** `tests/improve` 111
passed (50 existing + 39 + 22 new); `tests/test_cli.py` 21 passed; `dogfood/studio/check.sh` PASS,
415 cases.

## Findings and post hoc

1. **V1g differs from V1a in three leaf fields, not two.** `closing_point.source`, the warning, and
   `entry.ts` (the echoed ledger line's `ts`, which is 23:12:00 in V1g's ledger by construction).
   Every number, `closing_point.ts` and the verdict are V1a's. The prereg's "in those two fields
   only" was imprecise.
2. **Why `--at` exists, shown on the real data.** The real I-3 entry was closed AFTER the measuring
   runs, so without `--at` the report can only say `not_enough_data` (V1d). The ledger `ts` is when
   the owner ran `ledger`, not when the change landed; the owner should close the entry when the
   change lands, or pass `--at`.
3. **The prediction is rebuilt, not recorded.** The log has no proposal event and the ledger no
   prediction field, so `verify` re-runs the ranker's template on the before window alone. If the
   ranker changes, old reports would change with it. Only the layer-X template predicts; other
   proposals get `no prediction recorded` and nothing is compared.
4. **Script and process notes.** The builder's V1g path-naming bug in the probe runner was fixed
   (script, not system); IM7/IM8 were re-anchored on different lines; the builder ran `git stash`
   briefly in its own worktree, spoiling one mutant run, which it discarded and re-ran.

## Orchestrator verification

| Check | How | Result |
|---|---|---|
| Full re-run from scratch | `tests/improve`, `tests/test_cli.py`, `dogfood/improve2_probes.py`, `dogfood/improve_mutants.py`, `dogfood/studio/check.sh` | 111 / 21 / 15 of 15 / 21 of 21 / PASS 415: equal to the node's report |
| Safety read | grep of `verify.py` and `draft.py` for process, network, LLM and write calls | none; stdlib + `lattice.improve` imports only |
| A real `verify` | the I-3 data in a scratch state dir with `--at` | improved, -61.01 s vs predicted -71.7 s, 14.9% |
| Lint | `ruff check` and `ruff format --check` on the 8 touched `.py` files | clean |

## What this does not show

- **One real measurement.** V1 is the single I-3 run (n=3 against 3, full separation is p=0.05
  one-sided); the other probes are synthetic logs with known truth. No real Studio plan produced
  the events (the older limitation stands).
- **A real proposal accepted end to end.** `draft` produces text; nobody applied one and then ran
  `verify`. The change-set -> rebuild-loop step, the Studio API and the web UI are not built.
- **Confounders and human time** are named in the report but not measured.

## Not done

Studio API for `verify`/`draft`, web UI for proposals, mock parity, a dogfood rule, applying a
draft through the rebuild loop, cross-project aggregation, concurrent log appends.
