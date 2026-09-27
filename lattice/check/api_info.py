"""API profile: parsing of `@endpoint` declarations (shared by the checker passes) and
the endpoint metadata a checked program hands to lowering (`CheckedProgram.endpoints`).

Every parser here is total: a malformed declaration yields a `Problem` (message, hint)
or is skipped, never an exception. P1 reports the problems; later passes and
`endpoint_info` only read declarations P1 accepted.
"""

from __future__ import annotations

import ast
import datetime as dt
import re
import uuid
from dataclasses import dataclass
from typing import Literal

from lattice.catalog import (
    API_ERRORS,
    API_METHODS,
    API_SUCCESS_KINDS,
    CURRENT_USER,
    PARAM_CONSTRAINTS,
    QUERY_METHODS,
    UNSET_NAME,
)
from lattice.check.astutil import literal_value
from lattice.diagnostics import Span, span_of
from lattice.profile import ApiProfile
from lattice.typesys import SCALAR_NAMES, Scalar, TableT, plain_columns


class _Missing:
    """Sentinel: a parameter without a default value."""

    _instance: _Missing | None = None

    def __new__(cls) -> _Missing:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()


class _Unset:
    """Sentinel: the default of an optional parameter (`name: Type = UNSET`). A request
    that does not send the parameter leaves the column it updates unchanged."""

    _instance: _Unset | None = None

    def __new__(cls) -> _Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()

ParamSource = Literal["path", "query", "body", "user"]
SuccessKind = Literal["One", "Page", "Created", "NoContent"]


@dataclass(frozen=True)
class ParamSpec:
    """One endpoint parameter. `scalar` is None only for `user: CurrentUser`
    (source "user"). Bounds are the literal constraints of the annotation; `one_of`
    is set for `OneOf(...)` (scalar Str). `default` is MISSING when there is none, and
    UNSET (with `optional=True`) for `name: Type = UNSET`: the parameter may be left
    out of the request, and is then "not sent" (update() keeps the column). Date
    defaults are `datetime.date`, DateTime defaults timezone-aware `datetime.datetime`
    (UTC), Uuid defaults canonical lowercase strings."""

    name: str
    source: ParamSource
    scalar: Scalar | None
    ge: float | None = None
    le: float | None = None
    min_len: int | None = None
    max_len: int | None = None
    one_of: tuple[str, ...] | None = None
    default: object = MISSING
    optional: bool = False  # default is UNSET

    @property
    def bounded(self) -> bool:
        """Safe to write to the database (L811): Int needs ge= (ids and counts rarely
        have a natural upper bound; requiring one forced arbitrary values), Float needs
        ge= and le=, Str a max_len= (or a OneOf set); Bool, Date, DateTime and Uuid are
        always bounded."""
        if self.scalar is Scalar.INT:
            return self.ge is not None
        if self.scalar is Scalar.FLOAT:
            return self.ge is not None and self.le is not None
        if self.scalar is Scalar.STR:
            return self.one_of is not None or self.max_len is not None
        return True


@dataclass(frozen=True)
class EndpointInfo:
    """What lowering needs from one checked `@endpoint` function."""

    name: str
    method: str
    path: str
    auth: str
    params: tuple[ParamSpec, ...]
    success: SuccessKind
    errors: tuple[str, ...]  # declared errors, in annotation order
    node: ast.FunctionDef
    span: Span
    # @endpoint(..., response="Name"): the ApiProfile.responses model the response
    # objects are serialized with (exactly its fields, in its order); None = every
    # returned column. For Page it describes each item.
    response: str | None = None
    # Page endpoints: the returned page was built with page(..., total=True), so the
    # envelope carries ApiProfile.page_shape["total"] (set by P6 from the returned
    # table's TableT.page_total).
    page_total: bool = False


Problem = tuple[str, str | None]  # (message, hint)


# ---- declaration ----------------------------------------------------------------


def endpoint_decorator(fn: ast.FunctionDef) -> ast.Call | None:
    """The `@endpoint(...)` call among a function's decorators, if any."""
    for deco in fn.decorator_list:
        if (
            isinstance(deco, ast.Call)
            and isinstance(deco.func, ast.Name)
            and deco.func.id == "endpoint"
        ):
            return deco
    return None


def is_endpoint(fn: ast.AST) -> bool:
    return isinstance(fn, ast.FunctionDef) and endpoint_decorator(fn) is not None


@dataclass(frozen=True)
class Decl:
    method: str
    path: str
    auth: ast.expr | None  # the auth= value, if given
    response: str | None = None  # the response= model name, if given


def parse_decl(deco: ast.Call) -> Decl | Problem:
    usage = '@endpoint("GET", "/orders/{order_id}", auth=Customer)'
    names = [kw.arg for kw in deco.keywords]
    if (
        len(deco.args) != 2
        or any(isinstance(a, ast.Starred) for a in deco.args)
        or any(n not in ("auth", "response") for n in names)
        or len(names) != len(set(names))
    ):
        return (
            'an endpoint is declared as @endpoint(METHOD, PATH, auth=Role[, response="Model"])',
            f"e.g. {usage}",
        )
    response_node = next((kw.value for kw in deco.keywords if kw.arg == "response"), None)
    response = literal_value(response_node) if response_node is not None else None
    if response_node is not None and not isinstance(response, str):
        return (
            "response= must name a response model as a string",
            'e.g. response="ItemPublic"',
        )
    method, path = (literal_value(a) for a in deco.args)
    if not isinstance(method, str) or not isinstance(path, str):
        return ("endpoint METHOD and PATH must be string literals", f"e.g. {usage}")
    if method not in API_METHODS:
        return (
            f"unknown HTTP method {method!r}",
            "use one of " + ", ".join(f'"{m}"' for m in API_METHODS),
        )
    auth = next((kw.value for kw in deco.keywords if kw.arg == "auth"), None)
    return Decl(method, path, auth, response if isinstance(response, str) else None)


_SEGMENT = re.compile(r"[A-Za-z0-9_.~-]+")
_PARAM_SEGMENT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def path_params(path: str) -> list[str] | None:
    """Names of the `{name}` segments of a path, or None if the path is malformed. One
    trailing "/" is allowed (e.g. "/items/"): the route matches exactly as declared,
    and the runtime redirects the slash-less request to it as FastAPI does."""
    if path == "/":
        return []
    if not path.startswith("/"):
        return None
    body = path[1:-1] if path.endswith("/") else path[1:]
    names: list[str] = []
    for seg in body.split("/"):
        m = _PARAM_SEGMENT.fullmatch(seg)
        if m is not None:
            names.append(m.group(1))
        elif not _SEGMENT.fullmatch(seg):
            return None
    return names


def route_shape(method: str, path: str) -> tuple[str, str]:
    """(method, path with every parameter name erased): two endpoints with the same
    shape would match the same requests."""
    return method, _PARAM_SEGMENT.sub("{}", path)


# ---- parameters -------------------------------------------------------------------


@dataclass(frozen=True)
class ParamType:
    scalar: Scalar | None  # None: CurrentUser
    ge: float | None = None
    le: float | None = None
    min_len: int | None = None
    max_len: int | None = None
    one_of: tuple[str, ...] | None = None


_TYPE_HINT = (
    'use Int(ge=, le=), Float(ge=, le=), Str(min_len=, max_len=), OneOf("a", "b"), Bool, '
    "Date, DateTime, Uuid (or a bare Int/Float/Str), or user: CurrentUser"
)


def _number(node: ast.expr, *, integer: bool) -> int | float | None:
    v = literal_value(node)
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    if integer and not isinstance(v, int):
        return None
    return v


def param_type(name: str, ann: ast.expr | None) -> ParamType | Problem:
    """The type of one endpoint parameter annotation."""
    need = (f"parameter '{name}' needs an API parameter type", _TYPE_HINT)
    match ann:
        case ast.Name(id=type_name) if type_name in SCALAR_NAMES:
            return ParamType(SCALAR_NAMES[type_name])
        case ast.Name(id=type_name) if type_name == CURRENT_USER:
            return ParamType(None)
        case ast.Call(func=ast.Name(id="OneOf"), args=args, keywords=[]) if args:
            values = [literal_value(a) for a in args]
            strs = [v for v in values if isinstance(v, str)]
            if len(strs) != len(values) or isinstance(args[0], ast.Starred):
                return (
                    f"OneOf(...) for '{name}' takes string literals",
                    'e.g. OneOf("pending", "paid")',
                )
            if len(set(strs)) != len(strs):
                return (f"OneOf(...) for '{name}' lists a value twice", "list each value once")
            return ParamType(Scalar.STR, one_of=tuple(strs))
        case ast.Call(func=ast.Name(id=type_name), args=[], keywords=kws) if (
            type_name in PARAM_CONSTRAINTS
        ):
            return _constrained(name, type_name, kws)
        case _:
            return need


def _constrained(name: str, type_name: str, kws: list[ast.keyword]) -> ParamType | Problem:
    allowed = PARAM_CONSTRAINTS[type_name]
    values: dict[str, int | float] = {}
    for kw in kws:
        if kw.arg not in allowed:
            return (
                f"{type_name}(...) has no constraint '{kw.arg}'",
                f"allowed: {', '.join(sorted(allowed))}",
            )
        integer = type_name != "Float"
        v = _number(kw.value, integer=integer)
        kind = "an integer" if integer else "a number"
        if v is None:
            return (f"constraint {kw.arg}= of '{name}' must be {kind} literal", f"e.g. {kw.arg}=1")
        if type_name == "Str" and v < 0:
            return (f"constraint {kw.arg}= of '{name}' cannot be negative", f"e.g. {kw.arg}=1")
        values[kw.arg] = v
    lo, hi = ("min_len", "max_len") if type_name == "Str" else ("ge", "le")
    if lo in values and hi in values and values[lo] > values[hi]:
        return (f"'{name}' has {lo}= greater than {hi}=", "swap or fix the bounds")
    if type_name == "Str":
        return ParamType(
            Scalar.STR,
            min_len=int(values["min_len"]) if "min_len" in values else None,
            max_len=int(values["max_len"]) if "max_len" in values else None,
        )
    return ParamType(SCALAR_NAMES[type_name], ge=values.get("ge"), le=values.get("le"))


def default_problem(name: str, pt: ParamType, node: ast.expr) -> Problem | None:
    """Whether a parameter default is a literal of the parameter's type within its
    constraints, or UNSET (an optional parameter of any scalar type)."""
    if is_unset(node):
        if pt.scalar is None:
            return (f"'{name}: CurrentUser' cannot have a default", "remove the default")
        return None
    v = literal_value(node)
    bad = (f"default of '{name}' must be a literal of its type", None)
    match pt.scalar:
        case None:
            return (f"'{name}: CurrentUser' cannot have a default", "remove the default")
        case Scalar.INT if isinstance(v, int) and not isinstance(v, bool):
            pass
        case Scalar.FLOAT if isinstance(v, int | float) and not isinstance(v, bool):
            pass
        case Scalar.STR if isinstance(v, str):
            if pt.one_of is not None and v not in pt.one_of:
                return (f"default of '{name}' is not one of its OneOf values", None)
            if (pt.min_len is not None and len(v) < pt.min_len) or (
                pt.max_len is not None and len(v) > pt.max_len
            ):
                return (f"default of '{name}' violates its length bounds", None)
        case Scalar.BOOL if isinstance(v, bool):
            pass
        case Scalar.DATE if isinstance(v, str):
            try:
                dt.date.fromisoformat(v)
            except ValueError:
                return (f"default of '{name}' must be an ISO date string", 'e.g. "2024-01-01"')
        case Scalar.DATETIME if isinstance(v, str):
            if _datetime_default(v) is None:
                return (
                    f"default of '{name}' must be an ISO date-time string with a timezone",
                    'e.g. "2024-01-01T00:00:00Z"',
                )
        case Scalar.UUID if isinstance(v, str):
            if _uuid_default(v) is None:
                return (
                    f"default of '{name}' must be a UUID string",
                    'e.g. "5b3e9c1c-0000-4000-8000-000000000000"',
                )
        case _:
            return bad
    if isinstance(v, int | float) and not isinstance(v, bool):
        if (pt.ge is not None and v < pt.ge) or (pt.le is not None and v > pt.le):
            return (f"default of '{name}' is outside its bounds", None)
    return None


def is_unset(node: ast.AST | None) -> bool:
    """The `UNSET` marker (only valid as an endpoint parameter default)."""
    return isinstance(node, ast.Name) and node.id == UNSET_NAME


_UUID_TEXT = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _uuid_default(text: str) -> str | None:
    """The canonical (lowercase, hyphenated) form of a UUID literal, or None."""
    if not _UUID_TEXT.fullmatch(text):
        return None
    return str(uuid.UUID(text))


def _datetime_default(text: str) -> dt.datetime | None:
    """A timezone-aware ISO-8601 date-time literal, converted to UTC, or None."""
    try:
        value = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value.astimezone(dt.UTC)


def _default_value(scalar: Scalar | None, node: ast.expr) -> object:
    """The Python value of a default P1 accepted."""
    if is_unset(node):
        return UNSET
    v = literal_value(node)
    if isinstance(v, str):
        match scalar:
            case Scalar.DATE:
                return dt.date.fromisoformat(v)
            case Scalar.DATETIME:
                return _datetime_default(v)
            case Scalar.UUID:
                return _uuid_default(v)
            case _:
                pass
    return v


def _defaults(fn: ast.FunctionDef) -> dict[str, ast.expr]:
    args = fn.args.args
    return {
        a.arg: d
        for a, d in zip(args[len(args) - len(fn.args.defaults) :], fn.args.defaults, strict=True)
    }


def endpoint_params(fn: ast.FunctionDef, method: str, path: str) -> tuple[ParamSpec, ...]:
    """The parameters of an endpoint P1 accepted (malformed ones are skipped)."""
    in_path = set(path_params(path) or [])
    defaults = _defaults(fn)
    specs: list[ParamSpec] = []
    for a in fn.args.args:
        pt = param_type(a.arg, a.annotation)
        if not isinstance(pt, ParamType):
            continue
        source: ParamSource
        if pt.scalar is None:
            source = "user"
        elif a.arg in in_path:
            source = "path"
        else:
            source = "query" if method in QUERY_METHODS else "body"
        default: object = MISSING
        if a.arg in defaults:
            default = _default_value(pt.scalar, defaults[a.arg])
        specs.append(
            ParamSpec(
                a.arg,
                source,
                pt.scalar,
                pt.ge,
                pt.le,
                pt.min_len,
                pt.max_len,
                pt.one_of,
                default,
                optional=default is UNSET,
            )
        )
    return tuple(specs)


# ---- return annotation ---------------------------------------------------------------


def _flatten_union(node: ast.expr) -> list[ast.expr]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _flatten_union(node.left) + _flatten_union(node.right)
    return [node]


_RETURN_HINT = "e.g. -> One | NotFound | Conflict"


def parse_returns(node: ast.expr | None) -> tuple[str, tuple[str, ...]] | Problem:
    """(success kind, declared errors) of `-> One | NotFound | ...`."""
    if node is None:
        return (
            "an endpoint must declare its response: a success kind, then its errors",
            _RETURN_HINT,
        )
    parts = _flatten_union(node)
    names = [p.id if isinstance(p, ast.Name) else None for p in parts]
    kinds = ", ".join(API_SUCCESS_KINDS)
    errs = ", ".join(API_ERRORS)
    if names[0] not in API_SUCCESS_KINDS:
        return (f"the response must start with a success kind ({kinds})", _RETURN_HINT)
    errors: list[str] = []
    for n in names[1:]:
        if n in API_SUCCESS_KINDS:
            return ("declare exactly one success kind, first", _RETURN_HINT)
        if n not in API_ERRORS:
            shown = n if n is not None else "this"
            return (
                f"'{shown}' is not a declarable error (declare any of: {errs})",
                "401 and 422 are automatic and never declared",
            )
        if n in errors:
            return (f"error '{n}' is declared twice", "declare each error once")
        errors.append(n)
    assert names[0] is not None
    return names[0], tuple(errors)


# ---- the caller ---------------------------------------------------------------------


def user_table(api: ApiProfile) -> TableT | None:
    """`user: CurrentUser`: the caller's row of the users table (one row)."""
    schema = api.tables.get(api.users_table)
    if schema is None:
        return None
    owner_cols = frozenset({schema.owner}) if schema.owner is not None else frozenset()
    return TableT(
        schema,
        at_most_one_row=True,
        keys=schema.keys,
        nullable=schema.nullable,
        owner=schema.owner,
        owned_from=frozenset({api.users_table}) if schema.owner is not None else frozenset(),
        owner_cols=owner_cols,
        owner_checked=True,  # the caller's own row
        private=schema.private,
        source=api.users_table,
        plain=plain_columns(schema),
    )


def endpoint_info(fn: ast.FunctionDef) -> EndpointInfo | None:
    """The metadata of an endpoint whose declaration P1 accepted."""
    deco = endpoint_decorator(fn)
    if deco is None:
        return None
    decl = parse_decl(deco)
    ret = parse_returns(fn.returns)
    if not isinstance(decl, Decl) or not isinstance(ret[1], tuple):
        return None
    success, errors = ret
    auth = decl.auth.id if isinstance(decl.auth, ast.Name) else ""
    return EndpointInfo(
        fn.name,
        decl.method,
        decl.path,
        auth,
        endpoint_params(fn, decl.method, decl.path),
        success,  # type: ignore[arg-type]
        errors,
        fn,
        span_of(fn),
        response=decl.response,
    )
