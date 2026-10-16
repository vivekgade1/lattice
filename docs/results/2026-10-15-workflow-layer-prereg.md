# Workflow layer (Spike B): implemented flows and machines, observed: pre-registration

## 1. Status, and the exact gap

2026-10-15, branch `np-workflow` (from `np-goextract` a5fed2e). **Written BEFORE any code.** Recon: code reading; Streak
a0679e9 archived to scratch; `gocheck extract` run on it; Streak's and job-scheduler's (ef17cf8, `spec.tickets.t5()`)
scenarios loaded in Python and normalised; `ls` (names only) of the six held-out checkouts. No LLM. **(m)** = measured,
everything else is a **prediction (p)**. Results go in `2026-10-15-workflow-layer-results.md`, with its data alongside.

**The code contradicts six premises (all m).**
1. **The spec's scenarios are mostly not flows.** Streak a0679e9 has **156** scenarios (90 written + 66 access-matrix
   cells). Normalised to (method, path with every id as `{}`) with `PUT /test/clock` dropped and consecutive repeats
   collapsed: **132 touch one route, 23 touch two or more (20 distinct skeletons)**, 1 touches none. 133 start from
   `given` rows; only 4 multi-route ones thread an id from an earlier request (`change_seq_counts_every_change`,
   `sync_changes_in_order`, `nudge_after_daily_pass_is_suppressed`, partly `second_check_in_on_a_date_is_refused`). So a
   synthesiser that builds state through requests matches few spec skeletons *by construction*. The gates below therefore
   rest on **11 spec-implied dependency edges** (m: a path id owned by a `given` row or earlier body of table T at
   route C) plus the plan's own known flows, and the skeleton recall is reported with low predicted ranges.
2. **Scenarios cannot thread values.** `Step.path`/`json` are literals and `Step.user` logs in through the harness
   (`spec/model.py:87-104`, `spec/check.py:1573-1700`). A flow run by a user who just registered cannot be replayed as a
   `Scenario`, so the plan's "register -> login -> create habit -> check in -> progress" becomes two flows: an anonymous
   account flow, and a habit flow run by a declared user.
3. **`mine.py` reads SQLite only** (`_read` uses sqlite3 at `mine.py:301-317`; `RunRecorder` uses the backup API).
   Go apps run on MySQL, so each step's MySQL state must be copied into a SQLite snapshot.
4. **`mine.machines()` only looks at profile `one_of` columns** (`mine.py:874-885`), and Go has no enums: the generated
   profile has no `one_of` for `nudges.delivery`. The layer has to derive it from the status writes.
5. **Spike A gives no from-states, param bindings or response shapes.** A status write carries `guard` as uninterpreted
   text. `owner_uses` follow only the caller id. `response_model=None`. In Streak, `period` has `choices: []` even though
   `period.Valid` compares it against constants (`internal/period/period.go:24`). The batch body is flattened: `items`
   (type None) sits beside top-level `id/habit_id/date/note`.
6. **`review.py` does not blank scenarios in general.** It does so only in the single-invariant re-probe
   (`review.py:411`). The real gap is that `build_spec`/`emit_spec.py` never emit a `Scenario` (0 mentions).

**What Spike A gives (m, Streak):** 26 routes with file:line, auth (21 Member, 5 Public), and body/query/path params
with bounds. 10 routes have path params (11 slots). `owner_uses` cover all 21 Member routes. `status_codes` include
`[200,201]` for devices and completions. 18 `status_writes`, of which only `nudges.delivery` has >= 2 values
{failed, pending, sent, suppressed}: `pending`/`suppressed` at the `Create` in `nudges/checkin.go:94` (roots: the 3
check-in routes); `failed`/`sent`/`suppressed` at the `UpdateColumns` in `nudges/nudger.go:180` (root
`nudges.RunDailyPass`). 4 `unread` items. Nudge ids are random (`crypto/rand`, `nudges/checkin.go:17-22`). Device ids
are auto-increment (the seed takes id 1).

**Rulings requested.** R1: the new Go facts go in a new `goextract_workflow.go`, enabled by an input flag. The default
output stays byte-identical. This needs a small edit to Spike A's `goextract.go` input struct and an opt-in kwarg on
`go_gin.extract_json`. R2: harness discovery falls back to a root `harness.py` (`make_app`, `build_db`, optional
`login`, `SERVICES`, `RUNTIME`) when there is no `studio.toml` `[app]`. R3: callers come from `--user` (the probes pass
the spec's `USERS` handles: Streak ann, ben). R4: the structural match definition below. R5: the held-out apps are
scored as in Spike A (the orchestrator freezes, then runs once).

## 2. Decisions (fixed before coding)

**D1 Inputs.** Spike A's `RepoModel` plus a workflow-mode sidecar: `gocheck extract` with `{"root", "workflow": true}`
gives the default JSON plus 4 keys (absent from default output): (a) `param_uses[]` per route `{param, source:
path|body, table, column, how: compare|assign, file, line}`: Spike A D8's condition parser on values from `c.Param(x)`,
a module helper returning one (`httpapi.PathUUID`) or a Body key, followed through locals and into helper parameters
(depth 4; `db.OwnedHabit`); (b) `creates[]` per route `{table, key_from: <body param>|server, file, line}` for
`Create`/`Save` of a new literal; (c) `params[].literals[]`: string constants the field's value is compared to (`==`,
`!=`, `switch`, map-key lookup) in the closure, helpers included (depth 4); (d) `status_writes[].kind` (create|update)
and `from[]` (D6). Everything not read goes to `unread` with file:line. Nothing is inferred from names.

**D2 Dependency graph.** An edge `(T.key; P -> C.p)` exists iff C's path param `p` has a `compare` param use on T's key
and P creates T (`creates[]`). Id sources, in order: (1) `client`: `key_from` is a body param, so a fresh id is generated;
(2) `response`: top-level `id` of P's response, else the one key whose object value has an `id`; (3) `list`: the first
GET over T with no path param on T (registration order), taking the first element of the first array-valued top-level
key whose elements carry `id`; (4) `seed`: the smallest key of T in the harness-seeded database; (5) otherwise the
edge goes to `unread` ("no producer"). Several creators: the first by registration order produces, the rest are listed
as `alternatives`. Composite keys and non-key params (a slug) bind to the producer's request field that `assign`s that
column, else `unread`. Query params (cursor, limit) are never bound.

**D3 Flow kinds** (each flow is a step list; `setup` = the steps before the focus producer, `focus_from` its index).
`pair`: one per edge: the setup chain satisfying P's own path params, then P, then C; pairs with the same skeleton are
merged and list every edge they cover. `lifecycle`: per root table, write steps only, in phase order (create T, create
children, update T by PATCH/PUT or POST action routes on T, delete children, delete T). `effect`: for each write step
W in a chain (creators and T-param writes) and each GET route R whose path params are all bindable in the flow, with
`tables(R) ∩ (tables(W) − {users_table}) ≠ ∅`: [setup, W, R]. `owner_guard`: a pair flow whose consumer has owner
evidence, with the consumer called by the second declared user. `after_delete`: [create T, delete T, the first read
consumer of T]. `account`: register (the Public POST creating `auth.users_table`), login (`auth.login_path`), then the
Public POSTs whose body keys equal keys of the login response (refresh). `machine`: a chain leaving a machine-table
row in an initial state, then each route rooted at an update write of that column whose from-state matches.
`service`: D7; `guard`: D5. Ordering inside a flow: phase, then registration (file, line). Routes registered under a
`condition` (`/test/clock`) are never used and are listed. Caps: <= 8 requests per flow, <= 96 flows per app; on
overflow flows are kept in priority order (pair, lifecycle, account, owner_guard, after_delete, guard, effect, machine,
service), then by skeleton, and the dropped ones are listed under `capped`.

**D4 Values** (deterministic; required fields plus the key field only; optional fields omitted). Candidates per field, in
order: (1) `choices`, then `literals`; (2) a bound value: a same-name field of an earlier request in the flow, then of
an earlier response (top level or one level down), then, for date types, the first ISO-date value in earlier
responses (key fields are never bound, always freshly generated); (3) a type default: Int `max(1, ge, gt+1)`, Bool
`false` then `true`, Str a slug `wf<flow>-<field>` padded or cut to the bounds; (4) a fixed Str probe list: a canonical
UUID `00000000-0000-4000-8000-<12-digit counter>`, `wf<n>@example.test`, `UTC`, `2026-01-01`, `1.0.0`,
`https://example.test/`. Generated ids come from a per-flow counter, so they are reproducible. **Repair:** a 422 whose
JSON names a field (any string value equal to a sent key) moves that field to its next candidate, else the fields with
candidates left move in sorted order; a non-422 4xx (except 401/404 on the focus consumer) inserts each action route on
T before the step, in registration order, at most 3 times (`inserted: precondition`); a 401/403 on a Member step
retries with the next declared user. Limits: <= 6 attempts per step, <= 24 per flow. Each value records its
`value_source`.

**D5 Running, and what `verified` means.** One throwaway MySQL (`dbserver.server_for("mysql",
name_prefix="lattice-wf-")`); per flow a fresh database, `build_db`, `make_app` through the harness (R2); the runtime
is the harness `RUNTIME`, else Spike A's `go_gin.runtime`; logins use `runtime.auth.token(client, handle, login)`, as
the checker does. **Live run L:** values threaded; after each request every profile table is read (`read_table`,
ordered by key) into a SQLite snapshot under the output's scratch directory. The expected status is the observed one
when it is in the route's code statuses (2xx `status_codes`, or `raises`/`guards` for a refusal), else the step fails
("status not in code evidence"). **Freeze:** concrete paths, bodies and users become a `Scenario`; a bound value that
differs between L and the replay shows up as a replay failure. **Replay R:** `lattice.spec.check.check(spec',
layers="D", runtime, login, services)` on a fresh database, `spec'` = Spike A's `build_spec` candidate with
`scenarios=(the frozen one,)`, `invariants=()`, `machines=()`, `users` = the declared handles. **`status`:**
`verified` = L passed every step and R gave 0 layer-D findings; `unverified` = L passed but R failed (the reason quotes
the step; typically a random id or token), or the flow cannot be a Scenario, or the run was static (`--static`, no
harness); `failed` = L did not pass, with the step, observed status and refusal `code` recorded; when that status is
one of the route's raise statuses, the prefix up to it is also emitted as `kind: guard`, expecting it. `source`:
`static` (never run) or `observed` (run live). No flow is `verified` without R.

**D6 Machines.** A candidate is a `(table, column)` with >= 2 constant written values: states = the values, `initial`
= the values written at `create` sites. Static edges run from each `update` site's `from[]` to its value: a row loaded
or ranged over by a query binding the same column to a constant gives `from` (Streak: `pending` from
`nudges.delivery = ?` at `nudger.go:125`), as does an enclosing `if x.Col == c` / `switch x.Col`; otherwise `from =
["*"]`, the edge goes to `unread` and no static map is made. Confirmation: the L snapshots become `mine.Run`s and
`mine(profile', runs, hints=MiningHints(transitions=[the static map]), templates=("machine",))` runs unchanged,
`profile'` = the generated profile plus `one_of` = the states. `status`: `confirmed` (every static edge observed),
`partial` (some), `static` (none observed, none refuted), `observed` (no static map; mined edges only), `refuted`
(mine refuted the map). Each edge records `source` (static|observed|both), evidence file:line and its observed count.

**D7 Services and Await.** Await steps are never invented. For each harness service and each machine, a **sweep** runs
after the first flow (in sort order) whose L left rows in that machine's table. The service starts on that flow's
database for <= 8 s and is stopped once two consecutive snapshots are equal after a change. If a machine column moved,
a `service` flow is emitted: the flow plus `Await(service, expect_db={T: {"where": {key: v}, "equals": {col: final}}},
within_s=10)`. It is verified by R like the rest. If nothing moved, the sweep is recorded with no flow.

**D8 Destructive steps.** Each flow runs on its own database, so destruction never leaks between flows. DELETE steps
come last by phase. A later step that fails because of an earlier deletion or logout is a `failed` flow with its
evidence; nothing is special-cased by name.

**D9 Output `workflows.json`, `schema: "lattice.workflows/1"`** (the contract Spike C reads). `app {module, framework,
extract_sha256, workflow_extract_sha256}`; `run {mode: observed|static, harness: studio.toml|harness.py|null, users[],
services_swept[], caps{}}`; `edges[] {id, table, key, producer, consumer, param, param_source, id_source,
alternatives[], evidence[]}`; `machines[] {id, table, column, states[], initial[], edges[{from, to, source, observed,
evidence[]}], status, spec_object{table, column, edges, initial}, sweeps[{service, moved}], unread[]}`; `flows[] {id:
"wf-"+sha256(kind, skeleton, callers)[:10], name, inferred_name: true, kind, skeleton["METHOD /p/{}"], routes["METHOD
/p/{x}"], focus_from, status, source, reason, machine_refs[], edge_refs[], steps[]}`, where a request step is `{i, kind:
"request", method, route, path, caller, role: setup|focus, json, expect_status, expect_from: observed|static,
code_statuses[], bindings[{param, source, value, from}], value_sources{}, inserted, attempts, refusal, evidence[{what:
route|handler|param_use|raise|create, at: "file:line"}]}` and an Await step is `{i, kind: "await", service, expect_db,
within_s}`; plus `unread[]`, `capped[]`, `summary{flows, verified, unverified, failed, edges, machines}`.

Names are built from handler names and verbs (`create habits > create completions > read progress`) and are always
labelled inferred. Arrays are sorted: edges by (table, producer, consumer, param); machines by (table, column); flows
by (kind priority, skeleton, callers). No absolute path, port, container name, time or duration appears, and no
response body (only statuses and refusal codes). Paths are relative with `/`. Server-assigned values appear only after
R has reproduced them.

**D10 Exposure.** `lattice/importer/workflows.py` (graph, synthesis, values, schema, `to_json`) and
`lattice/importer/workflow_run.py` (L, R, snapshots, sweeps, the `mine` bridge). CLI `lattice import workflows <root>
--out DIR [--package .] [--user H]... [--static] [--max-flows 96] [--max-steps 8] [--no-services]`, dispatched in
`importer/cli.py` like `review` (a repo literally named `workflows` needs `./workflows`). `--out` is required and a path
under `<root>` is refused; nothing is written into the target; bytecode writing is off; a missing Docker, binary or
driver stops the run with an error naming it; teardown in `finally`. `mine.py`, `review.py`, `emit_spec.py`,
`appmodel.py` and `lattice/spec/*` are not edited.

## 3. Probes (runner `dogfood/workflow_probes.py`; blind pass rules)

Clone **S** = `git archive` of Streak a0679e9 into `$SCRATCH`. The runner builds `bin/` in the clone (as in Spike A)
and runs with `--user ann --user ben`. **Structural match (R4):** a spec skeleton `K` (as in section 1) matches flow F
iff `K == F.skeleton[j:]` for some `j <= F.focus_from`, that is, F equals `K` once some of its setup steps are dropped.
Flows count at any status; verified-only is reported. The 20 Streak spec skeletons (m) are listed in the runner.

| id | probe | prediction (p) | pass |
|---|---|---|---|
| PE | dependency edges vs the 11 spec-implied (table, route, param) edges (m): habits x 8 consumers, completions/{cid}, devices/{}, nudges/{}/dismiss | 11/11 (9-11); 0 extra path edges (0-2); `id_source` per producer table: client 2 (habits, completions), response 1 (devices), list 1 (nudges); batch `habit_id` body edge reported apart | >= 10/11; <= 2 extra |
| PS | structural recall vs the 20 multi-route spec skeletons | 3/20 (1-5): `POST c > DELETE c`, `POST h > POST c > GET c`, `PATCH h > GET progress`; verified-only 1-3 | >= 1/20; reported |
| PS2 | spec precision (flows matching any spec skeleton / flows), report only | 0.03-0.15 | reported |
| PK | plan's known flows: K1 = account `[register, login]` + habit `[POST h, POST c, GET progress]`; K2 batch `[POST h, POST /v1/completions/batch]`; K3 `[POST devices, DELETE devices/{}]` | synthesised 3/3; verified K1 and K3, K2 `failed` (nested `items` body, 422) | synthesised 3/3; verified >= 2/3 |
| PF | flows on S: count; verified / unverified / failed fractions | 63 (45-80); verified 0.55-0.90; unverified 0.05-0.20 (nudge ids, refresh token); failed 0.00-0.20 | count in range; verified >= 0.50 |
| PR | independent replay: a separate process rebuilds each verified flow's Scenario from `workflows.json` and runs `check(layers="D")` on a fresh container | 0 failures | **exactly 0** |
| PG | evidence: every step's route and handler file:line hold the named symbol; every binding has a `param_use`; every expected status >= 400 is in that route's raises/guards | 100% | 100% |
| PM | `Nudge.delivery`: states, static edges, initial vs the spec's Machine; observed | states 4/4; edges = {pending->sent, pending->failed, pending->suppressed} exactly; initial {pending, suppressed}; observed initial ⊇ {pending}; observed moves 0 (0-1; the nudger does nothing at 07:00 local); status `static`; 0 refutations | states, edges, initial exact; 0 refuted |
| PV | value sources: `period` from literals; `installation_id`, `timezone`, email from the probe list; check-in `date` bound from `start_date` | all four as stated | reported |
| PD | 10 static runs (`--static`) + 1 from a second clone path; 10 dynamic runs | `workflows.json` byte-identical 11/11 and 10/10; no `/Users`, `/private`, port or ISO timestamp outside values | 11/11, 10/10 |
| PW | tree listing + sha256 of every S file after the build, vs after 10 dynamic runs | identical | 0 changed, 0 new |
| PT | containers `lattice-wf-*` after every run (and after a run killed with SIGINT mid-flow) | 0 | 0 |
| PB | byte-compat | default `gocheck extract` JSON on S and every `tests/gocheck/fixtures/goextract/*` identical to a5fed2e's binary; gocheck findings identical; `pytest tests/importer tests/gocheck` passes; `tests/importer/goldens` and `mine, review, emit_spec, appmodel, profile_gen, spec/*` have an empty diff | all |
| PX | wall: synthesis (static) and a full dynamic run on S | 0.1-1 s; 3-12 min | <= 5 s; <= 30 min |

**H, held out (scored once, report-only, never tuned).** Apps: the six `job-scheduler-go*` of Spike A. 3 have a
`harness.py` (`-go`, `-min-spec-opus`, `-min-spec-sonnet`) and 3 have none, so those run `--static` (m, names only).
Spec: job-scheduler ef17cf8 `t5()`, measured: 43 scenarios, 21 multi-route (19 distinct skeletons), 24 path-param
slots over 21 routes, `JobRun.status` with 5 states / 5 edges / initial `queued`, services worker and scheduler. Users:
the spec's `USERS` handles in the order the mapping lists them. Order: (1) freeze: sha256 of `goextract*.go`,
`go_gin.py`, `workflows.py`, `workflow_run.py` recorded in the results; (2) the scorer is hashed; (3) the runner
archives each app into scratch and runs once. Predicted ranges per app: slot recall 0.50-1.00; structural recall on
the 19 skeletons 0.10-0.40 (pair flows plus precondition repair: `POST jobs > activate`, `POST jobs > activate >
runs`, and the `guard` `POST jobs > runs` 409); machine states 0.60-1.00, static edges 0.20-1.00, observed edges
(worker sweep, harness apps) 0.20-1.00; verified fraction (harness apps) 0.20-0.80; PR failures 0; crashes 0/6.
Misses are generality findings, never fixed and re-scored as blind.

## 4. Mutants (`dogfood/workflow_mutants.py` on a scratch copy; control passes first; predicted 13/13 killed)

| id | mutant | killed by |
|---|---|---|
| WF1 | `param_uses` ignored (no edges) | PE (0/11) |
| WF2 | path params filled with `1` instead of threaded ids | PF (consumers 404, verified < 0.50) |
| WF3 | `verified` from L alone (no replay R) | PR (nudge/refresh flows fail replay) |
| WF4 | expected status = first static 2xx, not observed | PF (devices 201 vs 200, completions) |
| WF5 | machine from-rule off (`from = "*"`) | PM (0/3 static edges) |
| WF6 | every written value counted as initial | PM (initial 4 != 2) |
| WF7 | unsorted maps / absolute paths / a timestamp in output | PD |
| WF8 | repair loop off | PF, PV (`installation_id`, timezone, email refused) |
| WF9 | `literals` not read | PV, PF (`period` 422: every habit flow fails) |
| WF10 | guard status accepted when not in code statuses | PG |
| WF11 | workflow keys emitted in default extract | PB |
| WF12 | snapshots written under the target root | PW |
| WF13 | container not removed on error | PT |

## 5. Risks, and what this cannot show

1. **Flows are only those derivable**: edges need a code-visible key comparison; business sequencing in people's heads
   or the client (Streak's "set nudge_time 06:00, then wait for the nudger") is not recovered, hence PS is low and the
   nudger edges are static only.
2. **Value synthesis is the bottleneck**: a field validated by a parser the extractor cannot read, and satisfied by no
   probe-list value, fails its flow (`failed` with the refusal, never guessed); nested bodies (`items[]`) are not
   modelled.
3. **The Scenario model's limits** (no threading, harness logins) move random-id and new-user flows to `unverified`; a
   `verified` flow proves those exact requests answer those statuses on a fresh seeded database, nothing about bodies.
4. **Overfitting**: D1-D4 were written with Streak open (mitigated by H; repair and probe lists are generic "common
   formats"); the effect rule over-generates (`GET /v1/me` after a check-in, via `notification_prefs`).
5. **Timing**: sweeps depend on service timing (final state only); without a test clock, date-bound values follow the
   real day (determinism holds within a day).
6. **Cannot show:** response shapes, row-level effects (`expect_db` only on sweeps), concurrency, roles beyond the
   declared handles, non-Gin frameworks. **Builder bias:** the builder sees these predictions and the Streak ground
   truth; only H is blind.

## 6. Blind vs post hoc, and commands

These predictions are fixed now. A failed prediction is a **finding**, never silently fixed. Fixes go in separate
post-hoc commits, with the probes re-run and the deltas reported. H is never re-scored as blind. If a ruling changes
D1-D9, it is recorded by amendment before code.

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-workflow PYTHONDONTWRITEBYTECODE=1
SCRATCH=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/workflow
DRV=$SCRATCH/../perfengines/perfvenv/bin/python      # pymysql (read-only reuse)
cd /Users/vivekgade/Documents/lattice-workflow
git -C /Users/vivekgade/Documents/streak status --short; docker image inspect mysql:8.4 >/dev/null
"$DRV" dogfood/workflow_probes.py --streak-ref a0679e9 --users ann,ben --static-runs 10 --runs 10 \
    --scratch "$SCRATCH/p" --out "$SCRATCH/p-data"                    # every S probe of section 3
uv run python dogfood/workflow_mutants.py $(seq -f WF%g 1 13)        # control passes; 13 killed
uv run python -m pytest tests/importer tests/gocheck -q -rs          # PB
"$DRV" -m lattice.cli import workflows "$SCRATCH/p/S" --user ann --user ben --out "$SCRATCH/cli-out"
shasum -a 256 lattice/gocheck/src/goextract*.go lattice/importer/frontends/go_gin.py \
    lattice/importer/workflows.py lattice/importer/workflow_run.py  # freeze, recorded BEFORE H
"$DRV" dogfood/workflow_probes.py --held-out --scratch "$SCRATCH/h" --out "$SCRATCH/h-data"   # orchestrator, once
uv run ruff check <changed .py>; uv run ruff format --check <created .py>; gofmt -l lattice/gocheck/src
```

**Pass:** every gated row of section 3, 13/13 mutants, PB; H reported against its ranges.

## 7. File ownership (build node)

**Owns (new):** `lattice/gocheck/src/goextract_workflow.go`; `lattice/importer/workflows.py`,
`lattice/importer/workflow_run.py`; `tests/gocheck/fixtures/goextract_workflow/` (a small Gin app with server ids, a
from-rule, a list-id fallback, a nested body, a precondition guard, a conditional route);
`tests/gocheck/test_goextract_workflow.py`, `tests/importer/test_workflows.py`; `dogfood/workflow_probes.py`,
`dogfood/workflow_mutants.py`; the results doc and its data. **Edits (minimal, R1):** `lattice/gocheck/src/goextract.go`
(the input struct gains `workflow` plus a branch calling the new file, <= 12 lines, default path byte-identical);
`lattice/importer/frontends/go_gin.py` (`extract_json(..., workflow=False)`); `lattice/importer/cli.py` (the
`workflows` dispatch, <= 6 lines). **Not touched:** `lattice/importer/{mine,review,emit_spec,appmodel,profile_gen,
rules,transport}.py`, `lattice/spec/*`, the other gocheck files, existing tests and goldens (any edit is a finding),
the Streak checkout, every `job-scheduler*` directory, the main checkout. The build node never reads, runs or
extracts any `job-scheduler-go*` source (only the orchestrator runs H).

## Amendment 1 (orchestrator rulings, 2026-10-15, before any code)

**R1 approved:** the opt-in workflow facts (`goextract_workflow.go`, input flag `{"workflow": true}`, an edit of
at most 12 lines to `goextract.go`, an opt-in keyword on `go_gin.extract_json`). Condition: the default
extract output of the Streak a0679e9 and 1d00ede stages is **byte-identical** to the committed Spike A
output (sha256 of the sidecar and `to_json()` equal), and the Spike A probes PR1-PU, PD, PW still pass; the
Spike A results and held-out table stay valid for the committed extractor (a5fed2e), which this change does not
re-score. **R2 approved** (root `harness.py` fallback when there is no `studio.toml`; read-only).
**R3 approved** (callers from `--user`). **R4 approved with a strict companion:** every structural-match
number is reported twice, strict (skeleton equals the flow) and lenient (equals the flow after dropping
leading setup steps); the gate uses the one the pre-registration names, and the other is reported beside it
so the lenient rule cannot inflate the headline. **R5 approved** as in Spike A: the build node does **not
read, list, run or extract anything under `/Users/vivekgade/Documents/job-scheduler-go*`**; the orchestrator
freezes the hashes, has a fresh agent write held-out ground truth where one is needed, and runs that stage
once; three of the six apps have no harness and are scored statically only (stated in the table).
**Other rulings.** The headline gate is the 11 spec-implied dependency edges and the verified-flow
replay (0 failing), not recall of spec scenario skeletons (predicted 3/20 by construction, reported as the
measured figure). A prediction that misses is a finding. Dynamic determinism is 10 static runs and 10 dynamic
runs as registered even though the dynamic runs take about an hour; the orchestrator's own re-run uses fewer
and says so. Docker containers use a `lattice-wf-` prefix and must be 0 after every run. Nothing is written into
the Streak checkout, `job-scheduler*` or the main checkout.
