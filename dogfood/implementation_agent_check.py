"""The scorer's own cases (run before the freeze): every item of
``dogfood/implementation_agent_scorer.py`` against the canned answers of
``dogfood/implementation_agent_scorer_cases.json``. Exit 0 when every case holds.

    python dogfood/implementation_agent_check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import implementation_agent_scorer as S  # noqa: E402

CASES = json.loads((HERE / "implementation_agent_scorer_cases.json").read_text())


def facts() -> dict[str, Any]:
    f = CASES["scorer_facts"]
    files, ev = f["files"], set(f["evidence"])
    return {
        "check": lambda p, n: p in files and 1 <= n <= files[p],
        "evidence": lambda p, n: f"{p}:{n}" in ev,
        "keys": set(f["keys"]),
        "rules": set(f["rules"]),
    }


def run_case(c: dict[str, Any]) -> Any:
    a, fx = c["args"], facts()
    item = c["item"]
    if item == "B1":
        return S.b1(a["trace"], set(a["denied"]))["pass"]
    if item == "B2":
        return S.b2(a["answer"], a["key"])["pass"]
    if item == "B3":
        return S.b3(a["answer"], a["label"], fx["check"], fx["evidence"])["pass"]
    if item == "B4":
        return S.b4(a["answer"], fx["keys"])["pass"]
    if item == "B5":
        return S.b5(a["answer"], a["key"], fx["rules"], a["key_rules"])["pass"]
    if item == "B6":
        return S.b6(a["answer"])["pass"]
    if item == "B8":
        r = S.b8(a["answer"], a["key"], a["elements"])
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
            s["q"],
            s["answer"],
            s["trace"],
            facts(),
            s["with_tools"],
            set(),
            s["world_unchanged"],
        )
        if out["items"] != s["expect"] or out["pass"] != s["expect_pass"]:
            bad.append(f"{s['name']}: {out['items']} pass={out['pass']}")
    for q in CASES["questions"]:
        n += 1
        if q["key"] not in S.LABELS or (q["key"] == "AMBIGUOUS") != bool(q.get("key_elements")):
            bad.append(f"question {q['id']}: key {q['key']!r} or its elements are wrong")
        if (q["key"] == "REQUIRED-NOT-IMPLEMENTED") != bool(q.get("key_rules")):
            bad.append(f"question {q['id']}: key rules do not fit the key")
    for line in bad:
        print("FAIL", line)
    print(f"{n - len(bad)}/{n} scorer cases hold")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
