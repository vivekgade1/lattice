# Typed rule conditions (Spike H): the Streak arm, scored run (strict G)

2026-10-18. Worktree `/Users/vivekgade/Documents/lattice-ha`, branch `np-h-arm`. Runner `dogfood/forms_probes_streak.py`
(sha256 `93f3262e...6ac`, frozen in `data/2026-10-18-typed-conditions-streak-arm/FREEZE`, `shasum -c` OK before and after:
PH). Set: author-written Streak set, manifest `f8727792...c1655`, 58 files; Streak `a0679e9`. Registered in
`2026-10-17-typed-conditions-prereg.md` 3.3; amended by the "Streak arm" and "library fix" amendments at the end of
`2026-10-18-typed-conditions-round3-prereg.md`. Data: `data/2026-10-18-typed-conditions-streak-arm/scored/` (`streak-strict.json`
= the 16 scored rows, `raw.json`, `raw/*.json.gz` = the CD reports). Nothing was committed, staged or merged; no model
call, no API key; nothing written into `~/Documents/streak` or the main checkout. Labels: **(m)** measured here, **(p)** the
registered prediction. The PS rows are report-only as registered: they do not change the Spike H gate verdict.

Ledger `~/.lattice/spike-h-streak-ledger.json`: one `strict` run, status `scored`, no re-run used. Wall time 2417.5 s (40.3 min).
G variant: `strict` (the library's generated elements as generated, nothing dropped). 43 forms usable, none raises, 10 generated
invariants and 92 generated scenarios, the effective spec validates, 0 invariants dropped.

## 1. Summary

The arm ran to the end. The deterministic and Docker-free rows hold (PS2, PS3, PSx, PG4, PH). The two agreement rows have
their registered percentage bands met but their registered pass rules **failed**, and the cause is one thing: on the clean app
`ok`, the generated check of form F13 fails (see 3). 28 of the 30 `G != H` cells with `H == T` are that one false positive; the
other 2 are the registered `form_gap` cells. Two library refusals are missing (one shape, see 4). The not-expressible share is
outside its predicted band.

## 2. Rows: predicted vs observed

| row | prediction (p) | observed (m) | status |
|---|---|---|---|
| PS0 `forms coverage` | formed 22-45 (15-30%); not_expressible 40-70; `time` and `cross_request` the largest | scope 146. formed **23** (15.8%, in band); not_expressible **123** (OUT of band, 40-70); english 0. Largest categories: time 32, other 21, response_shape 18, cross_table 13, other smaller; cross_request is 7. Library classification equals the author's rule by rule (0 mismatches). Full coverage only for R14, R56, R77, R78 (4); 19 partial | REPORTED. Two misses: not_expressible band, and the "time and cross_request largest" claim |
| PS1 G == H | 85-100%; pass: no cell with G != H and H == T | **654/690 = 94.8%** (in band). 36 cells G != H. **30 cells G != H with H == T** (28 R31 + R28 and R55 form_gap) | **FAIL** (pass rule) |
| PS1t G == T | 90-100%; pass: 100% outside K6, K7 and form_gap | **659/690 = 95.5%** (in band). **28 cells outside the exempt groups are wrong**, all R31 (G true, H false, T false) on the 28 apps whose R31 truth is "holds" | **FAIL** (pass rule) |
| PS2 | PT2f; 50 vs 60 pair P9 determined; candidates | 24 pairs, precision 1.0, recall classes a-d 10/10, class e 3/3, 0 raises, 0 items outside the pair, 0 false pos. P9 (F29, F111) found as class b. `stated_twice` gives exactly the one class-b item (F29, F44, witness 50 counted rows); `limit50` and `limit60` none; `impact --tiers 23` gains exactly that item in `tier2.determined` | **PASS** |
| PS3 | 10/10 byte-identical but `Await` timing | 10/10 identical to the first (0 differ, 0 Await-only, 0 path/time leaks); `forms generate` x10 gives 1 distinct output. Each run has the same 1 finding and verdict `fail` (the F13 false positive), so the determinism is of a failing report | **PASS** |
| PSx | 30-60 s per run; gate <= 180 s | 10 `ok`+G runs, wall 48.3, 48.4, 49.1, 51.0, 51.3, 53.8, 55.5, 56.3, 56.5, **63.6** s; median checker total 52.5 s (in band); one run is above 60 s. Max over all 43 runs 105.0 s. No-G baseline here 42.3, 40.9, 35.0 s (the earlier node measured 28-31 s: the machine was shared with other Docker nodes) | **PASS** (gate 180 s) |

Extra reported rows:

| row | observed (m) | status |
|---|---|---|
| PS1o (G fails exactly what T marks violated on `ok`) | T marks nothing violated on `ok`; G fails **R31** on `ok` (F13) | FAIL |
| PS1k (G kills a superset of H's mutants) | H kills 25 of 29, G kills 22 of 29; G-only 0; **H-only 3**: `m_name_taken_case_sensitive` and `m_patch_habit_any_owner` (both registered `form_gap`) and `m_push_token_max_4097` (G fails R31 already on `ok`, so the mutant causes no flip) | FAIL |
| PS1layer | both fail in 26 cells; same layer set in 23; layer-D agreement 1.0 (25 of 25 where H includes D) | REPORTED |
| PS-H (H as run here, CD with G in the same run, vs the author's `h_verdicts.json`, ACD without G) | 690 cells compared, **0 differ**. H vs truth disagrees in 7 cells (below) | REPORTED |
| PS-PG4 (compile, validate) | 10 invariants compile (sqlite, mysql), 92 scenarios validate, 0 L011/L012, effective spec validates, 0 raise, no self-check diagnostics | PASS |
| PS-PG5 (invalid forms refused, valid accepted) | 26 of 27 invalid refused with the exact code; all 43 valid forms accepted; **I2 accepted** | FAIL |
| PS-PNX (not-expressible attempts refused F001 or F017) | 15 of 16 attempts refused; **N100 accepted** | FAIL |
| PH (hash discipline) | freeze file OK before and after; runner, set manifest, Streak commit as constants. `library_vs_freeze3`: `lattice/spec/forms.py` differs from the round-3 freeze (the disclosed fix, finding one) | PASS |
| containers | 0 containers left with label `lattice.owner=h-streak-arm` | PASS |

### Groups listed apart

* **K6 cells** (R37, R39, R42, every one of the 3 x 30 = 90 cells): one is violated, R37 x `m_platform_unchecked`; G fails it
  (G true, H false, T true: G correct, H misses). K6 misses (G passes a violated K6 cell): **0**.
* **K7 cells** (`db_unchecked:` note): none.
* **`form_gap` cells** (3, registered "a correct G misses these by construction"): R28 x `m_patch_habit_any_owner` (G false,
  H true, T true), R55 x `m_name_taken_case_sensitive` (G false, H true, T true), R60 x `m_description_max_499` (G false,
  H false, T true). G misses all three as registered; H catches two.

### Every G != H cell outside the form_gap group (33 cells)

| rule | cells | G / H / T | reading |
|---|---|---|---|
| R31 | 28 apps (all but `m_platform_unchecked`, `m_push_token_max_4097`) | true / false / false | **G false positive**, F13 (section 3) |
| R33 | `m_delete_device_any_owner` | true / false / true | G catches, H misses |
| R37 (K6) | `m_platform_unchecked` | true / false / true | G catches, H misses |
| R60 | `m_name_max_101`, `m_target_week_max_8` | true / false / true | G catches, H misses |
| R60 | `m_patch_version_ignored` | false / true / false | H false positive, G correct |
| R69 | `m_habit_limit_49` | true / false / true | G catches, H misses |

The 7 cells where H disagrees with T: R33, R37, R60 x 3 (`m_name_max_101`, `m_target_week_max_8`, `m_description_max_499`), R60
x `m_patch_version_ignored` (H false positive), R69. G disagrees with T only on the 28 R31 cells and the 3 form_gap cells.

## 3. Every miss, stated

1. **PS1, PS1t, PS1o, PS1k fail on one cause: F13 on `ok`.** F13 (R31, `bound`, `push_token` `min_len 1`) states no `accepted`.
   The library derives the accepted outcome from the spec (`accepted_status`); `POST /v1/devices` has none, so it defaults to
   status 200 with the note `accepted_default:POST /v1/devices`. Streak answers 201. The generated scenario
   `form:F13:at_bound / step 1` therefore fails on `ok` ("status: expected 200, got 201", layer D, verdict `silent`) and on the
   28 apps where R31's truth is "holds". The author's own check (`author_check.json`) used the author's expansion of the forms,
   not the library's generator, and all of `ok` passed there. Whether this is a set property (F11 and F12 state
   `accepted: {status: 201}`, F13 does not) or a library default is not decided here; it is reported. Not fixed or edited.
2. **PS0 not_expressible 123 vs predicted 40-70**, and the largest categories are `time` (32) and `other` (21), not `time` and
   `cross_request` (7). The prediction was written before the rules were classified; formed 23 is in band.
3. **PS1 pass rule**: 30 cells G != H with H == T: the 28 above plus R28 x `m_patch_habit_any_owner` and R55 x
   `m_name_taken_case_sensitive` (the registered form_gap cells, where G misses and H catches).
4. **PS1t pass rule**: 28 cells outside the exempt groups (all the F13 cells). The exempt groups themselves behave as registered.
5. **PS1k**: H kills 3 mutants G does not (two form_gap, one masked by the F13 false positive).
6. **PS-PG5 / PS-PNX**: one shape accepted twice: a `transition` form that carries `given` (I2, and the PNX attempt N100 on the
   same R123 nudge rule). The set expects F001 (I2) and F001 or F017 (N100). Transition forms for Streak are not expressible
   (amendment); the library does not refuse the key. Reported, not fixed.
7. **PSx**: one `ok`+G run at 63.6 s is above the predicted 30-60 s band (the gate is 180 s; median in band).

## 4. Findings

**Finding one: the pre-fix refusal and the library fix (disclosed).** The runner's preflight found that the round-3 library
generated `Habit.deleted_at == None` for four row-limit forms (F28, F29, F33, F35). The validator refused them (S008) and `forms
check` refused them (F004), so the strict G could not run on any checker (the preflight exits 3). The orchestrator made a minimal
fix before any scored run (the "library fix" amendment: +8/-1 in `lattice/spec/forms.py` only; `None` in a row_limit `where`
types against a nullable column and is generated as `is_null(...)`; NOT NULL columns and wrongly typed literals still give
F004; output for forms without None is byte-identical, pinned by `tests/spec/test_forms_none_literal.py`). `forms.py` sha256
after the fix `3e2cb2f9...4c54` (m: the file here has that hash). The scored run is therefore on the fixed library; the round-1
to round-3 frozen runners pin the old hash and cannot run on this tree. Under the fix F28, F29, F33, F35 generate
`is_null(Habit.deleted_at)` and validate (PS-PG4 PASS, nothing dropped).

**Environmental stop on this node's first launch (disclosed).** The first scored launch exited 3 with the same four S008
diagnostics, before the ledger was opened ("ledger untouched") and before any container or build. The cause was environmental:
the instructed `UV_PROJECT_ENVIRONMENT` is the main checkout's venv, an editable install of `/Users/vivekgade/Documents/lattice`
(forms.py sha `161c1bb9...`, the unfixed library). Streak's `spec.py` imports `lattice` before the runner's `sys.path.insert(0,
REPO)`, so the preflight loaded the main checkout's library, not the frozen worktree one. This was confirmed read-only: with the
worktree first on the path, `forms.generate` emits `is_null(Habit.deleted_at)` and `validate` returns no problem. The second
launch set `PYTHONPATH=/Users/vivekgade/Documents/lattice-ha` and is the scored run (ledger: one run, `scored`). No file was
edited, no flag skipped; the checker subprocesses already put the worktree first on `PYTHONPATH`. This is not a use of the
once-only re-run. The first refusal log is kept at the scratch path `h-run/run-attempt0-refused.log`.

**R56 truth correction (disclosed, the author's).** R56 x `m_restore_limit_skipped` was first written `violates: false`; the
author's own check failed R56's count invariant on that app, and on re-reading R56 ("a user has at most 50 active habits") the
state rule is broken by a restore to 51, so it was corrected to `violates: true` before the manifest was fixed. The scored
cell agrees: G == H == T on it.

**Finding: F13 default status** (3, item 1). **Finding: transition `given` accepted** (3, item 6).

## 5. Reading, bounded

What the data supports: with the fix, every Streak form generates, validates, runs on MySQL 8.4 for the first time (the first
execution of generated invariants on MySQL; so far only compiled to MySQL text), and the generated checks are deterministic
across 10 runs. G flips 22 of the 29 mutants against `ok` (H 25), and on five cells G catches a violation H misses. What it does not support: that G equals H or truth on Streak. G produces a false alarm on the
unmutated app through F13. Not a gate: the Spike H verdict (round 3, 12 of 16 gated rows) is unchanged by these report-only rows.

## 6. Containers and environment

Each CD run started one throwaway `mysql:8.4` container through Lattice's dbserver, labelled `lattice.owner=h-streak-arm`, and the
runner removed it; after the run `docker ps -a --filter label=lattice.owner=h-streak-arm` is empty (m; row `containers` PASS).
`cyclops-*` and `studio-*` containers were not touched. Docker was shared with other nodes, so wall times are loaded times.
One stray `uv run` without `UV_NO_SYNC` created an ignored `.venv/` in the worktree (110 packages, not used by the run); it is
untracked-ignored and was not removed (removal was denied by the permission layer; the owner can delete it).

## 7. Files created

* `docs/results/2026-10-18-typed-conditions-streak-arm-scored-results.md` (this file)
* `docs/results/data/2026-10-18-typed-conditions-streak-arm/scored/streak-strict.json`, `raw.json`, `raw/*.json.gz` (42: 3 no-G baseline, 29 mutants, 10 ok+G)
* Outside the repo: `~/.lattice/spike-h-streak-ledger.json`; scratch `h-run/` (logs, trees, `dbg.py`).

## Orchestrator verification and ruling
The FREEZE check passes and the ledger shows exactly one `strict` run, `scored`. I read the stored raw data of the `ok`-class
runs: the R31 failure on the clean app is `form:F13:at_bound` on `POST /v1/devices` (the generated expectation defaults to
status 200 and Streak answers 201), as reported; it is a library default meeting an app whose spec states no status for that
endpoint, not a Streak defect. **Ruling on the first launch:** it exited 3 under a wrong environment (the main venv is an
editable install of the unfixed library); the run node corrected the environment (PYTHONPATH) without editing any file or
skipping any flag, before the ledger opened, so the once-only run was not spent. I accept that; it is disclosed as part of
finding one. Gate verdict of Spike H is unchanged (the Streak rows are report-only; PS1, PS1t, PS-PG5 and PS-PNX miss their pass
rules, and the transition-with-`given` acceptance (I2, N100) is a library gap).
