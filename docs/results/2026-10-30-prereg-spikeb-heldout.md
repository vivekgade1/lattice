# Spike B held-out: the dynamic workflow layer on an unseen Go app with a different check shape: pre-registration

**Status: REGISTERED, NOT RUN; needs owner approval of the budget.** Written 2026-10-31 by node T2P of the 2026-10-30 agent graph
(branch `nc/t2p-preregs`, from the integration tip 3cc77f7). No model was called and no runner exists yet. The author cap in section 9 is
the owner's decision; no figure here is approved.

## 1. Why, and what stands

Spike B (the workflow layer: flows and state machines of a Go Gin + GORM app, planned statically and run against the app) has had two rounds:
round 1 (`docs/results/2026-10-15-workflow-layer-prereg.md`, results `docs/results/2026-10-15-workflow-layer-results.md`) with the six J apps as
held-out, and round 2 (`docs/results/2026-10-16-workflow-layer-round2-prereg.md`, results `docs/results/2026-10-16-workflow-layer-round2-results.md`),
a development round on the same apps. Round 2's own risk 1 said a blind claim needs an unseen app with a **different check shape** (a Makefile
target, `pyproject` scripts, a harness under a subdirectory), and that those shapes were not handled.

GD (commit f3de391; `docs/results/2026-10-30-go-workflow-body-diagnosis.md`, merged into the integration tip from branch `nc/go-ga2`) found why the
J flows died at step 0 and fixed three causes in the extractor: bodies decoded by module helpers or JSON decoders are now read
(`lattice/gocheck/src/goextract_body.go`), body fields the handler refuses when absent are sent (workflow opt-in key `param_required`, read by
`lattice/importer/workflows.py`), and per-handler authentication makes a route Member (`lattice/gocheck/src/goextract_assemble.go`). On the J apps
(dev data) the flows that run went from 7/7/5 to 41/26/19, verified from 2/2/2 (all refusal-expecting `guard` flows) to 16/19/9, and step-0 deaths
from 5/5/3 to 0/0/3. **All of that was measured on apps read to find the causes.** This round asks whether it holds on an app nobody has read.

**Discovery today** (`lattice/importer/check_invocation.py`): a `studio.toml` `[app]`, a `python -m lattice.spec check` line in a `[rebuild]`
gate file or the root `check.sh`, or a root `harness.py`; nothing else. A Makefile target or a `pyproject` script is **not read**; such an app
runs static with a reason that lists the sources looked for.

## 2. Question and hypotheses

**Question.** On an unseen Gin + GORM app whose check is a Makefile target that runs the checker from a subdirectory holding the harness, does
the layer (a) find the harness, and (b) recover and verify the app's multi-step flows and its state machine, without crashes, failing replays,
unbound path parameters or nondeterminism?

| id | hypothesis |
|---|---|
| HB-DISC | With the tree as frozen, discovery does **not** resolve the Makefile shape (static, reason stated); it does only if the optional stage D0 (section 4) is built and frozen first |
| HB-FLOW | GD's body and auth readers generalise: most flows get past step 0 and some non-guard flows verify |
| HB-SAFE | Replays, unbound-parameter rule, determinism, no-write and teardown hold as on Streak and J |

## 3. Data status (binding)

Development data, never the basis of a blind claim: **HO5**, **SG**, **S4**, the six **J** apps (`~/Documents/job-scheduler-go` and its five
siblings), the **chat round-2** questions, **D1**, **H1**, **H2**, **Streak** (every revision, including the round-1 S corpus a0679e9), **Cyclops**,
the job-scheduler spec and the `tests/gocheck/fixtures/bodybind` fixture. Only the app of section 6, written after the freeze, backs a blind row.

## 4. Design

**Optional stage D0 (owner's choice, before the freeze; code work, no model spend).** Extend discovery to read a Makefile target's recipe
lines (with a `cd <dir> &&` prefix resolving references relative to that directory), tested on hand-written fixtures only, never on the
held-out app. If D0 is not built, B-DISC is reported (predicted static) and only arm B-EXP below carries the flow rows. The decision is
recorded in a dated amendment before the freeze; it cannot be taken after the app exists.

**Arms (deterministic; no model call in any scored run).**
- **B-AS**: `lattice import workflows <app copy> --out <dir> --user <h1> --user <h2>` on a `git archive` of the app as delivered (discovery decides).
- **B-EXP**: the same, on a scratch copy to which the runner adds a `studio.toml` `[app]` built **only** from the author's sealed
  `HARNESS_REFS.json` (section 6). This isolates flow recovery from discovery. If B-AS also runs dynamically, its `workflows.json` must equal
  B-EXP's except the `run.harness*` keys (gate B-CONS).
- Database SQLite (the layer's default without a `--database` in the invocation); users the spec's `USERS` handles in the order listed.

**Truth.** The author's Lattice spec, read by the round-1 scorer unchanged: `spec_truth` and `score_held_out` in `dogfood/workflow_probes.py`
(multi-route scenario skeletons, path-parameter slots, the machine's states, edges and initial states).

**Definitions (fixed now).**
- A truth skeleton is **recovered** when at least one flow in `workflows.json` (any status) matches it under round 1's R4 lenient rule
  (`lenient_match`: equal after dropping some setup steps); the strict count (`strict_match`) is reported beside it.
- A flow is **verified** when its status is `verified` **and** the independent replay (a separate process rebuilds its Scenario and runs
  `check(layers="D")` on a fresh database, with harness references hard-coded from `HARNESS_REFS.json`, never from discovery) passes.
  `guard` flows (they expect a refusal) are counted apart: they show that a refusal reproduces, not that a lifecycle works (round-2 finding 4).
- **Verified non-guard** = verified flows of kind `pair`, `owner_guard`, `effect` or `lifecycle`. **Verified fraction** = verified non-guard / flows run.
- A **step-0 death** is a failed flow whose first sent step answered a 4xx (GD's metric).
- **Determinism stage:** 10 static runs plus 1 from a second copy path, and **10 dynamic runs** of B-EXP, on fresh copies, one calendar day.

## 5. Gates (fixed now; sources named per row)

| id | rule | source |
|---|---|---|
| B-CRASH | 0 crashes over all runs | round 2 PJ |
| B-PR | exactly 0 failing independent replays of verified flows | round 1/2 PR |
| B-PU | exactly 0 sent requests with an unbound path parameter | round 2 PU (R2-D4) |
| B-DET | static 11/11 and dynamic 10/10 byte-identical; no `/Users`, `/private`, port or ISO time outside values | round 1/2 PD |
| B-PW / B-PT | 0 changed or new files in the app archive and author directory; 0 leftover app processes or `lattice-wf-*` containers | round 1/2 PW, PT |
| B-VER | verified non-guard flows >= 3 **and** verified fraction >= 0.20 | round 1 H range, low end (0.20) |
| B-STEP0 | step-0 deaths <= 0.25 of flows run | new (GD's claim) |
| B-SLOT | path-parameter slot recall >= 0.50 | round 1 H range, low end |
| B-SKEL | lenient structural recall >= 0.10 of the truth skeletons | round 1 H range, low end |
| B-MACH | machine states recall >= 0.60 and static edges recall >= 0.20; observed edges reported | round 1 H range, low ends |
| B-CONS | B-AS equals B-EXP except `run.harness*` (only if B-AS is dynamic) | new |
| B-DISC | B-AS resolves the Makefile shape (mode `observed`, source named) | gated only if D0 was built; else reported |

Round 1 registered the J ranges as report-only predictions; this round makes their low ends gates, which is stricter and is stated here
before the app exists. A gate over an empty set (no truth skeletons, no verified flow to replay) is **not measured** and fails; it never passes vacuously.

## 6. Author and isolation protocol

- **One new agent** (not a J author, builder, verifier or this file's writer), `claude` CLI only via `env -u ANTHROPIC_API_KEY`, **sonnet**,
  dispatched after the freeze commit, in the fixed empty directory `~/Documents/b-heldout-author` (new), with the H2 flag set (no MCP server,
  `--setting-sources ""`, fixed session id, Bash/Read/Write/Edit/Glob/Grep only; `docs/results/2026-10-23-dotnet-h2-prereg.md` section 9.1).
- **Brief** (`BRIEF.md`, new, frozen): a Go Gin + GORM service over SQLite (buildable with the module proxy) with >= 12 routes, >= 8 of them
  taking JSON bodies, >= 2 users of different roles and a login route, one table whose status column has >= 4 states and >= 3 moves made by
  routes (not only by a background job); a Lattice spec (harness API note supplied) with >= 8 multi-route scenarios, >= 4 of which carry an id
  from one response into a later request; a `Makefile` whose `check` target builds the server and runs the checker from a subdirectory `qa/`
  that holds the harness; no `check.sh`, no `studio.toml`, no root `harness.py`; `FILES.txt`, `freeze.sha256`; and, outside the app,
  `sealed/HARNESS_REFS.json` (references importable with the repository root on `sys.path`) and `sealed/IDIOMS.md` (how bodies are read, how
  authentication is decided, where the schema lives). The brief names **no** idiom (no decoder, binder, helper, middleware or validation style),
  no domain of an earlier app (job scheduling, habits, marinas, notes, tool rental, help desks, kilns, beekeeping, image viewers), and nothing of
  this file. `make check` must pass before the author stops.
- **Audit:** `scripts/audit_transcript_generic.py <transcript> --root ~/Documents/b-heldout-author --forbid ~/Documents/job-scheduler --forbid
  ~/Documents/g5- --forbid ~/Documents/cyclops`; its built-in forbidden prefixes cover the Lattice checkouts, `~/.claude`, Streak and the J apps. One forbidden or outside item voids the app after the dispatching node reads the
  call; review items are listed, not voiding. A voided app is never scored.
- **Contract check** (orchestrator, once): `make check` passes on a `git archive`; the spec loads; the harness references in `HARNESS_REFS.json`
  import. One fix round with the failing printout only, disclosed.

## 7. Predictions (before any runner exists)

| row | prediction | p(pass) |
|---|---|---|
| B-DISC without D0 / with D0 | static, reason lists the 4 sources / resolves | 0.05 / 0.75 |
| B-CRASH | 0 | 0.85 |
| B-PR | 0 failing | 0.9 |
| B-PU | 0 (unbound parameters fail and name themselves) | 0.9 |
| B-DET | identical; risk: time-derived values in bodies | 0.8 |
| B-PW / B-PT | 0 / 0 | 0.95 |
| B-VER | verified fraction 0.05-0.60 (point 0.25), non-guard verified 0-15 (point 5) | 0.45 |
| B-STEP0 | 0.05-0.60 (point 0.20); risk: a body idiom GD does not read, validity predicates (GD left them) | 0.5 |
| B-SLOT | 0.40-1.00 (point 0.75) | 0.75 |
| B-SKEL | lenient 0.00-0.50 (point 0.20), strict lower | 0.55 |
| B-MACH | states 0.5-1.0, static edges 0.2-1.0 | 0.6 |
| all gated rows of B-EXP | | 0.10-0.20 |
| the app is scored at all (no void, no cap stop) | | 0.7 |

## 8. Runner (new; after this prereg, before the freeze)

`dogfood/workflow_probes_ho.py` (new) imports `spec_truth`, `score_held_out`, `strict_match`, `lenient_match` and `flow_matches` from
`dogfood/workflow_probes.py` unchanged, archives the app (never a checkout), runs B-AS, B-EXP, the determinism stage and the replays, and
writes one append-only ledger with the H2 refusal rules (scored once; a rerun only after `crash_before_metric` with unchanged bytes; verify once).
`--out` and `--scratch` are required (`docs/design/templates/blind-round-runner-section.md`). Self-test on a stand-in: a J app copy with a
hand-written Makefile wrapper (dev data, never the ledger).

## 9. Budget (the owner's decision; **nothing approved**)

| item | model | cap | note |
|---|---|---|---|
| author (dispatch + one fix round) | sonnet | **USD 3.00** | H2's author cap; P(cap hit) about 0.3 (app + spec + harness + Makefile) |
| D0 (optional) | none | USD 0 | code node |
| scored runs, determinism, verify | none | USD 0 | deterministic layer, local app |
| **round ceiling** | | **USD 3.00** | |

**Stop rule:** a call whose result is not `success` or whose cost reaches the cap stops the round; nothing is re-dispatched; the owner decides.
A usage limit is the same kind of stop. One `spend.jsonl` line per call; never an API key.

## 10. Freeze list and ordering

**Order (binding):** this prereg committed alone -> (D0 with its fixture tests, if chosen, and an amendment saying so) -> runner and self-test ->
`BRIEF.md`, harness API note, dispatch script -> freeze file + empty ledger committed -> author -> audit -> contract check (one fix round) ->
inputs manifest committed -> one scored run -> independent verify -> results. No amendment after the freeze file exists.

**Freeze file** `docs/results/data/<run date>-spikeb-heldout/freeze.sha256` (new): this prereg; the new runner and `dogfood/workflow_probes.py`;
`lattice/importer/workflow_run.py`, `lattice/importer/workflows.py`, `lattice/importer/check_invocation.py`, `lattice/gocheck/src/goextract_body.go`,
`lattice/gocheck/src/goextract_workflow.go`, `lattice/gocheck/src/goextract_closure.go`, `lattice/gocheck/src/goextract_assemble.go`; the brief
and dispatch script; `scripts/audit_transcript_generic.py`; every tracked file under `lattice/` (must-list). **Inputs manifest:** the app archive,
`HARNESS_REFS.json`, the spec and its sha256. Every entry tracked and committed.

## 11. Risks, and what this cannot show

1. **n = 1 app, one check shape** (Makefile + subdirectory); `pyproject` scripts stay untested.
2. **Discovery and flows are separated on purpose**; a B-EXP pass with a B-DISC miss means "flows generalise, discovery does not".
3. **The spec bounds the truth.** As round 1 found on Streak, scenarios that start from given rows match few request-built flows; the brief's
   multi-route minimum limits that, it does not remove it.
4. **Values the extractor does not model** (formats, validity predicates) can fail flows after step 0; those are findings, never tuned.
5. **Time.** Dynamic determinism holds within one calendar day only; the stage runs in one day.

## 12. Launch checklist

1. Owner approves the author cap (USD 3.00) and decides D0 (build or not), in a dated amendment committed before the freeze.
2. Runner self-test passes on the stand-in; a refused launch writes no ledger line.
3. Freeze and empty ledger committed; from a fresh clean worktree (`git status --ignored --short` empty) `--verify-freeze-only` passes.
4. `~/Documents/b-heldout-author` absent or empty; dispatch; audit; contract check; inputs manifest committed.
5. One scored run with explicit `--out` and `--scratch` (B-AS, B-EXP, 10 + 1 static, 10 dynamic, replays); Docker not required.
6. Independent verifier re-runs `--kind verify` from a second clean worktree; any differing verdict reported.
7. Results: every row and prediction scored once; misses localised in a separate post hoc section; J and GD numbers kept as dev rows.
