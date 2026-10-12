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

## L0 outcome (2026-10-12; every number re-checked by the orchestrator against its source)

| Node | Result | Document |
|---|---|---|
| K-R (spike 4) | Studio chat sessions see only `lattice` today. Dropping `--strict-mcp-config` (keeping `--setting-sources ""`) adds the claude.ai connectors (Docs, Figma, Gmail; Drive and Calendar need auth) without the user's plugins or hook. GitHub, Atlassian, Linear, Notion and Slack are *plugin* servers: they need the `user` source (hook, 17 plugins, 110 skills return), and none is usable now (GitHub failed on the unset token, the rest need auth). The node's first run said the connectors stay hidden; the orchestrator's re-run disproved it and the document is corrected. | `2026-10-12-k-r-mcp-visibility.md` |
| P0 (spike 1a) | Query count is a stable metric: `GET /v1/home` = exactly N + 6 statements in 50 of 50 runs, an N+1 confirmed in the handler. A budget needs a seed size (or "constant + per item"); latency is noisy (CV 5-23%). | `2026-10-12-p0-query-count-baseline.md` |
| M-R (spike 2) | 38 seams: 31 manifest-able, 7 SDK-needed. A descriptor of data fields plus 3 code items (adapter, optional clone, layer-A checker) would do; per-engine marginal cost is not measurable from history; core must be refactored to read a descriptor (unmeasured). | `2026-10-12-m-r-engine-seams.md` |
| S0 (spike 1b) | 102 attacks, 88% with the defence in the spec's own words; 25 covered, 77 new; 50 of the new are writable in today's DSL, 16 are blocked (headers, carried values), 11 need concurrency or deployment control. Five spec inconsistencies found. | `2026-10-12-s0-streak-auth-attack-list.md` |
| I-R (spike 3) | The frequency x cost method's top 3 overlap the predicted owner priorities on one item; it cannot see human time, risk or recurrence. Missing signal fields are listed (per-layer gate timings, give-up cause, human-intervention events, ledger wall_s for parallel nodes). | `2026-10-12-i-r-improvement-ranking.md` |

## What L0 changes about the build levels

1. **1a is cheap to continue.** The metric is deterministic, so P1 (seeded regression) will detect
   reliably; P2 has a real target already: the `/v1/home` N+1 (budget "constant + per item" to
   "constant").
2. **4 is blocked on a decision and on auth, not on code.** Connectors only (one flag) or also
   plugin servers (user source, hook cost); and the GitHub token.
3. **2 is sized.** A descriptor probe (M-P) for MySQL is a small, bounded build.
4. **1b has a transport gap before an agent gap.** The module needs its own HTTP client: the
   scenario DSL cannot send headers or carry values (16 of the new attacks).
5. **3 needs signals first.** Per-layer gate timings, a give-up cause field and human-intervention
   events are small Lattice changes that are useful on their own (I-1/I-2).
6. **Streak spec questions** (R28 vs R54/R79 and four more, listed in the S0 document) are owner
   decisions, best raised as Studio questions.

