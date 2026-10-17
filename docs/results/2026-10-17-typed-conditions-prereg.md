# Typed rule conditions (Spike H): pre-registration

## 1. Status, goal and the exact gap

2026-10-17, from `next-phases` 6a318e5 (Spikes A-D, F and G merged). **Written BEFORE any code.** Results:
`2026-10-17-typed-conditions-results.md`, data in `docs/results/data/2026-10-17-typed-conditions/`. Source: the plan's
Addendum "Spike H" (`docs/design/lattice-implemented-logic-spikes-plan.md`). Labels: **(m)** measured during recon in
this container, **(r)** a recon script's output (a blind expectation), everything else a **prediction (p)**. Recon:
code reading at 6a318e5; one scratch script (`$SCRATCH/h/recon.py`, not in the repo) that ran the existing checker,
layers CD, on the minishop fixture and its 10 broken variants. No model was called.

**Goal (owner, binding).** Not a general rule language. A small **typed condition form** for four common rule shapes:
**bounds** on a field, **limits** on a count of rows, **ownership**, and **state transitions** (trigger, condition,
outcome as nodes). Lattice **generates** the scenario and the invariant of such a rule **from the form**, so the two
cannot disagree, and decides **spec-level contradictions between two forms** logically (a widening of Spike D's Tier
2). Everything else stays English rules, scenarios and invariants. A form is **optional per rule**; with no forms
present **every existing output is byte-identical**. A model may draft a form; the owner approves it (Spike G's rule);
nothing is auto-accepted. Mapping a form to the code's conditions is a later question and is **not claimed here**.

**Environment (m), binding on how this spike is scored.** This container has **no Streak checkout** and **no Docker
daemon** (`docker info` fails; the binary exists). `go`, `uv`, `python3`, `node` and the `claude` CLI 2.1.302 exist.
So the plan's registered test ("on Streak, the checks generated from the forms must match the hand-written scenarios
and invariants ... on today's app and on the mutated apps") **cannot run here**: Streak needs a clone and MySQL in
Docker. This document therefore registers **two arms**: the **minishop arm (primary gate, Docker-free, runnable
now)** and the **Streak arm (registered, NOT RUN here, gated when run on a host with both)**. Section 8 lists exactly
what cannot run.

**What exists, and what does not (m).**
1. **The four shapes already have homes in the spec model, but as separate, hand-written pieces.** Bounds:
   `ContractParam.{ge,gt,le,lt,min_len,max_len,one_of}` (request side, layer R) and invariant row rules
   `T.c >= n` (`invariants.py`, layer C). Row limits: the invariant **count bound** form
   `count(T, by=..., where=...) <= n`. Ownership: `Schema.owner` / `owner_via` and `Contract.owner_scope`
   (`owner`, `owner(<col>)`, `owner_or_public(<col>)`, `owner_or_admin`, `any`). Transitions: `Machine(table, column,
   edges, initial)`, checked only by layer A (GO808) and X, never by C or D (Spike D premise 1). Scenarios (`Step`,
   `expect_status`, `expect_db`) are written by hand or by the drafting model. **Nothing ties a scenario and an
   invariant to one statement of the rule**, so they can drift apart: a rule's bound can be 100 in its invariant and
   99 in its scenario, and only a run notices.
2. **Contradictions decided today are narrow.** `lint.conflicts` (L011: same `given`, same first request, different
   status), `preconditions.contradictions` (L012: a row expected then expected absent with nothing between) and D's
   `impact_tier2` D4a (exact prefix identity, incompatible expectation). None reasons about **values**: `ge 5` and
   `le 3` on one field, or two different limits on one count, are invisible unless two scenarios happen to send the
   same request.
3. **`lattice/check` is the reference type discipline.** P4 (`p4_types.py`) types every expression against the
   profile's `Schema`s with `lattice.typesys` (`Scalar`, `NUMERIC`, `unify`); diagnostics are `lattice.diagnostics.
   Diagnostic(code, severity, span, message, hint)`. No `F`-prefixed code exists anywhere in `lattice/` (m). The form
   checker reuses `typesys` and `Diagnostic` **read-only**; `lattice/check/` is not edited (27 files, frozen hash in
   section 7).
4. **Owner approval already has a mechanism.** Spike G's `rationale.py`: an authoritative file written only by an
   `approve` command that refuses without a terminal or with `CLAUDECODE` set, a `.proposed.json` sibling for drafts,
   an `anchor` (sha256 of the rule's normalised text) and an `approved_sha` over the approved content; states
   `current | moved | edited | ambiguous | stale`, only `current` and `moved` count. H reuses this pattern and its
   helpers (`anchor_of`, `norm_text`) read-only.
5. **The minishop fixture runs in-process, without Docker (m).** `tests/spec/minishop.py`: FastAPI + SQLModel on
   sqlite, 4 tables, `Order.status` machine, 2 invariants, 2 base scenarios, 9 access scenarios, 4 `BROKEN` and 6
   `SEC_BROKEN` app variants. `check(SPEC, app, build_db, layers="CD", login=login)` (r):

   | app | findings (layer, check) | time |
   |---|---|---|
   | ok | 0 | 1.53 s (first, imports) |
   | partial_write, wrong_answer | 0, 0 | 0.14 s each |
   | total_drift | 1: C `order_total` | 0.13 s |
   | wrong_quantity | 1: D `ann adds a line to a new order` | 0.13 s |
   | sec: authz, auth, bounds, mass, leak | 0 each (their scenarios are in `attack_spec`, not `SPEC`) | ~0.14 s |
   | sec: state | 1: D `ann cancels her pending order` | 0.16 s |

   So the base spec's hand-written checks already discriminate 3 of 10 variants; ownership, auth and bound mutants
   pass it. The fixture `tests/spec/fixtures/impact/base` gives minishop a `REQUIREMENTS.md` (5 rules) and a trace.
6. **Spike E has not landed** (no commit on `next-phases`). The plan orders H after D, E and F and says H "consumes E's
   before/after runs". H does not need E for its gates: the minishop arm runs today's app and its mutants directly. E's
   use is registered as **not applicable this round** (open question O1).

**Rulings requested.**
- **R1** Storage beside the requirements, Spike G's pattern: `<stem>.forms.json` (approved, authoritative) and
  `<stem>.forms.proposed.json` (drafts). The trace file is not edited (G found that `Trace.from_json` drops unknown
  keys). Schema `lattice.forms/1`.
- **R2** CLI home `python -m lattice.spec forms ...`: one `AUTHORING` entry (`"forms": "lattice.spec.forms"`);
  `lattice --help` unchanged.
- **R3** Generated checks enter a check run **only** when an approved forms file exists and resolves (state `current`
  or `moved`). Then `check` runs `spec + generated` (generated elements carry reserved names, D3). With no forms file,
  the code path that adds them is not entered, so every output is byte-identical by construction (probed by PB).
- **R4** A rule with an approved form and hand-written checks keeps both. A **form-vs-hand-written** disagreement that
  is decidable (an identical request with an incompatible expectation, D5c) is a Tier 2 contradiction and a new lint
  code **L013**, shown only when forms exist.
- **R5** No new MCP tool and no Studio change this round. `change_impact` gains form contradictions only through D's
  report (D5). Contracts (layer R) are not generated from forms this round.
- **R6** The gated set (rule shapes, hand-written checks, app mutants, truth table, contradiction pairs, not-expressible
  rules) is written and hashed by a **separate agent** before the build node runs a gated probe (section 3.1).

## 2. Decisions (fixed before coding)

**D1 The closed node set.** A form is JSON. The grammar below is the **whole** language: no variables, no references
between forms, no arithmetic, no functions, no loops, no user-defined nodes. A key outside it is a type error (F001).

| node | fields (types) | meaning |
|---|---|---|
| `Field` | `{"column": "Table.col"}` or `{"param": "METHOD /tpl", "path": "body.a.b" \| "query.x" \| "path.x"}` | a stored column, or a request value |
| `Lit` | int, float, string, bool, null | typed against the field (P4's `unify`); a float for an INT field is F004 |
| `Cmp` | `op`: `ge gt le lt eq ne`; `value`: Lit | numeric or equality comparison |
| `Len` | `op`: `min_len max_len`; `value`: non-negative int | text length |
| `In` | `values`: list of Lit (<= 32) | membership (`one_of`) |
| `Where` | `{col: Lit, ...}` (<= 4 keys, equality only, one table) | the rows a limit counts |
| `Endpoint` | `"METHOD /tpl"` (a scenario-step template the spec already uses, as L006's `template_of`) | the trigger |
| `Caller` | `"<handle of Spec.users>"`, `"anon"`, `"owner"`, `"other"` | who sends it |
| `Request` | `{path, json?, params?}`: a concrete witness request that the rule accepts | the generator varies only the form's field |
| `Given` | `{table: [row, ...]}` (the `Scenario.given` form) | the prior state |
| `State` | `{"machine": "Table.col", "value": s}`; `s` a state the `Machine` names | a machine state |
| `Outcome` | `{"status": int, "db": "unchanged" \| {"equals": {col: Lit}} \| {"count": int}}` | the answer and its effect |

Four **kinds**, each a fixed record of those nodes (and nothing else):
- **`bound`**: `{field, cond: Cmp | Len | In, endpoint?, caller?, request?, given?, refused: Outcome}`. On a column
  without an endpoint it states a stored-value rule; with an endpoint it also states the refusal of a request beyond
  the bound.
- **`row_limit`**: `{table, by: [col, ...], where?: Where, max: int >= 1, endpoint, caller, request, given?, refused:
  Outcome}`.
- **`ownership`**: `{table, scope: one of Contract.owner_scope's forms, endpoints: [Endpoint], owner: handle, other:
  handle, given?, row: Request-path value, refused_other: Outcome, refused_anon: Outcome}`.
- **`transition`**: `{machine: "Table.col", trigger: Endpoint, caller, request, row: {key: Lit}, from: [state, ...],
  to: state | null, accepted: Outcome, refused: Outcome}`. `to: null` means the trigger leaves the state unchanged
  (a read). The condition node is `from` (a set of states); a further guard is not expressible this round.

**D2 Form file and approval** (`lattice.forms/1`, R1). `{"schema", "source", "forms": [{"id": "F<n>", "rule": "R<n>",
"anchor", "kind", "form": {...}, "status": approved|proposed|rejected, "author": owner|llm:<model>|hand,
"approved_by", "approved_sha"}]}`. `approved_sha` is the sha256 of the canonical JSON of `{kind, form}`. **States**
exactly as G's (D3 there): `edited`, `current`, `moved`, `ambiguous`, `stale`; only `current` and `moved` generate.
Commands: `forms show | check | generate | contradictions | coverage` (read-only, write nothing),
`forms draft --rule R ... --out FILE [--model M]` (calls only the `claude` CLI, never an API key; writes only the
`--out` proposals file, which must be the `.proposed.json` sibling or outside the project), and `forms approve |
reject` (refuse without a TTY or with `CLAUDECODE` set; write one file atomically). `check` type-checks every form
against the spec (D4) and exits 1 on any error. A proposal never generates a check, anywhere.

**D3 Generation (`forms.generate(spec, forms) -> Generated{scenarios, invariants, links}`).** A pure function: no
I/O, no clock, no randomness, sorted output. Element names are reserved: `form:<F-id>:<case>` (a name the hand-written
spec cannot already use: `forms check` refuses a spec that has one, F010). Per kind:

| kind | invariant (layer C) | scenarios (layer D), every refused step also expects `db: unchanged` unless the form says otherwise |
|---|---|---|
| `bound` on a column | the row rule `T.c <op> v` (or `T.c in (...)`, or none for `Len`, which the invariant grammar has no form for: listed as `no_invariant: length`) | with an endpoint: `at_bound` (the witness request with the field at the bound: accepted with the form's accepted status), `beyond` (one step past it: `refused`). INT steps are +-1; FLOAT steps use the nearest value that differs by more than D's 2x tolerance; `In` uses one listed value and one value not listed (the generator takes the first string, sorted, that is not in the list: `"~"*k`) |
| `bound` on a param | none | `at_bound`, `beyond` as above |
| `row_limit` | `count(T, by=(...), where=...) <= max` (the existing count-bound form) | `below` (given max-1 counted rows: the request is accepted, the count becomes max), `at_limit` (given max rows: `refused`, count stays max) |
| `ownership` | none | per endpoint: `owner` (accepted), `other` (`refused_other`), `anon` (`refused_anon`); writes expect `db: unchanged` for other and anon |
| `transition` | `T.c in (machine states)` | `from_<s>` for each `s` in `from` (accepted, `db.equals {c: to}`); `refused_<s>` for every machine state not in `from` (refused, unchanged) |

Givens: the form's `given`, plus the minimal rows the case needs (the counted rows for a limit, the row in state `s`
for a transition), with keys chosen above the largest key in the form's `given` and the seed (from the spec's `users`
and the given rows only; never from the app). The generated `links` map each element to the form's rule
(`rule -> ["form:F3:at_bound", ...]`), merged into an **in-memory** effective trace; the trace file is not touched.
Every generated invariant must compile under `compile_invariant` for the sqlite and mysql dialects, and every generated
scenario must pass `validate` and produce **no L011 or L012** finding among the generated elements (else F020, a
generator bug, never shipped silently).

**D4 Type checking (codes `F001`-`F019`, `lattice.diagnostics.Diagnostic`, span = the form's JSON pointer).** F001
unknown key or node; F002 unknown table or column (resolved by `Spec.table`); F003 unknown endpoint template; F004 a
literal's type does not unify with the field's `Scalar`; F005 an `In` value outside the profile's `one_of`; F006
ordering a `one_of` column (as the invariant grammar refuses); F007 unknown user handle; F008 unknown machine or
state; F009 `from` empty or `to` in `from` for a writing trigger; F010 reserved element name in the hand-written
spec; F011 `max < 1` or a negative length; F012 `where` on a column not in the table; F013 an ownership scope on a
table with no `owner`/`owner_via`; F014 a witness request that does not match its endpoint template; F015 two forms
with the same id; F016 a form for a rule not in the trace; F017 a form node the grammar lists as not expressible
(D6); F018 `Outcome.status` outside 100-599; F019 a transition edge `(from, to)` that the spec's `Machine` does not
have (a contradiction with the machine, also reported by D5).

**D5 Tier 2: contradictions decided from forms.** `forms.contradictions(spec, forms) -> [item]`, pure. Two forms
**contradict** when no app can pass both forms' generated checks. Decision procedures, one per pair class:
- **(a) bound x bound, same field:** the intersection of the two value sets is empty. Intervals with open and closed
  ends; on INT, `gt a` is `ge a+1` and `lt b` is `le b-1` (so `gt 3` and `lt 4` is empty on INT, not on FLOAT);
  `In` is a finite set intersected with intervals and `ne`; `Len` intervals intersect separately. A column bound and
  a param bound on the param's stored column (`ContractParam.column`, or the form's own `stores: Table.col`) also
  contradict when the param's accepted set has a value outside the column's set **and** the endpoint's accepted
  outcome writes it (`db.equals` on that column): the accepted request must store a forbidden value.
- **(b) row_limit x row_limit, same (table, by, where):** `max` differs (the larger limit's `below` case accepts the
  request the smaller's `at_limit` refuses).
- **(c) ownership x ownership, same table and endpoint:** the scopes differ in who may act (as `Contract.owner_scope`
  is read by layer R), or the `refused_other` / `refused_anon` statuses differ.
- **(d) transition x transition, same machine and trigger:** a state is in both `from` sets with different `to` or
  accepted outcomes, or in one `from` and refused by the other. **(d')** transition x `Machine`: F019.
- **(e) any pair, by generated steps:** two generated steps (from two forms, or **one form and one hand-written step:
  L013, R4**) with canonically equal `given`, prefix and request (D's D4a identity, `impact_tier2.canon_given`,
  `canon_request`) and an incompatible expectation (`impact_tier2.incompatible`).

Each item: `{id, kind: "form_contradiction", forms: [F, F] | [F, "scenario:<name>"], rules, class: a..e, witness,
why}`. **`witness`** is a concrete value, state or request on which the two disagree; an item is emitted only if
the witness, placed into both forms' generated cases, gives two expectations that D's `incompatible` calls
incompatible (a self-check: no witness, no claim). **Widening D's Tier 2:** when either side of an impact report has
an approved forms file, `tier2.determined[]` gains these items (status `determined`, `tier: 2`), and
`decidability[]` entries for formed rules may read `determined` with reason `form`. Without forms the report is
byte-identical (PB). The only edit to D's modules is that additive branch in `impact_tier2.py`.

**D6 Not expressible (reported, never guessed).** A rule shape outside D1 gets `not_expressible:<category>` with a
reason. Categories (closed): `time` (dates, durations, deadlines, schedules, "within N days", a test clock),
`money` (sums, totals, currency, rounding: the aggregate-equality invariant stays hand-written), `rate` (N per minute
or per day, throttling), `external` (push, email, payment, any service outside the app's database), `cross_request`
(a rule over a sequence of two or more triggers: "after X, Y is refused", flows), `background` (an `Await`, a
worker), `aggregate` (any aggregate except a count), `cross_table` (a condition joining two tables, beyond `owner_via`),
`response_shape` (field names, ordering, pagination, formats), `role` (role-based access other than the owner scopes),
`effect_value` (a stored value equal to a request value or computed from it, e.g. "the stored line has the requested
quantity"), `disjunction` (an `or` across fields or kinds), `other`. `forms coverage` reports, per rule: `formed`,
`not_expressible:<category>`, `english` (no form, not classified). The classification of an English rule is done by
the drafter (a proposal) or by the owner; the deterministic part is only that **no form outside D1 type-checks**
(F001/F017).

**D7 Never:** write the spec, the trace, `REQUIREMENTS.md`, the app or the inventory; generate from a proposal or an
`edited`/`stale` form; call a model outside `forms draft`; use an API key; approve anything; edit `lattice/check/`,
`check.py`, `model.py`, `invariants.py`, `lint.py` (L013 is added by a wrapper in the new module and registered only
when forms exist; ruling R4), `rationale.py`, `impact_report.py`, `impact_tier3.py`; add a key to any existing JSON
output when no forms file exists.

## 3. Probes (runner `dogfood/forms_probes.py`; blind pass rules)

### 3.1 The frozen set (held out; written by a separate agent; this author does not write it)

**Who and how.** After this prereg is committed, the orchestrator dispatches **one separate agent** (neither this
author nor the builder) through the `claude` CLI (no API key; `--max-budget-usd` capped; NOT RUN on a usage limit,
then retried, never replaced by the builder). Its prompt contains only: section D1 and D6 of this document verbatim,
read access to `tests/spec/minishop.py` and `tests/spec/fixtures/impact/base/`, and the output contract below. It must
not read `lattice/spec/forms*.py` (absent at that time anyway). It writes to `$SCRATCH/hshapes/` (outside the repo):

| file | content | minimum |
|---|---|---|
| `minishop/REQUIREMENTS.md` | English rules over minishop's routes and tables, **>= 3 per kind** (bounds, limits, ownership, transitions) plus the 5 base rules | >= 16 rules |
| `minishop/handwritten.py` | a `Spec` (minishop's profile, programs and users) with the agent's **own** hand-written scenarios and invariants for those rules, as a spec author would write them, and a trace | every formed rule linked |
| `minishop/forms.json` | `lattice.forms/1`, one approved form per expressible rule (`author: hand`, `approved_by: shapes-agent`) | >= 12 forms |
| `minishop/mutants.json` | app edits `(file, old, new)` in `minishop.BROKEN`'s format, each aimed at one rule's condition (off-by-one bound, missing owner check on one route, wrong refused status, an extra legal transition, limit off by one, a refused write that still writes, ...) | >= 10 |
| `minishop/truth.json` | for each (rule, app) with app in {ok, the 10 existing variants, the agent's mutants}: `violates: true|false` and a one-line reason, judged from the English rule and the edit, **not** from a run | all cells |
| `minishop/h_verdicts.json` | the agent's run of its hand-written checks (existing `check`, layers CD) on every app: pass/fail per rule; cells where it disagrees with `truth` are marked `h_incomplete` with a reason | all cells |
| `pairs.json` | form pairs over minishop: **>= 8 contradictory** (with the witness) and **>= 8 compatible near-misses** (`ge 1`/`le 1` on INT; `gt 3`/`lt 4` on FLOAT; equal limits; disjoint `from` sets; ...), each with class a-e; >= 2 form-vs-hand-written pairs (class e) | >= 16 |
| `invalid.json` | forms that must be refused, each with the expected F-code | >= 15, >= 10 distinct codes |
| `not_expressible.json` | English rules over minishop and Streak-like apps that must not be formed, each with its D6 category | >= 12, >= 6 categories |
| `streak/` | the same files for Streak a0679e9 (rules picked from its 148), written when a Streak checkout is available; not part of this round's gate | registered |
| `MANIFEST.sha256` | sha256 of every file, sorted by path | |

The orchestrator copies `MANIFEST.sha256` to `docs/results/data/2026-10-17-typed-conditions/shapes.manifest.sha256`
and commits it **before the build node runs any gated probe**. The builder never opens `$SCRATCH/hshapes/`; the runner
reads it with `--shapes DIR` and checks the manifest first (a mismatch aborts the run). If the agent's own
`h_verdicts.json` shows a hand-written check that fails on `ok`, that rule is fixed by the agent before the freeze, or
excluded with the reason, never after.

### 3.2 Primary gate: the minishop arm (Docker-free, runnable here)

A **cell** is (formed rule, app). The verdict of a set of checks on an app is `pass` iff the CD run has no finding in
those checks. **G** = the rule's generated elements (`form:F*:*`), **H** = the agent's hand-written elements linked to
the rule, **T** = `truth.json`. Apps: ok + 4 `BROKEN` + 6 `SEC_BROKEN` + the agent's >= 10 mutants (>= 21 apps), so
>= 12 x 21 = **>= 252 cells**.

| id | probe | prediction | pass |
|---|---|---|---|
| PG1 | G vs H per cell | G == H on 90-100% of cells (p); every G != H cell is an `h_incomplete` cell where G == T | **0 cells with G != H and H == T**; every G != H cell is one where H != T |
| PG1t | G vs T per cell | G == T on 95-100% (p) | **100%** (a G miss or a G false fail is a finding, by kind) |
| PG1o | on `ok`, every generated element passes | 0 failing (p) | 0, except rules T marks violated on `ok` (then G fails exactly those) |
| PG1k | mutants killed: an app is killed by G (or H) when some cell flips to fail | G kills >= every mutant H kills, and 3-6 more of the 21 (p: sec `bounds`, `authz`, `auth` and limit mutants that H misses) | G kills a superset of H's kills |
| PG2 | layer of the failing check, where both G and H fail | the same layer in >= 80% (p): H often uses a scenario where G adds an invariant | reported |
| PG3 | `forms generate` 10x, random `PYTHONHASHSEED`, two scratch roots; the CD reports 10x | byte-identical; no absolute path or time in output | 10/10 |
| PG4 | every generated invariant compiles (sqlite, mysql dialect strings); every generated scenario validates; 0 L011/L012 among generated elements | 100% (p) | 100% |
| PG5 | `invalid.json` refused with the expected F-code; the agent's valid forms accepted | 15/15 refused (p), codes exact 90-100% (p) | **0 invalid accepted**; 0 valid refused; codes reported |
| PT2f | `forms contradictions` on `pairs.json` | precision 1.0; recall on classes a-d 1.0, on e 0.8-1.0 (p) | **precision 1.0; recall 1.0 on classes a-d**; class e reported |
| PT2x | witness self-check: each emitted item's witness gives `incompatible` expectations in both generated cases | 100% | 100% |
| PT2w | D's impact report on a minishop candidate with two contradictory forms (one in base, one in candidate): `tier2.determined` gains exactly the form items; on D's 7 fixtures under `tests/spec/fixtures/impact/` without forms: byte-identical to the 6a318e5 output | as stated | exact; identical |
| PNX | `not_expressible.json`: a form built for each by the agent's own best attempt (if any) is refused (F001/F017); `forms coverage` lists them as given | 100% refused (p) | 0 accepted |
| PA | approval: `draft` writes only the proposals file; a proposal generates nothing; `approve` refuses with no TTY and with `CLAUDECODE` set; an approved form whose `{kind, form}` is edited by hand is `edited` and generates nothing; a rule rewording makes it `stale` (or `moved` when the text moved) | as stated | all |
| PW | snapshot (sha256, mode, mtime, listing with hidden files) of the spec dir, the forms files and the app dir around `show`, `check`, `generate`, `contradictions`, `coverage` x10 and a check run with forms; a recording `claude` shim first on `PATH` | 0 changed, 0 new, 0 shim calls | all 0 |
| PB | byte-compat, below | all | all |
| PX | wall time: `generate` on the frozen forms; the whole minishop arm (>= 21 apps x CD with G and H) | 0.05-0.3 s; 15-60 s (p, from section 1 item 5) | <= 1 s; <= 180 s |

**PB checks (no forms file anywhere), against `git archive 6a318e5`:** identical bytes for: `lattice --help`;
`python -m lattice.spec check --help`, `lint --help`, `impact --help`; `lint --json` and `check --json` (CD) on the
minishop base fixture and on `attack_spec`; `lattice spec impact --tiers 23 --json` on each of D's 7 fixtures;
`appmap --json` and `rationale coverage --json` on the implementation fixture. `python -m lattice.spec` usage differs by
exactly one `AUTHORING` line (`forms`). The files of section 7 keep their hashes. `pytest tests/spec tests/mcp
tests/studio tests/importer tests/gocheck` shows only the pre-existing failure ids (recorded at build start, before any
edit). MCP `tools/list` and the Studio route table are unchanged (R5).

### 3.3 Registered, NOT RUN here: the Streak arm

The plan's registered test, kept with its numbers so it can run on a host with a Streak checkout and Docker MySQL 8.4.
Corpus: Streak a0679e9 (S), the agent's `streak/` files, and as mutated apps D's change-set reference diffs applied
in reverse where they touch a formed rule, plus the agent's Streak mutants (>= 10).

| id | probe | prediction | pass |
|---|---|---|---|
| PS0 | `forms coverage` on S's 148 rules | formed 22-45 (15-30%) (p); `not_expressible` 40-70, `time` and `cross_request` the largest (p) | reported |
| PS1 | PG1 on S cells | G == H 85-100% (p) | as PG1 |
| PS1t | PG1t on S | 90-100% (p) | as PG1t |
| PS2 | PT2f on Streak pairs; the 50-active-habit limit stated twice with 50 and 60 is determined | as PT2f | as PT2f |
| PS3 | PG3 on S: CD with G 10x | byte-identical except `Await` timing (excluded, D's PT1x risk) | 10/10 |
| PSx | wall time: CD on S with G | 30-60 s per run (p; S's own-spec CD is 29.6 s in D, m there) | <= 180 s |

Here every PS row is recorded `NOT RUN: no Streak checkout, no Docker daemon` in the results doc, with no number and
no substitute. Passing the minishop arm does **not** stand in for the Streak arm; the results doc says so.

## 4. Mutants of the implementation (`dogfood/forms_mutants.py`, on a scratch copy; the control passes first; predicted 16/16)

| id | mutant | killed by |
|---|---|---|
| HX1 | a refused step's `db: unchanged` dropped | PG1 on `partial_write`-like mutant; unit |
| HX2 | `ge` generated as `gt` (boundary off by one) | PG1 (`at_bound` fails on ok); unit |
| HX3 | INT tightening ignored (`gt 3`, `lt 4` on INT satisfiable) | PT2f recall (class a) |
| HX4 | two limits with different `where` treated as the same count | PT2f precision |
| HX5 | the ownership generator omits the `anon` case | PG1k on `sec:auth` |
| HX6 | the transition generator omits the `refused_<s>` cases | PG1k on `sec:state`-like mutants |
| HX7 | a proposal generates checks | PA |
| HX8 | an `edited` approved form still generates | PA |
| HX9 | the check output gains `"forms": null` when no file exists | PB |
| HX10 | generated names from set iteration order | PG3 |
| HX11 | a `now()` / `sum` node accepted | PNX, PG5 |
| HX12 | contradiction items emitted without the witness self-check | PT2x (a planted false pair) |
| HX13 | `row_limit` `below` case given `max` rows instead of `max - 1` | PG1o |
| HX14 | the generated count invariant uses `<` for `<=` | PG1o |
| HX15 | `one_of` membership not type-checked (F005 skipped) | PG5 |
| HX16 | `generate` writes a cache file next to the forms | PW |

## 5. Risks, and what this cannot show

1. **Expressiveness creep.** Every shape request beyond D1 is pressure toward a language. Mitigation: the node set is
   closed in this document; a new node is a new pre-registration; out-of-set rules are `not_expressible`, counted.
2. **"Match the hand-written checks" is the wrong target where the hand-written check is weaker.** Generated checks
   probe boundaries and every refused state, hand-written ones often a single value. So PG1 is scored against the
   frozen truth table: G may differ from H only where H is wrong. The truth table is one agent's judgement (risk 4).
3. **The witness request carries intent.** A form needs a valid request and a given row; the generator only varies the
   form's field. So "generated from the form" means "from the form plus its witness", stated as such; a wrong witness
   makes both `at_bound` and `beyond` fail, which PG1o catches on `ok`.
4. **One agent writes rules, checks, mutants and truth.** Its truth labels and its hand-written checks may share a
   blind spot. The builder never sees them, and H's kills are reported beside G's; there is no second labeller.
5. **minishop is small** (5 routes, 4 tables, sqlite). 12-16 formed rules and 21 apps can hide shape problems that
   Streak's MySQL types, `Body` accessor bounds and `Routes{Public, Member}` ownership would show. The Streak arm is
   registered for that; until it runs, the spike's claim is limited to the fixture.
6. **sqlite vs MySQL.** Generated invariants are compiled for both dialects (PG4) but run only on sqlite here; MySQL's
   case-insensitive collation can change a text `In` or `eq` (documented in `invariants.py`).
7. **Tier 2 soundness rests on the forms' semantics,** not on the app: a determined contradiction means no app passes
   both forms' generated checks, which is true by construction of the witness self-check, and says nothing about
   whether the owner meant either form.
8. **Cannot show:** a form-to-code mapping (out of scope, plan Addendum); E's before/after use of forms (E not landed);
   drafting quality of the model (report-only, D2); the Streak arm (section 8).

## 6. Blind vs post hoc, and commands

Fixed now. A missed prediction is a **finding**; fixes are separate post-hoc commits, rerun with deltas reported and
labelled post hoc. The orchestrator scores the frozen set **once**, after `lattice/spec/forms*.py` is hashed into
`$SCRATCH/freeze.sha256`; a fix made after seeing the set is never re-scored as blind. If the agent's set is short of
a minimum in 3.1, the agent adds to it **before** any scoring. The drafter (`forms draft` through `claude`) is
report-only: its proposals are scored for "type-checks" and "kind matches the agent's form" and never gated, with a
USD cap per call; a usage limit is NOT RUN. **Pass:** every gated row of section 3.2, mutants 16/16 (by probes or named
unit tests, disclosed), PB. The Streak arm's rows are NOT RUN, which is not a pass.

```sh
export UV_NO_SYNC=1 PYTHONDONTWRITEBYTECODE=1
SCRATCH=<the build node's scratchpad>/forms
cd <worktree>
uv run python -m pytest tests/spec tests/mcp tests/studio tests/importer tests/gocheck -q -rs > "$SCRATCH/pre.txt"  # before any edit
sha256sum lattice/spec/forms*.py > "$SCRATCH/freeze.sha256"                          # BEFORE the gated run
uv run python dogfood/forms_probes.py --stage dev --scratch "$SCRATCH/p" --out "$SCRATCH/p-data"   # PB, PW, PA, PG3 on dev forms
uv run python dogfood/forms_probes.py --stage frozen --shapes "$SCRATCH/../hshapes" \
    --manifest docs/results/data/2026-10-17-typed-conditions/shapes.manifest.sha256 \
    --scratch "$SCRATCH/f" --out "$SCRATCH/f-data"                                    # orchestrator, once
uv run python dogfood/forms_mutants.py $(seq -f HX%g 1 16)
uv run python -m pytest tests/spec/test_forms.py -q
uv run ruff check <changed .py>; uv run ruff format --check <created .py>
# Streak arm (NOT RUN here): --stage streak --streak-ref a0679e9 --shapes .../hshapes/streak (needs Docker MySQL 8.4)
```

## 7. File ownership, frozen hashes and the byte-compat promise

**Owns (build node, no commits):** new `lattice/spec/forms.py` (schema, states, type checker, generator, CLI),
`lattice/spec/forms_tier2.py` (D5); `lattice/spec/__main__.py`: one `AUTHORING` line; `lattice/spec/impact_tier2.py`:
one additive branch taken only when a forms file is present (D5); new `tests/spec/test_forms.py` and
`tests/spec/fixtures/forms/` (the builder's own **dev** forms on minishop, distinct from the frozen set); new
`dogfood/forms_probes.py`, `dogfood/forms_mutants.py`; the results doc and its data directory. The orchestrator stages
explicit paths only; never `git add -A`.
**Reuses read-only:** `typesys`, `diagnostics`, `model`, `invariants.compile_invariant`, `validate`, `lint.conflicts`,
`preconditions.contradictions`, `impact_tier2.{canon_given, canon_request, incompatible}`, `rationale.{anchor_of,
norm_text}`, `check.check`, `tests/spec/minishop.py`.
**Frozen at 6a318e5 (sha256; the build must leave each unchanged; any change is a finding):**

| file or set | sha256 |
|---|---|
| `lattice/spec/model.py` | `a7f7b3f834f22a89004f6bfb69484f6c4ff35caf86a32f4254c6a1182c9ec9ce` |
| `lattice/spec/check.py` | `68f13b288cde4a3fa348412495ba560815e003b342ea9cca90b1a779c1bb525e` |
| `lattice/spec/invariants.py` | `c3f374756d0b4186bea4df00c1f910ed2942b8bfcb771a8df472793d48354a73` |
| `lattice/spec/lint.py` | `0462853748f3c0101db4f00633c708e98f209fde526fb7eb6563c665f087b442` |
| `lattice/spec/preconditions.py` | `f1d8abee4f8d92b26fac557081967757736a7f3c8d0aaaa806134b8136595687` |
| `lattice/spec/rules.py` | `e3c0bc041595de280abae7c9beebbab64f09e0fd55182e173ee623c4dfddb248` |
| `lattice/spec/incremental.py` | `29ded6ecbbddcc55a5d94914cf9bce82fb9ee61a10d3453d70c336565c6090e6` |
| `lattice/spec/rationale.py` | `ea0f05dd47a27934d0e6d5d6e20d0964c1449f7667966e8c5c4e495de9c434e9` |
| `lattice/spec/impact_report.py` | `a3e11f1c266a813a66e33d8943b8773705a419ce8dde806615203d750ae0137c` |
| `lattice/spec/impact_tier3.py` | `22228513beba44fcff4b0d5ab48c662d2010e7ac320b73ac860f134ffb6e2faf` |
| `lattice/spec/validate.py` | `97691c98b66a49f656315dea00225e1465861e99533e5886b10718be1427b012` |
| `tests/spec/minishop.py` | `90117594e10c61272b8896bfc631623c5d23c4689d8e1853649cf73810baccfd` |
| `lattice/check/**/*.py` (27 files: `sha256sum` sorted, then `sha256sum`) | `ca52b5a0076a04328c97c1cdafdd50af1a557ac671795697f9ec448146ba57bf` |
| `lattice/gocheck/**` (same method) | `b7a4dacfe0dd049a19fe58fb838b9c83e25cf0931433770d16e52df1dc761ba1` |
| `tests/spec/fixtures/impact/**` (same method) | `291eab2058ca5c86079b73287d1feff42e97afcd9a53f2358322df7b75473d1b` |

Baselines, not frozen (edited additively): `lattice/spec/impact_tier2.py` `6f86fc6c...a90a`, `lattice/spec/__main__.py`
`e7ca4bf4...a109a383`. **Byte-compat promise:** with no `*.forms.json` / `*.forms.proposed.json` file, every existing
command, report, lint output, impact report, app map, MCP listing and Studio response is byte-identical to 6a318e5,
except the one `forms` line in `python -m lattice.spec`'s usage. Existing tests and goldens are not edited.

## 8. What cannot be run here

- **The Streak arm (3.3, PS0-PSx):** no Streak checkout and no Docker daemon in this container. Registered NOT RUN,
  with its numbers; the agent's `streak/` files are written only when a checkout is available.
- **E's before/after runs** (the plan's "consumes E's runs"): Spike E has not landed. NOT RUN, not applicable.
- **MySQL execution of generated invariants:** compiled to MySQL SQL text (PG4) but executed only on sqlite.
- **The model drafter at scale:** `forms draft` goes through the `claude` CLI only (present, 2.1.302), report-only,
  capped; on a usage limit it is NOT RUN. Nothing gated depends on it.
- **A held-out second app:** none exists with forms; the frozen minishop set by a separate agent is the held-out part.

## 9. Open questions for the orchestrator

- **O1** Spike E has not landed. Accept H's gates without E's before/after runs (this document's position), or wait?
- **O2** "the mutated apps of the kill rig": the repo's kill rig (`improve_kill_rig_probes.py`) is an events-log rig,
  not app mutants. This document reads it as app mutants: minishop's `BROKEN` + `SEC_BROKEN` plus the agent's, and on
  Streak D's change-set reference diffs plus the agent's. Confirm.
- **O3** R3: should generated checks join a plain `lattice spec check` run when approved forms exist, or only `forms`
  subcommands and an explicit `--forms` flag (stricter byte-compat, but forms then do not replace drift-prone checks
  by default)?
- **O4** R4/L013: a new lint code shown only with forms. Acceptable, or keep form-vs-hand-written disagreements in
  `forms contradictions` and D's Tier 2 only?

## Amendment 1 (orchestrator rulings, 2026-10-17; defaults, overturnable)

- O1: accept gating H without E's before/after runs; H's Tier 2 widening is additive and E can adopt it later. O2: confirmed: the kill rig means minishop `BROKEN` + `SEC_BROKEN` variants plus the agent's mutants; on Streak, D's reference diffs plus the agent's. O3: approved forms' generated checks run only through the `forms` subcommands or an explicit `--forms` flag, never a plain `lattice spec check`. O4: no new lint code; a form/hand-written disagreement is reported in `forms contradictions` and Tier 2 only.
- The Streak arm (PS0-PSx) is NOT RUN here. The separate-agent frozen rule-shape set must be hashed and committed before the builder runs any gated probe, and the builder never opens it.

## Amendment 2 (frozen set delivered, 2026-10-17)

The separate agent wrote the frozen set to a scratch directory outside the repository; it has not been opened by the builder or the orchestrator. MANIFEST.sha256 sha256 = 182fe629e10241eded91e307abe8296db0a5877a08941fbdecef81a69d83af13 (9 files; 20 rules, 16 forms, 14 author mutants, 500 truth cells, 32 contradiction pairs, 29 invalid forms, 18 not-expressible rules). Ruling: rules R12-R14 (row-count limits) are violated on the clean minishop app by construction; they stay in the set as "violated on ok" per PG1o, which allows that, and section 3.1's "fix or exclude" applies only to hand-written checks that are wrong. The builder's runner copy of the manifest is data/2026-10-17-typed-conditions/shapes.manifest.sha256.
