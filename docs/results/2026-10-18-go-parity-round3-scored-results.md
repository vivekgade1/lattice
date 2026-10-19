# Go parity, round 3: scored results (RUN node)

Frozen at `2c918f2` (Amendment 4). No tracked file was edited; nothing was committed. Both freezes verified before any metric
(`freeze3.sha256` 31 entries, `freeze4.sha256` 65 entries, 0 mismatches; the runners verify them again themselves). Lattice under test:
`/Users/vivekgade/Documents/lattice-g3/lattice/__init__.py`. Bases: `978b468` (git archive), Streak `a0679e9`, job-scheduler-go `00dd1de`
(each read only through `git archive`). No API key, no model calls. Go 1.26.2.

Each scored kind ran **once** (no re-run, no crash). Probes runner: 3452 s wall (about 57.5 min), including PB-T 1111 s. Held-out mutant
runner: 10.6 s wall (control failed, see section 3). M-R1 ran on Docker (one `mysql:8.4` container, `lattice.owner=g3-run`); after the runs
`docker ps -a` shows no container with that label (and none left by this run).

Ledger entries (`docs/results/data/2026-10-18-go-parity-round3/ledger.jsonl`, sha256 `0dc50ef3b3b2311175c91542cd9c5a6665c74ad8c2a0440b449f379a1b2d2bd8`):
`G3:0, G3:S, G3:M, G3:J, G3:2, G3:P, G3:H, G3:X`, kind `scored`, each `started` 2026-10-19T03:42:30Z and `completed` 2026-10-19T04:32:34Z, run_id
`28c1361c13dc`, runner sha256 `d58838de18f67c37a120892c38b20a1359cf846c13158b6a375607403a6ca982`, freeze3 list sha256 `79a5642d6dfee8b6de33e816bc492cf2774f663be70f30377ed90a19a233bf8f`.
`ledger-hmut.jsonl` (sha256 `3c24af1febf56418057ae97cf4b3629d3ebe4d97fe0de0e639663b646b582b97`): arm `G3:HMUT`, kind `scored`, run_id `cfccc5aa1f29`, `started`
04:39:58Z, `completed` 04:40:09Z, `control_passed: false`, `killed: 0`. The earlier Tier 0 entries (`T0` shakedown x1, scored x1, 2026-10-18) are in the same ledger.
The runner appended to the tracked `ledger.jsonl` (it shows as modified in `git status`; that is the runner's own once-only record, not an edit by this node).

## 1. Verdict by tier

| tier | rows gated | passed | failed | NOT RUN |
|---|---|---|---|---|
| Tier 0 (S3 re-score on the 978b468 library, run earlier, `t0/probes-t0.json`) | 5 | 5 | 0 | 0 |
| Probes run (Arms 0, S, M, J, 2, H, P, X) | 49 | 42 | **7** | 0 |
| Held-out mutants (HM1-HM16) | 16 mutants + 4 class gates | 0 judged | control failed: **no mutant judged, gate not met** | 0 (all 16 listed as not run because the control failed) |

Gated failures (all stated as misses, none re-run): **PS-PI3-3, M-SM1, M-SM2, M-SM3, M-I1, P2-805A** (all six are dev data, post hoc; Amendments 2 and 3 had
already seen them fail on dev data and ruled them stay failed) and **H-PI3** (blind, new). Plus the held-out mutant control failure.

By the prereg pass rule (section 10.7) G3 does **not** pass Tier 1 (6 gated failures), nor Tier 4 (H-PI3 failed and the held-out mutant gate is not met), nor Tier 2 (P2-805A failed).
Tier 0 passes. The library-mutant runner (`go_parity_mutants3.py`, 18/18) and the dev re-applied mutants were not part of this task and were not run here.

## 2. Gate tables (probes run, `scored/probes3.json`)

"Blind" = only Arm H rows (and the held-out mutants). Every other row has seen dev data (S1-S3, goparity3, Streak, job-scheduler) and is **post hoc** in the sense of prereg 10.6.

### Tier 0 (post hoc: S3 is dev; registered blind in round 2; run before any G3 library edit)

| id | predicted | observed | pass |
|---|---|---|---|
| T0-FREEZE | all OK | all OK | yes |
| T0-PI4 | 35 seed-verified; 34-35 retained; >= 0.95 | 35 of 35 (1.0) | yes |
| T0-PR3 | identical verdict map, 63 = 63, 0 corrections | identical, 63 = 63, 0 corrections | yes |
| T0-CTL | identical (63 items, 35 verified) | identical, 63 / 35 | yes |
| T0-FORM | identical to T0-CTL | identical bare vs dotted | yes |

### Arm 0 (static fixtures goparity3; post hoc)

| id | predicted | observed | pass |
|---|---|---|---|
| P0-WRAP | GO801 on w1; none on w2, w3 | exactly that | yes |
| P0-OPQ | GO801 on m1; none on m2 | exactly that | yes |
| P0-BODY | `title` body Str required max_len 40; one `body_model` | `["title","body","Str",true,40]`; one `body_model` (POST /widgets, widgetBody) | yes |
| P0-CHOICES2 | c1 none; c2 two; S2 Run.status; S3 Ticket.status | goparity3 `Gadget.kind [a,b]`, S2 `[queued,cancelled]`, S3 `[open,closed]` | yes |
| P0-REG | round-2 static rows as before | all six true | yes |

### Arm S (S1, S2, S3; post hoc)

| id | predicted | observed | pass |
|---|---|---|---|
| PS-PI3-3 | 63-70 items; share 0.60-0.68; R2 creates on queues and handoffs verified | 67 items; verified 40 (share **0.597**), unexercised 26, unclear 1; `R2 POST /queues/{qid}/tickets` verified, `R2 POST /tickets/{tid}/handoffs` **unexercised** | **NO** (0.597 < 0.60; handoffs create not verified) |
| PS-PI4-3 | all seed-verified still verified; >= 0.95 | 38 of 40 = 0.95 (lost `T3 Handoff.to_user_id ge 1`, `R1 POST /tickets/{tid}/handoffs`, both now unexercised) | yes (exactly at the floor) |
| PS-PR3-3 | identical verdict map | 67 = 67, 0 corrections, no differences | yes |
| PS-JM31 | `GO801\|POST /tickets/:tid/close\|Ticket`; original 0 | exact; original 0 | yes |
| PS-ST2 | `GO801\|GET /tickets/:tid\|Ticket` | exact | yes |
| PS-ST3 | `GO801\|POST /tickets/:tid/reopen\|Ticket` | exact | yes |
| PS-S3A | 0 findings under `from_spec` and `go_spec_policy` | 0 and 0 | yes |
| PS-S2 | PI1-PI7, PR3, PR-T2, PA1-PA3 as round 2 | all twelve true | yes |
| PS-S1 | PR1, PR2, PR4, PB1, PB1-CLI, PB2, PB3 | all seven true | yes |

### Arm M2 (Streak a0679e9; post hoc; M-R1 on Docker)

| id | predicted | observed | pass |
|---|---|---|---|
| M-A0 | 0 findings | 0 | yes |
| M-A1 (informational) | G3: 18-24 of 24 wrapped routes judged | **7 of 26 judged (6 of 25 wrapped)**; missed the prediction | reported (miss) |
| M-SM1 | `GO801\|GET /v1/habits/:id\|Habit` | `GO801\|GET /habits/:id\|Habit` (detected, but the key has no `/v1`) | **NO** (strict key; the `/v1`-less key is the registered post hoc row) |
| M-SM2 | `GO801\|PATCH /v1/habits/:id\|Habit` | no new finding | **NO** |
| M-SM3 | a GO801 on GET habits/{id}/completions | no new finding | **NO** |
| M-SM4 (reported) | GO801 on dismiss (p 0.6) | no new finding | reported (miss) |
| M-I1 | exit 0; `not_run:server_dialect`, mysql; 26 routes / 26 contracts / 0 items; empty proposals; 0 docker calls; no SQLite file; review list 0 items | all of it true except the review clause: `review` lists **176** entries, 0 with a verdict | **NO** (the registered clause; Amendment 2 already saw it fail) |
| M-I2 | 4 owner_uses, 2 json_dash, status_facts only Nudge.delivery, no choices on quiet_*/nudge_time | 4 / 2 / 1 (`Nudge.delivery` failed, pending, sent, suppressed) / no spurious choices; plus 10 `body_model` | yes |
| M-P1 | `go_spec_policy == GoPolicy.from_spec` in every field | equal in every field; `undeclared_tables == [SchemaMigration]` | yes |
| M-R1 (Docker, `--layers ACD --database mysql`) | exit 0, pass, 0 findings, 480 cases, normalised sha `351c3104c613c54a` | exit 0, pass, 0 findings, 480 cases, 29.4 s; normalised sha `28a6b06463a38be6` (differs); identical to Arm M once 978b468's layer-A gating note is put back (GO805 leaves the gated note, declared in section 8) | yes (pass rule allows a difference confined to layer A's notes) |

### Arm J (job-scheduler-go 00dd1de; Docker-free; post hoc)

| id | predicted | observed | pass |
|---|---|---|---|
| J-0 | pass, 0 findings; report identical to 978b468 | pass, 0 findings, 154 cases both runs; digest differs only by the gated note (GO805), identical after restoring it | yes |
| J-A | 0 findings | 0 findings, 9 of 22 routes judged | yes |
| J-SM1 (reported) | A 0; B or D reports it (p 0.5) | A 0; nothing reported (rc 0): survived | reported (miss) |
| J-SM2 (reported) | A 0; D reports it (p 0.8) | A 0; D 35 findings (rc 1) | reported (as predicted) |

### Tier 2 (K8-K10; post hoc)

| id | predicted | observed | pass |
|---|---|---|---|
| P2-805A | S2 404 (3 linked raises); S3 404 (6); Streak unclear/none | S2 404 with **2** linked; S3 404 with **4** linked; Streak none (GO805 silent) | **NO** (status exact, counts are part of the rule; Amendment 3 ruled it stays failed) |
| P2-805B | `GO805\|GET /tickets/:tid\|not_owned` on the 403 copy | exact (api/api.go:211) | yes |
| P2-805C | 0 GO805 on S1, S2, S3, Streak, job-scheduler | 0 on all five | yes |
| P2-805D | GO805 out of the gated list; GO804/GO806 text identical | as stated | yes |
| P2-J0 | every J0' cell cites an existing file:line | 20 rows, 169 citations, 0 problems | yes |
| P2-DEF (reported) | K9 not built | `check.py` unchanged | reported |

### Arm H (held-out S4 `shelfie`; **blind**; 15 routes, Gin + GORM + glebarez/sqlite)

| id | predicted | observed | pass |
|---|---|---|---|
| H-PI1 | recall >= 0.90; auth >= 0.85; 0 extra | recall 1.0; auth 1.0; 0 extra, 0 missing | yes |
| H-PI2 | 0 confident-wrong owners | confident `Note.user_id`, `Shelf.user_id` (recall 1.0); 0 wrong | yes |
| H-PI3 | verified share >= 0.50; refuted 0-4, each with correction/baseline; every unexercised item a reason | 119 items; **1 verified (share 0.0084)**, 118 unexercised, 0 refuted; every unexercised item has a reason | **NO** (blind miss, far outside the predicted 0.50-0.70) |
| H-PI4 | >= 0.95 retained | 1 of 1 (vacuous, only one seed-verified item) | yes (vacuous) |
| H-PI5 (reported) | truth machine's first edge proposed (p 0.7) | `Book.status` first edge queued -> reading **not** proposed (0 proposed edges) | reported (miss) |
| H-PI7 | import 10x identical | 10 of 10 | yes |
| H-PR3 | identical verdict map | 119 = 119, no differences | yes |
| H-PA1 | private columns within truth; owners within truth | private `User.password`; owners `Note`, `Shelf` `user_id`; 0 wrong | yes |
| H-PA0 | 0 findings outside the baseline | total 5 (all in the candidate spec's baseline), 0 outside | yes |
| H-JUDGE | judged share >= 0.75 | 10 of 10 owner-scoped routes (1.0) | yes |
| H-X | import <= 180 s | 5.5 s | yes |

What H-PI3 shows: the import's own layer R note reads `layer R: no login given; probes run without callers`. All `R1` items say "no user of any role was logged in", every authenticated probe
was answered 401, only `R3 GET /health 5xx` was verified. The 118 unexercised items are therefore one cause (no caller in layer R on the held-out), not 118 separate misses. The cause inside the library
or the harness (the harness's `LOGIN`/`USERS` are the standard form and `build_db` stores the plain password the login compares) was not investigated by this node; that is for a post hoc node.
Because the verified set is 1, H-PI4 and H-PR3 passed trivially.

### Cross-arm (post hoc)

| id | observed | pass |
|---|---|---|
| PB-X | 9 of 9 identical | yes |
| PB-F | 38 files; 0 changed vs 978b468 beyond the 3 declared test edits | yes |
| PB-G | one difference, the allowed `fx/goextract [derived]`: `GO801\|DELETE /api/items/:id\|Item` appears (Amendment 2); workflows and inventory identical | yes |
| PB-P / PB-P2 | all identical | yes |
| PB-N | pytest 607 passed, 1 skipped both; goginapp/gotickets exports identical (`7560c50c8e65c5ac`) | yes |
| PB-H | all help texts and MCP tools/list identical | yes |
| PB-T | base 2 failed, 3326 passed, 36 skipped; now 2 failed, 3503 passed, 36 skipped; failing/error ids identical (none only-now, none only-base) | yes |
| PB-D | `dogfood/studio/check.sh` rc 0 on both | yes |
| PW | 0 changed; 0 docker/claude shim calls before the first test-suite row (116 calls from the test suites themselves, reported) | yes |

Gated count run: 49 (the runner's list). Passed 42, failed 7, NOT RUN 0.

## 3. Held-out mutants HM1-HM16 (**blind**): control failed, no mutant judged

Command as given; run once; `scored-hmut/hmut3.json`, `scored-hmut/evidence/control.json`. The S4 tree sha256 `99b201eed7ebf771860f4ffa4b41f4b4dd852c75da4a934a87e8ba062518c0f0`; truth sha256 `0855ff58...8501a8`; the runner's freeze check passed (65 entries, 0 mismatches);
the harness `BINARY_ENV` injection works; the pristine copy equals S4 and shows no new findings.

The strict control **failed**: `control.passes: false`, because layer A's baseline already reports GO801 on two held-out routes: `GO801|POST /books/:id/finish|Shelf` and `GO801|POST /books/:id/start|Shelf`
(the routes of HM13 and HM12). The original has 5 GO801 findings in total, all in the candidate spec's baseline (`GET /books/:id`, `PATCH /books/:id`, `POST /books/:id/finish`, `POST /books/:id/notes`,
`POST /books/:id/start`, all table `Shelf`: books reach their owner through the parent shelf, i.e. `owner_via`, which the import does not infer; this is the same cause the prereg names for HM11/HM12-13). By Amendment 4's strict control rule
("a baseline finding on an HM route fails the control and the run") the run is completed with the verdict `incomplete`:

| gate | observed |
|---|---|
| killed | 0 of 16 judged (all 16 `not run`: HM1 ... HM16) |
| HM1-4 (need >= 3) | 0 of 4, FAIL (not judged) |
| HM5-6 (2/2) | 0 of 2, FAIL (not judged) |
| HM7-8 (2/2) | 0 of 2, FAIL (not judged) |
| HM9-10 (2/2) | 0 of 2, FAIL (not judged) |
| overall (>= 13 of 16, predicted 12-15) | **gate not met; no mutant was judged**, so no survivor or kill statement can be made |

Other control facts: layer R was available but verified 1 of 119 items (the same no-login cause as H-PI3); the 119 items were stable between two runs; 0 registered R items refuted; no fixtures changed; 0 docker/claude shim calls.
The 16 predictions for the mutants (12-15 killed) are therefore neither confirmed nor refuted. There is no re-run: this was not a crash and the ledger permits a rerun only after `crash_before_metric`.

## 4. Misses, stated

Blind (Arm H and HM): **H-PI3** (share 0.008 vs >= 0.50, one cause: layer R had no caller on S4); **H-PI5** (the machine's first edge not proposed; reported row, p 0.7); the **held-out mutant control** (baseline GO801 on the
start and finish routes: not predicted by the prereg, which expected the baseline to be empty of HM routes except by the HM11-13 "reported" outcome). Everything else blind passed: H-PI1 (recall and auth 1.0), H-PI2, H-PI4 (vacuous),
H-PI7, H-PR3, H-PA1, H-PA0, H-JUDGE (1.0), H-X.

Post hoc (dev data): PS-PI3-3 (0.597 and handoffs unexercised); M-SM1 (strict `/v1` key), M-SM2, M-SM3, M-SM4 (reported); M-I1 review clause; M-A1 (7 of 26 vs 18-24); P2-805A counts (2 and 4 vs 3 and 6); J-SM1 (reported, survived).
PS-PI4-3 passed only at the exact floor (0.95).

## 5. Not run

Nothing in the probe arms is NOT RUN. Not run in this task by design: `go_parity_mutants3.py` (the 18 library mutants), the dev re-applied mutants, the `go_parity_s3r.py` Tier 0 (already run earlier; its data is cited above),
Tier 3 (not in this round by Amendment 1 Q2), and V's independent verify run. The 16 held-out mutants were **not judged** (control failure, section 3).

## 6. Files

Created by the runs (all untracked, inside the worktree):
`docs/results/data/2026-10-18-go-parity-round3/scored/probes3.json`, `.../scored/pytest_base.txt`, `.../scored/pytest_now.txt`,
`.../scored-hmut/hmut3.json`, `.../scored-hmut/evidence/control.json`, `.../scored-hmut/hmut_import_candidate_spec.py`, `.../hmut_import_go_policy.json`, `.../hmut_import_profile.py`,
`.../hmut_import_proposals.json`, `.../hmut_import_report.json`, `.../ledger-hmut.jsonl`, and this file. Appended to (tracked, by the runner): `.../ledger.jsonl`.
Scratch (outside the repo): `/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/g3-run/` (`base`, `streak`, `jobsched`, `scored`, `hmut`, `scored.log`, `hmut.log`).

## Orchestrator note and rulings
Both freezes verify after the runs (freeze3 31 entries, freeze4 65 entries, 0 mismatches); the ledger records one `scored` run per arm and the held-out mutant stage as
`completed` with `control_passed: false`. The independent opus verifier V of the prereg was NOT run in this session (cost); the saved raw outputs and the pure scorer
allow it later. **Result against the pass rule:** Tier 0 passes; Tiers 1, 2 and 4 do not (42 of 49 gated rows pass; the blind failures are H-PI3 (1 of 119 verified) and, by the strict
control rule, all 16 held-out mutants unjudged). **Held-out mutants:** Amendment 4's strict control rule failed the run because layer A's baseline already reports GO801 on
two mutated routes (`POST /books/:id/start` and `/finish`, table `Shelf`) of the unmutated S4; the registered gate (>= 13 of 16) is therefore NOT met and the run is not repeated (one scored run).
**Post hoc** (not blind, labelled): the cause of H-PI3 (the import's layer R note says no login was given, so every authenticated probe answered 401: whether the cause is the
S4 harness, the author's `studio.toml` or a library defect is not yet diagnosed) and why layer A reports a baseline GO801 on the status-transition routes are separate diagnoses.
The library changes (K1-K6, K8) are merged with these failures stated: PB-X, PB-F, PB-G, PB-P, PB-N, PB-D, PB-T (same two failing tests as the base) and PW all pass.
