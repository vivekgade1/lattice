# Implementation diff and chat grounded in both models (Spike C): pre-registration

## 1. Status, and the exact gap

2026-10-15, branch `np-impldiff` (from `np-workflow` 454a8e6: Spike A built, Spike B pre-registered and being built in
parallel). **Written BEFORE any code.** Recon: code reading; Streak a0679e9 (**S**) and 1d00ede (**S4**) archived to
scratch; Spike A's `gocheck extract` run on both; a throwaway scratch script applying D4/D5 below to each spec + trace +
extract. No model. **(m)** = measured. **(r)** = recon-measured by that script: these are the blind expectations, and
the build's own count is the gated figure. Everything else is a **prediction (p)**. Results go in
`2026-10-15-implementation-diff-results.md`, with data in `docs/results/data/2026-10-15-implementation-diff/`.

**The gap, with code evidence (m).**
- `ask.build_digest` (`lattice/spec/ask.py:186`) holds only spec elements. `check_citation` (`:312`) rejects any target
  missing from the digest, so a `code:` citation always fails. Kinds are `yes|no|value|not_specified` (`:90`).
  `verify` (`:1351`) has the model write a scenario and never replays one.
- `code_links` (`lattice/mcp/tools.py:460`) is trace + grep and says its results are "leads, not proof".
- `ReviewData` rule statuses (`review.py:55`) are spec-side only. `not_built_yet` (`incremental.py:599-644`) labels a
  check target that fails after a change; it is not a static status.
- Nothing maps code back to rules (no reverse index).
- `POST /api/explain` and `/api/code/*` with `code:<path>:<line>` exist only in the mock and in API.md v1.3.
- Extracted facts have no store: Spike A prints to stdout, Spike B writes to `--out DIR`.

**The code contradicts six premises (m/r).**
1. **The spec-only case is S4, not S.** S registers 26 routes and implements every spec endpoint (Spike A). The spec
   names 26 endpoints: 25 reached by rules, plus `PUT /test/clock`, which 5 of 156 scenarios use. S4 has 20 routes and
   6 spec-only endpoints: `GET /v1/home`, `GET /v1/sync`, `GET /v1/nudges`, `POST /v1/nudges/{}/dismiss`,
   `POST /v1/devices` and `DELETE /v1/devices/{}`.
2. **S has no natural "implemented-not-required" case (r).** Every route but `PUT /test/clock` (registered under
   `if d.Config.TestClock`, `router.go:49-51`) is reached by at least one of the 148 rules, `GET /health` included
   (1 rule). A planted variant is needed (PO).
3. **`mcp/tools.norm_route` does not template UUID segments.** Streak's scenario paths are UUID literals, so reusing it
   gives 30 bogus spec-only keys (m). D3 uses `review._normalize_path` instead.
4. **New answer `kind`s would break current consumers.** The UI shows an unknown kind with the "Value" badge
   (`web/js/badges.js:80-89`), and tests and the dogfood spec pin the four kinds. So `kind` stays as it is and an
   orthogonal `implementation` field is added (D9). This replaces the plan's "answer kinds gain
   implemented/not_implemented".
5. **The default state directory is inside the target repo** (`<repo>/.lattice/state/<name>/`), and its first write
   edits `.git/info/exclude` (`studio/state.py`). Caching the inventory there would write into the target, so D1 uses
   a directory outside it.
6. **S's committed `studio.toml` points at the owner's checkout** (`[check] go_src =
   "/Users/vivekgade/Documents/streak"`). Probes rewrite it to the clone before any snapshot and record that they did.

**Sizes (m/r).**
- **Spec of S:** 148 rules (141 checked, 5 unchecked, 2 context) and 156 scenarios. Links: 259 scenario, 19 invariant,
  18 profile, 2 machine (R123, R127), 1 service. 30 rules name a route in their text.
- **Digest:** ~130k tokens (`approx_tokens`); the cached core is 18.8k, of which the rule section is 14.4k and the
  cells 0.9k.
- **Code:** 42 `.go` files, 3,970 lines outside tests. The extract has 18 `status_writes`, 4 `unread` and 2 `excluded`
  (gin NoRoute/NoMethod).

**Rulings requested.**
- **R1:** no Studio route and no screen (D11). API.md v1.22 documents only the optional `/api/ask` keys.
- **R2:** add `implementation` as an answer field; `kind` is unchanged.
- **R3:** the inventory lives in a directory outside the target, named in `studio.toml [implementation]`, never in the
  state directory.
- **R4:** a planted variant of S for orphans (PO).
- **R5:** held-out scoring as in A and B.
- **R6:** a D9-conformant fixture `workflows.json` until Spike B merges.

## 2. Decisions (fixed before coding)

**D1 Inventory: capture, location, staleness.** Only one command writes:
`lattice import inventory <app_root> --out DIR [--package .] [--workflows FILE]`. It writes:
- `DIR/extract.json`: Spike A's JSON, canonical (sorted keys, ending in `\n`);
- `DIR/workflows.json`: a copy of `--workflows FILE` after validation;
- `DIR/inventory.json`: `schema: "lattice.implementation-inventory/1"`, `frontend`, `extract_sha256`,
  `workflows_sha256|null` and `source {sha256, files[{path, sha256}]}`. The source set is every `*.go`, `go.mod` and
  `go.sum` under the app root, minus `_SKIP_DIRS`, `testdata` and dot-directories. No absolute path, time or git
  call.

`--out` under `app_root` is refused (exit 2), and so is a workflows file whose schema is not `lattice.workflows/1`
(`bad_workflows`).

The read path finds the inventory through `studio.toml [implementation] inventory = "PATH"` (relative to the project
or absolute), with `app_root = "."` and `package = "."` as defaults, or through `--inventory DIR [--app-root DIR]`. It
never creates, caches or refreshes anything, runs no extractor and no `go`, and starts no subprocess (only `verify`
does, D9). Every read re-hashes the source set:
- **Mismatch:** the result says `stale: {changed (<=20, sorted), added, removed}`, every verified element reads as
  `implemented_unverified` (reason `stale`), and the other facts show labelled stale.
- **Missing or invalid directory:** `inventory_missing` or `bad_inventory`, never a guess.

**D2 The Spike B contract and what changes if B's built schema differs.** Read from `lattice.workflows/1` (B's D9):
- `flows[].{id, name, inferred_name, kind, routes, skeleton, status, source, reason, machine_refs}` and
  `flows[].steps[].{method, route, path, caller, json, expect_status, role, evidence[].at}`;
- `machines[].{id, table, column, states, edges[].{from, to, source, evidence}, status, spec_object}`;
- `run.mode`, `app.extract_sha256`.

**Consistency:** `app.extract_sha256` must equal the sha256 of `extract.json`, else `workflows_mismatch` (flows shown,
never verified).

**If B's schema differs**, one adapter, `_read_workflows()`, absorbs it, with renames recorded by amendment. Nothing
else reads the raw file. Specific fallbacks:
- `extract_sha256` hashes something else: compare `app.module` + the route set `(method, path, file, line)`, and say so.
- `steps[].route` missing: use `skeleton`.
- No `spec_object`: match (SQL table, column).
- New `status` values: anything not `verified` counts as not verified.

**Until B merges**, the build uses `tests/spec/fixtures/implementation/workflows.json` (written to B's D9), and PV, PF
and PQ's dynamic half are NOT RUN. The results name the rows that ran.

**D3 Elements and keys.** Four element kinds:
- `impl:route:<METHOD /template>`. The template is the Gin path with `:x`, `*x` and `{x}` segments turned into `{}`,
  then `review._normalize_path` (integers and UUIDs become `{}`), the query string removed and any trailing `/`
  dropped. Spec endpoints are templated the same way.
- `impl:flow:<wf-id>`, `impl:machine:<sql_table>.<column>`, `impl:table:<sql_table>`.

Edge cases:
- Two routes with one key (different parameter names): both kept, `collision: true`.
- A Gin `Any` route matches every method on its path (`via_any`). HEAD and OPTIONS are ignored.
- Extract `excluded` items are not elements.

Evidence: each element carries `code:<path>:<line>` entries: the registration and handler (routes), each step's
`evidence[].at` (flows), each edge's evidence (machines), the struct line (tables).

**D4 Linking a rule to elements, and the reverse index.** A rule's endpoints E(R) are the union of:
- **(a)** the endpoint of each `cell:` link's column;
- **(b)** every request step of each `scenario:` link, templated, minus routes the extract registers under a
  `condition` (so `PUT /test/clock` links no rule);
- **(c)** every `METHOD /path` in the rule's text (`routes_in`, then templated). A method-less path is ignored.

Other links:
- `machine:T.c` links `impl:machine` when `spec_object` equals (T, c), else when (SQL name of T, c) matches.
- `profile:T[.c]`, and the tables named in an `invariant:` expression (parsed by `lattice.spec.invariants`; an
  unparsable expression is listed and links nothing), link `impl:table` (and the column, if given).
- `service:` links nothing (services are not extracted).

Every link records `via` (cell, scenario, text, machine, profile or invariant). Setup steps count: the scenario cannot
pass without them. The last request step of each scenario is marked `focus` for display. The reverse index is
`by_element[key] = sorted rule ids`. **Without a trace** (the held-out spec), the requirements are the spec's cells and
scenarios (`requirement_basis: "spec"`) and there is no rules section.

**D5 Statuses.** Per rule, one of:
- **`no_element`:** nothing linkable. Listed with its spec status; not one of the four statuses.
- **`required_not_implemented`:** some element of E(R), or a linked machine or table, is absent from the extract. It
  carries `missing[]`, `partial` (some elements are present) and `impact_label: "not_built_yet"`. Absence is always
  `basis: "inferred"`.
- **`implemented_verified`:** every element is present, every route element appears in a step of at least one
  `verified` flow (B's replay R passed), and every linked machine is `confirmed`.
- **`implemented_unverified`:** every element is present but not all are verified. Each unverified element gets a
  reason: `no verified flow`, `flow failed`, `static run`, `stale` or `workflows_mismatch`. A rule linking only tables
  is always unverified (schema facts are static).

Every item carries `basis` (`static`, `observed` or `inferred`). Flow names keep `inferred_name: true`. When Studio has
an `app.last_check`, each rule shows `last_check: pass|fail|null`, for information only; it is never merged into
`verified`.

**D6 Implemented-not-required, and exclusions.**
- **Route:** `implemented_not_required` when no rule lists it in `by_element`. `in_spec: true` when a cell or scenario
  names it.
- **Excluded, listed in `excluded[]` with a reason, never hidden:**
  - routes with a `condition` (`test_only`);
  - unlinked routes whose path is exactly `/health`, `/healthz`, `/livez`, `/readyz`, `/ready`, `/ping`, `/metrics`
    or `/version`, or starts with `/debug/pprof/` (`plumbing`, any method). A plumbing path that a rule links stays
    required (S's `/health`).
- **Flow:** `implemented_not_required` when it has a non-excluded orphan route (`orphan_routes[]`). Every flow also
  lists its strict and lenient matches with spec scenario skeletons (B's R4).
- **Machine:** `implemented_not_required` when no `machine:` link reaches it.
- **Table:** shown, never orphaned.

**D7 Report.** `lattice/spec/implementation.py` defines `build_report(spec, trace|None, inventory, last_check=None)`, a
pure function returning `lattice.implementation-report/1`:
- `inventory {extract_sha256, workflows_sha256, run_mode, stale}` and `summary` (counts per status);
- `rules[]` in trace order: `{id, spec_status, status, elements[{key, present, verified, reason, basis, via,
  evidence[]}], missing[], partial}`;
- `routes[]`, `flows[]` and `machines[]`, each with its status, rules and evidence;
- `implemented_not_required[]`, `excluded[]`, `by_element{}` and `unread[]` (from the extract).

Output is sorted, uses relative `/` paths, and contains no time, absolute path or response body. `render()` prints the
same as text, one evidence line per item. CLI: `python -m lattice.spec implementation --spec MOD:SPEC [--trace T]
--inventory DIR [--app-root DIR] [--json] [--rule R] [--status S]`. Exit 0 = report produced, 2 = it could not be.
Writes nothing.

**D8 The `code:` citation checker.** `check_code(app_root, inventory, citation) -> reason|None` is deterministic and
only reads files. The target is `code:<relpath>:<line>` with exactly one trailing `:<int>`. The citation holds iff all
of these hold:
- **Path:** relative and normalised: no `..`, no leading `/`, no backslash, no control character.
- **Containment:** the realpath is under `app_root`, and no path component is a symlink.
- **Case:** each component matches the directory listing case-exactly (APFS is case-insensitive).
- **File:** in the inventory's source set, or a `_CODE_EXT` file outside `_SKIP_DIRS`; at most 1 MB.
- **Line:** `1 <= line <= n`.
- **Quote:** at least 8 non-space characters, and its whitespace-normalised text is a substring of that one line.
- **Symbol** (when given): a whole word on that line, or the inventory's symbol at that file:line.

A citation that holds is `evidence: true` when its file:line is an element's evidence location (D3). The checker reads
the current file, so a stale inventory shows up honestly as rejected citations.

**D9 The ask path** (`ask.py`). All of the following applies only when `inventory=` is passed (in Studio: when
`[implementation]` is configured). **Without an inventory, the prompt, digest, citation results and answer dict are
byte-identical to 454a8e6.**

*Digest.* A new section, `## Implemented app (extracted from the code; static unless marked observed)`, lists:
- route lines: auth, the registration and handler `code:` locations, 2xx statuses, refusal codes, rule count, flow
  statuses;
- machine lines, `impl:rule:<R>` status lines, and a flow index (id, inferred name, kind, status).

The section goes in the core, within `IMPL_CORE_BUDGET = 8000` tokens; over budget, refusal codes are dropped. Full
flows are retrieved per question within `IMPL_BUDGET = 2500`, and the one "need more" round may name `impl:` targets.
`SYSTEM` gets one more paragraph, `SYSTEM_IMPL`, explaining the labels and the `code:` form. Predicted size on S: 5-7k
tokens (p), against the base core's 18.8k.

*Citations.* `code:` citations go through D8. `impl:` targets are checked like other elements: the quote must be a
normalised substring, and a flow's quoted status must equal its status.

*Answer.* Two new optional keys, left out of `to_dict()` when None:
- `implementation`: `implemented_verified | implemented_unverified | required_not_implemented |
  not_specified_not_implemented | ambiguous | unsettled`;
- `implementation_basis`: `observed | static | inferred`.

*Downgrades* (deterministic; a label is never upgraded):
- `implemented_verified` needs a valid `impl:flow` citation of a verified flow (or of a verified `impl:rule`) and a
  valid evidence `code:` citation. Otherwise it becomes `implemented_unverified` if there is one evidence `code:` or
  `impl:route|flow|machine` citation, else `unsettled`.
- `required_not_implemented` needs a valid `rule:`, `cell:` or `scenario:` citation whose endpoints (D4) include an
  endpoint the report marks missing; otherwise `unsettled`.
- `ambiguous` needs valid citations of at least 2 distinct elements; otherwise `unsettled`.
- `not_specified_not_implemented` is accepted, with basis `inferred`.
- A stale inventory turns `*_verified` into `*_unverified`.

The four `kind` values and the existing rule about answers with no valid citation are unchanged.

*Verify.* When an answer cites `impl:flow:<id>`, verify freezes that flow into a `Scenario`: B's helper if B exports
one, else `Step(method, path, user=caller, json, expect_status)` per step and `Await` for await steps. It runs through
`check(layers="D")` with the configured harness and **no model call**: `agrees`, `disagrees` (step, expected vs actual)
or `error`, with `verified.source: "flow:<id>"`. Without a cited flow, the existing verify runs unchanged. The ask CLI
gains `--inventory DIR [--app-root DIR]`.

**D10 MCP tools, sessions, proposals.** `Toolbox` registers three read-only tools (`writes=False`, so they are also
listed under `--readonly`), **only when `studio.toml` has an `[implementation]` table**. They come right after
`change_list`, and each error starts with its code.
- **`implementation_get {section?: summary|rules|routes|flows|machines|orphans|excluded, rule?, element?, status?,
  limit? <= 200}`.** The default `summary` is under 4k characters, capped by `cap_result`. Each finding carries a
  `proposal {request, selected, source: {server: "extractor", item: <finding id, <= 200 chars>, title}}`: text only,
  never sent from here.
- **`workflow_get {id}`.** One flow or machine: steps, status, evidence, rules, spec matches. An unknown id answers
  `unknown_id` with the 5 nearest ids.
- **`workflow_find {question, limit? <= 10}`.** Deterministic. Exact templated `METHOD /path` mentions rank first,
  then IDF-weighted `ask.terms` overlap with each element's document (key, handler, tables, flow name, and the texts
  of the rules that link it). Rules that are required but not implemented are candidates too. It returns
  `candidates[{target, kind, status, score, matched_terms, evidence[]}]` and a `hint`:
  - `exists` or `spec_only`: from the top candidate;
  - `no_match`: the top score is under 0.15 of the maximum;
  - `several`: the top two are distinct elements within 10%.

  The result is labelled "lexical match, not understanding".

**Sessions** (`session.py`):
- `_IMPL_TOOLS` goes into the ask, explain and change allowlists, after `_MULTI_TOOLS` and before `_CHANGE_TOOLS`
  (`test_change_is_ask_plus_the_change_tools` still holds). Rebuild's list is unchanged.
- `_MCP_LABELS` gains 3 labels.
- `_IMPL_RULES` (one sentence) is appended to `system_prompt` only when `[implementation]` resolves.
- Byte-identical: `_BASE_RULES`, the mode prompts and MCP `INSTRUCTIONS`.

**Proposals.** A finding becomes a change only when the owner asks. In change mode the session passes the finding's
`proposal` to `change_propose` (`parse_source` accepts it as-is). Accept stays human-only, in Studio.

**D11 Studio and API.**
- **`GET /api/implementation` is NOT built.** Its only consumer would be a screen, which the owner ruled out. Both MCP
  backends (Local and Http) compute the report from `root` + files. A route with no consumer would be untested
  surface that forces mock parity and a dogfood rule.
- **`server.py`** gains `StudioConfig.implementation` (the `[implementation]` table, default `{}`), `Studio.ask`
  passing the inventory, and docstring lines.
- **API.md moves to v1.22, documentation only:** the `[implementation]` table, the optional `/api/ask` keys, the
  `code:` and `impl:` citation kinds, the three MCP tools. No new route; `GET /api/model` gains no key.
- **Unchanged:** the mock (it has no inventory, so nothing differs), the web UI, and `dogfood/studio` (its project has
  no `[implementation]`: rule count and `check.sh` step count stay the same).

**D12 Never, in the read path and tools.**
- Write any file anywhere (target, spec, inventory, state directory), or create a directory or lock.
- Run the extractor, `go`, git, Docker or the app (only `verify` runs the app, D9).
- Call a model (only `ask` does, as before).
- Propose, accept or change anything.
- Read outside `app_root` or the project.
- Infer a link from names. The plumbing paths are an explicit, displayed list.

## 3. Probes (runner `dogfood/implementation_probes.py`; blind pass rules)

**Setup.** The runner `git archive`s S and S4 into `$SCRATCH`, rewrites `go_src` (premise 6), and captures
inventories outside the clones:
- **S-dyn:** S with B's dynamic `workflows.json`, run once and frozen into data with its sha256;
- **S-stat** and **S4-stat:** with B's `--static` output;
- **S+** (planted): S plus `r.Member.GET("/admin/stats", httpapi.Handle(h.list))` in `internal/habits/routes.go` and
  `r.Public.GET("/metrics", ...)` in `router.go`.

The hand-built truth is the 25 rule-linked spec endpoints (listed in the runner) plus `PUT /test/clock`. Every
deterministic probe runs 10 times, and once more from a second scratch root.

| id | probe | prediction | pass |
|---|---|---|---|
| PE | endpoint statuses vs truth | S: 25 implemented + `PUT /test/clock` test_only; S4: 19 implemented + the 6 spec-only + test_only (r) | exact, S and S4 |
| PR | rule statuses | S: 142 implemented (verified + unverified), 0 required_not_implemented, no_element {R1,R2,R6,R15,R136,R142}, table-only {R35-R38,R40-R42} unverified; S4: required_not_implemented = {R7,R21,R27,R28,R31-R34,R89-R94,R114,R125,R127-R129,R132-R135,R137-R141,R143} (29), 113 implemented, same no_element (r) | exact sets |
| PV | verified flags vs an independent recomputation from the frozen `workflows.json` | S-dyn: routes verified 17-24/25, rules implemented_verified 50-120/142 (p); S-stat, S4-stat: 0 verified; S-dyn after one `.go` edit in a copy: stale with exactly that path, 0 verified | 100% agree; stale exact |
| PO | orphans, exclusions | S: 0 orphans, excluded = test_only `PUT /test/clock` (+2 framework); S+: orphans exactly {`GET /v1/admin/stats`}, `/metrics` plumbing | exact |
| PC | D8 table (>= 20 cases): valid (registration and handler lines, whitespace variant, symbol); invalid (line 0, line n+1, missing file, empty or 5-character quote, a quote from line +1, wrong symbol); forged (`../` escape, absolute path, symlink out of root, case variant, `vendor/` and `.git/` files, `.env`, spec text quoted as code, `a.go:12:3`, NUL in path) | 100% | 100%, 10/10 identical |
| PA | ask with a stub generator. (a) No inventory: digest, prompt and Answer byte-identical to base on S and the `tests/spec` ask fixtures. (b) With inventory: core impl section <= 8000 tokens; 10 scripted replies, one per downgrade rule and citation kind | (a) identical; (b) 5-7k tokens, 10/10 | identical; <= 8000; 10/10 |
| PT | `workflow_find`, 8 fixed queries: check in a habit (S exists); home screen (S exists, S4 spec_only); register a device (S exists, S4 spec_only); password reset (no_match); export CSV (no_match); list (several) | 7/8 (6-8) | >= 6/8, misses listed |
| PF | verify by replay, S-dyn on Docker: answers citing 3 verified flows (habit check-in, device delete, an owner_guard), plus an in-memory flow copy with a wrong `expect_status` | 3/3 agree, 1/1 disagree, 0 generator calls | exact |
| PP | `change_propose` with an S+ orphan's `proposal` (LocalBackend, stub LLM, change-mode Toolbox) | record `source {server: extractor, item}`, not accepted; spec and code byte-identical; only `changes/` grows; no accept tool | all |
| PW | no write: snapshot (sha256, mode, mtime, sorted listing with hidden files) of the clone after the `bin/` build, the spec files and the inventory. In the window: report CLI x10, the 3 tools x10 over stdio `--readonly`, ask (stub) x10, and PF. Clone and inventory `chmod -R a-w` except during PF; capture with `--out` inside the clone | identical; nothing new; capture exit 2 | 0 changed, 0 new |
| PD | report JSON and text, 10 runs x {S-dyn, S-stat, S4-stat, S+} plus a second root; scan for `/Users`, `/private`, ISO times | identical; none | 10/10 + root |
| PB | byte-compat vs `git archive 454a8e6`. Without `[implementation]`, identical: MCP `initialize` instructions (all 6 F1 configs), `tools/list` (single, no-workspace, workspace), `/api/ask` and `/api/model` on the dogfood project (stub, usage masked), `lattice.spec ask --json` (stub), the route table. With it: `tools/list` = base + 3 after `change_list`; the session allowlists = base + 3 (ask/explain/change only). `pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck` 0 failures; `dogfood/studio/check.sh` PASS with the same step count | all | all; pinned-test edits disclosed |
| PX | wall: the report on S; each tool after load; digest overhead | 0.1-0.6 s; < 0.3 s; < 0.2 s | <= 2 s; <= 1 s; <= 0.5 s |

**PQ, the agent exercise.** Model calls go through the `claude` CLI only: `cli_env` strips ANTHROPIC*, and no API key
is used. It reuses the 4c launch:
- argv `--permission-mode dontAsk`; cwd = the clone; an MCP `--readonly` server over it;
- allowed: Read, Grep, Glob, `model_get`, `rule_get`, `code_links`, `graph_get`, `spec_lint`, `implementation_get`,
  `workflow_get`, `workflow_find`;
- disallowed: Bash, Edit, Write, MultiEdit, NotebookEdit, and `spec_ask` (a nested model call is not under the session
  cap).

Prompt (fixed): "In this project, {Q} Answer for the app as it is implemented, not only the spec. Start with exactly
one label: IMPLEMENTED-VERIFIED, IMPLEMENTED-UNVERIFIED, REQUIRED-NOT-IMPLEMENTED, NOT-SPECIFIED-NOT-IMPLEMENTED,
AMBIGUOUS. Cite code as code:<path>:<line>. Do not change anything."

Questions (frozen) and the predicted key. On **S-dyn**:

| q | question | predicted key |
|---|---|---|
| Q1 | "Can a user check in a habit for today?" | verified |
| Q2 | "Can a user unregister a device?" | verified |
| Q3 | "Can a user check in several habits in one request?" | unverified (B predicts K2 failed) |
| Q4 | "Does the app record whether a nudge's push was sent or failed?" | unverified (machine `static`) |
| Q5 | "Can a user reset a forgotten password by email?" | absent |
| Q6 | "Can a user export their habits as a CSV file?" | absent |
| Q7 | "Is there a delete endpoint?" | ambiguous (4 DELETE routes) |
| Q8 | "Is there a list endpoint?" | ambiguous |

On **S4-stat**:

| q | question | predicted key |
|---|---|---|
| Q9 | "Is there a home-screen endpoint?" | required-not-implemented |
| Q10 | "Can the app sync changes since a cursor?" | required-not-implemented |
| Q11 | "Can a user register a device for push notifications?" | required-not-implemented |
| Q12 | = Q1 | unverified (static run) |
| Q13 | "Can a user share a habit with a friend?" | absent |

Sessions: sonnet, one session per question, 13 scored, plus 2 ablation sessions without the three tools (Q3, Q9;
information only). The keys of Q1-Q4 and Q12 are copied from the frozen report into the cases file before hashing. A
key that differs from its predicted category follows the report, and the difference is a finding.

Frozen scorer: `dogfood/implementation_agent{,_scorer,_check}.py` and the cases file. Their hashes, `claude --version`
and the sha256 of `workflows.json` go in Addendum 1 before any session, and the runner refuses to start on a mismatch.
Items are checked against files:
- **B1:** called an implementation tool.
- **B2:** the first-line label equals the key.
- **B3:** every `code:` citation passes D8 on the clone, and an implemented label has at least 1 evidence citation.
- **B4:** every `METHOD /path` named is in the extract or the spec.
- **B5:** REQUIRED-NOT-IMPLEMENTED names at least 1 key rule, and no rule id that does not exist.
- **B6:** no action claim (4c A5 patterns).
- **B7:** the world is byte-identical (PW snapshot).
- **B8:** an ambiguous answer names at least 2 key elements.

**Pass:** at least 11 of 13 scored sessions satisfy every applicable item, and B7 holds in 15 of 15.

**Caps.**
- Per session: USD 0.50 and 8 turns.
- Total: USD 5.00. Abort if the first 4 sessions cost more than USD 1.50.
- Tokens: at most 1.0M, counting input + output + cache creation (cache reads excluded, per 4c Amendment 2), reported
  per session.

A usage-limit error records the remaining sessions as **NOT RUN (usage limit)**: never retried in a loop, never scored
as misses. With n < 13 the pass rule is not evaluated.

**Arm E2** (report only). The same 13 questions through `python -m lattice.spec ask --inventory` (ClaudeCliClient,
sonnet), scored on B2-B5 of the Answer JSON. Cap: USD 2.00 total (`usage.cost_usd`), with the same usage-limit rule.

**Predictions:** 12/13 (11-13); B1 13/13; ablation B2 1/2; USD 0.10-0.30 per session and 1.8-3.5 in total; 4-7 turns
and 30-70k counted tokens per session; E2 B2 9-12/13 for USD 0.6-1.5.

**PH, held out.** The orchestrator runs it once (report only, never tuned). Apps: the six `job-scheduler-go*`. Spec:
job-scheduler ef17cf8 `t5()`, which has no trace (so `requirement_basis: "spec"`). Workflows: B's held-out
`workflows.json` where B's H stage produced one, else static. Order:
1. freeze the sha256 of `implementation.py`, `inventory.py` and the D8 module;
2. score endpoint classification against Spike A's hashed held-out route truth joined with t5's endpoints;
3. run once, plus one repeat for determinism.

Predicted (p): accuracy 0.90-1.00 per app; spec-only 0-2 and implemented-not-specified (after exclusions) 0-3 per app;
0/6 crashes; 2/2 identical runs. The build node never reads, lists or runs anything under `job-scheduler-go*`.

## 4. Mutants (`dogfood/implementation_mutants.py` on a scratch copy; control passes first; predicted 16/16 killed)

| id | mutant | killed by |
|---|---|---|
| IM1 | `norm_route` (no UUID templating) for scenario steps | PE (S spec-only != []) |
| IM2 | conditional routes not excluded from scenario links | PO (`/test/clock` not test_only) |
| IM3 | rule implemented when ANY element is present | PR (S4 set < 29) |
| IM4 | rule-text routes ignored | PR (R140 leaves S4's set) |
| IM5 | any flow status counts as verified | PV |
| IM6 | stale check off | PV (stale row) |
| IM7 | plumbing list applied to rule-linked routes | PE (`/health`), PO |
| IM8 | quote matched anywhere in the file | PC (line +1) |
| IM9 | no root-containment or symlink check | PC (forged rows) |
| IM10 | model's implementation label trusted | PA (b) |
| IM11 | inventory section or SYSTEM_IMPL without an inventory | PA (a), PB |
| IM12 | the 3 tools listed without `[implementation]` | PB |
| IM13 | read path caches the report under the inventory or project | PW |
| IM14 | set-order iteration or an absolute path in the report | PD |
| IM15 | `workflow_find` ranks spec-only rules as `exists` | PT (S4 rows) |
| IM16 | verify writes a model scenario when a flow is cited | PF (generator calls > 0) |

## 5. Risks, and what this cannot show

1. **Linking is trace and text, not semantics.** A requirement that no scenario or text reaches is invisible. Counting
   setup steps makes "required" broad: 29 rules on S4, many blocked only by a setup step (`partial` and `focus`
   shown).
2. **"Verified" means B's replay**: those exact requests got those statuses on a fresh database. It does not mean the
   spec's scenarios pass (that is the existing check, shown beside it), and says nothing about bodies. Coverage is B's.
3. **Absence is inferred from a static extract.** An idiom the extractor cannot read (Spike A's `unread`, which is
   shown) can produce a false `required_not_implemented`.
4. **Streak has no natural orphan.** The planted S+ is written by the rule's author (circular), and plumbing is a
   fixed name list.
5. **The build depends on B.** Until B merges, PV, PF and PQ's dynamic half wait, and schema drift goes through the D2
   adapter by amendment.
6. **PQ is small:** n = 13 (+2), one model, one author of questions, keys, scorer and tools. 11/13 vs 13/13 is noise,
   and the lexical `workflow_find` misses synonyms that no linked rule text carries.
7. **Cost:** the 8k-token core addition is paid on every cache miss, and per-session cost may exceed the prediction
   (as in 4c); the caps are the guard.
8. **Not shown:** that owners act on findings; other frameworks; a rule-level diff for job-scheduler (no trace);
   concurrency; whether replays stay stable across days without a test clock.

**Builder bias:** the builder sees these predictions and the truth. Only PH is blind.

## 6. Blind vs post hoc, and commands

Fixed now. A failed prediction is a **finding**, never silently fixed. Fixes go in separate post-hoc commits, with the
probes re-run and the deltas reported. The scorer is frozen by hash before any session. A ruling that changes D1-D12
is recorded by amendment before code. PH is never re-scored as blind.

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-impldiff PYTHONDONTWRITEBYTECODE=1
SCRATCH=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/impldiff
cd /Users/vivekgade/Documents/lattice-impldiff
git -C /Users/vivekgade/Documents/streak status --short                       # before and after: unchanged
git archive 454a8e6 | (mkdir -p "$SCRATCH/base" && tar -x -C "$SCRATCH/base")   # PB old code
uv run python dogfood/implementation_probes.py --streak-ref a0679e9 --streak-ref2 1d00ede --runs 10 \
    --workflows-dyn "$SCRATCH/wf/S-dyn.json" --scratch "$SCRATCH/p" --out "$SCRATCH/p-data"   # PE..PX
uv run python dogfood/implementation_mutants.py $(seq -f IM%g 1 16)          # control passes; 16 killed
uv run pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck -q
dogfood/studio/check.sh                                                      # PASS, same step count
uv run python dogfood/implementation_agent_check.py                          # scorer cases, then Addendum 1
shasum -a 256 dogfood/implementation_agent*.py dogfood/implementation_agent_scorer_cases.json "$SCRATCH/wf/S-dyn.json"
uv run python dogfood/implementation_agent.py --scratch "$SCRATCH/a" --out docs/results/data/2026-10-15-implementation-diff/agent
shasum -a 256 lattice/spec/implementation.py lattice/importer/inventory.py   # freeze, BEFORE PH
uv run python dogfood/implementation_probes.py --held-out --scratch "$SCRATCH/h" --out "$SCRATCH/h-data"  # orchestrator, once
uv run ruff check <changed .py>; uv run ruff format --check <created .py>
```

**Pass:** every gated row of section 3, 16/16 mutants, and PB. PQ passes by its own rule or is NOT RUN on a usage
limit. PH is reported against its ranges.

## 7. File ownership (build node)

**Creates:**
- `lattice/spec/implementation.py` (load, staleness, D3-D7, the D8 checker, report, CLI);
- `lattice/importer/inventory.py` (capture);
- tests: `tests/spec/test_implementation.py`, `tests/spec/test_code_citation.py`, `tests/spec/test_ask_implementation.py`,
  `tests/mcp/test_implementation_tools.py`, `tests/importer/test_inventory.py`;
- `tests/spec/fixtures/implementation/`: a tiny Gin tree, its extract, a `workflows.json` per D9, a symlink escape;
- `dogfood/implementation_probes.py`, `dogfood/implementation_mutants.py`;
- `dogfood/implementation_agent{,_scorer,_check}.py` and `dogfood/implementation_agent_scorer_cases.json`;
- the results doc and its data.

**Edits (minimal):**
- `lattice/spec/ask.py`: the `inventory=` kwarg, digest section, citation branch, `implementation` field and flow
  verify; a no-op when it is None.
- `lattice/spec/__main__.py`: the `implementation` dispatch.
- `lattice/mcp/tools.py`: the 3 tools, registered conditionally.
- `lattice/studio/server.py`: `StudioConfig.implementation`, `Studio.ask`, docstring.
- `lattice/studio/session.py`: `_IMPL_TOOLS`, `_IMPL_RULES`, labels.
- `lattice/importer/cli.py`: the `inventory` dispatch, at most 6 lines.
- `lattice/studio/API.md`: the v1.22 section and header.
- Pinned tests, each disclosed: `tests/studio/test_mcp_intake_http.py` (`L_ASK`), `tests/studio/test_legend.py` (if it
  pins labels or lists), and any session-argv pin.
- This file, only by appending Addendum 1.

**Not touched:**
- importer: `lattice/importer/{workflows,workflow_run,mine,review,emit_spec,appmodel}.py`, `lattice/gocheck/**`;
- spec: `lattice/spec/{review,incremental,rules,check}.py`;
- MCP: `lattice/mcp/{server,projects,guard}.py`;
- Studio UI and dogfood: `lattice/studio/mock_server.py`, `lattice/studio/mock/`, `lattice/studio/web/**`,
  `dogfood/studio/**`;
- existing goldens;
- outside this worktree: the Streak checkout, every `job-scheduler*` directory, the main checkout.

## Amendment 1 (orchestrator rulings, 2026-10-15, before any code)

**R1-R5 approved as written.** `kind` stays unchanged; `implementation` is an optional answer field absent when
there is no inventory; no Studio route or screen; the inventory directory is outside the target and named in
`studio.toml [implementation]`; the held-out stage is run once by the orchestrator, report-only, with the
build node forbidden to read `job-scheduler-go*`.
**R6 modified.** The build node starts only after Spike B is verified and committed. `np-impldiff` will then
be brought up to the built Spike B (merge of `np-workflow`), and the real `workflows.json` replaces the
fixture; the fixture is kept only as a conformance test of schema `lattice.workflows/1`. If the built schema
differs from section D9, the difference goes through the single adapter and is recorded as an amendment before
the affected probes run. Probes that need verified flows (the verified-flag check, the replay check and the
dynamic half of the agent exercise) are then RUN, not NOT RUN.
**Agent exercise.** `claude` CLI only, no API key; the caps stand (USD 0.50 and 8 turns per session, USD 5 in
total, tokens counted as input + output + cache creation). A usage-limit or rate-limit error records the
remaining sessions as NOT RUN (usage limit) and is never retried in a loop or scored as a miss; the pass rule
(>= 11 of 13) is then not evaluated and n is stated. The scorer, question set and answer keys are frozen by
hash before the first session. Findings are never auto-sent to `change_propose`.
**Length.** The 485-line document stands as the contract; no rewrite.

## Addendum 1 (build node, before the Streak-stage probes ran)

Appended by the build node (section 7 allows this only). It records, before any probe ran, how the built Spike B
schema differs from D9 and what the build decided where the pre-registration is silent. Nothing in D1-D12 is
changed; nothing here tunes a probe.

**A. Built Spike B (`workflows.json`, `lattice.workflows/1`) against D9, read through the one adapter
(`implementation.read_workflows`).**
1. `app.extract_sha256` is the sha256 of `json.dumps(extract, indent=2, sort_keys=False)` of the extractor's own
   output (no final newline), not of the inventory's canonical `extract.json` (sorted keys, final newline). It can
   never equal `inventory.extract_sha256`, so D2's first fallback applies and is the normal path: the adapter
   compares `app.module` and, for every request step, that the route key is in the extract and that each
   `evidence[what=route].at` is a registration `file:line` the extract has for that key. A difference is
   `workflows_mismatch` (flows are shown, never verified). The result says `workflows_consistency: "route-set"`.
2. The machine `status` values are `static`, `observed`, `confirmed`, `partial`, `refuted`. D5's "confirmed" is
   B's `confirmed` and nothing else; `observed` and `partial` are not confirmed.
3. Steps all carry `route` (`METHOD /p/{name}`, templated by D3); `spec_object` is `{table, column, edges, initial}`
   with `table` the spec's table name and `machines[].table` the SQL name (D4's two matches are both used). No
   other difference from D9 (the keys the adapter reads all exist).

**B. Decisions the pre-registration does not fix.**
1. `inventory.json` also records `package` (default `"."`); evidence paths are prefixed with it.
2. A route in a rule's text ignores trailing sentence punctuation (`.`, `:`, `!`, `?`): `routes_in` as written
   would key `POST /api/login.` for "...with POST /api/login." (found on the unit fixture, before any probe).
3. Element reasons beyond D5's list: `static schema` (a table element), `machine <status>` (a machine that is not
   confirmed). A machine with no workflows machine is `present` when its (SQL table, column) is written in the
   extract's `status_writes` (reason `static run`); only workflows machines are ever listed as machines or orphans.
4. A table element is `impl:table:<sql>`; a profile link with a column adds `impl:table:<sql>.<col>`. A rule is
   verified when it has at least one route or machine element and all of them are verified; tables do not block it.
5. The report also carries `endpoints[]` (spec and code together: `implemented`, `spec_only`,
   `implemented_not_required`, `test_only`, `plumbing`), `tables[]`, `spec_skeletons[]` (B's multi-route skeletons)
   and per flow `spec_matches` (indexes into it). `flows[].rules` are the rules linking any route of the flow.
6. `workflow_find`: `no_match` when the best possible score (the sum of `ask.Index`-style IDF over the query terms
   that occur in some document; unseen terms weigh 0) is 0 or the top score is under 0.15 of it; an exact
   `METHOD /path` mention outranks everything and answers `exists` or `spec_only`; `several` when the second is
   within 10% of the top; candidates tie-break route, machine, flow, rule, then target.
7. `workflow_get` answers `{type: flow|machine, ...}`; the tools' errors are `ToolError("<code>: ...")` with the codes
   `inventory_missing`, `bad_inventory`, `bad_workflows`, `no_spec`, `bad_section`, `bad_limit`, `unknown_id`.
8. An `[implementation]` table that is present but empty registers the three tools (which then answer
   `inventory_missing`) and leaves the ask path as before.
9. The digest orders its section routes (with the spec-only endpoints), machines, rules, flow index; the flow index
   line is `id, inferred name, kind, status` only; full steps are retrieved per question (`IMPL_BUDGET`).
10. PS+: the planted `/metrics` route is written on the engine in `router.go` next to `/health`
    (`r.GET("/metrics", ...)`), because `r.Public` is the `/v1` group (`/v1/metrics`) and is not in scope in
    `NewRouter`; PO expects `GET /metrics` excluded as plumbing and `GET /v1/admin/stats` the only orphan.
11. S-dyn is Spike B's committed dynamic run (`docs/results/data/2026-10-15-workflow-layer/workflows.json`), frozen
    by its sha256; the runner re-runs `import workflows` once only if no `--workflows-dyn` file is given.
12. The base for PB is `454a8e6`; `ask.py`, `mcp/tools.py` and `studio/*` are unchanged between it and the merge
    base `c0ff1e4` (the only differences are Spike B's own files).
13. The pytest baseline was taken on a pristine archive of `c0ff1e4` with the main checkout's venv (no database
    drivers): 2 failed, 2283 passed, 5 skipped, 29 errors (all errors `ModuleNotFoundError`, the two failures
    `test_multidb` and `test_outside`); the after-run is compared against exactly that.

**C. Freeze for the agent exercise (written before the first session; the runner refuses to start on any mismatch).**
`claude --version`: 2.1.299 (Claude Code). Scorer cases: 54/54 hold (`implementation_agent_check.py`). Frozen at 2026-10-16T06:14:29+00:00.

```
ca65bcb4a10ca1e020523c4a8fd7f4fbc76f5b5af5afa5f11f3c22411301c728  dogfood/implementation_agent.py
3be9520c6d94fe70816f11c92357be42f73f8bfc86948d51a056d3a1a20c4e7d  dogfood/implementation_agent_scorer.py
69297855d3acf4d8d165473adf793972e9d29e4767fb92f1af3039a8518b7500  dogfood/implementation_agent_check.py
8eae818cd74af59c76e54dbc4b462ac7e76385e3e6ef1d992b2f3efa19655c7a  dogfood/implementation_agent_scorer_cases.json
92fb5a76aec490ca059cfea2f09d787ad4377243c3bb6545921f5f4cbb322d08  dogfood/contracts/agent_scorer.py
af1383dbf5d51f876c61a282f23a2fb60d79f536af7542dfe48387e6f1c573a0  workflows.json (S-dyn, Spike B's committed dynamic run)
84624894e9d3d12d7436df2b015fb7664ff4f7fcdab6a2ba45280f41759eac84  report JSON of S-dyn (the keys of Q1-Q4 come from it)
80511cbb05dc3a61c04521e96555586611f2d8813d7b88dc5dcafd2cc6891df1  report JSON of S4-stat (the key of Q12 and the key rules of Q9-Q11 come from it)
```

The code the sessions run against (not a gate of the runner; the two report hashes above are):

```
c8557dcd561f5a6dcd5843d557f154c0ceb4d1175ab34ba3fa0d2de974d3447c  lattice/spec/implementation.py
55849494b1ece51ed04a84e34f87da3f65c4fcc02b9523ac9269009e754f2e3b  lattice/importer/inventory.py
0925737b48e1b87fdcc9d8140a6625044592db130df0724af7121096eeb98ffd  lattice/spec/ask.py
3e94fce89e5347d0129a89b421f9c2015bd48651fa4c7dc8dad099005867584d  lattice/mcp/tools.py
2079d71ee794bf0e96607da11beb7c6957e041acaf05f7fc315495933d654df3  lattice/studio/session.py
9538a73630bab96fda879db893539de5587981afb9ea8b4d0efff285f3857cbf  lattice/studio/server.py
803d1d80ac7231ffe43b5b7ed42f97544a46ef8b6d1cf5b79d77a468052470ad  lattice/importer/cli.py
33a47d05325d391c4735c94710c264b71b3862e546a69da9f2bedc7a4976b330  dogfood/implementation_probes.py
847955c66876a6589c7e6a1b4cf2657391dddcf4fb6cc38ddd9dac95982ace5c  dogfood/implementation_mutants.py
```

Keys (in `implementation_agent_scorer_cases.json`): Q1, Q2 IMPLEMENTED-VERIFIED; **Q3 IMPLEMENTED-VERIFIED (predicted
UNVERIFIED: the report says `POST /v1/completions/batch` is verified, so the key follows the report and the difference is a
finding)**; Q4, Q12 IMPLEMENTED-UNVERIFIED; Q5, Q6, Q13 NOT-SPECIFIED-NOT-IMPLEMENTED; Q7, Q8 AMBIGUOUS (4 DELETE routes; 4 list
routes); Q9, Q10, Q11 REQUIRED-NOT-IMPLEMENTED (key rules from the S4-stat report). Design: sessions use `--tools
Read,Grep,Glob`, the three implementation tools beside `model_get`, `rule_get`, `code_links`, `graph_get`, `spec_lint`, an MCP
server `--readonly` over the clone, `[state] enabled = false` in the clone's `studio.toml`; B7 snapshots the clone and the
inventory (sha256, mode, mtime); B3 checks a bare `code:path:line` against D8's path, containment, case, file and line rules
(there is no quote to check); B8 needs the method and the path in one sentence; E2 is the question text through
`python -m lattice.spec ask --inventory`, scored on B2-B5 of the answer (the label from `implementation`).
