"""Unit-test runner of the round-2 scorer (prereg section 2.3) and, from round 3, of R9
(``docs/results/2026-10-14-agent-run-3-prereg.md`` section 3.2: cases W01-W06, 90 in all).

Exits 0 only when every case gets its expected verdict.

    python dogfood/contracts/agent_scorer_check.py

Runs every case of ``agent_scorer_cases.json``. An R6 case is checked under mechanism A alone,
mechanism B alone and the combined verdict; the verdict of A alone / B alone includes the
pre-registered round-1 PINCLAIM clause (the combined rule's third operand), so that the
PINCLAIM case (C18, no first-person subject) fails under all three as the appendix states.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import agent_scorer as sc  # noqa: E402

CASES = HERE / "agent_scorer_cases.json"
PREFIX = "mcp__lattice__"


def run_case(c: dict[str, Any]) -> tuple[bool, str, str]:
    """(matches, expected, got) of one case."""
    kind, exp = c["kind"], c["expected"]
    if kind == "r6":
        r = sc.r6(c["text"])
        pin = r["PINCLAIM"] is not None
        a_claim = (not r["A"]["pass"]) or pin
        b_claim = (not r["B"]["pass"]) or pin
        comb_claim = not r["pass"]
        verdict = lambda claim: "claim" if claim else "ok"  # noqa: E731
        got = f"A={verdict(a_claim)} B={verdict(b_claim)} combined={verdict(comb_claim)}"
        ok = a_claim == b_claim == comb_claim == (exp == "claim")
        return ok, exp, got
    if kind == "r3b":
        r = sc.r3b(c["condition"], c["text"])
        assert r is not None
        got = "ok" if r["pass"] else "fail"
        return got == exp, exp, got
    if kind == "r1b":
        trace = []
        for i, t in enumerate(c["tools"]):
            err = t.endswith("!")
            trace.append(
                {"name": PREFIX + t.rstrip("!"), "id": f"t{i}", "is_error": err, "result": ""}
            )
        r = sc.r1b(trace, set())
        got = "pass" if r["pass"] else "fail"
        return got == exp, exp, got
    if kind == "r9":  # round 3: one tool_use of c["tool"] with c["input"]
        trace = [{"name": PREFIX + c["tool"], "id": "t0", "input": c["input"], "is_error": False}]
        r = sc.r9(trace)
        got = "default" if r["n_default_calls"] == 1 else "not"
        return got == exp, exp, got
    raise AssertionError(f"unknown kind {kind}")


def main() -> int:
    doc = json.loads(CASES.read_text())
    cases = doc["cases"]
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
        mark = "ok" if match else "MISMATCH"
        print(f"{c['id']:4} {c['kind']:4} expected {exp:5} got {got:42} {mark}")
    for kind, (g, n) in sorted(kinds.items()):
        print(f"{kind}: {g}/{n}")
    print(f"cases: {good}/{len(cases)} as expected")
    return 0 if good == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
