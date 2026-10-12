# Next phases and spikes: plan and L0 pre-registration (2026-10-12)

Branch `next-phases` (from `roadmap` 59bece3). Sources: `docs/design/spikes/00..04`,
`docs/design/lattice-multi-project-phase.md`. Owner decisions for this tranche: **L0 research
only**, the recommended defaults of the spike docs, no Lattice code.

## Defaults in force (overturnable before any build level)

- External MCPs: read-style tools only in ask/explain/change, never in rebuild or the planner;
  writes off. First server of interest: GitHub.
- Perf: first metric is **query count per request** (latency second); budgets stay in a side
  file until the owner decides on spec syntax.
- Marketplace: descriptor-only slice; the index is a directory in this repo for now.
- Security module: separate module outside the build gates; black-box first; own systems only.
- Improvement loop: per-project log; proposals target modules and docs, not the checker, first.
- Always: CLI backend only (no API key), agents never commit, no automatic merges, results in
  `docs/results/`, blind and post-hoc reported separately.

## L0 nodes (parallel; read-only on Lattice and on Streak `main`)

| Node | Spike | Work | Exit criterion (fixed here, before running) |
|---|---|---|---|
| K-R | 4 | `claude -p` with and without `--strict-mcp-config` (and `--setting-sources ""`): read the init record's server list and tool names | A table: which servers and tools a headless session sees in each mode; answers spike 4 finding 2 exactly |
| P0 | 1a | One Streak list endpoint on `build/streak` (detached worktree): queries and latency per request, 10 runs, statement log | Written baseline with variance; says whether query count is stable enough to be a budget metric (yes/no with the numbers) |
| M-R | 2 | Read the git history of the MySQL and Go target commits; list every core seam touched | A table of seams with line counts, each classed manifest-able or SDK-needed; a recommendation on SDK size |
| S0 | 1b | Hand-written attack list from the Streak spec for the auth feature, no agents | List of attacks, the rule each violates, how many are derivable from the spec alone vs need app knowledge |
| I-R | 3 | Rank Streak backlog, gate profile and handoff open items by hand with the proposed method | Ranked list of the top items with the signal for each; the method written down; a statement of what is missing to automate it |

Each node writes exactly one file `docs/results/2026-10-12-<node>.md` (plus raw data files under
`docs/results/data/next-phases/` where needed) and reports. The orchestrator verifies claims
independently before committing.

## After L0

The owner picks which spike to build from the evidence (the order in `spikes/00-index.md` is
the default: 4, 1a, 2, 1b, 3, with the multi-project layers 1 and 2 alongside). Build levels
need their probes pre-registered first.
