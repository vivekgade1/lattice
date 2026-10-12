# Spike 3 pre-registration: a self-improving Lattice from logged usage signals

Date: 2026-10-12. Branch `np-spike3` (worktree `lattice-np3`, from `next-phases` 396bb91).
Written BEFORE any probe is run and before the code exists. The criteria and predictions below
are blind: a failed prediction is a finding, reported as such in
`docs/results/2026-10-12-spike3-results.md`, never silently fixed. Post-hoc observations are
reported separately from the blind ones.

Read first: `docs/design/spikes/03-self-improving-lattice.md`, `docs/results/2026-10-12-i-r-improvement-ranking.md`
(I-R: the method, 26 items, the "Gaps for automation" table), `docs/design/next-phases-plan.md`
(defaults in force: per-project log; proposals target modules and docs, not the checker, first;
humans merge).

## What is built (summary; the results document has the detail)

1. Studio closes I-R gaps 1, 2, 3, 5, 7 (items 1-5 and 7-8 of the task): `layer_seconds` on gate
   runs, a machine `cause` on every give-up/stop/failed node, human interventions as timeline and
   log events (`actor: "owner"`), the ledger line's `wall_s` = the node's own wall, a `signal_key`
   on every finding; an improvement ledger (`improvements.jsonl`, gap 8).
2. `lattice/improve/events.py`: an append-only JSONL event log `<state dir>/events.jsonl`.
3. `lattice/improve/analyze.py`: the I-R ranker (no LLM): frequency x cost, seconds first, then
   list-price dollars, never converted; frequency only for the rest; closed items out; at most N
   proposals; protected targets `needs_owner`.
4. CLI `python -m lattice.improve report|import-plan|ledger` and `lattice improve ...`.
5. `GET /api/improve` (API v1.15), read-only; no web UI in this spike.

## How the ranker will read the old plan records (decided before the replay)

`import-plan` turns a plan record into events with only the fields it has:

- `gate_run` per level run with a gate (`level_runs[].gate.gates[]`): role `level`, mode
  `full` when the command is the bare first gate, `regression` when it carries `--only-file`;
  seconds; no `layer_seconds` (the records never had them: I-R gap 1). Cluster key
  `gate:<role>_<mode>:total` when a gate run has no per-layer seconds, `gate:<role>_<mode>:<layer>`
  (one occurrence per layer, cost = that layer's seconds) when it has them.
- `gate_run` per start baseline: the record's `baseline` (seconds as recorded), and the earlier
  baselines that survive only as timeline lines ("start baseline on B at SHA: N failing"), their
  seconds derived as the gap from the preceding "resume N started" line (marked `derived`).
  The ranker (not the importer) marks a baseline whose sha already had a gate run earlier in the
  log as a re-run of a known tip: cluster `gate:baseline_rerun:total`; other baselines
  `gate:baseline:total`.
- `node_ended` per build end found in the timeline ("node X gave_up (bN, k round(s))", wall from
  "node X started as bN"), else per node from its fields. A plan record carries no cause (I-R gap
  2), so a give-up from an old record gets `cause: "other"` (detail "not recorded"), a node
  `stopped` in a `stopped` plan `stopped_by_owner`.
- `human` events: `approve` (the `approved` time), `resume` per "resume N started" line.
- p2.json is an earlier snapshot of the same plan as p2-final.json: not imported (importing
  it would count p2's level 0/1 gates twice). Import is idempotent (deterministic event ids).

The closed items of I-R (I-05 L012, I-06 await cap, I-07 scoped/regression gates, I-08 spec
sync, I-09 stop hygiene, I-23 cache tokens) go into the replay's `improvements.jsonl` under the
signal keys the fixes address: I-05 `cause:spec_unsatisfiable`, I-06 `gate:node*:D`, I-07
`gate:level_full:*`, I-08 `human:spec_sync`; I-09 and I-23 have no signal key (no event kind
records them).

## Probes and pre-registered criteria

### I-R replay (blind)

Import `docs/results/data/streak-plans-2026-10-11/p2-final.json`, `p1.json`, `p3.json` with
`import-plan`; write the closed items above to the ledger; run `report --top 5`.

**Criteria (fixed by the task):** the ranker's top 3 contain layer X at level gates AND the
first-baseline cost, i.e. at least 2 of I-R's hand-ranked top 3 open items (I-01 layer X at
level gates, I-02 first-baseline reuse, I-03 container per gate); and none of its top 5 is an
item I-R lists as closed.

**Hypothesis (my prediction, computed by hand from the records before any code ran):**

| # | cluster | f | c (s) | score (s) | I-R item |
|---|---|---|---|---|---|
| 1 | `cause:stopped_by_owner` (p3 account) | 1 | 2709.2 | 2709.2 | I-05 (closed; I-R's "very likely" attribution) |
| 2 | `gate:baseline_rerun:total` (p2 resume-2 416 s derived, p3 426.35 s, both at 941735104c gated by p2 level 1) | 2 | 421.2 | 842.4 | I-02 (I-R counted only p2's: f=1) |
| 3 | `gate:level_regression:total` (137.5, 138.92, 143.71, 145.32, 150.89) | 5 | 143.3 | 716.3 | I-01, without the X attribution |
| 4 | `cause:other` (p2 account b4 309 s, sync b11 325 s) | 2 | 317.0 | 634.0 | I-05 (closed) |
| 5 | `gate:baseline:total` (p2 resume 3 146 s, resume 4 150.8 s) | 2 | 148.4 | 296.8 | none (I-R excluded these as unavoidable) |

(`gate:level_full:total`, 2 x 423.2 = 846.3 s, is I-07 and is closed by the ledger.)

Predicted outcome: **"at least 2 of I-R's top 3": pass on identity** (I-02 at #2, I-01 at #3), but
**"layer X at level gates": fail**: the records hold only whole-gate seconds, so the ranker can
name the level gates, not layer X. **"None of the top 5 closed": fail**: #1 and #4 are the events
I-R attributes to the closed I-05, and an old record cannot say their cause. I-03 is not
predicted to appear (container start is in `timings.setup`, not in any record). If the live
events of I-3 (which do carry `layer_seconds`) are added, the X cluster is predicted to appear;
that is post hoc and reported as such.

### I-2 accounting (blind)

A stub plan on the dogfood seed (`dogfood/studio/project_seed`, copied as
`tests/studio/seed.py` does) with three changes to the copy, none to Studio's behaviour: the
gate writes a `lattice spec check --json`-shaped report (`[rebuild] findings_json`) with
`timings.layers` and per-finding `details.problems`; for the unsatisfiable scenario the
problems carry the "spec problem: ..." line produced by `lattice.spec.preconditions.unestablished`
on a real unsatisfiable `Scenario`; a `metrics_hook` appends the ledger line. Scope
`rules R1, R3, R4` (seed: `w-broken` gives up after three rounds, sibling `r-ok` passes), with
holds so the level lasts longer than `r-ok`'s own build.

Prediction: the forced give-up's cause is **`spec_unsatisfiable`** (the spec-problem line is in the
last round's findings and takes precedence over `no_progress`, which also holds: the same failing
set three rounds running).

**Pass, in 3 of 3 runs, all four:** (1) `gate_run` events carry `layer_seconds` equal to the
report's `timings.layers`; (2) `w-broken`'s `node_ended` has `cause: "spec_unsatisfiable"`, and so
do its plan node record and its build record; (3) `human` events `approve` and `run` with
`actor: "owner"` exist (and the plan timeline entries carry `actor: "owner"`); (4) `r-ok`'s
`node_ended.wall_s` equals its build record's own `usage.wall_s`, the ledger line's `wall_s` equals
it too, and it is smaller than the level's wall.

### I-3 one improvement end to end, n=1 (labelled n=1)

Take the ranker's top open proposal about layer X at level gates; its evidence must be logged
`layer_seconds`. Logging: 3 level gates ("before") on a scratch clone of Streak
(`git clone --no-hardlinks /Users/vivekgade/Documents/streak <scratch>/streak-imp`, checkout
a0679e9, `UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/streak/.venv`,
`PYTHONPATH=<clone>:/Users/vivekgade/Documents/lattice-np3`) run through Studio's own gate code
(`Rebuilder.gate_round` on an integration worktree, the level-gate command built by the same
function `_merge_level` uses: first gate + `level_gate_args` + `--only-file` with every scenario
that passes at a0679e9). MySQL: one container `lattice-imp-mysql` (same image and arguments as
`lattice/spec/engines/mysql.toml`), passed as `LATTICE_MYSQL_URL` so no `lattice-mysql-*`
container is started; removed at the end. Then the change the proposal names, in the clone's
`studio.toml` only (`level_gate_args = ["--x-repeats", "2"]`; the owner's merge into the real repo
is NOT done), and 3 "after" level gates.

The proposal states its expected saving from the logged signal alone: X seconds x (1 - 2/r), r the
X repeats the logged gate ran with (Streak's `check.sh` passes `--x-repeats 5`), i.e. a 60% drop.

**Hypothesis:** X drops by 40-60% (the earlier hand measurement: 120.0 s to 58.9 s, -51%).
**Pass:** the direction is right (X after < X before, every run) and the measured mean saving is
within 25% of the predicted saving: |measured - predicted| <= 0.25 x predicted. The full gate's
seconds and the other layers are reported too (coverage trade: fewer X tries at level gates; the
final full gate is unchanged).

### I-4 refusal (blind)

Two synthetic event sets: (a) top cluster "the L012 lint is too noisy" (`finding_class`
`lint:L012`, with seconds), whose proposal targets `lattice/spec/lint.py` and
`lattice/spec/preconditions.py`; (b) top cluster a slow layer C at level gates
(`gate:level_regression:C`), which has no configuration knob, so its proposal targets
`lattice/spec/check.py`. **Pass:** (a) is a normal proposal (`status: "open"`), (b) is
`status: "needs_owner"` and carries no draft; both in `tests/improve/` and in one CLI run.

## Not tested here

Owner agreement with any ranking (I-R's open exit), a second project (n=1 project), recurrence
over time, human time as a cost, the cost of risk/correctness items.
