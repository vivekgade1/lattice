# Go-behind-Python gaps, round 2 (Spike J, node J2): results

**NOT RUN, stated first: Arm M (Streak a0679e9 on MySQL 8.4, M1-M4) was not run (no Streak checkout, no Docker, none faked). Arm S3 (`tests/spec/gotickets`, the blind arm) was not run: its 10 gated rows and mutants JM31-JM34 are NOT RUN, pending the isolated author (Amendment 1, Q2). The 47-row gate and the 35/35 mutant gate are therefore NOT met.** Every S1 and S2 number below is development data (`dev`) and is gated as the pre-registration says.

2026-10-18. Node J2, worktree `/home/user/wt/j2` (branch `go-parity-gaps-j2`, base `193f7f6` plus `next-phases`). Pre-registration: `2026-10-18-go-parity-gaps-round2-prereg.md` with Amendment 1. Data: `docs/results/data/2026-10-18-go-parity-gaps-round2/`. No model, no API key, no Docker, no sub-agent. Nothing was committed or staged. The orchestrator records Amendment 3 and runs the scored run; the run below is the build node's own pass over the frozen files.

## 1. Headline

| item | value |
|---|---|
| gated rows (47) | **37 PASS, 0 FAIL, 10 NOT RUN (all S3).** Of the 37, five Arm 0 rows (P0-PARSE, P0-OMIT, P0-BIND, P0-CHOICES, P0-PRIV) carry an S3 sub-claim that is NOT RUN, and PG805 and PX each have an informational S3 part NOT RUN |
| mutants (35) | **30 killed, 1 survived (JM6), 4 NOT RUN (JM31-JM34, S3).** JM8 is killed by its unit test only (its PI2-3 detector is S3) |
| line budgets | all within except `goextract_closure.go`: **102 against 80 (22 over)**. The Go total is 131 against 140 (assemble 5/30, routes 24/30) |
| S2 import (dev) | 41 items, 25 verified (0.610), 0 refuted, 16 unexercised (all with reasons), 0 corrections. Round 1: 38 items, 19 verified (0.50), 4 refuted, 15 unexercised |
| S1 (dev) | R: 23 items, 16 verified, 0 refuted, 10 of 10 identical. B with `rules=RULES`: 32 cases, 0 findings. The CLI with `--rules` equals it; without the flag the 6 F2 findings are unchanged |
| byte-compat | PB-F, PB-X (8 of 8), PB-G, PB-P, PB-P2, PB-H all identical to `193f7f6` (with the readings in section 5) |
| tests | PB-T: 7 failed / 37 skipped / 101 errors on both trees, the same failing and skipped ids; passed 2909 to 2927 (+18 new). PB-D passes on both trees |
| lint | ruff check and format and gofmt clean on every touched file, before and after (`lint.txt`) |

## 2. What was built (E1-E7)

* **E1 path parse** (`goextract_closure.go`, `g.policy` only): `params[].parse = "Int"` and `params[].parse_at` when `strconv.Atoi/ParseInt/ParseUint` is applied to `c.Param(..)` (directly or through one local), the error is bound to a name, and its branch returns only constant 4xx refusals. `go_rules.infer_path_parse` retypes those params to `Int` and records `path_parse` (evidence is the parse site).
* **E2 omitempty bounds** (default extract): with `omitempty`, a lower bound of 1 becomes 0 (numbers) or none (string length); above 1 it becomes none plus one route `unread` entry.
* **E3 bind status** (`raises[].bind`, `g.policy` only; `go_rules.infer_bind_status`): one constant status is stated as the profile's `validation_status`; a disagreement changes nothing and records `unclear` with the statuses.
* **E4 choices** (`go_rules.infer_choices`): default first, then the other constant values written (`status_writes`) or named as `from` states (`status_facts`), sorted. The Seeder is untouched.
* **E5 private columns** (`go_rules.infer_private`, `go_rules.patch_profile`): the `json:"-"` columns minus the primary key, written into the generated profile source after `lift_repo` (the profile is regenerated text; `lift_repo` is frozen). The same patch states E3's `validation_status`. `rules.py` gains 2 call lines and a one-line owner filter (`rule == "owner_uses"`), because `status_facts` entries also carry `table` and `column`.
* **E6 Gin library middleware** (`routes.go`): the eight listed `gin.*` Logger/Recovery constructors (package resolved by import path) are neither a guard nor unreadable, in `Use`, in a group's arguments and in a route's leading handlers. Anything else unresolved keeps today's silence.
* **E7** `python -m lattice.spec check --rules MODULE:ATTR` (`__main__.py`).
* `derive.py`: the wrong `spec_policy` docstring (J1 deviation 7), 5 lines.

### Line budgets (`git diff --numstat` insertions + deletions vs `193f7f6`)

| file | used | budget |
|---|---|---|
| `lattice/importer/go_rules.py` | 150 | 220 |
| `lattice/importer/rules.py` | 5 | 25 |
| `lattice/gocheck/derive.py` | 5 | 6 |
| `lattice/gocheck/src/goextract_closure.go` | **102** | **80 (over by 22)** |
| `lattice/gocheck/src/goextract_assemble.go` | 5 | 30 |
| `lattice/gocheck/src/routes.go` | 24 | 30 |
| `lattice/spec/__main__.py` | 6 | 15 |
| J1 tests (assertions an E-decision changes) | 10 (`test_go_derive.py` 8: the policy extract also adds `parse`, `parse_at`, `bind`; `test_go_rules.py` 2: `inferences` now holds 7 entries, so the owner assertion filters `rule == owner_uses`) | 40 |

The closure file overran because E1 and E3 are read there (the two facts share one walk). The assemble budget was not used to absorb it.

## 3. Process (as the pre-registration orders it)

1. `fixtures.sha256` (goparity2, goparity2mixed, with `truth.json`) was written **before any library edit**. S3 was not created.
2. `amendment2_notes.txt`, `frozen_193f7f6.sha256`, `pbx_default_extract_193f7f6.sha256`: the section 8 table recomputed at `193f7f6`, the default-extract digests, the E5 path.
3. Library code, tests, the two runners (shakedowns on Arm 0, S1, S2, cross-arm; disclosed in section 7).
4. `freeze.sha256` (21 entries: the 7 library files, 6 test files, the 6 fixture files, both runners). The runner verified every entry, its own included, before the first metric (`freeze.mismatches = 0`). Hashes were re-checked after the mutant run: unchanged.
5. The build node's pass over all arms (`run/`), then the 35 mutants (`mutants/`).

## 4. The 47 rows and 35 mutants, predicted against observed

Full observed values are in `run/probes2.json` and `mutants/mutants2.json`. (`/` replaces `|` in keys below.)

| id | probe | predicted | observed | result |
|---|---|---|---|---|
| P0-PARSE | E1 on goparity2, and S2 (extract facts and the model) | {"goparity2": "a: Int; b, c: no key", "S2": "cid, rid x2", "S3": "qid, tid x4"} | {"goparity2": {"GET /a/{id}": ["Int"], "GET /b/{id}": [null], "GET /c/{id}": [null]}, "S2_model_Int": [["GET /runs/{rid}", "rid"], ["POST /configs/{cid}/runs", "cid"], ["POST /runs/{rid}/cancel", "rid"]], "S2_inferences": 3} | PASS |
| P0-OMIT | E2 on goparity2 and S2 limit | {"goparity2": "ge 0/le 10; ge null/le 10 + 1 unread; ge 1/le 10", "S2": "ge 0/le 50", "S3": "ge 0/le 100"} | {"goparity2": {"GET /n1": [0, 10, 0], "GET /n2": [null, 10, 1], "GET /n3": [1, 10, 0]}, "S2": [0, 50]} | PASS |
| P0-BIND | E3 on goparity2, goparity2mixed, S2 | {"goparity2": 422, "goparity2mixed": "unchanged + unclear entry", "S2": 422, "S3": 422} | {"goparity2": {"validation_status": 422, "bind_status": [{"rule": "bind_status", "status": 422, "evidence": ["code:main.go:93", "code:main.go:103", "code:main.go:113"]}]}, "goparity2mixed": {"validation_status": null, "bind_sta... | PASS |
| P0-LIBMW | layer A on goparity2: gin.Recovery()/Logger vs ext.Guard() | ["GO801/GET /notes/Note"] | ["GO801/GET /notes/Note"] | PASS |
| P0-CHOICES | E4 on S2: choices from status_facts | {"S2": {"Run.status": ["queued", "cancelled"]}, "S3": {"Ticket.status": ["open", "closed"]}} | {"Run.status": ["queued", "cancelled"]} | PASS |
| P0-PRIV | E5 on S2 and the goextract fixture (json:"-") | {"S2": {"User": ["password", "token"]}, "goextract": {"User": ["password_hash"]}, "S3": {"User": ["password_hash"]}} | {"S2": {"User": ["password", "token"]}, "goextract": {"User": ["password_hash"]}} | PASS |
| PA4 | static: derive_go_policy on tests/gocheck/fixtures/goextract | {"owners": {"Item": "owner_id", "Audit": "actor"}, "private": {"User": ["password_hash"]}} | {"owners": {"Item": "owner_id", "Audit": "actor"}, "private": {"User": ["password_hash"]}} | PASS |
| PG805 | GO805 gated: gated_note() and GO_GATED | "byte-identical to base" | {"identical": true, "pairing_count_informational": {"goginapp": {"compare_uses": 3, "with_a_raise_within_3_lines": 2}, "goextract": {"compare_uses": 3, "with_a_raise_within_3_lines": 1}}} | PASS |
| PR1 | layer R on S1 (go_harness_rb, the safe app) | {"items": "18-30", "verified_share": "0.55-0.85", "refuted": 0} | {"items": 23, "verdicts": {"verified": 16, "refuted": 0, "unexercised": 7, "unclear": 0}, "verified_share": 0.6957, "routes": {"verified": 4, "total": 4}} | PASS |
| PR2 | layer R on S1, app mutants JM1 and JM2 | "each refutes >= 1 item on GET /runs/{rid}" | {"JM1": {"refuted": ["R1 GET /runs/{rid}"], "on_route": ["R1 GET /runs/{rid}"]}, "JM2": {"refuted": ["R3 GET /runs/{rid} 404 Not found"], "on_route": ["R3 GET /runs/{rid} 404 Not found"]}} | PASS |
| PR4 | layer R on S1, 10x | "identical item verdicts and findings" | {"identical": 10, "of": 10} | PASS |
| PB1 | layer B on S1, safe app | {"cases": "28-40", "findings": 0} | {"cases": 32, "findings": []} | PASS |
| PB1-CLI | layer B on S1 through the CLI, with and without --rules | {"with --rules": "identical to PB1 (0 findings)", "without": "6 F2 findings"} | {"with_rules_rc": 0, "with_rules_findings": [], "python_api": [], "without_rules_rc": 1, "without_rules_findings": 6} | PASS |
| PB2 | layer B on S1, GOAPP_BUG=nocheck / nocancel | {"nocheck": ">= 1 B finding on POST /configs/{cid}/runs", "nocancel": ">= 1 B finding on POST /runs/{rid}/cancel"} | {"nocheck": {"endpoints": ["POST /configs/{cid}/runs"], "findings": 2}, "nocancel": {"endpoints": ["POST /runs/{rid}/cancel"], "findings": 2}} | PASS |
| PB3 | layer B on S1, 10x (safe app and nocheck) | "identical finding sets" | {"identical": 20, "of": 20, "safe": [], "nocheck": ["B/POST /configs/{cid}/runs/POST /configs/{cid}/runs / ann / cid=2", "B/POST /configs/{cid}/runs/POST /configs/{cid}/runs / ben / cid=2"]} | PASS |
| PI1 | rules-first import of S2: framework, routes, contracts, tables | {"framework": "gin", "routes": 7, "contracts": 7, "tables": 3, "public": ["GET /health", "POST /login"], "others": "C... | {"framework": "gin", "routes": 7, "contracts": 7, "tables": ["configs", "runs", "users"], "auth": {"GET /health": "Public", "POST /login": "Public", "GET /configs": "Customer", "POST /configs/{cid}/runs": "Customer", "GET /runs... | PASS |
| PI2 | owners and inferences[] of the S2 import | {"owner": "Run: user_id, 4 evidence lines (1 assign, 3 compare)", "inferences": {"owner_uses": 1, "path_parse": 3, "b... | {"inferences": 7, "by_rule": {"owner_uses": 1, "path_parse": 3, "bind_status": 1, "status_facts": 1, "json_dash": 1}, "how": ["assign", "compare", "compare", "compare"], "owners": {"User": null, "Config": null, "Run": "user_id"}} | PASS |
| PI3 | rule items of the S2 import on the original (seed) | {"items": "38-44", "verified_share": "0.62-0.82", "refuted": "0-1", "corrections": 0, "gate": "share >= 0.60; every r... | {"items": 41, "verdicts": {"verified": 25, "unexercised": 16}, "verified_share": 0.6098, "refuted": 0, "refuted_without_correction": [], "unexercised_without_reason": [], "refuted_items": {}} | PASS |
| PI4 | the corrected spec re-probed on variant2 | "every seed-verified item still verified (>= 0.95)" | {"seed_verified": 25, "still_verified": 25, "fraction": 1.0, "lost": {}} | PASS |
| PI5 | proposals.json (mining) | "2-6 proposals including the machine runs.status queued -> cancelled" | {"count": 4, "kinds": ["invariant", "machine"], "machine_runs_status": true, "all_pending": true, "sentences": ["Run's config_id is positive.", "No two User rows share name.", "No two User rows share token.", "Run.status moves ... | PASS |
| PI6 | owner review: python -m lattice.importer review <dir> list | "exit 0; ids equal the report's not-verified ids after the correction map" | {"exit": 0, "listed": 16, "expected": 16, "listed_not_expected": [], "expected_not_listed": [], "corrections": 0, "stderr": ""} | PASS |
| PR3 | layer R on S2 with the imported candidate_spec.py | "the same item verdicts as the import's final verdicts (after the correction map)" | {"items_import": 41, "items_rerun": 41, "corrections": 0, "differences [import, rerun]": {}} | PASS |
| PR-T2 | T2 items of the S2 import (E5) | "T2 User.password and T2 User.token present and verified" | {"T2 User.password": "verified", "T2 User.token": "verified"} | PASS |
| PA3 | edits on a scratch copy of S2 under go_spec_policy of its candidate sp | {"JM5": "GO801 on POST /runs/:rid/cancel Run (E6)", "JM6": "0 new findings (C5b, stated in advance)", "original_outsi... | {"original_total": 1, "original_outside_baseline": 0, "original_outside": [], "new_findings": {"JM5": [{"code": "GO801", "key": "GO801/POST /runs/:rid/cancel/Run"}], "JM6": []}} | PASS |
| PI7 | the S2 import 10x into separate scratch dirs | "byte-identical after the timing normaliser" | {"identical": 10, "of": 10, "differing_files": [], "walls_s": [10.22, 9.0, 8.86, 9.93, 9.27, 8.44, 9.5, 9.56, 9.17, 9.15]} | PASS |
| PA1 | derive_go_policy on S2 | {"tables": 3, "users_table": "User", "owners": {"Run": "user_id"}, "private": {"User": ["password", "token"]}, "admin... | {"tables": 3, "users_table": "User", "owners": {"Run": "user_id"}, "private": {"User": ["password", "token"]}, "admin_routes": [], "machines": {}, "baseline": ["GO802/POST /login/User.token"]} | PASS |
| PA2 | go_spec_policy with a spec that declares Run with no owner + the machi | {"run_owned": false, "machine": ["Run"], "baseline": []} | {"run_owned": false, "machine": ["Run"], "baseline": [], "derived_baseline": ["GO802/POST /login/User.token"]} | PASS |
| PI1-3 | S3 (blind) | null | "NOT RUN: tests/spec/gotickets is written by a separate isolated author after the library freeze (Amendment 1, Q2); it has not been delivered" | NOT RUN |
| PI2-3 | S3 (blind) | null | "NOT RUN: tests/spec/gotickets is written by a separate isolated author after the library freeze (Amendment 1, Q2); it has not been delivered" | NOT RUN |
| PI3-3 | S3 (blind) | null | "NOT RUN: tests/spec/gotickets is written by a separate isolated author after the library freeze (Amendment 1, Q2); it has not been delivered" | NOT RUN |
| PI4-3 | S3 (blind) | null | "NOT RUN: tests/spec/gotickets is written by a separate isolated author after the library freeze (Amendment 1, Q2); it has not been delivered" | NOT RUN |
| PI5-3 | S3 (blind) | null | "NOT RUN: tests/spec/gotickets is written by a separate isolated author after the library freeze (Amendment 1, Q2); it has not been delivered" | NOT RUN |
| PI6-3 | S3 (blind) | null | "NOT RUN: tests/spec/gotickets is written by a separate isolated author after the library freeze (Amendment 1, Q2); it has not been delivered" | NOT RUN |
| PI7-3 | S3 (blind) | null | "NOT RUN: tests/spec/gotickets is written by a separate isolated author after the library freeze (Amendment 1, Q2); it has not been delivered" | NOT RUN |
| PR3-3 | S3 (blind) | null | "NOT RUN: tests/spec/gotickets is written by a separate isolated author after the library freeze (Amendment 1, Q2); it has not been delivered" | NOT RUN |
| PA1-3 | S3 (blind) | null | "NOT RUN: tests/spec/gotickets is written by a separate isolated author after the library freeze (Amendment 1, Q2); it has not been delivered" | NOT RUN |
| PA3-3 | S3 (blind) | null | "NOT RUN: tests/spec/gotickets is written by a separate isolated author after the library freeze (Amendment 1, Q2); it has not been delivered" | NOT RUN |
| PW | snapshot of the fixtures around every run; docker/claude shims | {"changed": 0, "shim_calls": 0} | {"changed": [], "shim_calls": []} | PASS |
| PX | wall time | {"s2_import": "15-90 s", "r_s1": "2-15 s", "b_s1": "3-20 s", "gate": "<= 180 s; <= 30 s; <= 60 s"} | {"s2_import_with_go_build_s": 9.55, "s2_import_prebuilt_mean_s": 9.31, "r_s1_s": 2.29, "b_s1_s": 3.09} | PASS |
| PB-F | every frozen file has its sha256 at 193f7f6; existing fixtures and tes | "all identical" | {"files": 35, "prereg_value_differs_from_193f7f6": ["lattice/gocheck/src/*.go except goextract.go, goextract_tables.go, goextract_closure.go, goextract_assemble.go, routes.go (18 files, `cat` in `LC_ALL=C ls` order, which inclu... | PASS |
| PB-X | default gocheck extract byte-identical on the 7 module dirs and goapp | "8 of 8 identical to 193f7f6" | {"identical_to_base": 8, "of": 8} | PASS |
| PB-G | Go outputs byte-identical to 193f7f6 | "all identical" | {"workflows --static": {"same": true, "rc": [0, 0]}, "inventory": {"same": true, "rc": [0, 0]}, "literal inventory: files added by the new fixtures only": {"added": ["goparity2/go.mod", "goparity2/main.go", "goparity2mixed/go.m... | PASS |
| PB-P | Python outputs byte-identical to base | "all identical" | {"control: the base twice, after the normaliser, is identical": true, "control differing files": [], "rules import (multivendor) identical to base after the normaliser": true, "differing files": [], "report.json has no inferenc... | PASS |
| PB-P2 | a Python import with a decoy studio.toml [app] app | "report.json app_factory is the Python make_app, as without the decoy" | {"plain": {"rc": 0, "app_factory": "bench.import_repos.multivendor:make_app", "stderr": ""}, "decoy": {"rc": 0, "app_factory": "bench.import_repos.multivendor:make_app", "stderr": ""}, "base_with_decoy": {"rc": 0, "app_factory"... | PASS |
| PB-H | help texts and MCP tools/list identical; check --help differs by --rul | "as stated" | {"lattice --help": {"same": true, "rc": [0, 0]}, "python -m lattice.importer --help": {"same": true, "rc": [0, 0]}, "python -m lattice.spec --help": {"same": true, "rc": [0, 0]}, "python -m lattice.spec check --help": {"added_o... | PASS |
| PB-T | pytest tests/importer pycheck gocheck spec mcp studio | "the same failing, skipped and error ids as 193f7f6 (plus the new tests passing)" | {"base": ["7 failed", "2909 passed", "37 skipped", "101 errors"], "now": ["7 failed", "2927 passed", "37 skipped", "101 errors"], "wall_s": 1017.8, "counts_base": {"failed": 7, "skipped": 37, "error": 101}, "counts_now": {"fail... | PASS |
| PB-D | dogfood/studio check (layers DX, venv python) | "passes" | {"base": [0, "  2.59s cancel_while_drafting (start 0.41, db 0.01, steps 2.07, 7 requests)", ""], "now": [0, "  2.74s a_change_rejected_while_drafting_stays_rejected (start 0.60, db 0.01, steps 2.03, 6 requests)", ""]} | PASS |

### Mutants

| id | mutant | killed | flipped detectors |
|---|---|---|---|
| JM1 | getRun skips caller() (goapp copy) | killed | {"refuted_on_route": ["R1 GET /runs/{rid}"]} |
| JM2 | getRun answers 403 for a missing run (goapp copy) | killed | {"refuted_on_route": ["R3 GET /runs/{rid} 404 Not found"]} |
| JM3 | GOAPP_BUG=nocheck (existing variant) | killed | {"endpoints": ["POST /configs/{cid}/runs"]} |
| JM4 | GOAPP_BUG=nocancel (existing variant) | killed | {"endpoints": ["POST /runs/{rid}/cancel"]} |
| JM5 | goginapp copy: cancel drops AND user_id = ? (detectors: PA3 GO801 under E6; R2 c | killed | {"pa3_killed": true, "r_killed": true, "r_item": "R2 POST /runs/{rid}/cancel"} |
| JM6 | goginapp copy: token loses json:"-" (detector: PR-T2; its PA3 side is predicted  | SURVIVED | {"pa3_killed": false, "r_killed": false, "r_item": "T2 User.token"} |
| JM7 | D2 requires both compare and assign (Audit loses its owner) | killed | {"probe:PA4": [true, false], "test:test_assign_only_owner": [true, false]} |
| JM8 | D2 picks the first of two candidate columns instead of unclear | killed | {"test:test_two_candidates_unclear": [true, false]} |
| JM9 | go_spec_policy keeps the derived baseline | killed | {"probe:PA2": [true, false], "test:test_the_spec_policy_has_an_empty_baseline_and_the_spec_wins_for_declared_tables": [true, false]} |
| JM10 | the derived owner wins over the spec's declared table | killed | {"probe:PA2": [true, false]} |
| JM11 | harness_factory used for Python front ends too | killed | {"probe:PB-P2": [true, false], "test:test_the_python_path_never_asks_for_the_harness_factory": [true, false]} |
| JM12 | private columns from name heuristics instead of json | killed | {"probe:PA4": [true, false], "test:test_a_private_column_is_the_json_dash_never_a_name": [true, false]} |
| JM13 | the json key leaks into the default extract | killed | {"probe:PB-X": [true, false], "test:test_the_default_extract_has_no_json_key": [true, false]} |
| JM14 | GO805 ungated by line-proximity pairing | killed | {"probe:PG805": [true, false], "probe:PB-G": [true, false]} |
| JM15 | the import writes candidate_spec.py into the target dir | killed | {"probe:PW": [true, false]} |
| JM16 | inferences written as [] for every import | killed | {"probe:PB-P": [true, false], "test:test_inferences_reach_the_report_only_when_there_are_some": [true, false]} |
| JM17 | E6 off: Gin library middleware marks the route unreadable (round-1 behaviour) | killed | {"probe:P0-LIBMW": [true, false], "probe:PA3": [true, false], "test:test_e6_library_middleware_keeps_a_route_readable_and_other_imports_do_not": [true, false]} |
| JM18 | E6 too broad: any unresolved outside-package middleware is treated as readable | killed | {"probe:P0-LIBMW": [true, false]} |
| JM19 | E1 off: no path retyping | killed | {"probe:P0-PARSE": [true, false], "probe:PI3": [true, false]} |
| JM20 | E1 ignores condition 2: a parse with a `_` error retypes too | killed | {"probe:P0-PARSE": [true, false]} |
| JM21 | E1's parse key (and E3's bind key) leak into the default extract | killed | {"probe:PB-X": [true, false], "test:test_e1_and_e3_keys_are_absent_from_the_default_extract": [true, false]} |
| JM22 | E2 off: omitempty ignored | killed | {"probe:P0-OMIT": [true, false], "probe:PI3": [true, false]} |
| JM23 | E2: omitempty,min=3 mapped to ge 0 | killed | {"probe:P0-OMIT": [true, false]} |
| JM24 | E3 off: validation_status stays 400 | killed | {"probe:P0-BIND": [true, false], "probe:PI3": [true, false]} |
| JM25 | E3 on a mixed module: the first status wins | killed | {"probe:P0-BIND": [true, false]} |
| JM26 | E4 off: no choices (the Status 01.. seed) | killed | {"probe:P0-CHOICES": [true, false], "probe:PI5": [true, false]} |
| JM27 | E4 uses only the `from` states (S2: queued only) | killed | {"probe:P0-CHOICES": [true, false]} |
| JM28 | E5 off: no private columns in the profile | killed | {"probe:P0-PRIV": [true, false], "probe:PR-T2": [true, false]} |
| JM29 | E5 also takes columns with a json name (every string column) | killed | {"probe:P0-PRIV": [true, false]} |
| JM30 | E7: --rules parsed but not passed | killed | {"probe:PB1-CLI": [true, false]} |
| JM31 | gotickets copy: close drops AND reporter_id = ? | NOT RUN | {} |
| JM32 | gotickets copy: password_hash loses json:"-" | NOT RUN | {} |
| JM33 | gotickets copy: get answers 403 for another caller's ticket | NOT RUN | {} |
| JM34 | gotickets copy: reopen accepts an open ticket (no 409) | NOT RUN | {} |
| JM35 | an E1-E5 inference entry written for a Python import | killed | {"probe:PB-P": [true, false]} |


## 5. Misses, gates not met, and what they mean

Every miss is a finding. T-S2, T-S3 and every pass rule are as registered.

1. **S3 NOT RUN (10 rows, 4 mutants, S3 sub-parts of 5 Arm 0 rows).** `gotickets` is written by a separate isolated author after the freeze. The blind claims of this round (the new fixture's imports, owners, `Handoff` unclear, the `GET /handoffs` OR query, JM31-JM34) are untested. This is not a pass.
2. **JM6 survived.** Predicted: re-registered onto layer R's T2 item (`T2 User.token` refuted). Observed: the item stays `verified` on the mutant binary, no item flipped (R: `{}`), and layer A is silent as predicted (C5b). Cause (a reading of Go semantics, not measured here): removing `json:"-"` from `Token` makes `encoding/json` emit the key `Token` (the field name), and layer R's T2 item looks for the column name `token`. So the detector the pre-registration chose cannot see this mutant, and the S3 analogue (JM32, `password_hash` becoming `PasswordHash`) is likely to be invisible to `T2 User.password_hash` for the same reason; its other detector, GO802 on `GET /me`, is unaffected. Not fixed: any change after the freeze would be post hoc.
3. **PI3 verified share 0.6098 against the predicted 0.62-0.82.** The gate (>= 0.60) passes by 0.0098; the item count (41, predicted 38-44), refuted (0) and corrections (0) are inside their predictions.
4. **C3 only partly held.** Predicted: `409 already live`, `409 already final`, `R2 create` and `R2 cancel` become reachable. Observed: `R2 POST /runs/{rid}/cancel` and `T1 Run` verify, but `R2 POST /configs/{cid}/runs`, `R3 ... 409 already live` and `R3 ... 409 already final` stay unexercised ("no probe reached it" / "unreachable-by-probe: no example triggers it"). The seed does queue runs (`runs.status` choices `queued`, `cancelled`; PI5 proposes the machine). One observation, from the seed's own arithmetic (`suite.py` `_seed_value`/`seed_plan`, 4 seed users, 8 rows): status is `choices[i % 2]` and the owner is `users[i % 4]`, so each user holds a single status (admin and bob `queued`, alice and carol `cancelled`). Whether that, or the probes' example generator, keeps the 409s unreached was not isolated.
5. **Line budget:** `goextract_closure.go` 102 against 80.
6. **Prereg inconsistencies, recorded, not replaced** (`amendment2_notes.txt`): the group hash row (prereg `97c86f08...`, "18 files") differs from the 19-file value at `193f7f6` (`8e891040...`); it equals the same set without `goextract_routes.go`. PB-X's "8 of 8" counts goapp twice; the runner uses J1's 7 dirs and adds `tests/gocheck/fixtures/runs` as the 8th.
7. **PI2:** `inferences[]` has exactly the predicted 7 entries (1 `owner_uses`, 3 `path_parse`, 1 `bind_status` 422, 1 `status_facts`, 1 `json_dash`). PR-T2, PA1 (`User`, one baseline key), PA3 (JM5 `GO801|POST /runs/:rid/cancel|Run`, JM6 0 new, original 0 outside the baseline), PR3 and PI6 (0 corrections, so literal) all match their corrected predictions. PX: S2 import 9.55 s, R on S1 2.29 s, B on S1 3.09 s (gates 180/30/60); the S3 import time is NOT RUN.

## 6. Predictions that held

E1-E6 on the static fixtures (P0 rows, exact); E7 (PB1-CLI: 0 findings with `--rules`, 6 F2 findings without); S1's R and B regressions unchanged (23/16/0; 32 cases); PI4 1.0 (25 of 25 stay verified on variant2); PI7 and PR4 and PB3 identical 10 of 10; PW 0 changed files and 0 shim calls; the byte-compat promise (declared differences only: goginapp's default extract `limit ge 1 -> 0`, its policy extract keys, its layer A (JM5 now visible), its import, and the `check --help` option).

## 7. Deviations (all disclosed)

1. Closure budget over (section 5.5).
2. An extra opt-in key `params[].parse_at` (the parse site), because the param's own file/line is the route's and the prereg's evidence line needs the call.
3. E5 path: neither a `RepoModel` input nor `rules.py` after `generate_profile` was possible as worded (`generate_profile` runs inside the frozen `lift_repo`; a `DataModel` would also change `RESPONSES`). The profile source is patched after `lift_repo` by `go_rules.patch_profile` (2 lines in `rules.py`).
4. E4's values come from `status_writes` (default extract) plus the `from` states of `status_facts`; `status_facts` has no written values (the prereg says "status_facts").
5. Arm 0 rows are evaluated through `InferringFrontEnd` (end to end: the model, `validation_status`, `private`, `inferences`), not only on the raw extract, so the importer mutants (JM19, JM24-JM29) are visible to them.
6. PB-G: `inventory` hashes every file under its root, so the new fixtures beside `goextract_workflow` change its output by construction. Both trees run on a copy of the `193f7f6` fixtures; the literal in-repo difference is reported and is exactly the 4 new fixture files.
7. PB-P2 (new): the decoy `studio.toml` is placed in a directory literally named `bench.import_repos.multivendor.app` (the Go lookup joins root and the dotted package), with a `decoyapp.py` that exits; the factory must stay `bench.import_repos.multivendor:make_app`. JM11 is killed by it and by its unit test.
8. PI6/PR3: the correction map is implemented, with review's ` #k` suffix read as "k from 2 on a collision"; with 0 corrections both are literal.
9. PB-H compares option sets and descriptions (argparse re-wraps the usage line): `check --help` differs by `--rules` only.
10. PB-T uses the venv python and `-p no:cacheprovider`; the base log is the same command on the `git archive`. PB-D uses the venv python (J1 deviation 10).
11. The runner has an extra arm `X` (cross-arm rows), so a shakedown can skip the 20-minute PB-T. The shakedowns (Arm 0; S1, S2 and cross-arm without PB-T/PB-D) and a 5-mutant check ran before the freeze; their outputs are not kept except where noted. Two runner bugs were fixed before the freeze (the frozen-table row labels, PB-G's input) and one probe wrapper (PB1's range).
12. J1's 3 listed tests: only 2 needed edits (10 lines); `test_go_layers_rb.py` is unchanged.
13. The PI1 role is `Customer` (C11).

## 8. Findings carried forward (not fixed here)

F2 (default rule is FastAPI-shaped), F1, C15 (GO802 keying), F10 (` #k` ids) stay as registered. New: the T2 detector's blindness to Go's default capitalised JSON keys (JM6, and probably JM32); C3's partial miss; the PB-X count and group-hash inconsistencies. Arm M's M4 (do Streak's `Use(gin.*)` and `omitempty` bounds change under E2/E6) remains NOT RUN.

## 9. Files

`lattice/importer/go_rules.py`, `rules.py`, `lattice/gocheck/derive.py`, `lattice/gocheck/src/goextract_closure.go`, `goextract_assemble.go`, `routes.go`, `lattice/spec/__main__.py`; tests `tests/gocheck/test_go_derive2.py`, `tests/importer/test_go_rules2.py`, `tests/spec/test_go_layers2.py` and the two edited J1 tests; fixtures `tests/gocheck/fixtures/goparity2/`, `goparity2mixed/`; runners `dogfood/go_parity_probes2.py`, `dogfood/go_parity_mutants2.py`; data under `docs/results/data/2026-10-18-go-parity-gaps-round2/` (`fixtures.sha256`, `freeze.sha256`, `amendment2_notes.txt`, `frozen_193f7f6.sha256`, `pbx_default_extract_193f7f6.sha256`, `lint.txt`, `pytest/`, `run/`, `mutants/`). Not created, by order: `tests/spec/gotickets/**`, `tests/spec/gotickets_harness.py`.
