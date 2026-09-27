# Loop corpus (sub-project 1b)

Real-style Python loops, each paired with its Lattice equivalent. This folder is the
seed test set for the importer (sub-project 2): given the original loop, the importer
should produce the Lattice program, or keep the loop as a native island.

Each `lNN_*.py` item describes itself:

- the module docstring gives the loop shape, the construct and the targets;
- `SHAPE`, `CONSTRUCT`: short labels;
- `HINT`: what the checker's L127 hint names when the original loop is checked as
  Lattice;
- `original`: the Python function with the explicit `for`/`while` loop;
- `LATTICE`: the equivalent program;
- `generate(rng)`: random inputs.

Tabular items also have `SCHEMAS`, `INPUTS` and `KEY`. The API items (07 and 10) have
their own FastAPI + SQLModel app, `PROFILE`, `build_db` and `requests`.

The equivalence tests are in `tests/loops/test_loop_corpus.py`:

- tabular items run 25 seeds each: the original, the polars executor and, for programs
  without `iterate`, the compiled SQL statement;
- API items run 8 generated databases each, comparing responses, database rows and
  side effects between the original app and the Lattice runtime.

| # | Item | Loop shape | Construct | Targets |
|---|---|---|---|---|
| 01 | running balance per account | running value per key (`+=`) | `window` + `win.cum_sum` | polars, SQL |
| 02 | previous-value delta (pandas `itertuples`) | previous row of the same key | `window` + `win.lag`, `derive` | polars, SQL |
| 03 | salary rank within department | rank within group, ties share | `window` + `win.rank` | polars, SQL |
| 04 | 3-row moving average (pandas + `deque`) | moving frame of N rows | `window` + `win.mean(rows=3)` | polars, SQL |
| 05 | category descendants with depth | BFS down, seen-set, depth cap | `traverse` | polars, SQL |
| 06 | breadcrumb (ancestors path) | `node = by_id.get(node.parent_id)` | `traverse(direction="up")` | polars, SQL |
| 07 | org chart reports (FastAPI + SQLModel) | one query per level, cycle-safe | `traverse` in an endpoint | API |
| 08 | prune orphaned replies | repeat a pass until nothing changes | `iterate(until="fixpoint")` | polars |
| 09 | converge forecasts | repeat until a per-row predicate holds | `iterate(until=<Bool>)` | polars |
| 10 | notify watchers after insert (FastAPI + SQLModel) | side effect per row | `for_each` + outbox | API |
| 11 | order sequence number, same-day ties | per-key counter, ties broken by id | `window` + `win.row_number`, key in `order` | polars, SQL |
| 12 | login lockout replay | multi-accumulator state machine | **native island** | — |

## Bounds the Lattice versions state

The original loops have no bound, or an implicit one. The Lattice versions state it,
and a run that reaches it fails loudly instead of returning a partial answer:

- **05, 06, 07:** `max_depth` is explicit. The originals take the same limit as a
  parameter.
- **08:** the original stops after at most `len(comments) + 1` passes. `iterate` caps
  at `max=100` and raises R610 past it.
- **09:** the original halves each gap per pass, so it needs about `log2(gap / 0.5)`
  passes. `iterate` caps at `max=64`.
- **10:** the original notifies every watcher. `for_each` needs a bounded input
  (L439), so the Lattice version notifies the first 100 by id. The generated projects
  have fewer watchers. Delivery goes through the outbox after commit: at-least-once,
  and a failed notification never fails the request (the original swallows the error
  too).

## Native island: 12, the login lockout state machine

`replay_logins` walks each user's login events in order and carries four pieces of
state:

- consecutive failures;
- a lock deadline;
- a session count;
- the status of the current event.

Every step reads the state and the event kind, and decides both the next state and the
output:

- a failure increments the counter, and locks the account at 3;
- an attempt during a lock is refused and does not count;
- a lock expires on the first event at or after its deadline;
- a success clears the failures and counts a session;
- an admin reset clears everything.

**Why it cannot be expressed:**

- **`window`:** its functions are fixed scans. `cum_sum`, `cum_count`, `cum_min` and
  `cum_max` are associative, `lag`/`lead` read fixed offsets, and the rankings and
  moving frames depend only on the order. Here the next state depends on the previous
  state through branches (the lock deadline decides whether a failure counts, and the
  failure count decides the deadline). That is a non-associative fold over a tuple of
  accumulators, and no combination of scans rebuilds it: the failure counter resets at
  points that depend on the counter itself.
- **`iterate`:** rounds work on whole tables with a fixed step. It could emulate the
  fold by advancing one event per round, but that needs as many rounds as events
  (capped at 10,000), costs O(n²), and hides the logic in a bookkeeping step. That is
  worse than the loop on every axis Lattice cares about.
- **`traverse`:** does not apply (there are no parent/child links), and neither does
  `for_each` (there are no effects).

So this loop stays a **native function**: its signature is typed and checked (events
in, rows with `status` and `sessions` out), and its body is trusted Python. The checker
points there: the loop gets L127 with the hint *"other loop state (several
accumulators, a state machine, early exit): keep that logic in a native function"*.
`tests/loops/test_loop_corpus.py` asserts this, and the importer should report it as a
native island in its coverage report.

## Language gaps found while writing the corpus

- **`@step` parameters lose declared keys.** Inside a `@step`, a parameter typed
  `Table[Comments]` does not carry the schema's key `(id,)`. So
  `select(t, cols=["id"])` → `rename(..., id="parent_id")` → `join(t, ..., on=["parent_id"])`
  is L420 inside the step, although the same lines check clean at the top level.
  Item 08 works around it with `group(t, by=["id"], n=agg.count())`, which proves
  uniqueness again.
- **`for_each` arguments come from the input table only.** Item 10 cannot pass the new
  comment's id to each watcher's notification without a join, and a `join` on the
  bounded watcher list is not recognized as bounded by L439. The notification carries
  `project_id` and `body` (endpoint parameters) instead.
- **Unbounded fan-out needs a cap.** "Notify every watcher" becomes "the first 100"
  (L439). The bound is deliberate (spec §3.4), but the importer must report it as a
  change in behaviour when the original has no bound.
