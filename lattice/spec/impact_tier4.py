"""`lattice.spec.impact_tier4`: Tier 4 of the change-impact report, *observed after the change*
(docs/results/2026-10-17-implement-verify-prereg.md, D2, D4-D7; driver ``lattice spec implement``
in :mod:`lattice.studio.implement`).

This module is pure: it reads the Spike D report, two check runs of the candidate spec plus the
verified flows (``before`` on the base code, ``after`` on the code a rebuild produced) and the
rebuild's outcome, and answers what the change did. It runs nothing, calls no model and writes
nothing. Every statement of Tier 4 comes from a check that ran on the rebuilt code (the owner's
rule: a workflow failure is observed or logically determined, never predicted).

A *run* is ``{"findings": [F...], "flows_run": [flow id...]}`` where ``F`` is a check finding as a
dict (``layer``, ``verdict``, ``rule``, ``endpoint``, ``case``, ``code``, ``summary``, ``details``,
``request``) and ``F["flow"]`` is the id of the flow it is about (None: about the spec itself).

Classes of candidate checks: ``resolved`` (failing before, passing after), ``still_failing`` (both)
and ``regression`` (passing before, failing after). Flows: ``flow_unchanged``, ``flow_changed``
(passes before, a D finding after; ``reported_as`` ``t2_determined`` | ``t3_may_fail`` |
``unreported``; ``spec_backed``), ``workflow_fails`` (a C finding after, absent before) and
``flow_before_failed`` (listed, not judged). Verdicts: ``pass``, ``needs_owner``, ``fail``,
``not_built``, ``nothing_to_build``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from lattice.spec.impact_report import SCHEMA, stamp

SPEC_FILES = ("REQUIREMENTS.md", "spec.py", "spec.trace.json")
VERDICTS = ("pass", "needs_owner", "fail", "not_built", "nothing_to_build")
UNEXPECTED = ("regression", "unreported", "workflow_fails")


class Refusal(Exception):
    """An input `lattice spec implement` refuses (exit 2). `code`: ``bad_report``,
    ``report_mismatch``, ``stale_inventory``, ``spec_contradiction``, ``not_a_repo``,
    ``no_rebuild``, ``branch_exists``, ``branch_protected``, ``branch_checked_out``,
    ``change_not_ready``, ``bad_paths``, ``bad_inputs``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = message


# ---- the report as an input (D2) --------------------------------------------------------------


def eid_of(base: Mapping[str, Any], cand: Mapping[str, Any]) -> str:
    """``i`` + the first 10 hex digits of sha256 of the base and candidate file hashes."""
    parts = []
    for side in (base, cand):
        parts += [str(side.get(k)) for k in ("requirements", "spec", "trace")]
    return "i" + hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:10]


def check_report(
    report: Any,
    *,
    base: Mapping[str, Any],
    cand: Mapping[str, Any],
    inventory: Mapping[str, Any] | None,
    inventory_stale: bool = False,
    flows_sha256: str | None = None,
) -> None:
    """Raise :class:`Refusal` unless `report` is a ``lattice.impact/1`` report for exactly these
    inputs (`base`, `cand`: the file hashes E reads; `inventory`: ``{extract_sha256,
    workflows_sha256}`` of the inventory E loads, None without one) with Tier 4 still unset and no
    candidate that contradicts itself (an L011 pair new in the candidate)."""
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise Refusal("bad_report", f"the report is not a {SCHEMA} report")
    if report.get("tier4") is not None:
        raise Refusal("bad_report", "tier4 is already set in the report")
    if not isinstance(report.get("tiers"), dict) or "input" not in report:
        raise Refusal("bad_report", "the report has no tiers or input section")
    inp = report["input"]
    if inp.get("base") != dict(base):
        raise Refusal("report_mismatch", "the base files differ from the ones the report names")
    if inp.get("candidate") != dict(cand):
        raise Refusal(
            "report_mismatch", "the candidate files differ from the ones the report names"
        )
    rec = inp.get("inventory")
    if rec is not None:
        if inventory is None:
            raise Refusal("report_mismatch", "the report names an inventory and none was given")
        for key in ("extract_sha256", "workflows_sha256"):
            if rec.get(key) != inventory.get(key):
                raise Refusal("report_mismatch", f"the inventory's {key} differs from the report's")
        if inventory_stale or rec.get("stale"):
            raise Refusal("stale_inventory", "the inventory is stale: its flows are not verified")
    elif inventory is not None and inventory_stale:
        raise Refusal("stale_inventory", "the inventory is stale: its flows are not verified")
    for item in (report.get("tier2") or {}).get("determined") or []:
        if item.get("kind") == "spec_contradiction":
            raise Refusal(
                "spec_contradiction",
                "the candidate contradicts itself (L011): no app can pass it, nothing to build",
            )


# ---- findings and idents ----------------------------------------------------------------------


def ident(f: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    """The identity of a finding or of a report T1 item. D's ``_ident`` is ``(layer, rule, case,
    key)``; ``key`` is a function of ``layer``, ``endpoint`` and ``case`` (layer D, C) or of the
    code and message (layer A), and a report item carries no ``key``, so E uses the fields both
    shapes share."""
    return (
        str(f.get("layer")),
        str(f.get("rule")),
        str(f.get("case") or ""),
        str(f.get("endpoint") or ""),
        str(f.get("code") or ""),
    )


def _own(run: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [f for f in run.get("findings") or [] if not f.get("flow")]


def _of_flow(run: Mapping[str, Any], fid: str) -> list[Mapping[str, Any]]:
    return [f for f in run.get("findings") or [] if f.get("flow") == fid]


def failing_set(run: Mapping[str, Any]) -> set[tuple[str, str, str, str, str]]:
    return {ident(f) for f in _own(run)}


def target_of(f: Mapping[str, Any]) -> str | None:
    """The spec element a finding is about (``scenario:`` for D, ``invariant:`` for C)."""
    details = f.get("details") or {}
    if f.get("layer") == "D":
        return "scenario:" + str(f.get("rule")).removeprefix("scenario: ")
    if f.get("layer") == "C" and details.get("invariant"):
        return f"invariant:{details['invariant']}"
    return None


def matches_report(report: Mapping[str, Any], before: Mapping[str, Any]) -> bool | None:
    """Whether E's before run fails exactly where the report's Tier 1 says (None: Tier 1 was not
    run). Compared: the idents of the spec's own failing checks, and the (kind, flow) of the
    flows' D and C findings, ``baseline_drift`` and ``workflow_fails``."""
    if (report.get("tiers") or {}).get("1") != "run":
        return None
    t1 = report.get("tier1") or {}
    want = {ident(i) for i in t1.get("failing") or []}
    if want != failing_set(before):
        return False
    inv = (report.get("input") or {}).get("inventory")
    if inv is None:
        # the report ran no flow (no inventory): only the spec's own checks are comparable
        return True
    want_flows = {(str(i.get("kind")), str(i.get("flow"))) for i in t1.get("flows") or []}
    got: set[tuple[str, str]] = set()
    for fid in before.get("flows_run") or []:
        fs = _of_flow(before, str(fid))
        if any(f.get("layer") == "D" for f in fs):
            got.add(("baseline_drift", str(fid)))
        if any(f.get("layer") == "C" for f in fs):
            got.add(("workflow_fails", str(fid)))
    return want_flows == got


# ---- spec_backed (D6) -------------------------------------------------------------------------


def spec_backed(
    flow: Mapping[str, Any],
    finding: Mapping[str, Any] | None,
    cand_steps: Sequence[Mapping[str, Any]],
    after_failing: set[tuple[str, str, str, str, str]],
    route_key: Any,
) -> tuple[bool | None, str | None]:
    """(spec_backed, why). True when the flow's first failing step has a candidate scenario step
    with the same method, route key and user (None: no token) whose ``expect_status`` equals the
    status observed after, and that step passes after. None (counted as false) when the observed
    status is not in the finding's details."""
    if finding is None:
        return None, "no finding for the flow"
    m = re.search(r"/ step (\d+)$", str(finding.get("case") or ""))
    steps = [s for s in flow["steps"]]
    if not m or not 1 <= int(m.group(1)) <= len(steps):
        return None, "the failing step of the flow cannot be read from the finding"
    step = steps[int(m.group(1)) - 1]
    if step.get("kind") == "await":
        return None, "the failing step is an await"
    actual = (finding.get("details") or {}).get("actual")
    status = actual.get("status") if isinstance(actual, Mapping) else None
    if not isinstance(status, int):
        return None, "the status observed after is not in the finding's details"
    want_key = route_key(step["method"], step["path"])
    failed_cases = {i[2] for i in after_failing if i[0] == "D"}
    for cs in cand_steps:
        if (
            str(cs["method"]).upper() == str(step["method"]).upper()
            and route_key(cs["method"], cs["path"]) == want_key
            and (cs.get("user") or None) == (step.get("caller") or None)
            and cs.get("expect_status") == status
            and f"{cs['scenario']} / step {cs['k']}" not in failed_cases
        ):
            return True, None
    return False, None


# ---- t3_vs_edit (D6; report-only) -------------------------------------------------------------

_TEST_FILE = re.compile(
    r"(^|/)(tests?|__tests__|testdata)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|(^|/)conftest\.py$"
)


def edit_files(
    names: Sequence[str], *, project_rel: str = ".", test_only: Sequence[str] = ()
) -> list[str]:
    """The files of an edit that count for ``t3_vs_edit``: not a test file, not a spec file, not a
    ``test_only_files`` entry."""
    pre = "" if project_rel in (".", "") else project_rel.rstrip("/") + "/"
    skip = {pre + n for n in (*SPEC_FILES, *test_only)}
    return sorted({n for n in names if n not in skip and not _TEST_FILE.search(n)})


def t3_vs_edit(
    report: Mapping[str, Any],
    edit: Mapping[str, Any],
    *,
    tree: Any = None,
    prefix: str = "",
) -> dict[str, Any]:
    """T3's location files against the files the edit touched (recall = the share of the edit's
    files T3 listed, precision = the share of T3's files the edit touched, ``missed`` = the edit's
    files T3 did not list); declarations when `tree` (an ``impact_tier3.GoTree``) is given.
    Report-only. `prefix`: what to strip from an edit path to get a path under the app root."""
    if (report.get("tiers") or {}).get("3") != "run":
        return {"status": "not_applicable: tier3 not run", "files": None, "declarations": None}
    touched = sorted(
        {
            p[len(prefix) :] if prefix and p.startswith(prefix) else p
            for p in edit.get("files") or []
        }
    )
    listed = sorted({str(i["file"]) for i in (report.get("tier3") or {}).get("locations") or []})
    hit = sorted(set(touched) & set(listed))
    files: dict[str, Any] = {
        "touched": touched,
        "listed": listed,
        "hit": hit,
        "recall": _ratio(len(hit), len(touched)),
        "precision": _ratio(len(hit), len(listed)),
        "missed": [
            stamp(
                4,
                {
                    "kind": "t3_missed",
                    "target": None,
                    "rules": [],
                    "file": f,
                    "evidence": [
                        {
                            "kind": "check",
                            "at": f"edit:{f}",
                            "what": "the edit touched it; tier 3 did not list it",
                        }
                    ],
                },
            )
            for f in sorted(set(touched) - set(listed))
        ],
    }
    decl: dict[str, Any] | str
    if tree is None:
        decl = "not_applicable: no extract"
    else:
        touched_d: set[tuple[str, str]] = set()
        hunks = edit.get("hunks") or {}
        for path in touched:
            full = prefix + path
            if not path.endswith(".go"):
                continue
            for start, count in hunks.get(full, hunks.get(path, [])):
                lo, hi = start, start + max(count, 1) - 1
                for d in tree.decls(path):
                    if d.start <= hi and lo <= d.end:
                        touched_d.add((path, f"{d.recv + '.' if d.recv else ''}{d.name}"))
        listed_d: set[tuple[str, str]] = set()
        for i in (report.get("tier3") or {}).get("locations") or []:
            d = tree.enclosing(str(i["file"]), int(i["line"]))
            if d is not None:
                listed_d.add((str(i["file"]), f"{d.recv + '.' if d.recv else ''}{d.name}"))
        hit_d = touched_d & listed_d
        decl = {
            "touched": sorted(f"{a}:{b}" for a, b in touched_d),
            "listed": sorted(f"{a}:{b}" for a, b in listed_d),
            "recall": _ratio(len(hit_d), len(touched_d)),
            "precision": _ratio(len(hit_d), len(listed_d)),
        }
    return {"status": "run", "files": files, "declarations": decl}


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


# ---- the change handed to the loop (D5) -------------------------------------------------------


def change_dict(
    eid: str,
    report: Mapping[str, Any],
    *,
    request: str | None,
    markdown_diff: str,
    before: Mapping[str, Any],
) -> dict[str, Any]:
    """The change record `Rebuilder.start` takes, in memory (never written to a state dir)."""
    rules = (report.get("diff") or {}).get("rules") or {}
    added, changed, removed = (rules.get(k) or [] for k in ("added", "changed", "removed"))
    if not request:
        request = (
            f"Implement the candidate spec change: {len(added)} rule(s) added, "
            f"{len(changed)} changed, {len(removed)} removed."
        )
    failing = [
        {"target": target_of(f), "label": "not_built_yet", "message": f.get("summary")}
        for f in _own(before)
        if f.get("layer") in ("C", "D") and target_of(f)
    ]
    return {
        "id": eid,
        "request": request,
        "status": "accepted",
        "rules": {
            "added": [a["id"] for a in added],
            "changed": [{"old": c["old"], "new": c["new"]} for c in changed],
            "removed": [r["id"] for r in removed],
        },
        "markdown_diff": markdown_diff,
        "checks": {"impact": {"verdict": "fail", "failing": failing}},
    }


# ---- the section ------------------------------------------------------------------------------


def _item(
    kind: str, f: Mapping[str, Any], links: Mapping[str, Sequence[str]], **extra: Any
) -> dict[str, Any]:
    target = target_of(f)
    rules = sorted(
        {r for r, ts in links.items() if target and target in ts},
        key=lambda r: (int(r[1:]) if r[1:].isdigit() else 0, r),
    )
    return stamp(
        4,
        {
            "kind": kind,
            "target": target,
            "rules": rules,
            "layer": f.get("layer"),
            "rule": f.get("rule"),
            "case": f.get("case"),
            "endpoint": f.get("endpoint"),
            "summary": f.get("summary"),
            "evidence": [
                {
                    "kind": "check",
                    "at": f"check:{f.get('layer')}:{f.get('case') or f.get('endpoint') or ''}",
                    "what": f.get("summary"),
                }
            ],
            **extra,
        },
    )


def _sorted(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda i: (
            str(i.get("kind")),
            str(i.get("target") or ""),
            str(i.get("case") or ""),
            str(i["id"]),
        ),
    )


def compute(
    report: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    flows: Sequence[Mapping[str, Any]],
    rebuild: Mapping[str, Any],
    edit: Mapping[str, Any],
    *,
    cand_steps: Sequence[Mapping[str, Any]] = (),
    links: Mapping[str, Sequence[str]] | None = None,
    route_key: Any = None,
    implementation: Mapping[str, Any] | None = None,
    owner: Mapping[str, Any] | None = None,
    tree: Any = None,
    prefix: str = "",
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    """Tier 4 (``tier4`` of the report) for a rebuild that passed. `cand_steps`: the candidate
    spec's steps ``{scenario, k, method, path, user, expect_status}`` (``k`` from 1, awaits
    counted); `links`: the candidate trace's rule -> targets; `route_key`: ``implementation.
    route_key``. Pure."""
    links = links or {}
    if route_key is None:
        from lattice.spec.implementation import route_key as route_key
    bset, aset = failing_set(before), failing_set(after)
    b_own = {ident(f): f for f in _own(before)}
    a_own = {ident(f): f for f in _own(after)}
    resolved = _sorted([_item("resolved", b_own[i], links) for i in b_own if i not in aset])
    still = _sorted([_item("still_failing", a_own[i], links) for i in a_own if i in bset])
    regress = _sorted([_item("regression", a_own[i], links) for i in a_own if i not in bset])
    failing = _sorted([_item("check_failure", f, links) for f in a_own.values()])

    t2_flows = {str(i.get("target")): i for i in (report.get("tier2") or {}).get("flows") or []}
    t3_flows = {str(i.get("target")) for i in (report.get("tier3") or {}).get("flows") or []}
    by_id = {str(f["id"]): f for f in flows}
    after_idents = {ident(f) for f in _own(after)}
    flow_items: list[dict[str, Any]] = []
    changed: dict[str, dict[str, Any]] = {}
    for fid in sorted(set(after.get("flows_run") or []) | set(before.get("flows_run") or [])):
        flow = by_id.get(str(fid))
        name = str(flow.get("name") or "") if flow else ""
        b_fs, a_fs = _of_flow(before, str(fid)), _of_flow(after, str(fid))
        base = {"flow": fid, "name": name, "target": f"flow:{fid}", "rules": []}
        b_d = [f for f in b_fs if f.get("layer") == "D"]
        a_d = [f for f in a_fs if f.get("layer") == "D"]
        if b_d:
            flow_items.append(
                stamp(
                    4,
                    {
                        **base,
                        "kind": "flow_before_failed",
                        "summary": b_d[0].get("summary"),
                        "evidence": [
                            {
                                "kind": "check",
                                "at": f"check:D:{b_d[0].get('case')}",
                                "what": b_d[0].get("summary"),
                            }
                        ],
                    },
                )
            )
            continue
        b_c = {(str(f.get("rule")), str(f.get("case"))) for f in b_fs if f.get("layer") == "C"}
        new_c = [
            f
            for f in a_fs
            if f.get("layer") == "C" and (str(f.get("rule")), str(f.get("case"))) not in b_c
        ]
        if new_c:
            flow_items.append(
                stamp(
                    4,
                    {
                        **base,
                        "kind": "workflow_fails",
                        "invariant": target_of(new_c[0]),
                        "summary": new_c[0].get("summary"),
                        "evidence": [
                            {
                                "kind": "check",
                                "at": f"check:C:{new_c[0].get('case')}",
                                "what": new_c[0].get("summary"),
                            }
                        ],
                    },
                )
            )
        if a_d:
            f0 = a_d[0]
            reported = (
                "t2_determined"
                if f"flow:{fid}" in t2_flows
                else "t3_may_fail"
                if f"flow:{fid}" in t3_flows
                else "unreported"
            )
            backed, why = (
                spec_backed(flow, f0, cand_steps, after_idents, route_key)
                if flow
                else (None, "no flow")
            )
            extra: dict[str, Any] = {"reported_as": reported, "spec_backed": backed}
            if why:
                extra["why"] = why
            item = stamp(
                4,
                {
                    **base,
                    "kind": "flow_changed",
                    "case": f0.get("case"),
                    "summary": f0.get("summary"),
                    "evidence": [
                        {
                            "kind": "check",
                            "at": f"check:D:{f0.get('case')}",
                            "what": f0.get("summary"),
                        }
                    ],
                    **extra,
                },
            )
            flow_items.append(item)
            changed[str(fid)] = item
        elif not new_c:
            flow_items.append(
                stamp(
                    4,
                    {
                        **base,
                        "kind": "flow_unchanged",
                        "evidence": [
                            {
                                "kind": "check",
                                "at": f"flow:{fid}",
                                "what": "passes before and after",
                            }
                        ],
                    },
                )
            )
    flow_items = _sorted(flow_items)

    t2_check: list[dict[str, Any]] = []
    for tgt, claim in sorted(t2_flows.items()):
        fid = tgt.removeprefix("flow:")
        t2_check.append(
            stamp(
                4,
                {
                    "kind": "t2_flow_claim",
                    "target": tgt,
                    "claim": claim.get("claim"),
                    "confirmed": fid in changed,
                    "rules": [],
                    "evidence": [
                        {
                            "kind": "check",
                            "at": f"flow:{fid}",
                            "what": "observed flow_changed"
                            if fid in changed
                            else "the flow did not change",
                        }
                    ],
                },
            )
        )
    for d in (report.get("tier2") or {}).get("determined") or []:
        if d.get("kind") != "scenario_fails" or d.get("status") != "determined":
            continue
        tgt = str(d.get("target"))
        ok = any(target_of(f) == tgt for f in _own(before) if f.get("layer") == "D")
        t2_check.append(
            stamp(
                4,
                {
                    "kind": "t2_scenario_claim",
                    "target": tgt,
                    "claim": d.get("claim"),
                    "confirmed": ok,
                    "rules": list(d.get("rules") or []),
                    "evidence": [
                        {
                            "kind": "check",
                            "at": f"check:D:{tgt}",
                            "what": "fails before the build"
                            if ok
                            else "does not fail before the build",
                        }
                    ],
                },
            )
        )
    t2_check = _sorted(t2_check)
    contradicted = [c for c in t2_check if not c["confirmed"]]
    t2_confirmed = not contradicted

    counts = {
        "resolved": len(resolved),
        "still_failing": len(still),
        "regressions": len(regress),
        "failing_after": len(failing),
        "flows_run": len(by_id),
        "flow_unchanged": sum(1 for i in flow_items if i["kind"] == "flow_unchanged"),
        "flow_changed": len(changed),
        "flow_before_failed": sum(1 for i in flow_items if i["kind"] == "flow_before_failed"),
        "workflow_fails": sum(1 for i in flow_items if i["kind"] == "workflow_fails"),
        "unreported": sum(1 for i in changed.values() if i["reported_as"] == "unreported"),
        "t2_contradicted": len(contradicted),
        "may_fail_unbacked": sum(
            1
            for i in changed.values()
            if i["reported_as"] == "t3_may_fail" and i["spec_backed"] is not True
        ),
    }
    counts["unexpected_regressions"] = (
        counts["regressions"] + counts["unreported"] + counts["workflow_fails"]
    )
    owner = dict(owner) if owner is not None else {"untouched": True, "checked": []}
    mr = matches_report(report, before)
    verdict = verdict_of(rebuild, counts, owner_untouched=bool(owner.get("untouched")), matches=mr)
    return {
        "implementation": dict(implementation) if implementation is not None else None,
        "rebuild": dict(rebuild),
        "before": {
            "matches_report": mr,
            "failing_count": len(bset),
            "flows_run": len(before.get("flows_run") or []),
        },
        "failing": failing,
        "resolved": resolved,
        "still_failing": still,
        "regressions": regress,
        "flows": flow_items,
        "t2_check": t2_check,
        "t2_confirmed": t2_confirmed,
        "t3_vs_edit": t3_vs_edit(report, edit, tree=tree, prefix=prefix),
        "owner": owner,
        "verdict": verdict,
        "counts": counts,
        "notes": sorted(notes),
    }


def verdict_of(
    rebuild: Mapping[str, Any],
    counts: Mapping[str, int],
    *,
    owner_untouched: bool,
    matches: bool | None,
) -> str:
    """The verdict table of the pre-registration (D6)."""
    if rebuild.get("status") != "passed":
        return "not_built"
    broken = (
        not owner_untouched
        or matches is False
        or counts["regressions"]
        or counts["still_failing"]
        or counts["workflow_fails"]
        or counts["unreported"]
        or counts["t2_contradicted"]
    )
    if broken:
        return "fail"
    return "needs_owner" if counts["may_fail_unbacked"] else "pass"


def placeholder(
    verdict: str,
    rebuild: Mapping[str, Any] | None,
    before: Mapping[str, Any],
    report: Mapping[str, Any],
    *,
    owner: Mapping[str, Any] | None = None,
    implementation: Mapping[str, Any] | None = None,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    """Tier 4 of a change that was not (or could not be) verified: ``nothing_to_build`` and
    ``not_built`` have no after run, so every observed list is empty."""
    return {
        "implementation": dict(implementation) if implementation is not None else None,
        "rebuild": dict(rebuild) if rebuild is not None else None,
        "before": {
            "matches_report": matches_report(report, before),
            "failing_count": len(failing_set(before)),
            "flows_run": len(before.get("flows_run") or []),
        },
        "failing": [],
        "resolved": [],
        "still_failing": [],
        "regressions": [],
        "flows": [],
        "t2_check": [],
        "t2_confirmed": None,
        "t3_vs_edit": {"status": "not_applicable: no edit", "files": None, "declarations": None},
        "owner": dict(owner) if owner is not None else {"untouched": True, "checked": []},
        "verdict": verdict,
        "counts": {},
        "notes": sorted(notes),
    }


def with_owner(tier4: Mapping[str, Any], owner: Mapping[str, Any]) -> dict[str, Any]:
    """`tier4` with the owner guard's final answer; a changed owner checkout is a ``fail``."""
    out = dict(tier4)
    out["owner"] = dict(owner)
    if not owner.get("untouched") and out.get("verdict") not in (None, "not_built"):
        out["verdict"] = "fail"
    return out


# ---- the merge (D7) ---------------------------------------------------------------------------


def merge(report: Mapping[str, Any], state: str, tier4: Mapping[str, Any]) -> dict[str, Any]:
    """The input report with exactly two keys changed: ``tiers["4"]`` and ``tier4``."""
    out = dict(report)
    tiers = dict(report["tiers"])
    tiers["4"] = state
    out["tiers"] = tiers
    out["tier4"] = dict(tier4)
    return out
