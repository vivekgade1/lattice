# K-R: which MCP servers and tools a headless `claude -p` session sees

Date: 2026-10-12. Node K-R of the L0 research tranche (spike 4, `docs/design/spikes/04-mcp-in-studio.md`).
Claude Code 2.1.296, CLI backend only (no API key set or used), macOS. No tracked file was changed.
Server and tool NAMES only; no descriptions, no secrets.

## Orchestrator correction (re-run 2026-10-12, same machine, Claude Code 2.1.296)

The orchestrator re-ran variants A, B, C, D, F and G after reading this document and **could not reproduce
its B and F rows**. With `--setting-sources ""` and no `--strict-mcp-config`, the claude.ai connectors DO load:

| Variant (re-run) | Servers | Tools |
|---|---|---|
| A no flags | 33: 5 connected, 14 failed, 14 needs-auth | 128 (mcp 100), skills 110, plugins 17 |
| B `--setting-sources ""` | 5: 3 connected (Docs, Figma, Gmail), 2 needs-auth (Drive, Calendar), all `claudeai` | 106 (mcp 79), skills 17, plugins 3 |
| C strict + empty config | 0 | 25 (mcp 0), skills 110, plugins 17 |
| D `""` + strict + empty config | 0 | 24, skills 17, plugins 3 |
| F `""` + `--mcp-config lattice.json`, not strict (run twice) | 6: `lattice` (dynamic) + the same 5 connectors | 117 (mcp 90), skills 17, plugins 3 |
| G `--setting-sources user`, not strict | 33 as in A | 128, skills 110, plugins 17 |

So the answer to spike-4 finding 2 is **partly yes**: dropping `--strict-mcp-config` (keeping
`--setting-sources ""`) gives a session `lattice` plus the account's claude.ai connectors, without the
user's plugins, skills or SessionStart hook (skills 17 and plugins 3 are the built-ins, as in D). It does
**not** give the plugin servers (GitHub, Atlassian, Linear, Notion, Slack) or the `~/.claude.json`
servers; those need the `user` source (G). The cause of the earlier B/F result is unknown (connector
discovery may not have been cached in the first runs); treat connector visibility as time-dependent. The
text below keeps the first run's data; where it contradicts this section, this section wins.

## Question

Spike 4 finding 2: with `--setting-sources ""`, do plugin-provided and claude.ai connector MCP servers still load
once `--strict-mcp-config` is dropped? Behind it: which variant of the flags does a Studio chat session use
today, and what is the smallest change that gives a Studio session the user's configured servers plus
`lattice` while the rebuild agent stays hermetic?

## Method

Scratch directory (also the cwd for A-H):
`/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/4a12ca95-cf29-48df-94c4-af809da90b17/scratchpad/k-r/`.
Every run:

```
claude -p --output-format stream-json --verbose --max-turns 1 <FLAGS> -- "Reply with the single word ok. Do not call any tool."
```

(`--max-turns` is accepted although it is absent from `claude --help`.) stdin was `/dev/null` (A1 did not
redirect it; the CLI only warned). The `system`/`init` record's `mcp_servers` (name, status, source) and `tools`
(names) were read with a small script. Files: `lattice.json` =
`{"mcpServers":{"lattice":{"command":"<repo>/.venv/bin/python","args":["-m","lattice.mcp","serve","--project","<repo>","--readonly"]}}}`;
`bogus.json` = the same plus a `bogus` server whose command is `/nonexistent/no-such-server-binary`;
`copied.json` = `lattice.json` plus the user's `codegraph` definition copied from `~/.claude.json` (command and
args only; it has no env or headers). The `.venv` interpreter is used because the system Python cannot import
`lattice` outside the repo.

| Run | FLAGS |
|---|---|
| A1, A2 | (none) |
| B | `--setting-sources ""` |
| C | `--strict-mcp-config --mcp-config lattice.json` |
| D | `--setting-sources "" --strict-mcp-config --mcp-config lattice.json` (what Studio chat and the rebuild agent use) |
| E | as D with `--mcp-config bogus.json` |
| F (extra) | `--setting-sources "" --mcp-config lattice.json` (D without `--strict-mcp-config`) |
| G (extra) | `--setting-sources user --mcp-config lattice.json` (no strict) |
| H (extra) | as D with `--mcp-config copied.json` |
| S (extra) | the exact argv of `lattice.studio.session.build_argv` (mode `change`, configs from `write_configs`), plus `--max-turns 1`, run with cwd = the repo, as the Studio does |

B-E ran in parallel, as did F-H. 10 `claude -p` calls in all (the brief said about 8; F, G, H and S were
added because B alone answered finding 2 but not the "smallest change" question).

## Data

Source of a server in the init record: `plugin`, `user` (`~/.claude.json`), `claudeai` (account connectors),
`dynamic` (from `--mcp-config`). Tool names of a server are `mcp__<server with : . space -> _>__<tool>`, e.g.
`mcp__plugin_lattice_lattice__*`, `mcp__claude_ai_Gmail__*`. "builtin" = non-`mcp__` tools.

### A1 and A2: no extra flags (identical to each other)

| Status | Servers (source) | Tools |
|---|---|---|
| connected (5) | `plugin:lattice:lattice` (plugin), `codegraph` (user), `claude.ai Claude Docs`, `claude.ai Figma`, `claude.ai Gmail` (claudeai) | lattice 11, codegraph 10, Claude Docs 8, Figma 41, Gmail 30 |
| needs-auth (14) | plugin: finance slack; productivity notion, linear, atlassian, monday, clickup; design intercom; engineering datadog; data hex, amplitude, amplitude-eu, definite (12). claudeai: Google Drive, Google Calendar (2) | 0 |
| failed (14) | plugin: github; finance snowflake, databricks, google calendar, gmail; productivity google calendar, gmail; design google calendar, gmail; engineering google calendar, gmail; data snowflake, databricks (13). user: `aetheris-bff` (1) | 0 |

Totals: 33 servers (5 + 14 + 14), 128 tools (28 builtin + 100 MCP). User-level plugin servers do appear
(26 `plugin:` entries), including the GitHub plugin (`plugin:github:github`, failed). A2 matched A1 in server
order, status, tool count and tool names. 17 plugins, 110 skills, 151 slash commands, 8 agents loaded. A
`SessionStart:startup` hook ran and returned 3604 characters.

### B: `--setting-sources ""`

| Status | Servers | Tools |
|---|---|---|
| (none) | `mcp_servers` is `[]` | 24, all builtin |

No plugin servers and none of the user's own `~/.claude.json` servers (`codegraph`, `aetheris-bff`). 3 plugins
(builtin ones only), 17 skills, 53 slash commands, 5 agents, no hook. **Correction:** the orchestrator's re-run
saw the 5 claude.ai connectors here (see the correction section); the "none" above was not reproduced.

### C: `--strict-mcp-config --mcp-config lattice.json`

| Status | Servers | Tools |
|---|---|---|
| connected | `lattice` (dynamic) | lattice 9 (`--readonly`), builtin 25 |
| failed / needs-auth | none listed | - |

34 tools. No plugin or connector server appears. But 17 plugins, 110 skills, 8 agents still load and the
`SessionStart:startup` hook still ran (3604 chars): `--strict-mcp-config` only filters MCP servers.

### D: `--setting-sources "" --strict-mcp-config --mcp-config lattice.json`

| Status | Servers | Tools |
|---|---|---|
| connected | `lattice` (dynamic) | lattice 9, builtin 24 |

33 tools; 3 plugins, 17 skills, 5 agents, no hook. No user-level plugin servers.

### E: D with a server that cannot start

| Status | Servers | Tools |
|---|---|---|
| connected | `lattice` (dynamic) | lattice 9, builtin 24 |
| failed | `bogus` (dynamic) | 0 |

The run still exits 0 and answers `ok`; stderr is empty. The init record carries `name`, `status`, `source`
only, with no error text and no separate warning record, so a failure is visible only as `status: "failed"`
with zero tools of that server. The same holds for the GitHub plugin in A: `failed`, no reason in the record.

### F: `--setting-sources "" --mcp-config lattice.json` (no `--strict-mcp-config`)

| Status | Servers | Tools |
|---|---|---|
| connected | `lattice` (dynamic) | lattice 9, builtin 24 |

First run: same as D (33 tools). **Correction:** re-run twice by the orchestrator: `lattice` plus the 5 claude.ai
connectors (6 servers, 117 tools). Dropping `--strict-mcp-config` does add the connectors.

### G: `--setting-sources user --mcp-config lattice.json` (no strict)

| Status | Servers | Tools |
|---|---|---|
| connected (6) | everything A had connected, plus `lattice` (dynamic) | lattice 9, plugin lattice 11, codegraph 10, Docs 8, Figma 41, Gmail 30 |
| needs-auth (14), failed (14) | same as A | 0 |

34 servers, 137 tools (28 builtin). 17 plugins, 110 skills, 8 agents, and the `SessionStart:startup` hook ran
(3604 chars). Two `lattice` servers are visible: `mcp__lattice__*` (Studio's, 9 tools with `--readonly`) and
`mcp__plugin_lattice_lattice__*` (the user's plugin, 11 tools including `change_propose` and `check_run`).

### H: D with `codegraph` copied into `--mcp-config`

| Status | Servers | Tools |
|---|---|---|
| connected | `lattice`, `codegraph` (both dynamic) | lattice 9, codegraph 10, builtin 24 |

43 tools; 3 plugins, 5 agents, no hook. A user-scope stdio server whose definition is copied into the
generated config works under `--setting-sources ""`.

### S: Studio's real `build_argv` (mode `change`)

| Status | Servers | Tools |
|---|---|---|
| connected | `lattice` (dynamic) | lattice 11 (not read-only in `change`), builtin 3 (`Read`, `Grep`, `Glob` via `--tools`) |

14 tools, 0 slash commands, 0 skills, 5 agents, no hook. Identical server list to D.

### Comparison

| Variant | setting-sources | strict | servers (connected / failed / needs-auth) | user/plugin/claudeai servers visible | tools | SessionStart hook ran |
|---|---|---|---|---|---|---|
| A (x2) | default | no | 5 / 14 / 14 | yes (all, incl. github plugin = failed) | 128 | yes |
| B | "" | no | first run 0 / 0 / 0; re-run 3 / 0 / 2 | first run no; **re-run: the 5 claude.ai connectors** | 24; re-run 106 | no |
| C | default | yes | 1 / 0 / 0 | no | 34 | yes |
| D | "" | yes | 1 / 0 / 0 | no | 33 | no |
| E | "" | yes | 1 / 1 / 0 | no | 33 | no |
| F | "" | no | first run 1 / 0 / 0; re-run 4 / 0 / 2 | first run no; **re-run: lattice + the 5 claude.ai connectors** | 33; re-run 117 | no |
| G | user | no | 6 / 14 / 14 | yes (all) | 137 | yes |
| H | "" | yes | 2 / 0 / 0 | only the copied `codegraph` | 43 | no |
| S | "" | yes | 1 / 0 / 0 | no | 14 | no |

## Findings

1. With `--setting-sources ""`, plugin servers and the user-scope servers in `~/.claude.json` do not load,
   strict or not (B, F). **Corrected:** the claude.ai connectors DO load once `--strict-mcp-config` is dropped
   (orchestrator re-run of B and F). Under D it is `--strict-mcp-config` that hides the connectors, and the
   empty setting-sources list that hides plugins and user-scope servers.
2. Servers come back only when the user setting source is on (A, G). Then everything returns with its status:
   the 26 plugin servers (1 connected, 12 needs-auth, 13 failed), 2 user servers, 5 connectors.
3. `--setting-sources` and `--strict-mcp-config` are independent and each does one thing. Setting sources
   control plugins, skills, agents, the SessionStart hook and the user-scope/plugin/connector servers.
   `--strict-mcp-config` only ignores every MCP source except `--mcp-config` (C: plugins and hook still run).
4. Needs-auth and failed servers contribute no tool names to `tools` in the headless init. The only evidence of
   them is their `mcp_servers` entry. No error text is in the record, so Studio cannot tell "github: HTTP 400"
   from "no token" from the init alone.
5. Of the 5 servers that are usable headlessly here, 3 are connectors (Docs, Figma, Gmail), 1 is `codegraph`,
   1 is the user's `lattice` plugin. None of the issue trackers named in spike 4 (Atlassian, Linear, Notion,
   Slack, GitHub) is usable now: all are needs-auth or failed. The connector state differs from the spike's
   table (Drive and Calendar are needs-auth in this run, not connected).
6. Turning on the user setting source also turns on the SessionStart hook (3604 characters per session),
   17 plugins and 110 skills, which is what `--setting-sources ""` was added to avoid (spike finding 6).
7. A copied stdio definition is honoured under `--setting-sources ""` (H). A plugin or connector server
   cannot be copied this way: those definitions are not in `~/.claude.json` (connectors live on the account;
   plugin definitions are in plugin directories).
8. `--max-turns 1` and `--disable-slash-commands` both work; with the 33-tool D set the prompt is about 5x
   cheaper than A (cost per run $0.018 vs $0.097, though prompt caching skews single-run costs).
9. A name clash: with the user source on (G), the user's plugin `lattice` server appears next to Studio's
   `lattice`. Studio's allowlists name `mcp__lattice__*`, so under `dontAsk` the plugin copy's
   `mcp__plugin_lattice_lattice__*` would not be covered by the allowlist and so would be denied (inferred from the argv, not run).

## Answer to spike-4 finding 2

Which variant is Studio's chat session today: D. `build_argv` (`lattice/studio/session.py:573`) passes
`--setting-sources ""`, `--strict-mcp-config --mcp-config <generated>`, and S confirms the real argv yields
exactly `lattice` and no other server. The rebuild agent (`build_rebuild_argv`, `lattice/studio/rebuild.py:980`)
and the planner (`lattice/studio/plan.py:713`) use the same two flags.

Finding 2's answer (corrected by the orchestrator's re-run): dropping `--strict-mcp-config` while keeping
`--setting-sources ""` makes the claude.ai **connectors** load (Docs, Figma, Gmail connected; Drive, Calendar
needs-auth) without the user's plugins, skills or hook; it does **not** make plugin servers (GitHub etc.) or
`~/.claude.json` servers load. The smallest useful change for "the connected MCPs in Studio" is therefore:
in `build_argv`, delete `--strict-mcp-config` only (keep `--setting-sources ""` and `--mcp-config`). That is
variant F: `lattice` + connectors, no hook. Plugin servers still need the `user` source (G, below).

Smallest change for a chat session to see the user's servers plus `lattice`: in `build_argv` only, replace
`"--setting-sources", ""` by `"--setting-sources", "user"` and delete `"--strict-mcp-config"` (keep
`--mcp-config <generated>` so `lattice` is still added). That is variant G, measured: 34 servers including the
5 usable ones, 137 tools. Rebuild stays hermetic with no edit because `build_argv` is called only by
`SessionManager` (`session.py:726`) and `build_rebuild_argv` (the only call is `rebuild.py:2955`) and the
planner have their own argv with `--setting-sources "" --strict-mcp-config`.

Cost of that change, observed in G: the user's SessionStart hook, plugins (17) and skills come back
(finding 6; `--disable-slash-commands` is already in the argv but its effect together with `user` was not
measured), and the user's plugin `lattice` server appears beside Studio's (finding 9). Allowlist per mode
(`--allowedTools`) is then the control, as spike K-1 plans. The alternative that keeps user settings out is to
copy definitions into the generated mcp.json (H); that covers only `~/.claude.json` servers (here `codegraph`,
`aetheris-bff`), not plugin or connector servers, so it does not reach the spike's target servers.

## What could not be determined

- Why each server failed or needs auth: the init record has no error text. The GitHub 400 and the
  `aetheris-bff` ECONNREFUSED are known from the CLI's own notice in the invoking session, not from these runs.
  `GITHUB_PERSONAL_ACCESS_TOKEN` being unset was not checked.
- Whether `--setting-sources user` also injects the user's `CLAUDE.md` and memory into the prompt: not
  inspected (only the hook's output size and the plugin/skill counts were). `memory_paths` in init was
  identical across all variants.
- Whether `--setting-sources project`, `local` or a combination gives connectors/plugin servers without the
  hook; not run.
- Whether the hook can be suppressed while keeping the user source (e.g. through `--settings`); not run.
- Whether plugin or connector tools are callable in a `-p` session under `dontAsk` with an allowlist: only
  the init list was read, no tool was called.
- Whether a needs-auth server becomes usable headlessly after OAuth in interactive Claude Code: not tried
  (OAuth cannot be completed non-interactively).
- cwd effects: A-H ran in the scratch directory, S in the repo. The repo has no `.mcp.json`, and
  `~/.claude.json` lists no project-scoped servers, so a project-scope difference is not expected, but A was
  not re-run in the repo.
- Stability over longer periods: A was run twice about a minute apart; connector status can change with time.

## Cost and time

10 `claude -p` calls (A1, A2, B, C, D, E, F, G, H, S), all `ok`, exit 0. Reported `total_cost_usd` per run:
A1 0.097, A2 0.097, B 0.016, C 0.054, D 0.018, E 0.018, F 0.002, G 0.084, H 0.022, S 0.034; about $0.44 total.
Costs are cache-dependent (F is low because the prompt was cached by B/D), so they are not a clean
per-variant figure. Wall time 1-8 s per call (A1 8 s with a cold start, the rest 1-4 s), about 25 s of CLI
time in all; the research took a few minutes. Raw records are in the scratch directory above.
