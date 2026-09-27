"""create: ``row = T.model_validate(body, update={"<owner>": caller.<key>})``,
``session.add(row)``, ``session.commit()``, ``session.refresh(row)`` -> ``insert(T,
owner=user, f=f, ..., <defaulted column>=<default>)``.

Every body field must be a column of ``T`` with the same type. Every other column must
be the owner (set from the caller's key in ``update=``, and the profile's owner column),
a key with an automatic default (``uuid.uuid4``), or have a default Lattice can spell: a
literal, or a ``default_factory`` returning the current time (``fn.now()``). A body field
the request model makes Optional over a NOT NULL column is a :class:`NotNull` policy."""

from __future__ import annotations

import ast

from lattice.importer.extract import RepoModel
from lattice.importer.lifters._orm import column_default
from lattice.importer.lifters.base import (
    USER,
    LiftFragment,
    Param,
    RouteSource,
    Step,
    assigned,
    attr_of,
    body_fields,
    field_annotation,
    lit,
    model_validate,
)
from lattice.importer.policy import NotNull

IDIOM = "T.model_validate(body, update={owner: caller.id}) + add/commit/refresh -> insert(T, ...)"


def _update_owner(route: RouteSource, call: ast.Call) -> str | None | bool:
    """The column ``update={"<col>": caller.<key>}`` sets; None without ``update=``;
    False for any other keyword or mapping."""
    kws = {k.arg: k.value for k in call.keywords}
    if not kws:
        return None
    if set(kws) != {"update"} or not isinstance(kws["update"], ast.Dict):
        return False
    d = kws["update"]
    users = route.users_table
    key = route.key(users) if users is not None else None
    if len(d.keys) != 1 or key is None:
        return False
    k, v = d.keys[0], d.values[0]
    if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
        return False
    if attr_of(v, route.user) != key.name:
        return False
    return k.value


def match(route: RouteSource, model: RepoModel) -> LiftFragment | None:
    body = route.body
    for i, stmt in enumerate(body):
        a = assigned(stmt)
        mv = model_validate(a[1]) if a is not None else None
        if a is None or mv is None:
            continue
        var = a[0]
        cls, call = mv
        table = route.table(cls)
        if table is None or len(call.args) != 1 or not isinstance(call.args[0], ast.Name):
            return None
        source = call.args[0].id
        owner = _update_owner(route, call)
        if owner is False or source not in route.params_of("body"):
            return None
        if owner != route.owner(table):
            return None  # the owner column unset, or another column set from the caller
        j = i + 1
        if not (j < len(body) and route.session_stmt(body[j], "add", var)):
            return None
        j += 1
        if not (j < len(body) and route.session_stmt(body[j], "commit")):
            return None
        j += 1
        if j < len(body) and route.session_stmt(body[j], "refresh", var):
            j += 1
        fields = body_fields(route, source)
        if not fields:
            return None
        columns = {c.name: c for c in table.columns}
        params: list[Param] = []
        policies: list[NotNull] = []
        kwargs: list[tuple[str, str]] = [("owner", USER)] if owner else []
        for f in fields:
            c = columns.get(f.name)
            ann = field_annotation(f)
            if c is None or ann is None or c.type != f.type or c.primary_key or c.name == owner:
                return None
            if c.nullable and not f.nullable and not f.required:
                return None
            if f.nullable and not c.nullable:
                policies.append(NotNull(f.name))
            if f.required:
                default = None
            elif f.default in (None, "None"):
                default = "UNSET"
            else:
                try:
                    value = ast.literal_eval(f.default)
                except (ValueError, SyntaxError):
                    return None
                default = lit(value)
            params.append(Param(f.name, "body", ann, default))
            kwargs.append((f.name, f.name))
        given = {f.name for f in fields} | ({owner} if owner else set())
        for c in table.columns:
            if c.name in given:
                continue
            d = column_default(route, table, c.name)
            if d is None:
                if c.nullable:
                    continue  # stored NULL, as the original stores it
                return None
            kind, value = d
            if kind == "key":
                if not c.primary_key:
                    return None
            elif kind == "now":
                kwargs.append((c.name, "fn.now()"))
            elif value is not None:
                kwargs.append((c.name, lit(value)))
        return LiftFragment(
            lifter=__name__.rsplit(".", 1)[-1],
            idiom=IDIOM,
            stmts=tuple(range(i, j)),
            steps=(
                Step(
                    var,
                    "insert",
                    (table.name,),
                    tuple(kwargs),
                    binds=var,
                    writes=tuple(f.name for f in fields),
                ),
            ),
            params=tuple(params),
            consumes=(source,),
            policies=tuple(policies),
        )
    return None
