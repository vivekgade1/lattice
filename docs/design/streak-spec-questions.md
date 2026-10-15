# Questions for the owner of Streak: contradictions in the spec

Source: `docs/results/2026-10-12-s0-streak-auth-attack-list.md` (S0), section "Spec issues surfaced while writing the list" (heading at line 232, items at lines 236-243), checked against Streak's own files at commit `a0db033` (`REQUIREMENTS.md`, `spec.py`, `spec.trace.json`). Rule ids are the ids in `spec.trace.json`; `REQUIREMENTS.md` itself carries no R-numbers. All quoted text, line numbers and names below were checked by a script (appendix).

## How many are there

`docs/design/next-phases-plan.md` summarises S0 as "five spec inconsistencies". The S0 document does not give five: its list has **eight** numbered "spec issues", and when each is read against the spec only **four** are inconsistencies in the strict sense (two statements in the spec that both apply to the same request and give different answers). The other four are silences (the spec states nothing), listed after the summary table so the owner can decide on them; I did not turn them into questions and I did not invent a fifth inconsistency. Four questions follow, `SQ-1` to `SQ-4`.

The question format below is the one Studio uses (`{id, text, default}` in `lattice/studio/API.md`, plus the `rules` list that `spec.trace.json` carries for its own `Q2`..`Q20`). The ids `SQ-n` do not collide with the `Q` ids already in Streak's trace.

---

## SQ-1: another user's id on create: 404 (R28) or 409 (R54, R79)?

```json
{
  "id": "SQ-1",
  "text": "R28 says a habit, check-in, nudge or device of another user answers 404 not_found 'on every route, exactly like an id that does not exist'. R54 says POST /v1/habits with an id that 'belongs to another user' answers 409 id_conflict, and R79 answers 409 id_conflict for a check-in id that exists 'under another habit' or with different content. A caller who posts another user's id therefore gets 409, while a fresh id gets 201, so the answer is not 'exactly like an id that does not exist'. Which wins on create-with-an-existing-id?",
  "rules": ["R28", "R54", "R79"],
  "default": "409 id_conflict stays for create with an id that exists (R54, R79); R28 is narrowed to say so. A create cannot answer 404 (a fresh id answers 201), and the ids are unguessable client UUIDs, so the 409 reveals nothing an attacker can use. All other routes keep 404."
}
```

**The three rules (verbatim)**

> R28 (REQUIREMENTS.md:103): "A habit, check-in, nudge or device of another user answers 404 with code `not_found` on every route, exactly like an id that does not exist, and writes nothing."

> R54 (REQUIREMENTS.md:144): "`POST /v1/habits` with an id that exists with different content, belongs to another user, or belongs to a deleted habit answers 409 with code `id_conflict` and writes nothing."

> R79 (REQUIREMENTS.md:172): "A check-in with the id of an existing check-in of the same habit, date and note answers 200 with the existing one and writes nothing; the same id with different content, under another habit, or of a deleted check-in answers 409 `id_conflict`."

**Which scenarios disagree**

| Side | Scenario (spec.py line) | What it expects |
|---|---|---|
| R28 | `unknown_ids_answer_not_found` (`spec.py:675`) | an id that does not exist: `POST` of a check-in under habit 999 at `spec.py:681` answers `expect_status=404` with `err("not_found")` (`spec.py:682`) |
| R28 | `ACCESS` access matrix (`spec.py:273`), caller `ben` | `"add_completion": 404` at `spec.py:316` (ben posting a fresh check-in id under ann's habit), also `"get_habit": 404` at `spec.py:315` and `"delete_device": 404` at `spec.py:321` |
| R54 | `habit_id_conflicts` (`spec.py:812`) | ann posts ben's habit id: `expect_status=409, expect_body=err("id_conflict")` at `spec.py:819`, row still ben's (`spec.py:820`) |
| R79 | `check_in_retry_and_id_conflict` (`spec.py:1102`) | the same id under habit 101 answers `expect_status=409` at `spec.py:1116` with `err("id_conflict")` |

**The three requests and what each side expects**

| Request | R28 reading | R54 / R79 reading | Spec today |
|---|---|---|---|
| (a) ben `POST /v1/habits` with ann's habit id | 404 `not_found`, like an unknown id | 409 `id_conflict` | R54 is explicit and `habit_id_conflicts` pins it (ann posting ben's id, `spec.py:818-821`). R28 is contradicted |
| (b) ben `POST /v1/habits/{ben_habit}/completions` with the id of one of ann's check-ins | 404 `not_found` | 409 `id_conflict` ("the same id ... under another habit") | no scenario; the R79 scenario uses the same user's second habit (`spec.py:1115-1117`) |
| (c) ben `POST /v1/habits/{ann_habit}/completions` with ann's existing check-in id and identical content | 404 `not_found` (the habit is another user's) | 200 with the existing check-in ("existing check-in of the same habit, date and note") | no scenario; S0 row I03 expects 404 |

(a) and (b) are the existence oracle S0 lists as I11: the answer differs from a fresh id's 201. (c) would be worse than an oracle, because 200 would hand back another user's check-in.

**Default and reason.** Keep 409 `id_conflict` for (a) and (b), with the standard error body of R4 and nothing of the other user's row; answer 404 for (c) because the habit is resolved and its owner checked first. Reason: a create with a client-chosen primary key cannot answer 404 without making fresh ids 404 too, and v4 UUIDs cannot be guessed, so the 409 is the least-bad oracle.

**What changes in the spec if the default is accepted**

- `REQUIREMENTS.md:103` (R28) gains an exception: "..., except that creating a habit or check-in with an id that already exists answers 409 `id_conflict` as R54 and R79 state, and never returns or names the other user's row."
- R54 and R79 are unchanged in meaning. R79 gets the clause "(including a check-in id that belongs to another user)" so (b) is stated.
- `spec.py`: two new steps or one new scenario next to `check_in_retry_and_id_conflict` (`spec.py:1102`) for (b) (409, ann's row untouched) and (c) (404, ann's row untouched), with trace links R28, R79.
- S0 row I11 then has a stated defence (none beyond the unguessable id), instead of "no defence stated".

---

## SQ-2: a refresh token that is both rotated and revoked: `invalid_refresh_token` or `refresh_token_reused`?

```json
{
  "id": "SQ-2",
  "text": "R19 says a refresh token that is 'revoked' answers 401 invalid_refresh_token; R20 says a refresh token that 'was already rotated' answers 401 refresh_token_reused and revokes every token of that user and installation. A token that is both (rotated, then revoked by R20 itself, by a login under R17, or by a logout under R23) matches both rules. Which answer does replaying it get, and does it revoke anything again?",
  "rules": ["R19", "R20", "R17", "R23"],
  "default": "invalid_refresh_token wins whenever revoked_at is set (check R19 first), and nothing else is revoked; refresh_token_reused is answered only for a token that is rotated and not revoked. A revoked session is already ended, so the answer is stable on every replay, and a stale rotated token cannot kill a newer login's session."
}
```

**The rules (verbatim)**

> R19 (REQUIREMENTS.md:94): "A refresh token that is unknown, expired or revoked answers 401 with code `invalid_refresh_token`."

> R20 (REQUIREMENTS.md:95): "A refresh token that was already rotated (reuse) answers 401 with code `refresh_token_reused` and revokes every refresh token of that user and installation, so the session must log in again."

> R17 (REQUIREMENTS.md:92): "A user has at most one live refresh token per installation: a login revokes (sets `revoked_at` on) every unrevoked refresh token of the same user and installation before writing the new one."

**Which scenarios disagree.** None disagrees, because none builds the state; that is the hole. `refresh_token_reuse_revokes_the_session` (`spec.py:528`) replays the rotated token once (`spec.py:533`: `err("refresh_token_reused")`), then replays the other, live-but-now-revoked token (`spec.py:537-538`: `err("invalid_refresh_token")`). `refresh_token_unknown_or_expired_is_refused` (`spec.py:541`) seeds a revoked, not-rotated token (`spec.py:544-545`) and expects `err("invalid_refresh_token")` at `spec.py:554`. No scenario replays the rotated token a second time, after R20 has revoked it, nor a rotated token after a login (R17) or logout (R23). The trace links R19 to the two scenarios above and R20 to the first only.

**What each side expects**

| Request | R19 side | R20 side |
|---|---|---|
| replay the rotated token again after the first `refresh_token_reused` | 401 `invalid_refresh_token` (it is revoked now) | 401 `refresh_token_reused`, revoke everything again |
| replay a pre-login rotated token after a new login (if a rotated token with `revoked_at` null counts as "unrevoked" in R17, the login sets `revoked_at` on it; if it does not, it stays rotated and not revoked, and the question is the right-hand cell) | 401 `invalid_refresh_token` | 401 `refresh_token_reused`, and "revokes every refresh token of that user and installation", which kills the new login's tokens |

**Default and reason.** See the JSON block: revoked takes precedence. Stated as a rule it removes a way for a stale client to end a fresh session, and makes the response to repeated replays idempotent.

**What changes in the spec if the default is accepted**

- `REQUIREMENTS.md:95` (R20) becomes "A refresh token that is not revoked and was already rotated (reuse) answers ...", and `REQUIREMENTS.md:94` (R19) gains "; a token that is both revoked and rotated answers `invalid_refresh_token` and revokes nothing further".
- `spec.py`: one more step in `refresh_token_reuse_revokes_the_session` (`spec.py:528`) replaying `rt-ann-rotated` again, expecting 401 `invalid_refresh_token` and an unchanged `refresh_tokens` count; trace link R19 and R20.
- S0 row C07 gets its oracle.

---

## SQ-3: a malformed refresh body: 422 `validation_failed` (R5) or 401 `invalid_refresh_token` (R19)?

```json
{
  "id": "SQ-3",
  "text": "R5 says every request body value that fails validation answers 422 validation_failed with the offending field. R19 says a refresh token that is 'unknown' answers 401 invalid_refresh_token. POST /v1/auth/refresh with a body that has no refresh_token, a null, an empty string or a non-string is both 'fails validation' and 'unknown'. Which one is answered?",
  "rules": ["R5", "R18", "R19"],
  "default": "422 validation_failed with field refresh_token when refresh_token is missing, null, not a string or an empty string; 401 invalid_refresh_token only for a non-empty string that matches no live token. R5 is stated for every request body, and register (R9) already answers invalid fields with 422; R19 is about a well-formed token."
}
```

**The rules (verbatim)**

> R5 (REQUIREMENTS.md:77): "Every request body or query value that fails validation answers 422 with code `validation_failed` and the offending `field`, and writes nothing."

> R18 (REQUIREMENTS.md:93): "`POST /v1/auth/refresh` takes `{refresh_token}`; for a live token (not rotated, not revoked, not expired) it sets that token's `rotated_at`, writes a new refresh token for the same user and installation, and answers 200 with a new access token and the new refresh token in the login's shape."

> R19 (REQUIREMENTS.md:94): "A refresh token that is unknown, expired or revoked answers 401 with code `invalid_refresh_token`."

**Which scenarios disagree.** No scenario sends a refresh request without a well-formed string: all six `POST /v1/auth/refresh` steps send a string (`spec.py:520`, `532`, `537`, `546`, `549`, `553`), the unknown one being `"rt-nobody"` at `spec.py:546-547` which expects 401 `err("invalid_refresh_token")`. The trace links R5 only to `habit_name_bounds`, `patch_validation_changes_nothing` and `me_update_and_validation`, none of which touches the refresh route. R5 would expect `err("validation_failed", "refresh_token")` in the form `spec.py` uses at `spec.py:446`; R19 would expect `err("invalid_refresh_token")` as at `spec.py:547`.

**What each side expects**

| Body | R5 reading | R19 reading |
|---|---|---|
| `{}` or `{"refresh_token": null}` | 422 `validation_failed`, field `refresh_token` | 401 `invalid_refresh_token` (no token that matches) |
| `{"refresh_token": ""}` | 422 | 401 |
| `{"refresh_token": 12345}` | 422 | 401 |
| `{"refresh_token": "rt-nobody"}` | not a validation failure | 401 `invalid_refresh_token` |

**What changes in the spec if the default is accepted**

- `REQUIREMENTS.md:93` (R18) gains: "`refresh_token` must be a non-empty string; otherwise the request answers 422 `validation_failed` with `field` `refresh_token` and writes nothing."
- R19 is unchanged but now clearly means a well-formed string.
- `spec.py`: a scenario `refresh_body_must_be_a_string` posting `{}`, `{"refresh_token": null}`, `{"refresh_token": ""}`, `{"refresh_token": 12345}` expecting 422 and no change to `refresh_tokens`; trace link R5, R18.
- S0 row C08 gets its oracle.

---

## SQ-4: which answer when 426 (R6), 401 (R21, R22), 429 (R29) and 422 (R5) all apply?

```json
{
  "id": "SQ-4",
  "text": "R6 says a request with an X-Client-Version that is too low or malformed answers 426 upgrade_required and writes nothing. R21 says a request without a bearer token answers 401 not_authenticated, R22 says an expired token answers 401 token_expired, R29 says the 121st request in a minute answers 429 rate_limited, and R5 says an invalid body answers 422 validation_failed. Each is worded without a condition, so one request can satisfy several. In what order are they checked?",
  "rules": ["R5", "R6", "R21", "R22", "R29"],
  "default": "426 first, then 401, then 429, then 422, then the resource answers (404, 409). A version gate comes before anything the old client may not understand; 429 comes after 401 because R29 counts 'a signed-in user' only; validation comes last because it needs the caller and the rate budget. The spec already orders its date rules this way (R76)."
}
```

**The rules (verbatim)**

> R6 (REQUIREMENTS.md:78): "A request may send `X-Client-Version: MAJOR.MINOR.PATCH`; when it is lower than the configured minimum (`HABIT_MIN_CLIENT_VERSION`) or malformed, the request answers 426 with code `upgrade_required` and writes nothing; a request without the header is served."

> R21 (REQUIREMENTS.md:96): "Every other `/v1` route except register, login and refresh needs `Authorization: Bearer <access token>`; without one it answers 401 with code `not_authenticated`."

> R22 (REQUIREMENTS.md:97): "An access token that is past its expiry answers 401 with code `token_expired`; a malformed or wrongly signed one, or one whose user no longer exists, answers 401 with code `invalid_token`."

> R29 (REQUIREMENTS.md:107): "A signed-in user may make 120 requests per clock minute (fixed windows starting at second 0); the 121st and later requests in that minute answer 429 with code `rate_limited` and a `Retry-After` header giving the seconds until the window ends, and write nothing."

**Which scenarios disagree.** None can: R6 is declared unchecked because a step cannot send headers (`spec.py:1798-1799`: `Unchecked("R6: X-Client-Version and the 426 upgrade_required answer -- a step cannot "`), the trace has no scenario link for R6, and no scenario in `spec.py` mentions `X-Client-Version`. Existing scenarios pin each of the other answers alone, never in combination: `access_token_expires_after_15_minutes` (`spec.py:558`) expects `err("token_expired")` for a `POST /v1/habits` at `spec.py:564`, and `rate_limit_120_per_minute` (`spec.py:622`) expects `err("rate_limited")` at `spec.py:631`.

**What each side expects for a request that has two problems**

| Request | One reading | Other reading |
|---|---|---|
| no `Authorization` and `X-Client-Version: 0.0.1` (below the minimum) on `GET /v1/me` | 426 `upgrade_required` (R6) | 401 `not_authenticated` (R21) |
| valid token, 121st request this minute, `X-Client-Version` too low | 426 (R6) | 429 `rate_limited` (R29) |
| valid token, malformed body, `X-Client-Version` too low, on `POST /v1/habits` | 426 (R6) | 422 `validation_failed` (R5) |
| expired token and a malformed body | 401 `token_expired` (R22) | 422 (R5) |

**What changes in the spec if the default is accepted**

- A new bullet in "Errors and versioning" after `REQUIREMENTS.md:79`: "When more than one error applies, the order is 426 `upgrade_required`, 401 (R21, R22), 429 `rate_limited`, 422 `validation_failed`, then the route's own answers."
- R6 stays declared unchecked until the scenario DSL can send headers; the ordering of 401 before 429 before 422 can be pinned today by new steps (an expired token with a malformed body; a 121st request with a malformed body expects 429).
- S0 row D17 gets its oracle.

---

## Summary

| Id | Question in one line | Rules | Scenario lines that disagree or are missing | Default | Spec change |
|---|---|---|---|---|---|
| SQ-1 | Create with another user's id: 404 or 409? | R28 vs R54, R79 | `unknown_ids_answer_not_found` `spec.py:675` and ACCESS `spec.py:273` (404) vs `habit_id_conflicts` `spec.py:812` and `check_in_retry_and_id_conflict` `spec.py:1102` (409) | 409 on create (R54, R79); 404 on every other route; R28 narrowed; foreign habit under check-in create is 404 | edit `REQUIREMENTS.md:103`, R79 wording; scenario steps for (b) and (c) |
| SQ-2 | Rotated and revoked token: which code? | R19 vs R20 (via R17, R23) | no scenario replays a rotated and revoked token; `spec.py:528` and `spec.py:541` | `invalid_refresh_token` wins when revoked; nothing re-revoked | edit `REQUIREMENTS.md:94-95`; one step in `spec.py:528` |
| SQ-3 | Malformed refresh body: 422 or 401? | R5 vs R19 (via R18) | no scenario sends a non-string; all six steps at `spec.py:520-553` send strings | 422 for missing, null, empty or non-string; 401 for unknown non-empty string | one sentence in R18 (`REQUIREMENTS.md:93`); new scenario |
| SQ-4 | Order of 426, 401, 429, 422 | R6 vs R21, R22, R29, R5 | R6 is `Unchecked` at `spec.py:1798`; no scenario sets a header | 426, 401, 429, 422, then resource answers | one bullet after `REQUIREMENTS.md:79`; steps for 401 vs 429 vs 422 |

### S0's other four items: silences, not inconsistencies (no question written)

| S0 item (line in S0) | Why it is not a two-rules-disagree case | Spec lines |
|---|---|---|
| 2: access tokens after logout, session revocation (R20) or reuse (S0:237) | R22 and R23 never say an access token is revoked; nothing contradicts, nothing is stated. R20's phrase "so the session must log in again" is about the refresh token | `REQUIREMENTS.md:95`, `REQUIREMENTS.md:97-98` |
| 4: unauthenticated routes unlimited only in assumption M16 (S0:239) | R29 limits "a signed-in user" and says nothing about anonymous callers; M16 says "unauthenticated routes are not limited in v1"; the two are consistent, M16 is just not a numbered rule | `REQUIREMENTS.md:66-67`, `REQUIREMENTS.md:107` |
| 5: no per-user cap on installations or devices (S0:240) | no rule states a cap or says there is none | R17, R31 |
| 6 (first half): unknown request fields (S0:241) | no rule states an answer. (Its second half, malformed refresh bodies, is SQ-3) | R31, R60 |
| 8: a session cannot be ended once the access token has lapsed (S0:243) | follows from R22 and R23 ("signed in"); S0 itself calls it by design | R22, R23 |

(Item 7, order of 426 / 401 / 422, is SQ-4; item 3 is SQ-2; item 1 is SQ-1. Streak's trace already carries `Q12`, "R7 says any 4xx writes nothing, but R20 makes a reused refresh token's 401 revoke the session", and `R7` at `REQUIREMENTS.md:79` now states that exception, so it is not asked again.)

---

## Appendix: verification output

The script is kept at `/private/tmp/claude-501/-Users-vivekgade-Documents-lattice/ee5b7f26-0758-4aaf-845d-fa75c50a8e80/scratchpad/streakq/verify.py`. It reads `spec.py`, `spec.trace.json` and `REQUIREMENTS.md` straight from `git -C /Users/vivekgade/Documents/streak show a0db033:<path>`, checks every rule id, scenario name, `spec.py:N` and `REQUIREMENTS.md:N` citation in this document, every verbatim rule quote, and every quoted code fragment against the line it cites.

```text
rules cited: 19 (all in trace: True)
checks passed: 251; failed: 0
RESULT: PASS
```

The script was first run with two failures, both defects of this document, fixed before the run above: the heading of S0's list is at line 232 (the document said 234), and the check for R6 looked for it in the wrong part of the trace. No citation was dropped.
