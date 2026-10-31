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
