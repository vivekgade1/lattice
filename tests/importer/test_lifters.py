"""lattice.importer.lifters: one test group per idiom family, with positive and negative
snippets, and the composer's acceptance rules (importer plan, node I3).

Snippets replace a template route's body: the route keeps its extracted metadata
(parameters, dependencies, response model) and the names resolve through its module's
imports, so each snippet is the original route with one thing changed. A lifter must
return None for any shape it does not know -- never a guess.
"""

from __future__ import annotations

import ast
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from lattice.importer.extract import RepoModel, _Index, extract
from lattice.importer.lift import load_profile
from lattice.importer.lifters import (
    admin_list,
    create,
    delete,
    get_or_404,
    lift_route,
    login,
    me,
    owner_check,
    page_list,
    partial_update,
    returns,
    unique_check,
)
from lattice.importer.lifters.base import RouteSource
from lattice.importer.policy import NotNull, PageBound
from lattice.importer.profile_gen import generate_profile

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "bench.template_app.app"


@pytest.fixture(scope="module")
def ctx() -> tuple[RepoModel, _Index, object]:
    model = extract(REPO_ROOT, PACKAGE)
    api = load_profile(generate_profile(model), "lifters_test_profile").profile().api
    return model, _Index(REPO_ROOT, PACKAGE), api


def route(ctx, name: str, body: str | None = None, **changes) -> RouteSource:
    """The template route ``name``, its body replaced by ``body`` when given."""
    model, index, api = ctx
    r = next(r for r in model.routes if r.name == name and "private" not in r.module)
    r = replace(r, **changes)
    fn = index.modules[r.module].defs[r.name]
    if body is not None:
        fn = replace_body(fn, body)
    return RouteSource(r, fn, index, api, model)


def replace_body(fn: ast.FunctionDef, body: str) -> ast.FunctionDef:
    new = ast.parse(textwrap.dedent(body)).body
    for stmt in new:  # keep the real line numbers plausible
        ast.increment_lineno(stmt, fn.lineno)
    return ast.FunctionDef(
        name=fn.name,
        args=fn.args,
        body=new,
        decorator_list=fn.decorator_list,
        returns=fn.returns,
        type_comment=None,
        type_params=[],
        lineno=fn.lineno,
        col_offset=fn.col_offset,
    )


def lifted(ctx, name: str, body: str | None = None, **changes):
    src = route(ctx, name, body, **changes)
    return lift_route(src, src.model)


# ---- get-or-404 --------------------------------------------------------------------------

READ_ITEM = """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return item
"""


def test_get_or_404_lifts_the_lookup_and_its_guard(ctx):
    f = get_or_404.match(route(ctx, "read_item"), ctx[0])
    assert f is not None and f.stmts == (0, 1) and f.errors == ("NotFound",)
    assert [(s.name, s.op) for s in f.steps] == [
        ("items", "fetch"),
        ("found", "where"),
        ("item", "one"),
    ]
    assert f.steps[2].kwargs == (("else_", 'NotFound("Item not found")'),)
    assert f.consumes == ("id",) and f.params[0].annotation == "Uuid"


def test_get_or_404_accepts_is_none_and_status_constants(ctx):
    body = READ_ITEM.replace("if not item:", "if item is None:").replace(
        "status_code=404", "status_code=status.HTTP_404_NOT_FOUND"
    )
    # `status` is not imported by items.py: the constant is still read by its name
    f = get_or_404.match(route(ctx, "read_item", body), ctx[0])
    assert f is not None and f.errors == ("NotFound",)


@pytest.mark.parametrize(
    "change",
    [
        ("if not item:", "if item:"),  # the inverse guard
        ("status_code=404", "status_code=418"),  # no Lattice error for 418
        ('detail="Item not found"', "detail=f'{id} not found'"),  # not a literal
        ("session.get(Item, id)", "session.get(Item, uuid.uuid4())"),  # not the path param
        ("session.get(Item, id)", "session.get(Item, id, with_for_update=True)"),
    ],
)
def test_get_or_404_refuses_other_shapes(ctx, change):
    body = READ_ITEM.replace(*change)
    assert body != READ_ITEM
    assert get_or_404.match(route(ctx, "read_item", body), ctx[0]) is None


def test_get_or_404_needs_the_guard_right_after_the_lookup(ctx):
    body = READ_ITEM.replace("    if not item:", "    print(item)\n    if not item:")
    assert get_or_404.match(route(ctx, "read_item", body), ctx[0]) is None


# ---- superuser-or-owner 403 ---------------------------------------------------------------


def test_owner_check_lifts_to_require_owner_is(ctx):
    f = owner_check.match(route(ctx, "read_item"), ctx[0])
    assert f is not None and f.stmts == (2,) and f.errors == ("Forbidden",)
    (step,) = f.steps
    assert (step.op, step.args, step.binds) == ("require", ("{@item}", "owner_is(user)"), "item")


def test_owner_check_accepts_either_order(ctx):
    body = READ_ITEM.replace(
        "not current_user.is_superuser and (item.owner_id != current_user.id)",
        "current_user.id != item.owner_id and not current_user.is_superuser",
    )
    assert owner_check.match(route(ctx, "read_item", body), ctx[0]) is not None


@pytest.mark.parametrize(
    "test",
    [
        "item.owner_id != current_user.id",  # no admin bypass: owner_is would admit admins
        "not current_user.is_superuser and (item.title != current_user.id)",  # not the owner
        "not current_user.is_superuser or (item.owner_id != current_user.id)",  # or
        "not current_user.is_active and (item.owner_id != current_user.id)",  # not the admin flag
        "not current_user.is_superuser and (item.owner_id == current_user.id)",  # inverted
    ],
)
def test_owner_check_refuses_other_conditions(ctx, test):
    body = READ_ITEM.replace(
        "not current_user.is_superuser and (item.owner_id != current_user.id)", test
    )
    assert owner_check.match(route(ctx, "read_item", body), ctx[0]) is None


def test_owner_check_needs_a_403(ctx):
    body = READ_ITEM.replace("status_code=403", "status_code=404")
    assert owner_check.match(route(ctx, "read_item", body), ctx[0]) is None


# ---- partial update ------------------------------------------------------------------------

UPDATE_ITEM = READ_ITEM.replace(
    "    return item\n",
    """    update_dict = item_in.model_dump(exclude_unset=True)
    item.sqlmodel_update(update_dict)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item
""",
)


def test_partial_update_lifts_to_update_with_unset_params(ctx):
    f = partial_update.match(route(ctx, "update_item"), ctx[0])
    assert f is not None and f.stmts == (3, 4, 5, 6, 7)
    assert [(p.name, p.annotation, p.default) for p in f.params] == [
        ("title", "Str(min_len=1, max_len=255)", "UNSET"),
        ("description", "Str(max_len=255)", "UNSET"),
    ]
    # ItemUpdate.title is Optional over a NOT NULL column: a declared policy
    assert f.policies == (NotNull("title"),)
    assert f.steps[0].writes == ("title", "description")


def test_partial_update_accepts_the_inline_dump(ctx):
    body = UPDATE_ITEM.replace(
        "    update_dict = item_in.model_dump(exclude_unset=True)\n"
        "    item.sqlmodel_update(update_dict)\n",
        "    item.sqlmodel_update(item_in.model_dump(exclude_unset=True))\n",
    )
    f = partial_update.match(route(ctx, "update_item", body), ctx[0])
    assert f is not None and f.stmts == (3, 4, 5, 6)


@pytest.mark.parametrize(
    "change",
    [
        ("model_dump(exclude_unset=True)", "model_dump()"),  # every field, set or not
        ("item.sqlmodel_update(update_dict)", "item.sqlmodel_update(update_dict, update={})"),
        ("    session.commit()\n", ""),  # never committed
    ],
)
def test_partial_update_refuses_other_shapes(ctx, change):
    body = UPDATE_ITEM.replace(*change)
    assert body != UPDATE_ITEM
    assert partial_update.match(route(ctx, "update_item", body), ctx[0]) is None


def test_partial_update_refuses_a_body_read_again(ctx):
    body = UPDATE_ITEM.replace("    return item\n", "    print(item_in)\n    return item\n")
    assert partial_update.match(route(ctx, "update_item", body), ctx[0]) is None


# ---- create -------------------------------------------------------------------------------

CREATE = """
    item = Item.model_validate(item_in, update={"owner_id": current_user.id})
    session.add(item)
    session.commit()
    session.refresh(item)
    return item
"""


def test_create_lifts_to_insert_with_owner_and_defaults(ctx):
    f = create.match(route(ctx, "create_item"), ctx[0])
    assert f is not None and f.stmts == (0, 1, 2, 3)
    (step,) = f.steps
    assert step.op == "insert" and step.args == ("Item",)
    assert step.kwargs == (
        ("owner", "user"),
        ("title", "title"),
        ("description", "description"),
        ("created_at", "fn.now()"),
    )
    assert [(p.name, p.default) for p in f.params] == [("title", None), ("description", "UNSET")]


@pytest.mark.parametrize(
    "change",
    [
        ('{"owner_id": current_user.id}', '{"owner_id": id}'),  # not the caller's key
        ('{"owner_id": current_user.id}', '{"title": current_user.email}'),  # not the owner
        (', update={"owner_id": current_user.id}', ""),  # the owner never set
        ("    session.add(item)\n", ""),  # never added
    ],
)
def test_create_refuses_other_shapes(ctx, change):
    body = CREATE.replace(*change)
    assert body != CREATE
    assert create.match(route(ctx, "create_item", body), ctx[0]) is None


# ---- uniqueness pre-check ------------------------------------------------------------------

UPDATE_ME = """
    if user_in.email:
        existing_user = crud.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != current_user.id:
            raise HTTPException(status_code=409, detail="User with this email already exists")
    user_data = user_in.model_dump(exclude_unset=True)
    current_user.sqlmodel_update(user_data)
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user
"""


def test_unique_check_through_a_lookup_helper(ctx):
    f = unique_check.match(route(ctx, "update_user_me"), ctx[0])
    assert f is not None and f.stmts == (0,) and f.errors == ("Conflict",)
    (m,) = f.modifiers
    assert (m.keyword, m.value, m.subject, m.column) == (
        "unique_or",
        'Conflict("User with this email already exists")',
        "current_user",
        "email",
    )


def test_unique_check_inline_lookup(ctx):
    body = UPDATE_ME.replace(
        "crud.get_user_by_email(session=session, email=user_in.email)",
        "session.exec(select(User).where(User.email == user_in.email)).first()",
    )
    assert unique_check.match(route(ctx, "update_user_me", body), ctx[0]) is not None


@pytest.mark.parametrize(
    "change",
    [
        ("status_code=409", "status_code=400"),  # unique_or answers 409 only
        ("existing_user.id != current_user.id", "existing_user.email != current_user.email"),
        ("existing_user and ", ""),  # would raise for a missing row too
        (
            "crud.get_user_by_email(session=session, email=user_in.email)",
            "session.exec(select(User).where(User.full_name == user_in.full_name)).first()",
        ),
        (
            "crud.get_user_by_email(session=session, email=user_in.email)",
            "crud.authenticate(session=session, email=user_in.email, password='x')",
        ),
    ],
)
def test_unique_check_refuses_other_shapes(ctx, change):
    body = UPDATE_ME.replace(*change)
    assert body != UPDATE_ME
    assert unique_check.match(route(ctx, "update_user_me", body), ctx[0]) is None


# ---- delete --------------------------------------------------------------------------------

DELETE = READ_ITEM.replace(
    "    return item\n",
    """    session.delete(item)
    session.commit()
    return Message(message="Item deleted successfully")
""",
)


def test_delete_lifts_to_delete_and_a_message_row(ctx):
    f = delete.match(route(ctx, "delete_item"), ctx[0])
    assert f is not None and f.stmts == (3, 4, 5) and f.returns == "{#message}"
    assert [s.op for s in f.steps] == ["delete", "derive", "select"]
    assert f.steps[1].kwargs == (("message", '"Item deleted successfully"'),)


@pytest.mark.parametrize(
    "change",
    [
        ("    session.commit()\n", ""),
        ('Message(message="Item deleted successfully")', "Message(message=item.title)"),
        ("session.delete(item)", "session.delete(current_user)"),
    ],
)
def test_delete_refuses_other_shapes(ctx, change):
    body = DELETE.replace(*change)
    assert body != DELETE
    assert delete.match(route(ctx, "delete_item", body), ctx[0]) is None


# ---- lists ---------------------------------------------------------------------------------

READ_ITEMS = """
    if current_user.is_superuser:
        count_statement = select(func.count()).select_from(Item)
        count = session.exec(count_statement).one()
        statement = select(Item).order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
        items = session.exec(statement).all()
    else:
        count_statement = (
            select(func.count()).select_from(Item).where(Item.owner_id == current_user.id)
        )
        count = session.exec(count_statement).one()
        statement = (
            select(Item).where(Item.owner_id == current_user.id)
            .order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
        )
        items = session.exec(statement).all()
    items_public = [ItemPublic.model_validate(item) for item in items]
    return ItemsPublic(data=items_public, count=count)
"""


def test_page_list_lifts_the_owned_list(ctx):
    f = page_list.match(route(ctx, "read_items"), ctx[0])
    assert f is not None and f.success == "Page" and f.response == "ItemPublic"
    assert [(s.name, s.op) for s in f.steps] == [
        ("items", "fetch"),
        ("visible", "owned"),
        ("ordered", "sort"),
        ("result", "page"),
    ]
    assert [(p.name, p.annotation, p.default) for p in f.params] == [
        ("skip", "Int(ge=0)", "0"),
        ("limit", "Int(ge=0, le=100)", "100"),
    ]
    assert f.policies == (PageBound("skip", 0, None), PageBound("limit", 0, 100))


def test_page_list_accepts_inlined_queries_and_a_direct_envelope(ctx):
    body = READ_ITEMS.replace(
        "        count_statement = select(func.count()).select_from(Item)\n"
        "        count = session.exec(count_statement).one()\n",
        "        count = session.exec(select(func.count()).select_from(Item)).one()\n",
    ).replace(
        "    items_public = [ItemPublic.model_validate(item) for item in items]\n"
        "    return ItemsPublic(data=items_public, count=count)\n",
        "    return ItemsPublic(data=items, count=count)\n",
    )
    assert page_list.match(route(ctx, "read_items", body), ctx[0]) is not None


@pytest.mark.parametrize(
    "change",
    [
        # the branches list in different orders
        (
            "select(Item).order_by(col(Item.created_at).desc())",
            "select(Item).order_by(col(Item.title))",
        ),
        # the admin branch filtered too / the customer branch unfiltered
        (
            "select(func.count()).select_from(Item)\n",
            "select(func.count()).select_from(Item).where(Item.owner_id == current_user.id)\n",
        ),
        (
            ".where(Item.owner_id == current_user.id)\n            .order_by",
            "\n            .order_by",
        ),
        # a filter on another column
        (
            "select(Item).where(Item.owner_id == current_user.id)",
            "select(Item).where(Item.title == current_user.email)",
        ),
        # no offset
        ("desc()).offset(skip).limit(limit)\n        items", "desc()).limit(limit)\n        items"),
        # the count is not the envelope's
        ("count=count)", "count=len(items))"),
    ],
)
def test_page_list_refuses_other_shapes(ctx, change):
    body = READ_ITEMS.replace(*change)
    assert body != READ_ITEMS
    assert page_list.match(route(ctx, "read_items", body), ctx[0]) is None


def test_page_list_refuses_a_default_page_size_over_the_bound(ctx):
    src = route(ctx, "read_items")
    params = tuple(replace(p, default="500") if p.name == "limit" else p for p in src.route.params)
    assert page_list.match(route(ctx, "read_items", params=params), ctx[0]) is None


def test_admin_list_lifts_the_unfiltered_list_with_a_projection(ctx):
    f = admin_list.match(route(ctx, "read_users"), ctx[0])
    assert f is not None
    assert [(s.name, s.op) for s in f.steps] == [
        ("users", "fetch"),
        ("public", "select"),
        ("ordered", "sort"),
        ("result", "page"),
    ]
    # the users table has a private column: the response model's fields are selected
    assert "hashed_password" not in f.steps[1].kwargs[0][1]


def test_admin_list_is_not_the_owned_list(ctx):
    assert admin_list.match(route(ctx, "read_items"), ctx[0]) is None
    assert page_list.match(route(ctx, "read_users"), ctx[0]) is None


# ---- /me, returns --------------------------------------------------------------------------


def test_me_returns_the_caller_projected(ctx):
    f = me.match(route(ctx, "read_user_me"), ctx[0])
    assert f is not None and f.returns == "{#shown}"
    (step,) = f.steps
    assert step.op == "select" and step.args == ("{@current_user}",)


def test_me_refuses_anything_but_the_caller(ctx):
    assert me.match(route(ctx, "read_user_me", "return current_user.email"), ctx[0]) is None
    assert me.match(route(ctx, "read_item"), ctx[0]) is None


def test_returns_hands_back_a_held_row(ctx):
    f = returns.match(route(ctx, "read_item"), ctx[0])
    assert f is not None and f.steps == () and f.returns == "{@item}"
    assert returns.match(route(ctx, "read_user_me"), ctx[0]) is None  # the caller: me's


# ---- login --------------------------------------------------------------------------------


def test_login_lifts_through_the_authenticate_helper_and_natives(ctx):
    f = login.match(route(ctx, "login_access_token"), ctx[0])
    assert f is not None and f.endpoint == (("body", '"form"'),)
    exprs = [a for s in f.steps for a in (*s.args, *(v for _, v in s.kwargs))]
    assert 'native.verify_password(password, col("hashed_password"))' in exprs
    assert 'native.create_access_token(col("id"))' in exprs
    assert f.errors == ("BadRequest", "BadRequest")
    assert f.notes == (login.REHASH_NOTE,)  # authenticate's rehash write is not lifted


LOGIN = """
    user = crud.authenticate(session=session, email=form_data.username, password=form_data.password)
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    elif not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return Token(
        access_token=security.create_access_token(user.id, expires_delta=access_token_expires)
    )
"""


@pytest.mark.parametrize(
    "change",
    [
        # a lookup without password verification is not a login
        (
            "crud.authenticate(session=session, email=form_data.username, "
            "password=form_data.password)",
            "crud.get_user_by_email(session=session, email=form_data.username)",
        ),
        # no active check
        (
            "    elif not user.is_active:\n"
            '        raise HTTPException(status_code=400, detail="Inactive user")\n',
            "",
        ),
        # the token for something other than the account's key
        ("create_access_token(user.id,", "create_access_token(user.email,"),
        # a local the token call does not use
        ("expires_delta=access_token_expires", "expires_delta=None"),
    ],
)
def test_login_refuses_other_shapes(ctx, change):
    body = LOGIN.replace(*change)
    assert body != LOGIN
    assert login.match(route(ctx, "login_access_token", body), ctx[0]) is None


# ---- the composer -------------------------------------------------------------------------


def test_an_unclaimed_statement_rejects_the_route_with_its_line(ctx):
    body = READ_ITEM.replace("    return item\n", "    print(item)\n    return item\n")
    lift = lifted(ctx, "read_item", body)
    assert not lift.ok and lift.source is None
    assert lift.reason.startswith("no lifter matches the statements at line")


def test_an_unread_parameter_rejects_the_route(ctx):
    # read_user_me with a query parameter its body never reads: the original validates
    # it (a malformed value is its 422), the lift would not
    skip = next(p for p in route(ctx, "read_items").route.params if p.name == "skip")
    params = (*route(ctx, "read_user_me").route.params, skip)
    lift = lifted(ctx, "read_user_me", params=params)
    assert not lift.ok and "'skip' is not read" in lift.reason


def test_unclear_auth_is_never_lifted(ctx):
    lift = lifted(ctx, "read_item", auth="unclear", auth_reason="custom dependency")
    assert not lift.ok and lift.reason.startswith("auth is unclear")


def test_a_modifier_without_its_write_rejects_the_route(ctx):
    body = UPDATE_ME.replace(
        """    user_data = user_in.model_dump(exclude_unset=True)
    current_user.sqlmodel_update(user_data)
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
""",
        "",
    )
    lift = lifted(ctx, "update_user_me", body)
    assert not lift.ok


def test_a_composed_route_renders_one_endpoint(ctx):
    lift = lifted(ctx, "update_item")
    assert lift.ok and lift.lifters == ("get_or_404", "owner_check", "partial_update", "returns")
    assert lift.source is not None and lift.source.startswith(
        '@endpoint("PUT", "/api/v1/items/{id}", auth=Customer, response="ItemPublic")'
    )
    assert "-> One | NotFound | Forbidden:" in lift.source
    assert lift.source.rstrip().endswith("return updated")
