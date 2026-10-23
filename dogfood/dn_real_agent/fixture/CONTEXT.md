# Context: the Shelf service

An HTTP and JSON service (ASP.NET Core minimal APIs on .NET 10, `Microsoft.EntityFrameworkCore.Sqlite`) over a SQLite
file. The behaviour is `REQUIREMENTS.md` / `spec.py`. You build the service; you do not change the spec, the
requirements, the harness, the check script or this file.

* **Entry point.** `dnbuild.csproj` (already present: `Microsoft.NET.Sdk.Web`, `net10.0`, assembly `dnbuild`, nullable
  and implicit usings on, package `Microsoft.EntityFrameworkCore.Sqlite` 10.0.13). Write `Program.cs` (and further `.cs`
  files if you like) next to it. Do not add other packages: there is no network in the gate, only that package and what the
  SDK provides.
* **Environment the checker sets.** `PORT`: listen on `http://127.0.0.1:$PORT` (plaintext). `DB_PATH`: the SQLite file; the
  table `items(id integer primary key autoincrement, name text not null, qty integer not null)` with a unique index on
  `name` already exists (the harness created it): do not create or migrate tables (no `EnsureCreated`, no migrations).
  Use EF Core (`DbContext`, `UseSqlite`) with the connection string `Data Source=<DB_PATH>`; map the entity to the table
  `items`.
* **Readiness.** The checker waits for `GET /health` to answer 200 (any JSON body, for instance `{"ok": true}`).
* **Wire shapes.** JSON property names are exactly `id`, `name`, `qty`, `items` (lower case). Errors are JSON
  `{"detail": "<message>"}` with the statuses in `REQUIREMENTS.md`: 422 for an invalid body (empty or missing name,
  negative or missing quantity, a malformed body) and for a non-integer id in the path, 409 for a duplicate name, 404 for a
  missing item.
* **Gate.** `./check.sh` (`dotnet build`, then the Lattice check of `spec.py`, layer D). Exit 0 = done.
