from __future__ import annotations

from bench.tasks import Task
from lattice.llm.prompt import BASELINE_SYSTEM, baseline_task_message, dataset_description


def test_baseline_system_mentions_pandas_and_output_contract() -> None:
    assert "pandas" in BASELINE_SYSTEM
    assert "out.parquet" in BASELINE_SYSTEM
    assert "index=False" in BASELINE_SYSTEM
    assert "```python" in BASELINE_SYSTEM


def test_dataset_description_orders() -> None:
    desc = dataset_description("orders")
    assert desc.startswith("orders.csv")
    assert "order_id (Int)" in desc
    assert "unit_price (Float)" in desc
    assert "order_date (Date)" in desc
    assert "discount (Float)" in desc


def test_dataset_description_customers_and_products() -> None:
    assert dataset_description("customers").startswith("customers.csv")
    assert "region (Str)" in dataset_description("customers")
    assert dataset_description("products").startswith("products.csv")
    assert "cost (Float)" in dataset_description("products")


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


def test_baseline_task_message_includes_prompt_and_input_and_output_contract() -> None:
    message = baseline_task_message(_task())

    assert "Do the thing with the data." in message
    assert "orders.csv" in message
    assert "out.parquet" in message


def test_baseline_task_message_lists_every_input() -> None:
    message = baseline_task_message(_task(inputs=("orders", "customers")))

    assert "orders.csv" in message
    assert "customers.csv" in message


def test_baseline_task_message_never_mentions_lattice_ops() -> None:
    message = baseline_task_message(_task())

    assert "load(" not in message
    assert "lattice" not in message.lower()
