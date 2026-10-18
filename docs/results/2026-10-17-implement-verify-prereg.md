# Implement with verification (Spike E): pre-registration

## 1. Status, goal and the exact gap

2026-10-17, proposed branch `np-implement` (from `implemented-logic-spikes-a-to-g` 6a318e5: Spikes A-D, F, G merged).
**Written BEFORE any code.** Results: `2026-10-17-implement-verify-results.md`, data in
`docs/results/data/2026-10-17-implement-verify/`. Labels: **(m)** measured during recon in this container, **(r)** a
recon script's output (a blind expectation), everything else a **prediction (p)**. Recon: code reading at 6a318e5
(`lattice/studio/rebuild.py`, `areas.py`, `lattice/spec/impact_report.py`, `impact_tier2.py`, `impact_tier3.py`, the
rebuild and change-impact pre-registrations and results), `pytest tests/spec/test_impact_report.py` (58 passed, 14.3 s)
and `tests/studio/test_rebuild.py` (19 passed, 10.6 s) (m), and a recon script that ran D's `build_report` on the seven
minishop impact fixtures (r, numbers below). No model was called.

**Owner goal (plan Addendum, E).** Drive the existing rebuild loop from a Spike D impact report on a branch in an
isolated worktree. Gate on before/after workflow results: the intended workflows change as reported, and there are 0
unexpected regressions. Never merge. Fill Tier 4 of the impact report. **Binding owner rule (D, F):** a workflow failure
is observed or logically determined, never predicted. Tier 4 is the "observed after the change" tier: every statement in
it comes from a check run on the rebuilt code.

**What the code says (m, code reading).**
1. **The rebuild loop needs an accepted change, or a scope over rules the owner's trace already has.** Change mode
   (`Rebuilder.start(changes=...)`) branches `rebuild/<cid>` from the owner's `HEAD`, and its brief says the spec "already
   contains the accepted change(s)". Scoped mode resolves rule ids against `trace_data()`, which reads the **owner's**
   `spec.trace.json`, so a candidate's added rule gives 404 `no rule`. Acceptance is human-only, and it commits to the
   owner's branch (`accept_commits_once`). So E cannot hand a candidate to either mode unchanged.
2. **`Rebuilder` is relative to its `project` path.** `repo = _toplevel(cfg.repo_dir)`, and `owner_head = rev-parse
   HEAD` there. A linked git worktree is its own toplevel. So a `Rebuilder` built on a detached worktree whose HEAD is a
   commit holding the candidate spec treats that worktree as "the owner's checkout". Its rebuild branch, worktree and
   commit then land on that base. The owner's real checkout is never its `repo`. **E edits no line of `rebuild.py`.**
3. **Change-mode targets come from `checks.impact.failing`** (`_targets`: label `not_built_yet`), the shape D's probe PS
   pinned against T1 restricted to C/D. The brief comes from `request`, `rules {added, changed, removed}` and
   `markdown_diff`. E can build such a dict from the D report and pass it in memory. No Studio record or `StateStore`
   is needed.
4. **Gates are the only pass criterion of a change-mode round**: all gate commands exit 0, and the integrity check is
   clean. **Flows are not gates.** A rebuild can pass while a verified workflow that no spec scenario covers changes
   its answers. That is the gap E closes: the before/after flow comparison happens outside the loop.
5. **D reserves Tier 4.** `tiers["4"] = "not_run:spike_e"`, `"tier4": null`, with the reserved shape `{implementation
   {ref, diff_sha256}, failing[], resolved[], flows[], t3_vs_edit}` (D prereg D2). `stamp(4, item)` gives `t4-` ids.
   D's file hashes at 6a318e5 equal D's freeze (`impact_report.py` a3e11f1c..., `impact_tier2.py` 6f86fc6c...,
   `impact_tier3.py` 22228513...) (m).
6. **D's T1 lists failures only.** A flow absent from `tier1.flows` passed if it was run (`counts.flows_run`). The
   report carries no flow *pass* set and no observed status per step. So E runs before and after itself, through the
   same runner, and checks its before against the report.
7. **The worktree default writes the owner's git metadata.** `worktrees = "repo"` puts worktrees under
   `<repo>/.lattice/rebuilds/` and appends to `info/exclude`. With a linked worktree as `repo`, that is the shared
   common dir. E overrides the `[rebuild]` table in memory with `worktrees = "temp"` (`Rebuilder(table=...)`) and never
   edits `studio.toml`.

**Recon on the Docker-free corpus (r; minishop, in-process FastAPI on sqlite, D's `build_report` tiers 1-2, ACD).**
- `status_changed` (cancel twice: 409 -> 400): T1 = 1 D finding (`ann cancels her pending order / step 2`,
  `not_built_yet`), T2 = 1 `determined`, 1.18 s.
- A verified flow that repeats that scenario's two requests is a T2 `flow_fails_after_change` (`determined`) and passes
  in T1 (no drift).
- `new_request`, `reword` and `removed_rule`: T1 empty.
- `bad_invariant`: T1 = 2 C findings, seed included.
- `conflict`: T1 = 1 D finding labelled `contradiction`, T2 = 2 claims.
- minishop's spec has 2 scenarios (cancel twice, add a line). No scenario requests `GET /orders` or `GET /orders/{id}`,
  so an edit to those routes is invisible to an ACD gate.

**Rulings requested.**
- **R1 (drive mode):** change mode, as in 2: a detached "spec worktree" at a new commit `S` = `HEAD` + exactly the
  candidate's spec files, on branch `implement/<eid>`. The `Rebuilder` gets that worktree's project dir, an in-memory
  table copy (`worktrees = "temp"`, the agent mode, `max_rounds`), and a change dict built from the report (D5). The
  rebuild branch is `rebuild/<eid>` from `S`. No edit to `rebuild.py`, `server.py` or `areas.py`.
  - Alternative: scoped mode over the candidate's added and changed rules on that worktree. It tolerates out-of-scope
    failures, but it moves `implement/<eid>` by `update-ref`.
- **R2 (home):** the driver is `lattice/studio/implement.py` (it drives a Studio loop). The pure Tier 4 logic is
  `lattice/spec/impact_tier4.py`. The CLI is `lattice spec implement`, one `AUTHORING` entry (D added `impact` the same
  way, with `lattice spec` usage byte-identical).
- **R3 (no new surface):** no MCP tool, no HTTP route, no Studio view and no session allowlist change this round.
  Showing Tier 4 in F's Impact view is later work.
- **R4 (verdict for within-report flow changes):** a flow whose answers change on a route D listed as T3 `may_fail`,
  where no candidate step backs the new answer (D6), gives `needs_owner`, never `pass`.
- **R5 (oracle agent):** a probe-only agent mode `oracle` turns a reference diff into stub-agent rounds. It tests the
  verification machinery apart from agent quality. It is test-only like `stub`, never the default, and recorded in
  `tier4.rebuild.agent`.

## 2. Decisions (fixed before coding)

**D1 CLI.**
```
lattice spec implement --project DIR --impact REPORT.json (--candidate CDIR | --change cN [--state-dir SD])
    [--inventory INV] [--flows FILE] [--agent claude|stub|oracle] [--ref-diff FILE] [--max-rounds N] [--model M]
    [--usd-cap X] [--work DIR] [--json] [--out FILE]
```
- `--impact` is a `lattice.impact/1` report for the same base and candidate.
- `--flows` is a verified-flows file (the `workflows.json` shape) for apps with no inventory. Python apps have no
  extract, so the minishop corpus uses it.
- `--work` is where E's spec, before and after worktrees go. It defaults to the system temp directory and must be
  outside the project, the app root and the inventory.
- `--out` follows D's rule: outside the project, the app root and the inventory.

Exit codes:
- **0:** a report with `tiers["4"]` set (any verdict).
- **2:** refused (D2, D3), or inputs unusable.
- **3:** the before or after run raised, or the owner guard tripped. `tiers["4"] = "not_run:<reason>"`, and the report
  is still written.

Timings and commit shas go to stderr.

**D2 Preconditions (refusals, exit 2, codes).**
- `bad_report`: the schema is not `lattice.impact/1`, or `tier4` is not null.
- `report_mismatch`: `input.base` and `input.candidate` sha256 differ from the files E reads, or
  `input.inventory.{extract_sha256, workflows_sha256}` differ from the inventory E loads.
- `stale_inventory`: the inventory is stale.
- `spec_contradiction`: the report's T2 has a `spec_contradiction` item (an L011 pair new in the candidate). Such a
  candidate cannot be built, so no agent is spent on it.
- `not_a_repo` / `no_rebuild`: `capability()` is false. The reason is passed on.
- `branch_exists`: `implement/<eid>` or `rebuild/<eid>` exists.
- `change_not_ready`: D's `read_change` codes, read as a plain file.

`eid` = `i` + the first 10 hex digits of sha256(base hashes + candidate hashes), so it is stable per candidate.

**D3 Worktree, branch and no-merge guard.**
- *Owner snapshot*, taken before anything and again at the end:
  - `rev-parse HEAD`;
  - `symbolic-ref -q HEAD`;
  - `status --porcelain=v2 -uall` (with `GIT_OPTIONAL_LOCKS=0`, so the index is not refreshed);
  - `diff --cached --name-status`;
  - `for-each-ref` minus `refs/heads/implement/*` and `refs/heads/rebuild/*`;
  - `stash list`;
  - `worktree list --porcelain` minus E's own entries.
- Any difference sets `tier4.owner.untouched = false`, the verdict `fail`, and exit 3.
- *Spec commit:*
  - `git worktree add --detach <work>/spec-<eid> HEAD`;
  - write exactly `REQUIREMENTS.md`, `spec.py` and `spec.trace.json` (the candidate's) into the project dir there, plus
    `stub-rebuild.json` in `stub`/`oracle` mode only (listed as `test_only_files`);
  - `git add -- <those paths>`, never `-A`;
  - one commit `spec: candidate <eid> (Lattice implement; not built)` with the trailer `Lattice-Implement: <eid>`;
  - then `update-ref refs/heads/implement/<eid> <S> ""` (create-only, compare-and-swap).
- *Refused* (exit 2):
  - the project dir is the owner's checked-out branch with a non-empty `--branch` override;
  - `implement/<eid>` equals `HEAD`'s branch, is in `[rebuild] protected_branches`, or is checked out anywhere.
- E never calls `merge`, `rebase`, `reset`, `checkout`/`switch` in the owner's worktree, `push`, `update-ref` on any
  other ref, or `Rebuilder.start(confirm_unsafe_branch=True)`.
- **Never merges:** a passed rebuild leaves `rebuild/<eid>` with one build commit on `S`. The rebuild worktree is kept
  for review (rebuild's own behaviour), and E prints rebuild's `merge_hint` text verbatim.
- E removes its spec worktree and its before/after worktrees in `finally` (`git worktree remove --force`, then `prune`).

**D4 Before and after runs (observed, symmetric).**
- *Before* = the candidate spec plus the verified flows (inventory or `--flows`, frozen by Spike C's `flow_scenario`),
  run on the code at `S`, which is the base code.
- *After* = the same spec and flows, on the code at the build commit `B`.
- Each runs in its own fresh `git worktree add --detach` (never in the kept review worktree). It uses
  `impact_report.harness_runner(<worktree>/<project_rel>)`, or an injected runner in the Python API, through D's
  `run_tier1(..., baseline="none")`. D's runner reuse is read-only.
- Each run uses a fresh `LATTICE_CONTAINER_OWNER` token and D's `owned_containers()` cleanup.
- *Cross-check:* when the report's `tiers["1"] == "run"`, E's before `failing` set (by D's `_ident`) must equal the
  report's `tier1.failing`. The result is recorded as `before.matches_report` (`true`, `false`, or `null` when T1 was not
  run). `false` is a finding, and the verdict cannot be `pass`.
- *Nothing to build:* the before run has 0 failing and the report has 0 T2 claims. Then no rebuild starts, the verdict
  is `nothing_to_build`, and `implementation` is null.

**D5 The change dict handed to the loop** (in memory, never written to a state dir):
- `id`: `eid`.
- `request`: the record's `request` (`--change`), else the fixed template `"Implement the candidate spec change: <n>
  rule(s) added, <n> changed, <n> removed."`.
- `rules`: copied from `report.diff.rules`.
- `markdown_diff`: `difflib.unified_diff` of the base and candidate `REQUIREMENTS.md`, 3 lines of context.
- `checks.impact.failing`: E's before items of layers C and D, as `{target, label, message: summary}`.

Agents:
- `claude`: rebuild's real agent, unchanged (the argv, no API key, `gate_env`).
- `stub`: the fixture's `stub-rebuild.json`.
- `oracle`: E writes `stub-rebuild.json` whose round 1 holds the post-diff content of every file `--ref-diff` touches
  (applied with `git apply --check`, then `git apply` in a scratch copy).
- `--usd-cap`, `claude` mode only: E polls the record's `usage` and calls `Rebuilder.stop` once the CLI's list estimate
  passes the cap. The run ends `stopped`, so the verdict is `not_built`.

**D6 Tier 4 (pure: `impact_tier4.compute(report, before, after, flows, rebuild, edit) -> dict`).** Identity is D's
`_ident`.

**Classes of candidate checks:**
- `resolved`: failing before, passing after.
- `still_failing`: failing before and after.
- `regression`: passing before, failing after.

**Flows** (verified flows only; a flow that failed *before* is `flow_before_failed`, listed and not judged):
- `flow_unchanged`: passes before and after.
- `flow_changed`: passes before, gets a D finding after. It carries:
  - `reported_as`: `t2_determined` if the report's T2 `flows` claim this flow, else `t3_may_fail` if T3 `flows` list
    it, else `unreported`;
  - `spec_backed`: true when the flow's first failing step has a candidate scenario step with the same method,
    `route_key` and user (null = no token), whose `expect_status` equals the status observed after (read from the D
    finding's details; when it is absent, `spec_backed: null` with `why`, which counts as false), and that step passes
    after.
- `workflow_fails`: a C finding on the flow after, under the candidate invariants, absent before.

**T2 confirmation:**
- `t2_confirmed`: every T2 `fails_after_change` flow is observed `flow_changed`, and every T2 `determined` scenario claim
  is a before failure.
- `t2_contradicted`: otherwise. This is a soundness finding against D, never hidden.

**`t3_vs_edit`** (report-only, never gating; D's risk 4: one implementation):
- The edit is `git diff -U0 S..B`, minus test files, the spec files and `test_only_files`.
- *Files:* the touched set vs the T3 location files gives `recall`, `precision` and the `missed` list (each a
  `t3_missed` item).
- *Declarations:* Go only, through `impact_tier3.GoTree`'s declaration ranges (read-only). Python: `not_applicable: no
  extract`. T3 not run: `not_applicable: tier3 not run`.

**Verdict:**

| verdict | when |
|---|---|
| `pass` | rebuild `passed` and `owner.untouched` and `before.matches_report != false` and 0 `regression`, 0 `still_failing`, 0 `workflow_fails`, 0 `unreported`, 0 `t2_contradicted`, and every `t3_may_fail` flow change is `spec_backed` |
| `needs_owner` | as `pass`, except >= 1 `t3_may_fail` change is not `spec_backed` (R4) |
| `fail` | the rebuild passed and any other condition is broken |
| `not_built` | the rebuild ended `gave_up`, `stopped` or `interrupted` (no after run; `tiers["4"] = "not_run:rebuild_<status>"`) |
| `nothing_to_build` | as D4 |

"**Unexpected regression**", the owner's term, = `regression` + `unreported` + `workflow_fails`. The gate is 0.

**D7 Output.** The input D report, byte-identical except two keys:
- `tiers["4"]` (`run` or `not_run:<reason>`);
- `tier4`, with these keys:
  - `implementation {branch_spec, branch_build, spec_tree, build_tree, diff_sha256, files[]}` (tree shas, not commit
    shas: deterministic for the stub and oracle modes);
  - `rebuild {status, rounds, agent, model, gates[{command, exit_code}], usage}` (`usage` only in `claude` mode);
  - `before {matches_report, failing_count, flows_run}`;
  - `failing[]` (the after run), `resolved[]`, `still_failing[]`, `regressions[]`;
  - `flows[]`, `t2_check[]`, `t3_vs_edit`;
  - `owner {untouched, checked[]}`;
  - `verdict`, `counts`, `notes[]`.

Every item is `stamp(4, ...)` with `evidence[{kind: check, at: check:<layer>:<case>, what}]`, `target` and `rules`. The
JSON is canonical (D's `report_json`): no absolute path, time, host, port or commit sha.

**D8 Never:**
- merge, push or fast-forward anything;
- write the owner's worktree, index or other refs;
- edit `studio.toml`, the spec files on the owner's branch, the inventory, the input report or a state dir;
- open `StateStore`, or accept or propose a change;
- call a model outside rebuild's `claude` agent; pass an Anthropic API key (rebuild's `gate_env` strips it, and probe PM
  checks it);
- leave an E worktree or a `lattice-` container behind;
- edit `rebuild.py`, `areas.py`, `server.py`, `session.py`, `state.py`, `impact_*.py`, `implementation.py`,
  `check.py`, `incremental.py` or `mcp/tools.py`.

The git repository gains only `refs/heads/implement/<eid>`, `refs/heads/rebuild/<eid>`, their objects, the kept
review worktree (temp) and its `worktrees/` entry.

## 3. Design split: the Docker-free core and the Streak arm

| part | where | runs here |
|---|---|---|
| report shape, D2 refusals, change dict (D5), Tier 4 classification and verdict (D6), byte-compat merge (D7) | `impact_tier4.py`, pure; unit tests on synthetic D reports | yes |
| `t3_vs_edit` | on the Go fixture `tests/spec/fixtures/implementation` (extract + `app/*.go`) with synthetic diffs | yes (static) |
| spec commit, branch and worktree guard, no-merge guard, cleanup (D3) | throwaway git repos under `tmp_path` | yes |
| the loop end to end with the stub and oracle agents | **MS corpus:** the minishop `ok` app committed in a fresh git repo; the base spec; `gate.py` (runs `lattice.spec.check.check` ACD in-process on the worktree's package; prints the `PASS/FAIL: N findings (...)` line, exits 0 iff pass); `flows.json` (5 hand-written verified flows); `[rebuild] agent = "stub"` | yes |
| the real `claude` agent on MS | as above, `agent = claude` | only if the CLI is logged in; else NOT RUN |
| the Streak arm: S (a0679e9) clone, MySQL 8.4 containers, D's held-out change set (21 changes with `ref.diff`, manifest `52e0e541...caa3`), D's frozen engine | owner's machine | **NOT RUN here** (no Streak checkout, no Docker daemon) |

**MS dev changes** (fixture `tests/spec/fixtures/implement/`; the spec dirs are copies of D's minishop impact fixtures,
made self-contained):

The edits:
- `E_ok`: cancelling again answers 400.
- `E_list`: `GET /orders` answers 404. No scenario covers it; flow `wf-list` does.
- `E_ben`: `read_order` drops the owner check (minishop's `wrong_answer` edit), so ben's `GET /orders/1` answers 200
  where flow `wf-read-ben` recorded 404.

The flows: `wf-cancel-twice` (repeats the cancel scenario), `wf-add`, `wf-list`, `wf-read-ann`, `wf-read-ben`.

| id | candidate | stub rounds | predicted verdict (p unless marked) |
|---|---|---|---|
| i1 | `status_changed` | r1 `E_ok` | `pass` in round 1; resolved = {cancel step 2}; `wf-cancel-twice` `flow_changed`, `t2_determined`, `spec_backed`; `t2_confirmed` |
| i2 | `status_changed` | r1 no-op, r2 `E_ok` | `pass` in round 2 |
| i3 | `status_changed` | r1 `E_ok` + `E_list` | `fail`: 1 `unreported` (`wf-list`), gates passed |
| i4 | `status_changed` | r1 `E_ok` + `E_ben` | `fail`: 1 `unreported` (`wf-read-ben`) |
| i5 | `reword` | none | `nothing_to_build` (T1 empty, r) |
| i6 | `removed_rule` | none | `nothing_to_build` (r) |
| i7 | `new_request` | none | `nothing_to_build` (r) |
| i8 | `status_changed` | each round `E_ok` + an edit of `spec.py`; `max_rounds` 2 | `not_built` (integrity restores, both rounds fail) |
| i9 | `status_changed` | no-op x2 | `not_built` (`gave_up`) |
| i10 | `conflict` | (none) | refused `spec_contradiction`, exit 2 (r: T2 has 2 claims; L011 pair) |
| i11 | `status_changed`, `--agent oracle --ref-diff` (`E_ok` as a diff) | generated | `pass`, the same tier 4 as i1 except `rebuild.agent` |

## 4. Probes (runner `dogfood/implement_probes.py --stage core|streak`; blind pass rules)

| id | probe | prediction | pass |
|---|---|---|---|
| PV | the verdict, rounds and counts of i1-i11 vs the table of section 3 | 11/11 as predicted (p) | the verdict exact on 11/11; a count miss is a finding |
| PR | for each passed build: `resolved` = the before `failing` set; `regressions` = `still_failing` = 0; the after `failing` = 0 | i1, i2, i11 | exact |
| PBF | before cross-check: E's before = the D report's `tier1.failing` (D's report from D's CLI/API on the same inputs) | 11/11 `true` | 100% |
| PU | planted unexpected regressions (i3, i4) are `unreported`, and the verdict is `fail` | 2/2 | 2/2 |
| PT2 | `t2_check` on i1: `wf-cancel-twice` and the scenario claim confirmed; a planted T2 flow claim on `wf-add` (the report edited in a unit test) is `t2_contradicted` | as stated | exact |
| PSB | `spec_backed` unit table: 8 cases (method, route, user, status match or mismatch; missing details gives null) | 8/8 | 8/8 |
| PT3E | `t3_vs_edit` on the Go fixture: 4 synthetic diffs (one file in T3, one outside, a test file, a new file) vs counts by hand | exact | exact |
| PH | refusals: `bad_report`, `report_mismatch` (base byte, candidate byte, inventory hash), `stale_inventory`, `spec_contradiction`, `branch_exists`, protected or checked-out branch, `--work`/`--out` inside the project | 10/10 codes | 10/10 |
| PG | guard on every core run (i1-i11, plus a failing-gate run and a raise mid after-run): owner snapshot (D3) plus a file snapshot of the owner's worktree (sha256, mode, mtime, listing with hidden files, `.git` excluded) and of `.git/{HEAD,index,refs,packed-refs,config,info}`; allowed new entries only (D8) | 0 changed, 0 new beyond the allowed list | all 0 |
| PNM | after a `pass`: `git merge-base --is-ancestor B <owner HEAD>` is false, the owner HEAD is unchanged, and `rebuild/<eid>`'s only new commit has parent `S`, and `S`'s parent is the owner HEAD | as stated | exact |
| PSC | the spec commit `S` changes exactly the 3 spec files (+ `stub-rebuild.json` in test modes), with the trailer `Lattice-Implement: <eid>` | exact | exact |
| PC | cleanup: `git worktree list` after = before + the kept review worktree (passed and gave_up) or + 0 (nothing_to_build, refused); 0 leftover E temp dirs; 0 live processes in E's groups | as stated | all 0 |
| PD | determinism: i1, i3 and i11, 10 processes each with random `PYTHONHASHSEED` and two scratch roots: canonical bytes of the output; scan for `/home`, `/tmp`, `/Users`, `/private`, ISO times, 40-hex commit shas | 10/10 identical; none | 10/10 |
| PBC | byte-compat (see below) | all | all |
| PM | model and key: in stub and oracle mode, a recording `claude` shim first on `PATH` sees 0 calls; in every child environment E starts (gates, runner), `ANTHROPIC_API_KEY` set in the parent is absent | 0; absent | 0; absent |
| PX | wall time per MS change (git + before + gates + after) | 2-8 s (p); full core stage 40-120 s | <= 30 s each |
| PAg-MS | real `claude` agent (sonnet) on i1, i3's candidate (no planted edit) and `bad_invariant`; `max_rounds` 3, USD 3.00 cap in all | i1 `pass` (p 0.8); `bad_invariant` `not_built` or `pass` with `regression` 0; 0 `unreported` | **report-only**; NOT RUN if the CLI is not logged in or hits a usage limit |
| SO | **Streak, oracle** (NOT RUN here): 21 held-out changes, the frozen D engine for the report, `--agent oracle --ref-diff <c>/ref.diff` | rebuild passes round 1 on 19/19 with T1 non-empty; the 2 with empty T1 (c15, c21: empty `ref.diff`) `nothing_to_build`; `resolved` = before failing, exact on 19/19; 8 flow changes (c03: 2, c08: 6) all `t3_may_fail` (D measured T3 `may_fail` recall 8/8), 0 `unreported`, 0 `workflow_fails`; `spec_backed` 2-8 of 8; verdicts: pass 17-19, needs_owner 0-2, fail 0 | `unreported` = 0 and `regression` = 0 on 21/21; `resolved` exact on 19/19; `before.matches_report` 21/21 |
| SO3 | `t3_vs_edit` vs D's PT3 truth (same edits, D's `impact_truth` go/parser tool) | file sets equal per change; micro file recall 0.864 (D measured) | exact at file level; declarations reported |
| SA | **Streak, real agent** (NOT RUN here): 6 changes, the first of each of K1, K2, K5, K9, K10 and K15 by sorted id; sonnet, `max_rounds` 3, USD 12.00 total cap, 1800 s per round | passed 3-6 of 6; on passed builds: `regression` 0, `unreported` 0-1, `needs_owner` 0-2; cost USD 3-10 | **report-only**; a usage limit is NOT RUN / partial k of 6 |
| SL | LEAK on the Streak arm: `lattice-` containers before vs after each change, plus a fault run (the after run raises) | 0 | 0 (NOT RUN here) |
| SG | PG and PNM on every Streak run, on the scratch S clone; the owner's Streak checkout `git status --short` unchanged (pre-existing ` M studio.toml`) | all 0 | all 0 (NOT RUN here) |

**PBC, against `git archive 6a318e5`:**
- *Identical:*
  - `lattice --help`; `lattice spec` usage; `python -m lattice.spec check --help`; `lattice spec impact --help`;
  - D's `lattice spec impact --json` on the 7 minishop fixtures and on the Go implementation fixture (tiers 2 and 3);
  - MCP `tools/list` and instructions in D's and C's configs;
  - the Studio route table, `/api/model`, and a stub rebuild record of `dogfood/studio`'s seed.
- *Empty diff:* the files in D8's last "Never" item.
- *pytest* `tests/spec tests/mcp tests/studio tests/importer tests/gocheck`: only the same pre-existing failure ids as a
  baseline run taken before any edit.
- *`dogfood/studio/check.sh`:* PASS with the same case count.
- *The output report minus `tiers["4"]` and `tier4`:* byte-equal to the input report on every core run.

## 5. Mutants (`dogfood/implement_mutants.py`, on a scratch copy; the control passes first; predicted 16/16)

| id | mutant | killed by |
|---|---|---|
| EM1 | Tier 4 built from the before run only (no after run) | PV i3/i4, PR |
| EM2 | after run on `S` (or the owner HEAD) instead of `B` | PR (`resolved` empty on i1) |
| EM3 | `unreported` flow changes counted as `t3_may_fail` | PU |
| EM4 | the verdict ignores `regression` | unit `regression_fails` (a synthetic after run) |
| EM5 | `git merge rebuild/<eid>` in the owner's repo after a pass | PNM, PG |
| EM6 | `confirm_unsafe_branch=True`, or the spec commit made on the owner's current branch | PG, PH |
| EM7 | the report hash check skipped | PH (`report_mismatch` x3) |
| EM8 | the spec commit uses `git add -A` (the whole project dir) | PSC (on a fixture with an untracked owner file) |
| EM9 | the merge also rewrites `summary` or `tier1` | PBC (report minus T4 byte-equal) |
| EM10 | `nothing_to_build` starts a rebuild anyway, or is reported `pass` | PV i5-i7 |
| EM11 | `t3_vs_edit` counts test files and `stub-rebuild.json` | PT3E |
| EM12 | absolute worktree paths or commit shas in `tier4` | PD |
| EM13 | `spec_backed` matches by route only (ignores user and status) | PSB |
| EM14 | the after-run worktree is not removed | PC |
| EM15 | a contradicted T2 flow claim is not flagged | PT2 |
| EM16 | a `gave_up` build gets an after run on its uncommitted tree and a verdict | PV i8/i9 |

## 6. Risks

1. **Flows are only as good as the capture.** A changed answer on an uncovered route that no verified flow requests is
   invisible to E as well. "0 unexpected regressions" means 0 among the checks and flows that ran, not "none exist". On
   Streak the denominator is the 59 verified flows plus the candidate spec.
2. **`needs_owner` may be common.** D's T3 `may_fail` is broad (352 listings over 21 changes), and the `spec_backed`
   test is narrow (status only: flows record statuses, not bodies). The verdict shifts work to the owner instead of
   guessing. If the oracle arm shows `needs_owner` on most flow-changing changes, that is reported, not tuned.
3. **One reference edit (oracle).** SO measures the machinery on a known-good implementation. SA measures a real agent
   on 6 changes only, with cost caps. Neither proves agent quality in general.
4. **Determinism of after runs:** Streak's nudger `Await` timing (D risk 2). PD runs on MS only. On Streak, a flaky
   flow would look like `flow_changed`. SO runs the before run twice on 3 changes to measure it (report-only).
5. **The detached-worktree view of `Rebuilder`** (R1) relies on `_toplevel` and `rev-parse HEAD` semantics in a linked
   worktree. Rebuild's tests do not cover it. PG and PNM are the guard. If rebuild logic reads the common dir somewhere
   unexpected (for example `_ensure_excluded` with `worktrees = "repo"`), the in-memory `worktrees = "temp"` override is
   the mitigation, and PG detects the write.
6. **MS is small and Python.** It has no T3 (no extract). So `t3_may_fail` and `t3_vs_edit` are tested on synthetic
   reports and the Go fixture, not end to end, until the Streak arm runs.
7. **Cost:** each change costs two extra full check runs (before and after). That is about 70 s on S with 59 flows (D
   measured), on top of the rebuild's own gates.

## 7. What cannot be run here (this container: no Streak checkout, no Docker daemon)

- **NOT RUN here:**
  - SO, SO3, SA, SL and SG (Streak and MySQL 8.4 containers);
  - D's held-out change set (on the owner's machine, under D's scratch);
  - any Docker-backed gate.
- **Run here:** every core row: PV, PR, PBF, PU, PT2, PSB, PT3E, PH, PG, PNM, PSC, PC, PD, PBC, PM, PX, and mutants
  16/16.
  - PBC's `dogfood/studio/check.sh` runs here if it needs no container; else it is marked NOT RUN, with the reason.
  - PAg-MS runs only if the `claude` CLI here is logged in (it is installed at `/opt/node22/bin/claude`, m), within the
    USD 3.00 cap; else NOT RUN.
- A NOT RUN row is never described as passed. E's results first line says which arms ran.

## 8. Blind vs post hoc, and commands

All predictions and pass rules are fixed now. A missed prediction is a **finding**. Fixes are separate post-hoc commits,
rerun with deltas reported. The Streak arm is scored **once**, after the freeze (below), by the orchestrator on the
owner's machine. A fix made after seeing it is never re-scored as blind. PAg-MS and SA run last (cost) and once; a
rerun is reported as a rerun. **Pass:** every gated core row, mutants 16/16 (by probes or named unit tests, disclosed),
and PBC. The Streak rows pass or are honestly NOT RUN; E then passes **core only**, and says so.

```sh
# this container (core arm)
export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
SCRATCH=/tmp/claude-0/-home-user-lattice/307195c7-09d5-5df9-b82b-62010344228d/scratchpad/implement
cd /home/user/lattice
uv run python -m pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck -q -rfE > "$SCRATCH/pytest-before.txt"  # before edits
uv run python -m pytest tests/spec/test_impact_tier4.py tests/studio/test_implement.py -q
uv run python dogfood/implement_probes.py --stage core --scratch "$SCRATCH/p" --out docs/results/data/2026-10-17-implement-verify/core
uv run python dogfood/implement_mutants.py --scratch "$SCRATCH/m" $(seq -f EM%g 1 16)
sha256sum lattice/studio/implement.py lattice/spec/impact_tier4.py dogfood/implement_probes.py > "$SCRATCH/freeze.sha256"   # BEFORE the Streak arm
uv run ruff check <changed .py>; uv run ruff format --check <created .py>
# owner's machine (Streak arm; orchestrator, once; at most 4 changes in parallel, each with its own clone and owner token)
"$DRV" dogfood/implement_probes.py --stage streak --streak-ref a0679e9 --changeset "$D_SCRATCH/changeset" \
    --agent oracle --parallel 4 --scratch "$SCRATCH/s" --out .../streak-oracle
"$DRV" dogfood/implement_probes.py --stage streak --agent claude --subset K1,K2,K5,K9,K10,K15 --usd-cap 12 \
    --parallel 4 --scratch "$SCRATCH/sa" --out .../streak-agent
```

## 9. File ownership (build node)

**Owns (new):**
- `lattice/studio/implement.py` (driver, guards, CLI `main`) and `lattice/spec/impact_tier4.py` (pure);
- `tests/spec/test_impact_tier4.py`, `tests/studio/test_implement.py`;
- `tests/spec/fixtures/implement/` (i1-i11 spec dirs, edits, stub scripts, `gate.py`, `flows.json`, `E_ok.diff`, and
  synthetic D reports with T2/T3 flow items);
- `dogfood/implement_probes.py`, `dogfood/implement_mutants.py`;
- the results doc and its data directory.

**Additive edit:** `lattice/spec/__main__.py`, one `AUTHORING` line (`"implement": "lattice.studio.implement"`).

**Reuses read-only:**
- `impact_report.{build_inputs, read_change, run_tier1, harness_runner, owned_containers, stamp, report_json, _ident,
  verified_flows}`;
- `impact_tier3.GoTree`;
- `implementation.{flow_scenario, route_key, load_inventory}`;
- `rebuild.{Rebuilder, capability, RebuildConfig}`;
- `lint.conflicts`;
- `tests/spec/minishop.py` (sources and `build_db`).

**Not touched:** every file in D8's last "Never" item; `mock_server.py`, `API.md` and the web UI; existing tests,
goldens and fixtures (any edit is a finding); Streak, `job-scheduler*` and D's change set.

The builder makes no commits; the orchestrator stages explicit paths only (never `git add -A`). No API key; model use
only via the `claude` CLI, inside the rebuild loop, in PAg-MS and SA.

**Frozen inputs at prereg time (sha256 at 6a318e5; any change before the results is a finding):**
```
ddb005b594e20acbf61fd228d15b73d7b5c331d6eba392999509d0b0de3a7cb7  lattice/studio/rebuild.py
e4b11ec2d0a3082f6e5d9325e6a2d44cb72a55c9838510075572cbc7d1d72522  lattice/studio/areas.py
01a8df5522e5498d02c64c7e07a204bfa09149086fedcd67a9329c36edb515c1  lattice/studio/server.py
428d8d2321afb90209e76af9686ecd720d8cb9a67f8e5bd17df2bff960088ad0  lattice/studio/state.py
44ea3dad50791af04d04264481a768fc03a9331e1f9f98dfc9aa5a4e6ac3b2c6  lattice/studio/session.py
a3e11f1c266a813a66e33d8943b8773705a419ce8dde806615203d750ae0137c  lattice/spec/impact_report.py
6f86fc6cfb2b7f2a6e485633d810e55782fdfbcfdce71921bff8f9cff560a90a  lattice/spec/impact_tier2.py
22228513beba44fcff4b0d5ab48c662d2010e7ac320b73ac860f134ffb6e2faf  lattice/spec/impact_tier3.py
68f13b288cde4a3fa348412495ba560815e003b342ea9cca90b1a779c1bb525e  lattice/spec/check.py
29ded6ecbbddcc55a5d94914cf9bce82fb9ee61a10d3453d70c336565c6090e6  lattice/spec/incremental.py
7012db6e907e9b853402ed6a433b6690e64fd7b82df37ec96ccd0df647a6d2e6  lattice/spec/implementation.py
7c0833aa945e2d0a2596e53881c37484001a7ee9592dc5d317efc7b03523423c  lattice/mcp/tools.py
701f17f333ec918ffadf79904b2c9926a9026a1a1f34c6402ea2083d4172c796  lattice/spec/appmap.py
c6f43564156741afefdb8ac143d658b0ddb0e615d7caa0ca21726772b408797d  lattice/spec/impact_ask.py
90117594e10c61272b8896bfc631623c5d23c4689d8e1853649cf73810baccfd  tests/spec/minishop.py
```
Before the Streak arm, the orchestrator records `freeze.sha256` of the three E files above.

## 10. Questions for the orchestrator

- **Q1 (R1).** Change mode on a detached spec worktree (recommended: it uses the existing loop unedited, and its pass
  criterion is the project's gates), or scoped mode over the candidate's rules?
- **Q2 (R4).** Should a `t3_may_fail` flow change without `spec_backed` be `needs_owner` (recommended), or count as an
  unexpected regression (`fail`)?
- **Q3 (R5).** Approve the probe-only `oracle` agent mode for the Streak arm (it reuses D's `ref.diff`s and needs no
  model)?
- **Q4.** The Streak arm needs the owner's machine: D's change set, MySQL 8.4, S. Run it in the same node after the
  core freeze, or as a separate orchestrator step? Budgets: SA USD 12.00 and PAg-MS USD 3.00. Approve?
- **Q5.** Does Tier 4 in F's Impact view stay out of scope this round (R3)? The view keeps its fixed "Tier 4 · not run"
  heading.
- **Q6.** Is `rebuild/<eid>` (rebuild's own naming) acceptable beside `implement/<eid>`, or should the build branch be
  `implement/<eid>-build`? The second needs a `branch` argument that change mode does not take, so it would need a
  rebuild edit, which this pre-registration avoids.

## Amendment 1 (orchestrator rulings, 2026-10-17; defaults, overturnable by the owner before any gated run)

- Q1: drive the rebuild loop in change mode on the detached spec worktree. Q2: a flow that changes on a route D only listed as `may_fail`, with no candidate step backing the new answer, is `needs_owner`, not `fail`. Q3: a test-only `oracle` agent mode that replays D's reference diffs and calls no model is approved; it is never reachable from Studio or the CLI default. Q5: Tier 4 stays out of the Studio Impact view this round. Q6: `rebuild/<eid>` is accepted.
- Q4: the Streak arm (SO, SO3, SA, SL, SG) is NOT RUN in this container and is a separate orchestrator step on a host with Streak and Docker. The USD 12 and USD 3 real-agent budgets are NOT approved by this amendment; they need the owner's yes before any model run.
- The build node implements and gates only the Docker-free core (17 probes); it reports the Streak arm as NOT RUN, never skipped silently.

## Amendment 2 (orchestrator ruling, 2026-10-18, before any Streak run)
The frozen runner `dogfood/implement_probes.py` is core-only (the 2026-10-17 results section 9 says so). The Streak arm
(SO, SO3, SA, SL, SG) and the real-agent probe PAg-MS therefore need **a new runner file,
`dogfood/implement_probes_streak.py`, separate from the frozen files**, written by a build node from section 5 of
`docs/results/2026-10-18-implement-verify-streak-arm-results.md` (the spec of the missing scorers), frozen (sha256
recorded in `docs/results/data/2026-10-18-implement-verify-streak-arm/freeze.sha256`) **before any Streak run**, and
then run once by a separate run node. The frozen core runner, the engine files and the frozen core results are not edited.
The real-agent budgets are approved by the owner as registered (USD 12 for SA, USD 3 for PAg-MS), hard caps; a usage limit
is NOT RUN, never a pass. This is a departure from "run the frozen runner unchanged" because that runner has no code for
these rows; it is stated, not hidden.

## Amendment 3 (orchestrator rulings on the Streak-arm runner's ambiguities, 2026-10-18, before any Streak run)
The runner `dogfood/implement_probes_streak.py` (sha256 recorded in `docs/results/data/2026-10-18-implement-verify-streak-arm/freeze.sha256`)
is frozen. Rulings on its 11 resolved ambiguities: (1) c15 and c21 (empty `ref.diff`) run through the claude path behind a
logging shim and are expected to end `nothing_to_build` with no agent call: accepted. (2) The `post-checkout` hook building
`bin/streak-api` in the scratch repo is accepted (Streak sources unchanged). (3) The default gate is the owner's `./check.sh`
(all layers), `round_timeout` 1800 and `gate_timeout` 2400: accepted; a B/X failure is reported as such. (4) The driver venv
is the children's environment: accepted. (5) SO "resolved exact" is the multiset comparison on (layer, rule, case, endpoint):
accepted. (6) SO3 is file-level against D's truth; declaration rows are report-only: accepted. (7) SO-rep repeats the whole
pipeline: accepted. (8) The SL fault method (`sitecustomize` raising in the after run, default change c14): accepted. (9) The
USD 12 (SA) and USD 3 (PAg-MS) caps are TOTALS on the CLI's list estimate: accepted. (10) **Engine drift:** the engine version
that counts is the tree the runner runs on (the `next-phases` tip merged into this branch), not the pre-merge hash list;
the runner reports the five engine files that differ from 6a318e5 and D's own freeze mismatches, and they are stated in the
results as drift, not hidden. (11) SG scope as built: accepted. The two synthetic smoke runs (c90, c91) are disclosed and
excluded from every scored value. The run is scored once.
