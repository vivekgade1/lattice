# ruff: noqa
# app/schemas/product.py
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional

class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = None
    category: str
    price: float = Field(gt=0)
    stock_quantity: int = Field(ge=0)

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = Field(default=None, gt=0)
    is_active: Optional[bool] = None

class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vendor_id: int
    name: str
    description: Optional[str]
    category: str
    price: float
    stock_quantity: int
    version: int
    is_active: bool