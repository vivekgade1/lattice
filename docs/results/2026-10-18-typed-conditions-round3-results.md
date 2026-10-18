# Typed rule conditions, round 3 (Spike H3): results, development stage only

2026-10-18. Pre-registration: `2026-10-18-typed-conditions-round3-prereg.md` with Amendments 1 and 2. Worktree
`/home/user/wt/np-typedcond`, branch `spike-h-typed-conditions` at `59d285f`. **Nothing was committed or staged; no
model was called; no API key was used; no sub-agent was spawned.** Data: `data/2026-10-18-typed-conditions-round3/`.
Labels as in the prereg: **(m)** measured here, **(p)** prediction, **(ph)** post hoc.

**This document reports the build/dev stage only. H3 was not scored.** The only file of the H3 set that was opened is
`hshapes3/README.md` (layout of data types, to write the reader). The 11 files of the set were not opened, hashed
by this node, or run. Every gated row of prereg 5.2 (PG1 ... PX on H3) is therefore **NOT RUN**, and the Streak arm
(PS0 ... PSx) is **NOT RUN: no Streak checkout, no Docker daemon**. Nothing below stands in for either.

## 1. Summary

| group | result |
|---|---|
| Library | `forms.py` +51 / -7 = **58 changed lines** (budget 115; cause 8 28/40, cause 7 5/15, Amendment 2 note 2/4, Amendment 2 sibling limits 23/(15-20, "about"; stop rule 30)); `forms_tier2.py` 0 / 15; `impact_report.py` frozen; 0 edits to `test_forms.py` / `test_forms_r2.py` |
| Probes passed (dev, gated rows) | PC3 7/7, PB (18/18 vs `3679ab2`, 18/18 vs `d7ba7de`, pytest failing ids identical), PW, PA, PG3, PG4 on the three dev fixtures |
| Probes failed (dev, gated rows) | **PRC** (residual (e) has no cause in the RC record), **PR1** (the round-1 and round-2 fixtures differ in `notes` by the Amendment-2 `db_unchecked` notes; no scenario, invariant or link differs), **PR2** (the frozen round-2 runner's own PR1 row fails for the same reason), **PH** at dev time before the freeze (passes after, section 7) |
| Probes NOT RUN | every gated H3 row (PG1, PG1t, PG1o, PG1k, PG3, PG4, PG5, PT2f, PT2e, PT2x, PT2w, PNX, PA, PW, PX **on H3**); PS0-PSx (Streak) |
| Post hoc, reported | PD2 (H2): PG1 4 -> **1** cell (the registered K7 cell), PG1k H-only 2 -> 0, PNX 10/12 -> **12/12**, class e 2/4 -> **4/4**; PD1 (round-1 set): identical to round 2 (397/400, precision 17/18) |
| Mutants | **47/47 killed** (HZ1-HZ15 15/15, HY1-HY16 16/16, HX1-HX16 16/16), each by a named unit test (section 6) |
| pytest | before 7 failed, 2816 passed, 37 skipped, 98 errors; after 7 failed, **2836** passed (+20 = `test_forms_r3.py`), 37 skipped, 98 errors; failing ids identical (section 8) |
| ruff | `ruff check` and `ruff format --check` clean on every touched or created `.py` |

**Gates missed, plainly.** (1) PRC does not pass: the RC node's record has no finding for residual (e). (2) PR1 does not
pass literally and PR2 fails one of its rows: Amendment 2's `db_unchecked` note changes the `notes` array of
`forms generate` on both older fixtures (4 notes each); every scenario, invariant and link is byte-identical. The
byte-compat promise (2) is broken by exactly that ruling. (3) On H2 (post hoc) PG1 still shows one cell with G != H and
H == T, `m_cancel_sneaky x R19`: that is residual (b), ruled a registered limitation (K7) and **not fixed**. If H3 holds the
same shape (its quotas make that likely), **PG1 will fail on H3** (prereg risk 2). (4) The H3 manifest and the H3 set
existed before this build node started, so the prereg's order (freeze before the set is written) could not be kept; the
freeze is before any gated run, and the set was never opened (section 7).

## 2. RC and PRC (section 3 of the prereg)

`rc.json` is **a transcription of Amendment 2**, written (06:49) before the first library edit (07:00), and says so in its
`provenance` field: the RC node's own report lived in the orchestrator's session record and was not delivered to this
checkout, so the per-residual evidence the prereg asks for (G/H/T verdicts, generated cases, requests, db states) is
NOT RUN here. Content: (b) `ownership()` drops `refused_other: {db: unchanged}` silently when the placeholder is not a
column and the form has no `given` (ruled: note, no synthesis, K7); (c) rule C gives every synthesised row the same
value on non-`by` columns (ruled: pairwise distinct on columns a sibling `row_limit` counts by); (d) `outcome()` hard-codes F004
(ruled: cause 7's order everywhere, 1 line); **(e) is not addressed by Amendment 2**, so the D0 default stood: no code in
`forms_tier2.py`. PRC (dev scorer): **FAIL**, "every residual has a cause or 'not found'" is not met for (e). Post hoc,
PD2 shows (e) followed from (a) (class e 4/4 including P24 and P25), so the D0 assumption held, but that is not an RC finding.

## 3. Library changes (D0 and Amendment 2), against `3679ab2`

`git diff --numstat lattice/spec/forms.py`: 51 added, 7 deleted. By hunk:

| change | what | lines | budget |
|---|---|---|---|
| (a) cause 8 | `_place` takes the endpoint template and substitutes the value at the named placeholder's segment only (nested routes: the named one, not the first or last); `bound()` passes it; the `beyond` case of a path bound gets a db locus on the key column of the table the first placeholder addresses (only for the first placeholder, an int below 2**63); `_Checker._bound` refuses F021 for a path value that is empty or holds `/ ? #` | 28 | <= 40 |
| (d) cause 7 | `_Checker.lit` sends a dict/list to `value_lit` (F017 for a node name, else F001) so `given` rows, `where` and transition `row` follow the rule; `outcome()` types `db.equals` values the same way | 5 | <= 15 |
| (b) Amendment 2 | `ownership()` appends `F<n>: db_unchecked:<case>` when an `unchanged` refusal cannot be asserted | 2 | ~4 |
| (c) Amendment 2 | `row_limit()`: counted rows get pairwise distinct values (`_vary`) on every column another `row_limit` form of the same table counts by, except the form's own `by`/`where` columns and the key; `_Gen.limits` is filled by `generate()` | 23 | ~15-20 (stop rule: 150% = 30) |

(c) is above the Amendment's "about 15-20" and under the stop rule. The query-key half of cause 8 needed **no code**: `_place` already
placed query values for any key; PC3 and HZ6 pin it. `forms_tier2.py`: 0 lines (e follows from (a), section 5).

## 4. Dev probes (final run, the committed library and runner)

| probe | gate | observed |
|---|---|---|
| PRC | exists before code; each residual has a cause | **FAIL**: exists, before code, (b)(c)(d) have causes, (e) none (section 2) |
| PC3 | all | **PASS 7/7**: path bound (`/orders/1` accepted 200; `/orders/0` refused 404 with `Order where id=0 count 0`), query bound (`limit` 1 / 0), nested route bound on the second placeholder (`/orders/5/items/1`, `/orders/5/items/0`), F021 for an empty path value, outcome `db.equals` `{param}` -> F017 and `[1, 2]` -> F001, ownership `db_unchecked` notes, sibling limits (F4 rows `product_id` 2,3,4; F5 rows `order_id` 21,22) |
| PR1 | only path/query-bound cases differ | **FAIL** (literal): on both older fixtures 0 scenarios, invariants, links or `no_invariant` entries differ; `notes` gain 4 `db_unchecked` lines each (round-1 fixture F6, round-2 fixture F3), nothing else; the round-2 fixture has no path/query bound (p: 0-2 forms: 0) |
| PR2 | as round 2 | **FAIL** (one row): `forms_probes2.py --stage dev` (frozen, 9e3ec0ad) passes every row except its own PR1, which compares `notes` for equality |
| PB | all identical, failing ids = pre-edit set | **PASS**: 18/18 commands identical vs `3679ab2` (after removing the registered timing keys), 18/18 vs `d7ba7de`, frozen hashes and sets OK, pytest failing ids identical |
| PW, PA, PG3, PG4 (dev fixtures) | 0/all/10-10/100% | PASS on `forms`, `forms_r2`, `forms_r3` (PA on `forms`, as round 1) |
| PH | OK | before the freeze FAIL (files absent); after the freeze OK (section 7) |

## 5. Post hoc: H2 (PD2) and the round-1 set (PD1), never gated

Library and runner as frozen. H2 files read from the scratchpad copy, manifest `shapes2.manifest.sha256` checked first.

| row | round 2 on H2 (prereg 1.1) | round 3 library, round 3 scorer (ph) | delta |
|---|---|---|---|
| PG1 | 668 / 672; 4 cells G != H, H == T | **671 / 672; 1 cell**: `m_cancel_sneaky x R19` (K7) | closed: `m_read_clamp x R9`, `m_cancel_clamp x R10` (a), `m_pair2 x R15` (c); open: (b) by ruling |
| PG1t | 572 / 576 outside K6; K6 96 cells, 0 misses | 671 / 672 scored equal; **0 misses outside K6 and K7**; K6 96 cells, 0 misses; K7: 1 cell | |
| PG1o | exact | exact (R14-R17, R21, R25) | = |
| PG1k | G 20, H 22; H-only 2 | G 22, H 22; **H-only 0**, G-only 0 | H-only closed |
| PG2 | 99 / 223 same layer set | 101 / 225 same layer set (layers C,D vs D on R14-R17: no change by ruling); layer-D agreement 223 / 223 | reported |
| PG5 / PG5c | 0 / 0; 29 / 29 | 0 invalid accepted, 0 valid refused; 29 / 29 exact | = |
| PT2f | precision 1.0; a-d 11/11; class e 2/4 | precision 1.0; a-d 11/11; class e **4/4** | class e closed |
| PT2e | n/a | recall 1.0; the 2 path/query-bound pairs (P24, P25) found | follows from (a) |
| PT2x | 20 items, 0 failed, 2 weak | 22 items, 0 failed, 2 weak (class c) | +2 class-e items, strong |
| PT2w | 1 / 1, tier 2 | 1 / 1, tier 2 | = |
| PNX | 10 / 12 | **12 / 12** (N12, N13 now F017) | closed |
| PA, PW, PG3, PG4 | not run | PASS, PASS, PASS, PASS (12 invariants, 51 scenarios, 0 raises) | n/a |
| PX | 132.4 s arm | 195 s whole arm (scoring part 128 s); `generate` median 1.9 ms | different definition (section 9) |

PD1 (round-1 set, 400 cells): PG1 398 / 400 (the 2 `h_cancel_typo_state` cells R19/R20), PG1t 397 / 400, PG1k 18 = 18, PT2f precision 17/18
(P15, class e over-claim), PNX and PG5 pass: **identical to round 2's PD1**, as predicted (g is not fixed). PT2e on that set fails
only because it has no path/query-bound pair.

**Predicted vs observed.** PG1 4 -> 0-2: observed 1. PG1k H-only 2 -> 0: observed 0. PNX 10/12 -> 12/12: observed 12/12. Class e 2/4 -> 4/4:
observed 4/4. PD1 +/- 1 cell: observed 0 cells. PC3 all: observed all. PR1 "only path/query-bound cases differ": observed no
case differs, notes do (miss). PRC (b),(c) p = 0.7 each: both have a cause (from the Amendment); (e) none (miss).

## 6. Mutants (`dogfood/forms_mutants3.py`, after the freeze; control OK)

47/47 killed, each by a named unit test (the pre-registered catchers PD2, PG1, PR1, PB, PH need the frozen set or a full
run; the table says which probe each stands for, as in rounds 1-2). HY and HX edits all still applied textually (none re-expressed).

| id | mutant | killed by (`tests/spec/test_forms_r3.py::`) |
|---|---|---|
| HZ1 | path bound emits no case | `test_pc3_a_path_placeholder_bound_substitutes...`, `test_hz1_...` |
| HZ2 | beyond = the bound value | `test_hz2_beyond_is_the_value_one_step_past_the_bound` |
| HZ3 | last placeholder substituted | `test_hz3_the_named_placeholder_is_substituted...` |
| HZ4 | path-bound at_bound expects refused | `test_hz4_...` |
| HZ5 | path-bound beyond without a db locus | `test_hz5_...` |
| HZ6 | query-key bound ignored | `test_hz6_...` |
| HZ7 | F021 not raised at check (**re-expressed**: a run-time raise cannot be injected by one text edit) | `test_hz7_...` |
| HZ8 | outcome `db.equals` typed before node | `test_hz8_...`, `test_pc3_an_outcome_db_value...` |
| HZ9 | cause 7 not applied to `given` rows | `test_hz9_...` |
| HZ10 | (b) reverted: silent drop (the RC Amendment defines it: the fix is the note) | `test_hz10_...` |
| HZ11 | (c) reverted: shared values | `test_hz11_counted_rows_are_pairwise_distinct...` |
| HZ12 | class e skips path/query-bound cases | `test_hz12_class_e_compares_a_path_bound_case_with_an_ownership_case` |
| HZ13 | class e keys a request without its path | `test_hz13_two_cases_on_different_paths_are_not_one_request` |
| HZ14 | `forms generate` adds a `path_bound` key | `test_hz14_the_generated_view_has_no_new_key` |
| HZ15 | scorer edited after the freeze | `test_hz15_the_runner_files_match_runner3_sha256` (needs `runner3.sha256`) |

HY1-HY16 and HX1-HX16: killed by the round-2 and round-1 tests named in `forms_mutants2.py` / `forms_mutants.py` (full list in `mutants.json`).

## 7. Hash discipline and the freeze

* `freeze3.sha256`: `forms.py` `161c1bb9...`, `forms_tier2.py` `288beb57...` (unchanged), `impact_report.py` `aa29aede...` (unchanged), plus the three runner
  files (a superset of the prereg's library-only list, so the scorer is hashed there too); `runner3.sha256`: scorer `forms_probes3.py` `c35cb27e...`,
  reader `forms_reader3.py` `bcd638fd...`, mutants `forms_mutants3.py` `eafaedf0...`. `sha256sum -c` OK on both after the mutants, the pytest run and the final dev run.
  `frozen-hashes-at-freeze.txt` lists the 23 frozen files and sets: unchanged from `3679ab2`.
* `shapes3.manifest.sha256` is the committed file; its own sha256 is `1d0b8256...` (m, equals Amendment 2). The frozen stage refuses to run unless that
  sha, the set's `MANIFEST.sha256`, every file hash, `freeze3.sha256` and `runner3.sha256` all match, and re-checks them after the run (PH).
* Order: freeze at 07:24 UTC, before anything gated. The H3 set and manifest were created before this node started (Amendment 2), so "freeze before the set exists" was not possible.
  The set's 11 files were not opened; the reader was written from `README.md` alone and exercised on a synthetic conversion of H2 into the README layout (672 cells,
  identical to PD2, PA/PW/PG3/PG4 PASS); it has never read a real H3 file. D3 (2): a reader fix on H3 is allowed only to turn a NOT RUN into a run.
* Frozen docs and data of rounds 1-2: `git diff 3679ab2 -- docs/results/2026-10-17*` shows 4 added lines in `2026-10-17-typed-conditions-round2-prereg.md`, the orchestrator's
  committed Amendment (`41228f9`), not an edit by this node; no other frozen file differs (`frozen_check` OK in PB).
* Data files here: `rc.json`, `freeze3.sha256`, `runner3.sha256`, `frozen-hashes-at-freeze.txt`, `shapes3.manifest.sha256` (orchestrator's), `dev.json` / `dev.log`,
  `pd2-h2-post-hoc.json`, `pd1-round1-post-hoc.json`, `mutants.json`, `pytest-failing-ids-{before,after}.txt`, `pytest-failing-full-ids-after.txt`.
* The reader maps `sec:<v>` to `sec_<v>` in truth (H2 already writes `sec_`; the round-1 set writes `sec:`): with that, PD1 reproduces round 2's PD1 numbers (400 cells scored, 397 equal).
  Round 2's *final* runner left the round-1 `sec:` cells unscored (304); the number round 2 reported (400) came from an earlier runner version.

## 8. Tests and ruff

`tests/spec/test_forms_r3.py` (20 tests) and `tests/spec/fixtures/forms_r3/**` (5 forms on the mini-shop) are new. Edits to `test_forms.py` / `test_forms_r2.py`: **0**
(no pinned note was overturned). `pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck -q -rs --continue-on-collection-errors`:
before `7 failed, 2816 passed, 37 skipped, 124 warnings, 98 errors`; after `7 failed, 2836 passed, 37 skipped, 124 warnings, 98 errors`. The ids are compared by failure/error header
(`pytest-failing-ids-before.txt` / `-after.txt`, 74 headers each, identical; the before run used `-rs` only, so full node ids exist for the after run only,
`pytest-failing-full-ids-after.txt`). The 7 failures are all `tests/importer/*` and pre-existing. ruff: `ruff check` and `ruff format --check` clean on
`lattice/spec/forms.py`, `dogfood/forms_probes3.py`, `dogfood/forms_reader3.py`, `dogfood/forms_mutants3.py`, `tests/spec/test_forms_r3.py`.

## 9. Deviations from the prereg (all disclosed; none tuned to a score)

1. **rc.json is a transcription**, not an RC node report; PRC FAIL on (e) (section 2).
2. **Amendment 2's `db_unchecked` note breaks PR1/PR2 and the byte-compat promise (2)** on the round-1 and round-2 fixtures (notes only). Kept, because the Amendment rules it.
3. **Whole-set generation in the scorer** (round 2 generated form by form, which cannot see sibling forms; Amendment 2 (c) needs them). A scorer change relative to round 2, applied to PD2 and PD1 as well.
4. **K7 is defined by this scorer** (Amendment 2 does not define it): a cell whose rule's form generated a `db_unchecked` note, where truth says violated and G passes. PG1t's gate excludes K6 and K7; PG1 has **no** K7 exemption.
5. **PX arm** is the whole frozen stage (scoring, PA, PW, PG3, PG4) <= 240 s; on H2 195 s (scoring part 128 s). PG3's CD report uses the `ok` app only (two apps cost 99 s under load).
6. **freeze3.sha256 also lists the runner files** (the instruction to hash the scorer in freeze3); `runner3.sha256` is the prereg's.
7. **HZ7 re-expressed** (no run-time raise injection); every mutant is killed by a unit test, not by the probe the prereg names.
8. **pytest ids compared by header** (section 8).
9. **A scorer fix after PD1 first ran** (dev, before the freeze): a class-e item from a non-e pair whose witness is a request without `method`/`path` no longer indexes `rq["method"]` (it re-derives from the item's cases or is weak).
10. (c) landed at 23 lines against "about 15-20" (within the stop rule); cause 8 added a db locus on the key column for the first placeholder, which the prereg's D0 does not spell out ("locus as round-2 cause 5").

## 10. What this does not show

H3 was not run: PG1 is predicted to fail (K7 cells in ownership forms with no `given`, p about 0.5 in prereg risk 2), PG1t should hold outside K6 and K7,
PT2e and PNX should pass on the evidence of H2 but H3 has its own readings (the set's author read the earlier D-sections, Amendment 2). The Streak arm,
MySQL execution and the drafter are not touched. The scorer, reader and mutant files are final and hashed; a change to any of them from here makes the rows it can move post hoc.
