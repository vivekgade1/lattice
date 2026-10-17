# Lattice Studio server: requirements

Lattice Studio (`python -m lattice.studio PROJECT_DIR`) serves one project directory (`REQUIREMENTS.md`, `spec.py`, `spec.trace.json`) over a JSON API on 127.0.0.1, with no login. These rules are what `lattice/studio/server.py` and `tests/studio/test_server.py` establish; `lattice/studio/API.md` is the contract.

## The model

- GET /api/model answers 200 with the project (`name`: `[project] name` of its studio.toml, else the directory's name; `git_head`: the short id of the repository's HEAD), the rules of the trace with their status and links, the full REQUIREMENTS.md text, the data model's tables, `app` (`configured`, `check_running`, `last_check`) and the change records (`id`, `request`, `status`).
- A change that is drafting, ready, rejected, cancelled or failed never writes the project's files: GET /api/model keeps answering the same markdown and `git_head` until a change is accepted.

## Change sets

- POST /api/changes {"request": ...} answers 200 with the change's id (`c1`, then `c2`, ... in order) and drafts it in the background: its status starts as `drafting`, goes through `checking`, and ends `ready` (or `failed`). GET /api/changes/{id} answers the change record.
- GET /api/changes/{id}?wait=S (S from 0 to 60 seconds) answers once the change's background job has finished, or after S seconds; a `wait` that is not such a number answers 422.
- A ready change's record names the rules its edit added, changed and removed, and `checks.valid` says whether its drafted spec validates.
- POST /api/changes/{id}/accept on a ready, valid change writes REQUIREMENTS.md, spec.py and spec.trace.json and makes exactly one git commit of those files on top of HEAD, with the message `spec: <request>` and the trailer; it answers 200 with the commit id and the message. Other files of the project are not committed.
- After an accept, GET /api/model answers the new markdown, `git_head` the new commit, and the change's status `accepted`.
- A change can be accepted once: a second accept answers 409 ("change <id> is accepted, not ready").
- A proposal drafted on files that have changed since (for example because another change was accepted) is stale: its accept answers 409 ("the spec changed since this proposal") and writes nothing.
- POST /api/changes/{id}/reject answers 200 {"ok": true} and the change's status becomes `rejected`; an accept after it answers 409. Rejecting an accepted change answers 409 ("change <id> is already accepted").
- A change rejected while it is still drafting or checking stays rejected: its background job's result is dropped and it can never be accepted.
- DELETE /api/changes/{id} cancels a change that is still drafting or checking: it answers 200 {"ok": true, "status": "cancelled"}, the change stays `cancelled` after its background job ends, and it can never be accepted. Cancelling any other change (ready, failed, cancelled, accepted, rejected) answers 409.
- A change whose markdown edit or draft fails ends `failed` with the error in `error`; it cannot be accepted (409) or cancelled (409).

## Follow-ups

- POST /api/changes with "parent": "<id>" drafts a follow-up on top of the parent's proposal (its markdown already holds the parent's edit). The parent must be ready, else 409.
- When the follow-up becomes ready, it supersedes its parent: the parent's status becomes `rejected` and its `superseded_by` names the follow-up; a further follow-up on that parent then answers 409.
- Accepting the follow-up writes the parent's edit and its own in one commit.

## Errors

- A request body that is not a JSON object answers 400; an empty `request` (or `question` for POST /api/ask) answers 422 with a `detail`.
- An unknown change id answers 404 ("no change <id>") on GET, DELETE, accept and reject, and as a follow-up's parent; an unknown /api/ path answers 404 ("no such route").
- Every error answer is a JSON object `{"detail": "..."}`.

## Ask

- POST /api/ask {"question": ...} answers 200 with `kind` (yes, no, value or not_specified), `answer`, `explanation` and `citations` checked against the spec; without an app configured, `verified` is null.
- An answer's content comes from the LLM.

## The web UI

- Any other GET path serves a file of the web UI directory (`/` is index.html); a path that resolves outside that directory, or names no file, answers 404 {"detail": "not found"}.

## Not covered here

- POST /api/check runs the full check of the current spec against the configured app in the background and reports it in `app.last_check`.
- The chat runs on Claude Code sessions (POST /api/session and the routes under it).
- A change's impact is checked against the configured app when `studio.toml` hooks one up.
- Outside a git repository, accept writes the files and answers `commit: null` with a note; a git failure answers 500 after the files are written.

## Projects

- GET /api/projects answers 200 with every project the server serves, in order: `name`, `root`, `git_head`, `spec` (whether `spec.py` and `spec.trace.json` are present), `app_configured`, `last_check` (null until a check has run) and `default`. A server over one directory lists that one project, as the default.
- Every /api/ route is also served under /p/<name>/api/... for a served project: /p/<name>/api/model answers what the bare /api/model answers for the default project, and a change made through one path is the same change through the other.
- A /p/<name>/ path whose name is no served project answers 404 ("no such project").
- Projects served by one server do not share change sets: each project numbers its own changes from `c1`, and a change made in one project is not visible in another.

## Rebuild

- POST /api/rebuild {"changes": [...]} on accepted changes answers 200 with the rebuild's id (`b1`, then `b2`, ... in order) and runs it in the background in a new git worktree, on a new branch `rebuild/<first change id>` (`-2`, `-3`, ... when that branch exists) made from HEAD. GET /api/rebuild/{id} answers its record; with ?wait=S (S from 0 to 60 seconds) it answers once the rebuild has finished, or after S seconds.
- Each round, the coding agent edits the code in the worktree, then Studio itself runs the project's gate commands there: a round passes only when every gate exits 0. A failing round's findings, taken from the gates' output, are in the round's record with each gate's exit code, and they are the next round's brief.
- A round in which the agent edited REQUIREMENTS.md, spec.py or spec.trace.json fails with the finding "spec files were edited", and those files are restored to the base commit's content.
- When a round passes, the rebuild ends `passed` with exactly one commit on its branch, on top of the base commit, with the message `build: <requests>` and a `Lattice-Rebuild:` trailer, holding the files the rounds changed. After `max_rounds` failing rounds it ends `gave_up`, with nothing committed.
- A rebuild never merges and never moves the owner's checkout: after it passes, GET /api/model answers the same markdown and `git_head` as before, and the record's `merge_hint` says how to merge the branch.
- An accepted change carries `build` in GET /api/model: `not_built` before any rebuild and after a stopped one, `rebuilding` while one runs, `built` with its `rebuild_id`, `branch` and `commit` once one passed, and `gave_up` when one gave up. A gave-up change can be rebuilt again.
- POST /api/rebuild for a change that is not accepted answers 409 ("change <id> is <status>, not accepted"), for a change already rebuilding or built 409 ("change <id> is already <state>"), and while a rebuild runs another one in the same project answers 409 ("a rebuild is already running in this project (<id>)"). An unknown change answers 404 ("no change <id>") and a body without a non-empty list of change ids, or with `max_rounds` below 1, answers 422.
- DELETE /api/rebuild/{id} stops a queued or running rebuild: it answers {"id": ..., "status": "stopped"}, its worktree is removed, its branch too when it has no commit of its own, and its changes go back to `not_built`. DELETE on a finished rebuild answers 409 ("cannot stop a rebuild that is <status>"); an unknown rebuild id answers 404 ("no rebuild <id>") on GET and DELETE.
- GET /api/projects reports `capabilities.rebuild`: true when the project's `[rebuild]` table names a git repository and gate commands that resolve, and its agent (Claude Code, or the test-only stub) can run.

## Build from spec

- GET /api/areas answers the spec's areas: its rules grouped by their top-level requirements section, in document order, each with `id`, `title`, `rules`, `scenarios` (the scenarios linked to those rules), `failing` (null until a check of the target branch ran) and `status` (`not_built`, `partial`, `built` or `building`), then the area `all`; ?branch=B answers them for target branch B.
- POST /api/rebuild {"scope": {"area": id} | {"rules": [ids]} | "all"} needs no accepted change: Studio first runs the gates once (the baseline), and the scope's targets are the baseline's failing checks linked to the scope's rules; the record carries `scope`, `baseline` (its findings, each with its rules and whether it is in scope) and the targets.
- A scoped round passes when every target of the scope passes and no check that passed in the baseline fails (a regression), decided by Studio from the gate findings; checks outside the scope that failed in the baseline may stay failing. Each round records `scope_passing`, `scope_total`, `regressions` and `newly_fixed`.
- Scoped builds land on a target branch, by default `build/<project>`, created from HEAD on first use: each build starts from the target's tip and, when it passes, the target is fast-forwarded to its commit, so areas accumulate; the owner's branch and checkout never move, and GET /api/areas reports the target's areas from the build's results.
- A scope with nothing failing after the baseline answers 409 ("nothing to build: the checks in this scope already pass") and leaves no rebuild record; an unknown area or rule answers 404 ("no area <id>", "no rule <id>"), a malformed scope 422, and a `branch` without a scope 422 ("branch needs a scope").
- GET /api/branches answers the repository's local branches (`name`, `head`, `current`, `protected`, `ahead_of_base`, `is_integration`) with `current_branch`, `protected` and `default_target`.
- POST /api/rebuild with "branch": {"name", "create", "from"} targets that branch: an unknown branch answers 404 (code `no_branch`), an existing one with create 409 (`branch_exists`, nothing overwritten), an unknown `from` 404 (`no_ref`), an invalid name 422 (`branch_name_invalid`); a protected branch (409 `branch_protected`) or the branch checked out in the owner's worktree (409 `branch_checked_out`) is refused unless the request confirms it, and even then a worktree with changes answers 409 (`tree_dirty`).
- GET /api/projects reports `capabilities.build_from_spec`, true when the project can rebuild.

## Build plans

- POST /api/plan {"scope": "all" | {"areas": [ids]} | {"rules": [ids]}} answers 200 with the plan's id (`p1`, then `p2`, ... in order) and status `planning`, and plans in the background; GET /api/plan/{id}?wait=S (S from 0 to 60 seconds) answers the plan once it is neither planning nor running, or after S seconds, and GET /api/plans lists the plans. A ready plan has its `source` (`agent`, or `fallback` with a `fallback_reason`), `version`, `target_branch`, `max_parallel` (default 2), its nodes -- each with `kind` (scaffold, area, merge or integration), `scope`, `depends_on`, `tier`, the tier's `model`, `touches` (the tables, packages and files it writes), the `gate` Studio sets and its `level` -- and `levels`, the node ids of each level.
- Studio validates every plan: two nodes on one level never write the same table, package or file -- the later node (in the plan's order) gets a dependency on the earlier one, `serialised_because` names the earlier node and the overlap, and `validation.auto_edits` records it -- and a level holds at most `max_parallel` nodes (the others move down a level).
- A plan whose rules are not each covered by exactly one node (or that has a cycle or a malformed node) goes back to the planner once; if it is still invalid, the plan is the fallback -- the areas in document order, each depending on the one before, then an integration node -- with `source` `fallback` and a `fallback_reason` naming the issues. `"planner": "fallback"` asks for the fallback directly.
- A plan runs only once approved: POST /api/plan/{id}/run on a plan that is not approved answers 409 ("plan <id> is ready, not approved: approve it first" for a ready plan); POST /api/plan/{id}/approve on a ready plan answers its status `approved`; POST /api/plan/{id}/revise with edits re-validates the plan, raises its `version` and clears the approval.
- A running plan builds level by level: the nodes of a level whose dependencies all passed start from the target branch's tip at the start of the level; a node with no failing check in its scope passes with `nothing_to_build`; at the end of the level the passed nodes' branches are merged into that tip in the plan's node order and the full gate runs on the merged tip, and the target branch (by default `build/<project>`) moves to the merged tip only when that gate shows no regression. The owner's branch and checkout never move. A plan whose nodes all passed ends `passed`.
- When a level's gate on the merged tip still fails after `[rebuild] level_repair_attempts` repair attempts, or fails with the repair loop off (0), the level fails: its merged nodes become `regressed`, the level's record names the regressions and the nodes blamed, the target branch stays at its last good tip, and the plan ends `partial` with `resume.kind` `repair` (another bounded repair pass on run), or with the loop off `failed` (or `partial` once an earlier level landed).
- A node whose build gives up ends `gave_up`; the nodes that depend on it are `blocked` (with `blocked_by`) and never run, while its siblings run and land; the plan ends `partial`.
- DELETE /api/plan/{id} stops a planning or running plan: it answers {"id": ..., "status": "stopped"} and its running node builds are stopped; on any other plan it answers 409 ("cannot stop a plan that is <status>"). While a plan runs, POST /api/rebuild answers 409 ("a build plan is running in this project (<id>)").
- An unknown plan answers 404 ("no plan <id>"); when a plan is created, a malformed scope answers 422, an unknown area or rule 404 ("no area <id>", "no rule <id>"), a `max_parallel` outside 1 to 8 422, and a target branch is refused with the codes of scoped builds.

## Durable state and resume

- GET /api/plans and GET /api/plan/{id} answer every plan created since the project's state began, each with `interrupted` (false unless a restart cut it off), `attempts` (its runs so far) and `resume` (`available`, `kind`, `from_level`, `will_skip`, `will_run`, `note`); a plan that has not run has nothing to resume.
- POST /api/plan/{id}/land answers 409 with the code `nothing_to_land` ("nothing is waiting to land in plan <id>") when no level of the plan passed its full gate without landing, and 404 ("no plan <id>") for an unknown plan.
- DELETE /api/plan/{id}?forget=true drops a plan that is not running and answers {"id", "status": "forgotten", "refs_removed"}, the git anchors it removed (`refs/lattice/plans/<id>/L<n>` for each gated level tip, `refs/lattice/plans/<id>/<node>` for each node commit); the plan then answers 404, is no longer listed, and its id is never handed out again. A `forget` other than true or false answers 422, and DELETE without `forget` keeps stopping a plan as before (409 on a finished one).
- A plan that ended `partial` because a node gave up offers a retry (`resume.kind` `retry`, the passed nodes in `will_skip`): POST /api/plan/{id}/run runs the nodes that did not pass again without running the passed ones, each start counting in the node's `attempts`; once a node has had `[rebuild] max_attempts` (3) attempts, `resume.available` is false and a further run answers 409 naming the cap.
- A stopped plan offers to continue: `resume.kind` `continue` from the level it stopped at.
- Plans, build records and change sets are kept on disk in the project's state directory (git-ignored) and survive a restart of Studio: a plan, build or change that was running when Studio stopped is `interrupted` after the restart (its orphan processes terminated), resumes from where it stopped without running passed nodes again, and a level that passed its full gate but could not land is landed by POST /api/plan/{id}/land without running any agent.

## Build performance

- GET /api/projects reports each project's `plan_defaults` from its `[rebuild]` settings: `max_parallel`, `models`, `scoped_gate` (the scoped gate's arguments, or null), `level_gate` (`full` or `regression`), `node_timeout` and `level_repair_attempts`.
- With `[rebuild] scoped_gate_args`, a plan node other than the integration node runs its gate as the first gate command with those arguments and `--only-file` naming its selection: the scenarios of its scope plus the scenarios that passed at the start of its level (the regression set); its build record carries `scoped_gate` with `args`, `in_scope`, `regression_set` and the number of `scenarios`.
- With `[rebuild] level_gate = "regression"` a level ends with the first gate on the scenarios that passed before the level and those of every scope landed so far, with the full layers, and a scenario it does not select keeps its failing verdict; the last level is decided by the full gate (`final_gate`); each level run's gate says its `mode` and how many scenarios it `selected`, and a plan whose nodes pass ends `passed` as before.
- When a level's gate fails on the merged tip -- a check that passed before the level, or a node's own target that passed in its node gate -- the plan goes on: each failing check is attributed to the node whose scope links it (`attributed`), those nodes get a repair round with the level gate's findings, verified by their own gate, then the level merges again and its gate runs again; a level that passes after a repair lands, with `repairs` (`attempt`, `of`, `tip`, `targets`, `attributed`, `nodes`, `outcome`) on its level run, `repairs` on each repaired node and its build, and `repair_attempts` on the plan.
- GET /api/areas reports a target branch's areas from the gate results recorded for its tip, so the areas of landed levels are `built`.
- Stopping a plan or a build, or a gate that times out, ends the gate's whole process tree (the apps and services its check started included) and removes the database containers its check started and no other; an agent stopped by `node_timeout` or `node_max_tokens` keeps the usage it had spent in its round, which is marked `timeout`.

## Spec sync

- GET /api/branches gives every local branch that is neither protected nor the current one a `spec`: `in_sync`, `files` (the spec files -- REQUIREMENTS.md, spec.py, spec.trace.json and the `[mcp] protected` files -- whose content committed on the branch differs from the content committed at HEAD; uncommitted edits never count) and `head` (HEAD's commit); the current branch and protected branches have `spec` null.
- POST /api/branches/{name}/sync-spec makes one commit on the branch, `spec: sync from <current branch> <sha7>` with the trailer `Lattice-Spec-Sync: <HEAD's commit>`, holding exactly the files that differ as committed at HEAD and nothing else, and moves the branch to it only if the branch has not moved meanwhile; it answers `branch`, `synced_from` (HEAD's commit), `files`, `commit` and `previous_tip`. The owner's branch, checkout and working copy never change, and the branch's spec is then in sync.
- A sync is refused, changing nothing, with 409 and the code `branch_protected` for a protected branch, `branch_checked_out` for a branch checked out in any worktree, `build_running` while a build or a plan runs, and `nothing_to_sync` when the spec is the same; with 404 `no_branch` for an unknown branch, 422 `branch_name_invalid` for a malformed name, and 422 for an unknown field in the body.
- A plan carries `spec_drift` -- `head` and `files` when the spec committed on its target branch differs from HEAD's, else null -- computed when the plan is created and again whenever it is read while not running, and while it differs the plan's warnings say "The spec on <current branch> differs from <target> in <files>: sync it first".
- With `[rebuild] sync_spec = "auto"`, POST /api/plan/{id}/run first syncs the spec onto the plan's target branch when it differs (a refusal is the run's 409), before the plan's start baseline, which then runs on the sync commit, and the plan's timeline records "spec synced from <branch> at <sha7> (<files>)"; with the default `"off"` a run syncs nothing.
- The Build view and the plan view show a quiet banner while the chosen target's spec, or a plan's target's, is behind ("The spec on <branch> is newer than <target> (<files>).") with one primary button, Sync spec, that syncs it and reloads; a refusal is shown inline, and the banner is hidden while a plan runs.

## Connected tools (MCP)

- GET /api/mcp answers `servers` (each with `name`, `status`, `source`, `tools` sorted into `read`, `write` and `unknown`, `allowed_in` per chat mode and `writes_allowed`), `refreshed_at` and `allowed_in` (how many external tools ask, explain and change may run); with the stub LLM it answers no servers, `refreshed_at` null, zero counts and a `note`, and starts no process, `refresh=1` included; a `refresh` other than 0, 1, true or false answers 422.
- There is no write route for connected servers: POST /api/mcp answers 404.
- POST /api/changes takes an optional `source` (`server` and `item`, optional `url` and `title`): the change record carries it (`source` null when none), GET /api/model lists it on the change, and a follow-up that gives none keeps its parent's.
- An invalid `source` (not an object, a missing or empty `server` or `item`, a field that is not a string or is longer than its cap, a `url` that is not http or https, a control character, an unknown field) answers 422 and creates no change.
- A chat session (ask, explain, change) sees the claude.ai connectors of the owner's account besides `lattice`, but not the owner's plugins, skills, hooks or plugin servers; only the read tools of connected servers may run, write and unknown tools are never offered unless `[mcp] allow_writes` lists the server (change mode only), and the rebuild agent and the planner see no external server.
- The conversation panel has a quiet Connected tools list (each server's state and how many read tools each mode may use, with a Refresh), and a change card shows the external item its request came from, with its link.

## Database engines and modules

- GET /api/engines answers `engines` (each with `name`, `source` -- `builtin`, `project` or `module` --, `used`, `server` with its `image` or null, `adapter` and `requires`): SQLite and the built-in `postgres`, `mysql` and `clickhouse` first, then the engines of the project's `[engines] paths` and of its installed modules; `modules` (each with `name`, `version`, `sha256` and `status`) and `errors`. A project with no engines or modules of its own lists the built-ins, SQLite `used` (the default database), and no modules or errors.
- There is no write route for engines: POST /api/engines and DELETE /api/engines answer 404.
- The project's engine descriptors and installed modules are registered for its checks; every read re-hashes the modules, and one whose installed tree no longer matches its lock hash is listed `changed`, its engine is not registered and an error names it.

## Improvement proposals

- GET /api/improve answers `proposals` (each with `id`, `title`, `signal_key`, `evidence`, `score`, `unit`, `score_class`, `frequency`, `cost_per`, `kind`, `target_paths`, `why` and `status`), `events` (how many events the project's log holds), `method` and `blind_spots`; a project whose log is empty has no proposals.
- There is no write route for proposals: POST /api/improve answers 404.
- The owner's change decisions are logged: accepting or rejecting a change adds a `human` event and a `change` event to the project's log; GET /api/improve then counts them, proposes nothing for an accepted change, and proposes the rejected change sets as one cluster with no recorded cost (`change:rejected`, class C).
- Every build and plan logs its improvement signals without blocking: gate runs with `layer_seconds` copied from the check's JSON timings, a `cause` on every give-up, stop and failed node, the owner's plan actions as timeline entries with `actor: "owner"`, the ledger line's own wall time, and a `signal_key` on every finding.
- Proposals are ranked by frequency x cost, seconds before list-price dollars and never converted, with no model; items closed in the improvement ledger are left out; a proposal that touches the checker or the spec language is `needs_owner` and never drafted.

## Workspace

- GET /api/workspace answers the workspace manifest Studio found when it started (`--workspace`, else `LATTICE_WORKSPACE`, else the first `lattice.workspace.toml` walking up from the directory it started in): `workspace` (`name` and `path`), `projects` (each with `name`, `path`, `role`, `in_hub`, `hub_name` and `spec`), `links` (each with `from`, `to`, `kind` and `note`) and `problems` (each with `code`, `severity` and `message`); it answers the same under `/p/<name>/api/workspace`.
- A workspace project is `in_hub` when Studio serves its directory, and its `hub_name` is the name Studio serves it under; a project Studio does not serve has `in_hub` false and `hub_name` null; `spec` says whether the project's directory holds spec.py and spec.trace.json.
- There is no write route for the workspace: POST /api/workspace answers 404, and reading it changes nothing; a declared project whose directory does not exist is a `missing_path` warning and the workspace still answers.
- With no manifest, GET /api/workspace answers `workspace` null and empty `projects`, `links` and `problems`; a manifest with an unknown link end, a duplicate project name, a self link or a cycle of `depends_on` links answers its projects and links with each of those as an error in `problems`.
- GET /api/contracts answers the static contract check of the workspace manifest's links that carry a `contract` key, the same JSON as `lattice contracts check --static-only --json`: `workspace` (the manifest's name), `consumer`, `checked`, `links` and `problems`; `?consumer=NAME` limits it to that project, a name the manifest does not declare answers an `unknown_project` error in `problems` and no links, and it answers the same under `/p/<name>/api/contracts`; it is read-only: POST /api/contracts answers 404 and nothing changes.
- GET /api/contracts/impact?provider=NAME&candidate=REF answers the advisory, read-only preview of a candidate version of a workspace provider against the pins of the consumers that rely on it, the same JSON as `lattice contracts impact NAME --candidate REF --json`: `workspace`, `provider`, `candidate`, `checked`, `consumers`, `counts`, `problems` and `exit_code`; a provider that no link with a `contract` key points to answers `checked` 0 and the `no_consumers` warning with `exit_code` 0, a provider the manifest does not declare answers an `unknown_project` error in `problems`, an empty `provider` or `candidate` a `missing_argument` error, both with `exit_code` 2 and no consumers, and it answers the same under `/p/<name>/api/contracts/impact`; it is read-only: POST /api/contracts/impact answers 404 and nothing changes.
- GET /api/linked-questions answers the project's own linked questions, the advisory notes that other workspace projects left in its state directory when their accepted contract broke its pins: `format`, `consumer` (the project's name), `state` (`present`), `state_dir`, `questions` in id order, `counts` (`open`, `resolved`, `dismissed`, `superseded`) and `problems`; with no note left it answers no questions and every count 0, `?status=` limits it and a value other than open, resolved, dismissed or all answers 422 with the code `bad_status`, GET /api/linked-questions/{id} of an id that does not exist answers 404 with the code `unknown_question` and so do POST /api/linked-questions/{id}/resolve and /dismiss, and it answers the same under `/p/<name>/api/linked-questions`; nothing changes.
- GET /api/linked-notifications answers `notifications`, this project's accept outcomes since the server started (newest first, at most 20; each says which consumers were left a note, or why none was), empty before any accept; it is read-only: POST /api/linked-notifications answers 404 and nothing changes.

## Chat sessions

- POST /api/session with `mode` ask, explain or change answers 201 with an idle session (`turns` 0, `claude_session_id` and `resume_command` null) and starts no process; `rebuild` answers 501, any other mode and a `change` that is not a change id answer 422; GET /api/session lists the sessions in the order they were created; a message with no text answers 422 before the session is looked up, a message to, the events of or a DELETE of a session that does not exist answer 404, and an `after` that is not an integer >= 0 answers 422.

## Improvement drafts and applies

- GET /api/improve/{id}/draft answers `lattice improve draft ID --json` for this project with HTTP 200, a refusal included (`refused` true with a `reason` and a `detail`: `unknown_id` for an id that is not in the log), and a rejected change set drafts as `template` `prereg` with `toml` null; GET /api/improve/{id}/verify answers `lattice improve verify ID --json` the same way, with `?at=TS` passed through (`not_closed` is refused for an open proposal, with or without `at`); both answer the same under `/p/<name>/api/improve/...`, POST to either answers 404, and nothing is written: GET /api/improve still counts the same events.
- GET /api/improve/applies answers `format` 1, `enabled`, `why_disabled`, `applies`, `running` and `problems` (no apply yet: `applies` empty, `running` null, and `enabled` true when the server was started with `--allow-apply`); POST /api/improve/{id}/apply with a `draft_sha256` that is not the sha256 of the current draft's text answers 409 with the code `draft_changed` and the current `draft_sha256` (null when the draft is refused), and a body without a 64 hex digit `draft_sha256` answers 422 with the code `bad_body`; POST /api/improve/{id}/discard of an id that was never applied answers 200 with `exit_code` 3 and the result's `reason` `not_applied`, GET /api/improve/{id}/apply of it answers 404 with the code `not_applied`, POST /api/improve/applies answers 404, the same lists answer the same under `/p/<name>/api/improve/applies`, and none of these writes anything or logs an event.
- An `{id}` of the improve routes that does not match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` after unquoting answers 422 with the code `bad_id` on draft, verify and apply; a `gates` that is not a boolean and a `branch` that is neither null nor a string of 1 to 200 characters answer 422 with the code `bad_body`; a `wait` of GET /api/improve/{id}/apply outside 0 to 60 answers 422; nothing changes.
- POST /api/improve/{id}/apply and /discard are off unless the server was started with `--allow-apply` (403 `apply_disabled`; `enabled` false in GET /api/improve/applies), and then refuse in this order: a `Host` other than 127.0.0.1:port or localhost:port, or an `Origin` that is not that host (403 `bad_origin`), a `Content-Type` other than application/json (415 `json_required`), a body that is not a JSON object (400), a bad id (422 `bad_id`), a bad body (422 `bad_body`, apply only), a project with no durable state (409 `no_state`), a second apply or discard while one runs (409 `apply_running`, one per server) and, for apply, a draft that is not the one the owner read (409 `draft_changed`); an accepted apply answers 202 with a running job, commits the draft's one line on a branch of its own in a worktree beside the checkout, runs the gates there, and never merges, pushes, closes the ledger or logs an event.
- The web UI has an Improve view (the proposals, the applies, Draft, Verify, and, for a config draft with apply enabled and no live record, "Apply on a branch..." behind a confirmation, then the record with a NOT MERGED banner, the six commands to copy and "Discard...") and, when the workspace manifest names one, a Contracts view (the links, "Check contracts" and "Preview impact" only on demand, the linked questions with Resolve and Dismiss with a note, and the deliveries); every call goes through api.js, and no route of the UI merges, pushes or closes a ledger entry.

## App map and impact

- GET /api/appmap answers 200 with `lattice.appmap/1`; on a project whose studio.toml has no `[implementation]` table its `state` is `not_configured` and it has no other key than `schema`, `state`, `inputs` and `why`, with or without `?section=`, it answers the same under `/p/<name>/api/appmap`, and it runs and writes nothing.
- A `section` of GET /api/appmap that is not summary, rules, workflows, machines, access, endpoints or unread answers 422 with the code `bad_section`; GET /api/appmap/element without a `key`, or with one over 300 characters, answers 422 with the code `bad_key`, and on a map that is not `ok` every key is unknown: 404 with the code `unknown_key` and an empty `nearest`.
- GET /api/impact answers 422 with the code `bad_change` unless `change` is `c` and one to six digits, and 422 with the code `bad_section` for a `section` that is not summary, tier1, tier2, tier3, flows, unread or decidability; on a project whose studio.toml has no `[impact]` table a valid id answers 200 with `lattice.studio-impact/1`, `state` `impact_not_configured` and `report` null, and runs nothing.
- There is no write route for the app map or the impact: POST and DELETE on /api/appmap, /api/appmap/element and /api/impact answer 404 `no such route`, and the model, the files and the state stay as they were.
- The three routes answer under a project prefix like every other (`/p/<name>/api/appmap`, `/p/<name>/api/appmap/element`, `/p/<name>/api/impact`), and an unknown project answers 404 `no such project` on each.
