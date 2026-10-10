# Lattice Studio API contract (v1.3)

`lattice studio PROJECT_DIR [--port N] [--mock FIXTURE_DIR]` serves the web UI at `/` and this JSON API. It polls; there are no websockets.

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

When a rebuild passes: the changes' `build.state` becomes `built`, `app.last_check` becomes a `pass` check (no findings), `project.git_head` moves to the rebuild's commit, and `GET /api/code/file` marks the rebuilt lines (`rebuilt_in`) and no longer reports failing coverage or findings.

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
