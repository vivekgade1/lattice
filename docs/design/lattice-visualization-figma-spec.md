# Lattice Studio: visualization spec for Figma

> **Status:** DRAFT for the user's review, 2026-10-04.
>
> **Companion to** `docs/lattice-direction-executable-spec.md`, which describes what
> Lattice does: a user-owned spec, an LLM writing the code, and a four-layer checker. This
> document describes what users *see*.
>
> **Audience:** a product designer building the Figma file, and the engineer who later
> implements it. Everything here can be built in Figma with components, variants,
> variables and auto layout. §11 gives the file structure. The data each view needs is
> pinned to outputs Lattice already produces, or to the new ones the direction draft
> adds (§10).

## 1. Purpose and principles

Lattice Studio makes four things visible that are invisible today:
1. **The application's intent:** what the spec says, in plain language and as diagrams.
2. **Whether code honours it:** each check layer's verdict, with minimal counterexamples.
3. **What an LLM changed and why it was sent back:** the repair loop as a timeline.
4. **How much of an existing app is covered:** the import's verified, inferred, proposed
   and unchecked parts.

**Design principles:**
- **Show the rule, then the evidence.** Every red mark links to the rule it breaks and a
  reproducible counterexample, never to an opinion.
- **Trust states are first-class.** Every spec item is *verified*, *inferred*, *proposed*
  or *unchecked*, with a consistent colour, icon and label everywhere (§3.2). A user must
  never mistake an unchecked requirement for a checked one.
- **Plain language first, Lattice source one click away.** Each rule is shown as a
  sentence; the source is a toggle.
- **Silent is worse than loud.** "Silent" failures (wrong answer, no error) get the
  strongest treatment. "Loud" failures (500, crash, refusal) come second.
- **No dashboards without decisions.** Every summary view has a primary action: approve,
  fix, re-run, or open the counterexample.

## 2. Users and jobs

| Persona | Main jobs | Primary screens |
|---|---|---|
| **Spec owner** (product engineer or tech lead) | Write and approve rules with LLM help; review imported rules; decide what is in scope | Spec Explorer, Rule Review, Import Review |
| **Developer** (often driving an LLM agent) | See why a change failed, reproduce it, fix it or let the agent repair it | Check Run, Counterexample, Repair Timeline |
| **Reviewer / lead** | Gate a PR or release; see coverage and trends | PR Check Summary, Coverage Dashboard |
| **Evaluator** (this project's experiments) | Compare arms, see silent/loud/correct with confidence intervals | Experiment Board |

## 3. Design tokens (Figma variables)

Define these as **Figma variables** in a `Lattice/Tokens` collection, with **Light** and
**Dark** modes. The values below are starting points; contrast must meet WCAG 2.2 AA
(text 4.5:1, UI marks 3:1) in both modes.

### 3.1 Base

| Token | Light | Dark | Use |
|---|---|---|---|
| `color/bg/canvas` | #FFFFFF | #0F1115 | page |
| `color/bg/surface` | #F6F7F9 | #171A21 | cards, panels |
| `color/bg/raised` | #FFFFFF | #1E222B | popovers, modals |
| `color/border/subtle` | #E3E6EB | #2A2F3A | dividers |
| `color/text/primary` | #14171F | #ECEFF4 | body |
| `color/text/secondary` | #5B6270 | #A7AFBD | meta |
| `color/accent` | #3B5BDB | #7C93FF | links, focus, selection |

### 3.2 Trust states (spec items)

| State | Meaning | Colour token | Icon | Label |
|---|---|---|---|---|
| **Verified** | Checked against the running app or proved by the checker | `trust/verified` #1F8A5B | shield-check | "Verified" |
| **Inferred** | Read from code; not yet confirmed | `trust/inferred` #B7791F | eye | "Inferred" |
| **Proposed** | Mined or LLM-suggested; needs approval | `trust/proposed` #6B5BD2 | sparkle-question | "Proposed" |
| **Unchecked** | Prose requirement the spec can't express | `trust/unchecked` #6B7280, dashed outline | text-lines | "Not checked" |

### 3.3 Verdicts (checks and runs)

| Verdict | Token | Treatment |
|---|---|---|
| Correct / pass | `verdict/pass` #1F8A5B | filled dot |
| **Silent** (wrong answer, no error) | `verdict/silent` #C2255C | filled diamond plus a "silent" badge: the strongest mark |
| Loud (5xx, crash, refused) | `verdict/loud` #E8590C | filled triangle |
| Equal under a rule / accepted deviation | `verdict/equal-rule` #5B6270 | hollow dot plus tooltip naming the rule |
| Not run / skipped | `verdict/none` #A7AFBD | dash |

### 3.4 Check layers

| Layer | Token | Glyph |
|---|---|---|
| A: static | `layer/a` #3B5BDB | `{ }` |
| B: behaviour (vs spec runtime) | `layer/b` #0C8599 | ⇄ |
| C: invariants | `layer/c` #9C36B5 | ∀ |
| D: scenarios | `layer/d` #2F9E44 | ▶ |

### 3.5 Type, spacing, radius

- **Type:** Inter for UI; JetBrains Mono for code, Lattice source, requests and SQL.
  - Scale (Figma text styles): `display/24-32`, `title/18-26`, `body/14-20`, `meta/12-16`,
    `code/13-20`.
- **Spacing:** a 4-pt grid, with tokens `space/1`=4, 2=8, 3=12, 4=16, 6=24, 8=32.
- **Radius:** `radius/sm`=4, `radius/md`=8, `radius/lg`=12.
- **Elevation:** two shadows, `elev/1` for cards and `elev/2` for popovers and modals.

## 4. Information architecture

```
Project ─┬─ Overview (coverage + latest runs)
         ├─ Spec
         │   ├─ Data & ownership (entity graph)
         │   ├─ Policies & roles (matrix)
         │   ├─ State machines
         │   ├─ Invariants
         │   ├─ Endpoints (contract / behaviour)
         │   ├─ Scenarios
         │   └─ Unchecked requirements
         ├─ Review queue (proposed / inferred items awaiting approval)
         ├─ Checks
         │   ├─ Runs list
         │   └─ Run detail → finding → counterexample → repair timeline
         ├─ Import (report + review)
         └─ Experiments (evaluator only)
```

Global chrome:
- **Left nav:** the sections above.
- **Top bar:** project switcher, spec version (a git ref), check status pill, search
  (`⌘K`: rules, entities, endpoints, findings).
- **Right drawer:** a context panel for the selected rule, entity or finding.

## 5. Screens (frames)

All frames are **1440×1024 desktop**. Tablet (1024) and mobile (390) are reflowed only
for Overview, PR Check Summary and Counterexample (§9). Each frame has an auto-layout
root, a 24-px page padding, a 12-column grid with 24 gutters, and a 240-px left nav.

### 5.1 Overview

The answer to "is this app's code faithful to its spec?" in one screen.

**Header:** project name, spec version, last check time, and a primary button,
**Run checks**.

**Coverage card row** (4 cards, `Card/Metric`):
1. **Spec coverage:** a stacked bar of verified, inferred, proposed and unchecked items,
   with counts.
2. **Endpoints:** behaviour-specified, contract-only and not covered.
3. **Latest run:** a verdict pill, then counts by layer (A/B/C/D) of pass, silent and
   loud.
4. **Open review items:** a count and a button to the Review queue.

**Silent failures panel:** silent findings from the latest run. Each row shows the rule
in a sentence, the layer, the endpoint, and an **Open counterexample** link. It's empty
when there are none, with the empty state "No silent failures in the latest run".

**Trend:** a small multiples line chart of silent, loud and correct runs over the last 30
check runs, plus cost per correct change.

**Unchecked requirements callout:** "N requirements are not checked", linking to the
list. It is always visible while N > 0.

### 5.2 Spec: Data & ownership (entity graph)

**Canvas:** an entity-relationship graph (`Node/Entity` components).
- **Node content:** entity name; key columns; columns with type badges, bounds as chips
  (`> 0`, `≤ 255`) and enums as a chip list; and private columns (lock icon, masked).
- **Edges:** foreign keys. **Ownership edges** are drawn thicker with an owner glyph and
  labelled "owner" or "owner via Vendor".
- **Node frame colour:** the trust state of its ownership fact. Dashed if unchecked.
- **The users table** is pinned to the left with a person glyph, and roles are listed
  under it as chips.

**Interactions:**
- Click a node to open the drawer: every rule touching the entity in plain language, its
  endpoints, its invariants, and its source toggle.
- Hover an edge for its tooltip, e.g. "Product rows belong to the owner of their Vendor
  (verified by probe: vicky cannot PATCH victor's product → 403)".

**Toolbar:** a layout toggle (auto / manual), filters (owned only, private columns,
proposed items) and **Export SVG**.

### 5.3 Spec: Policies & roles (matrix)

A grid: rows are endpoints (method plus path), columns are roles (Buyer, Vendor, Admin,
Public). Each cell takes one of these values:

| Cell | Meaning |
|---|---|
| ✓ | allowed |
| ✕ | refused |
| ✓ own | allowed on the caller's own rows only |
| ✓ all | admin bypass |

**Refusal tooltips** give the status and detail (e.g. 403 "You do not have access to
this order").

The **cell outline** shows the trust state; for imports, *verified* means the probe
observed it. A column header that is a role set (`Roles(Buyer, Vendor)`) shows a small
badge.

### 5.4 Spec: State machines

One diagram per `machine` (`Diagram/StateMachine`):
- **States:** rounded nodes, with the initial state double-outlined.
- **Transitions:** arrows labelled with the endpoints that perform them (e.g. "PATCH
  /orders/{id}/status · cancel").
- **Illegal transitions** a check observed in code are dashed red arrows with a ⚠ marker,
  linking to the finding.
- **Side panel:** the transitions table and each terminal state's description.

### 5.5 Spec: Invariants

A list of `Row/Invariant` items, each showing:
- the sentence ("An order's total equals the sum of its lines");
- the formula in code font;
- a layer C badge and the last result (pass or the number of violations);
- the tables it reads;
- a trust state.

Expanding a row shows the generated SQL, the last violating request and the rows
involved.

### 5.6 Spec: Endpoints

Two-pane: a filterable list on the left, details on the right.

**List rows** show method colour chips (GET, POST, PATCH, DELETE), the path, the auth
badge, the spec depth (**behaviour** = executable, **contract** = shape only), and the
trust state.

**Details:**
- **Contract:** params with bounds, request and response models (with nested models
  drawn as a tree), and declared errors.
- **Behaviour:** the Lattice source with guarantees annotated as gutter badges (one
  statement per read, atomic write, owner-scoped), plus a "Run in spec runtime" console
  that sends a sample request and shows the response.
- **Scenarios** using this endpoint.
- **Import provenance:** original file and line, lifter or LLM, verification tally (equal,
  rule, deviation, mismatch).

### 5.7 Spec: Scenarios

Cards in a Given / When / Then layout.
- **Given:** seed rows as small tables.
- **When:** the request.
- **Then:** expected status, body and DB effect (a diff).

Each card has a status pill showing whether the user approved it and its last result,
and an "Emit as test" control with tabs for pytest, Jest and xUnit (showing the
generated code).

### 5.8 Spec: Unchecked requirements

A plain list: the prose text, the entities and endpoints it concerns, who added it and
when, and a **Promote to rule** action that opens the LLM-assisted rule editor (§5.9).

### 5.9 Rule editor and Review queue

**Rule editor (modal, 880 wide):**
- **Left:** the conversation with the LLM (user intent, LLM questions).
- **Right:** the drafted rule as a plain-language sentence plus Lattice source, and the
  checker's verdict on the spec (consistent, or errors with fix hints). Below that are
  generated scenarios the user can accept, edit or reject.
- **Footer:** **Approve rule** (primary), Save as proposed, Discard.

**Review queue (page):** proposed and inferred items grouped by entity, with bulk
approval. Each item shows its evidence (probe results, mined statistics, source lines)
and its effect: how many endpoints and checks it adds.

Keyboard: `J`/`K` to move, `A` to approve, `R` to reject, `E` to edit.

### 5.10 Checks: Runs list

A table with columns: run id, trigger (CLI, CI, agent), spec version, code ref, verdict,
counts by layer, silent count, repair rounds, cost, duration. Filters by verdict, layer
and branch.

### 5.11 Checks: Run detail

**Header:** verdict, code ref, spec ref, duration, cost, tokens and rounds (if an agent
loop), and **Re-run**.

**Layer lanes:** four horizontal lanes (A, B, C, D). Each lane shows findings as chips
coloured by verdict (silent diamonds first), the number of cases run, and coverage, such
as "B: 412 requests · 3 roles · 9 endpoints".

**Findings table:** rule (sentence), layer, verdict, endpoint, file:line (layer A), case
name (B/C/D), and **Open**.

**Selection:** selecting a finding opens the drawer with:
- the rule;
- the evidence: for layer A, the code excerpt with the offending lines highlighted and
  the fix hint (from pycheck's `Finding.hint`); for B/C/D, a link to the counterexample;
- an "Explain to agent" copy button (the same text the repair loop sends).

### 5.12 Counterexample

The most important screen: it must make a silent failure undeniable.

**Request sequence:** a vertical list of the minimal shrunk requests, each with method,
path, caller (role and user), and body.

**Side-by-side comparison:** spec runtime (left) vs the app (right), per request:
- status, with a mismatch highlighted;
- body diff, showing a JSON tree with changed keys marked;
- **database effects diff:** per table, rows added, removed or changed. A diff that should
  be empty but isn't (e.g. a refused request that wrote rows) gets a banner: "A refused
  request changed the database".

**Invariant violations (layer C)** appear under the request that caused them.

**Actions:**
- **Copy as test** (pytest, Jest or xUnit);
- **Copy curl sequence**;
- **Send to agent**;
- **Mark as spec issue**, which opens the rule editor when the user decides the *spec* is
  wrong (this keeps the human in charge of intent).

### 5.13 Repair timeline

For agent-driven changes, a horizontal timeline of rounds. Each node shows:
- the round number and the files the LLM changed (a mini diffstat);
- findings by layer and verdict;
- tokens and cost.

Arrows between rounds are labelled with what feedback was sent. The final node is green
(passed) or grey (budget spent). Clicking a node shows the diff and the findings of that
round. A summary strip shows rounds, total cost, cost per correct change, and wall time.

### 5.14 Import: Report

**Header:** repository, language and framework detected (e.g. "Python · FastAPI ·
SQLModel" or "C# · ASP.NET Core · EF Core"), import date, cost and LLM rounds.

**Coverage by layer:**

| Layer of the spec | Visual |
|---|---|
| Rules (tables, owners, roles, bounds) | progress bar by trust state |
| Contracts (endpoint shapes) | progress bar |
| Behaviour (executable endpoints) | progress bar, with "verified" meaning `verify()` agreed with the running app |

**Routes table:** route, status (verified / contract-only / island / unclear), rung, by
(lifter or LLM), tally (equal / rule / deviation / mismatch), reason, cost. The data
comes from today's `report.json`.

**Unclear decisions:** grouped by topic (owner, route_auth, roles, storage), each with
candidates and **Resolve** (opens Review).

**Fidelity check card:** the result of running the spec's executable parts against the
original app on the fidelity suite, as a pass count, with failures linking to their
counterexamples.

### 5.15 PR check summary (embeddable)

A compact 720-wide frame for CI comments and IDE panels:
- the verdict;
- silent and loud counts by layer;
- the top three findings with links;
- spec coverage for the touched endpoints;
- any unchecked requirements touched by the change.

Export it as an image spec as well as a component for the web app.

### 5.16 Experiments board (evaluator)

A grid of arms × models. Each cell shows correct, silent and loud as a three-segment bar
with Wilson 95% intervals as whiskers, cost per correct change, and mean rounds.

- **Header:** the pre-registered decision as a verdict pill (CONFIRMED / NOT CONFIRMED /
  REFUTED), with the test statistics.
- **Per-ticket strip:** silent counts per arm.
- **Mechanism tags:** silent runs grouped by rule class.

This mirrors `bench/edit_slice/main_analysis.py`'s output.

## 6. Components (Figma)

Build these as components with **variants**, using auto layout and the tokens above.
Every name uses slash grouping: `Lattice/<Group>/<Name>`.

| Component | Variants (properties) | Notes |
|---|---|---|
| `Badge/Trust` | state = verified · inferred · proposed · unchecked; size = sm · md | icon + label; unchecked is dashed |
| `Badge/Verdict` | verdict = pass · silent · loud · equal-rule · none; style = dot · pill | the silent pill reads "Silent" in bold |
| `Badge/Layer` | layer = A · B · C · D | glyph + letter |
| `Chip/Method` | method = GET · POST · PATCH · PUT · DELETE | fixed widths |
| `Chip/Bound` | kind = range · length · enum · pattern | shows `> 0`, `≤ 255`, `OneOf(…)` |
| `Chip/Auth` | kind = Public · Role · Roles · Authenticated | `Roles` shows its members |
| `Node/Entity` | state = trust · selected · hover · has-findings | columns as an inner list; private columns masked |
| `Edge/Relation` | kind = fk · owner · owner_via; state = default · highlighted | owner edges are thicker |
| `Diagram/StateMachine` node + arrow | node = initial · normal · terminal; arrow = legal · observed-illegal | |
| `Row/Rule` | kind = invariant · policy · bound · machine; expanded = yes/no | sentence + source toggle |
| `Row/Finding` | layer = A–D; verdict; selected | |
| `Card/Metric` | kind = coverage · run · review · trend | |
| `Card/Scenario` | status = approved · proposed · failing | Given / When / Then slots |
| `Panel/DiffJSON` | side = spec · app; state = equal · changed | key-level highlights |
| `Panel/DiffDB` | per-table; state = equal · added · removed · changed · should-be-empty | |
| `Timeline/Round` | outcome = pass · findings · budget-spent; selected | |
| `Matrix/PolicyCell` | value = allowed · refused · own · all; trust state | |
| `Bar/Coverage` | segments by trust state | label inside when there is space |
| `Bar/Outcome` | correct · silent · loud segments; with CI whiskers | for the Experiments board |
| `Modal/RuleEditor` | step = draft · checking · ready · errors | |
| `Nav/Left`, `Bar/Top`, `Drawer/Context` | collapsed / expanded | app chrome |

States to draw for every interactive component: default, hover, focus (2-px `accent`
ring), pressed, disabled, loading (skeleton) and error.

## 7. Prototype flows (Figma prototyping)

Link these flows in a `Flows` page with `On click` / `While hovering` interactions and
`Smart animate` between variant states:

1. **Silent failure to fix:** Overview → Silent failures row → Counterexample → Copy as
   test / Send to agent → Repair timeline (round 2 passes) → Run detail (pass).
2. **Spec is wrong, not the code:** Counterexample → Mark as spec issue → Rule editor
   (draft revised rule, checker OK, scenario accepted) → Approve → Re-run → pass.
3. **Import review:** Import report → Unclear decisions → Resolve owner of Product →
   Review queue (evidence from the probe) → Approve → Spec: Data & ownership shows the
   edge verified.
4. **Write a new rule with the LLM:** Spec: Invariants → New → Rule editor conversation →
   checker error (with fix hint) → corrected → scenarios generated → Approve → the
   Invariants list shows the new row as "Verified in spec · not yet run".
5. **PR gate:** PR check summary (embed) → Run detail → finding → code excerpt with hint.

## 8. Content and copy rules

- **Rules are sentences** in the present tense, naming the entity first: "Product rows
  belong to the owner of their Vendor". Avoid jargon such as `owner_via` in sentences;
  show it in the source toggle.
- **Verdict words are fixed:** *Correct*, *Silent*, *Loud*, *Not run*. Never "warning"
  for a silent failure.
- **Evidence phrasing** says who observed what: "Observed by probe: vicky → PATCH
  /products/5 → 403".
- **Unchecked requirements always carry the label "Not checked by Lattice"**.
- **Numbers carry units and denominators**: "8 of 42 runs silent", never a bare 19%.

## 9. Accessibility and responsive behaviour

- **Colour is never the only signal.** Every verdict, trust state and layer has an icon
  or glyph and a text label.
- **Contrast:** AA in both modes. Silent red on surfaces is checked at 4.5:1 for labels.
- **Diagrams** have an accessible list alternative (the Entities list, the Transitions
  table) reachable from a toggle.
- **Keyboard:** every action is reachable. Focus order follows reading order. Graphs
  support arrow-key node traversal.
- **Responsive:** Overview, Counterexample and PR check summary have 1024 and 390 variants
  (stacked lanes, diff panes become tabs). The others are desktop-first.

## 10. Data contracts (what each view reads)

Views read JSON produced by Lattice. The existing shapes are used as they are; new ones
are marked **new**.

| View | Source | Shape (key fields) |
|---|---|---|
| Import report | `bench/imports/<repo>/report.json` (exists) | `coverage`, `budget`, `cost`, `routes[]` {`key`, `method`, `path`, `auth`, `status`, `rung`, `by`, `tier`, `tally`, `reason`, `cost_usd`, `rounds`}, `unclear[]`, `islands`, `behaviour_changes` |
| Data & ownership, Policies | the generated profile (exists: `bench/imports/<repo>/profile.py`) → **new** `lattice spec --json` | `tables{name: {columns[], keys[], owner, owner_via, private[], one_of{}}}`, `roles[]`, `roles_from`, `endpoints[]` {`auth`, `roles`} |
| Layer A findings | `lattice.pycheck` (exists) | `Finding` {`code`, `file`, `line`, `message`, `hint`, `key`} |
| Layer B/C/D cases | oracle outcomes (exist: `bench/edit_slice/oracle.py`) → **new** `lattice check --json` | `Outcome` {`kind`, `cases{name: {verdict, deviation, reason, diff}}`, `unverified[]`}; **new**: `requests[]` (the shrunk sequence), `db_diff{table: {added[], removed[], changed[]}}`, `invariant_violations[]` |
| Repair timeline | edit-slice run records (exist: `RunRecord`) | `rounds`, `round_problems[]`, `round_findings[]`, `input_tokens`, `output_tokens`, `cost_usd`, `wall_s`, `artefacts` (per-round files) |
| Experiments | `bench/edit_slice/main_analysis.py` (exists) | per model × arm: `runs`, `correct`, `silent`, `loud`, Wilson CIs, `fisher_p`, `sign`, `decision`, `per_ticket` |
| State machines, Invariants, Scenarios, Unchecked | **new** spec constructs (direction draft §4.3, §4.4, §4.7, §4.8) | `machines{field: {states[], edges[(from, to, endpoints[])]}}`, `invariants[]` {`name`, `sentence`, `formula`, `sql`, `tables[]`, `trust`}, `scenarios[]` {`given`, `when`, `then`, `status`}, `unchecked[]` |

A small `lattice studio export` command (**new**) would write all of the above into one
`studio.json` per project, so the Figma prototype can be filled with real data from the
multi-vendor example, using a JSON-to-Figma plugin or a later code prototype.

## 11. Figma file structure

| Page | Contents |
|---|---|
| `0 Cover` | title, status, links to the two docs |
| `1 Foundations` | variables (colours with Light/Dark modes, spacing, radius), text styles, effect styles, icon set (shield-check, eye, sparkle-question, text-lines, layer glyphs) |
| `2 Components` | everything in §6, with variant tables and a usage note per component |
| `3 Screens: Spec` | 5.2–5.9 |
| `4 Screens: Checks` | 5.10–5.13, 5.15 |
| `5 Screens: Import` | 5.14 |
| `6 Screens: Overview & Experiments` | 5.1, 5.16 |
| `7 Flows` | the prototype flows of §7 |
| `8 Responsive` | 1024 and 390 variants |
| `9 Archive` | explorations |

**Conventions:**
- every frame uses auto layout;
- no detached instances in screens;
- a frame's name is its screen id (`5.12 Counterexample`);
- sample data comes from the multi-vendor example: users ada, vicky, victor, carol, dave;
  orders 1–9; the t38 reorder counterexample for 5.12; the PY807 finding for 5.11.

## 12. Example content for the key frames

To keep the design honest, fill the hero frames with real cases from the project:
- **5.12 Counterexample (t38 reorder):** carol `POST /orders/8/reorder` → spec: `409
  "Insufficient stock for product 5"` with no DB change; app: `409` with the same body,
  **but** `orders` +1 row (`total 0.0`) and `products` candle stock −1. Show it with the
  "A refused request changed the database" banner. (This is the real slip Sonnet made 3/3
  times in the pilot.)
- **5.11 Run detail:** the PY807 finding: "POST /orders/{order_id}/reorder commits (in
  place_order) at line 33 (decrement_stock_optimistic() (it commits)) and can still fail
  afterwards…", with the fix hint.
- **5.2 Entity graph:** the multi-vendor profile (users, vendors, products, orders,
  order_items), with Product owned via Vendor and Order owned by User, and `hashed_password`
  private.
- **5.3 Policy matrix:** POST /orders: Buyer ✓, Vendor ✕, Admin ✕ (`Roles(Buyer)`, the case
  the role-set feature was built for).
- **5.16 Experiments board:** the option-1 main result (Sonnet P 24/18/0 vs Q 34/8/0;
  CONFIRMED, p = 0.016).

## 13. Open questions

1. **Product surface:** standalone web app, IDE panel (VS Code / JetBrains), or both? The
   components work for both, but navigation differs.
2. **Entity graph:** auto-layout only, or user-arranged with the positions saved in the
   repo?
3. **Experiments board:** in the product, or kept as an internal research tool?
4. **Brand:** keep the neutral palette above, or define a Lattice brand before building?
5. **Next step:** build the Figma file itself (Foundations and Components pages first)
   using the Figma integration, after this spec is approved?
