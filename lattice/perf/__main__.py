"""``python -m lattice.perf measure|check``: statements per request (and latency) of the
endpoints a project's ``perf.budget.json`` names, against their budgets (spike 1a).

    python -m lattice.perf measure --project DIR [--budget FILE] [--json] [--sizes 5,25]
                                   [--runs 10] [--id ID ...] [--statements]
    python -m lattice.perf check   (the same flags)

The app is started through the project's harness (``DIR/studio.toml``, as ``lattice check``
does), on a throwaway database per size, seeded through its API (``lattice.perf.measure``).
``--budget`` defaults to ``DIR/perf.budget.json``; ``--sizes`` overrides every budget's
sizes; ``--runs`` is the measured requests per size; ``--id`` measures only those budgets;
``--statements`` adds each size's statement shapes (first run) to the report.

Exit codes: ``measure`` 0 when every budget was measured, 2 otherwise; ``check`` 0 when every
budget passes, 1 when one is exceeded, 2 when the run cannot happen or a budget could not be
measured.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from lattice.perf.budget import BUDGET_FILE, BudgetError, load, overall_verdict, render


def _sizes(text: str) -> list[int]:
    try:
        out = [int(x) for x in text.split(",") if x.strip()]
    except ValueError as err:
        msg = f"--sizes: comma-separated integers, not {text!r}"
        raise argparse.ArgumentTypeError(msg) from err
    if any(n < 0 for n in out):
        raise argparse.ArgumentTypeError("--sizes: non-negative integers")
    return out


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="lattice perf", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name, what in (
        ("measure", "measure and report (exit 0 when measured)"),
        ("check", "measure and compare with the budgets (exit 1 over budget)"),
    ):
        p = sub.add_parser(name, help=what)
        p.add_argument("--project", default=".", help="the project directory (studio.toml)")
        p.add_argument("--budget", help=f"the budget file (default DIR/{BUDGET_FILE})")
        p.add_argument("--json", action="store_true", help="print the reports as JSON")
        p.add_argument("--sizes", type=_sizes, help="seed sizes, e.g. 5,25 (overrides the file)")
        p.add_argument("--runs", type=int, default=10, help="measured requests per size")
        p.add_argument("--id", action="append", dest="ids", help="only this budget (repeatable)")
        p.add_argument(
            "--statements",
            action="store_true",
            help="add the statement shapes of each size's first run",
        )
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 2)
    from lattice.perf.measure import MeasureError, harness_from_project, measure

    project = Path(args.project).resolve()
    budget_path = Path(args.budget).resolve() if args.budget else project / BUDGET_FILE
    try:
        budgets = load(budget_path)
        harness = harness_from_project(project)
        reports = measure(budgets, harness, sizes=args.sizes, runs=args.runs, ids=args.ids,
                          want_shapes=args.statements)  # fmt: skip
    except (BudgetError, MeasureError) as err:
        print(f"lattice perf: {err}", file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 -- anything else that stops the run is exit 2
        print(f"lattice perf: error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    verdicts = [r.verdict for r in reports]
    overall = overall_verdict(verdicts)
    if args.json:
        print(json.dumps({"version": 1, "command": args.command, "project": str(project),
                          "budget_file": str(budget_path), "verdict": overall,
                          "reports": [r.to_dict() for r in reports]}, indent=1))  # fmt: skip
    else:
        print(render(reports))
    if args.command == "measure":
        return 2 if "unmeasured" in verdicts else 0
    return {"pass": 0, "fail": 1}.get(overall, 2)


if __name__ == "__main__":
    sys.exit(main())
