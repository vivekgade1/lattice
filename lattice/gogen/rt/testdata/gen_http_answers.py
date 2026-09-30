# ruff: noqa: E501
"""Regenerates http_answers.json: what the FastAPI app generated from httpapp.PROGRAM
(lattice.api.codegen, over the benchmark's platform_kit) answers for a sequence of
requests -- status, body, content-type / content-length / location headers, the number
of SQL statements (counted as bench.api_runner counts them, with a sqlite3 trace
callback) and the items table after each request -- once per error body style. It also
records each endpoint's generated Endpoint, SQL and response constants, and the
database, so the Go tests (http_test.go) serve the same endpoints and replay the same
requests. Run from the repository root:

    uv run python lattice/gogen/rt/testdata/gen_http_answers.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import sqlite3  # noqa: E402

import httpapp  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from bench.api_runner import _is_counted_statement  # noqa: E402
from lattice.api.codegen import compile_source_to_fastapi  # noqa: E402

KIT = ROOT / "bench" / "api_kit" / "platform_kit.py"
T = httpapp.TOKENS

# (name, method, path, Authorization header or None, body or None)
CASES = [
    ("page", "GET", "/items", None, None),
    ("page 2", "GET", "/items?n=1&skip=1", None, None),
    ("page repeated n", "GET", "/items?n=5&n=1", None, None),
    ("page n=0", "GET", "/items?n=0", None, None),
    ("page two problems", "GET", "/items?n=abc&skip=-1", None, None),
    ("one offset spelling", "GET", "/items/2", None, None),
    ("one space spelling", "GET", "/items/3", None, None),
    ("one missing", "GET", "/items/99", None, None),
    ("one bad id", "GET", "/items/x", None, None),
    ("one slash redirect", "GET", "/items/2/", None, None),
    ("list slash redirect", "GET", "/items/?n=1", None, None),
    ("put slash redirect", "PUT", "/items/1/", None, None),
    ("unknown path", "GET", "/nowhere", None, None),
    ("unknown slash", "GET", "/nowhere/", None, None),
    ("wrong method", "DELETE", "/items", None, None),
    ("head", "HEAD", "/items", None, None),
    ("encoded slash", "GET", "/items/a%2Fb", None, None),
    ("mine no token", "GET", "/items/mine", None, None),
    ("mine unknown token", "GET", "/items/mine", "Bearer nope", None),
    ("mine basic", "GET", "/items/mine", "Basic " + T["ann"], None),
    ("mine lowercase scheme", "GET", "/items/mine", "  bearer   " + T["ann"] + " ", None),
    ("mine inactive", "GET", "/items/mine", "Bearer " + T["cy"], None),
    ("admin as customer", "GET", "/admin/ping", "Bearer " + T["ann"], None),
    ("admin", "GET", "/admin/ping", "Bearer " + T["bob"], None),
    ("admin calls customer endpoint", "GET", "/items/mine", "Bearer " + T["bob"], None),
    ("create", "POST", "/items", "Bearer " + T["ann"], '{"name": "kiwi", "price": 2}'),
    ("create conflict", "POST", "/items", "Bearer " + T["ann"], '{"name": "kiwi", "price": 3}'),
    ("create invalid", "POST", "/items", "Bearer " + T["ann"], '{"price": -1}'),
    ("create non-object", "POST", "/items", "Bearer " + T["ann"], "[1]"),
    ("create no token", "POST", "/items", None, '{"name": "x"}'),
    ("create inactive before validation", "POST", "/items", "Bearer " + T["cy"], "[1]"),
    ("create unknown before validation", "POST", "/items", "Bearer nope", "{"),
    ("create as admin", "POST", "/items", "Bearer " + T["bob"], '{"name": "plum", "price": 1000}'),
    ("mine after create", "GET", "/items/mine", "Bearer " + T["ann"], None),
    ("page after create", "GET", "/items?n=2&skip=3", None, None),
]


def normalize(text: str) -> str:
    """The reply with each instant of the request (fn.now(), not the seeded year 2000)
    replaced by <now>: the Go test normalizes its replies the same way."""
    return re.sub(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z",
        lambda m: m.group(0) if m.group(0).startswith("2000-") else "<now>",
        text,
    )


def load_kit():
    """A fresh platform_kit module (its FastAPI app is module state)."""
    spec = importlib.util.spec_from_file_location("platform_kit", KIT)
    kit = importlib.util.module_from_spec(spec)
    sys.modules["platform_kit"] = kit
    spec.loader.exec_module(kit)
    return kit


def run_style(style: str) -> dict[str, object]:
    result, source = compile_source_to_fastapi(httpapp.PROGRAM, httpapp.PROFILES[style])
    assert source is not None, result.diagnostics
    kit = load_kit()
    counter = [0]

    def on_connect(conn: sqlite3.Connection) -> None:
        def trace(sql: str) -> None:
            if _is_counted_statement(sql):
                counter[0] += 1

        conn.set_trace_callback(trace)

    kit._on_connect = on_connect
    module = types.ModuleType(f"generated_app_{style}")
    sys.modules[module.__name__] = module
    exec(compile(source, "app.py", "exec"), module.__dict__)  # noqa: S102 - the reference app
    ns = module.__dict__
    endpoints = {}
    constants = {}
    for name, value in ns.items():
        if type(value).__name__ == "Endpoint" and name.endswith("_ENDPOINT"):
            ep = value
            endpoints[ep.name] = {
                "auth": ep.auth,
                "writes": ep.writes,
                "auth_sql": ep.auth_sql,
                "user_columns": list(ep.user_columns),
                "user_key": ep.user_key,
                "admin_role": ep.admin_role,
                "params": [
                    {
                        "name": p.name,
                        "source": p.source,
                        "scalar": p.scalar,
                        "ge": p.ge,
                        "le": p.le,
                        "max_len": p.max_len,
                        "default": None if repr(p.default) == "MISSING" else p.default,
                    }
                    for p in ep.params
                ],
            }
        elif name.isupper() and name.endswith(("_SQL", "_COLUMNS", "_RESPONSE", "_PAGE_SHAPE")):
            constants[name] = value
    routes = [
        {"path": r.path, "method": sorted(r.methods)[0], "name": r.name}
        for r in kit.app.routes
        if getattr(r, "name", "") in endpoints
    ]
    replies = []
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            httpapp.build_db(Path(tmp) / "app.db")
            client = TestClient(kit.app, follow_redirects=False)
            for name, method, path, auth, body in CASES:
                counter[0] = 0
                headers = {} if auth is None else {"Authorization": auth}
                if body is not None:
                    headers["Content-Type"] = "application/json"
                r = client.request(method, path, headers=headers, content=body)
                conn = sqlite3.connect(Path(tmp) / "app.db")
                items = conn.execute(
                    "SELECT id, name, price, owner_id FROM items ORDER BY id"
                ).fetchall()
                conn.close()
                replies.append(
                    {
                        "name": name,
                        "method": method,
                        "path": path,
                        "auth": auth,
                        "body": body,
                        "status": r.status_code,
                        "reply": normalize(r.content.decode("utf-8")),
                        "content_type": r.headers.get("content-type"),
                        "content_length": r.headers.get("content-length"),
                        "location": r.headers.get("location"),
                        "queries": counter[0],
                        "items": [list(row) for row in items],
                    }
                )
        finally:
            os.chdir(cwd)
    return {"endpoints": endpoints, "constants": constants, "routes": routes, "replies": replies}


out = {
    "python": sys.version.split()[0],
    "schema": httpapp.SCHEMA,
    "styles": {style: run_style(style) for style in ("lattice", "fastapi")},
}
dest = HERE / "http_answers.json"
dest.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
print(f"wrote {dest}")
