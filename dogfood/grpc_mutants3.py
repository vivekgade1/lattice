"""Mutants of gRPC round 3 (docs/results/2026-10-18-grpc-round3-prereg.md, section 8): eighteen new
deliberate faults (GW1-GW11 reachability, GU1-GU3 auth, GC1-GC2 reconcile, GS1-GS2 scorer) and the
seventeen of round 2 (GR1-GR17, ``grpc_mutants2.py``) re-applied. The run works on a scratch copy
of the worktree -- never on the worktree itself -- and the control (the copy unmodified) runs every
check first.

    uv run python dogfood/grpc_mutants3.py --scratch "$SCRATCH/m" --deskd <deskd> --base "$SCRATCH/base" \\
        --base-r1 "$SCRATCH/base-r1" $(seq -f GW%g 1 11) GU1 GU2 GU3 GC1 GC2 GS1 GS2 $(seq -f GR%g 1 17)

A *check* is a pytest selection of the copy, one probe of ``grpc_probes3.py --check <name>`` or one
probe of round 2's ``grpc_probes2.py --check <name>`` (for the re-applied GR mutants), run against
the copy through ``PYTHONPATH``. A mutant is KILLED when any of its checks gives a different
verdict than the control's, INVALID when its patch did not apply; every listed check is run, so the
table says which of them (the pre-registration's named killer, or the unit tests also named) killed
it. A mutant text that no longer applies as the pre-registration worded it is re-expressed over the
code that exists (``note``), and the table says so.
"""

# ruff: noqa: E501 -- mutant texts are data lines
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import grpc_mutants2 as m2  # noqa: E402

GO = "lattice/gocheck/src/goextract_grpc.go"
LOAD = "lattice/gocheck/src/load.go"
GRPC = "lattice/spec/grpc.py"
SC3 = "dogfood/grpc_scorer3.py"

WIRE = ["probe3", "p3-wire"]
AUTHU = ["probe3", "p3-authu"]
SCORE = ["probe3", "p3-score"]
DESK = ["probe3", "pr3-desk"]
T_WIRE = ["pytest", "tests/grpc/test_wiring3.py"]
T_AUTH = ["pytest", "tests/grpc/test_auth3.py"]
T_REC = ["pytest", "tests/grpc/test_reconcile3.py"]

# id -> what, edits [(file, old, new)], checks, the pre-registration's named killer, note
NEW: dict[str, dict[str, Any]] = {
    "GW1": {
        "what": "R1 off: package-level initializers are not walked",
        "edits": [
            (
                GO,
                "func (w *grpcWalk) useVar(pkg *Pkg, name string) {\n",
                "func (w *grpcWalk) useVar(pkg *Pkg, name string) {\n\treturn\n",
            )
        ],
        "checks": [WIRE, T_WIRE],
        "killer": "P3-WIRE, P3-UNIT",
    },
    "GW2": {
        "what": "R2 off: a function named as a value in a walked initializer is not followed",
        "edits": [
            (
                GO,
                "		if f := w.g.funcRef(e, x, 0); f != nil && f.Lit == nil {\n			w.add(f)\n		}\n",
                "",
            )
        ],
        "checks": [WIRE, T_WIRE],
        "killer": "P3-WIRE",
        "note": "D1.2 has no code of its own (the HTTP walker's value rule, applied to a walked initializer, realizes it): the mutant removes that rule",
    },
    "GW3": {
        "what": "R2 takes only the first element of a ranged literal",
        "edits": [
            (
                GO,
                "		body := &ast.BlockStmt{List: []ast.Stmt{&ast.ExprStmt{X: vi.value}}}\n",
                "		val := vi.value\n		if cl, ok := val.(*ast.CompositeLit); ok && len(cl.Elts) > 0 {\n			val = cl.Elts[0]\n		}\n		body := &ast.BlockStmt{List: []ast.Stmt{&ast.ExprStmt{X: val}}}\n",
            )
        ],
        "checks": [WIRE, T_WIRE],
        "killer": "P3-WIRE (the second element's service)",
        "note": "re-expressed over the walked initializer, where the elements of a ranged literal are read",
    },
    "GW4": {
        "what": "R3 off: a registration on a grpc.ServiceRegistrar parameter is ignored",
        "edits": [
            (
                GO,
                "			r.impl, r.fn, r.e, r.call = call.Args[1], fn, e, call\n",
                '			if id, ok := call.Args[0].(*ast.Ident); ok {\n				for _, p := range fn.Params() {\n					if p.name == id.Name && g.src(p.typ) == "grpc.ServiceRegistrar" {\n						return\n					}\n				}\n			}\n			r.impl, r.fn, r.e, r.call = call.Args[1], fn, e, call\n',
            )
        ],
        "checks": [WIRE, T_WIRE],
        "killer": "P3-WIRE",
        "note": "the extractor matches Register calls by name, so R3 is not a filter; the mutant adds the filter the rule rules out",
    },
    "GW5": {
        "what": "R4: methods promoted from an embedded struct are missing from the impl's method set",
        "edits": [(GO, "h := g.m.MethodOf(ti, r.Name, 3)", "h := g.m.MethodOf(ti, r.Name, 0)")],
        "checks": [WIRE, T_WIRE],
        "killer": "P3-WIRE (unimplemented instead of implemented)",
        "note": "re-expressed: value-receiver methods were already in MethodOf's set, so the mutant cuts the promotion depth",
    },
    "GW6": {
        "what": "R4: a constructor returning an interface is not followed",
        "edits": [
            (
                GO,
                "	if c, ok := unparen(x).(*ast.CallExpr); ok {\n		if callee := g.m.Callee(e, c); callee != nil {\n			set :=",
                "	if c, ok := unparen(x).(*ast.CallExpr); ok && false {\n		if callee := g.m.Callee(e, c); callee != nil {\n			set :=",
            )
        ],
        "checks": [WIRE, T_WIRE],
        "killer": "P3-WIRE",
    },
    "GW7": {
        "what": "R5 off: a descriptor held in a variable is not followed",
        "edits": [(GO, "	f, e, x = g.followDesc(f, e, x)\n", "")],
        "checks": [WIRE, T_WIRE],
        "killer": "P3-WIRE",
    },
    "GW8": {
        "what": "R6 off: a referenced registration the walk cannot reach is reported not_registered",
        "edits": [
            (GO, 'if w.refs[strings.TrimPrefix(topFunc(fn).Short, "var.")] > 0 {', "if false {")
        ],
        "checks": [WIRE, T_WIRE],
        "killer": "P3-WIRE (opaque case), P3-UNIT",
    },
    "GW9": {
        "what": "R6 too broad: an unreferenced Register function counts as referenced",
        "edits": [
            (
                GO,
                'if w.refs[strings.TrimPrefix(topFunc(fn).Short, "var.")] > 0 {',
                'if w.refs[strings.TrimPrefix(topFunc(fn).Short, "var.")] >= 0 {',
            )
        ],
        "checks": [WIRE, T_WIRE],
        "killer": "P3-WIRE (dead case)",
    },
    "GW10": {
        "what": "a _test.go reference counts as wiring",
        "edits": [
            (
                LOAD,
                '		if strings.HasSuffix(p, ".go") && !strings.HasSuffix(p, "_test.go") {\n',
                '		if strings.HasSuffix(p, ".go") {\n',
            )
        ],
        "checks": [WIRE, T_WIRE],
        "killer": "P3-WIRE (test-only case)",
        "note": "load.go never reads _test.go files, so the mutant (made on the scratch copy only) lets the loader read them",
    },
    "GW11": {
        "what": "the gRPC walk is merged into g.wired (HTTP and gateway reachability change)",
        "edits": [
            (
                GO,
                "		for ; len(w.queue) > 0; w.queue = w.queue[1:] {\n			w.scan(w.queue[0])\n		}\n",
                "		for ; len(w.queue) > 0; w.queue = w.queue[1:] {\n			w.scan(w.queue[0])\n		}\n		for fn := range w.wired {\n			g.wired[fn] = true\n		}\n",
            )
        ],
        "checks": [["probe3", "p3-bc-ext"], WIRE, T_WIRE],
        "killer": "P3-BC",
        "note": "P3-BC's inputs have no gRPC-only function whose HTTP reading would move; the gateway call in the wiring fixture is what shows it",
    },
    "GU1": {
        "what": "D2 off: an interceptor that was not read decides nothing (round 2: Public)",
        "edits": [
            (
                GO,
                '		if why := g.untracedReturn(fn, es, run); why != "" {',
                '		if why := g.untracedReturn(fn, es, run); why != "" && false {',
            )
        ],
        "checks": [AUTHU, T_AUTH],
        "killer": "P3-AUTHU",
    },
    "GU2": {
        "what": "every other interceptor is auth_unknown (the benign check is skipped)",
        "edits": [
            (
                GO,
                '		if why := g.untracedReturn(fn, es, run); why != "" {',
                '		if why := "the benign check is skipped"; why != "" {',
            )
        ],
        "checks": [AUTHU, T_AUTH],
        "killer": "P3-AUTHU (logger, recover cases)",
    },
    "GU3": {
        "what": "an untraced `return nil, err` counts as benign",
        "edits": [
            (
                GO,
                "		if len(r.unread) > 0 {\n			return fmt.Sprintf(",
                "		if len(r.unread) > 0 && false {\n			return fmt.Sprintf(",
            )
        ],
        "checks": [AUTHU, T_AUTH],
        "killer": "P3-AUTHU",
    },
    "GC1": {
        "what": "reconcile_with iterates model.routes only (round 2)",
        "edits": [
            (
                GRPC,
                "for svc, meth, streaming in contract_rpcs(rpc):",
                "for svc, meth, streaming in ():",
            )
        ],
        "checks": [DESK, T_REC],
        "killer": "PR3-DESK, P3-UNIT",
    },
    "GC2": {
        "what": "non-route rpcs are called without a fresh login (RC1 off for them)",
        "edits": [
            (
                GRPC,
                "                        by_key[key] = (svc, meth, False)\n",
                "                        by_key[key] = (svc, meth, True)\n",
            )
        ],
        "checks": [T_REC, DESK],
        "killer": "P3-UNIT",
    },
    "GS1": {
        "what": "scorer S5: supplementary calls are ignored",
        "edits": [(SC3, "    if supplementary_calls != 0:\n", "    if False:\n")],
        "checks": [SCORE],
        "killer": "P3-SCORE",
    },
    "GS2": {
        "what": "scorer S7: an all-unclear AUTH passes",
        "edits": [
            (
                SC3,
                '    return _verdict(row, "unclear_above_cap" if share is not None and share > unclear_cap else "")\n',
                '    return _verdict(row, "")\n',
            )
        ],
        "checks": [SCORE],
        "killer": "P3-SCORE",
    },
}


def checks_of(mid: str) -> list[list[str]]:
    if mid in NEW:
        return [list(c) for c in NEW[mid]["checks"]]
    return [list(c) for c in m2.MUTANTS[mid]["checks"]]


def run_check(tree: Path, args: argparse.Namespace, check: list[str], label: str) -> dict[str, Any]:
    env = {
        **os.environ,
        "PYTHONPATH": str(tree),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LATTICE_GOCHECK_CACHE": str(args.scratch / f"gocheck-{label}"),
        "LATTICE_GRPCBRIDGE_CACHE": str(args.scratch / "bridge-shared"),
        "LATTICE_GRPC_NOTES_BIN": str(args.scratch / "notesapp"),
        "LATTICE_GRPC_SESSIONS_BIN": str(args.scratch / "sessionsapp"),
        "LATTICE_HELDOUT_BUILD": str(args.scratch / "hbuild"),
    }
    t0 = time.perf_counter()
    if check[0] == "pytest":
        cmd = [sys.executable, "-m", "pytest", *check[1:], "-q", "-p", "no:cacheprovider"]
    elif check[0] == "probe3":
        cmd = [
            sys.executable,
            str(tree / "dogfood" / "grpc_probes3.py"),
            "--check",
            check[1],
            "--scratch",
            str(args.scratch / f"s-{label}"),
            "--base",
            str(args.base),
            "--deskd",
            str(args.deskd),
        ]
    else:  # round 2's probe, for a re-applied GR mutant (its base is the round-1 tree)
        cmd = [
            sys.executable,
            str(tree / "dogfood" / "grpc_probes2.py"),
            "--check",
            check[1],
            "--scratch",
            str(args.scratch / f"s-{label}"),
            "--base",
            str(args.base_r1),
            "--deskd",
            str(args.deskd),
        ]
    p = subprocess.run(
        cmd, cwd=tree, env=env, capture_output=True, text=True, check=False, timeout=3600
    )
    text = p.stdout + p.stderr
    return {
        "check": " ".join(check[1:]),
        "kind": check[0],
        "returncode": p.returncode,
        "seconds": round(time.perf_counter() - t0, 1),
        "failed": sorted(set(re.findall(r"^FAILED (\S+)", text, re.M)))[:4],
        "summary": (text.strip().splitlines() or [""])[-1][:200],
        "tail": text[-600:],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True, help="git archive of the merge commit")
    ap.add_argument(
        "--base-r1", type=Path, required=True, help="git archive cea85fc (the GR mutants' probes)"
    )
    ap.add_argument("--deskd", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=REPO / "docs/results/data/2026-10-18-grpc-round3")
    args = ap.parse_args(argv)
    args.scratch.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO))
    os.environ.setdefault("LATTICE_GRPCBRIDGE_CACHE", str(args.scratch / "bridge-shared"))
    from tests.grpc.fixtures.notes import harness as notes
    from tests.grpc.fixtures.sessions import harness as sessions

    for name, h in (("notesapp", notes), ("sessionsapp", sessions)):
        shutil.copyfile(h.binary(), args.scratch / name)
        (args.scratch / name).chmod(0o755)
    tree = args.scratch / "tree"
    m2.copy_tree(tree)
    distinct: dict[str, list[str]] = {}
    for mid in args.ids:
        for c in checks_of(mid):
            distinct.setdefault(" ".join(c), c)
    control: dict[str, dict[str, Any]] = {}
    for key, c in distinct.items():
        control[key] = run_check(tree, args, c, "control")
        print(
            f"control {key}: rc={control[key]['returncode']} {control[key]['summary'][:90]} ({control[key]['seconds']}s)",
            flush=True,
        )
    rows: list[dict[str, Any]] = []
    for mid in args.ids:
        m = NEW.get(mid) or m2.MUTANTS[mid]
        m2.copy_tree(tree)
        why = m2.apply(tree, m["edits"])
        row: dict[str, Any] = {
            "id": mid,
            "mutant": m["what"],
            "killer": m["killer"],
            "checks": [],
            "note": m.get("note", ""),
        }
        if why:
            row |= {"result": "INVALID", "why": why}
        else:
            killers = []
            for c in checks_of(mid):
                r = run_check(tree, args, c, mid.lower())
                ctrl = control[" ".join(c)]
                differs = r["returncode"] != ctrl["returncode"]
                r["differs_from_control"] = differs
                row["checks"].append(
                    {
                        k: r[k]
                        for k in (
                            "check",
                            "kind",
                            "returncode",
                            "seconds",
                            "failed",
                            "summary",
                            "differs_from_control",
                        )
                    }
                )
                if differs:
                    killers.append(f"{r['kind']}:{r['check']}")
            row["result"] = "KILLED" if killers else "SURVIVED"
            row["killed_by"] = killers
        rows.append(row)
        print(
            f"{row['result']:9} {mid:5} {m['what'][:60]:60} {row.get('killed_by', row.get('why', ''))}",
            flush=True,
        )
    killed = sum(r["result"] == "KILLED" for r in rows)
    print(
        f"{killed}/{len(rows)} killed (control checks: "
        + ", ".join(f"{k}={v['returncode']}" for k, v in control.items())
        + ")"
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "mutants3.json").write_text(
        json.dumps(
            {
                "schema": "lattice.grpc-mutants3/1",
                "control": {
                    k: {x: v[x] for x in ("returncode", "summary", "seconds")}
                    for k, v in control.items()
                },
                "killed": killed,
                "of": len(rows),
                "mutants": rows,
            },
            indent=1,
        )
        + "\n"
    )
    return 0 if killed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
