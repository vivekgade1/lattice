"""Mutants of the workflow layer (docs/results/2026-10-15-workflow-layer-prereg.md, section 4;
round 2: docs/results/2026-10-16-workflow-layer-round2-prereg.md, section 4, WR1-WR11).

Each mutant is a small source change to a scratch copy of the repository (Go sources of
``lattice/gocheck/src`` or the layer's Python modules), run against the Streak probes of
``workflow_probes`` (and, for the ones a probe cannot see, the layer's unit tests); it is
killed when a gated probe (the pre-registration names one) or a test fails. The control (no
change) must pass first.

    "$DRV" dogfood/workflow_mutants.py --scratch "$SCRATCH/m" --out "$SCRATCH/m-data" WF1 WF2 ...
    "$DRV" dogfood/workflow_mutants.py --scratch "$SCRATCH/m" --out "$SCRATCH/m-data" WR1 ... WR11

A round-1 mutant (WF) runs the Streak stage of ``workflow_probes``; a round-2 mutant (WR) runs
the ``--round2`` stage with only the probes that can see it (``R2_ONLY``), on the pre-registered
apps (``R2_APPS``, from ``$LATTICE_R2_DOCS``, default ``~/Documents``).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
GO_ENV = {"GOFLAGS": "-mod=mod", "GOPROXY": "off", "GOTOOLCHAIN": "local", "GOWORK": "off"}

GO = "lattice/gocheck/src/goextract_workflow.go"
GOX = "lattice/gocheck/src/goextract.go"
RUN = "lattice/importer/workflow_run.py"
WFS = "lattice/importer/workflows.py"
CI = "lattice/importer/check_invocation.py"

NOTE_USE = (
    "func (w *wfCtx) noteUse(v *wfVisit, b *Func, p token.Pos, origin string, "
    "t *xTable, col, how string) {\n"
)
ADD_LITERAL = (
    "func (w *wfCtx) addLiteral(v *wfVisit, b *Func, p token.Pos, origin, value string) {\n"
)
MODE = '        mode = "observed" if harness is not None and not self.static else "static"'

# id -> (what, the killer the pre-registration names, [(file, old, new)], unit tests that also run)
MUTANTS: dict[str, tuple[str, str, list[tuple[str, str, str]], str]] = {
    "WF1": (
        "param_uses ignored (no edges)",
        "PE",
        [(GO, NOTE_USE, NOTE_USE + "\treturn\n")],
        "",
    ),
    "WF2": (
        "path params filled with 1 instead of the threaded ids",
        "PF",
        [
            (
                RUN,
                "        rec.path = self.render(route, path_values)\n",
                "        rec.path = self.render(route, {p: 1 for p in route.path_params})\n",
            )
        ],
        "",
    ),
    "WF3": (
        "verified from the live run alone (no replay)",
        "PR",
        [
            (
                RUN,
                '        if verdict is True:\n            return "verified", ""',
                '        if True:\n            return "verified", ""',
            )
        ],
        "",
    ),
    "WF4": (
        "expected status = the first static 2xx, not the observed one",
        "PF",
        [
            (
                RUN,
                "                expect_status=int(rec.status),",
                "                expect_status=int(min([s for s in rec.route.status_codes "
                "if 200 <= s < 300] or [rec.status])),",
            ),
            (
                RUN,
                "            st = rec.status if executed else None\n",
                "            st = rec.status if executed else None\n"
                "            if executed:\n"
                "                st = min([s for s in rec.route.status_codes if 200 <= s < 300] "
                "or [rec.status])\n",
            ),
        ],
        "",
    ),
    "WF5": (
        "machine from-rule off (from = *)",
        "PM",
        [
            (
                GO,
                "\t\t\t\t\tif len(f.From) == 0 {",
                "\t\t\t\t\tf.From, f.FromAt = nil, nil\n\t\t\t\t\tif len(f.From) == 0 {",
            )
        ],
        "",
    ),
    "WF6": (
        "every written value counted as initial",
        "PM",
        [
            (
                WFS,
                '            if fact["kind"] == "create":\n                initial.add(w["value"])',
                '            initial.add(w["value"])\n            if fact["kind"] == "create":\n'
                '                initial.add(w["value"])',
            )
        ],
        "",
    ),
    "WF7": (
        "an absolute path and a timestamp in the output",
        "PD",
        [
            (
                RUN,
                MODE,
                '        doc["app"]["root"] = str(self.root)\n'
                '        doc["app"]["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")\n' + MODE,
            )
        ],
        "",
    ),
    "WF8": (
        "repair loop off",
        "PF",
        [(RUN, "if ans.status == 422 and idx:", "if False and idx:")],
        "",
    ),
    "WF9": (
        "literals not read",
        "PV",
        [(GO, ADD_LITERAL, ADD_LITERAL + "\treturn\n")],
        "",
    ),
    "WF10": (
        "a status accepted although it is not in the code evidence",
        "PG",
        [
            (
                RUN,
                "        evidence = code_statuses(route)\n",
                "        evidence = list(range(100, 600))\n",
            )
        ],
        "tests/importer/test_workflows.py -k not_in_the_code_evidence",
    ),
    "WF11": (
        "workflow keys emitted in the default extract",
        "PB-extract",
        [(GOX, "if in.Workflow {", "if true {")],
        "tests/gocheck/test_goextract_workflow.py -k default_output_has_no_workflow_key",
    ),
    "WF12": (
        "snapshots written under the target root, never removed",
        "PW",
        [
            (
                RUN,
                'self.scratch = self.out_dir / ".scratch"',
                'self.scratch = self.root / ".wf-scratch"',
            ),
            (RUN, "shutil.rmtree(self.scratch, ignore_errors=True)", "pass"),
        ],
        "",
    ),
    "WF13": (
        "the container is not removed on exit (an error included), and dbserver's own "
        "atexit guard is off",
        "PT",
        [
            (RUN, "            self.server.stop()\n", "            pass\n"),
            (
                RUN,
                "        self.dbs = dbserver.ServerDatabases(self.server, build_db)",
                "        import atexit\n\n        atexit.unregister(dbserver.remove_all)\n"
                "        self.dbs = dbserver.ServerDatabases(self.server, build_db)",
            ),
        ],
        "",
    ),
    # ---- round 2 (the pre-registration's section 4)
    "WR1": (
        "the check invocation is ignored (round-1 discovery)",
        "PH",
        [(CI, "        lambda: _from_invocation(root, cfg),\n", "        lambda: None,\n")],
        "",
    ),
    "WR2": (
        "an unresolvable source falls through to harness.py, and the convention also accepts "
        "build_seed_db",
        "PH",
        [
            (
                CI,
                "        if got is not None:\n            return got\n",
                "        if got is not None and got.found:\n            return got\n",
            ),
            (
                CI,
                '    missing = [a for a in ("make_app", "build_db") if got[a] is None]',
                '    if got["build_db"] is None:\n        try:\n'
                '            got["build_db"] = _import(root, "harness:build_seed_db")\n'
                "        except Exception:\n            pass\n"
                '    missing = [a for a in ("make_app", "build_db") if got[a] is None]',
            ),
        ],
        "",
    ),
    "WR3": (
        "--login from the invocation dropped (login=None)",
        "PH",
        [
            (
                CI,
                "        base.login = _load(root, login) if login else None",
                "        base.login = None",
            )
        ],
        "",
    ),
    "WR4": (
        "services read by name when --services is absent",
        "PH",
        [
            (
                CI,
                "        base.services = _services(root, services) if services else {}",
                "        try:\n"
                '            base.services = _services(root, services or "harness:SERVICES")\n'
                "        except _Unresolvable:\n            base.services = {}",
            )
        ],
        "",
    ),
    "WR5": (
        "the invocation's database ignored (the extract's dialect used)",
        "PH",
        [
            (
                CI,
                '        base.database = inv.flags.get("database") or "sqlite"',
                "        base.database = None",
            )
        ],
        "",
    ),
    "WR6": (
        "$VAR values taken literally",
        "PH",
        [(CI, '    if "$" in value or "`" in value:', "    if False:")],
        "",
    ),
    "WR7": (
        'the `set -- ... "$@"` splice not followed',
        "PH",
        [(CI, "                args += set_args", "                args += []")],
        "",
    ),
    "WR8": (
        "default list rule back to first",
        "PL",
        [(RUN, 'DEFAULT_LIST_RULE = "specific"', 'DEFAULT_LIST_RULE = "first"')],
        "",
    ),
    "WR9": (
        "--list-rule first behaves as specific",
        "PL1",
        [(RUN, '        if self.list_rule == "specific":', "        if True:")],
        "",
    ),
    "WR10": (
        "unbound path params filled with 1",
        "PU",
        [
            (RUN, "str(values[name])", "str(values.get(name, 1))"),
            (RUN, "            if name not in path_values:", "            if False:"),
        ],
        "tests/importer/test_workflows.py -k unbound_path_param or render_has_no_default",
    ),
    "WR11": (
        "a static run with harness_reason null",
        "PH",
        [(RUN, '"harness_reason": None if observed else reason,', '"harness_reason": None,')],
        "",
    ),
}
# the round-2 probes that can see each mutant (--only of the --round2 stage)
R2_ONLY = {
    "WR1": "PH", "WR2": "PH", "WR3": "PH", "WR4": "PH", "WR5": "PH", "WR6": "PH", "WR7": "PH",
    "WR8": "PL", "WR9": "PL1", "WR10": "PU", "WR11": "PH",
}  # fmt: skip
R2_CONTROL = "PH,PL,PL1,PU,PJs"
GATES_R2 = ("PH", "PL", "PL1", "PU", "PJs", "PJ", "PD", "PR", "PW", "PT")

GATES = (
    "PE",
    "PS",
    "PK",
    "PF",
    "PG",
    "PM",
    "PD",
    "PW",
    "PT",
    "PR",
    "PB-extract",
    "PB-gocheck",
    "PX",
)
ONLY = "PE,PS,PK,PF,PG,PM,PV,PD,PW,PT,PR,PB-extract,PB-gocheck,PX"


def copy_tree(dest: Path) -> None:
    """The parts of the repository the probes and tests read, copied (a worktree's ``.git``
    file points at the real object store, which ``git archive`` reads)."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for rel in ("lattice", "dogfood", "tests", "pyproject.toml"):
        src = REPO / rel
        if src.is_dir():
            shutil.copytree(src, dest / rel, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy(src, dest / rel)
    for rel in (
        "docs/results/data/2026-10-15-go-frontend",
        "docs/results/data/2026-10-15-workflow-layer",
    ):
        shutil.copytree(REPO / rel, dest / rel)
    git = REPO / ".git"
    if git.is_file():
        shutil.copy(git, dest / ".git")


def patch(tree: Path, mid: str) -> None:
    for rel, old, new in MUTANTS[mid][2]:
        target = tree / rel
        text = target.read_text()
        if text.count(old) != 1:
            raise RuntimeError(f"{mid}: the needle occurs {text.count(old)} times in {rel}")
        target.write_text(text.replace(old, new, 1))


def leftovers() -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=lattice-wf-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    for name in out:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    return out


def run_probes(tree: Path, scratch: Path, out: Path, args: argparse.Namespace) -> dict[str, Any]:
    env = dict(os.environ, **GO_ENV)
    env.update(
        WORKFLOW_PROBES_REPO=str(tree),
        PYTHONPATH=str(tree),
        PYTHONDONTWRITEBYTECODE="1",
        LATTICE_GOCHECK_CACHE=str(scratch / "gocheck-cache"),
        TMPDIR=str(scratch / "tmp"),
    )
    (scratch / "tmp").mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(tree / "dogfood/workflow_probes.py"),
        "--only", ONLY,
        "--static-runs", str(args.static_runs),
        "--runs", str(args.runs),
        "--users", "ann,ben",
        "--scratch", str(scratch / "p"),
        "--out", str(out),
    ]  # fmt: skip
    proc = subprocess.run(cmd, cwd=tree, env=env, capture_output=True, text=True, check=False)
    pj = out / "probes.json"
    rows = json.loads(pj.read_text())["results"] if pj.is_file() else []
    return {"rc": proc.returncode, "rows": rows, "stderr": proc.stderr[-500:]}


def r2_apps() -> list[str]:
    d = os.environ.get("LATTICE_R2_DOCS", str(Path.home() / "Documents"))
    apps = {
        "go": ("job-scheduler-go", "00dd1de"),
        "mso": ("job-scheduler-go-min-spec-opus", "7d1a730"),
        "mss": ("job-scheduler-go-min-spec-sonnet", "f12365b"),
        "base": ("job-scheduler-go-baseline", "b8d9fe0"),
        "mbo": ("job-scheduler-go-min-base-opus", "00cbf55"),
        "mbs": ("job-scheduler-go-min-base-sonnet", "7ac55d5"),
    }
    out: list[str] = []
    for name, (dirname, ref) in apps.items():
        out += ["--app", f"{name}={d}/{dirname}@{ref}"]
    for name in ("go", "mso", "mss"):
        out += ["--build", f"{name}=go build -o bin/server ./cmd/server"]
    out += ["--spec", f"{d}/job-scheduler@ef17cf8:spec.tickets:t5"]
    return out


def run_probes_r2(
    tree: Path, scratch: Path, out: Path, args: argparse.Namespace, only: str
) -> dict[str, Any]:
    env = dict(os.environ, **GO_ENV)
    env.update(
        WORKFLOW_PROBES_REPO=str(tree),
        PYTHONPATH=str(tree),
        PYTHONDONTWRITEBYTECODE="1",
        LATTICE_GOCHECK_CACHE=str(scratch / "gocheck-cache"),
        TMPDIR=str(scratch / "tmp"),
    )
    (scratch / "tmp").mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(tree / "dogfood/workflow_probes.py"),
        "--round2", "--only", only,
        "--streak-ref", "a0679e9", "--users", "ann,ben",
        "--static-runs", "1", "--runs", "1", "--j-users", "root,ann", "--j-runs", "1",
        "--scratch", str(scratch / "p"), "--out", str(out),
        *r2_apps(),
    ]  # fmt: skip
    proc = subprocess.run(cmd, cwd=tree, env=env, capture_output=True, text=True, check=False)
    pj = out / "probes.json"
    rows = json.loads(pj.read_text())["results"] if pj.is_file() else []
    return {"rc": proc.returncode, "rows": rows, "stderr": proc.stderr[-500:]}


def run_unit(tree: Path, scratch: Path, expr: str) -> tuple[bool, str]:
    if not expr:
        return True, ""
    env = dict(os.environ, **GO_ENV)
    env.update(
        PYTHONPATH=str(tree),
        PYTHONDONTWRITEBYTECODE="1",
        LATTICE_GOCHECK_CACHE=str(scratch / "gocheck-cache"),
    )
    path, _, k = expr.partition(" -k ")
    cmd = [sys.executable, "-m", "pytest", path, "-q", "-p", "no:cacheprovider"]
    if k:
        cmd += ["-k", k]
    proc = subprocess.run(cmd, cwd=tree, env=env, capture_output=True, text=True, check=False)
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-400:]


def failing(rows: list[dict[str, Any]], gates: Sequence[str] = GATES) -> list[str]:
    return sorted({r["id"] for r in rows if r["pass"] is False and r["id"] in gates})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--static-runs", type=int, default=3)
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument(
        "ids",
        nargs="*",
        default=sorted(MUTANTS, key=lambda m: (m[:2] == "WR", int(m[2:]))),
    )
    args = ap.parse_args(argv)
    scratch, out = Path(args.scratch).resolve(), Path(args.out).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    pre = leftovers()
    # the controls: the unmutated tree, once per stage a requested mutant belongs to
    stages = ([] if not any(m.startswith("WF") for m in args.ids) else ["r1"]) + (
        [] if not any(m.startswith("WR") for m in args.ids) else ["r2"]
    )
    for stage in stages:
        tree = scratch / f"control-{stage}"
        copy_tree(tree)
        if stage == "r1":
            res = run_probes(tree, scratch / "control-run", out / "control", args)
            control_fail = failing(res["rows"])
        else:
            res = run_probes_r2(
                tree, scratch / "control-r2-run", out / "control-r2", args, R2_CONTROL
            )
            control_fail = failing(res["rows"], GATES_R2)
        control = {
            "id": f"control-{stage}",
            "failing": control_fail,
            "rc": res["rc"],
            "probes": len(res["rows"]),
            "passes": not control_fail and bool(res["rows"]),
            "containers_before": pre,
            "containers_after": leftovers(),
            "stderr": res["stderr"] if not res["rows"] else "",
        }
        print(f"control-{stage}:", json.dumps(control), flush=True)
        rows.append(control)
        if not control["passes"]:
            (out / "mutants.json").write_text(json.dumps({"rows": rows}, indent=2) + "\n")
            print("the control does not pass: no mutant is judged", file=sys.stderr)
            return 1
    for mid in args.ids:
        what, expected, _, unit = MUTANTS[mid]
        row: dict[str, Any] = {"id": mid, "mutant": what, "expected": expected}
        try:
            tree = scratch / mid.lower()
            copy_tree(tree)
            patch(tree, mid)
            if mid.startswith("WR"):
                res = run_probes_r2(
                    tree, scratch / f"{mid.lower()}-run", out / mid.lower(), args, R2_ONLY[mid]
                )
                bad = failing(res["rows"], GATES_R2)
            else:
                res = run_probes(tree, scratch / f"{mid.lower()}-run", out / mid.lower(), args)
                bad = failing(res["rows"])
            unit_ok, tail = run_unit(tree, scratch / f"{mid.lower()}-run", unit)
            caught = bad + ([] if unit_ok else [f"unit: {unit}"])
            row.update(
                caught_by=caught,
                killed=bool(caught),
                expected_hit=expected in bad,
                probes_ran=len(res["rows"]),
                evidence={r["id"]: r["observed"] for r in res["rows"] if r["id"] in bad},
                unit_tail=None if unit_ok else tail,
                stderr=res["stderr"] if not res["rows"] else "",
            )
        except Exception as err:  # noqa: BLE001 -- a mutant that cannot run is reported
            row.update(caught_by=[], killed=False, error=f"{type(err).__name__}: {err}"[:500])
        row["containers_left"] = leftovers()
        print(
            f"{mid}: killed={row['killed']} by {row.get('caught_by')} (expected {expected})",
            flush=True,
        )
        rows.append(row)
    mut = [r for r in rows if not r["id"].startswith("control")]
    killed = sum(1 for r in mut if r.get("killed"))
    doc = {"rows": rows, "killed": killed, "total": len(mut)}
    (out / "mutants.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    print(f"killed {killed}/{len(mut)}")
    return 0 if killed == len(mut) else 1


if __name__ == "__main__":
    raise SystemExit(main())
