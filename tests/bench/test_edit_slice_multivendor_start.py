"""Arm L's start state on the multi-vendor app (option 1's main experiment,
docs/superpowers/specs/2026-09-29-option1-main-experiment.md): the importer's committed
output (``bench/imports/multivendor``, from ``python -m lattice.importer ... --backend
cli``, never edited by hand), emitted into the original app with the route-preserving
merge (the import's verified routes are Lattice, its islands stay the original code).

Before any run, that app must answer exactly as the original does on every existing
route (:func:`bench.edit_slice.repos.multivendor.fidelity_cases`): the JustNotes import
passed its own generated suite and was still wrong on requests the suite never made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.edit_slice import oracle, run
from bench.edit_slice.repos import multivendor as MV
from bench.edit_slice.tickets import OPTION1B_TICKETS
from lattice.emit.thin import app_routes, make_emitted_app
from lattice.importer.verify import verify


def _start_factory(tmp_path: Path):
    programs, profile = run.start_lattice("multivendor")
    arm = run._LatticeArm(OPTION1B_TICKETS[0])
    emitted = run.emit_lattice("multivendor", programs, profile, tmp_path / "out", arm.lifted())
    original = app_routes(run.APP_FACTORIES["multivendor"])
    assert len(emitted.routes_replaced) + len(emitted.routes_kept) == len(original)
    return lambda db: make_emitted_app(tmp_path / "out", db)


def test_arm_l_starts_from_the_committed_import():
    out = run.import_dir("multivendor")
    programs, profile = run.start_lattice("multivendor")
    assert run.has_lattice_start("multivendor")
    assert profile == (out / "profile.py").read_text()
    assert programs, "the import verified no route"


def test_the_fidelity_cases_are_deterministic_on_the_original():
    factory = run.APP_FACTORIES["multivendor"]
    from bench.import_repos.multivendor import make_app

    result = verify(make_app, make_app, MV.db_factory, MV.fidelity_cases(), oracle.RULES, {},
                    login=MV.login, hash_columns=("hashed_password",))  # fmt: skip
    assert factory
    assert all(o.kind == "equal" for o in result.outcomes.values())
    assert len(result.outcomes) >= 80


def test_the_start_state_answers_as_the_original(tmp_path: Path):
    from bench.import_repos.multivendor import make_app

    start = _start_factory(tmp_path)
    result = verify(make_app, start, MV.db_factory, MV.fidelity_cases(), oracle.RULES, {},
                    login=MV.login, hash_columns=("hashed_password",))  # fmt: skip
    differing = {}
    for name, o in result.outcomes.items():
        if o.kind in ("equal", "equal_under_rule"):  # e.g. FastAPI's 422 envelope
            continue
        if MV.canon(result.original[name]) == MV.canon(result.lattice[name]):
            continue  # fresh timestamps / the optimistic-lock version only
        differing[name] = (o.reason, o.diff.render() if o.diff else "")
    assert not differing, "\n".join(f"{n}: {r}\n{d}" for n, (r, d) in differing.items())


@pytest.mark.parametrize("tid", [t.id for t in OPTION1B_TICKETS])
def test_each_ticket_touches_only_new_routes(tid):
    """t39-t44 add routes; arm L writes them as new Lattice endpoints."""
    assert set(run.lift_status(tid).values()) == {"new"}
