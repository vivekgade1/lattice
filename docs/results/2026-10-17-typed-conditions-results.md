# Typed rule conditions (Spike H): results of the build node

2026-10-17. Pre-registration: `2026-10-17-typed-conditions-prereg.md` (with Amendment 1), not edited. Data:
`docs/results/data/2026-10-17-typed-conditions/` (`dev.json`, `frozen.json`, `mutants.json`, `freeze.sha256`,
`frozen-hashes-at-start.txt`, `pytest-failing-ids-before.txt`). Worktree `/home/user/wt/np-typedcond`, from
`next-phases` 5125da1 (code identical to 6a318e5). Nothing was committed or staged. No model was called and no API
key was used. No sub-agent was spawned.

## 0. Verdict in one paragraph

**The primary gate (the minishop arm, PG1, PG1t, PG1o, PG1k, PG2, PG5, PT2f, PT2x, PNX, and the frozen part of PT2w)
was NOT RUN: the separate agent's frozen rule-shape set is not present in this worktree, there is no
`shapes.manifest.sha256`, and the builder must not write it.** Scope for this node: build everything, run what does
not need the set, record the rest as NOT RUN. Built and run: the type checker, generator, states, approval guard,
Tier 2 procedures, CLI, the 16 mutants (**16/16 killed**, by named unit tests, disclosed) and the dev-stage probes
PB, PW, PA, PG3, PG4, PX (all pass; two predictions missed, section 3). The Streak arm is NOT RUN (no checkout, no
Docker). **This is not a pass of the spike**: the pass rule of the prereg (section 6) needs every gated row.

## 1. Files

Created: `lattice/spec/forms.py` (schema, states, type checker F001-F019, generator, coverage, approval, draft, CLI),
`lattice/spec/forms_tier2.py` (D5 classes a-e and d'), `tests/spec/test_forms.py` (71 tests, 17 of them the catchers
of the mutants), `tests/spec/fixtures/forms/minishop/` (the builder's own **dev** forms: 7 forms over
7 rules of 4 kinds, plus `not_expressible` for R2 and R3), `dogfood/forms_probes.py`, `dogfood/forms_mutants.py`,
this document and its data directory.

Changed (additive, one place each): `lattice/spec/__main__.py` +1 line (`"forms": "lattice.spec.forms"` in
`AUTHORING`, after `appmap`); `lattice/spec/impact_tier2.py` +3 lines and one keyword (`forms_items=None`; the branch is taken only
when a caller passes it). Every frozen file of section 7 keeps its hash (checked, see PB). Sizes: forms.py 1989
lines, forms_tier2.py 625 (as `ruff format` lays them out).

ruff: `ruff check` clean and `ruff format --check` clean on every created and edited `.py` file. Before/after pytest
(`tests/spec tests/mcp tests/studio tests/importer tests/gocheck`): before any edit 7 failed, 98 errors (105 ids,
`pytest-failing-ids-before.txt`; 2627 passed). After the build, **the first full run showed three new failing ids
(10 failed, 2695 passed): a missed gate of PB ("only the pre-existing failure ids")**. (1) and (2)
`tests/spec/test_appmap.py::test_the_cli_is_one_authoring_entry_and_help_is_unchanged` and
`tests/spec/test_impact_report.py::test_lattice_spec_impact_is_one_authoring_entry`: both pin the *last four*
`AUTHORING` keys (`implementation, rationale, glossary, impact`), and I had appended `forms` last. My error, not the
tests'; fixed by placing the `forms` line after `appmap` (still one line, still the only edit), which leaves the tail
as pinned; the two tests and 210 neighbours (`test_appmap`, `test_impact_report`, `test_forms`, `test_rationale`,
`test_authoring`) pass. The full run was **not repeated** after this one-line move (13.7 min); the three affected
files were re-run. (3) `tests/studio/test_projects.py::test_prefix_routing_and_the_bare_default` fails
intermittently on the unmodified code too (the two projects' commit hashes differ when their commits fall in
different seconds): 1 of 3 runs failed with my tracked changes stashed, 3 of 3 `test_projects.py` files passed with
them; not caused by this build. The ids of the first post-build run are in the scratch pad, not in the data directory.

## 2. Frozen file hashes (before scoring), `freeze.sha256`

```
849d33b26c2c0ba9f0d88584ffd15a721e4a5d85540bf1e24991d1cb35837647  lattice/spec/forms.py
6f9c2482b65a3b1c5c92a82398c3b65be8ea3b0b20020390acfced66bf1646e2  lattice/spec/forms_tier2.py
```

Taken before any probe run of the table below, and verified unchanged after the probes and the mutants
(`sha256sum -c`: both OK). The code was not touched after the freeze. (One implementation change was made before the
freeze, after the first mutant run: see HX3 in section 4.)

## 3. Probes: predicted vs observed

Labels as the prereg: (p) a prediction. "Gate" is the prereg's pass rule.

| id | prediction | observed | gate |
|---|---|---|---|
| **PG1, PG1t, PG1o, PG1k, PG2** | (frozen set) | **NOT RUN: the frozen set is absent** | not met |
| **PG5** | (frozen `invalid.json`) | **NOT RUN: the frozen set is absent**. The checker refuses every invalid form I built in the unit tests, each with its code (F001-F019, 19 codes, one test per code) | not met |
| **PT2f, PT2x** | (frozen `pairs.json`) | **NOT RUN: the frozen set is absent.** Ungated, my own planted pairs (`dev.json` `DEV-PT2f`): 7 contradictory pairs of classes a-d, 7 compatible near-misses (`ge 1`/`le 1`, `gt 3`/`lt 4` on FLOAT, equal limits, limits with different `where`, equal ownership, equal transitions): 7/7 found, 0 false positives. Not a score: I wrote both the pairs and the procedures | not met |
| **PNX** | (frozen `not_expressible.json`) | **NOT RUN: the frozen set is absent.** Unit tests: `now`, `sum`, `or` keys and nodes are refused with F017 | not met |
| **PT2w** | exact on a minishop candidate; identical on D's 7 fixtures | the "identical on D's 7 fixtures" half passed in PB (7/7 impact fixtures, no forms file). The "gains exactly the form items" half: at `compute()` level only (`DEV-PT2w`: PASS, 1 item); **through the `impact` CLI it is NOT RUN**: `impact_report.py` is frozen and does not pass `forms_items`, so a report built by the CLI never reads a forms file. Finding, see section 5 | partly |
| **PB** | all identical; `python -m lattice.spec` usage differs by exactly one line | **18/18 commands identical after removing wall-clock fields; 16/18 identical as raw bytes.** The two that differ as raw bytes are `check --json` (CD) on the base fixture and on `attack_spec`: only timing keys (`total_s`, `seconds`, `db_clone_s`...) differ. The prereg's literal "identical bytes" for `check --json` was unachievable (the report carries wall times); disclosed. **Missed prediction: usage differs by 0 lines, not 1**: `python -m lattice.spec`'s usage never listed `AUTHORING`, so adding `forms` changes nothing visible. 12 frozen files, `lattice/check/**` and `tests/spec/fixtures/impact/**`: hashes match. `lattice/gocheck/**`: my `find -type f \| sort \| sha256sum` method gives `e9515e85...`, not the prereg's `b7a4dacf...`; **`git diff 6a318e5 -- lattice/gocheck` is empty**, so I read it as a method difference (unresolved: the prereg's exact method for `gocheck` is not reproducible from its text). `git diff 6a318e5 --` on `lattice/mcp`, `lattice/studio`, `lattice/check`, `lattice/gocheck`, the impact fixtures: empty. MCP `tools/list` and the Studio route table were **not queried live**; their sources are unchanged | pass, with the two disclosures |
| **PW** | 0 changed, 0 new, 0 shim calls | 0 changed, 0 new, 0 shim calls; `show`, `check`, `generate`, `contradictions`, `coverage` x10 each (all exit 0) and a check run with forms (22 generated elements); snapshots (sha256, mode, mtime_ns, hidden files) of the project directory and of the app directory; a recording `claude` shim first on `PATH` | pass |
| **PA** | as stated | all as stated: a subprocess `approve` refuses with no terminal (`approve_needs_terminal`) and with `CLAUDECODE=1` and writes nothing; a proposal generates nothing and has no door into a check run; a hand-edited approved form is `edited` and generates nothing; a reworded rule makes the form `stale`; the same text moved to another rule id is `moved` (to R1); `draft` (a recording fake generator, **not** the `claude` CLI) writes only the proposals file, forces `status: proposed` over a forged `approved`, and refuses the authoritative file | pass |
| **PG3** | 10/10 byte-identical | 10/10: `forms generate` (1 distinct output) and the CD reports on `ok` and `total_drift` (1 distinct each), 10 random `PYTHONHASHSEED`s, two scratch roots, in fresh processes; no absolute path or time in the generated output | pass |
| **PG4** | 100% | 100%: 6 generated invariants compile for sqlite and mysql; 16 generated scenarios pass `validate`; 0 L011/L012 among them (F020 never fired) | pass |
| **PX** | `generate` 0.05-0.3 s; arm 15-60 s | `generate` on the dev forms: **median 0.43 ms** (50 runs). Gate (<= 1 s) met, **but the predicted range was missed by more than two orders of magnitude** (the prediction was too pessimistic, `generate` is a few dict builds). The whole minishop arm: not run (needs the set); my 11-app dev run (CD, G + H in one run) took 9.7 s, 0.9 s per app | pass (gate), prediction missed |

### Ungated dev data: what the dev forms' generated checks (G) find on the 11 apps (`DEV-GH`)

Dev forms, 7 forms. These are the builder's own forms and no truth table exists for them; read as a smoke test of the
generator, never as PG1.

| app | G finds (beyond what G finds on ok) | H (hand-written) finds |
|---|---|---|
| ok | `form:F7` (limit of 5 lines per order) fails: **the app has no such limit** (the dev form states a rule the app does not implement; G is right) | none |
| partial_write | none | none |
| wrong_answer | `F6:other_get_orders` | none |
| total_drift | none (R3 `money` is not expressible) | `order_total` |
| wrong_quantity | none (R2 `effect_value` is not expressible) | `ann adds a line to a new order` |
| sec:authz | `F6:other_get_orders` | none |
| sec:auth | `F6:anon_get_orders`, `F6:anon_post_orders_cancel` | none |
| sec:bounds | `F3`, `F4` invariants and `F3:beyond`, `F4:beyond` | none |
| sec:mass | none | none |
| sec:state | `F1:refused_{cancelled,paid,shipped}` | `ann cancels her pending order` |
| sec:leak | `F3:beyond`, `F4:beyond` (the verbose 500 is not the 422 the form expects) | none |

G kills 6 of the 10 broken variants and H 3; **G does not kill every variant H kills**: `total_drift` and
`wrong_quantity` are killed only by H because their rules are `money` and `effect_value`, which D6 makes not
expressible. If PG1k is scored over *all* of H's findings that gate is missed by construction; if it is scored over
(formed rule, app) cells as section 3.2 defines a cell, it is not (those two rules have no form). Worth fixing in
the scorer before the set is scored, not after.

## 4. Mutants (`dogfood/forms_mutants.py`, scratch copy of the tree; the control passes first)

**16/16 killed** (`mutants.json`). Each is killed by a named test of `tests/spec/test_forms.py`; the prereg names
probes as catchers (PG1, PT2f, PA, PB, PW ...), most of which need the frozen set, so **the named unit tests stand
for them and this is a disclosed substitution**.

| id | killed by (test) | stands for |
|---|---|---|
| HX1 | `test_hx1_a_refused_step_expects_the_database_unchanged` | PG1 |
| HX2 | `test_hx2_ge_is_generated_at_the_bound_not_past_it`, `test_bound_values_int_float_in_len` | PG1 |
| HX3 | `test_class_a_int_tightening_gt3_lt4_hx3` | PT2f class a |
| HX4 | `test_class_b_different_where_is_a_different_count_hx4` | PT2f precision |
| HX5 | `test_hx5_...anon_case_and_catches_the_auth_hole` (runs the generated check on the sec `auth` app) | PG1k on sec:auth |
| HX6 | `test_hx6_the_transition_generator_refuses_every_other_state` | PG1k |
| HX7 | `test_a_proposal_generates_nothing_hx7` | PA |
| HX8 | `test_an_edited_approved_form_generates_nothing_hx8` | PA |
| HX9 | `test_check_with_forms_without_a_forms_file_is_the_plain_check` | PB |
| HX10 | `test_generate_is_identical_under_different_hash_seeds_hx10` (6 seeds) | PG3 |
| HX11 | `test_hx11_a_now_or_sum_node_is_refused` | PNX, PG5 |
| HX12 | `test_class_a_witness_self_check_suppresses_a_false_pair_hx12` (a planted false pair) | PT2x |
| HX13 | `test_hx13_the_below_case_has_one_row_fewer_than_the_limit` | PG1o |
| HX14 | `test_hx14_the_count_invariant_is_at_most_not_below` | PG1o |
| HX15 | `test_hx15_a_one_of_value_outside_the_column_is_refused` | PG5 |
| HX16 | `test_generate_writes_no_cache_file_hx16` | PW |

**A missed prediction, found and fixed before the freeze.** The first mutant run was **15/16**: HX3 survived. The
cause was in my implementation, not the test: the interval code had a second, redundant path (an integer-gap count)
that decided `gt 3`/`lt 4` on INT even when the tightening was removed, so the mutant was behaviourally equivalent.
I removed the redundancy (the gap check now needs closed integer endpoints, i.e. depends on the tightening) and
re-ran: 16/16. The change was made before `freeze.sha256` and before the dev probes were run; none of the tests was
loosened. Disclosed because "mutants 16/16" was predicted and was first missed.

## 5. Deviations from the pre-registration, and decisions it left open

1. **The frozen set is absent**, so the gated rows are NOT RUN (per the node's scope). `dogfood/forms_probes.py
   --stage frozen` is built and refuses to run without `--shapes` and a manifest that matches `--manifest`; with the
   set absent it writes the ten NOT RUN rows (`frozen.json`). Its scoring code for PG1/PG1t/PG1o/PG1k/PG5/PNX reads
   the layouts of section 3.1 **as I guessed them** (the docstring lists them); it was never run against a real set
   and is **UNVALIDATED**. PG2, PT2f, PT2x need layouts I could not guess and stay NOT RUN in that runner.
2. **Tier 2 reaches the impact report only at `compute()` level.** `impact_report.py` is frozen (section 7) and it
   does not pass the new `forms_items` keyword, and `Side` carries no directory, so `lattice spec impact` never
   reads a forms file. D5's "`decidability[]` entries for formed rules read `determined` with reason `form`" is **not
   implemented** (it would need `impact_report.py`). The Tier 2 widening is real and tested, but needs one small edit
   in a frozen file to be reachable. A missed registration, not a miss of a prediction.
3. **Diagnostic spans**: `Diagnostic.span` is a line/column pair; the JSON pointer leads the message instead.
4. **D1 additions needed to make the forms concrete** (all optional, none changes what the grammar can say):
   `accepted` (Outcome of the accepted case; default status 200) on `bound`, `row_limit` and per endpoint on
   `ownership`; `bodies` (a witness body per endpoint) on `ownership`; `stores` (D5 names it) on a param `bound`; a
   top-level `not_expressible` map in the forms file (so `coverage` can report D6 categories, the classification the
   prereg leaves to "the drafter or the owner"). A `bound` needs `refused` only when it has an endpoint.
5. **How `Outcome.db` becomes a check** is not specified by D1. Mine: the *locus* is the field's (or `stores`')
   table, narrowed by the columns the witness request carries; `unchanged` needs a non-empty locus (the seed's rows
   are unknown to the form) and asserts the given rows it matches (their values for one row, else a count);
   `equals` and `count` assert as written; a refused step defaults to `db: unchanged`. For a column bound with an
   endpoint the value goes where the witness carries a key named like the column. For `row_limit`, the first row of
   `given[table]` is the counted-row template. All stated in the module docstring.
6. **Amendment 1**: O3 followed (no `--forms` flag on `spec check`; the only door is `forms.generated_for` /
   `check_with_forms`, used by the `forms` tooling and the probes), O4 followed (no L013; a form/hand-written
   disagreement is class e of `forms contradictions`).
7. **`forms draft`** goes through `lattice.llm.cli_client.ClaudeCliClient` (the `claude` CLI, 2.1.302 exists) and is
   built but **was not called**: scoring it ("type-checks", "kind matches the agent's form") needs the frozen forms,
   and the node's rules forbid a model call that scores nothing. NOT RUN. The tests and PA use a recording fake.
8. **Mutants are killed by unit tests, not by the probes** the prereg names (section 4 above).
9. **The dev fixture contains a form the app does not satisfy** (F7, at most 5 lines per order): deliberate, so that
   a generated check can be seen to find a rule the app lacks; it makes `G` fail on `ok` in the dev table. It is the
   builder's fixture, not the frozen set, and does not bear on PG1o.
10. **Baseline pytest** has 7 failures and 98 errors before any edit (the `bench/template_app` NameError, 12
    collection errors, and importer cases): recorded in `pytest-failing-ids-before.txt`.

## 6. Not run here (reasons)

- The whole gated minishop arm (PG1, PG1t, PG1o, PG1k, PG2, PG5, PT2f, PT2x, PNX): the frozen set is not in the
  worktree.
- PT2w through the `impact` CLI: `impact_report.py` is frozen (item 2).
- The Streak arm PS0, PS1, PS1t, PS2, PS3, PSx: `NOT RUN: no Streak checkout, no Docker daemon`, no number, no
  substitute. Passing the minishop arm would not stand in for it.
- The model drafter (`forms draft` through `claude`): not called (item 7).
- MCP `tools/list` and the Studio route table, live: only the unchanged sources were checked.
- MySQL execution of the generated invariants: compiled to MySQL SQL (PG4), run only on sqlite.
- E's before/after runs: Spike E has not landed (O1, accepted by Amendment 1).

## 7. Commands (rerun)

```sh
export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
uv run python dogfood/forms_probes.py --stage dev --scratch $SCRATCH/p --out $SCRATCH/p-data
uv run python dogfood/forms_probes.py --stage frozen --scratch $SCRATCH/f --out $SCRATCH/f-data   # NOT RUN rows
uv run python dogfood/forms_mutants.py $(seq -f HX%g 1 16) --scratch $SCRATCH/mut --out $SCRATCH/mutants.json
uv run python -m pytest tests/spec/test_forms.py -q
uv run ruff check <changed .py>; uv run ruff format --check <created .py>
```
