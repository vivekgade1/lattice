"""The importer on FastAPI + SQLModel shapes beyond the template (plan: generic, gracefully
degrading import). Every test is a small synthetic snippet repo -- never the held-out
repos' code:

* naive / aware datetimes, integer keys, Enum and ``Literal`` columns;
* the database dialect from a settings default when the engine's URL is not a constant;
* unannotated session generators, repository dependencies (``return Repo(session)``),
  the users table from what the current-user dependency returns (a repository method,
  ``session.get``) rather than from a table's name;
* the token decoder through python-jose as well as PyJWT, in the dependency or in a
  helper; app configuration it reads through ``request.app.state`` becomes a
  NativeContext setting (other request state is not expressible, with the reason); a
  claim naming a unique users column is a ``NativeToken(lookup=)``, a non-unique one a
  language gap;
* Str Enum / ``Literal`` columns are ``OneOf`` columns (an Enum's member names as their
  stored spellings); Int enums remain a language gap;
* single-role apps (no admin flag, no role column) get ``roles_from=NoRoles()``;
* graceful degradation: unsupported columns, no users table, framework parameters
  (``Request``) and routes an app factory defines are reported (unclear / skipped with
  the reason), never a crash.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from lattice.importer.adapt import adapt, auth_blocker, table_spec
from lattice.importer.extract import LANGUAGE_GAP, RepoModel, _Index, extract
from lattice.importer.lift import LiftResult
from lattice.importer.lifters import RouteLift
from lattice.importer.lifters._orm import column_default, has_default
from lattice.importer.lifters.base import RouteSource
from lattice.importer.llm_lift import RouteOutcome, classify_gap
from lattice.importer.profile_gen import ProfileResult, generate_profile
from lattice.importer.suite import (
    MISSING_INT,
    NOT_A_CHOICE,
    AuthSpec,
    ParamSpec,
    RouteSpec,
    generate_suite,
    hypothesis_bodies_for,
    seed_plan,
)
from lattice.profile import NativeToken, NoRoles
from lattice.typesys import Scalar

MODELS = """
import datetime as dt
from enum import StrEnum, IntEnum
from typing import Literal

from pydantic import AwareDatetime, NaiveDatetime
from sqlalchemy.orm import registry
from sqlmodel import Field, SQLModel


class TableBase(SQLModel, registry=registry()):  # its own MetaData: one per snippet repo
    pass


class Level(IntEnum):
    LOW = 1
    HIGH = 2


class Status(StrEnum):
    OPEN = "open"
    DONE = "done"


class Account(TableBase, table=True):
    __tablename__ = "accounts"

    id: int | None = Field(default=None, primary_key=True)
    login: str = Field(unique=True, max_length=50)
    password_digest: str
    joined: NaiveDatetime
    seen: AwareDatetime | None = None


class AccountOut(SQLModel):
    id: int
    login: str


class Ticket(TableBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=80)
    status: Status = Field(default=Status.OPEN)
    level: Level = Level.LOW
    due: dt.datetime | None = None
    owner_id: int = Field(foreign_key="accounts.id")


class TicketIn(SQLModel):
    title: str = Field(max_length=80)
    status: Status = Status.OPEN
    kind: Literal["bug", "idea"] = "bug"


class TicketOut(SQLModel):
    id: int
    title: str
    status: Status
"""

SETTINGS = """
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./tickets.db"
    secret: str = "s"


settings = Settings()
"""

DB = """
from sqlmodel import Session, create_engine

from app.settings import settings


def build(url):
    return create_engine(url)


engine = build(settings.database_url)


def get_session():
    with Session(engine) as session:
        yield session
"""

REPO = """
from sqlmodel import Session, select

from app.models import Account, Ticket


class AccountRepo:
    def __init__(self, session: Session):
        self.session = session

    def by_id(self, account_id: int):
        return self.session.exec(select(Account).where(Account.id == account_id)).first()

    def by_login(self, login: str):
        return self.session.exec(select(Account).where(Account.login == login)).first()


class TicketRepo:
    def __init__(self, session: Session):
        self.session = session

    def mine(self, owner_id: int):
        return self.session.exec(select(Ticket).where(Ticket.owner_id == owner_id)).all()
"""

# the current-user dependency: python-jose, a repository, no return annotation
DEPS = """
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app.db import get_session
from app.repo import AccountRepo, TicketRepo
from app.settings import settings

oauth2 = OAuth2PasswordBearer(tokenUrl="/login")


def get_accounts(session=Depends(get_session)):
    return AccountRepo(session)


def get_tickets(session=Depends(get_session)):
    return TicketRepo(session)


def get_caller(token: str = Depends(oauth2), repo: AccountRepo = Depends(get_accounts)):
    try:
        payload = jwt.decode(token, settings.secret, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="bad token")
    account_id = payload.get("sub")
    account = repo.by_id(int(account_id))
    if account is None:
        raise HTTPException(status_code=401, detail="unknown")
    return account
"""

ROUTES = """
from fastapi import APIRouter, Depends, Request

from app.deps import get_caller, get_tickets
from app.models import AccountOut, Status, TicketOut
from app.repo import TicketRepo

router = APIRouter(prefix="/tickets")


@router.get("/me", response_model=AccountOut)
def me(caller=Depends(get_caller)):
    return caller


@router.get("", response_model=list[TicketOut])
def mine(status: Status | None = None, caller=Depends(get_caller),
         repo: TicketRepo = Depends(get_tickets)):
    return repo.mine(caller.id)


@router.get("/ping")
def ping(request: Request):
    return {"ok": True}
"""

MAIN = """
from fastapi import FastAPI

from app import routes


def create_app():
    application = FastAPI()
    application.include_router(routes.router)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    return application
"""

FILES = {
    "models": MODELS,
    "settings": SETTINGS,
    "db": DB,
    "repo": REPO,
    "deps": DEPS,
    "routes": ROUTES,
    "main": MAIN,
}


@pytest.fixture(autouse=True)
def _isolated_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """The generated profile imports the snippet package (its natives): each test's
    package gets its own name and sys.path entry."""
    monkeypatch.setattr(sys, "path", list(sys.path))


def package_name(root: Path) -> str:
    return "synth_" + "".join(c if c.isalnum() else "_" for c in root.name)


def write_repo(root: Path, **overrides: str) -> Path:
    pkg_name = package_name(root)
    pkg = root / pkg_name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    for name, src in {**FILES, **overrides}.items():
        src = src.replace("from app.", f"from {pkg_name}.").replace(
            "from app import", f"from {pkg_name} import"
        )
        (pkg / f"{name}.py").write_text(textwrap.dedent(src))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def model_of(tmp_path: Path, **overrides: str) -> RepoModel:
    return extract(write_repo(tmp_path, **overrides), package_name(tmp_path))


def index_of(tmp_path: Path) -> _Index:
    return _Index(tmp_path, package_name(tmp_path))


def load(result: ProfileResult) -> ModuleType:
    module = ModuleType("generic_import_profile")
    exec(compile(result.source, "<generic_import_profile>", "exec"), module.__dict__)  # noqa: S102
    return module


def column(model: RepoModel, table: str, name: str):
    t = next(t for t in model.tables if t.name == table)
    return next(c for c in t.columns if c.name == name)


def unclear(result: ProfileResult, topic: str) -> list[str]:
    return [u.reason for u in result.unclear if u.topic == topic]


# ---- column types ---------------------------------------------------------------------------


def test_naive_and_aware_datetime_aliases_are_datetimes(tmp_path: Path) -> None:
    model = model_of(tmp_path)
    assert column(model, "Account", "joined").type == "DateTime"
    seen = column(model, "Account", "seen")
    assert (seen.type, seen.nullable) == ("DateTime", True)
    assert column(model, "Ticket", "due").type == "DateTime"


def test_enum_and_literal_columns_keep_their_values(tmp_path: Path) -> None:
    model = model_of(tmp_path)
    status = column(model, "Ticket", "status")
    assert (status.type, status.choices) == ("Str", ("open", "done"))
    level = column(model, "Ticket", "level")
    assert (level.type, level.choices) == ("Int", (1, 2))
    body = next(m for m in model.models if m.name == "TicketIn")
    kind = next(f for f in body.fields if f.name == "kind")  # Literal: a body model's field
    assert (kind.type, kind.choices, kind.required) == ("Str", ("bug", "idea"), False)
    # a query parameter typed by the Enum, optional
    route = next(r for r in model.routes if r.name == "mine")
    p = next(p for p in route.params if p.name == "status")
    assert (p.type, p.source, p.choices, p.required) == ("Str", "query", ("open", "done"), False)


def test_str_enum_columns_are_one_of_and_int_enums_a_language_gap(tmp_path: Path) -> None:
    result = generate_profile(model_of(tmp_path))
    source = result.source
    assert "('status', Scalar.STR)" in source and "('level', Scalar.INT)" in source
    ticket = load(result).profile().api.tables["Ticket"]
    assert ticket.choices("status") == ("open", "done")
    # SQLAlchemy stores an Enum column by member name
    assert ticket.stored_spellings() == (("status", (("open", "OPEN"), ("done", "DONE"))),)
    assert ticket.choices("level") is None  # an Int enum: no OneOf spelling
    notes = [u for u in result.unclear if u.topic == "column_type"]
    assert {u.subject for u in notes} == {"Ticket.level"}
    assert all(f"{LANGUAGE_GAP} (enum constraint)" in u.reason for u in notes)


def test_a_literal_column_is_stored_as_its_values(tmp_path: Path) -> None:
    models = MODELS.replace("    status: Status = Field(default=Status.OPEN)\n",
                            '    status: Literal["open", "done"] = "open"\n')  # fmt: skip
    ticket = load(generate_profile(model_of(tmp_path, models=models))).profile().api
    assert ticket.tables["Ticket"].choices("status") == ("open", "done")
    assert ticket.tables["Ticket"].stored_spellings() == ()


def test_a_mixed_enum_is_not_a_scalar(tmp_path: Path) -> None:
    models = MODELS.replace('DONE = "done"', "DONE = 2")
    assert column(model_of(tmp_path, models=models), "Ticket", "status").type is None


def test_integer_keys_become_int_keys_and_the_seed_uses_integers(tmp_path: Path) -> None:
    model = model_of(tmp_path)
    api = load(generate_profile(model)).profile().api
    tickets = table_spec(next(t for t in model.tables if t.name == "Ticket"), api)
    assert (tickets.key.name, tickets.key.type) == ("id", "int")
    accounts = table_spec(next(t for t in model.tables if t.name == "Account"), api)
    auth = AuthSpec(users_table="accounts", email_column="login", key_type="int")
    plan = seed_plan((accounts, tickets), auth)
    assert [u.id for u in plan.users] == [1, 2, 3, 4]
    assert all(isinstance(r["owner_id"], int) for r in plan.rows["ticket"])
    assert {r["owner_id"] for r in plan.rows["ticket"]} <= {1, 2, 3, 4}
    # enum columns are seeded with their own values only
    assert {r["status"] for r in plan.rows["ticket"]} == {"open", "done"}
    assert {r["level"] for r in plan.rows["ticket"]} == {1, 2}
    assert auth.missing_user == MISSING_INT


def test_int_path_ids_and_enum_parameters_in_the_suite(tmp_path: Path) -> None:
    model = model_of(tmp_path)
    api = load(generate_profile(model)).profile().api
    tables = tuple(table_spec(t, api) for t in model.tables)
    auth = AuthSpec(users_table="accounts", email_column="login", key_type="int")
    status = ParamSpec("status", "str", "body", choices=("open", "done"))
    route = RouteSpec(
        "edit", "PUT", "/tickets/{id}", "update", "ticket", auth="public",
        params=(ParamSpec("id", "int", "path"), status),
    )  # fmt: skip
    cases = {c.name: c for c in generate_suite([route], tables, auth, hypothesis_bodies=0).cases}
    assert cases["edit.missing_id"].path == f"/tickets/{MISSING_INT}"
    assert cases["edit.happy"].json == {"status": "open"}
    # a value outside the enum is requested, so a lift that drops the constraint fails
    assert cases["edit.boundary.status.not_a_choice"].json == {"status": NOT_A_CHOICE}
    bodies = hypothesis_bodies_for(route, 4, 31)
    assert bodies and all(b["status"] in ("open", "done") for b in bodies)


# ---- the database and the dependencies --------------------------------------------------------


def test_the_dialect_comes_from_the_settings_default(tmp_path: Path) -> None:
    model = model_of(tmp_path)
    assert (model.database_url, model.dialect) == ("sqlite:///./tickets.db", "sqlite")
    two = SETTINGS.replace("secret: str", 'other: str = "postgresql://h/db"\n    secret: str')
    assert model_of(tmp_path / "two", settings=two).dialect is None  # ambiguous: not guessed


def test_unannotated_session_generators_and_repositories(tmp_path: Path) -> None:
    deps = {d.name: d for d in model_of(tmp_path).dependencies}
    assert deps["get_session"].kind == "session"
    pkg = package_name(tmp_path)
    assert (deps["get_accounts"].kind, deps["get_accounts"].provides) == (
        "repository",
        f"{pkg}.repo:AccountRepo",
    )
    assert deps["get_tickets"].provides == f"{pkg}.repo:TicketRepo"


def test_a_repository_needs_the_session_only(tmp_path: Path) -> None:
    deps = DEPS.replace(
        "def get_accounts(session=Depends(get_session)):",
        "def get_accounts(session=Depends(get_session), token: str = Depends(oauth2)):",
    )
    found = {d.name: d for d in model_of(tmp_path, deps=deps).dependencies}
    assert found["get_accounts"].kind == "unknown"


def test_the_users_table_is_what_the_current_user_dependency_returns(tmp_path: Path) -> None:
    model = model_of(tmp_path)
    caller = next(d for d in model.dependencies if d.name == "get_caller")
    assert (caller.kind, caller.returns) == ("current_user", "Account")
    routes = {r.name: r for r in model.routes}
    assert routes["me"].auth == "Customer"
    # tables reached through a repository method
    assert routes["mine"].tables == ("Ticket",)


def test_the_users_table_through_session_get(tmp_path: Path) -> None:
    deps = DEPS.replace(
        "    account = repo.by_id(int(account_id))\n",
        "    account = session.get(Account, int(account_id))\n",
    ).replace(
        "repo: AccountRepo = Depends(get_accounts)):",
        "session=Depends(get_session)):",
    ).replace("from app.settings import settings", "from app.models import Account\n"
              "from app.settings import settings")  # fmt: skip
    caller = next(d for d in model_of(tmp_path, deps=deps).dependencies if d.name == "get_caller")
    assert (caller.kind, caller.returns) == ("current_user", "Account")


# ---- the token decoder --------------------------------------------------------------------------


def test_python_jose_decode_is_the_token_decoder(tmp_path: Path) -> None:
    model = model_of(tmp_path)
    decoder = next(n for n in model.natives if n.kind == "token_decoder")
    assert decoder.expressible, decoder.reason
    assert decoder.decode_expr == "jwt.decode(token, settings.secret, algorithms=['HS256'])"
    assert "from jose import jwt" in decoder.imports
    assert "from jose import JWTError" in decoder.imports
    assert decoder.result_type == "Int"
    api = load(generate_profile(model)).profile().api
    assert api.auth == NativeToken("decode_token") and api.users_table == "Account"


def test_pyjwt_decode_is_still_the_token_decoder(tmp_path: Path) -> None:
    deps = DEPS.replace("from jose import JWTError, jwt", "import jwt\nfrom jwt import PyJWTError")
    deps = deps.replace("except JWTError:", "except PyJWTError:")
    decoder = next(n for n in model_of(tmp_path, deps=deps).natives if n.kind == "token_decoder")
    assert decoder.expressible and decoder.decode_expr.startswith("jwt.decode(token")


def test_a_decode_inside_a_helper_is_followed(tmp_path: Path) -> None:
    deps = DEPS.replace(
        """    try:
        payload = jwt.decode(token, settings.secret, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="bad token")
""",
        """    payload = read_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="bad token")
""",
    ).replace(
        "def get_caller(",
        """def read_token(raw: str):
    try:
        return jwt.decode(raw, settings.secret, algorithms=["HS256"])
    except JWTError:
        return None


def get_caller(""",
    )
    decoder = next(n for n in model_of(tmp_path, deps=deps).natives if n.kind == "token_decoder")
    assert decoder.expressible, decoder.reason
    assert decoder.decode_expr == "read_token(token)"
    assert f"from {package_name(tmp_path)}.deps import read_token" in decoder.imports


HELPER_DEPS = DEPS.replace(
    """    try:
        payload = jwt.decode(token, settings.secret, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="bad token")
    account_id = payload.get("sub")
""",
    """    payload = read_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="bad token")
    account_id: str | None = payload.get("sub")
    if account_id is None:
        raise HTTPException(status_code=401, detail="no subject")
""",
).replace(
    "def get_caller(",
    """def read_token(raw: str):
    try:
        return jwt.decode(raw, settings.secret, algorithms=["HS256"])
    except JWTError:
        return None


def get_caller(""",
)


@pytest.mark.parametrize("deps", [DEPS, HELPER_DEPS], ids=["inline", "helper"])
def test_a_claims_minter_is_derived_from_the_decode(tmp_path: Path, deps: str) -> None:
    """With no single-argument token mint, the suite mints its own tokens with the
    decode's library, key and algorithm (followed into a helper): a valid token naming
    no user, and one without the claim -- so the dependency's branches for them are
    reached."""
    from jose import jwt

    model = model_of(tmp_path, deps=deps)
    module = load(generate_profile(model))
    inputs = adapt(model, module.profile().api, index_of(tmp_path), module.NATIVES)
    auth = inputs.auth
    assert auth.mint_token is None and auth.mint_claims is not None
    assert jwt.decode(auth.mint_claims({"sub": "7"}), "s", algorithms=["HS256"])["sub"] == "7"
    suite = generate_suite(list(inputs.routes.values()), inputs.tables, auth,
                           hypothesis_bodies=0)  # fmt: skip
    names = {c.name for c in suite.cases if c.route == "me"}
    assert {"me.unknown_user_token", "me.claimless_token"} <= names


def test_token_policies_follow_a_decode_helper_and_a_caller_lookup(tmp_path: Path) -> None:
    """``payload = helper(token); if payload is None: raise`` (an invalid token),
    ``sub = payload.get("sub"); if sub is None: raise`` (no claim) and ``caller =
    <lookup>(...); if caller is None: raise`` (no such user): the original's answers."""
    from lattice.importer.policy import auth_policies

    model = model_of(tmp_path, deps=HELPER_DEPS)
    api = load(generate_profile(model)).profile().api
    found = {p.case: p.original for p in auth_policies(model, index_of(tmp_path), api)}
    assert found == {
        "bad_token": (401, "bad token"),
        "claimless_token": (401, "no subject"),
        "unknown_user_token": (401, "unknown"),
    }


def _request_deps(read: str) -> str:
    deps = DEPS.replace("def get_caller(token", "def get_caller(request: Request, token").replace(
        "settings.secret, algorithms", f"{read}, algorithms"
    )
    return deps.replace("from fastapi import Depends", "from fastapi import Depends, Request")


def test_a_decode_reading_app_state_reads_a_native_context_setting(tmp_path: Path) -> None:
    model = model_of(tmp_path, deps=_request_deps("request.app.state.config.secret"))
    decoder = next(n for n in model.natives if n.kind == "token_decoder")
    assert decoder.expressible, decoder.reason
    # the Settings class annotates `secret: str`: a Str setting, read from app.state
    assert decoder.settings == (("secret", "Str", "state.config.secret"),)
    assert decoder.decode_expr == "jwt.decode(token, ctx['secret'], algorithms=['HS256'])"
    module = load(generate_profile(model))
    api = module.profile().api
    assert api.settings == {"secret": Scalar.STR} and api.users_table == "Account"
    assert api.natives["decode_token"].settings == ("secret",)
    app = SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(secret="k")))
    assert module.settings_from_app(app) == {"secret": "k"}


def test_a_decode_reading_other_request_state_is_not_expressible(tmp_path: Path) -> None:
    model = model_of(tmp_path, deps=_request_deps("request.headers['x-key']"))
    decoder = next(n for n in model.natives if n.kind == "token_decoder")
    assert not decoder.expressible and "request" in decoder.reason
    result = generate_profile(model)
    assert any("request" in r for r in unclear(result, "auth"))
    api = load(result).profile().api  # still a valid profile, without auth
    assert api.users_table != "Account"


BY_LOGIN = ("account = repo.by_id(int(account_id))", "account = repo.by_login(account_id)")


def test_a_claim_naming_a_unique_column_is_a_token_lookup(tmp_path: Path) -> None:
    model = model_of(tmp_path, deps=DEPS.replace(*BY_LOGIN))
    decoder = next(n for n in model.natives if n.kind == "token_decoder")
    assert decoder.expressible, decoder.reason
    assert (decoder.lookup, decoder.result_type) == ("login", "Str")
    api = load(generate_profile(model)).profile().api
    assert api.auth == NativeToken("decode_token", lookup="login")
    assert api.tables["Account"].keys == (("id",), ("login",))
    assert api.natives["decode_token"].result is Scalar.STR


def test_a_claim_naming_a_non_unique_column_is_a_runtime_checked_lookup(
    tmp_path: Path,
) -> None:
    """The column is not declared unique (the app checks it in code only): the lookup is
    checked at runtime (unique="runtime": two rows are a loud 500), the column is not
    made a key, and an Unclear note says so."""
    models = MODELS.replace("login: str = Field(unique=True, max_length=50)",
                            "login: str = Field(max_length=50)")  # fmt: skip
    model = model_of(tmp_path, deps=DEPS.replace(*BY_LOGIN), models=models)
    decoder = next(n for n in model.natives if n.kind == "token_decoder")
    assert decoder.expressible, decoder.reason
    assert (decoder.lookup, decoder.lookup_unique) == ("login", "runtime")
    result = generate_profile(model)
    api = load(result).profile().api
    assert api.auth == NativeToken("decode_token", lookup="login", unique="runtime")
    assert api.tables["Account"].keys == (("id",),)
    notes = unclear(result, "auth")
    assert any("Account.login" in r and "not declared unique" in r for r in notes)


SECURITY = """
def hash_password(password: str) -> str:
    return "h:" + password
"""

REGISTER = ROUTES + """

from app.models import Account
from app.security import hash_password


@router.post("/register", response_model=AccountOut)
def register(login: str, password: str, repo=Depends(get_tickets)):
    digest = hash_password(password)
    account = Account(login=login, password_digest=digest)
    repo.session.add(account)
    repo.session.commit()
    return account
"""


def test_the_hasher_is_found_through_a_local_name(tmp_path: Path) -> None:
    """``digest = hash_password(p); Account(..., password_digest=digest)``: the native
    stored in the password column is followed through the local name, so the seed
    stores hashed passwords (the original's login can verify them)."""
    model = model_of(tmp_path, security=SECURITY, routes=REGISTER)
    module = load(generate_profile(model))
    inputs = adapt(model, module.profile().api, index_of(tmp_path), module.NATIVES)
    assert inputs.auth.password_column == "password_digest"
    assert inputs.auth.hash_password is not None
    assert inputs.auth.hash_password("pw") == "h:pw"


SIGNUP_MODELS = MODELS + """

class AccountIn(SQLModel):
    login: str = Field(max_length=50)
    password: str
"""

SIGNUP = ROUTES + """

from fastapi import HTTPException

from app.deps import get_accounts
from app.models import AccountIn
from app.repo import AccountRepo


def login_taken(repo, login):
    return repo.by_login(login) is not None


@router.post("/signup", status_code=201)
def signup(body: AccountIn, repo: AccountRepo = Depends(get_accounts)):
    if login_taken(repo, body.login):
        raise HTTPException(status_code=409, detail="taken")
    return {"ok": True}
"""


def test_a_conflict_on_a_unique_column_gets_a_case_without_a_known_table(
    tmp_path: Path,
) -> None:
    """The route's table is not known (its writes go through helpers), but its 409 is on
    a body field only one table declares unique: the suite requests a taken value."""
    model = model_of(tmp_path, models=SIGNUP_MODELS, routes=SIGNUP)
    module = load(generate_profile(model))
    inputs = adapt(model, module.profile().api, index_of(tmp_path), module.NATIVES)
    spec = inputs.routes["signup"]
    assert [(e.status, e.cause, e.column) for e in spec.errors] == [(409, "unique", "login")]
    suite = generate_suite([spec], inputs.tables, inputs.auth, hypothesis_bodies=0)
    body = {c.name: c for c in suite.cases}["signup.error.409.unique"].json
    assert body["login"] in {u.email for u in suite.plan.users}
    assert "password" in body  # the rest of a valid body: only the taken value is wrong


def test_the_seed_owns_rows_through_the_one_foreign_key_to_the_users_table(
    tmp_path: Path,
) -> None:
    """With ``repo.mine`` handed the caller's id, ``Ticket.owner_id`` is the owner (the
    repository method filters it). Without it the profile does not infer the owner (no
    route filters it: the table is shared), but the suite still spreads and requests rows
    by it: a route's "own" id is a row of the acting user, not whichever row comes
    first."""
    owned = load(generate_profile(model_of(tmp_path / "owned"))).profile().api
    assert owned.tables["Ticket"].owner == "owner_id"
    model = model_of(tmp_path, routes=ROUTES.replace("repo.mine(caller.id)", "repo.mine(0)"))
    api = load(generate_profile(model)).profile().api
    assert api.tables["Ticket"].owner is None
    ticket = next(t for t in model.tables if t.name == "Ticket")
    assert table_spec(ticket, api).owner == "owner_id"


# ---- graceful degradation ------------------------------------------------------------------


def test_a_single_role_app_gets_no_roles_and_suites_for_its_user_routes(
    tmp_path: Path,
) -> None:
    model = model_of(tmp_path)
    result = generate_profile(model)
    assert not any("single-role" in r for r in unclear(result, "roles"))
    module = load(result)
    api = module.profile().api
    assert api.roles_from == NoRoles("Customer")
    assert auth_blocker(model, api) is None
    inputs = adapt(model, api, index_of(tmp_path), module.NATIVES)
    assert "me" in inputs.routes and "mine" in inputs.routes
    assert inputs.auth.users_table == "accounts" and inputs.auth.key_type == "int"
    assert inputs.auth.email_column == "login" and inputs.auth.password_column == "password_digest"
    assert inputs.auth.admin_column is None


def test_an_app_with_a_role_column_keeps_its_roles(tmp_path: Path) -> None:
    role = "    password_digest: str\n    role: str\n"
    models = MODELS.replace("    password_digest: str\n", role)
    api = load(generate_profile(model_of(tmp_path, models=models))).profile().api
    assert not isinstance(api.roles_from, NoRoles)


def test_the_gap_classifier_still_names_a_language_gap(tmp_path: Path) -> None:
    model = model_of(tmp_path)
    me = next(r for r in model.routes if r.name == "me")
    result = LiftResult("me", me, "no-lifter", RouteLift("me", ()))
    why = f"no request suite: {LANGUAGE_GAP} (token subject is not a unique users column): x"
    o = RouteOutcome("me", result, "island", "R4", reason=why)
    assert classify_gap(o) == f"{LANGUAGE_GAP}: token subject is not a unique users column"


def test_framework_parameters_are_not_request_data(tmp_path: Path) -> None:
    model = model_of(tmp_path)
    ping = next(r for r in model.routes if r.name == "ping")
    assert [(p.name, p.source) for p in ping.params] == [("request", "framework")]
    module = load(generate_profile(model))
    inputs = adapt(model, module.profile().api, index_of(tmp_path), module.NATIVES)
    assert inputs.routes["ping"].params == ()


def test_routes_an_app_factory_defines_are_found_and_not_guessed(tmp_path: Path) -> None:
    model = model_of(tmp_path)
    health = next(r for r in model.routes if r.name == "health")
    assert (health.method, health.path, health.enclosing) == ("GET", "/health", "create_app")
    module = load(generate_profile(model))
    inputs = adapt(model, module.profile().api, index_of(tmp_path), module.NATIVES)
    assert "defined inside create_app()" in inputs.skipped["health"]


def test_unsupported_columns_and_a_missing_users_table_never_crash(tmp_path: Path) -> None:
    models = MODELS.replace(
        "    due: dt.datetime | None = None\n",
        "    tags: dict = Field(default_factory=dict, sa_type=JSON)\n",
    ).replace("from sqlmodel import", "from sqlalchemy import JSON\nfrom sqlmodel import")
    deps = DEPS.replace("    return account\n", "    return account.id\n")  # no users table
    model = model_of(tmp_path, models=models, deps=deps)
    result = generate_profile(model)
    assert any(u.subject == "Ticket.tags" for u in result.unclear if u.topic == "column_type")
    module = load(result)
    inputs = adapt(model, module.profile().api, index_of(tmp_path), module.NATIVES)
    assert "cannot be seeded" in inputs.skipped["mine"]  # its table has the unsupported column
    assert "ping" in inputs.routes  # a Public route still has its suite
    assert inputs.skipped["me"].startswith("auth unclear")  # the dependency returns no row
    assert inputs.auth.users_table == ""  # no users rows in the seed


def test_the_users_table_is_found_from_the_model_when_the_profile_has_none(
    tmp_path: Path,
) -> None:
    # a decode reading request state a native cannot see: the profile has no auth
    model = model_of(tmp_path, deps=_request_deps("request.headers['x-key']"))
    module = load(generate_profile(model))
    api = module.profile().api
    assert api.users_table != "Account"  # no auth: left unset
    inputs = adapt(model, api, index_of(tmp_path), module.NATIVES)
    assert inputs.auth.users_table == "accounts"
    assert "request" in inputs.skipped["me"]


def test_a_profile_the_language_refuses_falls_back_without_its_auth(tmp_path: Path) -> None:
    # an "active" flag that is not a Bool column: ApiProfile refuses active_column
    models = MODELS.replace(
        "    joined: NaiveDatetime\n", "    joined: NaiveDatetime\n    is_active: int = 1\n"
    )
    deps = DEPS.replace(
        "    return account\n",
        "    if not account.is_active:\n"
        '        raise HTTPException(status_code=400, detail="Inactive")\n'
        "    return account\n",
    )
    result = generate_profile(model_of(tmp_path, models=models, deps=deps))
    (note,) = [u for u in result.unclear if u.topic == "profile"]
    assert "active_column" in note.reason and "active_column" in note.candidates
    api = load(result).profile().api  # the fallback profile builds
    assert api.active_column is None and not isinstance(api.auth, NativeToken)


# ---- column defaults an insert must reproduce ------------------------------------------------

DEFAULTS = """
from datetime import UTC, datetime


def naive_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def local_naive() -> datetime:
    return datetime.now().replace(tzinfo=None)


def new_code() -> str:
    return "c"
"""


def test_insert_defaults_naive_utc_now_and_unspellable_factories(tmp_path: Path) -> None:
    models = MODELS.replace(
        "    due: dt.datetime | None = None\n",
        "    due: dt.datetime | None = None\n"
        "    stamp: NaiveDatetime | None = Field(default_factory=naive_now)\n"
        "    local: NaiveDatetime | None = Field(default_factory=local_naive)\n"
        "    code: str | None = Field(default_factory=new_code)\n"
        "    plain: str | None\n",
    ).replace("from sqlmodel import", "from app.defaults import local_naive, naive_now, new_code\n"
              "from sqlmodel import")  # fmt: skip
    model = model_of(tmp_path, models=models, defaults=DEFAULTS)
    module = load(generate_profile(model))
    fn = index_of(tmp_path).modules[f"{package_name(tmp_path)}.routes"].defs["me"]
    route = next(r for r in model.routes if r.name == "me")
    src = RouteSource(route, fn, index_of(tmp_path), module.profile().api, model)  # type: ignore[arg-type]
    ticket = next(t for t in model.tables if t.name == "Ticket")
    # datetime.now(UTC).replace(tzinfo=None): the current instant, naive -> fn.now()
    assert column_default(src, ticket, "stamp") == ("now", None)
    # not UTC, or not a time at all: no Lattice spelling -- and not "stores NULL" either
    assert column_default(src, ticket, "local") is None and has_default(src, ticket, "local")
    assert column_default(src, ticket, "code") is None and has_default(src, ticket, "code")
    assert column_default(src, ticket, "due") == ("literal", None)
    assert not has_default(src, ticket, "plain")  # no default: the insert stores NULL


# ---- FastAPI's credentials_exception idiom ----------------------------------------------------

CREDENTIALS = DEPS.replace(
    "def get_caller(token: str = Depends(oauth2), repo: AccountRepo = Depends(get_accounts)):\n",
    "def get_caller(token: str = Depends(oauth2), repo: AccountRepo = Depends(get_accounts)):\n"
    '    credentials_exception = HTTPException(status_code=401, detail="Could not validate")\n',
).replace(
    '        raise HTTPException(status_code=401, detail="bad token")\n',
    "        raise credentials_exception\n",
).replace(
    '    if account is None:\n        raise HTTPException(status_code=401, detail="unknown")\n',
    "    if account is None or not account.is_active:\n        raise credentials_exception\n",
)
ACTIVE_MODELS = MODELS.replace(
    "    joined: NaiveDatetime\n", "    joined: NaiveDatetime\n    is_active: bool = True\n"
)


def test_a_raised_exception_variable_is_a_raise_site(tmp_path: Path) -> None:
    """``raise credentials_exception`` (assigned an HTTPException once) is read like the
    call it holds: its status and detail, and an ``or``-ed active check."""
    model = model_of(tmp_path, deps=CREDENTIALS, models=ACTIVE_MODELS)
    caller = next(d for d in model.dependencies if d.name == "get_caller")
    assert caller.kind == "current_user"
    assert {(r.status, r.detail) for r in caller.raises} == {(401, "Could not validate")}
    assert (caller.active_column, caller.active_status, caller.active_detail) == (
        "is_active", 401, "Could not validate",
    )  # fmt: skip
    api = load(generate_profile(model)).profile().api
    assert (api.active_column, api.inactive_status) == ("is_active", 401)


def test_an_exception_variable_assigned_twice_is_not_resolved(tmp_path: Path) -> None:
    deps = CREDENTIALS.replace(
        "        raise credentials_exception\n",
        '        credentials_exception = HTTPException(status_code=403, detail="x")\n'
        "        raise credentials_exception\n",
        1,
    )
    model = model_of(tmp_path, deps=deps, models=ACTIVE_MODELS)
    caller = next(d for d in model.dependencies if d.name == "get_caller")
    assert caller.raises == () and caller.active_column is None
