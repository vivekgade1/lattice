"""The mutation run of spike 2 (engine descriptors and local modules), pre-registered in
``docs/results/2026-10-12-spike2-prereg.md``.

Modelled on ``dogfood/perf_mutants.py``: for each mutant, ``lattice/``, ``tests/``,
``docs/examples`` and ``dogfood/studio`` are copied to a scratch directory and the mutant's edit is
applied to the copy
(its old text must occur exactly once), then the spike's tests run there with the copy first on
``PYTHONPATH``: ``tests/spec/test_engines.py``, ``tests/modules`` and
``tests/studio/test_engines_api.py``; with ``--dogfood`` also the dogfood gate
(``dogfood/studio/check.sh``, the server served from the copy). A mutant is caught when any fails. A
control run of the unpatched copy comes first. The working tree is never patched.

Usage: ``uv run python dogfood/module_mutants.py [--out FILE] [--jobs N] [--dogfood] [ID ...]``.
Exit 0 when the control passes and every mutant is caught.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ["tests/spec/test_engines.py", "tests/modules", "tests/studio/test_engines_api.py"]
E, MY, MOD = (
    "lattice/spec/engines.py",
    "lattice/spec/engines/mysql.toml",
    "lattice/modules/__init__.py",
)
FILES = [E, MY, MOD, *SUITE, "dogfood/module_mutants.py"]

# id -> (behaviour, file, old text, new text)
MUTANTS: dict[str, tuple[str, str, str, str]] = {
    "MM1": (
        "the registry answers the wrong default port for mysql",
        MY,
        "default_port = 3306\n",
        "default_port = 3307  # MUTANT MM1\n",
    ),
    "MM2": (
        "the tamper check on load is skipped",
        MOD,
        "        if actual != e.sha256:\n",
        "        if False and actual != e.sha256:  # MUTANT MM2\n",
    ),
    "MM3": (
        "install writes without confirmation",
        MOD,
        "    if not yes and not ask(",
        "    if False and not yes and not ask(  # MUTANT MM3\n        ",
    ),
    "MM4": (
        "descriptor validation accepts an unknown capability flag",
        E,
        "        if key not in CAPABILITIES:\n",
        "        if False and key not in CAPABILITIES:  # MUTANT MM4\n",
    ),
    "MM5": (
        "the lock hash ignores file contents",
        MOD,
        "        h.update(hashlib.sha256(p.read_bytes()).digest())\n",
        "        h.update(b'')  # MUTANT MM5\n",
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache")
    for rel in ("lattice", "tests", "docs/examples", "dogfood/studio"):
        shutil.copytree(ROOT / rel, dst / rel, ignore=ignore)


def apply(dst: Path, mid: str) -> None:
    _what, rel, old, new = MUTANTS[mid]
    path = dst / rel
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{mid}: the patched text occurs {n} times in {rel} (must be once)")
    path.write_text(text.replace(old, new), encoding="utf-8")


def _env(dst: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env["PYTHONPATH"] = str(dst)
    env["LATTICE_STUDIO_PYTHONPATH"] = str(dst)  # the dogfood server runs the copy too
    return env


def run_pytest(dst: Path) -> dict[str, object]:
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(ROOT),
                "pytest",
                *SUITE,
                "-q",
                "-p",
                "no:cacheprovider",
                "-rf",
                "--no-header",
            ],
            cwd=str(dst),
            env=_env(dst),
            capture_output=True,
            text=True,
            timeout=900,
        )
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e.stdout or "") + "\n[timeout after 900s]", 124
    failed = sorted({m.group(1) for m in re.finditer(r"^FAILED (\S+)", out, re.M)})
    summary = next(
        (ln for ln in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed)", ln)), ""
    ).strip()
    return {
        "exit": rc,
        "failed": failed,
        "summary": summary,
        "seconds": round(time.monotonic() - t0, 1),
        "tail": out[-2500:],
    }


def run_dogfood(dst: Path) -> dict[str, object]:
    t0 = time.monotonic()
    r = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(ROOT),
            "python",
            "-m",
            "lattice.spec",
            "check",
            "--spec",
            "spec:SPEC",
            "--app",
            "harness:make_app",
            "--db",
            "harness:build_db",
            "--runtime",
            "harness:RUNTIME",
            "--layers",
            "DX",
        ],
        cwd=str(dst / "dogfood/studio"),
        env={**_env(dst), "PYTHONPATH": f"{dst / 'dogfood/studio'}:{dst}"},
        capture_output=True,
        text=True,
        timeout=3600,
    )
    head = (r.stdout.splitlines() or [""])[0]
    return {
        "exit": r.returncode,
        "summary": head,
        "seconds": round(time.monotonic() - t0, 1),
        "tail": (r.stdout + r.stderr)[-1500:],
    }


def one(mid: str, dogfood: bool) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"lattice-mod-mutant-{mid}-") as d:
        dst = Path(d)
        copy_tree(dst)
        if mid != "control":
            apply(dst, mid)
        res: dict[str, object] = {"pytest": run_pytest(dst)}
        if dogfood:
            res["dogfood"] = run_dogfood(dst)
    caught_by = [k for k, v in res.items() if v["exit"] != 0]  # type: ignore[index]
    if mid == "control":
        verdict = "PASS (control)" if not caught_by else "CONTROL FAILED"
    else:
        verdict = "caught" if caught_by else "SURVIVED"
    return {**res, "caught_by": caught_by, "verdict": verdict}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", default=list(MUTANTS))
    ap.add_argument("--out", type=Path, default=None, help="write the results JSON here")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--dogfood", action="store_true", help="also run the dogfood gate per mutant")
    args = ap.parse_args(argv)
    hashes = {f: sha(ROOT / f) for f in FILES if (ROOT / f).is_file()}
    print("hashes:", json.dumps(hashes), flush=True)
    with tempfile.TemporaryDirectory(prefix="lattice-mod-mutants-") as d:  # every patch applies
        copy_tree(Path(d))
        for mid in args.ids:
            if mid not in MUTANTS:
                raise SystemExit(f"unknown mutant {mid}")
            apply(Path(d), mid)
    results: dict[str, object] = {"hashes": hashes, "runs": {}}
    control = one("control", args.dogfood)
    results["runs"]["control"] = control  # type: ignore[index]
    print(f"control: {control['verdict']} ({control['pytest']['summary']})", flush=True)  # type: ignore[index]
    ok = control["verdict"] == "PASS (control)"
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(one, mid, args.dogfood): mid for mid in args.ids}
        for fut in concurrent.futures.as_completed(futs):
            mid = futs[fut]
            res = fut.result()
            results["runs"][mid] = res  # type: ignore[index]
            failed = res["pytest"]["failed"]  # type: ignore[index]
            print(
                f"{mid}: {res['verdict']} (by {', '.join(res['caught_by']) or 'nothing'}; "
                f"pytest failed {len(failed)}: {', '.join(f.split('::')[-1] for f in failed)})",
                flush=True,
            )
            ok = ok and res["verdict"] == "caught"
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
