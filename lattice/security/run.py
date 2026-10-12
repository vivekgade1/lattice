"""Run attacks against the loopback target and judge the claims.

The run has two phases, as the plan requires:

1. **Attack** -- every planned attack is sent against one started, seeded target through a
   bounded thread pool. A request that errors is counted refuted, never a finding. This
   phase gathers *candidates*; because the attacks share one database it may over-claim
   (one write seen by another attack), which the judge removes.
2. **Judge** -- every candidate is replayed on its OWN fresh database and seed (a new
   target). A finding is ``confirmed`` only if the oracle fires again in isolation.
   Candidates that do not reproduce are reported as unreproduced claims, with the count.

The oracles are the deterministic ones in :mod:`lattice.security.schema`; nothing else can
turn a request into a finding.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager

from lattice.security.schema import (
    SEVERITY,
    Attack,
    ClassReport,
    Confirm,
    Finding,
    Observation,
    Report,
)
from lattice.security.target import Target
from lattice.spec.model import Spec

SUCCESS = range(200, 300)
MAX_WORKERS = 4

# a factory that, each call, yields a fresh started Target (one fresh database and seed)
MakeTarget = Callable[[], AbstractContextManager[Target]]


def _preview(answer: object, limit: int = 300) -> str:
    text = getattr(answer, "text", "") or ""
    return text[:limit]


def _send_all(target: Target, attack: Attack) -> list[Observation]:
    obs: list[Observation] = []
    for req in attack.requests:
        answer = target.request(req)
        obs.append(Observation(req.method, req.path, answer.status, _preview(answer)))
    return obs


def _fires(confirm: Confirm, target: Target, obs: list[Observation]) -> tuple[bool, str]:
    """Whether the oracle fires, and why. Reads the database for row oracles (tolerant: a
    read it cannot do is not a finding)."""
    last = obs[-1].status if obs else 0
    texts = [o.body_preview or "" for o in obs]
    if confirm.kind == "served":
        if last in SUCCESS:
            return True, f"served {last} where {confirm.expect_status} was due ({confirm.reason})"
        return False, f"refused with {last}"
    if confirm.kind == "status_not_in":
        if last not in confirm.expect_status:
            return True, f"answered {last}, not one of {confirm.expect_status}"
        return False, f"answered {last}"
    if confirm.kind == "server_error":
        bad = [o.status for o in obs if o.status >= 500]
        if bad:
            return True, f"server error {bad} ({confirm.reason})"
        return False, "no 5xx"
    if confirm.kind == "bad_accepted":
        if last in SUCCESS:
            return True, f"bad input accepted ({last}); {confirm.reason}"
        if any(o.status >= 500 for o in obs):
            return True, f"bad input caused a 5xx; {confirm.reason}"
        return False, f"refused with {last}"
    if confirm.kind == "body_contains":
        for needle in confirm.needles:
            for t in texts:
                if needle in t:
                    return True, f"response body contains {needle!r} ({confirm.reason})"
        return False, "no leaked marker in any body"
    if confirm.kind in ("row_present", "row_changed") and confirm.table and confirm.where:
        rows = target.read(confirm.table, confirm.where)
        if confirm.kind == "row_present":
            if rows:
                return (
                    True,
                    f"{len(rows)} match in {confirm.table} {confirm.where}; {confirm.reason}",
                )
            return False, f"no row of {confirm.table} matches {confirm.where}"
        for row in rows:
            if confirm.equals and all(row.get(k) == v for k, v in confirm.equals.items()):
                return True, f"a row of {confirm.table} has {confirm.equals} ({confirm.reason})"
        return False, "no matching row changed"
    return False, "oracle did not fire"


def _attempt(target: Target, attack: Attack) -> tuple[bool, str, list[Observation], bool]:
    """(oracle fired, why, observations, errored). An errored attempt is inconclusive, not a
    refutation: it is still sent to the judge so a shared-database lock or interference in
    phase 1 never hides a hole."""
    try:
        obs = _send_all(target, attack)
    except Exception as err:  # noqa: BLE001 -- inconclusive; the judge decides in isolation
        return False, f"request error: {type(err).__name__}: {err}", [], True
    fired, why = _fires(attack.confirm, target, obs)
    return fired, why, obs, False


def judge_one(make_target: MakeTarget, attack: Attack) -> tuple[bool, str, list[Observation]]:
    """Replay one attack on a brand-new target (fresh database and seed). True only if the
    oracle fires again in isolation."""
    with make_target() as fresh:
        fired, why, obs, _errored = _attempt(fresh, attack)
        return fired, why, obs


def run(
    make_target: MakeTarget,
    attacks: Sequence[Attack],
    *,
    scope: str = "system",
    max_workers: int = MAX_WORKERS,
    out_dir: str = "",
    notes: Sequence[str] = (),
) -> Report:
    """Run ``attacks`` and judge them; a :class:`Report`. ``make_target`` yields a fresh
    started :class:`Target` each call (one fresh seeded database)."""
    t0 = time.perf_counter()
    by_class: dict[str, ClassReport] = {}
    for a in attacks:
        by_class.setdefault(a.cls, ClassReport(a.cls)).run += 1

    # phase 1: gather candidates against one shared target
    candidates: list[Attack] = []
    refuted: list[Attack] = []
    host = ""
    target_desc = ""
    with make_target() as target:
        host = target.host
        target_desc = target.base_url
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            results = list(pool.map(lambda a: (a, _attempt(target, a)), attacks))
    for a, (fired, _why, _obs, errored) in results:
        (candidates if (fired or errored) else refuted).append(a)

    # phase 2: judge every candidate on its own fresh database and seed
    findings: list[Finding] = []
    for a in candidates:
        confirmed, why, obs = judge_one(make_target, a)
        findings.append(
            Finding(
                attack_id=a.id,
                cls=a.cls,
                title=a.title,
                endpoint=a.endpoint,
                rule=a.rule,
                expected=a.expected,
                severity=SEVERITY.get(a.cls, "unknown"),
                confirmed=confirmed,
                detail=why,
                observations=obs,
            )
        )

    for a in refuted:
        by_class[a.cls].refuted += 1
    for f in findings:
        cr = by_class[f.cls]
        if f.confirmed:
            cr.findings += 1
        else:
            cr.unreproduced += 1

    classreports = [by_class[c] for c in sorted(by_class)]
    return Report(
        target=target_desc or "(target)",
        host=host,
        scope=scope,
        classes=tuple(sorted({a.cls for a in attacks})),
        attacks_planned=len(attacks),
        classreports=classreports,
        findings=sorted(findings, key=lambda f: (not f.confirmed, f.cls, f.attack_id)),
        wall_seconds=time.perf_counter() - t0,
        out_dir=out_dir,
        notes=list(notes),
    )


def union_given(spec: Spec) -> dict[str, list[dict]]:
    """The union of every scenario's ``given`` rows, deduped by each table's key, so the
    resources attacks target (e.g. the owner's seeded habit) exist on the throwaway database
    even when the harness seed alone does not contain them."""
    from lattice.spec.model import resolve_table

    merged: dict[str, dict[tuple, dict]] = {}
    for sc in spec.scenarios:
        for table, rows in (sc.given or {}).items():
            found = resolve_table(spec.profile, table)
            if found is None or not rows:
                continue
            _n, _sql, schema = found
            keycols = schema.keys[0] if schema.keys else tuple(rows[0].keys())
            for row in rows:
                key = tuple(row.get(c) for c in keycols)
                merged.setdefault(table, {}).setdefault(key, row)
    return {t: list(d.values()) for t, d in merged.items()}


def make_seed_hook(spec: Spec) -> Callable[[object], None] | None:
    """A ``seed_hook(db_ref)`` that inserts :func:`union_given` into the throwaway database,
    tolerantly (a row that conflicts with the harness seed is skipped). None when there is
    nothing extra to seed."""
    given = union_given(spec)
    if not given:
        return None

    def hook(db_ref: object) -> None:
        from lattice.spec.check import _stored
        from lattice.spec.db import open_db
        from lattice.spec.model import resolve_table

        with open_db(db_ref) as conn:
            for table, rows in given.items():
                found = resolve_table(spec.profile, table)
                if found is None:
                    continue
                _n, sql, schema = found
                for row in rows:
                    try:
                        conn.insert(sql, [{c: _stored(schema, c, row[c]) for c in row}])
                    except Exception:  # noqa: BLE001 -- a row the base seed already has is skipped
                        pass

    return hook


def distinct_findings(report: Report) -> int:
    """Confirmed findings deduplicated by (class, endpoint, rule) -- the S3 dedupe metric."""
    return len({(f.cls, f.endpoint, f.rule) for f in report.confirmed})


__all__ = ["distinct_findings", "judge_one", "run"]
