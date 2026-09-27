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
    # API profile scalars (real apps): a UUID (canonical lowercase text; compared only
    # with == and !=) and a timezone-aware instant (ISO-8601 text ending in Z; ordered).
    UUID = "Uuid"
    DATETIME = "DateTime"


NUMERIC: frozenset[Scalar] = frozenset({Scalar.INT, Scalar.FLOAT})
ORDERED: frozenset[Scalar] = frozenset(
    {Scalar.INT, Scalar.FLOAT, Scalar.STR, Scalar.DATE, Scalar.DATETIME}
)
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
    # API profile metadata: the column holding the owning user's id, and columns that
    # must never leave the server (checker rules L802/L803).
    owner: str | None = field(default=None, compare=False)
    private: frozenset[str] = field(default=frozenset(), compare=False)
    # Indirect ownership: (foreign-key column, parent schema name) -- a row belongs to
    # the owner of the parent row its foreign key references (e.g. an order item
    # belongs to the owner of its order). The parent must have a direct owner column
    # and a single-column key (checked by `validate_owner_via`).
    owner_via: tuple[str, str] | None = field(default=None, compare=False)
    # Columns only an Admin endpoint may write (e.g. a user's role): update()/insert()
    # setting one in any other endpoint is L802.
    admin_write: frozenset[str] = field(default=frozenset(), compare=False)
    # Columns of an owned table that an aggregate mixing many owners' rows may group by,
    # filter on and aggregate in a non-Admin endpoint (e.g. review ratings per product).
    # Every other column keeps such an aggregate owner-private (checker rule L802).
    public_group: frozenset[str] = field(default=frozenset(), compare=False)

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
        if self.owner is not None and self.owner not in known:
            raise ValueError(f"owner column {self.owner!r} not in schema")
        if not self.private <= known:
            raise ValueError(f"private columns {sorted(self.private - known)} not in schema")
        if self.owner_via is not None:
            fk, _parent = self.owner_via
            if fk not in known:
                raise ValueError(f"owner_via column {fk!r} not in schema")
            if self.owner is not None:
                raise ValueError("a schema has either an owner column or owner_via, not both")
        if not self.admin_write <= known:
            extra = sorted(self.admin_write - known)
            raise ValueError(f"admin_write columns {extra} not in schema")
        if not self.public_group <= known:
            extra = sorted(self.public_group - known)
            raise ValueError(f"public_group columns {extra} not in schema")
        if self.public_group & self.private:
            both = sorted(self.public_group & self.private)
            raise ValueError(f"public_group columns {both} are private")
        if self.public_group & identity_columns(self):
            ids = sorted(self.public_group & identity_columns(self))
            raise ValueError(f"public_group columns {ids} identify a row or its owner")

    @staticmethod
    def from_pairs(
        pairs: Iterable[tuple[str, Scalar]],
        *,
        keys: Iterable[Iterable[str]] = (),
        nullable: Iterable[str] = (),
        owner: str | None = None,
        private: Iterable[str] = (),
        owner_via: tuple[str, str] | None = None,
        admin_write: Iterable[str] = (),
        public_group: Iterable[str] = (),
    ) -> Schema:
        return Schema(
            tuple(pairs),
            tuple(tuple(k) for k in keys),
            frozenset(nullable),
            owner,
            frozenset(private),
            owner_via,
            frozenset(admin_write),
            frozenset(public_group),
        )

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


def validate_owner_via(tables: Mapping[str, Schema]) -> None:
    """Every `owner_via` names a parent schema in `tables` that has a direct owner
    column and a single-column key (what the foreign key references)."""
    for name, schema in tables.items():
        if schema.owner_via is None:
            continue
        fk, parent_name = schema.owner_via
        parent = tables.get(parent_name)
        if parent is None:
            raise ValueError(f"{name}.owner_via names unknown table {parent_name!r}")
        if parent.owner is None:
            raise ValueError(f"{name}.owner_via parent {parent_name!r} has no owner column")
        if not parent.keys or len(parent.keys[0]) != 1:
            raise ValueError(f"{name}.owner_via parent {parent_name!r} needs a one-column key")


def via_parent_key(parent: Schema) -> str:
    """The column an owner_via foreign key references (the parent's first key)."""
    return parent.keys[0][0]


def identity_columns(schema: Schema) -> frozenset[str]:
    """Columns that identify a row or its owner: key columns, the owner column and the
    owner_via foreign key."""
    cols = {c for k in schema.keys for c in k}
    if schema.owner is not None:
        cols.add(schema.owner)
    if schema.owner_via is not None:
        cols.add(schema.owner_via[0])
    return frozenset(cols)


def plain_columns(schema: Schema) -> frozenset[str]:
    """Stored columns that neither identify a row/owner nor are private -- for an owned
    table (owner column or owner_via) only those it declares publicly aggregatable
    (`public_group`): an aggregate over many owners' rows may group by, filter on and
    aggregate only these."""
    ordinary = frozenset(schema.names()) - identity_columns(schema) - schema.private
    if schema.owner is not None or schema.owner_via is not None:
        return ordinary & schema.public_group
    return ordinary


def generated_key(schema: Schema) -> str | None:
    """The key column the database assigns on insert: a one-column Int first key."""
    if schema.keys and len(schema.keys[0]) == 1:
        key = schema.keys[0][0]
        if schema.get(key) is Scalar.INT:
            return key
    return None


def uuid_key(schema: Schema) -> str | None:
    """The key column an insert fills with a new uuid4 when it is not given: a
    one-column Uuid first key (unlike `generated_key`, an insert may also set it)."""
    if schema.keys and len(schema.keys[0]) == 1:
        key = schema.keys[0][0]
        if schema.get(key) is Scalar.UUID:
            return key
    return None


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
    # ---- API profile metadata (never set for tabular programs; not part of equality) ----
    # Column holding the owning user's id (from Schema.owner), while it survives.
    owner: str | None = field(default=None, compare=False)
    # Indirect owner (Schema.owner_via): (foreign-key column, parent schema name), while
    # the foreign-key column survives.
    owner_via: tuple[str, str] | None = field(default=None, compare=False)
    # Database tables with an owner column (or owner_via) whose per-owner rows this
    # table derives from. Cleared by a group(...) whose rows are not per-owner.
    owned_from: frozenset[str] = field(default=frozenset(), compare=False)
    # Columns whose values identify the owner: the owner / owner_via column and values
    # computed from them.
    owner_cols: frozenset[str] = field(default=frozenset(), compare=False)
    # The rows were narrowed by owner-identifying or key columns (a where/join on
    # them), so an aggregate over them may describe a single owner.
    narrowed: bool = field(default=False, compare=False)
    # The rows passed through owned(..., by=user) (or are the caller's own insert).
    owner_checked: bool = field(default=False, compare=False)
    # Columns holding private data (Schema.private, and values computed from them).
    private: frozenset[str] = field(default=frozenset(), compare=False)
    # Database table (schema name) whose rows these are, while update()/delete() can
    # still identify them: fetch/insert/user, kept by row filters and row-wise ops.
    source: str | None = field(default=None, compare=False)
    # The result of page(...) (kept by derive/select/rename).
    paged: bool = field(default=False, compare=False)
    # page(..., total=True): the response carries the row count before limit/offset
    # (the profile's page_shape["total"] field). Kept exactly like `paged`.
    page_total: bool = field(default=False, compare=False)
    # "Plain" columns: unmodified stored values of a database column that is not a key,
    # owner-identifying or private column (kept by row ops, select, rename, group keys;
    # never a derived value). Only a group whose keys are all plain and whose aggregates
    # read no owner-identifying or private column yields rows that are not per-owner.
    plain: frozenset[str] = field(default=frozenset(), compare=False)

    @property
    def unchecked_owned(self) -> frozenset[str]:
        """Owned database tables whose rows reach this table without owned()."""
        return frozenset() if self.owner_checked else self.owned_from


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
