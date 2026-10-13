# Layer 3 (contracts between projects) on a second provider, Streak: pre-registration

## 1. Status

Date: 2026-10-13. Branch `np-contracts-streak` (worktree `lattice-contracts-streak`, from
`next-phases` 573aafc, which already contains layer 3). Written BEFORE any probe of this
experiment exists or runs. Nothing below was run or measured by this document's author: no
`lattice.contracts` command was run on Streak. The predictions come from three sources: the rules
in `lattice/contracts/export.py` and `check.py` (read at 573aafc), recon R1
(`r1/r1-streak-surface.json` and `r1/r1-surface.md` in the session scratchpad, re-verified by the
orchestrator), and a hand reading of Streak's `spec.py` at a0db033 (1829 lines). The results will
go in `docs/results/2026-10-13-contracts-streak-results.md`, with raw data in
`docs/results/data/2026-10-13-contracts-streak/`. No LLM and no API key are used: the cost is 0 tokens.

**The question.** Does layer 3 hold on a second, differently shaped provider **without code
changes**? The first experiment covered one pair: job-scheduler, a Python/FastAPI provider with an
old-format spec and int ids. Streak differs in almost every respect: Go + Gin + MySQL (only its
SPEC is used here), a Studio-format `spec.py` with `access_matrix`, `/v1` paths, UUID and int ids,
cursor-paginated envelopes, `json_key(error)` error bodies, 404 (never 403) for other users' rows,
429 rate limits, 14 `Await`s and a background nudger.

## 2. Decisions taken before any probe

1. **Provider ref: Streak `main` at a0db033.** This is the reviewed spec, and Streak's spec fixes
   land on `main`. Its `spec.py` and `spec.trace.json` are identical on `build/streak`, so the
   export cannot depend on the choice. The Go code on `build/streak` is irrelevant to a spec-only
   check. All work is done on `git clone --no-hardlinks ~/Documents/streak <scratch>/streak-clean`
   checked out at a0db033. Nothing is ever written into `/Users/vivekgade/Documents/streak`: its
   `git status --short` is ` M studio.toml`, the owner's uncommitted file. The clone carries the
   committed `studio.toml`, so the clean clone is not dirty.
2. **Spec ref: `spec:SPEC`, the default.** No `spec` key is set in the manifest; Streak's
   `studio.toml` has none either. `spec:SPEC` is Streak's whole spec: 156 scenarios
   (`(*ACCESS, *SCENARIOS)`).
3. **Static only.** The LIVE check is out of scope, because Streak needs a Go build and a MySQL
   container. One probe (W3) records what a `contract = "live"` link on Streak does.
4. **Consumer `streak-widget`** (`dogfood/contracts/streak-widget/`): a tiny home-screen widget
   client with 7 calls on Streak's real endpoints (R1 templates). `client.py` is committed as
   below; it imports `httpx`, which is in Lattice's venv.

```python
"""streak-widget: what a home-screen widget asks Streak for (today, one habit, a check-in)."""
import os, sys, uuid
import httpx

def main() -> int:
    c = httpx.Client(base_url=os.environ["STREAK_URL"], timeout=10)
    r = c.post("/v1/auth/login", json={"email": os.environ["STREAK_EMAIL"],               # W1
                                       "password": os.environ["STREAK_PASSWORD"],
                                       "installation_id": os.environ["WIDGET_INSTALLATION"]})
    if r.status_code != 200: return fail("login", r)
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    ttl = r.json()["expires_in"]
    me = c.get("/v1/me").json()                                                           # W2
    home = c.get("/v1/home").json()                                                       # W3
    print(f"user {me['id']} ({me['timezone']}) on {home['today']}; token valid {ttl} s")
    habits, params = [], {"limit": 50}
    while True:
        page = c.get("/v1/habits", params=params).json()                                  # W4
        habits += page["items"]
        if not page["has_more"]: break
        params = {"limit": 50, "cursor": page["next_cursor"]}
    want = os.environ.get("WIDGET_HABIT", "walk")
    habit = next((h for h in habits if h["name"] == want), None)
    if habit is None:
        print("no habit named", want, file=sys.stderr)
        return 1
    p = c.get(f"/v1/habits/{habit['id']}/progress").json()                               # W5
    print(f"{want}: {p['done']} of {p['target']} ({p['status']})")
    if os.environ.get("WIDGET_CHECK_IN") == "1":
        r = c.post(f"/v1/habits/{habit['id']}/completions",                               # W6
                   json={"id": str(uuid.uuid4()), "date": home["today"]})
        if r.status_code not in (200, 201): return fail("check-in", r)
        print("checked in", r.json()["id"], "for", r.json()["date"])
    if home["nudges"] and os.environ.get("WIDGET_DISMISS") == "1":
        nudge = home["nudges"][0]["id"]
        print("dismissed", c.post(f"/v1/nudges/{nudge}/dismiss").json()["id"])           # W7
    return 0

def fail(what, r):
    print(what, "failed:", r.status_code, file=sys.stderr)
    return 1

if __name__ == "__main__":
    sys.exit(main())
```

   **Import rules.** Each import lists exactly the **top-level** keys its line reads or sends.
   Query names count as request fields, because `holds` accepts `query_fields`. `status` is what
   the line handles. `as_user = "ann"` is set on every token import: the widget's account has
   ann's standing. Nested reads cannot be expressed in the import format: `items[].id` and
   `items[].name` (W4), and `nudges[0].id` (W3). They are listed as comments and disclosed (limit
   3b, D12). `lattice.imports.toml` has exactly these keys and values, in this order. It is shown
   here as the equivalent inline array to save lines; the committed file writes each entry as an
   `[[import]]` table with one key per line (same parse result):

```toml
# streak-widget (client.py) relies on Streak: one table per call W1-W7, top-level keys only.
# Not expressible (nested): W3 nudges[].id; W4 items[].id, items[].name.
import = [
 {provider="streak", endpoint="POST /v1/auth/login", status=[200], auth="public", request_fields=["email","password","installation_id"], response_fields=["access_token","expires_in"]},
 {provider="streak", endpoint="GET /v1/me", status=[200], auth="token", as_user="ann", response_fields=["id","timezone"]},
 {provider="streak", endpoint="GET /v1/home", status=[200], auth="token", as_user="ann", response_fields=["today","nudges"]},
 {provider="streak", endpoint="GET /v1/habits", status=[200], auth="token", as_user="ann", request_fields=["limit","cursor"], response_fields=["items","has_more","next_cursor"]},
 {provider="streak", endpoint="GET /v1/habits/{id}/progress", status=[200], auth="token", as_user="ann", response_fields=["done","target","status"]},
 {provider="streak", endpoint="POST /v1/habits/{habit_id}/completions", status=[200,201], auth="token", as_user="ann", request_fields=["id","date"], response_fields=["id","date"]},
 {provider="streak", endpoint="POST /v1/nudges/{id}/dismiss", status=[200], auth="token", as_user="ann", response_fields=["id"]},
]
```

5. **Probe workspace** (generated in the scratchpad), `lattice.workspace.toml`:
   - `[[project]] name = "streak"`, `path` = the clone, `role = "provider"`, and no other key. The
     export needs none; live reads `studio.toml`.
   - `[[project]] name = "streak-widget"`, `path` = a copy of `dogfood/contracts/streak-widget`,
     `role = "consumer"`.
   - One `[[link]]`: `from = "streak-widget"`, `to = "streak"`, `kind = "uses"`,
     `contract = "static"`. W1 and W3 vary the `contract` key.

   A lock pinned against the clean clone is committed as the golden
   `dogfood/contracts/golden-streak/lattice.contracts.lock.json`. Each D or C edit is applied to a
   fresh clone of the clean clone with `str.replace`, after asserting the stated occurrence count.
   It is then committed in that clone (`-c user.name=probe -c user.email=probe@example.invalid`).
   The runner is a new script, `dogfood/contracts/streak_probes.py`, written by a later node. It
   may reuse `probes.py` helpers, and it hard-codes this document's predictions, which are audited
   line by line against it.
6. **The two endpoints outside `/v1`.** Neither `export_spec` nor `template_path` has any notion
   of a test-harness or internal route; only "no 2xx step" excludes an endpoint.
   - `GET /health` has the anonymous access cell `"health": 200`.
   - `PUT /test/clock` has 6 anonymous `clock()` steps with expect_status=200.

   Both are therefore **predicted exported**, as `public`, with `/test/clock` showing request
   field `now`. Exporting a test-harness route as part of the contract is a **FINDING about layer
   3** (consumers could rely on it, and a drift in it would be CHANGED). It is not pre-fixed here.
7. **No code change before or during the blind run.** `git diff 573aafc -- lattice/` must be empty
   when the probes run. If a probe yields a finding, any code change it motivates is a separate,
   later, post-hoc commit. The blind numbers stay as first measured.

## 3. Honest limits for Streak (what the static check cannot see)

Streak's spec has 0 `Contract`s, so every exported fact is an observed lower bound (R1).

- **a. Headers.** The `X-Client-Version` -> 426 rule (R6) is an `Unchecked`, because `Step` has no
  headers. A widget that sends an old version (or none) is invisible to the check: it would get
  426. `Retry-After` (R29) and the `Authorization` scheme are invisible too.
- **b. Pagination.** `profile.api.page_shape` says items/limit/offset, but the bodies show a
  cursor envelope (`items`, `next_cursor`, `has_more`). The export never reads `page_shape`. It
  reads bodies, and `_fields` takes only an object's **top-level** keys.
  - The envelope keys are exported. The keys of the items inside it are NOT (D12 is a predicted
    MISS).
  - `cursor` appears as a query name only in a 422 step of `GET /v1/habits` (R93's cursors "cannot
    be reused by a later step"). It is a permanent gap there. In `GET /v1/sync`,
    `params={"cursor": ""}` is a 200 step.
- **c. Computed and nested fields.** Names only, no types (D9 is a MISS). Nested structures are
  invisible: `home.habits[].progress`, `notification_prefs.*`, `batch.results[]`, `sync.changes[]`.
- **d. Ids.** UUID and int ids are both templated to `{}`. A client-chosen id (`id` in POST habits
  and POST completions) is just a request field: nothing says the client generates it.
- **e. Ownership is 404, never 403.** "403" occurs 0 times in `spec.py`, so `refused` is empty on
  every endpoint. A handle that loses access appears only as leaving `granted` (`granted -> none`,
  `auth_tightened`). An outsider's 404 is indistinguishable from "not found".
- **f. 14 `Await`s and the background nudger.** `Await`s are skipped. What the nudger writes
  (nudges, delivery, `last_pass_date`) is not in the export. A widget that relies on a nudge
  appearing at `nudge_time` cannot be checked.
- **g. Rate limits.** 429 is only a status, seen on `POST /v1/habits` and `GET /v1/me`. The policy
  (120/min) is not exported.
- **h. Required vs optional.** D10 is predicted under-classified. Error codes (`version_conflict`,
  `not_found`) are not exported; only the `error_body` style is.
- **i. Live.** Not run. Static cannot see the Go app diverging from its spec.

## 4. Probes

**Global rules.**
- Every deterministic probe runs **10 times**. Pass requires the canonical output (`elapsed_s`
  excluded) to be identical in 10 of 10 runs, plus the probe's own rule.
- All D, C and V probes check against the lock pinned on the clean clone.
- Auth items are written as the code writes them (`as_user:ann`).
- "S:" below is `spec.py` at a0db033. Every count is
  `git -C /Users/vivekgade/Documents/streak show a0db033:spec.py | grep -cF -- '<old>'`. For every
  string here the number of lines equals the number of occurrences (`grep -oF | wc -l`); the
  exception, E5a, is noted in its row.

**B0 baseline.** Predictions:

- **Endpoint count: 26.** R1 has 26 templates. Each one has at least one 2xx status, including
  `GET /health` (200) and `PUT /test/clock` (200), so 26 - 0 excluded = **26**.
  `template_path` collapses digits and UUIDs exactly as R1's normaliser does; only the names
  differ (`{}`).
- **Auth.**
  - `public` **5**: `GET /health`, `PUT /test/clock`, `POST /v1/auth/login`,
    `POST /v1/auth/refresh`, `POST /v1/auth/register` (anonymous 2xx).
  - `token` **21**: every other endpoint. Its only anonymous steps are the access-matrix
    `None` cells, all 401.
  - `unknown` **0**.
- **`error_body`** = `"json_key(error)"`; `basis` = `observed`.
- **Statuses** equal R1's `expect_status` per template, 26 of 26. Named:
  - `POST /v1/habits` = [200, 201, 401, 409, 422, 429];
  - `GET /v1/me` = [200, 401, 429];
  - `POST /v1/habits/{}/completions` = [200, 201, 401, 404, 409, 422].
- **`GET /v1/habits`.**
  - `response_fields` = **[has_more, items, next_cursor]**: the envelope only, no item keys (the
    bodies are objects, not lists).
  - `query_fields` = [include_archived, limit]: `cursor` occurs only in a 422 step.
  - `body` = [object].
- **Bodies.** `body` = [object] on 19 endpoints and [] on 7: `GET /health`, `PUT /test/clock`,
  `POST /v1/auth/logout`, and the 4 DELETEs (204, no body).
- **The widget's endpoints.**

| endpoint | request (and query) | response |
|---|---|---|
| login | [email, installation_id, password] | [expires_in, token_type] |
| `GET /v1/me` | | [email, id, notification_prefs, timezone] |
| `GET /v1/home` | | [habits, nudges, today] |
| progress | | the 14 keys of R1 minus `error` |
| `POST .../completions` | [date, id, note] | [date, habit_id, id, note] |
| `POST .../dismiss` | | [habit_id, id, kind, message] |

- **Granted and refused.**
  - `ann` is granted on all 21 token endpoints.
  - `ben` is granted on exactly **10**: `POST /v1/completions/batch`, `POST /v1/devices`,
    `GET /v1/habits`, `POST /v1/habits`, `GET /v1/habits/{}/progress` (spec line 1370),
    `GET /v1/home`, `GET /v1/me`, `PUT /v1/me`, `GET /v1/nudges`, `GET /v1/sync`.
  - The 5 public endpoints have `granted` = [].
  - `refused` = [] on **26 of 26**.
- **`GET /health` and `PUT /test/clock` appear** (decision 6, reported as a finding).
- **Pin and check.**
  - `pin` exits 0 and the lock has 26 endpoints.
  - `check` exits 0, with 0 findings, `version_changed` false, `dirty` false and exactly **2
    gaps**, in this order:
    1. `request_field_unobserved cursor @ GET /v1/habits` (limit 3b);
    2. `response_field_unobserved access_token @ POST /v1/auth/login` (no `expect_body` states it).
  - Every other claim of W1-W7 holds, including `as_user:ann` on all 6 token imports and both
    200 and 201 on W6 (the retry at line 1107 is a 200).

Pass: all of the above.

**D-series: drifts in the provider's `spec.py`.**
- Pass: **13 of 13** drifts match the predicted worst class (BROKEN, CHANGED, or none = MISS) and
  the exact set of BROKEN (kind, endpoint, item).
- Also reported, with a prediction of 13 of 13: the full CHANGED set (kind, endpoint, item,
  `relied`) matches.
- In every D, `version_changed` is true. The gap list equals B0's in 12 of 13; in D7 it is empty
  (both gap endpoints are missing, so only endpoint claims are reported).
- Of the **10** drifts that break the widget's code (D1-D7, D9, D10, D12), static is predicted to
  flag **7 as BROKEN** (D1-D7), 1 only as relied CHANGED (D10), and to miss 2 (D9, D12).

| id | S: old -> new (count) | predicted | why |
|---|---|---|---|
| D1 endpoint removed | E1a `"home": (200, {"today": TODAY,` -> `"home": (404, {"today": TODAY,` (2; lines 314 ben, 370 ann); E1b `Step("GET", "/v1/home", "ann", expect_status=200,` -> `...=404,` (2; 1391, 1666) | BROKEN `endpoint_missing` GET /v1/home (item ""); CHANGED none; exit 1 | every 2xx becomes 404, the answer to a removed route; with only 401 and 404 left the template is excluded, and `endpoint_removed` is folded |
| D2 status changed | E2a `"dismiss": (200, {"id": u(500), "habit_id": u(100)},` -> `(204,` (1; 381); E2b `Step("POST", f"/v1/nudges/{u(500)}/dismiss", "ann", expect_status=200,` -> `...=204,` (3; 1642, 1659, 1678); E2c `Step("POST", f"/v1/nudges/{u(n)}/dismiss", "ann", expect_status=200,` -> `...=204,` (1; 1693) | BROKEN `status_not_observed` 200 @ POST /v1/nudges/{}/dismiss; CHANGED `status_added` 204 relied=true | 200 leaves the set. The bodies stay on the 204 steps, so the fields and `granted` do not move |
| D3 response field renamed | `"expires_in": 900}` -> `"expires_in_s": 900}` (2; 491 login, 521 refresh) | BROKEN `response_field_missing` expires_in @ POST /v1/auth/login; CHANGED `field_added` response expires_in_s @ login relied=true; `field_added` expires_in_s and `field_removed` expires_in @ POST /v1/auth/refresh relied=false | an API-wide rename: relied only where the widget imports |
| D4 request field renamed | `"password": "password-ann",` -> `"passphrase": "password-ann",` (4; 489, 499, 513, 616) | BROKEN `request_field_missing` password @ POST /v1/auth/login; CHANGED `field_added` request passphrase relied=true | both 2xx login steps (489, 499) now send `passphrase`; register is untouched |
| D5 access removed from ann | E5a: the line that ENDS with `"home": (200, {"today": TODAY,` -> `(404,` (1 line: `grep -cE '"home": \(200, \{"today": TODAY,$'` = 1; line 370; in `str.replace` terms, the old string includes the trailing newline); E5b = E1b (2) | BROKEN `auth_changed` as_user:ann @ GET /v1/home; CHANGED `status_added` 404 relied=true | ann has no 2xx left (granted -> none, folded); ben's 200 body keeps the endpoint and all 3 fields |
| D6 next_cursor renamed | `"next_cursor"` -> `"next"` (11; the 12th `next_cursor`, in R93's `Unchecked` text, has no quotes) | BROKEN `response_field_missing` next_cursor @ GET /v1/habits; CHANGED `field_added` next @ GET /v1/habits relied=true; `field_added` next and `field_removed` next_cursor @ GET /v1/habits/{}/completions and @ GET /v1/nudges, relied=false (4) | all 11 sites are 2xx envelope bodies of these 3 list endpoints |
| D7 version bump | `/v1/` -> `/v2/` (144, including the docstring at line 11 and the `hp()` f-string) | BROKEN `endpoint_missing` x 7 (one per import); CHANGED 41: 17 `endpoint_removed` /v1 + 24 `endpoint_added` /v2, all relied=false; 0 gaps; still 26 endpoints | `v2` is not an id segment; the 7 relied removals are folded |
| D8 endpoint added | `Step("GET", "/v1/me", "ben", expect_status=200),` -> the same + newline + 8 spaces + `Step("GET", "/v1/me/stats", "ben", expect_status=200, expect_body={"habits": 0}),` (1; 633) | CHANGED `endpoint_added` GET /v1/me/stats relied=false; BROKEN none; exit 1 | a new 2xx template that no import names |
| D9 field type | `"expires_in": 900}` -> `"expires_in": "900"}` (2) | **MISS**: 0 findings, `spec_hash` equal, exit 0 | names only, no types; the widget's TTL arithmetic would break |
| D10 required field | `return {"id": u(n), "date": date, **more}` -> `return {"id": u(n), "date": date, "client_tz": "UTC", **more}` (1; 204, `ci()`) | **under-classified**: CHANGED `field_added` request client_tz @ POST /v1/habits/{}/completions relied=true; BROKEN none | `ci()` feeds only completion POSTs; required-ness is not exported |
| D11 ownership (ben vs ann) | `Step("GET", hp(101, "/progress"), "ben", expect_status=200,` -> `...=404,` (1; 1370) | CHANGED `auth_tightened` as_user:ben @ GET /v1/habits/{}/progress relied=true; BROKEN none | ben's only 2xx there becomes Streak's 404-for-others; 404 is already observed and ben's keys all occur in ann's bodies; `relied` is endpoint-level, so a ben-only change on a relied endpoint is tagged relied |
| D12 nested item key | `"list_habits": (200, {"items": [{"id": u(100), "name": "walk"}],` -> `... "title": "walk"}],` (1; 339) | **MISS**: 0 findings, `spec_hash` equal | item keys inside an object envelope are not exported (limit 3b); the widget's `h["name"]` breaks |
| D13 relied noise (429) | `Step("GET", "/v1/me", "ann", expect_status=429, expect_body=err("rate_limited")),` -> `Step("GET", "/v1/me", "ann", expect_status=200),` (1; 632) | CHANGED `status_removed` 429 @ GET /v1/me relied=true; BROKEN none | the only 429 on that endpoint; the widget relies on 200 only |

**C-series: controls that must stay quiet** (0 BROKEN and 0 relied findings). Pass: **6 of 6** as
predicted.

| id | S: change (count) | predicted |
|---|---|---|
| C1 no change | none (the clean clone) | exit 0, 0 findings, B0's 2 gaps, `version_changed` false |
| C2 comment and whitespace | `EMPTY_LIST = {"items": [], "next_cursor": None, "has_more": False}` -> the same + `  # an empty first page\n` (that is, a comment and one extra blank line after the line) (1; 124) | file sha256 changes; `spec_hash` equal; exit 0; `version_changed` true |
| C3 scenario reorder | `scenarios=(*ACCESS, *SCENARIOS),` -> `scenarios=(*SCENARIOS, *ACCESS),` (1; 1796): all 156 scenarios are reordered | `spec_hash` equal; exit 0 |
| C4 benign, unused endpoint | `expect_status=201, expect_body={"email": "cara@example.com", "timezone": "UTC"},` -> `... "timezone": "UTC", "id": 3},` (1; 431) | exit 1, exactly 1 CHANGED `field_added` response id @ POST /v1/auth/register relied=false; 0 BROKEN |
| C5 one scenario's UUIDs | in `progress_timezone_example`: `habit(101, "b", user=2, start="2026-02-02")` -> `habit(777, ...)` (1; 1365); `Step("GET", hp(101, "/progress"), "ben", expect_status=200,` -> `hp(777, ...)` (1; 1370) | `spec_hash` equal (the concrete UUID ...0101 becomes ...0777, the same template); exit 0 |
| C6 every UUID | `return f"00000000-0000-4000-8000-{n:012d}"` -> `return f"00000000-0000-4000-9000-{n:012d}"` (1; 128): every UUID in the spec becomes another valid UUID | `spec_hash` equal; exit 0 |

**W-series: workspace opt-in.** Pass: **4 of 4**.

- **W1.** The link has no `contract` key, on a clone whose `spec.py` ends with
  `raise RuntimeError("not loaded")`. `contracts check` exits 0 with `checked` 0: the spec is
  never loaded. The same link with `contract = "static"` exits 2 with `export_failed`.
- **W2.** With `"static"`, the spec loads and the check runs. The result equals B0, and the link's
  `live` is null.
- **W3.** With `contract = "live"` on Streak and the golden lock in place (without a lock, `no_pin`
  comes first), read from `live.py` and `harness_from_project`:
  - **`check`** (no `--static-only`) exits **2**. The link has `status` `"error"` and
    `error.code` **`unsupported_database`**: the child loads the spec, builds the harness from
    `studio.toml` (`database = "mysql"`) and refuses any database that is not SQLite.
  - The link's `findings` and `gaps` are [], because `_error_link` replaces the static result.
  - No container, no Go build and no process is started. `bin/` is git-ignored and absent from
    the clone.
  - `check --static-only` exits 0, with `live` = `{"status": "not_run", "reason": "static-only"}`.
  - `pin` exits 0, because pin never runs live.

  Pass: all of these, 10 of 10.
- **W4.** A manifest of this experiment without any layer-3 keys. These outputs, run on 573aafc,
  are **byte-identical** (6 of 6) to the old code in the detached worktree
  `/Users/vivekgade/Documents/lattice-contracts-base` (c0cdefc; confirm HEAD first):
  `lattice workspace show`, `show --json`, `validate`, `validate --json`, `impact streak` and
  `impact streak --json`.

**V-series: pin and lock.** Pass: **6 of 6**.

- **V1.** Pin 10 times on the clean clone. The locks are byte-identical to each other and to the
  golden. `spec_hash` equals a recomputed sha256 of the embedded export. The lock contains 0
  occurrences of the scratch path and no timestamp.
- **V2.** With no lock: exit 2, `no_pin`, and the message names the lock path and
  `lattice contracts pin`.
- **V3.** After D1:
  - `pin` exits 1 and the lock's bytes are unchanged;
  - `pin --force` writes the lock;
  - the next check exits 0, with a third gap `endpoint_unobserved GET /v1/home`.
- **V4.** A README-only commit in the clone: exit 0, 0 findings, `version_changed` true, the same
  `spec_hash`, and a `git_ref` other than a0db033.
- **V5.** C2's edit left uncommitted: `dirty` true, `version_changed` true, exit 0.
- **V6.** The lock with one hex digit of `spec_hash` flipped: exit 2, `bad_pin`.

**S1, safety.** In `/Users/vivekgade/Documents/streak`, before and after the whole run:
- HEAD = a0db033…;
- `git --no-optional-locks status --short` = ` M studio.toml`;
- the sha256 of the owner's `studio.toml` is unchanged;
- `find /Users/vivekgade/Documents/streak -newer <start marker>` finds 0 files.

job-scheduler is never opened. Pass: identical (a single measurement).

**T1, cost** (reported, with a threshold). The median wall time of the static check of one link
over 10 runs must be **≤ 10 s**. The prediction is **≤ 1 s**:
- the first pair took 0.20 s;
- Streak's spec is about 3x larger (466 steps against 152);
- the cost is dominated by the subprocess start and the spec import.

The time of `contracts export streak` is also reported.

## 5. Named risks (what could make this experiment meaningless)

1. **Imports written to fit (ci-bot bias).** The widget was designed by the same author who read
   R1 and `spec.py`, and who wrote D3, D9 and D12.
   - `expires_in` and `items[].name` are read partly so that those drifts have a reliance to hit.
     That is disclosed here.
   - Mitigations: the field rule is mechanical (every top-level key read or sent). Both gaps are
     kept, although dropping `access_token` and `cursor` would make B0 "clean". The nested reads
     the format cannot express are listed, not hidden.
2. **The code is not blind.** Layer 3 was built knowing the first experiment's predictions, and
   this author read `export.py` and `check.py` before predicting. A pass therefore shows that the
   existing rules **generalise to a new spec shape without code changes**. It does not show that
   an independent implementation would agree.
3. **Predictions by hand reading.** `spec.py` was read, not executed: 156 scenarios, most of them
   generated by `access_matrix`. A misread step (for example a forgotten anonymous 2xx, or a ben
   200) would surface as a miss that is a **prediction error, not a layer-3 defect**. The results
   must classify each miss as one or the other, with the step that explains it.
4. **UUID and int templating collisions.**
   - Any all-digit or UUID-shaped literal segment would be templated. Streak has none: R1 found
     48 concrete paths and 26 templates, and `v1` is not all digits.
   - A future `/v1/reports/2026` would collide. C5, C6 and the B0 count of 26 guard the current
     spec.
5. **Test-harness endpoints are part of the contract** (decision 6). `PUT /test/clock` is public
   and writable. A consumer could import it, and an edit to it would be CHANGED. Layer 3 has no
   notion of internal routes. The prediction is a finding, reported, not fixed.
6. **Observed-set noise from 156 scenarios.**
   - Streak's tests touch relied endpoints heavily: `GET /v1/me` alone has 127 steps. Unrelated
     test edits (D13, D11) therefore give relied CHANGED, and a re-pin acknowledges them.
   - Unions hide partial changes. D5 must edit all 3 of ann's 2xx sites; editing 2 of them would
     be invisible.
7. **One more pair is still not breadth.**
   - Streak's spec is still a Lattice spec written in Python by the same lab. The Go app is never
     run (static only).
   - There is one consumer, written for the experiment. Two pairs show that the rules survive a
     second shape; they say nothing about third-party specs or real consumers.
8. **W3 depends on the environment.** The predicted `unsupported_database` requires
   `harness.py` to import in Lattice's venv. It imports `bcrypt` (present: 5.0.0) and
   `lattice.spec.db`.
   - If the import fails, the code gives `no_harness` instead.
   - No test in `tests/contracts` covers `unsupported_database`, so this path is untested.

## 6. Decisions of the owner recorded (inputs, not probes)

- The `as_user` coupling is **accepted for now**: the widget names Streak's seed handle `ann`, and
  a renamed handle would read as `auth_changed`.
- **BROKEN-needs-pin is kept**: a reliance never shown in the spec is a GAP, and `pin --force`
  turns a BROKEN into a GAP (V3).
- Only Streak's spec is used. Live checking of a MySQL/Go provider is out of scope; W3 records the
  current refusal.

## 7. Blind vs post hoc, and verification

The edits, controls, predictions and pass rules above are fixed now, before any probe exists.
- A failed prediction (a drift classified differently, a predicted MISS that is caught, a control
  that is not quiet, a B0 fact that differs) is reported as a **finding**, with its output. It is
  classified as a prediction error (risk 3) or a layer-3 defect, and never fixed silently.
- A code change motivated by a finding goes in a **separate post-hoc commit**, after the blind run
  is recorded. The blind numbers are never replaced.
- Anything learned while running (timings, edge cases, runner decisions) goes in a separate
  "Post hoc" section.

**Verification** (the orchestrator runs these):

```sh
export UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/Users/vivekgade/Documents/lattice/.venv \
       PYTHONPATH=/Users/vivekgade/Documents/lattice-contracts-streak PYTHONDONTWRITEBYTECODE=1
cd /Users/vivekgade/Documents/lattice-contracts-streak
ST=/Users/vivekgade/Documents/streak
git -C $ST --no-optional-locks rev-parse HEAD; git -C $ST --no-optional-locks status --short   # S1 before
shasum -a 256 $ST/studio.toml; touch "$SCRATCH/s1-marker"
# every edit string of D1-D13 and C2-C6, e.g.:
git -C $ST show a0db033:spec.py | grep -cF -- '"next_cursor"'                                  # 11
git -C $ST show a0db033:spec.py | grep -cE '"home": \(200, \{"today": TODAY,$'                # 1 (E5a)
git diff --stat 573aafc -- lattice/                                                           # empty: no code change
uv run python dogfood/contracts/streak_probes.py --runs 10 \
  --scratch "$SCRATCH/contracts-streak" --out docs/results/data/2026-10-13-contracts-streak  # B0 D C W V S T
uv run pytest tests/contracts tests/workspace -q                                              # unchanged suite passes
git -C /Users/vivekgade/Documents/lattice-contracts-base rev-parse HEAD                        # c0cdefc (W4)
git -C $ST --no-optional-locks rev-parse HEAD; git -C $ST --no-optional-locks status --short   # S1 after: identical
shasum -a 256 $ST/studio.toml; find $ST -newer "$SCRATCH/s1-marker" | wc -l                   # same sha; 0
```

**Edit-string counts at a0db033** (`grep -cF`; lines = occurrences for each):

| string (old) | count |
|---|---|
| `"home": (200, {"today": TODAY,` | 2 |
| `Step("GET", "/v1/home", "ann", expect_status=200,` | 2 |
| `"dismiss": (200, {"id": u(500), "habit_id": u(100)},` | 1 |
| `Step("POST", f"/v1/nudges/{u(500)}/dismiss", "ann", expect_status=200,` | 3 |
| `Step("POST", f"/v1/nudges/{u(n)}/dismiss", "ann", expect_status=200,` | 1 |
| `"expires_in": 900}` | 2 |
| `"password": "password-ann",` | 4 |
| `"next_cursor"` | 11 |
| `/v1/` | 144 |
| `Step("GET", "/v1/me", "ben", expect_status=200),` | 1 |
| `return {"id": u(n), "date": date, **more}` | 1 |
| `Step("GET", hp(101, "/progress"), "ben", expect_status=200,` | 1 |
| `"list_habits": (200, {"items": [{"id": u(100), "name": "walk"}],` | 1 |
| `Step("GET", "/v1/me", "ann", expect_status=429, expect_body=err("rate_limited")),` | 1 |
| `EMPTY_LIST = {"items": [], "next_cursor": None, "has_more": False}` | 1 |
| `scenarios=(*ACCESS, *SCENARIOS),` | 1 |
| `expect_status=201, expect_body={"email": "cara@example.com", "timezone": "UTC"},` | 1 |
| `habit(101, "b", user=2, start="2026-02-02")` | 1 |
| `return f"00000000-0000-4000-8000-{n:012d}"` | 1 |
| E5a, `grep -cE '"home": \(200, \{"today": TODAY,$'` | 1 |
| `403` (sanity: no 403 anywhere) | 0 |

**Pass for the experiment:**
- B0 as stated;
- D 13 of 13 (worst class and BROKEN set), with the full CHANGED set reported;
- C 6 of 6, W 4 of 4 and V 6 of 6;
- S1 identical and T1 within its threshold;
- every deterministic probe identical 10 of 10;
- `git diff 573aafc -- lattice/` empty at run time.

`$SCRATCH` is the session scratchpad.
