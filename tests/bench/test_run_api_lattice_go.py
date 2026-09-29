"""Arm B/C on the Go backend (5b pilot Task 5, bench.runners.run_api_lattice(backend="go")).

``backend="go"`` checks the program for the Go target, builds it with
``lattice.gogen.build.build_go_app`` (cached under the task's workdir), and runs the
task's hidden cases on the binary through ``bench.api_runner.run_cases_go`` (the same
harness code the Go equivalence gate uses, ``tests/gogen/test_go_differential.py``), so
status/body/SQL counts are measured identically to arm G. A checker rejection (natives
L890, or any other static error) is fed back for repair exactly like the runtime and
"generated" backends.

A failure later in the Lattice -> Go pipeline on an already-checked program -- ``go
build`` itself failing, or any other exception while lowering/emitting it -- is a bug
in gogen, not the model's program, but it still counts against arm B: it is scored as
a loud, final failure (``failure="loud"``), never fed back for repair (the model
cannot fix a gogen bug), and never a ``harness_error`` (which the pilot's analysis,
``bench.analysis.go_pilot``, excludes from cost and n -- excluding it here would
silently drop a real B failure and bias the cost comparison toward B). See
docs/superpowers/specs/2026-09-29-lattice-go-pilot.md, "Tests and decision", third
pre-freeze revision. A missing/too-old Go toolchain, by contrast, is a genuine harness
infrastructure failure and is left to propagate uncaught (never a silent skip).

Tests that build real Go binaries carry ``@pytest.mark.go`` individually (skipped
without the Go toolchain on PATH, tests/conftest.py); the mocked build-failure test
needs no toolchain and runs unconditionally.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bench.api_datasets import api_profile, build_db
from bench.api_tasks import API_TASKS
from bench.runners import run_api_lattice
from lattice.llm.client import ScriptedGenerator
from tests.api.test_codegen_differential import PROGRAMS, setup_of

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "api"
API_TASKS_BY_ID = {t.id: t for t in API_TASKS}

O01_SOURCE = (FIXTURES / "o01" / "lattice.py").read_text()
# The reference solution with its ownership filter (`mine = owned(orders, by=user)`)
# removed: `found` reads straight from the unfiltered `orders`, so any customer's
# order id returns another customer's row -- a real L802 checker rejection (an
# ownership check missing before the rows reach the caller), independent of the Go
# target (the same error on `check_source(..., target="python")`).
O01_BROKEN_OWNERSHIP = O01_SOURCE.replace(
    'mine = owned(orders, by=user)\n    found = where(mine, col("id") == order_id)',
    'found = where(orders, col("id") == order_id)',
)
assert O01_BROKEN_OWNERSHIP != O01_SOURCE  # the replace above actually matched


def _fenced(code: str) -> str:
    return f"```python\n{code}```\n"


@pytest.fixture(scope="module")
def pristine_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("go_backend_db") / "pristine.db"
    build_db(path)
    return path


@pytest.fixture
def db_builder(pristine_db: Path):
    def build(path: Path) -> None:
        shutil.copyfile(pristine_db, path)

    return build


@pytest.mark.go  # builds and runs a real Go binary (skipped without `go`)
def test_reference_program_scores_correct_on_the_go_backend(
    tmp_path: Path, db_builder
) -> None:
    """The hand-written o01 solution (tests/fixtures/api/o01/lattice.py) checks,
    builds and passes every case on the Go binary, exactly as it does on the
    runtime (tests/test_api_solutions.py)."""
    gen = ScriptedGenerator([_fenced(O01_SOURCE)])

    outcome = run_api_lattice(
        API_TASKS_BY_ID["o01"],
        gen,
        tmp_path / "w",
        db_builder=db_builder,
        profile=api_profile(),
        arm="C",
        max_repairs=0,
        backend="go",
    )

    assert outcome.correct is True
    assert outcome.check_passed is True
    assert outcome.rounds == 1
    assert outcome.failure is None
    assert outcome.detail == "backend=go"
    assert outcome.failed_cases == ()
    assert outcome.mean_queries > 0


@pytest.mark.go  # builds and runs a real Go binary (skipped without `go`)
def test_checker_error_triggers_a_repair_round_with_the_diagnostics(
    tmp_path: Path, db_builder
) -> None:
    """A real static checker error (L802, an ownership check the go target rejects
    exactly as the python target does) is fed back as checker feedback, and arm B
    repairs on the following round's corrected program."""
    gen = ScriptedGenerator([_fenced(O01_BROKEN_OWNERSHIP), _fenced(O01_SOURCE)])

    outcome = run_api_lattice(
        API_TASKS_BY_ID["o01"],
        gen,
        tmp_path / "w",
        db_builder=db_builder,
        profile=api_profile(),
        arm="B",
        max_repairs=2,
        backend="go",
    )

    assert outcome.correct is True
    assert outcome.rounds == 2
    assert outcome.check_passed is True
    assert outcome.diagnostics_per_round == (("L802",), ())
    feedback = gen.calls[1][1][-1]["content"]
    assert feedback.startswith("The program failed Lattice checks:")
    assert "L802" in feedback
    assert feedback.endswith("Return the complete corrected program in one ```python block.")


@pytest.mark.go  # builds a real Go binary (skipped without `go`; rejected before it runs)
def test_native_program_is_rejected_with_l890_feedback(tmp_path: Path) -> None:
    """A program that calls a native (here: NativeToken auth, decode_token) cannot
    run on Go: the checker rejects it with L890, fed back as repair feedback exactly
    like any other static error, on every round of the budget."""
    source, task = PROGRAMS["realapps"]
    setup = setup_of("realapps")
    gen = ScriptedGenerator([_fenced(source), _fenced(source)])

    outcome = run_api_lattice(
        task,
        gen,
        tmp_path / "w",
        db_builder=setup.build,
        profile=setup.profile,
        arm="B",
        max_repairs=1,
        backend="go",
    )

    assert outcome.correct is False
    assert outcome.check_passed is False
    assert outcome.failure == "static"
    assert "L890" in outcome.detail
    assert all("L890" in codes for codes in outcome.diagnostics_per_round)
    feedback = gen.calls[1][1][-1]["content"]
    assert feedback.startswith("The program failed Lattice checks:")
    assert "L890" in feedback


def test_gogen_build_failure_is_a_loud_scored_failure_not_a_repair(
    tmp_path: Path, db_builder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GoBuildError with no ``check`` set -- go build itself failing on code that
    already passed the checker, simulating a gogen bug -- is a loud, final,
    *scored* failure of arm B: not fed back for repair (only one response is
    queued -- ScriptedGenerator raises IndexError if a second round is attempted --
    even though the budget has rounds left), and never a harness_error (the
    controller ruling: excluding it from cost/n as a harness error would silently
    drop a real B failure and bias the pilot's cost comparison toward B).

    No Go toolchain is needed: build_go_app is monkeypatched before it would ever
    invoke `go`, so this test runs unconditionally (review nit: it must not be
    skipped when Go is absent, since it is pure Python-level behavior)."""
    import lattice.gogen.build as gobuild

    def _boom(source: str, profile: object, out_dir: Path) -> None:
        raise gobuild.GoBuildError("go build failed:\nsome fake compiler stderr")

    monkeypatch.setattr(gobuild, "build_go_app", _boom)
    monkeypatch.setattr(gobuild, "check_toolchain", lambda: None)  # toolchain is fine
    gen = ScriptedGenerator([_fenced(O01_SOURCE)])  # only one response: proves no repair

    outcome = run_api_lattice(
        API_TASKS_BY_ID["o01"],
        gen,
        tmp_path / "w",
        db_builder=db_builder,
        profile=api_profile(),
        arm="B",
        max_repairs=2,  # budget left, but a gogen bug is not repairable
        backend="go",
    )

    assert outcome.correct is False
    assert outcome.check_passed is True  # the program passed the go checker
    assert outcome.failure == "loud"
    assert outcome.rounds == 1  # no repair round spent on a gogen bug
    assert len(gen.calls) == 1
    assert "gogen_build:" in outcome.detail
    assert "some fake compiler stderr" in outcome.detail


def test_missing_go_toolchain_is_still_a_harness_error(
    tmp_path: Path, db_builder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike a gogen build failure, a missing/too-old Go toolchain is a genuine
    harness infrastructure failure (the run environment isn't set up): it
    propagates out of run_api_lattice uncaught, so the caller (bench.cli's
    run_task) records it as a harness_error, not a loud arm-B failure."""
    import lattice.gogen.build as gobuild

    def _no_go() -> None:
        raise gobuild.GoBuildError("the go toolchain is not installed (go not on PATH): ...")

    monkeypatch.setattr(gobuild, "check_toolchain", _no_go)
    gen = ScriptedGenerator([_fenced(O01_SOURCE)])

    with pytest.raises(gobuild.GoBuildError) as exc_info:
        run_api_lattice(
            API_TASKS_BY_ID["o01"],
            gen,
            tmp_path / "w",
            db_builder=db_builder,
            profile=api_profile(),
            arm="C",
            max_repairs=0,
            backend="go",
        )
    assert exc_info.value.check is None
    assert "not installed" in str(exc_info.value)
