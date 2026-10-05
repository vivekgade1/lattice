"""The Django ORM half of the Django + DRF front end (Phase 2 plan §2.2, W2-A): booting the
app's Django project and reading its tables from the **live** model registry
(``apps.get_models()`` and each model's ``_meta``), as :class:`TableModel`\\ s.

* **Which tables.** The app's own models (defined under the package), the users model
  (``AUTH_USER_MODEL``, wherever it is defined: ``django.contrib.auth``'s ``User`` too),
  and the many-to-many link tables between them (auto-created ``through`` models, named
  like Django names them: ``Recipe_tags``). Every other table -- Django's and DRF's
  (``auth_permission``, ``django_session``, ``authtoken_token``...), and the users
  model's links to them (``auth_user_groups``) -- is excluded and listed with a reason
  (:class:`~lattice.importer.appmodel.Excluded`), plus ``django_migrations``.
* **Columns** (``_meta.local_concrete_fields``, in declaration order; the implicit key
  first): the SQL column name (``owner_id`` for a foreign key ``owner``), a Lattice type
  read from the field class (``EmailField`` -> Email, ``DecimalField`` -> Float,
  ``FileField`` -> Str (the stored path), a foreign key -> its target's type; None when
  there is no reading: ``JSONField``...), ``null``, required (no default, not null, not
  auto-filled), the default's text, ``max_length``, the validators' bounds
  (``Min``/``MaxLengthValidator``, ``Min``/``MaxValueValidator``; ``Positive*Field``:
  ``ge=0``), ``choices`` (stored values), ``unique``, ``db_index``, the foreign key as
  ``table.column``, and ``tz_aware`` for a ``DateTimeField`` under ``USE_TZ``.
* **Table keys and checks.** ``unique_together`` and unconditional ``UniqueConstraint``\\ s
  as column tuples; ``CheckConstraint``\\ s (and conditional unique constraints) as text,
  for invariant mining.

Django's settings, app registry and connections are process-global: :func:`boot` refuses
a second project in one process. Booting reads no database (the settings' own may be a
placeholder) and sends no mail.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Any

from lattice.importer.appmodel import Excluded, FieldSpec, TableModel
from lattice.importer.frontends.pyindex import _Index, _lit

# tables Django keeps outside the model registry
UNREGISTERED_FRAMEWORK_TABLES = (("django_migrations", "Django's migration recorder"),)
SETTINGS_NAMES = ("lattice_settings", "settings")  # module names tried for the settings
SETTINGS_ATTR = "SETTINGS_MODULE"  # a package __init__ naming its settings module

INT_FIELDS = (
    "AutoField", "BigAutoField", "SmallAutoField", "IntegerField", "BigIntegerField",
    "SmallIntegerField", "PositiveIntegerField", "PositiveBigIntegerField",
    "PositiveSmallIntegerField",
)  # fmt: skip
TYPES = {
    **{name: "Int" for name in INT_FIELDS},
    "CharField": "Str",
    "TextField": "Str",
    "SlugField": "Str",
    "URLField": "Str",
    "FilePathField": "Str",
    "GenericIPAddressField": "Str",
    "IPAddressField": "Str",
    "FileField": "Str",
    "ImageField": "Str",
    "EmailField": "Email",
    "BooleanField": "Bool",
    "NullBooleanField": "Bool",
    "DateTimeField": "DateTime",
    "DateField": "Date",
    "FloatField": "Float",
    "DecimalField": "Float",
    "UUIDField": "Uuid",
}


# ---------------------------------------------------------------------------- booting --


def settings_module(root: Path, package: str) -> str:
    """The package's Django settings module: ``DJANGO_SETTINGS_MODULE`` when it names a
    module of the package; else the ``SETTINGS_MODULE`` the package's ``__init__``
    assigns (the vendored apps' convention); else a ``lattice_settings`` / ``settings``
    module of the package; else the one module assigning both ``INSTALLED_APPS`` and
    ``ROOT_URLCONF``. A LookupError when there is none."""
    index = _Index(Path(root), package)
    env = os.environ.get("DJANGO_SETTINGS_MODULE")
    if env and env in index.modules:
        return env
    init = index.modules.get(package)
    named = _lit(init.defs.get(SETTINGS_ATTR)) if init is not None else None  # type: ignore[arg-type]
    if isinstance(named, str) and named:
        return named
    for name in SETTINGS_NAMES:
        if f"{package}.{name}" in index.modules:
            return f"{package}.{name}"
    found = [
        m.name
        for m in index.modules.values()
        if {"INSTALLED_APPS", "ROOT_URLCONF"} <= _assigned(m.tree)
    ]
    if len(found) == 1:
        return found[0]
    raise LookupError(f"no Django settings module found in {package!r} ({found or 'none'})")


def _assigned(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            out |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
    return out


def boot(root: Path, package: str) -> str:
    """Set Django up for the package's settings (idempotent for the same project); the
    settings module's name. A RuntimeError when this process already runs another
    Django project."""
    import django
    from django.apps import apps
    from django.conf import settings

    module = settings_module(root, package)
    root_text = str(Path(root).resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    if apps.ready or settings.configured:
        if getattr(settings, "SETTINGS_MODULE", None) != module:
            raise RuntimeError(
                f"this process already runs Django project "
                f"{getattr(settings, 'SETTINGS_MODULE', None)!r}; {module!r} needs its own "
                "process"
            )
        if not apps.ready:
            django.setup()
        return module
    os.environ["DJANGO_SETTINGS_MODULE"] = module
    django.setup()
    return module


# ----------------------------------------------------------------------------- tables --


def in_package(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def tables(package: str) -> tuple[tuple[TableModel, ...], tuple[Excluded, ...]]:
    """The app's tables and the excluded ones (Django must be booted)."""
    from django.apps import apps
    from django.contrib.auth import get_user_model

    users = get_user_model()
    models = apps.get_models(include_auto_created=True)
    own = [m for m in models if not m._meta.auto_created and (
        m is users or in_package(m.__module__, package))]  # fmt: skip
    kept = set(own)
    links = []
    for m in models:
        if not m._meta.auto_created:
            continue
        owner = m._meta.auto_created
        others = {f.related_model for f in m._meta.local_concrete_fields if f.is_relation}
        if owner in kept and others <= kept:
            links.append(m)
    out = [table(m, package) for m in own] + [table(m, package) for m in links]
    excluded: dict[str, Excluded] = {}
    for m in models:
        if m not in kept and m not in links and m._meta.db_table not in excluded:
            excluded[m._meta.db_table] = Excluded("table", m._meta.db_table, _why_excluded(m, kept))
    for name, why in UNREGISTERED_FRAMEWORK_TABLES:
        excluded.setdefault(name, Excluded("table", name, why))
    return tuple(out), tuple(excluded.values())


def _why_excluded(model: Any, kept: set[Any]) -> str:
    meta = model._meta
    if meta.auto_created:
        others = sorted(
            f.related_model._meta.label
            for f in meta.local_concrete_fields
            if f.is_relation
            and f.related_model not in kept
            and f.related_model is not meta.auto_created
        )
        return (
            f"framework table: link table of {meta.auto_created._meta.label} to {', '.join(others)}"
        )
    return f"framework table: model {meta.label} ({model.__module__}) is not the app's"


def table(model: Any, package: str) -> TableModel:
    meta = model._meta
    fields = list(meta.local_concrete_fields)
    columns = tuple(column(f) for f in fields)
    by_name = {f.name: f.column for f in fields}
    by_name.update({f.attname: f.column for f in fields})
    unique: list[tuple[str, ...]] = [
        tuple(by_name.get(n, n) for n in group) for group in meta.unique_together
    ]
    checks: list[str] = []
    from django.db.models import CheckConstraint, UniqueConstraint

    for c in meta.constraints:
        if isinstance(c, UniqueConstraint) and c.fields and c.condition is None:
            unique.append(tuple(by_name.get(n, n) for n in c.fields))
        elif isinstance(c, UniqueConstraint):
            checks.append(f"unique({', '.join(c.fields or ())}) where {c.condition}")
        elif isinstance(c, CheckConstraint):
            cond = getattr(c, "condition", None) or getattr(c, "check", None)
            checks.append(f"{c.name}: {cond}")
    if meta.auto_created and not unique:  # an auto-created link table: unique pair
        rel = tuple(f.column for f in fields if f.is_relation)
        if len(rel) == 2:
            unique.append(rel)
    bases = tuple(
        f"{b.__module__}:{b.__qualname__}"
        for b in model.__bases__
        if in_package(b.__module__, package)
    )
    return TableModel(
        name=model.__name__,
        table_name=meta.db_table,
        module=(meta.auto_created or model).__module__,
        columns=columns,
        bases=bases,
        unique_together=tuple(dict.fromkeys(unique)),
        through=_through(model),
        checks=tuple(checks),
    )


def _through(model: Any) -> str | None:
    """``Owner.field`` when ``model`` is the through table of a many-to-many field."""
    from django.apps import apps

    for m in apps.get_models():
        for f in m._meta.local_many_to_many:
            if f.remote_field.through is model:
                return f"{m.__name__}.{f.name}"
    return None


def field_type(field: Any) -> str | None:
    """The Lattice type of a model field (a relation: its target's)."""
    if field.is_relation and (field.many_to_one or field.one_to_one):
        return field_type(field.target_field)
    for klass in type(field).__mro__:
        if klass.__name__ in TYPES and klass.__module__.startswith("django.db.models"):
            return TYPES[klass.__name__]
    return None


def column(field: Any) -> FieldSpec:
    from django.conf import settings
    from django.core import validators as v
    from django.db import models

    auto = isinstance(field, models.AutoField) or field.auto_created and field.primary_key
    filled = auto or getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False)
    default = None
    if field.has_default():
        d = field.default
        default = f"{d.__module__}.{d.__qualname__}" if callable(d) else repr(d)
    bounds: dict[str, Any] = {}
    for val in field._validators:
        if isinstance(val, v.MinLengthValidator):
            bounds["min_length"] = val.limit_value
        elif isinstance(val, v.MaxLengthValidator):
            bounds["max_length"] = val.limit_value
        elif isinstance(val, v.MinValueValidator):
            bounds["ge"] = val.limit_value
        elif isinstance(val, v.MaxValueValidator):
            bounds["le"] = val.limit_value
    if type(field).__name__.startswith("Positive") and "ge" not in bounds:
        bounds["ge"] = 0
    max_length = getattr(field, "max_length", None)
    if max_length is not None and not isinstance(field, models.FileField):
        bounds.setdefault("max_length", max_length)
    choices: tuple[Any, ...] = ()
    if field.choices:
        choices = tuple(value for value, _ in field.flatchoices)
    fk = None
    if field.is_relation and (field.many_to_one or field.one_to_one):
        target = field.target_field
        fk = f"{target.model._meta.db_table}.{target.column}"
    typ = field_type(field)
    return FieldSpec(
        name=field.column,
        annotation=type(field).__name__,
        type=typ,
        nullable=bool(field.null),
        required=not (field.has_default() or field.null or filled),
        default=default,
        unique=bool(field.unique and not field.primary_key),
        primary_key=bool(field.primary_key),
        foreign_key=fk,
        index=bool(field.db_index),
        tz_aware=typ == "DateTime" and bool(getattr(settings, "USE_TZ", False)),
        choices=choices,
        **bounds,
    )


def users_model() -> Any:
    from django.contrib.auth import get_user_model

    return get_user_model()


def dialect() -> str | None:
    """The default database's dialect, from the (booted) settings' ENGINE."""
    from django.conf import settings

    engine = settings.DATABASES.get("default", {}).get("ENGINE", "")
    name = engine.rsplit(".", 1)[-1]
    return {"sqlite3": "sqlite", "postgresql": "postgresql", "postgresql_psycopg2": "postgresql",
            "mysql": "mysql", "oracle": "oracle"}.get(name)  # fmt: skip


# ------------------------------------------------------------------- runtime: seeding --


def seed_hook(tables_: Any) -> Any:
    """An ``insert_hook`` writing the seed through the app's own Django models: the schema
    by ``migrate`` on the file, then every table's rows by ``bulk_create`` (rows keyed by
    SQL column, which is each field's ``attname``). The connection is closed after."""
    from django.apps import apps

    from lattice.importer.suite import ColumnSpec, TableSpec, fk_order

    by_table = {m._meta.db_table: m for m in apps.get_models(include_auto_created=True)}
    names = [t.table_name for t in tables_]
    if any(n not in by_table for n in names):
        return None
    # foreign-key order (a custom users model -- AUTH_USER_MODEL -- of an app listed after
    # the apps referencing it goes first); a cycle broken at a nullable foreign key,
    # whose values are set once every row is in
    specs = []
    for name in names:
        cols = []
        for f in by_table[name]._meta.concrete_fields:
            fk = None
            if f.is_relation and (f.many_to_one or f.one_to_one):
                fk = f"{f.target_field.model._meta.db_table}.{f.target_field.column}"
            cols.append(ColumnSpec(f.column, "str", nullable=bool(f.null),
                                   primary_key=bool(f.primary_key), foreign_key=fk))  # fmt: skip
        specs.append(TableSpec(name, tuple(cols)))
    order = fk_order(specs)
    notes: list[str] = []
    if order.cycle:
        notes.append(f"seed: a foreign-key cycle among {', '.join(order.cycle)} with no "
                     "nullable foreign key to break it: those tables are inserted with "
                     "the foreign-key checks off")  # fmt: skip

    def hook(path: Path, plan: Any) -> None:
        from django.db import connections

        use_database(path)
        later: list[tuple[Any, Any, str, Any]] = []  # (model, key, attname, value)
        deferred = set(order.deferred)
        loose = set(order.cycle or ())
        try:
            for name in order.order:
                model = by_table[name]
                attnames = {f.column: f.attname for f in model._meta.concrete_fields}
                pk = model._meta.pk.attname
                objs = []
                for row in plan.rows.get(name, []):
                    values = {attnames.get(k, k): val for k, val in row.items()}
                    for table, col in deferred:
                        att = attnames.get(col, col)
                        if table == name and values.get(att) is not None:
                            later.append((model, values.get(pk), att, values[att]))
                            values[att] = None
                    objs.append(model(**values))
                if name in loose:
                    conn = connections["default"]
                    with conn.constraint_checks_disabled():
                        model.objects.bulk_create(objs)
                else:
                    model.objects.bulk_create(objs)
            for model, key, att, value in later:
                model.objects.filter(pk=key).update(**{att: value})
        finally:
            connections.close_all()

    hook.notes = notes  # type: ignore[attr-defined]
    return hook


def use_database(path: Path | str) -> Path:
    """Point the default connection at the SQLite file ``path``, migrating it when it has
    no schema yet."""
    import sqlite3

    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections

    path = Path(path).resolve()
    connections.close_all()
    settings.DATABASES["default"]["NAME"] = str(path)
    connections["default"].settings_dict["NAME"] = str(path)
    has_schema = False
    if path.exists() and path.stat().st_size:
        con = sqlite3.connect(path)
        try:
            has_schema = (
                con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='django_migrations'"
                ).fetchone()
                is not None
            )
        finally:
            con.close()
    if not has_schema:
        call_command("migrate", run_syncdb=True, interactive=False, verbosity=0)
        connections.close_all()
    return path


def dispose(app: Any = None) -> None:
    """Close every Django connection (before the database file is read or copied)."""
    from django.db import connections

    connections.close_all()
