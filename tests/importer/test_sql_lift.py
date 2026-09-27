"""lattice.importer.sql_lift (spec 2026-09-27 importer §3.6, S6): SQL back to Lattice
steps -- unit tests on the shapes SQLAlchemy emits, the constructs that are not lifted,
and the round trip through our own compiler on the realapps and template programs."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from bench.template_profile import template_profile, template_program
from lattice.api.ir import ReadStep, WriteStep, lower_api
from lattice.check import check_source
from lattice.importer.sql_lift import (
    LiftContext,
    Lifted,
    NotLiftable,
    Step,
    consolidate,
    lift_sql,
    lift_trace,
    parse_sql,
    program_steps,
    source_chain,
    step,
    with_total,
)
from lattice.profile import Profile
from tests.api.realapps import PROFILE as REALAPPS_PROFILE
from tests.api.realapps import PROGRAM as REALAPPS_PROGRAM

CALLER = uuid.UUID("8e80884a-4cb2-5dd2-9d4f-8e91a2da251a")
ITEM_ID = uuid.UUID("fe9a10a9-d1c1-519e-9d4f-f669bb77757f")
ITEM_COLS = ("title", "description", "id", "created_at", "owner_id")
USER_COLS = ("email", "is_active", "is_superuser", "full_name", "id", "hashed_password",
             "created_at")  # fmt: skip
CTX = LiftContext(
    tables={"item": "Item", "user": "User"},
    columns={"item": ITEM_COLS, "user": USER_COLS},
    owners={"item": "owner_id"},
    users_table="user",
    keys={"item": "id", "user": "id"},
    bindings={CALLER: "user", ITEM_ID: "id", 100: "limit", 0: "skip", "x": "title"},
)
FETCH_ITEM = step("fetch", "Item")
OWNED = step("owned", by="user")


def lifted(sql: str, *params: object, ctx: LiftContext = CTX) -> Lifted:
    out = lift_sql(sql, params, ctx)
    assert isinstance(out, Lifted), out
    return out


# ---- SQLAlchemy's shapes ------------------------------------------------------------------


def test_a_filtered_ordered_page():
    out = lifted(
        "SELECT item.title, item.description, item.id, item.created_at, item.owner_id \n"
        "FROM item \nWHERE item.owner_id = ? ORDER BY item.created_at DESC\n LIMIT ? OFFSET ?",
        CALLER.hex, 100, 0,
    )  # fmt: skip
    assert out.steps == (
        FETCH_ITEM,
        OWNED,
        step("sort", by=["created_at"], desc=True),
        step("page", limit="limit", offset="skip"),
    )
    assert out.kind == "read" and out.columns == ITEM_COLS
    assert out.render() == (
        "t0 = fetch(Item)\nt1 = owned(t0, by=user)\n"
        "t2 = sort(t1, by=['created_at'], desc=1)\nt3 = page(t2, limit=limit, offset=skip)"
    )


def test_a_count_folds_into_the_page_it_counts():
    count = lifted("SELECT count(*) AS count_1 \nFROM item \nWHERE item.owner_id = ?", CALLER.hex)
    assert count.kind == "count" and count.steps == (FETCH_ITEM, OWNED, step("count"))
    rows = lifted(
        "SELECT item.title FROM item WHERE item.owner_id = ? ORDER BY item.created_at DESC "
        "LIMIT ? OFFSET ?", CALLER.hex, 100, 0,
    )  # fmt: skip
    total = with_total(count, rows)
    assert total is not None
    assert total.steps[-1] == step("page", limit="limit", offset="skip", total=True)
    other = lifted("SELECT count(*) AS count_1 FROM item")
    assert with_total(other, rows) is None  # counts other rows


def test_a_lookup_by_key_and_the_callers_row():
    got = lifted(
        "SELECT item.title AS item_title, item.id AS item_id FROM item WHERE item.id = ?",
        ITEM_ID.hex,
    )
    # SQLAlchemy's labels ("item_title") are the column names; two of five is a projection
    assert got.steps == (
        FETCH_ITEM, step("where", "col('id') == id"), step("select", cols=["title", "id"])
    )  # fmt: skip
    assert got.columns == ("title", "id")
    me = lifted("SELECT user.email AS user_email FROM user WHERE user.id = ?", CALLER.hex)
    assert me.steps == (Step("user"), step("select", cols=["email"]))


def test_writes():
    update = lifted("UPDATE item SET title=? WHERE item.id = ?", "x", ITEM_ID.hex)
    assert update.steps == (FETCH_ITEM, step("where", "col('id') == id"),
                            step("update", title="title"))  # fmt: skip
    me = lifted("UPDATE user SET email=? WHERE user.id = ?", "a@b.c", CALLER.hex)
    assert me.steps == (Step("user"), step("update", email="'a@b.c'"))
    delete = lifted("DELETE FROM item WHERE item.id = ?", ITEM_ID.hex)
    assert delete.steps == (FETCH_ITEM, step("where", "col('id') == id"), step("delete"))
    insert = lifted(
        "INSERT INTO item (title, description, id, created_at, owner_id) VALUES (?, ?, ?, ?, ?)",
        "x", None, uuid.uuid4().hex, "2026-09-27 14:13:27.407808", CALLER.hex,
    )  # fmt: skip
    assert insert.steps == (
        step("insert", "Item", title="title", description="None", created_at="fn.now()",
             owner="user"),
    )  # fmt: skip
    assert len(insert.notes) == 2  # the generated key and the timestamp are assumptions


def test_joins_derived_columns_and_limits():
    bad = lift_sql(
        "SELECT item.title, user.email, item.title || '!' AS loud FROM item "
        "JOIN user ON user.id = item.owner_id", (), CTX,
    )  # fmt: skip
    assert isinstance(bad, NotLiftable) and bad.construct == "operator ||"
    joined = lifted(
        "SELECT item.title, user.email, lower(user.email) AS e FROM item "
        "JOIN user ON user.id = item.owner_id WHERE user.email = ? LIMIT 3",
        "a@b.c",
    )
    assert joined.steps == (
        FETCH_ITEM,
        step("join", "fetch(User)", on="{'owner_id': 'id'}"),
        step("where", "col('email') == 'a@b.c'"),
        step("derive", e="fn.lower(col('email'))"),
        step("select", cols=["title", "email", "e"]),
        step("limit", n=3),
    )
    left = lifted("SELECT a.x FROM a LEFT OUTER JOIN b ON a.k = b.k", ctx=LiftContext())
    assert left.steps[1] == step("join", "fetch(B)", on="['k']", how="'left'")


def test_ascending_sorts_note_the_null_order():
    out = lifted("SELECT item.title FROM item ORDER BY item.title, item.id DESC")
    assert out.steps[-1] == step("sort", by=["title", "id"], desc="[0, 1]")
    assert any("NULLs first" in n for n in out.notes)


def test_a_param_on_the_left_is_flipped():
    out = lifted("SELECT item.title FROM item WHERE ? < item.created_at", "2026-01-01 00:00:00")
    assert out.steps[1] == step("where", "col('created_at') > '2026-01-01 00:00:00'")


@pytest.mark.parametrize(
    ("sql", "construct"),
    [
        ("SELECT item.owner_id, count(*) FROM item GROUP BY item.owner_id", "GROUP BY"),
        ("SELECT DISTINCT item.owner_id FROM item", "DISTINCT"),
        ("SELECT item.id FROM item UNION SELECT user.id FROM user", "UNION"),
        ("SELECT item.id FROM item WHERE item.id IN (?, ?)", "IN"),
        ("SELECT item.id FROM item WHERE item.title LIKE ?", "operator LIKE"),
        ("SELECT item.id FROM item WHERE EXISTS (SELECT 1 FROM user)", "EXISTS"),
        ("SELECT CASE WHEN item.title = ? THEN 1 ELSE 0 END AS c FROM item", "CASE"),
        ("SELECT rank() OVER (ORDER BY item.title) AS r FROM item", "window function"),
        ("SELECT item.id FROM item, user", "comma join"),
        ("SELECT item.id FROM item RIGHT OUTER JOIN user ON user.id = item.owner_id",
         "RIGHT JOIN"),
        ("SELECT item.id FROM item WHERE json_valid(item.title)", "function json_valid()"),
        ("INSERT INTO item (title) VALUES (?), (?)", "multi-row INSERT"),
        ("INSERT INTO item (title) SELECT user.email FROM user", "INSERT ... SELECT"),
        ("PRAGMA table_info(item)", "syntax"),
    ],
)  # fmt: skip
def test_what_is_not_lifted(sql, construct):
    out = lift_sql(sql, ("a", "b"), CTX)
    assert isinstance(out, NotLiftable), out
    assert out.construct == construct and out.reason


def test_the_parser_reads_the_compilers_ctes():
    stmt = parse_sql(
        'WITH t0 AS (SELECT "a", "id" AS __ord FROM "x"), t1 AS (SELECT "a", __ord FROM t0 '
        'WHERE ("a" = :p_a)) SELECT "a" FROM t1 ORDER BY __ord'
    )
    assert [n for n, _ in stmt.ctes] == ["t0", "t1"]  # type: ignore[union-attr]


def test_consolidate_keeps_the_owner_filter_over_the_admin_variant():
    rows = lambda *s: Lifted("read", (FETCH_ITEM, *s))  # noqa: E731
    page = step("page", limit="limit", offset="skip")
    admin, user = rows(page), rows(OWNED, page)
    assert consolidate([admin, user, user]) == [user]
    count = Lifted("count", (FETCH_ITEM, OWNED, step("count")))
    total = step("page", limit="limit", offset="skip", total=True)
    assert consolidate([count, user]) == [rows(OWNED, total)]


# ---- the round trip through our compiler --------------------------------------------------

PROGRAMS = {
    "realapps": (REALAPPS_PROGRAM, REALAPPS_PROFILE),
    "template": (template_program(), template_profile()),
}


def lift_plans(source: str, profile: Profile) -> list[tuple[str, str, object, object]]:
    """(endpoint, step id, lifted, expected) for every statement the compiled plans run."""
    checked = check_source(source, profile)
    assert checked.program is not None
    api = profile.api
    assert api is not None
    tables = {api.table_names.get(c, c): c for c in api.tables}
    owners = {api.table_names.get(c, c): s.owner for c, s in api.tables.items() if s.owner}
    programs = program_steps(source)
    out = []
    for plan in lower_api(checked.program):
        done: list[str] = []
        for st in plan.steps:
            if not isinstance(st, ReadStep | WriteStep):
                continue
            ctx = LiftContext(
                tables=tables,
                owners=owners,
                binds=dict(st.sql.binds),
                rows={bind: sid for bind, sid, _ in st.sql.rows},
            )
            got = lift_sql(st.sql.text, None, ctx)
            chain, checks = source_chain(programs[plan.name], st.id, done)
            flagged = {c.id for c in plan.steps if getattr(c, "flag_step", None) == st.id}
            out.append((plan.name, st.id, got, (chain, checks if flagged else ())))
            done.append(st.id)
    return out


@pytest.mark.parametrize("program", sorted(PROGRAMS))
def test_our_compilers_sql_lifts_back_to_the_program(program):
    cases = lift_plans(*PROGRAMS[program])
    assert len(cases) >= 10 if program == "template" else len(cases) >= 20
    for name, sid, got, (chain, checks) in cases:
        assert isinstance(got, Lifted), (name, sid, got)
        assert got.steps == chain, (name, sid, got.render(), chain)
        assert set(got.checks) == set(checks), (name, sid, got.checks, checks)


def test_the_round_trip_covers_every_statement_shape():
    ops = {op for p in PROGRAMS.values() for *_, got, _ in lift_plans(*p)
           for op in got.ops}  # type: ignore[attr-defined]  # fmt: skip
    assert ops == {
        "fetch", "rows", "user", "where", "owned", "derive", "select", "sort", "page",
        "insert", "update", "delete",
    }  # fmt: skip


def test_a_trace_marks_the_refresh_of_a_written_row():
    new = uuid.uuid4().hex
    got = lift_trace(
        [
            ("INSERT INTO item (title, id, owner_id) VALUES (?, ?, ?)", ("x", new, CALLER.hex)),
            ("SELECT item.title FROM item WHERE item.id = ?", (new,)),
            ("SELECT item.title FROM item WHERE item.id = ?", (ITEM_ID.hex,)),
        ],
        CTX,
    )
    assert [x.kind for x in got] == ["insert", "refresh", "read"]  # type: ignore[union-attr]


def test_consolidate_votes_per_statement_shape():
    sql = "SELECT item.title FROM item LIMIT ? OFFSET ?"
    good = replace(lifted(sql, 100, 0), sql=sql)
    clash = replace(lifted(sql, 1, 1, ctx=replace(CTX, bindings={1: "limit"})), sql=sql)
    assert clash.steps[-1] == step("page", limit="limit", offset="limit")
    assert consolidate([good, clash, good]) == [good]
