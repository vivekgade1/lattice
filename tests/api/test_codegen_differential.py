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
from lattice.llm.api_prompt import LATTICE_EXAMPLES
from tests.api import effects, loops, partb, realapps

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
TASK_IDS = [t.id for t in API_TASKS]
EXAMPLE_IDS = [f"example{i}" for i in range(1, len(LATTICE_EXAMPLES) + 1)]
EXTRA_IDS = ["numeric", "mapped_join", "realapps", "partb", "loops", "effects"]


@dataclasses.dataclass(frozen=True)
class Setup:
    """How a program runs: its profile, database, natives (and the natives module
    copied next to the generated app)."""

    profile: Any
    build: Any
    natives: Mapping[str, Any] | None = None
    natives_file: Path | None = None


DEFAULT_SETUP = Setup(api_profile(), build_db)
SETUPS = {
    "realapps": Setup(_R.PROFILE, _R.build_db, _R.NATIVES, _R.NATIVES_FILE),
    "partb": Setup(_PB.PROFILE, _PB.build_db, _PB.NATIVES, _PB.NATIVES_FILE),
    "loops": Setup(loops.PROFILE, loops.build_db),
    "effects": Setup(effects.PROFILE, effects.build_db, effects.NATIVES, effects.NATIVES_FILE),
}


def setup_of(pid: str) -> Setup:
    return SETUPS.get(pid, DEFAULT_SETUP)


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


_ANCHOR = "    passed, reason = evaluate_case(case, status, body, queries, after_rows, crash)\n"
_CAPTURE = (
    "    with open(" + repr(CAPTURE_FILE) + ', "a") as _cf:\n'
    "        _cf.write(json.dumps({\n"
    '            "name": case["name"], "status": status, "body": body, "crash": crash,\n'
    '            "queries": queries, "after": after_rows,\n'
    '            "digest": None if crash else db_digest("app.db"),\n'
    '        }) + "\\n")\n'
)


def _capturing_driver_source(task: ApiTask) -> str:
    source = _original_driver_source(task)
    assert _ANCHOR in source, "the sandbox driver changed: update the capture anchor"
    helper = inspect.getsource(db_digest)
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
    """ASGI wrapper recording one case's response body and the DB state after it."""

    def __init__(self, app: Any, db_path: Path, case: HttpCase, sink: list[dict]) -> None:
        self.app, self.db_path, self.case, self.sink = app, db_path, case, sink
        self.recorded: int | None = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status, chunks = 0, []

        async def record(message: Mapping[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
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
        # a followed redirect is two requests: keep the final response's record
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
        check, app = runtime.serve_source(src, profile, db_path, natives=setup.natives)
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


def _mismatches(pid: str, runs: tuple) -> list[str]:
    rt_results, rt_captured, gen_results, gen_captured = runs
    task = PROGRAMS[pid][1]
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
        if gc["crash"]:
            problems.append(f"{where}: generated app crashed: {gc['crash']}")
            continue
        pairs = {
            "status": (rt.status, gen.status),
            "queries": (rt.queries, gen.queries),
            "passed": (rt.passed, gen.passed),
            "reason": (rt.reason, gen.reason),
            "after_sql rows": (normalize(rc["after"]), normalize(gc["after"])),
            "db digest": (rc["digest"], gc["digest"]),
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
