"""The six edit-slice gap features (plan 2026-09-27 thin slice; tests/api/gaps.py):
checker rules (each positive and negative), SQL and runtime semantics end to end, the
SQL functions under both DateTime storage spellings, and the tabular (polars) side of
the features that exist there (case-insensitive contains, the sort nulls policy).
The generated FastAPI app is compared with the runtime in test_codegen_differential."""

from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent

import polars as pl
import pytest
from starlette.testclient import TestClient

from lattice.api.backend import SqliteDialect
from lattice.api.ir import EndpointPlan, ReadStep, lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.profile import Profile
from lattice.runtime.exec import run_source
from lattice.typesys import Scalar, Schema
from tests.api import gaps
from tests.api.gaps import DELETED, LOCKED, NATIVES, PROFILE, PROGRAM, TOKENS, UNDATED, uid

S = Scalar


def _h(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


def _check(src: str, profile: Profile = PROFILE):
    return check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), profile)


def _diags(src: str, profile: Profile = PROFILE) -> list[tuple[str, str]]:
    return [
        (d.code, f"{d.message} {d.hint or ''}")
        for d in _check(src, profile).diagnostics
        if d.severity is Severity.ERROR
    ]


def _errors(src: str, profile: Profile = PROFILE) -> list[str]:
    return [code for code, _ in _diags(src, profile)]


def _plans(program: str = PROGRAM) -> tuple[EndpointPlan, ...]:
    check = check_source(program, PROFILE)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    return lower_api(check.program)


def _list(params: str, where: str, extra: str = "") -> str:
    """A list endpoint over the caller's items with `params` and one where()."""
    return f"""
    @endpoint("GET", "/items/", auth=Customer)
    def read_items(user: CurrentUser, {params}) -> Page:
        items = fetch(Item)
        mine = owned(items, by=user)
        found = where(mine, {where})
        {extra or 'ordered = sort(found, by=["id"])'}
        shown = select(ordered, cols=["id", "title"])
        result = page(shown, limit=10)
        return result
    """


def _put(params: str, body: str) -> str:
    """An update endpoint over one of the caller's items: `body` ends in `done = ...`."""
    return f"""
    @endpoint("PUT", "/items/{{id}}", auth=Customer)
    def update_item(id: Uuid, user: CurrentUser, {params}) -> One | NotFound | Forbidden:
        items = fetch(Item)
        mine = owned(items, by=user)
        found = where(mine, col("id") == id)
        item = one(found, else_=NotFound("Item not found"))
        {body}
        shown = select(done, cols=["id", "title"])
        return shown
    """


def test_the_fixture_program_checks_clean():
    result = check_source(PROGRAM, PROFILE)
    assert result.ok, [d.render() for d in result.diagnostics]
    assert [d for d in result.diagnostics if d.severity is Severity.ERROR] == []


# ==== 1. case-insensitive contains ======================================================


@pytest.mark.parametrize(
    "where",
    [
        'fn.contains(col("title"), sub=q, ignore_case=True)',
        'fn.contains(col("title"), sub="gro", ignore_case=True)',
        'fn.contains(col("title"), sub=q, ignore_case=False)',
        'fn.contains(col("title"), sub=q)',
    ],
)
def test_contains_ignore_case_is_accepted(where):
    assert _errors(_list("q: Str(max_len=10)", where)) == []


@pytest.mark.parametrize(
    ("where", "code", "message"),
    [
        ('fn.contains(col("title"), sub=q, ignore_case=flag)', "L404", "True or False"),
        ('fn.contains(col("title"), sub=q, ignore_case="yes")', "L402", "expects Bool"),
        ('fn.contains(col("title"), sub=q, case=True)', "L404", "no keyword argument 'case'"),
        ('fn.contains(col("title"), sub=fn.lower(q))', "L404", "must be a literal"),
    ],
)
def test_contains_ignore_case_must_be_a_bool_literal(where, code, message):
    found = _diags(_list("q: Str(max_len=10), flag: Bool = True", where))
    assert any(c == code and message in m for c, m in found), found


def test_contains_ignore_case_in_the_tabular_profile_matches_sql(tmp_path: Path):
    """polars and SQL agree on Unicode lowercase (Python str.lower / Rust to_lowercase):
    'ÄPFEL' contains 'äpf', 'İ' lowercases to 'i̇' (two code points), 'ß' is not 'SS'."""
    names = ["ÄPFEL", "Äpfel", "apfel", "STRASSE", "straße", "İstanbul", None, "Weekly GROCERIES"]
    schema = Schema.from_pairs([("id", S.INT), ("name", S.STR)], keys=[("id",)])
    csv = "id,name\n" + "".join(f"{i},{n or ''}\n" for i, n in enumerate(names))
    (tmp_path / "t.csv").write_text(csv)
    subs = ["äpf", "ÄPF", "SS", "ß", "i̇st", "grocer", "", "İ"]
    derived = ", ".join(
        f'c{i}=fn.contains(col("name"), sub="{s}", ignore_case=True)' for i, s in enumerate(subs)
    )
    src = (
        "# lattice: v1\n"
        f't = load("t.csv", schema=T)\nx = derive(t, {derived})\nwrite(x, "out.csv")\n'
    )
    profile = Profile(schemas={"T": schema})
    check, run = run_source(src, profile, tmp_path)
    assert check.ok and run is not None, [d.render() for d in check.diagnostics]
    polars_out = run.outputs["out.csv"]
    from lattice.api.sql import bind_values, compile_select, register_functions, rels_from_plan
    from lattice.ir import lower

    plan = lower(check.program)
    written = next(n for n in plan.nodes if n.op == "write")
    rels = rels_from_plan(plan.nodes, {"t.csv": ("t", schema)})
    conn = sqlite3.connect(":memory:")
    register_functions(conn)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO t VALUES (?, ?)", list(enumerate(names)))
    query = compile_select(rels, written.inputs[0])
    rows = conn.execute(query.text, bind_values(query)).fetchall()
    for i, s in enumerate(subs):
        sql_col = [None if r[2 + i] is None else bool(r[2 + i]) for r in rows]
        assert sql_col == polars_out[f"c{i}"].to_list(), s
        expected = [None if n is None else s.lower() in n.lower() for n in names]
        assert sql_col == expected, s


# ==== 2. sent() ==========================================================================

_TITLE = "title: Str(min_len=1, max_len=50) = UNSET"


@pytest.mark.parametrize(
    "body",
    [
        'ok = require(item, ~sent(title) | (col("title") != "x"), else_=Forbidden("no"))\n'
        "        done = update(ok, title=title)",
        'flagged = derive(item, touched=sent(title))\n'
        "        done = update(item, title=title)",
        'picked = where(item, sent(title) | is_null(col("description")))\n'
        "        done = update(picked, title=title)",
        'labelled = derive(item, t=when(sent(title), then=1, otherwise=0))\n'
        "        done = update(item, title=title)",
    ],
)
def test_sent_is_accepted_in_conditions(body):
    assert _errors(_put(_TITLE, body)) == []


@pytest.mark.parametrize(
    ("params", "body", "message"),
    [
        (  # a required parameter is always sent
            "title: Str(min_len=1, max_len=50)",
            'ok = require(item, ~sent(title) | (col("title") != "x"), else_=Forbidden("no"))\n'
            "        done = update(ok, title=title)",
            "only for an optional parameter",
        ),
        (  # a `| None` parameter: test it with is_null
            "title: Str(min_len=1, max_len=50) | None = None",
            'ok = require(item, ~sent(title) | (col("title") != "x"), else_=Forbidden("no"))\n'
            "        done = update(ok, title=title)",
            "is_null(title)",
        ),
        (
            _TITLE,
            'ok = require(item, ~sent(col("title")) | (col("title") != "x"), '
            'else_=Forbidden("no"))\n'
            "        done = update(ok, title=title)",
            "sent() takes one parameter name",
        ),
        (  # the parameter itself still only goes directly into update()/insert()
            _TITLE,
            'ok = require(item, sent(title) & (col("title") != title), else_=Forbidden("no"))\n'
            "        done = update(ok, title=title)",
            "is optional (default UNSET)",
        ),
    ],
)
def test_sent_misuse_is_l404(params, body, message):
    found = _diags(_put(params, body))
    assert any(c == "L404" and message in m for c, m in found), found


def test_sent_is_only_allowed_in_an_endpoint_body():
    src = """
    @step
    def touched(t: Table[Item], flag: Str) -> Table:
        out = derive(t, touched=sent(flag))
        return out
    """
    found = _diags(src)
    assert any(c == "L404" and "endpoint body" in m for c, m in found), found


def test_sent_is_unknown_in_tabular_programs():
    schema = Schema.from_pairs([("id", S.INT)])
    src = 't = load("t.csv", schema=T)\nx = derive(t, s=sent(1))\nwrite(x, "out.csv")\n'
    codes = _errors(src, Profile(schemas={"T": schema}))
    assert codes and "L404" not in codes


# ==== 3. DateTime arithmetic =============================================================


@pytest.mark.parametrize(
    "expr",
    [
        'col("created_at") >= fn.datetime_add(fn.now(), hours=-24)',
        'fn.datetime_add(col("created_at"), days=1, hours=2, minutes=3, seconds=4) > fn.now()',
        'fn.datetime_diff(fn.now(), col("created_at"), unit="hours") > 24',
        'fn.datetime_diff(fn.now(), col("created_at"), unit="days") > n',
        'col("created_at") > fn.datetime_add(fn.now(), minutes=n)',
    ],
)
def test_datetime_arithmetic_is_accepted(expr):
    assert _errors(_list("n: Int(ge=0, le=100) = 1", expr)) == []


@pytest.mark.parametrize(
    ("expr", "code", "message"),
    [
        ('col("created_at") >= fn.datetime_add(fn.now())', "L404", "at least one of"),
        ('col("created_at") >= fn.datetime_add(fn.now(), weeks=1)', "L404", "'weeks'"),
        ('col("created_at") >= fn.datetime_add(fn.now(), hours=1.5)', "L402", "expects Int"),
        ('fn.datetime_diff(fn.now(), col("created_at"), unit="weeks") > 1', "L404", "unit"),
        ('fn.datetime_diff(fn.now(), col("created_at")) > 1', "L404", "'unit'"),
        ('fn.datetime_add(col("title"), days=1) == fn.now()', "L402", "DateTime"),
        ('col("created_at") >= fn.now() - 1', "L402", "cannot combine"),
    ],
)
def test_datetime_arithmetic_misuse(expr, code, message):
    found = _diags(_list("n: Int(ge=0, le=100) = 1", expr))
    assert any(c == code and message in m for c, m in found), found


def test_datetime_functions_are_unknown_in_tabular_programs():
    schema = Schema.from_pairs([("id", S.INT), ("d", S.DATE)])
    src = (
        't = load("t.csv", schema=T)\n'
        'x = derive(t, e=fn.datetime_diff(col("d"), col("d"), unit="days"))\n'
        'write(x, "out.csv")\n'
    )
    assert "L106" in _errors(src, Profile(schemas={"T": schema}))


@pytest.mark.parametrize("storage", ["iso_z", "sql_space"])
def test_the_datetime_sql_functions_respect_the_storage(tmp_path: Path, storage: str):
    d = SqliteDialect("canonical", storage)
    stored = d.to_db(dt.datetime(2026, 3, 1, 10, 0, tzinfo=dt.UTC))
    conn = d.connect(tmp_path / "x.db")
    try:
        added, back, diffs, bad, huge, nulls = conn.execute(
            "SELECT lattice_datetime_add(?, 1, 2, -3, 4), lattice_datetime_add(?, 0, -24, 0, 0), "
            "json_array(lattice_datetime_diff(?, ?, 'hours'), "
            "lattice_datetime_diff(?, ?, 'hours'), lattice_datetime_diff(?, ?, 'days'), "
            "lattice_datetime_diff(?, ?, 'seconds'), lattice_datetime_diff(?, ?, 'minutes')), "
            "lattice_datetime_add('nope', 1, 0, 0, 0), "
            "lattice_datetime_add(?, 9999999, 0, 0, 0), lattice_datetime_add(NULL, 1, 0, 0, 0)",
            (
                stored,
                stored,
                stored,
                "2026-03-01T08:30:00Z",  # 1.5 h earlier: 1 whole hour
                "2026-03-01T08:30:00Z",
                stored,  # -1.5 h: -1 (truncated toward zero)
                stored,
                "2026-02-27 11:00:00",  # 1 day 23 h: 1 day
                stored,
                "2026-03-01 09:59:59.5",
                stored,
                "2026-03-01 10:00:00+01:00",  # an offset is honoured: 60 minutes
                stored,
            ),
        ).fetchone()
    finally:
        conn.close()
    expected = d.to_db(dt.datetime(2026, 3, 2, 11, 57, 4, tzinfo=dt.UTC))
    assert added == expected
    assert back == d.to_db(dt.datetime(2026, 2, 28, 10, 0, tzinfo=dt.UTC))
    assert diffs == "[1,-1,1,0,60]"
    assert bad is None and huge is None and nulls is None
    # stored text order is time order, so comparisons with stored columns are exact
    assert (back < stored) and (added > stored)


def test_now_is_one_instant_per_request_with_datetime_add():
    plans = {p.name: p for p in _plans()}
    reads = [s for s in plans["update_item"].steps if isinstance(s, ReadStep)]
    assert any(s.sql.now for s in reads)
    assert all(s.sql.text.count(":__now") <= 1 for s in reads)


# ==== 4. optional parameters without a sentinel ==========================================


@pytest.mark.parametrize(
    ("params", "where"),
    [
        ("since: DateTime | None = None", 'is_null(since) | (col("created_at") >= since)'),
        ("q: Str(max_len=10) | None = None", 'is_null(q) | (col("title") == q)'),
        ("n: Int(ge=0, le=5) | None = None", '(coalesce(n, 0) >= 0) & (col("title") != "x")'),
        ("flag: Bool | None = None", 'is_null(flag) | (col("title") != "x")'),
    ],
)
def test_nullable_parameters_are_accepted(params, where):
    assert _errors(_list(params, where)) == []


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ("since: DateTime | None", "default of 'since' must be None"),
        ('since: DateTime | None = "2024-01-01T00:00:00Z"', "default of 'since' must be None"),
        ("since: DateTime | None = UNSET", "default of 'since' must be None"),
        ("since: DateTime = None", "since: DateTime | None = None"),
        ("since: None | DateTime = None", "needs an API parameter type"),
        ("since: CurrentUser | None = None", "needs an API parameter type"),
    ],
)
def test_bad_nullable_parameters_are_l601(params, message):
    found = _diags(_list(params, 'col("title") != "x"'))
    assert any(c == "L601" and message in m for c, m in found), found


def test_a_path_parameter_cannot_be_nullable():
    src = """
    @endpoint("GET", "/items/{id}", auth=Customer)
    def read_item(user: CurrentUser, id: Uuid | None = None) -> One | NotFound:
        items = fetch(Item)
        found = where(items, col("id") == id)
        item = one(found, else_=NotFound("Item not found"))
        return item
    """
    found = _diags(src)
    assert any(c == "L404" and "path parameter" in m for c, m in found), found


def test_a_nullable_parameter_written_to_a_required_column_is_l410():
    ok = _put("text: Str(max_len=50) | None = None", "done = update(item, description=text)")
    assert _errors(ok) == []
    bad = _put("text: Str(max_len=50) | None = None", "done = update(item, title=text)")
    found = _diags(bad)
    assert any(c == "L410" and "'title'" in m for c, m in found), found


def test_unset_and_none_parameters_are_distinct():
    """UNSET = "leave unchanged" (writes only, sent() tests it); None = "not given"
    (a value like any other, is_null() tests it)."""
    params = "title: Str(min_len=1, max_len=50) = UNSET, note: Str(max_len=50) | None = None"
    body = (
        'ok = require(item, is_null(note) | ~sent(title) | (col("title") != note), '
        'else_=Forbidden("no"))\n'
        "        done = update(ok, title=title, description=note)"
    )
    assert _errors(_put(params, body)) == []
    specs = {p.name: p for p in _plans(PROGRAM)[0].params}
    assert specs["since"].default is None and not specs["since"].optional
    assert specs["since"].nullable


# ==== 5. null literals in writes ==========================================================


def test_null_is_written_to_a_nullable_column():
    assert _errors(_put("", "done = update(item, description=None)")) == []
    create = """
    @endpoint("POST", "/items/", auth=Customer)
    def create(user: CurrentUser, title: Str(min_len=1, max_len=9)) -> Created:
        made = insert(Item, owner=user, title=title, description=None)
        return made
    """
    assert _errors(create) == []


def test_null_into_a_required_column_is_l410():
    found = _diags(_put("", "done = update(item, title=None)"))
    assert any(c == "L410" and "'title'" in m for c, m in found), found
    create = """
    @endpoint("POST", "/items/", auth=Customer)
    def create(user: CurrentUser) -> Created:
        made = insert(Item, owner=user, title=None)
        return made
    """
    found = _diags(create)
    assert any(c == "L410" and "'title'" in m for c, m in found), found


def test_null_is_still_not_an_expression():
    found = _diags(_put("", 'more = derive(item, x=None)\n        done = update(more, title="a")'))
    assert any(c == "L410" for c, _ in found), found
    found = _diags(_put("", 'done = update(item, title=coalesce(None, "a"))'))
    assert any(c == "L410" for c, _ in found), found


# ==== 6. the sort direction from a parameter; the nulls policy ============================


@pytest.mark.parametrize(
    ("params", "extra"),
    [
        ('sort: OneOf("newest", "oldest") = "newest"',
         'ordered = sort(found, by=["created_at"], desc=[sort == "newest"], nulls="smallest")'),
        ("newest: Bool = True", 'ordered = sort(found, by=["created_at"], desc=newest)'),
        ("newest: Bool = True",
         'ordered = sort(found, by=["created_at", "id"], desc=[newest, False], nulls="first")'),
        ('d: Str(pattern="asc|desc") = "asc"',
         'ordered = sort(found, by=["created_at"], desc=[d == "desc"], nulls="last")'),
        ("", 'ordered = sort(found, by=["created_at"], desc=True, nulls="smallest")'),
    ],
)  # fmt: skip
def test_sort_direction_from_a_parameter_is_accepted(params, extra):
    assert _errors(_list(params or "n: Int = 1", 'col("title") != "x"', extra)) == []


@pytest.mark.parametrize(
    ("params", "extra", "message"),
    [
        ("", 'ordered = sort(found, by=["created_at"], desc=[col("title") == "x"])',
         "no columns"),
        ('d: Str(max_len=4) = "asc"', 'ordered = sort(found, by=["created_at"], desc=[d])',
         "Bool"),
        ("", 'ordered = sort(found, by=["created_at"], nulls="middle")', "nulls="),
        ('d: Str(max_len=4) = "last"', 'ordered = sort(found, by=["created_at"], nulls=d)',
         "nulls="),
        ("newest: Bool = True", 'ordered = sort(found, by=["created_at", "id"], desc=[newest])',
         "one True/False"),
    ],
)  # fmt: skip
def test_bad_sort_directions_are_l404(params, extra, message):
    found = _diags(_list(params or "n: Int = 1", 'col("title") != "x"', extra))
    assert any(c == "L404" and message in m for c, m in found), found


def test_the_tabular_sort_direction_stays_literal():
    schema = Schema.from_pairs([("id", S.INT)])
    src = (
        "# lattice: v1\n"
        'D = True\nt = load("t.csv", schema=T)\nx = sort(t, by=["id"], desc=[D])\n'
        'write(x, "out.csv")\n'
    )
    codes = [d.code for d in check_source(src, Profile(schemas={"T": schema})).diagnostics]
    assert "L404" in codes


@pytest.mark.parametrize("nulls", ["last", "first", "smallest"])
@pytest.mark.parametrize("desc", ["True", "False", "[True, False]", "[False, True]"])
def test_the_sort_nulls_policy_matches_between_polars_and_sql(tmp_path: Path, nulls, desc):
    rows = [(1, 3, "b"), (2, None, "a"), (3, 1, None), (4, 3, "a"), (5, None, None), (6, 2, "c")]
    schema = Schema.from_pairs([("id", S.INT), ("k", S.INT), ("s", S.STR)], keys=[("id",)])
    csv = "id,k,s\n" + "".join(
        f"{i},{'' if k is None else k},{'' if s is None else s}\n" for i, k, s in rows
    )
    (tmp_path / "t.csv").write_text(csv)
    src = (
        "# lattice: v1\n"
        f't = load("t.csv", schema=T)\nx = sort(t, by=["k", "s"], desc={desc}, nulls="{nulls}")\n'
        'write(x, "out.csv")\n'
    )
    check, run = run_source(src, Profile(schemas={"T": schema}), tmp_path)
    assert check.ok and run is not None, [d.render() for d in check.diagnostics]
    polars_ids = run.outputs["out.csv"]["id"].to_list()
    from lattice.api.sql import bind_values, compile_select, register_functions, rels_from_plan
    from lattice.ir import lower

    plan = lower(check.program)
    written = next(n for n in plan.nodes if n.op == "write")
    rels = rels_from_plan(plan.nodes, {"t.csv": ("t", schema)})
    conn = sqlite3.connect(":memory:")
    register_functions(conn)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, k INTEGER, s TEXT)")
    conn.executemany("INSERT INTO t VALUES (?, ?, ?)", rows)
    query = compile_select(rels, written.inputs[0])
    sql_ids = [r[0] for r in conn.execute(query.text, bind_values(query)).fetchall()]
    assert sql_ids == polars_ids
    # the policy: nulls last / first always; "smallest": first ascending, last descending
    flags = [desc == "True"] * 2 if desc in ("True", "False") else [desc.startswith("[True")] * 2
    frame = pl.DataFrame(rows, schema=["id", "k", "s"], orient="row")
    k_nulls_last = {"last": True, "first": False, "smallest": flags[0]}[nulls]
    first_nulls = [r for r in sql_ids if frame.filter(pl.col("id") == r)["k"][0] is None]
    positions = [sql_ids.index(r) for r in first_nulls]
    assert positions == ([4, 5] if k_nulls_last else [0, 1])


# ==== end to end through the runtime ======================================================


@pytest.fixture(scope="module")
def pristine(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("gaps") / "pristine.db"
    gaps.build_db(path)
    return path


@pytest.fixture
def db(pristine: Path, tmp_path: Path) -> Path:
    path = tmp_path / "app.db"
    shutil.copyfile(pristine, path)
    return path


@pytest.fixture
def client(db: Path) -> Iterator[TestClient]:
    with TestClient(build_app(_plans(), db, natives=NATIVES)) as c:
        yield c


def _titles(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [r["title"] for r in resp.json()["data"]]


def test_search_ignores_case_and_counts_the_matches(client: TestClient):
    got = client.get("/items/", params={"q": "GROC"}, headers=_h("alice"))
    assert _titles(got) == ["weekly GROCERIES"] and got.json()["count"] == 1
    assert _titles(client.get("/items/", params={"q": "a"}, headers=_h("alice"))) == [
        "Alpha",
        "Beta",
    ]
    assert _titles(client.get("/items/", params={"q": "zzz"}, headers=_h("alice"))) == []
    everything = _titles(client.get("/items/", headers=_h("alice")))
    assert _titles(client.get("/items/", params={"q": ""}, headers=_h("alice"))) == everything
    assert everything == ["weekly GROCERIES", "Alpha", "Beta"]  # Delta is deleted


def test_since_filters_and_the_sort_follows_the_parameter(client: TestClient):
    bob = _titles(client.get("/items/", headers=_h("bob")))
    assert bob == ["Gamma", "Epsilon"]  # Epsilon is undated: nulls last when newest first
    assert _titles(client.get("/items/", params={"sort": "oldest"}, headers=_h("bob"))) == [
        "Epsilon",  # nulls="smallest": first when oldest first
        "Gamma",
    ]
    since = {"since": "2000-01-04T12:00:00+02:00"}  # 10:00 UTC, inclusive
    assert _titles(client.get("/items/", params=since, headers=_h("bob"))) == ["Gamma"]
    alice = client.get("/items/", params={"since": "2000-01-05T10:00:00Z"}, headers=_h("alice"))
    assert _titles(alice) == ["weekly GROCERIES", "Alpha"] and alice.json()["count"] == 2
    bad = client.get("/items/", params={"since": "yesterday"}, headers=_h("alice"))
    assert bad.status_code == 422
    assert client.get("/items/", params={"sort": "random"}, headers=_h("bob")).status_code == 422


def test_a_bool_parameter_sets_the_direction(client: TestClient):
    def ids(**params) -> list[str]:
        got = client.get("/items/ordered", params=params, headers=_h("bob"))
        assert got.status_code == 200, got.text
        return [r["title"] for r in got.json()["data"]]

    assert ids() == ["Epsilon", "Gamma"]  # nulls="first"
    assert ids(newest_first="true") == ["Epsilon", "Gamma"]
    got = client.get("/items/ordered-last", params={"newest_first": "false"}, headers=_h("bob"))
    assert [r["title"] for r in got.json()["data"]] == ["Gamma", "Epsilon"]


def test_the_title_window(client: TestClient, db: Path):
    now = dt.datetime.now(dt.UTC)
    conn = sqlite3.connect(db)
    for n, hours in ((101, 1), (102, 25)):
        stamp = (now - dt.timedelta(hours=hours)).isoformat(timespec="microseconds")
        conn.execute(
            "UPDATE item SET created_at = ? WHERE id = ?", (stamp.replace("+00:00", "Z"), uid(n))
        )
    conn.commit()
    conn.close()
    fresh, old = f"/items/{uid(101)}", f"/items/{uid(102)}"
    assert client.put(fresh, json={"title": "Renamed"}, headers=_h("alice")).status_code == 200
    locked = client.put(old, json={"title": "Renamed"}, headers=_h("alice"))
    assert locked.status_code == 403 and locked.json()["detail"] == LOCKED
    # an explicit null for a required column is refused before the body runs
    assert client.put(old, json={"title": None}, headers=_h("alice")).status_code == 422
    described = client.put(old, json={"description": "ok"}, headers=_h("alice"))
    assert described.status_code == 200 and described.json()["title"] == "Beta"
    assert client.put(old, json={"title": "Fixed"}, headers=_h("admin")).status_code == 200
    # undated items are never locked; ownership is checked first
    undated = client.put(f"/items/{UNDATED}", json={"title": "x"}, headers=_h("bob"))
    assert undated.status_code == 200
    other = client.put(f"/items/{UNDATED}", json={"title": "x"}, headers=_h("alice"))
    assert other.status_code == 403 and other.json()["detail"] == "Not enough permissions"


def test_soft_delete_restore_and_null_writes(client: TestClient, db: Path):
    def deleted_at(item: str) -> object:
        conn = sqlite3.connect(db)
        try:
            return conn.execute("SELECT deleted_at FROM item WHERE id = ?", (item,)).fetchone()[0]
        finally:
            conn.close()

    restored = client.post(f"/items/{DELETED}/restore", headers=_h("admin"))
    assert restored.status_code == 200 and restored.json()["title"] == "Delta"
    assert deleted_at(DELETED) is None
    again = client.post(f"/items/{DELETED}/restore", headers=_h("admin"))
    assert again.status_code == 400 and again.json()["detail"] == "Item is not deleted"
    assert client.post(f"/items/{DELETED}/restore", headers=_h("alice")).status_code == 403
    assert client.delete(f"/items/{uid(101)}", headers=_h("alice")).status_code == 204
    assert deleted_at(uid(101)) is not None
    made = client.post("/items/", json={"title": "New"}, headers=_h("alice"))
    assert made.status_code == 201 and made.json()["description"] is None


def test_datetime_values_in_a_response(client: TestClient):
    got = client.get(f"/items/{uid(101)}/times", headers=_h("alice"))
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["next_day"] == "2000-01-06T09:30:00.000000Z"
    assert body["back"] == "2000-01-05T09:58:30.000000Z"
    # created 2000-01-05T10:00Z, start 2000-01-01T00:00Z: 4 days 10 hours later
    assert (body["hours"], body["days"], body["minutes"]) == (106, -4, 106 * 60)
    assert body["seconds"] == 106 * 3600 and body["recent"] is False
