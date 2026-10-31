# T1-4: real-model C-ASK on Go/Streak, corrected citation definition: result (exploratory)

Date: 2026-10-30. Pre-registration: `docs/results/2026-10-30-t14-casko-corrected-citation-prereg.md` with Amendment 1 (six questions, cap unchanged at USD 0.60; committed before the driver changes). Driver `dogfood/t14_casko_run.py`, data `docs/results/data/2026-10-30-t14/paid/` (`ask-result.json`, `ask-freeze.json`, `ledger.jsonl`, `ask-raw/`). One run, `claude` CLI only (sonnet), no API key, through the capped shim. **Data status: development data, exploratory** (post hoc citation definition; Streak is the owner's spec at a0679e9; questions were written by an agent with the digest and code open).

## Result
| gate | prediction (prereg) | observed | verdict |
|---|---|---|---|
| A-ANSWERED (6 of 6) | p 0.6 | 6 of 6 answered, 0 errors, 6 model calls | pass |
| A-CITED (0 uncited route/rule claims) | p 0.7 | 11 known claims, 0 uncited, 0 rejected citations, 0 non-resolving | pass |
| A-COST (<= USD 0.60) | -- | USD 0.3719 | pass |
Reported, not gated: A-CORRECT 6 of 6 checked and 6 of 6 raw (prediction 5 of 6; the Q08 false-premise trap was answered correctly); damage 0; 1 unknown route mention (the trap's non-existent route, named by the model while correcting the premise); no answer downgraded by the library.

## What this does and does not show
- The corrected definition (an `impl:route:`/`impl:rule:` inventory element that resolves counts as a citation) removes the artefact that failed the .NET C-ASK: here 0 of 11 claims were uncited and 0 citations were rejected.
- **It does not show the claim checker's value.** Raw model text had the same 11 known claims, 0 uncited and 6 of 6 correct as the checked text (chat round 2 had 53 unsupported claims raw vs 0 checked). Six easy questions with the digest in context did not provoke any unsupported claim, so raw and checked are indistinguishable. n = 6, one run, one model, questions written with the code open: no claim beyond "the corrected audit works on a clean run".
- Spend: USD 0.3719 of the USD 0.60 cap (6 calls); the amendment's cost model predicted about 0.52.
