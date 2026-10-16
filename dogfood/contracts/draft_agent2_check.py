"""Case-table runner of the round-2 scorer (slice 4d, ``docs/results/2026-10-16-consumer-draft-4d-
round2-prereg.md`` section 3, V1). Exits 0 only when every case of
``draft_agent2_scorer_cases.json`` gets its expected verdict and the allow-list fixtures hold.

    python dogfood/contracts/draft_agent2_check.py [--scorer PATH]   # PATH: a (mutated) scorer copy
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

CASES = HERE / "draft_agent2_scorer_cases.json"
PREFIX = "mcp__lattice__"
# tokens that G3-G7 expect to fail: no fixture allow-list may contain one
FAIL_TOKENS = ("next_token", "nxt", "state_x", "green", "linked_question_fix", "retry_after",
               "state_code", "status_code")  # fmt: skip


def load_scorer(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("draft_agent2_scorer", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["draft_agent2_scorer"] = mod
    spec.loader.exec_module(mod)
    return mod


def fixture_problems(fx: dict[str, Any]) -> list[str]:
    bad: list[str] = []
    for name, al in fx["allow"].items():
        have = set(al["tools"]) | set(al["tokens"]) | set(al["words"])
        for need in ("contract_check", "ok"):
            if need not in have:
                bad.append(f"{name} lacks {need}")
        bad += [f"{name} contains {t}" for t in FAIL_TOKENS if t in have]
    return bad


def run_case(sc: Any, fx: dict[str, Any], c: dict[str, Any]) -> tuple[bool, str, str]:
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
    elif kind == "a3":  # round 1's A3 cases, scored by A3b with an empty allow-list
        got = "pass" if sc.a3b(c["text"], c["corpus"], {})["pass"] else "fail"
    elif kind == "a3b":
        corpus = fx["corpora"][c["corpus"]]
        allow = fx["allow"][c["allow"]]
        got = (
            "pass" if sc.a3b(c["text"], corpus["text"], allow, corpus["rules"])["pass"] else "fail"
        )
    elif kind == "a4":
        got = "pass" if sc.a4(c["text"])["pass"] else "fail"
    elif kind == "a5":
        got = "pass" if sc.a5(c["text"])["pass"] else "fail"
    elif kind == "a7":
        got = "pass" if sc.a7(c["text"])["pass"] else "fail"
    elif kind == "allow_from":
        al = sc.allow_from(c["init"], c["results"], c["lock"], c["question"], c["draft"])
        e = c["expect"]
        ok = True
        if "tools_eq" in e:
            ok &= sorted(al["tools"]) == sorted(e["tools_eq"])
        ok &= all(t in al["tokens"] for t in e.get("tokens_has", []))
        ok &= not any(t in al["tokens"] for t in e.get("tokens_lacks", []))
        ok &= all(h in al["hex"] for h in e.get("hex_has", []))
        ok &= all(sc.known_commit(t, al) for t in e.get("commit_known", []))
        ok &= not any(sc.known_commit(t, al) for t in e.get("commit_unknown", []))
        got = "pass" if ok else "fail"
    else:
        raise AssertionError(f"unknown kind {kind}")
    return got == exp, exp, got


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scorer", type=Path, default=HERE / "draft_agent2_scorer.py")
    ns = ap.parse_args(argv)
    sc = load_scorer(ns.scorer.resolve())
    doc = json.loads(CASES.read_text())
    fx, cases = doc["fixtures"], doc["cases"]
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    problems = fixture_problems(fx)
    for p in problems:
        print(f"FIXTURE PROBLEM {p}")
    good = 0
    groups: dict[str, list[int]] = {}
    for c in cases:
        match, exp, got = run_case(sc, fx, c)
        good += match
        g = groups.setdefault(c.get("group", "r1-" + c["kind"]), [0, 0])
        g[1] += 1
        g[0] += match
        print(f"{c['id']:6} {c['kind']:10} expected {exp:5} got {got:5} "
              f"{'ok' if match else 'MISMATCH'}")  # fmt: skip
    for name, (g, n) in sorted(groups.items()):
        print(f"{name}: {g}/{n}")
    print(f"cases: {good}/{len(cases)} as expected")
    return 0 if good == len(cases) and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
