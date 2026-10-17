# Streak: requirements

Streak is a mobile habit tracker (iOS and Android clients) where a user creates habits with a target count per week, month or year, checks in (also offline), sees whether the target is still reachable at the current rate, and gets nudges in the app and as push notifications. This document specifies the backend API: Go with Gin + GORM on MySQL.

## Assumptions made on the owner's behalf

The owner's idea did not state these; each is restated as a rule below. Review them first.

```text
GENERAL
G1  Periods are calendar periods in the USER'S timezone: week = Monday-Sunday,
    month = 1st-last day, year = Jan 1-Dec 31. "Today" is the user's local date.
G2  A habit's target repeats every period until the habit ends (end_date) or is archived.
G3  At most one check-in per habit per local date (a second answers 409), so a day adds 1.
G4  A habit that starts or ends inside a period gets a pro-rated target for that
    period, rounded up: ceil(N * active_days / period_days).
G5  Editing target_count takes effect for the current period at once; past periods keep
    their target. This needs an extra table, habit_targets, beyond the owner's list.
G6  The period (week/month/year) cannot be changed after creation (422).
G7  start_date defaults to the user's local today and can change only before the habit
    has started (409).
G8  "on_track" = the projection at the current rate reaches the target; the first day
    of a habit's active window counts as on_track even with nothing done.
G9  One nudge per habit per local day. Check-in writes rewrite the day's nudge text; the
    daily pass creates or refreshes it; a dismissed nudge stays dismissed that day.
G10 Limits: name 1-100 chars, unique per user (case-insensitively) among non-deleted
    habits (409); description <= 500; note <= 280; at most 50 active (non-archived,
    non-deleted) habits per user (409); history limit 1-100 (default 12).
G11 No admins or roles: every user only sees and changes their own data.

MOBILE
M1  All routes under /v1 (except GET /health). Errors: {"error": {"code", "message",
    "field"?}} with the stable codes listed under "Errors".
M2  X-Client-Version "MAJOR.MINOR.PATCH" is optional; below the configured minimum, or
    malformed, answers 426 upgrade_required; absent is allowed.
M3  Access token: JWT, 15 minutes. Refresh token: opaque, 30 days, stored as a SHA-256
    hash, one live token per (user, installation); every refresh rotates it; reusing a
    rotated token revokes that installation's whole session.
M4  The client sends an installation_id (a UUID it generates once per app install) at
    login; it names the device session. Logout revokes that session and deletes the
    installation's device row.
M5  Devices are registered per push_token (idempotent): a known push_token is updated
    (and moves to the caller if another user had it).
M6  The user's timezone is set at registration (default "UTC") and changed only with
    PUT /v1/me; a device's own timezone is stored for information only.
M7  Notification prefs (per user): push on, quiet hours 22:00-08:00 local, nudge time
    20:00 local. A quiet window may cross midnight; start == end means no quiet hours.
M8  A timezone change does not move past check-ins: they keep their local date.
M9  Check-ins: local date chosen by the client; not after local today, not before
    start_date, not after end_date, not older than 60 days.
M10 Habits and check-ins have client-generated UUIDs; nudges get server UUIDs.
    Create with an existing id: identical content -> 200 with the record; different
    content, someone else's id, or a deleted record's id -> 409 id_conflict.
M11 PATCH on a habit needs "version"; the server increments it on every change; a
    stale version -> 409 version_conflict with the current habit. DELETE needs no
    version (delete wins).
M12 Deletes of habits and check-ins are soft (deleted_at, tombstone); deleting a habit
    tombstones its check-ins and nudges too. Account deletion is a hard delete.
M13 Sync order: every change to a habit, check-in or nudge gets the next value of the
    user's change counter (change_seq); the cursor is an opaque encoding of the last
    change_seq seen. Full sync (no cursor) also returns tombstones.
M14 Only "behind" and "unreachable" nudges are pushed; "on_track" and "achieved" are
    in-app only. Pushes are sent only by the daily pass, at the user's nudge time.
M15 Push limits: at most 1 push per habit per day (one nudge per habit per day) and
    at most 3 pushes per user per local day; the rest are suppressed (still in-app).
M16 Rate limit: 120 requests per minute per signed-in user, fixed windows of a clock
    minute; unauthenticated routes are not limited in v1.
M17 GET /v1/home returns every active habit with its progress plus today's active
    nudges in one response (not paginated: at most 50 active habits).
M18 Lists (habits, check-ins, nudges) use cursor pagination: limit default 50, max 200.
```

## Errors and versioning

- Every route is under `/v1`, except `GET /health`, which answers 200 without a token.
- Every error answers `{"error": {"code": <code>, "message": <text>}}`, plus `"field": <request field name>` when one request field caused it; `code` is one of the codes named in this document and never changes meaning.
- Every request body or query value that fails validation answers 422 with code `validation_failed` and the offending `field`, and writes nothing.
- A request may send `X-Client-Version: MAJOR.MINOR.PATCH`; when it is lower than the configured minimum (`HABIT_MIN_CLIENT_VERSION`) or malformed, the request answers 426 with code `upgrade_required` and writes nothing; a request without the header is served.
- A refused or invalid request (any 4xx) writes nothing, with one exception: a 401 `refresh_token_reused` revokes every refresh token of that user and installation.

## Accounts, tokens and sessions

- `POST /v1/auth/register` takes `{email, password, timezone?}` and answers 201 `{id, email, timezone}`; the email is stored lower-cased and `timezone` defaults to `"UTC"`.
- A password is 8 to 72 bytes, an email must be valid, and a timezone must be an IANA zone name (e.g. `Europe/Berlin`); anything else is answered with 422 `validation_failed` and creates no user.
- An email already registered, compared case-insensitively, answers 409 with code `email_taken` and creates no user.
- Registering a user also creates their `notification_prefs` row with `push_enabled` true, `quiet_start` `"22:00"`, `quiet_end` `"08:00"` and `nudge_time` `"20:00"`.
- The password is stored only as a bcrypt hash in `users.hashed_password`; no response ever contains the password, its hash, or a refresh token's hash.
- `POST /v1/auth/login` takes `{email, password, installation_id}` (`installation_id` a UUID the app generates once per install) and answers 200 `{access_token, refresh_token, token_type: "bearer", expires_in: 900}`.
- A wrong password or an unknown email at login answers 401 with code `invalid_credentials`.
- The access token is a JWT signed HS256 with `HABIT_SECRET_KEY`, with `sub` = the user id and `sid` = the installation_id, expiring 15 minutes after it was issued by the app's clock.
- The refresh token is an opaque random string of at least 32 bytes of entropy, valid for 30 days; the app stores only its SHA-256 hex digest in `refresh_tokens.token_hash`.
- A user has at most one live refresh token per installation: a login revokes (sets `revoked_at` on) every unrevoked refresh token of the same user and installation before writing the new one.
- `POST /v1/auth/refresh` takes `{refresh_token}`; for a live token (not rotated, not revoked, not expired) it sets that token's `rotated_at`, writes a new refresh token for the same user and installation, and answers 200 with a new access token and the new refresh token in the login's shape.
- A refresh token that is unknown, expired or revoked answers 401 with code `invalid_refresh_token`.
- A refresh token that was already rotated (reuse) answers 401 with code `refresh_token_reused` and revokes every refresh token of that user and installation, so the session must log in again.
- Every other `/v1` route except register, login and refresh needs `Authorization: Bearer <access token>`; without one it answers 401 with code `not_authenticated`.
- An access token that is past its expiry answers 401 with code `token_expired`; a malformed or wrongly signed one, or one whose user no longer exists, answers 401 with code `invalid_token`.
- `POST /v1/auth/logout` (signed in) revokes every refresh token of the caller's installation (the token's `sid`), deletes the caller's device row with that installation_id if any, and answers 204.
- `GET /v1/me` answers `{id, email, timezone, notification_prefs: {push_enabled, quiet_start, quiet_end, nudge_time}}`.
- `PUT /v1/me` takes any subset of `{timezone, push_enabled, quiet_start, quiet_end, nudge_time}` and answers 200 with the new `GET /v1/me` body; times are `"HH:MM"` 24-hour local times and an invalid value answers 422 `validation_failed` with nothing changed.
- `DELETE /v1/me` answers 204 and permanently deletes the user with their notification prefs, devices, refresh tokens, habits, habit targets, check-ins and nudges (no tombstones); afterwards the user's old access token answers 401 `invalid_token` and their login answers 401 `invalid_credentials`.
- There are no roles or admins: a user sees and changes only their own habits, check-ins, nudges and devices.
- A habit, check-in, nudge or device of another user answers 404 with code `not_found` on every route, exactly like an id that does not exist, and writes nothing.

## Rate limiting

- A signed-in user may make 120 requests per clock minute (fixed windows starting at second 0); the 121st and later requests in that minute answer 429 with code `rate_limited` and a `Retry-After` header giving the seconds until the window ends, and write nothing.
- A sync or batch request counts as one request whatever it contains.

## Devices and notification preferences

- `POST /v1/devices` takes `{installation_id, platform, push_token, timezone, app_version}` and answers 201 with the device `{id, installation_id, platform, push_token, timezone, app_version}`; `platform` is `ios` or `android`, `push_token` 1 to 4096 characters, `timezone` an IANA zone, and anything else answers 422 `validation_failed`.
- `POST /v1/devices` with a `push_token` that is already registered updates that device row's other fields, makes it the caller's, and answers 200 with it; there is never more than one row per `push_token`.
- `DELETE /v1/devices/{id}` answers 204 and deletes the caller's device row.
- A device's `timezone` is stored for information only; it never changes the user's timezone.

## Data model (table and column names are fixed)

- `users`: `id` (integer), `email` (unique), `hashed_password`, `timezone`, `change_seq` (integer, the last change counter value given out, starting at 0), `created_at`.
- `notification_prefs`: `user_id` (unique), `push_enabled`, `quiet_start`, `quiet_end`, `nudge_time` (all three `"HH:MM"`), `last_pass_date` (a date, nullable: the local date of the user's last daily pass).
- `devices`: `id` (integer), `user_id`, `installation_id`, `platform` (`ios` | `android`), `push_token` (unique), `timezone`, `app_version`, `created_at`, `updated_at`.
- `refresh_tokens`: `id` (integer), `user_id`, `installation_id`, `token_hash` (unique), `expires_at`, `rotated_at` (nullable), `revoked_at` (nullable), `created_at`.
- `habits`: `id` (a client UUID), `user_id`, `name`, `description` (nullable), `target_count`, `period` (`week` | `month` | `year`), `start_date`, `end_date` (nullable), `archived` (default false), `version` (starts at 1), `change_seq`, `created_at`, `updated_at`, `deleted_at` (nullable).
- `habit_targets`: `id` (integer), `habit_id`, `target_count`, `effective_from` (the first day of the period from which this target applies); one row per (`habit_id`, `effective_from`).
- `completions`: `id` (a client UUID), `habit_id`, `date` (the user's local date), `note` (nullable), `change_seq`, `created_at`, `updated_at`, `deleted_at` (nullable); at most one non-deleted row per (`habit_id`, `date`).
- `nudges`: `id` (a server UUID), `habit_id`, `for_date` (a local date), `kind` (`on_track` | `behind` | `unreachable` | `achieved`), `message`, `delivery` (`pending` | `sent` | `failed` | `suppressed`), `delivery_reason` (nullable: `quiet_hours` | `push_disabled` | `no_device` | `daily_limit` | `in_app_only` | `after_daily_pass` | `dismissed`), `change_seq`, `created_at`, `updated_at`, `dismissed_at` (nullable), `deleted_at` (nullable); one row per (`habit_id`, `for_date`).
- Dates travel as `YYYY-MM-DD` strings and instants as ISO-8601 UTC strings ending in `Z`; every stored instant is UTC; a malformed date or id in a request answers 422 `validation_failed`.

## Time, timezones and periods

- The app reads the current instant only from its clock: the real time in production, and in test mode the instant the harness sets (see CONTEXT.md).
- A user's "today" (written `t` below) is the calendar date of the current instant in the user's `timezone`; every day and period boundary is computed in that timezone.
- A weekly period runs Monday to Sunday, a monthly period from the 1st to the last day of the calendar month, a yearly period from January 1 to December 31, all as local dates; `L` is the number of days in the period (7; 28 to 31; 365 or 366).
- Changing the user's timezone changes only what today is from then on: check-ins keep the local `date` they were made with, and no period or past check-in is recomputed.
- A habit's target applies again in every period, from the period containing `start_date` until the period containing `end_date` (or for ever when `end_date` is null); archiving does not change the periods, it only stops nudges and check-ins.

## Habits

- `POST /v1/habits` takes `{id, name, description?, target_count, period, start_date?, end_date?}` (`id` a UUID the client generated) and answers 201 with the habit `{id, name, description, target_count, period, start_date, end_date, archived, version, created_at, updated_at}`; `archived` is false and `version` is 1.
- `start_date` defaults to the user's today; it may be in the past or the future.
- `name` is 1 to 100 characters and not only whitespace, `description` at most 500 characters, `period` one of `week`, `month`, `year`, and `target_count` an integer from 1 to 7 for `week`, 31 for `month` and 366 for `year`; anything else answers 422 `validation_failed` with that `field`.
- `end_date`, when given, must be on or after `start_date`; an earlier one answers 422 `validation_failed` with field `end_date`.
- `POST /v1/habits` with the id of an existing habit of the caller whose name, description, target_count, period, start_date and end_date all equal the request's answers 200 with the existing habit and writes nothing (a retried create).
- `POST /v1/habits` with an id that exists with different content, belongs to another user, or belongs to a deleted habit answers 409 with code `id_conflict` and writes nothing.
- A habit name is unique among the user's non-deleted habits, archived or not, compared case-insensitively (`Run` and `run` conflict); a duplicate on create or on rename answers 409 with code `name_taken`. Another user may use the same name.
- A user has at most 50 active (non-archived, non-deleted) habits; creating one more answers 409 with code `habit_limit`.
- Creating a habit also writes its first `habit_targets` row: its `target_count`, with `effective_from` = the first day of the period containing `start_date`.
- `GET /v1/habits` answers `{items, next_cursor, has_more}` with the caller's non-deleted, non-archived habits ordered by id; `?include_archived=true` includes archived ones.
- `GET /v1/habits/{id}` answers the habit; a deleted habit answers 404 `not_found`.
- `PATCH /v1/habits/{id}` takes `{version, ...}` with any subset of `{name, description, target_count, start_date, end_date, archived}` and answers 200 with the updated habit, its `version` one higher; the same validation as creation applies.
- A `PATCH` without `version` answers 422 `validation_failed` with field `version`.
- A `PATCH` whose `version` is not the habit's current version answers 409 `{"error": {"code": "version_conflict", ...}, "current": <the habit as stored>}` and changes nothing.
- A `PATCH` body that contains `period` answers 422 `validation_failed` with field `period` and changes nothing.
- `description: null` and `end_date: null` in a `PATCH` clear them.
- Changing `target_count` updates `habits.target_count` and writes the `habit_targets` row for the current period (`effective_from` = the first day of the period containing today, or of the period containing `start_date` when the habit has not started yet), replacing that row's value if it exists; rows of earlier periods never change.
- The new `target_count` applies to the current period at once: progress and nudges use it from the next request on.
- `start_date` can be changed only while today is before the current `start_date`; otherwise the change answers 409 with code `start_date_locked`. A permitted change moves the habit's only `habit_targets` row to the period containing the new `start_date`.
- `end_date` can be changed at any time, also to a past date (which ends the habit); an `end_date` before the date of one of the habit's non-deleted check-ins answers 409 with code `checkins_after_end`.
- `archived: true` archives the habit and `archived: false` restores it; restoring when the user already has 50 active habits answers 409 `habit_limit`.
- `DELETE /v1/habits/{id}` answers 204, sets the habit's `deleted_at`, increments its `version`, and sets `deleted_at` on its non-deleted check-ins and nudges; nothing is removed from the database.
- Every change to a habit, check-in or nudge (create, update, soft delete, dismissal, a nudge rewrite) increments the owner's `users.change_seq` and stores the new value in that row's `change_seq`, and sets its `updated_at` to now.

## Check-ins

- `POST /v1/habits/{habit_id}/completions` takes `{id, date, note?}` (`id` a client UUID, `date` the user's local date) and answers 201 `{id, habit_id, date, note, created_at, updated_at}`.
- A check-in `date` after the user's today answers 422 with code `date_in_future`.
- A check-in `date` before the habit's `start_date` or after its `end_date` answers 422 with code `date_out_of_range`.
- A check-in `date` more than 60 days before the user's today (earlier than `t - 60`) answers 422 with code `date_too_old`; `t - 60` itself is accepted.
- When several of these date rules apply at once, the code answered is `date_in_future` first, then `date_out_of_range`, then `date_too_old`.
- `note` is at most 280 characters; longer answers 422 `validation_failed` with field `note`.
- A habit has at most one non-deleted check-in per date; another one with a new id answers 409 with code `already_checked_in`.
- A check-in with the id of an existing check-in of the same habit, date and note answers 200 with the existing one and writes nothing; the same id with different content, under another habit, or of a deleted check-in answers 409 `id_conflict`.
- Creating or deleting a check-in on an archived habit answers 409 with code `habit_archived`.
- `GET /v1/habits/{habit_id}/completions` answers `{items, next_cursor, has_more}` with the habit's non-deleted check-ins ordered by date ascending; `?from=` and `?to=` (local dates, inclusive) filter by date, and `from` after `to` answers 422 `validation_failed`.
- `DELETE /v1/habits/{habit_id}/completions/{id}` answers 204 and sets the check-in's `deleted_at`; a check-in of a different habit, or one already deleted, answers 404 `not_found`.
- `POST /v1/completions/batch` takes `{items: [{id, habit_id, date, note?}, ...]}` with 1 to 100 items, else 422 `validation_failed` with field `items`.
- A batch processes its items in order, each exactly as a single check-in create would, and answers 200 `{results: [{id, status, code?, completion?}, ...]}` in the items' order, where `status` is `created` (with the completion), `existing` (an identical retry, with the completion) or `rejected` (with the `code` the single create would answer, e.g. `not_found`, `date_too_old`, `already_checked_in`, `id_conflict`).
- A batch item that is rejected writes nothing, and never stops the other items.

## Lists and pagination

- A list answers `{items, next_cursor, has_more}`; `?limit=` is 1 to 200 (default 50), any other value answers 422 `validation_failed`, and `?cursor=` is the `next_cursor` of the previous page.
- `has_more` is true exactly when more items follow; `next_cursor` is null when `has_more` is false.
- A cursor that the app did not issue answers 422 with code `invalid_cursor`.

## Sync

- `GET /v1/sync?cursor=<opaque>&limit=<n>` answers `{changes, next_cursor, has_more}`: the caller's habits, check-ins and nudges whose `change_seq` is greater than the cursor's, ordered by `change_seq` ascending.
- Each change is `{type: "habit" | "completion" | "nudge", id, change_seq, deleted, data}`, where `deleted` is true for a soft-deleted row (a tombstone) and `data` is the row in its normal response shape (null for a tombstone).
- A missing or empty cursor means a full sync: every change from the start, tombstones included.
- `limit` is 1 to 200 (default 100); any other value answers 422 `validation_failed`.
- `next_cursor` is always present and encodes the `change_seq` of the last change returned (or the request's own cursor position when nothing is returned); `has_more` is true exactly when more changes follow.
- A cursor that the app did not issue answers 422 with code `invalid_cursor`.

## Progress of the current period

- `GET /v1/habits/{id}/progress` answers 200 `{habit_id, period, period_start, period_end, target, done, days_elapsed, days_remaining, rate_per_day, rate_per_period, required_per_day, required_per_period, projected, status}`, computed exactly by the rules of this section, all dates being local dates of the user.
- The current period is the period containing `t` (the user's today); `period_start` and `period_end` are its first and last day.
- The active window is `as = max(period_start, start_date)` to `ae = min(period_end, end_date)` (`ae = period_end` when `end_date` is null), and `A = ae - as + 1` is its number of days.
- `N` is the habit's current `target_count`; the effective target is `target = N` when `A = L`, else `target = (N*A + L - 1) div L` (pro-rated and rounded up; `div` is integer division throughout).
- `done` = `D` is the number of the habit's non-deleted check-ins dated from `as` to `ae` inclusive.
- `days_elapsed` = `E = t - as + 1` (today included) and `days_remaining` = `R = ae - t` (days after today).
- `open_today` is 1 when the habit has no non-deleted check-in dated `t`, else 0; the capacity `C = R + open_today` is the most check-ins still possible in the period, and `needed = max(0, target - D)`.
- `projected = (D*A) div E`, the total at the current rate.
- A rate `x / y` is reported in hundredths, rounded half up: `h(x, y) = (200*x + y) div (2*y)`, answered as the JSON number `h / 100` (e.g. 233 is answered as 2.33).
- `rate_per_day = h(D, E)` and `rate_per_period = h(D*L, E)` (the current rate scaled to a whole period).
- `required_per_day = h(needed, C)` and `required_per_period = h(needed*L, C)` when `C > 0` and `needed > 0`; both are 0 when `needed = 0`, and both are null when `needed > 0` and `C = 0`.
- `status` is the first that applies: `not_started` if `t < start_date`; `achieved` if `D >= target`; `unreachable` if `needed > C`; `on_track` if `projected >= target`, or if `E = 1` and `D = 0` (the first day of the active window); `behind` otherwise.
- A habit that has not started (`t < start_date`) answers the period containing `start_date`, with `D = 0`, `days_elapsed = 0`, `days_remaining = A`, `C = A`, both current rates 0, `projected = 0`, the required rates `h(target, A)` and `h(target*L, A)`, and `status = "not_started"`.
- A habit whose `end_date` is before today answers the period containing `end_date`, computed as if `t = end_date` with `open_today = 0` (so `days_remaining = 0`, `C = 0`), and its status is `achieved` or `unreachable`.
- Example (user in America/New_York, clock 2026-03-04T12:00:00Z, so today is Wednesday 2026-03-04): a weekly habit with target 3 started 2026-03-02 with one check-in dated 2026-03-02 answers `period_start` 2026-03-02, `period_end` 2026-03-08, `target` 3, `done` 1, `days_elapsed` 3, `days_remaining` 4, `rate_per_day` 0.33, `rate_per_period` 2.33, `required_per_day` 0.4, `required_per_period` 2.8, `projected` 2, `status` `behind`.
- Example (same user and clock): a weekly habit with target 4 starting 2026-03-04 has `target` 3 (5 active days of 7: `(4*5 + 6) div 7`), `done` 0, `days_elapsed` 1 and `status` `on_track`.
- Example (same user and clock): a weekly habit with target 6 started 2026-03-02 with no check-ins has `needed` 6 > `C` 5, so `status` is `unreachable`.
- Example (same user and clock): a monthly habit with target 10 started 2026-03-01 with check-ins dated March 1, 2 and 3 has `target` 10, `done` 3, `days_elapsed` 4, `days_remaining` 27, `projected` 23 and `status` `on_track`.
- Example (timezone): at clock 2026-03-09T02:00:00Z it is still Sunday 2026-03-08 22:00 in America/New_York, so that user's current week is 2026-03-02 to 2026-03-08 with `days_remaining` 0, while for a user in UTC it is Monday 2026-03-09 and a new week.

## Home screen

- `GET /v1/home` answers `{today, habits, nudges}`: `today` the user's local date; `habits` every non-deleted, non-archived habit of the caller ordered by id, each the habit's normal body plus `progress` equal to its `GET /v1/habits/{id}/progress` body; `nudges` the caller's active nudges as a plain array (not paginated), the same nudges `GET /v1/nudges` lists, ordered by `habit_id`.

## Nudges

- A nudge is a habit's status for one local day, stored in `nudges` with `for_date` = that day, `kind` = the status, and `message` built exactly from the templates below; `not_started` gets no nudge.
- `on_track` message: `On track: {done} of {target} this {unit}. Keep it up!`, where `{unit}` is `week`, `month` or `year`.
- `behind` message: `Behind: do {needed} more in the next {C} day(s). Needs {required_per_period} per {unit}, you are at {rate_per_period}.`, the rates written with exactly two decimals (e.g. `2.80`, `0.00`).
- `unreachable` message: `Out of reach this {unit}: at most {D + C} of {target}. A new {unit} starts on {period_end + 1 day}.`
- `achieved` message: `Goal reached: {done} of {target} this {unit}. Nice work!`
- Example: the first progress example's nudge is `Behind: do 2 more in the next 5 day(s). Needs 2.80 per week, you are at 2.33.`
- No nudge is ever created or rewritten for a deleted or archived habit, a habit that has not started, or a habit whose `end_date` is before the user's today.
- Every check-in write that succeeds (a created or deleted check-in, also in a batch) computes the habit's status for the user's today and, for an eligible habit, creates today's nudge or rewrites the `kind` and `message` of today's existing nudge unless it is dismissed; the write's own answer is unchanged by this.
- Rewriting a nudge never changes its `delivery` or `delivery_reason`.
- A nudge created by a check-in write before the user's daily pass of that day has `delivery` `pending`; one created after that day's pass has `delivery` `suppressed` with reason `after_daily_pass`.
- A dismissed nudge is never rewritten, recreated or pushed for the same day.
- The background service `nudger` checks every minute (and once at start-up), for each user, whether the user's local time has reached their `nudge_time` and their `last_pass_date` is before their local today; if so it runs that user's daily pass and sets `last_pass_date` to the local today.
- A daily pass, for each eligible habit of the user in id order, creates today's nudge or rewrites its `kind` and `message` (unless dismissed), then decides the delivery of each of today's nudges that is still `pending`, in habit id order, by the first rule that applies.
- Delivery rule 1: a dismissed nudge becomes `suppressed` with reason `dismissed`.
- Delivery rule 2: an `on_track` or `achieved` nudge becomes `suppressed` with reason `in_app_only`.
- Delivery rule 3: if the user's `push_enabled` is false, the nudge becomes `suppressed` with reason `push_disabled`.
- Delivery rule 4: if the user has no device row, the nudge becomes `suppressed` with reason `no_device`.
- Delivery rule 5: if the user's local time is inside their quiet hours (from `quiet_start` inclusive to `quiet_end` exclusive, crossing midnight when `quiet_start` is later than `quiet_end`; no quiet hours when they are equal), the nudge becomes `suppressed` with reason `quiet_hours`.
- Delivery rule 6: if 3 of the user's nudges for that local day already have `delivery` `sent`, the nudge becomes `suppressed` with reason `daily_limit`.
- Delivery rule 7: otherwise the push service sends the nudge's message to every device of the user; the nudge becomes `sent` if at least one send succeeded, else `failed` (reason null in both cases).
- So a habit gets at most one push per day and a user at most 3 pushes per local day; every nudge, pushed or not, stays in the in-app list.
- Sending a push goes only through the push service interface (`PushSender`), which the test harness replaces with a stub.
- `GET /v1/nudges` answers `{items, next_cursor, has_more}` (default page 50, maximum 200, as every list) with the caller's active nudges ordered by `habit_id`: those with `for_date` = the user's today, `dismissed_at` and `deleted_at` null, and a non-archived habit; each item as `{id, habit_id, for_date, kind, message, delivery, delivery_reason, dismissed_at}`.
- `POST /v1/nudges/{id}/dismiss` sets `dismissed_at` to now and answers 200 with the nudge; a nudge already dismissed answers 409 with code `nudge_dismissed`, unchanged.
- A dismissal belongs to the user, not to a device: a dismissed nudge is not returned as active by `GET /v1/nudges`, by the `nudges` of `GET /v1/home` or by `GET /v1/sync` for any device or installation of that user, and `GET /v1/sync` returns the dismissed nudge as a `nudge` change with `deleted` false and `data.dismissed_at` set, so the user's other devices remove it locally.
- After a successful `POST /v1/nudges/{id}/dismiss`, the push service sends a silent clear push for that nudge (carrying the nudge's id) to every device of the user except the device row whose `installation_id` equals the caller's `sid`.
- A clear push that fails does not fail the dismiss (it still answers 200) and does not change the nudge's `delivery` or `delivery_reason`.
- A dismiss answered 409 `nudge_dismissed` sends no push.
- Clear pushes never count towards the daily limit of 3 pushes in delivery rule 6.

## History

- `GET /v1/habits/{id}/history` answers the habit's past periods, newest first, as `{items: [{period_start, period_end, target, done, achieved}]}`.
- A past period is one whose last day is before the user's today, from the period containing `start_date` up to the last period before the current one, and never after the period containing `end_date`.
- A past period's `target` is the `target_count` of the habit's `habit_targets` row with the latest `effective_from` on or before that period's first day, pro-rated by the period's active window as in the progress rules; `done` is the number of non-deleted check-ins in its active window; `achieved` is `done >= target`.
- `?limit=n` (1 to 100, default 12) answers at most n periods; any other limit answers 422 `validation_failed`.
- A habit that has not started (today before `start_date`) answers 409 with code `habit_not_started`; a started habit with no past period answers 200 `{"items": []}`.
