# Multi-project layers 1 and 2: pre-registration

Date: 2026-10-12. Branch `np-multi` (worktree `lattice-npm`, from `next-phases` ca20d4a).
Written BEFORE any code of this build exists and before any probe runs. The criteria and
predictions below are blind: a failed prediction is a finding, reported as such in
`docs/results/2026-10-12-multi-results.md`, never silently fixed. Post-hoc observations are
reported separately from the blind ones.

Read first: `docs/design/lattice-multi-project-phase.md` (layers 1-4; this build is 1 and 2, not 3
or 4), `docs/design/next-phases-plan.md`.

## What is built (summary)

1. **Layer 1.** `python -m lattice.mcp serve` takes `--project DIR` (repeatable, optional) and
   `--workspace PATH`. Tools `project_list` and `project_set_active` (always listed, also in
   `--readonly`); every existing tool accepts an optional `project` (a name or a path as
   `project_list` gives it). Discovery is Studio's (`lattice/studio/hub.py`), extended in place
   with a source per project, not copied. The guard (`python -m lattice.mcp guard`) takes
   `--project` optionally and repeatably and resolves each path's owning project by walking up to
   the nearest `spec.trace.json`.
2. **Layer 2.** `lattice.workspace.toml` parsed by `lattice/workspace.py` (stdlib `tomllib`),
   CLI `python -m lattice.workspace show|validate|impact` and `lattice workspace ...`; MCP
   `project_list` carries roles and links, and `workspace_impact` (listed only with a workspace);
   Studio `GET /api/workspace` (API v1.16), mock parity, dogfood rules R90-R93; the web app's
   project switcher shows manifest names when a workspace exists.

## Decisions taken before coding (not fixed by the task)

- **Who is known.** With `--project` given: the explicit projects plus the workspace's projects
  (no `dogfood/*` and no registry: otherwise a single-`--project` server would know more than one
  project and its output would change, which decision 1 forbids). With no `--project`: the launch
  directory (source `launch`, listed even when it has no spec), the workspace's projects, then
  Studio's discovery over the launch directory (`discovered`: the directory, its children,
  `dogfood/*`) and the registry file (`registry`). Deduplicated by resolved path; the first source
  wins, but a project in the workspace always carries its manifest name and role.
- **Single-project byte compatibility.** In a single-project server the existing tools' schemas
  are unchanged (the `project` argument is accepted but advertised in `inputSchema` only when more
  than one project is known), the two new tools are appended after the existing ones, the
  `initialize` instructions are unchanged, and no result gains a `project` field.
- **The `project` field when several projects are known.** `model_get` (overview) and
  `project_status` already return a key `project`. With more than one project known, `project` is
  the name of the project the call ran on and the original value moves to `project_info`. Tool
  errors then end with `(project: NAME)`.
- **`--studio-url`** keeps proxying the first project only (as today); other known projects are
  served locally.
- **Guard reach.** The explicit `--project`s keep their old protection exactly (including
  `[mcp] protected` names); in addition every path is checked against its own owning project
  (nearest `spec.trace.json` walking up from the real path; the walk stops at the filesystem root
  or at a directory holding `.git` that has no `spec.trace.json`). Interpreter one-liners and
  `git reset --hard`/bare `git stash` (which name no path) are checked against the projects owning
  the command's working directory and the explicit ones. Not covered: a `rm -rf` of a directory
  that holds a project deeper than one level below it, when that project is not explicit.
- **Studio's hub** gains the workspace as a discovery source only when Studio runs with no DIR
  (like the registry); Studio finds the manifest with `--workspace`, else `LATTICE_WORKSPACE`, else
  by walking up from its working directory. `GET /api/workspace` re-reads the manifest per request.
- **Real projects are not written.** Building a `Studio` on a project writes its durable state
  (and the repository's `info/exclude`), so every probe that runs a tool on Streak or on
  `dogfood/studio` uses a scratch copy: `git clone` of Streak's committed HEAD and a file copy of
  `dogfood/studio`, under the session scratchpad. The guard only decides, so MP-2 uses the real
  paths (`/Users/vivekgade/Documents/streak`, this worktree's `dogfood/studio`) for its files;
  the symlink aliases live in the scratchpad.

## Probes and pre-registered criteria

### MP-1 two real projects, one server (blind)

ONE `python -m lattice.mcp serve` with no `--project`, launched in a scratch directory holding a
`lattice.workspace.toml` with `[[project]] name = "streak"` (the scratch clone of Streak) and
`name = "studio"` (the scratch copy of `dogfood/studio`), a `uses` link from studio to streak.
Over the real stdio protocol (JSON-RPC lines on a pipe):

1. `project_list` lists both (plus the launch directory, source `launch`, spec missing); both have
   `spec: "present"` and `source: "workspace"`.
2. `model_get {project: "streak"}` returns Streak's rules: `rule_count` 148 and `rules[0].text`
   equal to R1 of Streak's `spec.trace.json` ("Streak is a mobile habit tracker ..."), and
   `project: "streak"`; `model_get {project: "studio"}` returns dogfood's: `rule_count` equal to
   the copy's trace (89 before this build's R90-R93, 93 after) and its R1 text ("Lattice Studio
   (`python -m lattice.studio PROJECT_DIR`) serves ..."), `project: "studio"`.
3. `project_set_active {name: "streak"}`, then `model_get` with no `project` returns Streak's;
   `project_set_active {name: "studio"}`, then `rule_get {id: "R1"}` with no `project` returns
   dogfood's R1.
4. `model_get {project: "nope"}` and `project_set_active {name: "nope"}` are tool errors
   (`isError: true`) whose text lists the known names (`streak`, `studio`).

**Pass:** all four, in 3 of 3 runs (three separate server processes).

**Byte compatibility (part of MP-1):** a single-`--project` server (same scratch copy of
`dogfood/studio`) on this branch vs. the old code of `next-phases` (run from
`/Users/vivekgade/Documents/lattice`, `PYTHONDONTWRITEBYTECODE=1`, after confirming its HEAD is
ca20d4a and `lattice/mcp` is unmodified there): (a) the `initialize` result is byte-identical;
(b) the `tools/list` result is byte-identical once the two appended entries `project_list` and
`project_set_active` are removed, and the old JSON tool array is a byte prefix of the new one up
to those entries; (c) the results of `model_get {}` and `project_status {}` are byte-identical.
**Pass:** (a), (b) and (c).

**Prediction:** pass. Risk named in advance: building a Studio on the Streak clone imports its
`harness.py` (needs `bcrypt`/PyMySQL in this venv); if that fails, `model_get` on streak fails
and MP-1 fails.

### MP-2 guard across projects (blind)

The real hook (`python -m lattice.mcp guard`, JSON on stdin, deny = the hook JSON with
`permissionDecision: "deny"`), run with `--project /Users/vivekgade/Documents/streak --project
<worktree>/dogfood/studio`. `S` = Streak, `D` = `dogfood/studio`, `N` = a scratch directory with
no `spec.trace.json` above it, `C` = a scratch directory (not a project).

| # | tool | input | expected |
|---|---|---|---|
| 1 | Edit | `S/spec.py` | deny, reason names `streak` |
| 2 | Bash (cwd D) | `echo x > S/spec.py` | deny, reason names `streak` |
| 3 | Edit | `D/spec.py` | deny, reason names `lattice-studio` |
| 4 | Bash (cwd D) | `echo x >> spec.trace.json` | deny, reason names `lattice-studio` |
| 5 | Edit | `S/harness.py` | allow |
| 6 | Bash (cwd D) | `echo x > harness.py` | allow |
| 7 | Edit | `N/spec.py` | allow |
| 8 | Bash (cwd N) | `echo x > spec.py` | allow |
| 9 | Write (cwd C) | `C/alias.py`, a symlink to `S/spec.py` | deny, reason names `streak` |
| 10 | Edit (cwd C) | `C/dalias/spec.trace.json`, `dalias` a symlink to the directory D | deny, reason names `lattice-studio` |

(The names are the projects' `[project] name` in `studio.toml`: Streak's is `streak`, dogfood's
`lattice-studio`.) Case 9's alias is "from the other project's side" in the sense that the
guard is asked from a directory that is not Streak; a symlink inside `dogfood/studio` itself is
not created, because this build deletes nothing outside its scratchpad.

**Pass:** 10 of 10. **Also reported (not part of the pass):** the same 10 cases with no
`--project` at all (prediction: 10 of 10, by the nearest-trace rule alone).

### MP-3 workspace (blind)

A manifest with a provider (`billing-api`), a consumer (`web-app`, `uses` billing-api) and a
library (`shared-auth`; both `depends_on` it):

- `validate` accepts it (exit 0);
- each error class is rejected (exit 1) with its own message and code: unknown link target
  (`unknown_project`), duplicate name (`duplicate_name`), self link (`self_link`), a cycle of
  `depends_on` links (`depends_on_cycle`); a missing project directory is a warning
  (`missing_path`, exit 0);
- `impact_of("shared-auth")` = {web-app, billing-api}; `impact_of("web-app")` = none; and
  transitivity: `impact_of("billing-api")` = {web-app};
- `GET /api/workspace` on the real server (not only the mock) answers the same projects, links
  and problems; with no manifest it answers `workspace: null` and empty lists.

**Pass:** all, in tests and in one real-server run (`python -m lattice.studio` on scratch toy
projects, `curl`).

### MP-4 no regression (blind)

- The verification suite (baseline 1313 passed, 5 skipped before this build's tests) passes with
  0 failures, plus this build's new tests.
- `dogfood/studio/check.sh` passes (baseline 403 cases; prediction: 403 + the new scenarios).
- `scripts/restart_drill.py`: A, B, C, F pass.
- Studio chat sessions still get one fixed project: `tests/studio/test_session.py` passes
  unchanged and `session.py` is not modified.

## Mutants (`dogfood/multi_mutants.py`), predictions written before the tests exist

The tests are named by what they check; the exact test names are filled in post hoc in the
mutant file's docstring.

| id | mutant | predicted catchers |
|---|---|---|
| MM1 | the guard protects the active (first explicit) project only | `tests/mcp/test_multi_guard.py`: the second project's spec denied; nearest-trace with no `--project` |
| MM2 | the `project` argument is ignored (always the active project) | `tests/mcp/test_multi_server.py`: model_get per project, unknown project error |
| MM3 | `project_set_active` does not persist | `test_multi_server.py`: set active then default call |
| MM4 | a `depends_on` cycle is accepted | `tests/workspace/test_workspace.py`: the cycle error; CLI validate exit 1 |
| MM5 | impact is not transitive (direct dependents only) | `test_workspace.py`: transitive impact; MCP `workspace_impact` |
| MM6 | a single-project server adds the `project` field | `test_multi_server.py`: single-project byte compatibility (golden from the old code) |

Prediction: all six caught. A survivor is a finding: the test that kills it is added and
reported post hoc.
