# ruff: noqa
# app/models/product.py
from sqlmodel import SQLModel, Field, Relationship
from bench.import_repos.multivendor.app._lattice_registry import TableBase  # LATTICE MOD (isolated registry)
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from bench.import_repos.multivendor.app.models.vendor import Vendor
    from bench.import_repos.multivendor.app.models.order_item import OrderItem

class Product(TableBase, table=True):  # LATTICE MOD (was SQLModel)
    __tablename__ = "products"

    id: Optional[int] = Field(default=None, primary_key=True)
    vendor_id: int = Field(foreign_key="vendors.id", index=True, ondelete="RESTRICT")
    name: str = Field(index=True)
    description: Optional[str] = None
    category: str = Field(index=True)
    price: float
    stock_quantity: int = Field(default=0)
    version: int = Field(default=0)
    is_active: bool = Field(default=True)

    vendor: Optional["Vendor"] = Relationship(back_populates="products")
    order_items: List["OrderItem"] = Relationship(back_populates="product")