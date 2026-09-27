# ruff: noqa
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, func, select

from bench.template_app.app.api.deps import (
    CurrentUser,
    SessionDep,
    get_current_active_superuser,
)
from bench.template_app.app.models import (
    Item,
    ItemAuditEntry,
    ItemCreate,
    ItemPublic,
    ItemsAudit,
    ItemsPublic,
    ItemUpdate,
    Message,
    User,
)

router = APIRouter(prefix="/items", tags=["items"])


@router.get("/", response_model=ItemsPublic)
def read_items(
    session: SessionDep, current_user: CurrentUser, skip: int = 0, limit: int = 100
) -> Any:
    """
    Retrieve items.
    """

    if current_user.is_superuser:
        count_statement = select(func.count()).select_from(Item)
        count = session.exec(count_statement).one()
        statement = (
            select(Item).order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
        )
        items = session.exec(statement).all()
    else:
        count_statement = (
            select(func.count())
            .select_from(Item)
            .where(Item.owner_id == current_user.id)
        )
        count = session.exec(count_statement).one()
        statement = (
            select(Item)
            .where(Item.owner_id == current_user.id)
            .order_by(col(Item.created_at).desc())
            .offset(skip)
            .limit(limit)
        )
        items = session.exec(statement).all()

    items_public = [ItemPublic.model_validate(item) for item in items]
    return ItemsPublic(data=items_public, count=count)


# Declared before /{id}: an id-shaped path parameter would otherwise take "audit" as an
# item id first and fail with a 422, never reaching this route.
@router.get(
    "/audit",
    response_model=ItemsAudit,
    dependencies=[Depends(get_current_active_superuser)],
)
def read_item_audit(
    session: SessionDep, owner_email: str | None = None, skip: int = 0, limit: int = 100
) -> Any:
    """
    Admin audit: items across every account, including deactivated ones. ``owner_email``
    matches the account's email exactly (the same case-sensitive comparison the app uses
    everywhere else an email identifies an account, not a case-insensitive search).
    """
    count_statement = select(func.count()).select_from(Item).join(User, col(Item.owner_id) == User.id)
    statement = select(Item, User).join(User, col(Item.owner_id) == User.id)
    if owner_email is not None:
        count_statement = count_statement.where(User.email == owner_email)
        statement = statement.where(User.email == owner_email)
    count = session.exec(count_statement).one()
    statement = statement.order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
    rows = session.exec(statement).all()

    data = [
        ItemAuditEntry(
            id=item.id, title=item.title, owner_email=user.email, created_at=item.created_at
        )
        for item, user in rows
    ]
    return ItemsAudit(data=data, count=count)


@router.get("/{id}", response_model=ItemPublic)
def read_item(session: SessionDep, current_user: CurrentUser, id: uuid.UUID) -> Any:
    """
    Get item by ID.
    """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return item


@router.post("/", response_model=ItemPublic)
def create_item(
    *, session: SessionDep, current_user: CurrentUser, item_in: ItemCreate
) -> Any:
    """
    Create new item.
    """
    item = Item.model_validate(item_in, update={"owner_id": current_user.id})
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.put("/{id}", response_model=ItemPublic)
def update_item(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    id: uuid.UUID,
    item_in: ItemUpdate,
) -> Any:
    """
    Update an item.
    """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    update_dict = item_in.model_dump(exclude_unset=True)
    item.sqlmodel_update(update_dict)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.delete("/{id}")
def delete_item(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Message:
    """
    Delete an item.
    """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    session.delete(item)
    session.commit()
    return Message(message="Item deleted successfully")
