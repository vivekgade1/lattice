from __future__ import annotations

from pathlib import Path

from bench.datasets import load_fixtures, write_fixtures
from bench.runners import run_baseline_repair
from bench.seed_tasks import SEED_TASKS
from lattice.llm.client import ScriptedGenerator

_S01 = next(t for t in SEED_TASKS if t.id == "s01")


def _fenced(code: str) -> str:
    return f"```python\n{code}\n```\n"


_CORRECT_S01 = _fenced(
    "import pandas as pd\n\n"
    'orders = pd.read_csv("orders.csv", parse_dates=["order_date"])\n'
    'result = orders[orders["status"] == "paid"]\n'
    'result.to_parquet("out.parquet", index=False)\n'
)

_WRONG_S01 = _fenced(
    "import pandas as pd\n\n"
    'orders = pd.read_csv("orders.csv", parse_dates=["order_date"])\n'
    'result = orders[orders["status"] == "pending"]\n'
    'result.to_parquet("out.parquet", index=False)\n'
)

_CRASHING_S01 = _fenced("import pandas as pd\n\nraise RuntimeError('boom')\n")

_NO_CODE_TEXT = "Sorry, I can't help with that request."


def _setup(tmp_path: Path) -> tuple[Path, dict]:
    fixtures_dir = tmp_path / "fixtures"
    write_fixtures(fixtures_dir)
    workdir = tmp_path / "work"
    write_fixtures(workdir)  # deterministic; byte-identical to fixtures_dir
    fixtures_frames = load_fixtures(fixtures_dir)
    return workdir, fixtures_frames


def test_crash_then_correct_is_correct_with_two_rounds(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_CRASHING_S01, _CORRECT_S01])

    outcome = run_baseline_repair(_S01, generator, workdir, fixtures_frames, max_repairs=2)

    assert outcome.correct is True
    assert outcome.failure is None
    assert outcome.arm == "P"
    assert outcome.check_passed is None
    assert outcome.rounds == 2

    # The second request must carry the failure and stderr text back to the model.
    _, second_messages = generator.calls[1]
    second_user_text = second_messages[-1]["content"]
    assert "runtime_error" in second_user_text
    assert "boom" in second_user_text


def test_three_crashes_exhaust_the_repair_budget_as_loud(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_CRASHING_S01, _CRASHING_S01, _CRASHING_S01])

    outcome = run_baseline_repair(_S01, generator, workdir, fixtures_frames, max_repairs=2)

    assert outcome.correct is False
    assert outcome.failure == "runtime_error"
    assert outcome.rounds == 3
    assert len(generator.calls) == 3


def test_wrong_but_running_first_script_is_silent_with_no_second_request(
    tmp_path: Path,
) -> None:
    """Review Focus 5: a wrong_result must not trigger a repair round (no
    reference leakage to the model)."""
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_WRONG_S01])

    outcome = run_baseline_repair(_S01, generator, workdir, fixtures_frames, max_repairs=2)

    assert outcome.correct is False
    assert outcome.failure == "wrong_result"
    assert outcome.rounds == 1
    assert len(generator.calls) == 1


def test_no_code_then_correct_is_correct_with_two_rounds(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_NO_CODE_TEXT, _CORRECT_S01])

    outcome = run_baseline_repair(_S01, generator, workdir, fixtures_frames, max_repairs=2)

    assert outcome.correct is True
    assert outcome.rounds == 2


def test_correct_first_shot_is_correct_with_one_round(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_CORRECT_S01])

    outcome = run_baseline_repair(_S01, generator, workdir, fixtures_frames, max_repairs=2)

    assert outcome.correct is True
    assert outcome.rounds == 1
    assert len(generator.calls) == 1


def test_tokens_and_latency_are_summed_across_rounds(tmp_path: Path) -> None:
    from lattice.llm.client import Generation, Usage

    class _SequenceGenerator:
        def __init__(self, items: list[str]) -> None:
            self._items = items
            self._n = 0
            self.calls: list[tuple[str, list[dict]]] = []

        def generate(self, system, messages):  # type: ignore[no-untyped-def]
            self.calls.append((system, list(messages)))
            text = self._items[self._n]
            self._n += 1
            return Generation(text, Usage(10, 20, 300, 4000), 1.0, "end_turn", model="m")

    workdir, fixtures_frames = _setup(tmp_path)
    generator = _SequenceGenerator([_CRASHING_S01, _CORRECT_S01])

    outcome = run_baseline_repair(_S01, generator, workdir, fixtures_frames, max_repairs=2)

    assert outcome.correct is True
    assert outcome.rounds == 2
    assert outcome.output_tokens == 40
    assert outcome.input_tokens == 2 * 4310
    assert outcome.cache_read_tokens == 600
    assert outcome.model == "m"


def test_refusal_during_repair_is_a_refused_outcome(tmp_path: Path) -> None:
    from lattice.llm.client import GenerationRefused, Usage

    class _RefuseOnSecond:
        def __init__(self) -> None:
            self._n = 0
            self.calls: list[tuple[str, list[dict]]] = []

        def generate(self, system, messages):  # type: ignore[no-untyped-def]
            self.calls.append((system, list(messages)))
            self._n += 1
            if self._n == 1:
                from lattice.llm.client import Generation

                return Generation(_CRASHING_S01, Usage(0, 0), 0.0, "end_turn")
            raise GenerationRefused("declined", usage=Usage(1, 2), latency_s=0.1, model="m")

    workdir, fixtures_frames = _setup(tmp_path)
    generator = _RefuseOnSecond()

    outcome = run_baseline_repair(_S01, generator, workdir, fixtures_frames, max_repairs=2)

    assert outcome.correct is False
    assert outcome.failure == "refused"
    assert outcome.rounds == 2


def test_unsafe_is_recorded_on_the_final_scored_attempt(tmp_path: Path) -> None:
    leaking_script = "```python\n" + (
        "import pandas as pd\n\n"
        'orders = pd.read_csv("orders.csv", parse_dates=["order_date"])\n'
        'result = orders[orders["status"] == "paid"]\n'
        'result.to_parquet("out.parquet", index=False)\n'
        'with open("leak.csv", "w") as f:\n'
        '    f.write("leaked")\n'
    ) + "```\n"
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([leaking_script])

    outcome = run_baseline_repair(_S01, generator, workdir, fixtures_frames, max_repairs=2)

    assert outcome.correct is True
    assert any(u == "extra_file:leak.csv" for u in outcome.unsafe)
