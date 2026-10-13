# Multi-project layer 3 (contracts between projects): results

Date: 2026-10-13. Branch `np-contracts` (worktree `lattice-contracts`, from `next-phases` c0cdefc),
commits c795619 (prereg), 6d08959 (core), 3dad23f (live), 7898274 (MCP and API), 7b053cb (probe
runner, mutants, raw data). Pre-registration: `docs/results/2026-10-13-contracts-prereg.md`
(written before any code; section numbers below are its own). Raw data:
`docs/results/data/2026-10-13-contracts/` (`summary.json` and one JSON per probe; the recorded run's
`code_head` is 7898274, and 7b053cb adds only the runner, the mutant suite and the data). No LLM
and no API key were used. The real job-scheduler repository was only read (S1), and Streak was
never opened.

## Status and scope

**All pre-registered criteria pass: 35 of 35 probes, 0 prediction misses, 11 of 11 mutants
killed.** The claim is narrow. It covers ONE pair: job-scheduler (Python/FastAPI, old-format spec,
0 `Contract`s, so every exported fact is observed only) as provider, and the tiny consumer
`ci-bot` written for this experiment. The regression gate over the final code is at the end
(numbers filled in by the orchestrator).

**Blindness (prereg risk 3).** The builder (B1) and the live builder (B3) read the prereg,
including its predictions. B1 also ran the D, C and V series itself as a scratch sanity check
before the official run. According to the nodes, everything matched and nothing was tuned, but
the build was not blind to the predictions. The pre-registration fixed the predictions before
any code existed; it did not keep the builders from knowing them.

## What was built

- **Package `lattice/contracts/`** (new): `export.py` derives a provider's export from its loaded
  `Spec` in a subprocess (templated `METHOD /path` keys, observed statuses, request, query and
  response field names, body shape, auth class, granted and refused handles, `error_body`), with
  `spec_hash`, `git_ref` and `dirty` as its version. `check.py` holds the import file, the lock,
  `pin` and `check` (BROKEN, CHANGED with `relied`, GAP, `version_changed`). `live.py` replays the
  provider's own spec steps against its app on a throwaway SQLite database. `__main__.py` is the
  CLI.
- **CLI** (`lattice contracts ...` = `python -m lattice.contracts ...`, all with `--workspace PATH`):
  `export NAME [--spec REF]` or `export --project DIR`; `pin CONSUMER [--force] [--json]`;
  `check [CONSUMER] [--json] [--static-only]`. Exit codes as in prereg section 2, decision 10.
- **Workspace keys** (`lattice/workspace.py`): `[[link]] contract = "static" | "live"` (anything
  else is `bad_contract`; absent means never checked), `[[link]] live_url` (read only by the live
  check, which refuses it), `[[project]] spec`, `app`, `db`, `login`, `src` and `env` (`$PROJECT`
  expands to the project directory). All are emitted only when set.
- **Studio API v1.17**: `GET /api/contracts[?consumer=NAME]`, read-only and static only, the same
  body as `check --static-only --json` except a live link's reason `"read-only"`. Documented in
  `lattice/studio/API.md`, mocked in `mock_server.py`.
- **MCP**: `contract_check {consumer?}` in `lattice/mcp/projects.py`, listed only with a workspace,
  allowed under `--readonly`.
- **Dogfood**: rule R94 and one scenario in `dogfood/studio`; the consumer
  `dogfood/contracts/ci-bot/` (`client.py`, `lattice.imports.toml`, README), the golden lock
  `dogfood/contracts/golden/lattice.contracts.lock.json`, `dogfood/contracts/probes.py`,
  `dogfood/contracts_mutants.py`.
- **Tests**: `tests/contracts/` (export, check, live, integration, workspace opt-in),
  `tests/mcp/test_contract_check.py`, `tests/studio/test_contracts_api.py` and `contract_world.py`.
  Line counts: `git diff --stat c0cdefc 7b053cb`. No web UI change.

## Blind results: probes against the pre-registered predictions

Every probe ran 10 times with canonical output identical 10 of 10 (S1 and T1 are single
measurements by design). Predictions are quoted from `summary.json`, which the orchestrator
audited line by line against the prereg's D1-D10 table.

| id | predicted | observed | pass |
|---|---|---|---|
| B0 | 20 endpoints; auth public 1 / token 4 / unknown 15; `error_body` fastapi; statuses = R1; no `detail`; pin 0; check 0 findings, 1 gap | exactly that; the gap is `response_field_unobserved access_token @ POST /auth/login` | yes |
| D1 endpoint removed | BROKEN `endpoint_missing` GET /applications/{}/jobs/{}/runs; no CHANGED | same, exit 1 | yes |
| D2 status 201 -> 200 | BROKEN `status_not_observed` 201 @ POST .../jobs/{}/runs; CHANGED `status_added` 200 | same (relied), exit 1 | yes |
| D3 response field renamed | BROKEN `response_field_missing` status @ POST .../runs/{}/cancel; CHANGED `field_added` response `state` | same (relied), exit 1 | yes |
| D4 request field renamed | BROKEN `request_field_missing` email @ POST /auth/login; CHANGED `field_added` request `username` | same (relied), exit 1 | yes |
| D5 role tightened | BROKEN `auth_changed` as_user eve @ GET /applications/{}/runs/{}; CHANGED `status_added` 403 | same (relied), exit 1 | yes |
| D6 endpoint added | CHANGED `endpoint_added` GET /users/{} relied=false; no BROKEN | same, exit 1 | yes |
| D7 field type 0 -> "0" | **MISS**: 0 findings, `spec_hash` equal, exit 0 | missed as predicted | yes |
| D8 required field added | **under-classified**: CHANGED `field_added` request `otp` relied=true; no BROKEN | same, exit 1 | yes |
| D9 status removed, relied endpoint | CHANGED `status_removed` 404 @ GET /applications/{}/jobs relied=true; no BROKEN | same, exit 1 | yes |
| D10 bound 100 -> 50 | **MISS**: 0 findings, `spec_hash` equal, exit 0 | missed as predicted | yes |
| C1-C4 controls | C1-C3 exit 0, 0 findings (C2 file sha changes, `spec_hash` equal; C3 reorder hash-neutral); C4 exactly 1 CHANGED `field_added created_by` relied=false, 0 BROKEN | all as predicted | 4/4 |
| L1 live, clean | static 0; live 0 findings, 7/7 replayed, `access_token` `verified_live` | same; full exit 0 | yes |
| L2 app status 201 -> 200 | static 0, exit 0; live exactly 1 BROKEN `status_not_observed` 201 (answered 200) | same; full exit 1 | yes |
| L3 app role | static 0; live exactly 1 BROKEN `auth_changed` as_user eve @ GET .../runs/{} (answered 403) | same; full exit 1 | yes |
| L4 app field dropped | static 0; live exactly 1 BROKEN `response_field_missing` exit_code @ GET .../runs/{} | same; full exit 1 | yes |
| L5 refusal | 3 x `not_loopback` (10.0.0.1, example.com, 0.0.0.0), 127.0.0.1:8000 `live_url_unsupported`, all exit 2, no request | same; listener on 127.0.0.1:8000 saw 0 connections | yes |
| V1-V6 version | V1 README commit: exit 0, same hash, new `git_ref`; V2 `no_pin` exit 2 naming lock and command; V3 10 pins byte-identical to the golden; V4 pin refuses after D2, `--force` turns the 201 into a gap; V5 `dirty` true, exit 0; V6 flipped hex `bad_pin` | all as predicted (V1 `git_ref` ef17cf88 -> 4e8dfb30; V3 `spec_hash` 9bcae0e24da9 recomputed equal) | 6/6 |
| W1-W6 opt-in | W1 absent: 0 links checked, `static`: `export_failed`; W2 static link `live` null; W3 live link both parts; W4 `"yes"`/`true` `bad_contract`; W5 6 outputs byte-identical to c0cdefc; W6 (reported) old code accepts `contract` keys | all as predicted; W5 6/6 against each of two old checkouts; W6 exit 0, problems 0 | 6/6 |
| A1 parity | MCP and API equal the CLI `--static-only --json` on B0 and D2; mock same keys and types | 4/4 comparisons equal; mock shapes ok | yes |
| S1 safety | job-scheduler HEAD ef17cf8, ` M uv.lock` before and after, no newer file | same; 0 newer files | yes |
| T1 cost | static median <= 10 s (pred. <= 3 s); live <= 60 s (pred. <= 15 s) | static 0.20 s wall (0.15 s `elapsed_s`); live 0.72 s wall (0.68 s) | yes |

Totals (`summary.json`): **35 of 35 pass**; D-series core 10 of 10 and full CHANGED set 10 of 10.
Of the 6 drifts that break ci-bot's code (D1-D5, D8), static flags 5 as BROKEN and D8 as CHANGED
only, exactly as predicted. In every D probe `version_changed` was true and the gap list unchanged.
No leftover process, no leftover temp entry, scratch removed.

## Findings

**0 prediction misses.** Every probe matched its pre-registered prediction, including the three
predicted failures of the static check. The following are observations, NOT misses:

1. **The static check is blind where predicted.** D7 (a type change) and D10 (a tightened bound)
   give 0 findings and an equal `spec_hash`; D8 (a new required request field) is only a relied
   CHANGED, so ci-bot would fail to log in while the link is not BROKEN.
2. **The D-series measures spec-to-spec derivation only** (prereg risk 1). A spec drift is read by
   the same loader the checker uses. It shows the export, the diff and the classification do what
   the prereg says; it says nothing about whether the provider behaves so.
3. **The L-series is the evidence on app drift.** With the app changed and the spec untouched,
   static was clean (exit 0) in L2-L4, and live found exactly the one BROKEN each time. In L1-L4,
   live also verified the `access_token` reliance (`verified_live` 1), which static can only list
   as a permanent gap.
4. **Relied CHANGED noise (prereg risk 5) is real.** D9 removes an unrelated 404 example from an
   endpoint ci-bot uses and exits 1. A re-pin acknowledges it.
5. **`--force` launders a BROKEN into a gap**, as disclosed (V4): after D2, a forced pin turns the
   201 reliance into `status_unobserved`, and check exits 0.
6. **The old code silently accepts layer-3 manifests** (W6: exit 0, 0 problems). A manifest with
   `contract` keys read by c0cdefc is never checked and nothing says so.
7. **A1: MCP and CLI agree exactly; the API differs from the CLI only in the live link's reason**
   (`"read-only"` vs `"static-only"`), as required.
8. **Cost is far under the predictions**: 0.20 s static and 0.72 s live per link against predicted
   upper bounds of 3 s and 15 s. The predictions were bounds, so this is not a miss.

**What this does not show.** Breadth (one pair; ten runs show determinism only); a Studio-format
provider (Streak); a Go provider; a real consumer (ci-bot was written knowing eve is covered by the
spec, prereg risk 2); required vs optional fields; field types; bounds.

## Post hoc (not pre-registered)

**Decisions the prereg did not fix, taken by the builders.**

- **B1 (decisions 1-17).** `Link.live_url` as a manifest key; a `LiveError` type; helpers in
  `check.py` (`claims_of`, `holds`, `Import`, `finding`) exposed for the live module; `relied` is
  endpoint-level (a CHANGED item is relied when some import names its endpoint); the semantics of
  `version_changed`; per-claim evaluation (endpoint, each status, each field, auth); the diff's
  ranks and item names; the order of errors; import-file validation; the pin behaviours; lock
  formatting with `indent=1`; `export --project`; the spec ref default (`spec:SPEC` unless the
  manifest says otherwise); the subprocess for the export; how spec step paths are read; the live
  statuses; reformatting of `client.py`.
- **B3 (decisions 1-12).** Replay in a child process with a private `TMPDIR`; the
  `status_not_observed` item is the import's lowest 2xx; the field check applies to any 2xx answer;
  an empty list answer gives `fields_unobservable`; extra result keys `replays`,
  `fields_unobservable` and `not_checked`; `verified_live` may include a claim that is statically
  BROKEN; `not_replayed` reasons, and `Await` steps are skipped; replays are shared between imports
  that hit the same step; the harness source is `studio.toml`, else the manifest keys; the
  `LiveError` codes; an unparseable `live_url` gives `not_loopback`; the provider's environment is
  not scrubbed.

**Probe procedure** (`summary.json` `post_hoc.procedure`; no script correction was recorded):

- The prereg writes the auth item as `as_user eve`, the code as `as_user:eve`; the comparison
  normalises the colon only. D1's `endpoint_missing` item is the empty string.
- `relied` is compared only where the prereg states it (D6, D8, D9, C4); elsewhere it is recorded.
- C3 is a slice swap at asserted line numbers, not a `str.replace`; D1 and D9 assert the fragment's
  count, then delete the whole lines; L4 deletes the line with its newline.
- W5 and W6 record which `lattice/workspace.py` each child imports and that the old code has no
  `lattice.contracts`. **This guard was added after a first full run (35/35) and before the final
  run.**
- L5's "no request sent" is evidenced by a listener on 127.0.0.1:8000 (0 connections) and, for
  hosts that cannot be observed, by the exit code, the error code and the wall time.
- S1's git calls use `--no-optional-locks`, so `git status` cannot refresh the real index.

**Process notes.**

- **The main checkout's branch moved.** W5 names `/Users/vivekgade/Documents/lattice` as the old
  code. When the orchestrator first looked, that checkout was on `develop` (no `workspace.py`). At
  run time it was on `next-phases` at c0cdefc; its reflog shows `checkout: moving from develop to
  next-phases` at 12:24:59, by an actor the orchestrator cannot identify (the orchestrator did not
  run it). The probe compared against BOTH it and the detached worktree `lattice-contracts-base`
  (c0cdefc): 6/6 byte-identical against each. The main checkout's branch is therefore not what it
  was at session start. No data was lost: it was clean apart from the owner's untracked files.
- **Shared-worktree operations by B2.** B2 ran `git stash` / `git stash pop` in the shared worktree
  and `pkill -f lattice.studio`. The orchestrator verified the stash list is empty and the
  committed `live.py` equals the working tree: nothing was lost.
- **Two B1 tests rewritten.** Two tests in `tests/contracts/test_check.py` asserted B1's live stub
  ("not built"); the orchestrator rewrote them in 3dad23f when B3 replaced the stub.
- **The prereg is 470 lines**, over the 300-line cap given to its node; content complete, accepted.

## Mutants (`dogfood/contracts_mutants.py`, suite `tests/contracts tests/workspace`)

Control PASS (214 passed); **11 of 11 killed**; total 195 s (orchestrator's run from scratch). A
mutant is killed by a failing test; the prereg's "the probe named also kills it" was not run per
mutant (the column below is the prereg's prediction, not a measurement).

| id | mutant | prereg probe | first failing test (orchestrator's run) |
|---|---|---|---|
| CM1 | `status_not_observed` never emitted | D2, L2 | `test_check.py::test_each_broken_kind[status_not_observed]` |
| CM2 | auth / `as_user` never compared | D5, L3 | `test_check.py::test_each_broken_kind[auth_changed_public]` |
| CM3 | `endpoint_missing` treated as satisfied | D1 | `test_check.py::test_each_broken_kind[endpoint_missing]` |
| CM4 | pin ignored, P := E | D1-D6, D9, C4 | `test_check.py::test_each_broken_kind[endpoint_missing]` |
| CM5 | opt-in ignored | W1 | `test_check.py::test_pin_with_nothing_opted_in_is_an_error` |
| CM6 | field reliance ignored | D3, D4, L4 | `test_check.py::test_each_broken_kind[request_field_missing]` |
| CM7 | ids not templated | B0 | `test_check.py::test_clean_pin_and_check` |
| CM8 | `spec_hash` over file bytes | C2, C3 | `test_check.py::test_clean_pin_and_check` |
| CM9 | pin writes despite BROKEN | V4 | `test_check.py::test_pin_refuses_on_broken_unless_forced` |
| CM10 | `ensure_safe_target` call removed | L5 | `test_live.py::test_a_non_loopback_live_url_is_refused_before_start[...10.0.0.1:8000]` |
| CM11 | `Link.to_dict` always emits `contract` | W5 | `test_workspace_contract.py::test_manifests_without_layer3_keys_print_as_before[show_json-good]` |

CM8 is also killed incidentally through the lock integrity check (a lock whose `spec_hash` no
longer matches its embedded export is `bad_pin`), not only by the hash-neutrality tests.

## Timings

| What | Time |
|---|---|
| Static check of one link (T1, 10 runs) | median 0.20 s wall, 0.15 s `elapsed_s`; over all 280 static check calls of the run, median 0.196 s |
| Live check, 7 imports (T1, 10 runs) | median 0.72 s wall, 0.68 s `elapsed_s`; over all 50 live check calls, median 0.722 s |
| Whole probe run, `--runs 10` | 160.7 s (`summary.json` `wall_s`) |
| Mutant run (control + 11) | 195 s |
| Baseline suite at c0cdefc | 7 min 33 s |
| B2 subset (`tests/mcp tests/studio tests/contracts tests/workspace`) | 5 min 59 s |

## Orchestrator verification

| Check | How it was checked | Result |
|---|---|---|
| R1 recon | the agent's script and the orchestrator's own one-liner | job-scheduler `spec.tickets:t5`: 43 scenarios, 152 steps, 0 Contracts, 33 distinct (method, path), 21 templates; Streak: 156 scenarios, 466 steps, 48 / 26. Both agree |
| R2 recon | probe re-run twice by the orchestrator | the provider app starts on loopback SQLite from Lattice's own pieces; same statuses; original repo unchanged |
| Prereg drift texts | all 22 drift, control and app-edit strings checked against job-scheduler ef17cf8 before the prereg commit c795619 | 0 mismatches. Prereg is 470 lines (cap 300 exceeded, accepted) |
| Baseline at c0cdefc | re-measured | 1400 passed, 5 skipped (7 min 33 s); `check.sh` PASS, 0 findings, 408 cases |
| B1 core (6d08959) | re-run on the orchestrator's own clone | `tests/contracts tests/workspace` 164 passed; clean export byte-identical x3 (sha256 prefix c772b715), 20 endpoints, auth public 1 / token 4 / unknown 15; diff scope limited to the listed files; ruff clean on 14 files |
| B3 live (3dad23f) | two B1 stub tests rewritten in `test_check.py`; L1, L2, L5 run on the orchestrator's own clones | 214 passed. L1: 0 findings, 7/7 replayed, `access_token` `verified_live`, identical x3. L2: exactly 1 BROKEN `status_not_observed` 201, static clean, link status `broken`, live findings under `links[0].live.findings`. L5: 3 x `not_loopback`, 1 x `live_url_unsupported` |
| B2 surfaces (7898274) | suite and dogfood re-run; worktree state after B2's stash and pkill | `tests/mcp tests/studio tests/contracts tests/workspace` 791 passed, 1 skipped (5 min 59 s); `check.sh` PASS, 0 findings, 415 cases (408 + the 7 steps of the R94 scenario); stash list empty, committed `live.py` = working tree, no loss |
| X + B4 (7b053cb) | `probes.py --runs 10` and `contracts_mutants.py` re-run from scratch | probes 35/35 pass, T1 static median 0.20 s, live 0.72 s; mutants control PASS (214 passed), CM1-CM11 11/11 killed, 195 s |
| Predictions in the runner | `probes.py` audited line by line against the prereg table | D1-D10 predictions equal the prereg's |

## Regression gate (final code 7b053cb, run by the orchestrator)

| Check | Result |
|---|---|
| pytest, prereg section 9 suite list (incl. `tests/contracts`) | **1596 passed, 5 skipped, 0 failed** (8 min 35 s). Baseline at c0cdefc: 1400 passed, 5 skipped, so 196 more tests (`tests/contracts`, `tests/mcp/test_contract_check.py`, `tests/studio/test_contracts_api.py`); skipped: 5, the same count as the baseline |
| `dogfood/studio/check.sh` | PASS, 0 findings (D 0, X 0), **415 cases** (baseline 408; the 7 new are the R94 scenario's steps) |
| ruff check, every `.py` changed since c0cdefc (24 files) | All checks passed |
| ruff format --check, same 24 files | 21 already formatted; 3 would be reformatted: `lattice/studio/server.py`, `lattice/studio/mock_server.py`, `dogfood/studio/spec.py`. The same 3 fail at c0cdefc (checked in the baseline worktree) and B2 compared the hunks: none of the reformat hunks is new |
| job-scheduler HEAD and `git status --short` after the run | ef17cf8, ` M uv.lock` (unchanged from before the session) |

## Known limits

- **Live does not check request fields**: it sends the spec step's body, not the consumer's
  (`not_checked: ["request_fields"]` in every live result).
- **`Await` steps and background services are not run** by the replay.
- **Live findings live under `links[0].live.findings`**; the top-level `findings` list is static
  only. In L2-L4 the static `findings` is empty while the link `status` is `broken`.
- **`as_user` ties the consumer to the provider's seed handle names** (prereg risk 8): a renamed
  handle reads as `auth_changed`.
- **Hashes cover the export only**: anything the export does not carry (types, bounds, headers,
  5xx, the app itself) cannot change `spec_hash`.
- **The live replay executes provider code** (the spec module, the app, the worker's fixture
  command) on a throwaway database; in the probes it ran only on scratch clones. The provider's
  environment is not scrubbed, and its dependencies come from Lattice's venv.
- **CM8 is also killed incidentally** through the lock integrity check (see Mutants).
- Unions hide partial changes (a field kept in one 2xx step; a handle granted on one id), and list
  item fields are unseen when every observed list is empty (prereg section 3).

## What is next

- Layer 4 (impact labels across links, linked pending questions and changes in consumers).
- A second pair with a Studio-format provider (Streak), then a Go provider; a real consumer.
- Live request-field checking (send the consumer's fields, not the spec's).
- A contract check as a Studio gate or UI; the Map's workspace edges showing contract state; the
  plugin config.
- Required vs optional fields (the D8 gap), which needs the provider's spec to say so.
