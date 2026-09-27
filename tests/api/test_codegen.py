"""Unit tests for lattice.api.codegen: the shape of the generated FastAPI app.py, and
in-process equivalence on requests the harness cannot express (raw headers/bodies)."""

from __future__ import annotations

import ast
import importlib
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from bench.api_datasets import TOKENS, api_profile
from lattice.api.codegen import compile_source_to_fastapi, generate_fastapi
from lattice.api.ir import ReadStep, WriteStep, lower_api
from lattice.api.runtime import build_app
from tests.api.conftest import checked, source

KIT_PATH = Path(__file__).resolve().parents[2] / "bench" / "api_kit" / "platform_kit.py"
O06 = Path(__file__).resolve().parents[1] / "fixtures" / "api" / "o06" / "lattice.py"

PROGRAM = source(
    """
    @endpoint("POST", "/orders/{order_id}/cancel", auth=Customer)
    def cancel(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound | Conflict:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        found = where(mine, col("id") == order_id)
        order = one(found, else_=NotFound("order"))
        ok = require(order, col("status") == "pending", else_=Conflict("order is not pending"))
        done = update(ok, status="cancelled")
        return done

    @endpoint("GET", "/orders/{order_id}", auth=Customer)
    def show(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        found = where(mine, col("id") == order_id)
        order = one(found, else_=NotFound("order"))
        return order

    @endpoint("GET", "/orders/mine", auth=Customer)
    def run(user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0) -> Page:
        orders = fetch(Orders)
        mine = owned(orders, by=user)
        ordered = sort(mine, by=["id"])
        result = page(ordered, limit=limit, offset=offset)
        return result

    @endpoint("PATCH", "/products/{product_id}/stock", auth=Admin)
    def set_stock(product_id: Int(ge=1), stock: Int(ge=0, le=10000), user: CurrentUser
                  ) -> One | NotFound:
        products = fetch(Products)
        found = where(products, col("id") == product_id)
        slim = select(found, cols=["id", "name", "stock"])
        product = one(slim, else_=NotFound("product"))
        updated = update(product, stock=stock)
        return updated
    """
)


@pytest.fixture(scope="module")
def plans():
    return lower_api(checked(PROGRAM[len("# lattice: v1\n") :]))


@pytest.fixture(scope="module")
def code(plans) -> str:
    return generate_fastapi(plans, PROGRAM)


@pytest.fixture(scope="module")
def tree(code: str) -> ast.Module:
    return ast.parse(code)


def _constants(tree: ast.Module) -> dict[str, object]:
    out: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                try:
                    out[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return out


# ---- shape -------------------------------------------------------------------------------


def test_imports_only_stdlib_fastapi_and_the_kit(tree: ast.Module):
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.module is not None
            modules.add(node.module.split(".")[0])
    assert modules - set(sys.stdlib_module_names) == {"fastapi", "platform_kit"}
    kit = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "platform_kit"]
    assert [sorted(a.name for a in n.names) for n in kit] == [["app", "get_db"]]


def test_header_names_the_source_lines(code: str, plans):
    head = code.split('"""', 2)[1]
    assert head.startswith("Generated from a Lattice API program")
    for plan in plans:
        assert f"line {plan.span.line:>4}: {plan.method:<6} {plan.path} -> {plan.name}" in code
        assert f"Lattice source line {plan.span.line})" in code
    # checks and writes are annotated with the Lattice line they came from
    assert '# line 8: ok = require(order, col("status") == "pending"' in code


def test_routes_are_decorated_in_the_runtime_order(tree: ast.Module, plans):
    routes = []
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.decorator_list:
            deco = node.decorator_list[0]
            if (
                isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Attribute)
                and isinstance(deco.func.value, ast.Name)
                and deco.func.value.id == "app"
                and deco.func.attr != "exception_handler"
            ):
                path = ast.literal_eval(deco.args[0])
                routes.append((deco.func.attr.upper(), path, node.name))
    # /orders/mine (literal segment) is registered before /orders/{order_id}
    assert routes == [
        ("GET", "/orders/mine", "run_2"),  # `run` is a prelude helper: not shadowed
        ("GET", "/orders/{order_id}", "show"),
        ("POST", "/orders/{order_id}/cancel", "cancel"),
        ("PATCH", "/products/{product_id}/stock", "set_stock"),
    ]
    assert len(routes) == len(plans)


def test_sql_is_the_plans_text_verbatim(tree: ast.Module, plans):
    constants = _constants(tree)
    texts = {v for k, v in constants.items() if k.endswith("_SQL")}
    wanted = {s.sql.text for p in plans for s in p.steps if isinstance(s, ReadStep | WriteStep)}
    assert texts == wanted


def test_no_sql_is_built_from_values(tree: ast.Module):
    """Every statement executed is a module constant (or a fixed literal / the Endpoint's
    auth_sql field); values only ever travel as bind parameters."""
    constants = _constants(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "execute":
            sql = node.args[0]
        elif isinstance(func, ast.Name) and func.id == "run":
            sql = node.args[1]
        else:
            continue
        match sql:
            case ast.Constant(value=str(text)):
                assert text in ("BEGIN IMMEDIATE", "BEGIN", "COMMIT", "ROLLBACK")
            case ast.IfExp(body=ast.Constant(), orelse=ast.Constant()):
                pass
            case ast.Name(id=name):
                assert name == "sql" or isinstance(constants.get(name), str), name
            case ast.Attribute(attr="auth_sql"):
                pass
            case _:
                pytest.fail(f"SQL built dynamically: {ast.unparse(sql)}")
    for node in ast.walk(tree):  # no f-string anywhere assembles SQL
        if isinstance(node, ast.JoinedStr):
            text = ast.unparse(node).upper()
            assert not any(k in text for k in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ")), text


def test_binds_carry_params_literals_rows_and_the_caller(code: str):
    assert '"p_order_id": sql_value(values["order_id"])' in code
    assert '"__l0": "pending"' in code
    assert '"__user_id": user_id' in code and '"__is_admin": is_admin' in code
    assert '"__r0": rows_json(' in code


def test_generated_code_is_ruff_formatted(tmp_path: Path, code: str):
    ruff = shutil.which("ruff", path=str(Path(sys.executable).parent)) or shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff is not installed")
    path = tmp_path / "app.py"
    path.write_text(code)
    done = subprocess.run(
        [ruff, "format", "--check", "--isolated", "--line-length", "100", str(path)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_compile_source_to_fastapi_rejects_what_the_checker_rejects():
    bad = PROGRAM.replace("mine = owned(orders, by=user)", "mine = where(orders, col('id') > 0)")
    result, code = compile_source_to_fastapi(bad, api_profile())
    assert code is None and not result.ok
    result, code = compile_source_to_fastapi(O06.read_text(), api_profile())
    assert result.ok and code is not None and "def cancel_order(" in code


# ---- in-process equivalence on raw requests ---------------------------------------------------


@pytest.fixture
def apps(tmp_path: Path, db: Path, plans, code: str, monkeypatch) -> Iterator[tuple]:
    """(runtime client, generated client, runtime db, generated db)."""
    gen_dir = tmp_path / "gen"
    gen_dir.mkdir()
    shutil.copyfile(KIT_PATH, gen_dir / "platform_kit.py")
    (gen_dir / "generated_app.py").write_text(code)
    shutil.copyfile(db, gen_dir / "app.db")
    rt_db = tmp_path / "rt.db"
    shutil.copyfile(db, rt_db)
    monkeypatch.chdir(gen_dir)
    monkeypatch.syspath_prepend(str(gen_dir))
    for name in ("platform_kit", "generated_app"):
        sys.modules.pop(name, None)
    module = importlib.import_module("generated_app")
    try:
        with TestClient(build_app(plans, rt_db)) as rt, TestClient(module.app) as gen:
            yield rt, gen
    finally:
        for name in ("platform_kit", "generated_app"):
            sys.modules.pop(name, None)


ALICE = TOKENS["alice"]
ADMIN_H = {"Authorization": f"Bearer {TOKENS['admin']}"}
RAW_REQUESTS = [
    ("POST", "/orders/1/cancel", {"Authorization": f"bearer {ALICE}"}, None),
    ("POST", "/orders/1/cancel", {"Authorization": f"  BEARER   {ALICE}  "}, None),
    ("POST", "/orders/1/cancel", {"Authorization": f"Basic {ALICE}"}, None),
    ("POST", "/orders/1/cancel", {"Authorization": "Bearer"}, None),
    ("POST", "/orders/1/cancel", {"Authorization": "Bearer    "}, None),
    ("POST", "/orders/1/cancel", {"Authorization": f"Bearer{ALICE}"}, None),
    ("GET", "/orders/mine?limit=0&offset=-1", {"Authorization": f"Bearer {ALICE}"}, None),
    ("GET", "/orders/mine?limit=+3&offset= 1", {"Authorization": f"Bearer {ALICE}"}, None),
    ("GET", "/orders/mine?limit=2&limit=3", {"Authorization": f"Bearer {ALICE}"}, None),
    ("GET", "/orders/mine?limit=", {"Authorization": f"Bearer {ALICE}"}, None),
    ("GET", "/orders/abc", {"Authorization": f"Bearer {TOKENS['bob']}"}, None),
    ("PATCH", "/products/1/stock", {"Authorization": f"Bearer {ALICE}"}, b"{"),
    ("PATCH", "/products/1/stock", ADMIN_H, b"{"),
    ("PATCH", "/products/1/stock", ADMIN_H, b"  "),
    ("PATCH", "/products/1/stock", ADMIN_H, b"[1]"),
    ("PATCH", "/products/x/stock", ADMIN_H, b"{}"),
    ("PATCH", "/products/1/stock", ADMIN_H, b'{"stock": 1e2}'),
    ("PATCH", "/products/1/stock", ADMIN_H, b'{"stock": NaN}'),
    ("PATCH", "/products/1/stock", ADMIN_H, b'{"stock": "5"}'),
    ("DELETE", "/products/1/stock", {}, None),
    ("GET", "/nowhere", {}, None),
    ("GET", "/orders/mine/", {"Authorization": f"Bearer {ALICE}"}, None),
]


@pytest.mark.parametrize(("method", "url", "headers", "body"), RAW_REQUESTS)
def test_raw_requests_match_the_runtime(apps, method, url, headers, body):
    rt, gen = apps
    a = rt.request(method, url, headers=headers, content=body, follow_redirects=False)
    b = gen.request(method, url, headers=headers, content=body, follow_redirects=False)
    assert (a.status_code, a.content) == (b.status_code, b.content)
    assert a.headers.get("content-type") == b.headers.get("content-type")
