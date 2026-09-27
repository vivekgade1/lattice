# Lattice sub-project 2 — Importer: FastAPI + SQLModel → Lattice (design, for review)

> Status: DRAFT for review · 2026-09-27. Roadmap: `docs/lattice-vision.md` §4 and §6. Builds on sub-projects 1 and 1b. Their evidence (template proof 49/53, with the rest policy deviations; loop corpus 11/11 plus 1 island) is in `docs/results/2026-09-26-bench-smoke.md`.

## 1. Goal

Given a FastAPI + SQLModel repository, produce a Lattice import of it:

- a **profile** (schemas, keys, owners, private columns, roles, auth, page shape, response models, storage formats);
- **Lattice programs** for every route that can be lifted;
- **native islands** for logic that can't be lifted, which reference the original code and are never rewritten;
- an **import report** covering coverage, verification results, policy deviations, behaviour changes and islands.

An import is accepted only when the Lattice import answers the same as the original on a generated request suite. The only allowed differences are policy deviations, and the report lists each one explicitly.

Sub-project 3 (emitter + edit loop) then takes this import, lets an LLM edit the Lattice, and emits code back.

## 2. Non-goals

- **Frameworks other than FastAPI + SQLModel/SQLAlchemy ORM.** Flask, Django and raw SQL come later, through the same pipeline and new extractors.
- **Proofs of equivalence.** Equivalence here is tested equivalence, as vision §4.2 states.
- **Migrations, background workers, websockets, file uploads.** These are reported as unsupported routes, which become islands, or are skipped with a reason.
- **Preserving the original formatting.** That belongs to the emitter.

## 3. Pipeline

```
repo ──► 1 Extract (deterministic AST analysis)
           models → schemas     deps → auth/roles     security fns → natives     routes → route IR
     ──► 2 Lift per route
           2a deterministic lifters (idiom matchers) ──► Lattice
           2b LLM lift (checker + repair loop) for routes no lifter matches
     ──► 3 Verify per route
           generated request suite vs original app, on identical seeded DBs
           mismatch → LLM repair with the diff (≤ 2 rounds) → still failing → native island
     ──► 4 Report + write lattice_import/
```

### 3.1 Extract (deterministic, no LLM)

| Source construct | Lattice result | Rule |
|---|---|---|
| `class X(SQLModel, table=True)` fields | `Schema` columns and types | `uuid.UUID` → Uuid, `datetime` → DateTime, `EmailStr` → Email, `Field(max_length=)` → bounds, `Optional`/`\| None` → nullable, `Field(unique=True)` → unique, `primary_key` → key, `foreign_key` → fk |
| `Field(foreign_key="user.id")` + a route that compares it to `current_user.id` | `owner=` | inferred from the routes, and only confirmed when every route touching the table compares it the same way; otherwise reported as "owner unclear" and left unset, which makes the checker demand it |
| Columns never returned by any response model (e.g. `hashed_password`) | `private=` | response-model analysis |
| `Depends(get_current_user)` / `get_current_active_superuser` chains | `auth=`, `active_column`, `roles_from=AdminFlag(...)` | pattern over the dependency functions |
| Functions in security/util modules that routes call and that are not ORM work (hashing, tokens, email sending) | typed natives (pure or `effect=True`) | the signature comes from annotations; the body is referenced, not copied |
| Response models (`XPublic`, `XsPublic{data,count}`) | `responses=`, `page_shape=` | pydantic model analysis |
| DB URL / column types | `uuid_storage`, `datetime_storage` | taken from the SQLAlchemy column types and the dialect |

### 3.2 Lift

- **Deterministic lifters (2a)** are AST pattern matchers over route bodies, each emitting a Lattice fragment. The initial set covers the template's idioms:
  - `session.get(M, id)` + `if not x: raise HTTPException(404)` → `one(...)` / `NotFound`;
  - `select(M).where(...).offset(skip).limit(limit)` + `count()` → `page(..., total=True)`;
  - the superuser-or-owner check raising 403 → `require(x, owner_is(user), else_=Forbidden(...))`;
  - `M.model_validate(body, update=...)` + `add`/`commit`/`refresh` → `insert(...)`;
  - `obj.sqlmodel_update(body.model_dump(exclude_unset=True))` → `update(...)` with `UNSET` params;
  - a uniqueness pre-check raising 400/409 → `unique_or=Conflict(...)`;
  - `for` loops matching the 1b shapes → `window`/`traverse`/`for_each`, cued by the 1b loop hints.
- **LLM lift (2b):** for anything the lifters don't match, the model gets:
  - the route source;
  - the extracted profile;
  - the Lattice API guide;
  - the lifter output for the parts that did match.
  
  It then writes the Lattice route, and the checker/repair loop runs as in the benchmark (≤ 2 rounds). Model: claude-opus-5 by default, configurable.
- **Policy conflicts.** When the original violates a Lattice guarantee (an unbounded `limit`, or a 403 that reveals other users' rows exist), the lift adopts the Lattice guarantee and records a **policy deviation**. With `--strict-behaviour`, such a route becomes a native island instead, and nothing changes behaviour.

### 3.3 Verify

- **Request suite generation, per route:**
  - built from the route IR and OpenAPI;
  - covers the happy path, other users' rows, admin, missing ids, malformed ids, boundary values, missing and extra fields, partial updates, pagination edges and each declared error;
  - adds hypothesis-generated bodies within the declared bounds;
  - uses a seeded DB generated from the schemas: several users, ownership spread across them, nullable fields both null and set.
- **Comparison:** the same machinery as `bench/template_suite.py` and `tests/template/test_template_differential.py`, generalised into `lattice/importer/verify.py`. It compares status + JSON (placeholders keep the storage spelling) and DB effects. The representation rules are the profile's error body, datetime wire and validation envelope, each asserted as needed.
- **Outcome per route:** equal, equal except for declared policy deviations, or not equal after repair, which makes the route an island.

### 3.4 Output — `lattice_import/`

```
lattice_import/
  profile.py          # generated ApiProfile
  natives.py          # typed wrappers importing the original functions (islands and helpers)
  routes/<router>.lattice.py
  report.md / report.json
```

The report contains:

- **per route:** lifted by a lifter or by the LLM, verified-equal, deviations, island + reason, LLM rounds and cost;
- **coverage:** routes, lines and AST nodes lifted vs islands;
- **behaviour changes** that need human acceptance, e.g. "limit capped at 100" or "notify watchers: first 100".

## 4. Corpora and acceptance

1. **Template (development set):** `bench/template_app/` imported automatically, with:
   - all 9 routes lifted;
   - results equal to the hand-written proof: 49/53 match, and the rest are the same policy deviations;
   - 0 islands in the routes. The security natives are helpers, not islands.
2. **Loop corpus:** the originals of `tests/fixtures/loops/l07`, `l10` (API) lifted to the same constructs, and `l12` reported as an island with the matching reason.
3. **Held-out generalisation:** 2 public MIT/Apache FastAPI + SQLModel repositories, chosen and pinned by commit when the plan is written, vendored like the template and never used while developing lifters. The reported numbers:
   - route coverage (lifted and verified);
   - island rate with reasons;
   - deviation count;
   - LLM cost per route.
   
   **Target:** ≥ 70% of CRUD routes lifted and verified, with no unverified route ever emitted as Lattice.
4. **Soundness gate:** a route is never in the output as Lattice unless its verification passed. This has its own test, which uses an injected lifter bug and asserts that the route becomes an island.

## 5. Self-learning hook (vision §5)

Every import appends to `corpus/imports.jsonl`:

- (route source, lifted Lattice, verification result);
- lifter misses;
- LLM repair traces;
- island reasons.

This is the raw material for later learning work: mining new lifters and clustering islands to propose constructs. Sub-project 2 only records it; it doesn't act on it.

## 6. Components

| Module | Responsibility |
|---|---|
| `lattice/importer/extract.py` | repo → `RepoModel` (models, deps, routes, natives, response models); deterministic, no execution of the repo beyond importing it for pydantic model introspection |
| `lattice/importer/profile_gen.py` | `RepoModel` → `ApiProfile` source + list of unclear decisions |
| `lattice/importer/lifters/*.py` | one module per idiom family, `match(route_ir) -> LiftFragment \| None` |
| `lattice/importer/llm_lift.py` | prompt + checker/repair loop for unmatched routes |
| `lattice/importer/suite.py` | request suite + seeded DB generation |
| `lattice/importer/verify.py` | differential original vs Lattice (generalised from the template test) |
| `lattice/importer/report.py` | report.md / report.json |
| `lattice/importer/cli.py` | `python -m lattice.importer <repo> --app module:app --out lattice_import [--strict-behaviour] [--no-llm]` |

## 7. Testing

- Unit tests per extractor rule and per lifter, with positive and negative source snippets.
- A deterministic end-to-end run on the template with `--no-llm`. Every route should be covered by the lifters, so it needs no API key in CI.
- The soundness-gate test (§4.4).
- Live LLM runs on the held-out repos go in `bench/`, not CI, and report cost as the benchmark does.

## 8. Build order

1. Extract + profile generation (template).
2. Lifters (template idioms) + verify, generalised from the template differential.
3. Report + CLI; template end-to-end with `--no-llm`.
4. LLM lift + repair.
5. Loop-corpus lifting.
6. Held-out repos: select, vendor, run, report.
7. Review.
