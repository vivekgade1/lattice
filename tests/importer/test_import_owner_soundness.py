"""Soundness of owner inference (plan node C2): a wrongly inferred owner is never
silent. The synthetic app below checks ``Book.owner_id`` against the caller when reading
and updating a book and sets it on create -- the evidence the rule takes an owner from --
but a delete route lets any signed-in user delete any book and answers with a message
(no rows of the table, so no contradiction the rule sees). The inferred owner is wrong
for that route: its lift must fail verification (the checker, then the differential
against the original) and never be emitted, while the routes the owner is right for
still verify.

The app is written to a temporary directory (its own package and SQLAlchemy registry),
never the held-out repos' code; its shape is the single-role test app's.
"""

from __future__ import annotations

import importlib
import sys
import textwrap

import pytest

from lattice.importer.lift import ImportResult, lift_repo, verify_candidate

PACKAGE = "solo_wrong_owner"

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


        class Message(SQLModel):
            message: str


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
            Book, BookCreate, BookPublic, BookUpdate, Message, Token, User, UserPublic,
            UserRegister,
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


        @router.delete("/books/{id}", response_model=Message)
        def delete_any_book(*, session: SessionDep, current_user: CurrentUser, id: int) -> Any:
            # any signed-in user may delete any book: Book is not really owned
            book = session.get(Book, id)
            if not book:
                raise HTTPException(status_code=404, detail="Book not found")
            session.delete(book)
            session.commit()
            return Message(message="Book deleted")
    """,
}


@pytest.fixture(scope="module")
def imported(tmp_path_factory: pytest.TempPathFactory) -> ImportResult:
    root = tmp_path_factory.mktemp("wrong_owner")
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


def test_the_owner_is_inferred_wrongly(imported: ImportResult) -> None:
    """The premise: the evidence the rule reads names ``owner_id`` as the owner."""
    api = imported.lattice_profile.api
    assert api is not None
    assert api.tables["Book"].owner == "owner_id"
    assert imported.profile.ownership["Book"] == "owner"


def test_the_route_the_owner_is_wrong_for_fails_verification(imported: ImportResult) -> None:
    """It is lifted (the delete idiom), and the lift -- deleting only the caller's own
    books -- disagrees with the original, which deletes anyone's: verification fails and
    the route is not emitted."""
    key = "delete_any_book"
    r = imported.result(key)
    assert r.lift.ok, r.reason
    assert r.status == "lifted-unverified"
    assert "L802" in " ".join(r.diagnostics)  # the lift writes Book rows unchecked
    assert f"def {key}(" not in imported.program()


def test_an_ownership_respecting_lift_fails_the_differential(imported: ImportResult) -> None:
    """The lift the checker would accept under the wrong owner (the caller's books
    only) disagrees with the original on another user's book: the differential, not
    the inference, has the last word."""
    assert imported.context is not None
    base = imported.result("delete_any_book")
    assert base.lift.source is not None
    owned = base.lift.source.replace(
        "books = fetch(Book)", "books = accessible(fetch(Book), by=user)"
    )
    assert owned != base.lift.source
    got = verify_candidate(imported.context, base, owned)
    assert got.status == "lifted-unverified"
    assert got.reason.startswith("verification failed")
    assert "other_user_row" in got.reason


@pytest.mark.parametrize("key", ["read_book", "create_book", "update_book"])
def test_the_routes_the_owner_is_right_for_still_verify(imported: ImportResult, key: str) -> None:
    r = imported.result(key)
    assert r.status == "lifted-verified", r.reason
