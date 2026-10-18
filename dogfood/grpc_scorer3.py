"""The scorer of gRPC round 3 (docs/results/2026-10-18-grpc-round3-prereg.md, section 5).

Pure: no process, no network, no file outside the ones named. Round 2's flows scorer
(``grpc_flow_scorer.py``: S1-S4) is reused unchanged for flows; this file adds the three rows
round 3 changes:

* **S5 REC on library output.** The denominator is the truth rpcs that are not streaming (all
  states) plus ``(absent)``. Coverage = rpcs present in ``reconcile_with``'s output / denominator.
  Agreement = covered rpcs whose final code class matches the truth state (``implemented``:
  answered, not UNIMPLEMENTED; ``unimplemented`` and ``not_registered``: UNIMPLEMENTED; absent:
  UNIMPLEMENTED). An empty denominator FAILs as ``vacuous``; a runner that made any call of its
  own (``supplementary_calls != 0``) FAILs.
* **S6 order non-vacuity.** A truth rpc whose ``effects`` is not ``none`` must be in the library's
  output with an answer (a missing rpc, or one with no code or a ``reason``, was not in both
  passes); otherwise FAIL ``order_vacuous``. With no such rpc in the truth the row is reported
  ``not_exercised`` (it can still pass; disclosed). The library output does not expose its two
  passes, so "in both passes" is read as "present and answered": an ``isolated`` entry is
  reported, not failed.
* **S7 AUTH.** The denominator is every truth rpc whose state is ``implemented`` or
  ``unimplemented`` (not only those the extractor found: a missing rpc counts as ``unclear``).
  ``wrong`` = an extracted ``Public`` where the truth is not public, or ``Member`` where it is
  public. Pass = ``wrong == 0`` and ``unclear / denominator <= 0.50``; an empty denominator FAILs.

    python dogfood/grpc_scorer3.py --cases dogfood/grpc_scorer3_cases.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

COVERAGE_BOUND = 0.95
UNCLEAR_CAP = 0.50
NOT_RPC_KEYS = ("(absent)", "order", "auth_refused")
PUBLIC_LABELS = {"public", "none", "anonymous", "anon", "unauthenticated", "open", "no"}


def norm_auth(value: Any) -> str:
    """``Public`` / ``Member`` from a truth or extract label; anything else is returned as is
    (``unclear``, ``""``)."""
    v = str(value or "").strip()
    if v.lower() in PUBLIC_LABELS:
        return "Public"
    if v.lower() == "member":
        return "Member"
    return v


def _verdict(row: dict[str, Any], reason: str) -> dict[str, Any]:
    row["reason"], row["pass"] = reason, not reason
    return row


def _class_of(state: str) -> str:
    return state if state in ("implemented", "unimplemented", "not_registered") else "implemented"


def _agrees(cls: str, code: Any) -> bool:
    if cls == "implemented":
        return code is not None and code != "UNIMPLEMENTED"
    return code == "UNIMPLEMENTED"


def score_rec(
    truth: Mapping[str, str],
    rec: Mapping[str, Any],
    *,
    supplementary_calls: int = 0,
    coverage_bound: float = COVERAGE_BOUND,
) -> dict[str, Any]:
    """S5. ``truth`` maps ``<Service>/<Method>`` to its state; ``rec`` is ``reconcile_with``'s
    output. Returns the row (``pass``, ``reason``, coverage, agreement, the wrong ones)."""
    wanted = {k: _class_of(v) for k, v in truth.items() if v != "streaming"}
    wanted["(absent)"] = "absent"
    covered = {k for k in wanted if k in rec and isinstance(rec[k], Mapping)}
    by_class: dict[str, list[bool]] = {}
    wrong: list[tuple[str, Any, str]] = []
    for k in sorted(covered):
        code = rec[k].get("code")
        ok = _agrees(wanted[k], code)
        by_class.setdefault(wanted[k], []).append(ok)
        if not ok:
            wrong.append((k, code, wanted[k]))
    den = len(wanted)
    coverage = len(covered) / den if den else None
    agreement = (len(covered) - len(wrong)) / len(covered) if covered else None
    row: dict[str, Any] = {
        "denominator": den,
        "covered": len(covered),
        "coverage": None if coverage is None else round(coverage, 4),
        "agreement": None if agreement is None else round(agreement, 4),
        "by_class": {c: f"{sum(v)}/{len(v)}" for c, v in sorted(by_class.items())},
        "wrong": wrong,
        "missing": sorted(set(wanted) - covered),
        "supplementary_calls": supplementary_calls,
    }
    if len(wanted) <= 1:  # only (absent): the truth lists no rpc
        return _verdict(row, "vacuous")
    if supplementary_calls != 0:
        return _verdict(row, "supplementary_calls")
    if coverage is None or coverage < coverage_bound:
        return _verdict(row, "coverage_below_bound")
    return _verdict(row, "agreement" if wrong else "")


def score_order(effects: Mapping[str, str], rec: Mapping[str, Any]) -> dict[str, Any]:
    """S6. ``effects`` maps ``<Service>/<Method>`` to its truth ``effects`` value."""
    effectful = sorted(k for k, v in effects.items() if v not in ("none", "", None))
    if not effectful:
        return {"status": "not_exercised", "pass": True, "effectful": [], "reason": ""}
    bad: list[str] = []
    isolated: list[str] = []
    for k in effectful:
        e = rec.get(k)
        if not isinstance(e, Mapping) or e.get("code") is None or e.get("reason"):
            bad.append(k)
        elif e.get("isolated"):
            isolated.append(k)
    return {
        "status": "order_vacuous" if bad else "exercised",
        "pass": not bad,
        "effectful": effectful,
        "not_in_both_passes": bad,
        "isolated": isolated,
        "reason": "order_vacuous" if bad else "",
    }


def score_auth(
    truth: Mapping[str, Mapping[str, Any]],
    got: Mapping[str, Any],
    *,
    unclear_cap: float = UNCLEAR_CAP,
) -> dict[str, Any]:
    """S7. ``truth`` maps ``<Service>/<Method>`` to ``{state, auth}``; ``got`` maps it to the
    extractor's auth label (a rpc the extractor did not find is absent from ``got``)."""
    den = [k for k, v in truth.items() if v["state"] in ("implemented", "unimplemented")]
    wrong: list[tuple[str, str, str]] = []
    unclear: list[tuple[str, str]] = []
    for k in sorted(den):
        label = norm_auth(got.get(k))
        want = "Public" if norm_auth(truth[k]["auth"]) == "Public" else "Member"
        if label not in ("Public", "Member"):
            unclear.append((k, label or "(none)"))
        elif label != want:
            wrong.append((k, label, want))
    share = len(unclear) / len(den) if den else None
    row: dict[str, Any] = {
        "denominator": len(den),
        "wrong": wrong,
        "unclear": len(unclear),
        "unclear_share": None if share is None else round(share, 4),
        "unclear_rpcs": unclear,
    }
    if not den:
        return _verdict(row, "vacuous")
    if wrong:
        return _verdict(row, "wrong")
    return _verdict(row, "unclear_above_cap" if share is not None and share > unclear_cap else "")


# ---- the cases -------------------------------------------------------------------------------


def _run_case(case: Mapping[str, Any]) -> dict[str, Any]:
    fn, inp = case["fn"], case["input"]
    if fn == "rec":
        return score_rec(
            inp["truth"], inp["rec"], supplementary_calls=inp.get("supplementary_calls", 0)
        )
    if fn == "order":
        return score_order(inp["effects"], inp["rec"])
    if fn == "auth":
        return score_auth(inp["truth"], inp["got"], unclear_cap=inp.get("cap", UNCLEAR_CAP))
    raise ValueError(f"unknown case function {fn!r}")


def _matches(got: Mapping[str, Any], expect: Mapping[str, Any]) -> bool:
    plain = json.loads(json.dumps(got, default=str))  # tuples compare as the lists of the cases
    return all(plain.get(k) == v for k, v in expect.items())


def run_cases(path: Path) -> list[str]:
    """The cases of ``path`` that disagree with their expectation (empty: all agree)."""
    doc = json.loads(path.read_text())
    bad = []
    for case in doc["cases"]:
        got = _run_case(case)
        if not _matches(got, case["expect"]):
            bad.append(f"{case['name']}: got {json.dumps(got, sort_keys=True, default=str)[:300]}")
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
