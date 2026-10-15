"""The mutation run of API v1.21 (the improvement loop in the Studio UI), pre-registered in
``docs/results/2026-10-15-studio-ui-prereg.md`` (section 4): control + U1-U17.

For each mutant, ``lattice/``, ``tests/`` and ``dogfood/studio/project_seed/`` are copied to a
scratch directory and ONE edit is applied to the copy (its old text must occur exactly once). The
catchers the prereg predicts then run against the copy:

* a probe of ``dogfood/studio_ui_probes.py`` (P0-P8) with ``STUDIO_UI_TREE=<copy>``: the probes'
  servers import the copy; the CLI side and the base archive stay the working tree's;
* the dogfood gate ``dogfood/studio/check.sh`` with ``LATTICE_STUDIO_PYTHONPATH=<copy>``;
* P10, ``tests/studio/test_studio_ui.py`` run inside the copy.

Every mutant is also run against the pytest suites of this change (``tests/studio/
test_improve_api.py`` and ``test_studio_ui.py``), reported separately as extra catchers. A mutant
is CAUGHT when a predicted catcher fails; one that no predicted catcher fails is reported (a
survivor is never fixed silently). A control run of the unpatched copy comes first and must pass
every catcher. The working tree is never patched.

Usage: ``uv run python dogfood/studio_ui_mutants.py [--out DIR] [--jobs N] [ID ...]``.
Exit 0 when the control passes and every mutant is caught by a predicted catcher.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S = "lattice/studio/server.py"
M = "lattice/studio/mock_server.py"
IMP = "lattice/studio/web/js/views/improve.js"
RB = "lattice/studio/web/js/views/rebuilds.js"

# id -> (behaviour, file, old text, new text, predicted catchers)
MUTANTS: dict[str, tuple[str, str, str, str, list[str]]] = {
    "U1": (
        "--allow-apply ignored (the writes are always on)",
        S,
        "        if not gate.allow:  # (b)\n",
        "        if False:  # (b) MUTANT U1\n",
        ["P0"],
    ),
    "U2": (
        "the Origin check removed",
        S,
        '            origin is not None and origin != f"http://{host}"\n',
        "            False  # MUTANT U2\n",
        ["P0"],
    ),
    "U3": (
        "the Host check removed",
        S,
        '        if host not in (f"127.0.0.1:{port}", f"localhost:{port}") or (\n',
        "        if False or (  # MUTANT U3\n",
        ["P0"],
    ),
    "U4": (
        "the Content-Type check removed",
        S,
        '        if ctype != "application/json":  # (d)\n',
        "        if False:  # (d) MUTANT U4\n",
        ["P0"],
    ),
    "U5": (
        "the id check skipped on GET .../apply (the record route reads the raw id)",
        S,
        "                if not APPLY_ID.match(ident):\n"
        '                    return self._json({"detail": f"bad id {ident!r}", '
        '"code": "bad_id"}, 422)\n',
        '                if not APPLY_ID.match(ident) and parts[1] != "apply":  # MUTANT U5\n'
        '                    return self._json({"detail": f"bad id {ident!r}", '
        '"code": "bad_id"}, 422)\n',
        ["P0", "gate"],
    ),
    "U6": (
        "the draft_sha256 comparison removed",
        S,
        "            if current != sha:\n",
        "            if False:  # MUTANT U6\n",
        ["P0", "gate"],
    ),
    "U7": (
        "a discard appends a human event to the log",
        S,
        "            code, doc = apply_mod.discard(self.root, store.root, ident)\n",
        "            code, doc = apply_mod.discard(self.root, store.root, ident)\n"
        '            self.events.append("human", what="apply_discard", actor="owner", '
        "change=ident)  # MUTANT U7\n",
        ["gate", "P5"],
    ),
    "U8": (
        "no_gates=gates (inverted)",
        S,
        "                            no_gates=not gates,\n",
        "                            no_gates=gates,  # MUTANT U8\n",
        ["P2", "P3"],
    ),
    "U9": (
        "the stop event is not passed to apply()",
        S,
        "                            stop=gate.stop,\n",
        "                            stop=None,  # MUTANT U9\n",
        ["P3"],
    ),
    "U10": (
        "state_flag is always None",
        S,
        "            flag = None if default.resolve() == store.root.resolve() "
        "else str(store.root)\n",
        "            flag = None  # MUTANT U10\n",
        ["P2"],
    ),
    "U11": (
        "the apply lock is per project, not per server",
        S,
        "        gate = self.server.apply_gate\n        if not gate.allow:  # (b)\n",
        "        gate = self.server.__dict__.setdefault('_pg', {}).setdefault(  # MUTANT U11\n"
        "            st.name, ApplyGate(self.server.apply_gate.allow)\n"
        "        )\n"
        "        if not gate.allow:  # (b)\n",
        ["P6"],
    ),
    "U12": (
        "the applies listing globs *.json (discarded records are listed)",
        S,
        "            if DISCARDED_RECORD.search(p.name):\n",
        "            if False:  # MUTANT U12\n",
        ["P4"],
    ),
    "U13": (
        "verify drops `at`",
        S,
        "        return verify(events, ledger, ident, at=at, warnings=warnings)[1]\n",
        "        return verify(events, ledger, ident, at=None, warnings=warnings)[1]"
        "  # MUTANT U13\n",
        ["P1"],
    ),
    "U14": (
        "the mock's applies listing drops `running`",
        M,
        '            "running": running,\n',
        "",
        ["P8"],
    ),
    "U15": (
        "canApply is true for a prereg draft",
        IMP,
        '  if (draft.template !== "config") return false;\n',
        '  if (draft.template !== "config" && draft.template !== "prereg") return false;\n',
        ["P10"],
    ),
    "U16": (
        "GET /api/improve gains an applies key",
        S,
        '        return {k: rep[k] for k in ("proposals", "events", "method", "blind_spots")}\n',
        '        return {**{k: rep[k] for k in ("proposals", "events", "method", "blind_spots")},'
        ' "applies": []}  # MUTANT U16\n',
        ["P7"],
    ),
    "U17": (
        "one byte of views/rebuilds.js edited",
        RB,
        "// Evidence > Rebuilds: the live Rebuild card",
        "// Evidence > Rebuilds; the live Rebuild card",
        ["P7"],
    ),
}
EXTRA = ["tests/studio/test_improve_api.py", "tests/studio/test_studio_ui.py"]
PROBE_ROWS = {  # catcher -> (the --only key, the summary row that must pass)
    "P0": ("P0", "P0"),
    "P1": ("P1", "P1"),
    "P2": ("P2", "P2"),
    "P3": ("P3", "P3"),
    "P4": ("P4", "P4"),
    "P5": ("P4", "P5"),
    "P6": ("P6", "P6"),
    "P7": ("P7", "P7"),
    "P8": ("P8", "P8"),
}


def env_base() -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    return e


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache")
    shutil.copytree(ROOT / "lattice", dst / "lattice", ignore=ignore)
    shutil.copytree(ROOT / "tests", dst / "tests", ignore=ignore)
    for rel in (
        Path("dogfood") / "studio" / "project_seed",
        Path("docs") / "results" / "data" / "2026-10-12-spike3",  # the tests' event log
    ):
        shutil.copytree(ROOT / rel, dst / rel, ignore=ignore)


def apply(dst: Path, mid: str) -> None:
    _what, rel, old, new, _catch = MUTANTS[mid]
    path = dst / rel
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{mid}: the patched text occurs {n} times in {rel} (must be once)")
    path.write_text(text.replace(old, new), encoding="utf-8")


def run_probe(dst: Path, work: Path, key: str) -> dict[str, object]:
    env = env_base()
    env["STUDIO_UI_TREE"] = str(dst)
    env["PYTHONPATH"] = str(ROOT)  # the probes' own imports; the servers get STUDIO_UI_TREE
    out = work / f"probe-{key}"
    t0 = time.monotonic()
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "dogfood/studio_ui_probes.py"),
            "--runs",
            "1",
            "--only",
            key,
            "--scratch",
            str(work / f"scratch-{key}"),
            "--out",
            str(out),
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    summary = out / "summary.json"
    rows: dict[str, dict[str, object]] = {}
    if summary.is_file():
        for t in json.loads(summary.read_text())["table"]:
            rows[t["id"]] = {"passed": bool(t["pass"]), "observed": str(t["observed"])[:300]}
    problems = [ln.strip() for ln in r.stdout.splitlines() if "problem:" in ln][:8]
    return {
        "kind": "probe",
        "rows": rows,
        "problems": problems,
        "seconds": round(time.monotonic() - t0, 1),
        "tail": r.stdout[-800:] + r.stderr[-400:],
    }


def run_gate(dst: Path) -> dict[str, object]:
    env = env_base()
    env["LATTICE_STUDIO_PYTHONPATH"] = str(dst)
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            ["./check.sh"],
            cwd=str(ROOT / "dogfood" / "studio"),
            env=env,
            capture_output=True,
            text=True,
            timeout=1500,
        )
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e.stdout or "") + "\n[timeout]", 124
    return {
        "kind": "gate",
        "exit": rc,
        "passed": rc == 0,
        "summary": out.splitlines()[0] if out else "",
        "seconds": round(time.monotonic() - t0, 1),
        "tail": out[:1500],
    }


def run_pytest(dst: Path, files: list[str]) -> dict[str, object]:
    env = env_base()
    env["PYTHONPATH"] = str(dst)
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *files,
                "-q",
                "-p",
                "no:cacheprovider",
                "-rf",
                "--no-header",
            ],
            cwd=str(dst),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e.stdout or "") + "\n[timeout]", 124
    failed = sorted({ln.split()[1] for ln in out.splitlines() if ln.startswith("FAILED ")})
    last = next((ln for ln in reversed(out.splitlines()) if "passed" in ln or "failed" in ln), "")
    return {
        "kind": "pytest",
        "exit": rc,
        "passed": rc == 0,
        "failed": failed,
        "summary": last,
        "seconds": round(time.monotonic() - t0, 1),
        "tail": out[-1500:],
    }


def one(mid: str) -> dict[str, object]:
    catchers = (
        MUTANTS[mid][4]
        if mid != "control"
        else ["P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "gate", "P10"]
    )
    with tempfile.TemporaryDirectory(prefix=f"lattice-ui-mutant-{mid}-") as d:
        dst = Path(d) / "tree"
        work = Path(d) / "work"
        work.mkdir()
        dst.mkdir()
        copy_tree(dst)
        if mid != "control":
            apply(dst, mid)
        results: dict[str, dict[str, object]] = {}
        done_keys: dict[str, dict[str, object]] = {}
        for c in catchers:
            if c == "gate":
                results[c] = run_gate(dst)
            elif c == "P10":
                results[c] = run_pytest(dst, ["tests/studio/test_studio_ui.py"])
            else:
                key, row = PROBE_ROWS[c]
                if key not in done_keys:
                    done_keys[key] = run_probe(dst, work, key)
                got = done_keys[key]["rows"].get(row)  # type: ignore[union-attr]
                results[c] = {
                    "kind": "probe",
                    "passed": bool(got and got["passed"]),
                    "observed": got["observed"] if got else "no result",
                    "problems": done_keys[key]["problems"],
                    "seconds": done_keys[key]["seconds"],
                }
        extra = run_pytest(dst, EXTRA)
    caught_by = [c for c, v in results.items() if not v["passed"]]
    if mid == "control":
        verdict = "PASS (control)" if not caught_by and extra["passed"] else "CONTROL FAILED"
    else:
        verdict = "caught" if caught_by else "SURVIVED"
    return {
        "catchers": results,
        "extra_pytest": extra,
        "caught_by": caught_by,
        "predicted": catchers,
        "verdict": verdict,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", default=list(MUTANTS))
    ap.add_argument("--out", type=Path, default=None, help="write results.json here")
    ap.add_argument("--jobs", type=int, default=3)
    args = ap.parse_args(argv)
    for mid in args.ids:  # every edit applies before anything runs
        with tempfile.TemporaryDirectory(prefix="lattice-ui-mutant-dry-") as d:
            rel = MUTANTS[mid][1]
            (Path(d) / rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(d) / rel).write_text((ROOT / rel).read_text(encoding="utf-8"))
            apply(Path(d), mid)
    results: dict[str, object] = {"runs": {}}
    control = one("control")
    results["runs"]["control"] = control  # type: ignore[index]
    print(
        f"control: {control['verdict']} (failed catchers: {control['caught_by']}; "
        f"extra pytest: {control['extra_pytest']['summary']})",
        flush=True,
    )
    ok = control["verdict"] == "PASS (control)"
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(one, mid): mid for mid in args.ids}
        for fut in concurrent.futures.as_completed(futs):
            mid = futs[fut]
            res = fut.result()
            results["runs"][mid] = res  # type: ignore[index]
            ok = ok and res["verdict"] == "caught"
            extra = res["extra_pytest"]
            print(
                f"{mid}: {res['verdict']} by {res['caught_by']} (predicted {res['predicted']}); "
                f"suites: {extra['summary']} {extra['failed'][:4]}",
                flush=True,
            )
    results["table"] = [
        {
            "id": mid,
            "behaviour": MUTANTS[mid][0],
            "predicted": MUTANTS[mid][4],
            "caught_by": results["runs"][mid]["caught_by"],  # type: ignore[index]
            "verdict": results["runs"][mid]["verdict"],
        }  # type: ignore[index]
        for mid in args.ids
    ]
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "results.json").write_text(json.dumps(results, indent=1))
    print(
        f"mutants: {sum(1 for t in results['table'] if t['verdict'] == 'caught')}/"  # type: ignore[index]
        f"{len(args.ids)} caught",
        flush=True,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
