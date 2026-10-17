"""`lattice.spec.impact_ask`: the model-free answer to "what breaks if ..." and the Studio view of
a change's impact (docs/results/2026-10-16-goto-place-prereg.md, D3 and D5, Amendment 2).

**The binding rule.** A workflow failure is observed or logically determined, never predicted. Every
failure statement the Studio shows traces to a tier 1 (observed) or tier 2 (determined) item of a
``lattice.impact/1`` report (Spike D); anything else is "static: may fail" or "not decided".

Two parts, both pure (no model, no process, no write; the report is built by Spike D's
:mod:`lattice.spec.impact_report` exactly as its ``change_impact`` tool does by default, tiers 2 and
3, ``observe`` off, and is never edited here):

* :func:`impact_view` -- ``GET /api/impact``: ``lattice.studio-impact/1``, D's report verbatim, the
  change record's own ``checks.impact`` (labelled, never relabelled as tier 1), and a saved tier 1
  report that is shown **only if** its input hashes equal what D computes now.
* :func:`route` -- the one-shot ``POST /api/ask`` router. It matches two frozen patterns (A: a
  change id and an impact word; B: a hypothetical) and answers with a fixed template and the
  report's items. A question that matches neither returns ``None`` and follows the old path
  untouched.

The regular expressions below are frozen with this module's first commit (their hash is recorded in
``docs/results/data/2026-10-16-goto-place/freeze.sha256``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lattice.spec import appmap as _appmap
from lattice.spec import impact_report as ir
from lattice.spec import implementation as impl

SCHEMA = "lattice.studio-impact/1"
IMPACT_STATES = (
    "ok",
    "impact_not_configured",
    "no_change",
    "change_not_ready",
    "no_state",
    "inventory_missing",
    "error",
)
TIER1_STATES = ("shown", "mismatch", "absent", "not_configured")
CHANGE_RE = re.compile(r"^c[0-9]{1,6}$")
TIER3_CAP = 20
SECTIONS = ir.SECTIONS


def refuse_change(change: str | None) -> tuple[str, str] | None:
    """(detail, code) of a 422 for a missing or malformed change id, else None."""
    if not isinstance(change, str) or not CHANGE_RE.match(change):
        return "change must match ^c[0-9]{1,6}$ (a Studio change id)", "bad_change"
    return None


def refuse_section(section: str | None) -> tuple[str, str] | None:
    """(detail, code) of a 422 for a bad ``section``, else None."""
    if section is not None and section not in SECTIONS:
        return f"section must be one of {', '.join(SECTIONS)}", "bad_section"
    return None


# ---- the frozen patterns (R5; case-insensitive by Amendment 2) -----------------------------------

CHANGE_ID = re.compile(r"\bc\d+\b", re.IGNORECASE)
IMPACT_WORD = re.compile(
    r"\b(break|breaks|broken|fail|fails|failing|impact|affect)\b", re.IGNORECASE
)
HYPOTHETICAL = (
    re.compile(r"\bwhat\s+(would\s+|will\s+)?(break|fail)s?\b.*\bif\b", re.IGNORECASE),
    re.compile(r"\bif\s+we\b.*\b(break|fail)s?\b", re.IGNORECASE),
    re.compile(r"^\s*what\s+breaks\b", re.IGNORECASE),
)

NO_PREDICTION = (
    "Lattice does not predict failures. It decides them only for a drafted change (tier 1 "
    "observed, tier 2 determined). Draft this change, then ask about its id."
)
_STATIC_NOTE = (
    "Static: these elements reference the ones you named; whether anything fails is not decided."
)
_WHY = {
    "no_change": "there is no such change",
    "change_not_ready": "the change is not ready",
    "no_state": "durable state is off, so no change record is kept",
    "inventory_missing": "no implementation inventory can be read",
    "error": "the report could not be built",
    "impact_not_configured": "studio.toml has no [impact] table",
}
_MACHINE = re.compile(r"\bmachine:([A-Za-z_]\w*)\.([A-Za-z_]\w*)")
_RULE = re.compile(r"(?<![\w:])(?:rule:)?(R\d+)\b")
_FLOW = re.compile(r"\bwf-[0-9a-f]+\b")


# ---- GET /api/impact -----------------------------------------------------------------------------


def saved_tier1(
    reports: Path | None, change: str, live: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The saved tier 1 report ``reports/<change>.json``, shown only if its input hashes equal the
    ones D computes now for that change and tier 1 ran. ``state``: ``shown``, ``mismatch`` (with
    ``differs``), ``absent`` or ``not_configured``. Reads one file; writes nothing."""
    out: dict[str, Any] = {"state": "not_configured", "differs": [], "tier1": None}
    if reports is None:
        return out
    path = Path(reports) / f"{change}.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {**out, "state": "absent"}
    except OSError:
        return {**out, "state": "mismatch", "differs": ["unreadable"]}
    try:
        saved = json.loads(raw)
        saved_in = saved["input"]
        assert saved["schema"] == ir.SCHEMA
        saved_tier1_doc = saved["tier1"]
        saved_tiers = saved["tiers"]
    except (ValueError, KeyError, TypeError, AssertionError):
        return {**out, "state": "mismatch", "differs": ["not_a_report"]}
    if live is None:
        return {**out, "state": "mismatch", "differs": ["no_report_now"]}
    now = live["input"]
    inv_s, inv_n = saved_in.get("inventory") or {}, now.get("inventory") or {}
    differs = [
        name
        for name, same in (
            ("base_sha256", saved_in.get("base") == now.get("base")),
            ("candidate_sha256", saved_in.get("candidate") == now.get("candidate")),
            ("extract_sha256", inv_s.get("extract_sha256") == inv_n.get("extract_sha256")),
            ("workflows_sha256", inv_s.get("workflows_sha256") == inv_n.get("workflows_sha256")),
        )
        if not same
    ]
    if saved_tiers.get("1") != "run":
        differs.append("tier1_not_run")
    if differs:
        return {**out, "state": "mismatch", "differs": differs}
    return {"state": "shown", "differs": [], "tier1": saved_tier1_doc}


def _scrub(message: str) -> str:
    return _appmap._scrub(message)


def impact_view(
    *,
    project: Path,
    state_dir: Path | None,
    change: str,
    configured: bool,
    studio_check: Mapping[str, Any] | None = None,
    reports: Path | None = None,
) -> dict[str, Any]:
    """The body of ``GET /api/impact`` for `change` (``lattice.studio-impact/1``, whole report).

    Spike D's report is built **in process** with the arguments and mode of ``change_impact``'s
    default: ``build_inputs(project, change=, state_dir=)`` then ``build_report(tiers="23",
    runner=None, baseline="none")``. Tier 1 is never run: no app, no container, no subprocess."""
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "change": change,
        "state": "ok",
        "why": None,
        "report": None,
        "studio_check": dict(studio_check) if studio_check is not None else None,
        "tier1_saved": {"state": "not_configured", "differs": [], "tier1": None},
    }

    def fail(state: str, why: str | None = None) -> dict[str, Any]:
        body["state"] = state
        body["why"] = _scrub(why) if why else _WHY.get(state)
        body["tier1_saved"] = saved_tier1(reports, change, None)
        return body

    if not configured:
        return fail("impact_not_configured")
    if state_dir is None:
        return fail("no_state")
    try:
        inp = ir.build_inputs(project=project, change=change, state_dir=state_dir)
    except ir.ImpactError as err:
        if err.code == "no_candidate":
            return fail("no_change")
        if err.code == "change_not_ready":
            return fail("change_not_ready", err.detail)
        return fail("error", str(err))
    if inp.inventory is None:
        return fail("inventory_missing", inp.inventory_error)
    try:
        report = ir.build_report(inp, tiers="23", runner=None, baseline="none")
    except ir.ImpactError as err:
        return fail("error", str(err))
    body["report"] = report
    body["tier1_saved"] = saved_tier1(reports, change, report)
    return body


def with_section(body: dict[str, Any], section: str | None) -> dict[str, Any]:
    """`body` with ``report`` narrowed to one section of D's report (``section_of``)."""
    if section is None or body["report"] is None:
        return body
    return {**body, "report": ir.section_of(body["report"], section)}


# ---- POST /api/ask: the router -------------------------------------------------------------------


def _usage() -> dict[str, Any]:
    return {"seconds": 0, "tokens": 0}


def _answer(
    kind: str,
    answer: str,
    explanation: str,
    citations: list[dict[str, Any]],
    impact: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "answer": answer,
        "explanation": explanation,
        "citations": citations,
        "citations_checked": True,
        "rejected_citations": [],
        "verified": None,
        "usage": _usage(),
        "impact": impact,
    }


def is_routed(question: str) -> bool:
    """Whether the frozen patterns match (A or B)."""
    if CHANGE_ID.search(question) and IMPACT_WORD.search(question):
        return True
    return any(p.search(question) for p in HYPOTHETICAL)


def named_elements(am: _appmap.AppMap | None, question: str) -> list[str]:
    """The map's keys of every element the question names exactly (``METHOD /path``, ``Rn`` or
    ``rule:Rn``, ``wf-...``, ``machine:T.c``) that the map has, in order, once each."""
    if am is None or am.state != "ok":
        return []
    keys = _appmap.element_keys(am)
    have = set(keys)
    found: list[str] = []
    for k in impl.routes_in_text(question):
        found.append(f"impl:route:{k}")
    for m in _RULE.finditer(question):
        found.append(f"rule:{m.group(1)}")
    found += _FLOW.findall(question)
    for t, c in _MACHINE.findall(question):
        direct = f"impl:machine:{t}.{c}"
        if direct in have:
            found.append(direct)
            continue
        for m in am.doc["machines"]:
            if m["spec_target"] == f"machine:{t}.{c}":
                found.append(m["key"])
    out: list[str] = []
    for k in found:
        if k in have and k not in out:
            out.append(k)
    return out


def _items(report: Mapping[str, Any], saved: Mapping[str, Any]) -> dict[str, Any]:
    """The `impact` key of an answer for an ok report: the change's tiers, tier 1 and 2 items
    verbatim, tier 3 capped with a total."""
    t1: Mapping[str, Any] = (
        saved["tier1"] if saved.get("state") == "shown" and saved.get("tier1") else {}
    )
    t2, t3 = report["tier2"], report["tier3"]
    return {
        "change": report["input"]["change"],
        "state": "ok",
        "tiers": dict(report["tiers"]),
        "tier1": {
            "state": "shown" if t1 else "not_run",
            "failing": list(t1.get("failing", [])),
            "flows": list(t1.get("flows", [])),
        },
        "tier2": {"determined": list(t2["determined"]), "flows": list(t2["flows"])},
        "tier3": {
            "locations": list(t3["locations"][:TIER3_CAP]),
            "flows": list(t3["flows"][:TIER3_CAP]),
            "total": {"locations": len(t3["locations"]), "flows": len(t3["flows"])},
        },
        "tier4": "not_run",
    }


def _cite(item: Mapping[str, Any]) -> dict[str, Any]:
    quote = item.get("message") or item.get("label") or item.get("target") or ""
    return {"target": f"impact:{item['id']}", "step": None, "quote": str(quote)}


def _tier_template(impact: Mapping[str, Any]) -> str:
    t1 = impact["tier1"]
    first = (
        f"{len(t1['failing']) + len(t1['flows'])} failing"
        if t1["state"] == "shown"
        else "not run here"
    )
    t2 = impact["tier2"]
    t3 = impact["tier3"]["total"]
    return (
        f"Tier 1 (observed): {first}; Tier 2 (determined): "
        f"{len(t2['determined']) + len(t2['flows'])}; Tier 3 (static, may fail): "
        f"{t3['locations']} locations, {t3['flows']} workflows; Tier 4: not run."
    )


def route(
    question: str,
    *,
    lookup: Callable[[str], Mapping[str, Any]],
    appmap: Callable[[], _appmap.AppMap | None] | None = None,
) -> dict[str, Any] | None:
    """The routed answer to `question`, or ``None`` (not routed: the old path answers).

    `lookup(change_id)` returns the whole ``lattice.studio-impact/1`` body (see
    :func:`impact_view`); `appmap()` the app map for the static neighbourhood of a hypothetical.
    A routed answer calls no model (``usage.tokens`` is 0)."""
    cid_m = CHANGE_ID.search(question)
    if cid_m and IMPACT_WORD.search(question):
        cid = cid_m.group(0).lower()
        if not CHANGE_RE.match(cid):
            body: Mapping[str, Any] = {"state": "no_change", "why": _WHY["no_change"]}
        else:
            body = lookup(cid)
        if body["state"] != "ok":
            why = body.get("why") or _WHY.get(body["state"], "")
            return _answer(
                "not_specified",
                f"Lattice has no impact report for {cid}: {why}. Nothing is decided.",
                "A report exists only for a drafted change that is ready or accepted.",
                [],
                {"change": cid, "state": body["state"], "tiers": None},
            )
        impact = _items(body["report"], body["tier1_saved"])
        cites = [_cite(i) for i in [*impact["tier1"]["failing"], *impact["tier1"]["flows"]]]
        cites += [_cite(i) for i in [*impact["tier2"]["determined"], *impact["tier2"]["flows"]]]
        return _answer(
            "value",
            _tier_template(impact),
            "Tier 1 and tier 2 items are listed under impact with their ids. Tier 3 is static: "
            "it names code and workflows the change touches and decides nothing. Tier 4 is "
            "not run.",
            cites,
            impact,
        )
    if any(p.search(question) for p in HYPOTHETICAL):
        am = appmap() if appmap is not None else None
        names = named_elements(am, question)
        touched = {k: _appmap.touches(am, k) for k in names} if am is not None else {}
        explain = (
            f"{_STATIC_NOTE} They are listed under impact.touches."
            if touched
            else "No element was named exactly (METHOD /path, Rn, wf-... or machine:T.c), so "
            "there is no static neighbourhood to list."
        )
        return _answer(
            "not_specified",
            NO_PREDICTION,
            explain,
            [],
            {
                "change": None,
                "state": "no_change",
                "tiers": None,
                "touches": touched,
                "basis": "static",
                "meaning": _appmap.MEANING,
            },
        )
    return None


__all__ = [
    "CHANGE_ID",
    "HYPOTHETICAL",
    "IMPACT_STATES",
    "IMPACT_WORD",
    "NO_PREDICTION",
    "SCHEMA",
    "SECTIONS",
    "impact_view",
    "is_routed",
    "named_elements",
    "route",
    "saved_tier1",
    "with_section",
]
