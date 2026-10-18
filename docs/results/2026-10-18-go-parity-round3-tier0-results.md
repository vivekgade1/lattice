# Go parity, round 3, Tier 0: the S3 re-score on the round-2 library (node T0)

2026-10-18. Pre-registration: `docs/results/2026-10-18-go-parity-round3-prereg.md`, sections 1.2 (G-c), 2 (K7), 5.1, 7, 8, 10 and
Amendment 1 (Q5: K7 stays runner-only; Q7: Tier 0 runs on the round-2 library before any G3 library edit, and that is the scored value).
No model was called and no API key was used. No library file was edited (the T0-FREEZE row proves `lattice/` is byte-identical to
978b468). Worktree `/Users/vivekgade/Documents/lattice-g3`, branch `np-go-round3`, HEAD `f245a5c`. Nothing is committed.

## 1. Verdict

**Tier 0 passes: 5 of 5 rows, every prediction met, no miss.** The round-2 library's S3 import is unchanged by the runner fix: the
two rows that crashed in round 2 (PI4-3, PR3-3) were a runner defect (G-c), not a library defect.

| row | prediction (p, fixed in the prereg) | observed (scored run) | gate | pass | prediction met |
|---|---|---|---|---|---|
| T0-PI4 | 35 seed-verified; 34-35 still verified on `variant2` | **35 seed-verified, 35 still verified**, fraction 1.0000, lost `{}` | >= 0.95 | **PASS** | yes |
| T0-PR3 | identical verdict map after round 2's correction map; 63 = 63; 0 corrections | **63 = 63 items, 0 corrections, 0 differences** | identical | **PASS** | yes |
| T0-CTL | in-process `machinery()` (root on `sys.path`) == the CLI import's final verdicts on the seed; 63 items, 35 verified | **identical; 63 items, 35 verified** (both sides), 0 differences | identical | **PASS** | yes |
| T0-FORM | the same on a scratch copy of S3 whose `studio.toml` uses the dotted form; identical to T0-CTL | **identical within the dotted copy (63 items, 35 verified, 0 differences) and identical to the bare-form import (0 differences)** | identical | **PASS** | yes |
| T0-FREEZE | `freeze.sha256` (21), `s3runner.sha256` (3), `s3.freeze.sha256` (8 files) and the runner's own entry, all OK before any metric | **all OK** (21 / 3 / 9 entries, 0 mismatches; own entry listed; 8 archive copies byte-equal; `lattice/` identical to 978b468) | all | **PASS** | yes |

Misses: **none**. Fixtures changed during the run: `[]`. docker / claude shim calls: `[]` (no Docker was started; no container carries
`lattice.owner=g3-t0`, and none exists).

Round 2's scored S3 import (`docs/results/data/2026-10-18-go-parity-gaps-round2/scored/s3_import_report.json`) and this run's bare import
have **identical item verdict maps** (63 items: 35 verified, 27 unexercised, 1 unclear). The round-2 library is deterministic on S3 and
the PI3-3 share (35/63 = 0.5556, below the 0.60 gate) is therefore unchanged; PI3-3 is a Tier 1 (K3) row, not a Tier 0 row.

## 2. What was run

```sh
WT=/Users/vivekgade/Documents/lattice-g3; cd "$WT"
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv PYTHONPATH="$WT" PYTHONDONTWRITEBYTECODE=1 PATH=/opt/homebrew/bin:$PATH
shasum -a 256 -c docs/results/data/2026-10-18-go-parity-round3/t0-freeze.sha256        # both OK
uv run python dogfood/go_parity_s3r.py --kind scored \
  --out docs/results/data/2026-10-18-go-parity-round3/t0 --scratch "$SCRATCH/scored"   # run_id 2eaecdb33b1d
```

Wall: fixture build 0.4 s (cached), CLI import 7.9 s (bare) and 8.1 s (dotted), whole run about 35 s. `lattice.__file__` =
`/Users/vivekgade/Documents/lattice-g3/lattice/__init__.py`; the import subprocess resolves `lattice` to the same path. Go 1.26.2, Python
3.14.0.

## 3. The runner (K7) and how each row is computed

`dogfood/go_parity_s3r.py` (sha256 `7dc58c193f8f452d0f8d9427f21846e1af5ffa68a062528161552e80aaea1cc8`) is new and imports the frozen
round-1 and round-2 runners read-only for helpers only (`result`, `Ctx`, `make_shims`, `go_build`, `snapshots`, `load_candidate`,
`verdict_map`, `report_of`); it does not import their `main()`. Round 2's `machinery()` is copied (disclosed, prereg risk 7) with one
change and one generalisation:

* **K7**: `add_import_root(root)` puts the import root on `sys.path` exactly as `cli.py:161-162` and `rules.py:939-940` do, before
  `go_rules.harness_factory` is called. The repository root is also added, so the dotted form `tests.spec.gotickets_harness` resolves as
  it does in the CLI process. The library is untouched (Q5).
* The root and the cache slot are parameters, so the same code serves the bare form (`tests/spec`) and the dotted-form scratch copy.

**Diagnostic, not a metric (post hoc, labelled).** With `add_import_root` replaced by a no-op, `machinery()` on the committed S3 raises
`ModuleNotFoundError: No module named 'gotickets_harness'`, round 2's exact crash (`t0-k7-off-diagnostic.txt`). With K7 on, the same call
succeeds. This confirms the G-c reading: the crash was the runner, and the library's different resolution of a bare harness module in
process and in the CLI (the finding F) remains a reported finding.

Row definitions (the pass rules are the prereg's; where it left a reading open this runner fixed one **before** the scored run):

* **T0-PI4** is round 2's PI4-3 code: the imported `candidate_spec.py` is probed by layer R on `seed` and on `variant2` with the
  import's callers and marks; every seed-verified item must still be verified, fraction >= 0.95.
* **T0-PR3** is round 2's PR3-3 code: the `seed` rerun's verdict map equals the import's final verdicts after `corrections[]` maps item
  ids (here 0 corrections).
* **T0-CTL** (reading): the same rerun against the CLI import's final verdicts **literally** (no correction map), the control that the
  in-process machinery agrees with the CLI. It overlaps T0-PR3 by construction when there are 0 corrections; it differs in the literal
  comparison and in carrying the counts (63 items, 35 verified) in its prediction check.
* **T0-FORM** (reading): a scratch copy of `tests/spec/gotickets` whose `studio.toml` has every `gotickets_harness:` reference in the dotted
  form `tests.spec.gotickets_harness:` (3 references, converted exactly; the committed fixture is never edited); its CLI import and
  in-process rerun must agree, **and** its CLI final verdicts must equal the bare import's.
* **T0-FREEZE** is evaluated first, aborts the whole run before any metric on any mismatch, and cannot be switched off (no flag; the
  `--freeze` option only accepts the fixed `t0-freeze.sha256`; `--base` is accepted and ignored for the prereg's command line).

## 4. T0-FREEZE in detail

| check | result |
|---|---|
| `freeze.sha256` | 21 entries, each re-hashed, 0 mismatches; the list file itself equals the pinned sha256 `800fc1f7131ca996...` (the runner pins it) |
| `s3runner.sha256` | 3 entries, 0 mismatches; list file pinned (`35524804d13e541a...`) |
| `s3.freeze.sha256` | **9 entries = the 8 S3 files plus `FILES.txt`**; the 8 are re-hashed in the worktree and also against the extracted author archive `archive-frozen-sets-2026-10-18/s3-gotickets-author-out.tgz` (8 copies byte-equal); `FILES.txt` exists only in the archive and is hashed there; list file pinned (`0a27192ddd13b9ba...`) |
| `t0-freeze.sha256` | lists `dogfood/go_parity_s3r.py` (the running script) and `tests/spec/test_go_parity_s3r.py`; sha256 of this file `4c7508e9eba61fa7c08966d08648fce8ef1a15f263d7994b9861b52fd84fed45` |
| library | `git diff 978b468 -- lattice dogfood tests` is empty and no untracked file lies under `lattice/` (extra to the prereg: it makes "the round-2 library" a checked fact, not an assumption) |
| environment | `lattice.__file__` and the import subprocess's `lattice.__file__` lie inside the worktree |

Deviation from the prereg's wording, disclosed: the prereg says "`s3.freeze.sha256` (8 files)"; the committed list has 9 lines, the 8
files and `FILES.txt`. The runner requires all 9 and the entry count, so the list cannot be shortened.

## 5. Process record

* **Ledger.** `docs/results/data/2026-10-18-go-parity-round3/ledger.jsonl` (fixed path in the runner) holds 4 entries: a `shakedown` run
  (`a8f7889674fa`, started and completed) and the `scored` run (`2eaecdb33b1d`, started and completed). A second `--kind scored` attempt
  was **refused** by the ledger ("arm T0 already has a scored run (completed)") before anything ran and wrote nothing. Ledger sha256 after
  the scored run: `e653bb19c84263163bf11486cf2951c0e47d568cea70a15d702a74e600be53d5`.
* **A shakedown preceded the scored run** (disclosed; ledger kind `shakedown`, same runner bytes, same freeze file). Reason: a crash
  inside a row counts as FAIL (prereg 10.5), so the row code had to be exercised first. The shakedown and scored values are identical
  in every row (`t0-shakedown/probes-t0.json`), and the scored values are the ones reported. Tier 0 is not blind (prereg section 1: S3 is
  development data and these rows were registered in round 2); the shakedown therefore costs no blindness.
* **No re-run, no edit after the scored run.** The runner and test bytes in the scored ledger entry are the bytes frozen in
  `t0-freeze.sha256` and the bytes shown below.
* **Unit tests.** `tests/spec/test_go_parity_s3r.py` (35 tests, no Docker, no Go build) passes (`t0-pytest.txt`): the scoring
  functions at their boundaries, every kind of freeze drift (edit, delete, missing entry, running script not listed [RM2 in miniature],
  wrong entry count, wrong pinned list hash, absent list), the archive checks, `library_unchanged`, the `lattice.__file__` guard, every
  ledger rule (scored once, rerun once after a pre-metric crash only with identical bytes, verify once, shakedown free), K7 and the dotted
  copy, and the absence of any flag that skips a check. `ruff check` and `ruff format --check` are clean (`t0-lint.txt`).

## 6. Hashes

| file | sha256 |
|---|---|
| `dogfood/go_parity_s3r.py` | `7dc58c193f8f452d0f8d9427f21846e1af5ffa68a062528161552e80aaea1cc8` |
| `tests/spec/test_go_parity_s3r.py` | `2a7b7dd3dff5cd773e2c03a93be10de700124382cc969ee4bc3128b75087fa3b` |
| `docs/results/data/2026-10-18-go-parity-round3/t0-freeze.sha256` | `4c7508e9eba61fa7c08966d08648fce8ef1a15f263d7994b9861b52fd84fed45` |
| `docs/results/data/2026-10-18-go-parity-round3/ledger.jsonl` (after the scored run) | `e653bb19c84263163bf11486cf2951c0e47d568cea70a15d702a74e600be53d5` |

## 7. What this does not show

* It scores the **round-2 library** only. It says nothing about the G3 library; the same rows on the G3 library are separate rows
  (Amendment 1 Q7), e.g. PS-PI4-3 and PS-PR3-3 in Tier 1.
* PI3-3 (share 0.5556 < 0.60) is not re-scored here and still fails on the round-2 library.
* The independent verifier (V) has not yet re-run Tier 0: `kind verify` is unused in the ledger and the ledger allows exactly one.
* The runner's dependence on round-2's frozen files means it cannot run on a tree where K1-K6 are applied (the 21-entry freeze and the
  `lattice/` identity check fail by design); the verifier must run it on the round-2 library (this commit's tree before B1 merges).

## 8. Data (under `docs/results/data/2026-10-18-go-parity-round3/`)

`t0/probes-t0.json` (the scored rows, the freeze record, timings, shim calls), `t0/t0_import_{bare,dotted}_{report.json,candidate_spec.py,
profile.py,proposals.json,go_policy.json}`, `t0-shakedown/probes-t0.json`, `t0-k7-off-diagnostic.txt`, `t0-freeze.sha256`, `ledger.jsonl`,
`t0-pytest.txt`, `t0-lint.txt`.
