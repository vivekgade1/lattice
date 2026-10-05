"""The Phase 2 held-out run (plan 2026-10-05, Wave 4) through the frozen exit script.

    python -m bench.heldout.run_exit APP [--out bench/heldout/results]

``bench/phase2_exit.py`` (frozen) knows the dev apps only (its ``APPS``). This wrapper adds
the held-out packages to that dict at run time and calls the exit script's own
:func:`bench.phase2_exit.measure` and :func:`bench.phase2_exit.markdown`, unchanged. So, per
app and once:

1. the import, ``python -m lattice.importer <repo> --package <pkg> --rules-only --no-llm
   --no-corpus --out bench/heldout/<app>`` (``--work bench/heldout``), timed;
2. the recomputation of the verdicts from ``report.json["rules"]["items"]``;
3. the fidelity suite (``python -m lattice.spec fidelity``).

Writes ``<out>/<app>.json`` and ``<out>/<app>.md``, plus ``<out>/<app>.wall_s`` (the wall
time of the whole measurement). An exception in the measurement itself (e.g. a timeout)
is recorded as that app's result, with the traceback.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from bench import phase2_exit

HELD_OUT: dict[str, str] = {
    "drf_blog": "bench.import_repos.drf_blog",
    "projmgmt": "bench.import_repos.projmgmt",
    "conduit": "bench.import_repos.conduit",
}
HERE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.heldout.run_exit")
    p.add_argument("app", choices=sorted(HELD_OUT))
    p.add_argument("--out", type=Path, default=HERE / "results")
    args = p.parse_args(argv)
    phase2_exit.APPS.update(HELD_OUT)
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        result = phase2_exit.measure(args.app, HERE)
    except Exception:  # noqa: BLE001 -- recorded as the app's result
        result = {"app": args.app, "error": traceback.format_exc(), "import_s": None}
    wall = round(time.perf_counter() - started, 2)
    (args.out / f"{args.app}.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    (args.out / f"{args.app}.wall_s").write_text(f"{wall}\n")
    if "error" in result and result.get("import_s") is None:
        table = f"{args.app}: measurement raised; see {args.app}.json\n"
    else:
        table = phase2_exit.markdown([result])
    (args.out / f"{args.app}.md").write_text(table)
    print(table)
    print(f"wall {wall}s")
    return 1 if "error" in result or not result.get("agrees") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
