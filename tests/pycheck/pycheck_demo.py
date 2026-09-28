"""The ``demo`` app's sources (see conftest.py)."""

MODELS = """\
from datetime import datetime

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(max_length=255)
    hashed_password: str
    is_active: bool = True
    is_superuser: bool = False


class UserPublic(SQLModel):
    id: int
    email: str
    is_active: bool
    is_superuser: bool


class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=100)


class ItemCreate(ItemBase):
    pass


class Item(ItemBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime | None = None
    owner_id: int = Field(foreign_key="user.id")


class ItemPublic(ItemBase):
    id: int
    owner_id: int
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int
"""

DEPS = """\
from collections.abc import Generator
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, create_engine

from demo.models import User

engine = create_engine("sqlite:///demo.db")
oauth2 = OAuth2PasswordBearer(tokenUrl="/login")


def get_db() -> Generator[Session]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_db)]
TokenDep = Annotated[str, Depends(oauth2)]


def get_current_user(session: SessionDep, token: TokenDep) -> User:
    try:
        payload = jwt.decode(token, "secret", algorithms=["HS256"])
    except jwt.InvalidTokenError:
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
        raise HTTPException(status_code=403, detail="The user doesn't have enough privileges")
    return current_user
"""

ROUTES = """\
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import func, select

from demo.deps import CurrentUser, SessionDep, get_current_active_superuser
from demo.models import Item, ItemCreate, ItemPublic, ItemsPublic, User, UserPublic

router = APIRouter(prefix="/items")


@router.get("/", response_model=ItemsPublic)
def read_items(session: SessionDep, current_user: CurrentUser, skip: int = 0,
               limit: int = 100) -> Any:
    statement = select(Item)
    if not current_user.is_superuser:
        statement = statement.where(Item.owner_id == current_user.id)
    items = session.exec(statement.offset(skip).limit(limit)).all()
    count = session.exec(select(func.count()).select_from(Item)).one()
    return ItemsPublic(data=items, count=count)


@router.get("/{id}", response_model=ItemPublic)
def read_item(session: SessionDep, current_user: CurrentUser, id: int) -> Any:
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return item


@router.post("/", response_model=ItemPublic)
def create_item(session: SessionDep, current_user: CurrentUser, item_in: ItemCreate) -> Any:
    item = Item.model_validate(item_in, update={"owner_id": current_user.id})
    session.add(item)
    session.commit()
    return item


@router.get("/users/me", response_model=UserPublic)
def read_user_me(current_user: CurrentUser) -> Any:
    return current_user


@router.delete("/users/{user_id}", dependencies=[Depends(get_current_active_superuser)])
def delete_user(session: SessionDep, user_id: int) -> Any:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    session.delete(user)
    session.commit()
    return {"ok": True}
"""

MAIN = """\
from fastapi import FastAPI

from demo import routes

app = FastAPI()
app.include_router(routes.router)
"""

FILES = {
    "__init__.py": "",
    "models.py": MODELS,
    "deps.py": DEPS,
    "routes.py": ROUTES,
    "main.py": MAIN,
}
