# ruff: noqa
from pydantic import BaseModel,EmailStr,ConfigDict
from typing import Optional
from datetime import datetime
from bench.import_repos.multivendor.app.models.user import Role

class UserCreate(BaseModel):
    full_name:str
    email:EmailStr
    password:str
    
class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
   
class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id:int
    full_name:str
    email:EmailStr
    role:Role
    is_active:bool
    created_at:datetime