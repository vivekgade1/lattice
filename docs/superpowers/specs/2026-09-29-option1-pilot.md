# Option 1 pilot: do frontier models make silent mistakes on harder tickets? (pre-registered)

> Status: PRE-REGISTERED 2026-09-29, before any model run on these tickets. It follows the
> confirmation run (`docs/results/2026-09-26-bench-smoke.md`, "Confirmation run"), where
> Sonnet editing plain Python was nearly perfect on CRUD tickets (89/90 correct, 1 silent),
> and `docs/option1-handoff.md`, which asks for a cheap P-only pilot before any
> pre-registered comparison of arms.

## Question

On tickets whose correct change depends on subtle, cross-cutting invariants, does
claude-sonnet-5 editing **plain Python** (arm P) make enough silent mistakes for Lattice to
have something to fix, and would Lattice's guarantees catch those mistakes?

The pilot measures only arm P. It says nothing yet about arms L or Q. It decides whether a
main experiment comparing them is worth running, and on which family.

## Scope

- **App:** the held-out multi-vendor marketplace
  (`bench/import_repos/multivendor`, CS-Nithin/multi-vendor-inventory-booking-system @
  `54fbf34`, MIT). It was vendored for this pilot with compatibility-only patches listed in
  its `PROVENANCE.md`, and a ticket-neutral seed written before the tickets.
- **Tickets:** t31–t38 (`bench/edit_slice/tickets.py`, `OPTION1_TICKETS`). They were
  written as texts first and approved before any reference or oracle work. There are two
  families:
  - **A, aggregations with traps:** t31 vendor sales, t32 monthly report, t33 top sellers,
    t34 customer spend.
  - **O, ownership inherited through parent tables:** t35 vendor order lines (a
    **control**, see below), t36 cancel order, t37 product buyers, t38 reorder.
- **Arm:** P only (plain Python edits). The prompts, the repair budget of 2 and the smoke
  feedback are as in every round. For this app, arm P is shown the ticket's files plus
  the models, `core/dependencies.py`, `core/permissions.py` and the two repository files.
  That way a trap never depends on code the model cannot see.
- **Model:** claude-sonnet-5.
- **Reps:** 3 per ticket, so 24 runs: 12 per family.
- **Transport:** `--backend cli` (the user's plan). A usage limit pauses the run; it
  resumes with `--resume`. A timeout counts as a loud run and is reported.
- **Command:** `uv run python -m bench.edit_slice.run --backend cli --model claude-sonnet-5
  --arm P --tickets option1 --reps 3 --out bench/results/option1_pilot_sonnet_P.jsonl`
- **Code:** the commit this spec is committed with.

## The oracle

Each ticket's hidden oracle (`bench/edit_slice/repos/multivendor.py`) runs hand-written
cases on the app's own seed and on a few database variants. Each case is compared with the
ticket's reference implementation. A run is:

- **correct** if every case agrees;
- **silent** if any case gives a wrong non-5xx answer or a wrong database effect;
- **loud** otherwise (5xx, fails to start, refused, timeout).

Before this spec was committed, tests (`tests/bench/test_edit_slice_option1.py`)
established the following:

- every reference is correct, with every raise site reached;
- the unmodified app is not correct on any ticket;
- 31 trap-violating variants (3–5 per ticket) all score silent, each on the case named
  for its trap;
- the smoke run and arm P's dry run pass on every reference;
- no trap note or case name reaches the prompt.

## Mechanism tags (fixed now)

Each trap case carries the Lattice guarantee that would make its violation impossible or
refused (`MECHANISMS` in the oracle module, assigned before any model run):

| Tag | Guarantee |
|---|---|
| `L420` | join fan-out |
| `L421` | null policy: an aggregate over no rows |
| `L802` | ownership scoping |
| `one-statement/atomic` | one statement per read, one transaction per write |
| `typed-params` | typed, bounded parameters |
| `none` | everything else: route order, date boundaries, business rules |

**t35 is a control ticket.** An order line has two owners: the buyer, through the order,
and the vendor, through the product. Lattice's single-owner model does not express that.
t35 counts toward family O's silent rate but is left out of the mechanism gate.

## Gates and decision (fixed now)

Unit: a run. For each family separately:

1. **Silent-rate gate:** at least 2 of the family's 12 runs are silent (≥ 15%).
2. **Mechanism gate:** a silent run is *covered* if **every** case it fails silently has a
   tag other than `none`. Only then would Lattice's guarantees have turned the run
   non-silent. The gate passes if at least half of the family's silent runs outside the
   control ticket are covered. A family with no silent runs outside the control fails
   this gate.

**Decision:**

| Outcome | Next step |
|---|---|
| A family passes both gates | Pre-register a main experiment on that family: arms P, L and Q; claude-sonnet-5 primary, claude-haiku-4-5 secondary; ≥ 6 reps; one-sided Fisher on silent runs plus a per-ticket sign test; each ticket's family set exactly as here. Before it runs, arm L needs the importer's output for this app, with start-state fidelity checked against every ticket's untouched-route cases. |
| Both families pass | As above, with both families, analysed separately. |
| Silent-rate gate passes, mechanism gate fails | Frontier models do slip here, but not where Lattice's guarantees reach. Report it as evidence against Lattice as an editing interface for this class. Do not run the main experiment. |
| Neither family reaches 15% silent | Nothing to fix on this class either. Report a **no-go** on option 1's premise for these families. |

The 15% threshold on 12 runs is coarse by design. The pilot only screens for a
signal; it is not a test. The pilot's data is **not** pooled into any main experiment.

## Reported

- **Per ticket and per family:** correct, silent and loud runs, with Wilson 95% intervals.
- **For each silent run:** the cases it failed and their mechanism tags.
- **Efficiency:** tokens in and out, cost per correct run, repair rounds, time per run and
  lines changed.
