# Brief

You work alone, inside one directory, and you hand back files. Your scratch directory is

    /Users/vivekgade/Documents/g3-s4-author

Read, list and write only inside it (the Go toolchain and the Go module proxy are the two exceptions, see
`HARNESS_API.md`). `HARNESS_API.md` is in that directory beside this file; it states every fact about how your
service is built, started, seeded and checked. Read it completely before you start.

## The service

Write a small multi-user HTTP JSON service in Go, using Gin and GORM over `github.com/glebarez/sqlite` (one SQLite
file). The domain is your choice: a small real product you can describe in a sentence. It must not be tickets,
runs, habits or jobs. It has:

- 10 to 16 routes in total, counting the health route and the login;
- a login;
- per-user data on at least two tables: each user sees and changes only their own;
- at least one table whose rows belong to a user through a parent row (a row of another table) and carry no
  reference to a user of their own;
- at least one column that is stored but never returned to a client by any route;
- a status column whose value changes through the API, with at least two distinct transitions (a row starts at
  an initial value, and at least two different moves from one value to another can be made through the routes);
- request validation: a request with bad or missing input is refused with a client error and a message.

Every route is open to anyone, or needs a logged-in user, or needs a logged-in administrator: use the kinds your
product needs. At least two ordinary users exist in the seed; an administrator is optional.

Organise it the way you would organise a production service.

## What you hand back

Everything under `/Users/vivekgade/Documents/g3-s4-author/out/`. Choose a name `<x>` for the service (lowercase letters and digits, starting
with a letter) and write:

1. `out/tests/spec/<x>/`: the Go module (`go.mod`, `go.sum`, `main.go` at its root, and whatever else it needs);
2. `out/tests/spec/<x>_harness.py`: the harness described in `HARNESS_API.md`;
3. `out/tests/spec/<x>/studio.toml`: as `HARNESS_API.md` gives it;
4. `out/tests/spec/<x>/truth.json`: a machine-readable account of the service you wrote, in the schema below,
   with a `file:line` for every fact (paths relative to `out/tests/spec/<x>/`).

`truth.json` is a record of what your code does, not a plan: write it from your finished code, and every
`file:line` must point at the statement it describes.

### `truth.json` schema

```json
{
  "package": "<x>",
  "harness": "tests.spec.<x>_harness",
  "routes": [
    {"method": "GET", "path": "/health", "auth": "Public", "file": "main.go", "line": 30},
    {"method": "GET", "path": "/things/:id", "auth": "Member", "file": "main.go", "line": 41}
  ],
  "tables": [
    {"struct": "Thing", "table": "things", "columns": [
      {"name": "id", "json": "id", "private": false},
      {"name": "note", "json": null, "private": true}
    ]}
  ],
  "owners": {"Thing": "user_id"},
  "owner_via": {"Part": ["Thing", "thing_id"]},
  "machines": {"Thing.status": {"initial": "new", "edges": [["new", "done"], ["done", "new"]]}},
  "refusals": [
    {"route": "GET /things/:id", "status": 404, "detail": "not found", "cause": "not_found",
     "file": "api/things.go", "line": 52}
  ],
  "bind_status": 422,
  "login": {"path": "/login", "fields": ["username", "password"]},
  "seed_users": [{"name": "ann", "role": "Member"}, {"name": "ben", "role": "Member"}],
  "refs": {
    "tables.Thing": "models/thing.go:9",
    "owners.Thing": "models/thing.go:12",
    "owner_via.Part": "models/part.go:11",
    "machines.Thing.status.initial": "api/things.go:60",
    "machines.Thing.status.edge.new->done": "api/things.go:88",
    "machines.Thing.status.edge.done->new": "api/things.go:101",
    "bind_status": "api/things.go:48",
    "login": "main.go:31",
    "seed_users": "../<x>_harness.py:23"
  }
}
```

(The values above only show the shapes.) Meaning of every key:

- `package`: the module directory name `<x>`. `harness`: `tests.spec.<x>_harness`.
- `routes`: EVERY route the service registers, health and login included. `method`: `GET`, `POST`, `PUT`, `PATCH` or
  `DELETE`. `path`: in Gin's own pattern syntax (`/things/:id`); path parameters are integer row ids. `auth`:
  `Public` (anyone), `Member` (a logged-in user) or `Admin` (a logged-in administrator). `file`, `line`: the
  statement that registers the route.
- `tables`: EVERY table the service creates. `struct`: the Go struct's name; `table`: the SQL table name;
  `columns`: EVERY column, by its SQL name. `json`: the key the column is returned under in answers, or `null` if
  no route ever returns it; `private`: `true` exactly when `json` is `null`.
- `owners`: for every struct whose rows each belong directly to one user, the column holding that user's id.
- `owner_via`: for every struct whose rows belong to a user only through a parent row, `[parent struct, the
  column of this struct that refers to the parent row]`.
- `machines`: for every status column, key `"Struct.column"`: `initial` is the value a new row gets, `edges` is
  EVERY distinct `[from, to]` move the routes can make.
- `refusals`: EVERY place where the code deliberately answers a request with a status of 400 or more, one entry
  per statement. `route`: `"METHOD path"` exactly as in `routes`. `status`; `detail`: the message the answer
  carries, verbatim. `cause`: `auth` (credentials missing or wrong, or the role is not allowed), `not_found` (no
  such row), `not_owned` (the row belongs to another user), `invalid` (the request fails validation), `conflict` (the
  request clashes with the current state), or `other`. `file`, `line`: the statement that answers.
- `bind_status`: the status of the validation refusals (if you use several, the one for malformed or invalid
  request bodies).
- `login`: the login route's `path` and the names of the fields of its request body.
- `seed_users`: the users of `USERS` in your harness: `name` is the login name, `role` is `Member` or `Admin`.
- `refs`: a `file:line` for every fact the other keys state without one: `tables.<Struct>` (the struct's
  declaration), `owners.<Struct>` and `owner_via.<Struct>` (the column's declaration), `machines.<Struct.column>.initial`
  (where a new row gets its value), `machines.<Struct.column>.edge.<from>-><to>` (where that move is made), plus
  `bind_status`, `login` and `seed_users`. For the harness file use the path `../<x>_harness.py`.

## Work area

Use exactly one directory, `/Users/vivekgade/Documents/g3-s4-author`, for everything: your sources under `out/`, the toolchain's caches, your
own scripts. Export the environment given in `HARNESS_API.md` section 2 before every command so that the
toolchain writes nowhere else. Look at nothing outside it, and use no network except the Go module proxy.

## When finished

1. Run `python3 /Users/vivekgade/Documents/g3-s4-author/smoke.py /Users/vivekgade/Documents/g3-s4-author/out <x>` (`HARNESS_API.md` section 10) until every line reads `PASS`.
2. Write `out/FILES.txt` (every file under `out/`, one path per line, relative to `out/`, sorted, without
   `FILES.txt` and `freeze.sha256`) and `out/freeze.sha256` (the sha256 of each file in `FILES.txt`, in the format
   `sha256sum -c` reads; `shasum -a 256` produces it).
3. Report: the name `<x>`, the number of routes and of tables, the commands you ran and their last lines, and the
   sha256 of `out/freeze.sha256`.
4. Then stop. Change nothing after the freeze.
