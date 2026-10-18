# Typed rule conditions, round 2 (Spike H2): pre-registration

## 1. Status, and the exact gap

2026-10-17. **Written before any round-2 code; no model was called, no API key was used, nothing was committed or
staged.** Base: branch `spike-h-typed-conditions` at d7ba7de (round 1 built, gated stage run once), worktree
`/home/user/wt/np-typedcond`, read only for this document. Sources: round 1's pre-registration
`2026-10-17-typed-conditions-prereg.md` (with Amendments 1 and 2) and its results `2026-10-17-typed-conditions-results.md`
(section 8 = the gated stage); format from `2026-10-16-change-impact-prereg.md`, round-2 discipline from
`2026-10-16-implementation-diff-round2-prereg.md`. Labels: **(m)** measured (read off round 1's committed data or hashed
here), **(ph)** post hoc (a statement about the round-1 set, which has now been seen), everything else a
**prediction (p)**. Results of this round: `2026-10-17-typed-conditions-round2-results.md`, data in
`docs/results/data/2026-10-17-typed-conditions-round2/`.

**This is a development round for the round-1 set.** The round-1 frozen rule-shape set (MANIFEST sha256
`182fe629e10241eded91e307abe8296db0a5877a08941fbdecef81a69d83af13`, 9 files) was written blind by a separate agent, but
it has now been opened by the round-1 runner, its failures were read by the round-1 author, and they are read again by
this author (section 8's 7 root causes quote its forms F2-F16, pairs P13-P30 and attempts N2-N9). From here on it is
**development data**: re-scored in round 2 only post hoc, never gated, never edited. Round 2's gates run on a **new
frozen set (H2) written by a new separate agent** (section 3.1), hashed and committed before any gated probe.

**Environment (m), binding on scoring.** This container has **no Streak checkout and no Docker daemon**; `go` and `uv`
work. So the **Docker-free minishop arm is the primary gate** (sqlite, in-process FastAPI); the Streak arm stays
registered and NOT RUN (section 3.4), and passing minishop does not stand in for it.

### 1.1 Round 1, blind vs post hoc (m; not re-scored here, restated so the two are never merged)

**Blind part.** The library (`forms.py` 849d33b2..., `forms_tier2.py` 6f9c2482...) was frozen before the set was opened
and was not touched afterwards. **Runner run 1** (the only runner written before the set was seen) produced **all 10
rows NOT RUN** (`KeyError: 'node'`: the set wrote condition nodes untagged). Every round-1 number below was therefore
measured by a **runner adapted after the set was opened** (runs 2-4: a reader for untagged nodes with `approved_sha`
recomputed over the tagged form; a class-e id filter; PG1k counted as flips relative to `ok`). The library was blind;
the runner was not. Dev-stage rows (PB, PW, PA, PG3, PG4, PX, 16/16 mutants) were run on the builder's own dev forms and
were never blind evidence.

| probe | round-1 pass rule | round-1 observed (library blind, runner post hoc) | status |
|---|---|---|---|
| PG1 | 0 cells with G != H and H == T | 249 such cells of 400 | FAIL |
| PG1t | G == T 100% | 150 / 400 (37.5%): 199 false fails, 51 misses | FAIL |
| PG1o | G fails on `ok` exactly T's violated rules (R12-R14) | G fails R6-R8, R14-R20; no check for R12, R13 | FAIL |
| PG1k | G kills a superset of H | G 3, H 18 of 24; H-only 15 | FAIL |
| PG2 | reported | 53 / 54 same layer set | reported |
| PG5 | 0 invalid accepted, 0 valid refused | 0 / 29 accepted; **5 / 16 valid refused** (F2-F4 F002 "needs a table"; F8, F9 F002 "counted-row template") | FAIL |
| PT2f | precision 1.0, recall a-d 1.0 | precision 1.0 (14/14); recall a-d 13/14 (b 0/1); class e 1/3; 5 pairs raise `AssertionError` | FAIL |
| PT2x | 100% | 19 items, 0 without witness (weak: not re-derived) | PASS (weak) |
| PT2w | CLI `tier2.determined` gains the form items | 0 items: `impact_report.py` frozen, never passes `forms_items` | FAIL |
| PNX | 0 accepted | 0 / 8 accepted; 3 with F004/F002 instead of F001/F017 | PASS |

**7 of 10 minishop probes failed**, 2 passed, 1 reported; Streak arm and drafter NOT RUN.

**Post hoc part (ph).** The 7 root causes (section 2, D0) were derived after the failures were seen. No fix was scored in
round 1. Any number this round produces on the round-1 set is post hoc (probe PD1) and is reported beside, never instead
of, the table above.

**Rulings requested.**
- **R1** Per cause, the classification of D0 (spec clarification of D1 text, library change, or set error). This
  document's position: **0 set errors**; causes 1, 2, 4, 5 are D1 ambiguities clarified in D1' and implemented; cause 3
  is a library departure from D1 as written (library change only); cause 6 is a registered limitation (no library
  change, scored as class K6); cause 7 is a checker-order change (D4 clarification). The untagged-node finding of runner
  run 1 is promoted to a library change (cause 0).
- **R2** **Unfreeze `lattice/spec/impact_report.py`** for one bounded additive branch so PT2w can pass through the CLI
  (D6). Round 1's D5 promised the widening and its D7 froze the only file that could deliver it; the two contradicted.
- **R3** Commission the new frozen set H2 (section 3.1) from a new separate agent; the gated stage waits for its
  manifest.
- **R4** Gate PG1t at 100% on cells **outside class K6** (cause 6), with K6 cells reported; or rule that cause 6 must be
  fixed in the library (question Q2).

## 2. Decisions (fixed before coding)

**D0 The root causes, decided one by one.** "Change" names the only place round 2 edits for it; budgets are lines of
diff in `forms.py` (F) / `forms_tier2.py` (T2) as `ruff format` lays them out. A cause whose fix exceeds its budget by
more than 50% is a finding, reported.

| # | round-1 cause (section 8) | classification | decision | budget |
|---|---|---|---|---|
| 0 | the set writes condition nodes untagged (`{op, value}`, `{values}`); the library required `node` (runner run 1) | D1 ambiguity (D1 names the nodes, never says they are tagged) | **library + D1'**: `node` is optional; inferred from the keys (`op` in `ge gt le lt eq ne` -> Cmp, `op` in `min_len max_len` -> Len, `values` -> In); a tag that disagrees with the keys is F001. `approved_sha` stays the sha256 of `{kind, form}` **as written** (normalisation happens after state resolution), so no recompute | F <= 25 |
| 1 | `bound` default accepted = 200; minishop answers 201 | D1 gap (`bound` has no accepted outcome) | **library + D1'**: optional `accepted: Outcome` on `bound`, `row_limit` and per endpoint on `ownership` (round-1 deviation 4, now in D1' so a set author can use it). When absent, the accepted status is **derived** from the spec: the unique 2xx `expect_status` of the hand-written steps on that endpoint template (then of a contract or program for it); if none or two different, 200 and a note `accepted_default:<endpoint>` in `generate`'s notes (never a refusal of a D1-valid form) | F <= 40 |
| 2 | ownership `row` read as a value; the set writes a path (`"/orders/1"`) | D1 ambiguity ("Request-path value") | **library + D1'**: `row` is either a key literal or a concrete request path. A string starting with `/` must match one of the form's endpoint templates or a static prefix of one; the row key is the value bound to its **first** placeholder; then every endpoint is instantiated with that key. A path matching no endpoint is F014 | F <= 30 |
| 3 | transition `row` read as the full table row; D1 says `{key: Lit}` | **library departure from D1** (D1 is clear) | **library only**: `row` is `{key: Lit}` (a full row stays accepted, backward compatible); the generator **completes** the row by the completion rule C (below). A column C cannot fill is F021 at `check` time, never an `IntegrityError` at run time | F <= 45 (shared with 4) |
| 4 | `row_limit` needs a counted-row template in `given[table][0]`; the set gives the parent's prior state; `generate` raises a bare `AssertionError` and aborts the whole set; F10's template replaced the row its own request addresses | D1 gap (`given?` is "the prior state"; no template is named) | **library + D1'**: `given` is the prior state only and is kept as written. Counted rows are **synthesised** by C, with the `by` columns bound by rule B (below) and `where` applied; given rows of the counted table that match the locus **count toward** the limit (`below` adds max-1-m rows, `at_limit` adds max-m, m = matching given rows; m > max-1 is F011). An explicit counted row in `given[table]` is still accepted as the template for the unbound columns. **No `assert` on a form that passed `check`**: every generator precondition is a `check` diagnostic (F021) | F <= 45 (shared with 3) |
| 5 | param `bound` with `refused.db: unchanged` refused by `check` (F002) unless `stores` is given | D1 gap (D1 never asks for `stores`; D5 names it) | **library + D1'**: `stores` stays optional. With no column and no `stores`, the locus table is derived: (a) the spec's contract or program for the endpoint, if it names one table; (b) else the unique table with a foreign key to the table the endpoint's first placeholder addresses whose columns cover the witness JSON keys; (c) else `unchanged` is asserted only on the form's own `given` rows (by key, values unchanged) and a note `db_locus:given_only` is emitted. Never F002 for a D1-valid form | F <= 30 |
| 6 | a column-only `bound` (no endpoint) is blind to an app that stops enforcing it (R4 on `h_no_stock_check`) | **neither**: D3 as registered (a column bound without endpoint is an invariant only; it can only fail on states some scenario reaches) | **no library change**; D1' says so in one sentence. Scored as class **K6** (R4, question Q2). A form that wants the refusal checked must name an endpoint | 0 |
| 7 | the checker types the literal before the node: refusal F004/F002 instead of F001/F017 (N2, N3, N9) | D4 order unspecified | **library + D4'**: node and key validity first. A `value` (or `values` item) that is not a `Lit` is F017 when its key is a not-expressible node name (`now today date sum avg min max count or any all param column ref add sub mul div`), else F001; F004/F002/F005 are only reported for Lits | F <= 20 |
| T2 | 5 pairs make `contradictions` raise (cause 4) | follows from 4 | **forms_tier2.py**: per pair, a form the generator refuses yields no claim and an `undecided` note (`pair_undecided:<F>`), never an exception, never a `compatible` claim | T2 <= 15 |

Total budget: `forms.py` <= 235 changed lines (round 1: 1989), `forms_tier2.py` <= 15. No other library module changes
except `impact_report.py` (D6).

**Rule B (binding a `by` column, deterministic, in order).** (1) a witness path placeholder, JSON key or query key with
the column's name; (2) the endpoint's first placeholder, when the column is a foreign key to the table that the
placeholder's key column addresses (the profile's FK, `resolve_table`); (3) the caller's user id, when the column is the
table's `Schema.owner`; (4) else F021 naming the column.

**Rule C (completing a row, deterministic).** Start from the form's row (key, machine column per case, bound `by`
values, `where`). Then for every NOT NULL column without a default, in schema order: the owner column -> the caller's
user id (`other` cases keep the owner's id: the row belongs to the owner); a foreign key -> the smallest key of the
referenced table among the form's `given` rows, else of the seed users for a users FK, else F021; a machine column -> the
machine's initial state; a `one_of` column -> the first listed value, sorted; otherwise the typed zero (`0`, `0.0`, `""`,
`false`). Keys of synthesised rows are chosen above the largest key in `given` and the seed (round-1 D3, unchanged).

**D1' The node set, clarified (replaces round-1 D1 for round 2; the grammar's power is unchanged).** Round-1 D1 table
and kinds verbatim, plus exactly these sentences: (i) "A condition's `node` tag is optional; it is inferred from its
keys." (ii) "`bound`, `row_limit` take an optional `accepted: Outcome`; `ownership` an optional `accepted: {Endpoint:
Outcome}`; when absent, the accepted status is the one the spec's own steps expect on that endpoint, else 200." (iii)
"Ownership `row` is the owned row's key, or a concrete request path of one of the endpoints." (iv) "Transition `row` is
`{key: Lit}`; Lattice completes the other columns." (v) "`row_limit.given` is the prior state; Lattice synthesises the
counted rows; given rows that match count toward the limit." (vi) "A param `bound` may name `stores: Table.col`; it is
optional." (vii) "A column `bound` without `endpoint` states a stored-value rule: it generates an invariant only." D6
(not expressible) is round 1's verbatim. **D4'** = round-1 D4 plus cause 7's order and **F021** "the generator cannot
complete a row or bind a column (names the column)". F020 keeps its round-1 meaning.

**D2 Inherited unchanged:** round-1 D2 (file, approval, states), D3 (generation; amended only by causes 1-5), D5
(classes a-e, witness self-check), D7 (never), Amendment 1 (O1-O4: no `--forms` on plain `spec check`, no L013).

**D3 Scoring definitions fixed now (the round-1 results asked for them before a set is scored).** A cell is (formed rule,
app). H = the agent's hand-written elements linked to that rule. A **kill** is a cell that **flips** to fail relative to
`ok` (round-1 run 4's reading, now registered), counted only over formed rules (H's kills through not-formed rules, such
as `money` and `effect_value`, are reported apart). **Crosstalk** (round-1 finding 8: a hand-written invariant failing
inside a generated scenario) is attributed to the rule whose element **failed**, not to the scenario's rule, and listed.

**D4 Never (round 2).** Edit, re-label or extend the round-1 set; gate on it; open the H2 set before `freeze2.sha256`
and `shapes2.manifest.sha256` are both committed; call a model (the builder and this author); use an API key; edit any
file of section 7's frozen list; let `generate` or `contradictions` raise on a form `check` accepts.

## 3. Probes (runner `dogfood/forms_probes2.py`; numeric pass rules, blind)

### 3.1 The new frozen set H2 (commissioning only; this author does not write it)

**Who.** One **new** separate agent: not the round-1 shapes agent, not the round-1 or round-2 builder, not this author,
not the orchestrator. Dispatched by the orchestrator through the `claude` CLI (no API key, `--max-budget-usd` capped,
question Q8); a usage limit is NOT RUN and retried later, never replaced by the builder.
**Prompt contains only:** sections D1' and D6 of this document verbatim (with D4' and its code list, for `invalid.json`), read
access to `tests/spec/minishop.py` and `tests/spec/fixtures/impact/base/`, and the output contract below.
**It must not read:** `lattice/spec/forms*.py`, `dogfood/forms_*`, `tests/spec/test_forms*.py`,
`tests/spec/fixtures/forms*/`, any `docs/results/*typed-conditions*` file or data directory, the round-1 set's directory.
The orchestrator runs it in a fresh scratch tree that contains only the permitted files (a listing is stored with the
manifest), so the exclusion is enforced, not requested.
**Writes** to `$SCRATCH/hshapes2/` (outside the repo), the same files as round-1 section 3.1 with these minimums:

| file | minimum |
|---|---|
| `minishop/REQUIREMENTS.md` | >= 20 rules, >= 4 per kind, plus the 5 base rules |
| `minishop/handwritten.py` | every formed rule linked to >= 1 hand-written element |
| `minishop/forms.json` | >= 16 approved forms; each D1' optional key (`accepted`, `stores`, `given`, `node` tag) used in >= 2 forms and omitted in >= 2 where it could apply; >= 2 ownership `row`s written each way |
| `minishop/mutants.json` | >= 14 app edits, >= 3 per kind |
| `minishop/truth.json`, `minishop/h_verdicts.json` | all cells (ok + 4 BROKEN + 6 SEC_BROKEN + its mutants: >= 25 apps) |
| `pairs.json` | >= 24: >= 10 contradictory, >= 10 compatible near-misses, >= 2 per class a-d, >= 4 class e (>= 2 form vs hand-written) |
| `invalid.json` | >= 20, >= 12 distinct codes, F021 included |
| `not_expressible.json` | >= 15 rules, >= 7 categories, >= 10 with an attempted form |
| `MANIFEST.sha256` | sha256 of every file, sorted by path |

**Order (binding).** (1) Round-2 build done; `sha256sum lattice/spec/forms.py lattice/spec/forms_tier2.py
lattice/spec/impact_report.py > freeze2.sha256` copied to the data directory. (2) The agent writes H2; if its
`h_verdicts.json` shows a hand-written check failing on `ok` against `truth`, the agent fixes or excludes it **before**
the manifest. (3) The orchestrator copies `MANIFEST.sha256` to
`docs/results/data/2026-10-17-typed-conditions-round2/shapes2.manifest.sha256` and commits `freeze2.sha256` and it
**before any gated probe**, recording the sha256 of the manifest itself in an Amendment. (4) The runner checks both
hashes first (a mismatch aborts) and runs the gated stage **once**. (5) A runner layout mismatch on H2 is a NOT RUN, fixed
in the runner only, rerun, and disclosed as a runner change (round 1's run-1 lesson); the library is never edited after
step 1.

### 3.2 Primary gate: the minishop arm on H2 (Docker-free)

Cells >= 16 formed rules x >= 25 apps = **>= 400**. K6 = cells of a column-only `bound` (no endpoint).

| id | probe | prediction (p) | pass (gated unless "reported") |
|---|---|---|---|
| PG1 | G vs H per cell | G == H on 85-97%; G != H with H == T on 0-8 cells | **0 cells with G != H and H == T** |
| PG1t | G vs T per cell | outside K6: 94-100%; K6 misses 0-4 cells | **100% outside K6**; K6 reported (count, apps) |
| PG1o | on `ok` | G fails exactly T's violated-on-ok rules (p: 0-2 extra) | **exact** |
| PG1k | kills by flips, formed rules only | G kills H's kills plus 1-5 more (p); G-only >= 1 | **G's kills a superset of H's** |
| PG2 | layer where both fail | same layer set >= 85% | reported |
| PG3 | `forms generate` and CD 10x, random `PYTHONHASHSEED`, two roots | 10/10 | **10/10 byte-identical** |
| PG4 | generated invariants compile (sqlite, mysql); scenarios validate; 0 L011/L012; no raise | 100% | **100%, 0 raises** |
| PG5 | `invalid.json` and the valid forms through `check` | 0 accepted; 0-1 valid refused; codes exact 85-100% | **0 invalid accepted; 0 valid refused** |
| PG5c | exact F-code on `invalid.json` | 85-100% | reported |
| PT2f | `forms contradictions` on `pairs.json` | precision 1.0; recall a-d 0.9-1.0; class e 0.5-1.0; 0 raises | **precision 1.0; recall a-d 1.0; 0 raises**; class e reported |
| PT2x | witness, **strong**: the runner re-derives both generated cases and calls `impact_tier2.incompatible` itself | 100% | **100%** |
| PT2w | `lattice spec impact --tiers 23 --json` on a minishop candidate adding one H2 form that contradicts a base form; and D's 7 impact fixtures without forms | exact; identical | **`tier2.determined` gains exactly the new form items (`tier: 2`); D's 7 fixtures byte-identical to d7ba7de** |
| PNX | `not_expressible.json` attempts through `check` | 0 accepted; F001/F017 on 90-100% | **0 accepted; every refusal carries F001 or F017** |
| PA | approval states, as round 1 (draft writes only proposals; no TTY / `CLAUDECODE` refuses; edited, stale, moved) | all | **all** |
| PW | snapshots around `show/check/generate/contradictions/coverage` x10 and a check with forms; recording `claude` shim | 0, 0, 0 | **0 changed, 0 new, 0 shim calls** |
| PX | wall time: `generate` on H2; the whole arm | 0.3-5 ms; 35-90 s | **<= 1 s; <= 180 s** |

### 3.3 Development and compatibility probes (round-1 data; run before the freeze and again after)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| PD1 | **post hoc**: the round-1 set (manifest 182fe629, hash-checked) scored by `forms_probes2.py` with the round-2 library; every row reported with its round-1 value and the delta, labelled post hoc | PG1t 393-399 / 400 (misses: R4 on `h_no_stock_check` (K6), 0-6 crosstalk or derivation cells); PG1o exact (R12, R13, R14); PG1k G 17-21, superset of H's 18 (p: yes); PG5 0 / 16 valid refused; PT2f recall a-d 14/14, class e 3/3, 0 raises; PNX codes 8/8 F001/F017 | **reported, never gated** (tuned-on data) |
| PC | one dev case per cause 0-7 on new builder fixtures in `tests/spec/fixtures/forms_r2/` (untagged node; 201 endpoint without `accepted`; ownership row as path; transition `{id}` only; row_limit with parent-only `given`; param bound without `stores`; column-only bound documented; non-Lit `value`) | 8/8 | **8/8** |
| PR1 | round-1 dev fixture `tests/spec/fixtures/forms/minishop/` under round-2 code: `forms generate` bytes | identical except F7's two scenarios (F7 used a `given` template; cause 4 changes its synthesised keys; its invariant is identical) | **only F7's `below`/`at_limit` differ**; the 71 round-1 tests pass unedited |
| PR2 | round-1 runner `dogfood/forms_probes.py` (unchanged) `--stage dev` | PB, PW, PA, PG3, PG4 as round 1 | as round 1 |
| PB | byte-compat with no forms file, against `git archive d7ba7de` (and round-1's 6a318e5 targets): `lattice --help`; `python -m lattice.spec` usage and `check/lint/impact --help`; `lint --json`, `check --json` (CD) on the minishop base fixture and `attack_spec`; `impact --tiers 23 --json` on D's 7 fixtures; `appmap --json`, `rationale coverage --json`; frozen hashes of section 7; pytest failing ids | identical after removing the timing keys `total_s`, `seconds`, `db_clone_s`, `elapsed_s` (registered list; any other differing key is a miss) | **all identical; failing ids = the pre-edit set** |

### 3.4 Registered, NOT RUN here: the Streak arm

Round-1 section 3.3 unchanged (PS0, PS1, PS1t, PS2, PS3, PSx, with its numbers), on an H2 `streak/` directory written
when a checkout exists. Recorded `NOT RUN: no Streak checkout, no Docker daemon`, no number, no substitute. The model
drafter (`forms draft`) stays report-only and is not called by the build.

**Probe count: 21** (16 rows in 3.2, 5 in 3.3), plus 6 Streak rows registered NOT RUN. Gated (17): PG1, PG1t, PG1o,
PG1k, PG3, PG4, PG5, PT2f, PT2x, PT2w, PNX, PA, PW, PX, PC, PR1, PB. Reported (3): PG2, PG5c, PD1. PR2 is a
non-regression check whose miss fails PB.

## 4. Mutants (`dogfood/forms_mutants2.py`, scratch copy; control first; predicted 16/16 + round-1 16/16)

Killed when a gated probe that passes on the control fails, a PC/PR1 case flips, or a unit test of
`tests/spec/test_forms_r2.py` fails (named per mutant, disclosed as round 1 did). The round-1 mutants HX1-HX16 are
re-applied to the round-2 code and must still be killed (predicted 16/16; an HX that no longer applies textually is
re-expressed and disclosed).

| id | mutant (cause) | predicted catcher |
|---|---|---|
| HY1 | untagged condition nodes refused (0) | PC-0, PG5 valid refused |
| HY2 | `approved_sha` computed over the normalised (tagged) form (0) | PA: every untagged form reads `edited` |
| HY3 | accepted status not derived, fixed 200 (1) | PC-1, PG1o |
| HY4 | two different 2xx statuses on an endpoint: the first is taken instead of the 200-plus-note fallback (1) | unit |
| HY5 | ownership path `row` used as a value (round-1 behaviour) (2) | PC-2, PG1o |
| HY6 | the key taken from the last placeholder, not the first (2) | unit (nested route `/orders/{}/items/{}`) |
| HY7 | transition row not completed (3) | PC-3, PG1o (an `IntegrityError` on ok) |
| HY8 | completion puts the `other` user's id in the owner column of an `other` case (3, C) | PG1k on an ownership mutant; unit |
| HY9 | given rows of the counted table replaced, not counted (round-1 template semantics) (4) | PC-4 (the R14-shaped case: 404) |
| HY10 | rule B step (2) (foreign key to the placeholder's table) off (4) | PC-4, PG5 valid refused (F021) |
| HY11 | `generate` raises on an incomplete row instead of F021 at `check` (4) | PG4 "0 raises", PT2f "0 raises" |
| HY12 | param bound without `stores` refused F002 (round-1) (5) | PC-5, PG5 |
| HY13 | derived locus dropped: refused param step asserts no db effect even when a table is derivable (5) | PG1 on a refused-write mutant; unit |
| HY14 | literal typed before node (round-1 order) (7) | PC-7, PNX |
| HY15 | `impact_report.py` never passes `forms_items` (round-1) | PT2w |
| HY16 | `impact_report.py` builds forms items with no forms file (adds a key or an item) | PB (D's 7 fixtures) |

**Mutant count:** 16 new (HY1-HY16) + 16 round-1 re-applied (HX1-HX16) = 32.

## 5. Risks, and what this cannot show

1. **D1' encodes the fixes.** The new agent reads clarifications written because of the round-1 failures; H2 is then
   easier for this library than an unprepared author's set would be. This is the point of a clarification, but it means
   round 2 tests "the library meets D1'", not "D1 was right". The quotas of 3.1 force the clarified keys to appear both
   used and omitted, which limits, not removes, the advantage.
2. **Derivations are heuristics stated as rules** (accepted from the spec's steps, B, C, the cause-5 locus). G now
   borrows H's accepted status when a form omits it: if H is wrong there, G is wrong the same way, and PG1 cannot see it
   (PG1t can, through T).
3. **K6 is a carve-out.** Excluding column-only bound cells from the PG1t gate is decided before H2 exists, but it is
   still a lowered bar for one shape; K6 cells are reported with their count, and an H2 with many column-only bounds
   would make PG1t weaker (question Q2).
4. **Development contamination.** This author, the round-1 author and the round-2 builder have all seen the round-1 set.
   PD1 will look good by construction (predicted 393-399 / 400) and is labelled post hoc everywhere.
5. **A new independent author finds new readings.** Round 1 met 2 of 10 gated rows; a cleaner library and clearer text
   do not guarantee H2 passes. Prediction for the whole gate: met with probability about 0.35 (p); the most likely misses
   are PG1t outside K6 (a new derivation case) and PT2f class a-d recall (a pair shape not tried).
6. **One agent writes rules, checks, mutants and truth** (round-1 risk 4, unchanged): no second labeller.
7. **minishop is small and sqlite only** (round-1 risks 5, 6): the Streak arm stays the only test of scale and MySQL.
8. **The `impact_report.py` unfreeze** touches D's frozen report path; PB on D's 7 fixtures is its only guard.
9. **Cannot show:** form-to-code mapping, drafter quality, the Streak arm, MySQL execution of generated invariants.

## 6. Blind vs post hoc, and commands

Fixed now: D0-D4, D1', D4', rules B and C, the gates, the K6 definition, the mutant list. A missed prediction is a
finding. H2 is scored **once** after `freeze2.sha256` and `shapes2.manifest.sha256` are committed; a fix after that is a
separate post hoc commit, rerun with deltas labelled post hoc, never counted as blind. PD1 (the round-1 set) is post hoc
in every run. **Pass:** every gated row of 3.2 and 3.3, HY 16/16 and HX 16/16 (by probes or named unit tests, disclosed),
PB. The Streak arm's rows are NOT RUN, which is not a pass.

```sh
export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
SCRATCH=<the build node's scratchpad>/forms2
cd /home/user/wt/np-typedcond
uv run python -m pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck -q -rs > "$SCRATCH/pre.txt"  # before any edit
uv run python dogfood/forms_probes2.py --stage dev --scratch "$SCRATCH/d" --out "$SCRATCH/d-data"   # PC, PR1, PB, PW, PA, PG3, PG4
uv run python dogfood/forms_probes2.py --stage round1 --shapes <round-1 hshapes> \
    --manifest docs/results/data/2026-10-17-typed-conditions/shapes.manifest.sha256 --out "$SCRATCH/pd1"   # PD1, post hoc
uv run python dogfood/forms_probes.py --stage dev --scratch "$SCRATCH/r1" --out "$SCRATCH/r1-data"      # PR2, round-1 runner
uv run python dogfood/forms_mutants2.py $(seq -f HY%g 1 16) $(seq -f HX%g 1 16) --scratch "$SCRATCH/mut"
sha256sum lattice/spec/forms.py lattice/spec/forms_tier2.py lattice/spec/impact_report.py > "$SCRATCH/freeze2.sha256"
# orchestrator: commission H2 (3.1), commit freeze2.sha256 + shapes2.manifest.sha256, then once:
uv run python dogfood/forms_probes2.py --stage frozen --shapes "$SCRATCH/../hshapes2" \
    --manifest docs/results/data/2026-10-17-typed-conditions-round2/shapes2.manifest.sha256 \
    --freeze docs/results/data/2026-10-17-typed-conditions-round2/freeze2.sha256 --scratch "$SCRATCH/f" --out "$SCRATCH/f-data"
uv run ruff check <changed .py>; uv run ruff format --check <created .py>
```

## 7. File ownership, frozen hashes and the byte-compat promise

**Owns (round-2 build node, no commits, never `git add`):**
- **May change (unfrozen from round 1, re-frozen in `freeze2.sha256`):** `lattice/spec/forms.py` (round-1 freeze
  `849d33b26c2c0ba9f0d88584ffd15a721e4a5d85540bf1e24991d1cb35837647`), within D0's budgets; `lattice/spec/forms_tier2.py`
  (`6f9c2482b65a3b1c5c92a82398c3b65be8ea3b0b20020390acfced66bf1646e2`), the T2 row only; **`lattice/spec/impact_report.py`**
  (`a3e11f1c266a813a66e33d8943b8773705a419ce8dde806615203d750ae0137c`), D6 only.
- **D6 the `impact_report.py` edit:** one additive branch, <= 30 lines, taken only when `REQUIREMENTS.forms.json` exists
  beside the base `--project` or the `--candidate` requirements (a change record has no forms: branch never taken). It
  passes `forms_items=` to `impact_tier2.compute` (round 1's keyword, unchanged): the candidate's contradiction items
  (approved, `current`/`moved` forms, against the candidate spec) whose `(class, forms, witness)` is not among the
  base's. `decidability[]` reason `form` for formed rules: implemented if it fits the budget, else reported as not done.
  Without a forms file the report bytes are identical (PB, HY16).
- **Creates:** `dogfood/forms_probes2.py`, `dogfood/forms_mutants2.py`, `tests/spec/test_forms_r2.py`,
  `tests/spec/fixtures/forms_r2/**`, the round-2 results doc and data directory; this file only by appended Amendments.
- **May edit additively:** `tests/spec/test_forms.py` only if a round-1 test pins round-1 behaviour that a cause
  overturns (only cause 4's template semantics can); each such edit is disclosed by test id; no test is deleted or
  loosened otherwise (PR1 predicts 0 such edits).

**Stays frozen (sha256 at d7ba7de, m; any change is a finding):**

| file or set | sha256 |
|---|---|
| `lattice/spec/model.py` | `a7f7b3f834f22a89004f6bfb69484f6c4ff35caf86a32f4254c6a1182c9ec9ce` |
| `lattice/spec/check.py` | `68f13b288cde4a3fa348412495ba560815e003b342ea9cca90b1a779c1bb525e` |
| `lattice/spec/invariants.py` | `c3f374756d0b4186bea4df00c1f910ed2942b8bfcb771a8df472793d48354a73` |
| `lattice/spec/lint.py` | `0462853748f3c0101db4f00633c708e98f209fde526fb7eb6563c665f087b442` |
| `lattice/spec/preconditions.py` | `f1d8abee4f8d92b26fac557081967757736a7f3c8d0aaaa806134b8136595687` |
| `lattice/spec/rules.py` | `e3c0bc041595de280abae7c9beebbab64f09e0fd55182e173ee623c4dfddb248` |
| `lattice/spec/incremental.py` | `29ded6ecbbddcc55a5d94914cf9bce82fb9ee61a10d3453d70c336565c6090e6` |
| `lattice/spec/rationale.py` | `ea0f05dd47a27934d0e6d5d6e20d0964c1449f7667966e8c5c4e495de9c434e9` |
| `lattice/spec/impact_tier3.py` | `22228513beba44fcff4b0d5ab48c662d2010e7ac320b73ac860f134ffb6e2faf` |
| `lattice/spec/validate.py` | `97691c98b66a49f656315dea00225e1465861e99533e5886b10718be1427b012` |
| `lattice/spec/impact_tier2.py` (round-1 additive state) | `d0e95f902b1ba1f6435a8ffe2624912706815222c46964e687bbfedd063dc8a0` |
| `lattice/spec/__main__.py` (round-1 `forms` line) | `e24bf2ff626f841c50606cc5ea208cab029981b0cec10c3bca5ab4570381faea` |
| `tests/spec/minishop.py` | `90117594e10c61272b8896bfc631623c5d23c4689d8e1853649cf73810baccfd` |
| `dogfood/forms_probes.py` (round-1 runner, so round-1 numbers reproduce) | `7449106201f93f6108addcd557f54ef353ccbb53322c05a3b4bda04eba3525c0` |
| `dogfood/forms_mutants.py` | `7b9483ff29f80b91164c594e0312beb50fc0e4b1ba5409cd83360967e2240597` |
| `lattice/check/**` (27 files) | `ca52b5a0076a04328c97c1cdafdd50af1a557ac671795697f9ec448146ba57bf` |
| `lattice/gocheck/**` (24 files) | `e9515e859e4aebff1020d8fffa721e8859396b9545d4d7b2b768d4980a367c3f` |
| `tests/spec/fixtures/impact/**` (21 files) | `291eab2058ca5c86079b73287d1feff42e97afcd9a53f2358322df7b75473d1b` |
| `tests/spec/fixtures/forms/**` (round-1 dev forms, 4 files) | `ee956fc0472296d5eeb58b743c79bcd8014249032a9699d63ec4a6cd6e5cd0be` |
| round-1 frozen set (outside the repo) | MANIFEST `182fe629e10241eded91e307abe8296db0a5877a08941fbdecef81a69d83af13` |

Set hashes use one registered method: `git ls-files -z DIR | sort -z | xargs -0 sha256sum | sha256sum`. It reproduces
round 1's `lattice/check` (ca52b5a0) and impact-fixture (291eab20) values; for `lattice/gocheck` it gives e9515e85, the
round-1 builder's value, not round-1 prereg's b7a4dacf (unexplained there; `git diff 6a318e5 -- lattice/gocheck` was
empty). Also unchanged: `docs/results/2026-10-17-typed-conditions-{prereg,results}.md` and
`docs/results/data/2026-10-17-typed-conditions/**` (`git diff d7ba7de --` on them empty).

**Byte-compat promise.** (1) With no `*.forms.json` / `*.forms.proposed.json` anywhere, every command, report, lint
output, impact report (D's 7 fixtures included), app map, MCP listing and Studio response is byte-identical to d7ba7de
after removing the registered timing keys (PB); `python -m lattice.spec` usage is unchanged. (2) With the round-1 dev
forms, `forms generate` and `forms check` output is identical except F7's two `row_limit` scenarios (PR1). (3) No new
key appears in any existing JSON output when no forms file exists. Existing tests and goldens are not edited (except
the disclosed case above).

## 8. What cannot be run here

- The Streak arm (PS0-PSx): no Streak checkout, no Docker daemon. NOT RUN, registered.
- MySQL execution of generated invariants: compiled only (PG4), executed on sqlite.
- The model drafter: not called by the build (no model calls); report-only when the orchestrator runs it.
- The H2 set itself: written by the commissioned agent, not by this author or the builder; until its manifest is
  committed, every 3.2 row is NOT RUN.

## 9. Questions for the orchestrator

- **Q1** The relayed user request reads "y1,2 3". This document takes it as the go-ahead for round 2 and infers no
  ruling from it. If it answers numbered options from an earlier message (for instance R1-R3 above, or round-1 O1-O4),
  please map it explicitly.
- **Q2** Cause 6: accept class K6 (column-only bound cells reported, outside the PG1t gate), or require a library change
  (e.g. generating a `beyond` step from a hand-written writing step on that column), or keep PG1t at 100% on every cell
  and record a predicted miss?
- **Q3** Confirm the `impact_report.py` unfreeze (R2, D6, <= 30 lines, branch only with a forms file).
- **Q4** Is deriving the accepted status from the spec's own hand-written steps acceptable (risk 2), or must a form
  without `accepted` default to 200 with a note only?
- **Q5** Accept F021 as a new D4' code, and the classification "0 set errors" (D0)?
- **Q6** The `lattice/gocheck` set hash: accept the registered `git ls-files` method and e9515e85 as the reference?
- **Q7** Spike E has since landed on `next-phases` (1955f0b). Build round 2 on d7ba7de (this document) or rebase onto
  next-phases first, and does round-1 O1 ("E's before/after runs not applicable") still hold?
- **Q8** Commissioning H2: the USD cap per call and in all, and confirmation that its prompt includes D1' (risk 1).
- **Q9** This file was written in `/home/user/lattice/docs/results/` (the `next-phases` checkout) because the
  `np-typedcond` worktree was read-only for this step; the round-2 builder works in that worktree. Copy it there before
  the build, or keep it here?
- **Q10** PD1 re-scores the round-1 set with the round-2 library (post hoc). Confirm that running it at all is wanted;
  it is information only and will look good by construction.

## Amendment 1 (orchestrator rulings, 2026-10-17; defaults, overturnable)

Q1: the owner's "y1,2 3" approved a round 2 for H and I and the E merge; it is the go-ahead only. Q2: accepted: cause 6 cells are scored as the separate class K6, reported, and kept out of the PG1t gate; the carve-out is stated in the results. Q3: accepted: `impact_report.py` is unfrozen for one additive branch of at most 30 lines taken only when a forms file exists. Q4: where the spec has a hand-written step for the endpoint, `accepted` is derived from it; otherwise a missing `accepted` defaults to 200 with a note in the output. Q5: accepted (F021; 0 set errors). Q6: adopt the `git ls-files` method and e9515e85 as the gocheck hash reference; round 1's b7a4dacf is recorded as unreproducible. Q7: the build merges next-phases (Spike E, 1955f0b) into `spike-h-typed-conditions` first; round-1 O1 still holds. Q8: the H2 author is a Claude Code subagent (no API key, no per-call USD cap); its prompt may include the D1' clarifications. Q9: this file is merged into the build branch. Q10: PD1 runs, reported-only and labelled post hoc.

## Amendment 2 (records the verifier found missing, 2026-10-18)

sha256 of the H2 `MANIFEST.sha256` file: 6a6f40c69ae3eaed62655cf51653c34098e36a405dbf28242feef107b9003a8f (it lists 11 files; round-2 results say "9 set files", a miscount). The runner `dogfood/forms_probes2.py` was c52d27f5... at f49fa97, before H2 was opened; it changed after (see round 3).
