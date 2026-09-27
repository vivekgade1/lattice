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
  stores in the password-hash column) and the one the login route mints tokens with.
"""

from __future__ import annotations

import ast
import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.importer.extract import FieldSpec, RepoModel, Route, TableModel, _Index
from lattice.importer.profile_gen import LIMIT_NAMES, OFFSET_NAMES
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
from lattice.profile import ApiProfile

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
                **_bounds(c),
            )
        )
    schema = api.tables.get(t.name)
    return TableSpec(t.table_name, tuple(cols), owner=schema.owner if schema else None)


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


def _params(model: RepoModel, route: Route, table: TableModel | None) -> tuple[ParamSpec, ...]:
    out: list[ParamSpec] = []
    unique = {c.name for c in table.columns if c.unique} if table is not None else set()
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
                    default=_literal(p.default),
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
                        **_bounds(f),
                    )
                )
        elif p.source == "form":
            out += [ParamSpec(n, "str", "body") for n in FORM_FIELDS]
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


def route_spec(model: RepoModel, route: Route, api: ApiProfile) -> RouteSpec:
    if route.auth not in AUTH_KINDS:
        raise Unsupported(f"auth {route.auth}")
    tables = {t.name: t for t in model.tables}
    users = tables.get(api.users_table or "")
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
    or attribute assignment of that name)."""
    by_origin = {n.origin: n.name for n in model.natives if n.expressible}
    found: list[str] = []

    def note(mod: str, value: ast.expr | None) -> None:
        if isinstance(value, ast.Call):
            name = by_origin.get(index.resolve(mod, value.func) or "")
            if name is not None and name not in found:
                found.append(name)

    for mod, m in index.modules.items():
        for node in ast.walk(m.tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values, strict=True):
                    if isinstance(k, ast.Constant) and k.value == column:
                        note(mod, v)
            elif isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == column:
                        note(mod, kw.value)
            elif isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Attribute) and t.attr == column for t in node.targets):
                    note(mod, node.value)
    return found[0] if len(found) == 1 else None


def auth_spec(
    model: RepoModel,
    api: ApiProfile,
    index: _Index,
    natives: Mapping[str, Callable[..., Any]],
    specs: Mapping[str, RouteSpec],
) -> AuthSpec:
    users = next((t for t in model.tables if t.name == api.users_table), None)
    if users is None:
        raise Unsupported("no users table")
    email = next((c.name for c in users.columns if c.type == "Email" and c.unique), "email")
    schema = api.tables.get(users.name)
    private = list(schema.private) if schema is not None else []
    password = private[0] if len(private) == 1 else "hashed_password"
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
    if login is not None:
        found = index.node(f"{login.module}:{login.name}")
        sigs = api.natives
        for name in _natives_called(index, model, found[1], login.module) if found else []:
            sig = sigs.get(name)
            if sig is not None and len(sig.params) == 1 and sig.params[0].name == "UUID":
                mint = natives.get(name)
                break
    hasher = _hash_native(index, model, password)
    return AuthSpec(
        users_table=users.table_name,
        email_column=email,
        password_column=password,
        active_column=api.active_column,
        admin_column=admin,
        login_path=login.path if login is not None else None,
        hash_password=natives.get(hasher) if hasher else None,
        mint_token=mint,
    )


def _insert_hook(model: RepoModel) -> Callable[[Path, SeedPlan], None] | None:
    """The seed written through the original's own SQLModel classes."""
    try:
        classes = {
            t.table_name: getattr(importlib.import_module(t.module), t.name) for t in model.tables
        }
    except (ImportError, AttributeError):
        return None

    def engine(path: Path) -> Any:
        from sqlalchemy import create_engine

        return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    return sqlmodel_insert_hook(classes, engine)


def adapt(
    model: RepoModel, api: ApiProfile, index: _Index, natives: Mapping[str, Callable[..., Any]]
) -> SuiteInputs:
    """Every route's :class:`RouteSpec` (or why not), the tables and the auth."""
    tables = tuple(table_spec(t, api) for t in model.tables)
    routes: dict[str, RouteSpec] = {}
    skipped: dict[str, str] = {}
    keys: dict[tuple[str, str], str] = {}
    for r in model.routes:
        key = route_key(model, r)
        keys[(r.module, r.name)] = key
        try:
            routes[key] = route_spec(model, r, api)
        except Unsupported as err:
            skipped[key] = str(err)
    auth = auth_spec(model, api, index, natives, routes)
    return SuiteInputs(routes, skipped, tables, auth, keys, _insert_hook(model))


__all__ = ["SuiteInputs", "Unsupported", "adapt", "route_key", "route_spec", "table_spec"]
