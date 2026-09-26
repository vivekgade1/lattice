"""System prompts and task messages for LLM generation.

The baseline (pandas) arm's prompt lives here alongside the Lattice arm's
system prompt and task message (Task 21). ``lattice`` must never import
``bench`` at module import time, so any lookup into the benchmark's dataset
registry is a lazy import inside the function that needs it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lattice.catalog import AGG_FUNCS, FN_SIGS, OP_SHAPES

if TYPE_CHECKING:
    from bench.tasks import Task
    from lattice.profile import Profile

BASELINE_SYSTEM = (
    "You are an expert data engineer. Write one Python 3 script using pandas "
    "that reads the listed CSV files from the current directory, computes "
    "exactly what is requested, and writes the final result to out.parquet "
    "with exactly the requested columns, using index=False. "
    "Output exactly one ```python code block containing the complete script "
    "and nothing else runnable."
)


def dataset_description(name: str) -> str:
    """Render one input dataset as ``"<file>.csv — columns: c1 (T1), c2 (T2), ..."``."""
    from bench.datasets import DATASETS

    dataset = DATASETS[name]
    columns = ", ".join(
        f"{col_name} ({scalar.value})" for col_name, scalar in dataset.schema.columns
    )
    return f"{dataset.filename} — columns: {columns}"


def baseline_task_message(task: Task) -> str:
    """The user-turn message for the pandas baseline arm."""
    lines = [task.prompt, "", "Input files (read them from the current directory):"]
    for name in task.inputs:
        lines.append(f"- {dataset_description(name)}")
    lines.append("")
    lines.append('Write the final result to out.parquet with index=False.')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lattice arm (Task 21)
# ---------------------------------------------------------------------------

_OP_DESCRIPTIONS: dict[str, str] = {
    "load": "read a schema-declared input table from a CSV file",
    "where": "keep only the rows where the Bool predicate is true; null predicates drop the row",
    "derive": "add one or more new columns, each a column expression",
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
        lines.append(f"- {_render_fn_sig(name)}")
    lines.append("")
    lines.append(
        "Aggregate library `agg.*` (only valid as a `group()` keyword value): "
        + ", ".join(f"`agg.{name}(...)`" for name in sorted(AGG_FUNCS))
        + ". `agg.count()` takes no argument; every other aggregate takes one column expression."
    )
    return "\n".join(lines)


_RULES_SECTION = """## Rules

- The first line of the program must be exactly `# lattice: v1`.
- Import only `from lattice.ops import ...` and `from lattice.schemas import ...`; \
the imported names must exactly match the names used in the program (no unused \
imports, nothing used without being imported).
- Every statement calls exactly one table op or `@step` and assigns its result to \
a brand-new name. Never reassign or reuse a name (no shadowing).
- Never nest calls: a table argument must always be a plain name, never another \
call. Assign the inner result to its own name first.
- `where(table, predicate)` takes its predicate positionally; every other \
non-table argument to every op is passed by keyword (e.g. `load(path, \
schema=Orders)`, `join(l, r, on=[...], how="left")`, `write(table, path)`).
- There are no loops, no `if` statements, no lambdas, no comprehensions, no \
method calls, and no subscripts anywhere. Use `when(...)` for conditionals and \
`col("name")` for column access.
- Use `&`, `|`, `~` for boolean combination — never `and`, `or`, `not`.
- `when(...)` may only nest inside `otherwise=`, never inside `then=`.
- `@step` defines a reusable, pure sub-pipeline: every parameter is annotated \
(`Table[Schema]` or a scalar type name), the return type is `Table[Schema]` or \
bare `Table`, the body is single-assignment statements ending in exactly one \
`return <name>`, and it may only read its own parameters and locals — never a \
module-level table. A step must be defined before it is called and cannot call \
itself.
- The program must end by writing its result: `write(<table>, "out.parquet")`.
"""

_EXAMPLE_1 = '''```python
# lattice: v1
from lattice.ops import coalesce, col, derive, load, select, where, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
paid = where(orders, col("status") == "paid")
priced = derive(paid, net_price=col("unit_price") * (1 - coalesce(col("discount"), 0.0)))
result = select(priced, cols=["order_id", "net_price"])
write(result, "out.parquet")
```'''

_EXAMPLE_2 = '''```python
# lattice: v1
from lattice.ops import agg, col, group, limit, load, sort, where, write
from lattice.schemas import Orders

orders = load("orders.csv", schema=Orders)
paid = where(orders, col("status") == "paid")
by_product = group(
    paid,
    by=["product_id"],
    total_revenue=agg.sum(col("qty") * col("unit_price")),
)
ranked = sort(by_product, by=["total_revenue", "product_id"], desc=True)
top5 = limit(ranked, n=5)
write(top5, "out.parquet")
```'''

_EXAMPLE_3 = '''```python
# lattice: v1
from lattice.ops import Str, Table, agg, col, group, join, load, step, where, write
from lattice.schemas import Orders, Products


@step
def revenue_by_category(
    orders: Table[Orders], products: Table[Products], wanted: Str
) -> Table:
    paid = where(orders, col("status") == wanted)
    joined = join(paid, products, on=["product_id"], how="left")
    by_category = group(
        joined, by=["category"], revenue=agg.sum(col("qty") * col("unit_price"))
    )
    return by_category


orders = load("orders.csv", schema=Orders)
products = load("products.csv", schema=Products)
revenue = revenue_by_category(orders, products, wanted="paid")
write(revenue, "out.parquet")
```'''

_EXAMPLES_SECTION = f"""## Examples

Example 1 — filter, then derive a column, then project:

{_EXAMPLE_1}

Example 2 — group, sort with an explicit tie-break, then limit:

{_EXAMPLE_2}

Example 3 — a reusable `@step` that joins two tables:

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
        "You are writing a program in Lattice: a small, statically checked "
        "Python-syntax profile for tabular data pipelines. It is ordinary "
        "Python source, but only a narrow, whitelisted subset of the language "
        "is legal, and every program is checked for structure, names, and "
        "types before it ever runs. Your program will be rejected with exact "
        "diagnostics if it violates a rule below — follow them precisely.",
        _RULES_SECTION,
        _op_catalog_section(),
        _expression_library_section(),
        _EXAMPLES_SECTION,
        _OUTPUT_FORMAT_SECTION,
    ]
    return "\n\n".join(sections) + "\n"


def lattice_task_message(task: Task, profile: Profile) -> str:
    """The user-turn message for the Lattice arm.

    Renders each input as the exact ``load(...)`` call that already loaded it
    (inputs are provided pre-loaded; the model must not call ``load`` itself
    with a different path) plus its schema, then the task prompt, then the
    required ``write(...)`` call.
    """
    from bench.datasets import DATASETS

    lines = [task.prompt, "", "Input tables (already available exactly as loaded here):"]
    for name in task.inputs:
        dataset = DATASETS[name]
        schema = profile.schemas[dataset.schema_name]
        lines.append(f'{name} = load("{dataset.filename}", schema={dataset.schema_name})')
        cols = ", ".join(f"{col_name} ({scalar.value})" for col_name, scalar in schema.columns)
        lines.append(f"  schema {dataset.schema_name}: {cols}")
    lines.append("")
    lines.append('Write the final table with write(<table>, "out.parquet").')
    return "\n".join(lines)
