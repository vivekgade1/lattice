"""The extractor's ``RepoModel`` as the request-suite generator's inputs (spec 2026-09-27
importer §3.3): :class:`~lattice.importer.suite.RouteSpec` per route,
:class:`~lattice.importer.suite.TableSpec` per table and the
:class:`~lattice.importer.suite.AuthSpec`.

Deliberately independent of the lifters: what a route's suite exercises comes from the
original (its method, path, parameters, request models, ``raise`` sites, dependencies)
and the generated profile (owner and private columns), never from the lift being
verified. A wrong guess here can only leave a branch unexercised, and ``verify``'s
branch coverage reports every ``raise`` site no case reached -- the route is then
unverified, not accepted.

* kind: ``login`` (an OAuth2 password form), else by method -- GET with a path id
  ``read``, GET with offset/limit query parameters ``list``, GET/POST returning the
  caller ``me``, POST with a body ``create``, PUT/PATCH ``update`` (``update_me``
  without a path id), DELETE with a path id ``delete``; anything else ``other``.
* errors: per ``raise HTTPException`` of the route, in order -- 404 with a path id
  ``not_found``; 403 where the route compares an owner column with the caller
  ``not_owner``; 400/409 when a body field writes a UNIQUE column ``unique``; a login's
  first raise ``bad_credentials`` and the one with the active check's detail
  ``inactive_login``; an Admin route adds the superuser dependency's ``not_admin``.
* auth: the users table and its email / password-hash / active / admin columns, the
  login route, the native that hashes passwords (the one whose result the package
  stores in the password-hash column) and the one the login route mints tokens with
  (taking the users key: ``Uuid`` or ``Int``).

Never a crash on an unfamiliar app: a table with a column type the suite cannot seed
has no spec and the routes touching it are skipped with the reason; with no users
table, no auth the profile can run or no role for callers (:func:`auth_blocker`),
every authenticated route is skipped with the reason (a language gap is marked as
one) while Public routes keep their suites.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.importer.extract import (
    FieldSpec,
    NativeCandidate,
    RepoModel,
    Route,
    TableModel,
    _Index,
)
from lattice.importer.profile_gen import LANGUAGE_GAP, LIMIT_NAMES, OFFSET_NAMES
from lattice.importer.suite import (
    AuthSpec,
    ColumnSpec,
    ErrorSpec,
    ParamSpec,
    RouteSpec,
    SeedPlan,
    TableSpec,
    sqlmodel_insert_hook,
)
from lattice.profile import AdminFlag, ApiProfile, RoleColumn

COLUMN_TYPES = {
    "Str": "str",
    "Email": "email",
    "Int": "int",
    "Float": "float",
    "Bool": "bool",
    "Uuid": "uuid",
    "DateTime": "datetime",
}
AUTH_KINDS = {"Public": "public", "Customer": "user", "Admin": "admin"}
FORM_FIELDS = ("username", "password")
LOGIN_NAMES = ("email", "username", "login", "user_name")
PASSWORD_HINT = "password"


class Unsupported(ValueError):
    """The route (or table) has something the suite generator cannot express."""


@dataclass
class SuiteInputs:
    """``routes``/``skipped`` are keyed by :func:`route_key`; ``keys`` maps (module,
    route name) to it."""

    routes: dict[str, RouteSpec]
    skipped: dict[str, str]
    tables: tuple[TableSpec, ...]
    auth: AuthSpec
    keys: dict[tuple[str, str], str] = field(default_factory=dict)
    insert_hook: Callable[[Path, SeedPlan], None] | None = None


def route_key(model: RepoModel, route: Route) -> str:
    """The route's name, qualified by its module's last part when the name repeats."""
    same = [r for r in model.routes if r.name == route.name]
    return route.name if len(same) == 1 else f"{route.module.rsplit('.', 1)[-1]}_{route.name}"


def _bounds(f: FieldSpec) -> dict[str, Any]:
    ge = f.ge if f.ge is not None else (f.gt + 1 if isinstance(f.gt, int) else None)
    le = f.le if f.le is not None else (f.lt - 1 if isinstance(f.lt, int) else None)
    return {"min_len": f.min_length, "max_len": f.max_length, "ge": ge, "le": le}


def table_spec(t: TableModel, api: ApiProfile) -> TableSpec:
    cols = []
    for c in t.columns:
        kind = COLUMN_TYPES.get(c.type or "")
        if kind is None:
            raise Unsupported(f"{t.name}.{c.name}: column type {c.annotation}")
        cols.append(
            ColumnSpec(
                c.name,
                kind,
                nullable=c.nullable,
                primary_key=c.primary_key,  # type: ignore[arg-type]
                unique=c.unique,
                foreign_key=c.foreign_key,
                choices=c.choices,
                **_bounds(c),
            )
        )
    if sum(c.primary_key for c in cols) == 0:
        raise Unsupported(f"{t.name}: no primary key column")
    schema = api.tables.get(t.name)
    owner = schema.owner if schema else None
    if owner is None:
        # the seed's ownership only (which rows a request calls its own), never the
        # profile's: the one foreign key to the users table
        users = api.table_names.get(api.users_table, api.users_table)
        fks = [c.name for c in cols if (c.foreign_key or "").split(".")[0] == users]
        owner = fks[0] if len(fks) == 1 and t.name != api.users_table else None
    return TableSpec(t.table_name, tuple(cols), owner=owner)


def users_model(model: RepoModel, api: ApiProfile) -> TableModel | None:
    """The users table: the profile's, else the one the current-user dependencies return
    (the profile leaves ``users_table`` unset when it has no auth for it)."""
    tables = {t.name: t for t in model.tables}
    if api.users_table in tables:
        return tables[api.users_table]
    found = {d.returns for d in model.dependencies if d.kind == "current_user" and d.returns}
    return tables.get(found.pop()) if len(found) == 1 else None


def auth_blocker(model: RepoModel, api: ApiProfile) -> str | None:
    """Why no authenticated route can be verified against this profile, or None: no
    users table, no auth the profile can run, or no role for callers (a language gap)."""
    users = users_model(model, api)
    if users is None:
        return "no users table: no current-user dependency names one"
    problems = []
    if api.users_table != users.name:
        decoder = next((n for n in model.natives if n.kind == "token_decoder"), None)
        why = (
            f"the token decoder is not expressible: {decoder.reason}"
            if decoder is not None and not decoder.expressible
            else f"the profile has no way to authenticate a {users.name}"
        )
        problems.append(f"auth unclear: {why}")
    names = {c.name for c in users.columns}
    match api.roles_from:
        case AdminFlag():
            pass
        case RoleColumn(column=column) if column not in names:
            problems.append(
                f"{LANGUAGE_GAP} (single-role auth): {users.name} has no role column and the "
                "app no admin flag; ApiProfile.roles_from cannot give callers a role"
            )
    return "; ".join(problems) or None


def _kind(model: RepoModel, route: Route, users: TableModel | None) -> str:
    sources = {p.source for p in route.params}
    path = "path" in sources
    body = "body" in sources
    if "form" in sources:
        return "login"
    method = route.method
    users_response = users is not None and _returns_users(model, route, users)
    if method == "GET":
        if path:
            return "read"
        if _pagination(route) is not None:
            return "list"
        return "me" if users_response else "other"
    if method == "POST":
        if body:
            return "create"
        return "me" if users_response and not path else "other"
    if method in ("PUT", "PATCH"):
        if not body:
            return "other"
        return "update" if path else "update_me"
    if method == "DELETE":
        return "delete" if path else "other"
    return "other"


def _returns_users(model: RepoModel, route: Route, users: TableModel) -> bool:
    name = route.response_model
    if name == users.name:
        return True
    m = next((m for m in model.models if m.name == name), None)
    return m is not None and bool(set(m.bases) & set(users.bases))


def _pagination(route: Route) -> tuple[str, str] | None:
    q = [p.name for p in route.params if p.source == "query" and p.type == "Int"]
    off = next((n for n in q if n in OFFSET_NAMES), None)
    lim = next((n for n in q if n in LIMIT_NAMES), None)
    return (off, lim) if off and lim else None


def _literal(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError) as err:
        raise Unsupported(f"default {text!r} is not a literal") from err


def _default(text: str | None, choices: tuple[str | int, ...]) -> Any:
    """A parameter's default; an Enum member's (``Status.TODO``) is its value."""
    if text is not None and choices:
        try:
            return _literal(text)
        except Unsupported:
            member = text.rsplit(".", 1)[-1]
            by_name = [c for c in choices if str(c).upper() == member.upper()]
            if len(by_name) == 1:
                return by_name[0]
            raise
    return _literal(text)


def _params(model: RepoModel, route: Route, table: TableModel | None) -> tuple[ParamSpec, ...]:
    out: list[ParamSpec] = []
    if table is not None:
        unique = {c.name for c in table.columns if c.unique}
    else:  # the route's table is not known: a column exactly one table declares unique
        named: dict[str, int] = {}
        for t in model.tables:
            for c in t.columns:
                if c.unique and not c.primary_key:
                    named[c.name] = named.get(c.name, 0) + 1
        unique = {n for n, k in named.items() if k == 1}
    for p in route.params:
        if p.source in ("path", "query"):
            kind = COLUMN_TYPES.get(p.type or "")
            if kind is None:
                raise Unsupported(f"parameter {p.name}: type {p.annotation}")
            out.append(
                ParamSpec(
                    p.name,
                    kind,
                    p.source,
                    required=p.required,  # type: ignore[arg-type]
                    default=_default(p.default, p.choices),
                    choices=p.choices,
                )
            )
        elif p.source == "body":
            m = next((m for m in model.models if m.name == p.type), None)
            if m is None:
                raise Unsupported(f"body {p.name}: model {p.annotation} not found")
            for f in m.fields:
                kind = COLUMN_TYPES.get(f.type or "")
                if kind is None:
                    raise Unsupported(f"body field {f.name}: type {f.annotation}")
                out.append(
                    ParamSpec(
                        f.name,
                        kind,
                        "body",
                        required=f.required,
                        nullable=f.nullable,  # type: ignore[arg-type]
                        column=f.name if f.name in unique else None,
                        choices=f.choices,
                        **_bounds(f),
                    )
                )
        elif p.source == "form":
            out += [ParamSpec(n, "str", "body") for n in FORM_FIELDS]
        # dependency and framework parameters (Request...) are not request data
    return tuple(out)


def _errors(
    model: RepoModel, route: Route, kind: str, params: tuple[ParamSpec, ...]
) -> tuple[ErrorSpec, ...]:
    out: list[ErrorSpec] = []
    path = any(p.source == "path" for p in params)
    compares = any(u.how == "compare" for u in route.owner_uses)
    unique = next((p.column for p in params if p.source == "body" and p.column), None)
    active = next((d.active_detail for d in model.dependencies if d.active_column), None)
    for i, r in enumerate(route.raises):
        if kind == "login":
            if i == 0 and r.status is not None:
                out.append(ErrorSpec(r.status, r.detail, "bad_credentials"))
            elif r.detail == active and r.status is not None:
                out.append(ErrorSpec(r.status, r.detail, "inactive_login"))
        elif r.status == 404 and path:
            out.append(ErrorSpec(404, r.detail, "not_found"))
        elif r.status == 403 and path and compares:
            out.append(ErrorSpec(403, r.detail, "not_owner"))
        elif r.status in (400, 409) and unique is not None:
            out.append(ErrorSpec(r.status, r.detail, "unique", column=unique))
    if route.auth == "Admin":
        admin = next((d for d in model.dependencies if d.kind == "admin"), None)
        if admin is not None and admin.admin_status is not None:
            out.append(ErrorSpec(admin.admin_status, admin.admin_detail, "not_admin"))
    return tuple(out)


def route_spec(
    model: RepoModel, route: Route, api: ApiProfile, blocker: str | None = None
) -> RouteSpec:
    if route.auth not in AUTH_KINDS:
        raise Unsupported(f"auth {route.auth}: {route.auth_reason}")
    if route.auth != "Public" and blocker is not None:
        raise Unsupported(blocker)
    if route.enclosing is not None:
        raise Unsupported(
            f"defined inside {route.enclosing}(): the original cannot be referenced for "
            "branch coverage"
        )
    tables = {t.name: t for t in model.tables}
    users = users_model(model, api)
    kind = _kind(model, route, users)
    if len(route.tables) == 1:
        table: TableModel | None = tables[route.tables[0]]
    elif not route.tables and kind in ("me", "update_me", "login"):
        table = users
    else:
        table = None
    params = _params(model, route, table)
    return RouteSpec(
        route_key(model, route),
        route.method,
        route.path,
        kind,  # type: ignore[arg-type]
        table.table_name if table is not None else None,
        auth=AUTH_KINDS[route.auth],  # type: ignore[arg-type]
        params=params,
        body="form" if kind == "login" else "json",
        pagination=_pagination(route) if kind == "list" else None,
        errors=_errors(model, route, kind, params),
    )


# ---- auth ----------------------------------------------------------------------------------


def _natives_called(index: _Index, model: RepoModel, fn: ast.AST, module: str) -> list[str]:
    by_origin = {n.origin: n.name for n in model.natives if n.expressible}
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            name = by_origin.get(index.resolve(module, node.func) or "")
            if name is not None:
                out.append(name)
    return out


def _hash_native(index: _Index, model: RepoModel, column: str) -> str | None:
    """The native whose result the package stores in ``column`` (a dict entry, keyword
    or attribute assignment of that name) -- directly, or through a local name assigned
    once in the same function (``h = hash_password(p); User(password=h)``)."""
    by_origin = {n.origin: n.name for n in model.natives if n.expressible}
    found: list[str] = []

    def note(mod: str, value: ast.expr | None, local: dict[str, ast.expr | None]) -> None:
        if isinstance(value, ast.Name):
            value = local.get(value.id)
        if isinstance(value, ast.Call):
            name = by_origin.get(index.resolve(mod, value.func) or "")
            if name is not None and name not in found:
                found.append(name)

    for mod, m in index.modules.items():
        scopes: list[ast.AST] = [m.tree]
        functions = (ast.FunctionDef, ast.AsyncFunctionDef)
        scopes += [n for n in ast.walk(m.tree) if isinstance(n, functions)]
        for scope in scopes:
            local = _single_assignments(scope) if scope is not m.tree else {}
            for node in ast.walk(scope):
                if isinstance(node, ast.Dict):
                    for k, v in zip(node.keys, node.values, strict=True):
                        if isinstance(k, ast.Constant) and k.value == column:
                            note(mod, v, local)
                elif isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == column:
                            note(mod, kw.value, local)
                elif isinstance(node, ast.Assign):
                    if any(isinstance(t, ast.Attribute) and t.attr == column for t in node.targets):
                        note(mod, node.value, local)
    return found[0] if len(found) == 1 else None


def _single_assignments(fn: ast.AST) -> dict[str, ast.expr | None]:
    """Local name -> its value, for the names ``fn`` assigns exactly once (a name
    assigned more than once maps to None)."""
    out: dict[str, ast.expr | None] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                out[target.id] = node.value if target.id not in out else None
    return out


def auth_spec(
    model: RepoModel,
    api: ApiProfile,
    index: _Index,
    natives: Mapping[str, Callable[..., Any]],
    specs: Mapping[str, RouteSpec],
    settings: Mapping[str, Any] | None = None,
) -> AuthSpec:
    users = users_model(model, api)
    if users is None:
        # no users table: only Public routes have a suite (see auth_blocker); the seed
        # has no users rows
        return AuthSpec(users_table="", active_column=None, admin_column=None)
    key = next((c for c in users.columns if c.primary_key), None)
    email = next((c.name for c in users.columns if c.type == "Email" and c.unique), None)
    if email is None:
        # the login name column: a unique text column, else the one named like it
        texts = [c for c in users.columns if c.type in ("Str", "Email") and not c.primary_key]
        email = next((c.name for c in texts if c.unique), None) or next(
            (c.name for c in texts if c.name in LOGIN_NAMES), "email"
        )
    schema = api.tables.get(users.name)
    private = list(schema.private) if schema is not None else []
    password = private[0] if len(private) == 1 else None
    if password is None:
        hashed = [c.name for c in users.columns if c.type == "Str" and PASSWORD_HINT in c.name]
        password = hashed[0] if len(hashed) == 1 else "hashed_password"
    admin = next((d.admin_column for d in model.dependencies if d.kind == "admin"), None)
    login = next(
        (
            r
            for r in model.routes
            if specs.get(route_key(model, r)) is not None
            and specs[route_key(model, r)].kind == "login"
        ),
        None,
    )
    mint = None
    key_scalar = "INT" if key is not None and key.type == "Int" else "UUID"
    if login is not None:
        found = index.node(f"{login.module}:{login.name}")
        sigs = api.natives
        for name in _natives_called(index, model, found[1], login.module) if found else []:
            sig = sigs.get(name)
            if sig is not None and len(sig.params) == 1 and sig.params[0].name == key_scalar:
                mint = natives.get(name)
                break
    hasher = _hash_native(index, model, password)
    decoder = next(
        (n for n in model.natives if n.kind == "token_decoder" and n.expressible), None
    )
    minter = None
    if mint is None and decoder is not None:
        minter = claims_minter(decoder, settings or {})
    return AuthSpec(
        users_table=users.table_name,
        email_column=email,
        password_column=password,
        active_column=api.active_column,
        admin_column=admin,
        key_type=COLUMN_TYPES.get(key.type or "", "uuid") if key is not None else "uuid",  # type: ignore[arg-type]
        login_path=login.path if login is not None else None,
        hash_password=natives.get(hasher) if hasher else None,
        mint_token=mint,
        mint_claims=minter,
        claim=decoder.claim if decoder is not None and decoder.claim else "sub",
        subject_column=decoder.lookup if decoder is not None else None,
    )


MINTED_TOKEN_SECONDS = 7 * 24 * 3600  # test tokens: outlive any import run


def claims_minter(
    decoder: NativeCandidate, settings: Mapping[str, Any]
) -> Callable[[Mapping[str, Any]], str] | None:
    """A function signing claims as ``decoder``'s decode verifies them: the same
    library's ``encode`` (PyJWT and python-jose share ``encode(claims, key,
    algorithm=)``), key and first algorithm, evaluated where the decode reads them -- the
    decode expression (its imports, ``ctx`` = the NativeContext ``settings``) or, when
    that is a call of a package helper, the ``<lib>.decode(<param>, key, ...)`` inside
    the helper (its module's globals). None when neither shape is found."""
    if decoder.decode_expr is None:
        return None
    try:
        call = ast.parse(decoder.decode_expr, mode="eval").body
        ns: dict[str, Any] = {"ctx": dict(settings)}
        for line in decoder.imports:
            exec(line, ns)  # noqa: S102 -- the package's own import lines
        found = _decode_call(call)
        if found is None and isinstance(call, ast.Call):
            helper = eval(compile(ast.Expression(call.func), "<decode>", "eval"), ns)  # noqa: S307
            helper = inspect.unwrap(helper)
            tree = ast.parse(textwrap.dedent(inspect.getsource(helper)))
            fn = tree.body[0]
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) or not fn.args.args:
                return None
            param = fn.args.args[0].arg
            found = next(
                (
                    c
                    for n in ast.walk(fn)
                    if (c := _decode_call(n)) is not None
                    and isinstance(c.args[0], ast.Name)
                    and c.args[0].id == param
                ),
                None,
            )
            ns = dict(helper.__globals__)
        if found is None:
            return None

        def value(node: ast.expr) -> Any:
            return eval(compile(ast.Expression(node), "<decode>", "eval"), ns)  # noqa: S307

        encode = value(found.func.value).encode  # type: ignore[attr-defined]
        key = value(found.args[1])
        algorithms = next((value(k.value) for k in found.keywords if k.arg == "algorithms"), None)
        if isinstance(algorithms, list | tuple):
            algorithms = algorithms[0] if algorithms else None
        algorithm = algorithms if isinstance(algorithms, str) else "HS256"
    except Exception:  # noqa: BLE001 -- an unfamiliar decode: no minter
        return None

    def mint(claims: Mapping[str, Any]) -> str:
        body = {"exp": int(time.time()) + MINTED_TOKEN_SECONDS, **claims}
        token = encode(body, key, algorithm=algorithm)
        return token.decode() if isinstance(token, bytes) else str(token)

    return mint


def _decode_call(node: ast.AST) -> ast.Call | None:
    """``<lib>.decode(token, key, ...)``."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "decode"
        and len(node.args) >= 2
    ):
        return node
    return None


def _insert_hook(tables: Sequence[TableModel]) -> Callable[[Path, SeedPlan], None] | None:
    """The seed written through the original's own SQLModel classes."""
    try:
        classes = {
            t.table_name: getattr(importlib.import_module(t.module), t.name) for t in tables
        }
    except (ImportError, AttributeError):
        return None

    def engine(path: Path) -> Any:
        from sqlalchemy import create_engine

        return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    return sqlmodel_insert_hook(classes, engine)


def adapt(
    model: RepoModel,
    api: ApiProfile,
    index: _Index,
    natives: Mapping[str, Callable[..., Any]],
    settings: Mapping[str, Any] | None = None,
) -> SuiteInputs:
    """Every route's :class:`RouteSpec` (or why not), the tables and the auth
    (``settings``: the NativeContext values, for a token minter reading them)."""
    specs: list[TableSpec] = []
    unseedable: dict[str, str] = {}  # table class -> why it has no spec
    for t in model.tables:
        try:
            specs.append(table_spec(t, api))
        except Unsupported as err:
            unseedable[t.name] = str(err)
    tables = tuple(specs)
    blocker = auth_blocker(model, api)
    users = users_model(model, api)
    if users is not None and users.name in unseedable:
        blocker = f"the users table cannot be seeded: {unseedable[users.name]}"
    routes: dict[str, RouteSpec] = {}
    skipped: dict[str, str] = {}
    keys: dict[tuple[str, str], str] = {}
    for r in model.routes:
        key = route_key(model, r)
        keys[(r.module, r.name)] = key
        bad = [unseedable[t] for t in r.tables if t in unseedable]
        if bad:
            skipped[key] = "a table it touches cannot be seeded: " + "; ".join(bad)
            continue
        try:
            routes[key] = route_spec(model, r, api, blocker)
        except Unsupported as err:
            skipped[key] = str(err)
        except Exception as err:  # noqa: BLE001 -- never crash on unfamiliar code
            skipped[key] = f"the suite generator failed on the route: {type(err).__name__}: {err}"
    auth = auth_spec(model, api, index, natives, routes, settings)
    seeded = [t for t in model.tables if t.name not in unseedable]
    return SuiteInputs(routes, skipped, tables, auth, keys, _insert_hook(seeded))


__all__ = [
    "SuiteInputs",
    "Unsupported",
    "adapt",
    "auth_blocker",
    "route_key",
    "route_spec",
    "table_spec",
    "users_model",
]
