"""The mutation run of spike 1a (``lattice.perf``), pre-registered in
``docs/results/2026-10-12-spike1a-prereg.md``.

Modelled on ``dogfood/studio/sync_mutants.py``: for each mutant, ``lattice/`` and ``tests/`` are
copied to a scratch directory and the mutant's edit is applied to the copy (its old text must
occur exactly once), then (a) ``tests/perf`` runs there with the copy first on ``PYTHONPATH``
and, with ``--streak DIR`` (a Streak checkout with its harness, e.g. a clone at ``build/streak``),
(b) two real ``python -m lattice.perf check`` runs of that project with the copy's lattice: the
clean budgets (``GET /v1/home`` 6 + 1*N, ``GET /v1/habits`` 4 + 0*N) must exit 0 with exactly
those fits, and a too-tight budget (``GET /v1/home`` 8 + 0*N) must exit 1. A mutant is caught
when either fails. A control run of the unpatched copy comes first. The working tree is never
patched.

PR1-PR9 (2026-10-13, ``docs/results/2026-10-13-perf-rule-prereg.md`` section 4) mutate the judging
rule "a budget's constant may rise when its per-item term falls" (``judge`` in budget.py); each
has the tests it was predicted to be caught by (``PREDICTED``), reported next to the ones that
did fail. The equivalent mutants PE1 and PE2 are not here: they survive by construction and are
checked on the grid by ``dogfood/perf_rule_probes.py`` (J2e).

Usage: ``uv run python dogfood/perf_mutants.py [--out FILE] [--jobs N] [--streak DIR
[--python PY]] [ID ...]`` (``--python``: an interpreter with PyMySQL for the Streak runs).
Prints a table and ends ``mutants: N/N killed``; exit 0 when the control passes and every
mutant is caught.
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
SUITE = ["tests/perf"]
B, M, C = "lattice/perf/budget.py", "lattice/perf/measure.py", "lattice/perf/__main__.py"
FILES = [B, M, C, "tests/perf/test_budget.py", "tests/perf/test_measure.py",
         "dogfood/perf_mutants.py"]  # fmt: skip

# id -> (behaviour, file, old text, new text)
MUTANTS: dict[str, tuple[str, str, str, str]] = {
    "PM1": (
        "the fit takes the larger size's count as the constant",
        B,
        "    constant = c_lo - per_item * lo\n",
        "    constant = Fraction(c_hi)  # MUTANT PM1\n",
    ),
    "PM2": (
        "per_item compared with >= instead of >",
        B,
        "    if f.per_item > budget.per_item:\n        failures.append(",
        "    if f.per_item >= budget.per_item:  # MUTANT PM2\n        failures.append(",
    ),
    "PM3": (
        "counting includes the tool's own statements",
        M,
        "        if int(thread) in tool_threads:\n            continue\n",
        "        if False and int(thread) in tool_threads:  # MUTANT PM3\n            continue\n",
    ),
    "PM4": (
        "a failing budget exits 0",
        C,
        '    return {"pass": 0, "fail": 1}.get(overall, 2)\n',
        '    return {"pass": 0, "fail": 0}.get(overall, 2)  # MUTANT PM4\n',
    ),
    "PR1": (
        "a trade needs no size within its allowed count (the within conjunct dropped)",
        B,
        "    trade = f.constant > budget.constant and f.per_item < budget.per_item and within\n",
        "    trade = f.constant > budget.constant and f.per_item < budget.per_item  # MUTANT PR1\n",
    ),
    "PR2": (
        "the constant failure is never recorded",
        B,
        "    if f.constant > budget.constant and not trade:\n",
        "    if False:  # MUTANT PR2\n",
    ),
    "PR3": (
        "the per_item check never fires (p' > p allowed)",
        B,
        "    if f.per_item > budget.per_item:\n",
        "    if False and f.per_item > budget.per_item:  # MUTANT PR3\n",
    ),
    "PR4": (
        "the trade note is not appended",
        B,
        '        report.notes.append(\n            f"constant traded for per-item: constant '
        '{_show(f.constant)} > budget "\n'
        '            f"{_show(budget.constant)} accepted (per_item {_show(f.per_item)} < budget "\n'
        '            f"{_show(budget.per_item)}, every measured size within its allowed count); "\n'
        '            f"at or under the budget for N >= N* = {n_star}, over it for N < {n_star}"\n'
        "        )\n",
        "        pass  # MUTANT PR4\n",
    ),
    "PR5": (
        "the constant failure ignores trade (the verdict as before the trade)",
        B,
        "    if f.constant > budget.constant and not trade:\n",
        "    if f.constant > budget.constant:  # MUTANT PR5\n",
    ),
    "PR6": (
        "N* uses the budget's per_item as the denominator",
        B,
        "/ (budget.per_item - f.per_item))\n",
        "/ budget.per_item)  # MUTANT PR6\n",
    ),
    "PR7": (
        "within uses < instead of <=",
        B,
        "max(s.statements) <= budget.allowed(s.n) for s in sizes)\n",
        "max(s.statements) < budget.allowed(s.n) for s in sizes)  # MUTANT PR7\n",
    ),
    "PR8": (
        "a trade excuses a latency failure",
        B,
        '    if failures:\n        report.verdict = "fail"\n',
        '    if failures and not trade:  # MUTANT PR8\n        report.verdict = "fail"\n',
    ),
    "PR9": (
        "the no-trade note is not appended",
        B,
        "        report.notes.append(\n"
        '            f"no trade: per_item {_show(f.per_item)} < budget {_show(budget.per_item)}, '
        'but a "\n'
        '            f"measured size is over its allowed count, so constant {_show(f.constant)} '
        '> "\n'
        '            f"budget {_show(budget.constant)} stays over budget"\n'
        "        )\n",
        "        pass  # MUTANT PR9\n",
    ),
}

# the tests each PR mutant was predicted to be caught by (prereg section 4); PM1-PM4: none named
_RULE = "test_trading_constant_for_per_item_passes_and_shows_the_crossover"
_NO_TRADE = "test_no_trade_when_a_size_is_over_its_allowed_count"
_FRACTION = "test_trade_shows_a_fractional_crossover"
_BOUNDARY = "test_trade_at_the_boundary_size_passes"
_LATENCY = "test_trade_does_not_excuse_latency"
_FALLING = "test_falling_count_trades_against_a_zero_per_item_budget"
PREDICTED: dict[str, list[str]] = {
    "PR1": [_NO_TRADE],
    "PR2": [
        "test_constant_over_budget_fails",
        _NO_TRADE,
        "test_constant_over_with_equal_per_item_still_fails",
    ],
    "PR3": ["test_reverse_trade_is_refused", "test_per_item_over_budget_fails"],
    "PR4": [_RULE, _FRACTION, _BOUNDARY, _LATENCY, _FALLING],
    "PR5": [_RULE, _FRACTION, _BOUNDARY, _FALLING, _LATENCY],
    "PR6": [_FRACTION, _FALLING],
    "PR7": [_BOUNDARY, _FALLING],
    "PR8": [_LATENCY],
    "PR9": [_NO_TRADE],
}

SEED = {"items": "N", "steps": [{
    "method": "POST", "path": "/v1/habits", "user": "ann", "count": "N",
    "json": {"id": "{uuid}", "name": "habit-{i}", "period": "week", "target_count": 3,
             "start_date": "2026-03-02"}}]}  # fmt: skip


def _budget(bid: str, endpoint: str, constant: int, per_item: int) -> dict[str, object]:
    queries = {"constant": constant, "per_item": per_item}
    return {"id": bid, "endpoint": endpoint, "user": "ann", "seed": SEED, "sizes": [5, 25],
            "queries": queries, "latency_ms": None}  # fmt: skip


STREAK_CLEAN = {"version": 1, "budgets": [_budget("home", "GET /v1/home", 6, 1),
                                          _budget("habits", "GET /v1/habits", 4, 0)]}  # fmt: skip
STREAK_TIGHT = {"version": 1, "budgets": [_budget("home", "GET /v1/home", 8, 0)]}
CLEAN_FITS = {"home": {"constant": 6, "per_item": 1, "exact": True},
              "habits": {"constant": 4, "per_item": 0, "exact": True}}  # fmt: skip


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
            "seconds": round(time.monotonic() - t0, 1), "tail": out[-2500:]}  # fmt: skip


def _perf(dst: Path, python: str, project: Path, budget: dict) -> tuple[int, dict | None, str]:
    bf = dst / f"budget-{hashlib.sha1(json.dumps(budget).encode()).hexdigest()[:8]}.json"
    bf.write_text(json.dumps(budget))
    r = subprocess.run(
        [python, "-m", "lattice.perf", "check", "--project", str(project), "--budget", str(bf),
         "--runs", "2", "--json"],
        cwd=str(dst), env=_env(dst), capture_output=True, text=True, timeout=900,
    )  # fmt: skip
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        data = None
    return r.returncode, data, (r.stderr or "")[-1500:]


def run_streak(dst: Path, python: str, project: Path) -> dict[str, object]:
    t0 = time.monotonic()
    problems: list[str] = []
    rc, data, err = _perf(dst, python, project, STREAK_CLEAN)
    fits = {r["budget_id"]: r["fit"] for r in (data or {}).get("reports", [])}
    counts = {r["budget_id"]: [s["statements"] for s in r["sizes"]]
              for r in (data or {}).get("reports", [])}  # fmt: skip
    if rc != 0:
        problems.append(f"clean budgets exited {rc} (expected 0) {err.strip()[:300]}")
    if fits != CLEAN_FITS:
        problems.append(f"clean fits {fits} (expected {CLEAN_FITS})")
    rc2, data2, err2 = _perf(dst, python, project, STREAK_TIGHT)
    if rc2 != 1:
        problems.append(f"tight budget exited {rc2} (expected 1) {err2.strip()[:300]}")
    return {"exit": 1 if problems else 0, "problems": problems, "clean_exit": rc,
            "clean_fits": fits, "clean_counts": counts, "tight_exit": rc2,
            "seconds": round(time.monotonic() - t0, 1)}  # fmt: skip


def one(mid: str, streak: Path | None, python: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"lattice-perf1a-mutant-{mid}-") as d:
        dst = Path(d)
        copy_tree(dst)
        if mid != "control":
            apply(dst, mid)
        res: dict[str, object] = {"pytest": run_pytest(dst)}
        if streak is not None:
            res["streak"] = run_streak(dst, python, streak)
    caught_by = [k for k, v in res.items() if v["exit"] != 0]  # type: ignore[index]
    if mid == "control":
        verdict = "PASS (control)" if not caught_by else "CONTROL FAILED"
    else:
        verdict = "caught" if caught_by else "SURVIVED"
    return {**res, "caught_by": caught_by, "verdict": verdict}


def print_table(
    control: dict[str, object], rows: dict[str, dict[str, object]], ids: list[str]
) -> None:
    """One line per mutant: verdict, what caught it, the failed tests and whether every
    predicted killer is among them (extra failures are not a miss)."""
    print(f"control | {control['verdict']} | pytest: {control['pytest']['summary']}")  # type: ignore[index]
    print("id | behaviour | verdict | caught by | failed tests | predicted killers")
    for mid in ids:
        res = rows.get(mid)
        if res is None:
            continue
        failed = [f.split("::")[-1] for f in res["pytest"]["failed"]]  # type: ignore[index]
        want = PREDICTED.get(mid, [])
        missing = [t for t in want if t not in failed]
        pred = "-" if not want else ("MISSING " + ", ".join(missing) if missing else "all present")
        print(f"{mid} | {MUTANTS[mid][0]} | {res['verdict']} | "
              f"{', '.join(res['caught_by']) or 'nothing'} | "
              f"{len(failed)}: {', '.join(failed)} | {pred}")  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", default=list(MUTANTS))
    ap.add_argument("--out", type=Path, default=None, help="write the results JSON here")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--streak", type=Path, default=None, help="a Streak project to measure")
    ap.add_argument("--python", default=sys.executable, help="interpreter for the Streak runs")
    args = ap.parse_args(argv)
    hashes = {f: sha(ROOT / f) for f in FILES}
    print("hashes:", json.dumps(hashes), flush=True)
    for mid in args.ids:  # every patch applies, each to a fresh copy, before anything runs
        if mid not in MUTANTS:
            raise SystemExit(f"unknown mutant {mid}")
        with tempfile.TemporaryDirectory() as d:
            copy_tree(Path(d))
            apply(Path(d), mid)
    streak = args.streak.resolve() if args.streak else None
    results: dict[str, object] = {"hashes": hashes, "streak": str(streak), "runs": {}}
    control = one("control", streak, args.python)
    results["runs"]["control"] = control  # type: ignore[index]
    print(f"control: {control['verdict']}", flush=True)
    ok = control["verdict"] == "PASS (control)"
    rows: dict[str, dict[str, object]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(one, mid, streak, args.python): mid for mid in args.ids}
        for fut in concurrent.futures.as_completed(futs):
            mid = futs[fut]
            res = fut.result()
            results["runs"][mid] = res  # type: ignore[index]
            failed = res["pytest"]["failed"]  # type: ignore[index]
            print(f"{mid}: {res['verdict']} (by {', '.join(res['caught_by']) or 'nothing'}; "
                  f"pytest failed {len(failed)}: {', '.join(f.split('::')[-1] for f in failed)})",
                  flush=True)  # fmt: skip
            ok = ok and res["verdict"] == "caught"
            rows[mid] = res
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    print_table(control, rows, list(args.ids))
    killed = sum(1 for r in rows.values() if r["verdict"] == "caught")
    print(f"mutants: {killed}/{len(args.ids)} killed", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
