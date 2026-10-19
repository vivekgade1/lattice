# Go parity round 3, Tier 1 (build node B1): results on the development data

Pre-registration `2026-10-18-go-parity-round3-prereg.md` with Amendments 1-2. Branch `np-go3-b1`. Tier 0 is in `...-tier0-results.md`.
All values here are development data (Streak, S1, S2, S3, job-scheduler); nothing was scored blind. The build node's report is the
source of the numbers; the orchestrator's independent re-run follows the freeze.

## What was built
K1 (the checker reads wrapped handlers with one root per route and the wrapper literal's call of its parameter), K2 (+K2c) (method
calls on an outside-rooted receiver chain and conversions to type literals are not opaque), K3 (a body model for Go routes; 40 of 67
S3 items verified), K4 (an honest `not_run:server_dialect` skip when `studio.toml [app] database` names a server engine), K5 (choices
need at least two distinct values), K6 (tables matched by SQL name; undeclared tables listed separately); the runners
`dogfood/go_parity_probes3.py` (every arm) and `dogfood/go_parity_mutants3.py`; stand-in truths; 60 new unit tests.

## Gated rows that FAIL (stated first)
| row | registered | observed |
|---|---|---|
| PS-PI3-3 | 63-70 items, share 0.60-0.68 | 67 items, **0.597** (gate 0.60); `R2 POST /tickets/{tid}/handoffs` unexercised |
| M-SM1 | `GO801 GET /v1/habits/:id Habit` | detected as `GET /habits/:id` (no `/v1`: the strict key fails) |
| M-SM2 | GO801 on the PATCH defect | silent (interface call `d.Clock.Now(ctx)`) |
| M-SM3 | GO801 on the completions-list defect | silent (`body(r)` function-typed parameter of `NewPage`) |
| M-I1 | exit 0, skip, 26 contracts, ..., review lists 0 items | all true except the review lists 176 `[unprobed]` rule items |
| PB-T | same failing test ids | one declared change (the inferences list gains `body_model`), now updated by the orchestrator |
M-SM4 and the job-scheduler membership defects (J-SM1/J-SM2) are reported only. **K1/K2 as registered cannot reach Streak's PATCH or
completions-list routes**: the planted ownership defect is detected on `GET /v1/habits/{id}` only (modulo the `/v1` prefix).

## Passed
Arm 0 5/5 (P0-WRAP, OPQ, BODY, CHOICES2, REG); PS-PI4-3 0.95 (boundary), PS-PR3-3, JM31/ST2/ST3 exact with the original at 0; S3A 0/0;
S2 (all 12 sub-rows; PI3 0.6098, just under its predicted band); S1 7/7; M-A0; M-I2; M-P1; J-0 (identical to 978b468, sha
`ce07cdc4632ca1e7`), J-A 0 findings; PB-F, PB-X, PB-G (one allowed key: a real unscoped delete in the goextract fixture), PB-P, PB-P2,
PB-H, PB-N, PB-D; PW. Self-test OK (every arm's code path ran; no improper NOT RUN).

## Mutants
Library 16/18 killed (LM16 and LM17 need B2's K8; NOT RUN); runner RM1 and RM2 caught; dev ST1-ST3 3/3; SM1-SM3 0/3 on the registered rows;
12/12 re-applied round-2 mutants killed. Total 35 of 41.

## NOT RUN
M-R1 (Docker, by instruction), LM16/LM17, Tier 2, the real Arm H on S4 (stand-ins only), a scored-kind run and the Amendment 3 freeze.
Disclosures: fixture revision 2; the build node's `pkill -f` cleanup could have killed another session's checks (they restarted); one
unshimmed pytest started and removed throwaway containers (none remain).
