"""The data the security module speaks: an :class:`Attack` (what to send and how to judge
it), an :class:`AttackRequest` (one HTTP request, headers and all -- the DSL cannot express
these, this module's own httpx client can), a :class:`Finding` (a judged, replayed claim)
and a :class:`Report` (per-class tallies, for humans and as JSON).

An attack's ``confirm`` is a deterministic oracle, not code: a small tagged record the
judge evaluates against the responses and (where available) the database. No ``eval``; the
only ways a request becomes a finding are the ones listed in :data:`ORACLES`.

Severity is a documented per-class table (:data:`SEVERITY`), NOT a CVSS claim: it orders
classes for a human reading the report, nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# the attack classes this module generates (plan.py), in report order
CLASSES = ("authz", "auth", "bounds", "mass", "state", "leak")

# a documented ordering for humans, not a CVSS score
SEVERITY: dict[str, str] = {
    "authz": "high",  # another user's data read or written
    "auth": "high",  # a protected route served without a valid token
    "mass": "high",  # a field the request model forbids changed a row
    "state": "medium",  # an illegal lifecycle transition
    "bounds": "medium",  # a stated limit not enforced / a 5xx on bad input
    "leak": "medium",  # a secret or another user's data in a response body
}

# the confirm-oracle tags the judge understands (schema.Finding is produced only by these)
ORACLES = (
    "served",  # the LAST response's status is a success (2xx) where a refusal was due
    "status_not_in",  # the LAST status is not one of the expected refusal statuses
    "server_error",  # any response status >= 500 (a stated bound not handled)
    "bad_accepted",  # the LAST status is a 2xx (bad input stored) OR >= 500 (a bound unhandled)
    "body_contains",  # any response body/text contains one of `needles` (a leak)
    "row_changed",  # a database row matching `where` in `table` has `equals` (a forbidden write)
    "row_present",  # at least one row of `table` matches `where` (a forbidden create)
)


@dataclass(frozen=True)
class AttackRequest:
    """One HTTP request of an attack.

    ``as_user``: a ``Spec.users`` handle whose token is attached, or ``None`` for an
    anonymous request. ``headers`` are sent verbatim and OVERRIDE the auth header for their
    own names (so ``{"Authorization": "Bearer junk"}`` with ``as_user=None`` sends a
    malformed token -- the transport gap the DSL has does not apply here). ``note`` says what
    the request is for, in words."""

    method: str
    path: str
    as_user: str | None = None
    json: Any = field(default=None, hash=False)
    params: dict[str, Any] | None = field(default=None, hash=False)
    headers: dict[str, str] | None = field(default=None, hash=False)
    note: str = ""


@dataclass(frozen=True)
class Confirm:
    """A deterministic finding oracle (see :data:`ORACLES`). Only fields the ``kind`` uses
    are set. ``expect_status``: the refusal statuses the spec states (for ``served`` /
    ``status_not_in``). ``needles``: substrings whose presence in a body is a leak.
    ``table``/``where``/``equals``: a database check (a forbidden write or create)."""

    kind: str
    expect_status: tuple[int, ...] = ()
    needles: tuple[str, ...] = ()
    table: str | None = None
    where: dict[str, Any] | None = field(default=None, hash=False)
    equals: dict[str, Any] | None = field(default=None, hash=False)
    reason: str = ""


@dataclass(frozen=True)
class Attack:
    """A generated attack: a stable ``id``, its ``cls`` (one of :data:`CLASSES`), the
    ``requests`` to send in order, the ``rule`` id it targets (or ``"none stated"``), the
    ``expected`` defence in the spec's words, and the ``confirm`` oracle the judge uses."""

    id: str
    cls: str
    title: str
    requests: tuple[AttackRequest, ...]
    rule: str
    expected: str
    confirm: Confirm
    endpoint: str = ""


@dataclass
class Observation:
    """What one request answered during a run (kept so a finding carries its evidence)."""

    method: str
    path: str
    status: int
    body_preview: str = ""


@dataclass
class Finding:
    """A judged attack. ``confirmed`` is True only if the judge reproduced it on a fresh
    database and seed; otherwise it is a claim that did not reproduce."""

    attack_id: str
    cls: str
    title: str
    endpoint: str
    rule: str
    expected: str
    severity: str
    confirmed: bool
    detail: str
    observations: list[Observation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attack_id": self.attack_id,
            "class": self.cls,
            "title": self.title,
            "endpoint": self.endpoint,
            "rule": self.rule,
            "expected": self.expected,
            "severity": self.severity,
            "confirmed": self.confirmed,
            "detail": self.detail,
            "observations": [
                {"method": o.method, "path": o.path, "status": o.status} for o in self.observations
            ],
        }


@dataclass
class ClassReport:
    """Per-class tallies: attacks run, attacks the app refuted (defended), confirmed
    findings, and claims that did not reproduce (``not_refuted`` is findings +
    unreproduced -- attacks the app did not clearly refute)."""

    cls: str
    run: int = 0
    refuted: int = 0
    findings: int = 0
    unreproduced: int = 0

    @property
    def not_refuted(self) -> int:
        return self.findings + self.unreproduced

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": self.cls,
            "severity": SEVERITY.get(self.cls, "unknown"),
            "run": self.run,
            "refuted": self.refuted,
            "findings": self.findings,
            "unreproduced": self.unreproduced,
            "not_refuted": self.not_refuted,
        }


# what a spec-driven attack cannot see; stated in every report (guardrail 6)
BLIND_SPOTS = (
    "dependency CVEs (the spec does not name the app's libraries)",
    "TLS and transport security (the module speaks plain loopback HTTP)",
    "deployment and process config (one in-memory process is not the production topology)",
    "secrets committed to the repository",
    "timing side channels, and holes needing the signing key or app source knowledge",
    "races and concurrency (the deterministic generator does not race requests)",
)


@dataclass
class Report:
    """A security run's result. ``target`` describes the app and its loopback host;
    ``scope``/``classes`` what was asked; ``classreports`` the per-class tallies;
    ``findings`` every judged claim (confirmed first)."""

    target: str
    host: str
    scope: str
    classes: tuple[str, ...]
    attacks_planned: int
    classreports: list[ClassReport]
    findings: list[Finding]
    wall_seconds: float
    out_dir: str
    notes: list[str] = field(default_factory=list)

    @property
    def confirmed(self) -> list[Finding]:
        return [f for f in self.findings if f.confirmed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "lattice.security/report/1",
            "target": self.target,
            "host": self.host,
            "scope": self.scope,
            "classes": list(self.classes),
            "attacks_planned": self.attacks_planned,
            "wall_seconds": round(self.wall_seconds, 3),
            "out_dir": self.out_dir,
            "classes_report": [c.to_dict() for c in self.classreports],
            "confirmed_findings": [f.to_dict() for f in self.confirmed],
            "unreproduced_claims": [f.to_dict() for f in self.findings if not f.confirmed],
            "cannot_see": list(BLIND_SPOTS),
            "notes": list(self.notes),
        }
