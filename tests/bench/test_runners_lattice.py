from __future__ import annotations

from pathlib import Path

from bench.datasets import load_fixtures, write_fixtures
from bench.runners import classify_failure, run_lattice
from bench.seed_tasks import SEED_TASKS
from lattice.diagnostics import Diagnostic, Severity, Span
from lattice.llm.client import ScriptedGenerator

_S01 = next(t for t in SEED_TASKS if t.id == "s01")

_CORRECT_S01 = (
    "```python\n"
    "# lattice: v1\n"
    "from lattice.ops import col, load, where, write\n"
    "from lattice.schemas import Orders\n\n"
    'orders = load("orders.csv", schema=Orders)\n'
    'paid = where(orders, col("status") == "paid")\n'
    'write(paid, "out.parquet")\n'
    "```\n"
)

# Rebinds the name "orders" -> L201 shadowing.
_REBINDING_S01 = (
    "```python\n"
    "# lattice: v1\n"
    "from lattice.ops import col, load, where, write\n"
    "from lattice.schemas import Orders\n\n"
    'orders = load("orders.csv", schema=Orders)\n'
    'orders = where(orders, col("status") == "paid")\n'
    'write(orders, "out.parquet")\n'
    "```\n"
)

_WRONG_S01 = (
    "```python\n"
    "# lattice: v1\n"
    "from lattice.ops import col, load, where, write\n"
    "from lattice.schemas import Orders\n\n"
    'orders = load("orders.csv", schema=Orders)\n'
    'pending = where(orders, col("status") == "pending")\n'
    'write(pending, "out.parquet")\n'
    "```\n"
)


def _setup(tmp_path: Path) -> tuple[Path, dict]:
    fixtures_dir = tmp_path / "fixtures"
    write_fixtures(fixtures_dir)
    workdir = tmp_path / "work"
    write_fixtures(workdir)  # deterministic; byte-identical to fixtures_dir
    fixtures_frames = load_fixtures(fixtures_dir)
    return workdir, fixtures_frames


def test_correct_first_shot_is_correct_with_one_round(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_CORRECT_S01])

    outcome = run_lattice(
        _S01, generator, workdir, fixtures_frames, arm="C", max_repairs=2
    )

    assert outcome.correct is True
    assert outcome.check_passed is True
    assert outcome.rounds == 1
    assert outcome.failure is None
    assert outcome.arm == "C"


def test_arm_c_never_repairs_and_classifies_shadowing(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_REBINDING_S01])

    outcome = run_lattice(
        _S01, generator, workdir, fixtures_frames, arm="C", max_repairs=2
    )

    assert outcome.correct is False
    assert outcome.check_passed is False
    assert outcome.rounds == 1
    assert outcome.failure == "shadowing"


def test_arm_b_repairs_shadowing_with_a_second_response(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_REBINDING_S01, _CORRECT_S01])

    outcome = run_lattice(
        _S01, generator, workdir, fixtures_frames, arm="B", max_repairs=2
    )

    assert outcome.correct is True
    assert outcome.check_passed is True
    assert outcome.rounds == 2
    assert outcome.failure is None


def test_wrong_result_after_passing_checks(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_WRONG_S01])

    outcome = run_lattice(
        _S01, generator, workdir, fixtures_frames, arm="B", max_repairs=2
    )

    assert outcome.correct is False
    assert outcome.check_passed is True
    assert outcome.failure == "wrong_result"


def test_no_code_block_is_no_code(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator(["I can't help with that."])

    outcome = run_lattice(
        _S01, generator, workdir, fixtures_frames, arm="C", max_repairs=0
    )

    assert outcome.correct is False
    assert outcome.check_passed is False
    assert outcome.failure == "no_code"


def test_classify_failure_maps_known_codes() -> None:
    def _diag(code: str) -> Diagnostic:
        return Diagnostic(code, Severity.ERROR, Span(1, 1), "msg")

    assert classify_failure([_diag("L201")]) == "shadowing"
    assert classify_failure([_diag("L202")]) == "sequencing"
    assert classify_failure([_diag("L604")]) == "sequencing"
    assert classify_failure([_diag("L301")]) == "chaining"
    assert classify_failure([_diag("L302")]) == "chaining"
    assert classify_failure([_diag("L401")]) == "schema"
    assert classify_failure([_diag("L419")]) == "schema"
    assert classify_failure([_diag("L000")]) == "no_code"
    assert classify_failure([_diag("L501")]) == "other_static"
    assert classify_failure([]) == "other_static"


# --- Regression tests for review finding minor 7 ---------------------------

from lattice.llm.client import Generation, GenerationRefused, Usage  # noqa: E402


class _SequenceGenerator:
    """Yields (text, model) generations with fixed usage; refuses on ``refuse_at``."""

    def __init__(self, items: list[tuple[str, str]], refuse_at: int | None = None) -> None:
        self._items = items
        self._refuse_at = refuse_at
        self._n = 0

    def generate(self, system, messages):  # type: ignore[no-untyped-def]
        self._n += 1
        if self._n == self._refuse_at:
            raise GenerationRefused("no", usage=Usage(1, 2, 3, 4), latency_s=0.5, model="m-r")
        text, model = self._items[self._n - 1]
        return Generation(text, Usage(10, 20, 300, 4000), 1.0, "end_turn", model=model)


def test_lattice_tokens_include_cache_and_models_are_recorded(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = _SequenceGenerator([(_REBINDING_S01, "m-a"), (_CORRECT_S01, "m-b")])

    outcome = run_lattice(_S01, generator, workdir, fixtures_frames, arm="B", max_repairs=2)

    assert outcome.correct is True
    assert outcome.rounds == 2
    assert outcome.input_tokens == 2 * 4310
    assert outcome.cache_read_tokens == 600
    assert outcome.output_tokens == 40
    assert outcome.model == "m-a,m-b"


def test_refusal_in_repair_round_keeps_earlier_round_tokens(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = _SequenceGenerator([(_REBINDING_S01, "m-a")], refuse_at=2)

    outcome = run_lattice(_S01, generator, workdir, fixtures_frames, arm="B", max_repairs=2)

    assert outcome.correct is False
    assert outcome.failure == "refused"
    assert outcome.check_passed is False
    assert outcome.rounds == 2
    assert outcome.output_tokens == 20 + 2
    assert outcome.input_tokens == 4310 + (1 + 3 + 4)
    assert outcome.cache_read_tokens == 300 + 3
    assert outcome.latency_s == 1.5
    assert outcome.model == "m-a,m-r"
