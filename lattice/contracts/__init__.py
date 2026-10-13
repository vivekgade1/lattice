"""Contracts between projects (multi-project layer 3, docs/design/lattice-multi-project-phase.md).

A *contract* here is a cross-project reliance: a consumer project says which endpoints, statuses,
fields and access it relies on in a provider project, and Lattice checks that against the
provider's spec. It is unrelated to :class:`lattice.spec.Contract`.

* :mod:`lattice.contracts.export`: the provider's export, derived from its loaded ``Spec``.
* :mod:`lattice.contracts.check`: the import file, the lock, ``pin`` and ``check``.
* :mod:`lattice.contracts.live`: the opt-in live check (the provider app, started by the check).

CLI: ``lattice contracts export|pin|check`` (also ``python -m lattice.contracts``). Nothing here
is imported by ``import lattice.contracts`` itself, so the package stays cheap to import.
"""

from __future__ import annotations
