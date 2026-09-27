"""The FastAPI app generated from the real-apps program (tests/api/realapps.py) answers
raw requests exactly like the runtime, in process: including what the harness-level
differential cannot compare (an unfollowed 307 trailing-slash redirect, a 500 from a
native's contract violation, malformed JSON), and it stays `ruff format`-clean with its
natives import."""

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

from lattice.api.codegen import generate_fastapi
from lattice.api.ir import lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from tests.api.realapps import NATIVES, NATIVES_FILE, PROFILE, PROGRAM, TOKENS, build_db, uid
from tests.api.test_codegen_differential import normalize

KIT_PATH = Path(__file__).resolve().parents[2] / "bench" / "api_kit" / "platform_kit.py"
_MODULES = ("platform_kit", "generated_realapps", "natives")


@pytest.fixture(scope="module")
def plans():
    check = check_source(PROGRAM, PROFILE)
    assert check.program is not None
    return lower_api(check.program)


@pytest.fixture(scope="module")
def code(plans) -> str:
    return generate_fastapi(plans, PROGRAM)


@pytest.fixture
def apps(tmp_path: Path, plans, code: str, monkeypatch) -> Iterator[tuple]:
    gen_dir = tmp_path / "gen"
    gen_dir.mkdir()
    shutil.copyfile(KIT_PATH, gen_dir / "platform_kit.py")
    shutil.copyfile(NATIVES_FILE, gen_dir / "natives.py")
    (gen_dir / "generated_realapps.py").write_text(code)
    build_db(gen_dir / "app.db")
    rt_db = tmp_path / "rt.db"
    build_db(rt_db)
    monkeypatch.chdir(gen_dir)
    monkeypatch.syspath_prepend(str(gen_dir))
    for name in _MODULES:
        sys.modules.pop(name, None)
    module = importlib.import_module("generated_realapps")
    try:
        with (
            TestClient(build_app(plans, rt_db, natives=NATIVES)) as rt,
            TestClient(module.app) as gen,
        ):
            yield rt, gen
    finally:
        for name in _MODULES:
            sys.modules.pop(name, None)


def _h(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


RAW = [
    ("GET", "/items", _h("alice"), None),  # 307 to /items/
    ("GET", "/items?limit=1&skip=1", _h("alice"), None),
    ("GET", "/users/me/", _h("alice"), None),  # 307 to /users/me
    ("GET", "/items/", _h("alice"), None),
    ("GET", "/items/?skip=99", _h("admin"), None),
    ("GET", f"/items/{uid(101)}/broken", _h("alice"), None),  # a native's null: 500
    ("GET", f"/items/{uid(101)}/loud", _h("alice"), None),
    ("GET", f"/items/{uid(103)}", _h("alice"), None),
    ("GET", f"/items/{uid(102)}/loud", _h("alice"), None),  # a native require: 400
    ("GET", f"/items/{uid(999)}", _h("alice"), None),
    ("GET", f"/notes/{uid(202)}", _h("alice"), None),
    ("GET", "/items/since?after=2000-01-03T11%3A00%3A00%2B02%3A00", _h("alice"), None),
    ("GET", "/items/since?after=bad", _h("alice"), None),
    ("GET", f"/clock/{uid(101)}", _h("alice"), None),
    ("PUT", f"/items/{uid(101)}", _h("alice"), b'{"title": null}'),
    ("PUT", f"/items/{uid(101)}", _h("alice"), b'{"description": null, "title": "T"}'),
    ("PUT", f"/items/{uid(101)}", _h("alice"), b"{}"),
    ("PUT", f"/items/{uid(101)}", _h("alice"), b"{"),
    ("PATCH", "/users/me", _h("bob"), b'{"full_name": null}'),
    ("POST", "/items/", _h("alice"), b'{"title": "x", "description": null}'),
    ("POST", f"/items/{uid(101)}/copy", _h("alice"), b'{"new_id": "' + uid(555).encode() + b'"}'),
    ("POST", f"/items/{uid(101)}/copy", _h("alice"), b'{"new_id": 5}'),
    ("POST", "/login/access-token", {}, b'{"username": "bob@example.com", "password": "bob-pass"}'),
    (
        "GET",
        "/users/me",
        {"Authorization": "Bearer tok-00000000-0000-4000-8000-000000000404"},
        None,
    ),
    ("GET", "/users/", _h("bob"), None),
    ("GET", "/users/?limit=1", _h("admin"), None),
]


@pytest.mark.parametrize(("method", "url", "headers", "body"), RAW)
def test_raw_requests_match_the_runtime(apps, method, url, headers, body):
    rt, gen = apps
    a = rt.request(method, url, headers=headers, content=body, follow_redirects=False)
    b = gen.request(method, url, headers=headers, content=body, follow_redirects=False)
    assert a.status_code == b.status_code
    assert a.headers.get("location") == b.headers.get("location")
    assert a.headers.get("content-type") == b.headers.get("content-type")
    if a.content and a.headers.get("content-type") == "application/json":
        assert normalize(a.json()) == normalize(b.json())
    else:
        assert a.content == b.content


def test_the_raw_requests_reach_redirects_and_500s(apps):
    rt, _ = apps
    statuses = {
        rt.request(m, u, headers=h, content=b, follow_redirects=False).status_code
        for m, u, h, b in RAW
    }
    assert {200, 201, 307, 400, 401, 403, 404, 422, 500} <= statuses


def test_the_generated_app_imports_only_the_natives_it_calls(code: str):
    tree = ast.parse(code)
    (imp,) = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "natives"]
    assert sorted(a.name for a in imp.names) == sorted(NATIVES)  # every native is called
    assert all(a.asname == f"native_{a.name}" for a in imp.names)


def test_the_generated_app_is_ruff_formatted(tmp_path: Path, code: str):
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
