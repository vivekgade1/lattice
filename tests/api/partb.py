"""Sub-project 1b Part B fixture: the real-apps profile (tests/api/realapps.py) with
every Part B setting switched on, and a program using every Part B language feature.

* B1 ``active_column="is_active"``: an inactive caller (carol) is answered 400
  "Inactive user" by auth itself -- before the role check and before parameter
  validation;
* B2 ``error_body="fastapi"``: every error body is ``{"detail": ...}``, with the
  automatic 401/403 detail texts configured;
* B3 ``datetime_wire="naive"``: DateTime values answer as naive UTC ISO text;
* B4 ``Email`` and ``Str(pattern=...)`` parameters;
* B5 ``unique_or=Conflict("...")`` on insert and update;
* B6 module-level list-of-Str constants in ``select(cols=...)``.

The database, natives and tokens are the real-apps ones (carol is seeded inactive).
Shared by tests/api/test_partb.py and the codegen differential.
"""

from __future__ import annotations

from dataclasses import replace

from lattice.profile import Profile
from tests.api import realapps

NATIVES = realapps.NATIVES
NATIVES_FILE = realapps.NATIVES_FILE
TOKENS = realapps.TOKENS
build_db = realapps.build_db
uid = realapps.uid

UNAUTHORIZED = "Not authenticated"
FORBIDDEN = "The user doesn't have enough privileges"
EMAIL_TAKEN = "User with this email already exists"
SIGNUP_TAKEN = "The user with this email already exists in the system"

API = replace(
    realapps.API,
    active_column="is_active",
    inactive_status=400,
    inactive_detail="Inactive user",
    error_body="fastapi",
    unauthorized_detail=UNAUTHORIZED,
    forbidden_detail=FORBIDDEN,
    datetime_wire="naive",
)
PROFILE = Profile(schemas={}, api=API)

PROGRAM = """# lattice: v1
PUBLIC = ["email", "is_active", "is_superuser", "full_name", "id", "created_at"]
BRIEF = ["id", "title", "created_at"]


@endpoint("GET", "/items/", auth=Customer, response="ItemPublic")
def read_items(user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=1, le=100) = 100) -> Page:
    items = fetch(Item)
    mine = owned(items, by=user)
    ordered = sort(mine, by=["created_at"], desc=True)
    result = page(ordered, limit=limit, offset=skip, total=True)
    return result


@endpoint("GET", "/items/{id}/brief", auth=Customer)
def brief_item(id: Uuid, user: CurrentUser) -> One | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    found = where(mine, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    shown = select(item, cols=BRIEF)
    return shown


@endpoint("POST", "/items/", auth=Customer, response="ItemPublic")
def create_item(
    user: CurrentUser,
    title: Str(min_len=1, max_len=255, pattern="[A-Z][A-Za-z ]*"),
    description: Str(max_len=255) = UNSET,
) -> Created:
    item = insert(Item, owner=user, title=title, description=description, created_at=fn.now())
    return item


@endpoint("GET", "/users/me", auth=Customer, response="UserPublic")
def read_user_me(user: CurrentUser) -> One:
    shown = select(user, cols=PUBLIC)
    return shown


@endpoint("PATCH", "/users/me", auth=Customer, response="UserPublic")
def update_user_me(
    user: CurrentUser, full_name: Str(max_len=255) = UNSET, email: Email(max_len=255) = UNSET
) -> One | Conflict:
    done = update(
        user,
        full_name=full_name,
        email=email,
        unique_or=Conflict("User with this email already exists"),
    )
    shown = select(done, cols=PUBLIC)
    return shown


@endpoint("GET", "/users/", auth=Admin, response="UserPublic")
def read_users(user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=1, le=100) = 100) -> Page:
    users = fetch(User)
    public = select(users, cols=PUBLIC)
    ordered = sort(public, by=["created_at"])
    result = page(ordered, limit=limit, offset=skip, total=True)
    return result


@endpoint("POST", "/users/signup", auth=Public, response="UserPublic")
def register_user(
    email: Email(max_len=255),
    password: Str(min_len=8, max_len=40),
    full_name: Str(max_len=255) = UNSET,
) -> Created | Conflict:
    hashed = native.hash_password(password)
    created = insert(
        User,
        email=email,
        hashed_password=hashed,
        full_name=full_name,
        is_active=True,
        is_superuser=False,
        created_at=fn.now(),
        unique_or=Conflict("The user with this email already exists in the system"),
    )
    shown = select(created, cols=PUBLIC)
    return shown


@endpoint("GET", "/items/{id}", auth=Customer, response="ItemPublic")
def read_item(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    mine = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    return mine
"""
