# Lattice sub-project 1c — Tier T2: general procedural code with graded guarantees (design, for review)

> Status: DRAFT for review · 2026-09-27. The user set the direction ("expressiveness should cover everything Python covers"; chose "T2, then round 3"). Evidence: thin-slice rounds 1–2 (`docs/results/2026-09-26-bench-smoke.md`). Rounds 1 and 2 showed that missing constructs cause loud failures. They also showed that silent failures come from Lattice-specific semantics and awkward references (caller id, ownership intent, default null order), not from missing loops.

## 1. Goal

Everything Python can express is expressible in Lattice. Each piece of code carries the strongest guarantee its form allows, and the checker pushes code towards the strongest tier.

| Tier | Form | Guarantees |
|---|---|---|
| T0 | Declarative ops (today's core) | all: types, fan-out, nulls, ownership, bounds, one statement per read, termination |
| T1 | `window`/`traverse`/`iterate`/`for_each` | all; termination by explicit bounds |
| **T2** | `@procedure`: ordinary Python in a checked subset | static types; declared effects/capabilities; ownership/private rules at every data boundary; fuel-bounded execution. *Not* guaranteed: termination proof, freedom from logic errors inside |
| T3 | typed natives | signature and declared effects only |

## 2. T2 surface

```python
@procedure
def title_locked(item: Row[Item], caller: Caller, now: DateTime) -> Bool:
    if caller.is_admin:
        return False
    age = now - item.created_at            # Duration
    return age > hours(24)

@procedure(effects=["mail"])
def digest(rows: list[Row[Item]], caller: Caller) -> list[str]:
    lines: list[str] = []
    for r in rows:                          # bounded: iterates a finite value
        if r.title.strip():
            lines.append(f"{r.title} ({r.created_at:%Y-%m-%d})")
    return lines
```

- **Allowed:**
  - assignment and rebinding of locals, augmented assignment, `if`/`elif`/`else`;
  - `for` over finite values (lists, dicts, rows, `range`);
  - `while` (fuel-bounded);
  - `break`/`continue`, `return`;
  - comprehensions, f-strings, `try`/`except` of declared error types, local helper `@procedure`s, recursion (fuel-bounded);
  - `@record` dataclasses as value types;
  - a standard-library allowlist: `math`, `datetime`, `re`, `decimal`, `string`/`str` methods, `itertools` (finite functions), `collections.Counter`/`defaultdict`, `json`.
- **Not allowed:**
  - unrestricted I/O, `import` outside the allowlist, `eval`/`exec`, reflection (`getattr` with computed names, `__dict__`);
  - global mutable state, threads, `async`;
  - classes other than `@record`;
  - mutation of values passed in: inputs are immutable, and procedures return new values.
- **Types:** parameters and returns are annotated. Locals are inferred by a Lattice-owned checker over a small type language: `Int Float Str Bool Date DateTime Duration Uuid`, `T | None`, `list[T]`, `dict[K, V]`, `tuple[...]`, `Row[Schema]`, `@record` types and `Caller`. Diagnostics use the existing L-code format with spans and fix hints. Pyright/mypy are **not** used, so that diagnostics stay predictable for the repair loop.
- **The caller as a value (`Caller`):** it has `caller.id`, `caller.is_admin`, `caller.role` and profile-declared public user columns. The same references become available in **T0 expressions**: `user.id`, `user.is_admin`, and `is_admin(user)` as a Bool. This fixes the round-2 finding directly.

## 3. How T2 connects to T0/T1

- **Endpoints call procedures in two ways:**
  - as scalar functions in `derive`/`where`/`require` conditions, row by row (`derive(t, locked=title_locked(row(), user, fn.now()))`);
  - or whole-value, `x = call(proc, rows=t, ...)`, where the input is a bounded table from `page`/`limit`/`one`/`mine` (L439-style bound).
- **Procedures never query the database directly.** Data arrives through T0 reads, so reads stay single SQL statements and ownership stays checked at the boundary. A procedure that needs more data declares it as a parameter, and the endpoint reads it with T0.
- **Writes happen in T0:** `update`/`insert` with values computed by procedures, or rows returned by a procedure (a `list[record]` bounded by its input), via `insert_many(T, rows=...)`.
- **Private columns:** a `Row[Schema]` passed to a procedure excludes private columns unless the endpoint is Admin and the parameter is declared `Row[Schema, private=True]` (L803 otherwise).
- **Effects:** `@procedure(effects=[...])` must name a capability from the profile (`mail`, `http`, …). Effect calls go through effect natives. An effectful procedure may only be called from `for_each`, which keeps the outbox/after-commit semantics.

## 4. Execution and targets

- **Fuel:** every loop iteration, call and comprehension element costs fuel. The per-request budget is `ApiProfile.fuel` (default 1,000,000). Running out raises runtime diagnostic **R620**, answered as a 500 with a stable body and a log entry. This gives bounded work, not a termination proof.
- **Runtime:** T2 bodies are compiled to Python with fuel counters inserted: an AST transform, then `compile` of the *checked* AST only. Values are converted at the boundary: SQL rows become immutable records.
- **Codegen:** the generated FastAPI app contains the same instrumented functions. The differential test covers T2 endpoints.
- **SQL:** T2 is never pushed into SQL. Per-row scalar procedures used inside a read run in the app layer after the single statement, and the plan marks such a read "T2-post". For filters that would change `page` totals, the checker requires the T2 condition to come after `page`, or reports **L880** "T2 filter before page — totals would be computed in Python" and suggests a T0 form.
- **Tabular profile:** T2 procedures as row functions via polars `map_elements`. This comes later; API first.

## 5. Tier reporting and guidance

- The checker annotates each endpoint and procedure with its tier. An import or edit report shows the tier mix.
- The T1 loop hints extend to T2 bodies. For example, "this `for` computes a running total — use `window` (T1)" is a **warning**, not an error.
- The guide says: "Prefer T0/T1; use a `@procedure` when the logic is naturally step-by-step."

## 6. Round-2 fixes folded in (general, not ticket-specific)

1. **Source-semantics defaults.** `ApiProfile.null_order` (`"last"` | `"smallest"`, where smallest means nulls first ascending and last descending, SQLite's own order). The importer sets it from the source dialect; the template gets `"smallest"`.
2. **Caller references in T0** (§2): `user.id`, `user.is_admin`, `is_admin(user)`.
3. **Ownership intent.** Add `accessible(t, by=user)` as the preferred spelling of `owned`, keeping `owned` as a deprecated alias, next to `mine(t, by=user)`. The guide and lifters use `accessible`/`mine`.
4. **Owner transfer** (the t12 gap): `update(t, owner=<Uuid expr>)` is allowed only in Admin endpoints, or when the row passed `owner_is(user)`. The new owner must exist (FK), and it is one statement.

## 7. Acceptance

1. Checker tests per rule. Type checker tests: inference, unions, None narrowing via `if x is None`, records, list/dict.
2. Fuel tests: an infinite `while` gives R620 at the budget; recursion depth; comprehension cost.
3. Runtime and codegen differential on a T2 program set: scalar procedures, whole-value procedures, effects via `for_each`, and T2-post reads.
4. Re-express the loop corpus native island (`l12`, the login-lockout state machine) as T2, and verify it equal to the original.
5. Importer: the carve-out rung R3 emits a T2 procedure instead of a native where the code fits the subset.
6. All existing tests pass. The dry run (t1–t12) is correct in both arms, including t12, now expressible.
7. Prompt guide: one concise T2 section, with equal-information baseline notes. Size delta reported.
8. Then **round 3** of the thin slice:
   - the 12 template tickets;
   - **6 fresh held-out template tickets (t13–t18)**;
   - **6 tickets on the held-out repositories** (3 on TaskFlow, 3 on JustNotes; `bench/import_repos/`), to test whether the language and profile mechanisms are generic rather than shaped by the template.
   
   Arm L's starting state on the held-out repos comes from the importer (lifters + the LLM close-the-gap loop, importer node I5), never from hand-written Lattice. Prompt caching is enabled in both arms. That makes 24 tickets × 2 arms × 2 models × 3 reps = 288 runs.

## 8. Build order

1. Round-2 fixes (§6): small, and they unblock round 3.
2. T2 type checker + subset validation.
3. Fuel-instrumented compilation (runtime + codegen) + T0 integration.
4. Tier reporting, hints, guide.
5. Loop corpus l12 + importer R3 → T2.
6. Review.
7. Importer I5 (LLM close-the-gap loop), needed to import the held-out repos for round 3.
8. Round 3.
