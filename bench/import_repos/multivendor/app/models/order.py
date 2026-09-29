# ruff: noqa
# app/models/order.py
from sqlmodel import SQLModel, Field, Relationship
from bench.import_repos.multivendor.app._lattice_registry import TableBase  # LATTICE MOD (isolated registry)
from datetime import datetime
from pydantic import NaiveDatetime  # LATTICE MOD (sqlmodel compat)
from enum import Enum
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from bench.import_repos.multivendor.app.models.user import User
    from bench.import_repos.multivendor.app.models.order_item import OrderItem

class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

class Order(TableBase, table=True):  # LATTICE MOD (was SQLModel)
    __tablename__ = "orders"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="RESTRICT")
    status: OrderStatus = Field(default=OrderStatus.PENDING)
    total_amount: float = Field(default=0)
    created_at: NaiveDatetime = Field(default_factory=datetime.utcnow)  # LATTICE MOD (was datetime)

    user: Optional["User"] = Relationship(back_populates="orders")
    items: List["OrderItem"] = Relationship(
        back_populates="order",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )