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
from lattice.ops import load, where, derive, group, write, col, agg

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

Allowed: `Module`, `ImportFrom` (only `from lattice.ops import …`, first statement block only), `Assign` (single `Name` target), `Expr` (only a bare terminal op call, e.g. `write`), `Call`, `Name`, `Constant`, `List`/`Tuple` of constants, `keyword`, `Compare`/`BinOp`/`BoolOp`/`UnaryOp` **only inside column expressions** (§5.5), `Attribute` **only** as `agg.<fn>` / `fn.<name>` (§5.10). `FunctionDef` **only** with exactly the `@step` decorator, fully annotated, whose body obeys §5.9; `Return` only as the last statement of a step.

Everything else is rejected with `L1xx` codes: undecorated/other-decorated `def`, nested `def`, `class`, `lambda`, comprehensions, `for`/`while`/`if` statements, conditional expressions (`a if c else b` — use `when`), `with`, `try`, `global`/`nonlocal`, `import`, subscripts, starred args, default parameter values, f-strings, walrus, augmented assignment, method calls, `async`, `yield`.

### 5.3 P2 — Names and binding (static, single-assignment)

- Scoping: **static** (lexical) resolution in a pass after parsing — one module scope, plus one scope per `@step` body (§5.9).
- Every name is bound **exactly once** (`L201 rebinding`). This *is* the no-shadowing rule.
- Every use must be bound **earlier in the file** (`L202 use-before-def`, `L203 unknown name`).
- Every binding must be **used later** or be a terminal op (`L204 dead step` — warning).
- Names shadowing catalog ops or Python builtins rejected (`L205`).

### 5.4 P3 — Canonical-form rules (one way to say each thing)

| Rule | Code |
|---|---|
| A table-typed argument must be a `Name`, never a nested op call (no chaining) | `L301` |
| Exactly one op call per statement | `L302` |
| Every op call uses its one fixed call shape (§5.11 item 2): table input(s) positional, `where`'s predicate positional, every other parameter by keyword; step calls pass tables positionally and scalars by keyword | `L303` |
| No no-op steps: `where(t, True)`, `derive(t)` with no columns, and similar identity ops are rejected (two consecutive `where` steps remain legal) | `L304` |
| Column references only via `col("name")`; no bare strings where a column is expected | `L305` |
| Import list must exactly match ops used (no unused, no missing) | `L306` |

### 5.5 P4 — Types and schemas (synthesized bottom-up)

Types are few and distinct, no implicit coercion:

- Scalar: `Int`, `Float`, `Str`, `Bool`, `Date`, `Null`-able variants `T?`
- `Col[T]` — a column expression yielding `T`
- `Table[S]` — `S` is a **schema**: ordered map `column → scalar type`

Each op in the catalog has a typed signature **with a schema transfer function**, e.g.

- `where(t: Table[S], pred: Col[Bool]) -> Table[S]`
- `derive(t: Table[S], **new: Col[T_i]) -> Table[S ∪ {new_i: T_i}]` (error if a name already exists — `L405`)
- `group(t: Table[S], by: list[col∈S], **aggs: Agg) -> Table[{by…} ∪ {agg names}]`
- `join(left: Table[A], right: Table[B], on: list[str], how: "inner"|"left") -> Table[A ⊎ B]` (collision error)

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
| **Lexical scope, no free table variables**: a step body may reference only its parameters, its own locals, catalog ops, schemas, and *earlier-defined* steps — never module-level tables (steps are pure, cacheable, testable in isolation) | `L603` |
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

1. **Imports.** Exactly two modules may be imported from: `from lattice.ops import …` (the ops, `col`, `when`, `coalesce`, `is_null`, `agg`, `fn`, `step`, `Table`, and the scalar type names) and `from lattice.schemas import …` (input schema names from the profile). No aliases, no wildcards, no relative imports. Imports must exactly match use (unused → `L306`, missing → `L203`).
2. **Canonical call shapes (refines `L303`).** Each op has one fixed signature saying which parameters are positional and which are keyword. Table inputs are positional; `where(t, pred)` takes its predicate positionally; everything else that is not a table is passed by keyword (e.g. `load(path, schema=S)`, `join(l, r, on=[…], how="left")`, `write(t, path)`).
3. **Step annotations.** A step's return annotation may be bare `Table` (schema inferred from the body); parameters must be concrete (`Table[Orders]`) or scalar (`Int`, …). A concrete return type is checked exactly (`L605`).
4. **Nullability.** Every column is nullable in v1; the `T?` distinction is deferred.
5. **Output files.** `write` accepts `.csv` or `.parquet` paths (extension case-insensitive, and the file name must have a stem: `".parquet"` is `L505`); paths containing control characters (NUL, newline, …) are `L501`. The benchmark always uses `out.parquet`.
6. **Table positions.** A table argument (and a step's `return`) must name a table binding; a schema, import, type, op or step name there is `L419` (`L602` for `return`), so every accepted table argument resolves at lowering.
7. **Literals.** A *literal* is a bool/int/float/str constant or a (possibly repeatedly) negated number (`-1`, `--2.0`); the checker, IR and runtime all fold the same way. `fn.*` keyword arguments must be literals or a scalar step parameter — computed values (`days=5 % 0`, `sub=fn.to_str(1)`) are `L404`. `fn.round(digits=)` must be in 0..2³²−1 (`L404`; a step argument out of range is runtime `R103`). Int literals must fit in 64 bits (`L404`).
8. **`fn.clip`.** The result is Float when either bound is Float (the column is cast); Int bounds on an Int column stay Int.
9. **Date step parameters.** Rejected at the definition (`L601`): there is no Date literal to pass. Take a `Str` parameter and convert inside the step with `fn.to_date(s, format=...)`. PEP 695 type parameters on a step are also `L601`.
10. **Limits.** Nesting deeper than 100 AST levels is a single `L003` (the checker never raises). At runtime an op whose output exceeds the row limit is `R201`; a `join` is sized exactly from per-key counts and refused *before* it runs. The time limit (`R202`) is checked between ops, so a single long op is not interrupted.
11. **No-ops (`L304`)** also include `sort(t, by=[])` and renaming a column to itself.

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
