# Spike I, Arms M and L (gRPC in import and build): results of the run on the owner's machine

**Status line.** 2026-10-18, worktree `lattice-ia`, branch `np-i-arms` (cut from `next-phases` 978b468; nothing committed, staged or pushed).
**PM-S (Arm M, Streak byte-compat): PASS, 18 of 18 rows identical.** **PM-SG (Arm M, Streak reproduced as grpc-go): NOT RUN** (the app, its
truth, its harness and its scorer do not exist; section 3). **Arm L (build round on a toy proto): PASS on the second run, in 1 round, USD 0.08;
the first run is a registered-gate miss caused by a library defect plus my config** (section 4). **The round-4 held-out (PG-HO4) was not
touched** (the owner's instruction; it was never scored). No Anthropic API key anywhere; the only model use was the `claude` CLI (sonnet) in
Arm L, plus a USD 0.10 smoke call to check that `--max-turns` and `--max-budget-usd` are accepted. No Docker container was started
(`docker ps -a --filter label=lattice.owner=w-i` is empty). Data: `docs/results/data/2026-10-18-grpc-arms-m-l/`. **(m)** = measured.

## 1. Inputs, freeze and what was and was not frozen

* Freeze check before anything ran (`data/.../freeze-verify.txt`): `freeze-dev4.sha256` (5 round-4 truth files) and `freeze4.sha256`
  (`goextract_grpc.go`, `go_grpc.py`, `spec/grpc.py`, `transport.py`, `grpc_scorer4.py`, `grpc_scorer4_cases.json`, `grpc_probes4.py`,
  `grpc_contract4.py`): **13 of 13 OK, 0 differences** (m). The archived held-out tarballs (`archive-frozen-sets-2026-10-18/`) were not opened.
* **The frozen runners contain no implementation of Arm M or Arm L.** `grpc_probes.py` ... `grpc_probes4.py` each end with a `not_run` stub
  (`PM-S`, `PM-SG`, `Arm L`); there is no `--arm M` or `--arm L`, and `tests/grpc/fixtures/toybuild/` (prereg section 11) was never created. So
  both arms needed new driver files. None of them edits a frozen file (`git status`: only the new data directory). They are listed in section 6
  with their hashes. This is a deviation from "use the frozen runner files": there was no frozen runner to use.
* The prereg's wording for Arm M ("needs Docker MySQL") is wrong for PM-S: the Streak import, report, app map and impact tiers 2 and 3 are
  static; **PM-S ran with no Docker**. Only PM-SG would need MySQL.

## 2. PM-S: Streak import byte-compat against `git archive 6a318e5`

Prereg (section 4, Arm M): `lattice import` on Streak a0679e9 and 1d00ede: `extract.json`, the inventory, `build_report`, `build_appmap` and D's
impact (tiers 2 and 3) on the 6 Spike F dev candidates byte-identical to `git archive 6a318e5`; `select()` still answers `gin`. Prediction:
identical.

Method (`pms_driver.py`): Streak `git archive` of a0679e9 (S) and 1d00ede (S4) into scratch (the owner's checkout was only read with
`git archive`; `go_src` in the clone's `studio.toml` pointed at the clone, as in the earlier probes). The same clones and the same absolute
paths were used for both lattice trees, so no path normalisation was needed. Tree "base" = `git archive 6a318e5` of this repository; tree "new"
= this worktree (978b468 plus nothing). Each tree built its own `gocheck` binary (different binaries: `8c4a12ba...` vs `6ea0eb5c...`), and ran as
a separate process with `PYTHONPATH=<tree>`: `gocheck extract` (stdout sha256), the `go_gin` `RepoModel` JSON, `select()`, `lattice import
inventory` (S with the committed workflows.json af1383db..., S4 static; sha256 of every inventory file), `lattice.spec implementation --json`,
`lattice.spec appmap --json`, and `lattice.spec impact --candidate <d1..d6> --tiers 23 --baseline none --json` on S with the six dev candidates
of `data/2026-10-16-goto-place/dev-candidates/`. The base tree was run twice (determinism).

| row group | rows | prediction | observed (m) | result |
|---|---|---|---|---|
| `gocheck extract` (S, S4) | 2 | identical | identical; no `grpc` key in either extract | PASS |
| `RepoModel` JSON (S, S4) | 2 | identical | identical | PASS |
| `select()` (S, S4) | 2 | `gin` | `gin`, `gin` | PASS |
| inventory (S, S4) | 2 | identical | identical (every file) | PASS |
| `build_report` (S, S4) | 2 | identical | identical | PASS |
| `build_appmap` (S, S4) | 2 | identical | identical | PASS |
| D's impact T2+T3, d1_limit ... d6_reword | 6 | identical | identical (6 of 6; 15 kB to 95 kB each) | PASS |
| base vs base (second run) | 18 | identical | identical | PASS |

**18 of 18 rows base == new** (`armM/compare.txt`, `armM/pms-{base,base2,new}.json`). No miss. Limits of what this shows: the layers that need a
live app (impact tier 1, the workflow replay, layers A/B/C/D/X against Streak on MySQL) were not part of PM-S and were not run; the six candidates
are the Spike F text edits of Streak's spec, not new ones.

## 3. PM-SG: NOT RUN

The prereg row is "Streak reproduced as grpc-go by a separate agent against Streak's spec (MySQL): rpc recall/precision, codes, flows replayed,
ACD on its gRPC spec, as PG-HO's rules". What exists: Streak's HTTP spec and app. What does **not** exist, and what I did not make up: a grpc-go
Streak (proto, server over MySQL), a gRPC form of Streak's 24-endpoint spec (`Rpc` scenarios), a `truth.json`, a harness (`make_app`,
`build_db`, `RUNTIME` with an rpc login), and a scorer/runner arm for it (the round-4 runner scores a held-out directory with a `truth.json`
written by an author; no such directory exists for Streak). Writing them is a build task with its own protocol (a separate, blind author, a
freeze, a budget the owner has not set), not a run. Stopped, as instructed for a missing input. To run it: one author agent builds the app and
`truth.json` against `HARNESS_API.md` (after the three fixes in the handoff, section 5.3), the orchestrator freezes it, then
`grpc_probes4.py --arm H4`-style scoring with a Docker MySQL (label `lattice.owner`).

## 4. Arm L: the build round on a toy proto

Prereg (section 4, Arm L): a toy proto (1 service, 4 unary rpcs, 1 table) plus its spec with `Rpc` scenarios; `studio.toml [grpc] protos,
generate`; the rebuild loop through the `claude` CLI only, sonnet, `--max-turns 40`, **USD 5.00 cap** on the CLI's list estimate. Gates: the
proto and spec sha256 unchanged after the loop (hard); stubs produced only by the declared command; the final app passes the spec's CD check.
Prediction: within <= 3 rounds. A usage limit would make it NOT RUN or partial, never a pass.

**The fixture is mine (`toybuild/`)**, written for this run because none existed: `toy.v1.Shelf` with `AddItem`, `GetItem`, `ListItems`,
`RemoveItem`; one table `items`; 8 rules (7 scenarios of `Rpc` steps with `expect_code`, `expect_message`, `expect_db`; one invariant);
`harness.py` (ProcessApp, `ready="grpc"`, no auth); `generate.sh` (buf 1.69.0 with `protoc-gen-go` v1.36.5 and `protoc-gen-go-grpc` v1.5.1 from a
scratch `GOBIN`, writing only `gen/`, which `.gitignore` excludes); `check.sh` (generate, `go build`, `lattice.spec check --layers CD`);
`CONTEXT.md` (the harness facts the builder needs: `PORT`, `DB_PATH`, health service, the schema already exists, no network); a scaffold
`main.go` and `shelf.go` (server starts, health answers, every rpc `UNIMPLEMENTED`: baseline 7 findings, D 7, C 0); `go.mod` and `go.sum` from a
reference solution (`toybuild-reference/main.go`, never shown to the builder). Calibration before the model ran (m): the reference passes
(`PASS: 0 findings`, 15 cases); three mutants of the reference (list order reversed, quantity check loosened, `ALREADY_EXISTS` -> `UNKNOWN`)
each fail the check (3 of 3). The loop is Studio's own `Rebuilder` (`scope="all"`, `agent="claude"`, sonnet, `max_rounds=3`), started by
`armL_driver.py`; the `claude` binary is `armL_shim.py`, which only adds `--max-turns 40` and `--max-budget-usd <remaining of the cap>` and keeps
a ledger of the CLI's cost.

| id | prediction | observed (m) | result |
|---|---|---|---|
| L-run1 | passes within <= 3 rounds | **gave_up after 3 rounds, USD 0.37 (10, 3, 2 turns).** Round 1's agent made all 7 in-scope scenarios pass (`scope_passing 7/7`, check verdict counts C 0, D 0); Studio failed every round with "spec files were edited (restored from the base commit): proto" although the proto was not touched | MISS |
| L-run2 (config corrected) | passes within <= 3 rounds | **passed in round 1**, 10 turns, **USD 0.0773**, 25.6 s of agent+gate wall time, commit `8e1fc47` on `build/toybuild`, 1 file changed (`shelf.go`, +74/-1) | PASS |
| L-G1 proto and spec unchanged (hard) | identical | 13 protected files (proto, spec.py, spec.trace.json, REQUIREMENTS.md, harness.py, generate.sh, buf*.yaml, check.sh, CONTEXT.md, studio.toml, go.mod, go.sum) base vs tip: 13 of 13 same sha256; the only changed file is `shelf.go`; Studio's own integrity check: `spec_edited: []` | PASS |
| L-G2 stubs only by the declared command | yes | no `*.pb.go` and no `gen/` path in the commit; the agent's tool calls were 4 reads, 1 glob, 1 `Write` (`shelf.go`) and 3 `./check.sh`; `gen/` in the build worktree is byte-identical to a fresh `./generate.sh` of the base proto (4 files) | PASS |
| L-G3 CD check passes on the final app | passes | clean `git archive` of the tip, `./check.sh` x3: `PASS: 0 findings (C 0, D 0); 15 cases run` each, rc 0 | PASS |
| L cost | within USD 5.00 | arm total USD 0.45 (0.37 + 0.08); the smoke call USD 0.10 is outside the arm | PASS |

**Why L-run1 missed (a finding, not a model failure).** `[mcp] protected` accepts a directory in the docs (`spec_names()`: "a directory means the
files under it") and I listed `proto`. `Rebuilder._restore_protected` compares each protected path as a *file* (`git cat-file -p base:proto`
returns a tree listing, the worktree side `is_file()` is false), so a directory entry is reported as edited in every round. That is a defect
in `lattice/studio/rebuild.py` (not fixed here; it is outside this node's remit); the workaround, used in run 2, is to list the proto file.
The agent also noticed and said it could not find the edit. Run 2 changed only that one line of `studio.toml`
(`toybuild-fixture-run1.sha256` vs `toybuild-fixture.sha256`) on a fresh repository. Both runs are reported; run 1 is not hidden by run 2.

**Things this arm does not show.**
* The task is easy by construction (a scaffold that already wires the server, 4 small handlers, explicit rules, an environment note); one
  round and USD 0.08 say that the loop, the gates and the proto protection work with a `.proto`, not that the builder can build a hard gRPC
  service. One sample, one model (sonnet), no repeat.
* Prereg item 5 ("the rebuild loop's prompt and gates take the `.proto` as part of the contract; stubs produced by the project's own command,
  declared in `studio.toml`") is **not implemented in the library**: `rebuild.py` has no mention of protos, `[grpc] generate` is read by
  nothing (my `check.sh` runs `./generate.sh` itself), and the brief does not name the proto (the agent found and read it anyway; the
  rule "never the proto" holds only through `[mcp] protected`, which worked once the entry was a file).
* Layers A, R, X, B were not used (D9: not supported for rpcs); the gate is C and D.

## 5. Predicted vs observed, whole table, and the misses

| row | prediction | observed | result |
|---|---|---|---|
| PM-S | identical to `git archive 6a318e5`, `select()` = `gin` | 18 of 18 identical, `gin` | PASS |
| PM-SG | as PG-HO's rules | no app, no truth, no harness, no scorer | **NOT RUN** |
| Arm L (run 1) | <= 3 rounds | gave_up (library false positive on a protected directory) | MISS |
| Arm L (run 2) | <= 3 rounds | 1 round, USD 0.08 | PASS |
| Arm L gates (hard) | proto/spec unchanged; stubs by the command only; CD check passes | 13/13 same; no stub in the commit and `gen/` == generator output; PASS x3 | PASS |
| Docker containers | none left | none started; `docker ps -a --filter label=lattice.owner=w-i` empty | clean |

Misses: (1) L-run1, above; (2) the prereg's premise that Arm M needs Docker (it does not, for PM-S); (3) the prereg's premise that the
rebuild loop reads `[grpc] generate` and names the proto in its brief (it does not). None of these changes a round-4 number: Arms M and L never
fed the held-out score, and PG-HO4 stays NOT RUN.

## 6. Files, reproduction

Created (all new, nothing existing edited): this document and `data/2026-10-18-grpc-arms-m-l/` (`pms_driver.py`, `armL_driver.py`,
`armL_shim.py`, `driver-hashes.sha256`, `freeze-verify.txt`, `armM/`, `toybuild/` with `toybuild-fixture{,-run1}.sha256`, `toybuild-reference/`,
`armL-run1/`, `armL-run2/`; the agents' stream-json logs are under each `ledger/logs/`).

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<this worktree>
D=docs/results/data/2026-10-18-grpc-arms-m-l
# PM-S (driver venv with pymysql is not needed for the static outputs but was used): the same Streak clones for both trees
$DRV $D/pms_driver.py --tree <git archive 6a318e5> --label base --scratch $S --out $S/pms-base.json
$DRV $D/pms_driver.py --tree <this worktree>        --label new  --scratch $S --out $S/pms-new.json
# Arm L: TOY tools = protoc-gen-go v1.36.5 and protoc-gen-go-grpc v1.5.1 in $S/tools/bin; buf 1.69.0
export TOY_TOOLS_BIN=$S/tools/bin ARML_REAL=$(command -v claude) ARML_DIR=$S/armL2/ledger ARML_CAP=4.50
uv run python $D/armL_driver.py --repo $S/armL2/repo --shim $D/armL_shim.py --out $S/armL2/out.json
```
