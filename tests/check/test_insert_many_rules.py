"""insert_many(T, rows=x) is a write like insert(): the P6 write rules apply to it.

Owner forgery (the owner column in the rows, or no owner=user), admin-only columns in
the rows (a role column of the users table), lineage (rows that are not a procedure's
value, e.g. another owner's fetched rows, cannot be re-inserted as the caller's own)
and the L811 bounds of the parameters the rows carry."""

from dataclasses import replace

from t2_support import API, PROFILE, S, errors, first, ok

from lattice.profile import AdminFlag, Profile
from lattice.typesys import Schema

RECORDS = """
@record
class NewItem:
    owner_id: Int
    title: Str
    created_at: DateTime
    score: Float
    secret: Str


@record
class Draft:
    title: Str
    created_at: DateTime
    score: Float
    secret: Str


@procedure
def forge(victim: Int, now: DateTime) -> list[NewItem]:
    return [NewItem(owner_id=victim, title="x", created_at=now, score=0.0, secret="s")]


@procedure
def drafts(title: Str, now: DateTime) -> list[Draft]:
    return [Draft(title=title, created_at=now, score=0.0, secret="s")]
"""


def program(body: str) -> str:
    return RECORDS + "\n\n" + body


def test_owner_forgery_through_the_rows_is_refused():
    """(a) the rows carry the owner column: a Customer writes rows owned by a victim."""
    src = program(
        """
@endpoint("POST", "/items/forge", auth=Customer)
def forge_items(user: CurrentUser, victim: Int(ge=1)) -> List:
    rows = call(forge, victim=victim, now=fn.now())
    written = insert_many(Items, rows=rows)
    return written
"""
    )
    d = first(src, "L802")
    assert "owner" in d.message


def test_owner_column_in_the_rows_is_refused_even_with_owner_user():
    src = program(
        """
@endpoint("POST", "/items/forge", auth=Customer)
def forge_items(user: CurrentUser, victim: Int(ge=1)) -> List:
    rows = call(forge, victim=victim, now=fn.now())
    written = insert_many(Items, rows=rows, owner=user)
    return written
"""
    )
    assert "L802" in errors(src)


def test_an_owned_table_needs_owner_user():
    src = program(
        """
@endpoint("POST", "/items/drafts", auth=Customer)
def add_drafts(user: CurrentUser, title: Str(max_len=20)) -> List:
    rows = call(drafts, title=title, now=fn.now())
    written = insert_many(Items, rows=rows)
    return written
"""
    )
    d = first(src, "L802")
    assert "owner=user" in (d.hint or "")


def test_the_callers_own_rows_are_accepted():
    ok(
        program(
            """
@endpoint("POST", "/items/drafts", auth=Customer)
def add_drafts(user: CurrentUser, title: Str(max_len=20)) -> List:
    rows = call(drafts, title=title, now=fn.now())
    written = insert_many(Items, rows=rows, owner=user)
    shown = select(written, cols=["id", "title"])
    return shown
"""
        )
    )


def test_an_admin_endpoint_may_set_the_owner_column():
    ok(
        program(
            """
@endpoint("POST", "/admin/items", auth=Admin)
def admin_items(user: CurrentUser, victim: Int(ge=1)) -> List:
    rows = call(forge, victim=victim, now=fn.now())
    written = insert_many(Items, rows=rows)
    shown = select(written, cols=["id", "title"])
    return shown
"""
        )
    )


def test_an_admin_endpoint_must_still_set_an_owner():
    src = program(
        """
@endpoint("POST", "/admin/items", auth=Admin)
def admin_items(user: CurrentUser, title: Str(max_len=20)) -> List:
    rows = call(drafts, title=title, now=fn.now())
    written = insert_many(Items, rows=rows)
    shown = select(written, cols=["id", "title"])
    return shown
"""
    )
    assert "L404" in errors(src)


# ---- (b) admin-only columns -----------------------------------------------------------------

USERS_FLAG = Schema.from_pairs(
    [
        ("id", S.INT),
        ("email", S.STR),
        ("is_superuser", S.BOOL),
        ("token", S.STR),
    ],
    keys=[("id",)],
    private=["token"],
)
FLAG_PROFILE = Profile(
    schemas={},
    api=replace(
        API,
        tables={**API.tables, "Users": USERS_FLAG},
        roles_from=AdminFlag("is_superuser", "Customer"),
    ),
)

SIGNUPS = """
@record
class Signup:
    email: Str
    is_superuser: Bool
    token: Str


@procedure
def signups(email: Str, flag: Bool) -> list[Signup]:
    return [Signup(email=email, is_superuser=flag, token="t")]
"""


def test_admin_flag_in_the_rows_is_refused():
    src = (
        SIGNUPS
        + """

@endpoint("POST", "/signups", auth=Customer)
def add(user: CurrentUser, email: Str(max_len=50), flag: Bool) -> List:
    rows = call(signups, email=email, flag=flag)
    written = insert_many(Users, rows=rows)
    shown = select(written, cols=["id", "email"])
    return shown
"""
    )
    d = first(src, "L802", FLAG_PROFILE)
    assert "is_superuser" in d.message


def test_role_column_in_the_rows_is_refused():
    src = """
@record
class NewUser:
    email: Str
    role: Str
    token: Str
    password_hash: Str


@procedure
def users(email: Str) -> list[NewUser]:
    return [NewUser(email=email, role="Admin", token="t", password_hash="h")]


@endpoint("POST", "/users/many", auth=Customer)
def add(user: CurrentUser, email: Str(max_len=50)) -> List:
    rows = call(users, email=email)
    written = insert_many(Users, rows=rows)
    shown = select(written, cols=["id", "email"])
    return shown
"""
    d = first(src, "L802", PROFILE)
    assert "role" in d.message


def test_schema_admin_write_columns_in_the_rows_are_refused():
    items = replace(API.tables["Items"], admin_write=frozenset({"score"}))
    profile = Profile(schemas={}, api=replace(API, tables={**API.tables, "Items": items}))
    src = program(
        """
@endpoint("POST", "/items/drafts", auth=Customer)
def add_drafts(user: CurrentUser, title: Str(max_len=20)) -> List:
    rows = call(drafts, title=title, now=fn.now())
    written = insert_many(Items, rows=rows, owner=user)
    shown = select(written, cols=["id", "title"])
    return shown
"""
    )
    d = first(src, "L802", profile)
    assert "score" in d.message


# ---- (c) lineage ----------------------------------------------------------------------------


def test_rows_of_another_table_are_not_reinserted_as_the_callers():
    """(c) rows read from the database (another owner's) are not a procedure's value:
    re-inserting them with owner=user would launder their lineage."""
    src = program(
        """
@endpoint("POST", "/items/steal", auth=Customer)
def steal(user: CurrentUser) -> List:
    items = fetch(Items)
    theirs = select(items, cols=["title", "created_at", "score", "secret"])
    written = insert_many(Items, rows=theirs, owner=user)
    shown = select(written, cols=["id", "title"])
    return shown
"""
    )
    assert "L881" in errors(src)


def test_renamed_rows_are_not_a_procedures_value_either():
    src = program(
        """
@endpoint("POST", "/events/copy", auth=Admin)
def copy(user: CurrentUser) -> List:
    events = fetch(LoginEvents)
    picked = select(events, cols=["id", "minute", "kind"])
    renamed = rename(picked, id="user_id")
    written = insert_many(LoginEvents, rows=renamed)
    return written
"""
    )
    assert "L881" in errors(src)


def test_a_procedure_value_selected_is_still_a_value():
    ok(
        program(
            """
@endpoint("POST", "/items/drafts", auth=Customer)
def add_drafts(user: CurrentUser, title: Str(max_len=20)) -> List:
    rows = call(drafts, title=title, now=fn.now())
    picked = select(rows, cols=["title", "created_at", "score", "secret"])
    written = insert_many(Items, rows=picked, owner=user)
    shown = select(written, cols=["id", "title"])
    return shown
"""
        )
    )


# ---- (d) L811 ------------------------------------------------------------------------------


def test_unbounded_parameters_carried_by_the_rows_are_refused():
    src = program(
        """
@endpoint("POST", "/items/drafts", auth=Customer)
def add_drafts(user: CurrentUser, title: Str) -> List:
    rows = call(drafts, title=title, now=fn.now())
    written = insert_many(Items, rows=rows, owner=user)
    shown = select(written, cols=["id", "title"])
    return shown
"""
    )
    d = first(src, "L811")
    assert "title" in d.message
