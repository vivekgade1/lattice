# .NET parity (Spike J): scored results, failures stated

Date: 2026-10-21. Pre-registration: `docs/results/2026-10-18-dotnet-prereg.md` (Amendments 1-6). Branch `np-dotnet`. Data: `docs/results/data/2026-10-21-dotnet-scored/`
(`probes/`, `mutants/`, `posthoc-hm-derived-policy.json`), ledger `docs/results/data/2026-10-18-dotnet/ledger.jsonl`, the archived refused launch
`ledger-refused-launch-1.jsonl`. Freezes: `freeze-h1-final.sha256` (sha256 `eb41e57d0d2986a22b0ef7d0b9670daec17c78e7fd2b7701775da3f082faed12`, probes) and
`freeze-h1-hm.sha256` (sha256 `913fc9d833cfc1b7743a49bbb920a618befd802a916d71092d104bc41aed4b30`, mutants). No model was called; the held-out service and its mutants were written by isolated agents (transcripts audited, 0 repository reads).

## 1. The short version
- **Exact, deterministic extraction generalises to an unfamiliar .NET app.** On the held-out service H1 (`outlay`, 16 routes, 4 tables, written blind after the freeze): routes 16 of 16 and 0 extra, auth 16 of 16, tables 4 of 4, column facts 190 of 192, wire names 28 of 28, owners 2 of 2 with 0 confident-wrong, bounds precision 1.00 and recall 1.00 (34 of 34), 0 silent drops, 10 of 10 identical extract and layer A, import wall 46 s, seven workflows verified with 0 failing on replay.
- **What does not generalise:** the rules import (verified share **0.039**, 3 of 77, gate 0.50), refusal recall (**0.40**, gate 0.60; precision 1.00), the import's 10-run determinism (1 of 10 identical), the private-column policy derived from code (over-derives: it names `id`, `title` and eight other ordinary report columns as private), and the held-out mutants: **0 of 16 killed, gate 13**.
- **Overall (93 gated rows): 76 passed, 16 failed, 1 not run (P-J0).** Arm 0 15 of 15, Arm D 33 of 42, Arm H 14 of 19, Arm M 2 of 2, Arm N 4 of 4, Arm X 8 of 11 (two declared, one not run). Library mutants 24 of 24, runner mutants 2 of 2, dev mutants 4 of 6 (DM4 survived, DM6 not run).
- **This does not reach equal support with Python and Go** (section 5): 9 supported, 9 partial, 2 absent of 20 cells, against Python 15/3/2 and Go 15/4/1.

## 2. Failing rows, with the cause as far as it is known
| row | observed | gate | what is known |
|---|---|---|---|
| H-PI3 | 77 items, 3 verified (0.039), 73 unexercised, 0 refuted | share >= 0.50 | the import cannot build request bodies for H1's validated DTOs and seed rows for its tables; same family as D1's 0.255 (guid keys typed as strings, `DateOnly`, bodies read through custom accessors) |
| H-RAISE | precision 1.00, recall 0.405 (17 of 42 truth pairs) | recall >= 0.60 | refusals raised in service methods and typed exceptions behind an exception handler are not all followed; all misses are `404`/`409` pairs of the owner-scoped routes |
| H-PI7 / I-D1-7 on H1 | 1 of 10 identical | 10/10 | values the app mints (tokens, hashes) reach the evidence; the runner's masking (Amendment 3) handled D1 but not H1's token form |
| H-PA1 | derived `private` names ordinary columns of `expense_reports` | 0 wrong | the projection rule (S round 2) over-derives; it would produce false DN802 findings if used as a policy |
| H-IMPL | the imported spec has 0 requirements, so every route is implemented-not-required | 0 extra rows | follows from H-PI3 (an empty candidate spec) |
| H-PI5 (reported) | machine `expense_reports.status` found, its edges not proposed | reported | as on D1 |
| D1 rows A-DM, A-DERIVE, I-D1-2/3/T2, I-D1-PR3 | DM4 not killed; D1 `private` not derived; verified share 0.255 | as stated in Amendment 3 | dev data, stated before H1 existed |
| C-IMPL, C-IMPLM | prediction errors (Amendment 3, item 7) | -- | not library defects |
| C-IMP | c01 resolves 38 locations; c02-c05 exit 2 | 5 of 5 | **unexplained**: it passed 5 of 5 in the shakedown on the same inputs; diagnosed in section 6 if the verifier's re-run reproduces it |
| PB-F | `lattice/importer/review.py` differs from the base | 0 changed | declared (Amendment 3, item 4): `candidate_item_ids` |
| PB-T | one Go test pins `check.py`'s hash | 0 failures | declared (Q10) |

## 3. Held-out mutants (HM, written by W from H1 and its truth only)
**0 of 16 killed against the registered gate of 13** (classes: HM1-4 0/4 (need 3), HM5-6 0/2, HM7-8 0/2, HM9-10 0/2). All 16 patches applied and built. The cause is in the registered path: layer A ran under
the policy of H1's **imported candidate spec**, and that policy holds only `PayoutMethod -> user_id` (checked post hoc by loading the candidate spec); with no owner for `ExpenseReport` and no private columns, DN801 and DN802 cannot fire on 14 of the 16 mutants, and layer R's `R1` items (HM7-8) were not verified on the control (H-PI3).
**Post hoc, not a gate** (`dogfood/dotnet_hm_posthoc.py`, `posthoc-hm-derived-policy.json`): under the policy derived from the *code* (`derive_dn_policy`), layer A flags HM1, HM4 and HM11 with DN801 and HM15 with DN803 (not its registered detector); HM2, HM3, HM5-HM10, HM12-HM14 and HM16 stay silent, so even the richer policy would not meet the class gates (HM1-4: 2 of 4).
Library mutants LM1-24 **24 of 24 killed**; runner mutants RM1-2 caught; dev mutants DM1, DM2, DM3, DM5 killed, DM4 survived (every Member route already answers 401, as registered), DM6 not run (no HTTP route).

## 4. Process irregularities (all disclosed in the amendments)
1. The first scored launch was refused by the runner's self-hash check (the freeze file predated the amendments and omitted the H1 archives): 12 ledger entries, no metric. The orchestrator archived them and started a fresh ledger (Amendment 5, with a correction of an earlier wrong statement that no ledger existed). A departure from the once-only rule.
2. The patch names W delivered (`HMn.patch`) differ from the names the mutants runner reads (`mNN.diff`); byte-identical copies were frozen (Amendment 6).
3. S stalled twice in round 2; its uncommitted edits were checked by lint, tests and diff review before commit. Its all-arm shakedown was killed and re-run on the final tree.
4. The runners' self-tests initially failed on stand-in drift (a stand-in `SPEC` object); fixed in glue (`--streak` pass-through) and re-run to `SELF-TEST OK`.
5. Dev data (D1, DM1-6) were used to fix generic gaps before the freeze (`Required.cs`, deterministic duplicate ids, `json_login`); a drop on H1 was predicted in Amendment 3 and is what happened for the rules import.

## 5. J0-dn: the parity matrix, .NET column (re-graded from this run; Python and Go as in `2026-10-18-parity-matrix-j0prime.md`)
`S` supported, `P` partial, `A` absent. Evidence is the scored row or dev row named; the verifier compares the runs, it does not re-grade these cells.
| # | capability | .NET | evidence |
|---|---|---|---|
| 1 | Importer front end | **S** | H-ROUTES/AUTH/TABLES/WIRE/UNREAD on H1; F-DETECT/MODEL/RT |
| 2 | Behaviour lifting | **A** | not built (as Go) |
| 3 | Candidate-spec emission, rules-first import | **P** | runs end to end; H-PI3 0.039, I-D1-3 0.255, H-PI7 1/10 |
| 4 | Layer A static checks | **P** | DN800-803, 806-808 on dev; DN804/805 gated; HM 0/16 under the spec policy |
| 5 | Runtime harness | **S** | process runtime for D1, H1, dnapp |
| 6 | Layer B | **S** | N-B1 (32 cases, 0 findings, both planted bugs found), dev app |
| 7 | Layer C | **S** | M-R1 (`--layers ACDX --database mysql`, D1) |
| 8 | Layer D | **S** | M-R1: scenarios run; the one frozen failing scenario is the harness's |
| 9 | Layer X | **S** | M-R1/N-B1 race variants (dev apps) |
| 10 | Layer R | **P** | runs; verified shares 0.255 / 0.039; DM4 unkilled |
| 11 | Workflow layer | **S** | W-D1-1/2/3/4, H-WF (7 verified, 0 failing replay) |
| 12 | Implementation report | **P** | C-INV/CITE/MCP pass; C-IMPL/C-IMPLM prediction errors; H-IMPL empty spec |
| 13 | Chat grounded in spec and code | **P** | C-ASK measured with a scripted model only |
| 14 | App map | **S** | C-MAP |
| 15 | Change impact | **P** | C-IMP 1 of 5 in the scored run (unexplained) |
| 16 | Perf engines | **P** | C-PERF on D1 over MySQL (as Go) |
| 17 | Contracts | **S** | C-LIVE |
| 18 | Improve loop | **P** | C-IMPROVE ran `improve report` twice on an empty log only |
| 19 | Build-from-spec | **P** | C-REBUILD with a stub agent and a `dotnet build` gate; no real-agent build |
| 20 | Code generation | **A** | ruled out by the owner |
Counts: **S 9, P 9, A 2** (Python 15 / 3 / 2, Go 15 / 4 / 1). The honest gap to "equal support": rows 3, 4, 10 (the imported spec on an unfamiliar app), 12, 13, 15, 16, 18, 19 are partial; rows 2 and 20 are absent by decision.

## 6. NOT RUN
P-J0 (prerequisite: this matrix, written after the scores); a real-agent toy build (claude CLI, USD 5 cap); real-agent model answers for C-ASK; the independent verifier's result is appended in section 7.
