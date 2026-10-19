"""GO805 for Go (PY805's not-owned half; round 3, K8): the status an app answers another user's row
with, derived from the app, and the routes that answer something else.

Both halves read the opt-in *policy* extract (``gocheck extract``), whose ``owner_uses`` gain a
``raise: {status, file, line}`` when the use is a comparison in the ``Where`` of a GORM chain whose
result is tested by the next statement (``err := chain`` then ``if err != nil``,
``if err := chain; ...``, ``if chain.Error != nil``) and that branch returns exactly one constant
4xx refusal. The link is structural: a refusal that merely sits near a comparison is not linked.

* :func:`derive_not_owned`: the app's answer is the one status every linked refusal shares.
  Refusals that disagree, or none, give no answer (``status`` is None, with a note): GO805 stays
  silent.
* :func:`not_owned_findings`: given the answer (``GoPolicy.not_owned``, derived from the original
  with :func:`with_not_owned`), each route of the checked module whose linked refusal differs is a
  ``GO805`` finding, keyed ``GO805|<METHOD path>|not_owned`` (a warning, as PY805).

*Cannot see:* a refusal in a helper the handler calls, a lookup whose result is not tested by the
next statement, an error value returned instead of a constant status (Streak's style), the admin
answer (PY805's second half: a Gin app declares no admin dependency), and details (status only).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lattice.gocheck import GoCheckError, GoPolicy
from lattice.pycheck.findings import Finding

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotOwned:
    """The app's answer to another user's row: ``status`` (None when unclear or none), how many
    linked refusals it rests on, and why there is no answer."""

    status: int | None
    linked: int
    note: str = ""


def linked_uses(
    extract: Mapping[str, Any], owners: Mapping[str, str] | None = None
) -> Iterator[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """(route, owner use) for each owner comparison with a linked refusal; with ``owners``
    (struct -> owner column) only the comparisons of an owned table's owner column."""
    for route in extract.get("routes", ()):
        for use in route.get("owner_uses", ()):
            if not (use.get("how") == "compare" and use.get("raise")):
                continue
            if owners is None or owners.get(use.get("model")) == use.get("column"):
                yield route, use


def derive_not_owned(
    extract: Mapping[str, Any], owners: Mapping[str, str] | None = None
) -> NotOwned:
    """The one status every linked refusal of the module shares (see the module docstring)."""
    seen = [use["raise"]["status"] for _, use in linked_uses(extract, owners)]
    if not seen:
        return NotOwned(None, 0, "no owner-scoped lookup refuses with a constant 4xx: GO805 silent")
    if len(set(seen)) != 1:
        note = f"the owner-scoped lookups refuse with {sorted(set(seen))}: unclear, GO805 is silent"
        return NotOwned(None, len(seen), note)
    return NotOwned(seen[0], len(seen))


def derive_from_module(module_dir: Path | str, owners: Mapping[str, str] | None = None) -> NotOwned:
    """:func:`derive_not_owned` on the Go module at ``module_dir`` (the original)."""
    from lattice.gocheck.derive import policy_extract

    return derive_not_owned(policy_extract(module_dir), owners)


def with_not_owned(policy: GoPolicy, module_dir: Path | str) -> GoPolicy:
    """``policy`` with the answer derived from the original at ``module_dir`` (its owned tables'
    comparisons); no answer leaves GO805 silent, and the note is logged."""
    got = derive_from_module(module_dir, policy.owners)
    if got.status is None:
        log.info("GO805: %s", got.note)
    return dataclasses.replace(policy, not_owned=got.status)


def not_owned_findings(src_dir: Path | str, policy: GoPolicy) -> list[Finding]:
    """GO805 findings on the module at ``src_dir`` (none without ``policy.not_owned``; a failed
    extract answers none, as :func:`lattice.gocheck.run_gocheck` does)."""
    answer = policy.not_owned
    if answer is None or not policy.owners:
        return []
    from lattice.gocheck.derive import policy_extract

    try:
        extract = policy_extract(src_dir)
    except GoCheckError:
        log.exception("GO805 extract failed on %s", src_dir)
        return []
    out: dict[str, Finding] = {}
    for route, use in linked_uses(extract, policy.owners):
        raised = use["raise"]
        if raised["status"] == answer:
            continue
        where = f"{route['method']} {route['path']}"
        key = f"GO805|{where}|not_owned"
        out.setdefault(
            key,
            Finding(
                "GO805",
                raised["file"],
                int(raised["line"]),
                f"{where} answers another user's {use['model']} with {raised['status']}; "
                f"this app answers it with {answer}",
                f"answer {answer} when the {use['model']} row is not the caller's -- this app's "
                "own answer to another user's row",
                key=key,
            ),
        )
    return list(out.values())


__all__ = [
    "NotOwned",
    "derive_from_module",
    "derive_not_owned",
    "linked_uses",
    "not_owned_findings",
    "with_not_owned",
]
