"""The visible smoke run of the edit slice (plan 2026-09-27 thin slice, node E3).

What a developer sees locally before shipping, without the hidden acceptance suite: the
app starts (or its traceback), and the ticket's happy-path requests -- taken from the
ticket text alone, never from the oracle -- answer as the ticket says (or their status,
body and, for a server error, the traceback). Both arms get exactly this as repair
feedback, besides arm L's checker diagnostics.

The smoke database is built the way a developer's is: the candidate app creates its
tables on an empty file (so new columns exist), then the template's seed rows (users
and items, :mod:`bench.template_app.seed`) are copied into the columns both have.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from bench.template_app.seed import TEMPLATE_USERS, build_template_db

API = "/api/v1"
AppFactory = Callable[[Path], Any]
TRACEBACK_LINES = 30
BODY_CHARS = 400
CREATED = "{created}"  # a path placeholder: the id of the item the scenario created


@dataclass(frozen=True)
class SmokeRequest:
    """One request as ``user`` (a seed handle, or None). ``expect``: the status the
    ticket implies; ``save``: remember the response's ``id`` as ``{created}``."""

    user: str | None
    method: str
    path: str
    expect: int = 200
    params: dict[str, Any] | None = None
    json: Any = None
    save: bool = False

    def label(self) -> str:
        query = "&".join(f"{k}={v}" for k, v in (self.params or {}).items())
        who = f" as {self.user}" if self.user else ""
        body = f" {self.json}" if self.json is not None else ""
        return f"{self.method} {self.path}{'?' + query if query else ''}{body}{who}"


_ITEMS = f"{API}/items/"
_ITEM = f"{API}/items/{CREATED}"

# The happy paths each ticket's text describes (the requests a developer would try).
SMOKE: dict[str, tuple[SmokeRequest, ...]] = {
    "t1_search": (SmokeRequest("alice", "GET", _ITEMS, params={"q": "groc"}),),
    "t2_since": (
        SmokeRequest("alice", "GET", _ITEMS, params={"since": "2026-02-01T12:00:00Z"}),
        SmokeRequest("alice", "GET", _ITEMS, params={"sort": "oldest"}),
    ),
    "t3_soft_delete": (
        SmokeRequest("alice", "POST", _ITEMS, json={"title": "Smoke test"}, save=True),
        SmokeRequest("alice", "DELETE", _ITEM),
        SmokeRequest("alice", "GET", _ITEMS),
    ),
    "t4_restore": (
        SmokeRequest("admin", "POST", _ITEMS, json={"title": "Smoke test"}, save=True),
        SmokeRequest("admin", "DELETE", _ITEM),
        SmokeRequest("admin", "POST", f"{_ITEM}/restore"),
    ),
    "t5_title_window": (
        SmokeRequest("alice", "POST", _ITEMS, json={"title": "Smoke test"}, save=True),
        SmokeRequest("alice", "PUT", _ITEM, json={"title": "Smoke test, renamed"}),
    ),
    "t6_stats": (SmokeRequest("alice", "GET", f"{API}/items/stats"),),
}


@dataclass
class SmokeResult:
    """``started``: the app was built; ``lines``: one line per request (or the start
    failure); ``failed``: some request crashed or answered another status."""

    started: bool
    lines: list[str] = field(default_factory=list)
    failed: bool = False

    def render(self) -> str:
        return "\n".join(self.lines)


def _tail(text: str, n: int = TRACEBACK_LINES) -> str:
    lines = text.rstrip("\n").splitlines()
    return "\n".join((["..."] if len(lines) > n else []) + lines[-n:])


def format_exception(exc: BaseException) -> str:
    return _tail("".join(traceback.format_exception(exc)))


@cache
def _seed_db() -> Path:
    root = Path(tempfile.mkdtemp(prefix="edit-slice-smoke-seed-"))
    return build_template_db(root / "seed.db")


def _copy_seed(db_path: Path) -> None:
    """Copy the seed's users and items into ``db_path``'s tables (shared columns)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ATTACH DATABASE ? AS seed", (str(_seed_db()),))
        for table in ("user", "item"):
            mine = [r[1] for r in conn.execute(f'PRAGMA main.table_info("{table}")')]
            theirs = {r[1] for r in conn.execute(f'PRAGMA seed.table_info("{table}")')}
            cols = ", ".join(f'"{c}"' for c in mine if c in theirs)
            conn.execute(f'INSERT INTO main."{table}" ({cols}) SELECT {cols} FROM seed."{table}"')
        conn.commit()
    finally:
        conn.close()


def build_app(factory: AppFactory, root: Path) -> Any:
    """The candidate app on a fresh seeded smoke database under ``root`` (raises what
    the candidate raises)."""
    db = root / "smoke.db"
    if db.exists():
        db.unlink()
    app = factory(db)
    _copy_seed(db)
    return app


def _login(client: Any, handle: str) -> str:
    email, password, _ = TEMPLATE_USERS[handle]
    r = client.post(f"{API}/login/access-token", data={"username": email, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"login as {handle} answered {r.status_code}: {r.text[:BODY_CHARS]}")
    return r.json()["access_token"]


def run_smoke(ticket_id: str, factory: AppFactory) -> SmokeResult:
    """Build the app on a smoke database and issue the ticket's happy-path requests."""
    from fastapi.testclient import TestClient

    root = Path(tempfile.mkdtemp(prefix="edit-slice-smoke-"))
    try:
        try:
            app = build_app(factory, root)
        except Exception as exc:  # noqa: BLE001 -- the developer sees the traceback
            return SmokeResult(False, ["The app failed to start:", format_exception(exc)], True)
        result = SmokeResult(True)
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                _requests(ticket_id, client, result)
        except Exception as exc:  # noqa: BLE001 -- e.g. the lifespan failed
            result.failed = True
            result.lines += ["The app failed while serving:", format_exception(exc)]
        finally:
            engine = getattr(getattr(app, "state", None), "engine", None)
            if engine is not None:
                engine.dispose()
        return result
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _requests(ticket_id: str, client: Any, result: SmokeResult) -> None:
    tokens: dict[str, str] = {}
    created: str | None = None
    for req in SMOKE[ticket_id]:
        headers = {}
        if req.user is not None:
            if req.user not in tokens:
                try:
                    tokens[req.user] = _login(client, req.user)
                except Exception as exc:  # noqa: BLE001
                    result.failed = True
                    result.lines += [f"login as {req.user} failed:", format_exception(exc)]
                    return
            headers["Authorization"] = f"Bearer {tokens[req.user]}"
        if CREATED in req.path and created is None:
            result.failed = True
            result.lines.append(f"{req.label()} -> skipped (no item was created)")
            continue
        path = req.path.replace(CREATED, created or "")
        try:
            r = client.request(req.method, path, params=req.params, json=req.json, headers=headers)
        except Exception as exc:  # noqa: BLE001 -- a server error, raised by TestClient
            result.failed = True
            result.lines += [f"{req.label()} -> 500 (server error)", format_exception(exc)]
            continue
        line = f"{req.label()} -> {r.status_code}"
        if r.status_code != req.expect:
            result.failed = True
            line += f" (expected {req.expect}): {r.text[:BODY_CHARS]}"
        result.lines.append(line)
        if req.save and r.status_code < 300:
            try:
                created = str(r.json()["id"])
            except Exception:  # noqa: BLE001
                created = None


def smoke_feedback(result: SmokeResult) -> str:
    """The repair message part for a failed smoke run."""
    head = (
        "Running the app locally with the ticket's basic requests"
        if result.started
        else "Starting the app locally"
    )
    return f"{head}:\n{result.render()}"


__all__ = ["SMOKE", "SmokeRequest", "SmokeResult", "build_app", "run_smoke", "smoke_feedback"]
