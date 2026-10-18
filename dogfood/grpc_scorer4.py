"""The scorer of gRPC round 4 (docs/results/2026-10-18-grpc-round4-prereg.md, section 5).

Pure: no process, no network, no file outside the ones named. Round 3's scorer (``grpc_scorer3.py``:
S5 REC, S6 order, S7 AUTH) and round 2's flows scorer (``grpc_flow_scorer.py``: S1-S4) are reused
unchanged; this file adds three rows:

* **S8 SAFE.** Confident wrong states: an extracted ``not_registered`` or ``unimplemented`` where the
  truth is ``implemented``; ``implemented`` where the truth is ``not_registered`` or
  ``unimplemented``; ``Public`` where the truth is not public; ``Member`` where the truth is public.
  Pass = count 0. ``unread`` and ``unclear`` are never counted here (they are counted, as wrong, in
  RPC and AUTH); an rpc the extractor does not list is not counted either.
* **S9 CODES.** Micro recall and precision over ``(rpc, code)`` of the truth's ``implemented`` rpcs
  (``OK`` excluded). An extracted ``UNKNOWN`` the truth does not list is an extra (it costs
  precision, and is named); an ``unread`` code is neither a match nor an extra (it lowers recall
  only). Pass = recall >= 0.70 and precision >= 0.90.
* **S10 provenance.** The scorer, not the runner, labels a held-out row. It is given the hashes the
  prereg's amendments record and the hashes, run index and crash history the runner wrote into its
  output. ``blind`` only if the runner, scorer and held-out hashes equal the recorded ones and the run
  index is 1 -- or 2 after a crash before any metric with the same runner bytes as the recorded ones.
  Any other case: ``post_hoc`` with the reason. The label is not a flag the runner can set.

    python dogfood/grpc_scorer4.py --cases dogfood/grpc_scorer4_cases.json
"""

from __future__ import annotations

# ruff: noqa: E501 -- docstrings and data lines
import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import grpc_scorer3 as sc3  # noqa: E402

RECALL_BOUND = 0.70
PRECISION_BOUND = 0.90
HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
STATES_SERVED = ("implemented", "unimplemented")


def _verdict(row: dict[str, Any], reason: str) -> dict[str, Any]:
    row["reason"], row["pass"] = reason, not reason
    return row


# ---- S8 --------------------------------------------------------------------------------------


def score_safe(
    truth: Mapping[str, Mapping[str, Any]], got: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """``truth`` maps ``<Service>/<Method>`` to ``{state, auth}``; ``got`` maps it to the extractor's
    ``{state, auth}``. Returns the row: ``wrong`` lists ``(rpc, kind, extracted, truth)``."""
    wrong: list[tuple[str, str, str, str]] = []
    for k in sorted(truth):
        t, e = truth[k], got.get(k)
        if e is None:
            continue
        ts, es = t.get("state"), e.get("state")
        if (es in ("not_registered", "unimplemented") and ts == "implemented") or (
            es == "implemented" and ts in ("not_registered", "unimplemented")
        ):
            wrong.append((k, "state", str(es), str(ts)))
        if ts in STATES_SERVED and es in STATES_SERVED:
            label, want = sc3.norm_auth(e.get("auth")), sc3.norm_auth(t.get("auth"))
            want = "Public" if want == "Public" else "Member"
            if label in ("Public", "Member") and label != want:
                wrong.append((k, "auth", label, want))
    row: dict[str, Any] = {"count": len(wrong), "wrong": wrong, "checked": len(truth)}
    return _verdict(row, "confident_wrong" if wrong else "")


# ---- S9 --------------------------------------------------------------------------------------


def score_codes(
    truth: Mapping[str, Mapping[str, Any]], got: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """``truth`` maps rpc to ``{state, codes}``; ``got`` maps rpc to ``{state, codes, unread}``
    (``codes`` as names; ``unread``: the extractor said it could not read some code)."""
    tc = {
        (k, c)
        for k, v in truth.items()
        if v.get("state") == "implemented"
        for c in v.get("codes", [])
        if c != "OK"
    }
    ec = {
        (k, c)
        for k, v in got.items()
        if v.get("state") == "implemented"
        for c in v.get("codes", [])
        if c != "OK"
    }
    hit = tc & ec
    recall = len(hit) / len(tc) if tc else None
    precision = len(hit) / len(ec) if ec else None
    unknown_extras = sorted(f"{k}:{c}" for k, c in ec - tc if c == "UNKNOWN")
    row: dict[str, Any] = {
        "truth": len(tc),
        "extracted": len(ec),
        "matched": len(hit),
        "recall": None if recall is None else round(recall, 4),
        "precision": None if precision is None else round(precision, 4),
        "unknown_extras": unknown_extras,
        "unread_rpcs": sorted(k for k, v in got.items() if v.get("unread")),
        "missed": sorted(f"{k}:{c}" for k, c in tc - ec),
        "extra": sorted(f"{k}:{c}" for k, c in ec - tc),
    }
    if recall is None:
        return _verdict(row, "vacuous")
    if recall < RECALL_BOUND:
        return _verdict(row, "recall_below_bound")
    if precision is not None and precision < PRECISION_BOUND:
        return _verdict(row, "precision_below_bound")
    return _verdict(row, "")


# ---- S10 -------------------------------------------------------------------------------------


def recorded_hashes(text: str) -> dict[str, str]:
    """The sha256 values the prereg's amendments record, by the name written beside each (the first
    64-hex string after the name on the same line)."""
    out: dict[str, str] = {}
    names = {
        "runner": r"grpc_probes4\.py",
        "scorer": r"grpc_scorer4\.py",
        "contract": r"grpc_contract4\.py",
        "freeze4": r"freeze4\.sha256",
        "heldout": r"heldout4/freeze\.sha256",
    }
    for line in text.splitlines():
        for key, pat in names.items():
            m = re.search(pat + r"[^\n]*?(" + HEX64.pattern + ")", line)
            if m and key not in out:
                out[key] = m.group(1)
    return out


def provenance(recorded: Mapping[str, str], run: Mapping[str, Any]) -> dict[str, Any]:
    """``recorded``: runner, scorer, heldout hashes of the amendments. ``run``: what the runner wrote
    -- ``runner``, ``scorer``, ``heldout`` (hashes), ``run_index``, and for a re-run ``prior``
    (``crashed_before_metric``, ``runner``: the first run's runner hash)."""
    reasons: list[str] = []
    for k in ("runner", "scorer", "heldout"):
        if not recorded.get(k):
            reasons.append(f"{k}_hash_not_recorded")
        elif run.get(k) != recorded[k]:
            reasons.append(f"{k}_hash_mismatch")
    idx = run.get("run_index")
    prior = run.get("prior") or {}
    if idx == 2:
        if not prior.get("crashed_before_metric"):
            reasons.append("rerun_without_a_crash_before_any_metric")
        if prior.get("runner") != run.get("runner"):
            reasons.append("rerun_with_changed_runner_bytes")
    elif idx != 1:
        reasons.append(f"run_index_{idx}")
    label = "post_hoc" if reasons else "blind"
    return {
        "label": label,
        "reasons": reasons,
        "pass": label == "blind",
        "reason": ";".join(reasons),
    }


# ---- the cases -------------------------------------------------------------------------------


def _run_case(case: Mapping[str, Any]) -> dict[str, Any]:
    fn, inp = case["fn"], case["input"]
    if fn == "safe":
        return score_safe(inp["truth"], inp["got"])
    if fn == "codes":
        return score_codes(inp["truth"], inp["got"])
    if fn == "prov":
        return provenance(inp["recorded"], inp["run"])
    if fn == "recorded":
        return {"recorded": recorded_hashes(inp["text"])}
    return sc3._run_case(case)


def run_cases(path: Path) -> list[str]:
    """The cases of ``path`` that disagree with their expectation (empty: all agree)."""
    doc = json.loads(path.read_text())
    bad = []
    for case in doc["cases"]:
        got = _run_case(case)
        if not sc3._matches(got, case["expect"]):
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
