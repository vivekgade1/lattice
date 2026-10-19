# gRPC round 5: scored results (held-out HO5 and PM-SG, blind, once each)

Pre-registration `2026-10-18-grpc-round5-prereg.md` with Amendments 1-3 and 3-SG. Stage A-C results in their own documents. Both targets were written by separate isolated
authors after the freeze (audited transcripts: 0 reads of the repository); contract5 printed every line ok for both; each stage was scored once through the runner's preflight and ledger.

## HO5, the held-out `harborlight` marina service (5 services + health, 26 unary rpcs + 1 server-streaming, 2 unimplemented, 1 never-registered service, 11 flows): 3 pass, 6 fail
| row | observed | |
|---|---|---|
| HO5-RPC | recall 0.733, precision 0.733 (22 of 30 matched) | FAIL |
| HO5-SAFE | 1 confident-wrong of 30 checked | FAIL |
| HO5-CODES | recall 0.675, precision 0.80 (56 of 83 truth codes) | FAIL |
| HO5-AUTH | all 28 classed unclear (share 1.0, above the cap); 0 wrong | FAIL (safe, not useful) |
| HO5-TABLES | recall 1.0, precision 0.889 | PASS |
| HO5-REC | coverage 1.0 and agreement 1.0 (26/26 implemented, 2/2 unimplemented, 1/1 not_registered, 1/1 absent); another sub-check fails | FAIL |
| HO5-FLOWS | 11 of 11 truth flows replay; only 6 recovered, all 6 failed | FAIL |
| HO5-DET / HO5-NW | 10 of 10 identical, 0 leaks / 0 changed, 0 claude calls | PASS |
Round 3's held-out was 2 of 8 rpcs; this is 22 of 30, but far from the round-4 gates (unchanged). Auth is the largest gap (every rpc unclear).

## PM-SG, Streak reproduced as grpc-go over MySQL 8.4 (`streak.v1`, 26 rpcs): 4 pass, 3 fail, 2 not supported
| row | observed | |
|---|---|---|
| SG-RPC | recall 1.0, precision 1.0 (26 of 26) | PASS |
| SG-SAFE | 0 confident-wrong of 26 | PASS |
| SG-CODES | recall 0.0 of 132; all 25 extracted codes are `UNKNOWN` | FAIL |
| SG-AUTH | all 26 unclear (share 1.0) | FAIL |
| SG-TABLES | 0 of 9 extracted (the schema lives in an embedded `.sql` file read from the MySQL database, and `go:embed` DDL is out of scope: Q12) | FAIL |
| SG-REC, SG-FLOWS | NOT SUPPORTED on MySQL (registered: they build SQLite paths) | not scored |
| SG-CD (reported) | 136 findings from the real checker on the author's own spec, though the author's stub runner passed all 162 scenarios | reported |
| SG-SQLT (reported) | recall 0.784, precision 1.0 | reported |
| SG-DET / SG-NW | 10 of 10 identical / 0 changed | PASS |

## Round verdict
The round cannot pass in full: P5-BUDGET (Go group 924 lines vs 650) and PR4-ALL failed before the held-out stage (Stage B and C results); the held-out HO5 and PM-SG
rows above add the blind failures. What improved: toolshed's round-4 confident-wrong auth answer is fixed (dev), rpc recall on the new held-out reaches 0.73 and on Streak-as-gRPC 1.0 with no
confident-wrong answer. What did not: codes and auth on unfamiliar wiring, tables from an embedded schema, and recovered flows (their replay failed). The SG-CD result (136 findings)
needs a post hoc look: it may be a defect in the author's gRPC spec, the harness or the checker's gRPC step support.
Disclosures: the author of HO5 left `IDIOMS.md` in its directory root, outside the freeze (not read by the scorer); the SG author's one `docker ps` listing.
