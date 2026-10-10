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

Studio 2 (API v1.3) adds, all fixture-driven and polled:

- ``GET /api/code/files``, ``/api/code/file?path=``, ``/api/code/links?target=``: the Code
  lens, from ``mock/code/index.json`` (annotations and links) and the Go sources beside it;
- ``POST /api/explain`` picks an ``explain-*.json`` fixture by keyword, like ask;
- ``GET /api/flow?endpoint=&caller=`` serves ``flow-*.json`` with the divergence point;
- ``POST /api/rebuild`` (409 unless every change is accepted and not already built),
  ``GET /api/rebuild/{id}`` (one round per ``POLLS_ROUND`` polls, from ``rebuild-c1.json``:
  round 1 fails with two findings, round 2 passes) and ``DELETE /api/rebuild/{id}`` (stops
  between rounds, 409 once finished). A passing rebuild marks its changes ``build.state
  "built"``, moves ``app.last_check`` to pass and ``project.git_head`` to the commit, and
  the Code lens then serves the rebuilt ``cancelRun`` with ``rebuilt_in`` markers.

Run it with ``uv run python -m lattice.studio.mock_server --port 8765``.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import hashlib
import json
import mimetypes
import re
import threading
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "web"
FIXTURE_DIR = HERE / "mock"

# How many polls of GET /api/changes/{id} a change spends in each state.
POLLS_DRAFTING = 2
POLLS_CHECKING = 2
# How many polls of GET /api/model a started check takes.
POLLS_CHECK = 2

# How many polls of GET /api/rebuild/{id} a rebuild waits queued, and then spends per round.
POLLS_REBUILD_QUEUED = 1
POLLS_ROUND = 2
REBUILD_FINISHED = ("passed", "gave_up", "stopped")
LIVE_CHANGE_STATUSES = ("ready", "accepted")

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


# Keyword -> explain fixture (words that must all appear, lower-cased). First match wins.
EXPLAIN_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("r44",), "explain-r44"),
    (("live run",), "explain-r44"),
    (("cancel",), "explain-cancel"),
    (("409",), "explain-r44"),
    (("why",), "explain-r44"),
)
EXPLAIN_DEFAULT = "explain-not-found"


def pick_explain_fixture(question: str) -> str:
    """The name of the explain fixture for ``question`` (without ``.json``)."""
    q = question.lower()
    for words, name in EXPLAIN_RULES:
        if all(w in q for w in words):
            return name
    return EXPLAIN_DEFAULT


def flow_key(endpoint: str) -> str:
    """A route in any of its spellings ("POST /configs/{}/runs", "POST
    /applications/1/jobs/{id}/runs") reduced to one comparable form."""
    method, _, path = endpoint.strip().partition(" ")
    path = re.sub(r"\{[^}]*\}|(?<=/)\d+(?=/|$)", "{}", path)
    path = re.sub(r"^/applications/\{\}", "", path).replace("/configs/", "/jobs/")
    return f"{method.upper()} {path}"


class MockState:
    """The mutable state behind the mock API: change sets, the check run, the git head."""

    def __init__(self, fixture_dir: Path) -> None:
        self.fixture_dir = fixture_dir
        self.lock = threading.Lock()
        self.model: dict[str, Any] = self._load("model")
        self.changes: dict[str, dict[str, Any]] = {}
        self.check_polls_left: int | None = None
        self._next_id = 1
        self.code: dict[str, Any] = json.loads(
            (fixture_dir / "code" / "index.json").read_text(encoding="utf-8")
        )
        self.rebuilds: dict[str, dict[str, Any]] = {}
        self._next_rebuild = 1
        self.built_in: str | None = None  # the change a passed rebuild built, for the Code lens
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
            out["changes"] = []
            for c in self.changes.values():
                item = {
                    "id": c["id"],
                    "request": c["request"],
                    "status": c["status"],
                    "parent": c.get("parent"),
                    "superseded_by": c.get("superseded_by"),
                }
                if c["status"] == "accepted":
                    item["build"] = dict(c.get("build") or {"state": "not_built"})
                out["changes"].append(item)
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
            self._apply_cells(c)
            return {"commit": sha, "message": f"spec: {c['request']}"}

    def _apply_cells(self, c: dict[str, Any]) -> None:
        """Called with the lock held: an accepted change rewrites the spec, so the access
        cells its fixture modifies take their new value in the model."""
        fixture = self._load(c.get("fixture", "change-c1"))
        for cell in fixture.get("view_diff", {}).get("cells", []):
            key = f"{cell['row']}|{cell['col']}"
            for g in self.model.get("views", {}).get("access_groups", []):
                if key in g.get("cells", {}):
                    g["cells"][key] = cell["new"]

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

    # ---------------------------------------------------------------- code lens

    def _code_entry(self, path: str) -> dict[str, Any] | None:
        for f in self.code["files"]:
            if f["path"] == path:
                return f
        return None

    def _source(self, entry: dict[str, Any]) -> str:
        name = entry["source"]
        if self.built_in and entry.get("rebuilt_source"):
            name = entry["rebuilt_source"]
        return (self.fixture_dir / "code" / name).read_text(encoding="utf-8")

    # GET /api/code/files
    def code_files(self) -> list[dict[str, Any]]:
        with self.lock:
            out = []
            for f in self.code["files"]:
                rules: set[str] = set()
                for a in f["annotations"]:
                    rules.update(a["rules"])
                for link in self.code["links"]:
                    if link["path"] == f["path"]:
                        rules.update(link["rules"])
                size = len(self._source(f).encode("utf-8"))
                out.append(
                    {
                        "path": f["path"],
                        "language": f["language"],
                        "size": size,
                        "rules": sorted(rules, key=lambda r: int(r[1:])),
                    }
                )
            return out

    # GET /api/code/file?path=
    def code_file(self, path: str) -> dict[str, Any] | None:
        with self.lock:
            entry = self._code_entry(path)
            if entry is None:
                return None
            rebuilt = set(entry.get("rebuilt_lines", [])) if self.built_in else set()
            annotations = []
            for a in entry["annotations"]:
                item = {k: v for k, v in a.items() if k != "expect"}
                item["rules"] = list(a["rules"])
                if self.built_in and item["coverage"] == "failing":
                    item["coverage"] = "covered"
                    item.pop("finding", None)
                if a["line"] in rebuilt:
                    item["rebuilt_in"] = self.built_in
                annotations.append(item)
            return {
                "path": path,
                "language": entry["language"],
                "lines": self._source(entry).splitlines(),
                "annotations": annotations,
            }

    # GET /api/code/links?target=
    def code_links(self, target: str) -> list[dict[str, Any]] | None:
        """Code locations for a rule / cell / scenario target, the rules served for a
        ``code:<path>:<line>`` target. ``None`` when a code target names no known line."""
        with self.lock:
            if target.startswith("code:"):
                path, _, line_s = target.removeprefix("code:").rpartition(":")
                entry = self._code_entry(path)
                if entry is None or not line_s.isdigit():
                    return None
                line = int(line_s)
                if not 1 <= line <= len(self._source(entry).splitlines()):
                    return None
                ids: list[str] = []
                for a in entry["annotations"]:
                    if a["line"] == line:
                        ids += a["rules"]
                for link in self.code["links"]:
                    if link["path"] == path and link["line_from"] <= line <= link["line_to"]:
                        ids += link["rules"]
                texts = {r["id"]: r for r in self.model["rules"]}
                seen: list[str] = []
                for r in ids:
                    if r not in seen and r in texts:
                        seen.append(r)
                return [
                    {"rule": r, "text": texts[r]["text"], "status": texts[r]["status"]}
                    for r in seen
                ]
            out = []
            for link in self.code["links"]:
                if self._link_matches(link, target):
                    out.append(
                        {k: link[k] for k in ("path", "line_from", "line_to", "symbol", "why")}
                    )
            return out

    @staticmethod
    def _link_matches(link: dict[str, Any], target: str) -> bool:
        if target.startswith("rule:"):
            return target.removeprefix("rule:") in link["rules"]
        if target.startswith("cell:"):
            col = target.split("|", 1)[-1]
            return any(
                t.startswith("cell:") and t.split("|", 1)[-1] == col for t in link["targets"]
            )
        return any(fnmatch.fnmatchcase(target, t) for t in link["targets"])

    # POST /api/explain
    def explain(self, body: dict[str, Any]) -> dict[str, Any]:
        answer = self._load(pick_explain_fixture(str(body.get("question", ""))))
        answer["verified"] = None
        return answer

    # GET /api/flow?endpoint=&caller=
    def flow(self, endpoint: str, caller: str) -> dict[str, Any] | None:
        key = flow_key(endpoint)
        fixture = None
        for name in ("flow-start-run", "flow-cancel-run"):
            f = self._load(name)
            if flow_key(f["endpoint"]) == key:
                fixture = f
                break
        if fixture is None:
            return None
        with self.lock:
            divergence = None
            last = self.model["app"].get("last_check")
            if last is not None and last["verdict"] != "pass":
                divergence = fixture["divergences"].get(caller)
                pending = fixture.get("pending_divergences", {}).get(caller)
                if divergence is None and pending is not None:
                    open_change = next(
                        (
                            c
                            for c in self.changes.values()
                            if c["status"] in LIVE_CHANGE_STATUSES
                            and (c.get("build") or {}).get("state", "not_built") != "built"
                        ),
                        None,
                    )
                    if open_change is not None:
                        divergence = {
                            k: v.replace("{change}", open_change["id"]) for k, v in pending.items()
                        }
            return {
                "endpoint": endpoint,
                "caller": caller,
                "stages": fixture["stages"],
                "divergence": divergence,
            }

    # ---------------------------------------------------------------- rebuild

    # POST /api/rebuild. Raises KeyError (unknown change), ValueError (message, a 409).
    def start_rebuild(
        self, ids: list[str], model: str | None, max_rounds: int | None
    ) -> dict[str, str]:
        fx = self._load("rebuild-c1")
        with self.lock:
            for cid in ids:
                c = self.changes.get(cid)
                if c is None:
                    raise KeyError(cid)
                if c["status"] != "accepted":
                    raise ValueError(f"change {cid} is {c['status']}, not accepted")
                state = (c.get("build") or {}).get("state", "not_built")
                if state in ("rebuilding", "built"):
                    raise ValueError(f"change {cid} is already {state}")
            rid = f"b{self._next_rebuild}"
            self._next_rebuild += 1
            self.rebuilds[rid] = {
                "id": rid,
                "changes": list(ids),
                "status": "queued",
                "model": model or fx["model"],
                "max_rounds": max_rounds or fx["max_rounds"],
                "started": datetime.now(UTC),
                "polls": 0,
                "frozen": None,
                "fixture": fx,
            }
            for cid in ids:
                self.changes[cid]["build"] = {"state": "rebuilding", "rebuild_id": rid}
            return {"id": rid}

    def _round_state(self, rec: dict[str, Any]) -> tuple[int, str]:
        """(rounds completed, status) for the record's poll count."""
        fx_rounds = rec["fixture"]["rounds"]
        limit = min(len(fx_rounds), rec["max_rounds"])
        steps = rec["polls"] - POLLS_REBUILD_QUEUED
        if steps <= 0:
            return 0, "queued"
        done = min(steps // POLLS_ROUND, limit)
        if done >= 1 and fx_rounds[done - 1]["check"]["verdict"] == "pass":
            return done, "passed"
        if done >= limit:
            return done, "gave_up"
        return done, "running"

    def _finish(self, rec: dict[str, Any], status: str) -> None:
        """Called with the lock held, once, when a rebuild reaches ``status``."""
        rec["status"] = status
        state = {"passed": "built", "gave_up": "gave_up", "stopped": "not_built"}[status]
        for cid in rec["changes"]:
            self.changes[cid]["build"] = {"state": state, "rebuild_id": rec["id"]}
        if status != "passed":
            return
        fx = rec["fixture"]
        requests = "; ".join(self.changes[c]["request"] for c in rec["changes"])
        sha = hashlib.sha1(f"rebuild:{rec['id']}:{requests}".encode()).hexdigest()
        rec["commit"] = {
            "sha": sha,
            "message": f"build: {requests}" if requests else fx["commit"]["message"],
            "files_changed": list(fx["commit"]["files_changed"]),
        }
        self.model["project"]["git_head"] = sha[:7]
        self.built_in = rec["changes"][0]
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        last = self.model["app"].get("last_check") or {}
        targets = {t: "pass" for t in last.get("targets", {})}
        self.model["app"]["last_check"] = {
            "at": now,
            "verdict": "pass",
            "counts": {"A": 0, "B": 0, "C": 0, "D": 0, "X": 0},
            "targets": targets,
            "findings": [],
        }

    def _rebuild_view(self, rec: dict[str, Any]) -> dict[str, Any]:
        fx = rec["fixture"]
        status = rec["status"]
        if status in REBUILD_FINISHED:
            done = rec["frozen"] if status == "stopped" else self._round_state(rec)[0]
        else:
            done = self._round_state(rec)[0]
        started = rec["started"]
        rounds: list[dict[str, Any]] = []
        for r in fx["rounds"][:done]:
            rounds.append(self._round_item(rec, r, started, with_check=True))
        if status in ("queued", "running") and done < min(len(fx["rounds"]), rec["max_rounds"]):
            if rec["polls"] > POLLS_REBUILD_QUEUED:
                r = fx["rounds"][done]
                rounds.append(self._round_item(rec, r, started, with_check=False))
        passed_round = done if status == "passed" else 0
        out: dict[str, Any] = {
            "id": rec["id"],
            "changes": rec["changes"],
            "status": status,
            "model": rec["model"],
            "max_rounds": rec["max_rounds"],
            "started": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rounds": rounds,
            "targets": [
                {
                    "id": t["id"],
                    "label": t["label"],
                    "done": passed_round >= t["done_in_round"] > 0,
                }
                for t in fx["targets"]
            ],
        }
        if status == "passed":
            out["commit"] = rec["commit"]
        return out

    @staticmethod
    def _round_item(
        rec: dict[str, Any], r: dict[str, Any], started: datetime, with_check: bool
    ) -> dict[str, Any]:
        at = started.timestamp() + r["offset_s"]
        return {
            "n": r["n"],
            "started": datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "agent_summary": r["agent_summary"] if with_check else "",
            "files_changed": list(r["files_changed"]) if with_check else [],
            "check": copy.deepcopy(r["check"]) if with_check else None,
        }

    # GET /api/rebuild/{id}
    def get_rebuild(self, rid: str) -> dict[str, Any] | None:
        with self.lock:
            rec = self.rebuilds.get(rid)
            if rec is None:
                return None
            if rec["status"] not in REBUILD_FINISHED:
                rec["polls"] += 1
                _, status = self._round_state(rec)
                if status in ("passed", "gave_up"):
                    self._finish(rec, status)
                else:
                    rec["status"] = status
            return self._rebuild_view(rec)

    # DELETE /api/rebuild/{id}. KeyError: unknown id; ValueError: already finished.
    def stop_rebuild(self, rid: str) -> dict[str, Any]:
        with self.lock:
            rec = self.rebuilds.get(rid)
            if rec is None:
                raise KeyError(rid)
            if rec["status"] in REBUILD_FINISHED:
                raise ValueError(rec["status"])
            rec["frozen"] = self._round_state(rec)[0]
            self._finish(rec, "stopped")
            return {"id": rid, "status": "stopped"}

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
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        state = self.server.state
        if path == "/api/model":
            return self._json(state.get_model())
        if path == "/api/code/files":
            return self._json(state.code_files())
        if path == "/api/code/file":
            want = query.get("path", "")
            if not want:
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "path is required")
            code = state.code_file(want)
            if code is None:
                return self._error(HTTPStatus.NOT_FOUND, f"no file {want}")
            return self._json(code)
        if path == "/api/code/links":
            target = query.get("target", "")
            if not target:
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "target is required")
            links = state.code_links(target)
            if links is None:
                return self._error(HTTPStatus.NOT_FOUND, f"no code at {target}")
            return self._json(links)
        if path == "/api/flow":
            endpoint = query.get("endpoint", "")
            if not endpoint:
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "endpoint is required")
            flow = state.flow(endpoint, query.get("caller") or "ann")
            if flow is None:
                return self._error(HTTPStatus.NOT_FOUND, f"no flow for {endpoint}")
            return self._json(flow)
        if path.startswith("/api/rebuild/"):
            rid = path.removeprefix("/api/rebuild/")
            rebuild = state.get_rebuild(rid)
            if rebuild is None:
                return self._error(HTTPStatus.NOT_FOUND, f"no rebuild {rid}")
            return self._json(rebuild)
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
        if path == "/api/explain":
            if not str(body.get("question", "")).strip():
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "question is empty")
            return self._json(state.explain(body))
        if path == "/api/rebuild":
            ids = body.get("changes")
            if (
                not isinstance(ids, list)
                or not ids
                or not all(isinstance(i, str) and i for i in ids)
            ):
                return self._error(
                    HTTPStatus.UNPROCESSABLE_ENTITY, "changes must be a non-empty list of ids"
                )
            rounds = body.get("max_rounds")
            if rounds is not None and (
                isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1
            ):
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "max_rounds must be >= 1")
            model = body.get("model")
            if model is not None and not isinstance(model, str):
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "model must be a string")
            try:
                return self._json(state.start_rebuild(ids, model, rounds))
            except KeyError as e:
                return self._error(HTTPStatus.NOT_FOUND, f"no change {e.args[0]}")
            except ValueError as e:
                return self._error(HTTPStatus.CONFLICT, str(e))
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
        if path.startswith("/api/rebuild/"):
            rid = path.removeprefix("/api/rebuild/")
            if not rid or "/" in rid:
                return self._error(HTTPStatus.NOT_FOUND, "no such route")
            try:
                return self._json(state.stop_rebuild(rid))
            except KeyError:
                return self._error(HTTPStatus.NOT_FOUND, f"no rebuild {rid}")
            except ValueError as e:
                return self._error(HTTPStatus.CONFLICT, f"cannot stop a rebuild that is {e}")
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
