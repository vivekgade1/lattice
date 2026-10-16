"""The round-2 scorer's own cases (run before the freeze): every item of
``dogfood/implementation_agent2_scorer.py`` against the canned answers of
``dogfood/implementation_agent2_scorer_cases.json`` -- synthetic rows per item, whole sessions,
the question table, and round 1's 13 answers as fixtures (they test the scorer; they are not a
re-score of round 1). Exit 0 when every case holds.

    python dogfood/implementation_agent2_check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import implementation_agent2_scorer as S  # noqa: E402

CASES = json.loads((HERE / "implementation_agent2_scorer_cases.json").read_text())


def synth_facts() -> dict[str, Any]:
    f = CASES["scorer_facts"]
    files, ev = f["files"], set(f["evidence"])
    return {
        "check": lambda p, n: p in files and 1 <= n <= files[p],
        "evidence": lambda p, n: f"{p}:{n}" in ev,
        "keys": set(f["keys"]),
        "rules": set(f["rules"]),
        "reg": {k: tuple(v) for k, v in f["reg"].items()},
        "rule_lines": f["rule_lines"],
        "trace_source": f["trace_source"],
    }


def real_facts(world: str) -> dict[str, Any]:
    f = CASES["real_facts"][world]
    return {
        "keys": set(f["keys"]),
        "rules": set(f["rules"]),
        "reg": {k: tuple(v) for k, v in f["reg"].items()},
        "rule_lines": f["rule_lines"],
        "trace_source": f["trace_source"],
    }


def run_case(c: dict[str, Any]) -> Any:
    a, fx = c["args"], synth_facts()
    item = c["item"]
    if item == "C1":
        return S.S1.b1(a["trace"], set(a["denied"]))["pass"]
    if item == "C2":
        return S.c2(a["answer"], a["key"])["pass"]
    if item == "C3":
        return S.S1.b3(a["answer"], a["label"], fx["check"], fx["evidence"])["pass"]
    if item == "C4":
        return S.c4(a["answer"], fx["keys"], fx["reg"])["pass"]
    if item == "C5":
        r = S.c5(a["answer"], a["key"], fx["rules"], a["key_rules"], fx["rule_lines"],
                 fx["trace_source"])  # fmt: skip
        return r["pass"]
    if item == "C6":
        return S.S1.b6(a["answer"])["pass"]
    if item == "C8":
        r = S.c8(a["answer"], a["key"], a["elements"], fx["reg"])
        return None if r is None else r["pass"]
    raise AssertionError(item)


def main() -> int:
    bad: list[str] = []
    n = 0
    for c in CASES["scorer_cases"]:
        n += 1
        got = run_case(c)
        if got != c["expect"]:
            bad.append(f"{c['name']}: expected {c['expect']!r}, got {got!r}")
    for s in CASES["scorer_sessions"]:
        n += 1
        out = S.score(
            s["q"], s["answer"], s["trace"], synth_facts(), s["with_tools"], set(),
            s["world_unchanged"],
        )  # fmt: skip
        if out["items"] != s["expect"] or out["pass"] != s["expect_pass"]:
            bad.append(f"{s['name']}: {out['items']} pass={out['pass']}")
    for fx in CASES["round1_fixtures"]:
        n += 1
        f, q = real_facts(fx["world"]), fx["q"]
        ans = fx["answer"]
        got = {
            "C2": S.c2(ans, q["key"])["pass"],
            "C4": S.c4(ans, f["keys"], f["reg"])["pass"],
            "C5": S.c5(ans, q["key"], f["rules"], q.get("key_rules", []), f["rule_lines"],
                       f["trace_source"])["pass"],
        }  # fmt: skip
        c8 = S.c8(ans, q["key"], q.get("key_elements", []), f["reg"])
        got["C8"] = None if c8 is None else c8["pass"]
        if got != fx["expect"]:
            bad.append(f"{fx['name']}: expected {fx['expect']}, got {got}")
    qs = CASES["questions"]
    for q in qs:
        n += 1
        if q["key"] not in S.LABELS or (q["key"] == "AMBIGUOUS") != bool(q.get("key_elements")):
            bad.append(f"question {q['id']}: key {q['key']!r} or its elements are wrong")
        if (q["key"] == "REQUIRED-NOT-IMPLEMENTED") != bool(q.get("key_rules")):
            bad.append(f"question {q['id']}: key rules do not fit the key")
    n += 1
    ids = {q["id"] for q in qs}
    if len(qs) != 13 or not set(CASES["ablation"]) <= ids:
        bad.append("the question table is not 13 questions with its two ablations")
    for line in bad:
        print("FAIL", line)
    print(f"{n - len(bad)}/{n} scorer cases hold")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
