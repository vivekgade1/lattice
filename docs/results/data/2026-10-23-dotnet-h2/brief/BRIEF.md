# Your task

Design and build a back-end product of your own invention: a web service, spoken to over HTTP with JSON, that many people
use at once. Each person signs in and works with records that are theirs alone. Use C# on ASP.NET Core with the .NET 10
SDK (10.0.401), and keep the data with EF Core in SQLite through the package `Microsoft.EntityFrameworkCore.Sqlite`.

## The product

Invent it. These subjects are taken and may not be used: habits, streaks, check-ins or reminders; tickets; runs; jobs;
notes; expenses, expense reports, reimbursements or payouts. Choose a subject that has real rules in it.

Whatever you invent must include all of the following:

- 10 to 16 HTTP routes in total, among them one where a person signs in with a name and a password;
- seeded people with at least two different roles, and at least one route that only one of those roles may use;
- at least two kinds of record that belong to the person who is signed in, so that no one can see or change somebody
  else's;
- at least one kind of record that belongs to a person only by way of a record it sits under;
- at least one stored value that the service keeps but never sends back in any answer;
- one record with a lifecycle: a state that changes at least twice over its life, where some changes are refused in
  some states;
- checking of what clients send, so that a request with a missing value or a value out of range is turned away.

## The code is yours

How the code is structured, and how each requirement above is met, are your decisions alone. Decide as you would for
a real product that is going to keep growing. The two documents beside this file describe only the environment your
service runs in (`HARNESS_API_DOTNET.md`) and the answer key you write (`TRUTH_SCHEMA_DOTNET.md`); neither one is a
design to imitate, and no example in them is a pattern to follow.

## Where things go

- OUT, as `HARNESS_API_DOTNET.md` uses the word, is this directory (`.`). SCRATCH is `./scratch`. Keep every build, log,
  package cache and helper script under `./scratch`.
- Hand back the service, its harness and `studio.toml` exactly as `HARNESS_API_DOTNET.md` lays them out, and a
  `truth.json` in the module directory written from your finished code exactly as `TRUTH_SCHEMA_DOTNET.md` lays it out,
  with a `file:line` for every fact.
- Run the checks of section 12 of `HARNESS_API_DOTNET.md` until every line reads `PASS` and the last reads
  `ALL CHECKS PASSED`.
- Then add three files to the module directory: `FILES.txt` (every file of the module, one path per line),
  `freeze.sha256` (the sha256 of every file named in `FILES.txt`), and `IDIOMS.md` with four lines saying, for your
  service: how a request reaches your code, how failures are answered, how sign-in and permission are decided, and where
  the database schema comes from.

## Ground rules

- Touch nothing outside this directory: do not read, list, search or write any path above it. Running the toolchain
  (`dotnet`, `python3`, ordinary shell utilities) is fine. No web browsing.
- Nobody will answer questions while you work; make each decision yourself.
- Your spending is capped. Get the service working first, then the answer key, then the three closing files.
