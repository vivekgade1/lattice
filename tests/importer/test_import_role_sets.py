"""End to end on a synthetic app with a role/permission table (spec
docs/superpowers/specs/2026-09-29-lattice-role-sets-design.md, the importer part):

* an enum role column (``StrEnum``, stored by member name, which the profile decodes to
  the value: ``one_of_stored``) -> ``RoleColumn("kind", values={"member": "Member", ...})``;
* a dependency factory, ``Depends(grant(Perm.X))``, whose inner checker refuses callers
  whose role lacks the permission -> ``auth=Roles(...)`` with exactly the granted roles
  (the admin only where the table grants it); a plain current-user dependency ->
  ``auth=Authenticated``;
* a table whose users foreign key is unique (one studio per user): the generated seed
  gives each user at most one row;
* query parameters declared ``Query(default=..., ge=..., le=...)``: their defaults and
  bounds are read (and probed by the suite), not refused as non-literal defaults.

The app is written to a temporary directory, never the held-out repos' code.
"""

from __future__ import annotations

import importlib
import sys
import textwrap

import pytest

from lattice.importer.lift import ImportResult, lift_repo
from lattice.profile import RoleColumn

PACKAGE = "synthetic_role_sets"

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
        from enum import StrEnum

        from sqlalchemy.orm import registry as _registry
        from sqlmodel import Field, SQLModel

        registry = _registry()


        class TableBase(SQLModel, registry=registry):
            pass


        class Kind(StrEnum):
            MEMBER = "member"
            CURATOR = "curator"
            ADMIN = "admin"


        class User(TableBase, table=True):
            __tablename__ = "users"

            id: int | None = Field(default=None, primary_key=True)
            email: str = Field(unique=True, max_length=80)
            hashed_password: str
            kind: Kind = Field(default=Kind.MEMBER)
            is_active: bool = True


        class UserOut(SQLModel):
            id: int
            email: str
            kind: Kind


        class Studio(TableBase, table=True):
            __tablename__ = "studios"

            id: int | None = Field(default=None, primary_key=True)
            user_id: int = Field(foreign_key="users.id", unique=True)
            name: str = Field(max_length=40)


        class StudioOut(SQLModel):
            id: int
            user_id: int
            name: str


        class Print(TableBase, table=True):
            __tablename__ = "prints"

            id: int | None = Field(default=None, primary_key=True)
            owner_id: int = Field(foreign_key="users.id")
            title: str = Field(max_length=60)
            medium: str = Field(max_length=20)


        class PrintIn(SQLModel):
            title: str = Field(min_length=1, max_length=60)
            medium: str = Field(min_length=1, max_length=20)


        class PrintOut(SQLModel):
            id: int
            owner_id: int
            title: str
            medium: str
    """,
    "grants": """
        from enum import Enum

        from PKG.models import Kind


        class Perm(str, Enum):
            BROWSE = "browse"
            SUBMIT = "submit"
            CURATE = "curate"
            AUDIT = "audit"


        TABLE = {
            Kind.MEMBER: {Perm.BROWSE, Perm.SUBMIT},
            Kind.CURATOR: {Perm.BROWSE, Perm.CURATE},
            Kind.ADMIN: {Perm.AUDIT, Perm.CURATE},
        }
    """,
    "security": """
        from datetime import UTC, datetime, timedelta

        import jwt

        SECRET = "synthetic-role-sets-secret-key-000000000"


        def create_access_token(subject: str) -> str:
            expire = datetime.now(UTC) + timedelta(minutes=30)
            return jwt.encode({"exp": expire, "sub": subject}, SECRET, algorithm="HS256")


        def decode_access_token(token: str) -> dict:
            try:
                return jwt.decode(token, SECRET, algorithms=["HS256"])
            except jwt.InvalidTokenError:
                raise ValueError("Invalid or expired token")


        def get_password_hash(password: str) -> str:
            return "hashed:" + password


        def verify_password(plain: str, hashed: str) -> bool:
            return hashed == "hashed:" + plain
    """,
    "deps": """
        from collections.abc import Generator
        from typing import Annotated

        from fastapi import Depends, HTTPException
        from fastapi.security import OAuth2PasswordBearer
        from sqlmodel import Session

        from PKG.grants import TABLE, Perm
        from PKG.models import User
        from PKG.security import decode_access_token

        oauth2 = OAuth2PasswordBearer(tokenUrl="/login")


        def get_db() -> Generator[Session]:
            raise NotImplementedError  # replaced by make_app


        SessionDep = Annotated[Session, Depends(get_db)]


        def get_current_user(session: SessionDep, token: Annotated[str, Depends(oauth2)]) -> User:
            credentials = HTTPException(status_code=401, detail="Could not validate credentials")
            try:
                payload = decode_access_token(token)
            except ValueError:
                raise credentials
            sub = payload.get("sub")
            if sub is None:
                raise credentials
            user = session.get(User, int(sub))
            if user is None:
                raise HTTPException(status_code=401, detail="Could not validate credentials")
            if not user.is_active:
                raise HTTPException(status_code=403, detail="Account disabled")
            return user


        def grant(perm: Perm):
            def check(user: User = Depends(get_current_user)) -> User:
                if perm not in TABLE.get(user.kind, set()):
                    raise HTTPException(status_code=403, detail="Missing permission")
                return user

            return check
    """,
    "routes": """
        from typing import Annotated, Any

        from fastapi import APIRouter, Depends, HTTPException, Query
        from fastapi.security import OAuth2PasswordRequestForm
        from sqlmodel import select

        from PKG import security
        from PKG.deps import SessionDep, get_current_user, grant
        from PKG.grants import Perm
        from PKG.models import Print, PrintIn, PrintOut, Studio, StudioOut, User, UserOut

        router = APIRouter()


        @router.post("/login")
        def login(
            session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
        ) -> dict:
            user = session.exec(select(User).where(User.email == form_data.username)).first()
            if not user or not security.verify_password(form_data.password, user.hashed_password):
                raise HTTPException(status_code=400, detail="Incorrect email or password")
            return {"access_token": security.create_access_token(str(user.id))}


        @router.get("/me", response_model=UserOut)
        def read_me(user: User = Depends(get_current_user)) -> Any:
            return user


        @router.post("/signup", response_model=UserOut)
        def signup(session: SessionDep, email: str, password: str) -> Any:
            user = User(email=email, hashed_password=security.get_password_hash(password))
            session.add(user)
            session.commit()
            session.refresh(user)
            return user


        @router.get("/studios/{id}/browse", response_model=StudioOut)
        def browse_studio(
            session: SessionDep, id: int, user: User = Depends(grant(Perm.BROWSE))
        ) -> Any:
            studio = session.get(Studio, id)
            if studio is None:
                raise HTTPException(status_code=404, detail="Studio not found")
            return studio


        @router.post("/prints", response_model=PrintOut)
        def submit_print(
            session: SessionDep, data: PrintIn, user: User = Depends(grant(Perm.SUBMIT))
        ) -> Any:
            item = Print.model_validate(data, update={"owner_id": user.id})
            session.add(item)
            session.commit()
            session.refresh(item)
            return item


        @router.get("/studios/{id}/curate", response_model=StudioOut)
        def curate_studio(
            session: SessionDep, id: int, user: User = Depends(grant(Perm.CURATE))
        ) -> Any:
            studio = session.get(Studio, id)
            if studio is None:
                raise HTTPException(status_code=404, detail="Studio not found")
            return studio


        @router.get("/users/{id}", response_model=UserOut)
        def audit_user(
            session: SessionDep, id: int, user: User = Depends(grant(Perm.AUDIT))
        ) -> Any:
            found = session.get(User, id)
            if found is None:
                raise HTTPException(status_code=404, detail="User not found")
            return found


        @router.get("/studios", response_model=list[StudioOut])
        def list_studios(
            session: SessionDep,
            skip: int = Query(default=0, ge=0),
            limit: int = Query(default=20, le=100),
            user: User = Depends(get_current_user),
        ) -> Any:
            return session.exec(select(Studio).offset(skip).limit(limit)).all()


        @router.get("/studios/{id}", response_model=StudioOut)
        def read_studio(
            session: SessionDep, id: int, user: User = Depends(get_current_user)
        ) -> Any:
            studio = session.get(Studio, id)
            if studio is None:
                raise HTTPException(status_code=404, detail="Studio not found")
            return studio
    """,
}


@pytest.fixture(scope="module")
def imported(tmp_path_factory: pytest.TempPathFactory) -> ImportResult:
    root = tmp_path_factory.mktemp("role_sets")
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


def test_the_role_column_is_decoded_from_the_enum(imported: ImportResult) -> None:
    api = imported.lattice_profile.api
    assert api is not None
    assert api.roles == frozenset({"Public", "Member", "Curator", "Admin"})
    assert api.admin_role == "Admin"
    assert api.roles_from == RoleColumn(
        "kind", values={"member": "Member", "curator": "Curator", "admin": "Admin"}
    )
    assert api.forbidden_detail == "Missing permission"


@pytest.mark.parametrize(
    ("key", "auth"),
    [
        ("read_me", "Authenticated"),
        ("browse_studio", "Roles(Curator, Member)"),
        ("submit_print", "Roles(Member)"),
        ("curate_studio", "Roles(Admin, Curator)"),
        ("audit_user", "Roles(Admin)"),
        ("read_studio", "Authenticated"),
    ],
)
def test_the_permission_table_becomes_exact_role_sets(
    imported: ImportResult, key: str, auth: str
) -> None:
    route = next(r for r in imported.model.routes if r.name == key)
    assert route.auth == auth, route.auth_reason


@pytest.mark.parametrize(
    "key",
    ["read_me", "browse_studio", "submit_print", "curate_studio", "audit_user", "read_studio"],
)
def test_the_role_routes_verify(imported: ImportResult, key: str) -> None:
    r = imported.result(key)
    assert r.status == "lifted-verified", r.reason
    auth = next(x for x in imported.model.routes if x.name == key).auth
    assert f"auth={auth}" in (r.source or "")


def test_every_role_calls_every_role_route(imported: ImportResult) -> None:
    """The suite signs in as a user of each role on every authenticated route, so a
    wrong role set cannot verify."""
    assert imported.suite is not None
    for key in ("browse_studio", "submit_print", "audit_user"):
        users = {c.user for c in imported.suite.cases if c.route == key and c.user}
        assert {"role_curator", "admin", "alice"} <= users, (key, users)


def test_a_unique_users_foreign_key_gets_one_row_per_user(imported: ImportResult) -> None:
    assert imported.suite is not None
    rows = imported.suite.plan.rows["studios"]
    owners = [r["user_id"] for r in rows]
    assert len(owners) == len(set(owners)) > 0


def test_query_defaults_and_bounds_are_read(imported: ImportResult) -> None:
    route = next(r for r in imported.model.routes if r.name == "list_studios")
    params = {p.name: p for p in route.params}
    assert (params["skip"].default, params["skip"].required, params["skip"].ge) == ("0", False, 0)
    assert (params["limit"].default, params["limit"].le) == ("20", 100)
    from lattice.importer.adapt import route_spec

    api = imported.lattice_profile.api
    assert api is not None
    spec = {p.name: p for p in route_spec(imported.model, route, api).params}
    assert (spec["skip"].default, spec["skip"].ge) == (0, 0)
    assert (spec["limit"].default, spec["limit"].le) == (20, 100)


def test_a_dialect_given_for_an_environment_url_sets_the_storage(
    tmp_path_factory: pytest.TempPathFactory, imported: ImportResult
) -> None:
    """An app whose database URL is built at runtime (no URL literal anywhere): the
    dialect is unknown unless the caller names the deployment's (``--dialect``)."""
    root = tmp_path_factory.mktemp("role_sets_dialect")
    pkg = root / PACKAGE
    pkg.mkdir()
    files = dict(FILES)
    files["__init__"] = (
        files["__init__"]
        .replace('f"sqlite:///{Path(db_path)}"', 'SCHEME + ":///" + str(Path(db_path))')
        .replace(
            "def make_app(db_path):", 'SCHEME = "sq" + "lite"\n\n\n        def make_app(db_path):'
        )
    )
    for name, src in files.items():
        (pkg / f"{name}.py").write_text(textwrap.dedent(src).replace("PKG", PACKAGE))
    for name in [m for m in sys.modules if m == PACKAGE or m.startswith(PACKAGE + ".")]:
        del sys.modules[name]
    sys.path.insert(0, str(root))
    try:
        factory = importlib.import_module(PACKAGE).make_app
        result = lift_repo(root, PACKAGE, factory, hypothesis_bodies=0, only=["read_me"],
                           dialect="sqlite")  # fmt: skip
    finally:
        sys.path.remove(str(root))
        for name in [m for m in sys.modules if m == PACKAGE or m.startswith(PACKAGE + ".")]:
            del sys.modules[name]
    assert result.model.dialect == "sqlite"
    assert "backend='sqlite'" in result.profile.source
    from lattice.importer.extract import extract

    assert extract(root, PACKAGE).dialect is None  # the package alone does not say
