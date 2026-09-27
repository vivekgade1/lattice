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
    for needle in ("accessible(t, by=user)", "owner=user", "404", "ge=, le=", "max_len=", "422"):
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


# ------------------------------------------------ loop constructs and Part B --


def test_both_arms_document_row_by_row_work_formats_and_uniqueness() -> None:
    from lattice.catalog import WIN_FUNCS
    from lattice.llm.api_prompt import _BASELINE_LOOPS_SECTION, _LATTICE_LOOPS_SECTION

    for hints in (True, False):  # reference, not removable advice
        assert _LATTICE_LOOPS_SECTION in api_lattice_system_prompt(hints=hints)
        assert _BASELINE_LOOPS_SECTION in api_baseline_system_prompt(hints=hints)
    assert 0.7 <= len(_BASELINE_LOOPS_SECTION) / len(_LATTICE_LOOPS_SECTION) <= 1.3
    lattice, baseline = _LATTICE_LOOPS_SECTION, _BASELINE_LOOPS_SECTION
    for construct in ("`window(", "`traverse(", "`for_each(", "`Email(max_len=)`"):
        assert construct in lattice, construct
    for name in WIN_FUNCS:
        assert f"`win.{name}(" in lattice, name
    for needle in ('pattern=r"', "unique_or=", "PUBLIC = ", "cols=PUBLIC"):
        assert needle in lattice, needle
    for needle in ("ROW_NUMBER()", "LAG(", "ROWS BETWEEN", "WITH RECURSIVE", "MIN(depth)"):
        assert needle in baseline, needle
    for needle in ("db.commit()", "IntegrityError", 'Field(pattern=r"', "PUBLIC = "):
        assert needle in baseline, needle
    # the same facts in both arms
    for needle in ("at-least-once", "at most 100", "409", "422", "smallest", "key"):
        assert needle.replace("at most 100", "100") in lattice and needle in baseline, needle


def test_api_loop_examples_check_clean() -> None:
    from lattice.llm.api_prompt import (
        _LATTICE_LOOPS_SECTION,
        API_FOR_EACH_EXAMPLE,
        API_LIST_EXAMPLE,
        API_TRAVERSE_EXAMPLE,
        API_UNIQUE_EXAMPLE,
        API_WINDOW_EXAMPLE,
    )
    from lattice.profile import ApiProfile, NativeSig, Profile
    from lattice.typesys import Scalar, Schema

    for example in (
        API_WINDOW_EXAMPLE,
        *API_TRAVERSE_EXAMPLE,
        API_FOR_EACH_EXAMPLE,
        API_UNIQUE_EXAMPLE,
        *API_LIST_EXAMPLE,
    ):
        assert f"`{example}`" in _LATTICE_LOOPS_SECTION, example
    bench_api = api_profile().api
    assert bench_api is not None
    users = Schema.from_pairs(
        [("id", Scalar.INT), ("email", Scalar.STR), ("name", Scalar.STR),
         ("role", Scalar.STR), ("token", Scalar.STR)],
        keys=[("id",)],
        private=["token"],
    )  # fmt: skip
    categories = Schema.from_pairs(
        [("id", Scalar.INT), ("parent_id", Scalar.INT), ("name", Scalar.STR)],
        keys=[("id",)],
        nullable=["parent_id"],
    )
    profile = Profile(
        schemas={},
        api=ApiProfile(
            tables={"Users": users, "Orders": bench_api.tables["Orders"], "Categories": categories},
            table_names={"Users": "users", "Orders": "orders", "Categories": "categories"},
            roles=bench_api.roles,
            natives={"send_welcome": NativeSig((Scalar.STR,), effect=True)},
        ),
    )
    source = f"""# lattice: v1
{API_LIST_EXAMPLE[0]}


@endpoint("GET", "/orders/numbered", auth=Customer)
def numbered_orders(user: CurrentUser) -> Page:
    orders = fetch(Orders)
    visible = owned(orders, by=user)
    {API_WINDOW_EXAMPLE}
    result = page(numbered, limit=100)
    return result


@endpoint("GET", "/categories/{{category_id}}/tree", auth=Public)
def category_tree(category_id: Int(ge=1)) -> Page:
    categories = fetch(Categories)
    {API_TRAVERSE_EXAMPLE[0]}
    {API_TRAVERSE_EXAMPLE[1]}
    result = page(tree, limit=100)
    return result


@endpoint("POST", "/signup", auth=Public)
def signup(
    email: Email(max_len=255),
    name: Str(min_len=1, max_len=50),
    token: Str(max_len=64, pattern=r"[A-Za-z0-9]{{16,64}}"),
) -> Created | Conflict:
    created = insert(
        Users, email=email, name=name, role="Customer", token=token, {API_UNIQUE_EXAMPLE}
    )
    {API_FOR_EACH_EXAMPLE}
    shown = {API_LIST_EXAMPLE[1]}
    return shown
"""
    result = check_source(source, profile)
    assert result.ok and not result.diagnostics, [d.render() for d in result.diagnostics]


# ---------------------------------- optional values, search, time, ordering --


def test_both_arms_document_optional_values_search_time_and_ordering() -> None:
    from lattice.llm.api_prompt import _BASELINE_OPTIONAL_SECTION, _LATTICE_OPTIONAL_SECTION

    for hints in (True, False):  # reference, not removable advice
        assert _LATTICE_OPTIONAL_SECTION in api_lattice_system_prompt(hints=hints)
        assert _BASELINE_OPTIONAL_SECTION in api_baseline_system_prompt(hints=hints)
    lattice, baseline = _LATTICE_OPTIONAL_SECTION, _BASELINE_OPTIONAL_SECTION
    assert 0.7 <= len(baseline) / len(lattice) <= 1.3
    # one bullet per feature in each arm
    assert lattice.count("\n- ") == baseline.count("\n- ") == 6
    for needle in (
        "| None = None",
        "is_null(",
        "`sent(p)`",
        "ignore_case=True",
        "fn.datetime_add(",
        "fn.datetime_diff(",
        "=None)",
        'nulls="smallest"',
    ):
        assert needle in lattice, needle
    for needle in (
        "| None = None",
        "IS NULL OR",
        "model_fields_set",
        "exclude_unset=True",
        "timedelta(hours=24)",
        "= NULL",
        '"DESC"',
    ):
        assert needle in baseline, needle
    # the same facts in both arms
    for needle in (
        '"not given"',
        "(even as null)",
        "an empty",
        "once per request",
        "truncated toward zero",
        "first ascending",
        "last descending",
    ):
        needle_l = "`sub=\"\"` matches" if needle == "an empty" else needle
        needle_l = "one instant per request" if needle == "once per request" else needle_l
        assert needle_l in lattice and needle in baseline, needle


def test_api_optional_examples_check_clean() -> None:
    from lattice.llm.api_prompt import _LATTICE_OPTIONAL_SECTION, API_OPTIONAL_EXAMPLES
    from tests.api.gaps import PROFILE

    for example in API_OPTIONAL_EXAMPLES:
        assert f"`{example}`" in _LATTICE_OPTIONAL_SECTION, example
    where, require, contains, diff, update, sort = API_OPTIONAL_EXAMPLES
    source = f"""# lattice: v1
@endpoint("PUT", "/items/{{id}}", auth=Customer)
def example(
    id: Uuid,
    user: CurrentUser,
    since: DateTime | None = None,
    title: Str(min_len=1, max_len=9) = UNSET,
    q: Str(max_len=20) = "",
    order: OneOf("newest", "oldest") = "newest",
) -> One | NotFound | Forbidden:
    items = fetch(Item)
    visible = owned(items, by=user)
    found = where(visible, col("id") == id)
    t = one(found, else_=NotFound("item"))
    a = {where}
    b = where(visible, {contains})
    c = derive(visible, hours={diff})
    e = {sort}
    r = {require}
    d = {update}
    return d
"""
    result = check_source(source, PROFILE)
    errors = [d.render() for d in result.diagnostics if d.severity.value == "error"]
    assert result.ok and errors == [], errors


def test_both_arms_tell_accessible_rows_from_the_callers_own_rows() -> None:
    """The owned()/mine() distinction (thin slice: "my items" written as owned() leaked
    every row to admins) is stated once in each arm's reference part, with equal
    information: access filter = Admin sees every row; own rows = Admin too."""
    from lattice.llm.api_prompt import BASELINE_OWN_ROWS, LATTICE_OWN_ROWS

    assert LATTICE_OWN_ROWS == (
        "- `accessible(t, by=user)` = rows the caller may access (admins: all rows). "
        "`mine(t, by=user)` = the caller's own rows only (admins too) — use it for "
        "'my …' features."
    )
    assert "owner_id == current_user.id" in BASELINE_OWN_ROWS
    assert "superusers" in BASELINE_OWN_ROWS and "'my …' features" in BASELINE_OWN_ROWS
    assert 0.7 <= len(BASELINE_OWN_ROWS) / len(LATTICE_OWN_ROWS) <= 1.3
    for hints in (True, False):
        assert LATTICE_OWN_ROWS in api_lattice_system_prompt(hints=hints)
        assert BASELINE_OWN_ROWS in api_baseline_system_prompt(hints=hints)


def test_owned_results_are_never_named_as_the_callers_own() -> None:
    """Every accessible() result in the Lattice guide reads as visible rows, not as
    "mine"."""
    prompt = api_lattice_system_prompt(hints=True)
    bindings = re.findall(r"\b(\w+) = accessible\(", prompt)
    assert bindings and all(b.startswith("visible") for b in bindings), bindings
    assert not re.search(r"\b(mine|my_\w+) = ", prompt)


def test_both_arms_document_the_round2_fixes() -> None:
    """Round 2 (spec 2026-09-27 lattice-t2 §6): the Lattice guide names `accessible`
    (with `owned` as its older name), the caller references and the owner transfer;
    the baseline guide carries the transfer rule in SQL terms (the caller's values are
    its `current_user` dict already)."""
    lattice = api_lattice_system_prompt(hints=False)
    for needle in (
        "`accessible(t, by=user)`",
        "`owned(t, by=user)` is an older name",
        "`user.id`",
        "`is_admin(user)`",
        "owner=<user row from one(...)>",
        'else_=NotFound("...")',
        "`null_order`",
    ):
        assert needle in lattice, needle
    assert "= owned(" not in lattice
    baseline = api_baseline_system_prompt(hints=False)
    assert "SET owner_id = ?" in baseline and "answer 404 when no user" in baseline
    assert '"id", "role", "email"' in baseline
