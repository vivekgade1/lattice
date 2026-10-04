"""Phase 0 exit: the multi-vendor spec, plus a ticket's delta, checked against an app.

`check_ticket(tid, files)` checks the multi-vendor app with ``files`` replaced (a ticket's
reference, or a trap variant of it) against ``apply(SPEC, DELTAS[tid])`` on all four
layers, with the original app's accepted baseline. A trap is *caught* when the report
fails; the reference of every ticket must pass. ``delta=`` checks against another delta
(Phase 1: arm S's approved draft, arm S*'s blind delta).

The seed database is built once per process and copied (:func:`seed_db_factory`): the
seed hashes eight passwords with bcrypt, which was most of a check's time.

`python -m bench.specs.exit [--workers N] [--out FILE]` runs every reference and every
trap (``TRAPS`` in ``tests/bench/test_edit_slice_option1.py``) and writes the per-layer
catch table as JSON.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import threading
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from bench.edit_slice.apps import repo
from bench.edit_slice.reference import reference_files
from bench.edit_slice.repos import multivendor
from bench.edit_slice.variant import load_variant
from bench.specs.multivendor import BASELINE, NATIVES, SPEC
from bench.specs.multivendor_tickets import DELTAS, SpecDelta, apply
from lattice.pycheck import materialize
from lattice.spec import Report, check

_SEED: dict[str, Path] = {}
_SEED_LOCK = threading.Lock()  # run.py's --workers runs checks on threads


def seed_db_factory(variant: str, path: Path) -> Path:
    """:func:`multivendor.db_factory`, with the seed built once per process and copied
    (the same rows and password hashes; a variant's writer runs on the copy)."""
    with _SEED_LOCK:
        if "seed" not in _SEED:
            cache = Path(tempfile.mkdtemp(prefix="lattice-exit-seed-")) / "seed.db"
            multivendor.db_factory("seed", cache)
            _SEED["seed"] = cache
    shutil.copyfile(_SEED["seed"], path)
    if variant != "seed":
        engine = multivendor.sqlite_engine(path)
        try:
            multivendor.WRITERS[variant](engine)
        finally:
            engine.dispose()
    return Path(path)


def check_ticket(
    tid: str, files: Mapping[str, str], *, layers: str = "ABCD", delta: SpecDelta | None = None
) -> Report:
    """The app with ``files`` replaced, checked against the spec plus ``tid``'s delta
    (``delta``: this one instead)."""
    spec = apply(SPEC, DELTAS[tid] if delta is None else delta)
    variant = load_variant(files, repo="multivendor")
    app = repo("multivendor")
    with tempfile.TemporaryDirectory(prefix="lattice-exit-") as tmp:
        app_dir = materialize(app.root, app.package, files, Path(tmp))
        return check(
            spec,
            variant.make_app,
            seed_db_factory,
            layers=layers,
            app_dir=app_dir,
            package=app.package,
            login=multivendor.login,
            mint_token=multivendor.mint,
            natives=NATIVES,
            baseline=BASELINE,
        )


def _traps() -> dict[str, tuple[str, Any]]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tests.bench.test_edit_slice_option1 import TRAPS

    return dict(TRAPS)


def _summary(name: str, tid: str, report: Report) -> dict[str, Any]:
    return {
        "name": name,
        "ticket": tid,
        "verdict": report.verdict,
        "counts": report.counts,
        "layers": sorted({f.layer for f in report.findings}),
        "findings": [f.key for f in report.findings][:20],
        "stale_baseline": [a.pattern for a in report.stale],
    }


def run_one(job: tuple[str, str]) -> dict[str, Any]:
    """``(name, tid)``: a trap name, or ``reference`` for the ticket's reference."""
    name, tid = job
    try:
        files = reference_files(tid) if name == "reference" else _traps()[name][1]()
        return _summary(name, tid, check_ticket(tid, files))
    except Exception as err:  # noqa: BLE001 -- reported per job
        return {"name": name, "ticket": tid, "verdict": "error", "error": f"{err!r}"[:500]}


def jobs() -> list[tuple[str, str]]:
    traps = _traps()
    tickets = sorted({tid for tid, _ in traps.values()})
    return [("reference", t) for t in tickets] + [(n, traps[n][0]) for n in sorted(traps)]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.specs.exit")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--out", default="bench/specs/phase0-exit.json")
    p.add_argument("--only", nargs="*", help="trap names or 'reference:<tid>'")
    args = p.parse_args(argv)
    todo = jobs()
    if args.only:
        todo = [j for j in todo if j[0] in args.only or f"{j[0]}:{j[1]}" in args.only]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run_one, todo))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2) + "\n")
    refs = [r for r in results if r["name"] == "reference"]
    traps = [r for r in results if r["name"] != "reference"]
    bad_refs = [r["ticket"] for r in refs if r["verdict"] != "pass"]
    missed = [r["name"] for r in traps if r["verdict"] != "fail"]
    print(f"references: {len(refs) - len(bad_refs)}/{len(refs)} pass; failing: {bad_refs}")
    print(f"traps: {len(traps) - len(missed)}/{len(traps)} caught; missed: {missed}")
    return 0 if not bad_refs and not missed else 1


if __name__ == "__main__":
    sys.exit(main())
