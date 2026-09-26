# Lattice Phase 0–3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a benchmark harness, a statically checked Python-AST profile for tabular data pipelines (with `@step` functions and a `when`/`fn.*` expression library), a polars executor, and a Claude-driven generate→check→repair loop — then measure whether constrained generation beats plain pandas generation.

**Architecture:** The LLM writes ordinary Python text. We parse it with the stdlib `ast`, run five checker passes (P1 node whitelist → P2 names → P3 canonical form → P4 types/schemas → P5 effects) that collect every diagnostic with a `line:col` span and stable code, lower the checked AST into a step DAG (`Plan`), and execute that DAG with polars through an exhaustive dispatcher — never `eval`/`exec`. The benchmark runs three arms (A: plain pandas, B: Lattice + repair, C: Lattice first-shot) over the same tasks and computes the go/no-go gate.

**Tech Stack:** Python ≥3.12, uv, polars, pyarrow, pandas (baseline arm only), anthropic SDK, pytest, hypothesis, ruff.

**Spec:** `docs/superpowers/specs/2026-09-26-lattice-design.md`

## Global Constraints

- Python `>=3.12`; package manager `uv`; tests `uv run pytest`; lint `uv run ruff check .`.
- Program header: first line exactly `# lattice: v1` (code `L001`).
- Every diagnostic: `Diagnostic(code, severity, span, message, hint)`; rendered `line:col: severity CODE: message` (+ ` (hint: …)`). Lines and columns are **1-based**.
- The checker **collects all** diagnostics; it never raises on user errors. Internal invariants raise `TypeError`/`LatticeInternalError`.
- No `eval`, `exec`, `compile` of model text anywhere in `lattice/`. The only place model-written code runs is the baseline arm's subprocess (`bench/sandbox.py`).
- Types: `Int, Float, Str, Bool, Date`; no implicit coercion except `Int`→`Float` widening where stated.
- Runtime semantics (spec §5.7): `/0` and `%0` → null + warning `R301`; math-domain errors → null + `R302`; failed casts → null + `R303`; NaN → null at `load`; null propagates in arithmetic; `where` drops null predicates; `sum`/`count` over empty → 0, `mean/min/max` → null.
- Default model: `claude-opus-5` (configurable via `--model`). Every live model run costs money — **ask the user before running `bench` against the live API**.
- Never name the source textbook or its author in code, comments, identifiers, or logs.
- Commit after every task with a conventional-commit message ending in the two attribution lines:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01DkfBU2ur5VhLPgqfejszsZ`

### Spec clarifications decided by this plan (Task 9 writes them into the spec)

1. **Imports:** two allowed modules — `from lattice.ops import …` (ops, `col`, `when`, `coalesce`, `is_null`, `agg`, `fn`, `step`, `Table`, scalar type names) and `from lattice.schemas import …` (input schema names). Imports must exactly match use.
2. **Canonical call shapes (refines L303):** each op has one fixed signature saying which parameters are positional and which are keyword. `where(t, pred)` takes its predicate positionally; everything else that is not a table is keyword.
3. **Step return annotation** may be bare `Table` (schema inferred from the body); parameters must be concrete (`Table[Orders]`) or scalar (`Int`, …). A concrete return type is checked exactly (L605).
4. **Nullability:** every column is nullable in v1; the `T?` distinction is deferred.
5. **Output files:** `write` accepts `.csv` or `.parquet`; the benchmark always uses `out.parquet`.

## Review Focus

1. **Empty input tables** (0 rows after `where`) flowing into `group`/`join`/`derive` — expect an empty result with the statically predicted schema; `group(t, by=[], n=agg.count())` → one row with `n = 0`. Test in Task 19.
2. **Column names that aren't identifiers** (`"unit price"`) — `col("unit price")` must work end-to-end; only *new* column names (derive/group keywords) must be identifiers. Test in Task 19.
3. **Messy model output** — prose around code, several code blocks, no code block, a non-`python` fence. First ```` ```python ```` block wins; none → diagnostic `L000` fed to repair. Tests in Task 5 and Task 21.
4. **Invalid data in input files** (bad date string in a `Date` column) — expect runtime error `R101` pointing at the `load` line, not a polars traceback. Test in Task 19.
5. **Source text with CRLF line endings or a UTF-8 BOM** (common from some clients) — header detection and spans must still work. Test in Task 8.

---

## File Structure

```
pyproject.toml                  project metadata, deps, pytest/ruff config
.gitignore                      results/, .venv, caches
lattice/__init__.py
lattice/typesys.py              Scalar, Schema, checker types (TableT, ColT, LitT, …), unify()
lattice/diagnostics.py          Span, Severity, Diagnostic, span_of(), render helpers
lattice/catalog.py              op shapes, importable names, fn/agg signatures, aliases (pure data)
lattice/profile.py              Profile (schemas + version)
lattice/parse.py                header check + ast.parse → ParseResult
lattice/check/__init__.py       check_source() orchestrator → CheckResult / CheckedProgram
lattice/check/context.py        CheckContext (diagnostics list + type side table)
lattice/check/astutil.py        shared AST helpers (annotation shapes, step param kinds)
lattice/check/p1_nodes.py       node whitelist + step signature shape
lattice/check/p2_names.py       scopes, single assignment, use-before-def, purity of steps
lattice/check/p3_canon.py       one-op-per-statement, no nesting, call shapes, no-op steps
lattice/check/exprs.py          column-expression typing (col/when/coalesce/fn.*/operators)
lattice/check/p4_types.py       op schema transfer functions, step signatures
lattice/check/p5_effects.py     load/write placement and paths
lattice/canon.py                canonical pretty-printer
lattice/ir.py                   Plan/Node + lowering with step inlining
lattice/runtime/__init__.py
lattice/runtime/exprs.py        AST expression → polars Expr (+ null guards)
lattice/runtime/ops.py          op implementations
lattice/runtime/exec.py         execute(plan) with schema assertions and limits
lattice/llm/__init__.py
lattice/llm/client.py           Generator protocol, ClaudeGenerator, ScriptedGenerator, extract_code
lattice/llm/prompt.py           system prompts (Lattice + baseline) and task messages
lattice/llm/repair.py           synthesize(): generate → check → repair loop
bench/__init__.py
bench/datasets.py               deterministic fixtures + dataset registry
bench/tasks.py                  Task model + registry of suites
bench/seed_tasks.py             30 seed tasks with polars reference solutions
bench/more_tasks.py             ≥70 additional tasks (Task 23)
bench/compare.py                type-tolerant table comparison
bench/sandbox.py                subprocess runner for baseline code
bench/runners.py                run_baseline / run_lattice → TaskOutcome
bench/report.py                 summary, markdown report, gate evaluation
bench/cli.py                    `python -m bench.cli run …`
tests/…                         mirrors the modules above
```

---
## Execution notes

- Tasks are grouped into execution nodes (see "Execution graph" at the end). Tasks inside a node run in order; nodes at the same level run in parallel on **disjoint files**.
- Each task lists **Interfaces** (exact names/types later tasks rely on) and **Required tests** (exact inputs → expected outputs). Implement TDD: write the listed tests, see them fail, implement, see them pass. You may add tests; you may not drop listed ones.
- `lattice/typesys.py` and the scaffold (Task 1) are done and are the shared vocabulary: `Scalar`, `NUMERIC`, `ORDERED`, `SCALAR_NAMES`, `unify`, `Schema`, `TableT`, `ColT`, `LitT`, `ScalarParamT`, `StepT`, `UnknownT`/`UNKNOWN`, `Type`.

---

### Task 1: Scaffold + typesys — DONE

`lattice/profile.py` (`Profile`), `pyproject.toml` (deps: polars, pyarrow, pandas, anthropic; dev: pytest, hypothesis, ruff; pytest marker `live` deselected by default), `.gitignore`, package `__init__`s, `lattice/typesys.py`, `tests/test_typesys.py`.

---

## Phase 0 — Benchmark baseline

### Task 2: Deterministic datasets

**Files:** Create `bench/datasets.py`; Test `tests/bench/test_datasets.py`

**Interfaces (Produces):**
```python
CUSTOMERS: Schema  # customer_id Int, name Str, region Str, signup_date Date, tier Str
ORDERS: Schema     # order_id Int, customer_id Int, product_id Int, qty Int, unit_price Float,
                   # status Str, order_date Date, discount Float
PRODUCTS: Schema   # product_id Int, product_name Str, category Str, cost Float
@dataclass(frozen=True)
class Dataset: name: str; filename: str; schema_name: str; schema: Schema
DATASETS: Mapping[str, Dataset]   # keys "customers","orders","products";
                                  # schema_name "Customers","Orders","Products"
def polars_schema(schema: Schema) -> dict[str, pl.DataType]  # Int->Int64 Float->Float64 Str->String Bool->Boolean Date->Date
def write_fixtures(directory: Path, seed: int = 7) -> None     # writes the three CSVs
def load_fixtures(directory: Path) -> dict[str, pl.DataFrame]  # keyed by dataset name, typed via polars_schema
def bench_profile() -> Profile   # Profile(schemas={"Customers": CUSTOMERS, "Orders": ORDERS, "Products": PRODUCTS})
```
Content rules (seeded `random.Random(seed)`): 40 customers (regions from `north,south,east,west`, 2 customers with null region; tiers `bronze,silver,gold`; signup dates in 2022–2024; names with occasional leading/trailing spaces), 15 products (4 categories, cost 2–60), 300 orders (dates across 2023–2024, statuses `paid,pending,cancelled,refunded` with paid ≈ 60%, qty 0–12 with ≥5 zero-qty rows, unit_price ≥ cost, discount null in ≈20% of rows else 0.0–0.3). Dates written ISO `YYYY-MM-DD`.

**Required tests:** fixtures are byte-identical across two writes with the same seed; row counts 40/15/300; `load_fixtures` dtypes equal `polars_schema(...)`; orders has ≥5 rows with qty 0, ≥1 null discount; customers has exactly 2 null regions; every `orders.customer_id`/`product_id` exists in its parent table.

### Task 3: Table comparison

**Files:** Create `bench/compare.py`; Test `tests/bench/test_compare.py`

**Interfaces:** `def frames_match(actual: pl.DataFrame, expected: pl.DataFrame, *, ordered: bool, rtol: float = 1e-6, atol: float = 1e-9) -> tuple[bool, str]` — second item is `""` on success or a one-line reason (`"columns differ: missing ['x'], extra ['y']"`, `"row count 3 != 4"`, `"column 'x' differs at row 2: 1.5 != 2.0"`).

Algorithm: normalize both frames — numeric dtypes → `Float64`, `fill_nan(None)`, `round(6)`; `Datetime` → `Date` → `String`; everything else → `String`. Column **set** must be equal; reorder `actual` to `expected`'s order. Row count equal. If not `ordered`, sort both by all columns (`nulls_last=True`). Numeric columns: `|a-b| <= atol + rtol*|b|` or both null; others: `eq_missing`.

**Required tests:** identical frames match; row order ignored when `ordered=False` and detected when `True`; column order ignored; Int 3 vs Float 3.0 match; 1.0 vs 1.0000000001 match, 1.0 vs 1.1 mismatch with reason naming the column; NaN vs null match; pandas-style `Datetime` vs `Date` match; missing column reason lists it; null vs null in string column match.

### Task 4: Task model + 30 seed tasks

**Files:** Create `bench/tasks.py`, `bench/seed_tasks.py`; Test `tests/bench/test_tasks.py`

**Interfaces:**
```python
Tier = Literal["single", "multi", "join"]
@dataclass(frozen=True)
class Task:
    id: str                    # "s01".."s10", "m01".."m12", "j01".."j08"
    tier: Tier
    prompt: str                # natural language; MUST name every output column and its order-sensitivity; never mentions files
    inputs: tuple[str, ...]    # dataset names from bench.datasets.DATASETS
    reference: Callable[[Mapping[str, pl.DataFrame]], pl.DataFrame]
    ordered: bool = False
def suite(name: str) -> tuple[Task, ...]   # "seed" -> SEED_TASKS; "all" -> seed + more (Task 23; until then == seed)
SEED_TASKS: tuple[Task, ...]               # in bench/seed_tasks.py
```
Seed task content (prompt summary → output columns):
- single: s01 paid orders (all order columns); s02 add `revenue = qty*unit_price` (all + revenue); s03 customers in region west; s04 orders with qty ≥ 5; s05 `net_price = unit_price*(1 - discount)` with missing discount treated as 0 (order_id, net_price); s06 only order_id, customer_id, status; s07 rename qty→quantity (all columns); s08 10 orders with highest unit_price, ties by order_id descending, ordered (order_id, unit_price); s09 order count per status (status, n_orders); s10 order year (order_id, order_year).
- multi: m01 paid revenue per customer (customer_id, total_revenue); m02 per product paid revenue sum and mean qty, keep total > 500 (product_id, total_revenue, mean_qty); m03 size bucket large(qty≥10)/medium(qty≥3)/small, count per bucket (size, n_orders); m04 2024 paid revenue per month sorted by month ascending, ordered (month, revenue); m05 per has_discount flag: count and mean unit_price (has_discount, n_orders, mean_unit_price); m06 customers per tier and signup year (tier, signup_year, n_customers); m07 cancellation rate per customer = cancelled/total (customer_id, total_orders, cancelled_orders, cancel_rate); m08 top 5 paid-revenue customers with total ≥ 1000, descending, ordered (customer_id, total_revenue); m09 net revenue per status rounded to 2 decimals (status, net_revenue); m10 customer_id, name_upper = upper(strip(name)), name_len = length of stripped name; m11 Q4-2024 order count per status (status, n_orders); m12 days from signup to 2025-01-01 (customer_id, days_since_signup).
- join: j01 order_id with customer region; j02 paid revenue per region (region, revenue); j03 revenue per category (category, revenue); j04 top-5 products by total margin `(unit_price-cost)*qty`, ordered descending (product_name, margin); j05 customers with no orders (customer_id, name); j06 orders per customer tier (tier, n_orders); j07 revenue per region × category (region, category, revenue); j08 average paid order value per region rounded 2 (region, avg_order_value).

References are written in **polars** directly and must match the semantics the prompt states (nulls, rounding).

**Required tests:** 30 tasks, ids unique, tier counts 10/12/8; every reference runs on `load_fixtures` output and returns ≥1 row; every `ordered=True` task's prompt contains the word "sorted" or "order"; `suite("seed") == SEED_TASKS`.

### Task 5: Generator client + code extraction

**Files:** Create `lattice/llm/client.py`; Test `tests/llm/test_client.py`

**Interfaces:**
```python
@dataclass(frozen=True)
class Usage: input_tokens: int; output_tokens: int; cache_read_input_tokens: int = 0; cache_creation_input_tokens: int = 0
@dataclass(frozen=True)
class Generation: text: str; usage: Usage; latency_s: float; stop_reason: str
class GenerationRefused(Exception): ...
class Generator(Protocol):
    def generate(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation: ...
class ClaudeGenerator:
    def __init__(self, model: str = "claude-opus-5", max_tokens: int = 16000, effort: str = "high", client: Any | None = None): ...
class ScriptedGenerator:           # test double; replays texts in order, records calls
    def __init__(self, texts: Sequence[str]): ...
    calls: list[tuple[str, list[dict]]]
def extract_code(text: str) -> str | None
```
`ClaudeGenerator.generate` calls `client.beta.messages.create(model=..., max_tokens=..., betas=["server-side-fallback-2026-06-01"], fallbacks=[{"model": "claude-opus-4-8"}], output_config={"effort": effort}, system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}], messages=list(messages))`; client defaults to `anthropic.Anthropic()`. Raises `GenerationRefused` when `stop_reason == "refusal"`. Text = concatenation of `block.text` for blocks with `type == "text"`. Usage copies the four usage fields (missing → 0). `extract_code`: first fenced block whose info string is `python` or `py` (case-insensitive); else the first fenced block with an empty info string; else `None`. Tolerates CRLF.

**Required tests:** fake client (SimpleNamespace) receives exactly the kwargs above (system block carries `cache_control`); text blocks concatenated and thinking blocks ignored; refusal raises; usage copied; `ScriptedGenerator` returns texts in order and raises `IndexError` when exhausted; `extract_code` cases — prose + one python block; two blocks (first python wins); ```` ```py ````; bare ```` ``` ````; ```` ```sql ```` only → `None`; no block → `None`; CRLF block.

### Task 6: Sandbox + baseline arm runner

**Files:** Create `bench/sandbox.py`, `bench/runners.py`, `lattice/llm/prompt.py` (baseline part only); Test `tests/bench/test_sandbox.py`, `tests/bench/test_runners_baseline.py`

**Interfaces:**
```python
# bench/sandbox.py
@dataclass(frozen=True)
class ScriptResult: returncode: int; stdout: str; stderr: str; timed_out: bool
def run_python_script(code: str, workdir: Path, timeout_s: float = 60.0) -> ScriptResult
    # writes workdir/solution.py; runs [sys.executable, "-I", "solution.py"] with cwd=workdir,
    # env = {"PATH": os.environ["PATH"], "HOME": str(workdir)}; stderr truncated to 4000 chars
# lattice/llm/prompt.py
BASELINE_SYSTEM: str
def dataset_description(name: str) -> str   # "orders.csv — columns: order_id (Int), ..."
def baseline_task_message(task: Task) -> str
# bench/runners.py
Arm = Literal["A", "B", "C"]
@dataclass(frozen=True)
class TaskOutcome:
    task_id: str; arm: Arm; tier: str; correct: bool; check_passed: bool | None
    rounds: int; output_tokens: int; input_tokens: int; latency_s: float
    failure: str | None        # None when correct; else a taxonomy label
    detail: str
def prepare_workdir(task: Task, root: Path, fixtures: Path) -> Path   # root/<task.id>/ with copies of its input CSVs
def run_baseline(task: Task, generator: Generator, workdir: Path, fixtures_frames: Mapping[str, pl.DataFrame]) -> TaskOutcome
```
`BASELINE_SYSTEM`: "expert data engineer; write one Python 3 script using pandas; read the listed CSV files from the current directory; write the final result to `out.parquet` with exactly the requested columns (`index=False`); output exactly one ```python code block". Baseline failure labels: `no_code`, `timeout`, `runtime_error`, `no_output`, `wrong_result`. `check_passed` is `None` for arm A; `rounds` is 1. Correctness uses `frames_match(pl.read_parquet(out), task.reference(frames), ordered=task.ordered)`.

**Required tests:** sandbox returns stdout of `print(1)`; non-zero returncode for `raise SystemExit(3)`; `timed_out` for `while True: pass` with timeout 1; script cannot see parent env var `ANTHROPIC_API_KEY` (set it in the test with monkeypatch; script prints `os.environ.get(...)` → `None`). Runner: with `ScriptedGenerator` returning a correct pandas solution for s01 → `correct=True`; wrong solution → `failure="wrong_result"`; no code → `no_code`; exception → `runtime_error`.

### Task 7: Report + CLI (arm A)

**Files:** Create `bench/report.py`, `bench/cli.py`; Test `tests/bench/test_report.py`, `tests/bench/test_cli.py`

**Interfaces:**
```python
# bench/report.py
@dataclass(frozen=True)
class ArmTierStats: n: int; accuracy: float; first_shot_pass: float | None; mean_rounds: float; mean_output_tokens: float; mean_latency_s: float
@dataclass(frozen=True)
class Summary: stats: Mapping[tuple[str, str], ArmTierStats]   # (arm, tier) with tier "all" included
                failures: Mapping[str, Mapping[str, int]]      # arm -> label -> count
@dataclass(frozen=True)
class GateResult: passed: bool; checks: tuple[tuple[str, bool, str], ...]   # (name, ok, detail)
def summarize(outcomes: Sequence[TaskOutcome]) -> Summary
def evaluate_gate(summary: Summary) -> GateResult     # needs arms A and B; see Task 24 for thresholds
def render_markdown(summary: Summary, gate: GateResult | None) -> str
def write_outcomes(path: Path, outcomes) -> None; def read_outcomes(path: Path) -> list[TaskOutcome]  # JSONL
# bench/cli.py  —  python -m bench.cli run [--arms A,B,C] [--suite seed|all] [--tasks s01,m02] [--model M]
#                  [--out results/<timestamp>] [--offline SCRIPT_DIR] [--max-repairs 2]
def main(argv: Sequence[str] | None = None) -> int
```
`--offline DIR` uses `ScriptedGenerator` per task and arm, reading `DIR/<arm>/<task_id>.txt` (one or more model responses separated by a line `=====`). The CLI writes `outcomes.jsonl` and `report.md` in `--out`, prints the report path, and returns 0. Arms B/C raise `NotImplementedError("arm B lands in Task 22")` until Task 22.

`evaluate_gate` checks: (1) `acc(B, multi) - acc(A, multi) >= 0.15`; (2) `acc(B, single) >= acc(A, single) - 0.02`; (3) share of B outcomes with `check_passed` ≥ 0.99.

**Required tests:** summarize on hand-made outcomes computes per-tier and "all" accuracy; the gate passes and fails on constructed summaries; markdown contains a table row per arm; `write_outcomes`/`read_outcomes` round trip; CLI offline arm A on tasks s01,s02 with scripted correct answers writes a report showing 100% accuracy.

---

## Phase 1 — Parse and structural checks

### Task 8: Diagnostics + parse

**Files:** Create `lattice/diagnostics.py`, `lattice/parse.py`; Test `tests/test_diagnostics.py`, `tests/test_parse.py`

**Interfaces:**
```python
# diagnostics.py
@dataclass(frozen=True, order=True)
class Span: line: int; col: int                      # 1-based
class Severity(StrEnum): ERROR = "error"; WARNING = "warning"
@dataclass(frozen=True)
class Diagnostic:
    code: str; severity: Severity; span: Span; message: str; hint: str | None = None
    def render(self) -> str          # "3:5: error L201: 'x' is already bound (line 2) (hint: pick a new name)"
def span_of(node: ast.AST) -> Span   # Span(node.lineno, node.col_offset + 1); Span(1, 1) if absent
def has_errors(diags: Iterable[Diagnostic]) -> bool
def sort_key(d: Diagnostic) -> tuple[int, int, str]
def render_all(diags) -> str          # sorted, one per line
def render_for_llm(diags) -> str      # "- L201 at line 3, col 5: <message>. Fix: <hint>" per line, errors first
# parse.py
HEADER = "# lattice: v1"
@dataclass(frozen=True)
class ParseResult: module: ast.Module | None; diagnostics: tuple[Diagnostic, ...]; source: str
def parse(source: str) -> ParseResult
```
`parse`: strip a leading UTF-8 BOM, normalize `\r\n`/`\r` to `\n`; if the first line (rstripped) is not `HEADER` → error `L001` at 1:1 ("first line must be '# lattice: v1'") — still parse. `SyntaxError` → `L002` at `(e.lineno or 1, e.offset or 1)` with `e.msg`, `module=None`. `ParseResult.source` is the normalized text.

**Required tests:** valid header → no diagnostics; missing header → exactly one L001 and module not None; wrong version `# lattice: v2` → L001; syntax error → L002 with correct line, module None; CRLF + BOM source with header parses with no diagnostics and spans (`span_of` of the second statement) match LF version; `render` formats with and without hint; `render_for_llm` lists errors before warnings.

### Task 9: Catalog, profile, check context, orchestrator skeleton, spec clarifications

**Files:** Create `lattice/catalog.py`, `lattice/check/context.py`, stub `lattice/check/p4_types.py` + `lattice/check/p5_effects.py`; Replace `lattice/check/__init__.py`; Modify `docs/superpowers/specs/2026-09-26-lattice-design.md` (add §5.11 "Clarifications" with the five items from Global Constraints and adjust L303 wording); Test `tests/check/test_catalog.py`, `tests/check/test_context.py`

**catalog.py (exact content):**
```python
OPS_MODULE = "lattice.ops"; SCHEMAS_MODULE = "lattice.schemas"
@dataclass(frozen=True)
class OpShape:
    positional: tuple[str, ...]          # names of positional params, in order
    tables: int                          # how many leading positional params are tables
    required_kw: frozenset[str] = frozenset()
    optional_kw: frozenset[str] = frozenset()
    var_kw: bool = False                 # accepts arbitrary extra keywords (new columns / aggregates / renames)
OP_SHAPES: Mapping[str, OpShape] = {
  "load":   OpShape(("path",), 0, frozenset({"schema"})),
  "where":  OpShape(("table", "predicate"), 1),
  "derive": OpShape(("table",), 1, var_kw=True),
  "select": OpShape(("table",), 1, frozenset({"cols"})),
  "rename": OpShape(("table",), 1, var_kw=True),
  "group":  OpShape(("table",), 1, frozenset({"by"}), var_kw=True),
  "join":   OpShape(("left", "right"), 2, frozenset({"on"}), frozenset({"how"})),
  "sort":   OpShape(("table",), 1, frozenset({"by"}), frozenset({"desc"})),
  "limit":  OpShape(("table",), 1, frozenset({"n"})),
  "write":  OpShape(("table", "path"), 1),
}
TABLE_OPS = frozenset(OP_SHAPES); TERMINAL_OPS = frozenset({"write"}); IO_OPS = frozenset({"load", "write"})
EXPR_FUNCS = frozenset({"col", "when", "coalesce", "is_null"})
NAMESPACES = frozenset({"agg", "fn"})
TYPE_NAMES = frozenset({"Table", "Int", "Float", "Str", "Bool", "Date"})
IMPORTABLE = TABLE_OPS | EXPR_FUNCS | NAMESPACES | TYPE_NAMES | {"step"}
AGG_FUNCS = frozenset({"sum", "mean", "min", "max", "count"})
@dataclass(frozen=True)
class FnSig:
    params: tuple[frozenset[Scalar], ...]   # allowed scalar types per positional argument
    kwargs: Mapping[str, Scalar]            # required keyword args; values must be literals
    result: Scalar | None                   # None = same type as the first argument
N, S_, D = NUMERIC, frozenset({Scalar.STR}), frozenset({Scalar.DATE})
ANY = frozenset(Scalar)
FN_SIGS: Mapping[str, FnSig] = {
  "abs": FnSig((N,), {}, None), "round": FnSig((frozenset({Scalar.FLOAT}),), {"digits": Scalar.INT}, Scalar.FLOAT),
  "floor": FnSig((frozenset({Scalar.FLOAT}),), {}, Scalar.FLOAT), "ceil": FnSig((frozenset({Scalar.FLOAT}),), {}, Scalar.FLOAT),
  "log": FnSig((N,), {}, Scalar.FLOAT), "sqrt": FnSig((N,), {}, Scalar.FLOAT),
  "clip": FnSig((N,), {"lo": Scalar.FLOAT, "hi": Scalar.FLOAT}, None),
  "lower": FnSig((S_,), {}, Scalar.STR), "upper": FnSig((S_,), {}, Scalar.STR), "strip": FnSig((S_,), {}, Scalar.STR),
  "len": FnSig((S_,), {}, Scalar.INT), "contains": FnSig((S_,), {"sub": Scalar.STR}, Scalar.BOOL),
  "starts_with": FnSig((S_,), {"prefix": Scalar.STR}, Scalar.BOOL),
  "replace": FnSig((S_,), {"old": Scalar.STR, "new": Scalar.STR}, Scalar.STR),
  "concat": FnSig((S_, S_), {}, Scalar.STR),
  "year": FnSig((D,), {}, Scalar.INT), "month": FnSig((D,), {}, Scalar.INT), "day": FnSig((D,), {}, Scalar.INT),
  "date_diff": FnSig((D, D), {"unit": Scalar.STR}, Scalar.INT), "date_add": FnSig((D,), {"days": Scalar.INT}, Scalar.DATE),
  "to_date": FnSig((S_,), {"format": Scalar.STR}, Scalar.DATE),
  "to_int": FnSig((ANY,), {}, Scalar.INT), "to_float": FnSig((ANY,), {}, Scalar.FLOAT), "to_str": FnSig((ANY,), {}, Scalar.STR),
}
DATE_DIFF_UNITS = frozenset({"days"})
FN_ALIASES: Mapping[str, str] = {"length": "len", "strlen": "len", "trim": "strip", "lowercase": "lower",
  "uppercase": "upper", "startswith": "starts_with", "absolute": "abs", "ln": "log", "str": "to_str",
  "int": "to_int", "float": "to_float"}
OP_ALIASES: Mapping[str, str] = {"filter": "where", "mutate": "derive", "with_columns": "derive",
  "groupby": "group", "group_by": "group", "aggregate": "group", "merge": "join", "order_by": "sort",
  "head": "limit", "read_csv": "load", "save": "write", "columns": "select"}
```
(`clip` kwargs are Float; an Int literal is accepted for a Float kwarg.)

**profile.py:** already created in Task 1 (`Profile(schemas: Mapping[str, Schema], version: str = "v1")`). `lattice` must never import `bench`; the benchmark profile is built by `bench.datasets.bench_profile()` (Task 2).

**check/context.py:**
```python
@dataclass
class CheckContext:
    profile: Profile
    diagnostics: list[Diagnostic] = field(default_factory=list)
    types: dict[int, Type] = field(default_factory=dict)     # id(ast node) -> synthesized type
    def error(self, code: str, node: ast.AST | Span, message: str, hint: str | None = None) -> None
    def warn(self, code: str, node: ast.AST | Span, message: str, hint: str | None = None) -> None
    def error_count(self) -> int
    def set_type(self, node: ast.AST, t: Type) -> Type      # stores and returns t
```
**check/__init__.py:**
```python
@dataclass(frozen=True)
class CheckedProgram: module: ast.Module; source: str; types: Mapping[int, Type]; steps: Mapping[str, StepT]; profile: Profile
@dataclass(frozen=True)
class CheckResult:
    program: CheckedProgram | None; diagnostics: tuple[Diagnostic, ...]
    @property
    def ok(self) -> bool       # program is not None
def check_source(source: str, profile: Profile) -> CheckResult
```
Orchestration: `parse` → if module None return; run P1; if P1 added errors, stop (return sorted diagnostics); else run P2, P3, P4, P5 in order (each module exposes `run(module, ctx)`; P4's `run` returns `Mapping[str, StepT]`). Passes that don't exist yet are imported lazily and skipped only via a module-level `PASSES` list that later tasks extend — simplest: Task 9 wires P1–P3 slots with `try: from . import p4_types` guarded by `ImportError` **not allowed**; instead Task 9 creates stub modules `p4_types.py` / `p5_effects.py` whose `run` returns `{}` / `None`; Tasks 14/15 replace them. Diagnostics sorted by `sort_key`. `program` is None if any ERROR.

**Required tests:** catalog invariants (every OP_ALIASES value in TABLE_OPS; every FN_ALIASES value in FN_SIGS; IMPORTABLE contains `step`); context error/warn record severity and span (from node and from Span); `check_source` on a minimal valid program `# lattice: v1\nfrom lattice.ops import load, write\nfrom lattice.schemas import Orders\nx = load("orders.csv", schema=Orders)\nwrite(x, "out.parquet")\n` returns ok (with stubs).

### Task 10: P1 — node whitelist and step signature shape

**Files:** Create `lattice/check/astutil.py`, `lattice/check/p1_nodes.py`; Test `tests/check/test_p1_nodes.py`

**Interfaces:**
```python
# astutil.py
def is_table_annotation(node: ast.expr | None) -> bool      # Name "Table" or Subscript(Name "Table", Name)
def annotation_schema_name(node) -> str | None              # "Orders" for Table[Orders]
def is_valid_annotation(node) -> bool                       # scalar type Name, Name "Table", or Table[Name]
def step_param_kinds(fn: ast.FunctionDef) -> tuple[tuple[str, Literal["table", "scalar"]], ...]
def steps_of(module: ast.Module) -> dict[str, ast.FunctionDef]
def call_name(call: ast.Call) -> str | None                 # "where" for Name func; "fn.upper"/"agg.sum" for namespace attrs
# p1_nodes.py
def run(module: ast.Module, ctx: CheckContext) -> None
```
Rules (code — trigger — message gist — hint):
- `L102` ImportFrom after any other statement — "imports must come first".
- `L103` `import x`, or ImportFrom from a module other than `lattice.ops`/`lattice.schemas`, or relative import.
- `L104` import alias (`as`).
- `L112` imported name unknown: from ops not in `IMPORTABLE` (hint canonical via OP_ALIASES/FN_ALIASES if alias); from schemas not in `ctx.profile.schemas` (hint: available schemas).
- Module statements allowed: ImportFrom, `Assign` with exactly one `Name` target (`L108` otherwise, incl. tuple unpacking / multiple targets), `Expr`, `FunctionDef` (step).
- `L110` FunctionDef whose decorator list is not exactly `[Name("step")]`; `L111` nested FunctionDef.
- `L601` step signature: posonly/kwonly/vararg/kwarg/defaults present; any param without annotation or with invalid annotation; return annotation missing, not a table annotation.
- `L602` step body: must be ≥1 `Assign` followed by exactly one final `Return` whose value is a `Name`; any other statement kind or a non-final Return.
- Expressions (recursively inside Assign values, Expr values, Return values): allowed Constant, Name, List/Tuple (recurse), Call (func Name, or Attribute on Name `agg`/`fn`; `L105` method call for any other Attribute func; `L101` for Starred args or `**kwargs`), Attribute only on Name `agg`/`fn` (`L106` otherwise), BinOp with op in {Add, Sub, Mult, Div, Mod, BitAnd, BitOr} (`L101` other ops), UnaryOp USub/Invert (`L107` for `not`, hint `~`; `L101` for UAdd), Compare with exactly one op (`L109` chained) in {Eq, NotEq, Lt, LtE, Gt, GtE} (`L113` for `is`/`is not`/`in`/`not in`, hint `is_null(...)`/`|`).
- Banned nodes with friendly codes (do **not** recurse into them): Lambda `L120`; ListComp/SetComp/DictComp/GeneratorExp `L121`; IfExp `L122` (hint `when(cond, then=..., otherwise=...)`); Subscript `L123` (hint `col("name")`); JoinedStr `L124` (hint `fn.concat`); NamedExpr `L125`; AugAssign/AnnAssign `L126`; For/While/If/AsyncFor `L127`; ClassDef `L128`; Try/With/Raise/Assert/Delete/Pass/Break/Continue `L129`; Global/Nonlocal `L130`; Await/Yield/YieldFrom/AsyncFunctionDef `L131`; BoolOp `L107` (hint `&`/`|`); anything else `L101` "'<NodeType>' is not allowed in a Lattice program".
- Annotations are validated only via `is_valid_annotation`, never walked as expressions.

**Required tests (one per code at minimum):** valid pipeline and valid step produce zero diagnostics; each of L101–L131 and L601/L602 triggered by a minimal snippet with the expected code and span line; `df = [x for x in y]` yields only L121 (no cascade); `a = b if c else d` → L122 with hint mentioning `when`; `x = t.filter(...)` → L105; `x = load(...) and y` → L107; a program with three independent P1 violations yields three diagnostics.

### Task 11: P2 — names, single assignment, step purity

**Files:** Create `lattice/check/p2_names.py`; Test `tests/check/test_p2_names.py`

**Interfaces:** `def run(module: ast.Module, ctx: CheckContext) -> None`

Model: `globals_` scope (imports + steps, visible everywhere), `tables` scope (module-level assignments; parent `globals_`), one scope per step (parent `globals_`, **not** `tables`). Each binding has kind `import|step|table|param|local`, node, `used` flag. Name uses = every `ast.Name` found by `ast.walk` over a statement's value (Assign value, Expr value, Return value) and over step param/return annotations.

Lookup order for an unresolved use: (1) inside step `f`, name == `f` → `L604` "step 'f' cannot call itself; recursion is not allowed"; (2) inside a step, name is any module-level table (bound earlier or later) → `L603` "step bodies cannot read module-level table 'x'" hint "pass it as a parameter"; (3) name is bound later in the same scope (or is a module step defined later) → steps: `L604` "step 'x' is used before it is defined" hint "move the definition above line N"; tables/locals: `L202` "'x' is used before it is assigned (line N)"; (4) otherwise `L203` "unknown name 'x'" — hint: "add it to 'from lattice.ops import ...'" if in IMPORTABLE, "add it to 'from lattice.schemas import ...'" if a profile schema, canonical name if in OP_ALIASES/FN_ALIASES, else `difflib.get_close_matches` over visible names.

Binding rules: name is a Python builtin (`dir(builtins)`) → `L205`; name already visible (any enclosing scope, or for module-level steps/tables either module scope) → if existing kind is `import` or `step` and new kind is not → `L205` "'x' shadows an imported name/step"; else `L201` "'x' is already bound (line N); every name is bound exactly once" hint "use a new descriptive name". First binding wins. Duplicate import → `L201`.

After the pass: unused import → **error** `L306` "imported name 'x' is never used"; unused table/local/step/param → **warning** `L204` "'x' is never used".

**Required tests:** rebinding `df` twice → L201 on second line; step param shadowing import `where` → L205; `sum = derive(...)` → L205; use before assignment → L202; unknown `pandas` → L203; missing import of `where` → L203 with hint mentioning `from lattice.ops import`; `filter` used → L203 hint "where"; step body referencing module table → L603; recursive step → L604; calling step defined later → L604; unused import → L306 error; unused table → L204 warning; valid step program → no diagnostics; nested step call to earlier step allowed.

### Task 12: P3 — canonical form

**Files:** Create `lattice/check/p3_canon.py`; Test `tests/check/test_p3_canon.py`

**Interfaces:** `def run(module: ast.Module, ctx: CheckContext) -> None`

A *unit call* is a Call whose func is a Name in `TABLE_OPS` or in the module's steps. For every module-level Assign/Expr and every step-body Assign:
- value not a unit call → `L302` "each statement must call exactly one table op or step".
- `Expr` statement whose unit is not in TERMINAL_OPS → `L302` "the result of 'x' must be assigned to a name".
- any unit call nested anywhere inside the statement's call (via `ast.walk`, excluding the top call) → `L301` "table ops cannot be nested; assign the inner result to its own name first".
- op call shape vs `OP_SHAPES[op]`: positional count mismatch → `L303` "'op' takes N positional argument(s) (names), got M" hint "pass other arguments by keyword"; each table-position arg must be a `Name` (non-Name, non-unit → `L301` "table argument must be a name"); missing required keyword → `L303`; unexpected keyword (not required/optional and not var_kw) → `L303`.
- step call shape: positional args == table params in order, each a Name; keywords == exactly the scalar params → else `L303`.
- `L304` no-op steps: `where` predicate is a Constant; `derive`/`rename` with no keywords; `select` with `cols=[]`; `group` with `by=[]` and no aggregate keywords.
- `L701`: any `when(...)` call (anywhere) whose `then` keyword value is itself a `when(...)` call — "nest when() only in 'otherwise'".

**Required tests:** chained `where(derive(t, a=col("x")), ...)` → L301 at inner call; `x = col("a")` → L302; bare `derive(t, a=...)` statement → L302; `where(t, pred=col("a") > 1)` → L303 (positional expected); `select(t)` → L303 missing `cols`; `sort(t, by=["a"], reverse=True)` → L303 unexpected; `where(t, True)` → L304; `derive(t)` → L304; nested when in then → L701; step called with scalar positionally → L303; valid program → none.

## Task Index (full step-by-step bodies still to be written — see status note)

**Phase 0 — Benchmark baseline**
1. Scaffold (`pyproject.toml`, `.gitignore`, package skeleton) + `lattice/typesys.py` `Scalar`, `Schema`, `unify()`.
2. `bench/datasets.py`: deterministic `customers.csv` / `orders.csv` / `products.csv` (seeded, with nulls and `qty == 0` rows); schemas `Customers`, `Orders`, `Products`.
3. `bench/compare.py`: `frames_match(actual, expected, *, ordered, rtol=1e-6) -> tuple[bool, str]`: normalises numeric columns to Float64 and dates/other columns to strings, compares nulls as equal, and ignores row order unless the task says order matters.
4. `bench/tasks.py` + `bench/seed_tasks.py`: `Task(id, tier, prompt, inputs, reference, ordered)`; 30 tasks (10 single, 12 multi, 8 join) with polars references.
5. `lattice/llm/client.py`: `Generator` protocol, `ClaudeGenerator` (`claude-opus-5`, cached system block, refusal fallback beta, reads token usage), `ScriptedGenerator`, `extract_code()`.
6. `bench/sandbox.py` + baseline prompt + `run_baseline()` (isolated subprocess with a timeout, output to `out.parquet`).
7. `bench/report.py` + `bench/cli.py` for arm A; record baseline numbers (live run only with user approval).

**Phase 1 — Parse and structural checks**
8. `lattice/diagnostics.py` + `lattice/parse.py` (header `L001`, syntax `L002`, CRLF/BOM tolerant).
9. `lattice/catalog.py`, `lattice/profile.py`, `lattice/check/context.py`, `check_source()` skeleton; write the spec clarifications into the spec.
10. P1 node whitelist (`L101–L131`, `L601`, `L602`) + `check/astutil.py`.
11. P2 names (`L201–L205`, `L306`, `L603`, `L604`).
12. P3 canonical form (`L301–L304`, `L701`).

**Phase 2 — Types, IR, execution**
13. Checker types + `check/exprs.py` (`col`, `when`, `coalesce`, `is_null`, `fn.*`, operators; `L401–L413`, `L702`).
14. P4 op schema transfer functions + step signatures (`L405`, `L414–L419`, `L605`).
15. P5 effects (`L501–L505`) + multi-error test (5 errors → 5 diagnostics) + a 300-step program checks in under 1 s.
16. `lattice/canon.py`: round trip is a fixpoint.
17. `lattice/ir.py`: lowering, with `@step` calls inlined as prefixed sub-graphs and scalar parameters substituted.
18. `runtime/exprs.py`: polars compilation, with guards that produce `R301`–`R303`.
19. `runtime/ops.py` + `runtime/exec.py` (runtime schema assertion `E901`, `R101`, row/time limits) + Review Focus tests 1, 2, 4.
20. Differential tests (Lattice solutions vs. polars references) + one test per spec §5.7 row.

**Phase 3 — LLM loop and gate**
21. Lattice system prompt (rules, catalog, examples) + `synthesize()` repair loop (`L000` when there is no code block).
22. `run_lattice()`; arms B and C in the CLI; failure taxonomy (L201 → shadowing, L202/L604 → sequencing, L301/L302 → chaining, L4xx → schema).
23. Grow the suite to ≥100 tasks (`bench/more_tasks.py`), keeping the tier ratios.
24. Gate evaluation (+15 points on multi-step, no regression on single-step, ≥99% pass within 2 repairs); live run with approval; write the results doc.
