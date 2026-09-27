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

PROGRAMS = _programs()
TASK_IDS = [t.id for t in API_TASKS]
EXAMPLE_IDS = [f"example{i}" for i in range(1, len(LATTICE_EXAMPLES) + 1)]
EXTRA_IDS = ["numeric", "mapped_join"]


# ---- capture ---------------------------------------------------------------------------------


def db_digest(path: str) -> str:
    """A digest of every table's rows (stdlib only: also copied into the sandbox driver)."""
    import hashlib as _hashlib
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(path)
    try:
        h = _hashlib.sha256()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        for (table,) in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            rows = conn.execute(f"SELECT * FROM {quoted} ORDER BY rowid").fetchall()
            h.update(repr((table, rows)).encode())
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
    app_source: str, workdir: Path, task: ApiTask, pristine: Path
) -> tuple[CaseResults, list[dict]]:
    capture = workdir / CAPTURE_FILE
    capture.unlink(missing_ok=True)
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
        self.sink.append(
            {
                "name": self.case.name,
                "status": status,
                "body": body,
                "crash": "",
                "after": after,
                "digest": db_digest(str(self.db_path)),
            }
        )


def _run_runtime(source: str, workdir: Path, task: ApiTask, pristine: Path):
    captured: list[dict] = []
    calls = [0]

    def serve(src: str, profile: Any, db_path: Path):
        check, app = runtime.serve_source(src, profile, db_path)
        case = task.cases[calls[0]]
        calls[0] += 1
        return check, (None if app is None else _Shim(app, db_path, case, captured))

    results = run_cases_lattice(
        source,
        workdir,
        task,
        db_builder=_copier(pristine),
        profile=api_profile(),
        serve=serve,
        hooks=runtime,
    )
    return results, captured


@pytest.fixture(scope="session")
def differential_runs(tmp_path_factory: pytest.TempPathFactory) -> Mapping[str, tuple]:
    """program id -> (runtime results, runtime captures, generated results, captures)."""
    root = tmp_path_factory.mktemp("codegen_diff")
    pristine = root / "pristine.db"
    build_db(pristine)
    apps: dict[str, str] = {}
    for pid, (source, _task) in PROGRAMS.items():
        check, app_source = compile_source_to_fastapi(source, api_profile())
        assert app_source is not None, [d.render() for d in check.diagnostics]
        apps[pid] = app_source
        (root / f"{pid}_app.py").write_text(app_source)

    runtime_runs = {
        pid: _run_runtime(source, root / f"{pid}_runtime", task, pristine)
        for pid, (source, task) in PROGRAMS.items()
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(api_runner, "driver_source", _capturing_driver_source)

        def gen(pid: str):
            return _run_generated(apps[pid], root / f"{pid}_generated", PROGRAMS[pid][1], pristine)

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
            "after_sql rows": (rc["after"], gc["after"]),
            "db digest": (rc["digest"], gc["digest"]),
        }
        for what, (x, y) in pairs.items():
            if x != y:
                problems.append(f"{where}: {what} differs: runtime {x!r} vs generated {y!r}")
        if not strict_equal(rc["body"], gc["body"]):
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
