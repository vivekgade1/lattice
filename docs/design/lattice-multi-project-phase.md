# Phase (planned): Lattice across multiple projects

Status: **planned, not started** (decided 2026-10-10; the owner starts a new project first and takes
this later). Today: Studio serves many projects (`lattice/studio/hub.py`, `/p/<name>/api/*`,
`GET /api/projects`, project switcher), but the MCP server and the spec guard are bound to one
project at launch (`--project`), and nothing relates one project to another.

## Goal

1. One Lattice plugin install usable on many projects, with an explicit "Lattice is on project X
   now" state and filter.
2. Relate projects: two projects that share REST API code or integrations can declare that, and
   Lattice checks it and reports cross-project impact.

## Why it is not there yet

- The MCP server was built for the Studio chat session (one session = one project), so a fixed
  `--project` was the simplest correct design; the user-level plugin made the limit visible.
- No workspace concept: a project is a directory with its spec and trace; nothing groups projects.
- A spec describes one app's own endpoints and tables; it cannot say "this endpoint is provided
  by project B", so a change in one project cannot tell what it breaks in another.

## Layers (each useful on its own)

| # | Layer | Size | What |
|---|---|---|---|
| 1 | One MCP server, many projects | small | `list_projects`, `set_active_project`, optional `project` arg on every tool; default = launch dir; discovery reuses Studio's (explicit paths, `dogfood/*`, `~/.config/lattice/projects.toml`); guard resolves the owning project by walking up to the nearest `spec.trace.json` |
| 2 | Workspace manifest | small | `lattice.workspace.toml`: projects, short names, declared links; Studio switcher becomes the project filter; Map gets a workspace level (projects as nodes, links as edges) |
| 3 | Contracts between projects | the real work | provider **exports** (endpoints, request/response shapes, access rules derived from its spec); consumer **imports** and states what it relies on; Lattice checks consumer expectations against the provider spec (and optionally the running provider); shared code = a small library project with a spec + `depends_on` |
| 4 | Cross-project impact and changes | medium | extend impact labels (not built yet / regression / contradiction) across links; accepting a provider change creates linked pending questions/changes in consumers; each project still accepts and rebuilds on its own. **Slice 4a built (2026-10-13):** `lattice contracts impact PROVIDER --candidate REF\|PATH`, MCP `contract_impact`, `GET /api/contracts/impact` (API v1.18): an advisory, read-only preview of which consumers a candidate provider version would break (`contradiction` / `regression` / `unaffected`; results `docs/results/2026-10-13-contracts-impact-results.md`). **Slice 4b built (2026-10-14):** `lattice contracts notify PROVIDER --candidate REF\|PATH` and the provider Studio's accept leave an advisory *linked question* in a consumer's own state directory for each `contradiction` row (`linked_questions/`, the first cross-project write: atomic, under a flock, never creating a state directory); the consumer's owner lists and clears them (`lattice contracts questions list\|resolve\|dismiss\|init`, `GET /api/linked-questions`, API v1.19, dogfood rules R96, R97; MCP `linked_question_list`, list-only); results `docs/results/2026-10-14-linked-questions-results.md`. **Not built:** 4c consumer-side drafting, transitive contract impact, change-set integration, any web UI. |

```
workspace
 |- billing-api  (provider)  exports: POST /invoices, GET /invoices/{id}
 |- web-app      (consumer)  imports: billing-api/POST /invoices  -> checked against the export
 '- shared-auth  (library)   exports: verify_token()  <- both depend on it
```

## Decisions to make before layer 3

- **Contract ownership:** the provider's spec is the source; consumers only declare reliance;
  disagreements surface as questions (proposed).
- **Static vs live checking:** spec-to-spec is cheap and always available; running the provider is
  more faithful but needs both apps runnable. Likely both, opt-in per link.
- **Versioning:** a contract needs a version or git ref, or "provider changed" and "consumer is
  stale" are indistinguishable.
- **Cost:** cross-project checks multiply runs; opt-in per link.

## Suggested order and evidence

Layer 1, then 2, then a first contract check (layer 3) on a deliberately small pair (for example the
job-scheduler API and a tiny consumer) before generalising. Pre-register the layer 3 experiment
(drift injected into the provider; the consumer check must catch it) and report blind and post-hoc
separately, as for the earlier experiments.
