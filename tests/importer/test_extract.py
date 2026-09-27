"""lattice.importer.extract: one test group per extractor rule (spec 2026-09-27 importer
§3.1), each with small positive and negative source snippets, plus the template repo."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from lattice.importer.extract import RepoModel, extract

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PACKAGE = "bench.template_app.app"

MODELS = """
import uuid
from datetime import datetime

from pydantic import EmailStr
from sqlmodel import Field, Relationship, SQLModel


class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    notes: list["Note"] = Relationship(back_populates="owner")


class UserPublic(UserBase):
    id: uuid.UUID


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class NoteBase(SQLModel):
    body: str = Field(min_length=1, max_length=100)


class NoteCreate(NoteBase):
    pass


class Note(NoteBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = None
    owner_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)


class NotePublic(NoteBase):
    id: uuid.UUID
    owner_id: uuid.UUID


class NotesPublic(SQLModel):
    data: list[NotePublic]
    count: int


class Message(SQLModel):
    message: str
"""

CONFIG = """
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = "secret"
    DATABASE_URL: str = "sqlite://"


settings = Settings()
"""

DB = """
from sqlmodel import create_engine

from app.config import settings

engine = create_engine(str(settings.DATABASE_URL))
"""

SECURITY = """
from datetime import timedelta
from typing import Any

import jwt

from app.config import settings

ALGORITHM = "HS256"
OUTBOX: list[str] = []


def create_access_token(subject: str | Any, expires_delta: timedelta) -> str:
    return jwt.encode({"sub": str(subject)}, settings.SECRET_KEY, algorithm=ALGORITHM)


def verify_password(plain_password: str, hashed_password: str) -> tuple[bool, str | None]:
    return plain_password == hashed_password, None


def get_password_hash(password: str) -> str:
    return password


def send_email(*, email_to: str, subject: str = "") -> None:
    OUTBOX.append(email_to)
"""

DEPS = """
import uuid
from collections.abc import Generator
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from sqlmodel import Session

from app import security
from app.config import settings
from app.db import engine
from app.models import User

reusable_oauth2 = OAuth2PasswordBearer(tokenUrl="/login")


def get_db() -> Generator[Session]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_db)]
TokenDep = Annotated[str, Depends(reusable_oauth2)]


def get_current_user(session: SessionDep, token: TokenDep) -> User:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[security.ALGORITHM])
    except InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Could not validate")
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

NOTES = """
import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, func, select

from app import security
from app.config import settings
from app.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.models import Message, Note, NoteCreate, NotePublic, NotesPublic, User, UserCreate
from app.models import UserPublic, UserUpdateMe

router = APIRouter(prefix="/notes", tags=["notes"])


@router.get("/", response_model=NotesPublic)
def read_notes(
    session: SessionDep, current_user: CurrentUser, skip: int = 0, limit: int = 100
) -> Any:
    counting = select(func.count()).select_from(Note).where(Note.owner_id == current_user.id)
    count = session.exec(counting).one()
    mine = select(Note).where(col(Note.owner_id) == current_user.id)
    notes = session.exec(mine.offset(skip).limit(limit)).all()
    return NotesPublic(data=notes, count=count)


@router.get("/{id}", response_model=NotePublic)
def read_note(session: SessionDep, current_user: CurrentUser, id: uuid.UUID) -> Any:
    note = session.get(Note, id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    if not current_user.is_superuser and (note.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return note


@router.post("/", response_model=NotePublic)
def create_note(*, session: SessionDep, current_user: CurrentUser, note_in: NoteCreate) -> Any:
    note = Note.model_validate(note_in, update={"owner_id": current_user.id})
    session.add(note)
    session.commit()
    return note


@router.delete("/{id}", dependencies=[Depends(get_current_active_superuser)])
def delete_note(session: SessionDep, id: uuid.UUID) -> Message:
    note = session.get(Note, id)
    session.delete(note)
    return Message(message="deleted")


@router.post(
    "/users/", dependencies=[Depends(get_current_active_superuser)], response_model=UserPublic
)
def create_user(*, session: SessionDep, user_in: UserCreate) -> Any:
    hashed = security.get_password_hash(user_in.password)
    user = User.model_validate(user_in, update={"hashed_password": hashed})
    session.add(user)
    return user


@router.patch("/me", response_model=UserPublic)
def update_me(*, session: SessionDep, user_in: UserUpdateMe, current_user: CurrentUser) -> Any:
    ok, _ = security.verify_password("x", current_user.hashed_password)
    current_user.sqlmodel_update(user_in.model_dump(exclude_unset=True))
    return current_user


@router.post("/token")
def token(session: SessionDep, current_user: CurrentUser) -> Message:
    expires = timedelta(minutes=5)
    security.send_email(email_to=current_user.email)
    return Message(message=security.create_access_token(current_user.id, expires_delta=expires))
"""

MAIN = """
from fastapi import APIRouter, FastAPI

from app import notes
from app.config import settings

api_router = APIRouter()
api_router.include_router(notes.router)


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix=settings.API_V1_STR)
    return app
"""

BASE_FILES = {
    "models.py": MODELS,
    "config.py": CONFIG,
    "db.py": DB,
    "security.py": SECURITY,
    "deps.py": DEPS,
    "notes.py": NOTES,
    "main.py": MAIN,
}


def write_repo(root: Path, files: dict[str, str], package: str = "app") -> Path:
    pkg = root / package
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    for name, src in files.items():
        (pkg / name).write_text(textwrap.dedent(src))
    return root


def repo(tmp_path: Path, **overrides: str) -> RepoModel:
    files = {**BASE_FILES, **{f"{k}.py": v for k, v in overrides.items()}}
    return extract(write_repo(tmp_path, files), "app")


def by_name(items, name):
    for item in items:
        if item.name == name:
            return item
    raise KeyError(name)


# ---- tables -------------------------------------------------------------------------------


def test_table_columns_types_and_order(tmp_path: Path) -> None:
    model = repo(tmp_path)
    user = by_name(model.tables, "User")
    assert user.table_name == "user"
    assert [c.name for c in user.columns] == [
        "email",
        "is_active",
        "is_superuser",
        "full_name",
        "id",
        "hashed_password",
    ]  # base fields first; the Relationship is not a column
    types = {c.name: c.type for c in user.columns}
    assert types == {
        "email": "Email",
        "is_active": "Bool",
        "is_superuser": "Bool",
        "full_name": "Str",
        "id": "Uuid",
        "hashed_password": "Str",
    }
    note = by_name(model.tables, "Note")
    assert {c.name: c.type for c in note.columns}["created_at"] == "DateTime"
    assert [t.name for t in model.tables] == ["User", "Note"]


def test_table_nullability_bounds_unique_keys_fks(tmp_path: Path) -> None:
    model = repo(tmp_path)
    user = by_name(model.tables, "User")
    cols = {c.name: c for c in user.columns}
    assert cols["full_name"].nullable and not cols["email"].nullable
    assert not cols["id"].nullable and cols["id"].primary_key
    assert cols["email"].unique and cols["email"].max_length == 255
    assert not cols["full_name"].unique
    note = {c.name: c for c in by_name(model.tables, "Note").columns}
    assert note["body"].min_length == 1 and note["body"].max_length == 100
    assert note["owner_id"].foreign_key == "user.id" and not note["owner_id"].nullable
    assert note["created_at"].nullable
    assert note["id"].foreign_key is None


def test_explicit_tablename_and_nullable_override(tmp_path: Path) -> None:
    models = MODELS + textwrap.dedent(
        """
        class Tag(SQLModel, table=True):
            __tablename__ = "tags"
            id: int | None = Field(default=None, primary_key=True)
            label: str | None = Field(default=None, nullable=False, ge=0)
        """
    )
    model = repo(tmp_path, models=models)
    tag = by_name(model.tables, "Tag")
    assert tag.table_name == "tags"
    cols = {c.name: c for c in tag.columns}
    assert not cols["id"].nullable  # a primary key is never null
    assert not cols["label"].nullable


def test_non_table_models_are_not_tables(tmp_path: Path) -> None:
    model = repo(tmp_path)
    assert "UserPublic" not in [t.name for t in model.tables]
    public = by_name(model.models, "UserPublic")
    assert [f.name for f in public.fields] == [
        "email",
        "is_active",
        "is_superuser",
        "full_name",
        "id",
    ]
    envelope = by_name(model.models, "NotesPublic")
    assert [(f.name, f.type, f.item) for f in envelope.fields] == [
        ("data", "List", "NotePublic"),
        ("count", "Int", None),
    ]


# ---- dependencies -------------------------------------------------------------------------


def test_dependency_chain_is_classified(tmp_path: Path) -> None:
    model = repo(tmp_path)
    kinds = {d.name: d.kind for d in model.dependencies}
    assert kinds["get_db"] == "session"
    assert kinds["reusable_oauth2"] == "token"
    assert kinds["get_current_user"] == "current_user"
    assert kinds["get_current_active_superuser"] == "admin"
    current = by_name(model.dependencies, "get_current_user")
    assert current.returns == "User"
    assert (current.active_column, current.active_status, current.active_detail) == (
        "is_active",
        400,
        "Inactive user",
    )
    assert (403, "Could not validate") in [(r.status, r.detail) for r in current.raises]
    admin = by_name(model.dependencies, "get_current_active_superuser")
    assert (admin.admin_column, admin.admin_status, admin.admin_detail) == (
        "is_superuser",
        403,
        "Not a superuser",
    )
    token = by_name(model.dependencies, "reusable_oauth2")
    assert token.scheme == "OAuth2PasswordBearer"


def test_separate_active_dependency(tmp_path: Path) -> None:
    deps = DEPS.replace(
        "    if not user.is_active:\n"
        '        raise HTTPException(status_code=400, detail="Inactive user")\n',
        "",
    ) + textwrap.dedent(
        """
        def get_current_active_user(current_user: CurrentUser) -> User:
            if not current_user.is_active:
                raise HTTPException(status_code=401, detail="Account disabled")
            return current_user
        """
    )
    model = repo(tmp_path, deps=deps)
    active = by_name(model.dependencies, "get_current_active_user")
    assert active.kind == "active_user"
    assert (active.active_column, active.active_status, active.active_detail) == (
        "is_active",
        401,
        "Account disabled",
    )
    assert by_name(model.dependencies, "get_current_user").active_column is None


def test_unknown_custom_auth_dependency_is_unknown(tmp_path: Path) -> None:
    deps = DEPS + textwrap.dedent(
        """
        def require_api_key(x_api_key: str) -> None:
            if x_api_key != "k":
                raise HTTPException(status_code=401, detail="bad key")
        """
    )
    notes = NOTES + textwrap.dedent(
        """
        from app.deps import require_api_key


        @router.get("/public-ish", dependencies=[Depends(require_api_key)])
        def keyed(session: SessionDep) -> Message:
            return Message(message="hi")
        """
    )
    model = repo(tmp_path, deps=deps, notes=notes)
    assert by_name(model.dependencies, "require_api_key").kind == "unknown"
    route = by_name(model.routes, "keyed")
    assert route.auth == "unclear"
    assert "require_api_key" in route.auth_reason


# ---- routes -------------------------------------------------------------------------------


def test_route_method_path_params_and_response(tmp_path: Path) -> None:
    model = repo(tmp_path)
    read_notes = by_name(model.routes, "read_notes")
    assert (read_notes.method, read_notes.path) == ("GET", "/api/v1/notes/")
    assert read_notes.response_model == "NotesPublic"
    assert read_notes.auth == "Customer"
    params = {p.name: p for p in read_notes.params}
    assert (params["skip"].source, params["skip"].type, params["skip"].default) == (
        "query",
        "Int",
        "0",
    )
    assert params["limit"].default == "100"
    assert params["current_user"].source == "dependency"
    assert params["current_user"].dependency == "get_current_user"
    assert params["session"].dependency == "get_db"
    read_note = by_name(model.routes, "read_note")
    assert read_note.path == "/api/v1/notes/{id}"
    assert {p.name: (p.source, p.type) for p in read_note.params}["id"] == ("path", "Uuid")
    assert [(r.status, r.detail) for r in read_note.raises] == [
        (404, "Note not found"),
        (403, "Not enough permissions"),
    ]
    create = by_name(model.routes, "create_note")
    body = {p.name: p for p in create.params}["note_in"]
    assert (body.source, body.type) == ("body", "NoteCreate")
    assert create.body_models == ("NoteCreate",)
    token = by_name(model.routes, "token")
    assert token.response_model == "Message"  # from the return annotation


def test_route_auth_from_decorator_dependencies(tmp_path: Path) -> None:
    model = repo(tmp_path)
    delete = by_name(model.routes, "delete_note")
    assert delete.auth == "Admin"
    assert "get_current_active_superuser" in delete.dependencies
    assert delete.method == "DELETE"


def test_route_without_auth_is_public(tmp_path: Path) -> None:
    notes = NOTES + textwrap.dedent(
        """
        @router.get("/health")
        async def health() -> bool:
            return True
        """
    )
    route = by_name(repo(tmp_path, notes=notes).routes, "health")
    assert route.auth == "Public" and route.response_model is None


def test_router_level_unknown_dependency_is_unclear(tmp_path: Path) -> None:
    notes = NOTES.replace(
        'router = APIRouter(prefix="/notes", tags=["notes"])',
        'def audit() -> None:\n    pass\n\n\nrouter = APIRouter(prefix="/notes", '
        "dependencies=[Depends(audit)])",
    )
    model = repo(tmp_path, notes=notes)
    assert all(r.auth == "unclear" for r in model.routes)


def test_owner_uses_are_recorded(tmp_path: Path) -> None:
    model = repo(tmp_path)
    uses = {(r.name, u.table, u.column) for r in model.routes for u in r.owner_uses}
    assert ("read_notes", "Note", "owner_id") in uses  # Note.owner_id == current_user.id
    assert ("read_note", "Note", "owner_id") in uses  # note.owner_id != current_user.id
    assert ("create_note", "Note", "owner_id") in uses  # update={"owner_id": ...}
    assert not any(r == "delete_note" for r, _, _ in uses)
    assert "Note" in by_name(model.routes, "delete_note").tables


def test_owner_use_through_an_other_column(tmp_path: Path) -> None:
    notes = NOTES + textwrap.dedent(
        """
        @router.get("/mine")
        def mine(session: SessionDep, current_user: CurrentUser) -> Message:
            n = session.get(Note, current_user.id)
            if n.created_by != current_user.id:
                raise HTTPException(status_code=403, detail="no")
            return Message(message="x")
        """
    )
    route = by_name(repo(tmp_path, notes=notes).routes, "mine")
    assert [(u.table, u.column) for u in route.owner_uses] == [("Note", "created_by")]


# ---- natives -------------------------------------------------------------------------------


def test_native_candidates(tmp_path: Path) -> None:
    model = repo(tmp_path)
    natives = {n.name: n for n in model.natives}
    assert set(natives) >= {
        "get_password_hash",
        "verify_password",
        "create_access_token",
        "send_email",
        "decode_token",
    }
    hash_ = natives["get_password_hash"]
    assert hash_.origin == "app.security:get_password_hash"
    assert [(p.name, p.type) for p in hash_.params] == [("password", "Str")]
    assert (hash_.result_type, hash_.effect, hash_.expressible) == ("Str", False, True)
    verify = natives["verify_password"]
    assert verify.result_type == "Bool" and verify.adapter == "first"
    assert "hashed_password" in verify.params[1].passed_columns
    token = natives["create_access_token"]
    assert [(p.name, p.type, p.bound) for p in token.params] == [
        ("subject", "Uuid", None),  # `str | Any`, typed from the call sites (x.id)
        ("expires_delta", None, "timedelta(minutes=5)"),  # bound: built from constants
    ]
    assert token.expressible
    send = natives["send_email"]
    assert send.effect and send.result_type is None
    assert [p.name for p in send.params] == ["email_to", "subject"]
    decode = natives["decode_token"]
    assert decode.kind == "token_decoder"
    assert decode.origin == "app.deps:get_current_user"
    assert (decode.result_type, decode.nullable, decode.claim) == ("Uuid", True, "sub")
    assert "jwt.decode(" in decode.decode_expr


def test_orm_functions_are_not_natives(tmp_path: Path) -> None:
    crud = """
    from sqlmodel import Session, select

    from app.models import User
    from app.security import verify_password


    def authenticate(*, session: Session, email: str, password: str) -> User | None:
        user = session.exec(select(User).where(User.email == email)).first()
        if user and verify_password(password, user.hashed_password)[0]:
            return user
        return None
    """
    notes = NOTES + textwrap.dedent(
        """
        from app import crud


        @router.post("/login")
        def login(session: SessionDep, email: str, password: str) -> Message:
            user = crud.authenticate(session=session, email=email, password=password)
            return Message(message=str(user))
        """
    )
    model = repo(tmp_path, crud=crud, notes=notes)
    names = {n.name for n in model.natives}
    assert "authenticate" not in names
    assert "verify_password" in names  # reached through the ORM function


def test_effect_heuristic(tmp_path: Path) -> None:
    security = SECURITY + textwrap.dedent(
        """
        def notify_admin(text: str) -> bool:
            print(text)
            return True


        def slugify(text: str) -> str:
            return text.lower()
        """
    )
    notes = NOTES + textwrap.dedent(
        """
        @router.post("/slug")
        def slug(session: SessionDep, text: str) -> Message:
            security.notify_admin(text)
            return Message(message=security.slugify(text))
        """
    )
    natives = {n.name: n for n in repo(tmp_path, security=security, notes=notes).natives}
    assert natives["notify_admin"].effect  # by name
    assert not natives["slugify"].effect


def test_non_scalar_native_is_not_expressible(tmp_path: Path) -> None:
    security = SECURITY + textwrap.dedent(
        """
        def make_pair(text: str) -> dict[str, str]:
            return {"a": text}
        """
    )
    notes = NOTES + textwrap.dedent(
        """
        @router.post("/pair")
        def pair(session: SessionDep, text: str) -> Message:
            return Message(message=str(security.make_pair(text)))
        """
    )
    native = by_name(repo(tmp_path, security=security, notes=notes).natives, "make_pair")
    assert not native.expressible and "dict" in native.reason


# ---- settings, dialect, serialisation ------------------------------------------------------


def test_dialect_and_defaults(tmp_path: Path) -> None:
    model = repo(tmp_path)
    assert model.dialect == "sqlite"
    assert model.database_url == "sqlite://"
    assert model.exception_handlers == ()
    assert model.custom_serialization == ()


def test_custom_exception_handler_is_recorded(tmp_path: Path) -> None:
    main = MAIN.replace(
        "    return app",
        "    app.add_exception_handler(ValueError, lambda r, e: None)\n    return app",
    )
    assert repo(tmp_path, main=main).exception_handlers != ()


def test_postgres_dialect(tmp_path: Path) -> None:
    config = CONFIG.replace('"sqlite://"', '"postgresql://u:p@h/db"')
    assert repo(tmp_path, config=config).dialect == "postgresql"


def test_repo_model_is_json_serialisable(tmp_path: Path) -> None:
    model = repo(tmp_path)
    data = json.loads(model.to_json())
    assert data["app_package"] == "app"
    assert {t["name"] for t in data["tables"]} == {"User", "Note"}


def test_repo_model_is_frozen(tmp_path: Path) -> None:
    model = repo(tmp_path)
    with pytest.raises(AttributeError):
        model.app_package = "x"  # type: ignore[misc]


# ---- the template ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def template() -> RepoModel:
    return extract(REPO_ROOT, TEMPLATE_PACKAGE)


def test_template_tables(template: RepoModel) -> None:
    assert [t.name for t in template.tables] == ["User", "Item"]
    item = by_name(template.tables, "Item")
    assert [c.name for c in item.columns] == [
        "title",
        "description",
        "id",
        "created_at",
        "owner_id",
    ]


def test_template_routes(template: RepoModel) -> None:
    paths = {(r.method, r.path): r for r in template.routes}
    assert ("POST", "/api/v1/login/access-token") in paths
    assert ("GET", "/api/v1/items/{id}") in paths
    assert ("PATCH", "/api/v1/users/me") in paths
    assert ("POST", "/api/v1/private/users/") in paths
    assert paths[("GET", "/api/v1/users/")].auth == "Admin"
    assert paths[("GET", "/api/v1/items/")].auth == "Customer"
    assert paths[("POST", "/api/v1/login/access-token")].auth == "Public"
    login = paths[("POST", "/api/v1/login/access-token")]
    form = {p.name: p for p in login.params}["form_data"]
    assert form.source == "form"
    assert all(r.auth != "unclear" for r in template.routes)
    assert len(template.routes) == 23


def test_template_natives(template: RepoModel) -> None:
    natives = {n.name: n for n in template.natives}
    assert natives["create_access_token"].origin == (
        "bench.template_app.app.core.security:create_access_token"
    )
    assert natives["verify_password"].adapter == "first"
    assert natives["send_email"].effect
    assert not natives["generate_new_account_email"].expressible
    assert natives["decode_token"].origin == "bench.template_app.app.api.deps:get_current_user"
    assert "authenticate" not in natives
