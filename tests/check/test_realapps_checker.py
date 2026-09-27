"""Sub-project 1 (real apps), checker side: Uuid/DateTime scalars and fns, the ApiProfile
additions (page shape, response models, natives, auth scheme, backend), page totals,
response models (L834), UNSET partial updates, owner_is() (L802/L823) and natives."""

from __future__ import annotations

import ast
import datetime as dt
from dataclasses import replace
from textwrap import dedent

import pytest

from lattice.catalog import API_FN_FUNCS, API_ONLY_FNS, FN_DOCS, FN_SIGS
from lattice.check import check_source
from lattice.check.api_info import MISSING, UNSET
from lattice.diagnostics import Severity
from lattice.profile import (
    AdminFlag,
    ApiProfile,
    NativeSig,
    NativeToken,
    Profile,
    RoleColumn,
    TokenColumn,
)
from lattice.typesys import ORDERED, SCALAR_NAMES, Scalar, Schema, TableT

S = Scalar

USER = Schema.from_pairs(
    [
        ("id", S.UUID),
        ("email", S.STR),
        ("is_active", S.BOOL),
        ("is_superuser", S.BOOL),
        ("full_name", S.STR),
        ("hashed_password", S.STR),
        ("created_at", S.DATETIME),
    ],
    keys=[("id",)],
    nullable=["full_name", "created_at"],
    private=["hashed_password"],
)
ITEM = Schema.from_pairs(
    [
        ("id", S.UUID),
        ("title", S.STR),
        ("description", S.STR),
        ("owner_id", S.UUID),
        ("created_at", S.DATETIME),
    ],
    keys=[("id",)],
    nullable=["description", "created_at"],
    owner="owner_id",
)
TAG = Schema.from_pairs([("id", S.INT), ("label", S.STR)], keys=[("id",)])

ITEM_PUBLIC = ("title", "description", "id", "owner_id", "created_at")
USER_PUBLIC = ("email", "is_active", "is_superuser", "full_name", "id", "created_at")

API = ApiProfile(
    tables={"User": USER, "Item": ITEM, "Tag": TAG},
    table_names={"User": "user", "Item": "item", "Tag": "tag"},
    roles=frozenset({"Public", "Customer", "Admin"}),
    users_table="User",
    page_shape={"items": "data", "total": "count", "limit": "", "offset": ""},
    responses={
        "ItemPublic": ITEM_PUBLIC,
        "UserPublic": USER_PUBLIC,
        "Token": ("access_token", "token_type"),
    },
    natives={
        "hash_password": NativeSig((S.STR,), S.STR),
        "verify_password": NativeSig((S.STR, S.STR), S.BOOL, reads_private=True),
        "create_access_token": NativeSig((S.UUID,), S.STR),
        "decode_token": NativeSig((S.STR,), S.UUID, nullable=True),
        "score": NativeSig((S.STR,), S.FLOAT, nullable=True),
        "echo": NativeSig((S.STR,), S.STR),
    },
    auth=NativeToken("decode_token"),
)
PROFILE = Profile(schemas={}, api=API)
DEFAULT_SHAPE = Profile(schemas={}, api=replace(API, page_shape=ApiProfile.default_page_shape()))
TABULAR = Profile(schemas={"Tag": TAG})


def check(src: str, profile: Profile = PROFILE):
    return check_source("# lattice: v1\n" + dedent(src).lstrip("\n"), profile)


def codes(src: str, profile: Profile = PROFILE) -> list[str]:
    return [d.code for d in check(src, profile).diagnostics]


def errors(src: str, profile: Profile = PROFILE) -> list[str]:
    return [d.code for d in check(src, profile).diagnostics if d.severity is Severity.ERROR]


def diag(src: str, code: str, profile: Profile = PROFILE):
    found = [d for d in check(src, profile).diagnostics if d.code == code]
    assert found, check(src, profile).diagnostics
    return found[0]


# ---- the template-style program: checks clean except the intended L823 warnings ------

TEMPLATE = """
@endpoint("GET", "/items", auth=Customer, response="ItemPublic")
def read_items(user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=1, le=100) = 100) -> Page:
    items = fetch(Item)
    mine = owned(items, by=user)
    ordered = sort(mine, by=["created_at"], desc=True)
    result = page(ordered, limit=limit, offset=skip, total=True)
    return result

@endpoint("GET", "/items/{id}", auth=Customer, response="ItemPublic")
def read_item(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    mine = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    return mine

@endpoint("POST", "/items", auth=Customer, response="ItemPublic")
def create_item(
    user: CurrentUser, title: Str(min_len=1, max_len=255), description: Str(max_len=255) = UNSET
) -> Created:
    item = insert(Item, owner=user, title=title, description=description, created_at=fn.now())
    return item

@endpoint("PUT", "/items/{id}", auth=Customer, response="ItemPublic")
def update_item(
    id: Uuid,
    user: CurrentUser,
    title: Str(min_len=1, max_len=255) = UNSET,
    description: Str(max_len=255) = UNSET,
) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    mine = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    done = update(mine, title=title, description=description)
    return done

@endpoint("DELETE", "/items/{id}", auth=Customer)
def delete_item(id: Uuid, user: CurrentUser) -> NoContent | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("Item not found"))
    mine = require(item, owner_is(user), else_=Forbidden("Not enough permissions"))
    gone = delete(mine)
    return gone

@endpoint("GET", "/users/me", auth=Customer, response="UserPublic")
def read_user_me(user: CurrentUser) -> One:
    shown = select(user, cols=USER_PUBLIC_COLS)
    return shown

@endpoint("PATCH", "/users/me", auth=Customer, response="UserPublic")
def update_user_me(
    user: CurrentUser, full_name: Str(max_len=255) = UNSET, email: Str(max_len=255) = UNSET
) -> One:
    done = update(user, full_name=full_name, email=email)
    shown = select(done, cols=USER_PUBLIC_COLS)
    return shown

@endpoint("GET", "/users", auth=Admin, response="UserPublic")
def read_users(user: CurrentUser, skip: Int(ge=0) = 0, limit: Int(ge=1, le=100) = 100) -> Page:
    users = fetch(User)
    public = select(users, cols=USER_PUBLIC_COLS)
    ordered = sort(public, by=["created_at"])
    result = page(ordered, limit=limit, offset=skip, total=True)
    return result

@endpoint("POST", "/users/signup", auth=Public, response="UserPublic")
def register_user(
    email: Str(max_len=255),
    password: Str(min_len=8, max_len=40),
    full_name: Str(max_len=255) = UNSET,
) -> Created:
    hashed = native.hash_password(password)
    created = insert(
        User,
        email=email,
        hashed_password=hashed,
        full_name=full_name,
        is_active=True,
        is_superuser=False,
        created_at=fn.now(),
    )
    shown = select(created, cols=USER_PUBLIC_COLS)
    return shown

@endpoint("POST", "/login/access-token", auth=Public, response="Token")
def login(username: Str(max_len=255), password: Str(max_len=255)) -> One | BadRequest:
    users = fetch(User)
    found = where(users, col("email") == username)
    account = one(found, else_=BadRequest("Incorrect email or password"))
    verified = require(
        account,
        native.verify_password(password, col("hashed_password")),
        else_=BadRequest("Incorrect email or password"),
    )
    active = require(verified, col("is_active"), else_=BadRequest("Inactive user"))
    token = derive(active, access_token=native.create_access_token(col("id")), token_type="bearer")
    shown = select(token, cols=["access_token", "token_type"])
    return shown
""".replace(
    "USER_PUBLIC_COLS", '["email", "is_active", "is_superuser", "full_name", "id", "created_at"]'
)


def test_template_program_checks_clean_but_for_l823() -> None:
    result = check(TEMPLATE)
    assert result.ok, result.diagnostics
    assert [d.code for d in result.diagnostics] == ["L823", "L823", "L823"]
    program = result.program
    assert program is not None
    eps = program.endpoints
    assert eps["read_item"].response == "ItemPublic"
    assert eps["delete_item"].response is None
    assert eps["read_items"].page_total is True
    assert eps["read_item"].page_total is False
    params = {p.name: p for p in eps["update_item"].params}
    assert params["title"].optional and params["title"].default is UNSET
    assert params["id"].scalar is S.UUID and not params["id"].optional
    assert params["id"].default is MISSING
    # the page total is recorded on the page() call's TableT and the returned name
    ret = eps["read_items"].node.body[-1]
    assert isinstance(ret, ast.Return)
    t = program.types[id(ret.value)]
    assert isinstance(t, TableT) and t.paged and t.page_total


def test_template_profile_is_the_default_behaviour_otherwise() -> None:
    api = ApiProfile(tables={}, table_names={}, roles=frozenset({"Public"}))
    assert api.page_shape == {"items": "items", "limit": "limit", "offset": "offset"}
    assert api.responses == {} and api.natives == {}
    assert api.auth == TokenColumn("token") and api.backend == "sqlite"


# ---- scalars ---------------------------------------------------------------------------


def test_new_scalars() -> None:
    assert SCALAR_NAMES["Uuid"] is S.UUID and SCALAR_NAMES["DateTime"] is S.DATETIME
    assert S.DATETIME in ORDERED and S.UUID not in ORDERED


GET_ITEM = """
@endpoint("GET", "/items/{{id}}", auth=Customer)
def get_item(id: Uuid, user: CurrentUser) -> One | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    found = where(mine, {pred})
    item = one(found, else_=NotFound("item"))
    return item
"""


def test_uuid_equality_is_allowed() -> None:
    assert errors(GET_ITEM.format(pred='col("id") == id')) == []
    assert errors(GET_ITEM.format(pred='col("id") != id')) == []


def test_uuid_is_not_ordered() -> None:
    d = diag(GET_ITEM.format(pred='col("id") < id'), "L402")
    assert "==" in (d.hint or "")


def test_uuid_vs_str_literal_hints_to_uuid() -> None:
    d = diag(GET_ITEM.format(pred='col("id") == "0b6f..."'), "L402")
    assert "fn.to_uuid" in (d.hint or "")


def test_to_uuid_makes_a_uuid() -> None:
    pred = 'col("id") == fn.to_uuid("5b3e9c1c-0000-4000-8000-000000000000")'
    assert errors(GET_ITEM.format(pred=pred)) == []
    assert "L402" in errors(GET_ITEM.format(pred='col("id") == fn.to_uuid(1)'))


def test_datetime_is_ordered_and_now_is_a_datetime() -> None:
    assert errors(GET_ITEM.format(pred='col("created_at") < fn.now()')) == []
    assert "L404" in errors(GET_ITEM.format(pred='col("created_at") < fn.now(1)'))


def test_fn_date_and_to_datetime() -> None:
    pred = 'fn.date(col("created_at")) == fn.date(fn.now())'
    assert errors(GET_ITEM.format(pred=pred)) == []
    pred = 'col("created_at") > fn.to_datetime("2024-01-01 10:00", format="%Y-%m-%d %H:%M")'
    assert errors(GET_ITEM.format(pred=pred)) == []
    assert "L402" in errors(GET_ITEM.format(pred='fn.date(col("title")) == fn.date(fn.now())'))
    assert "L404" in errors(GET_ITEM.format(pred='col("created_at") > fn.to_datetime("x")'))


def test_datetime_vs_str_hints_to_datetime() -> None:
    d = diag(GET_ITEM.format(pred='col("created_at") > "2024-01-01"'), "L402")
    assert "fn.to_datetime" in (d.hint or "")


def test_new_fns_are_catalogued_and_api_only() -> None:
    for name in ("to_uuid", "now", "date", "to_datetime"):
        assert name in FN_SIGS and name in FN_DOCS and name in API_FN_FUNCS
        assert name in API_ONLY_FNS
    assert FN_SIGS["now"].params == ()


def test_new_fns_are_unknown_in_tabular_programs() -> None:
    src = """
    tags = load("tags.csv", schema=Tag)
    stamped = derive(tags, at=fn.now())
    write(stamped, "out.parquet")
    """
    d = diag(src, "L106", TABULAR)
    assert "fn.now" in d.message


def test_tabular_prompt_omits_api_only_fns() -> None:
    from lattice.llm.prompt import lattice_system_prompt

    prompt = lattice_system_prompt()
    assert "fn.now(" not in prompt and "fn.to_uuid(" not in prompt


def test_uuid_and_datetime_params_and_defaults() -> None:
    src = """
    @endpoint("GET", "/items", auth=Customer)
    def recent(user: CurrentUser, since: DateTime = "2024-01-01T00:00:00Z") -> Page:
        items = fetch(Item)
        mine = owned(items, by=user)
        fresh = where(mine, col("created_at") >= since)
        result = page(fresh, limit=20)
        return result
    """
    result = check(src)
    assert result.ok, result.diagnostics
    assert result.program is not None
    (since,) = [p for p in result.program.endpoints["recent"].params if p.name == "since"]
    assert since.scalar is S.DATETIME and since.bounded
    assert since.default == dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    bad = src.replace('"2024-01-01T00:00:00Z"', '"yesterday"')
    assert errors(bad) == ["L601"]
    for overflow in ("0001-01-01T00:00:00+01:00", "9999-12-31T23:00:00-05:00"):
        assert errors(src.replace("2024-01-01T00:00:00Z", overflow)) == ["L601"]


def test_uuid_param_default_must_be_a_uuid() -> None:
    src = """
    @endpoint("GET", "/items", auth=Customer)
    def by_owner(user: CurrentUser, owner: Uuid = "{v}") -> Page:
        items = fetch(Item)
        mine = owned(items, by=user)
        theirs = where(mine, col("owner_id") == owner)
        result = page(theirs, limit=20)
        return result
    """
    ok = check(src.format(v="5B3E9C1C-0000-4000-8000-000000000000"))
    assert ok.ok, ok.diagnostics
    assert ok.program is not None
    (owner,) = [p for p in ok.program.endpoints["by_owner"].params if p.name == "owner"]
    assert owner.default == "5b3e9c1c-0000-4000-8000-000000000000"
    assert errors(src.format(v="not-a-uuid")) == ["L601"]


def test_agg_max_of_datetime_but_not_uuid() -> None:
    src = """
    @endpoint("GET", "/items/latest", auth=Customer)
    def latest(user: CurrentUser) -> One:
        items = fetch(Item)
        mine = owned(items, by=user)
        newest = group(mine, by=[], at=agg.max({x}, nulls="skip"))
        return newest
    """
    assert errors(src.format(x='col("created_at")')) == []
    assert errors(src.format(x='col("id")')) == ["L402"]


def test_uuid_key_is_generated_on_insert() -> None:
    src = """
    @endpoint("POST", "/tags", auth=Customer)
    def tag_item(user: CurrentUser, title: Str(max_len=10)) -> Created:
        item = insert(Item, owner=user, title=title{extra})
        return item
    """
    assert errors(src.format(extra="")) == []
    given = ', id=fn.to_uuid("5b3e9c1c-0000-4000-8000-000000000000")'
    assert errors(src.format(extra=given)) == []


# ---- ApiProfile validation ---------------------------------------------------------------


def _api(**kw) -> ApiProfile:
    base = {
        "tables": {"User": USER, "Item": ITEM},
        "table_names": {"User": "user", "Item": "item"},
        "roles": frozenset({"Public", "Customer", "Admin"}),
        "users_table": "User",
        "auth": TokenColumn("email"),
    }
    return ApiProfile(**{**base, **kw})


@pytest.mark.parametrize(
    "shape",
    [
        {"items": "data", "pages": "n"},
        {"limit": "limit"},
        {"items": ""},
        {"items": "data", "total": "data"},
    ],
)
def test_page_shape_validation(shape) -> None:
    with pytest.raises(ValueError):
        _api(page_shape=shape)


def test_responses_validation() -> None:
    with pytest.raises(ValueError):
        _api(responses={"X": ("a", "a")})
    with pytest.raises(ValueError):
        _api(responses={"X": ()})
    assert _api(responses={"X": ("a", "b")}).responses == {"X": ("a", "b")}


def test_natives_validation() -> None:
    with pytest.raises(ValueError):
        _api(natives={"not an identifier": NativeSig((S.STR,), S.STR)})
    with pytest.raises(ValueError):
        _api(natives={"f": NativeSig(("Str",), S.STR)})  # type: ignore[arg-type]


def test_native_token_validation() -> None:
    decode = {"decode": NativeSig((S.STR,), S.UUID, nullable=True)}
    assert _api(natives=decode, auth=NativeToken("decode")).auth == NativeToken("decode")
    with pytest.raises(ValueError, match="unknown native"):
        _api(auth=NativeToken("decode"))
    with pytest.raises(ValueError, match="Str -> Uuid"):
        _api(natives={"decode": NativeSig((S.STR,), S.INT)}, auth=NativeToken("decode"))
    with pytest.raises(ValueError, match="Str -> Uuid"):
        _api(natives={"decode": NativeSig((S.STR, S.STR), S.UUID)}, auth=NativeToken("decode"))


def test_token_column_validation() -> None:
    with pytest.raises(ValueError, match="token column"):
        _api(auth=TokenColumn("token"))  # User has no token column
    assert _api(auth=TokenColumn("email")).auth == TokenColumn("email")


def test_backend_must_be_named() -> None:
    with pytest.raises(ValueError):
        _api(backend="")


# ---- page(total=True) --------------------------------------------------------------------

PAGE = """
@endpoint("GET", "/items", auth=Customer)
def list_items(user: CurrentUser) -> Page:
    items = fetch(Item)
    mine = owned(items, by=user)
    result = page(mine, limit=20{extra})
    return result
"""


def test_page_total_needs_a_total_field() -> None:
    assert errors(PAGE.format(extra=", total=True")) == []
    d = diag(PAGE.format(extra=", total=True"), "L404", DEFAULT_SHAPE)
    assert "total" in d.message
    assert errors(PAGE.format(extra=", total=False"), DEFAULT_SHAPE) == []


def test_page_total_must_be_a_bool_literal() -> None:
    assert errors(PAGE.format(extra=", total=1")) == ["L404"]


def test_page_without_total_records_false() -> None:
    result = check(PAGE.format(extra=""))
    assert result.program is not None
    assert result.program.endpoints["list_items"].page_total is False


def test_page_total_survives_select() -> None:
    src = """
    @endpoint("GET", "/items", auth=Customer)
    def list_items(user: CurrentUser) -> Page:
        items = fetch(Item)
        mine = owned(items, by=user)
        result = page(mine, limit=20, total=True)
        shown = select(result, cols=["id", "title"])
        return shown
    """
    result = check(src)
    assert result.program is not None
    assert result.program.endpoints["list_items"].page_total is True


# ---- response models (L834) --------------------------------------------------------------

ONE_ITEM = """
@endpoint("GET", "/items/{{id}}", auth=Customer{response})
def get_item(id: Uuid, user: CurrentUser) -> One | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    found = where(mine, col("id") == id)
    item = one(found, else_=NotFound("item"))
    shown = select(item, cols=[{cols}])
    return shown
"""
ALL_ITEM_COLS = '"id", "title", "description", "owner_id", "created_at"'


def test_response_model_matches_in_any_order() -> None:
    result = check(ONE_ITEM.format(response=', response="ItemPublic"', cols=ALL_ITEM_COLS))
    assert result.ok, result.diagnostics
    assert result.program is not None
    assert result.program.endpoints["get_item"].response == "ItemPublic"


def test_response_model_missing_and_extra_fields() -> None:
    d = diag(ONE_ITEM.format(response=', response="Token"', cols='"id", "title"'), "L834")
    assert "access_token" in d.message and "token_type" in d.message
    assert "'id'" in d.message and "'title'" in d.message
    d = diag(ONE_ITEM.format(response=', response="ItemPublic"', cols='"id", "title"'), "L834")
    assert "missing" in d.message and "extra" not in d.message


def test_unknown_response_model() -> None:
    d = diag(ONE_ITEM.format(response=', response="ItemPubic"', cols=ALL_ITEM_COLS), "L834")
    assert "ItemPublic" in (d.hint or "")


def test_response_must_be_a_string_literal() -> None:
    src = ONE_ITEM.format(response=", response=ItemPublic", cols=ALL_ITEM_COLS)
    assert errors(src) == ["L601"]


def test_response_on_no_content() -> None:
    src = """
    @endpoint("DELETE", "/items/{id}", auth=Customer, response="ItemPublic")
    def drop(id: Uuid, user: CurrentUser) -> NoContent | NotFound:
        items = fetch(Item)
        mine = owned(items, by=user)
        found = where(mine, col("id") == id)
        item = one(found, else_=NotFound("item"))
        gone = delete(item)
        return gone
    """
    assert errors(src) == ["L834"]


def test_response_model_for_page_items() -> None:
    src = """
    @endpoint("GET", "/items", auth=Customer, response="ItemPublic")
    def list_items(user: CurrentUser) -> Page:
        items = fetch(Item)
        mine = owned(items, by=user)
        shown = select(mine, cols=["id", "title"])
        result = page(shown, limit=20)
        return result
    """
    assert errors(src) == ["L834"]


# ---- UNSET partial updates ----------------------------------------------------------------

UPDATE = """
@endpoint("PATCH", "/items/{{id}}", auth=Customer)
def edit(id: Uuid, user: CurrentUser, title: Str(max_len=255) = UNSET) -> One | NotFound:
    items = fetch(Item)
    mine = owned(items, by=user)
    found = where(mine, col("id") == id)
    item = one(found, else_=NotFound("item"))
    done = update(item, {values})
    return done
"""


def test_optional_param_used_directly_in_update() -> None:
    assert errors(UPDATE.format(values="title=title")) == []


def test_optional_param_inside_an_expression_is_l404() -> None:
    d = diag(UPDATE.format(values="title=fn.lower(title)"), "L404")
    assert "UNSET" in d.message and "title" in d.message


def test_optional_param_in_a_filter_is_l404() -> None:
    src = """
    @endpoint("GET", "/items", auth=Customer)
    def search(user: CurrentUser, title: Str(max_len=255) = UNSET) -> Page:
        items = fetch(Item)
        mine = owned(items, by=user)
        found = where(mine, col("title") == title)
        result = page(found, limit=20)
        return result
    """
    assert errors(src) == ["L404"]


def test_unset_outside_a_parameter_default_is_l404() -> None:
    assert errors(UPDATE.format(values="title=UNSET")) == ["L404"]
    src = """
    NOTHING = UNSET

    @endpoint("GET", "/me", auth=Customer)
    def me(user: CurrentUser) -> One:
        shown = select(user, cols=["id"])
        return shown
    """
    assert "L404" in errors(src)


def test_unset_on_the_caller_is_l601() -> None:
    src = """
    @endpoint("GET", "/me", auth=Customer)
    def me(user: CurrentUser = UNSET) -> One:
        shown = select(user, cols=["id"])
        return shown
    """
    assert errors(src) == ["L601"]


def test_path_parameter_cannot_be_unset() -> None:
    src = """
    @endpoint("PATCH", "/tags/{id}", auth=Admin)
    def relabel(
        user: CurrentUser, id: Int(ge=1) = UNSET, label: Str(max_len=9) = UNSET
    ) -> One | NotFound:
        tags = fetch(Tag)
        tag = one(tags, else_=NotFound("tag"))
        done = update(tag, label=label)
        return done
    """
    d = diag(src, "L404")
    assert "path parameter 'id'" in d.message
    assert errors(src) == ["L404"]


def test_insert_optional_param_needs_a_nullable_column() -> None:
    src = """
    @endpoint("POST", "/items", auth=Customer)
    def add(
        user: CurrentUser, title: Str(max_len=255) = UNSET, description: Str(max_len=9) = UNSET
    ) -> Created:
        item = insert(Item, owner=user, title=title, description=description)
        return item
    """
    d = diag(src, "L404")
    assert "title" in d.message and "description" not in d.message
    assert errors(src) == ["L404"]


def test_optional_param_still_needs_bounds() -> None:
    src = UPDATE.replace("Str(max_len=255) = UNSET", "Str = UNSET").format(values="title=title")
    assert errors(src) == ["L811"]


# ---- owner_is(user) -----------------------------------------------------------------------

OWNER_IS = """
@endpoint("GET", "/items/{{id}}", auth={auth})
def get_item(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
    items = fetch(Item)
    found = where(items, col("id") == id)
    item = one(found, else_=NotFound("item"))
    mine = require(item, {cond}, else_=Forbidden("not yours"))
    return mine
"""


def test_owner_is_is_an_ownership_check_with_l823() -> None:
    result = check(OWNER_IS.format(auth="Customer", cond="owner_is(user)"))
    assert result.ok, result.diagnostics
    d = diag(OWNER_IS.format(auth="Customer", cond="owner_is(user)"), "L823")
    assert d.severity is Severity.WARNING
    assert "403" in d.message and "accessible()" in d.message


def test_owner_is_in_admin_endpoints_has_no_warning() -> None:
    assert codes(OWNER_IS.format(auth="Admin", cond="owner_is(user)")) == []


def test_other_require_is_not_an_ownership_check() -> None:
    assert "L802" in errors(OWNER_IS.format(auth="Customer", cond='col("title") == "x"'))


def test_owner_is_must_be_the_whole_condition() -> None:
    cond = 'owner_is(user) & (col("title") == "x")'
    assert "L404" in errors(OWNER_IS.format(auth="Customer", cond=cond))


def test_owner_is_takes_the_caller() -> None:
    assert "L404" in errors(OWNER_IS.format(auth="Customer", cond="owner_is(id)"))
    assert "L404" in errors(OWNER_IS.format(auth="Customer", cond="owner_is()"))


def test_owner_is_outside_require_is_l404() -> None:
    src = """
    @endpoint("GET", "/items", auth=Customer)
    def mine(user: CurrentUser) -> Page:
        items = fetch(Item)
        own = where(items, owner_is(user))
        result = page(own, limit=20)
        return result
    """
    assert "L404" in errors(src)


def test_owner_is_needs_an_owner_column() -> None:
    src = """
    @endpoint("GET", "/tags/{id}", auth=Customer)
    def get_tag(id: Int(ge=1), user: CurrentUser) -> One | NotFound | Forbidden:
        tags = fetch(Tag)
        found = where(tags, col("id") == id)
        tag = one(found, else_=NotFound("tag"))
        mine = require(tag, owner_is(user), else_=Forbidden("no"))
        return mine
    """
    assert errors(src) == ["L404"]


def test_a_business_check_before_owner_is_is_l802() -> None:
    src = """
    @endpoint("GET", "/items/{id}", auth=Customer)
    def get_item(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden | Conflict:
        items = fetch(Item)
        found = where(items, col("id") == id)
        item = one(found, else_=NotFound("item"))
        titled = require(item, col("title") == "x", else_=Conflict("no"))
        mine = require(titled, owner_is(user), else_=Forbidden("not yours"))
        return mine
    """
    assert "L802" in errors(src)


def test_returning_the_unchecked_row_is_still_l802() -> None:
    src = """
    @endpoint("GET", "/items/{id}", auth=Customer)
    def get_item(id: Uuid, user: CurrentUser) -> One | NotFound | Forbidden:
        items = fetch(Item)
        found = where(items, col("id") == id)
        item = one(found, else_=NotFound("item"))
        mine = require(item, owner_is(user), else_=Forbidden("not yours"))
        return item
    """
    assert "L802" in errors(src)


NOTES = Schema.from_pairs(
    [("id", S.INT), ("order_id", S.INT), ("author_id", S.UUID), ("body", S.STR)],
    keys=[("id",)],
    owner="author_id",
)
ORDERS = Schema.from_pairs(
    [("id", S.INT), ("customer_id", S.UUID), ("total", S.FLOAT)],
    keys=[("id",)],
    owner="customer_id",
)
JOINED = Profile(
    schemas={},
    api=replace(
        API,
        tables={**API.tables, "Notes": NOTES, "Orders": ORDERS},
        table_names={**API.table_names, "Notes": "notes", "Orders": "orders"},
    ),
)


def test_owner_is_over_a_join_of_owned_tables_is_l404() -> None:
    # the SQL checks one owner column: the other table's rows would leak
    src = """
    @endpoint("GET", "/notes", auth=Customer)
    def list_notes(user: CurrentUser) -> Page | Forbidden:
        notes = fetch(Notes)
        orders = fetch(Orders)
        j = join(notes, orders, on={"order_id": "id"})
        ok = require(j, owner_is(user), else_=Forbidden("x"))
        result = page(ok, limit=20)
        return result
    """
    d = diag(src, "L404", JOINED)
    assert "owner_is()" in d.message
    assert errors(src, JOINED) == ["L404"]


def test_owner_is_over_one_fetched_table_is_clean() -> None:
    src = """
    @endpoint("GET", "/notes/{id}", auth=Customer)
    def get_note(id: Int(ge=1), user: CurrentUser) -> One | NotFound | Forbidden:
        notes = fetch(Notes)
        found = where(notes, col("id") == id)
        picked = select(found, cols=["id", "author_id", "body"])
        note = one(picked, else_=NotFound("note"))
        ok = require(note, owner_is(user), else_=Forbidden("x"))
        return ok
    """
    assert errors(src, JOINED) == []


def test_owner_is_is_unknown_in_tabular_programs() -> None:
    src = """
    tags = load("tags.csv", schema=Tag)
    mine = where(tags, owner_is(1))
    write(mine, "out.parquet")
    """
    assert "L203" in errors(src, TABULAR)


# ---- natives -------------------------------------------------------------------------------

NATIVE = """
@endpoint("GET", "/items", auth=Customer)
def list_items(user: CurrentUser, q: Str(max_len=20) = "") -> Page:
    items = fetch(Item)
    mine = owned(items, by=user)
    tagged = derive(mine, v={expr})
    result = page(tagged, limit=20)
    return result
"""


def test_native_call_is_typed_from_the_profile() -> None:
    assert errors(NATIVE.format(expr='native.hash_password(col("title"))')) == []
    assert errors(NATIVE.format(expr="native.hash_password(q)")) == []
    assert errors(NATIVE.format(expr='native.create_access_token(col("id"))')) == []


def test_native_arity_and_types() -> None:
    assert errors(NATIVE.format(expr="native.hash_password()")) == ["L404"]
    assert errors(NATIVE.format(expr='native.hash_password(q, x="y")')) == ["L404"]
    d = diag(NATIVE.format(expr='native.create_access_token(col("title"))'), "L402")
    assert "Uuid" in d.message and "Str" in d.message


def test_unknown_native_suggests_a_name() -> None:
    d = diag(NATIVE.format(expr="native.hash_pasword(q)"), "L106")
    assert "hash_password" in (d.hint or "")


def test_natives_cannot_take_tables() -> None:
    assert errors(NATIVE.format(expr="native.hash_password(items)")) == ["L411"]


def test_bare_native_attribute_is_not_a_value() -> None:
    assert errors(NATIVE.format(expr="native.hash_password")) == ["L411"]


def test_nullable_native_result_needs_a_null_policy() -> None:
    src = """
    @endpoint("GET", "/items/score", auth=Customer)
    def avg_score(user: CurrentUser) -> One:
        items = fetch(Item)
        mine = owned(items, by=user)
        scored = derive(mine, s=native.score(col("title")))
        stats = group(scored, by=[], avg=agg.mean(col("s")))
        return stats
    """
    assert errors(src) == ["L421"]
    assert errors(src.replace('col("s")', 'col("s"), nulls="skip"')) == []
    direct = src.replace(
        'scored = derive(mine, s=native.score(col("title")))\n        ', ""
    ).replace(
        'group(scored, by=[], avg=agg.mean(col("s")))',
        'group(mine, by=[], avg=agg.mean(native.score(col("title"))))',
    )
    assert errors(direct) == ["L421"]


def test_natives_are_not_available_in_tabular_programs() -> None:
    src = """
    tags = load("tags.csv", schema=Tag)
    hashed = derive(tags, h=native.hash_password(col("label")))
    write(hashed, "out.parquet")
    """
    assert "L105" in errors(src, TABULAR)


def test_natives_in_steps_called_from_endpoints() -> None:
    src = """
    @step
    def hashed(t: Table[Tag]) -> Table:
        out = derive(t, h=native.hash_password(col("label")))
        return out

    @endpoint("GET", "/tags", auth=Public)
    def tags() -> Page:
        all_tags = fetch(Tag)
        shown = hashed(all_tags)
        result = page(shown, limit=20)
        return result
    """
    assert errors(src) == []


def test_private_column_through_a_plain_native_is_l803() -> None:
    src = """
    @endpoint("POST", "/check", auth=Public)
    def probe(username: Str(max_len=255), guess: Str(max_len=255)) -> One | BadRequest:
        users = fetch(User)
        found = where(users, col("email") == username)
        account = one(found, else_=BadRequest("no"))
        ok = require(account, native.echo(col("hashed_password")) == guess, else_=BadRequest("no"))
        shown = select(ok, cols=["id"])
        return shown
    """
    assert errors(src) == ["L803"]


def test_derived_value_of_a_plain_native_over_a_private_column_is_private() -> None:
    src = """
    @endpoint("GET", "/users/me/x", auth=Customer)
    def leak(user: CurrentUser) -> One:
        out = derive(user, x=native.echo(col("hashed_password")))
        shown = select(out, cols=["id", "x"])
        return shown
    """
    assert errors(src) == ["L803"]


def test_verifier_native_result_is_not_private() -> None:
    src = """
    @endpoint("POST", "/users/me/check", auth=Customer)
    def check_pw(user: CurrentUser, guess: Str(max_len=40)) -> One:
        out = derive(user, ok=native.verify_password(guess, col("hashed_password")))
        shown = select(out, cols=["id", "ok"])
        return shown
    """
    assert errors(src) == []


# ---- the role column is admin-only ----------------------------------------------------------

SELF_GRANT = """
@endpoint("PATCH", "/users/me", auth={auth})
def update_user_me(user: CurrentUser, {param}: {typ} = UNSET) -> One:
    done = update(user, {param}={param})
    shown = select(done, cols=["id", "email"])
    return shown
"""
ADMIN_FLAG = Profile(schemas={}, api=replace(API, roles_from=AdminFlag("is_superuser", "Customer")))


def test_a_customer_cannot_set_the_admin_flag() -> None:
    src = SELF_GRANT.format(auth="Customer", param="is_superuser", typ="Bool")
    d = diag(src, "L802", ADMIN_FLAG)
    assert "is_superuser" in d.message
    assert errors(src, ADMIN_FLAG) == ["L802"]


def test_an_admin_may_set_the_admin_flag() -> None:
    src = SELF_GRANT.format(auth="Admin", param="is_superuser", typ="Bool")
    assert errors(src, ADMIN_FLAG) == []


def test_a_customer_may_set_other_columns_under_an_admin_flag() -> None:
    src = SELF_GRANT.format(auth="Customer", param="full_name", typ="Str(max_len=255)")
    assert errors(src, ADMIN_FLAG) == []


def test_a_customer_cannot_set_the_role_column() -> None:
    users = Schema.from_pairs(
        [("id", S.UUID), ("email", S.STR), ("role", S.STR)], keys=[("id",)], owner="id"
    )
    profile = Profile(
        schemas={},
        api=replace(API, tables={**API.tables, "User": users}, roles_from=RoleColumn("role")),
    )
    src = SELF_GRANT.format(auth="Customer", param="role", typ="Str(max_len=20)")
    assert errors(src, profile) == ["L802"]


def test_a_signup_may_set_the_admin_flag_to_false() -> None:
    src = """
    @endpoint("POST", "/users/signup", auth=Public)
    def signup(email: Str(max_len=255), flag: Bool) -> One:
        made = insert(User, email=email, hashed_password="x", is_active=True, is_superuser={v})
        shown = select(made, cols=["id", "email"])
        return shown
    """
    assert errors(src.replace("{v}", "False"), ADMIN_FLAG) == []
    assert errors(src.replace("{v}", "True"), ADMIN_FLAG) == ["L802"]
    assert errors(src.replace("{v}", "flag"), ADMIN_FLAG) == ["L802"]
