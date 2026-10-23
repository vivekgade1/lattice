"""Layer A for ASP.NET Core + EF Core apps: ``dncheck check`` (Spike J, node B3), the .NET counterpart of
:mod:`lattice.gocheck`.

Usage mirrors gocheck: build the policy from the spec, then check the project directory::

    policy = DnPolicy.from_spec(spec)
    findings = run_dncheck(project_dir, policy)   # [Finding(code, file, line, message, hint, key)]

The rules are C# (``src/Check*.cs``, added to B1's Roslyn helper as the ``check`` subcommand: they read the extraction
``extract`` makes and add nothing to it). The target is only read, never restored or built. Each rule fires only on a
pattern it can read with certainty; the guarantees mean what their PY8xx counterparts mean (``docs/lattice-language-rules.md``:
one meaning, gated per target).

* **DN800 parse.** A compiled file with a Roslyn syntax error (the diagnostic ids only). With a DN800 no other rule is judged.
* **DN801 ownership** (PY801). A route with a complete reach (N10) that reads, updates or deletes rows of an owned table
  with no owner evidence for it anywhere in what it runs: the owner column compared with, or set from, the caller (B1's N8),
  a global query filter in force, or any other comparison, assignment or initializer of the owner property (as GO801: any
  mention counts). A table owned through a parent needs that evidence on the parent. Not judged: admin routes (the policy's, or
  ``RequireRole(admin)``), the policy's ``open_routes``, creating a row alone, the users table. A query handed to a project
  method (``Shape(db.Notes.Where(..))``) is judged unless the callee compares the owner itself. A write by key
  (``Where(x => x.Id == id)``, ``Find(id)``, ``Remove(row)``) is covered by an EARLIER statement of the same request that
  loaded that entity scoped to the caller from the same key with the miss refused (``?? throw``, ``Single*``/``First*``,
  ``if (row == null) throw|return``), or created the row with its owner set; the same key, not another id. The owner column
  set from a value the request carries (``OwnerId = request.OwnerId ?? caller``) is a finding too. *Cannot see:* evidence that
  exists but is not applied to the query that reaches the response; a reach that is incomplete (an opaque call, an interface
  with several registrations, depth) is not judged.
* **DN802 private columns** (PY802). In a route that is not admin-only, a response (``Ok``, ``Created*``, ``Accepted*``,
  ``Json``, ``TypedResults.Ok``, a handler's ``return value``) that serializes a private column: an entity (a collection, a DTO
  field, a navigation of one) whose private property has no ``[JsonIgnore]``, or an anonymous object or DTO copying
  ``row.PrivateProperty``; ``StatusCode(code, value)`` answers like ``Ok``, and an answer that is what a project method
  returns is followed into that method's returned expressions. A conditional ``[JsonIgnore]`` is not claimed. *Cannot see:*
  values copied through containers or helper arguments, hand-written serialization.
* **DN803 bounds** (PY803). A request value (a body DTO field, a query/form parameter) carried unchanged to a column
  (assignment, initializer, ``SetProperty``) or to ``Take``/``Skip`` with no enforced bound (a string needs a maximum length,
  a number an upper bound; B1 reads annotations under ``[ApiController]``, validators the handler calls and refuses on, handler
  guards) and no refusing ``if`` on it before the write. *Cannot see:* values changed on the way (``Math.Min``, a helper).
* **DN806 naive/aware datetimes** (PY806; **active** for .NET, where a ``DateTime`` has a Kind). A comparison, subtraction or
  Compare/Max/Min between a ``DateTime`` of known Local kind and one of known Utc kind, or a known Local/Unspecified
  ``DateTime`` converted to ``DateTimeOffset`` and compared with an offset value. Unknown kinds (an EF column read) stay silent.
* **DN807 partial writes** (PY807). A ``SaveChanges[Async]`` statement (or a transaction ``Commit``) outside a transaction
  that a refusal (a 4xx answer or mapped throw) or a second commit can follow in the same request.
* **DN808 state machines** (PY808; only with ``machines``). A status write from a guarded state with no edge to the written state.

Not available for .NET (gated, :data:`DN_GATED`; each with its reason, reported in layer A's notes): DN804 and DN805.
"""

# ruff: noqa: E501
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from lattice.dncheck import DnCheckError, DotnetToolchainMissing, run, sdk_version
from lattice.gocheck import GoPolicy
from lattice.pycheck.findings import Finding, render_findings

log = logging.getLogger(__name__)

# code -> the guarantee (the PY8xx guarantee of the same number)
DN_CODES: dict[str, str] = {
    "DN800": "the app's code does not parse",
    "DN801": "ownership: an owned table's rows reach a caller who was not checked to own them",
    "DN802": "private columns: a private column reaches a response",
    "DN803": "bounds: a request value is written to the database or used as a limit unbounded",
    "DN806": "datetimes: a Local-kind and a Utc-kind DateTime are compared",
    "DN807": "partial writes: a commit that a later refusal of the same request can follow",
    "DN808": "state machines: a state written from a guarded state with no edge to it",
}

# the PY8xx rules with no .NET counterpart in this round: code -> why
DN_GATED: dict[str, str] = {
    "DN804": "route order (PY804) is moot on ASP.NET Core: endpoint routing orders by template precedence (a literal "
    "segment beats a parameter whatever the registration order); two templates of equal precedence throw "
    "AmbiguousMatchException at request time, which is a note (ambiguous routes), not a finding",
    "DN805": "error conventions (PY805) is not built for .NET: Go round 3's K8 derivation of the not-owned answer was merged but "
    "its own Tier 2 rows had misses and it reaches layer A only through a caller that passes the answer (`not_owned`); the "
    "prereg (Amendment 1, Q9) builds DN805 only if K8 passed",
}


@dataclass(frozen=True)
class DnPolicy(GoPolicy):
    """The guarantees dncheck checks an app against: the :class:`~lattice.gocheck.GoPolicy` fields, and
    ``from_spec`` is the same algorithm (it is GoPolicy's, building a ``DnPolicy``). Names are the spec's: schema
    (entity) names and SQL column names; a property name is accepted where a column has the same name without underscores
    and case. ``not_owned`` is reserved for DN805 and ignored while it is gated."""


def all_dn_findings(src_dir: Path | str, policy: DnPolicy) -> list[Finding]:
    """Every finding on the project at ``src_dir``, the baseline included (raises :class:`DnCheckError` when the
    checker cannot run)."""
    payload = {
        "root": str(Path(src_dir).resolve()),
        "sdk": sdk_version(),
        "policy": policy.to_json(),
    }
    try:
        data = json.loads(run("check", payload))
    except ValueError as err:
        raise DnCheckError(f"dncheck check answered invalid JSON: {err}") from err
    return [
        Finding(f["code"], f["file"], int(f["line"]), f["message"], f["hint"], key=f["key"])
        for f in data.get("findings", ())
    ]


def run_dncheck(src_dir: Path | str, policy: DnPolicy, *, strict: bool = False) -> list[Finding]:
    """The guarantee findings on the project at ``src_dir``, minus the policy's baseline, sorted by file and line. A
    checker failure answers no findings (it must not cost the model a repair round) unless ``strict``; a missing SDK
    always raises :class:`DotnetToolchainMissing`."""
    if not Path(src_dir).is_dir():
        raise DnCheckError(f"no .NET project directory at {src_dir}")
    try:
        found = all_dn_findings(src_dir, policy)
    except DotnetToolchainMissing:
        raise
    except DnCheckError:
        if strict:
            raise
        log.exception("dncheck failed on %s", src_dir)
        return []
    return [f for f in found if f.key not in policy.baseline]


def gated_note() -> str:
    """Layer A's note naming the PY rules with no .NET counterpart, and why."""
    return "layer A (dncheck): not available for .NET: " + "; ".join(
        f"{code} -- {why}" for code, why in DN_GATED.items()
    )


def render(findings: Iterable[Finding], prefix: str = "") -> str:
    return render_findings(findings, prefix)


__all__ = [
    "DN_CODES",
    "DN_GATED",
    "DnPolicy",
    "all_dn_findings",
    "gated_note",
    "render",
    "run_dncheck",
]
