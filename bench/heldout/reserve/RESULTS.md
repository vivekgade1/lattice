# Phase 2 held-out reserve run (Wave 4, second attempt): results

Checkout `lattice-phase2-reserve` at `9dbd2aa`. The importer, probes and exit script are
frozen at `cad36e5`: nothing under `lattice/`, `bench/phase2_exit.py`, `bench/edit_slice/`,
`tests/importer/`, `tests/spec/` or `bench/heldout/posthoc*` was edited
(`git diff --stat` on those paths is empty). The run was made on 2026-10-05 by a fresh agent
that had not seen the importer's output for any app. No model or API call was made:
`--no-llm`, 0 tokens, $0.

**Headline: the reserve app fails the exit.**
- The exit script counts 1/5 tables (20%) and 0/19 routes (0%), and fidelity fails.
- On review it is 0/8 tables and 0/19 routes.
- Layer R ran with **no logged-in caller**: the importer found no login and no request
  bodies.
- One confound comes from the vendoring, not the importer (§4.1). To make the app run on
  flask-jwt-extended 4, I added a compatibility shim. It hides `jwt_required` and
  `current_user` from the Flask front end's static analysis, which recognises them by their
  `flask_jwt_extended` import origin. So the 12 R1 refutations, and probably the missed
  article ownership, are partly my doing. On my reading of the code the shim does not
  change pass/fail: with no login, no route can be verified either way. That reading is an
  inference, not a measurement.

## 1. The table (the frozen, first and only run)

| app | framework | tables verified / total (outside API) | routes verified / total | unreachable | fidelity | proposals (true by review) | import wall | measurement wall | review time |
|---|---|---|---|---|---|---|---|---|---|
| reserve: realworld | Flask + Flask-SQLAlchemy, flask-jwt-extended | 1 / 5 = 20.0% (**3 outside API**) | 0 / 19 = 0.0% | 0 routes; 0 items `unreachable-by-probe` | **fail**: variant 2 has 12 findings (every `jwt_required` route's R1); scenarios 22, steps 82, answers 80/82, oracle 51/82 | 3 (3 true) | 1.06 s | 1.84 s | 1 min 58 s |

- **Recomputation:** `phase2_exit.recompute` agrees with the report's summary.
- **Items:** 6 verified, 2 verified (corrected), 12 refuted, 30 unexercised, 1 unclear.
- **Excluded framework routes:** 1 (`/static/{filename}`).
- **Stage times** (`rules.timing`): extract 0.28 s, probe 0.17 s, correct 0.01 s,
  mine 0.02 s, variant 2 0.14 s; total 0.65 s.
- **Baseline** (recorded as upstream defects), 2 items:
  - `POST /api/users` without a body → 500;
  - `POST /api/users/login` without a body → 500.
  - Both are real: upstream's marshmallow `pre_load` reads `data['user']` (`KeyError`).
- **Columns:**
  - "import wall" is `import_s`, the timed `python -m lattice.importer` subprocess.
  - "measurement wall" adds recomputation and the fidelity subprocess.
  - The review time is an agent's tool-call wall time, 17:58:51–18:00:49 UTC, not a
    human's.
- **Raw outputs:**
  - the import: `bench/heldout/reserve/realworld/`;
  - the exit record: `bench/heldout/reserve/results/realworld.json`, `.md` and `.wall_s`;
  - the console log: `results/run.log`;
  - start and end timestamps: `results/run.started` (17:58:44Z–17:58:46Z).

### The 90% bar (tables AND routes) and the exit

| app | tables ≥ 90% | routes ≥ 90% | fidelity | exit |
|---|---|---|---|---|
| realworld | no (20.0%; **0/8 on review**) | no (0.0%; 0/19 on review) | fail | **fail** |

The frozen reserve run fails Phase 2 exit criteria 1 and 2.

## 2. How the run was made

1. **Vendoring.**
   - Source: a fresh clone outside the repo, at commit
     `4b95fb2227dfeb5dd1a45d89b2bf48630b93fd28` (2019-09-01).
   - Destination: `conduit/` went to `bench/import_repos/realworld/conduit/`, and `LICENSE`
     was copied unchanged (MIT, verified with `cmp`).
   - Lattice-owned files: `PROVENANCE.md`, `__init__.py` (`make_app`, `dispose`), `seed.py`
     (`ROLES = {user: alice}`, `REFUSED = {}`, `USER_IDS`, `login_headers`) and
     `fidelity.py` (22 scenarios from the upstream tests, README and RealWorld API spec,
     recorded against the running app).
   - Upstream has no `.env` file and no dotenv loading. It reads `CONDUIT_SECRET` and
     `DATABASE_URL` from `os.environ`, and `LatticeConfig` overrides both.
2. **Dependencies.** `flask-apispec`, `flask-bcrypt`, `flask-caching`, `flask-migrate` and
   `unicode-slugify` were added to the `phase2_apps` group in `pyproject.toml` / `uv.lock`.
   No existing pin changed.
3. **Smoke test.** `bench/heldout/reserve/test_reserve_app.py` passes 2/2. It is a copy of
   `bench/heldout/test_heldout_apps.py`, and its worker `_reserve_probe.py` is a copy of
   `_heldout_probe.py`. The test checks that:
   - the app boots and seeds;
   - `alice` logs in through `POST /api/users/login`;
   - anonymous `GET /api/user` gets 401;
   - all 22 scenarios replay;
   - nothing is written outside `tmp_path`;
   - `LICENSE` and `PROVENANCE.md` are present and every `LATTICE MOD` file is listed.
4. **The frozen run.** `bench/heldout/reserve/run_exit.py` is a copy of
   `bench/heldout/run_exit.py` whose `HELD_OUT` is `{"realworld": ...}` and whose work dir is
   `bench/heldout/reserve`.
   - It calls the exit script's own `measure()` and `markdown()` unchanged. So the single
     import was `python -m lattice.importer <repo> --package bench.import_repos.realworld
     --rules-only --no-llm --no-corpus --out bench/heldout/reserve/realworld`.
   - It ran once, 2026-10-05 17:58:44–17:58:46 UTC. There was no crash, so there was no
     re-run of any kind.

## 3. What the importer saw

- **No callers.**
  - The importer found no login it could call: the login route takes
    `{"user": {"email", "password"}}` through flask-apispec `use_kwargs(user_schema)` with a
    marshmallow `pre_load` envelope.
  - It found no minter: there is no flask-jwt-extended minter in the frozen importer.
  - The front end extracted no request body for any route. All 40 probes sent `json=null`
    and were either anonymous or carried a "malformed" token sent with a `Bearer` scheme.
    The app wants `Token`, so that probe tests a wrong scheme (401 "Missing 'Token' type"),
    not a bad token.
  - The profile's auth note reads "the app's own auth (flask, bearer) is not lifted".
- **Every contract is `auth='Public'`.** The `jwt_required` guards were not recognised
  (§4.1).
- **R3 sites missing.** None of the app's 12 `raise InvalidUsage.*()` sites became an R3
  item: 404 "Article not found", 404 "User not found" and 422 "User already registered".
  Each route got only a generic "no unstated 5xx" item.

## 4. Manual review (one pass, read-only)

The review used `report.md`, `report.json` (items and all 40 probes), `proposals.json`,
`candidate_spec.py` and the exit record. Nothing was accepted or rejected. The ground truth
is the code read while vendoring and the answers recorded in `fidelity.py`.

### 4.1 Confound: the vendoring shim hides flask-jwt-extended from the front end

- **What the shim does.** flask-jwt-extended 4 changed three names upstream imports:
  - `@jwt_required` is now a factory;
  - `@jwt_optional` is gone;
  - `current_user` raises `RuntimeError` outside a JWT view, where 3.x returned `None`.

  So the vendored files import these three names from a Lattice-owned
  `conduit/_lattice_compat.py`, which restores the 3.x behaviour.
- **What the front end looks for.** It recognises guards and callers by their import
  origin:
  - guards: `lib == "flask_jwt_extended" and qual.endswith("jwt_required")`
    (`lattice/importer/frontends/flask_app.py:831`);
  - callers: `CALLER_NAMES` (`flask_jwt_extended:current_user`, line 111).
- **What happens to the shim's names.** They resolve into the package instead.
  `package_decorator` finds no inner wrapper in the shim's
  `def jwt_required(fn): return _jwt_required_factory()(fn)`, so it yields no guard, and
  `current_user` is not a caller name.
- **Consequences:**
  - all 19 routes were inferred `Public`, and the 12 `jwt_required` routes were refuted;
  - plausibly, the `author_id=current_user.profile.id` scoping on article update/delete
    and comment delete was missed (Article came out `shared`; UserProfile came out unclear
    with "no route ... sets UserProfile.user_id from the caller").
- **A shim-free vendoring was possible:**
  - rewrite each decorator in place (`@jwt_required()`, `@jwt_required(optional=True)`);
  - keep `from flask_jwt_extended import current_user`;
  - restore the 3.x `None` through a `before_request` default in the Lattice-owned
    `make_app`.

  I saw this only while reviewing the output, so I did not re-vendor or re-run.
- **Estimated effect (inference, not measured):**
  - With the guards recognised, the 12 R1 items would predict `Authenticated`. But the run
    still had no logged-in caller.
  - After the post-hoc rounds, R1 items no longer count as verified without callers. So
    they would most likely be `unexercised`, not verified.
  - Routes would stay near 0/19 and the exit would still fail.
- **Recommendation for whoever owns the exit:**
  - read the 12 refutations as a vendoring artefact, not an importer inference error;
  - count the login/request-body gap (flask-apispec / webargs / marshmallow envelopes) as
    the importer's own failure.

### 4.2 Item by item

| item(s) | verdict | importer right? |
|---|---|---|
| `T1 Article` "shared: any user may read them" | **verified** (two anonymous reads answered 200) | **Wrong.** Reads are public, but writes are author-scoped: `PUT /api/articles/<slug>` filters `author_id = caller's profile` (a non-author gets 404, recorded), and so does `DELETE` (a non-author gets an upstream 500). `shared` misses that rule. The probe was also anonymous, where §1.1 asks for "a non-admin reads another user's row". Likely related to §4.1 (`current_user` not recognised). |
| `R3 GET /api/profiles/{username} 5xx`, `GET /api/articles`, `GET /api/articles/{slug}`, `GET /api/tags`, `GET /api/articles/{slug}/comments` (no unstated 5xx) | verified (5) | **Right, all 5.** None of these routes has a 5xx path I know of. The evidence for profiles is thin: both probes asked for the nonexistent username "valid username" and got 404 "User not found", a site that is not an item. |
| `R3 POST /api/users 5xx`, `R3 POST /api/users/login 5xx` corrected to "states the app's 500" (baseline) | verified (corrected) (2) | **Right, both, and both are real upstream defects:** a missing `user` envelope raises `KeyError` in the schema's `pre_load`. |
| R1 of the 12 `jwt_required` routes (`GET`/`PUT /api/user`, `POST`/`DELETE …/follow`, `POST /api/articles`, `PUT`/`DELETE /api/articles/{slug}`, `POST`/`DELETE …/favorite`, `GET /api/articles/feed`, `POST …/comments`, `DELETE …/comments/{cid}`) predicted Public | refuted, "anonymous refused" | **Right, all 12.** Anonymous gets 401 "Missing Authorization Header" (recorded in fidelity). No correction was proposed, which is right with no callers. The wrong Public prediction comes from the shim (§4.1). |
| R3 5xx of those 12 routes | unexercised, "no probe got past the auth check" | right (no login) |
| R1 of the 7 open routes (`POST /api/users`, `POST /api/users/login`, `GET /api/profiles/{u}`, `GET /api/articles`, `GET /api/articles/{slug}`, `GET /api/tags`, `GET …/comments`) predicted Public | unexercised, "no callers" | **The prediction is right for all 7** (`jwt_optional` or no guard). The verdict is the conservative one with no role caller, and acceptable. |
| `T1 Tags`, `T1 Comment` "no probe reached it" | unexercised → `unreached` | **Wrong, both.** `GET /api/tags` answered 200 with the seeded tag names, and `GET /api/articles/{slug}/comments` answered 200 with comment rows (both in `report.json` `probes`). |
| `T1 User` "no probe reached it" | unexercised → `unreached` | **Wrong.** Article and comment answers embed the author's `username` and `email` from `users`. It is also a consequence of having no login. |
| `T1 UserProfile` "no route filters, checks or sets UserProfile.user_id from the caller" | unclear | **The sentence is false.** Every `jwt_required` view uses `current_user.profile`, and `POST /api/users` creates the profile. It is a 1:1 extension of the identity table. Unclear was the honest verdict, given that the front end did not see `current_user` (§4.1). |
| `T1 favoritor_assoc`, `tag_assoc`, `followers_assoc` "outside API: no route reads or writes …" | unexercised → **left out of the table denominator** | **Wrong, all 3:** <br>• favorite/unfavorite write `favoritor_assoc`, and `?favorited=` / `favoritesCount` read it; <br>• article create writes `tag_assoc`, and `?tag=` / `tagList` read it; <br>• follow/unfollow write `followers_assoc`, and the feed and `following` read it. <br>This is the same failure as H3: ORM relationship writes (`list.append`, `.remove`) are not seen as table writes. It turns 1/8 into the reported 1/5. |
| `T5 Tags/Comment/Article/UserProfile/User (id)` "no route creates X rows" | unexercised (5) | **Wrong, all 5.** `POST /api/users` creates User and UserProfile, `POST /api/articles` creates Article and Tags, and `POST …/comments` creates Comment. With no request bodies they were not probe-able, but the stated reason is false. |
| Missing items | none | Missed candidates: <br>• T5 unique keys `users.username`, `users.email`, `article.slug` (they appear only as proposals); <br>• T3 bounds from `String(80/100/120/300)`; <br>• T2 `users.password` (left out of the profile: "LargeBinary has no Lattice scalar"); <br>• the 12 R3 `InvalidUsage` sites (§3). |
| Proposals: `No two Article rows share slug`, `No two User rows share email`, `No two User rows share username` | pending, support 0 (static) | **True, all 3** (`unique=True` in the models and in the SQLite schema). |

### 4.3 Fidelity

- Fidelity fails on variant 2's 12 findings (the refuted R1s) and on the oracle.
- **Oracle, 51/82.** The spec says `Public` for every route and states no error answer,
  so every 401, 404 and 422 step is mispredicted.
- **Answers, 80/82: my error, not the importer's.** The two failing answers are
  `expect_body=""` on the empty 200 bodies of the two delete routes. My httpx worker reads
  an empty body as `""`; Lattice's `AppRuntime` reads it as `None`.
  `expect_body=None` (any body) was the right encoding. I did not change `fidelity.py`
  after the run, and fidelity fails without these two steps anyway.

### 4.4 Precision summary (review)

| | realworld |
|---|---|
| items marked "verified" or "verified (corrected)" that are right | **7 / 8** (5 R3 no-5xx, 2 R3 baseline corrections; `T1 Article` wrong) |
| tables marked verified that are right | 0 / 1 |
| route verdicts "verified" that are right | n/a (0 verified) |
| refutations that are right | 12 / 12 (the predictions they refute come from the vendoring shim, §4.1) |
| corrections that are right | 2 / 2 |
| tables left out as "outside API" that really are | 0 / 3 |
| "unreached" / "no route creates" reasons that are true | 0 / 8 (T1 Tags, Comment, User; T5 ×5) |
| proposals true | 3 / 3 |

**On review:**
- tables 0/8;
- routes 0/19 verified, with a correct R1 *prediction* on 7/19 (the open ones);
- verified-item precision 7/8 = 87.5%;
- corrections 2/2;
- proposals 3/3.

## 5. Every LATTICE MOD

Full tables with line numbers are in `bench/import_repos/realworld/PROVENANCE.md`. Every
vendored `.py` file also starts with `# ruff: noqa`. In all, 40 lines changed: 19 import
paths, 20 compatibility lines and 1 marker comment. No model, schema field, guard or route
logic was changed.

**Import paths (19 lines):** `conduit.` → `bench.import_repos.realworld.conduit.`, in
`app.py`, `utils.py`, `extensions.py`, `articles/{models,serializers,views}.py`,
`user/{models,views}.py` and `profile/{models,views}.py`.

**Compatibility (20 lines + 1 marker comment):**
1. `extensions.py` (2 lines): `Model` is imported from `flask_sqlalchemy.model`.
   Flask-SQLAlchemy 3 no longer exports it at the top level.
2. `extensions.py` (1 line): `jwt.user_loader_callback_loader(jwt_identity)` →
   `jwt.user_lookup_loader(lambda _h, d: jwt_identity(d['sub']))`, for flask-jwt-extended 4.
3. `user/models.py` (1 line): `db.Binary(128)` → `db.LargeBinary(128)`. SQLAlchemy 2 removed
   the alias.
4. `app.py` (4 lines): the three `cors.init_app(<blueprint>)` calls are guarded to run once
   per process. Flask ≥ 2.3 refuses `after_request` on an already-registered blueprint, so a
   second `create_app()` failed.
5. Six import lines (`articles/models.py`, `profile/models.py`, `articles/views.py`,
   `profile/views.py`, `user/views.py` ×2): `jwt_required`, `jwt_optional` and
   `current_user` come from the Lattice-owned `conduit/_lattice_compat.py` (3.x semantics on
   flask-jwt-extended 4). **This is the mod that confounds the run (§4.1).**
6. `articles/views.py` (2 lines): `use_kwargs(..., location='query')` on `GET /api/articles`
   and the feed. webargs 3 read the query string by default; webargs 8 reads only the JSON
   body.
7. `articles/views.py` (1 line + 1 marker comment): the feed's
   `join(current_user.profile.follows)` → `join(... .subquery())`. SQLAlchemy 2 no longer
   coerces a `Query` join target, and the feed answered 500.
8. `user/serializers.py`, `articles/serializers.py` (3 added lines): `unknown = 'exclude'`
   in `Meta`. marshmallow 2 ignored unknown fields; marshmallow 3 rejects them.

**Harness choices in the Lattice-owned `LatticeConfig`, not upstream mods:**
- SQLite at `db_path`, a fixed `SECRET_KEY`, `DEBUG=False`;
- `BCRYPT_LOG_ROUNDS = 4`, upstream's own `TestConfig` value;
- two compatibility settings:
  - `CACHE_TYPE = "SimpleCache"`: Flask-Caching 2 dropped `'simple'`, and no route uses the
    cache;
  - `JWT_VERIFY_SUB = False`: upstream's identity is the integer user id, and PyJWT ≥ 2.10
    rejects a non-string `sub`;
- `make_app` runs `db.create_all()`, because upstream ships no migrations.

Nothing needed stubbing: upstream sends no mail and calls no outside service.

**Upstream defects kept** (see PROVENANCE.md; several are in `fidelity.py` as recorded
500s):
- non-author article and comment deletes answer 500;
- a duplicate slug answers 500;
- a missing field or envelope answers 500;
- `?favorited=` lists every favorited article once the named user exists;
- `Article.favorited` and `is_favourite` are global, so a user can hold one favorite;
- a wrong password answers 404;
- register answers 200, not 201;
- `followers_assoc`'s foreign keys point at `userprofile.user_id`.

## 6. Contamination

1. **Direct search.** `grep -rniI` over `lattice/`, `tests/importer/`, `tests/spec/` and
   `bench/phase2_exit.py`:
   - "realworld", "real-world", "real world" and "gothinkster": **no hits**;
   - `favoritor`, `followers_assoc`, `tag_assoc`, `userprofile`, `InvalidUsage`,
     `JWT_HEADER_TYPE`, `"Token "`: **no hits**;
   - `git log` has no importer commit naming the reserve app.
2. **"conduit": 3 hits, all about H3 conduit-drf, not this app:**
   - `lattice/spec/probes.py:2202`: H3's `{"code": 404, "msg": ...}` error envelope;
   - `tests/spec/test_probes_soundness2.py:310` and `:342`: H3's `IsAdminOrOwner` and its
     200-with-`{"code": 404}` comment delete.

   They come from the post-hoc soundness rounds (`79991f1`, `cad36e5`), made after looking at
   H3's output.
3. **The RealWorld overlap.**
   - H3 conduit-drf and this reserve app implement versions of the same RealWorld
     ("Conduit") API spec: users, profiles, follows, favorites, tags, comments,
     author-owned content.
   - So the post-hoc rounds tuned the importer on an app from the same spec family as the
     reserve, before the freeze. The surface is not shared: H3 is Django + DRF with
     `/api/topics/` and a `{"code": ...}` envelope, while this app is Flask + flask-apispec
     with `/api/articles/` and `{"errors": {"body": [...]}}`.
   - The results argue against any benefit: no Flask/flask-jwt-extended/apispec handling
     came from it, and the reserve scores 0/19 routes.
4. **The plan's selection notes.** The plan's §6 row names this app's guards
   ("author-owned articles and comments, `Token` header scheme"), and an importer author
   could read it. The importer has no `Token`-scheme handling (it assumed bearer), so there
   is no sign it was used.

## 7. What these results suggest (for the record, not acted on)

- **Login and request bodies:** the Flask front end does not read flask-apispec / webargs
  `use_kwargs(<marshmallow schema>)`, nor a marshmallow `pre_load` envelope. So it has no
  login, no callers and no write bodies. This alone keeps every route unverified.
- **flask-jwt-extended minter:** the plan's §3 lists `create_access_token` inside an app
  context as a minter. The frozen importer has none (no `create_access_token` anywhere in
  `lattice/`).
- **Header scheme:** `JWT_HEADER_TYPE = 'Token'` is not read, so the "malformed token" probe
  is really a wrong-scheme probe.
- **"Outside API" and "unreached":** both still trust a front end that cannot see ORM
  relationship writes (`collection.append` / `.remove` on `secondary=` tables). Probe
  answers that do read a table (`GET /api/tags`, `GET …/comments`) are not credited as
  reaching it. This is now seen on H3 and on the reserve.
- **R3 sites:** a project-specific exception raised through a classmethod factory
  (`raise InvalidUsage.article_not_found()`, with an `app.errorhandler` mapping it to its
  `status_code`) is not found.
- **Vendoring guidance:** future compatibility shims should be applied in place
  (`@jwt_required()`) rather than through re-export modules, because the front ends key on
  library import origins (§4.1).
