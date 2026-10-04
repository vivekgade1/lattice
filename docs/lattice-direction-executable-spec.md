# Lattice as an executable specification and checker: direction and roadmap

> **Status:** DRAFT for the user's review, 2026-10-04. Nothing in it is approved or built
> unless it says so.
>
> **Background:**
> - `docs/handoff-2026-09-30.md`: where the project stands.
> - `docs/results/2026-09-26-bench-smoke.md`: all measured results.
> - `reports/Spec driven LLM code checkers.md`: the external landscape (2026-10-04
>   research, every claim sourced).
>
> **Supersedes:** `docs/lattice-vision.md`'s goal of Lattice as the language LLMs write
> application code in. Lattice becomes the language the *application's intent* is written
> in. Code is still written by LLMs, in mainstream languages, and checked against that
> intent.

## 1. The idea in one paragraph

A user describes their application in Lattice, with an LLM's help: its data, who owns
what, who may do what, the rules values obey, the state machines things move through, the
behaviour of each endpoint, and examples of what must happen. The Lattice checker makes
sure that description is itself consistent. An LLM then writes the real application in
the stack the team uses (Python, JavaScript/TypeScript, C#, later Go), plus tests. The
Lattice checker judges that code against the description in three ways:
- **statically:** the guarantees as code checks;
- **behaviourally:** the description is executable, so the code and the description run
  side by side on generated requests and must agree;
- **by invariants:** the rules are tested on the database after every request.

Every violation goes back to the LLM with a minimal failing example, and the loop repeats
until the code passes or the budget is spent. An existing project can be imported: Lattice
lifts a description from its code, verifies the description against the running app, and
the user reviews it. From then on, every change, by a human or an LLM, is checked against
it.

## 2. Why this direction (the evidence)

### 2.1 Our own experiments

| Experiment (pre-registered unless noted) | Result | What it says about this direction |
|---|---|---|
| Confirmation run: LLM edits through Lattice vs Python (CRUD) | Lattice was **not** safer (Sonnet: 7/67 silent vs 1/90) | Making the LLM write Lattice *as the implementation* doesn't pay |
| Go cost pilot: Lattice→Go vs hand-written Go | about 30% cheaper per correct task, 6–7× shorter programs, both 120/120 correct | Lattice is a compact, faithful description; correctness hit the ceiling |
| Option 1 pilots (aggregation, ownership, atomicity traps) | Sonnet got aggregation and ownership right; it slipped on **atomicity** (8/9 runs where a helper commits partway) | Frontier models fail on business rules that span a request, not on syntax |
| Option 1 main: Python vs Python + Lattice-derived checks (PY807) | Sonnet: silent runs **18 → 8**, correct runs **24 → 34**, partial writes **17 → 0** (p = 0.016); Haiku: partial writes 14 → 0 | **A Lattice guarantee as a check on ordinary code works on a frontier model** |
| Same run: what Q still got wrong | unstated rules (price 0 accepted, repeated id counted twice), response shapes | Once *declared*, these become checkable: an invariant, a bound, a response model |

### 2.2 The outside landscape

From the research report:
- **Spec-driven coding tools write prose.** Spec Kit, Kiro, Tessl, OpenSpec and CodeSpeak
  all keep their specs in Markdown. Only Kiro checks code mechanically, with
  LLM-extracted property tests ("not formal verification"), and none publishes evidence
  that this works.
- **Executable specs make good oracles, but not for business apps.** In industry
  (Cedar, ShardStore, CCF), a small executable model checked against the real code by
  random testing finds real bugs cheaply. No one applies this to business application
  code.
- **Verified-code research doesn't reach business backends.** The Dafny/Verus/Lean loops
  work on small algorithms. They are gamed when the model controls the spec, and they
  barely touch backends.
- **Checkers on ordinary code are generic.** Ownership has research tools. **Atomicity,
  the best-documented class of web-app bugs (53 of 91 ad hoc transactions faulty), has no
  checker.**
- **No product combines the pieces:** a checked business-rule spec, an executable oracle,
  checks on LLM-written mainstream code, and a repair loop. PY807 is the only measured
  business-rule checker in an LLM loop found anywhere.

**What would be new:** a checked business-rule language whose guarantees become
deterministic checks on mainstream code, whose runtime is the oracle for behaviour, and
whose spec is owned by the user. Keeping the spec out of the code-writing LLM's hands is
what blocks the oracle gaming seen in the verification research.

## 3. The loop

```
                ┌──────────────────────── user intent ─────────────────────────┐
                │   (conversation with an LLM, documents, an existing codebase) │
                └───────────────┬───────────────────────────────┬──────────────┘
                     author / refine (LLM drafts,         import (§6)
                     user approves, §4.7)                 lift + verify against
                                │                         the running app
                                ▼                               ▼
                    ┌───────────────────────────────────────────────┐
                    │ Lattice spec (the app's intent, version-controlled) │
                    │  data · ownership · roles · bounds · state machines │
                    │  invariants · endpoint behaviour · scenarios        │
                    └───────────────┬───────────────────────────────┘
                                    │  Lattice checker: the spec is consistent,
                                    │  every guarantee holds *in the spec*
                                    ▼
                 LLM writes / changes the application (Python · JS/TS · C# · Go)
                                    │        (the spec is read-only to this LLM)
                                    ▼
          ┌──────────────── check the code against the spec ────────────────┐
          │ A. static:      guarantees as code checks (ownership, atomicity,  │
          │                 bounds, private data, route order, errors)        │
          │ B. behaviour:   spec runtime vs app on generated requests:        │
          │                 same status, body, database effects               │
          │ C. invariants:  every rule queried on the database after every    │
          │                 request (totals, stock ≥ 0, legal transitions)     │
          │ D. scenarios:   the user's approved examples, as tests             │
          └──────────────┬───────────────────────────────────┬──────────────┘
                 violations│ (minimal counterexample,           │ all pass
                 file/line, the rule that failed)               ▼
                         ▼                               code + generated tests
                   back to the LLM (repair, budgeted)    + a check report
```

**Principles:**
1. **The spec is the source of truth, and the user owns it.** The code-writing LLM can
   read it but never change it. A change to the spec is a separate, reviewed step.
2. **Deterministic checks, not LLM judgement.** Every finding is reproducible, with a
   counterexample. An LLM may *draft* specs and *write* code. It never decides pass/fail.
3. **Silence over guessing.** A check that can't decide stays silent and reports the gap.
   This is the PY801 lesson: false findings make strong models worse.
4. **Anything the spec cannot express is marked as such.** It's written as prose
   requirements, shown as "unchecked" in every report, never silently trusted.
5. **Measured, pre-registered, cost-adjusted.** Every claim about the loop is backed by an
   experiment that reports silent failures, cost per correct change, repair rounds and
   time.

## 4. The Lattice spec

Today's Lattice already holds part of a spec. The **profile** has tables, owners,
`owner_via`, roles (`Roles(...)`, `Authenticated`, `RoleColumn(values=)`), bounds, enums
(`one_of`), private columns and error conventions. **Endpoint programs** are executable
behaviour with checked guarantees: one statement per read, all-or-nothing writes,
declared errors. What follows extends it into a full application spec. Items marked
**(new)** don't exist yet.

### 4.1 Data and ownership (exists)

```python
# shop.lattice.py   (the profile, today written as Python data; proposed: this surface syntax)
table User:     id: Int key; email: Email unique; role: OneOf("buyer", "vendor", "admin"); private hashed_password
table Vendor:   id: Int key; user_id: Int -> User unique   owner(user_id)
table Product:  id: Int key; vendor_id: Int -> Vendor; price: Float(gt=0); stock: Int(ge=0)   owner_via(vendor_id)
table Order:    id: Int key; user_id: Int -> User; status: OneOf(*ORDER_STATES); total: Float   owner(user_id)
table OrderItem: id: Int key; order_id: Int -> Order; product_id: Int -> Product; quantity: Int(gt=0); price_at_purchase: Float
roles: Buyer, Vendor, Admin from User.role
```

### 4.2 Policies (exists in part)

Who may call what, and whose rows they see: `auth=Roles(Buyer)`, `mine()`, `owned()`,
`owner_is()`. **New:** named policies reused across endpoints and checked on code, for
example `policy order_access: owner or Admin, else 403 "You do not have access to this
order"`.

### 4.3 State machines (new)

```python
machine Order.status:
    pending   -> confirmed | cancelled
    confirmed -> shipped   | cancelled
    shipped   -> delivered
# checked: every write to Order.status moves along an edge (static where visible, C always)
```

### 4.4 Invariants (new)

Rules over the whole database that must hold after every request:

```python
invariant order_total: Order.total == sum(OrderItem.quantity * OrderItem.price_at_purchase by order_id)
invariant stock_never_negative: Product.stock >= 0
invariant one_vendor_per_user: count(Vendor by user_id) <= 1
```

Invariants compile to SQL queries run by layer C. They are also proved, where possible,
against the spec's own endpoints by the Lattice checker.

### 4.5 Effects and transactions (exists for endpoints; new as a declared rule)

`atomic` (the default for every write endpoint) means a refused request writes nothing.
It is checked by layer A (PY807-style) and layer B (a refused request leaves the
database unchanged). `for_each` effects (emails, webhooks) run after commit. **New:**
`idempotent(key)` and `retry-safe` declarations.

### 4.6 Endpoint behaviour (exists)

Endpoint programs are the executable reference. Where the spec gives an endpoint's full
behaviour, layer B compares the app with it response by response. Where it gives only
the endpoint's contract (params, auth, errors, response model), layers A, C and D still
apply. **New:** nested response models (an order with its items), the language gap that
blocked arm L in option 1. These are needed for full behaviour specs of common APIs.

### 4.7 Scenarios (new)

Concrete examples the user approves ("a buyer reorders an order whose product ran out:
409, nothing written"). They run as tests in every language. They're also the guard on
spec adequacy: a spec that a known-wrong scenario passes is too weak (§7.3).

### 4.8 Unchecked requirements (new)

Prose requirements the spec cannot yet express (for example a UI behaviour, or an
external system's contract). They're attached to the endpoints or tables they concern,
listed in every report as **not checked**, and counted, so the gap stays visible and
shrinks over time.

### 4.9 How specs are written

- The user describes intent in conversation, and an LLM drafts the Lattice. The Lattice
  checker rejects inconsistent drafts with exact fixes, the loop Lattice already has.
- The spec is shown back as **plain-language rules and generated scenarios** for the user
  to approve, line by line.
- **Spec review is the human cost of this design, and it is the one we must keep small.**
  Specs were about 6–7× shorter than the equivalent code in the Go pilot. Measure review
  time.

## 5. The checker

### 5.1 Layer A: static guarantee checks (per language)

pycheck (PY801–PY807) generalized: each guarantee of the spec becomes a check on the
code, using the spec's facts (owners, roles, bounds, machines) rather than conventions
inferred from code alone.

| Guarantee | Check on code | Today |
|---|---|---|
| Ownership / policies | a route touching owned rows applies the policy (filter, check or admin bypass) | PY801 (Python; switched off for role-set apps until precise) |
| Private columns | never reach a response | PY802 |
| Bounds | request values bounded where written or used as limits | PY803 |
| Route order | literal paths before parameter paths | PY804 |
| Error conventions | the spec's status/detail for each case | PY805 |
| Datetimes | no naive/aware mixing | PY806 |
| Atomicity | no commit that a later failure can follow | **PY807 (measured: 0 false alarms, 8/8 real slips caught)** |
| State machines | writes to a machine field move along an edge | new |
| Nested response shapes | response matches the declared model | new |

Each check ships only after a **precision gate**: 0 findings on correct references, and
recall measured on trap variants and real model slips, as PY807 was.

### 5.2 Layer B: behavioural equivalence against the spec runtime (language-agnostic)

The spec's endpoints run on the Lattice runtime. The app runs as itself. The same
generated requests go to both, on copies of the same database:
- users of every role;
- other users' rows, missing ids, boundary values;
- scenario sequences, and random request sequences (stateful property testing).

Status, body (canonicalized) and database effects are compared, and a disagreement is
**shrunk** to a minimal request sequence. The machinery exists:
- the importer's `verify()` and its generated suites;
- the option-1 oracles;
- the differential runners (Lattice runtime vs generated FastAPI vs Go: 0 mismatches on
  2,478 cases).

**This layer works on any language**, because it only needs the app running over HTTP and
its database. That is what makes multi-language support affordable.

### 5.3 Layer C: invariant monitors (language-agnostic)

After every request of layers B and D, every invariant runs as SQL on the app's
database. A violation names the invariant, the request that broke it, and the rows.

### 5.4 Layer D: scenarios as tests (language-agnostic)

The approved scenarios are run against the app, and each one is also emitted as a native
test in the app's language (pytest, Jest/Vitest, xUnit). The delivered code then carries
its tests, so they can keep running in CI after the check passes.

### 5.5 Feedback to the LLM

One block per violation, giving:
- the rule (from the spec, with its line);
- the minimal counterexample;
- the file and line in the app where known;
- a one-line fix idiom.

The format follows `lattice/pycheck` findings and the edit-slice repair loop, which
already measure rounds and cost.

### 5.6 Surfaces

- **CLI:** `lattice check <app> --spec shop.lattice.py`.
- **CI gate:** a GitHub Action / CI step with a check report.
- **Agent integration:** an MCP server exposing `check`, `explain_rule` and
  `counterexample`, so Claude Code, Cursor and Kiro can run the loop. Hooks re-check every
  edited file, the way Semgrep's MCP does for generic rules.

## 6. Importing an existing project (Python, JavaScript/TypeScript, C#; then Go)

Most code already exists. Import gives an existing app a **verified** Lattice spec, so
every later change, by a human or an LLM, is checked against it.

### 6.1 Principle: rules first, behaviour where it verifies

An import doesn't need to translate every route. It produces three things, in order of
value:
1. **The data and rule layer:** tables, keys, foreign keys, enums, bounds, private
   columns, owners, roles and policies, error conventions. Inferred from models, schema,
   migrations and route code, then **confirmed by probing the running app**. Example:
   call each route as a user of every role and observe 403s, as the importer now does for
   permission factories.
2. **Proposed invariants and state machines:** mined from the data and code by
   Daikon-style inference plus LLM proposals, **then checked against the running app and
   approved by the user.** They're never adopted silently.
3. **Endpoint behaviour, where it verifies:** a route becomes an executable spec only if
   the lifted program answers exactly like the original on the generated suite (the
   importer's `verify()`). Otherwise it stays a **contract-only** route (params, auth,
   errors, response model), still covered by layers A, C and D.

Today's importer (FastAPI + SQLModel → Lattice) does 1 and 3 for Python. On held-out apps
it verifies 3–10 routes of 10–30, so layer 3 is the hard part and layer 1 the reliable
one. **The rules-first ordering is what makes import useful even where translation
fails.**

### 6.2 Architecture: one importer, language front ends

```
           ┌─ Python front end  (ast; FastAPI · Django · Flask; SQLAlchemy · SQLModel · Django ORM)
source ────┼─ JS/TS front end   (TypeScript compiler API / tree-sitter; Express · NestJS · Next.js API · Fastify;
           │                     Prisma · TypeORM · Sequelize · Drizzle)
           ├─ C# front end      (Roslyn; ASP.NET Core minimal APIs & controllers; EF Core;
           │                     [Authorize(Roles=…)], policies, DataAnnotations)
           └─ Go front end      (go/ast; net/http · Gin · Echo; GORM · sqlc)   [after the first three]
                       │
                       ▼   one shared "app model": routes, params, auth, tables, relations, raises, writes
           shared lifter / profile generator / suite generator / verifier   (exists for Python, generalize)
                       │
                       ▼
       Lattice spec + import report (verified / contract-only / unclear, with reasons)
                       │
           probe the running app (language-agnostic): roles, ownership answers, error conventions, bounds
```

Today's `lattice/importer/extract.py` already produces a framework-neutral model (`RepoModel`:
tables, routes, dependencies, raise sites). The front ends emit that model. Everything
after it is shared, and the parts that run the app (probing, the suite, `verify()`) are
already language-agnostic over HTTP.

### 6.3 Per language: what maps to what

| Concept | Python | JavaScript / TypeScript | C# |
|---|---|---|---|
| Routes | FastAPI decorators, Django URLconf + views, Flask routes | Express/Fastify handlers, NestJS controllers, Next.js route handlers | ASP.NET Core controllers / minimal APIs |
| Request validation (bounds) | pydantic `Field`, Django forms/serializers | zod, class-validator (NestJS DTOs), Joi | DataAnnotations, FluentValidation |
| Data model | SQLModel / SQLAlchemy / Django models | Prisma schema, TypeORM entities, Sequelize models, Drizzle | EF Core entities + `DbContext`, migrations |
| Auth / roles | dependencies (`Depends(...)`), Django permissions | middleware, NestJS guards, passport | `[Authorize(Roles=…)]`, policy handlers |
| Transactions (atomicity) | `session.commit()`, `transaction.atomic` | `prisma.$transaction`, TypeORM `queryRunner`, Sequelize `transaction` | `SaveChanges`, `BeginTransaction`, `TransactionScope` |
| Errors | `HTTPException`, DRF exceptions | `res.status(...)`, Nest exceptions | `NotFound()`, `Forbid()`, `ProblemDetails` |
| Tests emitted (layer D) | pytest | Jest / Vitest | xUnit |
| Layer-A checks | pycheck (exists) | new, on the TS AST (typescript-eslint parser / TS compiler API) | new, as Roslyn analyzers |

### 6.4 Import output and review

- **The spec**, with each item marked: **verified** (the probes or the differential agree),
  **inferred** (from code, not yet confirmed), or **proposed** (mined; needs the user's
  approval).
- **An import report:** coverage by layer (rules, contract, behaviour), each unclear item
  with its reason and candidates, and the original app's own findings (its baseline: the
  conventions it already breaks, recorded rather than reported as new).
- **A start-state fidelity check:** the spec's executable parts answer as the app does on a
  hand-checkable suite. This is the JustNotes lesson; the multi-vendor import has such a
  suite.

## 7. Risks and how the design meets them

| Risk | Mitigation |
|---|---|
| **A wrong spec is faithfully enforced** (the central risk: LLMs wrote TLA+ with 8.6% semantic accuracy; our LLM-written Lattice had silent errors) | User approval of every rule in plain language; scenarios as concrete checks; import-derived facts verified on the running app; **measure spec error rates** as a first-class metric |
| **Spec too weak to catch wrong code** | Mutant discrimination: generate wrong variants of the reference (our trap-variant machinery) and require the spec plus checks to reject them; report the score |
| **The code LLM games the checker** | The spec is read-only to it; checks are deterministic; trivial-spec detection (a rule no mutant breaks is flagged) |
| **False findings make strong models worse** (confirmation run, PY801) | A precision gate before any check ships; silence over guessing; baselines for an app's own conventions |
| **Repair gains vanish at equal cost** (Olausson) | Report cost per correct change and rounds; pre-register |
| **Review burden** (the SDD critique: too much Markdown) | The spec is short and checked, not prose; shown as rules plus scenarios; measure review time |
| **Expressiveness gaps** (nested responses, external calls) | Unchecked requirements are explicit and counted; language growth driven by import and benchmark evidence (rules R8, R6) |
| **Multi-language cost** | Layers B, C and D are language-agnostic; only layer A and the import front ends are per language |
| **Concurrency** (PY807 is single-request; races need more) | Out of scope for v1; later, concurrent request schedules in layer B |

## 8. Roadmap

Every phase ends with a pre-registered experiment (CLI only, no API key, both verdicts
reported if the oracle is corrected) or a measured gate. A phase's exit criterion must
hold before the next starts.

### Phase 0: consolidate on Python (engineering, ~2 weeks of sessions)

- **Spec v0:** today's profile and endpoints, plus `machine`, `invariant`, `scenario` and
  `unchecked`. Each needs checker rules, semantics cases, Python/Go gates per the language
  rules, and differential tests.
- **Checker v0** on Python: layer A (PY801–PY807 plus state machines), layer B (generalize
  `verify()` and the oracles into `lattice check`), layer C (invariant SQL), layer D
  (scenarios to pytest).
- **Nested response models** (closes the option-1 gap).
- **Exit:** the multi-vendor app's spec checks clean against its original code, and every
  option-1 trap variant is caught by at least one layer.

### Phase 1: the loop on Python (experiment)

- **Arms:**
  - **P:** ticket → Python, the baseline.
  - **S:** ticket → spec change (LLM-drafted, auto-approved by a held-out oracle of
    approved rules, to simulate the user) → Python checked by layers A–D → repair.
- **Scope:** the multi-vendor app plus the template, Sonnet and Haiku, at least 6 reps,
  about 20 tickets including state-machine, invariant and nested-response changes.
- **Measures:**
  - silent failures against intent;
  - spec error rate;
  - cost per correct change and repair rounds;
  - catches by layer;
  - false findings.
- **Exit (pre-registered):** S reduces silent failures vs P on Sonnet (one-sided Fisher,
  p < 0.05, with a correctness guard), and the spec error rate stays under a bar fixed
  before the run.

### Phase 2: import for Python projects

- **Front ends:** Django (+ DRF) and Flask, besides FastAPI. Plus SQLAlchemy and Django ORM.
- **Rules-first import:** probing by role, invariant mining with proposals, and import
  reports with a review workflow.
- **Exit:** on 3 held-out open-source Python apps, at least 90% of tables and routes have a
  verified rule layer and a fidelity suite passes. Behaviour coverage is reported, not
  gated.

### Phase 3: JavaScript / TypeScript

- **Front ends:** a TS front end (Express, NestJS, Next.js route handlers; Prisma, TypeORM).
- **Checks:** layer A as TS-AST checks, with the precision gate; layer D emits Jest/Vitest.
- **Experiment:** a JS/TS app and tickets, arms P vs S as in phase 1.
- **Exit:** precision gate met, and a pre-registered result on the JS/TS app.

### Phase 4: C#

- **Front ends:** a Roslyn front end (ASP.NET Core, EF Core, authorization attributes and
  policies, DataAnnotations).
- **Checks:** layer A as Roslyn analyzers; layer D emits xUnit.
- **Exit:** as phase 3, on a C# app.

### Phase 5: Go and the product surface

- **Go front end:** a Go target already exists, so the spec can also compile to a verified
  Go service where wanted.
- **Product surface:** the CLI, the CI action and the MCP server.

### Cross-cutting, every phase

- Language rules R1–R14: every new spec construct needs semantics cases on every target,
  or a gate.
- The results log is append-only.
- The project's metric set: tokens, cost per correct change, rounds, time, and Wilson
  intervals.

## 9. What happens to what exists

| Asset | Role in this direction |
|---|---|
| Checker passes P1–P8, guarantees | Validate specs; the source of every code check |
| Lattice runtime (Python) | **The executable oracle** for layer B |
| Generated FastAPI app, Go target, TS client | An optional *compile path*: where a spec is complete, ship it directly with tested equivalence; the TS client gives frontends a validated client |
| Importer (FastAPI + SQLModel) | The Python front end and the shared lift/verify core of §6 |
| pycheck PY801–PY807 | Layer A for Python |
| Edit-slice harness, oracles, trap variants, rescore, analysis scripts | The experiment platform and the mutant-discrimination engine |
| Tabular profile | Unchanged; out of scope here |

Nothing is thrown away. The change is *who writes what*. The user, with an LLM's help,
writes the spec. The LLM writes the code. Lattice judges.

## 10. Open questions for the user

1. **Spec surface syntax:** keep today's Python-syntax profiles and programs, or add a
   compact surface (§4.1 sketch) that compiles to the same IR?
2. **First non-Python language:** JS/TS (phase 3) or C# (phase 4)? This draft puts JS/TS
   first, for a larger pool of open-source apps to import and test on.
3. **Go:** after JS/TS and C# (this draft), or alongside Python, since a Go target already
   exists?
4. **Phase 1's simulated user:** auto-approval by a held-out set of rules is cheaper but
   optimistic. A real review step measures the true cost. Which one?
5. **Commercial surface:** a CLI and CI gate first, or the MCP server for agents first?
