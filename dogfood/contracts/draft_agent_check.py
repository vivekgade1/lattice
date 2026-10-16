"""Unit-test runner of the P3 scorer of the consumer-side draft (slice 4d,
``docs/results/2026-10-15-consumer-draft-4c-prereg.md`` section 3, P3). Exits 0 only when every
case of ``draft_agent_scorer_cases.json`` gets its expected verdict.

    python dogfood/contracts/draft_agent_check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import draft_agent_scorer as sc  # noqa: E402

CASES = HERE / "draft_agent_scorer_cases.json"
PREFIX = "mcp__lattice__"


def run_case(c: dict[str, Any]) -> tuple[bool, str, str]:
    """(matches, expected, got) of one case."""
    kind, exp = c["kind"], c["expected"]
    if kind == "a1":
        trace = [
            {"name": PREFIX + t.rstrip("!"), "id": f"t{i}", "is_error": t.endswith("!"),
             "result": ""}
            for i, t in enumerate(c["tools"])
        ]  # fmt: skip
        got = "pass" if sc.a1(trace)["pass"] else "fail"
    elif kind == "a2":
        got = "pass" if sc.a2(c["world"], c["text"])["pass"] else "fail"
    elif kind == "a3":
        got = "pass" if sc.a3(c["text"], c["corpus"])["pass"] else "fail"
    elif kind == "a4":
        got = "pass" if sc.a4(c["text"])["pass"] else "fail"
    elif kind == "a5":
        got = "pass" if sc.a5(c["text"])["pass"] else "fail"
    elif kind == "a7":
        got = "pass" if sc.a7(c["text"])["pass"] else "fail"
    else:
        raise AssertionError(f"unknown kind {kind}")
    return got == exp, exp, got


def main() -> int:
    cases = json.loads(CASES.read_text())["cases"]
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    good = 0
    kinds: dict[str, list[int]] = {}
    for c in cases:
        match, exp, got = run_case(c)
        good += match
        k = kinds.setdefault(c["kind"], [0, 0])
        k[1] += 1
        k[0] += match
        print(f"{c['id']:4} {c['kind']:3} expected {exp:5} got {got:5} "
              f"{'ok' if match else 'MISMATCH'}")  # fmt: skip
    for kind, (g, n) in sorted(kinds.items()):
        print(f"{kind}: {g}/{n}")
    print(f"cases: {good}/{len(cases)} as expected")
    return 0 if good == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
