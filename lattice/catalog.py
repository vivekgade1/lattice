"""The v1 catalog: op call shapes, importable names, fn/agg signatures, aliases (pure data)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from lattice.typesys import NUMERIC, Scalar

OPS_MODULE = "lattice.ops"
SCHEMAS_MODULE = "lattice.schemas"


@dataclass(frozen=True)
class OpShape:
    positional: tuple[str, ...]  # names of positional params, in order
    tables: int  # how many leading positional params are tables
    required_kw: frozenset[str] = frozenset()
    optional_kw: frozenset[str] = frozenset()
    var_kw: bool = False  # accepts arbitrary extra keywords (new columns / aggregates / renames)


# window()'s own keywords (every other keyword names a new column)
WINDOW_KW = frozenset({"by", "order", "desc"})

OP_SHAPES: Mapping[str, OpShape] = {
    "load": OpShape(("path",), 0, frozenset({"schema"})),
    "where": OpShape(("table", "predicate"), 1),
    "derive": OpShape(("table",), 1, var_kw=True),
    "select": OpShape(("table",), 1, frozenset({"cols"})),
    "rename": OpShape(("table",), 1, var_kw=True),
    "group": OpShape(("table",), 1, frozenset({"by"}), var_kw=True),
    "join": OpShape(("left", "right"), 2, frozenset({"on"}), frozenset({"how", "fanout"})),
    "sort": OpShape(("table",), 1, frozenset({"by"}), frozenset({"desc", "nulls"})),
    "limit": OpShape(("table",), 1, frozenset({"n"})),
    # window(t, by=[...], order=[...], desc=[...], name=win.<f>(...), ...)
    "window": OpShape(("table",), 1, optional_kw=WINDOW_KW, var_kw=True),
    # traverse(edges, start, parent="...", child="...", max_depth=N, direction="down")
    "traverse": OpShape(
        ("edges", "start"),
        2,
        frozenset({"parent", "child", "max_depth"}),
        frozenset({"direction"}),
    ),
    "write": OpShape(("table", "path"), 1),
}
TABLE_OPS = frozenset(OP_SHAPES)
TERMINAL_OPS = frozenset({"write"})
IO_OPS = frozenset({"load", "write"})
EXPR_FUNCS = frozenset({"col", "when", "coalesce", "is_null"})
NAMESPACES = frozenset({"agg", "fn", "win"})
TYPE_NAMES = frozenset({"Table", "Int", "Float", "Str", "Bool", "Date"})
# Loop constructs (spec 1b §3.3, §3.4). Not table ops: iterate(step, t, ...) takes a
# step first, and for_each(t, native.x, args...) is a statement with no result.
ITERATE = "iterate"
FOR_EACH = "for_each"
# iterate(step, t, until="fixpoint" | <Col[Bool]>, max=N): the call shape (the step and
# the table are positional), the fixpoint spelling of until=, the max= bound (L436)
ITERATE_SHAPE = OpShape(("step", "table"), 0, frozenset({"until", "max"}))
ITERATE_FIXPOINT = "fixpoint"
ITERATE_MAX = 10_000
# for_each(t, native.x, args...): the input of at most this many rows (page / limit, L439)
FOR_EACH_MAX_ROWS = 100
LOOP_CONSTRUCTS = frozenset({ITERATE, FOR_EACH})
IMPORTABLE = TABLE_OPS | EXPR_FUNCS | NAMESPACES | TYPE_NAMES | LOOP_CONSTRUCTS | {"step"}
AGG_FUNCS = frozenset({"sum", "mean", "min", "max", "count", "count_distinct", "at_min", "at_max"})
# aggregates that pick one row's value by an ordering expression: agg.at_max(x, of=o)
ORDER_AGGS = frozenset({"at_min", "at_max"})
# Rejected names: "first"/"last" read as "first row after a sort" (the pandas idiom), but
# aggregates never see row order — models sorted newest-first and took agg.first, getting
# the oldest value silently.
AGG_ALIASES: Mapping[str, str] = {
    "first": "at_min",
    "last": "at_max",
    "arg_min": "at_min",
    "arg_max": "at_max",
}

# win.* -- window functions, only as window() keyword values (spec 1b §3.1).
WIN_RANKINGS = frozenset({"row_number", "rank", "dense_rank"})
WIN_OFFSETS = frozenset({"lag", "lead"})
WIN_CUMULATIVE = frozenset({"cum_sum", "cum_count", "cum_min", "cum_max"})
WIN_MOVING = frozenset({"mean", "sum"})
WIN_FUNCS = WIN_RANKINGS | WIN_OFFSETS | WIN_CUMULATIVE | WIN_MOVING
# Functions whose value on a row does not depend on how rows tied in `order` are
# ordered among themselves (tied rows share a rank); every other one does (L431).
WIN_TIE_SAFE = frozenset({"rank", "dense_rank"})
# traverse(): the added depth column, the directions, the max_depth= bounds (L433)
TRAVERSE_DEPTH = "depth"
TRAVERSE_DIRECTIONS = ("down", "up")
TRAVERSE_MAX_DEPTH = 1000
# sort(t, by=[...], nulls=...): where null keys go. "last" (the default) and "first"
# hold in either direction; "smallest" orders nulls as the smallest value: first when
# ascending, last when descending (SQL's usual order).
SORT_NULLS = ("last", "first", "smallest")


@dataclass(frozen=True)
class FnSig:
    params: tuple[frozenset[Scalar], ...]  # allowed scalar types per positional argument
    kwargs: Mapping[str, Scalar]  # required keyword args; values must be literals
    result: Scalar | None  # None = same type as the first argument
    # optional keyword args (same value rules as kwargs)
    optional: Mapping[str, Scalar] = field(default_factory=lambda: MappingProxyType({}))


N, S_, D = NUMERIC, frozenset({Scalar.STR}), frozenset({Scalar.DATE})
DT = frozenset({Scalar.DATETIME})
ANY = frozenset(Scalar)
_F = frozenset({Scalar.FLOAT})

FN_SIGS: Mapping[str, FnSig] = {
    "abs": FnSig((N,), {}, None),
    "round": FnSig((_F,), {"digits": Scalar.INT}, Scalar.FLOAT),
    "floor": FnSig((_F,), {}, Scalar.FLOAT),
    "ceil": FnSig((_F,), {}, Scalar.FLOAT),
    "log": FnSig((N,), {}, Scalar.FLOAT),
    "sqrt": FnSig((N,), {}, Scalar.FLOAT),
    "clip": FnSig((N,), {"lo": Scalar.FLOAT, "hi": Scalar.FLOAT}, None),
    "lower": FnSig((S_,), {}, Scalar.STR),
    "upper": FnSig((S_,), {}, Scalar.STR),
    "strip": FnSig((S_,), {}, Scalar.STR),
    "len": FnSig((S_,), {}, Scalar.INT),
    "contains": FnSig((S_,), {"sub": Scalar.STR}, Scalar.BOOL, {"ignore_case": Scalar.BOOL}),
    "starts_with": FnSig((S_,), {"prefix": Scalar.STR}, Scalar.BOOL),
    "replace": FnSig((S_,), {"old": Scalar.STR, "new": Scalar.STR}, Scalar.STR),
    "concat": FnSig((S_, S_), {}, Scalar.STR),
    "year": FnSig((D,), {}, Scalar.INT),
    "month": FnSig((D,), {}, Scalar.INT),
    "day": FnSig((D,), {}, Scalar.INT),
    "date_diff": FnSig((D, D), {"unit": Scalar.STR}, Scalar.INT),
    "date_add": FnSig((D,), {"days": Scalar.INT}, Scalar.DATE),
    "to_date": FnSig((S_,), {"format": Scalar.STR}, Scalar.DATE),
    "to_int": FnSig((ANY,), {}, Scalar.INT),
    "to_float": FnSig((ANY,), {}, Scalar.FLOAT),
    "to_str": FnSig((ANY,), {}, Scalar.STR),
    # API profile only (API_ONLY_FNS)
    "to_uuid": FnSig((S_,), {}, Scalar.UUID),
    "now": FnSig((), {}, Scalar.DATETIME),
    "date": FnSig((DT,), {}, Scalar.DATE),
    "to_datetime": FnSig((S_,), {"format": Scalar.STR}, Scalar.DATETIME),
    "datetime_add": FnSig(
        (DT,),
        {},
        Scalar.DATETIME,
        {"days": Scalar.INT, "hours": Scalar.INT, "minutes": Scalar.INT, "seconds": Scalar.INT},
    ),
    "datetime_diff": FnSig((DT, DT), {"unit": Scalar.STR}, Scalar.INT),
}
# fn.* that exist only in API programs (Profile.api set): tabular programs have no
# Uuid/DateTime values, so there they are unknown (L106) and not in the prompt.
API_ONLY_FNS = frozenset(
    {"to_uuid", "now", "date", "to_datetime", "datetime_add", "datetime_diff"}
)
# One-line semantics for every fn.* (rendered into the model's system prompt).
FN_DOCS: Mapping[str, str] = {
    "abs": "absolute value",
    "round": "round to `digits` decimals",
    "floor": "round down",
    "ceil": "round up",
    "log": "natural log; null where x <= 0",
    "sqrt": "square root; null where x < 0",
    "clip": "limit to [lo, hi]",
    "lower": "lowercase",
    "upper": "uppercase",
    "strip": "trim leading/trailing whitespace",
    "len": "character count",
    "contains": "contains `sub` (plain text); `ignore_case=True` compares both lowercased",
    "starts_with": "starts with `prefix`",
    "replace": "replace every `old` with `new` (plain text)",
    "concat": "join two strings; null if either is null",
    "year": "calendar year",
    "month": "month 1-12",
    "day": "day of month 1-31",
    "date_diff": (
        'end minus start in whole days (positive when end is later); unit "days" only; '
        'e.g. days since signup: fn.date_diff(cutoff, col("signup_date"), '
        'unit="days")'
    ),
    "date_add": "shift by `days` (negative is earlier)",
    "to_date": 'parse with a strftime `format` such as "%Y-%m-%d"; null if unparsable',
    "to_int": "null if not convertible",
    "to_float": "null if not convertible",
    "to_str": "as text",
    "to_uuid": 'parse a UUID such as "5b3e9c1c-0000-4000-8000-000000000000"; null if invalid',
    "now": "the current instant (UTC), the same value everywhere in one request",
    "date": "the calendar date (UTC) of a DateTime",
    "to_datetime": (
        'parse with a strftime `format` such as "%Y-%m-%d %H:%M" (UTC unless the format '
        "has an offset); null if unparsable"
    ),
    "datetime_add": (
        "shift by `days`/`hours`/`minutes`/`seconds` (at least one; negative is earlier); "
        "null out of range"
    ),
    "datetime_diff": (
        'end minus start in whole `unit`s ("days", "hours", "minutes" or "seconds"), '
        "truncated toward zero"
    ),
}

DATE_DIFF_UNITS = frozenset({"days"})
DATETIME_DIFF_UNITS = ("days", "hours", "minutes", "seconds")
DATETIME_ADD_UNITS = ("days", "hours", "minutes", "seconds")
ROUND_DIGITS_MAX = 2**32 - 1  # fn.round digits= range (polars takes a u32)
DATE_ADD_DAYS_MAX = 36_500_000  # |fn.date_add days=| (~100k years; keeps dates representable)

FN_ALIASES: Mapping[str, str] = {
    "length": "len",
    "strlen": "len",
    "trim": "strip",
    "lowercase": "lower",
    "uppercase": "upper",
    "startswith": "starts_with",
    "absolute": "abs",
    "ln": "log",
    "str": "to_str",
    "int": "to_int",
    "float": "to_float",
}
OP_ALIASES: Mapping[str, str] = {
    "filter": "where",
    "mutate": "derive",
    "with_columns": "derive",
    "groupby": "group",
    "group_by": "group",
    "aggregate": "group",
    "merge": "join",
    "order_by": "sort",
    "head": "limit",
    "read_csv": "load",
    "save": "write",
    "columns": "select",
}

# ---- API profile (only in scope when Profile.api is set) ----------------------------

# API-only ops. `fetch`/`insert` take a database table (schema) name positionally, so
# their `tables` count is 0; `insert` takes `owner=user` plus one keyword per column.
API_OP_SHAPES: Mapping[str, OpShape] = {
    "fetch": OpShape(("table_name",), 0),
    "accessible": OpShape(("table",), 1, frozenset({"by"})),
    "owned": OpShape(("table",), 1, frozenset({"by"})),
    "mine": OpShape(("table",), 1, frozenset({"by"})),
    "one": OpShape(("table",), 1, frozenset({"else_"})),
    "require": OpShape(("table", "condition"), 1, frozenset({"else_"})),
    "page": OpShape(("table",), 1, frozenset({"limit"}), frozenset({"offset", "total"})),
    "insert": OpShape(("table_name",), 0, var_kw=True),
    "update": OpShape(("table",), 1, var_kw=True),
    "delete": OpShape(("table",), 1),
}
API_OPS = frozenset(API_OP_SHAPES)
# `accessible(t, by=user)` keeps the rows the caller may access (Admin: every row);
# `owned` is its older name, kept as an alias. `mine(t, by=user)` keeps only the
# caller's own rows, for every role.
OWNER_FILTERS = frozenset({"accessible", "owned", "mine"})
# Alias -> the preferred op (the guide documents only the preferred one).
API_OP_ALIASES: Mapping[str, str] = {"owned": "accessible"}
# Ambient API names that programs may still bind as variables (`mine = owned(...)` was
# the idiom before `mine()` existed): only a body that never calls the op may bind it.
SOFT_API_NAMES = frozenset({"mine", "accessible", "is_admin"})
# insert(..., unique_or=Conflict("...")) / update(..., unique_or=...): the declared 409
# answered when the write hits a UNIQUE constraint (a reserved keyword, not a column).
UNIQUE_OR = "unique_or"
# one(..., else_=E) / require(..., else_=E): the declared error; update(t, owner=...,
# else_=E): the error answered when the new owner does not exist.
ELSE_KW = "else_"
API_WRITE_OPS = frozenset({"insert", "update", "delete"})
# Table ops allowed inside an endpoint body (besides steps); anything else is L841.
API_ENDPOINT_OPS = API_OPS | frozenset(
    {"where", "derive", "select", "rename", "group", "join", "sort", "limit", "window", "traverse"}
)
API_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
QUERY_METHODS = frozenset({"GET", "DELETE"})  # other methods take non-path params from the body
API_SUCCESS_KINDS = ("One", "Page", "Created", "NoContent")
# `-> List`: a JSON array of the returned rows (no envelope); the rows must be bounded
# (L439's rule), e.g. the value of a whole-value procedure call (T2).
LIST_KIND = "List"
# Declarable errors and their HTTP status codes (401 and 422 are automatic).
API_ERRORS: Mapping[str, int] = {
    "NotFound": 404,
    "Forbidden": 403,
    "Conflict": 409,
    "BadRequest": 400,
}
PUBLIC_ROLE = "Public"
# auth=Authenticated: any authenticated caller; auth=Roles(A, B): exactly these roles
# (spec 2026-09-29-lattice-role-sets-design). Reserved: no profile role may be named so.
AUTHENTICATED = "Authenticated"
ROLES_CALL = "Roles"
RESERVED_ROLE_NAMES = frozenset({AUTHENTICATED, ROLES_CALL})
CURRENT_USER = "CurrentUser"
USER_PARAM = "user"
PAGE_LIMIT_MAX = 100
# Parameter type constructors and their literal constraint keywords.
# `pattern=` is a string literal (a Python regular expression the whole value must match).
PARAM_CONSTRAINTS: Mapping[str, frozenset[str]] = {
    "Int": frozenset({"ge", "le"}),
    "Float": frozenset({"ge", "le"}),
    "Str": frozenset({"min_len", "max_len", "pattern"}),
    "Email": frozenset({"min_len", "max_len"}),
}
# `Email`: a Str parameter that must be an email address (EMAIL_PATTERN, a syntactic
# check: a dot-atom local part, "@", and a domain of dot-separated labels ending in a
# 2-63 letter top-level label; ASCII only, no quoted local parts or IP literals).
EMAIL_TYPE = "Email"
EMAIL_PATTERN = (
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
)
# RFC 5321: an address is at most 254 characters (pydantic's EmailStr refuses longer
# ones), whatever `Email(max_len=)` allows; both limits apply.
EMAIL_MAX_LEN = 254
# `Uuid` and `DateTime` are scalar types only API programs have (tabular data has none).
API_TYPE_NAMES = frozenset({"OneOf", CURRENT_USER, "Uuid", "DateTime", EMAIL_TYPE})
# `UNSET`: an endpoint parameter default meaning "not sent" (partial updates).
UNSET_NAME = "UNSET"
# `owner_is(user)`: the whole condition of require(t, owner_is(user), else_=Forbidden(..)).
OWNER_IS = "owner_is"
# `sent(p)`: whether the request included the optional (`= UNSET`) parameter p (a Bool).
SENT = "sent"
# `native.<name>(...)`: a typed native function declared by ApiProfile.natives.
NATIVE_NS = "native"
# Caller references in endpoint expressions: `user.<column>` (a non-private column of
# the users table), `user.is_admin` and `is_admin(user)` (a Bool: the caller has the
# admin role) -- the caller's values, the same on every row.
IS_ADMIN = "is_admin"
API_AMBIENT = (
    API_OPS
    | frozenset(API_ERRORS)
    | frozenset(API_SUCCESS_KINDS)
    | {LIST_KIND}
    | API_TYPE_NAMES
    | {"endpoint", UNSET_NAME, OWNER_IS, NATIVE_NS, SENT, IS_ADMIN}
)
# The expression subset the API profile compiles to SQL (anything else is L841).
API_FN_FUNCS = frozenset(
    {"lower", "upper", "strip", "len", "contains", "starts_with", "concat", "year", "month",
     "day"}
    | API_ONLY_FNS
)
API_AGG_FUNCS = frozenset(
    {"sum", "count", "count_distinct", "min", "max", "mean", "at_min", "at_max"}
)

# ---- T2: @procedure (sub-project 1c, spec §2) ---------------------------------------
# Ordinary Python in a checked subset, typed by lattice.check.t2. Only API programs
# have procedures. Everything below is data: the checker interprets the signature
# strings with the annotation language (lattice.check.t2.types.parse_sig).

PROCEDURE = "procedure"
RECORD = "record"
ROW_TYPE = "Row"
CALLER_TYPE = "Caller"
# `Row(Schema, private=True)`: the keyword that makes private columns readable
ROW_PRIVATE = "private"
# `@procedure(effects=[...])`: the one decorator keyword
EFFECTS_KW = "effects"
# Names a program may import from lattice.ops for procedures (API programs). They are
# ambient in procedure bodies and annotations only, never in T0 code.
T2_OPS_NAMES = frozenset(
    {PROCEDURE, RECORD, ROW_TYPE, CALLER_TYPE, "Duration", "Json", "days", "hours", "minutes",
     "seconds"}
)
# The type language: Lattice names, and the Python spellings accepted as aliases.
T2_TYPE_NAMES = frozenset(
    {"Int", "Float", "Str", "Bool", "Date", "DateTime", "Duration", "Uuid", "Json", "None"}
)
T2_TYPE_ALIASES: Mapping[str, str] = {
    "int": "Int",
    "float": "Float",
    "str": "Str",
    "bool": "Bool",
    "date": "Date",
    "datetime": "DateTime",
    "timedelta": "Duration",
}
# Generic containers of the type language: name -> number of type arguments.
T2_GENERICS: Mapping[str, int] = {"list": 1, "set": 1, "dict": 2, "tuple": -1}
# Common misspellings of annotations -> the fix hint.
T2_TYPE_HINTS: Mapping[str, str] = {
    "Optional": "write 'T | None'",
    "Union": "write 'A | B' (only 'T | None' unions are supported)",
    "List": "write 'list[T]'",
    "Dict": "write 'dict[K, V]'",
    "Set": "write 'set[T]'",
    "Tuple": "write 'tuple[A, B]'",
    "Any": "name the type; for parsed JSON use Json",
    "Table": "a procedure takes rows as list[Row[Schema]]",
}
# Errors an `except` clause may name: the ones allowlisted functions raise. A bare
# `except:` / `except Exception` is L149: it would also swallow fuel exhaustion (R620).
T2_ERRORS: Mapping[str, str] = {
    "ValueError": "a conversion failed, e.g. int('x') or json.loads('{')",
    "KeyError": "d[k] for a missing key",
    "IndexError": "xs[i] out of range, or pop() from an empty list",
    "ZeroDivisionError": "division or modulo by zero",
}
T2_BROAD_ERRORS = frozenset({"Exception", "BaseException"})


@dataclass(frozen=True)
class StdSig:
    """One overload of an allowlisted function or method.

    Types are strings in the annotation language plus signature-only forms: type
    variables T, U, W, K, V; `Num` (Int or Float, the result keeps it); `Iterable[T]`
    (list, set, tuple, dict keys, str, range, views); `Sized`; `Key[T]` (a `lambda`
    over T returning an orderable value); `Any` (anything JSON-serializable, or
    printable for str()); `Iter[T]` (a one-shot iterable); `Match`; `Counter[T]`.
    """

    params: tuple[str, ...]
    result: str
    optional: tuple[str, ...] = ()  # optional trailing positionals
    kwargs: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    mutates: bool = False  # a method that changes its receiver (never on a parameter)
    literal: tuple[int, ...] = ()  # positions that must be string literals (regexes)
    doc: str = ""


def _sig(params: str, result: str, **kw: object) -> StdSig:
    ps = tuple(p.strip() for p in params.split(";")) if params else ()
    return StdSig(ps, result, **kw)  # type: ignore[arg-type]


_KEY = MappingProxyType({"key": "Key[T]", "reverse": "Bool"})
_KEY_ONLY = MappingProxyType({"key": "Key[T]"})

# Builtins callable in a procedure (name -> overloads, tried in order).
T2_BUILTINS: Mapping[str, tuple[StdSig, ...]] = {
    "len": (_sig("Sized", "Int"),),
    "abs": (_sig("Num", "Num"),),
    "round": (_sig("Float", "Int"), _sig("Float; Int", "Float")),
    "min": (
        _sig("Iterable[T]", "T", kwargs=_KEY_ONLY),
        _sig("T; T", "T", optional=("T",), kwargs=_KEY_ONLY),
    ),
    "max": (
        _sig("Iterable[T]", "T", kwargs=_KEY_ONLY),
        _sig("T; T", "T", optional=("T",), kwargs=_KEY_ONLY),
    ),
    "sum": (_sig("Iterable[Num]", "Num", optional=("Num",)),),
    "sorted": (_sig("Iterable[T]", "list[T]", kwargs=_KEY),),
    "range": (_sig("Int", "Iter[Int]", optional=("Int", "Int")),),
    "enumerate": (_sig("Iterable[T]", "Iter[tuple[Int, T]]", optional=("Int",)),),
    "zip": (
        _sig("Iterable[T]; Iterable[U]", "Iter[tuple[T, U]]"),
        _sig("Iterable[T]; Iterable[U]; Iterable[W]", "Iter[tuple[T, U, W]]"),
    ),
    "reversed": (_sig("list[T]", "Iter[T]"),),
    "any": (_sig("Iterable[Bool]", "Bool"),),
    "all": (_sig("Iterable[Bool]", "Bool"),),
    "str": (_sig("Any", "Str"),),
    "int": (_sig("Str", "Int"), _sig("Num", "Int")),
    "float": (_sig("Str", "Float"), _sig("Num", "Float")),
    "list": (_sig("", "list[T]"), _sig("Iterable[T]", "list[T]")),
    "set": (_sig("", "set[T]"), _sig("Iterable[T]", "set[T]")),
    "dict": (_sig("", "dict[K, V]"), _sig("Iterable[tuple[K, V]]", "dict[K, V]")),
}
# Lattice helpers ambient in procedures: Duration constructors.
T2_HELPERS: Mapping[str, tuple[StdSig, ...]] = {
    unit: (_sig("Num", "Duration", doc=f"a Duration of n {unit}"),)
    for unit in ("days", "hours", "minutes", "seconds")
}

# The standard-library allowlist: module -> name -> overloads (a function) or a type
# string (a constant). `import m` and `from m import name` both work; nothing else
# from these modules (or any other module) is importable (L142).
T2_STDLIB: Mapping[str, Mapping[str, tuple[StdSig, ...] | str]] = {
    "math": {
        "floor": (_sig("Num", "Int"),),
        "ceil": (_sig("Num", "Int"),),
        "trunc": (_sig("Num", "Int"),),
        "sqrt": (_sig("Num", "Float"),),
        "exp": (_sig("Num", "Float"),),
        "log": (_sig("Num", "Float", optional=("Num",)),),
        "log10": (_sig("Num", "Float"),),
        "pow": (_sig("Num; Num", "Float"),),
        "fabs": (_sig("Num", "Float"),),
        "isclose": (_sig("Float; Float", "Bool"),),
        "gcd": (_sig("Int; Int", "Int"),),
        "pi": "Float",
        "e": "Float",
        "inf": "Float",
    },
    "datetime": {
        "timedelta": (
            _sig(
                "",
                "Duration",
                kwargs=MappingProxyType(
                    {u: "Num" for u in ("weeks", "days", "hours", "minutes", "seconds")}
                ),
            ),
        ),
        "date": (_sig("Int; Int; Int", "Date"),),
    },
    "re": {
        "fullmatch": (_sig("Str; Str", "Match | None", literal=(0,)),),
        "match": (_sig("Str; Str", "Match | None", literal=(0,)),),
        "search": (_sig("Str; Str", "Match | None", literal=(0,)),),
        "sub": (_sig("Str; Str; Str", "Str", literal=(0,)),),
        "findall": (_sig("Str; Str", "list[Str]", literal=(0,)),),
        "split": (_sig("Str; Str", "list[Str]", literal=(0,)),),
    },
    "json": {
        "dumps": (
            _sig("Json", "Str", kwargs=MappingProxyType({"sort_keys": "Bool", "indent": "Int"})),
        ),
        "loads": (_sig("Str", "Json"),),
    },
    "collections": {
        "Counter": (_sig("", "Counter[T]"), _sig("Iterable[T]", "Counter[T]")),
        # defaultdict(factory): factory is list, set, int, float or str; the key and
        # value types come from the annotation (d: dict[K, V] = defaultdict(list))
        "defaultdict": (_sig("Factory", "dict[K, V]"),),
    },
    "string": {
        "ascii_letters": "Str",
        "ascii_lowercase": "Str",
        "ascii_uppercase": "Str",
        "digits": "Str",
        "punctuation": "Str",
    },
}
T2_DEFAULTDICT_FACTORIES = frozenset({"list", "set", "int", "float", "str"})

# Methods by receiver kind. Receiver type variables: list/set T; dict K, V; Counter T
# (keys; values Int).
T2_METHODS: Mapping[str, Mapping[str, tuple[StdSig, ...]]] = {
    "Str": {
        "lower": (_sig("", "Str"),),
        "upper": (_sig("", "Str"),),
        "casefold": (_sig("", "Str"),),
        "title": (_sig("", "Str"),),
        "capitalize": (_sig("", "Str"),),
        "strip": (_sig("", "Str", optional=("Str",)),),
        "lstrip": (_sig("", "Str", optional=("Str",)),),
        "rstrip": (_sig("", "Str", optional=("Str",)),),
        "split": (_sig("", "list[Str]", optional=("Str", "Int")),),
        "rsplit": (_sig("", "list[Str]", optional=("Str", "Int")),),
        "splitlines": (_sig("", "list[Str]"),),
        "join": (_sig("Iterable[Str]", "Str"),),
        "replace": (_sig("Str; Str", "Str", optional=("Int",)),),
        "startswith": (_sig("Str", "Bool"),),
        "endswith": (_sig("Str", "Bool"),),
        "find": (_sig("Str", "Int"),),
        "rfind": (_sig("Str", "Int"),),
        "count": (_sig("Str", "Int"),),
        "partition": (_sig("Str", "tuple[Str, Str, Str]"),),
        "rpartition": (_sig("Str", "tuple[Str, Str, Str]"),),
        "zfill": (_sig("Int", "Str"),),
        "ljust": (_sig("Int", "Str", optional=("Str",)),),
        "rjust": (_sig("Int", "Str", optional=("Str",)),),
        "center": (_sig("Int", "Str", optional=("Str",)),),
        "isdigit": (_sig("", "Bool"),),
        "isalpha": (_sig("", "Bool"),),
        "isalnum": (_sig("", "Bool"),),
        "isspace": (_sig("", "Bool"),),
        "islower": (_sig("", "Bool"),),
        "isupper": (_sig("", "Bool"),),
    },
    "list": {
        "append": (_sig("T", "None", mutates=True),),
        "extend": (_sig("Iterable[T]", "None", mutates=True),),
        "insert": (_sig("Int; T", "None", mutates=True),),
        "pop": (_sig("", "T", optional=("Int",), mutates=True),),
        "remove": (_sig("T", "None", mutates=True),),
        "clear": (_sig("", "None", mutates=True),),
        "sort": (_sig("", "None", kwargs=_KEY, mutates=True),),
        "reverse": (_sig("", "None", mutates=True),),
        "index": (_sig("T", "Int"),),
        "count": (_sig("T", "Int"),),
        "copy": (_sig("", "list[T]"),),
    },
    "dict": {
        "get": (_sig("K", "V | None"), _sig("K; V", "V")),
        "keys": (_sig("", "Iter[K]"),),
        "values": (_sig("", "Iter[V]"),),
        "items": (_sig("", "Iter[tuple[K, V]]"),),
        "pop": (_sig("K", "V", mutates=True), _sig("K; V", "V", mutates=True)),
        "setdefault": (_sig("K; V", "V", mutates=True),),
        "update": (_sig("dict[K, V]", "None", mutates=True),),
        "clear": (_sig("", "None", mutates=True),),
        "copy": (_sig("", "dict[K, V]"),),
    },
    "set": {
        "add": (_sig("T", "None", mutates=True),),
        "discard": (_sig("T", "None", mutates=True),),
        "remove": (_sig("T", "None", mutates=True),),
        "clear": (_sig("", "None", mutates=True),),
        "update": (_sig("Iterable[T]", "None", mutates=True),),
        "union": (_sig("Iterable[T]", "set[T]"),),
        "intersection": (_sig("Iterable[T]", "set[T]"),),
        "difference": (_sig("Iterable[T]", "set[T]"),),
        "issubset": (_sig("Iterable[T]", "Bool"),),
        "issuperset": (_sig("Iterable[T]", "Bool"),),
        "copy": (_sig("", "set[T]"),),
    },
    "Counter": {
        "most_common": (_sig("", "list[tuple[T, Int]]", optional=("Int",)),),
    },
    "Date": {
        "isoformat": (_sig("", "Str"),),
        "weekday": (_sig("", "Int"),),
        "isoweekday": (_sig("", "Int"),),
        "strftime": (_sig("Str", "Str"),),
    },
    "DateTime": {
        "isoformat": (_sig("", "Str"),),
        "weekday": (_sig("", "Int"),),
        "strftime": (_sig("Str", "Str"),),
        "date": (_sig("", "Date"),),
        "timestamp": (_sig("", "Float"),),
    },
    "Duration": {"total_seconds": (_sig("", "Float"),)},
    "Match": {
        "group": (_sig("", "Str"), _sig("Int", "Str | None"), _sig("Str", "Str | None")),
        "start": (_sig("", "Int"),),
        "end": (_sig("", "Int"),),
    },
}
# Attributes (not methods) by receiver kind.
T2_ATTRS: Mapping[str, Mapping[str, str]] = {
    "Date": {"year": "Int", "month": "Int", "day": "Int"},
    "DateTime": {
        "year": "Int",
        "month": "Int",
        "day": "Int",
        "hour": "Int",
        "minute": "Int",
        "second": "Int",
        "microsecond": "Int",
    },
    "Duration": {"days": "Int", "seconds": "Int"},
    "Float": {"real": "Float"},
}
# The Caller: `id` (the users-table key), `is_admin`, `role` and every public column
# of the users table.
T2_CALLER_BUILTIN = MappingProxyType({"is_admin": "Bool", "role": "Str"})

# Rejected calls and attributes (L140 I/O and code execution; L141 reflection).
T2_IO_CALLS = frozenset(
    {"open", "print", "input", "eval", "exec", "compile", "__import__", "breakpoint", "exit",
     "quit", "help"}
)
T2_REFLECTION_CALLS = frozenset(
    {"getattr", "setattr", "hasattr", "delattr", "vars", "globals", "locals", "dir", "type",
     "isinstance", "issubclass", "id", "super", "object", "memoryview", "classmethod",
     "staticmethod", "property"}
)

# ---- T2 in endpoints (sub-project 1c, spec §3; node T2b) ------------------------------
# Endpoints call procedures as scalar functions -- the whole value of a derive column,
# the whole predicate of where, the whole condition of require (`proc(col("a"), user,
# fn.now())`, `row()` for the whole row) -- run in the app layer after the read's one
# statement ("T2-post"); or whole-value: `x = call(proc, rows=t, ...)` over bounded
# tables. `insert_many(T, rows=x)` writes the rows of a table (a procedure's value)
# in one statement. Effectful procedures are sent with for_each(t, proc, args...).
# These names are ambient only in programs that have procedures.
T2_CALL = "call"
INSERT_MANY = "insert_many"
ROW_FN = "row"
T2_T0_SHAPES: Mapping[str, OpShape] = {
    T2_CALL: OpShape(("procedure",), 0, var_kw=True),
    INSERT_MANY: OpShape(
        ("table_name",), 0, frozenset({"rows"}), frozenset({"owner", "unique_or"})
    ),
}
T2_T0_NAMES = frozenset({T2_CALL, INSERT_MANY, ROW_FN})
# The outbox `native` of an effectful procedure sent by for_each.
T2_EFFECT_PREFIX = "procedure:"
# ApiProfile.fuel: the default fuel budget of one request.
DEFAULT_FUEL = 1_000_000
# ApiProfile.max_value_size: the default largest str (characters), list/dict/set
# (elements) or int (bits / 8) a procedure may build; larger is R622.
DEFAULT_MAX_VALUE_SIZE = 10_000_000
T2_T0_CODES: Mapping[str, str] = {
    "L880": "a procedure filter before page()/limit() (totals and pages would be computed in "
    "Python)",
    "L881": "a procedure call where T0 needs SQL: nested in an expression, an argument that "
    "is not a column/parameter/constant/user/fn.now()/row(), or an op that needs SQL "
    "over rows a procedure computed",
    "L882": "an effectful procedure outside for_each, or a pure one sent with for_each",
    "L883": "a value that does not cross the T0/T2 boundary (a type T0 has no column for, "
    "a nullable value for a non-optional parameter, a table that lacks a record's fields)",
}
RUNTIME_T2_CODES: Mapping[str, str] = {
    "R620": "a procedure ran out of fuel (ApiProfile.fuel), or recursed too deep",
    "R621": "a whole-value call returned more rows than its input bounds",
    "R622": "a procedure built a value larger than ApiProfile.max_value_size",
}

# ---- diagnostic codes --------------------------------------------------------------
# T2 subset (L140-L149) and the T2 type checker (L900-L949). L128 (plain class), L130
# (global/nonlocal), L131 (async) and L803 (private column) are shared with T0.
T2_CODES: Mapping[str, str] = {
    "L140": "I/O or code execution (open, print, input, eval, exec, ...) in a procedure",
    "L141": "reflection (getattr, type, isinstance, dunder attributes, ...) in a procedure",
    "L142": "import outside the T2 standard-library allowlist (T2_STDLIB)",
    "L144": "a @record is annotated fields only (no bases, methods or other decorators)",
    "L145": "mutation of a value passed in (parameters are immutable), or of a record",
    "L146": "statement not allowed in a procedure (with, raise, assert, del, nested def, ...)",
    "L147": "expression not allowed in a procedure (lambda outside key=, :=, *x, ...)",
    "L148": "@procedure shape: decorator, annotated plain parameters, return annotation",
    "L149": "bare `except:` / `except Exception` (would swallow fuel exhaustion)",
    "L900": "unknown name",
    "L901": "assigned value does not match the variable's type",
    "L902": "invalid type annotation",
    "L903": "operator or literal applied to incompatible types",
    "L904": "call arguments do not match the signature",
    "L905": "unknown attribute, method, field or native",
    "L906": "a value that may be None is used without a None check",
    "L907": "not every path returns a value",
    "L908": "returned value does not match the declared return type",
    "L909": "condition, iteration or unpacking of an unsupported type",
    "L910": "local variable may be unbound on some path",
    "L911": "cannot infer the element type of an empty container",
    "L912": "value is not callable",
    "L913": "subscript of an unsupported type or with a wrong index type",
    "L914": "duplicate or clashing top-level name",
    "L916": "regular expression must be a valid string literal",
    "L917": "unpacking arity mismatch",
    "L918": "except names an error type that is not declared (T2_ERRORS)",
    "L919": "break/continue outside a loop",
    "L920": "declared effect is not a capability of the profile",
    "L921": "effect native called without declaring its effect",
    "L922": "procedure calls a procedure with effects it does not declare",
    "L923": "dict key or set element of an unhashable type",
    "L925": "regular expression that may backtrack catastrophically (nested unbounded "
    "repeats, or a repeated alternation whose branches start alike)",
    "L926": "a regular expression outside the RE2 subset (lookaround, backreference, "
    "conditional, possessive repeat or atomic group)",
    "L890": "a native (call, effect or NativeToken auth) on a target without natives",
    "L891": "a tabular-profile program on a target without the tabular profile",
    "L892": "a T2 call the chosen target does not implement yet",
}
