# Results: Go (Gin/GORM) importer front end, Spike A (Streak stage)

Pre-registration: `2026-10-15-go-frontend-prereg.md` (with Amendment 1). Branch `np-goextract`.
Data: `docs/results/data/2026-10-15-go-frontend/`. The held-out stage H is reported in section 5 below
after it is run (blind order fixed by the pre-registration).

## What was built
`lattice/gocheck/src/goextract*.go` (stdlib only, `main.go` gains an `extract` dispatch; every other existing
Go file byte-identical) and `lattice/importer/frontends/go_gin.py`, a `FrontEnd` that extracts a
`RepoModel` from a Gin/GORM app and reconciles each route against the running app. Deterministic, no LLM, no
writes to the target, anything unrecognised listed as `unread` with file:line.

## Result on Streak (the ground-truth stage): 21 of 22 gated probes pass; 13/13 mutants killed
Ground truth: Streak `a0679e9` (26 registrations, 24 under `/v1`, 0 spec-only) and `1d00ede` (20 routes, 6
spec-only). Observed: route recall and precision 26/26 and 20/20 (exact paths, no duplicates); the 6 spec-only
routes reported exactly; 26 distinct handlers with file:line 26/26; auth 21 Customer + 5 Public, 26/26 against
code and against the spec's anonymous column; 10 tables, 67/67 columns, 469/469 column facts, composite
uniques 2/2; every one of the spec's 54 error cells and all 65 (status, code) pairs found; 32/32 spec 422
field cells; owner evidence on 14/14 habit routes and 21/21 Member routes; the Nudge.delivery machine's four
states; 10 identical runs; the Streak tree unchanged after extract + reconcile; extract wall 20 ms. Tests:
`tests/gocheck tests/importer` 775 passed (739 before, +36); `go vet` and `gofmt` clean; ruff clean; existing
gocheck findings and importer goldens byte-identical.

## Findings (never tuned)
1. **PX fails on its control column only.** All 26 routes were observed present (21 x 401, `/health` 200, 4
   public 422), and the stale-binary probe PX2 caught a removed route. But the control request to an
   absent path (`<path>/zz-absent`) did not return 404 for 5 routes, because it matched another route's
   `:id` pattern (405 or 401). That is a flaw in the control's design, not in the extractor.
2. **PE2: 111 route x status x code triples with no spec cell** (predicted 25-45). The prediction did not
   define "extra"; narrower readings give 22 (handler raises only) and 72 (raises + guards). 19 are Member 429s.
3. **PS:** the generated profile has no ownership entries and no `unclear` entry for the `user_id` tables:
   no foreign key is invented (R-C), so ownership is simply absent rather than labelled `unclear`.
4. **PT2:** extract and build were faster than predicted (0.02 s; 0.2-1.8 s).
5. Unread on Streak: 4 items (a non-constant bound `period.MaxTarget(kind)`, two function-value calls, one
   interface dispatch); 0 of 26 routes structurally unread.
6. Known limits: auth stored via `context.WithValue` reads as Public; a route registered through a function
   value never named in a reachable body is `excluded` as unwired.

## Post hoc (found by the build node; first run kept in `first-run/`)
Owner-chain detection bug (PO 4/14 -> 14/14), guard `if` with an init statement (PF2 5/7 -> 7/7), router
depth counting, a stack overflow in the shared `FuncRef` on `x := x` (found by fuzzing 232 module-cache
modules: 18 of the first 150 died, 0 of 232 after), runner fixes. No expectation or ground truth changed.

## Orchestrator verification
Independent re-run: `tests/gocheck tests/importer` 775 passed; `go vet`, `gofmt -l`, `ruff check`, `ruff
format --check` clean; the probe runner at 3 runs reproduces every row above including the PX control miss;
no `lattice-gox-` container left; the Streak checkout's `git status` is unchanged (only the owner's
`studio.toml`). `go_gin.py` sha256 equals the builder's freeze hash `5dad5058...`.

## 5. Held-out stage H (run once by the orchestrator; report-only; never tuned)
Order, as pre-registered: (1) freeze: sha256 of `lattice/gocheck/src/*.go` and `go_gin.py` recorded
(`held-out/freeze.sha256`, combined 62e41b39aa2c6013..., at 27a20d8, 2026-10-16T03:27Z) before any held-out file
was opened by anyone in this graph; (2) a fresh agent (not the builder) wrote each app's ground truth from the
`.go` sources alone, hashed (`held-out/truth.sha256`) before the run; (3) the runner extracted each app once;
the freeze and truth hashes were re-verified immediately before. Apps: the job-scheduler Go port (multidb
00dd1de) and its five independently LLM-written variants.

| app | route recall / precision | table recall | column facts | auth vs spec rows | raise-status recall | structural unread | unread items | crash |
|---|---|---|---|---|---|---|---|---|
| job-scheduler-go | 1.00 / 1.00 | 1.00 | 0.979 | 1.00 | 1.00 | 0 | 1 | no |
| baseline | 1.00 / 1.00 | 1.00 | 0.979 | 1.00 | 1.00 | 0 | 0 | no |
| min-base-opus | 1.00 / 1.00 | 1.00 | 0.978 | 1.00 | 1.00 | 0 | 0 | no |
| min-base-sonnet | 1.00 / 1.00 | 1.00 | 0.978 | 1.00 | 1.00 | 0 | 0 | no |
| min-spec-opus | 1.00 / 1.00 | 1.00 | 0.976 | 1.00 | 1.00 | 0 | 0 | no |
| min-spec-sonnet | 1.00 / 1.00 | 1.00 | 0.978 | 1.00 | 1.00 | 0 | 1 | no |

All predicted ranges were met or exceeded (predicted route recall 0.80-1.00, precision 0.90-1.00, table
recall 0.85-1.00, column facts 0.85-1.00, auth 0.60-1.00, raise-status 0.40-0.95; observed 1.00 on routes,
tables, auth and raise-status). 0 crashes of 6. **The column-fact shortfall is entirely one convention:** all
31 differing facts (of ~1400) are `id` primary-key columns, which the extractor marks `nullable=false` (a
primary key is NOT NULL in the database) and the truth author, applying the pre-registered rule literally
(nullable only when the tag lacks `not null`), marked nullable. Excluding that convention, column facts are 1.00.
The literal rule in D3 therefore mis-describes primary keys; the extractor's reading is the schema's.
**What this does and does not show:** six apps of one author's spec and one framework pair (Gin + GORM),
produced by LLMs from the same requirements, so their idioms are more alike than five unrelated codebases;
`job-scheduler-go`'s own `unread` item and one more in a variant are listed in the output. It is not evidence
about other Go frameworks, about apps that store auth with `context.WithValue`, or about dynamic route tables.
H is not re-scored as blind after any later change to the extractor.
