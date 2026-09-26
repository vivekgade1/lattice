from __future__ import annotations

import re

from bench.datasets import bench_profile
from bench.tasks import Task
from lattice.catalog import FN_SIGS, OP_SHAPES
from lattice.check import check_source
from lattice.llm.prompt import lattice_system_prompt, lattice_task_message

_FENCE_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)


def _code_blocks(text: str) -> list[str]:
    return _FENCE_RE.findall(text)


def test_system_prompt_is_deterministic() -> None:
    assert lattice_system_prompt() == lattice_system_prompt()


def test_system_prompt_mentions_every_op() -> None:
    prompt = lattice_system_prompt()
    for op in OP_SHAPES:
        assert op in prompt, f"missing op {op!r}"


def test_system_prompt_mentions_every_fn() -> None:
    prompt = lattice_system_prompt()
    for name in FN_SIGS:
        assert f"fn.{name}(" in prompt, f"missing fn.{name}"


def test_system_prompt_has_exactly_three_examples_that_all_check_clean() -> None:
    prompt = lattice_system_prompt()
    blocks = _code_blocks(prompt)
    assert len(blocks) == 3

    profile = bench_profile()
    for block in blocks:
        result = check_source(block, profile)
        assert result.ok, [d.render() for d in result.diagnostics]
        assert not result.diagnostics


def test_system_prompt_states_output_format() -> None:
    prompt = lattice_system_prompt()
    assert "```python" in prompt
    assert "exactly one" in prompt.lower()


def _task(**overrides: object) -> Task:
    defaults: dict[str, object] = dict(
        id="t01",
        tier="single",
        prompt="Do the thing with the data.",
        inputs=("orders",),
        reference=lambda frames: frames["orders"],
        ordered=False,
    )
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


def test_task_message_includes_prompt_and_load_call_and_schema() -> None:
    message = lattice_task_message(_task(), bench_profile())

    assert "Do the thing with the data." in message
    assert 'load("orders.csv", schema=Orders)' in message
    assert "order_id (Int)" in message
    assert "unit_price (Float)" in message
    assert 'write(<table>, "out.parquet")' in message


def test_task_message_lists_every_input() -> None:
    message = lattice_task_message(
        _task(inputs=("orders", "customers")), bench_profile()
    )

    assert 'load("orders.csv", schema=Orders)' in message
    assert 'load("customers.csv", schema=Customers)' in message


def test_system_prompt_avoids_wording_that_triggered_refusals() -> None:
    # A live probe (claude-opus-5, 40 calls each) refused 8/40 with an intro describing
    # a "whitelisted subset" that is "legal" and "rejected ... before it ever runs",
    # and 0/40 with a neutral DSL description.
    text = lattice_system_prompt().lower()
    for phrase in ("whitelisted", "is legal", "before it ever runs", "will be rejected"):
        assert phrase not in text
