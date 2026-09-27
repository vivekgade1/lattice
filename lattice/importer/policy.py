"""Policy deviations a lift adopts, as data (spec 2026-09-27 importer §3.2 "Policy
conflicts").

When the original violates a Lattice guarantee, the lift keeps the guarantee and says so:
each policy below names the requests where the import answers differently and exactly
what it answers instead. :func:`deviations_for` turns a route's policies into the
:class:`~lattice.importer.verify.Deviation` per case that ``verify`` asserts -- the
import's status and body exactly, and that the case really differs from the original.
A policy never widens what counts as equal: a case it names that the import answers
like the original is a mismatch (``verify``: "declared but the import agrees").

* :class:`PageBound` -- a page window parameter the original leaves unbounded gets
  Lattice's bounds (``limit: Int(ge=0, le=100)``, ``skip: Int(ge=0)``): a value outside
  them is a 422 where the original passes it to SQL.
* :class:`NotNull` -- an optional body field (``str | None``) writing a NOT NULL column:
  an explicit null is a 422 where the original writes it and fails (500).
* :class:`TokenPolicy` -- Lattice's auth answers every token that names no caller (an
  invalid token, or a valid one for a user that does not exist) with 401 and the
  profile's text; the original's own answer (read from its current-user dependency by
  :func:`auth_policies`) is kept in the policy.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lattice.importer.verify import Case, Deviation

if TYPE_CHECKING:
    from lattice.importer.extract import RepoModel, _Index
    from lattice.profile import ApiProfile


@dataclass(frozen=True)
class PageBound:
    """Lattice's bounds on a query parameter the original leaves unbounded."""

    param: str
    ge: int | None = None
    le: int | None = None


@dataclass(frozen=True)
class NotNull:
    """An optional body field writing a NOT NULL column: null is refused."""

    field: str


@dataclass(frozen=True)
class TokenPolicy:
    """``case``: the suite's case kind (``bad_token`` / ``unknown_user_token``);
    ``original``: the original's (status, detail) for it."""

    case: str
    id: str
    reason: str
    original: tuple[int, str]


Policy = PageBound | NotNull | TokenPolicy

PAGE_REASON = (
    "Lattice requires a bounded, non-negative page window ({bounds}); the original "
    "passes any integer to SQL (SQLite reads LIMIT -1 as no limit and a negative OFFSET "
    "as 0), so it answers 200 with every row."
)
NULL_REASON = (
    "An explicit null for a field the table stores as NOT NULL (the request model makes "
    "it Optional): the original writes it and fails with 500 (IntegrityError); Lattice "
    "refuses it with 422 and writes nothing."
)


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _validation(api: ApiProfile, entries: list[dict[str, Any]]) -> Any:
    from lattice.api.runtime import error_body

    return error_body(api.error_body, 422, "validation", entries)


def deviations_for(
    cases: Sequence[Case], policies: Sequence[Policy], api: ApiProfile
) -> dict[str, Deviation]:
    """The declared deviation of every case one of ``policies`` covers."""
    from lattice.api.runtime import error_body

    pages = [p for p in policies if isinstance(p, PageBound)]
    nulls = [p.field for p in policies if isinstance(p, NotNull)]
    tokens = {p.case: p for p in policies if isinstance(p, TokenPolicy)}
    bounds = ", ".join(
        f"{p.param}: Int({', '.join(x for x in (_b('ge', p.ge), _b('le', p.le)) if x)})"
        for p in pages
    )
    out: dict[str, Deviation] = {}
    for c in cases:
        what = c.name.removeprefix(f"{c.route}.") if c.route else c.name
        if what in tokens:
            t = tokens[what]
            out[c.name] = Deviation(
                t.id,
                "policy",
                t.reason,
                401,
                error_body(api.error_body, 401, "unauthorized", api.unauthorized_detail),
            )
            continue
        params = c.params or {}
        entries: list[dict[str, Any]] = []
        first: str | None = None
        for p in pages:
            v = params.get(p.param)
            if isinstance(v, bool) or not isinstance(v, int):
                continue
            if p.ge is not None and v < p.ge:
                entries.append({"loc": ["query", p.param], "msg": f"must be >= {_num(p.ge)}"})
                first = first or "non-negative-page-window"
            elif p.le is not None and v > p.le:
                entries.append({"loc": ["query", p.param], "msg": f"must be <= {_num(p.le)}"})
                first = first or "bounded-page-size"
        if entries and first is not None:
            out[c.name] = Deviation(
                first, "policy", PAGE_REASON.format(bounds=bounds), 422, _validation(api, entries)
            )
            continue
        body = c.json if isinstance(c.json, dict) else {}
        null = [
            {"loc": ["body", f], "msg": "must not be null"}
            for f in nulls
            if f in body and body[f] is None
        ]
        if null:
            out[c.name] = Deviation(
                "null-into-required-column", "policy", NULL_REASON, 422, _validation(api, null)
            )
    return out


def _b(name: str, v: int | None) -> str:
    return "" if v is None else f"{name}={v}"


# ---- auth ---------------------------------------------------------------------------------


def auth_policies(model: RepoModel, index: _Index, api: ApiProfile) -> tuple[TokenPolicy, ...]:
    """The token policies for routes behind the current-user dependency: how that
    dependency answers an undecodable token (the ``raise HTTPException`` in the handler
    of the ``try`` around the decode) and a token naming no user (the ``raise`` guarding
    the ``session.get`` of the caller), each where it differs from Lattice's 401. A
    dependency of another shape yields no policy (those cases then fail verification)."""
    dep = next((d for d in model.dependencies if d.kind == "current_user"), None)
    if dep is None:
        return ()
    found = index.node(f"{dep.module}:{dep.name}")
    if found is None or not isinstance(found[1], ast.FunctionDef | ast.AsyncFunctionDef):
        return ()
    fn = found[1]
    lattice = (401, api.unauthorized_detail)
    out: list[TokenPolicy] = []
    bad = _handler_raise(fn, dep.module, index)
    if bad is not None and bad != lattice:
        out.append(
            TokenPolicy(
                "bad_token",
                "invalid-token-401",
                f"The original answers an invalid bearer token with {bad[0]} {bad[1]!r}; "
                "Lattice's auth answers every missing or invalid credential with 401.",
                bad,
            )
        )
    unknown = _missing_user_raise(fn, dep.module, index)
    if unknown is not None and unknown != lattice:
        out.append(
            TokenPolicy(
                "unknown_user_token",
                "unknown-user-token",
                f"A valid token naming a user that does not exist: the original answers "
                f"{unknown[0]} {unknown[1]!r}; Lattice's token auth answers 401 like any "
                "token naming no caller.",
                unknown,
            )
        )
    return tuple(out)


def _raise_of(
    stmt: ast.stmt, module: str, index: _Index, fn: ast.AST | None = None
) -> tuple[int, str] | None:
    """(status, detail) of ``raise HTTPException(...)`` -- or of ``raise <name>`` where
    ``name`` is assigned an ``HTTPException(...)`` once in ``fn`` (FastAPI's
    ``credentials_exception`` idiom)."""
    from lattice.importer.extract import _single_assignment
    from lattice.importer.lifters.base import status_of

    if not isinstance(stmt, ast.Raise):
        return None
    exc = stmt.exc
    if isinstance(exc, ast.Name) and fn is not None:
        exc = _single_assignment(fn, exc.id)
    if not isinstance(exc, ast.Call):
        return None
    q = index.resolve(module, exc.func)
    if q is None or not q.endswith(":HTTPException"):
        return None
    kw: Mapping[str | None, ast.expr] = {k.arg: k.value for k in exc.keywords}
    args = exc.args
    status = status_of(kw.get("status_code", args[0] if args else None))
    detail = kw.get("detail", args[1] if len(args) > 1 else None)
    if status is None or not (isinstance(detail, ast.Constant) and isinstance(detail.value, str)):
        return None
    return status, detail.value


def _handler_raise(fn: ast.AST, module: str, index: _Index) -> tuple[int, str] | None:
    for node in ast.walk(fn):
        if isinstance(node, ast.Try) and len(node.handlers) == 1:
            body = node.handlers[0].body
            if len(body) == 1 and (r := _raise_of(body[0], module, index, fn)) is not None:
                return r
    return None


def _missing_user_raise(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, module: str, index: _Index
) -> tuple[int, str] | None:
    from lattice.importer.lifters.base import is_falsy_test

    body = fn.body
    for i, stmt in enumerate(body[:-1]):
        match stmt:
            case ast.Assign(
                targets=[ast.Name(id=var)],
                value=ast.Call(func=ast.Attribute(attr="get"), args=[_, _]),
            ):
                nxt = body[i + 1]
                if (
                    isinstance(nxt, ast.If)
                    and not nxt.orelse
                    and len(nxt.body) == 1
                    and is_falsy_test(nxt.test, var)
                ):
                    return _raise_of(nxt.body[0], module, index, fn)
    return None


__all__ = [
    "NotNull",
    "PageBound",
    "Policy",
    "TokenPolicy",
    "auth_policies",
    "deviations_for",
]
