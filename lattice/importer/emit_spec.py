"""The candidate spec of an import, and the spec module it is written as (Phase 2 plan
2026-10-05, §1, §2.4 and §5; W2-D).

**The candidate spec** (:func:`build_spec`, :func:`candidate_spec`): the generated
profile, the verified programs, the seed's users and one :class:`~lattice.spec.Contract`
per route of the app model, read from what the front end found -- nothing here is
verified; layer R (:func:`import_rules`) probes it on the original:

* ``auth``: the route's ``auth`` when the front end decided it (``Public``,
  ``Authenticated``, ``Admin``, a role, ``Roles(...)``); else from its guards (an
  ``admin`` guard: ``Admin``; ``role`` guards: ``Roles(...)`` of the roles they all let
  through, role values named as the profile names them; ``authenticated`` /
  ``object_owner``: ``Authenticated``); a ``custom`` guard or nothing known: None
  (unknown, so R1 is ``unclear``);
* ``table``: the table the route returns rows of, else the one it compares or sets an
  owner column of, else the one table it touches besides the users table (the users
  table when that is all it touches; a route touching none: the table its response
  model is named after, ``UserPublic`` -> ``User``); ``kind``: the login route
  ``action`` (``create`` when it writes the caller's own row: microblog's tokens); GET
  with a path id or one response model ``read`` (``list`` under a parent row --
  ``/users/{id}/posts`` -- or for a page envelope, a ``Paginated...`` or ``List...``
  model), else ``list``; POST with a body ``create`` (``update`` when it addresses one
  row of its own table by its key and the path names no other table after it --
  ``/recipes/{pk}/upload-image/``), else ``action``; PUT/PATCH ``update``; DELETE
  ``delete``;
* ``owner_scope`` (owned tables only; None for an admin-only route -- ``Admin``,
  ``Roles(<admin>)`` -- which no non-admin reaches): ``owner`` when the route compares
  or sets an owner column of the table or of a table it is owned through, or has an
  ``object_owner`` guard; else ``any`` (stated: the route reaches anyone's rows). Two
  forms name a column (W3b): ``owner(<column>)`` on a link between users (two users
  foreign keys as its whole key) when the route compares only the side that is not the
  owner (microblog's ``/me/followers``: ``followed_id``), and
  ``owner_or_public(<column>)`` when the route's owner check lets a row through on a
  Bool column (``row.owner != caller and not row.<column>``, in the route or a package
  function it calls: :func:`public_scopes`, JustNotes' ``isPublic``). On a link between
  users a POST setting the owner side from the caller is a ``create`` (its 400/409 the
  ``duplicate``), and a path id names the other user (``ref`` the users table);
* ``params``: path and query parameters (named as on the wire: a ``Query(alias=...)``),
  and every body field (a list of models as ``name.0.field``, a list of values as
  ``name.0``), with type, required, bounds (``ge``/``gt``/``le``/``lt``,
  ``min_len``/``max_len``) and ``one_of``; a bounded field names the column it is
  stored in (the route's table, else the one table of the route with that column), a
  path id or ``<table>_id`` field the table whose key it is (``ref``; a generic ``id``
  / ``pk`` the table the path segment before it names);
* ``errors``: one per distinct raise site (status, literal detail): ``not_found`` (404
  with a path id), ``not_owned`` (403/404 where the route checks ownership, with a path
  id), ``duplicate`` (400/409 when a body field writes a unique column), ``other``
  otherwise (the login's first: with a wrong-credentials ``example``); plus
  ``invalid`` (the profile's validation status) when a parameter is bounded or listed;
* ``response``: the route's response model when the profile knows it.

Accepted proposals (W2-C's ``Proposal(kind, spec_object, evidence, support, source)``)
add their ``spec_object``: ``invariant`` -> ``Spec.invariants``, ``machine`` ->
``machines``, ``unchecked`` -> ``unchecked``, ``contract`` -> replaces the route's.

**The spec module** (:func:`spec_source`, :func:`write_spec`, :func:`load_spec`): a
Python module defining ``SPEC`` that loads its profile from ``PROFILE_FILE`` beside it
(the import's ``profile.py``: the profile is generated source, kept as it is), holds the
programs, users, contracts, machines, invariants and unchecked requirements as
literals, and ``STATUS`` (rule item or proposal id -> status), each status also written
as a comment on its contract. It round-trips: loading the module and emitting its
``SPEC`` and ``STATUS`` again gives the same text. ``spec_meta.json`` beside it holds
the per-item status (see :func:`write_spec`).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import MISSING, dataclass, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from lattice.importer.appmodel import FieldSpec, RepoModel, Route, TableModel
from lattice.profile import ApiProfile, Profile, RoleColumn
from lattice.spec import (
    Contract,
    ContractError,
    ContractParam,
    Invariant,
    Machine,
    Spec,
    Unchecked,
    validate,
)
from lattice.typesys import Scalar

if TYPE_CHECKING:
    from lattice.importer.lift import ImportResult
    from lattice.spec.probes import RuleResult
    from lattice.spec.report import Report

__all__ = [
    "LoadedSpec",
    "build_spec",
    "candidate_spec",
    "contract_diff",
    "file_digest",
    "import_rules",
    "load_spec",
    "object_key",
    "probe_import",
    "proposal_record",
    "public_scopes",
    "route_contract",
    "route_contracts",
    "spec_object_from",
    "spec_object_to",
    "spec_source",
    "write_spec",
]

META_SCHEMA = "lattice-spec-meta/1"
SCALARS = {
    "Str": Scalar.STR,
    "Email": Scalar.STR,
    "Int": Scalar.INT,
    "Float": Scalar.FLOAT,
    "Bool": Scalar.BOOL,
    "Uuid": Scalar.UUID,
    "DateTime": Scalar.DATETIME,
    "Date": Scalar.DATE,
}
KEYWORD_AUTH = ("Public", "Authenticated", "Admin")
BOUNDS = ("ge", "gt", "le", "lt", "min_len", "max_len")
LOGIN_EXAMPLE = ("nobody@example.com", "wrong-password")


class ProposalLike(Protocol):
    """W2-C's proposal (``lattice.importer.mine``): what kind of spec object it proposes,
    the object, its evidence, support and source."""

    kind: str
    spec_object: Any


# ---- contracts ---------------------------------------------------------------------------


def _tables(model: RepoModel) -> dict[str, TableModel]:
    return {t.name: t for t in model.tables}


def _role_names(api: ApiProfile) -> dict[str, str]:
    """A role value or name -> the profile's role name."""
    names = {r: r for r in api.roles if r != "Public"}
    if isinstance(api.roles_from, RoleColumn):
        for value, name in dict(api.roles_from.values).items():
            names.setdefault(str(value), name)
            names.setdefault(str(value).lower(), name)
    return names


def _known_auth(auth: str | None, api: ApiProfile) -> str | None:
    if not auth or auth == "unclear":
        return None
    if auth in KEYWORD_AUTH or auth in api.roles:
        return auth
    m = re.fullmatch(r"Roles\((.*)\)", auth)
    if m:
        roles = [r.strip() for r in m.group(1).split(",") if r.strip()]
        return auth if roles and all(r in api.roles and r != "Public" for r in roles) else None
    return None


def _auth(route: Route, api: ApiProfile) -> str | None:
    """The route's auth (see the module docstring); None: unknown."""
    known = _known_auth(route.auth, api)
    if known is not None or not route.guards:
        return known
    kinds = {g.kind for g in route.guards}
    if "custom" in kinds:
        return None
    if "admin" in kinds:
        return "Admin"
    roles = [g for g in route.guards if g.kind == "role"]
    if roles:
        names = _role_names(api)
        allowed: list[str] | None = None
        for g in roles:
            mapped = [names.get(r, names.get(str(r).lower(), "")) for r in g.roles]
            if not g.roles or not all(mapped):
                return None
            allowed = mapped if allowed is None else [r for r in allowed if r in mapped]
        return f"Roles({', '.join(dict.fromkeys(allowed or ()))})" if allowed else None
    return "Authenticated" if kinds & {"authenticated", "object_owner"} else None


def _users_table(model: RepoModel, api: ApiProfile) -> str | None:
    return api.users_table if api.users_table in _tables(model) else None


def _table(model: RepoModel, route: Route, api: ApiProfile) -> str | None:
    tables = _tables(model)
    if route.returns in tables:
        return route.returns
    owned = [u.table for u in route.owner_uses if u.table in tables]
    if owned:
        return owned[0]
    users = _users_table(model, api)
    touched = [t for t in route.tables if t in tables]
    others = [t for t in touched if t != users]
    if len(others) == 1:
        return others[0]
    if not others and users in touched:
        return users
    if not touched and _is_login(model, route):
        return users
    if not touched and route.response_model is not None:
        return _response_table(model, route.response_model)
    return None


def _response_table(model: RepoModel, name: str) -> str | None:
    """The table a response model returns rows of: the table itself, a model sharing
    its bases, or one named after it (``UserPublic`` -> ``User``)."""
    tables = _tables(model)
    if name in tables:
        return name
    m = next((d for d in model.models if d.name == name), None)
    for t in model.tables:
        if m is not None and set(m.bases) & set(t.bases):
            return t.name
        rest = name[len(t.name) :]
        if name.startswith(t.name) and rest[:1].isupper():
            return t.name
    return None


def _is_login(model: RepoModel, route: Route) -> bool:
    if route.method != "POST":
        return False
    if model.auth is not None and model.auth.login_path is not None:
        return model.auth.login_path == route.path
    return any(p.source == "form" for p in route.params)


def _kind(model: RepoModel, route: Route) -> str:
    sources = {p.source for p in route.params}
    if _is_login(model, route):
        # a login writing the caller's own row of a table (microblog's tokens): a create
        stamps = any(u.how == "assign" for u in route.owner_uses)
        return "create" if stamps else "action"
    match route.method:
        case "GET":
            name = (route.response_model or "").lower()
            many = route.response_model is None or name.startswith(("list", "paginated", "page"))
            many = many or _envelope(model, route.response_model)
            return "read" if "path" in sources or not many else "list"
        case "POST":
            return "create" if sources & {"body", "form"} else "action"
        case "PUT" | "PATCH":
            return "update"
        case "DELETE":
            return "delete"
    return "action"


def _acts_on_one(model: RepoModel, route: Route, table: str | None) -> bool:
    """A POST addressing one row of its own table: its one path parameter is the
    table's key (``pk``, ``id``, ``<table>_id``) and the path does not go on to name
    another table's rows (``/recipes/{pk}/upload-image/``, not ``/posts/{id}/comments/``)."""
    holes = [p for p in route.params if p.source == "path"]
    if table is None or len(holes) != 1:
        return False
    name = holes[0].name
    if name not in ("pk", "id") and _ref_by_name_model(model, name) != table:
        return False
    after = route.path.split("{" + name + "}", 1)[-1].strip("/").split("/")[0]
    if not after:
        return False
    word = after.replace("-", "").replace("_", "").lower()
    spellings = set()
    for t in model.tables:
        for s in (t.name.lower(), t.table_name.replace("_", "").lower()):
            spellings |= {s, s + "s", s.removesuffix("s")}
    return word not in spellings


def _ref_by_name_model(model: RepoModel, name: str) -> str | None:
    if not name.endswith("_id"):
        return None
    stem = name[: -len("_id")].replace("_", "").lower()
    return next((t.name for t in model.tables if t.name.lower() == stem), None)


def _envelope(model: RepoModel, name: str | None) -> bool:
    """Whether a response model is a page envelope: one list of rows and counts."""
    m = next((d for d in model.models if d.name == name), None)
    if m is None:
        return False
    lists = [f for f in m.fields if f.type == "List"]
    return len(lists) == 1 and all(f.type in ("List", "Int") for f in m.fields)


def _owned(api: ApiProfile, table: str | None) -> bool:
    schema = api.tables.get(table) if table else None
    return schema is not None and bool(schema.owner or schema.owner_via)


def _owner_chain(api: ApiProfile, table: str) -> set[str]:
    """The table and every table it is owned through."""
    out = {table}
    schema = api.tables.get(table)
    while schema is not None and schema.owner_via and schema.owner_via[1] not in out:
        out.add(schema.owner_via[1])
        schema = api.tables.get(schema.owner_via[1])
    return out


def _owner_checked(route: Route, api: ApiProfile, table: str | None) -> bool:
    if table is None:
        return False
    chain = _owner_chain(api, table)
    return any(u.table in chain for u in route.owner_uses) or any(
        g.kind == "object_owner" for g in route.guards
    )


def _admin_only(auth: str | None, api: ApiProfile) -> bool:
    """Whether only the admin role passes ``auth`` (``Admin``, ``Roles(<admin>)``)."""
    if auth == "Admin":
        return True
    m = re.fullmatch(r"Roles\((.*)\)", auth or "")
    return bool(m) and {r.strip() for r in m.group(1).split(",")} == {api.admin_role}


def _users_link(model: RepoModel, api: ApiProfile, table: str | None) -> tuple[str, ...] | None:
    """A link between users (Phase 2 W3b): the table's whole primary key is two users
    foreign keys (microblog's ``followers``: ``follower_id``, ``followed_id``)."""
    tables = _tables(model)
    t, users = tables.get(table or ""), tables.get(api.users_table)
    if t is None or users is None:
        return None
    fks = tuple(c.name for c in t.columns
                if (c.foreign_key or "").split(".")[0] == users.table_name)  # fmt: skip
    pk = {c.name for c in t.columns if c.primary_key}
    return fks if len(fks) == 2 and pk == set(fks) else None


def _owner_scope(
    model: RepoModel,
    route: Route,
    api: ApiProfile,
    table: str | None,
    public: Mapping[str, tuple[str, str]] | None = None,
) -> str | None:
    """The route's owner scope (see the module docstring), with the two forms naming a
    column: ``owner(<column>)`` on a link between users when the route compares only
    the other side (``/me/followers``: ``followed_id``), ``owner_or_public(<column>)``
    when the route's own check lets a row through on a Bool column (``public``:
    :func:`public_scopes`)."""
    if not _owned(api, table):
        return None
    if not _owner_checked(route, api, table):
        return "any"
    schema = api.tables[table]  # type: ignore[index]
    link = _users_link(model, api, table)
    compared = {u.column for u in route.owner_uses if u.table == table}
    if link is not None and schema.owner not in compared and len(compared) == 1:
        side = next(iter(compared))
        if side in link:
            return f"owner({side})"
    found = (public or {}).get(f"{route.method} {route.path}")
    if found is not None and found[0] == table:
        return f"owner_or_public({found[1]})"
    return "owner"


def public_scopes(root: Path | str, package: str, model: RepoModel) -> dict[str, tuple[str, str]]:
    """Routes whose owner check lets anyone through on a Bool column of the row
    (``METHOD /path`` -> (table, column)): the route function, or a package function it
    calls (transitively, as JustNotes' ``_check_note_access``), tests ``row.<owner> !=
    <caller> and not row.<column>`` (or ``row.<owner> == <caller> or row.<column>``),
    where ``<owner>`` is a column the route compares to the caller (its owner evidence)
    and ``<column>`` a Bool column of that table. Read from the AST, as the front ends
    read routes; the app model is not changed (the FastAPI goldens pin it)."""
    import ast

    from lattice.importer.extract import _Index, route_function

    try:
        index = _Index(Path(root), package)
    except (FileNotFoundError, SyntaxError):
        return {}
    tables = _tables(model)

    def attr(node: ast.AST) -> tuple[str, str] | None:
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return node.value.id, node.attr
        return None

    def check(node: ast.BoolOp, owners: Mapping[str, str]) -> tuple[str, str] | None:
        want_op = ast.NotEq if isinstance(node.op, ast.And) else ast.Eq
        compared: dict[str, str] = {}  # row variable -> its owner column's table
        flags: list[tuple[str, str]] = []
        for v in node.values:
            if isinstance(v, ast.Compare) and len(v.ops) == 1 and isinstance(v.ops[0], want_op):
                for side in (v.left, v.comparators[0]):
                    a = attr(side)
                    if a is not None and a[1] in owners:
                        compared[a[0]] = owners[a[1]]
            flag = v.operand if isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.Not) else v
            if isinstance(v, ast.UnaryOp) != isinstance(node.op, ast.And):
                continue  # "and not row.c" / "or row.c"
            a = attr(flag)
            if a is not None:
                flags.append(a)
        for var, column in flags:
            table = compared.get(var)
            t = tables.get(table or "")
            col = next((c for c in t.columns if c.name == column), None) if t else None
            if col is not None and col.type == "Bool":
                return table, column  # type: ignore[return-value]
        return None

    out: dict[str, tuple[str, str]] = {}
    for r in model.routes:
        owners = {u.column: u.table for u in r.owner_uses if u.how == "compare"}
        fn = route_function(index, r) if owners else None
        if fn is None:
            continue
        seen: set[int] = set()
        todo: list[tuple[str, ast.AST]] = [(r.view.split(":", 1)[0] if r.view else r.module, fn)]
        found = None
        while todo and found is None and len(seen) < 40:
            mod, node = todo.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            for sub in ast.walk(node):
                if isinstance(sub, ast.BoolOp):
                    found = check(sub, owners)
                    if found is not None:
                        break
                elif isinstance(sub, ast.Call):
                    callee = index.node(index.resolve(mod, sub.func))
                    if callee is not None and isinstance(
                        callee[1], ast.FunctionDef | ast.AsyncFunctionDef
                    ):
                        todo.append(callee)
        if found is not None:
            out[f"{r.method} {r.path}"] = found
    return out


def _scalar(type_name: str | None) -> Scalar | None:
    return SCALARS.get(type_name or "")


def _one_of(choices: Sequence[str | int]) -> tuple[str, ...] | None:
    return tuple(str(c) for c in choices) if choices else None


def _ref_by_name(model: RepoModel, api: ApiProfile, name: str) -> str | None:
    """The table a ``<table>_id`` name refers to (by class or SQL name, singular)."""
    if not name.endswith("_id"):
        return None
    stem = name[: -len("_id")].replace("_", "").lower()
    for t in model.tables:
        spellings = {t.name.lower(), t.table_name.replace("_", "").lower()}
        spellings |= {s.removesuffix("s") for s in list(spellings)}
        if stem in spellings and t.name in api.tables:
            return t.name
    return None


def _ref_by_segment(model: RepoModel, api: ApiProfile, route: Route, hole: str) -> str | None:
    """The table a generic path parameter (``id``, ``pk``) keys: the one the path segment
    before it names (``/users/{id}/posts`` -> the users table)."""
    if hole not in ("id", "pk"):
        return None
    before = route.path.split("{" + hole + "}", 1)[0].strip("/").split("/")
    word = before[-1].replace("-", "").replace("_", "").lower() if before and before[-1] else ""
    if not word:
        return None
    for t in model.tables:
        names = {t.name.lower(), t.table_name.replace("_", "").lower()}
        if word in names | {n + "s" for n in names} | {n.removesuffix("s") for n in names}:
            return t.name if t.name in api.tables else None
    return None


def _column_for(model: RepoModel, route: Route, table: str | None, name: str) -> str | None:
    """``Table.column`` a body field named ``name`` is stored in: the route's table, else
    the one table of the route having that column."""
    tables = _tables(model)
    if table in tables and any(c.name == name for c in tables[table].columns):
        return f"{table}.{name}"
    found = [
        t for t in route.tables if t in tables and any(c.name == name for c in tables[t].columns)
    ]
    return f"{found[0]}.{name}" if len(found) == 1 else None


def _bounds(f: FieldSpec) -> dict[str, Any]:
    return {"ge": f.ge, "gt": f.gt, "le": f.le, "lt": f.lt,
            "min_len": f.min_length, "max_len": f.max_length}  # fmt: skip


def _body_params(
    model: RepoModel,
    route: Route,
    api: ApiProfile,
    table: str | None,
    model_name: str,
    prefix: str,
    source: str,
    seen: frozenset[str] = frozenset(),
) -> list[ContractParam]:
    data = {m.name: m for m in model.models} | {
        t.name: t for t in model.tables if t.name not in {m.name for m in model.models}
    }
    m = data.get(model_name)
    if m is None or model_name in seen:
        return []
    fields_ = m.fields if hasattr(m, "fields") else m.columns  # type: ignore[union-attr]
    out: list[ContractParam] = []
    for f in fields_:
        name = f"{prefix}{f.name}"
        if f.type in ("List", "Tuple") and f.item in data:
            out += _body_params(model, route, api, table, f.item, f"{name}.0.", source,
                                seen | {model_name})  # fmt: skip
            continue
        if f.type in ("List", "Tuple") and _scalar(f.item) is not None:
            # a list of values (DRF's many-related keys): one element, a row's key when
            # the field names a table
            ref = _ref_by_name(model, api, f.name) or _ref_by_name(
                model, api, f.name.removesuffix("s") + "_id"
            )
            out.append(ContractParam(f"{name}.0", source, _scalar(f.item),  # type: ignore[arg-type]
                                     required=f.required, ref=ref))  # fmt: skip
            continue
        if f.type in data:
            out += _body_params(model, route, api, table, f.type, f"{name}.", source,
                                seen | {model_name})  # fmt: skip
            continue
        scalar = _scalar(f.type)
        if scalar is None:
            continue
        bounds = {k: v for k, v in _bounds(f).items() if v is not None}
        if scalar is not Scalar.STR:
            bounds.pop("min_len", None), bounds.pop("max_len", None)
        if scalar not in (Scalar.INT, Scalar.FLOAT):
            for k in ("ge", "gt", "le", "lt"):
                bounds.pop(k, None)
        one_of = _one_of(f.choices)
        column = _column_for(model, route, table, f.name) if bounds or one_of else None
        out.append(
            ContractParam(
                name,
                source,
                scalar,
                required=f.required,
                one_of=one_of,
                column=column,
                ref=_ref_by_name(model, api, f.name),
                **bounds,
            )
        )
    return out


def _params(
    model: RepoModel, route: Route, api: ApiProfile, table: str | None, kind: str
) -> tuple[ContractParam, ...]:
    out: list[ContractParam] = []
    path_params = [p for p in route.params if p.source == "path"]
    for p in route.params:
        if p.source in ("path", "query"):
            scalar = _scalar(p.type)
            if scalar is None:
                continue
            ref = None
            if p.source == "path":
                ref = _ref_by_name(model, api, p.name) or _ref_by_segment(model, api, route, p.name)
                if ref is None and _users_link(model, api, table) is not None:
                    ref = api.users_table  # a link's other user: /me/following/{id}
                if ref is None and len(path_params) == 1 and table in api.tables:
                    ref = table if kind in ("read", "update", "delete") else None
            numeric = scalar in (Scalar.INT, Scalar.FLOAT)
            text = scalar is Scalar.STR
            out.append(
                ContractParam(
                    _wire_name(p),
                    p.source,
                    scalar,
                    required=p.required,
                    ge=p.ge if numeric else None,
                    le=p.le if numeric else None,
                    min_len=p.min_length if text else None,
                    max_len=p.max_length if text else None,
                    one_of=_one_of(p.choices),
                    ref=ref,
                )
            )
        elif p.source == "body" and p.type:
            out += _body_params(model, route, api, table, p.type, "", "body")
        elif p.source == "form":
            auth = model.auth
            user = auth.username_field if auth is not None else "username"
            password = auth.password_field if auth is not None else "password"
            out += [
                ContractParam(user, "form", Scalar.STR),
                ContractParam(password, "form", Scalar.STR),
            ]
    if _is_login(model, route) and not out and model.auth is not None:
        source = "form" if model.auth.login_encoding == "form" else "body"
        out = [ContractParam(model.auth.username_field, source, Scalar.STR),
               ContractParam(model.auth.password_field, source, Scalar.STR)]  # fmt: skip
    unique: dict[str, ContractParam] = {}
    for param in out:
        unique.setdefault(f"{param.source} {param.name}", param)
    return tuple(unique.values())


ALIAS = re.compile(r"""\balias\s*=\s*['"]([^'"]+)['"]""")


def _wire_name(p: Any) -> str:
    """A query or path parameter's name on the wire: its ``alias`` (``Query(alias=
    "status")``), else its name."""
    m = ALIAS.search(f"{p.annotation} {p.default or ''}")
    return m.group(1) if m else p.name


def _login_example(model: RepoModel, params: Sequence[ContractParam]) -> dict[str, Any]:
    user, password = LOGIN_EXAMPLE
    names = [p.name for p in params][:2] or ["username", "password"]
    values = dict(zip(names, (user, password), strict=False))
    encoding = model.auth.login_encoding if model.auth is not None else "form"
    return {"form": values} if encoding == "form" else {"json": values}


def _unique_columns(model: RepoModel, table: str | None) -> set[str]:
    t = _tables(model).get(table or "")
    return {c.name for c in t.columns if c.unique and not c.primary_key} if t else set()


def _errors(
    model: RepoModel,
    route: Route,
    api: ApiProfile,
    table: str | None,
    params: Sequence[ContractParam],
    kind: str | None = None,
) -> tuple[ContractError, ...]:
    out: list[ContractError] = []
    path = any(p.source == "path" for p in params)
    login = _is_login(model, route)
    checked = _owner_checked(route, api, table)
    unique = _unique_columns(model, table) & {p.name for p in params if p.source == "body"}
    # a create on a link between users: the link's key is the duplicate (a second follow)
    if kind == "create" and _users_link(model, api, table) is not None:
        unique = unique | {"(link)"}
    for i, r in enumerate(route.raises):
        example = None
        if login and i == 0:
            when, example = "other", _login_example(model, params)
        elif r.status == 404 and path and not any(e.when == "not_found" for e in out):
            when = "not_found"
        elif r.status in (403, 404) and path and checked:
            when = "not_owned"
        elif r.status in (400, 409) and unique:
            when = "duplicate"
        else:
            when = "other"
        error = ContractError(r.status, r.detail, when=when, example=example)
        if not any((e.status, e.detail, e.when) == (r.status, r.detail, when) for e in out):
            out.append(error)
    bounded = any(p.one_of or any(getattr(p, b) is not None for b in BOUNDS) for p in params)
    if bounded and not any(e.when == "invalid" for e in out):
        out.append(ContractError(api.validation_status, when="invalid"))
    return tuple(out)


def route_contract(
    model: RepoModel,
    route: Route,
    api: ApiProfile,
    public: Mapping[str, tuple[str, str]] | None = None,
) -> Contract:
    """The candidate contract of one route (see the module docstring). ``public``: the
    routes whose owner check admits a public row (:func:`public_scopes`)."""
    table = _table(model, route, api)
    kind = _kind(model, route)
    if kind == "create" and _acts_on_one(model, route, table):
        kind = "update"  # a POST on one row (an upload, an action), not a new row
    link = _users_link(model, api, table)
    if (
        kind == "action"
        and route.method == "POST"
        and link is not None
        and any(u.table == table and u.how == "assign" for u in route.owner_uses)
    ):
        kind = "create"  # a link between users the route adds: /me/following/{id}
    params = _params(model, route, api, table, kind)
    holes = [p for p in params if p.source == "path"]
    if kind == "read" and table and holes and all(p.ref not in (None, table) for p in holes):
        kind = "list"  # rows of the table under a parent row: /users/{id}/posts
    auth = _auth(route, api)
    return Contract(
        route.method,
        route.path,
        auth=auth,
        table=table if table in api.tables else None,
        kind=kind,
        # an admin-only route has no ownership scope (R2 n/a: no non-admin reaches it)
        owner_scope=None
        if _admin_only(auth, api)
        else _owner_scope(model, route, api, table, public),
        params=params,
        errors=_errors(model, route, api, table, params, kind),
        response=route.response_model if route.response_model in api.responses else None,
    )


def route_contracts(
    model: RepoModel, api: ApiProfile, public: Mapping[str, tuple[str, str]] | None = None
) -> list[Contract]:
    """A contract per route, in the model's order (an endpoint given twice: the first)."""
    out: dict[str, Contract] = {}
    for route in model.routes:
        c = route_contract(model, route, api, public)
        out.setdefault(c.endpoint, c)
    return list(out.values())


# ---- the candidate spec --------------------------------------------------------------------


def _accepted(spec: Spec, proposals: Iterable[ProposalLike]) -> Spec:
    invariants, machines, unchecked = (
        list(spec.invariants),
        list(spec.machines),
        list(spec.unchecked),
    )
    contracts = {c.endpoint: c for c in spec.contracts}
    for p in proposals:
        obj = p.spec_object
        match p.kind:
            case "invariant" if isinstance(obj, Invariant):
                invariants.append(obj)
            case "machine" if isinstance(obj, Machine):
                machines.append(obj)
            case "unchecked" if isinstance(obj, Unchecked):
                unchecked.append(obj)
            case "contract" | "rule" if isinstance(obj, Contract):
                contracts[obj.endpoint] = obj
            case _:
                raise ValueError(f"a proposal of kind {p.kind!r} with {type(obj).__name__}")
    return replace(spec, invariants=tuple(invariants), machines=tuple(machines),
                   unchecked=tuple(unchecked), contracts=tuple(contracts.values()))  # fmt: skip


def build_spec(
    model: RepoModel,
    profile: Profile,
    *,
    users: Mapping[str, tuple[str, str]] | None = None,
    programs: Mapping[str, str] | None = None,
    proposals: Iterable[ProposalLike] = (),
    public: Mapping[str, tuple[str, str]] | None = None,
) -> Spec:
    """The candidate spec: ``profile``, ``programs`` (router -> verified Lattice source),
    ``users`` (handle -> (login, password)), a contract per route of ``model`` (``public``:
    the routes admitting a public row, :func:`public_scopes`) and the accepted
    ``proposals``."""
    api = profile.api
    if api is None:
        raise ValueError("the profile has no API profile")
    spec = Spec(
        profile,
        programs=dict(programs or {}),
        users=dict(users or {}),
        contracts=tuple(route_contracts(model, api, public)),
    )
    return _accepted(spec, proposals)


def _users(result: ImportResult) -> dict[str, tuple[str, str]]:
    ctx = result.context
    suite = ctx.full_suite() if ctx is not None else None
    return dict(suite.credentials) if suite is not None else {}


def _public(result: ImportResult) -> dict[str, tuple[str, str]]:
    root = getattr(result.context, "root", None)
    if root is None:
        return {}
    return public_scopes(root, result.model.app_package, result.model)


def candidate_spec(
    result: ImportResult,
    *,
    proposals: Iterable[ProposalLike] = (),
    users: Mapping[str, tuple[str, str]] | None = None,
) -> Spec:
    """:func:`build_spec` of an import: its model, profile, verified programs, the
    seed's users (``users``, else the request suite's credentials) and the routes whose
    owner check admits a public row (:func:`public_scopes`, read from the package)."""
    return build_spec(result.model, result.lattice_profile,
                      users=_users(result) if users is None else users,
                      programs=result.programs, proposals=proposals,
                      public=_public(result))  # fmt: skip


def probe_import(result: ImportResult, spec: Spec, *, layers: str = "R",
                 only: Sequence[str] | None = None) -> Report:  # fmt: skip
    """``lattice.spec.check`` of ``spec`` on the import's original app in ``layers``
    (R, and C to watch the spec's invariants on every probe): the import's seed
    (``full_suite``'s database, users and login), its runtime's transport and token
    scheme, its token minter for the inactive caller."""
    from lattice.importer.transport import scheme_header
    from lattice.spec.check import check

    ctx = result.context
    if ctx is None:
        raise ValueError("the import has no verification context: layer R cannot run")
    suite = ctx.full_suite()
    if suite is None:
        raise ValueError("the import has no request suite: no seed to probe")
    runtime = ctx.runtime

    @contextmanager
    def sessions(db: Path) -> Iterator[Any]:
        with runtime.serve(ctx.app_factory, db) as (_, transport):
            yield transport

    return check(
        spec,
        ctx.app_factory,
        suite.db_factory,
        layers=layers,
        login=suite.login,
        mint_token=ctx.inputs.auth.mint_token,
        sessions=sessions,
        scheme=scheme_header(runtime.auth.scheme),
        rule_items=only,
    )


def import_rules(
    result: ImportResult,
    *,
    spec: Spec | None = None,
    proposals: Iterable[ProposalLike] = (),
    only: Sequence[str] | None = None,
) -> tuple[Spec, RuleResult]:
    """Layer R of the candidate spec (``spec``, default :func:`candidate_spec` with the
    accepted ``proposals``) on the original app (:func:`probe_import`)."""
    if spec is None:
        spec = candidate_spec(result, proposals=proposals)
    report = probe_import(result, spec, only=only)
    assert report.rules is not None
    return spec, report.rules


# ---- spec objects as JSON (proposals.json) -------------------------------------------------


SPEC_OBJECTS: dict[str, type] = {
    "Invariant": Invariant,
    "Machine": Machine,
    "Unchecked": Unchecked,
    "Contract": Contract,
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Scalar):
        return value.value
    if isinstance(value, ContractParam | ContractError):
        return {f.name: _jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple | list):
        return [_jsonable(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def spec_object_to(obj: Any) -> dict[str, Any]:
    """A spec object (Invariant, Machine, Unchecked, Contract) as JSON data."""
    name = type(obj).__name__
    if name not in SPEC_OBJECTS:
        raise TypeError(f"not a spec object: {obj!r}")
    return {"type": name, **{f.name: _jsonable(getattr(obj, f.name)) for f in fields(obj)}}


def spec_object_from(data: Mapping[str, Any]) -> Any:
    """The spec object :func:`spec_object_to` wrote."""
    data = dict(data)
    cls = SPEC_OBJECTS[data.pop("type")]
    if cls is Contract:
        data["params"] = [
            ContractParam(**{**p, "scalar": Scalar(p["scalar"])}) for p in data.get("params", ())
        ]
        data["errors"] = [ContractError(**e) for e in data.get("errors", ())]
    if cls is Machine:
        data["edges"] = [tuple(e) for e in data.get("edges", ())]
    return cls(**data)


def _sentence(obj: Any) -> str:
    match obj:
        case Invariant(sentence=s):
            return s
        case Machine(table=t, column=c, edges=edges):
            return f"{t}.{c} moves only along: " + ", ".join(f"{a} -> {b}" for a, b in edges)
        case Unchecked(text=text):
            return text
        case Contract():
            return f"the rule layer of {obj.endpoint}"
    return repr(obj)


def proposal_record(p: Any, *, status: str = "pending") -> dict[str, Any]:
    """A proposal as a ``proposals.json`` record: a stable ``id`` (the proposal's own,
    else one from its kind and object), kind, sentence, the spec object, its expression
    (invariants), evidence, support, source and status."""
    obj = p.spec_object
    data = spec_object_to(obj)
    pid = getattr(p, "id", None)
    if not pid:
        digest = hashlib.sha256(json.dumps([p.kind, data], sort_keys=True).encode()).hexdigest()
        pid = f"P-{p.kind}-{digest[:8]}"
    return {
        "id": pid,
        "kind": p.kind,
        "sentence": getattr(p, "sentence", None) or _sentence(obj),
        "spec_object": data,
        "expr": getattr(obj, "expr", None),
        "evidence": _jsonable(getattr(p, "evidence", ())),
        "support": getattr(p, "support", None),
        "source": getattr(p, "source", None),
        "status": status,
    }


# ---- the spec module -----------------------------------------------------------------------


def _lit(value: Any) -> str:
    """A Python literal for a value of a spec object (deterministic)."""
    if isinstance(value, Scalar):
        return f"Scalar.{value.name}"
    if isinstance(value, tuple):
        inner = ", ".join(_lit(v) for v in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    if isinstance(value, list):
        return "[" + ", ".join(_lit(v) for v in value) + "]"
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{_lit(k)}: {_lit(v)}" for k, v in value.items()) + "}"
    return repr(value)


def _call(obj: Any, positional: Sequence[str], indent: str) -> str:
    """``Type(pos..., field=value...)``: non-default fields only, one per line."""
    args: list[str] = []
    for f in fields(obj):
        if not f.init:
            continue
        value = getattr(obj, f.name)
        if f.name in positional:
            args.append(_lit(value))
            continue
        default = f.default
        if default is MISSING and f.default_factory is not MISSING:
            default = f.default_factory()
        if default is not MISSING and value == default:
            continue
        args.append(f"{f.name}={_lit(value)}")
    head = f"{type(obj).__name__}("
    one_line = head + ", ".join(args) + ")"
    if len(indent) + len(one_line) <= 96:
        return one_line
    inner = indent + "    "
    return head + "\n" + "".join(f"{inner}{a},\n" for a in args) + indent + ")"


def _contract_source(c: Contract, indent: str) -> str:
    inner = indent + "    "
    lines = [f"{indent}Contract(", f"{inner}{_lit(c.method)},", f"{inner}{_lit(c.path)},"]
    lines.append(f"{inner}auth={_lit(c.auth)},")
    for name in ("table", "kind", "owner_scope"):
        value = getattr(c, name)
        if value is not None:
            lines.append(f"{inner}{name}={_lit(value)},")
    for name, items, positional in (
        ("params", c.params, ("name", "source", "scalar")),
        ("errors", c.errors, ("status",)),
    ):
        if not items:
            continue
        lines.append(f"{inner}{name}=(")
        lines += [f"{inner}    {_call(x, positional, inner + '    ')}," for x in items]
        lines.append(f"{inner}),")
    if c.response is not None:
        lines.append(f"{inner}response={_lit(c.response)},")
    lines.append(f"{indent}),")
    return "\n".join(lines)


def _program_literal(source: str) -> str:
    if '"""' not in source and "\\" not in source and not source.endswith('"'):
        return '"""' + source + '"""'
    return repr(source)


def _contract_items(c: Contract, status: Mapping[str, str]) -> list[str]:
    """The status keys of ``c``'s route items (R1, R2, R3)."""
    out = []
    for item in status:
        kind, _, rest = item.partition(" ")
        if kind in ("R1", "R2", "R3") and (rest == c.endpoint or rest.startswith(c.endpoint + " ")):
            out.append(item)
    return out


def object_key(obj: Any, index: int = 0) -> str:
    """The status key of a spec object: ``invariant <name>``, ``machine <Table>.<col>``,
    ``unchecked <index>`` (its place in ``Spec.unchecked``)."""
    match obj:
        case Invariant(name=name):
            return f"invariant {name}"
        case Machine(table=table, column=column):
            return f"machine {table}.{column}"
        case Unchecked():
            return f"unchecked {index}"
    return f"{type(obj).__name__.lower()} {index}"


def spec_source(
    spec: Spec,
    *,
    status: Mapping[str, str] | None = None,
    package: str = "",
    profile_file: str = "profile.py",
) -> str:
    """The spec module's text (see the module docstring); ``status``: item id -> status
    (``verified`` / ``inferred`` / ``proposed-accepted`` ...), written as ``STATUS`` and
    as comments."""
    status = dict(status or {})
    out = [
        f'"""Lattice spec of {package or "the imported app"}.',
        "",
        "Generated by lattice.importer.emit_spec (python -m lattice.importer review DIR emit):",
        "the profile is PROFILE_FILE beside this module; every contract is commented with",
        "the status of its rule items (verified / inferred / proposed-accepted), as STATUS",
        'holds them."""',
        "",
        "from __future__ import annotations",
        "",
        "import importlib.util",
        "from pathlib import Path",
        "",
        "from lattice.spec import (",
        "    Contract,",
        "    ContractError,",
        "    ContractParam,",
        "    Invariant,",
        "    Machine,",
        "    Spec,",
        "    Unchecked,",
        ")",
        "from lattice.typesys import Scalar",
        "",
        f"PACKAGE = {package!r}",
        f"PROFILE_FILE = {profile_file!r}",
        "",
        "",
        "def _profile_module():",
        "    path = Path(__file__).resolve().parent / PROFILE_FILE",
        '    name = "_lattice_spec_profile_" + str(abs(hash(str(path))))',
        "    found = importlib.util.spec_from_file_location(name, path)",
        "    module = importlib.util.module_from_spec(found)",
        "    found.loader.exec_module(module)",
        "    return module",
        "",
        "",
        "_PROFILE = _profile_module()",
        "PROFILE = _PROFILE.profile()",
        "NATIVES = _PROFILE.NATIVES",
        "",
    ]
    out.append("PROGRAMS: dict[str, str] = {")
    for name, source in spec.programs.items():
        out.append(f"    {name!r}: {_program_literal(source)},")
    out += ["}", ""]
    out.append("USERS: dict[str, tuple[str, str]] = {")
    out += [f"    {h!r}: {_lit(tuple(v))}," for h, v in spec.users.items()]
    out += ["}", ""]
    out.append("CONTRACTS: tuple[Contract, ...] = (")
    for c in spec.contracts:
        for item in _contract_items(c, status):
            out.append(f"    # {item}: {status[item]}")
        out.append(_contract_source(c, "    "))
    out += [")", ""]
    for name, items, positional in (
        ("MACHINES", spec.machines, ("table", "column")),
        ("INVARIANTS", spec.invariants, ("name", "sentence", "expr")),
        ("UNCHECKED", spec.unchecked, ("text",)),
    ):
        out.append(f"{name}: tuple = (")
        for i, x in enumerate(items):
            mark = status.get(object_key(x, i))
            if mark:
                out.append(f"    # {mark}")
            out.append(f"    {_call(x, positional, '    ')},")
        out += [")", ""]
    out.append("STATUS: dict[str, str] = {")
    out += [f"    {k!r}: {v!r}," for k, v in status.items()]
    out += ["}", ""]
    out += [
        "SPEC = Spec(",
        "    PROFILE,",
        "    programs=PROGRAMS,",
        "    machines=MACHINES,",
        "    invariants=INVARIANTS,",
        "    unchecked=UNCHECKED,",
        "    users=USERS,",
        "    contracts=CONTRACTS,",
        ")",
    ]
    return "\n".join(out) + "\n"


@dataclass(frozen=True)
class LoadedSpec:
    """A spec module, loaded: its ``SPEC``, ``STATUS``, ``PACKAGE`` and ``PROFILE_FILE``."""

    spec: Spec
    status: dict[str, str]
    package: str
    profile_file: str
    module: Any


def load_spec(path: Path | str) -> LoadedSpec:
    """Load a spec module (any module defining ``SPEC``; the emitted ones also define
    ``STATUS``, ``PACKAGE`` and ``PROFILE_FILE``)."""
    path = Path(path).resolve()
    name = f"_lattice_spec_{uuid.uuid4().hex}"
    found = importlib.util.spec_from_file_location(name, path)
    if found is None or found.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(found)
    sys.modules[name] = module
    try:
        found.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return LoadedSpec(
        module.SPEC,
        dict(getattr(module, "STATUS", {})),
        getattr(module, "PACKAGE", ""),
        getattr(module, "PROFILE_FILE", "profile.py"),
        module,
    )


def file_digest(path: Path | str) -> str | None:
    """The sha256 of a file's bytes; None when it does not exist."""
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def write_spec(
    spec: Spec,
    out: Path | str,
    *,
    status: Mapping[str, str] | None = None,
    package: str = "",
    profile: Path | str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> Path:
    """Write ``spec`` as the module ``out`` and ``spec_meta.json`` beside it. ``profile``:
    the profile source to copy beside it as ``profile.py`` (default: one must be there
    already). The spec must validate. ``spec_meta.json``: ``schema``, ``package``,
    ``spec`` (the module's sha256), ``items`` (``meta["items"]``, else id ->
    ``{"status": ...}`` from ``status``) and every other key of ``meta``."""
    problems = validate(spec)
    if problems:
        raise ValueError("the spec is not valid:\n  " + "\n  ".join(p.render() for p in problems))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    target = out.parent / "profile.py"
    if profile is not None and Path(profile).resolve() != target.resolve():
        shutil.copyfile(profile, target)
    if not target.exists():
        raise FileNotFoundError(f"no profile.py beside {out}")
    status = dict(status or {})
    text = spec_source(spec, status=status, package=package, profile_file="profile.py")
    with tempfile.NamedTemporaryFile("w", dir=out.parent, delete=False, suffix=".tmp") as f:
        f.write(text)
    Path(f.name).replace(out)
    meta = dict(meta or {})
    if "items" in meta:  # the caller's per-item records are authoritative
        items = {k: dict(v) for k, v in dict(meta.pop("items")).items()}
    else:
        items = {item: {"status": s} for item, s in status.items()}
    data = {"schema": META_SCHEMA, "package": package, "spec": file_digest(out), **meta,
            "items": items}  # fmt: skip
    (out.parent / "spec_meta.json").write_text(json.dumps(data, indent=2, default=str) + "\n")
    return out


# ---- comparing contracts -------------------------------------------------------------------


def _auth_key(auth: str | None) -> Any:
    if auth is None:
        return None
    m = re.fullmatch(r"Roles\((.*)\)", auth)
    return frozenset(r.strip() for r in m.group(1).split(",")) if m else auth


def _param_key(p: ContractParam) -> tuple:
    return tuple(getattr(p, f.name) for f in fields(p) if f.name != "example")


def contract_diff(emitted: Sequence[Contract], hand: Sequence[Contract]) -> list[dict[str, Any]]:
    """Field-by-field differences of contracts matched by endpoint: rows of endpoint,
    field (``auth``, ``params``, ``errors[i].detail``...), emitted and hand values. Auth
    compares role sets; params compare as sets (examples aside); errors pairwise."""
    rows: list[dict[str, Any]] = []
    mine = {c.endpoint: c for c in emitted}
    for h in hand:
        e = mine.get(h.endpoint)
        if e is None:
            rows.append({"endpoint": h.endpoint, "field": "contract", "emitted": None, "hand": h})
            continue

        def row(name: str, a: Any, b: Any, ep: str = h.endpoint) -> None:
            rows.append({"endpoint": ep, "field": name, "emitted": a, "hand": b})

        if _auth_key(e.auth) != _auth_key(h.auth):
            row("auth", e.auth, h.auth)
        for name in ("table", "kind", "owner_scope", "response"):
            if getattr(e, name) != getattr(h, name):
                row(name, getattr(e, name), getattr(h, name))
        pe, ph = {_param_key(p) for p in e.params}, {_param_key(p) for p in h.params}
        if pe != ph:
            row("params", sorted(pe - ph, key=str), sorted(ph - pe, key=str))
        if len(e.errors) != len(h.errors):
            row("errors", len(e.errors), len(h.errors))
        for i, (a, b) in enumerate(zip(e.errors, h.errors, strict=False)):
            for name in ("status", "when", "detail", "example"):
                if getattr(a, name) != getattr(b, name):
                    row(f"errors[{i}].{name}", getattr(a, name), getattr(b, name))
    return rows
