"""System prompts and task messages for LLM generation.

The baseline (pandas) arm's prompt lives here alongside the Lattice arm's
system prompt and task message (Task 21). ``lattice`` must never import
``bench`` at module import time, so any lookup into the benchmark's dataset
registry is a lazy import inside the function that needs it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lattice.catalog import AGG_FUNCS, FN_DOCS, FN_SIGS, OP_SHAPES, ORDER_AGGS

if TYPE_CHECKING:
    from collections.abc import Mapping

    import polars as pl

    from bench.datasets import Dataset
    from bench.tasks import Task
    from lattice.profile import Profile


def _registry(registry: str) -> Mapping[str, Dataset]:
    """The benchmark's dataset registry by name (imported lazily: ``lattice`` must
    not import ``bench`` at module import time)."""
    if registry == "bench":
        from bench.datasets import DATASETS

        return DATASETS
    if registry == "bench2":
        from bench.bench2_datasets import DATASETS2

        return DATASETS2
    raise ValueError(f"unknown dataset registry: {registry!r}")


def dataset_description(name: str, registry: str = "bench") -> str:
    """Render one input dataset as ``"<file>.csv — columns: c1 (T1), c2 (T2), ..."``,
    followed by ``; unique key: (a, b)`` and ``; may be missing: x, y`` when its schema
    declares keys or nullable columns."""
    dataset = _registry(registry)[name]
    schema = dataset.schema
    columns = ", ".join(f"{col_name} ({scalar.value})" for col_name, scalar in schema.columns)
    text = f"{dataset.filename} — columns: {columns}"
    if schema.keys:
        text += "; unique key: " + ", ".join(f"({', '.join(k)})" for k in schema.keys)
    if schema.nullable:
        text += "; may be missing: " + ", ".join(n for n in schema.names() if n in schema.nullable)
    return text


_INPUTS_HEADER = "Input files (in the current directory):"
_OUTPUT_LINE = "Write the final result to out.parquet"


def _samples_section(task: Task, samples: Mapping[str, tuple[pl.DataFrame, int]]) -> list[str]:
    """Message lines showing sample rows of every input, identical in both arms.

    ``samples`` maps each input dataset name to ``(head, total_rows)``: the first K
    rows as loaded and the input's full row count. Each input renders as
    ``Sample rows of <file> (first K of N):`` followed by compact CSV (a header and
    K rows, missing values empty), with a blank line before each section."""
    datasets = _registry(task.datasets)
    lines: list[str] = []
    for name in task.inputs:
        head, total = samples[name]  # KeyError: every input needs samples
        lines.append("")
        lines.append(f"Sample rows of {datasets[name].filename} (first {head.height} of {total}):")
        lines.extend(head.write_csv().splitlines())
    return lines


def baseline_task_message(
    task: Task, samples: Mapping[str, tuple[pl.DataFrame, int]] | None = None
) -> str:
    """The user-turn message for the pandas baseline arm.

    Mirrors :func:`lattice_task_message`: same files, columns/types, sample rows
    (when given) and output target; it has no per-file loading hint because
    reading the CSVs is part of the pandas task.
    """
    lines = [task.prompt, "", _INPUTS_HEADER]
    for name in task.inputs:
        lines.append(f"- {dataset_description(name, task.datasets)}")
    if samples is not None:
        lines.extend(_samples_section(task, samples))
    lines.append("")
    lines.append(f"{_OUTPUT_LINE} with index=False. Write no other file.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lattice arm (Task 21)
# ---------------------------------------------------------------------------

_OP_DESCRIPTIONS: dict[str, str] = {
    "load": "read an input CSV",
    "where": "keep rows where the Bool predicate is true (null drops the row)",
    "derive": "add or replace columns",
    "select": "keep exactly the listed columns, in that order",
    "rename": "rename columns",
    "group": "one row per `by` group (by=[]: one row overall) with the aggregates",
    "join": "on shared key columns; how 'inner' or 'left'",
    "sort": "ascending unless desc=True; desc=[True, False] sets each by= column's direction",
    "limit": "first n rows",
    "write": "write the result (.csv or .parquet)",
}


_VAR_KW_HINT: dict[str, str] = {
    "derive": "name=<expr>, ...",
    "rename": 'old_name="new_name", ...',
    "group": "name=agg.<fn>(<expr>), ...",
}


def _render_op_shape(op: str) -> str:
    shape = OP_SHAPES[op]
    parts = list(shape.positional)
    for kw in sorted(shape.required_kw):
        parts.append(f"{kw}=...")
    for kw in sorted(shape.optional_kw):
        parts.append(f"{kw}=... (optional)")
    if shape.var_kw:
        parts.append(_VAR_KW_HINT[op])
    return f"{op}({', '.join(parts)})"


def _op_catalog_section() -> str:
    header = "Call each table op exactly this way (positional vs keyword):"
    lines = ["## Op catalog", "", header, ""]
    for op in OP_SHAPES:
        lines.append(f"- `{_render_op_shape(op)}`: {_OP_DESCRIPTIONS[op]}")
    return "\n".join(lines)


def _render_fn_sig(name: str) -> str:
    sig = FN_SIGS[name]
    n_params = len(sig.params)
    params = ["x"] if n_params == 1 else [f"x{i + 1}" for i in range(n_params)]
    if name == "date_diff":
        params = ["end", "start"]
    args = list(params) + [f"{k}=" for k in sig.kwargs]
    result = sig.result.value if sig.result is not None else "type of x"
    return f"`fn.{name}({', '.join(args)})` -> {result}"


# One doc line per group of aggregates; every name in AGG_FUNCS must appear once.
_AGG_DOCS = (
    (("sum", "mean", "min", "max"), "take one column expression"),
    (("count",), "`agg.count()` counts rows, `agg.count(x)` the non-null values of x"),
    (("count_distinct",), "`agg.count_distinct(x)` its distinct non-null values"),
    (
        ("at_min", "at_max"),
        "`agg.at_min(x, of=o)`/`agg.at_max(x, of=o)` give x where o is smallest/largest "
        "(ties: earliest row; an earlier sort has no effect)",
    ),
)


def _aggregate_line() -> str:
    documented = [n for names, _ in _AGG_DOCS for n in names]
    if sorted(documented) != sorted(AGG_FUNCS):
        raise AssertionError(f"_AGG_DOCS out of sync with AGG_FUNCS: {sorted(documented)}")
    simple = ", ".join(f"`agg.{n}(x)`" for n in _AGG_DOCS[0][0] if n not in ORDER_AGGS)
    rest = "; ".join(doc for _, doc in _AGG_DOCS[1:])
    head = "Aggregates `agg.*` (only as `group()` keyword values):"
    return f"{head} {simple} {_AGG_DOCS[0][1]}; {rest}."


def _expression_library_section() -> str:
    lines = [
        "## Expression library",
        "",
        'Column expressions use only: `col("name")`; `+ - * / %` on numbers; comparisons '
        "`== != < <= > >=`; `&`, `|`, `~` on Bool (never `and`/`or`/`not`); "
        "`when(cond, then=a, otherwise=b)`, the only conditional (nest `when` only in "
        "`otherwise`, never in `then`); `coalesce(a, b)` (first non-null); `is_null(a)`. "
        "No loops, `if`, lambdas, comprehensions, method calls, subscripts or f-strings "
        "anywhere.",
        "",
        "`fn.*` (no aliases):",
        "",
    ]
    for name in FN_SIGS:
        lines.append(f"- {_render_fn_sig(name)}: {FN_DOCS[name]}")
    lines.append("")
    lines.append(_aggregate_line())
    return "\n".join(lines)


_RULES_SECTION = """## Rules

- The first line is exactly `# lattice: v1`; the last is `write(<table>, "out.parquet")`. \
Write only the output file named in the task.
- No imports are needed: all ops, `col`, `when`, `coalesce`, `is_null`, `fn`, \
`agg`, `step`, the type names and the input schema names are always available.
- Each statement assigns one table op or `@step` call to a new name. Never reassign \
a name or use a built-in name (op, function, type or schema, e.g. \
`where`, `Orders`) as a variable, step or parameter.
- Never nest calls: table arguments are plain names.
- A statement may also bind a named constant `name = <expr>` using no columns \
(literals, operators, `fn.*` on literals, earlier constants, a step's scalar \
parameters), e.g. `cutoff = fn.to_date("2025-01-01", format="%Y-%m-%d")`, usable \
as any value; as an `fn.*` keyword or step argument only if a plain literal (`days = 30`).
- `derive(t, name=expr)` may overwrite an existing column in place. All expressions \
in one `derive` read the input table `t`, not each other's results (chain a second \
`derive` for that).
- `join(l, r, on=[], how="inner")` (or `"left"`) attaches `r` to every row of `l`; \
`r` must have at most one row. Share of a total: \
`total = group(t, by=[], all_qty=agg.sum(col("qty")))`, \
`with_total = join(t, total, on=[], how="inner")`, \
`shares = derive(with_total, share=col("qty") / col("all_qty"))`.
- A join must not silently repeat rows of `l`: `join(l, r, on=K)` needs `r` unique on \
`K` (a declared "unique key" of `r` inside `K`, or `r` from `group(..., by=K, ...)`). \
Otherwise reduce `r` to one row per key first, e.g. current price per product: \
`group(prices, by=["product_id"], price=agg.at_max(col("price"), of=col("valid_from")))`. \
Pass `fanout="allow"` if repeats are intended or `r` is unique by the task's meaning \
(e.g. one primary contact per customer).
- `agg.mean`, `agg.min` and `agg.max` over a value that "may be missing" (a listed \
column, a left join's right-hand column, or an `fn.to_int`/`to_float`/`to_date`/\
`log`/`sqrt` result) must say how to treat missing values: \
`agg.mean(coalesce(col("x"), 0.0))` counts them as 0, `agg.mean(col("x"), \
nulls="skip")` ignores them. `group` keeps rows with a missing key as their own group.
- `@step` defines a pure sub-pipeline: parameters annotated `Table[Schema]` or a \
scalar type, returns `Table[Schema]` or `Table`, body of single-assignment statements \
ending in one `return <name>`, reading only its parameters, locals and earlier \
constants (never a module-level table). Define it before calling it; no recursion."""

# The three worked example problems. Both arms see the SAME three problems
# (Lattice programs here, pandas scripts in the baseline guide) so neither arm
# gets extra worked examples. They deliberately differ from every benchmark
# task (tests/llm/test_prompt_fairness.py enforces this).
_EXAMPLE_PROBLEMS = (
    "Example 1. For refunded orders, return order_id, customer_id, refund_amount = "
    "qty * unit_price * (1 - discount), a missing discount counting as 0.",
    "Example 2. Among cancelled orders, return product_id, cancelled_qty = sum(qty) "
    "for the 3 products with the largest cancelled_qty (ties: higher product_id first).",
    "Example 3. As a reusable function: for orders with a given status (here "
    '"pending"), inner-join products on product_id and return category, '
    "max_unit_price = max(unit_price) per category.",
)

_EXAMPLE_1 = """```python
# lattice: v1
orders = load("orders.csv", schema=Orders)
refunded = where(orders, col("status") == "refunded")
priced = derive(
    refunded, refund_amount=col("qty") * col("unit_price") * (1 - coalesce(col("discount"), 0.0))
)
result = select(priced, cols=["order_id", "customer_id", "refund_amount"])
write(result, "out.parquet")
```"""

_EXAMPLE_2 = """```python
# lattice: v1
orders = load("orders.csv", schema=Orders)
cancelled = where(orders, col("status") == "cancelled")
by_product = group(cancelled, by=["product_id"], cancelled_qty=agg.sum(col("qty")))
ranked = sort(by_product, by=["cancelled_qty", "product_id"], desc=True)
top3 = limit(ranked, n=3)
write(top3, "out.parquet")
```"""

_EXAMPLE_3 = """```python
# lattice: v1
@step
def max_price_by_category(orders: Table[Orders], products: Table[Products], wanted: Str) -> Table:
    kept = where(orders, col("status") == wanted)
    joined = join(kept, products, on=["product_id"], how="inner")
    by_category = group(joined, by=["category"], max_unit_price=agg.max(col("unit_price")))
    return by_category

orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
prices = max_price_by_category(orders, products, wanted="pending")
write(prices, "out.parquet")
```"""

_EXAMPLES_SECTION = f"""## Examples

{_EXAMPLE_PROBLEMS[0]}

{_EXAMPLE_1}

{_EXAMPLE_PROBLEMS[1]}

{_EXAMPLE_2}

{_EXAMPLE_PROBLEMS[2]}

{_EXAMPLE_3}"""

_OUTPUT_FORMAT_SECTION = (
    "## Output format\n\n"
    "Respond with exactly one ```python code block holding the complete program; "
    "no prose, no other code blocks."
)


# Pitfall advice, removable for the no-hints condition (language reference stays).
_LATTICE_ADVICE_BULLETS = (
    "- A join must not silently repeat rows",
    "- `agg.mean`, `agg.min` and `agg.max` over a value",
)
_PANDAS_ADVICE_BULLETS = (
    "- A merge must not silently repeat left rows",
    "- `mean`, `min` and `max` skip missing values silently",
    "- Columns typed `Str` must be read as text",
)
_PANDAS_ADVICE_FRAGMENTS = (
    " (e.g. `fillna(0.0)` only when told to treat missing as 0)",
    " `groupby` drops rows whose key is null unless you pass `dropna=False`; keep or "
    "drop null keys exactly as the task says.",
)


def _without_advice(section: str, bullets: tuple[str, ...], fragments=()) -> str:
    """Drop whole advice bullets (one line each) and advice sentences inside bullets."""
    for prefix in bullets:
        if not any(line.startswith(prefix) for line in section.splitlines()):
            raise AssertionError(f"advice bullet not found: {prefix!r}")
    kept = [ln for ln in section.splitlines() if not ln.startswith(bullets)]
    text = "\n".join(kept) + ("\n" if section.endswith("\n") else "")
    for fragment in fragments:
        if fragment not in text:
            raise AssertionError(f"advice fragment not found: {fragment!r}")
        text = text.replace(fragment, "")
    return text


def lattice_system_prompt(hints: bool = True) -> str:
    """The Lattice arm's system prompt: rules, op catalog, expression library,

    three worked examples, and the output contract. Deterministic (no
    timestamps or randomness) and built from ``lattice.catalog`` so the op and
    function lists cannot drift from the checker.
    """
    sections = [
        # Neutral wording matters: an intro describing a "whitelisted subset" that is
        # "legal" and "rejected before it ever runs" drew cyber-category refusals from
        # claude-opus-5 on 8/40 calls; this wording drew 0/40.
        "Lattice is a small domain-specific language for tabular data analysis, "
        "written in Python syntax using only the operations and functions documented "
        "below. A validator checks each program's structure, names and column types.",
        _RULES_SECTION if hints else _without_advice(_RULES_SECTION, _LATTICE_ADVICE_BULLETS),
        _op_catalog_section(),
        _expression_library_section(),
        _EXAMPLES_SECTION,
        _OUTPUT_FORMAT_SECTION,
    ]
    return "\n\n".join(sections) + "\n"


def lattice_task_message(
    task: Task,
    profile: Profile,
    samples: Mapping[str, tuple[pl.DataFrame, int]] | None = None,
) -> str:
    """The user-turn message for the Lattice arm.

    Lists exactly the same files, columns/types, sample rows (when given) and
    output target as :func:`baseline_task_message`; the only differences are the
    per-file ``load(...)`` call (the language's API for reading an input) and the
    ``write(...)`` output contract.
    """
    datasets = _registry(task.datasets)
    lines = [task.prompt, "", _INPUTS_HEADER]
    for name in task.inputs:
        dataset = datasets[name]
        if dataset.schema_name not in profile.schemas:
            raise KeyError(f"schema {dataset.schema_name!r} missing from profile")
        lines.append(f"- {dataset_description(name, task.datasets)}")
        lines.append(f'  load with: load("{dataset.filename}", schema={dataset.schema_name})')
    if samples is not None:
        lines.extend(_samples_section(task, samples))
    lines.append("")
    allowed = sorted(profile.outputs) if profile.outputs else ["out.parquet"]
    target = "out.parquet" if "out.parquet" in allowed else allowed[0]
    lines.append(f'{_OUTPUT_LINE} with write(<table>, "{target}"). Write no other file.')
    if profile.outputs is not None:
        lines.append(f"This task may only write: {', '.join(allowed)}.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Baseline (pandas) arm system prompt
# ---------------------------------------------------------------------------
#
# Spec §2 requires "same model, same prompt budget": the pandas arm gets a
# guide of comparable length (tests keep it within ±30% of the Lattice
# prompt) that works the SAME three example problems as the Lattice prompt.

_PANDAS_RULES_SECTION = """## Rules

- Write one complete Python 3 script. Import only `pandas as pd` and, if you \
need it, `numpy as np`. Do not use the network, do not read or write any file \
other than the listed inputs and `out.parquet`, and do not print large output.
- Columns typed `Date` must be parsed as dates (e.g. `parse_dates=[...]`), so \
they are real datetimes rather than strings.
- Column types in the task description map to pandas as follows: `Int` -> \
int64 (a column containing nulls may load as float64), `Float` -> float64, \
`Str` -> object/str, `Date` -> datetime64 (via `parse_dates`), `Bool` -> bool.
- A missing (null) value in the CSV loads as `NaN`/`NaT`. Treat nulls exactly \
as the task says (e.g. `fillna(0.0)` only when told to treat missing as 0). \
Comparisons against `NaN` are false, so null rows drop out of filters.
- Use `&`, `|`, `~` with parentheses around each comparison to combine boolean \
masks — never `and`/`or`/`not`. Use `.copy()` after filtering before you add \
columns, to avoid chained-assignment warnings.
- Group with `df.groupby(keys, as_index=False).agg(new_name=("column", "fn"))` \
using `sum`, `mean`, `min`, `max` or `size`/`count`. `groupby` drops rows whose \
key is null unless you pass `dropna=False`; keep or drop null keys exactly as \
the task says.
- Join with `left.merge(right, on=[...], how="inner")` or `how="left"`. For \
"rows with no match" use `~left["key"].isin(right["key"])` (or a left merge \
followed by an `isna()` test on a right-hand column).
- A merge must not silently repeat left rows: pass \
`validate="many_to_one"` to `merge` (or first reduce the right side to one row \
per key, e.g. the latest row per key: `sort_values(date_col)` then \
`drop_duplicates(keys, keep="last")`) when the right table has several rows \
per key; a column listed as "unique key" is safe to merge on, and so is a right \
side unique by the task's meaning (e.g. one primary contact per customer).
- `mean`, `min` and `max` skip missing values silently: when the task says a \
missing value counts as 0, `fillna(0.0)` before aggregating (a "may be missing" \
column, or right-hand columns after a left merge). `groupby(..., dropna=False)` \
keeps rows with a missing key as their own group when the task keeps them.
- Columns typed `Str` must be read as text (e.g. `dtype=str` for those \
columns): code-like values such as zip codes keep leading zeros.
- Write only `out.parquet`: no other files, no network access, no shell or \
subprocess calls, no `os`/`shutil` file operations.
- Whole-table aggregates (no grouping) produce a single-row result: build it \
explicitly, e.g. `pd.DataFrame({"total": [df["x"].sum()], "n": [len(df)]})`.
- Share of a total: compute the whole-table total as a scalar first, e.g. \
`total_qty = df["qty"].sum()` (which skips nulls), then divide: \
`df["share"] = df["qty"] / total_qty` (for a per-group total: \
`df.groupby(keys)["qty"].transform("sum")`); a single-row frame can also be \
attached to every row with `df.merge(one_row, how="cross")`. Named scalar constants \
are fine and often help readability, e.g. `cutoff = pd.Timestamp("2025-01-01")` or \
`min_qty = 5`. In aggregations `"count"` counts the non-null values of a column, \
`"nunique"` its distinct non-null values, and `"size"` every row, nulls included.
- Conditionals: `np.where(cond, a, b)` for two branches; for several ordered \
branches use `np.select([c1, c2], [v1, v2], default=v3)` — the first true \
condition wins. Null handling: `s.isna()` tests for null, `s.fillna(v)` \
replaces nulls, `a.fillna(b)` is "a, or b when a is null".
- Conversions: `.astype("int64")` truncates floats toward zero, \
`.astype("float64")` widens ints, `.astype(str)` renders numbers as text \
(`3` becomes `"3"`). Keep integer results as integers and fractional results \
as floats; do not round unless the task asks for rounding.
- Sort with `sort_values(by=[...], ascending=...)`, always including the \
tie-break columns the task names, then `head(n)` for a top-n.
- Strings: `.str.strip()`, `.str.lower()`, `.str.upper()`, `.str.len()`, \
`.str.contains(s, regex=False)`, `.str.startswith(s)`, \
`.str.replace(old, new, regex=False)`, and `+` to concatenate. Dates: \
`.dt.year`, `.dt.month`, `.dt.day`, `pd.Timestamp("YYYY-MM-DD")`, \
`(a - b).dt.days`, `+ pd.Timedelta(days=n)`. Math: `np.floor`, `np.ceil`, \
`np.sqrt`, `np.log`, `.abs()`, `.clip(lower=, upper=)`, `.round(n)`, \
`.astype(...)`.
- Output exactly the requested columns, with exactly the requested names, and \
nothing else (no index column, no helper columns). Row order only matters \
when the task asks for a sorted or top-n result.
- The script must end by writing its result: \
`result.to_parquet("out.parquet", index=False)`.
"""

_PANDAS_EXAMPLE_1 = """```python
import pandas as pd

orders = pd.read_csv("orders.csv", parse_dates=["order_date"])
refunded = orders[orders["status"] == "refunded"].copy()
refunded["refund_amount"] = (
    refunded["qty"] * refunded["unit_price"] * (1 - refunded["discount"].fillna(0.0))
)
result = refunded[["order_id", "customer_id", "refund_amount"]]
result.to_parquet("out.parquet", index=False)
```"""

_PANDAS_EXAMPLE_2 = """```python
import pandas as pd

orders = pd.read_csv("orders.csv", parse_dates=["order_date"])
cancelled = orders[orders["status"] == "cancelled"]
by_product = cancelled.groupby("product_id", as_index=False).agg(
    cancelled_qty=("qty", "sum")
)
ranked = by_product.sort_values(["cancelled_qty", "product_id"], ascending=False)
top3 = ranked.head(3)
top3.to_parquet("out.parquet", index=False)
```"""

_PANDAS_EXAMPLE_3 = """```python
import pandas as pd


def max_price_by_category(
    orders: pd.DataFrame, products: pd.DataFrame, wanted: str
) -> pd.DataFrame:
    kept = orders[orders["status"] == wanted]
    joined = kept.merge(products, on=["product_id"], how="inner")
    return joined.groupby("category", as_index=False).agg(
        max_unit_price=("unit_price", "max")
    )


orders = pd.read_csv("orders.csv", parse_dates=["order_date"])
products = pd.read_csv("products.csv")
prices = max_price_by_category(orders, products, wanted="pending")
prices.to_parquet("out.parquet", index=False)
```"""

_PANDAS_EXAMPLES_SECTION = f"""## Examples

{_EXAMPLE_PROBLEMS[0]}

{_PANDAS_EXAMPLE_1}

{_EXAMPLE_PROBLEMS[1]}

{_PANDAS_EXAMPLE_2}

{_EXAMPLE_PROBLEMS[2]}

{_PANDAS_EXAMPLE_3}
"""


def baseline_system_prompt(hints: bool = True) -> str:
    """Arm A/P system prompt; hints=False drops the pitfall advice (no-hints condition)."""
    rules = (
        _PANDAS_RULES_SECTION
        if hints
        else _without_advice(
            _PANDAS_RULES_SECTION, _PANDAS_ADVICE_BULLETS, _PANDAS_ADVICE_FRAGMENTS
        )
    )
    return (
        "\n\n".join(
            [
                "You are an expert data engineer. Write one Python 3 script using "
                "pandas that reads the listed CSV files from the current directory, "
                "computes exactly what is requested, and writes the final result to "
                "out.parquet with exactly the requested columns, using index=False. "
                "Your script is run once, unattended, in a fresh directory that "
                "contains only the input files; its out.parquet is then compared "
                "with the correct answer, so follow the task and the rules below "
                "precisely.",
                rules,
                _PANDAS_EXAMPLES_SECTION,
                _OUTPUT_FORMAT_SECTION.replace("program", "script"),
            ]
        )
        + "\n"
    )


BASELINE_SYSTEM = baseline_system_prompt()
