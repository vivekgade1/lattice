# SG-CD diagnosis: why the checker reports 136 findings on the PM-SG spec (2026-10-30)

**Label: SG is development data (scored once in gRPC round 5). This note is a post hoc diagnosis. It makes no claim, backs no blind result, and changes no number of the round.** No model was called (USD 0). No file under `lattice/` or `dogfood/` was edited; the author directory `~/Documents/g5-sg-author` was only read (the app was copied to a scratch directory first, and a `find -newer` over the author directory afterwards lists nothing).

## Answer in one paragraph

The 136 findings are not "anonymous-access scenarios that the app refuses or accepts differently". The round-5 data only *looked* like that, because the probe stored the first 20 rule names (`dogfood/grpc_probes5.py:1698`, `[... for f in rep.findings][:20]`, rule only, never the message) and the spec lists its 21 `access_anonymous_*` scenarios first. The real split is:

| Family | Count | Layer / verdict | Cause |
|---|---|---|---|
| F1 given-rows insert refused by MySQL, error 1292 (`created_at` 131, `expires_at` 4) | 135 | D / loud | Lattice's MySQL adapter inserts the spec's `given` instants as text (`"2026-03-01T00:00:00Z"`); a strict MySQL 8.4 session refuses the trailing `Z`. The scenario stops before its first step, so none of those 135 scenarios ever ran an RPC. **Library gap** (`lattice/spec/db.py`). |
| F2 `access_token_expires_after_15_minutes / step 4` expected UNAUTHENTICATED, got OK | 1 | D / silent | The library logs a caller in **lazily, at the caller's first step** (after the spec's `clock` step has already moved the test clock); the author's runner (and the brief) log callers in **before the first step, at the seed's clock instant**. The token is therefore minted at 12:14, not 12:00, and is still valid at 12:16. **Library semantics versus the stated harness contract** (`lattice/spec/check.py`). |
| Layer C | 0 | | none |

The author's spec and app are not at fault: with the two library behaviours adjusted in a scratch run (no repository edit) the real checker reports **0 findings on all 162 scenarios**, including all 21 `access_anonymous_*`, 21 `access_ann_*` and 16 `access_ben_*` ones. The anonymous-caller step support (`user=None`, no metadata) is not implicated.

## Commands and environment

Worktree `agent-a191802412480d3d0` at `cff84db` (develop); real checker `lattice.spec.check` with `database="mysql"`, i.e. `lattice/spec/dbserver.py` starting its throwaway `mysql:8.4` container (Docker was up; image `mysql:8.4` already present). The probe's own gate `docker_ready()` first answered `'pymysql is not importable (run with a driver environment)'` in the repo venv (pymysql is only in the `db` extra), so the PyMySQL driver was installed into a scratch directory without touching the venv:

```
uv pip install --python .venv/bin/python --target <scratch>/sg/pylib "pymysql>=1.1" cryptography
cp -R ~/Documents/g5-sg-author/app <scratch>/sg/app          # never write into the author directory
```

`<scratch>/sg/run_cd.py` is `sg_cd` (`dogfood/grpc_probes5.py:1691`) unrolled: `load_app5(...)` (same harness import), `_harness_attr(root, "spec:SPEC")`, `LATTICE_CONTAINER_OWNER=w-gr`, then

```
rep = check(spec, app.make, app.build_db, layers="CD", runtime=app.runtime, database="mysql")
remove_all()
```

and it dumps every `CheckFinding` (layer, verdict, rule, case, request, full `details`) to JSON. Run: `.venv/bin/python <scratch>/sg/run_cd.py <scratch>/sg CD` (about 3.5 minutes; docker_ready `''`).

Results of the unmodified run: `FINDINGS 136`; layer/verdict: `('D','loud') 135`, `('D','silent') 1`, layer C 0. By scenario family: `access_anonymous_*` 21, `access_ann_*` 21, `access_ben_*` 16, every other scenario 78 (77 loud + the 1 silent). The spec has 162 scenarios, 135 of which carry `given` rows and 27 do not: the 135 loud findings are exactly the 135 scenarios with `given` rows; of the 27 without, 26 pass and 1 is the silent finding.

The author's own runner was reproduced too (`tools/dump_spec.py` then `go test -run TestSpecScenarios ./internal/app` with `HABIT_TEST_DSN` pointing at a Lattice-provided mysql:8.4): `162 scenarios, 748 steps`, `PASS subtests 162, FAIL subtests 0`, `ok streak/internal/app 30.085s`. So **136 and 162/162 both reproduce exactly**; there is no drift between round 5 and this commit.

## Verbatim findings

Message text (`details`) of five findings, from `findings-CD.json`:

1. `scenario: access_anonymous_me`, case `access_anonymous_me / given`, layer D, loud:
   `{'error': 'the app\'s database refused the scenario\'s given rows: DatabaseError: (1292, "Incorrect datetime value: \'2026-03-01T00:00:00Z\' for column \'created_at\' at row 1")'}`
2. `scenario: access_ben_sync`, case `access_ben_sync / given`, D, loud: same text, `created_at`, `\'2026-03-01T00:00:00Z\'`.
3. `scenario: refresh_rotates_the_token`, case `refresh_rotates_the_token / given`, D, loud:
   `... (1292, "Incorrect datetime value: \'2026-04-01T00:00:00Z\' for column \'expires_at\' at row 1")` (4 findings are `expires_at`: `login_revokes_the_installation_s_old_tokens`, `refresh_rotates_the_token`, `refresh_token_reuse_revokes_the_session`, `refresh_token_unknown_or_expired_is_refused`; the other 131 are `created_at`).
4. `scenario: access_ann_delete_me`, case `access_ann_delete_me / given`, D, loud: same text as 1.
5. `scenario: access_token_expires_after_15_minutes`, case `access_token_expires_after_15_minutes / step 4`, endpoint `RPC streak.v1.AccountService/GetMe`, D, silent:
   `{'problems': ['code: expected UNAUTHENTICATED, got OK', "message: expected {'message': 'token_expired'} within {'email': 'ann@example.com', 'id': 1, ...}"], 'expected': {'status': 'UNAUTHENTICATED', 'code': 'UNAUTHENTICATED', 'body': {'message': 'token_expired'}, 'db': None}, 'actual': {'status': 'OK', 'code': 'OK', 'body': {'email': 'ann@example.com', 'id': 1, 'notification_prefs': {...}, 'timezone': 'America/New_York'}}}`

## Root cause 1 (135 findings): ISO `Z` instants in `given` rows on strict MySQL

Library (the bug):
- `lattice/spec/check.py:1299-1311` `_insert_given`: only `_stored(...)` (one-of spellings) is applied, then `conn.insert(sql, rows)`.
- `lattice/spec/db.py:242-251` `Database.insert` builds `INSERT ... VALUES (%s, ...)` with the Python values as they are; `MysqlDatabase` (`db.py:366`) does not override `insert` (its `_run` just calls `cur.execute`). PyMySQL sends the string `'2026-03-01T00:00:00Z'`; MySQL 8.4's default session `sql_mode` includes `STRICT_TRANS_TABLES`, takes `T` and `+00:00` but not `Z`, and answers error 1292.
- `lattice/spec/check.py:1664-1678` turns that `DatabaseError` into the finding "the app's database refused the scenario's given rows" and `return`s: the scenario's steps never run.

App (innocent): `internal/schema/schema.sql` declares `created_at DATETIME NOT NULL` (lines 13, 39, 40, ...); the app's own Go runner converts `YYYY-MM-DDTHH:MM:SSZ` to `YYYY-MM-DD HH:MM:SS` before inserting (`internal/app/specrun_test.go:239-257` `dbValue`, line 251-253), which is why it passes 162/162.

Spec (conventional, not wrong): `spec.py:139` `T0 = "2026-03-01T00:00:00Z"`, `spec.py:615` `expires="2026-03-01T00:00:00Z"`; the brief given to the author (Streak `CONTEXT.md:7`) tells authors to write instants as `"2026-03-01T00:00:00Z"`.

Harness: `harness.py:65-94` (`build_db`, `make_app`) never relaxes the server's `sql_mode`. Streak's own harness knows the gap and works around it in the harness (`~/Documents/streak/harness.py:27-35` docstring, `:118-130` `CHECKER_SQL_MODE` / `SET GLOBAL sql_mode`). The SG author was isolated from that file, and `HARNESS_API.md` / `BRIEF-SG.md` say nothing about instants (grep for datetime/timestamp/ISO: no hit), so the author could not have known. The gap is therefore a *known library limitation that was patched per-harness in one place and not fixed in the library*.

## Root cause 2 (1 finding): token minted lazily, after the clock step

Spec: `spec.py:630-637`, steps are `clock(12:14)`, `GetMe` as ann (expects OK), `clock(12:16)`, `GetMe` as ann (expects `UNAUTHENTICATED token_expired`), `CreateHabit`. The only way a 15-minute token is expired at 12:16 while valid at 12:14 is that it was minted at the seed instant (12:00).

App: `internal/app/interceptor.go:26,33-39` issues `exp = now + 15m` from the request's clock reading (`TableClock`, `clock.go:24-31`, reading `test_clock`), and `interceptor.go:120` answers `token_expired` against that same clock. Correct.

Library: `lattice/spec/check.py:1857-1868` (`_rpc_step`; the REST twin is `:1733-1746`) logs a handle in only `if step.user not in tokens`, i.e. at that handle's first step, which here comes after `clock(12:14)`. The token is minted at 12:14 and is valid until 12:29, so the step at 12:16 correctly gets OK from the app. The documented contract says otherwise: Streak `CONTEXT.md:18` (given to the author): "the harness gets each caller's tokens through ... login ... **at the seed's clock instant**", and the author's runner does exactly that (`specrun_test.go:95-97`, "the harness signs its users in before the first step"). The module docstring (`check.py:27`, "callers logged in through `login(client, handle)`") does not say when.

Anonymous-caller handling (the question in the brief): not involved. `access_anonymous_*` use `user=None`; they only failed because their `given` rows (F1) were refused before step 1, and they pass once F1 is fixed.

## Evidence that these two causes are all of it (scratch experiments, no repository edit)

1. `patch.py` (scratch, monkeypatched into the run, not in the repo): wrap `MysqlDatabase.insert` so a string matching `^(\d{4}-\d\d-\d\d)T(\d\d:\d\d:\d\d(?:\.\d+)?)Z$` becomes `"\1 \2"`. Run: **`FINDINGS 1`** (only F2 remains; all 135 F1 gone, so none of them hid a second problem: the 135 scenarios now run their steps and pass).
2. Same patch plus, in the *scratch copy* of `spec.py` only, one extra `GetMe` step as ann ahead of the first `clock(...)` in `access_token_expires_after_15_minutes` (this makes the library log ann in at the seed instant, which is what an eager login does): **`FINDINGS 0`**.
3. Author's Go runner: 162/162 (above).

## Proposed minimal fix (described, not made)

Fix A (removes 135 of 136, library, `lattice/spec/db.py`): in `MysqlDatabase`, override `insert` so that an ISO-8601 instant string with a trailing `Z` (the spelling the language and the brief prescribe for `given` instants) is passed as the naive UTC literal `YYYY-MM-DD HH:MM:SS[.ffffff]`. Safer variant: convert only for columns whose `information_schema.columns.data_type` is `datetime`/`timestamp`, so a free-text column that happens to hold an ISO string is untouched; the regex-only variant is about 8 lines and was what the scratch run used. `_same_time` (`check.py:1327-1343`) already compares read-back datetimes with the spec's `Z` text, so no read-side change is needed. Test: a Docker-free unit test with a fake PyMySQL cursor asserting the parameter conversion, plus one live-marked MySQL case with a `given` row. This is an adapter change, not a language change (the spec text is unchanged), but per `docs/lattice-language-rules.md` it must keep Postgres/SQLite behaviour byte-identical (Postgres takes `Z` natively; leave it alone) and should be confirmed on the Streak harness, whose `SET GLOBAL sql_mode` workaround then becomes unnecessary.

Fix B (removes the last one, library, `lattice/spec/check.py`): before a scenario's first step, after the `given` rows are inserted and the app started, obtain a token for every distinct `step.user` of its Step/Rpc steps (a loop over `scenario.steps` calling the existing `runtime.auth.token(...)` / `login(...)` and filling `tokens`), so the lazy branches at `check.py:1733` and `:1857` become no-ops. This matches the stated contract ("at the seed's clock instant") and the author's runner. It is a behaviour change for every layer-D run (a login that fails now fails at step 0, with the existing "cannot log in as ..." finding), so it needs the owner's decision and a run of `tests/spec` plus the Streak and HO5 apps; a conservative alternative is to document the lazy behaviour in `check.py`'s docstring and in `HARNESS_API.md` so authors put a call before the first `clock` step (a spec edit, not allowed here to make a check pass).

Neither fix touches the author's app or spec; both make the findings disappear because the checker then runs the scenarios as the author's runner does, not because anything is hidden or loosened.

## Caveats

- SG is development data; this diagnosis uses it only to find library gaps. It supports no accuracy claim and the round-5 table (`SG-CD`: "reported") is unchanged.
- The scratch experiments were run once each; the patched runs are prototypes of the fixes, not tests of the final code.
- The pymysql driver came from a scratch `--target` install (version 1.2.3 resolved at run time); the repo venv was not modified.
