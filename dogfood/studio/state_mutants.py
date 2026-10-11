"""The mutation run of API v1.10 (durable state and resume), pre-registered in
``docs/results/2026-10-11-state-resume-prereg.md``.

For each mutant: ``lattice/``, ``tests/`` and ``dogfood/studio/project_seed/`` are copied to
a scratch directory, ONE patch is applied to the copy (its old text must occur exactly once),
and ``tests/studio/test_state_store.py`` and ``tests/studio/test_resume.py`` run there with
the copy first on ``PYTHONPATH`` (the restart test's Studio servers import the copy too).
The working tree is never patched. A control run of the unpatched copy comes first.
Mutants run one at a time (the restart test takes ports from 8900-8999).

Usage: ``uv run python dogfood/studio/state_mutants.py [--out DIR] [ID ...]``. Exit 0 when
the control passes and every mutant run fails (caught).
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
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ["tests/studio/test_state_store.py", "tests/studio/test_resume.py"]
FILES = ["lattice/studio/state.py", "lattice/studio/plan.py", "lattice/studio/rebuild.py",
         "lattice/studio/server.py", *SUITE]  # fmt: skip

# id -> (file, behaviour, old text, new text)
MUTANTS: dict[str, tuple[str, str, str, str]] = {
    "S-M1": (
        "lattice/studio/state.py",
        "state file written non-atomically (a direct write: a kill mid-write truncates it)",
        """    tmp = d / f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(str(tmp))
        raise
    _fsync_dir(d)""",
        """    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)  # MUTANT S-M1
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    finally:
        os.close(fd)""",
    ),
    "S-M2": (
        "lattice/studio/plan.py",
        "a plan that was running stays running after restart (zombie)",
        """            if rec.get("status") in TRANSIENT:
                self._reconcile(plan)""",
        """            if False and rec.get("status") in TRANSIENT:  # MUTANT S-M2
                self._reconcile(plan)""",
    ),
    "S-M3": (
        "lattice/studio/plan.py",
        "resume re-runs nodes that already passed and landed",
        """                    if n["status"] == "passed":
                        continue
                    bad = [d for d in n["depends_on"] if nodes[d]["status"] != "passed"]""",
        """                    if n["status"] == "passed" and False:  # MUTANT S-M3
                        continue
                    bad = [d for d in n["depends_on"] if nodes[d]["status"] != "passed"]""",
    ),
    "S-M4": (
        "lattice/studio/plan.py",
        "land advances the branch while it is checked out without confirmation",
        """            if name == current and not confirm:""",
        """            if False and name == current and not confirm:  # MUTANT S-M4""",
    ),
    "S-M5": (
        "lattice/studio/state.py",
        "a corrupt state file crashes startup",
        """            except (OSError, ValueError, UnicodeDecodeError) as exc:
                self._quarantine(p, f"unreadable state file ({type(exc).__name__}: {exc})")
                continue""",
        """            except (OSError, ValueError, UnicodeDecodeError):  # MUTANT S-M5
                raise""",
    ),
    "S-M6": (
        "lattice/studio/state.py",
        "orphan cleanup kills by pid without matching the command line",
        """    return command == info.get("command") or (bool(match) and match in command)""",
        """    return True  # MUTANT S-M6: the command line is not compared""",
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache")
    shutil.copytree(ROOT / "lattice", dst / "lattice", ignore=ignore)
    shutil.copytree(ROOT / "tests", dst / "tests", ignore=ignore)
    shutil.copytree(ROOT / "dogfood" / "studio" / "project_seed",
                    dst / "dogfood" / "studio" / "project_seed", ignore=ignore)  # fmt: skip


def apply(dst: Path, mid: str) -> None:
    rel, _what, old, new = MUTANTS[mid]
    path = dst / rel
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{mid}: the patched text occurs {n} times in {rel} (must be once)")
    path.write_text(text.replace(old, new), encoding="utf-8")


def run_suite(dst: Path) -> dict[str, object]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env["PYTHONPATH"] = str(dst)
    t0 = time.monotonic()
    r = subprocess.run(
        ["uv", "run", "--project", str(ROOT), "pytest", *SUITE, "-q", "-p", "no:cacheprovider",
         "-rf", "--no-header"],
        cwd=str(dst), env=env, capture_output=True, text=True, timeout=1800,
    )  # fmt: skip
    out = r.stdout + r.stderr
    failed = sorted({m.group(1) for m in re.finditer(r"^FAILED (\S+)", out, re.M)})
    summary = next(
        (ln for ln in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed)", ln)), ""
    )
    return {"exit": r.returncode, "failed": failed, "summary": summary.strip(),
            "seconds": round(time.monotonic() - t0, 1), "tail": out[-3000:]}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", default=list(MUTANTS))
    ap.add_argument("--out", type=Path, default=None, help="write results.json here")
    args = ap.parse_args(argv)
    hashes = {f: sha(ROOT / f) for f in FILES}
    print("hashes:", json.dumps(hashes), flush=True)
    results: dict[str, object] = {"hashes": hashes, "runs": {}}
    ok = True
    for mid in ["control", *args.ids]:
        with tempfile.TemporaryDirectory(prefix=f"lattice-mutant-{mid}-") as d:
            dst = Path(d)
            copy_tree(dst)
            if mid != "control":
                apply(dst, mid)
            res = run_suite(dst)
        caught = res["exit"] != 0
        if mid == "control":
            ok = ok and not caught
            verdict = "PASS (control)" if not caught else "CONTROL FAILED"
        else:
            ok = ok and caught
            verdict = "caught" if caught else "SURVIVED"
        results["runs"][mid] = {**res, "verdict": verdict}  # type: ignore[index]
        print(
            f"{mid}: {verdict}; exit {res['exit']}; {res['summary']}; {res['seconds']}s", flush=True
        )
        for t in res["failed"]:  # type: ignore[union-attr]
            print(f"    FAILED {t}", flush=True)
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "results.json").write_text(json.dumps(results, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
