# Typed rule conditions, round 3 (Spike H3): pre-registration

## 1. Status, and the exact gap

2026-10-18. **Written before any round-3 code and before any root-cause probing; no model was called, no API key was
used, nothing was committed or staged.** Base: `next-phases` at `3679ab2` (the merge of `spike-h-typed-conditions`,
rounds 1-2, "gate NOT met, merged at the owner's request as a partial pass"), checkout `/home/user/lattice`. Read only
for this document: `/home/user/wt/np-typedcond` `docs/results/2026-10-17-typed-conditions-round2-{prereg,results}.md`
(section 9 = the gated stage on H2; byte-identical to the copies in this checkout (m)). Format from
`2026-10-16-change-impact-prereg.md`; round-2 discipline from `2026-10-17-grpc-round2-prereg.md` and
`2026-10-17-typed-conditions-round2-prereg.md` (with its Amendment 1). Results of this round:
`2026-10-18-typed-conditions-round3-results.md`, data in `docs/results/data/2026-10-18-typed-conditions-round3/`.
Labels: **(m)** measured here (hashed or counted while writing this), **(r)** read from round-2 data or the H2 files
while writing this, **(ph)** post hoc, everything else a **prediction (p)**.

**Round 3 is a development round for both earlier sets.** The round-1 set (MANIFEST `182fe629...`) and the round-2 set
H2 (the one scored once, section 1.1) have both been opened, scored and read: H2's failing cells were printed in the
round-2 results and this author read the H2 forms F5, F6, F11 and F15 (r) while writing this. **Both are development data
from now on: re-scored only post hoc (PD1, PD2), never gated, never edited.** Round 3's gates run on a **new frozen set
H3 written by a new separate agent** (section 4), whose manifest hash is committed (and the manifest file's own sha256
recorded in an Amendment) **before any gated probe**, and which is scored **once**.

**Environment (m), binding on scoring.** No Streak checkout and no Docker daemon in this container; `go` and `uv` work.
**The Docker-free minishop arm (sqlite, in-process FastAPI) is the primary and only gated arm.** The Streak arm stays
registered and NOT RUN (section 5.4); passing minishop does not stand in for it.

### 1.1 Round 2 on H2: blind and post hoc kept apart (m/r; restated, never merged)

Library `freeze2.sha256` (forms.py `dd9b7566...`, forms_tier2.py `288beb57...`, impact_report.py `aa29aede...`) was
committed in `f49fa97` with `shapes2.manifest.sha256` (11 entries (m); the results text says "9 set files": a
miscount, corrected here) before the set was scored, and the library did not change afterwards (m: same hashes at
`3679ab2`). The **runner did change after the set was opened**: `dogfood/forms_probes2.py` was `c52d27f5...` (939
lines) at `f49fa97` and is `9e3ec0ad...` at `3679ab2` (+165 / -47, `git diff --numstat f49fa97 ee664ae`) (m). No runner
hash was recorded before opening (verifier's process error 2); it is recoverable only from git.

| probe | gate | run 1 (runner as committed before opening, `c52d27f5`) | run 3 (library blind, reader adapted after opening) | kind of runner change before the number | status for round-3 purposes |
|---|---|---|---|---|---|
| PG1 | 0 cells G != H, H == T | NOT RUN (`TypeError`, truth layout) | 668 / 672; 4 cells | reader only (`_truth_cells`) | **FAIL, blind-library** |
| PG1t | 100% outside K6 | NOT RUN | 572 / 576 outside K6; K6 96 cells, 0 misses | reader only | **FAIL, blind-library** |
| PG1o | exact | NOT RUN | exact (R14-R17, R21, R25) | reader only | PASS, blind-library |
| PG1k | superset | NOT RUN | G 20, H 22; H-only 2 | reader only | **FAIL, blind-library** |
| PG2 | reported | NOT RUN | 99 / 223 (44%) same layer set | reader only | reported (prediction >= 85% missed) |
| PG5 / PG5c | 0 and 0 / reported | NOT RUN | 0 / 29, 0 / 21; 29 / 29 exact | reader only | PASS, blind-library |
| PT2f | precision 1.0, recall a-d 1.0 | NOT RUN | precision 1.0, recall a-d 11/11, class e 2/4 | **scoring**: class e re-derived from the pair's witness (run-2 fix) and the `items_outside_the_pair` rule (an item naming a form or step outside the pair is not counted) | **post hoc**: not a blind pass |
| PT2x | 100% | NOT RUN | 20 items, 0 failed, 2 weak (class c) | **scoring** (class e re-derivation, run-2 fix) | **post hoc, weak** |
| PT2w | gains exactly the new items | NOT RUN | 1 / 1, `tier: 2` | reader only (wrapper `spec.py`) | PASS, blind-library |
| PNX | every refusal F001/F017 | NOT RUN | 10 / 12; N12, N13 F004 only | reader only | **FAIL, blind-library** |
| PX | <= 1 s; <= 180 s | NOT RUN | arm 132.4 s; `generate` alone not measured | | PASS (half the row unmeasured) |
| PA, PW, PG3, PG4 on H2 | as registered | NOT RUN | NOT RUN (not in that stage's list) | | **NOT RUN: not a pass** |

**Strictly blind (runner frozen before opening), round 2 has 0 scored rows.** Read with a reader adapted after opening
and the library blind: 4 gated rows fail (PG1, PG1t, PG1k, PNX), 5 pass (PG1o, PG5, PT2w, PX; PT2f and PT2x only after a
scoring change, so post hoc), 4 were never run on H2. Process error 1 (verifier): the sha256 of the manifest file itself
was not recorded in an Amendment. It is recorded here (m): `sha256sum shapes2.manifest.sha256` =
`6a6f40c69ae3eaed62655cf51653c34098e36a405dbf28242feef107b9003a8f`, and the H2 files at
`<orchestrator scratchpad>/hshapes2/` pass `sha256sum -c MANIFEST.sha256` today (m).

### 1.2 The residual causes (from round-2 9.3, plus the verifier), each with its status now

| # | residual | cells / rows (r) | what is known | status before code |
|---|---|---|---|---|
| a | path-parameter bounds F5 (R9), F6 (R10): `field.path = path.order_id`, `ge 1`, witness `/orders/1` | PG1 2 cells (`m_read_clamp` x R9, `m_cancel_clamp` x R10), = PG1k H-only 2 | library note "the witness request does not carry the field" for `F5/F6:at_bound`, `:beyond`: no case generated | **cause known**; decided in D0 |
| b | `m_cancel_sneaky` x R19: G passes, H and T fail. F15 = ownership, `row: 3` (key), owner ben, other ann, `refused_other` 404 + `db unchanged` | PG1 1 cell | the mutant cancels another buyer's pending order, then answers 404 | **cause unknown**: root-cause node RC first (section 3) |
| c | `m_pair2` x R15: G fails, H and T pass (false positive). F11 = row_limit `by [order_id, product_id]`, `max 2`, caller ben, `/orders/3/items`, json `{product_id 1, quantity 1}` | PG1 1 cell | the mutant implements the 2-per-(order, product) limit exactly | **cause unknown**: RC first |
| d | PNX N12, N13: `accepted.db.equals` values `{sub}` / `{param}` refused F004 only | PNX 2 / 12 | cause 7's node-before-literal order is applied to `cond.value`/`values`, not to outcome `db.equals` values (p, the RC node confirms by reading) | **cause probable**; decided in D0, confirmed by RC |
| e | PT2f class e: P24 (ownership `accepted` 200 vs a path-param bound `accepted` 202), P25 (bound 200 vs ownership 203) missed | class e 2 / 4 | both pairs have a path-param bound on one side (r) | **probably follows from (a)**: no bound case exists to compare; RC confirms |
| f | PG2: G reports C and D on every R14-R17 cell, H only D | 124 of 223 cells | row-limit forms emit an invariant (layer C) | **no change**; reported only |
| g | open from PD1 (round-1 set, post hoc): machine invariant emitted once per transition form (R19/R20 on `h_cancel_typo_state`); P15 class e over-claim on two `row_limit` forms | PD1 only | round-2 7.3, 7.4 | **not in scope** (Q4); PD1 will still show them (p) |
| P | process: manifest-file sha not recorded; runner not hashed before opening; scoring changed after opening; PA/PW/PG3/PG4 not run on the set | | verifier | **fixed by D3 and section 4's order** |

### 1.3 Scope (binding)

The smallest library change that closes (a), (d), and (e) if it follows from (a); (b) and (c) only after RC reports a root
cause and an Amendment rules on it; no change for (f) or (g); a frozen, hashed runner whose scoring cannot change after
opening; a new set H3. Nothing else.

## 2. Decisions (fixed before coding)

**D0 Per residual: change, place, budget.** Budgets are changed lines (`git diff --numstat`, added + deleted, as `ruff
format` lays them out) against `3679ab2`. A fix that exceeds its budget by more than 50% is a finding, reported, **and
stops that fix**: the node reports and does not land the over-budget change (round 2 overran by +58% and landed it;
round 3 does not). No code may be moved out of `forms.py` to meet a number.

| # | decision | file | budget |
|---|---|---|---|
| a | **Cause 8, path and query bounds.** When a `bound`'s `field.path` is `path.<p>` (or `query.<k>`) and `<p>` is a placeholder of the form's endpoint (or `<k>` a query key the endpoint reads), the generator builds `at_bound` and `beyond` by substituting the at-bound and beyond values into the witness path (or query) at that placeholder only, keeping every other placeholder and the body. The values follow round-1 D3's `bound` value rule for the op (for `ge 1`: at 1, beyond 0; for `lt n`: at n-1, beyond n). `at_bound` expects `accepted` (derived as round-2 cause 1 when absent); `beyond` expects `refused` with its `db` (locus as round-2 cause 5). A path value that the type cannot hold (a negative into an unsigned or a non-int placeholder) is F021 at `check`, never a run-time error. The note "the witness request does not carry the field" stays only for fields that are neither in the body, the path nor the query. **D1'' (viii):** "A `bound` may name a path placeholder or a query key; Lattice substitutes the value there." | `forms.py` | F <= 40 |
| d | **Cause 7 extended**: the node-before-literal order (round-2 D4') applies to every value position of a form, including `accepted.db.equals`, `refused.db.equals` and `given` rows: a non-`Lit` there is F017 when its key is a not-expressible node name, else F001; F004 only for Lits. Exactly the code path of cause 7, called at the outcome sites | `forms.py` | F <= 15 |
| e | **No separate change unless RC shows (e) does not follow from (a).** If it follows, class e sees the new path-bound cases with 0 lines in `forms_tier2.py`. If RC shows another cause, the fix is ruled in an Amendment within T2 <= 15 | `forms_tier2.py` | T2 <= 15 (conditional) |
| b | **Conditional on RC and an Amendment.** No decision is made here. RC states the cause, the file and line it lives on, and a proposed fix with a line count; the orchestrator rules (fix, set error, or registered limitation) in an Amendment **before** any code for it | `forms.py` only, unless the Amendment says otherwise | F <= 30 (conditional) |
| c | **Conditional on RC and an Amendment**, as (b) | `forms.py` only, unless the Amendment says otherwise | F <= 30 (conditional) |
| f | none: PG2 stays reported; additionally reported as "layer D agreement" (cells where both fail and both include D) | runner only | 0 |
| g | none (Q4) | | 0 |

**Totals.** `forms.py` <= 115 changed lines (a 40 + d 15 + b 30 + c 30; b and c only if ruled), `forms_tier2.py` <= 15
(conditional), **`impact_report.py` and every other library module frozen**. Tests: `tests/spec/test_forms_r3.py`
(created, unbudgeted); edits to `tests/spec/test_forms.py` or `tests/spec/test_forms_r2.py` <= 10 lines in all,
expectation only, each disclosed by test id, only where cause 8 overturns a pinned note (predicted: 0-2 edits, on the
`forms_r2` path-bound note, if any test pins it).

**D1 Inherited unchanged:** round-2 D1' (i-vii) and D4' (with F021), rules B and C, D2 (round-1 D2, D3, D5, D7, Amendment
1 O1-O4), D3 scoring definitions (kill = flip relative to `ok`, formed rules only; crosstalk attributed to the failing
element's rule), K6 (column-only bound cells reported, outside the PG1t gate; round-2 Amendment 1 Q2). D1'' = D1' plus
sentence (viii) above, plus D4'' = D4' with cause 7 over every value position.

**D2 Never (round 3).** Edit, re-label or gate on the round-1 set or H2; open H3 before `freeze3.sha256`,
`runner3.sha256` and `shapes3.manifest.sha256` are all committed and the manifest file's sha recorded in an Amendment;
change any file named in `runner3.sha256` after H3 is opened, except the reader file under D3; write code for (b) or (c)
before the RC report and its Amendment; call a model; use an API key; edit a frozen file of section 7; let `generate` or
`contradictions` raise on a form `check` accepts.

**D3 Runner discipline (fixes process errors P).** The round-3 runner is two files: `dogfood/forms_probes3.py`
(**scorer**: every metric, every count, every pass rule, every re-derivation; copied from `forms_probes2.py` at
`9e3ec0ad` and extended) and `dogfood/forms_reader3.py` (**reader**: functions that turn set files into the scorer's
in-memory records, nothing else; it imports nothing from the scorer). Both are hashed into `runner3.sha256` with the
mutant runner `dogfood/forms_mutants3.py`, committed **before H3 is opened**. After opening: (1) a **scorer** change of any
kind makes every row whose number it can change **post hoc** (reported beside, never a pass) and is listed with its diff;
(2) a **reader** change is allowed only to turn a NOT RUN (a raise or a layout mismatch before any metric) into a run,
must leave every count the scorer derives from round-2 data unchanged (checked by re-running PD2 with the new reader:
identical numbers, else the change is a scorer change), is listed with its diff, and the new reader hash is recorded in
the results; (3) a reader change that drops, merges or re-labels set records is a scorer change. The runner reads H2's
layout as written (that is its development data), so a reader change on H3 is predicted rare (p: 0-1).

## 3. The root-cause node RC (read-only; reported before any code)

**Who.** A node separate from the build node or the build node's first stage; it **writes no library, test or runner
file** and changes nothing in the repository. It may run `forms generate`, `check`, the round-2 runner `--stage frozen`
on **H2** (development data now), and minishop apps from H2's `mutants.json`, all on a scratch copy.

**Output** `docs/results/data/2026-10-18-typed-conditions-round3/rc.json` and a section "RC" in the results doc, written
before the build node's first library edit, with, per residual (b), (c), (d), (e): the observed cell (G, H, T verdicts and
the failing check names, or none), the **generated** cases for the form (`forms generate` output for F15 / F11 / N12-N13 /
P24-P25), the requests and the db states the generated checks produced against the mutant and `ok`, a stated cause in one
sentence naming the file and function, the evidence that rules out the other hypotheses below, a proposed fix with a line
count, and a classification: **library defect / D1 ambiguity / set error / registered limitation**.

**Hypotheses to test, registered so RC cannot only confirm a favourite (p; not decisions).**
- (b) b1: rule C completes row 3 in a state from which the mutant does not cancel (the machine's initial state is not
  `pending`, or row 3 is not ben's in the generated state), so the mutant's sneaky branch is never taken in G's case;
  b2: `refused_other`'s `db unchanged` is asserted on a derived locus that omits `orders.status` (round-2 cause 5 c,
  `db_locus:given_only` with an empty `given`); b3: the generated `other` case is sent as a user for whom the mutant's
  branch is not reached (ann vs another buyer); b4: the case is generated but its db assertion is skipped on a 404.
- (c) c1: seed `order_items` rows of order 3 with product 1 are not counted (only `given` rows count, round-2 cause 4),
  so `at_limit` sends a request the correct app accepts; c2: synthesised counted rows collide on a column the mutant keys
  on (all synthesised rows take the witness `product_id`, round-2 7.4's mechanism); c3: the R15 invariant (layer C) fails
  on a state another scenario reaches (crosstalk attributed to R15); c4: `below` adds max-1-m rows with m miscounted.
- (d) d1: as stated in D0 (outcome `db.equals` typed before node); d2: the not-expressible attempts use a key not in the
  cause-7 list (then the code is right and the set's expectation is the question: Q3).
- (e) e1: follows from (a); e2: class e compares only cases with identical paths and the bound's case path differs.

**RC pass rule (process gate, PRC):** the report exists before the first library edit (file mtimes and the results'
command log), names a cause with evidence for each of (b) and (c), or says "not found" with the hypotheses ruled out.
"Not found" is allowed and leaves the cell as a **predicted miss** registered in an Amendment; no speculative fix lands.

## 4. The new frozen set H3 (commissioning described; this author does not write it)

**Who.** One **new** separate agent: not the round-1 or H2 shapes agent, not any round-1/2/3 builder, not RC, not this
author, not the orchestrator. A Claude Code subagent (round-2 Amendment 1 Q8: no API key), in a fresh scratch tree holding
only the permitted files; the listing of that tree is stored beside the manifest, so the exclusion is enforced.
**Prompt contains only:** D1'' and D6 (not expressible) verbatim, D4'' with the code list, `tests/spec/minishop.py` and
`tests/spec/fixtures/impact/base/` read-only, and the output contract below. **It must not read:** `lattice/spec/forms*.py`,
`dogfood/forms_*`, `tests/spec/test_forms*.py`, `tests/spec/fixtures/forms*/`, any `*typed-conditions*` doc or data
directory, the round-1 set, H2, `rc.json`.
**Writes** `$SCRATCH/hshapes3/`, same files as H2 (`README.md`, `invalid.json`, `not_expressible.json`, `pairs.json`,
`minishop/{REQUIREMENTS.md, forms.json, handwritten.py, handwritten.trace.json, mutants.json, truth.json,
h_verdicts.json}`, `MANIFEST.sha256`), **in H2's layout** (the runner's reader reads it; a layout section of the prompt
gives the JSON shapes as data types only, no examples taken from H2), with these minimums:

| file | minimum |
|---|---|
| `REQUIREMENTS.md` | >= 22 rules, >= 4 per kind, plus the 5 base rules |
| `forms.json` | >= 20 approved forms; each D1'' optional key used in >= 2 forms and omitted in >= 2; **>= 3 bounds on a path placeholder or query key**, on >= 2 endpoints; >= 2 ownership forms with `refused_other` on a **writing** endpoint; >= 2 `row_limit` with a 2-column `by` |
| `mutants.json` | >= 16 app edits, >= 3 per kind; >= 2 that clamp or ignore a path/query bound; >= 2 that write and then answer a refusal status |
| `truth.json`, `h_verdicts.json` | all cells (ok + 4 BROKEN + 6 SEC_BROKEN + mutants: >= 27 apps); a hand-written check failing on `ok` against truth is fixed or excluded before the manifest |
| `pairs.json` | >= 26: >= 10 contradictory, >= 10 compatible near-misses, >= 2 per class a-d, **>= 6 class e** (>= 2 form vs hand-written, >= 2 with a path/query bound on one side) |
| `invalid.json` | >= 20, >= 12 distinct codes, F021 included |
| `not_expressible.json` | >= 15 rules, >= 7 categories, >= 10 attempted forms, **>= 3 attempts placing a non-literal in an outcome `db` value** |

The quotas name the residual shapes (risk 1): this is disclosed, and they force the shapes to appear without telling the
author how the library handles them.

**Order (binding).** (1) Build done; RC report and its Amendment exist; `sha256sum lattice/spec/forms.py
lattice/spec/forms_tier2.py lattice/spec/impact_report.py > freeze3.sha256`; `sha256sum dogfood/forms_probes3.py
dogfood/forms_reader3.py dogfood/forms_mutants3.py > runner3.sha256`; both copied to the data directory. (2) The agent
writes H3. (3) The orchestrator copies `MANIFEST.sha256` to `data/.../shapes3.manifest.sha256`, commits it with
`freeze3.sha256` and `runner3.sha256`, and **records `sha256sum shapes3.manifest.sha256` and the commit id in an
Amendment** before any gated probe. (4) The runner checks all three files first (any mismatch aborts) and runs the
frozen stage **once**, every row of 5.2 included (PA, PW, PG3, PG4 too). (5) A reader-only mismatch: NOT RUN, D3 (2),
rerun, disclosed. (6) After the scored run, `sha256sum -c` on all three again; the result is in the results doc.

## 5. Probes (runner `dogfood/forms_probes3.py`; numeric pass rules, blind)

### 5.1 Development and process probes (before the freeze, and again after)

| id | probe | prediction (p) | pass |
|---|---|---|---|
| PRC | RC report before the first library edit (section 3) | exists; (b) and (c) each a cause with evidence, p = 0.7 each | **exists, before code; every residual has a cause or "not found" with hypotheses ruled out** |
| PD2 | **post hoc**: H2 (manifest `6a6f40c6...` as a file; entries hash-checked) re-scored by `forms_probes3.py` with the round-3 library; every row beside its round-2 number and delta | PG1 4 -> 0-2 cells (a's 2 closed; b, c as ruled); PG1k H-only 2 -> 0; PNX 10/12 -> 12/12; class e 2/4 -> 4/4 | **reported, never gated** |
| PD1 | **post hoc**: round-1 set (182fe629) re-scored | as round 2's PD1 (397/400, precision 17/18) +/- 1 cell; the two R19/R20 cells and P15 remain (g not fixed) | **reported, never gated** |
| PC3 | one dev case per round-3 change on new fixtures `tests/spec/fixtures/forms_r3/` (path-placeholder bound `ge 1`; query-key bound; a nested route `/orders/{}/items/{}` bound on the second placeholder; outcome `db.equals` with `{param}` -> F017 and `[1, 2]` -> F001; b and c cases if ruled) | all | **all** |
| PR1 | `forms generate` bytes on `tests/spec/fixtures/forms/` (round 1) and `tests/spec/fixtures/forms_r2/` vs `3679ab2` | round-1 fixture identical; `forms_r2` identical except new `at_bound`/`beyond` cases of path/query bounds, if it has any (p: 0-2 forms) | **only path/query-bound cases differ; every other byte identical** |
| PR2 | round-2 runner `forms_probes2.py` (frozen, `9e3ec0ad`) `--stage dev` | as round 2 except PB's registered hash rows | **as round 2** |
| PB | byte-compat with no forms file, against `git archive 3679ab2`: `lattice --help`; `python -m lattice.spec` usage and `check/lint/impact --help`; `lint --json`, `check --json` (CD) on the minishop base fixture and `attack_spec`; `impact --tiers 23 --json` on D's 7 fixtures; `appmap --json`; `rationale coverage --json`; frozen hashes of section 7; pytest failing ids | identical after removing `total_s`, `seconds`, `db_clone_s`, `elapsed_s` and the `impact: <t>s` stderr line (registered list) | **all identical; failing ids = the pre-edit set** |
| PH | hash discipline: `freeze3`, `runner3`, `shapes3` checked before and after the scored run; the Amendment carries the manifest-file sha | OK | **all OK; any scorer change after opening listed and its rows post hoc** |

### 5.2 Primary gate: the minishop arm on H3 (Docker-free; run once)

Cells >= 20 formed rules x >= 27 apps = **>= 540**. K6 as round 2.

| id | probe | prediction (p) | pass (gated unless "reported") |
|---|---|---|---|
| PG1 | G vs H per cell | G == H 97-100%; G != H with H == T on 0-3 cells | **0 cells with G != H and H == T** |
| PG1t | G vs T per cell | outside K6 98.5-100%; K6 misses 0-3 | **100% outside K6**; K6 reported (count, apps) |
| PG1o | on `ok` | exact (0-1 extra) | **exact** |
| PG1k | kills by flips, formed rules | G = H's kills + 0-3; H-only 0-1 | **G's kills a superset of H's** |
| PG2 | layer where both fail | same layer set 40-70%; layer-D agreement 90-100% | reported (both numbers) |
| PG3 | `forms generate` and CD 10x, random `PYTHONHASHSEED`, two roots, **on H3** | 10/10 | **10/10 byte-identical** |
| PG4 | generated invariants compile (sqlite, mysql); scenarios validate; 0 L011/L012; no raise, **on H3** | 100% | **100%, 0 raises** |
| PG5 | `invalid.json` and the valid forms through `check` | 0 accepted; 0-1 valid refused | **0 invalid accepted; 0 valid refused** |
| PG5c | exact F-code on `invalid.json` | 85-100% | reported |
| PT2f | `forms contradictions` on `pairs.json`, items counted by the round-2 `items_outside_the_pair` rule (registered now, so blind) | precision 1.0; recall a-d 0.9-1.0; 0 raises | **precision 1.0; recall a-d 1.0; 0 raises** |
| PT2e | class e recall on `pairs.json` (>= 6 class e pairs) | 0.67-1.0 | **>= 0.75, and >= 1 of the path/query-bound pairs found** |
| PT2x | witness strong: runner re-derives both cases from the pair's witness request and calls `impact_tier2.incompatible` itself | 100%; weak (class c) 0-3 | **100% of re-derivable items; weak items counted and reported** |
| PT2w | `lattice spec impact --tiers 23 --json` on a minishop candidate adding one H3 form that contradicts a base form | exact | **`tier2.determined` gains exactly the new items, `tier: 2`** |
| PNX | `not_expressible.json` attempts through `check` | 0 accepted; F001/F017 on 95-100% | **0 accepted; every refusal carries F001 or F017** |
| PA | approval states as round 1/2 on H3's forms (untagged current; hand edit -> `edited`; stale; moved; draft writes proposals only; no TTY / `CLAUDECODE` refuses) | all | **all** |
| PW | snapshots around `show/check/generate/contradictions/coverage` x10 on H3; recording `claude` shim | 0, 0, 0 | **0 changed, 0 new, 0 shim calls** |
| PX | wall time: `generate` on H3 (median of 50); whole arm | 0.5-5 ms; 110-200 s | **<= 1 s; <= 240 s** |

The PX arm gate rises from 180 s to 240 s because H3 has >= 540 cells and may have more than H2 (m: H2 took 132.4 s for 672 cells, about 0.197 s per cell);
a set of up to 800 cells at round 2's rate is ~158 s; 240 s leaves 50% headroom. Stated now, not after.

### 5.3 What counts

**Pass of round 3:** every gated row of 5.1 and 5.2 (PRC, PC3, PR1, PR2, PB, PH; PG1, PG1t, PG1o, PG1k, PG3, PG4, PG5,
PT2f, PT2e, PT2x, PT2w, PNX, PA, PW, PX) on the **single scored run**, mutants 15/15 new (HZ) plus HY 16/16 and HX 16/16. A row
measured only after a scorer change is post hoc and does not pass. A NOT RUN is not a pass.

### 5.4 Registered, NOT RUN here: the Streak arm

Round-1 section 3.3 unchanged (PS0, PS1, PS1t, PS2, PS3, PSx). Recorded `NOT RUN: no Streak checkout, no Docker daemon`,
no number, no substitute. The drafter stays report-only.

**Probe count: 25** (8 in 5.1, 17 in 5.2), plus 6 Streak rows registered NOT RUN. Gated 21; reported 4 (PD1, PD2, PG2,
PG5c).

## 6. Mutants (`dogfood/forms_mutants3.py`, scratch copy; control first; predicted 15/15 new + 32/32 re-applied)

Killed when a gated probe that passes on the control fails, a PC3/PR1 case flips, or a named unit test of
`tests/spec/test_forms_r3.py` fails (disclosed per mutant as rounds 1-2 did). HY1-HY16 and HX1-HX16 are re-applied (an
edit that no longer applies textually is re-expressed and disclosed).

| id | mutant (residual) | predicted catcher |
|---|---|---|
| HZ1 | path/query bound emits no case (round-2 behaviour) (a) | PC3, PD2 (`m_read_clamp` x R9), unit |
| HZ2 | `beyond` uses the bound value itself (off by one) (a) | PC3, unit |
| HZ3 | substitution into the last placeholder, not the named one (nested route) (a) | unit (PC3 nested case) |
| HZ4 | path-bound `at_bound` expects `refused` (a) | PG1o-style unit on `ok`, PD2 |
| HZ5 | path-bound `beyond` asserts no db effect even when a locus is derivable (a) | PD2 (`m_cancel_clamp` x R10), unit |
| HZ6 | query-key bound ignored (path only) (a) | PC3 query case |
| HZ7 | a path value the type cannot hold raises at run time instead of F021 at `check` (a) | PG4 "0 raises", unit |
| HZ8 | outcome `db.equals` typed before node (round-2 order) (d) | PNX on PD2 (N12, N13), PC3 |
| HZ9 | cause 7 applied to `db.equals` but not to `given` rows (d) | unit |
| HZ10 | the (b) fix reverted (defined by the RC Amendment; if (b) is "not found", replaced by: ownership `refused_other` skips the db assertion on a 404) (b) | PD2 (`m_cancel_sneaky` x R19), unit |
| HZ11 | the (c) fix reverted (defined by the RC Amendment; if "not found", replaced by: given rows of the counted table not counted) (c) | PD2 (`m_pair2` x R15), unit |
| HZ12 | class e skips cases that came from a path/query bound (e) | PT2e, PD2 (P24, P25) |
| HZ13 | class e reports two cases on different paths as one request (over-claim) (e) | PT2f precision on PD2/dev pairs, unit |
| HZ14 | `forms generate` adds a key (`path_bound: false`) to every bound case (byte-compat) | PR1, PB |
| HZ15 | runner: a scorer edit after the freeze (one count changed) | PH (`runner3.sha256` mismatch aborts) |

**Mutant count: 15 new** (HZ1-HZ15; HZ10 and HZ11 are fixed in the RC Amendment before code) + HY1-HY16 + HX1-HX16 = 47.

## 7. Files: frozen, changeable, budgets; the byte-compat promise

**May change (build node; no commits, never `git add`):**

| file | sha256 now (m, at 3679ab2) | budget |
|---|---|---|
| `lattice/spec/forms.py` | `dd9b756625760b4cfc9f3e56ed3e6c814da3acb82c4130747836cf2af0dbfa97` (2236 lines) | <= 115 changed (a 40, d 15, b 30 and c 30 only if ruled) |
| `lattice/spec/forms_tier2.py` | `288beb576f42845dee1856d3b461b4a4ea8d7c67d9943a5711c2e862c7b53e6b` | <= 15, only if RC shows (e) needs it |
| `tests/spec/test_forms.py`, `tests/spec/test_forms_r2.py` | `5ec3cc1f...`, `5e7e3b0e...` | <= 10 lines in all, expectation only, disclosed |

**Creates:** `dogfood/forms_probes3.py`, `dogfood/forms_reader3.py`, `dogfood/forms_mutants3.py`,
`tests/spec/test_forms_r3.py`, `tests/spec/fixtures/forms_r3/**`, the round-3 results doc and data directory (with
`rc.json`, `freeze3.sha256`, `runner3.sha256`, `shapes3.manifest.sha256`, `frozen-hashes-at-freeze.txt`, pytest failing
ids before/after); this file only by appended Amendments.

**Stays frozen (sha256 at 3679ab2, m; any change is a finding):**

| file or set | sha256 |
|---|---|
| `lattice/spec/impact_report.py` (round-2 D6 state) | `aa29aede06cce15ea74d32aebb964b71dcbdee5d1f70e02a012ac50028558a41` |
| `lattice/spec/impact_tier2.py` | `d0e95f902b1ba1f6435a8ffe2624912706815222c46964e687bbfedd063dc8a0` |
| `lattice/spec/__main__.py` (after Spike E) | `94542f34058145783670ca8cf95e83e6016bcc8aa03688b9af259d28af141c24` |
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
| `tests/spec/minishop.py` | `90117594e10c61272b8896bfc631623c5d23c4689d8e1853649cf73810baccfd` |
| `dogfood/forms_probes.py` (round-1 runner) | `7449106201f93f6108addcd557f54ef353ccbb53322c05a3b4bda04eba3525c0` |
| `dogfood/forms_mutants.py` | `7b9483ff29f80b91164c594e0312beb50fc0e4b1ba5409cd83360967e2240597` |
| `dogfood/forms_probes2.py` (round-2 runner as scored, run 3) | `9e3ec0adf13f85c019c16d282072c7ad7b5d204009f8f20a37b8a80b92f4ff4c` |
| `dogfood/forms_mutants2.py` | `45889ed95d69b292e6a285a2af533d5a33b1c0bed53f1ad000b7aa61d583a2f1` |
| `lattice/check/**` (27 files) | `ca52b5a0076a04328c97c1cdafdd50af1a557ac671795697f9ec448146ba57bf` |
| `lattice/gocheck/**` (24 files) | `e9515e859e4aebff1020d8fffa721e8859396b9545d4d7b2b768d4980a367c3f` |
| `tests/spec/fixtures/impact/**` (21 files) | `291eab2058ca5c86079b73287d1feff42e97afcd9a53f2358322df7b75473d1b` |
| `tests/spec/fixtures/forms/**` (4 files) | `ee956fc0472296d5eeb58b743c79bcd8014249032a9699d63ec4a6cd6e5cd0be` |
| `tests/spec/fixtures/forms_r2/**` (4 files) | `b3dafdc48c59c2cc424da2f4655050b27b952ffb33b400d3f6b0702e4957a3a6` |
| round-1 set (outside the repo) | MANIFEST `182fe629e10241eded91e307abe8296db0a5877a08941fbdecef81a69d83af13` |
| H2 (outside the repo) | `shapes2.manifest.sha256` file `6a6f40c69ae3eaed62655cf51653c34098e36a405dbf28242feef107b9003a8f` (11 entries) |
| docs and data of rounds 1-2 | `docs/results/2026-10-17-typed-conditions*` and `docs/results/data/2026-10-17-typed-conditions*/**`: `git diff 3679ab2 --` empty |

Set hashes: `git ls-files -z DIR | sort -z | xargs -0 sha256sum | sha256sum` (registered in round 2; reproduced here (m)).

**Byte-compat promise.** (1) With no `*.forms.json` / `*.forms.proposed.json` anywhere, every command, report, lint output,
impact report (D's 7 fixtures), app map, MCP listing and Studio response is byte-identical to `3679ab2` after removing the
registered timing keys (PB); `python -m lattice.spec` usage unchanged. (2) With the round-1 dev forms, `forms generate` and
`forms check` are identical to `3679ab2`; with `forms_r2`, identical except the new `at_bound`/`beyond` cases of path or
query bounds (PR1). (3) No new key in any existing JSON output; a form without a path/query bound generates the same
bytes as round 2 (HZ14). (4) `impact_report.py` is frozen, so impact output with a forms file is identical to round 2 for
every form that is not a path/query bound.

## 8. Risks, and what this cannot show

1. **Quotas and D1'' encode the residuals** (round-2 risk 1, sharper now): H3 is required to contain path bounds,
   write-then-refuse mutants, 2-column limits and outcome non-literals. Round 3 then tests "the library meets D1'' on the
   shapes it failed", not general robustness; a new author will still find a new reading (round 2's H2 found three the
   dev fixtures and PD1 did not).
2. **RC may find no cause** for (b) or (c); then the cell is a registered predicted miss and PG1 is very likely to fail
   on H3 if H3 contains the same shape (its quotas make that likely). Stated now: p(PG1 met) about 0.5.
3. **Conditional budgets** (b, c) are set before their causes are known; a cause outside `forms.py` (in minishop, the
   hand-written H, or a frozen module) is not fixable in round 3 without a ruling (Q2).
4. **Rule C / B heuristics** (round-2 5.2, 5.3) are unchanged; hypotheses b1 and c1-c2 are exactly these heuristics. A fix
   to one can move another cell (PD1, PD2 deltas will show it, post hoc).
5. **Open PD1 defects (g) are not fixed**: a set with three transition forms on one machine and a state-typo mutant
   fails PG1 on H3 the way `h_cancel_typo_state` did on the round-1 set (Q4).
6. **One author** writes rules, checks, mutants and truth (rounds 1-2 risk, unchanged); no second labeller.
7. **minishop is small and sqlite only**; MySQL invariants compiled, not executed; the Streak arm is the only test of scale.
8. **The runner split (D3) is a new mechanism**: a reader function that silently drops records would change counts while
   claiming to be layout-only; the PD2 identity check limits that, it does not remove it.
9. **Whole-gate prediction:** met with probability about 0.4 (p); likeliest misses PG1/PG1t (a new derivation case or
   residual b/c), then PT2e.
10. **Cannot show:** form-to-code mapping, drafter quality, the Streak arm, MySQL execution.

## 9. Blind vs post hoc, and commands

Fixed now: D0-D3, D1'', D4'', the RC hypotheses, the gates and numbers, K6, the `items_outside_the_pair` rule (now
registered, so blind on H3), the mutant list (HZ10, HZ11 completed by the RC Amendment before code). H3 is scored once.
Any fix after that is a separate post hoc commit, rerun with deltas labelled post hoc. PD1 and PD2 are post hoc in every
run.

```sh
export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
SCRATCH=<the build node's scratchpad>/forms3; cd <round-3 checkout>
uv run python -m pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck -q -rs \
    --continue-on-collection-errors > "$SCRATCH/pre.txt"                                   # before any edit
# RC node (read-only), on H2 = development data:
uv run python dogfood/forms_probes2.py --stage frozen --shapes <hshapes2> \
    --manifest docs/results/data/2026-10-17-typed-conditions-round2/shapes2.manifest.sha256 \
    --freeze docs/results/data/2026-10-17-typed-conditions-round2/freeze2.sha256 --scratch "$SCRATCH/rc" --out "$SCRATCH/rc-data"
# build node, after the RC Amendment:
uv run python dogfood/forms_probes3.py --stage dev --scratch "$SCRATCH/d" --out "$SCRATCH/d-data"        # PC3 PR1 PB PW PA PG3 PG4
uv run python dogfood/forms_probes3.py --stage h2 --shapes <hshapes2> \
    --manifest docs/results/data/2026-10-17-typed-conditions-round2/shapes2.manifest.sha256 --out "$SCRATCH/pd2"   # PD2, post hoc
uv run python dogfood/forms_probes3.py --stage round1 --shapes <round-1 hshapes> \
    --manifest docs/results/data/2026-10-17-typed-conditions/shapes.manifest.sha256 --out "$SCRATCH/pd1"           # PD1, post hoc
uv run python dogfood/forms_probes2.py --stage dev --scratch "$SCRATCH/r2" --out "$SCRATCH/r2-data"     # PR2
uv run python dogfood/forms_mutants3.py $(seq -f HZ%g 1 15) $(seq -f HY%g 1 16) $(seq -f HX%g 1 16) --scratch "$SCRATCH/mut"
sha256sum lattice/spec/forms.py lattice/spec/forms_tier2.py lattice/spec/impact_report.py > "$SCRATCH/freeze3.sha256"
sha256sum dogfood/forms_probes3.py dogfood/forms_reader3.py dogfood/forms_mutants3.py > "$SCRATCH/runner3.sha256"
# orchestrator: commission H3 (section 4), commit freeze3 + runner3 + shapes3, Amendment with the manifest-file sha, then once:
uv run python dogfood/forms_probes3.py --stage frozen --shapes <hshapes3> \
    --manifest docs/results/data/2026-10-18-typed-conditions-round3/shapes3.manifest.sha256 \
    --freeze docs/results/data/2026-10-18-typed-conditions-round3/freeze3.sha256 \
    --runner docs/results/data/2026-10-18-typed-conditions-round3/runner3.sha256 --scratch "$SCRATCH/f" --out "$SCRATCH/f-data"
uv run ruff check <changed .py>; uv run ruff format --check <changed and created .py>
```

## 10. Questions for the orchestrator

- **Q1** The relayed request reads "merge and then tun round 3". This document takes "tun" as "run", and the merge as the
  already-made `3679ab2`. Is a further merge meant (for instance of a later `next-phases` into a round-3 branch, or of
  Spike I's round 2)? Where does the round-3 build node work: a new branch from `3679ab2` in a new worktree (this
  document's assumption), or `next-phases` directly?
- **Q2** RC and rulings: accept that (b) and (c) get no code until RC reports and an Amendment rules, with "not found"
  registered as a predicted miss? If a cause lies outside `forms.py` (rules B/C need `minishop.py`, or H2's hand-written
  H/T was wrong: a set error), may the Amendment move the budget, or is that out of round 3?
- **Q3** If RC finds (d) is hypothesis d2 (N12/N13 use a node name outside the cause-7 list), is the right answer
  extending the list (library) or ruling the set's expectation wrong (H2 is development data, so no gate changes either way)?
- **Q4** Residual (g) (the per-form machine invariant, P15's class e over-claim): leave out of scope (this document), or
  add them with F <= 20 and T2 <= 10? Out of scope means H3 can fail on them.
- **Q5** Confirm the over-budget stop rule (a fix > 150% of budget is reported and not landed), which is stricter than
  round 2.
- **Q6** Confirm the runner split and D3's rule that any post-opening scorer change makes the affected rows post hoc,
  and the PX arm gate of 240 s (raised from 180 s, before H3 exists).
- **Q7** Confirm PT2e as a new gate (class e recall >= 0.75 on >= 6 pairs) rather than reported only.
- **Q8** Should the orchestrator append an Amendment 2 to the round-2 prereg recording H2's manifest-file sha
  (`6a6f40c6...`) and the pre-opening runner hash (`c52d27f5...` at `f49fa97`), so the round-2 record is complete where
  it lives? This document records both but does not edit that file.
- **Q9** H3 author: a Claude Code subagent with no API key, as H2; confirm its prompt may include D1'' and the
  quota table (risk 1).

## Amendment 1 (orchestrator rulings, 2026-10-18; defaults, overturnable)

Q1: the merge is 3679ab2; no other merge is meant ("tun" = run). Round 3 builds in `/home/user/wt/np-typedcond` after `next-phases` is merged into it. Q2: accepted; if a cause lies outside `forms.py` or in the set, an Amendment may move a budget by at most 50%. Q3: extend the cause-7 list only if the node name is in D1's closed set; otherwise H2's expectation is ruled wrong. Q4: out of scope. Q5: confirmed. Q6: confirmed, including PX at 240 s. Q7: confirmed. Q8: yes, Amendment 2 is appended to the round-2 prereg. Q9: confirmed.

## Amendment 2 (root-cause rulings and the H3 set, 2026-10-18)

Root-cause node (read-only; report in the session record): (b) m_cancel_sneaky x R19: `ownership()` silently drops `refused_other: {db: unchanged}` when the path placeholder is not a column and the form states no `given` row (set-error/D1 gap + silent drop). RULING: D3 `unchanged` is asserted only on rows the form's own `given` states; the generator emits a `db_unchecked:<case>` note instead of dropping silently (about 4 lines in forms.py); the 30-line owner-row synthesis is NOT built; (b) is registered as a predicted K7 cell (reported, outside the PG1t gate), and a form addressing a seeded row with no `given` is a set property. (c) m_pair2 x R15: library defect: `row_limit()` rule C gives every synthesised row the same value on non-`by` columns, so a sibling row_limit form's invariant is violated on other forms' `given` states. RULING: synthesised counted rows are pairwise distinct on any column that another row_limit form of the set counts by (about 15-20 lines in forms.py). (d) N12/N13: `outcome()` hard-codes F004 for any non-literal `db.equals` value; the node names are already in the cause-7 list. RULING: cause 7's order covers every non-`Lit` position including `Outcome.db.equals` values; a 1-line change to F017/F001; H2's expectation stands. (The first commit of this amendment, f17ee04, lost its backticked identifiers to a shell expansion; this text replaces that paragraph and the rulings are unchanged.)
H3 set (separate author): MANIFEST.sha256 sha256 = 1d0b8256344a8ac67345de519abf62c76ddb4846c755ce2ef10194fafbec3d98 (11 entries; 38 rules, 22 forms, 33 apps, 726 cells, 28 pairs, 29 invalid, 16 not-expressible). DISCLOSED DEVIATION: the author read the language-definition sections (D1-D6) of the round-1 and round-2 typed-conditions pre-registrations, which section 4 forbids, because its brief lacked D1''/D3/D4'/D6 verbatim; it read no results doc, data directory, earlier set, forms code or test. RULING: accepted and labelled in the results; no row is dropped. The author also designed its own JSON layout (README.md documents data types only); the build node may read ONLY hshapes3/README.md to write the reader, and any reader-only change after the set is opened is post hoc for the rows it touches.

## Amendment (Streak arm, orchestrator, 2026-10-18, before any Streak run)
The Streak arm (PS0-PSx) was NOT RUN because no `streak/` shape set and no Streak runner stage existed. A separate author has
now written the Streak set (23 formed rules, 43 forms, 29 Go mutations, 24 form pairs including the 50-vs-60 habit-limit pair P9,
27 invalid forms, a classification of all 146 in-scope rules; manifest sha256 `f8727792...c1655`, 58 files; archived in
`docs/results/data/2026-10-18-typed-conditions-streak-arm/streak-set.tgz`, hash in `streak-set.freeze.sha256`). A new runner file
`dogfood/forms_probes_streak.py` (not a frozen file) is written by a build node from the set's README and the PS rows,
frozen before any Streak run, and run once by a separate node. Rulings: forms are the author's (the generator must produce checks
that agree with the truth cells; disagreements are findings, not edits); the set is development-blind for the library (the library
was not tuned on it) but not for the builder of the runner; PS rows are report-only as registered ("not part of this round's gate"),
the gate verdict of Spike H is unchanged. The author's one truth correction made after a run and before the manifest (R56 on
`m_restore_limit_skipped`) is disclosed. Transition forms for Streak are not expressible (a form-language limit, a finding).
