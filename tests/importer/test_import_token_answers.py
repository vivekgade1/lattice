"""Auth-policy fidelity (spec 2026-09-27 surface §4.1): an imported app answers a bad
token, a claimless token and a token naming no user exactly as the original does -- on
every route, a route added later by an edit included.

Round 3's t24 (JustNotes) found the gap: the import declared the original's answers as
per-route token *policies* (deviations the verifier accepted), while the imported app
answered every such token with Lattice's 401 "Not authenticated"; a route an edit added
had no declared deviation, and the oracle saw a silent mismatch. Now the answers are
read from the current-user dependency's raise sites (following the decode helper it
passes the token to) and carried by the profile (``NativeToken(invalid=, unknown=)``),
so the verifier's token cases are equal, not declared.

Two synthetic apps (written to a temporary directory, never the held-out repos): one
answers a bad token 401 with detail X in the dependency itself; the other 403 with
detail Y from a decode helper that raises, and an unknown user 404.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import jwt
import pytest
from starlette.testclient import TestClient

from lattice.emit.thin import emit_into_app, make_emitted_app, program_routes
from lattice.importer.cli import main as import_main
from lattice.importer.lift import ImportResult, lift_repo
from lattice.importer.llm_lift import load_profile_source
from lattice.profile import NativeToken

SECRET = "synthetic-secret-for-the-token-answer-tests-0000"

COMMON = {
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
        from sqlalchemy.orm import registry as _registry
        from sqlmodel import Field, SQLModel

        registry = _registry()


        class TableBase(SQLModel, registry=registry):
            pass


        class User(TableBase, table=True):
            __tablename__ = "users"

            id: int | None = Field(default=None, primary_key=True)
            username: str = Field(unique=True, max_length=50)
            hashed_password: str


        class UserPublic(SQLModel):
            id: int
            username: str


        class UserRegister(SQLModel):
            username: str = Field(max_length=50)
            password: str = Field(min_length=8, max_length=40)


        class Token(SQLModel):
            access_token: str
            token_type: str = "bearer"
    """,
    "security": f"""
        import jwt

        SECRET_KEY = {SECRET!r}


        def create_access_token(subject: str) -> str:
            return jwt.encode({{"sub": subject}}, SECRET_KEY, algorithm="HS256")


        def get_password_hash(password: str) -> str:
            return "hashed:" + password


        def verify_password(plain: str, hashed: str) -> bool:
            return hashed == "hashed:" + plain
    """,
    "routes": """
        from typing import Annotated, Any

        from fastapi import APIRouter, Depends, HTTPException
        from fastapi.security import OAuth2PasswordRequestForm
        from sqlmodel import select

        from PKG import security
        from PKG.deps import CurrentUser, SessionDep
        from PKG.models import Token, User, UserPublic, UserRegister

        router = APIRouter()


        @router.post("/login")
        def login(
            session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
        ) -> Token:
            user = session.exec(select(User).where(User.username == form_data.username)).first()
            if not user or not security.verify_password(form_data.password, user.hashed_password):
                raise HTTPException(status_code=400, detail="Incorrect username or password")
            return Token(access_token=security.create_access_token(user.username))


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
    """,
}

DEPS_HEAD = """
    from collections.abc import Generator
    from typing import Annotated

    import jwt
    from fastapi import Depends, HTTPException
    from fastapi.security import OAuth2PasswordBearer
    from jwt.exceptions import InvalidTokenError
    from sqlmodel import Session, select

    from PKG import security
    from PKG.models import User

    oauth2 = OAuth2PasswordBearer(tokenUrl="/login")


    def get_db() -> Generator[Session]:
        raise NotImplementedError  # replaced by make_app


    SessionDep = Annotated[Session, Depends(get_db)]
"""

# the dependency raises itself: a bad or claimless token 401 X, an unknown user 401
IN_DEPENDENCY = """
    def get_current_user(session: SessionDep, token: Annotated[str, Depends(oauth2)]) -> User:
        try:
            payload = jwt.decode(token, security.SECRET_KEY, algorithms=["HS256"])
        except InvalidTokenError:
            raise HTTPException(status_code=401, detail="Token rejected: X")
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Token rejected: X")
        user = session.exec(select(User).where(User.username == username)).first()
        if user is None:
            raise HTTPException(status_code=401, detail="Unknown user X")
        return user


    CurrentUser = Annotated[User, Depends(get_current_user)]
"""

# a decode helper raises: a bad token 403 Y (the claim guard too), an unknown user 404
IN_HELPER = """
    def decode_access_token(raw: str) -> dict:
        try:
            return jwt.decode(raw, security.SECRET_KEY, algorithms=["HS256"])
        except InvalidTokenError:
            raise HTTPException(status_code=403, detail="Forbidden token Y")


    def get_current_user(session: SessionDep, token: Annotated[str, Depends(oauth2)]) -> User:
        payload = decode_access_token(token)
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=403, detail="Forbidden token Y")
        user = session.exec(select(User).where(User.username == username)).first()
        if user is None:
            raise HTTPException(status_code=404, detail="No user Y")
        return user


    CurrentUser = Annotated[User, Depends(get_current_user)]
"""

# variant -> (dependency source, bad/claimless answer, unknown-user answer)
VARIANTS = {
    "in_dependency": (IN_DEPENDENCY, (401, "Token rejected: X"), (401, "Unknown user X")),
    "in_helper": (IN_HELPER, (403, "Forbidden token Y"), (404, "No user Y")),
}

# an endpoint an edit adds after the import
ADDED = """# lattice: v1

@endpoint("GET", "/whoami", auth=Customer)
def whoami(user: CurrentUser) -> One:
    shown = select(user, cols=["id", "username"])
    return shown
"""

TOKENS = {
    "bad": "not-a-jwt",
    "forged": jwt.encode({"sub": "alice"}, "another-secret-another-secret-0000", algorithm="HS256"),
    "claimless": jwt.encode({"x": 1}, SECRET, algorithm="HS256"),
    "ghost": jwt.encode({"sub": "ghost"}, SECRET, algorithm="HS256"),
    "alice": jwt.encode({"sub": "alice"}, SECRET, algorithm="HS256"),
}


class App:
    def __init__(self, root: Path, package: str) -> None:
        self.root, self.package = root, package

    @property
    def factory(self):  # noqa: ANN201
        return importlib.import_module(self.package).make_app


@pytest.fixture(scope="module", params=sorted(VARIANTS))
def app(request, tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, App]]:
    variant = request.param
    package = f"token_answers_{variant}"
    root = tmp_path_factory.mktemp(variant)
    pkg = root / package
    pkg.mkdir()
    files = dict(COMMON, deps=DEPS_HEAD + VARIANTS[variant][0])
    for name, src in files.items():
        (pkg / f"{name}.py").write_text(textwrap.dedent(src).replace("PKG", package))
    sys.path.insert(0, str(root))
    try:
        yield variant, App(root, package)
    finally:
        sys.path.remove(str(root))
        for name in [m for m in sys.modules if m == package or m.startswith(package + ".")]:
            del sys.modules[name]


@pytest.fixture(scope="module")
def imported(app: tuple[str, App]) -> ImportResult:
    _, a = app
    return lift_repo(a.root, a.package, a.factory, hypothesis_bodies=2)


def test_the_profile_carries_the_originals_answers(app, imported: ImportResult) -> None:
    variant, _ = app
    _, invalid, unknown = VARIANTS[variant]
    api = imported.lattice_profile.api
    assert api is not None
    assert isinstance(api.auth, NativeToken)
    assert (api.auth.invalid, api.auth.unknown) == (invalid, unknown)
    assert "invalid=" in imported.profile.source


def test_the_token_cases_are_equal_not_declared(imported: ImportResult) -> None:
    me = imported.result("read_user_me")
    assert me.status == "lifted-verified", me.reason
    names = {c.name for c in imported.suite.cases if c.route == "read_user_me"}  # type: ignore[union-attr]
    assert {"read_user_me.bad_token", "read_user_me.unknown_user_token"} <= names
    assert not [n for n in me.deviations if "token" in n], me.deviations


def _answer(client: TestClient, path: str, who: str) -> tuple[int, object]:
    r = client.get(path, headers={"Authorization": f"Bearer {TOKENS[who]}"})
    return r.status_code, r.json()


def test_the_emitted_app_answers_like_the_original_on_every_route(app, tmp_path: Path) -> None:
    """The import written by the CLI, emitted into the original app with an endpoint an
    edit added: every token naming no caller is answered as the original answers it."""
    variant, a = app
    _, invalid, unknown = VARIANTS[variant]
    out = tmp_path / "import"
    assert import_main([
        str(a.root), "--package", a.package, "--app-factory", f"{a.package}:make_app",
        "--out", str(out), "--no-llm", "--no-corpus", "--hypothesis-bodies", "2",
    ]) == 0  # fmt: skip
    profile_src = (out / "profile.py").read_text()
    programs = {p.name: p.read_text() for p in sorted((out / "routes").iterdir())}
    assert programs, "nothing was lifted"
    profile = load_profile_source(profile_src).profile()
    lifted = program_routes(programs, profile)
    assert "GET /users/me" in lifted
    programs["added.lattice.py"] = ADDED
    emitted = emit_into_app(programs, profile_src, tmp_path / "emitted",
                            app_factory=f"{a.package}:make_app", lifted=lifted)  # fmt: skip
    assert "GET /whoami" in emitted.routes_emitted

    original = TestClient(a.factory(tmp_path / "original.db"))
    lattice = TestClient(make_emitted_app(tmp_path / "emitted", tmp_path / "lattice.db"))
    for c in (original, lattice):
        c.post("/users/signup", json={"username": "alice", "password": "password123"})
    expected = {
        "bad": (invalid[0], {"detail": invalid[1]}),
        "forged": (invalid[0], {"detail": invalid[1]}),
        "claimless": (invalid[0], {"detail": invalid[1]}),
        "ghost": (unknown[0], {"detail": unknown[1]}),
    }
    for who, answer in expected.items():
        assert _answer(original, "/users/me", who) == answer, who
        assert _answer(lattice, "/users/me", who) == answer, who
        assert _answer(lattice, "/whoami", who) == answer, who
    assert _answer(lattice, "/whoami", "alice")[0] == 200
    assert _answer(lattice, "/users/me", "alice") == _answer(original, "/users/me", "alice")
    missing = lattice.get("/whoami")
    assert (missing.status_code, missing.json()) == (401, {"detail": "Not authenticated"})


# ---- an answer NativeToken cannot carry ------------------------------------------------------

# a bad token 418 (outside 400/401/403/404), an unknown user 404 with an empty detail
TEAPOT = """
    def get_current_user(session: SessionDep, token: Annotated[str, Depends(oauth2)]) -> User:
        try:
            payload = jwt.decode(token, security.SECRET_KEY, algorithms=["HS256"])
        except InvalidTokenError:
            raise HTTPException(status_code=418, detail="I am a teapot")
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=418, detail="I am a teapot")
        user = session.exec(select(User).where(User.username == username)).first()
        if user is None:
            raise HTTPException(status_code=404, detail="")
        return user


    CurrentUser = Annotated[User, Depends(get_current_user)]
"""


@pytest.fixture(scope="module")
def teapot(tmp_path_factory: pytest.TempPathFactory) -> Iterator[App]:
    package = "token_answers_teapot"
    root = tmp_path_factory.mktemp("teapot")
    pkg = root / package
    pkg.mkdir()
    for name, src in dict(COMMON, deps=DEPS_HEAD + TEAPOT).items():
        (pkg / f"{name}.py").write_text(textwrap.dedent(src).replace("PKG", package))
    sys.path.insert(0, str(root))
    try:
        yield App(root, package)
    finally:
        sys.path.remove(str(root))
        for name in [m for m in sys.modules if m == package or m.startswith(package + ".")]:
            del sys.modules[name]


def test_an_answer_native_token_cannot_carry_falls_back_with_an_unclear_note(teapot: App):
    """A 418 (or an empty detail) is not a NativeToken answer: the import must not
    abort. The profile keeps the default answer for it, says why (Unclear), and the
    route's token cases fall back to a declared token policy."""
    imported = lift_repo(teapot.root, teapot.package, teapot.factory, hypothesis_bodies=2)
    api = imported.lattice_profile.api
    assert api is not None and isinstance(api.auth, NativeToken)
    assert (api.auth.invalid, api.auth.unknown) == (None, None)
    notes = [u for u in imported.unclear if u.topic == "token_answer"]
    assert {n.subject for n in notes} == {"invalid", "unknown"}, notes
    assert any("418" in n.reason for n in notes)
    me = imported.result("read_user_me")
    assert me.status.startswith("lifted"), me.reason
