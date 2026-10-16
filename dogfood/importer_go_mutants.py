"""Mutants of the Go importer front end (docs/results/2026-10-15-go-frontend-prereg.md, section 4).

Each mutant is a small source change to a scratch copy of ``lattice/gocheck/src`` (or of the
Python shim), built and run against the Streak probes of ``importer_go_probes``; it is killed
when the probe the pre-registration names (or any other) fails. The control (no change)
must pass first. GX9 is killed by the fixture test ``unwired_register``; GX13 (the shim)
needs Docker and the MySQL driver.

    "$DRV" dogfood/importer_go_mutants.py --scratch "$SCRATCH/m" --out "$SCRATCH/m-data" GX1 GX2 ...
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import importer_go_probes as P  # noqa: E402

REPO = P.REPO
SRC = REPO / "lattice/gocheck/src"
SHIM = REPO / "lattice/importer/frontends/go_gin.py"

APPLY_GUARD = (
    "func (g *gx) applyGuard(ci *closureInfo, v *gxVisit, b *Func, e *Env, "
    "ifs *ast.IfStmt, tr *tracked) {"
)
CTOR_OF = "func (g *gx) ctorOf(fn *Func) *ctorSum {"
WRITER_TARGET = "func (g *gx) writerTarget(key string) (xWriteMap, *xWriter, bool) {"

# id -> (what, expected killer, [(file, old, new)])
MUTANTS: dict[str, tuple[str, str, list[tuple[str, str, str]]]] = {
    "GX1": (
        "struct-field router not resolved (group prefix dropped)",
        "PR1",
        [
            (
                "goextract_routes.go",
                "alts := g.router(f.site.e, f.x, f.x.Pos(), depth-1)",
                'alts := []routerRes{{exact: true, engine: true, chain: []string{"engine"}}}',
            )
        ],
    ),
    "GX2": (
        "wrapper not unwrapped (handler = the wrapper's literal)",
        "PR2",
        [
            (
                "goextract_routes.go",
                "if idx := g.wrapperParam(callee); idx >= 0 && idx < len(v.Args) {",
                "if idx := -1; idx >= 0 && idx < len(v.Args) {",
            )
        ],
    ),
    "GX3": (
        "Body guards ignored (no bounds from the refusing if)",
        "PF2",
        [
            (
                "goextract_closure.go",
                APPLY_GUARD,
                APPLY_GUARD + "\n\treturn",
            )
        ],
    ),
    "GX4": (
        "caller id not followed into helper parameters",
        "PO",
        [
            ("goextract_closure.go", "nv[p.name] = true", "_ = nv"),
            ("goextract_closure.go", "ni[p.name] = true", "_ = ni"),
        ],
    ),
    "GX5": (
        "SQL condition strings not parsed",
        "PO",
        [
            (
                "goextract_owner.go",
                'if text != "\\x00" && strings.Contains(text, "?") {',
                "if false {",
            )
        ],
    ),
    "GX6": (
        "middleware ignored for auth (all Public)",
        "PA",
        [
            (
                "goextract_assemble.go",
                "case authenticating != nil:",
                "case authenticating != nil && false:",
            )
        ],
    ),
    "GX7": (
        "constructors not resolved (direct Abort* / JSON only)",
        "PE",
        [
            (
                "goextract_raise.go",
                CTOR_OF,
                CTOR_OF + "\n\treturn nil\n}\n\nfunc (g *gx) ctorOfDead(fn *Func) *ctorSum {",
            )
        ],
    ),
    "GX8": (
        "sentinels not mapped (ErrNotFound -> 404 lost)",
        "PE",
        [
            (
                "goextract_raise.go",
                WRITER_TARGET,
                WRITER_TARGET
                + "\n\treturn xWriteMap{}, nil, false\n}\n\n"
                + WRITER_TARGET.replace("writerTarget", "writerTargetDead"),
            )
        ],
    ),
    "GX9": (
        "wiring off (every registering function counts)",
        "fixture test unwired_register",
        [
            (
                "goextract.go",
                "func (g *gx) isWired(fn *Func) bool { return !g.hasMain || g.wired[fn] }",
                "func (g *gx) isWired(fn *Func) bool { return true }",
            )
        ],
    ),
    "GX10": (
        "conditional registration dropped",
        "PR1",
        [
            (
                "goextract_routes.go",
                "rg.path = p\n\t\t\tout = append(out, rg)",
                'rg.path = p\n\t\t\tif rg.cond != "" {\n\t\t\t\treturn\n\t\t\t}\n'
                "\t\t\tout = append(out, rg)",
            )
        ],
    ),
    "GX11": (
        "composite uniqueIndex read as per-column unique",
        "PT",
        [("goextract_tables.go", 'if name == "" || len(shared[name]) < 2 {', "if true {")],
    ),
    "GX12": (
        "unsorted map iteration and an absolute path in the output",
        "PD",
        [
            (
                "goextract.go",
                "g.readGoMod(root)",
                'g.readGoMod(root)\n\tg.res.Notes = append(g.res.Notes, "root "+root)',
            ),
            ("goextract_assemble.go", "sort.Strings(out.Calls)", ""),
        ],
    ),
    "GX13": (
        "reconcile counts a 404 as observed",
        "PX2",
        [
            (
                "../../importer/frontends/go_gin.py",
                '"observed": status not in (404, 405),',
                '"observed": status not in (405,),',
            )
        ],
    ),
}

GATES = ("PR1", "PR2", "PR3", "PA", "PA2", "PT", "PE", "PF", "PF2", "PO", "PM", "PS", "PU", "PD")


def build(src: Path, out: Path) -> None:
    env = dict(os.environ, **P.GO_ENV)
    subprocess.run(
        ["go", "build", "-trimpath", "-buildvcs=false", "-o", str(out), "."],
        cwd=src,
        env=env,
        check=True,
        capture_output=True,
    )


def mutate(scratch: Path, mid: str) -> tuple[Path, Path]:
    """A scratch copy of the checker sources with the mutant applied: (src dir, shim file)."""
    work = scratch / mid.lower()
    shutil.rmtree(work, ignore_errors=True)
    src = work / "src"
    shutil.copytree(SRC, src)
    shim = work / "go_gin_mutant.py"
    shutil.copy(SHIM, shim)
    for rel, old, new in MUTANTS[mid][2]:
        target = shim if rel.endswith("go_gin.py") else src / rel
        text = target.read_text()
        if text.count(old) != 1:
            raise RuntimeError(f"{mid}: the patch needle occurs {text.count(old)} times in {rel}")
        target.write_text(text.replace(old, new, 1))
    return src, shim


def failing(results: list[dict[str, Any]]) -> list[str]:
    return sorted({r["id"] for r in results if r["pass"] is False and r["id"] in GATES})


def run_pytest_fixture(binary: Path, expr: str) -> tuple[bool, str]:
    """Run the fixture test(s) selected by ``expr`` against ``binary``: (passed, tail)."""
    env = dict(os.environ, GOEXTRACT_BIN=str(binary), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/gocheck/test_goextract.py",
            "-q",
            "-p",
            "no:cacheprovider",
            "-k",
            expr,
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-600:]


def load_shim(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("go_gin_mutant", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--streak-ref", default="a0679e9")
    ap.add_argument("--streak-ref2", default="1d00ede")
    ap.add_argument("ids", nargs="*", default=sorted(MUTANTS, key=lambda m: int(m[2:])))
    args = ap.parse_args(argv)
    scratch, out = Path(args.scratch), Path(args.out)
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    s_root, s4_root, s_root2 = scratch / "s", scratch / "s4", scratch / "s-second-path"
    P.archive(P.STREAK, args.streak_ref, s_root)
    P.archive(P.STREAK, args.streak_ref2, s4_root)
    P.archive(P.STREAK, args.streak_ref, s_root2)

    rows: list[dict[str, Any]] = []
    # the control: the unmutated sources
    control_src = scratch / "control" / "src"
    shutil.rmtree(scratch / "control", ignore_errors=True)
    shutil.copytree(SRC, control_src)
    control_bin = scratch / "control" / "gocheck"
    build(control_src, control_bin)
    cdir = out / "control"
    cdir.mkdir(exist_ok=True)
    results, _, _ = P.evaluate_streak(control_bin, s_root, s4_root, s_root2, cdir, 3)
    control_fail = failing(results)
    ok_fixture, tail = run_pytest_fixture(control_bin, "unwired_register")
    control = {
        "id": "control",
        "failing": control_fail,
        "fixture_unwired_register": ok_fixture,
        "passes": not control_fail and ok_fixture,
    }
    print("control:", json.dumps(control))
    rows.append(control)
    if not control["passes"]:
        (out / "mutants.json").write_text(json.dumps({"rows": rows}, indent=2) + "\n")
        print("the control does not pass: no mutant is judged", file=sys.stderr)
        return 1

    for mid in args.ids:
        what, expected, _ = MUTANTS[mid]
        row: dict[str, Any] = {"id": mid, "mutant": what, "expected": expected}
        try:
            src, shim = mutate(scratch, mid)
            binary = scratch / mid.lower() / "gocheck"
            build(src, binary)
            mdir = out / mid.lower()
            mdir.mkdir(exist_ok=True)
            if mid == "GX9":
                passed, tail = run_pytest_fixture(binary, "unwired_register")
                row.update(
                    caught_by=[] if passed else ["fixture test unwired_register"],
                    killed=not passed,
                    evidence=tail[-300:],
                )
            elif mid == "GX13":
                mod = load_shim(shim)
                fe = mod.GoGinFrontEnd()
                model = fe.extract(s_root, ".")
                px2 = P.probe_px2(fe, s_root, scratch, model)
                row.update(
                    caught_by=[] if px2["pass"] else ["PX2"],
                    killed=not px2["pass"],
                    evidence=px2["observed"],
                )
            else:
                res, _, _ = P.evaluate_streak(binary, s_root, s4_root, s_root2, mdir, 3)
                bad = failing(res)
                row.update(
                    caught_by=bad,
                    killed=bool(bad),
                    expected_hit=expected in bad,
                    evidence={r["id"]: r["observed"] for r in res if r["id"] in bad},
                )
        except Exception as err:  # noqa: BLE001 -- a mutant that cannot build is reported
            row.update(caught_by=[], killed=False, error=f"{type(err).__name__}: {err}"[:500])
        print(f"{mid}: killed={row['killed']} by {row.get('caught_by')} (expected {expected})")
        rows.append(row)
    killed = sum(1 for r in rows[1:] if r.get("killed"))
    doc = {"rows": rows, "killed": killed, "total": len(rows) - 1}
    (out / "mutants.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    print(f"killed {killed}/{len(rows) - 1}")
    return 0 if killed == len(rows) - 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
