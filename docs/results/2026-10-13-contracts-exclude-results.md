# Layer 3: provider-declared route exclusion: results

Date: 2026-10-13. Branch `np-exclude` (worktree `lattice-exclude`, from `next-phases` 7358be2).
Pre-registration: `docs/results/2026-10-13-contracts-exclude-prereg.md` (282eb8d, written before any
code of the change; decision 6 was amended by the orchestrator before the commit, see below). Raw
data: `docs/results/data/2026-10-13-contracts-exclude/` (`summary.json`, one JSON per probe,
`mutants.json`, `X0-regression/`). No LLM, no API key, no Docker.

## What changed

The finding of the Streak experiment (test-harness routes exported as part of the contract) is
addressed by an explicit, provider-declared exclusion list in the workspace manifest:
`[[project]] exclude = ["GET /health", "PUT /test/clock"]` (strings `METHOD /path`, normalised like
imports). It is applied once, inside the export, before hashing; the export carries a sorted
`excluded` list only when one is declared, so **without the key every export, lock and check is
byte-identical to before** (both golden locks pinned). Two CHANGED kinds are new (`exclusion_added`,
`exclusion_removed`), `bad_exclude` is a manifest error, an unmatched entry is a warning
(`exclude_unmatched`), and `export --exclude` is a repeatable CLI flag. **An import of an excluded
endpoint is always BROKEN `endpoint_missing`, pin or no pin** (a stated fact, not an observation
gap; `pin` refuses it without `--force` and `--force` does not launder it). The live check does not
replay an excluded import. Files: `lattice/contracts/{export,check,live,__main__}.py`,
`lattice/workspace.py`, one paragraph in `lattice/studio/API.md` (no version bump). The spec
language and `docs/lattice-language-rules.md` are untouched.

## Blind results

| Probe | Predicted | Observed | Pass |
|---|---|---|---|
| X0 (a)-(c) no key | exports byte-identical to both goldens (spec hashes 9bcae0e24da9, d44b738522ea); `exclude = []` same bytes; check == the committed B0 docs | all 8 byte comparisons hold | yes |
| X0 (d) old runners | `probes.py` 35/35, `streak_probes.py` 32/32 | streak 32/32; **`probes.py` 34/35**: W5's `primary_old_usable` is False | **no (finding 1)** |
| X1 job-scheduler | 18 endpoints (public 1 / token 4 / unknown 13); vs the golden lock exit 1, 2 CHANGED `exclusion_added` unrelied, 0 BROKEN; re-pin clean | exact | yes |
| X2 Streak | 24 endpoints (public 3 / token 21); 2 CHANGED unrelied; re-pinned: exit 0, the 2 B0 gaps | exact | yes |
| X2b normalisation | spelling variants and `{id}`/numeric forms export identically | exact | yes |
| X3 import of an excluded route | pinned before: BROKEN + 1 CHANGED, pin refused; `--force`: still BROKEN; pinned after: refused, `--force` writes, BROKEN | all 6 rows | yes |
| X4 list changes | every change visible (CHANGED kinds; hash moves; `version` unchanged) | exact | yes |
| X5 validation | 8 bad values: one `bad_exclude`, validate 1, check 2 | exact | yes |
| X6 unmatched entry | warning, not an error | exact | yes |
| X7 parity | MCP and API == CLI static-only JSON, 6 of 6 | 6 of 6 | yes |
| X8 no key | 18 of 18 workspace outputs byte-identical to 7358be2 | 18/18 | yes |
| X9 live | (a) 7 replayed; (b) 6 replayed, the excluded import in `not_replayed` | exact | yes |
| S safety | job-scheduler and Streak untouched | untouched | yes |

**Mutants:** control PASS (258 passed); **23 of 23 killed** (CM1-CM11 unchanged, XM1-XM12 new).
**Tests:** `tests/contracts tests/workspace tests/mcp tests/studio`: **835 passed, 1 skipped**
(baseline 791 + 44 new). `dogfood/studio/check.sh`: PASS, 415 cases, 0 findings.

## Findings and post hoc

1. **X0 (d) failed on an environmental precondition, not on the change.** `probes.py` W5 required
   the primary checkout `/Users/vivekgade/Documents/lattice` to be at `c0cdefc` (experiment 1's old
   code). That checkout is `next-phases`, which moved on when layers and results were merged, so the
   check could not hold any more. The same failure occurs on a pristine 7358be2 (control saved as
   `X0-regression/W5-at-7358be2-control.json`); every other W5 check passes, including 6/6
   byte-identical against the detached `c0cdefc` worktree. This is a defect of the earlier runner (a
   check coupled to a mutable checkout), found because a later change re-ran it. It is fixed in a
   separate post-hoc commit (see below) and reported here as a blind miss.
2. **Decision 6 was changed before the commit.** The author's draft made an import of an excluded
   endpoint a GAP when the pin did not hold it (so a consumer pinned after the exclusion exited 0
   while relying on a route the provider disowned). The orchestrator changed it to always BROKEN
   and added mutant XM12 for it. This departs from "BROKEN needs the pin", which the owner kept for
   observed facts; an exclusion is a stated fact.
3. Prereg slips found by the builder, recorded not edited: section 4 says "11 of 11" but lists 12
   mutants (all 12 run); X9(b)'s text is garbled (implemented as 17 endpoints, 1 BROKEN, 0 CHANGED).
4. `streak_probes.py` could not run with any change under `lattice/` (its preflight refused); it
   gained `--allow-code-diff`, which records the diff instead of refusing (decision 14).
5. **Post-hoc fix of finding 1 (commit after 84cff6b; the blind numbers above stay as measured).**
   `dogfood/contracts/probes.py` W5 no longer requires the primary checkout to be at `c0cdefc`
   (`primary_old_usable` is not a pass check; the primary is still recorded in the data). One old
   reference at `c0cdefc` (the detached `lattice-contracts-base` worktree) is required and compared
   byte for byte, as before. Re-run by the orchestrator: `probes.py --runs 10` **35/35**;
   `exclude_probes.py --only X0` **pass** (goldens, `exclude = []`, check == B0 docs, both old
   runners). Residual risk, disclosed: W5 now depends on the `lattice-contracts-base` worktree
   existing; deleting it makes W5 fail with `old_reference_usable` false, loudly.
6. No script corrections to any prediction; the previous node run died of an expired login after
   one partial edit (`export.py`), which the restart reviewed and kept.

## Orchestrator verification

| Check | How | Result |
|---|---|---|
| Whole verification re-run from scratch | tests, `exclude_probes.py --runs 10`, `streak_probes.py --allow-code-diff`, `probes.py`, mutants, dogfood | 835 passed / 11 of 12 (X0 d only) / 32 of 32 / 34 of 35 (W5 only) / 23 of 23 / PASS 415 cases: identical to the node's report |
| Goldens and language | `git diff --quiet 7358be2` on both golden dirs, `lattice/spec`, the language rules | identical |
| Real repos | `git status --short` before and after | ` M uv.lock`, ` M studio.toml` |
| Lint | `ruff check` and `ruff format --check` on the 10 touched `.py` files | clean |

## What this does not show

- The exclusion list lives in the workspace manifest, not in the provider's repository: a second
  workspace could consume the same provider without it. A later move into the provider's own repo
  (a file the provider owns) is the real fix and is not built.
- It excludes by exact `METHOD /path`; no prefix or glob. A wrong entry silently drops a real
  endpoint from every consumer's contract (it is visible: `excluded` is in every export and lock).
- Two pairs; live checking of a Go/MySQL provider is still not built.
