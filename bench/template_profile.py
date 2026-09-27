"""The Lattice API profile of the vendored FastAPI template (spec 2026-09-27 realapps §4).

``template_profile()`` describes the template's ``user`` and ``item`` tables exactly as
the template's SQLModel metadata creates them on SQLite (``bench/template_app``): Uuid
keys stored as 32 hex digits (SQLAlchemy's ``CHAR(32)``), ``created_at`` stored as naive
UTC ``"YYYY-MM-DD HH:MM:SS.ffffff"`` text, ``Item.owner_id`` the owner column,
``User.hashed_password`` private and ``User.is_superuser`` the Admin flag. Its page
shape, response models and natives mirror the template's ``ItemsPublic``/``UsersPublic``
envelopes, ``ItemPublic``/``UserPublic``/``Token``/``Message`` models and
``core/security.py``.

The natives below wrap the template's own ``core/security.py`` (Argon2 via pwdlib, JWT
via PyJWT with the template's ``SECRET_KEY`` and ``ALGORITHM``); nothing about the
crypto is reimplemented here. ``decode_token`` mirrors ``api/deps.get_current_user``'s
decode step (``jwt.decode`` with the template's settings, then the ``sub`` claim).

The hand-written Lattice programs are ``tests/fixtures/template/*.lattice.py`` (login,
items, users); ``template_program()`` joins them into the one program that is served
(``build_app(template_plans(), db, natives=NATIVES)``) and compiled to FastAPI
(``compile_source_to_fastapi(template_program(), template_profile(),
natives_module="bench.template_profile")``).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import jwt
from jwt.exceptions import InvalidTokenError

from bench.template_app.app.core import security
from bench.template_app.app.core.config import settings
from lattice.api.ir import EndpointPlan, lower_api
from lattice.check import check_source
from lattice.profile import AdminFlag, ApiProfile, NativeSig, NativeToken, Profile
from lattice.typesys import Scalar, Schema

S = Scalar
PROGRAM_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "template"
PROGRAM_FILES = ("login.lattice.py", "items.lattice.py", "users.lattice.py")

# ---- natives: the template's core/security.py ------------------------------------------


def hash_password(password: str) -> str:
    return security.get_password_hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    verified, _updated_hash = security.verify_password(plain, hashed)
    return verified


def create_access_token(user_id: uuid.UUID) -> str:
    expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return security.create_access_token(user_id, expires_delta=expires)


def decode_token(token: str) -> uuid.UUID | None:
    """The user id a valid access token names (``sub``), or None for a bad token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[security.ALGORITHM])
    except InvalidTokenError:
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str):
        return None
    try:
        return uuid.UUID(sub)
    except ValueError:
        return None


NATIVES = {
    "hash_password": hash_password,
    "verify_password": verify_password,
    "create_access_token": create_access_token,
    "decode_token": decode_token,
}

# ---- the profile ---------------------------------------------------------------------------

USER = Schema.from_pairs(
    [
        ("email", S.STR),
        ("is_active", S.BOOL),
        ("is_superuser", S.BOOL),
        ("full_name", S.STR),
        ("id", S.UUID),
        ("hashed_password", S.STR),
        ("created_at", S.DATETIME),
    ],
    keys=[("id",)],
    nullable=["full_name", "created_at"],
    private=["hashed_password"],
    # the template's non-admin writes (UserUpdateMe) never touch these
    admin_write=["is_superuser", "is_active"],
)
ITEM = Schema.from_pairs(
    [
        ("title", S.STR),
        ("description", S.STR),
        ("id", S.UUID),
        ("created_at", S.DATETIME),
        ("owner_id", S.UUID),
    ],
    keys=[("id",)],
    nullable=["description", "created_at"],
    owner="owner_id",
)

# The template's response models (bench/template_app/app/models.py), fields in order.
RESPONSES: dict[str, tuple[str, ...]] = {
    "ItemPublic": ("title", "description", "id", "owner_id", "created_at"),
    "UserPublic": ("email", "is_active", "is_superuser", "full_name", "id", "created_at"),
    "Token": ("access_token", "token_type"),
    "Message": ("message",),
}


def template_profile() -> Profile:
    """The Lattice profile for the template's ``user``/``item`` tables."""
    api = ApiProfile(
        tables={"User": USER, "Item": ITEM},
        table_names={"User": "user", "Item": "item"},
        roles=frozenset({"Public", "Customer", "Admin"}),
        users_table="User",
        # ItemsPublic / UsersPublic: {"data": [...], "count": n}
        page_shape={"items": "data", "total": "count", "limit": "", "offset": ""},
        responses=RESPONSES,
        natives={
            "hash_password": NativeSig((S.STR,), S.STR),
            "verify_password": NativeSig((S.STR, S.STR), S.BOOL, reads_private=True),
            "create_access_token": NativeSig((S.UUID,), S.STR),
            "decode_token": NativeSig((S.STR,), S.UUID, nullable=True),
        },
        auth=NativeToken("decode_token"),
        roles_from=AdminFlag("is_superuser", "Customer"),
        # SQLAlchemy's SQLite spellings (CHAR(32) ids, naive "YYYY-MM-DD HH:MM:SS.ffffff")
        uuid_storage="hex32",
        datetime_storage="sql_space",
    )
    return Profile(schemas={}, api=api)


def template_sources() -> dict[str, str]:
    """File name -> the hand-written Lattice source, in ``PROGRAM_FILES`` order."""
    return {name: (PROGRAM_DIR / name).read_text() for name in PROGRAM_FILES}


def template_program() -> str:
    """The three sources as one program (each file's ``# lattice: v1`` header once)."""
    header = "# lattice: v1\n"
    parts = []
    for name, src in template_sources().items():
        if not src.startswith(header):
            raise ValueError(f"{name} must start with {header!r}")
        parts.append(src.removeprefix(header).strip("\n"))
    return header + "\n\n\n".join(parts) + "\n"


def template_plans() -> tuple[EndpointPlan, ...]:
    """The checked and lowered template program; a rejected program is a ValueError."""
    result = check_source(template_program(), template_profile())
    if result.program is None:
        errors = "\n".join(d.render() for d in result.diagnostics)
        raise ValueError(f"the template program is rejected:\n{errors}")
    return lower_api(result.program)
