# Lattice sub-project 1b — Loop-shaped constructs and real-app gaps (design, for review)

> Status: DRAFT for review · 2026-09-27. Part of the import → Lattice → emit roadmap (`docs/archive/lattice-vision.md` §6). Builds on the language spec (`2026-09-26-lattice-design.md`), the API spec (`2026-09-27-lattice-api-design.md`) and sub-project 1 (`2026-09-27-lattice-realapps-design.md`, done: template proof 44/53 + named deviations).

## 1. Goal

Real code is full of loops. Almost all of them fall into a handful of shapes. Lattice should give each shape a checked construct with a guarantee, so that the importer (sub-project 2) can lift loops into Lattice instead of leaving them as native islands. Each construct:

- terminates by construction (a static bound, never "until it happens to stop");
- is deterministic (an explicit order wherever order matters);
- runs on every target: polars (pipelines), SQL (API reads stay one statement where the construct allows it) and generated FastAPI, all proven equal by differential tests.

Unbounded, general loops stay out of checked Lattice on purpose. They live in native functions, whose signatures are checked and whose bodies are trusted.

Part B also closes the language gaps found in the template proof, so the proof moves from 44/53 towards 53/53 with only policy deviations left.

## 2. Loop shapes → constructs

| Loop shape in real code | Example | Lattice construct | Guarantee |
|---|---|---|---|
| Carry a value across rows in order | running total, previous row, rank within group | `window(...)` + `win.*` | deterministic order (L431); bounded by input size |
| Repeat a step until nothing changes | propagate status, iterative clean-up, convergence | `iterate(step, t, until=..., max=N)` | at most N rounds; loud error when the bound is hit (R-code), never a silent partial answer |
| Walk parent/child links | org chart, category tree, bill of materials, reply threads | `traverse(edges, start, ..., max_depth=N)` | cycle-safe; depth ≤ N; one SQL statement (recursive CTE) |
| Do something for each row | send email, call webhook, enqueue job | `for_each(t, native.x, ...)` with effect natives | bounded row count; runs only after the transaction commits; an outbox makes it atomic with the write |
| Accumulate into a result | sum, count, max, collect | already covered by `group` + `agg.*` | — |
| Filter/map over a list | comprehensions | already covered by `where` / `derive` | — |

## 3. Part A — constructs

### 3.1 Window functions

```python
ranked = window(orders, by=["customer_id"], order=["created_at", "id"], desc=["created_at"],
                n=win.row_number(),
                prev_total=win.lag(col("total"), offset=1),
                running=win.cum_sum(col("total")),
                avg_3=win.mean(col("total"), rows=3))
```

- **New op:** `window(t, by=[...], order=[...], desc=[...], **cols)` → `Table[S ∪ {cols}]`. `by` may be empty (the whole table).
- **New namespace `win.*`:**
  - rankings: `row_number()`, `rank()`, `dense_rank()`;
  - offsets: `lag(x, offset=1)`, `lead(x, offset=1)`;
  - cumulative: `cum_sum(x)`, `cum_count()`, `cum_min(x)`, `cum_max(x)`;
  - moving frames: `mean(x, rows=N)`, `sum(x, rows=N)`, where the frame is the current row and the N−1 rows before it.
- **Semantics:**
  - Nulls sort last.
  - `lag`/`lead` past the edge of the partition give null (nullable result type).
  - `cum_*` skip nulls, like `agg.*`.
- **Checker rules:**
  - L430: `order` is required for any order-dependent function (all of the above).
  - L431 (warning in the tabular profile, error in the API profile): `by + order` is not proven unique, so `row_number`/`lag`/`lead` could be non-deterministic. The fix hint is to add a key column to `order`. Uniqueness comes from the P4 key tracking that L420 already uses.
  - L432: `win.*` used outside `window`, or `agg.*` used inside `window`.
- **Targets:**
  - SQL: `OVER (PARTITION BY … ORDER BY … ROWS BETWEEN N-1 PRECEDING AND CURRENT ROW)`.
  - polars: `.over()` with a sort.
  - Backend capability `window_functions`: L874 when missing.

### 3.2 `traverse` — bounded graph/tree walk

```python
reports = traverse(employees, start=where(employees, col("id") == manager_id),
                   parent="manager_id", child="id", max_depth=10)
# → the rows of `employees` reachable from start, plus a column `depth` (0 = the start rows)
```

- **Op:** `traverse(edges, start, parent=..., child=..., max_depth=N, direction="down"|"up")` → `Table[S ∪ {depth: Int}]`.
  - `start` must be a name. Following L301, the example above binds the `where` result first.
  - `direction="up"` walks towards the ancestors.
- **Semantics:**
  - Each node appears once, at its minimum depth (so cycles are safe).
  - Depth is at most `max_depth`.
  - Rows come out in `(depth, key)` order.
- **Checker rules:**
  - L433: `max_depth` must be an Int literal between 1 and 1000, or a bounded Int parameter.
  - L434: `parent`/`child` must be columns of the same type, and `child` must be a key.
  - In the API profile, ownership applies to `edges` as it does to `fetch`: `traverse` over an owned table requires `owned()` on its input (L802 unchanged).
- **Targets:**
  - SQL: `WITH RECURSIVE` with a depth counter and `UNION`, then `MIN(depth)` per node. This keeps one statement per read.
  - polars: bounded BFS in the executor.
  - Later: Mongo `$graphLookup` with `maxDepth` (sub-project 4).
  - Capability `recursive_queries`: L875 when missing.

### 3.3 `iterate` — bounded fixpoint

```python
@step
def settle(accounts: Table[Accounts]) -> Table[Accounts]:
    ...  # one round: a pipeline whose output schema equals its input schema
    return next_accounts

settled = iterate(settle, accounts, until="fixpoint", max=50)
```

- **Op:** `iterate(step, t, until="fixpoint" | <Col[Bool]>, max=N)`.
  - `step` is a `@step` of type `Table[S] -> Table[S]` (L435 otherwise).
  - With `until="fixpoint"`, iteration stops when a round returns the same rows (as a multiset).
  - With a predicate, iteration stops when every row satisfies it.
- **Semantics:**
  - At most `max` rounds.
  - If the stop condition is still false after `max` rounds, the run fails loudly with R-code **R610** ("iterate did not converge in N rounds"). It never returns a partial answer as if it had converged.
  - `max` must be an Int literal ≤ 10,000 (L436).
- **Targets and scope:**
  - Tabular profile: polars loop in the executor.
  - API profile: **not allowed in v1b** (L437). A read must stay one statement, and a round loop on the database would break the one-statement and bounded-latency guarantees. Tree/graph walks use `traverse`; anything else is a native.
- **Why not general `while`:** a fixpoint with a static cap covers convergence-style code. Arbitrary loop state (counters, several accumulators) is what makes loops hard to check and is where models make mistakes. The importer maps such loops to natives and reports them in its coverage report.

### 3.4 `for_each` — batched side effects after commit

```python
# ApiProfile.natives: "send_welcome": NativeSig(args=(Scalar.STR, Scalar.STR), returns=None, effect=True)
created = insert(User, email=email, full_name=full_name, ...)
for_each(created, native.send_welcome, col("email"), col("full_name"))
return created
```

- **`NativeSig` gains `effect: bool`.** Effect natives:
  - can only be called through `for_each` (L438);
  - are never usable inside an expression, so pure natives stay pure and can still be SQL functions.
- **Semantics (outbox):**
  - `for_each` writes one outbox row per input row, in the same transaction as the endpoint's writes. If the endpoint rolls back, nothing is sent.
  - After commit, the runtime dispatches the rows in order. Each row is marked `done` or `failed` with its error.
  - The HTTP response does not depend on the dispatch outcome.
  - A `lattice outbox retry` command re-dispatches failed rows.
  - Delivery is at-least-once, so natives must be idempotent. This is documented and flagged in the prompt guide.
- **Checker rules:**
  - L439: the input of `for_each` must be bounded: the result of `one`, `insert` or `update` of one row, or `page`/`limit` with n ≤ 100.
  - `for_each` is a statement, not a value.
  - Effect natives count as effects in P5.
- **Targets:**
  - Runtime and generated FastAPI get the same outbox table and dispatcher; the differential test compares outbox contents.
  - Tabular profile: `for_each` is not allowed (only `write` is an effect there).

## 4. Part B — real-app gaps from the template proof

| # | Gap (template proof) | Addition | Removes deviation |
|---|---|---|---|
| B1 | No "active user" at the auth level; the check runs after role checks and validation | `ApiProfile.active_column="is_active"` (+ `inactive_status=400`, `inactive_detail`): checked in auth, before the role check and parameter validation | `inactive-before-role`, `inactive-before-validation`; −8 lines of repeated `require` |
| B2 | Error body shape fixed | `ApiProfile.error_body="lattice"` (default) \| `"fastapi"` (`{"detail": ...}`, with automatic 401/403 detail texts configurable) | `ERROR_ENVELOPE` rule, `automatic-401-body`, `automatic-403-body` |
| B3 | DateTime wire spelling fixed | `ApiProfile.datetime_wire="iso_z"` (default) \| `"naive"` | `DATETIME_WIRE` rule |
| B4 | No format-validated params | Types `Email` (a Str subtype) and `Str` with `pattern=` in `Annotated`-style bounds; validation errors are 422 | `email-format` |
| B5 | Uniqueness pre-check impossible over `UNSET` | `unique_or(Conflict("..."))` on `insert`/`update`: maps the store's UNIQUE violation to the declared `Conflict` detail (the store already enforces it, and 409 mapping already exists) | `email-conflict-detail` |
| B6 | No list constants in API programs | Allow module-level `Str` list constants (`PUBLIC = ["id", ...]`) usable in `select(cols=PUBLIC)`; still single-assignment | — (removes duplicated lists) |

The policy deviations stay: page cap, 401 for a bad token, and 401 for an unknown user. They are Lattice's guarantees, not gaps.

Out of scope: natives that write to the database (rehashing a password on login). They stay a native-island case for the importer.

## 5. Checker, capability and IR changes

- **New codes:**
  - L430–L439 (above);
  - L874 (`window_functions` for `window`);
  - L875 (`recursive_queries` for `traverse`);
  - L876 (`for_each` without `transactions`, because the outbox needs atomicity);
  - R610 (iterate did not converge).
- **`Capabilities` gains `recursive_queries: bool`.** SQLite: true.
- **Plan IR gains nodes:** `Window`, `Traverse`, `Iterate` (tabular only) and `ForEach` (API only). The SQL compiler emits them inside the existing CTE chain.
- **Prompt guide** (`lattice/llm/api_prompt.py` and the tabular guide) gets one short section per construct, with one example each. Every rejection hint names the construct to use, e.g. a `for` statement gets L1xx with a hint pointing to `window`/`traverse`/`iterate`/`for_each`.

## 6. Testing and acceptance

1. Checker unit tests: one positive and one negative test per new code.
2. Semantics tests per construct:
   - window: ties, nulls, partition edges, moving frame shorter at the start;
   - traverse: cycles, diamond graphs (minimum depth), `max_depth` cut-off, `up` direction;
   - iterate: fixpoint in k rounds, R610 at the cap;
   - for_each: rollback sends nothing, failure marks the row and keeps the 200, retry.
3. Differential tests:
   - SQL vs polars for `window` and `traverse` on generated tables (hypothesis);
   - runtime vs generated FastAPI for `window`, `traverse` and `for_each` (outbox contents compared).
4. Template proof re-run with B1–B6: target **53/53 matched except the 5 policy deviations** (page cap ×2, bad token ×2, unknown-user token). Each remaining deviation is still named and asserted.
5. A small loop corpus: about 12 hand-picked Python loops from real FastAPI/SQLModel code (running balances, category trees, retry-until-stable, notification fan-out). Each is written in Lattice and checked equal to the original on generated inputs. This corpus becomes the importer's first test set (sub-project 2).
6. `uv run pytest -q` green, `ruff` clean, codegen differential 0 mismatches.

## 7. Build order

1. Part B (small, unblocks the template proof): B1, B2, B3, B4, B5, B6.
2. `window`.
3. `traverse`.
4. `for_each` + outbox.
5. `iterate` (tabular).
6. Loop corpus + prompt guide + template re-run + review.
