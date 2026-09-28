"""End to end on a synthetic app with integer keys and naive datetimes (plan: generic
import): the template's idioms on ``int`` primary keys, an ``int`` token subject and
``NaiveDatetime`` columns lift and verify -- Lattice's ``insert`` lets the database assign
an ``Int`` key (autoincrement), the seed and the suite use integer ids, and the token
decoder yields the integer key.

The app is written to a temporary directory (its own package and SQLAlchemy registry),
never the held-out repos' code.
"""

from __future__ import annotations

import importlib
import sys
import textwrap

import pytest

from lattice.importer.lift import ImportResult, lift_repo
from lattice.profile import NativeToken

PACKAGE = "shelf_int_keys"

FILES = {
    "__init__": """
        from collections.abc import Generator
        from pathlib import Path

        from fastapi import FastAPI
        from sqlmodel import Session, create_engine

        from PKG import deps
        from PKG.models import registry
        from PKG.routes import router


        def make_app(db_path):
            engine = create_engine(
                f"sqlite:///{Path(db_path)}", connect_args={"check_same_thread": False}
            )
            registry.metadata.create_all(engine)

            def get_db() -> Generator[Session]:
                with Session(engine) as session:
                    yield session

            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[deps.get_db] = get_db
            return app
    """,
    "models": """
        from datetime import UTC, datetime

        from pydantic import EmailStr, NaiveDatetime
        from sqlalchemy.orm import registry as _registry
        from sqlmodel import Field, SQLModel

        registry = _registry()


        def utc_now() -> datetime:
            return datetime.now(UTC).replace(tzinfo=None)


        class TableBase(SQLModel, registry=registry):
            pass


        class User(TableBase, table=True):
            __tablename__ = "users"

            id: int | None = Field(default=None, primary_key=True)
            email: EmailStr = Field(unique=True, max_length=255)
            is_active: bool = True
            is_superuser: bool = False
            hashed_password: str
            created_at: NaiveDatetime | None = Field(default_factory=utc_now)


        class UserRegister(SQLModel):
            email: EmailStr = Field(max_length=255)
            password: str = Field(min_length=8, max_length=40)


        class UserPublic(SQLModel):
            id: int
            email: EmailStr
            is_active: bool
            is_superuser: bool
            created_at: NaiveDatetime | None = None


        class BookBase(SQLModel):
            title: str = Field(min_length=1, max_length=100)
            note: str | None = Field(default=None, max_length=100)


        class BookCreate(BookBase):
            pass


        class BookUpdate(SQLModel):
            title: str | None = Field(default=None, min_length=1, max_length=100)
            note: str | None = Field(default=None, max_length=100)


        class Book(BookBase, TableBase, table=True):
            __tablename__ = "books"

            id: int | None = Field(default=None, primary_key=True)
            owner_id: int = Field(foreign_key="users.id")
            created_at: NaiveDatetime | None = Field(default_factory=utc_now)


        class BookPublic(BookBase):
            id: int
            owner_id: int
            created_at: NaiveDatetime | None = None


        class Message(SQLModel):
            message: str


        class Token(SQLModel):
            access_token: str
            token_type: str = "bearer"
    """,
    "security": """
        from datetime import UTC, datetime, timedelta

        import jwt

        SECRET = "synthetic-secret-key-for-tests-000000000000"
        ALGORITHM = "HS256"


        def create_access_token(subject: int) -> str:
            expire = datetime.now(UTC) + timedelta(minutes=30)
            return jwt.encode({"exp": expire, "sub": str(subject)}, SECRET, algorithm=ALGORITHM)


        def get_password_hash(password: str) -> str:
            return "hashed:" + password


        def verify_password(plain: str, hashed: str) -> bool:
            return hashed == "hashed:" + plain
    """,
    "crud": """
        from sqlmodel import Session, select

        from PKG.models import User
        from PKG.security import verify_password


        def authenticate(*, session: Session, email: str, password: str) -> User | None:
            user = session.exec(select(User).where(User.email == email)).first()
            if not user:
                return None
            if not verify_password(password, user.hashed_password):
                return None
            return user
    """,
    "deps": """
        from collections.abc import Generator
        from typing import Annotated

        import jwt
        from fastapi import Depends, HTTPException
        from fastapi.security import OAuth2PasswordBearer
        from jwt.exceptions import InvalidTokenError
        from sqlmodel import Session

        from PKG.models import User
        from PKG.security import ALGORITHM, SECRET

        reusable_oauth2 = OAuth2PasswordBearer(tokenUrl="/login/access-token")


        def get_db() -> Generator[Session]:
            raise NotImplementedError  # replaced by make_app


        SessionDep = Annotated[Session, Depends(get_db)]
        TokenDep = Annotated[str, Depends(reusable_oauth2)]


        def get_current_user(session: SessionDep, token: TokenDep) -> User:
            try:
                payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
            except InvalidTokenError:
                raise HTTPException(status_code=403, detail="Could not validate credentials")
            user = session.get(User, int(payload["sub"]))
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            if not user.is_active:
                raise HTTPException(status_code=400, detail="Inactive user")
            return user


        CurrentUser = Annotated[User, Depends(get_current_user)]


        def get_current_active_superuser(current_user: CurrentUser) -> User:
            if not current_user.is_superuser:
                raise HTTPException(status_code=403, detail="Not enough privileges")
            return current_user
    """,
    "routes": """
        from typing import Annotated, Any

        from fastapi import APIRouter, Depends, HTTPException
        from fastapi.security import OAuth2PasswordRequestForm

        from PKG import crud, security
        from PKG.deps import CurrentUser, SessionDep
        from PKG.models import (
            Book, BookCreate, BookPublic, BookUpdate, Message, Token, User, UserPublic,
            UserRegister,
        )

        router = APIRouter()


        @router.post("/login/access-token")
        def login_access_token(
            session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
        ) -> Token:
            user = crud.authenticate(
                session=session, email=form_data.username, password=form_data.password
            )
            if not user:
                raise HTTPException(status_code=400, detail="Incorrect email or password")
            elif not user.is_active:
                raise HTTPException(status_code=400, detail="Inactive user")
            return Token(access_token=security.create_access_token(user.id))


        @router.post("/users/signup", response_model=UserPublic)
        def register_user(session: SessionDep, user_in: UserRegister) -> Any:
            user = User(
                email=user_in.email, hashed_password=security.get_password_hash(user_in.password)
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
            if not (current_user.is_superuser or book.owner_id == current_user.id):
                raise HTTPException(status_code=403, detail="Not enough permissions")
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
            if not current_user.is_superuser and (book.owner_id != current_user.id):
                raise HTTPException(status_code=403, detail="Not enough permissions")
            update_dict = book_in.model_dump(exclude_unset=True)
            book.sqlmodel_update(update_dict)
            session.add(book)
            session.commit()
            session.refresh(book)
            return book


        @router.delete("/books/{id}")
        def delete_book(session: SessionDep, current_user: CurrentUser, id: int) -> Message:
            book = session.get(Book, id)
            if not book:
                raise HTTPException(status_code=404, detail="Book not found")
            if not current_user.is_superuser and (book.owner_id != current_user.id):
                raise HTTPException(status_code=403, detail="Not enough permissions")
            session.delete(book)
            session.commit()
            return Message(message="Book deleted successfully")
    """,
}
LIFTED = ("read_user_me", "read_book", "create_book", "update_book", "delete_book")


@pytest.fixture(scope="module")
def imported(tmp_path_factory: pytest.TempPathFactory) -> ImportResult:
    root = tmp_path_factory.mktemp("int_keys")
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


def test_the_profile_has_int_keys_and_an_int_token_decoder(imported: ImportResult) -> None:
    api = imported.lattice_profile.api
    assert api is not None and api.auth == NativeToken(
        "decode_token",
        invalid=(403, "Could not validate credentials"),
        unknown=(404, "User not found"),
    )  # the original's answers to a token naming no caller
    assert api.tables["Book"].keys == (("id",),) and api.tables["User"].keys == (("id",),)
    assert "int(claim)" in imported.profile.source
    assert api.datetime_wire == "naive" and api.datetime_storage == "sql_space"
    auth = imported.context.inputs.auth  # type: ignore[union-attr]
    assert auth.key_type == "int" and auth.mint_token is not None


@pytest.mark.parametrize("key", LIFTED)
def test_the_template_idioms_verify_on_int_keys(imported: ImportResult, key: str) -> None:
    r = imported.result(key)
    assert r.status == "lifted-verified", r.reason


def test_the_create_lets_the_database_assign_the_int_key(imported: ImportResult) -> None:
    source = imported.result("create_book").source or ""
    assert "insert(" in source and "id=" not in source
    assert "created_at=fn.now()" in source  # datetime.now(UTC).replace(tzinfo=None)
    cases = [c for c in imported.suite.cases if c.route == "create_book"]  # type: ignore[union-attr]
    assert any(c.name == "create_book.happy" for c in cases)


def test_int_path_ids_are_requested_as_integers(imported: ImportResult) -> None:
    cases = {c.name: c for c in imported.suite.cases}  # type: ignore[union-attr]
    assert cases["read_book.missing_id"].path == "/books/999999"
    assert cases["read_book.malformed_id"].path == "/books/not-an-int"
    assert cases["read_book.happy"].path.rsplit("/", 1)[-1].isdigit()


def test_only_verified_routes_are_emitted(imported: ImportResult) -> None:
    program = imported.program()
    for r in imported.results:
        assert (f"def {r.key}(" in program) == (r.status == "lifted-verified"), r.key
