"""Deterministic lifters (spec 2026-09-27 importer §3.2, 2a): one module per idiom family,
each ``match(route, model) -> LiftFragment | None`` (see :mod:`.base`), and the composer
:func:`lift_route` that turns a route's fragments into one Lattice endpoint.

| module           | idiom                                                                |
|------------------|----------------------------------------------------------------------|
| ``get_or_404``   | ``session.get(T, id)`` + ``if not x: raise 404`` -> ``one``          |
| ``owner_check``  | superuser-or-owner ``raise 403`` -> ``require(x, owner_is(user))``   |
| ``unique_check`` | lookup by a unique column + ``raise 409`` -> ``unique_or=Conflict``  |
| ``partial_update`` | ``sqlmodel_update(model_dump(exclude_unset=True))`` -> ``update``  |
| ``create``       | ``model_validate(body, update={owner})`` + commit -> ``insert``      |
| ``delete``       | ``session.delete`` + commit + ``Message`` -> ``delete`` + message    |
| ``page_list``    | superuser-all / owned list with offset/limit + count -> ``page``     |
| ``admin_list``   | unfiltered list with offset/limit + count -> ``page``                |
| ``me``           | ``return current_user`` -> the caller's row, private columns dropped |
| ``returns``      | ``return row`` -> the row                                            |
| ``login``        | OAuth2 form + authenticate helper + token -> natives                 |

The composer is the only place fragments meet: it accepts a route only when every
statement of its body is claimed by exactly one fragment, every request parameter of the
original (path, query, body, form) is read by one, one fragment holds the final
``return``, every ``{@var}`` hole names a variable an earlier fragment bound, and every
modifier finds its write. Otherwise the route has no lift, with the reason -- it goes to
the LLM (I5), it is never guessed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from lattice.catalog import API_AMBIENT, OP_SHAPES
from lattice.importer.extract import RepoModel
from lattice.importer.lifters import (
    admin_list,
    create,
    delete,
    get_or_404,
    login,
    me,
    owner_check,
    page_list,
    partial_update,
    returns,
    unique_check,
)
from lattice.importer.lifters.base import (
    USER,
    LiftFragment,
    Modifier,
    Param,
    RouteSource,
    Step,
)
from lattice.importer.policy import Policy

# The registry, in the order fragments are asked for (the order does not decide
# anything: overlapping claims reject the route).
LIFTERS = (
    get_or_404,
    owner_check,
    unique_check,
    partial_update,
    create,
    delete,
    page_list,
    admin_list,
    me,
    returns,
    login,
)
AUTH_ROLES = frozenset({"Public", "Customer", "Admin", "Authenticated"})


def lattice_auth(auth: str) -> bool:
    """An auth= the lifted program can say: a role kind, or ``Roles(...)`` (an exact role
    set read from a role checker, extract.RoleScheme)."""
    return auth in AUTH_ROLES or (auth.startswith("Roles(") and auth.endswith(")"))


SUCCESS_BY_STATUS = {None: "One", 200: "One", 201: "Created"}
LINE = 99
_HOLE = re.compile(r"\{([@#])(\w+)\}")


@dataclass(frozen=True)
class RouteLift:
    """A route's lift: ``source`` is the Lattice endpoint (decorator, signature, body),
    or None with ``reason``. ``fragments``: every fragment the lifters returned."""

    route_name: str
    fragments: tuple[LiftFragment, ...]
    source: str | None = None
    reason: str = ""
    params: tuple[Param, ...] = ()
    policies: tuple[Policy, ...] = ()
    notes: tuple[str, ...] = ()
    lifters: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.source is not None


class _Reject(Exception):
    pass


def lift_route(route: RouteSource, model: RepoModel) -> RouteLift:
    """Ask every lifter, then compose (see the module docstring)."""
    fragments = tuple(f for m in LIFTERS if (f := m.match(route, model)) is not None)
    try:
        return compose(route, fragments)
    except _Reject as err:
        return RouteLift(route.route.name, fragments, reason=str(err))


def _line(route: RouteSource, i: int) -> int:
    return route.body[i].lineno


def compose(route: RouteSource, fragments: Sequence[LiftFragment]) -> RouteLift:
    r = route.route
    if not lattice_auth(r.auth):
        raise _Reject(f"auth is {r.auth}: {r.auth_reason}")
    if r.status_code not in SUCCESS_BY_STATUS:
        raise _Reject(f"status_code={r.status_code} has no Lattice success kind here")
    claims: dict[int, LiftFragment] = {}
    for f in fragments:
        for i in f.stmts:
            if i in claims:
                raise _Reject(
                    f"line {_line(route, i)} is claimed by both {claims[i].lifter} and {f.lifter}"
                )
            claims[i] = f
    missing = [i for i in range(len(route.body)) if i not in claims]
    if missing or not route.body:
        lines = ", ".join(str(_line(route, i)) for i in missing) or "(empty body)"
        raise _Reject(f"no lifter matches the statements at line {lines}")
    # the original's request parameters must all be read, its dependencies all known
    consumed = {c for f in fragments for c in f.consumes}
    for p in r.params:
        if p.source in ("path", "query", "body", "form") and p.name not in consumed:
            raise _Reject(f"parameter {p.name!r} is not read by any lifted statement")
        if p.source == "dependency" and p.name not in (route.session, route.user):
            raise _Reject(f"dependency parameter {p.name!r} is not the session or the caller")
        if p.source == "framework":
            raise _Reject(f"parameter {p.name!r} is the framework's {p.type}: not lifted")
    ordered = sorted(fragments, key=lambda f: min(f.stmts))
    ret = [f for f in ordered if f.returns is not None]
    if len(ret) != 1 or len(route.body) - 1 not in ret[0].stmts:
        raise _Reject("the lifted statements do not end in exactly one return")

    reserved = {
        USER,
        *API_AMBIENT,
        *OP_SHAPES,
        *route.api.tables,
        *(p.name for f in fragments for p in f.params),
    }
    env: dict[str, str] = {route.user: USER} if route.user else {}
    used: set[str] = set()
    pending: list[Modifier] = []
    lines: list[str] = []
    returned: str | None = None
    errors: list[str] = []
    for f in ordered:
        local: dict[str, str] = {}
        for step in f.steps:
            name = _fresh(step.name, reserved | used)
            used.add(name)
            args = [_fill(a, env, local) for a in step.args]
            kwargs = [(k, _fill(v, env, local)) for k, v in step.kwargs]
            for m in list(pending):
                if _modifies(step, m):
                    kwargs.append((m.keyword, m.value))
                    pending.remove(m)
            lines += _call(name, step.op, args, kwargs)
            local[step.name] = name
            if step.binds is not None:
                env[step.binds] = name
        pending += f.modifiers
        for e in f.errors:
            if e not in errors:
                errors.append(e)
        if f.returns is not None:
            returned = _fill(f.returns, env, local)
    if pending:
        raise _Reject(f"no write for {', '.join(f'{m.keyword} on {m.column}' for m in pending)}")
    lines.append(f"    return {returned}")

    params = _params(route, fragments)
    success = next((f.success for f in ordered if f.success), SUCCESS_BY_STATUS[r.status_code])
    response = next((f.response for f in ordered if f.response), r.response_model)
    if response is None:
        raise _Reject("the route declares no response model")
    deco = [lit_(r.method), lit_(r.path), f"auth={r.auth}", f"response={lit_(response)}"]
    deco += [f"{k}={v}" for f in ordered for k, v in f.endpoint]
    head = f"@endpoint({', '.join(deco)})"
    sig = [
        f"{p.name}: {p.annotation}" + (f" = {p.default}" if p.default is not None else "")
        for p in params
    ]
    if r.auth != "Public":
        sig.insert(sum(p.source == "path" for p in params), f"{USER}: CurrentUser")
    rtype = " | ".join([success, *errors])
    one_line = f"def {r.name}({', '.join(sig)}) -> {rtype}:"
    if len(one_line) <= LINE:
        signature = [one_line]
    else:
        signature = [f"def {r.name}(", *(f"    {s}," for s in sig), f") -> {rtype}:"]
    source = "\n".join([head, *signature, *lines]) + "\n"
    return RouteLift(
        r.name,
        tuple(fragments),
        source,
        params=params,
        policies=tuple(p for f in ordered for p in f.policies),
        notes=tuple(n for f in ordered for n in f.notes),
        lifters=tuple(f.lifter for f in ordered),
    )


def lit_(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fresh(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    k = 2
    while f"{name}_{k}" in taken:
        k += 1
    return f"{name}_{k}"


def _fill(text: str, env: dict[str, str], local: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        kind, name = m.group(1), m.group(2)
        table = env if kind == "@" else local
        if name not in table:
            what = "variable" if kind == "@" else "step"
            raise _Reject(f"{what} {name!r} is used before a lifted statement binds it")
        return table[name]

    return _HOLE.sub(sub, text)


def _modifies(step: Step, m: Modifier) -> bool:
    if m.column not in step.writes:
        return False
    if step.op == "update":
        return bool(step.args) and step.args[0] == "{@" + m.subject + "}"
    return step.op == "insert" and step.binds == m.subject


def _call(name: str, op: str, args: list[str], kwargs: list[tuple[str, str]]) -> list[str]:
    parts = [*args, *(f"{k}={v}" for k, v in kwargs)]
    one = f"    {name} = {op}({', '.join(parts)})"
    if len(one) <= LINE:
        return [one]
    return [f"    {name} = {op}(", *(f"        {p}," for p in parts), "    )"]


def _params(route: RouteSource, fragments: Sequence[LiftFragment]) -> tuple[Param, ...]:
    """The endpoint's parameters: path (in path order), then query (in the original's
    order), then body fields (in lifted order) -- FastAPI's validation order."""
    found: dict[str, Param] = {}
    for f in sorted(fragments, key=lambda f: min(f.stmts)):
        for p in f.params:
            if p.name in found and found[p.name] != p:
                raise _Reject(f"parameter {p.name!r} is lifted two ways")
            found.setdefault(p.name, p)
    order = [p.name for p in route.route.params]
    path_order = re.findall(r"\{(\w+)\}", route.route.path)

    def key(p: Param) -> tuple[int, int]:
        if p.source == "path":
            return 0, path_order.index(p.name) if p.name in path_order else 99
        if p.source == "query":
            return 1, order.index(p.name) if p.name in order else 99
        return 2, 0

    params = sorted(found.values(), key=key)  # stable: body fields keep their order
    if any(p.source == "path" and p.name not in path_order for p in params):
        raise _Reject("a path parameter is not in the path")
    seen_default = False
    for p in params:
        if p.default is None and seen_default:
            raise _Reject(f"required parameter {p.name!r} follows a defaulted one")
        seen_default |= p.default is not None
    return tuple(params)


__all__ = ["LIFTERS", "LiftFragment", "RouteLift", "RouteSource", "compose", "lift_route"]
