"""lattice.importer.transport (Phase 2 plan, W1-A): one toy ASGI app and one toy WSGI app
answer identical ``Case``s identically through ``verify.run_case``; the ``Token`` /
``JWT`` header schemes, the login drivers (form, JSON, minted, session cookie) and the
dispose hooks."""

from __future__ import annotations

import ast
import json
import sqlite3
import types
from collections.abc import Callable
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest

from lattice.importer.transport import (
    Answer,
    AppRuntime,
    AsgiTransport,
    AuthDriver,
    WsgiTransport,
    dispose_state_engine,
)
from lattice.importer.verify import (
    Case,
    Normalizer,
    ValidationEnvelope,
    form_login,
    raise_sites,
    run_case,
    verify,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS = {"alice": ("alice@example.com", "pw-alice"), "bob": ("bob@example.com", "pw-bob")}

# ------------------------------------------------------------------- the toy app --


class _Engine:
    def __init__(self) -> None:
        self.disposed = 0

    def dispose(self) -> None:
        self.disposed += 1


def _handle(
    db: Path, method: str, path: str, query: str, headers: dict[str, str], body: bytes
) -> tuple[int, Any]:
    """The toy app, framework-free: (status, JSON body or None)."""
    users = {email: handle for handle, (email, _) in CREDENTIALS.items()}
    if path == "/boom":
        raise RuntimeError("boom")
    if path == "/whoami":
        auth = headers.get("authorization")
        return (200, {"auth": auth}) if auth else (401, {"detail": "Not authenticated"})
    if path == "/session/whoami":
        cookie = SimpleCookie(headers.get("cookie", ""))
        sid = cookie["session"].value if "session" in cookie else None
        return (200, {"user": sid.removeprefix("sid-")}) if sid else (401, {"detail": "no"})
    if path in ("/login", "/login-json", "/session/login") and method == "POST":
        if path == "/login-json":
            data = json.loads(body or b"{}")
        else:
            data = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        handle = users.get(data.get("username", ""))
        if handle is None or CREDENTIALS[handle][1] != data.get("password"):
            return 400, {"detail": "Incorrect email or password"}
        if path == "/login-json":
            return 200, {"data": {"token": f"tok-{handle}"}}
        if path == "/session/login":
            return 204, ("set-cookie", f"session=sid-{handle}; Path=/")
        return 200, {"access_token": f"tok-{handle}", "token_type": "bearer"}
    if path == "/items":
        conn = sqlite3.connect(db)
        try:
            if method == "GET":
                limit = parse_qs(query).get("limit", ["100"])[0]
                rows = conn.execute("SELECT id, name FROM item ORDER BY id LIMIT ?", (int(limit),))
                return 200, [{"id": i, "name": n} for i, n in rows]
            if method == "POST":
                data = json.loads(body or b"null")
                if not isinstance(data, dict) or not isinstance(data.get("name"), str):
                    return 422, {"detail": [{"loc": ["body", "name"], "msg": "required"}]}
                cur = conn.execute("INSERT INTO item (name) VALUES (?)", (data["name"],))
                conn.commit()
                return 201, {"id": cur.lastrowid, "name": data["name"]}
        finally:
            conn.close()
    return 404, {"detail": "Not Found"}


def _encode(status: int, payload: Any) -> tuple[list[tuple[str, str]], bytes]:
    if isinstance(payload, tuple):  # a header only (the session login)
        return [payload], b""
    return [("content-type", "application/json")], json.dumps(payload).encode()


class ToyAsgi:
    def __init__(self, db: Path) -> None:
        self.db = db
        self.state = types.SimpleNamespace(engine=_Engine())

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        status, payload = _handle(self.db, scope["method"], scope["path"],
                                  scope["query_string"].decode(), headers, body)  # fmt: skip
        out, content = _encode(status, payload)
        await send({"type": "http.response.start", "status": status,
                    "headers": [(k.encode(), v.encode()) for k, v in out]})  # fmt: skip
        await send({"type": "http.response.body", "body": content})


class ToyWsgi:
    def __init__(self, db: Path) -> None:
        self.db = db
        self.state = types.SimpleNamespace(engine=_Engine())

    def __call__(self, environ: dict, start_response: Callable) -> list[bytes]:
        headers = {
            k[5:].replace("_", "-").lower(): v for k, v in environ.items() if k.startswith("HTTP_")
        }
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(length) if length else b""
        status, payload = _handle(self.db, environ["REQUEST_METHOD"], environ["PATH_INFO"],
                                  environ.get("QUERY_STRING", ""), headers, body)  # fmt: skip
        out, content = _encode(status, payload)
        start_response(f"{status} X", out)
        return [content]


def _seed(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.executemany("INSERT INTO item (name) VALUES (?)", [("one",), ("two",)])
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def seed(tmp_path: Path) -> Path:
    return _seed(tmp_path / "seed.db")


ASGI = AppRuntime()  # the FastAPI default: today's TestClient, Bearer, engine.dispose()
WSGI = AppRuntime(framework="flask", transport=WsgiTransport)

CASES = [
    Case("list", "GET", "/items", tables=("item",)),
    Case("list_limit", "GET", "/items", params={"limit": 1}),
    Case("create", "POST", "/items", json={"name": "three"}, tables=("item",)),
    Case("create_invalid", "POST", "/items", json={"nam": 1}, tables=("item",)),
    Case("whoami_anonymous", "GET", "/whoami"),
    Case("whoami_alice", "GET", "/whoami", user="alice"),
    Case("whoami_token", "GET", "/whoami", token="raw"),
    Case("missing", "DELETE", "/nowhere"),
    Case("boom", "GET", "/boom"),
]


def _record(case: Case, runtime: AppRuntime | None, seed: Path, **kw: Any) -> Any:
    factory = ToyWsgi if runtime is not None and runtime.transport is WsgiTransport else ToyAsgi
    kw.setdefault("login", form_login("/login", CREDENTIALS))
    if runtime is not None:
        kw["runtime"] = runtime
    return run_case(case, factory, seed, Normalizer(seed), **kw)


# ------------------------------------------------------------------------------ tests --


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_the_asgi_and_wsgi_apps_answer_identical_cases_identically(case, seed):
    asgi = _record(case, ASGI, seed)
    wsgi = _record(case, WSGI, seed)
    assert asgi == wsgi
    expected = {"list": 200, "list_limit": 200, "create": 201, "create_invalid": 422,
                "whoami_anonymous": 401, "whoami_alice": 200, "whoami_token": 200,
                "missing": 404, "boom": 500}  # fmt: skip
    assert asgi.status == expected[case.name]


def test_the_default_runtime_is_todays_bearer_testclient(seed):
    record = _record(Case("w", "GET", "/whoami", user="alice"), None, seed)
    assert record.body == {"auth": "Bearer tok-alice"}
    assert record == _record(Case("w", "GET", "/whoami", user="alice"), ASGI, seed)
    assert AppRuntime().transport is AsgiTransport
    assert AppRuntime().auth == AuthDriver(scheme="Bearer") == AuthDriver(scheme="bearer")
    assert AppRuntime().auth.scheme == "bearer"
    assert AppRuntime().dispose == (dispose_state_engine,)


@pytest.mark.parametrize("scheme", ["Token", "JWT", "Bearer"])
@pytest.mark.parametrize("runtime", [ASGI, WSGI], ids=["asgi", "wsgi"])
def test_the_header_schemes(scheme, runtime, seed):
    rt = AppRuntime(framework=runtime.framework, transport=runtime.transport,
                    auth=AuthDriver(scheme=scheme))  # fmt: skip
    assert rt.auth.headers("abc") == {"Authorization": f"{scheme} abc"}
    by_user = _record(Case("w", "GET", "/whoami", user="bob"), rt, seed)
    by_token = _record(Case("w", "GET", "/whoami", token="raw"), rt, seed)
    assert by_user.body == {"auth": f"{scheme} tok-bob"}
    assert by_token.body == {"auth": f"{scheme} raw"}


def test_a_custom_header_name(seed):
    driver = AuthDriver(scheme="Token", header="X-Auth")
    assert driver.headers("t") == {"X-Auth": "Token t"}
    assert driver.headers(None) == {}


@pytest.mark.parametrize("runtime", [ASGI, WSGI], ids=["asgi", "wsgi"])
def test_the_auth_driver_logs_in_through_a_json_login_route(runtime, seed):
    driver = AuthDriver.json("/login-json", CREDENTIALS, token_path=("data", "token"),
                             scheme="JWT")  # fmt: skip
    rt = AppRuntime(framework=runtime.framework, transport=runtime.transport, auth=driver)
    record = _record(Case("w", "GET", "/whoami", user="alice"), rt, seed, login=None)
    assert record.body == {"auth": "JWT tok-alice"}


@pytest.mark.parametrize("runtime", [ASGI, WSGI], ids=["asgi", "wsgi"])
def test_the_auth_driver_logs_in_through_a_form_login_route(runtime, seed):
    driver = AuthDriver.form("/login", CREDENTIALS, scheme="Token")
    rt = AppRuntime(framework=runtime.framework, transport=runtime.transport, auth=driver)
    record = _record(Case("w", "GET", "/whoami", user="bob"), rt, seed, login=None)
    assert record.body == {"auth": "Token tok-bob"}


def test_a_failed_login_says_so(seed):
    bad = {"alice": ("alice@example.com", "wrong")}
    rt = AppRuntime(auth=AuthDriver.json("/login-json", bad, token_path=("data", "token")))
    with pytest.raises(RuntimeError, match="login as 'alice' failed: 400"):
        _record(Case("w", "GET", "/whoami", user="alice"), rt, seed, login=None)


@pytest.mark.parametrize("runtime", [ASGI, WSGI], ids=["asgi", "wsgi"])
def test_the_auth_driver_mints_a_token_without_a_request(runtime, seed):
    driver = AuthDriver(scheme="JWT", mint=lambda handle: f"minted-{handle}")
    rt = AppRuntime(framework=runtime.framework, transport=runtime.transport, auth=driver)
    record = _record(Case("w", "GET", "/whoami", user="carol"), rt, seed, login=None)
    assert record.body == {"auth": "JWT minted-carol"}


@pytest.mark.parametrize("runtime", [ASGI, WSGI], ids=["asgi", "wsgi"])
def test_the_auth_driver_holds_a_session_cookie(runtime, seed):
    driver = AuthDriver.session("/session/login", CREDENTIALS, cookie="session")
    rt = AppRuntime(framework=runtime.framework, transport=runtime.transport, auth=driver)
    assert driver.headers("sid-x") == {}  # the session cookie sits in the client's jar
    record = _record(Case("w", "GET", "/session/whoami", user="bob"), rt, seed, login=None)
    assert record.body == {"user": "bob"}
    minted = AuthDriver(scheme="session", cookie="session", mint=lambda h: f"sid-{h}")
    rt = AppRuntime(framework=runtime.framework, transport=runtime.transport, auth=minted)
    record = _record(Case("w", "GET", "/session/whoami", user="alice"), rt, seed, login=None)
    assert record.body == {"user": "alice"}


def test_a_case_acting_as_a_user_needs_a_way_to_log_in(seed):
    with pytest.raises(ValueError, match="acts as a user but no login"):
        _record(Case("w", "GET", "/whoami", user="alice"), ASGI, seed, login=None)


@pytest.mark.parametrize("runtime", [ASGI, WSGI], ids=["asgi", "wsgi"])
def test_the_dispose_hooks_are_called_after_each_case_even_on_failure(runtime, seed):
    seen: list[tuple[str, Any]] = []
    rt = AppRuntime(framework=runtime.framework, transport=runtime.transport,
                    dispose=(lambda app: seen.append(("a", app)),
                             lambda app: seen.append(("b", app))))  # fmt: skip
    _record(Case("l", "GET", "/items"), rt, seed)
    assert [k for k, _ in seen] == ["a", "b"] and seen[0][1] is seen[1][1]
    seen.clear()
    with pytest.raises(ValueError):
        _record(Case("w", "GET", "/whoami", user="alice"), rt, seed, login=None)
    assert [k for k, _ in seen] == ["a", "b"]


def test_the_default_dispose_disposes_the_app_state_engine(seed):
    apps: list[Any] = []

    def factory(path: Path) -> ToyAsgi:
        apps.append(ToyAsgi(path))
        return apps[-1]

    run_case(Case("l", "GET", "/items"), factory, seed, Normalizer(seed))
    assert apps[0].state.engine.disposed == 1
    dispose_state_engine(object())  # an app without state.engine: nothing to dispose


def test_the_runtime_serves_an_app_and_disposes_it(seed):
    seen: list[Any] = []
    rt = AppRuntime(transport=WsgiTransport, dispose=(seen.append,))
    with rt.serve(ToyWsgi, seed) as (app, transport):
        answer = transport.request("GET", "/items", params={"limit": 1})
        assert seen == []
    assert seen == [app]
    assert isinstance(answer, Answer)
    assert (answer.status, answer.status_code) == (200, 200)
    assert answer.body == [{"id": 1, "name": "one"}]
    assert answer.json() == answer.body and answer.headers["content-type"] == "application/json"


def test_an_unhandled_error_raises_when_asked(seed):
    for runtime in (ASGI, WSGI):
        factory = ToyWsgi if runtime is WSGI else ToyAsgi
        with runtime.serve(factory, seed, raise_errors=True) as (_, transport):
            with pytest.raises(RuntimeError, match="boom"):
                transport.request("GET", "/boom")


def test_verify_runs_the_original_under_its_runtime(seed, tmp_path):
    """verify(): a WSGI original against an ASGI import, case by case equal."""

    def db_factory(variant: str, path: Path) -> Path:
        return _seed(path)

    login = form_login("/login", CREDENTIALS)
    result = verify(ToyWsgi, ToyAsgi, db_factory, CASES, [ValidationEnvelope()], {},
                    login=login, runtime=WSGI)  # fmt: skip
    assert not result.mismatches, result.render()
    assert result.tally()[3] == 0


def test_the_error_site_hook(tmp_path):
    """raise_sites reads a front end's error sites (here a Flask-style ``abort``)."""
    import importlib.util

    src = tmp_path / "toy_views.py"
    src.write_text(
        "def abort(code, detail=None):\n    raise RuntimeError(code)\n\n"
        "class HTTPException(Exception):\n    pass\n\n"
        "def view(x):\n"
        "    if x == 1:\n        abort(404, 'gone')\n"
        "    if x == 2:\n        raise HTTPException(403, 'no')\n"
        "    return x\n"
    )
    spec = importlib.util.spec_from_file_location("toy_views", src)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    def abort_site(node: ast.AST) -> tuple[int | None, str | None] | None:
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "abort"):  # fmt: skip
            return node.args[0].value, node.args[1].value  # type: ignore[attr-defined]
        return None

    default = raise_sites(module.view)
    assert [(s.status, s.detail) for s in default] == [(403, "no")]
    flask = raise_sites(module.view, error_site=abort_site)
    assert [(s.status, s.detail, s.line) for s in flask] == [(404, "gone", 9)]


def test_lift_does_not_import_the_testclient_at_module_level():
    tree = ast.parse((REPO_ROOT / "lattice/importer/lift.py").read_text())
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom):
            assert not (stmt.module or "").startswith(("fastapi", "starlette")), stmt.module
        if isinstance(stmt, ast.Import):
            assert not any(a.name.startswith(("fastapi", "starlette")) for a in stmt.names)
