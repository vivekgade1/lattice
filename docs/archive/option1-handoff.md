# Handoff: option 1 — find a task class where the guarantees matter

> For a fresh session. Read `docs/lattice-reference.md` first (full context), then this.

## Why option 1

The pre-registered confirmation (`docs/results/2026-09-26-bench-smoke.md`, "Confirmation run") found that on realistic **CRUD** change tickets:
- a frontier model editing plain Python is nearly perfect (Sonnet 89/90 correct, 1 silent);
- neither the Lattice language nor Lattice's checks applied to Python reduced silent bugs.

Before concluding that Lattice has no value as an LLM editing interface, test **one bounded experiment on a harder task class**, where frontier models plausibly make silent mistakes and where static guarantees could catch them.

## Goal

Design and run **one pre-registered experiment** comparing:
- **P:** plain Python edits;
- **L:** Lattice edits;
- **Q:** Python plus Lattice's checks.

It runs on a new ticket family in which the correct change depends on **subtle, cross-cutting invariants**. Deliver a clear go/no-go on Lattice as an editing interface.

## Candidate task families

Pick 1–2 of these, and justify the choice with a cheap pilot first.

1. **Complex authorization:**
   - multi-role rules (owner / member / admin / org-scoped);
   - delegated access;
   - shared resources with per-row visibility;
   - "public but not editable".
   
   The JustNotes public-note confusion hints that this is where models (and our importer) slip.
2. **Multi-table invariants:**
   - cascades;
   - counts and quotas kept consistent across tables;
   - "exactly one primary X per user";
   - soft-delete interacting with uniqueness.
3. **Transactional / effect ordering:** emails and webhooks sent only after commit, idempotency, retries. `for_each` and the outbox exist for this.
4. **Aggregations with traps:**
   - join fan-out;
   - null handling;
   - zero-row groups;
   - period boundaries.
   
   Lattice's earliest win came from these traps (tabular step 2, Haiku).
5. **Large-file / long-context edits:** the same ticket inside a much bigger app, where the model must find every affected route.

## Method (reuse everything)

- **Harness:** `bench/edit_slice/` (tickets, references, oracle with trap cases, arms, CLI backend with resume/workers, `rescore.py`, dry runs). Add tickets and oracles the same way as t7–t30 were added (`tests/bench/test_edit_slice_*` show the pattern: each reference correct; the unmodified app not correct; at least 3 trap variants silent).
- **Apps:** reuse the template, or add one richer held-out app with real multi-role auth. If a new app is added, vendor it like `bench/import_repos/*` with a PROVENANCE file, and never write Lattice for it by hand: arm L starts from the importer's output.
- **Sequence:**
  1. **Pilot first:** P only, Sonnet, 3 reps, about 8 tickets. Proceed only if P's silent rate is at least about 15%; otherwise there is nothing to fix.
  2. **Pre-register:** write the spec with the hypothesis, arms, scope, reps (≥ 6), statistical test (one-sided Fisher on silent runs + per-ticket sign test) and decision rule **before** the main run. Commit it.
- **Models:** claude-sonnet-5 (primary); claude-haiku-4-5 (secondary).
- **Transport:** `--backend cli`, which uses the user's plan. Usage limits pause the run; resume with `--resume` once the user says the limit has reset.

## Known pitfalls (learned the hard way)

- **Use enough reps.** With 3 reps, differences of a few runs were noise.
- **Check start-state fidelity first.** Before the main run, score each arm's *unedited* start state against every ticket's oracle: untouched-route cases must pass. The JustNotes import failed this silently in round 5.
- **Keep the arms fair.**
  - Aid tables must be ticket-neutral and built without oracle verdicts.
  - Arms Q and L must see the same ownership facts.
  - Retrieval must exclude a ticket's base chain.
  - `bench/edit_slice/aids/` and `tests/bench/test_edit_slice_aids_neutral.py` enforce this.
- **Don't let feedback push models into wrong edits.** `pycheck` findings made Sonnet worse by over-applying ownership rules. Check precision on the new tickets' references (0 findings) and on trap variants before the run.
- **Timeouts are a Lattice-specific failure mode.** Sonnet-L timed out at 600 s on bulk-delete. Record timeouts as loud and report them.

## Process preferences (the user's)

- Agentic graphs with parallel agents; TDD; the orchestrator commits after each gate.
- Agents never run tree-modifying git commands.
- Report cost per correct, tokens, rounds, silent/loud/correct, and confidence intervals.
- Nudge the user (a push notification) when a long run finishes or needs a decision.
