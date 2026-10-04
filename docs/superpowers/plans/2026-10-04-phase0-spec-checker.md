# Phase 0 plan: spec v0 and the four-layer checker on Python

> Roadmap: `docs/lattice-direction-executable-spec.md` §8, Phase 0. Branch: `roadmap`
> (from `develop` @ `80aa363`).
>
> **Defaults taken for the direction draft's §10 open questions** (the user may change them):
> - specs keep today's Python syntax (a `Spec` object in a Python module);
> - JS/TS comes before C#, and Go after both;
> - Phase 1 simulates the user's approval with a held-out set of approved rules;
> - the CLI comes before the CI action and the MCP server.
>
> **Process:**
> - tasks run as a dependency graph of agents in isolated worktrees, test-first;
> - agents never run tree-modifying git commands; the orchestrator reviews, merges and
>   commits after each gate;
> - the language rules (`docs/lattice-language-rules.md`) apply to every new construct.

## Exit criteria (from the roadmap)

1. **The spec checks clean against its own code.** The multi-vendor app's spec
   (`bench/specs/multivendor.py`) checks clean against the original app: layers A–D, 0
   findings, except the app's own baseline.
2. **Every trap is caught.** Every option-1 trap variant (t31–t44, `TRAPS` in
   `tests/bench/test_edit_slice_option1.py`) is caught by at least one layer. Each ticket's
   spec delta states that ticket's requirement.
3. **The suite passes.** The full test suite passes, and the Go/TS gates and the language
   rules are respected.

## Graph

```
wave 1 (parallel)           wave 2 (parallel, after wave-1 merge)      wave 3
 W1-A spec model +            W2-A check engine (B/C/D) + CLI ──┐
      invariant compiler ──►                                    ├──► W3 exit tests,
 W1-B nested responses ────►  W2-B multivendor spec + ticket ───┘     results, commit
 W1-C pycheck PY808 (state        spec deltas
      machines) ────────────►  (wiring PY808 to the spec: W2-A)
```

## Wave 1

### W1-A: `lattice/spec`, the spec model and invariant compiler (new package)

**Files:** `lattice/spec/__init__.py`, `model.py`, `invariants.py`, `validate.py`;
`tests/spec/test_model.py`, `test_invariants.py`.

**Model (frozen dataclasses):**
- `Machine(table: str, column: str, edges: tuple[tuple[str, str], ...], initial: tuple[str, ...] = ())`.
  States are the column's `one_of` values. Edges are (from, to) between those values.
- `Invariant(name: str, sentence: str, expr: str)`. `expr` is a restricted expression in
  Lattice/Python syntax, parsed with `ast`:
  - a row rule: `Product.stock_quantity >= 0`, `Product.price > 0`, with `and`/`or`,
    `is_null` and `in`;
  - an aggregate equality: `Order.total_amount == sum(OrderItem.quantity * OrderItem.price_at_purchase, by="order_id")`,
    compared with a float tolerance of 1e-9 relative, where an order with no lines sums
    to 0;
  - a count bound: `count(Vendor, by="user_id") <= 1`.
- `Scenario(name, given: Mapping[str, list[dict]] | None, steps: tuple[Step, ...])`.
  `Step` is `(method, path, user: str | None, json, params, expect_status: int, expect_body: Any | None, expect_db: Mapping[str, Any] | None)`.
  `expect_db` uses a small form: `{"orders": {"count": 9}}` or `{"products": {"where": {"id": 2}, "equals": {"stock_quantity": 30}}}`.
- `Unchecked(text: str, concerns: tuple[str, ...] = ())`.
- `Spec(profile: Profile, programs: Mapping[str, str] = {}, machines=(), invariants=(), scenarios=(), unchecked=(), users: Mapping[str, tuple[str, str]] = {})`.
  `programs` are endpoint programs, the executable behaviour. `users` maps a handle to
  login credentials for scenarios.

**Compiler:** `compile_invariant(inv, profile) -> str` gives SQL that returns the
*violating* rows (empty when the invariant holds). Table and column names come from
`profile.api.table_names` and the schemas. One-of columns with stored spellings must
compare the stored spelling.

**Validation:** `validate(spec) -> list[Diagnostic-like]`. It checks that:
- tables and columns exist;
- machine states are within the column's `one_of`;
- invariant expressions parse and use only the allowed forms;
- scenario users exist in `users`;
- program names are unique.

**Tests:**
- on a hand-built SQLite file, each invariant form returns exactly the violating rows;
- every validation error has a test;
- a valid multi-vendor-like spec has no errors.

### W1-B: nested response models (a language feature)

**Goal:** an endpoint can answer an object with a nested list from a child table, e.g. an
order with its `items` (the shape of `OrderRead`), and a `Page` of such objects. This is
the gap that blocked arm L in option 1. Design it inside the existing API profile:
`ApiProfile.responses` gains nested fields, e.g. `responses["OrderRead"] = ("id", ...,
Nested("items", "OrderItemRead", via="order_id"))`. Choose the smallest design that
compiles to **one SQL statement per read** (a correlated `json_group_array` sub-select on
SQLite), keeping that guarantee.

**Files:** `lattice/profile.py`, `lattice/check/*` (checker rule plus a new diagnostic code
with an exact fix hint), `lattice/api/ir.py`, `lattice/api/sql.py`, `lattice/api/runtime.py`,
`lattice/api/codegen.py`, `lattice/check/p8_target.py` + `lattice/targets.py` (gate on Go
with a decision-log row in `docs/lattice-language-rules.md`, unless the Go runtime gets
it), tsgen (support it or gate it).

**Tests:**
- a differential fixture `tests/api/nested.py` registered in
  `tests/api/test_codegen_differential.py`: the runtime and the generated FastAPI app give
  equal answers;
- checker tests for every new diagnostic;
- the Go differential and TS gates updated, as was done for `rolesets`;
- no existing test changes behaviour.

### W1-C: pycheck PY808, state machines (Python layer A)

**Files:** `lattice/pycheck/policy.py`, `checks.py`, `findings.py`, `describe.py`;
`tests/pycheck/test_pycheck_checks.py` (demo app).

The policy gets a field `machines: Mapping[str, tuple[str, frozenset[tuple[str, str]]]]`:
`table -> (column, legal edges)`. **PY808** flags a write of a literal or enum member to
`<row>.<column>` in a route or helper. It flags only when every state the row can be in
at that point is known from a dominating check in the same function (`if
order.status != X: raise`, or `order.status in (...)`) and an edge from those states to
the written state is missing. It is **silent when unsure**.

**Tests:**
- positive and negative cases on the demo app (add a `status` column with a machine);
- the original demo app has 0 findings;
- the precision gate: 0 PY808 findings on the t31–t44 references (multi-vendor; the
  policy machine is Order.status with the upstream `VALID_TRANSITIONS`).

## Wave 2

### W2-A: the check engine and CLI

**Files:** `lattice/spec/check.py`, `lattice/spec/report.py`, `lattice/spec/__main__.py`;
`tests/spec/test_check.py`.

`check(spec, app_factory, db_factory, *, layers="ABCD", files=None, repo=None) -> Report`:
- **A:** pycheck on `files` (the app's source) with a policy built from the spec: the
  spec's owners, private columns, roles, machines and error answers, plus the derived
  ones.
- **B:** for each endpoint the spec has a program for, run the importer's suite generator
  (every role's user, other users' rows, bounds) against the spec runtime (the reference)
  and the app (`lattice.importer.verify.verify`), and compare.
- **C:** after every request of B and D, run every invariant's SQL on the app's database.
  Each violation names the invariant, the request (case) and the rows.
- **D:** run the scenarios against the app, with `expect_*` checked.

The report is JSON with fields matching `docs/design/lattice-visualization-figma-spec.md`
§10, plus `render()` for humans. The CLI is `python -m lattice.spec check --spec mod:SPEC
--app mod:factory --db mod:build_db [--files DIR] [--json]`.

### W2-B: the multi-vendor spec and the ticket spec deltas

**File:** `bench/specs/multivendor.py`. It contains:
- the profile: start from `bench/imports/multivendor/profile.py`, reviewed as the user
  would, with corrections recorded in comments;
- the Order.status machine (from upstream `VALID_TRANSITIONS`);
- invariants: order total = Σ lines, stock ≥ 0, price > 0, one vendor per user;
- programs for the existing endpoints Lattice can express (with nested responses from
  W1-B);
- scenarios for the existing routes;
- an `unchecked` list.

**Ticket deltas:** `bench/specs/multivendor_tickets.py`, `DELTAS[ticket] -> Spec delta`
(added programs, invariants and scenarios) stating each t31–t44 requirement as its ticket
text does. Write the deltas **from the ticket texts**, not from the oracle cases, the way a
user would state a ticket.

## Wave 3: exit

Tests in `tests/spec/test_phase0_exit.py`:
- (1) the spec is clean against the original;
- (2) every trap variant of t31–t44 is caught by at least one layer, by its spec plus
  delta. The layer that caught each is recorded, and the per-layer catch table goes in
  the results log.

Then: the full suite, a results-log entry, a commit, and a note to the user.
