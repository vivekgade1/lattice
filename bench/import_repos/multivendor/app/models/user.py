# ruff: noqa
# app/models/user.py
from sqlmodel import SQLModel, Field, Relationship
from bench.import_repos.multivendor.app._lattice_registry import TableBase  # LATTICE MOD (isolated registry)
from datetime import datetime
from pydantic import NaiveDatetime  # LATTICE MOD (sqlmodel compat)
from enum import Enum
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from bench.import_repos.multivendor.app.models.vendor import Vendor
    from bench.import_repos.multivendor.app.models.order import Order

class Role(str, Enum):
    BUYER = "buyer"
    VENDOR = "vendor"
    ADMIN = "admin"

class User(TableBase, table=True):  # LATTICE MOD (was SQLModel)
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    full_name: str
    email: str = Field(unique=True, index=True)
    hashed_password: str
    role: Role = Field(default=Role.BUYER)
    is_active: bool = Field(default=True)
    created_at: NaiveDatetime = Field(default_factory=datetime.utcnow)  # LATTICE MOD (was datetime)

    vendor_profile: Optional["Vendor"] = Relationship(back_populates="user")
    orders: List["Order"] = Relationship(back_populates="user")