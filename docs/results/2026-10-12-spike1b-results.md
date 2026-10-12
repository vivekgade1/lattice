# Spike 1b results: security attack module

Date: 2026-10-12. Branch `np-spike1b` (worktree `lattice-np1b`, from `next-phases` fdb5cc6).
Pre-registration: `docs/results/2026-10-12-spike1b-prereg.md` (written before any probe ran).
Raw data: `docs/results/data/2026-10-12-spike1b/`.

## What was built

A separate package `lattice/security/` (NOT part of `lattice check`, never an A-X layer or a
rebuild gate; a finding never edits spec or code). It starts the app itself on a loopback
throwaway database, generates attacks deterministically from the spec model, runs them through
its own httpx transport (so the DSL's header/carried-value gap does not apply here), and a
judge replays every claimed finding on a fresh database and seed, keeping only those that
reproduce. CLI: `python -m lattice.security plan|run|report --project DIR [--scope ...]
[--classes ...] [--json] [--out DIR]` and `lattice attack ...`.

### Files changed (line counts)

| File | Lines | What |
|---|---:|---|
| `lattice/security/__init__.py` | 24 | package doc + banner ("owner's own systems only") |
| `lattice/security/schema.py` | 213 | Attack / AttackRequest / Confirm / Finding / Report; severity table; blind-spots |
| `lattice/security/target.py` | 257 | guardrailed target: loopback-only start via the harness; seed hook; DB reads for oracles |
| `lattice/security/plan.py` | 496 | deterministic generator (authz, auth, bounds, mass, state, leak) |
| `lattice/security/run.py` | 247 | runner (bounded pool) + judge (fresh-seed replay) + given-seeding + dedupe |
| `lattice/security/report.py` | 65 | text table + JSON schema |
| `lattice/security/agent.py` | 169 | optional LLM attacker (one `claude -p`, sonnet, curl-only) |
| `lattice/security/__main__.py` | 190 | CLI (plan/run/report), `--url` refusal, exit codes 0/1/2 |
| `lattice/security/module/` | 30 (2 files) | `lattice-module.toml` (kind=custom, provides=["attack"], entry) + README |
| `lattice/cli.py` | +8 | `lattice attack` dispatch + usage |
| `lattice/modules/__init__.py` | +12 | optional `entry` manifest field for a `custom` module |
| `tests/spec/minishop.py` | +224 | additive: contracts, access cells, `SEC_BROKEN` per-class holes, `attack_spec()` |
| `tests/security/*` | 411 (7 files) | conftest + generator, judge, guardrails, report, CLI, module-install tests |
| `dogfood/security_mutants.py` | 182 | 5 mutants |

### The generator (deterministic, from the spec)

Endpoint model: scenarios are the source of concrete requests -- a step is
`(method, path, caller, json, expect_status)`, and the access matrix emits one per
(caller, action) cell, so a caller served 2xx is the owner and the others' cells are the
refusals the spec promises. Contracts and the profile enrich each endpoint (matched by method
+ path template) with parameter bounds, the owned table, its columns and its state machine.

- **authz**: for every (endpoint, non-owner caller incl. anonymous) whose spec cell is a
  refusal, replay the owner's own request as that caller; a 2xx is a finding. A caller that is
  ever served 2xx on an endpoint is treated as an owner there and never attacked as a
  non-owner (this removed false "ann attacks her own habit" cells and keeps the clean app
  quiet).
- **auth**: endpoints the spec refuses to anonymous: resend with no token, a malformed bearer,
  an empty bearer, and a wrong scheme; a 2xx is a finding. Public routes (anon served) are
  skipped.
- **bounds**: each stated contract-parameter bound -- just-over `le`, just-under `ge`,
  over-max-len, empty, enum violation, wrong type, injection metacharacters; a value accepted
  (2xx) or a 5xx is a finding. Without contracts (Streak), bounds falls back to the profile's
  enum violations (documented limit).
- **mass**: each table column the request model does not allow (owner id, id, role,
  timestamps, version, price, ...), injected one at a time; a stored injected value (a DB read)
  is a finding.
- **state**: a terminal action repeated (double cancel, double delete); a 2xx on the repeat is
  a finding.
- **leak**: a malformed body to provoke an error, then scan every response body for a stack
  trace, SQL, a file path, or a driver name.

### Guardrails (plan decision 2), each with a test

1. The only target is the app this module starts itself on a loopback host (`127.0.0.1`) or
   in-process; `ensure_safe_target` refuses any other host, and `target_from_url` always
   refuses -- there is no `--url` and no host argument. CLI `--url`/`--host`/`--target` ->
   exit 2.
2. The optional LLM attacker's tools are `Bash(curl http://127.0.0.1:<port>/*)` and `Read`
   only; it is never run by default.
3. CLI backend only; no Anthropic API key is read or passed.
4. Reports and logs are written only under the run's `--out` directory (a printed temp dir by
   default).
5. The banner and `--help` say "FOR THE OWNER'S OWN SYSTEMS ONLY".
6. Every report (text and JSON) states what a spec-driven attack cannot see (CVEs, TLS,
   deployment config, repo secrets, timing, races).

## Verification

- Security tests: `tests/security` -- **39 passed** (ruff check clean; `ruff format --check`
  clean). `test_run.py` ran 3x stably.
- Full suite `tests/security tests/modules tests/perf tests/studio tests/mcp tests/test_cli.py
  tests/spec` (the pre-registered baseline command, DB tests ignored): **1236 passed, 5
  skipped, 0 failed** in 380s (baseline was 1197 passed, 5 skipped; the delta is the new
  `tests/security`). Intermediate runs surfaced and fixed: a `test_cli`/`spec` module-name
  collision (security CLI test renamed `test_security_cli.py`), a `sys.modules['spec']` leak
  across test modules (`load_spec` forces a fresh `spec` import + an autouse fixture restores
  import state), and a flaky mass-detection test under the concurrent shared-DB pool
  (detection tests now run phase 1 with `workers=1`).
- `dogfood/studio/check.sh`: **exit 0 (PASS)**, 47 D scenarios, 0 findings, 49s. No Studio code
  was changed in this spike. (The brief's "393 cases" baseline is a different count; this
  black-box gate reports 47 scenarios and exits clean.)
- `ruff check` and `ruff format --check` on all changed/created files: clean.
- Mutants (`dogfood/security_mutants.py`): control PASS (39 passed); all 5 mutants CAUGHT.

| Mutant | Behaviour | Predicted catcher | Actual catcher |
|---|---|---|---|
| SM1 | judge confirms without replay | test_run (judge rejects / clean quiet) | `test_each_seeded_hole_is_confirmed[mass]` |
| SM2 | non-loopback target allowed | test_guardrails (non-loopback refused) | `test_non_loopback_host_is_refused` (x5) |
| SM3 | authz skips the anon caller | test_plan (authz covers anon) | `test_authz_covers_every_non_owner_refusal_cell` |
| SM4 | bounds off by one (at the limit) | test_plan (not off by one) | `test_bounds_is_not_off_by_one` |
| SM5 | a confirmed finding exits 0 | test_cli (holed exits 1) | `test_run_holed_app_exits_1` |

SM1's actual catcher differed from the prediction: with the judge forced to "confirm", a
single cleanly-refuted attack in phase 1 never becomes a candidate, so the targeted
`test_judge_rejects...` still passed; the mutant was caught instead by a seeded-hole test
(cross-talk candidates confirmed as the wrong class). A failed prediction, reported not fixed.

## Probes

### In-repo swarm (controllable target: minishop, all 6 classes)

`tests/spec/minishop.py` with a holed variant per class (`SEC_BROKEN`). All classes x all
variants + clean x3 (`docs/results/data/2026-10-12-spike1b/minishop_swarm.json`):

- **recall 6/6** seeded holes confirmed (authz, auth, bounds, mass, state, leak);
- **precision 1.0** (clean app quiet, one hole each, every confirmed finding a consequence of
  that hole);
- **clean app: 0 confirmed findings in 3 of 3 runs.**

This is the deterministic, fully reproducible evidence; the Streak probes below are the
pre-registered real-app probes.

### S1 -- seeded hole, Streak clone, deterministic authz attacker (PASS)

Target: a clone of Streak at `a0679e9` (Go+Gin+GORM on a throwaway MySQL container
`lattice-sec-*`, served by the process runtime on `127.0.0.1:<port>`). Seeded hole: the
ownership scope removed from `GET /v1/habits/{id}` only (a separately-built binary), so another
user reads a habit by id. `lattice attack run --classes authz` (42 authz attacks planned from
Streak's access matrix). Raw: `data/.../s1_s3_streak.json`.

| | run 1 | run 2 | run 3 |
|---|---|---|---|
| **holed tip**: confirmed findings | 1 | 1 | 1 |
| | (`authz-GET-v1-habits-...100-ben`, each run) | | |
| holed wall time | 10.2s | 11.3s | 11.2s |
| **clean tip**: confirmed findings | 0 | 0 | 0 |
| clean wall time | 5.8s | 5.7s | 4.7s |

- H1a (found in 3/3 holed): **holds** -- the IDOR is confirmed every run.
- H1b (zero on clean in 3/3): **holds**.
- **S1 PASS** (both pre-registered hypotheses).

A design gap found and fixed during S1 (reported, not hidden): the runner first seeded only the
harness `build_db` (users/prefs/device), not the scenarios' `given` rows, so habit 100 did not
exist and the attack hit 404 on both tips (first S1 run: 0/0 on holed too). Fixed by seeding
the deduped union of the scenarios' `given` into the throwaway database at target start (reusing
the checker's `_insert_given`); the table above is the run after the fix.

### S1b -- LLM attacker (n=1): implemented, NOT executed

`lattice/security/agent.py` is built: one `claude -p` (model sonnet), tools restricted to
`Bash(curl http://127.0.0.1:<port>/*)` and `Read`, given only the loopback address and the
public spec text; the SAME judge replays its claimed findings; it refuses an in-process target
(no socket to curl) and uses the CLI backend (no API key). It was **not run** in this session
(it is "optional and separate, never run by default", and a real `claude -p` swarm is a cost/
time this build did not spend). The pre-registered 60% prediction therefore has no result; this
is stated, not silently dropped.

### S2 -- swarm over 5 seeded holes

The full 5-class (plus a 6th) seeded-hole swarm was executed on the **controllable in-repo
target** (minishop), not on 5 separately-built Streak Go binaries (seeding five Go holes was out
of this build's scope; the one Streak hole is S1). On minishop, all six classes each got their
own holed variant and all classes ran against each
(`data/.../minishop_swarm.json`, workers=1 for deterministic detection):

| variant (seeded hole) | detected | confirmed classes | n confirmed |
|---|---|---|---|
| authz (IDOR) | yes | authz | 1 |
| auth (token bypass) | yes | auth, authz | 6 |
| bounds (limit removed) | yes | bounds | 1 |
| mass (client price accepted) | yes | mass | 1 |
| state (double-cancel) | yes | state | 1 |
| leak (verbose error) | yes | bounds, leak | 7 |

- **recall 6/6** (prereg threshold for the 5 Streak holes was >= 4/5);
- **precision 1.0** (the clean app is quiet and exactly one hole is seeded per variant, so every
  confirmed finding is a consequence of that hole; e.g. the auth bypass also surfaces authz on
  anon, and the verbose-error hole turns bounds 422s into 500s -- both correspond to their
  seeded hole);
- **clean app: 0 confirmed findings in 3/3 runs.**

S2 meets every pre-registered threshold, on the in-repo target rather than on five Streak
binaries.

### S3 -- system scope on the clean Streak app (report only)

`lattice attack run --classes authz,auth,bounds,mass,state,leak` on the clean tip.
Raw: `data/.../s1_s3_streak.json`.

- **160 attacks planned**, wall **123.3s**, **0 confirmed findings** (no hole surfaced in the
  built app).
- Per class run/refuted/findings/not-refuted: authz 42/41/0/1, auth 84/63/0/21, leak 26/26/0/0,
  state 8/8/0/0. **bounds and mass generated 0 attacks on Streak** (see "missed" below).
- Dedup quality: 22 raw phase-1 claims, **0 confirmed** after the judge replayed each on a fresh
  seed -> 0 distinct findings. The 22 unreproduced claims were all transient phase-1 noise under
  the concurrent pool (21 `auth ... empty-bearer`, 1 authz); the judge's isolated replay
  correctly dropped every one. This is exactly the "not refuted, never verified" honesty the
  layer-X precedent requires: zero confirmed does not mean "secure".

## What a spec-driven deterministic swarm demonstrably missed

By construction and by evidence:

- **bounds and mass on Streak: 0 attacks generated.** Streak's spec uses `access_matrix`
  (scenarios), not `Contract` objects, and its numeric/length bounds and request-model field
  lists live in request models the spec does not carry as contracts. With no `ContractParam`,
  the bounds generator has only the profile's enum violations to work from (Streak's owned
  tables expose none on the scenario bodies) and the mass generator has no "allowed field" set
  to subtract. The S0 list's bounds and mass rows (A01-A03, H05, ...) are therefore NOT covered
  on Streak by this module today -- they need the spec to carry contracts (or the module to
  parse the DSL request shapes). On minishop, which carries contracts, both classes work.
- **Races / concurrency** (S0 rows C06, F06, G04, I12): out of scope for the deterministic
  generator (stated in the prereg).
- **Header/carried-value holes needing the signing key** (S0 D06-D09): the module sends raw
  headers (auth malformed/empty/wrong-scheme is covered), but minting a validly-signed forged
  token needs `HABIT_SECRET_KEY`, which a black-box spec attacker does not have.
- **Collation/padding, index limits, timing, deployment topology** (S0 NEEDS-APP-KNOWLEDGE
  rows): not derivable from the spec.

## The descriptor-schema change for a `custom` kind with a CLI entry

Spike 2 built only the `engine` module kind; its manifest had no way to name a `custom`
module's code entry point. The one change spike 1b needed: an **optional `entry` field**
(`entry = "pkg.module"`) accepted for `kind = "custom"` (refused for `engine`), naming the
Python module whose `main(argv)` the host runs for the subcommand(s) in `provides`. With it the
security module is a valid custom module (`provides = ["attack"]`, `entry = "lattice.security"`),
installs with spike 2's `lattice module install`, and `lattice module list` shows it. The
dispatch from an installed custom module's `entry` to its subcommand is documented-not-wired
(as spike 2's layer-A hook is); `lattice attack` is the built-in entry in the repo.

## Decisions I made (not specified in the brief)

- **Endpoint model from scenarios, enriched by contracts.** The brief described a generator
  "from the spec model + trace"; because Streak has no `Contract`s, the universal source is the
  scenarios (concrete requests + per-caller expected status, which the access matrix provides),
  with contracts/profile/machines as enrichment. Both paths are exercised (minishop carries
  contracts).
- **Owner-exclusion rule.** A caller ever served 2xx on an endpoint is never attacked as a
  non-owner there, even if another scenario (another seed) refuses it. This removed false
  "owner attacks her own resource" cells and is what keeps the clean app quiet.
- **Oracles are response- and DB-based, not LLM judgement.** `served` (a 2xx where a refusal
  was due), `bad_accepted` (bad input accepted or a 5xx), `body_contains` (a leaked marker),
  `row_present` (a forbidden write seen in the DB). The DB oracles read the SQL table name, and
  are tolerant (a read they cannot do is no finding), so mass under-reports rather than
  false-positives.
- **Phase 1 may false-negative under its concurrent shared database** (a write attack whose
  resource another attack consumed), which the judge cannot recover (it only removes false
  positives). The detection tests therefore run phase 1 with `--workers 1`; real runs against a
  process app on MySQL (Streak) do not hit this. A future version should clone a fresh database
  per attack.
- **Seeding the scenarios' `given`.** The target seeds the deduped union of the scenarios'
  `given` rows into the throwaway database (reusing the checker's `_insert_given`), so attacked
  resources exist; without this the Streak IDOR was unreachable (found during S1, fixed).

## What is not verified / limitations

- The deterministic generator does not race requests; concurrency holes are out of scope
  (stated in the prereg and S0).
- Bounds coverage on Streak is limited to profile enum violations (Streak has no `Contract`s;
  its stated numeric/length bounds live in request models the spec does not carry as
  contracts). The minishop target exercises the full contract-bounds path.
- The judge's DB-read oracles (mass) are tolerant: a read they cannot do is treated as "no
  finding", so mass can under-report rather than false-positive.

## Orchestrator verification and corrections (2026-10-12)

Independent re-run of the full matrix on the delivered tree: suite (tests/security + modules +
perf + studio + mcp + test_cli + spec) 1236 passed, 5 skipped; dogfood gate PASS, 393 cases.
Three things were found and fixed by the orchestrator; the final numbers below are after them.

1. **SM1 survived on the final code.** The mutant "the judge confirms without a replay" was
   reported caught earlier, but on the delivered code no test failed: with deterministic
   phase 1 a claim that fires on the shared database and not on a fresh replay never reached
   the judge in any test. Added
   `test_the_judge_replays_on_a_fresh_target_and_confirms_only_what_reproduces` (fake targets:
   fires on "shared" only -> not confirmed; fires on both -> confirmed). SM1 is now caught.
2. **The SM3 patch text was stale** (the generator gained an owner-exclusion condition after the
   mutant was written, so the runner aborted with "occurs 0 times"); the patch now matches the
   current source. Final run: control PASS (55 tests), SM1-SM5 all caught.
3. **The LLM attacker crashed on its first real run** (the pre-registered S1b was not executed
   by the build agent): a model-written claim had `expect_status` as an integer and the parser
   assumed a list. The claim parser now treats model output as untrusted data: int/str/list
   statuses, non-relative or host-bearing paths and odd field types are skipped or ignored,
   never fatal (`tests/security/test_agent.py`, 16 cases).

**S1, reproduced independently.** On a scratch clone of Streak at a0679e9 with an ownership
hole seeded by the orchestrator in a different way (an `AnyHabit` helper in `internal/db`
used by `GET /v1/habits/{id}`): holed: 1 confirmed (`GET /v1/habits/...100 as ben`: 200 where
404 was due), 17 s; clean: 0 confirmed, 10 s; the judge dropped 1 unreproduced claim on each.

**S1b, executed once by the orchestrator (n=1, labelled).** One real `claude -p` (sonnet, only
`curl` to the loopback address), black-box, against the holed clone: 180 s, $0.19, 1 claim
(`GET /v1/habits/2222...` as `b@x.com`). The judge did **not** confirm it: the claim names a
user that does not exist in the seed ("no seed user 'b@x.com'"; the attacker must have created
that account itself). So the pre-registered "finds it" is **not met by the pipeline**: a claim
must carry a replayable recipe (several steps, including registering users), not a request as
a seeded user. This is a design gap, not a verdict on the attacker; the substance of its claim
was not verified. S2 on five Streak binaries was not run (substituted by minishop, disclosed
above), and bounds and mass-assignment generate 0 attacks on Streak (its spec has no contracts).
