Write a small multi-user HTTP JSON service in C# on ASP.NET Core (.NET 10 SDK) with EF Core over SQLite
(`Microsoft.EntityFrameworkCore.Sqlite`), in the module directory `./tests/spec/<your name>/` inside this directory (choose a short lowercase
name), plus `harness.py` and `studio.toml` exactly as `HARNESS_API_DOTNET.md` says (dotted harness module form), and `truth.json` exactly as
`TRUTH_SCHEMA_DOTNET.md` says. Minimums: 10 to 16 routes; a login; per-user data on at least two tables; at least one table whose rows
belong to a user through a parent row; at least one column that is never returned to a client; a status column with at least two
transitions; request validation; at least two users of different roles. The domain is your choice, but it must not be habits, tickets, runs,
jobs or notes. Organise the service the way a team maintaining a mid-sized production service would organise it. Do not copy an example from
any document.

When the code and truth are complete and every check you can run passes, write `FILES.txt` (every file of the module, one per line),
`freeze.sha256` at the module root, and `IDIOMS.md` (one line each: how routes are wired, how errors are reported, how authentication
is decided, where the schema lives). Work only inside this directory; do not read anything outside it.
