"""Mutants of the implementation diff (docs/results/2026-10-15-implementation-diff-prereg.md,
section 4): each is a text patch of a scratch copy of the code; the probes of
``dogfood/implementation_probes.py`` run against the patched copy and must see it.

A mutant is **killed** when some probe that passed on the unmutated copy (the control) does not pass
on the mutant, or, for PT (which fails on the control: a finding of the blind round), when its rows
differ from the control's. The control runs first. Run::

    "$DRV" dogfood/implementation_mutants.py --scratch "$SCRATCH/m" --out "$SCRATCH/m-data" \\
        --base "$SCRATCH/p/base" --workflows-dyn "$SCRATCH/wf/S-dyn.json" IM1 IM2 ... IM16
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
ASK = "lattice/spec/ask.py"
TOOLS = "lattice/mcp/tools.py"
QUICK = "PE,PR,PV,PO,PC,PA,PT,PW,PD,PB"

# id -> (description, the predicted catcher, [(file, old, new)], probes to run, docker)
MUTANTS: dict[str, dict[str, Any]] = {
    "IM1": {
        "what": "norm_route (no UUID templating) for scenario steps",
        "predicted": "PE (S spec-only != [])",
        "patch": [
            (
                IMPL,
                "sc.name: [route_key(s.method, s.path) for s in sc.steps if isinstance(s, Step)]",
                "sc.name: [s.method + ' ' + (s.path.split('?')[0].rstrip('/') or '/') "
                "for s in sc.steps if isinstance(s, Step)]",
            )
        ],
    },
    "IM2": {
        "what": "conditional routes not excluded from scenario links",
        "predicted": "PO (/test/clock not test_only)",
        "patch": [
            (
                IMPL,
                "keys = [k for k in sv.scenarios.get(name, []) if k not in conditional]",
                "keys = list(sv.scenarios.get(name, []))",
            )
        ],
    },
    "IM3": {
        "what": "a rule is implemented when ANY element is present",
        "predicted": "PR (S4 set < 29)",
        "patch": [
            (IMPL, "    elif missing:\n", "    elif missing and len(missing) == len(els):\n")
        ],
    },
    "IM4": {
        "what": "rule-text routes ignored",
        "predicted": "PR (R140 leaves S4's set)",
        "patch": [
            (
                IMPL,
                "    for k in routes_in_text(row.text):\n        req.add(",
                "    for k in []:\n        req.add(",
            )
        ],
    },
    "IM5": {
        "what": "any flow status counts as verified",
        "predicted": "PV",
        "patch": [
            (
                IMPL,
                'ids = sorted(f["id"] for f in fl if f["status"] == "verified")',
                'ids = sorted(f["id"] for f in fl)',
            )
        ],
    },
    "IM6": {
        "what": "stale check off",
        "predicted": "PV (stale row)",
        "patch": [
            (
                IMPL,
                "    changed = sorted(p for p in recorded if p in now and recorded[p] != now[p])",
                "    return None\n"
                "    changed = sorted(p for p in recorded if p in now and recorded[p] != now[p])",
            )
        ],
    },
    "IM7": {
        "what": "plumbing list applied to rule-linked routes",
        "predicted": "PE (/health), PO",
        "patch": [
            (IMPL, 'elif not linked and is_plumbing(r["path"]):', 'elif is_plumbing(r["path"]):')
        ],
    },
    "IM8": {
        "what": "quote matched anywhere in the file",
        "predicted": "PC (line +1)",
        "patch": [
            (
                IMPL,
                '    if " ".join(quote.split()) not in " ".join(line.split()):',
                '    if " ".join(quote.split()) not in " ".join(text.split()):',
            )
        ],
    },
    "IM9": {
        "what": "no root-containment or symlink check",
        "predicted": "PC (forged rows)",
        "patch": [
            (
                IMPL,
                "        if cur.is_symlink():\n"
                '            return f"{rel!r}: {comp!r} is a symbolic link"\n',
                "",
            ),
            (
                IMPL,
                "    if real != real_root and real_root not in real.parents:\n"
                '        return f"{rel!r} is outside the app root"\n',
                "",
            ),
        ],
    },
    "IM10": {
        "what": "the model's implementation label trusted",
        "predicted": "PA (b)",
        "patch": [
            (
                ASK,
                "settle_implementation(\n            label, good, digest.impl\n        )",
                "(label, 'static')",
            )
        ],
    },
    "IM11": {
        "what": "SYSTEM_IMPL without an inventory",
        "predicted": "PA (a), PB",
        "patch": [
            (
                ASK,
                'system = SYSTEM + ("\\n" + SYSTEM_IMPL if digest.impl is not None else "")',
                'system = SYSTEM + "\\n" + SYSTEM_IMPL',
            )
        ],
    },
    "IM12": {
        "what": "the 3 tools listed without [implementation]",
        "predicted": "PB",
        "patch": [
            (
                TOOLS,
                '        return False\n    return isinstance(cfg.get("implementation"), dict)\n',
                "        return True\n    return True\n",
            ),
        ],
    },
    "IM13": {
        "what": "the read path caches the report under the inventory",
        "predicted": "PW",
        "patch": [
            (
                IMPL,
                "        report = build_report(spec, trace, inv)\n"
                "        sv = SpecView.of(spec, trace)",
                "        report = build_report(spec, trace, inv)\n"
                "        (inv.directory / 'report-cache.json').write_text(report_json(report))\n"
                "        sv = SpecView.of(spec, trace)",
            )
        ],
    },
    "IM14": {
        "what": "an absolute path in the report",
        "predicted": "PD",
        "patch": [
            (
                IMPL,
                '            "workflows_consistency": inventory.consistency,\n',
                '            "workflows_consistency": inventory.consistency,\n'
                '            "app_root": str(inventory.app_root),\n',
            )
        ],
    },
    "IM15": {
        "what": "workflow_find ranks spec-only rules as exists",
        "predicted": "PT (S4 rows)",
        "patch": [
            (
                IMPL,
                'hint = "spec_only" if rows[0][3]["status"] == "spec_only" else "exists"',
                'hint = "exists"',
            ),
            (
                IMPL,
                "        hint = (\n"
                '            "spec_only" if top["status"] in '
                '("spec_only", "required_not_implemented") else "exists"\n        )\n',
                '        hint = "exists"\n',
            ),
        ],
    },
    "IM16": {
        "what": "verify writes a model scenario when a flow is cited",
        "predicted": "PF (generator calls > 0)",
        "patch": [
            (
                ASK,
                "        if cited:\n            return _verify_flow(",
                "        if False:\n            return _verify_flow(",
            )
        ],
        "only": "PW",
        "docker": True,
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


def run_probes(work: Path, args: argparse.Namespace, label: str, only: str, docker: bool):
    scratch = Path(args.scratch) / f"{label}-s"
    out = Path(args.scratch) / f"{label}-out"
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)
    scratch.mkdir(parents=True)
    base = Path(args.base)
    if base.is_dir():
        (scratch / "base").symlink_to(base)
    tmp = scratch / "tmp-run"
    tmp.mkdir()
    env = dict(os.environ, PYTHONPATH=str(work), PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(tmp))
    cmd = [
        sys.executable, str(work / "dogfood/implementation_probes.py"),
        "--streak-ref", "a0679e9", "--streak-ref2", "1d00ede", "--runs", "3",
        "--workflows-dyn", args.workflows_dyn, "--scratch", str(scratch), "--out", str(out),
        "--only", only,
    ]  # fmt: skip
    if not docker:
        cmd.append("--no-docker")
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=work, check=False)
    rows = {}
    if (out / "probes.json").is_file():
        rows = {r["id"]: r for r in json.loads((out / "probes.json").read_text())}
    return {
        "rc": p.returncode,
        "seconds": round(time.perf_counter() - t0, 1),
        "rows": rows,
        "stderr_tail": p.stderr.strip()[-600:],
    }


def verdicts(res: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for pid, r in res["rows"].items():
        if pid == "LEAK":
            continue
        if pid == "PT":
            got = [x["got"] for x in r["observed"].get("rows", [])]
            out[pid] = (r.get("pass"), r["observed"].get("hits"), got)
        else:
            out[pid] = r.get("pass")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="", help="the 454a8e6 archive of the probes' scratch")
    ap.add_argument("--workflows-dyn", required=True)
    ap.add_argument("ids", nargs="*")
    args = ap.parse_args(argv)
    ids = args.ids or sorted(MUTANTS, key=lambda s: int(s[2:]))
    scratch, out = Path(args.scratch), Path(args.out)
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    # the control: the unmutated copy, on the quick probes (and PW with Docker for IM16)
    controls: dict[str, dict[str, Any]] = {}
    for only, docker in ((QUICK, False), ("PW", True)):
        work = scratch / "control"
        copy_tree(work)
        controls[only] = run_probes(
            work, args, f"control-{only.replace(',', '')[:6]}", only, docker
        )
    ctrl = {k: verdicts(v) for k, v in controls.items()}
    results["control"] = {
        k: {"verdicts": ctrl[k], "seconds": controls[k]["seconds"], "rc": controls[k]["rc"]}
        for k in controls
    }
    print("control:", json.dumps(results["control"], default=str)[:800], flush=True)
    killed = 0
    for mid in ids:
        m = MUTANTS[mid]
        only, docker = m.get("only", QUICK), m.get("docker", False)
        work = scratch / mid
        copy_tree(work)
        apply_patch(work, m["patch"])
        res = run_probes(work, args, mid, only, docker)
        mv, cv = verdicts(res), ctrl[only]
        diff = sorted(
            pid for pid in cv
            if (cv[pid] is True and mv.get(pid) is not True)
            or (pid == "PT" and mv.get(pid) != cv[pid])
            or (pid in cv and pid not in mv)
        )  # fmt: skip
        results[mid] = {
            "what": m["what"],
            "predicted_catcher": m["predicted"],
            "caught_by": diff,
            "killed": bool(diff),
            "seconds": res["seconds"],
            "rc": res["rc"],
            "verdicts": mv,
            "stderr_tail": res["stderr_tail"] if not res["rows"] else "",
        }
        killed += bool(diff)
        print(mid, "KILLED" if diff else "SURVIVED", diff, m["what"], flush=True)
        shutil.rmtree(work, ignore_errors=True)
    results["summary"] = {"killed": killed, "of": len(ids)}
    (out / "mutants.json").write_text(
        json.dumps(results, indent=2, sort_keys=True, default=str) + "\n"
    )
    print(json.dumps(results["summary"]))
    return 0 if killed == len(ids) else 1


if __name__ == "__main__":
    raise SystemExit(main())
