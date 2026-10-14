# Multi-project slice 4a (cross-project impact preview): results

Date: 2026-10-13. Branch `np-impact` (worktree `lattice-impact`, from `next-phases` 67189af).
Pre-registration: `docs/results/2026-10-13-contracts-impact-prereg.md` (1e4af63, written before any
code of the change). Raw data: `docs/results/data/2026-10-13-contracts-impact/` (`summary.json`, one
JSON per probe, `mutants.json`, `I8-regression/`). No LLM, no API key, no Docker.

## What was added

`lattice contracts impact PROVIDER --candidate REF|PATH [--workspace PATH] [--json]`, the MCP tool
`contract_impact` (listed only with a workspace, read-only) and `GET /api/contracts/impact`
(API v1.18, mocked; dogfood rule R95 with one scenario). For each consumer with an opted-in link to
PROVIDER it runs the layer-3 evaluation with the provider's export replaced by the candidate's and
the consumer's pin unchanged, and labels the row in Studio's vocabulary: `contradiction` (a declared
reliance would be BROKEN, an excluded endpoint included), `regression` (no BROKEN but a relied
CHANGED item), `unaffected`. Each row also carries `current_label` (the same label for the
provider's current export against the same pin: it exposes a stale pin). Exit 0 / 1 (a
contradiction) / 2 (cannot run); a `regression` alone exits 0. **Advisory and read-only:** the
candidate (a git ref, materialised from raw blobs without a checkout, or a directory) is exported
with the provider's manifest `spec` and `exclude`; nothing is written to any project, lock, state
or ledger, and nothing waits on the answer. Direct opted-in links only. One edit to layer-3 code:
`git_version` passes `--no-optional-locks`, so `check` and `export` no longer rewrite a provider's
`.git/index` (no output changes; the goldens are unchanged).

## Blind results

| Probe | Predicted | Observed | Pass |
|---|---|---|---|
| I0a/I0b identity | candidate = HEAD: `unaffected`, equal to `check`, exit 0 (job-scheduler 1 gap, Streak 2) | exact | yes |
| I0c stale pin | provider at D2, candidate HEAD: contradiction, `current_label` contradiction, = committed D2 | exact | yes |
| I1 job-scheduler D1-D10 | D1-D5 contradiction; D8, D9 regression; D6, D7, D10 unaffected (5 / 2 / 3); rows = the committed D-series | 10/10 labels and exits, 10/10 rows equal | yes |
| I2 Streak D1-D13 | D1-D7 contradiction; D10, D11, D13 regression; D8, D9, D12 unaffected (7 / 3 / 3) | 13/13, rows equal | yes |
| I3 controls (12) | unaffected, except the excluded-import control (contradiction) | 12/12 | yes |
| I4 several consumers | exactly one contradiction when only one is hit; consumers without an opted-in link not listed; `no_pin` an error row; unknown provider exit 2 | rows a-i exact | yes |
| I5 candidate forms | ref, directory, unknown ref, failing candidate; provider clone byte-identical | 8/8 | yes |
| I6 safety | over 1220 traced calls: 0 files changed, 0 temp left, 0 connects, 0 disallowed argv, 0 LLM variables | exact | yes |
| I7 parity | MCP and API == CLI on D2 and I4-a; mock same keys and types | 4/4 | yes |
| I8 no regression | workspace outputs, export, check and pin lock byte-identical to 67189af; runners, pytest, dogfood pass | byte-identical 10/10; the suites below | yes |
| T cost | median <= 10 s (predicted <= 2 s / <= 3 s) | 0.67 s (job-scheduler), 0.66 s (Streak) | yes |

**12 of 12 probes**, no prediction differed; 10 identical runs each. **Mutants: 14 of 14 IMP
killed** (control 298 passed; with CM1-CM11 and XM1-XM12, 37 of 37).

## Findings and post hoc

1. **"Regression alone exits 0" hides real breaks the layer cannot classify.** Of the five
   `regression` rows, three are test-edit noise (D9, D11, D13) but two are real breaks (D8 and D10:
   a new required request field), which layer 3 can only show as a relied CHANGED. The JSON
   `counts` lets a caller gate on `regression`. This is the prereg's named risk, observed.
2. **A stale pin looks like a contradiction even for HEAD** (I0c): that is why `current_label`
   exists; a preview cannot tell "the candidate breaks it" from "it was already broken".
3. **Layer-3 code touched once** (`--no-optional-locks`), disclosed above; and
   `tests/mcp/test_contract_check.py`'s tool-list assertions now end with `contract_impact`.
4. **Script correction:** a development-time extra check ("candidate.resolved equals the committed
   run's git_ref") was impossible (commit messages carry the clone name, so shas differ with
   identical trees); replaced by "resolved is the commit fetched as dN". No pre-registered rule
   changed.
5. **Orchestrator fixes** (outside the node's file list or missed by it): `lattice/cli.py` usage
   lines and the layer-4 row of `docs/design/lattice-multi-project-phase.md`; one 101-column line in
   the dogfood spec's R95 `Unchecked` text (split without changing its text; the dogfood gate was
   re-run).

## Orchestrator verification

| Check | How | Result |
|---|---|---|
| Predicted labels | derived from the committed D-series data with the stated mapping before the prereg commit | equal (5/2/3 and 7/3/3, all controls unaffected) |
| Module read | `lattice/contracts/impact.py` read against the prereg | baseline is the pin; candidate exported with manifest spec and exclude; only read-only git and export children |
| Full re-run from scratch, two parallel streams | pytest `tests/contracts tests/workspace tests/mcp tests/studio`; `dogfood/studio/check.sh`; `impact_probes.py --skip-regression`; `probes.py`; `streak_probes.py --allow-code-diff`; `exclude_probes.py`; IMP mutants | 900 passed, 1 skipped; PASS 422 cases; I0-I7 and T pass; 35/35; 32/32; 12/12; 14/14 killed |
| `--skip-regression` run | the node's I8 sub-checks (suites) were skipped by my flag and run separately | the runner scores I8 FAIL in that mode by design; not a defect |
| Real repos | `git status --short` before and after | ` M uv.lock`, ` M studio.toml` |
| Lint | `ruff check` on the 14 changed files; format check on the new files | clean after the one-line fix |

## What this does not show

- **Static only**: the candidate's spec, not the provider's behaviour; a `live` link is evaluated
  statically and says so.
- **Spec-level signal**: relied is endpoint-level, so an unrelated field change on a relied
  endpoint reads as `regression`; required-ness, types and bounds are invisible (D7, D8, D10).
- **Direct links and workspace members only**: consumers outside the workspace and transitive
  consumers are invisible.
- **Labels borrow Studio's vocabulary but are not Studio's change impact** (a different object).
- Two provider pairs, builder bias (the builder read the predictions), no real change set
  involved: the candidate is a ref or path, not a Studio change object.

## Not done (layer 4 remainder)

4b: accepting a provider change opens a linked pending question in each affected consumer (writes
into consumer state); 4c: consumer-side change drafting; transitive contract impact; Studio
change-set integration and any web UI.
