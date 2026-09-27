"""The v1 catalog: op call shapes, importable names, fn/agg signatures, aliases (pure data)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

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


OP_SHAPES: Mapping[str, OpShape] = {
    "load": OpShape(("path",), 0, frozenset({"schema"})),
    "where": OpShape(("table", "predicate"), 1),
    "derive": OpShape(("table",), 1, var_kw=True),
    "select": OpShape(("table",), 1, frozenset({"cols"})),
    "rename": OpShape(("table",), 1, var_kw=True),
    "group": OpShape(("table",), 1, frozenset({"by"}), var_kw=True),
    "join": OpShape(("left", "right"), 2, frozenset({"on"}), frozenset({"how", "fanout"})),
    "sort": OpShape(("table",), 1, frozenset({"by"}), frozenset({"desc"})),
    "limit": OpShape(("table",), 1, frozenset({"n"})),
    "write": OpShape(("table", "path"), 1),
}
TABLE_OPS = frozenset(OP_SHAPES)
TERMINAL_OPS = frozenset({"write"})
IO_OPS = frozenset({"load", "write"})
EXPR_FUNCS = frozenset({"col", "when", "coalesce", "is_null"})
NAMESPACES = frozenset({"agg", "fn"})
TYPE_NAMES = frozenset({"Table", "Int", "Float", "Str", "Bool", "Date"})
IMPORTABLE = TABLE_OPS | EXPR_FUNCS | NAMESPACES | TYPE_NAMES | {"step"}
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


@dataclass(frozen=True)
class FnSig:
    params: tuple[frozenset[Scalar], ...]  # allowed scalar types per positional argument
    kwargs: Mapping[str, Scalar]  # required keyword args; values must be literals
    result: Scalar | None  # None = same type as the first argument


N, S_, D = NUMERIC, frozenset({Scalar.STR}), frozenset({Scalar.DATE})
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
    "contains": FnSig((S_,), {"sub": Scalar.STR}, Scalar.BOOL),
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
}
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
    "contains": "contains `sub` (plain text)",
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
}

DATE_DIFF_UNITS = frozenset({"days"})
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
    "owned": OpShape(("table",), 1, frozenset({"by"})),
    "one": OpShape(("table",), 1, frozenset({"else_"})),
    "require": OpShape(("table", "condition"), 1, frozenset({"else_"})),
    "page": OpShape(("table",), 1, frozenset({"limit"}), frozenset({"offset"})),
    "insert": OpShape(("table_name",), 0, var_kw=True),
    "update": OpShape(("table",), 1, var_kw=True),
    "delete": OpShape(("table",), 1),
}
API_OPS = frozenset(API_OP_SHAPES)
API_WRITE_OPS = frozenset({"insert", "update", "delete"})
# Table ops allowed inside an endpoint body (besides steps); anything else is L841.
API_ENDPOINT_OPS = API_OPS | frozenset(
    {"where", "derive", "select", "rename", "group", "join", "sort", "limit"}
)
API_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
QUERY_METHODS = frozenset({"GET", "DELETE"})  # other methods take non-path params from the body
API_SUCCESS_KINDS = ("One", "Page", "Created", "NoContent")
# Declarable errors and their HTTP status codes (401 and 422 are automatic).
API_ERRORS: Mapping[str, int] = {
    "NotFound": 404,
    "Forbidden": 403,
    "Conflict": 409,
    "BadRequest": 400,
}
PUBLIC_ROLE = "Public"
CURRENT_USER = "CurrentUser"
USER_PARAM = "user"
PAGE_LIMIT_MAX = 100
# Parameter type constructors and their literal constraint keywords.
PARAM_CONSTRAINTS: Mapping[str, frozenset[str]] = {
    "Int": frozenset({"ge", "le"}),
    "Float": frozenset({"ge", "le"}),
    "Str": frozenset({"min_len", "max_len"}),
}
API_TYPE_NAMES = frozenset({"OneOf", CURRENT_USER})
API_AMBIENT = (
    API_OPS | frozenset(API_ERRORS) | frozenset(API_SUCCESS_KINDS) | API_TYPE_NAMES | {"endpoint"}
)
# The expression subset the API profile compiles to SQL (anything else is L841).
API_FN_FUNCS = frozenset(
    {"lower", "upper", "strip", "len", "contains", "starts_with", "year", "month", "day"}
)
API_AGG_FUNCS = frozenset({"sum", "count", "count_distinct", "min", "max", "mean"})
