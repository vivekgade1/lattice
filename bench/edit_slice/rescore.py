"""Re-score saved edit-slice rounds under the current oracle (N4, benchmark hygiene:
``docs/superpowers/specs/2026-09-27-lattice-surface-design.md`` §5).

Round 3 (``docs/results/2026-09-26-bench-smoke.md``) marked every ``t14_email_change``
and ``t17_page_size_preference`` run silent on cases the ticket text never determined
(:data:`bench.edit_slice.oracle.EXCLUDED_CASES`): unstated bounds on
``current_password`` (t14) and an unstated missing-field behaviour (t17). Those cases
are dropped from the oracle now, so a run that failed *only* on them should be rescored
without spending another model call: this module reconstructs each run's saved
**final** candidate -- the files the harness already wrote to its artefact directory
(``bench.edit_slice.run``: ``<root>/<model>/<arm>/<ticket>/rep<N>/round<k>/files/``, the
highest ``round<k>`` present is the one that was scored) -- and re-evaluates it with
:func:`bench.edit_slice.oracle.evaluate`.

A record with no artefacts on disk (a run without ``artefact_root``, or a final round
that had no runnable candidate: a refusal, or an answer with no usable files) passes
through unchanged -- there is nothing to rebuild, and its original outcome already
reflects that (loud, for lack of a candidate).

    python -m bench.edit_slice.rescore RESULTS.jsonl [RESULTS.jsonl ...]
        [--tickets t14_email_change,t17_page_size_preference] [--out OUT.jsonl]

Each ``RESULTS.jsonl`` is read alongside its artefacts directory, named the way
:func:`bench.edit_slice.run.main` names it (``<path>.artefacts``) -- the record's own
``artefacts`` field already points inside it, so nothing further is assumed about the
JSONL's location. ``--tickets`` defaults to the tickets the current oracle excludes
cases for (t14, t17); records naming another ticket pass through unchanged. ``--out``,
given once across every input file, receives every record (rescored or passed through,
in the order read); without it, only the per-record summary and a final tally print.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from bench.edit_slice.oracle import EXCLUDED_CASES, evaluate
from bench.edit_slice.tickets import ALL_TICKETS, ticket
from bench.edit_slice.variant import load_variant

AppFactory = Any


def _read_files(root: Path) -> dict[str, str]:
    """Every ``.py`` file under ``root``, keyed by its path relative to it (posix)."""
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*.py"))}


def _final_round_files(rep_dir: Path) -> Path | None:
    """The final round's ``files/`` directory (the highest ``round<k>`` present, the one
    the harness scored), or None when there is nothing to rebuild."""
    rounds = sorted(
        rep_dir.glob("round*"),
        key=lambda p: int(p.name.removeprefix("round")),
    )
    if not rounds:
        return None
    files_dir = rounds[-1] / "files"
    return files_dir if files_dir.is_dir() else None


def _lattice_factory(repo: str, files: Mapping[str, str]) -> tuple[AppFactory, Any]:
    """Arm L's candidate app factory rebuilt from its saved programs + profile, and a
    cleanup callable for the temporary emit directory."""
    from bench.edit_slice.prompts import PROFILE_FILE
    from bench.edit_slice.run import _LatticeArm, emit_lattice
    from lattice.emit.thin import make_emitted_app

    # any ticket on this repo gives the same lifted-route set (the importer's own
    # output, not the ticket's edit); the first one found stands in for it
    t = next(t for t in ALL_TICKETS if t.repo == repo)
    lifted = _LatticeArm(t).lifted()
    programs = {n: s for n, s in files.items() if n != PROFILE_FILE}
    profile_src = files[PROFILE_FILE]
    root = Path(tempfile.mkdtemp(prefix="rescore-emit-"))
    out = root / "emitted"
    emit_lattice(repo, programs, profile_src, out, lifted)

    def cleanup() -> None:
        shutil.rmtree(root, ignore_errors=True)

    return (lambda path: make_emitted_app(out, path)), cleanup


PYTHON_ARMS = frozenset({"P", "Q", "Q+", "S", "S+", "S*"})


def _candidate_factory(
    arm: str, ticket_id: str, files: Mapping[str, str]
) -> tuple[AppFactory, Any]:
    """(app factory, cleanup) rebuilt from a round's saved ``files/``."""
    t = ticket(ticket_id)
    if arm in PYTHON_ARMS:  # P, Q, Q+, S, S+, S* edit the Python app; L, L+ edit Lattice
        return load_variant(dict(files), repo=t.repo).make_app, (lambda: None)
    return _lattice_factory(t.repo, files)


def rescore_record(record: Mapping[str, Any], tickets: set[str]) -> tuple[dict[str, Any], str]:
    """(record, a one-line note). ``record`` is a plain copy of the input, rescored in
    place when its ticket is selected and its final round's candidate is on disk."""
    out = dict(record)
    tid, arm = record.get("ticket"), record.get("arm")
    if tid not in tickets:
        return out, "not selected"
    artefacts = record.get("artefacts")
    if not artefacts:
        return out, "no artefacts saved: unchanged"
    files_dir = _final_round_files(Path(artefacts))
    if files_dir is None:
        return out, "final round has no candidate: unchanged"
    files = _read_files(files_dir)
    factory, cleanup = _candidate_factory(arm, tid, files)
    try:
        outcome = evaluate(tid, factory)
    finally:
        cleanup()
    old_outcome = record.get("outcome")
    out["outcome"] = outcome.kind
    out["cases"] = dict(sorted(outcome.tally().items()))
    out["cases_generated"], out["cases_specific"] = outcome.counts()
    out["failed_cases"] = sorted(
        n for n, v in outcome.cases.items() if v.verdict in ("silent", "loud")
    )
    if outcome.kind == old_outcome:
        return out, f"{old_outcome} (unchanged)"
    return out, f"{old_outcome} -> {outcome.kind}"


def rescore_file(path: Path, tickets: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """(rescored records, one report line per record) of one JSONL file."""
    records, lines = [], []
    for raw in path.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        record = json.loads(raw)
        new, note = rescore_record(record, tickets)
        records.append(new)
        lines.append(f"{path.name}: {record.get('ticket')} {record.get('arm')} "
                     f"rep{record.get('rep')}: {note}")  # fmt: skip
    return records, lines


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m bench.edit_slice.rescore", description=__doc__)
    p.add_argument("jsonl", nargs="+", help="edit-slice results JSONL file(s)")
    p.add_argument("--tickets", default=",".join(sorted(EXCLUDED_CASES)),
                   help="comma-separated ticket ids to rescore (default: the tickets "
                        "the oracle currently excludes cases for)")  # fmt: skip
    p.add_argument("--out", default=None, help="write every record (rescored or not) here")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    tickets = {t.strip() for t in args.tickets.split(",") if t.strip()}
    all_records: list[dict[str, Any]] = []
    changed = 0
    considered = 0
    for arg in args.jsonl:
        path = Path(arg)
        records, lines = rescore_file(path, tickets)
        for record, line in zip(records, lines, strict=True):
            print(line, file=sys.stderr)
            if record.get("ticket") in tickets:
                considered += 1
                if "->" in line:
                    changed += 1
        all_records += records
    print(f"{considered} record(s) considered, {changed} outcome(s) changed", file=sys.stderr)
    if args.out:
        with open(args.out, "w") as f:
            for record in all_records:
                f.write(json.dumps(record) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
