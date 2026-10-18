"""PM-S driver (Arm M of Spike I): the byte-compat of Streak's import outputs, two lattice trees.

Usage: pms_driver.py --tree <lattice tree> --label <base|new> --scratch DIR --out OUT.json
The Streak clones (S = a0679e9, S4 = 1d00ede, from `git archive`) live under DIR/streak/ and are
shared by both runs so that absolute paths in outputs are identical. Nothing here is a frozen file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

WF = Path("/Users/vivekgade/Documents/lattice-ia/docs/results/data/2026-10-15-workflow-layer/workflows.json")
CANDS = Path("/Users/vivekgade/Documents/lattice-ia/docs/results/data/2026-10-16-goto-place/dev-candidates")
GO_ENV = {"GOFLAGS": "-mod=mod", "GOPROXY": "off", "GOTOOLCHAIN": "local", "GOWORK": "off"}


def sha(b: bytes | str) -> str:
    return hashlib.sha256(b.encode() if isinstance(b, str) else b).hexdigest()


def tree_sha(d: Path) -> dict[str, str]:
    return {p.relative_to(d).as_posix(): sha(p.read_bytes()) for p in sorted(d.rglob("*")) if p.is_file()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tree, scratch = Path(a.tree), Path(a.scratch)
    work = scratch / f"pms-{a.label}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    env = dict(os.environ, **GO_ENV)
    env.update(
        PYTHONPATH=str(tree),
        PYTHONDONTWRITEBYTECODE="1",
        NO_COLOR="1",
        TMPDIR=str(work),
        LATTICE_GOCHECK_CACHE=str(work / "gocache"),
        LATTICE_GRPCBRIDGE_CACHE=str(work / "bridgecache"),
    )
    env.pop("ANTHROPIC_API_KEY", None)
    py = sys.executable
    out: dict[str, object] = {"tree": str(tree), "label": a.label}

    def run(args, cwd=None, inp=None):
        p = subprocess.run([py, *args], capture_output=True, text=True, env=env, cwd=cwd or tree, input=inp, check=False)
        return p

    clones = {"S": scratch / "streak" / "S", "S4": scratch / "streak" / "S4"}
    # the extractor binary of this tree
    p = run(["-c", "from lattice.gocheck import build_gocheck; print(build_gocheck())"])
    assert p.returncode == 0, p.stderr[-2000:]
    binary = p.stdout.strip().splitlines()[-1]
    out["gocheck_binary_built_by"] = "tree"
    rows: dict[str, str] = {}
    notes: list[str] = []
    for name, clone in clones.items():
        # extract.json
        q = subprocess.run([binary, "extract"], input=json.dumps({"root": str(clone)}), capture_output=True, text=True, env=env, check=False)
        rows[f"extract:{name}"] = sha(q.stdout) if q.returncode == 0 else f"rc{q.returncode}:{q.stderr[-200:]}"
        (work / f"extract-{name}.json").write_text(q.stdout)
        # front end: select + model
        code = (
            "import sys,json,hashlib\n"
            "from pathlib import Path\n"
            "from lattice.importer.frontends import select\n"
            "c=Path(sys.argv[1])\n"
            "fe=select(None,c.parent,c.name)\n"
            "m=fe.extract(c.parent,c.name).to_json()\n"
            "print(json.dumps({'select':fe.name,'model':hashlib.sha256(m.encode()).hexdigest()}))\n"
        )
        q = run(["-c", code, str(clone)])
        if q.returncode == 0:
            d = json.loads(q.stdout.strip().splitlines()[-1])
            rows[f"select:{name}"] = d["select"]
            rows[f"model:{name}"] = d["model"]
        else:
            rows[f"select:{name}"] = f"error:{q.stderr[-300:]}"
        # inventory (S with the committed workflows, S4 static)
        inv = work / f"inv-{name}"
        cmd = ["-m", "lattice.cli", "import", "inventory", str(clone), "--out", str(inv)]
        if name == "S":
            cmd += ["--workflows", str(WF)]
        q = run(cmd)
        if q.returncode != 0:
            rows[f"inventory:{name}"] = f"rc{q.returncode}:{q.stderr[-300:]}"
            continue
        ts = tree_sha(inv)
        rows[f"inventory:{name}"] = sha(json.dumps(ts, sort_keys=True))
        out.setdefault("inventory_files", {})[name] = ts  # type: ignore[union-attr]
        # report and appmap
        for kind, sub in (("report", "implementation"), ("appmap", "appmap")):
            cmd = ["-m", "lattice.spec", sub, "--spec", "spec:SPEC", "--trace", "spec.trace.json", "--inventory", str(inv), "--app-root", str(clone), "--json"]
            q = run(cmd, cwd=clone)
            rows[f"{kind}:{name}"] = sha(q.stdout) if q.returncode == 0 else f"rc{q.returncode}:{q.stderr[-300:]}"
            (work / f"{kind}-{name}.json").write_text(q.stdout)
    # D's impact tiers 2+3 on the six dev candidates, project S
    inv = work / "inv-S"
    for cand in sorted(CANDS.iterdir()):
        cmd = ["-m", "lattice.spec", "impact", "--project", str(clones["S"]), "--candidate", str(cand), "--inventory", str(inv), "--tiers", "23", "--baseline", "none", "--json"]
        q = run(cmd)
        text = q.stdout
        rows[f"impact:{cand.name}"] = sha(text) if q.returncode == 0 else f"rc{q.returncode}:{q.stderr[-300:]}"
        (work / f"impact-{cand.name}.json").write_text(text)
    out["rows"] = rows
    out["notes"] = notes
    Path(a.out).write_text(json.dumps(out, indent=2, sort_keys=True))
    print(json.dumps(rows, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
