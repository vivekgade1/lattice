# Results: `lattice contracts impact --transitive` (G7)

Pre-registration: `2026-10-15-transitive-impact-prereg.md`. Branch `np-transitive`.
Data: `docs/results/data/2026-10-15-transitive-impact/`.

## What was built
`lattice contracts impact PROVIDER --candidate REF --transitive` adds a `transitive` block: a labelled,
**unverified** reachability list of projects downstream of the first-order roots (declared links only,
seeded from contradictions only, no depth cap, cycle-safe, provider never listed). Without the flag every
output is byte-identical to a4ab4a1; the key is emitted only when the flag is on.

## Result: 10 of 11 probes pass; the one that fails is an incomplete regression chain, not a product defect
- **T-flagoff is recorded FAIL.** Its byte-identity part holds: 30/30 cases byte-identical to a4ab4a1,
  each in 10 runs. The FAIL comes from its regression-suite part: in the recorded run
  `streak_probes.py --allow-code-diff` printed 32/32 pass but exited 1 because a *concurrent* Studio process
  of another job was counted as a leftover; alone, afterwards, it passed 32/32 rc 0 (`streak-rerun/`). The
  recorded JSON is left as first measured.
- **Not run in full:** the pre-registered full `impact_probes.py --runs 10` (12/12, ~1 h) and full
  `linkedq_probes.py` (10/10, ~45 min). Only the `--skip-regression` forms ran: impact 11/12 and linkedq
  9/10, each failing only its own `regression_run` check by construction. So "no regression in layer 4a/4b
  probes" rests on the skip forms plus `tests/contracts` (327 passed) rather than the full chains.
- T-basis, T-chain, T-diamond, T-cycle, T-long, T-edge, T-additive (28/28 pairs), T-parity, T-cost
  (delta +0.003 s), T-safety (1430 traced calls: no write, no connect, real repos unchanged) pass, 10/10
  identical. Mutants TR1-TR14 14/14 killed, control passes.

## Orchestrator verification
Independent re-run at 2 runs, no regression chain: 10/11 (T-flagoff "regression suites: not run", as
expected without `--regression`); `tests/contracts` 327 passed; ruff check and format clean;
`git diff a4ab4a1` touches only `contracts/{__main__,impact,transitive}.py` and new test/probe/data files.

## Decisions the pre-registration did not fix
`roots[].dependents` is a sorted name list; the link's `contract` is the first by sorted contract string;
`root_error`, `provider_downstream`, `no_pin`, `no_imports`, `bad_imports` notes as in the code; a workspace
with manifest errors yields an empty transitive block.

## Open
A quiet-machine re-run of the whole runner with `--regression`, and the two full chains, remain if the
owner wants the T-flagoff row to read PASS.
