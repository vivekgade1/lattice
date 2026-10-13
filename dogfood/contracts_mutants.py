"""The mutation run of layer 3 (contracts between projects), pre-registered with
``docs/results/2026-10-13-contracts-prereg.md`` section 6.

Modelled on ``dogfood/multi_mutants.py``: for each mutant the sources and the suite are copied to
a scratch directory and the mutant's one-line edit is applied to the COPY (its old text must
occur exactly once in the CURRENT source: a stale patch fails loudly, it is never skipped), then
the suite runs there with the copy first on ``PYTHONPATH``. A mutant is KILLED when pytest runs
and reports a failing test; a mutant that does not compile, or a run that ends in a collection or
import error (no ``FAILED`` line), is an ERROR and never counts as a kill. A control run of the
unpatched copy comes first and must pass. The real tree is never written to (the hashes of every
mutated file are checked again at the end).

The eleven mutants and the probes the prereg predicted to kill each (D, L, W, C, V, B: the
prereg's probe series; the tests are in ``tests/contracts``):

* CM1  status ignored: ``status_not_observed`` never emitted             (D2, L2)
* CM2  auth ignored: ``auth``/``as_user`` never compared                 (D5, L3)
* CM3  ``endpoint_missing`` treated as satisfied                         (D1)
* CM4  pin ignored: P := E (every unsatisfied reliance is a gap)         (D1-D5, D6, D9, C4)
* CM5  opt-in ignored: every link is checked whatever ``contract`` says  (W1)
* CM6  field reliance ignored (request and response)                     (D3, D4, L4)
* CM7  ids not templated (concrete paths kept)                           (B0)
* CM8  ``spec_hash`` over the spec file bytes, not the canonical export  (C2, C3)
* CM9  ``pin`` writes despite BROKEN                                     (V4)
* CM10 live: the ``ensure_safe_target`` call removed                     (L5)
* CM11 ``Link.to_dict`` always emits ``contract``                        (W5)

Test selection: ``tests/contracts tests/workspace -q -x`` (about a minute); a mutant that
touches only ``live.py`` runs ``tests/contracts/test_live.py`` first and the rest only when it
survives that. ``--all-failures`` drops ``-x`` to list every killing test.

Usage: ``python dogfood/contracts_mutants.py [--out FILE] [--jobs N] [--all-failures] [ID ...]``.
Exit 0 when the control passes and every mutant is killed; the last line is
``mutants: N/N killed``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SUITE = ["tests/contracts", "tests/workspace"]
LIVE_ONLY = ["tests/contracts/test_live.py"]
CK, EX, LV, WS = (
    "lattice/contracts/check.py",
    "lattice/contracts/export.py",
    "lattice/contracts/live.py",
    "lattice/workspace.py",
)
# copied into the scratch tree (dogfood/contracts only: the fixtures, not another node's probes)
COPY = [
    "lattice",
    "pyproject.toml",
    "tests/conftest.py",
    "tests/contracts",
    "tests/workspace",
    "dogfood/contracts/ci-bot",
    "dogfood/contracts/golden",
]

# id -> (behaviour, file, old text, new text, first-stage tests or None)
MUTANTS: dict[str, tuple[str, str, str, str, list[str] | None]] = {
    "CM1": (
        "status ignored: status_not_observed is never emitted",
        CK,
        '        return int(item) in ep["statuses"]\n',
        "        return True  # MUTANT CM1: a status claim always holds\n",
        None,
    ),
    "CM2": (
        "auth ignored: auth / as_user never compared",
        CK,
        '    if kind == "auth":\n        if item == "public":\n',
        '    if kind == "auth":\n'
        "        return True  # MUTANT CM2: an auth claim always holds\n"
        '        if item == "public":\n',
        None,
    ),
    "CM3": (
        "endpoint_missing treated as satisfied",
        CK,
        '    if ep is None:\n        return False\n    if kind == "endpoint":\n',
        "    if ep is None:\n"
        '        return kind == "endpoint"  # MUTANT CM3: missing is satisfied\n'
        '    if kind == "endpoint":\n',
        None,
    ),
    "CM4": (
        "pin ignored: P := E (every unsatisfied reliance is a gap, no CHANGED)",
        CK,
        "    pinned_v = version_of(entry)\n",
        "    pinned_v = version_of(entry)\n"
        '    entry = {**entry, "export": cur["export"]}  # MUTANT CM4: P := E\n',
        None,
    ),
    "CM5": (
        "opt-in ignored: every link is checked whatever contract says",
        CK,
        "    return [lk for lk in ws.links if lk.contract and (consumer is None or "
        "lk.source == consumer)]\n",
        "    return [lk for lk in ws.links if (consumer is None or lk.source == consumer)]"
        "  # MUTANT CM5\n",
        None,
    ),
    "CM6": (
        "field reliance ignored (request and response)",
        CK,
        "        for kind, item in claims_of(imp):\n",
        "        for kind, item in (c for c in claims_of(imp) if not c[0].endswith('_field')):"
        "  # MUTANT CM6\n",
        None,
    ),
    "CM7": (
        "ids not templated (concrete paths kept)",
        EX,
        "        if _INT.fullmatch(seg) or _UUID.fullmatch(seg) or _NAMED.fullmatch(seg):\n",
        "        if _NAMED.fullmatch(seg):  # MUTANT CM7: digits and UUIDs are kept\n",
        None,
    ),
    "CM8": (
        "spec_hash over the spec file bytes, not the canonical export",
        EX,
        '        "version": {"dirty": dirty, "git_ref": git_ref, '
        '"spec_hash": spec_hash(export)},\n',
        '        "version": {"dirty": dirty, "git_ref": git_ref, "spec_hash": hashlib.sha256('
        'Path(sys.modules[ref.split(":", 1)[0]].__file__).read_bytes()).hexdigest()},'
        "  # MUTANT CM8\n",
        None,
    ),
    "CM9": (
        "pin writes despite BROKEN",
        CK,
        '            "status": "refused" if broken and not force else "pinned", "error": None,\n',
        '            "status": "pinned", "error": None,  # MUTANT CM9: BROKEN does not refuse\n',
        None,
    ),
    "CM10": (
        "live: the ensure_safe_target call removed",
        LV,
        "    try:\n        ensure_safe_target(host)\n    except UnsafeTargetError as e:\n",
        "    try:\n        pass  # MUTANT CM10: no loopback guard\n"
        "    except UnsafeTargetError as e:\n",
        LIVE_ONLY,
    ),
    "CM11": (
        "Link.to_dict always emits contract",
        WS,
        '        if self.contract is not None:\n            out["contract"] = self.contract\n',
        '        out["contract"] = self.contract  # MUTANT CM11: emitted even when None\n',
        None,
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns(
        "__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache", "node_modules", "_tmp_*"
    )
    for rel in COPY:
        src = ROOT / rel
        if not src.exists():
            raise SystemExit(f"cannot copy {rel}: it is not in {ROOT}")
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, out, ignore=ignore)
        else:
            shutil.copy2(src, out)


def apply(dst: Path, mid: str) -> None:
    _what, rel, old, new, _first = MUTANTS[mid]
    path = dst / rel
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{mid}: the patched text occurs {n} times in {rel} (must be once)")
    if old == new:
        raise SystemExit(f"{mid}: the patch changes nothing")
    mutated = text.replace(old, new)
    try:
        compile(mutated, rel, "exec")
    except SyntaxError as e:
        raise SystemExit(
            f"{mid}: the mutant does not compile ({e}); a syntax error is no kill"
        ) from e
    path.write_text(mutated, encoding="utf-8")


def _env(dst: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env["PYTHONPATH"] = str(dst)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _pytest_cmd() -> list[str]:
    if importlib.util.find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest"]
    return ["uv", "run", "--no-sync", "--project", str(ROOT), "pytest"]


def run_pytest(dst: Path, tests: list[str], all_failures: bool) -> dict[str, Any]:
    t0 = time.monotonic()
    cmd = [*_pytest_cmd(), *tests, "-q", "-p", "no:cacheprovider", "-rf", "--no-header"]
    if not all_failures:
        cmd.append("-x")
    try:
        r = subprocess.run(
            cmd, cwd=str(dst), env=_env(dst), capture_output=True, text=True, timeout=1200
        )
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e.stdout or "") + "\n[timeout after 1200s]", 124
    failed = [m.group(1) for m in re.finditer(r"^FAILED (\S+)", out, re.M)]
    summary = next(
        (ln for ln in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed|error)", ln)),
        "",
    ).strip()
    return {
        "exit": rc,
        "failed": failed,
        "summary": summary,
        "seconds": round(time.monotonic() - t0, 1),
        "tail": out[-2000:],
    }


def one(mid: str, all_failures: bool) -> dict[str, Any]:
    """Run the control (`mid` == "control") or one mutant: the first stage, then the full suite
    only when a first stage exists and the mutant survived it."""
    t0 = time.monotonic()
    first = None if mid == "control" else MUTANTS[mid][4]
    stages: list[list[str]] = [first, SUITE] if first else [SUITE]
    with tempfile.TemporaryDirectory(prefix=f"lattice-contracts-mutant-{mid}-") as d:
        dst = Path(d)
        copy_tree(dst)
        if mid != "control":
            apply(dst, mid)
        res: dict[str, Any] = {}
        for tests in stages:
            res = run_pytest(dst, tests, all_failures)
            res["tests"] = " ".join(tests)
            if res["exit"] != 0:
                break
    if mid == "control":
        verdict = "PASS (control)" if res["exit"] == 0 else "CONTROL FAILED"
    elif res["exit"] == 0:
        verdict = "SURVIVED"
    elif res["failed"]:
        verdict = "killed"
    else:
        verdict = "ERROR"  # a collection/import error or a timeout: not a kill
    return {"pytest": res, "verdict": verdict, "seconds": round(time.monotonic() - t0, 1)}


def _order(mid: str) -> int:
    return int(mid[2:])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", default=sorted(MUTANTS, key=_order))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--all-failures", action="store_true", help="run without -x")
    args = ap.parse_args(argv)
    for mid in args.ids:
        if mid not in MUTANTS:
            raise SystemExit(f"unknown mutant {mid}")
    files = sorted({MUTANTS[m][1] for m in MUTANTS})
    hashes = {f: sha(ROOT / f) for f in files}
    print("hashes:", json.dumps(hashes), flush=True)
    with tempfile.TemporaryDirectory() as d:  # every patch applies cleanly before anything runs
        copy_tree(Path(d))
        for mid in args.ids:
            apply(Path(d), mid)
            (Path(d) / MUTANTS[mid][1]).write_text(  # undo: each patch is checked on its own
                (ROOT / MUTANTS[mid][1]).read_text(encoding="utf-8"), encoding="utf-8"
            )
    t0 = time.monotonic()
    runs: dict[str, dict[str, Any]] = {}
    control = one("control", args.all_failures)
    runs["control"] = control
    print(
        f"control: {control['verdict']} ({control['pytest']['summary']}) {control['seconds']}s",
        flush=True,
    )
    ok = control["verdict"] == "PASS (control)"
    if ok:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            futs = {ex.submit(one, mid, args.all_failures): mid for mid in args.ids}
            for fut in concurrent.futures.as_completed(futs):
                mid = futs[fut]
                runs[mid] = res = fut.result()
                failed = res["pytest"]["failed"]
                print(
                    f"{mid}: {res['verdict']} ({res['seconds']}s, pytest failed {len(failed)}: "
                    f"{', '.join(f.split('::', 1)[-1] for f in failed[:3])})",
                    flush=True,
                )
    else:
        print(control["pytest"]["tail"], flush=True)
    changed = [f for f in files if sha(ROOT / f) != hashes[f]]
    print(f"\n{'id':<8} {'verdict':<14} {'sec':>6}  {'change':<62} killed by (first failing test)")
    for mid in sorted(runs, key=lambda m: -1 if m == "control" else _order(m)):
        res = runs[mid]
        if mid == "control":
            what, by = "unmutated copy", res["pytest"]["summary"]
        else:
            what = MUTANTS[mid][0]
            failed = res["pytest"]["failed"]
            by = failed[0] if failed else res["verdict"]
        print(f"{mid:<8} {res['verdict']:<14} {res['seconds']:>6}  {what[:62]:<62} {by}")
    killed = sum(1 for m in args.ids if runs.get(m, {}).get("verdict") == "killed")
    if changed:
        print(f"the real tree was modified: {changed}", file=sys.stderr)
    print(f"total wall time: {round(time.monotonic() - t0, 1)}s")
    if args.out:
        args.out.write_text(json.dumps({"hashes": hashes, "runs": runs}, indent=1))
    print(f"mutants: {killed}/{len(args.ids)} killed")
    return 0 if ok and killed == len(args.ids) and not changed else 1


if __name__ == "__main__":
    sys.exit(main())
