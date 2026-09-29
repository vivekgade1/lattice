# ruff: noqa
# app/schemas/vendor.py
from pydantic import BaseModel, ConfigDict
from typing import Optional

class VendorCreate(BaseModel):
    business_name: str

class VendorUpdate(BaseModel):
    business_name: Optional[str] = None

class VendorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True) 

    id: int
    user_id: int
    business_name: str
    is_verified: bool

class VendorVerify(BaseModel):
    is_verified: bool