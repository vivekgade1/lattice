"""System prompts and task messages for LLM generation.

The baseline (pandas) arm's prompt lives here alongside the Lattice arm's
system prompt and task message (Task 21). ``lattice`` must never import
``bench`` at module import time, so any lookup into the benchmark's dataset
registry is a lazy import inside the function that needs it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lattice.catalog import AGG_FUNCS, FN_DOCS, FN_SIGS, OP_SHAPES

if TYPE_CHECKING:
    from bench.tasks import Task
    from lattice.profile import Profile

def dataset_description(name: str) -> str:
    """Render one input dataset as ``"<file>.csv — columns: c1 (T1), c2 (T2), ..."``."""
    from bench.datasets import DATASETS

    dataset = DATASETS[name]
    columns = ", ".join(
        f"{col_name} ({scalar.value})" for col_name, scalar in dataset.schema.columns
    )
    return f"{dataset.filename} — columns: {columns}"


_INPUTS_HEADER = "Input files (in the current directory):"
_OUTPUT_LINE = "Write the final result to out.parquet"


def _pandas_read_call(name: str) -> str:
    from bench.datasets import DATASETS
    from lattice.typesys import Scalar

    dataset = DATASETS[name]
    dates = [col for col, scalar in dataset.schema.columns if scalar is Scalar.DATE]
    if dates:
        return f'pd.read_csv("{dataset.filename}", parse_dates={dates!r})'.replace("'", '"')
    return f'pd.read_csv("{dataset.filename}")'


def baseline_task_message(task: Task) -> str:
    """The user-turn message for the pandas baseline arm.

    Mirrors :func:`lattice_task_message`: same files, columns/types and output
    target, plus a per-file language-specific loading hint.
    """
    lines = [task.prompt, "", _INPUTS_HEADER]
    for name in task.inputs:
        lines.append(f"- {dataset_description(name)}")
        lines.append(f"  read with: {_pandas_read_call(name)}")
    lines.append("")
    lines.append(f"{_OUTPUT_LINE} with index=False.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lattice arm (Task 21)
# ---------------------------------------------------------------------------

_OP_DESCRIPTIONS: dict[str, str] = {
    "load": "read a schema-declared input table from a CSV file",
    "where": "keep only the rows where the Bool predicate is true; null predicates drop the row",
    "derive": (
        "add new columns or replace existing ones (same position), each a column "
        "expression; every expression reads the input table, not columns set earlier "
        "in the same call"
    ),
    "select": "project the table down to exactly the listed columns, in that given order",
    "rename": "rename one or more existing columns",
    "group": "group by zero or more columns and compute the given aggregates per group",
    "join": "combine two tables on shared key columns ('inner' or 'left')",
    "sort": "sort rows by one or more columns, ascending unless desc=True",
    "limit": "keep only the first n rows",
    "write": "write the final table to a file (.csv or .parquet); must be the last statement",
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
    lines = ["## Op catalog", "", "Every table op below is called exactly this way:", ""]
    for op in OP_SHAPES:
        lines.append(f"- `{_render_op_shape(op)}` — {_OP_DESCRIPTIONS[op]}")
    return "\n".join(lines)


def _render_fn_sig(name: str) -> str:
    sig = FN_SIGS[name]
    n_params = len(sig.params)
    params = ["x"] if n_params == 1 else [f"x{i + 1}" for i in range(n_params)]
    if name == "date_diff":
        params = ["end", "start"]
    args = list(params) + [f"{k}=" for k in sig.kwargs]
    result = sig.result.value if sig.result is not None else "same scalar type as its first arg"
    return f"`fn.{name}({', '.join(args)})` -> {result}"


def _expression_library_section() -> str:
    lines = [
        "## Expression library",
        "",
        "Column expressions (`Col[T]`) are built only from the following; nothing",
        "else is allowed inside them (no method calls, no subscripts, no f-strings):",
        "",
        '- `col("name")` — reference an existing column by its exact name',
        "- operators `+ - * / %` on numeric columns; `&`, `|`, `~` on Bool columns "
        "(never `and`/`or`/`not`); comparisons `== != < <= > >=`",
        "- `when(cond, then=a, otherwise=b)` — the only conditional; multi-branch "
        "chains nest only inside `otherwise`, never inside `then`",
        "- `coalesce(a, b)` — first non-null value",
        "- `is_null(a)` — true where the value is null",
        "",
        "Function library `fn.*` (every function; no aliases are accepted):",
        "",
    ]
    for name in FN_SIGS:
        lines.append(f"- {_render_fn_sig(name)} — {FN_DOCS[name]}")
    lines.append("")
    lines.append(
        "Aggregate library `agg.*` (only valid as a `group()` keyword value): "
        + ", ".join(f"`agg.{name}(...)`" for name in sorted(AGG_FUNCS))
        + ". `agg.count()` counts rows and `agg.count(x)` counts the non-null values of x; "
        "every other aggregate takes one column expression."
    )
    return "\n".join(lines)


_RULES_SECTION = """## Rules

- The first line of the program must be exactly `# lattice: v1`.
- No imports are needed: all ops, `col`, `when`, `coalesce`, `is_null`, `fn`, \
`agg`, `step`, the type names and the input schema names are always available.
- Every statement calls exactly one table op or `@step` and assigns its result to \
a brand-new name. Never reassign or reuse a name, and never use a built-in name \
(an op, function, type or schema name such as `where` or `Orders`) as a variable, \
step or parameter name.
- A statement may also bind a named constant, `name = <expr>`, when the \
expression uses no columns (literals, operators, `fn.*` on literals, earlier \
constants, a step's scalar parameters), e.g. `cutoff = fn.to_date("2025-01-01", \
format="%Y-%m-%d")`. Use it anywhere a value is allowed; an `fn.*` keyword or a \
step's scalar argument takes a constant only if it is a plain literal (`days = 30`).
- `derive(t, name=expr)` may overwrite an existing column: it is replaced in place \
with the expression's value and type. All expressions in one `derive` call read \
the input table `t`, so a later keyword never sees an earlier keyword's new value; \
use a second `derive` for that.
- Never nest calls: a table argument must always be a plain name, never another \
call. Assign the inner result to its own name first.
- `where(table, predicate)` takes its predicate positionally; every other \
non-table argument to every op is passed by keyword (e.g. `load(path, \
schema=Orders)`, `join(l, r, on=[...], how="left")`, `write(table, path)`).
- `join(l, r, on=[], how="inner")` (or `"left"`) attaches every column of `r` to \
every row of `l`; `r` must have at most one row, e.g. a total from \
`group(t, by=[], ...)`. Share of a total: \
`total = group(t, by=[], all_qty=agg.sum(col("qty")))`, \
`with_total = join(t, total, on=[], how="inner")`, \
`shares = derive(with_total, share=col("qty") / col("all_qty"))`.
- There are no loops, no `if` statements, no lambdas, no comprehensions, no \
method calls, and no subscripts anywhere. Use `when(...)` for conditionals and \
`col("name")` for column access.
- Use `&`, `|`, `~` for boolean combination — never `and`, `or`, `not`.
- `when(...)` may only nest inside `otherwise=`, never inside `then=`.
- `@step` defines a reusable, pure sub-pipeline: every parameter is annotated \
(`Table[Schema]` or a scalar type name), the return type is `Table[Schema]` or \
bare `Table`, the body is single-assignment statements ending in exactly one \
`return <name>`, and it may only read its own parameters and locals (and constants \
defined above it) — never a module-level table. A step must be defined before it \
is called and cannot call itself.
- The program must end by writing its result: `write(<table>, "out.parquet")`.
"""

# The three worked example problems. Both arms see the SAME three problems
# (Lattice programs here, pandas scripts in the baseline guide) so neither arm
# gets extra worked examples. They deliberately differ from every benchmark
# task (tests/llm/test_prompt_fairness.py enforces this).
_EXAMPLE_PROBLEMS = (
    "Example 1 — filter, then derive a column, then project. For refunded "
    "orders, compute refund_amount = qty * unit_price * (1 - discount), treating "
    "a missing discount as 0. Return order_id, customer_id, refund_amount.",
    "Example 2 — group, sort with an explicit tie-break, then limit. Among "
    "cancelled orders, compute cancelled_qty = sum(qty) per product_id and return "
    "the 3 products with the largest cancelled_qty (ties: higher product_id "
    "first). Return product_id, cancelled_qty.",
    "Example 3 — a reusable function that joins two tables. For orders with a "
    "given status (here \"pending\"), inner-join products on product_id and "
    "compute max_unit_price = max(unit_price) per category. Return category, "
    "max_unit_price.",
)

_EXAMPLE_1 = '''```python
# lattice: v1
orders = load("orders.csv", schema=Orders)
refunded = where(orders, col("status") == "refunded")
priced = derive(
    refunded,
    refund_amount=col("qty") * col("unit_price") * (1 - coalesce(col("discount"), 0.0)),
)
result = select(priced, cols=["order_id", "customer_id", "refund_amount"])
write(result, "out.parquet")
```'''

_EXAMPLE_2 = '''```python
# lattice: v1
orders = load("orders.csv", schema=Orders)
cancelled = where(orders, col("status") == "cancelled")
by_product = group(cancelled, by=["product_id"], cancelled_qty=agg.sum(col("qty")))
ranked = sort(by_product, by=["cancelled_qty", "product_id"], desc=True)
top3 = limit(ranked, n=3)
write(top3, "out.parquet")
```'''

_EXAMPLE_3 = '''```python
# lattice: v1


@step
def max_price_by_category(
    orders: Table[Orders], products: Table[Products], wanted: Str
) -> Table:
    kept = where(orders, col("status") == wanted)
    joined = join(kept, products, on=["product_id"], how="inner")
    by_category = group(joined, by=["category"], max_unit_price=agg.max(col("unit_price")))
    return by_category


orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
prices = max_price_by_category(orders, products, wanted="pending")
write(prices, "out.parquet")
```'''

_EXAMPLES_SECTION = f"""## Examples

{_EXAMPLE_PROBLEMS[0]}

{_EXAMPLE_1}

{_EXAMPLE_PROBLEMS[1]}

{_EXAMPLE_2}

{_EXAMPLE_PROBLEMS[2]} In Lattice this is a `@step`:

{_EXAMPLE_3}
"""

_OUTPUT_FORMAT_SECTION = (
    "## Output format\n\n"
    "Respond with exactly one ```python code block containing the complete "
    "program and nothing else. No prose before or after the code, no other "
    "code blocks."
)


def lattice_system_prompt() -> str:
    """The Lattice arm's system prompt: rules, op catalog, expression library,

    three worked examples, and the output contract. Deterministic (no
    timestamps or randomness) and built from ``lattice.catalog`` so the op and
    function lists cannot drift from the checker.
    """
    sections = [
        # Neutral wording matters: an intro describing a "whitelisted subset" that is
        # "legal" and "rejected before it ever runs" drew cyber-category refusals from
        # claude-opus-5 on 8/40 calls; this wording drew 0/40.
        "Lattice is a small domain-specific language for tabular data analysis. "
        "Programs are written in Python syntax, using only the operations and "
        "expression functions documented below. A validator checks each program's "
        "structure, names and column types and reports precise diagnostics, so "
        "follow the conventions exactly.",
        _RULES_SECTION,
        _op_catalog_section(),
        _expression_library_section(),
        _EXAMPLES_SECTION,
        _OUTPUT_FORMAT_SECTION,
    ]
    return "\n\n".join(sections) + "\n"


def lattice_task_message(task: Task, profile: Profile) -> str:
    """The user-turn message for the Lattice arm.

    Lists exactly the same files, columns/types and output target as
    :func:`baseline_task_message`; the only difference is the per-file
    language-specific loading hint (the ``load(...)`` call and schema name).
    """
    from bench.datasets import DATASETS

    lines = [task.prompt, "", _INPUTS_HEADER]
    for name in task.inputs:
        dataset = DATASETS[name]
        if dataset.schema_name not in profile.schemas:
            raise KeyError(f"schema {dataset.schema_name!r} missing from profile")
        lines.append(f"- {dataset_description(name)}")
        lines.append(
            f'  load with: load("{dataset.filename}", schema={dataset.schema_name})'
        )
    lines.append("")
    lines.append(f'{_OUTPUT_LINE} with write(<table>, "out.parquet").')
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
- Read each input with `pd.read_csv("<file>.csv")` from the current directory. \
Pass `parse_dates=[...]` listing every column whose type is `Date`, so date \
columns are real datetimes rather than strings.
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
- Whole-table aggregates (no grouping) produce a single-row result: build it \
explicitly, e.g. `pd.DataFrame({"total": [df["x"].sum()], "n": [len(df)]})`.
- Share of a total: compute the whole-table total as a scalar first, e.g. \
`total_qty = df["qty"].sum()` (which skips nulls), then divide: \
`df["share"] = df["qty"] / total_qty` (for a per-group total: \
`df.groupby(keys)["qty"].transform("sum")`); a single-row frame can also be \
attached to every row with `df.merge(one_row, how="cross")`. Named scalar constants \
are fine and often help readability, e.g. `cutoff = pd.Timestamp("2025-01-01")` or \
`min_qty = 5`. In aggregations `"count"` counts the non-null values of a column \
while `"size"` counts every row, nulls \
included.
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

_PANDAS_EXAMPLE_1 = '''```python
import pandas as pd

orders = pd.read_csv("orders.csv", parse_dates=["order_date"])
refunded = orders[orders["status"] == "refunded"].copy()
refunded["refund_amount"] = (
    refunded["qty"] * refunded["unit_price"] * (1 - refunded["discount"].fillna(0.0))
)
result = refunded[["order_id", "customer_id", "refund_amount"]]
result.to_parquet("out.parquet", index=False)
```'''

_PANDAS_EXAMPLE_2 = '''```python
import pandas as pd

orders = pd.read_csv("orders.csv", parse_dates=["order_date"])
cancelled = orders[orders["status"] == "cancelled"]
by_product = cancelled.groupby("product_id", as_index=False).agg(
    cancelled_qty=("qty", "sum")
)
ranked = by_product.sort_values(["cancelled_qty", "product_id"], ascending=False)
top3 = ranked.head(3)
top3.to_parquet("out.parquet", index=False)
```'''

_PANDAS_EXAMPLE_3 = '''```python
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
```'''

_PANDAS_EXAMPLES_SECTION = f"""## Examples

{_EXAMPLE_PROBLEMS[0]}

{_PANDAS_EXAMPLE_1}

{_EXAMPLE_PROBLEMS[1]}

{_PANDAS_EXAMPLE_2}

{_EXAMPLE_PROBLEMS[2]} In pandas this is a plain function:

{_PANDAS_EXAMPLE_3}
"""

BASELINE_SYSTEM = (
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
            _PANDAS_RULES_SECTION,
            _PANDAS_EXAMPLES_SECTION,
            _OUTPUT_FORMAT_SECTION.replace("program", "script"),
        ]
    )
    + "\n"
)
