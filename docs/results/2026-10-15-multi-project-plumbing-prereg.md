# Multi-project plumbing leftovers (layers 1-2): pre-registration

Date: 2026-10-15. Node G8, branch `np-plumbing` (worktree `lattice-plumbing`, from `next-phases`
a4ab4a1). Written BEFORE any code or probe of this change. Decisions, pass rules and predictions
are blind: a failed prediction is a finding in `...-plumbing-results.md`, never silently fixed.

## 1 Status

Leftovers (HANDOFF-2026-10-12 section 4): (1) the plugin passes ONE `--project`; (2) `--studio-url`
with several projects proxies the first only, untested; (3) the guard misses `rm -rf` of a
directory holding a non-explicit project (the owner walk goes up, never down). Nothing built yet.

**Baseline observation (the existing host, not the new behaviour).** `ps`/`lsof` on the two plugin
servers running here (sessions in `~/Documents/lattice` and `~/Documents/streak`): argv `lattice
mcp serve --project .`, cwd = the session's project directory, `CLAUDE_PROJECT_DIR` in the process
env. So the host expanded `${CLAUDE_PROJECT_DIR:-.}` to its default `.` (unset at expansion time)
and the workspace walk-up already starts in the project. `lattice` on PATH is a uv tool install
editable onto the MAIN checkout: nothing built here reaches the plugin host before a merge.

Changed: `lattice/mcp/server.py` (D1b, D2), `__main__.py` (help texts), `guard.py` (D3),
`lattice-claude-plugin/README.md` (one paragraph). New: `tests/mcp/test_plumbing_{plugin,
studio_url,guard}.py`, `dogfood/multi_plumbing_{probes,mutants}.py`, data in
`docs/results/data/2026-10-15-multi-plumbing/`. Untouched: the plugin's `.mcp.json` and
`hooks/hooks.json`, `projects.py`, `tools.py`, `hub.py`, every existing test file.

## 2 Decisions (taken before coding)

### D1 The plugin and the workspace

- **D1a: the plugin config stays byte-identical.** `--project ${CLAUDE_PROJECT_DIR:-.}` keeps the
  server single-project when no manifest applies (the byte-compat rule, B1-B6). With the observed
  host the existing rule already finds a workspace (flag, else inherited `$LATTICE_WORKSPACE`,
  else walk-up from the launch dir) and knows the explicit project plus the manifest's. Dropping
  `--project` (discovery mode) is rejected: children, `dogfood/*` and the registry would make a
  single project several and change `initialize`/`tools/list` for single-project users.
- **D1b: a fallback anchored at the project (`serve()`).** Only when those three steps found no
  workspace and a `--project` is given: walk up from the FIRST explicit project; the first
  manifest found is used only if it loads (no `unreadable` problem) AND declares that project
  (`ws.by_path(root)`). Else no workspace, as today, plus one stderr line `lattice mcp: workspace
  <path> (found from <project>) not used: <why>`. It never overrides the flag, the env or a
  manifest found from the launch dir (even a non-declaring one: today's behaviour). It removes the
  dependence on the cwd for a host that expands `CLAUDE_PROJECT_DIR` but launches elsewhere.
- Edges: `--project .` resolves against the cwd first; the walk stops at the first manifest; a
  one-project workspace keeps the base instructions and lists the workspace tools (B4 shape); the
  server never reads `CLAUDE_PROJECT_DIR`. README: "Several projects" (manifest or env).

### D2 `--studio-url` with several projects

- **Unchanged:** the first project's backend is `HttpBackend(<url as given>, root)`; with ONE known
  project nothing else happens. **Base URL:** the given URL without a trailing `/` and without a
  trailing `/p/<segment>` (chat sessions pass `.../p/<name>`).
- **Other projects, on first use (lazy boxes):** `GET <base>/api/projects` (timeout 10 s), matched
  by PATH (first row whose `realpath(root)` equals the project's root; rows without string
  `name`/`root` skipped), never by name (hub and MCP names may differ). Match: `HttpBackend(<base>
  /p/<quote(name, safe="")>, root)`, the WHOLE backend (reads, ask, change_*, check_run), as for
  the first project: a change proposed for that project lands in that Studio.
- **Projects the Studio does not serve** (no row, or a 404 listing: a Studio without the hub API):
  local `PendingBackend`, today's behaviour; one stderr line; no result field added.
- **Errors:** a connection error/timeout or any other HTTP status on the listing, or the matched
  `HttpBackend`'s init failing: a tool error (`cannot reach Studio at <base>: ...` / `Studio
  answered <code>: ...` + `(project: NAME)`). A failure is not cached (the next call retries); a
  built box (HTTP or local) stays. No listing cache: at most N-1 extra GETs per process.

### D3 The guard walks down

- **Where:** `MultiGuard.holds_protected(resolved, below=True)`. Today's checks first, unchanged
  (explicit projects by prefix at any depth; the target's owner, walking up). If neither hits:
  `project_below(target)`, a `Guard` of the found project (cached like owners), and the deny names
  its first protected name and the project via the unchanged `_reason` (`of project NAME (DIR)`).
- **`project_below(target)`:** `target` is the real path (the target's own symlink followed, as
  today); nothing unless it is an existing, non-pruned directory. Breadth-first, entries sorted by
  name per level (the shallowest, then first-sorted project is named). A visit = one `os.scandir`
  (`OSError`: skipped); a project = a directory holding a file `spec.trace.json` (the owner
  marker). Depth: target 0, directories at depth 1..**5** visited. Budget: **4000** scandirs; when
  spent the walk stops and the command is ALLOWED (fail-open, no output). Directory symlinks
  inside the target are NOT followed (`rm -rf` removes the link; no cycles). Pruned (never
  entered, nor walked as the target): `.git node_modules __pycache__ .venv venv .mypy_cache
  .pytest_cache .ruff_cache .tox`. Nested repositories are entered (`rm -rf` deletes them).
- **Which commands:** every path checked with `dirs=True` today (`rm`; `mv` sources; `git
  checkout|restore|clean` paths; `git stash` pathspecs), plus `git rm` paths and `git mv` sources
  (same class). An `mv`/`git mv` destination (last path) gets today's checks, no walk (moving INTO
  a directory leaves its contents). **Pathless `git clean`** = `git clean <opts> .` (today `-fdx
  .` is denied in a project, `-fdx` allowed); no dry-run exemption.
- **Unchanged:** output shapes (allow: exit 0, silent; deny: hook JSON or `--mode exit2`), reason
  text, edit tools, redirections, `cp`, `dd`, one-liners, whole-tree commands (`git reset --hard`,
  bare `git stash`: cwd guards, no walk). **Known gaps kept:** globs (`rm -rf *`), `find -delete`,
  `xargs rm`, `rsync --delete`, `$(...)`, `git reset --hard` above a nested same-repo project.

## 3 Probes (runner `dogfood/multi_plumbing_probes.py`, 10 separate runs)

Real subprocesses (`python -m lattice.mcp`; L-plugin via `lattice.cli.main(["mcp", ...])`), env without `ANTHROPIC*`/`CLAUDE_CODE*`/`LATTICE_WORKSPACE` unless a case sets
it, `LATTICE_PROJECTS_REGISTRY` = a missing file, fixtures under `$S/g8`. "Old" = `git archive
a4ab4a1 lattice`. Deterministic: **pass = every check in 10 of 10 runs, outcomes identical.**

### L-plugin (D1)

| # | case | expected new | old (predicted) |
|---|---|---|---|
| P1 | `.mcp.json` and `hooks.json` parse; `git diff a4ab4a1` of both is empty; the `lattice` args are `["mcp","serve","--project","${CLAUDE_PROJECT_DIR:-.}"]`; expanded host-style (`${V}`, `${V:-d}`) without the var = `--project .`, with it = the path | holds | holds |
| P2 | byte-compat: cwd = a copy of `dogfood/studio` (no manifest above), plugin argv expanded both ways, through the CLI dispatch: `initialize`, `tools/list`, `model_get {}` new vs old | byte-identical | (reference) |
| P3 | `ws/` holds a manifest declaring `proj` and `other` (copies), cwd `ws/proj`, argv `--project .` | `tools/list` = P2's tools + the 4 workspace tools (`workspace_impact`, `contract_check`, `contract_impact`, `linked_question_list`); instructions = base + MULTI_NOTE + WORKSPACE_NOTE; project_list: `proj` source `explicit`, `other` source `workspace` | same (the gap is already closed for this host shape) |
| P4 | same `ws/`, cwd `$S/g8/elsewhere` (no manifest above), argv `--project <abs ws/proj>` | as P3 | single project, no workspace tools (= P2 shape) |
| P5 | a manifest above the project that does NOT declare it, cwd elsewhere, `--project <abs>` | byte-identical to old; stderr names the manifest "not used" | single project |
| P6 | `LATTICE_WORKSPACE=<ws>`, cwd elsewhere, `--project <abs ws/proj>` | as P3 | as P3 |
| P7 | an unreadable manifest above the project, cwd elsewhere | byte-identical to old (no problems row) | single project |

Prediction: P1-P7 10/10; old column as stated.

### L-url (D2): a fake Studio on loopback

An in-process `http.server` on `127.0.0.1:0` logs every request. Routes (also under `/p/<name>`,
like the hub): `GET /api/projects` = `[{"name": "alpha", "root": realpath(A), "default": true},
{"name": "beta", "root": realpath(B)}]`; `/api/model` and `/p/alpha/api/model`: `rules[0].text` =
`ALPHA via studio`; `/p/beta/api/model`: `BETA via studio`; unknown `/p/x/...`: 404. `A`, `B`, `C`
are bare dirs (no spec), so a LOCAL answer is the `is not a Lattice project yet` error; their MCP
names `a`, `b`, `c` deliberately differ from the hub's.

| # | server and calls | pass rule |
|---|---|---|
| U1 | `--project A --project B --project C --studio-url <base>/p/alpha`; `model_get` for a, b, c, then b again | a = `ALPHA via studio`; b = `BETA via studio`; c = `isError`, text has `is not a Lattice project yet` and C's path; log: exactly 2 `GET /api/projects` (b, c), 0 requests naming C, the second b call adds no listing request |
| U2 | same with the bare `<base>` | a via `/api/model`, b via `/p/beta/api/model`, c local error |
| U3 | the fake answers 404 on `/api/projects` | a via Studio; b = the local error (fallback) |
| U4 | the fake answers 500 on `/api/projects`; `model_get b` twice | both `isError` with `Studio answered 500` and `(project: b)`; 2 listing requests (no caching of a failure) |
| U5 | ONE `--project A --studio-url <base>/p/alpha`: `initialize`, `tools/list`, `model_get` | 0 `/api/projects` requests; the three responses byte-identical to old with the same fake |
| U6 | `--project <symlink to B>` among several | b via `/p/beta` (both sides realpath'd) |

Prediction: U1-U6 10/10. Reported only: one REAL hub (`python -m lattice.studio`, no DIR, two
`dogfood/studio` copies as children of `$S/g8/hub`): each copy's `model_get` arrives through it.

### L-guard (D3): the real hook, JSON on stdin

Fixture `$S/g8/lg` (names = `[project] name`): `ws/p1` (ws-p1, with `p1/build/`), `ws/team/svc/p3`
(ws-p3), `ws/plain`, `ws/node_modules/pkg/p5` (ws-p5), `notes.txt`; `links/alias` -> `ws/p1`;
`wslink` -> `ws`; `loop/back` -> `loop` and `loop/q/p` (loop-p); `cap/x1/x2/x3/x4/x5/p6` (cap-p6:
depth 6 from `cap`, 5 from `x1`); `wide/d0000`..`d4999` (empty) and `wide/zzz/p` (wide-p);
`repo2` (`git init`, `other/` empty, untracked `vendor/np`, ws-np). No `--project` unless stated.
`W` = this worktree (only read). Deny = hook JSON deny whose reason has `of project NAME (`.

| # | cwd | command | new | old |
|---|---|---|---|---|
| 1 | lg | `rm -rf ws` | deny ws-p1 | allow |
| 2 | lg/ws/plain | `rm -rf ../../ws` | deny ws-p1 | allow |
| 3 | lg/ws | `rm -rf team` (depth 2) | deny ws-p3 | allow |
| 4 | lg/ws | `rm -r ./team/../team/svc` | deny ws-p3 | allow |
| 5 | lg | `cd ws && rm -rf team` | deny ws-p3 | allow |
| 6 | lg/ws | `rm -rf plain` | allow | allow |
| 7 | lg/ws | `rm -rf p1/build` | allow | allow |
| 8 | lg/ws | `rm -rf p1` (zero levels) | deny ws-p1 | deny ws-p1 |
| 9 | lg/ws | `rm -rf node_modules` (pruned) | allow | allow |
| 10 | lg | `rm -rf links` (only a dir symlink to p1) | allow | allow |
| 11 | lg | `rm -rf wslink` (the target is a symlink) | deny ws-p1 | allow |
| 12 | lg | `rm -rf loop` (symlink cycle inside) | deny loop-p | allow |
| 13 | lg | `rm -rf cap/x1` (depth 5) | deny cap-p6 | allow |
| 14 | lg | `rm -rf cap` (depth 6, documented miss) | allow | allow |
| 15 | lg | `rm -rf cap`, `--project lg/cap/.../p6` | deny cap-p6 | deny cap-p6 |
| 16 | lg | `rm -rf wide` (budget spent at level 1) | allow | allow |
| 17 | lg | `rm -rf wide/zzz` | deny wide-p | allow |
| 18 | lg | `mv ws ws-old` | deny ws-p1 | allow |
| 19 | lg | `mv notes.txt ws/` (destination) | allow | allow |
| 20 | lg/repo2 | `git clean -fdx` | deny ws-np | allow |
| 21 | lg/repo2/other | `git clean -fdx` | allow | allow |
| 22 | lg/ws/p1 | `git clean -fd` (pathless, in a project) | deny ws-p1 | allow |
| 23 | lg/repo2 | `git clean -n` (no dry-run exemption) | deny ws-np | allow |
| 24 | lg/repo2 | `git rm -r -q vendor` | deny ws-np | allow |
| 25 | lg/ws | `git checkout -- team` | deny ws-p3 | allow |
| 26 | lg/ws | `git stash push -- team` | deny ws-p3 | allow |
| 27 | lg/ws | `rm -rf *` (glob, known gap) | allow | allow |
| 28 | lg | `find ws -delete` (known gap) | allow | allow |
| 29 | $S/g8 | `rm -rf W/dogfood` (real tree) | deny lattice-studio | allow |
| 30 | lg | Edit `ws/p1/spec.py` | deny ws-p1 | deny ws-p1 |

**Pass:** 30/30 rows as "new", 10/10 runs; a deny is exactly `hookSpecificOutput.{hookEventName,
permissionDecision, permissionDecisionReason}`, an allow is silent with exit 0; rows 1 and 13 also
exit 2 with the reason on stderr under `--mode exit2`. Old column predicted exactly (17 flips
allow -> deny: 1-5, 11-13, 17, 18, 20, 22-26, 29; none the other way); a mismatch is a finding.
**Cost** (in-process `decide`, 10 runs): row 16 median <= 1.0 s, max <= 2.0 s (predicted ~0.15 s);
row 29 median <= 0.5 s (predicted < 0.05 s). Reported only: `rm -rf ~/Documents` (read only),
predicted deny `job-scheduler-studio` (first sorted of its two depth-1 projects).

### L-regress

| # | check | pass |
|---|---|---|
| R1 | MP-2's 10 guard cases (`mp_probes.mp2`, real Streak and `W/dogfood/studio`, read only), with the two `--project`s and with none | 10/10 and 10/10, in each of 10 runs |
| R2 | the 10 existing `tests/mcp/test_*.py` files | `git diff --exit-code a4ab4a1` empty for them, and they pass |
| R3 | `dogfood/contracts/fixes_probes.py --old <2407eaf archive>` | B1-B6 6/6 |
| R4 | `pytest tests/mcp tests/workspace tests/contracts tests/studio tests/test_cli.py` | 0 failed; passed = the count recorded on a4ab4a1 before the first edit + the new tests |
| R5 | `dogfood/studio/check.sh` | PASS, 0 findings, same case count as before the first edit (435 at round 3) |
| R6 | `dogfood/multi_mutants.py` | control passes, MM1-MM6 caught (an anchor that no longer applies is reported and re-anchored post hoc) |

## 4 Mutants (`dogfood/multi_plumbing_mutants.py`; suite: tests/mcp + the probe runner's L-* cases)

| id | mutant | predicted catchers |
|---|---|---|
| PM1 | no walk-down | guard rows 1-5, 11-13, 17, 18, 20, 22-26, 29 |
| PM2 | the walk follows directory symlinks | row 10 (deny) |
| PM3 | depth cap 4 (off by one) | row 13 |
| PM4 | no budget | row 16 (deny), cost |
| PM5 | pruned names entered | row 9 (deny ws-p5) |
| PM6 | `mv` destination walked | row 19 |
| PM7 | pathless `git clean` not mapped to `.` | rows 20, 22, 23 |
| PM8 | `git rm` keeps `dirs=False` | row 24 |
| PM9 | non-first projects always local (today) | U1, U2, U6 |
| PM10 | match by hub name instead of path | U1, U2, U6 |
| PM11 | `/p/<name>` not stripped from the base | U1 (listing at `/p/alpha/api/projects` answers, then `/p/alpha/p/beta` 404) |
| PM12 | a 404 listing is an error (no local fallback) | U3 |
| PM13 | a failed listing cached as local | U4 (second call not an error) |
| PM14 | the listing fetched with one project | U5 (request count) |
| PM15 | fallback manifest accepted without declaring the project | P5 |
| PM16 | no fallback (launch walk-up only) | P4 |

Prediction: 16 of 16 caught, each by its listed catchers among others; control passes first.

## 5 Risks, and what this cannot show

1. **Over-blocking.** Deleting a directory that holds a COPY of a project (fixtures, clones) is now
   denied with the change_propose text, which misleads. Rebuild agents work in worktrees holding
   `spec.trace.json`, so `git clean -fd` there is now denied. Checked by R4/R5 and a grep of
   `lattice/` for `git clean` in agent prompts; no real rebuild session is run.
2. **Fail-open bounds.** Depth 5, budget 4000, prunes, globs, `find -delete` leave holes (`rm -rf
   ~` past the budget is allowed): a safety net, not a sandbox.
3. **Cross-project writes (D2).** A Studio chat session of A (with a workspace) can now propose
   into B's Studio queue (before: a throwaway local backend). The owner still accepts; whether the
   chat allow-list should restrict `project` is open.
4. **D1b turns a single-project server multi** (instructions, tool list) for someone launching
   outside a manifest that declares the project. Intended, but visible.
5. **Pre-existing, kept:** a bare `--studio-url` to a hub whose default is not the first known
   project still proxies the first project to the wrong one.
6. **Path matching** is exact on `realpath`; on case-insensitive APFS, roots spelled in another
   case do not match (local fallback). 7. Re-anchoring MM1-MM6 (R6) could weaken an old mutant.

**Cannot show:** the real plugin host. No Claude session is spawned; expansion and cwd are known
from one `ps` look at two sessions of one Claude Code version; other hosts (desktop, IDE) may
launch elsewhere, and the plugin runs the MAIN checkout's code until a merge. Not tested either:
agents actually using workspace tools through the plugin, more than one real hub run, Windows.

## 6 Blind vs post hoc, and verification

Blind (fixed now): section 2, every row and expected value, the old-code column, pass rules,
cost bounds, mutants and catchers. Post hoc, reported apart: test names (filled into the mutant
docstring after the tests exist), times, the real-hub run, re-anchored mutants, forced decisions.

```sh
W=/Users/vivekgade/Documents/lattice-plumbing
S=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=$W TMPDIR=$S/tmp PYTHONDONTWRITEBYTECODE=1
cd $W && for r in a4ab4a1 2407eaf; do mkdir -p $S/tmp $S/old-$r; git archive $r lattice | tar -x -C $S/old-$r; done
uv run python -c 'import lattice; print(lattice.__file__)'          # must be under $W
uv run python dogfood/multi_plumbing_probes.py --old $S/old-a4ab4a1 --scratch $S/g8 \
    --runs 10 --out docs/results/data/2026-10-15-multi-plumbing     # L-plugin, L-url, L-guard, R1
uv run python dogfood/contracts/fixes_probes.py --old $S/old-2407eaf --scratch $S/g8-b   # R3
git diff --exit-code a4ab4a1 -- lattice-claude-plugin/.mcp.json lattice-claude-plugin/hooks \
    $(git ls-tree --name-only a4ab4a1 tests/mcp/)                   # P1, R2
uv run python -m pytest tests/mcp tests/workspace tests/contracts tests/studio tests/test_cli.py -q
dogfood/studio/check.sh                                              # R5
uv run python dogfood/multi_plumbing_mutants.py && uv run python dogfood/multi_mutants.py
uv run ruff check lattice/mcp dogfood/multi_plumbing_*.py tests/mcp/test_plumbing_*.py
git -C /Users/vivekgade/Documents/lattice status --short; git -C ~/Documents/streak status --short
```
No LLM, no API key, no Claude session; Streak, job-scheduler and the main checkout are only read
(`git status` recorded before and after, unchanged).

## Amendment 1 (orchestrator rulings, 2026-10-15, before any code)

Two decisions of this document are overruled because they widen blast radius beyond the three stated
gaps. Everything not named here stands. The builder applies this section literally and reports any
row or probe it cannot map.

**A. D2 is read-only for non-first projects.** A non-first project that the Studio serves (matched by
realpath, as written) gets a backend from `/p/<name>` for its READ methods only (model, rule, lint,
code links, graph, status, change list/get). `change_propose`, `check_run` and `spec_ask` are NOT
routed: they behave for non-first projects exactly as at a4ab4a1. Reason: routing a proposal into
another project's Studio queue is a cross-project write by an agent, which the owner never approved
(risk 3 of this document is therefore removed, not accepted). Consequences: every U-row that predicted a
change/ask/check reaching the other Studio now predicts "served as at a4ab4a1"; read rows are
unchanged; mutant PM9 stays (non-first always local) and a new mutant PM17 "change_propose routed to
the matched project's Studio" must be killed by the U-rows that assert it is not.

**B. D3 keeps only the walk-down; the widenings are reverted.** The walk-down applies to the commands
the guard already treated as destructive on a path (`rm`, `rmdir`, `mv` source, and the existing
`git checkout|restore` and `git stash` pathspec handling) so that a PARENT directory that holds a
non-explicit project is protected. NOT done: `git rm` and `git mv` get no new directory rule; a
pathless `git clean` is NOT mapped to `.`; there is NO change to the dry-run handling of `git clean`
(`-n` stays as today: allowed). The existing inconsistency (`git clean -fdx .` denied inside a project,
`git clean -fdx` allowed) is documented as a known gap, not fixed. Reason: denying `git clean -fd`
inside any project would also hit rebuild-agent worktrees, a cost nobody asked for.
Predicted table: rows 20, 22, 23 and 24 are **allow** (equal to the old column); all other rows stand.
The flips allow -> deny are therefore 13: rows 1-5, 11-13, 17, 18, 25, 26, 29; none the other way.
Mutants PM7 and PM8 are replaced by PM7' "pathless `git clean` mapped to `.`" (must not exist: killed by
rows 20 and 22) and PM8' "`git rm` walked down" (must not exist: killed by row 24). Risk 4 (rebuild
worktrees) is removed.
