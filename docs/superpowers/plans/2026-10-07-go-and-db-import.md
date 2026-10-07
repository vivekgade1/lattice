# Plan: import for Go (Gin + GORM) and the popular databases (Python and Go)

> **Superseded in structure by `2026-10-07-module-marketplace.md`.** Its nodes become
> modules there:
> - ENG becomes M0 plus the engine pilots;
> - GOX, GOR, VEN and SQLX become the gin + gorm and sqlx modules in M1.
>
> The design notes below stay valid as the content of those modules.

> Requested by the user on 2026-10-07: "Add gorm, gin and most popular db connected in
> python and golang". Branch `roadmap`. Execution starts after experiment D finishes,
> as an `epg` graph (see [[use-epg-for-plans]]).
>
> **Lessons carried over from Phase 2** (results log, "Phase 2 conclusion"):
> - Import is a **rules-first assist**. It does not convert an app.
> - **"Verified" must be earned by a decisive probe** (`check_verdicts`).
> - Held-out apps are vendored only after the freeze, by a fresh agent, and run once.
> - The gate is **soundness**: the precision of "verified". Coverage is **reported**.

## Scope

### Tier 1 (this plan)
| area | Python (today) | Python (add) | Go (add) |
|---|---|---|---|
| web framework | FastAPI, Django + DRF, Flask | (none) | **Gin**, **net/http** (Go 1.22 `ServeMux` patterns), **chi** |
| ORM / DB layer | SQLModel, SQLAlchemy (sync), Django ORM | **SQLAlchemy async** (`AsyncSession`) | **GORM**, **sqlx** / `database/sql` (raw SQL) |
| database engine (probing and invariants) | SQLite only | **PostgreSQL**, **MySQL** | SQLite, **PostgreSQL**, **MySQL** |
| auth | OAuth2 form, JSON, Basic, token, session | (none) | JWT middleware (`golang-jwt`), Gin `c.Set` / context keys, Basic |

### Tier 2 (planned next, not built here)
- Go: Echo, Fiber, ent, sqlc, pgx raw.
- Python: Tortoise ORM, Peewee, raw psycopg / asyncpg.

The tier split is a default. Node `POP` checks it with a quick popularity scan (GitHub
stars and downloads, as of 2026-10) and adjusts it before any build.

## Design

### Go front end (`lattice/importer/frontends/golang/`)
- **A Go helper program** (`goextract`, built with `go/packages` + `go/types`). It runs
  on the target module and prints the AppModel as JSON. The Python side wraps it as a
  `FrontEnd`, like the others.
- **GORM models:**
  - struct fields;
  - `gorm` tags: `primaryKey`, `unique`, `uniqueIndex`, `not null`, `size`, `default`,
    `check`, `foreignKey`, `references`, `many2many`;
  - embedded `gorm.Model`;
  - `TableName()` methods;
  - the relations `belongs to`, `has many` and `many2many`, with join tables.
- **Request models:**
  - `binding:"required,min=…,max=…,oneof=…,email,gt=0"` tags (go-playground/validator)
    become bounds and one_of;
  - `json:"…"` gives the wire names.
- **Routes:**
  - Gin `r.GET/POST/...`, `Group(...)` prefixes and `Use(...)` middleware chains;
  - net/http `mux.HandleFunc("GET /items/{id}", …)`;
  - chi `r.Route` / `r.With`.
  - Handlers are followed through method values and closures.
- **Guards:**
  - auth middleware: a JWT parse followed by `c.Set("userID", …)` and `c.Abort*`;
  - role checks inside handlers or middleware: `if role != "admin" { c.AbortWithStatus(403) }`.
- **Owner evidence:**
  - `db.Where("user_id = ?", uid)`, `Where(&T{UserID: uid})`, `First(&x, id)` followed
    by `x.UserID != uid`;
  - on create, `x.UserID = uid`;
  - in sqlx, the SQL string's `WHERE user_id = $1`, parsed with `sqlglot`.
- **Error sites:** `c.JSON(4xx, …)`, `c.AbortWithStatusJSON`, `http.Error`, `ErrRecordNotFound`
  mapped to a status.
- **`unread` routes:** any handler path the helper can't follow (interface dispatch,
  reflection) is marked `unread`, so the outside-API veto and the R-item verdicts stay
  sound.

### Go runtime (`transport.py` + `frontends/golang/runtime.py`)
- **Running the app:**
  - `go build` the app once, with a small Lattice-owned `main` wrapper per vendored app
    that reads `LATTICE_DB_DSN` and `PORT`;
  - start it as a subprocess on a free port;
  - add a new `ProcessHttpTransport`, so the app is probed over real HTTP on localhost;
  - dispose by killing the process.
- **Isolating each probe's database:**
  - SQLite: copy the file;
  - PostgreSQL: `CREATE DATABASE … TEMPLATE seed`, which is fast;
  - MySQL: restore the seed schema into a fresh database per case. This is slower, so
    MySQL probing is batched.
- **Seeding:** through the live schema, inspected after the app's own migrations run
  (GORM `AutoMigrate` or migration files), never from guessed DDL.

### Database engines for every language (`lattice/importer/dbengine.py`)
- **An engine abstraction** for seeding, snapshots, diffs, per-case clones and invariant
  SQL. Its dialects are SQLite, PostgreSQL and MySQL.
- **Layer C:** the invariant compiler (`lattice/spec/invariants.py`) gains PostgreSQL
  and MySQL output. Tests check the same violating rows on all three engines.
- **Python apps on PostgreSQL/MySQL:** SQLAlchemy and Django settings overrides point at
  the engine. Layer C and probes then run on the real engine, not SQLite.
- **Provisioning:**
  - PostgreSQL comes from a pip-bundled server (`pgserver` or `postgresql-wheel`), so it
    needs no Docker.
  - MySQL needs **Docker running**: Docker 29 is installed, but its daemon is down. The
    tests skip MySQL with a clear message when Docker isn't available.

## Gate (pre-stated)

**Soundness.** On dev apps and on held-out apps, a manual review of all verified R1, R2
and T1 items, plus 10 random others per app, finds ≥ 95% right. The internal
`check_verdicts` never fires. There is no crash on any app.

**Reported, not gated:**
- tables and routes verified;
- proposals and their precision;
- import time.

**No regression:**
- the full suite passes;
- the FastAPI goldens are byte-identical;
- the Phase 2 dev-app gate still holds;
- experiment D's frozen checkout is untouched.

## Graph (for `epg`)

| id | goal | depends_on | model | passing criteria | on fail |
|---|---|---|---|---|---|
| POP | Popularity scan; confirm the tier split; propose 2 dev + 2 held-out apps per stack (Gin + GORM, net/http or chi + sqlx, a Python app on Postgres), with licences checked | none | haiku (search + extraction) | a table with sources; apps with MIT/BSD/Apache licences, runnable with no outside service | re-route to sonnet |
| ENG | The engine abstraction, PostgreSQL via pip-bundled binaries, MySQL via Docker (skips when absent), invariant compiler dialects | none | sonnet | the invariant tests give identical violating rows on SQLite and PostgreSQL, and on MySQL when Docker is up; existing tests are green | retry ×2, then escalate |
| GOX | `goextract`: GORM, Gin, net/http, chi, validator tags, guards, owner evidence, error sites, `unread` | POP (dev apps only) | opus (the analysis design is the hard part) | expected-fact tests on 2 Go toy apps and the Go dev apps | retry ×2 |
| GOR | The Go runtime: build wrapper, `ProcessHttpTransport`, per-case DB clones for all 3 engines, `AuthDriver` for JWT | ENG | sonnet | a toy Gin + GORM app boots, seeds, logs in each role and answers, on SQLite and PostgreSQL | retry ×2 |
| PYDB | Python on PostgreSQL/MySQL: SQLAlchemy (sync and async) and Django overrides, layer C and probes on the real engine | ENG | sonnet | recipes and flasky pass their dev gate on PostgreSQL with the same verdicts as on SQLite | retry ×2 |
| VEN | Vendor the Go dev apps, with the licence, provenance, wrapper `main`, `seed.go` or SQL, and fidelity scenarios | POP | sonnet | each app boots, seeds, logs in each role and replays fidelity | retry ×2 |
| SQLX | The raw-SQL front end for sqlx / `database/sql` (SQL strings parsed with `sqlglot`, schema from migrations or the live DB) | GOX | sonnet | expected facts on the sqlx dev app | retry ×2 |
| INT | Pipeline integration: auto-detect Go, rules-only import, report, review CLI; the dev gate for the soundness criterion | GOX, GOR, PYDB, VEN, SQLX | opus | the soundness gate holds on every dev app; the full suite is green; goldens byte-identical | fix + rerun ×2, then escalate |
| FRZ | Freeze, record the freeze commit; a fresh agent vendors the held-out apps and runs once | INT | opus (fresh agent) | `RESULTS.md` with the frozen numbers and the manual review | — |
| SYN | Results-log entry, memory, report to the user | FRZ | opus (inline) | committed | — |

**Levels:**
- Level 0, in parallel: POP, ENG
- Level 1, in parallel: GOX, GOR, PYDB, VEN
- Level 2: SQLX
- Level 3: INT
- Level 4: FRZ
- Level 5: SYN

## Decisions for the user

1. **MySQL needs Docker running.** Should PostgreSQL be the default engine, with MySQL
   tests skipped when Docker is down?
2. **Tier 1 scope as listed**, or add Echo/Fiber/ent now?
3. **The gate is soundness**, with coverage reported, not a 90% coverage gate. This
   follows the pivot decision.

## Effort and cost

- **No model calls in the build:** the agents are Claude Code agents, not experiment runs.
- **Estimated build:** 3 waves of parallel agents, about the size of Phase 2's W1–W3.
- **CPU:** the held-out run is CPU-only.
- **Experiment D:** the plan starts after D finishes, so D's timing isn't disturbed.
