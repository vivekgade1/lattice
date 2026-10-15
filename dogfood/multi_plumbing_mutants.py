"""The mutation run of the multi-project plumbing change, pre-registered with
``docs/results/2026-10-15-multi-project-plumbing-prereg.md`` (section 4, and Amendment 1: PM7' and
PM8' replace PM7 and PM8, PM17 is new).

Modelled on ``dogfood/multi_mutants.py``: ``lattice/``, ``tests/``, ``dogfood/`` and
``lattice-claude-plugin/`` are copied to a scratch directory, the mutant's edit is applied to
the copy (its old text must occur exactly once in the CURRENT source), and the suite runs there
with the copy first on ``PYTHONPATH``: the pytest files of ``tests/mcp`` and the probe runner's
L-plugin, L-url and L-guard cases (``dogfood/multi_plumbing_probes.py --runs 1``, without P1, R1
and the old column, which need git or the real tree). A mutant is CAUGHT when either fails. A
control run of the unpatched copy comes first and must pass. The working tree is never patched.

The prediction column (blind, from the pre-registration) is compared with the probe ids that
actually failed: a predicted catcher that did not fail is reported as MISSED, any other failure as
extra. Usage::

    python dogfood/multi_plumbing_mutants.py --old $S/old-a4ab4a1 [--out FILE] [--jobs N] [ID ...]

Exit 0 when the control passes and every mutant is caught; the last line is ``mutants: N/N killed``.
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
GD, SV = "lattice/mcp/guard.py", "lattice/mcp/server.py"
FILES = [GD, SV, "dogfood/multi_plumbing_probes.py", "dogfood/multi_plumbing_mutants.py"]
PROBES = (
    [f"P{i}" for i in range(2, 8)]
    + [f"U{i}" for i in range(1, 7)]
    + [f"G{i:02d}" for i in range(1, 31)]
    + ["C16", "C29"]
)


def g(*rows: int) -> list[str]:
    return [f"G{r:02d}" for r in rows]


# id -> (behaviour, file, old text, new text, predicted catching probe ids)
MUTANTS: dict[str, tuple[str, str, str, str, list[str]]] = {
    "PM1": (
        "no walk-down",
        GD,
        "        if below:\n            found = self.project_below(resolved)\n",
        "        if False:  # MUTANT PM1: no walk down\n"
        "            found = self.project_below(resolved)\n",
        g(1, 2, 3, 4, 5, 11, 12, 13, 17, 18, 25, 26, 29),
    ),
    "PM2": (
        "the walk follows directory symlinks",
        GD,
        "e.is_dir(follow_symlinks=False)",
        "e.is_dir()",
        g(10),
    ),
    "PM3": (
        "depth cap 4 (off by one)",
        GD,
        "WALK_DEPTH = 5\n",
        "WALK_DEPTH = 4  # MUTANT PM3\n",
        g(13),
    ),
    "PM4": (
        "no budget",
        GD,
        "                if spent >= WALK_BUDGET:\n                    return None\n",
        "                if False:  # MUTANT PM4: no budget\n                    return None\n",
        [*g(16), "C16"],
    ),
    "PM5": (
        "pruned names are entered",
        GD,
        "PRUNED_DIRS = frozenset(\n",
        "PRUNED_DIRS = frozenset() and frozenset(  # MUTANT PM5: nothing pruned\n",
        g(9),
    ),
    "PM6": (
        "the mv destination is walked",
        GD,
        'dest_last=cmd == "mv")',
        "dest_last=False)  # MUTANT PM6",
        g(19),
    ),
    "PM7p": (
        "pathless git clean mapped to `.` (must not exist)",
        GD,
        '        if sub in ("checkout", "restore", "rm", "mv", "clean", "apply", "am", '
        '"revert"):\n',
        '        if sub == "clean" and not [a for a in rest if not a.startswith("-")]:\n'
        '            rest = [*rest, "."]  # MUTANT PM7p\n'
        '        if sub in ("checkout", "restore", "rm", "mv", "clean", "apply", "am", '
        '"revert"):\n',
        g(20, 22),
    ),
    "PM8p": (
        "git rm walked down (must not exist)",
        GD,
        'dirs=sub in ("checkout", "restore", "clean"))',
        'dirs=sub in ("checkout", "restore", "clean", "rm"))  # MUTANT PM8p',
        g(24),
    ),
    "PM9": (
        "non-first projects always local (today)",
        SV,
        "        if base is None:\n            return local\n",
        "        if True:  # MUTANT PM9: always local\n            return local\n",
        ["U1", "U2", "U6"],
    ),
    "PM10": (
        "matched by the hub's name instead of the path",
        SV,
        "        name = studio_name_of(listing, p.root) if listing is not None else None\n",
        "        name = (  # MUTANT PM10: by name\n"
        "            p.name if listing is not None and any(\n"
        '                isinstance(r, dict) and r.get("name") == p.name for r in listing\n'
        "            ) else None\n"
        "        )\n",
        ["U1", "U2", "U6"],
    ),
    "PM11": (
        "/p/<name> not stripped from the base",
        SV,
        '    return re.sub(r"/p/[^/]+$", "", url.rstrip("/"))\n',
        '    return url.rstrip("/")  # MUTANT PM11\n',
        ["U1"],
    ),
    "PM12": (
        "a 404 listing is an error (no local fallback)",
        SV,
        "        if e.code == 404:\n            return None\n",
        "        if False:  # MUTANT PM12\n            return None\n",
        ["U3"],
    ),
    "PM13": (
        "a failed listing is cached as a local box",
        SV,
        "        listing = studio_listing(base)\n",
        "        try:  # MUTANT PM13\n"
        "            listing = studio_listing(base)\n"
        "        except ToolError:\n"
        "            listing = None\n",
        ["U4"],
    ),
    "PM14": (
        "the listing is fetched with one project",
        SV,
        "    base = studio_base(studio_url) if studio_url else None\n",
        "    base = studio_base(studio_url) if studio_url else None\n"
        "    if base is not None:  # MUTANT PM14\n"
        "        try:\n"
        "            studio_listing(base)\n"
        "        except ToolError:\n"
        "            pass\n",
        ["U5"],
    ),
    "PM15": (
        "the fallback manifest is accepted without declaring the project",
        SV,
        "            elif found.by_path(explicit[0]) is None:\n",
        "            elif False:  # MUTANT PM15\n",
        ["P5"],
    ),
    "PM16": (
        "no fallback (launch walk-up only)",
        SV,
        "    if ws_path is None and explicit:\n",
        "    if False:  # MUTANT PM16\n",
        ["P4"],
    ),
    "PM17": (
        "change_propose of a served non-first project is routed to that Studio",
        SV,
        "        out = self.local.new_change(body)\n",
        "        out = self.http.new_change(body)  # MUTANT PM17\n",
        ["U1"],
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache")
    for rel in ("lattice", "tests", "dogfood", "lattice-claude-plugin"):
        shutil.copytree(ROOT / rel, dst / rel, ignore=ignore)


def apply(dst: Path, mid: str) -> None:
    _what, rel, old, new, _pred = MUTANTS[mid]
    path = dst / rel
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{mid}: the patched text occurs {n} times in {rel} (must be once)")
    path.write_text(text.replace(old, new), encoding="utf-8")


def _env(dst: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "CLAUDE_CODE"))}
    env.pop("LATTICE_WORKSPACE", None)
    env["PYTHONPATH"] = str(dst)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_suite(dst: Path, old: Path) -> dict[str, object]:
    t0 = time.monotonic()
    env = _env(dst)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/mcp", "-q", "-p", "no:cacheprovider", "-rf",
             "--no-header"],
            cwd=str(dst), env=env, capture_output=True, text=True, timeout=1200, check=False,
        )  # fmt: skip
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = str(e.stdout or "") + "\n[timeout after 1200s]", 124
    failed = sorted({m.group(1) for m in re.finditer(r"^FAILED (\S+)", out, re.M)})
    summary = next(
        (ln for ln in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed)", ln)), ""
    ).strip()
    scratch = dst / "probe-scratch"
    scratch.mkdir()
    try:
        p = subprocess.run(
            [sys.executable, "dogfood/multi_plumbing_probes.py", "--old", str(old),
             "--scratch", str(scratch), "--runs", "1", "--only", ",".join(PROBES)],
            cwd=str(dst), env=env, capture_output=True, text=True, timeout=1200, check=False,
        )  # fmt: skip
        pout, prc = p.stdout, p.returncode
    except subprocess.TimeoutExpired as e:
        pout, prc = str(e.stdout or "") + "\n[timeout]", 124
    probes_failed = sorted(set(re.findall(r"^(\S+) \|.*\| FAIL$", pout, re.M)))
    last = pout.strip().splitlines()[-1] if pout.strip() else "(no output)"
    return {
        "pytest_exit": rc, "pytest_failed": failed, "pytest_summary": summary,
        "probe_exit": prc, "probes_failed": probes_failed, "probe_last_line": last,
        "seconds": round(time.monotonic() - t0, 1),
        "tail": out[-1500:] + "\n--- probes ---\n" + pout[-1500:],
    }  # fmt: skip


def one(mid: str, old: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"lattice-plumbing-mutant-{mid}-") as d:
        dst = Path(d)
        copy_tree(dst)
        if mid != "control":
            apply(dst, mid)
        res = run_suite(dst, old)
    named = bool(res["pytest_failed"]) or bool(res["probes_failed"])
    caught = res["pytest_exit"] != 0 or res["probe_exit"] != 0 or named
    # a patched copy that does not import or collect fails with no named test or probe: that is
    # an invalid mutant, not a catch
    broken = caught and not named
    if mid == "control":
        verdict = "PASS (control)" if not caught else "CONTROL FAILED"
    elif broken:
        verdict = "INVALID (no named failure)"
    else:
        verdict = "caught" if caught else "SURVIVED"
    return {"suite": res, "verdict": verdict}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", default=list(MUTANTS))
    ap.add_argument(
        "--old", type=Path, required=True, help="git archive a4ab4a1 lattice, extracted"
    )
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--jobs", type=int, default=2)
    args = ap.parse_args(argv)
    old = args.old.resolve()
    hashes = {f: sha(ROOT / f) for f in FILES}
    print("hashes:", json.dumps(hashes), flush=True)
    with tempfile.TemporaryDirectory() as d:  # every patch applies cleanly before anything runs
        copy_tree(Path(d))
        for mid in args.ids:
            if mid not in MUTANTS:
                raise SystemExit(f"unknown mutant {mid}")
            apply(Path(d), mid)
    results: dict[str, object] = {"hashes": hashes, "runs": {}}
    runs: dict[str, object] = results["runs"]  # type: ignore[assignment]
    control = one("control", old)
    runs["control"] = control
    suite = control["suite"]
    print(
        f"control: {control['verdict']} (pytest: {suite['pytest_summary']}; "  # type: ignore[index]
        f"probes: {suite['probe_last_line']})",  # type: ignore[index]
        flush=True,
    )
    ok = control["verdict"] == "PASS (control)"
    killed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(one, mid, old): mid for mid in args.ids}
        for fut in concurrent.futures.as_completed(futs):
            mid = futs[fut]
            res = fut.result()
            runs[mid] = res
            s = res["suite"]  # type: ignore[index]
            predicted = MUTANTS[mid][4]
            got = set(s["probes_failed"]) | {  # type: ignore[index]
                f"test:{f.split('::')[-1]}"
                for f in s["pytest_failed"]  # type: ignore[index]
            }
            missed = [p for p in predicted if p not in got]
            extra = sorted(x for x in got if x not in predicted)
            res["predicted"] = predicted
            res["missed_predicted"] = missed
            killed += res["verdict"] == "caught"
            print(
                f"{mid} | {MUTANTS[mid][0]} | predicted {','.join(predicted)} | "
                f"{res['verdict']} | probes failed: {','.join(s['probes_failed']) or '-'} | "  # type: ignore[index]
                f"tests failed: {len(s['pytest_failed'])} | missed predicted: "  # type: ignore[index]
                f"{','.join(missed) or '-'}",
                flush=True,
            )
            if extra:
                print(f"    also failed: {', '.join(extra)}", flush=True)
            ok = ok and res["verdict"] == "caught"
    if args.out:
        args.out.write_text(json.dumps(results, indent=1, sort_keys=True))
    print(f"mutants: {killed}/{len(args.ids)} killed")
    return 0 if ok and killed == len(args.ids) else 1


if __name__ == "__main__":
    sys.exit(main())
