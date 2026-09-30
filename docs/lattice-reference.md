# Lattice — complete reference (as of 2026-09-29)

> One document to orient anyone (human or a fresh Claude session) on what Lattice is, what was built, what was measured, what was decided and why.
>
> **Update 2026-09-30:** option 1 is done. Its findings and everything built since this document was written are in `docs/handoff-2026-09-30.md`; read that next.
>
> Branch `lattice-design`, about 147 commits since the start of the project.
>
> The detailed sources are:
> - the specs (`docs/superpowers/specs/`);
> - the plans (`docs/superpowers/plans/`);
> - the results log (`docs/results/2026-09-26-bench-smoke.md`, append-only, every round);
> - the vision (`docs/lattice-vision.md`).

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

**Vision** (`docs/lattice-vision.md`): Lattice as *the native interface through which LLMs write and change software*, with six goals:
- easy to write;
- expressive;
- correct by construction;
- optimally compiled;
- the interface for changing existing code (lift → edit → emit);
- self-improving.

**Where it stands after five benchmark rounds and a pre-registered confirmation (§6):**
- The engineering works and is heavily tested (about 5,400 tests).
- The central claim is **not supported by the evidence**. That claim was that LLM edits made through Lattice, or with Lattice's guarantees checked on Python, are safer than plain Python edits.
- The next step is chosen: **option 1**, testing a harder task class (§10, and `docs/option1-handoff.md`).

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
bench/                                    benchmarks (see §5)
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

## 5. Benchmarks and how to run them

**Tabular** (`bench/`): 130 tasks plus bench2 (54 traps/safety).
- Arms: A (pandas), P (pandas + repair), B (Lattice + repair), C (Lattice, first try).
- Conditions: hints / `--no-hints` / `--implicit`.

**API:** 40 tasks, with hidden HTTP cases.

**Edit slice** (`bench/edit_slice/`): the main benchmark of rounds 1–5 and the confirmation.
- **Tickets** (`tickets.py`): 30 realistic change tickets with unstated traps. The docstring lists the traps and is never sent to models.

  | Tickets | Group |
  |---|---|
  | t1–t6 | tuned |
  | t7–t18 | held-out template |
  | t19–t24 | held-out apps (TaskFlow, JustNotes) |
  | t25–t30 | fresh (3 template, 3 held-out apps) |
- **Apps:**
  - `bench/template_app/` (official full-stack-fastapi-template @ `cb740b6`, SQLite);
  - `bench/import_repos/taskflow/` (PhilipOkeke/taskflow-backend-api @ `8c53dbc`, MIT);
  - `bench/import_repos/justnotes/` (DatMayo/JustNotes @ `6cae6e3`, MIT).
- **Oracle** (`oracle.py`, `repos/*.py`): hidden cases (generated suite + trap cases) against a hand-written Python reference. Outcomes:
  - **correct**;
  - **loud:** 5xx, fails to start, or refused;
  - **silent:** a wrong non-5xx answer or a wrong DB effect.
- **Arms** (`run.py`):
  - **P:** edit Python.
  - **L:** edit imported Lattice, then emit.
  - **Q:** edit Python + `pycheck` findings.
  - **L+ / Q+:** plus retrieved examples + mistakes table (`aids/`).
- **Repairs:** 2 rounds. Feedback is only what a developer sees locally.
- **Commands:**
  - `uv run python -m bench.edit_slice.run --backend cli|api --model M --arm P|L|Q|L+|Q+ --tickets all|round3|round4|repos|t1,t2… --reps N --out X.jsonl [--resume] [--workers N] [--estimate] [--dry-run-reference] [--dry-run-partial]`
  - `bench/edit_slice/rescore.py` re-scores saved runs under an updated oracle.
  - `bench/analysis/diag_persistence.py` mines checker-error persistence.
- **Results:** in `bench/results/` (gitignored). Artefacts hold every round's model response and candidate files.

**CLI transport** (`lattice/llm/cli_client.py`):
- `claude -p --output-format json --system-prompt … --tools "" --setting-sources "" --no-session-persistence --strict-mcp-config --mcp-config '{"mcpServers":{}}' --disable-slash-commands`, run in an empty cwd.
- API-key and provider env vars are scrubbed, so it bills the logged-in plan.
- Usage limits stop cleanly (resume with `--resume`).
- Output cap via `CLAUDE_CODE_MAX_OUTPUT_TOKENS`.

**Importer:** `uv run python -m lattice.importer . --package <pkg>.app --app-factory <mod>:make_app --out DIR [--no-llm] [--backend cli] [--method M1..M6] [--max-cost-usd X]`. The committed imports are in `bench/imports/`.

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

The plan and constraints are in `docs/option1-handoff.md`.
