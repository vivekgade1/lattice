# T1-3: real-agent build from spec on an unfamiliar Python app (Cyclops viewer API): result (exploratory, n = 1)

Date: 2026-10-30. Pre-registration `docs/results/2026-10-30-t13-unfamiliar-python-build-prereg.md` with Amendment 1 (committed after the FastAPI path-converter fix of node I1 and before the driver update). Driver `dogfood/t13_unfamiliar_build_run.py`; data `docs/results/data/2026-10-30-t13/run/` (`result.json`, `ledger.jsonl`, `loop-record.json`, `loop-log.txt`, `raw/`, `freeze.json`, `import/`). One run, `claude` CLI (sonnet) through the capped shim, no API key. **Data status: development data, exploratory, no claim.** The app (`~/Documents/cyclops` at d4df0ad) was never seen by Lattice before this round; the importer was fixed during the round (I1), which the prereg discloses.

## Result: not built; first failure S5
| stage | verdict | detail |
|---|---|---|
| S1 import | pass | fastapi; 65 rule items (35 verified, 0 refuted, 30 unexercised); 0 of 30 contract routes verified; the `{path:path}` catch-all excluded as unsupported; `spec.py` emitted (sha256 b6d4b452...) |
| S2 calibration | pass | reference app passes the Lattice gate (and 178 held-out); the bare scaffold fails it (gate not vacuous) |
| S3 loop | pass | rebuild loop passed in round 1 (1 call, 13 turns, 59.5 s, 1 file changed: `cyclops/ui/server.py`) |
| S4 clean verification | pass | gate re-run on a clean `git archive` of the tip with protected files unchanged |
| S5 held-out tests | **fail** | 107 passed, 69 failed, 2 errors, 4 deselected (107 of 178 = 0.60) |
Spend: USD 0.2801 of the 0.80 cap (one call; per-call budget flag 0.70, max turns 30).

## Against the registered predictions
- Overall 'not built' (p about 0.98): as predicted. First failure at S5 (p about 0.4; S3 was p about 0.55): the S5 branch. S3 passing in one round (p about 0.45): happened. Held-out pass share if S5 is reached, point 0.35 (80 % interval 0.10 to 0.65): observed 0.60, inside the interval, above the point estimate. Spend 0.30 to 0.75 expected: observed 0.28, slightly below.

## What it shows and does not show
- The Lattice gate is satisfied by an agent that has seen only the imported spec, yet 40 % of the app's own behaviours (held-out tests: CORS handling, live-endpoint 404 behaviour, response details) are not captured by that spec. For an app with no database and no auth the imported spec holds the routes and their validation answers, not the behaviours; the gate then certifies what the spec states, not the app. This is a spec-completeness finding about the importer on an unfamiliar app, consistent with the H2 round's finding that import quality drops off the familiar idioms.
- About 7 held-out tests exercise the excluded catch-all or `FRONTEND_DIST` (Amendment 1 keeps them in the denominator rather than tuning after seeing the spec), so part of the 40 % is by construction.
- n = 1, one model, one app, one agent round, the prompt set by the loop's briefing: no rate, no comparison.
