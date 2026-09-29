# ruff: noqa
# app/schemas/order.py
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from typing import List, Optional
from bench.import_repos.multivendor.app.models.order import OrderStatus

class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)

class OrderCreate(BaseModel):
    items: List[OrderItemCreate] = Field(min_length=1)

class OrderItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    quantity: int
    price_at_purchase: float

class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    status: OrderStatus
    total_amount: float
    created_at: datetime
    items: List[OrderItemRead] = []

class OrderStatusUpdate(BaseModel):
    status: OrderStatus