# Workflow layer (Spike B): results (Streak stage; held-out stage not run by the builder)

Data: `docs/results/data/2026-10-15-workflow-layer/` (`probes.json`, `summary.md`, `workflows.json` dynamic,
`static-workflows.json`, `mutants/`, `posthoc-list-rule-specific/`, `freeze.sha256`, `run.log`). Streak a0679e9, users
ann, ben, MySQL 8.4 in Docker (`lattice-wf-*`), Go 1.x offline. Predictions are the pre-registration's.

| probe | predicted | observed | |
|---|---|---|---|
| PE | 11/11; 0 extra; id sources client 2, response 1, list 1 | 11/11; 0 extra; habits client, completions client, devices response, nudges list; batch `habit_id` body edge apart; static == dynamic | pass |
| PS (lenient, gate) | 3/20 (1-5), verified 1-3 | lenient 3/20, strict 1/20; verified-only 3/20 and 1/20 | pass |
| PS2 | 0.03-0.15 | lenient 0.059, strict 0.015 | report |
| PK | 3/3 synthesised; K1, K3 verified; K2 failed | 3/3; K1 and K3 verified, K2 failed (422 on `items`) | pass |
| PF | 63 (45-80); verified 0.55-0.90; unverified 0.05-0.20; failed 0-0.20 | 68; verified 59 (0.868); unverified 1 (0.015); failed 8 (0.118) | pass; unverified below its range |
| PR | 0 failures | 59 verified flows replayed in a separate process on a fresh container: 0 failures | pass |
| PG | 100% | routes 21/21, handlers 21/21, bindings 111/111, statuses 23/23 | pass |
| PM | 4 states, 3 edges, initial {pending, suppressed}, observed moves 0, `static` | exact; observed initial {pending}; moves 0; sweep of `nudger`: moved false; status `static` | pass |
| PV | period literal; installation_id, timezone, email probe; date bound | all five as stated | report |
| PD | static 11/11, dynamic 10/10 | 11/11, 10/10; no path/port/container/timestamp outside values | pass |
| PW | 0 changed, 0 new | 0, 0 (after build, after 10 runs, and at the end) | pass |
| PT | 0 containers | 0 after each of 10 runs; 0 after a SIGINT mid-flow run | pass |
| PB | all | default extract identical to a5fed2e's binary and to the committed Spike A sidecars and model on S, S4 and the fixtures; gocheck findings 23/23 identical; Spike A probes pass; pytest 857 passed (baseline 775) | pass |
| PX | <= 5 s; <= 30 min | static 0.24 s; dynamic 31 s | pass |

Mutants WF1-WF13: 13/13 killed (WF13 only after strengthening; see the report). Findings and deviations are listed in
the builder's report to the orchestrator.

## Orchestrator verification (independent re-run)
`tests/gocheck tests/importer` 857 passed; `go vet`, `gofmt -l`, `ruff check` and `ruff format --check` clean;
the freeze hashes of `goextract_workflow.go`, `workflows.py`, `workflow_run.py` and `workflow_probes.py` equal
the builder's; `workflow_probes.py --static-runs 3 --runs 2 --spike-a` reproduces every row (PE 11/11, PS
lenient 3/20 and strict 1/20, PK 3/3 synthesised with K2 failed, PF 68 flows = 59 verified / 1 unverified / 8
failed, PR 0 failures over 59 replayed scenarios, PM exact, PD, PW, PT incl. SIGINT, PB-extract 4/4, Spike A
probes all pass); no `lattice-wf-` container left; the Streak checkout's `git status` unchanged (the owner's
`studio.toml` only). Dynamic wall 31 s per run (predicted <= 30 min).
Honest reading of the numbers: the headline structural recall against the spec's multi-route scenarios is
3/20 lenient (1/20 strict), as predicted by construction (132 of Streak's 156 scenarios touch one route and
133 start from seeded rows); the strong results are the 11/11 dependency edges, 59 verified flows with 0
failing an independent replay, and the exact Nudge.delivery machine (statically; no transition was observed
because the nudger does nothing at the seeded time, so its status is `static`, not `observed`).
Known finding: the registered list rule picks `GET /v1/home` for the nudge id, so the nudge flows fail (8
failed flows); the post hoc `--list-rule specific` run gets 58 verified and 6 failed. Not the registered result.

## Held-out stage H (run once by the orchestrator at 0f851fe; report-only; not re-scored)
Apps: the job-scheduler Go port (multidb 00dd1de) and its five variants; the comparison is the job-scheduler
spec `spec.tickets:t5` (24 path-param slots, 19 distinct multi-route skeletons, the JobRun.status machine with
5 states and 5 edges). The builder never read these apps; freeze hashes in `held-out/freeze.sha256`.

| app | flows | slot recall | skeleton recall lenient / strict | JobRun machine (states, static edges) | verified |
|---|---|---|---|---|---|
| job-scheduler-go | 80 | 16/24 (0.67) | 0/19 / 0/19 | found, 5/5, 3/5, initial exact | 0 |
| baseline | 74 | 21/24 (0.88) | 2/19 / 0/19 | not found | 0 |
| min-base-opus | 69 | 19/24 (0.79) | 2/19 / 0/19 | found, 3/5, 2/5, initial not exact | 0 |
| min-base-sonnet | 65 | 21/24 (0.88) | 2/19 / 0/19 | found, 5/5, 3/5, initial exact | 0 |
| min-spec-opus | 74 | 21/24 (0.88) | 2/19 / 0/19 | found, 5/5, 3/5, initial exact | 0 |
| min-spec-sonnet | 15 | 21/24 (0.88) | 2/19 / 0/19 | found, 3/5, 1/5, initial exact | 0 |

0 crashes of 6; no `lattice-wf-` container left. **The dynamic half was effectively NOT RUN on the held-out
apps.** For the three apps that have a harness, the run reported `harness: null, mode: static`: the layer did
not find a usable harness, so every flow is `unverified` (static) and the observed machine edges are 0/5. The
cause is a generality limit of the harness fallback (R2): it looks for the Streak harness names
(`harness:build_db`, `harness:login`), while job-scheduler's harness exposes `build_seed_db` and takes its
login from `spec.seed:login`. This is a finding, not a pass; it is not fixed and re-scored as blind (a later
fix would be a new, labelled round). What the held-out table does show: the static machine and dependency-edge
extraction transfers (machine found in 5 of 6 apps, edges 17-22), slot recall 0.67-0.88, and structural recall
against the spec's multi-route skeletons is near zero (as it is on Streak, by construction). Replay-verified
flows are demonstrated on Streak only.
