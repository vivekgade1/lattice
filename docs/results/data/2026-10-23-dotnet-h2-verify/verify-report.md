# H2 independent verification (node V, prereg section 9.6)

Date 2026-10-23. Verifier V (opus). Clean worktree `/Users/vivekgade/Documents/lattice-h2-verify`, branch `np-h2-verify`, made from
`develop` at `ffa341ec`. Python `/Users/vivekgade/Documents/lattice/.venv/bin/python` with `PYTHONPATH` set to the worktree; .NET SDK
10.0.401; `ANTHROPIC_API_KEY` unset for the run. No model call, no API key. No frozen file was edited. Nothing was fixed, tuned or
re-scored.

## 1. Freeze and inputs manifests

`dogfood/dotnet_h2_probes.py --verify-freeze-only` and `dogfood/dotnet_h2_mutants.py --verify-freeze-only`, both exit 0:

| manifest | list sha256 | entries | mismatches | must-listed | running script listed |
|---|---|---|---|---|---|
| `freeze.sha256` | `ae6cde4135e6b26ab8264343701aacc1c5a5cef2ad798955557e1743e834b289` | 428 | 0 | 428 | yes (both runners) |
| `h2-inputs.sha256` | `c9486df64a7f8ccc67f22a66c18a4d74cf930590b672952ebc100752bbb85010` | 19 | 0 | n/a | n/a |

Both list hashes equal the ones in the scored ledger lines.

## 2. The probes verify run (arm H2:H)

One run, `--kind verify --out <worktree>/docs/results/data/2026-10-23-dotnet-h2-verify/probes --scratch
/Users/vivekgade/Documents/h2-verify-scratch/probes` (a fresh scratch directory; the scored run used `h2-scored-scratch`). It ran from the
archived inputs (`h2.tgz`, `h2-truth.json`), exit 0, run id `ff57647674e8`, runner sha256 `8be0b38a...ca795` (the same as the scored run's),
`target_unchanged: true`. Output: `probes/probes-h2.json` (sha256 `c21f4736f89e13496c63ec2a587e4e856ae233c315ebc8629827a0a1e45668fa`),
`probes/evidence/*.json` (20 files) and `probes-stdout.txt`.

### 2.1 Verdicts, row by row, against `2026-10-23-dotnet-h2-scored/probes/probes-h2.json`

| row | gated | scored pass | verify pass | ran (both) | observed |
|---|---|---|---|---|---|
| H2-ROUTES | yes | True | True | yes | identical |
| H2-AUTH | yes | False | False | yes | identical |
| H2-TABLES | yes | False | False | yes | identical |
| H2-WIRE | yes | False | False | yes | identical |
| H2-UNREAD | yes | True | True | yes | identical |
| H2-BOUNDS | yes | True | True | yes | identical |
| H2-RAISE | yes | False | False | yes | identical |
| H2-PI3 | yes | False | False | yes | noise only (2.2) |
| H2-PI7 | yes | True | True | yes | identical |
| H2-PA1 | yes | False | False | yes | identical |
| H2-IMPL | yes | False | False | yes | noise only (2.2) |
| H2-WF | yes | False | False | yes | identical |
| H2-JUDGE | yes | True | True | yes | identical |
| H2-OWN | reported | False | False | yes | noise only (2.2) |
| H2-PI4 | reported | False | False | yes | noise only (2.2) |
| H2-PI5 | reported | False | False | yes | noise only (2.2) |
| H2-PR3 | reported | False | False | yes | noise only (2.2) |
| H2-PA0 | reported | False | False | yes | noise only (2.2) |
| H2-DET | reported | True | True | yes | identical |
| H2-X | reported | False | False | yes | noise only (2.2) |

**Verdicts that differ: 0 of 20 (0 of 13 gated, 0 of 7 reported).** Summary identical: gated passed 5/13 (`H2-ROUTES`, `H2-UNREAD`,
`H2-BOUNDS`, `H2-PI7`, `H2-JUDGE`), gated failed 8, not run none, reported only `H2-DET` passing.

### 2.2 Observed fields that differ, and why they are noise

Method: a recursive field-by-field diff of the two `probes-h2.json` files and of each pair of `evidence/*.json`, after replacing the
worktree roots (`lattice-h2-run` / `lattice-h2-verify`) and the scratch roots (`h2-scored-scratch` / `h2-verify-scratch`) with one token.
What still differs:

- `kind` (`scored` / `verify`), `run_id` (random: `2f1badee6114` / `ff57647674e8`): by construction.
- `timings.build_h2_s` (1.0 / 0.8) and `timings.import_h2_main_s` (3.819 / 3.846): wall-clock timings.
- Absolute paths (`lattice_file`, `freeze.file`, `inputs.file`, `h2`, `h2_truth`, `shim_calls[*]`, evidence paths): the worktree and
  scratch roots only; equal after the replacement above.
- `observed` and `evidence.traceback` of the 8 rows that import H2 (`H2-PI3`, `H2-IMPL`, `H2-OWN`, `H2-PI4`, `H2-PI5`, `H2-PR3`,
  `H2-PA0`, `H2-X`): the text is `RuntimeError: the import of h2 failed (exit 1): ` plus the **last 500 characters** of the importer's
  stderr (`dotnet_probes.import_dir`). That stderr contains the worktree path, which is 3 characters longer in the verify worktree, so the
  500-character window starts 3 characters later (`=self._schema)` / `lf._schema)`). Both strings are 548 characters, both end with the
  same frame (`lattice/importer/suite.py`, line 484, in `sql_seed`, `conn.executemany(...)`) and the same error,
  `sqlite3.OperationalError: table sessions has no column named UserId`; the last 440 characters are equal byte for byte. Truncation
  window shift from path length: noise.

No other field differs; the 12 evidence files of the rows that do not import H2 are identical after the path replacement.

## 3. The mutants arm (H2:HM)

Not run. The arm's scored run ended `crash_after_metric` (ledger), so it has no completed scored run and its verify is not eligible
(prereg section 7). Per the dispatching instruction, V did not launch `--kind verify` or `--kind rerun` on `dotnet_h2_mutants.py`;
there is therefore no refusal text to record (no line was written to `refused.jsonl` or to the ledger). For the record only, read from
the frozen `dotnet_probes.ledger_refusal` (not executed): a verify would be refused with `arm H2:HM has no completed scored run to
verify`, and a rerun with `the scored run ended crash_after_metric, not crash_before_metric`.

## 4. Ledger (`docs/results/data/2026-10-23-dotnet-h2/ledger.jsonl`, sha256 after this run `a312dfad2cc839ea9f59a93515d34fe91777f995f21f41d764e67ef85df50c7d`)

| arm | kind | status | run id | runner sha256 | freeze / inputs sha256 |
|---|---|---|---|---|---|
| H2:H | scored | started, completed | `2f1badee6114` | `8be0b38a...` | `ae6cde41...` / `c9486df6...` |
| H2:HM | scored | started, crash_after_metric | `0fce5704643a` | `93bcc424...` | `ae6cde41...` / `c9486df6...` |
| H2:H | verify | started, completed | `ff57647674e8` | `8be0b38a...` | `ae6cde41...` / `c9486df6...` |

Six lines, as expected: H2:H scored started/completed, H2:H verify started/completed, H2:HM scored started/crash_after_metric only. No
`refused.jsonl` exists.

## 5. Notes

- The H2 import failure is deterministic and identical across runs: the same `sqlite3.OperationalError: table sessions has no column named
  UserId` from `lattice/importer/suite.py` `sql_seed` that crashed the mutants arm's control import in the scored run also fails the 8
  probes rows that import H2, in both the scored and the verify run. Those rows are FAIL (prereg 10.3: anything that raises is a FAIL, not
  NOT RUN), in both runs.
- Cause not investigated beyond reading the error (V verifies, does not localise or fix).
