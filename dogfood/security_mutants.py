"""The mutation run of spike 1b (``lattice.security``), pre-registered in
``docs/results/2026-10-12-spike1b-prereg.md``.

Modelled on ``dogfood/perf_mutants.py``: for each mutant, ``lattice/`` and ``tests/`` are copied
to a scratch directory and the mutant's edit is applied to the copy (its old text must occur
exactly once), then ``tests/security`` runs there with the copy first on ``PYTHONPATH``. A
mutant is CAUGHT when the suite fails. A control run of the unpatched copy comes first and must
pass. The working tree is never patched.

The five mutants and the test each is predicted to be caught by:

* SM1 judge confirms without a real replay   -> test_run (judge rejects / clean app quiet)
* SM2 a non-loopback target is allowed        -> test_guardrails (non-loopback host refused)
* SM3 the authz generator skips the anon caller -> test_plan (authz covers the anon cell)
* SM4 the bounds generator is off by one      -> test_plan (bounds is not off by one)
* SM5 a confirmed finding still exits 0       -> test_cli (holed app exits 1)

Usage: ``uv run python dogfood/security_mutants.py [--out FILE] [--jobs N] [ID ...]``.
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
SUITE = ["tests/security"]
RUN, TGT, PLAN, CLI = (
    "lattice/security/run.py",
    "lattice/security/target.py",
    "lattice/security/plan.py",
    "lattice/security/__main__.py",
)
FILES = [RUN, TGT, PLAN, CLI, "dogfood/security_mutants.py"]

# id -> (behaviour, file, old text, new text)
MUTANTS: dict[str, tuple[str, str, str, str]] = {
    "SM1": (
        "the judge confirms a claim without a real replay check",
        RUN,
        "        fired, why, obs, _errored = _attempt(fresh, attack)\n"
        "        return fired, why, obs\n",
        "        fired, why, obs, _errored = _attempt(fresh, attack)\n"
        "        return True, why, obs  # MUTANT SM1: confirm without replaying\n",
    ),
    "SM2": (
        "a non-loopback target is allowed",
        TGT,
        "    if host != IN_PROCESS and host not in LOOPBACK_HOSTS:\n"
        "        raise UnsafeTargetError(\n",
        "    if False and host != IN_PROCESS and host not in LOOPBACK_HOSTS:  # MUTANT SM2\n"
        "        raise UnsafeTargetError(\n",
    ),
    "SM3": (
        "the authz generator skips the anonymous caller",
        PLAN,
        "            status = ep.caller_status[caller]\n"
        "            if not _is_refusal(status) or caller in ep.owners:\n",
        "            if caller is None:  # MUTANT SM3: skip the anonymous caller\n"
        "                continue\n"
        "            status = ep.caller_status[caller]\n"
        "            if not _is_refusal(status) or caller in ep.owners:\n",
    ),
    "SM4": (
        "the bounds generator is off by one (sends the limit, not over it)",
        PLAN,
        '        vals.append(("just-over-le", p.le + 1))\n',
        '        vals.append(("just-over-le", p.le))  # MUTANT SM4: at the limit, not over\n',
    ),
    "SM5": (
        "a confirmed finding still exits 0",
        CLI,
        "    return 1 if report.confirmed else 0\n",
        "    return 0  # MUTANT SM5: a confirmed finding still exits 0\n",
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache")
    shutil.copytree(ROOT / "lattice", dst / "lattice", ignore=ignore)
    shutil.copytree(ROOT / "tests", dst / "tests", ignore=ignore)


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
    return env


def run_pytest(dst: Path) -> dict[str, object]:
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            ["uv", "run", "--project", str(ROOT), "pytest", *SUITE, "-q", "-p",
             "no:cacheprovider", "-rf", "--no-header"],
            cwd=str(dst), env=_env(dst), capture_output=True, text=True, timeout=900,
        )  # fmt: skip
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e.stdout or "") + "\n[timeout after 900s]", 124
    failed = sorted({m.group(1) for m in re.finditer(r"^FAILED (\S+)", out, re.M)})
    summary = next((ln for ln in reversed(out.splitlines())
                    if re.search(r"\d+ (passed|failed)", ln)), "").strip()  # fmt: skip
    return {"exit": rc, "failed": failed, "summary": summary,
            "seconds": round(time.monotonic() - t0, 1), "tail": out[-2000:]}  # fmt: skip


def one(mid: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"lattice-sec-mutant-{mid}-") as d:
        dst = Path(d)
        copy_tree(dst)
        if mid != "control":
            apply(dst, mid)
        res = run_pytest(dst)
    caught = res["exit"] != 0
    if mid == "control":
        verdict = "PASS (control)" if not caught else "CONTROL FAILED"
    else:
        verdict = "caught" if caught else "SURVIVED"
    return {"pytest": res, "verdict": verdict}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", default=list(MUTANTS))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--jobs", type=int, default=2)
    args = ap.parse_args(argv)
    hashes = {f: sha(ROOT / f) for f in FILES}
    print("hashes:", json.dumps(hashes), flush=True)
    with tempfile.TemporaryDirectory() as d:  # every patch applies cleanly before anything runs
        copy_tree(Path(d))
        for mid in args.ids:
            if mid not in MUTANTS:
                raise SystemExit(f"unknown mutant {mid}")
            apply(Path(d), mid)
    results: dict[str, object] = {"hashes": hashes, "runs": {}}
    control = one("control")
    results["runs"]["control"] = control  # type: ignore[index]
    print(f"control: {control['verdict']} ({control['pytest']['summary']})", flush=True)
    ok = control["verdict"] == "PASS (control)"
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(one, mid): mid for mid in args.ids}
        for fut in concurrent.futures.as_completed(futs):
            mid = futs[fut]
            res = fut.result()
            results["runs"][mid] = res  # type: ignore[index]
            failed = res["pytest"]["failed"]  # type: ignore[index]
            print(f"{mid}: {res['verdict']} (pytest failed {len(failed)}: "
                  f"{', '.join(f.split('::')[-1] for f in failed)})", flush=True)  # fmt: skip
            ok = ok and res["verdict"] == "caught"
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
