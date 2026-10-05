# Phase 2 held-out run (Wave 4): results

Checkout `lattice-phase2-heldout` at `adaa489`; importer, probes and exit script frozen at
`1cda54a` (nothing under `lattice/`, `bench/phase2_exit.py`, `bench/edit_slice/` or
`tests/importer/` was edited). Run on 2026-10-05 by a fresh agent that had not seen the
importer's output for any app. No model or API call; `--no-llm`, 0 tokens, $0.

**Headline: no held-out app passes the exit.** H1 (drf_blog) crashes in the importer's
own seeder before any probe runs. H2 (projmgmt) reaches 33% of tables. H3 (conduit)
clears 90% on the exit script's numbers but fails fidelity. The manual review also
finds that both completed reports overstate what was verified.

## 1. Per-app table (the frozen, first and only run)

| app | framework | tables verified / total (outside API) | routes verified / total | unreachable | fidelity | proposals (true by review) | import wall | measurement wall | review time |
|---|---|---|---|---|---|---|---|---|---|
| H1 drf_blog | Django + DRF, simplejwt | **crash** | **crash** | n/a | not run | n/a | 0.77 s (crash) | 0.77 s | none (no output to review) |
| H2 projmgmt | Flask + Flask-SQLAlchemy, PyJWT | 1 / 3 = 33.3% (0 outside) | 23 / 23 = 100% | 0 routes; 61 items `unreachable-by-probe` | **fail**: variant 2 has a finding (`T4 User.user_type`); scenarios 19/19, answers 93/93, oracle 93/93 | 1 (1 true) | 2.40 s | 7.97 s | 1 min 07 s |
| H3 conduit | Django + DRF, simplejwt | 1 / 1 = 100% (**5 outside API**) | 24 / 26 = 92.3% | 0 | **fail**: variant 2 has findings (`R1 POST /api/token/`, `R1 POST /api/token/refresh/`); scenarios 21, answers 80/80, oracle 70/80 | 8 (7 true) | 1.60 s | 2.45 s | 33 s |

- **Pooled, as the exit script counts:** tables 2/4, routes 47/49. H1 is excluded because
  it crashed.
- **Recomputation:** for H2 and H3, `phase2_exit.recompute` agrees with each report's
  summary.
- **Columns:**
  - "import wall" is `import_s` (the timed `python -m lattice.importer` subprocess).
  - "measurement wall" adds the fidelity subprocess.
  - The review times are an agent's tool-call wall time, not a human's.
- **Stage times** (`rules.timing`):
  - H2: extract 0.33 s, probe 0.93 s, correct 0.06 s, mine 0.03 s, variant 2 0.78 s.
  - H3: extract 0.37 s, probe 0.39 s, correct 0.16 s, mine 0.14 s, variant 2 0.24 s.
- **Baseline (upstream defects recorded by the import):** H3 has 5 500s. All of them are
  real upstream defects; see §4.
- **Raw outputs:**
  - imports: `bench/heldout/{projmgmt,conduit}/`;
  - exit records: `bench/heldout/results/<app>.json` and `<app>.md`, with
    `<app>.wall_s`;
  - console log: `bench/heldout/results/run.log`.

### The 90% bar (tables AND routes, per app) and the exit

| app | tables ≥ 90% | routes ≥ 90% | fidelity | exit |
|---|---|---|---|---|
| H1 drf_blog | no (crash) | no (crash) | not run | **fail** |
| H2 projmgmt | no (33.3%) | yes (100%) | fail | **fail** |
| H3 conduit | yes by the script (1/1); **no on review (1/6)** | yes (92.3%); **no on review (21/26 = 80.8%)** | fail | **fail** |

The frozen run fails Phase 2 exit criteria 1 and 2 on all three held-out apps.

## 2. How the run was made

1. **Vendoring.** H1–H3 were vendored into `bench/import_repos/{drf_blog,projmgmt,conduit}/`
   from fresh clones made outside the repo:

   | app | commit |
   |---|---|
   | drf_blog | `047e197bc48dba6bc75146de08b987b06ec7f8af` |
   | projmgmt | `bfff55412e6785ba0222d0dc92e6fc17d489d64c` |
   | conduit | `bcba78764216326c04098c10f32ef148cd45f0e6` |

   Each app has its upstream LICENSE, a `PROVENANCE.md`, `make_app`, `seed.py` and
   `fidelity.py` (23, 19 and 21 scenarios). The scenarios come from the upstream tests
   and README; conduit has no tests, so its scenarios come from the README and the view
   docstrings. All of them were recorded against the running app. The reserve app was not
   opened. Upstream `.env` files were neither read nor vendored.
2. **Dependencies.** `drf-spectacular` and `drf-spectacular-sidecar` (H1) and `loguru` (H2)
   were added to the `phase2_apps` group in `pyproject.toml` / `uv.lock`.
3. **Smoke test.** `bench/heldout/test_heldout_apps.py` is a copy of
   `tests/bench/test_phase2_apps.py`. Its worker, `bench/heldout/_heldout_probe.py`, is a
   copy of `_phase2_probe.py` with the held-out app list. It passes 6/6: each app boots,
   seeds, logs in every role through its own login, refuses its refused users, replays
   every fidelity scenario, and writes nothing outside `tmp_path`.
4. **The frozen run.** `bench/phase2_exit.py` has a hard-coded `APPS` and runs the import
   itself, so it cannot read imports made elsewhere.
   - `bench/heldout/run_exit.py` adds the held-out packages to that dict at run time and
     calls the exit script's own `measure()` and `markdown()`, unchanged, with
     `--work bench/heldout`.
   - So the exit script's import is the one and only import per app:
     `python -m lattice.importer <repo> --package bench.import_repos.<app> --rules-only
     --no-llm --no-corpus --out bench/heldout/<app>`. `--framework` was auto-detected;
     `--no-corpus` is the exit script's own flag and does nothing with `--rules-only`.
   - The apps ran once each, one after another, 2026-10-05 15:23:01–15:23:12 UTC.
5. **Re-runs, H1 only, to collect output the crash prevented:**
   - The exit record keeps only the last 4,000 characters of stderr, which cut off the
     table being inserted. One identical re-import of H1 was run only to capture the full
     stderr (`results/drf_blog_crash_rerun.stderr`). It crashed the same way in 0.79 s.
   - One diagnostic run followed, in scratch space outside the repo: the same CLI call,
     with Django's `QuerySet.bulk_create` wrapped to print the table being inserted. No
     importer code was changed.
   - Neither run produced a result. Nothing was re-run after looking at H2's or H3's
     results.

## 3. Crash: H1 drf_blog

The importer exits 1 during layer R's seeding, before any probe:

```
lattice/importer/__main__.py:5          raise SystemExit(main())
lattice/importer/cli.py:156             return _rules_only(p, args, root)
lattice/importer/cli.py:228             imported = import_rules(root, args.package, ...)
lattice/importer/rules.py:939           rules = run_probes(spec, sessions, seeder.make_db, ...)
lattice/spec/probes.py:2373             seed_db = Path(make_db(variant, tmp / "seed.db"))
lattice/importer/rules.py:328           self._write(variant, target)
lattice/importer/rules.py:340           self.insert_hook(path, plan)
lattice/importer/frontends/django_orm.py:341   model.objects.bulk_create([...])
django/db/models/query.py:900 bulk_create -> ... -> django/db/utils.py:94
django.db.utils.IntegrityError: FOREIGN KEY constraint failed   (sqlite3.IntegrityError)
```

**Cause** (found by the diagnostic run):
- The seed hook inserts the 8 `blog_blog` rows (`author_id` 1–4) first among the app's
  tables, while `account_user` still has **0 rows**.
- The seed plan's table order puts the blog table ahead of the custom users table it
  references (`AUTH_USER_MODEL = 'account.User'`, app label `account`). SQLite's
  foreign-key check then rejects the insert.
- No `report.json` or other output was written.

One of the three held-out apps cannot be imported at all with the frozen importer.

## 4. Manual review (one pass per app, read-only)

The review used `python -m lattice.importer review DIR list`, `report.md`, `report.json`
and `proposals.json`. Nothing was accepted or rejected (`review.jsonl` was not written).
The ground truth is the code read while vendoring and the recorded fidelity answers.

### H2 projmgmt (1 min 07 s)

**Layer R ran without callers.** `rules.notes`: "layer R: no login given; probes run
without callers".
- The app has a JSON login route (`POST /auth/login` → `access_token`). Neither the
  Flask front end nor a minter found it.
- So every probe was anonymous or carried a malformed token, and no role was inferred: the
  profile has `NoRoles('Customer')` and misses the `manager` / `employee` enum.

| item(s) | verdict | importer right? |
|---|---|---|
| R1, all 23 routes (`admits exactly: Public`) | verified, "2 callers as predicted" | **Wrong for 15 of 23.** The anonymous probe of each of these routes answered **401** `Authorization header is required` (recorded in `report.json` `probes`), yet R1 Public was marked verified. The 15: `POST /auth/logout`, `GET /auth/verify`, `POST`/`GET /users`, `GET`/`PUT`/`DELETE /users/{id}`, `POST /projects`, `PUT`/`DELETE /projects/{id}`, `GET`/`POST /tasks`, `POST /projects/{id}/tasks`, `PUT`/`DELETE /tasks/{id}`. They need a token, and 7 of them need the manager role. The other 8 are right: `GET /`, login, register, refresh, `GET /projects`, `GET /projects/{id}`, `GET /projects/{id}/tasks`, `GET /tasks/{id}`. This is a verifier false positive: a probe answer contradicts the item and the item is still verified. |
| 8 × `R3 ... 404` "verified (corrected) (404 → 401)" | verified (corrected) | **Wrong, all 8.** The routes do answer 404 (`User/Project/Task not found`) for an unknown id with a valid token (recorded in fidelity). The 401 came from anonymous probes. |
| 61 R3 items `unreachable-by-probe: no example triggers it` | unexercised | **Mostly wrong.** The 401, 403 and 400 sites are trivially reachable, and the anonymous probes even answered 401 on them. Only the `except Exception` 500 sites are plausibly unreachable. |
| `T1 Task` (shared) | verified | right |
| `T1 Project` (shared) | unclear | The sentence is right (no route scopes by `user_id`; anyone reads every project). The verdict withholds it. Half right. |
| `T1 User` (users table) | unexercised, "no probe reached it" → table `unreached` | **wrong** (a consequence of having no login) |
| `T4 User.user_type` one of manager, employee | refuted, "stored values outside the list: ['employee', 'manager']" | **Wrong refutation.** The app stores `MANAGER` / `EMPLOYEE` (SQLAlchemy `Enum` stores names), and the importer's own profile says so (`one_of_stored`). The lowercase values were written by the importer's raw-SQL seed, and the "violation" is in that seed. The same item is variant 2's finding, so fidelity fails on it. |
| `T5 Project/Task/User (id)` | unexercised | acceptable (autoincrement keys). Missed: `users.email` is `unique=True` and has no T5 item; it shows up only as a proposal. |
| Proposal `No two User rows share email` | pending | **true** |

Fidelity's scenario half passed with the rule oracle at 93/93. That includes steps that
answer 401/403 on routes whose contract says `Public`, so for this spec the oracle does not
constrain auth.

**On review:** tables 1/3 (Task); routes with a correct R1 8/23; corrections 0/8 right;
proposals 1/1 true.

### H3 conduit (33 s)

Layer R had 5 callers here (anonymous, malformed token, inactive user, Admin, Customer).
simplejwt's `/api/token/` login was used.

| item(s) | verdict | importer right? |
|---|---|---|
| `T1`/`T5` Topic, Tag, Comment, User_favorites, Topic_tags: "outside API: no route reads or writes …" | unexercised → **left out of the table denominator** | **Wrong, all 5.** Topics are listed, created, updated and deleted (`/api/topics/`, `/api/topic/{pk}/`); tags are listed and created on the fly; comments are listed and created; `favor` writes `User_favorites`; topic create and update write `Topic_tags`. The DRF front end does not see the ORM calls inside plain `ViewSet` methods. This is what turns 1/6 into the reported "1/1 = 100%". |
| `T5 User (_id)` "no route creates User rows" | unexercised | **wrong** (`POST /api/users/` creates users). Also missing: `email` and `username` are unique. |
| `T1 User` | verified | right |
| R1 `my-topics`, `my-favorites`, `profile/{u}`, `profile/{u}/favorites` corrected Public → Authenticated | verified (corrected) | right (4) |
| R1 `DELETE /api/user/{username}/` corrected → Admin | verified (corrected) | right (`IsAdminUser`) |
| R1 `PUT`/`DELETE /api/topic/{pk}/`, `DELETE /api/topic/{_id}/comment/{pk}/` corrected → Admin | verified (corrected) | **Wrong (3).** These are owner-or-staff (`IsAdminOrOwner`): alice updates her own topic (recorded 200), bob deletes his own topic. Fidelity's oracle fails on exactly these steps. |
| 5 × `R3 … 5xx` corrected to "states the app's 500" (baseline) | verified (corrected) | **Right, all 5, and real upstream defects:** `POST /api/topics/` and `PUT /api/topic/{pk}/` without `tags` (`KeyError`); `GET /api/profile/{u}/` and `…/favorites/` for an unknown user (`DoesNotExist`); `DELETE /api/topic/{_id}/comment/{pk}/` (`RelatedManager.remove` on a non-null FK, found independently while vendoring). |
| R1 `POST /api/token/`, `POST /api/token/refresh/` (Public) | refuted, "anonymous refused, Admin refused, Customer refused" | **Wrong refutation.** Both routes are public. Their 401/400 answers depend on the credentials sent (R2 territory by the plan), and anonymous callers do log in with them. These 2 are the routes the script counts as unverified, and they are variant 2's findings, so fidelity fails on them. |
| the other 19 R1 items | verified | right. `PUT /api/user/{u}/` (`IsAdminOrSelf`) as `Customer` is right for R1, but its self-or-staff object rule is not modelled at all. |
| Proposals | | 7 of 8 true. **True:** `email` unique; `username` unique; `Topic.update_at` set; `User.update_at` set (`auto_now`); `(topic_id, tag_id)` unique; `(user_id, topic_id)` unique; `gender ∈ {-1, 0, 1}`. **False:** `Topic.favorite > 0` (new topics start at 0, and an unfavor returns to 0). |

**On review:** tables 1/6 = 16.7%; routes 21/26 = 80.8% correct (24 marked verified, 3 of
them wrong; 2 refuted, both wrongly); corrections 10/13 right; proposals 7/8 true.

### Precision summary (review)

| | H2 projmgmt | H3 conduit |
|---|---|---|
| route verdicts "verified" that are right | 8 / 23 | 21 / 24 |
| corrections that are right | 0 / 8 | 10 / 13 |
| refutations that are right | 0 / 1 | 0 / 2 |
| tables left out as "outside API" that really are | n/a | 0 / 5 |
| proposals true | 1 / 1 | 7 / 8 |

## 5. Every LATTICE MOD

Full tables with line numbers are in each app's `PROVENANCE.md`. Every vendored `.py` file
also starts with `# ruff: noqa`.

**drf_blog (31 lines)**
- 3 lines: `from decouple import config` in `src/config/settings.py`, `logger.py` and
  `urls.py`, replaced by the Lattice-owned `_lattice_env.config`. This disables `.env`
  loading: python-decouple searches for `.env` in every parent directory and reads
  `os.environ`. `_lattice_env.py` answers from fixed values.
- 7 lines: package paths (`INSTALLED_APPS` ×4, `name =` in 3 `apps.py`).
- 21 lines: import paths (settings `ROOT_URLCONF`, the default permission class and the
  pagination class; three `include`s in `config/urls.py` and three in the apps' `urls.py`;
  `import blog.signals`; `extensions.utils` and `permissions` imports in views, models and
  signals; `blog.models` imports; the blog migration's `extensions.utils` import and
  `upload_to`).

**projmgmt (40 lines)**
- 2 lines in `config.py`, commented out: `from dotenv import load_dotenv` and
  `load_dotenv()`. This disables `.env` loading.
- 38 lines: import paths (`app.` → `bench.import_repos.projmgmt.app.`,
  `config` → `bench.import_repos.projmgmt.config`).

**conduit (13 lines)**
- 2 lines: package paths (`"api"` in `INSTALLED_APPS`, `name =` in `apps.py`).
- 11 lines: import paths (`ROOT_URLCONF`, the pagination class, simplejwt's
  `TOKEN_OBTAIN_SERIALIZER`, `include("api.urls")`, and the `api.*` imports in urls,
  serializers and views).

**What the mods do not change.** No compatibility fixes were needed on Django 6.1,
DRF 3.18 and Flask 3.1. No model, serializer, permission, decorator or view logic was
changed. Upstream sends no mail or SMS (H1 returns its OTP in the answer), so nothing
needed stubbing and the `socket.getfqdn` fix was not needed.

**Harness choices in Lattice-owned files, not upstream mods:**
- **Settings overrides:**
  - SQLite instead of Postgres (H1) and MySQL (H3);
  - LocMem cache and locmem mail;
  - fixed secret keys, `DEBUG=False` and the test host;
  - an MD5 password hasher;
  - H1's file log handler dropped (it wrote inside the vendored tree).
- **H1:** `make_app` clears the LocMem cache on each call, so each app starts like a fresh
  server process. That cache holds the OTP codes and the throttle counters; the upstream
  throttle rates are unchanged.
- **H2:**
  - `make_app` pins the config through a per-call `Config` subclass and sets `FLASK_ENV`
    only while `create_app()` runs.
  - It also moves upstream's queued stdout loguru sink to a synchronous stderr sink, so
    machine-read stdout stays clean.

## 6. Signs of contamination (importer tuned to these apps)

1. **Direct evidence, before the freeze.** Commit `6ca5211` (W3, before the `1cda54a`
   freeze) added the front end's minter path for "simplejwt behind an OTP"
   (`lattice/importer/rules.py:626`). It also added a test named for H1 by name:
   `tests/importer/test_rules.py:271`, "simplejwt behind an OTP login (drf_blog_api's
   kind): no login route the probes can call…".
   - So the importer was designed with knowledge of H1's login.
   - The plan predicted this exposure: §6 lists each held-out app's rules, and Risk 1
     names H1's OTP login.
   - It did not help: H1 crashed in seeding before any login was attempted.
2. **No other hits.** A search of `lattice/`, `tests/importer/` and `bench/phase2_exit.py`
   for the other identifiers found nothing. The search covered the app names, the
   upstream owners, `manager_required`, `token_required`, `IsAdminOrOwner`,
   `IsAdminOrSelf`, `IsSuperUser*`, `decouple`, `UserType`, `topic_tags` and
   `user_favorites`.
3. **The results argue against tuning that worked.** The importer:
   - misses H2's role enum and its login route;
   - drops 5 of H3's 6 tables as "outside API";
   - cannot seed H1.
4. **The plan's selection notes.** The plan's §6 table, which an importer author could
   read, describes these apps' guards. That is the residual exposure.

## 7. What these results suggest (for the record, not acted on)

- **Seeding:** the Django seed hook needs a foreign-key topological order that includes a
  custom users model (H1).
- **Login discovery:** the Flask front end does not find a JSON login route that answers
  `access_token` behind custom decorators (H2).
- **Verifier false positive:** R1 is verified when contradicting probe answers exist, and
  this happens when layer R has no callers. It also produces 404 → 401 "corrections" from
  anonymous probes (H2). It inflates H2's routes to 100%.
- **"Outside API" exclusion:** it trusts a front end that cannot see ORM calls inside
  plain DRF `ViewSet` methods (H3). The exclusion rule, decided after the dev-app gate,
  turned an undercount into a perfect score here.
- **Object permissions:** object-level permissions (`IsAdminOrOwner`) are "corrected" to
  Admin-only when the probe's target belongs to someone else (H3).
- **Login routes:** they are refuted because wrong-credential answers are counted as
  refusals (H3).
- **Seeded enum values:** the seed writes enum values instead of their stored spellings,
  which yields a false T4 refutation (H2).
- **Measurement:** to keep the exit gate's numbers meaningful, every verdict needs a
  check like this review: the exit script's 92.3% / 100% figures for H3 and H2's routes
  are not supported by the probe evidence in the reports themselves.
