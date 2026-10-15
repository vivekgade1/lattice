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
  "built"`` and moves ``app.last_check`` to pass, and the Code lens then serves the rebuilt
  ``cancelRun`` with ``rebuilt_in`` markers (a preview of the branch). As on the real server
  (API v1.7), ``project.git_head`` does not move: the commit is on the record's ``branch``
  (``rebuild/<change>``) with ``base``, ``worktree`` and a ``merge_hint``; nothing merges.

API v1.4 adds ``GET /api/graph`` (the business-logic graph: nodes, edges, lifecycle
machines and the open change's diff, all derived from the same fixtures, see
``build_graph``) and ``id``/``next`` on every ``GET /api/flow`` stage.

API v1.5 adds the chat as a Claude Code session (``/api/session``): the mock replays
``mock/session-ask.jsonl`` (partial text, an ``mcp__lattice__spec_ask`` card, a result) over
four polls, and ``mock/session-error.jsonl`` (a usage-limit error) for a message that
contains "limit". ``GET /api/model`` has ``app.session: true``.

API v1.6 adds ``GET /api/projects`` and ``/p/<name>/api/...``: two projects with separate
state, ``job-scheduler-studio`` (the fixtures, the default, also at the bare ``/api/...``)
and ``lattice-studio`` (the fixtures cut to ``SECOND_PROJECT_RULES`` rules, no changes).

API v1.8 adds build from spec: ``GET /api/areas[?branch=B]`` (the areas derived from the
model's rule sections and links by ``lattice.studio.areas``, as the real server does),
``GET /api/branches`` (a simulated ``main``, checked out and protected, plus every target
branch a scoped build created) and scoped ``POST /api/rebuild {"scope": ..., "branch":
...}``: the same validation codes as the real server, a synthetic baseline (the area's
targets the branch's results fail; with none yet, every check fails), two rounds (the first
fails in scope, the second passes) and, on pass, the target branch "fast-forwarded" and its
area results updated; a second build of a built area answers 409 "nothing to build".

API v1.9 adds build plans (``MockPlans``): ``POST /api/plan``, ``GET /api/plan/{id}``,
``GET /api/plans``, ``POST /api/plan/{id}/revise|approve|run``, ``DELETE /api/plan/{id}``.
API v1.10 (durable state, resume): the mock keeps its state in memory, but every plan
carries ``resume``, ``interrupted``, ``interrupted_at``, ``attempts`` and ``timeline``, and
the second project (``lattice-studio``) starts with two fixture plans: ``p1`` was
``interrupted`` while level 2 ran (``POST .../run`` resumes it), ``p2`` passed level 1's
full gate but could not land because the target is checked out in the owner's working copy
(``resume.kind: "land"``; ``POST /api/plan/p2/land`` answers 409 ``branch_checked_out``
unless the body confirms with ``confirm_unsafe_branch: true``). ``POST .../land`` is 409
``nothing_to_land`` on any other plan; ``DELETE /api/plan/{id}?forget=true`` drops a plan.
Planning takes one poll; the planner ``agent`` gives a mock agent plan (a scaffold over the
infrastructure areas, the accounts area, every other area depending on it with its tables
and one package, huge areas split in two), ``fallback`` the real fallback; both go through
the real validator (``lattice.studio.plan.validate_plan``). A run advances one step per
poll of ``GET /api/plan/{id}``: a level's nodes start, then pass, then the level merges.

API v1.11 (build performance): ``GET /api/projects`` items carry ``plan_defaults`` (the
seed's scoped gate and regression level gate); every plan carries ``planner_usage`` and,
once it runs, ``gate_config``; level runs carry ``gate.mode``, ``gate.selected`` and
``gate.in_scope_failing``; a mock run's level 1 (when the plan has at least three levels)
first fails its gate on the merged tip and is repaired over one poll (``repairs`` on the
level run, ``repairs`` on the nodes, timeline entries with ``repair: true``) before it
lands, and the second project gets a third fixture plan ``p3`` whose level 1 ran out of
repair attempts (``status: partial``, ``error.kind: repair_exhausted``, ``resume.kind:
repair``; one of its failing checks is owned by no node, so a ``kind: "repair"`` node
``repair-l1`` took it and gave up): ``POST /api/plan/p3/run`` repairs it again over one
poll and goes on. A fourth fixture ``p4`` is partial because a node gave up and blocked its
dependents (``resume.kind: "retry"``). Repair entries carry per-node ``usage``, nodes carry
``repair_usage``, a plan's ``totals`` include the planner, a resumed run's baseline is
``reused`` and a run's last level is preceded by ``final_gate``.

API v1.12 (spec sync): every project has a fixture target branch ``build/spec-behind`` whose
committed spec is behind ``main``'s (``REQUIREMENTS.md`` and ``spec.trace.json``), so ``GET
/api/branches`` gives it ``spec: {"in_sync": false, ...}`` (every other non-protected, non-
current branch is in sync; ``main`` has ``spec: null``) and a plan onto it carries
``spec_drift`` and the drift warning. ``POST /api/branches/{name}/sync-spec`` answers the real
server's fields and refusal codes (``branch_name_invalid``, ``branch_protected`` for main,
``no_branch``, ``build_running`` while a build or plan runs, ``nothing_to_sync``) and moves the
branch to a fake commit. ``[rebuild] sync_spec = "auto"`` is not simulated (the mock is "off").

API v1.13 (connected MCP servers): ``GET /api/mcp[?refresh=1]`` answers the real server's shape
from a fixture discovery (``MOCK_MCP_INIT``: two connected claude.ai connectors with read and
write tools, one that needs authentication, one that failed), classified and counted by
``lattice.studio.mcp_intake`` itself; ``refresh=1`` moves ``refreshed_at``. ``POST /api/changes``
validates and stores ``source`` like the real server (a follow-up keeps its parent's), and the
seeded change ``c0`` came from a fixture tracker item, so the change card shows a source.

API v1.14 (database engines and modules): ``GET /api/engines`` answers the real server's
shape: SQLite and the built-in engines read from the real registry (``lattice.spec.engines``;
SQLite ``used``), the project engine ``docs/examples/engines/mariadb.toml`` (``source``
``project``), and two fixture modules: ``acme-audit`` (kind custom, ``ok``, no engine) and
``pg-tuned`` (an engine module whose tree ``changed``: refused, so its engine is not listed and
``errors`` names it). No write route (``POST /api/engines`` answers 404).

API v1.15 (improvement proposals, spike 3): ``GET /api/improve`` answers the real server's
shape, ranked by ``lattice.improve.analyze`` itself from a fixture event log
(``MOCK_IMPROVE_EVENTS``: five regression level gates whose layer X took ~120 s, a start
baseline that re-ran an already-gated tip, a node that gave up on an unsatisfiable check, an
owner's resume) and a fixture ledger that closes the unsatisfiable-check cause. Read only (no
POST); no web UI in this version.

API v1.16 (workspace manifest, multi-project layer 2): ``GET /api/workspace`` answers the real
server's shape for a fixture manifest (``MOCK_WORKSPACE_*``): its two projects are the mock's two
(``in_hub``, ``hub_name`` computed from them), a third (``scheduler-web``, a consumer) is not
served and its directory is missing (a ``missing_path`` warning, worded by
``lattice.workspace.Problem``), and three links. ``MockServer.workspace_enabled = False`` answers
the no-manifest shape (``workspace: null``). Read only.

API v1.17 (contracts between projects, multi-project layer 3): ``GET /api/contracts
[?consumer=NAME]`` answers the real server's shape (``lattice contracts check --static-only
--json``) for the same fixture manifest, with its two ``uses`` links opted in
(``MOCK_CONTRACT_LINKS``): ``scheduler-web -> scheduler`` is static and ``broken`` (a BROKEN
``status_not_observed``, a CHANGED, a gap) and ``scheduler-web -> studio`` is live and ``ok``
with ``live: {status: not_run, reason: read-only}``. ``workspace_enabled = False`` answers the
no-manifest shape. Read only.

API v1.18 (cross-project impact preview, multi-project slice 4a): ``GET /api/contracts/impact
?provider=NAME&candidate=REF`` answers the real server's shape (``lattice contracts impact
PROVIDER --candidate REF --json``) for the same fixture manifest: provider ``scheduler`` has one
opted-in consumer, ``scheduler-web`` (static), whose row is a ``contradiction`` (the BROKEN and
CHANGED findings of v1.17's link); ``studio`` has one live link, ``unaffected`` with
``live: {status: not_run, reason: static-only}``; any other project of the manifest has no opted-in
link (``no_consumers``); a name the manifest does not declare answers ``unknown_project`` and an
empty argument ``missing_argument``, both with ``exit_code`` 2. The candidate is echoed as a
``ref`` that resolves to a fixed fixture SHA. ``workspace_enabled = False`` answers the
no-manifest shape. Read only.

API v1.19 (linked pending questions in consumers, multi-project slice 4b): ``GET
/api/linked-questions[?status=]``, ``GET /api/linked-questions/<id>``, ``POST
/api/linked-questions/<id>/resolve|dismiss {note?}`` and ``GET /api/linked-notifications`` answer
the real server's shapes. The consumer ``scheduler-web`` (a manifest project the mock does not
serve, so ``/p/scheduler-web/api/linked-*`` is answered specially) holds ``lq1`` open (v1.17's
link findings) and ``lq2`` dismissed; clearing answers in memory (``not_open`` 409, ``bad_note``
422, ``unknown_question`` 404). Every served project lists no question (``state: present``); the
provider ``job-scheduler-studio`` holds one ``done`` notification (its change ``c0`` created
``lq1``).

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

from lattice.contracts.linked import LEGEND
from lattice.studio import areas as areas_mod
from lattice.studio import plan as plan_mod
from lattice.studio.mcp_intake import McpIntake, SourceError, parse_init, parse_source
from lattice.studio.session import (
    SessionError,
    Transcript,
    parse_create,
    parse_message,
)

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "web"
FIXTURE_DIR = HERE / "mock"

# API v1.6: the mock's projects (GET /api/projects, /p/<name>/api/...).
DEFAULT_PROJECT = "job-scheduler-studio"
SECOND_PROJECT = "lattice-studio"
SECOND_PROJECT_RULES = 12

# API v1.16: the fixture workspace manifest GET /api/workspace answers
MOCK_WORKSPACE_NAME = "owner-dev"
MOCK_WORKSPACE_PATH = "/Users/owner/lattice.workspace.toml"
# (manifest name, hub project or None, path when not a hub project, role, spec)
MOCK_WORKSPACE_PROJECTS = (
    ("scheduler", DEFAULT_PROJECT, None, "provider", True),
    ("studio", SECOND_PROJECT, None, "app", True),
    ("scheduler-web", None, "/Users/owner/scheduler-web", "consumer", False),
)
MOCK_WORKSPACE_LINKS = (
    {"from": "scheduler-web", "to": "scheduler", "kind": "uses",
     "note": "POST /applications/{id}/runs"},
    {"from": "studio", "to": "scheduler", "kind": "depends_on", "note": "the fixtures"},
    {"from": "scheduler-web", "to": "studio", "kind": "uses", "note": ""},
)  # fmt: skip

# API v1.17: the two ``uses`` links of the fixture manifest, opted in to contract checks, as the
# static check reports them (GET /api/contracts)
MOCK_CONTRACT_VERSION_PINNED = {
    "spec_ref": "spec.tickets:t5",
    "spec_hash": "3c1f0b6a52d9e0b7f6f1e2a4c8d57b9a0e3f4d6c7b8a91025e4d3c2b1a0f9e8d",
    "git_ref": "5a0c7e1d9b3f4a6c8e2d1b0a9f8e7d6c5b4a3921",
    "dirty": False,
}
MOCK_CONTRACT_VERSION_CURRENT = {
    "spec_ref": "spec.tickets:t5",
    "spec_hash": "b7e2d9c4a1f08365e4d2c1b0a9f8e7d6c5b4a39281706f5e4d3c2b1a09f8e7d6",
    "git_ref": "5a0c7e1d9b3f4a6c8e2d1b0a9f8e7d6c5b4a3921",
    "dirty": True,
}
MOCK_CONTRACT_LINKS = (
    {
        "consumer": "scheduler-web", "provider": "scheduler", "contract": "static",
        "status": "broken", "error": None, "imports": 3,
        "pinned": MOCK_CONTRACT_VERSION_PINNED, "current": MOCK_CONTRACT_VERSION_CURRENT,
        "version_changed": True, "spec_hash_changed": True, "spec_ref_changed": False,
        "findings": [
            {"class": "BROKEN", "provider": "scheduler",
             "endpoint": "POST /applications/{}/jobs/{}/runs", "kind": "status_not_observed",
             "item": "201", "detail": "the spec now shows statuses [200, 403]"},
            {"class": "CHANGED", "provider": "scheduler",
             "endpoint": "POST /applications/{}/jobs/{}/runs", "kind": "status_added",
             "item": "200", "relied": True, "detail": "status 200 is now observed"},
        ],
        "gaps": [
            {"class": "GAP", "provider": "scheduler", "endpoint": "POST /auth/login",
             "kind": "response_field_unobserved", "item": "access_token",
             "detail": "the provider's spec never shows it"},
        ],
        "live": None,
    },
    {
        "consumer": "scheduler-web", "provider": "studio", "contract": "live",
        "status": "ok", "error": None, "imports": 1,
        "pinned": MOCK_CONTRACT_VERSION_PINNED, "current": MOCK_CONTRACT_VERSION_PINNED,
        "version_changed": False, "spec_hash_changed": False, "spec_ref_changed": False,
        "findings": [], "gaps": [],
        "live": {"status": "not_run", "reason": "read-only"},
    },
)  # fmt: skip

# API v1.18: the impact preview of the fixture manifest (GET /api/contracts/impact)
MOCK_IMPACT_SHA = "9d3e5f7a1b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e"
MOCK_IMPACT_VERSION = {
    "spec_hash": "e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d5e4f30291807f6e5d4c3b2a190",
    "git_ref": MOCK_IMPACT_SHA,
    "dirty": False,
}
MOCK_IMPACT_ROWS = {
    "scheduler": (
        {
            "consumer": "scheduler-web", "provider": "scheduler", "contract": "static",
            "label": "contradiction", "error": None, "imports": 3,
            "pinned": MOCK_CONTRACT_VERSION_PINNED, "version_changed": True,
            "spec_hash_changed": True, "spec_ref_changed": False, "current_label": "unaffected",
            "findings": MOCK_CONTRACT_LINKS[0]["findings"], "gaps": MOCK_CONTRACT_LINKS[0]["gaps"],
            "counts": {"broken": 1, "changed_relied": 1, "changed_unrelied": 0, "gaps": 1},
            "live": None,
        },
    ),
    "studio": (
        {
            "consumer": "scheduler-web", "provider": "studio", "contract": "live",
            "label": "unaffected", "error": None, "imports": 1,
            "pinned": MOCK_CONTRACT_VERSION_PINNED, "version_changed": True,
            "spec_hash_changed": False, "spec_ref_changed": False, "current_label": "unaffected",
            "findings": [], "gaps": [],
            "counts": {"broken": 0, "changed_relied": 0, "changed_unrelied": 0, "gaps": 0},
            "live": {"status": "not_run", "reason": "static-only"},
        },
    ),
}  # fmt: skip

# API v1.19: the linked questions of the fixture consumer (GET /api/linked-questions)
MOCK_LINKED_CONSUMER = "scheduler-web"
MOCK_LINKED_STATE = "/Users/owner/scheduler-web/.lattice/state/scheduler-web"


def _mock_linked_question(
    qid: str,
    status: str,
    findings: list[dict[str, Any]],
    endpoints: list[str],
    resolution: dict[str, Any] | None,
    key: str,
) -> dict[str, Any]:
    broken = [f for f in findings if f["class"] == "BROKEN"]
    relied = sum(1 for f in findings if f["class"] == "CHANGED" and f.get("relied"))
    ver = MOCK_IMPACT_VERSION
    return {
        "format": 1, "id": qid, "key": key, "kind": "linked_question",
        "workspace": MOCK_WORKSPACE_NAME, "provider": "scheduler",
        "consumer": MOCK_LINKED_CONSUMER, "contract": "static", "status": status,
        "label": "contradiction", "pinned": dict(MOCK_CONTRACT_VERSION_PINNED),
        "provider_after": {"spec_ref": "spec.tickets:t5", "spec_hash": ver["spec_hash"],
                           "git_ref": ver["git_ref"], "dirty": False},
        "candidate": {"input": ver["git_ref"], "kind": "ref", "resolved": ver["git_ref"]},
        "imports_affected": endpoints, "findings": findings,
        "counts": {"broken": len(broken), "changed_relied": relied, "changed_unrelied": 0,
                   "gaps": 1},
        "text": f"scheduler's accepted contract (spec_hash {ver['spec_hash'][:12]}, commit "
                f"{ver['git_ref'][:8]}) contradicts scheduler-web's pin (spec_hash "
                f"{MOCK_CONTRACT_VERSION_PINNED['spec_hash'][:12]}): {len(broken)} import(s) "
                f"BROKEN on {len(endpoints)} endpoint(s)",
        "suggestion": "advisory, nothing was changed in scheduler-web: adapt scheduler-web to "
                      'the findings, then "lattice contracts pin scheduler-web" and "lattice '
                      'contracts check scheduler-web"; or ask scheduler\'s owner to revert. '
                      f'Clear it with "lattice contracts questions resolve|dismiss '
                      f'scheduler-web {qid}"',
        "source": {"via": "accept", "change": "c0", "commit": ver["git_ref"]},
        "created_at": "2026-10-14T09:30:00Z", "resolution": resolution, "superseded": None,
    }  # fmt: skip


MOCK_LINKED_QUESTIONS = (
    _mock_linked_question(
        "lq1", "open", copy.deepcopy(MOCK_CONTRACT_LINKS[0]["findings"]),
        ["POST /applications/{}/jobs/{}/runs"], None,
        "sha256:" + "1f" * 32,
    ),
    _mock_linked_question(
        "lq2", "dismissed",
        [{"class": "BROKEN", "provider": "scheduler", "endpoint": "GET /applications/{}/jobs",
          "kind": "endpoint_missing", "item": "", "detail": "the provider's spec has no such "
                                                           "endpoint"}],
        ["GET /applications/{}/jobs"],
        {"status": "dismissed", "note": "the web UI no longer lists jobs", "at":
         "2026-10-14T10:05:00Z", "via": "studio"},
        "sha256:" + "2e" * 32,
    ),
)  # fmt: skip
MOCK_LINKED_NOTIFICATION = {
    "change": "c0", "commit": MOCK_IMPACT_SHA, "status": "done", "reason": None,
    "started_at": "2026-10-14T09:30:00Z", "error": None,
    "result": {
        "format": 1, "workspace": MOCK_WORKSPACE_NAME, "provider": "scheduler",
        "candidate": {"input": MOCK_IMPACT_SHA, "kind": "ref", "resolved": MOCK_IMPACT_SHA,
                      "spec_ref": "spec.tickets:t5", "version": dict(MOCK_IMPACT_VERSION),
                      "endpoints": 18},
        "source": {"via": "accept", "change": "c0", "commit": MOCK_IMPACT_SHA},
        "advisory": True, "dry_run": False,
        "consumers": [{"consumer": MOCK_LINKED_CONSUMER, "contract": "static",
                       "label": "contradiction", "error": None, "action": "created",
                       "question": "lq1", "current": "lq1", "state_dir": MOCK_LINKED_STATE,
                       "warning": None}],
        "counts": {"created": 1, "existing": 0, "none": 0, "undelivered": 0, "skipped": 0},
        "problems": [], "exit_code": 0, "elapsed_s": 0.0,
    },
}  # fmt: skip

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
# API v1.12: a fixture target branch whose committed spec is behind main's
SPEC_BEHIND_BRANCH = "build/spec-behind"
SPEC_BEHIND_FILES = ["REQUIREMENTS.md", "spec.trace.json"]
SPEC_DRIFT_WARNING = "The spec on "
# API v1.15 (spike 3): the fixture event log and ledger GET /api/improve ranks
MOCK_IMPROVE_EVENTS = (
    *({"v": 1, "ts": f"2026-10-11T23:{40 + 5 * i:02d}:00Z", "project": "mock",
       "kind": "gate_run", "id": f"e-mock-level-{i}", "gate": "level", "mode": "regression",
       "plan": "p2", "level": i + 2, "seconds": s, "x_repeats": 5,
       "layer_seconds": {"A": 2.1, "B": 3.4, "C": 1.2, "D": round(s - 126.7, 2), "X": 120.0}}
      for i, s in enumerate((137.5, 138.92, 143.71, 145.32, 150.89))),
    {"v": 1, "ts": "2026-10-11T02:02:35Z", "project": "mock", "kind": "gate_run",
     "id": "e-mock-level-1", "gate": "level", "mode": "full", "plan": "p2", "level": 1,
     "seconds": 423.87, "sha": "941735104cf670beb7f0905cd8d14b6022eda586"},
    {"v": 1, "ts": "2026-10-11T23:40:31Z", "project": "mock", "kind": "gate_run",
     "id": "e-mock-baseline", "gate": "baseline", "mode": "full", "plan": "p2",
     "seconds": 416.0, "sha": "941735104c"},
    {"v": 1, "ts": "2026-10-11T23:45:40Z", "project": "mock", "kind": "node_ended",
     "id": "e-mock-account", "plan": "p2", "node": "account", "build": "b4",
     "status": "gave_up", "cause": "spec_unsatisfiable", "rounds": 3, "wall_s": 308.85,
     "usage": {"cost_usd_list_estimate": 1.229954}},
    {"v": 1, "ts": "2026-10-12T01:28:25Z", "project": "mock", "kind": "human",
     "id": "e-mock-resume", "what": "resume", "actor": "owner", "plan": "p2"},
)  # fmt: skip
MOCK_IMPROVE_LEDGER = (
    {"id": "I-05", "signal": "cause:spec_unsatisfiable", "status": "closed",
     "commit": "e93faea", "ts": "2026-10-12T00:00:00Z"},
    {"id": "I-07", "signal": "gate:level_full:*", "status": "closed", "commit": "v1.11",
     "ts": "2026-10-11T12:00:00Z"},
)  # fmt: skip
# API v1.14: the project engine and the modules GET /api/engines lists
MOCK_ENGINE_FILE = Path(__file__).resolve().parents[2] / "docs/examples/engines/mariadb.toml"
MOCK_MODULES = (
    {"name": "acme-audit", "version": "0.2.0", "status": "ok",
     "sha256": "3f1c9a0e5b7d2c4a6e8f0b1d3c5e7a9b2d4f6a8c0e1b3d5f7a9c2e4b6d8f0a1c"},
    {"name": "pg-tuned", "version": "1.0.0", "status": "changed",
     "sha256": "9b8a7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b"},
)  # fmt: skip
MOCK_MODULE_ERROR = (
    "module 'pg-tuned' (1.0.0) is changed: its tree hashes to "
    "0000000000000000000000000000000000000000000000000000000000000000, the lock says "
    "9b8a7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b; it is refused "
    "(reinstall or uninstall it)"
)

# API v1.13: the system/init record a discovery would read (names only, as Claude Code prints them)
MOCK_MCP_INIT: dict[str, Any] = {
    "type": "system",
    "subtype": "init",
    "mcp_servers": [
        {"name": "claude.ai Gmail", "status": "connected", "source": "claudeai"},
        {"name": "claude.ai Claude Docs", "status": "connected", "source": "claudeai"},
        {"name": "claude.ai Google Drive", "status": "needs-auth", "source": "claudeai"},
        {"name": "claude.ai Tracker", "status": "failed", "source": "claudeai"},
    ],
    "tools": [
        *(f"mcp__claude_ai_Gmail__{t}" for t in (
            "search_threads", "get_thread", "get_message", "list_labels", "list_drafts",
            "get_draft", "send_message", "create_draft", "reply", "forward", "trash_thread",
            "label_message", "mark_message_spam")),
        *(f"mcp__claude_ai_Claude_Docs__{t}" for t in (
            "read", "query", "export", "guide", "create", "update", "delete", "batch")),
    ],
}  # fmt: skip
MOCK_SOURCE = {"server": "claude.ai Tracker", "item": "JOB-42",
               "url": "https://tracker.example.com/issue/JOB-42",
               "title": "Only admins should cancel runs"}  # fmt: skip

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


FLOW_FIXTURES = ("flow-start-run", "flow-cancel-run")


def flow_key(endpoint: str) -> str:
    """A route in any of its spellings ("POST /configs/{}/runs", "POST
    /applications/1/jobs/{id}/runs") reduced to one comparable form."""
    method, _, path = endpoint.strip().partition(" ")
    path = re.sub(r"\{[^}]*\}|(?<=/)\d+(?=/|$)", "{}", path)
    path = re.sub(r"^/applications/\{\}", "", path).replace("/configs/", "/jobs/")
    return f"{method.upper()} {path}"


# ---------------------------------------------------------------- business-logic graph

GRAPH_KINDS = ("rule", "endpoint", "table", "service", "scenario", "code", "invariant", "machine")
GRAPH_DEFAULT_LIMIT = 400
GRAPH_MAX_DEPTH = 6
# Worst first: a derived node shows the worst trust among the rules it touches.
TRUST_ORDER = ("unchecked", "probe_gap", "untraced", "not_built", "checked", "context")
# Which kinds survive a truncation first.
KIND_PRIORITY = {k: i for i, k in enumerate(GRAPH_KINDS)}
CHECK_KINDS = ("scenario", "invariant", "machine", "service")
METHODS = "GET|POST|PUT|PATCH|DELETE"
_RULE_ENDPOINT = re.compile(rf"`((?:{METHODS}) [^`\s]+)`")
_STEP_ENDPOINT = re.compile(rf"^({METHODS}) (\S+)")
_BACKTICK = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)(?:\.[A-Za-z_][A-Za-z0-9_]*)?`")


def _rule_num(rid: str) -> int:
    m = re.search(r"\d+", rid)
    return int(m.group()) if m else 0


def _norm_route(route: str) -> str:
    """``POST /applications/{app}/jobs/1`` -> ``POST /applications/{}/jobs/{}``."""
    method, _, path = route.strip().partition(" ")
    path = path.split("?", 1)[0]
    path = re.sub(r"\{[^}]*\}", "{}", path)
    path = re.sub(r"(?<=/)\d+(?=/|$)", "{}", path)
    return f"{method.upper()} {path}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "stage"


def _short(text: str, n: int = 90) -> str:
    text = re.sub(r"[*`]", "", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def with_stage_links(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stages with a stable ``id`` (the slug of the stage name, unique within the flow)
    and ``next`` (the ids that follow: the lanes of a graph). Existing values are kept."""
    seen: set[str] = set()
    ids: list[str] = []
    for s in stages:
        base = s.get("id") or _slug(s["stage"])
        sid, n = base, 2
        while sid in seen:
            sid, n = f"{base}-{n}", n + 1
        seen.add(sid)
        ids.append(sid)
    out = []
    for i, s in enumerate(stages):
        item = dict(s)
        item["id"] = ids[i]
        item.setdefault("next", [ids[i + 1]] if i + 1 < len(ids) else [])
        out.append(item)
    return out


def parse_machine(target: str, name: str, text: str) -> dict[str, Any]:
    """``"queued -> running, cancelled; running -> done (initial: queued)"`` -> states and
    moves. A final state is one with no outgoing move."""
    initial = None
    m = re.search(r"\(initial:\s*([^)]+)\)", text)
    if m:
        initial = m.group(1).strip()
        text = text[: m.start()]
    moves: list[dict[str, Any]] = []
    order: list[str] = [initial] if initial else []
    for clause in text.split(";"):
        src, arrow, dsts = clause.partition("->")
        src = src.strip()
        if not arrow or not src:
            continue
        if src not in order:
            order.append(src)
        for dst in (d.strip() for d in dsts.split(",")):
            if dst:
                moves.append({"from": src, "to": dst})
                if dst not in order:
                    order.append(dst)
    sources = {mv["from"] for mv in moves}
    states = []
    for s in order:
        st: dict[str, Any] = {"id": s}
        if s == initial:
            st["initial"] = True
        if s not in sources:
            st["final"] = True
        states.append(st)
    return {"target": target, "name": name, "states": states, "moves": moves}


def _worst(trusts: list[str]) -> str:
    for t in TRUST_ORDER:
        if t in trusts:
            return t
    return "context"


def build_graph(
    model: dict[str, Any],
    code: dict[str, Any],
    flows: list[dict[str, Any]],
    not_built: set[str],
) -> dict[str, Any]:
    """The whole business-logic graph: ``{"nodes": {id: node}, "edges": {id: edge},
    "machines": [...]}``. Pure: the same inputs always give the same graph."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    def node(kind: str, target: str, label: str, sub: str | None = None) -> dict[str, Any]:
        n = nodes.get(target)
        if n is None:
            n = {"id": target, "kind": kind, "label": label, "target": target}
            if sub:
                n["sub"] = sub
            nodes[target] = n
        return n

    def edge(src: str, dst: str, kind: str, label: str | None = None) -> None:
        if src == dst or src not in nodes or dst not in nodes:
            return
        eid = f"{src}>{kind}>{dst}"
        if eid not in edges:
            e = {"id": eid, "source": src, "target": dst, "kind": kind}
            if label:
                e["label"] = label
            edges[eid] = e

    views = model.get("views", {})
    s6 = views.get("section6", {})
    rules = model["rules"]
    for r in rules:
        node("rule", f"rule:{r['id']}", r["id"], _short(r["text"]))
    for t in model.get("data_model", {}).get("tables", []):
        node("table", f"profile:{t['name']}", t["name"], t["sql"])
    sql_to_table = {t["sql"]: f"profile:{t['name']}" for t in model["data_model"]["tables"]}
    for target, name, text in s6.get("services", []):
        node("service", target, name, _short(text))
    for target, name, text in s6.get("invariants", []):
        node("invariant", target, name, _short(text))
    for target, name, text in s6.get("machines", []):
        node("machine", target, name, text)
    for sc in s6.get("scenarios", []):
        node("scenario", sc["target"], sc["name"], _short(sc["steps"][0]) if sc["steps"] else None)

    # Endpoints: every column of the access matrix.
    ep_of: dict[str, str] = {}
    for g in views.get("access_groups", []):
        for col in g["columns"]:
            node("endpoint", f"endpoint:{col}", col)
            ep_of[_norm_route(col)] = f"endpoint:{col}"

    def endpoint_for(route: str) -> list[str]:
        route = route.strip()
        method, _, path = route.partition(" ")
        if path.startswith("..."):
            tail = _norm_route(route)[len(method) + 4 :]
            return sorted(
                t for k, t in ep_of.items() if k.startswith(method + " ") and k.endswith(tail)
            )
        hit = ep_of.get(_norm_route(route))
        return [hit] if hit else []

    # rule -> scenario / invariant / machine / service / table, and rule text -> endpoint, table.
    for r in rules:
        src = f"rule:{r['id']}"
        for link in r.get("links", []):
            kind = link.split(":", 1)[0]
            if kind in ("scenario", "invariant"):
                edge(src, link, "traces")
            elif kind == "machine":
                edge(src, link, "transitions")
            elif kind == "service":
                edge(src, link, "applies_to")
            elif kind == "profile":
                tbl = f"profile:{link.split(':', 1)[1].split('.', 1)[0]}"
                edge(src, tbl, "applies_to")
            elif kind == "cell":
                for ep in endpoint_for(link.split("|", 1)[-1]):
                    edge(src, ep, "enforces")
        for m in _RULE_ENDPOINT.finditer(r["text"]):
            for ep in endpoint_for(m.group(1)):
                edge(src, ep, "enforces")
        for m in _BACKTICK.finditer(r["text"]):
            if m.group(1) in sql_to_table:
                edge(src, sql_to_table[m.group(1)], "applies_to")
    for target in [t for t, n in nodes.items() if n["kind"] == "machine"]:
        tbl = f"profile:{target.split(':', 1)[1].split('.', 1)[0]}"
        edge(target, tbl, "applies_to")

    # scenario steps -> endpoint
    for sc in s6.get("scenarios", []):
        for step in sc["steps"]:
            m = _STEP_ENDPOINT.match(step)
            if m:
                for ep in endpoint_for(f"{m.group(1)} {m.group(2)}"):
                    edge(sc["target"], ep, "traces")

    # endpoint -> table: the resource a route names, then the flow fixtures' reads/writes.
    for key, ep in ep_of.items():
        method, _, path = key.partition(" ")
        segs = [s for s in path.split("/") if s and s != "{}"]
        if segs and segs[-1] in sql_to_table:
            edge(ep, sql_to_table[segs[-1]], "reads" if method == "GET" else "writes")

    # code symbols: index links and flow stages.
    sym_line: dict[tuple[str, str], int] = {}
    for link in code.get("links", []):
        k = (link["path"], link["symbol"])
        sym_line[k] = min(sym_line.get(k, link["line_from"]), link["line_from"])
    for fl in flows:
        for st in fl["stages"]:
            k = (st["path"], st["symbol"])
            sym_line[k] = min(sym_line.get(k, st["line"]), st["line"])
    code_id: dict[tuple[str, str], str] = {}
    for (path, symbol), line in sorted(sym_line.items()):
        cid = f"code:{path}:{line}"
        code_id[(path, symbol)] = cid
        node("code", cid, symbol, f"{path}:{line}")
    for link in code.get("links", []):
        cid = code_id[(link["path"], link["symbol"])]
        for rid in link["rules"]:
            edge(cid, f"rule:{rid}", "implements")
        for t in link["targets"]:
            if t.startswith("cell:"):
                for ep in endpoint_for(t.split("|", 1)[-1]):
                    edge(cid, ep, "implements")
                    for rid in link["rules"]:
                        edge(f"rule:{rid}", ep, "enforces")
    flow_ep = {flow_key(t.removeprefix("endpoint:")): t for t in ep_of.values()}
    for fl in flows:
        ep = flow_ep.get(flow_key(fl["endpoint"]))
        for st in fl["stages"]:
            cid = code_id[(st["path"], st["symbol"])]
            if ep:
                edge(cid, ep, "implements")
                for tname in st["reads"]:
                    if tname in sql_to_table:
                        edge(ep, sql_to_table[tname], "reads")
                for tname in st["writes"]:
                    if tname in sql_to_table:
                        edge(ep, sql_to_table[tname], "writes")
            for rid in st["rules"]:
                edge(cid, f"rule:{rid}", "implements")
            for tname in st["reads"]:
                if tname in sql_to_table:
                    edge(cid, sql_to_table[tname], "reads")
            for tname in st["writes"]:
                if tname in sql_to_table:
                    edge(cid, sql_to_table[tname], "writes")

    # Derived edges: tie each rule to what it governs. A derived edge carries
    # ``inferred: true`` and ``via`` (the node ids between its ends; every consecutive
    # pair, ends included, is joined by a direct edge). A direct edge with the same id
    # always wins, and a derived edge is never added twice.
    direct = list(edges.values())
    out_of: dict[str, list[tuple[str, str]]] = {}
    into: dict[str, list[tuple[str, str]]] = {}
    for e in direct:
        out_of.setdefault(e["source"], []).append((e["kind"], e["target"]))
        into.setdefault(e["target"], []).append((e["kind"], e["source"]))

    def derive(src: str, dst: str, kind: str, via: list[str]) -> None:
        eid = f"{src}>{kind}>{dst}"
        if src == dst or eid in edges:
            return
        edges[eid] = {
            "id": eid,
            "source": src,
            "target": dst,
            "kind": kind,
            "inferred": True,
            "via": via,
        }

    def nk(t: str) -> str:
        return nodes[t]["kind"]

    rule_ids = sorted((t for t in nodes if nk(t) == "rule"), key=lambda t: _rule_num(t))
    for r in rule_ids:
        # 1. rule -traces-> scenario -traces-> endpoint  =>  rule -enforces-> endpoint
        for k1, sc in sorted(out_of.get(r, [])):
            if k1 != "traces" or nk(sc) != "scenario":
                continue
            for k2, ep in sorted(out_of.get(sc, [])):
                if k2 == "traces" and nk(ep) == "endpoint":
                    derive(r, ep, "enforces", [sc])
        # 2. code -implements-> rule, code -implements-> endpoint  =>  rule -enforces-> endpoint
        for k1, cd in sorted(into.get(r, [])):
            if k1 != "implements" or nk(cd) != "code":
                continue
            for k2, ep in sorted(out_of.get(cd, [])):
                if k2 == "implements" and nk(ep) == "endpoint":
                    derive(r, ep, "enforces", [cd])
    for r in rule_ids:
        # 3. rule -enforces-> endpoint (direct, or derived through a scenario) -writes-> table
        #    =>  rule -applies_to-> table
        for e in sorted(
            (x for x in edges.values() if x["source"] == r and x["kind"] == "enforces"),
            key=lambda x: x["id"],
        ):
            hop = e.get("via", [])
            if hop and nk(hop[0]) != "scenario":
                continue
            for k2, tbl in sorted(out_of.get(e["target"], [])):
                if k2 == "writes" and nk(tbl) == "table":
                    derive(r, tbl, "applies_to", [*hop, e["target"]])
        # 4. code -implements-> rule, code -reads/writes-> table  =>  rule -applies_to-> table
        for k1, cd in sorted(into.get(r, [])):
            if k1 != "implements" or nk(cd) != "code":
                continue
            for k2, tbl in sorted(out_of.get(cd, [])):
                if k2 in ("reads", "writes") and nk(tbl) == "table":
                    derive(r, tbl, "applies_to", [cd])

    # trust and failing
    results = (model.get("app", {}).get("last_check") or {}).get("targets", {})
    failing_checks = {t for t, v in results.items() if v == "fail" and t in nodes}
    # ``adj`` (every edge) drives focus and the diff's impact; ``near`` (direct edges only)
    # drives trust, ``rules`` and ``failing``, so a derived edge never changes those.
    adj: dict[str, set[str]] = {t: set() for t in nodes}
    near: dict[str, set[str]] = {t: set() for t in nodes}
    for e in edges.values():
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])
        if not e.get("inferred"):
            near[e["source"]].add(e["target"])
            near[e["target"]].add(e["source"])
    rule_by_id = {r["id"]: r for r in rules}

    def rule_trust(rid: str) -> str:
        if rid in not_built:
            return "not_built"
        return rule_by_id[rid]["status"]

    for t, n in nodes.items():
        if n["kind"] == "rule":
            rid = t.split(":", 1)[1]
            n["trust"] = rule_trust(rid)
            n["rules"] = [rid]
            links = {x for x in rule_by_id[rid].get("links", []) if x in failing_checks}
            n["failing"] = len(links)
            continue
        rids = sorted({a.split(":", 1)[1] for a in near[t] if a.startswith("rule:")}, key=_rule_num)
        n["rules"] = rids
        gov = sorted({a.split(":", 1)[1] for a in adj[t] if a.startswith("rule:")}, key=_rule_num)
        if n["kind"] in ("endpoint", "table"):
            n["governed_by"] = gov
        n["trust"] = _worst([rule_trust(x) for x in rids])
        if n["kind"] in CHECK_KINDS:
            n["failing"] = 1 if t in failing_checks else 0
            continue
        seen = {a for a in near[t] if a in failing_checks}
        for x in rids:
            seen.update(y for y in rule_by_id[x].get("links", []) if y in failing_checks)
        n["failing"] = len(seen)

    machines = []
    for target, name, text in s6.get("machines", []):
        mc = parse_machine(target, name, text)
        linked = [r for r in rules if target in r.get("links", [])]
        for mv in mc["moves"]:
            hit = sorted(
                (r["id"] for r in linked if re.search(rf"\b{re.escape(mv['to'])}\b", r["text"])),
                key=_rule_num,
            )
            if hit:
                mv["rules"] = hit
        mc["rules"] = sorted((r["id"] for r in linked), key=_rule_num)
        mc["trust"] = nodes[target]["trust"]
        mc["failing"] = nodes[target]["failing"]
        machines.append(mc)
    return {"nodes": nodes, "edges": edges, "machines": machines, "adj": adj, "near": near}


def graph_diff(change: dict[str, Any] | None, graph: dict[str, Any]) -> dict[str, str]:
    """target -> modified|added|removed|impact for the open change, the way the UI overlay
    reads ``view_diff``. ``impact`` marks the nodes next to a changed one and the targets
    the impact run reports failing."""
    if change is None:
        return {}
    nodes: dict[str, Any] = graph["nodes"]
    out: dict[str, str] = {}

    def item(x: Any) -> str:
        if isinstance(x, str):
            return x
        if isinstance(x, list):
            return str(x[0])
        return str(x.get("target") or "")

    def put(target: str, how: str) -> None:
        if target:
            out[target] = how

    vd = change.get("view_diff", {})
    for cell in vd.get("cells", []):
        ep = next((n for n in nodes if n == f"endpoint:{cell['col']}"), None)
        if ep:
            put(ep, "modified")
    for group, kind in (("scenarios", "scenario"), ("invariants", "invariant")):
        g = vd.get(group, {})
        for how in ("added", "removed", "modified"):
            for x in g.get(how, []):
                t = item(x)
                put(t if ":" in t else f"{kind}:{t}", how)
    for x in vd.get("machines", {}).get("changed", []):
        put(item(x), "modified")
    for how in ("added", "removed"):
        for x in change.get("targets", {}).get(how, []):
            put(item(x), how)
    for x in change.get("targets", {}).get("modified", []):
        put(item(x), "modified")
    rl = change.get("rules", {})
    for rid in rl.get("added", []):
        put(f"rule:{rid}", "added")
    for rid in rl.get("removed", []):
        put(f"rule:{rid}", "removed")
    for x in rl.get("changed", []):
        rid = x if isinstance(x, str) else x.get("new") or x.get("old")
        put(f"rule:{rid}", "modified")
    changed = [t for t in out if t in nodes]
    for t in changed:
        for a in graph["near"][t]:
            out.setdefault(a, "impact")
    impact = (change.get("checks") or {}).get("impact") or {}
    for f in impact.get("failing", []):
        t = f.get("target", "")
        if t in nodes:
            out.setdefault(t, "impact")
    return dict(sorted(out.items()))


def resolve_focus(graph: dict[str, Any], focus: str) -> str | None:
    """The node id a focus target names: a node's own target, a ``cell:<row>|<route>``
    (its endpoint), a ``profile:<Table>.<column>`` (its table) or a route in any spelling."""
    nodes = graph["nodes"]
    if focus in nodes:
        return focus
    if focus.startswith("cell:"):
        return resolve_focus(graph, "endpoint:" + focus.split("|", 1)[-1])
    if focus.startswith("profile:") and "." in focus:
        return resolve_focus(graph, focus.split(".", 1)[0])
    if focus.startswith("endpoint:"):
        want = flow_key(focus.removeprefix("endpoint:"))
        for t, n in nodes.items():
            if n["kind"] == "endpoint" and flow_key(t.removeprefix("endpoint:")) == want:
                return t
    return None


def select_graph(
    graph: dict[str, Any],
    focus: str | None,
    depth: int,
    kinds: set[str] | None,
    limit: int,
) -> dict[str, Any]:
    """The nodes and edges of ``graph`` within ``depth`` hops of ``focus`` (the whole
    graph without one), of the given kinds, capped at ``limit`` nodes."""
    nodes: dict[str, Any] = graph["nodes"]
    adj: dict[str, set[str]] = graph["adj"]
    dist: dict[str, int] = {}
    if focus is None:
        dist = {t: 0 for t in nodes}
    else:
        dist[focus] = 0
        frontier = [focus]
        for d in range(1, depth + 1):
            nxt: list[str] = []
            for t in frontier:
                for a in sorted(adj[t]):
                    if a not in dist:
                        dist[a] = d
                        nxt.append(a)
            frontier = nxt
    keep = [t for t in dist if t == focus or kinds is None or nodes[t]["kind"] in kinds]
    keep.sort(key=lambda t: (t != focus, dist[t], KIND_PRIORITY[nodes[t]["kind"]], t))
    truncated = len(keep) > limit
    keep = keep[:limit]
    kept = set(keep)
    out_nodes = [nodes[t] for t in sorted(kept, key=lambda t: (KIND_PRIORITY[nodes[t]["kind"]], t))]
    out_edges = sorted(
        (e for e in graph["edges"].values() if e["source"] in kept and e["target"] in kept),
        key=lambda e: e["id"],
    )
    nc: dict[str, int] = {}
    for n in out_nodes:
        nc[n["kind"]] = nc.get(n["kind"], 0) + 1
    ec: dict[str, int] = {}
    for e in out_edges:
        ec[e["kind"]] = ec.get(e["kind"], 0) + 1
    return {
        "nodes": out_nodes,
        "edges": out_edges,
        "counts": {
            "nodes": dict(sorted(nc.items())),
            "edges": dict(sorted(ec.items())),
            "total": {"nodes": len(nodes), "edges": len(graph["edges"])},
        },
        "truncated": truncated,
    }


class MockSessions:
    """Chat sessions replayed from ``session-*.jsonl`` fixtures: raw Claude Code
    stream-json records, each with a ``_poll`` number. A message starts a turn; every
    ``GET .../events`` while it runs is one poll and reveals the records up to that number,
    fed through the same normalizer as the real server (lattice/studio/session.py). A
    message containing "limit" replays ``session-error.jsonl`` (a usage-limit error)."""

    def __init__(self, fixture_dir: Path) -> None:
        self.fixture_dir = fixture_dir
        self.lock = threading.Lock()
        self.sessions: dict[str, dict[str, Any]] = {}
        self._next = 1

    def _records(self, name: str) -> list[dict[str, Any]]:
        text = (self.fixture_dir / f"{name}.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _get(self, sid: str) -> dict[str, Any]:
        s = self.sessions.get(sid)
        if s is None:
            raise SessionError(404, f"no session {sid}")
        return s

    def _summary(self, s: dict[str, Any]) -> dict[str, Any]:
        t: Transcript = s["t"]
        return {
            "id": s["id"],
            "mode": s["mode"],
            "change": s["change"],
            "state": t.state,
            "claude_session_id": t.claude_session_id,
            "resume_command": (
                f"claude --resume {t.claude_session_id}" if t.claude_session_id else None
            ),
            "cwd": "/mock/project",
            "turns": t.turns,
            "cost_usd_total": round(t.cost_total, 6),
        }

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        mode, change = parse_create(body)
        with self.lock:
            sid = f"s{self._next}"
            self._next += 1
            self.sessions[sid] = {
                "id": sid,
                "mode": mode,
                "change": change,
                "t": Transcript(),
                "records": [],
                "polls": 0,
                "fed": 0,
            }
            return self._summary(self.sessions[sid])

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self._summary(s) for s in self.sessions.values()]

    def message(self, sid: str, body: dict[str, Any]) -> dict[str, Any]:
        text, selected = parse_message(body)
        with self.lock:
            s = self._get(sid)
            t: Transcript = s["t"]
            if t.state in ("starting", "running"):
                raise SessionError(409, "a turn is already running in this session")
            t.add_user(text, selected)
            t.set_state("starting")
            fixture = "session-error" if "limit" in text.lower() else "session-ask"
            s["records"], s["polls"], s["fed"] = self._records(fixture), 0, 0
            return {"turn": t.turns}

    def events(self, sid: str, after: int) -> dict[str, Any]:
        with self.lock:
            s = self._get(sid)
            t: Transcript = s["t"]
            if t.state in ("starting", "running"):
                s["polls"] += 1
                recs = s["records"]
                while s["fed"] < len(recs) and recs[s["fed"]].get("_poll", 0) <= s["polls"]:
                    t.feed({k: v for k, v in recs[s["fed"]].items() if k != "_poll"})
                    s["fed"] += 1
                if s["fed"] >= len(recs):
                    t.set_state("idle")
            return t.view(after)

    def interrupt(self, sid: str) -> dict[str, Any]:
        with self.lock:
            s = self._get(sid)
            t: Transcript = s["t"]
            if t.state not in ("starting", "running"):
                raise SessionError(409, "no turn is running in this session")
            s["fed"] = len(s["records"])
            t.set_state("interrupted")
            return {"id": sid, "state": "interrupted"}


class MockRefusal(Exception):
    """A refused mock request: answered as ``{"detail", "code"?}`` with `status`."""

    def __init__(self, status: int, detail: str, code: str | None = None) -> None:
        super().__init__(detail)
        self.status, self.detail, self.code = status, detail, code

    def body(self) -> dict[str, str]:
        return {"detail": self.detail, **({"code": self.code} if self.code else {})}


class MockState:
    """The mutable state behind the mock API: change sets, the check run, the git head."""

    def __init__(
        self,
        fixture_dir: Path,
        name: str | None = None,
        root: str | None = None,
        rules: int | None = None,
        seed_change: bool = True,
    ) -> None:
        self.fixture_dir = fixture_dir
        self.lock = threading.Lock()
        self.model: dict[str, Any] = self._load("model")
        if name is not None:
            self.model["project"]["name"] = name
        if root is not None:
            self.model["project"]["root"] = root
        if rules is not None:  # a smaller project: the first `rules` rules only
            self.model["rules"] = self.model["rules"][:rules]
            keep = {r["id"] for r in self.model["rules"]}
            views = self.model["views"]
            views["rule_rows"] = [r for r in views.get("rule_rows", []) if r["id"] in keep]
            counts = views.setdefault("counts", {})
            for k in ("checked", "unchecked", "context", "untraced", "probe_gap"):
                counts[k] = sum(1 for r in self.model["rules"] if r.get("status") == k)
            views["title"] = f"{self.model['project']['name']} spec review"
        self.changes: dict[str, dict[str, Any]] = {}
        self.check_polls_left: int | None = None
        self._next_id = 1
        self.code: dict[str, Any] = json.loads(
            (fixture_dir / "code" / "index.json").read_text(encoding="utf-8")
        )
        self.rebuilds: dict[str, dict[str, Any]] = {}
        self._next_rebuild = 1
        self.built_in: str | None = None  # the change a passed rebuild built, for the Code lens
        # build from spec (v1.8): simulated branches and per-branch failing targets
        self.branch_heads: dict[str, str] = {"main": self.model["project"]["git_head"]}
        self.branch_failing: dict[str, set[str]] = {}
        # spec sync (v1.12): a fixture branch whose spec is behind main's
        self.branch_heads[SPEC_BEHIND_BRANCH] = "5bc1e2d"
        self.spec_behind: dict[str, list[str]] = {SPEC_BEHIND_BRANCH: list(SPEC_BEHIND_FILES)}
        self.sessions = MockSessions(fixture_dir)
        self.plans = MockPlans(self)
        self.mcp_refreshed: str | None = None
        if seed_change:
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
            "source": dict(MOCK_SOURCE),  # v1.13: it came from a tracker item
        }

    # GET /api/engines (v1.14): the real registry's built-ins plus fixtures
    def engines(self) -> dict[str, Any]:
        from lattice.spec import engines as eng

        def row(e: Any, source: str, used: bool = False) -> dict[str, Any]:
            return {"name": e.name if e else "sqlite", "source": source, "used": used,
                    "server": {"image": e.server.image} if e else None,
                    "adapter": e.adapter if e else "sqlite",
                    "requires": list(e.requires) if e else []}  # fmt: skip

        rows = [row(None, "builtin", used=True)]
        rows += [row(r.engine, "builtin") for r in eng.REGISTRY.entries() if r.source == "builtin"]
        if MOCK_ENGINE_FILE.is_file():
            rows.append(row(eng.read_descriptor(MOCK_ENGINE_FILE), "project"))
        return {
            "engines": rows,
            "modules": [dict(m) for m in MOCK_MODULES],
            "errors": [MOCK_MODULE_ERROR],
        }

    # GET /api/improve (v1.15): the fixture event log, ranked by lattice.improve itself
    def improve(self) -> dict[str, Any]:
        from lattice.improve.analyze import analyze

        rep = analyze([dict(e) for e in MOCK_IMPROVE_EVENTS],
                      [dict(e) for e in MOCK_IMPROVE_LEDGER])  # fmt: skip
        return {k: rep[k] for k in ("proposals", "events", "method", "blind_spots")}

    # GET /api/mcp (v1.13): the fixture discovery, classified by mcp_intake itself
    def mcp(self, refresh: bool = False) -> dict[str, Any]:
        with self.lock:
            if refresh or self.mcp_refreshed is None:
                self.mcp_refreshed = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            at = self.mcp_refreshed
        intake = McpIntake("claude", ".", discover_fn=lambda *a, **k: {
            "servers": parse_init(MOCK_MCP_INIT), "refreshed_at": at})  # fmt: skip
        return intake.view(refresh)

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
            out["app"]["session"] = True
            out["changes"] = []
            for c in self.changes.values():
                item = {
                    "id": c["id"],
                    "request": c["request"],
                    "status": c["status"],
                    "parent": c.get("parent"),
                    "superseded_by": c.get("superseded_by"),
                }
                if c.get("source"):
                    item["source"] = dict(c["source"])
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
        source = parse_source(body.get("source"))  # SourceError: a 422
        with self.lock:
            cid = f"c{self._next_id}"
            self._next_id += 1
            parent = body.get("parent")
            parent = str(parent) if parent else None
            if source is None and parent and (self.changes.get(parent) or {}).get("source"):
                source = dict(self.changes[parent]["source"])
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
                "source": source,
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
                    "source": c.get("source"),
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
            full["source"] = c.get("source")
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
        for name in FLOW_FIXTURES:
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
                "stages": with_stage_links(fixture["stages"]),
                "divergence": divergence,
            }

    # GET /api/graph?focus=&kinds=&depth=&limit=
    def graph(
        self, focus: str | None, kinds: set[str] | None, depth: int, limit: int
    ) -> dict[str, Any] | None:
        """The graph payload; ``None`` when ``focus`` names no node."""
        flows = [self._load(n) for n in FLOW_FIXTURES]
        with self.lock:
            built = [
                c
                for c in self.changes.values()
                if c["status"] == "accepted"
                and (c.get("build") or {}).get("state", "not_built") != "built"
            ]
            not_built: set[str] = set()
            for c in built:
                rl = self._load(c.get("fixture", "change-c1")).get("rules", {})
                for x in [*rl.get("added", []), *rl.get("changed", [])]:
                    not_built.add(x if isinstance(x, str) else x.get("new") or x["old"])
            not_built &= {r["id"] for r in self.model["rules"]}
            ready = [c for c in self.changes.values() if c["status"] == "ready"]
            open_change = None
            if ready:
                c = max(ready, key=lambda c: int(c["id"][1:]) if c["id"][1:].isdigit() else 0)
                open_change = (c["id"], self._load(c.get("fixture", "change-c1")))
            g = build_graph(self.model, self.code, flows, not_built)
        if focus is not None:
            focus = resolve_focus(g, focus)
            if focus is None:
                return None
        out = select_graph(g, focus, depth, kinds, limit)
        out["focus"] = focus
        out["depth"] = depth if focus else None
        out["change"] = open_change[0] if open_change else None
        out["diff"] = graph_diff(open_change[1] if open_change else None, g)
        out["machines"] = g["machines"]
        return out

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
            branch = f"rebuild/{ids[0]}"
            n = 2
            while any(r.get("branch") == branch for r in self.rebuilds.values()):
                branch, n = f"rebuild/{ids[0]}-{n}", n + 1
            self.rebuilds[rid] = {
                "id": rid,
                "branch": branch,
                "base": self.model["project"]["git_head"],
                "worktree": f"{self.model['project']['root']}/.lattice/rebuilds/"
                f"{self.model['project']['name']}-{rid}",
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
                self.changes[cid]["build"] = {
                    "state": "rebuilding",
                    "rebuild_id": rid,
                    "branch": branch,
                }
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
            build = {"state": state, "rebuild_id": rec["id"]}
            if status != "stopped":
                build["branch"] = rec["branch"]
            self.changes[cid]["build"] = build
        if status != "passed":
            return
        fx = rec["fixture"]
        if rec.get("scope"):
            sha = hashlib.sha1(f"build:{rec['id']}:{rec['scope']['id']}".encode()).hexdigest()
            rec["commit"] = {"sha": sha, "message": fx["commit"]["message"],
                             "files_changed": list(fx["commit"]["files_changed"])}  # fmt: skip
            name = rec["target_branch"]
            before = self._failing(name)
            if before is None:
                before = {f["target"] for f in rec["baseline"]["findings"]}
            self.branch_failing[name] = set(before) - set(rec["scope_targets"])
            self.branch_heads[name] = sha[:7]
            return
        requests = "; ".join(self.changes[c]["request"] for c in rec["changes"])
        sha = hashlib.sha1(f"rebuild:{rec['id']}:{requests}".encode()).hexdigest()
        for cid in rec["changes"]:
            self.changes[cid]["build"]["commit"] = sha
        rec["commit"] = {
            "sha": sha,
            "message": f"build: {requests}" if requests else fx["commit"]["message"],
            "files_changed": list(fx["commit"]["files_changed"]),
        }
        # the commit is on the rebuild branch: git_head (the owner's HEAD) does not move
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
        if rec.get("scope"):
            out["scope"] = rec["scope"]
            out["baseline"] = rec["baseline"]
            for item, r in zip(out["rounds"], fx["rounds"], strict=False):
                if item["check"] is not None:
                    for k in ("scope_passing", "scope_total", "regressions", "newly_fixed"):
                        item[k] = copy.deepcopy(r[k])
            tb = rec["target_branch"]
            out.update(target_branch=tb, created_branch=rec["created_branch"],
                       base_ref=rec["base_ref"], integration_branch=tb,
                       integration_base=rec["base"], owner_head=self.model["project"]["git_head"],
                       merged_head=None, metrics=None, warnings=[],
                       commit_range={"from": rec["base"],
                                     "to": rec["commit"]["sha"] if status == "passed" else None},
                       usage={"cost_usd": 0.0, "cost_usd_list_estimate": 0.0, "duration_ms": 0,
                              "input_tokens": 0, "output_tokens": 0,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                              "wall_s": 0.0})  # fmt: skip
        out["branch"] = rec["branch"] if status != "stopped" else None
        out["base"] = rec["base"]
        out["worktree"] = rec["worktree"] if status != "stopped" else None
        out["merge_hint"] = None
        if status == "passed" and rec.get("scope"):
            out["commit"] = rec["commit"]
            out["merge_hint"] = (
                f"The build is on {rec['target_branch']} (now at {rec['commit']['sha'][:10]}) "
                f"and {rec['branch']}; Studio never merges into your branch: to take it, run "
                f"`git merge {rec['target_branch']}` in {self.model['project']['root']}."
            )
        elif status == "passed":
            out["commit"] = rec["commit"]
            out["merge_hint"] = (
                f"Studio never merges. To take the rebuilt code, run `git merge {rec['branch']}` "
                f"in {self.model['project']['root']}; the worktree {rec['worktree']} stays for "
                "review."
            )
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

    # ---------------------------------------------------------------- build from spec

    def _trace(self) -> dict[str, Any]:
        rules = self.model["rules"]
        return {
            "rules": [{"id": r["id"], "text": r["text"], "section": r.get("section", "")}
                      for r in rules],
            "links": {r["id"]: list(r.get("links") or []) for r in rules},
        }  # fmt: skip

    def default_target(self) -> str:
        return f"build/{areas_mod.slug(self.model['project']['name'], 60)}"

    def _failing(self, branch: str) -> set[str] | None:
        if branch in self.branch_failing:
            return self.branch_failing[branch]
        last = self.model["app"].get("last_check") or {}
        if branch == "main" and last.get("targets"):
            return {t for t, v in last["targets"].items() if v == "fail"}
        return None

    # GET /api/areas
    def areas(self, branch: str | None) -> list[dict[str, Any]]:
        target = branch or self.default_target()
        with self.lock:
            failing = self._failing(target)
            building = {
                r["scope"]["id"]
                for r in self.rebuilds.values()
                if r.get("scope") and r.get("target_branch") == target
                and r["status"] not in REBUILD_FINISHED
            }  # fmt: skip
            out = []
            for a in areas_mod.derive_areas(self._trace()):
                v = areas_mod.area_view(a, failing, building=a["id"] in building)
                v["target_branch"] = target
                out.append(v)
            return out

    # GET /api/branches
    def branches(self) -> dict[str, Any]:
        with self.lock:
            default = self.default_target()
            used = {r.get("target_branch") for r in self.rebuilds.values()}
            return {
                "branches": [
                    {"name": n, "head": h, "current": n == "main", "protected": n == "main",
                     "checked_out": self.model["project"]["root"] if n == "main" else None,
                     "ahead_of_base": 0 if n == "main" else 1,
                     "is_integration": n == default or n in used,
                     "spec": None if n == "main" else {
                         "in_sync": not self.spec_behind.get(n),
                         "files": list(self.spec_behind.get(n) or []),
                         "head": self.branch_heads["main"]}}
                    for n, h in sorted(self.branch_heads.items())
                ],
                "current_branch": "main",
                "protected": ["main", "master"],
                "default_target": default,
            }  # fmt: skip

    # spec sync (API v1.12)
    def spec_drift(self, name: str) -> dict[str, Any] | None:
        files = self.spec_behind.get(name)
        return {"head": self.branch_heads["main"], "files": list(files)} if files else None

    def drift_warnings(self, rec: dict[str, Any]) -> None:
        """A plan record's ``spec_drift`` and its warning, as the repository is now."""
        drift = self.spec_drift(str(rec.get("target_branch") or ""))
        rec["spec_drift"] = drift
        rec["warnings"] = [w for w in rec.get("warnings") or []
                           if not str(w).startswith(SPEC_DRIFT_WARNING)]  # fmt: skip
        if drift:
            rec["warnings"].append(f"{SPEC_DRIFT_WARNING}main differs from {rec['target_branch']} "
                                   f"in {', '.join(drift['files'])}: sync it first")  # fmt: skip

    def sync_spec(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/branches/{name}/sync-spec``: the real server's refusals, in its order;
        a sync moves the branch to a fake commit and clears its drift."""
        if body:
            raise MockRefusal(422, f"unknown field(s): {', '.join(sorted(body))}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", name) or ".." in name \
                or name.endswith(("/", ".lock")):  # fmt: skip
            raise MockRefusal(422, f"{name!r} is not a valid branch name", "branch_name_invalid")
        with self.lock:
            if name in ("main", "master"):
                raise MockRefusal(409, f"branch {name} is protected; Studio never syncs the "
                                  "spec onto a protected branch", "branch_protected")  # fmt: skip
            if name not in self.branch_heads:
                raise MockRefusal(404, f"no branch {name}", "no_branch")
            busy = next(
                (r["id"] for r in self.rebuilds.values() if r["status"] not in REBUILD_FINISHED),
                None,
            ) or next(
                (p["id"] for p in self.plans.plans.values() if p["status"] == "running"), None
            )
            if busy:
                raise MockRefusal(409, f"a build is running in this project ({busy}); sync the "
                                  "spec when it has finished", "build_running")  # fmt: skip
            head = self.branch_heads["main"]
            files = self.spec_behind.get(name)
            if not files:
                raise MockRefusal(409, f"the spec on {name} is the same as on main at "
                                  f"{head[:7]}: nothing to sync", "nothing_to_sync")  # fmt: skip
            prev = self.branch_heads[name]
            new = hashlib.sha1(f"sync {name} {prev} {head}".encode()).hexdigest()[:7]
            self.branch_heads[name] = new
            del self.spec_behind[name]
            return {"branch": name, "synced_from": head, "files": list(files), "commit": new,
                    "previous_tip": prev}  # fmt: skip

    # POST /api/rebuild with a scope. Raises MockRefusal.
    def start_scoped(self, body: dict[str, Any]) -> dict[str, str]:
        scope_raw = body.get("scope")
        trace = self._trace()
        try:
            scope = areas_mod.resolve_scope(scope_raw, areas_mod.derive_areas(trace), trace)
        except areas_mod.ScopeError as e:
            raise MockRefusal(e.status, e.detail) from None
        spec = body.get("branch")
        confirm = body.get("confirm_unsafe_branch") is True
        with self.lock:
            if any(r["status"] not in REBUILD_FINISHED for r in self.rebuilds.values()):
                busy = next(r["id"] for r in self.rebuilds.values()
                            if r["status"] not in REBUILD_FINISHED)  # fmt: skip
                raise MockRefusal(409, f"a rebuild is already running in this project ({busy})")
            name = self.default_target() if spec is None else (spec or {}).get("name")
            create = None if spec is None else bool((spec or {}).get("create", False))
            if not isinstance(name, str) or not name or ".." in name or " " in name:
                raise MockRefusal(422, f"{name!r} is not a valid branch name",
                                  "branch_name_invalid")  # fmt: skip
            exists = name in self.branch_heads
            if create is None:
                create = not exists
            if create and exists:
                raise MockRefusal(409, f"branch {name} already exists", "branch_exists")
            if not create and not exists:
                raise MockRefusal(404, f"no branch {name}", "no_branch")
            if name in ("main", "master") and not confirm:
                raise MockRefusal(409, f"branch {name} is protected; send "
                                  "confirm_unsafe_branch: true to build onto it "
                                  "(fast-forward only, with a clean worktree)",
                                  "branch_protected")  # fmt: skip
            failing = self._failing(name)
            targets = list(scope["targets"])
            if failing is None:  # no results yet: a new project, every check fails
                failing = {t for a in areas_mod.derive_areas(trace) for t in a["targets"]}
            in_scope = [t for t in targets if t in failing]
            if not in_scope:
                raise MockRefusal(409, "nothing to build: the checks in this scope already pass")
            tip = self.branch_heads.get(name, self.branch_heads["main"])
            rid = f"b{self._next_rebuild}"
            self._next_rebuild += 1
            slug = areas_mod.scope_slug(scope)
            n = 1
            while any(r.get("branch") == f"rebuild/{slug}-{n}" for r in self.rebuilds.values()):
                n += 1
            k = len(in_scope)
            findings = [{"id": f"f{i}", "title": f"{t.split(':', 1)[-1]} step 1: not built yet",
                         "target": t, "in_scope": True, "regression": False}
                        for i, t in enumerate(in_scope, 1)]  # fmt: skip
            others = sorted(failing - set(in_scope))
            fx = {
                "model": body.get("model") or "sonnet",
                "max_rounds": body.get("max_rounds") or 3,
                "rounds": [
                    {"n": 1, "offset_s": 0, "files_changed": ["internal/app/area.go"],
                     "agent_summary": f"Started {scope['title']}: handlers in place, checks "
                     "still failing.",
                     "check": {"verdict": "fail", "counts": {"D": k}, "findings": findings},
                     "scope_passing": 0, "scope_total": k, "regressions": [],
                     "newly_fixed": []},
                    {"n": 2, "offset_s": 64,
                     "files_changed": ["internal/app/area.go", "internal/app/area_test.go"],
                     "agent_summary": f"Finished {scope['title']}; added tests.",
                     "check": {"verdict": "pass", "counts": {"D": 0}, "findings": []},
                     "scope_passing": k, "scope_total": k, "regressions": [],
                     "newly_fixed": in_scope},
                ],
                "targets": [{"id": t, "label": f"{t} passes", "done_in_round": 2}
                            for t in in_scope],
                "commit": {"message": f"build: {scope['title']}",
                           "files_changed": ["internal/app/area.go", "internal/app/area_test.go"]},
            }  # fmt: skip
            self.rebuilds[rid] = {
                "id": rid,
                "branch": f"rebuild/{slug}-{n}",
                "base": tip,
                "worktree": f"{self.model['project']['root']}/.lattice/rebuilds/"
                f"{self.model['project']['name']}-{rid}",
                "changes": [],
                "status": "queued",
                "model": fx["model"],
                "max_rounds": fx["max_rounds"],
                "started": datetime.now(UTC),
                "polls": 0,
                "frozen": None,
                "fixture": fx,
                "scope": {k2: scope[k2] for k2 in ("kind", "id", "title", "rules")},
                "scope_targets": in_scope,
                "target_branch": name,
                "created_branch": bool(create),
                "base_ref": (spec or {}).get("from", "HEAD") if create else name,
                "baseline": {
                    "verdict": "fail",
                    "counts": {"D": len(failing)},
                    "findings": [
                        {"id": f"f{i}", "title": f"{t.split(':', 1)[-1]}: not built yet",
                         "target": t, "rules": [], "in_scope": t in in_scope}
                        for i, t in enumerate(in_scope + others, 1)
                    ],
                    "unmapped": [],
                },
            }  # fmt: skip
            if create:
                self.branch_heads[name] = tip
            return {"id": rid}

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


MOCK_PLAN_DEFAULTS = {
    "max_parallel": 2,
    "models": dict(plan_mod.DEFAULT_MODELS),
    "scoped_gate": ["--layers", "CD", "--x-repeats", "1", "--await-cap", "5"],
    "level_gate": "regression",
    "level_gate_args": None,
    "node_timeout": 1800.0,
    "level_repair_attempts": 2,
}


class MockPlans:
    """API v1.9 build plans over the fixture model (see the module docstring)."""

    def __init__(self, state: MockState) -> None:
        self.state = state
        self.plans: dict[str, dict[str, Any]] = {}
        self._next = 1
        self._builds = 1

    def _areas(self) -> list[dict[str, Any]]:
        return areas_mod.derive_areas(self.state._trace())

    def _agent_doc(self, scope: dict[str, Any]) -> dict[str, Any]:
        areas = [a for a in self._areas() if a["id"] in scope["areas"]]
        keep = set(scope["rules"])
        tables = (self.state.model.get("data_model") or {}).get("tables") or []
        texts = {r["id"]: r.get("text", "") for r in self.state.model["rules"]}
        nodes: list[dict[str, Any]] = []
        infra = [a for a in areas if plan_mod.is_infra(a["title"])]
        rest = [a for a in areas if not plan_mod.is_infra(a["title"])]
        none = {"tables": [], "packages": [], "files": []}
        if infra:
            nodes.append(
                {
                    "id": "scaffold",
                    "title": "Shared infrastructure",
                    "kind": "scaffold",
                    "scope": {"rules": [r for a in infra for r in a["rules"] if r in keep]},
                    "depends_on": [],
                    "tier": "mid",
                    "touches": {
                        "tables": [],
                        "packages": ["internal/app"],
                        "files": ["internal/app/router.go"],
                    },
                    "why": "router, config and error format the areas plug into",
                }
            )
        auth = next(
            (a for a in rest if re.search(r"account|auth|token|session", a["title"], re.I)), None
        )
        order = ([auth] if auth else []) + [a for a in rest if a is not auth]
        for a in order:
            rules = [r for r in a["rules"] if r in keep]
            if not rules:
                continue
            joined = " ".join(texts.get(r, "") for r in rules)
            touch = {
                "tables": plan_mod._area_tables(joined, tables)[:3],
                "packages": [f"internal/{a['id']}"],
                "files": [],
            }
            deps = (
                []
                if a is auth or auth is None
                else [{"id": auth["id"], "why": "needs a signed-in user"}]
            )
            tier = "frontier" if a is auth else "mid"
            parts = (
                [rules]
                if len(rules) <= plan_mod.SPLIT_RULES
                else [rules[: len(rules) // 2], rules[len(rules) // 2 :]]
            )
            for i, part in enumerate(parts, 1):
                nid = a["id"] if len(parts) == 1 else f"{a['id']}-{i}"
                nodes.append(
                    {
                        "id": nid,
                        "title": a["title"] + ("" if len(parts) == 1 else f" ({i}/{len(parts)})"),
                        "kind": "area",
                        "scope": {"area": a["id"]}
                        if len(parts) == 1 and len(part) == len(a["rules"])
                        else {"rules": part},
                        "depends_on": deps,
                        "tier": tier,
                        "touches": touch
                        if i == 1
                        else {**none, "packages": [f"internal/{a['id']}/part{i}"]},
                        "why": f"builds {a['title']}",
                    }
                )
        nodes.append(
            {
                "id": "integration",
                "title": "Integration: the full gate",
                "kind": "integration",
                "scope": "all",
                "depends_on": [],
                "tier": "frontier",
                "touches": none,
                "why": "the full gate",
            }
        )
        return {"nodes": nodes}

    def _validate(self, rec: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
        trace = self.state._trace()
        return plan_mod.validate_plan(
            doc,
            scope=rec["_scope"],
            areas=self._areas(),
            max_parallel=rec["max_parallel"],
            models=rec["models"],
            model_overrides=rec["_overrides"],
            trace_links=trace["links"],
        )

    def _apply(self, rec: dict[str, Any], v: dict[str, Any]) -> None:
        rec["version"] += 1
        rec["validation"] = {k: v[k] for k in ("ok", "issues", "auto_edits")}
        rec["levels"], rec["edges"] = v["levels"], v["edges"]
        rec["nodes"] = [
            {
                **n,
                "status": "pending",
                "build_id": None,
                "builds": [],
                "branch": None,
                "commit": None,
                "merged": False,
                "nothing_to_build": False,
                "blocked_by": [],
                "usage": plan_mod._node_usage({}),
                "wall_s": 0.0,
                "rounds": 0,
                "lines_changed": 0,
                "files_changed": [],
                "started_ts": None,
                "finished_ts": None,
                "error": None,
                "metrics": None,
                "attempts": 0,
                "timeline": [],
            }
            for n in v["nodes"]
        ]

    def create(self, body: dict[str, Any]) -> dict[str, str]:
        try:
            scope = plan_mod.resolve_plan_scope(body.get("scope"), self._areas())
        except plan_mod.PlanError as e:
            raise MockRefusal(e.status, e.detail) from None
        mp = body.get("max_parallel", 2)
        if isinstance(mp, bool) or not isinstance(mp, int) or not 1 <= mp <= 8:
            raise MockRefusal(422, "max_parallel must be 1 to 8")
        planner = body.get("planner", "agent")
        if planner not in ("agent", "fallback"):
            raise MockRefusal(422, 'planner must be "agent" or "fallback"')
        models = dict(plan_mod.DEFAULT_MODELS)
        if isinstance(body.get("models"), dict):
            models.update({k: str(v) for k, v in body["models"].items() if k in models})
        spec = body.get("branch")
        name = self.state.default_target() if spec is None else (spec or {}).get("name")
        if not isinstance(name, str) or not name or ".." in name or " " in name:
            raise MockRefusal(422, f"{name!r} is not a valid branch name", "branch_name_invalid")
        if name in ("main", "master") and body.get("confirm_unsafe_branch") is not True:
            raise MockRefusal(
                409,
                f"branch {name} is protected; send confirm_unsafe_branch: "
                "true to build onto it (fast-forward only, with a clean "
                "worktree)",
                "branch_protected",
            )
        with self.state.lock:
            pid = f"p{self._next}"
            self._next += 1
            self.plans[pid] = {
                "id": pid,
                "project": self.state.model["project"]["name"],
                "status": "planning",
                "source": None,
                "fallback_reason": None,
                "version": 0,
                "scope": {k: scope[k] for k in ("kind", "areas", "rules", "title")},
                "target_branch": name,
                "branch": spec,
                "created_branch": False,
                "max_parallel": mp,
                "models": models,
                "planner": {
                    "kind": planner,
                    "model": "opus",
                    "attempts": 1,
                    "usage": plan_mod._node_usage(
                        {
                            "input_tokens": 18000,
                            "output_tokens": 2400,
                            "cost_usd_list_estimate": 0.41,
                        }
                    ),
                    "wall_s": 41.0,
                    "error": None,
                },
                "nodes": [],
                "edges": [],
                "levels": [],
                "validation": {"ok": False, "issues": [], "auto_edits": []},
                "level_gate": plan_mod.LEVEL_GATE,
                "level_runs": [],
                "baseline": None,
                "runs": 0,
                "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "approved": None,
                "started": None,
                "finished": None,
                "target_tip": None,
                "owner_head": self.state.model["project"]["git_head"],
                "error": None,
                "warnings": [],
                "metrics": None,
                "totals": plan_mod._usage_totals(),
                "planner_usage": {
                    "tokens_in": 18000,
                    "tokens_out": 2400,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cost_usd_list_estimate": 0.41,
                    "wall_s": 41.0,
                    "calls": 1,
                },  # fmt: skip
                "repair_attempts": 0,
                "attempts": 0,
                "timeline": [
                    {"at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "event": "planning"}
                ],
                "interrupted": False,
                "interrupted_at": None,
                "interrupted_reason": None,
                "anchors": {},
                "pids": [],
                "spec_drift": None,
                "_checked_out": None,
                "_scope": scope,
                "_planner": planner,
                "_overrides": {},
                "_doc": None,
                "_cursor": None,
            }
            self.state.drift_warnings(self.plans[pid])
            return {"id": pid, "status": "planning"}

    def _view(self, rec: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy({k: v for k, v in rec.items() if not k.startswith("_")})
        out["resume"] = plan_mod.resume_view(
            out, checked_out_at=rec.get("_checked_out"),
            owner_worktree=self.state.model["project"]["root"], fast_forward=True,
            repair_attempts=MOCK_PLAN_DEFAULTS["level_repair_attempts"],
        )  # fmt: skip
        return out

    # -- API v1.10: fixtures, resume and land
    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _finish_node(self, rec: dict[str, Any], n: dict[str, Any], now: float) -> None:
        k = len(n["scope"]["rules"]) or 1
        sha = hashlib.sha1(f"{rec['id']}:{n['id']}".encode()).hexdigest()
        n.update(
            status="passed",
            commit=sha,
            finished_ts=now + 60 * k,
            rounds=n["rounds"] + 2,
            wall_s=round(n["wall_s"] + float(60 * k), 2),
            lines_changed=n["lines_changed"] + 40 * k,
            files_changed=[f"internal/{n['id']}/handlers.go"],
        )
        plan_mod._sum_into(n["usage"], plan_mod._node_usage({
            "input_tokens": 9000 * k, "output_tokens": 1500 * k,
            "cache_read_input_tokens": 60000 * k, "cost_usd_list_estimate": 0.12 * k}))  # fmt: skip
        rec["anchors"][n["id"]] = sha

    def _level_run(self, rec: dict[str, Any], L: int, ids: list[str]) -> dict[str, Any]:
        lr = {"level": L, "run": rec["runs"], "base": rec["target_tip"], "nodes": list(ids),
              "status": "running", "merged": [], "merge_conflicts": [], "tip": None,
              "gate": None, "regressions": [], "blame": [], "started": self._now(),
              "finished": None}  # fmt: skip
        rec["level_runs"].append(lr)
        return lr

    def _gate(self, rec: dict[str, Any], L: int, lr: dict[str, Any], ids: list[str]) -> str:
        tip = hashlib.sha1(f"{rec['id']}:L{L}:{rec['runs']}".encode()).hexdigest()
        lr.update(merged=list(ids), tip=tip, gated=True, finished=self._now(),
                  gate={"sha": tip, "verdict": "fail", "counts": {"D": 0}, "failing": [],
                        "regressions": [], "newly_fixed": [], "gates": [],
                        "mode": "regression", "selected": 12,
                        "in_scope_failing": []})  # fmt: skip
        rec["anchors"][f"L{L}"] = tip
        return tip

    def _land(self, rec: dict[str, Any], lr: dict[str, Any], by: str) -> None:
        nodes = {n["id"]: n for n in rec["nodes"]}
        for nid in lr["merged"]:
            nodes[nid].update(merged=True, status="passed", error=None)
        if lr.get("error"):
            lr["land_error"] = lr.pop("error")
        lr.update(status="passed", landed=True, landed_at=self._now(), landed_by=by)
        rec["target_tip"] = lr["tip"]
        self.state.branch_heads[rec["target_branch"]] = lr["tip"]
        rec["timeline"].append({"at": self._now(), "event": f"level {lr['level']} landed on "
                                f"{rec['target_branch']} at {lr['tip'][:10]} ({by})"})  # fmt: skip

    def seed_fixtures(self) -> None:
        """Four plans of the second project (see the module docstring): ``p1`` interrupted
        while level 2 ran, ``p2`` waiting to land (target checked out by the owner), ``p3``
        out of repair attempts, ``p4`` with a node that gave up and blocked the rest."""
        now = datetime.now(UTC).timestamp()
        for kind in ("interrupted", "land", "repair", "retry"):
            pid = self.create({"scope": "all"})["id"]
            rec = self.plans[pid]
            self._plan_now(rec)
            levels = rec["levels"]
            stop_at = min(2, len(levels) - 1) if kind == "interrupted" else min(1, len(levels) - 1)
            rec["gate_config"] = {"scoped_gate_args": MOCK_PLAN_DEFAULTS["scoped_gate"],
                                  "level_gate": "regression", "level_repair_attempts": 2,
                                  "node_timeout": 1800.0, "node_max_tokens": None}  # fmt: skip
            base = self.state.branch_heads["main"]
            rec.update(approved=self._now(), runs=1, attempts=1, started=self._now(),
                       target_tip=base)  # fmt: skip
            rec["baseline"] = {"sha": base, "verdict": "fail", "counts": {"D": 0},
                               "failing": [], "findings": []}  # fmt: skip
            nodes = {n["id"]: n for n in rec["nodes"]}
            for L in range(stop_at):
                lr = self._level_run(rec, L, levels[L])
                for nid in levels[L]:
                    n = nodes[nid]
                    n.update(build_id=f"b{100 + self._builds}", attempts=1,
                             branch=f"rebuild/{pid}-{nid}", started_ts=now)  # fmt: skip
                    n["builds"] = [n["build_id"]]
                    self._builds += 1
                    self._finish_node(rec, n, now)
                self._gate(rec, L, lr, levels[L])
                self._land(rec, lr, "run")
            ids = levels[stop_at]
            lr = self._level_run(rec, stop_at, ids)
            for nid in ids:
                n = nodes[nid]
                n.update(build_id=f"b{100 + self._builds}", attempts=1,
                         branch=f"rebuild/{pid}-{nid}", started_ts=now)  # fmt: skip
                n["builds"] = [n["build_id"]]
                self._builds += 1
            if kind == "interrupted":
                for nid in ids:
                    nodes[nid]["status"] = "interrupted"
                lr.update(status="interrupted", finished=self._now())
                rec.update(status="interrupted", interrupted=True, interrupted_at=self._now(),
                           interrupted_reason="studio_restarted", finished=self._now())  # fmt: skip
                rec["timeline"].append(
                    {"at": self._now(), "event": "interrupted while running: Studio restarted"}
                )
            elif kind == "repair":
                for nid in ids:
                    self._finish_node(rec, nodes[nid], now)
                self._repair_exhausted(rec, stop_at, lr, ids)
            elif kind == "retry":
                self._gave_up(rec, stop_at, lr, ids, now)
            else:
                err = {
                    "kind": "branch_checked_out",
                    "message": f"{rec['target_branch']} was "
                    f"checked out in {self.state.model['project']['root']} during the build",
                }
                for nid in ids:
                    self._finish_node(rec, nodes[nid], now)
                    nodes[nid].update(status="regressed", error=err)
                self._gate(rec, stop_at, lr, ids)
                lr.update(status="failed", error=err, landed=False)
                rec.update(status=plan_mod.final_status(rec), finished=self._now(),
                           _checked_out=self.state.model["project"]["root"])  # fmt: skip
            tot = plan_mod._usage_totals()
            for n in rec["nodes"]:
                plan_mod._sum_into(tot, n["usage"])
                tot["rounds"] += n["rounds"]
                tot["lines_changed"] += n["lines_changed"]
            plan_mod._sum_into(tot, rec["planner"]["usage"])  # v1.11: the planner is in
            tot["wall_s"] = round(sum(n["wall_s"] for n in rec["nodes"]), 2)
            rec["totals"] = tot

    def _gave_up(self, rec: dict[str, Any], L: int, lr: dict[str, Any], ids: list[str],
                 now: float) -> None:  # fmt: skip
        """Level ``L``: its first node gave up, the others passed and landed, every later node
        is blocked by it (``resume.kind: "retry"``)."""
        nodes = {n["id"]: n for n in rec["nodes"]}
        bad = ids[0]
        for nid in ids[1:]:
            self._finish_node(rec, nodes[nid], now)
        err = {"kind": "gave_up", "message": "its checks still fail after 4 rounds: "
               f"scenario:{bad}_rejects_a_duplicate"}  # fmt: skip
        nodes[bad].update(status="gave_up", error=err, rounds=4, wall_s=240.0)
        if len(ids) > 1:
            self._gate(rec, L, lr, ids[1:])
            self._land(rec, lr, "run")
        else:
            lr.update(status="failed", finished=self._now())
        for later in rec["levels"][L + 1 :]:
            for nid in later:
                nodes[nid].update(status="blocked", blocked_by=[bad])
        rec.update(status=plan_mod.final_status(rec), finished=self._now())

    def _repair_entry(self, rec: dict[str, Any], L: int, k: int, ids: list[str],
                      tip: str, outcome: str | None,
                      repair_node: str | None = None) -> dict[str, Any]:  # fmt: skip
        nodes = {n["id"]: n for n in rec["nodes"]}
        first = ids[0]
        owners = [first] + ([repair_node] if repair_node else [])
        targets = [f"scenario:{first}_works_with_the_others"]
        attributed = {targets[0]: {"by": "scope", "nodes": [first]}}
        if repair_node:  # a failing check no merged node owns goes to the repair node
            targets.append(f"scenario:{first}_cross_area")
            attributed[targets[1]] = {"by": "repair_node", "nodes": [repair_node]}
        rec["repair_attempts"] = int(rec.get("repair_attempts") or 0) + 1
        entry_nodes = []
        for nid in owners:
            n = nodes[nid]
            n["repairs"] = int(n.get("repairs") or 0) + 1
            n["rounds"] += 1
            use = plan_mod._node_usage({
                "input_tokens": 3000, "output_tokens": 600, "cache_read_input_tokens": 20000,
                "cost_usd_list_estimate": 0.05})  # fmt: skip
            plan_mod._sum_into(n["usage"], use)
            n.setdefault("repair_usage", plan_mod._node_usage({}))
            plan_mod._sum_into(n["repair_usage"], use)
            n.setdefault("timeline", []).append(
                {"at": self._now(), "repair": True, "event": f"node {nid}: repair attempt {k}"}
            )
            entry_nodes.append({"id": nid, "build_id": n.get("build_id"),
                                "verify": "fail" if nid == repair_node and outcome != "passed"
                                else "pass",
                                "commit": hashlib.sha1(
                                    f"{rec['id']}:{nid}:r{k}".encode()).hexdigest(),
                                "rounds": 1, "usage": use})  # fmt: skip
        rec["timeline"].append({"at": self._now(), "repair": True,
                                "event": f"level {L}: repair attempt {k} of 2: "
                                f"{', '.join(owners)} on {', '.join(targets)}"})  # fmt: skip
        return {"attempt": k, "of": 2, "run": rec["runs"], "at": self._now(), "tip": tip,
                "targets": targets, "attributed": attributed, "nodes": entry_nodes,
                "outcome": outcome}  # fmt: skip

    def _repair_node(self, rec: dict[str, Any], L: int, targets: list[str]) -> dict[str, Any]:
        """A dedicated ``repair-l<L>`` node (v1.11) for checks no merged node owns."""
        n = {
            "id": f"repair-l{L}",
            "title": f"Repair of level {L}",
            "kind": "repair",
            "scope": {"kind": "rules", "rules": []},
            "depends_on": [],
            "why": "checks that fail only in the merged level and no node owns",
            "tier": "frontier",
            "model": rec["models"]["frontier"],
            "touches": {"tables": [], "packages": [], "files": []},
            "gate": {"kind": "scoped", "checks": list(targets), "decided_by": "studio"},
            "level": L,
            "status": "gave_up",
            "builds": [],
            "branch": None,
            "commit": None,
            "merged": False,
            "nothing_to_build": False,
            "blocked_by": [],
            "usage": plan_mod._node_usage({}),
            "wall_s": 0.0,
            "rounds": 0,
            "lines_changed": 0,
            "files_changed": [],
            "started_ts": None,
            "finished_ts": None,
            "error": None,
            "metrics": None,
            "attempts": 1,
            "timeline": [],
            "repair": True,
            "build_id": f"b{100 + self._builds}",
        }
        n["error"] = {"kind": "repair", "message": "the repair node could not make "
                      f"{', '.join(targets)} pass"}  # fmt: skip
        self._builds += 1
        n["builds"] = [n["build_id"]]
        n["branch"] = f"rebuild/{rec['id']}-{n['id']}"
        rec["nodes"].append(n)
        rec["levels"][L].append(n["id"])
        return n

    def _repair_exhausted(self, rec: dict[str, Any], L: int, lr: dict[str, Any],
                          ids: list[str]) -> None:  # fmt: skip
        """A level whose gate failed on the merged tip after 2 repair attempts (v1.11)."""
        ids = list(ids)  # the repair node joins the level's list below
        nodes = {n["id"]: n for n in rec["nodes"]}
        tip = hashlib.sha1(f"{rec['id']}:L{L}:failed".encode()).hexdigest()
        targets = [f"scenario:{ids[0]}_works_with_the_others", f"scenario:{ids[0]}_cross_area"]
        lr.update(merged=list(ids), tip=None, finished=self._now(), status="failed",
                  regressions=list(targets), repair_pending=True,
                  blame=[{"target": targets[0], "nodes": [ids[0]], "by": "rules"},
                         {"target": targets[1], "nodes": [], "by": "level"}],
                  gate={"sha": tip, "verdict": "fail", "counts": {"D": 2},
                        "failing": list(targets), "regressions": list(targets),
                        "newly_fixed": [], "gates": [], "mode": "regression",
                        "selected": 12, "in_scope_failing": []})  # fmt: skip
        repair = self._repair_node(rec, L, targets[1:])  # no merged node owns the second
        lr["repairs"] = [
            self._repair_entry(rec, L, k, ids, tip, "failed", repair["id"]) for k in (1, 2)
        ]
        for r in lr["repairs"]:
            r["still_failing"] = list(targets)
        err = {"kind": "repair_exhausted", "level": L, "nodes": list(ids),
               "targets": list(targets),
               "message": f"level {L} still fails after 2 repair attempt(s) (2 in all): "
               f"{', '.join(targets)}; nodes {', '.join(ids)}"}  # fmt: skip
        lr["error"] = err
        for nid in ids:
            nodes[nid].update(
                status="regressed",
                error={
                    "kind": "regression",
                    "message": f"the full gate on the merged tip regressed: {targets[0]}",
                },
            )
        rec.update(status="partial", error=err, finished=self._now())

    def land(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        with self.state.lock:
            rec = self._rec(pid)
            if rec["status"] in ("planning", "running"):
                raise MockRefusal(409, f"plan {pid} is {rec['status']}")
            lw = plan_mod.land_waiting(rec)
            if lw is None:
                raise MockRefusal(409, f"nothing is waiting to land in plan {pid}",
                                  "nothing_to_land")  # fmt: skip
            if rec.get("_checked_out") and body.get("confirm_unsafe_branch") is not True:
                raise MockRefusal(409, f"{rec['target_branch']} is checked out in your working "
                                  f"copy {rec['_checked_out']}; switch it away and press Land (or "
                                  "send confirm_unsafe_branch: true to fast-forward it there, "
                                  "with a clean worktree)", "branch_checked_out")  # fmt: skip
            real = next(x for x in reversed(rec["level_runs"]) if x["level"] == lw["level"])
            self._land(rec, real, "resume")
            rec["_checked_out"] = None
            rec["status"] = plan_mod.final_status(rec)
            return {"id": pid, "status": rec["status"], "landed": True, "remerge": False,
                    "target_tip": rec["target_tip"]}  # fmt: skip

    def forget(self, pid: str) -> dict[str, Any]:
        with self.state.lock:
            rec = self._rec(pid)
            refs = [f"refs/lattice/plans/{pid}/{k}" for k in rec.get("anchors") or {}]
            del self.plans[pid]
            return {"id": pid, "status": "forgotten", "refs_removed": refs}

    def _plan_now(self, rec: dict[str, Any]) -> None:
        doc = (
            plan_mod.fallback_plan(rec["_scope"], self._areas())
            if rec["_planner"] == "fallback"
            else self._agent_doc(rec["_scope"])
        )
        v = self._validate(rec, doc)
        if not v["ok"]:  # the mock agent plan is always valid; the fallback in any case
            doc = plan_mod.fallback_plan(rec["_scope"], self._areas())
            v = self._validate(rec, doc)
            rec["_planner"] = "fallback"
        rec["_doc"] = doc
        self._apply(rec, v)
        tot = plan_mod._usage_totals()  # v1.11: the totals include the planner once it ended
        plan_mod._sum_into(tot, rec["planner"]["usage"])
        rec["totals"] = tot
        rec["source"] = "fallback" if rec["_planner"] == "fallback" else "agent"
        if rec["source"] == "fallback":
            rec["fallback_reason"] = "requested: the request asked for the fallback planner"
        rec["status"] = "ready"

    def _step(self, rec: dict[str, Any]) -> None:
        """One poll of a running plan."""
        nodes = {n["id"]: n for n in rec["nodes"]}
        cur = rec["_cursor"]
        now = datetime.now(UTC).timestamp()
        if cur["level"] >= len(rec["levels"]):
            rec["status"] = "passed"
            rec["finished"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            tot = plan_mod._usage_totals()
            for n in rec["nodes"]:
                plan_mod._sum_into(tot, n["usage"])
                tot["rounds"] += n["rounds"]
                tot["lines_changed"] += n["lines_changed"]
            plan_mod._sum_into(tot, rec["planner"]["usage"])
            tot["wall_s"] = round(sum(n["wall_s"] for n in rec["nodes"]), 2)
            widths = [len(x) for x in rec["levels"]]
            tot["shape"] = {
                "nodes": len(rec["nodes"]),
                "levels": len(widths),
                "max_level_width": max(widths),
            }
            rec["totals"] = tot
            return
        if cur.get("repair_level") == cur["level"]:  # resume.kind repair (v1.11): one poll
            L = cur.pop("repair_level")
            old = next(x for x in reversed(rec["level_runs"]) if x["level"] == L)
            old.update(superseded=True, repair_pending=False)
            ids = list(old["merged"])
            tip = hashlib.sha1(f"{rec['id']}:L{L}:repaired".encode()).hexdigest()
            lr = self._level_run(rec, L, ids)
            lr.update(repair_resume=True, repair_pass=2)
            repair = next((n for n in rec["nodes"] if n.get("kind") == "repair"
                           and n["level"] == L), None)  # fmt: skip
            lr["repairs"] = [self._repair_entry(rec, L, 3, ids, old["gate"]["sha"], "passed",
                                                repair["id"] if repair else None)]  # fmt: skip
            if repair is not None:  # its commit is merged after the level's nodes
                commit = lr["repairs"][0]["nodes"][1]["commit"]
                repair.update(status="passed", error=None, commit=commit)
                ids = [*ids, repair["id"]]
            self._gate(rec, L, lr, ids)
            self._land(rec, lr, "resume")
            rec["error"] = None
            cur.update(level=L + 1, phase="start")
            return
        if cur["phase"] == "merge":  # the level's nodes passed in the poll before
            ids = list(rec["level_runs"][-1]["nodes"])
        else:
            ids = [i for i in rec["levels"][cur["level"]] if nodes[i]["status"] != "passed"]
        if not ids:  # resumed: this level passed and landed before (v1.10)
            cur.update(level=cur["level"] + 1, phase="start")
            return
        if cur["phase"] == "start":
            if cur["level"] == len(rec["levels"]) - 1 and len(rec["levels"]) > 1:
                # v1.11: before the last level the plan runs the full gate on the target's tip
                rec["final_gate"] = {"sha": rec["target_tip"], "failing": [], "at": self._now(),
                                     "gates": []}  # fmt: skip
            for nid in ids:
                n = nodes[nid]
                bid = f"b{100 + self._builds}"
                self._builds += 1
                n.update(
                    status="running",
                    build_id=bid,
                    builds=[*n["builds"], bid],
                    started_ts=now,
                    branch=f"rebuild/{rec['id']}-{nid}",
                    attempts=n.get("attempts", 0) + 1,
                )
            rec["level_runs"].append(
                {
                    "level": cur["level"],
                    "run": rec["runs"],
                    "base": rec["target_tip"],
                    "nodes": list(ids),
                    "status": "running",
                    "merged": [],
                    "merge_conflicts": [],
                    "tip": None,
                    "gate": None,
                    "regressions": [],
                    "blame": [],
                }
            )
            cur["phase"] = "running"
        elif cur["phase"] == "running":
            for nid in ids:
                n = nodes[nid]
                if n["status"] != "running":
                    continue
                k = len(n["scope"]["rules"]) or 1
                sha = hashlib.sha1(f"{rec['id']}:{nid}".encode()).hexdigest()
                n.update(
                    status="passed",
                    commit=sha,
                    finished_ts=now + 60 * k,
                    rounds=2,
                    wall_s=float(60 * k),
                    lines_changed=40 * k,
                    files_changed=[f"internal/{nid}/handlers.go"],
                    usage=plan_mod._node_usage(
                        {
                            "input_tokens": 9000 * k,
                            "output_tokens": 1500 * k,
                            "cache_read_input_tokens": 60000 * k,
                            "cost_usd_list_estimate": 0.12 * k,
                        }
                    ),
                )
            cur["phase"] = "merge"
        else:
            L = cur["level"]
            lr = rec["level_runs"][-1]
            if L == 1 and len(rec["levels"]) >= 3 and rec["runs"] == 1 and not cur.get("repaired"):
                # v1.11: the merged level fails its gate once; its first node repairs it
                cur["repaired"] = True
                failed = hashlib.sha1(f"{rec['id']}:L{L}:failed".encode()).hexdigest()
                target = f"scenario:{ids[0]}_works_with_the_others"
                lr.update(merged=list(ids), gate={
                    "sha": failed, "verdict": "fail", "counts": {"D": 1}, "failing": [target],
                    "regressions": [], "newly_fixed": [], "gates": [], "mode": "regression",
                    "selected": 12, "in_scope_failing": [target]})  # fmt: skip
                lr["repairs"] = [self._repair_entry(rec, L, 1, ids, failed, None)]
                nodes[ids[0]]["status"] = "repairing"
                return
            if lr.get("repairs"):
                lr["repairs"][-1]["outcome"] = "passed"
                nodes[ids[0]]["status"] = "passed"
            tip = hashlib.sha1(f"{rec['id']}:L{cur['level']}".encode()).hexdigest()
            lr.update(
                status="passed",
                merged=list(ids),
                tip=tip,
                gate={
                    "sha": tip,
                    "verdict": "fail",
                    "counts": {"D": 0},
                    "failing": [],
                    "regressions": [],
                    "newly_fixed": [],
                    "gates": [],
                    "mode": "regression",
                    "selected": 12,
                    "in_scope_failing": [],
                },
            )
            for nid in ids:
                nodes[nid]["merged"] = True
            rec["target_tip"] = tip
            self.state.branch_heads[rec["target_branch"]] = tip
            cur.update(level=cur["level"] + 1, phase="start")

    def get(self, pid: str) -> dict[str, Any] | None:
        with self.state.lock:
            rec = self.plans.get(pid)
            if rec is None:
                return None
            if rec["status"] == "planning":
                self._plan_now(rec)
            elif rec["status"] == "running":
                self._step(rec)
            if rec["status"] != "running":
                self.state.drift_warnings(rec)  # API v1.12: refreshed when read
            return self._view(rec)

    def list(self) -> list[dict[str, Any]]:
        with self.state.lock:
            return [
                {
                    **{
                        k: copy.deepcopy(r[k])
                        for k in (
                            "id",
                            "status",
                            "source",
                            "version",
                            "scope",
                            "target_branch",
                            "max_parallel",
                            "levels",
                            "created",
                            "finished",
                            "totals",
                            "interrupted",
                            "attempts",
                        )
                    },
                    "resume": self._view(r)["resume"],
                    "nodes": [{"id": n["id"], "status": n["status"]} for n in r["nodes"]],
                }
                for r in self.plans.values()
            ]

    def _rec(self, pid: str) -> dict[str, Any]:
        rec = self.plans.get(pid)
        if rec is None:
            raise MockRefusal(404, f"no plan {pid}")
        return rec

    def approve(self, pid: str) -> dict[str, Any]:
        with self.state.lock:
            rec = self._rec(pid)
            if rec["status"] != "ready":
                raise MockRefusal(409, f"plan {pid} is {rec['status']}, not ready")
            rec["status"] = "approved"
            rec["approved"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {"id": pid, "status": "approved", "version": rec["version"]}

    def run(self, pid: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        with self.state.lock:
            rec = self._rec(pid)
            if rec["status"] not in ("approved", "partial", "failed", "stopped", "interrupted"):
                hint = ": approve it first" if rec["status"] == "ready" else ""
                raise MockRefusal(409, f"plan {pid} is {rec['status']}, not approved{hint}")
            busy = next((p for p in self.plans.values() if p["status"] == "running"), None)
            if busy is not None:
                raise MockRefusal(409, f"a build plan is running in this project ({busy['id']})")
            if rec["status"] != "approved":
                view = self._view(rec)["resume"]
                if not view["available"]:
                    raise MockRefusal(409, view["note"], "nothing_to_resume")
                if view["kind"] == "land":
                    self.land(pid, body)  # 409 while the target is checked out
                rw = plan_mod.repair_waiting(rec) if view["kind"] == "repair" else None
                keep = set(rw["merged"]) if rw else set()
                for n in rec["nodes"]:
                    if n["status"] not in ("passed", "pending") and n["id"] not in keep:
                        n.update(status="pending", blocked_by=[], error=None)
            rec.update(interrupted=False)
            rec.update(
                status="running",
                runs=rec["runs"] + 1,
                finished=None,
                started=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                target_tip=self.state.branch_heads.get(
                    rec["target_branch"], self.state.branch_heads["main"]
                ),
            )
            self.state.branch_heads.setdefault(rec["target_branch"], rec["target_tip"])
            rec["baseline"] = {
                "sha": rec["target_tip"],
                "verdict": "fail",
                "counts": {"D": 0},
                "failing": [],
                "findings": [],
                "reused": rec["runs"] > 1,  # v1.11: a resumed run starts on a recorded tip
            }
            rec["_cursor"] = {"level": 0, "phase": "start"}
            if rec["status"] == "running" and plan_mod.repair_waiting(rec) is not None:
                rec["_cursor"]["repair_level"] = int(plan_mod.repair_waiting(rec)["level"])
            rec["gate_config"] = {"scoped_gate_args": MOCK_PLAN_DEFAULTS["scoped_gate"],
                                  "level_gate": "regression", "level_repair_attempts": 2,
                                  "node_timeout": 1800.0, "node_max_tokens": None}  # fmt: skip
            rec["attempts"] = rec["runs"]
            rec["timeline"].append({"at": self._now(), "event": f"run {rec['runs']} started"})
            return {"id": pid, "status": "running"}

    def stop(self, pid: str) -> dict[str, Any]:
        with self.state.lock:
            rec = self._rec(pid)
            if rec["status"] not in ("planning", "running"):
                raise MockRefusal(409, f"cannot stop a plan that is {rec['status']}")
            rec["status"] = "stopped"
            for n in rec["nodes"]:
                if n["status"] == "running":
                    n["status"] = "stopped"
            return {"id": pid, "status": "stopped"}

    def revise(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        with self.state.lock:
            rec = self._rec(pid)
            if rec["status"] not in ("ready", "approved"):
                raise MockRefusal(
                    409,
                    f"plan {pid} is {rec['status']}; only a ready or approved plan can be revised",
                )
            if ("feedback" in body) == ("edits" in body):
                raise MockRefusal(422, "send feedback or edits (one of them)")
            if "feedback" in body:
                rec["status"] = "planning"
                rec["approved"] = None
                return {"id": pid, "status": "planning"}
            edits = body["edits"] if isinstance(body["edits"], dict) else {}
            doc = copy.deepcopy(rec["_doc"])
            by = {n["id"]: n for n in doc["nodes"]}
            for nid, e in (edits.get("nodes") or {}).items():
                if nid not in by:
                    raise MockRefusal(404, f"no node {nid}")
                if "depends_on" in e:
                    by[nid]["depends_on"] = list(e["depends_on"])
                if "tier" in e:
                    by[nid]["tier"] = e["tier"]
                if e.get("model"):
                    rec["_overrides"][nid] = e["model"]
            for a, b in edits.get("remove_edges") or []:
                by[b]["depends_on"] = [
                    d for d in by[b]["depends_on"] if (d if isinstance(d, str) else d["id"]) != a
                ]
            for a, b in edits.get("add_edges") or []:
                by[b]["depends_on"] = [*by[b]["depends_on"], a]
            if "max_parallel" in edits:
                rec["max_parallel"] = int(edits["max_parallel"])
            v = self._validate(rec, doc)
            if not v["ok"]:
                raise MockRefusal(
                    422, "the edited plan is invalid: " + "; ".join(v["issues"]), "plan_invalid"
                )
            rec["_doc"] = doc
            self._apply(rec, v)
            rec["status"] = "ready"
            rec["approved"] = None
            return self._view(rec)


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

    def _session(self, fn: Any, status: int = 200) -> None:
        try:
            self._json(fn(), status)
        except SessionError as e:
            self._error(HTTPStatus(e.status), e.detail)

    @staticmethod
    def _session_get(path: str, query: dict[str, str], sessions: MockSessions) -> Any:
        if path == "/api/session":
            return sessions.list()
        parts = path.removeprefix("/api/session/").split("/")
        if len(parts) == 2 and parts[1] == "events":
            try:
                after = int(query.get("after", "0"))
            except ValueError:
                after = -1
            if after < 0:
                raise SessionError(422, "after must be an integer >= 0")
            return sessions.events(parts[0], after)
        raise SessionError(404, "no such route")

    def _split(self) -> tuple[str, MockState] | None:
        """The path without its ``/p/<name>`` prefix and the project's state (the default
        project's without one); an unknown project answers 404 (None)."""
        path = urlparse(self.path).path
        if not path.startswith("/p/"):
            return path, self.server.state
        seg, _, rest = path.removeprefix("/p/").partition("/")
        state = self.server.states.get(unquote(seg))
        if (
            state is None
            and unquote(seg) == MOCK_LINKED_CONSUMER
            and rest.startswith("api/linked-")
        ):  # v1.19: a manifest consumer the mock does not serve answers its notes
            state = self.server.state
        if state is None:
            self._error(HTTPStatus.NOT_FOUND, "no such project")
            return None
        return "/" + rest, state

    def _owner(self) -> str:
        """The project name of the request's ``/p/<name>`` prefix (default: the default project)."""
        path = urlparse(self.path).path
        if not path.startswith("/p/"):
            return DEFAULT_PROJECT
        return unquote(path.removeprefix("/p/").partition("/")[0])

    def _refusal(self, fn: Any) -> None:
        try:
            self._json(fn())
        except MockRefusal as e:
            self._json(e.body(), e.status)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        split = self._split()
        if split is None:
            return None
        path, state = split
        if path == "/api/projects":
            return self._json(self.server.projects())
        if path == "/api/workspace":
            return self._json(self.server.workspace())
        if path == "/api/contracts":
            return self._json(self.server.contracts(query.get("consumer") or None))
        if path == "/api/contracts/impact":
            return self._json(
                self.server.contracts_impact(query.get("provider", ""), query.get("candidate", ""))
            )
        if path == "/api/model":
            return self._json(state.get_model())
        if path == "/api/areas":
            return self._json(state.areas(query.get("branch") or None))
        if path == "/api/branches":
            return self._json(state.branches())
        if path == "/api/engines":
            return self._json(state.engines())
        if path == "/api/linked-questions":
            return self._refusal(
                lambda: self.server.linked_list(self._owner(), query.get("status") or None)
            )
        if path.startswith("/api/linked-questions/") and "/" not in path.removeprefix(
            "/api/linked-questions/"
        ):
            qid = unquote(path.removeprefix("/api/linked-questions/"))
            return self._refusal(lambda: self.server.linked_get(self._owner(), qid))
        if path == "/api/linked-notifications":
            return self._json(self.server.linked_notifications(self._owner()))
        if path == "/api/improve":
            return self._json(state.improve())
        if path == "/api/mcp":
            raw = str(query.get("refresh", "0")).lower()
            if raw not in ("0", "1", "true", "false", ""):
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "refresh must be 1 or 0")
            return self._json(state.mcp(raw in ("1", "true")))
        if path == "/api/plans":
            return self._json(state.plans.list())
        if path.startswith("/api/plan/") and "/" not in path.removeprefix("/api/plan/"):
            pid = path.removeprefix("/api/plan/")
            plan = state.plans.get(pid)
            if plan is None:
                return self._error(HTTPStatus.NOT_FOUND, f"no plan {pid}")
            return self._json(plan)
        if path == "/api/session" or path.startswith("/api/session/"):
            return self._session(
                lambda: self._session_get(path, query, state.sessions),
            )
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
        if path == "/api/graph":
            focus = query.get("focus") or None
            kinds = None
            if "kinds" in query:
                kinds = {k.strip() for k in query["kinds"].split(",") if k.strip()}
                bad = sorted(kinds - set(GRAPH_KINDS))
                if bad or not kinds:
                    return self._error(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        f"unknown kinds {bad}; use {','.join(GRAPH_KINDS)}",
                    )
            nums: dict[str, int] = {}
            for key, default, lo, hi in (
                ("depth", 1, 0, GRAPH_MAX_DEPTH),
                ("limit", GRAPH_DEFAULT_LIMIT, 1, 5000),
            ):
                raw = query.get(key)
                try:
                    nums[key] = default if raw is None else int(raw)
                except ValueError:
                    nums[key] = lo - 1
                if not lo <= nums[key] <= hi:
                    return self._error(
                        HTTPStatus.UNPROCESSABLE_ENTITY, f"{key} must be an integer {lo}..{hi}"
                    )
            graph = state.graph(focus, kinds, nums["depth"], nums["limit"])
            if graph is None:
                return self._error(HTTPStatus.NOT_FOUND, f"no node {focus}")
            return self._json(graph)
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
        split = self._split()
        if split is None:
            return None
        path, state = split
        body = self._body()
        if body is None:
            return self._error(HTTPStatus.BAD_REQUEST, "the body must be a JSON object")
        if path == "/api/session":
            return self._session(lambda: state.sessions.create(body), 201)
        if path.startswith("/api/session/") and path.endswith("/message"):
            sid = path.removeprefix("/api/session/").removesuffix("/message")
            return self._session(lambda: state.sessions.message(sid, body), 202)
        if path.startswith("/api/linked-questions/"):
            parts = path.removeprefix("/api/linked-questions/").split("/")
            if len(parts) == 2 and parts[1] in ("resolve", "dismiss"):
                qid, verb = unquote(parts[0]), parts[1]
                return self._refusal(
                    lambda: self.server.linked_clear(self._owner(), qid, verb, body)
                )
        if path == "/api/ask":
            if not str(body.get("question", "")).strip():
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "question is empty")
            return self._json(state.ask(body))
        if path == "/api/explain":
            if not str(body.get("question", "")).strip():
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "question is empty")
            return self._json(state.explain(body))
        if path.startswith("/api/branches/") and path.endswith("/sync-spec"):
            name = unquote(path.removeprefix("/api/branches/").removesuffix("/sync-spec"))
            try:
                return self._json(state.sync_spec(name, body))
            except MockRefusal as e:
                return self._json(e.body(), e.status)
        if path == "/api/plan" or path.startswith("/api/plan/"):
            parts = path.removeprefix("/api/plan").strip("/").split("/")
            try:
                if path == "/api/plan":
                    return self._json(state.plans.create(body))
                if len(parts) == 2 and parts[1] == "approve":
                    return self._json(state.plans.approve(parts[0]))
                if len(parts) == 2 and parts[1] == "run":
                    return self._json(state.plans.run(parts[0], body))
                if len(parts) == 2 and parts[1] == "land":
                    return self._json(state.plans.land(parts[0], body))
                if len(parts) == 2 and parts[1] == "revise":
                    return self._json(state.plans.revise(parts[0], body))
            except MockRefusal as e:
                return self._json(e.body(), e.status)
            return self._error(HTTPStatus.NOT_FOUND, "no such route")
        if path == "/api/rebuild" and body.get("scope") is not None:
            if body.get("changes"):
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY,
                                   "the mock builds a scope without changes")  # fmt: skip
            try:
                return self._json(state.start_scoped(body))
            except MockRefusal as e:
                return self._json(e.body(), e.status)
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
            try:
                return self._json(state.new_change(body))
            except SourceError as e:
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(e))
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
        split = self._split()
        if split is None:
            return None
        path, state = split
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
        if path.startswith("/api/session/"):
            sid = path.removeprefix("/api/session/")
            if not sid or "/" in sid:
                return self._error(HTTPStatus.NOT_FOUND, "no such route")
            return self._session(lambda: state.sessions.interrupt(sid))
        if path.startswith("/api/plan/"):
            pid = path.removeprefix("/api/plan/")
            if not pid or "/" in pid:
                return self._error(HTTPStatus.NOT_FOUND, "no such route")
            forget = (parse_qs(urlparse(self.path).query).get("forget") or ["false"])[-1]
            try:
                if forget.lower() in ("true", "1"):
                    return self._json(state.plans.forget(pid))
                return self._json(state.plans.stop(pid))
            except MockRefusal as e:
                return self._json(e.body(), e.status)
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
        # two projects (API v1.6): the fixtures as job-scheduler-studio (the default, also
        # served at the bare /api/...), and a smaller lattice-studio
        self.states: dict[str, MockState] = {
            DEFAULT_PROJECT: MockState(fixture_dir, name=DEFAULT_PROJECT),
            SECOND_PROJECT: MockState(
                fixture_dir,
                name=SECOND_PROJECT,
                root="/Users/owner/lattice/dogfood/studio",
                rules=SECOND_PROJECT_RULES,
                seed_change=False,
            ),
        }
        self.state = self.states[DEFAULT_PROJECT]
        self.states[SECOND_PROJECT].plans.seed_fixtures()  # API v1.10: resume fixtures
        self.verbose = verbose
        self.workspace_enabled = True  # API v1.16: False answers the no-manifest shape
        # API v1.19: the fixture consumer's linked questions, cleared in memory
        self.linked: list[dict[str, Any]] = [copy.deepcopy(q) for q in MOCK_LINKED_QUESTIONS]
        self.linked_lock = threading.Lock()

    def workspace(self) -> dict[str, Any]:
        """``GET /api/workspace`` (v1.16): the fixture manifest over the mock's projects."""
        from lattice.workspace import WARNING, Problem

        if not self.workspace_enabled:
            return {"workspace": None, "projects": [], "links": [], "problems": []}
        rows, problems = [], []
        for name, hub, path, role, spec in MOCK_WORKSPACE_PROJECTS:
            if hub is not None:
                with self.states[hub].lock:
                    path = self.states[hub].model["project"]["root"]
            else:
                problems.append(Problem("missing_path", f"project {name}: {path} does not exist",
                                        WARNING).to_dict())  # fmt: skip
            rows.append({"name": name, "path": path, "role": role, "in_hub": hub is not None,
                         "hub_name": hub, "spec": spec})  # fmt: skip
        return {"workspace": {"name": MOCK_WORKSPACE_NAME, "path": MOCK_WORKSPACE_PATH},
                "projects": rows, "links": [dict(lk) for lk in MOCK_WORKSPACE_LINKS],
                "problems": problems}  # fmt: skip

    def contracts(self, consumer: str | None = None) -> dict[str, Any]:
        """``GET /api/contracts[?consumer=]`` (v1.17): the fixture static check over the fixture
        manifest, for `consumer` or for every consumer with an opted-in link."""
        from lattice.workspace import Problem

        if not self.workspace_enabled:
            return {"workspace": None, "links": [], "problems": []}
        problems = self.workspace()["problems"]
        names = [name for name, *_rest in MOCK_WORKSPACE_PROJECTS]
        if consumer is not None and consumer not in names:
            msg = f"no project {consumer!r} in the workspace (have {', '.join(names)})"
            problems.append(Problem("unknown_project", msg).to_dict())
            links: list[dict[str, Any]] = []
        else:
            links = [
                copy.deepcopy(lk)
                for lk in MOCK_CONTRACT_LINKS
                if consumer is None or lk["consumer"] == consumer
            ]
        return {"workspace": MOCK_WORKSPACE_NAME, "consumer": consumer, "checked": len(links),
                "links": links, "problems": problems, "elapsed_s": 0.0}  # fmt: skip

    def contracts_impact(self, provider: str, candidate: str) -> dict[str, Any]:
        """``GET /api/contracts/impact?provider=&candidate=`` (v1.18): the fixture preview over
        the fixture manifest; every key always present, exit_code 2 when it cannot run."""
        from lattice.workspace import WARNING, Problem

        ref = {"input": candidate, "kind": "ref", "resolved": None, "spec_ref": None,
               "version": None, "endpoints": None}  # fmt: skip
        out: dict[str, Any] = {
            "format": 1, "workspace": None, "provider": provider, "candidate": ref,
            "current": None, "advisory": True, "static_only": True, "baseline": "pin",
            "checked": 0, "consumers": [],
            "counts": {"contradiction": 0, "regression": 0, "unaffected": 0, "error": 0},
            "problems": [], "exit_code": 2, "elapsed_s": 0.0,
        }  # fmt: skip
        if not self.workspace_enabled:
            return out
        out["workspace"] = MOCK_WORKSPACE_NAME
        out["problems"] = self.workspace()["problems"]
        names = [name for name, *_rest in MOCK_WORKSPACE_PROJECTS]
        if not provider or not candidate:
            missing = " and ".join(k for k, v in (("provider", provider),
                                                  ("candidate", candidate)) if not v)  # fmt: skip
            msg = f"pass {missing}: ?provider=NAME&candidate=REF"
            out["problems"].append(Problem("missing_argument", msg).to_dict())
            return out
        if provider not in names:
            msg = f"no project {provider!r} in the workspace (have {', '.join(names)})"
            out["problems"].append(Problem("unknown_project", msg).to_dict())
            return out
        ref["spec_ref"] = MOCK_CONTRACT_VERSION_PINNED["spec_ref"]
        rows = [copy.deepcopy(r) for r in MOCK_IMPACT_ROWS.get(provider, ())]
        if not rows:
            msg = f"no link with a contract key points to {provider}"
            out["problems"].append(Problem("no_consumers", msg, WARNING).to_dict())
            out["exit_code"] = 0
            return out
        ref.update(resolved=MOCK_IMPACT_SHA, version=dict(MOCK_IMPACT_VERSION), endpoints=18)
        out.update(current=dict(MOCK_CONTRACT_VERSION_CURRENT), checked=len(rows), consumers=rows)
        for row in rows:
            out["counts"][row["label"]] += 1
        out["exit_code"] = 1 if out["counts"]["contradiction"] else 0
        return out

    def _linked_rows(self, owner: str) -> list[dict[str, Any]]:
        return self.linked if owner == MOCK_LINKED_CONSUMER else []

    def linked_list(self, owner: str, status: str | None) -> dict[str, Any]:
        """``GET /api/linked-questions[?status=]`` (v1.19): the consumer's notes (the fixture
        consumer holds two; every served project none)."""
        if status not in (None, "all", "open", "resolved", "dismissed"):
            raise MockRefusal(422, "status must be one of open, resolved, dismissed, all",
                              "bad_status")  # fmt: skip
        with self.linked_lock:
            rows = [copy.deepcopy(q) for q in self._linked_rows(owner)]
        counts = {k: sum(1 for q in rows if q["status"] == k)
                  for k in ("open", "resolved", "dismissed")}  # fmt: skip
        counts["superseded"] = sum(1 for q in rows if q["superseded"])
        shown = [q for q in rows if status in (None, "all") or q["status"] == status]
        if owner == MOCK_LINKED_CONSUMER:
            where = MOCK_LINKED_STATE
        else:
            where = f"{self.states[owner].model['project']['root']}/.lattice/state/{owner}"
        return {"format": 1, "consumer": owner, "state": "present", "state_dir": where,
                "questions": shown, "counts": counts, "problems": [],
                "legend": dict(LEGEND)}  # fmt: skip

    def linked_get(self, owner: str, qid: str) -> dict[str, Any]:
        with self.linked_lock:
            for q in self._linked_rows(owner):
                if q["id"] == qid:
                    return copy.deepcopy(q)
        raise MockRefusal(404, f"no linked question {qid!r}", "unknown_question")

    def linked_clear(self, owner: str, qid: str, verb: str, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/linked-questions/<id>/resolve|dismiss`` (v1.19), in memory."""
        note = body.get("note")
        text = "" if note is None else note
        if not isinstance(text, str) or len(text) > 2000:
            raise MockRefusal(422, "note must be a string of at most 2000 characters",
                              "bad_note")  # fmt: skip
        with self.linked_lock:
            for q in self._linked_rows(owner):
                if q["id"] != qid:
                    continue
                if q["status"] != "open":
                    raise MockRefusal(409, f"{qid} is already {q['status']}", "not_open")
                status = "resolved" if verb == "resolve" else "dismissed"
                at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                q["status"] = status
                q["resolution"] = {"status": status, "note": text, "at": at, "via": "studio"}
                return copy.deepcopy(q)
        raise MockRefusal(404, f"no linked question {qid!r}", "unknown_question")

    def linked_notifications(self, owner: str) -> dict[str, Any]:
        """``GET /api/linked-notifications`` (v1.19): the provider's one fixture delivery."""
        rows = [copy.deepcopy(MOCK_LINKED_NOTIFICATION)] if owner == DEFAULT_PROJECT else []
        return {"notifications": rows}

    def projects(self) -> list[dict[str, Any]]:
        """``GET /api/projects``."""
        out = []
        for name, st in self.states.items():
            with st.lock:
                m = st.model
                last = m["app"].get("last_check")
                out.append(
                    {
                        "name": name,
                        "root": m["project"]["root"],
                        "git_head": m["project"]["git_head"],
                        "spec": True,
                        "app_configured": bool(m["app"].get("configured")),
                        "last_check": (
                            {"verdict": last.get("verdict"), "at": last.get("at")} if last else None
                        ),
                        "capabilities": {
                            "session": True,
                            "rebuild": True,
                            "build_from_spec": True,
                            "build_plan": True,
                        },
                        "default": name == DEFAULT_PROJECT,
                        "state_warnings": [],  # API v1.10: the mock keeps its state in memory
                        "plan_defaults": dict(MOCK_PLAN_DEFAULTS),  # API v1.11
                    }
                )
        return out


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
