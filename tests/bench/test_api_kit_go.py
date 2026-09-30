"""The Go platform kit for the 5b pilot's arm G (bench/api_kit_go).

Arm G's model writes Go against `platformkit`; arm P's writes FastAPI against
bench/api_kit/platform_kit.py. The kit must give arm G exactly what the Python kit gives
arm P. So here one tiny handler set is written twice -- once on each kit -- and every
probe case (no token, malformed header, unknown token, wrong role, a valid token for
each role, Admin on a role-gated route, a data query, an unrouted path, a crash) must
get the same status, Content-Type, body (byte for byte) and SQL statement count from
both. The Python side counts statements with the harness's trace hook
(`platform_kit._on_connect`, bench.api_runner); the Go side sends X-Lattice-Sql-Count
with LATTICE_TRACE_SQL=1, run by lattice.gogen.proxy.GoServer as the harness runs it.

The Go module's own `go vet` and `go test` run here too (offline, GOPROXY=off).
"""

from __future__ import annotations

import http.client
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from bench.api_runner import _is_counted_statement
from lattice.gogen.build import GO_ENV
from lattice.gogen.proxy import SQL_COUNT_HEADER, GoServer

pytestmark = pytest.mark.go  # builds and runs Go (skipped without `go`, tests/conftest.py)

ROOT = Path(__file__).resolve().parents[2]
KIT_GO = ROOT / "bench" / "api_kit_go"
KIT_PY = ROOT / "bench" / "api_kit" / "platform_kit.py"


def build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL, name TEXT NOT NULL,
                            role TEXT NOT NULL, token TEXT NOT NULL UNIQUE,
                            password_hash TEXT NOT NULL);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL,
                             total INTEGER NOT NULL);
        INSERT INTO users VALUES (1, 'a@x', 'Alice', 'Customer', 'tok-a', 'h');
        INSERT INTO users VALUES (2, 'b@x', 'Bob', 'Customer', 'tok-b', 'h');
        INSERT INTO users VALUES (3, 's@x', 'Sam', 'Seller', 'tok-s', 'h');
        INSERT INTO users VALUES (4, 'u@x', 'Uma', 'Support', 'tok-u', 'h');
        INSERT INTO users VALUES (5, 'r@x', 'Root', 'Admin', 'tok-admin', 'h');
        INSERT INTO orders VALUES (1, 1, 10);
        INSERT INTO orders VALUES (2, 1, 20);
        INSERT INTO orders VALUES (3, 2, 30);
        """
    )
    conn.commit()
    conn.close()


# ---- the same handlers on each kit -------------------------------------------------------

PY_SOLUTION = textwrap.dedent(
    """
    from fastapi import Depends
    from fastapi.responses import JSONResponse

    from platform_kit import app, current_user, get_db, require_role


    @app.get("/me")
    def me(user=Depends(current_user)):
        return {"id": user["id"], "role": user["role"], "email": user["email"],
                "name": user["name"]}


    @app.get("/admin")
    def admin(user=Depends(require_role("Admin"))):
        return {"ok": True, "id": user["id"]}


    @app.get("/staff")
    def staff(user=Depends(require_role("Seller", "Support"))):
        return {"ok": True, "id": user["id"]}


    @app.get("/orders")
    def list_orders(user=Depends(current_user), db=Depends(get_db)):
        rows = db.execute(
            "SELECT id, total FROM orders WHERE customer_id = ? ORDER BY id", (user["id"],)
        ).fetchall()
        n = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        return {"items": [{"id": r["id"], "total": r["total"]} for r in rows], "count": n}


    @app.post("/orders")
    def create_order(user=Depends(current_user), db=Depends(get_db)):
        cur = db.execute(
            "INSERT INTO orders (customer_id, total) VALUES (?, ?)", (user["id"], 5)
        )
        db.commit()
        return JSONResponse(status_code=201, content={"id": cur.lastrowid})


    @app.get("/boom")
    def boom(user=Depends(current_user)):
        raise RuntimeError("boom")
    """
)

GO_MAIN = textwrap.dedent(
    """
    package main

    import (
    \t"encoding/json"
    \t"net/http"

    \t"platformkit"
    )

    func main() { platformkit.Serve(Register) }

    func writeJSON(w http.ResponseWriter, status int, v any) {
    \tw.Header().Set("Content-Type", "application/json")
    \tw.WriteHeader(status)
    \t_ = json.NewEncoder(w).Encode(v)
    }

    type me struct {
    \tID    int64  `json:"id"`
    \tRole  string `json:"role"`
    \tEmail string `json:"email"`
    \tName  string `json:"name"`
    }

    type okID struct {
    \tOK bool  `json:"ok"`
    \tID int64 `json:"id"`
    }

    type item struct {
    \tID    int64 `json:"id"`
    \tTotal int64 `json:"total"`
    }

    type page struct {
    \tItems []item `json:"items"`
    \tCount int64  `json:"count"`
    }

    func Register(mux *http.ServeMux, k *platformkit.Kit) {
    \tmux.HandleFunc("GET /me", func(w http.ResponseWriter, r *http.Request) {
    \t\tc, ok := k.Authenticate(w, r)
    \t\tif !ok {
    \t\t\treturn
    \t\t}
    \t\twriteJSON(w, 200, me{c.ID, c.Role, c.Email, c.Name})
    \t})
    \tmux.HandleFunc("GET /admin", func(w http.ResponseWriter, r *http.Request) {
    \t\tc, ok := k.Authenticate(w, r, "Admin")
    \t\tif !ok {
    \t\t\treturn
    \t\t}
    \t\twriteJSON(w, 200, okID{true, c.ID})
    \t})
    \t// the two lower-level calls, as a handler may use them
    \tmux.HandleFunc("GET /staff", func(w http.ResponseWriter, r *http.Request) {
    \t\tc, err := k.Caller(r)
    \t\tif err == nil {
    \t\t\terr = k.RequireRole(c, "Seller", "Support")
    \t\t}
    \t\tif err != nil {
    \t\t\tplatformkit.WriteError(w, err)
    \t\t\treturn
    \t\t}
    \t\twriteJSON(w, 200, okID{true, c.ID})
    \t})
    \tmux.HandleFunc("GET /orders", func(w http.ResponseWriter, r *http.Request) {
    \t\tc, ok := k.Authenticate(w, r)
    \t\tif !ok {
    \t\t\treturn
    \t\t}
    \t\trows, err := k.DB.Query(
    \t\t\t"SELECT id, total FROM orders WHERE customer_id = ? ORDER BY id", c.ID)
    \t\tif err != nil {
    \t\t\tpanic(err)
    \t\t}
    \t\tdefer rows.Close()
    \t\tp := page{Items: []item{}}
    \t\tfor rows.Next() {
    \t\t\tvar it item
    \t\t\tif err := rows.Scan(&it.ID, &it.Total); err != nil {
    \t\t\t\tpanic(err)
    \t\t\t}
    \t\t\tp.Items = append(p.Items, it)
    \t\t}
    \t\tif err := k.DB.QueryRow("SELECT COUNT(*) FROM orders").Scan(&p.Count); err != nil {
    \t\t\tpanic(err)
    \t\t}
    \t\twriteJSON(w, 200, p)
    \t})
    \tmux.HandleFunc("POST /orders", func(w http.ResponseWriter, r *http.Request) {
    \t\tc, ok := k.Authenticate(w, r)
    \t\tif !ok {
    \t\t\treturn
    \t\t}
    \t\tres, err := k.DB.Exec(
    \t\t\t"INSERT INTO orders (customer_id, total) VALUES (?, ?)", c.ID, 5)
    \t\tif err != nil {
    \t\t\tpanic(err)
    \t\t}
    \t\tid, _ := res.LastInsertId()
    \t\twriteJSON(w, 201, map[string]int64{"id": id})
    \t})
    \tmux.HandleFunc("GET /boom", func(w http.ResponseWriter, r *http.Request) {
    \t\tif _, ok := k.Authenticate(w, r); !ok {
    \t\t\treturn
    \t\t}
    \t\tpanic("boom")
    \t})
    }
    """
)


@dataclass(frozen=True)
class Probe:
    name: str
    method: str
    path: str
    # ASCII only: Starlette's TestClient re-encodes a non-ASCII header value, so it
    # could not reach both arms as the same bytes (kit_test.go pins the Latin-1 reading)
    authorization: str | None


PROBES = [
    Probe("no_token", "GET", "/me", None),
    Probe("empty_header", "GET", "/me", ""),
    Probe("basic_scheme", "GET", "/me", "Basic tok-a"),
    Probe("lowercase_bearer", "GET", "/me", "bearer tok-a"),
    Probe("bearer_no_token", "GET", "/me", "Bearer "),
    Probe("bearer_blank_token", "GET", "/me", "Bearer    "),
    Probe("unknown_token", "GET", "/me", "Bearer nope"),
    Probe("token_padded", "GET", "/me", "Bearer   tok-a"),
    Probe("token_padded_tab", "GET", "/me", "Bearer \t tok-a"),
    Probe("customer_me", "GET", "/me", "Bearer tok-a"),
    Probe("seller_me", "GET", "/me", "Bearer tok-s"),
    Probe("support_me", "GET", "/me", "Bearer tok-u"),
    Probe("admin_me", "GET", "/me", "Bearer tok-admin"),
    Probe("admin_no_token", "GET", "/admin", None),
    Probe("admin_unknown_token", "GET", "/admin", "Bearer nope"),
    Probe("admin_as_customer", "GET", "/admin", "Bearer tok-a"),
    Probe("admin_as_seller", "GET", "/admin", "Bearer tok-s"),
    Probe("admin_as_admin", "GET", "/admin", "Bearer tok-admin"),
    Probe("staff_no_token", "GET", "/staff", None),
    Probe("staff_unknown_token", "GET", "/staff", "Bearer nope"),
    Probe("staff_as_customer", "GET", "/staff", "Bearer tok-a"),
    Probe("staff_as_seller", "GET", "/staff", "Bearer tok-s"),
    Probe("staff_as_support", "GET", "/staff", "Bearer tok-u"),
    Probe("staff_as_admin", "GET", "/staff", "Bearer tok-admin"),
    Probe("orders_list", "GET", "/orders", "Bearer tok-a"),
    Probe("orders_list_other", "GET", "/orders", "Bearer tok-b"),
    Probe("orders_list_no_token", "GET", "/orders", None),
    Probe("orders_create", "POST", "/orders", "Bearer tok-b"),
    Probe("orders_wrong_method", "DELETE", "/orders", "Bearer tok-a"),
    Probe("unrouted", "GET", "/nowhere", "Bearer tok-a"),
    Probe("crash", "GET", "/boom", "Bearer tok-a"),
]


@dataclass(frozen=True)
class Answer:
    status: int
    content_type: str
    body: bytes
    queries: int


def _headers(p: Probe) -> dict[str, str]:
    return {} if p.authorization is None else {"Authorization": p.authorization}


# ---- the Python arm ----------------------------------------------------------------------


@pytest.fixture
def python_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Answer]:
    work = tmp_path / "py"
    work.mkdir()
    shutil.copy(KIT_PY, work / "platform_kit.py")
    (work / "solution.py").write_text(PY_SOLUTION)
    monkeypatch.chdir(work)
    monkeypatch.syspath_prepend(str(work))
    counter = [0]

    def trace(sql: str) -> None:
        if _is_counted_statement(sql):
            counter[0] += 1

    answers = {}
    try:
        for p in PROBES:
            for name in ("solution", "platform_kit"):
                sys.modules.pop(name, None)
            kit = importlib.import_module("platform_kit")
            kit._on_connect = lambda conn: conn.set_trace_callback(trace)
            solution = importlib.import_module("solution")
            db = work / "app.db"
            db.unlink(missing_ok=True)
            build_db(db)
            counter[0] = 0
            with TestClient(solution.app, raise_server_exceptions=False) as client:
                r = client.request(p.method, p.path, headers=_headers(p))
            answers[p.name] = Answer(
                r.status_code, r.headers.get("content-type", ""), r.content, counter[0]
            )
    finally:
        for name in ("solution", "platform_kit"):
            sys.modules.pop(name, None)
    return answers


# ---- the Go arm --------------------------------------------------------------------------


def go_env() -> dict[str, str]:
    return {**os.environ, **GO_ENV}


def build_probe_app(out: Path) -> Path:
    """The probe handlers as a Go main module using the kit through a `replace`, built
    offline as arm G's solutions are."""
    out.mkdir(parents=True)
    (out / "go.mod").write_text(
        "module kitprobe\n\ngo 1.26.0\n\nrequire platformkit v0.0.0\n\n"
        f"replace platformkit => {KIT_GO}\n"
    )
    shutil.copy(KIT_GO / "go.sum", out / "go.sum")
    (out / "main.go").write_text(GO_MAIN)
    proc = subprocess.run(  # noqa: S603
        ["go", "build", "-trimpath", "-buildvcs=false", "-o", "app", "."],  # noqa: S607
        cwd=out,
        env=go_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
    return out / "app"


@pytest.fixture(scope="module")
def probe_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_probe_app(tmp_path_factory.mktemp("gokit") / "mod")


@pytest.fixture
def go_answers(probe_binary: Path, tmp_path: Path) -> dict[str, Answer]:
    answers = {}
    for i, p in enumerate(PROBES):
        db = tmp_path / f"go{i}.db"
        build_db(db)
        with GoServer(probe_binary, db) as server:
            # http.client sends a header value byte for byte (httpx's h11 refuses the
            # trailing blanks of "Bearer "), as Starlette's TestClient passes it on
            host, port = server.base_url.removeprefix("http://").rsplit(":", 1)
            conn = http.client.HTTPConnection(host, int(port), timeout=30)
            try:
                conn.request(p.method, p.path, headers=_headers(p))
                r = conn.getresponse()
                body = r.read()
            finally:
                conn.close()
        assert r.getheader(SQL_COUNT_HEADER) is not None, p.name
        answers[p.name] = Answer(
            r.status, r.getheader("content-type", ""), body, int(r.getheader(SQL_COUNT_HEADER))
        )
    return answers


# ---- the tests ---------------------------------------------------------------------------


def _canonical(a: Answer) -> Answer:
    # encoding/json's Encoder ends the document with a newline; Starlette's does not.
    # The body is the handler's (not the kit's) and a JSON parser ignores it.
    body = a.body[:-1] if a.body.endswith(b"}\n") else a.body
    return Answer(a.status, a.content_type, body, a.queries)


def test_go_kit_answers_every_probe_as_the_python_kit(
    python_answers: dict[str, Answer], go_answers: dict[str, Answer]
) -> None:
    diffs = {
        p.name: (python_answers[p.name], go_answers[p.name])
        for p in PROBES
        if _canonical(python_answers[p.name]) != _canonical(go_answers[p.name])
    }
    assert not diffs, diffs


def test_probes_exercise_the_kit(python_answers: dict[str, Answer]) -> None:
    # the probe set is meaningful: each kit outcome occurs
    got = {name: (a.status, a.body, a.queries) for name, a in python_answers.items()}
    assert got["no_token"] == (401, b'{"error":"unauthorized"}', 0)
    assert got["unknown_token"] == (401, b'{"error":"unauthorized"}', 1)
    assert got["admin_as_customer"] == (403, b'{"error":"forbidden"}', 1)
    assert got["staff_as_admin"][0] == 200
    assert got["orders_list"][2] == 3  # the caller lookup + the handler's two queries
    assert got["orders_create"][:2] == (201, b'{"id":4}')
    assert got["crash"][0] == 500


def test_no_count_header_without_trace(probe_binary: Path, tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    build_db(db)
    with (
        GoServer(probe_binary, db, trace_sql=False) as server,
        httpx.Client(base_url=server.base_url) as client,
    ):
        r = client.get("/me", headers={"Authorization": "Bearer tok-a"})
    assert r.status_code == 200
    assert SQL_COUNT_HEADER not in r.headers


def _go(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["go", *args],  # noqa: S607
        cwd=KIT_GO,
        env=go_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )


def test_go_vet_is_clean() -> None:
    proc = _go("vet", "./...")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_go_unit_tests_pass() -> None:
    proc = _go("test", "-count=1", "./...")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_kit_counting_rule_is_the_harness_rule() -> None:
    # kit_test.go checks the kit's rule against testdata/counted.json; here the same
    # table is checked against bench.api_runner._is_counted_statement.
    table = json.loads((KIT_GO / "testdata" / "counted.json").read_text())
    assert len(table) >= 20
    for want, sql in table:
        assert _is_counted_statement(sql) is want, sql
