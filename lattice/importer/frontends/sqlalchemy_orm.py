"""SQLAlchemy tables from the live metadata (Phase 2 plan §2.2, ``sqlalchemy_orm.py``).

The app is imported to run anyway, so its tables are read from what SQLAlchemy built
rather than from the source: every mapped class defined in the package (Flask-SQLAlchemy's
``db.Model``, Alchemical's ``Model``, a plain ``DeclarativeBase``), every ``Table`` a
package module holds at top level (association tables such as microblog's
``followers``), and every relationship's ``secondary`` table.

:func:`read_tables` gives, per table, a :class:`~lattice.importer.appmodel.TableModel`
(columns as ``FieldSpec``: type, null, ``String(n)`` length, primary key, unique, index,
foreign key, an ``Enum``'s values and stored names, a readable default), the constraints
no column holds (``CheckConstraint`` text and composite uniques, for mining), and every
mapped relationship with the table and column that hold its key (owner evidence follows
``post.author`` to ``posts.user_id``). Only modules already imported are read: import
the app (build it) first."""

from __future__ import annotations

import ast
import enum
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.importer.appmodel import FieldSpec, TableConstraint, TableModel


@dataclass(frozen=True)
class Rel:
    """A mapped relationship: ``direction`` (SQLAlchemy's ``MANYTOONE`` / ``ONETOMANY`` /
    ``MANYTOMANY``), the ``target`` model name, and the ``table`` (model name) and
    ``column`` holding the key that links the two: the local foreign key (many-to-one),
    the target's (one-to-many), or the association table's column for this side."""

    direction: str
    target: str
    table: str
    column: str


@dataclass
class OrmTables:
    tables: tuple[TableModel, ...]
    constraints: tuple[TableConstraint, ...]
    # model name -> relationship attribute -> Rel
    relationships: dict[str, dict[str, Rel]] = field(default_factory=dict)
    # model name -> its mapped class (None for a table without one)
    classes: dict[str, Any] = field(default_factory=dict)
    # model name -> its sqlalchemy Table
    sa_tables: dict[str, Any] = field(default_factory=dict)

    def model_of(self, cls: Any) -> str | None:
        """The model name of a mapped class (None: not one of the package's)."""
        return next((n for n, c in self.classes.items() if c is cls), None)


def package_modules(package: str) -> list[Any]:
    """The package's imported modules, by name."""
    prefix = f"{package}."
    names = sorted(n for n in list(sys.modules) if n == package or n.startswith(prefix))
    return [sys.modules[n] for n in names if sys.modules[n] is not None]


def _top_level_assigned(module: Any) -> set[str]:
    path = getattr(module, "__file__", None)
    if not path or not Path(path).is_file():
        return set()
    tree = ast.parse(Path(path).read_text())
    out: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            out |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            out.add(stmt.target.id)
    return out


def read_tables(package: str) -> OrmTables:
    """The tables of ``package``'s imported modules (see the module docstring)."""
    import sqlalchemy as sa

    found: list[tuple[str, Any, Any, str]] = []  # (model name, table, class or None, module)
    seen: set[int] = set()
    for module in package_modules(package):
        assigned: set[str] | None = None
        for name, value in list(vars(module).items()):
            if isinstance(value, type) and value.__module__ == module.__name__:
                table = getattr(value, "__table__", None)
                if isinstance(table, sa.Table) and "__table__" in vars(value):
                    if id(table) not in seen:
                        seen.add(id(table))
                        found.append((value.__name__, table, value, module.__name__))
            elif isinstance(value, sa.Table) and id(value) not in seen:
                if assigned is None:
                    assigned = _top_level_assigned(module)
                if name in assigned:
                    seen.add(id(value))
                    found.append((value.name, value, None, module.__name__))
    # association tables reachable only through a relationship's ``secondary``
    for _, _, cls, mod in list(found):
        if cls is None:
            continue
        for rel in sa.inspect(cls).relationships:
            sec = rel.secondary
            if isinstance(sec, sa.Table) and id(sec) not in seen:
                seen.add(id(sec))
                found.append((sec.name, sec, None, mod))

    by_table = {id(t): n for n, t, _, _ in found}
    tables = tuple(
        TableModel(
            name=name,
            table_name=table.name,
            module=mod,
            columns=tuple(_column(c, table) for c in table.columns),
            bases=_bases(cls, package),
        )
        for name, table, cls, mod in found
    )
    constraints = tuple(c for name, table, _, _ in found for c in _constraints(name, table))
    relationships: dict[str, dict[str, Rel]] = {}
    for name, _, cls, _ in found:
        if cls is None:
            continue
        rels = {}
        for rel in sa.inspect(cls).relationships:
            r = _rel(rel, by_table)
            if r is not None:
                rels[rel.key] = r
        relationships[name] = rels
    return OrmTables(
        tables=tables,
        constraints=constraints,
        relationships=relationships,
        classes={n: c for n, _, c, _ in found},
        sa_tables={n: t for n, t, _, _ in found},
    )


def _bases(cls: Any, package: str) -> tuple[str, ...]:
    if cls is None:
        return ()
    out = []
    for base in cls.__bases__:
        mod = getattr(base, "__module__", "")
        if not (mod == package or mod.startswith(f"{package}.")):
            continue
        if "__table__" in vars(base) or "metadata" in vars(base) or "registry" in vars(base):
            continue  # another mapped class, or the declarative base itself
        out.append(f"{mod}:{base.__name__}")
    return tuple(out)


# ---- columns ---------------------------------------------------------------------------------


def _lattice_type(t: Any) -> tuple[str | None, tuple[Any, ...], tuple[str, ...]]:
    """(type name, choices, choice names) of a SQLAlchemy column type."""
    import sqlalchemy as sa

    if isinstance(t, sa.Enum):
        cls = t.enum_class
        if cls is not None and issubclass(cls, enum.Enum):
            members = list(cls)
            values = tuple(m.value for m in members)
            names = tuple(m.name for m in members)
        else:
            values, names = tuple(t.enums), ()
        if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            return "Int", values, names
        return "Str", tuple(str(v) for v in values), names
    if isinstance(t, sa.Boolean):
        return "Bool", (), ()
    if isinstance(t, sa.Integer):
        return "Int", (), ()
    if isinstance(t, sa.Float | sa.Numeric):
        return "Float", (), ()
    if isinstance(t, sa.DateTime):
        return "DateTime", (), ()
    if isinstance(t, sa.Date):
        return "Date", (), ()
    if isinstance(t, sa.Uuid):
        return "Uuid", (), ()
    if isinstance(t, sa.String):
        return "Str", (), ()
    return None, (), ()


def _annotation(t: Any) -> str:
    import sqlalchemy as sa

    name = type(t).__name__
    if isinstance(t, sa.Enum) and t.enum_class is not None:
        return f"{name}({t.enum_class.__name__})"
    length = getattr(t, "length", None)
    return f"{name}({length})" if isinstance(length, int) else name


def _default(col: Any) -> str | None:
    d = col.default
    if d is None:
        return None
    arg = getattr(d, "arg", None)
    if getattr(d, "is_callable", False) or callable(arg):
        fn = getattr(arg, "__wrapped__", arg)
        name = getattr(fn, "__name__", None)
        return f"{name}()" if name and name != "<lambda>" else None
    if getattr(d, "is_scalar", False):
        if isinstance(arg, enum.Enum):
            return f"{type(arg).__name__}.{arg.name}"
        return repr(arg)
    return None


def _single(columns: Any) -> str | None:
    cols = list(columns)
    return cols[0].name if len(cols) == 1 else None


def _column(col: Any, table: Any) -> FieldSpec:
    import sqlalchemy as sa

    type_name, choices, names = _lattice_type(col.type)
    unique_cols = {
        _single(c.columns) for c in table.constraints if isinstance(c, sa.UniqueConstraint)
    } | {_single(i.columns) for i in table.indexes if i.unique}
    index_cols = {_single(i.columns) for i in table.indexes}
    default = _default(col)
    int_pks = [c for c in table.primary_key.columns]
    autoincrement = (
        col.primary_key
        and len(int_pks) == 1
        and type_name == "Int"
        and col.autoincrement in (True, "auto")
        and not col.foreign_keys
    )
    required = (
        not col.nullable and default is None and col.server_default is None and not autoincrement
    )
    fk = next(iter(col.foreign_keys), None)
    return FieldSpec(
        name=col.name,
        annotation=_annotation(col.type),
        type=type_name,
        nullable=bool(col.nullable),
        required=required,
        default=default,
        max_length=col.type.length if isinstance(getattr(col.type, "length", None), int) else None,
        unique=bool(col.unique) or col.name in unique_cols,
        primary_key=bool(col.primary_key),
        foreign_key=fk.target_fullname if fk is not None else None,
        index=bool(col.index) or col.name in index_cols,
        tz_aware=bool(getattr(col.type, "timezone", False)),
        choices=choices,
        choice_names=names,
    )


# ---- constraints and relationships -----------------------------------------------------------


def _constraint_name(c: Any) -> str | None:
    name = c.name
    return name if type(name) is str and name else None


def _constraints(model: str, table: Any) -> list[TableConstraint]:
    import sqlalchemy as sa

    out = []
    names = [c.name for c in table.columns]
    column_level = [c for col in table.columns for c in col.constraints]
    for c in [*table.constraints, *(c for c in column_level if c not in table.constraints)]:
        if isinstance(c, sa.CheckConstraint):
            text = str(c.sqltext)
            words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
            cols = tuple(n for n in names if n in words)
            out.append(TableConstraint(model, "check", cols, text, _constraint_name(c)))
        elif isinstance(c, sa.UniqueConstraint) and len(c.columns) > 1:
            cols = tuple(col.name for col in c.columns)
            out.append(TableConstraint(model, "unique", cols, None, _constraint_name(c)))
    for i in table.indexes:
        if i.unique and len(i.columns) > 1:
            cols = tuple(col.name for col in i.columns)
            out.append(TableConstraint(model, "unique", cols, None, _constraint_name(i)))
    return out


def _rel(rel: Any, by_table: dict[int, str]) -> Rel | None:
    target = by_table.get(id(rel.mapper.local_table))
    if target is None:
        return None
    direction = rel.direction.name
    if rel.secondary is not None:
        pairs = list(rel.synchronize_pairs)
        table = by_table.get(id(rel.secondary))
        if not pairs or table is None:
            return None
        return Rel(direction, target, table, pairs[0][1].name)
    for local, remote in rel.local_remote_pairs:
        if local.foreign_keys and id(local.table) in by_table:
            return Rel(direction, target, by_table[id(local.table)], local.name)
        if remote.foreign_keys and id(remote.table) in by_table:
            return Rel(direction, target, by_table[id(remote.table)], remote.name)
    return None
