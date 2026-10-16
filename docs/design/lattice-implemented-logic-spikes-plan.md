# Plan: Lattice as a replica of the implemented business logic (Spikes A, B, C)

## Context
The owner wants Lattice to be an interface between the *intended* business logic (requirements / spec) and
the *implemented* business logic of an app, so the user can ask by chat "does workflow X exist in the app?"
and get an answer grounded in code and confirmed by running it. Target stack: Go + Gin + GORM + MySQL.

Findings from exploration (2026-10-15, next-phases 8e5e31f):
- The importer (`lattice/importer`) is Python-only: front ends FastAPI/Django/Flask behind a `FrontEnd`
  protocol (`frontends/__init__.py`) that returns a framework-neutral `RepoModel` (`appmodel.py`). Everything
  downstream (`emit_spec.py`, `mine.py`, `rules.import_rules`, `review.py`) consumes only `RepoModel` + DB
  snapshots, so a new front end gets candidate-spec emission, invariant/machine mining and owner review free.
- A Go design exists but was parked: `docs/archive/superpowers/plans/2026-10-07-go-and-db-import.md`
  (goextract helper -> AppModel JSON -> FrontEnd shim; nodes GOX/GOR/ENG/VEN/SQLX).
- `lattice/gocheck/src/*.go` (8.4k lines, stdlib only, go/ast) already loads packages, finds Gin routes
  (`routes.go: findRoutes/routerPrefix`), reads GORM models (`models.go`, `util.go: gormTag`), binding/validate
  tags (`bounds.go`), owner evidence (`owner.go`), status writes/guards (`machine.go`), call closure
  (`reach.go`). It is the foundation to extend.
- Streak's working tree on `main` is a skeleton. The implementation is `build/streak` a0679e9 (also present as
  a Studio rebuild under `.lattice/rebuilds/streak-b4-a93dcb`, which is an OLDER level-1 build): measured by
  Spike A at a0679e9: 26 route registrations (24 under `/v1`), 10 GORM tables (8 models + schema_migrations +
  test_clock), 1 state machine (Nudge.delivery); its spec has 26 endpoints, all implemented (0 spec-only). The
  spec-only case is commit 1d00ede (20 routes, 6 spec-only: home, sync, nudges, dismiss, both devices routes).
  (Corrected 2026-10-16; the first version of this plan said 20/17/4 from the older rebuild.)
- Streak idioms the extractor must handle (not textbook Gin): routes registered through
  `Routes{Public, Member}` struct fields and a `features ...Register` slice, handlers wrapped by
  `httpapi.Handle(h.create)`, validation via a hand-rolled `Body` map accessor + comparison guards (no
  `binding:` tags), errors via `httpapi.Invalid/Conflict/Unauthorized/NotFound`, ownership via
  `db.OwnedHabit(...)` helpers and `Where("id = ? AND user_id = ?")`.
- Chat today: `spec_ask` (`lattice/spec/ask.py`) answers only from a spec digest; `explain` mode reads code
  free-form (Read/Grep + `code_links` = trace+grep, "leads, not proof"). `POST /api/explain`, `/api/code/*`,
  `/api/flow` with `code:<path>:<line>` citations exist only in the mock server + API.md v1.3.
- Reusable for the diff: `ReviewData` rule status (`checked|unchecked|context|untraced|probe_gap`),
  `label_impact` (`not_built_yet`), `importer/intent.py disagreements()` ("code wins over notes"),
  `change_propose(request, selected, source)` -> owner-approved change; `accept` stays human-only.

Owner decisions (2026-10-15): first target Streak (build/streak a0679e9) with job-scheduler's Go port held
out; deterministic extraction only (no LLM in the extractor; unreadable idioms are listed, never guessed);
chat scope = report + read-only MCP tools + chat answers (no new Studio screen this round).

## Method for every spike (house protocol, run as an epg graph)
Pre-registration (opus: blind predictions, numeric pass rules, >= 8 mutants, risks, blind-vs-post-hoc rule)
-> orchestrator review + rulings -> commit prereg by explicit paths -> build node (sonnet, worktree, no
commit) -> independent orchestrator re-run -> results doc -> merge/push only on the owner's yes. No API key;
any model use through the `claude` CLI only. Never `git add -A` (owner's untracked files). Probes clone Streak
into scratch; never write into the owner's checkout.

## Spike A: Go front end (implemented-logic extraction)
Goal: `lattice import <go repo>` produces a `RepoModel`-shaped model + candidate spec for a Gin/GORM app.
- New Go program `lattice/gocheck/src` sibling or subcommand `goextract` (stdlib only; reuse `Load`, `findRoutes`,
  `Models`, `gormTag`, `Infer/FuncRef`) emitting JSON: tables (tags -> FieldSpec: nullable/unique/index/pk/
  default/size), routes (method, path, handler, middleware, auth Public|Member by group/middleware), request
  fields and bounds (binding/validate tags AND `Body`-accessor + comparison guards), raise sites (status,
  code, line) from error constructors, owner evidence (`Where(... user_id = ?)`, `Owned*` loaders), status
  writes (machine candidates), transactions.
- Needed extensions over gocheck: resolve router struct fields (`r.Member`), follow `features` slices through
  `Register` funcs, unwrap handler wrappers (`Handle(f)`), recognise project-declared error constructors by
  call-graph (a function that writes a status/abort), parse owner conditions from SQL strings.
- Python shim `lattice/importer/frontends/go_gin.py` (`detect`: go.mod with gin; `extract`: run goextract;
  `runtime`: ProcessApp via the project's harness, `lattice/spec/process.py`). Registered like the others.
- Output is byte-deterministic; anything unrecognised goes to `excluded`/`unread` with file:line.
Gates (numbers fixed in prereg): route recall/precision 100% on Streak a0679e9 vs the 20 registrations;
8/8 tables with column facts equal to `models.go`; every raise-site status matches the spec's scenarios for
the covered cells; the 4 unimplemented spec routes reported as spec-only; 10 identical runs; held-out
job-scheduler Go port scored once, frozen extractor, report-only (no tuning); dynamic reconcile: each
extracted route answered by the running app != 404 (route presence), via the existing process runtime;
>= 8 mutants (drop a group prefix, ignore wrapper, ignore Body guard, ...) killed; gocheck rules and tests/
importer byte-unchanged. Risk: idiom overfitting to Streak -> held-out app + `unread` list instead of guesses.
Files: lattice/gocheck/src/* (new file goextract.go), lattice/importer/frontends/go_gin.py, tests/importer/,
tests/gocheck/, dogfood/importer_go_probes.py.

## Spike B: Workflow layer (implemented flows, observed)
Goal: from the A model + running the app, produce a workflow model: state machines and multi-step flows,
each with code evidence and an `observed` flag.
- Machines: status/enum column writes + guards (gocheck `machine.go`) -> `Machine` candidates, confirmed by
  `importer/mine.py` observed transitions (reuse `transition_maps`, `Machine`).
- Flows: build a route-dependency graph (response id feeds a later path param; ownership chain; precondition
  guards) -> candidate `Scenario` step lists (none synthesised today: `review.py` blanks scenarios). Run each
  on a throwaway DB (Docker MySQL via existing `dbserver.py`); mark `verified` only when the steps pass.
- Output `workflows.json`: name, steps (method path expect), code locations, source static|observed,
  status verified|unverified|failed. Inferred names are labelled inferred.
Gates: Streak known flows recovered (register -> login -> create habit -> check in -> progress; batch
completion; device register; the Nudge delivery machine) >= predicted recall, 0 flows marked verified that
fail on replay, 10 identical runs, held-out job-scheduler flows scored once, >= 8 mutants, Docker teardown
clean. Files: lattice/importer/workflows.py, scenario synthesis stage between `mine` and `emit_spec`.

## Spike C: Two-model diff + chat grounded in both
Goal: answer "required vs implemented" and "does workflow X exist?" with evidence.
- Report `implementation_report` (new module, extends `ReviewData` gaps): per rule -> linked implemented
  elements; statuses: implemented+verified | implemented-unverified | required-not-implemented
  (`not_built_yet`) | implemented-not-required (route/workflow with no rule link; trace has no reverse index,
  add one). Code evidence `code:<path>:<line>`.
- Read-only MCP tools `implementation_get`, `workflow_get`, `workflow_find(question)`; Studio read routes
  (`GET /api/implementation`, API v1.22, mock parity) — no screen this round.
- Chat: add an implemented-inventory digest to `ask.py build_digest`, `code:` citation kind with a
  deterministic checker (file/line/symbol exists, quote matches) as specified in API.md v1.3; answer kinds
  gain `implemented`/`not_implemented`; `verify` runs the workflow's scenario to confirm. Findings become
  owner-approved changes via `change_propose(source={server:"extractor", item})`; accept stays human-only.
Gates: a frozen question set on Streak with known answers (exists / exists-but-unverified / spec-only /
absent) scored by a hash-frozen scorer, claims checked against files; real-agent exercise via the `claude`
CLI with USD/turn caps and NOT RUN on a usage limit; no-write probe; older commands byte-identical;
>= 8 mutants. Files: lattice/spec/implementation.py, lattice/mcp/tools.py, lattice/spec/ask.py,
lattice/studio/server.py (+ mock_server.py, API.md), tests, dogfood probes.

## Order and dependencies
A -> B -> C (B consumes A's model; C consumes both). Pre-registrations for A first; B and C
pre-registrations can be written while A builds, then rebased on A's model. Estimated nodes: 3 preregs, 3
builds, verification, one integration branch, one handoff.

## Verification (end to end)
Per spike: pytest of new suites + `tests/importer tests/gocheck tests/spec tests/mcp tests/studio` unchanged;
probe runner 10x identical; mutants killed; `dogfood/studio/check.sh` PASS; Streak and job-scheduler cloned
into scratch only. Final: `lattice import <streak clone>` -> report shows 17 routes, 8 tables, Nudge machine,
the 4 spec-only routes; chat asks "is there a home or sync endpoint?" -> not implemented (cited), "can I
check in a habit?" -> implemented, verified by a run.

## Out of scope / risks
LLM extraction (deferred by decision); non-Gin Go frameworks; Studio screens; races/concurrency.
Main risk: extractor tied to Streak idioms (mitigated by held-out app + `unread`). A replica is only as good as
the extraction: every statement carries evidence and an inferred/observed label.
