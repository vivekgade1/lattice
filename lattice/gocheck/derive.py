"""Layer A's policy for a Go app, derived from the app (Spike J, node J1; D3): the Go
counterpart of :func:`lattice.pycheck.derive_policy`, without the Python-only parts.

:func:`derive_go_policy` reads the module with ``gocheck extract`` (the opt-in *policy*
extract adds each column's ``json`` name) and builds a :class:`~lattice.gocheck.GoPolicy`:

* ``tables``: schema (struct) name -> SQL name, from the extract; ``users_table``: the
  extract's auth users table (a struct name, as the checker compares schema names);
* ``owners`` / ``owner_via``: the importer's D2 inference (``go_rules.infer_owners``) or the
  ``ownership`` given ({table: column});
* ``private``: the columns whose ``json`` is ``"-"``. Never guessed from a name (a column
  called ``password`` that the struct serializes is not private by that name alone);
* ``admin_routes``: the routes the extract marks ``Admin``;
* ``machines``: **empty**. The original's own writes are not a spec, and a machine is a
  decision, not a fact of the code;
* ``baseline``: the keys of ``run_gocheck(module, policy)`` -- what the original already does
  is accepted, as pycheck's derived baseline does.

:func:`go_spec_policy` (pycheck has no counterpart): the derived policy with an
**empty** baseline, laid under the spec -- for each table the spec declares, the spec's
owner, ``owner_via``, private columns and machine win (a declared table with no owner is not
owned); the spec's admin and open routes are added. Round 3 (K6): tables are matched by SQL name
(a derived struct whose table is a declared table's *is* that table), and a derived table the
spec does not declare and that carries no owner, private column, machine or users role is
dropped, listed by :func:`undeclared_tables`. ``lattice check`` on Go keeps
``GoPolicy.from_spec`` by default (Ruling Q3): a caller passes ``policy=go_spec_policy(...)``.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lattice.gocheck import (
    GoCheckError,
    GoPolicy,
    build_gocheck,
    run_gocheck,
)

if TYPE_CHECKING:
    from lattice.spec.model import Spec

__all__ = ["derive_go_policy", "go_spec_policy", "policy_extract", "undeclared_tables"]

EXTRACT_TIMEOUT_S = 120


def policy_extract(module_dir: Path | str) -> dict[str, Any]:
    """``gocheck extract`` with the opt-in policy facts: the default document plus
    ``tables[].columns[].json``. Read-only on the module."""
    root = Path(module_dir).resolve()
    if not root.is_dir():
        raise GoCheckError(f"no Go module directory at {root}")
    binary = build_gocheck()
    try:
        proc = subprocess.run(  # noqa: S603 -- the project's own checker binary
            [str(binary), "extract"],
            input=json.dumps({"root": str(root), "policy": True}),
            capture_output=True,
            text=True,
            check=False,
            timeout=EXTRACT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as err:
        raise GoCheckError(f"gocheck extract timed out after {EXTRACT_TIMEOUT_S}s") from err
    if proc.returncode != 0:
        raise GoCheckError(
            f"gocheck extract failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    try:
        return json.loads(proc.stdout)  # type: ignore[no-any-return]
    except ValueError as err:
        raise GoCheckError(f"gocheck extract answered invalid JSON: {err}") from err


def _owners(
    module_dir: Path, extract: Mapping[str, Any], ownership: Mapping[str, str] | None
) -> dict[str, str]:
    """{struct: SQL owner column}: ``ownership`` (struct -> column) when given, else D2."""
    if ownership is not None:
        return dict(ownership)
    from lattice.importer.frontends.go_gin import model_from_extract
    from lattice.importer.go_rules import infer_owners

    model = model_from_extract(extract)
    model, inferences = infer_owners(extract, model)
    return {i["table"]: i["column"] for i in inferences if i.get("column")}


def derive_go_policy(
    module_dir: Path | str,
    extract: Mapping[str, Any] | None = None,
    ownership: Mapping[str, str] | None = None,
) -> GoPolicy:
    """The policy derived from the Go module at ``module_dir`` (see the module docstring)."""
    module_dir = Path(module_dir).resolve()
    if extract is None or not _has_json_facts(extract):
        extract = policy_extract(module_dir)
    tables = {t["struct"]: t["table"] for t in extract["tables"]}
    auth = extract.get("auth") or {}
    users = auth.get("users_table")
    private: dict[str, tuple[str, ...]] = {}
    for t in extract["tables"]:
        hidden = [c["name"] for c in t["columns"] if c.get("json") == "-"]
        if hidden:
            private[t["struct"]] = tuple(sorted(hidden))
    admin = [
        (r["method"], r["path_template"]) for r in extract["routes"] if r.get("auth") == "Admin"
    ]
    owners = _owners(module_dir, extract, ownership)
    policy = GoPolicy(
        tables=tables,
        users_table=users,
        owners={t: c for t, c in owners.items() if t in tables},
        private=private,
        admin_routes=tuple(dict.fromkeys(admin)),
    )
    found = run_gocheck(module_dir, policy)
    return dataclasses.replace(policy, baseline=frozenset(f.key for f in found))


def _has_json_facts(extract: Mapping[str, Any]) -> bool:
    cols = [c for t in extract.get("tables", ()) for c in t["columns"]]
    return all("json" in c for c in cols)


def _identity(base: GoPolicy, stated: GoPolicy) -> tuple[set[str], list[str]]:
    """K6: (the derived tables that are declared tables under another schema name, the derived
    tables no spec table covers and no derived fact keeps alive)."""
    declared_sql = set(stated.tables.values())
    same = {t for t, sql in base.tables.items() if t not in stated.tables and sql in declared_sql}
    users = stated.users_table or base.users_table
    facts = (base.owners, base.owner_via, base.private, base.machines)
    bare = (t for t in base.tables if t not in stated.tables and t not in same and t != users)
    dropped = sorted(t for t in bare if not any(t in f for f in facts))
    return same, dropped


def undeclared_tables(
    spec: Spec, module_dir: Path | str, policy: GoPolicy | None = None
) -> list[str]:
    """The derived tables :func:`go_spec_policy` drops: undeclared by the spec, with no fact."""
    base = policy if policy is not None else derive_go_policy(module_dir)
    return _identity(base, GoPolicy.from_spec(spec))[1]


def go_spec_policy(spec: Spec, module_dir: Path | str, policy: GoPolicy | None = None) -> GoPolicy:
    """The derived policy (``policy`` when given, else :func:`derive_go_policy`) with an empty
    baseline, under the spec's declarations (see the module docstring)."""
    base = policy if policy is not None else derive_go_policy(module_dir)
    stated = GoPolicy.from_spec(spec)
    same, dropped = _identity(base, stated)
    declared = set(stated.tables) | same
    owners = {t: c for t, c in base.owners.items() if t not in declared}
    owners.update(stated.owners)
    via = {t: v for t, v in base.owner_via.items() if t not in declared}
    via.update(stated.owner_via)
    private = {t: c for t, c in base.private.items() if t not in declared}
    private.update(stated.private)
    machines = {t: m for t, m in base.machines.items() if t not in declared}
    machines.update(stated.machines)
    tables = {t: s for t, s in base.tables.items() if t not in same and t not in dropped}
    tables.update(stated.tables)
    admin = tuple(dict.fromkeys((*base.admin_routes, *stated.admin_routes)))
    return dataclasses.replace(
        base,
        tables=tables,
        users_table=stated.users_table or base.users_table,
        owners=owners,
        owner_via=via,
        private=private,
        machines=machines,
        admin_routes=admin,
        open_routes=stated.open_routes,
        admin_role=stated.admin_role,
        baseline=frozenset(),
    )
