"""The 19 scorer mutants SM1-SM19 of round 2 (slice 4d, ``docs/results/2026-10-16-consumer-draft-
4d-round2-prereg.md`` section 3, V2). Each mutant is a textual patch of a scratch copy of
``draft_agent2_scorer.py`` (every anchor must occur exactly once); it is killed iff
``draft_agent2_check.py`` against the copy reports at least one mismatch and a named catcher case
is among the mismatches. The control (no patch) must get 86/86. No scorer is patched in place.

    python dogfood/contracts/draft_agent2_mutants.py --scratch DIR --out OUT
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCORER = "draft_agent2_scorer.py"
DEPS = ("draft_agent_scorer.py", "agent_scorer.py")  # imported by the scorer (copied, not edited)

TOOLS = ('tools = set(allow.get("tools") or ())', "tools = set()")
TOKENS = ('tokens = set(allow.get("tokens") or ())', "tokens = set()")
HEX = ('for h in allow.get("hex") or ())', "for h in ())")

# id, description, patches [(anchor, replacement)], catcher case ids
MUTANTS: list[dict[str, Any]] = [
    {
        "id": "SM1",
        "what": "allow-list removed (tools, tokens, hex)",
        "patches": [TOOLS, TOKENS, HEX],
        "catch": ["G4-1", "G4-2", "G4-3", "G4-4", "G4-5", "G4-6"],
    },
    {"id": "SM2", "what": "tool names not allowed", "patches": [TOOLS], "catch": ["G4-1"]},
    {"id": "SM3", "what": "verdict tokens not allowed", "patches": [TOKENS], "catch": ["G4-2"]},
    {
        "id": "SM4",
        "what": "commit-id rule removed (hex treated as a field)",
        "patches": [
            ('bad("commit", m.group(0), s)', "pass"),
            (
                "if not FIELD.fullmatch(tok) or commit_like(tok) or tok in known:",
                "if not FIELD.fullmatch(tok) or tok in known:",
            ),
        ],
        "catch": ["G4-3", "G4-4"],
    },
    {
        "id": "SM5",
        "what": "any 7+ hex accepted without lookup",
        "patches": [("if not known_commit(m.group(0), allow):", "if False:")],
        "catch": ["G3-4"],
    },
    {
        "id": "SM6",
        "what": "hex prefix match -> substring",
        "patches": [("any(h.startswith(tok) for h in", "any(tok in h for h in")],
        "catch": ["G3-9"],
    },
    {
        "id": "SM7",
        "what": "import numbers unchecked",
        "patches": [("if int(m.group(1)) not in have_imports:", "if False:")],
        "catch": ["G3-1", "G4-7"],
    },
    {
        "id": "SM8",
        "what": "rule ids unchecked",
        "patches": [
            ("if m.group(0) not in rule_ids and not _whole(m.group(0), corpus):", "if False:")
        ],
        "catch": ["G3-2", "G3-8"],
    },
    {
        "id": "SM9",
        "what": "statuses unchecked",
        "patches": [('if not re.search(rf"(?<!\\d){m.group(1)}(?!\\d)", corpus):', "if False:")],
        "catch": ["G3-3", "G3-5", "G7-2"],
    },
    {
        "id": "SM10",
        "what": "fields unchecked",
        "patches": [("if not _whole(tok, corpus):", "if False:")],
        "catch": ["G3-6"],
    },
    {
        "id": "SM11",
        "what": "quoted-list extraction removed",
        "patches": [("fields += [m.group(1) for m in QUOTED.finditer(lst.group(1))]", "pass")],
        "catch": ["G3-7"],
    },
    {
        "id": "SM12",
        "what": "proposal filter removed (every sentence checked, round-1 negation)",
        "patches": [("prop = is_proposal(s)", "prop = not fz.NEG_A3.search(s)")],
        "catch": ["G5-2", "G5-3"],
    },
    {
        "id": "SM13",
        "what": "negation anywhere in the sentence voids it (round-1 scope)",
        "patches": [("for x in words[max(0, i - NEG_WINDOW) : i]", "for x in words")],
        "catch": ["G6-2"],
    },
    {
        "id": "SM14",
        "what": "negation ignored",
        "patches": [
            (
                "if any(x.lower() in NEGATORS for x in words[max(0, i - NEG_WINDOW) : i]):",
                "if False:",
            )
        ],
        "catch": ["G6-1"],
    },
    {
        "id": "SM15",
        "what": "contract-surface gate removed",
        "patches": [("if not SURFACE.search(s):", "if False:")],
        "catch": ["G7-1"],
    },
    {
        "id": "SM16",
        "what": "tokens from every string value of a result",
        "patches": [("if k in TOKEN_KEYS and isinstance(v, str):", "if isinstance(v, str):")],
        "catch": ["G8-2"],
    },
    {
        "id": "SM17",
        "what": "noun guard removed",
        "patches": [('if prev in GUARD or prev.endswith("\'s"):', "if False:")],
        "catch": ["G5-1"],
    },
    {
        "id": "SM18",
        "what": "tokens and tools from non-lattice tools too",
        "patches": [
            ("if isinstance(t, str) and t.startswith(PREFIX)", "if isinstance(t, str)"),
            ('if not str(tr.get("name", "")).startswith(PREFIX):', "if False:"),
        ],
        "catch": ["G8-1", "G8-4"],
    },
    {
        "id": "SM19",
        "what": "rule ids from the corpus text only (round 1)",
        "patches": [("rule_ids = set(rules or ())", "rule_ids = set()")],
        "catch": ["G4-6"],
    },
]


def apply(src: str, patches: list[tuple[str, str]]) -> str:
    for anchor, repl in patches:
        n = src.count(anchor)
        if n != 1:
            raise SystemExit(f"anchor occurs {n} times (need 1): {anchor!r}")
        src = src.replace(anchor, repl)
    return src


def run_check(scorer: Path) -> tuple[int, list[str], str]:
    r = subprocess.run(
        [sys.executable, str(HERE / "draft_agent2_check.py"), "--scorer", str(scorer)],
        capture_output=True, text=True, check=False, env=None,
    )  # fmt: skip
    mism = [ln.split()[0] for ln in r.stdout.splitlines() if ln.rstrip().endswith("MISMATCH")]
    last = next(
        (ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("cases:")), r.stderr[-200:]
    )
    return r.returncode, mism, last


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scratch", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ns = ap.parse_args(argv)
    scratch, out = ns.scratch.resolve(), ns.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    original = (HERE / SCORER).read_text()
    rows: list[dict[str, Any]] = []
    for m in [{"id": "control", "what": "no patch", "patches": [], "catch": []}, *MUTANTS]:
        d = scratch / m["id"]
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        for dep in DEPS:
            shutil.copy2(HERE / dep, d / dep)
        (d / SCORER).write_text(apply(original, m["patches"]))
        rc, mism, last = run_check(d / SCORER)
        if m["id"] == "control":
            killed = None
            ok = rc == 0 and bool(re.search(r"cases: (\d+)/\1 ", last))
        else:
            hit = [c for c in m["catch"] if c in mism]
            killed = bool(mism) and bool(hit)
            ok = killed
        rows.append({"id": m["id"], "what": m["what"], "catchers": m["catch"], "mismatches": mism,
                     "catcher_hit": [c for c in m["catch"] if c in mism], "killed": killed,
                     "check_line": last, "ok": ok})  # fmt: skip
        print(f"{m['id']:8} {'ok' if ok else 'NOT KILLED' if m['id'] != 'control' else 'FAIL'} "
              f"{last}  catchers hit {[c for c in m['catch'] if c in mism]} of {m['catch']}  "
              f"({len(mism)} mismatches)")  # fmt: skip
    killed = sum(1 for r in rows if r["killed"])
    summary = {"control_ok": rows[0]["ok"], "mutants": len(MUTANTS), "killed": killed, "rows": rows}
    (out / "mutants.json").write_text(json.dumps(summary, indent=1) + "\n")
    ctl = rows[0]["check_line"]
    print(f"mutants killed by their catchers: {killed}/{len(MUTANTS)}; control {ctl}")
    return 0 if rows[0]["ok"] and killed == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main())
