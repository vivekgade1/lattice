# Ground-truth notes (authored from .go source + go.mod only)

Conventions applied literally, as instructed:
- nullable = tag lacks `not null`. This makes every primary key `id` nullable=true (no app tags `id` with `not null`), and every column of a model whose tags carry no `not null` nullable=true even where raw DDL or the Go type implies otherwise. Pointer-ness ignored.
- unique = a uniqueIndex/unique tag that covers only that column. Composite uniqueIndex (same name on several columns) gives unique=false, index=true for each member column. Primary keys have unique=false (no unique tag).
- index = any `index` / `uniqueIndex` tag, including composite members. `check:` constraints, partial unique indexes and indexes created by raw SQL (db.Exec) are NOT counted as index.
- association fields (User, Application, JobConfig, Creator, Triggerer, ...) are relations, not columns; excluded.
- types: Go int/uint/int64 -> Int; string -> Str; bool -> Bool; time.Time/*time.Time -> DateTime. `type:varchar(N)` and `size:N` both give max_length N. Only explicit `default:` tags give default (only min-base-opus has any).
- routes: path params as {}; /health included (registered in app.go in all six); PATCH routes that only answer 405 are still registered with Gin, so included; NoRoute/NoMethod are not routes.

## job-scheduler-go
- Extra column job_runs.scheduled_for exists beyond a spec; included. Guard columns active_guard/live_guard/cron_minute_guard are added only by raw ALTER TABLE on MySQL and are not in the models: excluded.
- Three partial unique indexes via db.Exec are excluded from index flags.
- Table columns where a composite uniqueIndex plus plain `index` appear (memberships.user_id, application_id): index=true, unique=false.
- A dynamic Dialector (sqlite/mysql/postgres) is chosen at runtime; truth is the model, not dialect DDL.

## job-scheduler-go-baseline
- job_runs.trigger is varchar(10) here (20 in job-scheduler-go). Group("/", mw) with "/users/me" etc. resolves to "/users/me" (no double slash); I assumed Gin's joinPaths normalises this.
- PATCH /applications/{}/jobs/{} is registered on the root router (405 handler) -> included.
- Raw-SQL indexes excluded.

## job-scheduler-go-min-base-opus
- Table job_runs: a JobRun struct with TableName exists but the table is actually created by raw CREATE TABLE (not AutoMigrate), where all columns are NOT NULL, indexed, with CHECKs. Per the instruction (gorm tag only) its columns are all nullable=true/index=false. UNSURE whether the extractor/grader should honour the raw DDL; I followed the tag rule.
- Table cron_ticks exists only as raw SQL (no Go struct): NOT included in tables. UNSURE if a grader would expect it.
- Defaults present (`default:false`/`default:true`) recorded as text "false"/"true".
- Routes are registered via helper methods taking a *gin.RouterGroup (jobRoutes, runRoutes, workerRoutes, schedulerRoutes) called with the root-level group "" -> full paths without prefix. No GET /applications/{}/members/{user_id} here. Handlers pass param name :app (still {}).
- users.email / applications.name: no size/type tag except applications.name size:100 and job_configs sizes; email has no max_length.

## job-scheduler-go-min-base-sonnet
- No TableName methods and no column: tags; names derived by GORM defaults (JobConfig -> job_configs, etc.). Assumed default NamingStrategy (none is configured).
- created_at columns have no `not null` -> nullable=true (job_configs, job_runs). No autoCreateTime tag; default none.
- PATCH 405 route registered on root router, included. Group("/") used.

## job-scheduler-go-min-spec-opus
- CronTick model (table cron_ticks, default naming) is migrated via AutoMigrate: included (id, job_config_id, minute). `minute` and `job_config_id` get index=true from composite uniqueIndex; unique=false.
- check: tags ignored. Partial unique indexes via db.Exec ignored.
- Extra route GET /applications/{}/runs (list runs) exists only in this app.
- No PATCH route. No GET member route.

## job-scheduler-go-min-spec-sonnet
- No column: tags; default naming. TableName methods present for all but they match defaults.
- PATCH 405 route registered; no auth group (middleware presumably applied elsewhere); routes registered flat in routes.go.
- created_at on job_configs/job_runs has no `not null` -> nullable=true.
