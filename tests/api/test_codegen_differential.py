"""Differential test (spec §9): the FastAPI app generated from a Lattice program behaves
exactly like the Phase A runtime serving the same program.

For every task's reference program (tests/fixtures/api/<id>/lattice.py) and the three
worked examples of the Lattice prompt, each HTTP case runs through
(a) `bench.api_runner.run_cases_lattice` (the runtime, in process) and
(b) `bench.api_runner.run_cases_fastapi` (the generated app.py in the sandbox, over the
    benchmark's platform_kit),
and every case must agree on: status, the full JSON body (exact, type-strict equality),
the number of SQL statements, the task's after_sql rows, a digest of the whole database
after the request, and the harness's pass/fail verdict.

Besides the task's own cases, each endpoint gets probe cases (no/unknown/other tokens,
missing or mistyped parameters, a non-object body, wrong method, unknown path) so the
auth, validation and routing paths are compared too.

Full bodies are captured by test-local wrappers only (bench/ semantics are unchanged):
the sandbox driver `run_cases_fastapi` runs is extended with a capture line, and the
runtime app is wrapped in an ASGI shim that records the response.

The "realapps" program (tests/api/realapps.py) runs on its own profile and database
and covers the sub-project 1 features: Uuid keys (generated on insert), DateTime and
fn.now(), page totals in a custom page shape, response models, UNSET partial updates
(incl. explicit null), owner_is() 403s, natives in reads and requires, NativeToken
auth with an Admin flag column, and trailing-slash routes. Its natives module is copied
next to the generated app as `natives.py`. Values generated per request (a new uuid4,
the request's instant) differ between the two runs by nature, so bodies, after_sql rows
and the DB digest compare them through placeholders (`normalize`).

The registry (PROGRAMS, SETUPS) is also the Go equivalence gate's
(tests/gogen/test_go_differential.py): every program that checks for Go runs there
against the runtime the same way. "edges" (tests/api/edges.py) and "t2go" (the t2
program without natives) are Go-eligible programs added for it.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import shutil
import sqlite3
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

import bench.api_runner as api_runner
import lattice.api.runtime as runtime
from bench.api_datasets import TOKENS, api_profile, build_db
from bench.api_model import ApiTask, HttpCase
from bench.api_runner import CaseResults, classify, run_cases_fastapi, run_cases_lattice
from bench.api_tasks import API_TASKS
from lattice.api.codegen import compile_source_to_fastapi
from lattice.diagnostics import Severity
from lattice.llm.api_prompt import LATTICE_EXAMPLES
from tests.api import (
    edges,
    effects,
    gaps,
    hardening,
    loops,
    mine_owner,
    n1b,
    partb,
    realapps,
    rolesets,
    round2,
    solo,
    t2prog,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "api"
KIT_PATH = Path(__file__).resolve().parents[2] / "bench" / "api_kit" / "platform_kit.py"
CAPTURE_FILE = "_captured.jsonl"


# ---- the programs and their cases ---------------------------------------------------------


def _case(name: str, method: str, path: str, token: str | None, **kw: Any) -> HttpCase:
    return HttpCase(
        name=name,
        kind="happy",
        family="control",
        method=method,
        path=path,
        token=token,
        expect_status=kw.pop("status", 200),
        **kw,
    )


A, B, D, ADMIN = TOKENS["alice"], TOKENS["bob"], TOKENS["dave"], TOKENS["admin"]
_ADDR = "SELECT COUNT(*) FROM addresses"
_STOCK = "SELECT id, stock FROM products WHERE id <= 3 ORDER BY id"
_REVIEWS = "SELECT product_id, customer_id, rating, body FROM reviews ORDER BY id DESC LIMIT 2"

EXAMPLE_CASES: tuple[tuple[HttpCase, ...], ...] = (
    (
        _case("mine", "GET", "/addresses", A, after_sql=_ADDR),
        _case("page 2", "GET", "/addresses", A, query={"limit": 1, "offset": 1}),
        _case("admin sees all", "GET", "/addresses", ADMIN, query={"limit": 50}),
        _case("dave has none", "GET", "/addresses", D),
        _case("limit too big", "GET", "/addresses", A, query={"limit": 51}),
        _case("offset negative", "GET", "/addresses", A, query={"offset": -1}),
        _case("limit float text", "GET", "/addresses", A, query={"limit": "2.0"}),
    ),
    (
        _case("set", "PATCH", "/products/2/stock", ADMIN, json={"stock": 7}, after_sql=_STOCK),
        _case("customer", "PATCH", "/products/2/stock", A, json={"stock": 7}, after_sql=_STOCK),
        _case("missing", "PATCH", "/products/99999/stock", ADMIN, json={"stock": 7}),
        _case("too big", "PATCH", "/products/2/stock", ADMIN, json={"stock": 10001}),
        _case("float ok", "PATCH", "/products/3/stock", ADMIN, json={"stock": 5.0}),
        _case("bool bad", "PATCH", "/products/3/stock", ADMIN, json={"stock": True}),
        _case("no body", "PATCH", "/products/3/stock", ADMIN, after_sql=_STOCK),
    ),
    tuple(
        _case(
            f"review {who} {pid}",
            "POST",
            f"/products/{pid}/reviews",
            tok,
            json={"rating": 4, "body": "solid"},
            after_sql=_REVIEWS,
        )
        for who, tok in (("alice", A), ("bob", B), ("dave", D))
        for pid in range(1, 9)
    )
    + (
        _case("rating 0", "POST", "/products/1/reviews", A, json={"rating": 0, "body": ""}),
        _case("bad pid", "POST", "/products/0/reviews", A, json={"rating": 3, "body": "x"}),
        _case("too long", "POST", "/products/1/reviews", A, json={"rating": 3, "body": "x" * 501}),
    ),
)


# Input rules of the runtime (signed 64-bit Int, strict ASCII numerals, finite Float,
# UTF-8-encodable Str), probed in the path, the query string and the JSON body.
TEXT_VALUES: tuple[tuple[str, str], ...] = (
    ("int max", "9223372036854775807"),
    ("int max+1", "9223372036854775808"),
    ("int min-1", "-9223372036854775809"),
    ("5000 digits", "9" * 5000),  # beyond int()'s digit limit
    ("arabic digits", "\u0661\u0662"),
    ("fullwidth digit", "\uff17"),
    ("padded", " 7 "),
    ("plus", "+3"),
    ("underscore", "1_0"),
    ("inf", "inf"),
    ("nan", "nan"),
    ("-Infinity", "-Infinity"),
    ("1e999", "1e999"),
    ("exponent", "2.5e2"),
    ("dot", ".5"),
    ("hex", "0x10"),
)
BODY_VALUES: tuple[tuple[str, object], ...] = (
    ("int max", 2**63 - 1),
    ("int max+1", 2**63),
    ("int min-1", -(2**63) - 1),
    ("10**400", 10**400),  # overflows float()
    ("integral float", 3.0),
    ("fraction", 2.5),
    ("1e300", 1e300),
    ("bool", True),
    ("digits text", "12"),
    ("emoji", "ok \U0001f600"),
)
# (NaN, infinities and lone surrogates cannot be sent through the harness's `json=`:
# the HTTP client refuses to encode them. test_codegen.py sends them as raw bodies.)


def _probes(cases: tuple[HttpCase, ...]) -> tuple[HttpCase, ...]:
    """Auth, validation and routing probes for each distinct endpoint the cases call."""
    out: list[HttpCase] = []
    seen: set[tuple[str, str]] = set()
    for base in cases:
        route = (base.method, re.sub(r"\d+", "N", base.path))
        if route in seen:
            continue
        seen.add(route)
        n = len(seen)

        def probe(tag: str, base: HttpCase = base, n: int = n, **changes: Any) -> None:
            fields = {"name": f"probe {n} {tag}", "kind": "happy", "after_sql": None}
            fields |= {"after_expect": None, "expect_json": None, "max_queries": None}
            out.append(dataclasses.replace(base, **(fields | changes)))

        probe("no token", token=None)
        probe("unknown token", token="tok-nobody")
        probe("admin", token=TOKENS["admin"])
        probe("other customer", token=TOKENS["bob"])
        probe("carol", token=TOKENS["carol"])
        if base.json is not None:
            probe("empty body", json={})
            probe("list body", json=[1, 2])
            probe("no body", json=None)
            probe("strings", json={k: "abc" for k in base.json})
            probe("nulls", json={k: None for k in base.json})
            probe("negatives", json={k: -1 for k in base.json})
            for key in base.json:  # one parameter at a time: every input rule
                for tag, value in BODY_VALUES:
                    probe(f"body {key} {tag}", json={**base.json, key: value})
        if base.query:
            probe("bad query", query={k: "abc" for k in base.query})
            probe("huge query", query={k: "1000000000" for k in base.query})
            for key in base.query:
                for tag, text in TEXT_VALUES:
                    probe(f"query {key} {tag}", query={**base.query, key: text})
        if re.search(r"\d", base.path):
            probe("text id", path=re.sub(r"\d+", "abc", base.path, count=1))
            probe("zero id", path=re.sub(r"\d+", "0", base.path, count=1))
            probe("big id", path=re.sub(r"\d+", "987654", base.path, count=1))
            for tag, text in TEXT_VALUES:
                if "/" not in text and text.strip():
                    probe(f"path {tag}", path=re.sub(r"\d+", text, base.path, count=1))
        other = "PUT" if base.method != "PUT" else "PATCH"
        probe("wrong method", method=other)
        probe("unknown path", path=base.path.rstrip("/") + "/no-such-route")
    return tuple(out)


def _programs() -> dict[str, tuple[str, ApiTask]]:
    out: dict[str, tuple[str, ApiTask]] = {}
    for task in API_TASKS:
        source = (FIXTURES / task.id / "lattice.py").read_text()
        cases = task.cases + _probes(task.cases)
        out[task.id] = (source, dataclasses.replace(task, cases=cases))
    for i, (source, cases) in enumerate(zip(LATTICE_EXAMPLES, EXAMPLE_CASES, strict=True), 1):
        task = ApiTask(f"example{i}", "control", f"worked example {i}", cases + _probes(cases))
        out[task.id] = (source, task)
    task = ApiTask("numeric", "control", "non-finite results", NUMERIC_CASES)
    out[task.id] = (
        NUMERIC_PROGRAM,
        dataclasses.replace(task, cases=task.cases + _probes(task.cases)),
    )
    f01 = next(t for t in API_TASKS if t.id == "f01")
    out["mapped_join"] = (
        MAPPED_JOIN_PROGRAM,
        dataclasses.replace(f01, id="mapped_join", cases=f01.cases + _probes(f01.cases)),
    )
    return out


# f01 written with a join on differently-named keys (orders.id = per-order totals'
# order_id): the plan's SQL joins ON l."id" = r."order_id"; both backends must agree
# and pass every f01 case.
MAPPED_JOIN_PROGRAM = """# lattice: v1
@endpoint("GET", "/orders", auth=Customer)
def list_orders(
    user: CurrentUser, limit: Int(ge=1, le=100) = 20, offset: Int(ge=0) = 0
) -> Page:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    items = fetch(OrderItems)
    my_items = owned(items, by=user)
    units_per_order = group(my_items, by=["order_id"], units=agg.sum(col("qty")))
    joined = join(mine, units_per_order, on={"id": "order_id"}, how="left")
    filled = derive(joined, units=coalesce(col("units"), 0))
    shown = select(filled, cols=["id", "status", "created_at", "total", "units"])
    ordered = sort(shown, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
"""


# A program whose responses contain non-finite floats (price * 1e308 * 10 overflows to
# inf in SQLite): the runtime serializes them as JSON null. It also takes a Float query
# parameter and an Int offset, so the numeric probes reach both.
NUMERIC_PROGRAM = """# lattice: v1
@endpoint("GET", "/products/{product_id}/overflow", auth=Public)
def overflow(product_id: Int(ge=1)) -> One | NotFound:
    products = fetch(Products)
    found = where(products, col("id") == product_id)
    big = derive(found, huge=col("price") * 1e308 * 10.0, tiny=col("price") * 2.0)
    slim = select(big, cols=["id", "huge", "tiny"])
    product = one(slim, else_=NotFound("product"))
    return product


@endpoint("GET", "/products/pricey", auth=Public)
def pricey(
    min_price: Float(ge=-1e300, le=1e300) = 0.0,
    limit: Int(ge=1, le=100) = 5,
    offset: Int(ge=0) = 0,
) -> Page:
    products = fetch(Products)
    found = where(products, col("price") >= min_price)
    big = derive(found, huge=col("price") * 1e308 * 10.0)
    slim = select(big, cols=["id", "price", "huge"])
    ordered = sort(slim, by=["id"])
    result = page(ordered, limit=limit, offset=offset)
    return result
"""
NUMERIC_CASES = (
    _case("overflow 1", "GET", "/products/1/overflow", None),
    _case("overflow 2", "GET", "/products/2/overflow", None),
    _case("pricey", "GET", "/products/pricey", None, query={"min_price": 10, "offset": 1}),
    _case("pricey big offset", "GET", "/products/pricey", None, query={"offset": 2**63 - 1}),
)

# ---- the real-apps program (sub-project 1 features) -------------------------------------

_R = realapps
_RT = _R.TOKENS
_ITEMS_AFTER = "SELECT id, title, description, owner_id, created_at FROM item ORDER BY rowid"
_USERS_AFTER = 'SELECT id, email, full_name, created_at FROM "user" ORDER BY rowid'


def _rcase(name: str, method: str, path: str, who: str | None, **kw: Any) -> HttpCase:
    return _case(name, method, path, None if who is None else _RT[who], **kw)


def _rid(n: int) -> str:
    return _R.uid(n)


REALAPPS_CASES: tuple[HttpCase, ...] = (
    # page totals, the page shape, response models, trailing slashes
    _rcase("items alice", "GET", "/items/", "alice"),
    _rcase("items page 2", "GET", "/items/", "alice", query={"limit": 2, "skip": 2}),
    _rcase("items past the end", "GET", "/items/", "alice", query={"skip": 50}),
    _rcase("items admin", "GET", "/items/", "admin", query={"limit": 2, "skip": 1}),
    _rcase("items bob", "GET", "/items/", "bob"),
    _rcase("items redirect", "GET", "/items", "alice", query={"limit": 1}),
    _rcase("items bad limit", "GET", "/items/", "alice", query={"limit": 0}, status=422),
    _rcase("users admin", "GET", "/users/", "admin", query={"limit": 3}),
    _rcase("users customer", "GET", "/users/", "alice", status=403),
    _rcase("since default", "GET", "/items/since", "alice"),
    _rcase(
        "since offset", "GET", "/items/since", "alice", query={"after": "2000-01-03T11:00+02:00"}
    ),
    _rcase("since naive", "GET", "/items/since", "bob", query={"after": "2000-01-01T00:00:00"}),
    _rcase("since bad", "GET", "/items/since", "alice", query={"after": "soon"}, status=422),
    _rcase(
        "since too early",
        "GET",
        "/items/since",
        "alice",
        query={"after": "0001-01-01T00:00:00+01:00"},
        status=422,
    ),
    _rcase(
        "since too late",
        "GET",
        "/items/since",
        "alice",
        query={"after": "9999-12-31T23:00:00-05:00"},
        status=422,
    ),
    _rcase("to_uuid to_datetime", "GET", "/admin/alice-items", "admin"),
    # Uuid parameters and owner_is() (403) / 404
    _rcase("item own", "GET", f"/items/{_rid(101)}", "alice"),
    _rcase("item 32 hex", "GET", "/items/" + _rid(102).replace("-", "").upper(), "alice"),
    _rcase("item other 403", "GET", f"/items/{_rid(103)}", "alice", status=403),
    _rcase("item admin", "GET", f"/items/{_rid(103)}", "admin"),
    _rcase("item missing", "GET", f"/items/{_rid(999)}", "alice", status=404),
    _rcase("item bad uuid", "GET", "/items/not-a-uuid", "alice", status=422),
    _rcase("note own", "GET", f"/notes/{_rid(201)}", "alice"),
    _rcase("note other 403", "GET", f"/notes/{_rid(202)}", "alice", status=403),
    # inserts: a generated uuid4 key, fn.now(), optional values
    _rcase("create", "POST", "/items/", "alice", json={"title": "Made"}, status=201),
    _rcase(
        "create described",
        "POST",
        "/items/",
        "bob",
        json={"title": "Made", "description": "d"},
        status=201,
        after_sql=_ITEMS_AFTER,
    ),
    _rcase(
        "create null description",
        "POST",
        "/items/",
        "alice",
        json={"title": "M", "description": None},
        status=201,
    ),
    _rcase("create null title", "POST", "/items/", "alice", json={"title": None}, status=422),
    _rcase(
        "copy with given id",
        "POST",
        f"/items/{_rid(101)}/copy",
        "alice",
        json={"new_id": "AAAAAAAA-0000-4000-8000-000000000999"},
        status=201,
        after_sql=_ITEMS_AFTER,
    ),
    _rcase(
        "copy bad id",
        "POST",
        f"/items/{_rid(101)}/copy",
        "alice",
        json={"new_id": "nope"},
        status=422,
    ),
    # UNSET partial updates
    _rcase(
        "update title",
        "PUT",
        f"/items/{_rid(101)}",
        "alice",
        json={"title": "New"},
        after_sql=_ITEMS_AFTER,
    ),
    _rcase(
        "update description null",
        "PUT",
        f"/items/{_rid(101)}",
        "alice",
        json={"description": None},
        after_sql=_ITEMS_AFTER,
    ),
    _rcase("update nothing", "PUT", f"/items/{_rid(104)}", "alice", json={}),
    _rcase(
        "update title null",
        "PUT",
        f"/items/{_rid(101)}",
        "alice",
        json={"title": None},
        status=422,
    ),
    _rcase(
        "update other 403",
        "PUT",
        f"/items/{_rid(103)}",
        "alice",
        json={"title": "x"},
        status=403,
        after_sql=_ITEMS_AFTER,
    ),
    _rcase(
        "update missing", "PUT", f"/items/{_rid(998)}", "alice", json={"title": "x"}, status=404
    ),
    _rcase(
        "update me",
        "PATCH",
        "/users/me",
        "bob",
        json={"full_name": "Bobby"},
        after_sql=_USERS_AFTER,
    ),
    _rcase("update me email null", "PATCH", "/users/me", "bob", json={"email": None}, status=422),
    _rcase(
        "update me email taken",
        "PATCH",
        "/users/me",
        "bob",
        json={"email": "alice@example.com"},
        status=409,
        after_sql=_USERS_AFTER,
    ),
    _rcase(
        "delete own", "DELETE", f"/items/{_rid(104)}", "alice", status=204, after_sql=_ITEMS_AFTER
    ),
    _rcase("delete other", "DELETE", f"/items/{_rid(104)}", "bob", status=403),
    # natives and NativeToken auth
    _rcase("me", "GET", "/users/me", "alice"),
    _rcase("me trailing slash", "GET", "/users/me/", "carol"),
    _case("me unknown user", "GET", "/users/me", f"tok-{_rid(777)}", status=401),
    _case("me bad token", "GET", "/users/me", "garbage", status=401),
    _rcase(
        "signup",
        "POST",
        "/users/signup",
        None,
        json={"email": "new@example.com", "password": "longpassword"},
        status=201,
        after_sql=_USERS_AFTER,
    ),
    _rcase(
        "signup short",
        "POST",
        "/users/signup",
        None,
        json={"email": "x@example.com", "password": "short"},
        status=422,
    ),
    _rcase(
        "login",
        "POST",
        "/login/access-token",
        None,
        json={"username": "alice@example.com", "password": "alice-pass"},
    ),
    _rcase(
        "login wrong",
        "POST",
        "/login/access-token",
        None,
        json={"username": "alice@example.com", "password": "nope"},
        status=400,
    ),
    _rcase(
        "login inactive",
        "POST",
        "/login/access-token",
        None,
        json={"username": "carol@example.com", "password": "carol-pw"},
        status=400,
    ),
    _rcase("loud", "GET", f"/items/{_rid(101)}/loud", "alice"),
    _rcase("loud empty", "GET", f"/items/{_rid(105)}/loud", "bob"),
    _rcase("loud require", "GET", f"/items/{_rid(102)}/loud", "alice", status=400),
    # fn.now() once per request, fn.date
    _rcase("clock", "GET", f"/clock/{_rid(101)}", "alice"),
    _rcase("clock not mine", "GET", f"/clock/{_rid(103)}", "alice", status=404),
)


# ---- the Part B program (sub-project 1b: active check, error bodies, DateTime wire,
# Email / pattern= parameters, unique_or, list constants) -------------------------------

_PB = partb


def _pbcase(name: str, method: str, path: str, who: str | None, **kw: Any) -> HttpCase:
    return _case(name, method, path, None if who is None else _PB.TOKENS[who], **kw)


PARTB_CASES: tuple[HttpCase, ...] = (
    # B1: inactive (carol) before the role check and before validation
    _pbcase("inactive items", "GET", "/items/", "carol", status=400),
    _pbcase("inactive admin list", "GET", "/users/", "carol", status=400),
    _pbcase("inactive bad limit", "GET", "/items/", "carol", query={"limit": "x"}, status=400),
    _pbcase(
        "inactive bad email",
        "PATCH",
        "/users/me",
        "carol",
        json={"email": "nope"},
        status=400,
        after_sql=_USERS_AFTER,
    ),
    _case("unknown user", "GET", "/users/me", f"tok-{_rid(777)}", status=401),
    # B2: {"detail": ...} bodies, configured 401/403 texts
    _pbcase("no token", "GET", "/items/", None, status=401),
    _case("bad token", "GET", "/items/", "garbage", status=401),
    _pbcase("customer admin list", "GET", "/users/", "alice", status=403),
    _pbcase("declared 403", "GET", f"/items/{_rid(103)}", "alice", status=403),
    _pbcase("declared 404", "GET", f"/items/{_rid(999)}", "alice", status=404),
    _pbcase("validation", "GET", "/items/", "alice", query={"limit": 0, "skip": -1}, status=422),
    # B3: naive DateTime wire
    _pbcase("items", "GET", "/items/", "alice"),
    _pbcase("item", "GET", f"/items/{_rid(101)}", "alice"),
    _pbcase("admin list", "GET", "/users/", "admin", query={"limit": 3}),
    # B4: Email, pattern=
    _pbcase(
        "bad email",
        "PATCH",
        "/users/me",
        "bob",
        json={"email": "not-an-email"},
        status=422,
        after_sql=_USERS_AFTER,
    ),
    _pbcase(
        "good email",
        "PATCH",
        "/users/me",
        "bob",
        json={"email": "bob.new@example.org"},
        after_sql=_USERS_AFTER,
    ),
    _pbcase(
        "email too long",
        "PATCH",
        "/users/me",
        "bob",
        # 255 characters, a valid address: over RFC 5321's 254 (Email refuses it)
        json={"email": "b" * 64 + "@" + ".".join(["d" * 63, "d" * 63, "d" * 50]) + ".example.com"},
        status=422,
        after_sql=_USERS_AFTER,
    ),
    _pbcase(
        "signup bad email",
        "POST",
        "/users/signup",
        None,
        json={"email": "a@b", "password": "longpassword"},
        status=422,
    ),
    _pbcase("pattern miss", "POST", "/items/", "alice", json={"title": "lower"}, status=422),
    _pbcase(
        "pattern hit",
        "POST",
        "/items/",
        "alice",
        json={"title": "Upper Case"},
        status=201,
        after_sql=_ITEMS_AFTER,
    ),
    # B5: unique_or on update and insert
    _pbcase(
        "email taken",
        "PATCH",
        "/users/me",
        "bob",
        json={"email": "alice@example.com"},
        status=409,
        after_sql=_USERS_AFTER,
    ),
    _pbcase(
        "signup taken",
        "POST",
        "/users/signup",
        None,
        json={"email": "alice@example.com", "password": "longpassword"},
        status=409,
        after_sql=_USERS_AFTER,
    ),
    _pbcase(
        "signup",
        "POST",
        "/users/signup",
        None,
        json={"email": "new@example.com", "password": "longpassword"},
        status=201,
        after_sql=_USERS_AFTER,
    ),
    # B6: list constants
    _pbcase("me", "GET", "/users/me", "alice"),
    _pbcase("brief", "GET", f"/items/{_rid(101)}/brief", "alice"),
)


# ---- the loops program (sub-project 1b N2: window + win.*, traverse) -----------------------

_LT = loops.TOKENS


def _lcase(name: str, method: str, path: str, who: str | None, **kw: Any) -> HttpCase:
    return _case(name, method, path, None if who is None else _LT[who], **kw)


LOOPS_CASES: tuple[HttpCase, ...] = (
    _lcase("running alice", "GET", "/expenses/running", "alice"),
    _lcase("running bob", "GET", "/expenses/running", "bob"),
    _lcase("running admin", "GET", "/expenses/running", "admin"),
    _lcase("running page 2", "GET", "/expenses/running", "alice", query={"limit": 2, "offset": 2}),
    _lcase("running anonymous", "GET", "/expenses/running", None, status=401),
    _lcase("tree root", "GET", "/categories/1/tree", None),
    _lcase("tree depth 1", "GET", "/categories/1/tree", None, query={"depth": 1}),
    _lcase("tree depth 0", "GET", "/categories/1/tree", None, query={"depth": 0}, status=422),
    _lcase("tree depth 11", "GET", "/categories/1/tree", None, query={"depth": 11}, status=422),
    _lcase("tree cycle", "GET", "/categories/21/tree", None),
    _lcase("tree leaf", "GET", "/categories/6/tree", None),
    _lcase("tree missing", "GET", "/categories/99/tree", None),
    _lcase("path leaf", "GET", "/categories/5/path", None),
    _lcase("path cycle", "GET", "/categories/20/path", None),
    _lcase("folders alice", "GET", "/folders/1/subtree", "alice"),
    _lcase("folders bob in alice's", "GET", "/folders/1/subtree", "bob"),
    _lcase("folders bob", "GET", "/folders/4/subtree", "bob"),
    _lcase("folders admin", "GET", "/folders/1/subtree", "admin"),
)


# ---- the effects program (sub-project 1b N3: for_each through the outbox) ---------------------

_ET = effects.TOKENS
_OUTBOX = effects.OUTBOX_AFTER  # the outbox rows, without id, created_at and claimed_at
_HAS_OUTBOX = "SELECT COUNT(*) FROM sqlite_master WHERE name = 'lattice_outbox'"
_ITEMS = "SELECT id, owner_id, title, due, done FROM items ORDER BY id"


def _ecase(name: str, method: str, path: str, who: str | None, **kw: Any) -> HttpCase:
    return _case(name, method, path, None if who is None else _ET[who], **kw)


EFFECTS_CASES: tuple[HttpCase, ...] = (
    _ecase(
        "signup",
        "POST",
        "/users/signup",
        None,
        json={"email": "new@example.com", "full_name": "New"},
        status=201,
        after_sql=_OUTBOX,
    ),
    _ecase(
        "signup taken (rolled back)",
        "POST",
        "/users/signup",
        None,
        json={"email": "alice@example.com", "full_name": "Dup"},
        status=409,
        after_sql=_HAS_OUTBOX,
    ),
    _ecase(
        "create", "POST", "/items", "alice", json={"title": "Fresh"}, status=201, after_sql=_OUTBOX
    ),
    _ecase(
        "create, dispatch fails",
        "POST",
        "/items",
        "alice",
        json={"title": "boom"},
        status=201,
        after_sql=_OUTBOX,
    ),
    _ecase(
        "create refused after the outbox insert",
        "POST",
        "/items",
        "alice",
        json={"title": "forbidden"},
        status=400,
        after_sql=_HAS_OUTBOX,
    ),
    _ecase("recent alice", "GET", "/items/recent", "alice", after_sql=_OUTBOX),
    _ecase(
        "recent alice 2",
        "GET",
        "/items/recent",
        "alice",
        query={"note": "hi", "limit": 2},
        after_sql=_OUTBOX,
    ),
    _ecase("recent carol (none)", "GET", "/items/recent", "carol", after_sql=_OUTBOX),
    _ecase(
        "recent admin", "GET", "/items/recent", "admin", query={"limit": 100}, after_sql=_OUTBOX
    ),
    _ecase("finish", "PATCH", "/items/1/done", "alice", after_sql=_OUTBOX),
    _ecase("finish null due", "PATCH", "/items/2/done", "alice", after_sql=_OUTBOX),
    _ecase("finish not mine", "PATCH", "/items/3/done", "alice", status=404, after_sql=_ITEMS),
)


# ---- the gap-features program (edit-slice gaps: contains ignore_case, sent(), DateTime
# arithmetic, `| None` parameters, null writes, sort direction from a parameter) ---------

_G = gaps
_GITEMS = "SELECT id, title, description, created_at, deleted_at FROM item ORDER BY rowid"


def _gcase(name: str, method: str, path: str, who: str | None, **kw: Any) -> HttpCase:
    return _case(name, method, path, None if who is None else _G.TOKENS[who], **kw)


GAPS_CASES: tuple[HttpCase, ...] = (
    _gcase("list", "GET", "/items/", "alice"),
    _gcase("search upper", "GET", "/items/", "alice", query={"q": "GROC"}),
    _gcase("search a", "GET", "/items/", "alice", query={"q": "a", "limit": 1, "skip": 1}),
    _gcase("search empty", "GET", "/items/", "alice", query={"q": ""}),
    _gcase("search unicode", "GET", "/items/", "alice", query={"q": "ÄLPHA"}),
    _gcase("search admin", "GET", "/items/", "admin", query={"q": "e"}),
    _gcase("since", "GET", "/items/", "bob", query={"since": "2000-01-04T12:00:00+02:00"}),
    _gcase("since naive", "GET", "/items/", "alice", query={"since": "2000-01-05T10:00:00"}),
    _gcase("since bad", "GET", "/items/", "alice", query={"since": "yesterday"}, status=422),
    _gcase("oldest", "GET", "/items/", "bob", query={"sort": "oldest"}),
    _gcase("oldest admin", "GET", "/items/", "admin", query={"sort": "oldest", "limit": 3}),
    _gcase("newest admin", "GET", "/items/", "admin", query={"sort": "newest", "skip": 2}),
    _gcase("sort bad", "GET", "/items/", "bob", query={"sort": "random"}, status=422),
    _gcase("ordered", "GET", "/items/ordered", "admin"),
    _gcase("ordered desc", "GET", "/items/ordered", "admin", query={"newest_first": "true"}),
    _gcase("ordered last", "GET", "/items/ordered-last", "admin", query={"newest_first": "1"}),
    _gcase("ordered last asc", "GET", "/items/ordered-last", "bob"),
    _gcase(
        "title locked",
        "PUT",
        f"/items/{_G.uid(101)}",
        "alice",
        json={"title": "Renamed"},
        status=403,
        after_sql=_GITEMS,
    ),
    _gcase(
        "description only",
        "PUT",
        f"/items/{_G.uid(101)}",
        "alice",
        json={"description": "fine"},
        after_sql=_GITEMS,
    ),
    _gcase("title admin", "PUT", f"/items/{_G.uid(101)}", "admin", json={"title": "Fixed"}),
    _gcase("title undated", "PUT", f"/items/{_G.UNDATED}", "bob", json={"title": "Dated"}),
    _gcase("title fresh", "PUT", f"/items/{_G.FUTURE}", "carol", json={"title": "Soon"}),
    _gcase(
        "title other", "PUT", f"/items/{_G.FUTURE}", "alice", json={"title": "x"}, status=403
    ),
    _gcase(
        "title deleted", "PUT", f"/items/{_G.DELETED}", "alice", json={"title": "x"}, status=404
    ),
    _gcase(
        "create null description",
        "POST",
        "/items/",
        "alice",
        json={"title": "New"},
        status=201,
        after_sql=_GITEMS,
    ),
    _gcase(
        "soft delete",
        "DELETE",
        f"/items/{_G.uid(101)}",
        "alice",
        status=204,
        after_sql=_GITEMS,
    ),
    _gcase(
        "restore",
        "POST",
        f"/items/{_G.DELETED}/restore",
        "admin",
        after_sql=_GITEMS,
    ),
    _gcase(
        "restore live", "POST", f"/items/{_G.uid(101)}/restore", "admin", status=400
    ),
    _gcase("restore customer", "POST", f"/items/{_G.DELETED}/restore", "alice", status=403),
    _gcase("times", "GET", f"/items/{_G.uid(101)}/times", "alice"),
    _gcase(
        "times start", "GET", f"/items/{_G.uid(102)}/times", "alice",
        query={"start": "2000-01-03T12:00:01+01:00"},
    ),  # fmt: skip
    _gcase("times undated", "GET", f"/items/{_G.UNDATED}/times", "bob"),
)


# ---- mine(t, by=user) vs owned(t, by=user) (tests/api/mine_owner.py) ----------------------

_M = mine_owner
_MITEMS = "SELECT id, title, owner_id FROM item ORDER BY rowid"


def _mcase(name: str, method: str, path: str, who: str | None, **kw: Any) -> HttpCase:
    return _case(name, method, path, None if who is None else _M.TOKENS[who], **kw)


MINE_CASES: tuple[HttpCase, ...] = (
    *(
        _mcase(f"{path} {who}", "GET", path, who)
        for path in ("/items/mine", "/items/visible", "/items/mine/stats")
        for who in ("admin", "alice", "bob", "carol")
    ),
    _mcase("mine page", "GET", "/items/mine", "alice", query={"limit": 1, "skip": 1}),
    *(
        _mcase(f"{path} {who}", "GET", path, who)
        for path in ("/notes/mine", "/notes/visible")
        for who in ("admin", "alice", "bob", "carol")
    ),
    _mcase(
        "admin retitles alice's", "PUT", f"/items/mine/{_M.ALPHA}", "admin",
        json={"title": "hijacked"}, status=404, after_sql=_MITEMS,
    ),  # fmt: skip
    _mcase(
        "admin retitles own", "PUT", f"/items/mine/{_M.ZETA}", "admin",
        json={"title": "Zeta 2"}, after_sql=_MITEMS,
    ),  # fmt: skip
    _mcase(
        "alice retitles own", "PUT", f"/items/mine/{_M.ALPHA}", "alice",
        json={"title": "A2"}, after_sql=_MITEMS,
    ),  # fmt: skip
)


# ---- round-2 fixes: null order, caller references, accessible, owner transfer ---------------
# (tests/api/round2.py)

_R2 = round2
_R2ITEMS = "SELECT id, title, description, owner_id FROM item ORDER BY rowid"


def _r2case(name: str, method: str, path: str, who: str | None, **kw: Any) -> HttpCase:
    return _case(name, method, path, None if who is None else _R2.TOKENS[who], **kw)


ROUND2_CASES: tuple[HttpCase, ...] = (
    _r2case("null order asc", "GET", "/sorted/descriptions", "admin"),
    _r2case("null order desc", "GET", "/sorted/descriptions", "admin", query={"newest": True}),
    _r2case("explicit nulls last", "GET", "/sorted/descriptions/last", "admin"),
    _r2case("window null order", "GET", "/sorted/numbered", "admin"),
    *(_r2case(f"user.id {who}", "GET", "/me/items", who) for who in ("admin", "alice", "bob")),
    *(_r2case(f"caller derive {who}", "GET", "/me/flags", who) for who in ("admin", "bob")),
    _r2case("stamp own", "PUT", f"/items/{_R2.ALPHA}/stamp", "alice", after_sql=_R2ITEMS),
    _r2case(
        "stamp as admin", "PUT", f"/items/{_R2.ZETA}/stamp", "admin", status=403,
        after_sql=_R2ITEMS,
    ),  # fmt: skip
    _r2case(
        "stamp other's", "PUT", f"/items/{_R2.GAMMA}/stamp", "alice", status=404,
        after_sql=_R2ITEMS,
    ),  # fmt: skip
    _r2case(
        "signed insert", "POST", "/items/signed", "bob", json={"title": "New"},
        after_sql="SELECT title, description, owner_id FROM item ORDER BY rowid",
    ),  # fmt: skip
    *(
        _r2case(
            f"transfer {who} {email}", "POST", f"/items/{item}/transfer", who,
            json={"new_owner_email": email}, status=status, after_sql=_R2ITEMS,
        )  # fmt: skip
        for who, item, email, status in (
            ("alice", _R2.ALPHA, "bob@example.com", 200),
            ("alice", _R2.ALPHA, "alice@example.com", 400),
            ("alice", _R2.ALPHA, "nobody@example.com", 404),
            ("alice", _R2.ALPHA, "carol@example.com", 400),
            ("bob", _R2.ALPHA, "bob@example.com", 403),
            ("admin", _R2.GAMMA, "alice@example.com", 200),
            ("alice", _R2.NOBODY, "bob@example.com", 404),
        )
    ),
    *(
        _r2case(
            f"give {who} {owner[-3:]}", "PUT", f"/items/{item}/owner", who,
            json={"owner_id": owner}, status=status, after_sql=_R2ITEMS,
        )  # fmt: skip
        for who, item, owner, status in (
            ("alice", _R2.ALPHA, _R2.CAROL, 200),
            ("alice", _R2.ALPHA, _R2.NOBODY, 404),
            ("alice", _R2.GAMMA, _R2.CAROL, 404),
            ("admin", _R2.GAMMA, _R2.ADMIN, 200),
        )
    ),
    _r2case("claim", "PUT", f"/items/{_R2.GAMMA}/claim", "admin", after_sql=_R2ITEMS),
    _r2case(
        "give all", "PUT", "/items/mine/owner", "alice", json={"owner_id": _R2.BOB},
        status=204, after_sql=_R2ITEMS,
    ),  # fmt: skip
    _r2case(
        "give all nobody", "PUT", "/items/mine/owner", "alice",
        json={"owner_id": _R2.NOBODY}, status=409, after_sql=_R2ITEMS,
    ),  # fmt: skip
)


# ---- procedures called from endpoints (sub-project 1c T2b; tests/api/t2prog.py) -----------

_T2 = t2prog


def _t2case(name: str, method: str, path: str, who: str | None, **kw: Any) -> HttpCase:
    return _case(name, method, path, None if who is None else _T2.TOKENS[who], **kw)


T2_CASES: tuple[HttpCase, ...] = (
    # scalar procedures in derive (row(), user, fn.now()), a page(total=True) after them
    *(_t2case(f"items {who}", "GET", "/items", who) for who in ("alice", "bob", "carol", "admin")),
    _t2case("items page 2", "GET", "/items", "alice", query={"limit": 2, "offset": 1}),
    _t2case("items past the end", "GET", "/items", "alice", query={"offset": 9}),
    _t2case("items bad limit", "GET", "/items", "alice", query={"limit": 0}, status=422),
    # a procedure filter after page()
    _t2case("long alice", "GET", "/items/long", "alice"),
    _t2case("long admin", "GET", "/items/long", "admin", query={"min_len": 8, "limit": 3}),
    _t2case("long none", "GET", "/items/long", "bob", query={"min_len": 20}),
    # require() with procedures; a T2-post read without page
    _t2case("item own", "GET", "/items/1", "alice"),
    _t2case("item short 403", "GET", "/items/2", "alice", status=403),
    _t2case("item other 404", "GET", "/items/3", "alice", status=404),
    _t2case("item done own", "GET", "/items/5", "alice"),
    _t2case("item admin", "GET", "/items/3", "admin"),
    _t2case("long item", "GET", "/items/1/long", "alice"),
    _t2case("long item short", "GET", "/items/2/long", "alice", status=404),
    # whole-value calls: a List of records, a scalar written by update
    *(_t2case(f"digest {who}", "GET", "/digest", who) for who in ("alice", "bob", "carol")),
    _t2case(
        "rescore",
        "PUT",
        "/items/1/rescore",
        "alice",
        json={"factor": 2},
        after_sql=_T2.SCORES_AFTER,
    ),
    _t2case("rescore default", "PUT", "/items/3/rescore", "bob", json={}),
    _t2case("rescore other", "PUT", "/items/3/rescore", "alice", json={}, status=404),
    _t2case(
        "rescore bad factor", "PUT", "/items/1/rescore", "alice", json={"factor": 11}, status=422
    ),
    # insert_many of a procedure's rows
    _t2case("expand", "POST", "/events/expand", "admin", json={"n": 3}, after_sql=_T2.EVENTS_AFTER),
    _t2case(
        "expand ok",
        "POST",
        "/events/expand",
        "admin",
        json={"n": 2, "kind": "ok"},
        after_sql=_T2.EVENTS_AFTER,
    ),
    _t2case("expand none", "POST", "/events/expand", "admin", json={"n": 0}),
    _t2case("expand customer", "POST", "/events/expand", "alice", json={"n": 1}, status=403),
    # an effectful procedure through for_each and the outbox
    _t2case("notify", "POST", "/items/1/notify", "alice", after_sql=_T2.OUTBOX_AFTER),
    _t2case("notify fails", "POST", "/items/4/notify", "alice", after_sql=_T2.OUTBOX_AFTER),
    _t2case("notify other", "POST", "/items/3/notify", "alice", status=404),
    # the l12 state machine
    _t2case("replay", "GET", "/logins/replay", "admin"),
    _t2case("replay customer", "GET", "/logins/replay", "alice", status=403),
    # an Int handed to a Float procedure parameter (R-widen, api/ir.py): a parameter, a
    # column, constants and a caller column all arrive as floats
    _t2case("floats", "GET", "/items/1/floats", "alice", query={"n": 7}),
    _t2case("floats negative", "GET", "/items/4/floats", "alice", query={"n": -5}),
    _t2case("floats default", "GET", "/items/3/floats", "bob"),
    _t2case("floats other", "GET", "/items/3/floats", "alice", status=404),
    # R620: fuel and recursion depth
    _t2case("spin", "GET", "/spin", "alice", status=500),
    _t2case("spin nothing to run", "GET", "/spin", "carol", status=404),
    _t2case("burn", "GET", "/burn", "alice", query={"n": 100, "w": 3}),
    _t2case("burn out", "GET", "/burn", "alice", query={"n": 100000}, status=500),
    _t2case("depth", "GET", "/depth/10", "alice"),
    _t2case("depth too deep", "GET", "/depth/5000", "alice", status=500),
    _t2case("depth none", "GET", "/depth/3", "carol", status=404),
)


# ---- the held-out import gaps: NoRoles, NativeToken(lookup=), NativeContext, OneOf columns
# (tests/api/solo.py)

_SO = solo
_STASKS = "SELECT id, owner_id, title, status, priority FROM tasks ORDER BY id"


def _socase(name: str, method: str, path: str, who: str | None, **kw: Any) -> HttpCase:
    return _case(name, method, path, None if who is None else _SO.TOKENS[who], **kw)


SOLO_CASES: tuple[HttpCase, ...] = (
    *(
        _socase(f"{path} {who}", "GET", path, who)
        for path in ("/tasks", "/tasks/mine", "/me")
        for who in ("alice", "bob")
    ),
    _socase("filter status", "GET", "/tasks", "alice", query={"status": "doing"}),
    _socase("filter bad status", "GET", "/tasks", "alice", query={"status": "x"}, status=422),
    _socase("ghost 401", "GET", "/me", "ghost", status=401),
    _socase("forged 401", "GET", "/me", "forged", status=401),
    _socase("inactive", "GET", "/me", "carol", status=400),
    _socase("read own", "GET", "/tasks/1", "alice"),
    _socase("read other 403", "GET", "/tasks/3", "alice", status=403),
    _socase("read missing", "GET", "/tasks/99", "alice", status=404),
    _socase(
        "create default", "POST", "/tasks", "alice", json={"title": "New"},
        status=201, after_sql=_STASKS,
    ),  # fmt: skip
    _socase(
        "create full", "POST", "/tasks", "bob",
        json={"title": "N2", "status": "doing", "priority": "high"},
        status=201, after_sql=_STASKS,
    ),  # fmt: skip
    _socase(
        "create bad status", "POST", "/tasks", "alice", json={"title": "x", "status": "later"},
        status=422, after_sql=_STASKS,
    ),  # fmt: skip
    _socase(
        "create bad priority", "POST", "/tasks", "alice",
        json={"title": "x", "priority": "urgent"}, status=422,
    ),  # fmt: skip
    _socase(
        "patch", "PATCH", "/tasks/2", "alice", json={"status": "done"}, after_sql=_STASKS
    ),  # fmt: skip
    _socase(
        "patch bad", "PATCH", "/tasks/2", "alice", json={"priority": "mid"},
        status=422, after_sql=_STASKS,
    ),  # fmt: skip
    _socase("patch other", "PATCH", "/tasks/3", "alice", json={"status": "done"}, status=403),
    _socase("finish", "POST", "/tasks/1/finish", "alice", after_sql=_STASKS),
    _socase("finish other", "POST", "/tasks/3/finish", "alice", status=404),
)

# NativeToken(lookup="username", unique="runtime"): the same program over a users table
# with no unique username and two "dave"s -- one row authenticates, none is 401, two a
# loud 500 (R311) that writes nothing
SOLO_RUNTIME_CASES: tuple[HttpCase, ...] = (
    _socase("one row", "GET", "/me", "alice"),
    _socase("one row list", "GET", "/tasks", "bob"),
    _socase("no row 401", "GET", "/me", "ghost", status=401),
    _socase("two rows 500", "GET", "/me", "dave", status=500),
    _socase("two rows write 500", "POST", "/tasks", "dave", json={"title": "x"}, status=500),
    _socase("after 500 nothing written", "GET", "/tasks", "bob"),
)

# NativeToken(invalid=, unknown=): the original's answers to a token naming no caller,
# on every endpoint (spec 2026-09-27 surface §4.1)
SOLO_ANSWERS_CASES: tuple[HttpCase, ...] = tuple(
    _socase(f"{path} {who}", method, path, who, status=status, **kw)
    for method, path, kw in (
        ("GET", "/me", {}),
        ("GET", "/tasks", {}),
        ("GET", "/tasks/1", {}),
        ("POST", "/tasks", {"json": {"title": "x"}, "after_sql": _STASKS}),
    )
    for who, status in (("forged", 401), ("ghost", 403), (None, 401), ("alice", 200))
    if not (who == "alice" and method == "POST")
)


PROGRAMS = _programs()
PROGRAMS["realapps"] = (
    _R.PROGRAM,
    ApiTask("realapps", "control", "real-app features", REALAPPS_CASES + _probes(REALAPPS_CASES)),
)
PROGRAMS["partb"] = (
    _PB.PROGRAM,
    ApiTask("partb", "control", "Part B features", PARTB_CASES + _probes(PARTB_CASES)),
)
PROGRAMS["loops"] = (
    loops.PROGRAM,
    ApiTask("loops", "control", "window and traverse", LOOPS_CASES + _probes(LOOPS_CASES)),
)
PROGRAMS["effects"] = (
    effects.PROGRAM,
    ApiTask(
        "effects", "control", "for_each and the outbox", EFFECTS_CASES + _probes(EFFECTS_CASES)
    ),
)
PROGRAMS["gaps"] = (
    gaps.PROGRAM,
    ApiTask("gaps", "control", "edit-slice gap features", GAPS_CASES + _probes(GAPS_CASES)),
)
PROGRAMS["mine"] = (
    _M.PROGRAM,
    ApiTask("mine", "control", "mine() vs owned()", MINE_CASES + _probes(MINE_CASES)),
)
PROGRAMS["round2"] = (
    _R2.PROGRAM,
    ApiTask("round2", "control", "round-2 fixes", ROUND2_CASES + _probes(ROUND2_CASES)),
)
PROGRAMS["solo"] = (
    _SO.PROGRAM,
    ApiTask("solo", "control", "held-out import gaps", SOLO_CASES + _probes(SOLO_CASES)),
)
PROGRAMS["solo_runtime"] = (
    _SO.PROGRAM,
    ApiTask(
        "solo_runtime",
        "control",
        'NativeToken(unique="runtime")',
        SOLO_RUNTIME_CASES + _probes(SOLO_RUNTIME_CASES),
    ),
)
PROGRAMS["solo_answers"] = (
    _SO.PROGRAM,
    ApiTask(
        "solo_answers",
        "control",
        "NativeToken(invalid=, unknown=)",
        SOLO_ANSWERS_CASES + _probes(SOLO_ANSWERS_CASES),
    ),
)
PROGRAMS["t2"] = (
    _T2.PROGRAM,
    ApiTask("t2", "control", "procedures in endpoints", T2_CASES + _probes(T2_CASES)),
)

# ---- exact role sets and stored role values (tests/api/rolesets.py)
_RS = rolesets
ROLESETS_CASES: tuple[HttpCase, ...] = tuple(
    _case(
        f"{path} {who}",
        "GET",
        path,
        None if who is None else _RS.TOKENS[who],
        status=401 if who is None else 200 if who in _RS.ALLOWED[path] else 403,
    )
    for path in sorted(_RS.ALLOWED)
    for who in (*_RS.USERS, None)
)
PROGRAMS["rolesets"] = (
    _RS.PROGRAM,
    ApiTask("rolesets", "control", "Roles(...), Authenticated, RoleColumn(values=)",
            ROLESETS_CASES + _probes(ROLESETS_CASES)),
)  # fmt: skip

# ---- N1b (tests/api/n1b.py): named column expressions, row values in insert/update/derive,
# when(..., otherwise=None) -- on the gap-features profile and database
_N1B = n1b
N1B_CASES: tuple[HttpCase, ...] = (
    _gcase("dup", "POST", f"/items/{_G.uid(101)}/duplicate", "alice", status=201,
           after_sql=_GITEMS),  # fmt: skip
    _gcase("dup null desc", "POST", f"/items/{_G.uid(102)}/duplicate", "alice", status=201),
    _gcase("dup other", "POST", f"/items/{_G.uid(103)}/duplicate", "alice", status=403),
    _gcase("dup missing", "POST", f"/items/{_G.uid(999)}/duplicate", "alice", status=404),
    _gcase("title from", "PUT", f"/items/{_G.uid(101)}/title-from/{_G.uid(102)}", "alice",
           after_sql=_GITEMS),  # fmt: skip
    _gcase("title from desc", "PUT", f"/items/{_G.uid(102)}/title-from/{_G.uid(104)}", "alice"),
    _gcase("title from other", "PUT", f"/items/{_G.uid(101)}/title-from/{_G.uid(103)}", "alice",
           status=404),  # fmt: skip
    _gcase("around", "GET", f"/items/{_G.uid(101)}/around", "alice"),
    _gcase("around admin", "GET", f"/items/{_G.uid(103)}/around", "admin"),
    _gcase("around missing", "GET", f"/items/{_G.uid(103)}/around", "alice", status=404),
    _gcase("stamp clear", "POST", f"/items/{_G.uid(101)}/stamp", "alice", json={"clear": True},
           after_sql=_GITEMS),  # fmt: skip
    _gcase("stamp now", "POST", f"/items/{_G.uid(102)}/stamp", "alice", json={"clear": False}),
)
PROGRAMS["n1b"] = (
    _N1B.PROGRAM,
    ApiTask("n1b", "control", "N1b spellings", N1B_CASES + _probes(N1B_CASES)),
)

# ---- t2 without natives (tests/api/t2prog.py GO_PROGRAM): the procedures program on
# targets without natives, with every T2 case but the effect native's (notify)
T2GO_CASES = tuple(c for c in T2_CASES if "/notify" not in c.path)
PROGRAMS["t2go"] = (
    _T2.GO_PROGRAM,
    ApiTask("t2go", "control", "procedures, no natives", T2GO_CASES + _probes(T2GO_CASES)),
)

# ---- endpoint edges (tests/api/edges.py): page totals, unique_or 409, owner transfer to
# a missing user, trailing-slash 307s, UNSET partial updates with explicit null, 204, R310
_ED = edges
EDGES_CASES: tuple[HttpCase, ...] = (
    _case("page total", "GET", "/reviews/", ADMIN, query={"limit": 3, "skip": 2}),
    _case("page past the end", "GET", "/reviews/", ADMIN, query={"skip": 100000}),
    _case("page forbidden", "GET", "/reviews/", A, status=403),
    _case("slash redirect", "GET", "/reviews", ADMIN),  # 307, followed by the client
    _case("slash redirect query", "GET", "/reviews", ADMIN, query={"limit": 2}),
    _case("notes page", "GET", "/notes/", A, after_sql=_ED.NOTES_AFTER),
    _case("notes page 2", "GET", "/notes", A, query={"limit": 1, "offset": 1}),
    _case("notes none", "GET", "/notes/", D),
    _case("slash other method", "PATCH", "/notes/1/", A, json={"memo": "x"}),
    _case("conflict", "PATCH", "/users/me", A, json={"email": "bob@example.com"}, status=409,
          after_sql=_ED.USERS_AFTER),  # fmt: skip
    _case("own email again", "PATCH", "/users/me", A, json={"email": "alice@example.com"}),
    _case("me name only", "PATCH", "/users/me", A, json={"name": "Al"},
          after_sql=_ED.USERS_AFTER),  # fmt: skip
    _case("me nothing", "PATCH", "/users/me", B, json={}, after_sql=_ED.USERS_AFTER),
    _case("me null name", "PATCH", "/users/me", A, json={"name": None}, status=422),
    _case("review rating", "PATCH", "/reviews/111", A, json={"rating": 2},
          after_sql=_ED.REVIEWS_AFTER),  # fmt: skip
    _case("review both", "PATCH", "/reviews/111", A, json={"rating": 5, "body": "ok"},
          after_sql=_ED.REVIEWS_AFTER),  # fmt: skip
    _case("review none", "PATCH", "/reviews/111", A, json={}),
    _case("review not mine", "PATCH", "/reviews/8", A, json={"rating": 2}, status=404),
    _case("owner moved", "PUT", "/reviews/111/owner", ADMIN, json={"owner_id": 2},
          after_sql=_ED.REVIEWS_AFTER),  # fmt: skip
    _case("owner missing", "PUT", "/reviews/111/owner", ADMIN, json={"owner_id": 999},
          status=404, after_sql=_ED.REVIEWS_AFTER),  # fmt: skip
    _case("owner no review", "PUT", "/reviews/99999/owner", ADMIN, json={"owner_id": 2},
          status=404),  # fmt: skip
    _case("R310", "GET", "/any/23", ADMIN, status=500),
    _case("any none", "GET", "/any/999999", ADMIN, status=404),
    _case("one", "GET", "/reviews/111", A),
    _case("mine route", "GET", "/reviews/mine", A),
    _case("delete", "DELETE", "/reviews/111", A, status=204, after_sql=_ED.REVIEWS_AFTER),
    _case("delete other", "DELETE", "/reviews/8", A, status=404),
    _case("memo null", "PATCH", "/notes/1", A, json={"memo": None},
          after_sql=_ED.NOTES_AFTER),  # fmt: skip
    _case("memo set", "PATCH", "/notes/2", A, json={"memo": "hi"}, after_sql=_ED.NOTES_AFTER),
    _case("title and null memo", "PATCH", "/notes/1", A, json={"title": "t", "memo": None},
          after_sql=_ED.NOTES_AFTER),  # fmt: skip
    _case("title null", "PATCH", "/notes/1", A, json={"title": None}, status=422),
    _case("note unchanged", "PATCH", "/notes/2", A, json={}, after_sql=_ED.NOTES_AFTER),
    _case("note other", "PATCH", "/notes/3", A, json={"memo": None}, status=404),
    _case("note bob", "PATCH", "/notes/3", B, json={"memo": None}, after_sql=_ED.NOTES_AFTER),
    # Str(pattern=...): re.fullmatch(pattern, value, flags=re.ASCII) on every target
    _case("find defaults", "GET", "/notes/find", A),
    _case("find ci", "GET", "/notes/find", A, query={"ci": "call mom"}),
    _case("find ci upper", "GET", "/notes/find", A, query={"ci": "CALL MOM"}),
    _case("find ci long s", "GET", "/notes/find", A, query={"ci": "\u017f"}, status=422),
    _case("find ci kelvin", "GET", "/notes/find", A, query={"ci": "\u212a"}, status=422),
    _case("find ci digit", "GET", "/notes/find", A, query={"ci": "abc1"}, status=422),
    _case("find end", "GET", "/notes/find", A, query={"end": "call mom"}),
    _case("find end newline", "GET", "/notes/find", A, query={"end": "call mom\n"}, status=422),
    _case("find rep", "GET", "/notes/find", A, query={"rep": "abcd efg"}),
    _case("find rep over", "GET", "/notes/find", A, query={"rep": "abcde f"}, status=422),
    _case("find upto", "GET", "/notes/find", A, query={"upto": "xx"}),
    _case("find upto over", "GET", "/notes/find", A, query={"upto": "xxx"}, status=422),
    _case("find upto literal", "GET", "/notes/find", A, query={"upto": "x{,2}"}, status=422),
)
PROGRAMS["edges"] = (
    _ED.PROGRAM,
    ApiTask("edges", "control", "endpoint edges", EDGES_CASES + _probes(EDGES_CASES)),
)

TASK_IDS = [t.id for t in API_TASKS]
EXAMPLE_IDS = [f"example{i}" for i in range(1, len(LATTICE_EXAMPLES) + 1)]
EXTRA_IDS = [
    "numeric",
    "mapped_join",
    "realapps",
    "partb",
    "loops",
    "effects",
    "gaps",
    "mine",
    "round2",
    "t2",
    "solo",
    "solo_runtime",
    "solo_answers",
    "n1b",
    "rolesets",
    "t2go",
    "edges",
]


@dataclasses.dataclass(frozen=True)
class Setup:
    """How a program runs: its profile, database, natives (and the natives module
    copied next to the generated app)."""

    profile: Any
    build: Any
    natives: Mapping[str, Any] | None = None
    natives_file: Path | None = None
    settings: Mapping[str, Any] | None = None  # NativeContext values (the runtime's)


DEFAULT_SETUP = Setup(api_profile(), build_db)
SETUPS = {
    "realapps": Setup(_R.PROFILE, _R.build_db, _R.NATIVES, _R.NATIVES_FILE),
    "partb": Setup(_PB.PROFILE, _PB.build_db, _PB.NATIVES, _PB.NATIVES_FILE),
    "loops": Setup(loops.PROFILE, loops.build_db),
    "effects": Setup(effects.PROFILE, effects.build_db, effects.NATIVES, effects.NATIVES_FILE),
    "gaps": Setup(gaps.PROFILE, gaps.build_db, gaps.NATIVES, gaps.NATIVES_FILE),
    "mine": Setup(_M.PROFILE, _M.build_db, _M.NATIVES, _M.NATIVES_FILE),
    "round2": Setup(_R2.PROFILE, _R2.build_db, _R2.NATIVES, _R2.NATIVES_FILE),
    "t2": Setup(_T2.PROFILE, _T2.build_db, _T2.NATIVES, _T2.NATIVES_FILE),
    "solo": Setup(_SO.PROFILE, _SO.build_db, _SO.NATIVES, _SO.NATIVES_FILE, _SO.SETTINGS),
    "solo_runtime": Setup(
        _SO.PROFILE_RUNTIME, _SO.build_db_runtime, _SO.NATIVES, _SO.NATIVES_FILE, _SO.SETTINGS
    ),
    "solo_answers": Setup(
        _SO.PROFILE_ANSWERS, _SO.build_db, _SO.NATIVES, _SO.NATIVES_FILE, _SO.SETTINGS
    ),
    "n1b": Setup(n1b.PROFILE, n1b.build_db, n1b.NATIVES, n1b.NATIVES_FILE),
    "rolesets": Setup(rolesets.PROFILE, rolesets.build_db),
    "t2go": Setup(_T2.GO_PROFILE, _T2.build_db),
    "edges": Setup(_ED.PROFILE, _ED.build_db),
}


def setup_of(pid: str) -> Setup:
    return SETUPS.get(pid, DEFAULT_SETUP)


# ---- review hardening: the caller's stored spellings decoded, insert_many owner=user,
# size-aware fuel (R620) and the value size cap (R622) (tests/api/hardening.py)

_HD = hardening
_HNOTES = "SELECT id, owner_id, title FROM notes ORDER BY id"


def _hdcase(name: str, method: str, path: str, who: str | None, **kw: Any) -> HttpCase:
    return _case(name, method, path, None if who is None else _HD.TOKENS[who], **kw)


HARDENING_CASES: tuple[HttpCase, ...] = (
    _hdcase("offers pro", "GET", "/offers", "alice"),
    _hdcase("offers free", "GET", "/offers", "bob"),
    _hdcase("plan pro", "GET", "/me/plan", "alice"),
    _hdcase("plan free", "GET", "/me/plan", "bob"),
    _hdcase("admin by stored role", "GET", "/admin/ping", "admin"),
    _hdcase("customer not admin", "GET", "/admin/ping", "alice", status=403),
    _hdcase(
        "insert_many owner", "POST", "/notes", "bob", json={"title": "t", "n": 2},
        after_sql=_HNOTES,
    ),  # fmt: skip
    _hdcase("pad small", "GET", "/pad", "alice", query={"s": "ab", "n": 5}),
    _hdcase("pad over cap", "GET", "/pad", "alice", query={"s": "ab", "n": 200_000}, status=500),
    _hdcase("pad huge", "GET", "/pad", "alice", query={"s": "ab", "n": 10**11}, status=500),
    _hdcase("grow ok", "GET", "/grow", "alice", query={"n": 90_000, "k": 2}),
    _hdcase("grow fuel", "GET", "/grow", "alice", query={"n": 90_000, "k": 20}, status=500),
    _hdcase("shift huge", "GET", "/shift", "alice", query={"n": 10**9}, status=500),
)
PROGRAMS["hardening"] = (
    _HD.PROGRAM,
    ApiTask(
        "hardening", "control", "review hardening", HARDENING_CASES + _probes(HARDENING_CASES)
    ),
)
EXTRA_IDS.append("hardening")
SETUPS["hardening"] = Setup(_HD.PROFILE, _HD.build_db)


def normalize(value: object) -> object:
    """`value` (JSON or rows) with values generated per request replaced by
    placeholders: a UUID not seeded (tests/api/realapps.py seeds only ids with the
    prefix 00000000-0000-4000-8000-) is "<uuid>", a DateTime (stored, or answered in
    either wire spelling) not in the seeded year 2000 is "<now>"."""
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [normalize(v) for v in value]
    if isinstance(value, str):
        value = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            lambda m: m.group(0) if m.group(0).startswith("00000000-0000-4000-8000-") else "<uuid>",
            value,
        )
        value = re.sub(  # "...Z", or naive (ApiProfile.datetime_wire="naive")
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{6})?Z?",
            lambda m: m.group(0) if m.group(0).startswith("2000-") else "<now>",
            value,
        )
    return value


# ---- capture ---------------------------------------------------------------------------------


def db_digest(path: str) -> str:
    """A digest of every table's rows (stdlib only: also copied into the sandbox driver).
    Values generated per request (a UUID that was not seeded, a stored DateTime outside
    the seeded year 2000) enter it as placeholders (see `normalize`)."""
    import hashlib as _hashlib
    import re as _re
    import sqlite3 as _sqlite3

    uuids = _re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    instants = _re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z")

    def cell(v: object) -> object:
        if not isinstance(v, str):
            return v
        v = uuids.sub(
            lambda m: m.group(0) if m.group(0).startswith("00000000-0000-4000-8000-") else "<u>", v
        )
        return instants.sub(lambda m: m.group(0) if m.group(0).startswith("2000-") else "<t>", v)

    conn = _sqlite3.connect(path)
    try:
        h = _hashlib.sha256()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        for (table,) in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            rows = conn.execute(f"SELECT * FROM {quoted} ORDER BY rowid").fetchall()
            h.update(repr((table, [tuple(cell(v) for v in r) for r in rows])).encode())
        return h.hexdigest()
    finally:
        conn.close()


def wire_record(
    first_status: int, first_headers: Any, status: int, headers: Any, content: bytes
) -> dict:
    """What a case's response looked like on the wire beyond its parsed JSON body
    (stdlib only: also copied into the sandbox driver): the first hop's status and
    Location (so a 307 that the client followed never equals a direct answer), the final
    response's content type, and its raw bytes when they are not JSON (else None: the
    parsed body is compared). Headers are (name, value) text pairs."""
    import json as _json

    def header(pairs: Any, name: str) -> str | None:
        for k, v in pairs:
            if k.lower() == name:
                return v
        return None

    raw = None
    if content:
        try:
            _json.loads(content)
        except ValueError:
            raw = content.decode("latin-1")
    return {
        "first": [first_status, header(first_headers, "location")],
        "content_type": header(headers, "content-type"),
        "raw": raw,
    }


def _wire_of_response(response: Any) -> dict | None:
    """`wire_record` of an httpx response (the sandbox driver's; None without one)."""
    if response is None:
        return None
    first = response.history[0] if response.history else response
    return wire_record(
        first.status_code,
        list(first.headers.items()),
        response.status_code,
        list(response.headers.items()),
        response.content,
    )


_ANCHOR = "    passed, reason = evaluate_case(case, status, body, queries, after_rows, crash)\n"
_CAPTURE = (
    "    _wire = _wire_of_response(locals().get('response'))\n"
    "    with open(" + repr(CAPTURE_FILE) + ', "a") as _cf:\n'
    "        _cf.write(json.dumps({\n"
    '            "name": case["name"], "status": status, "body": body, "crash": crash,\n'
    '            "queries": queries, "after": after_rows,\n'
    '            "digest": _digest_or_none("app.db"),\n'
    '            **(_wire or {"first": None, "content_type": None, "raw": None}),\n'
    '        }) + "\\n")\n'
)


def _digest_or_none(path: str) -> str | None:
    """The database digest after a request, also after a 500 (a runtime diagnostic such
    as R620 must leave the database as it was); None if it cannot be read."""
    try:
        return db_digest(path)
    except Exception:
        return None


def _capturing_driver_source(task: ApiTask) -> str:
    source = _original_driver_source(task)
    assert _ANCHOR in source, "the sandbox driver changed: update the capture anchor"
    helper = "\n\n".join(
        inspect.getsource(f) for f in (db_digest, _digest_or_none, wire_record, _wire_of_response)
    )
    source = source.replace(
        "\ndef _run_case(case, app):", f"\n{helper}\n\ndef _run_case(case, app):"
    )
    return source.replace(_ANCHOR, _CAPTURE + _ANCHOR)


_original_driver_source = api_runner.driver_source


def _copier(pristine: Path):
    def build(target: Path) -> None:
        shutil.copyfile(pristine, target)

    return build


def _run_generated(
    app_source: str, workdir: Path, task: ApiTask, pristine: Path, setup: Setup = DEFAULT_SETUP
) -> tuple[CaseResults, list[dict]]:
    capture = workdir / CAPTURE_FILE
    capture.unlink(missing_ok=True)
    if setup.natives_file is not None:  # imported by the generated app as `natives`
        workdir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(setup.natives_file, workdir / "natives.py")
    results = run_cases_fastapi(
        app_source, workdir, task, db_builder=_copier(pristine), kit_path=KIT_PATH, timeout_s=300
    )
    captured = (
        [json.loads(line) for line in capture.read_text().splitlines()] if capture.exists() else []
    )
    return results, captured


class _Shim:
    """ASGI wrapper recording one case's response body, its wire record (`wire_record`:
    the first hop, content type, non-JSON bytes) and the DB state after it."""

    def __init__(self, app: Any, db_path: Path, case: HttpCase, sink: list[dict]) -> None:
        self.app, self.db_path, self.case, self.sink = app, db_path, case, sink
        self.recorded: int | None = None
        self.first: tuple[int, list[tuple[str, str]]] | None = None  # the first hop

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status, chunks = 0, []
        headers: list[tuple[str, str]] = []

        async def record(message: Mapping[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                for k, v in message.get("headers", []):
                    headers.append((k.decode("latin-1"), v.decode("latin-1")))
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
            await send(message)

        await self.app(scope, receive, record)
        content = b"".join(chunks)
        try:
            body = json.loads(content) if content else None
        except ValueError:
            body = None
        after = None
        if self.case.after_sql:
            conn = sqlite3.connect(self.db_path)
            try:
                after = [list(r) for r in conn.execute(self.case.after_sql).fetchall()]
            finally:
                conn.close()
        record = {
            "name": self.case.name,
            "status": status,
            "body": body,
            "crash": "",
            "after": after,
            "digest": db_digest(str(self.db_path)),
        }
        if self.first is None:
            self.first = (status, headers)
        record |= wire_record(*self.first, status, headers, content)
        # a followed redirect is two requests: keep the final response's record (and
        # the first hop's status and Location)
        if self.recorded is None:
            self.recorded = len(self.sink)
            self.sink.append(record)
        else:
            self.sink[self.recorded] = record


def _run_runtime(
    source: str, workdir: Path, task: ApiTask, pristine: Path, setup: Setup = DEFAULT_SETUP
):
    captured: list[dict] = []
    calls = [0]

    def serve(src: str, profile: Any, db_path: Path):
        check, app = runtime.serve_source(
            src, profile, db_path, natives=setup.natives, settings=setup.settings
        )
        case = task.cases[calls[0]]
        calls[0] += 1
        return check, (None if app is None else _Shim(app, db_path, case, captured))

    results = run_cases_lattice(
        source,
        workdir,
        task,
        db_builder=_copier(pristine),
        profile=setup.profile,
        serve=serve,
        hooks=runtime,
    )
    return results, captured


@pytest.fixture(scope="session")
def differential_runs(tmp_path_factory: pytest.TempPathFactory) -> Mapping[str, tuple]:
    """program id -> (runtime results, runtime captures, generated results, captures)."""
    root = tmp_path_factory.mktemp("codegen_diff")
    pristines: dict[int, Path] = {}

    def pristine(pid: str) -> Path:
        setup = setup_of(pid)
        key = id(setup)
        if key not in pristines:
            pristines[key] = root / f"pristine_{len(pristines)}.db"
            setup.build(pristines[key])
        return pristines[key]

    apps: dict[str, str] = {}
    for pid, (source, _task) in PROGRAMS.items():
        check, app_source = compile_source_to_fastapi(source, setup_of(pid).profile)
        assert app_source is not None, [d.render() for d in check.diagnostics]
        apps[pid] = app_source
        (root / f"{pid}_app.py").write_text(app_source)
        pristine(pid)

    runtime_runs = {
        pid: _run_runtime(source, root / f"{pid}_runtime", task, pristine(pid), setup_of(pid))
        for pid, (source, task) in PROGRAMS.items()
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(api_runner, "driver_source", _capturing_driver_source)

        def gen(pid: str):
            return _run_generated(
                apps[pid],
                root / f"{pid}_generated",
                PROGRAMS[pid][1],
                pristine(pid),
                setup_of(pid),
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            generated = dict(zip(PROGRAMS, pool.map(gen, PROGRAMS), strict=True))
    return {pid: (*runtime_runs[pid], *generated[pid]) for pid in PROGRAMS}


# ---- comparison ----------------------------------------------------------------------------


def strict_equal(a: object, b: object) -> bool:
    """JSON equality that also tells 1 from 1.0 and True from 1."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(strict_equal(a[k], b[k]) for k in a)  # type: ignore[union-attr]
    if isinstance(a, list):
        return len(a) == len(b) and all(strict_equal(x, y) for x, y in zip(a, b, strict=True))  # type: ignore[arg-type]
    return a == b


def _mismatches(pid: str, runs: tuple, task: ApiTask | None = None) -> list[str]:
    """Every difference between the runtime's run and the other side's (the generated
    FastAPI app here; the Go binary in tests/gogen/test_go_differential.py) of `task`
    (default: the registry's)."""
    rt_results, rt_captured, gen_results, gen_captured = runs
    task = PROGRAMS[pid][1] if task is None else task
    problems: list[str] = []
    assert rt_results.started, rt_results.start_error
    assert gen_results.started, gen_results.start_error
    n = len(task.cases)
    assert len(rt_results.cases) == len(gen_results.cases) == n
    assert len(rt_captured) == len(gen_captured) == n
    for case, rt, gen, rc, gc in zip(
        task.cases, rt_results.cases, gen_results.cases, rt_captured, gen_captured, strict=True
    ):
        assert rc["name"] == gc["name"] == case.name
        where = f"{pid} / {case.name} ({case.method} {case.path})"
        # a runtime diagnostic answered as a 500 (R620: a procedure ran out of fuel) is
        # no crash of the generated app when the runtime answers the very same body
        same_500 = rt.status == gen.status >= 500 and strict_equal(rc["body"], gc["body"])
        if gc["crash"] and not same_500:
            problems.append(f"{where}: generated app crashed: {gc['crash']}")
            continue
        pairs = {
            "status": (rt.status, gen.status),
            "queries": (rt.queries, gen.queries),
            "passed": (rt.passed, gen.passed),
            "reason": (rt.reason, gen.reason),
            "after_sql rows": (normalize(rc["after"]), normalize(gc["after"])),
            "db digest": (rc["digest"], gc["digest"]),
            "first hop (status, Location)": (normalize(rc["first"]), normalize(gc["first"])),
            "content type": (rc["content_type"], gc["content_type"]),
            "raw (non-JSON) body": (normalize(rc["raw"]), normalize(gc["raw"])),
        }
        for what, (x, y) in pairs.items():
            if x != y:
                problems.append(f"{where}: {what} differs: runtime {x!r} vs generated {y!r}")
        if not strict_equal(normalize(rc["body"]), normalize(gc["body"])):
            problems.append(
                f"{where}: body differs: runtime {rc['body']!r} vs generated {gc['body']!r}"
            )
    if classify(rt_results) != classify(gen_results):
        problems.append(f"{pid}: classification differs")
    return problems


@pytest.mark.parametrize("pid", TASK_IDS + EXAMPLE_IDS + EXTRA_IDS)
def test_generated_app_matches_the_runtime(pid: str, differential_runs):
    problems = _mismatches(pid, differential_runs[pid])
    assert problems == [], "\n".join(problems[:20])


@pytest.mark.parametrize("pid", TASK_IDS)
def test_generated_app_passes_every_case_the_runtime_passes(pid: str, differential_runs):
    """The reference programs pass their task's own cases on both backends."""
    rt_results, _, gen_results, _ = differential_runs[pid]
    task = next(t for t in API_TASKS if t.id == pid)
    names = {c.name for c in task.cases}
    for results in (rt_results, gen_results):
        own = [c for c in results.cases if c.name in names]
        assert len(own) == len(task.cases)
        assert all(c.passed for c in own), [(c.name, c.reason) for c in own if not c.passed]


def test_the_differential_covers_every_task_and_example(differential_runs):
    assert set(differential_runs) == {t.id for t in API_TASKS} | set(EXAMPLE_IDS) | set(EXTRA_IDS)
    assert len(API_TASKS) == 40 and len(EXAMPLE_IDS) == 3
    probes = sum(len(PROGRAMS[p][1].cases) for p in PROGRAMS)
    assert probes > sum(len(t.cases) for t in API_TASKS)


def test_the_mapped_join_program_passes_f01(differential_runs):
    """f01 with on={"id": "order_id"}: every f01 case passes on both backends."""
    rt_results, _, gen_results, _ = differential_runs["mapped_join"]
    f01 = next(t for t in API_TASKS if t.id == "f01")
    names = {c.name for c in f01.cases}
    for results in (rt_results, gen_results):
        own = [c for c in results.cases if c.name in names]
        assert len(own) == len(f01.cases)
        assert all(c.passed for c in own), [(c.name, c.reason) for c in own if not c.passed]


def test_db_digest_sees_writes(tmp_path: Path):
    path = tmp_path / "d.db"
    build_db(path)
    before = db_digest(str(path))
    conn = sqlite3.connect(path)
    conn.execute("UPDATE products SET stock = stock + 1 WHERE id = 1")
    conn.commit()
    conn.close()
    assert db_digest(str(path)) != before


def test_wire_record_keeps_the_first_hop_and_non_json_bytes():
    json_ct = [("content-type", "application/json")]
    assert wire_record(200, json_ct, 200, json_ct, b'{"a": 1}') == {
        "first": [200, None],
        "content_type": "application/json",
        "raw": None,  # JSON: compared as the parsed body
    }
    redirected = wire_record(307, [("Location", "http://testserver/x/")], 200, json_ct, b"[]")
    assert redirected["first"] == [307, "http://testserver/x/"]
    text = wire_record(500, [("Content-Type", "text/plain")], 500, [("Content-Type", "text/plain")],
                       b"Internal Server Error")  # fmt: skip
    assert text["raw"] == "Internal Server Error" and text["content_type"] == "text/plain"
    assert wire_record(204, [], 204, [], b"") == {"first": [204, None], "content_type": None,
                                                  "raw": None}  # fmt: skip


def _record(**changes: Any) -> dict:
    base = {"name": "c", "status": 200, "body": {"a": 1}, "crash": "", "after": None,
            "digest": "d", "first": [200, None], "content_type": "application/json",
            "raw": None}  # fmt: skip
    return base | changes


@pytest.mark.parametrize(
    "changes",
    [
        {"first": [307, "http://testserver/c/"]},  # a redirect followed vs a direct 200
        {"raw": "oops", "body": None},  # non-JSON bytes vs JSON
        {"content_type": "text/plain"},
    ],
)
def test_mismatches_see_the_first_hop_and_the_raw_body(changes):
    case = _case("c", "GET", "/c", None)
    task = ApiTask("t", "control", "t", (case,))
    result = CaseResults(
        cases=(api_runner.CaseResult("c", "happy", "control", True, 200, 1, 0.0, ""),),
        started=True,
    )
    same = _mismatches("t", (result, [_record()], result, [_record()]), task)
    assert same == []
    problems = _mismatches("t", (result, [_record()], result, [_record(**changes)]), task)
    assert problems, changes


def test_the_probes_reach_the_new_input_rules_and_non_finite_results(differential_runs):
    """The differential exercised each new runtime rule (on both backends, equally)."""
    msgs, bodies = set(), []
    for pid in differential_runs:
        _, rt_captured, _, gen_captured = differential_runs[pid]
        for captured in (rt_captured, gen_captured):
            for c in captured:
                bodies.append(c["body"])
                if isinstance(c["body"], dict) and c["body"].get("error") == "validation":
                    msgs |= {d["msg"] for d in c["body"]["detail"]}
    assert "must be an integer between -9223372036854775808 and 9223372036854775807" in msgs
    assert {"must be an integer", "must be a number"} <= msgs
    _, rt_captured, _, gen_captured = differential_runs["numeric"]
    for captured in (rt_captured, gen_captured):
        first = captured[0]["body"]
        assert first["huge"] is None and isinstance(first["tiny"], float)
        assert all(item["huge"] is None for item in captured[2]["body"]["items"])


def test_the_realapps_program_passes_its_cases(differential_runs):
    """Every real-apps case gets its expected status on both backends, and the cases
    reach each new feature (equally on both, by the differential above)."""
    rt_results, rt_captured, gen_results, _ = differential_runs["realapps"]
    want = {c.name: c.expect_status for c in REALAPPS_CASES}
    for results in (rt_results, gen_results):
        got = {c.name: c.status for c in results.cases if c.name in want}
        assert got == want  # (after_sql rows are compared between the backends above)
    bodies = {c["name"]: c["body"] for c in rt_captured}
    assert bodies["items past the end"] == {"data": [], "count": 3}
    assert bodies["item other 403"]["error"] == "forbidden"
    assert normalize(bodies["create"])["id"] == "<uuid>"
    clock = normalize(bodies["clock"])
    assert clock["a"] == clock["b"] == "<now>"
    assert bodies["update description null"]["description"] is None
    assert bodies["login"]["access_token"] == realapps.TOKENS["alice"]
    assert bodies["loud"]["loud"] == "ALPHA!"
    # a UNIQUE violation is a 409 that names no table or column (rolled back)
    assert bodies["update me email taken"] == {
        "error": "conflict",
        "detail": runtime.UNIQUE_CONFLICT,
    }


def test_the_gaps_program_passes_its_cases(differential_runs):
    """Every gap-features case gets its expected status on both backends, and the
    bodies show each feature (equal on both, by the differential above)."""
    rt_results, rt_captured, gen_results, _ = differential_runs["gaps"]
    want = {c.name: c.expect_status for c in GAPS_CASES}
    for results in (rt_results, gen_results):
        got = {c.name: c.status for c in results.cases if c.name in want}
        assert got == want
    bodies = {c["name"]: c["body"] for c in rt_captured}

    def titles(name: str) -> list[str]:
        return [r["title"] for r in bodies[name]["data"]]

    assert titles("search upper") == ["weekly GROCERIES"]
    assert titles("search unicode") == []  # 'ä' is not 'a'
    assert titles("since") == ["Gamma"]
    assert titles("oldest") == ["Epsilon", "Gamma"]  # nulls="smallest"
    assert bodies["title locked"]["detail"] == gaps.LOCKED
    assert bodies["title fresh"]["title"] == "Soon"
    assert bodies["create null description"]["description"] is None
    assert bodies["restore"]["title"] == "Delta"
    assert bodies["times"]["hours"] == 106


def test_the_mine_program_keeps_an_admins_own_rows_only(differential_runs):
    """mine() excludes other users' rows for an Admin caller (owned() keeps them),
    directly and through a parent, on both backends (equal by the differential)."""
    rt_results, rt_captured, gen_results, _ = differential_runs["mine"]
    want = {c.name: c.expect_status for c in MINE_CASES}
    for results in (rt_results, gen_results):
        assert {c.name: c.status for c in results.cases if c.name in want} == want
    bodies = {c["name"]: c["body"] for c in rt_captured}

    def owners(name: str) -> set[str]:
        return {r["owner_id"] for r in bodies[name]["data"]}

    assert owners("/items/mine admin") == {realapps.ADMIN}
    assert owners("/items/visible admin") == {realapps.ADMIN, realapps.ALICE, realapps.BOB}
    assert owners("/items/mine alice") == {realapps.ALICE}
    assert bodies["/items/mine/stats admin"]["count"] == 1
    assert [r["body"] for r in bodies["/notes/mine admin"]["data"]] == ["note on zeta"]
    assert len(bodies["/notes/visible admin"]["data"]) == 3
    assert bodies["admin retitles alice's"]["detail"] == "Item not found"


def test_the_generated_realapps_app_imports_its_natives():
    check, code = compile_source_to_fastapi(realapps.PROGRAM, realapps.PROFILE)
    assert code is not None, [d.render() for d in check.diagnostics]
    assert "from natives import " in code and "native_decode_token" in code
    check, code = compile_source_to_fastapi(realapps.PROGRAM, realapps.PROFILE, "my.natives")
    assert code is not None and "from my.natives import " in code


def test_the_partb_program_passes_its_cases(differential_runs):
    """Every Part B case gets its expected status on both backends, and the bodies show
    each Part B feature (equal on both, by the differential above)."""
    rt_results, rt_captured, gen_results, _ = differential_runs["partb"]
    want = {c.name: c.expect_status for c in PARTB_CASES}
    for results in (rt_results, gen_results):
        got = {c.name: c.status for c in results.cases if c.name in want}
        assert got == want
    bodies = {c["name"]: c["body"] for c in rt_captured}
    assert bodies["inactive admin list"] == {"detail": "Inactive user"}
    assert bodies["inactive bad limit"] == {"detail": "Inactive user"}
    assert bodies["no token"] == {"detail": partb.UNAUTHORIZED}
    assert bodies["customer admin list"] == {"detail": partb.FORBIDDEN}
    assert bodies["declared 404"] == {"detail": "Item not found"}
    assert [d["loc"] for d in bodies["validation"]["detail"]] == [
        ["query", "skip"],
        ["query", "limit"],
    ]
    assert bodies["item"]["created_at"] == "2000-01-05T10:00:00"
    assert bodies["bad email"]["detail"][0]["msg"] == "must be an email address"
    assert bodies["email taken"] == {"detail": partb.EMAIL_TAKEN}
    assert bodies["signup taken"] == {"detail": partb.SIGNUP_TAKEN}
    assert list(bodies["brief"]) == ["id", "title", "created_at"]


def test_the_loops_program_passes_its_cases(differential_runs):
    """Every window/traverse case gets its expected status on both backends; the bodies
    (equal on both, by the differential above) show the window values and each tree
    node once, at its minimum depth, in (depth, id) order."""
    rt_results, rt_captured, gen_results, _ = differential_runs["loops"]
    want = {c.name: c.expect_status for c in LOOPS_CASES}
    for results in (rt_results, gen_results):
        got = {c.name: c.status for c in results.cases if c.name in want}
        assert got == want
    bodies = {c["name"]: c["body"] for c in rt_captured}

    def walk(name: str) -> list[tuple[int, int]]:
        return [(row["id"], row["depth"]) for row in bodies[name]["items"]]

    assert walk("tree root") == [(1, 0), (2, 1), (3, 1), (4, 2), (7, 2), (5, 3), (6, 3)]
    assert walk("tree depth 1") == [(1, 0), (2, 1), (3, 1)]
    assert walk("tree cycle") == [(21, 0), (22, 1), (20, 2)]
    assert walk("tree missing") == []
    assert walk("path leaf") == [(5, 0), (4, 1), (2, 2), (1, 3)]
    assert walk("path cycle") == [(20, 0), (22, 1), (21, 2)]
    assert walk("folders alice") == [(1, 0), (2, 1), (3, 2)]
    assert walk("folders bob in alice's") == []
    assert walk("folders admin") == [(1, 0), (2, 1), (3, 2), (6, 2)]
    alice = {row["id"]: row for row in bodies["running alice"]["items"]}
    # category 5, newest first: 8 (01-06), 1 (01-03), 4 (01-03, null amount), 2 (01-01)
    assert [alice[i]["n"] for i in (8, 1, 4, 2)] == [1, 2, 3, 4]
    assert [alice[i]["prev_amount"] for i in (8, 1, 4, 2)] == [None, 6, 10, None]
    assert [alice[i]["running"] for i in (8, 1, 4, 2)] == [6, 16, 16, 20]
    assert [alice[i]["avg2"] for i in (8, 1, 4, 2)] == [6.0, 8.0, 10.0, 4.0]
    assert [alice[i]["day_rank"] for i in (2, 3, 1, 4, 6, 5, 8)] == [1, 2, 3, 3, 5, 6, 7]


def test_the_effects_program_passes_its_cases(differential_runs):
    """Every for_each case gets its expected status on both backends, and both leave the
    same outbox rows (compared above through after_sql and the DB digest): dispatched in
    order and marked done, failed with the error, or -- rolled back -- absent."""
    rt_results, rt_captured, gen_results, gen_captured = differential_runs["effects"]
    want = {c.name: c.expect_status for c in EFFECTS_CASES}
    for results in (rt_results, gen_results):
        got = {c.name: c.status for c in results.cases if c.name in want}
        assert got == want
    for captured in (rt_captured, gen_captured):
        after = {c["name"]: c["after"] for c in captured}
        assert after["signup"] == [
            [
                "signup",
                "send_welcome",
                '{"types":["Str","Str"],"values":["new@example.com","New"]}',
                "done",
                None,
                1,
                1,
            ]
        ]
        assert after["signup taken (rolled back)"] == [[0]]
        assert after["create refused after the outbox insert"] == [[0]]
        assert [r[3:] for r in after["create, dispatch fails"]] == [
            ["failed", "ValueError: boom refused", 1, 1]
        ]
        assert [r[2] for r in after["recent alice"]] == [
            '{"types":["Int","Str","Str"],"values":[4,"BOOM","seen"]}',
            '{"types":["Int","Str","Str"],"values":[2,"BETA","seen"]}',
            '{"types":["Int","Str","Str"],"values":[1,"ALPHA","seen"]}',
        ]
        assert after["recent carol (none)"] == []
        assert [r[2] for r in after["finish null due"]] == [
            '{"types":["Int","Date"],"values":[2,null]}'
        ]


def test_the_t2_program_passes_its_cases(differential_runs):
    """Procedures called from endpoints give the same results on both backends (compared
    above) and the expected ones: T2-post reads, a procedure filter after page(), checks,
    whole-value calls, insert_many, an effectful procedure through the outbox, the l12
    state machine, and R620 as a 500 with the same located message."""
    rt_results, rt_captured, gen_results, gen_captured = differential_runs["t2"]
    want = {c.name: c.expect_status for c in T2_CASES}
    for results in (rt_results, gen_results):
        got = {c.name: c.status for c in results.cases if c.name in want}
        assert got == want
    for captured in (rt_captured, gen_captured):
        body = {c["name"]: c["body"] for c in captured}
        after = {c["name"]: c["after"] for c in captured}
        items = body["items alice"]
        assert items["total"] == 4 and [i["id"] for i in items["items"]] == [1, 2, 4]
        assert items["items"][0] == {
            "id": 1,
            "text": "Alpha Task (due Jan 05)",
            "stale": True,
            "weekday": 2,
            "twice": 3.0,
        }
        assert items["items"][1]["weekday"] is None
        assert not any(i["stale"] for i in body["items admin"]["items"])
        assert body["items carol"]["items"] == [] and body["items carol"]["total"] == 0
        assert [i["id"] for i in body["long alice"]["items"]] == [5, 4, 1]  # "be" dropped
        assert body["long admin"]["limit"] == 3
        assert [i["id"] for i in body["long admin"]["items"]] == [6]
        assert body["item own"] == {"id": 1, "text": "Alpha Task (due Jan 05)"}
        assert body["item short 403"]["detail"] == "title too short"
        assert [line["slug"] for line in body["digest alice"]] == [
            "delta",
            "be",
            "alpha-task",
            "boom-box",
        ]
        assert body["digest carol"] == []
        assert body["rescore"] == {"id": 1, "score": 3.0}
        assert [1, 3.0] in after["rescore"]
        assert body["expand"] == [
            {"id": 11, "user_id": 1, "minute": 100, "kind": "fail"},
            {"id": 12, "user_id": 1, "minute": 107, "kind": "fail"},
            {"id": 13, "user_id": 1, "minute": 114, "kind": "fail"},
        ]
        assert after["expand"][-3:] == [
            [11, 1, 100, "fail"],
            [12, 1, 107, "fail"],
            [13, 1, 114, "fail"],
        ]
        assert body["expand none"] == []
        assert after["notify"] == [
            [
                "notify_item",
                "procedure:notify",
                '{"types":["Str","Str"],"values":["alice@example.com","alpha task"]}',
                "done",
                None,
                1,
                1,
            ]
        ]
        assert [r[3:5] for r in after["notify fails"]] == [["failed", "ValueError: boom refused"]]
        statuses = [(r["id"], r["status"], r["sessions"]) for r in body["replay"]]
        assert statuses[:5] == [
            (1, "failed", 0),
            (2, "failed", 0),
            (3, "locked", 0),
            (4, "refused", 0),
            (5, "ok", 1),
        ]
        assert body["spin"]["error"] == "internal"
        spin = body["spin"]["detail"]
        assert "error R620" in spin and "budget of 20000" in spin
        assert "error R620" in body["depth too deep"]["detail"]
        assert "deeper than" in body["depth too deep"]["detail"]
        assert body["depth"] == {"id": 1, "depth": 10}
        # an Int handed to a Float procedure parameter arrives as a float (R-widen)
        assert body["floats"] == {
            "id": 1,
            "by_param": "7.0 0.875",
            "by_col": "2.0 0.25",
            "by_const": "2.0 0.25",
            "by_neg": "-3.0 -0.375",
            "by_caller": "2.0 0.25",
        }
        assert body["floats negative"]["by_param"] == "-5.0 -0.625"
        assert body["burn"] == {"id": 2, "total": 400}
        assert "error R620" in body["burn out"]["detail"]


def test_the_edges_program_passes_its_cases(differential_runs):
    """The endpoint edges (tests/api/edges.py) answer as expected on both backends: page
    totals, the 307 of a trailing slash (followed), unique_or's 409, an owner transfer
    to a missing user, UNSET partial updates with explicit nulls, a 204 delete, R310."""
    rt_results, rt_captured, gen_results, gen_captured = differential_runs["edges"]
    want = {c.name: c.expect_status for c in EDGES_CASES}
    for results in (rt_results, gen_results):
        got = {c.name: c.status for c in results.cases if c.name in want}
        assert got == want
    for captured in (rt_captured, gen_captured):
        body = {c["name"]: c["body"] for c in captured}
        after = {c["name"]: c["after"] for c in captured}
        page = body["page total"]
        assert (page["limit"], page["offset"], page["total"]) == (3, 2, 120)
        assert [i["id"] for i in page["items"]] == [3, 4, 5]
        past = body["page past the end"]
        assert past["items"] == [] and past["total"] == 120
        assert len(body["slash redirect query"]["items"]) == 2
        assert body["notes page 2"] == {
            "items": [{"id": 2, "customer_id": 1, "title": "call mom", "memo": None}],
            "limit": 1,
            "offset": 1,
            "total": 2,
        }
        assert body["slash other method"]["memo"] == "x"
        assert body["conflict"] == {"error": "conflict", "detail": "email taken"}
        assert [1, "alice@example.com", "Alice Nguyen"] in after["conflict"]
        assert after["me name only"][0] == [1, "alice@example.com", "Al"]
        assert body["owner moved"] == {"id": 111, "customer_id": 2}
        assert [r[:2] for r in after["owner moved"]][-1] == [111, 2]
        assert body["owner missing"] == {"error": "not_found", "detail": "User not found"}
        assert [r[:2] for r in after["owner missing"]][-1] == [111, 1]  # unchanged
        assert "error R310" in body["R310"]["detail"]
        assert body["delete"] is None and [r[0] for r in after["delete"]] == [8]
        assert after["memo null"][0] == [1, 1, "groceries", None]
        assert after["memo set"][1] == [2, 1, "call mom", "hi"]
        assert after["title and null memo"][0] == [1, 1, "t", None]
        assert body["title null"]["error"] == "validation"
        assert after["note unchanged"] == [list(r) for r in edges.NOTE_ROWS]
        assert after["note bob"][2] == [3, 2, "bob's", None]
        assert [n["id"] for n in body["find ci"]] == [2]
        assert [n["id"] for n in body["find end"]] == [2]
        assert body["find ci upper"] == body["find defaults"]  # the defaults' "call mom"
        assert "must match the pattern '(?i)[a-z ]+'" in str(body["find ci kelvin"])


def test_a_procedure_filter_before_page_is_rejected_by_both_backends(tmp_path: Path):
    """L880: the t2 program with its procedure filter moved before page()."""
    result, code = compile_source_to_fastapi(_T2.L880_PROGRAM, _T2.PROFILE)
    errors = [d.code for d in result.diagnostics if d.severity is Severity.ERROR]
    assert code is None and errors == ["L880"]
    db = tmp_path / "app.db"
    _T2.build_db(db)
    check, app = runtime.serve_source(_T2.L880_PROGRAM, _T2.PROFILE, db, natives=_T2.NATIVES)
    assert app is None and not check.ok


def test_the_solo_program_passes_its_cases(differential_runs):
    """NoRoles, a username token lookup, NativeContext natives and OneOf columns give the
    same results on both backends (compared above) and the expected ones."""
    rt_results, rt_captured, gen_results, gen_captured = differential_runs["solo"]
    want = {c.name: c.expect_status for c in SOLO_CASES}
    for results in (rt_results, gen_results):
        assert {c.name: c.status for c in results.cases if c.name in want} == want
    for captured in (rt_captured, gen_captured):
        body = {c["name"]: c["body"] for c in captured}
        assert body["/me alice"]["greeting"] == "Hello, alice!"
        assert body["/me bob"]["admin"] is False
        assert body["/tasks alice"] == body["/tasks/mine alice"]
        after = {c["name"]: c["after"] for c in captured}
        assert after["finish"][0][3] == "DONE"  # stored by name, answered as "done"
        assert body["finish"]["status"] == "done"


def test_the_solo_runtime_unique_program_rejects_an_ambiguous_caller(differential_runs):
    """unique="runtime": both backends answer 401 for no row and the same loud R311 500
    for two, never serving one of the rows."""
    rt_results, rt_captured, gen_results, gen_captured = differential_runs["solo_runtime"]
    want = {c.name: c.expect_status for c in SOLO_RUNTIME_CASES}
    for results in (rt_results, gen_results):
        assert {c.name: c.status for c in results.cases if c.name in want} == want
    bodies = []
    for captured in (rt_captured, gen_captured):
        body = {c["name"]: c["body"] for c in captured}
        assert "error R311: ambiguous caller" in json.dumps(body["two rows 500"])
        bodies.append(body["two rows 500"])
    assert bodies[0] == bodies[1]


def test_the_hardening_program_passes_its_cases(differential_runs):
    """Both backends decode the caller's stored spellings, record the caller as the owner
    of insert_many's rows, and answer R620 / R622 with the same located message."""
    rt_results, rt_captured, gen_results, gen_captured = differential_runs["hardening"]
    want = {c.name: c.expect_status for c in HARDENING_CASES}
    for results in (rt_results, gen_results):
        assert {c.name: c.status for c in results.cases if c.name in want} == want
    for captured in (rt_captured, gen_captured):
        body = {c["name"]: c["body"] for c in captured}
        assert [o["id"] for o in body["offers pro"]] == [2, 3]
        plan = {"id": 2, "role": "Customer", "plan": "pro", "p": "pro", "q": "pro"}
        assert body["plan pro"] == plan
        assert [r["owner_id"] for r in body["insert_many owner"]] == [3, 3]
        codes = (("pad over cap", "R622"), ("grow fuel", "R620"), ("shift huge", "R622"))
        for name, code in codes:
            assert f"error {code}" in json.dumps(body[name])


def test_the_role_set_program_passes_its_cases(differential_runs):
    """auth=Roles(...), Authenticated and stored role values admit exactly the same
    callers on both backends (compared above), and the expected ones."""
    rt_results, rt_captured, gen_results, gen_captured = differential_runs["rolesets"]
    want = {c.name: c.expect_status for c in ROLESETS_CASES}
    for results in (rt_results, gen_results):
        assert {c.name: c.status for c in results.cases if c.name in want} == want
    for captured in (rt_captured, gen_captured):
        body = {c["name"]: c["body"] for c in captured}
        assert body["/me ada"]["admin"] is True and body["/me odd"]["admin"] is False
        assert [r["id"] for r in body["/items ada"]["items"]] == [1, 2, 3]
