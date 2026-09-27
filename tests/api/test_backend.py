"""The backend capability model and the dialect-parameterized SQL layer (spec §3).

* the dialect registry and the SQLite dialect's capabilities;
* the SQLite dialect compiles byte-for-byte what the SQL layer compiled before it was
  parameterized (tests/fixtures/sql_snapshot.json, see tests/api/sql_snapshot.py);
* a dialect with other quoting/bind spellings (still executable by SQLite) changes
  exactly those spellings, and serves the same rows and responses;
* a dialect that spells every non-portable piece differently leaves no SQLite text in
  the output — the compiler hard-codes none;
* P7 (L870-L873) with a dialect whose capabilities are all false.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import re
import shutil
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from bench.api_datasets import api_profile
from lattice.api import backend
from lattice.api.backend import (
    DIALECTS,
    SQLITE,
    Capabilities,
    Dialect,
    SqliteDialect,
    dialect_for,
)
from lattice.api.codegen import generate_fastapi
from lattice.api.ir import lower_api
from lattice.api.runtime import serve_source
from lattice.api.sql import (
    ParamRef,
    bind_values,
    compile_delete,
    compile_insert,
    compile_lookup,
    compile_select,
    compile_update,
    expr_sql,
    rels_from_plan,
)
from lattice.check import check_source, p7_backend
from lattice.check.context import CheckContext
from lattice.ir import lower
from lattice.profile import Profile
from lattice.runtime import LatticeInternalError
from tests.api import test_runtime as rt
from tests.api.conftest import source
from tests.api.sql_snapshot import FIXTURES, SNAPSHOT, collect
from tests.api.test_sql import CASES, LOAD, PROFILE, TABLES, program

# ---- dialects used by the tests ------------------------------------------------------------


class BracketDialect(SqliteDialect):
    """SQLite with other (still valid SQLite) spellings: [identifiers] and @binds."""

    name = "bracket"

    def quote(self, ident: str) -> str:
        return "[" + ident.replace("]", "]]") + "]"

    def bind(self, name: str) -> str:
        return "@" + name


class MarkerDialect(SqliteDialect):
    """Every non-portable piece spelled differently (not executable): whatever SQLite
    text survives in its output was hard-coded by the compiler."""

    name = "marker"

    def quote(self, ident: str) -> str:
        return "`" + ident + "`"

    def bind(self, name: str) -> str:
        return "%(" + name + ")s"

    def positional_bind(self) -> str:
        return "%s"

    def rows_relation(self, bind: str, columns: Sequence[str], ord_col: str) -> str:
        cols = ", ".join(f"ROWS_COL({i}) AS {self.quote(c)}" for i, c in enumerate(columns))
        return f"SELECT {cols}, ROWS_ORD() AS {ord_col} FROM ROWS_OF({self.bind(bind)})"

    def natural_order(self) -> str:
        return "NATURAL_ORDER()"

    def row_number(self, order_by: str) -> str:
        return f"ROWNUM({order_by})"

    def true_condition(self) -> str:
        return "TRUE"

    def flag(self, expr: str) -> str:
        return f"FLAG({expr})"

    def divide(self, a: str, b: str) -> str:
        return f"DIV({a}, {b})"

    def modulo(self, a: str, b: str) -> str:
        return f"FLOORMOD({a}, {b})"

    def function(self, name: str, x: str, kw: Mapping[str, str]) -> str | None:
        return f"FN_{name}({', '.join([x, *kw.values()])})"

    def insert(self, table: str, cols: Sequence[str], vals: Sequence[str], ret: str) -> str:
        return f"INS {table} ({', '.join(cols)}) ({', '.join(vals)}) RET {ret}"

    def update_from(self, table: str, sets: str, source: str, match: str, ret: str) -> str:
        return f"UPD {table} SET {sets} USING ({source}) s ON {match} RET {ret}"

    def new_uuid(self) -> str:
        return "NEW_UUID()"

    def native(self, name: str, args: Sequence[str]) -> str:
        return f"NATIVE_{name}({', '.join(args)})"


_NONE = Capabilities(
    transactions=False,
    returning=False,
    unique_constraints=False,
    window_functions=False,
    json_params=False,
    mutations_sync=False,
    native_functions=False,
)


class NoCapsDialect(SqliteDialect):
    name = "nocaps"
    capabilities = _NONE


BRACKET, MARKER, NOCAPS = BracketDialect(), MarkerDialect(), NoCapsDialect()


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test dialects, registered for the test only."""
    for d in (BRACKET, MARKER, NOCAPS):
        monkeypatch.setitem(DIALECTS, d.name, d)


def with_backend(profile: Profile, name: str) -> Profile:
    """`profile` with `api.backend = name` (works before ApiProfile has the field)."""
    api = profile.api
    assert api is not None
    try:
        api = dataclasses.replace(api, backend=name)  # type: ignore[call-arg]
    except TypeError:
        api = dataclasses.replace(api)
        object.__setattr__(api, "backend", name)
    return dataclasses.replace(profile, api=api)


# ---- the registry --------------------------------------------------------------------------


def test_registry_resolves_sqlite() -> None:
    assert DIALECTS["sqlite"] is SQLITE
    assert dialect_for("sqlite") is SQLITE
    assert SQLITE.name == "sqlite"
    assert isinstance(SQLITE, Dialect)


def test_unknown_backend_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="unknown backend 'oracle'"):
        dialect_for("oracle")


def test_sqlite_has_every_capability() -> None:
    caps = SQLITE.capabilities
    assert [f.name for f in dataclasses.fields(caps)] == [
        "transactions",
        "returning",
        "unique_constraints",
        "window_functions",
        "json_params",
        "mutations_sync",
        "native_functions",
    ]
    assert all(getattr(caps, f.name) is True for f in dataclasses.fields(caps))


def test_capabilities_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        SQLITE.capabilities.returning = False  # type: ignore[misc]


def test_sqlite_pieces() -> None:
    assert SQLITE.quote('a"b') == '"a""b"'
    assert SQLITE.bind("p_x") == ":p_x"
    assert SQLITE.positional_bind() == "?"


def test_sqlite_connect_registers_functions_and_runs_the_hook(tmp_path: Path) -> None:
    seen: list[object] = []
    conn = SQLITE.connect(tmp_path / "x.db", seen.append)
    try:
        assert seen == [conn]
        assert conn.isolation_level is None  # autocommit: transactions are explicit
        got = conn.execute(
            "SELECT lattice_lower('ÄB'), lattice_upper('ä'), lattice_strip(' x '), "
            "lattice_mod(-7, 3), lattice_mod(1, 0)"
        ).fetchone()
        assert got == ("äb", "Ä", "x", 2, None)
        SQLITE.begin(conn, writes=True)
        assert conn.in_transaction
        SQLITE.rollback(conn)
        assert not conn.in_transaction
        SQLITE.rollback(conn)  # no transaction: never raises
    finally:
        conn.close()


# ---- SQLite output is unchanged ---------------------------------------------------------------


def test_sqlite_sql_matches_the_pre_dialect_snapshot() -> None:
    expected = json.loads(SNAPSHOT.read_text())
    assert len(expected) > 100
    actual = json.loads(json.dumps(collect()))
    assert actual.keys() == expected.keys()
    for key in expected:
        assert actual[key] == expected[key], key


def test_explicit_sqlite_dialect_is_the_default() -> None:
    expected = json.loads(SNAPSHOT.read_text())
    actual = json.loads(json.dumps(collect(dialect=SQLITE)))
    assert {k: v for k, v in actual.items() if k.startswith("tabular/")} == {
        k: v for k, v in expected.items() if k.startswith("tabular/")
    }


def _e(text: str) -> ast.expr:
    return ast.parse(text, mode="eval").body


# Captured from the SQL layer before it was parameterized by a dialect.
_WRITES_BEFORE = {
    "insert_default": 'INSERT INTO "t" DEFAULT VALUES RETURNING "id"',
    "insert_owner": 'INSERT INTO "t" ("a", "o") VALUES (:__l0, :__user_id) RETURNING "id", "a"',
    "delete_two_keys": (
        "WITH t0 AS (SELECT json_extract(j.value, '$[0]') AS \"a\", "
        "json_extract(j.value, '$[1]') AS \"b\", json_extract(j.value, '$[2]') AS \"c\", "
        'j.key AS __ord FROM json_each(:__r0) AS j)\nDELETE FROM "t" WHERE ("a", "b") IN '
        '(SELECT "a", "b" FROM t0)'
    ),
    "update_two_keys": (
        "WITH t0 AS (SELECT json_extract(j.value, '$[0]') AS \"a\", "
        "json_extract(j.value, '$[1]') AS \"b\", json_extract(j.value, '$[2]') AS \"c\", "
        'j.key AS __ord FROM json_each(:__r0) AS j)\nUPDATE "t" SET "c" = s."__v0" FROM '
        '(SELECT "a" AS "__k0", "b" AS "__k1", lattice_mod("c", :__l0) AS "__v0" FROM t0) '
        'AS s WHERE "t"."a" = s."__k0" AND "t"."b" = s."__k1" RETURNING "a", "b", "c"'
    ),
    "expr": '(CAST(lattice_lower("x") AS REAL) / :__l0)',
    "lookup": 'SELECT "id", "role" FROM "users" WHERE "token" = ?',
}


def _writes(dialect: Dialect | None = None) -> dict[str, str]:
    kw = {} if dialect is None else {"dialect": dialect}
    src = ("s1", ("a", "b", "c"))
    return {
        "insert_default": compile_insert("t", [], ["id"], **kw).text,
        "insert_owner": compile_insert(
            "t", [("a", ast.Constant(1))], ["id", "a"], owner="o", **kw
        ).text,
        "delete_two_keys": compile_delete("t", ["a", "b"], src, **kw).text,
        "update_two_keys": compile_update(
            "t", ["a", "b"], src, [("c", _e('col("c") % 2'))], **kw
        ).text,
        "expr": expr_sql(_e('fn.lower(col("x")) / 2'), **kw),
        "lookup": compile_lookup("users", ["id", "role"], "token", **kw),
    }


def test_sqlite_writes_match_the_pre_dialect_text() -> None:
    assert _writes() == _WRITES_BEFORE
    assert _writes(SQLITE) == _WRITES_BEFORE


def test_compile_read_and_write_are_the_dialect_entry_points() -> None:
    from lattice.api.sql import DeleteWrite, ReadPipeline

    tab = _tabular_plan("limit_no_sort")
    rels, target = tab
    assert SQLITE.compile_read(ReadPipeline(rels, target)) == compile_select(rels, target)
    spec = DeleteWrite("t", ["a", "b"], ("s1", ("a", "b", "c")))
    assert SQLITE.compile_write(spec).text == _WRITES_BEFORE["delete_two_keys"]


# ---- other spellings -----------------------------------------------------------------------------


def _tabular_plan(name: str) -> tuple[dict, str]:
    body = LOAD + CASES[name] + '\nwrite(x, "out.csv")\n'
    check = check_source(program(body), PROFILE)
    assert check.program is not None, name
    plan = lower(check.program)
    written = next(n for n in plan.nodes if n.op == "write")
    return rels_from_plan(plan.nodes, TABLES), written.inputs[0]


_IDENT = re.compile(r'"((?:[^"]|"")*)"')
_BIND = re.compile(r":(\w+)")


def _respell(text: str) -> str:
    """SQLite text with identifiers as [x] and binds as @x."""
    text = _IDENT.sub(lambda m: "[" + m.group(1).replace('""', '"') + "]", text)
    return _BIND.sub(lambda m: "@" + m.group(1), text)


def _cases() -> list[str]:
    return sorted(n for n in CASES if n != "constants")


@pytest.mark.parametrize("name", _cases())
def test_other_quoting_and_binds_change_exactly_those(name: str) -> None:
    rels, target = _tabular_plan(name)
    base = compile_select(rels, target)
    other = compile_select(rels, target, dialect=BRACKET)
    assert other.text == _respell(base.text)
    assert (other.params, other.binds, other.rows, other.user) == (
        base.params,
        base.binds,
        base.rows,
        base.user,
    )
    assert '"' not in other.text and not _BIND.search(other.text)


@pytest.fixture(scope="module")
def items_db(tmp_path_factory: pytest.TempPathFactory) -> sqlite3.Connection:
    from tests.api.test_sql import ITEM_ROWS, TAG_ROWS

    conn = SQLITE.connect(tmp_path_factory.mktemp("backend") / "db.sqlite")
    conn.execute(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, grp TEXT, qty INTEGER, "
        "price REAL, day TEXT, flag INTEGER)"
    )
    conn.execute("CREATE TABLE tags (id INTEGER, tag TEXT, weight INTEGER)")
    conn.executemany("INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?)", ITEM_ROWS)
    conn.executemany("INSERT INTO tags VALUES (?, ?, ?)", TAG_ROWS)
    return conn


@pytest.mark.parametrize("name", _cases())
def test_other_spellings_read_the_same_rows(name: str, items_db: sqlite3.Connection) -> None:
    rels, target = _tabular_plan(name)
    base = compile_select(rels, target)
    other = compile_select(rels, target, dialect=BRACKET)
    want = items_db.execute(base.text, bind_values(base)).fetchall()
    got = items_db.execute(other.text, bind_values(other, dialect=BRACKET)).fetchall()
    assert got == want


# SQLite-only spellings (the SQLite dialect's pieces); none may come from the compiler.
_SQLITE_TEXT = (
    '"',
    "json_each",
    "json_extract",
    "j.key",
    "rowid",
    "ROW_NUMBER",
    "lattice_",
    "strftime",
    "instr(",
    "substr(",
    "length(",
    "AS REAL",
    "DEFAULT VALUES",
    "RETURNING",
    "INSERT INTO",
    "UPDATE ",
    " ON 1",
    "?",
)


def _assert_no_sqlite_text(text: str, where: str) -> None:
    for token in _SQLITE_TEXT:
        assert token not in text, f"{where}: {token!r} in {text}"
    assert not _BIND.search(text), f"{where}: a :bind in {text}"


@pytest.mark.parametrize("name", _cases())
def test_compiler_emits_no_sqlite_text_for_reads(name: str) -> None:
    rels, target = _tabular_plan(name)
    extra = [("__e", _e('fn.contains(fn.lower(col("id")), sub="x") | (col("id") % 2 == 1)'))]
    if "id" not in rels[target].schema.names():
        extra = []
    _assert_no_sqlite_text(compile_select(rels, target, dialect=MARKER, extra=extra).text, name)


def test_compiler_emits_no_sqlite_text_for_writes_and_lookups() -> None:
    for key, text in _writes(MARKER).items():
        _assert_no_sqlite_text(text, key)
    assert _writes(MARKER)["insert_default"] == "INS `t` () () RET `id`"


def test_api_programs_compile_through_the_profiles_backend(registered: None) -> None:
    """ApiProfile.backend selects the dialect lowering compiles with (MARKER has
    SQLite's capabilities, so P7 accepts every reference program)."""
    profile = with_backend(api_profile(), "marker")
    n = 0
    for path in sorted((FIXTURES / "api").glob("*/lattice.py")):
        check = check_source(path.read_text(), profile)
        assert check.program is not None, path
        for plan in lower_api(check.program):
            assert plan.backend == "marker"
            for step in plan.steps:
                sql = getattr(step, "sql", None)
                if sql is not None and sql.text:
                    _assert_no_sqlite_text(sql.text, f"{path.parent.name}/{step.id}")
                    n += 1
    assert n > 50


# ---- the runtime over another dialect ----------------------------------------------------------


@pytest.fixture
def bracket_db(db: Path, tmp_path: Path) -> Path:
    other = tmp_path / "bracket.db"
    shutil.copy(db, other)
    return other


def _exchange(client: TestClient, db: Path) -> list[object]:
    oid = rt.order_of(db, "alice", "pending")
    out: list[object] = []
    for method, url, headers, body in [
        ("GET", "/orders?limit=3&offset=1", rt.auth("alice"), None),
        ("GET", "/orders", rt.auth("admin"), None),
        ("POST", f"/orders/{oid}/cancel", rt.auth("alice"), None),
        ("POST", f"/orders/{oid}/cancel", rt.auth("alice"), None),  # now a conflict
        ("POST", "/orders/999999/cancel", rt.auth("alice"), None),
        (
            "POST",
            "/addresses",
            rt.auth("bob"),
            {"line1": "1 Road", "city": "X", "postcode": "12345"},
        ),
        ("GET", "/orders", None, None),
    ]:
        r = client.request(method, url, headers=headers, json=body)
        out.append((r.status_code, r.json() if r.content else None))
    return out


def test_runtime_serves_identically_over_another_dialect(
    registered: None, db: Path, bracket_db: Path
) -> None:
    src = source(rt.CANCEL + rt.LIST + rt.ADDRESSES)
    results = []
    for profile, path in (
        (api_profile(), db),
        (with_backend(api_profile(), "bracket"), bracket_db),
    ):
        check, app = serve_source(src, profile, path)
        assert app is not None, [d.render() for d in check.diagnostics]
        with TestClient(app) as client:
            results.append(_exchange(client, path))
    assert results[0] == results[1]
    assert rt.rows(db, "SELECT * FROM orders") == rt.rows(bracket_db, "SELECT * FROM orders")


def test_codegen_rejects_a_non_sqlite_plan(registered: None) -> None:
    check = check_source(source(rt.LIST), with_backend(api_profile(), "bracket"))
    assert check.program is not None
    with pytest.raises(LatticeInternalError, match="sqlite backend only"):
        generate_fastapi(lower_api(check.program))


# ---- P7: backend capabilities -----------------------------------------------------------------

TWO_WRITES = """
@endpoint("POST", "/addresses/{address_id}/move", auth=Customer)
def move(address_id: Int(ge=1), user: CurrentUser) -> NoContent | NotFound:
    rows = fetch(Addresses)
    mine = owned(rows, by=user)
    found = where(mine, col("id") == address_id)
    target = one(found, else_=NotFound("address"))
    copy = insert(Addresses, owner=user, line1="moved", city="X", zip="00000")
    gone = delete(target)
    return gone
"""

PAGE_TOTAL = """
@endpoint("GET", "/orders", auth=Customer)
def list_orders(user: CurrentUser) -> Page:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    result = page(mine, limit=10, offset=0, total=True)
    return result
"""

NATIVE = """
@endpoint("GET", "/orders", auth=Customer)
def list_orders(user: CurrentUser) -> Page:
    orders = fetch(Orders)
    shown = derive(orders, h=native.hash_password(col("status")))
    result = page(shown, limit=10)
    return result
"""

UPDATE_DERIVED = """
@endpoint("POST", "/orders/{order_id}/cancel", auth=Customer)
def cancel(order_id: Int(ge=1), user: CurrentUser) -> One | NotFound:
    orders = fetch(Orders)
    mine = owned(orders, by=user)
    found = where(mine, col("id") == order_id)
    order = one(found, else_=NotFound("order"))
    done = update(order, status="cancelled")
    shown = select(done, cols=["id", "status"])
    return shown
"""


def p7(src: str, backend_name: str) -> list[str]:
    """P7's diagnostic codes for `src` (parsed only: P7 runs on the AST)."""
    module = ast.parse(source(src))
    ctx = CheckContext(with_backend(api_profile(), backend_name))
    p7_backend.run(module, ctx)
    return [d.code for d in ctx.diagnostics]


PROGRAMS = {
    "two_writes": (TWO_WRITES, ["L870"]),
    "page_total": (PAGE_TOTAL, ["L871"]),
    "insert_returned": (rt.ADDRESSES, ["L872"]),  # the insert is returned; the delete not
    "update_returned_derived": (UPDATE_DERIVED, ["L872"]),
    "native": (NATIVE, ["L873"]),
    "plain_read": (rt.LIST, []),
    "update_returned": (rt.CANCEL, ["L872"]),
}


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_p7_rules_fire_without_the_capability(registered: None, name: str) -> None:
    src, codes = PROGRAMS[name]
    assert p7(src, "nocaps") == codes


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_p7_rules_never_fire_for_sqlite(name: str) -> None:
    src, _ = PROGRAMS[name]
    assert p7(src, "sqlite") == []


def test_p7_each_rule_needs_only_its_capability(
    registered: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = {
        "transactions": (TWO_WRITES, "L870"),
        "window_functions": (PAGE_TOTAL, "L871"),
        "returning": (rt.ADDRESSES, "L872"),
        "native_functions": (NATIVE, "L873"),
    }
    for cap, (src, code) in rules.items():
        caps = dataclasses.replace(SQLITE.capabilities, **{cap: False})
        one_off = type("OneOff", (SqliteDialect,), {"name": "oneoff", "capabilities": caps})()
        monkeypatch.setitem(DIALECTS, "oneoff", one_off)
        assert p7(src, "oneoff") == [code], cap
        # the other programs need other capabilities only
        for other, (osrc, _) in rules.items():
            if other != cap:
                assert p7(osrc, "oneoff") == [], (cap, other)


def test_p7_diagnostics_are_located_and_explained(registered: None) -> None:
    module = ast.parse(source(TWO_WRITES))
    ctx = CheckContext(with_backend(api_profile(), "nocaps"))
    p7_backend.run(module, ctx)
    (diag,) = [d for d in ctx.diagnostics if d.code == "L870"]
    assert diag.span.line == source(TWO_WRITES).splitlines().index("    gone = delete(target)") + 1
    assert "'nocaps' backend" in diag.message and diag.hint


def test_p7_rejects_a_checked_program(registered: None) -> None:
    result = check_source(source(rt.ADDRESSES), with_backend(api_profile(), "nocaps"))
    assert result.program is None
    assert [d.code for d in result.diagnostics if d.code.startswith("L87")] == ["L872"]


def test_p7_skips_tabular_programs_and_unknown_backends() -> None:
    ctx = CheckContext(PROFILE)
    p7_backend.run(ast.parse(source(rt.ADDRESSES)), ctx)
    assert ctx.diagnostics == []
    assert p7(rt.ADDRESSES, "no-such-backend") == []


def test_backend_module_exports() -> None:
    assert set(backend.__all__) >= {"Capabilities", "Dialect", "SQLITE", "DIALECTS", "dialect_for"}


def test_param_refs_bind_through_the_dialect() -> None:
    text = expr_sql(ast.Compare(ParamRef("x"), [ast.Eq()], [_e('col("id")')]), BRACKET)
    assert text == "(@p_x = [id])"


def test_real_app_features_compile_through_the_dialect(registered: None) -> None:
    """Uuid key generation, fn.now()/to_uuid/date/to_datetime, natives, page totals,
    UNSET updates, owner_is() and NativeToken auth leave no SQLite text either."""
    from lattice.api.runtime import auth_sql
    from tests.api import realapps

    profile = with_backend(realapps.PROFILE, "marker")
    check = check_source(realapps.PROGRAM, profile)
    assert check.program is not None, [d.render() for d in check.diagnostics]
    seen: set[str] = set()
    for plan in lower_api(check.program):
        if plan.auth != "Public":
            text = auth_sql(plan, MARKER)
            _assert_no_sqlite_text(text, f"{plan.name}/auth")
            assert "NATIVE_decode_token(%s)" in text
        for step in plan.steps:
            sql = getattr(step, "sql", None)
            if sql is not None and sql.text:
                _assert_no_sqlite_text(sql.text, f"{plan.name}/{step.id}")
                seen |= {
                    k
                    for k in (
                        "NEW_UUID()",
                        "NATIVE_",
                        "FN_to_uuid",
                        "FN_date(",
                        "FN_to_datetime",
                        "%(__now)s",
                        "CASE WHEN FLAG(",
                        "LEFT JOIN",
                        "ON TRUE",
                    )
                    if k in sql.text
                }
    assert seen == {
        "NEW_UUID()",
        "NATIVE_",
        "FN_to_uuid",
        "FN_date(",
        "FN_to_datetime",
        "%(__now)s",
        "CASE WHEN FLAG(",
        "LEFT JOIN",
        "ON TRUE",
    }
