"""The flows scorer of gRPC round 2 (docs/results/2026-10-17-grpc-round2-prereg.md, section 5).

Pure: no process, no network, no file outside the ones named. It scores the rpc flows an
importer recovered (``lattice.rpc-flows/2``) against the truth flows of an app and their
``links`` (D4), under the vacuity-aware rule:

* **S1** a gated rate whose denominator is 0 is a FAIL with ``reason: "vacuous"`` (never 1.0,
  never a pass);
* **S2** a flows row passes only with ``verified >= 1`` **and** ``verified_but_failing == 0``
  **and** edge recall >= the row's bound **and** flow coverage >= the row's bound (a pass of
  round 1's rule with ``verified == 0`` is a FAIL);
* **S3** beside every row: the recovered flows by status, exact recall, the histogram of the
  code a failed flow's failing step answered, the unbound reasons;
* **S4** :func:`rescore_ho_flows` re-scores round 1's recorded ``deskd`` HO-FLOWS numbers
  (labelled post hoc by the caller; nothing is re-run).

D4, the edge definition. A truth flow's *edges* are its ``links``: ``(producer rpc, producer
response field, consumer rpc, consumer request field, consumer expected code)``. A recovered
*verified* flow **contains** an edge when it has a step whose rpc is the consumer, whose recorded
code is the expected code and whose ``bound[field]`` names a step of the producer rpc and the
producer's response field. Edge recall = edges contained in at least one verified flow / all edges
of the truth flows that pass their own replay; flow coverage = those truth flows with at least
one contained edge / those truth flows; exact recall (reported only) = truth flows whose
``(rpc, code)`` sequence equals a verified flow's. A truth flow that fails its own replay is a
truth defect: reported, and its edges leave the denominator; more than one third failing makes
the row not scorable (FAIL).

Formats. A recovered flow: ``{"id", "kind", "status", "reason", "steps": [{"rpc", "code",
"bound": {field: {"step": n, "from_field": name}}, ...}]}``. A truth flow: ``{"name" | "id",
"steps": [{"rpc", "expect_code" | "code", ...}]}``. ``links``: ``{flow name: [{"step", "field",
"from_step", "from_field"}]}`` (1-based steps).

    python dogfood/grpc_flow_scorer.py --cases dogfood/grpc_flow_scorer_cases.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

STATUSES = ("verified", "unverified", "failed", "unbound")
TRUTH_DEFECT_SHARE = 1 / 3


def rate(num: int, den: int, bound: float | int | None, *, gated: bool = True) -> dict[str, Any]:
    """``num / den`` against ``bound``. S1: a gated rate with ``den == 0`` fails as vacuous."""
    out: dict[str, Any] = {"num": num, "den": den, "bound": bound, "gated": gated}
    out["value"] = round(num / den, 4) if den else None
    if not gated or bound is None:
        out["pass"] = True
        return out
    if den == 0:
        out.update({"pass": False, "reason": "vacuous"})
    else:
        out["pass"] = num / den >= bound
        if not out["pass"]:
            out["reason"] = "below_bound"
    return out


def truth_step_code(step: Mapping[str, Any]) -> Any:
    return step.get("expect_code", step.get("code"))


def edges_of(truth: Mapping[str, Any], links: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The edges (D4) of one truth flow from its links."""
    steps = truth["steps"]
    out = []
    for ln in links:
        try:
            consumer, producer = steps[ln["step"] - 1], steps[ln["from_step"] - 1]
        except (IndexError, KeyError, TypeError):
            continue
        out.append(
            {
                "producer": producer["rpc"],
                "from_field": ln["from_field"],
                "consumer": consumer["rpc"],
                "field": ln["field"],
                "code": truth_step_code(consumer),
            }
        )
    return out


def contains(flow: Mapping[str, Any], edge: Mapping[str, Any]) -> bool:
    """Whether a recovered flow contains ``edge`` (D4)."""
    steps = flow.get("steps") or []
    for step in steps:
        if step.get("rpc") != edge["consumer"] or step.get("code") != edge["code"]:
            continue
        b = (step.get("bound") or {}).get(edge["field"])
        if not isinstance(b, Mapping):
            continue
        k = b.get("step")
        if not isinstance(k, int) or not 1 <= k <= len(steps):
            continue
        if (
            steps[k - 1].get("rpc") == edge["producer"]
            and b.get("from_field") == edge["from_field"]
        ):
            return True
    return False


def flow_name(flow: Mapping[str, Any]) -> str:
    return str(flow.get("name", flow.get("id")))


def failing_code(flow: Mapping[str, Any]) -> str | None:
    m = re.search(r"answered ([A-Z_]+)", str(flow.get("reason") or ""))
    return m.group(1) if m else None


def score_flows(
    recovered: Sequence[Mapping[str, Any]],
    truth_flows: Sequence[Mapping[str, Any]],
    links: Mapping[str, Sequence[Mapping[str, Any]]],
    truth_passing: Mapping[str, bool] | None = None,
    *,
    verified_but_failing: int = 0,
    min_verified: int = 1,
    edge_recall_bound: float | None = 0.30,
    coverage_bound: int | None = 2,
) -> dict[str, Any]:
    """One flows row under S1-S3. ``truth_passing`` maps a truth flow's name to whether it passes
    its own replay (missing: it passes). ``edge_recall_bound`` / ``coverage_bound`` ``None``: the
    rate is reported, not gated (a row with no links, as the notes app's)."""
    passing = [t for t in truth_flows if (truth_passing or {}).get(flow_name(t), True)]
    defects = [
        flow_name(t) for t in truth_flows if flow_name(t) not in {flow_name(t) for t in passing}
    ]
    verified = [f for f in recovered if f.get("status") == "verified"]
    all_edges = [(t, e) for t in passing for e in edges_of(t, links.get(flow_name(t), ()))]
    hit = [(t, e) for t, e in all_edges if any(contains(f, e) for f in verified)]
    covered = {flow_name(t) for t, _ in hit}
    seqs = [[(s.get("rpc"), s.get("code")) for s in f.get("steps", [])] for f in verified]
    exact = sum(
        any([(s["rpc"], truth_step_code(s)) for s in t["steps"]] == q for q in seqs)
        for t in passing
    )
    reasons: list[str] = []
    if len(verified) < min_verified:
        reasons.append("no_verified_flow" if min_verified <= 1 else "too_few_verified")
    if verified_but_failing:
        reasons.append("verified_but_failing")
    if truth_flows and len(defects) > len(truth_flows) * TRUTH_DEFECT_SHARE:
        reasons.append("truth_not_scorable")
    recall = rate(len(hit), len(all_edges), edge_recall_bound, gated=edge_recall_bound is not None)
    cover: dict[str, Any] = {
        "num": len(covered),
        "den": len(passing),
        "bound": coverage_bound,
        "gated": coverage_bound is not None,
        "value": round(len(covered) / len(passing), 4) if passing else None,
        "pass": True,
    }
    if coverage_bound is not None:  # the bound counts truth flows, not a share
        if not passing:
            cover["reason"] = "vacuous"
            cover["pass"] = False
        elif len(covered) < coverage_bound:
            cover.update(reason="below_bound")
            cover["pass"] = False
    for name, r in (("edge_recall", recall), ("coverage", cover)):
        if not r["pass"]:
            reasons.append(f"{name}:{r['reason']}")
    by_status = {s: sum(f.get("status") == s for f in recovered) for s in STATUSES}
    return {
        "pass": not reasons,
        "reasons": reasons,
        "recovered": len(recovered),
        "recovered_by_status": by_status,
        "verified": len(verified),
        "verified_but_failing": verified_but_failing,
        "edge_recall": recall,
        "coverage": cover,
        "exact": {"num": exact, "den": len(passing)},
        "truth_defects": defects,
        "failing_codes": dict(
            sorted(
                Counter(
                    failing_code(f) or "other" for f in recovered if f.get("status") == "failed"
                ).items()
            )
        ),
        "unbound_reasons": dict(
            sorted(
                Counter(
                    str(f.get("reason")) for f in recovered if f.get("status") == "unbound"
                ).items()
            )
        ),
    }


def rescore_ho_flows(observed: Mapping[str, Any]) -> dict[str, Any]:
    """S4: round 1's recorded ``HO-FLOWS`` observation under S1/S2 (post hoc; nothing re-run)."""
    by_status = observed.get("recovered by status") or {}
    verified = int(observed.get("verified", 0))
    vbf = int(observed.get("verified-but-failing", 0))
    reasons = []
    if verified < 1:
        reasons.append("no_verified_flow")
    if vbf:
        reasons.append("verified_but_failing")
    cov = str(observed.get("truth flows covered (subsequence of a verified flow)", "0/0"))
    num, _, den = cov.partition("/")
    if int(den or 0) == 0 or int(num or 0) == 0:
        reasons.append("coverage:vacuous" if int(den or 0) == 0 else "coverage:below_bound")
    return {
        "label": "post hoc: round-1 numbers re-scored under S1/S2, not re-run",
        "round1_rule": "PASS (0 verified-but-failing, vacuously)",
        "recovered_by_status": by_status,
        "verified": verified,
        "pass": not reasons,
        "reasons": reasons,
    }


# ---- the cases -------------------------------------------------------------------------------


def _run_case(case: Mapping[str, Any]) -> Any:
    fn, arg = case["fn"], case["input"]
    if fn == "rate":
        return rate(arg["num"], arg["den"], arg.get("bound"), gated=arg.get("gated", True))
    if fn == "contains":
        return contains(arg["flow"], arg["edge"])
    if fn == "edges":
        return edges_of(arg["truth"], arg["links"])
    if fn == "score":
        return score_flows(
            arg["recovered"],
            arg["truth"],
            arg.get("links", {}),
            arg.get("passing"),
            verified_but_failing=arg.get("vbf", 0),
            min_verified=arg.get("min_verified", 1),
            edge_recall_bound=arg.get("edge_bound", 0.30),
            coverage_bound=arg.get("coverage_bound", 2),
        )
    if fn == "rescore":
        return rescore_ho_flows(arg)
    raise ValueError(f"unknown case function {fn!r}")


def _matches(got: Any, want: Any) -> bool:
    """``want`` is a subset of ``got`` (every key it names equal; lists and scalars equal)."""
    if isinstance(want, Mapping):
        return isinstance(got, Mapping) and all(
            k in got and _matches(got[k], v) for k, v in want.items()
        )
    return bool(got == want)


def run_cases(path: Path) -> list[str]:
    """The cases of ``path`` that disagree with their expectation (empty: all agree)."""
    doc = json.loads(path.read_text())
    bad = []
    for case in doc["cases"]:
        got = _run_case(case)
        if not _matches(got, case["expect"]):
            bad.append(f"{case['name']}: got {json.dumps(got, sort_keys=True)[:300]}")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--cases", type=Path, required=True)
    args = ap.parse_args(argv)
    n = len(json.loads(args.cases.read_text())["cases"])
    bad = run_cases(args.cases)
    for line in bad:
        print("FAIL", line)
    print(f"{n - len(bad)}/{n} cases agree")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
