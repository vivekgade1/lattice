"""Native implementations for the real-apps test profile (tests/api/realapps.py).

Standard library only: the codegen differential copies this file next to the generated
app as `natives.py`, and the generated app imports its functions by name.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid


def hash_password(password: str) -> str:
    return "sha256$" + hashlib.sha256(password.encode()).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    return hash_password(plain) == hashed


def create_access_token(user_id: uuid.UUID) -> str:
    return f"tok-{user_id}"


def decode_token(token: str) -> uuid.UUID | None:
    if not token.startswith("tok-"):
        return None
    try:
        return uuid.UUID(token[4:])
    except ValueError:
        return None


def shout(text: str) -> str:
    return text.upper() + "!"


def year_start(when: dt.datetime) -> dt.datetime:
    return when.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def maybe_len(text: str) -> int | None:
    return len(text) if text else None


def broken(text: str) -> str | None:
    return None  # declared non-nullable: a null result is an error (500)


NATIVES = {
    "hash_password": hash_password,
    "verify_password": verify_password,
    "create_access_token": create_access_token,
    "decode_token": decode_token,
    "shout": shout,
    "year_start": year_start,
    "maybe_len": maybe_len,
    "broken": broken,
}
