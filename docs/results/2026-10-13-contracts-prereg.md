# Multi-project layer 3 (contracts between projects): pre-registration

## 1. Status

Date: 2026-10-13. Branch `np-contracts` (worktree `lattice-contracts`, from `next-phases` c0cdefc).
Written BEFORE any code of this build exists and before any probe runs. Nothing below was run or
measured by this document's author. The only measured facts it uses come from recon R1 and R2,
which the orchestrator re-verified (`r1/r1-surface.md`, `r1/r1-job-scheduler-surface.json`,
`r2/r2_live_probe.py`, `r2/run{1,2,3}.json` in the session scratchpad). The results will go in
`docs/results/2026-10-13-contracts-results.md`, with raw data in `docs/results/data/2026-10-13-contracts/`.
No LLM and no API key are used anywhere: the cost is 0 tokens.

## 2. Decisions taken before coding

These are the user-approved defaults: the provider's spec is the source of the contract; a static
spec-to-spec check is the base; live checking is opt-in per link; the version is the spec hash
plus the git ref; every check is opt-in per link. The pair is job-scheduler (provider) and a tiny
consumer `ci-bot` under `dogfood/contracts/`. Real repositories are never written. Items marked
**[changed]** depart from the orchestrator's skeleton, with the reason given.

1. **Code layout.** The package is `lattice/contracts/`, with `export.py`, `check.py`, `live.py`,
   `__main__.py`, and the CLI `lattice contracts ...`. "Contract" here means a cross-project
   reliance. It is unrelated to `lattice.spec.Contract`, which both specs use 0 times (R1).
2. **Path template.** Split the path on `/`. Every segment that is all digits, or a UUID
   (8-4-4-4-12 hex, any case), becomes `{}`. Import paths go through the same rule, and any
   `{name}` segment is also turned into `{}`. Names are labels only and are not exported. The key
   is `METHOD /path`: the method is upper-cased, there is no trailing slash, and query strings are
   not allowed. `/users/me` stays literal, so it does not collide with `/users/{}`.
3. **Export (derived from the loaded `Spec`, never hand-authored).** Only `Step`s are read.
   `Await`s are ignored. An endpoint with no 2xx step is excluded: on job-scheduler this is
   `PATCH /applications/{}/jobs/{}`, the 405 probe. The facts per endpoint:
   - `statuses`: every observed status.
   - `request_fields`: the union of `Step.json` keys over the 2xx steps.
   - `query_fields`: `Step.params` keys over the 2xx steps.
   - `response_fields`: the union over the 2xx steps of the `expect_body` dict keys, or of the
     dict keys of the items when the body is a list.
   - `body`: the observed shapes, a subset of `object` and `list`.
   - `auth`: `public` if an anonymous step got a 2xx; `token` if there are anonymous steps and
     every one got 401; `unknown` otherwise.
   - `granted`: the handles with at least one 2xx.
   - `refused`: the handles with a 403 and no 2xx.

   **[changed]** The fields come from 2xx steps only, and list items count. Error-only keys such
   as `detail` are profile facts, and R1's lists had no item keys. At the top level the export
   holds `format: 1` and `error_body` (from `profile.api.error_body`). Every fact is `observed`,
   meaning a lower bound, because both specs have 0 Contracts.
4. **Version.** `spec_hash` is the sha256 of the canonical export JSON (sorted keys, `(",", ":")`
   separators, ASCII; `version` and `spec_ref` excluded). `git_ref` is the provider's `git rev-parse
   HEAD`, or null. `dirty` is true when `git status --porcelain --untracked-files=no` is non-empty.
   - Invariant: the hash differs exactly when the computed diff is non-empty.
   - The export is computed in a subprocess (`python -m lattice.contracts export`). This isolates
     `sys.modules`: both projects have a package named `spec` (R1 used `sys.modules.pop`).
5. **Where the provider's settings come from [changed].** job-scheduler has no `studio.toml`, and
   `spec:SPEC` is ticket 1 only. So a `[[project]]` may carry `spec` (here `spec.tickets:t5`) and,
   for live checks, `app`, `db`, `login` and `src` (the strings R2 used) plus `env`, a table of
   strings in which `$PROJECT` is the provider directory. Studio's `studio.toml` wins when it
   exists. Wrong types give a `bad_type` problem. `WsProject.to_dict` gains these keys only when
   they are set.
6. **Import file `lattice.imports.toml`** (in the consumer's directory). It holds `[[import]]` tables
   with these keys:
   - `provider`: a manifest name.
   - `endpoint`.
   - `status`: a non-empty list of ints.
   - `auth`: `"public"` or `"token"`.
   - `as_user`: optional, `token` only.
   - `request_fields`, `response_fields`: lists of strings, default `[]`.

   Unknown keys, bad types or a bad endpoint give `bad_imports` and exit 2. **[changed]**
   `as_user` was added. Role evidence in a spec exists only per seed handle, because
   `Spec.users` is (login, password) only (R1). The consumer names the handle whose standing its
   account has (`eve`, executor of `alpha`). An import with `auth = "token"` and no `as_user` asks
   only that some handle was granted.
7. **Lock `lattice.contracts.lock.json`** (in the consumer's directory). It is
   `{"format": 1, "consumer": NAME, "providers": {NAME: {"spec_ref", "version", "export"}}}`, with
   no timestamps and no absolute paths, so two pins of the same provider commit are
   byte-identical.
   - `lattice contracts pin` writes it.
   - **[changed]** `pin` refuses (exit 1, the file untouched) while the link has a BROKEN
     finding, unless `--force`. Without this, re-pinning would launder a BROKEN into a gap
     (decision 8).
   - A lock whose `spec_hash` does not match its own embedded export gives `bad_pin` (exit 2).
8. **Check semantics.** For each import `r`, let `E` be the current export and `P` the pinned
   export.
   - If `r` is satisfied by `E`, it is ok.
   - If it is not satisfied by `E` but was by `P`, it is **BROKEN**: `endpoint_missing`,
     `status_not_observed`, `request_field_missing`, `response_field_missing` or `auth_changed`
     (public now `token`/`unknown`, or `as_user` no longer granted).
   - If it is satisfied by neither, it is a **GAP**, a reliance the provider's spec never showed:
     `*_unobserved`. **[changed]** In the skeleton, BROKEN meant only "not satisfied by `E`". The
     export is a lower bound, so the clean baseline would be BROKEN (`access_token` is never in
     job-scheduler's spec).
   - **CHANGED** is every item of the diff `P -> E` over the whole export, each tagged
     `relied: true|false`. Its kinds are `endpoint_added`, `endpoint_removed`, `status_added`,
     `status_removed`, `field_added`, `field_removed` (with `where`: request, response or query),
     `auth_tightened`, `auth_loosened` (the auth class, or one handle's granted/refused move),
     `body_shape_changed` and `error_body_changed`.
   - A diff item that causes a BROKEN finding is folded into it and not repeated as CHANGED.
   - Informational, never a finding: `version_changed` (the git ref or dirty flag differs while
     `spec_hash` is equal) and the gap list.
   - Findings are sorted by (class, endpoint, kind, item).
9. **Workspace opt-in.**
   - A `[[link]]` key `contract` may be absent (never checked, the provider spec never loaded),
     `"static"` or `"live"` (static plus live).
   - Any other value or type gives the error `bad_contract`, and the link stays listed.
   - `Link.to_dict` emits `contract` only when it is set, and `show` appends `  [contract: X]` only
     then. Manifests without the key are byte-identical in every output.
   - The key is allowed on either link kind.
   - `contracts check` on a manifest with any error exits 2 and prints the problems.
10. **CLI.**
    - `lattice contracts export NAME` prints the canonical export and version: exit 0, or 2 if the
      export cannot load.
    - `lattice contracts pin CONSUMER [--force]` exits 0 when it writes the lock, 1 when it
      refuses, 2 when it cannot run.
    - `lattice contracts check [CONSUMER] [--json] [--static-only]` exits 0 when clean, 1 on any
      BROKEN or CHANGED, 2 when it cannot run. With several links, 2 takes precedence over 1, and
      1 over 0.
    - All three take `[--workspace PATH]`. A missing pin gives `no_pin` and exit 2, with a message
      that names the lock path and `lattice contracts pin`.
11. **Live [changed].** Live runs only against the provider app that the check starts itself: a
    throwaway SQLite database through `start_target_harness`, in-process by default. There is no
    URL target, because a replay mutates data (it logs in, creates configs and runs). This
    follows the security module, whose `target_from_url` always refuses.
    - A `live_url` key fails `ensure_safe_target`, giving `not_loopback` for a non-loopback host.
      A loopback URL is refused with `live_url_unsupported`. Either way the exit is 2.
    - For each import, find the first 2xx step for that endpoint, in scenario then step order, in
      the provider's current spec, made by `as_user` (anonymous for `public`, any handle for a
      token import without `as_user`).
    - Replay that scenario's steps up to that step on a fresh seed. Earlier steps are sent
      without assertions. Compare the target step's answer with the import:
      - status not in `status`: 401 or 403 give `auth_changed`, anything else gives
        `status_not_observed`;
      - a 2xx without a relied field gives `response_field_missing`.
    - Live findings are judged against the import, not the pin. A gap that the live answer
      satisfies is listed as `verified_live`.
    - Live does not check request fields, because it sends the spec step's body, not the
      consumer's.
12. **MCP and API.** Both are in this build and both are static only.
    - The MCP tool `contract_check {consumer?}` is listed only with a workspace, like
      `workspace_impact`, and is allowed under `--readonly`.
    - `GET /api/contracts[?consumer=NAME]` is API v1.17, documented in `API.md`, with a mock in
      `mock_server.py`. It re-reads the manifest on every request. With no workspace it answers
      `{"workspace": null, "links": [], "problems": []}`. A live link shows
      `live: {"status": "not_run", "reason": "read-only"}`.
    - Dogfood gets at most one rule (R94) and one scenario for the endpoint. There is no web UI
      change.
13. **Out of scope:**
    - layer 4 (impact labels, linked pending questions or changes);
    - running both apps together;
    - headers;
    - required vs optional fields, bounds, field types, nullability;
    - error-shape reliance by consumers;
    - pagination and ordering;
    - Streak or Go providers;
    - `depends_on` library semantics;
    - caching and CI integration.

## 3. Honest limits (what the static check cannot see; predicted as misses)

Both specs have 0 `Contract`s, so every exported fact is an observed lower bound. The static check
reads the provider's spec, never its app. It is predicted blind to the following:

- **Changed field types and nullability.** Only names are exported. D7 (`exit_code` 0 -> "0") is
  predicted MISS.
- **Tightened bounds and formats.** Validation shows up only as 422 examples. D10 (an application
  name limit of 100 -> 50) is predicted MISS.
- **An added required request field.** Required vs optional is unknown, so it appears as a
  CHANGED `field_added`, not a BROKEN. D8 (login needs `otp`) is predicted to be under-classified
  in this way.
- **An app that diverges from its spec.** L2-L4: static is predicted clean, and only live sees it.
- **Anything no scenario shows.** This includes `access_token`, which `spec.seed:login` reads but
  no `expect_body` states. Such a reliance is a permanent gap that only live can verify.
- **Partial changes hidden by unions.**
  - A field renamed in some 2xx steps but kept in another is unseen.
  - A handle granted on one application id stays granted even if refused on another (the
    templates merge the ids).
- **Other things the spec does not model:** headers (for example the Authorization scheme),
  5xx, rate limits, query semantics, background services (`Await`), and list item fields when
  every observed list is empty.
- **Live limits.** Live replays the provider's spec steps, not the consumer's code. It can replay
  only imports that have a matching 2xx step. It runs on the seed, not on production data.

## 4. The tiny consumer `ci-bot` (`dogfood/contracts/ci-bot/`)

`client.py` (committed as below; it imports `httpx`, which is in Lattice's venv). It logs in as an
executor of application `alpha`, starts the newest version of a job, waits for the run, and on a
timeout lists the job's runs and cancels its own:

```python
"""ci-bot: start the newest version of a job on the job scheduler and wait for its run."""
import os, sys, time
import httpx

def main() -> int:
    c = httpx.Client(base_url=os.environ["SCHEDULER_URL"], timeout=10)
    r = c.post("/auth/login", json={"email": os.environ["CI_EMAIL"],                 # I1
                                    "password": os.environ["CI_PASSWORD"]})
    if r.status_code != 200: return fail("login", r)
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    app = next(a["id"] for a in c.get("/applications").json()                        # I2
               if a["name"] == os.environ.get("CI_APP", "alpha"))
    jobs = [j for j in c.get(f"/applications/{app}/jobs").json()                     # I3
            if j["job_name"] == os.environ.get("CI_JOB", "nightly_report")]
    job = max(jobs, key=lambda j: j["version"])["id"]
    r = c.post(f"/applications/{app}/jobs/{job}/runs")                               # I4
    if r.status_code != 201: return fail("start", r)
    run, status = r.json()["id"], r.json()["status"]
    deadline = time.monotonic() + float(os.environ.get("CI_TIMEOUT_S", "600"))
    while time.monotonic() < deadline:
        body = c.get(f"/applications/{app}/runs/{run}").json()                       # I5
        if body["status"] in ("succeeded", "failed", "cancelled"):
            print(run, body["status"], body["exit_code"])
            return 0 if body["status"] == "succeeded" else 1
        time.sleep(5)
    print("timeout; runs:", [(x["id"], x["status"]) for x in
                             c.get(f"/applications/{app}/jobs/{job}/runs").json()])  # I6
    print("cancel:", c.post(f"/applications/{app}/runs/{run}/cancel").json()["status"])  # I7
    return 1

def fail(what, r):
    print(what, "failed:", r.status_code, file=sys.stderr)
    return 1

if __name__ == "__main__":
    sys.exit(main())
```

`lattice.imports.toml`: one `[[import]]` table per call I1-I7, with exactly these keys and values
(shown as the equivalent inline array to save lines; the file uses `[[import]]` tables). Each
import lists exactly the fields its line reads or sends. The status for I2, I3 and I5-I7 is the
2xx the code parses.

```toml
import = [
 {provider="scheduler", endpoint="POST /auth/login", status=[200], auth="public", request_fields=["email","password"], response_fields=["access_token"]},
 {provider="scheduler", endpoint="GET /applications", status=[200], auth="token", as_user="eve", response_fields=["id","name"]},
 {provider="scheduler", endpoint="GET /applications/{}/jobs", status=[200], auth="token", as_user="eve", response_fields=["id","job_name","version"]},
 {provider="scheduler", endpoint="POST /applications/{}/jobs/{}/runs", status=[201], auth="token", as_user="eve", response_fields=["id","status"]},
 {provider="scheduler", endpoint="GET /applications/{}/runs/{}", status=[200], auth="token", as_user="eve", response_fields=["status","exit_code"]},
 {provider="scheduler", endpoint="GET /applications/{}/jobs/{}/runs", status=[200], auth="token", as_user="eve", response_fields=["id","status"]},
 {provider="scheduler", endpoint="POST /applications/{}/runs/{}/cancel", status=[200], auth="token", as_user="eve", response_fields=["status"]},
]
```

**Probe workspace** (generated in the scratchpad):

- Project `scheduler`: path = a `git clone --no-hardlinks` of job-scheduler at ef17cf8, role
  provider, `spec = "spec.tickets:t5"`, `app = "job_scheduler:make_app"`,
  `db = "spec.seed:build_seed_db"`, `login = "spec.seed:login"`, `src = "src"`,
  `env = {JOB_SCHEDULER_REPO_ROOT = "$PROJECT"}`.
- Project `ci-bot`: a copy of `dogfood/contracts/ci-bot`.
- One link `ci-bot -> scheduler`, kind `uses`, `contract = "static"`; `"live"` in the L-series.

A lock pinned against the clean clone is committed as a golden. Each drift or control is applied
to a fresh clone of the clean clone with `str.replace`, after asserting the stated occurrence
count, and is then committed in that clone.

## 5. Probes

**Global rules.**

- Every probe runs **10 times**. Pass requires the canonical output to be identical in 10 of 10
  runs (`elapsed_s` excluded), plus the probe's own rule.
- All D, C and V probes check against the lock pinned on the clean clone.
- `T`, `S`: tickets.py and spec.py under the clone's `spec/`.

**B0 baseline.** Prediction:

- The export has **20** endpoints (R1's 21 templates minus the PATCH 405).
- `auth` is `public` 1 (login), `token` 4 (`GET /applications`, `POST .../jobs`,
  `POST .../runs`, `GET /users/me`), `unknown` 15. `error_body` is `fastapi`.
- The statuses equal R1's `expect_status` per template.
- No endpoint's `response_fields` contains `detail`.
- `pin` exits 0. `check` exits 0 with 0 findings and exactly **1 gap**:
  `response_field_unobserved access_token @ POST /auth/login`.

Pass: all of these.

**D-series: drifts in the provider's SPEC SOURCE.**

- Pass: **10 of 10** drifts match the predicted worst class (BROKEN, CHANGED or none = MISS) and
  the exact set of BROKEN (kind, endpoint, item).
- Also reported, with a prediction of 10 of 10: the full CHANGED set matches.
- In every D, `version_changed` is true (a new commit) and the gap list is unchanged.
- Of the 6 drifts that break ci-bot's code (D1-D5, D8), static is predicted to flag **5 as
  BROKEN**. D8 is flagged only as CHANGED.

| id | file: old -> new (exact; occurrences) | predicted | why |
|---|---|---|---|
| D1 endpoint removed | T: delete the 2 whole lines `Step("GET", RUNS, "eve", expect_status=200,` + `expect_body=[{"id": 1, "status": "queued"}]),` (1; lines 203-204, the only step of the endpoint) | BROKEN `endpoint_missing` GET /applications/{}/jobs/{}/runs; CHANGED none | the endpoint was in the pin and is gone (`endpoint_removed` folded) |
| D2 status 201->200 | T: `Step("POST", RUNS, "eve", expect_status=201` -> `...=200` (5); `Step("POST", RUNS, "ann", expect_status=201` -> `...=200` (6); `Step("POST", f"{JOBS}/2/runs", "eve", expect_status=201` -> `...=200` (1) | BROKEN `status_not_observed` 201 @ POST .../jobs/{}/runs; CHANGED `status_added` 200 | all 12 of its 201 steps become 200, so 201 leaves the set |
| D3 response field renamed | T: `expect_body={"id": 1, "status": "cancelled"}),` -> `expect_body={"id": 1, "state": "cancelled"}),` (1) | BROKEN `response_field_missing` status @ POST .../runs/{}/cancel; CHANGED `field_added` response `state` | line 224 is the only 2xx cancel step with a body |
| D4 request field renamed | S: `json={"email": "ann@example.com", "password": "pw-ann"},`, `...{"email": "ann@example.com", "password": "wrong"},`, `...{"email": "nobody@example.com", "password": "pw-x"},`, `...{"email": "ina@example.com", "password": "pw-ina"},` -> `"email"` replaced by `"username"` in each (1 each) | BROKEN `request_field_missing` email @ POST /auth/login; CHANGED `field_added` request `username` | the only 2xx login step now sends `username` |
| D5 role tightened | T: `Step("GET", "/applications/1/runs/1", "eve", expect_status=200,` -> `...=403,` (1); `Step("GET", "/applications/1/runs/2", "eve", expect_status=200,` -> `...=403,` (1) | BROKEN `auth_changed` as_user eve @ GET /applications/{}/runs/{}; CHANGED `status_added` 403 | eve leaves `granted` and enters `refused`; ann's 4 steps keep the field union unchanged |
| D6 endpoint added | S: `Step("GET", "/users/me", None, expect_status=401),` -> the same + newline + `Step("GET", "/users/2", "root", expect_status=200, expect_body={"id": 2, "email": "ann@example.com"}),` (1) | CHANGED `endpoint_added` GET /users/{} relied=false; BROKEN none | new 2xx template that no import uses |
| D7 field type | T: `expect_body={"id": 1, "status": "succeeded", "exit_code": 0}),` -> `..."exit_code": "0"}),` (1) | **MISS**: 0 findings, `spec_hash` equal, exit 0 | names only, no types |
| D8 required field added | S: `json={"email": "ann@example.com", "password": "pw-ann"},` -> `json={"email": "ann@example.com", "password": "pw-ann", "otp": "000000"},` (1) | **under-classified**: CHANGED `field_added` request `otp` relied=true; BROKEN none | required-ness is not exported; ci-bot would fail to log in |
| D9 status removed, relied endpoint | T: delete the whole line `Step("GET", JOBS, "bob", expect_status=404),` (1) | CHANGED `status_removed` 404 @ GET /applications/{}/jobs relied=true; BROKEN none | ci-bot relies on 200 only; bob was neither granted nor refused |
| D10 bound tightened | S: `"x" * 101}` -> `"x" * 51}` (1) | **MISS**: 0 findings, `spec_hash` equal | a 422 example changed; no exported fact moves |

**C-series: controls that must stay quiet.** Pass: **4 of 4** as predicted.

| id | change | predicted |
|---|---|---|
| C1 no change | none (the clean clone itself) | exit 0, 0 findings, 1 gap, `version_changed` false |
| C2 comment and whitespace | T: `RUNS = f"{JOBS}/1/runs"` -> `RUNS = f"{JOBS}/1/runs"  # job 1's runs in alpha` + one extra blank line (1) | file sha256 changes; `spec_hash` equal; exit 0, 0 findings; `version_changed` true (informational) |
| C3 scenario reorder | S: lines 173-201 (the `"login"` Scenario, lines 173-190, then `"me"`, lines 191-201) -> the `"me"` block then the `"login"` block, byte for byte | `spec_hash` equal; exit 0, 0 findings |
| C4 benign change, unused endpoint | S: `expect_status=201, expect_body={"id": 3, "name": "gamma"},` -> `expect_status=201, expect_body={"id": 3, "name": "gamma", "created_by": 1},` (1) | exit 1 with exactly 1 CHANGED `field_added` response `created_by` @ POST /applications relied=false; **0 BROKEN** |

**L-series: live**, with `contract = "live"`. The drift is in the APP source of the clone, and the
spec is untouched. The file below is `src/job_scheduler/`.

| id | app change (exact) | predicted static | predicted live |
|---|---|---|---|
| L1 clean | none | 0 findings | 0 findings; 7 of 7 imports replayed; `access_token` gap `verified_live` |
| L2 status | `routers/runs.py`: `    status_code=201,` -> `    status_code=200,` (1) | 0 findings, exit 0 | exactly 1 BROKEN `status_not_observed` @ POST .../jobs/{}/runs (answered 200) |
| L3 role | `routers/runs.py`: `    require_membership,\n)` -> `    require_app_admin,\n    require_membership,\n)` (1); `    require_membership(session, application, current_user)\n    return get_job_run_or_404(session, application, run_id)` -> `require_app_admin(...)` in place of `require_membership(...)` (1) | 0 findings | exactly 1 BROKEN `auth_changed` @ GET /applications/{}/runs/{} (answered 403) |
| L4 field | `schemas.py`: delete the line `    exit_code: int \| None` (1, in `RunPublic`) | 0 findings | exactly 1 BROKEN `response_field_missing` exit_code @ GET /applications/{}/runs/{} |
| L5 refusal | link `live_url` = `http://10.0.0.1:8000`, `http://example.com`, `http://0.0.0.0:8000` | n/a | exit 2 `not_loopback` (3 of 3); `http://127.0.0.1:8000` exits 2 `live_url_unsupported`; no request sent |

Pass: L1-L4 exactly as predicted, and L5 4 of 4 codes, each 10 of 10. The I5 replay runs the
provider's worker (`/worker/run-once` executes `fixtures/ok.py`). This is disclosed, not a finding.

**V-series: version.** Pass: **6 of 6**.

- **V1.** A README-only commit in the clone gives exit 0, 0 findings, `version_changed` true, the
  same `spec_hash` and a different `git_ref`.
- **V2.** With no lock, or a lock without this provider: exit 2, `no_pin`, and the message names
  the lock path and `lattice contracts pin`.
- **V3.** Pin 10 times on the clean clone: the locks are byte-identical to each other and to the
  committed golden, and `spec_hash` equals a recomputed sha256 of the embedded export.
- **V4.** After D2:
  - `pin` exits 1 and the lock's bytes are unchanged;
  - `pin --force` writes the lock;
  - the next check reports the 201 reliance as a gap `status_unobserved`, not BROKEN (the
    disclosed cost of `--force`).
- **V5.** An uncommitted, export-neutral edit (C2's text, not committed) gives `dirty` true,
  `version_changed` true and exit 0.
- **V6.** A lock with one hex digit of `spec_hash` flipped gives exit 2 `bad_pin`.

**W-series: workspace opt-in.** Pass: **6 of 6**.

- **W1.** `contract` absent, on a clone whose `spec/tickets.py` ends with
  `raise RuntimeError("not loaded")`: exit 0, "0 links checked". The same link with
  `contract = "static"` exits 2 `export_failed`.
- **W2.** With `"static"`, `live` is null although the project has `app`, `db` and `login`.
- **W3.** With `"live"`, the static and live results are both present.
- **W4.** `contract = "yes"` and `contract = true`: `workspace validate` exits 1 with
  `bad_contract` (each), and `contracts check` exits 2.
- **W5.** On the layers 1-2 MP-3 manifest (no `contract` keys), `lattice workspace show`,
  `show --json`, `validate`, `validate --json`, `impact shared-auth` and `impact shared-auth --json`
  are **byte-identical** (6 of 6) to the old code at `/Users/vivekgade/Documents/lattice`. Before
  running, confirm that its HEAD is c0cdefc and that `lattice/workspace.py` is unmodified.
- **W6.** Reported, not part of the pass: the old code validates a manifest that has `contract`
  keys with exit 0 and no problems (unknown keys are ignored). Prediction: yes.

**Other probes.**

- **A1, parity.** On B0 and D2, MCP `contract_check` (over stdio) and `GET /api/contracts` on the
  real server equal `contracts check --static-only --json` (`elapsed_s` excluded). The mock has
  the same keys and types. Pass: 4 of 4 comparisons.
- **S1, safety.** `/Users/vivekgade/Documents/job-scheduler` has HEAD ef17cf8 and
  `git status --short` = ` M uv.lock` before and after the whole run. `find -newer <start marker>`
  finds no file in it, and Streak is never opened. Pass: identical.
- **T1, cost** (reported, with a threshold).
  - Static check of one link: median ≤ 10 s; the prediction is ≤ 3 s, mostly the subprocess spec
    load.
  - Live (7 imports, a fresh seed each): median ≤ 60 s; the prediction is ≤ 15 s. R2 measured
    0.49 s for a process-mode start and stop.

## 6. Mutation plan (`dogfood/contracts_mutants.py`, for a later node)

Each mutant is a one-line behaviour change. Each must be killed by `tests/contracts` (pytest) on
the final code, and the probe named also kills it. Prediction: **11 of 11** killed. A survivor is a
finding: the test that kills it is added and reported post hoc.

| id | mutant | killed by (predicted) |
|---|---|---|
| CM1 | status ignored: `status_not_observed` never emitted | D2, L2 |
| CM2 | auth ignored: `auth`/`as_user` never compared | D5, L3 |
| CM3 | `endpoint_missing` treated as satisfied | D1 |
| CM4 | pin ignored: `P` := `E` (every unsatisfied reliance becomes a gap, no CHANGED) | D1-D5 (become gaps), D6, D9, C4 |
| CM5 | opt-in ignored: every link checked whatever `contract` says | W1 |
| CM6 | field reliance ignored (request and response) | D3, D4, L4 |
| CM7 | ids not templated (concrete paths kept) | B0 (the endpoint count is no longer 20; I3-I7, 5 of 7 imports, become gaps) |
| CM8 | `spec_hash` over the spec file bytes, not the canonical export | C2, C3 |
| CM9 | `pin` writes despite BROKEN | V4 |
| CM10 | live: the `ensure_safe_target` call removed | L5 (`not_loopback` becomes `live_url_unsupported`) |
| CM11 | `Link.to_dict` always emits `contract` | W5 (the `--json` outputs differ) |

## 7. Named risks (what could make this experiment meaningless)

1. **The D-series measures only spec-to-spec derivation.** A spec drift is read by the same loader
   the checker uses, so a pass shows that export, diff and classification are right, not that the
   provider behaves so.
   - Mitigation: the L-series drifts the app with the spec unchanged, and its prediction is that
     static is blind.
   - Disclosed in the results as two separate claims.
2. **The imports are written to fit the checker.**
   - Each import is the exact set of fields read or sent on its client line (I1-I7).
   - `access_token` is kept although it is predicted to be a gap; dropping it would make B0
     "clean".
   - The pair was chosen knowing that eve is covered by the spec. A real consumer would have more
     gaps. Disclosed.
3. **The predictor also designed the format.** The drifts are exact text edits with occurrence
   counts, fixed here, so they cannot be tuned after the code exists.
4. **Path-template collisions.** A numeric literal segment (`/v2`, `/reports/2024`) would be
   templated. job-scheduler has none (R1: 33 concrete -> 21 templates).
   - Mitigation: a test maps each export key to exactly one route of the clone's FastAPI app
     (`app.routes`, normalised).
5. **Observed-set noise.** Unions do not depend on order (C3). But an unrelated test step that
   adds a status to a relied endpoint gives a relied CHANGED (as D9 does). This is expected noise
   that a re-pin acknowledges; it is counted and reported, not hidden.
6. **Silent wrong spec.** Without `spec = "spec.tickets:t5"`, `spec:SPEC` is ticket 1, and the
   export would silently miss tickets 2-5.
   - Mitigation: the export and the lock carry `spec_ref`, and a changed `spec_ref` is shown.
     W-tests use the explicit key.
7. **The live check executes provider code** (the spec module, the app, the worker's fixture
   command).
   - It runs only on scratch clones and a throwaway SQLite database, in-process or on loopback,
     and never against a URL (decision 11).
   - Provider dependencies come from Lattice's venv (fastapi, sqlmodel, bcrypt and croniter
     import there), and their versions may differ from the provider's own `uv.lock`.
8. **The `as_user` coupling.** It ties the consumer to the provider's seed handle names. A renamed
   handle would read as `auth_changed`. This is accepted for this build and disclosed.
9. **Generality.** It is one pair and one provider shape (Python and FastAPI, old-format spec).
   Ten runs show only determinism, not breadth. Claims are limited to this pair.
10. **A dirty real provider.** The real job-scheduler has ` M uv.lock`, so `dirty` would be true
    there. The probes use clean clones and never the real path.

## 8. Blind vs post hoc

The criteria, drifts, controls and predictions above are fixed now, before any code exists.

- A failed prediction (a drift classified differently, a predicted MISS that is caught, a control
  that is not quiet) is reported as a **finding**, with its output, in the results document. It
  is never fixed silently. Any later fix is described as a post-hoc change, and the blind numbers
  stay as first measured.
- Anything learned while building or running (new kinds, edge cases, timings) goes in a separate
  "Post-hoc observations" section. Changes to the formats after this point are listed there with
  their reason.

## 9. Verification (the orchestrator runs these on the final code)

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-contracts PYTHONDONTWRITEBYTECODE=1
cd /Users/vivekgade/Documents/lattice-contracts
git -C /Users/vivekgade/Documents/job-scheduler rev-parse HEAD; git -C /Users/vivekgade/Documents/job-scheduler status --short  # before (S1)
uv run pytest tests/contracts tests/workspace -q                                   # new + layer 2
uv run pytest tests/contracts tests/workspace tests/improve tests/security tests/modules tests/perf \
  tests/studio tests/mcp tests/test_cli.py tests/spec \
  --ignore=tests/spec/test_db.py --ignore=tests/spec/test_multidb.py -q            # baseline 1400 passed, 5 skipped
dogfood/studio/check.sh                                                            # baseline PASS, 408 cases; >= 408, 0 findings
uv run python dogfood/contracts/probes.py --runs 10 \
  --scratch "$SCRATCH/contracts" --out docs/results/data/2026-10-13-contracts     # B0 D C L V W A S T
uv run python dogfood/contracts_mutants.py                                         # control passes; CM1-CM11 killed
uv run ruff check <changed .py files>; uv run ruff format --check <created .py files>
git -C /Users/vivekgade/Documents/job-scheduler rev-parse HEAD; git -C /Users/vivekgade/Documents/job-scheduler status --short  # after: identical
```

**Pass for the build:**

- the suite has 0 failures;
- `check.sh` PASS;
- B0, D (10 of 10), C (4 of 4), L (5 of 5), V (6 of 6), W (6 of 6), A1 and S1 as stated, with
  T1 within its thresholds;
- 11 of 11 mutants killed.

`$SCRATCH` is the session scratchpad. The baselines (1400 / 5 and 408) are re-measured at c0cdefc
before the build, and the re-measured numbers are the ones compared.
