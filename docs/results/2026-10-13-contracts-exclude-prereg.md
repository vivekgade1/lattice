# Layer 3: a provider-declared exclusion list (`exclude`): pre-registration

## 1. Status

Date: 2026-10-13. Branch `np-exclude` (worktree `lattice-exclude`, from `next-phases` 7358be2, which
holds layer 3 and both of its experiments). Written BEFORE any code of this change exists. Nothing
below was run or measured by this document's author. The only measured facts used are committed:
the golden locks `dogfood/contracts/golden/` (job-scheduler ef17cf8, 20 endpoints, `spec_hash`
9bcae0e24da9, file sha256 29983cf89b5d) and `golden-streak/` (Streak a0db033, 26 endpoints,
`spec_hash` d44b738522ea, file sha256 36f89f671c58), and `docs/results/data/2026-10-13-contracts{,-streak}/`.
Results: `docs/results/2026-10-13-contracts-exclude-results.md`, raw data in
`docs/results/data/2026-10-13-contracts-exclude/`. No LLM or API key is used (0 tokens).

**The finding.** Experiment 2 (finding 3) showed that layer 3 exports test-harness routes as
contract. Streak's `GET /health` and `PUT /test/clock` are public and in the pinned lock.
job-scheduler's `POST /worker/run-once` and `POST /worker/schedule-tick` are internal triggers with
no shared prefix, so a `/v1`-style prefix scope would not help.

**The decision (the orchestrator's; not reopened here).** Option (a): the provider declares an
explicit exclusion list in the workspace manifest, next to the other layer-3 provider settings:
`[[project]] exclude = ["GET /health", "PUT /test/clock"]`. Reasons: no spec-language change
(`docs/lattice-language-rules.md` and `lattice/spec/` untouched); explicit and reviewable; works
for both providers; opt-in (no key = exactly today's behaviour).

## 2. Decisions (every edge case, fixed before coding)

1. **Key.** `exclude` on a `[[project]]`: a TOML array of `METHOD /path` strings. Allowed on any
   project; it acts only where that project is the `to` of a link with a `contract` key, for all
   of that provider's consumers alike. A project that is never a provider gets no warning.
2. **Normalisation = `parse_endpoint`, the import rule.** Method upper-cased and one of
   `HTTP_METHODS`; digit, UUID and `{name}` segments become `{}`; trailing slash dropped;
   surrounding whitespace tolerated. The path keeps its case (`GET /Health` is not `GET /health`:
   unmatched, decision 8). `WsProject.exclude: tuple[str, ...] | None` holds the normalised keys in
   declared order.
3. **Validation: one error `bad_exclude` per project**, its message joining every reason with
   `"; "` (as `bad_imports`): `project P: exclude must be a list of 'METHOD /path' strings`;
   `project P: exclude entry #N: <parse_endpoint's reason>` (not a string, no method or path,
   non-HTTP method, no leading `/`, query string, empty segment); `project P: exclude names KEY
   twice` (equal after normalisation). As for `bad_type`/`bad_contract`, the project and its links
   stay listed and only the key is dropped. So `workspace validate` exits 1, `contracts check` and
   `pin` exit 2 with no link run, and the API and MCP answer the problem with no links.
   `exclude = []` is valid: kept as `()`, shown by `show`, excludes nothing.
4. **The filter applies once: in the export, before hashing.** `build_export(project, ref,
   exclude)` takes `export_spec(spec)`, drops the listed endpoints, adds `excluded`, and only then
   computes `spec_hash`. `pin`, `evaluate` (BROKEN/GAP), `diff_exports` (CHANGED), the live gaps,
   MCP and the API all read that export, so no second filter can disagree. The spec is not
   filtered: a live replay still sends an excluded endpoint as an earlier setup step of a
   scenario. The exclusion governs the contract, not the app or the spec.
5. **What the export carries.** `excluded` (sorted, de-duplicated normalised keys, matched or not)
   is present only when the list is non-empty. With no key or `[]` the export has exactly today's
   keys (`basis`, `endpoints`, `error_body`, `format`), so neither golden nor hash (9bcae0e24da9,
   d44b738522ea) can move. `excluded` is inside the hashed dict: changing the list changes
   `spec_hash`, the lock entry and the diff (decision 7), never silently. `FORMAT` stays 1
   (absence = the old meaning); `read_lock` still checks only the hash. The version stays
   `{spec_hash, git_ref, dirty}`: when only the exclusion changes, `spec_hash_changed` is true and
   `version_changed` false (the manifest is not in the provider's repository).
6. **A consumer importing an excluded endpoint is ALWAYS BROKEN `endpoint_missing`, pin or no
   pin** (orchestrator decision, 2026-10-13, amending the author's draft, which made it a GAP when
   the pin did not hold it). Item `""`; the detail is exactly `the provider excludes it from its
   contract (exclude in the workspace manifest)` ("EX" below). Why: "BROKEN needs the pin" is the
   owner's rule for OBSERVED facts, which are a lower bound (a field the spec never showed is a
   GAP, not a break). An exclusion is a STATED fact: the provider has said the route is not part of
   its contract, so a reliance on it contradicts the provider whether or not the consumer ever
   pinned it, and passing silently (exit 0) would defeat the purpose of the exclusion. `pin` then
   refuses without `--force`, and `--force` does not launder it: the next check is still BROKEN. No
   new finding kind (the kind and the JSON enumerations stay). The rule for observed facts is
   unchanged. Implementation consequence: `evaluate` must report an import of an excluded endpoint
   as BROKEN even when there is no pinned export (first pin).
7. **Two new CHANGED kinds** (item `""`, `relied` as today): **`exclusion_added`** (`the provider
   now excludes it from its contract`), one per key in current minus pinned `excluded`
   (`.get("excluded", [])`), absorbing the `endpoint_removed` of the same key; **`exclusion_removed`**
   (`the provider no longer excludes it from its contract`), the reverse, absorbing
   `endpoint_added`. `_folded` also folds `exclusion_added` into a BROKEN `endpoint_missing` there.
   The invariant "the hash differs exactly when the diff is non-empty" holds. No new text note.
8. **A key naming no exported endpoint** (absent, or with no 2xx step, such as job-scheduler's 405
   probe `PATCH /applications/{}/jobs/{}`) is kept in `excluded` (the declaration is what is
   pinned) and gives the **warning `exclude_unmatched`** (`P: exclude names KEY, which its export
   does not hold (no 2xx step in its spec)`), once per (provider, key), sorted, after the manifest
   problems, in `check` and `pin` (CLI, MCP, API); not in `workspace validate`, which loads no
   spec. A warning, not an error: excluding nothing is safe, while an error would break every
   consumer the day a provider deletes a route it had excluded. The `export` output carries
   `exclude_unmatched` (a list, possibly `[]`) at top level, outside the hashed `export`, only when
   the list is non-empty.
9. **Subprocess.** `python -m lattice.contracts export --project DIR --spec REF --exclude "GET
   /health" --exclude ...` (repeatable, validated like the key; exit 2 with the reason);
   `compute_export(project, ref, exclude=())` passes it. `export NAME` uses the manifest's list,
   and `--exclude` with NAME exits 2 (`the exclusion of a workspace project comes from its
   manifest`), so `export NAME` is always what `check` uses. `_Session`'s cache key becomes
   `(path, ref, exclude)`.
10. **Live.** `run_live` already receives the current export: an import whose endpoint is in its
    `excluded` is not sent to the child, and is listed in `not_replayed` with the reason
    `excluded by the provider (exclude)`; `replayed` counts the rest. No new key in the live
    result. The job's `settings` leave out `exclude`, as they leave out `env`.
11. **Workspace output.** `settings()`/`to_dict` emit `exclude` only when not None, so manifests
    without it are byte-identical (X8). `show` prints `exclude = [...]` via `json.dumps`, as for
    `env`. `GET /api/workspace` builds its rows by hand: unchanged except that `bad_exclude` can
    appear in `problems`.
12. **Parity.** MCP `contract_check` and `GET /api/contracts` call `run_check`: no code change (X7).
13. **Docs.** Module docstrings of `workspace.py` and `contracts/{export,check,__main__,live}.py`;
    `lattice/studio/API.md` one paragraph in v1.17 (no version bump, no mock change: keys and types
    unchanged, new values are 2 kinds and 2 codes). `docs/lattice-reference.md` never mentions
    layer 3 (0 matches): unchanged.
14. **Runners (disclosed).** `streak_probes.py` refuses to run when `git diff 573aafc -- lattice/`
    is non-empty; it gains `--allow-code-diff`, recording the diff stat in `summary.json` instead
    (the 32 probes untouched). The new `dogfood/contracts/exclude_probes.py` reuses `probes.Ctx` and
    `streak_probes.SCtx`, adding one `exclude` line to their manifests. `contracts_mutants.py` gains
    XM1-XM12, with `golden-streak/` and `streak-widget/` in `COPY`.

## 3. Probes

Global rules: each probe runs **10 times**, canonical output (`elapsed_s` and scratch paths
scrubbed) identical 10 of 10. Clones: job-scheduler ef17cf8 with `spec = "spec.tickets:t5"`
(manifest as experiment 1) and Streak a0db033 with `spec:SPEC` (as experiment 2); consumers are
copies of `ci-bot` and `streak-widget`. "Pinned with E" = a `pin` under list E. EX = decision 6's
detail text.

**X0, no-exclude regression** (both providers). (a) No key: `export` gives an `export` member with
canonical sha256 9bcae0e24da9… / d44b738522ea… and top-level keys exactly `export`, `spec_ref`,
`version`; `pin` writes lock bytes with sha256 29983cf89b5d… / 36f89f671c58… (the golden files).
(b) `exclude = []`: the same bytes as (a). (c) `check --json` equals the committed B0 run docs of
both experiments. (d) `probes.py --runs 10` 35/35; `streak_probes.py --runs 10 --allow-code-diff`
32/32. Pass: 8 of 8 byte comparisons, (c) 2 of 2, (d) both.

**X1, job-scheduler, `exclude = ["POST /worker/run-once", "POST /worker/schedule-tick"]`.** Both
are among B0's 20, so: **18** endpoints, public 1, token 4, unknown **13**; `excluded` the 2 keys
sorted; `exclude_unmatched` `[]`; `spec_hash` ≠ 9bcae0e24da9 and equal to the sha256 recomputed
from the embedded export; `export scheduler` byte-identical to `export --project <clone> --spec
spec.tickets:t5 --exclude ... --exclude ...`. Against the golden lock: exit 1, 0 BROKEN, exactly 2
CHANGED `exclusion_added` (relied false: ci-bot imports neither), `spec_hash_changed` true,
`version_changed` false, 1 gap (`access_token`). `pin` exits 0 ("18 endpoint(s)"); the next check
exits 0, 0 findings, 1 gap. Pass: all.

**X2, Streak, `exclude = ["GET /health", "PUT /test/clock"]`.** **24** endpoints: public **3**
(login, refresh, register), token 21, unknown 0. Against the golden: exit 1, 2 CHANGED
`exclusion_added` (relied false), 0 BROKEN. After a re-pin: exit 0, 0 findings, the 2 B0 gaps in
order (`cursor @ GET /v1/habits`, `access_token @ POST /v1/auth/login`).

**X2b, normalisation.** `["get /health/", " PUT  /test/clock "]` gives an export byte-identical to
X2's; `["GET /v1/habits/{id}/progress"]` and `["GET /v1/habits/7/progress"]` give byte-identical
exports (25 endpoints, `excluded` `["GET /v1/habits/{}/progress"]`). Pass: 2 of 2.

**X3, a consumer that imports an excluded endpoint.** The consumer is the widget plus one
`[[import]]`: `endpoint = "PUT /test/clock"`, `status = [200]`, `auth = "public"`,
`request_fields = ["now"]`.

| step | prediction |
|---|---|
| no key, golden lock | exit 0, 0 findings, 2 gaps: the import holds today (finding 3 itself) |
| pin with no key | lock bytes = the golden-streak file (a lock does not depend on imports) |
| pinned BEFORE, then X2's list | exit 1; BROKEN `endpoint_missing` @ PUT /test/clock, item `""`, detail EX; exactly 1 CHANGED (`exclusion_added` @ GET /health, relied false; the clock's is folded); 2 gaps; `spec_hash_changed` true |
| `pin` | exit 1 REFUSED; lock bytes unchanged |
| `pin --force`, then check | `pin` exit 0 (forced, lock written); check exit 1; BROKEN `endpoint_missing` @ PUT /test/clock (detail EX) STILL reported; 2 gaps (the B0 pair): `--force` does not launder it |
| pinned AFTER (fresh copy, no lock) | `pin` exit 1 REFUSED, no lock written; `pin --force` exit 0 writes it (lock bytes = the forced lock above); check exit 1, the same single BROKEN (detail EX), 2 gaps |

Pass: 6 of 6.

**X4, changing the list on a pinned consumer.** The widget is pinned with `["GET /health"]`. In
every row `version_changed` is false and `git_ref` is a0db033.

| change | prediction |
|---|---|
| a: add `"PUT /test/clock"` | exit 1; exactly 1 CHANGED `exclusion_added` @ PUT /test/clock relied false (no `endpoint_removed`); `spec_hash_changed` true |
| b: `exclude = []` | exit 1; exactly 1 CHANGED `exclusion_removed` @ GET /health (no `endpoint_added`) |
| c: `["get /health/"]` | exit 0; `spec_hash_changed` false |
| d: add `"GET /v1/nope"` | exit 1; 1 CHANGED `exclusion_added` @ GET /v1/nope; 1 warning `exclude_unmatched` |

Pass: 4 of 4.

**X5, validation.** Each value is set on the Streak project. Prediction for each row: exactly 1
`bad_exclude` error; `workspace validate` exits 1 (`--json` `ok` false); `contracts check` exits 2
with `links` `[]`; the project is still listed in `show --json`, without `exclude`.

| # | value | reason in the message |
|---|---|---|
| 1 | `"GET /health"` | not a list |
| 2 | `42` | not a list |
| 3 | `[42]` | entry #1 must be a string |
| 4 | `["GET"]` | entry #1 must be 'METHOD /path' |
| 5 | `["GET /health", "get /health/"]` | names GET /health twice |
| 6 | `["GET /v1/habits?limit=1"]` | a query string is not part of an endpoint |
| 7 | `["FETCH /health"]` | not one of GET, POST, ... |
| 8 | `["GET health"]` | the path must start with '/' |

Two CLI cases, `export streak --exclude "GET /health"` (decision 9) and `export --project <clone>
--exclude "GET"`, are each predicted to exit 2. Pass: 8 of 8 rows (16 exit codes) and 2 of 2.

**X6, an exclusion the spec does not hold.** (a) Streak `["GET /health", "PUT /test/clock",
"GET /v1/nope"]`, pinned with the same list: 24 endpoints; `excluded` the 3 keys sorted;
`exclude_unmatched` `["GET /v1/nope"]`; check exit 0, 0 findings, 2 gaps, `problems` = exactly 1
warning `exclude_unmatched`; `workspace validate` exit 0, no problem. (b) job-scheduler
`["PATCH /applications/{id}/jobs/{id}"]` (the 405 probe): 20 endpoints, 1 warning, `spec_hash` ≠
the golden's; against the golden lock exit 1 with 1 CHANGED `exclusion_added` @ PATCH
/applications/{}/jobs/{} relied false. Pass: both.

**X7, parity.** On X2 (pinned after), X3 (pinned before) and X6a, MCP `contract_check` (stdio) and
`GET /api/contracts` (the real server) each equal `contracts check --static-only --json`
(`elapsed_s` excluded). Pass: 6 of 6.

**X8, manifests without the key.** On 3 manifests (X1's and X2's with the `exclude` line removed,
experiment 1's `GOOD_MANIFEST`), `workspace show`, `show --json`, `validate`, `validate --json`,
`impact NAME`, `impact NAME --json` are byte-identical (stdout, stderr, exit) to the code at
7358be2, extracted by `git archive 7358be2 lattice` into the scratch directory. Pass: 18 of 18.
Reported, not in the pass: 7358be2 validates X2's manifest with `exclude` at exit 0 with no
problem (unknown keys are ignored). Prediction: yes.

**X9, live** (job-scheduler, `contract = "live"`, pinned with the list). (a) The two worker keys:
static 0 findings, 1 gap; live `replayed` **7**, `not_replayed` `[]`, 0 findings, `verified_live`
`[access_token]`, the 7 replays (endpoint, scenario, step, answered) equal to experiment 1's L1;
exit 0. `GET /applications/{}/runs/{}` still answers 200: its scenario's worker step is still sent
(decision 4). (b) (a) plus `"POST /applications/{app}/runs/{run}/cancel"`: static 17 endpoints, 0
**1 BROKEN** (`endpoint_missing` @ the cancel endpoint, detail EX), 1 gap (`access_token`); `pin` needs `--force` here; exit 1;
live `replayed` **6**, `not_replayed` exactly `[{"endpoint": "POST /applications/{}/runs/{}/cancel",
"reason": "excluded by the provider (exclude)"}]`, 0 findings, `verified_live` `[access_token]`,
replays = L1's minus the cancel one; the live part itself has 0 findings. Pass: both.

**S, safety.** The real job-scheduler and Streak repositories have the same HEAD and
`git status --short` before and after the run, and 0 files newer than the start marker.

## 4. Mutation plan (`dogfood/contracts_mutants.py`)

Each mutant is one line on a scratch copy and must be killed by new tests in `tests/contracts` or
`tests/workspace` (the runner's suite); the probe named also kills it. Prediction: **11 of 11
killed**, and CM1-CM11 still 11 of 11 (a CM patch that no longer applies verbatim is re-anchored
and disclosed).

| id | mutant | killed by (predicted) |
|---|---|---|
| XM1 | filter not applied: `build_export` keeps every endpoint (`excluded` still set) | X1 (20, not 18), X2, X9b |
| XM2 | filter after hashing: `spec_hash` over the unfiltered export | X1 (the re-pinned check exits 2 `bad_pin`) |
| XM3 | normalisation skipped: raw strings stored and compared | X2b (`get /health/` matches nothing), X4c |
| XM4 | validation off: bad entries dropped, no problem | X5 (validate exits 0, not 1) |
| XM5 | duplicate check removed | X5 row 5 |
| XM6 | excluded imports treated as satisfied (`evaluate` skips them) | X3 (no BROKEN in any step) |
| XM12 | an excluded import is BROKEN only when the pin held it (the draft's rule) | X3 pinned AFTER (exit 0 instead of 1), X9b |
| XM7 | `excluded` not recorded in the export | X4d (silent, exit 0), X6b (golden hash) |
| XM8 | diff ignores `excluded`: `endpoint_removed`, not `exclusion_added` | X4a, X1 |
| XM9 | live replays excluded imports | X9b (`replayed` 7, not 6) |
| XM10 | `exclude_unmatched` never emitted | X6a, X4d |
| XM11 | `WsProject.to_dict` always emits `exclude` (`[]` when None) | X8 (`show --json`) |

## 5. Named risks

1. **Hiding a real endpoint by mistake.** An exclusion is a promise by the provider; a wrong entry
   drops a real endpoint from every consumer's contract. Mitigations: `excluded` is in every
   export and lock; an added entry is a CHANGED; for an importing consumer pinned before, a BROKEN
   that `pin` refuses (X3); a consumer that imports an excluded route is BROKEN whether it pinned before or after (decision 6).
2. **Silent drift of the exclusion.** X4 shows every list change is visible. Residual: an
   unmatched key kept "for later" hides a route the spec adds later; the only signal is that the
   `exclude_unmatched` warning disappears.
3. **The list lives in the workspace manifest, not the provider's repository.** The manifest is
   workspace-level: it usually sits above several repositories and is edited by whoever assembles
   the workspace, possibly a consumer's team. "Provider-declared" is so in name only: an exclusion
   change moves no provider `git_ref` or `dirty`, and a consumer could exclude a provider's
   endpoints from its own view. A later move into the provider's repository would need a
   provider-side file read inside the export subprocess, a precedence rule against the manifest
   key (or its removal), and a `bad_exclude` equivalent there. Because the export records the
   effective list, not its source, a move that keeps the list is hash-neutral (no CHANGED; locks
   stay valid).
4. **The goldens.** Any accidental change to the no-key export (an `excluded: []` emitted, a
   reordered key) moves 9bcae0e24da9 / d44b738522ea. X0 pins both byte for byte, plus the full 35
   and 32 probe runs. `streak_probes.py` needs one disclosed flag; any other runner edit is a
   finding.
5. **Builder bias.** The author chose the semantics and predicts them, having read `export.py` and
   `check.py`; the predictions are deterministic consequences of section 2 and the committed B0
   data. Agreement shows the code matches this document, not that the design is right. The
   mutant-killing tests are written by the same builder.
6. **Excluded imports depart from "BROKEN needs the pin".** The owner kept that rule for observed
   facts; an exclusion is a stated fact, so an import of an excluded route is BROKEN even with no
   pin (decision 6). Cost: a consumer that already relies on a harness route cannot pin without
   `--force` until it drops the import, and `--force` leaves it BROKEN. This is deliberate, and
   it is one more place where `evaluate` behaves differently from the observed-fact path.
7. **The exclusion governs the contract only.** Excluded routes remain in the spec and the app;
   live replays still send them as setup steps (X9a); nothing stops a consumer calling them.

## 6. Blind vs post hoc, and verification

Sections 2-4 are fixed now. A failed prediction is a **finding**, reported with its output, never
fixed silently; a later fix is listed as post hoc and the blind numbers stay as first measured.
Message texts, kind names or runner changes made after this point go under "Post-hoc observations"
with their reasons.

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-exclude PYTHONDONTWRITEBYTECODE=1
cd /Users/vivekgade/Documents/lattice-exclude
uv run pytest tests/contracts tests/workspace tests/mcp tests/studio -q      # 0 failures (baseline re-measured at 7358be2)
uv run python dogfood/contracts/exclude_probes.py --runs 10 --scratch "$SCRATCH/x" \
  --out docs/results/data/2026-10-13-contracts-exclude                       # X0-X9, S
uv run python dogfood/contracts/probes.py --runs 10 --scratch "$SCRATCH/c1" --out "$SCRATCH/c1d"   # 35/35
uv run python dogfood/contracts/streak_probes.py --runs 10 --allow-code-diff \
  --scratch "$SCRATCH/c2" --out "$SCRATCH/c2d"                               # 32/32
uv run python dogfood/contracts_mutants.py                                    # control passes; 23/23 killed
dogfood/studio/check.sh                                                       # PASS, 0 findings
git diff --stat 7358be2 -- lattice/   # only workspace.py, contracts/{export,check,live,__main__}.py, studio/API.md
git diff 7358be2 -- lattice/spec docs/lattice-language-rules.md   # empty
```

**Pass for the change:** 0 test failures; X0-X9 and S as stated, each 10 of 10 identical; 23 of
23 mutants killed; `check.sh` PASS; the two diff scopes as stated.

`$SCRATCH` is the session scratchpad. The committed data of experiments 1 and 2 is not rewritten.
