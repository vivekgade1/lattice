"""Lattice's static types: scalar column types, table schemas, and checker types."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Scalar(StrEnum):
    INT = "Int"
    FLOAT = "Float"
    STR = "Str"
    BOOL = "Bool"
    DATE = "Date"


NUMERIC: frozenset[Scalar] = frozenset({Scalar.INT, Scalar.FLOAT})
ORDERED: frozenset[Scalar] = frozenset({Scalar.INT, Scalar.FLOAT, Scalar.STR, Scalar.DATE})
SCALAR_NAMES: Mapping[str, Scalar] = {s.value: s for s in Scalar}


def unify(a: Scalar, b: Scalar) -> Scalar | None:
    """Common type of two scalars: equal types, or Int/Float widening to Float."""
    if a == b:
        return a
    if a in NUMERIC and b in NUMERIC:
        return Scalar.FLOAT
    return None


@dataclass(frozen=True)
class Schema:
    """Ordered mapping of column name -> scalar type.

    `keys` (declared unique keys: each a tuple of columns identifying a row) and
    `nullable` (columns that may be missing) are dataset metadata used by the
    checker's L420/L421 rules; they never take part in schema equality."""

    columns: tuple[tuple[str, Scalar], ...]
    keys: tuple[tuple[str, ...], ...] = field(default=(), compare=False)
    nullable: frozenset[str] = field(default=frozenset(), compare=False)

    def __post_init__(self) -> None:
        names = [n for n, _ in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate column names in schema: {names}")
        known = set(names)
        for key in self.keys:
            if not key or not set(key) <= known:
                raise ValueError(f"schema key {key!r} must name existing columns")
        if not self.nullable <= known:
            raise ValueError(f"nullable columns {sorted(self.nullable - known)} not in schema")

    @staticmethod
    def from_pairs(
        pairs: Iterable[tuple[str, Scalar]],
        *,
        keys: Iterable[Iterable[str]] = (),
        nullable: Iterable[str] = (),
    ) -> Schema:
        return Schema(tuple(pairs), tuple(tuple(k) for k in keys), frozenset(nullable))

    def names(self) -> tuple[str, ...]:
        return tuple(n for n, _ in self.columns)

    def get(self, name: str) -> Scalar | None:
        for n, t in self.columns:
            if n == name:
                return t
        return None

    def __contains__(self, name: object) -> bool:
        return any(n == name for n, _ in self.columns)

    def extend(self, pairs: Iterable[tuple[str, Scalar]]) -> Schema:
        """Append columns; caller has already rejected collisions."""
        return Schema(self.columns + tuple(pairs))

    def with_columns(self, pairs: Iterable[tuple[str, Scalar]]) -> Schema:
        """derive(): replace existing columns in place (new type), append new ones in order."""
        updates = dict(pairs)
        kept = tuple((n, updates.pop(n, t)) for n, t in self.columns)
        return Schema(kept + tuple(updates.items()))

    def project(self, names: Iterable[str]) -> Schema:
        return Schema(tuple((n, self._require(n)) for n in names))

    def renamed(self, mapping: Mapping[str, str]) -> Schema:
        return Schema(tuple((mapping.get(n, n), t) for n, t in self.columns))

    def render(self) -> str:
        return "{" + ", ".join(f"{n}: {t.value}" for n, t in self.columns) + "}"

    def _require(self, name: str) -> Scalar:
        t = self.get(name)
        if t is None:
            raise KeyError(name)
        return t


# ---- checker types (synthesized attributes stored in the checker's side table) ----


@dataclass(frozen=True)
class TableT:
    schema: Schema
    # Statically known to have at most one row (group(by=[]), limit(n=1), and row-wise
    # ops of such a table). Not part of equality: tables match by schema only.
    at_most_one_row: bool = field(default=False, compare=False)
    # Statically known unique keys (each a tuple of columns identifying a row), used by
    # the join fan-out check (L420). Not part of equality.
    keys: tuple[tuple[str, ...], ...] = field(default=(), compare=False)
    # Columns that may hold missing values, used by the aggregate null policy (L421).
    # Not part of equality.
    nullable: frozenset[str] = field(default=frozenset(), compare=False)


@dataclass(frozen=True)
class ColT:
    """A column expression producing values of `scalar`."""

    scalar: Scalar


@dataclass(frozen=True)
class LitT:
    """A constant expression (literal or scalar step parameter) of type `scalar`."""

    scalar: Scalar


@dataclass(frozen=True)
class ScalarParamT:
    """A scalar parameter of a @step."""

    scalar: Scalar


@dataclass(frozen=True)
class StepT:
    params: tuple[tuple[str, TableT | ScalarParamT], ...]
    result: TableT | UnknownT


class UnknownT:
    """Type of an expression that already produced a diagnostic; suppresses cascades."""

    _instance: UnknownT | None = None

    def __new__(cls) -> UnknownT:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN = UnknownT()

Type = TableT | ColT | LitT | ScalarParamT | StepT | UnknownT
