# J0': parity matrix, Python / Go / .NET, re-graded after Go parity round 3 (Spike J, node G3, builder B2, K10)

Date: 2026-10-18. Document only: the grades below come from reading the tree and the registered results; no model was called and
no result was re-scored here. Supersedes `docs/results/2026-10-17-parity-matrix-j0.md` (J0, kept as written). Pre-registration:
`docs/results/2026-10-18-go-parity-round3-prereg.md` (K10, section 5.6 row P2-J0).

**Commit.** There is no "G3 commit" yet. This file was built in the builder worktree `lattice-g3b2` (branch `np-go3-b2`: `np-go3-b1`
`840bc75`, which holds G3 Tiers 0 and 1 and the Tier 1 build, plus B2's uncommitted K8). Every `file:line` below was resolved by a
script from the code at that state (a `grep` of a named symbol, never typed by hand). The orchestrator re-runs the check of section 6
on the merged G3 commit; a line that moved is a mechanical edit, a symbol that vanished is a finding.

Cell values (as J0): `S` supported, `P` partial (gap named), `A` absent. Every cell cites code (`file:line`) and a test or a results
row. **Python column**: no G3 change touches a Python path (PB-P, PB-N: byte-identical, prereg section 5.9), so each Python grade is
J0's, re-resolved and re-cited. **Go column**: re-graded from the tree and from the rounds 1, 2 and 3 documents. Go numbers from the
G3 build are development data (`dev`) unless the cell says otherwise; the blind rows (S4, Tier 4) are not run.

**.NET: absent in every row, by decision.** No front end for C# is registered (`lattice/importer/frontends/__init__.py:70` loads FastAPI, Django + DRF, Flask +
SQLAlchemy, Gin + GORM and gRPC-Go only), the stack switch is `lattice/spec/check.py:279` (Python frameworks) plus the `"go"` branch of `check.py`,
and code generation for C# is ruled out by the owner. Nothing in G3 adds a .NET capability; all 20 cells stay `A`.

## 1. Matrix

| # | capability | Python | Go (Gin + GORM) | .NET |
|---|---|---|---|---|
| 1 | Importer front end (detect / extract / runtime -> `RepoModel`) | **S**: registry `lattice/importer/frontends/__init__.py:49`; `lattice/importer/frontends/fastapi.py:2322`, `lattice/importer/frontends/django_drf.py:1574`, `lattice/importer/frontends/flask_app.py:2395`. T: `tests/importer/test_frontends.py`, `tests/importer/test_frontend_goldens.py` | **S**: `lattice/importer/frontends/go_gin.py:262` (`lattice/importer/frontends/go_gin.py:285`, `lattice/importer/frontends/go_gin.py:328`, `lattice/importer/frontends/go_gin.py:341`, `lattice/importer/frontends/go_gin.py:111`) over `lattice/gocheck/src/goextract*.go`, registered at `lattice/importer/frontends/__init__.py:80` beside the gRPC-Go front end `lattice/importer/frontends/go_grpc.py:261`. G3 widened what it reads: wrapped handlers (K1, `lattice/gocheck/src/routes.go:25`) and a request-body model per route (K3, `lattice/importer/go_rules.py:41`). T: `tests/importer/test_go_gin.py`, `tests/gocheck/test_go_wrap3.py`, `tests/importer/test_go_rules3.py`. R: Streak 26/26 routes (`docs/results/2026-10-15-go-frontend-results.md:35`). Limits: one HTTP framework; auth stored via `context.WithValue` reads as Public (`docs/results/2026-10-15-go-frontend-results.md:36`) | A |
| 2 | Behaviour lifting to spec programs (deterministic lifters + LLM close-the-gap) | **S**: `lattice/importer/lift.py:246`; `lattice/importer/lifters/__init__.py:1`; `lattice/importer/llm_lift.py:217`. T: `tests/importer/test_lifters.py`, `tests/importer/test_llm_lift.py` | **A**: lifting walks the Python package index (`lattice/importer/lift.py:273`); no Go lifter exists, so an imported Go app has no programs. Stays an owner decision outside G3 (`docs/results/2026-10-18-go-parity-round3-prereg.md:587`). T: `tests/importer/test_lifters.py` (Python idioms only) | A |
| 3 | Candidate-spec emission (rules-first import: `candidate_spec.py`, layer R on the original, corrections, mining, `proposals.json`, owner review) | **S**: `lattice/importer/rules.py:923`; `lattice/importer/emit_spec.py:877`; `lattice/importer/review.py:476`. T: `tests/importer/test_rules.py`, `tests/importer/test_emit_spec.py`, `tests/importer/test_review_cli.py` | **P** (J0: P; the gap moved): the import now runs end to end on a SQLite process app (Go branch `lattice/importer/rules.py:74`; the harness comes from `studio.toml`, `lattice/importer/go_rules.py:90`; owners from the extract, `lattice/importer/go_rules.py:113`; the Python-only `lattice/importer/rules.py:149` is not used). S2: 41 items, 25 verified (0.610), 0 refuted; S3: 63 items, 35 verified (0.556) before K3, 67 items / 0.597 after (`dev`; gate 0.60 **missed** as registered). On a server database (Streak, MySQL) the import completes with contracts and owners and writes `rules.status = not_run:server_dialect` (K4, `lattice/importer/rules.py:859`, detection `lattice/importer/go_rules.py:79`): no verified rules there. Gaps: no programs (row 2); no verified rules on server dialects (Q4, `docs/results/2026-10-18-go-parity-round3-prereg.md:588`). T: `tests/importer/test_go_rules.py`, `test_go_rules2.py`, `test_go_rules3.py`. R: `docs/results/2026-10-18-go-parity-gaps-round2-results.md:16`, `docs/results/2026-10-18-go-parity-gaps-round2-results.md:215`, `docs/results/2026-10-18-go-parity-round3-tier1-results.md:17`, `docs/results/2026-10-18-go-parity-round3-tier1-results.md:10` | A |
| 4 | Layer A static checks (`pycheck` / `gocheck`) | **S**: PY800-PY808 (`lattice/pycheck/findings.py:9`); policy derived from the original (`lattice/pycheck/derive.py:42`) and overlaid with the spec (`lattice/spec/check.py:298`). T: `tests/pycheck/test_pycheck_checks.py`, `tests/spec/test_check.py:57` | **P** (J0: P, "805 a real gap"): GO800-803, **GO805 (K8, new)**, 807, 808 are carried over (`lattice/gocheck/__init__.py:203`); GO804 and GO806 stay gated as moot on Gin/Go (`lattice/gocheck/__init__.py:214`). The policy is derived from the code (`lattice/gocheck/derive.py:95`, `lattice/gocheck/derive.py:152`, `lattice/gocheck/derive.py:144`) and GO805's answer is derived from the original (`lattice/gocheck/notowned.py:60`, `lattice/gocheck/notowned.py:80`, checked by `lattice/gocheck/notowned.py:89`, merged in layer A at `lattice/gocheck/__init__.py:461`, field `lattice/gocheck/__init__.py:256`, the structural link in the extractor at `lattice/gocheck/src/goextract_raise.go:681`). Gaps: `lattice check` still builds `GoPolicy.from_spec` only (`lattice/spec/check.py:369`; K9 not built, Q6, `docs/results/2026-10-18-go-parity-round3-prereg.md:591`), so derived owners and GO805's answer reach layer A only through a caller that passes them; GO805 carries PY805's not-owned half only (a Gin app declares no admin dependency) and reads only a lookup's refusal in the handler itself; GO801 judges only routes whose reach is complete (K1/K2 widened it: JM31, ST2, ST3 are caught; Streak's PATCH and completions-list defects are not, `docs/results/2026-10-18-go-parity-round3-prereg.md:38`, `docs/results/2026-10-18-go-parity-round3-tier1-results.md:18`); skipped with a note without a Go toolchain (`lattice/spec/check.py:373`). T: `tests/gocheck/test_gocheck_layer_a.py:34`, `tests/gocheck/test_gocheck_gates.py:21`, `tests/gocheck/test_go_derive.py`, `tests/gocheck/test_go_wrap3.py`, `tests/gocheck/test_go805.py:265`, `tests/gocheck/test_go805.py:279`. R: `docs/results/2026-10-18-go-parity-gaps-round2-results.md:69` | A |
| 5 | Runtime harness | **S**: in-process transports `lattice/importer/transport.py:125` (FastAPI TestClient, Django, Flask). T: `tests/importer/test_transport.py`, `tests/spec/test_check_runtime.py` | **S**: `lattice/spec/process.py:477` (`framework="go"`); `--runtime go` (`lattice/spec/__main__.py:6`). S1, S2 and S3 run SQLite process apps (`tests/spec/go_harness.py`, `tests/spec/gogin_harness.py`, `tests/spec/gotickets_harness.py`). T: `tests/spec/test_process_runtime.py` | A |
| 6 | Layer B (app vs the spec's programs) | **S**: `lattice/spec/check.py:1110`. T: `tests/spec/test_check.py` | **S** (J0: P): the code path is runtime-neutral and is now exercised on a Go app: S1 (`tests/spec/goapp` with `go_harness_rb`'s hand-written programs) gives 32 cases, 0 findings on the safe app and both existing bugs found on the right endpoint, 10 of 10 repeatable. T: `tests/spec/test_go_layers_rb.py:98`. R: `docs/results/2026-10-18-go-parity-gaps-results.md:26`, `docs/results/2026-10-18-go-parity-gaps-round2-results.md:73`. Limits: B is skipped on server dialects for every stack (`lattice/spec/check.py:2231`, Q4); an imported Go spec has no programs (row 2) | A |
| 7 | Layer C (invariants after every request) | **S**: `lattice/spec/check.py:520`. T: `tests/spec/test_invariants.py` | **S**: the same monitor, through the transport (`lattice/spec/check.py:520`). T: `tests/spec/test_process_runtime.py` (`test_b_layer_c_*`); Streak on MySQL via `dogfood/impact_probes.py` (`--layers ACD`). R: `docs/results/2026-10-15-go-frontend-results.md` | A |
| 8 | Layer D (scenarios, `Await` services) | **S**: `lattice/spec/check.py:1591`. T: `tests/spec/test_check.py`, `tests/spec/test_services.py` | **S**: the same runner (`lattice/spec/check.py:1591`). T: `tests/spec/test_process_runtime.py` (`test_a_scenarios_*`); Streak on MySQL via `dogfood/impact_probes.py`. R: `docs/results/2026-10-15-live-contract-go-mysql-results.md` | A |
| 9 | Layer X (races) | **S**: `lattice/spec/race.py:186`. T: `tests/spec/test_layer_x.py` | **S**: the same race runner (`lattice/spec/race.py:186`). T: `tests/spec/test_process_runtime.py` (`test_c_layer_x_*`), `tests/spec/goapp` (a racy and a safe variant) | A |
| 10 | Layer R (rule probes) | **S**: `lattice/spec/probes.py:3509`, `lattice/spec/check.py:1934`. T: `tests/spec/test_probes.py`, `tests/importer/test_mine.py` | **S** (J0: P): R runs on Go apps over SQLite: S1 23 items, 16 verified, 0 refuted, 10 of 10 identical (PR1); mutants JM1 and JM2 refute an item on the right route (PR2); S3's JM33 (a 403 for another caller's ticket) refutes its 404 item. T: `tests/spec/test_go_layers_rb.py:70`, `tests/spec/test_go_parity_s3.py:292`. R: `docs/results/2026-10-18-go-parity-gaps-round2-results.md:70`, `docs/results/2026-10-18-go-parity-gaps-round2-results.md:242`. Limits: skipped on server dialects for every stack (`lattice/spec/check.py:2231`); S3's JM34 (an unexercised 409) is invisible to R | A |
| 11 | Workflow layer (flows, machines, `workflows.json`) | **A**: `lattice/importer/workflow_run.py:919` hard-wires the Go front end; `lattice/importer/workflows.py:1`. K11 (a Python inventory) is split out of G3 (`docs/results/2026-10-18-go-parity-round3-prereg.md:585`). T: `tests/importer/test_workflows.py` (Go only) | **S**: `lattice/importer/workflow_run.py`, `lattice/importer/workflows.py`. T: `tests/importer/test_workflows.py`. R: `docs/results/2026-10-15-workflow-layer-results.md:1`, `docs/results/2026-10-16-workflow-layer-round2-results.md:1` | A |
| 12 | Implementation report (inventory, diff, MCP `implementation_get` / `workflow_get`, Studio `/api/implementation`) | **A**: the inventory is Go only (`lattice/importer/inventory.py:81`, `lattice/importer/inventory.py:87`); source set `*.go`, `go.mod`, `go.sum` (`lattice/spec/implementation.py:127`). T: `tests/importer/test_inventory.py`, `tests/spec/test_implementation.py` (Go fixtures) | **S**: `lattice/spec/implementation.py`, MCP `lattice/mcp/tools.py:1254`, `lattice/mcp/tools.py:1371`. T: `tests/spec/test_implementation.py`, `tests/importer/test_inventory.py`. R: `docs/results/2026-10-16-implementation-diff-round2-results.md:1` | A |
| 13 | Chat (`spec ask`) grounded in spec and code | **P**: the spec digest works for any stack; the `impl:` section needs an inventory (`lattice/spec/ask.py:58`) which Python cannot produce (row 12). T: `tests/spec/test_ask.py` | **S**: `lattice/spec/ask.py:300`. T: `tests/spec/test_ask_implementation.py`, `tests/spec/test_code_citation.py` | A |
| 14 | App map (Spike F, `lattice.appmap/1`) | **P**: spec cells only; state `inventory_missing` without an inventory (`lattice/spec/appmap.py:46`, `lattice/spec/appmap.py:613`). T: `tests/spec/test_appmap.py` | **S**: `lattice/spec/appmap.py`. T: `tests/spec/test_appmap.py`. R: `docs/results/2026-10-16-goto-place-results.md:1` | A |
| 15 | Change impact (`lattice spec impact`, MCP `change_impact`) | **P**: T1 observed and T2 determined run (`lattice/spec/impact_tier2.py`); T3 static is `not_run:inventory_missing`; T4 `not_run:spike_e` for every stack (`lattice/spec/impact_report.py:15`). T: `tests/spec/test_impact_report.py` | **S**: T1-T3 (`lattice/spec/impact_tier3.py:118`, reads `*.go`). T4 not built. T: `tests/spec/test_impact_report.py`. R: `docs/results/2026-10-16-change-impact-results.md:1` | A |
| 16 | Perf engines (`lattice perf`, `--explain`) | **S**: in-process SQLite counting and server engines (`lattice/perf/measure.py:29`). T: `tests/perf/test_measure.py`, `tests/perf/test_counting.py` | **P**: MySQL / Postgres counted at the server (Streak arm); a process app on SQLite is `unmeasured` (`lattice/perf/measure.py:37`). Not touched by G3. T: `tests/perf/test_measure.py`. R: `docs/results/2026-10-15-perf-missing-index-results.md:1` | A |
| 17 | Contracts (export, check, impact, draft, live) | **S**: spec-level, stack-neutral (`lattice/contracts/export.py:81`). T: `tests/contracts/test_export.py`, `tests/contracts/test_check.py` | **S**: same modules; live check on Go + MySQL (`lattice/contracts/live.py:72`). T: `tests/contracts/test_live_server.py`. R: `docs/results/2026-10-15-live-contract-go-mysql-results.md:1` | A |
| 18 | Improve loop (report, ledger, verify, draft, apply) | **S**: stack-neutral event log (`lattice/improve/__init__.py:1`). T: `tests/improve/test_analyze.py`, `tests/improve/test_apply.py` | **S**: same (`lattice/improve/__init__.py:1`); Streak plan records imported. T: `tests/improve/test_import.py`. R: `docs/results/data/streak-plans-2026-10-11/` | A |
| 19 | Build-from-spec (Studio rebuild loop) | **S**: project gates, any stack (`lattice/studio/rebuild.py:1138`). T: `tests/studio/test_rebuild.py`; Python dogfood `dogfood/studio/check.sh` | **S**: same loop; Streak builds with `prepare: go build` (`lattice/perf/budget.py:7`). T: `tests/studio/test_rebuild.py`. R: `docs/results/2026-10-11-perf-and-resume-results.md` | A |
| 20 | Code generation (spec programs to app code) | **S**: `lattice/emit/thin.py:198`, `lattice/t2/emit_py.py:545`, `lattice/api/codegen.py:238`. T: `tests/emit/test_thin.py` | **P**: the Go generator was removed 2026-10-12 (`lattice/gogen/__init__.py:4`); only the target gate remains (`lattice/targets.py:58`). Not a parity goal: codegen is ruled out and is used by no checker, importer or Studio path. T: `tests/emit/test_thin.py`. R: `docs/results/2026-10-17-go-parity-gaps-prereg.md` | A (ruled out) |

## 2. Cell counts

| status | Python | Go | .NET | total |
|---|---|---|---|---|
| supported | 15 | 15 | 0 | 30 |
| partial | 3 | 4 | 0 | 7 |
| absent | 2 | 1 | 20 | 23 |
| cells | 20 | 20 | 20 | 60 |

J0 counted Python 15 / 3 / 2 and Go 13 / 6 / 1. Python is unchanged. **Go gains two supported cells: rows 6 (layer B) and 10 (layer R)**,
both closed by the Go parity rounds 1 and 2 on SQLite apps (tests and results above) and not re-opened by G3. Rows 3 and 4 stay `P`
with their gap changed (row 3: the import exists but lifting and server-dialect rules are missing; row 4: GO805 exists but the
default `lattice check` does not derive a policy). Row 16 and 20 are as in J0.

## 3. What changed since J0 (Go column)

| row | J0 | J0' | what moved it (or did not) |
|---|---|---|---|
| 3 | P | P | rules-first import exists (J1, J2); S2 0.610, S3 0.556 -> 0.597 (`dev`, gate 0.60 missed); K4 gives a server-dialect app an honest `not_run` instead of a crash |
| 4 | P | P | derived policy (J1) and GO805 (K8, G3); the default `lattice check` is unchanged by Amendment 1 (Q6) |
| 6 | P | S | layer B tested on a Go app (S1: 32 cases, 0 findings, both bugs found) |
| 10 | P | S | layer R tested on Go apps (S1 PR1/PR2, S2 and S3 imports, JM33) |
| 1, 5, 7-9, 11-15, 17-19 | S | S | unchanged; K1-K3 widen what the extractor and layer A read |
| 2, 16, 20 | A, P, P | A, P, P | unchanged (Q3: lifting is an owner decision; SQLite process-app perf; codegen ruled out) |

## 4. Remaining gaps, ranked

| rank | gap (row) | size | note |
|---|---|---|---|
| 1 | Behaviour lifting (2) | L | owner decision (Q3); feeds programs for layer B on imported apps |
| 2 | Server-dialect layer R and B (3, 6, 10) | M | one port for every stack (Q4, a separate node); until then a MySQL Go app imports to `not_run:server_dialect` |
| 3 | Default `lattice check` on Go derives the policy (4) | S | K9 was not built (Q6): 10 lines in the frozen `check.py`; Arm M measured findings equal on Streak |
| 4 | GO801 reach on idioms not yet read (4) | M | interface calls (`d.Clock.Now`), function-typed parameters (`NewPage`), wrappers that call a method on their own parameter's error: silent by design |
| 5 | Perf on a process app over SQLite (16) | S | optional; MySQL is the Go target |
| - | Python behind Go: workflow layer, inventory, chat `impl:`, app map, impact T3 (11-15) | L | K11, its own pre-registered round (`docs/results/2026-10-18-go-parity-round3-prereg.md:585`) |
| - | Code generation (20), .NET | - | ruled out by the owner |

## 5. How to read the evidence

* A `file:line` is the first line of the named symbol at the commit; the section 6 check proves the file exists and the line is inside it.
* `T:` is a test file (or a test line) in the tree; `R:` is a results document (or a row) in the tree. The G3 build's own numbers are in
  `docs/results/2026-10-18-go-parity-round3-tier0-results.md` and `docs/results/2026-10-18-go-parity-round3-tier1-results.md` (development data); the blind rows are Tier 4's.
* The Go-column claims about S1-S3 imports and layers R and B are round 1 and 2 measurements (`dev`); G3 Tier 1 reports the same arms
  on the G3 tree in `docs/results/2026-10-18-go-parity-round3-tier1-results.md`.

## 6. Check (P2-J0; run by an independent verifier on the G3 commit)

```sh
python3 - <<'PY'
import re, sys
from pathlib import Path
doc = Path("docs/results/2026-10-18-parity-matrix-j0prime.md").read_text()
bad = []
for tok in set(re.findall(r"`([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.[a-z]+)(?::(\d+))?`", doc)):
    path, line = tok
    p = Path(path)
    if not p.is_file():
        bad.append(f"missing file {path}")
    elif line and not 1 <= int(line) <= len(p.read_text().splitlines()):
        bad.append(f"{path}:{line} outside the file")
rows = [l for l in doc.splitlines() if re.match(r"\| \d+ \| ", l) and len(l.strip("|").split("|")) == 5]
for l in rows:
    cells = [c.strip() for c in l.strip("|").split("|")]
    for name, cell in (("Python", cells[2]), ("Go", cells[3])):
        has_line = re.search(r"`[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.[a-z]+:\d+`", cell)
        has_ev = re.search(r"(T|R): ", cell) and re.search(r"`(tests|docs/results)/", cell)
        if not (has_line and has_ev):
            bad.append(f"row {cells[0]} {name}: needs a file:line and a test or results row")
print(len(rows), "rows;", len(bad), "problems"); [print(" ", b) for b in bad]; sys.exit(bool(bad))
PY
```

Section 7 lists, for every resolved citation, the source line it points at, so a verifier can see that the line still holds the symbol
the claim names. A symbol that moved or vanished is a finding; a line number that only shifted is a mechanical edit.

## 7. Citation index (what each resolved line holds)

| citation | the line |
|---|---|
| `lattice/importer/frontends/__init__.py:49` | `def register(front_end: FrontEnd) -> FrontEnd:` |
| `lattice/importer/frontends/__init__.py:80` | `from lattice.importer.frontends import go_gin, go_grpc  # noqa: F401 -- register` |
| `lattice/importer/frontends/fastapi.py:2322` | `class FastApiFrontEnd:` |
| `lattice/importer/frontends/django_drf.py:1574` | `class DjangoFrontEnd:` |
| `lattice/importer/frontends/flask_app.py:2395` | `class FlaskFrontEnd:` |
| `lattice/importer/frontends/go_gin.py:262` | `class GoGinFrontEnd:` |
| `lattice/importer/frontends/go_gin.py:285` | `def detect(self, root: Path, package: str) -> bool:` |
| `lattice/importer/frontends/go_gin.py:328` | `def extract(self, root: Path, package: str = ".") -> RepoModel:` |
| `lattice/importer/frontends/go_gin.py:341` | `def runtime(` |
| `lattice/importer/frontends/go_gin.py:111` | `def model_from_extract(data: Mapping[str, Any], package: str = ".") -> RepoModel:` |
| `lattice/importer/frontends/go_grpc.py:261` | `class GoGrpcFrontEnd(GoGinFrontEnd):` |
| `lattice/gocheck/src/routes.go:25` | `Wraps        []*Func // K1: the wrapper literals of the handler and middleware` |
| `lattice/importer/go_rules.py:41` | `* K3 ''body_model'': the extract hands a route's request-body fields over as scalar body` |
| `docs/results/2026-10-15-go-frontend-results.md:35` | `interface dispatch); 0 of 26 routes structurally unread.` |
| `docs/results/2026-10-15-go-frontend-results.md:36` | `6. Known limits: auth stored via 'context.WithValue' reads as Public; a route registered through a function` |
| `lattice/importer/lift.py:246` | `def lift_repo(` |
| `lattice/importer/lift.py:273` | `index = _Index(root, app_package)` |
| `lattice/importer/lifters/__init__.py:1` | `"""Deterministic lifters (spec 2026-09-27 importer §3.2, 2a): one module per idiom family,` |
| `lattice/importer/llm_lift.py:217` | `def parse_candidate(` |
| `docs/results/2026-10-18-go-parity-round3-prereg.md:587` | `- **Q3:** Go lifting (J0 row 2) stays an owner decision outside G3.` |
| `lattice/importer/rules.py:923` | `def import_rules(` |
| `lattice/importer/rules.py:149` | `def find_factory(package: str, spec: str \| None = None) -> tuple[Callable[[Path], Any], str]:` |
| `lattice/importer/rules.py:74` | `8. **Go** (Spike J, node J1; :mod:'lattice.importer.go_rules'). The same import for a Go` |
| `lattice/importer/rules.py:859` | `if self.not_run:  # K4: layer R did not run (no items, no corrections, no proposals)` |
| `lattice/importer/emit_spec.py:877` | `def candidate_spec(` |
| `lattice/importer/review.py:476` | `def main(argv: Sequence[str] \| None = None) -> int:` |
| `lattice/importer/go_rules.py:90` | `def harness_factory(root: Path \| str, package: str) -> tuple[Callable[[Path], Any], str] \| None:` |
| `lattice/importer/go_rules.py:79` | `def server_dialect(root: Path \| str, package: str) -> str \| None:` |
| `lattice/importer/go_rules.py:113` | `def infer_owners(` |
| `docs/results/2026-10-18-go-parity-gaps-round2-results.md:16` | `\| S2 import (dev) \| 41 items, 25 verified (0.610), 0 refuted, 16 unexercised (all with reasons), 0 correctio` |
| `docs/results/2026-10-18-go-parity-gaps-round2-results.md:215` | `\| PI3-3 \| 50-80 items; verified share 0.60-0.85; refuted 0-3; gate share >= 0.60 \| 63 items (in range); 35 ` |
| `docs/results/2026-10-18-go-parity-round3-tier1-results.md:17` | `\| PS-PI3-3 \| 63-70 items, share 0.60-0.68 \| 67 items, **0.597** (gate 0.60); 'R2 POST /tickets/{tid}/handof` |
| `docs/results/2026-10-18-go-parity-round3-tier1-results.md:10` | `S3 items verified), K4 (an honest 'not_run:server_dialect' skip when 'studio.toml [app] database' names a serv` |
| `lattice/pycheck/findings.py:9` | `CODES: dict[str, str] = {` |
| `lattice/pycheck/derive.py:42` | `def derive_policy(app_dir: Path, package: str, *, ownership: Ownership \| None = None) -> Policy:` |
| `lattice/spec/check.py:298` | `def spec_policy(` |
| `lattice/spec/check.py:369` | `pol = policy if isinstance(policy, GoPolicy) else GoPolicy.from_spec(spec)` |
| `lattice/spec/check.py:373` | `report.notes.append(f"layer A skipped: {err}")` |
| `lattice/gocheck/__init__.py:203` | `GO_CODES: dict[str, str] = {` |
| `lattice/gocheck/__init__.py:214` | `GO_GATED: dict[str, str] = {` |
| `lattice/gocheck/__init__.py:256` | `not_owned: int \| None = None  # the status the original answers another user's row with (GO805)` |
| `lattice/gocheck/__init__.py:461` | `from lattice.gocheck.notowned import not_owned_findings` |
| `lattice/gocheck/notowned.py:60` | `def derive_not_owned(` |
| `lattice/gocheck/notowned.py:80` | `def with_not_owned(policy: GoPolicy, module_dir: Path \| str) -> GoPolicy:` |
| `lattice/gocheck/notowned.py:89` | `def not_owned_findings(src_dir: Path \| str, policy: GoPolicy) -> list[Finding]:` |
| `lattice/gocheck/src/goextract_raise.go:681` | `func (g *gx) linkRaise(ci *closureInfo, b *Func, stack []ast.Node, n0 int) {` |
| `lattice/gocheck/derive.py:95` | `def derive_go_policy(` |
| `lattice/gocheck/derive.py:152` | `def go_spec_policy(spec: Spec, module_dir: Path \| str, policy: GoPolicy \| None = None) -> GoPolicy:` |
| `lattice/gocheck/derive.py:144` | `def undeclared_tables(` |
| `tests/gocheck/test_gocheck_layer_a.py:34` | `def test_layer_a_through_check():` |
| `tests/gocheck/test_gocheck_gates.py:21` | `def test_every_py_rule_is_carried_over_or_gated():` |
| `tests/gocheck/test_go805.py:265` | `def test_s3_copy_whose_get_answers_403_is_a_finding(tmp_path: Path):` |
| `tests/gocheck/test_go805.py:279` | `def test_go805_is_carried_over_not_gated():` |
| `tests/spec/test_check.py:57` | `def test_the_correct_app_passes_all_four_layers(apps):` |
| `docs/results/2026-10-18-go-parity-gaps-round2-results.md:69` | `\| PG805 \| GO805 gated: gated_note() and GO_GATED \| "byte-identical to base" \| {"identical": true, "pairing` |
| `docs/results/2026-10-18-go-parity-round3-prereg.md:38` | `\| Arm M post hoc \| a planted 'GET /v1/habits/{id}' that loads the habit without the 'user_id' filter is **no` |
| `docs/results/2026-10-18-go-parity-round3-tier1-results.md:18` | `\| M-SM1 \| 'GO801 GET /v1/habits/:id Habit' \| detected as 'GET /habits/:id' (no '/v1': the strict key fails)` |
| `lattice/importer/transport.py:125` | `class HttpxTransport:` |
| `lattice/spec/process.py:477` | `def process_runtime(` |
| `lattice/spec/__main__.py:6` | `[--x-k 12] [--x-repeats 3] [--x-budget 60] [--runtime mod:RUNTIME \| process \| go] \\` |
| `lattice/spec/check.py:1110` | `def _layer_b(` |
| `lattice/spec/check.py:1934` | `def _layer_r(` |
| `lattice/spec/check.py:2231` | `skipped = [x for x in "BR" if x in run]` |
| `lattice/spec/check.py:520` | `class InvariantMonitor:` |
| `lattice/spec/check.py:1591` | `def _run_scenario(` |
| `lattice/spec/race.py:186` | `def machine_violations(` |
| `lattice/spec/probes.py:3509` | `def run_probes(` |
| `tests/spec/test_go_layers_rb.py:98` | `def test_layer_b_is_quiet_on_the_safe_app(app):` |
| `tests/spec/test_go_layers_rb.py:70` | `def test_layer_r_verifies_the_safe_app_and_refutes_nothing(app):` |
| `docs/results/2026-10-18-go-parity-gaps-results.md:26` | `* Layer R and layer B on Go over SQLite (the user-visible gain, S1, 'tests/spec/goapp' byte-frozen): **R verif` |
| `docs/results/2026-10-18-go-parity-gaps-round2-results.md:70` | `\| PR1 \| layer R on S1 (go_harness_rb, the safe app) \| {"items": "18-30", "verified_share": "0.55-0.85", "re` |
| `docs/results/2026-10-18-go-parity-gaps-round2-results.md:73` | `\| PB1 \| layer B on S1, safe app \| {"cases": "28-40", "findings": 0} \| {"cases": 32, "findings": []} \| PAS` |
| `docs/results/2026-10-18-go-parity-gaps-round2-results.md:242` | `\| JM33 (get answers 403 for another caller's ticket) \| layer R: the 404 item of 'GET /tickets/{tid}' refuted` |
| `tests/spec/test_go_parity_s3.py:292` | `def test_jm33_answers_403_for_the_missing_ticket_and_only_there(module):` |
| `lattice/importer/workflow_run.py:919` | `from lattice.importer.frontends.go_gin import GoGinFrontEnd` |
| `lattice/importer/workflows.py:1` | `"""The workflow layer of the Go importer (Spike B): from what Spike A extracted plus the` |
| `docs/results/2026-10-15-workflow-layer-results.md:1` | `# Workflow layer (Spike B): results (Streak stage; held-out stage not run by the builder)` |
| `docs/results/2026-10-16-workflow-layer-round2-results.md:1` | `# Workflow layer, round 2: results (development set; builder run)` |
| `lattice/importer/inventory.py:81` | `from lattice.importer.frontends.go_gin import GoGinFrontEnd` |
| `lattice/importer/inventory.py:87` | `"frontend": "go_gin",` |
| `lattice/spec/implementation.py:127` | `def source_files(app_root: Path) -> list[str]:` |
| `lattice/mcp/tools.py:1254` | `def implementation_get(a: Mapping[str, Any]) -> dict[str, Any]:` |
| `lattice/mcp/tools.py:1371` | `def workflow_get(a: Mapping[str, Any]) -> dict[str, Any]:` |
| `docs/results/2026-10-16-implementation-diff-round2-results.md:1` | `# Implementation diff, round 2 (workflow_find ranking): results` |
| `lattice/spec/ask.py:58` | `**The implemented app** (Spike C, ''inventory=''). With an inventory` |
| `lattice/spec/ask.py:300` | `d = _with_impl(d, spec, trace, inventory)` |
| `lattice/spec/appmap.py:46` | `STATES = ("ok", "not_configured", "inventory_missing", "bad_inventory", "no_spec")` |
| `lattice/spec/appmap.py:613` | `state = "inventory_missing" if err.code == "inventory_missing" else "bad_inventory"` |
| `docs/results/2026-10-16-goto-place-results.md:1` | `# The go-to place (Spike F): results` |
| `lattice/spec/impact_report.py:15` | `* **T4 observed after the change** -- Spike E's; the slot is kept, ''not_run:spike_e''.` |
| `lattice/spec/impact_tier3.py:118` | `if p.name.endswith(".go") and not p.name.endswith("_test.go") and p.is_file()` |
| `docs/results/2026-10-16-change-impact-results.md:1` | `# Change impact engine (Spike D): results` |
| `lattice/perf/measure.py:37` | `* Not supported (the report says ''unmeasured''): a process app on SQLite (its SQLite` |
| `lattice/perf/measure.py:29` | `* Any other server engine whose descriptor has a ''[perf]'' table with ''counter = "sql"''` |
| `docs/results/2026-10-15-perf-missing-index-results.md:1` | `# Results: perf missing-index check and seeded-id capture (G14)` |
| `lattice/contracts/live.py:72` | `LIVE_DATABASES = ("sqlite", "mysql")` |
| `lattice/contracts/export.py:81` | `def endpoint_key(method: str, path: str) -> str:` |
| `docs/results/2026-10-15-live-contract-go-mysql-results.md:1` | `# Live contract check on a Go + MySQL provider (Streak): results` |
| `lattice/improve/__init__.py:1` | `"""Lattice improves itself from use (spike 3): an event log of usage signals` |
| `lattice/studio/rebuild.py:1138` | `class Rebuilder:` |
| `lattice/perf/budget.py:7` | `"prepare": "go build -o bin/streak-api ./cmd/server",      # optional, run in the project` |
| `lattice/gogen/__init__.py:4` | `its Go runtime under rt/) was removed on 2026-10-12 with the language/codegen direction (it` |
| `lattice/targets.py:58` | `# The kinds of T2 value the Go emitter implements ('lattice.gogen.types.kinds'): a` |
| `lattice/emit/thin.py:198` | `def join_sources(lattice_sources: Mapping[str, str] \| Sequence[str] \| str) -> str:` |
| `lattice/t2/emit_py.py:545` | `def _widen(fuel: str, value: ast.expr, src: T2Type, dst: T2Type) -> ast.expr:` |
| `lattice/api/codegen.py:238` | `def error(status: int, code: str, detail: str) -> ErrorResponse:` |
| `lattice/importer/frontends/__init__.py:70` | `def _load_others() -> None:` |
| `lattice/spec/check.py:279` | `PYTHON_FRAMEWORKS = frozenset({"fastapi", "flask", "django"})` |
| `docs/results/2026-10-18-go-parity-round3-prereg.md:585` | `- **Q2:** Tier 3 (K11, the Python inventory) is split into its own pre-registered round after G3 merges. G3 ga` |
| `docs/results/2026-10-18-go-parity-round3-prereg.md:591` | `- **Q6:** K9 not done: 'check.py' stays frozen and P2-DEF is reported, not gated (the owner may reopen it).` |
| `docs/results/2026-10-18-go-parity-round3-prereg.md:588` | `- **Q4:** the server-dialect layer R/B port is a separate node to be pre-registered later; no MySQL seed path ` |
