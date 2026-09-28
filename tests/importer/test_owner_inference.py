"""Owner inference (plan node C2): a table's owner column from generic evidence.

The rule (lattice.importer.profile_gen): a table's owner is its single foreign key to
the users table (a) when some route -- directly or through the helpers and repository
methods it calls with the caller or the caller's id -- filters or checks that column
against the caller (b) or sets it from the caller on create (c), and no authenticated
non-admin route returns rows of the table without any owner check (that makes the table
*shared*, recorded as such). A table with no user foreign key whose foreign-key parent is
owned, and whose routes check through that parent, is owned through it (``owner_via``).

Every app here is a small synthetic snippet repo (bookmarks, folders, labels) -- never
the held-out repos' code.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from types import ModuleType

from lattice.importer.extract import extract
from lattice.importer.profile_gen import ProfileResult, generate_profile

MODELS = """
from sqlmodel import Field, SQLModel


class Account(SQLModel, table=True):
    __tablename__ = "accounts"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True)
    is_active: bool = True
    is_superuser: bool = False


class Bookmark(SQLModel, table=True):
    __tablename__ = "bookmarks"

    id: int | None = Field(default=None, primary_key=True)
    url: str
    is_public: bool = False
    account_id: int = Field(foreign_key="accounts.id")
EXTRA_FK


class BookmarkIn(SQLModel):
    url: str


class BookmarkOut(SQLModel):
    id: int
    url: str


class Label(SQLModel, table=True):
    __tablename__ = "labels"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    bookmark_id: int = Field(foreign_key="bookmarks.id")


class LabelOut(SQLModel):
    id: int
    name: str


class Message(SQLModel):
    message: str
"""

DEPS = """
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from app.models import Account

oauth2 = OAuth2PasswordBearer(tokenUrl="/login")


def get_db() -> Session:
    return Session()


SessionDep = Annotated[Session, Depends(get_db)]


def get_caller(session: SessionDep, token: Annotated[str, Depends(oauth2)]) -> Account:
    payload = jwt.decode(token, "k", algorithms=["HS256"])
    account = session.get(Account, int(payload["sub"]))
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


Caller = Annotated[Account, Depends(get_caller)]


def get_admin(caller: Caller) -> Account:
    if not caller.is_superuser:
        raise HTTPException(status_code=403, detail="Not a superuser")
    return caller
"""

MAIN = """
import sqlmodel

from fastapi import FastAPI

from app import routes

engine = sqlmodel.create_engine("sqlite://")
app = FastAPI()
app.include_router(routes.router)
"""

HEAD = """
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app import store
from app.deps import Caller, SessionDep, get_admin
from app.models import Account, Bookmark, BookmarkIn, BookmarkOut, Label, LabelOut, Message

router = APIRouter()
"""

# (b) a direct filter and (c) a create assigning the caller
DIRECT = """
@router.get("/bookmarks", response_model=list[BookmarkOut])
def list_bookmarks(session: SessionDep, caller: Caller):
    return session.exec(select(Bookmark).where(Bookmark.account_id == caller.id)).all()


@router.post("/bookmarks", response_model=BookmarkOut)
def add_bookmark(session: SessionDep, caller: Caller, body: BookmarkIn):
    row = Bookmark(url=body.url, account_id=caller.id)
    session.add(row)
    session.commit()
    return row
"""

# (b) through module-level CRUD helpers, the caller's id passed positionally, the caller
# passed whole
STORE = """
from fastapi import HTTPException
from sqlmodel import Session, select

from app.models import Bookmark, BookmarkIn


def fetch(session: Session, bookmark_id: int, uid: int) -> Bookmark:
    row = session.exec(
        select(Bookmark).where(Bookmark.id == bookmark_id, Bookmark.account_id == uid)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    return row


def add(session: Session, body: BookmarkIn, uid: int) -> Bookmark:
    row = Bookmark.model_validate(body, update={"account_id": uid})
    session.add(row)
    session.commit()
    return row


def check(row: Bookmark, who) -> None:
    if row.account_id != who.id:
        raise HTTPException(status_code=403, detail="Not yours")
"""

VIA_HELPERS = """
@router.get("/bookmarks/{bookmark_id}", response_model=BookmarkOut)
def read_bookmark(session: SessionDep, caller: Caller, bookmark_id: int):
    return store.fetch(session, bookmark_id, caller.id)


@router.post("/bookmarks", response_model=BookmarkOut)
def add_bookmark(session: SessionDep, caller: Caller, body: BookmarkIn):
    return store.add(session, body, caller.id)


@router.delete("/bookmarks/{bookmark_id}", response_model=Message)
def drop_bookmark(session: SessionDep, caller: Caller, bookmark_id: int):
    row = session.get(Bookmark, bookmark_id)
    store.check(row, caller)
    session.delete(row)
    session.commit()
    return Message(message="ok")
"""

# (b) through a repository class a dependency provides
REPO = """
from sqlmodel import Session, select

from app.models import Bookmark


class BookmarkRepo:
    def __init__(self, session: Session):
        self.session = session

    def for_account(self, uid: int):
        return self.session.exec(select(Bookmark).where(Bookmark.account_id == uid)).all()

    def one(self, bookmark_id: int, who):
        row = self.session.get(Bookmark, bookmark_id)
        if row is None or row.account_id != who.id:
            return None
        return row
"""

VIA_REPO = """
from app.repo import BookmarkRepo


def get_repo(session: SessionDep) -> BookmarkRepo:
    return BookmarkRepo(session)


@router.get("/bookmarks", response_model=list[BookmarkOut])
def list_bookmarks(caller: Caller, repo: BookmarkRepo = Depends(get_repo)):
    return repo.for_account(caller.id)


@router.get("/bookmarks/{bookmark_id}", response_model=BookmarkOut)
def read_bookmark(bookmark_id: int, caller: Caller, repo: BookmarkRepo = Depends(get_repo)):
    row = repo.one(bookmark_id, caller)
    if row is None:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    return row
"""

# an authenticated route returning every account's rows: a shared table
LIST_ALL = """
@router.get("/everyone", response_model=list[BookmarkOut])
def everyone(session: SessionDep, caller: Caller):
    return session.exec(select(Bookmark)).all()
"""

# an unauthenticated public listing and an admin listing: no contradiction
PUBLIC_AND_ADMIN = """
@router.get("/public", response_model=list[BookmarkOut])
def public_bookmarks(session: SessionDep):
    return session.exec(select(Bookmark).where(Bookmark.is_public)).all()


@router.get("/admin/bookmarks", response_model=list[BookmarkOut])
def all_bookmarks(session: SessionDep, admin: Account = Depends(get_admin)):
    return session.exec(select(Bookmark)).all()
"""

# a child table checked through its owned parent
LABELS = """
@router.get("/bookmarks/{bookmark_id}/labels", response_model=list[LabelOut])
def list_labels(session: SessionDep, caller: Caller, bookmark_id: int):
    store.fetch(session, bookmark_id, caller.id)
    return session.exec(select(Label).where(Label.bookmark_id == bookmark_id)).all()


@router.post("/bookmarks/{bookmark_id}/labels", response_model=LabelOut)
def add_label(session: SessionDep, caller: Caller, bookmark_id: int, name: str):
    parent = store.fetch(session, bookmark_id, caller.id)
    row = Label(name=name, bookmark_id=parent.id)
    session.add(row)
    session.commit()
    return row
"""

# a child table read by an authenticated route without any check
LABELS_OPEN = """
@router.get("/labels", response_model=list[LabelOut])
def every_label(session: SessionDep, caller: Caller):
    return session.exec(select(Label)).all()
"""


def generate(
    tmp_path: Path, *routes: str, extra_fk: str = "", files: dict[str, str] | None = None
) -> ProfileResult:
    pkg = tmp_path / "app"
    pkg.mkdir(parents=True, exist_ok=True)
    sources = {
        "__init__": "",
        "models": MODELS.replace("EXTRA_FK", extra_fk),
        "deps": DEPS,
        "main": MAIN,
        "store": STORE,
        "repo": REPO,
        "routes": HEAD + "".join(routes),
        **(files or {}),
    }
    for name, src in sources.items():
        (pkg / f"{name}.py").write_text(textwrap.dedent(src))
    return generate_profile(extract(tmp_path, "app"))


def load(result: ProfileResult) -> ModuleType:
    module = ModuleType("owner_profile")
    exec(compile(result.source, "<owner_profile>", "exec"), module.__dict__)
    return module


def tables(result: ProfileResult) -> dict:
    return load(result).profile().api.tables


def owner_notes(result: ProfileResult, table: str) -> list[str]:
    return [u.reason for u in result.unclear if u.topic == "owner" and u.subject == table]


# ---- the evidence ---------------------------------------------------------------------------


def test_a_direct_filter_and_a_create_assigning_the_caller(tmp_path: Path) -> None:
    result = generate(tmp_path, DIRECT)
    assert tables(result)["Bookmark"].owner == "account_id"
    assert result.ownership["Bookmark"] == "owner"
    assert not owner_notes(result, "Bookmark")


def test_the_filter_through_crud_helpers(tmp_path: Path) -> None:
    """The caller's id passed positionally into ``fetch`` / ``add``, the caller passed
    whole into ``check``: the helpers' comparisons and assignment are the routes'."""
    result = generate(tmp_path, VIA_HELPERS)
    model = extract(tmp_path, "app")
    uses = {r.name: {(u.table, u.column, u.how) for u in r.owner_uses} for r in model.routes}
    assert ("Bookmark", "account_id", "compare") in uses["read_bookmark"]
    assert ("Bookmark", "account_id", "assign") in uses["add_bookmark"]
    assert ("Bookmark", "account_id", "compare") in uses["drop_bookmark"]
    assert tables(result)["Bookmark"].owner == "account_id"


def test_the_filter_through_a_repository_class(tmp_path: Path) -> None:
    result = generate(tmp_path, VIA_REPO)
    model = extract(tmp_path, "app")
    uses = {r.name: {(u.table, u.column) for u in r.owner_uses} for r in model.routes}
    assert ("Bookmark", "account_id") in uses["list_bookmarks"]
    assert ("Bookmark", "account_id") in uses["read_bookmark"]
    assert tables(result)["Bookmark"].owner == "account_id"


def test_a_helper_comparing_its_own_key_is_no_owner_evidence(tmp_path: Path) -> None:
    """``Account.id == uid`` in a helper names the caller's row, not an owner column:
    it neither counts for nor against an owner."""
    store = STORE + textwrap.dedent(
        """
        def exists(session: Session, uid: int) -> bool:
            from app.models import Account
            return session.get(Account, uid) is not None and any(
                b.id == uid for b in session.exec(select(Bookmark)).all()
            )
        """
    )
    routes = DIRECT + textwrap.dedent(
        """
        @router.get("/check", response_model=Message)
        def check_me(session: SessionDep, caller: Caller):
            store.exists(session, caller.id)
            return Message(message="ok")
        """
    )
    result = generate(tmp_path, routes, files={"store": store})
    assert tables(result)["Bookmark"].owner == "account_id"


# ---- what stays unclear, what is shared -----------------------------------------------------


def test_two_user_foreign_keys_stay_unclear(tmp_path: Path) -> None:
    extra = '    editor_id: int | None = Field(default=None, foreign_key="accounts.id")'
    result = generate(tmp_path, DIRECT, extra_fk=extra)
    assert tables(result)["Bookmark"].owner is None
    notes = [u for u in result.unclear if u.topic == "owner" and u.subject == "Bookmark"]
    assert notes and set(notes[0].candidates) == {"account_id", "editor_id"}
    assert result.ownership["Bookmark"] == "unclear"


def test_a_user_fk_no_route_uses_stays_unclear(tmp_path: Path) -> None:
    routes = """
@router.get("/ping", response_model=Message)
def ping(session: SessionDep, caller: Caller):
    return Message(message="pong")
"""
    result = generate(tmp_path, routes)
    assert tables(result)["Bookmark"].owner is None
    assert result.ownership["Bookmark"] == "unclear"


def test_an_authenticated_list_of_every_row_makes_the_table_shared(tmp_path: Path) -> None:
    result = generate(tmp_path, DIRECT, LIST_ALL)
    assert tables(result)["Bookmark"].owner is None
    assert result.ownership["Bookmark"] == "shared"
    assert "everyone" in result.ownership_reasons["Bookmark"]
    assert "# Bookmark: shared" in result.source
    assert not owner_notes(result, "Bookmark")  # a decision, not an open question


def test_a_public_listing_and_an_admin_listing_do_not_contradict(tmp_path: Path) -> None:
    """Only an authenticated non-admin route returning the rows unchecked contradicts:
    an unauthenticated public listing (the verifier still compares it) and an admin
    listing do not."""
    result = generate(tmp_path, DIRECT, PUBLIC_AND_ADMIN)
    assert tables(result)["Bookmark"].owner == "account_id"


def test_a_customer_route_writing_without_a_check_is_no_contradiction(tmp_path: Path) -> None:
    """It returns no rows of the table: the owner is inferred and verification (not the
    inference) judges the route (see test_import_owner_soundness)."""
    routes = DIRECT + textwrap.dedent(
        """
        @router.delete("/bookmarks/{bookmark_id}", response_model=Message)
        def drop_any(session: SessionDep, caller: Caller, bookmark_id: int):
            session.delete(session.get(Bookmark, bookmark_id))
            session.commit()
            return Message(message="ok")
        """
    )
    result = generate(tmp_path, routes)
    assert tables(result)["Bookmark"].owner == "account_id"


# ---- owner_via ------------------------------------------------------------------------------


def test_a_child_checked_through_its_owned_parent_is_owned_via_it(tmp_path: Path) -> None:
    result = generate(tmp_path, VIA_HELPERS, LABELS)
    got = tables(result)
    assert got["Bookmark"].owner == "account_id"
    assert got["Label"].owner is None
    assert got["Label"].owner_via == ("bookmark_id", "Bookmark")
    assert result.ownership["Label"] == "owner_via"


def test_a_child_read_without_the_parent_check_is_shared(tmp_path: Path) -> None:
    result = generate(tmp_path, VIA_HELPERS, LABELS, LABELS_OPEN)
    assert tables(result)["Label"].owner_via is None
    assert result.ownership["Label"] == "shared"


def test_a_child_of_an_unowned_parent_is_not_owned_via_it(tmp_path: Path) -> None:
    result = generate(tmp_path, VIA_HELPERS, LABELS, LIST_ALL)
    got = tables(result)
    assert got["Bookmark"].owner is None
    assert got["Label"].owner_via is None
    assert "Label" not in result.ownership or result.ownership["Label"] != "owner_via"


def test_an_untyped_row_is_the_table_it_is_named_after(tmp_path: Path) -> None:
    """``label, _ = pair()`` is not typed: ``label.bookmark_id`` could be any table's
    column of that name; the variable's name settles it (Label, not Bookmark)."""
    models = MODELS.replace(
        '    bookmark_id: int = Field(foreign_key="bookmarks.id")',
        '    bookmark_id: int = Field(foreign_key="bookmarks.id")\n'
        '    account_id: int = Field(foreign_key="accounts.id")',
    )
    routes = DIRECT + textwrap.dedent(
        """
        def pair(session, label_id):
            return session.get(Label, label_id), session.get(Bookmark, label_id)


        @router.get("/labels/{label_id}", response_model=LabelOut)
        def read_label(session: SessionDep, caller: Caller, label_id: int):
            label, _ = pair(session, label_id)
            if label.account_id != caller.id:
                raise HTTPException(status_code=403, detail="Not yours")
            return label
        """
    )
    result = generate(tmp_path, routes, files={"models": models})
    model = extract(tmp_path, "app")
    route = next(r for r in model.routes if r.name == "read_label")
    assert {"Bookmark", "Label"} <= set(route.tables)
    uses = {(u.table, u.column) for u in route.owner_uses}
    assert uses == {("Label", "account_id")}
    assert tables(result)["Label"].owner == "account_id"
