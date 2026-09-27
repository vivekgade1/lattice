"""API benchmark prompts (spec 2026-09-27 §5.5): both arms get the same
information, the same three worked examples and equivalent removable advice,
within ±30% of each other's length; the Lattice examples check clean and both
arms' example solutions answer the same requests identically."""

from __future__ import annotations

import ast
import importlib.util
import re
import sqlite3
from pathlib import Path

import pytest

from bench.api_datasets import TOKENS, USER_IDS, api_profile, build_db, schema_description
from bench.api_model import ApiTask, HttpCase
from bench.api_runner import CaseResults, run_cases_fastapi, run_cases_lattice
from bench.tasks import suite
from lattice.catalog import (
    API_AGG_FUNCS,
    API_ERRORS,
    API_FN_FUNCS,
    API_OP_SHAPES,
    API_SUCCESS_KINDS,
)
from lattice.check import check_source
from lattice.llm.api_prompt import (
    ARM_NOTES,
    BASELINE_ADVICE,
    BASELINE_EXAMPLES,
    ERROR_BODY_NAMES,
    EXAMPLE_PROBLEMS,
    LATTICE_ADVICE,
    LATTICE_EXAMPLES,
    api_baseline_system_prompt,
    api_lattice_system_prompt,
    api_task_message,
)

KIT_PATH = Path(__file__).resolve().parents[2] / "bench" / "api_kit" / "platform_kit.py"
_FENCE_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)
PROMPTS = {"lattice": api_lattice_system_prompt, "baseline": api_baseline_system_prompt}


def _example_blocks(prompt: str) -> list[str]:
    """The ```python blocks of the Examples section only."""
    examples = prompt.split("## Examples", 1)[1].split("## Output format", 1)[0]
    return _FENCE_RE.findall(examples)


# ------------------------------------------------------------------ shape --


@pytest.mark.parametrize("arm", sorted(PROMPTS))
@pytest.mark.parametrize("hints", [True, False])
def test_prompts_are_deterministic(arm: str, hints: bool) -> None:
    assert PROMPTS[arm](hints=hints) == PROMPTS[arm](hints=hints)


@pytest.mark.parametrize("hints", [True, False])
def test_budget_parity_within_30_percent(hints: bool) -> None:
    lattice = len(api_lattice_system_prompt(hints=hints))
    baseline = len(api_baseline_system_prompt(hints=hints))
    assert 0.7 <= baseline / lattice <= 1.3, (lattice, baseline)


def test_hints_toggle_only_the_advice() -> None:
    for arm, advice in (("lattice", LATTICE_ADVICE), ("baseline", BASELINE_ADVICE)):
        full, bare = PROMPTS[arm](hints=True), PROMPTS[arm](hints=False)
        assert "## Advice" in full and "## Advice" not in bare
        for bullet in advice:
            assert bullet in full
            assert bullet not in bare
        # the reference and examples are untouched
        assert bare == full.replace("## Advice\n\n" + "\n".join(advice) + "\n\n", "")


def test_advice_has_equal_substance_in_both_arms() -> None:
    assert len(LATTICE_ADVICE) == len(BASELINE_ADVICE) == 4
    topics = ("- Ownership:", "- Bounds:", "- Errors:", "- Lists and related data:")
    for topic, lat, base in zip(topics, LATTICE_ADVICE, BASELINE_ADVICE, strict=True):
        assert lat.startswith(topic) and base.startswith(topic)
        assert 0.7 <= len(base) / len(lat) <= 1.3, topic
    lattice = "\n".join(LATTICE_ADVICE)
    for needle in ("owned(t, by=user)", "owner=user", "404", "ge=, le=", "max_len=", "422"):
        assert needle in lattice, needle
    for needle in ("return annotation", "else_=NotFound", "page(", "join", "group"):
        assert needle in lattice, needle
    baseline = "\n".join(BASELINE_ADVICE)
    for needle in ("WHERE", "customer_id = ?", "404", "Field(ge=, le=)", "max_length", "422"):
        assert needle in baseline, needle
    for needle in ("None", "500", "409", "JOIN", "GROUP BY", "LIMIT ? OFFSET ?", "le <= 100"):
        assert needle in baseline, needle
    # owned-through tables and cross-customer aggregates are described in both arms
    for needle in (
        "through a parent",
        "groups by, filters on and aggregates only",
        '"may be aggregated publicly"',
    ):
        assert needle in lattice and needle in baseline, needle


@pytest.mark.parametrize("arm", sorted(PROMPTS))
def test_intro_wording_is_neutral(arm: str) -> None:
    text = PROMPTS[arm]().lower()
    for phrase in ("whitelist", "rejected", "before it ever runs", "sandbox", "attack"):
        assert phrase not in text, phrase
    assert not re.search(r"\blegal\b", text)


@pytest.mark.parametrize("arm", sorted(PROMPTS))
def test_output_format_is_one_python_block(arm: str) -> None:
    prompt = PROMPTS[arm]()
    assert prompt.rstrip().endswith("no prose, no other code blocks.")
    assert "Respond with exactly one ```python code block" in prompt


def test_lattice_prompt_documents_the_api_surface() -> None:
    prompt = api_lattice_system_prompt(hints=False)
    assert "# lattice: v1" in prompt and "@endpoint(" in prompt
    assert "user: CurrentUser" in prompt
    for op in API_OP_SHAPES:
        assert f"`{op}(" in prompt, op
    for op in ("where", "derive", "select", "rename", "group", "join", "sort", "limit"):
        assert f"`{op}(" in prompt, op
    for kind in API_SUCCESS_KINDS:
        assert f"`{kind}`" in prompt, kind
    for err, status in API_ERRORS.items():
        assert f'`{err}` {status} (`"{ERROR_BODY_NAMES[err]}"`)' in prompt
    for fn in API_FN_FUNCS:
        assert f"`fn.{fn}(" in prompt, fn
    for agg in API_AGG_FUNCS:
        assert f"`agg.{agg}`" in prompt, agg
    assert "`agg.at_max(x, of=o)`" in prompt
    assert "owned through a parent" in prompt and "`zip`" in prompt
    for constraint in ("Int(ge=, le=)", "Str(min_len=, max_len=)", "OneOf(", "Bool", "Date"):
        assert constraint in prompt, constraint
    for rule in ("Never nest calls", "new name", "exactly one `return", "No imports"):
        assert rule in prompt, rule
    for status in ("401", "403", "422", "201", "204"):
        assert status in prompt, status


def test_baseline_prompt_documents_the_kit_and_the_same_conventions() -> None:
    prompt = api_baseline_system_prompt(hints=False)
    assert "from platform_kit import app, get_db, current_user, require_role" in prompt
    for status in ("401", "403", "422", "201", "204"):
        assert status in prompt, status
    for err, status in API_ERRORS.items():
        assert f'`{err}` {status} (`"{ERROR_BODY_NAMES[err]}"`)' in prompt
    assert '{"error": "validation", "detail": [...]}' in prompt
    assert '{"items": [...], "limit": n, "offset": n}' in prompt
    assert "The kit only authenticates" in prompt
    lattice = api_lattice_system_prompt(hints=False).replace("\n", " ")
    baseline = prompt.replace("\n", " ")
    for shared in (
        '{"items": [...], "limit": n, "offset": n}',
        '{"error": "validation", "detail": [...]}',
        '{"error": "unauthorized"}',
        "the others come from the query string for GET/DELETE and from the JSON body",
        "Admin",
    ):
        assert shared in lattice, shared
        assert shared in baseline, shared


# --------------------------------------------------------------- examples --


def test_both_arms_work_the_same_three_problems() -> None:
    assert len(EXAMPLE_PROBLEMS) == 3
    for arm, solutions in (("lattice", LATTICE_EXAMPLES), ("baseline", BASELINE_EXAMPLES)):
        prompt = PROMPTS[arm]()
        positions = [prompt.index(p) for p in EXAMPLE_PROBLEMS]
        assert positions == sorted(positions)
        blocks = _example_blocks(prompt)
        assert [b.rstrip() for b in blocks] == [s.rstrip() for s in solutions]


@pytest.mark.parametrize("index", range(3))
def test_lattice_examples_check_clean(index: int) -> None:
    result = check_source(LATTICE_EXAMPLES[index], api_profile())
    assert result.ok
    assert result.diagnostics == ()


@pytest.mark.parametrize("index", range(3))
def test_baseline_examples_parse_and_import_only_allowed_modules(index: int) -> None:
    tree = ast.parse(BASELINE_EXAMPLES[index])
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    assert roots <= {"sqlite3", "typing", "fastapi", "pydantic", "platform_kit"}


def _normalize_route(text: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", text)


def test_examples_do_not_overlap_the_task_suite() -> None:
    routes = [
        "GET /addresses",
        "PATCH /products/{}/stock",
        "POST /products/{}/reviews",
    ]
    for task in suite("api"):
        spec = _normalize_route(task.spec)
        for route in routes:
            assert not re.search(re.escape(route) + r"(?![\w/{])", spec), (task.id, route)


# ------------------------------------------------------------ task message --


def _task(spec: str = "Endpoint: GET /widgets (Public) lists widgets.") -> ApiTask:
    return ApiTask(id="t01", family="control", spec=spec, cases=())


def test_task_message_has_spec_schema_and_one_arm_line() -> None:
    task = _task()
    lattice = api_task_message(task, "lattice")
    baseline = api_task_message(task, "baseline")
    for message in (lattice, baseline):
        assert message.startswith(task.spec)
        assert schema_description() in message
    assert lattice.endswith(ARM_NOTES["lattice"])
    assert baseline.endswith(ARM_NOTES["baseline"])
    assert "Lattice runtime" in ARM_NOTES["lattice"]
    assert "platform_kit.app" in ARM_NOTES["baseline"]
    # nothing else differs
    lat_lines, base_lines = lattice.splitlines(), baseline.splitlines()
    assert lat_lines[:-1] == base_lines[:-1]
    assert lat_lines[-1] != base_lines[-1]
    assert api_task_message(task, "lattice") == lattice  # deterministic


def test_task_message_rejects_an_unknown_arm() -> None:
    with pytest.raises(ValueError):
        api_task_message(_task(), "pandas")  # type: ignore[arg-type]


# ----------------------------------------- identical HTTP responses per arm --


def _cases(db: Path) -> list[ApiTask]:
    """One task per example with full expected responses computed from the DB."""
    conn = sqlite3.connect(db)
    try:
        alice = USER_IDS["alice"]
        addresses = [
            dict(zip(("id", "customer_id", "line1", "city", "zip"), row, strict=True))
            for row in conn.execute(
                "SELECT id, customer_id, line1, city, zip FROM addresses"
                " WHERE customer_id = ? ORDER BY id",
                (alice,),
            )
        ]
        product = conn.execute("SELECT id, name FROM products ORDER BY id LIMIT 1 OFFSET 2")
        pid, pname = product.fetchone()
        ordered = {
            r[0]
            for r in conn.execute(
                "SELECT i.product_id FROM order_items i JOIN orders o ON o.id = i.order_id"
                " WHERE o.customer_id = ?",
                (alice,),
            )
        }
        reviewed = {
            r[0]
            for r in conn.execute("SELECT product_id FROM reviews WHERE customer_id = ?", (alice,))
        }
        all_products = [r[0] for r in conn.execute("SELECT id FROM products ORDER BY id")]
        fresh = min(ordered - reviewed)
        unordered = min(set(all_products) - ordered)
        next_review_id = conn.execute("SELECT MAX(id) FROM reviews").fetchone()[0] + 1
        dup_handle, dup_pid = None, None
        for handle in ("alice", "bob", "carol"):
            row = conn.execute(
                "SELECT r.product_id FROM reviews r WHERE r.customer_id = ? AND EXISTS ("
                " SELECT 1 FROM order_items i JOIN orders o ON o.id = i.order_id"
                " WHERE o.customer_id = r.customer_id AND i.product_id = r.product_id)"
                " ORDER BY r.product_id LIMIT 1",
                (USER_IDS[handle],),
            ).fetchone()
            if row is not None:
                dup_handle, dup_pid = handle, row[0]
                break
        assert dup_handle is not None, "fixture DB has no reviewed-and-ordered product"
    finally:
        conn.close()

    def case(name: str, method: str, path: str, handle: str | None, status: int, **kw):
        token = TOKENS[handle] if handle is not None else None
        return HttpCase(
            name=name,
            kind="happy",
            family="control",
            method=method,
            path=path,
            token=token,
            expect_status=status,
            **kw,
        )

    missing = {"error": "not_found", "detail": "product"}
    review_body = {"rating": 5, "body": "Solid and well made."}
    return [
        ApiTask(
            "ex1",
            "control",
            EXAMPLE_PROBLEMS[0],
            (
                case(
                    "page",
                    "GET",
                    "/addresses",
                    "alice",
                    200,
                    query={"limit": 1, "offset": 0},
                    expect_json={"items": addresses[:1], "limit": 1, "offset": 0},
                    ordered=True,
                ),
                case(
                    "all",
                    "GET",
                    "/addresses",
                    "alice",
                    200,
                    expect_json={"items": addresses, "limit": 20, "offset": 0},
                    ordered=True,
                ),
                case(
                    "big_limit",
                    "GET",
                    "/addresses",
                    "alice",
                    422,
                    query={"limit": 500},
                    expect_json={"error": "validation"},
                ),
                case("anon", "GET", "/addresses", None, 401, expect_json={"error": "unauthorized"}),
            ),
        ),
        ApiTask(
            "ex2",
            "control",
            EXAMPLE_PROBLEMS[1],
            (
                case(
                    "set",
                    "PATCH",
                    f"/products/{pid}/stock",
                    "admin",
                    200,
                    json={"stock": 7},
                    expect_json={"id": pid, "name": pname, "stock": 7},
                    after_sql=f"SELECT stock FROM products WHERE id = {pid}",
                    after_expect=[[7]],
                ),
                case(
                    "missing",
                    "PATCH",
                    "/products/9999/stock",
                    "admin",
                    404,
                    json={"stock": 7},
                    expect_json=missing,
                ),
                case(
                    "too_big",
                    "PATCH",
                    f"/products/{pid}/stock",
                    "admin",
                    422,
                    json={"stock": 10001},
                    expect_json={"error": "validation"},
                ),
                case(
                    "customer",
                    "PATCH",
                    f"/products/{pid}/stock",
                    "alice",
                    403,
                    json={"stock": 7},
                    expect_json={"error": "forbidden"},
                ),
            ),
        ),
        ApiTask(
            "ex3",
            "control",
            EXAMPLE_PROBLEMS[2],
            (
                case(
                    "created",
                    "POST",
                    f"/products/{fresh}/reviews",
                    "alice",
                    201,
                    json=review_body,
                    expect_json={
                        "id": next_review_id,
                        "product_id": fresh,
                        "customer_id": alice,
                        **review_body,
                    },
                    after_sql=f"SELECT customer_id, product_id FROM reviews "
                    f"WHERE id = {next_review_id}",
                    after_expect=[[alice, fresh]],
                ),
                case(
                    "no_product",
                    "POST",
                    "/products/9999/reviews",
                    "alice",
                    404,
                    json=review_body,
                    expect_json=missing,
                ),
                case(
                    "not_ordered",
                    "POST",
                    f"/products/{unordered}/reviews",
                    "alice",
                    403,
                    json=review_body,
                    expect_json={"error": "forbidden", "detail": "product not ordered"},
                    after_sql="SELECT COUNT(*) FROM reviews",
                    after_expect=[[next_review_id - 1]],
                ),
                case(
                    "duplicate",
                    "POST",
                    f"/products/{dup_pid}/reviews",
                    dup_handle,
                    409,
                    json=review_body,
                    expect_json={"error": "conflict", "detail": "already reviewed"},
                ),
                case(
                    "bad_rating",
                    "POST",
                    f"/products/{fresh}/reviews",
                    "alice",
                    422,
                    json={"rating": 6, "body": "x"},
                    expect_json={"error": "validation"},
                ),
            ),
        ),
    ]


@pytest.fixture(scope="module")
def example_tasks(tmp_path_factory: pytest.TempPathFactory) -> list[ApiTask]:
    db = tmp_path_factory.mktemp("api_db") / "commerce.db"
    build_db(db)
    return _cases(db)


def _assert_all_pass(results: CaseResults, task: ApiTask) -> None:
    assert results.started, results.start_error
    assert [c.name for c in results.cases] == [c.name for c in task.cases]
    failed = [(c.name, c.reason) for c in results.cases if not c.passed]
    assert not failed, failed


@pytest.fixture(scope="module")
def fastapi_results(
    example_tasks: list[ApiTask], tmp_path_factory: pytest.TempPathFactory
) -> list[CaseResults]:
    return [
        run_cases_fastapi(
            BASELINE_EXAMPLES[i],
            tmp_path_factory.mktemp(f"fastapi_ex{i}"),
            task,
            db_builder=build_db,
            kit_path=KIT_PATH,
        )
        for i, task in enumerate(example_tasks)
    ]


@pytest.mark.parametrize("index", range(3))
def test_fastapi_examples_answer_the_example_requests(
    index: int, example_tasks: list[ApiTask], fastapi_results: list[CaseResults]
) -> None:
    _assert_all_pass(fastapi_results[index], example_tasks[index])


_RUNTIME_MISSING = importlib.util.find_spec("lattice.api.runtime") is None


@pytest.mark.skipif(
    _RUNTIME_MISSING,
    reason="lattice.api.runtime is not importable yet (the Lattice API runtime is being "
    "built separately); the Lattice/FastAPI response comparison runs once it lands",
)
@pytest.mark.parametrize("index", range(3))
def test_lattice_examples_answer_like_the_fastapi_examples(
    index: int,
    example_tasks: list[ApiTask],
    fastapi_results: list[CaseResults],
    tmp_path: Path,
) -> None:
    results = run_cases_lattice(
        LATTICE_EXAMPLES[index],
        tmp_path,
        example_tasks[index],
        db_builder=build_db,
        profile=api_profile(),
    )
    _assert_all_pass(results, example_tasks[index])
    lattice_statuses = [(c.name, c.status) for c in results.cases]
    fastapi_statuses = [(c.name, c.status) for c in fastapi_results[index].cases]
    assert lattice_statuses == fastapi_statuses


def test_implicit_view_gives_both_arms_the_same_implicit_message() -> None:
    """Spec §5.6: under --implicit both arms' task message carries the implicit spec
    (never the explicit one) and still differs only in the arm line."""
    implicit = [t for t in suite("api") if t.implicit_spec is not None]
    assert implicit
    for task in implicit:
        view = task.implicit_view()
        lattice = api_task_message(view, "lattice")
        baseline = api_task_message(view, "baseline")
        assert lattice.startswith(task.implicit_spec) and baseline.startswith(task.implicit_spec)
        assert task.spec not in lattice and task.spec not in baseline
        assert lattice.splitlines()[:-1] == baseline.splitlines()[:-1]
