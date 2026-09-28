"""The edit slice's tickets and hidden acceptance oracle (plan 2026-09-27 thin slice, E1).

* each reference implementation scores **correct** against its own oracle, and the
  oracle reaches every ``raise HTTPException`` of the reference routes it covers;
* the unmodified template (and, for the restore ticket, its soft-delete base) scores
  **not correct** on every ticket: the suite detects that the change is missing;
* trap-violating variants of the references (search without owner scoping, a count
  that includes deleted items, restore without the admin check, the stats route
  shadowed by ``/{id}``) score **silent**; crashing ones (a naive/aware datetime
  comparison, ``since`` parsed by hand) score **loud**;
* the variant loader builds edited copies of the template in-process, side by side
  with the template and with each other.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from bench.edit_slice import oracle
from bench.edit_slice.reference import (
    REFERENCE_ROOT,
    make_reference_app,
    reference_files,
    reference_variant,
)
from bench.edit_slice.tickets import BASELINE, HELD_OUT, ROUND3_TEMPLATE, TICKETS, ticket
from bench.edit_slice.variant import load_variant
from bench.template_app import make_app
from bench.template_app.seed import TEMPLATE_USERS, build_template_db
from lattice.importer.verify import verify

IDS = [t.id for t in TICKETS]
API = "/api/v1"
# (generated cases, ticket-specific cases) per ticket
EXPECTED_COUNTS = {
    "t1_search": (23, 10),
    "t2_since": (23, 14),
    "t3_soft_delete": (87, 12),
    "t4_restore": (10, 5),
    "t5_title_window": (25, 9),
    "t6_stats": (6, 6),
    "t7_deactivate": (18, 11),
    "t8_user_filter": (15, 14),
    "t9_item_quota": (20, 7),
    "t10_owner_counts": (15, 7),
    "t11_title_trim": (45, 13),
    "t12_transfer": (17, 14),
    "t13_password_reset": (21, 7),
    "t14_email_change": (17, 6),
    "t15_duplicate_item": (9, 5),
    "t16_admin_audit": (15, 8),
    "t17_page_size_preference": (23, 8),
    "t18_duplicate_title_guard": (45, 8),
}


# ---- tickets -------------------------------------------------------------------------------


def test_eighteen_tickets_with_their_routes_and_files():
    assert IDS == [*BASELINE, *HELD_OUT, *ROUND3_TEMPLATE]
    assert list(BASELINE) == [
        "t1_search",
        "t2_since",
        "t3_soft_delete",
        "t4_restore",
        "t5_title_window",
        "t6_stats",
    ]
    assert list(HELD_OUT) == [
        "t7_deactivate",
        "t8_user_filter",
        "t9_item_quota",
        "t10_owner_counts",
        "t11_title_trim",
        "t12_transfer",
    ]
    assert list(ROUND3_TEMPLATE) == [
        "t13_password_reset",
        "t14_email_change",
        "t15_duplicate_item",
        "t16_admin_audit",
        "t17_page_size_preference",
        "t18_duplicate_title_guard",
    ]
    for t in TICKETS:
        assert t.title and t.text and t.routes and t.files
        own = {p.relative_to(REFERENCE_ROOT / t.id).as_posix()
               for p in (REFERENCE_ROOT / t.id).rglob("*.py")}  # fmt: skip
        assert set(t.files) == own, t.id
        assert set(t.files) <= set(reference_files(t.id))
    assert ticket("t4_restore").base == "t3_soft_delete"
    assert all(t.base is None for t in TICKETS if t.id != "t4_restore")


@pytest.mark.parametrize(
    ("tid", "spelled_out"),
    [
        ("t1_search", ["owner", "other users"]),
        ("t2_since", ["422", "invalid"]),
        ("t3_soft_delete", ["count", "404"]),
        ("t5_title_window", ["timezone", "naive"]),
        ("t6_stats", ["route order", "before"]),
        ("t7_deactivate", ["hashed_password", "normal user", "other admin"]),
        ("t8_user_filter", ["truth", "filtered count", "admin-only"]),
        ("t9_item_quota", ["own items only", "49", "51"]),
        ("t10_owner_counts", ["zero", "no items", "route order", "inactive"]),
        ("t11_title_trim", ["description only", "tab", "newline"]),
        ("t12_transfer", ["not enough permissions", "owner or", "before"]),
        ("t13_password_reset", ["self-lockout", "old password", "another admin"]),
        ("t14_email_change", ["before", "own current email", "excluded"]),
        ("t15_duplicate_item", ["original item's owner", "takes it over", "fresh id"]),
        ("t16_admin_audit", ["deactivated", "case-sensitive", "route order"]),
        ("t17_page_size_preference", ["never", "not a 404", "no other route"]),
        ("t18_duplicate_title_guard", ["before the duplicate", "case-sensitive", "writes nothing"]),
    ],
)
def test_the_traps_are_not_spelled_out(tid, spelled_out):
    text = ticket(tid).text.lower()
    assert not [w for w in spelled_out if w in text]


# ---- the variant loader ----------------------------------------------------------------------


def test_an_empty_overlay_behaves_like_the_template(tmp_path):
    db = build_template_db(tmp_path / "seed.db")
    email, password, _ = TEMPLATE_USERS["alice"]
    answers = []
    for factory in (make_app, load_variant({}).make_app):
        app = factory(db)
        with TestClient(app) as client:
            token = client.post(
                f"{API}/login/access-token", data={"username": email, "password": password}
            ).json()["access_token"]
            r = client.get(f"{API}/items/", headers={"Authorization": f"Bearer {token}"})
            answers.append((r.status_code, r.json()))
        app.state.engine.dispose()
    assert answers[0] == answers[1]
    assert answers[0][1]["count"] > 0


def test_a_variant_with_a_new_column_lives_beside_the_template(tmp_path):
    variant = reference_variant("t3_soft_delete")
    assert load_variant(reference_files("t3_soft_delete")) is variant  # cached by content
    db = tmp_path / "soft.db"
    app = make_reference_app("t3_soft_delete", db)
    app.state.engine.dispose()
    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(item)")]
    assert "deleted_at" in cols
    from bench.template_app.app.models import Item

    assert "deleted_at" not in Item.__table__.c  # the template's model is untouched


def test_an_overlay_that_does_not_import_is_loud():
    broken = {"api/routes/items.py": "this is not python"}
    outcome = oracle.evaluate("t1_search", load_variant(broken).make_app)
    assert outcome.kind == "loud"
    assert "failed to start" in (outcome.error or "")


# ---- references score correct ------------------------------------------------------------------


@pytest.fixture(scope="module")
def reference_outcomes() -> dict[str, oracle.Outcome]:
    return {tid: oracle.evaluate(tid, lambda p, tid=tid: make_reference_app(tid, p)) for tid in IDS}


@pytest.mark.parametrize("tid", IDS)
def test_each_reference_is_correct_against_its_own_oracle(tid, reference_outcomes):
    outcome = reference_outcomes[tid]
    assert outcome.kind == "correct", outcome.render()
    assert outcome.unverified == (), outcome.render()
    assert {c.verdict for c in outcome.cases.values()} == {"equal"}


@pytest.mark.parametrize("tid", HELD_OUT)
def test_held_out_references_never_crash(tid):
    """A reference 5xx would be 'equal' to a candidate crashing the same way, hiding a
    broken case; only the template's own null-title IntegrityError is allowed."""
    o = oracle.ticket_oracle(tid)
    result = verify(o.reference_factory, o.reference_factory, o.db_factory, o.cases,
                    oracle.RULES, {}, login=o.login)  # fmt: skip
    crashed = {n for n, rec in result.original.items() if rec.status >= 500}
    allowed = {c.name for c in o.cases
               if any(v is None and (c.route, k) in oracle.NOT_NULL
                      for k, v in (c.json or {}).items())}  # fmt: skip
    assert crashed <= allowed, sorted(crashed - allowed)


def test_case_counts(reference_outcomes):
    counts = {tid: o.counts() for tid, o in reference_outcomes.items()}
    for tid, (generated, specific) in counts.items():
        assert generated >= 6 and specific >= 5, (tid, generated, specific)
    assert counts == EXPECTED_COUNTS


# ---- the unmodified template fails every ticket ----------------------------------------------


@pytest.mark.parametrize("tid", IDS)
def test_the_unmodified_template_is_not_correct(tid):
    outcome = oracle.evaluate(tid, make_app)
    assert outcome.kind != "correct"
    # the ticket-specific cases alone already notice
    assert any(c.verdict in ("silent", "loud") for c in outcome.specific().values())


def test_the_restore_ticket_fails_on_its_soft_delete_base():
    outcome = oracle.evaluate("t4_restore", lambda p: make_reference_app("t3_soft_delete", p))
    assert outcome.kind != "correct"


# ---- trap-violating variants ------------------------------------------------------------------


def _patched(tid: str, path: str, old: str, new: str, count: int = 1) -> dict[str, str]:
    files = dict(reference_files(tid))
    assert files[path].count(old) == count, (tid, path, old)
    files[path] = files[path].replace(old, new)
    return files


TRAPS = {
    # search without owner scoping: the filter is applied, the owner clause dropped
    "search-leaks-other-users-items": (
        "t1_search",
        "silent",
        lambda: _patched(
            "t1_search",
            "api/routes/items.py",
            "    if not current_user.is_superuser:\n",
            "    if False:\n",
        ),
    ),
    # soft delete where the count still counts deleted rows
    "count-includes-deleted": (
        "t3_soft_delete",
        "silent",
        lambda: _patched(
            "t3_soft_delete",
            "api/routes/items.py",
            "count_statement = select(func.count()).select_from(Item).where(NOT_DELETED)",
            "count_statement = select(func.count()).select_from(Item)",
        ),
    ),
    # restore open to every signed-in user
    "restore-without-admin-check": (
        "t4_restore",
        "silent",
        lambda: _patched(
            "t4_restore",
            "api/routes/items.py",
            "    dependencies=[Depends(get_current_active_superuser)],\n",
            "",
        ),
    ),
    # stats declared after /{id}: "stats" is parsed as an item id (422)
    "stats-shadowed-by-item-route": (
        "t6_stats",
        "silent",
        lambda: _move_stats_after_read_item(),
    ),
    # comparing SQLite's naive created_at with an aware now(): TypeError, 500
    "naive-aware-comparison": (
        "t5_title_window",
        "loud",
        lambda: _patched(
            "t5_title_window",
            "api/routes/items.py",
            "created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at",
            "created_at",
        ),
    ),
    # since taken as a str and parsed by hand: an invalid value crashes (500)
    "since-parsed-by-hand": ("t2_since", "loud", lambda: _since_by_hand()),
    # ---- the held-out tickets
    # deactivate open to every signed-in user (the admin dependency dropped)
    "deactivate-without-admin-check": (
        "t7_deactivate",
        "silent",
        lambda: _patched(
            "t7_deactivate",
            "api/routes/users.py",
            '    "/{user_id}/deactivate",\n'
            "    dependencies=[Depends(get_current_active_superuser)],\n",
            '    "/{user_id}/deactivate",\n',
        ),
    ),
    # `if is_active:` -- is_active=false skips the filter
    "is-active-false-ignored": (
        "t8_user_filter",
        "silent",
        lambda: _patched(
            "t8_user_filter",
            "api/routes/users.py",
            "    if is_active is not None:\n",
            "    if is_active:\n",
        ),
    ),
    # the quota counts every item the caller can see (an admin sees them all)
    "quota-counts-visible-items": (
        "t9_item_quota",
        "silent",
        lambda: _patched(
            "t9_item_quota",
            "api/routes/items.py",
            "select(func.count()).select_from(Item).where(Item.owner_id == current_user.id)",
            "select(func.count()).select_from(Item).where(\n"
            "        col(Item.id).is_not(None) if current_user.is_superuser\n"
            "        else Item.owner_id == current_user.id\n"
            "    )",
        ),
    ),
    # the quota refuses the 50th item instead of the 51st
    "quota-off-by-one": (
        "t9_item_quota",
        "silent",
        lambda: _patched(
            "t9_item_quota",
            "api/routes/items.py",
            "if owned >= ITEM_LIMIT:",
            "if owned >= ITEM_LIMIT - 1:",
        ),
    ),
    # an inner join: users without items disappear from the counts
    "counts-drop-users-without-items": (
        "t10_owner_counts",
        "silent",
        lambda: _patched(
            "t10_owner_counts",
            "api/routes/users.py",
            ".outerjoin(Item, col(Item.owner_id) == User.id)",
            ".join(Item, col(Item.owner_id) == User.id)",
        ),
    ),
    # the counts route declared after /{user_id}: "item-counts" is parsed as a user id
    "counts-shadowed-by-user-route": (
        "t10_owner_counts",
        "silent",
        lambda: _move_counts_after_read_user(),
    ),
    # the title is stripped on create only
    "trim-on-create-only": (
        "t11_title_trim",
        "silent",
        lambda: _patched(
            "t11_title_trim",
            "api/routes/items.py",
            "    update_dict = _stripped_title(item_in.model_dump(exclude_unset=True))\n",
            "    update_dict = item_in.model_dump(exclude_unset=True)\n",
        ),
    ),
    # a PUT without a title (description only) crashes: None.strip()
    "trim-assumes-a-title": (
        "t11_title_trim",
        "loud",
        lambda: _patched(
            "t11_title_trim",
            "api/routes/items.py",
            '    if "title" not in data or data["title"] is None:\n        return data\n'
            '    title = data["title"].strip()\n',
            '    title = data.get("title").strip()\n',
        ),
    ),
    # transfer without the ownership check: anyone can take anyone's item
    "transfer-without-owner-check": (
        "t12_transfer",
        "silent",
        lambda: _patched(
            "t12_transfer",
            "api/routes/items.py",
            "    if not current_user.is_superuser and (item.owner_id != current_user.id):\n"
            '        raise HTTPException(status_code=403, detail="Not enough permissions")\n'
            "    new_owner = ",
            "    new_owner = ",
        ),
    ),
    # the new owner looked up before the ownership check: a stranger can probe emails
    "transfer-checks-email-first": (
        "t12_transfer",
        "silent",
        lambda: _transfer_email_first(),
    ),
    # ---- round 3's held-out template set (t13-t18; no Lattice reference)
    # an admin cannot reset another admin's password (or their own): both are admins
    "password-reset-blocks-admins": (
        "t13_password_reset",
        "silent",
        lambda: _password_reset_blocks_admins(),
    ),
    # the new email is looked up before the password is checked: a wrong password with a
    # taken email answers 409, revealing the email is taken to someone who guessed wrong
    "email-change-checks-email-first": (
        "t14_email_change",
        "silent",
        lambda: _email_change_checks_email_first(),
    ),
    # the copy is owned by the caller, not by the original item's owner
    "duplicate-takes-ownership": (
        "t15_duplicate_item",
        "silent",
        lambda: _patched(
            "t15_duplicate_item",
            "api/routes/items.py",
            "        owner_id=item.owner_id,\n",
            "        owner_id=current_user.id,\n",
        ),
    ),
    # owner_email matched case-insensitively: a search-style fold, not the account lookup
    # the ticket asks for
    "audit-email-filter-case-insensitive": (
        "t16_admin_audit",
        "silent",
        lambda: _patched(
            "t16_admin_audit",
            "api/routes/items.py",
            "User.email == owner_email",
            "func.lower(User.email) == func.lower(owner_email)",
            count=2,
        ),
    ),
    # the upper bound is off by one: 101 is accepted instead of refused
    "preferences-upper-bound-off-by-one": (
        "t17_page_size_preference",
        "silent",
        lambda: _patched(
            "t17_page_size_preference",
            "api/routes/users.py",
            "default_page_size: int | None = Field(ge=1, le=100)",
            "default_page_size: int | None = Field(ge=1, le=101)",
        ),
    ),
    # the duplicate-title check runs before the ownership check: a stranger editing
    # someone else's item to a colliding title learns of the collision (400, not 403)
    "duplicate-title-checked-before-ownership": (
        "t18_duplicate_title_guard",
        "silent",
        lambda: _patched(
            "t18_duplicate_title_guard",
            "api/routes/items.py",
            "    if not current_user.is_superuser and (item.owner_id != current_user.id):\n"
            '        raise HTTPException(status_code=403, detail="Not enough permissions")\n'
            "    update_dict = item_in.model_dump(exclude_unset=True)\n"
            '    if "title" in update_dict and update_dict["title"] != item.title:\n'
            "        _check_title_available(\n"
            "            session, item.owner_id, update_dict[\"title\"], exclude_id=item.id\n"
            "        )\n",
            "    update_dict = item_in.model_dump(exclude_unset=True)\n"
            '    if "title" in update_dict and update_dict["title"] != item.title:\n'
            "        _check_title_available(\n"
            "            session, item.owner_id, update_dict[\"title\"], exclude_id=item.id\n"
            "        )\n"
            "    if not current_user.is_superuser and (item.owner_id != current_user.id):\n"
            '        raise HTTPException(status_code=403, detail="Not enough permissions")\n',
        ),
    ),
}

HELD_OUT_TRAPS = sorted(n for n, (tid, _, _) in TRAPS.items() if tid in HELD_OUT)
ROUND3_TRAPS = sorted(n for n, (tid, _, _) in TRAPS.items() if tid in ROUND3_TEMPLATE)


def test_held_out_traps_score_silent():
    silent = [n for n in HELD_OUT_TRAPS if TRAPS[n][1] == "silent"]
    assert len(silent) >= 3
    assert {TRAPS[n][0] for n in HELD_OUT_TRAPS} == set(HELD_OUT)


def test_round3_traps_score_silent():
    """Round 3's held-out template set (t13-t18): at least 3 trap variants score
    silent, one per ticket, covering every ticket of the set."""
    silent = [n for n in ROUND3_TRAPS if TRAPS[n][1] == "silent"]
    assert len(silent) >= 3
    assert {TRAPS[n][0] for n in ROUND3_TRAPS} == set(ROUND3_TEMPLATE)


def _move_stats_after_read_item() -> dict[str, str]:
    files = dict(reference_files("t6_stats"))
    src = files["api/routes/items.py"]
    start = src.index('@router.get("/stats"')
    end = src.index('@router.get("/{id}"')
    stats = src[start:end]
    src = src[:start] + src[end:]
    anchor = src.index('@router.post("/"')
    files["api/routes/items.py"] = src[:anchor] + stats + src[anchor:]
    return files


def _move_counts_after_read_user() -> dict[str, str]:
    files = dict(reference_files("t10_owner_counts"))
    src = files["api/routes/users.py"]
    start = src.index('@router.get(\n    "/item-counts"')
    end = src.index('@router.get("/{user_id}"')
    counts = src[start:end]
    src = src[:start] + src[end:]
    anchor = src.index('@router.patch(\n    "/{user_id}"')
    files["api/routes/users.py"] = src[:anchor] + counts + src[anchor:]
    return files


def _transfer_email_first() -> dict[str, str]:
    files = dict(reference_files("t12_transfer"))
    path = "api/routes/items.py"
    src = files[path]
    check = (
        "    if not current_user.is_superuser and (item.owner_id != current_user.id):\n"
        '        raise HTTPException(status_code=403, detail="Not enough permissions")\n'
    )
    lookup = "    new_owner = crud.get_user_by_email(session=session, email=body.new_owner_email)\n"
    lookup_block = lookup + (
        "    if not new_owner:\n"
        '        raise HTTPException(status_code=404, detail="User not found")\n'
    )
    start = src.index('@router.post("/{id}/transfer"')
    head, tail = src[:start], src[start:]
    assert tail.count(check + lookup_block) == 1
    tail = tail.replace(check + lookup_block, lookup_block + check)
    files[path] = head + tail
    return files


def _password_reset_blocks_admins() -> dict[str, str]:
    files = dict(reference_files("t13_password_reset"))
    path = "api/routes/users.py"
    src = files[path]
    not_found = (
        '        raise HTTPException(status_code=404, detail="User not found")\n'
    )
    old = not_found + "    user.hashed_password = get_password_hash(body.new_password)\n"
    blocked = (
        "    if user.is_superuser:\n"
        "        raise HTTPException(\n"
        '            status_code=403, detail="Cannot reset an admin\'s password"\n'
        "        )\n"
    )
    new = not_found + blocked + "    user.hashed_password = get_password_hash(body.new_password)\n"
    assert src.count(old) == 1
    files[path] = src.replace(old, new)
    return files


def _email_change_checks_email_first() -> dict[str, str]:
    files = dict(reference_files("t14_email_change"))
    path = "api/routes/users.py"
    src = files[path]
    check_password = (
        "    verified, _ = verify_password(body.current_password, "
        "current_user.hashed_password)\n"
        "    if not verified:\n"
        '        raise HTTPException(status_code=400, detail="Incorrect password")\n'
    )
    check_email = (
        "    existing_user = crud.get_user_by_email(session=session, email=body.new_email)\n"
        "    if existing_user and existing_user.id != current_user.id:\n"
        "        raise HTTPException(\n"
        '            status_code=409, detail="User with this email already exists"\n'
        "        )\n"
    )
    old = check_password + check_email
    new = check_email + check_password
    assert src.count(old) == 1
    files[path] = src.replace(old, new)
    return files


def _since_by_hand() -> dict[str, str]:
    files = _patched(
        "t2_since",
        "api/routes/items.py",
        "    since: datetime | None = None,\n",
        "    since: str | None = None,\n",
    )
    path, old = "api/routes/items.py", "    if since is not None:\n"
    assert files[path].count(old) == 1
    files[path] = files[path].replace(old, old + "        since = datetime.fromisoformat(since)\n")
    return files


@pytest.mark.parametrize("name", sorted(TRAPS))
def test_trap_variants_are_caught(name):
    tid, expected, make = TRAPS[name]
    outcome = oracle.evaluate(tid, load_variant(make()).make_app)
    assert outcome.kind == expected, outcome.render()
    # the trap is caught by the ticket-specific cases, not only by generated ones
    bad = {n for n, c in outcome.cases.items() if c.verdict == expected}
    assert bad & set(outcome.specific()), outcome.render()


def test_a_policy_deviation_is_accepted_as_well_as_the_reference_answer():
    """A Lattice-emitted app answers an unbounded ``limit`` with 422 (the template
    proof's page-size policy); that is a declared deviation, not a failure."""
    guard = (
        "    if limit > 100:\n"
        "        raise HTTPException(\n"
        "            status_code=422,\n"
        '            detail=[{"loc": ["query", "limit"], "msg": "must be <= 100"}],\n'
        "        )\n"
    )
    files = _patched(
        "t1_search", "api/routes/items.py", '    """\n    Retrieve items.\n    """\n',
        '    """\n    Retrieve items.\n    """\n' + guard,
    )  # fmt: skip
    outcome = oracle.evaluate("t1_search", load_variant(files).make_app)
    assert outcome.kind == "correct", outcome.render()
    capped = {n: c.deviation for n, c in outcome.cases.items() if c.verdict == "deviation"}
    assert capped == {
        "read_items.page.limit_huge": "bounded-page-size",
        "read_items.page.limit_huge.admin": "bounded-page-size",
        "t1.limit_huge": "bounded-page-size",
    }


def test_outcome_render_names_the_failing_cases():
    outcome = oracle.evaluate("t6_stats", make_app)
    text = outcome.render()
    assert text.startswith("t6_stats: silent")
    assert "t6.alice" in text
