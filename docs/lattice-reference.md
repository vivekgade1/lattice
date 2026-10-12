# Lattice — complete reference (as of 2026-09-29)

> One document to orient anyone (human or a fresh Claude session) on what Lattice is, what was built, what was measured, what was decided and why.
>
> **Update 2026-09-30:** option 1 is done. Its findings and everything built since this document was written are in `docs/archive/handoff-2026-09-30.md`; read that next.
>
> Branch `lattice-design`, about 147 commits since the start of the project.
>
> The detailed sources are:
> - the specs (`docs/archive/superpowers/specs/`);
> - the plans (`docs/archive/superpowers/plans/`);
> - the results log (`docs/results/2026-09-26-bench-smoke.md`, append-only, every round);
> - the vision (`docs/archive/lattice-vision.md`).

---

## 1. The idea and where it stands

**Origin.** `docs/LLM-Native Coding Language Feasibility v1.md` (untracked; the user's source document) argued against a new text language for LLMs. It recommended *constraining what the model emits, validating it structurally, and compiling it deterministically*.

**Lattice** is that idea as a DSL written in Python syntax:
1. The model writes Lattice source.
2. Python's parser (`ast.parse`) parses it.
3. Checker passes P1–P7 enforce a strict profile.
4. The checked program is lowered to a **language-neutral plan IR**.
5. The plan is compiled to:
   - polars (tabular pipelines);
   - SQL (one statement per read, executed by the Lattice runtime);
   - a generated FastAPI app (proven equal to the runtime by differential tests).
   - A Go target has been specced (see §9).

**Vision** (`docs/archive/lattice-vision.md`): Lattice as *the native interface through which LLMs write and change software*, with six goals:
- easy to write;
- expressive;
- correct by construction;
- optimally compiled;
- the interface for changing existing code (lift → edit → emit);
- self-improving.

**Where it stands after five benchmark rounds and a pre-registered confirmation (§6):**
- The engineering works and is heavily tested (about 5,400 tests).
- The central claim is **not supported by the evidence**. That claim was that LLM edits made through Lattice, or with Lattice's guarantees checked on Python, are safer than plain Python edits.
- The next step is chosen: **option 1**, testing a harder task class (§10, and `docs/archive/option1-handoff.md`).

---

## 2. Architecture and code map

```
lattice/
  parse.py, diagnostics.py, canon.py      parse to AST; Diagnostic{code, span, message, hint, fix}; canonical printing
  catalog.py                              ops, fn.*/agg.*/win.* signatures, T2 stdlib allowlist, code tables
  typesys.py, profile.py                  scalar types, Schema (keys/owner/owner_via/private/one_of…),
                                          ApiProfile (auth, roles_from, page_shape, responses, natives, storage,
                                          error_body, datetime_wire, null_order, fuel, max_value_size…)
  check/                                  checker passes
    p1_nodes  p2_names  p3_canon          node whitelist, single assignment/scopes, canonical form
    p4_types  exprs  nullability          schemas/types, join fan-out (L420), null policy (L421)
    p5_effects  p6_api  p7_backend        effects; API rules (auth/ownership L80x, bounds L811, errors L82x,
                                          pagination L83x…); backend capabilities L87x
    normalise.py                          Python spellings → canonical Lattice (row.x, and/or, x if c else y, …)
    t2/ t2_calls.py tiers.py redos.py     T2 @procedure subset + type checker; T0↔T2 integration; tier labels
    loop_hints.py fix_hints.py            loop → construct hints; program-specific fix hints with machine Fix data
  ir.py, runtime/                         tabular plan IR + polars executor (window, traverse, iterate)
  api/                                    API plan IR (ir.py), SQL compiler (sql.py, Dialect in backend.py),
                                          runtime (runtime.py: Starlette server), codegen.py (FastAPI generator),
                                          outbox.py (for_each), native_context.py
  t2/                                     fuel-instrumented compilation of @procedures (R620/R621/R622)
  emit/thin.py                            emit Lattice into a host FastAPI app, replacing only lifted routes
  importer/                               FastAPI+SQLModel → Lattice (extract, graph, outside, trace, sql_lift,
                                          profile_gen, adapt, policy, suite, verify, lifters/, lift, llm_lift,
                                          carve, context, intent, report, cli)
  pycheck/                                Lattice guarantees checked on ordinary FastAPI code (PY801–PY806)
  llm/                                    prompts (tabular + API guides, equal-information baselines),
                                          client.py (Anthropic API), cli_client.py (claude -p transport)
bench/                                    task corpora, fixture apps and test helpers of the earlier benchmarks (see §5)
tests/                                    ~5,400 tests; `uv run pytest -q`; `live` marker deselected by default
```

**Key invariants, all enforced by tests:**
- The generated FastAPI app equals the runtime (`tests/api/test_codegen_differential.py`, 0 mismatches).
- An API read is one SQL statement.
- The importer never emits a route as Lattice unless it has passed `verify()` against the original app.

---

## 3. The language (what exists)

**Tiers** (spec `2026-09-27-lattice-t2-design.md`):

| Tier | Constructs | Guarantees |
|---|---|---|
| T0 | `fetch where derive select rename group join sort limit page one require insert update delete accessible/owned mine owner_is`, `when coalesce is_null fn.* agg.*`, params with bounds (`Int(ge,le)`, `Str(min_len,max_len,pattern)`, `Email`, `OneOf`, `Uuid`, `DateTime`, `T \| None`, `UNSET`+`sent()`), `unique_or=Conflict`, list constants, `user.<col>`, `is_admin(user)` | types, fan-out, nulls, ownership (L802), private columns (L803), bounds (L811), declared errors, one statement per read, termination |
| T1 | `window` + `win.*`, `traverse`, `iterate` (tabular), `for_each` (transactional outbox, after commit) | as T0; termination by explicit bounds |
| T2 | `@procedure`, `@record`: checked Python subset, own type checker, allowlisted stdlib, effects/capabilities, fuel-bounded (R620), size-bounded (R622) | types, effects, boundary ownership/privacy, bounded work. Not termination or internal logic |
| T3 | typed natives (pure, or effect via `for_each`) | signature and effects only |

**Profile options for faithful import:**
- `uuid_storage`, `datetime_storage`, `datetime_wire`, `error_body`, `null_order`;
- auth `TokenColumn`/`NativeToken(lookup=, unique=, invalid=, unknown=)`;
- roles `RoleColumn`/`AdminFlag`/`NoRoles`;
- `active_column`, `settings` (NativeContext), `one_of`/`one_of_stored`.

**Normaliser** (`normalise.py`): Python spellings are accepted and canonicalised. Examples: `row.title`, `a+b` on strings, `and`/`or`/`not`, `x if c else y`, string methods, `is None`, `in`, nested ops, named column expressions, `r.x` one-row values in writes, `None` branches.

**Diagnostic code ranges:**

| Range | Area |
|---|---|
| L0xx | syntax and version |
| L1xx | nodes and subset (L14x T2 subset) |
| L2xx | names |
| L3xx | canonical form |
| L4xx | types (L420 fan-out, L421 nulls, L43x loop constructs) |
| L5xx | effects |
| L6xx | steps and params |
| L7xx | conditionals |
| L8xx | API (L80x auth/ownership, L81x bounds, L82x errors, L83x pagination/response, L84x constants, L87x backend, L88x T2 integration) |
| L9xx | T2 types (L900–L925) |
| R-codes | runtime (R311 ambiguous caller, R610 iterate cap, R620/621/622 fuel, rows, size) |

---

## 4. Sub-projects and timeline

| # | Sub-project | Spec | Key commits | Outcome |
|---|---|---|---|---|
| 0–3 | Core DSL, checker, polars runtime, LLM loop, tabular benchmark | `2026-09-26-lattice-design.md`, plan `phase0-3` | early history | Built |
| step 2 | Trap/safety tabular benchmark (bench2), no-hints condition | `2026-09-26-lattice-step2-design.md` | `ed7b038`…`29d3612` | First measured Lattice advantage: Haiku, no hints, tabular traps |
| API | API profile: endpoints, ownership, validation, SQL runtime, FastAPI codegen | `2026-09-27-lattice-api-design.md` | `e73802a`…`538b81b` | Parity on explicit tickets; 0 vs 16 silent failures on implicit org policies (page cap) |
| 1 | Real-app extensions + FastAPI template proof | `2026-09-27-lattice-realapps-design.md` | `5438c77`, `19a808e`, `53d1821`, `5d302b7` | Template 49/53; the rest are named policy deviations |
| 1b | Loop constructs + Part B real-app gaps | `2026-09-27-lattice-loops-design.md` | `53508ee`…`edecb93` | Loop corpus 11/11 equivalent; template 49/53, policy-only |
| 2 | Importer (lift → verify; LLM close-the-gap; evidence layers S1–S6) | `2026-09-27-lattice-importer-design.md` | `48e0af4`…`679a577`, `37a0716`, `e32dc02`, `df8c1c5` | Template: 10 routes verified without an LLM. Held-out: TaskFlow 6/10, JustNotes 10/30 |
| slice | Thin end-to-end edit benchmark (import → LLM edit → emit → verify) | plan `2026-09-27-lattice-thin-slice.md` | `42cfa0d`, `2b9eee3`, … | Rounds 1–5 (§6) |
| 1c | T2 procedures, tiers, round-2 fixes | `2026-09-27-lattice-t2-design.md` | `fb85998`, `635e90d`, `3a918c8`, `24081f9` | Built; l12 lockout state machine now T2 |
| surface | Normalisation, endpoint-scoped edits, import readiness | `2026-09-27-lattice-surface-design.md` | `afb92c8`, `43537da` | Built; little effect on refusals |
| round 5 | Pre-registered language-vs-checks experiment, `pycheck`, CLI transport | `2026-09-28-lattice-round5-decision-experiment.md` | `7931377`, `0567f6f`, `ea239a0`, `23eac2a`, `0b3536c`, `85ca1de` | MIXED |
| confirm | Pre-registered confirmation | `2026-09-28-lattice-confirmation-run.md` | `8c8763e`, `dacbb9a` | Hypotheses refuted / not confirmed |
| 5 (draft) | Go target + Go benchmark | `2026-09-28-lattice-go-target-design.md`, plan `…-go-target-5a.md` | `81e7f68`, `1acc30e` | Specced in a separate session; not built |

---

## 5. Benchmarks (the harness was removed 2026-10-12)

The experiment harness of the earlier language/codegen direction (the runner CLI and
report, the Go arm, the edit-slice runner and analyses, the held-out apps under
`bench/heldout/`, the offline kit) was removed on 2026-10-12; it is in git history
before that date, and its results stay in `docs/results/` and `docs/archive/`.

What remains in `bench/`, because live tests of the checker, runtime and importer use it:

- **Task corpora and reference solutions:** `tasks.py`, `seed_tasks.py`, `more_tasks.py`,
  `deep_tasks.py`, `bench2_*.py`, `api_tasks.py`, `api_datasets.py`, `datasets.py`
  (tabular: 130 tasks plus bench2's 54 traps; API: 40 tasks with hidden HTTP cases).
- **Test helpers:** `compare.py`, `api_runner.py`, `sandbox.py`, `metrics.py`,
  `template_profile.py`, `template_suite.py`.
- **Fixture apps** (read as source by the importer and spec tests): `template_app/`
  (official full-stack-fastapi-template @ `cb740b6`, SQLite), `import_repos/` (taskflow @
  `8c53dbc`, justnotes @ `6cae6e3`, multivendor and the Phase-2 apps), `imports/`, `specs/`.
- **`edit_slice/`:** only the modules these tests import (tickets, oracle, repos, aids).

**CLI transport** (`lattice/llm/cli_client.py`):
- `claude -p --output-format json --system-prompt … --tools "" --setting-sources "" --no-session-persistence --strict-mcp-config --mcp-config '{"mcpServers":{}}' --disable-slash-commands`, run in an empty cwd.
- API-key and provider env vars are scrubbed, so it bills the logged-in plan.
- Usage limits stop cleanly (resume with `--resume`).
- Output cap via `CLAUDE_CODE_MAX_OUTPUT_TOKENS`.

**The `lattice` command** (`lattice/cli.py`, a console script: `uv run lattice ...` in the repo, `uv tool install -e .` for a global one). A thin dispatcher: `lattice X ...` is the same as `python -m lattice.X ...`, argv forwarded verbatim.

| Command | Same as | Notes |
|---|---|---|
| `lattice studio [DIR ...] [--port N] [--model M] [--mock [FIXTURES]]` | `python -m lattice.studio` | the Studio server |
| `lattice check [--project DIR] [flags]` | `python -m lattice.spec check` | `--spec`, `--app`, `--db`, `--login`, `--runtime`, `--services`, `--database`, `--layers` ... default to `DIR/spec.py` and the `[app]`/`[check]` tables of `DIR/studio.toml` (DIR defaults to the cwd); explicit flags win |
| `lattice spec check/fidelity/draft/lint/probe/review/ask ...` | `python -m lattice.spec ...` | |
| `lattice import ...` | `python -m lattice.importer` | |
| `lattice mcp serve/guard ...` | `python -m lattice.mcp` | |
| `lattice perf measure/check [--project DIR] [--budget FILE] [--json] [--sizes 5,25] [--runs 10] [--id ID] [--statements]` | `python -m lattice.perf` | statements per request (and latency) of the endpoints in `DIR/perf.budget.json`, against their budgets; not part of `lattice check`, not a build gate (spike 1a, below) |
| `lattice module install SOURCE [--index DIR] [--yes]`, `list`, `info NAME`, `uninstall NAME` (all `[--project DIR]`) | `python -m lattice.modules` | local modules pinned by sha256 in `DIR/lattice.modules.lock`, copied to `DIR/.lattice/modules/<name>/`; install prints the manifest and needs `--yes` or an interactive y; every load re-hashes and refuses a changed tree; git sources not implemented. Engines are descriptors (`lattice/spec/engines/*.toml`); a project adds one with `studio.toml` `[engines] paths` or `lattice spec check --engine-file FILE` (spike 2, `docs/design/lattice-module-descriptor.md`, API.md v1.14) |
| (removed 2026-10-12) `lattice gogen`, `lattice tsgen` | the Go and TypeScript code generators | `lattice/gogen` keeps only `regex.py` and `types.py` for the checker's Go-target rules |
| `lattice projects [DIR ...]` | | lists Studio projects (`studio.toml`, or `spec.py` + `spec.trace.json`) in DIR, cwd, `dogfood/*` and one level of children |

`lattice --version`, `lattice --help` and `lattice <command> --help` exit 0; an unknown command exits 2.

**Studio config keys** (`studio.toml`; the full list, per API version, is in `lattice/studio/API.md`). Keys a build branch's spec depends on:

| Key | Default | Meaning |
|---|---|---|
| `[rebuild] protected_branches` | `["main", "master"]` | branches Studio never builds onto without confirmation, and never syncs the spec onto |
| `[rebuild] sync_spec` | `"off"` | `"auto"`: `POST /api/plan/{id}/run` first commits the spec files as committed at HEAD (`REQUIREMENTS.md`, `spec.py`, `spec.trace.json`, `[mcp] protected`) onto the plan's target branch when they differ, before the start baseline; `"off"`: only on request (`POST /api/branches/{name}/sync-spec`, the Build view's Sync spec button). API.md v1.12 (2026-10-11) |
| `[mcp] allow_writes` | `[]` | MCP servers (as `GET /api/mcp` names them, e.g. `"claude.ai Linear"`) whose write and unknown tools a **change** chat session may run. By default chat sessions (ask, explain, change) see `lattice` plus the account's claude.ai connectors (no `--strict-mcp-config`, `--setting-sources ""` kept, so no plugins, plugin servers, skills or hooks) and may run only the connectors' read tools (`lattice/studio/mcp_intake.py`); rebuild and the planner see no external server. API.md v1.13 (2026-10-12) |

**Narrowing and timing a check** (`lattice check` / `python -m lattice.spec check`; `lattice/spec/selection.py`, 2026-10-11):
- `--only SELECTOR [SELECTOR ...]` (repeatable) and `--only-file PATH` (a JSON array of selectors, or one per line, `#` comments) run layers **D and X** on the selected scenarios only (and layer C's invariants after their requests); layers A, B, C (seed) and R are not narrowed. Selectors: `scenario:NAME`, a bare `NAME`, a glob on names (`fnmatch`: `auth_*`, `scenario:*login*`; a colon with another prefix is part of the name), `rule:ID` (the scenarios the trace links to the rule, globs on ids too; the trace is `--trace PATH`, default `<spec module>.trace.json` next to the spec module, i.e. `spec.trace.json`). Every selector must match a scenario: an unknown one exits 2 naming the nearest scenario names (or rule ids); an empty list, a `rule:` with no trace, or a rule linking no scenario also exit 2. Order is the spec's. Without `--only` the report is unchanged but for the new `timings` key (golden test `tests/spec/test_selection_timings.py`). With it, the JSON has `selection: {selectors, scenarios, of}` and a note. `check(..., only=[...], trace=...)` is the API.
- `--await-cap SECONDS` (2026-10-11, opt-in; `check(..., await_cap=N)`) caps every `Await`'s `within_s` at SECONDS and notes it in the report (`--await-cap N: every Await waited at most Ns ...`); an effect that does not show within the cap still fails the scenario. Meant for in-loop gates (Studio's `[rebuild] scoped_gate_args`, API.md v1.11), where a scenario awaiting a service not built yet would otherwise wait out its 30 s. On SIGTERM the check CLI exits through `SystemExit`, so its cleanup stops app processes and services and removes containers; `$LATTICE_CONTAINER_OWNER` labels its containers `lattice.owner=<value>`.
- The report (JSON and text) carries `timings`: `total_s`; `layers` (seconds per layer run; layer C's own entry is the seed check, its per-request SQL is inside B/D/X); `setup` on a server database (`database_server_s` container start, `seed_build_s.<variant>`, `db_clones`, `db_clone_mean_s`, `database_teardown_s`); `invariants_s`; and `scenarios[]`, one per layer-D scenario: `name, seconds, db_clone_s, given_s, app_start_s, steps_s, app_stop_s, db_release_s` (the parts, in order), `login_s, invariants_s` (included in the parts), `process_spawn_s, health_wait_s, process_stop_s` (a process app: of `app_start_s` / `app_stop_s`), `requests, logins`. The text summary ends with the layers on one line and the 5 slowest scenarios. Measured on Streak: `docs/results/2026-10-11-gate-timings.md`.

**Performance budgets** (`lattice perf`, `lattice/perf/`, spike 1a, 2026-10-12; results `docs/results/2026-10-12-spike1a-results.md`). A side file, not spec syntax (P3 undecided): `DIR/perf.budget.json` = `{"version": 1, "prepare"?: "<shell command run in DIR first, e.g. go build>", "budgets": [{"id", "endpoint": "GET /v1/home", "user": "ann", "seed": {"items": "N", "steps": [{"method", "path", "user", "json", "count": "N" | k}]}, "sizes": [5, 25], "queries": {"constant": 8, "per_item": 0}, "latency_ms": null | {"median", "size", "margin"}}]}`. In seed strings `{i}` is the 1-based repetition index and `{uuid}` a fresh UUID.
- A budget is **statements per request = constant + per_item * N** (N = seeded items). For each size the app is started through the project's harness exactly as `lattice check` starts it (`studio.toml` read by the same code; a fresh clone of the seed and a fresh app per size), seeded through the API, and the endpoint sent `--runs` times; the fit is the line through the smallest and largest size in exact arithmetic (`exact` when every run lies on it). `check` fails a budget when the fitted constant **or** per-item exceeds the budget's, or a size's count exceeds `constant + per_item * n`; values below the budget are reported as "better" with `delta` = fit - budget. Latency (min/median/max) is reported and compared only when `latency_ms` is set (`median * (1 + margin)` at `size`).
- Counting: MySQL through the general log (`log_output=TABLE`; `Query` + `Execute` rows of every connection that is not the tool's own; truncated before each measured request; switched back after); SQLite with an in-process (FastAPI) app through a `sqlite3` trace callback. Not supported (`unmeasured`, exit 2): a process app on SQLite, Postgres, ClickHouse. Containers are named `lattice-perf-*` and removed.
- Report JSON per budget: `{budget_id, endpoint, sizes: [{n, statements: [per run], latency_ms: {min, median, max}}], fit: {constant, per_item, exact}, budget, verdict: pass|fail|unmeasured, delta, notes}`; text: a table per budget. Exit codes: `check` 0 pass, 1 over budget, 2 cannot run or unmeasured; `measure` 0 measured, 2 otherwise.
- As an extra gate for a rebuild (Studio is unchanged; it runs gate commands as given): `[rebuild] gates = ["./check.sh", "lattice perf check"]` (in a project whose Python has Lattice and the database driver, e.g. `uv run python -m lattice.perf check`). Keep it out of `lattice check`: a latency threshold must never block a correct build, and a count budget is a target, not a correctness rule.

**Importer:** `uv run lattice import . --package <pkg>.app --app-factory <mod>:make_app --out DIR [--no-llm] [--backend cli] [--method M1..M6] [--max-cost-usd X]` (equivalently `uv run python -m lattice.importer ...`). The committed imports are in `bench/imports/`.

---

## 6. Measured results (headline numbers)

Full tables are in `docs/results/2026-09-26-bench-smoke.md`. The unit is a run; silent failures are the metric that matters.

| Round | What changed | Headline |
|---|---|---|
| Tabular step 2 | traps, no hints | Haiku: Lattice cut silent wrong answers on traps from 9–15 to 2–4 (accuracy 85–94% vs 72–82%); frontier models at parity |
| API | 40 endpoint tasks | Parity on explicit tickets; with implicit org policies: Lattice 0 silent vs FastAPI 16 (page cap) |
| Edit slice R1 (72 runs) | baseline | Lattice 3/18 vs Python 15/18 (Sonnet); language gaps + `owned()` semantic trap |
| R2 (144) | gap features, `mine()` | Sonnet parity on tuned tickets; silent failures no lower |
| R3 (288) | T2 era, held-out apps | Lattice wins on tuned tickets, loses on held-out tickets and apps (0/18 on apps: import gaps) |
| R4 (360) | normaliser, scoped edits | Silent failures halved (Sonnet 4 vs 14; Haiku 15 vs 31), but correctness lower and cost 2.7–3.7× |
| R5 (900, pre-registered) | arms P/L/Q/L+/Q+, CLI | Rule → **MIXED**. Sonnet: P 64/72 · 8 silent; L 34 · 31 (JustNotes import confound). Haiku: Q+ 61 · 11 vs P 51 · 20 |
| **Confirmation (607/630, pre-registered)** | 15 unconfounded template tickets, 6 reps | **Q1 refuted:** Sonnet L 7/67 silent vs P **1/90** (P 89/90 correct). **Q2 not confirmed:** Haiku Q+ 82 · 8 vs P 80 · 10 (p=0.40). Sonnet Q (checks only) 21 silent: the checks *hurt* the strong model |

**Takeaways:**
- **Sampling noise:** 3-rep rounds overstated effects. With 6 reps, plain Python on a frontier model is nearly perfect on this CRUD ticket class.
- **Lattice as the language LLMs write** lowers correctness everywhere and isn't reliably safer.
- **The checks on Python** add nothing measurable for Haiku and hurt Sonnet.

---

## 7. Key decisions and why

| Decision | Reason | Where recorded |
|---|---|---|
| A profile over Python's AST, not new syntax | the model is fluent in Python; no parser to write | design spec §3.1 |
| Plan IR is the core; Python is only input syntax | multi-target; clarified to the user | vision §3 |
| Guarantees never self-modify; the language grows only with evidence | soundness | vision §5 |
| Pre-registered decision rules for rounds 5 and the confirmation | avoid post-hoc spin | the two specs |
| Held-out tickets and repos; generality fixes only via synthetic tests | avoid tuning to the benchmark | importer plan, results notes |
| CLI transport (option A) | run on the user's plan; context isolated; the API key scrubbed | round-5 spec §8 |
| `owned()` → `accessible()` + `mine()` | a semantic trap (admins see all) caused silent failures | results R1–R2 |
| `one(t)` without `else_` stays refused | the 404 detail is API behaviour; a default would create silent failures | N1b report |
| Importer: owner only with FK + caller evidence; else unclear or shared | a wrong owner must fail verification, never pass silently | `profile_gen.py` docstring |

---

## 8. Known issues and gaps (open)

1. **Importer verification is too weak.** The JustNotes lift passed the importer's suite but was wrong on nested owner objects and other users' public notes (found in round 5). The generated suite needs cases for public or shared rows and nested response shapes.
2. **The held-out import is partial:**
   - TaskFlow: 6/10 routes verified; `login`, `update_task` and the two app-factory routes are islands.
   - JustNotes: 10/30 verified.
   - Remaining gaps: bare-list vs page-envelope responses, budget, the auth routes.
3. **CLI max-output cap:** the CLI makes up to 4 capped turns before failing, all billed. Sonnet-L on t25 exceeds the 600 s timeout.
4. **`pycheck`:** recall is 15/63 on trap variants. Acting on its findings made Sonnet worse (over-applied ownership and admin rules).
5. **Flaky tests under load:** `tests/api/test_for_each.py` timing (`LATER` is fixed at collection); fixed earlier, watch for recurrence.
6. **Stray untracked files** at the repo root: `app.db`, `db.sqlite` (0-byte, source unknown), and `.claude/` worktrees (now gitignored).

---

## 9. Other threads

- **Go target (sub-project 5):** a spec and a 5a plan were committed from a separate session (`81e7f68`, `1acc30e`). Not built.
- **Language-rules doc:** a saved note from another session refers to `docs/lattice-language-rules.md` ("multi-target, no drift"), but that file is not in this branch. Treat the note as pending until the file exists.
- **Self-improvement loop (vision §5):** only corpus logging exists (`corpus/imports.jsonl`, gitignored).
- **Housekeeping PR:** pushing to GitHub was blocked by credentials (account `aetheris-qao` stored). The PR description draft was in a past session's scratchpad. Compare URL: `https://github.com/vivekgade1/lattice/compare/main...lattice-design?expand=1`.
- **Standing process preferences:**
  - agentic graphs with parallel agents;
  - TDD;
  - the orchestrator commits;
  - agents never run tree-modifying git commands;
  - report tokens, cost per correct, program size, rounds and timing alongside accuracy;
  - pre-register decision rules.

---

## 10. Next: option 1

**Question:** is there a task class where frontier models *do* make silent mistakes editing ordinary code, and where Lattice (language or checks) measurably helps?

The plan and constraints are in `docs/archive/option1-handoff.md`.
