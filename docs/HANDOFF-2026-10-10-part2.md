# Handoff, part 2: 2026-10-10 (evening)

Continues `docs/HANDOFF-2026-10-10.md` (read that first for the checker, evidence and rules). This part
covers what was built after it: the Studio 2 UI, the graph, Claude Code integration, the `lattice` CLI,
Lattice's own spec for Studio, the multi-project hub and the rebuild loop, and the first two real runs of
"Studio improves Lattice". Branch `roadmap`, nothing pushed, no remote.

## What is built (commit order)

| Commit | What |
|---|---|
| `ca18b4c` `956cc83` | Studio 2 clickable UI in a quiet minimal look; mock server API v1.3 (code lens, explain, flow, rebuild) |
| `33199e0` | Business-logic graph in React Flow (vendored bundle `lattice/studio/graph` -> `web/vendor`), `GET /api/graph` v1.4; Map, Lifecycle, Request flow |
| `c5ae3fb` `237a2c3` `03d4b46` | `python -m lattice.mcp` (stdio MCP server + PreToolUse spec guard), chat sessions on a real `claude -p --resume` process (API v1.5), chat UI, `lattice-claude-plugin/` |
| `e68a864` | Fixes: `ask --verify` -> inconclusive on failed setup (Q06), L006 skips 405-only routes, auth section of the draft reference |
| `609d350` | `lattice` console command (`studio`, `check`, `spec`, `import`, `mcp`, `gogen`, `tsgen`, `projects`) |
| `cbaafb6` `2373833` | `dogfood/studio`: Lattice's own spec for Studio (layers D and X), pre-registered 6 mutants, 6/6 caught; real bug found (reject while drafting) and fixed; `--stub-llm`; `GET /api/changes/{id}?wait=` |
| `510ed27` | One server, many projects: hub, discovery, `/p/<name>/api/*`, `GET /api/projects` (API v1.6) |
| `056bb4b` `1df1ec5` | Rebuild loop (API v1.7): worktree branch `rebuild/<id>`, agent rounds, Studio-run gates, never merges; 8 mutants, 7/8 blind (R-M2 survived: stale `git_head` cache, fixed) |
| `f9a0981` | UI: project switcher, rebuild capability gating, real-shape rebuild card |
| `75ef3fb` `dc07886` | Fixes found by the first real run: junk pathspecs, gate-env test isolation, usage kept and honest card after a crash |
| `61ca98a` `287ca07` `263338e` | Results: run 1 and run 2 of Studio improving Lattice |

## Evidence (all in `docs/results/`)

- `2026-10-10-dogfood-{prereg,results}.md`: self-spec; 6/6 pre-registered mutants caught; 1 real bug found.
- `2026-10-10-rebuild-{prereg,results}.md`: 7/8 blind, 8/8 after a post-hoc fix of a real caching bug. Reported as 7/8.
- `2026-10-10-studio-improves-lattice.md` (run 1): the real agent wrote a correct route + test; the rebuild
  crashed on a Studio commit-step bug (fixed); about $0.95.
- `2026-10-10-studio-improves-lattice-run2.md`: fresh clone, same request: draft 161 s, rebuild passed in
  round 1 (agent 139 s + both gates 96 s), about $0.63. Patch: `...-run2.patch` (`git apply`, not `am`).
- Pre-checks: `Bash(cmd with spaces:*)` parses as one rule; other Bash is denied; but acceptEdits lets the
  agent run rm/mv/cp inside the worktree, and `:*` allows extra arguments (gates are not a sandbox).

## How to run and review

```sh
uv run lattice studio                    # from the repo root: discovers dogfood/studio (lattice-studio)
uv run lattice check --project dogfood/studio
cd dogfood/studio && ./check.sh          # Lattice's spec for Studio, 159 cases
uv run pytest tests/spec tests/studio tests/mcp tests/test_cli.py -q
```

Review the run-2 result: the throwaway clone is in the session scratchpad
(`.../scratchpad/lattice-clone2`, branch `rebuild/c1`); it will vanish with `/tmp`. The patch and write-up
are in `docs/results/`. Applying it needs the spec commit too (rule R5 and its scenario), so prefer
`git fetch <clone2> rebuild/c1` over the patch if the clone still exists.

## Open items and decisions for the owner

1. **Stale rule ids after a draft renumbers rules.** Inserting R5 renumbers later rules; the trace's
   `unchecked` keys follow but the free-text `Unchecked(...)` notes in `spec.py` do not (R7..R40 are one
   too low in the dogfood spec, plus a duplicate R23 note). Nothing checks this. Candidate lint (L012).
2. **A rebuild can pass with zero tests** and `expect_body` uses `subset()`, so extra response fields pass.
   Consider requiring a test in the brief/gate and an exact-keys option.
3. **Change sets are in memory** (restart loses an accepted, unbuilt change); build state is not
   persisted either. Persisting them is a product decision.
4. **Two follow-ups on the same parent can both be drafted** (parent stays `ready` until the first
   follow-up is ready); left as an `Unchecked` note in the dogfood spec.
5. **Rebuild cost accounting** counts input/output tokens but not cache-read/write tokens (about 75x more
   in run 2); the card understates tokens.
6. **Rebuild never merges** (by design); there is no merge or discard UI, so worktrees/branches are
   removed by hand (`git worktree remove`, `git branch -D`).
7. **Read/Grep are allowed on any path** in Studio chat sessions (no write or network tool).
8. **`CLAUDE.md` and auto-memory still load** into Studio turns (not settings, so `--setting-sources ""`
   does not stop them).
9. **Housekeeping, owner's call:** `.claude/worktrees/agent-a27e4a918db076f6a` is DIRTY (uncommitted
   `bench/edit_slice` work, so it was not removed); the eight clean `lattice-frozen-*` worktrees were
   kept on request.
10. **Known test flake:** `tests/studio/test_projects.py::test_prefix_routing_and_the_bare_default` can
    fail once when two temp repos commit in different seconds. Also the old order-dependent
    `tests/importer/test_outside.py::...one_explained_disagreement`.
11. `ruff check` still reports older errors repo-wide (`lattice/spec/lint.py` is unformatted at HEAD
    around line 806); the touched dirs are clean.

## Next steps (suggested order)

1. Decide items 1-3 above; they are small and make the self-hosted loop trustworthy.
2. Repeat "Studio improves Lattice" on a second, different change (and one that should be refused or
   fail) to get beyond n=1 before any claim; pre-register it.
3. Bcrypt hashed-secret format probe and the scheduler "2 minutes vs once a minute" gap (need
   pre-registered experiments and the job-scheduler repos; deferred from this session).
4. External validity: spec-first on a second app with requirements written by someone else, and at
   least one real Postgres app.
5. Studio: merge/discard actions for a rebuild branch, real code index for the Code lens on the real
   server (currently mock-only), persisted change sets.

## Rules (unchanged, re-stated)

Never use the Anthropic API key (CLI only; child envs are stripped by `cli_env`). Run complete plans as an
epg graph with objective gates; agents never commit, the orchestrator does. Pre-register experiments and
report blind and post-hoc separately. Never edit `docs/design/lattice-visualization-figma-spec.md`. Never
commit `app.db`, `db.sqlite`, `reports/`, `research_notes/`, `docs/LLM-Native...md`, `.DS_Store`. Never
read `.env`. Do not `git stash` in this repo during verification.

## Addendum: the Streak project (first new project built with Lattice)

Repo `~/Documents/streak` (not in this repo): mobile habit-tracker API, Go + Gin + GORM + MySQL.
State at HEAD `31b1f5b`: spec final (143 rules, 153 scenarios, 15 invariants, 1 service), Go skeleton
and MySQL harness set up, `./check.sh` runs clean and is red as expected (152 findings, all
"no endpoint yet"). The app is not built. Metrics history: `metrics/phases.jsonl`.

Lattice-side findings from it (backlog):

1. **MySQL adapter and ISO `Z` instants.** `_stored`/MysqlDatabase inserts `2026-03-01T00:00:00Z`
   as text; strict MySQL 8.4 rejects the `Z` (error 1292). The Streak harness works around it by
   relaxing the throwaway container's global `sql_mode`, which also makes spec `given` rows
   non-strict. Fix in Lattice: convert ISO-Z strings for DATE/DATETIME columns.
2. **A missing spec table aborts the whole check** (uncaught DatabaseError 1146 in `_expect_db`)
   instead of a finding. The Streak skeleton therefore creates all tables up front.
3. **Accepted change sets do not mark questions answered:** the 17 questions stay open in the trace
   with their defaults rewritten. Needs an answer-recording path in Studio.
4. **The DSL cannot express** (so these rules stay Unchecked): request headers (426, Retry-After),
   arbitrary Authorization headers, carrying a value between steps (next_cursor, returned tokens),
   response header reads, pattern matches in `expect_body`.
5. **Studio's in-process mysql check** needs `lattice[db]` (PyMySQL) in Studio's venv; the project's
   own `./check.sh` uses the project's uv env, so the rebuild gate is unaffected.
6. **Studio's commit message** is the full request text and carries its own co-author trailer.
7. Layer B is skipped on MySQL by design (served on SQLite).
