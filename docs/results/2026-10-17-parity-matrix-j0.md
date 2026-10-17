# J0: parity matrix, Python / Go / .NET (Spike J, node J0)

Date: 2026-10-17. Read-only node. Built from the repository at the current working tree (no code run, nothing
edited but this file). Plan: `docs/design/lattice-implemented-logic-spikes-plan.md`, Addendum "Spike J".

Cell values: `S` supported, `P` partial (gap named), `A` absent. Every cell cites the file that proves it.

**.NET: absent in every row.** No `*.csproj`/`*.cs` handling, no Roslyn helper, no front end registered in
`lattice/importer/frontends/__init__.py` (only FastAPI, Django + DRF, Flask + SQLAlchemy, Gin + GORM), and
`PYTHON_FRAMEWORKS` / the `"go"` branch in `lattice/spec/check.py:270,393` are the only stack switches.
Code generation for C# is ruled out by the owner, so that cell is `A` by decision.

## 1. Matrix

| # | capability | Python | Go (Gin + GORM) | .NET |
|---|---|---|---|---|
| 1 | Importer front end (detect / extract / runtime -> `RepoModel`) | S: `lattice/importer/frontends/__init__.py:51-101` (registry); `fastapi.py`, `django_drf.py`, `flask_app.py`; `tests/importer/test_frontends.py`, `test_frontend_goldens.py` | S: `lattice/importer/frontends/go_gin.py:285` detect, `:328` extract, `:341` runtime, over `lattice/gocheck/src/goextract*.go`; `tests/importer/test_go_gin.py`; Streak 26/26 routes, held-out 1.00/1.00 (`docs/results/2026-10-15-go-frontend-results.md`). One framework only; auth via `context.WithValue` reads as Public (results, finding 6) | A |
| 2 | Behaviour lifting to spec programs (deterministic lifters + LLM close-the-gap) | S: `lattice/importer/lift.py:246` `lift_repo`; `lattice/importer/lifters/*`; `lattice/importer/llm_lift.py`; `tests/importer/test_lifters.py`, `test_llm_lift.py` | A: lifting walks the Python package index and AST (`lift.py:273` `_Index(root, app_package)`, `:294` `route_function`, `lifters/base.py`); no Go lifter exists, so an imported Go app has no programs | A |
| 3 | Candidate-spec emission (rules-first import: `candidate_spec.py`, layer R on the original, corrections, mining, `proposals.json`, owner review) | S: `lattice/importer/rules.py` `import_rules`; `lattice/importer/emit_spec.py` `candidate_spec`; `lattice/importer/review.py`; `tests/importer/test_rules.py`, `test_emit_spec.py`, `test_review_cli.py` | P: `emit_spec.build_spec` is reached only inside the workflow replay (`lattice/importer/workflow_run.py:1242`); `--rules-only` needs a Python `make_app` (`rules.py:141-159` `find_factory` imports a Python module); no Go import directory, so no review; the generated profile has no ownership entries when GORM has no association tags (`goextract_tables.go:3-4`; go-frontend results, finding PS) | A |
| 4 | Layer A static checks (`pycheck` / `gocheck`) | S: PY800-PY808 (`lattice/pycheck/findings.py:9-19`); policy derived from the original code (`lattice/pycheck/derive.py`) and overlaid with the spec (`lattice/spec/check.py:289` `spec_policy`, `:384`); `tests/pycheck/*`, `tests/spec/test_check.py:73` | P: GO800-803, 807, 808 (`lattice/gocheck/__init__.py:203-210`); GO804/805/806 gated (`:213-222`): 804 and 806 moot on Gin/Go, **805 a real gap** ("no Go derivation exists yet"); policy from the spec only (`GoPolicy.from_spec`, `:260`), no derivation from code (no owner/private inference, no baseline of the original's own findings); skipped with a note without a Go toolchain (`check.py:363-365`). Tests `tests/gocheck/test_gocheck_layer_a.py`, `test_gocheck_gates.py:21` | A |
| 5 | Runtime harness | S: in-process transports `lattice/importer/transport.py` (FastAPI TestClient, Django, Flask); `tests/importer/test_transport.py`, `tests/spec/test_check_runtime.py` | S: `lattice/spec/process.py:340` `process_runtime(framework="go")`; `--runtime go` (`lattice/spec/__main__.py:264-267`); `tests/spec/test_process_runtime.py`, `tests/spec/goapp/main.go` | A |
| 6 | Layer B (app vs the spec's programs) | S: `lattice/spec/check.py:1101` `_layer_b`; `tests/spec/test_check.py:57` | P: the code path is runtime-neutral, but no test runs B on a Go app (`tests/spec/test_process_runtime.py` uses D, C, X, A only; `tests/spec/go_harness.py` has no programs); imported Go specs have no programs (row 2); B is skipped on server dialects (`check.py:69-71`) and Go apps run MySQL (Streak) | A |
| 7 | Layer C (invariants after every request) | S: `check.py` `InvariantMonitor`; `tests/spec/test_invariants.py` | S: through the transport (`check.py:79-81`); `tests/spec/test_process_runtime.py:94,106,134`; MySQL on Streak via `dogfood/impact_probes.py:236` (`--layers ACD`) | A |
| 8 | Layer D (scenarios, `Await` services) | S: `check.py:1573` `_run_scenario`; `tests/spec/test_check.py`, `tests/spec/test_services.py` | S: `tests/spec/test_process_runtime.py:75,82`; Streak MySQL `dogfood/impact_probes.py:236` | A |
| 9 | Layer X (races) | S: `lattice/spec/race.py`; `tests/spec/test_layer_x.py` | S: `tests/spec/test_process_runtime.py:114,128` | A |
| 10 | Layer R (rule probes) | S: `lattice/spec/probes.py:3509` `run_probes`, `check.py:1798` `_layer_r`; `tests/spec/test_probes*.py`, `tests/importer/test_mine.py:595` | P: runtime-neutral code, but no test or results doc runs R on a Go app; skipped on server dialects (whole-table SQLite diffs, `check.py:69-71`); no rules-first import for Go (row 3) | A |
| 11 | Workflow layer (flows, machines, `workflows.json`) | A: `lattice/importer/workflow_run.py:918-926` hard-wires `GoGinFrontEnd` and reads goextract JSON; `:954` `"framework": "gin"`; `lattice/importer/workflows.py:1` ("the workflow layer of the Go importer"); CLI says Go only (`workflow_run.py:1647`) | S: `workflow_run.py`, `workflows.py`; `tests/importer/test_workflows.py`; `docs/results/2026-10-15-workflow-layer-results.md`, `2026-10-16-workflow-layer-round2-results.md` | A |
| 12 | Implementation report (inventory, diff, MCP `implementation_get` / `workflow_get` / `workflow_find`, Studio `/api/implementation`) | A: the inventory is Go only: `lattice/importer/inventory.py:81-87` (`GoGinFrontEnd`, `"frontend": "go_gin"`), source set `*.go`, `go.mod`, `go.sum` (`lattice/spec/implementation.py:125-141`), refuses a non-goextract document (`:289`) | S: `lattice/spec/implementation.py`, `lattice/mcp/tools.py:1254,1371,1389`; `tests/spec/test_implementation.py`, `tests/importer/test_inventory.py`; `docs/results/2026-10-16-implementation-diff-round2-results.md` | A |
| 13 | Chat (`spec ask`) grounded in spec and code | P: the spec digest works for any stack; the `impl:` section and the `implementation` answer keys need an inventory (`lattice/spec/ask.py:58-63,299`), which Python cannot produce (row 12) | S: `ask.py:299` `_with_impl`; `tests/spec/test_ask_implementation.py`, `tests/spec/test_code_citation.py` | A |
| 14 | App map (Spike F, `lattice.appmap/1`) | P: spec cells only; state `inventory_missing` without an inventory (`lattice/spec/appmap.py:46,125`) | S: `appmap.py`; `tests/spec/test_appmap.py`; `docs/results/2026-10-16-goto-place-results.md` | A |
| 15 | Change impact (`lattice spec impact`, MCP `change_impact`) | P: T1 observed and T2 determined run (`lattice/spec/impact_report.py`, `impact_tier2.py`); T3 static is `not_run:inventory_missing` (`impact_report.py:183-197,918-923`); T4 `not_run:spike_e` for every stack | S: T1-T3 (`impact_tier3.py:53,118`, reads `*.go`); `tests/spec/test_impact_report.py`; `docs/results/2026-10-16-change-impact-results.md`. T4 not built (Spike E prereg only) | A |
| 16 | Perf engines (`lattice perf`, `--explain`) | S: in-process SQLite counting and server engines (`lattice/perf/measure.py:25,376-379`); `tests/perf/test_measure.py`, `test_counting.py` | P: MySQL / Postgres counted at the server (Streak arm, `docs/results/2026-10-15-perf-missing-index-results.md:16-19`); a process app on SQLite is `unmeasured` (`measure.py:37-38,474`) | A |
| 17 | Contracts (export, check, impact, draft, live) | S: spec-level, stack-neutral (`lattice/contracts/export.py`, `check.py`, `impact.py`, `draft.py`); `tests/contracts/*` | S: same modules; live check on Go + MySQL (`lattice/contracts/live.py`, `LIVE_DATABASES`; `tests/contracts/test_live_server.py`; `docs/results/2026-10-15-live-contract-go-mysql-results.md`) | A |
| 18 | Improve loop (report, ledger, verify, draft, apply) | S: stack-neutral event log (`lattice/improve/*`); `tests/improve/*` | S: same; Streak plan records imported (`tests/improve/test_import.py`, `docs/results/data/streak-plans-2026-10-11/`) | A |
| 19 | Build-from-spec (Studio rebuild loop) | S: `lattice/studio/rebuild.py` (project gates, any stack); `tests/studio/test_rebuild.py`; Python dogfood `dogfood/studio/check.sh` | S: same loop; Streak builds (`docs/results/data/streak-plans-2026-10-11/`, perf budget `prepare: go build`, `lattice/perf/budget.py:7`) | A |
| 20 | Code generation (spec programs to app code) | S: `lattice/emit/thin.py`, `lattice/t2/emit_py.py`, `lattice/api/codegen.py` | P: the Go generator was removed 2026-10-12 (`lattice/gogen/__init__.py:3-8`); only the target gate remains (`lattice/targets.py:100-110`, `lattice/check/p8_target.py`). Not a parity goal: the spike plan rules codegen out (item 3) and it is not used by checker, importer or Studio | A (ruled out) |

## 2. Cell counts

| status | Python | Go | .NET | total |
|---|---|---|---|---|
| supported | 15 | 13 | 0 | 28 |
| partial | 3 | 6 | 0 | 9 |
| absent | 2 | 1 | 20 | 23 |
| cells | 20 | 20 | 20 | 60 |

## 3. Go-behind-Python gaps (in scope for Spike J), ranked by size

| rank | gap (row) | size | what closing it takes | before .NET? |
|---|---|---|---|---|
| 1 | Behaviour lifting (2) | L | Go-AST lifters to Lattice programs (the Python lifters are AST-bound) and Go source in the LLM close-the-gap prompts; feeds layer B on imported apps | No: the largest piece and the least needed by the Go flows Streak uses; decide scope with the owner (row 6 can be closed without it) |
| 2 | Candidate-spec emission, rules-first import, owner review (3) | M | `import_rules` with a front end whose app factory is the harness (`studio.toml [app]`), not a Python `make_app`; write `candidate_spec.py` / `proposals.json` for a Go app; owner inference without GORM association tags (from the extract's `owner_uses`) | Yes: mostly wiring over the existing `RepoModel` path, and it is the template the .NET front end will follow |
| 3 | Layer A policy derivation and GO805 (4) | M | derive owners/private from the extract (the counterpart of `pycheck/derive.py`), the baseline of the original's own findings, and an error-convention (GO805) derivation for Gin | Yes for derivation (small Python over the extract); GO805 may stay gated with its reason |
| 4 | Layer R on Go (10) | M | tests on `tests/spec/goapp` (SQLite); for MySQL, R's whole-table diffs through the database adapter (the same skip affects every server-dialect app) | Yes on SQLite; the server-dialect part benefits all stacks |
| 5 | Layer B on Go (6) | S-M | a Go test with hand-written programs on SQLite; the server-dialect skip as in rank 4; full value on imports needs rank 1 | Yes on SQLite |
| 6 | Perf on a process app over SQLite (16) | S | count statements of an out-of-process SQLite app (or document MySQL/Postgres as the Go path) | Optional; MySQL is the Go target and works |
| - | Code generation (20) | - | out of scope by the owner's ruling | No |

Recommendation: close ranks 2, 3 (derivation), 4 and 5 (SQLite) as their own node before .NET; they reuse the
`RepoModel` and runtime paths .NET will need. Rank 1 is a separate owner decision.

## 4. Also found: Python behind Go (not in the plan's stated scope)

Rows 11-15 run only on a goextract inventory: workflow layer and implementation report are `A` for Python, and
chat, app map and change impact T3 are `P` for the same reason. "All three columns reach the same row set"
requires a Python inventory (an extract of the Python `RepoModel` in the shape `implementation.py` and
`workflows.py` read). Size L. Flagged for the owner; the J pre-registration should state whether it is in scope.

Not in the matrix: Spike E (implement with verification) and Spike H (typed conditions) exist only as
pre-registrations (`docs/results/2026-10-17-implement-verify-prereg.md`, `2026-10-17-typed-conditions-prereg.md`),
so no stack supports them yet.
