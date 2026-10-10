# Lattice Studio API contract (v1.7)

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
The v1.3 record plus: `project`, `agent` (`claude|stub`), `finished`, `gates` (the commands), `base` (the commit the branch starts from), `branch`, `worktree` (absolute path; `null` once removed), `merge_hint` (`null` until passed), `usage` (`cost_usd`, `duration_ms`, `input_tokens`, `output_tokens`, summed over the rounds' result events), `error` (`null`, or `{kind, message}` for `usage_limit`, `git` or `internal`). `commit` gains `name_status` (`git diff --name-status base..commit`, as `"A path"`). Each round also has `finished`, `gates` (`[{command, exit_code, seconds, output_tail}]`, the tail capped at 20 000 characters), `spec_edited` (paths), `agent` (`cost_usd`, `duration_ms`, tokens, `num_turns`, `session_id`) and `error` (`null` or `{kind: usage_limit|cli|timeout, message}`). `check.counts` sums `PASS|FAIL: N findings (D n, X n)` lines over the gates, else `{<gate command>: n}`; `check.findings` come from the lattice report's finding lines (target = their scenario), from lines starting `FAIL`, `finding` or `✗` (target = a `scenario:`/`cell:`/... token in the line, else `""`), or `<gate> exited <code>: <last line>`. `?wait=S` (0..60 s) holds the answer until the rebuild has finished. `GET /api/rebuild` lists every record of the project.

### End states and branches
- `passed`: one commit on the rebuild branch (`git add -A` in the worktree minus caches; message `build: <requests>`, a sentence naming the changes and the round, and the trailer `Lattice-Rebuild: <id> (<project>)`; the author is the repository's identity), the worktree is kept for review, and `merge_hint` reads ``Studio never merges. To take the rebuilt code, run `git merge rebuild/c1` in <repo>; the worktree <dir> stays for review.`` **Studio never merges into the owner's branch.** `project.git_head` and `app.last_check` describe the owner's checkout and do not move until the owner merges.
- `gave_up`: after `max_rounds` failing rounds, or at once when the agent hits its usage limit (`error.kind: usage_limit`); the branch and worktree stay (uncommitted edits), nothing is committed. A gave-up change can be rebuilt again (on `rebuild/<id>-2`, ...).
- `stopped` (`DELETE`): the running agent's or gate's process group is killed, the worktree removed, the branch deleted when it has no commit of its own (`branch: null`, `worktree: null`), rounds still in progress dropped.
- A crashing agent (non-zero exit, no result) is an error round (`error.kind: cli`); the loop goes on.

### `build` on `model.changes[]`
v1.3's shape plus `branch` (while `rebuilding`, and when `built` or `gave_up`) and `commit` (when `built`): `{"state": "built", "rebuild_id": "b1", "branch": "rebuild/c1", "commit": "<sha>"}`. Build state is in memory: after a restart every accepted change is `not_built` again (the branches stay in git).

### GET /api/model: `project.git_head`
Read live on every call (it was cached with the spec files, so a commit that left them alone, like a merge of a rebuild branch, did not show).

### The mock
The mock's rebuild record has `branch`, `base`, `worktree` and `merge_hint`, its `build` has `branch` and `commit`, and a passing mock rebuild no longer moves `project.git_head`. It still previews the rebuilt code in the Code lens and turns `app.last_check` to pass (the real server does neither until the owner merges).
