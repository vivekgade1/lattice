"""The deterministic attack generator: spec model -> a list of :class:`Attack`.

It builds no traffic and makes no LLM call. Every attack is a function of the spec's
scenarios (concrete requests and their per-caller expected status -- the access matrix is
exactly this), its contracts and profile (parameter bounds, ownership, private columns) and
its state machines. The output is sorted, so two runs of the same spec plan the same
attacks in the same order.

Endpoint model. Scenarios are the source of concrete requests: a step is
``(method, path, caller, json, expect_status)`` and the access matrix emits one per
(caller, action) cell, so a step whose caller got a 2xx is the owner's request and the
other callers' cells are the refusals the spec promises. Contracts and the profile enrich
each endpoint (matched by method + path template) with parameter bounds, the owned table,
its private columns and its state machine. An app whose spec has no contracts (Streak uses
``access_matrix``) still gets authz, auth, state and leak from scenarios + profile; bounds
is then limited to the enum violations the profile states (documented in the report).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from lattice.security.schema import Attack, AttackRequest, Confirm
from lattice.spec.model import Spec, Step
from lattice.typesys import Scalar

# fields a mass-assignment attack injects that a request model must not let the caller set
MASS_FIELDS: tuple[tuple[str, Any], ...] = (
    ("id", 999999),
    ("role", "Admin"),
    ("is_admin", True),
    ("created_at", "2000-01-01T00:00:00Z"),
    ("updated_at", "2000-01-01T00:00:00Z"),
    ("version", 999),
    ("change_seq", 999),
)
# metacharacters a bounds/injection attack puts in a string field
INJECTIONS: tuple[str, ...] = ("' OR '1'='1", "'; DROP TABLE users;--", "\x00", "../../etc/passwd")
# markers of a leaked internal in a response body
LEAK_MARKERS: tuple[str, ...] = (
    "Traceback (most recent call last)",
    "SQLSTATE",
    "syntax error at or near",
    "sqlalchemy",
    "gorm",
    "/Users/",
    "/home/",
    "goroutine",
    "panic:",
)
SUCCESS = range(200, 300)


def _is_refusal(status: int) -> bool:
    return status >= 400


@dataclass
class Endpoint:
    method: str
    path: str  # concrete (ids baked in by the scenario), or a template when only a contract
    owner: str | None = None  # a handle that got a 2xx
    owner_json: Any = None  # the owner's 2xx request body
    example_json: Any = None  # any request body seen
    caller_status: dict[str | None, int] = field(default_factory=dict)
    owners: set[str | None] = field(default_factory=set)  # callers ever served 2xx here
    contract: Any = None  # a matching lattice.spec.model.Contract, or None
    table: str | None = None

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


_PARAM = re.compile(r"\{[^}]+\}")


def _template(path: str) -> str:
    """A path with its ``{param}`` segments and concrete id segments collapsed, for matching
    a concrete scenario path to a contract template."""
    return _PARAM.sub("{}", re.sub(r"/\d+(?=/|$)", "/{}", path))


def _endpoints(spec: Spec) -> list[Endpoint]:
    """The endpoint model: one :class:`Endpoint` per concrete (method, path) a scenario
    exercises, enriched by any matching contract."""
    eps: dict[str, Endpoint] = {}
    for scenario in spec.scenarios:
        for step in scenario.steps:
            if not isinstance(step, Step):
                continue
            ep = eps.setdefault(
                f"{step.method.upper()} {step.path}", Endpoint(step.method.upper(), step.path)
            )
            # a caller that is ever served 2xx here is an owner; never attack it as a
            # non-owner even if another scenario (another seed) refuses it on this path
            if step.expect_status in SUCCESS:
                ep.owners.add(step.user)
                ep.owner = step.user
                if step.json is not None:
                    ep.owner_json = step.json
            ep.caller_status.setdefault(step.user, step.expect_status)
            if step.expect_status in SUCCESS:
                ep.caller_status[step.user] = step.expect_status
            if step.json is not None and ep.example_json is None:
                ep.example_json = step.json
    contracts_by_template = {}
    for c in spec.contracts:
        contracts_by_template[(c.method.upper(), _template(c.path))] = c
    for ep in eps.values():
        c = contracts_by_template.get((ep.method, _template(ep.path)))
        if c is not None:
            ep.contract = c
            ep.table = c.table
    return sorted(eps.values(), key=lambda e: e.key)


def _owned_table(spec: Spec, ep: Endpoint) -> tuple[str, str, Any] | None:
    """The owned table an endpoint writes, resolved from its contract or its path's first
    segment, with its (schema name, owner column)."""
    name = ep.table
    if name is None and ep.contract is not None:
        name = ep.contract.table
    if name is None:
        return None
    resolved = spec.table(name)
    if resolved is None:
        return None
    _schema_name, sql_name, schema = resolved
    return sql_name, (schema.owner or ""), schema


# ---- the six class generators ----------------------------------------------------------


def _authz(spec: Spec, eps: list[Endpoint]) -> list[Attack]:
    """Every (endpoint, non-owner caller incl. anonymous) whose spec cell is a refusal:
    send the owner's own request as that caller; a 2xx is a finding."""
    out: list[Attack] = []
    for ep in eps:
        for caller in sorted(ep.caller_status, key=lambda u: (u is not None, u or "")):
            status = ep.caller_status[caller]
            if not _is_refusal(status) or caller in ep.owners:
                continue  # the spec allows this caller here (owner, own-scope list, public)
            who = caller or "anon"
            out.append(
                Attack(
                    id=f"authz-{ep.method}-{_slug(ep.path)}-{who}",
                    cls="authz",
                    title=f"{ep.key} as {who} (not the owner)",
                    endpoint=ep.key,
                    requests=(
                        AttackRequest(
                            ep.method,
                            ep.path,
                            as_user=caller,
                            json=ep.owner_json,
                            note=f"owner's request replayed as {who}",
                        ),
                    ),
                    rule="ownership/access matrix",
                    expected=f"{status} (the spec refuses {who} on this route); writes nothing",
                    confirm=Confirm(
                        "served",
                        expect_status=(status,),
                        reason=f"a 2xx means {who} reached an owner-only resource",
                    ),
                )
            )
    return out


def _auth(spec: Spec, eps: list[Endpoint]) -> list[Attack]:
    """Endpoints the spec refuses to an anonymous caller (401): resend with no token and
    with a malformed ``Authorization`` header; a 2xx is a finding."""
    out: list[Attack] = []
    for ep in eps:
        anon = ep.caller_status.get(None)
        if anon is None or not _is_refusal(anon) or None in ep.owners:
            continue  # a public route (anon is served) is not an auth hole
        base = ep.owner_json if ep.owner_json is not None else ep.example_json
        for tag, headers in (
            ("no-token", None),
            ("malformed-bearer", {"Authorization": "Bearer not-a-real-token"}),
            ("empty-bearer", {"Authorization": "Bearer "}),
            ("wrong-scheme", {"Authorization": "Basic Zm9vOmJhcg=="}),
        ):
            out.append(
                Attack(
                    id=f"auth-{ep.method}-{_slug(ep.path)}-{tag}",
                    cls="auth",
                    title=f"{ep.key} with {tag}",
                    endpoint=ep.key,
                    requests=(
                        AttackRequest(
                            ep.method,
                            ep.path,
                            as_user=None,
                            json=base,
                            headers=headers,
                            note=tag,
                        ),
                    ),
                    rule="authentication",
                    expected=f"{anon} (an unauthenticated or forged token is refused); never 2xx",
                    confirm=Confirm(
                        "served",
                        expect_status=(anon,),
                        reason="a 2xx means a protected route was served without a valid token",
                    ),
                )
            )
    return out


def _bounds(spec: Spec, eps: list[Endpoint]) -> list[Attack]:
    """For each stated bound of a contract parameter: just-over, huge, wrong type, empty,
    enum violation, and injection metacharacters. Finding = a 5xx (the bound was not
    handled). Without contracts, uses the profile's enums."""
    out: list[Attack] = []
    for ep in eps:
        c = ep.contract
        owner = ep.owner
        base = dict(ep.owner_json) if isinstance(ep.owner_json, dict) else {}
        if c is not None:
            for p in c.params:
                if p.source not in ("body", "query"):
                    continue
                for tag, value in _bound_values(p):
                    body = {**base, p.name: value}
                    out.append(_bounds_attack(ep, owner, p.name, tag, body))
        else:
            for name, bad in _enum_violations(spec, ep, base):
                out.append(_bounds_attack(ep, owner, name, "enum-violation", bad))
    return out


def _bound_values(p: Any) -> list[tuple[str, Any]]:
    """The out-of-bound values for one contract parameter (deterministic order)."""
    vals: list[tuple[str, Any]] = []
    if p.le is not None:
        vals.append(("just-over-le", p.le + 1))
    if p.lt is not None:
        vals.append(("at-lt", p.lt))
    if p.ge is not None:
        vals.append(("just-under-ge", p.ge - 1))
    if p.gt is not None:
        vals.append(("at-gt", p.gt))
    if p.max_len is not None:
        vals.append(("over-max-len", "a" * (p.max_len + 1)))
    if p.min_len:
        vals.append(("empty", ""))
    if p.one_of:
        vals.append(("not-in-one-of", "__nope__"))
    if p.scalar in (Scalar.INT, Scalar.FLOAT):
        vals.append(("wrong-type", "not-a-number"))
        vals.append(("huge", 10**18))
    if p.scalar is Scalar.STR:
        vals.append(("injection", INJECTIONS[1]))
    return vals


def _enum_violations(spec: Spec, ep: Endpoint, base: dict) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    resolved = _owned_table(spec, ep)
    if resolved is None or not isinstance(ep.owner_json, dict):
        return out
    _name, _owner, schema = resolved
    for col, _values in schema.one_of:
        if col in base:
            out.append((col, {**base, col: "__not_a_valid_enum__"}))
    return out


def _bounds_attack(ep: Endpoint, owner: str | None, pname: str, tag: str, body: dict) -> Attack:
    return Attack(
        id=f"bounds-{ep.method}-{_slug(ep.path)}-{_slug(pname)}-{tag}",
        cls="bounds",
        title=f"{ep.key} {pname}={tag}",
        endpoint=ep.key,
        requests=(
            AttackRequest(ep.method, ep.path, as_user=owner, json=body, note=f"{pname} {tag}"),
        ),
        rule="input bounds",
        expected="422 validation_failed; the value is never accepted and never 5xx",
        confirm=Confirm(
            "bad_accepted",
            reason=f"{pname}={tag} should be refused with 422, not accepted or crashed",
        ),
    )


def _mass(spec: Spec, eps: list[Endpoint]) -> list[Attack]:
    """For each create/update endpoint with a known owner body: send the owner's body plus
    a forbidden field (owner id of another user, role, id, timestamps, version). Finding =
    the owned table gains/keeps a row carrying the injected value."""
    out: list[Attack] = []
    for ep in eps:
        if ep.method not in ("POST", "PUT", "PATCH") or not isinstance(ep.owner_json, dict):
            continue
        resolved = _owned_table(spec, ep)
        if resolved is None:
            continue
        table, owner_col, schema = resolved
        known = set(schema.names())
        for fname, fvalue in _mass_fields(ep, known, owner_col):
            body = {**ep.owner_json, fname: fvalue}
            out.append(
                Attack(
                    id=f"mass-{ep.method}-{_slug(ep.path)}-{_slug(fname)}",
                    cls="mass",
                    title=f"{ep.key} over-posts {fname}",
                    endpoint=ep.key,
                    requests=(
                        AttackRequest(
                            ep.method,
                            ep.path,
                            as_user=ep.owner,
                            json=body,
                            note=f"inject {fname}={fvalue!r}",
                        ),
                    ),
                    rule="mass-assignment / request model",
                    expected=f"the server assigns {fname}; a client value is ignored",
                    confirm=Confirm(
                        "row_present",
                        table=table,
                        where={fname: fvalue},
                        reason=f"a stored {fname}={fvalue!r} means the client set a server field",
                    ),
                )
            )
    return out


def _mass_fields(ep: Endpoint, columns: set[str], owner_col: str) -> list[tuple[str, Any]]:
    """The forbidden fields to inject: with a contract, every table column the caller does
    not already set (its params + example body) and that is not the client's to set; without
    one, the static server-owned names that are table columns. Distinctive values, so a
    stored one is unambiguous."""
    distinctive: dict[str, Any] = {
        owner_col: 999999,
        "id": 999999,
        "role": "Admin",
        "is_admin": True,
        "price": 0.01,
        "created_at": "2000-01-01T00:00:00Z",
        "updated_at": "2000-01-01T00:00:00Z",
        "version": 999,
        "change_seq": 999,
    }
    out: list[tuple[str, Any]] = []
    if ep.contract is not None:
        allowed = {p.name for p in ep.contract.params}
        allowed |= set(ep.owner_json) if isinstance(ep.owner_json, dict) else set()
        for col in sorted(columns - allowed):
            out.append((col, distinctive.get(col, 999999)))
    else:
        base = set(ep.owner_json) if isinstance(ep.owner_json, dict) else set()
        for fname, fvalue in MASS_FIELDS:
            if fname in columns and fname not in base:
                out.append((fname, fvalue))
        if owner_col and owner_col in columns and owner_col not in base:
            out.insert(0, (owner_col, 999999))
    return out


def _state(spec: Spec, eps: list[Endpoint]) -> list[Attack]:
    """Lifecycle abuse the state machine forbids: repeat a terminal action (double cancel,
    double delete) and expect a refusal the second time; a 2xx on the repeat is a finding."""
    out: list[Attack] = []
    for ep in eps:
        owner = ep.owner
        if owner is None:
            continue
        verb = ep.path.rstrip("/").rsplit("/", 1)[-1]
        if not (ep.method == "DELETE" or verb in ("cancel", "archive", "close", "dismiss")):
            continue
        req = AttackRequest(ep.method, ep.path, as_user=owner, json=ep.owner_json, note="first")
        again = AttackRequest(ep.method, ep.path, as_user=owner, json=ep.owner_json, note="repeat")
        out.append(
            Attack(
                id=f"state-{ep.method}-{_slug(ep.path)}-repeat",
                cls="state",
                title=f"{ep.key} applied twice",
                endpoint=ep.key,
                requests=(req, again),
                rule="state machine / lifecycle",
                expected="the second call is refused (409/404); a terminal state is final",
                confirm=Confirm(
                    "served",
                    expect_status=(404, 409, 400, 410),
                    reason="a 2xx on the repeat means a forbidden transition from a terminal state",
                ),
            )
        )
    return out


def _leak(spec: Spec, eps: list[Endpoint]) -> list[Attack]:
    """Trigger an error with a malformed body and scan the error body for a stack trace,
    SQL, file paths, or a private column's seeded value."""
    out: list[Attack] = []
    needles = list(LEAK_MARKERS)
    for ep in eps:
        if ep.method not in ("POST", "PUT", "PATCH"):
            continue
        owner = ep.owner
        out.append(
            Attack(
                id=f"leak-{ep.method}-{_slug(ep.path)}",
                cls="leak",
                title=f"{ep.key} error body",
                endpoint=ep.key,
                requests=(
                    AttackRequest(
                        ep.method,
                        ep.path,
                        as_user=owner,
                        json={"__bad__": object_marker()},
                        note="malformed body to provoke an error",
                    ),
                ),
                rule="information leak / error shape",
                expected="a clean error body: no stack trace, SQL, file path, or secret",
                confirm=Confirm(
                    "body_contains",
                    needles=tuple(needles),
                    reason="an internal detail in the error body is a leak",
                ),
            )
        )
    return out


def object_marker() -> str:
    return "not-a-valid-nested-object-\u0000"


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", str(text)).strip("-").lower() or "x"


_GENERATORS = {
    "authz": _authz,
    "auth": _auth,
    "bounds": _bounds,
    "mass": _mass,
    "state": _state,
    "leak": _leak,
}


def generate(spec: Spec, classes: Iterable[str] | None = None) -> list[Attack]:
    """Every attack of the requested ``classes`` (default: all), deterministically ordered
    by class then id."""
    chosen = tuple(classes) if classes else tuple(_GENERATORS)
    eps = _endpoints(spec)
    attacks: list[Attack] = []
    for cls in _GENERATORS:
        if cls in chosen:
            attacks.extend(_GENERATORS[cls](spec, eps))
    return sorted(attacks, key=lambda a: (list(_GENERATORS).index(a.cls), a.id))


def scope_endpoints(spec: Spec, scope: str, value: str | None) -> Spec:
    """A spec narrowed to a scope: ``system`` (all), ``endpoints`` (a comma list of
    ``METHOD /path`` prefixes), or ``rules`` (handled upstream via the trace). Returns the
    spec unchanged for ``system``; filters scenarios by endpoint otherwise."""
    if scope == "system" or not value:
        return spec
    if scope == "endpoints":
        wanted = {v.strip() for v in value.split(",")}
        kept = tuple(
            s
            for s in spec.scenarios
            if any(
                isinstance(st, Step)
                and any(f"{st.method.upper()} {st.path}".startswith(w) for w in wanted)
                for st in s.steps
            )
        )
        return _replace_scenarios(spec, kept)
    return spec


def _replace_scenarios(spec: Spec, scenarios: tuple) -> Spec:
    from dataclasses import replace

    return replace(spec, scenarios=scenarios)


__all__ = ["Endpoint", "generate", "scope_endpoints"]
