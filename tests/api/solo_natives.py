"""Native implementations for the single-role test profile (tests/api/solo.py).

Standard library only: the codegen differential copies this file next to the generated
app as `natives.py`, and the generated app imports its functions by name -- and the
settings the context natives read, from `SETTINGS` (supplied at start-up).

A native that declares settings (`NativeSig(settings=(...))`) takes the read-only
`NativeContext` (a mapping of exactly those settings) as its first argument.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

# the deployment's configuration: the settings the context natives read
SETTINGS = {"secret": "solo-secret", "greeting": "Hello"}


def _sign(secret: str, username: str) -> str:
    return hashlib.sha256(f"{secret}:{username}".encode()).hexdigest()[:12]


def mint_token(secret: str, username: str) -> str:
    """A bearer token for `username`, signed with `secret` (tests only)."""
    return f"solo.{username}.{_sign(secret, username)}"


def decode_token(ctx: Mapping[str, object], token: str) -> str | None:
    """The username a valid token names (its signature checks out), else None."""
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "solo":
        return None
    _, username, sig = parts
    return username if sig == _sign(str(ctx["secret"]), username) else None


def greet(ctx: Mapping[str, object], name: str) -> str:
    return f"{ctx['greeting']}, {name}!"


def hash_password(password: str) -> str:
    return "sha256$" + hashlib.sha256(password.encode()).hexdigest()


NATIVES = {"decode_token": decode_token, "greet": greet, "hash_password": hash_password}
