"""Layer A's policy for an ASP.NET Core app, derived from the app (Spike J, node B3): the .NET counterpart of
:mod:`lattice.gocheck.derive`.

:func:`derive_dn_policy` reads the project with ``dncheck extract`` (policy mode adds each column's ``json`` name) and builds
a :class:`~lattice.dncheck.policy.DnPolicy`:

* ``tables``: entity name -> SQL name; ``users_table``: the extract's auth users entity;
* ``owners``: the D2 rule of ``go_rules.infer_owners`` on ``owner_uses`` (N8): a table other than the users table, an
  Int column (as the users primary key) the code compares with, or sets from, the caller or a global query filter
  (``how`` compare/assign/filter, directly, not through a parent), exactly one such column. Unlike Go's, a column that
  already has a declared foreign key to the users table counts (EF Core declares one by convention); ``ownership``
  ({entity: column}) replaces the rule;
* ``owner_via``: a table whose owner evidence is a comparison through a navigation to an owned parent
  (``owner_uses[].via``), or that has exactly one foreign key to an owned table, as ``(fk column, parent entity)``
  (GoPolicy's order);
* ``private``: the columns the serializer skips (``[JsonIgnore]``, or what no response reads: extract ``json == "-"``, see
  ``Projection.cs``). Never guessed from a name;
* ``admin_routes``: the routes the extract marks ``Admin``;
* ``machines``: **empty**. Edges derived from the app's own writes would make every write an edge (DN808 could not
  fire), and a machine is a decision, not a fact of the code (as in Go);
* ``baseline``: the keys of ``run_dncheck(project, policy)`` -- what the original already does is accepted.

:func:`dn_spec_policy`: the derived policy with an empty baseline, laid under the spec by ``go_spec_policy``'s algorithm
(K6's table identity by SQL name; the spec's owners, private columns, machines, admin and open routes win).
"""

# ruff: noqa: E501
from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lattice.dncheck import DnCheckError, extract_json
from lattice.dncheck.policy import DnPolicy, run_dncheck

if TYPE_CHECKING:
    from lattice.spec.model import Spec

__all__ = ["derive_dn_policy", "dn_spec_policy", "policy_extract", "undeclared_tables"]

OWNER_HOWS = ("compare", "assign", "filter")


def policy_extract(project_dir: Path | str) -> dict[str, Any]:
    """``dncheck extract`` with the opt-in policy facts (``columns[].json``). Read-only on the project."""
    return extract_json(Path(project_dir), policy=True)


def _owners(extract: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    tables = {t["struct"]: t for t in extract["tables"]}
    users = (extract.get("auth") or {}).get("users_table")
    pk = (
        next((c for c in tables[users]["columns"] if c.get("primary_key")), None)
        if users in tables
        else None
    )
    if pk is None or pk.get("type") != "Int":
        return {}, {}
    used: dict[str, set[str]] = {}
    via: dict[str, tuple[str, str]] = {}
    for route in extract["routes"]:
        for o in route.get("owner_uses", ()):
            if o.get("how") not in OWNER_HOWS or o["model"] not in tables or o["model"] == users:
                continue
            link = o.get("via") or {}
            if link.get("parent"):
                via.setdefault(o["model"], (link["fk"], link["parent"]))
            else:
                used.setdefault(o["model"], set()).add(o["column"])
    owners = {}
    for model, cols in used.items():
        found = [
            c["name"]
            for c in tables[model]["columns"]
            if c["name"] in cols and c.get("type") == "Int"
        ]
        if len(found) == 1:
            owners[model] = found[0]
    by_sql = {t["table"].lower(): t["struct"] for t in extract["tables"]}
    for (
        model,
        t,
    ) in tables.items():  # a child table with one foreign key to an owned table is owned through it
        fks = {
            (c["name"], by_sql.get((c.get("foreign_key") or "").split(".")[0].lower()))
            for c in t["columns"]
        }
        parents = [(c, p) for c, p in fks if p in owners]
        if model not in owners and model not in via and len(parents) == 1:
            via[model] = parents[0]
    return owners, {t: v for t, v in via.items() if v[1] in owners and t not in owners}


def derive_dn_policy(
    project_dir: Path | str,
    extract: Mapping[str, Any] | None = None,
    ownership: Mapping[str, str] | None = None,
) -> DnPolicy:
    """The policy derived from the project at ``project_dir`` (see the module docstring)."""
    project_dir = Path(project_dir).resolve()
    if extract is None or not all(
        "json" in c for t in extract.get("tables", ()) for c in t["columns"]
    ):
        extract = policy_extract(project_dir)
    tables = {t["struct"]: t["table"] for t in extract["tables"]}
    owners, via = _owners(extract)
    if ownership is not None:
        owners, via = dict(ownership), {}
    private = {}
    for t in extract["tables"]:
        hidden = [
            c["name"]
            for c in t["columns"]
            if c.get("json") == "-" or (c.get("private") and not c.get("primary_key"))
        ]
        if hidden:
            private[t["struct"]] = tuple(sorted(hidden))
    admin = [
        (r["method"], r["path_template"]) for r in extract["routes"] if r.get("auth") == "Admin"
    ]
    policy = DnPolicy(
        tables=tables,
        users_table=(extract.get("auth") or {}).get("users_table"),
        owners={t: c for t, c in owners.items() if t in tables},
        owner_via={t: v for t, v in via.items() if t in tables},
        private=private,
        admin_routes=tuple(dict.fromkeys(admin)),
    )
    return dataclasses.replace(
        policy, baseline=frozenset(f.key for f in run_dncheck(project_dir, policy))
    )


def undeclared_tables(
    spec: Spec, project_dir: Path | str, policy: DnPolicy | None = None
) -> list[str]:
    """The derived tables :func:`dn_spec_policy` drops: undeclared by the spec, with no fact."""
    from lattice.gocheck.derive import _identity

    base = policy if policy is not None else derive_dn_policy(project_dir)
    return _identity(base, DnPolicy.from_spec(spec))[1]


def dn_spec_policy(spec: Spec, project_dir: Path | str, policy: DnPolicy | None = None) -> DnPolicy:
    """The derived policy (``policy`` when given) with an empty baseline, under the spec's declarations."""
    from lattice.gocheck.derive import go_spec_policy

    base = policy if policy is not None else derive_dn_policy(project_dir)
    if not isinstance(base, DnPolicy):
        raise DnCheckError("dn_spec_policy needs a DnPolicy")
    return go_spec_policy(spec, project_dir, policy=base)  # type: ignore[return-value]
