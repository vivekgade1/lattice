"""N1b — single-meaning spellings beyond the syntactic table (spec 2026-09-27
lattice-surface-design §1): named column expressions (macros), values read from a
one-row table in writes and derives, and the typed null `when(..., otherwise=None)`.
`one(t)` without `else_=` stays refused (its 404 detail is app-specific), with a
concrete hint. Each accepted spelling keeps its guarantee: see the negative tests."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent

import pytest
from starlette.testclient import TestClient

from lattice.api.ir import ReadStep, WriteStep, lower_api
from lattice.api.runtime import build_app
from lattice.check import CheckResult, check_source
from lattice.diagnostics import Diagnostic, Severity, Span
from lattice.profile import Profile
from lattice.typesys import TableT
from tests.api import n1b
from tests.api.n1b import NATIVES, PROFILE, PROGRAM, TOKENS, uid
from tests.check.test_normalise import TABULAR, _fingerprint, _ok, _tab

ALPHA, BETA, GAMMA = uid(101), uid(102), uid(103)


def _check(src: str, profile: Profile = PROFILE) -> CheckResult:
    return check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), profile)


def _errors(src: str, profile: Profile = PROFILE) -> list[Diagnostic]:
    return [d for d in _check(src, profile).diagnostics if d.severity is Severity.ERROR]


def _codes(src: str, profile: Profile = PROFILE) -> list[str]:
    return [d.code for d in _errors(src, profile)]


def _endpoint(body: str, returns: str = "Created | NotFound | Forbidden") -> str:
    """A POST endpoint over one of the caller's items; `body` ends in `return ...`."""
    return (
        '@endpoint("POST", "/items/{id}/x", auth=Customer)\n'
        f"def x(id: Uuid, user: CurrentUser) -> {returns}:\n"
        "    items = fetch(Item)\n"
        "    mine = owned(items, by=user)\n"
        '    found = where(mine, col("id") == id)\n'
        '    item = one(found, else_=NotFound("Item not found"))\n'
        + dedent(body)
        .strip("\n")
        .replace("\n", "\n    ")
        .join(["    ", "\n"])
    )


# ---- 1. named column expressions -------------------------------------------------------------


def test_named_column_expression_is_substituted_at_its_use():
    spelled = _tab('big = col("qty") > 2\nout = where(orders, big)\n')
    canonical = _tab('out = where(orders, col("qty") > 2)\n')
    a, b = _ok(spelled, TABULAR), _ok(canonical, TABULAR)
    assert a.program is not None and b.program is not None
    from lattice.ir import lower

    assert _fingerprint(lower(a.program)) == _fingerprint(lower(b.program))
    assert {k for k, _ in a.normalisations} == {"column_expr"}


def test_named_column_expressions_nest_and_serve_several_uses():
    spelled = _tab(
        'big = col("qty") > 2\n'
        'eu = fn.lower(col("region")) == "eu"\n'
        "both = big & eu\n"
        "some = where(orders, both)\n"
        "out = derive(some, flag=both, again=big)\n"
    )
    canonical = _tab(
        'some = where(orders, (col("qty") > 2) & (fn.lower(col("region")) == "eu"))\n'
        'out = derive(some, flag=(col("qty") > 2) & (fn.lower(col("region")) == "eu"), '
        'again=col("qty") > 2)\n'
    )
    a, b = _ok(spelled, TABULAR), _ok(canonical, TABULAR)
    assert a.program is not None and b.program is not None
    from lattice.ir import lower

    assert _fingerprint(lower(a.program)) == _fingerprint(lower(b.program))


def test_named_column_expression_is_typed_at_the_use():
    r = check_source(_tab('n = col("qty") + 1\nout = where(orders, n)\n'), TABULAR)
    errors = [d for d in r.diagnostics if d.severity is Severity.ERROR]
    assert [d.code for d in errors] == ["L403"]
    assert errors[0].span == Span(4, 21)  # the use, not the definition
    assert "'n' is defined at line 3" in errors[0].message


def test_named_column_expression_unknown_column_is_l401_at_the_use_naming_the_definition():
    src = _tab(
        'customers = load("customers.csv", schema=Customers)\n'
        'rich = col("price") > 10\n'
        "some = where(customers, rich)\n"
        "out = where(orders, rich)\n"
    )
    r = check_source(src, TABULAR)
    errors = [d for d in r.diagnostics if d.severity is Severity.ERROR]
    assert [d.code for d in errors] == ["L401"]
    assert errors[0].span == Span(5, 25)  # where(customers, rich): Customers has no price
    assert "unknown column 'price'" in errors[0].message
    assert "'rich' is defined at line 4" in errors[0].message


def test_named_column_expression_rebinding_is_l201():
    src = _tab('big = col("qty") > 2\nbig = col("qty") > 3\nout = where(orders, big)\n')
    codes = [d.code for d in check_source(src, TABULAR).diagnostics]
    assert "L201" in codes


def test_unused_named_column_expression_is_still_refused():
    src = _tab('big = col("qty") > 2\nout = where(orders, col("qty") > 1)\n')
    codes = [d.code for d in check_source(src, TABULAR).diagnostics if d.severity == "error"]
    assert codes == ["L302"]


def test_named_column_expression_in_an_endpoint():
    r = _check(
        """
        @endpoint("GET", "/items/", auth=Customer)
        def read_items(user: CurrentUser) -> Page:
            items = fetch(Item)
            mine = owned(items, by=user)
            live = is_null(col("deleted_at")) and mine.title != ""
            found = where(mine, live)
            ordered = sort(found, by=["id"])
            shown = select(ordered, cols=["id", "title"])
            result = page(shown, limit=10)
            return result
        """
    )
    assert r.ok, [d.render() for d in r.diagnostics]
    assert {"column_expr", "bool_op", "attribute"} <= {k for k, _ in r.normalisations}


# ---- 2. values of a one-row table in writes and derives --------------------------------------


def test_fixture_program_checks():
    r = check_source(PROGRAM, PROFILE)
    assert r.ok, [d.render() for d in r.diagnostics]


def test_row_value_in_insert_compiles_to_a_scalar_subquery():
    r = check_source(PROGRAM, PROFILE)
    assert r.program is not None
    plan = next(p for p in lower_api(r.program) if p.name == "duplicate_item")
    write = next(s for s in plan.steps if isinstance(s, WriteStep))
    assert write.sql.rows, write.sql.text  # reads the checked row's materialized step
    assert "SELECT" in write.sql.text and "INSERT" in write.sql.text


def test_row_value_in_derive_compiles_to_a_scalar_subquery():
    r = check_source(PROGRAM, PROFILE)
    assert r.program is not None
    plan = next(p for p in lower_api(r.program) if p.name == "around")
    reads = [s for s in plan.steps if isinstance(s, ReadStep)]
    assert reads[-1].sql.rows, reads[-1].sql.text


def test_row_value_from_rows_that_may_be_many_is_refused():
    body = "copied = insert(Item, owner=user, title=found.title)\nreturn copied"
    errors = _errors(_endpoint(body))
    assert [d.code for d in errors] == ["L404"]
    assert "exactly one row" in errors[0].message
    assert "one(found" in (errors[0].hint or "")


def test_row_value_from_rows_without_an_ownership_check_is_l802():
    src = """
    @endpoint("POST", "/items/{id}/x", auth=Customer)
    def x(id: Uuid, user: CurrentUser) -> Created | NotFound:
        items = fetch(Item)
        found = where(items, col("id") == id)
        item = one(found, else_=NotFound("Item not found"))
        copied = insert(Item, owner=user, title=item.title)
        return copied
    """
    assert _codes(src) == ["L802"]


def test_row_value_of_a_private_column_is_l803_outside_admin():
    src = """
    @endpoint("POST", "/items/", auth=Customer)
    def x(user: CurrentUser) -> Created | NotFound:
        users = fetch(User)
        found = where(users, col("id") == user.id)
        me = one(found, else_=NotFound("User not found"))
        copied = insert(Item, owner=user, title=me.hashed_password)
        return copied
    """
    errors = _errors(src)
    assert [d.code for d in errors] == ["L803"]
    assert "hashed_password" in errors[0].message
    admin = src.replace("auth=Customer", "auth=Admin").replace("owner=user, ", "owner_id=user.id, ")
    assert "L803" not in _codes(admin)


def test_row_value_unknown_column_is_l401():
    errors = _errors(_endpoint("copied = insert(Item, owner=user, title=item.nope)\nreturn copied"))
    assert [d.code for d in errors] == ["L401"]
    assert "nope" in errors[0].message


def test_row_value_type_is_checked_against_the_column():
    errors = _errors(
        _endpoint("copied = insert(Item, owner=user, title=item.created_at)\nreturn copied")
    )
    assert [d.code for d in errors] == ["L402"]


def test_row_value_of_a_nullable_column_into_a_required_one_is_l410():
    errors = _errors(
        _endpoint("copied = insert(Item, owner=user, title=item.description)\nreturn copied")
    )
    assert [d.code for d in errors] == ["L410"]


def test_row_value_outside_writes_and_derives_is_still_l106():
    errors = _errors(
        _endpoint(
            'other = where(mine, col("title") == item.title)\n'
            'first = one(other, else_=NotFound("x"))\nreturn first',
            "One | NotFound",
        )
    )
    assert [d.code for d in errors] == ["L106"]


def test_row_value_of_a_scalar_is_still_refused():
    src = _endpoint("n = 3\ncopied = insert(Item, owner=user, title=n.real)\nreturn copied")
    assert _codes(src)  # not a table: refused, never read as a column


# ---- 3. when(..., otherwise=None): a typed null ---------------------------------------------


def test_when_with_a_none_branch_is_nullable():
    r = check_source(
        _tab('out = derive(orders, big=when(col("qty") > 2, then=col("price"), otherwise=None))\n'),
        TABULAR,
    )
    assert r.program is not None, [d.render() for d in r.diagnostics]
    derive = r.program.module.body[1].value
    t = r.program.types[id(derive)]
    assert isinstance(t, TableT) and "big" in t.nullable


def test_when_none_into_a_required_column_is_l410_at_the_use():
    errors = _errors(
        _endpoint(
            "done = update(item, title=when(is_null(col(\"description\")), then=None, "
            'otherwise="x"))\nreturn done',
            "One | NotFound",
        )
    )
    assert [d.code for d in errors] == ["L410"]
    assert "title" in errors[0].message


def test_when_none_into_a_nullable_column_is_accepted():
    r = _check(
        _endpoint(
            'done = update(item, description=when(col("title") == "", then=None, '
            'otherwise=col("title")))\nreturn done',
            "One | NotFound",
        )
    )
    assert r.ok, [d.render() for d in r.diagnostics]


def test_when_with_two_none_branches_is_l410():
    errors = _errors(
        _endpoint(
            "done = update(item, description=when(is_null(col(\"description\")), then=None, "
            "otherwise=None))\nreturn done",
            "One | NotFound",
        )
    )
    assert [d.code for d in errors] == ["L410"]


def test_counting_a_when_with_a_none_branch():
    r = check_source(
        _tab(
            'out = group(orders, by=["region"], '
            'big=agg.count(when(col("qty") > 2, then=col("order_id"), otherwise=None)))\n'
        ),
        TABULAR,
    )
    assert r.ok, [d.render() for d in r.diagnostics]


def test_if_expression_with_none_is_a_typed_null():
    r = check_source(
        _tab('out = derive(orders, big=col("price") if col("qty") > 2 else None)\n'), TABULAR
    )
    assert r.ok, [d.render() for d in r.diagnostics]


# ---- 4. one(t) without else_= stays refused, with a concrete hint ----------------------------


def test_one_without_else_is_refused_with_the_tables_404():
    errors = _errors(
        """
        @endpoint("GET", "/items/{id}", auth=Customer)
        def get_item(id: Uuid, user: CurrentUser) -> One:
            items = fetch(Item)
            mine = owned(items, by=user)
            found = where(mine, col("id") == id)
            item = one(found)
            return item
        """
    )
    assert [d.code for d in errors] == ["L303"]
    hint = errors[0].hint or ""
    assert 'else_=NotFound("Item not found")' in hint
    assert "-> One | NotFound" in hint


def test_one_without_else_hint_when_notfound_is_declared():
    errors = _errors(
        """
        @endpoint("GET", "/items/{id}", auth=Customer)
        def get_item(id: Uuid, user: CurrentUser) -> One | NotFound:
            items = fetch(Item)
            mine = owned(items, by=user)
            found = where(mine, col("id") == id)
            item = one(found)
            return item
        """
    )
    hint = errors[0].hint or ""
    assert 'else_=NotFound("Item not found")' in hint and "->" not in hint


# ---- the fixture program at runtime ----------------------------------------------------------


def _h(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


@pytest.fixture(scope="module")
def pristine(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("n1b") / "pristine.db"
    n1b.build_db(path)
    return path


@pytest.fixture
def client(pristine: Path, tmp_path: Path) -> Iterator[TestClient]:
    path = tmp_path / "app.db"
    shutil.copyfile(pristine, path)
    r = check_source(PROGRAM, PROFILE)
    assert r.program is not None
    with TestClient(build_app(lower_api(r.program), path, natives=NATIVES)) as c:
        yield c


def test_runtime_duplicate_copies_the_checked_row(client: TestClient):
    resp = client.post(f"/items/{ALPHA}/duplicate", headers=_h("alice"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Alpha (copy)"
    assert body["description"] == "first"
    assert body["created_at"].startswith("2000-01-05T10:00:00")
    assert body["id"] != ALPHA
    beta = client.post(f"/items/{BETA}/duplicate", headers=_h("alice")).json()
    assert beta["title"] == "Beta (copy)" and beta["description"] is None
    assert client.post(f"/items/{GAMMA}/duplicate", headers=_h("alice")).status_code == 403
    assert client.post(f"/items/{uid(999)}/duplicate", headers=_h("alice")).status_code == 404


def test_runtime_update_from_another_row(client: TestClient):
    resp = client.put(f"/items/{ALPHA}/title-from/{BETA}", headers=_h("alice"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "Beta" and resp.json()["description"] is None
    resp = client.put(f"/items/{BETA}/title-from/{uid(104)}", headers=_h("alice"))
    assert resp.json()["title"] == "Delta" and resp.json()["description"] == "fourth"
    missing = client.put(f"/items/{ALPHA}/title-from/{GAMMA}", headers=_h("alice"))
    assert missing.status_code == 404 and missing.json()["detail"] == "Source not found"


def test_runtime_derive_reads_the_anchor_row(client: TestClient):
    resp = client.get(f"/items/{ALPHA}/around", headers=_h("alice"))
    assert resp.status_code == 200, resp.text
    rows = {r["id"]: r for r in resp.json()["data"]}
    assert {r["anchor_title"] for r in rows.values()} == {"Alpha"}
    assert rows[ALPHA]["other_title"] is None and rows[BETA]["other_title"] == "Beta"
    assert rows[BETA]["days"] == -2 and rows[ALPHA]["days"] == 0


def test_runtime_when_none_writes_a_null(client: TestClient):
    cleared = client.post(f"/items/{ALPHA}/stamp", headers=_h("alice"), json={"clear": True})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["created_at"] is None
    stamped = client.post(f"/items/{ALPHA}/stamp", headers=_h("alice"), json={"clear": False})
    assert stamped.json()["created_at"] is not None
