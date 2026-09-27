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
    "sort": OpShape(("table",), 1, frozenset({"by"}), frozenset({"desc"})),
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


@dataclass(frozen=True)
class FnSig:
    params: tuple[frozenset[Scalar], ...]  # allowed scalar types per positional argument
    kwargs: Mapping[str, Scalar]  # required keyword args; values must be literals
    result: Scalar | None  # None = same type as the first argument


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
    # API profile only (API_ONLY_FNS)
    "to_uuid": FnSig((S_,), {}, Scalar.UUID),
    "now": FnSig((), {}, Scalar.DATETIME),
    "date": FnSig((DT,), {}, Scalar.DATE),
    "to_datetime": FnSig((S_,), {"format": Scalar.STR}, Scalar.DATETIME),
}
# fn.* that exist only in API programs (Profile.api set): tabular programs have no
# Uuid/DateTime values, so there they are unknown (L106) and not in the prompt.
API_ONLY_FNS = frozenset({"to_uuid", "now", "date", "to_datetime"})
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
    "to_uuid": 'parse a UUID such as "5b3e9c1c-0000-4000-8000-000000000000"; null if invalid',
    "now": "the current instant (UTC), the same value everywhere in one request",
    "date": "the calendar date (UTC) of a DateTime",
    "to_datetime": (
        'parse with a strftime `format` such as "%Y-%m-%d %H:%M" (UTC unless the format '
        "has an offset); null if unparsable"
    ),
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
    "page": OpShape(("table",), 1, frozenset({"limit"}), frozenset({"offset", "total"})),
    "insert": OpShape(("table_name",), 0, var_kw=True),
    "update": OpShape(("table",), 1, var_kw=True),
    "delete": OpShape(("table",), 1),
}
API_OPS = frozenset(API_OP_SHAPES)
# insert(..., unique_or=Conflict("...")) / update(..., unique_or=...): the declared 409
# answered when the write hits a UNIQUE constraint (a reserved keyword, not a column).
UNIQUE_OR = "unique_or"
API_WRITE_OPS = frozenset({"insert", "update", "delete"})
# Table ops allowed inside an endpoint body (besides steps); anything else is L841.
API_ENDPOINT_OPS = API_OPS | frozenset(
    {"where", "derive", "select", "rename", "group", "join", "sort", "limit", "window", "traverse"}
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
# `native.<name>(...)`: a typed native function declared by ApiProfile.natives.
NATIVE_NS = "native"
API_AMBIENT = (
    API_OPS
    | frozenset(API_ERRORS)
    | frozenset(API_SUCCESS_KINDS)
    | API_TYPE_NAMES
    | {"endpoint", UNSET_NAME, OWNER_IS, NATIVE_NS}
)
# The expression subset the API profile compiles to SQL (anything else is L841).
API_FN_FUNCS = frozenset(
    {"lower", "upper", "strip", "len", "contains", "starts_with", "year", "month", "day"}
    | API_ONLY_FNS
)
API_AGG_FUNCS = frozenset(
    {"sum", "count", "count_distinct", "min", "max", "mean", "at_min", "at_max"}
)
