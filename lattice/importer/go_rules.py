"""The rules-first import for a Go (Gin + GORM) app (Spike J, node J1): what
:func:`lattice.importer.rules.import_rules` needs beyond the Python path.

* :func:`harness_factory` (D1): the app factory of a Go module comes from the module's
  ``studio.toml`` ``[app] app`` (read-only reuse of ``go_gin._harness_attr``), not from a
  Python ``make_app``.
* :func:`infer_owners` (D2): GORM has no association tag the profile generator could read an
  owner from, but the extract records ``owner_uses`` (a column a route compares with, or sets
  from, the caller). A column ``c`` of table ``T`` is ``T``'s owner when all four hold:

  1. some route's ``owner_uses`` has ``(T, c, compare)`` or ``(T, c, assign)``;
  2. ``c`` is an ``Int`` column and so is the users table's primary key;
  3. ``T`` is not the users table (and has no users foreign key already);
  4. no other column of ``T`` meets 1 and 2.

  The model copy gives ``c`` an **inferred** users foreign key (``<users>.<pk>``), recorded in
  ``inferences[]`` as ``{"table", "column", "evidence": ["code:<file>:<line>", ...], "rule":
  "owner_uses"}``. Two or more candidates make ``T`` ``unclear`` with both columns named (each
  gets the foreign key: the profile generator then reports two users foreign keys). A table
  with no evidence keeps today's answer. The extract itself (the sidecar, the inventory,
  ``workflows.json``) is never touched: the inference is in memory only.
* :class:`InferringFrontEnd`: the Go front end with D2 applied to the model it extracts, so
  ``lift_repo`` (which extracts again) sees the inferred keys without an edit to it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lattice.importer.appmodel import FieldSpec, RepoModel, TableModel

__all__ = [
    "InferringFrontEnd",
    "harness_factory",
    "infer_owners",
]

RULE = "owner_uses"
OWNER_HOWS = ("compare", "assign")


def harness_factory(root: Path | str, package: str) -> tuple[Callable[[Path], Any], str] | None:
    """The app factory ``fn(db_path) -> app`` and its ``module:function`` name from the
    ``studio.toml`` ``[app] app`` of the Go module ``<root>/<package>``; None when the module
    has no such entry (the caller then looks for a Python ``make_app``, as before)."""
    from lattice.importer.frontends.go_gin import _harness_attr, _studio_app

    module_dir = Path(root) / package
    spec = _studio_app(module_dir).get("app")
    if not spec or ":" not in spec:
        return None
    factory = _harness_attr(module_dir, spec)
    if not callable(factory):
        return None
    return factory, spec


def _users_pk(model: RepoModel, users: str) -> FieldSpec | None:
    table = next((t for t in model.tables if t.name == users), None)
    if table is None:
        return None
    return next((c for c in table.columns if c.primary_key), None)


def infer_owners(
    extract: Mapping[str, Any], model: RepoModel
) -> tuple[RepoModel, list[dict[str, Any]]]:
    """``model`` with D2's inferred users foreign keys, and the ``inferences`` (see the module
    docstring). ``extract`` is the ``lattice.goextract/1`` document ``model`` was built from."""
    auth = model.auth
    users = auth.users_table if auth is not None else None
    pk = _users_pk(model, users) if users else None
    if users is None or pk is None or pk.type != "Int":
        return model, []
    users_sql = next(t.table_name for t in model.tables if t.name == users)
    uses: dict[tuple[str, str], set[tuple[str, int]]] = {}
    for route in extract.get("routes", ()):
        for o in route.get("owner_uses", ()):
            if o.get("how") in OWNER_HOWS:
                uses.setdefault((o["model"], o["column"]), set()).add((o["file"], o["line"]))
    inferences: list[dict[str, Any]] = []
    tables: list[TableModel] = []
    for t in model.tables:
        declared = any((c.foreign_key or "").split(".")[0] == users_sql for c in t.columns)
        found = [
            c for c in t.columns
            if t.name != users and not declared and c.type == "Int"
            and (t.name, c.name) in uses
        ]  # fmt: skip
        if not found:
            tables.append(t)
            continue
        evidence = {
            c.name: [f"code:{f}:{n}" for f, n in sorted(uses[(t.name, c.name)])] for c in found
        }
        if len(found) == 1:
            c = found[0]
            inferences.append({"table": t.name, "column": c.name, "evidence": evidence[c.name],
                               "rule": RULE})  # fmt: skip
        else:
            inferences.append({
                "table": t.name,
                "column": None,
                "columns": [c.name for c in found],
                "evidence": [e for c in found for e in evidence[c.name]],
                "rule": RULE,
                "unclear": True,
            })  # fmt: skip
        names = {c.name for c in found}
        fk = f"{users_sql}.{pk.name}"
        cols = tuple(
            dataclasses.replace(c, foreign_key=fk) if c.name in names else c for c in t.columns
        )
        tables.append(dataclasses.replace(t, columns=cols))
    if not inferences:
        return model, []
    return dataclasses.replace(model, tables=tuple(tables)), inferences


class InferringFrontEnd:
    """The Go front end with D2 applied to every model it extracts. Everything else is the
    wrapped front end's (``name``, ``runtime``, ``last_extract``, ``sidecar``...)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.inferences: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def extract(self, root: Path, package: str = ".") -> RepoModel:
        model = self._inner.extract(root, package)
        data = self._inner.last_extract
        if data is None:
            return model
        model, self.inferences = infer_owners(data, model)
        return model
