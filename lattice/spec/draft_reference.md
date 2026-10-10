# Lattice spec API reference (for drafting a spec)

A spec is one Python module that defines `SPEC: Spec`. The checker runs it against the
app: every scenario on a fresh copy of the harness's seeded database (layer D), every
invariant after every request (layer C), the state machines over every write. A spec
that `validate(SPEC)` accepts is consistent with its own profile; it says nothing about
whether the app is right -- that is what the checks find.

Parts marked *(from the code)* are generated from Lattice's own docstrings.

## Imports

```python
from lattice.profile import AdminFlag, ApiProfile, NoRoles, Profile, RoleColumn, TokenColumn
from lattice.spec import (
    Accepted, Await, Invariant, Machine, Scenario, Service, Spec, Step, Unchecked,
    access_matrix,
)
from lattice.typesys import Scalar, Schema
```

Only these names. A spec has no endpoint programs (`programs`) unless the context gives
them; leave `programs`, `contracts` and `approved` out unless the context asks for them.

## The profile: tables, keys, ownership, roles

`Schema.from_pairs(pairs, *, keys=(), nullable=(), owner=None, private=(),
owner_via=None, admin_write=(), public_group=(), one_of=None, one_of_stored=None)`:

- `pairs`: `[("id", Scalar.INT), ("title", Scalar.STR), ...]`, in the table's column
  order. Scalars: {{scalars}}.
- `keys`: unique keys, each a tuple of columns: `keys=[("id",), ("email",)]`.
- `nullable`: columns that may be NULL. `private`: columns that must never be answered
  (a password hash, a token).
- `owner`: the column holding the owning user's id (the row is that user's).
  `owner_via=("parent_fk", "ParentSchema")`: the row belongs to its parent row's owner
  (the parent has `owner=`). A table has one or neither.
- `one_of={"status": ("queued", "running", "done")}`: a Str column's allowed values
  (machines, invariants and `in (...)` use them). `one_of_stored`: how each value is
  spelled in the database when not as itself (an ORM storing an Enum by member name:
  `{"status": ("QUEUED", "RUNNING", "DONE")}`, in one_of's order); scenarios and
  invariants still use the values.

`Profile(schemas={}, api=ApiProfile(...))` -- the spec needs the API profile (else S001).
`ApiProfile` fields worth setting:

- `tables` (schema name -> Schema) and `table_names` (schema name -> SQL table name):
  `tables={"Todo": TODO}, table_names={"Todo": "todos"}`. Scenarios may name a table
  either way; invariants use the schema name.
- `roles`: a frozenset with `"Public"` and the app's roles: `frozenset({"Public",
  "Member", "Admin"})`; `admin_role` (default `"Admin"`). Never `Authenticated`/`Roles`.
- `users_table`: the schema name of the users table.
- `roles_from` -- where a caller's role comes from:
{{doc-bullets:lattice.profile.RoleColumn,lattice.profile.AdminFlag,lattice.profile.NoRoles}}
- `auth`: `TokenColumn("<users column>")` (the default is `TokenColumn("token")`; see
  *Auth* below -- the column must exist in the users table you declared).
- `active_column`: a Bool users column marking an active account (inactive callers
  answer `inactive_status`, 400 by default).
- `error_body`: `"fastapi"` for `{"detail": ...}` error bodies, else the default
  `"lattice"`. `unauthorized_detail` / `forbidden_detail`: the 401 / 403 texts.
  `validation_status`: 422 (FastAPI, the default) or 400.

### Auth: what the spec declares and what the harness does

The spec never says how a caller logs in. The *harness* (the person running the check)
supplies the login: a `--login-path /auth/login` route taking `Spec.users`' credentials,
a `login(client, handle) -> token` function, or an `AuthDriver`. The checker calls it
once per handle in `Spec.users`, then sends `Authorization: Bearer <token>` on every step
whose `user` is that handle (`user=None` sends no header). What the spec declares:

- `users={"ann": ("ann@example.com", "pw-ann")}`: handle -> (login, password), two
  strings, the credentials of a seeded user exactly as the context gives them (the login
  is the email or username the login route takes, not the handle).
- `ApiProfile.auth` is `TokenColumn("<column>")` and nothing else for a drafted spec
  (`NativeToken` needs a declared native; leave it out unless the context gives one).
  Pass the class itself with a column of the users `Schema` you declared, never a string
  (`auth="token"`) and never a `{...}` dict.
- The column must be in the users schema's `pairs`. If the app's tokens are stateless
  (JWTs: nothing stored per user) and the context's users table has no token column, do
  **not** invent `("token", S.STR)`: the users table is the app's, and a column it lacks
  fails the seed. Point `TokenColumn` at a column the context does list, preferably the
  secret one that is already in `private=[...]` (`TokenColumn("hashed_password")`), and
  say in a comment that tokens are minted by the login route.
- Keep the 401 / 403 / inactive answers in the profile (`unauthorized_detail`,
  `forbidden_detail`, `inactive_status`, `active_column`), not in scenarios' setup.

Minimal correct auth, for an app whose login is `POST /auth/login` returning a JWT and
whose users table is `users(id, email, hashed_password, is_super_admin, is_active)`:

```python
USER = Schema.from_pairs(
    [("id", S.INT), ("email", S.STR), ("hashed_password", S.STR),
     ("is_super_admin", S.BOOL), ("is_active", S.BOOL)],
    keys=[("id",), ("email",)],
    private=["hashed_password"],
)
api = ApiProfile(
    tables={"User": USER},
    table_names={"User": "users"},
    roles=frozenset({"Public", "Member", "Admin"}),
    users_table="User",
    roles_from=AdminFlag("is_super_admin", "Member"),
    auth=TokenColumn("hashed_password"),   # JWTs: no token column; the login route mints them
    active_column="is_active",
    error_body="fastapi",
)
USERS = {"root": ("root@example.com", "pw-root"), "ann": ("ann@example.com", "pw-ann")}
```

Messages you may get back, and what they mean:

| Message | Meaning and fix |
|---|---|
| `import: ValueError: token column 'token' is not a column of User` | `auth` defaults to `TokenColumn("token")` and the users schema has no `token`. Set `auth=TokenColumn("<a column of USER>")` explicitly; do not add a column. |
| `ValueError: ApiProfile.auth must be TokenColumn(...) or NativeToken(...)` | `auth` is a string, dict or other value. Write `auth=TokenColumn("col")` and import `TokenColumn` from `lattice.profile`. |
| `NativeToken names unknown native ...` | You used `NativeToken` without declaring the native. Use `TokenColumn`. |
| `S011` | a step signs in as a handle missing from `Spec.users`. |
| `S015` | a `Spec.users` entry is not `handle -> (login, password)` of two strings. |
| `cannot log in as 'ann': ...` / `login as 'ann' failed: 401` (a check finding) | the harness's login route refused the `Spec.users` credentials: they differ from the seed's. Take them from the context; the spec is not at fault otherwise. |
| `no login given` / `layer B: no login given` | the harness has no login, so every signed-in step cannot run; not a spec problem. |

Take the tables, columns, SQL names, roles, seeded users and service names from the
context. Do not invent a table or column the context does not give; when the
requirements need one the context lacks, raise a Question and use your default.

## The spec *(from the code)*

`Spec{{fields:lattice.spec.model.Spec}}`

{{doc:lattice.spec.model.Spec}}

`users`: handle -> (login, password); the harness signs every handle in. Use the
handles and credentials of the users the context says the seed has.

## Scenarios and steps *(from the code)*

`Scenario{{fields:lattice.spec.model.Scenario}}`

{{doc:lattice.spec.model.Scenario}}

`Step{{fields:lattice.spec.model.Step}}`

{{doc:lattice.spec.model.Step}}

- `json` is the request body; `params` the query string. `expect_body` is a subset
  match: an object matches when its keys match (recursively); a list must have the same
  length, element-wise.
- Scenario names are unique (S010) and are what the trace links (`scenario:<name>`);
  use snake_case.
- `given` rows go into the seeded database before the steps: give every NOT NULL
  column, one_of columns by value, and ids well clear of the seed's (100 and up).
- The seed is the harness's: know only what the context says it holds. When you do not
  know a table's seeded row count, check with a `where` (`{"todos": {"where": {"title":
  "x"}, "count": 0}}`) instead of an absolute `{"count": n}`.

### expect_db forms

```python
{"todos": {"count": 3}}                                       # the table has 3 rows
{"todos": {"where": {"user_id": 1, "state": "open"}, "count": 2}}   # 2 rows match
{"todos": {"where": {"id": 100}, "equals": {"state": "done"}}}      # every match (>= 1)
{"todos": {"where": {"title": "x"}, "count": 1, "equals": {"state": "open"}}}  # both
```

A `where` value `None` matches NULL. Several tables may be checked in one `expect_db`.

## Background services *(from the code)*

`Service{{fields:lattice.spec.model.Service}}` -- {{doc:lattice.spec.model.Service}}

`Await{{fields:lattice.spec.model.Await}}`

{{doc:lattice.spec.model.Await}}

Declare every service a scenario awaits: `Spec(services=(Service("sweeper", "archives
done todos"),))`, with the names the context gives (S024). An `Await` needs a non-empty
`expect_db` and a positive `within_s` (S025).

## State machines *(from the code)*

`Machine{{fields:lattice.spec.model.Machine}}`

{{doc:lattice.spec.model.Machine}}

`edges` may be a mapping: `Machine("Todo", "state", {"open": ("done",), "done":
"archived"}, initial=("open",))`. The column must be a one_of column (S004) and every
state one of its values (S005).

## Invariants *(from the code)*

`Invariant{{fields:lattice.spec.model.Invariant}}` -- {{doc:lattice.spec.model.Invariant}}

{{moddoc:lattice.spec.invariants|Three forms are allowed:|One-of columns}}

Examples: `Todo.title != ""`, `is_null(Job.finished_at) or Job.finished_at >=
Job.started_at`, `Job.status in ("queued", "running", "done")`,
`count(Todo, by="user_id", where=Todo.state == "open") <= 50`,
`count(Membership, by=("team_id", "user_id")) <= 1` (a unique pair).

Use the schema name (`Todo`, not `todos`), compare a one_of column only with its values,
and compare Uuid / DateTime columns only with columns of their own type (S009).

## Unchecked requirements *(from the code)*

`Unchecked{{fields:lattice.spec.model.Unchecked}}`

{{doc:lattice.spec.model.Unchecked}}

Start the text with the rule id it covers and give the reason: `Unchecked("R9: the
e-mail is sent -- no mail capture in the harness", concerns=("User",))`. A concern must
be a table, a `Table.column`, a program or an endpoint of the spec (S016); leave
`concerns` empty when nothing fits.

## The access matrix *(from the code)*

`access_matrix{{sig:lattice.spec.authoring.access_matrix}}`

{{doc:lattice.spec.authoring.access_matrix}}

The result is a tuple of scenarios: splice it in, `scenarios=(*ACCESS, ...)`, and link
the generated names (`scenario:access__create__as_anon`).

## Contracts (optional) *(from the code)*

{{doc:lattice.spec.model.Contract}}

Leave `contracts` empty unless the context asks for them.

## Validation codes *(from the code)*

All codes: {{codes-all}}.

The ones drafts hit most:

{{codes:S002,S003,S008,S009,S010,S011,S012,S016,S024,S025}}

The module must also import cleanly: a `Schema` or `ApiProfile` that is inconsistent
(an owner column not in the schema, a one_of on a non-Str column, a role in
`RoleColumn(values=...)` that `roles` lacks) raises when the module is imported.

## The trace *(from the code)*

{{moddoc:lattice.spec.rules|A **trace**|An incremental draft}}

You write the trace's `links`, `unchecked`, `context` and `questions`; `source` and
`rules` are filled in from the numbered rules. Every rule id appears in exactly one of
`links`, `unchecked`, `context`. A link names a real element of `SPEC`:
`scenario:<Scenario.name>`, `invariant:<Invariant.name>`, `machine:<Table>.<column>`,
`service:<Service.name>`, `profile:<Table>` or `profile:<Table>.<column>`.

## Worked example

Requirements (numbered by the tool as R1..R9):

```markdown
# Todo app

The app keeps a todo list per user.

## Todos

- A member creates a todo with POST /todos {"title": ...}; it answers 201 and the todo, which starts open.
- A title is 1 to 100 characters; any other title answers 422.
- A member has at most 50 open todos; one more answers 409.
- POST /todos/{id}/done marks the caller's own open todo done; anyone else's todo answers 404.
- Admins cannot create todos (403); no token answers 401.
- A done todo is archived by the sweeper within a minute.
- A todo cannot go back to open.
- Members get a weekly e-mail digest.
```

Context: FastAPI + SQLite; tables `users(id, email, password, token, role)` with role
`member` / `admin`, and `todos(id, user_id, title, state)`; errors are `{"detail": ...}`;
the seed has users ann (id 1, member), ben (id 2, member) and ada (id 3, admin), with
passwords `pw-<name>`, and no todos; the background service is `sweeper`.

```python
"""Spec of the todo app, drafted from REQUIREMENTS.md (R1..R9)."""

from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import Await, Invariant, Machine, Scenario, Service, Spec, Step, Unchecked
from lattice.spec import access_matrix
from lattice.typesys import Scalar, Schema

S = Scalar
USER = Schema.from_pairs(
    [("id", S.INT), ("email", S.STR), ("password", S.STR), ("token", S.STR), ("role", S.STR)],
    keys=[("id",), ("email",), ("token",)],
    private=["password", "token"],
    one_of={"role": ("member", "admin")},
)
TODO = Schema.from_pairs(
    [("id", S.INT), ("user_id", S.INT), ("title", S.STR), ("state", S.STR)],
    keys=[("id",)],
    owner="user_id",
    one_of={"state": ("open", "done", "archived")},
)
PROFILE = Profile(
    schemas={},
    api=ApiProfile(
        tables={"User": USER, "Todo": TODO},
        table_names={"User": "users", "Todo": "todos"},
        roles=frozenset({"Public", "Member", "Admin"}),
        users_table="User",
        roles_from=RoleColumn("role", values={"member": "Member", "admin": "Admin"}),
        error_body="fastapi",
    ),
)
USERS = {"ann": ("ann@example.com", "pw-ann"), "ben": ("ben@example.com", "pw-ben"),
         "ada": ("ada@example.com", "pw-ada")}
ANN_TODO = {"todos": [{"id": 100, "user_id": 1, "title": "milk", "state": "open"}]}
UNCHANGED = {"todos": {"where": {"id": 100}, "equals": {"state": "open"}}}


def created(title: str) -> dict:
    return {"todos": {"where": {"title": title}, "count": 1,
                      "equals": {"user_id": 1, "state": "open"}}}


def nothing_with(title: str) -> dict:
    return {"todos": {"where": {"title": title}, "count": 0}}


ACCESS = access_matrix(
    "todos",
    actions={
        "create": ("POST", "/todos", {"title": "tea"}, ("todos", 1)),
        "done": ("POST", "/todos/100/done", None, None),
    },
    callers={
        None: {"create": 401, "done": (401, None, UNCHANGED)},
        "ada": {"create": 403},  # wrong role (R5)
        "ben": {"done": (404, None, UNCHANGED)},  # outsider (R4)
        "ann": {"create": (201, {"title": "tea", "state": "open"}),
                "done": (200, {"id": 100, "state": "done"},
                         {"todos": {"where": {"id": 100}, "equals": {"state": "done"}}})},
    },
    baseline={"todos": 1},
    given=ANN_TODO,
)
FIFTY_OPEN = {"todos": [{"id": 100 + i, "user_id": 1, "title": f"t{i}", "state": "open"}
                        for i in range(50)]}

SPEC = Spec(
    profile=PROFILE,
    users=USERS,
    services=(Service("sweeper", "archives done todos"),),
    machines=(Machine("Todo", "state", {"open": ("done",), "done": ("archived",)},
                      initial=("open",)),),
    invariants=(
        Invariant("open_todos_at_most_50", "A user has at most 50 open todos.",
                  'count(Todo, by="user_id", where=Todo.state == "open") <= 50'),
    ),
    scenarios=(
        *ACCESS,
        Scenario("create_answers_the_open_todo", steps=(
            Step("POST", "/todos", "ann", json={"title": "bread"}, expect_status=201,
                 expect_body={"title": "bread", "state": "open"},
                 expect_db=created("bread")),
        )),
        Scenario("title_bounds", steps=(
            Step("POST", "/todos", "ann", json={"title": ""}, expect_status=422,
                 expect_db=nothing_with("")),
            Step("POST", "/todos", "ann", json={"title": "x" * 101}, expect_status=422,
                 expect_db=nothing_with("x" * 101)),
            Step("POST", "/todos", "ann", json={"title": "x"}, expect_status=201,
                 expect_db=created("x")),
            Step("POST", "/todos", "ann", json={"title": "y" * 100}, expect_status=201,
                 expect_db=created("y" * 100)),
        )),
        Scenario("the_51st_open_todo_is_refused", given=FIFTY_OPEN, steps=(
            Step("POST", "/todos", "ann", json={"title": "one more"}, expect_status=409,
                 expect_db={"todos": {"where": {"user_id": 1, "state": "open"}, "count": 50}}),
        )),
        Scenario("done_twice_is_refused", given=ANN_TODO, steps=(
            Step("POST", "/todos/100/done", "ann", expect_status=200),
            Step("POST", "/todos/100/done", "ann", expect_status=409,
                 expect_db={"todos": {"where": {"id": 100}, "equals": {"state": "done"}}}),
        )),
        Scenario("the_sweeper_archives_a_done_todo",
                 given={"todos": [{"id": 101, "user_id": 1, "title": "old", "state": "done"}]},
                 steps=(Await("sweeper", {"todos": {"where": {"id": 101},
                                                    "equals": {"state": "archived"}}},
                              within_s=60, note="archived within a minute (R6)"),)),
    ),
    unchecked=(Unchecked("R9: the weekly digest e-mail -- the harness captures no mail",
                         concerns=("User",)),),
)
```

```json
{
  "links": {
    "R2": ["scenario:access__create__as_ann", "scenario:create_answers_the_open_todo",
           "machine:Todo.state"],
    "R3": ["scenario:title_bounds"],
    "R4": ["scenario:the_51st_open_todo_is_refused", "invariant:open_todos_at_most_50"],
    "R5": ["scenario:access__done__as_ann", "scenario:access__done__as_ben",
           "scenario:done_twice_is_refused"],
    "R6": ["scenario:access__create__as_ada", "scenario:access__create__as_anon",
           "scenario:access__done__as_anon"],
    "R7": ["scenario:the_sweeper_archives_a_done_todo", "service:sweeper"],
    "R8": ["machine:Todo.state"]
  },
  "unchecked": {"R9": "no mail capture in the harness"},
  "context": ["R1"],
  "questions": [
    {"id": "Q1", "text": "What does marking an already-done todo done again answer?",
     "default": "409, and the todo stays done", "rules": ["R5"], "answer": null}
  ]
}
```
