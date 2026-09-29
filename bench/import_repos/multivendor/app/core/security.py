# ruff: noqa
# app/core/security.py
import bcrypt as _bcrypt  # LATTICE MOD (was passlib; see PROVENANCE.md)
from datetime import datetime, timedelta
from jose import jwt, JWTError
from bench.import_repos.multivendor.app.core.config import settings


def hash_password(plain_password: str) -> str:
    return _bcrypt.hashpw(plain_password.encode(), _bcrypt.gensalt()).decode()  # LATTICE MOD

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _bcrypt.checkpw(plain_password.encode(), hashed_password.encode())  # LATTICE MOD


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)

def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return payload
    except JWTError:
        raise ValueError("Invalid or expired token")