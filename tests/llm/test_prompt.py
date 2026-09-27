from __future__ import annotations

import re

from bench.datasets import bench_profile
from bench.tasks import Task
from lattice.catalog import API_ONLY_FNS, FN_SIGS, OP_SHAPES
from lattice.check import check_source
from lattice.llm.prompt import lattice_system_prompt, lattice_task_message
from lattice.profile import Profile

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
        if name in API_ONLY_FNS:
            continue  # API programs only (tests/check/test_realapps_checker.py)
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


def test_examples_use_no_imports() -> None:
    for block in _code_blocks(lattice_system_prompt()):
        assert "import" not in block


def test_rules_state_ambient_names_and_derive_overwrite() -> None:
    prompt = lattice_system_prompt()
    assert "No imports are needed" in prompt
    assert "input schema names are always available" in prompt
    assert "may overwrite an existing column" in prompt
    assert "read the input table `t`" in prompt
    # the old import-bookkeeping rule is gone
    assert "no unused" not in prompt and "exactly match the names used" not in prompt


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


def test_every_fn_has_a_semantic_description_in_the_prompt() -> None:
    from lattice.catalog import FN_DOCS, FN_SIGS

    assert set(FN_DOCS) == set(FN_SIGS)
    text = lattice_system_prompt()
    for name, doc in FN_DOCS.items():
        if name in API_ONLY_FNS:
            continue  # API programs only
        assert doc in text, name


def test_date_diff_documents_argument_order_and_unit() -> None:
    from lattice.catalog import FN_DOCS

    doc = FN_DOCS["date_diff"]
    assert "end" in doc and "start" in doc and '"days"' in doc


def test_rules_state_count_cross_join_and_constants() -> None:
    prompt = lattice_system_prompt()
    assert "`agg.count(x)` the non-null values of x" in prompt
    assert 'join(l, r, on=[], how="inner")' in prompt
    assert "at most one row" in prompt
    assert "named constant" in prompt


def test_rule_snippets_check_clean() -> None:
    """The inline share-of-total and constant snippets in the rules are valid Lattice."""
    prompt = lattice_system_prompt()
    statements = [
        'cutoff = fn.to_date("2025-01-01", format="%Y-%m-%d")',
        'total = group(t, by=[], all_qty=agg.sum(col("qty")))',
        'with_total = join(t, total, on=[], how="inner")',
        'shares = derive(with_total, share=col("qty") / col("all_qty"))',
    ]
    for stmt in statements:
        assert stmt.replace("cutoff = ", "") in prompt.replace("\n", " ") or stmt in prompt
    source = (
        "# lattice: v1\n"
        't = load("orders.csv", schema=Orders)\n'
        + "\n".join(statements)
        + '\nrecent = where(shares, col("order_date") >= cutoff)\n'
        'write(recent, "out.parquet")\n'
    )
    result = check_source(source, bench_profile())
    assert result.ok, [d.render() for d in result.diagnostics]
    assert not result.diagnostics


def test_prompts_document_joins_on_differently_named_keys() -> None:
    from lattice.llm.api_prompt import api_lattice_system_prompt
    from lattice.llm.prompt import baseline_system_prompt

    for hints in (True, False):
        assert 'on={"id": "order_id"}' in lattice_system_prompt(hints=hints)
        assert 'on={"id": "order_id"}' in api_lattice_system_prompt(hints=hints)
        assert "left_on=[...], right_on=[...]" in baseline_system_prompt(hints=hints)


def test_system_prompt_documents_every_loop_construct_and_win_function() -> None:
    from lattice.catalog import WIN_FUNCS

    for hints in (True, False):  # a language reference, not removable advice
        prompt = lattice_system_prompt(hints=hints)
        assert "## Loop constructs" in prompt
        for construct in ("`window(", "`traverse(", "`iterate("):
            assert construct in prompt, construct
        for name in WIN_FUNCS:
            assert f"`win.{name}(" in prompt, name
        assert 'direction="up"' in prompt and "unique key" in prompt


def test_loop_construct_examples_check_clean() -> None:
    """The inline window / traverse / iterate examples are valid Lattice (checked
    against the bench schemas with a declared order key, plus a category tree and a
    plans table)."""
    import dataclasses

    from bench.datasets import ORDERS
    from lattice.llm.prompt import ITERATE_EXAMPLE, TRAVERSE_EXAMPLE, WINDOW_EXAMPLE
    from lattice.typesys import Scalar, Schema

    prompt = lattice_system_prompt()
    for example in (WINDOW_EXAMPLE, *TRAVERSE_EXAMPLE, ITERATE_EXAMPLE):
        assert f"`{example}`" in prompt
    categories = Schema.from_pairs(
        [("id", Scalar.INT), ("parent_id", Scalar.INT), ("name", Scalar.STR)],
        keys=[("id",)],
        nullable=["parent_id"],
    )
    plans = Schema.from_pairs(
        [("id", Scalar.INT), ("est", Scalar.FLOAT), ("target", Scalar.FLOAT)], keys=[("id",)]
    )
    profile = Profile(
        schemas={
            "Orders": dataclasses.replace(ORDERS, keys=(("order_id",),)),
            "Categories": categories,
            "Plans": plans,
        }
    )
    source = (
        "# lattice: v1\n"
        "@step\n"
        "def halve(t: Table[Plans]) -> Table[Plans]:\n"
        '    out = derive(t, est=(col("est") + col("target")) / 2)\n'
        "    return out\n\n"
        'orders = load("orders.csv", schema=Orders)\n'
        'categories = load("categories.csv", schema=Categories)\n'
        'plans = load("plans.csv", schema=Plans)\n'
        + "\n".join((WINDOW_EXAMPLE, *TRAVERSE_EXAMPLE, ITERATE_EXAMPLE))
        + '\nwrite(running, "a.csv")\nwrite(tree, "b.csv")\nwrite(done, "c.csv")\n'
    )
    result = check_source(source, profile)
    assert result.ok and not result.diagnostics, [d.render() for d in result.diagnostics]
