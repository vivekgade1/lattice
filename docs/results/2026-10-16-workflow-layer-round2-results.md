# Workflow layer, round 2: results (development set; builder run)

Pre-registration: `2026-10-16-workflow-layer-round2-prereg.md` (with Amendment 1). Data:
`docs/results/data/2026-10-16-workflow-layer-round2/` (`probes.json`, `summary.md`, `ph.json`, `run.log`,
`freeze.sha256`, `workflows*.json`, `mutants/`, `pytest/`, `spike-a/`). Branch `np-b2` from the pre-registration
commit a4d3f6a. Streak a0679e9; job-scheduler apps at 00dd1de / 7d1a730 / f12365b / b8d9fe0 / 00cbf55 / 7ac55d5;
spec job-scheduler ef17cf8 `spec.tickets:t5`. **Every J number is a development-set number, not blind.** The
builder read only `check.sh` and `harness.py` of the three harness apps (and the stage outputs); no `spec/`, `cmd/`,
`internal/` or `.go` file of any J app was opened.

Stage run: `--static-runs 10 --runs 10 --j-runs 10 --j-mysql go --spike-a` (the full registered counts), 9 min 56 s.
Mutants WR1-WR11 and WF1-WF13 (28 min 37 s). 25 PH fixtures, 51 fixture files.

## Gated rows

| probe | predicted | observed | |
|---|---|---|---|
| PH | all exact | 5 apps + 3 harness-less + 25 fixtures exact; `-go`/`-min-spec-*` resolve through `check.sh` to sqlite, services {scheduler, worker} / {} | pass |
| PL | 69 flows; 58 / 5 / 6; `GET /v1/nudges`; 0 `/v1/home` bindings | 69; 58 / 5 / 6 (0.841); list routes {GET /v1/nudges: 5}; 0; S' equal to S | pass |
| PL1 | byte-identical to round 1 minus 4 keys | byte-identical (68 / 59 / 1 / 8); static also equal to round-1 static | pass |
| PU | exactly 0 | 0 on S, S', go, mso, mss | pass (but see finding 2) |
| PE PK PM PS PF | as round 1 | 11/11; K1 K3 verified, K2 failed; Nudge.delivery exact, 0 moves, `static`; lenient 3/20 strict 1/20; 0.841 / 0.072 / 0.087 | pass |
| PR | 0 failures | S: 58 scenarios, 0; go, mso, mss (refs hard-coded from check.sh): 2 scenarios each, 0 | pass |
| PG | S 100% | S: route 21/21, handler 21/21, binding 113/113, status 20/20, param_use 150/150. J: **NOT RUN** (would open .go files, R2-D7) | pass (S) |
| PJ | mode observed, harness check.sh 3/3, crashes 0/3 | 3/3, 0 crashes | pass |
| PJs | 3/3 identical | 3/3 identical to round 1 minus the new run keys | pass |
| PD | 11/11, 10/10, 10/10 x3 | 11/11, 10/10, 10/10 x3, no leaks | pass |
| PW | 0, 0 | archives 0/0 (S + 6 J); real checkouts (streak, 6 J, job-scheduler) porcelain and listing unchanged | pass |
| PT | 0 | 0 after 15 runs; SIGINT mid-flow on S and on `-go`: 0 containers, 0 apps | pass |
| PB | all | extract 4/4 = a5fed2e and committed Spike A; gocheck; diff (owned files only, frozen empty, `score_held_out`/`spec_truth` byte-identical); lint; Spike A probes pass; pytest (see below) | pass |
| PX | S static <= 5 s, dynamic <= 30 min; J <= 10 min | 0.27 s; 31-35 s; J 1.4-5.0 s per run; J-my 18.2 s | pass |

## PJ (report-only values) vs predictions

| app | flows | verified / failed | slots | skeletons lenient / strict | JobRun | observed edges | sweeps |
|---|---|---|---|---|---|---|---|
| `-go` | 7 (80) | 0.286 (0.20) / 0.714 (0.70) | 16/24 | 0 / 0 | 5/5, 3/5, exact; `static` | 0/5 | none |
| `-min-spec-opus` | 7 (74) | 0.286 (0.50) / 0.714 (0.40) | 21/24 | 0 (3) / 0 | 5/5, 3/5, exact; `static` (pred `observed`) | 0/5 (pred 1) | none |
| `-min-spec-sonnet` | 5 (15) | 0.40 (0.50) / 0.60 (0.40) | 21/24 | 0 (3) / 0 | 3/5, 1/5, exact; `static` | 0/5 | none |

J-my (`-go --database mysql`): same flows and statuses as the SQLite run, 0 containers left, 18.2 s.

## Findings (differences from the predictions; none fixed)

1. **J flow counts and recall collapse because every job-creating flow dies at step 0.** `POST /applications`
   and `POST /users` are sent with body `{}` (the extractor reports no body params for these routes, also in the
   round-1 static output) and answer 422. `-go`: `{'detail': [{'msg': 'name must be 1-100 characters'}]}`;
   `-min-spec-opus`: `Field required` for `name` (and `email`, `password` for users); `-min-spec-sonnet`:
   `{'detail': 'invalid name'}`. With no repairable field the step cannot be repaired, the flow fails, and failed
   flows with the same final id merge (round-1 dedupe), so 80 / 74 planned flows report as 7. Skeleton recall is 0
   and the JobRun machine is never reached. This is an extractor/value miss (R2-D7): a finding, not a fix.
2. **`-min-spec-sonnet`: every route is extracted as Public** (round-1 static output already shows caller None
   on all steps), so the steps are sent unauthenticated and answer 401 (`Not authenticated`). The login itself
   works (checked directly on the harness: token obtained, an authenticated GET /applications answers 200 and a POST 403 for ann on `-min-spec-sonnet` itself).
   Extractor miss, a finding.
3. **PU is vacuous on J.** No flow reaches a step with an unbound path param, so `-go` has no failed reason
   naming `job_id` / `user_id` (predicted). Planned: 60 of 80 `-go` flows contain 75 steps with an unbound param
   (static plan). WR10 is therefore killed by its unit test, not by PU.
4. The 2 "verified" flows per J app are `guard` flows that expect the 422/401 of the empty or unauthenticated
   step; their replay passes. They show that the refusal reproduces, not that a job lifecycle works.
5. `-min-spec-opus` JobRun `observed` edges 0 (predicted `queued->cancelled`, 0-1); status `static`, not `observed`.
6. WF8 is killed by PK (round-1 expectation named PF); WR10 by a unit test; both reported in the mutant table.

## Mutants

WR1 PH, WR2 PH, WR3 PH, WR4 PH, WR5 PH, WR6 PH, WR7 PH, WR8 PL, WR9 PL1, WR10 unit test
(`-k unbound_path_param or render_has_no_default`; PU does not kill it, finding 3), WR11 PH: **11/11 killed**.
WF1 PE/PF/PK/PS, WF2 PF/PK/PS, WF3 PD/PR, WF4 PF/PK, WF5 PM, WF6 PM, WF7 PD, WF8 PK, WF9 PF/PK/PS, WF10 unit,
WF11 PB-extract + unit, WF12 PW, WF13 PT: **13/13 killed**. Both controls passed first.

## Tests

`tests/gocheck tests/importer`: baseline 868 passed before the first edit (the pre-registration expected 857;
the next-phases HEAD has 11 more), 909 after (+36 `test_check_invocation.py`, +5 `test_workflows.py`).
`tests/importer tests/gocheck tests/spec tests/mcp`: 1926 passed, 2 failed, 29 errors, 4 skipped after; a `git archive
HEAD` copy gives 1885 passed with the identical 2 failed (`test_a_check_starts_and_removes_its_own_container`,
`test_the_template_reconciles_with_one_explained_disagreement`) and the same 29 setup errors (postgres, mysql,
clickhouse server fixtures). No existing assertion changed.

## Post hoc and pre-run script corrections

* PH refs comparison (probe script bug found in a smoke run before the registered run): old `got.refs == {non-None
  expected}`, new both sides drop None (`-min-spec-*` report `services: None`).
* WF2 mutant needle (`str(values.get(name, 1))` no longer exists, R2-D4): new needle fills `{p: 1 for p in
  route.path_params}` into `render`.
* Before the registered run: PH also checks which refs resolved (`describe()["resolved"]`) and, for the 3
  harness-less apps, the CLI run block (WR3, WR11 catchers); the `$VAR` fixtures require the words "shell variable".

## Orchestrator verification note
Independent smoke re-run from the committed tree with reduced counts (`--static-runs 2 --runs 1 --j-runs 2`, own
scratch, driver venv): 21 gated rows true; PB (extract, gocheck, diff, lint, Spike A probes) and LEAK all true.
PJ and PJs read False **only because the reduced counts cannot satisfy their registered "3/3 identical" and run
count rules**, not because a value differed; the builder's full-count run (10/10/10) is the registered result.
No finding above was fixed or re-scored. Every J number remains development-set, not blind.
