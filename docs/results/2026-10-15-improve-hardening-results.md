# Results: improvement-loop hardening (G10)

Pre-registration: `2026-10-15-improve-hardening-prereg.md`. Branch `np-improvehard`.
Data: `docs/results/data/2026-10-15-improve-hardening/`.

## What was built
`events.jsonl` appends are serialised by an advisory file lock (waits up to 2.0 s, then degrades to a
warning and writes nothing for `extend`); a torn final line is healed before the next append; imports are
idempotent under concurrency; gate-run events gain a `setup` field taken from `timings.setup` of the
check report (absent when empty, non-finite values dropped). The ranker and signals are unchanged.

## Result: 24 of 25 probes pass; one pre-registered probe FAILS
**E-kill FAILS its pre-registered rule (2/10 runs pass; 0/3 in the orchestrator's re-run).** The clause
"every SIGKILLed writer was cut short (m < 500 events)" does not hold: appends cost ~0.1 ms here, so a
writer often finishes all 500 events before the random kill lands (26 of 40 killed writers were really cut
short in the final run; 12 of 40 in the control arm). Every other clause of E-kill held in all runs: no
unparseable line including the tail, no blank line, per-writer sequence exactly 0..m-1, and the next append
after killing a lock holder took 0.0004 s. I did not retune the rule. The claim this supports is
therefore "no torn or lost line in the ~26 real mid-run kills per 10 runs", not the full 40 per 10 runs.
The pre-registered bar "all probes pass" is **not met**; a stronger kill rig (more events per writer)
would be a new pre-registration.

Other results:
- **E-stress does not discriminate on this filesystem:** the a4ab4a1 control arm also passes (0 torn,
  0 lost, 10/10). The lock's evidence rests on E-lock (control 0/10), E-import (duplicates in 8-10 of 10
  control runs), E-heal (control loses the new event) and E-short.
- Byte-compatibility: 0 differing bytes of 243013 over 149 comparisons, both directions; improve2 probes
  15/15 on both trees with equal documents; ledger 1600 entries, 0 lost; per-append cost +0.0 to +0.02 ms.
- Tests: `tests/improve` 111 -> 129 passed; `tests/improve tests/studio tests/test_cli.py` 616 passed,
  1 skipped; `dogfood/improve_mutants.py` control + 21/21 killed; IH1-IH14 14/14 killed;
  `dogfood/studio/check.sh` PASS, 435 cases.

## Decisions not fixed by the pre-registration
`report_setup` lives in `lattice/studio/rebuild.py` beside `report_layer_seconds`; `append_lines` gained
`degrade` and `what`; fsync failure after all lines were written counts as dropped, as at a4ab4a1; IH5 is
an unbounded poll rather than a literal blocking lock.

## Orchestrator verification
Independent re-run: `tests/improve` 129 passed; probe runner `--runs 3` 24/25 (E-kill the same single
failure); ruff check and format clean; `git diff a4ab4a1 --stat` shows only events.py, rebuild.py,
test_events.py, the pre-registration and this node's new files.
