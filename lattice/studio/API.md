# Lattice Studio API contract (v1)

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
