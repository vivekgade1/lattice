"""Step 2 prompt additions: L420/L421/L506 guidance, registry-aware dataset descriptions."""

from __future__ import annotations

import sys
import types

import polars as pl
import pytest

from bench.datasets import Dataset, bench_profile
from bench.tasks import Task
from lattice.check import check_source
from lattice.llm.prompt import (
    BASELINE_SYSTEM,
    baseline_task_message,
    dataset_description,
    lattice_system_prompt,
    lattice_task_message,
)
from lattice.profile import Profile
from lattice.typesys import Scalar, Schema

PRICES = Schema.from_pairs(
    [("product_id", Scalar.INT), ("valid_from", Scalar.DATE), ("price", Scalar.FLOAT)],
    keys=[("product_id", "valid_from")],
    nullable=["price"],
)
CUSTOMERS2 = Schema.from_pairs(
    [("customer_id", Scalar.INT), ("zip", Scalar.STR), ("region", Scalar.STR)],
    keys=[("customer_id",)],
    nullable=["zip", "region"],
)
FAKE_DATASETS2 = {
    "product_prices": Dataset("product_prices", "product_prices.csv", "ProductPrices", PRICES),
    "customers": Dataset("customers", "customers.csv", "Customers2", CUSTOMERS2),
}
PROFILE2 = Profile(
    schemas={"ProductPrices": PRICES, "Customers2": CUSTOMERS2},
    outputs=frozenset({"out.parquet"}),
)


@pytest.fixture
def fake_bench2(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("bench.bench2_datasets")
    module.DATASETS2 = FAKE_DATASETS2  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "bench.bench2_datasets", module)


def _task(inputs: tuple[str, ...], datasets: str = "bench2") -> Task:
    return Task(
        id="t1",
        tier="trap",
        prompt="Do the thing.",
        inputs=inputs,
        reference=lambda frames: pl.DataFrame(),
        datasets=datasets,
    )


# ---- system prompts ------------------------------------------------------------------


def test_lattice_prompt_has_new_rules() -> None:
    prompt = lattice_system_prompt()
    assert 'fanout="allow"' in prompt
    assert 'group(prices, by=["product_id"]' in prompt
    assert 'nulls="skip"' in prompt
    assert 'agg.mean(coalesce(col("x"), 0.0))' in prompt
    assert "Write only the output file named in the task" in prompt
    assert "missing key as their own group" in prompt
    assert "fanout=... (optional)" in prompt  # rendered from the catalog


def test_baseline_prompt_has_equivalent_rules() -> None:
    assert 'validate="many_to_one"' in BASELINE_SYSTEM
    assert "dropna=False" in BASELINE_SYSTEM
    assert "fillna(0.0)` before aggregating" in BASELINE_SYSTEM
    assert 'dtype={"zip": str}' in BASELINE_SYSTEM
    assert "Write only `out.parquet`: no other files, no network access, no shell" in (
        BASELINE_SYSTEM
    )


def test_prompts_are_deterministic_and_within_budget() -> None:
    assert lattice_system_prompt() == lattice_system_prompt()
    n = len(lattice_system_prompt())
    assert 0.7 * n <= len(BASELINE_SYSTEM) <= 1.3 * n


def test_lattice_rule_examples_check_clean() -> None:
    # the dedupe-first idiom and both null policies from the rules are valid programs
    src = (
        "# lattice: v1\n"
        'prices = load("product_prices.csv", schema=ProductPrices)\n'
        'latest = group(prices, by=["product_id"], price=agg.max(col("price"), nulls="skip"), '
        'm=agg.mean(coalesce(col("price"), 0.0)))\n'
        'write(latest, "out.parquet")\n'
    )
    result = check_source(src, PROFILE2)
    assert result.ok and not result.diagnostics, [d.render() for d in result.diagnostics]


# ---- dataset descriptions --------------------------------------------------------------


def test_bench_descriptions_unchanged() -> None:
    assert dataset_description("products") == (
        "products.csv — columns: product_id (Int), product_name (Str), category (Str), "
        "cost (Float)"
    )
    assert dataset_description("orders", "bench") == dataset_description("orders")
    assert "unique key" not in dataset_description("orders")
    assert "may be missing" not in dataset_description("customers")


def test_bench2_description_shows_keys_and_nullable(fake_bench2: None) -> None:
    assert dataset_description("product_prices", "bench2") == (
        "product_prices.csv — columns: product_id (Int), valid_from (Date), price (Float)"
        "; unique key: (product_id, valid_from); may be missing: price"
    )
    # nullable columns are listed in schema order
    assert dataset_description("customers", "bench2").endswith(
        "; unique key: (customer_id); may be missing: zip, region"
    )


def test_unknown_registry_raises() -> None:
    with pytest.raises(ValueError):
        dataset_description("orders", "nope")


# ---- task messages ------------------------------------------------------------------------


def test_lattice_task_message_bench2(fake_bench2: None) -> None:
    message = lattice_task_message(_task(("product_prices", "customers")), PROFILE2)
    assert "unique key: (product_id, valid_from)" in message
    assert "may be missing: zip, region" in message
    assert 'load("product_prices.csv", schema=ProductPrices)' in message
    assert 'write(<table>, "out.parquet")' in message
    assert "This task may only write: out.parquet." in message


def test_baseline_task_message_bench2(fake_bench2: None) -> None:
    message = baseline_task_message(_task(("product_prices",)))
    assert "unique key: (product_id, valid_from)" in message
    assert 'pd.read_csv("product_prices.csv", parse_dates=["valid_from"])' in message
    assert "out.parquet with index=False. Write no other file." in message


def test_both_arms_list_same_inputs(fake_bench2: None) -> None:
    task = _task(("product_prices", "customers"))
    lat = lattice_task_message(task, PROFILE2)
    base = baseline_task_message(task)
    for name in task.inputs:
        assert dataset_description(name, "bench2") in lat
        assert dataset_description(name, "bench2") in base


def test_bench_task_message_uses_bench_registry() -> None:
    task = _task(("orders",), datasets="bench")
    message = lattice_task_message(task, bench_profile())
    assert 'load("orders.csv", schema=Orders)' in message
    assert "This task may only write" not in message  # bench_profile() is unrestricted
    assert "Write no other file." in baseline_task_message(task)


def test_real_bench2_registry_if_available() -> None:
    try:
        from bench.bench2_datasets import DATASETS2
    except ImportError:
        pytest.skip("bench2 datasets not available yet")
    for name, ds in DATASETS2.items():
        desc = dataset_description(name, "bench2")
        assert desc.startswith(ds.filename)
        if ds.schema.keys:
            assert "; unique key: " in desc
        if ds.schema.nullable:
            assert "; may be missing: " in desc
