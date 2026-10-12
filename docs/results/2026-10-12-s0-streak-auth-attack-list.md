# S0 (spike 1b): hand-written attack list for Streak's auth feature, from the spec only

Date: 2026-10-12. Node S0 of the L0 tranche on branch `next-phases`. No agent, no app run, no request sent, no LLM call, no tracked file in either repo changed. Exit criterion (from `docs/design/next-phases-plan.md`): a list of attacks, the rule each violates, and how many are derivable from the spec alone vs need app knowledge.

## Question

How much of an attack plan for a feature does the Streak spec already give? Concretely, for the auth feature (register, login, refresh rotation, logout, token expiry, account deletion, rate limiting, device registration as it touches sessions, and the access matrix of every endpoint for anonymous / other-user / owner callers): how many attacks can be written down from the spec alone, which rule does each violate, and which of them the existing scenarios and invariants already cover, so that the remainder is what a swarm would add.

## Method

Read, in this order: `docs/design/next-phases-plan.md`, `docs/design/spikes/01b-security-attack-module.md`, then Streak (read-only, `main`): `REQUIREMENTS.md`, `spec.py` (all 1829 lines: profile, `ACCESS` access matrix, every `Scenario`, the 16 invariants, the 13 `Unchecked` entries), `spec.trace.json` (rule ids R1..R148 with their REQUIREMENTS.md line numbers, scenario links, unchecked list), and `CONTEXT.md` (harness facts: what a step cannot do). For layer X I read only the docstring of `lattice/spec/race.py`.

Conventions used in the table:

- **Rule ids.** `REQUIREMENTS.md` itself carries no R-numbers; the R-numbers are the ids in `spec.trace.json` (R3 is line 75, R29 is line 107, and so on). Every R-number below was looked up there. The assumption block ids (G1..G11, M1..M18) are not trace rules (R2 is the context rule that introduces them). Where a behaviour is only stated there I say "no R id" and cite M-id and line. "none stated" means I searched `REQUIREMENTS.md` and found no sentence for it.
- **Flag.** DERIVABLE-FROM-SPEC means the expected defence (the oracle) is in the spec's own words. I split it, as a judgement of mine, into (M) mechanical: the request follows from the rule's own limits, states, roles or ids (send not-X, the boundary, the other user), and (G) generic idea: the oracle is in the spec but choosing the input comes from general security knowledge (replay the stored hash, truncation at 72 bytes, duplicate JSON keys, path variants). NEEDS-APP-KNOWLEDGE means the attack or its oracle depends on something the spec does not state: schema or collation, framework routing, the signing key, deployment, timing.
- **Existing coverage.** `covered` = a named scenario or invariant in `spec.py` sends essentially this request and checks this oracle. `partial` = same endpoint or rule, but not the distinguishing input. `new` = nothing in `spec.py` exercises it. Judged by reading the steps, not by running the checker. Scenario names are exact `Scenario(...)` names from `spec.py` (access cells use the trace's `access__<action>__as_<caller>` names). Invariant coverage via layer X is taken from the `race.py` docstring and is probabilistic; I did not run it, so whether it flags anything on Streak is not known.
- **DSL column**, what writing the attack as a spec scenario needs today: `Y` expressible now; `H` needs request or response headers or a raw body (backlog item 4); `V` needs a value carried from an earlier response (a token, a cursor, an id); `C` needs concurrency; `T` timing or statistical sampling; `F` deployment or config control.
- **Class** is one of: authz, bounds, mass-assignment, state/lifecycle, race, enumeration/timing, rate-limit, injection, replay.
- Caller notation: `[anon]`, `[ann]` (user 1, America/New_York, installation `u(1)` = `00000000-0000-4000-8000-000000000001`, device 1 with token `tok-ann-1`), `[ben]` (user 2, UTC, installation `u(2)`). Seeded refresh token plaintexts and hashes are the CONTEXT.md pairs (`rt-ann-live` -> `c51763de...`). Test clock `2026-03-04T12:00:00Z`.
- Out of scope per the spike doc: dependency CVEs, TLS, deployment config, secrets in the repo. Rows D09, D16 and G09 are kept only because the spec or CONTEXT.md states a property of them.

## Attack table

### A. Register (R8-R12)

| ID | Class | Request(s) | Rule violated | Expected defence (per spec) | Flag | Existing coverage | DSL |
|---|---|---|---|---|---|---|---|
| A01 | mass-assignment | POST /v1/auth/register [anon] {email:"m1@example.com", password:"password-m1", id:1, change_seq:99, hashed_password:"x", created_at:"2000-01-01T00:00:00Z"} | R8 (answer is `{id,email,timezone}`), R35 (`change_seq` starts at 0); unknown fields: none stated | 201 with a server-assigned id (not 1: ann must not be overwritten), `change_seq` 0, hash = bcrypt of the password; or 422. Never a changed row 1 | DERIVABLE-FROM-SPEC (G) | new | Y |
| A02 | bounds | POST /v1/auth/register [anon] with wrong JSON types: {email:["a@b.c"], password:12345678, timezone:{}}; {email:null,...}; {password:null,...} | R9, R5 | 422 `validation_failed` with the offending `field`; no user row; never 5xx | DERIVABLE-FROM-SPEC (M) | partial: invalid_email_is_refused (string-valued bad emails and Mars/Base only); password_length_bounds | Y |
| A03 | bounds | POST /v1/auth/register [anon] {email: "a"*250+"@example.com", password:"password-x"} and a 1 MB `password` string | R9 (password <= 72 bytes -> 422); email length: none stated | 422 for the long password; the long email must be 201 or 422, never 5xx or a truncated stored address | NEEDS-APP-KNOWLEDGE | new | Y |
| A04 | enumeration/timing | POST /v1/auth/register [anon] {email:"ANN@EXAMPLE.COM"} and {email:"Ann@Example.com"}, password valid | R10 | 409 `email_taken`, no user created | DERIVABLE-FROM-SPEC (M) | covered: duplicate_email_is_refused; invariant one_account_per_email | Y |
| A05 | injection | POST /v1/auth/register [anon] {email:"ann@example.com "} (trailing space), {email:"ann@example.com\u0000"}, a fullwidth / Turkish-dotless-i case variant of ann's address | R10 (compared case-insensitively), R9 (must be valid) | 409 `email_taken` or 422; never 201 for an address the DB collation treats as equal to ann's | NEEDS-APP-KNOWLEDGE | new | Y |
| A06 | injection | POST /v1/auth/register [anon] {timezone:"../../etc/passwd"}, "/etc/localtime", "Local", "", "UTC+5", "Europe/Berlin\u0000" | R9 (IANA zone name; anything else 422) | 422 `validation_failed` field `timezone`, no user, no file read | DERIVABLE-FROM-SPEC (G) | partial: invalid_email_is_refused (Mars/Base only) | Y |
| A07 | bounds | POST /v1/auth/register [anon] {timezone:"europe/berlin"} and "EUROPE/BERLIN" | R9 (IANA zone name); case rule: none stated | 201 or 422, not stated; must not 5xx and `users.timezone` must be a value the clock logic (R45) can use | NEEDS-APP-KNOWLEDGE | new | Y |
| A08 | state/lifecycle | POST /v1/auth/register [anon] with a failing request (409 `email_taken`, 422 bad timezone), then count `users`, `notification_prefs` | R7, R10, R11, R9 | No `users` row and no `notification_prefs` row from the refused request (an orphan prefs row is not caught by `one_prefs_row_per_user`) | DERIVABLE-FROM-SPEC (M) | partial: duplicate_email_is_refused and invalid_email_is_refused check `users` count only | Y |
| A09 | race | POST /v1/auth/register [anon] x2 concurrently, same email, different passwords | R10; invariants one_account_per_email, one_prefs_row_per_user | One 201, one 409 (or 5xx-free equivalent); one user, one prefs row; the loser leaves nothing | DERIVABLE-FROM-SPEC (M) | covered: invariant one_account_per_email + layer X self-race of the register step (probabilistic, not run here) | C |
| A10 | enumeration/timing | POST /v1/auth/register [anon] with an existing vs a fresh email, compare answers | R10 (the 409 is the specified oracle) | By design the 409 reveals that an address is registered; no defence is specified | DERIVABLE-FROM-SPEC (M) | covered: duplicate_email_is_refused (behaviour only; the disclosure is not assessed) | Y |
| A11 | enumeration/timing | Any response, any route: search bodies (success, error, `current` in 409 `version_conflict`, sync `data`) for `hashed_password`, `token_hash`, bcrypt prefix `$2`, a refresh token | R12 | No response contains the password, its hash or a refresh token's hash | DERIVABLE-FROM-SPEC (M) | partial: profile private columns User.hashed_password, RefreshToken.token_hash; scope of the private-column check across all responses not verified | Y |

### B. Login (R13, R14, R17)

| ID | Class | Request(s) | Rule violated | Expected defence (per spec) | Flag | Existing coverage | DSL |
|---|---|---|---|---|---|---|---|
| B01 | enumeration/timing | POST /v1/auth/login [anon] {email:"ann@example.com", password:"wrong-pass", installation_id:u(9)}; {email:"nobody@example.com", ...} | R14 | 401 `invalid_credentials`, no refresh token written | DERIVABLE-FROM-SPEC (M) | covered: login_with_bad_credentials_is_refused | Y |
| B02 | state/lifecycle | POST /v1/auth/login [anon] {ann's email, WRONG password, installation_id:u(1)} with ann's live refresh token for u(1) already present | R7, R14, R17 (a login revokes before it writes the new one: only a successful login) | 401; ann's token for u(1) still unrevoked (anyone who knows her email and installation id must not be able to sign her out) | DERIVABLE-FROM-SPEC (M) | new | Y |
| B03 | authz | POST /v1/auth/login [anon] {ben's email, ben's password, installation_id:u(1)} (ann's installation) with ann's live refresh token on u(1) | R17 ("of the same user and installation"), R27 | 200 for ben; ann's refresh tokens and device 1 untouched | DERIVABLE-FROM-SPEC (M) | new | Y |
| B04 | bounds | POST /v1/auth/login [anon] {installation_id:"not-a-uuid"}; missing; ""; 5000 chars; a number | R13 (UUID), R5, R43 (a malformed id -> 422) | 422 `validation_failed` field `installation_id`, no token written | DERIVABLE-FROM-SPEC (M) | new: every login scenario uses a valid UUID | Y |
| B05 | bounds | POST /v1/auth/register password "a"*72 then POST /v1/auth/login with "a"*72+"X" (73 bytes) and "a"*72+"\u00e9" | R9 (72 bytes), R14 | 401 `invalid_credentials` (bcrypt must not truncate to the first 72 bytes and accept) | DERIVABLE-FROM-SPEC (G) | new: password_length_bounds is register-only | Y |
| B06 | state/lifecycle | POST /v1/auth/login [anon] {email:"ANN@EXAMPLE.COM", password:"password-ann", installation_id:u(9)} | R8, R10 imply a case-insensitive identity; login case rule: none stated | 200 or 401, spec silent; record which, and that it is consistent with R10 | DERIVABLE-FROM-SPEC (M) | new | Y |
| B07 | enumeration/timing | POST /v1/auth/login [anon] x200 each: unknown email vs known email with wrong password; compare latency distributions | R14 states only equal codes; no timing property stated | No stated defence; a bcrypt-only-when-user-exists branch shows as a latency gap | NEEDS-APP-KNOWLEDGE | new | T |
| B08 | enumeration/timing | Compare full bodies (`message`, `field`) of B01's two answers byte for byte | R14, R4 | Same code; message text must not distinguish the two cases | DERIVABLE-FROM-SPEC (M) | partial: B01 scenario matches a subset (code) only | Y |
| B09 | rate-limit | POST /v1/auth/login [anon] x1000 {ann's email, wrong passwords} | R29 limits signed-in users only; unauthenticated limits: no R id (assumption M16, REQUIREMENTS.md line 66: "not limited in v1") | No defence specified; expected result is 1000 x 401, never 429, never lockout | DERIVABLE-FROM-SPEC (M) | new | Y |
| B10 | bounds | POST /v1/auth/login [anon] x10000 same user, each with a fresh installation_id | R17 limits live tokens per installation only; per-user cap: none stated | No cap is specified: `refresh_tokens` grows without bound (record row growth) | DERIVABLE-FROM-SPEC (M) | new | Y |
| B11 | race | POST /v1/auth/login [anon] x2 concurrently, same user, same installation_id | R17; invariant one_live_refresh_token_per_installation | At most one unrotated, unrevoked token afterwards | DERIVABLE-FROM-SPEC (M) | covered: invariant one_live_refresh_token_per_installation + layer X self-race of a login/refresh step (probabilistic, not run here) | C |
| B12 | injection | POST /v1/auth/login [anon] {email:"' OR '1'='1", password:"x' OR '1'='1", installation_id:"'; DROP TABLE users;--"} | R14, R5 | 401 or 422; never 200, never 5xx | DERIVABLE-FROM-SPEC (G) | new | Y |
| B13 | state/lifecycle | POST /v1/auth/login [anon] with a JSON body carrying extra keys {user_id:2, sub:"2", sid:"x", expires_in:99999999} | R13, R15 (`sub` = the user id, 900 s) | 200 for the credentials' own user; `expires_in` 900; JWT `sub` the user id | DERIVABLE-FROM-SPEC (M) | new | Y |

### C. Refresh rotation and reuse (R18-R20)

| ID | Class | Request(s) | Rule violated | Expected defence (per spec) | Flag | Existing coverage | DSL |
|---|---|---|---|---|---|---|---|
| C01 | replay | POST /v1/auth/refresh [anon] {refresh_token:"rt-ann-live"} (live, seeded) | R18 | 200, old token `rotated_at` set, one new live token, new expiry now+30 d | DERIVABLE-FROM-SPEC (M) | covered: refresh_rotates_the_token | Y |
| C02 | replay | POST /v1/auth/refresh [anon] {refresh_token:"rt-ann-rotated"}, then {"rt-ann-live"} | R20, R7, R19 | 401 `refresh_token_reused`; every token of the installation revoked; the next refresh 401 `invalid_refresh_token` | DERIVABLE-FROM-SPEC (M) | covered: refresh_token_reuse_revokes_the_session | Y |
| C03 | replay | POST /v1/auth/refresh [anon] with an unknown, an expired and a revoked token | R19 | 401 `invalid_refresh_token`, nothing written | DERIVABLE-FROM-SPEC (M) | covered: refresh_token_unknown_or_expired_is_refused | Y |
| C04 | bounds | PUT /test/clock to expires_at-1s, expires_at, expires_at+1s; POST /v1/auth/refresh with the seeded live token | R16 (valid for 30 days), R19 | Before expiry 200; at and after the instant 401 `invalid_refresh_token` (the boundary instant itself is not stated) | DERIVABLE-FROM-SPEC (M) | new: only a long-expired token is tested | Y |
| C05 | race | POST /v1/auth/refresh [anon] x2 concurrently with the same live token | R18, R20; invariant one_live_refresh_token_per_installation | Exactly one 200; the other 401 `refresh_token_reused` (or none 200); never two live tokens | DERIVABLE-FROM-SPEC (M) | covered: invariant one_live_refresh_token_per_installation + layer X self-race (probabilistic, not run here) | C |
| C06 | race | POST /v1/auth/refresh [anon] concurrently with POST /v1/auth/logout [ann], same installation | R23 (revokes every refresh token of the installation), R18 | After both finish no live token for the installation (the one-live-token invariant is satisfied by one stray token, so it does not catch this) | DERIVABLE-FROM-SPEC (G) | new: no scenario holds both steps, so no pair race is built | C |
| C07 | state/lifecycle | POST /v1/auth/refresh [anon] with a token that is both rotated and revoked (replayed after C02) | R19 (revoked) vs R20 (rotated); precedence: none stated | Spec silent: `refresh_token_reused` again or `invalid_refresh_token`; must be one stable answer and never a new session | DERIVABLE-FROM-SPEC (M) | new | Y |
| C08 | bounds | POST /v1/auth/refresh [anon] {}, {refresh_token:null}, "", 12345, ["x"], a 1 MB string | R5 vs R19 (which applies is not stated) | 422 `validation_failed` field `refresh_token` or 401 `invalid_refresh_token`; never 5xx, nothing written | DERIVABLE-FROM-SPEC (M) | new | Y |
| C09 | injection | POST /v1/auth/refresh [anon] {refresh_token:"' OR 1=1 --"}, "%", "_", "\u0000" | R19 | 401 `invalid_refresh_token`; a LIKE wildcard must not match a stored hash | DERIVABLE-FROM-SPEC (G) | new | Y |
| C10 | replay | POST /v1/auth/refresh [anon] {refresh_token: <the stored digest> `c51763de...`} with the live row present | R16 (only the digest is stored), R19 | 401 `invalid_refresh_token`: possession of the stored hash is not possession of the token | DERIVABLE-FROM-SPEC (G) | new | Y |
| C11 | authz | POST /v1/auth/refresh with an ACCESS token as `refresh_token`; GET /v1/me with a REFRESH token as Bearer | R19, R22 | 401 `invalid_refresh_token` / 401 `invalid_token` | DERIVABLE-FROM-SPEC (G) | new | V |
| C12 | authz | POST /v1/auth/refresh, /v1/auth/login or /v1/auth/register with a garbage or expired `Authorization: Bearer ...` header | R21 (these routes need no token); behaviour with a bad header: none stated | Served as if no header, or 401; spec silent. Record which | DERIVABLE-FROM-SPEC (G) | new | H |
| C13 | replay | DELETE /v1/me [ann] then POST /v1/auth/refresh {"rt-ann-live"} (seeded live token) | R26 (refresh tokens deleted), R19 | 401 `invalid_refresh_token` | DERIVABLE-FROM-SPEC (M) | partial: delete_me_removes_everything checks the table is empty and login fails, not the refresh answer | Y |
| C14 | replay | Collect >= 1000 refresh tokens from logins; test length >= 32 bytes, uniqueness, no sequence or time pattern | R16 (>= 32 bytes entropy); marked Unchecked in spec.py | Tokens indistinguishable from random | DERIVABLE-FROM-SPEC (M) | new: declared Unchecked R16 (part) | T |

### D. Access token, expiry and session state (R15, R21, R22) and unauthenticated surface

| ID | Class | Request(s) | Rule violated | Expected defence (per spec) | Flag | Existing coverage | DSL |
|---|---|---|---|---|---|---|---|
| D01 | authz | Every /v1 route except register, login, refresh [anon] without Authorization (21 routes incl. health expectation 200) | R21, R3 | 401 `not_authenticated`; no row changed; `GET /health` 200 | DERIVABLE-FROM-SPEC (M) | covered: access__*__as_anon (22 cells, R21 links) | Y |
| D02 | replay | clock +16 min, then each WRITE route [ann]: POST /v1/auth/logout, PUT /v1/me, DELETE /v1/me, POST /v1/devices, DELETE /v1/devices/1 | R22, R7 | 401 `token_expired`; nothing written; session not revoked | DERIVABLE-FROM-SPEC (M) | partial: access_token_expires_after_15_minutes uses GET /v1/me and POST /v1/habits only | Y |
| D03 | bounds | clock to issue+14:59, +15:00, +15:01; GET /v1/me [ann] | R15 (expires 15 minutes after issue), R22 | 200 before, 401 `token_expired` after; the exact instant is not stated | DERIVABLE-FROM-SPEC (M) | partial: access_token_expires_after_15_minutes tests +14 and +16 min | Y |
| D04 | injection | GET /v1/me with Authorization: "Bearer", "Bearer ", "Bearer a.b.c", "Bearer "+"A"*8192, non-ASCII, two Authorization headers | R22 (malformed -> `invalid_token`) | 401 `invalid_token` (or `not_authenticated` for empty); never 5xx | DERIVABLE-FROM-SPEC (G) | new: declared Unchecked R22 (part) | H |
| D05 | authz | GET /v1/me with "Basic <b64>", "bearer <valid>", "Token <valid>", "<valid>" (no scheme) | R21 vs R22; scheme handling: none stated | 401 with `not_authenticated` or `invalid_token`; spec silent. Never 200 for a non-Bearer scheme without it being a decision | DERIVABLE-FROM-SPEC (G) | new | H |
| D06 | authz | GET /v1/me with a JWT whose header says alg none / HS384 / RS256 (HS256 key as public key) / has jku or kid, signature empty or stripped | R15 (HS256), R22 (wrongly signed -> `invalid_token`) | 401 `invalid_token` | DERIVABLE-FROM-SPEC (G) | new: declared Unchecked R15, R22 (part) | H |
| D07 | authz | Take a valid token, change `sub` to 2 or `sid` to u(2) or `exp` far ahead, keep the signature; GET /v1/me | R22 | 401 `invalid_token` | DERIVABLE-FROM-SPEC (G) | new | V |
| D08 | authz | Valid-signature tokens with `sub` of a nonexistent user, `sub` as string/float/negative, missing `exp`, missing `sid` | R22 (user no longer exists -> `invalid_token`); missing claims: none stated | 401 `invalid_token`; never 5xx. Needs the signing key to mint | NEEDS-APP-KNOWLEDGE | new | H |
| D09 | authz | Offline guess of HABIT_SECRET_KEY from common values against a token captured from /auth/login; if found, forge sub=2 | R15 (signed with HABIT_SECRET_KEY); key strength: none stated | Key is not guessable; a deployment/config property the spec cannot state | NEEDS-APP-KNOWLEDGE | new | F |
| D10 | state/lifecycle | POST /v1/auth/logout [ann], then GET /v1/me [ann] with the SAME access token | R23 revokes refresh tokens only; access-token validity after logout: none stated | Spec silent: stateless JWT keeps working until exp (<= 15 min). Record, report as a gap | DERIVABLE-FROM-SPEC (M) | new | Y |
| D11 | state/lifecycle | After a `refresh_token_reused` (C02) GET /v1/me with the session's access token | R20 (session must log in again); access-token validity: none stated | Spec silent (same as D10); the session is half-alive for <= 15 min | DERIVABLE-FROM-SPEC (M) | new | Y |
| D12 | state/lifecycle | DELETE /v1/me [ann] then GET /v1/me [ann] with the old token | R26, R22 | 401 `invalid_token` | DERIVABLE-FROM-SPEC (M) | covered: delete_me_removes_everything | Y |
| D13 | state/lifecycle | Register C (new id n), login, DELETE /v1/me; register D; present C's old token | R26, R22 (user no longer exists -> `invalid_token`) | 401 `invalid_token` even if D received C's integer id (id reuse after hard delete) | NEEDS-APP-KNOWLEDGE | new | V |
| D14 | authz | Path variants anon: GET /v1/me/, //v1/me, /V1/me, /v1/me%2f, /v1/habits/../me, /v1/me?x=; the same variants against the rate-limit budget (section G) | R21, R3 | 401 or 404, never a 2xx without a token; every variant counts against the same budget | NEEDS-APP-KNOWLEDGE | new | Y |
| D15 | authz | For each declared path try an undeclared method or sibling: GET /v1/devices, GET /v1/devices/1, PUT /v1/habits/{id}, POST /v1/me, GET /v1/users/2, /v1/admin, /metrics, /debug/pprof, /v1/auth/users as anon and as ben | R27 (no roles or admins), R3 | 404 or 405 with the error shape; never 2xx with data | DERIVABLE-FROM-SPEC (G) | new | Y |
| D16 | authz | PUT /test/clock [anon] {now:"2031-01-01T00:00:00Z"} against an app started WITHOUT HABIT_TEST_CLOCK | R44 (real time in production), CONTEXT.md (route exists only in test mode) | 404; the clock cannot be moved by an unauthenticated caller outside test mode. In test mode the route is unauthenticated by design | DERIVABLE-FROM-SPEC (G) | new | F |
| D17 | bounds | Any route with X-Client-Version: "1.99.99", "2.0", "02.0.0", "2.0.0-beta", "999999999999999999999.0.0", ""; also on POST /v1/auth/login | R6 | 426 `upgrade_required` and nothing written; absent header served; order against 401/422: none stated | DERIVABLE-FROM-SPEC (M) | new: declared Unchecked R6 | H |

### E. Logout (R23)

| ID | Class | Request(s) | Rule violated | Expected defence (per spec) | Flag | Existing coverage | DSL |
|---|---|---|---|---|---|---|---|
| E01 | authz | POST /v1/auth/logout [anon] | R21, R23, R7 | 401 `not_authenticated`; ann's seeded device untouched | DERIVABLE-FROM-SPEC (M) | covered: access__logout__as_anon | Y |
| E02 | state/lifecycle | POST /v1/auth/logout [ann] with a second installation u(9) holding a device and live token | R23 | 204; only installation u(1)'s tokens and device gone; u(9) untouched | DERIVABLE-FROM-SPEC (M) | covered: logout_ends_only_this_installation; access__logout__as_ann | Y |
| E03 | authz | POST /v1/auth/login [ben, installation_id:u(1) = ann's], then POST /v1/auth/logout [ben] | R23 ("the caller's device row", "the caller's installation"), R28 | 204; ann's device 1 and ann's tokens for u(1) are untouched | DERIVABLE-FROM-SPEC (M) | new | V |
| E04 | state/lifecycle | POST /v1/auth/logout [ann] twice with the same token | R23 ("deletes ... if any") | 204 both times; no 5xx | DERIVABLE-FROM-SPEC (M) | new | Y |
| E05 | state/lifecycle | clock +16 min; POST /v1/auth/logout [ann] | R22, R7, R23 | 401 `token_expired`; the refresh token is NOT revoked (a session cannot be ended once the access token has lapsed: by design) | DERIVABLE-FROM-SPEC (M) | partial: expiry tested on other routes; logout not | Y |
| E06 | replay | POST /v1/auth/logout [ann], then POST /v1/auth/refresh with the seeded token of u(1) | R23, R19 | 401 `invalid_refresh_token` | DERIVABLE-FROM-SPEC (M) | partial: revocation itself in logout_ends_only_this_installation; revoked token answer in refresh_token_unknown_or_expired_is_refused | Y |

### F. Account deletion (R26)

| ID | Class | Request(s) | Rule violated | Expected defence (per spec) | Flag | Existing coverage | DSL |
|---|---|---|---|---|---|---|---|
| F01 | state/lifecycle | DELETE /v1/me [ann] with habits, completions, nudges, device, tokens seeded | R26 | 204; all of ann's rows gone, ben's habit 101 untouched; old token 401 `invalid_token`; login 401 `invalid_credentials` | DERIVABLE-FROM-SPEC (M) | covered: delete_me_removes_everything; access__delete_me__as_ann | Y |
| F02 | authz | DELETE /v1/me [anon] | R21, R7 | 401; ann's user row present | DERIVABLE-FROM-SPEC (M) | covered: access__delete_me__as_anon | Y |
| F03 | authz | DELETE /v1/me [ben] with ann's data seeded (habits, completions, nudges, device, tokens) | R26, R27 | 204; every one of ann's rows still present; ben's rows gone | DERIVABLE-FROM-SPEC (M) | partial: reverse direction only (ann deletes, ben's habit checked); ben's own children not checked | Y |
| F04 | authz | DELETE /v1/me?user_id=1 [ben]; DELETE /v1/me with body {id:1} | R26, R27 | Deletes the caller only | DERIVABLE-FROM-SPEC (G) | new | Y |
| F05 | state/lifecycle | DELETE /v1/me [ann], then register ann@example.com again, login, GET /v1/habits, /v1/sync, /v1/nudges | R26 (no tombstones), R10 (email free again) | 201 with a new id; every list empty; nothing inherited | DERIVABLE-FROM-SPEC (M) | new | V |
| F06 | race | DELETE /v1/me [ann] concurrently with POST /v1/habits, POST /v1/habits/{id}/completions, POST /v1/devices [ann] | R26 (permanently deletes habits, check-ins, devices) | After both: no rows of ann in any table (no orphan habits or devices of a nonexistent user) | DERIVABLE-FROM-SPEC (G) | new: no invariant states that child rows have a parent; X races only count-bound writes | C |

### G. Rate limiting (R29, R30)

| ID | Class | Request(s) | Rule violated | Expected defence (per spec) | Flag | Existing coverage | DSL |
|---|---|---|---|---|---|---|---|
| G01 | rate-limit | 120 x GET /v1/me, then 1 x POST /v1/habits, then GET /v1/me [ann]; GET /v1/me [ben] | R29, R30, R7 | 121st+ answer 429 `rate_limited`, writes nothing; ben unaffected | DERIVABLE-FROM-SPEC (M) | covered: rate_limit_120_per_minute | Y |
| G02 | rate-limit | 120 requests at 12:00:59, 1 at 12:01:00 (clock move), then 121 within one minute | R29 (fixed windows starting at second 0) | Counter resets at the clock minute; the 121st inside one window 429 | DERIVABLE-FROM-SPEC (M) | new: window rollover not exercised | Y |
| G03 | rate-limit | Log ann in on a second installation; alternate two tokens, 121 requests in one minute | R29 (per signed-in user) | The 121st is 429 regardless of token or installation | DERIVABLE-FROM-SPEC (M) | new | V |
| G04 | race | POST /v1/habits (distinct ids/names) x200 concurrently inside one window [ann] | R29 | No more than 120 succeed in the window | DERIVABLE-FROM-SPEC (G) | new: rate limit has no invariant, so layer X does not race it | C |
| G05 | rate-limit | POST /v1/completions/batch [ann] x120 with 100 items each in one minute | R30, R83 | All 120 accepted (1 request each) = up to 12,000 writes/min; the 101st item is 422 | DERIVABLE-FROM-SPEC (M) | covered: rate_limit_120_per_minute (batch of 5 counts once); batch_item_bounds | Y |
| G06 | rate-limit | clock +16 min (token expired) then 500 x GET /v1/me with the expired token | R29 ("signed-in user"), R22 | 401 `token_expired` x500, never 429 (unauthenticated is unlimited, a stated limit-gap) | DERIVABLE-FROM-SPEC (M) | new | Y |
| G07 | rate-limit | 120 x requests that answer 404/422 for [ann], then GET /v1/me | R29 ("requests"); whether refused requests count: none stated | Spec silent; record whether the 121st is 429 | DERIVABLE-FROM-SPEC (M) | new | Y |
| G08 | rate-limit | Read `Retry-After` on a 429 at second 0, 30, 59 of the window | R29 (header giving seconds until window ends) | Integer seconds equal to the window remainder | DERIVABLE-FROM-SPEC (M) | new: declared Unchecked R29 (part) | H |
| G09 | rate-limit | Run the app as 2 processes behind one port; 120 requests to each; one signed-in user | R29; in-memory limiter noted in CONTEXT.md | Combined 121st must be 429 (per user, not per process); a deployment property | NEEDS-APP-KNOWLEDGE | new | F |

### H. Device registration as it touches sessions (R31-R34)

| ID | Class | Request(s) | Rule violated | Expected defence (per spec) | Flag | Existing coverage | DSL |
|---|---|---|---|---|---|---|---|
| H01 | authz | POST /v1/devices [anon]; DELETE /v1/devices/1 [anon] | R21 | 401 `not_authenticated` | DERIVABLE-FROM-SPEC (M) | covered: access__add_device__as_anon; access__delete_device__as_anon | Y |
| H02 | bounds | POST /v1/devices [ann] with platform "web", push_token "" and 4097 chars, timezone "Nowhere/Land"; push_token 4096 and 1 accepted | R31, R5 | 422 `validation_failed` field = the offender, no row; 4096 and 1 -> 201 | DERIVABLE-FROM-SPEC (M) | covered: device_registration | Y |
| H03 | authz | POST /v1/devices [ben] {push_token:"tok-ann-1"} (ann's token) | R32 (moves to the caller), R31 | 200, row 1 now ben's: spec-sanctioned. Consequence: ann's nudge pushes (message text with habit data) go to ben. No defence specified | DERIVABLE-FROM-SPEC (M) | covered: device_push_token_takeover (behaviour; the abuse is not assessed) | Y |
| H04 | authz | POST /v1/devices [ben] {push_token:"TOK-ANN-1"} and "tok-ann-1 " (case / trailing space), valid other fields | R32 (never more than one row per push_token = exact token) | 201 with a NEW row; ann's row 1 unchanged (a case- or pad-insensitive unique index would merge them) | NEEDS-APP-KNOWLEDGE | new | Y |
| H05 | mass-assignment | POST /v1/devices [ben] {id:1, user_id:1, installation_id:u(2), platform:"ios", push_token:"tok-ben-9", timezone:"UTC", app_version:"1.0.0"} | R31 (body shape), R28, R27 | Row created for ben with a server id; ann's device 1 unchanged, no row for user 1 | DERIVABLE-FROM-SPEC (G) | new | Y |
| H06 | authz | POST /v1/devices [ben] {installation_id:u(1) (ann's), push_token:"tok-ben-1"}; then POST /v1/auth/logout [ann]; then POST /v1/auth/logout [ben] | R23 ("the caller's device row with that installation_id"), R27 | Each logout deletes only the caller's own row | DERIVABLE-FROM-SPEC (G) | new | Y |
| H07 | bounds | POST /v1/devices [ann] with installation_id "", "x"*10000, "'; DROP--", not a UUID; app_version 10 kB | R31 (installation_id and app_version not validated in the rule) | 201 or 422, not stated; never 5xx; a later logout (R23) must still work | DERIVABLE-FROM-SPEC (G) | new | Y |
| H08 | bounds | POST /v1/devices [ann] with push_token = "\u00e9"*4096 (8192 bytes) and "\u00e9"*4097 | R31 (1 to 4096 characters) | 201 for 4096 characters, 422 for 4097; an index key-length error would show as 5xx | NEEDS-APP-KNOWLEDGE | new: device_registration uses 4096 ASCII characters | Y |
| H09 | race | POST /v1/devices x2 concurrently, same push_token, one as ann and one as ben | R32; invariant one_device_per_push_token | One row with one owner; answers 201/200, never 5xx | DERIVABLE-FROM-SPEC (G) | partial: invariant one_device_per_push_token plus layer X self-race (same user; probabilistic, not run here); the two-user takeover pair is not built | C |
| H10 | bounds | POST /v1/devices [ann] x5000, distinct push_tokens | R31; per-user device cap: none stated | No cap is specified; record growth and that every nudge pass (R134) fans out to all of them | DERIVABLE-FROM-SPEC (M) | new | Y |
| H11 | authz | DELETE /v1/devices/1 [ben]; DELETE /v1/devices/999 [ann] | R28, R33 | 404 `not_found`, device 1 present | DERIVABLE-FROM-SPEC (M) | covered: access__delete_device__as_ben; unknown_ids_answer_not_found | Y |
| H12 | bounds | DELETE /v1/devices/{id} [ann] with id "1 OR 1=1", "-1", "0", "1.0", "01", "9223372036854775808" | R43 (malformed id -> 422), R28 | 422 `validation_failed` or 404; no row deleted; never 5xx | DERIVABLE-FROM-SPEC (G) | new | Y |

### I. Access matrix and cross-user access (R21, R27, R28)

| ID | Class | Request(s) | Rule violated | Expected defence (per spec) | Flag | Existing coverage | DSL |
|---|---|---|---|---|---|---|---|
| I01 | authz | Matrix cells: 22 actions x {anon, ben, ann} as declared in `ACCESS` (59 cells; ben has 16 because 6 actions have no foreign target) | R21, R27, R28 | anon 401 `not_authenticated`, ben 404 `not_found` (or own-scope empty 200), ann 2xx; nothing written on refusal | DERIVABLE-FROM-SPEC (M) | covered: access__<action>__as_anon / as_ben / as_ann (59 scenarios) | Y |
| I02 | authz | DELETE /v1/habits/{ben_habit}/completions/{ann_completion_id} [ben] | R82, R28 | 404 `not_found`; ann's completion still live | DERIVABLE-FROM-SPEC (M) | partial: completion_of_another_habit_is_not_found is same-user, other habit | Y |
| I03 | authz | POST /v1/habits/{ann_habit}/completions [ben] with ann's EXISTING completion id and identical content | R28, R79 (retry shape answers 200 with the existing row) | 404 `not_found`, not 200 with ann's completion | DERIVABLE-FROM-SPEC (M) | new: access__add_completion__as_ben uses a fresh id | Y |
| I04 | authz | POST /v1/habits [ben] with ann's habit id and identical content | R54 | 409 `id_conflict`; ann's habit unchanged, still hers | DERIVABLE-FROM-SPEC (M) | covered: habit_id_conflicts (same shape, ann posting ben's id) | Y |
| I05 | mass-assignment | PATCH /v1/habits/{ann_habit} [ann] {version:1, user_id:2, id:"<other uuid>", deleted_at:"2026-03-04T00:00:00Z", change_seq:0, created_at:"2000-01-01T00:00:00Z"}; POST /v1/habits [ann] with user_id:2 | R60 (subset of name, description, target_count, start_date, end_date, archived), R27 | Unknown fields ignored or 422; the habit stays ann's; no column outside the subset changes | DERIVABLE-FROM-SPEC (G) | new | Y |
| I06 | authz | POST /v1/completions/batch [ben] items = [ann's habit, ben's own habit, a nonexistent habit] | R84, R85, R28 | 200; item 1 and 3 `rejected` `not_found`, item 2 created; ann's habit gets no row | DERIVABLE-FROM-SPEC (M) | partial: access__batch__as_ben sends one foreign item | Y |
| I07 | authz | GET /v1/habits?user_id=1; GET /v1/sync?user_id=1; GET /v1/home?owner=1 [ben] | R27, R28 | Only ben's rows (empty); parameters ignored or 422 | DERIVABLE-FROM-SPEC (G) | new | Y |
| I08 | authz | GET /v1/sync?cursor=<crafted> [ben]: base64 of 0, -1, 2^63, ann's change_seq, ann's habit id; GET /v1/habits?cursor=<ann's habit id encoded> | R88, R94 (a cursor the app did not issue -> 422 `invalid_cursor`) | 422 `invalid_cursor` or an empty page; never ann's changes, never 5xx. The encoding must be learned from traffic | NEEDS-APP-KNOWLEDGE | partial: sync_limits_and_cursors and list_pagination send "bogus" only | V |
| I09 | enumeration/timing | Provoke errors: wrong Content-Type, invalid UTF-8, 10k-deep JSON, 1 MB body, huge integers on every JSON route; search bodies for stack traces, SQL, file paths, driver names | R4 (error shape), R5 | Only the stated envelope; no internals; 4xx not 5xx | DERIVABLE-FROM-SPEC (G) | new | H |
| I10 | enumeration/timing | Compare full bodies and latency of: ben GET /v1/habits/{ann_id} vs GET /v1/habits/{random uuid}; same for completions, nudge dismiss, devices | R28 ("exactly like an id that does not exist") | Byte-identical bodies, indistinguishable latency | DERIVABLE-FROM-SPEC (M) | partial: matrix and unknown_ids_answer_not_found compare the code only | T |
| I11 | enumeration/timing | POST /v1/habits [ben] with ann's habit id (409) vs a fresh id (201); POST completions with ann's id | R54, R79 vs R28 ("on every route") | Spec-mandated existence oracle: the 409 differs from a fresh id's 201. Two rules disagree (R28 vs R54/R79); no defence stated | DERIVABLE-FROM-SPEC (M) | covered: habit_id_conflicts; check_in_retry_and_id_conflict (behaviour; the tension is not assessed) | Y |
| I12 | race | POST /v1/nudges/{id}/dismiss [ann] x2 concurrently | R138 (second answers 409 `nudge_dismissed`), R142 (no push after a 409) | One 200, one 409; one clear push | DERIVABLE-FROM-SPEC (G) | new: dismissed_at is not a Machine or a count bound, so X does not race it | C |
| I13 | bounds | GET /v1/habits?limit=0 / 201 / -1 / 1e3 / "x" / 2147483648 [ann]; ?include_archived=maybe | R86, R5 | 422 `validation_failed` field `limit` | DERIVABLE-FROM-SPEC (M) | covered: list_pagination; sync_limits_and_cursors (sync form) | Y |
| I14 | injection | Duplicate keys / params: {"email":"fresh@example.com","email":"ann@example.com"} to register/login; ?limit=1&limit=500; PUT /v1/me {"timezone":"UTC","timezone":"Mars/Base"} | R9, R10, R5 | The value that is validated is the value that is used; no user or row for the other value | DERIVABLE-FROM-SPEC (G) | new | H |

## Coverage: what already exists and what a swarm would add

Already covered (25 rows, `covered`), by scenario or invariant name in `spec.py`:

| Rows | Covered by |
|---|---|
| A04, A10 | `duplicate_email_is_refused`, invariant `one_account_per_email` |
| A09 | invariant `one_account_per_email` (+ layer X self-race, probabilistic) |
| B01 | `login_with_bad_credentials_is_refused` |
| B11, C05 | invariant `one_live_refresh_token_per_installation` (+ layer X) |
| C01 | `refresh_rotates_the_token` |
| C02 | `refresh_token_reuse_revokes_the_session` |
| C03 | `refresh_token_unknown_or_expired_is_refused` |
| D01 | the 22 `access__*__as_anon` cells (R21), incl. `access__health__as_anon` |
| D12, F01 | `delete_me_removes_everything`, `access__delete_me__as_ann` |
| E01 | `access__logout__as_anon` |
| E02 | `logout_ends_only_this_installation`, `access__logout__as_ann` |
| F02 | `access__delete_me__as_anon` |
| G01 | `rate_limit_120_per_minute` |
| G05 | `rate_limit_120_per_minute` (a 5-item batch counts once), `batch_item_bounds` |
| H01 | `access__add_device__as_anon`, `access__delete_device__as_anon` |
| H02 | `device_registration` |
| H03 | `device_push_token_takeover` (behaviour; the abuse is not assessed) |
| H11 | `access__delete_device__as_ben`, `unknown_ids_answer_not_found` |
| I01 | the 59 access-matrix cells: 22 anon, 16 ben, 21 ann |
| I04 | `habit_id_conflicts` |
| I11 | `habit_id_conflicts`, `check_in_retry_and_id_conflict` (behaviour; the R28 vs R54 tension is not assessed) |
| I13 | `list_pagination`, `sync_limits_and_cursors` |

Partially covered (16 rows): A02, A06, A08, A11, B08, C13, D02, D03, E05, E06, F03, H09, I02, I06, I08, I10. In each, the endpoint or rule is exercised but the attacking input is not (for example `access_token_expires_after_15_minutes` tests the expiry only on `GET /v1/me` and `POST /v1/habits`; `completion_of_another_habit_is_not_found` is same-user, not cross-user).

New (61 rows): everything else. What a swarm would add, by shape (the groups below mix new and partial rows):

1. **Cross-user combinations the matrix does not build** (B02, B03, E03, H05, H06, I02, I03, I05, I07): the matrix tests one foreign id at a time, with fresh ids and with the caller's own installation. The attacks that mix users' installation ids, existing completion ids or body fields were not written.
2. **Ordering and side-effect attacks on "a refused request writes nothing" (R7)**: B02 (a failed login must not revoke), A08 (a refused register must not leave a prefs row), D02 and E05 (expired token on write routes), C13.
3. **Everything that needs headers or a carried value** (9 `H` + 7 `V` rows, all 16 new or partial): the whole token-forgery family D04..D08, `X-Client-Version` (D17), Authorization on auth routes (C12), `Retry-After` (G08). `spec.py` lists R6, R15, R16 (part), R22 (part) and R29 (part) as `Unchecked` for exactly this reason; an HTTP-level attacker is the only thing that can check them.
4. **Races with no count-bound invariant** (C06, F06, G04, I12): layer X targets count-bound invariants and machines, so the rate limiter, refresh-vs-logout, deletion-vs-write and dismiss races are not raced.
5. **Boundaries and ambiguities the spec leaves open** (C04, C07, D03, D10, D11, G07, B06, C08): the attack is cheap but the oracle is "spec silent", so the first output is a question for the owner, not a finding.

## Counts

| | Count |
|---|---|
| Total attacks (rows; row I01 stands for 59 matrix cells, so cells would raise the total to 160) | **102** |
| DERIVABLE-FROM-SPEC | **90** (62 mechanical (M), 28 generic idea (G)) |
| NEEDS-APP-KNOWLEDGE | **12** (A03, A05, A07, B07, D08, D09, D13, D14, G09, H04, H08, I08) |
| Already covered (full) | **25** (all DERIVABLE (M)) |
| Partially covered | **16** (15 derivable, 1 needs app knowledge: I08) |
| New | **61** (50 derivable: 24 M + 26 G; 11 need app knowledge) |
| New or partial, expressible in the spec DSL today (`Y`) | 50 of 77 |
| New or partial, needing headers or a raw body (`H`) | 9 |
| New or partial, needing a carried value (`V`) | 7 |
| New or partial, concurrency (`C`) / timing (`T`) / config (`F`) | 5 / 3 / 3 |

By class (total, covered, partial, new): authz 29 (8, 4, 17); bounds 16 (2, 2, 12); state/lifecycle 14 (3, 2, 9); enumeration/timing 9 (4, 3, 2); rate-limit 9 (2, 0, 7); race 8 (3, 1, 4); replay 8 (3, 3, 2); injection 6 (0, 1, 5); mass-assignment 3 (0, 0, 3).

## Judgement

The spec alone gives most of an attack plan for this feature: 90 of 102 attacks (88%) have their expected defence written in the spec's own words, and 62 of those follow mechanically from a rule's limits, states and ownership (send the boundary, the other user, the revoked token). The R-numbered rules, the `access_matrix` and the `Unchecked` list work as a ready-made target list; every row has a rule id or an explicit "none stated". The existing scenarios cover 25 of the 102 rows, and all of those are the "stated behaviour" cases; the 61 new rows are what an attacker adds: cross-user mixes of ids the matrix never builds, refused requests that must write nothing, header and token forgery, races without a count bound, and boundaries. The classes the spec does not give are exactly those that need code, schema or traffic knowledge: collation and padding effects on unique columns (A05, H04), column and index limits (A03, H08), framework routing and middleware order (D14), the signing key and id assignment (D08, D09, D13), timing (B07, I10), opaque cursor format (I08) and deployment topology (G09). Two things follow for the module: its oracle problem is mostly solved by the spec (every row names the rule it would break), and its transport problem is not, since 16 of the 77 new-or-partial rows cannot be sent by the present scenario DSL (headers, carried tokens). A smaller point: 19 rows have an expected defence that is "spec silent" or "not stated" (A01, A03, A07, B06, B10, C04, C07, C08, C12, D03, D05, D08, D09, D10, D11, D17, G07, H07, H10), and an attacker agent would report these as questions, not holes.

## Spec issues surfaced while writing the list

These are not attacks but each one makes an attack's oracle undefined; I list them so the owner can decide.

1. **R28 vs R54/R79.** R28 says another user's habit, check-in, nudge or device answers 404 "on every route, exactly like an id that does not exist"; R54 and R79 explicitly answer 409 `id_conflict` for another user's id, which is distinguishable from a fresh id's 201 (I11).
2. **No stated effect on access tokens of logout, session revocation (R20) or reuse**: a stateless JWT stays valid up to 15 minutes (D10, D11).
3. **Rejected-and-revoked refresh token**: R19 (revoked) and R20 (rotated) both apply; precedence not stated (C07).
4. **Unauthenticated routes are unlimited** only in an assumption (M16, line 66), not in an R rule (B09, G06).
5. **No per-user cap** on installations (refresh tokens) or devices (B10, H10).
6. **Unknown request fields** (A01, H05, I05, B13) and **malformed refresh bodies** (R5 vs R19, C08) have no stated answer.
7. **Order of 426 / 401 / 422** when several apply is not stated (D17).
8. A session cannot be ended once its access token has lapsed: logout needs a valid token (R23, R22), so E05 is by design.

## What could not be determined

- Whether the Streak build on `build/streak` or `main` implements any of the new rows: I did not run the app or the checker, so no row is a finding, only an attack with an expected defence.
- Whether layer X is enabled for Streak's gate and how many tries it uses; the "covered via invariant + layer X" rows (A09, B11, C05, H09) rest on the docstring of `lattice/spec/race.py` and the invariants in `spec.py` only.
- How the private-column check (R12, A11) is applied: `spec.trace.json` links R12 to `profile:User.hashed_password` and `profile:RefreshToken.token_hash`, but I did not read the checker, so whether it scans every response body (error bodies, `current` of a 409, sync `data`) is not verified. A11 is marked partial for that reason.
- The DERIVABLE (M)/(G) split is a judgement; a different reader would move some rows (for example D15, F06, H12). The 90 / 12 split between derivable and app-knowledge is less sensitive to it than the M/G split.
- "Existing coverage" for I08 is partial on my reading: `sync_limits_and_cursors` sends the cursor `"bogus"`; I did not open `list_pagination` (lines 1218-1232) beyond its trace link (R86, R88) and name.
- Things a standard auth checklist would test but the spec has no endpoint for, so no attack could be written: password change or reset, email verification, "log out all devices", account lockout, token introspection, CORS and security headers, TLS. Their absence is itself not a spec rule; whether it is a gap is the owner's call.
- Row counts depend on how I grouped near-identical requests (for example the matrix is one row, I01, for 59 cells). Use the table, not the totals, to compare with a later swarm run.
