"""pycheck's six guarantee checks on the synthetic ``demo`` app (tests/pycheck/conftest.py):
a positive and a negative case per check, the policy derived from the original app, and
the original itself checking clean."""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice.pycheck import Finding, Policy, derive_policy, run_pycheck


def line_of(app: Path, rel: str, text: str) -> int:
    lines = (app / rel).read_text().splitlines()
    hits = [i for i, line in enumerate(lines, 1) if text in line]
    assert len(hits) == 1, (text, hits)
    return hits[0]


@pytest.fixture
def policy(demo_app) -> Policy:
    return derive_policy(demo_app(), "demo")


def check(demo_app, policy: Policy, extra: str = "", **replaced: str) -> list[Finding]:
    return run_pycheck(demo_app(extra, **replaced), policy)


def codes(findings: list[Finding]) -> list[str]:
    return [f.code for f in findings]


# ---- the policy and the original ----------------------------------------------------------


def test_policy_is_derived_from_the_original(policy):
    assert policy.package == "demo"
    assert policy.users_table == "User"
    assert dict(policy.owners) == {"Item": "owner_id"}
    assert dict(policy.private) == {"User": ("hashed_password",)}
    assert policy.admin is not None
    assert (policy.admin.column, policy.admin.dependency) == (
        "is_superuser",
        "get_current_active_superuser",
    )
    assert (policy.admin.status, policy.admin.detail) == (
        403,
        "The user doesn't have enough privileges",
    )
    assert policy.not_owned["Item"].status == 403
    assert policy.not_owned["Item"].detail == "Not enough permissions"
    assert policy.naive_datetimes  # SQLite
    # the original's own unbounded `limit` is the app's convention: baselined
    assert "PY803|limit|limit" in policy.baseline


def test_the_original_app_has_no_findings(demo_app, policy):
    assert run_pycheck(demo_app(), policy) == []


def test_an_unparseable_file_is_one_finding(demo_app, policy):
    found = check(demo_app, policy, "def broken(:\n    pass\n")
    assert codes(found) == ["PY800"]
    assert found[0].file == "routes.py"


def test_findings_render_with_file_line_and_hint(demo_app, policy):
    app = demo_app(
        '@router.get("/mine/all")\ndef all_items(session: SessionDep, '
        "current_user: CurrentUser) -> Any:\n"
        "    return session.exec(select(Item)).all()\n"
    )
    (f,) = run_pycheck(app, policy)
    text = f.render()
    assert text.startswith(f"routes.py:{f.line}: PY801 ")
    assert "fix:" in text


# ---- PY801 ownership ------------------------------------------------------------------------

UNSCOPED = """\
@router.get("/mine/titles")
def item_titles(session: SessionDep, current_user: CurrentUser) -> Any:
    items = session.exec(select(Item)).all()
    return [i.title for i in items]
"""


def test_py801_flags_an_owned_table_read_without_the_caller(demo_app, policy):
    app = demo_app(UNSCOPED)
    (f,) = run_pycheck(app, policy)
    assert f.code == "PY801" and f.file == "routes.py"
    assert f.line == line_of(app, "routes.py", "def item_titles")
    assert "Item" in f.message
    assert ".where(Item.owner_id == current_user.id)" in f.hint
    assert "current_user.is_superuser" in f.hint


@pytest.mark.parametrize(
    "scoped",
    [
        # filtered by the caller
        UNSCOPED.replace("select(Item)", "select(Item).where(Item.owner_id == current_user.id)"),
        # through an alias of the caller's id
        UNSCOPED.replace(
            "    items = session.exec(select(Item)).all()\n",
            "    uid = current_user.id\n"
            "    items = session.exec(select(Item).where(Item.owner_id == uid)).all()\n",
        ),
        # an explicit admin guard
        UNSCOPED.replace(
            "    items = ",
            "    if not current_user.is_superuser:\n"
            "        raise HTTPException(status_code=403, detail=\"The user doesn't have "
            'enough privileges")\n    items = ',
        ),
        # the admin dependency
        UNSCOPED.replace(
            '@router.get("/mine/titles")',
            '@router.get("/mine/titles", dependencies=[Depends(get_current_active_superuser)])',
        ),
    ],
)
def test_py801_accepts_a_caller_filter_or_an_admin_check(demo_app, policy, scoped):
    assert check(demo_app, policy, scoped) == []


def test_py801_ignores_an_owner_filter_in_dead_code(demo_app, policy):
    dead = UNSCOPED.replace(
        "    items = session.exec(select(Item)).all()\n",
        "    statement = select(Item)\n"
        "    if False:\n"
        "        statement = statement.where(Item.owner_id == current_user.id)\n"
        "    items = session.exec(statement).all()\n",
    )
    assert codes(check(demo_app, policy, dead)) == ["PY801"]


def test_py801_flags_an_insert_not_owned_by_the_caller(demo_app, policy):
    src = """\
@router.post("/for/{owner}")
def create_for(session: SessionDep, current_user: CurrentUser, owner: int,
               item_in: ItemCreate) -> Any:
    item = Item.model_validate(item_in, update={"owner_id": owner})
    session.add(item)
    session.commit()
    return item
"""
    assert codes(check(demo_app, policy, src)) == ["PY801"]


DEACTIVATE = """\
@router.post("/users/{user_id}/deactivate", response_model=UserPublic)
def deactivate(session: SessionDep, current_user: CurrentUser, user_id: int) -> Any:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    session.add(user)
    session.commit()
    return user
"""


def test_py801_flags_another_users_account_written_without_an_admin_check(demo_app, policy):
    app = demo_app(DEACTIVATE)
    (f,) = run_pycheck(app, policy)
    assert f.code == "PY801"
    assert f.line == line_of(app, "routes.py", "user.is_active = False")
    assert "get_current_active_superuser" in f.hint


def test_py801_accepts_an_account_write_by_an_admin_or_the_owner(demo_app, policy):
    admin = DEACTIVATE.replace(
        "response_model=UserPublic)",
        "response_model=UserPublic,\n"
        "             dependencies=[Depends(get_current_active_superuser)])",
    )
    own = DEACTIVATE.replace(
        "    user.is_active = False\n",
        "    if user.id != current_user.id:\n"
        '        raise HTTPException(status_code=403, detail="Not enough permissions")\n'
        "    user.is_active = False\n",
    )
    assert check(demo_app, policy, admin) == []
    assert check(demo_app, policy, own) == []


# ---- PY802 private columns -------------------------------------------------------------------


def test_py802_flags_a_private_column_in_a_response(demo_app, policy):
    table_response = """\
@router.get("/users/{user_id}/full", response_model=User)
def read_full(session: SessionDep, current_user: CurrentUser, user_id: int) -> Any:
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return session.get(User, user_id)
"""
    in_dict = """\
@router.get("/users/me/debug")
def debug_me(current_user: CurrentUser) -> Any:
    return {"email": current_user.email, "hash": current_user.hashed_password}
"""
    app = demo_app(table_response)
    (f,) = run_pycheck(app, policy)
    assert f.code == "PY802" and "hashed_password" in f.message
    assert "UserPublic" in f.hint
    (g,) = check(demo_app, policy, in_dict)
    assert g.code == "PY802" and "hashed_password" in g.message


def test_py802_accepts_public_models_and_admin_routes(demo_app, policy):
    public = """\
@router.get("/users/me/again", response_model=UserPublic)
def me_again(current_user: CurrentUser) -> Any:
    return current_user
"""
    admin = """\
@router.get("/users/{user_id}/full", response_model=User,
            dependencies=[Depends(get_current_active_superuser)])
def read_full(session: SessionDep, user_id: int) -> Any:
    return session.get(User, user_id)
"""
    assert check(demo_app, policy, public) == []
    assert check(demo_app, policy, admin) == []


# ---- PY803 unbounded inputs ------------------------------------------------------------------

LATEST = """\
@router.get("/mine/latest")
def latest(session: SessionDep, current_user: CurrentUser, n: int = 10) -> Any:
    statement = select(Item).where(Item.owner_id == current_user.id).limit(n)
    return session.exec(statement).all()
"""

RENAME = """\
class Rename(SQLModel):
    title: str


@router.patch("/{id}/title", response_model=ItemPublic)
def rename(session: SessionDep, current_user: CurrentUser, id: int, body: Rename) -> Any:
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    item.title = body.title.strip()
    session.add(item)
    session.commit()
    return item
""".replace("class Rename", "from sqlmodel import SQLModel\n\n\nclass Rename")


def test_py803_flags_an_unbounded_limit(demo_app, policy):
    app = demo_app(LATEST)
    (f,) = run_pycheck(app, policy)
    assert f.code == "PY803" and "'n'" in f.message
    assert "Query(ge=1, le=100)" in f.hint


def test_py803_accepts_a_bounded_limit(demo_app, policy):
    bounded = LATEST.replace("n: int = 10", "n: Annotated[int, Query(ge=1, le=100)] = 10").replace(
        "@router", "from typing import Annotated\n\nfrom fastapi import Query\n\n\n@router"
    )
    clamped = LATEST.replace(".limit(n)", ".limit(min(n, 100))")
    assert check(demo_app, policy, bounded) == []
    assert check(demo_app, policy, clamped) == []


def test_py803_flags_an_unbounded_body_string_written_to_the_database(demo_app, policy):
    app = demo_app(RENAME)
    (f,) = run_pycheck(app, policy)
    assert f.code == "PY803" and "title" in f.message
    assert "Field(max_length=" in f.hint


def test_py803_accepts_a_bounded_body_string(demo_app, policy):
    bounded = RENAME.replace("    title: str\n", "    title: str = Field(max_length=100)\n")
    bounded = bounded.replace(
        "from sqlmodel import SQLModel", "from sqlmodel import Field, SQLModel"
    )
    assert check(demo_app, policy, bounded) == []


def test_py803_flags_an_unbounded_query_string_written_to_the_database(demo_app, policy):
    src = RENAME.replace("body: Rename", "title: str").replace("body.title", "title")
    assert codes(check(demo_app, policy, src)) == ["PY803"]


# ---- PY804 route shadowing -------------------------------------------------------------------

STATS = """\
@router.get("/stats")
def item_stats(session: SessionDep, current_user: CurrentUser) -> Any:
    items = session.exec(select(Item).where(Item.owner_id == current_user.id)).all()
    return {"count": len(items)}
"""


def test_py804_flags_a_literal_route_after_a_parameter_route(demo_app, policy):
    app = demo_app(STATS)
    (f,) = run_pycheck(app, policy)
    assert f.code == "PY804"
    assert f.line == line_of(app, "routes.py", '@router.get("/stats")')
    assert "read_item" in f.message and "/items/{id}" in f.message
    assert "before" in f.hint


def test_py804_accepts_a_literal_route_declared_first(demo_app, policy):
    from pycheck_demo import ROUTES

    anchor = '@router.get("/{id}", response_model=ItemPublic)'
    routes = ROUTES.replace(anchor, STATS + "\n\n" + anchor)
    other_method = STATS.replace("@router.get", "@router.delete")
    assert check(demo_app, policy, routes=routes) == []
    assert codes(check(demo_app, policy, other_method)) == []


# ---- PY805 error conventions -----------------------------------------------------------------


def test_py805_flags_another_status_for_another_users_row(demo_app, policy):
    src = """\
@router.get("/{id}/title")
def item_title(session: SessionDep, current_user: CurrentUser, id: int) -> Any:
    item = session.get(Item, id)
    if not item or item.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"title": item.title}
"""
    app = demo_app(src)
    (f,) = run_pycheck(app, policy)
    assert f.code == "PY805"
    assert f.line == line_of(app, "routes.py", "if not item or item.owner_id")
    assert '403 "Not enough permissions"' in f.hint


def test_py805_flags_another_admin_error(demo_app, policy):
    src = UNSCOPED.replace(
        "    items = ",
        "    if not current_user.is_superuser:\n"
        '        raise HTTPException(status_code=403, detail="Admins only")\n    items = ',
    )
    (f,) = check(demo_app, policy, src)
    assert f.code == "PY805"
    assert "get_current_active_superuser" in f.hint


def test_py805_accepts_the_apps_own_answers(demo_app, policy):
    src = """\
@router.get("/{id}/title")
def item_title(session: SessionDep, current_user: CurrentUser, id: int) -> Any:
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and item.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return {"title": item.title}
"""
    assert check(demo_app, policy, src) == []


# ---- PY806 naive / aware datetimes -----------------------------------------------------------

FRESH = """\
from datetime import UTC, datetime, timedelta


@router.get("/{id}/fresh")
def is_fresh(session: SessionDep, current_user: CurrentUser, id: int) -> Any:
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and item.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    created = item.created_at
    cutoff = datetime.now(UTC) - timedelta(days=1)
    return {"fresh": created is not None and created >= cutoff}
"""

SINCE = """\
from datetime import UTC, datetime


@router.get("/mine/since")
def items_since(session: SessionDep, current_user: CurrentUser, since: datetime) -> Any:
    statement = select(Item).where(Item.owner_id == current_user.id)
    statement = statement.where(Item.created_at >= since)
    return session.exec(statement).all()
"""


def test_py806_flags_a_stored_datetime_compared_with_an_aware_now(demo_app, policy):
    app = demo_app(FRESH)
    (f,) = run_pycheck(app, policy)
    assert f.code == "PY806"
    assert f.line == line_of(app, "routes.py", "created >= cutoff")
    assert "replace(tzinfo=UTC)" in f.hint


def test_py806_flags_a_request_datetime_compared_in_sql_unconverted(demo_app, policy):
    app = demo_app(SINCE)
    (f,) = run_pycheck(app, policy)
    assert f.code == "PY806" and "since" in f.message
    assert "since.astimezone(UTC).replace(tzinfo=None)" in f.hint


def test_py806_accepts_normalised_datetimes(demo_app, policy):
    fresh = FRESH.replace(
        "    created = item.created_at\n",
        "    created = item.created_at\n"
        "    if created is not None and created.tzinfo is None:\n"
        "        created = created.replace(tzinfo=UTC)\n",
    )
    naive_now = FRESH.replace("datetime.now(UTC)", "datetime.utcnow()")
    since = SINCE.replace(
        "    statement = statement.where",
        "    since = since.astimezone(UTC).replace(tzinfo=None)\n    statement = statement.where",
    )
    for src in (fresh, naive_now, since):
        assert check(demo_app, policy, src) == [], src
