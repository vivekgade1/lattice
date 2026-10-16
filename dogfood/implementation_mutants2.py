"""Mutants of the round-2 `workflow_find` ranking and exercise-2 scorer (section 4 of
``docs/results/2026-10-16-implementation-diff-round2-prereg.md``): each is a text patch of a scratch
copy of the code; the round-2 probes (``implementation_probes2.py``: PT2A, PTDEV, PTF, PT3, PB2)
and the scorer cases (``implementation_agent2_check.py``) run against the patched copy.

A mutant is **killed** when a gated probe that passed on the control (the unmutated copy, run
first) does not pass on the mutant, when any PT2 (G), PT-dev or PT-F row differs from the
control's, when a scorer case flips, or when a PB2 check differs. Run::

    "$PY" dogfood/implementation_mutants2.py --scratch "$SCRATCH/m2" --out "$OUT" \\
        --workflows-dyn "$WF" --base2 "$SCRATCH/p2/base2" WM1 ... WM14
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent

IMPL = "lattice/spec/implementation.py"
SCORER = "dogfood/implementation_agent2_scorer.py"
TOOLS = "lattice/mcp/tools.py"
QUICK = "PT2,PTDEV,PTF,PT3,PB2"
GATED = ("PT2A", "PTDEV", "PB2")

STATUS_OLD = (
    '        if e["status"] not in ("implemented", "implemented_not_required", "spec_only"):\n'
)
SEVERAL_OLD = '    elif len(rows) > 1 and rows[1]["score"] >= (1 - FIND_TIE) * rows[0]["score"]:\n'
SEVERAL_NEW = (
    '    elif len(rows) > 1 and rows[1]["score"] + rows[1]["tie"] >= (1 - FIND_TIE) * (\n'
    '        rows[0]["score"] + rows[0]["tie"]\n    ):\n'
)
STATUS_WM11 = (
    '        if e["status"] not in (\n'
    '            "implemented",\n            "implemented_not_required",\n'
    '            "spec_only",\n'
    '            "test_only",\n            "plumbing",\n        ):\n'
)

MUTANTS: dict[str, dict[str, Any]] = {
    "WM1": {
        "what": "the rules' texts as a primary field (round 1's document)",
        "predicted": "PT2, PT-dev",
        "patch": [
            (
                IMPL,
                '        spec_only = e["status"] == "spec_only"\n',
                '        spec_only = e["status"] == "spec_only"\n'
                '        fields["path"] = fields["path"] | frozenset(\n'
                '            __import__("lattice.spec.ask", fromlist=["terms"]).terms(\n'
                '                " ".join(texts.get(x, "") for x in linked)\n'
                "            )\n"
                "        )\n",
            )
        ],
    },
    "WM2": {
        "what": "verbs literal (no verb classes)",
        "predicted": "PT-dev (delete endpoint DELETE)",
        "patch": [
            (
                IMPL,
                '    classes = {w: "V:" + "/".join(sorted(ms)) for w, ms in verbs.items()}\n',
                "    classes: dict[str, str] = {}\n",
            )
        ],
    },
    "WM3": {
        "what": "no compounds",
        "predicted": "PT-F (F04)",
        "patch": [(IMPL, "        if joined in vocab and len(a) > 1:\n", "        if False:\n")],
    },
    "WM4": {
        "what": "no resource penalty",
        "predicted": "PT2 (G09), PT-F (F01, F24)",
        "patch": [
            (
                IMPL,
                '        if d["last"] is not None and not d["last"].intersection(query):\n',
                "        if False:\n",
            )
        ],
    },
    "WM5": {
        "what": "out-of-vocabulary terms weigh 0 in the coverage",
        "predicted": "PT2 (G13-G15)",
        "patch": [
            (
                IMPL,
                "            return math.log(1 + n / cdf[w]) if w in cdf else idf_max\n",
                "            return math.log(1 + n / cdf[w]) if w in cdf else 0.0\n",
            )
        ],
    },
    "WM6": {
        "what": "the anchor rule off",
        "predicted": "PT-dev (agent query Q5)",
        "patch": [
            (
                IMPL,
                '        anchored = any(where in anchors for where in top["hit"].values())\n',
                "        anchored = True\n",
            )
        ],
    },
    "WM7": {
        "what": "several decided on S + T",
        "predicted": "PT2 (G11)",
        "patch": [(IMPL, SEVERAL_OLD, SEVERAL_NEW)],
    },
    "WM8": {
        "what": "spec-only endpoints are not candidates",
        "predicted": "PT2 (G16-G19)",
        "patch": [
            (
                IMPL,
                STATUS_OLD,
                '        if e["status"] not in ("implemented", "implemented_not_required"):\n',
            )
        ],
    },
    "WM9": {
        "what": "a spec-only top is answered exists (round 1's IM15)",
        "predicted": "PT2 (G16)",
        "patch": [
            (
                IMPL,
                '    elif rows[0]["exact"]:\n'
                '        hint = "spec_only" if rows[0]["d"]["spec_only"] else "exists"\n',
                '    elif rows[0]["exact"]:\n        hint = "exists"\n',
            ),
            (
                IMPL,
                '    else:\n        hint = "spec_only" if rows[0]["d"]["spec_only"] else "exists"\n'
                "    cands = []\n",
                '    else:\n        hint = "exists"\n    cands = []\n',
            ),
        ],
    },
    "WM10": {
        "what": "flows as separate candidates",
        "predicted": "PT2, PT-F",
        "patch": [
            (
                IMPL,
                '    for m in rep["machines"]:\n        docs[m["key"]] = {\n',
                '    for f in rep["flows"]:\n'
                "        docs[f\"impl:flow:{f['id']}\"] = {\n"
                '            "kind": "flow",\n'
                '            "status": f["effective"],\n'
                '            "spec_only": False,\n'
                '            "fields": {"path": _find_split(f["name"], lex)},\n'
                '            "method": None,\n'
                '            "last": None,\n'
                '            "tie": "",\n'
                '            "keys": list(f["routes"]),\n'
                '            "evidence": [],\n'
                '            "flows": None,\n'
                '            "rules": None,\n'
                "        }\n"
                '    for m in rep["machines"]:\n        docs[m["key"]] = {\n',
            )
        ],
    },
    "WM11": {
        "what": "plumbing and test_only endpoints as candidates",
        "predicted": "PT2 (G24)",
        "patch": [
            (
                IMPL,
                STATUS_OLD,
                STATUS_WM11,
            )
        ],
    },
    "WM12": {
        "what": "C4 accepts a group-relative path without the registration citation",
        "predicted": "scorer cases",
        "patch": [
            (
                SCORER,
                '    for k in cited:\n        m, _, p = k.partition(" ")\n',
                '    for k in sorted(keys):\n        m, _, p = k.partition(" ")\n',
            )
        ],
    },
    "WM13": {
        "what": "C2/C8 accept an AMBIGUOUS key with an implemented label or one element",
        "predicted": "scorer cases",
        "patch": [
            (
                SCORER,
                "    return S1.b2(answer, key)\n",
                "    r = S1.b2(answer, key)\n"
                '    if key == "AMBIGUOUS" and r["label"] in (\n'
                '        "IMPLEMENTED-VERIFIED",\n        "IMPLEMENTED-UNVERIFIED",\n    ):\n'
                '        r = {**r, "pass": True}\n    return r\n',
            ),
            (
                SCORER,
                '    return {"pass": len(named) >= 2, "named": named, "of": len(elements)}\n',
                '    return {"pass": len(named) >= 1, "named": named, "of": len(elements)}\n',
            ),
        ],
    },
    "WM14": {
        "what": "the workflow_find description edited",
        "predicted": "PB2",
        "patch": [
            (
                TOOLS,
                "Lexical match, not understanding. ",
                "Lexical match, not understanding (ranked by field). ",
            )
        ],
    },
}


def apply_patch(work: Path, patch: list[tuple[str, str, str]]) -> None:
    for rel, old, new in patch:
        p = work / rel
        text = p.read_text()
        if text.count(old) != 1:
            raise SystemExit(f"{rel}: patch target found {text.count(old)} times: {old[:70]!r}")
        p.write_text(text.replace(old, new))


def copy_tree(dest: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for name in ("lattice", "dogfood", "tests"):
        shutil.copytree(
            REPO / name,
            dest / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "fixtures_big", "goldens"),
            symlinks=True,
        )
    shutil.copy(REPO / "pyproject.toml", dest / "pyproject.toml")


def run_probes(work: Path, args: argparse.Namespace, label: str) -> dict[str, Any]:
    scratch = Path(args.scratch) / f"{label}-s"
    out = Path(args.scratch) / f"{label}-out"
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)
    scratch.mkdir(parents=True)
    tmp = scratch / "tmp-run"
    tmp.mkdir()
    env = dict(os.environ, PYTHONPATH=str(work), PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(tmp))
    cmd = [
        sys.executable, str(work / "dogfood/implementation_probes2.py"),
        "--workflows-dyn", args.workflows_dyn, "--scratch", str(scratch), "--out", str(out),
        "--only", QUICK, "--no-docker", "--git-repo", str(REPO), "--base2", args.base2,
        "--data", str(REPO / "docs/results/data/2026-10-16-implementation-diff-round2"),
    ]  # fmt: skip
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=work, check=False)
    rows = {}
    if (out / "probes.json").is_file():
        rows = {r["id"]: r for r in json.loads((out / "probes.json").read_text())}
    chk = subprocess.run(
        [sys.executable, str(work / "dogfood/implementation_agent2_check.py")],
        capture_output=True, text=True, env=env, cwd=work, check=False,
    )  # fmt: skip
    return {
        "rc": p.returncode,
        "seconds": round(time.perf_counter() - t0, 1),
        "rows": rows,
        "scorer_cases": (chk.stdout.strip().splitlines() or [""])[-1],
        "scorer_rc": chk.returncode,
        "scorer_failures": [ln for ln in chk.stdout.splitlines() if ln.startswith("FAIL")],
        "stderr_tail": p.stderr.strip()[-600:],
    }


def signature(rows: dict[str, Any]) -> dict[str, Any]:
    """The per-row results of PT2 (G), PT-dev and PT-F: what a mutant must not change."""
    sig: dict[str, Any] = {}
    r = rows.get("PT2A")
    if r and r.get("ran", True):
        for x in r["observed"]["rows"]:
            sig[f"G:{x['id']}"] = x["got"]
    r = rows.get("PTDEV")
    if r and r.get("ran", True):
        o = r["observed"]
        for x in o["PT_rows"]:
            sig[f"PT:{x['id']}"] = [x["got"], [t[0] for t in x["top"]]]
        for x in o["agent_own_rows"]:
            sig[f"agent:{x['id']}"] = x["got"]
        for x in o["agent_question_rows"]:
            sig[f"agentq:{x['id']}"] = x["got"]
    r = rows.get("PTF")
    if r and r.get("ran", True):
        for x in r["observed"]["rows"]:
            sig[f"F:{x['id']}"] = x["got"]
    return sig


def verdict(res: dict[str, Any]) -> dict[str, Any]:
    rows = res["rows"]
    gated = {
        pid: (rows[pid]["pass"] if pid in rows and rows[pid].get("ran", True) else None)
        for pid in GATED
    }
    pb2 = (rows.get("PB2") or {}).get("observed", {}).get("checks")
    return {
        "gated": gated,
        "signature": signature(rows),
        "pb2_checks": pb2,
        "scorer_rc": res["scorer_rc"],
        "scorer_failures": res["scorer_failures"],
        "pt3": [x["got"] for x in (rows.get("PT3") or {}).get("observed", {}).get("rows", [])],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base2", required=True, help="a bb3ff0c archive")
    ap.add_argument("--workflows-dyn", required=True)
    ap.add_argument("ids", nargs="*")
    args = ap.parse_args(argv)
    ids = args.ids or sorted(MUTANTS, key=lambda s: int(s[2:]))
    scratch, out = Path(args.scratch), Path(args.out)
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    work = scratch / "control"
    copy_tree(work)
    control = run_probes(work, args, "control")
    cv = verdict(control)
    results: dict[str, Any] = {
        "control": {
            "gated": cv["gated"],
            "rows": len(cv["signature"]),
            "scorer_cases": control["scorer_cases"],
            "pb2_checks": cv["pb2_checks"],
            "seconds": control["seconds"],
            "rc": control["rc"],
        }
    }
    print("control:", json.dumps(results["control"], default=str)[:900], flush=True)
    if any(v is not True for v in cv["gated"].values()) or control["scorer_rc"] != 0:
        print("the control does not pass its gated probes: mutants are not run", flush=True)
        (out / "mutants.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
        return 1
    killed = 0
    for mid in ids:
        m = MUTANTS[mid]
        work = scratch / mid
        copy_tree(work)
        apply_patch(work, m["patch"])
        res = run_probes(work, args, mid)
        mv = verdict(res)
        by: list[str] = []
        by += [
            f"{pid} fails"
            for pid, ok in mv["gated"].items()
            if cv["gated"][pid] is True and ok is not True
        ]
        changed = sorted(k for k in cv["signature"] if mv["signature"].get(k) != cv["signature"][k])
        if changed:
            by.append(f"rows differ: {', '.join(changed)}")
        if mv["scorer_rc"] != 0:
            by.append(f"scorer cases flip: {len(mv['scorer_failures'])}")
        if mv["pb2_checks"] and cv["pb2_checks"]:
            bad = sorted(k for k, v in mv["pb2_checks"].items() if v != cv["pb2_checks"].get(k))
            if bad:
                by.append(f"PB2 differs: {', '.join(bad)}")
        results[mid] = {
            "what": m["what"],
            "predicted_catcher": m["predicted"],
            "caught_by": by,
            "killed": bool(by),
            "gated": mv["gated"],
            "rows_changed": changed,
            "pt3_rows_changed": sum(
                1 for a, b in zip(mv["pt3"], cv["pt3"], strict=False) if a != b
            ),
            "scorer_failures": mv["scorer_failures"][:6],
            "seconds": res["seconds"],
            "rc": res["rc"],
            "stderr_tail": res["stderr_tail"] if not res["rows"] else "",
        }
        killed += bool(by)
        print(mid, "KILLED" if by else "SURVIVED", by, m["what"], flush=True)
        shutil.rmtree(work, ignore_errors=True)
    results["summary"] = {"killed": killed, "of": len(ids)}
    (out / "mutants.json").write_text(
        json.dumps(results, indent=2, sort_keys=True, default=str) + "\n"
    )
    print(json.dumps(results["summary"]))
    return 0 if killed == len(ids) else 1


if __name__ == "__main__":
    raise SystemExit(main())
