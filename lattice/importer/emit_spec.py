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
  table when that is all it touches); ``kind``: the login route ``action``; GET with a
  path id or one response model ``read``, else ``list``; POST with a body ``create``,
  else ``action``; PUT/PATCH ``update``; DELETE ``delete``;
* ``owner_scope`` (owned tables only): ``owner`` when the route compares or sets an
  owner column of the table or of a table it is owned through, or has an
  ``object_owner`` guard; else ``any`` (stated: the route reaches anyone's rows);
* ``params``: path and query parameters, and every body field (a list of models as
  ``name.0.field``), with type, required, bounds (``ge``/``gt``/``le``/``lt``,
  ``min_len``/``max_len``) and ``one_of``; a bounded field names the column it is
  stored in (the route's table, else the one table of the route with that column), a
  path id or ``<table>_id`` field the table whose key it is (``ref``);
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
        return "action"
    match route.method:
        case "GET":
            many = route.response_model is None or route.response_model.lower().startswith("list")
            return "read" if "path" in sources or not many else "list"
        case "POST":
            return "create" if sources & {"body", "form"} else "action"
        case "PUT" | "PATCH":
            return "update"
        case "DELETE":
            return "delete"
    return "action"


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


def _owner_scope(route: Route, api: ApiProfile, table: str | None) -> str | None:
    if not _owned(api, table):
        return None
    return "owner" if _owner_checked(route, api, table) else "any"


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
                ref = _ref_by_name(model, api, p.name)
                if ref is None and len(path_params) == 1 and table in api.tables:
                    ref = table if kind in ("read", "update", "delete") else None
            numeric = scalar in (Scalar.INT, Scalar.FLOAT)
            text = scalar is Scalar.STR
            out.append(
                ContractParam(
                    p.name,
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
) -> tuple[ContractError, ...]:
    out: list[ContractError] = []
    path = any(p.source == "path" for p in params)
    login = _is_login(model, route)
    checked = _owner_checked(route, api, table)
    unique = _unique_columns(model, table) & {p.name for p in params if p.source == "body"}
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


def route_contract(model: RepoModel, route: Route, api: ApiProfile) -> Contract:
    """The candidate contract of one route (see the module docstring)."""
    table = _table(model, route, api)
    kind = _kind(model, route)
    params = _params(model, route, api, table, kind)
    return Contract(
        route.method,
        route.path,
        auth=_auth(route, api),
        table=table if table in api.tables else None,
        kind=kind,
        owner_scope=_owner_scope(route, api, table),
        params=params,
        errors=_errors(model, route, api, table, params),
        response=route.response_model if route.response_model in api.responses else None,
    )


def route_contracts(model: RepoModel, api: ApiProfile) -> list[Contract]:
    """A contract per route, in the model's order (an endpoint given twice: the first)."""
    out: dict[str, Contract] = {}
    for route in model.routes:
        c = route_contract(model, route, api)
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
) -> Spec:
    """The candidate spec: ``profile``, ``programs`` (router -> verified Lattice source),
    ``users`` (handle -> (login, password)), a contract per route of ``model`` and the
    accepted ``proposals``."""
    api = profile.api
    if api is None:
        raise ValueError("the profile has no API profile")
    spec = Spec(
        profile,
        programs=dict(programs or {}),
        users=dict(users or {}),
        contracts=tuple(route_contracts(model, api)),
    )
    return _accepted(spec, proposals)


def _users(result: ImportResult) -> dict[str, tuple[str, str]]:
    ctx = result.context
    suite = ctx.full_suite() if ctx is not None else None
    return dict(suite.credentials) if suite is not None else {}


def candidate_spec(result: ImportResult, *, proposals: Iterable[ProposalLike] = ()) -> Spec:
    """:func:`build_spec` of an import: its model, profile, verified programs and the
    seed's users."""
    return build_spec(result.model, result.lattice_profile, users=_users(result),
                      programs=result.programs, proposals=proposals)  # fmt: skip


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
