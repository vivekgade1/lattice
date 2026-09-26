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
    "join": OpShape(("left", "right"), 2, frozenset({"on"}), frozenset({"how"})),
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
AGG_FUNCS = frozenset({"sum", "mean", "min", "max", "count"})


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
DATE_DIFF_UNITS = frozenset({"days"})

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
