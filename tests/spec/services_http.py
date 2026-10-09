"""A toy runs app as a process (stdlib HTTP over a SQLite file), for the process-runtime
tests of tests/spec/test_services.py: ``$PORT``, ``$DB_PATH``. ``GET /health``, ``POST
/configs/{cid}/runs`` (queues a run; bearer ``tok-<handle>``), ``GET /runs/{rid}``."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ["DB_PATH"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # quiet
        pass

    def answer(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def caller(self, conn: sqlite3.Connection) -> bool:
        auth = self.headers.get("Authorization") or ""
        token = auth.partition(" ")[2]
        return conn.execute("SELECT 1 FROM users WHERE token = ?", (token,)).fetchone() is not None

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            return self.answer(200, {"ok": True})
        m = re.fullmatch(r"/runs/(\d+)", self.path)
        conn = sqlite3.connect(DB, timeout=10)
        try:
            if not self.caller(conn):
                return self.answer(401, {"detail": "Not authenticated"})
            row = m and conn.execute(
                "SELECT id, config_id, status FROM runs WHERE id = ?", (int(m.group(1)),)
            ).fetchone()
            if not row:
                return self.answer(404, {"detail": "Not found"})
            return self.answer(200, {"id": row[0], "config_id": row[1], "status": row[2]})
        finally:
            conn.close()

    def do_POST(self) -> None:  # noqa: N802
        m = re.fullmatch(r"/configs/(\d+)/runs", self.path)
        conn = sqlite3.connect(DB, timeout=10)
        try:
            if not self.caller(conn):
                return self.answer(401, {"detail": "Not authenticated"})
            if not m:
                return self.answer(404, {"detail": "Not found"})
            cur = conn.execute(
                "INSERT INTO runs (config_id, status) VALUES (?, 'queued')", (int(m.group(1)),)
            )
            conn.commit()
            return self.answer(201, {"id": cur.lastrowid, "config_id": int(m.group(1)),
                                     "status": "queued"})  # fmt: skip
        finally:
            conn.close()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
