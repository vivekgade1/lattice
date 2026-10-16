# Change impact engine and report (Spike D): pre-registration

## 1. Status, and the exact gap

2026-10-16, branch `np-impact` (from `next-phases` bb3ff0c: Spikes A, B and C merged). **Written BEFORE any code.**
Recon: code reading; Streak a0679e9 (**S**) and 1d00ede (**S4**) archived into `$SCRATCH`, `bin/` built; `lattice import
inventory` on S; three ACD checks on MySQL 8.4 (S on its own spec, S4 on its own and on S's); a lexical function
resolver over the extract; a flow/scenario prefix count. No model. **(m)** = measured, **(r)** = recon script (blind
expectation), all else a **prediction (p)**. Results: `2026-10-16-change-impact-results.md`, data in
`docs/results/data/2026-10-16-change-impact/`. **Owner requirement (binding):** when the spec changes, pinpoint the code
it touches and the workflows that fail, then let spike E implement it; **correction:** "fails" must be deterministic. So
every item carries a tier: **T1 observed** (the candidate's checks on today's app; a failing check is a fact), **T2
determined** (follows from the spec plus observed passes, without a candidate run), **T3 static** (sound but incomplete
code locations, unknowns listed), **T4 observed after the change** (E's; the slot is kept).
**The code contradicts five premises (m).**
1. **`incremental.impact` is not "every failing check".** It runs CD (X with `race`), and `label_impact` drops every A
   and B finding ("no target"). Machines are checked only by layer A (gocheck GO808, static) and X (probabilistic),
   never by C or D (`check.py` docstring, `spec_policy`, `gocheck/__init__.py:131`). So T1 runs **ACD**, keeps every
   finding, and still reuses `label_impact` for labels.
2. **Streak's spec has 0 programs and 0 contracts** (156 scenarios, 15 invariants, 1 machine). The spec runtime cannot
   execute a single Streak step. T2's "replay on the spec runtime" is not applicable on Streak; it is gated on the
   `minishop` fixture (`tests/spec/minishop.py`, which has programs).
3. **Studio's change pipeline calls a model** (`edit_markdown`, `draft_since`), so the engine cannot rerun it. It reads
   the drafted record from `<state dir>/changes/cN.json`: `private.{base{markdown,module,trace}, new_md, new_module,
   new_trace}` (`state.py`, `server.py:1405-1443`). HTTP never serves `private`, so under `HttpBackend` the MCP tool
   reads that file locally.
4. **Extract `calls` carry no file:line.** They are `importpath:Func` or `importpath:Recv.Method`: 87 distinct over 26
   routes. Raise sites carry the route's call line, not the callee's (Spike A D6), and response shapes are not
   extracted. A lexical declaration match resolves **111/113** handler and closure functions uniquely (r). The other two
   are function literals (`NewRouter.func4`, `setTestClock.func1`).
5. **"Which workflows fail" before implementation is mostly undecidable.** Spike B flows record today's observed
   answers, so they pass today by construction. Today they can fail only under a changed invariant (C). **0** of the 59
   verified flows share even a first request with any of the 23 given-less scenarios (r): flows use synthesised values.
   So on Streak, T2 flow claims are predicted **0**; workflow failure is T1 (rare) or T3 `may_fail`, settled by E's T4.

**Baselines (m).** S, ACD, own spec: pass, 0 findings, 29.6 s (D 23.3 s, 157 database clones). S4: 36 D findings on its
own spec; 35 on S's spec. Each run takes about 296 s: 10 `Await` scenarios run to their timeout. Only S4's own-spec run
has `access__logout__as_anon / step 1`; S's spec fixes it. `git diff 1d00ede a0679e9 -- '*.go'` touches 10 files: 5
added, and 5 modified non-test ones (`cmd/server/main.go`, `internal/account/routes.go`, `internal/db/models.go`,
`internal/nudges/nudger.go`, `internal/push/push.go`). S's committed `studio.toml` points `go_src` at the owner's
checkout (Spike C premise 6); probes rewrite it in the clone. The owner's Streak checkout already shows ` M studio.toml`
(mtime Oct 11; not this work). "Unchanged" means unchanged against that state.
**Rulings requested.** **R1** CLI home `lattice spec impact`: one `AUTHORING` entry, no change to `lattice --help`.
**R2** T1 = ACD, with A findings marked `observation: static_checker`. X only with `--race`, never gated. **R3** MCP
`change_impact` exists only with a `studio.toml [impact]` table. `observe: true` is allowed under `--readonly` (a
throwaway database, nothing persistent). **R4** No Studio pipeline edit, so `checks.impact` stays byte-identical by
construction. **R5** The held-out set is the separate-agent change set. A second app is infeasible: the job-scheduler Go
harness apps need round-2 harness discovery, which is unmerged (handoff item 3). H-nat is report-only. **R6** The
function resolver is lexical, in Python, over the extract. `goextract` and its output bytes stay unchanged.

## 2. Decisions (fixed before coding)

**D1 Inputs.** `lattice spec impact --project DIR (--candidate CDIR | --change cN [--state-dir SD]) [--inventory INV]
[--tiers 123] [--layers ACD] [--baseline run|none|FILE] [--no-flows] [--race] [--json] [--section S] [--out FILE]`.
Base: `DIR/{REQUIREMENTS.md,spec.py,spec.trace.json}`, or the record's `private.base`. Candidate: `CDIR` (the same three
files), or the record's `new_*` fields (the record must be `ready` or `accepted`, else `change_not_ready`). App: the
harness flags `lattice check` fills from `studio.toml` (`_fill_check_args`). Inventory: `[implementation] inventory` or
`--inventory` (Spike C's `load_inventory`, with staleness). A change record is read as a plain file; `StateStore` is
never opened (opening it writes counters). Without `REQUIREMENTS.md`, the rule diff is empty and a note says so. Exit 0:
report produced. Exit 2: inputs unusable, or `--out` inside DIR or the app root. Exit 3: T1 could not run (the report
says `tier1: not_run` with the error).
**D2 Output `lattice.impact/1`.** Canonical JSON: sorted keys and arrays, relative `/` paths. No absolute path, time,
duration, host or port (timings go to stderr).

| key | content |
|---|---|
| `input` | `kind` (candidate or change), `change`, sha256 of the base and candidate files, `inventory {extract_sha256, workflows_sha256, stale}`, `app {configured, layers}` |
| `diff` | `rules {added, changed[{old,new}], removed}` (`diff_rules`); `targets {added, removed, modified}` (`changed_targets`) |
| `tiers` | `"1".."4"` -> `run` or `not_run:<reason>`; `"4"` is always `not_run:spike_e` |
| `tier1` | `failing[]`, `resolved[]` (fail on base, pass on candidate), `flows[]`, `baseline {run, failing}`, `counts` |
| `tier2` | `determined[]`, `flows[]`, `decidability[]` (one entry per added, changed or removed rule, and per changed target) |
| `tier3` | `locations[]`, `flows[]`, `unread[]`, `external {count}`, `counts` |
| `tier4` | `null`, reserved for E: `{implementation {ref, diff_sha256}, failing[], resolved[], flows[], t3_vs_edit}` |
| `summary` | counts per tier and kind; `workflows {t1_fail, t2_fail, t3_may_fail}` |

Every item has: `id`: `t<k>-` plus the first 10 hex digits of the sha1 of its canonical content; `tier`, `kind`,
`target` (`kind:name` or null), `rules[]`; `evidence[]`: `{kind: check|spec|code|extract, at: code:<path>:<line> |
spec:<file>:<line>, what}`; `file` and `line`: mandatory in T3. In T1/T2 they are the step's spec line if it resolves
(AST: the string literal equal to the scenario name), else the failing route's registration, else null with `why`.
**D3 T1, observed.** **What runs:** the candidate spec, plus each `verified` flow of the inventory's `workflows.json`,
frozen by Spike C's `flow_scenario` (as `flow_<id>`), through `check.check(..., layers=ACD)` on a throwaway database.
With `--baseline run` (the default), the base spec and the same flows run the same way. **`failing`:** **every finding**
of the candidate run, as `{layer, rule, case, summary, observation: runtime|static_checker, also_fails_on_base}`.
**`target`:** `_finding_target`, else from an A finding's details (GO808 -> `machine:T.c`), else null. A null target is
**kept, never dropped**. **`label`:** `label_impact(report, cand, diff, trace, base_spec=base)` for targeted items,
`unlinked` otherwise. `incremental.py` is not edited. **Flows:** a D failure on a flow is `baseline_drift` (its
expectations are today's answers); a C finding under the candidate's invariants, absent under the base's, is
`workflow_fails` (observed); with a stale inventory, flows are not run (reason `stale`). **X** runs only with `--race`,
as `reproducible: false`. **`resolved`:** base findings absent from the candidate run.
**D4 T2, determined.** A pure function of (base, candidate, baseline pass set, flows); it never sees the candidate run.
The **observed corpus** is every base scenario step that passed in the baseline, plus every verified flow step.
**D4a Same request, same prior state, incompatible expectation.** **Match:** a candidate step and a corpus step match
when their given rows, every earlier request (method, path, user, json, params) and their own request are canonically
equal. **Also required:** `users` and the profile schemas of the given tables are equal in base and candidate, and the
prefix contains no `Await`. **Incompatible** means no response can satisfy both expectations, decided by the checker's
own `_same`/`subset` (numbers must differ by more than 2x its tolerance): the statuses differ; at one body path the
scalars differ, the list lengths differ, or the types differ; for one `expect_db` table and `where`: the counts differ,
an `equals` value differs, or `count: 0` meets `equals`. **Claim:** `scenario:X fails at step <= k`, the first such
step, with both spec lines and both expectations. **Status:** `determined` when the premise "the corpus step passed" was
observed (the baseline run, or a verified flow); `conditional` when it comes from a `--baseline FILE` of unknown
provenance.
**D4b Workflows after the change.** A verified flow that is D4a-identical to a candidate step, and whose observed answer
is incompatible with that step's expectation, is `fails_after_change`. This holds for any implementation that passes the
candidate.
**D4c Spec-runtime replay.** Only for endpoints with programs in both specs. Corpus scenario prefixes replay on the
candidate's Lattice runtime. An answer incompatible with a step the app passed is a `runtime_disagreement` (`check:
null`). On Streak: `not_applicable: 0 programs` (m).
**Decidability.** Each changed rule or target gets `determined`, or undecidable with one reason: `new_request`;
`compatible_expectation`; `base_prefix_failed`; `profile_or_users_changed`; `await_in_prefix`; `invariant_or_machine`
(needs T1); `no_linked_check`; `removed_only`.
L011 pairs inside the candidate are listed as `spec_contradiction`.
**D5 T3, static.** No app; only the extract, the workflows and the trace.
*Seeds:* **(a)** added and changed rules, through Spike C's `ImplContext.of(cand, cand_trace, inv)`; removed and changed
ones through the base context; **(b)** changed targets: a scenario's changed steps' routes; an invariant's tables and
columns (Spike C's parser); a machine's `status_writes` for (table, column); `profile:T`'s struct and columns; **(c)**
each T1 failing step's route, when T1 ran.
*Locations, per seed route:* **`direct`:** the registration; the handler declaration; raises whose (status, code) is a
changed expectation; params named by a changed request or db key; owner uses (access seeds); `status_writes` of a
changed machine; the struct line and changed column lines. **`closure`:** every declaration in `calls`.
**`table_peer`:** the registration and handler of other routes that touch a changed table. **`insertion`** (an added
endpoint with no route): the registering functions of routes that share the longest whole-segment path prefix beyond the
router chain (>= 1 segment), else the extract's `main` files; plus the structs of the tables the new rules link.
*Function resolution:* `importpath:F` (or `Recv.M`) resolves in `<module-relative dir>/*.go`, test files excluded, when
exactly one line matches `^func\s+F\s*[\[(]` (or the receiver form). The declaration's range ends at the next `^}`.
`X.funcN` resolves to `X`'s declaration, with `literal: N`. 0 or 2+ matches is `unread`. Out-of-module calls are counted
in `external`. Each location carries a `via[]` chain ending in an extract fact, e.g. `rule:R44 -> scenario:... / step 2
-> route:POST /v1/habits -> call:...:atLimit -> code:internal/habits/routes.go:<line>`. Never grep, never names.
*`unread` lists:* extract `unread` items inside a reported range or a seed closure; `response_shape_not_extracted`, for
a changed body expectation; unresolved declarations; seeds with no element; unparsable invariants.
*Flows:* T3 `flows` are the flows with a step on a seed route: `may_fail`, never a claim.
**D6 MCP `change_impact`.** **Registration:** `writes=False`, `readOnlyHint` true. Registered **only with a `studio.toml
[impact]` table**, after the Spike C tools if present, else after `change_list`; `tools/list` is otherwise
byte-identical. **Arguments:** `{change? | candidate? (a project-relative dir), observe?: bool = false, section?:
summary|tier1|tier2|tier3|flows|unread|decidability, limit? <= 200}`. **Default (T2 + T3):** an in-process pure read.
**`observe: true`:** runs the CLI as a **subprocess** (`--tiers 123 --json`), so the harness's `chdir` and `sys.path`
never touch the server. It uses a fresh `LATTICE_CONTAINER_OWNER`, a timeout <= 900 s, a process-group kill, and
`remove_owned_containers` in `finally`. **Output:** the summary is <= 4k characters (`cap_result`). Error codes:
`change_not_ready`, `no_candidate`, `inventory_missing`, `no_app`. `session.py` is unchanged this round.
**D7 Studio.** `server.py`, `checks.impact`, `impact_json` and the pipeline are untouched (R4). For the same record and
code, the report's T1 items restricted to C/D carry the same `(target, label, rules)` as `checks.impact.failing` (probe
PS). API.md v1.23 adds documentation only: the `[impact]` table and the tool.
**D8 Never:** write the target repo, the spec files, the inventory or the state directory; create a cache or lock; call
a model or `claude`; run `go build` or git (except `state_dir_path`'s read-only `rev-parse` without `--state-dir`);
propose or accept a change; infer a location from names; leave a `lattice-` container after any run.

## 3. Probes (runner `dogfood/impact_probes.py`; blind pass rules)

**The change set C (held out, gated).** A **separate agent**, neither the builder nor this author, is dispatched by the
orchestrator before the build. It writes **>= 18 changes** (>= 15 must survive) to `$SCRATCH/changeset/<id>/`, each
independent from a0679e9.
Kinds, at least one each: K1 the 50-active-habit limit; K2 a request bound; K3 a new required request field, with a
column; K4 a renamed response field; K5 a new Nudge.delivery edge; K6 a removed edge; K7 an access rule; K8 a removed
endpoint; K9 an added endpoint; K10 a refusal's status or code; K11 a new invariant; K12 a default value; K13 the sync
page limit; K14 a nudger rule; K15 a pure rewording (control).
Each change has: `REQUIREMENTS.md`, `spec.py` and `spec.trace.json`: they load, and lint adds no unintended L011;
`ref.diff`: `*.go` under `cmd/` and `internal/` only, with the harness and CONTEXT unchanged; `meta.json`: the kind, the
intent, the agent's own ACD failing list on S, and the ACD verdict on S+ref, which must be `pass`.
The orchestrator records the `sha256` of the sorted file list before the build ends. **The builder never reads
`changeset/`.**
The runner computes the truth: **T1:** an independent `python -m lattice.spec check --spec <c>/spec:SPEC ... --layers
ACD --json` on S. **Ref check:** the same check on S+ref must pass; otherwise the change is excluded, as a finding.
**T3:** the existing non-test files and top-level declarations (func or type) whose old-side lines `git diff -U0` edits.
A `go/parser` tool (`dogfood/impact_truth/main.go`, ~60 lines, independent of D5) computes them. Added files are
reported and kept out of the denominators. **Flows:** the verified flows replayed on S+ref; the observed failures.
**H-nat** (real history; report-only, not blind since the history is visible): base = S4's spec, candidate = S's spec,
app = S4. Its T3 truth is the 5 modified files of `1d00ede..a0679e9`.

| id | probe | prediction | pass |
|---|---|---|---|
| PT1 | T1 `failing` (layer, rule, case) vs the independent rerun, every change of C plus a control (candidate = base) | exact on all; control 0; T1 non-empty on 11-15 of C (p) | exact, 0 false, 0 missed |
| PT1x | T1 run 10x on the first 3 changes of C (by sorted id) | byte-identical | 10/10 |
| PT1l | labels vs `label_impact` recomputed by the runner from the rerun | equal | 100% |
| PT1f | flows on base: 0 `workflow_fails` (p); a planted copy with one wrong `expect_status` | the copy is `baseline_drift` | exact |
| PT2 | T2 `determined` scenario claims vs the T1 truth | precision 1.0; recall 0.10-0.40 (p); case agreement reported | **precision 1.0**; recall reported |
| PT2i | spec-only mode (`--tiers 23 --baseline FILE`, the runner's base report) vs full mode | same `determined` set | identical |
| PT2w | T2 `fails_after_change` vs the flow truth on S+ref | 0 claims on Streak (r): vacuous | precision 1.0, or vacuous stated |
| PT2r | D4c on 3 minishop fixture changes (unit level) | as designed; Streak `not_applicable` | exact |
| PT2d | decidability: one entry per changed rule and target, each reason from D4's list | complete | 100% |
| PT3 | T3 vs the reference edits, micro-averaged over C | file recall 0.85-1.0, declaration recall 0.65-0.90; precision files 0.20-0.50, declarations 0.05-0.25 (p) | see below |
| PT3w | T3 `may_fail` flows vs the flow truth | recall >= 0.8 (p) | reported |
| PU | every T3 location passes Spike C's `check_code` (path, line, symbol); every extract `unread` in a reported range is listed; the 2 literals map to their enclosing declarations | 100% | 100% |
| PN | H-nat | T1 35 failing; `resolved` = {`access__logout__as_anon`}; T2 0 claims; T3 3-5/5 files (p) | T1 exact; the rest reported |
| PI | `--change cN` vs `--candidate` on the same files (PS's record) | identical except `input.kind` and `change` | identical |
| PS | a `ready` record from Studio's pipeline (scripted stub generator, as in `tests/studio`) on the S clone: `checks.impact.failing` vs the report's T1 restricted to C/D | same (target, label, rules) | equal |
| PD | T2+T3 mode: 10 processes per change with random `PYTHONHASHSEED`, plus a second scratch root; scan for `/Users`, `/private`, ISO times | identical; none | 10/10 |
| PW | snapshot (sha256, mode, mtime, listing including hidden files) of the clone after `bin/`, the base and candidate dirs, the inventory and the state dir, taken around: CLI x10, the MCP tool x10 (stdio, `--readonly`, `observe` on and off), PS's read. A recording `claude` shim is first on `PATH` | 0 changed, 0 new, 0 shim calls | all 0 |
| LEAK | `lattice-` containers, before vs after every run; includes a fault run (the candidate's check raises mid-run) and an MCP timeout | 0 | 0 |
| PB | byte-compat, see below | all | all |
| PX | wall time on S: T2+T3; T1 (2 ACD runs + 59 flows) | 0.3-1.5 s; 65-90 s | <= 3 s; <= 180 s |

**PT3 pass:** file recall >= 0.80 and declaration recall >= 0.60; precision >= 2x the all-files baseline; median files
per change <= 12 (of 42); at most 1 change with zero true files.
**PB checks, against `git archive bb3ff0c`:** identical: `lattice --help`, `lattice spec` usage, `python -m lattice.spec
check --help` and `draft --help`; without `[impact]`, identical: MCP instructions and `tools/list` in Spike C's 8
configs, the Studio route table, `/api/model`, and a change record (stub); `incremental.py`, `server.py`, `check.py`,
`implementation.py` and `gocheck/` have an empty diff; `pytest tests/spec tests/mcp tests/studio tests/importer
tests/gocheck` shows only the same pre-existing failure ids; `dogfood/studio/check.sh` passes with the same step count;
with `[impact]`: `tools/list` = base + 1.
**Why these thresholds.** T3 recall is bounded by the route-closure model: edits outside every closure (wiring in
`main`, shared helpers, push adapters) are reachable only as insertion points or `status_writes` roots, about 1 file in
5 on H-nat (`push.go`); hence files >= 0.80. Declarations: 0.60, since new helpers and edits between functions count
only at file level. Precision is reported (owner); the 2x lift and the size cap stop "list everything".

## 4. Mutants (`dogfood/impact_mutants.py`, on a scratch copy; the control passes first; predicted 16/16)

| id | mutant | killed by |
|---|---|---|
| IX1 | T1 drops A findings (`label_impact`'s filter) | PT1 on dev fixture `edge_removed` (GO808) |
| IX2 | T1 reuses the base report (no candidate run) | PT1 |
| IX3 | D4a ignores earlier requests (L011-style first step only) | PT2 precision; unit `prefix_differs` |
| IX4 | an added body key counts as incompatible | PT2 precision; unit |
| IX5 | D4a skips the baseline-passed premise | unit `base_prefix_failed` (minishop) |
| IX6 | T2 reads the candidate run | PT2i |
| IX7 | `.funcN` and ambiguous matches resolved to the first hit | PU |
| IX8 | locations from grep of path strings | PU (`via` must end in an extract fact); PT3 lift |
| IX9 | absolute paths or unsorted sets | PD |
| IX10 | the report cached under the inventory | PW |
| IX11 | the Studio pipeline switched to the new runner | PB (change record), PS |
| IX12 | `change_impact` listed without `[impact]` | PB |
| IX13 | no container cleanup on a check exception | LEAK fault run |
| IX14 | a flow's D failure counted as `workflow_fails` | PT1f |
| IX15 | a null-target finding dropped | PT1 (an A or untargeted finding in a dev fixture) |
| IX16 | `StateStore` opened to read `--change` | PW (the state dir gains a `meta.json` write) |

## 5. Risks, and what this cannot show

1. **T2 is narrow by design.** Exact prefix identity makes it sound but sparse: recall 0.10-0.40, and 0 for flows on
   Streak. Raising recall would mean predicting, which the owner ruled out.
2. **T1 assumes determinism:** the app as a function of (database, request, test clock). The nudger's `Await` timing is
   the weak spot (excluded from D4a); PT1x measures it.
3. **T3 is limited by the extract:** no response shapes; interface dispatch is `unread`; closures stop at depth 4; an
   added endpoint in a new package gets only insertion points (H-nat shows the cost); the lexical resolver assumes
   gofmt'd top-level declarations, so build-tagged duplicates become `unread`, never guesses.
4. **One reference implementation.** T3 recall measures agreement with one implementation; another correct edit may
   differ. T1 and T2 do not depend on the reference.
5. **Cost and bias.** T1 doubles a check (about 60 s on S, about 10 min on S4-like states with failing
   `Await`s). The builder sees S, H-nat and minishop; only C is blind.
6. **Cannot show:** T4; a second app (R5); T3 for Python apps (no extract; they answer `unread: no extract for this
   framework`); X races; the owner's intent behind a change.

## 6. Blind vs post hoc, and commands

Fixed now. A missed prediction is a **finding**; fixes are separate post-hoc commits, rerun with deltas reported. The
orchestrator scores C **once**, after the freeze; a fix made after seeing C is never re-scored as blind. If fewer than
15 changes survive, C's agent adds more *before* any scoring. **Pass:** every gated row of section 3, mutants 16/16 (by
probes or the named unit tests, disclosed), and PB.

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-impact PYTHONDONTWRITEBYTECODE=1
SCRATCH=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/impact
DRV=$SCRATCH/../perfengines/perfvenv/bin/python   # pymysql + bcrypt over the Lattice venv (read-only reuse)
cd /Users/vivekgade/Documents/lattice-impact; docker image inspect mysql:8.4 >/dev/null
git -C /Users/vivekgade/Documents/streak status --short     # same as before (pre-existing ` M studio.toml`)
"$DRV" dogfood/impact_probes.py --stage dev --streak-ref a0679e9 --streak-ref2 1d00ede --scratch "$SCRATCH/p" \
    --workflows docs/results/data/2026-10-15-workflow-layer/workflows.json --out "$SCRATCH/p-data"  # all but C rows
uv run python dogfood/impact_mutants.py $(seq -f IX%g 1 16)
uv run python -m pytest tests/spec/test_impact_report.py tests/mcp/test_change_impact.py -q
uv run python -m pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck -q -rs    # PB
shasum -a 256 lattice/spec/impact*.py lattice/mcp/tools.py > "$SCRATCH/freeze.sha256"           # BEFORE C
"$DRV" dogfood/impact_probes.py --stage changeset --changeset "$SCRATCH/changeset" --scratch "$SCRATCH/c" \
    --out "$SCRATCH/c-data"                                                                     # orchestrator, once
uv run ruff check <changed .py>; uv run ruff format --check <created .py>; gofmt -l dogfood/impact_truth
```

## 7. File ownership (build node)

**Owns:** new `lattice/spec/impact_report.py` (inputs, T1, schema, `render`, `main`), `impact_tier2.py` and
`impact_tier3.py`; `lattice/spec/__main__.py`: one `AUTHORING` line; `lattice/mcp/tools.py`: `_has_impact_table` and
`_build_impact`, additive only; `lattice/studio/API.md`: v1.23, documentation only; new tests:
`tests/spec/test_impact_report.py`, `tests/mcp/test_change_impact.py`, and `tests/spec/fixtures/impact/` (dev changes on
minishop, a GO808 `edge_removed` case, an untargeted-finding case); new `dogfood/impact_probes.py`,
`dogfood/impact_mutants.py` and `dogfood/impact_truth/main.go`; the results doc and its data.
**Reuses read-only:** `incremental.{diff_rules, changed_targets, label_impact, spec_elements}`;
`implementation.{ImplContext, load_inventory, check_code, flow_scenario}`; `check.{check, subset, _same}`;
`lint.conflicts`; `state.{state_dir_path, remove_owned_containers}`; `cli._fill_check_args`.
**Not touched:** `incremental.py`, `server.py`, `check.py`, `implementation.py`, `session.py`, `cli.py`, `gocheck/`,
`importer/`, the mock and the web UI; existing tests and goldens (any edit is a finding); Streak, `job-scheduler*` and
the main checkout; `$SCRATCH/changeset/`: the builder never opens it.
The builder makes no commits; the orchestrator stages explicit paths only.

## Amendment 1 (orchestrator rulings, 2026-10-16, before any code)

**R1 approved:** the CLI home is `lattice spec impact` (one `AUTHORING` entry; `lattice --help` unchanged).
**R2 approved:** Tier 1 runs layers ACD; layer X only through `--race`, never gated. **R3 approved with a
tightening:** the MCP tool `change_impact` is registered only when `studio.toml` has an `[impact]` table, and
`observe: true` (which runs the app and Docker) is **refused under `--readonly`** (a read-only server must not start
processes or containers); the refusal is a tool error with a stated code. **R4 approved** (no Studio pipeline edit;
`checks.impact` stays byte-identical). **R5 approved:** the gated set is the separate-agent change set (>= 18
changes, 15 kinds), written and hashed before the build, scored once by the orchestrator after the engine is
frozen; the second-app held-out is infeasible and the document says so. **R6 approved** (lexical function-location
resolver in Python from the extract; `goextract` and its output bytes unchanged).
**Rulings.** (1) The owner's correction stands and is made visible in the report: Tier 1 and Tier 2 are exact or
decided; everything else is Tier 3 `may_fail` (static, with unknowns listed) until Spike E observes it (Tier 4).
The report and the results must say plainly that **which workflows fail is mostly not decidable before the
change exists**: flows record today's answers and pass by construction, so before implementation only changed
invariants (Tier 1) and exact-prefix contradictions (Tier 2) are decidable. (2) The change set is authored by a
separate agent after this commit; the orchestrator records its sha256 manifest in a file next to the set and
hashes it before the build node runs the gated probes; the builder never opens `changeset/` by hand (the runner
reads it). (3) Tier 3 thresholds stand (file recall >= 0.80, function recall >= 0.60, precision >= 2x the
all-files baseline, median <= 12 files). (4) Tier 2 recall may be low; it is reported as measured and not
inflated. (5) Probes rewrite `studio.toml`'s `go_src` in each clone; nothing is written into the owner's Streak
checkout (whose ` M studio.toml` predates this work). (6) No API key, no model call anywhere in the engine.
