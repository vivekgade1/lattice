# Layer 3 (contracts between projects) on a second provider, Streak: results

Date: 2026-10-13. Branch `np-contracts-streak` (worktree `lattice-contracts-streak`, from `next-phases`
573aafc), commits 24bbd95 (prereg) and f7af097 (runner, consumer, golden lock, raw data).
Pre-registration: `docs/results/2026-10-13-contracts-streak-prereg.md` (section numbers below are
its own). Raw data: `docs/results/data/2026-10-13-contracts-streak/` (`summary.json`, one JSON per
probe, `run-table.txt`). The recorded run's `preflight.code_head` is 24bbd95; f7af097 adds only the runner,
the consumer, the golden lock and the data. No LLM and no API key were used (0 tokens). Streak was
only read (S1); job-scheduler was never opened.

## Status and scope

The question (prereg section 1): does layer 3 hold on a second, differently shaped provider
**without code changes**? **Answer: yes, on this pair. 32 of 32 probes match their pre-registered
predictions, 0 prediction misses, 0 layer-3 defects found, and `git diff 573aafc -- lattice/` is
empty.** The provider is Streak at a0db033: a Studio-format `spec.py` (156 scenarios, 0
`Contract`s), a Go + Gin + MySQL app that is never run, UUID and int ids, cursor-paginated
envelopes, 404 (never 403) for other users' rows, and `json_key(error)` error bodies. The consumer
is `streak-widget`, written for this experiment. The check is **static only**.

## What was added (no change under `lattice/`)

- `dogfood/contracts/streak-widget/`: `client.py` (7 calls W1-W7; never run by the checks),
  `lattice.imports.toml` (top-level keys only, `as_user = "ann"` on the 6 token imports), README.
- `dogfood/contracts/golden-streak/lattice.contracts.lock.json`: pinned against a0db033 (26
  endpoints, `spec_hash` d44b738522ea). `dogfood/contracts/streak_probes.py` (1653 lines): the
  runner, reusing `probes.py` helpers. The data directory: 34 files, 924K.

## Blind results: probes against the pre-registered predictions

Every probe ran 10 times, canonical output identical 10 of 10 (S1 is a single measurement; T1's
timing is not deterministic). Predictions are quoted from `summary.json`.

| id | predicted | observed | pass |
|---|---|---|---|
| B0 | 26 endpoints; public 5 / token 21 / unknown 0; `error_body` `json_key(error)`, observed; statuses = R1 26/26; `GET /v1/habits` response = envelope only, query [include_archived, limit]; ann granted 21, ben exactly 10, `refused` [] on 26; `/health`, `/test/clock` exported public; pin 0 (26); check 0 findings, 2 gaps in order | all 29 sub-checks true; bodies object 19 / empty 7; gaps `request_field_unobserved cursor @ GET /v1/habits`, `response_field_unobserved access_token @ POST /v1/auth/login` | yes |
| D1 endpoint removed | BROKEN `endpoint_missing` GET /v1/home; no CHANGED | same, exit 1 | yes |
| D2 status 200 -> 204 | BROKEN `status_not_observed` 200 @ POST /v1/nudges/{}/dismiss; CHANGED `status_added` 204 relied | same, exit 1 | yes |
| D3 response field renamed | BROKEN `response_field_missing` expires_in @ login; CHANGED 3 (expires_in_s added @ login relied; added, and expires_in removed, @ refresh unrelied) | same, exit 1 | yes |
| D4 request field renamed | BROKEN `request_field_missing` password @ login; CHANGED `field_added` request passphrase relied | same, exit 1 | yes |
| D5 access removed from ann | BROKEN `auth_changed` as_user:ann @ GET /v1/home; CHANGED `status_added` 404 relied | same, exit 1 | yes |
| D6 next_cursor -> next | BROKEN `response_field_missing` next_cursor @ GET /v1/habits; CHANGED 5 (next @ habits relied; next added and next_cursor removed @ GET /v1/habits/{}/completions and GET /v1/nudges, unrelied) | same, exit 1 | yes |
| D7 /v1/ -> /v2/ | BROKEN `endpoint_missing` x 7 (one per import); CHANGED 41 = 17 `endpoint_removed` + 24 `endpoint_added`, unrelied; 0 gaps; still 26 endpoints | same, exit 1 | yes |
| D8 endpoint added | CHANGED `endpoint_added` GET /v1/me/stats unrelied; no BROKEN | same, exit 1 | yes |
| D9 field type 900 -> "900" | **MISS**: 0 findings, `spec_hash` equal, exit 0 | missed as predicted | yes |
| D10 required field | **under-classified**: CHANGED `field_added` request client_tz relied; no BROKEN | same, exit 1 | yes |
| D11 ownership (ben) | CHANGED `auth_tightened` as_user:ben @ GET /v1/habits/{}/progress relied; no BROKEN | same, exit 1 | yes |
| D12 nested item key | **MISS**: 0 findings, `spec_hash` equal, exit 0 | missed as predicted | yes |
| D13 relied noise (429) | CHANGED `status_removed` 429 @ GET /v1/me relied; no BROKEN | same, exit 1 | yes |
| C1-C6 controls | C1 exit 0, 2 gaps, `version_changed` false; C2 file sha changes, `spec_hash` equal; C3 156 scenarios reordered, equal; C4 exactly 1 CHANGED `field_added` response id @ POST /v1/auth/register unrelied; C5 (101 -> 777) and C6 (every UUID) equal | all as predicted | 6/6 |
| W1-W4 opt-in | W1 no key: checked 0, `static`: exit 2 `export_failed`; W2 = B0, `live` null; W3 `live`: exit 2 `unsupported_database`, nothing started; W4 6 outputs byte-identical to c0cdefc | all as predicted; W3 shim calls 0; W4 6/6 | 4/4 |
| V1-V6 pin and lock | V1 10 pins = golden, hash recomputed, no scratch path or timestamp; V2 `no_pin`; V3 pin refuses after D1, `--force` gives a third gap `endpoint_unobserved GET /v1/home`; V4 README commit; V5 `dirty`; V6 `bad_pin` | V1 1 distinct lock in 10, d44b738522ea recomputed equal; V4 `git_ref` a0db033a -> 214cc5db, same hash; V6 recorded 044b738522ea, computed d44b738522ea | 6/6 |
| S1 safety | HEAD a0db033, ` M studio.toml`, `studio.toml` sha unchanged, 0 newer files | before and after identical (sha 540211a64cb5); 0 newer files | yes |
| T1 cost | static median <= 10 s (pred. <= 1 s) | 0.33 s wall (0.29 s `elapsed_s`); export 0.33 s wall | yes |

Totals (`summary.json`): **32 of 32 pass**; D-series core 13 of 13 and full CHANGED set 13 of 13;
211 sub-checks across the 32 probes, all true. In every D probe `version_changed` was true and the
gap list equalled B0's, except D7 (empty, as predicted). Of the 10 drifts that break the widget's
code (D1-D7, D9, D10, D12), static flags 7 as BROKEN, D10 only as relied CHANGED, and misses D9 and
D12, exactly as predicted. Whole run 177.0 s; no leftover process or temp entry; scratch removed.

## Findings

**1. 0 prediction misses, and layer 3 generalised without code changes.** The same export and
check rules, unchanged since 573aafc, handled a Studio-format spec, `/v1` paths, UUID and int ids,
cursor envelopes, 404-for-others and `json_key(error)` bodies. What this shows: the existing rules
carry over to a new spec shape. What it does not show: that an independent implementation would
agree (prereg risk 2). The predictor had read `export.py` and `check.py` before predicting, and the
layer-3 code and the first experiment's predictions were known to the predictor.

**2. Why the agreement is this complete.** The predictions came from a hand reading of the 1829
lines of `spec.py` and matched on every sub-check (29 in B0). This level of agreement mostly
reflects that the rules are deterministic functions of the spec and that the predictor had read
the rules; it is evidence about the predictor's reading as much as about layer 3. The two places
where agreement was least trivial:
- **ben's exact granted set of 10** (B0): one missed or extra ben 2xx step among 156 scenarios,
  most generated by `access_matrix`, would have changed it. The least visible member,
  `GET /v1/habits/{}/progress`, comes from one step at spec line 1370.
- **D7's counts**: 7 BROKEN, 17 + 24 CHANGED, 0 gaps, still 26 endpoints. This depends on how
  `template_path` treats `v2` (not an id), on folding the 7 relied removals into BROKEN, and on
  both gap endpoints disappearing. The prereg fixed the counts; the itemised expected set was
  derived by the runner from the clean export (post hoc, procedure).

**3. Test-harness routes are part of the contract (the predicted, named finding).** `GET /health`
(public, status [200], no fields) and `PUT /test/clock` (public, status [200], request field
`now`) ARE exported and are in the pinned golden lock. Layer 3 has no notion of internal routes:
only "no 2xx step" excludes an endpoint. A consumer could import `PUT /test/clock` and its check
would pass, and any edit to these routes is CHANGED. **Not fixed here.** A fix is a design decision
for the owner: a provider-declared exclusion list, a path-prefix scope (export only `/v1`), or a
spec-level flag marking test-harness routes.

**4. Observations that are NOT misses (known blind spots, re-confirmed on Streak).**
- D9 (a type change) and D12 (an item key inside an envelope) are misses by design (names only,
  top-level keys only): the widget's TTL arithmetic and `h["name"]` would break unseen. D10 (a new
  required request field) is only a relied CHANGED: required-ness is not exported.
- `refused` is empty on all 26 endpoints, because Streak answers 404, never 403 (`403` occurs 0
  times in `spec.py`). A handle that loses access shows only as `auth_tightened` (D11) or, for the
  importing handle, `auth_changed` (D5).
- Headers are invisible (`X-Client-Version` -> 426, `Retry-After`). `cursor` is a permanent gap:
  it appears as a query name only in a 422 step of `GET /v1/habits`.
- The widget's nested reads (`items[].id`, `items[].name`, `nudges[].id`) cannot be expressed in
  the import format (listed as comments in `lattice.imports.toml`).
- Streak's spec has 0 `Contract`s: every exported fact is observed, a lower bound.
- Relied CHANGED noise: D13 (an unrelated 429 example) and D11 (ben-only, tagged relied because
  `relied` is endpoint-level) both exit 1.

**5. Live checking is refused cleanly on Streak (W3).** A `contract = "live"` link exits 2 with
`unsupported_database`, findings and gaps []; nothing started (docker/go/mysql shims saw 0 calls,
`bin/` absent before and after); `--static-only` exits 0, pin exits 0. This path had no test in
`tests/contracts` (prereg risk 8). **Live checking of a Go/MySQL provider is NOT built and was not
run; this experiment is static only.**

**6. Cost.** Static check of one link: 0.33 s wall median over 10 runs for a 156-scenario spec
(first pair 0.20 s); predicted <= 1 s, threshold 10 s.

## Post hoc (not pre-registered)

**Script corrections** (`summary.json` `post_hoc.script_corrections`; none touched a prediction or
a pass rule; two complete runs, before and after them, both gave 32/32):
- V1 "no timestamp": a debug run (`--runs 2`) flagged 7 "timestamps" because a loose regex matched
  the field name `timezone`. It now looks for ISO date-times and time-named JSON keys.
- W3 shims: the shim scripts and their log were in the run's TMPDIR, which made
  `leftover_tmp_entries` non-empty; they now live under the scratch root.
- V2 and V6 observed text: the message was cut before the scratch path was replaced by `$WORK`, so
  a partial scratch path showed in the data; the text is now scrubbed before the cut.

**Procedure notes** (`post_hoc.procedure`): `str.replace` edits assert their count first (E5a's
old string ends with a newline); `as_user:` is normalised to a space, nothing else; empty items for
`endpoint_missing` and the endpoint/status/auth CHANGED kinds; D7's expected CHANGED set is derived
from the clean export's 24 `/v1/` endpoints minus the 7 imported; R1's 26 status lists are copied
from the recon file; W3's "nothing started" rests on shims that log and exit 127, a 20 ms `ps`
sampler (7 command lines, 0 provider-like; the check's own `lattice.contracts.live` child is
expected) and `git status --ignored`; S1 uses `--no-optional-locks`; T1 wall times include
interpreter start; W4 compares against `lattice-contracts-base` (HEAD c0cdefc).

## Orchestrator verification

| Check | How it was checked | Result |
|---|---|---|
| Prereg edit strings | all 20 occurrence counts (incl. the line-ending anchor E5a and `403` = 0) and 12 cited line numbers checked against Streak a0db033 `spec.py` before the prereg commit 24bbd95 | 0 mismatches; the prereg is 445 lines (aim 350; accepted) |
| Runner audit | D1-D13 and B0 predictions in `streak_probes.py` compared line by line with the prereg tables | equal: BROKEN/CHANGED sets and `relied` flags; D9 and D12 MISS, D10 under-classified, D11 ben-only, D13 noise; B0 26 endpoints, public 5 / token 21 / unknown 0, ben exactly 10, the two ordered gaps |
| Independent re-run | `streak_probes.py --runs 10` from scratch | 32/32 pass; T1 static median 0.33 s wall (0.29 s `elapsed_s`); `git diff 573aafc -- lattice/` 0 lines |
| Unchanged suite | `uv run pytest tests/contracts tests/workspace -q` | 214 passed |
| Streak untouched | `git status --short` and sha256 of `studio.toml`, before and after the re-run | ` M studio.toml` and the same sha256 |
| Artifact scan | golden lock searched for scratch paths, home paths, timestamps; ruff check and format on `streak_probes.py` and `client.py`; data directory size and count | 0 / 0 / 0; clean; 924K, 34 files |
| Process | `summary.json` `post_hoc.script_corrections` read; the runner's two complete runs are its own report, and the orchestrator's from-scratch re-run is a third complete run | 3 corrections, none touching a prediction or a pass rule; the orchestrator's run 32/32 |

## What this does not show

- **Independence.** The predictor had read the layer-3 rules and the first experiment; a pass
  shows the rules carry over, not that an independent implementation would agree (risk 2).
- **App behaviour.** The Go/MySQL app was never run. The D-series measures spec-to-spec derivation
  only; live checking beyond SQLite does not exist.
- **Breadth.** Two pairs, both with a Lattice spec in Python from the same lab; nothing about
  third-party specs. Ten runs show determinism only.
- **A real consumer.** `streak-widget` was written for this experiment by the author of the drifts;
  `expires_in` and `items[].name` are read partly so that D3, D9 and D12 have a reliance to hit
  (risk 1). Its nested reads are not checked at all.
- **Types, required-ness, headers, rate-limit policy, nested structures, `Await`s, the nudger**
  (prereg section 3): not in the export.

## What is next

- **Internal routes**: an owner decision on keeping test-harness routes out of the contract
  (declared exclusion, path-prefix scope or spec flag), then a post-hoc commit with its own probe.
- **Live checking beyond SQLite**: a harness that can start a Go/MySQL provider on a throwaway
  database, so that app drift on Streak can be seen.
- **Layer 4**: impact labels across links, linked pending questions and changes in consumers.
- **A real third-party consumer** (and, later, a provider whose spec Lattice did not write).
