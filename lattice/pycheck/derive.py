"""Derive an app's :class:`~lattice.pycheck.policy.Policy` from its original code.

Once per app, before any edit:

* **owners, owner_via** -- given by the caller (``ownership``: e.g. the owners a Lattice
  profile declares, so a checker and a Lattice arm state the same facts), or else the
  importer's own inference (below).
* **private** (and owners when not given) -- the importer's own inference
  (:mod:`lattice.importer.profile_gen`: a table's owner is its one users foreign key that
  routes check or set from the caller; private columns are those no response model of
  the table returns). A table the importer leaves *unclear* has no owner here either, so
  pycheck never guesses one. A non-owned table whose foreign keys point at owned tables
  is a *link* table (``NoteTagLink``). Every other table but the users table is
  *shared*: its rows belong to no one.
* private columns the original itself puts in a response dict are dropped: the app
  treats them as public (JustNotes returns ``owner_id`` in every note dict).
* **admin** -- the superuser dependency, its flag column and its answer.
* **not_owned** -- the original's answer to another user's row
  (:func:`lattice.pycheck.checks.derive_not_owned`).
* **examples** -- per owned table, a route of the original that scopes it to the caller
  (named in hints).
* **baseline** -- the keys of every finding on the original: the app's accepted
  conventions (the template's unbounded ``limit``, JustNotes' tag-link routes that never
  check the tag's owner), never reported on an edit.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from lattice.importer.profile_gen import _Generator
from lattice.pycheck.checks import all_findings, derive_not_owned, private_leaks
from lattice.pycheck.policy import AdminConcept, Policy
from lattice.pycheck.view import AppView

Ownership = tuple[Mapping[str, str], Mapping[str, tuple[str, str]]]


def derive_policy(app_dir: Path, package: str, *, ownership: Ownership | None = None) -> Policy:
    """The policy of the original app at ``app_dir`` (the directory of ``package``).
    ``ownership``: (owners, owner_via) to use instead of the importer's inference."""
    view = AppView(Path(app_dir), package)
    gen = _Generator(view.model)
    users = gen.users_table()
    responses, _ = gen.responses()
    plans = gen.table_plans(users, responses)

    if ownership is None:
        owners = {p.table.name: p.owner for p in plans if p.owner}
        owner_via = {p.table.name: p.owner_via for p in plans if p.owner_via}
    else:
        owners = {t: c for t, c in ownership[0].items() if c}
        owner_via = {t: tuple(v) for t, v in ownership[1].items() if v}
    by_sql_name = {p.table.table_name: p for p in plans}
    links: dict[str, tuple[tuple[str, str], ...]] = {}
    for p in plans:
        if p.table.name == users or p.table.name in owners or p.table.name in owner_via:
            continue
        pairs = []
        for c in p.table.columns:
            parent = by_sql_name.get((c.foreign_key or "").split(".")[0])
            if parent is not None and parent.table.name in owners:
                pairs.append((c.name, parent.table.name))
        if pairs:
            links[p.table.name] = tuple(pairs)
    shared = tuple(
        p.table.name
        for p in plans
        if p.table.name != users
        and p.table.name not in owners
        and p.table.name not in owner_via
        and p.table.name not in links
    )
    private = {p.table.name: tuple(p.private) for p in plans if p.private}

    admin = None
    admin_dep = next((d for d in view.model.dependencies if d.kind == "admin"), None)
    if admin_dep is not None and admin_dep.admin_column:
        admin = AdminConcept(
            admin_dep.admin_column, admin_dep.name, admin_dep.admin_status, admin_dep.admin_detail
        )

    names = Counter(n for r in view.model.routes for n in view.callers(r))
    policy = Policy(
        package=package,
        users_table=users,
        caller=names.most_common(1)[0][0] if names else "current_user",
        owners=owners,
        owner_via=owner_via,  # type: ignore[arg-type]
        shared=shared,
        links=links,
        private=private,
        admin=admin,
        naive_datetimes=view.model.dialect == "sqlite",
    )
    exposed = {
        col for *_, col, how, _ in private_leaks(view, policy, dicts_only=True) if how == "dict"
    }
    private = {
        t: kept
        for t, cols in private.items()
        if (kept := tuple(c for c in cols if c not in exposed))
    }
    policy = replace(
        policy,
        private=private,
        not_owned=derive_not_owned(view, policy),
        examples=_examples(view, owners),
    )
    return replace(policy, baseline=frozenset(f.key for f in all_findings(view, policy)))


def _examples(view: AppView, owners: dict[str, str]) -> dict[str, str]:
    """table -> the first Customer route (in file order) comparing its owner column with
    the caller."""
    out: dict[str, str] = {}
    for route in sorted(view.model.routes, key=lambda r: (r.module, r.line)):
        if route.auth != "Customer":
            continue
        for use in route.owner_uses:
            if use.how == "compare" and owners.get(use.table) == use.column:
                out.setdefault(use.table, route.name)
    return out


__all__ = ["derive_policy"]
