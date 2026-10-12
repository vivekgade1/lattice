# Thin end-to-end slice: import → LLM edit → emit → verify (plan)

> Approved direction 2026-09-27: prove the vision's core claim, that LLM changes made through Lattice are safer than changes made to the code directly, on one real codebase, before building out the importer. The importer's I5 (LLM loop) and I6b (method experiment) wait until after this slice; I2b and I6a continue.

**Question:** on the official FastAPI template, given realistic change tickets, does an LLM editing the imported Lattice (then emitted back to FastAPI) produce correct changes more often, with fewer silent bugs, and at what cost, than the same LLM editing the Python directly?

## Arms (same model, same ticket text, same repair budget)
- **P (Python edit):** the model gets the template's relevant route/model files plus the ticket, and returns edited files. It gets up to 2 repair rounds, fed by crash tracebacks or failing smoke requests (the same information as B gets from the checker).
- **L (Lattice edit):** the model gets the imported Lattice programs + profile + the API guide plus the ticket, and returns edited Lattice/profile. It gets up to 2 checker repair rounds. The result is emitted to FastAPI and run.

## Tickets (bench/edit_slice/tickets.py), 6 realistic changes with implicit traps
1. Search items by title substring (`q`), keeping owner scoping and page cap. (Trap: search leaks other users' items.)
2. Sort items newest first, with a `since` DateTime filter. (Trap: invalid datetime → 422, not 500.)
3. Soft delete: `deleted_at` column; delete sets it; lists and reads hide deleted items; the count excludes them. (Traps: the count, and reading a deleted item.)
4. Admin restore endpoint for soft-deleted items. (Trap: non-admin → 403.)
5. Titles editable only within 24h of creation, else 403 with a detail. (Trap: `now` evaluated once; admin bypass per ticket text.)
6. Per-user item stats `GET /items/stats` returning count and latest `created_at` for the caller. (Traps: owner scoping, empty set.)

## Oracle (bench/edit_slice/oracle.py)
- A hand-written reference implementation of each ticket on the Python template (`bench/edit_slice/reference/`).
- A hidden acceptance suite per ticket: the generated suite from `lattice/importer/suite.py` for the touched routes, plus ticket-specific cases including the traps.
- The comparison uses `lattice/importer/verify.py`, with the same representation rules as the template proof. Each Lattice policy deviation is pre-declared per ticket.
- **Outcome per run:**
  - correct — all cases equal or declared;
  - loud — crash, 500s, or unparseable output;
  - silent — a wrong non-5xx answer or wrong DB effect on any case.

## Emitter (thin): `lattice/emit/thin.py`
The edited Lattice goes through the existing `compile_source_to_fastapi` to produce a router module mounted into the template app in place of the lifted routers (the natives call the template's own `core/security.py`). Everything else in the repo is untouched. Diff minimality and repo-style SQLModel emission are sub-project 3; the slice reports emitted size but doesn't optimise it.

## Metrics
- per arm and model: correct / loud / silent;
- cost, tokens, repair rounds, wall time;
- edited-artefact size (lines changed);
- emitted code size;
- latency and queries per request on the acceptance suite.

## Models and runs
claude-sonnet-5 and claude-haiku-4-5, 3 reps per ticket per arm: 6 × 2 × 2 × 3 = 72 runs. The cost estimate is reported before the live run.

## Nodes
| Node | Depends on | Deliverable | Gate |
|---|---|---|---|
| E1 Tickets + oracle | — | tickets, reference implementations, acceptance suites, pre-declared deviations | each reference passes its own suite; the unmodified template fails each ticket's suite (the suite detects the change) |
| E2 Thin emitter | — | `lattice/emit/thin.py`: plans → router mounted into the template app | the hand-written template Lattice, emitted and mounted, passes the template differential exactly as the runtime does |
| E3 Harness | E1, E2, I3 | arms P and L in `bench/edit_slice/run.py`, reusing `lattice/llm` client, prompts and repair; fake-LLM tests; the reference solution fed through each arm scores correct | green tests; dry run with the recorded reference answers |
| E4 Live run + report | E3 | 72 runs; `docs/results/` section; verdict on the core claim | results written, with an honest verdict |
