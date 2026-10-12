# Lattice Studio API contract (v1.11)

`lattice studio [DIR ...] [--default NAME] [--port N] [--mock FIXTURE_DIR]` serves the web UI at `/` and this JSON API, over one project or many (v1.6). It polls; there are no websockets.

**A project directory** holds:
- `REQUIREMENTS.md`: the owner's text;
- `spec.py`: the module defining `SPEC`;
- `spec.trace.json`: the `lattice.spec.rules.Trace`;
- `studio.toml`: the app hookup for checks, i.e. the check command, or the harness refs (`app`, `db`, `login`, `runtime`, `services`) plus `database`. It is optional; without it, the app overlay and `--verify` are off.

**A "target"** is the trace's link form: `scenario:<name>`, `invariant:<name>`, `machine:<Table.column>`, `service:<name>`, `profile:<Table[.column]>`, plus `rule:<Rn>` and `cell:<row>|<METHOD /template>` (an access-matrix cell).

## GET /api/model
```json
{
  "project": {"name": "...", "root": "...", "git_head": "abc1234"},
  "rules": [{"id": "R12", "text": "...", "section": "...", "line": 33,
             "status": "checked|unchecked|context|untraced|probe_gap",
             "links": ["scenario:..."], "questions": ["Q3"], "unchecked_reason": null}],
  "markdown": "full REQUIREMENTS.md text",
  "views": { "...": "exactly lattice.spec.review's ReviewData as JSON (counts, questions, rule_rows, access_groups[cells keyed 'row|col'], section6{invariants,machines,services,scenarios}, probe_gaps, unchecked_trace, unchecked_notes, lint_gaps, back_translation)" },
  "data_model": {"tables": [{"name": "JobRun", "sql": "job_runs",
      "columns": [{"name": "status", "type": "str", "nullable": false, "one_of": ["queued","running"],
                   "unique": false, "private": false, "bounds": "1-100 chars or null"}]}]},
  "app": {"configured": true, "last_check": null | {"at": "iso", "verdict": "pass|fail",
          "counts": {"A":0,"B":0,"C":0,"D":0,"X":0},
          "targets": {"scenario:x": "pass|fail", "invariant:y": "pass|fail"},
          "findings": [{"layer":"D","target":"scenario:x","message":"..."}]}},
  "changes": [{"id": "c1", "request": "...", "status": "drafting|checking|ready|failed|accepted|rejected"}]
}
```

## POST /api/ask
Request: `{"question": "...", "selected": ["cell:eve|POST /applications/{}/runs/{}/cancel"], "verify": false}`

Answer:
```json
{"kind": "yes|no|value|not_specified", "answer": "short answer", "explanation": "1-3 sentences",
 "citations": [{"target": "scenario:...", "step": 3, "quote": "POST ... as eve -> 200"}],
 "citations_checked": true, "rejected_citations": [],
 "verified": null | {"status": "agrees|disagrees|error", "detail": "...", "scenario": "<the generated scenario as readable steps>"},
 "usage": {"seconds": 0, "tokens": 0}}
```
The UI highlights every cited target on the views.

## POST /api/changes
Request: `{"request": "only app admins may cancel runs", "selected": [...]}`. Response: `{"id": "c1"}`, then poll `GET /api/changes/c1`:
```json
{"id": "c1", "request": "...", "status": "drafting|checking|ready|failed", "error": null,
 "markdown_diff": "unified diff of REQUIREMENTS.md",
 "rules": {"added": ["R66"], "changed": [{"old": "R46", "new": "R46"}], "removed": ["R40"]},
 "targets": {"added": [], "removed": [], "modified": []},
 "view_diff": {"cells": [{"row": "eve", "col": "POST /applications/{}/runs/{}/cancel", "old": "200", "new": "403"}],
               "invariants": {"added": [], "removed": []}, "machines": {"changed": []},
               "scenarios": {"added": [], "removed": [], "modified": []}},
 "checks": {"valid": true, "problems": [], "lint": [{"code": "L004", "message": "...", "fix": "..."}],
            "conflicts": [{"a": "scenario:...", "b": "scenario:...", "message": "eve POST .../cancel: 200 vs 403", "question": "Which one holds?"}],
            "impact": null | {"verdict": "pass|fail", "failing": [{"target": "...", "label": "not_built_yet|regression|contradiction", "message": "..."}]}},
 "questions": [{"id": "Q13", "text": "...", "default": "..."}]}
```
- `GET /api/changes/c1?wait=S` (0 < S <= 60): answers once the change's background job has finished (it is `ready` or `failed`, or its cancelled run has stopped), or after S seconds, whichever is first; the same record. A `wait` that is not a number from 0 to 60 is 422.
- `POST /api/changes/c1/accept`: writes `REQUIREMENTS.md`, `spec.py` and `spec.trace.json`, then makes one git commit. Returns `{"commit": "sha", "message": "..."}`.
- `POST /api/changes/c1/reject`: returns `{"ok": true}`.

## POST /api/check
Runs the full check of the current spec against the app in the background. Returns `{"started": true}`; then poll `GET /api/model` for `app.last_check`.

## v1.1 additions (gaps the UI found; the UI already accepts these shapes)
1. **`view_diff` items carry content:**
   - `invariants.added/removed` are `{"target", "name", "sentence"}`;
   - `scenarios.added/removed/modified` are `{"target", "name", "steps": ["readable step", ...]}`;
   - `machines.changed` is `{"target", "old": "moves text", "new": "moves text"}`.
2. **`rejected_citations`** are `{"target", "reason"}`.
3. **Follow-ups:** `POST /api/changes` takes an optional `"parent": "c1"`, for answering a change set's conflict or question as a follow-up. The server drafts it on top of the parent's proposal and supersedes the parent.
4. `app.last_check.targets` may include `cell:` and `profile:` entries when known; otherwise the UI shows scenario, invariant, machine and service results only.
5. **Cancelling and failures:** `DELETE /api/changes/{id}` cancels a change still drafting or checking (`status: "cancelled"`). `GET /api/model` carries `app.check_running: bool`, and `app.last_check` may carry `"error": "..."` when the check could not run.
6. `rules[].questions` stays a list of ids; resolve them through `views.questions`.
7. **Observed answers:** for a `not_specified` answer with `"verify": true`, `verified.status` may be `"observed"`, meaning what the app currently does with no expectation from the spec, with `detail` giving the actual statuses and bodies.

## v1.2 additions
8. **Inconclusive verification:** `verified.status` may be `"inconclusive"`. A setup step of the generated scenario failed before the step that tests the claim, so the app neither confirmed nor contradicted the answer; `detail` says which step failed and why. `"disagrees"` now means only that **the claim step** failed.
9. **Answer and verification details:** the answer carries `verified.claim_step` (1-based) when a scenario was run, and `usage.digest_elements` (how many spec elements were sent to the model).

## v1.3 additions (Studio 2: code lens, explain, request flow, rebuild)
All new endpoints are plain JSON and polled; there are no websockets. Unknown ids and paths answer 404 `{"detail": "..."}`, a missing required query parameter or an empty body field answers 422. The status enum of a change set (`drafting|checking|ready|failed|accepted|rejected|cancelled`) does **not** change; build progress is the separate `build` field below.

### `build` on `model.changes[]`
Every change whose `status` is `"accepted"` carries `"build": {"state": "not_built|rebuilding|built|gave_up", "rebuild_id": "b1"}`; `rebuild_id` is absent until a rebuild has been started for the change. Changes with any other status have no `build`. A change is `not_built` after accepting or after a stopped rebuild, `rebuilding` while a rebuild runs, `built` once a rebuild's check passed, `gave_up` when a rebuild reached its round limit with findings left.

When a rebuild passes: the changes' `build.state` becomes `built`, `app.last_check` becomes a `pass` check (no findings), `project.git_head` moves to the rebuild's commit, and `GET /api/code/file` marks the rebuilt lines (`rebuilt_in`) and no longer reports failing coverage or findings. *(Superseded in v1.7: the commit lives on the rebuild's own branch and `project.git_head` does not move; see "v1.7 additions".)*

### GET /api/code/files
The source files that touch the spec: `[{"path": "internal/app/runs.go", "language": "go", "size": 3912, "rules": ["R42", "R44", "R46"]}]` (`rules` are rule ids, sorted).

### GET /api/code/file?path=
```json
{"path": "internal/app/runs.go", "language": "go", "lines": ["package app", "..."],
 "annotations": [{"line": 43, "rules": ["R44"],
                  "finding": {"code": "D", "message": "..."},
                  "coverage": "covered|failing|untested",
                  "rebuilt_in": "c1"}]}
```
`line` is 1-based and within `lines`. `finding` and `rebuilt_in` are optional. `rebuilt_in` is the id of the change whose rebuild produced the line. 404 for an unknown `path`, 422 without one.

### GET /api/code/links?target=
For a `rule:Rxx`, `cell:<row>|<METHOD /template>` or `scenario:<name>` (also `invariant:`, `machine:`) target, the code that serves it:
`[{"path", "line_from", "line_to", "symbol", "why"}]` (empty list when no code is linked: a visible gap).
For a `code:<path>:<line>` target, the rules that line serves: `[{"rule": "R44", "text": "...", "status": "checked"}]`. 404 for a code target naming an unknown path or a line outside the file; 422 without `target`.

### POST /api/explain
Request: `{"question": "...", "selected": ["code:internal/app/runs.go:43", "rule:R44"]}`. Answers like `POST /api/ask` (same card, `verified` always `null`), from the code: citations may be `code:<path>:<line>` (with `quote` the line's text) beside `rule:`, `scenario:` and `invariant:` targets. A question the indexed code cannot answer comes back `not_specified` with no citations. 422 when `question` is empty.

### GET /api/flow?endpoint=&caller=
One request traced end to end. `endpoint` is a route (`POST /applications/{}/runs/{}/cancel`; short forms such as `POST /runs/{}/cancel` and `POST /configs/{}/runs` are accepted), `caller` a user name (default `ann`).
```json
{"endpoint": "POST /runs/{}/cancel", "caller": "ann",
 "stages": [{"stage": "access", "symbol": "appAccess", "path": "internal/app/handlers.go", "line": 23,
             "rules": ["R4", "R5"], "reads": ["memberships"], "writes": []}],
 "divergence": null | {"stage": "database write", "expected": "...", "actual": "..."}}
```
`divergence.stage` is one of `stages[].stage`. It is set while the last check fails (or, for a pending accepted or ready change, shows what will differ) and `null` once the check passes. 404 for an endpoint with no flow.

### POST /api/rebuild
Request: `{"changes": ["c1"], "model": "sonnet", "max_rounds": 4}` (`model` and `max_rounds` optional). Response: `{"id": "b1"}`. 409 when a change is not `accepted`, or is already `rebuilding` or `built`; 404 for an unknown change; 422 for an empty list or `max_rounds` below 1. Several changes rebuild together (one brief, one gate).

### GET /api/rebuild/{id}
```json
{"id": "b1", "changes": ["c1"], "status": "queued|running|passed|gave_up|stopped",
 "model": "sonnet", "max_rounds": 4, "started": "2026-10-10T09:00:00Z",
 "rounds": [{"n": 1, "started": "iso", "agent_summary": "what the agent changed",
             "files_changed": ["internal/app/runs.go"],
             "check": {"verdict": "pass|fail", "counts": {"A": 0, "C": 0, "D": 2, "X": 0},
                       "findings": [{"id": "f1", "title": "...", "target": "scenario:..."}]}}],
 "targets": [{"id": "scenario:...", "label": "the not-built-yet check", "done": false}],
 "commit": {"sha": "...", "message": "...", "files_changed": ["..."]}}
```
- `rounds` lists finished rounds in order; while one is in progress its entry has `check: null`, an empty `agent_summary` and no `files_changed`.
- `targets` are the checks labelled *not built yet* by the impact run; `done` flips when a round passes them.
- `commit` is present only when `status` is `passed`.
- A `fail` round's findings are the next round's brief; the rebuild is `gave_up` when `max_rounds` rounds ended without a pass, and `stopped` after `DELETE`.

### DELETE /api/rebuild/{id}
Stops a queued or running rebuild between rounds: `{"id": "b1", "status": "stopped"}`; its completed rounds stay in the record and its changes go back to `build.state` `not_built`. 409 once the rebuild finished (`passed`, `gave_up` or `stopped`).

## v1.4 additions (business-logic graph)
Derived entirely from data the mock already serves (`model.json`, `code/index.json`, the `flow-*.json` fixtures); nothing is invented. Backward compatible: v1.3 shapes are unchanged except that flow stages gain two fields.

### GET /api/flow: stage `id` and `next`
Every `stages[]` item also carries `"id"` (stable slug of `stage`, unique within the flow, e.g. `"database-write"`) and `"next"` (list of the stage ids that follow: today the single following stage, `[]` for the last). A graph draws the flow as lanes from these.

### GET /api/graph?focus=&kinds=&depth=&limit=
| param | meaning |
|---|---|
| `focus` | optional target; omitted: the whole graph. Accepts any node `target`, a `cell:<row>\|<route>` (resolves to that endpoint), a `profile:<Table>.<column>` (resolves to the table) or an `endpoint:` route in any spelling (`POST /runs/{}/cancel`). 404 if no node matches. |
| `depth` | hops from `focus`, integer 0..6, default 1; ignored without `focus`. 422 otherwise. |
| `kinds` | comma list of node kinds to keep (`rule,endpoint,table,service,scenario,code,invariant,machine`); default all. 422 on an unknown kind. The focus node is kept whatever its kind. Traversal runs over the full graph, then filters, so excluding `rule` does not disconnect the rest. |
| `limit` | max nodes, 1..5000, default 400 (the full mock graph has 278). Past it the nodes nearest the focus win, then rules, endpoints, tables, services, machines, invariants, code, scenarios; `truncated` is `true`. |

```json
{"nodes": [{"id": "rule:R44", "kind": "rule", "label": "R44", "sub": "At most one queued or running run per config: ...",
            "target": "rule:R44", "trust": "checked", "failing": 0, "rules": ["R44"]}],
 "edges": [{"id": "rule:R44>traces>scenario:a_second_live_run_is_refused", "source": "rule:R44",
            "target": "scenario:a_second_live_run_is_refused", "kind": "traces"}],
 "counts": {"nodes": {"rule": 65}, "edges": {"traces": 378}, "total": {"nodes": 278, "edges": 483}},
 "truncated": false, "focus": null, "depth": null,
 "change": "c0", "diff": {"endpoint:POST /applications/{}/runs/{}/cancel": "modified"},
 "machines": [{"target": "machine:JobRun.status", "name": "JobRun.status",
               "states": [{"id": "queued", "initial": true}, {"id": "cancelled", "final": true}],
               "moves": [{"from": "queued", "to": "cancelled", "rules": ["R47"]}],
               "rules": ["R47", "R54"], "trust": "checked", "failing": 0}]}
```
**Nodes.** `id` equals `target` and is unique; `target` is in the contract's grammar. `label`/`sub` are display text (`sub` optional).

| kind | target | built from |
|---|---|---|
| `rule` | `rule:R44` | `model.rules` |
| `endpoint` | `endpoint:POST /applications/{}/runs/{}/cancel` | the access-matrix columns (`views.access_groups[].columns`) |
| `table` | `profile:JobRun` | `data_model.tables` (`sub` is the SQL name) |
| `service` / `invariant` / `machine` | `service:worker`, `invariant:...`, `machine:JobRun.status` | `views.section6` |
| `scenario` | `scenario:<name>` | `views.section6.scenarios` |
| `code` | `code:<path>:<line>` | one per (file, symbol) in `code/index.json` links and flow stages; `line` is the first line seen for the symbol, so the target resolves in `GET /api/code/links` |

- `trust`: `checked|unchecked|context|untraced|probe_gap|not_built`. A rule's is its `status`, or `not_built` while an accepted change that adds or changes it is not yet built. Every other node takes the worst (order `unchecked, probe_gap, untraced, not_built, checked, context`) trust of the rules in its `rules`; `context` when it has none.
- `rules`: ids of the rules joined to the node by a **direct** edge (a rule node: itself), sorted numerically.
- `governed_by` (endpoint and table nodes only): ids of every rule joined to the node by a direct **or derived** edge, sorted numerically; always a superset of `rules`. The Map's rule-count badge reads it.
- `failing`: count of failed checks (`app.last_check.targets` entries equal to `fail`): a scenario/invariant/machine/service node is 1 or 0 for itself; a rule counts its failing links; any other node counts the failing checks adjacent to it or linked from its adjacent rules. 0 when there is no check or the last check passed.

**Edges** (`id` is `source>kind>target`, deduplicated; both ends always exist among `nodes`; `label` is reserved and currently absent):

| kind | from -> to | source |
|---|---|---|
| `traces` | rule -> scenario/invariant; scenario -> endpoint | `rule.links`; the `METHOD /path` that starts each scenario step, normalised (`/1` -> `/{}`) |
| `transitions` | rule -> machine | `rule.links` |
| `applies_to` | rule -> service; rule -> table; machine -> table | `rule.links` (`profile:`, `service:`); rule text naming a table in backticks (`` `users` ``, `` `users.email` ``); the table half of `Table.column` |
| `enforces` | rule -> endpoint | `cell:` links; `` `METHOD /path` `` in rule text (placeholders and a leading `...` are matched to the matrix); rules of a code link whose targets include a `cell:*\|route` |
| `reads` / `writes` | endpoint -> table; code -> table | flow fixtures' stage `reads`/`writes`; plus a route whose last literal segment is a table's SQL name (`GET` reads, others write) |
| `implements` | code -> rule; code -> endpoint | `code/index.json` links; flow stage `rules` and the flow's endpoint; links' `cell:` targets |

**Derived edges** tie a rule to what it governs when no direct link says so. They carry `"inferred": true` and `"via"`: the node ids between the two ends, in order, such that every consecutive pair (ends included) is joined by a direct edge in either direction. Direct edges carry neither field and always win: a derived edge is only added when no edge with the same `source>kind>target` id exists, and each id appears once. Derivation runs over direct edges only (a derived edge is never a step of another), in rule order, so it is deterministic and never names a rule, endpoint or table that is not already a node.

| derived edge | rule | `via` |
|---|---|---|
| rule `enforces` endpoint | rule `traces` scenario, scenario `traces` endpoint (any step of a linked scenario calls the route) | `[scenario]` |
| rule `enforces` endpoint | code `implements` rule, the same code `implements` endpoint | `[code]` |
| rule `applies_to` table | rule `enforces` endpoint (direct, or derived through a scenario) and that endpoint `writes` the table | `[endpoint]` or `[scenario, endpoint]` |
| rule `applies_to` table | code `implements` rule, the same code `reads` or `writes` the table | `[code]` |

Derived edges count in `focus`/`depth` traversal (so `focus=rule:R44&depth=1` reaches `POST /applications/{}/jobs/{}/runs` and `JobRun`) and in `governed_by`; they do not change `trust`, `rules`, `failing` or the diff's `impact` marks, which stay on direct edges. The UI draws them dashed and says "inferred".

**Limits of the derivation.** A scenario's setup steps count like its claim step, so a rule can be tied to an endpoint a scenario only calls to set up state. Code-based edges are only as wide as the code index and the two flow fixtures: shared stages (`requireUser`, `appAccess`) tie auth and access rules to the two traced endpoints only, not to every route they guard. Rule-to-table edges through an endpoint follow its writes only (reads would tie every access rule to every table it looks up); through code they follow reads and writes. Rules with no scenario, code or direct endpoint link stay untied.

**`diff`**: `target -> modified|added|removed|impact` for the open change (the newest change in status `ready`; `change` is its id, `null` and `diff` `{}` when none). Read like the UI overlay reads it: `view_diff.cells` mark the column's endpoint `modified`; `view_diff.scenarios|invariants` `added/removed/modified`; `view_diff.machines.changed` and `targets.modified` `modified`; `targets.added/removed`; `rules.added/removed/changed`. `impact` marks every node adjacent to a changed existing node and each `checks.impact.failing` target, unless it is already marked. Added targets may have no node yet (a new scenario or rule); the UI can draw a ghost. A cell diff marks the endpoint, not the person's row.

**`machines`**: one per `section6.machines` entry, parsed from its moves text (`a -> b, c; b -> d (initial: a)`). States are in first-seen order, `initial` is the `(initial: x)` state, `final` a state with no outgoing move. A move's `rules` are the rules linked to the machine whose text names the move's target state (omitted when none; a heuristic); `rules` on the machine are all rules linking to it. `machines` is always the full list, regardless of `focus`/`kinds`.

**Known limits.** Endpoint to table edges beyond the two flow fixtures come only from the route's resource name, so e.g. `GET /applications/{}/members` has no `memberships` edge. Code nodes cover only the symbols the index and flows mention. Scenario patterns in code links (`scenario:access__*`) are not expanded into edges. Node set and edges are static for a project; only `trust`, `failing`, `change`, `diff` move with checks and changes.

## v1.5 additions (the chat runs on a Claude Code session)

Each user message spawns one `claude -p --output-format stream-json` subprocess in the project directory with the `lattice` MCP server (tools: `model_get`, `rule_get`, `spec_ask`, `code_links`, `graph_get`, `spec_lint`; `change_propose|get|list` in mode `change`) and no edit or shell tools; the first turn's Claude session id is reused with `--resume`, so the owner can continue the same conversation in a terminal. API credentials are stripped from the child's environment. Nothing is spawned until a message is sent. The UI polls; there are no websockets. Errors use the usual `{"detail": ...}`: 404 unknown session, 409 a turn is running (message) or none is running (DELETE), 422 a bad body, 501 mode `rebuild`, 503 the `claude` binary is missing.

### `app.session` on GET /api/model
`model.app.session` is `true` when the `claude` binary is found (so the session routes can work) and `false` otherwise; the UI then falls back to the one-shot `POST /api/ask` chat. The mock always says `true`.

### POST /api/session
Body `{"mode": "ask|explain|change|rebuild", "change": "c1"?}` (`change` is the open change id, mode `change`). `201`:
```json
{"id": "s1", "mode": "ask", "change": null, "state": "idle", "claude_session_id": null,
 "resume_command": null, "cwd": "/path/to/project", "turns": 0, "cost_usd_total": 0}
```
`rebuild` answers `501 {"detail": "mode rebuild is not enabled yet"}` (it needs a worktree). An unknown mode is 422.

### POST /api/session/{id}/message
Body `{"text": "...", "selected": ["rule:R47", "cell:ann|POST /notes"]?}`; `selected` are the targets the owner has picked in the Studio (chips); they reach the model's system prompt. `202 {"turn": 1}`. 422 on empty text, 409 while a turn runs.

### GET /api/session/{id}/events?after=N
```json
{"events": [ ... ], "next": 14, "state": "running", "done": false,
 "claude_session_id": "3f6c1d52-...", "resume_command": "claude --resume 3f6c1d52-...",
 "cost_usd_total": 0.0412}
```
`events` are those with `seq > after` (default 0); pass `next` as `after` on the following poll. `state` is `idle` (no turn yet or the last one ended), `starting`, `running` or `interrupted`; `done` is `state` not `starting`/`running`. `resume_command` is null until the first turn reports its session id (run it in the project directory, `cwd` in the session record).

**Events.** Every event has `seq` (int, from 1, strictly increasing) and `ts` (ISO UTC):

| `type` | fields |
|---|---|
| `user` | `text`, `selected[]` |
| `text` | `text`, `partial` (bool), `message_id` |
| `tool_use` | `id`, `name` (`mcp__lattice__spec_ask`, `Read`, ...), `label` (human: "Read runs.go", "Asked the spec"), `input_summary` |
| `tool_result` | `tool_use_id`, `ok`, `summary` |
| `status` | `state`: `starting|running|idle|interrupted` |
| `result` | `ok`, `text`, `cost_usd`, `duration_ms`, `input_tokens`, `output_tokens`, `num_turns`, `claude_session_id` |
| `error` | `message`, `kind`: `usage_limit|cli|timeout|other` |

**Partial text.** Streamed deltas are merged: one `text` event per content block, `partial: true` while it grows. Each growth re-issues that event under a fresh, higher `seq` (the older one is gone from later polls), so the latest text always arrives as a replacement; key it by `message_id` + position, or simply by `seq` order, and the last `text` event of a message wins. It ends with `partial: false`. A turn that ends with `error` (`usage_limit` when the plan's limit is hit) may or may not also carry a `result` with `ok: false`.

### DELETE /api/session/{id}
Interrupts the running turn (terminates the process group): `200 {"id": "s1", "state": "interrupted"}`; the next poll shows `status interrupted` and `done: true`. 409 when no turn is running. The session itself stays and can take another message.

### GET /api/session
A list of the session records (the shape of the POST answer, plus `created` on the real server).

### Configuration
`studio.toml` `[llm]`: `session_timeout` (seconds a turn may run, default 900; a turn that exceeds it is killed and ends with `error` kind `timeout`), `claude_bin` (the Claude Code binary, default `claude`), `model` (as for the other chat).

## v1.6 additions (many projects behind one server)

One server holds many projects. Each project has its own change sets, chat sessions, checks and model cache (a `Studio`, built the first time one of its API routes is called), so nothing crosses projects: change ids start at `c1` per project.

### Discovery
- `lattice studio DIR` (one directory): that project, exactly as before v1.6 (it must hold `REQUIREMENTS.md`, `spec.py` and `spec.trace.json`; the chat's `--studio-url` stays the bare server URL). `/api/projects` and `/p/<name>/api/...` are served too.
- `lattice studio DIR DIR ...`: each `DIR` that is a project, in order; a `DIR` that is not one contributes its project children (one level down).
- `lattice studio` (no directory): the cwd itself, its children one level down, then `cwd/dogfood/*` (as `lattice projects` lists them), then the registry `$XDG_CONFIG_HOME/lattice/projects.toml` (default `~/.config/lattice/projects.toml`; `LATTICE_PROJECTS_REGISTRY` names another file):
  ```toml
  [[project]]
  path = "/path/to/project"   # relative paths resolve against the registry file
  ```
  A missing or broken registry is ignored.

A project is a directory with `studio.toml`, or `spec.py` and `spec.trace.json` (a broken `studio.toml` still counts; that project's routes answer 500 until it is fixed). Duplicates (by resolved path) are dropped. A project's **name** is `[project] name` in its `studio.toml`, else the directory's name, made unique with `-2`, `-3`, ... The **default project** is the first one found, or `--default NAME`. No project found: the server exits 2 and says how to add a `studio.toml`.

### GET /api/projects
```json
[{"name": "lattice-studio", "root": "/abs/path/dogfood/studio", "git_head": "2373833",
  "spec": true, "app_configured": true,
  "last_check": null | {"verdict": "pass|fail", "at": "iso"},
  "capabilities": {"session": true, "rebuild": false},
  "default": true}]
```
In discovery order. Cheap: it reads each `studio.toml` and runs `git rev-parse --short HEAD` (5 s timeout; `""` outside git); it never builds a project. `spec`: `spec.py` and `spec.trace.json` are present. `app_configured`: `[app] app` and `[app] db` are set. `last_check`: the project's last full check, `null` until one has run in this server (a project not opened yet has none). `capabilities.session`: the `claude` binary is runnable (as `app.session`); `capabilities.rebuild`: the server can rebuild this project (v1.7: a git repository, gate commands that resolve, and a runnable agent; the mock says `true` for both its projects).

### Prefix routing
Every `/api/...` route is also served under `/p/<name>/api/...` (the prefix is stripped, the query string kept, e.g. `/p/lattice-studio/api/changes/c1?wait=30`). An unknown `<name>` answers `404 {"detail": "no such project"}` on any method. The bare `/api/...` routes are the default project's. `GET /api/model`'s `project.name` is the project's name. Static files are unchanged (also served under `/p/<name>/`).

### Sessions and MCP
A project's chat sessions run the `lattice` MCP server with `--studio-url http://127.0.0.1:PORT/p/<name>` (bare in one-directory mode), so its `change_propose` lands in that project's change sets.

### The mock
`python -m lattice.studio --mock` serves two projects: `job-scheduler-studio` (the fixtures, the default, also at the bare `/api/...`, with the pre-seeded change `c0`) and `lattice-studio` (the same fixtures cut to 12 rules, no changes). Their state is separate; the code, graph, flow and rebuild fixtures are the job scheduler's for both.

## v1.7 additions (the real rebuild loop)

The real server runs `POST /api/rebuild` (`lattice/studio/rebuild.py`): a coding agent edits the code in an isolated **git worktree on its own branch**, and the project's **gate commands, run by Studio itself**, decide when it is done. The shapes of v1.3 hold, with the additions below. The owner's checkout is never modified: no checkout, no merge, no index change.

### Configuration: `[rebuild]` in `studio.toml`
```toml
[rebuild]
repo = "."               # the git repository, relative to the project dir (default ".")
gates = ["./check.sh"]   # shell commands run from the repository root in the worktree
                         # (default: the project's check.sh, if it has one)
max_rounds = 3           # rounds before giving up (POST's max_rounds wins; 1..50)
round_timeout = 1800     # seconds one agent step may run
gate_timeout = 900       # seconds one gate command may run
model = "sonnet"         # optional; default: the Studio's model
worktrees = "repo"       # "repo": <repo>/.lattice/rebuilds/ (added to .git/info/exclude,
                         # never to .gitignore) | "temp": the system temp directory
agent = "claude"         # "claude" | "stub" (TEST ONLY: writes round k's files of
                         # stub-rebuild.json in the project dir; never the default)
```

### `capabilities.rebuild` on GET /api/projects
`true` when `[rebuild] repo` is a git work tree with a commit that holds the project, every gate's command resolves (a file in the repository, or a program on `PATH`), and the agent can run (the `claude` binary, or `agent = "stub"`).

### POST /api/rebuild
Request and response as v1.3 (`{"changes": [...], "model"?, "max_rounds"?}` -> `{"id": "b1"}`; ids `b1`, `b2`, ... per project). Refusals, in this order: 422 a body without a non-empty list of distinct change ids, `max_rounds` not 1..50 or `model` not a non-empty string; 404 `no change <id>`; 409 `change <id> is <status>, not accepted` or `change <id> is already rebuilding|built`; 503 `rebuild is not available: <why>` (no git, no gates, no claude and no stub); 409 `a rebuild is already running in this project (<id>)` (one at a time per project). It creates `git worktree add -b rebuild/<first change id>[-2, -3, ...] <dir> HEAD` before answering; the accepted change's spec, requirements and trace are already in HEAD (accept is a commit), so the worktree holds the contract.

### The round loop
1. **Agent.** `claude -p --output-format stream-json --verbose` in the worktree, with the API credentials stripped from its environment, `--setting-sources ""`, `--disable-slash-commands`, `--strict-mcp-config` with the `lattice` MCP server `--readonly` on the worktree's project dir, the `lattice.mcp.guard` PreToolUse hook on the worktree's project dir, `--tools Read,Grep,Glob,Edit,Write,Bash`, `--permission-mode acceptEdits`, `--allowedTools` = Read, Grep, Glob, Edit, Write, `Bash(<gate>:*)` for each gate command and the read-only MCP tools, `--disallowedTools NotebookEdit`, `--append-system-prompt` with the rules (fix the code, never the spec, requirements, trace or gate scripts; run the gates; stop when they pass), its own process group. Round 1's prompt is the brief: each change's request, its rules added / changed / retired (with their text and linked checks), its requirements diff, the targets, and the gate commands. Round n > 1 resumes the same Claude session (`--resume`) with the previous round's findings and the failing gates' output tails.
2. **Integrity.** The project's `REQUIREMENTS.md`, `spec.py`, `spec.trace.json` (plus `[mcp] protected`) and the gate scripts must be byte-identical to the base commit; an edited one fails the round with the finding `spec files were edited (restored from the base commit): <files>` (target `spec`) and is restored inside the worktree.
3. **Gates.** Studio runs each gate (`/bin/sh -c`) in the worktree. Its environment: no API credentials; `PYTHONPATH` = the worktree root, the worktree's project dir and its `[app] sys_path` entries first, then the `lattice` package's directory, then any inherited `PYTHONPATH`; `UV_NO_SYNC=1` and `UV_PROJECT_ENVIRONMENT` = the main checkout's `.venv` (else the server's venv), its `bin` first on `PATH`, so `uv run` reuses that venv without syncing it and imports the rebuilt code from the worktree; `PYTHONDONTWRITEBYTECODE=1`; `LATTICE_REBUILD`, `LATTICE_REBUILD_ROUND`, `LATTICE_REBUILD_WORKTREE`. The verdict is `pass` when every gate exits 0 (and the integrity check is clean); the agent's own claims are never used.

### GET /api/rebuild/{id}
The v1.3 record plus: `project`, `agent` (`claude|stub`), `finished`, `gates` (the commands), `base` (the commit the branch starts from), `branch`, `worktree` (absolute path; `null` once removed), `merge_hint` (`null` until passed), `usage` (`cost_usd`, `duration_ms`, `input_tokens`, `output_tokens`, summed over the rounds' result events), `error` (`null`, or `{kind, message}` for `usage_limit`, `git` or `internal`). `commit` gains `name_status` (`git diff --name-status base..commit`, as `"A path"`). Each round also has `finished`, `gates` (`[{command, exit_code, seconds, output_tail}]`, the tail capped at 20 000 characters), `spec_edited` (paths), `agent` (`cost_usd`, `duration_ms`, tokens, `num_turns`, `session_id`) and `error` (`null` or `{kind: usage_limit|cli|timeout|internal|git, message}`; `internal`/`git` rounds have no `gates`/`check`). `check.counts` sums `PASS|FAIL: N findings (D n, X n)` lines over the gates, else `{<gate command>: n}`; `check.findings` come from the lattice report's finding lines (target = their scenario), from lines starting `FAIL`, `finding` or `✗` (target = a `scenario:`/`cell:`/... token in the line, else `""`), or `<gate> exited <code>: <last line>`. `?wait=S` (0..60 s) holds the answer until the rebuild has finished. `GET /api/rebuild` lists every record of the project.

### End states and branches
- `passed`: one commit on the rebuild branch (`git add -A` in the worktree minus caches; message `build: <requests>`, a sentence naming the changes and the round, and the trailer `Lattice-Rebuild: <id> (<project>)`; the author is the repository's identity), the worktree is kept for review, and `merge_hint` reads ``Studio never merges. To take the rebuilt code, run `git merge rebuild/c1` in <repo>; the worktree <dir> stays for review.`` **Studio never merges into the owner's branch.** `project.git_head` and `app.last_check` describe the owner's checkout and do not move until the owner merges.
- `gave_up`: after `max_rounds` failing rounds, or at once when the agent hits its usage limit (`error.kind: usage_limit`); the branch and worktree stay (uncommitted edits), nothing is committed. A gave-up change can be rebuilt again (on `rebuild/<id>-2`, ...).
- `stopped` (`DELETE`): the running agent's or gate's process group is killed, the worktree removed, the branch deleted when it has no commit of its own (`branch: null`, `worktree: null`), rounds still in progress dropped.
- A crashing agent (non-zero exit, no result) is an error round (`error.kind: cli`); the loop goes on.
- A crash in Studio's own steps (snapshot, integrity restore, gates, commit) ends the rebuild `gave_up` with `error` `{kind: internal|git, message}` at record level. The round that was open gets the same `error`, `finished`, and `files_changed` from the worktree status, and no `gates` or `check` (none ran): a round with `error` and no `gates` means "stopped before the checks ran". `usage` is summed the moment the agent step ends, so it is complete in every end state (`passed`, `gave_up`, `stopped`, error), and the round's `agent` carries the result event's usage even when the round crashed afterwards.

### `build` on `model.changes[]`
v1.3's shape plus `branch` (while `rebuilding`, and when `built` or `gave_up`) and `commit` (when `built`): `{"state": "built", "rebuild_id": "b1", "branch": "rebuild/c1", "commit": "<sha>"}`. Build state is in memory: after a restart every accepted change is `not_built` again (the branches stay in git).

### GET /api/model: `project.git_head`
Read live on every call (it was cached with the spec files, so a commit that left them alone, like a merge of a rebuild branch, did not show).

### The mock
The mock's rebuild record has `branch`, `base`, `worktree` and `merge_hint`, its `build` has `branch` and `commit`, and a passing mock rebuild no longer moves `project.git_head`. It still previews the rebuilt code in the Code lens and turns `app.last_check` to pass (the real server does neither until the owner merges).

## v1.8 additions (build from spec, area by area, onto a target branch)

A project whose spec is committed but whose app is not (yet) built can be built from Studio without an accepted change: by **area** (a top-level section of the requirements), by a list of rules, or `all`. Each scoped build starts with a **baseline** gate run, works on the scope's failing checks only, is judged by Studio from the gate findings (never by the agent), and lands on a **target branch** (default `build/<project>`) that accumulates the areas. Code: `lattice/studio/areas.py` (areas and the findings-to-scope mapping, pure) and `lattice/studio/rebuild.py`. Everything in v1.7 holds for a rebuild without `scope`.

### Configuration: `[rebuild]` additions
```toml
[rebuild]
areas = "sections"            # "sections" (default) | "none" (only the area "all")
metrics_hook = "python3 metrics/collect.py"   # optional; relative to the project dir
findings_json = "metrics/last-check.json"     # optional; a `lattice.spec check --json`
                                              # report a gate writes, read for findings
integration_branch = "build/streak"           # optional; the default target branch
                                              # (default build/<project name, slugged>)
protected_branches = ["main", "master"]       # the default
```

### `capabilities.build_from_spec` on GET /api/projects
`true` exactly when `capabilities.rebuild` is (the same requirements). The mock says `true`.

### GET /api/areas[?branch=B]
`[{id, title, rules: [ids], scenarios: int, checks: int, targets: [target], failing: int|null, status, target_branch}]`, in document order, the virtual area `{"id": "all", "title": "All", ...}` last.
- **Areas.** A rule's trace `section` is its heading path joined by `" > "` (`Streak: requirements > Habits`). When every sectioned rule shares one first heading (the document title) and some go deeper, the area is the second heading and the rules directly under the title form a preamble area named after the title; otherwise the area is the first heading. Rules without a section form one area `Requirements`. `id` is the title slugged (lowercase words joined by `-`, at most 48 characters, `-2`, `-3` on a clash; `all` is reserved). `areas = "none"`: only `all`.
- **Counts.** `targets`: the trace links of the area's rules, in order, without repeats (a target linked from rules of two areas is in both); `scenarios`: the `scenario:` targets among them; `checks`: all of them.
- **failing / status** are per target branch B (default: the default target). `failing` = the area's targets that fail in the latest gate results Studio recorded for B while B's tip is still the commit they describe (a scoped build's baseline, or its passing round); for the branch checked out in the owner's worktree, falling back to `app.last_check.targets`; else `null` (unknown, e.g. after a restart, or after B moved). `status`: `building` while a scoped build of this area onto B runs; else `not_built` (failing null, or every target failing), `built` (failing 0; also an area with no targets), `partial` otherwise.

### GET /api/branches
`{branches: [{name, head, current, protected, checked_out: <worktree path>|null, ahead_of_base: int (commits in the branch not in HEAD), is_integration}], current_branch, protected: [names], default_target}`. Every local branch (including the per-build `rebuild/*` branches). `current`: checked out in the owner's main worktree (the repository of `[rebuild] repo`). `is_integration`: the default target, or the target of a build in this server.

### POST /api/rebuild with a scope
Body: `{"scope": {"area": "<id>"} | {"rules": ["R1", ...]} | "all", "changes"?: [ids], "branch"?: {"name", "create"?: bool, "from"?: "<ref>"}, "confirm_unsafe_branch"?: bool, "max_rounds"?, "model"?}` -> `{"id": "b3"}`. `changes` may be given too (each must be accepted, as in v1.7; the brief names them and their builds are tracked); without `scope`, `changes` is required (v1.7). `{"area": "all"}` equals `"all"`.

Refusals, in this order: 422 a malformed body (`scope must be "all", {"area": "<id>"} or {"rules": ["<id>", ...]}`; `branch needs a scope`; `confirm_unsafe_branch` not a boolean; the v1.7 checks); 404 `no area <id>` / `no rule <id>[, <id>]` / v1.7's `no change <id>`; 409 v1.7's change states; 503 not available; 409 `a rebuild is already running in this project (<id>)`; then the target branch (below); then, **after the baseline**, 409 `nothing to build: the checks in this scope already pass` (no in-scope target failing; the build leaves no record, no id and no branch). A scoped POST therefore answers only once the baseline gate run is done (for a slow gate, as long as it takes); the record exists meanwhile with status `baseline` (listed by `GET /api/rebuild`, stoppable by `DELETE`).

Error bodies may carry a machine-readable `code` next to `detail` (`{"detail": "...", "code": "branch_protected"}`).

### The target branch
- Omitted: the default target (`[rebuild] integration_branch`, else `build/<project>`), created from the owner's HEAD on first use (`created_branch: true`, `base_ref: "HEAD"`).
- `{"name": B}` (create false): B must exist (404 `no branch B`, code `no_branch`).
- `{"name": B, "create": true, "from": R}`: B must not exist (409 `branch B already exists`, code `branch_exists`; it is created with `git update-ref refs/heads/B <R> ""`, which also refuses a branch that appeared meanwhile, so nothing is ever overwritten); R defaults to `HEAD`, an unknown R is 404 (code `no_ref`); `from` without `create: true` is 422.
- B must pass `git check-ref-format --branch` (422, code `branch_name_invalid`).
- **Safety.** B checked out in a linked worktree (not the owner's main one) is always 409 (code `branch_checked_out`). B protected (`protected_branches`, code `branch_protected`) or checked out in the owner's main worktree (code `branch_checked_out`) is 409 unless the body carries `confirm_unsafe_branch: true` **and** the main worktree is clean (`git status --porcelain` empty, untracked files included; else 409 code `tree_dirty`).
- **Writes.** All work happens in a separate worktree on the per-build branch `rebuild/<scope-slug>-N` (N = 1, 2, ...; slug = the area id, `rules-r1-r2`, or `all`), made from B's tip. On pass, B is fast-forwarded to the build commit: with `git update-ref refs/heads/B <commit> <tip at start>` when no worktree has B checked out, or with `git merge --ff-only <commit>` in the owner's main worktree when the build was confirmed onto the checked-out branch (re-checked clean first). Never a reset, a forced checkout or a non-fast-forward. If B moved during the build (or got checked out, or the confirmed worktree got dirty), the build ends `gave_up` with `error.kind` `branch_moved` (`branch_checked_out`, `tree_dirty`); nothing is lost: the per-build branch keeps the commit and the record has `commit`.
- **The owner's new commits.** For the default target only: if the owner's HEAD is not in B, the worktree first runs `git merge --no-edit <owner HEAD sha>` (the merge commit is part of the build; `merged_head` records it); a conflict aborts the merge and ends the build `gave_up` with `error.kind: merge_conflict` before the baseline. A named target is built as it is (its own spec).

### Baseline, targets and the verdict
1. **Baseline.** The gates run once in the worktree before round 1 (`LATTICE_REBUILD_ROUND=0`). Findings come from the gates' text (v1.7), or, with `findings_json`, from that JSON report when a gate (re)wrote it during the run (else the text, plus a warning). A gate exiting with a code other than 0 or 1 makes the baseline unusable: `gave_up`, `error.kind: baseline`.
2. **Identity and scope.** A finding's identity is its target (`scenario:<name>`, `invariant:<name>`, a rule id, ...), else `key:<key>` (JSON), else `title:<title>`: findings with one identity are one failing check. A finding is in scope when its target is linked from (or is) a rule of the scope; findings without a target, or whose target no rule links to, are **unmapped** and out of scope (for the scope `all`, everything is in scope). The scope's **targets** = the in-scope identities of the baseline.
3. **Brief.** The scope's rules (id, text, linked checks) grouped by section, its scenarios, the failing in-scope checks (the first 40 with their messages), how many fail outside the scope, `CONTEXT.md`/`README.md` paths when present, the gates, and the rules (fix the code, never the spec, requirements, trace, gate scripts or test harness; do not weaken them; keep behaviour outside the scope; add tests where the stack has a convention). Round n > 1: what still fails in scope and the regressions.
4. **Verdict per round** (Studio's, from the findings): `pass` iff every gate exited 0 or 1, every target passes (its identity is no longer among the findings), there is **no regression** (an identity failing now that did not fail in the baseline), and the integrity check is clean. Out-of-scope baseline failures may stay. Each round adds `scope_passing`, `scope_total` (= number of targets), `regressions` (identities), `newly_fixed` (baseline identities, any scope, that no longer fail), `wall_s`; `check.findings[]` add `in_scope` and `regression` (a passing round keeps its out-of-scope findings).

### The record of a scoped build
v1.7's record plus: `scope` `{kind: area|rules|all, id, title, rules}`, `baseline` `{at, seconds, verdict, counts, findings: [{id, title, target, rules, in_scope}], unmapped: [finding ids], gates}`, `targets` (the baseline's in-scope identities, `done` on pass), `target_branch`, `created_branch`, `base_ref` (the ref B was created from, or B), `commit_range` `{from: B's tip at start, to: the commit|null}`, `integration_branch` and `integration_base` (= `target_branch` and `commit_range.from`), `owner_head`, `merged_head` (null unless the owner's HEAD was merged in), `metrics` (null, or the hook run below), `warnings` (strings). `base` is the commit the rounds start from (B's tip, or the merge commit). `status` adds `baseline` (before round 1). The commit message is `build: <scope title>[; <requests>]`, a sentence with the scope, `k/k in-scope checks pass in round n of m, no regressions`, and the `Lattice-Rebuild:` trailer. `merge_hint` names B (and says the owner's checkout was fast-forwarded when it was).

**Usage (every record):** `usage` gains `cost_usd_list_estimate` (the same figure as `cost_usd`, which is kept for v1.7 clients: the CLI's list-price estimate, **not a charge**), `cache_read_input_tokens`, `cache_creation_input_tokens` (from the result events' usage) and `wall_s`; each round's `agent` carries the same token and estimate fields, and `wall_s`. The chat's `result` event (v1.5) gains the two cache fields too.

### The metrics hook
With `metrics_hook`, after a scoped build ends `passed` (before its commit, so the hook's output, e.g. a new `metrics/phases.jsonl` line, is part of it) or `gave_up` (left in the worktree), Studio runs `<hook> --phase build_<scope-slug> --llm-json <tmp file> --note <text> [--check-json <findings_json path>]` with `cwd` = the worktree's project dir and the gate environment. The JSON: `{model, tokens_in (input + cache read + cache creation), tokens_out, input_tokens_uncached, cache_read_input_tokens, cache_creation_input_tokens, cost_usd_list_estimate, wall_s, rounds, llm_calls, target_branch}`; the note names the build, its end state and round, the scope score, the agent and the target branch. A failing hook is a warning (`warnings`), never a failed build; `metrics` records `{command, phase, exit_code, seconds, output_tail, llm}`. **`findings_json` shape relied on** (Lattice's `check --json`, e.g. Streak's `metrics/last-check.json`, whose top-level keys are `verdict`, `layers`, `counts`, `findings`, `cases`, `notes`, ...): `counts` (`{layer: n}`) and `findings[]` with `layer`, `verdict`, `rule` (`"scenario: <name>"`, `"invariant: <name>"`, or a rule id), `case`, `details.problems` (strings) and `key`; the other keys are not read.

### The mock
`GET /api/areas[?branch=]` derives the areas from the fixture model's rule sections and links (the job-scheduler spec: 8 areas plus `all`); `GET /api/branches` shows a simulated `main` (checked out, protected) plus every target branch a scoped build created; a scoped `POST /api/rebuild` validates like the real server (codes included), synthesizes a baseline (with no results yet, every check fails), plays two rounds over polls (round 1 fails in scope, round 2 passes) and on pass moves the target branch and marks the scope's targets passing there; building a built area again answers 409 `nothing to build`.

## v1.9 additions (build plans: the build from spec as an agentic execution graph)

v1.8 builds one scope at a time. A **build plan** builds a whole scope as an execution graph of **nodes** (each one a v1.8 scoped build: own worktree, own branch, own process group, judged by Studio), wired by real dependencies and run **level by level**; independent nodes of a level run concurrently. Parallel versus sequential is decided by the graph's construction (dependencies plus declared write sets), never by a flag: the only knob is `max_parallel`, a machine capacity cap. The plan is shown, editable and **approved** before it runs. Code: `lattice/studio/plan.py` (+ `prompts/epg_plan.md`, the planner's method prompt, distilled from the `epg` skill), on `lattice/studio/rebuild.py`. Everything in v1.8 holds; a stand-alone scoped build still runs one at a time.

### Configuration: `[rebuild]` additions
```toml
[rebuild]
planner = "agent"           # "agent" (claude -p, default) | "stub" (TEST ONLY: stub-plan.json)
planner_model = "opus"      # the planner's model (default opus)
planner_timeout = 900       # seconds one planner call may run
models = {fast = "haiku", mid = "sonnet", frontier = "opus"}   # tier -> node model (defaults)
max_parallel = 2            # default max_parallel of a new plan (1..8)
```

### `capabilities.build_plan` on GET /api/projects
`true` exactly when `capabilities.rebuild` is. The mock says `true`.

### POST /api/plan
Body: `{"scope": "all" | {"areas": ["<id>", ...]} | {"rules": ["<id>", ...]}, "branch"?: {"name", "create"?, "from"?}, "confirm_unsafe_branch"?: bool, "max_parallel"?: 1..8 (default 2), "planner"?: "agent" | "fallback" (default agent), "models"?: {"fast", "mid", "frontier"}}` -> `{"id": "p1", "status": "planning"}` (ids `p1`, `p2`, ... per project). The target branch is validated now with v1.8's rules and codes (`no_branch`, `branch_exists`, `no_ref`, `branch_name_invalid`, `branch_checked_out`, `branch_protected`, `tree_dirty`); nothing is created until the plan runs. Refusals: 422 malformed (`scope must be "all", {"areas": ["<id>", ...]} or {"rules": ["<id>", ...]}`, `scope is required`, `max_parallel must be 1 to 8`, `planner must be "agent" or "fallback"`, `models must map fast, mid and frontier to model names`, `unknown field(s): ...`); 404 `no area <id>` / `no rule <id>`; the branch codes; 503 not available. Planning runs in the background.

### GET /api/plan/{id}[?wait=S], GET /api/plans
`?wait=S` (0..60) holds the answer until the plan is neither `planning` nor `running`, or S seconds. Unknown id: 404 `no plan <id>`. `GET /api/plans`: every plan, short (`id, status, source, version, scope, target_branch, max_parallel, levels, created, finished, totals, nodes: [{id, status}]`).

The record:
```json
{"id": "p1", "project": "notes",
 "status": "planning|ready|approved|running|passed|partial|failed|stopped",
 "source": "agent|fallback", "fallback_reason": null, "version": 1,
 "scope": {"kind": "all|areas|rules", "areas": ["writing-notes"], "rules": ["R1"], "title": "All"},
 "target_branch": "build/notes", "branch": null, "created_branch": false, "max_parallel": 2,
 "models": {"fast": "haiku", "mid": "sonnet", "frontier": "opus"},
 "planner": {"kind": "agent|stub|fallback", "model": "opus", "attempts": 1, "usage": {...}, "wall_s": 12.3, "error": null},
 "nodes": [{"id": "scaffold", "title": "...", "kind": "scaffold|area|merge|integration",
            "scope": {"kind": "area|rules|all", "area": "<id, for kind area>", "rules": ["R1"]},
            "depends_on": ["..."], "why": "...", "tier": "fast|mid|frontier", "model": "sonnet",
            "touches": {"tables": [], "packages": [], "files": []},
            "gate": {"kind": "scoped|full", "criterion": "...", "rules": [...] | "all", "checks": [...] | "all", "regressions": "none", "decided_by": "studio"},
            "level": 0, "serialised_because": {"node": "a", "overlap": {"tables": [], "packages": [], "files": ["x"]}},
            "status": "pending|running|passed|gave_up|blocked|merge_conflict|regressed|stopped",
            "build_id": "b3", "builds": ["b3"], "branch": "rebuild/p1-scaffold", "commit": "<sha>|null",
            "merged": true, "nothing_to_build": false, "blocked_by": [],
            "usage": {"tokens_in", "tokens_out", "cache_read_input_tokens", "cache_creation_input_tokens", "cost_usd_list_estimate"},
            "wall_s": 0.0, "rounds": 0, "lines_changed": 0, "files_changed": [],
            "started_ts": 0.0, "finished_ts": 0.0, "error": null, "metrics": null, "live": {"rounds", "status", ...}}],
 "edges": [{"from": "scaffold", "to": "writing", "why": "...", "kind": "declared|scaffold|integration|serialised", "auto": false}],
 "levels": [["scaffold"], ["writing", "reading"], ["integration"]],
 "validation": {"ok": true, "issues": [], "auto_edits": [{"kind": "serialise|level|edge|node|tier|touches|scope|gate", "detail": "...", ...}]},
 "level_gate": "...", "level_runs": [{"level": 1, "run": 1, "base": "<sha>", "nodes": [...], "status": "running|passed|failed", "merged": [...], "merge_conflicts": [...], "tip": "<sha>|null", "gate": {"sha", "verdict", "counts", "failing", "regressions", "newly_fixed", "gates"}, "regressions": [...], "blame": [{"target", "nodes", "by": "rules|only_node|level"}], "error"?: {...}, "started", "finished"}],
 "baseline": {"at", "sha", "seconds", "verdict", "counts", "failing", "findings"}, "runs": 1,
 "created", "approved", "started", "finished", "target_tip": "<sha>", "owner_head": "<sha>",
 "error": null, "warnings": [], "metrics": null,
 "totals": {"tokens_in", "tokens_out", "cache_read_input_tokens", "cache_creation_input_tokens", "cost_usd_list_estimate", "wall_s", "rounds", "lines_changed", "shape": {"nodes", "levels", "max_level_width"}}}
```
`cost_usd_list_estimate` is the CLI's list-price estimate, not a charge. `live` (while a node runs) carries its build's rounds and usage so far. Node build records are ordinary rebuild records (`GET /api/rebuild/{build_id}`) with `plan: {id, node}`.

### The planner
One `claude -p` call per attempt: `--tools Read,Grep,Glob`, `--allowedTools Read,Grep,Glob`, `--disallowedTools Bash,Edit,Write,MultiEdit,NotebookEdit,WebFetch,WebSearch`, `--permission-mode dontAsk`, `--setting-sources ""`, `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`, `--no-session-persistence`, `--disable-slash-commands`, `--append-system-prompt <prompts/epg_plan.md>`, `--model <planner_model>`; `cwd` = the repository root; the environment without API credentials (`ANTHROPIC_*` and provider switches removed); its own process group (killed on DELETE or timeout). Its message: the scope's areas (id, title, rule ids and texts clipped to 220 characters, scenario/check/failing counts, endpoints and data-model tables their rules mention, `huge` when > 25 rules or > 30 scenarios), the data model (tables and column names), the repository file list (`git ls-files`, first 600). It answers one JSON object `{"nodes": [{id, title, kind, scope, depends_on: [{id, why}], tier, touches, why}]}`. Any `gate` it gives is ignored (an `auto_edits` entry): Studio sets every gate, so a plan can never contain a gate the agent could game.

### Validation (Studio, deterministic, on every plan and every edit)
- **Schema**: `nodes` non-empty; ids `[a-z0-9][a-z0-9_-]*`, unique; `kind` one of the four; `scope` `{"area"}` (an area of the plan scope; covers its rules in the scope), `{"rules"}` (rules of the plan scope) or `"all"` (the integration node); an `area` node covers at least one rule; `depends_on` names nodes (not itself, not the integration node); the scaffold depends on nothing.
- **Acyclic.** **Coverage**: every rule of the plan scope is covered by exactly one non-integration node (`R5 is covered by no node`, `R5 is covered by 2 nodes (a, b)`); the scaffold covers no rules or only infrastructure ones (rules of areas whose title names errors, versioning, the data model, time/timezones, lists or pagination); at most one scaffold and one integration node.
- **Sanitised**: `touches` keeps strings only, paths repo-relative without `..` or a leading `/`, at most 64 per list; unknown tier -> `mid`. Each correction is an `auto_edits` entry.
- **Structure**: every node without dependencies depends on the scaffold (when there is one); every sink depends on the integration node, which is added when missing.
- **Levels**: a node's level is one more than its deepest dependency. **No two nodes on one level may have overlapping write sets** (a table, a package or file path equal to or inside the other's): Studio adds an edge from the earlier node (document order) to the later one, records `serialised_because` on it and a `serialise` auto-edit, and re-levels; a level holds at most `max_parallel` nodes (the later ones move down a level, `level` auto-edits), to a fixpoint.
- **Models**: `tier` -> `models` (the request's, else `[rebuild] models`); an owner edit can pin a node's `model`.
- An invalid **agent** plan (schema, cycle, coverage) is sent back once with the issues; if the second answer is invalid too, or a call fails (timeout, no JSON, CLI error), the plan is the fallback. An invalid **edit** is refused (422, code `plan_invalid`) and changes nothing.

### The fallback plan (deterministic, no LLM)
A `scaffold` node covering the infrastructure areas' rules (when the scope has any), then the first area whose title names accounts, auth, tokens or sessions, then every other area in document order, each depending on the one before (sequential: safe), then `integration`. Area nodes are named after the area id; an area only partly in a rules scope covers just those rules. Tiers: `frontier` for areas naming accounts, auth, tokens, sessions, sync, concurrency, locks, nudges or scheduling, and for integration; else `mid`. `source: "fallback"` and `fallback_reason` say why (`requested: the request asked for the fallback planner`, `the planner failed: ...`, `the planner's plan was invalid twice: <issues>`).

### POST /api/plan/{id}/revise
`{"feedback": "..."}` re-runs the planner (agent, or the stub) with the current plan and the feedback (status `planning`, then `ready`), or `{"edits": {"nodes": {"<id>": {"depends_on": [ids], "tier": "...", "model": "..."|null}}, "remove_edges": [[from, to]], "add_edges": [[from, to]], "max_parallel": n, "branch": {...}, "confirm_unsafe_branch": bool}}` -> the new record (re-validated; `version` + 1; status `ready`: an approval is cleared). One of the two, not both (422). Only a `ready` or `approved` plan (else 409). An edge the overlap rule needs comes back as a `serialised` edge.

### POST /api/plan/{id}/approve, POST /api/plan/{id}/run, DELETE /api/plan/{id}
- `approve`: a `ready` plan -> `{"id", "status": "approved", "version"}`; else 409 `plan p1 is <status>, not ready`.
- `run`: 409 `plan p1 is ready, not approved: approve it first` (any status but `approved`, or `partial`/`failed`/`stopped` to resume); 409 `a build plan is running in this project (<id>)` or `a rebuild is already running in this project (<id>)`; 503 not available -> `{"id", "status": "running"}`. While a plan runs, `POST /api/rebuild` answers 409 `a build plan is running in this project (<id>)`.
- `DELETE`: a `planning` or `running` plan -> `{"id", "status": "stopped"}` (the planner or the running node builds are stopped, their process groups killed; passed nodes' branches stay); else 409 `cannot stop a plan that is <status>`.

### The runner
1. The target branch is resolved as in v1.8 (created now when asked or when the default target does not exist). The **start baseline** is the full gate (every `[rebuild] gates` command) on its tip, in a detached integration worktree (for the default target, the owner's HEAD is merged in first when the branch lacks it, as in v1.8). Gates that cannot run end the plan `failed` (`error.kind: baseline`).
2. **Per level**, in order: a node whose dependencies are not all `passed` is `blocked` (`blocked_by`); the others start from the target tip **at the start of the level**, at most `max_parallel` at once, as builds `rebuild/<plan>-<node>[-n]` (`Lattice-Plan: <plan>/<node>` trailer), model = the node's. A node's targets are its in-scope failing checks of the level-start full gate; with none it is `passed` with `nothing_to_build` (no agent run) -- except the scaffold, which always runs (judged on regressions alone). Each node is judged by Studio (v1.8's verdict against the level-start gate). The stub agent plays `scopes[<node id>]`.
3. **Level end**: the passed nodes' commits are merged into the level's start tip **in plan node order** in a temporary integration worktree, one `git merge --no-ff` commit per node (`plan p1 level 1: merge node writing (rebuild/p1-writing)`), so each node stays a revertible unit; a conflicting merge is aborted and the node is `merge_conflict` (its dependents are blocked; the others continue). The nodes' metrics hooks run there (below). Then the **full gate** on the merged tip: a check failing there that did not fail on the last good tip (which never fails more than the start baseline) is a **regression**: the level fails (`level_runs[].status: failed`, `regressions`, `blame` by the rules each failing check is linked to, else the only merged node, else the level), its merged nodes become `regressed`, and the plan stops. Otherwise the target branch is fast-forwarded to the merged tip (v1.8's `update-ref` with the old tip, or `merge --ff-only` in the owner's clean worktree when confirmed; a moved branch fails the level): **the target only ever moves to a tip whose full gate showed no regression.** Passed nodes' worktrees are removed (branches kept); a gave-up node keeps its worktree for review.
4. **End**: `passed` (every node passed), `partial` (some passed: e.g. a node gave up and blocked its dependents), `failed` (none passed, or a level failed before anything landed), `stopped`. `POST .../run` on a `partial`, `failed` or `stopped` plan resumes: every node not `passed` runs again from the current target tip (a new start baseline first).

**Locking.** The Rebuilder's one lock guards builds and plans. A running plan holds the project's build slot (`plan_active`), taken under that lock only when no build runs; `POST /api/rebuild` refuses while it is held, and a plan run refuses while any build runs. Node builds of the holding plan start without the one-at-a-time check (each has its own worktree, branch, process group and record); the plan's own steps (merges, full gates) run in its integration worktrees.

### Metrics
Per node: its builds' usage summed (`usage`), `wall_s`, `rounds`, `lines_changed` and `files_changed` (`git diff --numstat` of its commit). `totals`: the nodes' and the planner's tokens and estimate, the run wall time, rounds, lines and the plan's shape. With `metrics_hook`: per node `--phase build_<plan>_<node>` (v1.8's JSON and note), run at the level end in the integration worktree, so sibling nodes never both append to the hook's files on their branches (its output is committed there as `metrics: plan <id> level <n>`, before the full gate); and once per plan `--phase build_plan_<id>` with the totals, the shape and per-node lines in the JSON, in a temporary worktree at the final target tip; files it writes are committed on `rebuild/<plan>-metrics` (`metrics.branch`), never on the target, because that commit has not been gated.

### TEST ONLY: the stub planner
`[rebuild] planner = "stub"` reads `stub-plan.json` in the project dir: a planner document, or `{"scopes": {"<plan scope slug>": <doc> | {"doc": <doc>, "retry": <doc>} | {"error": "..."}}}` (slugs: `all`, `areas-<ids joined by ->`, `rules-r1-r3`; `default` as a fallback key); the re-ask after an invalid plan reads `retry` (else `doc` again). Never the default.

### The mock
`POST /api/plan` validates like the real server and plans over one poll: the planner `agent` gives a mock agent plan (a scaffold over the infrastructure areas, the accounts area, the other areas as siblings with one package each, integration), `fallback` the real fallback; both go through the real validator. `approve`, `revise` (edits only; `feedback` re-plans the same way) and `DELETE` behave as on the real server. A run advances one step per `GET /api/plan/{id}` poll: a level's nodes start, then pass (with synthetic usage), then the level merges and its full gate passes; the plan ends `passed`.


## v1.10 additions (durable state and resume)

Before v1.10 plans, build records and change sets lived only in memory: a restart or a crash lost them, and a plan stopped by a level that passed its full gate but could not land re-ran that level's agents. v1.10 keeps them on disk and resumes from where they stopped. Code: `lattice/studio/state.py` (the store), on `rebuild.py`, `plan.py` and `server.py`. Everything in v1.9 holds; with the store off (`[state] enabled = false`, or a Studio built without one) Studio behaves as v1.9.

### Configuration: `[state]` in `studio.toml`
```toml
[state]
dir = ".lattice/state/notes"   # optional, relative to the project dir
enabled = true                 # false: in memory only, as before v1.10
[rebuild]
max_attempts = 3               # starts of one build or plan node (continue, retry) before 409
```
Default directory: `<repo root>/.lattice/state/<project name>/`, the repo root being the git top level of `[rebuild] repo` (default the project dir), else the project dir. Inside a repository it is git-ignored through `.git/info/exclude` (`/.lattice/state/`) before the first file is written; never `.gitignore`, never the tracked tree. Two projects never share a directory.

### Layout and guarantees
```
<state dir>/meta.json         {"schema": 1, "written_by": "lattice-studio 0.1.0", "counters": {"plans": 3, "builds": 7, "changes": 2}, "results": {...}}
<state dir>/plans/p1.json     {"schema": 1, "written_by": ..., "kind": "plans", "id": "p1", "saved_at": ..., "record": <the API record>, "private": {...}}
<state dir>/builds/b1.json    (same envelope; "private": what a build needs to go on: worktree, scope, baseline ids, session id, ...)
<state dir>/changes/c1.json   (same envelope; "private": the proposal and its base, so a ready change can be accepted after a restart)
```
- **Atomic writes**: temporary file in the same directory, `fsync`, `os.replace`, `fsync` of the directory. A crash leaves the old file or the new one, never a torn one. Written through on every status transition and after every round, level and node event.
- **Tolerant reads**: unknown fields are kept; missing ones defaulted. A file that does not parse, or whose record id is not its name, is moved aside to `<name>.bad-<ts>`; a warning goes to `GET /api/projects` `state_warnings` (`[{at, file, message, moved_to}]`) and startup goes on.
- **Ids never repeat** after a restart: counters persisted, and raised to the highest id among the files (`.bad-*` included). A refused build still leaves no record and no id (v1.8).
- **Loaded lazily** when the project is first used (its Studio is built then; a one-project server builds it at startup). Every field an API answer shows survives a restart unchanged, except live process handles.

### Reconciliation on load
A record found live is reconciled once: a plan `planning`/`running` -> `interrupted` (its running nodes -> `interrupted`, a running level run -> `interrupted`, the lost run time added to `totals.wall_s`); a build `queued`/`baseline`/`running` -> `interrupted` (an unfinished round dropped, its usage kept in the total); a change `drafting`/`checking` -> `failed` with `error_kind: "interrupted"` (its `request` kept). Each gets `interrupted: true`, `interrupted_at`, `interrupted_reason: "studio_restarted"` and a `timeline` entry. Records keep the process groups they spawn (`pids: [{pid, pgid, started, command, match, role, at}]`, agents, gates, the planner); an orphan group is terminated (SIGTERM, then SIGKILL) only when the pid exists AND its start time AND its command line match (or still contain `match`, for a `/bin/sh -c` that exec'd); never by pid alone. Killed ones are listed in `orphans_killed`. Worktrees referenced by interrupted records are kept for resume; ones under `.lattice/rebuilds/` no record refers to are reported in `state_warnings` and never removed.

A change interrupted while drafting is re-drafted by `POST /api/changes {"parent": "<its id>", "request": ""}`: on the interrupted change's own base, with its request when `request` is empty; when ready, the interrupted change's `superseded_by` names it.

### New and extended fields
- **Plan**: `status` may be `interrupted`; `interrupted`, `interrupted_at`, `interrupted_reason`; `attempts` (= `runs`); `timeline: [{at, event}]`; `anchors: {"<node>": sha, "L<n>": sha}`; `pids`; `integration_worktree`; `orphans_killed`; `resume` (below). Each node: `status` may be `interrupted`; `attempts` (starts of its agent build); `timeline`; `previous_findings` (what its last attempt ended with, put in the retry's brief). Each level run: `gated` (its full gate passed), `landed`, `landed_at`, `landed_by: "run" | "resume"`, `land_error` (why it did not land at first), `remerge: true` (a re-merge onto a moved target). `GET /api/plans` items add `interrupted`, `attempts` and `resume`.
- **Build record**: `status` may be `interrupted`; `pids`, `attempts`, `timeline`, `interrupted*`, `session_id` (the agent's Claude session), `orphans_killed`, `abandoned` (an interrupted node build replaced by a fresh one; its usage still counts for the node).
- **Change record**: `error_kind` (null, or `interrupted`), `interrupted_at`, `interrupted_reason`.
- `model.changes[].build.state` may be `interrupted` (a rebuild of the change was interrupted; it can be continued, or the change rebuilt).

`resume` on a plan (computed when read, from the record and the repository as it is now):
```json
{"available": true, "kind": "continue|land|retry", "from_level": 1,
 "will_skip": ["scaffold", "auth"], "will_run": ["account", "...", "integration"],
 "note": "Level 1 passed its check but could not be added to build/streak because that branch is checked out in your working copy. Switch it away and press Land."}
```
`kind`: `land` when a level passed its full gate but never landed; `retry` when a node gave up, did not merge or regressed; else `continue`. `available: false` (with the reason in `note`) for a plan that has not run, runs, passed, was never approved, or whose failed nodes reached `max_attempts`.

### Durable git anchors
Each node commit is pinned as `refs/lattice/plans/<plan>/<node>` and each gated level tip as `refs/lattice/plans/<plan>/L<n>`, so a plan's work outlives its worktrees and `git gc`. They are removed only by `DELETE /api/plan/{id}?forget=true`.

### POST /api/plan/{id}/run (resume)
Body (optional): `{"confirm_unsafe_branch": bool}`. Runs an `approved` plan, or resumes a `partial`, `failed`, `stopped` or `interrupted` one that was approved before (else 409 `plan p1 is <status> and was never approved: ...`; `resume.available: false` -> 409 with `resume.note`, code `nothing_to_resume`). The target branch is checked at once (v1.8 codes, e.g. 409 `branch_checked_out`). Then, in order:
1. levels that passed their full gate and landed are skipped, their nodes' results kept;
2. a level that passed its full gate but did not land (`branch_checked_out`, `branch_moved`, `tree_dirty`) lands first: a fast-forward of the target to its pinned gated tip when the tip still descends from the target's tip (the owner's checked-out branch only with `confirm_unsafe_branch` and a clean worktree, by `merge --ff-only`), `landed_by: "resume"`, no node runs again; when the target moved past it, its node commits are merged onto the new tip (a new level run, `remerge: true`) and only the full gate runs: no regression -> the target moves; regressions -> the plan stops with the findings;
3. an `interrupted` node continues its own build when its worktree and branch still exist and it was made from this level's tip: from the round after its last completed one, with `--resume <session id>` when the record has one, else a fresh session whose brief carries the last round's findings; otherwise it starts afresh from the level's tip (the old build `abandoned`, its usage kept);
4. a node that gave up, did not merge or regressed is retried with a brief that adds "A previous attempt ended with these findings: ..."; each start counts in its `attempts`; at `[rebuild] max_attempts` (default 3) `run` answers 409 (`Node <id> already had <n> attempts (the cap is [rebuild] max_attempts = <n>); ...`);
5. metrics accumulate across attempts (usage, wall time, rounds, lines per node and in the totals); a resumed node's metrics hook note starts `resumed (attempt <n>); `. Merged history is never rewritten; the integration node runs last.

### POST /api/plan/{id}/land
Body (optional): `{"confirm_unsafe_branch": bool}`. Only step 2 above, never an agent: a fast-forward answers at once `{"id", "status": "partial|passed|failed", "landed": true, "remerge": false, "target_tip"}`; a re-merge runs in the background (`{"id", "status": "running", "landed": false, "remerge": true, "target_tip"}`; the plan then ends `partial`/`passed`, or with the regression). 409: `nothing is waiting to land in plan <id>` (code `nothing_to_land`), the branch still checked out (`branch_checked_out`, with the worktree in the message), a dirty worktree (`tree_dirty`), a running plan or build.

### DELETE /api/plan/{id}?forget=true
Stops the plan if it is planning or running, then drops its record, its state file and its anchors -> `{"id", "status": "forgotten", "refs_removed": ["refs/lattice/plans/p1/L0", ...]}`; its node branches stay; its id is never reused. Without `forget` (or `forget=false`) `DELETE` keeps v1.9's stop semantics. `forget` other than true/false: 422.

### POST /api/rebuild/{id}/continue
Continues an `interrupted` stand-alone build (v1.7 rebuild or v1.8 scoped build) by the rules of step 3 (a build interrupted during its baseline runs it again) -> `{"id", "status": "running", "from_round": n, "attempts": n}`. 409: not interrupted (`rebuild b1 is <status>; only an interrupted rebuild can be continued`), a plan node (`... resume the plan (POST /api/plan/<id>/run)`), the build slot taken, `max_attempts` reached; 404 unknown; 503 unavailable. An accepted change never built stays `build.state: not_built` across a restart.

### TEST ONLY: the stub agent
A stub round may carry `"run": "<shell command>"`: run in the worktree's project dir as its own process group (recorded in `pids`, killed on DELETE), the round goes on when it exits. With `LATTICE_STUB_AGENT_LOG=<file>` in Studio's environment every stub agent round appends `{"build", "round", "plan", "node", "key"}` to that file.

### The mock
The mock keeps its state in memory but serves every v1.10 field. Its second project (`lattice-studio`) starts with two fixture plans: `p1` `interrupted` while level 2 ran (`run` resumes it, the passed levels skipped), and `p2` waiting to land (`resume.kind: "land"`, the target checked out in the owner's working copy): `POST /api/plan/p2/land` answers 409 `branch_checked_out` unless the body confirms. `DELETE /api/plan/{id}?forget=true` drops a plan.


## v1.11 additions (build performance: scoped gates, the regression level gate, the level repair loop)

A Streak level took 35-45 minutes, mostly gate time: the full gate (`./check.sh`, ABCDX) ran three times per level, and 95% of its layer D waited out 30 s `Await`s on a service not built yet (`docs/results/2026-10-11-gate-timings.md`). v1.11 runs a node's gate on its scope plus the regression set, ends a level with a regression gate, repairs a level whose merged result fails instead of stopping the plan, records every gate's verdicts per commit, and kills a stopped gate's whole process tree. Code: `lattice/studio/rebuild.py`, `plan.py`, `state.py`, `hub.py`; `lattice spec check --await-cap`. Every new key is optional; without them Studio behaves as v1.10, except the repair loop (default 2 attempts; `level_repair_attempts = 0` is v1.10's stop) and the reuse of a tip's recorded results.

### Configuration: `[rebuild]` additions
```toml
[rebuild]
scoped_gate_args = ["--layers", "CD", "--x-repeats", "1", "--await-cap", "5"]  # default: none (gates as configured)
level_gate = "full"            # "full" (default) | "regression"
level_repair_attempts = 2      # repair rounds after a failing level gate (0: stop as in v1.10)
node_timeout = 1800            # seconds one plan node's agent step may run (default round_timeout)
node_max_tokens = 0            # output tokens one node's agent may spend in all (0/absent: no cap)
stop_grace = 5                 # seconds a stopped gate's group gets after SIGTERM before SIGKILL
```
The scoped args are appended to the **first** gate command (the Lattice check; it must forward `"$@"` last, as Streak's `check.sh` does), followed by `--only-file <selection>`; the other gates run as they are.

### `lattice spec check --await-cap SECONDS`
Caps every `Await.within_s` at SECONDS (the report notes `--await-cap N: every Await waited at most Ns (k step(s) capped); ...`); an effect that does not appear within the cap still fails the scenario. Opt-in: without it nothing changes. `check(..., await_cap=N)` is the API. The check CLI now also exits through `SystemExit` on SIGTERM, so its `atexit` guards stop app processes and services and remove containers; with `$LATTICE_CONTAINER_OWNER` set, `lattice.spec.dbserver` labels its containers `lattice.owner=<value>`.

### Scoped node gates and the selection file
With `scoped_gate_args`, a plan node other than the integration node runs its gates (the agent's runs and every round) as `<first gate> <scoped_gate_args> --only-file <worktree>/.lattice-scope/selection.json`. The selection is a JSON array of `scenario:<name>` selectors: **the scenarios of the node's scope** (every scenario its rules link to in the trace) **UNION the scenarios that passed at the start of its level** (the regression set, from the level start's per-tip result). An in-scope failing scenario missing from it is a bug (Studio asserts). `.lattice-scope/` is git-ignored through `info/exclude` and never committed. The exact scoped command is the brief's gate and the agent's only Bash allowlist entry (`Bash(<command>:*)`), and the brief gains a **Budget** section: `You have N minutes for this node. Run the scoped gate `...`, not the full one ...` (N from `node_timeout`; plus the output budget with `node_max_tokens`). Studio judges as before (every in-scope target passes, no regression), from the scoped gate's findings; a scenario the selection leaves out is never counted as fixed. The node's build record carries:
```json
"scoped_gate": {"command": "./check.sh --layers CD ... --only-file /…/.lattice-scope/selection.json",
                "selection_file": "/…/.lattice-scope/selection.json", "args": ["--layers", "CD", ...],
                "scenarios": 31, "in_scope": ["scenario:…", ...], "regression_set": ["scenario:…", ...]}
```
(`null` when the node runs unscoped), and `gates` is the scoped list.

### Level gates
`level_gate = "full"`: v1.10's full gate after every level. `level_gate = "regression"`: the first gate on `<passing before the level> ∪ <the scenarios of every scope landed so far, this level's included>` (written to the integration worktree's `.lattice-scope/level-selection.json`), **with the full layers** (no scoped args, no await cap), so the failing, not-yet-built scenarios of later nodes cost nothing; a scenario it does not select keeps its failing verdict (`carried`). **The last level, and any level holding the integration node, always run the unscoped full gate**; with `regression`, before the last level runs the plan also runs the full gate on its tip (`final_gate: {sha, failing, at, gates}`) unless that tip already has a full result, so the integration node decides from every check. `level_runs[].gate` adds `mode` (`full|regression`), `selected` (count, or null) and `in_scope_failing`.

A level now fails on **regressions** (a check failing that did not fail on the last good tip) **and** on a merged node's own target that passed its node gate but fails on the merged tip (race-only, or cross-area). Gates that cannot run still stop the plan.

### Per-tip results
After every start baseline, level gate and final gate (and a stand-alone scoped build's baseline and passing round), Studio records `<state dir>/results/<sha>.json` (in memory without a store):
```json
{"tip": "<sha>", "kind": "full|regression", "failing": ["scenario:…", "title:…"], "passing": ["scenario:…"],
 "carried": ["scenario:…"], "selected": ["scenario:…"] | null, "findings": [{"title", "target", "key"?, "file"?}],
 "counts": {}, "gates": [{"command", "exit_code", "seconds"}], "gate_cmds": ["./check.sh"], "source": "plan p1 level 0 (regression gate)", "at": "..."}
```
`passing` is every known scenario (the trace's, the JSON report's `timings.scenarios`, earlier passing ones, the selection) that does not fail: the next level's regression set. A tip's verdicts are never recomputed: a plan's start baseline on a tip with a recorded full or regression result of the same gate commands reuses it (`baseline.reused: true`). `GET /api/areas[?branch=B]` reads `failing`/`status` from the result of B's tip first (so landed levels report `built`), then v1.8's sources.

### The level repair loop
When a level gate fails on something a node can fix (not when gates could not run), the plan goes on: each failing check is **attributed** (a) to the merged nodes whose scope links it (the integration node only if no other does), else (b) to the merged nodes whose commit touched the finding's `file` (a path equal to it or a directory above it), else (c) to a dedicated **repair node** `repair-l<L>` (kind `repair`, `repair: true`, scoped to those checks, started from the failing merged tip, its commit merged after the level's nodes). Then up to `level_repair_attempts` attempts, each: the responsible nodes get one repair round (concurrently) in their worktree on their branch (made again from the branch if gone), continuing their agent session with `--resume` when the record has one (else a fresh session whose brief is the node's brief plus the repair brief: "The merged result fails these checks that passed in isolation ... Fix the code, never the spec or the gate."), judged by their own gate (scoped: their scope + the regression set) on the attributed checks plus their own targets; a pass commits on the node's branch (`build: <title> (repair <n> after the level gate)`); then the level merges again from its base and its gate runs again. Passes: the level lands (v1.10 rules). Still failing after the cap: the level's nodes stay `regressed`, the level run gets `repair_pending: true` and `error: {kind: "repair_exhausted", level, nodes, targets, message}`, the plan ends **`partial`** with that error, and `resume.kind` is `repair`: `POST /api/plan/{id}/run` merges the level's nodes onto the current tip and does another bounded pass (a new level run `repair_resume: true, repair_pass: n`; the old one `superseded: true`), no node starting over; at `max_attempts` passes `resume.available` is false. Repair usage (tokens, cost, rounds, wall) adds to the node (`usage`, `rounds`, `wall_s`, plus `repair_usage`) and the plan totals.

Repair-loop states:

| where | field | values |
|---|---|---|
| node | `status` | adds `repairing` (a repair round runs); `passed` after a verified repair; `regressed` while the level fails |
| node | `repairs` | repair rounds run for it |
| build record | `repairs`, `timeline[].repair` | repair rounds of the node's build; its timeline entries `repair: true` |
| level run | `repairs[]` | `{attempt, of, run, at, tip (the failing merged tip), targets, attributed: {target: {by: scope|files|repair_node, nodes}}, nodes: [{id, build_id, verify: pass|fail|error, commit, rounds, usage}], outcome: passed|failed|verify_failed, still_failing?}` |
| level run | `repair_pending`, `repair_resume`, `repair_pass`, `superseded` | exhaustion and resumed passes |
| plan | `repair_attempts` | attempts in all; `error.kind: repair_exhausted`; `resume.kind: repair` |
| plan | `timeline[].repair` | `true` on the loop's entries |

### Planner and areas
- `planner_usage: {tokens_in (cache included), tokens_out, cache_read_input_tokens, cache_creation_input_tokens, cost_usd_list_estimate, wall_s, calls}` on every plan; `totals` include the planner from the moment planning ends. A planner call cut off without a result event keeps the usage its stream showed.
- The planner message marks each area `built: true|false|null` (every check of it passes at the target's tip, from the per-tip results; null: never gated). After planning (and after a revision), a node with nothing failing in its scope at the target's tip is `passed` with `nothing_to_build` and `dropped_at_planning: <sha>` and never runs an agent; the scaffold too: a scaffold with rules runs only when its own checks fail, a scaffold with no rules only while something fails in the plan scope (the v1.9 "the scaffold always runs" rule is gone). The same rule applies when a level starts.
- Plan records add `gate_config: {scoped_gate_args, level_gate, level_repair_attempts, node_timeout, node_max_tokens}` (once run), `final_gate`, `repair_attempts`, `planner_usage`.

### A restart during a level repair
A Studio killed while a level was being repaired reconciles on load like any interrupted plan (`status: "interrupted"`, `interrupted_reason: "studio_restarted"`), and the repair is **kept, not discarded**: the nodes that were `repairing` become `interrupted`, the level run gets `repair_pending: true` with `error.kind: "repair_interrupted"` (`level`, `nodes`, `targets`, `message`), and `resume.kind` is `repair`. `POST /api/plan/{id}/run` then does another bounded repair pass from the merged nodes: a node build that had passed (it has its `commit`) is repaired again although it is `interrupted`; the round that never finished runs again with the session resumed, and no completed round or node starts over. `scripts/restart_drill.py` kills a real server at three moments (an agent round, a level gate, a repair round) and checks all of this; `tests/studio/test_restart_drill.py` runs it.

### Stop and budget hygiene
- A gate runs in its own session; while it runs Studio records its descendants in `pids` (`role: gate-child`, pid + start time + command line; exited ones dropped), and the gate's own entry carries `containers: <owner token>` (`$LATTICE_CONTAINER_OWNER` of that gate run). On DELETE of a build or plan, on a gate timeout, and on a restart's reconciliation: SIGTERM to the gate's group, up to `stop_grace` seconds for the check's own cleanup, SIGKILL, then every recorded descendant still alive (matched by pid, start time and command line; an app in its own session ignoring SIGTERM is SIGKILLed after the grace), then `docker ps -a --filter label=lattice.owner=<token>` and `docker rm -f -v` of each listed `lattice-*` container: never any other. Descendants a gate left after exiting on its own are terminated too. A gate's result may add `stopped: true` and `containers_removed`.
- An agent stopped by `node_timeout` (plan nodes; stand-alone builds keep `round_timeout`) or `node_max_tokens` keeps the usage its stream showed (each assistant message's usage, once per message id) instead of 0: the round gets `status: "timeout"`, `error: {kind: "timeout", reason: "node_timeout|round_timeout|node_max_tokens", message}`, `agent.partial: true`, and its files stay (the gates still run on them).

### `plan_defaults` on GET /api/projects
`{"max_parallel": 2, "models": {"fast", "mid", "frontier"}, "scoped_gate": [args] | null, "level_gate": "full|regression", "node_timeout": 1800.0, "level_repair_attempts": 2}` from the project's `[rebuild]`.

### The mock
Projects carry `plan_defaults` (scoped gate, regression level gate); plans carry `planner_usage` and, once run, `gate_config`; level gates carry `mode`/`selected`/`in_scope_failing`. A mock run's level 1 (in a plan of three or more levels) fails its gate once and is repaired over one poll (a node `repairing`, `repairs` on the level run and the node, timeline `repair: true`) before it lands. The second project gets a third fixture plan `p3` whose level 1 ran out of repair attempts (`partial`, `error.kind: repair_exhausted`, `resume.kind: repair`); `POST /api/plan/p3/run` repairs it again over one poll and goes on. One of p3's failing checks is owned by no merged node, so it also carries a `kind: "repair"` node `repair-l1` (`gave_up`; `attributed` by `repair_node`). A fourth fixture `p4` is `partial` because a node gave up and blocked its dependents (`resume.kind: "retry"`). Repair entries carry per-node `usage`, nodes `repair_usage`, `totals` include the planner, a resumed run's `baseline.reused` is true and a run's last level is preceded by `final_gate`.
