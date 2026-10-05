"""``lattice.importer.frontends.sqlalchemy_orm`` on a plain declarative base (no Flask
extension): columns, types, Enum stored names, composite uniques, association tables,
and ``CheckConstraint`` text handed to mining (``RepoModel.constraints``)."""

from __future__ import annotations

import importlib
import sys
import textwrap

from lattice.importer.appmodel import TableConstraint
from lattice.importer.frontends import sqlalchemy_orm

MODELS = textwrap.dedent(
    """
    import enum
    from datetime import datetime

    import sqlalchemy as sa
    from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


    class Base(DeclarativeBase):
        pass


    class Status(enum.Enum):
        draft = "d"
        live = "l"


    tags = sa.Table(
        "item_tags",
        Base.metadata,
        sa.Column("item_id", sa.ForeignKey("items.id"), primary_key=True),
        sa.Column("tag", sa.String(20), primary_key=True),
    )


    class Owner(Base):
        __tablename__ = "owners"
        id: Mapped[int] = mapped_column(primary_key=True)
        email: Mapped[str] = mapped_column(sa.String(120), unique=True)


    class Item(Base):
        __tablename__ = "items"
        __table_args__ = (
            sa.CheckConstraint("price >= 0", name="price_nonneg"),
            sa.UniqueConstraint("owner_id", "title"),
        )
        id: Mapped[int] = mapped_column(primary_key=True)
        title: Mapped[str] = mapped_column(sa.String(80))
        price: Mapped[float] = mapped_column(sa.Numeric(10, 2), sa.CheckConstraint("price < 1e6"))
        status: Mapped[Status] = mapped_column(sa.Enum(Status), default=Status.draft)
        created: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
        owner_id: Mapped[int] = mapped_column(sa.ForeignKey("owners.id"))
        owner: Mapped[Owner] = relationship()
    """
)


def _package(tmp_path, monkeypatch, name="sa_plain_pkg"):
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "models.py").write_text(MODELS)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    module = importlib.import_module(f"{name}.models")
    return name, module


def test_plain_declarative_base(tmp_path, monkeypatch):
    name, _ = _package(tmp_path, monkeypatch)
    try:
        read = sqlalchemy_orm.read_tables(name)
    finally:
        for mod in [m for m in sys.modules if m == name or m.startswith(f"{name}.")]:
            del sys.modules[mod]
    assert {t.name: t.table_name for t in read.tables} == {
        "Owner": "owners", "Item": "items", "item_tags": "item_tags",
    }  # fmt: skip
    item = next(t for t in read.tables if t.name == "Item")
    cols = {c.name: c for c in item.columns}
    assert (cols["title"].type, cols["title"].max_length) == ("Str", 80)
    assert cols["price"].type == "Float"
    assert (cols["status"].type, cols["status"].choices, cols["status"].choice_names) == (
        "Str", ("d", "l"), ("draft", "live"),
    )  # fmt: skip
    assert (cols["status"].required, cols["status"].default) == (False, "Status.draft")
    assert (cols["created"].type, cols["created"].tz_aware) == ("DateTime", True)
    assert cols["owner_id"].foreign_key == "owners.id"
    assert item.module == f"{name}.models"
    assert read.tables[0].module == f"{name}.models"
    owner = next(t for t in read.tables if t.name == "Owner")
    assert next(c for c in owner.columns if c.name == "email").unique
    assert set(read.constraints) == {
        TableConstraint("Item", "check", ("price",), "price >= 0", "price_nonneg"),
        TableConstraint("Item", "check", ("price",), "price < 1e6", None),
        TableConstraint("Item", "unique", ("owner_id", "title"), None, None),
    }
    rel = read.relationships["Item"]["owner"]
    assert (rel.direction, rel.target, rel.table, rel.column) == (
        "MANYTOONE", "Owner", "Item", "owner_id",
    )  # fmt: skip
