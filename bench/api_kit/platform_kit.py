"""FastAPI baseline scaffolding for the API benchmark (spec section 5.3).

This module is COPIED, standalone, into a sandbox workdir next to a
model-written FastAPI script (`bench/api_runner.py` adds the model's routes
to `app`). It must not import anything from the rest of this repository --
only the standard library and FastAPI -- because it runs inside the
untrusted-script sandbox alongside model-generated code.

It authenticates only: it locates the caller from a bearer token and gates
endpoints by role. Ownership checks (no IDOR), input validation beyond
FastAPI's own parameter types, error-code mapping, and pagination are the
script's job -- exactly as they are the Lattice program's job on the other
arm. This keeps the two arms' scaffolding capability identical.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, Header
from fastapi.responses import JSONResponse

app = FastAPI()

DB_FILENAME = "app.db"


class AuthError(Exception):
    """Raised by `current_user`/`require_role`; mapped to a JSON error body
    by the handler registered below."""

    def __init__(self, status_code: int, error: str) -> None:
        super().__init__(error)
        self.status_code = status_code
        self.error = error


@app.exception_handler(AuthError)
async def _handle_auth_error(_request: Any, exc: AuthError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.error})


def _default_on_connect(_conn: sqlite3.Connection) -> None:
    """No-op by default; the harness replaces this module attribute with a
    hook that wraps the connection's `set_trace_callback` to count queries."""


# The harness instruments this by assignment (`platform_kit._on_connect = ...`)
# before importing/running the model's script, so `get_db` must read it via
# the module attribute (not a captured default argument) on every call.
_on_connect: Callable[[sqlite3.Connection], None] = _default_on_connect


def get_db() -> sqlite3.Connection:
    """A fresh connection to `app.db` in the current working directory."""
    conn = sqlite3.connect(DB_FILENAME)
    conn.row_factory = sqlite3.Row
    _on_connect(conn)
    return conn


def current_user(
    authorization: str | None = Header(None),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008 -- idiomatic FastAPI DI
) -> dict[str, Any]:
    """Resolve the caller from `Authorization: Bearer <token>`.

    401 `{"error": "unauthorized"}` if the header is missing, malformed, or
    the token does not match any user.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError(401, "unauthorized")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise AuthError(401, "unauthorized")
    row = db.execute("SELECT id, role, email, name FROM users WHERE token = ?", (token,)).fetchone()
    if row is None:
        raise AuthError(401, "unauthorized")
    return dict(row)


def require_role(*roles: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Dependency factory: 403 `{"error": "forbidden"}` unless the caller's
    role is one of `roles` or the caller is an Admin (Admin may call every
    endpoint)."""
    allowed = set(roles)

    def _dependency(
        user: dict[str, Any] = Depends(current_user),  # noqa: B008 -- idiomatic FastAPI DI
    ) -> dict[str, Any]:
        if user["role"] not in allowed and user["role"] != "Admin":
            raise AuthError(403, "forbidden")
        return user

    return _dependency
