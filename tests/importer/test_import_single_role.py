"""End to end on a synthetic single-role app (plan: the held-out import gaps, closed
generically): no admin flag and no role column (``roles_from=NoRoles()``), a JWT whose
subject is the unique ``username`` (``NativeToken(lookup="username")``), a decode that
reads its secret from ``request.app.state.settings`` (a NativeContext setting whose
value is read from the original app), a login minting tokens with that secret (no
single-argument mint: the Lattice side logs in through the original's own login), and a
``StrEnum`` column written from request bodies (a ``OneOf`` column and parameters).

The app is written to a temporary directory (its own package and SQLAlchemy registry),
never the held-out repos' code.
"""

from __future__ import annotations

import importlib
import sys
import textwrap

import pytest

from lattice.importer.lift import ImportResult, lift_repo
from lattice.profile import NativeToken, NoRoles

PACKAGE = "solo_single_role"

FILES = {
    "__init__": """
        from collections.abc import Generator
        from dataclasses import dataclass
        from pathlib import Path

        from fastapi import FastAPI
        from sqlmodel import Session, create_engine

        from PKG import deps
        from PKG.models import registry
        from PKG.routes import router


        @dataclass(frozen=True)
        class Settings:
            secret_key: str = "default-secret-never-used-in-tests-0000"
            minutes: int = 30


        def make_app(db_path, secret_key="factory-secret-key-for-the-tests-000000"):
            engine = create_engine(
                f"sqlite:///{Path(db_path)}", connect_args={"check_same_thread": False}
            )
            registry.metadata.create_all(engine)

            def get_db() -> Generator[Session]:
                with Session(engine) as session:
                    yield session

            app = FastAPI()
            app.state.settings = Settings(secret_key=secret_key)
            app.include_router(router)
            app.dependency_overrides[deps.get_db] = get_db
            return app
    """,
    "models": """
        from enum import StrEnum

        from sqlalchemy.orm import registry as _registry
        from sqlmodel import Field, SQLModel

        registry = _registry()


        class TableBase(SQLModel, registry=registry):
            pass


        class Shelf(StrEnum):
            UNREAD = "unread"
            READING = "reading"
            READ = "read"


        class User(TableBase, table=True):
            __tablename__ = "users"

            id: int | None = Field(default=None, primary_key=True)
            username: str = Field(unique=True, max_length=50)
            is_active: bool = True
            hashed_password: str


        class UserPublic(SQLModel):
            id: int
            username: str


        class UserRegister(SQLModel):
            username: str = Field(max_length=50)
            password: str = Field(min_length=8, max_length=40)


        class BookBase(SQLModel):
            title: str = Field(min_length=1, max_length=100)
            shelf: Shelf = Shelf.UNREAD


        class BookCreate(BookBase):
            pass


        class BookUpdate(SQLModel):
            title: str | None = Field(default=None, min_length=1, max_length=100)
            shelf: Shelf | None = None


        class Book(BookBase, TableBase, table=True):
            __tablename__ = "books"

            id: int | None = Field(default=None, primary_key=True)
            owner_id: int = Field(foreign_key="users.id")


        class BookPublic(BookBase):
            id: int
            owner_id: int


        class Token(SQLModel):
            access_token: str
            token_type: str = "bearer"
    """,
    "security": """
        from datetime import UTC, datetime, timedelta

        import jwt


        def create_access_token(subject: str, secret_key: str, minutes: int) -> str:
            expire = datetime.now(UTC) + timedelta(minutes=minutes)
            return jwt.encode({"exp": expire, "sub": subject}, secret_key, algorithm="HS256")


        def get_password_hash(password: str) -> str:
            return "hashed:" + password


        def verify_password(plain: str, hashed: str) -> bool:
            return hashed == "hashed:" + plain
    """,
    "deps": """
        from collections.abc import Generator
        from typing import Annotated

        import jwt
        from fastapi import Depends, HTTPException, Request
        from fastapi.security import OAuth2PasswordBearer
        from jwt.exceptions import InvalidTokenError
        from sqlmodel import Session, select

        from PKG.models import User

        oauth2 = OAuth2PasswordBearer(tokenUrl="/login")


        def get_db() -> Generator[Session]:
            raise NotImplementedError  # replaced by make_app


        SessionDep = Annotated[Session, Depends(get_db)]


        def get_current_user(
            request: Request, session: SessionDep, token: Annotated[str, Depends(oauth2)]
        ) -> User:
            credentials_exception = HTTPException(
                status_code=401, detail="Could not validate credentials"
            )
            try:
                payload = jwt.decode(
                    token, request.app.state.settings.secret_key, algorithms=["HS256"]
                )
            except InvalidTokenError:
                raise credentials_exception
            username = payload.get("sub")
            user = session.exec(select(User).where(User.username == username)).first()
            if user is None or not user.is_active:
                raise credentials_exception
            return user


        CurrentUser = Annotated[User, Depends(get_current_user)]
    """,
    "routes": """
        from typing import Annotated, Any

        from fastapi import APIRouter, Depends, HTTPException, Request
        from fastapi.security import OAuth2PasswordRequestForm
        from sqlmodel import select

        from PKG import security
        from PKG.deps import CurrentUser, SessionDep
        from PKG.models import (
            Book, BookCreate, BookPublic, BookUpdate, Token, User, UserPublic, UserRegister,
        )

        router = APIRouter()


        @router.post("/login")
        def login(
            request: Request,
            session: SessionDep,
            form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
        ) -> Token:
            user = session.exec(select(User).where(User.username == form_data.username)).first()
            if not user or not security.verify_password(form_data.password, user.hashed_password):
                raise HTTPException(status_code=400, detail="Incorrect username or password")
            settings = request.app.state.settings
            token = security.create_access_token(
                user.username, settings.secret_key, settings.minutes
            )
            return Token(access_token=token)


        @router.post("/users/signup", response_model=UserPublic)
        def register_user(session: SessionDep, user_in: UserRegister) -> Any:
            user = User(
                username=user_in.username,
                hashed_password=security.get_password_hash(user_in.password),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user


        @router.get("/users/me", response_model=UserPublic)
        def read_user_me(current_user: CurrentUser) -> Any:
            return current_user


        @router.get("/books/{id}", response_model=BookPublic)
        def read_book(session: SessionDep, current_user: CurrentUser, id: int) -> Any:
            book = session.get(Book, id)
            if not book:
                raise HTTPException(status_code=404, detail="Book not found")
            if book.owner_id != current_user.id:
                raise HTTPException(status_code=403, detail="Not your book")
            return book


        @router.post("/books/", response_model=BookPublic)
        def create_book(
            *, session: SessionDep, current_user: CurrentUser, book_in: BookCreate
        ) -> Any:
            book = Book.model_validate(book_in, update={"owner_id": current_user.id})
            session.add(book)
            session.commit()
            session.refresh(book)
            return book


        @router.put("/books/{id}", response_model=BookPublic)
        def update_book(
            *, session: SessionDep, current_user: CurrentUser, id: int, book_in: BookUpdate
        ) -> Any:
            book = session.get(Book, id)
            if not book:
                raise HTTPException(status_code=404, detail="Book not found")
            if book.owner_id != current_user.id:
                raise HTTPException(status_code=403, detail="Not your book")
            book.sqlmodel_update(book_in.model_dump(exclude_unset=True))
            session.add(book)
            session.commit()
            session.refresh(book)
            return book
    """,
}


@pytest.fixture(scope="module")
def imported(tmp_path_factory: pytest.TempPathFactory) -> ImportResult:
    root = tmp_path_factory.mktemp("single_role")
    pkg = root / PACKAGE
    pkg.mkdir()
    for name, src in FILES.items():
        (pkg / f"{name}.py").write_text(textwrap.dedent(src).replace("PKG", PACKAGE))
    sys.path.insert(0, str(root))
    try:
        factory = importlib.import_module(PACKAGE).make_app
        yield lift_repo(root, PACKAGE, factory, hypothesis_bodies=2)
    finally:
        sys.path.remove(str(root))
        for name in [m for m in sys.modules if m == PACKAGE or m.startswith(PACKAGE + ".")]:
            del sys.modules[name]


def test_the_profile_is_single_role_with_a_username_lookup(imported: ImportResult) -> None:
    api = imported.lattice_profile.api
    assert api is not None
    assert api.roles_from == NoRoles("Customer")
    credentials = (401, "Could not validate credentials")
    assert api.auth == NativeToken(
        "decode_token", lookup="username", invalid=credentials, unknown=credentials
    )
    assert ("username",) in api.tables["User"].keys
    assert api.natives["decode_token"].settings == ("secret_key",)
    assert not [u for u in imported.unclear if "single-role" in u.reason]


def test_the_setting_is_read_from_the_original_app(imported: ImportResult) -> None:
    """The factory's secret, not the settings class default: tokens the original signs
    decode on the Lattice side."""
    assert imported.context is not None
    assert imported.context.settings == {"secret_key": "factory-secret-key-for-the-tests-000000"}
    assert "ctx['secret_key']" in imported.profile.source


def test_the_enum_column_is_one_of_and_its_parameters_too(imported: ImportResult) -> None:
    api = imported.lattice_profile.api
    assert api is not None
    assert api.tables["Book"].choices("shelf") == ("unread", "reading", "read")
    source = imported.result("create_book").source or ""
    assert 'shelf: OneOf("unread", "reading", "read")' in source


@pytest.mark.parametrize("key", ["read_user_me", "read_book", "create_book", "update_book"])
def test_the_single_role_routes_verify(imported: ImportResult, key: str) -> None:
    r = imported.result(key)
    assert r.status == "lifted-verified", r.reason


def test_a_value_outside_the_enum_is_requested_and_agrees(imported: ImportResult) -> None:
    r = imported.result("create_book")
    assert r.verify is not None
    names = {c.name for c in imported.suite.cases if c.route == "create_book"}  # type: ignore[union-attr]
    assert any("not_a_choice" in n for n in names)


def test_the_credentials_exception_idiom_is_read(imported: ImportResult) -> None:
    """``raise credentials_exception``: the or-ed active check is the profile's (401 with
    the exception's detail), and so are the bad-token and unknown-user answers
    (``NativeToken(invalid=, unknown=)``): those cases are equal, not declared."""
    api = imported.lattice_profile.api
    assert api is not None
    assert (api.active_column, api.inactive_status, api.inactive_detail) == (
        "is_active", 401, "Could not validate credentials",
    )  # fmt: skip
    assert api.auth.invalid == api.auth.unknown == (401, "Could not validate credentials")  # type: ignore[union-attr]
    me = imported.result("read_user_me")
    assert not [n for n in me.deviations if "token" in n], me.deviations
