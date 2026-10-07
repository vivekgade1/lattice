# Plan: Lattice modules and a module marketplace

> Requested by the user on 2026-10-07. MySQL, SQL and ClickHouse connectors, Kafka,
> Redis, numpy, pandas, scikit-learn, Spark, and anything custom should be importable
> from a project and understood by Lattice. Adding a new one should be easy, through a
> separate marketplace, and Lattice should be able to extend itself.
>
> Status: **design, for the user's review**. It runs as an `epg` graph once approved. It
> **replaces** the ENG node of `2026-10-07-go-and-db-import.md`: PostgreSQL and MySQL
> become the first engine modules, not core code.

## 1. The problem today

Each new library means editing the core:
- a front end in `lattice/importer/frontends/`;
- natives in profiles;
- target capabilities in `lattice/targets.py` / `p8_target.py`;
- seeding in `suite.py`;
- SQLite-only snapshots in `probes.py`.

Phase 2 showed what that costs. Each app's library choices (webargs bodies, a JWT
library, relationship writes) were gaps the core had to grow into.

**Goal:** a **module** is a versioned package that teaches Lattice one library or
service, end to end. The core only defines the extension points and the soundness rules.

## 2. What "Lattice understands X" means, per module kind

| kind | examples | import (reading the user's code) | spec (what can be stated) | checker | runtime under test |
|---|---|---|---|---|---|
| **engine** (SQL stores) | MySQL, PostgreSQL, ClickHouse, generic DB-API/ODBC SQL | recognise drivers and ORMs (PyMySQL, mysqlclient, clickhouse-connect, asyncpg, go-sql-driver/mysql, clickhouse-go, GORM dialects); read the live schema | tables, keys, bounds, invariants in that dialect (`Invariant` → SQL); ClickHouse's append-mostly tables and no-UPDATE semantics, stated as engine capabilities | layer C compiles invariants to the dialect; R and T probes snapshot and diff the real engine | provision a server (pip-bundled or a container); clone per case (template DB, schema restore, or a ClickHouse `CREATE TABLE … AS`) |
| **resource** (state outside SQL) | Redis, Kafka, (later) S3, queues | recognise clients (redis-py, go-redis, confluent-kafka, kafka-go, sarama) and the keys, topics and messages a route touches | a **resource view**: Redis as virtual tables (`kv(key, value, ttl)`, sets and hashes as relations), Kafka as an append-only `topic(name, key, value, offset)`; message schemas; effects: "publishes", "caches", "expires" | invariants and scenarios over the views (e.g. "a refused request publishes nothing", "the cache equals the DB row"); atomicity across DB and Kafka is checked, not assumed | fakes first (fakeredis, an in-memory Kafka log); real servers through containers when available; snapshot and diff of each view |
| **compute** (pure libraries) | numpy, pandas, scikit-learn, Spark | recognise calls and data flow (DataFrame in, DataFrame out; `fit` / `predict`); mark the code it doesn't model as `unread` | **contracts, not semantics**: column schemas and dtypes, shapes, value ranges, row-count relations ("output rows ≤ input rows"), determinism with a seed, no NaN in a stated column | property checks on real executions: generated inputs within the schema, then each contract checked on the output; layer A flags known pitfalls (chained assignment, leakage from fitting on test data) | run the library itself (local Spark mode for Spark); no fakes, since the computation is the thing tested |
| **framework** (web front ends) | FastAPI, Django + DRF, Flask (existing); Gin, net/http, chi (planned) | today's `FrontEnd` protocol, moved into modules | contracts (R items), guards, owner scope | layer R probes | today's `AppRuntime` / transports |
| **custom** | an in-house client library, a company auth wrapper | a manifest that declares call patterns and their meaning ("`acme_auth.require(role)` is a role guard") | the same vocabulary as the kinds above | the same checks | the module's own fake, or a recorder |

**The honest limit.** Lattice never claims to *understand* what a compute library
computes. It checks **stated contracts** on real runs. Anything a module doesn't model
stays `unread` or `unchecked`, so soundness doesn't depend on module coverage.

## 3. Module anatomy

A module is a pip package (with an optional Go module for Go-side recognisers).

```
lattice-mod-redis/
  lattice-module.toml        # the manifest
  recognizers/python.py      # call-pattern recognisers (AST), plugged into the front ends
  recognizers/go/            # Go analysis, plugged into goextract
  vocabulary.py              # resource views, spec types, effect kinds
  provision.py               # fakes/containers: start, seed, snapshot, diff, clone, stop
  natives/                   # typed natives for Lattice programs, one per target, or gated
  checks.py                  # optional layer-A rules
  guide.md                   # text the LLM sees when drafting specs and code that use it
  conformance/               # fixtures the conformance kit runs
```

The manifest:

```toml
[module]
name = "redis"
version = "0.3.0"
kinds = ["resource"]
lattice_api = ">=1.0,<2"            # SDK compatibility range
languages = ["python", "go"]

[recognize.python]
imports = ["redis", "redis.asyncio"]
[recognize.go]
imports = ["github.com/redis/go-redis/v9"]

[provision]
default = "fake"                    # fakeredis; "container" needs Docker
container = "redis:7"

[targets]                           # language rules: implemented or gated, per target
python = "supported"
go = "gated: L894 natives not built for Go yet"
ts = "n/a"
```

## 4. Core changes: the module SDK (`lattice.sdk`, versioned)

The extension points, each a small protocol:

| extension point | today | becomes |
|---|---|---|
| `Recognizer` | logic hard-coded in each front end | front ends call every installed recogniser on each call site; a hit yields typed facts (guards, owner evidence, table and resource touches, `unread`) |
| `FrontEnd` | `frontends/__init__.register` | registered by framework modules |
| `Engine` | SQLite paths in `suite.py` / `probes.py` / `invariants.py` | a dialect with provision, clone, snapshot, diff, introspection and invariant SQL |
| `Resource` | none | a view schema, plus provision, snapshot and diff; views join tables in layers C and D and in probe diffs |
| `Contract` vocabulary | fixed in `lattice/spec/model.py` | modules add spec types and effects, validated by `lattice.spec.validate` through module hooks |
| `Native` | `NativeSig` in profiles | modules ship natives with per-target implementations; P8 gates the missing ones (language rules R4) |
| `Check` | the pycheck codes | modules add layer-A codes in a namespaced range (`M<module>:<n>`) |
| `Guide` | prompts in `sdraft` / `prompts` | module guides go into draft and code prompts for the libraries a ticket touches |

**Discovery:** Python entry points (`[project.entry-points."lattice.modules"]`).
`lattice modules list` shows what is installed, the version, the SDK range and trust.

**The language rules extend to modules:**
- every module construct states its per-target support;
- unsupported targets get an L89x gate;
- each module keeps its own decision log (`MODULE.md`), and the core test enforces that
  it exists.

**The soundness rules extend to modules:**
- a recogniser may only add facts, and must mark what it can't resolve as `unread`;
- `check_verdicts` applies to every item a module contributes.

## 5. The marketplace

- **The index** is a git repository (`lattice-modules-index`). Each module has a
  metadata file: name, versions, SDK range, kinds, languages, trust tier, package
  coordinates, artefact hashes and conformance results.
- **The CLI:**
  - `lattice module search <term>` and `lattice module info <name>`;
  - `lattice module install <name>[@ver]`: a pip (and `go get`) install, pinned by hash,
    then registration;
  - `lattice module new <name> --kind resource --from <library>`: scaffolds a module (see §6);
  - `lattice module test <path>`: runs the conformance kit;
  - `lattice module publish`: opens a pull request to the index, with conformance results attached.
- **Trust tiers:**

  | tier | requirements | how it runs |
  |---|---|---|
  | **core** | maintained in this repo | in-process |
  | **verified** | passes conformance and a human review; signed | in-process |
  | **community** | passes conformance; unreviewed | its recognisers and provisioners run **in a subprocess sandbox** with no network; the report labels its verdicts "community module" |

- **Security:** modules execute code inside the checker, so installs are hash-pinned,
  verified modules are signed, and community modules are sandboxed. A module can never
  touch `.env` or credentials; the SDK passes it only what it declares.

## 6. "Lattice updates itself": drafting modules with an LLM

`lattice module new redis --from redis` drafts a module from a library:
1. It reads the library's public API (introspection, type stubs, docs).
2. It drafts the recognisers, vocabulary, provisioner and guide, through the CLI (no API
   key).
3. It generates conformance fixtures: toy apps that use the library correctly, plus
   mutants that use it wrongly.
4. It runs the conformance kit.
5. It repairs on failures, with bounded rounds.
6. It stops for a **human review** before the module can go above community.

**The conformance kit (the gate):**
- every recogniser fact on the fixtures matches the fixture's declared facts;
- every mutant is refuted by the expected item;
- no "verified" item appears without a decisive probe;
- provisioning is isolated (no state leaks between cases).

A drafted module that fails stays a draft. Lattice "updates itself" only through modules
that pass.

## 7. Pilot modules: one per kind, to prove the SDK

| pilot | kind | why it proves the kind |
|---|---|---|
| **mysql** | engine | a second SQL dialect; a container or local server; per-case clones without template DBs |
| **clickhouse** | engine | a different storage model (append-mostly, no UPDATE, async mutations), which forces engine *capabilities* into the SDK |
| **redis** | resource | non-SQL state, TTLs, fakes vs real servers |
| **kafka** | resource | an append-only effect plus cross-store atomicity ("a refused request publishes nothing") |
| **pandas** (+ numpy) | compute | contracts on real runs: DataFrame schemas, property generation |
| **gin + gorm** | framework + engine (Go) | the Go side of the SDK: recognisers in Go |
| **custom example** | custom | an in-house auth wrapper declared only by a manifest, with no code |

scikit-learn and Spark follow pandas, once the compute contract vocabulary is proven.
Spark runs in local mode; the contracts are on its input and output tables.

## 8. Phases and exit criteria

| phase | content | exit (objective) |
|---|---|---|
| **M0: SDK extraction** | define `lattice.sdk`; move SQLite, FastAPI, Django + DRF and Flask into **core modules** behind the extension points, with no behaviour change | FastAPI goldens byte-identical; dev-app gate unchanged; Phase 0 / Phase 1 / pivot tests green; `lattice modules list` shows the core modules |
| **M1: engine and framework pilots** | mysql, clickhouse, postgres; gin + gorm (absorbs the Go/DB plan's GOX/GOR/VEN/SQLX nodes) | conformance passes; the invariant suite gives identical violating rows across engines (ClickHouse within its stated capabilities); Go dev apps pass the soundness gate |
| **M2: resource pilots** | redis, kafka, with resource views in layers C/D and probe diffs | conformance passes; a cross-store atomicity mutant ("publish before a failing DB write") is refuted |
| **M3: compute pilots** | pandas + numpy, then scikit-learn | conformance passes; contract mutants (dropped column, dtype change, data leakage from fitting on test data) refuted; no false findings on correct fixtures |
| **M4: marketplace** | index repo, CLI, trust tiers, sandbox, signing | install, test and publish work end to end on a community module; the sandbox blocks network and file access outside the module's declared paths |
| **M5: self-extension** | `lattice module new --from` | on 3 libraries not seen during the build (held out, as in Phase 2), drafted modules pass conformance after ≤ 3 repair rounds, and a human review finds no unsound recogniser fact |

**The gate throughout:** soundness first. The precision of "verified" stays ≥ 95% on
review. Coverage is reported, not gated.

## 9. Effect on pending work

- **Experiment D:** unaffected; it is running from a frozen checkout.
- **The Go/DB plan** (`2026-10-07-go-and-db-import.md`): restructured.
  - Its **ENG** node becomes M0 + the engine pilots.
  - **GOX/GOR/VEN/SQLX** become the gin + gorm and sqlx modules in M1.
  - Its soundness gate and held-out discipline carry over unchanged.
- **Order:** M0 first. Building pilots on today's hard-coded core would mean writing them twice.

## 10. Decisions for the user

1. **The marketplace's home:** a separate git repo for the index (recommended), with
   modules as ordinary pip/Go packages.
2. **Trust:** sandbox community modules, with in-process only for core and verified
   (recommended).
3. **Containers:** MySQL, ClickHouse, Kafka and real Redis need Docker running.
   - Fakes and pip-bundled servers cover PostgreSQL and Redis without Docker.
   - Is it OK to require Docker for those pilots, and to skip them cleanly when it's down?
4. **Order:** M0 → M1 → M2 → M3, with M4/M5 last (recommended)? Or the marketplace (M4) earlier?
