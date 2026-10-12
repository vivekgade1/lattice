# Spike 4: connected MCPs available in Studio (issues, specs, context)

Status: research and plan. No code.

## Question

Can Studio use the MCP servers already connected in the user's Claude Code session (Jira/Atlassian,
GitHub, Linear, Notion, Slack, Google Drive, ...) to fetch data and add context to a request, for example
pull another project's spec or import issues to implement?

## Owner decision (fixed)

Use the MCP servers **already present in the Claude Code session**. Just make them available. No new
registry, no per-project server definitions.

## What exists

- Studio chat sessions run `claude -p` and are locked down on purpose: `--setting-sources ""`,
  `--strict-mcp-config --mcp-config <generated>` (only the `lattice` server), `--tools Read,Grep,Glob`,
  `--permission-mode dontAsk`, `--allowedTools <per mode>`, a `PreToolUse` guard hook
  (`lattice/studio/session.py`: `write_configs` builds `mcp.json`, `build_argv` assembles argv,
  `MODE_PROFILES` holds ask / explain / change / rebuild).
- The planner runs with an empty MCP config (`lattice/studio/plan.py`).
- `change_propose` is the only path from a request to a spec change; the owner accepts in Studio.
- The importer (`lattice/importer`) derives a draft spec from existing code.
- Claude CLI facts (from `claude --help` and `claude mcp list` in this environment):
  `--strict-mcp-config` = "only use MCP servers from --mcp-config, ignoring all other MCP configurations".
  Configured servers here are claude.ai connectors (Docs, Figma, Drive, Calendar, Gmail: connected), plugin
  servers (lattice: connected; github: **failed**, HTTP 400 "Authorization header is badly formatted";
  Atlassian, Linear, Notion, Slack, Monday, ClickUp, Datadog, Intercom, Figma plugin: **need
  authentication**).

## Findings

1. The seam is one place: stop forcing `--strict-mcp-config` (or add the user's servers to the generated
   config). Everything else (modes, allowlists, guard hook) already exists.
2. **Unverified, must be tested first:** with `--setting-sources ""`, do plugin-provided and claude.ai
   connector servers still load once `--strict-mcp-config` is dropped? The help text says plugins and
   settings sources are separate things; the answer decides between "drop the flag" and "copy definitions
   into the session config from the CLI's own config".
3. Availability is not usability: most servers need OAuth done interactively in the user's Claude Code
   first. Studio sessions are non-interactive (`-p`) and cannot complete OAuth. Servers that "need
   authentication" would be listed but unusable until authorized there. The UI should say so.
4. **Write tools are exposed too** when a whole server is made available (create issue, add comment, send
   email, post message). In `dontAsk` mode only `--allowedTools` entries run, so this is a deliberate
   allowlist decision per mode, not automatic.
5. **Prompt injection:** issue bodies, docs and emails are attacker-controllable text read by an agent
   that can call `change_propose` and, if allowed, write tools. Fetched content must be treated as data.
6. The user's own CLAUDE.md, memory and SessionStart hooks are deliberately kept out of Studio turns
   (`--setting-sources ""`); making MCPs available must not reopen that (handoff open item 8).

## Design (recommended)

- **Availability:** Studio chat sessions get the servers configured in the user's Claude Code, plus
  `lattice`. Implementation choice decided by finding 2.
- **Tool permissions by mode** (allowlist, not blanket):
  - `ask`, `explain`, `change`: allow read-style tools of external servers (get / list / search / read /
    fetch). Writes denied by default.
  - Writes opt-in per server in the project's `studio.toml`, if ever wanted.
  - `rebuild` and the planner: **no external MCPs** (build runs stay hermetic and reproducible).
- **Intake flows** (use existing pieces, no new mechanism):
  1. *Issue -> change:* in `change` mode, the user says "implement PROJ-123"; the session reads the issue
     through the server, summarizes with a citation to the source, calls `change_propose`; the owner
     reviews the normal change set and starts a rebuild.
  2. *Other project's spec:* the session reads the spec repo or document through the server (or user points
     at a local path), and the importer/`change_propose` produces a draft.
  3. *Context for ask:* answers cite which external item informed them.
- **Provenance:** every change set created this way records the external item id/URL it came from.
- **UI:** list connected servers and their state (connected / needs authentication / failed) and which
  tools are allowed in which mode.

## Plan

| Phase | Work | Exit (objective) |
|---|---|---|
| K-R | Research probe, no Lattice change: run `claude -p --setting-sources ""` with and without `--strict-mcp-config` and read the init record's server list and tool names | Answers finding 2 exactly; recorded in `docs/results/` |
| K-1 | Decide allowlist rule (read-style names) from the tool names actually exposed by connected servers | A table: server -> read tools, write tools |
| K-2 | Fix connection issues for the servers the owner wants (GitHub 400 auth header; OAuth for Atlassian etc. in Claude Code) | `claude mcp list` shows them connected |
| K-3 | Injection probe: an issue body containing instructions to call a write tool or to widen the change | Session refuses; no write tool called; change set cites the issue |
| K-4 | End-to-end probe: one real issue -> change set -> (owner accepts) -> rebuild on a throwaway project | Works, with cost and time; n=1 labeled |

Pre-register K-3 and K-4.

## Risks

- Prompt injection with write-capable tools is the main risk (finding 5). Read-only default and no MCPs in
  rebuild/planner contain it.
- Silent data exposure: reading private tickets, mail or docs into Studio transcripts that may be saved or
  shared. State this in the UI; transcripts are local.
- Non-determinism: the same request yields different context depending on external data; the change set
  must embed the fetched facts it relied on, not just a link.
- Auth fragility: expired OAuth makes a mode silently less capable; surface it.

## Open decisions for the owner

1. Which servers matter first (GitHub, Atlassian/Jira, Linear, Notion, Drive)?
2. Writes: never, or opt-in per server?
3. Should issues be imported in bulk (list a project's open issues as candidate change sets) or one at a time?
