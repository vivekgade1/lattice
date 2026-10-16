"""P4: the install summary under bb3ff0c's tree and the worktree, N runs, fixed paths.

usage: python p4_summary.py --old OLD_TREE --new NEW_TREE --runs N --scratch DIR
Prints one JSON object on stdout (the pass/fail per case and the raw diffs).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--old", required=True)
ap.add_argument("--new", required=True)
ap.add_argument("--runs", type=int, default=10)
ap.add_argument("--scratch", required=True)
a = ap.parse_args()
old, new = Path(a.old).resolve(), Path(a.new).resolve()
scratch = Path(a.scratch).resolve()
scratch.mkdir(parents=True, exist_ok=True)
child = Path(__file__).with_name("p4_child.py")


def run(tree: Path) -> str:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(scratch / "home"),
        "TMPDIR": os.environ.get("TMPDIR", str(scratch)),
        "PYTHONPATH": str(tree),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LC_ALL": "C",
    }
    (scratch / "home").mkdir(exist_ok=True)
    r = subprocess.run(
        [sys.executable, str(child), str(tree), str(new), str(scratch / "fx")],
        env=env, capture_output=True, text=True, cwd=str(scratch),
    )
    if r.returncode != 0:
        raise SystemExit(f"child failed under {tree}: {r.stderr[-2000:]}")
    return r.stdout


olds = [run(old) for _ in range(a.runs)]
news = [run(new) for _ in range(a.runs)]
ident_old = len(set(olds)) == 1
ident_new = len(set(news)) == 1
o, n = json.loads(olds[0]), json.loads(news[0])

LINE = {
    "f": "  entry:    lattice.security (CODE: a Python module, main(argv); not imported by install)",
    "g": "  entry:    evilentry (CODE: a Python module, main(argv); not imported by install)",
    "h": '  entry:    "a\\nb" (CODE: a Python module, main(argv); not imported by install)',
    "i": '  entry:    "" (CODE: a Python module, main(argv); not imported by install)',
}
rows = {}
for case in "abcde":
    rows[case] = {"expect": "byte-identical", "ok": o[case]["describe"] == n[case]["describe"]}
for case in "afghi":
    pass
for case in "fghi":
    ol = o[case]["describe"].split("\n")
    nl = n[case]["describe"].split("\n")
    # position: right after the requires line
    idx = next(i for i, ln in enumerate(ol) if ln.startswith("  requires:"))
    want = ol[: idx + 1] + [LINE[case]] + ol[idx + 1 :]
    rows[case] = {
        "expect": "old + exactly the D2 line after requires:",
        "ok": nl == want,
        "new_line": nl[idx + 1] if len(nl) > idx + 1 else None,
        "old_has_no_entry_line": not any(ln.startswith("  entry:") for ln in ol),
    }
    if nl != want:
        rows[case]["observed_new"] = n[case]["describe"]
for case in ("install-a", "install-c"):
    rows[case] = {"expect": "byte-identical", "ok": o[case] == n[case]}
    if o[case] != n[case]:
        rows[case]["old"], rows[case]["new"] = o[case], n[case]
res = {
    "runs": a.runs,
    "old_identical_across_runs": ident_old,
    "new_identical_across_runs": ident_new,
    "old_sha256": hashlib.sha256(olds[0].encode()).hexdigest(),
    "new_sha256": hashlib.sha256(news[0].encode()).hexdigest(),
    "rows": rows,
    "pass": ident_old and ident_new and all(r["ok"] for r in rows.values()),
    "old_output": o,
    "new_output": n,
}
print(json.dumps(res, indent=1, sort_keys=True))
