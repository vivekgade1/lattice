"""The WSGI client behind :class:`lattice.importer.transport.WsgiTransport` (Flask apps):
``httpx.Client`` over ``httpx.WSGITransport``, no new dependency and no WSGI-to-ASGI
adapter. Imported only when a WSGI app is served."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from typing import Any

BASE_URL = "http://testserver"


def wsgi_client(app: Any, *, raise_errors: bool = False, base_url: str | None = None) -> Any:
    """An ``httpx.Client`` calling ``app`` in-process. Without ``raise_errors`` an
    unhandled error in the app is a 500 with an empty body (as Starlette's TestClient
    answers with ``raise_server_exceptions=False``); with it, the error propagates."""
    import httpx

    target = app if raise_errors else _catching(app)
    transport = httpx.WSGITransport(app=target, raise_app_exceptions=raise_errors)
    return httpx.Client(transport=transport, base_url=base_url or BASE_URL)


def _catching(app: Any) -> Callable[[dict[str, Any], Callable[..., Any]], Iterable[bytes]]:
    def wrapped(environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        try:
            result = app(environ, start_response)
            try:
                return list(result)  # drained here, so an error while streaming is caught too
            finally:
                if hasattr(result, "close"):
                    result.close()
        except Exception:  # noqa: BLE001 -- an unhandled error in the app: a 500
            start_response("500 Internal Server Error", [("content-length", "0")], sys.exc_info())
            return [b""]

    return wrapped
