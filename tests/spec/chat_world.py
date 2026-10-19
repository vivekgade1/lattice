"""A small synthetic world for the chat router and checker tests (Spike F, chat round 2): an app
map (``lattice.appmap/1`` rows, built by hand so every field a disposition reads is known), a spec
digest with two rules, and ``lattice.studio-impact/1`` bodies of two changes (c1 with tier 1, 2 and 3
items, c2 with a determined item of another kind, c9 with no record). Nothing here runs a process."""

# ruff: noqa: E501 -- expected outputs are data lines

from __future__ import annotations

from typing import Any

from lattice.spec import appmap as am
from lattice.spec import ask
from lattice.spec import claim_check as cc

T1 = "t1-aaaaaaaaaa"
T2 = "t2-bbbbbbbbbb"
T2B = "t2-cccccccccc"
T3 = "t3-dddddddddd"
T3B = "t3-eeeeeeeeee"
T2_OTHER = "t2-1111111111"
WF_FAILED = "wf-053146a70c"
WF_OK = "wf-0c6bdd7ab0"
WF_UNVER = "wf-0da61e11ce"

RULE_TEXT = "A check-in that fails validation is refused with 422 and writes nothing."


def _rule(rid: str, text: str, status: str = "implemented_verified", **kw: Any) -> dict[str, Any]:
    row = {
        "id": rid,
        "text": text,
        "spec_status": "checked",
        "impl_status": status,
        "reasons": [],
        "missing": [],
        "impact_label": None,
        "basis": {"implemented_verified": "observed", "implemented_unverified": "static"}.get(
            status, "inferred" if status == "required_not_implemented" else "none"
        ),
        "elements": [],
        "evidence": [],
        "workflows": [],
        "machines": [],
        "cells": [],
        "last_check": None,
    }
    row.update(kw)
    return row


def _wf(wid: str, name: str, status: str, routes: list[str], reason: str = "", **kw: Any) -> dict:
    row = {
        "id": wid,
        "name": name,
        "inferred_name": True,
        "kind": "effect",
        "status": status,
        "effective": status,
        "reason": reason,
        "routes": routes,
        "rules": ["R2"],
        "orphan_routes": [],
        "implemented_not_required": False,
        "spec_matches": {"strict": [], "lenient": []},
        "machine_refs": [],
        "evidence": [],
        "basis": "observed" if status == "verified" else "static",
        "steps": [
            {"i": i, "method": r.split()[0], "route": r, "caller": "ann", "expect_status": 200,
             "evidence": []}
            for i, r in enumerate(routes)
        ],
    }  # fmt: skip
    row.update(kw)
    return row


def make_map() -> am.AppMap:
    rules = [
        _rule("R1", "A user signs in.", "implemented_verified"),
        _rule("R2", RULE_TEXT, "implemented_unverified", workflows=[WF_FAILED, WF_OK]),
        _rule("R3", "A project has at most 50 tickets.", "implemented_unverified"),
        _rule("R4", "Tickets can be exported.", "required_not_implemented",
              missing=["impl:route:GET /v1/export"]),
        _rule("R5", "The service is for small teams.", "no_element"),
        _rule("R6", "A job can be retried.", "required_not_implemented"),
    ]  # fmt: skip
    workflows = [
        _wf(WF_FAILED, "create ticket > read ticket", "failed",
            ["POST /v1/tickets", "GET /v1/tickets/{}"],
            "step 1 GET /v1/tickets/{id} answered 404 (not_found): status 404",
            machine_refs=["wm-001"]),
        _wf(WF_OK, "create ticket", "verified", ["POST /v1/tickets"]),
        _wf(WF_UNVER, "login", "unverified", ["POST /v1/login"],
            'replay failed at step 0: ["status: expected 200, got 401"]'),
    ]  # fmt: skip
    machines = [
        {
            "key": "impl:machine:jobs.delivery",
            "id": "wm-001",
            "states": ["failed", "pending", "sent"],
            "edges": ["pending -> failed", "pending -> sent"],
            "status": "static",
            "rules": ["R2"],
            "implemented_not_required": False,
            "evidence": ["code:jobs.go:10", "code:jobs.go:20"],
            "spec_target": "machine:Job.delivery",
            "edges_detail": [
                {"from": "pending", "to": "failed", "in_spec": True, "in_code": True},
                {"from": "pending", "to": "sent", "in_spec": True, "in_code": True},
            ],
        }
    ]
    endpoints = [
        {"key": "POST /v1/tickets", "status": "implemented", "in_spec": True, "in_code": True},
        {"key": "GET /v1/tickets/{}", "status": "implemented", "in_spec": True, "in_code": True},
        {"key": "GET /v1/export", "status": "spec_only", "in_spec": True, "in_code": False},
        {"key": "POST /v1/login", "status": "implemented", "in_spec": True, "in_code": True},
    ]
    cells = []
    for row in ("ann", "ben", "no token"):
        for col, spec, route_status in (
            ("POST /v1/tickets", "201/401" if row != "no token" else "401", "implemented"),
            ("GET /v1/export", "200", "spec_only"),
        ):
            built = route_status == "implemented"
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "spec": spec,
                    "route": col if built else None,
                    "auth": "Customer" if built else None,
                    "refusals": ["401 not_authenticated", "422 validation_failed"]
                    if built
                    else None,
                    "route_status": route_status,
                    "observed": (
                        [{"flow": WF_OK, "step": 0, "status": 201}]
                        if built and row == "ann"
                        else []
                    ),
                    "evidence": [],
                }
            )
    cells.append({"row": "ann", "col": "GET /health", "spec": "·", "route": "GET /health",
                  "auth": "Public", "refusals": [], "route_status": "implemented",
                  "observed": [], "evidence": []})  # fmt: skip
    doc = {
        "schema": am.SCHEMA,
        "state": "ok",
        "inputs": {},
        "summary": {},
        "rules": rules,
        "workflows": workflows,
        "machines": machines,
        "access": {
            "groups": [
                {
                    "prefix": "v1",
                    "rows": ["ann", "ben", "no token"],
                    "columns": ["POST /v1/tickets", "GET /v1/export"],
                }
            ],
            "cells": cells,
        },  # fmt: skip
        "endpoints": endpoints,
        "excluded": [],
        "unread": [],
    }
    report = {
        "routes": [
            {"key": "POST /v1/tickets", "auth": "Customer", "refusals": [], "rules": ["R2"],
             "flows": [WF_FAILED, WF_OK], "evidence": ["code:tickets.go:12", "code:tickets.go:30"]},
            {"key": "GET /v1/tickets/{}", "auth": "Customer", "refusals": [], "rules": ["R2"],
             "flows": [WF_FAILED], "evidence": ["code:tickets.go:40"]},
            {"key": "POST /v1/login", "auth": "Public", "refusals": [], "rules": ["R1"],
             "flows": [WF_UNVER], "evidence": ["code:auth.go:9"]},
        ]
    }  # fmt: skip
    return am.AppMap(doc, report)


def make_digest() -> ask.Digest:
    els = {
        f"rule:{r}": ask.Element(f"rule:{r}", "rule", text)
        for r, text in (("R1", "A user signs in."), ("R2", RULE_TEXT),
                        ("R3", "A project has at most 50 tickets."),
                        ("R4", "Tickets can be exported."), ("R5", "The service is for small teams."),
                        ("R6", "A job can be retried."))
    }  # fmt: skip
    return ask.Digest("", els, (), True)


def _item(iid: str, tier: int, **kw: Any) -> dict[str, Any]:
    return {"id": iid, "tier": tier, **kw}


def body_c1() -> dict[str, Any]:
    """c1: one tier 1 item (a saved, hash-matched observed report), two tier 2 items, two tier 3."""
    report = {
        "schema": "lattice.impact/1",
        "input": {"change": "c1"},
        "tiers": {"1": "run", "2": "run", "3": "run", "4": "not_run:spike_e"},
        "diff": {"rules": {"added": [], "changed": [{"id": "R3"}], "removed": []}},
        "tier1": {"failing": [], "flows": []},
        "tier2": {
            "determined": [
                _item(
                    T2,
                    2,
                    kind="scenario_fails",
                    target="scenario:ticket_limit",
                    claim="scenario:ticket_limit fails at step 2",
                    rules=["R3"],
                )
            ],  # fmt: skip
            "flows": [
                _item(
                    T2B,
                    2,
                    kind="flow_fails_after_change",
                    target=f"flow:{WF_OK}",
                    claim=f"flow:{WF_OK} fails at step 0 after the change",
                    rules=["R3"],
                )
            ],  # fmt: skip
        },
        "tier3": {
            "locations": [
                _item(
                    T3,
                    3,
                    kind="table_peer",
                    target="invariant:at_most_50_tickets",
                    what="route on a changed table",
                    file="tickets.go",
                    line=12,
                    rules=["R3"],
                )
            ],  # fmt: skip
            "flows": [
                _item(
                    T3B,
                    3,
                    kind="may_fail",
                    target=f"flow:{WF_FAILED}",
                    routes=["POST /v1/tickets"],
                    rules=["R3"],
                )
            ],  # fmt: skip
        },
    }
    tier1 = {
        "failing": [
            _item(
                T1,
                1,
                kind="check_failure",
                target="rule:R3",
                summary="rule R3 case 51 answered 201",
                rules=["R3"],
            )
        ],  # fmt: skip
        "flows": [],
    }
    return {
        "schema": "lattice.studio-impact/1",
        "change": "c1",
        "state": "ok",
        "why": None,
        "report": report,
        "studio_check": None,
        "tier1_saved": {"state": "shown", "differs": [], "tier1": tier1},
    }


def body_c2() -> dict[str, Any]:
    """c2: a change with a report and nothing at tier 1 or 2 (a rewording), one tier 3 item."""
    report = {
        "schema": "lattice.impact/1",
        "input": {"change": "c2"},
        "tiers": {"1": "not_run:not_requested", "2": "run", "3": "run", "4": "not_run:spike_e"},
        "diff": {"rules": {"added": [], "changed": [], "removed": []}},
        "tier1": {"failing": [], "flows": []},
        "tier2": {"determined": [], "flows": []},
        "tier3": {"locations": [], "flows": []},
    }
    return {
        "schema": "lattice.studio-impact/1",
        "change": "c2",
        "state": "ok",
        "why": None,
        "report": report,
        "studio_check": None,
        "tier1_saved": {"state": "not_configured", "differs": [], "tier1": None},
    }


def body_c3() -> dict[str, Any]:
    """c3: another change, with a tier 2 item of its own (the cross-change fixture)."""
    b = body_c1()
    b["change"] = "c3"
    b["report"]["input"]["change"] = "c3"
    t2 = b["report"]["tier2"]["determined"][0]
    t2["id"] = T2_OTHER
    t2["claim"] = "scenario:other fails at step 1"
    t2["target"] = "scenario:other"
    b["report"]["tier2"]["flows"] = []
    b["tier1_saved"] = {"state": "not_configured", "differs": [], "tier1": None}
    return b


BODIES = {"c1": body_c1, "c2": body_c2, "c3": body_c3}


def lookup(cid: str) -> dict[str, Any]:
    if cid in BODIES:
        return BODIES[cid]()
    return {"schema": "lattice.studio-impact/1", "change": cid, "state": "no_change",
            "why": "there is no such change", "report": None, "studio_check": None,
            "tier1_saved": {"state": "not_configured", "differs": [], "tier1": None}}  # fmt: skip


def ctx(question: str = "", **kw: Any) -> cc.CheckContext:
    kw.setdefault("lookup", lookup)
    kw.setdefault("appmap", make_map())
    kw.setdefault("digest", make_digest())
    return cc.CheckContext(question, **kw)
