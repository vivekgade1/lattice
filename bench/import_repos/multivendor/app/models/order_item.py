# ruff: noqa
# app/models/order_item.py
from sqlmodel import SQLModel, Field, Relationship
from bench.import_repos.multivendor.app._lattice_registry import TableBase  # LATTICE MOD (isolated registry)
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bench.import_repos.multivendor.app.models.order import Order
    from bench.import_repos.multivendor.app.models.product import Product

class OrderItem(TableBase, table=True):  # LATTICE MOD (was SQLModel)
    __tablename__ = "order_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="orders.id", index=True, ondelete="CASCADE")
    product_id: int = Field(foreign_key="products.id", index=True, ondelete="RESTRICT")
    quantity: int
    price_at_purchase: float

    order: Optional["Order"] = Relationship(back_populates="items")
    product: Optional["Product"] = Relationship(back_populates="order_items")