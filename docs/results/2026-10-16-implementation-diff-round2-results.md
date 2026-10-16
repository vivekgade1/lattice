# Implementation diff, round 2 (workflow_find ranking): results

Pre-registration: `2026-10-16-implementation-diff-round2-prereg.md` (with Amendment 1). Data:
`docs/results/data/2026-10-16-implementation-diff-round2/`. Branch `np-c2` from 16eb743. Written by the
orchestrator from the builder's report and an independent re-run (below). The builder did not append Addendum 1 or
write this document.

## Gated rows
| probe | rule | observed | |
|---|---|---|---|
| PT2A | top-ranked route is the key route on >= 75% of single-target rows of G (12/16) | 13/16 (81%) | pass |
| PTDEV | round 1 PT >= 6/8 | 8/8 (round 1's ranker: 2/8); 13 agent queries 13/13 | pass |
| PD2 | 10 processes + second root identical | 1 distinct output over 99 queries | pass |
| PX2 | fast call | median 17.7 ms | pass |
| PB2 | byte-compat with bb3ff0c outside the D7 region | all checks true | pass |
| PR1 | round 1 runner unchanged at the new code | PT 8/8, all other rows as round 1 | pass |
| PQ2 | agent exercise (claude CLI): pass rule >= 11/13 | evaluated and met; 13 scored, USD 1.88, no usage limit | pass |
| Mutants | 14/14 killed, control first | 14/14 | pass |

## Reported, not gated (honest misses)
- **PT2B exact label + route on G: 13/24 (54%)**, below 75%. This was predicted to miss (Amendment 1 ruling R1) and
  did. Round 1's ranker scored 2/24 on the same set.
- Burned set F (development data): 20/27 exact. Held-out PT3 (orchestrator queries, scored once, never tuned on):
  **6/14 exact**, top route right 7/13. This is the number to quote for unseen queries.
- Limits are lexical: synonyms with no shared token and wordy queries.

## Regression
`tests/mcp tests/studio tests/spec tests/importer tests/gocheck`: 2469 passed; the 2 failed and 29 errors are the
same ids as the baseline (test_outside template flake, multidb container start, driver-less db tests).
`check.sh` pass (481 cases). +41 tests (`tests/spec/test_workflow_find2.py`).

## Orchestrator independent verification
Freeze list (`freeze.sha256`): every hash verified OK. Probes re-run from the committed tree in my own scratch:
PT2A 13/16, PT2B 13/24, PTDEV 8/8, PTF 20/27, PT3 6/14, PD2 identical, PX2, PB2, PR1 all equal the builder's. Mutants
re-run: control passes, **14/14 killed**. (My first mutant attempt failed its control only because I passed a relative
workflows path and a stripped interpreter; reran correctly. Not a result.)
