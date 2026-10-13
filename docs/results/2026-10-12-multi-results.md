# Multi-project layers 1 and 2: results

Date: 2026-10-12. Branch `np-multi` (worktree `lattice-npm`, from `next-phases` ca20d4a).
Pre-registration: `docs/results/2026-10-12-multi-prereg.md` (written before any code). Raw data:
`docs/results/data/2026-10-12-multi/`. No model call was made (no API key, no Claude CLI); no
Docker container was used. Streak (`/Users/vivekgade/Documents/streak`) and
`/Users/vivekgade/Documents/lattice` were only read: `git status` of both is as before the
build, and Streak's `.lattice/state` timestamps are unchanged (Oct 11).

## What was built

**Layer 1: one MCP server, many projects.**

- `python -m lattice.mcp serve [--project DIR ...] [--workspace PATH]`: `--project` is repeatable
  and optional (none = the launch directory plus discovery).
- `lattice/mcp/projects.py` (new): `known_projects` (who is known) and `ProjectRouter`. The router
  has the `Toolbox` interface, so `McpServer` serves it unchanged.
- Tools `project_list` and `project_set_active` (always listed, also with `--readonly`). Every
  existing tool takes an optional `project` (a name or a path). `workspace_impact` is listed only
  when a workspace is known.
- Discovery is Studio's own: `hub.discover` now calls a new `hub.discover_sourced`, which returns
  each project's source. The MCP server calls the same function. Nothing was copied, and the hub
  tests pass unchanged.
- Guard (`lattice/mcp/guard.py`): `--project` is optional and repeatable. A new `MultiGuard`
  checks each path against its owning project (`owner_of`: the nearest `spec.trace.json`, walking
  up, stopping at a `.git` that holds no trace). The explicit projects keep their old protection.
  The deny reason now names the project.

**Layer 2: the workspace manifest.**

- `lattice/workspace.py` (new, stdlib `tomllib` only): `parse`, `load`, `try_load`, `find`,
  `Workspace.impact_of`, typed errors (`ManifestUnreadable`, `ManifestInvalid`) and problem codes.
- CLI `python -m lattice.workspace show|validate|impact NAME [--json]` and `lattice workspace ...`.
  Exit codes: 0 ok, 1 problems, 2 cannot read.
- MCP: with a workspace, `project_list` carries each project's role and links, and
  `workspace_impact` is listed.
- Studio: `GET /api/workspace` (API v1.16; new section in `API.md`) and `lattice studio
  --workspace`. With no DIR, the workspace's projects join hub discovery.
- Mock parity in `mock_server.py`.
- Dogfood rules R90-R93, one new scenario, and the harness writes a manifest next to each
  scenario's project.
- Web: the project switcher shows manifest names, and the Map has a Workspace level.

### Files changed (lines)

| File | Change |
|---|---|
| `lattice/workspace.py` | new, 432 |
| `lattice/mcp/projects.py` | new, 346 |
| `lattice/mcp/guard.py` | +133 / -17 |
| `lattice/mcp/server.py` | +45 / -12 |
| `lattice/mcp/__main__.py` | +25 / -5 |
| `lattice/studio/hub.py` | +96 / -16 |
| `lattice/studio/server.py` | +30 / -4 |
| `lattice/studio/mock_server.py` | +46 |
| `lattice/studio/API.md` | +32 / -1 (v1.16 section) |
| `lattice/cli.py` | +7 / -1 |
| `lattice/studio/web/js/{api,store,app}.js`, `views/map.js` | +6, +1, +30 / -7, +53 / -3 |
| `dogfood/studio/{REQUIREMENTS.md,spec.py,spec.trace.json,harness.py}` | +7, +34, +35 / -1, +41 |
| `tests/mcp/test_server.py` | +4 / -2 (the two tool-list assertions, required by decision 2) |
| `tests/mcp/test_multi_server.py` | new, 384 (18 tests) |
| `tests/mcp/test_multi_guard.py` | new, 203 (31 tests) |
| `tests/workspace/test_workspace.py` | new, 297 (25 tests) |
| `tests/workspace/test_studio_api.py` | new, 260 (13 tests) |
| `dogfood/multi_mutants.py` | new, 202 |
| `docs/results/2026-10-12-multi-{prereg,results}.md`, `data/2026-10-12-multi/` | new |

## Blind results (against the pre-registered criteria)

### MP-1: two real projects, one server. PASS

ONE `python -m lattice.mcp serve` with no `--project`, launched in a scratch directory whose
manifest names `streak` (a `git clone` of Streak, HEAD a0db033) and `studio` (a copy of
`dogfood/studio`). It was driven over its real stdio pipe, one message at a time, in three
separate processes. Each run passed all four checks (`mp1-run{1,2,3}.json`):

1. `project_list` listed `launch` (source `launch`, spec `missing`), then `streak` and `studio`
   (source `workspace`, spec `present`).
2. `model_get {project: "streak"}` returned `project: "streak"`, `rule_count` 148 and Streak's R1
   ("Streak is a mobile habit tracker ..."). `model_get {project: "studio"}` returned
   `project: "studio"`, 93 rules and dogfood's R1.
3. After `project_set_active {name: "streak"}`, `model_get` with no `project` returned Streak's
   overview. After `{name: "studio"}`, `rule_get {id: "R1"}` returned dogfood's R1 with
   `project: "studio"`.
4. `model_get {project: "nope"}` and `project_set_active {name: "nope"}` were `isError` results:
   `unknown project 'nope'; known projects: launch, streak, studio (project_list shows their
   paths)`.

Byte compatibility (`mp1-compat.json`): a single-`--project` server on the `dogfood/studio` copy
was compared with the old code (run from `/Users/vivekgade/Documents/lattice`, HEAD ca20d4a,
`lattice/mcp` unmodified, `PYTHONDONTWRITEBYTECODE=1`). All four checks passed:

- (a) `initialize` was byte-identical.
- (b) `tools/list` was byte-identical once the two appended tools were removed, and the old tool
  array is a byte prefix of the new one.
- (c) `model_get {}` and `project_status {}` were byte-identical.

The predicted risk (Streak's `harness.py` imports) did not happen: the model loaded.

### MP-2: guard across projects. PASS, 10 of 10

The real hook ran with `--project /Users/vivekgade/Documents/streak --project
<worktree>/dogfood/studio` (`mp2-guard.json`). All 10 pre-registered cases came out as expected:

- Cases 1, 2 and 9 were denied with "spec.py of project streak (/Users/vivekgade/Documents/streak)".
- Cases 3, 4 and 10 were denied naming `lattice-studio`.
- Cases 5 to 8 were allowed.

The same 10 cases with no `--project` at all (reported, not part of the pass): **10 of 10**, by
the nearest-trace rule alone. Prediction: 10 of 10.

### MP-3: workspace. PASS

**Tests** (`tests/workspace`, 38 tests):

- `validate` accepts the provider/consumer/library manifest (exit 0).
- Each error class is rejected with its own code and message, both by the parser and by the CLI
  (exit 1): `unknown_project` ("ghost is not a declared project"), `duplicate_name` ("web-app is
  declared twice"), `self_link` ("cannot link to itself"), `depends_on_cycle` ("depends_on links
  form a cycle: a -> b -> a").
- A missing directory is a `missing_path` warning (exit 0).
- `impact_of("shared-auth")` = {web-app, billing-api}, `impact_of("web-app")` = [] and
  `impact_of("billing-api")` = [web-app]. A three-hop chain also resolves.
- `GET /api/workspace` on the real server answers the same projects, links and problems. With no
  manifest it answers `workspace: null` and empty lists. An unreadable manifest answers the
  `unreadable` problem. The mock matches the real keys and types.

**One real-server run** (`mp3-cli.txt`, `mp3-real-server.txt`), three scratch copies of the seed
project:

- `lattice workspace validate` was ok (exit 0). `impact shared-auth --json` gave
  `["web-app", "billing-api"]` and `impact web-app` gave `[]`.
- `python -m lattice.studio ../billing` (started in `mp3/web`, manifest found by walking up):
  - `GET /api/workspace` listed all three projects. `billing-api` was `in_hub: true`,
    `hub_name: "billing"`; the other two were `in_hub: false`. All three links were listed, with
    no problems.
  - `/p/billing/api/workspace` gave the same answer.
  - `POST /api/workspace` answered `404 {"detail": "no such route"}`.
- The same server started in a directory with no manifest above it answered `{"workspace": null,
  "projects": [], "links": [], "problems": []}`.

### MP-4: no regression. PASS

- Verification suite: **1400 passed, 5 skipped, 0 failed** in 430 s (baseline 1313 passed, 5 skipped; +87 new tests).
- `dogfood/studio/check.sh`: **PASS, 0 findings, 408 cases** (baseline 403; +5 for the new
  scenario's steps), 49 scenarios.
- `scripts/restart_drill.py`: **A, B, C and F PASS** (47 checks ok, 0 failed).
- Studio chat sessions still get one fixed project: `lattice/studio/session.py` is not modified,
  and `tests/studio/test_session.py` is part of the suite.

## Numbers

| Check | Result |
|---|---|
| Verification suite (`tests/workspace tests/improve tests/security tests/modules tests/perf tests/studio tests/mcp tests/test_cli.py tests/spec`, minus test_db/test_multidb) | **1400 passed, 5 skipped, 0 failed**, 430 s (`pytest-full.txt`) |
| New tests | 87: tests/workspace 38, tests/mcp/test_multi_server.py 18, tests/mcp/test_multi_guard.py 31 |
| Dogfood gate | PASS, 408 cases, 0 findings, 49.7 s on the final code (`dogfood-check.txt`) |
| Restart drill | A, B, C, F PASS (`restart-drill.txt`) |
| ruff check (changed .py files) | All checks passed |
| ruff format --check (created .py files) | 8 files already formatted. `server.py`, `mock_server.py` and `dogfood/studio/spec.py` were already unformatted at ca20d4a and are not reformatted here |
| Mutants (`dogfood/multi_mutants.py`, suite tests/mcp + tests/workspace) | control PASS (181 passed); **6 of 6 caught** |

### Mutants: predicted catchers vs actual (`mutants.txt`, `mutants.json`)

| id | mutant | predicted (all were among the failures) | also caught by |
|---|---|---|---|
| MM1 | guard protects the first explicit project only | `test_each_project_protects_its_own_spec[none,first]`, dotdot, symlink, bash forms, hook reason | `[both]`, explicit-without-spec, whole-tree (16 failures) |
| MM2 | `project` argument ignored | project argument, unknown project, stdio workspace server | single-project router, set_active, repeated flags, error suffix (7) |
| MM3 | `project_set_active` not persisted | `test_project_set_active_persists...`, stdio workspace server | none (exactly 2) |
| MM4 | depends_on cycle accepted | error class `[depends_on_cycle]`, cycle message, CLI validate, Studio route | none (exactly 4) |
| MM5 | impact not transitive | chain test, MCP `workspace_impact` (and, as predicted, `test_impact_is_transitive_over_links` did NOT catch it) | none (exactly 2) |
| MM6 | single-project server adds `project` | single-project router, single-project stdio server | the old `tests/mcp/test_server.py::test_read_tools_round_trip` (3) |

No survivor, so no test was added after the run.

## UI: how much changed and what the Map does

- `api.js` +6, `store.js` +1, `app.js` +30/-7, `views/map.js` +53/-3. In total about 90 lines of JS.
- **Switcher.** When `GET /api/workspace` names a workspace:
  - the hub's projects are listed under a "Workspace <name>" group by manifest name and role
    ("scheduler · provider");
  - manifest projects the hub does not serve appear as disabled entries
    ("scheduler-web · consumer (not in this Studio)");
  - the option values stay hub names.
  - The switcher now also appears on a single-project server when the workspace has more than one
    project. Without a workspace it is unchanged.
- **Map: Workspace level.** The Map's existing graph adapter (`mountGraph` in `graph.js`) already
  takes generic `{nodes, edges}`, so the workspace level is about 45 of the 53 added map.js lines:
  - a third "Workspace" switch, shown only with a workspace;
  - projects are nodes (subtitle: role, "not in this Studio", "no spec"), links are edges (dashed
    `uses`, solid `depends_on`), with the manifest's problems listed above;
  - double-clicking a project the hub serves opens it.
  - It needs no `/api/graph`. On the real server, whose Map falls back to tiles, a
    "Workspace map" button above the tiles opens the same level.
  - Node kind `project` is drawn with the bundle's default node type. No bundle change.
- **Checked in headless Chrome over CDP:**
  - mock: `ui-check.json`, `ui-map-workspace.png`;
  - real server: `ui-check-real.json`, `ui-real-map-workspace.png`;
  - result: the switcher options, 3 nodes, 3 edges, the warning, no console errors.
  - Cosmetic: edge labels overlap a little in the dagre layout (visible in the screenshot).

## Decisions taken that the task did not fix

1. **Who is known.**
   - With `--project`: the explicit projects plus the workspace's, with no `dogfood/*` and no
     registry, so a single-`--project` server stays single.
   - With no `--project`: launch dir, discovered, workspace, registry.
   - First source wins, but a declared project is labelled `workspace` rather than
     `discovered`/`registry`, and always has its manifest name and role. An explicit `--project`
     that is declared keeps the source `explicit` and takes the manifest name.
   - The launch directory and missing workspace directories are listed (spec `missing`).
2. **The `project` argument** is accepted in every call. It is advertised in `inputSchema` only
   when more than one project is known; this keeps the single-project schemas byte-identical.
3. **Multi-project output.**
   - `project` is the first key of every result, so it survives the 20 000-char truncation
     preview.
   - A pre-existing `project` key (model_get overview, project_status) moves to `project_info`.
   - Tool errors end with `(project: NAME)`.
   - In multi mode the `initialize` instructions get one sentence about `project_list` and
     `project`.
4. **`--studio-url`** proxies the first project only; the others are served locally.
5. **A bad manifest never stops the MCP server.** It warns on stderr, and `project_list` shows the
   problem.
6. **Guard.**
   - Explicit projects keep their old protection, plus the owner of each path.
   - Commands that name no path (interpreter one-liners, `git reset --hard`, bare `git stash`) are
     checked against the explicit projects and the cwd's owner.
   - "Stop at a `.git` boundary" is implemented as: stop at the first directory that holds `.git`
     but no `spec.trace.json`.
   - The deny reason adds `of project NAME (DIR)` and a `project: "<DIR>"` hint for
     change_propose.
7. **Manifest.**
   - Paths may be relative (to the manifest) or absolute.
   - A missing `[workspace] name` defaults to the manifest's directory name.
   - Invalid links stay listed with their problem (`impact_of` skips them).
   - `uses` links may form cycles; `impact_of` follows both kinds.
   - Unknown keys are ignored, which leaves room for layer 3 fields.
   - Extra problem codes: `bad_name`, `bad_role`, `bad_kind`, `missing_field`, `bad_type`, and
     `unreadable` (used by the API).
8. **Studio.**
   - The manifest is located once at start (`--workspace`, `LATTICE_WORKSPACE`, walk-up from the
     cwd) and re-read on every request.
   - Workspace projects join hub discovery only with no DIR, under manifest names.
   - `GET /api/workspace` rows carry an extra `hub_name`, which the switcher needs to map hub
     names to manifest names.
9. **Dogfood.**
   - The harness writes `lattice.workspace.toml` next to each scenario's project. This is outside
     the project's git repository, so no recorded commit id changed.
   - Because every scenario then has a valid workspace, R93 (no manifest; error classes) is
     `unchecked` with a pointer to the tests and this run.
10. **Existing test edited.** In `tests/mcp/test_server.py` the two exact tool-list assertions now
    include `project_list` and `project_set_active`. Decision 2 requires them in every listing;
    nothing else in that file changed.

## Post-hoc observations (not pre-registered)

- Streak's `model_get` overview is 27 567 characters, over the server's 20 000-char cap, so it
  arrives as the truncation envelope. This is the same in a single-project server today. The
  MP-1 probe read `project`, `rule_count` and R1 from the envelope's preview (a prefix of the same
  JSON). This is recorded rather than hidden: the pre-registration assumed a plain result.
- Building a Studio on a project writes its durable state. The scratch clones got
  `.lattice/state/...`, which confirms the pre-registered choice to run tools only on copies.
- A hub with no DIR in the MP-3 directory made `shared-auth` the default project. Discovery lists
  the cwd's children alphabetically: `auth`, `billing`, `web`.
- **Behaviour change to note.** The nearest-trace rule now also protects nested projects inside an
  explicit one. Example: `dogfood/studio/project_seed/spec.*` for an agent run with
  `--project dogfood/studio`, including rebuild agents in a worktree. Only two `spec.trace.json`
  files are tracked in this repository (`dogfood/studio` and its `project_seed`).
- Studio chat sessions still pass one `--project`. But if a `lattice.workspace.toml` sits above a
  session's working directory, that session's MCP server knows the workspace projects and tags
  results with `project`. Decision 1 allows this (it is no longer "no workspace"). It was not
  exercised with a real Claude session.

## Not verified

- No real Claude Code session was run against the multi-project server or the new guard. The
  hook was exercised as a subprocess with hook-shaped JSON.
- `--studio-url` with several projects (HttpBackend for the first only) has no test.
- The plugin (`lattice-claude-plugin/.mcp.json`, `hooks.json`) is unchanged and still passes one
  `--project`. Multi-project use from the plugin needs a workspace manifest above the project, or
  a config edit.
- Guard limits:
  - `rm -rf DIR` where DIR holds a project more than zero levels below it and that project is not
    explicit (the owner walk goes up, not down);
  - Windows paths.
- Performance with many discovered projects. Toolboxes are built lazily, but each used project
  holds its own Studio in memory.

## What this build already constrains for layer 3

- **Contract ownership.**
  - Links are declared in the workspace manifest, a third file owned by neither project.
  - `from -> to` means "from relies on to", and roles are per project in the manifest.
  - Layer 3 must decide whether a consumer's imports live in its own spec or extend `[[link]]`.
    Today the `note` is the only per-link text.
  - The proposed "provider's spec is the source" is compatible: the manifest holds no contract
    content.
- **Static vs live.**
  - Everything built is static: manifest, paths, graph traversal. Nothing runs another project.
  - Per-link opt-in for live checks would be a new `[[link]]` key. The parser ignores unknown
    keys, so adding one does not break today's manifests (an old parser would silently ignore
    it).
- **Versioning.**
  - Projects are working-directory paths with no ref.
  - Links carry no version or git ref. `GET /api/projects` exposes `git_head` per hub project, but
    the workspace does not.
  - So "provider changed" and "consumer stale" are still indistinguishable. Layer 3 needs a
    ref/version on the link or on the provider's export, and must decide what a path-based
    workspace checks against: the checked-out tree, today.
- **Cost.**
  - `impact_of` is free (graph traversal only). Cross-project checks do not exist yet.
  - In the MCP server each used project builds its own Studio (memory, and state writes per
    project). A contract check that loads both specs would cost two Studio builds, or a lighter
    spec loader.
- **Identity.**
  - Manifest names (`[a-z0-9._-]+`, unique) are the cross-project identifiers. MCP resolves them
    by name or path. Hub names can differ, which is why the API carries `hub_name`.
  - Layer 3 contract references should use manifest names.
  - The `depends_on` graph is already enforced acyclic (libraries form a DAG); `uses` may cycle.

## Orchestrator verification (re-run independently on a backup copy of the worktree)

| Check | Result |
|---|---|
| pytest, the task's suite list | 1400 passed, 5 skipped (7 min 8 s) |
| `dogfood/studio/check.sh` | PASS, 0 findings, 408 cases |
| `scripts/restart_drill.py` | drills A, B, C, F pass |
| `dogfood/multi_mutants.py` | control 181 passed; MM1-MM6 all caught |
| ruff check (changed files), ruff format --check (created files) | clean |
| Single-project byte compatibility, re-measured on `dogfood/studio` against the old code | `initialize` identical; the old `tools/list` (11 tools) is a prefix of the new one (13), the two added tools being `project_list` and `project_set_active` |

Not re-run by the orchestrator: MP-1 over two real projects, MP-2's 10 guard cases and MP-3's
real-server run (read from the agent's recorded transcripts), and the headless-Chrome UI check.
