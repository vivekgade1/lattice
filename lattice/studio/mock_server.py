"""A tiny stdlib server for building the Studio web UI without the real server.

It serves ``lattice/studio/web/`` at ``/`` and answers the routes of ``API.md`` from the
fixtures in ``lattice/studio/mock/``. Nothing here calls an LLM or touches git:

- the model starts with one pre-existing ``ready`` change (``c0``), so the UI has
  something to show on load without the owner doing anything;
- ``POST /api/ask`` picks an ``ask-*.json`` fixture by keyword in the question;
- ``POST /api/changes`` hands out ids ``c1``, ``c2``, ...; each one reports ``drafting``,
  then ``checking``, then ``ready`` (the ``change-c1.json`` fixture, or ``change-followup
  .json`` when the request carries a ``parent``) over a few polls;
- a follow-up (``"parent": "c1"``) supersedes its parent the moment it turns ``ready``:
  the parent is marked ``rejected`` with ``superseded_by`` pointing at the follow-up;
- ``DELETE /api/changes/{id}`` cancels a change that is still ``drafting`` or
  ``checking`` (409 otherwise);
- ``accept`` returns a fake commit sha and moves the model's ``git_head`` to it;
- ``POST /api/check`` marks a check as running; ``app.last_check.at`` moves forward after
  a couple of ``GET /api/model`` polls.

Run it with ``uv run python -m lattice.studio.mock_server --port 8765``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import mimetypes
import threading
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "web"
FIXTURE_DIR = HERE / "mock"

# How many polls of GET /api/changes/{id} a change spends in each state.
POLLS_DRAFTING = 2
POLLS_CHECKING = 2
# How many polls of GET /api/model a started check takes.
POLLS_CHECK = 2

# Keyword -> ask fixture. The first rule whose words all appear in the question wins.
ASK_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("inactive",), "ask-verified-disagrees"),
    (("login",), "ask-verified-disagrees"),
    (("concurrently",), "ask-inconclusive"),
    (("inconclusive",), "ask-inconclusive"),
    (("cancel",), "ask-yes"),
    (("eve",), "ask-yes"),
    (("retry",), "ask-not-specified"),
    (("retried",), "ask-not-specified"),
)
ASK_DEFAULT = "ask-not-specified"


def pick_ask_fixture(question: str) -> str:
    """The name of the ask fixture for ``question`` (without ``.json``)."""
    q = question.lower()
    for words, name in ASK_RULES:
        if all(w in q for w in words):
            return name
    return ASK_DEFAULT


class MockState:
    """The mutable state behind the mock API: change sets, the check run, the git head."""

    def __init__(self, fixture_dir: Path) -> None:
        self.fixture_dir = fixture_dir
        self.lock = threading.Lock()
        self.model: dict[str, Any] = self._load("model")
        self.changes: dict[str, dict[str, Any]] = {}
        self.check_polls_left: int | None = None
        self._next_id = 1
        self._seed_initial_change()

    def _load(self, name: str) -> Any:
        return json.loads((self.fixture_dir / f"{name}.json").read_text(encoding="utf-8"))

    def _seed_initial_change(self) -> None:
        """One change already sits in the model when the server starts, so a fresh page
        load has something to show without the owner doing anything first."""
        self.changes["c0"] = {
            "id": "c0",
            "request": "only app admins may cancel runs",
            "status": "ready",
            "polls": 0,
            "parent": None,
            "superseded_by": None,
            "error": None,
            "fixture": "change-c1",
        }

    # GET /api/model
    def get_model(self) -> dict[str, Any]:
        with self.lock:
            if self.check_polls_left is not None:
                self.check_polls_left -= 1
                if self.check_polls_left <= 0:
                    self.check_polls_left = None
                    last = self.model["app"].get("last_check")
                    if last is not None:
                        last["at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            out = copy.deepcopy(self.model)
            out["changes"] = [
                {
                    "id": c["id"],
                    "request": c["request"],
                    "status": c["status"],
                    "parent": c.get("parent"),
                    "superseded_by": c.get("superseded_by"),
                }
                for c in self.changes.values()
            ]
            return out

    # POST /api/ask
    def ask(self, body: dict[str, Any]) -> dict[str, Any]:
        answer = self._load(pick_ask_fixture(str(body.get("question", ""))))
        if not body.get("verify"):
            answer["verified"] = None
        return answer

    # POST /api/changes
    def new_change(self, body: dict[str, Any]) -> dict[str, str]:
        with self.lock:
            cid = f"c{self._next_id}"
            self._next_id += 1
            parent = body.get("parent")
            parent = str(parent) if parent else None
            self.changes[cid] = {
                "id": cid,
                "request": str(body.get("request", "")),
                "status": "drafting",
                "polls": 0,
                "parent": parent,
                "superseded_by": None,
                "error": None,
                # A follow-up is drafted "on top of the parent's proposal" (v1.1 #3); the
                # mock just answers with the fixture that shows the conflict resolved.
                "fixture": "change-followup" if parent else "change-c1",
            }
            return {"id": cid}

    def _supersede_parent(self, c: dict[str, Any]) -> None:
        """Called with the lock held, right after `c` turns ready: if it is a follow-up,
        its parent is reported as rejected, pointing at it."""
        parent_id = c.get("parent")
        if not parent_id:
            return
        p = self.changes.get(parent_id)
        if p is not None and p["status"] not in ("accepted", "rejected", "cancelled"):
            p["status"] = "rejected"
            p["superseded_by"] = c["id"]

    # GET /api/changes/{id}
    def get_change(self, cid: str) -> dict[str, Any] | None:
        with self.lock:
            c = self.changes.get(cid)
            if c is None:
                return None
            if c["status"] in ("drafting", "checking"):
                c["polls"] += 1
                if c["polls"] > POLLS_DRAFTING + POLLS_CHECKING:
                    c["status"] = "ready"
                    self._supersede_parent(c)
                elif c["polls"] > POLLS_DRAFTING:
                    c["status"] = "checking"
            if c["status"] in (
                "drafting",
                "checking",
                "cancelled",
                "failed",
                "accepted",
                "rejected",
            ):
                return {
                    "id": cid,
                    "request": c["request"],
                    "status": c["status"],
                    "error": c.get("error"),
                    "parent": c.get("parent"),
                    "superseded_by": c.get("superseded_by"),
                    "notes": [],
                    "timings": {},
                    "markdown_diff": "",
                    "rules": {"added": [], "changed": [], "removed": []},
                    "targets": {"added": [], "removed": [], "modified": []},
                    "view_diff": {
                        "cells": [],
                        "invariants": {"added": [], "removed": []},
                        "machines": {"changed": []},
                        "scenarios": {"added": [], "removed": [], "modified": []},
                    },
                    "checks": {
                        "valid": True,
                        "problems": [],
                        "lint": [],
                        "conflicts": [],
                        "impact": None,
                    },
                    "questions": [],
                }
            full = self._load(c.get("fixture", "change-c1"))
            full["id"] = cid
            full["request"] = c["request"] or full["request"]
            full["status"] = c["status"]
            full["parent"] = c.get("parent")
            full["superseded_by"] = c.get("superseded_by")
            full.setdefault("notes", [])
            full.setdefault("timings", {})
            return full

    # POST /api/changes/{id}/accept | reject
    def decide(self, cid: str, verb: str) -> dict[str, Any] | None:
        with self.lock:
            c = self.changes.get(cid)
            if c is None:
                return None
            if verb == "reject":
                c["status"] = "rejected"
                return {"ok": True}
            sha = hashlib.sha1(f"{cid}:{c['request']}".encode()).hexdigest()
            c["status"] = "accepted"
            self.model["project"]["git_head"] = sha[:7]
            return {"commit": sha, "message": f"spec: {c['request']}"}

    # DELETE /api/changes/{id}
    def cancel(self, cid: str) -> dict[str, Any]:
        """The minimal ``{id, status}`` on success. Raises ``KeyError`` for an unknown
        id, ``ValueError`` (carrying the current status) when it cannot be cancelled."""
        with self.lock:
            c = self.changes.get(cid)
            if c is None:
                raise KeyError(cid)
            if c["status"] not in ("drafting", "checking"):
                raise ValueError(c["status"])
            c["status"] = "cancelled"
            return {"id": cid, "status": "cancelled"}

    # POST /api/check
    def start_check(self) -> dict[str, Any]:
        with self.lock:
            self.check_polls_left = POLLS_CHECK
            return {"started": True}


class Handler(BaseHTTPRequestHandler):
    server: MockServer  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if self.server.verbose:
            super().log_message(format, *args)

    def _json(self, obj: Any, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: HTTPStatus, detail: str) -> None:
        self._json({"detail": detail}, status)

    def _body(self) -> dict[str, Any] | None:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return None
        return body if isinstance(body, dict) else None

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        state = self.server.state
        if path == "/api/model":
            return self._json(state.get_model())
        if path.startswith("/api/changes/"):
            cid = path.removeprefix("/api/changes/")
            change = state.get_change(cid)
            if change is None:
                return self._error(HTTPStatus.NOT_FOUND, f"no change {cid}")
            return self._json(change)
        if path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "no such route")
        return self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        state = self.server.state
        body = self._body()
        if body is None:
            return self._error(HTTPStatus.BAD_REQUEST, "the body must be a JSON object")
        if path == "/api/ask":
            if not str(body.get("question", "")).strip():
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "question is empty")
            return self._json(state.ask(body))
        if path == "/api/changes":
            if not str(body.get("request", "")).strip():
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "request is empty")
            return self._json(state.new_change(body))
        if path == "/api/check":
            return self._json(state.start_check())
        if path.startswith("/api/changes/"):
            parts = path.removeprefix("/api/changes/").split("/")
            if len(parts) == 2 and parts[1] in ("accept", "reject"):
                out = state.decide(parts[0], parts[1])
                if out is None:
                    return self._error(HTTPStatus.NOT_FOUND, f"no change {parts[0]}")
                return self._json(out)
        return self._error(HTTPStatus.NOT_FOUND, "no such route")

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        state = self.server.state
        if path.startswith("/api/changes/"):
            cid = path.removeprefix("/api/changes/")
            if not cid or "/" in cid:
                return self._error(HTTPStatus.NOT_FOUND, "no such route")
            try:
                out = state.cancel(cid)
            except KeyError:
                return self._error(HTTPStatus.NOT_FOUND, f"no change {cid}")
            except ValueError as e:
                return self._error(HTTPStatus.CONFLICT, f"cannot cancel a change with status {e}")
            return self._json(out)
        return self._error(HTTPStatus.NOT_FOUND, "no such route")

    def _static(self, path: str) -> None:
        rel = unquote(path).lstrip("/") or "index.html"
        root = self.server.web_dir.resolve()
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    # The browser fetches every ES module at once; the default backlog of 5 resets some.
    request_queue_size = 128

    def __init__(
        self, addr: tuple[str, int], web_dir: Path, fixture_dir: Path, verbose: bool = False
    ) -> None:
        super().__init__(addr, Handler)
        self.web_dir = web_dir
        self.state = MockState(fixture_dir)
        self.verbose = verbose


def make_server(
    port: int = 0,
    host: str = "127.0.0.1",
    web_dir: Path = WEB_DIR,
    fixture_dir: Path = FIXTURE_DIR,
    verbose: bool = False,
) -> MockServer:
    """A mock server bound to ``host:port`` (port 0 picks a free one); not yet serving."""
    return MockServer((host, port), web_dir, fixture_dir, verbose)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--fixtures", type=Path, default=FIXTURE_DIR)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    srv = make_server(args.port, args.host, WEB_DIR, args.fixtures, verbose=not args.quiet)
    print(f"Lattice Studio (mock) on http://{args.host}:{srv.server_address[1]}/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
