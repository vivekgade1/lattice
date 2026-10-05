"""Lattice-owned stand-in for ``itsdangerous.TimedJSONWebSignatureSerializer`` (not upstream).

itsdangerous 2.1 removed ``TimedJSONWebSignatureSerializer``, which flasky's
``app/models.py`` uses for its API auth tokens (and its confirmation / reset / email-change
tokens). This class keeps the interface flasky relies on:

- ``Serializer(secret_key, expires_in=3600)`` (3600 was itsdangerous' default);
- ``dumps(obj) -> bytes`` (flasky calls ``.decode('utf-8')`` on it);
- ``loads(token)`` accepts ``str`` or ``bytes``, and raises (``BadSignature`` /
  ``SignatureExpired``) on a bad or expired token; flasky catches every exception.

The expiry travels inside the signed payload, so a serializer built without
``expires_in`` (as flasky's ``loads`` callers are) still enforces it, as the JWS header
did. Tokens are not byte-compatible with the old JWS format; nothing stores them.
"""

from __future__ import annotations

import time
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeSerializer


class TimedJSONWebSignatureSerializer:
    DEFAULT_EXPIRES_IN = 3600

    def __init__(self, secret_key: str, expires_in: int | None = None, **kwargs: Any) -> None:
        self._serializer = URLSafeSerializer(secret_key, salt="flasky-timed-jws")
        self.expires_in = self.DEFAULT_EXPIRES_IN if expires_in is None else expires_in

    def dumps(self, obj: Any) -> bytes:
        payload = {"exp": int(time.time()) + int(self.expires_in), "data": obj}
        return self._serializer.dumps(payload).encode("utf-8")

    def loads(self, token: str | bytes) -> Any:
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        payload = self._serializer.loads(token)
        if not isinstance(payload, dict) or "exp" not in payload:
            raise BadSignature("malformed token")
        if payload["exp"] < time.time():
            raise SignatureExpired("token expired")
        return payload["data"]
