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
* :class:`TokenPolicy` -- a token that names no caller (an invalid token, a claimless
  one, or a valid one for a user that does not exist) the import answers otherwise
  than the original. The profile carries the original's answers
  (``NativeToken(invalid=, unknown=)``, read by :func:`token_answers`), so this
  remains only for what the profile cannot say.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lattice.importer.transport import is_http_exception_class
from lattice.importer.verify import Case, Deviation

# a front end's error class: a resolved exception class ("module:Name") that answers an
# error (FastAPI's: HTTPException)
ErrorClass = Callable[[str], bool]

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
    # what the imported app answers instead (default: the 401 with the profile's text)
    lattice: tuple[int, str | None] | None = None


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


def validation_status(api: ApiProfile) -> int:
    """The status the import answers invalid input with (the profile's; 422 by default)."""
    return api.validation_status


def _validation(api: ApiProfile, entries: list[dict[str, Any]]) -> Any:
    from lattice.api.runtime import error_body

    return error_body(api.error_body, api.validation_status, "validation", entries)


def deviations_for(
    cases: Sequence[Case], policies: Sequence[Policy], api: ApiProfile
) -> dict[str, Deviation]:
    """The declared deviation of every case one of ``policies`` covers."""
    from lattice.api.runtime import STATUS_CODES, error_body

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
            status, detail = t.lattice or (401, api.unauthorized_detail)
            out[c.name] = Deviation(
                t.id,
                "policy",
                t.reason,
                status,
                error_body(api.error_body, status, STATUS_CODES[status], detail),
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
                first,
                "policy",
                PAGE_REASON.format(bounds=bounds),
                api.validation_status,
                _validation(api, entries),
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
                "null-into-required-column",
                "policy",
                NULL_REASON,
                api.validation_status,
                _validation(api, null),
            )
    return out


def _b(name: str, v: int | None) -> str:
    return "" if v is None else f"{name}={v}"


# ---- auth ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenAnswers:
    """The original's (status, detail) for a token naming no caller, read from its
    current-user dependency's raise sites: ``invalid`` an undecodable token (the decode
    helper's raise, or the guard on its result), ``claimless`` a valid token without the
    caller's claim, ``unknown`` a valid token naming a user that does not exist. None:
    not found."""

    invalid: tuple[int, str] | None = None
    claimless: tuple[int, str] | None = None
    unknown: tuple[int, str] | None = None


def token_answers(
    model: RepoModel, index: _Index, *, is_error: ErrorClass = is_http_exception_class
) -> TokenAnswers:
    """The original's answers to the tokens naming no caller (see :class:`TokenAnswers`):
    the ``raise HTTPException`` in the handler of the ``try`` around the decode -- in the
    dependency, or in the decode helper it passes the token to -- or the guard on the
    decoded payload; the guard on the claim; the guard on the caller's lookup.
    ``is_error``: the front end's error class (default FastAPI's ``HTTPException``).

    Without a current-user dependency (a framework's own auth, e.g. DRF's authentication
    classes), the answers the front end's ``AuthModel`` records."""
    dep = next((d for d in model.dependencies if d.kind == "current_user"), None)
    if dep is None:
        auth = model.auth
        if auth is None or auth.invalid_answer is None:
            return TokenAnswers()
        invalid = tuple(auth.invalid_answer)
        unknown = tuple(auth.unknown_answer) if auth.unknown_answer is not None else invalid
        return TokenAnswers(invalid, invalid, unknown)  # type: ignore[arg-type]
    found = index.node(f"{dep.module}:{dep.name}")
    if found is None or not isinstance(found[1], ast.FunctionDef | ast.AsyncFunctionDef):
        return TokenAnswers()
    fn = found[1]
    token = next(
        (a.arg for a in fn.args.args if a.arg == "token" or "token" in a.arg.lower()), None
    )
    bad = (
        _handler_raise(fn, dep.module, index, is_error=is_error)
        or _helper_raise(fn, dep.module, index, token, is_error=is_error)
        or _decoded_raise(fn, dep.module, index, token, is_error=is_error)
    )
    unknown = _missing_user_raise(fn, dep.module, index, is_error=is_error)
    # no guard on the claim itself: the missing claim reaches the caller's lookup
    claimless = _claim_raise(fn, dep.module, index, is_error=is_error) or unknown
    return TokenAnswers(bad, claimless, unknown)


def lattice_token_answers(api: ApiProfile) -> TokenAnswers:
    """What the imported app answers each such token: NativeToken's ``invalid`` for a
    token its native decodes to null (a bad one, and a claimless one: the generated
    decode returns null without the claim), its ``unknown`` for one naming no row --
    else the 401 with the profile's text."""
    from lattice.profile import NativeToken

    default = (401, api.unauthorized_detail)
    auth = api.auth
    invalid = (auth.invalid if isinstance(auth, NativeToken) else None) or default
    unknown = (auth.unknown if isinstance(auth, NativeToken) else None) or default
    return TokenAnswers(invalid, invalid, unknown)  # type: ignore[arg-type]


def auth_policies(
    model: RepoModel,
    index: _Index,
    api: ApiProfile,
    *,
    is_error: ErrorClass = is_http_exception_class,
) -> tuple[TokenPolicy, ...]:
    """The token policies for routes behind the current-user dependency: each token
    naming no caller (:func:`token_answers`) the imported app answers otherwise than the
    original (:func:`lattice_token_answers`). The profile carries the original's
    answers (``NativeToken(invalid=, unknown=)``), so a policy remains only for what it
    cannot say (a claimless token answered unlike a bad one). A dependency of another
    shape yields no policy (those cases then fail verification)."""
    original = token_answers(model, index, is_error=is_error)
    ours = lattice_token_answers(api)
    out: list[TokenPolicy] = []
    bad = original.invalid
    if bad is not None and bad != ours.invalid:
        out.append(
            TokenPolicy(
                "bad_token",
                "invalid-token-401",
                f"The original answers an invalid bearer token with {bad[0]} {bad[1]!r}; "
                "Lattice's auth answers every missing or invalid credential with 401.",
                bad,
                ours.invalid,
            )
        )
    claimless = original.claimless
    if claimless is not None and claimless != ours.claimless:
        out.append(
            TokenPolicy(
                "claimless_token",
                "claimless-token",
                f"A valid token without the caller's claim: the original answers "
                f"{claimless[0]} {claimless[1]!r}; Lattice's token auth answers it like "
                "an invalid token.",
                claimless,
                ours.claimless,
            )
        )
    unknown = original.unknown
    if unknown is not None and unknown != ours.unknown:
        out.append(
            TokenPolicy(
                "unknown_user_token",
                "unknown-user-token",
                f"A valid token naming a user that does not exist: the original answers "
                f"{unknown[0]} {unknown[1]!r}; Lattice's token auth answers 401 like any "
                "token naming no caller.",
                unknown,
                ours.unknown,
            )
        )
    return tuple(out)


def _helper_raise(
    fn: ast.AST,
    module: str,
    index: _Index,
    token: str | None,
    *,
    is_error: ErrorClass = is_http_exception_class,
) -> tuple[int, str] | None:
    """The decode helper's own raise: ``helper(token)`` a package function whose ``try``
    around the decode answers a bad token with ``raise HTTPException(...)``."""
    if token is None:
        return None
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if not any(isinstance(a, ast.Name) and a.id == token for a in node.args):
            continue
        qual = index.resolve(module, node.func)
        found = index.node(qual) if qual else None
        if found is None or not isinstance(found[1], ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        hmod, helper = found
        answer = _handler_raise(helper, hmod, index, is_error=is_error)
        if answer is not None:
            return answer
    return None


def _pairs(fn: ast.AST) -> list[tuple[ast.stmt, ast.stmt]]:
    """Every two consecutive statements of every statement list in ``fn`` (its body,
    a ``try`` body, an ``if`` branch...)."""
    out: list[tuple[ast.stmt, ast.stmt]] = []
    for node in ast.walk(fn):
        for name in ("body", "orelse", "finalbody"):
            block = getattr(node, name, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                out += list(zip(block, block[1:], strict=False))
    return out


def _assigned(stmt: ast.stmt) -> tuple[str, ast.expr] | None:
    """``name = value`` or ``name: T = value``."""
    match stmt:
        case ast.Assign(targets=[ast.Name(id=name)], value=value):
            return name, value
        case ast.AnnAssign(target=ast.Name(id=name), value=value) if value is not None:
            return name, value
    return None


def _guard(
    stmt: ast.stmt,
    var: str,
    module: str,
    index: _Index,
    fn: ast.AST,
    *,
    is_error: ErrorClass = is_http_exception_class,
) -> tuple[int, str] | None:
    """``if <var is None / not var> [or ...]: raise ...``: the raise's answer."""
    from lattice.importer.lifters.base import is_falsy_test

    if not (isinstance(stmt, ast.If) and not stmt.orelse and len(stmt.body) == 1):
        return None
    test = stmt.test
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        test = test.values[0]  # `if user is None or not user.is_active`
    if not is_falsy_test(test, var):
        return None
    return _raise_of(stmt.body[0], module, index, fn, is_error=is_error)


def _decoded_raise(
    fn: ast.AST,
    module: str,
    index: _Index,
    token: str | None,
    *,
    is_error: ErrorClass = is_http_exception_class,
) -> tuple[int, str] | None:
    """``payload = <decode helper>(token); if payload is None: raise ...``."""
    if token is None:
        return None
    for stmt, nxt in _pairs(fn):
        got = _assigned(stmt)
        if got is None or not isinstance(got[1], ast.Call):
            continue
        call = got[1]
        if any(isinstance(a, ast.Name) and a.id == token for a in call.args):
            return _guard(nxt, got[0], module, index, fn, is_error=is_error)
    return None


def _claim_raise(
    fn: ast.AST, module: str, index: _Index, *, is_error: ErrorClass = is_http_exception_class
) -> tuple[int, str] | None:
    """``sub = payload.get("<claim>")`` (or ``payload["<claim>"]``) followed by
    ``if sub is None / not sub: raise ...``."""
    for stmt, nxt in _pairs(fn):
        got = _assigned(stmt)
        if got is None:
            continue
        match got[1]:
            case ast.Call(func=ast.Attribute(attr="get"), args=[ast.Constant(value=str()), *_]):
                pass
            case ast.Subscript(slice=ast.Constant(value=str())):
                pass
            case _:
                continue
        found = _guard(nxt, got[0], module, index, fn, is_error=is_error)
        if found is not None:
            return found
    return None


def _raise_of(
    stmt: ast.stmt,
    module: str,
    index: _Index,
    fn: ast.AST | None = None,
    *,
    is_error: ErrorClass = is_http_exception_class,
) -> tuple[int, str] | None:
    """(status, detail) of ``raise HTTPException(...)`` -- or of ``raise <name>`` where
    ``name`` is assigned an ``HTTPException(...)`` once in ``fn`` (FastAPI's
    ``credentials_exception`` idiom). ``is_error``: the front end's error class."""
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
    if q is None or not is_error(q):
        return None
    kw: Mapping[str | None, ast.expr] = {k.arg: k.value for k in exc.keywords}
    args = exc.args
    status = status_of(kw.get("status_code", args[0] if args else None))
    detail = kw.get("detail", args[1] if len(args) > 1 else None)
    if status is None or not (isinstance(detail, ast.Constant) and isinstance(detail.value, str)):
        return None
    return status, detail.value


def _handler_raise(
    fn: ast.AST, module: str, index: _Index, *, is_error: ErrorClass = is_http_exception_class
) -> tuple[int, str] | None:
    for node in ast.walk(fn):
        if isinstance(node, ast.Try) and len(node.handlers) == 1:
            body = node.handlers[0].body
            r = _raise_of(body[0], module, index, fn, is_error=is_error) if len(body) == 1 else None
            if r is not None:
                return r
    return None


def _missing_user_raise(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    module: str,
    index: _Index,
    *,
    is_error: ErrorClass = is_http_exception_class,
) -> tuple[int, str] | None:
    """The raise guarding the caller's lookup: ``user = session.get(M, id)`` -- or any
    call assigned to the name the dependency returns -- followed by ``if user is None /
    not user [or ...]: raise ...``."""
    returned = {
        n.value.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Name)
    }
    for stmt, nxt in _pairs(fn):
        got = _assigned(stmt)
        if got is None or not isinstance(got[1], ast.Call):
            continue
        var, call = got
        is_get = isinstance(call.func, ast.Attribute) and call.func.attr == "get"
        if not ((is_get and len(call.args) == 2) or var in returned):
            continue
        found = _guard(nxt, var, module, index, fn, is_error=is_error)
        if found is not None:
            return found
    return None


__all__ = [
    "NotNull",
    "PageBound",
    "Policy",
    "TokenAnswers",
    "TokenPolicy",
    "auth_policies",
    "lattice_token_answers",
    "token_answers",
    "deviations_for",
]
