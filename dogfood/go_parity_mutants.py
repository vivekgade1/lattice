"""Mutants of the Go parity gaps (docs/results/2026-10-17-go-parity-gaps-prereg.md, section 5;
Spike J, node J1). The control (no change) runs first; a mutant is killed when a detector the
control passes fails on it.

* JM1, JM2: copies of ``tests/spec/goapp`` (``getRun`` skips ``caller()`` / answers 403 for a
  missing run), layer R on S1 (PR2's mechanism).
* JM3, JM4: ``GOAPP_BUG=nocheck`` / ``nocancel`` of the existing goapp, layer B on S1 (PB2).
* JM5, JM6: copies of ``tests/spec/goginapp`` (cancel drops ``AND user_id = ?`` / ``token``
  loses ``json:"-"``): gocheck under ``go_spec_policy`` of the imported spec (PA3) and layer R of
  that spec on the mutant binary.
* JM7 to JM16: source changes to a scratch copy of the repository (``lattice``, ``tests``,
  ``bench``, ``dogfood``), killed by the probes the pre-registration names (the probe runner of
  *that copy*, ``--only``) or the named unit tests.

    "$PY" dogfood/go_parity_mutants.py --scratch "$SCRATCH/m" --out "$SCRATCH/m-data" JM1 JM2 ...
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
sys.path.insert(0, str(HERE))

import go_parity_probes as P  # noqa: E402

REPO = P.REPO
TREE_DIRS = ("lattice", "tests", "bench", "dogfood")

# id -> (what, killed by, [(file, old, new)], probe ids, unit-test selectors)
LIB: dict[str, dict[str, Any]] = {
    "JM7": {
        "what": "D2 requires both compare and assign (Audit loses its owner)",
        "killed_by": "PA4; unit assign_only_owner",
        "edits": [
            (
                "lattice/importer/go_rules.py",
                "    uses: dict[tuple[str, str], set[tuple[str, int]]] = {}\n",
                "    uses: dict[tuple[str, str], set[tuple[str, int]]] = {}\n"
                "    hows: dict[tuple[str, str], set[str]] = {}\n",
            ),
            (
                "lattice/importer/go_rules.py",
                '                uses.setdefault((o["model"], o["column"]), set()).add((o["file"], o["line"]))\n',  # noqa: E501
                '                uses.setdefault((o["model"], o["column"]), set()).add((o["file"], o["line"]))\n'  # noqa: E501
                '                hows.setdefault((o["model"], o["column"]), set()).add(o["how"])\n',
            ),
            (
                "lattice/importer/go_rules.py",
                "            and (t.name, c.name) in uses\n",
                '            and hows.get((t.name, c.name)) == {"compare", "assign"}\n',
            ),
        ],
        "probes": ["PA4"],
        "tests": ["tests/importer/test_go_rules.py::test_assign_only_owner"],
    },
    "JM8": {
        "what": "D2 picks the first of two candidate columns instead of unclear",
        "killed_by": "unit two_candidates_unclear",
        "edits": [
            (
                "lattice/importer/go_rules.py",
                "        if not found:\n            tables.append(t)\n            continue\n",
                "        found = found[:1]\n        if not found:\n            tables.append(t)\n            continue\n",  # noqa: E501
            )
        ],
        "probes": [],
        "tests": ["tests/importer/test_go_rules.py::test_two_candidates_unclear"],
    },
    "JM9": {
        "what": "go_spec_policy keeps the derived baseline",
        "killed_by": "PA2 (baseline must be empty); unit",
        "edits": [
            (
                "lattice/gocheck/derive.py",
                "        admin_routes=admin,\n        open_routes=stated.open_routes,\n        admin_role=stated.admin_role,\n        baseline=frozenset(),\n",  # noqa: E501
                "        admin_routes=admin,\n        open_routes=stated.open_routes,\n        admin_role=stated.admin_role,\n        baseline=base.baseline,\n",  # noqa: E501
            )
        ],
        "probes": ["PA2"],
        "tests": [
            "tests/gocheck/test_go_derive.py::test_the_spec_policy_has_an_empty_baseline_and_the_spec_wins_for_declared_tables"
        ],
    },
    "JM10": {
        "what": "the derived owner wins over the spec's declared table",
        "killed_by": "PA2",
        "edits": [
            (
                "lattice/gocheck/derive.py",
                "    owners.update(stated.owners)\n",
                "    owners = {**stated.owners, **base.owners}\n",
            )
        ],
        "probes": ["PA2"],
        "tests": [],
    },
    "JM11": {
        "what": "harness_factory used for Python front ends too",
        "killed_by": "PB-P; unit (FastAPI fixture without studio.toml)",
        "edits": [
            (
                "lattice/importer/rules.py",
                '    go_factory = None\n    if fe.name == "gin":',
                "    from lattice.importer import go_rules as _gr\n\n"
                "    go_factory = _gr.harness_factory(root, package) if app_factory is None else None\n"  # noqa: E501
                '    if fe.name == "gin":',
            )
        ],
        "probes": ["PB-P"],
        "tests": [
            "tests/importer/test_go_rules.py::test_the_python_path_never_asks_for_the_harness_factory"
        ],
    },
    "JM12": {
        "what": "private columns from name heuristics instead of json",
        "killed_by": "unit; PA4",
        "edits": [
            (
                "lattice/gocheck/derive.py",
                '        hidden = [c["name"] for c in t["columns"] if c.get("json") == "-"]\n',
                '        hidden = [c["name"] for c in t["columns"] if c["name"] in ("password", "token")]\n',  # noqa: E501
            )
        ],
        "probes": ["PA4"],
        "tests": [
            "tests/gocheck/test_go_derive.py::test_a_private_column_is_the_json_dash_never_a_name"
        ],
    },
    "JM13": {
        "what": "the json key leaks into the default extract",
        "killed_by": "PB-X",
        "edits": [
            (
                "lattice/gocheck/src/goextract_tables.go",
                "\t\tif g.policy {\n\t\t\tcol.JSON =",
                "\t\tif true {\n\t\t\tcol.JSON =",
            )
        ],
        "probes": ["PB-X"],
        "tests": ["tests/gocheck/test_go_derive.py::test_the_default_extract_has_no_json_key"],
    },
    "JM14": {
        "what": "GO805 ungated by line-proximity pairing",
        "killed_by": "PG805; PB-G",
        "edits": [
            (
                "lattice/gocheck/__init__.py",
                '    "GO805": "error conventions',
                '    "GO805-pairing": "error conventions',
            )
        ],
        "probes": ["PG805", "PB-G"],
        "tests": [],
    },
    "JM15": {
        "what": "the import writes candidate_spec.py into the target dir",
        "killed_by": "PW",
        "edits": [
            (
                "lattice/importer/rules.py",
                '    if r.go_policy is not None:\n        extra["go_policy.json"]',
                '    (Path(r.result.context.root) / package / "candidate_spec.py").write_text(\n'
                '        extra["candidate_spec.py"]\n    )\n'
                '    if r.go_policy is not None:\n        extra["go_policy.json"]',
            )
        ],
        "probes": ["PI1", "PW"],
        "tests": [],
    },
    "JM16": {
        "what": "inferences written as [] for every import",
        "killed_by": "PB-P",
        "edits": [
            (
                "lattice/importer/rules.py",
                '        if self.inferences:  # Go only, and only when something was inferred\n            data["inferences"] = self.inferences\n',  # noqa: E501
                '        data["inferences"] = self.inferences\n',
            )
        ],
        "probes": ["PB-P"],
        "tests": [
            "tests/importer/test_go_rules.py::test_inferences_reach_the_report_only_when_there_are_some"
        ],
    },
}
APP = {
    "JM1": "getRun skips caller() (goapp copy)",
    "JM2": "getRun answers 403 for a missing run (goapp copy)",
    "JM3": "GOAPP_BUG=nocheck (existing variant)",
    "JM4": "GOAPP_BUG=nocancel (existing variant)",
    "JM5": "goginapp copy: cancel drops AND user_id = ?",
    "JM6": 'goginapp copy: token loses json:"-"',
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


def apply(tree: Path, edits: list[tuple[str, str, str]]) -> None:
    for rel, old, new in edits:
        f = tree / rel
        text = f.read_text()
        if text.count(old) != 1:
            raise RuntimeError(f"{rel}: the edit matches {text.count(old)} times: {old[:60]!r}")
        f.write_text(text.replace(old, new, 1))


def run_probes(tree: Path, base: Path, ids: list[str], scratch: Path, out: Path) -> dict[str, Any]:
    """The probe runner of ``tree`` (cwd and PYTHONPATH there) on the probes ``ids``."""
    env = dict(os.environ, PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", GOTOOLCHAIN="auto")
    for k in ("LATTICE_GOAPP_BIN", "LATTICE_GOGINAPP_BIN"):
        env.pop(k, None)
    cmd = [sys.executable, str(tree / "dogfood/go_parity_probes.py"), "--arm", "S", "--scratch",
           str(scratch), "--out", str(out), "--base-dir", str(base), "--only", ",".join(ids),
           "--runs", "2"]  # fmt: skip
    proc = subprocess.run(cmd, cwd=tree, env=env, capture_output=True, text=True, check=False)
    f = out / "probes.json"
    if not f.exists():
        return {"error": (proc.stdout + proc.stderr)[-600:]}
    data = json.loads(f.read_text())
    return {r["id"]: r["pass"] for r in data["results"]}


def run_tests(tree: Path, selectors: list[str]) -> dict[str, Any]:
    if not selectors:
        return {}
    out = {}
    env = dict(os.environ, PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", GOTOOLCHAIN="auto")
    for sel in selectors:
        proc = subprocess.run([sys.executable, "-m", "pytest", sel, "-q", "-x", "-p",
                               "no:cacheprovider"], cwd=tree, env=env, capture_output=True,
                              text=True, check=False)  # fmt: skip
        out[sel] = proc.returncode == 0 and " passed" in proc.stdout
    return out


def detectors(tree: Path, base: Path, spec: dict[str, Any], scratch: Path, out: Path) -> dict:
    d: dict[str, Any] = {}
    if spec["probes"]:
        d["probes"] = run_probes(tree, base, spec["probes"], scratch, out)
    d["tests"] = run_tests(tree, spec["tests"])
    return d


def flat(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.get("probes", {}).items():
        out[f"probe:{k}"] = v
    for k, v in d.get("tests", {}).items():
        out[f"test:{k.split('::')[-1]}"] = v
    return out


# ---- the app and fixture mutants (in the repository itself: they only write to the scratch) ----


class AppMutants:
    def __init__(self, ctx: P.Ctx, imp: Path) -> None:
        self.ctx, self.imp = ctx, imp

    def r_refuted_on(self, binary: Path, route: str) -> list[str]:
        rep = P.s1_check(self.ctx, "R", binary=binary)
        return [i for i, v in rep.rules.items.items() if v.verdict == "refuted" and route in i]

    def run(self, mid: str) -> dict[str, Any]:
        ctx = self.ctx
        if mid in ("JM1", "JM2"):
            binary = P.build_goapp_mutant(ctx, mid)
            hit = self.r_refuted_on(binary, "GET /runs/{rid}")
            return {"detector": "PR2", "refuted_on_route": hit, "killed": bool(hit)}
        if mid in ("JM3", "JM4"):
            bug, route = {"JM3": ("nocheck", "POST /configs/{cid}/runs"),
                          "JM4": ("nocancel", "POST /runs/{rid}/cancel")}[mid]  # fmt: skip
            rep = P.s1_check(ctx, "B", bug=bug)
            eps = sorted({f.endpoint for f in rep.findings})
            return {"detector": "PB2", "endpoints": eps, "killed": route in eps}
        copy = P.mutate_goginapp(ctx, mid)
        ok, err, _ = P.go_build(copy, ctx.scratch / "mut" / mid.lower() / "goginapp-bin")
        row: dict[str, Any] = {"detector": "PA3+R"}
        cand = P.load_candidate(self.imp)
        from lattice.gocheck.derive import derive_go_policy, go_spec_policy

        derived = derive_go_policy(P.GOGINAPP)
        pol = go_spec_policy(cand.SPEC, P.GOGINAPP, derived)
        orig = {(f.code, f.message) for f in P.findings_of(P.GOGINAPP, pol)}
        found = P.findings_of(copy, pol)
        new = [f"{f.code} {f.key}" for f in found if (f.code, f.message) not in orig]
        want = "GO801" if mid == "JM5" else "GO802"
        row["pa3_new_findings"] = new
        row["pa3_killed"] = any(x.startswith(want) for x in new)
        if ok:
            os.environ["LATTICE_GOGINAPP_BIN"] = str(ctx.goginapp_bin)
            base_map = P.verdict_map(P.probe_r_on_spec(ctx, cand.SPEC, "seed"))
            os.environ["LATTICE_GOGINAPP_BIN"] = str(
                ctx.scratch / "mut" / mid.lower() / "goginapp-bin"
            )
            mut_map = P.verdict_map(P.probe_r_on_spec(ctx, cand.SPEC, "seed"))
            flipped = {i: [base_map.get(i), v] for i, v in mut_map.items()
                       if base_map.get(i) != v}  # fmt: skip
            row["r_flipped_items"] = flipped
            row["r_killed"] = any(v[1] == "refuted" for v in flipped.values())
        else:
            row["build_error"] = err
        os.environ["LATTICE_GOGINAPP_BIN"] = str(ctx.goginapp_bin)
        row["killed"] = row["pa3_killed"]
        return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base-ref", default="HEAD")
    ap.add_argument("ids", nargs="*", default=[f"JM{i}" for i in range(1, 17)])
    args = ap.parse_args(argv)
    scratch, out = Path(args.scratch).resolve(), Path(args.out).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    base = scratch / "base-archive"
    P.archive(args.base_ref, base)
    rows: list[dict[str, Any]] = []

    # ---- the control: every detector on an unmutated copy of the tree ----
    t0 = time.perf_counter()
    all_probes = sorted({p for s in LIB.values() for p in s["probes"]})
    all_tests = sorted({t for s in LIB.values() for t in s["tests"]})
    ctl_tree = make_tree(scratch / "tree-control")
    ctl = detectors(ctl_tree, base, {"probes": all_probes, "tests": all_tests}, scratch / "ctl",
                    out / "control")  # fmt: skip
    control = flat(ctl)
    # the unit tests must pass on the control; a probe that fails on it is not a detector of
    # a flip but its failure is the baseline (a mutant is killed when a flag changes)
    bad_tests = [k for k, v in control.items() if k.startswith("test:") and v is not True]
    row = {"id": "control", "detectors": control, "tests_failing_on_control": bad_tests,
           "passes": not bad_tests and "error" not in ctl.get("probes", {}),
           "wall_s": round(time.perf_counter() - t0, 1)}  # fmt: skip
    print("control:", json.dumps({k: v for k, v in row.items() if k != "detectors"}), flush=True)
    rows.append(row)
    (out / "control.json").write_text(json.dumps(ctl, indent=2) + "\n")
    if not row["passes"]:
        (out / "mutants.json").write_text(json.dumps({"rows": rows}, indent=2) + "\n")
        print("the control does not pass: no mutant is judged", file=sys.stderr)
        return 1

    # ---- the app and fixture mutants: they run inside the real tree, against the scratch ----
    app: AppMutants | None = None
    if any(i in APP for i in args.ids):
        ctx = P.Ctx(scratch / "app", out / "app", args.base_ref)
        ctx.base_dir = base
        P.setup(ctx, "S")
        imp = scratch / "app" / "imp"
        res = P.import_s2(ctx, imp)
        if res["rc"] != 0:
            print("the S2 import failed:", res["stderr"], file=sys.stderr)
        app = AppMutants(ctx, imp)
    for mid in args.ids:
        what = APP.get(mid) or LIB[mid]["what"]
        r: dict[str, Any] = {"id": mid, "mutant": what}
        try:
            if mid in APP:
                assert app is not None
                r.update(app.run(mid))
            else:
                spec = LIB[mid]
                tree = make_tree(scratch / f"tree-{mid.lower()}")
                apply(tree, spec["edits"])
                got = flat(detectors(tree, base, spec, scratch / mid.lower(), out / mid.lower()))
                flips = {k: [control.get(k), v] for k, v in got.items() if control.get(k) != v}
                r.update(killed_by=spec["killed_by"], detectors=got, flipped=flips,
                         killed=bool(flips))  # fmt: skip
                shutil.rmtree(tree, ignore_errors=True)
        except Exception as err:  # noqa: BLE001 -- a mutant that cannot run is reported
            r.update(killed=False, error=f"{type(err).__name__}: {err}"[:500])
        print(f"{mid}: killed={r.get('killed')} {r.get('flipped', r.get('detector'))}", flush=True)
        rows.append(r)
    killed = sum(1 for r in rows[1:] if r.get("killed"))
    doc = {"rows": rows, "killed": killed, "total": len(rows) - 1}
    (out / "mutants.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    print(f"killed {killed}/{len(rows) - 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
