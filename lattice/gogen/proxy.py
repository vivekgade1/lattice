"""A built Go app as a process and as an ASGI app (plan 5a Task 13).

`GoServer(binary, db)` runs a binary made by `lattice.gogen.build.build_go_app` on a
database: it starts `app --db <db> --addr 127.0.0.1:0`, waits for the `LISTENING
<host:port>` line, and terminates the process on exit (killed after 5 s). With
`trace_sql` (the default) the process runs with LATTICE_TRACE_SQL=1, so every response
carries `X-Lattice-Sql-Count`, the number of data statements the request ran. With
`sql_log` (a path) the process writes each of those statements, as SQLite expands it
with its bound values, to that file (LATTICE_TRACE_SQL_LOG; one JSON string per line).
The process's stderr is the caller's.

`go_app_factory(binary)` is an app factory in the shape the harnesses use
(`factory(db_path) -> ASGI app`, `lattice.importer.verify.AppFactory`): the ASGI app
starts a `GoServer` for that database (at lifespan startup, or at the first request)
and forwards each HTTP request to it unchanged -- method, raw path, query string,
headers (the Host header too, so a 307's Location names the client's host as
Starlette's does) and body -- and answers with the server's status, headers and body
unchanged. Redirects are not followed by the proxy: the client sees the 307. The server
stops at lifespan shutdown, or on `close()`.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

__all__ = ["GoProxyApp", "GoServer", "go_app_factory"]

_START_TIMEOUT_S = 30.0
_STOP_TIMEOUT_S = 5.0
SQL_COUNT_HEADER = "x-lattice-sql-count"


class GoServer:
    """A Go app process serving `db` on a free local port (a context manager)."""

    def __init__(
        self, binary: Path, db: Path, trace_sql: bool = True, sql_log: Path | None = None
    ) -> None:
        self.binary, self.db, self.trace_sql = Path(binary), Path(db), trace_sql
        self.sql_log = sql_log
        self.proc: subprocess.Popen[str] | None = None
        self.base_url = ""

    def start(self) -> GoServer:
        if self.proc is not None:
            return self
        env = dict(os.environ)
        if self.trace_sql:
            env["LATTICE_TRACE_SQL"] = "1"
        else:
            env.pop("LATTICE_TRACE_SQL", None)
        env.pop("LATTICE_TRACE_SQL_LOG", None)
        if self.sql_log is not None:
            env["LATTICE_TRACE_SQL_LOG"] = str(self.sql_log)
        self.proc = subprocess.Popen(  # noqa: S603
            [str(self.binary), "--db", str(self.db), "--addr", "127.0.0.1:0"],
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline().strip()
        if not line.startswith("LISTENING "):
            self.stop()
            raise RuntimeError(f"{self.binary} did not start: {line!r}")
        self.base_url = "http://" + line.split()[1]
        return self

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(_STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()

    def __enter__(self) -> GoServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


class GoProxyApp:
    """ASGI app forwarding every HTTP request to a `GoServer` on `db`.

    `sql_counter`, when given, is a one-element list to which each response's
    X-Lattice-Sql-Count is added (so a client that follows a redirect counts the
    statements of both requests, as the Python harness's trace callback does)."""

    def __init__(
        self,
        binary: Path,
        db: Path,
        *,
        trace_sql: bool = True,
        sql_counter: list[int] | None = None,
        timeout_s: float = 300.0,
    ) -> None:
        self.server = GoServer(binary, db, trace_sql)
        self.sql_counter = sql_counter
        self.timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    async def _ensure(self) -> httpx.AsyncClient:
        self.server.start()
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.server.base_url, timeout=self.timeout_s, follow_redirects=False
            )
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()
        self.server.stop()

    def close(self) -> None:
        """Stop the server (the async client, if any, is dropped with its event loop)."""
        self._client = None
        self.server.stop()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            raise RuntimeError(f"the Go proxy serves HTTP only, not {scope['type']!r}")
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        client = await self._ensure()
        target = scope.get("raw_path") or scope["path"].encode()
        query = scope.get("query_string", b"")
        url = target.decode("latin-1") + ("?" + query.decode("latin-1") if query else "")
        headers = [  # the body's framing is httpx's to set (it sends `body` as is)
            (k.decode("latin-1"), v.decode("latin-1"))
            for k, v in scope["headers"]
            if k.lower() not in (b"content-length", b"transfer-encoding")
        ]
        request = client.build_request(scope["method"], url, headers=headers, content=body)
        # the raw target, byte for byte (httpx would re-quote a path it is given as text)
        request.url = request.url.copy_with(raw_path=target + (b"?" + query if query else b""))
        response = await client.send(request)
        content = await response.aread()
        await response.aclose()
        if self.sql_counter is not None and SQL_COUNT_HEADER in response.headers:
            self.sql_counter[0] += int(response.headers[SQL_COUNT_HEADER])
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": [(k, v) for k, v in response.headers.raw],
            }
        )
        await send({"type": "http.response.body", "body": content})

    async def _lifespan(self, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await self._ensure()
                except Exception as exc:  # reported to the server, as Starlette does
                    await send({"type": "lifespan.startup.failed", "message": str(exc)})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return


def go_app_factory(
    binary: Path, *, sql_counter: list[int] | None = None
) -> Callable[[Path], GoProxyApp]:
    """`factory(db_path) -> ASGI app` serving `db_path` with the Go binary."""

    def factory(db_path: Path) -> GoProxyApp:
        return GoProxyApp(binary, Path(db_path), sql_counter=sql_counter)

    return factory
