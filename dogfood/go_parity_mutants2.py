"""Mutants of the Go parity gaps, round 2 (docs/results/2026-10-18-go-parity-gaps-round2-prereg.md,
section 6; Spike J, node J2). 19 new (JM17-JM35) and the 16 of round 1 re-applied (JM1-JM16): 35.

The control (no change) runs first; a mutant is killed when a detector that passes on the control
stops passing on it. Library mutants are source edits to a scratch copy of the repository
(``lattice``, ``tests``, ``bench``, ``dogfood``) judged by the probe runner of *that copy*
(``go_parity_probes2.py --only``) and by the named unit tests. JM1-JM6 are copies of the Go fixtures
(layer R, layer B, layer A). JM31-JM34 are copies of ``tests/spec/gotickets``; they are NOT RUN while
that fixture has not been delivered (Amendment 1, Q2).

    "$PY" dogfood/go_parity_mutants2.py --base "$SCRATCH/base" --scratch "$SCRATCH/m" \\
        --out docs/results/data/2026-10-18-go-parity-gaps-round2/mutants JM1 ... JM35
"""  # noqa: E501

# ruff: noqa: E501
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
sys.path.insert(0, str(HERE))

import go_parity_mutants as M1  # noqa: E402  (round 1: JM7-JM16 edits, app mutants JM1-JM4)
import go_parity_probes as P1  # noqa: E402
import go_parity_probes2 as P  # noqa: E402

REPO = P.REPO
TREE_DIRS = ("lattice", "tests", "bench", "dogfood")
GOTICKETS_ABSENT = "NOT RUN: tests/spec/gotickets has not been delivered (written by a separate isolated author after the freeze)"

U_DERIVE2 = "tests/gocheck/test_go_derive2.py"
U_RULES2 = "tests/importer/test_go_rules2.py"

# id -> {what, killed_by, edits, probes, tests}; the new library mutants
NEW: dict[str, dict[str, Any]] = {
    "JM17": {
        "what": "E6 off: Gin library middleware marks the route unreadable (round-1 behaviour)",
        "killed_by": "P0-LIBMW; PA3 (JM5)",
        "edits": [
            (
                "lattice/gocheck/src/routes.go",
                "if ginNeutral(mw.e, mw.x) {",
                "if false && ginNeutral(mw.e, mw.x) {",
            )
        ],
        "probes": ["P0-LIBMW", "PA3"],
        "tests": [
            f"{U_DERIVE2}::test_e6_library_middleware_keeps_a_route_readable_and_other_imports_do_not"
        ],
    },
    "JM18": {
        "what": "E6 too broad: any unresolved outside-package middleware is treated as readable",
        "killed_by": "P0-LIBMW (the ext.Guard() route gains GO801)",
        "edits": [
            (
                "lattice/gocheck/src/routes.go",
                "\t\t\t\tif f == nil {\n\t\t\t\t\tr.Unreadable = true\n\t\t\t\t\tcontinue",
                "\t\t\t\tif f == nil {\n\t\t\t\t\tcontinue",
            )
        ],
        "probes": ["P0-LIBMW"],
        "tests": [],
    },
    "JM19": {
        "what": "E1 off: no path retyping",
        "killed_by": "P0-PARSE; PI3 (404 items)",
        "edits": [
            (
                "lattice/importer/go_rules.py",
                'if q is not None and q["parse"] == "Int":',
                'if False and q is not None and q["parse"] == "Int":',
            )
        ],
        "probes": ["P0-PARSE", "PI3"],
        "tests": [],
    },
    "JM20": {
        "what": "E1 ignores condition 2: a parse with a `_` error retypes too",
        "killed_by": "P0-PARSE (/b/:id)",
        "edits": [
            (
                "lattice/gocheck/src/goextract_closure.go",
                "\t\t\tcase *ast.BlockStmt:\n",
                "\t\t\tcase *ast.BlockStmt:\n"
                "\t\t\t\tfor _, st := range s.List {\n"
                "\t\t\t\t\tif a, ok := st.(*ast.AssignStmt); ok && len(a.Rhs) == 1 {\n"
                "\t\t\t\t\t\tif call, ok := a.Rhs[0].(*ast.CallExpr); ok && len(call.Args) > 0 {\n"
                "\t\t\t\t\t\t\tif sel, ok := call.Fun.(*ast.SelectorExpr); ok && parseFns[sel.Sel.Name] {\n"
                '\t\t\t\t\t\t\t\tif name := g.paramName(e, call.Args[0]); name != "" {\n'
                "\t\t\t\t\t\t\t\t\tci.parsed[name] = entryPos{b.File.Rel, g.line(call.Pos())}\n"
                "\t\t\t\t\t\t\t\t}\n\t\t\t\t\t\t\t}\n\t\t\t\t\t\t}\n\t\t\t\t\t}\n\t\t\t\t}\n",
            )
        ],
        "probes": ["P0-PARSE"],
        "tests": [],
    },
    "JM21": {
        "what": "E1's parse key (and E3's bind key) leak into the default extract",
        "killed_by": "PB-X (5 of 8 dirs differ)",
        "edits": [
            (
                "lattice/gocheck/src/goextract_closure.go",
                "\tif g.policy {\n\t\tg.policyFacts(ci)",
                "\tif true {\n\t\tg.policyFacts(ci)",
            )
        ],
        "probes": ["PB-X"],
        "tests": [f"{U_DERIVE2}::test_e1_and_e3_keys_are_absent_from_the_default_extract"],
    },
    "JM22": {
        "what": "E2 off: omitempty ignored",
        "killed_by": "P0-OMIT; PI3 (the invalid item)",
        "edits": [
            (
                "lattice/gocheck/src/goextract_closure.go",
                "if omit && *low != nil && **low > 0 {",
                "if false && omit && *low != nil && **low > 0 {",
            )
        ],
        "probes": ["P0-OMIT", "PI3"],
        "tests": [],
    },
    "JM23": {
        "what": "E2: omitempty,min=3 mapped to ge 0",
        "killed_by": "P0-OMIT (case 2)",
        "edits": [
            (
                "lattice/gocheck/src/goextract_closure.go",
                "if **low == 1 && numeric {",
                "if **low >= 1 && numeric {",
            )
        ],
        "probes": ["P0-OMIT"],
        "tests": [],
    },
    "JM24": {
        "what": "E3 off: validation_status stays 400",
        "killed_by": "P0-BIND; PI3",
        "edits": [
            (
                "lattice/importer/go_rules.py",
                "self.validation_status, bind = infer_bind_status(pol)",
                "_, bind = infer_bind_status(pol)",
            )
        ],
        "probes": ["P0-BIND", "PI3"],
        "tests": [],
    },
    "JM25": {
        "what": "E3 on a mixed module: the first status wins",
        "killed_by": "P0-BIND (goparity2mixed)",
        "edits": [
            (
                "lattice/importer/go_rules.py",
                "    if (\n        len(statuses) == 1\n        and len(statuses) == len({s for s, _ in sites})\n        and statuses[0] in (400, 422)\n    ):",
                "    if statuses and statuses[0] in (400, 422):",
            )
        ],
        "probes": ["P0-BIND"],
        "tests": [],
    },
    "JM26": {
        "what": "E4 off: no choices (the Status 01.. seed)",
        "killed_by": "P0-CHOICES; PI5",
        "edits": [
            (
                "lattice/importer/go_rules.py",
                'if not fs or c.type != "Str" or c.choices:',
                'if True or c.type != "Str" or c.choices:',
            )
        ],
        "probes": ["P0-CHOICES", "PI5"],
        "tests": [],
    },
    "JM27": {
        "what": "E4 uses only the `from` states (S2: queued only)",
        "killed_by": "P0-CHOICES",
        "edits": [
            (
                "lattice/importer/go_rules.py",
                'named = {w["value"] for w in ws if isinstance(w["value"], str)}',
                "named = set()",
            )
        ],
        "probes": ["P0-CHOICES"],
        "tests": [],
    },
    "JM28": {
        "what": "E5 off: no private columns in the profile",
        "killed_by": "P0-PRIV; PR-T2",
        "edits": [
            (
                "lattice/importer/go_rules.py",
                '    for t in pol["tables"]:\n        hidden = sorted(',
                "    for t in []:\n        hidden = sorted(",
            )
        ],
        "probes": ["P0-PRIV", "PR-T2"],
        "tests": [],
    },
    "JM29": {
        "what": "E5 also takes columns with a json name (every string column)",
        "killed_by": "P0-PRIV",
        "edits": [
            (
                "lattice/importer/go_rules.py",
                'if c.get("json") == "-" and not c["primary_key"]',
                'if c.get("json") is not None and not c["primary_key"]',
            )
        ],
        "probes": ["P0-PRIV"],
        "tests": [],
    },
    "JM30": {
        "what": "E7: --rules parsed but not passed",
        "killed_by": "PB1-CLI",
        "edits": [("lattice/spec/__main__.py", "            **extra,\n", "")],
        "probes": ["PB1-CLI"],
        "tests": [],
    },
    "JM35": {
        "what": "an E1-E5 inference entry written for a Python import",
        "killed_by": "PB-P (inferences absent)",
        "edits": [
            (
                "lattice/importer/rules.py",
                'inferences = list(getattr(fe, "inferences", ()))',
                'inferences = list(getattr(fe, "inferences", ())) or [{"rule": "path_parse"}]',
            )
        ],
        "probes": ["PB-P"],
        "tests": [],
    },
}

# round 1's JM7-JM16: the same edits, the round-2 detectors
REAPPLIED: dict[str, dict[str, Any]] = {}
for _id, _probes in {
    "JM7": ["PA4"], "JM8": ["PI2-3"], "JM9": ["PA2"], "JM10": ["PA2"], "JM11": ["PB-P2"],
    "JM12": ["PA4"], "JM13": ["PB-X"], "JM14": ["PG805", "PB-G"], "JM15": ["PI1", "PW"],
    "JM16": ["PB-P"],
}.items():  # fmt: skip
    _spec = dict(M1.LIB[_id])
    _spec["probes"] = [p for p in _probes if not p.endswith("-3")]  # S3 rows are NOT RUN
    _spec["s3_detector"] = [p for p in _probes if p.endswith("-3")]
    REAPPLIED[_id] = _spec

LIB: dict[str, dict[str, Any]] = {**REAPPLIED, **NEW}
APP = {
    "JM1": M1.APP["JM1"], "JM2": M1.APP["JM2"], "JM3": M1.APP["JM3"], "JM4": M1.APP["JM4"],
    "JM5": "goginapp copy: cancel drops AND user_id = ? (detectors: PA3 GO801 under E6; R2 cancel refuted)",
    "JM6": 'goginapp copy: token loses json:"-" (detector: PR-T2; its PA3 side is predicted not to flip, C5b)',
}  # fmt: skip
S3_MUTANTS = {
    "JM31": "gotickets copy: close drops AND reporter_id = ?",
    "JM32": 'gotickets copy: password_hash loses json:"-"',
    "JM33": "gotickets copy: get answers 403 for another caller's ticket",
    "JM34": "gotickets copy: reopen accepts an open ticket (no 409)",
}


def make_tree(dest: Path) -> Path:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for d in TREE_DIRS:
        shutil.copytree(REPO / d, dest / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(REPO / "pyproject.toml", dest / "pyproject.toml")
    (dest / "docs/results").mkdir(parents=True)
    shutil.copyfile(P.PREREG, dest / "docs/results" / P.PREREG.name)
    return dest


def run_probes(tree: Path, base: Path, ids: list[str], scratch: Path, out: Path) -> dict[str, Any]:
    env = dict(os.environ, PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", GOTOOLCHAIN="auto")
    for k in ("LATTICE_GOAPP_BIN", "LATTICE_GOGINAPP_BIN"):
        env.pop(k, None)
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(tree / "dogfood/go_parity_probes2.py"), "--arm", "0,S1,S2,X",
           "--base", str(base), "--scratch", str(scratch), "--out", str(out), "--only",
           ",".join(ids), "--runs", "2"]  # fmt: skip
    proc = subprocess.run(cmd, cwd=tree, env=env, capture_output=True, text=True, check=False)
    f = out / "probes2.json"
    if not f.exists():
        return {"error": (proc.stdout + proc.stderr)[-600:]}
    return {r["id"]: r["pass"] for r in json.loads(f.read_text())["results"]}


def detectors(tree: Path, base: Path, spec: dict[str, Any], scratch: Path, out: Path) -> dict:
    d: dict[str, Any] = {}
    if spec["probes"]:
        d["probes"] = run_probes(tree, base, spec["probes"], scratch, out)
    d["tests"] = M1.run_tests(tree, spec["tests"])
    return d


def flat(d: dict[str, Any]) -> dict[str, Any]:
    out = {f"probe:{k}": v for k, v in d.get("probes", {}).items()}
    out.update({f"test:{k.split('::')[-1]}": v for k, v in d.get("tests", {}).items()})
    return out


class AppMutants:
    """JM1-JM6: copies of the Go fixtures judged in the real tree, against the scratch."""

    def __init__(self, ctx: P1.Ctx, imp: Path) -> None:
        self.ctx, self.imp = ctx, imp
        self.old = M1.AppMutants(ctx, imp)

    def run(self, mid: str) -> dict[str, Any]:
        if mid in ("JM1", "JM2", "JM3", "JM4"):
            return self.old.run(mid)
        ctx = self.ctx
        copy = P1.mutate_goginapp(ctx, mid)
        binary = ctx.scratch / "mut" / mid.lower() / "goginapp-bin"
        ok, err, _ = P1.go_build(copy, binary)
        row: dict[str, Any] = {"detector": "PA3 + R" if mid == "JM5" else "PR-T2"}
        cand = P1.load_candidate(self.imp)
        from lattice.gocheck.derive import derive_go_policy, go_spec_policy

        derived = derive_go_policy(P1.GOGINAPP)
        policy = go_spec_policy(cand.SPEC, P1.GOGINAPP, derived)
        orig = {(f.code, f.message) for f in P1.findings_of(P1.GOGINAPP, policy)}
        new = [f"{f.code} {f.key}" for f in P1.findings_of(copy, policy)
               if (f.code, f.message) not in orig]  # fmt: skip
        row["pa3_new_findings"] = new
        row["pa3_killed"] = (
            "GO801 GO801|POST /runs/:rid/cancel|Run" in new if mid == "JM5" else False
        )
        if ok:
            os.environ["LATTICE_GOGINAPP_BIN"] = str(ctx.goginapp_bin)
            base_map = P1.verdict_map(P1.probe_r_on_spec(ctx, cand.SPEC, "seed"))
            os.environ["LATTICE_GOGINAPP_BIN"] = str(binary)
            mut_map = P1.verdict_map(P1.probe_r_on_spec(ctx, cand.SPEC, "seed"))
            flipped = {i: [base_map.get(i), v] for i, v in mut_map.items() if base_map.get(i) != v}
            row["r_flipped_items"] = flipped
            want = "R2 POST /runs/{rid}/cancel" if mid == "JM5" else "T2 User.token"
            row["r_killed"] = any(i == want and v[1] == "refuted" for i, v in flipped.items())
            row["r_item"] = want
        else:
            row["build_error"] = err
        os.environ["LATTICE_GOGINAPP_BIN"] = str(ctx.goginapp_bin)
        row["killed"] = bool(row["pa3_killed"] or row.get("r_killed"))
        return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", required=True, help="an existing `git archive 193f7f6`")
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("ids", nargs="*", default=[f"JM{i}" for i in range(1, 36)])
    args = ap.parse_args(argv)
    scratch, out, base = (
        Path(args.scratch).resolve(),
        Path(args.out).resolve(),
        Path(args.base).resolve(),
    )
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    ids = [i for i in args.ids if i in LIB or i in APP or i in S3_MUTANTS]

    # ---- the control: every detector on an unmutated copy of the tree ----
    t0 = time.perf_counter()
    all_probes = sorted({p for i in ids if i in LIB for p in LIB[i]["probes"]})
    all_tests = sorted({t for i in ids if i in LIB for t in LIB[i]["tests"]})
    ctl_tree = make_tree(scratch / "tree-control")
    ctl = detectors(ctl_tree, base, {"probes": all_probes, "tests": all_tests}, scratch / "ctl",
                    out / "control")  # fmt: skip
    control = flat(ctl)
    bad_tests = [k for k, v in control.items() if k.startswith("test:") and v is not True]
    row = {"id": "control", "detectors": control, "tests_failing_on_control": bad_tests,
           "probes_not_passing_on_control": sorted(k for k, v in control.items()
                                                   if k.startswith("probe:") and v is not True),
           "passes": not bad_tests and "error" not in ctl.get("probes", {}),
           "wall_s": round(time.perf_counter() - t0, 1)}  # fmt: skip
    print("control:", json.dumps({k: v for k, v in row.items() if k != "detectors"}), flush=True)
    rows.append(row)
    (out / "control.json").write_text(json.dumps(ctl, indent=2) + "\n")
    if not row["passes"]:
        (out / "mutants2.json").write_text(json.dumps({"rows": rows}, indent=2) + "\n")
        print("the control does not pass: no mutant is judged", file=sys.stderr)
        return 1

    app: AppMutants | None = None
    if any(i in APP for i in ids):
        ctx = P1.Ctx(scratch / "app", out / "app", "193f7f6")
        ctx.base_dir = base
        P1.setup(ctx, "S")
        imp = scratch / "app" / "imp"
        res = P1.import_s2(ctx, imp)
        if res["rc"] != 0:
            print("the S2 import failed:", res["stderr"], file=sys.stderr)
        app = AppMutants(ctx, imp)
    for mid in ids:
        what = APP.get(mid) or S3_MUTANTS.get(mid) or LIB[mid]["what"]
        r: dict[str, Any] = {"id": mid, "mutant": what}
        try:
            if mid in S3_MUTANTS:
                r.update(killed=None, not_run=GOTICKETS_ABSENT)
            elif mid in APP:
                assert app is not None
                r.update(app.run(mid))
            else:
                spec = LIB[mid]
                tree = make_tree(scratch / f"tree-{mid.lower()}")
                M1.apply(tree, spec["edits"])
                got = flat(detectors(tree, base, spec, scratch / mid.lower(), out / mid.lower()))
                flips = {k: [control.get(k), v] for k, v in got.items()
                         if control.get(k) is True and v is not True}  # fmt: skip
                r.update(killed_by=spec["killed_by"], detectors=got, flipped=flips,
                         killed=bool(flips))  # fmt: skip
                if spec.get("s3_detector"):
                    r["s3_detector_not_run"] = spec["s3_detector"]
                shutil.rmtree(tree, ignore_errors=True)
        except Exception as err:  # noqa: BLE001 -- a mutant that cannot run is reported
            r.update(killed=False, error=f"{type(err).__name__}: {err}"[:500])
        print(
            f"{mid}: killed={r.get('killed')} {r.get('flipped', r.get('detector', ''))}", flush=True
        )
        rows.append(r)
    killed = sum(1 for r in rows[1:] if r.get("killed"))
    not_run = [r["id"] for r in rows[1:] if r.get("killed") is None]
    doc = {"rows": rows, "killed": killed, "not_run": not_run, "total": len(rows) - 1}
    (out / "mutants2.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    print(f"killed {killed}/{len(rows) - 1} (not run: {not_run})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
