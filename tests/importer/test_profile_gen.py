"""lattice.importer.profile_gen: RepoModel -> profile source + unclear decisions.

The gate (plan node I1): the profile generated from ``bench/template_app`` is equivalent
to the hand-written ``bench/template_profile.template_profile()`` on every field that
matters, and the hand-written template programs check against it with no diagnostics.

Intentional differences from the hand-written profile (asserted below):

* natives keep the original function names: ``get_password_hash`` (the reference calls
  its wrapper ``hash_password``);
* natives the reference leaves out because no hand-written program needs them are
  present: ``generate_password_reset_token``, ``verify_password_reset_token`` (pure) and
  ``send_email`` (an effect native).
"""

from __future__ import annotations

import textwrap
import uuid
from pathlib import Path
from types import ModuleType

import pytest

from bench.template_profile import template_profile, template_sources
from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.importer.extract import extract
from lattice.importer.profile_gen import ProfileResult, Unclear, generate_profile
from lattice.profile import AdminFlag, NativeSig, NativeToken, Profile
from lattice.typesys import Scalar

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PACKAGE = "bench.template_app.app"

# ---- snippet repos (shared shape with test_extract) ---------------------------------------

MODELS = """
import uuid

from pydantic import EmailStr
from sqlmodel import Field, SQLModel


class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False


class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str


class UserPublic(UserBase):
    id: uuid.UUID


class Note(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    body: str = Field(max_length=100)
    owner_id: uuid.UUID = Field(foreign_key="user.id")
    created_by: uuid.UUID | None = Field(default=None, foreign_key="user.id")


class NotePublic(SQLModel):
    id: uuid.UUID
    body: str
    owner_id: uuid.UUID


class Message(SQLModel):
    message: str
"""

DEPS = """
import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from app.models import User

reusable_oauth2 = OAuth2PasswordBearer(tokenUrl="/login")


def get_db() -> Session:
    return Session()


SessionDep = Annotated[Session, Depends(get_db)]
TokenDep = Annotated[str, Depends(reusable_oauth2)]


def get_current_user(session: SessionDep, token: TokenDep) -> User:
    payload = jwt.decode(token, "k", algorithms=["HS256"])
    user = session.get(User, uuid.UUID(payload["sub"]))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_active_superuser(current_user: CurrentUser) -> User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not a superuser")
    return current_user
"""

ROUTES_OWNER_ID = """
import uuid

from fastapi import APIRouter, HTTPException

from app.deps import CurrentUser, SessionDep
from app.models import Message, Note, NotePublic, UserPublic

router = APIRouter(prefix="/notes")


@router.get("/{id}", response_model=NotePublic)
def read_note(session: SessionDep, current_user: CurrentUser, id: uuid.UUID) -> Note:
    note = session.get(Note, id)
    if note.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="no")
    return note


@router.get("/me", response_model=UserPublic)
def me(current_user: CurrentUser) -> UserPublic:
    return current_user
"""

MAIN = """
import sqlmodel

from fastapi import FastAPI

from app import routes

engine = sqlmodel.create_engine("sqlite://")
app = FastAPI()
app.include_router(routes.router)
"""


def write_repo(root: Path, files: dict[str, str]) -> Path:
    pkg = root / "app"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    for name, src in files.items():
        (pkg / f"{name}.py").write_text(textwrap.dedent(src))
    return root


def generate(tmp_path: Path, **overrides: str) -> ProfileResult:
    files = {"models": MODELS, "deps": DEPS, "routes": ROUTES_OWNER_ID, "main": MAIN}
    files.update(overrides)
    return generate_profile(extract(write_repo(tmp_path, files), "app"))


def topics(result: ProfileResult) -> set[tuple[str, str]]:
    return {(u.topic, u.subject) for u in result.unclear}


def load(result: ProfileResult, name: str = "generated_profile") -> ModuleType:
    module = ModuleType(name)
    exec(compile(result.source, f"<{name}>", "exec"), module.__dict__)
    return module


# ---- owner inference (review focus #1) -----------------------------------------------------


def test_two_user_foreign_keys_make_the_owner_unclear(tmp_path: Path) -> None:
    result = generate(tmp_path)
    assert ("owner", "Note") in topics(result)
    unclear = next(u for u in result.unclear if u.topic == "owner")
    assert set(unclear.candidates) == {"owner_id", "created_by"}
    note = load(result).profile().api.tables["Note"]
    assert note.owner is None  # left unset: the checker demands it


def test_single_user_fk_compared_by_every_route_is_the_owner(tmp_path: Path) -> None:
    models = MODELS.replace(
        '    created_by: uuid.UUID | None = Field(default=None, foreign_key="user.id")\n', ""
    )
    result = generate(tmp_path, models=models)
    assert ("owner", "Note") not in topics(result)
    assert load(result).profile().api.tables["Note"].owner == "owner_id"


def test_owner_unclear_when_a_route_compares_another_column(tmp_path: Path) -> None:
    models = MODELS.replace(
        '    created_by: uuid.UUID | None = Field(default=None, foreign_key="user.id")\n',
        "    created_by: uuid.UUID | None = None\n",
    )
    routes = ROUTES_OWNER_ID + textwrap.dedent(
        """
        @router.delete("/{id}")
        def delete_note(session: SessionDep, current_user: CurrentUser, id: uuid.UUID) -> Message:
            note = session.get(Note, id)
            if note.created_by != current_user.id:
                raise HTTPException(status_code=403, detail="no")
            return Message(message="ok")
        """
    )
    result = generate(tmp_path, models=models, routes=routes)
    assert ("owner", "Note") in topics(result)
    assert load(result).profile().api.tables["Note"].owner is None


def test_a_customer_route_returning_rows_unchecked_makes_the_table_shared(
    tmp_path: Path,
) -> None:
    """An authenticated non-admin route returning the table's rows without an owner
    check: the table is shared (a decision, recorded with the route), not owned."""
    models = MODELS.replace(
        '    created_by: uuid.UUID | None = Field(default=None, foreign_key="user.id")\n', ""
    )
    routes = ROUTES_OWNER_ID + textwrap.dedent(
        """
        @router.get("/any/{id}", response_model=NotePublic)
        def read_any(session: SessionDep, current_user: CurrentUser, id: uuid.UUID) -> Note:
            return session.get(Note, id)
        """
    )
    result = generate(tmp_path, models=models, routes=routes)
    assert ("owner", "Note") not in topics(result)
    assert result.ownership["Note"] == "shared"
    assert "read_any" in result.ownership_reasons["Note"]
    assert load(result).profile().api.tables["Note"].owner is None


def test_user_fk_never_compared_but_its_rows_returned_is_shared(tmp_path: Path) -> None:
    models = MODELS.replace(
        '    created_by: uuid.UUID | None = Field(default=None, foreign_key="user.id")\n', ""
    )
    routes = ROUTES_OWNER_ID.replace("if note.owner_id != current_user.id:", "if not note:")
    result = generate(tmp_path, models=models, routes=routes)
    assert result.ownership["Note"] == "shared"
    assert load(result).profile().api.tables["Note"].owner is None


# ---- auth (review focus #2) -----------------------------------------------------------------


def test_unknown_custom_auth_dependency_is_unclear_never_public(tmp_path: Path) -> None:
    routes = ROUTES_OWNER_ID + textwrap.dedent(
        """
        from fastapi import Depends


        def check_signature(x_signature: str) -> None:
            if x_signature != "ok":
                raise HTTPException(status_code=401, detail="bad signature")


        @router.get("/signed", dependencies=[Depends(check_signature)])
        def signed(session: SessionDep) -> Message:
            return Message(message="hi")
        """
    )
    result = generate(tmp_path, routes=routes)
    assert ("route_auth", "signed") in topics(result)
    reason = next(u.reason for u in result.unclear if u.subject == "signed")
    assert "check_signature" in reason


def test_no_token_decoder_makes_auth_unclear(tmp_path: Path) -> None:
    deps = DEPS.replace('payload = jwt.decode(token, "k", algorithms=["HS256"])', "payload = {}")
    result = generate(tmp_path, deps=deps)
    assert ("auth", "User") in topics(result)


# ---- private, roles, page shape, representation ---------------------------------------------


def test_private_columns_are_those_no_response_returns(tmp_path: Path) -> None:
    api = load(generate(tmp_path)).profile().api
    assert api.tables["User"].private == frozenset({"hashed_password"})
    assert api.tables["Note"].private == frozenset({"created_by"})


def test_roles_active_and_auth(tmp_path: Path) -> None:
    api = load(generate(tmp_path)).profile().api
    assert api.roles == frozenset({"Public", "Customer", "Admin"})
    assert api.roles_from == AdminFlag("is_superuser", "Customer")
    assert api.active_column == "is_active"
    assert (api.inactive_status, api.inactive_detail) == (400, "Inactive user")
    assert api.forbidden_detail == "Not a superuser"
    assert api.unauthorized_detail == "Not authenticated"
    assert api.auth == NativeToken("decode_token")
    assert api.natives["decode_token"] == NativeSig((Scalar.STR,), Scalar.UUID, nullable=True)
    assert api.users_table == "User"


def test_no_envelope_keeps_the_default_page_shape(tmp_path: Path) -> None:
    api = load(generate(tmp_path)).profile().api
    assert api.page_shape == {"items": "items", "limit": "limit", "offset": "offset"}
    assert api.responses == {
        "NotePublic": ("id", "body", "owner_id"),
        "UserPublic": ("email", "is_active", "is_superuser", "id"),
    }  # Message is no route's response here


def test_custom_exception_handler_makes_error_body_unclear(tmp_path: Path) -> None:
    main = MAIN + "app.add_exception_handler(ValueError, lambda r, e: None)\n"
    result = generate(tmp_path, main=main)
    assert ("error_body", "app") in topics(result)


def test_non_sqlite_dialect_makes_storage_unclear(tmp_path: Path) -> None:
    main = MAIN.replace('"sqlite://"', '"postgresql://u:p@h/db"')
    result = generate(tmp_path, main=main)
    assert ("storage", "app") in topics(result)


def test_a_sqlite_source_keeps_sqlites_null_order(tmp_path: Path) -> None:
    assert load(generate(tmp_path)).profile().api.null_order == "smallest"


def test_a_non_sqlite_source_keeps_the_default_null_order(tmp_path: Path) -> None:
    main = MAIN.replace('"sqlite://"', '"postgresql://u:p@h/db"')
    result = generate(tmp_path, main=main)
    assert "null_order" not in result.source


def test_unclear_is_a_value() -> None:
    u = Unclear("owner", "Note", "why", ("a", "b"))
    assert u.candidates == ("a", "b")


# ---- the template gate -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generated() -> ProfileResult:
    return generate_profile(extract(REPO_ROOT, TEMPLATE_PACKAGE))


@pytest.fixture(scope="module")
def module(generated: ProfileResult) -> ModuleType:
    return load(generated, "generated_template_profile")


@pytest.fixture(scope="module")
def profile(module: ModuleType) -> Profile:
    return module.profile()


def test_template_has_no_unclear_decisions(generated: ProfileResult) -> None:
    assert generated.unclear == []


def test_template_schemas_equal_the_reference(profile: Profile) -> None:
    ref = template_profile().api
    api = profile.api
    assert ref is not None and api is not None
    assert dict(api.tables) == dict(ref.tables)  # names, columns, types, order
    for name, schema in ref.tables.items():
        got = api.tables[name]
        assert got.keys == schema.keys, name
        assert got.nullable == schema.nullable, name
        assert got.owner == schema.owner, name
        assert got.private == schema.private, name
        assert got.admin_write == schema.admin_write, name
        assert got.owner_via == schema.owner_via, name
        assert got.public_group == schema.public_group, name
    assert dict(api.table_names) == dict(ref.table_names)
    assert api.users_table == ref.users_table


def test_template_settings_equal_the_reference(profile: Profile) -> None:
    ref = template_profile().api
    api = profile.api
    assert ref is not None and api is not None
    for field in (
        "roles",
        "admin_role",
        "page_shape",
        "responses",
        "auth",
        "backend",
        "roles_from",
        "uuid_storage",
        "datetime_storage",
        "active_column",
        "inactive_status",
        "inactive_detail",
        "error_body",
        "unauthorized_detail",
        "forbidden_detail",
        "datetime_wire",
        "null_order",
    ):
        assert getattr(api, field) == getattr(ref, field), field
    assert profile.schemas == {} and profile.version == "v1"


RENAMED = {"hash_password": "get_password_hash"}
EXTRA = {
    "generate_password_reset_token": NativeSig((Scalar.STR,), Scalar.STR),
    "verify_password_reset_token": NativeSig((Scalar.STR,), Scalar.STR, nullable=True),
    "send_email": NativeSig((Scalar.STR, Scalar.STR, Scalar.STR), effect=True),
}


def test_template_natives_equal_the_reference_up_to_listed_differences(
    profile: Profile,
) -> None:
    ref = template_profile().api
    api = profile.api
    assert ref is not None and api is not None
    expected = {RENAMED.get(name, name): sig for name, sig in ref.natives.items()} | EXTRA
    assert dict(api.natives) == expected


def test_template_native_wrappers_behave_like_the_reference(module: ModuleType) -> None:
    from bench import template_profile as ref

    natives = module.NATIVES
    assert set(natives) == set(EXTRA) | {
        "get_password_hash",
        "verify_password",
        "create_access_token",
        "decode_token",
    }
    hashed = natives["get_password_hash"]("secret-password")
    assert natives["verify_password"]("secret-password", hashed) is True
    assert natives["verify_password"]("wrong-password", hashed) is False
    assert ref.verify_password("secret-password", hashed) is True
    user_id = uuid.uuid4()
    token = natives["create_access_token"](user_id)
    assert natives["decode_token"](token) == user_id
    assert ref.decode_token(token) == user_id
    assert natives["decode_token"](ref.create_access_token(user_id)) == user_id
    assert natives["decode_token"]("not-a-token") is None
    assert natives["verify_password_reset_token"]("bad") is None
    reset = natives["generate_password_reset_token"]("a@example.com")
    assert natives["verify_password_reset_token"](reset) == "a@example.com"


@pytest.mark.parametrize("name", ["login.lattice.py", "items.lattice.py", "users.lattice.py"])
def test_template_programs_check_identically(profile: Profile, name: str) -> None:
    source = template_sources()[name]
    got = check_source(source, profile)
    ref = check_source(source, template_profile())
    # no errors; the warnings (items: L823 x3, L833 -- the documented policy deviations)
    # are exactly the reference profile's
    assert [d.render() for d in got.diagnostics if d.severity is Severity.ERROR] == []
    assert [d.render() for d in got.diagnostics] == [d.render() for d in ref.diagnostics]
    assert got.ok


def test_template_joined_program_checks(profile: Profile) -> None:
    from bench.template_profile import template_program

    result = check_source(template_program(), profile)
    assert result.ok, [d.render() for d in result.diagnostics]


def test_template_profile_source_is_importable_text(generated: ProfileResult) -> None:
    assert "def profile() -> Profile:" in generated.source
    assert "bench.template_app.app.core.security" in generated.source
    compile(generated.source, "<profile>", "exec")
