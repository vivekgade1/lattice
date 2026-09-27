"""The importer on FastAPI + SQLModel shapes beyond the template (plan: generic, gracefully
degrading import). Every test is a small synthetic snippet repo -- never the held-out
repos' code:

* naive / aware datetimes, integer keys, Enum and ``Literal`` columns;
* the database dialect from a settings default when the engine's URL is not a constant;
* unannotated session generators, repository dependencies (``return Repo(session)``),
  the users table from what the current-user dependency returns (a repository method,
  ``session.get``) rather than from a table's name;
* the token decoder through python-jose as well as PyJWT, in the dependency or in a
  helper; not expressible (with the reason) when it reads request state or when its
  claim names a users column other than the key (a language gap);
* graceful degradation: unsupported columns, no users table, single-role apps, framework
  parameters (``Request``) and routes an app factory defines are reported (unclear /
  skipped with the reason), never a crash.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from types import ModuleType

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
from lattice.profile import NativeToken

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


def test_enum_columns_are_str_with_a_language_gap_note(tmp_path: Path) -> None:
    result = generate_profile(model_of(tmp_path))
    source = result.source
    assert "('status', Scalar.STR)" in source and "('level', Scalar.INT)" in source
    notes = [u for u in result.unclear if u.topic == "column_type"]
    assert {u.subject for u in notes} == {"Ticket.status", "Ticket.level"}
    assert all(f"{LANGUAGE_GAP} (enum constraint)" in u.reason for u in notes)


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


def test_a_decode_reading_request_state_is_not_expressible(tmp_path: Path) -> None:
    deps = DEPS.replace("def get_caller(token", "def get_caller(request: Request, token").replace(
        "settings.secret, algorithms", "request.app.state.secret, algorithms"
    )
    deps = deps.replace("from fastapi import Depends", "from fastapi import Depends, Request")
    model = model_of(tmp_path, deps=deps)
    decoder = next(n for n in model.natives if n.kind == "token_decoder")
    assert not decoder.expressible and "request" in decoder.reason
    result = generate_profile(model)
    assert any("request" in r for r in unclear(result, "auth"))
    api = load(result).profile().api  # still a valid profile, without auth
    assert api.users_table != "Account"


def test_a_claim_naming_a_non_key_column_is_a_language_gap(tmp_path: Path) -> None:
    deps = DEPS.replace(
        "account = repo.by_id(int(account_id))", "account = repo.by_login(account_id)"
    )
    model = model_of(tmp_path, deps=deps)
    decoder = next(n for n in model.natives if n.kind == "token_decoder")
    assert not decoder.expressible
    assert f"{LANGUAGE_GAP} (token subject is not the users key)" in decoder.reason
    assert "Account.login" in decoder.reason


# ---- graceful degradation ------------------------------------------------------------------


def test_a_single_role_app_reports_the_roles_gap_and_islands_its_user_routes(
    tmp_path: Path,
) -> None:
    model = model_of(tmp_path)
    result = generate_profile(model)
    assert any(f"{LANGUAGE_GAP} (single-role auth)" in r for r in unclear(result, "roles"))
    module = load(result)
    api = module.profile().api
    blocker = auth_blocker(model, api)
    assert blocker is not None and "single-role auth" in blocker
    inputs = adapt(model, api, index_of(tmp_path), module.NATIVES)
    assert "single-role auth" in inputs.skipped["me"]
    assert "single-role auth" in inputs.skipped["mine"]
    me = next(r for r in model.routes if r.name == "me")
    result = LiftResult("me", me, "no-lifter", RouteLift("me", ()))
    o = RouteOutcome(
        "me", result, "island", "R4", reason=f"no request suite: {inputs.skipped['me']}"
    )
    assert classify_gap(o) == f"{LANGUAGE_GAP}: single-role auth"
    assert inputs.auth.users_table == "accounts" and inputs.auth.key_type == "int"
    assert inputs.auth.email_column == "login" and inputs.auth.password_column == "password_digest"


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
    deps = DEPS.replace(
        "account = repo.by_id(int(account_id))", "account = repo.by_login(account_id)"
    )
    model = model_of(tmp_path, deps=deps)
    module = load(generate_profile(model))
    api = module.profile().api
    assert api.users_table != "Account"  # no auth: left unset
    inputs = adapt(model, api, index_of(tmp_path), module.NATIVES)
    assert inputs.auth.users_table == "accounts"
    assert "token subject is not the users key" in inputs.skipped["me"]


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
