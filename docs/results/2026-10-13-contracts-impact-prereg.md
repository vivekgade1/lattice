# Multi-project slice 4a (cross-project impact preview): pre-registration

## 1. Status

Date: 2026-10-13. Branch `np-impact` (worktree `lattice-impact`, from `next-phases` 67189af). Written
BEFORE any code of this change exists; nothing below was run or measured by its author. Predictions
come from committed data only: `docs/results/data/2026-10-13-contracts/` (job-scheduler ef17cf8 +
`ci-bot`), `.../2026-10-13-contracts-streak/` (Streak a0db033 + `streak-widget`), `.../2026-10-13-contracts-exclude/`
(`X2`, `X3`), the edit tables of `dogfood/contracts/{probes,streak_probes}.py`, and `lattice/contracts/check.py`
at 67189af. Results: `docs/results/2026-10-13-contracts-impact-results.md`. No LLM, no API key.

**Scope (owner-approved; not widened).** `lattice contracts impact PROVIDER --candidate REF|PATH
[--workspace PATH] [--json]`, MCP `contract_impact`, `GET /api/contracts/impact`. Advisory, read-only,
direct opted-in links, static only; no UI, change sets, consumer writes (4b) or drafting (4c).

## 2. Decisions (fixed before coding)

1. **Code.** New `lattice/contracts/impact.py` (`run_impact(ws, provider, candidate, *, base)`), a
   subcommand in `contracts/__main__.py`, the tool in `lattice/mcp/projects.py`, `impact_answer` in
   `lattice/studio/server.py`. It reuses `compute_export`, `parse_imports`, `read_lock`, `version_of`,
   `evaluate`, `changed_of` unchanged; `_check_link`/`run_check` are untouched. One layer-3 change:
   `git_version` (export.py) passes `--no-optional-locks`, so no export child can refresh a
   provider's `.git/index` (makes `check` read-only too; no output change; I6 needs it, I8 guards it).
2. **Exit codes.** 0 no contradiction, 1 at least one `contradiction`, 2 cannot run (no manifest, a
   manifest error, unknown provider, unknown or unloadable candidate, any row in error); 2 over 1
   over 0, as `check`. **`regression` alone exits 0**: the exit answers "does this candidate break a
   declared reliance?", and of the 5 regressions predicted below 3 are test-edit noise (job-scheduler
   D9, Streak D11, D13) and 2 under-classified real breaks (job-scheduler D8, Streak D10): exit 1 would
   flag routine spec edits, exit 0 hides the 2 (risk 2). `counts` lets a caller gate on regression.
3. **Candidate.** `X` naming an existing directory (CLI: relative to the cwd; MCP/API: to the
   manifest's directory) is a **path**, used as the provider project; a directory shadows a same-named
   ref. Otherwise a **git ref** of the provider's repository: refused if it starts with `-`; resolved by
   `git -C P --no-optional-locks rev-parse --verify --quiet X^{commit}`; the project's subtree
   (`rev-parse --show-prefix`) is materialised into a `tempfile.mkdtemp()` directory with `ls-tree -r
   -z SHA -- PREFIX` + `cat-file --batch` (raw blobs, symlinks kept, submodules skipped; no checkout,
   index or worktree), removed in a `finally`. Working tree, index, HEAD, refs, reflog: never touched.
4. **Candidate export = the live recipe.** `compute_export(candidate_dir, ref, exclude)` with the
   provider's manifest `spec` (else `spec:SPEC`) and manifest `exclude`: same spec ref, filter,
   `excluded` key and hash rule as `export NAME`. The exclusion lives in the manifest, so it is the
   same for current and candidate (a candidate cannot add or remove one). Candidate version: for a
   ref `{spec_hash, git_ref: SHA, dirty: false}` (the child's git answer for the temp dir is
   discarded); for a path, the child's `git_version` of it. Messages say `candidate REF`, never the
   temp path. `exclude_unmatched` warnings come from the candidate export.
5. **Baseline = the pin, unchanged.** The lock is read once by `read_lock` (every entry's hash
   checked, as `check`); its bytes and mtime are unchanged afterwards. With `P` its PROVIDER export and
   `C` the candidate's, a row is exactly `evaluate(imports, C, P)` + `changed_of(P, C, broken,
   imports)`: the `check` the consumer would get if the candidate shipped and nobody re-pinned. Not the
   current export: BROKEN needs the pin (owner rule), the pin is what the consumer declared, and a
   current baseline would hide a break already present. If the current export already differs from
   the pin, the row shows the whole pin -> candidate difference plus `current_label` (the current
   export's label against the same pin); the exit code follows the candidate (risk 3).
6. **Labels.** Any BROKEN (including an import of an excluded endpoint, `EXCLUDED_DETAIL`) ->
   `contradiction`; else any CHANGED with `relied: true` (including `error_body_changed` `*`, relied
   whenever there is an import) -> `regression`; else `unaffected` (unrelied CHANGED, gaps, nothing).
   Items `_folded` into a BROKEN appear nowhere. GAPs are listed and never move a label.
7. **Who is listed.** The links with `to = PROVIDER` and a `contract` key (either kind), one row per
   link, sorted by (consumer, contract). No key: not listed; transitive: not listed. `live`: evaluated
   statically, `live = {"status": "not_run", "reason": "static-only"}` (static link: null). A
   consumer with several providers: only its imports of PROVIDER are evaluated (`read_lock` still
   checks every entry: a bad one makes the row `bad_pin`). Zero imports of PROVIDER: `unaffected`,
   findings as `changed_of` gives them (unrelied, `provider` "", the same wart as `check`). No
   opted-in link: exit 0, `checked` 0, warning `no_consumers`, candidate not resolved or loaded.
8. **Errors.** Row (label null, `error {code, message}` as `_error_link`, other rows still run, exit
   2): `no_pin` (no baseline, never "unaffected"), `bad_pin`, `no_imports`, `bad_imports`. Whole call
   (no rows, exit 2, a `problems` error): manifest errors, `unknown_project`, `unknown_candidate`
   (names X; also when the provider is not in a git repository), `candidate_failed` (the child's last
   stderr line), `missing_argument` (API). Order: manifest, provider, links, candidate, candidate
   export, current export, rows. A failing current export only warns (`current_export_failed`).
9. **JSON** (`json.dumps(indent=1)`, every key always present): `format: 1`, `workspace`, `provider`,
   `candidate {input, kind: ref|path, resolved: SHA|abs path|null, spec_ref, version: {spec_hash,
   git_ref, dirty}|null, endpoints}`, `current` (`version_of` the current export, or null),
   `advisory: true`, `static_only: true`, `baseline: "pin"`, `checked`, `consumers`, `counts
   {contradiction, regression, unaffected, error}`, `problems`, `exit_code`, `elapsed_s`. A row:
   `consumer`, `provider`, `contract`, `label`, `error`, `imports`, `pinned`, `version_changed`,
   `spec_hash_changed`, `spec_ref_changed` (candidate vs pin, as `check`), `current_label`, `findings`
   and `gaps` (layer 3's shape: `class`, `provider`, `endpoint`, `kind`, `item`, `detail`, `relied` on
   CHANGED, `where` on fields; `_sort_key` order), `counts {broken, changed_relied, changed_unrelied,
   gaps}`, `live`.
10. **Text, determinism.** `impact of X (kind, <git_ref 8> <spec_hash 12>) on PROVIDER: advisory,
    static only, baseline = each consumer's pin; provider now <short>`; per row `CONSUMER  LABEL|ERROR
    [code]  N import(s), pinned <short>, now: <current_label>` + findings and gaps as `check` prints
    them; problems; `N consumer(s): a contradiction, b regression, c unaffected, d error`. No timing in
    the text, no timestamp but `elapsed_s`, no temp path, sorted rows: same inputs, same bytes.
11. **Advisory, read-only.** Nothing reads its result (no gate, pin, check, accept or build waits
    on it). Writes nothing outside its own temp directory (removed before returning): no lock,
    workspace file, state directory, ledger, events log, git object, ref, index or reflog.
    Subprocesses: read-only `git` (`rev-parse`, `ls-tree`, `cat-file`) and the layer-3 export
    children (`python -P -m lattice.contracts export`, at most 2 per call: candidate, then current),
    which run only `git rev-parse`/`status --no-optional-locks`. No network, no app, no LLM. The
    brief's "no subprocess other than the export child" is read as this allow-list (a ref needs git).
12. **MCP `contract_impact {provider, candidate}`** (required strings, else `ToolError`), in
    `WORKSPACE_TOOLS`: listed only with a workspace, allowed under `--readonly`, manifest re-read per
    call, returns the CLI JSON. A test pinning the exact workspace tool list is updated (disclosed).
13. **API `GET /api/contracts/impact?provider=&candidate=`** (also `/p/<name>/...`; last repeated
    value wins; `POST` 404). Always HTTP 200, body = the CLI JSON. No or unreadable manifest: all
    keys, `workspace` null, no rows, `exit_code` 2 (`unreadable` when unreadable). Missing or empty
    argument: `missing_argument`. **API.md -> v1.18** (a new route; v1.17's exclusion paragraph
    added values only): one section (shape, labels, baseline, errors). **The mock changes**: real keys
    and types for the `owner-dev` fixture (`scheduler-web -> scheduler` static, `contradiction`;
    `missing_argument`; `unknown_project`), checked by a keys-and-types test as for `/api/contracts`.
14. **Docs.** `lattice/studio/API.md`; docstrings of `contracts/{__main__,export,impact}.py`,
    `mcp/projects.py`; `lattice/cli.py`'s help line; the layer 4 row of the phase design doc (4a built);
    `dogfood/studio` rule R95 + one scenario (no opted-in link: `checked` 0 + `no_consumers`;
    `missing_argument`; `unknown_project`; `POST` 404) + an `Unchecked` for row shapes, as R94.

## 3. Probes

**Global rules.** Each deterministic probe runs **10 times**: canonical JSON (`elapsed_s` dropped,
scratch root -> `$WORK`) and text identical 10 of 10. Workspaces as in layer 3: `scheduler` = clone
of job-scheduler at ef17cf8, `spec = "spec.tickets:t5"`, consumer `ci-bot` with `dogfood/contracts/golden/`;
`streak` = clone of Streak at a0db033 (`spec:SPEC`), `streak-widget` with `golden-streak/`. A
**candidate ref** `dN` = the committed edit list applied to a separate clone, committed with the
runners' fixed author and dates, fetched into the provider clone as `refs/heads/dN` before the
measured window (the provider's working tree stays clean). "= committed Dn": the row's `findings`
and `gaps` equal `links[0]` of `runs[0]` in that data file.

**I0 identity** (`--candidate HEAD`, clean provider). (a) job-scheduler: `ci-bot` `unaffected`, 0
findings, 1 gap (`access_token`), findings and gaps equal to `contracts check ci-bot --static-only
--json` in the same window, `spec_hash_changed` and `version_changed` false, `current_label`
`unaffected`, exit 0. (b) Streak: the same with 2 gaps. **(c) stale pin**: the provider clone's HEAD
is the D2 commit and `--candidate HEAD`: `contradiction`, `current_label` `contradiction`, findings
= `check --static-only` = committed D2, exit 1. Pass: 3 of 3.

**I1 job-scheduler D-series (ci-bot).** Source `data/2026-10-13-contracts/Dn.json`. Pass: 10/10
labels and exits; 10/10 rows = committed Dn; every row `current_label` `unaffected`,
`version_changed` true, 1 gap. `*` = relied.

| D | change | label | exit | findings | hash changed |
|---|---|---|---|---|---|
| D1 | endpoint removed | contradiction | 1 | BROKEN `endpoint_missing` GET .../jobs/{}/runs | yes |
| D2 | status 201->200 | contradiction | 1 | BROKEN `status_not_observed` 201; CHANGED* `status_added` 200 | yes |
| D3 | response field renamed | contradiction | 1 | BROKEN `response_field_missing` status; CHANGED* state | yes |
| D4 | request field renamed | contradiction | 1 | BROKEN `request_field_missing` email; CHANGED* username | yes |
| D5 | role tightened | contradiction | 1 | BROKEN `auth_changed` as_user:eve; CHANGED* `status_added` 403 | yes |
| D6 | endpoint added | unaffected | 0 | CHANGED `endpoint_added` GET /users/{} (unrelied) | yes |
| D7 | field type | unaffected (miss) | 0 | none | no |
| D8 | required field added | regression (under-classified) | 0 | CHANGED* `field_added` otp | yes |
| D9 | 404 removed, relied ep. | regression (noise) | 0 | CHANGED* `status_removed` 404 @ GET .../jobs | yes |
| D10 | bound tightened | unaffected (miss) | 0 | none | no |

Predicted 5 / 2 / 3. D6 is `unaffected` (no import names GET /users/{}); D9 is `regression`.

**I2 Streak D-series (streak-widget).** Source `data/2026-10-13-contracts-streak/Dn.json`
(`runs[0].check.doc`). Pass: 13/13 labels and exits; 13/13 rows = committed Dn; `current_label`
`unaffected`, `version_changed` true, 2 gaps (D7: 0).

| D | label | exit | findings |
|---|---|---|---|
| D1 home removed | contradiction | 1 | BROKEN `endpoint_missing` GET /v1/home |
| D2 dismiss 200->204 | contradiction | 1 | BROKEN `status_not_observed` 200; CHANGED* 204 |
| D3 expires_in renamed | contradiction | 1 | BROKEN `response_field_missing`; 1 CHANGED*, 2 unrelied @ refresh |
| D4 password renamed | contradiction | 1 | BROKEN `request_field_missing` password; CHANGED* passphrase |
| D5 ann loses home | contradiction | 1 | BROKEN `auth_changed` as_user:ann; CHANGED* `status_added` 404 |
| D6 next_cursor renamed | contradiction | 1 | BROKEN `response_field_missing`; 1 CHANGED*, 4 unrelied |
| D7 /v1 -> /v2 | contradiction | 1 | 7 BROKEN `endpoint_missing`; 41 unrelied CHANGED; 0 gaps |
| D8 endpoint added | unaffected | 0 | CHANGED `endpoint_added` GET /v1/me/stats (unrelied) |
| D9 field type | unaffected (miss) | 0 | none, hash unchanged |
| D10 required field | regression (under-classified) | 0 | CHANGED* `field_added` client_tz |
| D11 ben loses progress | regression (endpoint-level) | 0 | CHANGED* `auth_tightened` as_user:ben |
| D12 nested item key | unaffected (miss) | 0 | none, hash unchanged |
| D13 429 example removed | regression (noise) | 0 | CHANGED* `status_removed` 429 @ GET /v1/me |

Predicted 7 / 3 / 3.

**I3 controls.** Sources: `C1..C4.json`, Streak `C1..C6.json`, exclude `X2.json`, `X3.json`. Pass:
12 of 12. Job-scheduler J-C1 (base sha), J-C2 (comment), J-C3 (reorder): `unaffected`, 0 findings,
1 gap, exit 0; J-C4 (`created_by`): `unaffected`, exactly 1 unrelied CHANGED, exit 0. Streak S-C1..C3,
S-C5, S-C6 (UUIDs): `unaffected`, 0 findings, 2 gaps, exit 0 (C2, C3, C5, C6 hash unchanged); S-C4:
`unaffected`, 1 unrelied CHANGED. **I3x** (Streak, manifest `exclude = ["GET /health", "PUT
/test/clock"]`, candidate = S-C1): (a) widget re-pinned under the list (X2): `unaffected`, 0
findings, 2 gaps, exit 0; (b) widget + a `PUT /test/clock` import, force-pinned under the list (X3
"pinned after"): `contradiction`, exactly 1 BROKEN `endpoint_missing` PUT /test/clock with
`EXCLUDED_DETAIL`, 2 gaps, exit 1.

**I4 several consumers.** Runner-generated. Workspace A (rows a-g, i): `scheduler`, `ci-bot`, and
`ci-bot-2` = ci-bot's `POST /auth/login` and `GET /applications` tables verbatim, pinned on the clean
clone (entry byte-identical to the golden's). Workspace B (row h): `scheduler`, `streak`, and `multi`
= ci-bot's 7 + streak-widget's 7 tables, static links to both, pinned on both clean clones. ci-bot-2's
rows = committed Dn's items plus those `_folded` for ci-bot, relied only on its 2 endpoints. Pass: 9
of 9; rows ordered `ci-bot`, `ci-bot-2` in every run.

| row | setup | prediction |
|---|---|---|
| a | D1 | ci-bot contradiction; ci-bot-2 unaffected, exactly [CHANGED `endpoint_removed` GET .../jobs/{}/runs, unrelied], 1 gap; exit 1 |
| b | D3 | ci-bot contradiction; ci-bot-2 unaffected, exactly 2 unrelied CHANGED @ cancel (`field_added` state, `field_removed` status); exit 1 |
| c | D4 | each row contradiction, identical findings = committed D4; exit 1 |
| d | D8 | each row regression, identical findings = committed D8; exit 0 |
| e | a, ci-bot-2's link `live` | as a; ci-bot-2 `live` = `{"status": "not_run", "reason": "static-only"}` |
| f | a, ci-bot-2's link without `contract` | ci-bot-2 not listed; `checked` 1 |
| g | a, ci-bot-2's lock deleted | ci-bot-2 label null, error `no_pin`; ci-bot contradiction; exit 2 |
| h | `multi`: `scheduler` + D2, then `streak` + Streak D13 | contradiction = committed D2, 1 gap, no Streak item, exit 1; regression = committed Streak D13, 2 gaps, exit 0 |
| i | provider `ci-bot` (no inbound link), `HEAD`; provider `nobody` | exit 0, `checked` 0, `no_consumers`, `candidate.resolved` null; exit 2 `unknown_project` |

**I5 candidate forms (job-scheduler, D2 unless stated).** Pass: 8 of 8. (a) `d2` and the full D2
sha: JSON equal except `candidate.input`. (b) The D2 drift clone's directory: `kind` path, findings
= (a), `version.git_ref` = the D2 sha. (c) A directory with C2's edit uncommitted: `unaffected`,
`version.dirty` true. (d) `no-such-ref`: exit 2 `unknown_candidate` naming it, 0 export children.
(e) `--candidate=-x`: exit 2 `unknown_candidate`, no git call gets `-x`. (f) A ref whose
`spec/tickets.py` ends with `raise RuntimeError("not loaded")`: exit 2 `candidate_failed`, message
contains `not loaded`, no temp path. (g) A directory `d2` in the CLI's cwd (a copy of the clean
clone): `kind` path, `unaffected`. (h) Over (a)-(g) the provider clone is untouched: `git
--no-optional-locks status --porcelain=v2 --ignored`, HEAD, `for-each-ref`, reflog and the sha256 of
every file (`.git` included) identical before and after.

**I6 safety** (every call of I0-I5, I7). Pass: all of (1) sha256 of every file under the workspace
directory, each consumer (locks included), each provider clone (`.git` included) and the
lattice-impact worktree identical, `find -newer <marker>` 0 there; (2) the scratch `TMPDIR` has 0
entries after each call; (3) a `sitecustomize` on `PYTHONPATH` logs every `subprocess.Popen` argv and
`socket.connect`: 0 connects; every argv is `git` with a subcommand in {rev-parse, ls-tree,
cat-file, status} or the export child; export children per call 2 (0 for I4-i and I5-d, 1 for
I5-f); no `claude` binary, no `ANTHROPIC*` variable; (4) the real job-scheduler (HEAD ef17cf8,
` M uv.lock`) and Streak (HEAD a0db033, ` M studio.toml` and its sha256) unchanged, 0 newer files.

**I7 parity.** CLI `--json`, MCP `contract_impact` (stdio) and `GET /api/contracts/impact` (real
server) on I1-D2 and I4-a are equal (`elapsed_s` dropped; refs and absolute paths). The mock has the
same keys and types. Pass: 4 of 4 + mock.

**I8 no regression.** `probes.py --runs 10` 35/35 (needs `lattice-contracts-base`),
`streak_probes.py --runs 10 --allow-code-diff` 32/32, `exclude_probes.py --runs 10` 12/12; `pytest
tests/contracts tests/workspace tests/mcp tests/studio` 0 failures; `dogfood/studio/check.sh` PASS
(415 + R95's steps); on the I1 workspace `workspace show|validate|impact scheduler` (text and
`--json`), `contracts export scheduler`, `contracts check ci-bot --json` and `pin` lock bytes
byte-identical to the main checkout at 67189af (HEAD and `lattice/` checked first). Pass: all.

**T, cost (reported).** Median wall per preview, 10 runs: threshold <= 10 s; predicted <= 2 s
(job-scheduler), <= 3 s (Streak): two export children (committed 0.20 / 0.33 s) + materialising.

## 4. Mutation plan (`dogfood/contracts_mutants.py`, IMP1..IMP14)

One-line changes of the new code; each must be killed by the suite (`tests/contracts`, with the new
`test_impact.py`) and is predicted killed by the probe named. Prediction: 14 of 14.

| id | mutant | killed by |
|---|---|---|
| IMP1 | candidate ignored: the current export used as `C` | I1 D1-D5 unaffected |
| IMP2 | BROKEN mapped to `regression` | I1 D1-D5 label and exit |
| IMP3 | relied CHANGED mapped to `contradiction` | I1 D8, D9 |
| IMP4 | `relied` ignored: any CHANGED is `regression` | I1 D6, I3 J-C4, S-C4 |
| IMP5 | baseline = the current export, not the pin | I0c (unaffected) |
| IMP6 | `exclude` not passed for the candidate | I3x-a (2 `exclusion_removed`), I3x-b (regression) |
| IMP7 | the manifest `spec` ignored for the candidate | I0a (ticket-1 `spec:SPEC`: contradiction) |
| IMP8 | links without `contract` listed | I4-f |
| IMP9 | rows shuffled (`random.shuffle`) | I4 order and 10-run identity (survival 2^-10) |
| IMP10 | the candidate entry written into the consumer's lock | I6 (1); I1 run 2 differs |
| IMP11 | exit 1 on any `regression` | I1 D8, D9 exit |
| IMP12 | the ref materialised by `git checkout` in the provider tree and back | I5-h, I6 (1) |
| IMP13 | a `no_pin` row reported `unaffected` | I4-g (exit 0, not 2) |
| IMP14 | the temp directory not removed | I6 (2) |

## 5. Named risks

1. **A spec, not behaviour.** Static only (live links too): app drift is invisible; the predicted
   misses (job-scheduler D7, D10; Streak D9, D12) read `unaffected` while they break the consumer.
2. **`relied` is endpoint-level.** Unrelated edits on a relied endpoint are `regression` (D9, D11,
   D13); unclassifiable breaks (D8, D10) are only `regression` and exit 0: 2 of 5 are real breaks.
3. **Stale pins.** A lagging pin gives the cumulative pin -> candidate result: any candidate, even
   `HEAD`, can read `contradiction` and exit 1 (I0c); `current_label` shows it; only a re-pin cures it.
4. **Invisible consumers.** Outside the workspace, without `contract`, transitive, or nested reads
   the import format cannot express: `checked` counts only what is declared.
5. **Borrowed vocabulary.** Studio's words, not Studio's change impact (a change set's rules, with
   "not built yet"): layer-3 findings folded per consumer for a spec ref, tied to no change set.
6. **Builder bias, circularity.** The predictor read `check.py`; I1-I3 predict equality with layer-3
   results of the same `evaluate`/`changed_of`: they show the right inputs (candidate, pin, filter)
   and labels, not a new detection ability. Builders will read these predictions.
7. **Materialisation and trust.** Raw blobs (no LFS, filters, `export-subst`, submodules); modules
   found outside the candidate directory (an editable provider install) would mix in current code;
   loading a candidate executes its spec code in a child process (an untrusted ref runs its Python).
8. **Ref or path.** A directory shadows a same-named ref (I5-g); `candidate.kind` records it.

## 6. Blind vs post hoc, and verification

Fixed now. A failed prediction is a **finding** (output shown; prediction error or defect), never
fixed silently; blind numbers stay as first measured, any fix is a separate post-hoc commit. Runner
choices, timings, edge cases and test edits (the MCP tool list) go in the results' "Post hoc".

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-impact PYTHONDONTWRITEBYTECODE=1
cd /Users/vivekgade/Documents/lattice-impact
git -C /Users/vivekgade/Documents/lattice rev-parse HEAD                 # 67189af (I8 old code)
git -C /Users/vivekgade/Documents/lattice-contracts-base rev-parse HEAD  # c0cdefc (probes.py W5)
uv run python dogfood/contracts/impact_probes.py --runs 10 --scratch "$SCRATCH/i" --out "$SCRATCH/i-data"
uv run python dogfood/contracts/probes.py --runs 10 --scratch "$SCRATCH/c" --out "$SCRATCH/c-data"
uv run python dogfood/contracts/streak_probes.py --runs 10 --allow-code-diff --scratch "$SCRATCH/s" --out "$SCRATCH/s-data"
uv run python dogfood/contracts/exclude_probes.py --runs 10 --scratch "$SCRATCH/x" --out "$SCRATCH/x-data"
uv run python dogfood/contracts_mutants.py                               # control passes; IMP1-IMP14 killed
uv run pytest tests/contracts tests/workspace tests/mcp tests/studio -q  # 0 failures
dogfood/studio/check.sh                                                  # PASS, 415 + R95's steps
uv run ruff check <changed .py>; uv run ruff format --check <created .py>
```

**Pass:** I0 3/3, I1 10/10 (+ rows), I2 13/13 (+ rows), I3 12/12, I4 9/9, I5 8/8, I6, I7 4/4 +
mock, I8, T within threshold, 14/14 mutants, every deterministic probe identical 10/10. The recorded
run goes to `docs/results/data/2026-10-13-contracts-impact/`; `$SCRATCH` is the session scratchpad.
