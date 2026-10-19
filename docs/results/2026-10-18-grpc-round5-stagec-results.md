# gRPC round 5, Stage C (completion node; development data, nothing blind)

Pre-registration `2026-10-18-grpc-round5-prereg.md` with Amendments 1-2, Stage B results `2026-10-18-grpc-round5-stageb-results.md`. Branch `np-grpc5-b`
at 235c26c (Stage A, S0, Stage B committed). This node edited only the three declared test expectations and wrote this document and data
under `docs/results/data/2026-10-18-grpc-round5/{armL,stagec}/`. No library, Stage A or Stage B file was touched; no self-hash check was disabled;
nothing was committed. Model use: one real Arm L run through the `claude` CLI (sonnet), USD 0.1195 on the CLI estimate (cap 5.00). No API key.

**Verdict up front:** Arm L passes (1 round, USD 0.12). The MySQL halves of P5-HAPI and P5-REH run and pass. REG is the same failing set on a
clean sequential pair (2 failed + 29 driver errors on both sides) once the three Amendment-2 expectations are updated. PF5-LEAK passes (0).
**PR4-ALL FAILS: one round-4 PASS row, P4-BC, no longer passes** (arm 0 10/11, arm G 7/7). Nothing is NOT RUN. P5-BUDGET still stands failed
(Stage B); the round cannot pass in full.

## 1. The three declared test expectations (Amendment 2)

`tests/grpc/test_extract_notes.py::test_extract_matches_the_truth` and `::test_model_lists_rpcs_as_routes_and_bindings_on_the_rpc`,
`tests/grpc/test_proto_reader.py::test_validation_bounds_are_exact`. The only change is the interceptor code `UNAUTHENTICATED` on the notes app N.

* `truth.json` is **not** edited (its sha256 `debbd0b4...` is asserted by `test_truth_file_is_the_frozen_one`, which still passes; `sha256sum` re-checked).
  The expectation moves into the tests: a named constant `INTERCEPTOR_CODES` (8 rpcs of `notes.v1.Notes`, each `UNAUTHENTICATED`) and a helper
  `with_interceptor_codes(truth)` in `test_extract_notes.py`, which `test_proto_reader.py` imports; the two truth comparisons call the helper; the
  RepoModel test gains `("UNAUTHENTICATED", "grpc_status")` in CreateNote's expected raises.
* Proof that nothing else moved: `git diff` of the two files (+31 / -2 lines: the constant, the helper, three changed assertion lines, one import) and
  `data/.../stagec/truth-overlay-proof.txt`, a structural diff of `truth.json` against the overlaid truth: **8 differing leaves**, all
  `rpcs/notes.v1.Notes/{ArchiveNote,CreateNote,DeleteNote,GetNote,ListNotes,PublishNote,ShareNote,UpdateNote}/codes`, each `added: [UNAUTHENTICATED]`,
  `removed: []`. The eight differences the extractor produced before the edit are exactly these eight (the first failure message listed them).
* The three tests: 3 failed before, 16 of 16 pass in the two files after.

## 2. REG, six test directories, base archive (978b468) vs worktree

`pytest tests/spec tests/importer tests/gocheck tests/mcp tests/studio tests/grpc -q -p no:cacheprovider --continue-on-collection-errors`, base in
`git archive 978b468` (scratch), separate Go caches, **run sequentially** (clean pair).

| | summary | failing ids |
|---|---|---|
| before (base archive) | 2 failed, 3514 passed, 5 skipped, 29 errors | 31 |
| after (worktree) | 2 failed, 3675 passed, 5 skipped, 29 errors | 31, identical set (161 new tests, all passing) |

The 2 failed: `tests/spec/test_multidb.py::test_a_check_starts_and_removes_its_own_container`, `tests/importer/test_outside.py::test_the_template_reconciles_with_one_explained_disagreement`;
the 29 errors: 17 in `tests/spec/test_db.py` and 12 in `tests/spec/test_multidb.py` (driver modules not importable in this environment). The repo's own
REG probe (`p5_reg`) on the two logs: **PASS** (`stagec/reg-row.json`). Stage B's after-run had 5 failed (these two plus the three updated tests): now 2.

Disclosure: a first pair, run in parallel to save time, differed by three tests (`tests/spec/test_probe.py::test_temp_dirs_are_cleaned_up`,
`::test_jobs_greater_than_one_still_cleans_up_and_keeps_rule_order` failed only in the base run, `tests/studio/test_projects.py::test_prefix_routing_and_the_bare_default`
only in the worktree run). All three pass in both trees when run alone (`pytest-run1-parallel/`); they are load or shared-temp-directory flakes. The sequential pair above
is the measurement; the parallel logs are kept. An earlier attempt of that parallel pair was also lost when its processes were killed from outside (logs discarded).

## 3. Rows that were NOT RUN in Stage B

Driver environment: the perfvenv python (pymysql importable; lattice's own dependencies importable from it), `PYTHONPATH` the worktree, Docker `mysql:8.4` through
Lattice's dbserver with `LATTICE_CONTAINER_OWNER=w-gr`. `data/.../stagec/{runrow,runpr4,runreg}.py` are the three small callers I used to write full row JSON
(the runner's `--check` output truncates at 400 characters).

| id | predicted | observed | verdict |
|---|---|---|---|
| P5-HAPI (MySQL half) | 3/3 named; both fixtures all ok | sqlite fixture 14 lines all ok; 3/3 failure classes named; **mysql fixture exit 0, 15 lines all ok** | **PASS** (`p5-hapi.json`) |
| P5-REH | every row valued or NOT SUPPORTED, 0 crashes | 3 targets, 0 crashes, 0 missing rows. toolshed as delivered (`--arm H5`): RPC, SAFE, CODES, AUTH, TABLES, REC, DET, NW PASS, SQLT REPORTED, **FLOWS FAIL** (dev data, post hoc: 10/10 truth flows replay, 7 recovered all failed with `INVALID_ARGUMENT`, edge recall 0/35); harness5/sqlite (`H5`): all PASS; **harness5/mysql (`SG`, Docker)**: RPC, SAFE, CODES, AUTH, DET, NW PASS, **TABLES FAIL** (recall 0.0, `tables_source` go: MySQL has no D5.2 reader, the predicted bimodal outcome), REC and FLOWS NOT SUPPORTED as registered, CD and SQLT REPORTED | **PASS** (the row's rule is no crash; the FAIL values are reported, not gating here) |
| PR4-ALL | every round-4 PASS row PASS or declared | arm 0: **10/11, P4-BC regressed**; arm G: 7/7 | **FAIL** (see below) |
| PF5-LEAK (re-measure) | 0 | 0 live processes or bridges after reconcile and flows and after a forced startup failure; reconcile raised `ProcessStartError`; 0 stray processes; 0 containers labelled `lattice.owner=w-gr` | **PASS** (`pf5-leak.json`) |

PF5-LEAK disclosure: my first measurement from an inline shell command FAILED with one "stray" process, which was the tool's own `zsh -c` wrapper whose command
line contains a needle. Re-run from a script file (so no wrapper line contains a needle), no process exists and the row passes. The earlier Stage B stray `notesapp`
binary is gone (`ps` shows no `sessionsapp`, `notesapp`, `grpcbridge` or `toy-server`).

### PR4-ALL in detail (a miss)

Round 4's saved outputs under `docs/results/data/2026-10-18-grpc-round4/` are flat files and would only be compared with themselves, so I ran round 4's runner,
`dogfood/grpc_probes4.py --arm 0` and `--arm G`, **un-nested at round-5 code**, with its own `BASE` archive (`git archive be26a94`) and the dev corpora
(`heldout/deskd`, `heldout2`, `heldout3` of the frozen-set archives), into `stagec/pr4all/{0,G}/`, and gave that directory to `--pr4all-from`.
The runner itself is byte-unmodified. Arm G crashed unmodified at PF4-LEAK on macOS (round 3's stray reader opens `/proc`: `FileNotFoundError`), so arm G ran through
`stagec/pr4all/grpc_probes4_macos_wrap.py`, which imports and runs the same file after replacing `_stray3` by round 5's `ps`-based `stray5` (the same shim `pf5_leak` applies).

* Arm G: 7 of 7 round-4 PASS rows still PASS (PR4-N exact, PR4-DESK 1.0/1.0, PR4-SESS, PF4-N 17/17 verified, PF4-DESK 41 recovered/20 verified, PF4-LEAK, PX4).
* Arm 0: P4-WIRE, AUTHI, CODES, TABLES, LOGIN, DET, NW, UNIT (91), SCORE, PROC still PASS. **P4-BC FAILS**: AST `grpc.py: reconcile_with`, `grpc.py: run_rpc_flows`,
  `go_grpc.py: rpc_routes` (the accepted H1b change and its caller), HTTP output `cli:spec check --help`, and the extract plus RepoModel on all five dev targets
  (notes, sessions, deskd, kilnworks, harborline).
* Attribution, no hiding: against 978b468 (the state after round 4), the same runner's HTTP comparison is **identical (73/73)**, so the `--help` difference comes from
  commits between be26a94 and 978b468, not from round 5; the AST rule fails there too (the three round-5 sites), and the extract still differs.
  `pr4all-extract-diff-vs-978b468.txt` lists what differs: **only codes** on notes, sessions and deskd (code lists grew by one or more entries per rpc, E3b/E3d; Amendment 2 declares N only,
  so sessions and deskd are undeclared); codes, tables, writes and unread items on kilnworks and harborline; codes, tables, writes, state, auth and unread on toolshed (E1, E2, E3, E4, E5; declared post hoc in Amendment 2).
* Prediction was "all"; observed 17 of 18. The row measures what it was registered to measure, and one row fails, mostly because P4-BC is defined as "identical to its own base",
  which no later round that changes the extractor can satisfy. It is reported as FAIL, not as declared, because Amendment 1 lists no PR4-ALL declarations.

## 4. Arm L (the real run, once)

`dogfood/grpc_probes5.py --arm L --fixture tests/grpc/fixtures/toybuild5 --cap-usd 5 --out docs/results/data/2026-10-18-grpc-round5/armL` (scratch `--scratch` outside the
repo). The `claude` CLI, model `sonnet`, shim adds `--max-turns 40` and `--max-budget-usd <remaining>`. Frozen files ok before and after.

| id | predicted | observed | verdict |
|---|---|---|---|
| L5-CAL | reference PASS, 3/3 mutants fail | reference gate exit 0; list order reversed, quantity check loosened, ALREADY_EXISTS to UNKNOWN each exit 1 | PASS |
| L5-RUN | passes in 1 round (P 0.8), <= 3 (P 0.95), USD 0.05-0.40 | **passed in 1 round**, 1 CLI call, 10 turns, 28 s, USD 0.1195 | PASS (all three predictions hit) |
| L5-NOFALSE | 0 false `spec_edited` reports | 0 in the round; protected paths unchanged | PASS |
| L5-G1 | protected files and proto tree sha256 identical base vs tip | generate.sh, buf.yaml, buf.gen.yaml, harness.py, CONTEXT.md, proto/ all identical | PASS |
| L5-G2 | no generated file in the commit; a generate run before each gate | 0 generated files committed; 1 loop log line naming the generate run in 1 round | PASS |
| L5-G3 | clean `git archive` of the tip, `./generate.sh && ./check.sh` x3 | exit codes 0, 0, 0 | PASS |
| L5-COST | <= USD 5.00 | USD 0.1195 | PASS |

No usage limit was met. The ledger (`armL/`, `probes5-arml.json`) is the only saved output; the scratch repo and worktree are not copied.

## 5. Predicted against observed, and misses

| row | predicted | observed |
|---|---|---|
| three Amendment-2 expectations | only interceptor codes change | confirmed (8 leaves, one code), hit |
| REG | same set (new tests extra) | hit on the clean pair; the parallel pair missed by three load flakes |
| P5-HAPI MySQL half | exact | hit |
| P5-REH | 0 crashes | hit; toolshed HO5-FLOWS and harness5/mysql SG-TABLES values are FAIL (reported, see above) |
| PR4-ALL | all | **MISS** (P4-BC; 17 of 18) |
| PF5-LEAK | 0 | hit (after a measurement artifact in the first attempt) |
| Arm L | 1 round, USD 0.05-0.40 | hit: 1 round, USD 0.12 |

## 6. Containers, cost, files

* Containers: every MySQL container started by P5-HAPI and P5-REH was removed by the library's own teardown; `docker ps -a --filter label=lattice.owner=w-gr` is empty
  at the end, no `lattice-*` named container remains. Other users' compose containers on this machine were not touched.
* Cost: USD 0.1195 (Arm L, CLI estimate). No other model call.
* Changed: `tests/grpc/test_extract_notes.py`, `tests/grpc/test_proto_reader.py`. Created: this file; `docs/results/data/2026-10-18-grpc-round5/armL/probes5-arml.json`;
  `docs/results/data/2026-10-18-grpc-round5/stagec/` (listed in the final report). ruff check and format pass on every changed or created `.py`.
