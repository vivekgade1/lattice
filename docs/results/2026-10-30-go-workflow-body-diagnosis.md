# Go workflow layer: why the J flows die at step 0 (node GD, 2026-10-30)

Development data only. The six job-scheduler Go apps (`~/Documents/job-scheduler-go`,
`-go-min-spec-{opus,sonnet}`, `-go-min-base-{opus,sonnet}`, `-go-baseline`) were dev data in
Spike B round 2 (`2026-10-16-workflow-layer-round2-results.md`, findings 1 and 2) and stay dev
data: nothing below backs a claim, and any number after a fix is a dev measurement.

## Method

* Each app copied with `git archive HEAD` into the session scratchpad (`gd/j/<app>`); the three
  harness apps got `go build -o bin/server ./cmd/server` in the copy (what their `check.sh` does).
  Nothing under `~/Documents/job-scheduler*` was written.
* The extractor and the workflow layer were run from a `git archive` of the starting tree
  (`8f97724`, GA2's commit): `GoGinFrontEnd().extract_json(root.parent, root.name, workflow=True)`
  for the routes, and `python -m lattice.cli import workflows <copy> --out <dir> --user root
  --user ann` (the round-2 J invocation of `dogfood/workflow_probes.py`, SQLite, no `--static`).

Reproduction on the starting tree (equal to the round-2 PJ rows):

| app | planned (static) | flows after the run | verified / unverified / failed | step-0 answer |
|---|---|---|---|---|
| `-go` | 80 | 7 | 2 / 0 / 5 | 422 on `POST /applications`, `POST /users`, body `{}` |
| `-min-spec-opus` | 74 | 7 | 2 / 0 / 5 | 422 on `POST /applications`, `POST /users`, body `{}` |
| `-min-spec-sonnet` | 15 | 5 | 2 / 0 / 3 | 401 `Not authenticated`, sent with no caller, body `{}` |

The 2 verified flows per app are `guard` flows that expect the refusal (round-2 finding 4).

## What the extractor reports for the body routes

| app | how the handler reads the body | body params extracted for `POST /applications` |
|---|---|---|
| `-go` | `var in struct{ Name *string \`json:"name"\` }; if !readJSON(c, &in) {return}`; `readJSON(c *gin.Context, v any) bool` does `raw, err := c.GetRawData(); ...; json.Unmarshal(raw, v)` | none |
| `-min-spec-opus` | `var in applicationIn; if !readJSON(c, &in)`; `readJSON(c, dst any)` does `raw, err := io.ReadAll(io.LimitReader(c.Request.Body, 1<<20)); ...; json.Unmarshal(raw, dst)` | none |
| `-min-spec-sonnet` | `var body applicationIn; if err := c.ShouldBindJSON(&body); err != nil` (tag `binding:"max=200"`, no `required`) | `name` (body, `required: false`) |
| `-min-base-opus` (no harness) | `bindJSON(c, v any)`: `c.GetRawData()` + `json.Unmarshal(raw, v)` | none |
| `-min-base-sonnet` (no harness) | `c.ShouldBindJSON(&req)` | `name` (`required: false`) |
| `-go-baseline` (no harness) | `stringFields(c, "email", "password")`: `json.NewDecoder(c.Request.Body).Decode(&raw)` into `map[string]json.RawMessage`, then each named key | none |

So "no body params" is not one cause; three separate gaps meet:

**GD-1 (defect, the reported finding): a body decoded by a module helper is not read.** The
param scan (`goextract_closure.go`, `scanParams`) recognises a bound struct only when the call is a
Gin context method (`c.ShouldBindJSON(&x)`, `bindMethods`) with the struct as its argument, in the
function being scanned. In `-go`, `-min-spec-opus` and `-min-base-opus` the handler passes `&in` to
a module helper whose parameter is `any`; inside the helper the body is decoded with
`json.Unmarshal(raw, v)` where `raw` comes from `c.GetRawData()` or `io.ReadAll(...c.Request.Body)`.
The scan of the handler sees an ordinary call; the scan of the helper sees no Gin bind method and,
even if it did, the destination there is an `any` parameter with no struct type. No body param is
reported, so the login route also has no username/password fields in the auth model. The same
shapes written directly in a handler (`json.NewDecoder(c.Request.Body).Decode(&in)`,
`json.Unmarshal(raw, &in)`) are missed for the same reason. The workflow taint
(`goextract_workflow.go`, `bindCall`) has the identical blind spot: a field of `in` carries no
`body:` origin after `readJSON(c, &in)`, so its comparisons and literals are not reported either.
Not the cause: a struct in another package or an anonymous struct (both are read when bound
through `c.ShouldBindJSON`: `rowTypeAny` / `anonStructOf`), `binding:"required"` tags (read),
`c.BindJSON` (in `bindMethods`).

**GD-2 (defect, hidden behind GD-1): a field the code refuses when it is absent reads as
optional.** `required` on a bound field comes only from a `binding`/`validate` `required` tag
(`bindStruct`). The J apps check presence in code instead: `in.Name == nil || ...` -> 422
(`-go`, `-min-spec-opus`, pointer fields) and `len(body.Name) < 1 || ...` -> 422
(`-min-spec-sonnet`, `-min-base-sonnet`, string fields). The workflow runner sends only the
required fields and the key field (`workflows.required_fields`), so even where the field is
extracted (`-min-spec-sonnet`) the body is `{}` and the 422 names no sent field, so the repair loop
(`workflow_run`, `error_field`) has nothing to vary and the flow fails. Fixing GD-1 alone would
therefore still leave `-go` and `-min-spec-opus` at step 0. The accessor reads (`c.GetQuery`,
`Body.Str`) already get `required` from a refusing `!ok` guard (`applyGuard`); bound struct fields
have no such reading.

**GD-3 (defect, the all-Public finding, a separate cause): authentication in the handler is not
read.** `-min-spec-sonnet` registers every route on the bare router (`r.POST(...)`, no group
middleware). Each handler starts with `user, ok := a.authenticate(c); if !ok {return}`, and
`authenticate` reads `c.GetHeader("Authorization")`, checks the `Bearer ` prefix and answers 401
when either is missing or the token does not decode. Route auth (`goextract_assemble.go`, D5) is
decided from the middleware chain only: no authenticating middleware means `Public`
(`"no authenticating middleware in the chain (...)"`). The runner then sends every step without a
caller, and each answers 401. The other five apps authenticate in a group middleware and read
`Customer`/`Admin` correctly. This is generic (any Gin app that authenticates per handler), not
specific to this app.

**GD-4 (gap, documented, not fixed here): keys named at a helper call.** `-go-baseline`'s
`stringFields(c, "email", "password")` decodes into a `map[string]json.RawMessage` and reads the
keys passed as variadic constants. That is a keyed accessor over a decoded map, not a bound
struct; it has no harness, so it does not affect the dynamic run. Left as a documented gap.

## Plan (step 2)

* GD-1: recognise a body decode by destination: a Gin bind method (as now), `json.Unmarshal(raw,
  dst)` with `raw` read from the request body (`c.GetRawData()`, `io.ReadAll` of an expression over
  `c.Request.Body`), `json.NewDecoder(<over c.Request.Body>).Decode(dst)`; and a module function
  that hands one of its parameters to such a decode is a binder: a call to it binds the struct its
  argument points to. Both in the param scan (default output: the added params) and in the
  workflow taint. A destination with no struct type is skipped, as an unread item only where the
  existing bind-method path already reports one, so no unread line is added to a module that
  already extracts cleanly.
* GD-2: in the workflow opt-in only (the default output's `required` is spec-facing and stays
  tag-only): a new workflow key listing the body fields whose absence the handler refuses with a
  4xx (`x == nil`, `x == ""`, `len(x)`/`utf8.RuneCountInString(x)` below 1, as a top-level
  disjunct of an `if` or a tagless `switch` case whose branch raises a 4xx), read by
  `workflows.py` as required.
* GD-3: a route with no authenticating middleware whose handler closure calls a function that
  reads the `Authorization` header and raises 401 is `Customer`, with the function named in the
  reason. Checked against the golden snapshot: any module of the 71 whose auth moves is reported.

Files: `goextract_closure.go` (the shared param scan; outside the GO lane's file list, needed
because the param scan lives there), `goextract_workflow.go`, `goextract_assemble.go` (route auth,
D5; also outside the GO lane's file list), `lattice/importer/workflows.py`. Not touched:
`go_rules.py` (G9) and the gRPC files (GA2).

## Step 2: fixes and results (same day, after the commit above)

Test-first: `tests/gocheck/fixtures/bodybind` (a hand-written Gin + GORM module, one route per
idiom) with `tests/gocheck/test_goextract_body.py` and `tests/importer/test_workflows_body.py`.
Red on the starting tree (11 of 15 failing; the 4 passing are the negative and invariance
cases), green after (16 of 16, after three more fixture cases from the J run: an error list
refused later, a nested check, a refusal helper called twice). `pytest tests/grpc tests/gocheck
tests/importer -q`: starting tree 1660 passed, 1 skipped (a `git archive` copy, so
`test_split5`'s base-commit test skips; GA2 recorded 1661 passed in the worktree); final tree
1677 passed, 0 failed, 0 skipped (1661 + 16 new).

* **GD-1 closed.** New `lattice/gocheck/src/goextract_body.go`: a body decode is recognised by
  destination (`json.Unmarshal` of bytes read from the body -- `c.GetRawData()`, `io.ReadAll` over
  `c.Request.Body`, or a local whose every assignment is one, `raw = bytes.TrimSpace(raw)` kept --
  and `json.NewDecoder(<over c.Request.Body>).Decode`), and a module function that hands one of
  its parameters to such a decode or to a Gin bind method is a binder (through other binders
  too). `scanParams` (`goextract_closure.go`) binds the struct a binder's argument or a decode's
  destination points to, by the same `bindStruct` as `c.ShouldBindJSON`; a destination with no
  struct type is skipped with no unread line. The workflow taint (`bindCall`) binds the same way,
  so the fields carry their `body:` origins into `param_uses`/`param_literals`. With the body
  params present, the fixture's plain-password login route is recognised and the auth model names
  its credential fields (on the J apps no login route is recognised, before or after; not
  investigated here).
* **GD-2 closed, in the workflow opt-in only.** New workflow key `param_required` (route, param,
  source, how = nil | empty | length, file, line): a body field tested for absence (`x == nil`,
  `x == ""`, `len(x)`/`utf8.RuneCountInString(x)` refusing 0) in a top-level disjunct of an `if`
  or of a tagless `switch` case of the handler whose branch raises a 4xx; or appended to a local
  list that a later top-level `if len(list) > 0` refuses with a 4xx. A branch refuses when a 4xx
  raise of the route's closure sits in it, or when it calls a module function all of whose raises
  are 4xx (a helper called twice with the same arguments has its raise located at the first call
  only). `workflows.AppView` reads these fields as required, so `required_fields` sends them.
  The default output's `required` stays tag-only. Not read: a check nested under another condition
  (`if body.IsCron { if body.CronExpr == nil ...}` is required only under the condition), a
  conjunction (`in.Color != nil && ...`), checks inside a helper the handler calls
  (`-min-spec-opus` `a.validateJobConfig(in)`), and validity predicates that also refuse the zero
  value (`!isValidEmail(body.Email)` on `-min-spec-sonnet`).
* **GD-3 closed.** `goextract_assemble.go`: a route with no authenticating (or admin) middleware
  whose handler closure contains a function that reads the `Authorization` header
  (`c.GetHeader`/`c.Request.Header.Get`) and raises 401 is `Customer` (member), the reason naming
  that function. The auth model (`buildAuth`) still needs an authenticating middleware; an app that
  authenticates only per handler gets route auth but no auth model.
* **GD-4 not changed** (documented above).

Byte-compatibility (the starting tree `8f97724` against the final tree):

* Raw `gocheck extract` (default output) of every `go.mod` module in the repository (78, the 71
  of the golden list plus `lattice/gocheck/src`, `lattice/grpcbridge` and the five GA2 fixtures):
  byte-identical, 0 of 78 changed. No module in the repository decodes a body outside a Gin bind
  method or authenticates per handler, so the allowed difference (added body params) does not
  occur in it; it occurs only in the new fixture and the J apps.
* G9's golden snapshot (`snap.py`, 71 modules): `diff -r` empty; the fresh baseline on the
  starting tree also equals GA2's `ga2_final`.
* Workflow opt-in output of the same 78 modules: every module differs only by the new
  `param_required` key (empty in 74; non-empty in the `POST /api/batch` route of the
  `goextract_workflow` fixture and its two copies -- `len(req.Items) == 0` -> 422 -- and four
  `shelfie` fields whose `strings.TrimSpace(x) == ""` refusal already had a `required` tag).
  One run also showed a reordered `status_facts[].from` list on `tests/gocheck/fixtures/machine`:
  that is pre-existing nondeterminism (the starting tree's binary gives `["queued","succeeded"]` 7
  times and `["succeeded","queued"]` once in 8 runs), not from this change.

The J apps (dev data, report only, no claim; copies, SQLite, `--user root --user ann`):

| app | planned before -> after | flows before -> after | verified / unverified / failed before -> after | flows dying at step 0 before -> after |
|---|---|---|---|---|
| `-go` | 80 -> 80 | 7 -> 41 | 2/0/5 -> 16/0/25 | 5 -> 0 |
| `-min-spec-opus` | 74 -> 74 | 7 -> 26 | 2/0/5 -> 19/0/7 | 5 -> 0 |
| `-min-spec-sonnet` | 15 -> 64 | 5 -> 19 | 2/0/3 -> 9/0/10 | 3 -> 3 |

Before, every verified flow was a `guard` flow expecting the refusal; after, the verified flows
are `pair`, `owner_guard`, `effect` and `lifecycle` flows (`-go` 4/5/5/1, `-min-spec-opus`
5/6/6/1, `-min-spec-sonnet` 3/0/2/0, plus guards). `-min-spec-sonnet` now plans 64 flows (its
routes are no longer Public). What still fails: unbound `user_id`/`job_id` path params with no
producer (round-2 finding 3, unchanged), job-config creation refused on values the extractor does
not model (`invalid command`, `code_path`, opus's validation helper), and on `-min-spec-sonnet`
`POST /users` (`!isValidEmail(body.Email)`: a validity predicate, not an absence test, so `email`
is not sent) and `POST /worker/schedule-tick` (`now` given a probe value). The `JobRun` machine is
still `static` on all three (no run is created). These are development numbers on apps whose
failures were diagnosed by reading them.
