# ruff: noqa
# app/models/vendor.py
from sqlmodel import SQLModel, Field, Relationship
from bench.import_repos.multivendor.app._lattice_registry import TableBase  # LATTICE MOD (isolated registry)
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from bench.import_repos.multivendor.app.models.user import User
    from bench.import_repos.multivendor.app.models.product import Product

class Vendor(TableBase, table=True):  # LATTICE MOD (was SQLModel)
    __tablename__ = "vendors"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", unique=True, ondelete="RESTRICT")
    business_name: str
    is_verified: bool = Field(default=False)

    user: Optional["User"] = Relationship(back_populates="vendor_profile")
    products: List["Product"] = Relationship(back_populates="vendor")