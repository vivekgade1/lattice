# Studio UI for contracts and the improvement loop (API v1.21): pre-registration

## 1. Status and gap

Date: 2026-10-15. Branch `np-studioui` (worktree `lattice-studioui`, cut from `np-integ` 30c9120, which holds
np-apply, np-improvehard, np-plumbing, np-transitive, np-perfengines, np-market). **Written BEFORE any code of
this change exists.** Measured here (and only these): `dogfood/studio/check.sh --json` at 30c9120 PASS, 435
cases = 435 scenario steps over 53 scenarios (one case per step), 69.4 s; `pytest --collect-only`: tests/studio
467, tests/improve 197, tests/studio/test_web_mock.py 38; `dogfood/studio/spec.py` 36 `Unchecked`, trace rules
R1-R97; proposal ids `imp-9bade1d1` = sha1(`change:rejected`)[:8], `imp-3e1cd7f9` = sha1(`gate:level_regression:X`)[:8];
`draft()` on a hand-made 2-event reject log: exit 0, `template: "prereg"`, `toml: null`; `verify()` on it, with or
without `at`: exit 3 `not_closed`. **Every other number below is a prediction.** No LLM, no API key, 0 tokens.

**The gap (from the code).** Contracts already have HTTP routes, `GET /api/workspace` (v1.16), `/api/contracts`
(v1.17), `/api/contracts/impact` (v1.18), `/api/linked-questions` (+ `/{id}`, `POST /{id}/resolve|dismiss`),
`/api/linked-notifications` (v1.19), each "no web UI change". Improve has only `GET /api/improve` (v1.15, "Studio
UI: none"); `draft`, `verify`, `apply`/`--discard` are CLI-only. So contracts need **UI only, zero routes**;
improve needs **UI + six routes**, apply/discard the only new writes. `lattice/contracts/transitive.py`
(np-transitive) IS in this base; no route exposes it and nothing here uses it.

## 2. Decisions (fixed before coding)

**D1 Routes (API.md v1.20 -> v1.21; every route project-scoped like `/api/improve`: bare = default project,
`/p/<name>/api/...` = that project, unknown name 404 `no such project`).** `{id}` must match
`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` after unquoting, else 422 `{"detail", "code": "bad_id"}` (the record route
reads `<state>/applies/<id>.json`: `..`, `/` and `%2F` never reach a path).

| # | Method, path | Kind | Answers |
|---|---|---|---|
| 1 | GET `/api/improve/{id}/draft` | read | 200, body = `lattice improve draft ID --json` key for key (refusal too: `refused: true`, reason) |
| 2 | GET `/api/improve/{id}/verify[?at=TS]` | read | 200, body = `lattice improve verify ID [--at TS] --json` key for key (verdicts and refusals) |
| 3 | GET `/api/improve/applies` | read | 200 `{format: 1, enabled, why_disabled, applies, running, problems}` |
| 4 | GET `/api/improve/{id}/apply[?wait=S]` | read | 200 the job (below); 404 `not_applied`; 422 wait (the existing `wait must be ...` text, 0-60) |
| 5 | POST `/api/improve/{id}/apply` | **write** | 202 the job, `status: "running"`; refusals of D3 |
| 6 | POST `/api/improve/{id}/discard` | **write** | 200 `{id, command: "discard", exit_code, result}`; refusals of D3 |

Every other method on these paths, and `POST /api/improve`, answers 404 `no such route` (as today). Read
routes write nothing: no state dir creation, no event, no cache (draft re-reads `studio.toml` from disk as the
CLI does, not Studio's loaded config). The HTTP status says whether the route ran; `exit_code` (and
`refused`) in the body say what the command decided, as v1.18's impact does. No existing route changes a byte:
`GET /api/improve`, `/api/projects`, `/api/model` gain no key (the opt-in is reported by route 3 only).

**D2 Apply runs in the background, bounded by the gates' own timeouts.** POST 5 starts one worker thread
calling `apply.apply(root, store.root, lambda: store.root, id, branch=, no_gates=not gates, state_flag=, stop=)`.
Body: `{"draft_sha256": 64 lowercase hex (required), "gates": bool = true, "branch": null | str 1-200}`. Job:
`{id, command: "apply", status: "running"|"done", source: "job"|"record", started_at, finished_at, options
{gates, branch}, exit_code (null while running, then 0-3), result (null | the CLI's `--json` record | refusal |
{"error"}), record (while running: the on-disk record once written, `gates.status: "running"`, so branch and
commit show before the gates end), stale}`. After a restart (no job, a live record): `source: "record"`, `done`,
`exit_code` null, `stale` = (`gates.status == "running"`). `?wait=S` holds until `done` or S. **Bound:** no
Studio timer; each gate has `[rebuild] gate_timeout` (900 s) + `stop_grace`, so a job is at most n_gates x
(timeout + grace) + git time (dogfood: 2 gates, ~30 min worst case); `gates: false` skips them. **Close:**
`server_close` sets `stop` and joins the worker for at most `stop_grace + 10` s: the gate tree is terminated by
apply's own path, the record says `interrupted`, exit 2, branch/worktree/record kept (the CLI's SIGTERM case).
That needs the one `apply.py` change: keyword-only `stop: threading.Event | None = None`, read where
`interrupt.hit` is read (a worker thread installs no signal handler); `None` keeps the CLI byte-identical.
`state_flag` = `str(store.root)` only when it differs from the CLI's default `state_dir(root)` (Studio slugs the
hub name, the CLI the directory name): `next` commands then run verbatim, and otherwise the record = the CLI's.

**D3 Guards, in order, first hit wins (POST 5 and 6; `{"detail", "code"}` bodies).** (a) unknown project 404;
(b) 403 `apply_disabled`: the server was not started with `lattice studio --allow-apply` (new flag, default
**off**, server-wide; route 3 reports `enabled: false, why_disabled: "start Studio with --allow-apply"`);
(c) 403 `bad_origin`: `Host` is not `127.0.0.1:<port>` / `localhost:<port>` (DNS rebinding), or an `Origin`
header is present and is not `http://` + that host (a request without `Origin`, e.g. curl or the checker,
passes); (d) 415 `json_required`: `Content-Type` is not `application/json` (a cross-site "simple" POST cannot
send it without a preflight, which the server never grants); (e) 400 `the body must be a JSON object`
(existing); (f) 422 `bad_id`; (g) 422 `bad_body` (POST 5 only: `draft_sha256` missing or not 64 hex, `gates`
not a bool, `branch` not null or a 1-200 char string); (h) 409 `no_state` (`[state] enabled = false`: there is
no state dir to hold the record); (i) 409 `apply_running` (one apply or discard at a time **per server**, not per
project: two hub projects may live in one repository); (j) POST 5 only: 409 `draft_changed` with the current
`draft_sha256` (or null) when `draft()` on the same inputs is refused or the sha256 of its `text` differs from
the body's (the owner applies exactly the draft they read; the record stores the same hash). Then 202. Guards
(b)-(d) are new and apply only to routes 5 and 6; existing write routes are unchanged (a named risk).

**D4 Never.** Studio adds no git call: the job only calls `apply()`/`discard()`, whose rules stand (no merge,
push, reset, checkout, stash, update-ref; hooks off; owner's checkout, index, other refs, ledger, event log
untouched). Studio logs **nothing** for either (a `human:apply` cluster would change `GET /api/improve`; gate
runs in the log would pollute verify's windows). No route closes the ledger or merges; `next.merge`/`next.close`
are text to copy, never buttons. No MCP tool is added.

**D5 Idempotence, edge cases.** Double click: second POST 5 409 `apply_running`; re-apply after done: exit 3
`already_applied`; discard twice: exit 3 `not_applied`; discard during an apply: 409. The listing holds live
`applies/*.json` only (never `*.discarded-N.json`), by `applied_at` then id, unreadable files as `problems`. A
restart mid-gates loses the job (`stale`; the owner discards or re-runs gates). One signal in two hub projects of
one repo: the second gets apply's `branch_exists`/`worktree_exists` (the path is `<repo>.improve/<id>`). `at` is
passed through (the CLI validates it).

**D6 Web UI (no new server route for contracts).** *Improve* view in the Evidence group, always listed:
proposals (`GET /api/improve`: score, class, kind, `needs_owner` without a Draft button), applies (route 3), a
Draft panel (route 1: sections, the `toml` snippet), a Verify panel (route 2, optional `at`), and, only when the
draft's `template == "config"`, `enabled` and no live record, **Apply on a branch...**: a confirm dialog with the
draft line, `improve/<id>`, "Run the gates (can take minutes)" checked by default and "Lattice never merges,
pushes or closes the ledger."; the UI hashes `draft.text` (`crypto.subtle`, loopback is a secure context), POSTs,
polls route 4 (`wait=30`, epoch-aware), then shows the record (edit, diff, branch, commit, gates with collapsed
`output_tail`, warnings, a **NOT MERGED** banner, the six `next` commands copyable) and **Discard...** (confirm).
Disabled: the button is replaced by "Apply is off for this Studio (start it with --allow-apply)" plus the CLI
command. *Contracts* view in the Evidence group, listed only when `GET /api/workspace` names a workspace: the
current hub project mapped to its manifest name through `projects[].hub_name` (not in the manifest: a one-line
note, nothing else); its links from `/api/workspace` (as consumer and as provider); **Check contracts** (on
demand, never on load or polled: it spawns export subprocesses) -> `/api/contracts?consumer=<manifest name>`
(status badge, findings, gaps, pinned vs current); for a provider, **Preview impact** with a candidate field
(default `HEAD`) -> `/api/contracts/impact` (labels, counts, findings, "advisory" banner); linked questions
(`/api/linked-questions`, status filter, `legend`, Resolve/Dismiss with a note through the existing POSTs) and
`/api/linked-notifications`. Nav attention on Contracts: "N open" from one `?status=open` read per project load.
All calls go through `api.js` (the existing "no fetch outside api.js" test). Unchanged: every other view module,
`index.html`, the switcher, the Map.

**D7 Mock parity.** `mock_server.py` serves routes 1-6 with the real shapes: draft and verify computed by
`lattice.improve` itself from the existing fixture log and ledger (as its `/api/improve`); the default project is
`enabled: true` (apply of `imp-3e1cd7f9` runs ~1.5 s in memory, then a fixture record with the real record's key
tree, gates `passed` x2; discard in memory), the second project `enabled: false` (403), so both UI states show.
The mock runs no git and applies the same D3 ladder (b excepted per project as stated).

## 3. Probes (blind; scratch roots under the session scratchpad; real servers on 127.0.0.1)

Masking for equality: `applied_at`, `started_at`, `finished_at`, every commit sha, gate `seconds`,
`output_tail`. Deterministic probes run **3 times**: pass = 3 of 3 identical (masked) plus the row's rule.
"Synthetic repo" = `git init -b main`, identity in `.git/config`, `core.autocrlf=false`, a committed
`studio.toml` with `[rebuild]` and `[state] dir = "../state"`, `../state/events.jsonl` = the first 9 lines of
`docs/results/data/2026-10-12-spike3/i3-events.jsonl` (proposal `imp-3e1cd7f9`, config).

**P0 guard ladder** (synthetic repo; started with and without `--allow-apply`). Rule: 14 of 14 exact status and
code; after every non-2xx row the state dir, `.git` refs and working-tree hashes are unchanged.
| Row | Request | Expect |
|---|---|---|
| a | POST 5, no flag | 403 `apply_disabled`; route 3 `enabled: false` |
| b, c | `Origin: http://evil.example`; `Host: evil.example:<port>` | 403 `bad_origin` x2 |
| d | `Origin: http://127.0.0.1:<port>`, the right `draft_sha256` (control) | 202 |
| e | `Content-Type: text/plain`, valid JSON text | 415 `json_required` |
| f | body `[]` | 400 |
| g, h | id `..`; id `a%2Fb` (also on GET 4) | 422 `bad_id` x2 |
| i, j | no `draft_sha256`; `gates: "yes"` | 422 `bad_body` x2 |
| k | `[state] enabled = false` project | 409 `no_state` |
| l | second POST 5 while a `sleep 5` gate runs | 409 `apply_running` |
| m | `draft_sha256` of 64 zeros | 409 `draft_changed`, `draft_sha256` = sha256 of route 1's `text` |
| n | POST 6 with the flag off | 403 `apply_disabled` |

**P1 read routes = CLI.** Two states: the synthetic one (open) and a copy whose ledger closes
`gate:level_regression:X` (a real verdict). Ids `imp-3e1cd7f9`, `imp-00000000`; route 1 vs `lattice improve draft ID
--json --project P --state S`, route 2 with and without `?at=2026-10-01T12:00:00Z` vs `verify`. Rule: 12 of 12
parsed-JSON equal; same under `/p/<name>/`; state dir bytes unchanged (and not created when absent).

**P2 apply = CLI apply.** Twin synthetic repos at one path (run sequentially): POST 5 `{gates: false}` vs
`lattice improve apply imp-3e1cd7f9 --no-gates --json` (with `--state` exactly when D2 says). Rule: exit 0 both;
records equal (masked); tree id equal; `edit.action` `inserted`, `edit.line` `level_gate_args = ["--x-repeats",
"2"]`; route 4 `?wait=30` returns within 30 s; route 3 lists it. **P2c** a hub name != directory name: `next`
holds `--state <store root>` and running `next.close` verbatim writes `<store root>/improvements.jsonl`.

**P3 gates and close.** a `gates = ["true", "false"]`: exit 1, `failed`, 2 results, branch kept. b `["sleep 30"]`,
`gate_timeout = 2`: exit 1, 124, `timed_out`, seconds in [2, 9]. c `["sleep 30"]`, SIGTERM to the server 2 s into
the gate: the server exits within `stop_grace + 10` s, record `gates.status: "interrupted"`, 0 `sleep 30` processes
left (ps). d restart after c: route 4 `source: "record"`, `stale: false`; a record hand-set to `running`: `stale:
true`. Rule: 4 of 4.

**P4 discard.** After P2: POST 6 -> 200 exit 0, branch and worktree gone, record renamed
`imp-3e1cd7f9.discarded-1.json` byte-identical, route 3 `applies: []`, route 4 404; again -> exit 3
`not_applied`; after `git branch keep <commit>` on a fresh apply -> exit 3 `merged`. Rule: 3 of 3.

**P5 safety** (P2, P3a, P4). As improve-apply A3: before/after hashes of the owner's `.git/{index,HEAD,config,
packed-refs}`, every ref but the new one, every working-tree file, `events.jsonl`, `improvements.jsonl`; a `git`
shim on PATH logs argv: 0 `merge|push|reset|checkout|stash|update-ref`; `GET /api/improve` byte-identical before
and after; static: `server.py`/`mock_server.py` add 0 `subprocess`/`git` calls for these routes, 0 hits of
`claude`, `anthropic`, `lattice.llm` in the new code. Rule: 0 changes, 0 forbidden argv.

**P6 hub routing.** Hub of two synthetic projects A, B (separate repos): `/p/B/api/improve/imp-3e1cd7f9/apply`
writes only B's repo and state; A's hashes unchanged; route 3 of A empty; `/p/nobody/...` 404. Same-repo hub
(two projects in one repo): concurrent POST 5 on both -> one 202, one 409 `apply_running`. Rule: 3 of 3.

**P7 byte-compat.** Base server = `git archive 30c9120` in scratch; same seed, same path, sequential: GET
`/api/model`, `/api/projects`, `/api/improve`, `/api/workspace`, `/api/contracts`, `/api/contracts/impact?...`,
`/api/linked-questions`, `/api/linked-notifications`, `/api/engines`, `/api/mcp`, `/api/areas`, `/api/branches`,
`/api/plans`, `/api/rebuild`, `/api/session` -> 15 of 15 byte-identical bodies and statuses (base vs new, the
new one with and without `--allow-apply`). Static: sha256 of every web file except `js/app.js`, `js/api.js`,
`css/views.css` and the two new views equals 30c9120's. `lattice studio --help` differs only by `--allow-apply`.

**P8 mock parity.** For routes 1-6, mock and real (P2's state) answer the same recursive key tree and JSON types
for: draft config, draft refused, verify refused, applies (empty, one, disabled), job running, job done (exit 0),
job refused, discard 0 and 3; and the same status for P0 rows a, e, f, g, i, m. Rule: 17 of 17.

**P9 the gate (`dogfood/studio/check.sh`).** The harness's `SERVE` gains `--allow-apply` (nothing else in the
harness or the seed changes, so every recorded commit id stands). New rules R98-R100 (checked), R101-R102
(`Unchecked`, pointing to pytest and P0/P3/P6 and to the browser walk). Exactly 3 new scenarios, 0 existing
steps changed: `improvement_drafts_and_verdicts_are_read_only` (11 steps: draft of `imp-9bade1d1` refused
`unknown_id` on the empty log; change, wait, reject; draft `template: "prereg"`, `toml: null`, same under
`/p/notes`; verify refused `not_closed`, also with `?at=2026-10-01T12:00:00Z`; POST to route 1 404; `GET
/api/improve` still `events: 2`; UNCHANGED), `apply_and_discard_refuse_without_writing` (14 steps: route 3
`enabled: true`, `applies: []`, `running: null`; POST 5 zeros -> 409 `draft_changed`; `{}` -> 422 `bad_body`;
change, wait, reject; zeros again -> 409; POST 6 -> 200 exit 3 `not_applied`; route 4 -> 404; route 3 bare and
`/p/notes` empty; POST to route 3 404; `GET /api/improve` `events: 2`; UNCHANGED) and `improve_ids_and_waits_are_validated` (7 steps: `a%20b` on routes 1, 2, 5 -> 422
`bad_id`; `gates: "yes"` -> 422; `branch: ""` -> 422; `?wait=99` -> 422; UNCHANGED). Rule: PASS, **435 + 32 = 467
cases**, 0 findings; `Unchecked` 36 -> 38; trace rules 97 -> 102; wall 69.4 s -> ~72 s (pred.).

**P10 UI facts without a browser** (`tests/studio/test_studio_ui.py`, new). Needles: nav entries `id: "improve"`
and `id: "contracts"` (the latter gated on the workspace), every route string of D1 in `api.js`, the strings of
D6 ("Apply on a branch", "NOT MERGED", "Discard", "Run the gates", "Lattice never merges", "--allow-apply",
"Check contracts", "Preview impact", "advisory"); `node --check` on every JS file; node runs of exported pure
functions: `canApply(draft, applies)` (true only for `template == "config"`, `enabled`, no live record: 6 of 6
truth-table rows), `applyRefusal(code)` (a distinct sentence ending "." for each D3 code, none containing a status
number), and `api.applyProposal` with a stubbed `fetch` (POST, `Content-Type: application/json`, body keys
`draft_sha256`, `gates`, `branch`). Rule: all pass; test_web_mock.py's 38 pass unchanged.

## 4. Mutants (one edit each; `dogfood/studio_ui_mutants.py`, served from a patched copy via
`LATTICE_STUDIO_PYTHONPATH` like the existing mutant runs)

U1 `--allow-apply` ignored (always on) -> P0a, P0n. U2 `Origin` check removed -> P0b. U3 `Host` check
removed -> P0c. U4 `Content-Type` check removed -> P0e. U5 the id regex skipped on route 4 (reads
`<state>/applies/<id>.json` raw) -> P0g/h, gate scenario 3. U6 the `draft_sha256` comparison removed -> P0m, gate
scenario 2 (zeros answer 202, not 409). U7 apply/discard append a `human` event -> gate scenarios 1-2 (`events:
3`), P5. U8 `no_gates=gates` (inverted) -> P2, P3a. U9 `stop` not passed to `apply()` -> P3c (`sleep` left, record
`running`). U10 `state_flag` always None -> P2c. U11 the `apply_running` lock per project, not per server -> P6
same-repo row. U12 the applies listing globs `*.json` (discarded records listed) -> P4. U13 verify drops `at` ->
P1 `?at=` rows. U14 the mock's route 3 drops `running` -> P8. U15 `canApply` true for a `prereg` draft -> P10.
U16 `GET /api/improve` gains an `applies` key -> P7. U17 one byte of `views/rebuilds.js` edited -> P7 hashes.
Rule: 17 of 17 caught by a predicted catcher; the existing mutant sets (`dogfood/studio/*_mutants.py`,
`improve_mutants.py`) still all caught; a survivor is reported, never fixed silently.

## 5. Risks and what this cannot show

1. **Rendering is browser-only.** Checked without a browser: HTTP shapes and statuses (gate, P0-P9), that the
   UI's code calls the right routes with the right bodies, contains the stated strings and passes `node
   --check`, and the pure decision functions. Not checked blind: layout, focus order, a11y, that a panel is
   visible, the polling loop's feel, colour. A browser walk on the mock (draft, apply, running -> done, NOT
   MERGED, discard, the disabled text; contracts check, impact, a resolve with a note; console 0 errors) is
   recorded post hoc in the results, evidence only, never a pass rule.
2. **Loopback is the only authentication.** Any local process can call routes 5-6 once opted in. D3 (b)-(d)
   are defence in depth against browsers (cross-site POST, DNS rebinding), not auth. Existing writes (accept,
   rebuild, plan run, land) keep today's weaker posture: not widened here, not fixed here.
3. **Gates in Studio's process environment**, not the owner's shell: env differences can change a gate's
   result vs the CLI; a long gate holds the per-server lock for up to D2's bound; a `kill -9` of Studio orphans
   the gate's session (its own session by design) and leaves a `running` record (`stale` only after restart).
4. **The draft hash binds what was read, not what is wise**: the gates stay a smoke check (apply prereg risk 1).
5. **Mock divergence**: its per-project `enabled` is a fixture choice; the real opt-in is server-wide.
6. **Slow contract routes** run on demand only; a late answer after a project switch is dropped by the epoch.
7. **Builder bias**: P7/P8 compare against an archive of 30c9120 and the real server, not the builder's view.

## 6. Blind vs post hoc; verification

Every prediction above is blind; a miss is a finding, the number stays as first measured, and any fix it
motivates is a separate, labelled post-hoc commit. Results: `docs/results/2026-10-15-studio-ui-results.md`,
data `docs/results/data/2026-10-15-studio-ui/`.
```
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-studioui PYTHONDONTWRITEBYTECODE=1
uv run pytest tests/studio tests/improve tests/workspace tests/contracts -q   # 1029 + new, all pass
uv run python dogfood/studio_ui_probes.py            # P0-P8 -> docs/results/data/2026-10-15-studio-ui/
dogfood/studio/check.sh                              # P9: PASS, 467 cases
uv run python dogfood/studio_ui_mutants.py           # control + U1-U17
uv run python dogfood/improve_apply_probes.py        # unchanged (apply.py's stop hook is CLI-inert)
uv run python dogfood/improve_mutants.py             # IM*, IA* still caught
git diff 30c9120 --stat                              # only the files of section 7
```

## 7. File ownership (the build node may touch only these)

Edit: `lattice/studio/server.py` (routes, `Studio` apply/discard/draft/verify/applies, the job, the per-server
lock, `--allow-apply`, `server_close` stop); `lattice/studio/mock_server.py`; `lattice/studio/API.md` (title
v1.21, the `--allow-apply` flag in its first line, a v1.21 section); `lattice/improve/apply.py` (only the
keyword-only `stop` hook, at most 20 changed lines); `lattice/studio/web/js/app.js` (two nav entries, imports),
`js/api.js` (the six calls), `css/views.css` (appended rules only); `dogfood/studio/{spec.py, REQUIREMENTS.md,
spec.trace.json}` (R98-R102, 3 scenarios, 2 `Unchecked`), `dogfood/studio/harness.py` (`SERVE` only).
New: `lattice/studio/web/js/views/improve.js`, `js/views/contracts.js`, `tests/studio/test_improve_api.py`,
`tests/studio/test_studio_ui.py`, `dogfood/studio_ui_probes.py`, `dogfood/studio_ui_mutants.py`, the results doc
and its data dir. Not touched: `lattice/improve/{draft,verify,analyze,events,__main__}.py`, `lattice/contracts/`,
`lattice/mcp/`, `lattice/studio/{hub,rebuild,plan,state}.py`, `index.html`, every other web file, the seed
(`dogfood/studio/project_seed/`), `tests/studio/test_web_mock.py`, Streak, job-scheduler, the main checkout.

## Amendment 1 (orchestrator rulings, 2026-10-15, before any code)

**A. New base: np-mcphttp (G9) is merged into this branch (2a8ac57).** The dogfood gate at this base is
**PASS with 449 cases over 54 scenarios**, and rule **R98 is taken** (chat sessions, appended at the end of
`REQUIREMENTS.md`, trace entry and `Unchecked` note already present). Therefore this document's numbers
shift as follows and the shifted values are the registered ones:
- new rules are **R99-R101 (checked)** and **R102 (guards, `Unchecked`), R103 (web UI, `Unchecked`)**;
  wherever this document says R98-R100 / R101 / R102, read R99-R101 / R102 / R103;
- the gate prediction is **449 + 32 = 481 cases**, 57 scenarios (54 + 3); every existing step unchanged;
- the `Unchecked` count is whatever the base has plus 2 (the builder reports the true base number).
Appending the new rules after R98 follows the file's convention, so no earlier rule's `line` moves.

**B. Security posture (approved as designed):** `--allow-apply` off by default; the 7 guards in the stated
order; `draft_sha256` required; Studio logs no event for apply/discard; merge and ledger-close remain
commands to copy, never buttons; the apply lock is per server. The small keyword-only `stop` event in
`lattice/improve/apply.py` is approved (no effect on the CLI; byte-compat probe covers it).

**C. Not allowed:** any route that merges, pushes, closes a ledger entry, or writes outside the apply
worktree and the state `applies/` directory. A defect in an existing route found while building is a
finding, never a silent fix.

**D. Reporting.** Browser-only claims (rendering) are labelled NOT RUN unless a real browser walk is done;
the DOM/HTTP-level checks are the pass rules, as in section 3.
