# Lattice — Design Draft v0 (for review)

> Status: APPROVED design (v1 scope) · Date: 2026-09-26 · Source: `docs/LLM-Native Coding Language Feasibility v1.md`
> Next step: implementation plan for Phases 0–3.

---

## 1. Context

The source report asked: *should we build a new, general-purpose programming language designed for LLMs?* Its answer:

- **No-go** on a new text language — tokenizer fragmentation, zero training-data footprint, and the "flexibility fallacy" (more ways to say the same thing → higher branching factor → more errors in multi-step logic).
- **Go** on a pivot: constrain what the model may emit, validate it structurally, compile it deterministically, optionally verify it formally, and route cheaply in front.

Central thesis we are building on: **reliability comes from shrinking the model's decision space, not from expressive syntax.** The LLM is a semantic parser; deterministic tools own validation, formatting, and execution.

### 1.1 What we take from the report (and what we don't)

| Report claim | Our position |
|---|---|
| Constrained, canonical forms beat flexibility (Anka: 100% vs 60% on multi-step) | **Adopt** — core design principle. Re-measure ourselves; one 100-task benchmark is thin evidence. |
| Emit JSON ASTs instead of text | **Modify** — JSON is token-heavy, contradicting the report's own tokenization argument. We constrain an *existing* language's AST and let the model emit that language's familiar text (see §3). |
| Grammar-constrained decoding (XGrammar/SGLang) | **Defer** — only applies to self-hosted models; API models use validate→repair instead. Phase 4. |
| Lean 4 gives "absolute correctness" | **Reject as stated** — proofs only show conformance to a written spec. Scoped down to lightweight property/contract checking on a fragment. Phase 6. |
| "System One" router (Jev) | **Defer** — orthogonal to the core; sources unverifiable. Phase 7, if ever. |
| Multi-target codegen (Go, React, Python, C#) | **Defer** — start with one target (the base language itself); add a second only after the core is proven. |

---

## 2. Goals, non-goals, success criteria

**Goal.** A system that takes a natural-language task, has an LLM produce a program in a *constrained profile* of an existing language, statically proves the program obeys the profile's rules, repairs it via diagnostics when it doesn't, and executes it deterministically.

**In scope (v1).** Straight-line op pipelines (L0), the canonical conditional + expression library (L1, §5.10), and typed non-recursive user-defined steps (L2, §5.9) — all within a single file.

**Non-goals (v1).** New surface syntax; custom tokenizer; fine-tuning; general-purpose programming (no loops, no recursion, ever); multi-file modules and third-party packages (designed in §15, built after the gate); multiple codegen targets; formal proofs; routing layer.

**Success criteria.**
1. On a benchmark of ≥100 domain tasks, Lattice-generated programs beat unconstrained base-language generation (same model, same prompt budget) on **multi-step accuracy by ≥15 points**, with no regression on single-step tasks.
2. ≥99% of emitted programs pass the checker within ≤2 repair rounds.
3. Every checker diagnostic has a `line:col` span and a stable error code.
4. Zero host `eval`/`exec` of unchecked code.

---

## 3. Decisions

### 3.1 DECIDED — Representation: AST of an existing language (user decision)

The model writes ordinary source text of the base language. We parse it with the base language's own parser into its AST, and a **profile checker** rejects any tree outside the allowed subset. Consequences:

- ✅ Model stays in-distribution (no zero-token-footprint problem, no tokenizer fragmentation).
- ✅ No parser to write; spans come free from the host parser.
- ✅ Output is valid host code — runnable, readable, diffable by humans.
- ⚠️ The model's *prior* pulls toward banned idioms (comprehensions, chaining). Mitigation: explicit profile in the prompt + few-shot + fast repair loop with precise diagnostics. Measured in the benchmark (repair-round count).
- ⚠️ Branching factor is only reduced if the profile is **tight**. A loose subset gives no benefit. Rules in §5 are deliberately strict.

"DSL vs AST" clarified: the AST node whitelist + checker rules *are* the DSL. We're defining a DSL as a **profile over an existing abstract syntax**, not inventing a new one.

### 3.2 OPEN — Base language (recommendation: **Python**)

| Option | For | Against |
|---|---|---|
| **Python** (rec.) | Highest model familiarity (the report's 90–97% bias works *for* us); stdlib `ast` gives parse, spans (`lineno`/`col_offset`), and `ast.unparse`; the Anka failure modes (shadowing 42%, sequencing 31%, chaining 27%) are exactly what a checker can ban. | Dynamic typing — we must supply our own static type layer (§5.5). |
| TypeScript | Static types in host; TS compiler API. | Heavier toolchain (Node); weaker fit for data work. |
| Go | Already close to "one way to do it"; `go/ast` built in. | Lower model familiarity; verbose error handling inflates tokens. |

The rest of this draft assumes Python; the architecture is language-independent except §5 and §6.

### 3.3 OPEN — First domain (recommendation: **tabular data pipelines**)

A profile only lowers branching factor if it is domain-scoped. Data pipelines are where the report's evidence lives (Anka), multi-step failures are measurable, and outputs are checkable by comparing result tables. Alternatives (backend logic, UI components) are broader and harder to benchmark.

---

## 4. Architecture

```
 task (NL) ──► Prompt Builder ──► LLM ──► source text
                  ▲  (profile spec,                │
                  │   op catalog,                  ▼
                  │   few-shot)            ┌─ Parse (host ast) ─┐
                  │                        │  syntax diagnostics │
                  │                        ▼                     │
                  │                 Profile Checker (passes)     │
                  │                   P1 node whitelist          │
                  │                   P2 name resolution / SSA   │
                  │                   P3 canonical-form rules    │
                  │                   P4 schema & type check     │
                  │                   P5 termination / effects   │
                  │                        │                     │
                  └── Repair Loop ◄── diagnostics (all, spanned) ◄┘
                      (≤ N rounds)         │ ok
                                           ▼
                                  Canonicalizer (ast.unparse + fmt)
                                           ▼
                                  Plan Builder (AST → step DAG IR)
                                           ▼
                                  Executor (op library, sandboxed)
                                           ▼
                                      result tables
 Benchmark Harness drives all of the above and a no-profile baseline.
```

### 4.1 Units (each independently testable)

| Unit | Responsibility | Input → Output | Depends on |
|---|---|---|---|
| `profile` | Declarative profile definition: allowed nodes, op catalog with signatures, rule set, version | — → `Profile` object | nothing |
| `parse` | Host parse + span extraction; wraps `SyntaxError` into a diagnostic | `str` → `Module` \| `[Diagnostic]` | stdlib `ast` |
| `check` | Runs passes P1–P5, **collects all diagnostics**, returns decorated program | `Module, Profile` → `CheckedProgram` \| `[Diagnostic]` | `profile` |
| `diagnostics` | `Diagnostic{code, severity, span, message, hint}`; renderers for humans and for the LLM | — | nothing |
| `canon` | Canonical pretty-print (deterministic formatting) | `CheckedProgram` → `str` | `ast.unparse` |
| `ir` | Lower checked AST to a **step DAG**: nodes = op calls, edges = data deps | `CheckedProgram` → `Plan` | `check` |
| `ops` | The runtime op library (the domain vocabulary) | table values → table values | polars (§10) |
| `exec` | Evaluate a `Plan` in topological order; no host `eval` | `Plan, inputs` → outputs | `ir`, `ops` |
| `llm` | Prompt building, model call, repair loop | task → program text | Claude API |
| `bench` | Task suite, runners (Lattice vs baseline), metrics, reports | tasks → report | all |

Boundaries: `check` never executes; `exec` never sees unchecked code; `llm` never touches the runtime.

---

## 5. The profile (the actual DSL)

### 5.1 Shape of a program

A program is a module of **top-level single-assignment statements**, each calling exactly **one catalog op** whose table arguments are **names**, never nested calls. This is Python spelling of Anka's `STEP … INTO name`.

```python
# lattice: v1
# (no imports needed: ops, col, fn, agg, types and schema names are ambient — §5.11 item 12)
raw_orders       = load("orders.csv", schema=Orders)
paid_orders      = where(raw_orders, col("status") == "paid")
orders_w_total   = derive(paid_orders, total=col("qty") * col("price"))
revenue_by_region = group(orders_w_total, by=["region"], revenue=agg.sum(col("total")))
write(revenue_by_region, "revenue.parquet")
```

The same logic as the model would naturally write it, all banned:

```python
df = pd.read_csv("orders.csv")
df = df[df.status == "paid"]                                    # rebinding (shadowing)
out = df.assign(total=lambda d: d.qty * d.price).groupby("region").total.sum()  # lambda + chaining
```

### 5.2 P1 — Node whitelist

Allowed: `Module`, `ImportFrom` (optional; only `from lattice.ops import …` / `from lattice.schemas import …`, first statement block only), `Assign` (single `Name` target), `Expr` (only a bare terminal op call, e.g. `write`), `Call`, `Name`, `Constant`, `List`/`Tuple` of constants, `keyword`, `Compare`/`BinOp`/`BoolOp`/`UnaryOp` **only inside column expressions** (§5.5), `Attribute` **only** as `agg.<fn>` / `fn.<name>` (§5.10). `FunctionDef` **only** with exactly the `@step` decorator, fully annotated, whose body obeys §5.9; `Return` only as the last statement of a step.

Everything else is rejected with `L1xx` codes: undecorated/other-decorated `def`, nested `def`, `class`, `lambda`, comprehensions, `for`/`while`/`if` statements, conditional expressions (`a if c else b` — use `when`), `with`, `try`, `global`/`nonlocal`, `import`, subscripts, starred args, default parameter values, f-strings, walrus, augmented assignment, method calls, `async`, `yield`.

### 5.3 P2 — Names and binding (static, single-assignment)

- Scoping: **static** (lexical) resolution in a pass after parsing — one module scope, plus one scope per `@step` body (§5.9).
- Every name is bound **exactly once** (`L201 rebinding`). This *is* the no-shadowing rule.
- **Ambient names.** Every catalog name (the ops, `col`, `when`, `coalesce`, `is_null`, `fn`, `agg`, `step`, `Table` and the scalar type names) and every schema name in the profile is predeclared in the module-level scope and visible everywhere, including step bodies. Imports are optional (§5.11 items 1, 12).
- Every other use must be bound **earlier in the file** (`L202 use-before-def`, `L203 unknown name` — only for names that are neither ambient nor bound; did-you-mean and alias hints such as `filter` → `where`).
- **Named constants.** `name = <column-free scalar expression>` binds a constant (kind "const"; §5.11 item 13). Module constants live in a scope between the ambient names and the module tables, so step bodies can read constants defined *above* the step (a constant defined further down is `L202`) but still not module tables (`L603`).
- Every binding must be **used later** or be a terminal op (`L204 dead step` — warning; never for ambient names).
- Binding a name equal to an ambient name (a table, step, step parameter or local named `where`, `col`, `Orders`, …) or a Python builtin is rejected (`L205`, "shadows a built-in Lattice name").

### 5.4 P3 — Canonical-form rules (one way to say each thing)

| Rule | Code |
|---|---|
| A table-typed argument must be a `Name`, never a nested op call (no chaining) | `L301` |
| Exactly one op call per statement (a named constant — a column-free scalar expression — is the only other statement; §5.11 item 13) | `L302` |
| Every op call uses its one fixed call shape (§5.11 item 2): table input(s) positional, `where`'s predicate positional, every other parameter by keyword; step calls pass tables positionally and scalars by keyword | `L303` |
| No no-op steps: `where(t, True)`, `derive(t)` with no columns, `derive(t, a=col("a"))`, and similar identity ops are rejected (two consecutive `where` steps remain legal) | `L304` |
| Column references only via `col("name")`; no bare strings where a column is expected | `L305` |
| ~~Import list must exactly match ops used~~ — removed (§5.11 item 12); imports are optional | ~~`L306`~~ |

### 5.5 P4 — Types and schemas (synthesized bottom-up)

Types are few and distinct, no implicit coercion:

- Scalar: `Int`, `Float`, `Str`, `Bool`, `Date`, `Null`-able variants `T?`
- `Col[T]` — a column expression yielding `T`
- `Table[S]` — `S` is a **schema**: ordered map `column → scalar type`

Each op in the catalog has a typed signature **with a schema transfer function**, e.g.

- `where(t: Table[S], pred: Col[Bool]) -> Table[S]`
- `derive(t: Table[S], **new: Col[T_i]) -> Table[S ⊕ {new_i: T_i}]` — a new name is appended; an existing name is **replaced in place** (same position, type `T_i`). Every `new_i` is typed and evaluated against the **input** table `S`: a later keyword does not see an earlier keyword's new value (§5.11 item 12)
- `group(t: Table[S], by: list[col∈S], **aggs: Agg) -> Table[{by…} ∪ {agg names}]`; aggregates are `agg.sum/mean/min/max(x)`, `agg.count()` (rows) and `agg.count(x)` (non-null values of `x`, any scalar type) → `Int` (§5.11 item 13)
- `join(left: Table[A], right: Table[B], on: list[str], how: "inner"|"left") -> Table[A ⊎ B]` (collision error). With `on=[]` the right table must be statically *at most one row* (`TableT.at_most_one_row`, see §5.11 item 13), else `L404`; the result is `A ++ B` (every right column attached to every left row), at most one row iff `left` is
- Row-count flag: `group(t, by=[])` and `limit(t, n=1)` are at most one row; `where`/`derive`/`select`/`rename`/`sort`/`limit` preserve their input's flag; a step call carries its body's inferred flag; `load` and keyed `join` are not. The flag never takes part in schema matching (`L418`, `L605`)

So the checker knows every table's schema at every step, and rejects `col("prce")` (`L401 unknown column`, with did-you-mean), `col("qty") + col("region")` (`L402 Int + Str`), `where(t, col("qty"))` (`L403 predicate must be Bool`), arity/keyword errors (`L404`). Source schemas come from `schema=` declarations in the profile's schema registry (a typed Python dataclass per input), so **static checks happen before any data is read**.

Implementation follows the classic decorated-AST pattern: schema/type is a *synthesized* attribute stored in a side table keyed by node; the environment (name → `Table[S]`) is *inherited* down the statement list. An error yields an `Unknown` type that suppresses cascades, and checking continues so **all** errors are reported in one pass.

### 5.6 P5 — Termination and effects

- No loops exist, and user-defined steps cannot recurse (§5.9, `L604`) → every program terminates; the plan is a finite DAG.
- Effects: only `load` (read) and `write` (write) touch I/O; both take paths checked against an allowlisted root (`L501`). `write` must be terminal.

### 5.7 Written runtime semantics (decided, tested)

| Case | Semantics |
|---|---|
| Integer / float division by zero | result `null` for that row + runtime warning counter (not a crash) |
| NaN | normalized to `null` at `load` |
| `null` in arithmetic | propagates (`null + 1 = null`) |
| `null` in predicates | `where` treats `null` as false (SQL 3-valued logic) |
| `and`/`or` in column exprs | Written as `&`/`|` on `Col[Bool]` only; checker rejects non-Bool operands |
| Aggregates over empty group | `sum → 0`, `count → 0`, `mean/min/max → null` |
| Evaluation order | Topological order of the step DAG; deterministic tie-break by source order |
| Equality | Value equality; float equality allowed but `L406` warning |

### 5.8 Versioning

First line `# lattice: v1` is mandatory (`L001`). Op catalog and rule set are versioned together; a program is checked against the profile version it declares.

### 5.9 L2 — User-defined steps (`@step`)

Reusable, typed sub-pipelines. A step is a function whose body is itself a §5.1 pipeline.

```python
@step
def paid_revenue(orders: Table[Orders], min_qty: Int) -> Table[RegionRevenue]:
    paid      = where(orders, (col("status") == "paid") & (col("qty") >= min_qty))
    w_total   = derive(paid, total=col("qty") * col("price"))
    by_region = group(w_total, by=["region"], revenue=agg.sum(col("total")))
    return by_region

raw_orders = load("orders.csv", schema=Orders)
revenue    = paid_revenue(raw_orders, min_qty=2)
write(revenue, "revenue.parquet")
```

Rules:

| Rule | Code |
|---|---|
| Every parameter and the return type annotated with profile types (`Table[S]`, `Int`, `Str`, …); no defaults, no `*args/**kwargs` | `L601` |
| Body = single-assignment op statements followed by exactly one `return <name>` | `L602` |
| **Lexical scope, no free table variables**: a step body may reference only its parameters, its own locals, catalog ops, schemas, *earlier-defined* steps and module constants defined above it (§5.11 item 13) — never module-level tables (steps are pure, cacheable, testable in isolation) | `L603` |
| Step must be defined **before** use in file order. This alone makes the call graph acyclic; self-reference inside its own body is rejected explicitly | `L604` |
| Declared return schema must equal the body's synthesized schema (exact, column order included) | `L605` |
| Calls follow op rules: table args are names (`L301`), scalar args by keyword | reuse |

Semantics: call-by-value on immutable tables (no aliasing hazards); a step call inlines into the plan DAG as a named sub-graph (`ir` keeps the boundary for diagnostics and caching). Termination is preserved: no loops + acyclic call graph ⇒ finite DAG.

Checker mechanics: P2 builds one scope per step chained to a module scope that contains only ops/schemas/steps (not tables) — this is how `L603` is enforced structurally rather than by special cases. Step signatures are entered into the module scope when their `def` is *completed*, which yields `L604` for free.

### 5.10 L1 — Conditional and expression library

All inside column expressions (`Col[T]`); nothing here introduces statements.

- **Conditional (the only one):** `when(cond: Col[Bool], then: Col[T], otherwise: Col[T]) -> Col[T]`. Multi-branch = nesting **only in `otherwise`** (`L701` if nested in `then`), giving one canonical shape for if/elif/else. `then`/`otherwise` must unify to one `T` (`Int` ↔ `Float` is the only permitted widening, and it's explicit in the signature table).
- **Null handling:** `coalesce(a, b)`, `is_null(a)`; no other null idioms.
- **Function library** under one namespace `fn.*`, each with a typed signature in the catalog:
  - math: `fn.abs, fn.round(x, digits=), fn.floor, fn.ceil, fn.log, fn.sqrt, fn.clip(x, lo=, hi=)`
  - string: `fn.lower, fn.upper, fn.strip, fn.len, fn.contains(s, sub=), fn.starts_with, fn.replace(s, old=, new=), fn.concat(a, b)`
  - date: `fn.year, fn.month, fn.day, fn.date_diff(a, b, unit=), fn.date_add(d, days=), fn.to_date(s, format=)`
  - cast: `fn.to_int, fn.to_float, fn.to_str` (the only way to convert — no implicit coercion)
- Math-domain errors (`log(0)`, `sqrt(-1)`, failed casts) → `null` + runtime warning counter, consistent with §5.7.
- Catalog growth rule: a new `fn` is added only with a distinct intent — no aliases (`L702` if a program uses a removed/aliased name, with a hint to the canonical one).


### 5.11 Clarifications (decided by the Phase 0–3 plan)

1. **Imports.** Exactly two modules may be imported from: `from lattice.ops import …` (the ops, `col`, `when`, `coalesce`, `is_null`, `agg`, `fn`, `step`, `Table`, and the scalar type names) and `from lattice.schemas import …` (input schema names from the profile). No aliases, no wildcards, no relative imports. Unknown imported names are `L112` (with alias hints), other modules / plain `import` are `L103`, imports after other statements are `L102`, aliases `L104`. Imports are optional and an unused import is fine (`L306` was removed — item 12).
2. **Canonical call shapes (refines `L303`).** Each op has one fixed signature saying which parameters are positional and which are keyword. Table inputs are positional; `where(t, pred)` takes its predicate positionally; everything else that is not a table is passed by keyword (e.g. `load(path, schema=S)`, `join(l, r, on=[…], how="left")`, `write(t, path)`).
3. **Step annotations.** A step's return annotation may be bare `Table` (schema inferred from the body); parameters must be concrete (`Table[Orders]`) or scalar (`Int`, …). A concrete return type is checked exactly (`L605`).
4. **Nullability.** Every column is nullable in v1; the `T?` distinction is deferred.
5. **Output files.** `write` accepts `.csv` or `.parquet` paths (extension case-insensitive, and the file name must have a stem: `".parquet"` is `L505`); paths containing control characters (NUL, newline, …) are `L501`. The benchmark always uses `out.parquet`.
6. **Table positions.** A table argument (and a step's `return`) must name a table binding; a schema, import, type, op or step name there is `L419` (`L602` for `return`), so every accepted table argument resolves at lowering.
7. **Literals.** A *literal* is a bool/int/float/str constant or a (possibly repeatedly) negated number (`-1`, `--2.0`); the checker, IR and runtime all fold the same way. `fn.*` keyword arguments must be literals, a scalar step parameter or a named constant bound to a literal (item 13) — computed values (`days=5 % 0`, `sub=fn.to_str(1)`) are `L404`. `fn.round(digits=)` must be in 0..2³²−1 (`L404`; a step argument out of range is runtime `R103`). Int literals must fit in 64 bits (`L404`).
8. **`fn.clip`.** The result is Float when either bound is Float (the column is cast); Int bounds on an Int column stay Int.
9. **Date step parameters.** Rejected at the definition (`L601`): there is no Date literal to pass. Take a `Str` parameter and convert inside the step with `fn.to_date(s, format=...)`. PEP 695 type parameters on a step are also `L601`.
10. **Limits.** Nesting deeper than 100 AST levels is a single `L003` (the checker never raises). At runtime an op whose output exceeds the row limit is `R201`; a `join` is sized exactly from per-key counts and refused *before* it runs. The time limit (`R202`) is checked between ops, so a single long op is not interrupted.
11. **No-ops (`L304`)** also include `sort(t, by=[])`, renaming a column to itself, and deriving a column from itself (`derive(t, a=col("a"))`).
12. **Ambient names and derive overwrite (post-benchmark change).** A live benchmark on claude-haiku-4-5 showed first-shot Lattice failures dominated by bookkeeping rules rather than the rules the design targets: `L203` missing import (18 tasks), `L405` derive reusing an existing column (15), `L306` unused import (11). Two changes remove that friction:
    - *Ambient names.* Every name in `lattice.catalog.IMPORTABLE` and every profile schema name is predeclared in the module-level (globals) scope, visible everywhere including step bodies. `from lattice.ops/schemas import …` stays valid but optional; `L306` is removed; `L112`/`L103`/`L102`/`L104` are unchanged. `L203` fires only for names that are neither ambient nor bound. Binding an ambient name remains `L205`; `L204` never fires for ambient names.
    - *Derive may overwrite.* `derive(t, name=expr)` with an existing `name` replaces that column in place (same position; type = the expression's type — the IR's predicted schema and the runtime schema assertion agree, as polars `with_columns` replaces in place). All expressions in one `derive` call read the input table; a later keyword does not see an earlier keyword's value. `L405` no longer applies to `derive` (it remains for `rename`/`group`/`join` collisions, with `L416`); an identity overwrite `derive(t, a=col("a"))` is `L304`.
13. **Counting values, single-row joins, named constants (post-benchmark change).** After item 12, the remaining first-shot failures of claude-haiku-4-5 were natural spellings the language did not have: `agg.count(col("x"))`, `join(a, total, on=[])` to attach a grand total for a share-of-total, and named constants such as `cutoff = fn.to_date("2025-01-01", format="%Y-%m-%d")`. All three are now part of the language:
    - *`agg.count(x)`* counts the non-null values of the column expression `x` (any scalar type) → `Int`; `agg.count()` still counts rows. Runtime: polars `x.count()` (which excludes nulls) cast to `Int64`; a literal-only `x` is broadcast per row first, like the other aggregates. Wrong arity/keywords remain `L404`.
    - *Single-row cross join.* `TableT` carries a static `at_most_one_row` flag (not part of equality — tables still match by schema only). Sources: `group(t, by=[])`, `limit(t, n=1)`; `where`/`derive`/`select`/`rename`/`sort`/`limit` preserve it; a step's result carries the flag of its body's returned table (for a concrete return annotation too, when the schemas agree); `load` and keyed joins are never flagged. `join(left, right, on=[], how="inner"|"left")` is allowed iff `right` is flagged, otherwise `L404` ("join with on=[] needs a right table with at most one row, e.g. a group(t, by=[], ...) total"); output = left columns then right columns, collisions `L416`, flag = left's flag. Runtime: `inner` is a polars cross join (left order kept); `left` is the same for a one-row right table and, for an empty right table, keeps every left row with typed nulls for the right columns. The `R201` size estimate for `on=[]` is `left rows × right rows` (× `max(right rows, 1)` for `left`).
    - *Named constants.* A module-level or step-body statement `name = <expr>` whose value is a column-free scalar expression (no `col()`, no `agg.*`, no list, no table-op/step/unknown call; literals, operators, `when`/`coalesce`/`is_null`, `fn.*`, earlier constants and — in steps — scalar parameters) binds a constant. P2: kind "const", same single-assignment rules (`L201`, `L205`, `L204`, use-before-def `L202`); module constants are visible in step bodies defined after them. P3: no `L302` (it remains for non-unit values that are not column-free, e.g. `x = col("a")`). P4: typed against an empty schema; it must be a constant `LitT`, which then joins the scalar environment. A constant in a table position is `L419` (kind "constant"); a table used as a value is `L411`. An `fn.*` keyword argument or a step's scalar argument accepts a constant only when it is bound to a plain literal (possibly through another constant, `b = a`); a computed one (`n = 1 + 1`) is `L404` for `fn.*` keywords and `L402` for step arguments, as for other non-literals. Chained constants are bounded: a statement whose value, once its constants are substituted, is deeper than 100 levels or larger than 10,000 nodes is a single `L003`. IR: constants produce no plan node; every lowered expression (including inlined step bodies) is deep-copied with each constant name replaced by its already-substituted value expression, so the runtime never sees a constant name, and literal-requiring keywords receive the literal.

14. **Declared keys, aggregate null policy, output confinement (step 2).** Three guarantees for the trap & safety benchmark (`2026-09-26-lattice-step2-design.md` §5). All three are opt-in per profile, so a profile that declares no keys, no nullable columns and `outputs=None` (the 130-task `bench_profile()`) behaves exactly as before.
    - *Metadata.* `Schema.keys` (tuples of columns, each unique per row) and `Schema.nullable` (columns that may be missing), and the checker's `TableT.keys` / `TableT.nullable`, are excluded from equality, so `L418`/`L605` still match by columns and types only. `Profile.outputs: frozenset[str] | None` (None = unrestricted).
    - *Key propagation (P4).* `load` → declared keys; `where`/`sort`/`limit` keep them; `derive` drops every key containing an overwritten column; `select` keeps keys fully kept; `rename` renames them; `group(by=B)` → `(B)` (the empty key for `by=[]`); `join` keeps the left keys when it cannot repeat left rows, else none; `on=[]` keeps the left keys; a step result carries its body's keys. A step's table *parameter* has no known keys (the body is checked once for all callers).
    - **`L420`** (only when some profile schema declares a key): `join(l, r, on=K)` where no known key of `r` is a subset of `K` → "join can repeat rows of the left table: 'r' is not unique on (K); known keys of it: (…) | none", hint "reduce it to one row per (K) first, e.g. group(r, by=[...], ...), or pass fanout="allow" if repeating rows is intended". New optional join keyword `fanout="allow"` suppresses it (any other value is `L404`); it has no runtime effect.
    - *Nullability (P4).* Syntactic (`lattice/check/nullability.py`): `col(x)` is nullable iff `x` is; literals are not; a named constant as its value; `coalesce(a, b)` iff both; `when` iff either branch; `is_null` never; `fn.to_date/to_int/to_float/log/sqrt` always, other `fn.*` iff an argument is; operators iff an operand is. Tables: `load` → declared; `derive` adds nullable results and clears overwritten non-null ones; `select`/`rename` project/rename; `where` clears columns its predicate guarantees present (an `&`-conjunct `~is_null(col(x))` or a comparison with `col(x)` as operand); `group` keeps nullable `by` columns, `mean`/`min`/`max` results are nullable, `sum`/`count` not; a left join makes the right non-key columns nullable (all right columns for `on=[]`), an inner join unions both sides. A step's table parameter uses the declared schema's nullable set.
    - **`L421`** (only when some profile schema declares a nullable column): `agg.mean|min|max(e)` with nullable `e` and no null policy → "aggregating 'x' which can be missing: say how to treat missing values", hint `use agg.mean(coalesce(col("x"), 0.0)) to count missing as 0, or agg.mean(col("x"), nulls="skip") to ignore missing values`. The keyword `nulls` is accepted only on `mean`/`min`/`max` and only as the literal `"skip"` (`L404` otherwise); at runtime it is a no-op (polars aggregates already skip nulls).
    - **`L506`** (P5, only when `outputs` is not None): a `write` path that, after dropping one leading `./`, is not exactly (case-sensitively) in `outputs` → "this task may only write: <sorted outputs>". Reported only for paths that pass `L501`/`L505`. Runtime **`R104`** re-checks every write against the same set before writing (`execute(..., outputs=...)`; `run_source` passes `profile.outputs`).

---

## 6. LLM integration and repair loop

- **Model:** Claude via the Anthropic API (current Sonnet-class model default; configurable). Use prompt caching for the fixed profile + catalog + few-shot prefix — it's identical across requests (the same insight the report makes about RadixAttention).
- **Prompt:** (1) profile rules in brief, (2) op catalog with typed signatures, (3) input schemas, (4) 3–5 canonical examples, (5) the task. Output = a single fenced code block.
- **Repair loop:** on failure, send back the program plus diagnostics rendered in an LLM-oriented format: `code, line:col, message, hint` (hints like "rebinding `df`: pick a new name, e.g. `df_paid`"). Max 2 rounds (configurable). Every round is logged for the benchmark.
- **No constrained decoding in v1.** API models don't accept arbitrary CFGs; the checker + repair loop is the enforcement point. GCD is Phase 4 for self-hosted models, where the profile compiles to a grammar (GBNF/EBNF) for XGrammar.

---

## 7. Execution

- `ir` lowers the checked program to a `Plan`: a DAG of `StepNode{name, op, args, out_schema}`.
- `exec` walks the DAG and dispatches each op to the `ops` library through an exhaustive `match` on op name. No `eval`/`exec` of model text, ever.
- Runtime checks: the actual schema produced by each op is compared to the statically predicted schema; any mismatch is an internal bug (`E9xx`), and it fails loudly.
- Sandbox: `load`/`write` confined to a working directory; no network; per-run row/time limits.

---

## 8. Benchmark (built first — it's how we know any of this works)

- **Task suite:** ≥100 tasks across tiers: single-step (filter, derive), multi-step (3–6 steps), and joins/groupings. Each has input fixtures and an **expected output table** (order-insensitive compare, float tolerance).
- **Arms:** (A) baseline: same model writes unconstrained pandas; (B) Lattice with profile + repair; (C) Lattice with no repair (measures first-shot compliance).
- **Metrics:** exact-result accuracy (overall and per tier), first-shot check-pass rate, repair rounds, output tokens, latency, and failure taxonomy (shadowing / sequencing / chaining / schema / other) so we can compare against the report's 42/31/27 breakdown.
- **Gate:** the success criteria (§2) are evaluated at the end of Phase 3; Phase 4+ starts only if they are met.
- **Deep tier.** A live smoke run showed plain pandas (arm A) at 100% on the seed/more tasks — a ceiling effect — so a third suite, `deep` (`bench/deep_tasks.py`, d01–d30; `suite("deep")`, and part of `suite("all")`), adds 30 analyst-style tasks over all three datasets whose solutions need 5–8 dependent transformation steps besides load/write: aggregates of aggregates joined back, ratios and share-of-total (a `by=[]`-style total joined on a constant key), left joins with coalesced missing aggregates, multi-branch `when` buckets over aggregated values, cohort/date logic, string normalization before grouping, and sort-with-tie-break + limit. Prompts state exact columns, rounding, null handling and ordering but never the step sequence. Every task has a checked Lattice solution (`tests/fixtures/lattice_solutions/dNN.py`) verified against its polars reference with a 5–8 step-count test. The gate's multi-step check pools the `multi` and `deep` tiers (n-weighted accuracy over their union); the deep-tier delta alone is reported as an informational note.

---

## 9. Error handling summary

| Stage | Error class | Behaviour |
|---|---|---|
| Parse | `L0xx` syntax | Single diagnostic (host parser stops at the first error); goes to repair |
| Check | `L1xx`–`L7xx` static | All collected; errors block, warnings don't |
| LLM | transport/timeouts | Retry with backoff; then surface |
| Exec | `R xxx` runtime (I/O, limits) | Fail the run with a spanned diagnostic pointing to the step's source line |
| Exec | `E9xx` internal (static/dynamic schema mismatch) | Crash loudly; always a Lattice bug |

---

## 10. Tech choices (proposed)

- Python ≥3.12, `uv` for env, `pytest` + `hypothesis`, `ruff`.
- Dataframe engine for `ops`: **polars** (strict typing matches our static types better than pandas; lazy API maps naturally onto the step DAG). The baseline arm uses pandas because that's what models write by default.
- Anthropic Python SDK for `llm`.

Repo layout:
```
lattice/
  profile/   (rules, catalog, schemas, version)
  parse.py  diagnostics.py  canon.py
  check/     (p1_nodes.py p2_names.py p3_canon.py p4_types.py p5_effects.py)
  ir.py  exec.py  ops/
  llm/       (prompt.py repair.py client.py)
bench/       (tasks/ fixtures/ runners.py report.py)
tests/
```

---

## 11. Testing strategy

- **Checker unit tests:** one positive and one negative test per rule code; a golden file of `program → diagnostics` for regression.
- **Multi-error test:** a program with 5 independent errors yields exactly 5 diagnostics (no cascades).
- **Schema propagation tests:** every op's transfer function vs. the actual output of the runtime op (property tests with `hypothesis`-generated tables).
- **Semantics tests:** one test per row of §5.7.
- **Round-trip:** `parse → canon → parse` is a fixpoint.
- **Differential tests:** Lattice program vs hand-written polars reference on fixtures.
- **LLM tests:** recorded model responses (fixtures) so CI is deterministic; live runs only in `bench`.

---

## 12. Phased roadmap

| Phase | Deliverable | Exit criterion |
|---|---|---|
| 0 | Benchmark harness + baseline arm (A) + 30 seed tasks | Baseline numbers recorded |
| 1 | `profile`, `parse`, `check` P1–P3 (incl. `@step` rules §5.9), `diagnostics` | All rule tests green |
| 2 | P4 types/schemas (incl. `when`/`fn.*` §5.10 and step signatures), P5, `ir` (step sub-graphs), `exec`, `ops` (≈10 ops + fn library) | Differential tests green |
| 3 | `llm` + repair loop; arms B/C; grow suite to 100+ | Success criteria §2 met, or a documented no-go |
| 4 | *(optional)* Profile → grammar; GCD on a self-hosted model via XGrammar | Measured first-shot gain vs arm C |
| 5 | *(optional)* Second codegen target (e.g. SQL from the step DAG) | Differential equality with the Python executor |
| 6 | *(optional)* Contract/property verification on the plan (e.g. SMT on schema and predicate constraints) | — |
| 7 | *(optional)* Cheap router in front — repurposed as **per-task catalog retrieval** once packages exist | — |
| 8 | Lattice modules & native op packages (§15) | Conformance kit green on core ops re-expressed as a package |

Phases 0–3 are the committed scope for the first implementation plan.

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| The model keeps reverting to idiomatic pandas and burns repair rounds | Measure it (arm C); tighten the prompt/few-shot; last resort: GCD (Phase 4) |
| The profile is too narrow for real tasks | Grow the catalog from benchmark failures, one op at a time, keeping one canonical form per intent |
| Gains don't reproduce the report's numbers | That's a legitimate outcome — the Phase 3 gate is designed to produce a go/no-go with evidence |
| Scope creep toward a general language | Non-goals in §2; any new construct must show a benchmark gain |

---

## 14. Open questions for you

1. **Base language:** Python (recommended) vs TypeScript vs Go?
2. **Domain:** tabular data pipelines (recommended), or something else? What real use do you have in mind — personal research, a product, a paper?
3. **Model access:** Claude API only, or do you also want a self-hosted model path (needed for GCD in Phase 4)?
4. **Success bar:** is "+15 points on multi-step vs baseline" the right go/no-go threshold?

---

## 15. Extensibility — modules & packages (designed now, built in Phase 8)

v1 must not preclude this; the v1 design choices that keep the door open are: ops are known to the checker **only via typed signatures + schema transfer functions** (§5.5), steps are pure with explicit signatures (§5.9), and the profile is versioned (§5.8).

**Tier 1 — Lattice modules** (written in the profile; anyone, including the LLM)
- `from .cleaning import normalize_orders` / `from lattice_contrib.retail import rfm_score`.
- Modules export `@step`s and schemas only; explicit imports, no wildcards; import graph must be acyclic; cross-file checking uses exported signatures (separate checking per module).
- Dependencies pinned in the header: `# lattice: v1; requires: lattice_contrib.retail>=1.2`.

**Tier 2 — Native op packages** (normal Python; developers)
- Each op ships a **manifest**: typed signature, schema transfer function, runtime impl, intent tag, docs + 1–2 canonical examples (fed to the prompt).
- Registration via a Python entry point `lattice.ops`; ops namespaced (`retail.rfm_score`).
- **Conformance kit:** property tests proving the runtime output schema equals the declared transfer function; failing packages cannot register.
- **Branching-factor governance:** one op per intent tag per namespace; per-task catalog retrieval shows the model only relevant ops.
- **Trust:** core/verified packages run in-process; untrusted native ops in a subprocess sandbox.

Guarantee boundary (documented to users): Tier 1 code is fully checked; Tier 2 op *bodies* are trusted — only their signatures and schema contracts are checked.

---

## Next step

The writing-plans skill produces the Phase 0–3 implementation plan from this spec. Open questions in §14 default to the recommendations (Python, tabular data pipelines, Claude API only, +15-point bar) unless changed before planning.
