"""The rule oracle: a Spec's rule items, the probes that decide them, and their verdicts
(Phase 2 plan 2026-10-05, §1 and §2.4; layer R of :mod:`lattice.spec.check`).

**Routes.** :func:`route_contracts` is every route the spec states a rule layer for: its
``contracts``, plus one contract derived from each program endpoint that has none (auth,
table, kind and path parameters from the lowered program; no errors -- a program's
answers are layer B's).

**Items** (:func:`rule_items`), one id each:

* per table: ``T1 <Table>`` ownership (``owner`` / ``owner_via`` / ``users`` /
  ``shared``); ``T2 <Table>.<col>`` per private column; ``T3 <Table>.<col> <op> <n>``
  per bound a contract parameter stored in that column states (``ge gt le lt min_len
  max_len``); ``T4 <Table>.<col>`` per one_of column; ``T5 <Table> (<cols>)`` per key;
* per route: ``R1 <METHOD /path>`` auth; ``R2 <METHOD /path>`` ownership scoping (only
  for routes on an owned table that are not admin-only: "n/a" otherwise, so no item);
  ``R3 <METHOD /path> <status>[ <detail>]`` per stated error, and ``R3 <METHOD /path>
  5xx`` (no unpredicted 5xx).

**Probes** are requests made as the spec's users (``Spec.users``, each matched to its
users row by its login), each on a fresh copy of the seeded database, with the answer
and the database before and after recorded. Each bears on one or more items:

* R1: anonymously, with a malformed token, as an inactive user (a minted token, or one
  it got logging in while active), and as one user of every role, on up to two
  targets (the caller's own rows first). An *auth answer* -- the profile's forbidden,
  unauthorized and inactive answers, its token auth's invalid / unknown answers, and
  the ``refusals`` a front end recorded (DRF's 403 "credentials were not provided") --
  is a refusal, even on a route stating an error of that status (unless it states that
  very detail as a non-auth error: then only a probe on the caller's own row tells);
  otherwise a 401 the route does not state, or a 403 when the profile names no
  forbidden detail and the target is the caller's own. A caller is refused when all
  its probes are. The set of callers not refused must equal the auth's. On a Public
  route the malformed-token and inactive callers decide nothing (an app may refuse bad
  credentials on every request, as DRF does: the auth layer's choice, not the
  route's). On a login route taking HTTP Basic credentials (``login_route``) callers
  present their login and password.
* R2 and T1 (owner / owner_via): a non-admin reads, updates or deletes another user's
  row and gets the not-owned answer (the stated ``not_owned`` error, else 403 or 404)
  with the database unchanged; a list (or a read without an id) holds only the caller's
  rows; an update without an id changes only the caller's rows; a create as the caller
  stores the caller as the owner of every new owned row, even when the body names
  another user (and also without that field: an app refusing unknown fields says
  nothing then). ``owner_scope="any"``: another user's row is reached (not 403/404),
  and a list holds another user's row (never probed as if scoped to the caller).
  ``owner_or_public(<c>)``: another user's row whose ``<c>`` is false gets the not-owned
  answer and one whose ``<c>`` is true is reached, on a read; a write gets the not-owned
  answer on both (a public row first); a list holds the caller's rows and rows whose
  ``<c>`` is true only. A link between users (an owned table whose key is two columns,
  no one-column key: microblog's followers), found by its whole key, with the
  caller's side the scope's column (``owner(<c>)``) or else the owner: a list answers
  only links on the caller's side, or the users those links name on the other side; a
  create adds links on the caller's side only; a delete or update, called on a link of
  the caller's own and on another user's link to the caller that the caller does not
  mirror, touches links on the caller's side only; T5 repeats the caller's own link.
  A request may change its caller's own users row (a last-seen stamp); any other
  change counts as a write. Keys compare in one spelling (a UUID stored as 32 hex
  digits equals its dashed answer).
* T1 shared: a non-admin reads a row with a 2xx -- and, when routes update or delete
  the table's rows (``predicted["writes"]``), the same write of the same row (up to two
  rows, spread over their ``*_id`` columns) as every non-admin caller the route admits
  (pivot plan 2026-10-05, A.1). Reads alone never verify a table with such routes:
  verified needs two callers each writing the row, admitted with its database effect
  (each the other's control: a row has at most one owner); a caller refused (403/404,
  nothing written) where another wrote the row, having got past the route's check by
  some request, refutes it (the row is its owner's). With no such route the reads
  verify it and the reason says so. T1 users: every role a caller can
  have logged in, and a "me" route (a users-table read without an id) returns the
  caller (its key, else its login, in the answer).
* T2: no recorded body has the column in a row of the table, and some 2xx body returns
  a row of the table.
* T3: per write route with the parameter, the boundary value gets a 2xx and one step
  beyond (1 for Int, 0.01 for Float, one character) gets a 4xx with the database
  unchanged.
* T4: every listed value is accepted (not answered with the validation status) by a
  route writing the column; an unlisted value gets a 4xx with the database unchanged;
  every stored value after any probe is a stored spelling of a listed one.
* T5: a second create with the same key (the owner column: as a user who already owns
  a row; else key columns the route takes, from a seeded row) gets a non-2xx and adds
  no row. A 5xx still verifies the key, with a note (an app-baseline finding).
* R3: each stated error is triggered -- ``not_found`` by a missing id (the addressed
  row's, else a route's one path parameter naming a row: a link route's other user),
  ``not_owned`` by the R2 probes, ``unauthenticated`` / ``inactive`` / ``forbidden`` by
  the R1 probes,
  ``invalid`` by a body one step past a bound (or an unlisted value, or a required
  field missing), ``duplicate`` by the T5 probe, ``other`` by its ``example`` -- and
  answered with its status (and detail, when stated; a detail in an answer that is no
  JSON, an HTML page, is unreachable-by-probe). No probe of the route may answer a 5xx
  no error states; a route with optional query parameters is also called with every
  one of them, for that check.
* Routes in ``unreachable`` (they need the network, or an earlier route shadows them)
  are never called: their items are unexercised, ``unreachable-by-probe`` with the
  reason. A T3 bound no valid value reaches (an email address over 254 characters) is
  unreachable-by-probe too.

**Verdicts** (per item): ``verified`` -- at least one probe confirms it, none
contradicts it; ``refuted`` -- some probe contradicts it (``observed`` holds the fact
seen, the correction to propose); ``unexercised`` -- no probe could reach it (no route
does, or ``unreachable-by-probe`` with the reason); ``unclear`` -- the spec gives no
candidate (an unknown auth, owner scope or error status), or an R1 caller's answers
may be a role refusal the other callers of its role do not get. A probe whose answer
neither confirms nor contradicts an item (a create that fails, say) counts for neither.

**Evidence** (post-hoc round 2, 2026-10-05): an answer with more than one explanation
is no evidence -- its outcome is *undecided*, the reason saying why, and every probe's
record holds its ``bearing`` on each item (``decisive``, what it ``says``, ``why``); a
``verified`` item names its ``decisive`` probes, and :func:`check_verdicts` refuses one
without -- and a verified ``shared`` T1 resting on reads alone when its table has write
routes, or, without write routes, one whose reason does not say so. Probes are judged
once all have run (:class:`_Evidence`), so an outcome may rest on another probe's
answer:

* *reached* takes a 2xx that is no error envelope (``{"code": 404, ...}`` answered 200,
  :func:`_envelope`), and a write (a delete, or an update with a body) the database
  effect it predicts;
* R1: a 403 on a row that is not the caller's own is a role refusal or an ownership
  refusal alike (undecided) -- a *missing row* tells them apart (a 403 there, while
  another caller gets past it); a 404 or a 5xx to a request addressing a row, or a 405,
  admits no one; every role a probed caller holds must be decided;
* R2 / T1: another user's row refused (403/404) is the owner check's only when the
  caller gets past the route's check elsewhere and the row's owner gets past it with
  the very same request (a *witness*); a list or a write showing only the caller's own
  rows is owner scoping only when another caller's answer to the same request differs;
  ``owner_or_admin`` needs both halves;
* T3 / T4 / T5: a boundary value, a listed value or a second create refused counts only
  when the same request with plain valid values (a *control*; for T5, a new key) gets
  through -- or, refused as invalid, the control got past validation;
* R3 5xx: only probes past the auth check show the handler raised no 5xx; T4: a value
  only the import's own seed holds is no evidence.

**Transport.** Probes run through a *session factory*: ``sessions(db_path)`` is a
context manager opening the app on that database file, yielding an object with
``request(method, path, *, params, json, form, headers) -> Answer`` (and, for the
``login`` callable, ``client``: the underlying client), and disposing of the app on
exit. :func:`asgi_sessions` is today's ASGI TestClient, as ``lattice.importer.verify``
uses; another transport plugs in the same way.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from lattice.check.api_info import MISSING
from lattice.profile import AdminFlag, ApiProfile, NoRoles, RoleColumn
from lattice.spec.check import (
    BAD_TOKEN,
    MISSING_INT,
    VALID_UUID,
    _key_column,
    _NoEndpoint,
    _owner_of,
    _read_tables,
    _table_for_param,
    db_diff,
    spec_plans,
)
from lattice.spec.model import (
    Contract,
    ContractError,
    ContractParam,
    Spec,
    resolve_table,
    scope_parts,
    tables_of,
)
from lattice.spec.validate import auth_roles
from lattice.typesys import Scalar, Schema

Verdict = Literal["verified", "refuted", "unexercised", "unclear"]
VERDICTS: tuple[str, ...] = ("verified", "refuted", "unexercised", "unclear")
# a refuted item whose observed fact, as a corrected item, was verified on a second seed
# variant (the importer's corrections, lattice.importer.rules): it counts as verified
VERIFIED_CORRECTED = "verified (corrected)"
UNREACHABLE = "unreachable-by-probe"
# a table no route reads or writes (the importer's front-end evidence, never the probes):
# its T1 item is unexercised with a reason starting so, and the table's verdict is this,
# left out of the table denominator (plan 2026-10-05, measurement decision 1)
OUTSIDE_API = "outside API"


class VerdictInvariantError(RuntimeError):
    """An internal error: an item is ``verified`` while one of its recorded probes
    contradicts its prediction (:func:`summarise`'s self-check). A verdict path broke
    the rule "verified: some probe confirms it, none contradicts it"."""


# the R1 callers that are no user of a role: their answers alone never verify an R1
# item (the prediction is about which roles are admitted)
NON_ROLE_CALLERS = ("anonymous", "malformed token", "inactive")
# a body or form parameter carrying credentials (a password, a refresh token): probes
# invent its value, so the route's answer is about the credentials, not the caller
SECRET_PARAM = re.compile(r"(^|_)(password|passwd|pwd|secret|otp|refresh|token)($|_)", re.I)


def is_verified(verdict: str) -> bool:
    """Whether a verdict counts as verified: ``verified`` or ``verified (corrected)``."""
    return verdict in ("verified", VERIFIED_CORRECTED)


AUTH_TRIGGERS = ("unauthenticated", "forbidden", "inactive")
NOT_OWNED = (403, 404)
FLOAT_STEP = 0.01
UNLISTED = "not-a-choice"
Login = Callable[[Any, str], str]


# ---- transport ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    """What the app answered: status, headers, body (JSON, else text, else None)."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Any = None


class Session(Protocol):
    """An app opened on one database file."""

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        form: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Answer: ...


SessionFactory = Callable[[Path], AbstractContextManager[Any]]


def _parse(response: Any) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


class _ClientSession:
    """A Session over a TestClient-like client (``client.request(...)``)."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        form: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Answer:
        r = self.client.request(
            method, path, params=params, json=json, data=form, headers=dict(headers or {})
        )
        return Answer(r.status_code, dict(r.headers), _parse(r))


def dispose(app: Any) -> None:
    """Dispose of the app's engine (``app.state.engine``), as verify.run_case does."""
    engine = getattr(getattr(app, "state", None), "engine", None)
    if engine is not None and hasattr(engine, "dispose"):
        engine.dispose()


def asgi_sessions(app_factory: Callable[[Path], Any]) -> SessionFactory:
    """Sessions of an ASGI app built by ``app_factory(db_path)``, through Starlette's
    TestClient (server exceptions answered as 500s); the engine is disposed on exit."""

    @contextmanager
    def open_app(db: Path) -> Iterator[_ClientSession]:
        from fastapi.testclient import TestClient

        app = app_factory(db)
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                yield _ClientSession(client)
        finally:
            dispose(app)

    return open_app


# ---- items -------------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleItem:
    """One rule item: ``id`` (see the module docstring), ``kind`` (T1-T5, R1-R3), the
    table or endpoint it is about, the rule in words, what the spec predicts, and -- when
    the spec gives no candidate -- why it is ``unclear``."""

    id: str
    kind: str
    subject: str
    sentence: str
    predicted: Mapping[str, Any] = field(default_factory=dict, hash=False)
    unclear: str | None = None


def allowed_roles(auth: str | None, api: ApiProfile) -> set[str] | None:
    """The roles a caller may have to pass ``auth``; None for Public (anyone, no caller
    too) and for an unknown auth."""
    if auth is None or auth == "Public":
        return None
    every = {r for r in api.roles if r != "Public"}
    if auth == "Authenticated":
        return every
    if auth == "Admin":
        return {api.admin_role}
    roles = auth_roles(auth) or ()
    if auth.startswith("Roles("):
        return set(roles)
    return {*roles, api.admin_role}  # auth=<role>: that role or the admin role


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _schema(spec: Spec, table: str | None) -> tuple[str, str, Schema] | None:
    return resolve_table(spec.profile, table) if table else None


def owner_scope(spec: Spec, c: Contract) -> str | None:
    """The route's ownership scope: the contract's, else "owner" for an owned table
    unless the route is admin-only; None: not applicable."""
    if c.owner_scope is not None:
        return c.owner_scope
    found = _schema(spec, c.table)
    if found is None or not (found[2].owner or found[2].owner_via):
        return None
    api = spec.profile.api
    admitted = allowed_roles(c.auth, api) if api is not None else None
    admin_only = c.auth == "Admin" or (admitted is not None and api is not None
                                        and admitted <= {api.admin_role})  # fmt: skip
    return None if admin_only else "owner"


def _link(schema: Schema) -> tuple[str, str] | None:
    """A link table's key: two columns, the table having no one-column key (microblog's
    followers: ``(follower_id, followed_id)``); None otherwise."""
    if _key_column(schema) is not None:
        return None
    return next(((k[0], k[1]) for k in schema.keys if len(k) == 2), None)


def _link_sides(schema: Schema, scope: str | None) -> tuple[str, str] | None:
    """(the caller's side, the other side) of a route on an owned link table: the
    scope's column (``owner(<column>)``), else the owner column; None when the table is
    no link or that column is not one of its key's."""
    pair = _link(schema)
    if pair is None or not schema.owner:
        return None
    form, col = scope_parts(scope)
    side = col if form == "owner" and col else schema.owner
    if side not in pair:
        return None
    return side, pair[1] if pair[0] == side else pair[0]


def _public_columns(spec: Spec, routes: Sequence[Contract]) -> dict[str, str]:
    """Table -> the column an ``owner_or_public(<column>)`` route states for it."""
    out: dict[str, str] = {}
    for c in routes:
        form, col = scope_parts(c.owner_scope)
        found = _schema(spec, c.table)
        if form == "owner_or_public" and col and found is not None:
            out.setdefault(found[0], col)
    return out


def _error_id(c: Contract, e: ContractError) -> str:
    if e.status is None:
        return f"R3 {c.endpoint} ?"
    return f"R3 {c.endpoint} {e.status}" + (f" {e.detail}" if e.detail else "")


def _error_ids(c: Contract) -> list[tuple[str, ContractError]]:
    out: list[tuple[str, ContractError]] = []
    seen: dict[str, int] = {}
    for e in c.errors:
        base = _error_id(c, e)
        seen[base] = seen.get(base, 0) + 1
        out.append((base if seen[base] == 1 else f"{base} #{seen[base]}", e))
    return out


BOUND_OPS = ("ge", "gt", "le", "lt", "min_len", "max_len")


def _bounds(p: ContractParam) -> list[tuple[str, float]]:
    return [(op, getattr(p, op)) for op in BOUND_OPS if getattr(p, op) is not None]


def _column(spec: Spec, ref: str) -> tuple[str, str] | None:
    table, _, column = ref.partition(".")
    found = resolve_table(spec.profile, table)
    return (found[0], column) if found else None


SHARED_WRITE_KINDS = ("update", "delete")
# a verified "shared" T1 of a table no route updates or deletes says so in its reason
_NO_SHARED_WRITES = "no route updates or deletes {table} rows: reads only"


def _shared_writes(spec: Spec, table: str, routes: Sequence[Contract]) -> list[str]:
    """The routes (``METHOD /path``) updating or deleting rows of ``table``: what a
    ``shared`` T1 claims besides reads -- any user may write another user's row."""
    found = _schema(spec, table)
    out = []
    for c in routes:
        mine = _schema(spec, c.table)
        if found is not None and mine is not None and mine[0] == found[0] \
                and c.kind in SHARED_WRITE_KINDS:  # fmt: skip
            out.append(c.endpoint)
    return out


def rule_items(spec: Spec) -> list[RuleItem]:
    """Every rule item of the spec: tables first (in the profile's order), then routes."""
    api = spec.profile.api
    assert api is not None, "a spec with rule items has an API profile"
    tables, _ = tables_of(spec.profile)
    routes = route_contracts(spec)
    public = _public_columns(spec, routes)
    items: list[RuleItem] = []
    for name, schema in tables.items():
        pair = _link(schema) if _link_sides(schema, None) is not None else None
        if name == api.users_table:
            predicted: dict[str, Any] = {"ownership": "users"}
            sentence = f"{name} is the users table: every role logs in; 'me' is the caller"
        elif schema.owner and pair is not None:
            predicted = {"ownership": "owner", "column": schema.owner, "link": list(pair)}
            sentence = (
                f"a {name} row links two users ({', '.join(pair)}): only the user in "
                f"{schema.owner} creates or deletes it; each side reads its own links"
            )
        elif schema.owner and name in public:
            predicted = {"ownership": "owner", "column": schema.owner, "public": public[name]}
            sentence = (
                f"a {name} row belongs to the user in {schema.owner}; others read it only when "
                f"{public[name]} is true, and never write it"
            )
        elif schema.owner:
            predicted = {"ownership": "owner", "column": schema.owner}
            sentence = f"a {name} row belongs to the user in {schema.owner}; others cannot reach it"
        elif schema.owner_via:
            fk, parent = schema.owner_via
            predicted = {"ownership": "owner_via", "via": [fk, parent]}
            sentence = f"a {name} row belongs to the owner of its {parent} ({fk})"
        else:
            predicted = {"ownership": "shared"}
            sentence = f"{name} rows are shared: any user may read them"
            writes = _shared_writes(spec, name, routes)
            if writes:
                # the routes updating or deleting its rows: reads alone never verify it
                predicted["writes"] = writes
                sentence += f", and update or delete them ({', '.join(writes)})"
        items.append(RuleItem(f"T1 {name}", "T1", name, sentence, predicted))
        for col in sorted(schema.private):
            items.append(
                RuleItem(
                    f"T2 {name}.{col}",
                    "T2",
                    name,
                    f"{name}.{col} is private: no answer shows it",
                    {"private": col},
                )
            )
    seen_bounds: dict[str, RuleItem] = {}
    for c in routes:
        for p in c.params:
            col = _column(spec, p.column) if p.column else None
            if col is None:
                continue
            for op, value in _bounds(p):
                item_id = f"T3 {col[0]}.{col[1]} {op} {_num(value)}"
                seen_bounds.setdefault(
                    item_id,
                    RuleItem(
                        item_id,
                        "T3",
                        col[0],
                        f"{col[0]}.{col[1]} {op} {_num(value)}: writes past it are refused",
                        {"column": col[1], "op": op, "value": value},
                    ),
                )
    items += sorted(seen_bounds.values(), key=lambda i: i.id)
    for name, schema in tables.items():
        for col, values in schema.one_of:
            stored = dict(schema.one_of_stored).get(col, values)
            items.append(
                RuleItem(
                    f"T4 {name}.{col}",
                    "T4",
                    name,
                    f"{name}.{col} is one of {', '.join(values)}",
                    {"values": list(values), "stored": list(stored)},
                )
            )
    for name, schema in tables.items():
        for key in schema.keys:
            items.append(
                RuleItem(
                    f"T5 {name} ({', '.join(key)})",
                    "T5",
                    name,
                    f"no two {name} rows share ({', '.join(key)})",
                    {"key": list(key)},
                )
            )
    for c in routes:
        ep = c.endpoint
        allowed = allowed_roles(c.auth, api)
        r1_predicted: dict[str, Any] = {"allowed": "anyone" if allowed is None else sorted(allowed)}
        items.append(
            RuleItem(
                f"R1 {ep}",
                "R1",
                ep,
                f"{ep} admits exactly: {c.auth}",
                r1_predicted if c.auth is not None else {},
                None if c.auth is not None else "the route's auth is unknown",
            )
        )
        scope = owner_scope(spec, c)
        if scope is not None:
            form, col = scope_parts(scope)
            if form == "owner" and col:
                r2_sentence = f"{ep} acts only on the {c.table} rows whose {col} is the caller"
            elif form == "owner_or_public":
                r2_sentence = (f"{ep} reads the caller's own {c.table} rows and others' whose "
                               f"{col} is true; it writes the caller's own only")  # fmt: skip
            elif form == "owner":
                r2_sentence = f"{ep} acts on the caller's own {c.table} rows only"
            elif form == "owner_or_admin":
                r2_sentence = (f"{ep} acts on the caller's own {c.table} rows only; the admin "
                               "role on anyone's")  # fmt: skip
            else:
                r2_sentence = f"{ep} acts on any user's {c.table} rows"
            items.append(
                RuleItem(
                    f"R2 {ep}",
                    "R2",
                    ep,
                    r2_sentence,
                    {"scope": scope, "table": c.table},
                    "the route's ownership scope is unknown" if scope == "unknown" else None,
                )
            )
        for item_id, e in _error_ids(c):
            items.append(
                RuleItem(
                    item_id,
                    "R3",
                    ep,
                    f"{ep} answers {e.status or '?'}"
                    + (f" {e.detail!r}" if e.detail else "")
                    + f" ({e.when})",
                    {"status": e.status, "detail": e.detail, "when": e.when},
                    None if e.status is not None else "the error's status is unknown",
                )
            )
        items.append(
            RuleItem(
                f"R3 {ep} 5xx",
                "R3",
                ep,
                f"{ep} answers no 5xx that its errors do not state",
                {"statuses": sorted({e.status for e in c.errors if e.status and e.status >= 500})},
            )
        )
    return items


# ---- routes ------------------------------------------------------------------------------


def _function_calls(spec: Spec, method: str, path: str) -> list[tuple[str, str]]:
    """(call name, first-argument name) of every call in the program function of
    ``method path``."""
    import ast

    from lattice.spec.check import _endpoint_functions

    for source in spec.programs.values():
        for fn, m, p in _endpoint_functions(source):
            if (m, p) != (method, path):
                continue
            out = []
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    first = node.args[0] if node.args else None
                    out.append((node.func.id, first.id if isinstance(first, ast.Name) else ""))
            return out
    return []


def _from_plan(spec: Spec, plan: Any) -> Contract:
    """The rule layer a program states for its endpoint."""
    api = spec.profile.api
    assert api is not None
    tables, sql_names = tables_of(spec.profile)
    by_sql = {sql: name for name, sql in sql_names.items()}
    writes = [
        (s.kind, by_sql.get(s.table, s.table))
        for s in plan.steps
        if hasattr(s, "kind") and s.kind in ("insert", "update", "delete") and hasattr(s, "table")
    ]
    calls = _function_calls(spec, plan.method, plan.path)
    fetched = [arg for name, arg in calls if name == "fetch" and arg in tables]
    kinds = [k for k, _ in writes]
    if "insert" in kinds:
        kind, table = "create", writes[kinds.index("insert")][1]
    elif "delete" in kinds:
        kind, table = "delete", writes[kinds.index("delete")][1]
    elif "update" in kinds:
        kind, table = "update", writes[kinds.index("update")][1]
    else:
        kind = "list" if plan.success in ("List", "Page") else "read"
        table = fetched[0] if fetched else None
        if table is None and any(p.source == "user" for p in plan.params):
            table = api.users_table  # reads the caller's own row
    endpoint = _NoEndpoint(plan.method, plan.path)
    params: list[ContractParam] = []
    for p in plan.params:
        if p.source == "user" or p.scalar is None:
            continue
        source = "form" if p.source == "body" and plan.body == "form" else p.source
        required = p.default is MISSING and not p.optional and not p.nullable
        example = "probe@example.com" if p.email else None
        params.append(
            ContractParam(
                p.name,
                source,
                p.scalar,
                required=required,
                ge=p.ge,
                le=p.le,
                min_len=p.min_len,
                max_len=p.max_len,
                one_of=p.one_of,
                ref=_table_for_param(spec, endpoint, p.name) if p.source == "path" else None,
                example=example,
            )
        )
    return Contract(
        plan.method,
        plan.path,
        auth=plan.auth,
        table=table,
        kind=kind,
        params=tuple(params),
        response=plan.response,
    )


def route_contracts(spec: Spec) -> list[Contract]:
    """The spec's contracts, then a derived contract for every program endpoint the
    contracts do not cover (in program order)."""
    out = list(spec.contracts)
    covered = {c.endpoint for c in out}
    if not spec.programs:
        return out
    for plan in spec_plans(spec):
        if f"{plan.method} {plan.path}" not in covered:
            covered.add(f"{plan.method} {plan.path}")
            out.append(_from_plan(spec, plan))
    return out


# ---- probes ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    """One request and the items it bears on. ``expect`` names the judgement (see
    :func:`_judge`), ``args`` its parameters. ``user`` is a handle; ``token`` a raw
    token (overrides ``user``); ``headers`` the request's auth headers as they are
    (overrides both: a login route's HTTP Basic credentials)."""

    name: str
    route: str
    items: tuple[str, ...]
    method: str
    path: str
    expect: str
    user: str | None = None
    token: str | None = None
    params: Mapping[str, Any] | None = field(default=None, hash=False)
    json: Any = field(default=None, hash=False)
    form: Mapping[str, Any] | None = field(default=None, hash=False)
    args: Mapping[str, Any] = field(default_factory=dict, hash=False)
    headers: Mapping[str, str] | None = field(default=None, hash=False)
    # cookies sent besides (a caller's refresh-token cookie its login set)
    cookies: Mapping[str, str] | None = field(default=None, hash=False)

    def request(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "user": self.user,
            "token": "<given>" if self.token is not None else None,
            "params": dict(self.params) if self.params else None,
            "json": self.json,
            "form": dict(self.form) if self.form else None,
        }


@dataclass(frozen=True)
class Caller:
    """A handle of ``Spec.users`` as the seed knows it."""

    handle: str
    key: Any
    role: str | None
    active: bool


@dataclass
class ProbeContext:
    """The seed's rows (SQL table -> rows), the callers, their tokens, the inactive
    caller's (minted) token; ``refusals``: the (status, detail) answers that refuse a
    caller besides the profile's (a front end's recorded auth answers); ``login_route``:
    the (method, path) of a login route taking HTTP Basic credentials."""

    spec: Spec
    seed: Mapping[str, list[dict[str, Any]]]
    callers: dict[str, Caller]
    tokens: dict[str, str]
    inactive: Caller | None = None
    inactive_token: str | None = None
    refusals: tuple[tuple[int, str | None], ...] = ()
    login_route: tuple[str, str] | None = None
    # a login route taking JSON credentials: (method, path, username field, password
    # field); its R1 probes present each caller's own credentials
    json_login: tuple[str, str, str, str] | None = None
    # handle -> the string fields its login answered (``refresh``, ``access_token``...),
    # and the cookies the login set: a caller's own credentials to present
    answers: dict[str, dict[str, str]] = field(default_factory=dict)
    cookies: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def api(self) -> ApiProfile:
        assert self.spec.profile.api is not None
        return self.spec.profile.api

    def rows(self, table: str) -> list[dict[str, Any]]:
        found = resolve_table(self.spec.profile, table)
        return list(self.seed.get(found[1], ())) if found else []

    def owner(self, table: str, row: Mapping[str, Any], snapshot: Mapping | None = None) -> Any:
        return _owner_of(self.spec, table, row, snapshot if snapshot is not None else self.seed)

    def key_of(self, table: str) -> str | None:
        found = resolve_table(self.spec.profile, table)
        return _key_column(found[2]) if found else None

    def owns(self, handle: str, table: str) -> list[Any]:
        """Keys of the rows of ``table`` the handle's user owns (a users row: its own)."""
        caller, key = self.callers.get(handle), self.key_of(table)
        if caller is None or key is None:
            return []
        found = resolve_table(self.spec.profile, table)
        users = resolve_table(self.spec.profile, self.api.users_table)
        if found is not None and users is not None and found[0] == users[0]:
            return [r[key] for r in self.rows(table) if _k(r[key]) == _k(caller.key)]
        return [r[key] for r in self.rows(table) if self.owner(table, r) == caller.key]

    def with_token(self) -> list[Caller]:
        return [c for c in self.callers.values() if c.handle in self.tokens]

    def allowed(self, c: Contract) -> list[Caller]:
        """Callers with a token whose role the route admits, non-admins first."""
        roles = allowed_roles(c.auth, self.api)
        found = [x for x in self.with_token() if roles is None or x.role in roles]
        return sorted(found, key=lambda x: x.role == self.api.admin_role)

    def non_admin(self, c: Contract) -> list[Caller]:
        return [x for x in self.allowed(c) if x.role != self.api.admin_role]


def _role_of(api: ApiProfile, schema: Schema, row: Mapping[str, Any]) -> str | None:
    src = api.roles_from
    if isinstance(src, NoRoles):
        return src.role
    if isinstance(src, AdminFlag):
        return api.admin_role if row.get(src.column) else src.role
    column = src.column if isinstance(src, RoleColumn) else "role"
    stored = row.get(column)
    values = dict(schema.one_of).get(column)
    spellings = dict(schema.one_of_stored).get(column)
    if values and spellings and stored in spellings:
        stored = values[spellings.index(stored)]
    if isinstance(src, RoleColumn) and src.values:
        return dict(src.values).get(str(stored))
    return None if stored is None else str(stored)


def _callers(spec: Spec, seed: Mapping[str, list[dict[str, Any]]]) -> dict[str, Caller]:
    api = spec.profile.api
    assert api is not None
    found = resolve_table(spec.profile, api.users_table)
    if found is None:
        return {}
    _, sql, schema = found
    key = _key_column(schema) or "id"
    out: dict[str, Caller] = {}
    for handle, (login, _) in spec.users.items():
        row = next((r for r in seed.get(sql, ()) if login in r.values()), None)
        if row is None:
            continue
        active = bool(row.get(api.active_column, True)) if api.active_column else True
        out[handle] = Caller(handle, row.get(key), _role_of(api, schema, row), active)
    return out


# ---- request values ------------------------------------------------------------------------


def _clamp(p: ContractParam, value: float, step: float) -> float:
    if p.ge is not None:
        value = max(value, p.ge)
    if p.gt is not None:
        value = max(value, p.gt + step)
    if p.le is not None:
        value = min(value, p.le)
    if p.lt is not None:
        value = min(value, p.lt - step)
    return value


def _valid(p: ContractParam, ctx: ProbeContext) -> Any:
    """A value the parameter accepts."""
    if p.example is not None:
        return p.example
    if p.one_of:
        return p.one_of[0]
    if p.ref is not None:
        key = ctx.key_of(p.ref)
        rows = ctx.rows(p.ref)
        if key and rows:
            return rows[0][key]
    s = p.scalar
    if s is Scalar.INT:
        return int(_clamp(p, 1, 1))
    if s is Scalar.FLOAT:
        return float(_clamp(p, 1.0, FLOAT_STEP))
    if s is Scalar.BOOL:
        return True
    if s is Scalar.UUID:
        return VALID_UUID
    if s is Scalar.DATETIME:
        # as the app spells them: naive (pydantic's wire for a naive column) or UTC
        naive = ctx.spec.profile.api is not None and ctx.api.datetime_wire == "naive"
        return "2026-06-01T12:00:00" if naive else "2026-06-01T12:00:00Z"
    if s is Scalar.DATE:
        return "2026-06-01"
    if "email" in p.name.lower():
        return "probe@example.com"
    text = f"valid {p.name.rsplit('.', 1)[-1].replace('_', ' ')}"
    if p.min_len is not None and len(text) < p.min_len:
        text += "x" * (p.min_len - len(text))
    if p.max_len is not None:
        text = text[: p.max_len]
    return text


URL_SHAPE = re.compile(r"^https?://", re.I)
FIXED_SHAPES = (
    re.compile(r"^\d{1,3}(\.\d{1,3}){3}$"),  # an IPv4 address
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I),
)


def _sized(p: ContractParam, n: int) -> str | None:
    """A text of ``n`` characters in the parameter's format (its example's: a URL, an
    email address); None when the format admits no such length (an IP address)."""
    example = p.example if isinstance(p.example, str) else ""
    if "email" in p.name.lower() or (example and "@" in example and " " not in example):
        # an address of that length: the bound, not the format, decides
        return _address(n) if n > len(EMAIL_DOMAIN) else "b" * n
    if URL_SHAPE.match(example):
        base = "https://example.com/"
        return base + "b" * (n - len(base)) if n > len(base) else None
    if any(shape.match(example) for shape in FIXED_SHAPES):
        return None
    return "b" * n


def _edges(p: ContractParam, op: str, bound: float) -> tuple[Any, Any]:
    """(the boundary value, one step beyond) for one bound; None for a length the
    parameter's format cannot have."""
    if op in ("min_len", "max_len"):
        n = int(bound)
        beyond = max(n - 1 if op == "min_len" else n + 1, 0)
        return _sized(p, n), _sized(p, beyond)
    step: float = 1 if p.scalar is Scalar.INT else FLOAT_STEP
    cast = int if p.scalar is Scalar.INT else float
    if op == "ge":
        return cast(bound), cast(bound - step)
    if op == "gt":
        return cast(bound + step), cast(bound)
    if op == "le":
        return cast(bound), cast(bound + step)
    return cast(bound - step), cast(bound)  # lt


EMAIL_DOMAIN = "@example.com"


def _address(n: int) -> str:
    """An email address of ``n`` characters (a local part of at most 64, domain
    labels of at most 63)."""
    rest = n - len(EMAIL_DOMAIN)
    if rest <= 64:
        return "b" * max(rest, 0) + EMAIL_DOMAIN
    left = n - 65 - len(".com")  # after "<64 b>@", before ".com"
    labels = []
    while left > 0:
        size = min(left, 62 if left > 63 else left)
        labels.append("c" * size)
        left -= size + (1 if left > size else 0)
    return "b" * 64 + "@" + ".".join(labels) + ".com"


def replace_param(p: ContractParam, **changes: Any) -> ContractParam:
    from dataclasses import replace

    return replace(p, **changes)


def _put(root: dict[str, Any], name: str, value: Any) -> None:
    keys: list[Any] = [int(k) if k.isdigit() else k for k in name.split(".")]
    cur: Any = root
    for k, nxt in zip(keys, keys[1:], strict=False):
        empty: Any = [] if isinstance(nxt, int) else {}
        if isinstance(k, int):
            while len(cur) <= k:
                cur.append(None)
            if cur[k] is None:
                cur[k] = empty
            cur = cur[k]
        else:
            cur = cur.setdefault(k, empty)
    last = keys[-1]
    if isinstance(last, int):
        while len(cur) <= last:
            cur.append(None)
        cur[last] = value
    else:
        cur[last] = value


def _holes(path: str) -> list[str]:
    return re.findall(r"\{([^}]+)\}", path)


@dataclass
class _Route:
    """A route contract and what the probes need of it."""

    c: Contract
    table: tuple[str, str, Schema] | None
    scope: str | None
    path_params: dict[str, ContractParam]
    addr: str | None  # the path parameter addressing the route's table


def _route(spec: Spec, c: Contract) -> _Route:
    found = _schema(spec, c.table)
    declared = {p.name: p for p in c.params if p.source == "path"}
    path_params: dict[str, ContractParam] = {}
    endpoint = _NoEndpoint(c.method, c.path)
    for hole in _holes(c.path):
        p = declared.get(hole)
        if p is None:
            p = ContractParam(hole, "path", Scalar.INT, ref=_table_for_param(spec, endpoint, hole))
        elif p.ref is None:
            p = replace_param(p, ref=_table_for_param(spec, endpoint, hole))
        path_params[hole] = p
    addr = None
    if found is not None:
        for name, p in path_params.items():
            ref = _schema(spec, p.ref)
            if ref is not None and ref[0] == found[0]:
                addr = name
    only = next(iter(path_params.values()), None)
    if addr is None and len(path_params) == 1 and found is not None and only.ref is None:  # type: ignore[union-attr]
        addr = next(iter(path_params))
    return _Route(c, found, owner_scope(spec, c), path_params, addr)


def _lookup_value(r: _Route, ctx: ProbeContext, p: ContractParam, target: Any) -> Any:
    """The path value addressing the row whose key is ``target``: the key itself, or --
    for a parameter stating another column of the addressed table (``column``: DRF's
    ``lookup_field``, a slug), or named as one (``/users/{email}``) -- that row's value
    of it."""
    if r.table is None:
        return target
    key = ctx.key_of(r.table[0])
    if p.column:
        found = _column(ctx.spec, p.column)
        if found is None or found[0] != r.table[0] or key is None or found[1] == key:
            return target
        col = found[1]
    elif key is not None and p.name != key and p.name in r.table[2].names():
        col = p.name
    else:
        return target
    row = next((x for x in ctx.rows(r.table[0]) if _k(x.get(key)) == _k(target)), None)
    return row.get(col, target) if row is not None else target


def _id_column(table: str) -> list[str]:
    """The columns that name a row of ``table`` by key, by convention (``topic_id``,
    ``order_item_id``)."""
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", table).lower()
    return list(dict.fromkeys([f"{snake}_id", f"{table.lower()}_id"]))


def _consistent(r: _Route, ctx: ProbeContext, target: Any) -> dict[str, Any]:
    """Values of the route's other path holes that go with the addressed row ``target``:
    its own column named as the hole (or as the hole's table's key: ``topic_id`` for
    ``/topics/{topic}/comments/{pk}``), else a row linking the two (``/notes/{note_id}
    /tags/{tag_id}``: a tag linked to the note). A request whose holes disagree (a
    comment under another topic) is answered "not found" for that reason alone."""
    if r.addr is None or r.table is None:
        return {}
    key = ctx.key_of(r.table[0])
    row = next((x for x in ctx.rows(r.table[0]) if key and _k(x.get(key)) == _k(target)), None)
    if row is None:
        return {}
    tables, _ = tables_of(ctx.spec.profile)
    mine = [r.addr, *_id_column(r.table[0])]
    out: dict[str, Any] = {}
    for name, p in r.path_params.items():
        if name == r.addr:
            continue
        found = resolve_table(ctx.spec.profile, p.ref) if p.ref else None
        cols = [name, *(_id_column(found[0]) if found is not None else [])]
        col = next((c for c in cols if c in row and row[c] is not None), None)
        if col is not None:
            out[name] = row[col]
            continue
        if r.c.kind not in ("read", "update", "delete"):
            continue  # a link to add (an action, a create): not one that exists
        for t in tables:
            link = next((x for x in ctx.rows(t)
                         for a in mine if a in x and _k(x[a]) == _k(target)
                         and any(c in x for c in cols)), None)  # fmt: skip
            if link is not None:
                out[name] = link[next(c for c in cols if c in link)]
                break
    return out


@dataclass(frozen=True)
class _Req:
    path: str
    params: dict[str, Any] | None
    json: Any
    form: dict[str, Any] | None


def _build(
    r: _Route,
    ctx: ProbeContext,
    *,
    target: Any = None,
    set_: Mapping[str, Any] | None = None,
    drop: str | None = None,
    extra: Mapping[str, Any] | None = None,
    full: bool = False,
    fields: Mapping[str, Any] | None = None,
) -> _Req:
    """The route's request: path holes filled (``target`` for the addressed one), every
    required parameter valid (``full``: every optional one too, so an update writes),
    ``set_`` overriding values (a path hole's too), ``fields`` overriding a query, body
    or form parameter's only (a body field named as a path hole -- ``slug`` on ``PUT
    /pages/{slug}`` -- leaves the path alone), ``drop`` left out, ``extra`` added to a
    JSON object body."""
    holes = dict(set_ or {})  # path holes
    values = {**holes, **(fields or {})}  # query, body and form parameters
    # the other holes as the addressed row goes with them (its parent, a linked row)
    holes = {**_consistent(r, ctx, target), **holes} if target is not None else holes
    path = r.c.path
    for name, p in r.path_params.items():
        if name in holes:
            v = holes[name]
        elif name == r.addr and target is not None:
            v = _lookup_value(r, ctx, p, target)
        else:
            v = _valid(p, ctx)
        path = path.replace("{" + name + "}", str(v))
    params: dict[str, Any] = {}
    form: dict[str, Any] = {}
    body: dict[str, Any] = {}
    has_body = False
    for p in r.c.params:
        if p.source == "path":
            continue
        if p.source == "body":
            has_body = True
        if p.name == drop or (not p.required and not full and p.name not in values):
            continue
        v = values[p.name] if p.name in values else _valid(p, ctx)
        if p.source == "query":
            params[p.name] = v
        elif p.source == "form":
            form[p.name] = v
        else:
            _put(body, p.name, v)
    if extra and has_body:
        for k, v in extra.items():
            body.setdefault(k, v)
    return _Req(path, params or None, body if has_body else None, form or None)


class _Probes:
    """Builds the probes of one spec, naming them uniquely."""

    def __init__(self, spec: Spec, ctx: ProbeContext, items: Mapping[str, RuleItem]) -> None:
        self.spec = spec
        self.ctx = ctx
        self.items = items
        self.out: list[Probe] = []
        self.names: set[str] = set()

    def add(
        self,
        r: _Route,
        who: Caller | str | None,
        what: str,
        items: Sequence[str],
        req: _Req,
        expect: str,
        *,
        token: str | None = None,
        headers: Mapping[str, str] | None = None,
        cookies: Mapping[str, str] | None = None,
        **args: Any,
    ) -> str | None:
        """Add the probe (none when it bears on no selected item); its name."""
        items = tuple(i for i in dict.fromkeys(items) if i in self.items)
        if not items:
            return None
        label = who.handle if isinstance(who, Caller) else (who or "no caller")
        base = f"{r.c.endpoint} | {label} | {what}"
        name, k = base, 2
        while name in self.names:
            name, k = f"{base} #{k}", k + 1
        self.names.add(name)
        self.out.append(
            Probe(
                name=name,
                route=r.c.endpoint,
                items=items,
                method=r.c.method,
                path=req.path,
                expect=expect,
                user=who.handle if isinstance(who, Caller) and token is None else None,
                token=token,
                params=req.params,
                json=req.json,
                form=req.form,
                args=args,
                headers=headers,
                cookies=cookies,
            )
        )
        return name


def _targets(r: _Route, ctx: ProbeContext, caller: Caller | None, n: int = 2) -> list[Any]:
    """Up to ``n`` keys of the addressed table: the caller's own rows first."""
    if r.addr is None:
        return [None]
    p = r.path_params[r.addr]
    table = p.ref or (r.table[0] if r.table else None)
    key = ctx.key_of(table) if table else None
    if not table or not key:
        return [_valid(p, ctx)]
    rows = ctx.rows(table)
    own = ctx.owns(caller.handle, table) if caller is not None else []
    keys = [*own, *(row[key] for row in rows if row[key] not in own)]
    return keys[:n] or [_valid(p, ctx)]


def _auth_errors(c: Contract, when: str) -> list[str]:
    return [i for i, e in _error_ids(c) if e.when == when]


def _basic(login: str, password: str) -> dict[str, str]:
    import base64

    raw = base64.b64encode(f"{login}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


IDENT_PARAM = re.compile(r"(^|_)(email|username|user|login|identifier|phone)($|_)", re.I)


def takes_credentials(c: Contract) -> bool:
    """Whether the route's body is credentials (a login: a user's email or username and
    a password; a token refresh: a refresh token): its required body and form
    parameters are all identifiers or secrets, one at least a secret. Probes invent
    their values, so the route answers the credentials, not the caller."""
    body = [p for p in c.params if p.source in ("body", "form") and p.required]
    return (
        bool(body)
        and any(SECRET_PARAM.search(p.name) for p in body)
        and all(SECRET_PARAM.search(p.name) or IDENT_PARAM.search(p.name) for p in body)
    )


def _r1(b: _Probes, r: _Route) -> None:
    """R1 probes. On a Public route, a malformed token's and the inactive caller's
    refusals decide nothing (``credential`` probes): an app may refuse bad credentials
    on every request (DRF does) -- the auth layer's choice, not the route's rule. On the
    login route taking HTTP Basic credentials (``ProbeContext.login_route``), callers
    present their login and password instead of a token. A route whose body is
    credentials (:func:`takes_credentials`: a login, a token refresh) answers the
    probes' invented credentials, so each caller presents its own instead (anonymous:
    an active user's, still with no token): its login and password, and what its login
    answered (``ProbeContext.answers``: a refresh token, by the parameter's name; the
    cookies the login set). On such a route, and on the JSON login route
    (``ProbeContext.json_login``), only an admission decides: a refusal may answer
    credentials the probe could not present."""
    c, ctx, api = r.c, b.ctx, b.ctx.api
    if c.auth is None:
        return
    roles = allowed_roles(c.auth, api)
    public = roles is None
    basic = ctx.login_route == (c.method, c.path)
    item = f"R1 {c.endpoint}"
    req = _build(r, ctx, target=_targets(r, ctx, None, 1)[0])
    deny = _auth_errors(c, "unauthenticated")
    invented = takes_credentials(c)
    json_login = ctx.json_login is not None and ctx.json_login[:2] == (c.method, c.path)

    owned = invented or json_login  # a credential route: only an admission decides

    def present(base: _Req, handle: str | None) -> tuple[_Req, dict[str, str] | None]:
        """The request with the handle's own credentials in its body, and its cookies."""
        if not owned or handle is None or handle not in ctx.spec.users:
            return base, None
        login, password = ctx.spec.users[handle]
        got = ctx.answers.get(handle, {})
        values: dict[str, Any] = {}
        if json_login:
            _, _, user_field, password_field = ctx.json_login  # type: ignore[misc]
            values |= {user_field: login, password_field: password}
        for p in c.params:
            if p.source not in ("body", "form") or p.name in values:
                continue
            if p.name in got:
                values[p.name] = got[p.name]
            elif re.search(r"pass|pwd", p.name, re.I):
                values[p.name] = password
            elif IDENT_PARAM.search(p.name):
                values[p.name] = login
        form = {**(base.form or {}), **{k: v for k, v in values.items() if k in (base.form or {})}}
        body = {**(base.json if isinstance(base.json, Mapping) else {}),
                **{k: v for k, v in values.items() if k not in form}}  # fmt: skip
        req = _Req(base.path, base.params, body if body else base.json, form or base.form)
        return req, dict(ctx.cookies.get(handle, {})) or None

    someone = next((x.handle for x in ctx.callers.values() if x.active and x.handle in ctx.tokens
                    and x.role != api.admin_role), None)  # fmt: skip
    anon, anon_cookies = present(req, someone)
    addressed = r.addr is not None
    b.add(r, None, "anonymous", [item, *deny], anon, "auth", caller="anonymous", allowed=public,
          credential=owned, cookies=anon_cookies, addressed=addressed)  # fmt: skip
    b.add(
        r,
        "malformed token",
        "call",
        [item, *deny],
        req,
        "auth",
        token=BAD_TOKEN,
        headers=_basic("nobody@example.com", "not-a-valid-password") if basic else None,
        caller="malformed token",
        allowed=public,
        credential=public or owned,
        addressed=addressed,
    )
    inactive_headers = None
    if basic and ctx.inactive is not None and ctx.inactive.handle in ctx.spec.users:
        inactive_headers = _basic(*ctx.spec.users[ctx.inactive.handle])
    if ctx.inactive_token is not None or inactive_headers is not None:
        b.add(
            r,
            "inactive",
            "call",
            [item, *_auth_errors(c, "inactive")],
            req,
            "auth",
            token=ctx.inactive_token or BAD_TOKEN,
            headers=inactive_headers,
            caller="inactive",
            allowed=public,
            credential=public or owned,
            addressed=addressed,
        )
    for role in sorted(x for x in api.roles if x != "Public"):
        # two callers of each role (the second on one target): callers of one role that
        # answer differently show a distinction the profile's roles miss (_r1_verdict)
        of_role = [x for x in ctx.with_token() if x.role == role][:2]
        for nth, caller in enumerate(of_role):
            _r1_caller(b, r, item, role, caller, roles, public, basic, present, 1 if nth else 2)


def _r1_caller(
    b: _Probes,
    r: _Route,
    item: str,
    role: str,
    caller: Caller,
    roles: set[str] | None,
    public: bool,
    basic: bool,
    present: Callable[[_Req, str | None], tuple[_Req, dict[str, str] | None]],
    n: int,
) -> None:
    """One role caller's R1 probes, on up to ``n`` targets (its own rows first), with
    every parameter an update takes (so an admitted update shows its write); and, on a
    route addressing a row, one on a missing row: no row to own, so a 403 there -- while
    another caller gets past it -- is the role's refusal (:func:`_resolve_r1`)."""
    c, ctx = r.c, b.ctx
    owned = takes_credentials(c) or (
        ctx.json_login is not None and ctx.json_login[:2] == (c.method, c.path)
    )
    ok = public or role in roles  # type: ignore[operator]
    extra = [] if ok else _auth_errors(c, "forbidden")
    headers = _basic(*ctx.spec.users[caller.handle]) if basic else None
    table = r.table[0] if r.table is not None else None
    for i, target in enumerate(_targets(r, ctx, caller, n)):
        owns = table is not None and r.addr is not None and target in ctx.owns(caller.handle, table)
        # every parameter of an update (its write then shows) -- not on a credential
        # route: an optional secret invented there is answered, not the caller
        full = c.kind == "update" and not owned
        mine, cookies = present(_build(r, ctx, target=target, full=full,
                                       fields=_changing(r, ctx, target) if full else None),
                                caller.handle)  # fmt: skip
        b.add(
            r,
            caller,
            f"as {role}" + (f" target {target}" if target is not None else ""),
            [item, *extra],
            mine,
            "auth",
            headers=headers,
            cookies=cookies,
            caller=role,
            handle=caller.handle,
            allowed=ok,
            owns_target=owns or r.addr is None,
            # the request every caller of the role sends alike (no row addressed): its
            # answers compare across callers (_split_roles)
            same_request=r.addr is None,
            target_index=i,
            credential=owned,
            addressed=r.addr is not None,
            table=table,
            target=target if r.addr is not None else None,
            owner_known=_owner_known(ctx, table),
        )
    if r.addr is not None and not owned and not basic:
        missing = _missing_value(r.path_params[r.addr])
        req, cookies = present(_build(r, ctx, set_={r.addr: missing}, full=c.kind == "update"),
                               caller.handle)  # fmt: skip
        b.add(r, caller, f"as {role} on a missing row", [item, *extra], req, "auth",
              cookies=cookies, caller=role, handle=caller.handle, allowed=ok, owns_target=True,
              addressed=True, missing_row=True, credential=False)  # fmt: skip


def _changing(r: _Route, ctx: ProbeContext, target: Any) -> dict[str, Any]:
    """Body values an update of the row ``target`` sends that differ from what the row
    holds (a flag flipped, a number moved within its bounds, another listed value): an
    update that got through then shows its write."""
    if r.c.kind != "update" or r.table is None or target is None:
        return {}
    key = ctx.key_of(r.table[0])
    row = next((x for x in ctx.rows(r.table[0]) if key and _k(x.get(key)) == _k(target)), None)
    if row is None:
        return {}
    out: dict[str, Any] = {}
    schema = r.table[2]
    for p in r.c.params:
        if p.source not in ("body", "form"):
            continue
        col = p.name.rsplit(".", 1)[-1]
        if col not in row:
            continue
        v, now = _valid(p, ctx), row[col]
        if p.one_of:
            # the row's value as listed (an enum stored by its member's name)
            listed = dict(schema.one_of).get(col, ())
            spelled = dict(schema.one_of_stored).get(col, ())
            if now in spelled and len(listed) == len(spelled):
                now = listed[spelled.index(now)]
        if isinstance(v, bool) and bool(now) == v:
            out[p.name] = not v
        elif p.one_of and str(now) == str(v):
            out[p.name] = next((x for x in p.one_of if x != str(now)), v)
        elif isinstance(v, int | float) and not isinstance(v, bool) and now == v:
            up = v + 1
            out[p.name] = up if p.le is None or up <= p.le else v - 1
        elif isinstance(v, str) and now == v and p.example is None:
            out[p.name] = (v + " x")[: p.max_len] if p.max_len else v + " x"
    return out


def _owner_known(ctx: ProbeContext, table: str | None) -> bool:
    """Whether the profile names who owns a row of ``table`` (an owner column, an owner
    through a parent; a users row is its user's)."""
    found = resolve_table(ctx.spec.profile, table) if table else None
    users = resolve_table(ctx.spec.profile, ctx.api.users_table)
    if found is None:
        return False
    return bool(found[2].owner or found[2].owner_via) or (users is not None
                                                          and found[0] == users[0])  # fmt: skip


def _missing_value(p: ContractParam) -> Any:
    """A value of the path parameter no seeded row has."""
    return MISSING_INT if p.scalar is Scalar.INT else "00000000-0000-4000-8000-000000000000"


def _others(r: _Route, ctx: ProbeContext, caller: Caller) -> dict[str, Any]:
    """Path parameters naming a parent row the caller has no part in: another user
    (``/users/{id}/posts``), or a row another user owns."""
    out: dict[str, Any] = {}
    users = ctx.api.users_table
    for name, p in r.path_params.items():
        if name == r.addr or p.ref is None:
            continue
        key = ctx.key_of(p.ref)
        if key is None:
            continue
        found = resolve_table(ctx.spec.profile, p.ref)
        if found is not None and found[0] == users:
            other = next((x.key for x in ctx.callers.values() if x.key != caller.key), None)
        else:
            other = next((row[key] for row in ctx.rows(p.ref)
                          if ctx.owner(p.ref, row) not in (None, caller.key)), None)  # fmt: skip
        if other is not None:
            out[name] = other
    return out


def _not_owned_errors(c: Contract) -> list[tuple[str, ContractError]]:
    return [(i, e) for i, e in _error_ids(c) if e.when == "not_owned"]


def _ownership(b: _Probes, r: _Route) -> None:
    """R2 and T1 (owner / owner_via) probes through one route. ``owner_or_public(<c>)``:
    a read of another user's row answers when its ``<c>`` is true and is refused when
    false; a write is refused either way (a public row first). ``owner_or_admin``: as
    ``owner`` for non-admins, and the admin reaches another user's row (a list: holds
    one). A route on a link table between users: :func:`_link_ownership`."""
    c, ctx = r.c, b.ctx
    form, col = scope_parts(r.scope)
    scopes = ("owner", "any", "owner_or_public", "owner_or_admin")
    if r.table is None or form not in scopes or c.kind is None:
        return
    sides = _link_sides(r.table[2], r.scope) if form == "owner" else None
    if sides is not None:
        _link_ownership(b, r, *sides)
        return
    if form == "owner_or_admin":
        _admin_reaches(b, r)
    owned = form in ("owner", "owner_or_public", "owner_or_admin")
    public = col if form == "owner_or_public" else None
    reading = c.method == "GET" or c.kind in ("read", "list")
    table = r.table[0]
    t1 = f"T1 {table}" if owned else None
    r2 = f"R2 {c.endpoint}"
    attackers = sorted(ctx.non_admin(c), key=lambda x: not ctx.owns(x.handle, table))[:2]
    key = ctx.key_of(table)
    rows = ctx.rows(table)
    if c.kind in ("read", "update", "delete", "action") and r.addr is not None and key:
        # an action on one row (tagging a note) acts on the caller's own rows only too
        for a in attackers:
            others = [row for row in rows if ctx.owner(table, row) not in (None, a.key)]
            if public is None:
                victims = [(row[key], "", "not_owned" if owned else "reaches")
                           for row in others[:1]]  # fmt: skip
            else:
                shown = next((row[key] for row in others if row.get(public)), None)
                hidden = next((row[key] for row in others if not row.get(public)), None)
                victims = [(hidden, "private ", "not_owned"),
                           (shown, "public ", "reaches" if reading else "not_owned")]  # fmt: skip
                if not reading:  # a public row first: being public never opens a write
                    victims.reverse()
            for victim, label, expect in victims:
                if victim is None:
                    continue
                req = _build(
                    r, ctx, target=victim, full=c.kind == "update", fields=_changing(r, ctx, victim)
                )
                what = f"another user's {label}{table} {victim}"
                # the same request as the row's owner: the row is reachable there
                witness = _witness(b, r, _row_owner(ctx, r, table, victim, a), what,
                                   [t1 or "", r2], req)  # fmt: skip
                if expect == "not_owned":
                    stated = _not_owned_errors(c)
                    b.add(
                        r,
                        a,
                        what,
                        [t1 or "", r2, *(i for i, _ in stated)],
                        req,
                        "not_owned",
                        statuses=[e.status for _, e in stated if e.status] or list(NOT_OWNED),
                        details={i: [e.status, e.detail] for i, e in stated},
                        witness=witness,
                        table=table,
                        target=victim,
                    )
                else:
                    b.add(r, a, what, [t1 or "", r2], req, "reaches", witness=witness,
                          table=table, target=victim)  # fmt: skip
        if c.kind == "action" and owned:
            _child_stamps(b, r, table, attackers)
        return
    if c.kind in ("list", "read") and form == "any":
        # stated: the route shows anyone's rows -- a non-admin's answer holding a row
        # another user owns confirms it (never probed as if scoped to the caller)
        for a in attackers:
            if c.kind == "list":
                req = _build(r, ctx, target=_targets(r, ctx, a, 1)[0], set_=_others(r, ctx, a))
                what = f"other users' {table} rows"
                witness = _witness(b, r, _other_caller(ctx, c, a), what, [r2], req)
                b.add(r, a, what, [r2], req, "others_rows", table=table, caller=a.key,
                      witness=witness)  # fmt: skip
        return
    if c.kind in ("list", "read"):
        for a in attackers:
            if ctx.owns(a.handle, table) or c.kind == "list":
                req = _build(r, ctx, target=_targets(r, ctx, a, 1)[0])
                what = f"own {table} rows only" + (f" (or {public})" if public else "")
                # the same request as another caller: a different answer shows the
                # caller, not the request, selects the rows
                witness = _witness(b, r, _other_caller(ctx, c, a), what, [t1 or "", r2], req)
                b.add(
                    r,
                    a,
                    what,
                    [t1 or "", r2],
                    req,
                    "owned_rows",
                    table=table,
                    caller=a.key,
                    owns=bool(ctx.owns(a.handle, table)),
                    public=public,
                    witness=witness,
                )
        return
    if c.kind in ("update", "delete"):
        for a in attackers:
            if ctx.owns(a.handle, table):
                req = _build(r, ctx, full=True)
                what = f"writes own {table} rows only"
                witness = _witness(b, r, _other_caller(ctx, c, a), what, [t1 or "", r2], req)
                b.add(r, a, what, [t1 or "", r2], req, "writes_own", table=table,
                      caller=a.key, witness=witness)  # fmt: skip
        return
    if c.kind == "create" and owned:
        schema = r.table[2]
        stamp = schema.owner
        keyed = any(stamp and stamp in k for k in schema.keys)
        # a caller owning no row first: a table with one row per user refuses a second
        pool = sorted(ctx.non_admin(c), key=lambda x: (not keyed, bool(ctx.owns(x.handle, table))))
        if not pool:
            return
        a = pool[0]
        other = next((x.key for x in ctx.callers.values() if x.key != a.key), None)
        extra = {stamp: other} if stamp and other is not None else None
        children = [
            f"T1 {name}"
            for name, s in tables_of(b.spec.profile)[0].items()
            if s.owner_via and s.owner_via[1] == table
        ]
        # with the body naming another user as owner, and without (an app refusing
        # unknown fields answers the first 400: it then says nothing)
        basic = ctx.login_route == (c.method, c.path) and a.handle in ctx.spec.users
        for what, more in (("", extra), (" (body names no owner)", None)):
            if what and extra is None:
                continue
            b.add(
                r,
                a,
                f"create stamps the caller as owner of {table}{what}",
                [t1 or "", r2, *children],
                _build(r, ctx, extra=more),
                "stamps",
                # a login route taking HTTP Basic credentials: the caller's own
                headers=_basic(*ctx.spec.users[a.handle]) if basic else None,
                table=table,
                caller=a.key,
                children=[ch.split(" ", 1)[1] for ch in children],
            )


def _admin_reaches(b: _Probes, r: _Route) -> None:
    """``owner_or_admin``'s admin half: the admin reaches another user's row (not the
    not-owned answer), and a list as the admin holds another user's row."""
    c, ctx = r.c, b.ctx
    table = r.table[0]  # type: ignore[index]
    admin = next((x for x in ctx.allowed(c) if x.role == ctx.api.admin_role), None)
    key = ctx.key_of(table)
    if admin is None or not key:
        return
    r2 = f"R2 {c.endpoint}"
    if c.kind in ("read", "update", "delete", "action") and r.addr is not None:
        victim = next((row[key] for row in ctx.rows(table)
                       if ctx.owner(table, row) not in (None, admin.key)), None)  # fmt: skip
        if victim is not None:
            req = _build(
                r, ctx, target=victim, full=c.kind == "update", fields=_changing(r, ctx, victim)
            )
            what = f"another user's {table} {victim}"
            witness = _witness(b, r, _row_owner(ctx, r, table, victim, admin), what, [r2], req)
            b.add(r, admin, what, [r2], req, "reaches", witness=witness, table=table,
                  target=victim, admin_half=True)  # fmt: skip
    elif c.kind == "list":
        req = _build(r, ctx, target=_targets(r, ctx, admin, 1)[0], set_=_others(r, ctx, admin))
        what = f"other users' {table} rows"
        witness = _witness(b, r, _other_caller(ctx, c, admin), what, [r2], req)
        b.add(r, admin, what, [r2], req, "others_rows", table=table, caller=admin.key,
              witness=witness, admin_half=True)  # fmt: skip


def _row_owner(ctx: ProbeContext, r: _Route, table: str, key: Any, not_: Caller
               ) -> Caller | None:  # fmt: skip
    """The caller (with a token, whom the route admits) owning the row ``key`` of
    ``table``, else the admin; never ``not_``."""
    col = ctx.key_of(table) or ""
    found = next((row for row in ctx.rows(table) if _k(row.get(col)) == _k(key)), None)
    owner = ctx.owner(table, found) if found is not None else None
    allowed = [x for x in ctx.allowed(r.c) if x.handle != not_.handle]
    return next((x for x in allowed if owner is not None and _k(x.key) == _k(owner)),
                next((x for x in allowed if x.role == ctx.api.admin_role), None))  # fmt: skip


def _other_caller(ctx: ProbeContext, c: Contract, a: Caller) -> Caller | None:
    """Another caller the route admits (a non-admin first)."""
    return next((x for x in ctx.allowed(c) if x.handle != a.handle), None)


def _witness(b: _Probes, r: _Route, who: Caller | None, what: str, items: Sequence[str],
             req: _Req) -> str | None:  # fmt: skip
    """The same request as ``who`` (a witness: judged on no item; the probes it backs
    read its answer, :class:`_Evidence`); its name, or None."""
    if who is None:
        return None
    return b.add(r, who, f"witness: {what}", items, req, "witness")


def _user_hole(r: _Route, ctx: ProbeContext) -> str | None:
    """The path parameter naming another user on a link route (``/me/following/{id}``):
    the one whose ``ref`` is the users table, else the route's only path parameter
    when it names no table."""
    users = resolve_table(ctx.spec.profile, ctx.api.users_table)
    for name, p in r.path_params.items():
        found = resolve_table(ctx.spec.profile, p.ref) if p.ref else None
        if found is not None and users is not None and found[0] == users[0]:
            return name
    if len(r.path_params) == 1:
        name, p = next(iter(r.path_params.items()))
        return name if p.ref is None else None
    return None


def _user_keys(ctx: ProbeContext) -> list[Any]:
    key = ctx.key_of(ctx.api.users_table)
    return [row[key] for row in ctx.rows(ctx.api.users_table)] if key else []


def _link_ownership(b: _Probes, r: _Route, side: str, other: str) -> None:
    """R2 and T1 probes through a route on a link table between users, found by its
    whole key: the caller's ``side`` column, the ``other`` side's. A list holds only the
    users (or links) on the caller's side; a create adds links on the caller's side
    only; a delete (or update) removes or changes only the caller's side's links --
    probed on a link of the caller's own and on one of another user to the caller that
    the caller does not mirror (``/me/following/{id}`` must not touch ``id``'s link to
    the caller)."""
    c, ctx = r.c, b.ctx
    assert r.table is not None
    table = r.table[0]
    t1, r2 = f"T1 {table}", f"R2 {c.endpoint}"
    links = {(_k(row[side]), _k(row[other])) for row in ctx.rows(table)}
    attackers = ctx.non_admin(c)[:2]
    hole = _user_hole(r, ctx)
    if c.kind in ("list", "read") and hole is None:
        for a in attackers:
            what = f"own {table} links only ({side} is the caller)"
            witness = _witness(b, r, _other_caller(ctx, c, a), what, [t1, r2], _build(r, ctx))
            b.add(r, a, what, [t1, r2], _build(r, ctx), "link_rows", table=table, side=side,
                  other=other, caller=a.key, witness=witness)  # fmt: skip
        return
    if hole is None:
        return
    users = _user_keys(ctx)
    for a in attackers:
        me = _k(a.key)
        if c.kind == "create":
            fresh = [u for u in users if _k(u) != me and (me, _k(u)) not in links]
            fresh.sort(key=lambda u: (_k(u), me) in links)  # untouched pairs first
            if fresh:
                b.add(r, a, f"links it adds are its own ({side} is the caller)", [t1, r2],
                      _build(r, ctx, set_={hole: fresh[0]}), "stamps", table=table,
                      caller=a.key, column=side)  # fmt: skip
            continue
        own = [u for u in users if (me, _k(u)) in links]
        theirs = [u for u in users if (_k(u), me) in links and (me, _k(u)) not in links]
        for label, target in (("own side", own), ("other side", theirs)):
            if not target:
                continue
            pair = (a.key, target[0]) if label == "own side" else (target[0], a.key)
            what = f"{label}: {table} link ({side}={pair[0]}, {other}={pair[1]})"
            req = _build(r, ctx, set_={hole: target[0]}, full=True)
            witness = _witness(b, r, _other_caller(ctx, c, a), what, [t1, r2], req)
            b.add(r, a, what, [t1, r2], req, "writes_own", table=table, caller=a.key,
                  link=[side, other], witness=witness)  # fmt: skip


def _child_stamps(b: _Probes, r: _Route, table: str, callers: Sequence[Caller]) -> None:
    """An action on the caller's own row that adds rows of a table owned through it (a
    note's tag links): the new rows' owner is the caller."""
    ctx = b.ctx
    children = [f"T1 {name}" for name, s in tables_of(b.spec.profile)[0].items()
                if s.owner_via and s.owner_via[1] == table]  # fmt: skip
    if not children:
        return
    for a in callers:
        own = ctx.owns(a.handle, table)
        if own:
            b.add(r, a, f"rows it adds through its own {table} {own[0]} are its own",
                  children, _build(r, ctx, target=own[0]), "stamps", table=table,
                  caller=a.key, children=[ch.split(" ", 1)[1] for ch in children],
                  children_only=True)  # fmt: skip
            return


def _shared_and_users(b: _Probes, routes: Sequence[_Route]) -> None:
    ctx, api = b.ctx, b.ctx.api
    tables, _ = tables_of(b.spec.profile)
    for name, schema in tables.items():
        item = f"T1 {name}"
        if name == api.users_table:
            for r in routes:
                if r.table and r.table[0] == name and r.c.kind == "read" and not r.path_params:
                    for caller in ctx.allowed(r.c):
                        b.add(
                            r,
                            caller,
                            "me is the caller",
                            [item],
                            _build(r, ctx),
                            "is_caller",
                            caller=caller.key,
                            key=ctx.key_of(name),
                            login=ctx.spec.users.get(caller.handle, (None,))[0],
                        )
        elif not schema.owner and not schema.owner_via:
            for r in routes:
                if r.table and r.table[0] == name and r.c.kind == "read" and r.addr:
                    reader = next(iter(ctx.non_admin(r.c)), None)
                    target = _targets(r, ctx, None, 1)[0]
                    if reader is None and r.c.auth == "Public":
                        b.add(
                            r,
                            None,
                            f"reads {name} {target}",
                            [item],
                            _build(r, ctx, target=target),
                            "ok",
                        )
                    elif reader is not None:
                        b.add(
                            r,
                            reader,
                            f"reads {name} {target}",
                            [item],
                            _build(r, ctx, target=target),
                            "ok",
                        )
            for r in routes:
                if r.table and r.table[0] == name and r.c.kind in SHARED_WRITE_KINDS:
                    _shared_write_probes(b, r, item)


def _spread_targets(r: _Route, ctx: ProbeContext, n: int) -> list[Any]:
    """Up to ``n`` keys of the addressed table, spread over the rows' reference columns
    (``*_id`` besides the key: a row's vendor, author...) -- the profile names no owner,
    so rows differing there are likelier to differ in an owner the app checks."""
    if r.addr is None or r.table is None:
        return _targets(r, ctx, None, n)
    table = r.path_params[r.addr].ref or r.table[0]
    key = ctx.key_of(table)
    rows = ctx.rows(table)
    if not key or not rows:
        return _targets(r, ctx, None, n)
    refs = sorted({c for row in rows for c in row if c != key and c.endswith("_id")})
    picked: list[Any] = []
    seen: set[tuple] = set()
    for row in rows:
        sig = tuple(_k(row.get(c)) for c in refs)
        if sig not in seen:
            seen.add(sig)
            picked.append(row[key])
    for row in rows:  # fewer distinct rows than n: the first others
        if len(picked) >= n:
            break
        if row[key] not in picked:
            picked.append(row[key])
    return picked[:n]


def _shared_write_probes(b: _Probes, r: _Route, item: str) -> None:
    """T1 ``shared`` through a route updating or deleting a row of the table: the same
    write of the same row (up to two rows) as every non-admin caller the route admits
    (a Public route without any: once, with no caller). The probes of one row form a
    *group*: each caller's write is the others' control (:func:`_judge`,
    ``shared_write``)."""
    ctx = b.ctx
    if r.addr is None or r.table is None:
        return
    writers: list[Caller | None] = list(ctx.non_admin(r.c))
    if not writers and r.c.auth == "Public" and not ctx.with_token():
        writers = [None]
    if not writers:
        return
    for target in _spread_targets(r, ctx, 2):
        group = f"{r.c.endpoint} | {target}"
        req = _build(r, ctx, target=target, full=r.c.kind == "update",
                     fields=_changing(r, ctx, target))  # fmt: skip
        for w in writers:
            b.add(r, w, f"{r.c.kind}s {r.table[0]} {target} (shared)", [item], req,
                  "shared_write", table=r.table[0], target=target, group=group)  # fmt: skip


def _writer(r: _Route, ctx: ProbeContext) -> Caller | None:
    if r.c.auth == "Public" and not ctx.with_token():
        return None
    found = ctx.allowed(r.c)
    return found[0] if found else None


def _target_for(r: _Route, ctx: ProbeContext, caller: Caller | None) -> Any:
    return _targets(r, ctx, caller, 1)[0]


def _params_on(r: _Route, column: str) -> list[ContractParam]:
    return [p for p in r.c.params if p.column == column and p.source != "path"]


MAX_EMAIL = 254  # the longest email address (RFC 5321's path limit), as validators check


def _bounds_probes(b: _Probes, routes: Sequence[_Route]) -> dict[str, str]:
    """T3 and T4 probes; returns the reason of each bound no valid value can reach."""
    ctx = b.ctx
    reasons: dict[str, str] = {}
    for r in routes:
        if r.c.auth is None:
            continue
        caller = _writer(r, ctx)
        if caller is None and r.c.auth != "Public":
            continue
        target = _target_for(r, ctx, caller)
        invalid = [i for i, e in _error_ids(r.c) if e.when == "invalid"]
        # the same request with every value plain and valid: a boundary value's or a
        # listed value's refusal decides only when this one gets through (the request
        # may be refused for a reason of its own: the router, a role, another field)
        mine = [f"T3 {col[0]}.{col[1]} {op} {_num(bound)}" for p in r.c.params
                if p.source != "path" and (col := _column(b.spec, p.column) if p.column else None)
                for op, bound in _bounds(p)]  # fmt: skip
        mine += [f"T4 {col[0]}.{col[1]}" for p in r.c.params
                 if p.source != "path" and p.one_of
                 and (col := _column(b.spec, p.column) if p.column else None)]  # fmt: skip
        control = b.add(r, caller, "control: plain valid values", mine,
                        _build(r, ctx, target=target), "control") if mine else None  # fmt: skip
        for p in r.c.params:
            col = _column(b.spec, p.column) if p.column else None
            if col is None or p.source == "path":
                continue
            for op, bound in _bounds(p):
                item = f"T3 {col[0]}.{col[1]} {op} {_num(bound)}"
                if op == "max_len" and "email" in p.name.lower() and bound > MAX_EMAIL:
                    reasons[item] = (
                        f"{UNREACHABLE}: an email address has at most {MAX_EMAIL} characters, "
                        f"so no valid value reaches the bound {_num(bound)}"
                    )
                    continue
                inside, beyond = _edges(p, op, bound)
                if inside is None or beyond is None:
                    reasons[item] = (
                        f"{UNREACHABLE}: no value of {p.name}'s format ({p.example!r}) has "
                        f"{_num(bound if inside is None else bound + 1)} characters"
                    )
                    continue
                boundary = b.add(
                    r,
                    caller,
                    f"{p.name} {op} boundary {inside!r}",
                    [item],
                    _build(r, ctx, target=target, fields={p.name: inside}),
                    "ok2xx",
                    control=control,
                )
                b.add(
                    r,
                    caller,
                    f"{p.name} {op} beyond {beyond!r}",
                    [item, *invalid],
                    _build(r, ctx, target=target, fields={p.name: beyond}),
                    "invalid",
                    errors={i: _status_detail(r.c, i) for i in invalid},
                    control=boundary,
                )
            if p.one_of:
                item = f"T4 {col[0]}.{col[1]}"
                for v in p.one_of:
                    b.add(
                        r,
                        caller,
                        f"{p.name} {v!r} accepted",
                        [item],
                        _build(r, ctx, target=target, fields={p.name: v}),
                        "accepted",
                        control=control,
                    )
                b.add(
                    r,
                    caller,
                    f"{p.name} unlisted",
                    [item, *invalid],
                    _build(r, ctx, target=target, fields={p.name: UNLISTED}),
                    "invalid",
                    errors={i: _status_detail(r.c, i) for i in invalid},
                    control=control,
                )
    return reasons


def _status_detail(c: Contract, item_id: str) -> tuple[int | None, str | None]:
    e = dict(_error_ids(c))[item_id]
    return e.status, e.detail


def _keys_probes(b: _Probes, routes: Sequence[_Route]) -> dict[str, str]:
    """T5 probes; returns the reason of each key no probe can repeat."""
    ctx = b.ctx
    reasons: dict[str, str] = {}
    tables, _ = tables_of(b.spec.profile)
    for name, schema in tables.items():
        creates = [r for r in routes if r.table and r.table[0] == name and r.c.kind == "create"]
        for key in schema.keys:
            item = f"T5 {name} ({', '.join(key)})"
            made = False
            for r in creates:
                dup = [i for i, e in _error_ids(r.c) if e.when == "duplicate"]
                sides = _link_sides(schema, r.c.owner_scope)
                hole = _user_hole(r, ctx) if sides is not None else None
                if sides is not None and hole is not None and set(key) == set(sides):
                    # a link again: the caller's own link to a user, named in the path
                    side, other = sides
                    for a in ctx.non_admin(r.c):
                        linked = next((row[other] for row in ctx.rows(name)
                                       if _k(row[side]) == _k(a.key)), None)  # fmt: skip
                        if linked is not None:
                            # the control: a link to a user it has none to yet
                            mine = {_k(row[other]) for row in ctx.rows(name)
                                    if _k(row[side]) == _k(a.key)}  # fmt: skip
                            mine.add(_k(a.key))
                            fresh = next((u for u in _user_keys(ctx) if _k(u) not in mine), None)
                            control = None if fresh is None else b.add(
                                r, a, f"control: a new {name} link to {fresh}", [item],
                                _build(r, ctx, set_={hole: fresh}), "control")  # fmt: skip
                            b.add(r, a, f"repeat its {name} link to {linked}", [item, *dup],
                                  _build(r, ctx, set_={hole: linked}), "duplicate", table=name,
                                  errors={i: _status_detail(r.c, i) for i in dup},
                                  control=control)  # fmt: skip
                            made = True
                            break
                    continue
                if schema.owner and set(key) == {schema.owner}:
                    owners = [x for x in ctx.allowed(r.c) if ctx.owns(x.handle, name)]
                    if owners:
                        # the control: the same create by a caller owning no row yet
                        free = next((x for x in ctx.allowed(r.c) if not ctx.owns(x.handle, name)),
                                    None)  # fmt: skip
                        control = None if free is None else b.add(
                            r, free, f"control: a first {name} for {free.handle}", [item],
                            _build(r, ctx), "control")  # fmt: skip
                        b.add(
                            r,
                            owners[0],
                            f"second {name} for the same {schema.owner}",
                            [item, *dup],
                            _build(r, ctx),
                            "duplicate",
                            table=name,
                            errors={i: _status_detail(r.c, i) for i in dup},
                            control=control,
                        )
                        made = True
                    continue
                by_col = {
                    p.column.partition(".")[2]: p
                    for p in r.c.params
                    if p.column
                    and _column(b.spec, p.column)
                    and _column(b.spec, p.column)[0] == name
                }
                rows = ctx.rows(name)
                caller = _writer(r, ctx)
                if set(key) <= set(by_col) and rows and (caller or r.c.auth == "Public"):
                    values = {by_col[k].name: rows[0][k] for k in key}
                    # the control: the same create with a key no row has
                    fresh = _fresh_key(ctx, name, key, by_col)
                    control = None if fresh is None else b.add(
                        r, caller, f"control: a new {name} ({', '.join(key)})", [item],
                        _build(r, ctx, **_by_source(r, fresh)), "control")  # fmt: skip
                    b.add(
                        r,
                        caller,
                        f"repeat {name} ({', '.join(key)})",
                        [item, *dup],
                        _build(r, ctx, **_by_source(r, values)),
                        "duplicate",
                        table=name,
                        errors={i: _status_detail(r.c, i) for i in dup},
                        control=control,
                    )
                    made = True
            if not made:
                reasons[item] = (
                    f"no route can repeat the key: no create route of {name} takes "
                    f"({', '.join(key)}) or stamps it from the caller"
                    if creates
                    else f"no route creates {name} rows"
                )
    return reasons


def _by_source(r: _Route, values: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``_build``'s overrides for parameter values by name: a path parameter's to the
    path (``set_``), any other's to its parameter alone (``fields``)."""
    path = {p.name for p in r.c.params if p.source == "path"} | set(r.path_params)
    named = {p.name: p.source for p in r.c.params}
    return {
        "set_": {k: v for k, v in values.items() if named.get(k, "path") == "path" and k in path},
        "fields": {k: v for k, v in values.items() if named.get(k) not in (None, "path")},
    }


def _fresh(p: ContractParam, ctx: ProbeContext) -> Any:
    """A valid value of the parameter that differs from its plain one (a key no seeded
    row holds, in the parameter's format)."""
    v = _valid(p, ctx)
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v + 7_000_000 if p.le is None else max(min(v + 7_000_000, int(p.le)), v)
    if isinstance(v, float):
        return v + 7_000.5 if p.le is None else max(min(v + 7_000.5, float(p.le)), v)
    if not isinstance(v, str):
        return v
    if "@" in v:
        return "fresh." + v
    if URL_SHAPE.match(v):
        return v.rstrip("/") + "/fresh"
    if FIXED_SHAPES[0].match(v):
        return "192.0.2.77"
    if FIXED_SHAPES[1].match(v):
        return "11111111-2222-4333-8444-555555555555"
    text = "fresh-" + re.sub(r"\s+", "-", v)
    return text[: p.max_len] if p.max_len is not None else text


def _fresh_key(
    ctx: ProbeContext, table: str, key: Sequence[str], by_col: Mapping[str, ContractParam]
) -> dict[str, Any] | None:
    """Values of the key's parameters that no row of ``table`` holds together: a row's
    key for a parameter naming a table (``ref``), a listed value, else a fresh one."""
    import itertools

    taken = {tuple(_k(r.get(k)) for k in key) for r in ctx.rows(table)}
    options: list[list[Any]] = []
    for col in key:
        p = by_col[col]
        if p.ref is not None:
            rk = ctx.key_of(p.ref)
            options.append([row[rk] for row in ctx.rows(p.ref)] if rk else [])
        elif p.one_of:
            options.append(list(p.one_of))
        else:
            options.append([_fresh(p, ctx)])
    for combo in itertools.islice(itertools.product(*options), 1000):
        if tuple(_k(v) for v in combo) not in taken:
            return {by_col[col].name: v for col, v in zip(key, combo, strict=True)}
    return None


def _error_probes(b: _Probes, routes: Sequence[_Route]) -> dict[str, str]:
    """R3 probes for not_found, invalid and other (with an example) errors; returns
    the reason of each error no probe triggers."""
    ctx = b.ctx
    reasons: dict[str, str] = {}
    for r in routes:
        c = r.c
        caller = _writer(r, ctx)
        for item, e in _error_ids(c):
            if e.status is None:
                continue
            if e.when == "not_found":
                # as the admin when the route admits one: past every role and owner check
                admin = next((x for x in ctx.allowed(c) if x.role == ctx.api.admin_role), None)
                caller = admin or caller
                # the addressed row's id, else the one path parameter naming a table's row
                # (a link route's other user: /me/following/{id})
                hole = r.addr
                if hole is None and len(r.path_params) == 1:
                    only, p = next(iter(r.path_params.items()))
                    hole = only if p.ref is not None else None
                if hole is None:
                    reasons[item] = "unreachable-by-probe: the route addresses no row by id"
                    continue
                p = r.path_params[hole]
                missing = (
                    MISSING_INT
                    if p.scalar is Scalar.INT
                    else "00000000-0000-4000-8000-000000000000"
                )
                b.add(
                    r,
                    caller,
                    "missing id",
                    [item],
                    _build(r, ctx, set_={hole: missing}),
                    "error",
                    status=e.status,
                    detail=e.detail,
                )
            elif e.when == "invalid":
                req = _invalid_request(r, ctx)
                if req is None:
                    reasons[item] = "unreachable-by-probe: the route takes no checked parameter"
                    continue
                b.add(
                    r,
                    caller,
                    "invalid body",
                    [item],
                    req,
                    "error",
                    status=e.status,
                    detail=e.detail,
                )
            elif e.when == "other":
                if e.example is None:
                    reasons[item] = "unreachable-by-probe: no example triggers it"
                    continue
                ex = dict(e.example)
                who = ctx.callers.get(ex["user"]) if ex.get("user") else caller
                req = _build(r, ctx, target=_target_for(r, ctx, who))
                req = _Req(
                    ex.get("path", req.path),
                    ex.get("params", req.params),
                    ex.get("json", req.json),
                    ex.get("form", req.form),
                )
                if c.auth == "Public" and not ex.get("user"):
                    who = None
                b.add(r, who, "example", [item], req, "error", status=e.status, detail=e.detail)
    return reasons


def _invalid_request(r: _Route, ctx: ProbeContext) -> _Req | None:
    target = _target_for(r, ctx, None)
    for p in r.c.params:
        if p.source == "path":
            continue
        if p.one_of:
            return _build(r, ctx, target=target, fields={p.name: UNLISTED})
        bounds = _bounds(p)
        if bounds:
            op, bound = bounds[0]
            return _build(r, ctx, target=target, fields={p.name: _edges(p, op, bound)[1]})
    required = [p for p in r.c.params if p.source in ("body", "form") and p.required]
    if required:
        return _build(r, ctx, target=target, drop=required[0].name)
    return None


def _optional_probes(b: _Probes, routes: Sequence[_Route]) -> None:
    """A route with optional query parameters, called with every one of them (a valid
    value each), as a caller it admits: only its 5xx item judges it (a filter the app
    fails on, as recipe-app-api's ``?tags=``)."""
    ctx = b.ctx
    for r in routes:
        optional = [p for p in r.c.params if p.source == "query" and not p.required]
        if not optional or r.c.auth is None:
            continue
        caller = _writer(r, ctx)
        if caller is None and r.c.auth != "Public":
            continue
        values = {p.name: _valid(p, ctx) for p in optional}
        b.add(r, caller, "every optional query parameter", [f"R3 {r.c.endpoint} 5xx"],
              _build(r, ctx, target=_target_for(r, ctx, caller), set_=values), "any")  # fmt: skip


def build_probes(
    spec: Spec, ctx: ProbeContext, items: Mapping[str, RuleItem], skip: Sequence[str] = ()
) -> tuple[list[Probe], dict[str, str]]:
    """The probes bearing on ``items`` (none through the routes in ``skip``), and the
    reason of each item no probe can reach."""
    routes = [_route(spec, c) for c in route_contracts(spec) if c.endpoint not in skip]
    b = _Probes(spec, ctx, items)
    for r in routes:
        _r1(b, r)
        _ownership(b, r)
    _shared_and_users(b, routes)
    _optional_probes(b, routes)
    reasons = _bounds_probes(b, routes)
    reasons |= _keys_probes(b, routes)
    reasons |= _error_probes(b, routes)
    return b.out, reasons


# ---- judging -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """A probe's bearing on one item: ``ok`` True (confirms), False (contradicts), None
    (neither); for R1, ``refused`` (None: cannot tell). An outcome is *decisive* when it
    confirms or contradicts (R1: when it tells refused from admitted); ``reason`` says
    why, and for one that is not, why the answer has more than one explanation."""

    ok: bool | None
    reason: str = ""
    observed: Any = None
    refused: bool | None = None

    def decisive(self, kind: str) -> bool:
        return (self.refused if kind == "R1" else self.ok) is not None


def _error_key(api: ApiProfile | None) -> str:
    """The key an error body holds its detail under: ``json_key(<key>)``'s key, else
    ``detail``."""
    from lattice.profile import json_key_of

    key = json_key_of(api.error_body) if api is not None else None
    return key or "detail"


def _detail(body: Any, api: ApiProfile | None = None) -> Any:
    return body.get(_error_key(api)) if isinstance(body, Mapping) else None


# keys an error envelope states its code under (an app answering every request 200 and
# putting the error in the body: conduit's ``{"code": 404, "msg": ...}``)
ENVELOPE_CODES = ("code", "status", "status_code", "statusCode", "error_code")


def _envelope(
    answer: Answer,
    api: ApiProfile | None,
    c: Contract | None = None,
    refusals: Sequence[tuple[int, str | None]] = (),
) -> int | None:
    """The error a 2xx answer's JSON body states -- an *error envelope*: its code (a
    4xx/5xx under ``code``, ``status``...), else 0 (``success: false``, a non-empty
    ``error``, or the profile's error body holding an error answer the profile or the
    route states); None for a 2xx that states no error, and for any other status."""
    body = answer.body
    if not _ok(answer.status) or not isinstance(body, Mapping):
        return None
    for k in ENVELOPE_CODES:
        v = body.get(k)
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, int) and not isinstance(v, bool) and 400 <= v < 600:
            return v
    if body.get("success") is False or body.get("ok") is False:
        return 0
    err = body.get("error")
    if isinstance(err, str | Mapping | list) and err:
        return 0
    if api is not None:
        key = _error_key(api)
        detail = body.get(key)
        if isinstance(detail, str) and set(body) <= {key, *ENVELOPE_CODES}:
            known = {text for _, text in _auth_answers(api, refusals)}
            known |= {e.detail for e in (c.errors if c is not None else ()) if e.detail}
            if detail in known:
                return 0
    return None


def _reached(
    answer: Answer,
    api: ApiProfile | None,
    c: Contract | None = None,
    refusals: Sequence[tuple[int, str | None]] = (),
) -> bool:
    """A 2xx that is no error envelope (:func:`_envelope`): the request got through."""
    return _ok(answer.status) and _envelope(answer, api, c, refusals) is None


def _effective_status(
    answer: Answer,
    api: ApiProfile | None,
    c: Contract | None = None,
    refusals: Sequence[tuple[int, str | None]] = (),
) -> int | None:
    """The status an answer means: an error envelope's code (None: it states none),
    else the HTTP status."""
    code = _envelope(answer, api, c, refusals)
    if code is None:
        return answer.status
    return code or None


def _row_effect(
    before: Mapping[str, list[dict[str, Any]]],
    after: Mapping[str, list[dict[str, Any]]],
    ctx: ProbeContext,
    table: str | None,
    target: Any,
    kind: str | None,
) -> bool | None:
    """Whether a write to the row ``target`` of ``table`` shows its effect: a delete
    removed it, an update changed it. None: no such prediction (another kind, no key,
    a row the seed does not have)."""
    if table is None or target is None or kind not in ("update", "delete"):
        return None
    found = resolve_table(ctx.spec.profile, table)
    key = _key_column(found[2]) if found is not None else None
    if found is None or key is None:
        return None
    old = next((r for r in before.get(found[1], []) if _k(r.get(key)) == _k(target)), None)
    if old is None:
        return None
    new = next((r for r in after.get(found[1], []) if _k(r.get(key)) == _k(target)), None)
    return new is None if kind == "delete" else new != old


def _past_auth(answer: Answer) -> bool:
    """Whether the request got past the auth check to the route's handler: no refusal
    (401/403), no 405 and no 404 page that is not JSON (the router's)."""
    if answer.status in (401, 403, 405):
        return False
    return not (answer.status == 404 and not _readable(answer.body))


def _readable(body: Any) -> bool:
    """Whether an answer's body could carry a detail: JSON (an object, a list, a
    scalar, or none at all), not a text page."""
    return not isinstance(body, str)


def _k(value: Any) -> Any:
    """A key value in one spelling: a UUID (32 hex digits as SQLAlchemy stores it, or
    dashed as an answer spells it) as its canonical text; anything else as it is."""
    if isinstance(value, str) and len(value) in (32, 36):
        try:
            return str(uuid.UUID(value))
        except ValueError:
            return value
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _ok(status: int) -> bool:
    return 200 <= status < 300


def _changed(before: Mapping[str, list], after: Mapping[str, list], tables: Sequence[str]) -> bool:
    return any(before.get(t) != after.get(t) for t in tables)


def _changed_besides(
    before: Mapping[str, list], after: Mapping[str, list], ctx: ProbeContext, caller: Any
) -> bool:
    """Whether a request changed the database, besides its caller's own users row (an
    app may stamp a caller's last-seen time on every authenticated request)."""
    found = resolve_table(ctx.spec.profile, ctx.api.users_table)
    users = found[1] if found is not None else None
    key = _key_column(found[2]) if found is not None else None
    for t in before:
        if before.get(t) == after.get(t):
            continue
        if t != users or caller is None or key is None:
            return True
        old = {_k(r.get(key)): r for r in before.get(t, [])}
        new = {_k(r.get(key)): r for r in after.get(t, [])}
        if set(old) != set(new):
            return True
        if any(old[k] != new[k] for k in old if k != _k(caller)):
            return True
    return False


def _rows_in(body: Any, schema: Schema, key: str) -> list[Mapping[str, Any]]:
    """The objects in ``body`` that are rows of the table (its key, and mostly its
    columns)."""
    cols = set(schema.names())
    out: list[Mapping[str, Any]] = []

    def walk(v: Any) -> None:
        if isinstance(v, Mapping):
            keys = set(v)
            # mostly its columns (a DRF serializer adds hyperlinks: url, highlight...)
            if key in keys and len(keys & cols) >= 2 and len(keys & cols) >= 0.5 * len(keys):
                out.append(v)
            elif key not in keys and len(keys & cols) >= 2 and isinstance(v.get("url"), str):
                # a row named by its own URL (flasky's "url": "/api/v1/posts/7")
                m = re.search(r"/(\d+)/?$", v["url"])
                if m:
                    out.append({**v, key: int(m.group(1))})
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(body)
    return out


def _auth_answers(
    api: ApiProfile, refusals: Sequence[tuple[int, str | None]] = ()
) -> set[tuple[int | None, str]]:
    """The (status, detail) answers that refuse a caller, status None meaning any: the
    profile's automatic 401/403 details, its inactive answer, its token auth's invalid /
    unknown answers, and ``refusals`` (a front end's recorded auth answers)."""
    from lattice.profile import NativeToken

    out: set[tuple[int | None, str]] = set()
    for detail in (api.forbidden_detail, api.unauthorized_detail):
        if detail:
            out.add((None, detail))
    if api.active_column:
        out.add((api.inactive_status, api.inactive_detail))
    if isinstance(api.auth, NativeToken):
        for answer in (api.auth.invalid, api.auth.unknown):
            if answer is not None:
                out.add((answer[0], answer[1]))
    for status, detail in refusals:
        if detail:
            out.add((status, detail))
    return out


def _refused(
    answer: Answer,
    c: Contract,
    api: ApiProfile,
    owns_target: bool,
    *,
    refusals: Sequence[tuple[int, str | None]] = (),
    no_credential: bool = False,
    addressed: bool = True,
) -> bool | None:
    """Whether an answer refuses the caller (None: cannot tell -- the answer has more
    than one explanation). ``owns_target``: the request addresses no row, or the
    caller's own (``addressed``: it addresses a row at all).

    * A 401 or 403 to a request carrying ``no_credential`` of an active user (anonymous,
      a malformed token, the inactive user's) is a refusal, whatever the route states:
      such a request cannot get past authentication to any data-dependent answer.
    * A 2xx admits -- unless its body is an error envelope (:func:`_envelope`).
    * Never decides: a 5xx (a crash, before or past the check), a 405 (the router's),
      and a 404 to a request addressing a row (the router's -- a path value it does not
      match -- or a row hidden from the caller, by its role or its owner) or one that is
      no JSON (a router's page).
    * An authentication answer (see :func:`_auth_answers`: no or bad credentials, an
      inactive user) is a refusal on any row -- no ownership explains it -- though the
      route also states an error of that status, unless it states one with that very
      detail as a non-auth error, when only a probe on the caller's own row tells.
    * A 403 on a row that is not the caller's own (another user's, or one whose owner
      the profile does not know) is undecided, whatever its detail: an owner check
      answers it as a role check does (DRF's ``IsAdminOrOwner`` and ``IsAdminUser``
      answer the same detail). :func:`_resolve_r1` may decide it from the caller's
      answer on a missing row.
    * Otherwise a 401 the route does not state is a refusal; a 401 the route states as
      a non-auth error cannot be told apart (it never admits); a 403 on the caller's own
      row (or no row) is a refusal when the profile names no forbidden detail -- with
      one named, a 403 of another detail may be a role check's own message or a rule
      past it: undecided. A 403 the route states as a non-auth error admits when it is
      told from the role check's (the stated error's detail, or the profile's forbidden
      detail being another). Any other 4xx admits (past the check: the role check runs
      before the request is read)."""
    status = answer.status
    if no_credential and status in (401, 403):
        return True
    if _ok(status):
        return None if _envelope(answer, api, c, refusals) is not None else False
    if status >= 500 or status == 405:
        return None
    if status == 404 and (addressed or not _readable(answer.body)):
        return None
    detail = _detail(answer.body, api)
    if detail is not None:
        for want, text in _auth_answers(api, refusals):
            if text == detail and want in (None, status) and status >= 400:
                if status == 403 and not owns_target and text == api.forbidden_detail:
                    return None  # the authorization answer: an owner check answers it too
                # an authentication answer (no or bad credentials, an inactive user):
                # no ownership explains it
                same = any(
                    e.when not in AUTH_TRIGGERS and e.status == status and e.detail == detail
                    for e in c.errors
                )
                return (True if owns_target else None) if same else True
    if status == 403 and not owns_target:
        return None
    stated = [
        e
        for e in c.errors
        if e.when not in AUTH_TRIGGERS
        and e.status == status
        and (e.detail is None or e.detail == detail)
    ]
    if status == 401 and stated:
        # a 401 never admits a caller: the route's own 401 (bad credentials it was sent,
        # a token it revokes) cannot be told from a refusal
        return None
    if status == 403 and stated:
        # the route's own 403 -- told from the role check's by a detail of its own, or by
        # the profile's naming the role check's (this answer's detail is another)
        known = api.forbidden_detail is not None or any(e.detail is not None for e in stated)
        return False if known else None
    if status not in (401, 403) or stated:
        return False
    if status == 401:
        return True
    if api.forbidden_detail is not None:
        return None  # a 403 of another detail: a role check's own message, or a rule past it
    return True


def _shut_out(answer: Answer, p: Probe) -> bool:
    """Whether the app refused the probe's caller before its rule: a 401 (the request's
    credentials were not accepted, or there were none), or a 403 to a request carrying
    no credentials at all."""
    anonymous = p.user is None and p.token is None and p.headers is None
    return answer.status == 401 or (anonymous and answer.status == 403)


def _link_rows(body: Any, status: int, ctx: ProbeContext, a: Mapping[str, Any]) -> Outcome:
    """A list through a link table between users, as the caller: the links it answers
    (objects holding both key columns) are on the caller's side; else the users it
    answers are those the caller's links name on the other side (microblog's
    ``/me/following`` answers the followed users)."""
    table, side, other, me = a["table"], a["side"], a["other"], _k(a["caller"])
    if not _ok(status):
        return Outcome(False, f"the caller's own {table} links answered {status}",
                       {"status": status})  # fmt: skip
    links: list[Mapping[str, Any]] = []

    def walk(v: Any) -> None:
        if isinstance(v, Mapping):
            if side in v and other in v:
                links.append(v)
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(body)
    if links:
        foreign = sorted({str((x[side], x[other])) for x in links if _k(x[side]) != me})
        if foreign:
            return Outcome(False, f"answered {table} links of other users {foreign[:5]}",
                           {"other_users_rows": foreign})  # fmt: skip
        return Outcome(True, f"only the caller's {table} links ({len(links)})")
    users = resolve_table(ctx.spec.profile, ctx.api.users_table)
    ukey = _key_column(users[2]) if users is not None else None
    if users is None or ukey is None:
        return Outcome(None, "the users table has no key to find its rows by")
    known = {_k(row[ukey]) for row in ctx.rows(ctx.api.users_table)}
    shown = {_k(row[ukey]) for row in _rows_in(body, users[2], ukey) if _k(row[ukey]) in known}
    allowed = {_k(row[other]) for row in ctx.rows(table) if _k(row[side]) == me}
    foreign = sorted(shown - allowed, key=str)
    if foreign:
        return Outcome(
            False,
            f"answered users {foreign[:5]} that no {table} link of the caller ({side}) names",
            {"other_users_rows": foreign},
        )
    if not shown:
        return Outcome(None, "no user in the answer")
    return Outcome(True, f"only users the caller's {table} links name ({len(shown)})")


def _link_writes(
    before: Mapping[str, list[dict[str, Any]]],
    after: Mapping[str, list[dict[str, Any]]],
    sql: str,
    a: Mapping[str, Any],
) -> Outcome:
    """A write through a link table between users: every link it removed, added or
    changed (rows found by their whole key) is on the caller's side."""
    side, other = a["link"]

    def ident(row: Mapping[str, Any]) -> tuple:
        return tuple(sorted((k, str(v)) for k, v in row.items()))

    old = {ident(r): r for r in before.get(sql, [])}
    new = {ident(r): r for r in after.get(sql, [])}
    touched = [r for k, r in old.items() if k not in new] + [
        r for k, r in new.items() if k not in old
    ]
    foreign = sorted({(r.get(side), r.get(other)) for r in touched
                      if _k(r.get(side)) != _k(a["caller"])}, key=str)  # fmt: skip
    if foreign:
        return Outcome(
            False,
            f"changed {sql} links of other users {foreign[:5]} ({side}, {other})",
            {"other_users_rows": [list(f) for f in foreign]},
        )
    if not touched:
        return Outcome(None, "nothing changed")
    return Outcome(True, f"changed only the caller's {sql} links ({len(touched)})")


@dataclass
class _Evidence:
    """Every probe's answer and the database after it: the outcomes resting on another
    probe's answer read it here -- a *witness* (the same request as another caller: the
    row's owner reaches it; another caller's list differs) or a *control* (the same
    request with plain valid values gets through)."""

    ctx: ProbeContext
    contracts: Mapping[str, Contract]
    runs: Mapping[str, tuple[Probe, Answer, Mapping[str, list[dict[str, Any]]]]]
    _through: dict[tuple[str, str], bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for p, _, _ in self.runs.values():
            if p.user is None or p.token is not None or p.args.get("credential"):
                continue
            if self.past_check(p.name) and not p.args.get("missing_row"):
                self._through[(p.route, p.user)] = True

    def past_check(self, name: str | None) -> bool:
        """Whether that probe's request got past the route's auth check: through, or
        refused by a 4xx that is no refusal of the caller, the row or the route (not
        401, 403, 404, 405) -- the request was read (the role check runs first)."""
        run = self.runs.get(name or "")
        if run is None:
            return False
        if self.reached(name):
            return True
        p, answer, _ = run
        meant = _effective_status(answer, self.ctx.api, self.contracts.get(p.route),
                                  self.ctx.refusals)  # fmt: skip
        return meant is not None and 400 <= meant < 500 and meant not in (401, 403, 404, 405)

    def reached(self, name: str | None) -> bool | None:
        """Whether that probe's request got through (a 2xx, no error envelope); None: no
        such probe."""
        run = self.runs.get(name or "")
        if run is None:
            return None
        p, answer, _ = run
        return _reached(answer, self.ctx.api, self.contracts.get(p.route), self.ctx.refusals)

    def past_validation(self, name: str | None) -> bool:
        """Whether that probe's request got past the request's validation: through, or
        refused by a 4xx that is not the validation status nor a refusal of the caller,
        a row or the route (401, 403, 404, 405) -- a rule of the app's (a 409, a
        transition it refuses)."""
        run = self.runs.get(name or "")
        if run is None:
            return False
        if self.reached(name):
            return True
        p, answer, _ = run
        meant = _effective_status(answer, self.ctx.api, self.contracts.get(p.route),
                                  self.ctx.refusals)  # fmt: skip
        return (meant is not None and 400 <= meant < 500 and meant not in (401, 403, 404, 405)
                and meant != self.ctx.api.validation_status)  # fmt: skip

    def group(self, group: str) -> list[str]:
        """The probes of a ``shared_write`` group (one write of one row, per caller)."""
        return [p.name for p, _, _ in self.runs.values() if p.args.get("group") == group]

    def wrote(self, name: str | None) -> bool:
        """Whether that probe's write got through (a 2xx, no error envelope) and shows
        its effect on the addressed row (deleted, or changed)."""
        run = self.runs.get(name or "")
        if run is None or not self.reached(name):
            return False
        p, _, after = run
        c = self.contracts.get(p.route)
        return bool(_row_effect(self.ctx.seed, after, self.ctx, p.args.get("table"),
                                p.args.get("target"), c.kind if c else None))  # fmt: skip

    def answered(self, name: str | None) -> str:
        run = self.runs.get(name or "")
        return "no answer" if run is None else str(run[1].status)

    def admitted(self, p: Probe) -> bool:
        """Whether the probe's caller got past the route's check by some request (not
        one on a missing row)."""
        return p.user is not None and self._through.get((p.route, p.user), False)

    def shown(self, name: str | None, table: str) -> Any:
        """What a list answered (the seeded rows of ``table`` in it, by key; a table
        without a one-column key: the body); None when it did not get through."""
        run = self.runs.get(name or "")
        if run is None or not self.reached(name):
            return None
        found = resolve_table(self.ctx.spec.profile, table)
        key = _key_column(found[2]) if found is not None else None
        if found is None or key is None:
            import json

            return json.dumps(run[1].body, sort_keys=True, default=str)
        seeded = {_k(r[key]) for r in self.ctx.rows(table)}
        return frozenset(_k(r[key]) for r in _rows_in(run[1].body, found[2], key)
                         if _k(r[key]) in seeded)  # fmt: skip

    def touched(self, name: str | None, table: str) -> frozenset | None:
        """The rows of ``table`` a request changed, added or removed (whole rows)."""
        run = self.runs.get(name or "")
        if run is None:
            return None
        found = resolve_table(self.ctx.spec.profile, table)
        if found is None:
            return None

        def ident(row: Mapping[str, Any]) -> tuple:
            return tuple(sorted((k, str(v)) for k, v in row.items()))

        old = {ident(r) for r in self.ctx.seed.get(found[1], [])}
        new = {ident(r) for r in run[2].get(found[1], [])}
        return frozenset(old ^ new)


def _witnessed(p: Probe, ev: _Evidence | None) -> str:
    """Why another user's row refusing the probe's caller (403/404) is not yet an
    ownership refusal ("" when it is): the caller got past the route's check by some
    request (so its role is admitted), and the row's owner (else the admin) got past it
    by this very request (so the row is there, and the refusal is the caller's: the
    same request answered differently)."""
    if ev is None:
        return "no other answer to compare"
    if not ev.admitted(p):
        return ("the caller got past the route's check by no request: its role's refusal "
                "is not ruled out")  # fmt: skip
    witness = p.args.get("witness")
    if witness is None:
        return "no caller owning the row (or the admin) to reach it by the same request"
    if not ev.past_check(witness):
        return (f"the same request as the row's owner answered {ev.answered(witness)}: the "
                "row is not reachable there")  # fmt: skip
    return ""


def _differs(p: Probe, ev: _Evidence | None, table: str, mine: Any) -> str:
    """Why an answer holding only the caller's own rows (or touching only them) is not
    yet owner scoping ("" when it is): another caller's answer to the same request
    differs, so the caller -- not the request -- selects the rows."""
    if ev is None:
        return "no other answer to compare"
    witness = p.args.get("witness")
    if witness is None:
        return "no other caller to send the same request"
    theirs = ev.touched(witness, table) if p.expect == "writes_own" else ev.shown(witness, table)
    if theirs is None:
        return f"the same request as another caller answered {ev.answered(witness)}"
    if theirs == mine:
        return ("another caller's answer to the same request is the same: the request, not "
                "the caller, may select the rows")  # fmt: skip
    return ""


def _judge(
    p: Probe,
    answer: Answer,
    before: Mapping[str, list[dict[str, Any]]],
    after: Mapping[str, list[dict[str, Any]]],
    ctx: ProbeContext,
    contract: Contract,
    ev: _Evidence | None = None,
) -> dict[str, Outcome]:
    """The probe's outcome on each item it bears on. An answer with more than one
    explanation decides nothing (``ok`` None, the reason saying why); ``ev`` holds the
    other probes' answers a witness or a control reads."""
    spec, api, a = ctx.spec, ctx.api, p.args
    status, body = answer.status, answer.body
    caller = ctx.callers.get(p.user or "") if p.token is None else None
    unchanged = not _changed_besides(before, after, ctx, caller.key if caller else None)
    through = _reached(answer, api, contract, ctx.refusals)
    meant = _effective_status(answer, api, contract, ctx.refusals)
    envelope = "" if through or not _ok(status) else f" (an error envelope: {body!r:.80})"
    out: dict[str, Outcome] = {}

    def all_items(o: Outcome) -> dict[str, Outcome]:
        return {i: o for i in p.items}

    def stated_error(want: int | None, detail: str | None) -> Outcome:
        """An R3 error item: the stated status, and detail when stated. A stated
        detail in an answer that is no JSON (an HTML page) cannot be read: the status
        still contradicts, a matching one is unreachable-by-probe."""
        got = _detail(body, api)
        if detail is not None and status == want and not _readable(body):
            return Outcome(
                None,
                f"{UNREACHABLE}: the route answers {status} with a page that is not JSON; "
                "its detail cannot be read",
                {"status": status},
            )
        ok = status == want and (detail is None or got == detail)
        reason = f"answered {status} {got!r}; stated {want} {detail!r}"
        return Outcome(ok, reason, {"status": status, "detail": got})

    def errors(stated: Mapping[str, Any]) -> None:
        for item, (want, detail) in stated.items():
            if item in p.items:
                out[item] = stated_error(want, detail)

    def control_through(name: str | None, what: str) -> str:
        """ "" when the control probe got through -- or, for an answer that is the
        validation status, got past validation (refused by a rule of the app's: then
        only the value differs, and it is what failed validation); else why the answer
        decides nothing."""
        if ev is None:
            return "no control to compare"
        if name is None:
            return f"no {what} to compare"
        if ev.reached(name):
            return ""
        if meant == api.validation_status and ev.past_validation(name):
            return ""
        return (f"the {what} answered {ev.answered(name)} too: the request is refused for "
                "a reason of its own")  # fmt: skip

    if p.expect in ("witness", "control", "any"):
        return {}  # read by the probes it backs (and the route's 5xx item)
    if p.expect not in ("auth", "login") and _shut_out(answer, p):
        # the app refused the request's caller (a 401; a 403 to a request carrying no
        # credentials): it never reached the rule the probe is about, so it bears on no
        # item -- except an error stated with that very status (so no correction comes
        # from it: H2's 404 -> 401 "corrections" were probes made without a caller)
        why = f"the caller was refused ({status}); the probe never reached the rule"
        refused_out: dict[str, Outcome] = {}
        for item in p.items:
            want, detail = None, None
            if item.startswith("R3 ") and not item.endswith(" 5xx"):
                want, detail = _status_detail(contract, item)
            refused_out[item] = (stated_error(want, detail) if want == status
                                 else Outcome(None, why, {"status": status}))  # fmt: skip
        return refused_out
    if p.expect == "auth":
        refused = _refused(answer, contract, api, a.get("owns_target", True),
                           refusals=ctx.refusals, addressed=a.get("addressed", True),
                           no_credential=a.get("caller") in NON_ROLE_CALLERS)  # fmt: skip
        why = ""
        authorization = (status == 403 and api.forbidden_detail is not None
                         and _detail(body, api) == api.forbidden_detail
                         and a.get("caller") not in NON_ROLE_CALLERS)  # fmt: skip
        if a.get("credential") and refused and not authorization:
            # a Public route's bad credentials, or credentials the probe invented: the
            # answer is about them -- unless it is the role check's own (the profile's
            # forbidden detail, to a caller of a role): credentials never answer that
            refused = None
            why = "a refusal of the credentials sent (invented, or bad on purpose)"
        if refused is False and a.get("missing_row"):
            refused = None  # there is no row: only a refusal tells something here
            why = "a request on a missing row decides only a refusal"
        writes = contract.kind == "delete" or bool(p.json or p.form)
        if refused is False and _ok(status) and writes and not a.get("credential"):
            # the addressed row written -- or, the route's table being another than it
            # writes (a link it removes), anything written besides the caller's own row
            effect = _row_effect(before, after, ctx, a.get("table"), a.get("target"),
                                 contract.kind)  # fmt: skip
            if effect is False and not unchanged:
                effect = True
            if effect is False:
                refused = None
                why = f"answered {status} but the row was not written: no sign it got through"
        if refused is None and not why:
            why = _undecided(answer, a, envelope)
        observed = {"status": status, **({"undecided": why} if refused is None else {})}
        detail = _detail(body, api)
        if (
            refused is None
            and status == 403
            and not a.get("owns_target", True)
            and a.get("owner_known")
            and api.forbidden_detail is not None
            and detail is not None
            and a.get("handle")
            and all(text != detail for _, text in _auth_answers(api, ctx.refusals))
        ):
            # another detail than the role refusal's, on another user's row: the owner
            # check's -- decided with the caller's missing-row answer (_resolve_r1)
            observed["owner_refusal"] = True  # fmt: skip
        r1 = next(i for i in p.items if i.startswith("R1 "))
        said = {True: "refused", False: "admitted", None: "undecided"}[refused]
        out[r1] = Outcome(None, f"{said} ({status})" + (f": {why}" if why else ""), observed,
                          refused)  # fmt: skip
        for item in p.items:
            if item == r1:
                continue
            if refused is True:
                out[item] = stated_error(*_status_detail(contract, item))
            else:
                out[item] = Outcome(None, f"the caller was not refused for certain ({status})",
                                    {"status": status})  # fmt: skip
        return out
    reading = contract.method == "GET" or contract.kind in ("read", "list")
    if p.expect == "not_owned":
        statuses = a["statuses"]
        if not unchanged:
            o = Outcome(False, f"another user's row answered {status} and changed the database",
                        {"status": status, "db_changed": True})  # fmt: skip
        elif through and reading:
            o = Outcome(False, f"another user's row answered {status}: reached",
                        {"status": status, "db_changed": False})  # fmt: skip
        elif through:
            o = Outcome(None, f"answered {status} to a write of another user's row, writing "
                        "nothing: an owner-scoped no-op, or a refusal answered 2xx")  # fmt: skip
        elif meant in statuses:
            why = _witnessed(p, ev)
            reason = f"another user's row answered {meant}{envelope}, nothing written"
            o = Outcome(None if why else True, reason + (f"; not decisive: {why}" if why else ""),
                        {"status": status})  # fmt: skip
        else:
            o = Outcome(None, f"another user's row answered {status}{envelope}: neither the "
                        f"not-owned answer ({' or '.join(map(str, statuses))}) nor a reach",
                        {"status": status})  # fmt: skip
        out = all_items(o)
        errors(a.get("details", {}))
        return out
    if p.expect == "reaches":
        if through:
            writes = contract.kind == "delete" or bool(p.json or p.form)
            effect = _row_effect(before, after, ctx, a.get("table"), a.get("target"),
                                 contract.kind) if writes else None  # fmt: skip
            if effect is False:
                return all_items(Outcome(None, f"another user's row answered {status} but was "
                                         "not written: no sign it got through"))  # fmt: skip
            return all_items(Outcome(True, f"another user's row answered {status}",
                                     {"status": status}))  # fmt: skip
        if meant in NOT_OWNED:
            why = _witnessed(p, ev)
            reason = f"another user's row answered {meant}{envelope}"
            return all_items(Outcome(None if why else False,
                                     reason + (f"; not decisive: {why}" if why else ""),
                                     {"status": status}))  # fmt: skip
        return all_items(Outcome(None, f"another user's row answered {status}{envelope}: "
                                 "neither a reach nor a refusal", {"status": status}))  # fmt: skip
    if p.expect == "owned_rows":
        table = a["table"]
        found = resolve_table(spec.profile, table)
        assert found is not None
        key = _key_column(found[2]) or ""
        if not key:  # a link table (a composite key): its rows cannot be told apart here
            return all_items(Outcome(None, f"{table} has no one-column key to find its rows by"))
        if not through:
            return all_items(Outcome(None, f"the caller's own rows answered {status}{envelope}: "
                                     "nothing shown", {"status": status}))  # fmt: skip
        seeded = {_k(r[key]): r for r in ctx.rows(table)}
        rows = [r for r in _rows_in(body, found[2], key) if _k(r[key]) in seeded]
        public = a.get("public")  # owner_or_public: another user's row shows when true
        foreign = sorted(
            {
                _k(r[key])
                for r in rows
                if ctx.owner(table, seeded[_k(r[key])]) != a["caller"]
                and not (public and seeded[_k(r[key])].get(public))
            },
            key=str,
        )
        if foreign:
            return all_items(
                Outcome(
                    False,
                    f"answered other users' {table} rows {foreign}"
                    + (f" whose {public} is false" if public else ""),
                    {"other_users_rows": foreign},
                )
            )
        if not rows:  # an empty answer shows nothing about filtering
            return all_items(Outcome(None, "no row of the table in the answer"))
        mine = f"the caller's {table} rows" + (f" or whose {public} is true" if public else "")
        why = _differs(p, ev, table, frozenset(_k(r[key]) for r in rows))
        return all_items(Outcome(None if why else True, f"only {mine} ({len(rows)})"
                                 + (f"; not decisive: {why}" if why else "")))  # fmt: skip
    if p.expect == "link_rows":
        o = _link_rows(body, status, ctx, a)
        if not through:
            o = Outcome(None, f"the caller's own links answered {status}{envelope}: nothing shown",
                        {"status": status})  # fmt: skip
        elif o.ok:
            why = _differs(p, ev, a["table"], ev.shown(p.name, a["table"]) if ev else None)
            if why:
                o = Outcome(None, f"{o.reason}; not decisive: {why}")
        return all_items(o)
    if p.expect == "others_rows":
        table = a["table"]
        found = resolve_table(spec.profile, table)
        assert found is not None
        key = _key_column(found[2]) or ""
        if not key:  # a link table (a composite key): its rows cannot be told apart here
            return all_items(Outcome(None, f"{table} has no one-column key to find its rows by"))
        if not through:
            return all_items(Outcome(None, f"the list answered {status}{envelope}: nothing shown",
                                     {"status": status}))  # fmt: skip
        seeded = {_k(r[key]): r for r in ctx.rows(table)}
        rows = [r for r in _rows_in(body, found[2], key) if _k(r[key]) in seeded]
        foreign = sorted(
            {_k(r[key]) for r in rows if ctx.owner(table, seeded[_k(r[key])]) != a["caller"]},
            key=str,
        )
        if foreign:
            return all_items(Outcome(True, f"answered other users' {table} rows {foreign[:5]}"))
        if not rows:
            return all_items(Outcome(None, "no row of the table in the answer"))
        why = _differs(p, ev, table, frozenset(_k(r[key]) for r in rows))
        return all_items(Outcome(None if why else False, f"only the caller's own {table} rows"
                                 + (f"; not decisive: {why}" if why else ""),
                                 None if why else {"scope": "owner"}))  # fmt: skip
    if p.expect == "writes_own":
        table = a["table"]
        found = resolve_table(spec.profile, table)
        assert found is not None
        key = _key_column(found[2]) or ""
        if not key and a.get("link"):  # a link table: its rows by their whole key
            o = _link_writes(before, after, found[1], a)
        elif not key:  # a link table (a composite key): its rows cannot be told apart here
            return all_items(Outcome(None, f"{table} has no one-column key to find its rows by"))
        else:
            sql = found[1]
            diff = db_diff(before, after, {sql: key}).get(sql, {})
            touched = [ch["key"][key] for ch in diff.get("changed", [])] + [
                r[key] for r in diff.get("removed", [])
            ]
            seeded = {_k(r[key]): r for r in ctx.rows(table)}
            foreign = [
                k
                for k in touched
                if _k(k) in seeded and ctx.owner(table, seeded[_k(k)]) != a["caller"]
            ]
            if foreign:
                o = Outcome(False, f"changed other users' {table} rows {foreign}",
                            {"other_users_rows": foreign})  # fmt: skip
            elif not touched:
                o = Outcome(None, "nothing changed")
            else:
                o = Outcome(True, f"changed only the caller's {table} rows")
        if o.ok:
            why = _differs(p, ev, table, ev.touched(p.name, table) if ev else None)
            if why:
                o = Outcome(None, f"{o.reason}; not decisive: {why}")
        return all_items(o)
    if p.expect == "stamps":
        if not through:
            return all_items(Outcome(None, f"the create answered {status}{envelope}"))
        tables = [] if a.get("children_only") else [a["table"]]
        for table in [*tables, *a.get("children", [])]:
            found = resolve_table(spec.profile, table)
            assert found is not None
            key = _key_column(found[2])

            def ident(row: Mapping[str, Any], key: str | None = key) -> Any:
                # a row by its key; a link table's (a composite key) by all of it
                return row.get(key) if key else tuple(sorted(row.items()))

            old = {ident(r) for r in before.get(found[1], [])}
            new = [r for r in after.get(found[1], []) if ident(r) not in old]
            column = a.get("column") if table == a["table"] else None
            # a link's side named by the route's scope (owner(<column>)), else the owner
            owners = sorted({str(r.get(column) if column else ctx.owner(table, r, after))
                             for r in new})  # fmt: skip
            items = [i for i in p.items if i == f"T1 {table}"]
            if table == a["table"]:
                items += [i for i in p.items if i.startswith("R2 ")]
            if not new:
                # a 2xx adding no row (an upsert, a row written elsewhere) shows no owner
                o = Outcome(None, f"a {status} create added no {table} row")
            elif owners == [str(a["caller"])]:
                o = Outcome(True, f"new {table} rows owned by the caller")
            else:
                o = Outcome(
                    False, f"new {table} rows owned by {owners}, not the caller", {"owners": owners}
                )
            for i in items:
                out[i] = o
        return out
    if p.expect == "is_caller":
        if not through:
            return all_items(Outcome(None, f"answered {status}{envelope}: no caller shown",
                                     {"status": status}))  # fmt: skip
        got = body.get(a["key"]) if isinstance(body, Mapping) else None
        ok = _k(got) == _k(a["caller"])
        if isinstance(body, Mapping) and a["key"] not in body and a.get("login"):
            # an answer without the key (a serializer of email and name): the caller's
            # login value names it
            got = a["login"] if a["login"] in body.values() else None
            ok = got is not None
            return all_items(Outcome(ok, f"answered {status} with the login {got!r}",
                                     {"status": status, "login": got}))  # fmt: skip
        return all_items(
            Outcome(
                ok, f"answered {status} with {a['key']}={got!r}", {"status": status, a["key"]: got}
            )
        )
    if p.expect == "shared_write":
        return all_items(_shared_write(p, answer, before, after, ctx, contract, ev, through,
                                       meant, unchanged, envelope))  # fmt: skip
    if p.expect == "ok":
        if through:
            return all_items(Outcome(True, f"answered {status}", {"status": status}))
        return all_items(Outcome(None, f"answered {status}{envelope}: a refusal of the caller, "
                                 "the row or the request -- not told apart",
                                 {"status": status}))  # fmt: skip
    if p.expect == "ok2xx":
        if through:
            return all_items(Outcome(True, f"the boundary value answered {status}",
                                     {"status": status, "accepted": True}))  # fmt: skip
        why = control_through(a.get("control"), "same request with plain valid values")
        return all_items(Outcome(None if why else False,
                                 f"the boundary value answered {status}{envelope}"
                                 + (f"; not decisive: {why}" if why else ""),
                                 {"status": status, "accepted": False}))  # fmt: skip
    if p.expect == "accepted":
        if through:
            return all_items(Outcome(True, f"a listed value answered {status}", {"status": status}))
        if meant == api.validation_status:
            why = control_through(a.get("control"), "same request with plain valid values")
            return all_items(Outcome(None if why else False, f"a listed value answered {meant}"
                                     + (f"; not decisive: {why}" if why else ""),
                                     {"status": status}))  # fmt: skip
        if ev is not None and ev.past_validation(p.name):
            # refused by a rule of the app's (a transition it refuses), after validation:
            # the value itself was taken
            return all_items(Outcome(True, f"a listed value answered {meant}: past validation "
                                     f"({api.validation_status})", {"status": status}))  # fmt: skip
        return all_items(Outcome(None, f"a listed value answered {status}{envelope}: a refusal "
                                 "of the caller, the row or the route -- before validation, "
                                 "maybe", {"status": status}))  # fmt: skip
    if p.expect == "invalid":
        if not unchanged:
            o = Outcome(False, f"answered {status} and wrote to the database; expected a 4xx, "
                        "nothing written", {"status": status, "accepted": _ok(status),
                                            "db_changed": True})  # fmt: skip
        elif through:
            o = Outcome(False, f"answered {status}; expected a 4xx, nothing written",
                        {"status": status, "accepted": True, "db_changed": False})  # fmt: skip
        elif meant is not None and 400 <= meant < 500:
            why = control_through(a.get("control"), "same request with a valid value")
            o = Outcome(None if why else True, f"answered {meant}{envelope}, nothing written"
                        + (f"; not decisive: {why}" if why else ""),
                        {"status": status, "accepted": False, "db_changed": False})  # fmt: skip
        else:
            o = Outcome(None, f"answered {status}{envelope}: neither a 4xx nor an acceptance",
                        {"status": status})  # fmt: skip
        out = all_items(o)
        errors(a.get("errors", {}))
        return out
    if p.expect == "duplicate":
        found = resolve_table(spec.profile, a["table"])
        assert found is not None
        added = len(after.get(found[1], [])) - len(before.get(found[1], []))
        note = "a 5xx: the key holds, the 500 is an app-baseline finding" if status >= 500 else ""
        reason = (f"a second create answered {status}{envelope}, added {added} rows"
                  + (f" ({note})" if note else ""))  # fmt: skip
        observed = {"status": status, "rows_added": added}
        if added > 0 or through:
            # a row added, or the second create accepted (a 2xx that is no error
            # envelope): the stated refusal did not happen
            o = Outcome(False, reason, observed)
        else:
            why = control_through(a.get("control"), "same create with a new key")
            o = Outcome(None if why else True, reason + (f"; not decisive: {why}" if why else ""),
                        observed)  # fmt: skip
        out = all_items(o)
        errors(a.get("errors", {}))
        return out
    if p.expect == "error":
        return all_items(stated_error(a["status"], a.get("detail")))
    raise ValueError(f"unknown probe judgement {p.expect!r}")


def _shared_write(
    p: Probe,
    answer: Answer,
    before: Mapping[str, list[dict[str, Any]]],
    after: Mapping[str, list[dict[str, Any]]],
    ctx: ProbeContext,
    contract: Contract,
    ev: _Evidence | None,
    through: bool,
    meant: int | None,
    unchanged: bool,
    envelope: str,
) -> Outcome:
    """A ``shared`` T1 write probe's outcome (pivot plan 2026-10-05, A.1). Confirms:
    the write got through with its effect on the row, and another (non-admin) caller's
    same write of the same row did too -- one row has at most one owner, so one of the
    two is not it (a request with no caller at all: nobody to own it). Contradicts: the
    caller refused (403/404, nothing written) while another caller's same request
    wrote the row, the caller having got past the route's check by some request (so
    not its role's refusal): the row is its owner's. Anything else decides nothing."""
    a, status = p.args, answer.status
    table, target = a["table"], a["target"]
    others = [n for n in (ev.group(a["group"]) if ev is not None else []) if n != p.name]
    writers = [n for n in others if ev is not None and ev.wrote(n)]
    who = ", ".join(n.split(" | ")[1] for n in writers)
    if through:
        effect = _row_effect(before, after, ctx, table, target, contract.kind)
        if not effect:
            return Outcome(None, f"answered {status} but {table} {target} was not written: "
                           "no sign it got through")  # fmt: skip
        if p.user is None and p.token is None and p.headers is None:
            return Outcome(True, f"a request with no caller wrote {table} {target} ({status}): "
                           "no owner to check")  # fmt: skip
        if not writers:
            return Outcome(None, f"wrote {table} {target} ({status}); not decisive: no other "
                           "caller wrote the same row by the same request (the caller may "
                           "own it)")  # fmt: skip
        return Outcome(True, f"wrote {table} {target} ({status}), and so did {who} by the same "
                       "request: callers other than its owner write it")  # fmt: skip
    if not unchanged:
        return Outcome(None, f"answered {status}{envelope} and changed the database elsewhere")
    if meant in NOT_OWNED:
        why = ""
        if not writers:
            why = "no other caller wrote the same row by the same request"
        elif ev is None or not ev.admitted(p):
            why = ("the caller got past the route's check by no request: its role's refusal "
                   "is not ruled out")  # fmt: skip
        reason = f"{table} {target} refused the caller ({meant}{envelope}), nothing written"
        if why:
            return Outcome(None, f"{reason}; not decisive: {why}", {"status": status})
        return Outcome(False, f"{reason}, while {who} wrote it by the same request: the row is "
                       "its owner's, not shared",
                       {"ownership": "owned", "status": meant, "table": table})  # fmt: skip
    return Outcome(None, f"answered {status}{envelope}: neither a write nor a refusal",
                   {"status": status})  # fmt: skip


def _undecided(answer: Answer, a: Mapping[str, Any], envelope: str) -> str:
    """Why an R1 answer tells neither refused nor admitted."""
    status = answer.status
    if _ok(status):
        return f"a 2xx error envelope{envelope}"
    if status >= 500:
        return "a crash: before or past the auth check, not told apart"
    if status == 405:
        return "the router's 405"
    if status == 404:
        return ("a 404 to a request addressing a row: the router's (a path value it does not "
                "match), or a row hidden from the caller")  # fmt: skip
    if status == 403 and not a.get("owns_target", True):
        return ("a 403 on a row that is not the caller's own: an ownership refusal and a role "
                "refusal answer alike")  # fmt: skip
    if status == 403:
        return "a 403 of another detail than the profile's forbidden one: a role check or a rule"
    return f"a {status} the route states as an error of its own"


# ---- running -----------------------------------------------------------------------------


@dataclass
class ItemVerdict:
    """An item's verdict, the probes that decided it, why, and (refuted) the observed
    fact to propose as a correction."""

    item: RuleItem
    verdict: str
    reason: str = ""
    probes: list[str] = field(default_factory=list)
    observed: Any = None
    notes: list[str] = field(default_factory=list)
    witness: str | None = None  # the probe that refuted it
    # the probes whose outcome decides the item the way the verdict says (verified: the
    # confirming ones); a verified item without one is an internal error (check_verdicts)
    decisive: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.item.id,
            "kind": self.item.kind,
            "subject": self.item.subject,
            "sentence": self.item.sentence,
            "predicted": dict(self.item.predicted),
            "verdict": self.verdict,
            "reason": self.reason,
            "probes": list(self.probes),
            "decisive": list(self.decisive),
            "observed": self.observed,
            "notes": list(self.notes),
        }


@dataclass
class RuleResult:
    """Layer R's result: ``items`` (id -> ItemVerdict), ``probes`` (name -> its
    request, route, items, status, body and database difference), the ``tables`` and
    ``routes`` verdicts (§1.1, §1.2), and notes. ``contradicted``: item id -> the probes
    whose answer contradicted the item's prediction, recorded as they were judged
    (before any later verdict path ran): :func:`summarise`'s self-check reads it."""

    items: dict[str, ItemVerdict] = field(default_factory=dict)
    probes: dict[str, dict[str, Any]] = field(default_factory=dict)
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    routes: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    contradicted: dict[str, list[str]] = field(default_factory=dict)

    @property
    def summary(self) -> dict[str, Any]:
        def count(group: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
            # a table outside the API is listed, not counted (plan 2026-10-05, decision 1)
            inside = [v for v in group.values() if v["verdict"] != OUTSIDE_API]
            n = sum(is_verified(v["verdict"]) for v in inside)
            out = {"verified": n, "total": len(inside)}
            if len(inside) < len(group):
                out["outside_api"] = len(group) - len(inside)
            return out

        verdicts = {v: sum(i.verdict == v for i in self.items.values()) for v in VERDICTS}
        corrected = sum(i.verdict == VERIFIED_CORRECTED for i in self.items.values())
        if corrected:
            verdicts[VERIFIED_CORRECTED] = corrected
        return {"items": verdicts, "tables": count(self.tables), "routes": count(self.routes)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "items": {i: v.to_dict() for i, v in self.items.items()},
            "tables": self.tables,
            "routes": self.routes,
            "probes": self.probes,
            "notes": self.notes,
        }


def _reachable_roles(api: ApiProfile) -> set[str]:
    """The roles a caller can have: ``NoRoles``' one, ``AdminFlag``'s two, a
    ``RoleColumn``'s named values (and the admin role), else every non-Public role."""
    src = api.roles_from
    if isinstance(src, NoRoles):
        return {src.role}
    if isinstance(src, AdminFlag):
        return {src.role, api.admin_role}
    if isinstance(src, RoleColumn) and src.values:
        return {name for _, name in src.values}
    return {r for r in api.roles if r != "Public"}


def _set_active(spec: Spec, db: Path, key: Any, value: bool) -> None:
    """Set a users row's active column (the inactive caller is logged in while active)."""
    import sqlite3

    api = spec.profile.api
    found = resolve_table(spec.profile, api.users_table) if api is not None else None
    if api is None or found is None or not api.active_column:
        return
    key_col = _key_column(found[2]) or "id"
    con = sqlite3.connect(db)
    try:
        con.execute(
            f'UPDATE "{found[1]}" SET "{api.active_column}" = ? WHERE "{key_col}" = ?',
            (int(value), key),
        )
        con.commit()
    finally:
        con.close()


class _Recorder:
    """A client recording the answers of the requests a login makes through it (the
    rest delegated), so a caller's own credentials -- what its login answered: a
    refresh token, a cookie -- can be presented later."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.answers: list[Any] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def _record(self, method: str, *args: Any, **kw: Any) -> Any:
        r = getattr(self._client, method)(*args, **kw)
        self.answers.append(r)
        return r

    def post(self, *args: Any, **kw: Any) -> Any:
        return self._record("post", *args, **kw)

    def request(self, *args: Any, **kw: Any) -> Any:
        return self._record("request", *args, **kw)

    def take(self) -> tuple[dict[str, str], dict[str, str]]:
        """The string fields of the last answer's JSON object (one level of nesting
        flattened) and the cookies it set; then forget the answers."""
        fields: dict[str, str] = {}
        cookies: dict[str, str] = {}
        last = self.answers[-1] if self.answers else None
        self.answers = []
        if last is None:
            return fields, cookies
        try:
            body = last.json()
        except Exception:  # noqa: BLE001 -- not JSON: no fields
            body = None
        for k, v in body.items() if isinstance(body, Mapping) else ():
            if isinstance(v, str):
                fields.setdefault(k, v)
            elif isinstance(v, Mapping):
                for k2, v2 in v.items():
                    if isinstance(v2, str):
                        fields.setdefault(k2, v2)
        try:
            cookies = {k: v for k, v in dict(last.cookies).items() if isinstance(v, str)}
        except Exception:  # noqa: BLE001 -- a response without a cookie jar
            cookies = {}
        return fields, cookies


def _tokens(
    spec: Spec,
    sessions: SessionFactory,
    seed_db: Path,
    callers: Mapping[str, Caller],
    login: Login | None,
    notes: list[str],
    *,
    inactive_login: bool = False,
    answers: dict[str, dict[str, str]] | None = None,
    cookies: dict[str, dict[str, str]] | None = None,
) -> dict[str, str]:
    """Every active handle's token, logged in on the seed database itself, so a token
    the app keeps as a row (DRF's ``Token``, a tokens table) is in every probe's copy.
    ``inactive_login``: the inactive users too, each made active for its login only (a
    token the app minted for it while active; refused once it is inactive again).
    ``answers`` / ``cookies`` receive what each login answered (see :class:`_Recorder`)."""
    tokens: dict[str, str] = {}
    if login is None:
        if spec.users:
            notes.append("layer R: no login given; probes run without callers")
        return tokens
    asleep = [c for c in callers.values() if not c.active and inactive_login]
    for c in asleep:
        _set_active(spec, seed_db, c.key, True)
    try:
        with sessions(seed_db) as session:
            client = getattr(session, "client", session)
            rec = _Recorder(client) if client is not None else None
            for handle, caller in callers.items():
                if not caller.active and caller not in asleep:
                    continue
                try:
                    tokens[handle] = login(rec if rec is not None else client, handle)
                except Exception as err:  # noqa: BLE001 -- that caller is left out, noted
                    notes.append(f"layer R: cannot log in as {handle!r} ({err})")
                    continue
                if rec is not None:
                    fields, set_cookies = rec.take()
                    if answers is not None and fields:
                        answers[handle] = fields
                    if cookies is not None and set_cookies:
                        cookies[handle] = set_cookies
    finally:
        for c in asleep:
            _set_active(spec, seed_db, c.key, False)
    return tokens


def _selected(item_id: str, only: Sequence[str] | None) -> bool:
    return only is None or any(item_id.startswith(prefix) for prefix in only)


def run_probes(
    spec: Spec,
    sessions: SessionFactory,
    make_db: Callable[[str, Path], Path],
    *,
    login: Login | None = None,
    mint_token: Callable[[Any], str] | None = None,
    scheme: str = "Bearer",
    only: Sequence[str] | None = None,
    on_probe: Callable[[Probe], None] | None = None,
    refusals: Sequence[tuple[int, str | None]] = (),
    login_route: tuple[str, str] | None = None,
    auth_headers: Callable[[str], Mapping[str, str]] | None = None,
    observe: Callable[[Probe, Path, Path, Answer], None] | None = None,
    variant: str = "seed",
    inactive_login: bool = False,
    unreachable: Mapping[str, str] | None = None,
    auth_present: Callable[[Any, str], Mapping[str, str]] | None = None,
    json_login: tuple[str, str, str, str] | None = None,
) -> RuleResult:
    """Every rule item of ``spec`` (those whose id starts with one of ``only``, when
    given), probed on the app ``sessions`` opens; ``make_db(variant, path)`` writes the
    seeded database (``variant``: which one). ``login(client, handle)`` answers a
    handle's token (the session's ``client``); ``mint_token(user key)`` a token for the
    inactive user (without it, no inactive probe); ``scheme`` the Authorization scheme,
    or ``auth_headers(token)`` the headers carrying a token (an app runtime's).
    ``on_probe`` is called before each probe runs (layer C labels its requests with
    it), ``observe(probe, seed_db, db_after, answer)`` after it (the database files
    still there: mining records them). ``refusals``: answers (status, detail) that
    refuse a caller besides the profile's; ``login_route``: (method, path) of a login
    route taking HTTP Basic credentials (see :func:`_r1`); ``auth_present(session,
    token)`` attaches a token to a session and answers the headers still to send (a
    session cookie goes in the session's cookie jar). ``inactive_login``: the
    inactive caller's token comes from logging it in while active (see
    :func:`_tokens`), else from ``mint_token``. ``unreachable``: routes (``METHOD
    /path``) no probe may call, with the reason (a route that needs the network): their
    items are unexercised, ``unreachable-by-probe`` with that reason. ``json_login``:
    (method, path, username field, password field) of a login route taking JSON
    credentials (see :func:`_r1`)."""
    all_items = rule_items(spec)
    items = {i.id: i for i in all_items if _selected(i.id, only)}
    result = RuleResult()
    contracts = {c.endpoint: c for c in route_contracts(spec)}
    with tempfile.TemporaryDirectory(prefix="lattice-check-r-") as name:
        tmp = Path(name)
        seed_db = Path(make_db(variant, tmp / "seed.db"))
        _, sql_names = tables_of(spec.profile)
        seed_all = _read_tables(seed_db)
        tables = [t for t in dict.fromkeys(sql_names.values()) if t in seed_all]
        callers = _callers(spec, {t: seed_all[t] for t in tables})
        answers: dict[str, dict[str, str]] = {}
        cookies: dict[str, dict[str, str]] = {}
        tokens = _tokens(spec, sessions, seed_db, callers, login, result.notes,
                         inactive_login=inactive_login, answers=answers,
                         cookies=cookies)  # fmt: skip
        inactive = next((c for c in callers.values() if not c.active), None)
        inactive_token = None
        if inactive is not None and inactive.handle in tokens:
            inactive_token = tokens.pop(inactive.handle)
        elif inactive is not None and mint_token is not None:
            with sessions(seed_db):  # minted where the app keeps its tokens, if it does
                inactive_token = mint_token(inactive.key)
        elif inactive is not None:
            result.notes.append("layer R: no mint_token; the inactive-caller probes are skipped")
        seed_all = _read_tables(seed_db)  # with what logging in wrote (tokens, last login)
        seed = {t: seed_all[t] for t in tables}
        ctx = ProbeContext(spec, seed, callers, tokens, inactive, inactive_token,
                           tuple(refusals), login_route, json_login, answers,
                           cookies)  # fmt: skip
        skipped = dict(unreachable or {})
        probes, reasons = build_probes(spec, ctx, items, skip=tuple(skipped))
        for item_id, item in items.items():
            if item.subject in skipped:
                reasons[item_id] = f"{UNREACHABLE}: {skipped[item.subject]}"
        outcomes: dict[str, list[tuple[Probe, Outcome]]] = {i: [] for i in items}
        users_item = f"T1 {ctx.api.users_table}"
        if users_item in outcomes and login is not None:
            # T1 users: logging in as every role works (the "me" probes add theirs)
            roles = _reachable_roles(ctx.api)
            logged = {c.role for c in ctx.with_token()}
            missing = sorted(roles - logged, key=str)
            login_probe = Probe("(login) every role", "(login)", (users_item,), "POST",
                                "(login)", "login")  # fmt: skip
            outcomes[users_item].append((login_probe, Outcome(
                not missing,
                f"no caller of {missing} could log in" if missing
                else f"every role logs in ({', '.join(sorted(logged, key=str))})",
                {"missing": missing} if missing else None,
            )))  # fmt: skip
        answers: dict[str, tuple[Answer, dict[str, list]]] = {}
        for k, probe in enumerate(probes):
            if on_probe is not None:
                on_probe(probe)
            db = tmp / f"probe-{k}.db"
            shutil.copyfile(seed_db, db)
            headers: dict[str, str] = {}
            token = probe.token
            if token is None and probe.user is not None:
                token = tokens.get(probe.user)
            if probe.headers is not None:
                headers = dict(probe.headers)
            elif token is not None and auth_present is not None:
                pass  # presented on the session below (a session cookie)
            elif token is not None and auth_headers is not None:
                headers = dict(auth_headers(token))
            elif token is not None:
                headers["Authorization"] = f"{scheme} {token}"
            if probe.cookies:
                headers = {**headers, "Cookie": "; ".join(f"{k}={v}" for k, v in
                                                          probe.cookies.items())}  # fmt: skip
            with sessions(db) as session:
                if probe.headers is None and token is not None and auth_present is not None:
                    headers = dict(auth_present(session, token))
                answer = session.request(
                    probe.method,
                    probe.path,
                    params=probe.params,
                    json=probe.json,
                    form=probe.form,
                    headers=headers,
                )
            after = {t: rows for t, rows in _read_tables(db).items() if t in tables}
            if observe is not None:
                observe(probe, seed_db, db, answer)
            db.unlink(missing_ok=True)
            answers[probe.name] = (answer, after)
            diff = db_diff(seed, after, _keys(spec))
            result.probes[probe.name] = {
                "route": probe.route,
                "items": list(probe.items),
                "request": probe.request(),
                "status": answer.status,
                "body": answer.body,
                "db_diff": diff,
            }
            if probe.args.get("credential"):
                # its refusal decides nothing (bad credentials; invented ones)
                result.probes[probe.name]["credential"] = True
            if probe.expect in ("witness", "control"):
                result.probes[probe.name]["role"] = probe.expect
            if probe.expect == "shared_write":
                # a write of a shared table's row: what a verified "shared" T1 rests on
                result.probes[probe.name]["shared_write"] = True
        # judged once every probe ran: a witness's or a control's answer is read by the
        # probes it backs
        ev = _Evidence(ctx, contracts, {p.name: (p, *answers[p.name]) for p in probes})
        for probe in probes:
            answer, after = answers[probe.name]
            judged = _judge(probe, answer, seed, after, ctx, contracts[probe.route], ev)
            for item_id, outcome in judged.items():
                if item_id in outcomes:
                    outcomes[item_id].append((probe, outcome))
    for item_id, item in items.items():
        if item.kind == "R1":
            outcomes[item_id] = _resolve_r1(outcomes[item_id])
        _record_bearing(result, item, outcomes[item_id])
        result.items[item_id] = _verdict(item, outcomes[item_id], reasons.get(item_id))
        against = _contradicting(item, outcomes[item_id])
        if against:
            result.contradicted[item_id] = against
    _post_hoc(spec, result, items, probes, answers, ctx)
    _route_5xx(result, items, probes, answers, contracts)
    summarise(spec, result)
    return result


def _record_bearing(result: RuleResult, item: RuleItem,
                    found: Sequence[tuple[Probe, Outcome]]) -> None:  # fmt: skip
    """Each probe's bearing on the item, in its record: ``decisive`` (it confirms or
    contradicts; R1: tells refused from admitted), what it ``says`` and ``why``."""
    for p, o in found:
        record = result.probes.get(p.name)
        if record is None:
            continue
        if item.kind == "R1":
            says = {True: "refused", False: "admitted", None: "undecided"}[o.refused]
        else:
            says = {True: "confirms", False: "contradicts", None: "neither"}[o.ok]
        record.setdefault("bearing", {})[item.id] = {
            "decisive": o.decisive(item.kind),
            "says": says,
            "why": o.reason or says,
        }


def _keys(spec: Spec) -> dict[str, str]:
    tables, sql_names = tables_of(spec.profile)
    out = {}
    for t, schema in tables.items():
        key = _key_column(schema)
        if key:
            out[sql_names.get(t, t)] = key
    return out


def _verdict(
    item: RuleItem, found: Sequence[tuple[Probe, Outcome]], reason: str | None
) -> ItemVerdict:
    """An item's verdict from its probes' outcomes: refuted by a contradicting one,
    verified by a confirming one (``decisive``: the confirming probes), else
    unexercised with the reason -- an outcome deciding nothing (an answer with more than
    one explanation) counts for neither."""
    if item.unclear:
        return ItemVerdict(item, "unclear", item.unclear)
    if item.kind == "R1":
        if not found and reason:
            return ItemVerdict(item, "unexercised", reason)
        return _r1_verdict(item, found)
    names = [p.name for p, _ in found]
    bad = [(p, o) for p, o in found if o.ok is False]
    if bad:
        p, o = bad[0]
        return ItemVerdict(item, "refuted", o.reason, names, o.observed, witness=p.name,
                           decisive=[q.name for q, _ in bad])  # fmt: skip
    good = [(p, o) for p, o in found if o.ok]
    if good and item.predicted.get("scope") == "owner_or_admin":
        # two claims: non-admins act on their own rows only, and the admin on anyone's --
        # each needs a probe of its own confirming it
        halves = {bool(p.args.get("admin_half")) for p, _ in good}
        if halves != {True, False}:
            undecided = "the admin's" if True not in halves else "the non-admins'"
            why = "; ".join(dict.fromkeys(o.reason for p, o in found if o.ok is None))
            return ItemVerdict(item, "unexercised", f"{undecided} half of owner_or_admin is "
                               f"undecided: {why or 'no probe'}", names)  # fmt: skip
    if good and item.kind == "T1" and item.predicted.get("ownership") == "shared":
        writes = list(item.predicted.get("writes") or ())
        if not writes:
            # reads verify it: nothing else claims more (and the reason says so)
            notes = sorted({o.reason for _, o in good if "app-baseline" in o.reason})
            return ItemVerdict(item, "verified", f"{good[0][1].reason}; "
                               f"{_NO_SHARED_WRITES.format(table=item.subject)}", names,
                               notes=notes, decisive=[p.name for p, _ in good])  # fmt: skip
        good = [(p, o) for p, o in good if p.expect == "shared_write"]
        if not good:
            why = "; ".join(dict.fromkeys(o.reason for p, o in found
                                          if p.expect == "shared_write"))  # fmt: skip
            return ItemVerdict(item, "unexercised", f"reads alone do not verify shared: "
                               f"{item.subject} rows are updated or deleted by "
                               f"{', '.join(writes)}, and no write by a caller other than "
                               f"the row's owner was decisive: {why or 'no write probe'}",
                               names)  # fmt: skip
    if good:
        notes = sorted({o.reason for _, o in good if "app-baseline" in o.reason})
        return ItemVerdict(item, "verified", good[0][1].reason, names, notes=notes,
                           decisive=[p.name for p, _ in good])  # fmt: skip
    unreachable = [o.reason for _, o in found if o.reason.startswith(UNREACHABLE)]
    if found and len(unreachable) == len(found):
        reason = reason or unreachable[0]
    why = reason or (
        "no probe reached it"
        if not found
        else "no probe confirmed or contradicted it: "
        + "; ".join(dict.fromkeys(o.reason for _, o in found))
    )
    return ItemVerdict(item, "unexercised", why, names)


def _resolve_r1(found: Sequence[tuple[Probe, Outcome]]) -> list[tuple[Probe, Outcome]]:
    """R1 outcomes resting on other callers' answers: a refusal on a missing row (no
    row to own) is the role's only when some other caller got past it on a missing row
    too (a 404, a 400...); when every caller is refused there, the row's absence may be
    the reason (an app answering "not found or not yours" with a 403)."""
    past: set[str] = set()
    for p, o in found:
        status = o.observed.get("status") if isinstance(o.observed, Mapping) else None
        if p.args.get("missing_row") and isinstance(status, int) and status < 500 \
                and status not in (401, 403):  # fmt: skip
            past.add(p.args.get("handle") or "")
    out: list[tuple[Probe, Outcome]] = []
    for p, o in found:
        if (isinstance(o.observed, Mapping) and o.observed.get("owner_refusal")
                and p.args.get("handle") in past):  # fmt: skip
            # a 403 on another user's row (an owner the profile knows) with another
            # detail than the role refusal's, from a caller that gets past the check on
            # a missing row: the owner check refused it, past the role check
            o = Outcome(None, "admitted (403): the owner check's refusal -- another detail "
                        "than the role refusal's, on another user's row, and the caller gets "
                        "past the check on a missing row", {"status": 403}, False)  # fmt: skip
        if p.args.get("missing_row") and o.refused is True:
            if not past - {p.args.get("handle")}:
                o = Outcome(None, f"undecided ({o.observed.get('status')}): every caller is "
                            "refused on a missing row -- the row's absence may be why",
                            {**o.observed, "undecided": "every caller refused on a missing row"},
                            None)  # fmt: skip
        out.append((p, o))
    return out


def replace_outcome(o: Outcome, **changes: Any) -> Outcome:
    from dataclasses import replace

    return replace(o, **changes)


def _caller_flag(results: Sequence[tuple[Probe, Outcome]]) -> bool | None:
    """One caller's (or role's) R1 decision from its probes' decisive outcomes: any
    admission admits; else any refusal refuses; else None (nothing decides)."""
    flags = [o.refused for _, o in results if o.refused is not None]
    if any(f is False for f in flags):
        return False
    return True if flags else None


def _contradicting(item: RuleItem, found: Sequence[tuple[Probe, Outcome]]) -> list[str]:
    """The probes whose answer contradicts the item's prediction, as judged: an R1
    caller decided against its prediction (refused although admitted, or admitted
    although refused: its probes saying so), else an outcome ``ok`` False."""
    if item.kind != "R1":
        return [p.name for p, o in found if o.ok is False]
    split = _split_roles(found)
    if split:  # every decided probe of a split role contradicts one prediction or another
        return [p.name for p, o in found
                if p.args.get("caller") in split and o.refused is not None]  # fmt: skip
    by_caller: dict[str, list[tuple[Probe, Outcome]]] = {}
    for p, o in found:
        by_caller.setdefault(p.args["caller"], []).append((p, o))
    out: list[str] = []
    for results in by_caller.values():
        refused = _caller_flag(results)
        if refused is not None and refused == results[0][0].args["allowed"]:
            out += [p.name for p, o in results if o.refused is refused]
    return out


def _split_roles(found: Sequence[tuple[Probe, Outcome]]) -> dict[str, dict[str, bool]]:
    """Roles whose callers answered differently: role -> handle -> refused -- one
    admitted, another refused, each decided by its own decisive outcomes (an answer with
    more than one explanation decides no caller). The profile's roles then miss a
    distinction (an enum column it does not model), so no auth spells the route. On the
    request every caller sends alike (no row addressed) the answers compare by status
    alone: a 401/403 to one caller and a 2xx or another 4xx to another caller of the
    role, for the very same request, is a distinction between them whatever the 403's
    detail."""
    per: dict[str, dict[str, list[tuple[Probe, Outcome]]]] = {}
    for p, o in found:
        handle = p.args.get("handle")
        if handle is None or p.args.get("credential"):
            continue
        status = o.observed.get("status") if isinstance(o.observed, Mapping) else None
        if p.args.get("same_request") and o.refused is None and isinstance(status, int):
            if status in (401, 403):
                o = replace_outcome(o, refused=True)
            elif status < 500 and status != 405 and not _ok(status):
                o = replace_outcome(o, refused=False)
        per.setdefault(p.args["caller"], {}).setdefault(handle, []).append((p, o))
    out: dict[str, dict[str, bool]] = {}
    for role, handles in per.items():
        flags = {h: _caller_flag(rs) for h, rs in handles.items()}
        decided = {h: f for h, f in flags.items() if f is not None}
        if len(set(decided.values())) > 1:
            out[role] = decided
    return out


def _r1_verdict(item: RuleItem, found: Sequence[tuple[Probe, Outcome]]) -> ItemVerdict:
    """R1's verdict: refuted when callers of one role answered differently, or a caller
    was decided against the prediction; verified only when every role a probed caller
    holds is decided as predicted (the ``decisive`` probes) -- and no caller of a role
    decided admitted was refused in a way that may be the role's (an ambiguous 403:
    then ``unclear``); else unexercised."""
    names = [p.name for p, _ in found]
    split = _split_roles(found)
    if split:
        said = "; ".join(
            f"{role}: "
            + ", ".join(
                f"{h} {'refused' if x else 'admitted'}" for h, x in sorted(hs.items())
            )  # fmt: skip
            for role, hs in sorted(split.items())
        )
        witness = next(p.name for p, o in found
                       if p.args.get("caller") in split and o.refused is not None)  # fmt: skip
        return ItemVerdict(
            item,
            "refuted",
            f"callers of one role answered differently ({said}): the profile's roles miss a "
            "distinction the route makes",
            names,
            {"split_roles": sorted(split)},
            witness=witness,
            decisive=[
                p.name for p, o in found if p.args.get("caller") in split and o.refused is not None
            ],  # fmt: skip
        )
    by_caller: dict[str, list[tuple[Probe, Outcome]]] = {}
    for p, o in found:
        by_caller.setdefault(p.args["caller"], []).append((p, o))
    admitted: list[str] = []
    wrong: list[tuple[str, Probe]] = []
    decided: dict[str, bool] = {}
    for caller, results in by_caller.items():
        refused = _caller_flag(results)
        if refused is None:
            continue
        decided[caller] = refused
        allowed = results[0][0].args["allowed"]
        if not refused:
            admitted.append(caller)
        if refused == allowed:  # refused although allowed, or admitted although not
            witness = next(p for p, o in results if o.refused is refused)
            wrong.append((caller, witness))
    observed = {"allowed": sorted(admitted)}
    if wrong:
        callers = ", ".join(
            f"{c} {'refused' if p.args['allowed'] else 'admitted'}" for c, p in wrong
        )
        return ItemVerdict(
            item,
            "refuted",
            f"callers answered against the auth: {callers}",
            names,
            observed,
            witness=wrong[0][1].name,
            decisive=[
                p.name for c, _ in wrong for p, o in by_caller[c] if o.refused is decided[c]
            ],  # fmt: skip
        )
    roles = [c for c in by_caller if c not in NON_ROLE_CALLERS]
    if not roles:
        # the prediction is about which roles are admitted: anonymous, a malformed token
        # and the inactive user alone never verify it
        return ItemVerdict(
            item,
            "unexercised",
            "no callers: no user of any role was logged in, so no probe shows who is "
            "admitted" + (f" ({len(decided)} non-role callers as predicted)" if decided else ""),
            names,
        )
    # a caller of a role decided admitted whose own answers are all undecided, one of
    # them a 403 that may be the role's refusal (on a row not its own): the role may be
    # split -- the profile's roles missing a distinction the probes cannot see
    doubt = []
    for role in roles:
        if decided.get(role) is not False:
            continue
        handles: dict[str, list[tuple[Probe, Outcome]]] = {}
        for p, o in by_caller[role]:
            handles.setdefault(p.args.get("handle") or "", []).append((p, o))
        for h, rs in handles.items():
            if _caller_flag(rs) is None and any(
                isinstance(o.observed, Mapping) and o.observed.get("status") in (401, 403)
                for _, o in rs
            ):
                doubt.append(f"{h} ({role})")
    if doubt:
        return ItemVerdict(
            item,
            "unclear",
            f"{', '.join(doubt)}: refused only where an ownership refusal and a role refusal "
            "answer alike (a 403 on rows not its own; on a missing row every caller is "
            "refused alike), while other callers of the role are admitted -- the role may be "
            "split",
            names,
            observed,
        )
    open_roles = [r for r in roles if r not in decided]
    if open_roles:
        why = "; ".join(
            f"{role}: " + "; ".join(dict.fromkeys(
                o.observed.get("undecided", o.reason) if isinstance(o.observed, Mapping)
                else o.reason for _, o in by_caller[role]))
            for role in open_roles
        )  # fmt: skip
        return ItemVerdict(item, "unexercised", f"no answer decides {', '.join(open_roles)} -- "
                           f"{why}", names, observed)  # fmt: skip
    return ItemVerdict(
        item,
        "verified",
        f"{len(decided)} callers as predicted",
        names,
        decisive=[
            p.name
            for c, rs in by_caller.items()
            if c in decided
            for p, o in rs
            if o.refused is decided[c]
        ],  # fmt: skip
    )


def _post_hoc(
    spec: Spec,
    result: RuleResult,
    items: Mapping[str, RuleItem],
    probes: Sequence[Probe],
    answers: Mapping[str, tuple[Answer, dict[str, list]]],
    ctx: ProbeContext,
) -> None:
    """T2 (private columns, over every body) and T4's stored values (over every
    database after a probe)."""
    tables, sql_names = tables_of(spec.profile)
    for item_id, item in items.items():
        if item.kind == "T2":
            table, col = item.subject, item.predicted["private"]
            schema = tables[table]
            key = _key_column(schema) or "id"
            shown, rows_seen = [], []
            for p in probes:
                answer, _ = answers[p.name]
                rows = _rows_in(answer.body, schema, key)
                if any(col in r for r in rows):
                    shown.append(p.name)
                elif rows and _reached(answer, ctx.api, None, ctx.refusals):
                    rows_seen.append(p.name)
            for name in shown + rows_seen:
                says = "contradicts" if name in shown else "confirms"
                result.probes[name].setdefault("bearing", {})[item_id] = {
                    "decisive": True,
                    "says": says,
                    "why": f"a {table} row {'with' if says == 'contradicts' else 'without'} {col}",
                }
            if shown:
                result.items[item_id] = ItemVerdict(
                    item,
                    "refuted",
                    f"an answer shows {table}.{col}",
                    shown,
                    {"shown_by": shown[:5]},
                    witness=shown[0],
                    decisive=shown,
                )
            elif rows_seen:
                result.items[item_id] = ItemVerdict(
                    item,
                    "verified",
                    f"{len(rows_seen)} answers show {table} rows without {col}",
                    rows_seen[:20],
                    decisive=rows_seen[:20],
                )
            else:
                result.items[item_id] = ItemVerdict(
                    item, "unexercised", f"no 2xx answer returned a {table} row"
                )
        elif item.kind == "T4":
            table = item.subject
            col = item_id.split(".", 1)[1]
            stored = set(item.predicted["stored"])
            sql = sql_names.get(table, table)
            seen: dict[str, str] = {}

            # a value only the seed holds is the import's own (its seeder wrote it), not
            # the app's: only a value a probe wrote (a new row, a changed column) is
            seeded = {_row_ident(row, tables[table]): row.get(col) for row in ctx.seed.get(sql, [])}
            for p in probes:
                _, after = answers[p.name]
                for row in after.get(sql, []):
                    v = row.get(col)
                    ident = _row_ident(row, tables[table])
                    written = ident not in seeded or seeded[ident] != v
                    if v is not None and v not in stored and written:
                        seen.setdefault(str(v), p.name)
            ours = sorted({str(row.get(col)) for row in ctx.seed.get(sql, [])
                           if row.get(col) is not None and row.get(col) not in stored})  # fmt: skip
            if ours:
                result.notes.append(f"{item_id}: the import's seed holds values outside the "
                                    f"list {ours}: the seed's, not the app's")  # fmt: skip
            if seen:
                first = next(iter(seen.values()))
                result.items[item_id] = ItemVerdict(
                    item,
                    "refuted",
                    f"stored values outside the list: {sorted(seen)}",
                    sorted(set(seen.values())),
                    {"stored": sorted(seen)},
                    witness=first,
                    decisive=sorted(set(seen.values())),
                )
            elif (
                result.items[item_id].verdict == "unexercised" and not result.items[item_id].probes
            ):
                result.items[item_id].reason = (
                    f"no route writes {table}.{col} through a parameter "
                    "(every stored value is listed)"
                )


def _row_ident(row: Mapping[str, Any], schema: Schema) -> Any:
    """A row by its key (one column), else by all of it."""
    key = _key_column(schema)
    return _k(row.get(key)) if key else tuple(sorted((k, str(v)) for k, v in row.items()))


def _route_5xx(
    result: RuleResult,
    items: Mapping[str, RuleItem],
    probes: Sequence[Probe],
    answers: Mapping[str, tuple[Answer, dict[str, list]]],
    contracts: Mapping[str, Contract],
) -> None:
    for item_id, item in items.items():
        if not (item.kind == "R3" and item_id.endswith(" 5xx")):
            continue
        route = item.subject
        stated = {e.status for e in contracts[route].errors}
        mine = [p for p in probes if p.route == route]
        loud = [
            p
            for p in mine
            if answers[p.name][0].status >= 500 and answers[p.name][0].status not in stated
        ]
        # a probe refused before the handler ran (a 401/403, the router's 404 or 405)
        # shows nothing about the handler's errors
        past = [p for p in mine if _past_auth(answers[p.name][0])]
        for p in mine:
            ok = p in past and p not in loud
            result.probes[p.name].setdefault("bearing", {})[item_id] = {
                "decisive": p in past or p in loud,
                "says": "contradicts" if p in loud else "confirms" if ok else "neither",
                "why": (f"answered an unstated {answers[p.name][0].status}" if p in loud
                        else "answered past the auth check without a 5xx" if ok
                        else f"refused before the handler ran ({answers[p.name][0].status})"),
            }  # fmt: skip
        if loud:
            first = answers[loud[0].name][0]
            result.items[item_id] = ItemVerdict(
                item,
                "refuted",
                f"{len(loud)} probes answered an unstated {first.status}",
                [p.name for p in loud],
                {"status": first.status, "body": first.body},
                witness=loud[0].name,
                decisive=[p.name for p in loud],
            )
        elif past:
            result.items[item_id] = ItemVerdict(
                item, "verified", f"no unstated 5xx in {len(mine)} probes ({len(past)} past the "
                "auth check)", [p.name for p in mine], decisive=[p.name for p in past]
            )  # fmt: skip
        elif mine:
            statuses = sorted({answers[p.name][0].status for p in mine})
            result.items[item_id] = ItemVerdict(
                item, "unexercised", f"no probe got past the auth check to the handler "
                f"(answers {statuses})", [p.name for p in mine]
            )  # fmt: skip
        else:
            result.items[item_id] = ItemVerdict(item, "unexercised", "no probe of the route ran")


def _raw_contradictions(v: ItemVerdict, probes: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """An R1 item's probes whose recorded answer contradicts it, read from the answers
    themselves (none of the refusal rules): the anonymous probe -- no user, no token,
    no invented credentials -- answered 401/403 on a route predicted to admit anyone,
    or 2xx on one predicted to admit only some roles."""
    if v.item.kind != "R1" or "allowed" not in v.item.predicted:
        return []
    anyone = v.item.predicted["allowed"] == "anyone"
    out = []
    for name in v.probes:
        rec = probes.get(name)
        if rec is None or rec.get("credential") or name.split(" | ")[1:2] != ["no caller"]:
            continue
        req = rec.get("request") or {}
        if req.get("user") is not None or req.get("token") is not None:
            continue
        status = rec.get("status")
        if not isinstance(status, int):
            continue
        if (anyone and status in (401, 403)) or (not anyone and 200 <= status < 300):
            out.append(name)
    return out


def check_verdicts(result: RuleResult) -> None:
    """The self-check: no item is ``verified`` while one of its recorded probes
    contradicts its prediction -- as judged (``RuleResult.contradicted``) or, for R1,
    as the anonymous answer itself shows (:func:`_raw_contradictions`) -- nor without a
    decisive probe supporting it (``ItemVerdict.decisive``: an answer with one
    explanation, the one predicted; when the probe's record states its bearing, that
    bearing is decisive too). One that is raises :class:`VerdictInvariantError`: an
    internal error of a verdict path."""
    bad = []
    for item_id, v in result.items.items():
        if v.verdict != "verified":
            continue
        against = list(result.contradicted.get(item_id, ())) + _raw_contradictions(v, result.probes)
        if against:
            bad.append(f"{item_id}: verified although {', '.join(dict.fromkeys(against))} "
                       "contradict it")  # fmt: skip
        support = [n for n in v.decisive
                   if (result.probes.get(n, {}).get("bearing", {}).get(item_id) or
                       {"decisive": True})["decisive"]]  # fmt: skip
        if not support:
            bad.append(f"{item_id}: verified with no decisive probe supporting it")
        if v.item.kind == "T1" and v.item.predicted.get("ownership") == "shared":
            if v.item.predicted.get("writes"):
                # reads alone never verify "shared" on a table with write routes
                if not any(result.probes.get(n, {}).get("shared_write") for n in support):
                    bad.append(f"{item_id}: verified shared on reads alone; its rows are "
                               "written by " + ", ".join(v.item.predicted["writes"])
                               + " and no decisive write probe supports it")  # fmt: skip
            elif _NO_SHARED_WRITES.format(table=v.item.subject) not in v.reason:
                bad.append(f"{item_id}: verified shared on reads; its reason must say "
                           f"'{_NO_SHARED_WRITES.format(table=v.item.subject)}'")  # fmt: skip
    if bad:
        raise VerdictInvariantError("; ".join(bad))


def summarise(spec: Spec, result: RuleResult) -> None:
    """Table (§1.1) and route (§1.2) verdicts over the items run (again after the
    importer's corrections: ``verified (corrected)`` counts as verified), after the
    self-check (:func:`check_verdicts`)."""
    check_verdicts(result)
    contracts = {c.endpoint: c for c in route_contracts(spec)}
    result.tables.clear()
    result.routes.clear()
    by_subject: dict[str, list[ItemVerdict]] = {}
    for v in result.items.values():
        by_subject.setdefault(v.item.subject, []).append(v)
    tables, _ = tables_of(spec.profile)
    for name in tables:
        found = by_subject.get(name, [])
        if not found:
            continue
        t1 = next((v for v in found if v.item.kind == "T1"), None)
        bad = [v.item.id for v in found if v.verdict in ("refuted", "unclear")]
        if t1 is not None and t1.verdict == "unexercised" and t1.reason.startswith(OUTSIDE_API):
            verdict, reason = OUTSIDE_API, t1.reason
        elif t1 is not None and t1.verdict == "unexercised":
            verdict, reason = "unverified", "unreached"
        elif bad:
            verdict, reason = "unverified", "items " + ", ".join(bad)
        elif t1 is not None and not is_verified(t1.verdict):
            verdict, reason = "unverified", f"T1 {t1.verdict}"
        else:
            verdict, reason = "verified", ""
        result.tables[name] = {
            "verdict": verdict,
            "reason": reason,
            "items": {v.item.id: v.verdict for v in found},
        }
    for route in contracts:
        found = by_subject.get(route, [])
        if not found:
            continue
        r1 = next((v for v in found if v.item.kind == "R1"), None)
        r2 = next((v for v in found if v.item.kind == "R2"), None)
        r3_bad = [
            v.item.id for v in found if v.item.kind == "R3" and v.verdict in ("refuted", "unclear")
        ]
        reasons = []
        if r1 is not None and not is_verified(r1.verdict):
            reasons.append(f"R1 {r1.verdict}")
        if r2 is not None and not is_verified(r2.verdict):
            reasons.append(f"R2 {r2.verdict}")
        if r3_bad:
            reasons.append("items " + ", ".join(r3_bad))
        result.routes[route] = {
            "verdict": "unverified" if reasons else "verified",
            "reason": "; ".join(reasons),
            "items": {v.item.id: v.verdict for v in found},
        }


__all__ = [
    "Answer",
    "Caller",
    "ItemVerdict",
    "Outcome",
    "Probe",
    "ProbeContext",
    "RuleItem",
    "RuleResult",
    "Session",
    "SessionFactory",
    "VERDICTS",
    "OUTSIDE_API",
    "VERIFIED_CORRECTED",
    "VerdictInvariantError",
    "allowed_roles",
    "check_verdicts",
    "takes_credentials",
    "asgi_sessions",
    "is_verified",
    "summarise",
    "build_probes",
    "dispose",
    "owner_scope",
    "route_contracts",
    "rule_items",
    "run_probes",
]
