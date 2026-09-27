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
    assert 'agg.last(col("price"), order=col("valid_from"))' in prompt
    assert "latest = group(" not in prompt  # max price is not the latest price
    assert 'price=agg.max(col("price"))' not in prompt
    assert "unique by the task's meaning" in prompt
    assert "`agg.first(x, order=o)`" in prompt and "`agg.last(x, order=o)`" in prompt
    assert 'nulls="skip"' in prompt
    assert 'agg.mean(coalesce(col("x"), 0.0))' in prompt
    assert "Write only the output file named in the task" in prompt
    assert "missing key as their own group" in prompt
    assert "fanout=... (optional)" in prompt  # rendered from the catalog


def test_baseline_prompt_has_equivalent_rules() -> None:
    assert 'validate="many_to_one"' in BASELINE_SYSTEM
    assert "dropna=False" in BASELINE_SYSTEM
    assert "fillna(0.0)` before aggregating" in BASELINE_SYSTEM
    assert 'dtype={"zip": str}' not in BASELINE_SYSTEM  # no literal answer for a trap
    assert 'pd.read_csv("<file>.csv")' not in BASELINE_SYSTEM
    assert "must be read as text" in BASELINE_SYSTEM
    assert "dtype=str" in BASELINE_SYSTEM and "leading zeros" in BASELINE_SYSTEM
    assert "parse_dates" in BASELINE_SYSTEM
    assert 'keep="last"' in BASELINE_SYSTEM  # the pandas analogue of agg.last
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
        'current = group(prices, by=["product_id"], '
        'price=agg.last(col("price"), order=col("valid_from")), '
        'hi=agg.max(col("price"), nulls="skip"), m=agg.mean(coalesce(col("price"), 0.0)))\n'
        'write(current, "out.parquet")\n'
    )
    result = check_source(src, PROFILE2)
    assert result.ok and not result.diagnostics, [d.render() for d in result.diagnostics]


# ---- dataset descriptions --------------------------------------------------------------


def test_bench_descriptions_unchanged() -> None:
    assert dataset_description("products") == (
        "products.csv — columns: product_id (Int), product_name (Str), category (Str), cost (Float)"
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
    assert "read with" not in message and "read_csv" not in message
    assert "product_prices.csv — columns: product_id (Int), valid_from (Date)" in message
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


# ---- sample rows -----------------------------------------------------------------------


def _samples() -> dict[str, tuple[pl.DataFrame, int]]:
    import datetime

    prices = pl.DataFrame(
        {
            "product_id": [1, 1],
            "valid_from": [datetime.date(2024, 1, 1), datetime.date(2024, 3, 1)],
            "price": [9.5, None],
        }
    )
    customers = pl.DataFrame({"customer_id": [7], "zip": ["02134"], "region": [None]})
    return {"product_prices": (prices, 40), "customers": (customers, 3)}


def test_sample_rows_rendered_identically_in_both_arms(fake_bench2: None) -> None:
    task = _task(("product_prices", "customers"))
    lat = lattice_task_message(task, PROFILE2, samples=_samples())
    base = baseline_task_message(task, samples=_samples())
    section = (
        "Sample rows of product_prices.csv (first 2 of 40):\n"
        "product_id,valid_from,price\n"
        "1,2024-01-01,9.5\n"
        "1,2024-03-01,\n"
        "\n"
        "Sample rows of customers.csv (first 1 of 3):\n"
        "customer_id,zip,region\n"
        "7,02134,\n"
    )
    assert section in lat and section in base
    # the samples come after the input list and before the output contract
    for message in (lat, base):
        assert message.index("Input files") < message.index("Sample rows of")
        assert message.index("Sample rows of") < message.index("Write the final result")


def test_no_samples_by_default(fake_bench2: None) -> None:
    task = _task(("product_prices",))
    assert "Sample rows" not in lattice_task_message(task, PROFILE2)
    assert "Sample rows" not in baseline_task_message(task)
    assert lattice_task_message(task, PROFILE2, samples=None) == lattice_task_message(
        task, PROFILE2
    )


def test_samples_must_cover_every_input(fake_bench2: None) -> None:
    task = _task(("product_prices", "customers"))
    only_prices = {"product_prices": _samples()["product_prices"]}
    with pytest.raises(KeyError):
        baseline_task_message(task, samples=only_prices)
    with pytest.raises(KeyError):
        lattice_task_message(task, PROFILE2, samples=only_prices)


def test_both_messages_differ_only_in_loading_and_output_contract(fake_bench2: None) -> None:
    task = _task(("product_prices", "customers"))
    lat = lattice_task_message(task, PROFILE2, samples=_samples()).splitlines()
    base = baseline_task_message(task, samples=_samples()).splitlines()
    lat_only = [line for line in lat if line not in base]
    base_only = [line for line in base if line not in lat]
    assert lat_only == [
        '  load with: load("product_prices.csv", schema=ProductPrices)',
        '  load with: load("customers.csv", schema=Customers2)',
        'Write the final result to out.parquet with write(<table>, "out.parquet"). '
        "Write no other file.",
        "This task may only write: out.parquet.",
    ]
    assert base_only == [
        "Write the final result to out.parquet with index=False. Write no other file."
    ]
