# Workflow layer, round 2: harness discovery from the check invocation, list rule, dev-set dynamic run: pre-registration

## 1. Status, and the exact gap

2026-10-16, branch `np-b2` (worktree `~/Documents/lattice-b2`, from `next-phases` bb3ff0c). **Written BEFORE any round-2
code.** The round-1 frozen files (`workflows.py`, `workflow_run.py`, `go_gin.py`, `workflow_probes.py`) hash to the
round-1 `held-out/freeze.sha256` at bb3ff0c (m). **(m)** = measured; everything else is a **prediction (p)**. Results go
in `2026-10-16-workflow-layer-round2-results.md` with data in `docs/results/data/2026-10-16-workflow-layer-round2/`.
Recon for this document: the round-1 docs and data; code reading; Streak a0679e9 archived to scratch; for the three
job-scheduler Go apps that have a harness, `check.sh` and `harness.py` read, the apps archived into scratch, `go build -o
bin/server ./cmd/server` run there (rc 0, 0.6-1.5 s, offline), and the check's `module:attr` references imported with
the round-1 `_harness_attr`. The layer was **not** run on them. No Go source of any job-scheduler app was opened.

**This is a development-set round, not a blind held-out claim.** The six job-scheduler Go apps ("J") were the round-1
held-out set; their static outputs are published, and the builder of this round may read two of their files (R2-D7).
Any J number here measures transfer to apps already seen. A blind generality claim needs a fresh app (section 5).

**Round 1, as measured (m).** S = Streak a0679e9: 68 flows, 59 verified / 1 unverified / 8 failed, 0 failing replays,
11/11 edges, Nudge.delivery exact (static). J: all six static. For the three harness apps, `find_harness` returned
None: there is no `studio.toml`, and the `harness.py` fallback (R2) needs `build_db`, which none has. Recon shows this
exactly (m). `harness:make_app`, `harness:build_seed_db`, `harness:RUNTIME` and `spec.seed:login` resolve in all three.
`harness:build_db` and `harness:login` resolve in none. `harness:SERVICES` resolves in `-go` only.

**Corrections to the premise (all m).**
1. **The list rule explains 3 of the 8 failed S flows, not 8.** Under `first`, the 8 failures break down as: 3 nudge
   flows (pair and effect: the dismiss got a habit id from `GET /v1/home` and answered 404; owner_guard: no id), 2 batch
   flows (422 on `items`, K2 as predicted), and 3 effect flows that read a habit after `DELETE` (404). There is also a
   **spuriously verified** `guard` flow `[POST h, POST c, GET /v1/home, POST /v1/nudges/{}/dismiss]` that "expects" the
   404 from that wrong-table id. Post hoc `specific`: 69 / 58 / 5 / 6. The pair and two effect nudge flows go from
   failed to unverified (nudge ids come from `crypto/rand`, so the replay cannot reproduce them; round 1 predicted
   exactly this). The spurious guard disappears. The owner_guard nudge flow stays failed: the list is read as `ben`,
   who has no nudges. No flow verified for a sound reason is lost.
2. **The J checks run on SQLite.** None of the three `check.sh` passes `--database`, so `lattice.spec check` uses its
   default, `sqlite` (`spec/__main__.py`). The `build_seed_db` of `-min-spec-opus`/`-sonnet` is SQLite-only
   (`sqlite3.connect(path)`); `-go`'s also takes a server URL. So the J dynamic runs use the round-1 `SqliteProvider`
   with no Docker. Docker teardown is exercised on S, plus one report-only MySQL arm of `-go` (J-my).
3. **Only `-go` has services.** The `-min-spec-*` `check.sh` passes no `--services` and their harness defines no
   `SERVICES`, so they get no worker sweep. No `/worker/*` route appears in any J flow (round-1 outputs), so the 6 of
   the 19 spec skeletons that contain `/worker/run-once` or `/worker/schedule-tick` cannot match: the most recall can
   reach is 13/19.
4. **`-go` cannot reach a JobRun row.** `job_configs.id` has no producer (round-1 unread, `internal/app/jobs.go:113`
   and four more). 60 of `-go`'s 80 planned flows have a step whose path param is unbound (`job_id` x 57 steps, `user_id`
   x 18). At run time, round-1 `Executor.render` fills an unbound param with **`1`**. That is the WF2 mutant's
   behaviour, and on a fresh database it can hit by accident (the first job created gets id 1). `-min-spec-*`: 7 and 2
   flows (`DELETE .../members/{user_id}`). On S every sent step is bound (108/108; the one unbound step is not sent).
5. **The JobRun machine has no static map in any J app.** `cancelled` is written from an unread state, so the machine
   has `static: false`. `mine` therefore gets no transition hint and cannot refute. With moves the status becomes
   `observed`, without them `static`. "Observed edges measurable" holds, but `-go`'s is predicted 0 (point 4).
6. The list rule is inert on J: every J edge has a server key with a `response` id (17/22/21 edges, round 1).

## 2. Decisions (fixed before coding; round-1 D1-D9 hold unless changed here)

**R2-D1 Harness discovery: the project's own check invocation.** Sources in order, first match wins, and **no later
source is tried once one is found but cannot be resolved**:
(1) `studio.toml [app]` with both `app` and `db`: round 1, unchanged. An `[app]` that has some of `app/db/login/
runtime/services` but lacks `app` or `db` makes the run static with a reason.
(2) A **check invocation**. The files are the `studio.toml [rebuild] gates` entries that are files under root, else
root `check.sh`. They are read as text and **never executed**. Backslash-continued lines are joined, the text is split
into commands on newlines, `;`, `&&`, `||` and `|`, and `shlex` (posix, comments) tokenises each one. A command that
contains `-m lattice.spec check` is an invocation. If it contains `$@`, the arguments of the last preceding `set --` in
the same file are spliced in (Streak's form). The flags read are `--app`, `--db`, `--login`, `--runtime`, `--services`
and `--database` (both `--f v` and `--f=v`), plus the presence of `--login-path` or `--mint-token`. Several invocations
must agree on these flags; otherwise the source is unresolvable ("ambiguous"). A file with no invocation counts as
absent.
(3) A root `harness.py` with `make_app` + `build_db` (round-1 R2, unchanged; no other names are ever tried).
(4) None: the run is static.
**Resolution:** each `module:attr` is imported from root by round-1 `_harness_attr` (root on `sys.path`, no bytecode).
`--runtime process|go` maps to `process_runtime()` and `process_runtime(framework="go")`, as the checker does. With no
runtime, round 1 applies (Spike A's `go_gin.runtime`). With no login, `login=None` (round 1). With no services, `{}`;
services are never taken by name. **Unresolvable means static with a stated reason**: a value containing `$` or a
backtick, a missing `--app`/`--db`, an import or attribute error (the reason gives the reference and the exception
type, never a traceback), `--login-path`/`--mint-token` without `--login`, ambiguity, or an unsupported database.
`run.harness_reason` names the cause. When no source exists at all, the reason lists the four sources looked for.
**R2-D2 Database.** An explicit CLI `--database mysql|sqlite` wins. Otherwise (`auto`) the database is the invocation's
`--database`, or `sqlite` when the invocation names none (`lattice.spec check`'s default). For a `studio.toml` source it
is `[app] database` if present, else the extract's dialect. For a `harness.py` source it is the extract's dialect
(round 1). On S this gives `mysql` either way (m: `[app] database = "mysql"`, extract `mysql`). Anything other than
`mysql` or `sqlite` (`postgres`, an engine file) makes the run static with a reason.
**R2-D3 List rule (decided from the round-1 data in section 1 only; J cannot inform it, point 6).** The default becomes
**`specific`**. Among the GET routes with no path param whose tables include T, it takes the one touching the fewest
tables, ties broken by registration order, as round 1's post hoc variant does; only that one list is tried. The reasons:
it removes a spuriously verified flow; it turns the nudge flows into the unverified status that round 1 predicted for
random ids; it fails 2 fewer flows; and it costs nothing verified for a sound reason. `--list-rule first` keeps round 1
byte for byte. **Not taken:** reading the list as the producer's caller. It would let the owner_guard nudge flow run,
but its 404 by `ben` on a random id would then replay as "verified" whatever the id, a vacuous verification. It stays
failed, as a finding.
**R2-D4 No silent id.** A path param with no binding is never filled. The step is not sent and the flow is `failed`
with the reason `unbound path param <p>: no producer` (its `unread` item already says why). No change on S (point 4).
**R2-D5 Output.** `schema` stays `lattice.workflows/1` and the additions are additive. `run.harness` takes one of
`studio.toml | check.sh | harness.py | null` (`check.sh` stands for any gate file; the path relative to root goes in
`run.harness_file`, or null). Also new: `run.harness_reason` (null when found), `run.list_rule` and `run.database`
(`mysql | sqlite | null`). Spike C reads only `schema` and `run.mode` (m: `implementation.py:353,460`). No absolute
path, port or time is added.
**R2-D6 Frozen.** Round-1 D1 (extractor facts), D3 (flow kinds, caps), D4 (values, repair, probe list), D5 (statuses,
replay), D6 (machines), D7 (sweeps) and D8 are unchanged. So are `goextract*.go`, `go_gin.py` and `cli.py`, and the
probe runner's `score_held_out` and `spec_truth` keep byte-identical bodies.
**R2-D7 What the builder may read of J, and why that is acceptable now.** Allowed: **exactly** `check.sh` and
`harness.py` of `job-scheduler-go`, `-min-spec-opus` and `-min-spec-sonnet`, plus the J stage's own outputs
(`workflows.json`, stderr, scores). For the three harness-less apps, only the existence test of `studio.toml`,
`check.sh` and `harness.py`. The layer may *import* `harness.py` and `spec/seed.py` from scratch archives; nobody opens
`spec/*.py`, `cmd/`, `internal/` or any `.go` file, including at the file:line evidence the outputs cite. **Fixes the
J runs prompt are allowed only in R2-D1/D2 plumbing**: discovery, import, the provider, teardown. A J flow that fails
on a value (for example a schedule field refused with 422), on a missing extractor fact or on flow planning is a
finding, never a fix. Why acceptable: round 1 is closed, and its J numbers are published and are not re-scored. The two
files are the project's declared check contract, the same input a user hands Lattice (`[app]` is its TOML form). They
start a binary and seed three tables, with no business logic. Discovery cannot be designed from S alone, whose harness
comes through `studio.toml`. The cost: `harness.py` reveals the seed (users, applications, memberships with ids), which
could bias value choices. R2-D6 freezes D4 against that.

## 3. Probes (runner `dogfood/workflow_probes.py`, new `--round2` stage; blind pass rules)

S = `git archive` a0679e9. S' = S with `studio.toml` deleted in scratch. J = each app at its HEAD (m): `-go` 00dd1de,
`-min-spec-opus` 7d1a730, `-min-spec-sonnet` f12365b; harness-less: `-baseline` b8d9fe0, `-min-base-opus` 00cbf55,
`-min-base-sonnet` 7ac55d5. The build line is each `check.sh`'s `go build -o bin/server ./cmd/server` under round 1's
`GO_ENV`. Spec: job-scheduler ef17cf8 `spec.tickets:t5`, archived; truth as in round 1 (m): 24 slots, 19 skeletons,
JobRun.status with 5 states, 5 edges and initial `queued`. Users: S `ann,ben`; J `root,ann` (as round 1). Structural
match is reported strict and lenient (round-1 R4). Prediction ranges are in parentheses.

| id | probe | prediction (p) | pass |
|---|---|---|---|
| PH | discovery table: per app, `run.harness`, `harness_file`, the resolved refs, services keys, `database`, reason | S: studio.toml, mysql, {nudger}. S': check.sh (`set --` form), mysql, {nudger}, flows/statuses = S. `-go`: check.sh, app `harness:make_app`, db `harness:build_seed_db`, runtime `harness:RUNTIME`, login `spec.seed:login`, {worker, scheduler}, sqlite. `-min-spec-*`: same, services {}. 3 harness-less: null, reason lists the 4 sources. Fixtures (test file): `$VAR` in `--app`; `harness:nope`; `--database postgres`; `--login-path` only; incomplete `[app]`; harness.py with `build_seed_db` and no check.sh; two disagreeing invocations; `[app]`+check.sh disagreeing (studio.toml wins): each static with a reason naming its cause, or as stated | all exact |
| PL | list rule on S, default (`specific`) | 69 flows; 58 verified (0.841) / 5 unverified / 6 failed (m post hoc, predicted to reproduce exactly); the nudge list route `GET /v1/nudges`; 0 bindings from `GET /v1/home` | flows 65-75; verified >= 0.80; failed <= 6; 0 `/v1/home` id bindings |
| PL1 | `--list-rule first` on S | equal to round-1 `workflows.json` after dropping the 4 new `run` keys (68 / 59 / 1 / 8) | byte-identical |
| PU | sent requests with an unbound path param (S, S', J dynamic) | 0; `-go` failed reasons name `job_id`/`user_id` | **exactly 0** |
| PE, PK, PM, PS | round-1 probes on S | as round 1: 11/11 edges (nudges `list`); K1, K3 verified, K2 failed; Nudge.delivery exact, moves 0, `static`; PS lenient 3/20, strict 1/20 | as round 1 |
| PF | S fractions vs round-1 ranges | verified 0.841, unverified 0.072, failed 0.087 (all inside round-1 PF ranges; unverified now inside) | verified >= 0.50 |
| PR | independent replay of every verified flow (S on MySQL; each J harness app with the refs **hard-coded from its check.sh text**, not the layer's discovery) | S 58 scenarios, 0 failures; J 0 failures | **exactly 0** each |
| PG | evidence holds (S gated; J report-only) | 100% | S 100% |
| PJ | J dynamic run, per harness app: mode, flows, verified/unverified/failed, slots, skeletons, JobRun, sweeps | see the table below | mode `observed` and harness `check.sh` 3/3; crashes 0/3 |
| PJs | 3 harness-less apps | static; `workflows.json` = round-1 `h-*.workflows.json` minus the new `run` keys | 3/3 identical |
| PD | S: 10 static + 1 second-path, 10 dynamic; J: 10 dynamic per harness app | byte-identical 11/11, 10/10, 10/10 x3; no `/Users`, `/private`, port or ISO time outside values | all |
| PW | S and each J archive: tree + sha256 after the build vs after all runs; targets: `git status --porcelain` and file listing of the Streak checkout, the 6 J dirs and job-scheduler before vs after | 0 changed, 0 new | 0, 0 |
| PT | `lattice-wf-*` containers and leftover app processes (`streak-api`, `bin/server` from scratch paths) after every run, after SIGINT mid-flow on S and on `-go` | 0 | 0 |
| PB | default `gocheck extract` on S, S4 (1d00ede) and fixtures = a5fed2e binary; `pytest tests/importer tests/gocheck tests/spec tests/mcp` = base pass set (same pre-existing failure ids as the handoff); frozen files (R2-D6, `mine/review/emit_spec/appmodel/profile_gen`, `lattice/spec/*`, goldens) empty diff | all | all |
| PX | wall | S static 0.2-0.3 s, dynamic 25-45 s (m 31 s); J per dynamic run 20-150 s; J-my 30-240 s | S <= 30 min; J <= 10 min per run |

**PJ predictions (p), report-only except the gates above.** Round-1 values (m) are given where the static facts do not
change.

| app | flows | verified / failed fraction | slot recall | skeletons lenient / strict (of 19) | JobRun states, static edges, initial | observed edges | sweeps |
|---|---|---|---|---|---|---|---|
| `-go` | 80 (75-100) | 0.20 (0.05-0.40) / 0.70 (0.45-0.90) | 16/24 (m, unchanged) | 0 (0-2) / 0 (0-1) | 5/5, 3/5, exact (m) | 0/5 (0-1); status `static` | none run (0-2) |
| `-min-spec-opus` | 74 (70-95) | 0.50 (0.15-0.80) / 0.40 (0.10-0.80) | 21/24 (m) | 3 (1-6) / 0 (0-2) | 5/5, 3/5, exact (m) | 1/5 `queued->cancelled` (0-1); `observed` | none (no services) |
| `-min-spec-sonnet` | 15 (15-25) | 0.50 (0.15-0.85) / 0.40 (0.10-0.80) | 21/24 (m) | 3 (1-6) / 0 (0-2) | 3/5, 1/5, exact (m) | 1/5 (0-1); `observed` | none |

Unverified is 0.00-0.10 everywhere, since J ids are auto-increment and reproduce on replay. `list` id sources number 0
(0-2) per app. J-my (`-go --database mysql`, 1 run, report-only) matches its SQLite run in flows and statuses (p) and
leaves 0 containers. The verified fraction would be 0 if the layer could not log in. The miss would show as Member
steps answering 401, which is WR3's catcher.

## 4. Mutants (`dogfood/workflow_mutants.py`, scratch copy; the control passes first; predicted 11/11 killed)

| id | mutant | killed by |
|---|---|---|
| WR1 | the check invocation is ignored (round-1 discovery) | PH (J -> static), PJ mode |
| WR2 | an unresolvable source falls through to `harness.py`, or the convention also accepts `build_seed_db` | PH fixtures `harness:nope`, `build_seed_db`-only |
| WR3 | `--login` from the invocation dropped (`login=None`) | PH refs; PJ (Member steps 401, verified 0) |
| WR4 | services read by name when `--services` is absent | PH (`-min-spec-*` services {} expected; undeclared-services fixture) |
| WR5 | the invocation's database ignored (extract dialect used) | PH (`database` sqlite on J; `--database mysql` fixture) |
| WR6 | `$VAR` values taken literally | PH fixture (static, reason) |
| WR7 | the `set -- ... "$@"` splice not followed | PH S' (static instead of check.sh) |
| WR8 | default list rule back to `first` | PL (68/59/1/8; `/v1/home` bound) |
| WR9 | `--list-rule first` behaves as `specific` | PL1 |
| WR10 | unbound path params filled with `1` | PU (> 0 on `-go`) |
| WR11 | a static run with `harness_reason` null, or a traceback in the reason | PH |

Round-1 WF1-WF13 are re-run as a regression: 13/13 still killed (p). WR1, WR3, WR8 and WR10 need one dynamic run each,
about 1-3 min; the rest are caught by fixtures in seconds.

## 5. Risks, and what this cannot show

1. **Not blind.** J was seen, and two of its files shape R2-D1. Mitigations: R2-D6/D7 freeze everything else, and PJs
   checks the static apps byte for byte. A blind claim needs an unseen app with a different check shape (a Makefile
   target, `pyproject` scripts, a harness under a subdirectory). Those shapes are not handled and would be static with
   a reason.
2. **Discovery parses shell text and does not run it.** Invocations built dynamically (functions, `eval`, variables in
   refs, wrapper scripts) are static by design. Only `-m lattice.spec check` is recognised.
3. **Imports run project code** (`harness.py`, `spec.seed`) in the layer's process, as the checker does. A module
   named `spec` cached from another root would shadow the app's. One process per app avoids that, and the risk is noted.
4. **`-go`'s machine stays unobserved.** The cause is a static gap (no producer for `job_configs`). Fixing it needs the
   extractor and that app's Go source, so it is out of scope. The services exist but have no row to move.
5. **Value synthesis may block J job flows.** A refused schedule field or other format would fail most `/jobs` flows,
   which is why the verified ranges are wide. Per R2-D7 that is a finding.
6. **Timing.** The `-go` sweeps, if any run, depend on the worker and scheduler clocks, which could break 10/10
   determinism there. S has a test clock. J dates follow the real day (determinism holds within a day).
7. **Cost.** S dynamic is 31 s per run (m). J is predicted at 20-150 s per run per app. The registered stage (10+10 S,
   10x3 J, J-my, PR for 4 apps, SIGINT x2, mutants including WF1-WF13) is predicted at **45-150 min**. The
   orchestrator's re-run may use 3 static and 2 dynamic runs, and must say so.
8. **Spike C** figures were computed from the round-1 (`first`) S output. They are not re-scored here. A changed default
   changes what a fresh `lattice import workflows` hands Spike C.

## 6. Blind vs post hoc, and commands

These predictions are fixed now. A miss is a **finding**, never silently fixed. Post hoc fixes go in separate labelled
commits with all probes re-run and deltas reported. Rulings that change R2-D1..D7 are recorded as an amendment before
code. The PL numbers were measured post hoc in round 1 and are predicted to reproduce; they are not new evidence for
`specific`.

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-b2 PYTHONDONTWRITEBYTECODE=1
B2=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/b2
DRV=$B2/../perfengines/perfvenv/bin/python; D=/Users/vivekgade/Documents
cd $D/lattice-b2; docker image inspect mysql:8.4 >/dev/null
"$DRV" dogfood/workflow_probes.py --round2 --streak-ref a0679e9 --users ann,ben --static-runs 10 --runs 10 \
  --app go=$D/job-scheduler-go@00dd1de --app mso=$D/job-scheduler-go-min-spec-opus@7d1a730 \
  --app mss=$D/job-scheduler-go-min-spec-sonnet@f12365b --app base=$D/job-scheduler-go-baseline@b8d9fe0 \
  --app mbo=$D/job-scheduler-go-min-base-opus@00cbf55 --app mbs=$D/job-scheduler-go-min-base-sonnet@7ac55d5 \
  --build "go=go build -o bin/server ./cmd/server" --build "mso=go build -o bin/server ./cmd/server" \
  --build "mss=go build -o bin/server ./cmd/server" --spec $D/job-scheduler@ef17cf8:spec.tickets:t5 \
  --j-users root,ann --j-runs 10 --j-mysql go --scratch "$B2/r2" --out "$B2/r2-data"
uv run python dogfood/workflow_mutants.py $(seq -f WR%g 1 11) $(seq -f WF%g 1 13)
uv run python -m pytest tests/importer tests/gocheck tests/spec tests/mcp -q -rs
uv run ruff check <changed .py>; uv run ruff format --check <created .py>; gofmt -l lattice/gocheck/src
```

`--spec DIR@REF:...` archives the spec repo into scratch. The runner archives every app (`git archive`, never a
checkout) and records `freeze.sha256` of the changed files and the scorer before the J stage.

**Pass:** every gated row of section 3, WR1-WR11 killed with WF1-WF13 still killed, and PB. The J rows are reported
against their ranges.

## 7. File ownership (build node)

**Owns (new):** `lattice/importer/check_invocation.py` (R2-D1/D2: parse, resolve, reasons);
`tests/importer/test_check_invocation.py` with `tests/importer/fixtures/check_invocation/` (fake `check.sh`/`studio.toml`
variants and tiny harness modules, one per PH fixture); the results doc and its data. **Edits:**
`lattice/importer/workflow_run.py` (`find_harness` delegates to the new module; database choice; `--list-rule`
default `specific`; R2-D4 in `render`; the `run` keys); `tests/importer/test_workflows.py` (additive; any changed
existing assertion is listed in the results); `dogfood/workflow_probes.py` (the `--round2` stage; `score_held_out` and
`spec_truth` unchanged); `dogfood/workflow_mutants.py` (WR1-WR11). **Not touched:** `goextract*.go`, `go_gin.py`,
`cli.py`, `workflows.py` (unless R2-D4 needs a planner flag: one function at most, listed), `mine/review/emit_spec/
appmodel/profile_gen/rules/transport`, `lattice/spec/*`, Spike C files, round-1 docs and data, the Streak checkout,
every `job-scheduler*` directory (archives only; R2-D7 reads only), and the main checkout. Nothing is committed with
`git add -A`; paths are staged explicitly.

## Amendment 1 (orchestrator rulings, 2026-10-16, before any code)

**Approved as written:** the discovery order (`studio.toml [app]`, then the check invocation parsed with `shlex`
and never executed, then the round-1 `harness.py` convention; once a source is found but cannot be resolved the
run is static with a stated reason); the database rule (explicit flag, then the invocation's `--database`, else
`sqlite`); `specific` as the new list-rule default with `--list-rule first` reproducing round 1 byte for byte;
rule R2-D4 (no silent ids: an unbound path parameter is never filled, the flow fails and names it); the new
`run.*` keys with the schema string unchanged; the builder's read allowance of exactly `check.sh` and
`harness.py` of the three harness apps (and the stage's own outputs), nothing under `spec/`, `cmd/`, `internal/`
and no `.go` file; fixes prompted by the job-scheduler runs only in discovery, database, import and teardown
plumbing, value/planner/extractor misses are findings. Not blind: these apps were seen in round 1, so every
number here is a development-set result and is labelled so.
**Rulings.** (1) The premise corrections stand: the list rule explains 3 of the 8 failed Streak flows; the
old rule's one spurious nudge guard pass is reported as such; job-scheduler checks run on SQLite so Docker
teardown bites on Streak plus one report-only MySQL run. (2) Spike C's round-1 numbers were taken from the
old-rule Streak output and are not re-scored by this round; the next Spike C round (C2) reads whichever
committed `workflows.json` it names. (3) Round-1 probes that this change legitimately moves (flow counts under
the new default) are re-registered in this document's predictions; any other round-1 probe or golden that
changes is a regression. (4) The orchestrator's re-run uses fewer dynamic runs and says so. (5) Docker
containers use the `lattice-wf-` prefix and must be 0 after every run; no writes into Streak, job-scheduler*
or the main checkout; no API key.
