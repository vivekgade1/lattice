# The go-to place for the app's business workflows (Spike F): pre-registration

## 1. Status, goal and the exact gap

2026-10-16, branch `np-gotoplace` (from `next-phases` 8931d46). **Written BEFORE any code.** Results:
`2026-10-16-goto-place-results.md`, data in `docs/results/data/2026-10-16-goto-place/`.
**Owner goal:** Lattice is the go-to place for understanding the business workflows coded in an app. Scope (plan
Addendum, Spike F): an **app map** (rules, workflows, machines, an access matrix, each with a status and `code:<path>:<line>`
evidence), a **chat that answers "what breaks if ..."** from the Spike D impact engine, and a **Studio view**, checked
in a **real browser**. This revisits the "no new screen" decision of Spikes A-C (plan, owner decisions 2026-10-15):
the owner asked for a screen in the Addendum. **Binding owner rule:** a workflow failure is observed or logically
determined, never predicted. In F that rule is a presentation rule: every failure statement on every surface (API, view,
one-shot chat, agent chat) traces to a tier 1 (observed) or tier 2 (determined) item of a `lattice.impact/1` report.
Anything else is "static: may fail" or "not decided".

Labels: **(m)** measured during recon, **(r)** a recon script's output (a blind expectation), everything else a
**prediction (p)**. Recon: code reading at 8931d46 and in the D worktree (`lattice-impact`, prereg 894aa4f plus
uncommitted engine files); Streak a0679e9 (**S**) and 1d00ede (**S4**) archived into scratch; `lattice import inventory`
on both (S with the committed Spike B `workflows.json`, sha256 af1383db...); `build_report` and `review._build` on S and S4;
`list_connected_browsers`. No model was called.

**What exists, and what does not (m).**
1. **Every fact the map needs is already computed.** Spike C's `implementation.build_report` gives per-rule statuses
   with element evidence, routes (auth, refusals, flows), endpoints (`spec_only` and so on), flows, machines, tables,
   exclusions and `unread`. `review._build` (`ReviewData`) gives rule text and spec status and the access matrix. On S:
   148 rules (108 `implemented_verified`, 34 `implemented_unverified`, 0 `required_not_implemented`, 6 `no_element`),
   68 flows (59 verified, 8 failed, 1 unverified), 1 machine (`nudges.delivery`, 3 edges, `static`, rules R123 and
   R127), 10 tables, 4 `unread`, 0 orphans. Every implemented rule has at least one evidence location (0 without). The
   report is 251,016 bytes. `build_report` takes 0.02 s after a 0.3 s import, and two calls give byte-identical output.
   On S4 (static inventory, no workflows): 29 `required_not_implemented`, 6 `spec_only` endpoints
   (`DELETE /v1/devices/{}`, `GET /v1/home`, `GET /v1/nudges`, `GET /v1/sync`, `POST /v1/devices`,
   `POST /v1/nudges/{}/dismiss`), **0 machines**: machines come only from a workflows capture.
2. **The access matrix is the spec's, not the code's.** `ReviewData.access_groups` on S: 3 groups (`health`, `v1`,
   `test`), rows `ann`, `ben` and `no token`, 26 columns, 78 cells. Values: 12 `·` (not specified), 21 `401`, 11 `200`,
   9 `404`, 6 `200/422`, and 19 others. On the code side, each route has `auth` (`Public` or `Customer` on S) and its
   refusals. Spike B flow steps carry `caller` (S: 178 `ann`, 12 `ben`, 5 `null`). `review.py` names an absent user
   `"no token"` (`step.user or "no token"`), so a flow step joins a cell by that same rule. The join is lookup only.
3. **The real Studio server has no route for the implementation.** API v1.22 is documentation only. `/api/graph`,
   `/api/code/*` and `/api/flow` are mock-only (`api.js MOCK_ONLY`). The UI has 17 views in three groups; Map (`map.js`)
   is the spec graph, and nothing shows the implemented app.
4. **The impact engine (D) is still being built.** It is registered as `lattice.impact/1` (CLI `lattice spec impact`,
   MCP `change_impact` only with `studio.toml [impact]`, `observe: true` refused under `--readonly`) and documents
   API v1.23. Its builder's worktree has `impact_report.py`, `impact_tier2.py` and `impact_tier3.py`, uncommitted.
   D's prereg leaves `session.py` unchanged, so **today no Studio chat can call `change_impact`**: the session
   allowlists (`_IMPL_TOOLS` and the others) do not name it.
5. **Studio already records one observed result per change.** A drafted change's record has `checks.impact`
   (`{verdict, failing[{target, label, message}]}`, layers C and D on today's app, when an app is configured). D's probe
   PS pins that its tier 1, restricted to C/D, carries the same `(target, label, rules)`.
6. **The two chat paths.** With `claude` installed, the UI chat is a Claude Code session (v1.5): free text and tool
   calls, with no deterministic answer check. Without it, the chat is the one-shot `POST /api/ask` (`ask.py`: a model,
   plus a deterministic citation checker).
7. **No browser was connected at prereg time:** `list_connected_browsers` returned `[]` (m). The 2026-10-15 Studio UI
   spike reported its browser walk NOT RUN for the same reason.

**Rulings requested.**
- **R1:** F is built **after D is merged** into `next-phases`. F documents API **v1.24**, because D takes v1.23.
- **R2:** **no new MCP tool.** The chat reads the map through the existing Spike C tools and impact through D's
  `change_impact`. F only adds `change_impact` to the session allowlists, gated on `[impact]`.
- **R3:** optional key `[impact] reports = "<dir outside the project>"`: Studio shows a saved tier-1 report only when its
  input hashes match (D3).
- **R4:** a new navigator group **App**, always listed. Its not-configured state shows the `studio.toml` lines to add.
- **R5:** the one-shot `POST /api/ask` gets a deterministic, model-free router for impact questions. It adds an optional
  answer key `impact`, and `kind` keeps its four values.
- **R6:** the question set and the hypothetical-trap questions are written and hashed by a separate agent before the
  build node runs a gated probe.

## 2. Decisions (fixed before coding)

**D1 The app map, `lattice.appmap/1`.** A new pure module, `lattice/spec/appmap.py`, with
`build_appmap(spec, trace, inventory, review, last_check=None) -> dict`. It consumes only the outputs above:
`implementation.build_report(...)`, `review._build(...)`, the inventory's `workflows["flows"]` steps (for `caller`), and
optionally Studio's in-memory `app.last_check`. **No new extraction:** it runs no extractor, `go`, git, Docker or model,
reads no source file except through `load_inventory`'s staleness hash, and never infers from names. It produces
canonical JSON: sorted keys and arrays, relative `/` paths, no time, host, port or absolute path.

| key | content (each value copied from the named source; "join" = exact-key lookup) |
|---|---|
| `schema`, `state` | `lattice.appmap/1`; `ok \| not_configured \| inventory_missing \| bad_inventory \| no_spec` (no other keys but `inputs`, `why` when not `ok`) |
| `inputs` | sha256 of `spec.py`, `spec.trace.json`, `REQUIREMENTS.md`; `inventory {extract_sha256, workflows_sha256, run_mode, stale}` (report verbatim); `last_check: null \| {verdict, sha256}` |
| `summary` | counts per section and status; `notes[]` (fixed sentences, e.g. `no_workflows_capture`: "machines and flows come from a workflows capture; this inventory has none") |
| `rules[]` | `id`, `text` and `spec_status` (ReviewData `rule_rows`); `impl_status`, `reasons`, `missing`, `impact_label` (report); `basis` (observed if `implemented_verified`, static if `implemented_unverified`, inferred if `required_not_implemented`, `none` if `no_element`); `elements[{key, kind, present, verified, basis}]`; `evidence[]` (union of element evidence); `workflows[]`, `machines[]` (ids whose `rules` contain the rule); `cells[]` (trace links of kind `cell:`); `last_check` (`pass \| fail \| null`, from `last_check.targets["rule:Rn"]`) |
| `workflows[]` | the report's flow rows verbatim (`id, name, inferred_name, kind, status, effective, basis, reason, routes, rules, machine_refs, evidence, implemented_not_required, spec_matches`) plus `steps[{i, method, route, caller, expect_status, evidence}]` from the inventory flow (request steps only) |
| `machines[]` | the report's machine rows verbatim, plus `spec_target` (the spec machine whose `machine_for` match is this row, else null) and `edges_detail[{from, to, in_spec, in_code}]` (`in_spec`: the edge appears in the spec machine's moves; `in_code`: in the row's edges) |
| `access` | `groups[{prefix, rows, columns}]` (ReviewData) and `cells[{row, col, spec, route, auth, refusals, route_status, observed[], evidence[]}]`. `spec`: the ReviewData value verbatim (`·` kept). `route`, `auth`, `refusals`: the report route row whose key equals `col` (null if none). `route_status`: the endpoint's report status (`implemented`, `spec_only`, `test_only`, `plumbing`, `implemented_not_required`). `observed`: `{flow, step, status}` for each request step of a **verified** flow with `route == col` and `(caller or "no token") == row` |
| `endpoints[]`, `excluded[]`, `unread[]` | report verbatim; `unread` items gain `at: code:<path>:<line>` |

**The map never decides agreement.** It does not compare a spec cell with code auth, nor a spec machine with code
edges beyond set membership. Whether the code honours a cell is layer D's job (the Checks view). A cell shows the spec
value, the code facts and the observed flow answers side by side. **Basis words are fixed:** `observed` = a Spike B
replay of those requests passed on a fresh database **at capture time**; `static` = read from the code; `inferred` = an
absence in a static extract. A stale inventory turns `implemented_verified` into `implemented_unverified` (Spike C's
rule, inherited) and the map's `summary.notes` says `stale`.

**D2 The CLI.** `python -m lattice.spec appmap --spec MOD:SPEC [--trace T] --inventory DIR [--app-root DIR] [--json]
[--section S]`. It is one `AUTHORING` entry in `lattice/spec/__main__.py`; `lattice --help` is unchanged. Exit 0: map
produced; exit 2: not produced.

**D3 HTTP, API v1.24** (additive; project-scoped like `/api/improve`: bare = the default project,
`/p/<name>/api/...`; unknown project: 404 `no such project`; any other method on these paths: 404 `no such route`):
- `GET /api/appmap[?section=summary|rules|workflows|machines|access|endpoints|unread]`: always 200 with
  `lattice.appmap/1` (whole map, or `{schema, state, inputs, <section>}`), including the non-`ok` states. A bad
  `section` gives 422 `{"detail", "code": "bad_section"}`. **Needs only `[implementation]`.** It re-reads the
  inventory on every call; it caches and writes nothing.
- `GET /api/appmap/element?key=<rule:Rn | wf-... | impl:machine:t.c | cell:row|METHOD /tpl | impl:route:METHOD /tpl>`:
  one element with its full row and its **neighbourhood** `touches {rules, workflows, machines, cells, routes}`, the
  map's own join keys, labelled `"basis": "static"` and `"meaning": "these elements reference it; whether they fail is
  not decided"`. An unknown key gives 404 `{"code": "unknown_key", "nearest": [5 keys]}`; a key over 300 characters
  gives 422 `bad_key`.
- `GET /api/impact?change=cN[&section=summary|tier1|tier2|tier3|flows|unread|decidability]`:
  `{schema: "lattice.studio-impact/1", change, state, report, studio_check, tier1_saved}`.
  - `state`: `ok | impact_not_configured | no_change | change_not_ready | no_state | inventory_missing | error`.
  - `report`: **D's report verbatim**, computed in-process with exactly the arguments and mode of D's `change_impact`
    default (`observe: false`: tiers 2 and 3, no app run, no container). F makes **no** edit to D's module, so a
    divergence is impossible by construction and is probed (PI).
  - `studio_check`: the change record's `checks.impact` verbatim (or null). It is labelled in the UI as "Studio's
    change check: observed, layers C and D, when the change was drafted".
  - `tier1_saved`: with `[impact] reports = DIR` (R3), `DIR/<cN>.json` is read. It is shown (`state: shown`, `tier1`
    verbatim) **only when** its `input` hashes (base and candidate sha256, `inventory.extract_sha256` and
    `workflows_sha256`) equal the ones D computes now for that change **and** `tiers["1"] == "run"`. Otherwise
    `state: mismatch` (with the differing keys), `absent` or `not_configured`. The owner writes the file with
    `lattice spec impact ... --change cN --tiers 123 --json --out DIR/cN.json`.
  - `change` must match `^c[0-9]{1,6}$`, else 422 `bad_change`. **GET never runs tier 1**, starts no process and
    opens no `StateStore` (D reads the record as a plain file). With `[state] enabled = false`, the record lives only
    in memory: `state: no_state`, honestly. Tier 1 is reached from the CLI (saved), from Studio's own change check, or
    from a change-mode chat (`observe: true`).
- `GET /api/model`, `/api/projects` and every existing route gain **no key** and answer byte-identically.

**D4 The Studio view (web UI).** A new group **App** (R4) with two views. It is always listed, no attention dot, no
count. Only `api.js` calls the server (two new functions: `appmap(section)`, `appmapElement(key)`, `impact(change,
section)`).
- **`#appmap` "App map".** A header strip with the inventory state (`static` or `observed` capture, `stale` banner,
  `run_mode`) and four tabs (a `role="tablist"`): **Rules** (table: id, text, spec status, implementation status as a
  word plus an icon, basis, evidence count; filter by status), **Workflows** (table: name, inferred-name flag,
  status, basis, routes, rules; a row opens the steps with caller, expected status and evidence), **Lifecycles**
  (per machine: states, edges with `in_spec`/`in_code` marks, evidence, rules), **Access** (rows × columns grid per
  group; each cell shows the spec value, then the code `auth` and the observed flow answers as small text; legend
  below). Plus a **Not read** list (the `unread` items: "Lattice could not read these; nothing is guessed"). Selecting
  any element opens a drawer: its row, evidence lines (`code:<path>:<line>`, which open the Code lens where the
  server has one, else copyable text), and its `touches`, with the static meaning sentence. Buttons: **Ask about this**
  (`ctx.askAbout`) and **What breaks if I change this?** (it prefills a change request, `ctx.changeAbout`, and says
  that an answer needs a drafted change).
- **`#impact` "Impact".** A change picker (from `model.changes`, `ready`/`accepted` selectable, others shown with
  their status). Then four tier sections in fixed order, with **fixed headings**:
  - "Tier 1 · observed: a failing check on today's app";
  - "Tier 2 · determined: follows from the spec and steps observed to pass";
  - "Tier 3 · static: may fail (code and workflows it touches; not a claim)";
  - "Tier 4 · observed after the change: not run (implementation step, Spike E)".

  Every item is rendered with its `id`, `kind`, `target`, `rules` and its text fields **verbatim** from the JSON (no
  rewording, no merging, no reordering beyond the report's order). Tier 1 shows `tier1_saved` when `shown`, else
  "Not run here" plus the CLI command; `studio_check` sits in its own box beside it, never relabelled as tier 1. The
  view always shows D's sentence: "Which workflows fail is mostly not decidable before the change exists."
- **States, each with its own text** (and a `data-state` attribute for tests): `loading`; `not_configured` (the
  `[implementation]` lines to add); `inventory_missing` and `bad_inventory` (the code and message); `no_spec`; `stale`
  (a banner with the changed, added and removed counts, plus the map); `no_workflows_capture` (empty Workflows and
  Lifecycles tabs saying *not captured*, distinct from *none found*); `not_built` (rules `required_not_implemented`
  read "Not built yet", endpoints `spec_only` read "In the spec, not in the code"); `unread_empty`; Impact:
  `impact_not_configured`, `no_change_selected`, `change_not_ready`, `no_state`, `tier1_not_run`, `tier1_shown`,
  `tier1_mismatch`, `error`.
- **Words are fixed** (`appmap.js` and `impact.js` constants). The strings `will fail`, `would fail`, `will break`,
  `would break` and `predict` (any case) appear in neither file except in the negative sentence "Lattice does not
  predict failures". A status is never shown by colour alone.
- Existing view files are byte-unchanged. `app.js` gains the group and two imports, `api.js` three functions,
  `css/views.css` appended rules.

**D5 Chat.**
- **Session chat (Claude Code).** With `studio.toml [impact]`, `session.py` adds `mcp__lattice__change_impact` to the
  ask, explain and change allowlists (`_IMPACT_TOOLS`, not in rebuild's list). It adds one paragraph, `_IMPACT_RULES`,
  to those modes' system prompt, and one `tool_label` ("Checked the impact"). The paragraph text is frozen here:
  > "For 'what breaks if ...' questions use mcp__lattice__change_impact on a drafted change (change id cN) or a
  > candidate directory; never answer them from your own reasoning. Report its tiers with their item ids, using the
  > tool's wording: tier 1 observed and tier 2 determined are the only failures you may state; tier 3 items are
  > 'static: may fail', never a failure; tier 4 is not run. If no change exists, say that Lattice decides failures only
  > for a drafted change, offer to draft one (change mode), and list what the element touches as static facts only.
  > Under the read-only modes tier 1 (observe) is refused; say it was not run."

  Without `[impact]`, the argv, settings and system prompt are byte-identical to base.
- **One-shot `POST /api/ask` (R5).** A new pure module, `lattice/spec/impact_ask.py`, runs in `server.ask` (and the
  mock's ask) **before** the model path. It routes a question only when it matches a frozen pattern (the regexes are
  frozen in the module's first commit and hashed in `freeze.sha256`):
  - (A) a change id `\bc\d+\b` **and** an impact word (`break|breaks|broken|fail|fails|failing|impact|affect`);
  - or (B) a hypothetical: `\bwhat\s+(would\s+|will\s+)?(break|fail)s?\b.*\bif\b`, `\bif\s+we\b.*\b(break|fail)s?\b`
    or `^\s*what\s+breaks\b`.

  (A) answers `kind: "value"` with a fixed template ("Tier 1 (observed): n failing; Tier 2 (determined): n; Tier 3
  (static, may fail): n locations, n workflows; Tier 4: not run.") and `impact`. `impact` holds the change id, the
  `tiers` map, and the items of tiers 1 and 2 verbatim, plus tier 3 capped at 20 with a total. Citations are
  `impact:<item id>`. (B) without a change answers `kind: "not_specified"`, with the answer "Lattice does not predict
  failures. It decides them only for a drafted change (tier 1 observed, tier 2 determined). Draft this change, then
  ask about its id." `touches` holds the static neighbourhood of every element the question names exactly (`METHOD
  /path`, `Rn`/`rule:Rn`, `wf-...`, `machine:T.c`), labelled static. A routed answer makes **0 model calls**
  (`usage.tokens: 0`). A question that does not match follows the old path byte-identically. `ask.py` is not edited.

**D6 Never:** write the project, the spec files, the inventory, the reports directory or the state directory; create a
cache, lock or temp file from a GET; run tier 1, a container or a subprocess from any F route; call a model from any F
code path (the session chat calls one, as before); open `StateStore` for impact; edit D's or C's modules; infer a
link from a name.

## 3. Probes (runner `dogfood/goto_probes.py`; blind pass rules)

Corpora: **S** (a0679e9, with the committed `workflows.json`: an observed capture), **S-stat** (S without workflows),
**S4** (1d00ede, static), **S-stale** (S with one `.go` file appended a comment, in scratch), and the mock's fixture
project. The candidate changes for impact probes are **6 dev candidates** on S that the build node writes under
`$SCRATCH/dev-candidates/`, one each: limit change, request bound, new invariant, removed endpoint, access rule, and a
pure rewording (the control). They are recorded in a Studio state dir as `ready` records through the scripted stub
generator, as in `tests/studio` (D's PS method).

| id | probe | prediction | pass |
|---|---|---|---|
| PM1 | map vs sources: every `impl_status`, `basis`, `evidence`, flow and machine field equals the Spike C report; every `spec`/`text`/`spec_status` equals ReviewData; `observed` equals an independent recount from `workflows.json` | S: rules 108/34/0/6, flows 59/8/1, 1 machine with 3 edges, 78 cells (12 `·`) (m); observed cells 15-30 (p) | 100% equal |
| PM2 | every `code:` location in the map passes Spike C's `check_code` (path, line, inside the app root) | 100% | 100% |
| PM3 | S4: 29 rules `not built`, 6 `spec_only` endpoints = 18 access cells with `route: null`, `no_workflows_capture` note, 0 machines | as stated (r) | exact |
| PM4 | S-stale: `stale` counts {changed 1}, 0 `implemented_verified`, banner state | as stated | exact |
| PM5 | states: no `[implementation]` → `not_configured`; missing directory → `inventory_missing`; a tampered `extract.json` → `bad_inventory`; no spec files → `no_spec` | 4/4 | 4/4 |
| PM6 | `unread`: the 4 S items listed with `at` | 4 (m) | exact |
| PE | `/api/appmap/element` for 12 keys (3 per kind): `touches` equals the join recomputed by the runner; unknown key → 404 with 5 nearest | exact | 12/12 + 404 |
| PI | `/api/impact?change=cN` `report` vs D's CLI with D's MCP-default flags, run as a separate process, canonical bytes, on the 6 dev candidates | identical | 6/6 |
| PIS | `tier1_saved`: a matching saved report → `shown`; the same file after the candidate changes one byte → `mismatch` naming `candidate_sha256`; no file → `absent`; no key → `not_configured` | 4/4 | 4/4 |
| PIC | `studio_check` equals the record's `checks.impact` verbatim | equal | 6/6 |
| PR | router on the frozen question set (section 5): routing decision, kind, and `impact` items vs D's report; 0 generator calls on routed questions (counting stub); non-routed questions give an answer byte-identical to base with the same stub | exact | 100%; 0 calls |
| PS | sessions: without `[impact]`, argv, settings and prompt are identical to base in 4 configs (with and without `[implementation]` and a workspace); with `[impact]`, +1 allowed tool in ask, explain and change, +1 paragraph (the frozen text), rebuild unchanged | as stated | exact |
| PU | UI facts without a browser (`tests/studio/test_appmap_ui.py`): `node --check` on every module; the nav group; `api.js` strings; fixed headings and words; the forbidden-word scan; a fake-DOM walk of **every state of D4** against the mock and a fixture server, asserting the `data-state` and that each tier item's JSON text fields occur verbatim in the DOM | all | all |
| PA | accessibility, static (fake DOM): every button, tab and input has an accessible name; tables use `<th scope>`; a status has a text word; the tablist has `aria-selected`; the loading region is `aria-live="polite"`; the drawer is reachable by keyboard and closes on Escape | all | all |
| PBR | **real browser walk** (section 4) | NOT RUN if no browser is connected (as at prereg time) | as section 4 |
| PD | determinism: the map (CLI) and the two GET routes, 10 processes with random `PYTHONHASHSEED` plus a second scratch root, on S, S4 and S-stale; scan for `/Users`, `/private`, `/tmp`, ISO times, ports | identical; none | 10/10 |
| PW | no-write: snapshot (sha256, mode, mtime, listing with hidden files) of the project, app root, inventory, reports dir and state dir, taken around 10 calls of each GET route, the CLI ×10, the router ×(question set) and the browser walk (if run). A recording `claude` shim is first on `PATH`, and `docker ps -a` is compared | 0 changed, 0 new, 0 shim calls, 0 new containers | all 0 |
| PB | byte-compat vs `git archive` of the merged D tip (R1): every existing route's answer on the fixture projects (`/api/model`, `/api/projects`, `/api/graph` mock, `/api/changes/cN`, `POST /api/ask` non-routed with stub); `tools/list` and MCP instructions in D's and C's configs (F adds no tool); `lattice --help`, `lattice spec` usage; existing web files except `app.js`, `api.js`, `views.css`; empty diff for `implementation.py`, `impact_*.py`, `ask.py`, `tools.py`, `check.py`, `incremental.py`, `importer/`, `gocheck/`; pytest suites show only the same pre-existing failure ids; `test_web_mock.py` unchanged and passing | all | all |
| PP | mock parity: the mock's `/api/appmap`, `/element` and `/api/impact` answers are built by the same `appmap.py` and `impact_ask.py` over committed fixture inputs (`lattice/studio/mock/appmap/`, a copy of `tests/spec/fixtures/implementation` plus one candidate). A test compares the mock and real server key for key on that fixture; the mock's second project answers `not_configured` | equal | equal |
| PAPI | `tests/studio/test_appmap_api.py` parses the fenced JSON blocks under `## v1.24` in `API.md` and checks the real and mock answers have exactly those keys (recursively) and the documented enumerations; the header reads `v1.24` | pass | pass |
| PX | time on S: the CLI map; GET `/api/appmap` p50 (it re-hashes the source); `/api/impact` (D's tiers 2+3) | ≤ 0.6 s; ≤ 0.5 s; ≤ 3 s (p) | ≤ 2 s; ≤ 1.5 s; ≤ 5 s |
| PSZ | size: `summary` section; whole map on S | < 4 kB; 120-350 kB (p) | summary < 4 kB; reported |
| GATE | `dogfood/studio/check.sh` with new rules R104-R108 (GET `/api/appmap` not-configured answer, `bad_section` 422, `bad_change` 422, other methods 404, prefix routing) | PASS, 481 + 10-20 cases, 57 + 4 scenarios (p) | PASS |

## 4. Real-browser protocol (PBR)

**Who:** the orchestrator, in its own session, after the build is frozen. The build node does not claim a browser
result. **B0:** `list_connected_browsers`. If it returns no browser, PBR is reported **NOT RUN (no browser
connected)**, with PU and PA (fake DOM, no layout) as the only UI evidence. It is never described as passed. The
orchestrator may ask the owner to connect Chrome first (question Q6).
**B1 setup:** the real server (`lattice studio`) on a scratch copy of S with `[implementation]`, `[impact]`, `[impact]
reports`, durable state and the 6 dev change records, bound to `127.0.0.1:<port>`. A second instance uses S4. The mock
runs on a third port.
**B2 walk:** for each of the 17 states in D4 (9 map, 8 impact), navigate to `#appmap` or `#impact` (reaching the state through fixtures
or the picker) and record:
- (a) `get_page_text`, sha256 plus the `data-state` value;
- (b) the `read_page` accessibility tree: every interactive node has a name, 0 unnamed;
- (c) `read_console_messages`: 0 errors and 0 warnings from Studio's scripts;
- (d) `read_network_requests` during the map and impact browsing: only `GET` to `/api/model`, `/api/projects`,
  `/api/appmap*`, `/api/impact*` and static files; 0 `POST`/`DELETE`;
- (e) `javascript_tool`: `document.documentElement.scrollWidth <= innerWidth` at 1280 px and at 375 px
  (`resize_window`);
- (f) keyboard: 40 `Tab` presses record `document.activeElement` names. Every tab, the first 5 rows of each table and
  the drawer's buttons are reached; the focused element has a visible focus style (computed `outline-width > 0` or a
  `box-shadow`); `Escape` closes the drawer;
- (g) on `#impact` for each dev change: each item id from the report occurs in the page text, and the tier headings
  occur verbatim.

**B3 evidence:** `data/browser/<state>.json` (text hash, accessibility counts, console, network, scroll, focus log)
plus one screenshot per view at each width. **B4:** the PW snapshot is taken around the walk. **Pass:** (a)-(g) on 17/17
states. A single failure is a finding, never re-labelled.

## 5. The frozen question set and the real-agent exercise

**Authoring (R6).** A separate agent (not the builder, not this author) writes `$SCRATCH/questions/qset.json` and the
scorer `dogfood/goto_score.py` before the build node runs a gated probe. The agent reads only the Spike C report JSON,
ReviewData, `workflows.json` and D's CLI output on the 6 dev candidates; it never reads `appmap.py`. The orchestrator
records the sha256 of both files in `freeze.sha256` first. The builder never opens `questions/`; the runner reads it.

**Content: >= 44 questions, each with a known answer:**

| set | n | example | known answer from |
|---|---|---|---|
| M rules | 5 | "Which rules are implemented but not verified?" | report `rules[].status` |
| M workflows | 5 | "Which verified workflows use POST /v1/habits/{}/completions?" | report `flows` |
| M machines | 3 | "Is the nudge delivery lifecycle observed or only read from the code?" (static) | report `machines` |
| M access | 5 | "What does the spec expect for ben on DELETE /v1/habits/{}, and what did a workflow observe?" | ReviewData cells, flows |
| N not built (S4) | 4 | "Is GET /v1/sync built?" (no: `spec_only`) | S4 report |
| I impact, with a change | 12 | "What breaks if c2 is applied?" (2 phrasings × 6 changes, incl. the rewording control: tiers 1 and 2 empty) | D's CLI report |
| H hypothetical, no change | 6 | "What would break if we raised the habit limit to 100?" | must answer *not decided*, 0 failure claims |
| C controls | 4 | "What happens when a check-in fails validation?" (a spec question, not routed) | must follow the old path |

**Scorer (hash-frozen, deterministic).** M/N on the map API: the expected field values equal exactly. I on the router
and the agent: the stated tier 1 and tier 2 item ids equal the report's (set equality), and tier 3 is never called a
failure. H: `not_specified` (router), or for the agent the "not decided" meaning plus the offer to draft. C: not routed.
**Unsupported failure claim:** a sentence matching `\b(fail|fails|failing|failed|break|breaks|broke|broken)\b` that
cites no tier 1 or tier 2 item id of the relevant report and is not hedged within the sentence by
`may|might|could|not decided|undecid|static|not run|no change`. Its count is reported per surface.

| surface | gate |
|---|---|
| map API (M, N: 22) | 22/22 exact |
| router (I, H, C: 22) | 22/22 exact; 0 unsupported failure claims; 0 model calls on routed questions |
| **agent exercise** (all 44) | **0 unsupported failure claims** (hard); >= 36/44 correct (p: 37-42); I verbatim-id compliance >= 10/12 |

**The agent exercise.** `claude -p --output-format stream-json`, driven by `dogfood/goto_agent.py` with the exact argv,
settings and system prompt Studio's `session.py` builds (ask mode, `--readonly`). It runs on the S scratch project with
`[implementation]` and `[impact]`, with no API key in the environment (stripped as in Studio). Model `sonnet`, one fresh
session per question, `--max-turns 12`, a 600 s timeout per question, and a **USD 6.00 total cap** on the CLI's list
estimate (no further question is started after the cap). Two extra I questions run in **change** mode with
`observe: true` (tier 1, Docker), capped at 2. A usage limit (`error.kind: usage_limit`) stops the exercise; it is
reported **NOT RUN** (or *partial: k of 44*) and never scored as a pass. Usage is reported: tokens, cache tokens, list
estimate, wall time, turns, and cost per correct answer.

## 6. Mutants (`dogfood/goto_mutants.py`, on a scratch copy; the control passes first; predicted 16/16)

| id | mutant | killed by |
|---|---|---|
| GM1 | a `static` machine or `implemented_unverified` rule reported `observed` | PM1 |
| GM2 | `unread` dropped from the map | PM6 |
| GM3 | evidence paths made absolute | PD, PM2 |
| GM4 | access `auth` taken from a path-name heuristic (`/admin` → staff) instead of the route row | PM1 (an S fixture route) |
| GM5 | staleness ignored (no downgrade) | PM4 |
| GM6 | flow steps with `caller: null` not joined to `no token` | PM1 observed recount |
| GM7 | `/api/impact` merges tier 3 `may_fail` flows into tier 1 `failing` | PI, PU verbatim |
| GM8 | the router answers a hypothetical with tier 3 items as failures | PR (H rows) |
| GM9 | `tier1_saved` shown on a hash mismatch | PIS |
| GM10 | the mock serves a hand-edited fixture (drift) | PP |
| GM11 | `GET /api/appmap` writes a cache file under the state dir | PW |
| GM12 | `/api/model` gains an `appmap` key | PB |
| GM13 | the UI word map shows "Verified" for `implemented_unverified` | PU |
| GM14 | `session.py` lists `change_impact` without `[impact]` | PS |
| GM15 | the router calls the generator for routed questions | PR (call count), PW shim |
| GM16 | `GET /api/impact` runs tier 1 (observe) | PW (containers), PI |

## 7. Risks, and what this cannot show

1. **D is not frozen.** F specifies against D's registered interface. If D's built function names differ, only F's
   thin adapter in `server.py` changes. If D's report schema differs from its prereg, F stops and reports, without
   patching D.
2. **Router recall is lexical.** A hypothetical phrased without the frozen patterns ("suppose the limit is 100 — any
   problems?") goes to the old model path, which answers from the spec and may speculate. The C and H rows measure
   precision on the registered phrasings only. The session chat depends on a model following `_IMPACT_RULES`; the
   0-unsupported-claim gate measures it on 44 questions, which is not a proof.
3. **"Observed" is as old as the capture.** Flows and machine bases are Spike B replays at capture time; staleness
   detects code edits, not database or config drift.
4. **The access matrix decides nothing.** It juxtaposes spec, code and observations. Whether the code honours a cell
   stays with layer D. Machines exist only with a workflows capture (S4 shows none).
5. **Tier 1 in Studio is indirect.** It comes from a saved CLI report (hash-matched), from Studio's own change check
   (C/D only, at draft time), or from a change-mode chat. A GET never runs it. With `[state] enabled = false`, impact is
   unavailable (`no_state`).
6. **The browser gate may be NOT RUN.** No browser was connected at prereg time. The fake-DOM walk checks structure,
   words and states, but not layout, contrast, focus feel or rendering performance.
7. **Cannot show:** tier 4 (Spike E); other apps than Streak (no second captured Go app with workflows, handoff item
   3); Python apps (no extract: their map says `inventory_missing`); the owner's intent behind a rule (Spike G).

## 8. Blind vs post hoc, and commands

All predictions and pass rules are fixed now. A missed prediction is a **finding**. Fixes are separate post-hoc commits,
rerun with deltas reported. The question set is scored **once**, after the freeze. A fix made after seeing it is never
re-scored as blind. The agent exercise runs last (cost) and once; a rerun is reported as a rerun. **Pass:** every gated
row of sections 3 and 5, mutants 16/16 (by probes or named unit tests, disclosed), PB, and PBR passed or honestly NOT RUN
(F then passes **without** its browser gate, and says so in the first line of the results).

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-f PYTHONDONTWRITEBYTECODE=1
SCRATCH=/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/gotoplace
WF=docs/results/data/2026-10-15-workflow-layer/workflows.json                        # sha256 af1383db...
git -C /Users/vivekgade/Documents/streak status --short                               # unchanged (pre-existing ` M studio.toml`)
uv run python -m pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck -q -rs   # baseline, before edits
python dogfood/goto_probes.py --stage dev --streak-ref a0679e9 --streak-ref2 1d00ede --workflows $WF \
    --scratch "$SCRATCH/p" --out docs/results/data/2026-10-16-goto-place                  # all rows but section 5
uv run python dogfood/goto_mutants.py --scratch "$SCRATCH/m" $(seq -f GM%g 1 16)
uv run python -m pytest tests/spec/test_appmap.py tests/studio/test_appmap_api.py tests/studio/test_appmap_ui.py \
    tests/studio/test_impact_ask.py -q
dogfood/studio/check.sh --json
shasum -a 256 lattice/spec/appmap.py lattice/spec/impact_ask.py lattice/studio/server.py lattice/studio/session.py \
    lattice/studio/web/js/views/appmap.js lattice/studio/web/js/views/impact.js > "$SCRATCH/freeze.sha256"   # BEFORE the qset
python dogfood/goto_probes.py --stage qset --questions "$SCRATCH/questions" --out .../qset      # orchestrator, once
python dogfood/goto_agent.py --questions "$SCRATCH/questions" --usd-cap 6 --out .../agent      # orchestrator, once, last
uv run ruff check <changed .py>; uv run ruff format --check <created .py>; node --check lattice/studio/web/js/views/*.js
```

## 9. File ownership (build node)

**Owns (new):** `lattice/spec/appmap.py`; `lattice/spec/impact_ask.py`; `lattice/studio/web/js/views/appmap.js`,
`views/impact.js`; `lattice/studio/mock/appmap/` (fixture inputs); `tests/spec/test_appmap.py`,
`tests/studio/test_appmap_api.py`, `tests/studio/test_appmap_ui.py`, `tests/studio/test_impact_ask.py`;
`dogfood/goto_probes.py`, `dogfood/goto_mutants.py`, `dogfood/goto_agent.py`; the results data directory.
**Additive edits only:** `lattice/studio/server.py` (three GET routes, `Studio.get_appmap/get_appmap_element/get_impact`,
the router call at the top of `ask`); `lattice/studio/mock_server.py` (the same routes, router); `lattice/studio/API.md`
(header v1.24, a `## v1.24` section with fenced JSON shapes); `lattice/studio/session.py` (`_IMPACT_TOOLS`,
`_IMPACT_RULES`, one `tool_label` entry, gated on `[impact]`); `lattice/studio/web/js/app.js`, `api.js`,
`css/views.css`; `lattice/spec/__main__.py` (one `AUTHORING` line); `dogfood/studio/{REQUIREMENTS.md, spec.py,
spec.trace.json, harness.py if needed}` (R104-R108).
**Reuses read-only:** `implementation.{build_report, load_inventory, implementation_config, check_code, code_view}`,
`review._build`, D's impact entry points and `read_change`.
**Not touched:** `implementation.py`, `impact_report.py`, `impact_tier2.py`, `impact_tier3.py`, `ask.py`,
`lattice/mcp/tools.py`, `check.py`, `incremental.py`, `importer/`, `gocheck/`, existing view modules, existing tests,
goldens and fixtures (any edit is a finding); Streak, `job-scheduler*` and the main checkout; `$SCRATCH/questions/`. The
builder makes no commits; the orchestrator stages explicit paths only (never `git add -A`). No API key; model use only
via the `claude` CLI, and only in the agent exercise.

## 10. Questions for the orchestrator

- **Q1 (R1).** Should F be built after D merges into `next-phases` (recommended: v1.24 and byte-compat against D's tip),
  or on `np-impact`'s tip before it merges?
- **Q2 (R3).** Approve the optional `[impact] reports` key? If not, tier 1 in the view comes only from Studio's change
  check and the chat, and `tier1_saved` is dropped.
- **Q3 (R4).** Should the App group always be listed (recommended: the not-configured state teaches setup), or gated
  on `[implementation]` like Contracts on a workspace?
- **Q4 (R5).** Approve the model-free router and the optional `impact` answer key (kind keeps four values)?
- **Q5.** The agent exercise budget: USD 6.00 list estimate, 44 + 2 observe questions?
- **Q6.** Before scoring, should the owner be asked to connect Chrome so PBR can run? Without a browser, PBR is NOT RUN.
- **Q7 (R6).** Dispatch the separate question-set agent after this commit, and record `freeze.sha256` for `qset.json`
  and `goto_score.py` before the build node's gated probes.

## Amendment 1 (orchestrator rulings, 2026-10-16, before any code)
- **Q1:** F is built only after Spike D is merged into `next-phases`; the build branch is rebased/merged onto that tip.
- **Q2:** approved, the optional `[impact] reports` key; absent = no saved tier-1 report shown (state "tier 1 not run").
- **Q3:** the App group is always listed (empty states carry the explanation).
- **Q4:** approved: the model-free router and the optional `impact` answer key; the answer kind keeps its four values.
- **Q5:** the agent budget stands: USD 6 cap, 44 questions plus 2 tier-1 runs, claude CLI only, NOT RUN on a usage limit.
- **Q6:** Chrome connection is requested from the owner before scoring; with no browser the browser gate stays NOT RUN.
- **Q7:** the question-set agent is dispatched after D's engine is merged (the I and H questions need D's CLI
  report on the 6 dev candidates); sha256 of the question set and scorer are recorded in `freeze.sha256` by the
  orchestrator before the builder runs any gated probe.
- The standing rule stays binding: no predicted failures; the forbidden-word scan and the unsupported-claim gate
  are not relaxed.

## Amendment 2 (orchestrator rulings on the question-set author's questions, 2026-10-16, before the build)
- **Claim rule stays literal as the gate.** The unsupported-failure-claim regex and hedge list of section 5 are not
  loosened: the hard gate counts every match. The scorer also reports, **report-only and never gating**, a second
  count that exempts (a) a negation ("nothing fails", "no ... fail") and (b) a sentence that names a recorded replay
  (a flow id `wf-...` or "the replay of ..." with its status). Both counts appear in the results. The frozen agent
  prompt asks for "does not pass" / "is refused" phrasing; a literal-gate miss caused by echoing the user's own words
  ("fails validation") is reported as such, not excused.
- **Change records:** the runner records `candidates/c1..c6` (the author's byte-exact files) as ready change records,
  in that order, in a fresh state directory, so "c1".."c6" in the questions mean those changes. The builder is given the
  formats (answer files, candidate setup) through `FORMATS.md`, never the questions.
- **Path normalisation:** the app map joins routes on the normalised form (`{id}`, `{habit_id}` and similar written
  `{}`), as the access questions assume; an exact-string join would fail M-A1 and that would be a real finding.
- **Router patterns are case-insensitive.**
- The two extra `observe: true` questions are not frozen; their expected ids come from a D CLI `--tiers 123` run at
  scoring time and are reported separately.
