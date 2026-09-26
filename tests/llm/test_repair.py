from __future__ import annotations

from bench.datasets import bench_profile
from lattice.llm.client import ScriptedGenerator
from lattice.llm.repair import SynthesisResult, synthesize

_SYSTEM = "SYSTEM-PROMPT-MARKER: rules and catalog go here."

_VALID = (
    "# lattice: v1\n"
    "from lattice.ops import col, load, where, write\n"
    "from lattice.schemas import Orders\n\n"
    'orders = load("orders.csv", schema=Orders)\n'
    'paid = where(orders, col("status") == "paid")\n'
    'write(paid, "out.parquet")\n'
)

# Missing the required "# lattice: v1" header line -> L001, an error.
_INVALID_NO_HEADER = (
    "from lattice.ops import col, load, where, write\n"
    "from lattice.schemas import Orders\n\n"
    'orders = load("orders.csv", schema=Orders)\n'
    'paid = where(orders, col("status") == "paid")\n'
    'write(paid, "out.parquet")\n'
)

_NO_CODE_TEXT = "Sorry, I can't help with that."


def _fenced(code: str) -> str:
    return f"```python\n{code}\n```\n"


def test_first_shot_valid_program_is_one_round() -> None:
    generator = ScriptedGenerator([_fenced(_VALID)])

    result = synthesize(
        "do the thing", bench_profile(), generator, system=_SYSTEM, max_repairs=2
    )

    assert isinstance(result, SynthesisResult)
    assert result.ok is True
    assert len(result.rounds) == 1
    assert result.rounds[0].index == 1
    assert result.source is not None
    assert result.source.strip() == _VALID.strip()


def test_invalid_then_valid_is_two_rounds_and_repair_message_has_diagnostics() -> None:
    generator = ScriptedGenerator([_fenced(_INVALID_NO_HEADER), _fenced(_VALID)])

    result = synthesize(
        "do the thing", bench_profile(), generator, system=_SYSTEM, max_repairs=2
    )

    assert result.ok is True
    assert len(result.rounds) == 2
    assert result.rounds[0].index == 1
    assert result.rounds[1].index == 2
    assert any(d.code == "L001" for d in result.rounds[0].diagnostics)

    # The second call's messages must include the round-1 diagnostics.
    second_call_messages = generator.calls[1][1]
    joined = " ".join(m["content"] for m in second_call_messages)
    assert "L001" in joined


def test_three_invalid_rounds_exhausts_repairs() -> None:
    invalid = _fenced(_INVALID_NO_HEADER)
    generator = ScriptedGenerator([invalid, invalid, invalid])

    result = synthesize(
        "do the thing", bench_profile(), generator, system=_SYSTEM, max_repairs=2
    )

    assert result.ok is False
    assert len(result.rounds) == 3
    assert result.check is not None
    assert result.check.ok is False


def test_no_code_block_then_valid_reports_l000_in_round_one() -> None:
    generator = ScriptedGenerator([_NO_CODE_TEXT, _fenced(_VALID)])

    result = synthesize(
        "do the thing", bench_profile(), generator, system=_SYSTEM, max_repairs=2
    )

    assert result.ok is True
    assert len(result.rounds) == 2
    assert result.rounds[0].source is None
    assert [d.code for d in result.rounds[0].diagnostics] == ["L000"]


def test_repair_messages_never_include_the_system_prompt() -> None:
    generator = ScriptedGenerator([_fenced(_INVALID_NO_HEADER), _fenced(_VALID)])

    synthesize("do the thing", bench_profile(), generator, system=_SYSTEM, max_repairs=2)

    for system_arg, messages in generator.calls:
        assert system_arg == _SYSTEM
        for message in messages:
            assert _SYSTEM not in message["content"]


# --- Regression test for review finding minor 7 ---------------------------


class _RefusesOnCall:
    """Returns scripted generations, raising GenerationRefused on call ``refuse_at``."""

    def __init__(self, texts: list[str], refuse_at: int) -> None:
        self._texts = texts
        self._refuse_at = refuse_at
        self._n = 0

    def generate(self, system, messages):  # type: ignore[no-untyped-def]
        from lattice.llm.client import Generation, GenerationRefused, Usage

        self._n += 1
        if self._n == self._refuse_at:
            raise GenerationRefused("refused", usage=Usage(5, 1, 7, 0), model="m-refuse")
        text = self._texts[self._n - 1]
        return Generation(text, Usage(100, 10, 1000, 0), 0.5, "end_turn", model="m-ok")


def test_refusal_in_repair_round_keeps_earlier_rounds() -> None:
    import pytest

    from lattice.llm.client import GenerationRefused
    from lattice.llm.repair import SynthesisRefused

    generator = _RefusesOnCall([_fenced(_INVALID_NO_HEADER)], refuse_at=2)

    with pytest.raises(SynthesisRefused) as info:
        synthesize("do the thing", bench_profile(), generator, system=_SYSTEM, max_repairs=2)

    exc = info.value
    assert isinstance(exc, GenerationRefused)
    assert len(exc.rounds) == 1
    assert exc.rounds[0].generation.usage.output_tokens == 10
    assert exc.usage.cache_read_input_tokens == 7
    assert exc.model == "m-refuse"
