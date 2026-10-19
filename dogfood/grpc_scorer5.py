"""The scorer of gRPC round 5 (docs/results/2026-10-18-grpc-round5-prereg.md, section 7).

Pure: no process, no network, no file outside the ones named. Round 4's scoring functions
(``grpc_scorer4.py``: S8 SAFE, S9 CODES, S10 provenance, and through it round 3's S5-S7) and round
2's flows scorer (``grpc_flow_scorer.py``: S1-S4) are imported unchanged. New:

* **S10'** provenance as round 4's S10, but the run index comes from the ledger (S12) and the hashes
  from Amendment 2/3 of the round-5 pre-registration (the runner, scorer, ``grpc_truth5.py``
  and the target's freeze).
* **S11** every truth code passes H2 (``grpc_truth5.normalise_code``); the count normalised is reported
  per row; a code that fails H2 makes the row NOT RUN.
* **S12** the run index and the chain, from the ledger lines (never from the runner's output); a
  broken chain labels every row ``post_hoc`` with reason ``ledger_chain_broken``.
* **S13** FLOWS: round 2's scorer for recovered / verified / edges / coverage, with the truth flows
  that pass their own replay taken from H3's replay results (links applied).
* **S14 SQLT** (reported, never gated): per-rpc table recall and precision against the truth's
  ``tables[*].read_by`` + ``written_by``, case-folded.

    python dogfood/grpc_scorer5.py --cases dogfood/grpc_scorer5_cases.json
"""

from __future__ import annotations

# ruff: noqa: E501 -- docstrings and data lines
import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import grpc_flow_scorer as fs  # noqa: E402
import grpc_scorer3 as sc3  # noqa: E402
import grpc_scorer4 as sc4  # noqa: E402
import grpc_truth5 as t5  # noqa: E402

HEX64 = re.compile(r"\b[0-9a-f]{64}\b")


def _verdict(row: dict[str, Any], reason: str) -> dict[str, Any]:
    row["reason"], row["pass"] = reason, not reason
    return row


# ---- S11 -------------------------------------------------------------------------------------


def score_codes_spelling(values: Sequence[Sequence[Any]]) -> dict[str, Any]:
    """S11. ``values``: ``[json path, value]`` for every code the chain reads. Passes when every
    value maps through H2; ``normalised`` = how many changed spelling. A refused code makes the
    row NOT RUN (``status``), never a pass."""
    normalised, refused = 0, []
    for at, value in values:
        try:
            normalised += t5.normalise_code(value, at) != value
        except t5.TruthError as err:
            refused.append(str(err))
    row: dict[str, Any] = {
        "codes": len(values),
        "normalised": normalised,
        "refused": refused,
        "status": "not_run" if refused else "scored",
    }
    return _verdict(row, "code_refused" if refused else "")


# ---- S12 / S10' ------------------------------------------------------------------------------


def ledger_view(
    lines: Sequence[str], arm: str, target: str, seq: int | None = None
) -> dict[str, Any]:
    """S12. The chain verdict and the run a scored invocation of ``(arm, target)`` is, read from the
    ledger's lines: ``run_index`` = the 1-based position among that target's ``started`` lines of the
    line at ``seq`` (default: the last one), ``prior`` = the crashed run before it. The runner's own
    claim of a run index is never read."""
    problems = t5.verify_chain(lines)
    out: dict[str, Any] = {"chain_ok": not problems, "problems": problems}
    if problems:
        return out | {"run_index": None, "prior": {}}
    runs = t5.runs_of([json.loads(x) for x in lines], arm, target)
    pos = (
        len(runs) - 1
        if seq is None
        else next((i for i, r in enumerate(runs) if r["seq"] == seq), -1)
    )
    if pos < 0:
        return out | {"run_index": None, "prior": {}, "reason": "no_started_line"}
    prior = {}
    if pos >= 1:
        prev = runs[pos - 1]
        prior = {
            "crashed_before_metric": prev["closed"] != "scored",
            "runner": prev["runner_sha256"],
        }
    return out | {"run_index": pos + 1, "prior": prior}


def recorded_hashes5(text: str) -> dict[str, str]:
    """The sha256 values the round-5 pre-registration's amendments record, by the name written beside
    each (the first 64-hex string after the name on the same line)."""
    names = {
        "runner": r"grpc_probes5\.py",
        "scorer": r"grpc_scorer5\.py",
        "truth5": r"grpc_truth5\.py",
        "contract": r"grpc_contract5\.py",
        "freeze5": r"freeze5\.sha256",
        "target": r"(?:ho5|sg|app)/freeze\.sha256",
    }
    out: dict[str, str] = {}
    for line in text.splitlines():
        for key, pat in names.items():
            m = re.search(pat + r"[^\n]*?(" + HEX64.pattern + ")", line)
            if m and key not in out:
                out[key] = m.group(1)
    return out


def provenance5(
    recorded: Mapping[str, str], run: Mapping[str, Any], ledger: Mapping[str, Any]
) -> dict[str, Any]:
    """S10'. ``recorded``: the amendments' hashes (``runner``, ``scorer``, ``truth5``, ``target``).
    ``run``: the hashes the runner wrote (``runner``, ``scorer``, ``truth5``, ``target``) -- its own
    ``run_index`` is ignored. ``ledger``: :func:`ledger_view`. ``blind`` only if every hash equals the
    recorded one, the chain verifies and the ledger's run index is 1 (or 2 after a crash before any
    metric with the same runner bytes)."""
    if not ledger.get("chain_ok"):
        reasons = ["ledger_chain_broken"]
        return {"label": "post_hoc", "reasons": reasons, "pass": False, "reason": ";".join(reasons)}
    base = sc4.provenance(
        {
            "runner": recorded.get("runner", ""),
            "scorer": recorded.get("scorer", ""),
            "heldout": recorded.get("target", ""),
        },
        {
            "runner": run.get("runner"),
            "scorer": run.get("scorer"),
            "heldout": run.get("target"),
            "run_index": ledger.get("run_index"),
            "prior": ledger.get("prior") or {},
        },
    )
    reasons = [r.replace("heldout_", "target_") for r in base["reasons"]]
    if not recorded.get("truth5"):
        reasons.append("truth5_hash_not_recorded")
    elif run.get("truth5") != recorded["truth5"]:
        reasons.append("truth5_hash_mismatch")
    label = "post_hoc" if reasons else "blind"
    return {
        "label": label,
        "reasons": reasons,
        "pass": label == "blind",
        "reason": ";".join(reasons),
    }


# ---- S13 -------------------------------------------------------------------------------------


def score_flows5(
    recovered: Sequence[Mapping[str, Any]],
    truth_flows: Sequence[Mapping[str, Any]],
    links: Mapping[str, Sequence[Mapping[str, Any]]],
    replay: Sequence[Mapping[str, Any]],
    *,
    verified_but_failing: int = 0,
    min_verified: int = 1,
    edge_recall_bound: float | None = 0.30,
    coverage_bound: int | None = 2,
) -> dict[str, Any]:
    """S13. Round 2's flows row, with ``truth_passing`` from the replay results (``{name, ok, ...}`` as
    ``FlowResult.as_json`` writes them: links applied, dotted ``expect`` checked), and the share of
    truth flows that replay reported beside it."""
    passing = {str(r["name"]): bool(r["ok"]) for r in replay}
    row = fs.score_flows(
        recovered,
        truth_flows,
        links,
        passing,
        verified_but_failing=verified_but_failing,
        min_verified=min_verified,
        edge_recall_bound=edge_recall_bound,
        coverage_bound=coverage_bound,
    )
    n_ok = sum(1 for t in truth_flows if passing.get(fs.flow_name(t), False))
    reasons: dict[str, int] = {}
    for r in replay:
        if not r["ok"]:
            reasons[str(r.get("reason"))] = reasons.get(str(r.get("reason")), 0) + 1
    row["truth_flows"] = len(truth_flows)
    row["truth_flows_passing"] = n_ok
    row["truth_replay_share"] = round(n_ok / len(truth_flows), 4) if truth_flows else None
    row["truth_replay_failures"] = dict(sorted(reasons.items()))
    return row


# ---- S14 -------------------------------------------------------------------------------------


def _tables_of(r: Mapping[str, Any]) -> set[str]:
    out = {str(t).lower() for t in r.get("tables", [])}
    out |= {str(w["table"]).lower() for w in r.get("writes", []) if isinstance(w, Mapping)}
    return out


def score_sqlt(
    truth: Mapping[str, Mapping[str, Any]], got: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """S14, reported and never gated. ``truth`` maps rpc to ``{state, tables}`` (the truth's
    ``tables[*].read_by`` + ``written_by``), ``got`` maps rpc to the extractor's ``{state, tables,
    writes}``. Micro recall / precision over ``(rpc, table)`` of the truth's ``implemented`` rpcs,
    case-folded, and each rpc's own pair."""
    tp, ep = set(), set()
    per: dict[str, dict[str, Any]] = {}
    for k, v in sorted(truth.items()):
        if v.get("state") != "implemented":
            continue
        want = _tables_of(v)
        have = _tables_of(got.get(k, {}))
        tp |= {(k, t) for t in want}
        ep |= {(k, t) for t in have}
        per[k] = {
            "truth": sorted(want),
            "extracted": sorted(have),
            "recall": None if not want else round(len(want & have) / len(want), 4),
            "precision": None if not have else round(len(want & have) / len(have), 4),
        }
    hit = tp & ep
    row: dict[str, Any] = {
        "truth_pairs": len(tp),
        "extracted_pairs": len(ep),
        "matched": len(hit),
        "recall": None if not tp else round(len(hit) / len(tp), 4),
        "precision": None if not ep else round(len(hit) / len(ep), 4),
        "per_rpc": per,
        "gated": False,
    }
    row["pass"], row["reason"] = True, ("vacuous" if not tp else "")
    return row


# ---- the cases -------------------------------------------------------------------------------


def _run_case(case: Mapping[str, Any]) -> dict[str, Any]:
    fn, inp = case["fn"], case["input"]
    if fn == "codes5":
        return score_codes_spelling(inp["values"])
    if fn == "ledger":
        return ledger_view(inp["lines"], inp["arm"], inp["target"], inp.get("seq"))
    if fn == "recorded5":
        return {"recorded": recorded_hashes5(inp["text"])}
    if fn == "prov5":
        led = (
            ledger_view(inp["lines"], inp["arm"], inp["target"], inp.get("seq"))
            if "lines" in inp
            else inp["ledger"]
        )
        return provenance5(inp["recorded"], inp["run"], led)
    if fn == "flows5":
        row = score_flows5(
            inp["recovered"], inp["truth_flows"], inp["links"], inp["replay"], **inp.get("kw", {})
        )
        return row
    if fn == "sqlt":
        return score_sqlt(inp["truth"], inp["got"])
    if fn == "safe":
        return sc4.score_safe(inp["truth"], inp["got"])
    if fn == "codes":
        return sc4.score_codes(inp["truth"], inp["got"])
    if fn == "prov":
        return sc4.provenance(inp["recorded"], inp["run"])
    if fn == "recorded":
        return {"recorded": sc4.recorded_hashes(inp["text"])}
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
