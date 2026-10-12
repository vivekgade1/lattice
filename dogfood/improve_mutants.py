"""The mutation run of spike 3 (``lattice.improve`` and Studio's improvement signals),
pre-registered with ``docs/results/2026-10-12-spike3-prereg.md``.

Modelled on ``dogfood/security_mutants.py``: for each mutant, ``lattice/``, ``tests/``,
``dogfood/`` and ``docs/results/data/streak-plans-2026-10-11`` are copied to a scratch
directory and the mutant's edit is applied to the copy (its old text must occur exactly once
in the CURRENT source), then the suite runs there with the copy first on ``PYTHONPATH``. A
mutant is CAUGHT when the suite fails. A control run of the unpatched copy comes first and must
pass. The working tree is never patched.

The six mutants and the tests predicted to catch each:

* IM1 the ranker converts dollars into seconds (one cost unit)
      -> test_analyze: test_the_i_r_arithmetic_and_class_order,
         test_seconds_are_never_converted_to_dollars
* IM2 closed items are still ranked
      -> test_analyze: test_closed_items_are_left_out...; test_import: test_the_i_r_replay;
         test_cli: test_ledger_closes_an_item; test_events (studio): the mock's shape
* IM3 a protected target is not marked needs_owner
      -> test_analyze: test_i4_a_checker_proposal_needs_the_owner; test_cli: the I-4 run;
         test_events (studio): the mock's shape
* IM4 a torn last line crashes the reader
      -> test_events (improve): test_a_torn_last_line..., test_bad_middle_lines...
* IM5 a node's cause is always ``other``
      -> test_events (studio): test_b_cause_on_a_give_up, test_b_cause_on_an_owner_stop,
         test_the_ranker_reads_the_live_log
* IM6 ``layer_seconds`` copied from the wrong layer
      -> test_events (studio): test_a_layer_seconds_copied_from_the_report

Usage: ``uv run python dogfood/improve_mutants.py [--out FILE] [--jobs N] [ID ...]``.
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
SUITE = ["tests/improve", "tests/studio/test_events.py"]
AN, EV, RB = "lattice/improve/analyze.py", "lattice/improve/events.py", "lattice/studio/rebuild.py"
FILES = [AN, EV, RB, "lattice/improve/signals.py", "lattice/studio/plan.py",
         "dogfood/improve_mutants.py"]  # fmt: skip

# id -> (behaviour, file, old text, new text)
MUTANTS: dict[str, tuple[str, str, str, str]] = {
    "IM1": (
        "the ranker converts dollars into seconds (one cost unit, $1 = 3600 s)",
        AN,
        "    @property\n"
        "    def score_class(self) -> str:\n"
        '        if self._mean("seconds") is not None:\n'
        '            return "A"\n'
        '        if self._mean("usd") is not None:\n'
        '            return "B"\n'
        '        return "C"\n'
        "\n"
        "    @property\n"
        "    def unit(self) -> str | None:\n"
        '        return {"A": "s", "B": "usd", "C": None}[self.score_class]\n'
        "\n"
        "    @property\n"
        "    def cost_per(self) -> float | None:\n"
        "        cls = self.score_class\n"
        '        return self._mean("seconds") if cls == "A" '
        'else self._mean("usd") if cls == "B" else None\n',
        "    @property\n"
        "    def score_class(self) -> str:  # MUTANT IM1: one unit\n"
        '        if self._mean("seconds") is not None or self._mean("usd") is not None:\n'
        '            return "A"\n'
        '        return "C"\n'
        "\n"
        "    @property\n"
        "    def unit(self) -> str | None:\n"
        '        return {"A": "s", "C": None}[self.score_class]\n'
        "\n"
        "    @property\n"
        "    def cost_per(self) -> float | None:\n"
        '        s, d = self._mean("seconds"), self._mean("usd")\n'
        "        return s if s is not None else (d * 3600 if d is not None else None)\n",
    ),
    "IM2": (
        "closed items are still ranked",
        AN,
        "    open_ = [c for c in cl if c.closed is None]\n",
        "    open_ = list(cl)  # MUTANT IM2: closed items still ranked\n",
    ),
    "IM3": (
        "a protected target is not marked needs_owner",
        AN,
        '    status = "needs_owner" if protected else "open"\n',
        '    status = "open"  # MUTANT IM3: protected targets not refused\n',
    ),
    "IM4": (
        "a torn last line crashes the reader",
        EV,
        "        try:\n"
        '            ev = json.loads(raw.decode("utf-8"))\n'
        "        except (UnicodeDecodeError, json.JSONDecodeError):\n"
        "            ev = None\n",
        '        ev = json.loads(raw.decode("utf-8"))  # MUTANT IM4: no tolerance\n',
    ),
    "IM5": (
        "a node's cause is always other",
        RB,
        "        cause = sig.build_cause(rec, stopped_by=run.stopped_by)\n",
        '        cause = None if rec.get("status") == "passed" else "other"  # MUTANT IM5\n',
    ),
    "IM6": (
        "layer_seconds copied from the wrong layer (each value shifted to the next layer)",
        RB,
        "    out = {str(k): float(v) for k, v in layers.items()\n"
        "           if isinstance(v, int | float) and not isinstance(v, bool)}  # fmt: skip\n",
        "    _ks, _vs = list(layers), list(layers.values())  # MUTANT IM6: shifted layers\n"
        "    out = {str(k): float(v) for k, v in zip(_ks, _vs[1:] + _vs[:1])\n"
        "           if isinstance(v, int | float) and not isinstance(v, bool)}  # fmt: skip\n",
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def copy_tree(dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache")
    for rel in ("lattice", "tests", "dogfood", "docs/results/data/streak-plans-2026-10-11"):
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
    with tempfile.TemporaryDirectory(prefix=f"lattice-imp-mutant-{mid}-") as d:
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
