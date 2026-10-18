# Go parity gaps (Spike J, node J1), Arm M: Streak a0679e9 on MySQL 8.4: results

Run 2026-10-18 on the owner's machine (macOS, Docker 29.4.0, go 1.26.2), in the worktree `/Users/vivekgade/Documents/lattice-ma`
(branch `np-j1-arm-m`, from `next-phases` 978b468). Nothing is committed or staged. No model was called and no API key was used.
Streak was read only: `git archive build/streak` (a0679e9, the branch tip) into the scratch directory, built there
(`go build -o bin/streak-api ./cmd/server`), and every run used that copy (`--go-src` pointed at it too). `~/Documents/streak`
(`main` a0db033 plus the owner's uncommitted `studio.toml`) has the same `git status` before and after, and so does the main lattice
checkout. Data: `docs/results/data/2026-10-18-go-parity-arm-m/`. Predictions are quoted from the round-1 pre-registration
(`2026-10-17-go-parity-gaps-prereg.md`, section 2, "Arm M") and the round-2 one (`...-round2-prereg.md`, section 3 and E2), which
are the only places Arm M is registered.

**Verdict, first.** M1 FAILED (the rules-first import cannot run Streak: it crashes at its seed step, and the skip the prediction
named does not exist). M2 MISSED on its letter (the derived policy is not equal to `from_spec`; the findings are equal, 0 and 0).
M3 PASSED with one stated qualification (identical once the timings and one container name are removed; the raw bytes are never
identical, even from run to run). M4 is answered: Streak has one `Use(gin.…)` (`gin.CustomRecovery`, in E6's list), no
`binding:`/`validate:` tags (E2 has nothing to act on), and neither E2 nor E6 changes any Streak output. A post hoc probe found that
layer A on Streak cannot see a planted ownership defect in any version (section 6). Everything the arm does not cover is in
section 8.

## 1. What was registered, what exists, and how it was run

* **Registered rows.** Round 1: **M1** the rules-first import of Streak (R is skipped on MySQL, so the import reports
  `layer R: not_run:server_dialect`); **M2** the derived `GoPolicy` versus `from_spec` on Streak's spec (predicted equal, "since
  the spec declares its 8 models"); **M3** `lattice check --layers ACD` on Streak byte-identical to base. Round 2 adds **M4**: whether
  Streak uses explicit `Use(gin.…)` and `omitempty` bounds, i.e. how far E2 and E6 change its outputs (round-2 E2: "Streak is
  unknown (arm M)").
* **Labels.** The task text that dispatched this node calls M2 "layer-A byte-compat" and M3/M4 "whether E2/E6 change Streak's
  outputs". I kept the registered definitions above and covered the other reading inside them (M2 includes the layer-A findings
  under both policies; M3 and M4 compare E1-E6 outputs across versions).
* **No Arm M runner exists.** `dogfood/go_parity_probes.py --arm M` only prints "NOT RUN here" (line 1121), and `go_parity_probes2.py`
  and `go_parity_s3.py` have no M code. So the rows were run by hand with new scripts (`data/.../scripts/`), calling the library's
  public entry points. No frozen file was edited, and none was needed.
* **Freeze check (before any run).** `freeze.sha256` (21 entries), `s3runner.sha256`, `fixtures.sha256`: all OK. `s3.freeze.sha256`:
  8 of 9 OK (the ninth entry is the author's `FILES.txt`, which was never shipped into the tree; Amendment 4 already notes it).
  `frozen_193f7f6.sha256`: all OK except `goextract_grpc.go` (and the group hash that contains it), which Spike I round 4 owns and
  rewrote after 193f7f6 (749 lines). Output: `freeze/verify.txt` (76 OK lines). Not relevant to a non-gRPC app, and the extract of
  Streak is byte-identical across all four versions (M4).
* **Versions compared** (the library only, from `git archive <commit> lattice`, same Streak copy, same harness):

  | tag | commit | meaning |
  |---|---|---|
  | v0 | 5125da1 | J1 round-1 base ("base" of M3) |
  | v1 | 193f7f6 | J1 built (round-2 base) |
  | v2 | 39676aa | J2 built (E1-E7) |
  | v3 | 978b468 (worktree) | `next-phases`, everything merged |

* **Command (M3, mirrors `check.sh` minus B and X, which are not registered).** `python -m lattice.spec check --spec spec:SPEC
  --app harness:make_app --db harness:build_db --runtime harness:RUNTIME --login harness:login --services harness:SERVICES
  --database mysql --layers ACD --go-src <copy> --x-repeats 5 --json`, run with the driver venv python (pymysql), `PYTHONPATH` =
  version tree + Streak copy, `LATTICE_CONTAINER_OWNER=w-j1m`. Each run started its own `lattice-mysql-<hex>` container (image
  `mysql:8.4`, local, random loopback port, labels `lattice.spec=throwaway` and `lattice.owner=w-j1m`), 6 containers in all, each removed by
  the checker. After the last run `docker ps -a` shows no container with the label or a `lattice-` name.

## 2. Predicted versus observed

| row | predicted (as registered) | observed | result |
|---|---|---|---|
| M1 | rules-first import of Streak runs; reports `layer R: not_run:server_dialect` | `python -m lattice.importer <copy> --package . --rules-only --framework gin` exits 1 at v1, v2 and v3 with `RuntimeError: Streak runs on MySQL only ... got '/var/folders/.../seed.db'`, raised from `harness.make_app` while the importer's `Seeder` writes its SQLite seed (`rules.py:_schema` -> `transport.serve`). No `report.json`, `candidate_spec.py` or `proposals.json` is written. `--dialect mysql` does not change it. The string `not_run:server_dialect` appears nowhere under `lattice/` | **FAIL** |
| M2 | derived `GoPolicy` equal to `from_spec` on Streak's spec ("8 models") | **Not equal.** The spec declares 9 tables (it names `TestClock`; the app has 10 structs: 8 models plus `TestClockRow` and `SchemaMigration`). `go_spec_policy` (derived + spec overlay) equals `from_spec` in owners, owner_via, private, machines, admin, open routes, admin role, helper depth, and differs only in `tables` (adds `TestClockRow`, `SchemaMigration`). The raw `derive_go_policy` also lacks the 3 `owner_via` entries (HabitTarget, Completion, Nudge via Habit) and the Nudge `delivery` machine. **Layer A findings: 0 under `from_spec`, 0 under the overlay, 0 under the raw derivation** (empty baseline); v1, v2, v3 give the same JSON | **MISS** (letter); no effect on findings |
| M3 | `lattice check --layers ACD` byte-identical to base | exit 0, `pass`, 0 findings (A 0, C 0, D 0), 480 cases, 26-29 s, at v0, v1, v2, v3. Raw bytes are **never** identical, not even v0 against v0 (timings, plus the container name and the clone/seed durations in one note). With `timings` dropped and those two notes normalised, all 6 runs (v0 x2, v1, v2, v3 x2) have the same sha256 `351c3104c613c54a`; 0 diffs | **PASS**, qualified |
| M4a | (round 2) Streak's use of `Use(gin.…)` | one: `gin.New()` then `r.Use(gin.CustomRecovery(...))` at `internal/httpapi/router.go:39-40`. Groups `v1` and `Member` use Streak's own `ClientVersion`, `authenticate`, `RateLimited`. No `gin.Default`, `gin.Logger` or `gin.Recovery` | recorded; `CustomRecovery` is in E6's list |
| M4b | (round 2) Streak's `omitempty` bounds | 0 `binding:` or `validate:` tags in Streak (it validates by hand). `omitempty` occurs in 3 `json:` tags only (`completions/routes.go:38,39`, `httpapi/errors.go:22`) | E2 has nothing to act on |
| M4c | (round 2) how far E2 and E6 change Streak's outputs | **Not at all, at the level of every output measured.** Default extract bytes identical at v0..v3 (sha256 `5b473b1e3be0c440`, 128,523 bytes); policy extract identical at v1, v2, v3 (`eb7b1b646fa15770`, so E1's `parse` and E3's `bind` keys are absent); workflow extract identical (`e577ea625b5cfb51`); layer A report identical at v0..v3; ACD identical (M3). Inside the checker, E6 does flip routes: of 26 routes, `GET /health` and `PUT /test/clock` (the two registered on the root engine, behind the `Use`) are `Unreadable` at v1 and readable at v2/v3; neither touches an owned table, so the findings do not move | answered |

## 3. M1 in detail

* The registered command crashed before the report stage at every version that has a Go rules-first import (v1, v2, v3). v0
  (5125da1) predates the Go branch of `import_rules`; it was not run. Logs: `m1/v1.log`, `v2.log`, `v3.log`, `v3-dialect.log`.
* Cause (a reading of the code, confirmed by the traceback): the importer builds its seed on SQLite with its own `Seeder`
  (`rules.py:267-385`), then hands that path to the harness `make_app`. A MySQL-only harness refuses a path. The importer CLI has no
  database option (`--dialect` only labels the model), so there is no argument that fixes it. The "R is skipped on MySQL" in the
  pre-registration is the server-dialect skip of `lattice check` (`check.py:69-71`), which the importer does not use. The
  prediction described behaviour that was never implemented. I stopped this row there, as instructed (a missing input is not
  worked around; the import path would have to learn a server database, a library change outside this node).
* **Reconnaissance, not the registered command and not a pass.** Up to the seed step the import's pieces run on Streak
  (`scripts/m1_recon.py`, which repeats `import_rules` through `candidate_spec` with the harness factory and the Seeder's credentials,
  without starting the app): at v3 (and the same at v2) the front end extracts 26 routes and 10 tables, dialect `mysql`, auth users
  table `User`, 26 candidate contracts, 0 programs, 0 scenarios, 2 excluded routes (kind `framework`), and 10 inferences: 4
  `owner_uses` (NotificationPrefs, Device, RefreshToken, Habit, each `user_id`), 4 `status_facts`, 2 `json_dash`
  (`User.hashed_password`, `RefreshToken.token_hash`). No `path_parse` and no `bind_status` inference. The three child tables with
  ownership through `habit_id` (HabitTarget, Completion, Nudge) get no owner, as in M2. Data: `m1/recon-v3.json`, `recon-v2.json`
  (v1 has no `patch_profile`, `recon-v1.err`).
* Two things that would have broken a real import even if the seed step worked, both post hoc observations: the Seeder's users are
  `alice`, `bob`, `carol`, `admin`, not Streak's seeded `ann` and `ben`; and E4 turns constants written to ordinary string columns
  into `choices` (`NotificationPrefs.quiet_start` -> `["22:00"]`, `quiet_end` -> `["08:00"]`, `nudge_time` -> `["20:00"]`), which is
  right for `Nudge.delivery` (`failed, pending, sent, suppressed`) but is not a state column for the three time-of-day ones.

## 4. M2 in detail

`scripts/m2.py` loads `spec:SPEC` from the Streak copy and compares three policies, each with `run_gocheck(copy, policy, strict=True)`:
`GoPolicy.from_spec(SPEC)` (what `lattice check` uses, Ruling Q3), `derive_go_policy(copy)` (derived, baseline included) and
`go_spec_policy(SPEC, copy)` (derived laid under the spec, empty baseline). Data: `m2/m2-v1.json`, `m2-v2.json`, `m2-v3.json`.

* v1, v2 and v3 give the same comparison (the JSON differs only in the library path, and in the print order of one frozenset in a
  debug string).
* `derive_go_policy` baseline: empty (`[]`), so the raw derivation also has nothing to accept on Streak.
* Whether the 2 extra tables matter: they have no owner, no private column and no machine, so no check keys on them. Findings are
  identical, empty.
* What the overlay is doing on Streak: the derivation gets owners and private columns right (4 owners, 2 private columns, exactly the
  spec's), and cannot get `owner_via` or the machine (they are not facts the code states). The spec supplies them. So the equality
  of the findings holds because the spec declares them; a Streak without those declarations would lose the three child-table
  ownership checks.

## 5. M3 in detail

Runs (`m3/acd-*.json`, `*.rc`, `summary.json`; the layer-A-only runs are `a-v*.json`, 0 to 1 s, no container):

| run | exit | verdict | counts | cases | wall | raw sha256 (16) | normalised sha256 |
|---|---|---|---|---|---|---|---|
| v0 run 1 | 0 | pass | A 0, C 0, D 0 | 480 | 28 s | 19a819016bdd854b | 351c3104c613c54a |
| v0 run 2 | 0 | pass | same | 480 | 29 s | 05d588c6aa2ff6c1 | 351c3104c613c54a |
| v1 | 0 | pass | same | 480 | 30 s | b31b9a5ae5fe0a58 | 351c3104c613c54a |
| v2 | 0 | pass | same | 480 | 28 s | 1c5afe2cb5ac2e2e | 351c3104c613c54a |
| v3 run 1 | 0 | pass | same | 480 | 30 s | c703d246badde593 | 351c3104c613c54a |
| v3 run 2 | 0 | pass | same | 480 | 29 s | 904e186773a5669c | 351c3104c613c54a |

The normaliser (`scripts/compare_m3.py`) removes the `timings` object and, in the two `notes`, the container name and the
durations. Nothing else differs (a structural diff of the parsed JSON gives 0). The v0 against v0 control matters: the registered
word "byte-identical" cannot hold for raw bytes even on one version, so the claim that holds is the normalised one, and it holds
across all four versions. Layer A alone (`layers=A`) is also identical across the four (`624dc3cb48ab114e`, 0 findings). The 480
cases (about 23 s of the wall time) and 157 database clones are the D layer; C found nothing.

## 6. M4 in detail, and a post hoc probe (unregistered)

* Source facts: `grep` of the Streak copy for `gin.Default|gin.New|.Use(|gin.Recovery|gin.Logger|binding:|validate:|omitempty`
  (non-test Go): only `router.go:39-40` and the three `json:` tags above.
* Extract equality across versions: `m4/v*/default.json`, `policy.json`, `workflow.json` (v0 ignores the `policy` flag, so its
  `policy.json` equals its default one).
* Route readability under E6 (`m4/route-dump/`): I built two copies of `lattice/gocheck/src` in scratch (v1's and v3's, each with a
  stderr dump of the checker's route list added at the top of `checkOwnership`; the library tree is untouched) and ran the Streak
  policy payload through them. v1: 26 routes, 2 `unreadable=true`; v3: 26 routes, 0. The two are `GET /health` and `PUT /test/clock`.
  Every other route's handler shows as `Handle.func1`, the closure inside Streak's `Handle(...)` wrapper (see the next paragraph).
* **Post hoc probe, not a registered row.** To see whether E6 buys Streak any detection, I planted a defect in a scratch copy
  (`streak-mut`): `GET /v1/habits/{id}` loads the habit through a new helper that does not filter by `user_id`
  (`db.AnyHabit(tx, id)` instead of `db.OwnedHabit(tx, userID, id)`; the copy builds). Layer A finds **nothing at v0, v1, v2 and
  v3** (`m4/route-dump/out-v1-streak-mut.json`, `out-v3-streak-mut.json`, 0 findings). So E6 does not make the owner check reach
  Streak's handlers. The likely cause (a reading, not measured here): every route's handler root is `Handle.func1`, so the checker
  does not follow into the real handler `h.get`, and the reach is incomplete, which makes it skip the route. Streak's 0 layer-A
  findings are therefore weak evidence of ownership correctness: the same 0 appears with a planted violation. This is a finding
  for the next Go round, not a defect of this arm.

## 7. Anything that went wrong, and deviations

1. **No runner for Arm M**; ran by hand with new scripts (see section 1). The scripts and the command lines are in the data
   directory so any row can be repeated.
2. **M1 not completed** (section 3). The `Seeder` limitation is a library gap, not a runner bug.
3. The `base` of "byte-identical to base" is ambiguous across the two pre-registrations (5125da1 in round 1, 193f7f6 in round 2);
   I report both and the two later ones.
4. The first attempt at a v0-to-v3 shell loop clobbered a variable (`set --` in zsh) and archived nothing; redone with explicit
   pairs before any data was taken. No result depends on it.
5. The gocheck binaries for the four versions were built into `~/.cache/lattice/gocheck/<hash>/` (the library's own cache,
   outside the repo and outside Streak). Four extra build directories there; nothing else was written outside the scratch directory
   and the data directory.
6. Layer A in the three older trees ran with the same Streak copy and the same `harness.py` (v0's `lattice.spec` imports fine
   with it).

## 8. NOT RUN, and what this does not show

* **M1 proper** (a rules-first import of Streak producing a report): blocked, section 3.
* **Layers B, R and X on Streak**: not part of M1-M4. `check.sh` runs ABCDX; I ran ACD as registered. R is skipped on server
  dialects by `check.py` and B was not run.
* **The import on a server database** has no implementation; nothing here exercised one.
* **Spike E, D, H and I Streak arms** belong to other nodes.
* **A real change to the Streak app** was not made (no write into Streak, as ordered); the only mutation is the scratch
  `streak-mut` copy used by the post hoc probe.
* **No LLM, no API key, no `claude` call** (the importer was run with `--rules-only`).

## 9. Files

Results: this file. Data, all under `docs/results/data/2026-10-18-go-parity-arm-m/`: `freeze/verify.txt`; `m1/` (logs, recon);
`m2/` (three policy comparisons); `m3/` (6 ACD reports, 4 layer-A reports, rc files, `summary.json`); `m4/` (extracts for
v0..v3, `route-dump/`); `scripts/` (`env.sh`, `runcheck.sh`, `m1_recon.py`, `m2.py`, `m4.py`, `m4x.py`, `cmp.py`, `compare_m3.py`).
