# Typed rule conditions, round 2 (Spike H2): results of the build node (dev stage only)

2026-10-17. Pre-registration: `2026-10-17-typed-conditions-round2-prereg.md` (with Amendment 1), not edited. Data:
`docs/results/data/2026-10-17-typed-conditions-round2/` (`dev.json`, `round1.json`, `mutants.json`, `freeze2.sha256`,
`frozen-hashes-at-freeze.txt`, `pytest-failing-ids-before.txt`, `pytest-failing-ids-after.txt`). Worktree
`/home/user/wt/np-typedcond`, branch `spike-h-typed-conditions` at c5e2595 (round 1 d7ba7de plus the Spike E merge, made before
this node started). Nothing was committed or staged. No model was called and no API key was used. No sub-agent was spawned.
No Streak checkout and no Docker daemon exist here: the Streak arm is NOT RUN (registered, no number, no substitute).

## 0. Verdict in one paragraph

**Dev stage built and run; the gated stage was not run and could not be: the new frozen set H2 was not opened or scored by
this node.** (A directory `hshapes2/` with a `MANIFEST.sha256` exists in the shared scratchpad; this node did not read,
list or score it.) The per-cause library changes of D0 (causes 0-5, 7, T2) are in, F021 is in, the D6 branch of
`impact_report.py` is in, 41 new unit tests pass, and the 16 new mutants (HY) and the 16 re-applied round-1 mutants (HX)
are **32/32 killed**. Every dev probe passes (PC 8/8, PR1, PB 18/18, PW, PA, PG3, PG4, PX, PT2w on the dev fixture). The
post hoc re-score of the round-1 set (PD1, reported never gated) moves round 1's numbers from 150/400 to **397/400** (PG1t),
from 3 to **18** kills (PG1k), PG1o exact, PG5 0/16 valid refused, PT2f recall a-d 14/14 and class e 3/3, PNX 8/8 with
F001/F017 and PT2w through the CLI; but it also shows **three defects the dev fixtures did not**: two PG1 cells
(G != H, H == T: a machine invariant duplicated by three transition forms), one PT2f false positive (pair P15, class e on
two row limits), and the budget overruns of section 4. Gates missed, stated plainly in section 7. **No library file was
changed after the freeze** (`freeze2.sha256` verified before the probes and after the last run). Not a pass of the spike:
the pass rule needs H2 (section 6 of the prereg).

## 1. Files

Changed (library): `lattice/spec/forms.py` (+309 / -62), `lattice/spec/forms_tier2.py` (+13 / -3),
`lattice/spec/impact_report.py` (+29 / -1). Changed (test, disclosed): `tests/spec/test_forms.py` (+6 / -6, section 5, item 1).
Created: `tests/spec/test_forms_r2.py` (41 tests), `tests/spec/fixtures/forms_r2/minishop/` (dev fixture: 7 forms over rules
R6-R12, one case per cause), `dogfood/forms_probes2.py`, `dogfood/forms_mutants2.py`, this document and its data directory.
Not touched: every frozen file of the prereg's section 7 (hashes in `frozen-hashes-at-freeze.txt` and checked by PB),
`dogfood/forms_probes.py`, `dogfood/forms_mutants.py` (both re-used unchanged), `tests/spec/minishop.py`.

ruff: `ruff check` clean on every touched and created `.py` file; `ruff format --check` clean on the three library files and
the created files. mypy on the three library files reports exactly the error messages (same multiset, lines shifted) that the unmodified files
have (compared by swapping the three base files in for one run and restoring the frozen ones; `sha256sum -c` OK after; the
repo has 658 mypy errors in 97 files before any edit).

## 2. Freeze (before scoring anything)

```
dd9b756625760b4cfc9f3e56ed3e6c814da3acb82c4130747836cf2af0dbfa97  lattice/spec/forms.py
288beb576f42845dee1856d3b461b4a4ea8d7c67d9943a5711c2e862c7b53e6b  lattice/spec/forms_tier2.py
aa29aede06cce15ea74d32aebb964b71dcbdee5d1f70e02a012ac50028558a41  lattice/spec/impact_report.py
```

Taken after the unit tests and the budget pass, **before** the first mutant run, probe run or PD1 run; `sha256sum -c` OK
after the probes, after the full pytest run and after the final mutant run. The frozen files of section 7 are unchanged
(`frozen-hashes-at-freeze.txt`); one reference differs from the prereg and is not mine: `lattice/spec/__main__.py` is
94542f34... at HEAD, not e24bf2ff..., because the Spike E merge (Amendment 1, Q7) added its one line (`git diff d7ba7de HEAD
-- lattice/spec` is `__main__.py` +1 and the new `impact_tier4.py`). The set hash of `lattice/gocheck` is e9515e85...
(`git ls-files` method, Q6). Tests and runners written after the freeze: `test_forms_r2.py::test_hy12_*` (strengthened after
HY12 survived once, section 5 item 5), `forms_probes2.py` (three runner changes, section 5 item 9). No library edit.

## 3. Probes: predicted vs observed

Labels as the prereg: (p) prediction; gated rows are the prereg's. **All 3.2 rows are NOT RUN here (no H2).**

### 3.1 Dev and compatibility probes (3.3), `dogfood/forms_probes2.py --stage dev`

| id | prediction (p) | observed | status |
|---|---|---|---|
| PC | 8/8 | **8/8**: untagged node accepted, state `current` (0); `at_bound` expects 201 with no `accepted` and no note (1); ownership `row` `"/orders/1"` generates `/orders/1` and `/orders/1/cancel` (2); transition `row {id: 20}` generates `{id 20, user_id 1, total 0.0, status per case}` (3); `row_limit` with a parent-only `given` generates 4 and 5 counted rows of order 20 (4); param bound without `stores` asserts `unchanged` on `order_items` where `{product_id, quantity}` (5); column-only bound = invariant only, no scenario (6); `value` as `{param}`/`{column}`/`{sum}` and a `values` item `{now}` are F017, `[1,2]` is F001, `1.5` on an int is F004 (7) | PASS |
| PR1 | only F7's `below`/`at_limit` differ; the 71 round-1 tests pass unedited | `forms generate` on the round-1 dev forms vs the merge base: **only `form:F7:below` and `form:F7:at_limit` differ**; invariants, links and notes identical (no `accepted_default` note). **2 of the 71 round-1 tests needed an edit** (section 5, item 1) | bytes PASS; "71 unedited" **MISSED** |
| PR2 | PB, PW, PA, PG3, PG4 as round 1 | round-1 runner unchanged, `--stage dev`: PW, PA, PG3, PG4, PX pass; **PB FAIL** for three registered reasons, all outside the 18 commands (18/18 identical): `impact_report.py` hash changed (the D6 unfreeze), `lattice/gocheck` round-1 reference b7a4dacf... (unreproducible, Q6), `git diff --stat 6a318e5 -- lattice/studio` non-empty (Spike E merge) | as predicted for the unfreeze; PB(round 1) FAIL by design |
| PB (round 2) | all identical; failing ids = the pre-edit set | against `git archive c5e2595` and against d7ba7de: **18/18 identical each** (lint, check CD, D's 7 impact fixtures `--tiers 23 --json`, appmap, rationale coverage, 5 help/usage); all frozen hashes and set hashes match; pytest failing ids section 3.3 | PASS |
| PW | 0, 0, 0 | on both fixtures (round-1 dev, `forms_r2`): 0 changed, 0 new, 0 `claude` shim calls, x10 | PASS |
| PA | all | round-1 states; draft writes only proposals; no TTY / `CLAUDECODE` refuses (r1); on `forms_r2`: the untagged forms are `current` and a hand edit of an untagged form reads `edited` | PASS |
| PG3 | 10/10 | `generate` 1 distinct output and CD report 1 distinct (ok, total_drift) over 10 random `PYTHONHASHSEED`, two roots; both fixtures | PASS |
| PG4 | 100%, 0 raises | r1: 6 invariants, 16 scenarios, 0 problems; r2: 5, 18, 0 | PASS |
| PX | 0.3-5 ms | `generate` median of 50: 0.66 ms (r1), 0.78 ms (r2) | PASS |
| PT2w (dev) | exact; identical | CLI on the dev fixture, candidate adds one contradicting form: `tier2.determined` gains **exactly** the 2 new items (class a and class e), `tier: 2`, equal to the runner's own `contradictions(candidate) - contradictions(base)` | PASS |

Ungated G/H on the 11 minishop apps (`DEV-GH`): both fixtures: G kills `wrong_answer`, `sec:authz`, `sec:auth`,
`sec:bounds`, `sec:state`, `sec:leak`; H kills `total_drift`, `wrong_quantity`, `sec:state`. On `ok`, G fails only the
limit the app lacks (r1: `form:F7:below`/`at_limit`, see section 5 item 1; r2: `form:F5` invariant and `at_limit`).

### 3.2 PD1, post hoc (the round-1 set, manifest 182fe629 hash-checked, scored once after the freeze)

Reported beside round 1, **never gated**: that set has been read by the round-1 runner, the round-1 and round-2 authors and
this node (forms printed while designing; not scored before the freeze). Runner `forms_probes2.py --stage round1`: forms read
as written (untagged nodes, `approved_sha` over the text as written, no recompute).

| row | round 1 (blind library, runner post hoc) | prediction (p) | PD1 observed (post hoc) |
|---|---|---|---|
| PG1 | 249 cells G != H and H == T | n/a (reported) | G == H on 398/400; **2 cells G != H with H == T** (`h_cancel_typo_state`, R19 and R20) |
| PG1t | 150/400 | 393-399 | **397/400**; misses: R4 on `h_no_stock_check` (the K6 cell, 100 K6 cells over R4, R9, R10, R11) and the same two R19/R20 cells; outside K6 **2 misses** |
| PG1o | G fails R6-R8, R14-R20 | exact (R12, R13, R14) | **exact**: G fails R12, R13, R14 = T |
| PG1k | G 3, H 18 of 24 | G 17-21, superset | G **18**, H 18, G-only 0, H-only 0: **superset yes** |
| PG2 | 53/54 | reported | 103 of 104 cells where both fail have the same layer set (the one: `h_cancel_typo_state` R18, G on C and D, H on D) |
| PG5 | 0/29 accepted, 5/16 valid refused | 0 / 0 | **0/29 accepted, 0/16 valid refused**; PG5c exact code 28/29 (I26: F001 where F017 is the code and F001 acceptable) |
| PT2f | precision 1.0; recall a-d 13/14; e 1/3; 5 raises | recall 14/14, e 3/3, 0 raises | recall a-d **14/14**, class e **3/3**, **0 raises**, 0 undecided; **precision 17/18 = 0.944: one false positive, P15** (class b pair expected compatible; item `fc:e:PA:PB`) |
| PT2x | 19 items (weak) | 100% | 23 items; 19 re-derived by the runner (it recomputes the cases and calls `incompatible` itself for e and d, `cond_holds` for a, the limits for b), 0 failed, 4 class-c items not independently re-derived (weak) |
| PT2w | 0 items | exact | CLI on a minishop project built from the set: candidate adds one contradicting form: **1 new item, `tier: 2`**, equal to the runner's own difference |
| PNX | 0/8 accepted, 3 with F004/F002 | 8/8 F001/F017 | **0/8 accepted; every refusal carries F001 or F017** |
| PX | n/a | 35-90 s | 67.0 s for the whole arm (25 apps) |

If the same results came from H2, **PG1 (2 cells) and PT2f (precision) would be gate misses**: PD1 is where the dev
fixtures were too small to see them. They are findings, section 7, and are not fixed here (frozen; a fix is a separate post hoc
commit, rerun with deltas labelled post hoc).

## 4. Line budgets (D0)

Counted by `git diff --numstat` (added + deleted), laid out by `ruff format`. Per cause, by attributed functions.

| cause | budget | used (approx.) | note |
|---|---|---|---|
| 0 untagged nodes | 25 | 23 | `norm_cond`, `normalised`, three call sites |
| 1 accepted | 40 | 38 | `accepted_status`, `_Gen.accepted`, call sites |
| 2 ownership path | 30 | 22 | `_row_key`, checker and generator lines |
| 3+4 transition row, row_limit, rules B and C | 45 | **~140** | `_word_table`, `_fk_target`, `_ph_table`, `_first_ph`, `_user_id`, `_witness_col`, `bind_column`, `complete_row`, `_Gen.prepare`, checker hook: **over by >50%: a finding** |
| 5 `stores` | 30 | **~50** | `derive_table` (29), `_given_only` (12), bound edits (the removed `_locus_needed` offsets 10): **over by >50% (67%)** |
| 7 order | 20 | 24 | `NOT_EXPR_NODES`, `_names_node`, `value_lit`, `cond` edits |
| docstring | - | 4 | |
| **forms.py total** | **235** | **371** (+309 / -62) | **+58%**: a finding. No code was moved out of `forms.py` to meet the number |
| forms_tier2.py (T2) | 15 | 16 (+13 / -3) | within 50% |
| impact_report.py (D6) | 30 | 30 (+29 / -1) | `decidability[]` reason `form`: **not done** (budget) |

The overrun is rules B and C: the prereg gave them 45 lines and they need the foreign-key machinery (owner, `owner_via`,
the `x_id` convention, the first placeholder's table) that causes 3, 4 and 5 all share.

## 5. Deviations from the pre-registration, and decisions it left open

1. **Two round-1 tests edited (PR1's "71 unedited" missed; the sanctioned exception of section 7).** Cause 4 keeps the
   dev F7's `given` counted row (`id 1`) as written; the seed's `order_items` already has id 1, so both F7 scenarios stop at
   their `given` step (IntegrityError, a loud layer D finding) and the F7 invariant is never reached. Edited, expectation
   only, with a comment: `tests/spec/test_forms.py::test_generated_checks_pass_on_the_ok_app_except_the_limit_the_app_lacks`
   (now expects `form:F7:at_limit` and `form:F7:below`) and `::test_a_generated_ownership_check_catches_a_mutant_the_hand_written_misses`
   (same two names, no limit invariant). The information the old assertion carried (the app lacks the limit) is carried by
   `test_forms_r2.py::test_generated_checks_pass_on_the_ok_app_except_the_limit_the_app_lacks` on the `forms_r2` fixture.
   69 round-1 tests pass unedited.
2. **Rule C is extended by the witness request.** Every unfilled NOT NULL column takes the value the witness carries under
   its name (JSON key, query key, path placeholder) before the foreign-key, machine, one_of and typed-zero steps (the prereg
   gives it to foreign keys only through rule B). Without it F8's `product_id` is F021 (the set gave `given` only for orders)
   and the typed zero `quantity: 0` makes every `OrderItem.quantity >= 1` invariant fail inside the row-limit states. The
   owner column still takes the caller's id first. A rule-B source is added at the lowest priority: a `by` value from the
   given counted-row template.
3. **The user id of a handle is its 1-based position in `Spec.users`** (the seed's order in minishop). `Spec` carries no
   ids; this is a heuristic stated as a rule and wrong for an app seeded another way.
4. **`accepted_status`**: unique 2xx of the spec's hand-written steps on the endpoint template, else 200 without a note if a
   program serves it, else 200 with `accepted_default:<endpoint>`. The contract is not consulted (a `Contract` has no
   success status). The prereg said "then of a contract or program".
5. **HY12 survived once** (first mutant run: 31/32). Its catcher refused a `refused` outcome with a `db`, the fixture form has
   none. The catcher was strengthened (a form with `refused.db: unchanged`), HY12 then killed, and the final run is 32/32
   with the final tests. HY8 is re-expressed (ownership does not synthesise rows, so the "other user's id" lands in the owner
   column of a completed transition row; unit test).
6. **Environment baseline.** `pytest` on the five directories has 12 collection errors from `bench/template_app` (a
   `NameError: Item`), so both before and after were run with `--continue-on-collection-errors`. Before and after are the
   same 105 ids (7 failed, 98 errors); 2775 passed before, 2816 after (the 41 new tests).
7. **PB baseline** is `git archive c5e2595` (the Spike E merge happened first, Q7); d7ba7de is reported beside it and is
   identical too. The registered timing keys are extended by one stderr wall-clock line (`impact: 0.1s`).
8. **D6 `decidability[]` reason `form`** is not implemented. The forms file is found as `*.forms.json` beside the project
   or candidate requirements (the first by name); `Side` gains an optional `forms` field.
9. **Runner changes**, all in `forms_probes2.py` (created here; the round-1 runner is unchanged): the set hash by
   `git ls-files`; the stderr timing normalisation above; PT2w builds the project's `spec.py` as a wrapper around the set's
   `handwritten.py` (which reads `REQUIREMENTS.md` beside itself); `--stage frozen` exists but its H2 layout reading is
   **unvalidated** (same layouts as round 1, no tags added). A layout mismatch on H2 is a NOT RUN to be fixed in the runner
   only (prereg 3.1, step 5).
10. **Cause 6** is documentation only (module docstring, D1' vii), scored as class K6: PD1 has 100 K6 cells and one K6 miss.
11. PD1 was run twice after the freeze (the second time only to correct the PT2w wrapper, a runner bug; same numbers) and
    the dev stage twice (the second time for the stderr timing normalisation; the first run's `PB-d7ba7de` was `REPORTED` 17/18).

## 6. Mutants (`dogfood/forms_mutants2.py`, scratch copy of the tree; the control passes first)

**32/32 killed** (final run with the final tests): HY1-HY16 each by the named test of `tests/spec/test_forms_r2.py`
(catchers listed per mutant in `mutants.json`; the pre-registered probe each stands for is recorded there: PC, PG1o, PG5,
PNX, PT2w and PB need H2 or are covered by the unit test), HX1-HX16 re-applied unchanged by the round-1 catchers (every
round-1 edit still applied textually exactly once; no HX was re-expressed). Disclosed as in round 1: the kills are by
named unit tests, not by the probes of the frozen set.

## 7. Gates missed and open findings (stated plainly, nothing tuned)

1. **PR1 "the 71 round-1 tests pass unedited": missed** (2 edits, section 5 item 1). The bytes part of PR1 passes.
2. **Budgets: missed.** forms.py 371 vs 235 (+58%); causes 3+4 about 3x, cause 5 +67%. T2 16 vs 15.
3. **PD1 PG1: 2 cells (R19, R20 on `h_cancel_typo_state`).** The three transition forms on one machine (F14-F16) each emit
   the same invariant `Order.status in (<machine states>)`; the mutant writes `"canceled"`, so the invariant fails three
   times and the failure is attributed to R18, R19 and R20, where T marks only R18. A property of one invariant per form,
   not of the round-2 changes. Not fixed. Candidate fix (post hoc only): emit the machine invariant once per machine and
   link it to every rule of the machine's forms, or attribute it to the first.
4. **PD1 PT2f: precision 0.944** (P15: `row_limit` max 5 and `row_limit` max 6 where `product_id = 1`, same request). The
   synthesised states coincide (5 order lines, all product 1: the witness' `product_id` is the one value rule C uses), so
   `a:at_limit` expects 409 and `b:below` expects 201 on the same request: class e reports a contradiction. The two rules
   are compatible; the generated `below` check over-claims. Not fixed. Candidate fix (post hoc only): class e skips two
   `row_limit` forms whose `where`/`max` differ (class b already decides that pair).
5. **K6**: the column-only bound `Product.stock >= 0` generates only an invariant, so `h_no_stock_check` is missed
   (1 K6 cell of 100), as the prereg registered (cause 6, Q2).
6. **Risks that the dev stage cannot show**: rule C's id and witness heuristics (section 5 items 2, 3) are right on
   minishop's seed by construction; a `by` column nothing binds is F021, which a set author may meet as a refusal of a form
   D1' calls valid (PG5 "0 valid refused" is a gate on H2). The counted rows keep `price 0.0`, so a hand-written invariant
   on prices would be crosstalk in H2.
7. **Everything gated on H2 is NOT RUN**: PG1, PG1t, PG1o, PG1k, PG3 (H2 `generate`), PG4 (H2), PG5, PT2f, PT2x, PT2w
   (H2 form), PNX, PA, PW, PX on the set. The Streak arm (PS0-PSx) and the drafter are NOT RUN.

## 8. Commands (rerun; the orchestrator runs the gated stage)

```sh
export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
SCRATCH=<scratch>/forms2; cd /home/user/wt/np-typedcond
sha256sum -c docs/results/data/2026-10-17-typed-conditions-round2/freeze2.sha256
uv run python dogfood/forms_probes2.py --stage dev --scratch $SCRATCH/d --out $SCRATCH/d-data
uv run python dogfood/forms_probes2.py --stage round1 --shapes <round-1 hshapes> \
    --manifest docs/results/data/2026-10-17-typed-conditions/shapes.manifest.sha256 --scratch $SCRATCH/pd1 --out $SCRATCH/pd1-data
uv run python dogfood/forms_probes.py --stage dev --scratch $SCRATCH/r1 --out $SCRATCH/r1-data     # PR2
uv run python dogfood/forms_mutants2.py $(seq -f HY%g 1 16) $(seq -f HX%g 1 16) --scratch $SCRATCH/mut
# orchestrator, once, after freeze2.sha256 and shapes2.manifest.sha256 are committed:
uv run python dogfood/forms_probes2.py --stage frozen --shapes <hshapes2> \
    --manifest docs/results/data/2026-10-17-typed-conditions-round2/shapes2.manifest.sha256 \
    --freeze docs/results/data/2026-10-17-typed-conditions-round2/freeze2.sha256 --scratch $SCRATCH/f --out $SCRATCH/f-data
```

## 9. The gated stage on H2 (orchestrator run; scored once, library frozen)

2026-10-17. `shapes2.manifest.sha256` and the 9 set files verified by `sha256sum -c` (0 mismatches) and `freeze2.sha256`
verified (3 library files OK) **before and after** every run. Data: `data/.../frozen/frozen.json` (run 3, the scored run),
`frozen-run1-layout-mismatch.json` and `frozen-run2-layout-mismatch.json` (kept as written). No model call, no API key, no
sub-agent, nothing committed or staged. No Docker, no Streak: the Streak arm and PA, PW, PG3, PG4 (not in this stage's
list) are NOT RUN here.

### 9.1 Runner reader changes (dogfood/forms_probes2.py only; no library file, no metric changed)

| run | result | cause (reader mismatch before any metric) | fix |
|---|---|---|---|
| 1 | every gated probe NOT RUN, `TypeError: string indices must be integers` | `truth.json` `cells` is `{app: {rule: {violates, reason}}}`, the reader expected a list of `{rule, app, violates}` | `_truth_cells` accepts both |
| 2 | every gated probe NOT RUN, `TypeError: list indices must be integers` | the re-derivation of class e read the library item's witness (`request` is a list) instead of the pair's own `witness.request` (H2) | class e re-derives from the pair file's witness request: finds each side's generated case (or hand-written step) with that method, path, user and calls `incompatible` |
| 3 | scored | | |

Run 3 also reads, with the same change set: `mutants.json` as `{name: {kind, aims, why, edit: [file, old, new]}}`;
`pairs.json` as a list of `{id, class, expect, forms: [{id, rule, kind, form} | "scenario:<hand step>"], witness}` with
`expect` `contradictory`/`compatible`; `invalid.json` and `not_expressible.json` as lists (`expect` / `attempt` /
`expect_any_of`); `invalid.json` and pair forms completed to approved records (anchor of the rule text, `approved_sha` =
`form_sha`); sec apps named `sec_<v>` (round 1 used `sec:<v>`); untagged condition nodes need no reader change (the library
infers the tag, `norm_cond`); `accepted`/`stores`/`given` optional and `row` as path or key are library matters and
were read as written. K6 = column-only bound with no `endpoint` (F1/R4, F7/R11, F9/R13), kept out of the PG1t gate
(Amendment 1, Q2). A pair's items count only if every form they name is in the pair (or is its named hand-written step);
other items are listed (`items_outside_the_pair`). Run 3 was formatted with `ruff format` afterwards (no logic change).
The same file still reads the round-1 layouts. PT2x class-d/b/a branches are unchanged; class a without `holds`
is re-derived by requiring exactly one of the two conditions to hold at the value.

### 9.2 Predicted vs observed (run 3; 32 apps x 21 formed rules = 672 cells; 21 of 21 forms usable and `current`)

| probe | prediction (p) | gate | observed | status |
|---|---|---|---|---|
| PG1 | G == H on 85-97%; 0-8 cells G != H with H == T | 0 cells | G == H on **668 / 672 (99.4%)**, above the range; **4 cells G != H with H == T** (below) | **FAIL** |
| PG1t | outside K6 94-100%; K6 misses 0-4 | 100% outside K6 | G == T on 668 / 672 overall; outside K6 **572 / 576 (99.3%)**, 4 misses (the same 4 cells); K6 **96 cells, 0 misses** (R4, R11, R13 x 32 apps) | **FAIL** (K6 reported) |
| PG1o | exact (0-2 extra) | exact | G fails R14, R15, R16, R17, R21, R25 on `ok` = T's six; 0 extra, 0 missing | PASS |
| PG1k | G = H's kills + 1-5; G-only >= 1 | superset | G kills **20**, H kills **22** of 31 non-ok apps; G-only **0**, **H-only 2** (`m_read_clamp`, `m_cancel_clamp`) | **FAIL** |
| PG2 | same layer set >= 85% | reported | **99 / 223** (44%): all 124 differences are R14-R17 (G fails C and D, H only D) | reported, prediction missed |
| PG5 | 0 invalid accepted; 0-1 valid refused | 0 and 0 | 0 / 29 invalid accepted; 0 / 21 valid refused | PASS |
| PG5c | exact 85-100% | reported | **29 / 29** exact | reported |
| PT2f | precision 1.0; recall a-d 1.0; class e 0.5-1.0; 0 raises | precision 1.0, recall a-d 1.0, 0 raises | precision **1.0** (13 TP, 0 FP, 14 TN); recall a-d **11 / 11**; class e **2 / 4** (FN P24, P25); 0 raises | PASS (class e reported, inside the predicted range) |
| PT2x | 100% | 100% | 20 items, 0 failed re-derivations; 2 not independently re-derived ("weak": class c `fc:c:P13a:P13b`, `fc:c:P14a:P14b`) | PASS (weak disclosed) |
| PT2w | exact; D's 7 fixtures identical | gains exactly the new items | `tier2.determined` 1 item expected, 1 got, `tier: 2`, exit 0. D's 7 fixtures are not part of this stage (PB) | PASS (CLI part) |
| PNX | 0 accepted; F001/F017 on 90-100% | 0 accepted; every refusal F001 or F017 | 0 accepted of 12 attempts; **10 / 12 (83%)** refused with F001/F017; N12 and N13 refused with **F004 only** | **FAIL** |
| PX | 0.3-5 ms; arm 35-90 s | <= 1 s; <= 180 s | arm **132.4 s** (checks 130.9 s) inside the gate, above the predicted 35-90 s; the `generate` time alone is not measured in this stage | PASS (prediction missed) |

PG1 note list (library output): `F5:at_bound`, `F5:beyond`, `F6:at_bound`, `F6:beyond`: "the witness request does not carry the field".

### 9.3 Misses (findings; nothing tuned, library unchanged)

1. **PG1 / PG1t, `m_read_clamp` x R9 and `m_cancel_clamp` x R10 (G pass, H and T fail).** F5 and F6 are bounds on the
   path parameter `order_id` (`ge 1`), and the witness request is `/orders/1`. The library emits no at-bound or beyond case
   ("the witness request does not carry the field"), so an app that clamps order id 0 to 1 is not caught; the hand-written
   scenarios catch it. These two cells are also the PG1k H-only kills. Predicted: 0. Cause class: a bound on a path
   parameter has no generated case (a limit of the case synthesis, not a reader matter).
2. **PG1 / PG1t, `m_cancel_sneaky` x R19 (G pass, H and T fail).** The mutant cancels another buyer's pending order and
   then answers 404; F15 (`ownership`, `row: 3` as a key, `refused_other` 404 with db unchanged) does not flag it. Not
   root-caused in this run (no library or form probing was done).
3. **PG1 / PG1t, `m_pair2` x R15 (G fail, H and T pass): a false positive.** The mutant implements the 2-lines-per-
   (order, product) limit exactly; F11 (`by [order_id, product_id]`, `max 2`, caller ben, `/orders/3/items`) still reports a
   failure. Not root-caused in this run. Predicted: 0.
4. **PNX N12, N13** (`effect_value`: a stored value computed from, or equal to, a request value, written as an
   `accepted.db.equals` whose value is a `{sub}` / `{param}` node): refused with F004 ("not a literal") and not F001/F017.
   Same family as round 1's PNX misses. The refusal is correct; the code is not the registered one.
5. **PG2**: G reports layer C in addition to D on every R14-R17 cell (the row-limit forms generate an invariant); H
   has only D. Not a defect of correctness, but 44% vs the predicted >= 85%.
6. **PT2f class e**: P24 (ownership `accepted` 200 vs a path-param bound `accepted` 202 on the same request) and P25
   (bound 200 vs ownership `accepted` 203) are not reported: 2 of 4. P15 (a compatible pair) has 1 item that names a hand-
   written scenario, outside the pair, so it is not a false positive of the pair.
7. **PT2x**: the class c items cannot be re-derived independently by the runner (witness weak).
8. **Time**: the arm took 132.4 s against a predicted 35-90 s (still under the 180 s gate); 672 cells x CD checks.

### 9.4 Verdict for the gated stage

Passed: PG1o, PG5, PT2f, PT2x, PT2w (CLI), PX (and PG5c, PG2 reported). **Failed: PG1 (4 cells), PG1t (4 cells outside
K6), PG1k (H-only 2), PNX (N12, N13).** K6: 96 cells, 0 misses. PD1 (post hoc, section 3.2) was not re-run. NOT RUN: the
Streak arm, PA, PW, PG3, PG4 on the set. The gate rule of the prereg (section 6: every gated row) is **not met**.
