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
- When the full gate after a level fails a check that passed before the level (a regression), the level fails: its merged nodes become `regressed`, the level's record names the regressions and the nodes blamed, the plan ends `failed` (or `partial` once an earlier level landed), and the target branch stays at its last good tip.
- A node whose build gives up ends `gave_up`; the nodes that depend on it are `blocked` (with `blocked_by`) and never run, while its siblings run and land; the plan ends `partial`.
- DELETE /api/plan/{id} stops a planning or running plan: it answers {"id": ..., "status": "stopped"} and its running node builds are stopped; on any other plan it answers 409 ("cannot stop a plan that is <status>"). While a plan runs, POST /api/rebuild answers 409 ("a build plan is running in this project (<id>)").
- An unknown plan answers 404 ("no plan <id>"); when a plan is created, a malformed scope answers 422, an unknown area or rule 404 ("no area <id>", "no rule <id>"), a `max_parallel` outside 1 to 8 422, and a target branch is refused with the codes of scoped builds.
