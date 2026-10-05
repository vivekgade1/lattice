"""The POST-HOC held-out measurement (after the 2026-10-05 soundness fix round).

    python -m bench.heldout.posthoc.run_posthoc APP
    python -m bench.heldout.posthoc.run_posthoc APP --work bench/heldout/posthoc2  # round 2

The same approach as the frozen run's ``bench/heldout/run_exit.py``: the held-out
packages are added to ``bench.phase2_exit.APPS`` at run time, and the exit script's own
:func:`bench.phase2_exit.measure` and :func:`bench.phase2_exit.markdown` run unchanged --
but with the work directory ``bench/heldout/posthoc`` (the imports land in
``bench/heldout/posthoc/<app>/``) and the records in ``bench/heldout/posthoc/results/``,
so the frozen run's outputs (``bench/heldout/<app>/``, ``bench/heldout/results/``) are
never overwritten. A later post-hoc round passes a work directory of its own
(``--work bench/heldout/posthoc2``: the imports in ``<work>/<app>/``, the records in
``<work>/results/``), never overwriting an earlier round's.

This run is post-hoc: the importer was changed after its authors had seen the held-out
apps' results, so it does not count for the Phase 2 exit (a fresh held-out app does).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from bench import phase2_exit
from bench.heldout.run_exit import HELD_OUT

HERE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.heldout.posthoc.run_posthoc")
    p.add_argument("app", choices=sorted(HELD_OUT))
    # a later post-hoc round measures into a work directory of its own (the second
    # round: bench/heldout/posthoc2), never over an earlier round's outputs
    p.add_argument("--work", type=Path, default=HERE, help="where the imports go")
    p.add_argument("--out", type=Path, default=None, help="the records (<work>/results)")
    args = p.parse_args(argv)
    out = args.out or args.work / "results"
    phase2_exit.APPS.update(HELD_OUT)
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        result = phase2_exit.measure(args.app, args.work)
    except Exception:  # noqa: BLE001 -- recorded as the app's result
        result = {"app": args.app, "error": traceback.format_exc(), "import_s": None}
    wall = round(time.perf_counter() - started, 2)
    (out / f"{args.app}.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    (out / f"{args.app}.wall_s").write_text(f"{wall}\n")
    if "error" in result and result.get("import_s") is None:
        table = f"{args.app}: measurement raised; see {args.app}.json\n"
    else:
        table = phase2_exit.markdown([result])
    (out / f"{args.app}.md").write_text(table)
    print(table)
    print(f"wall {wall}s")
    return 1 if "error" in result or not result.get("agrees") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
