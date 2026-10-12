"""Build plans (API.md v1.9): Studio builds the app from the spec as an AGENTIC EXECUTION
GRAPH -- nodes (scoped builds) wired by real dependencies, run level by level -- instead of
one scope at a time.

**Plan.** ``POST /api/plan`` resolves the scope (``"all"``, ``{"areas": [ids]}``,
``{"rules": [ids]}``), validates the target branch now (v1.8 semantics, nothing created),
and plans in a background thread:

* the **planner** is one ``claude -p`` call (:func:`build_planner_argv`: read-only tools
  Read/Grep/Glob, no Bash/Edit/Write, ``--setting-sources ""``, no MCP server, no session
  persistence, the environment without API credentials, its own process group, model
  ``[rebuild] planner_model`` default opus) with the epg method prompt
  (``prompts/epg_plan.md``) as its system prompt and the areas, data model and repository
  file tree as its message (:func:`planner_prompt`). It answers ONE JSON object
  (``{"nodes": [...]}``); it never defines gates.
* every plan, and every owner edit, is **validated** by Studio (:func:`validate_plan`,
  deterministic): schema, unique ids, known dependencies, acyclic, coverage (every rule of
  the scope covered by exactly one non-integration node; the scaffold only by
  infrastructure rules), sanitised write sets (``touches``), Studio's gate on every node,
  tiers mapped to models (unknown: ``mid``). Then the levels: two nodes on one level must
  not have overlapping write sets (tables, packages, files; a path overlaps its
  sub-paths): the later one (document order) gets an edge from the earlier one
  (``serialised_because``, ``validation.auto_edits``); a level holds at most
  ``max_parallel`` nodes (the excess moves down a level). The scaffold runs first and the
  integration node last (edges added, and an integration node when the plan has none).
  PARALLEL vs SEQUENTIAL is decided by this construction, never by a flag.
* an invalid agent plan is sent back ONCE with the issues; still invalid (or the planner
  failed) -> the deterministic **fallback** (:func:`fallback_plan`): a scaffold of the
  infrastructure areas, the accounts/auth area, the other areas chained in document order,
  integration. ``source: "fallback"`` with ``fallback_reason``.
* TEST ONLY: ``[rebuild] planner = "stub"`` reads ``stub-plan.json`` in the project dir
  (a planner document, or ``{"scopes": {<plan scope slug>: doc | {"doc": doc, "retry":
  doc} | {"error": "..."}}}``).

The owner reviews the plan (``GET``), revises it (``POST .../revise``: edits re-validated,
or feedback re-runs the planner), and approves it; ``POST .../run`` is 409 until then.

**Runner** (one plan at a time per project, holding the Rebuilder's build slot
``plan_active``: a stand-alone build is refused while a plan runs and a plan is refused
while a build runs; node builds of the plan run concurrently, each its own worktree,
branch and process group, see :meth:`Rebuilder.start_node`):

1. the target branch is resolved (created when asked) and the **full gate** runs on its
   tip in a detached integration worktree: the plan's start baseline (for the default
   target, the owner's HEAD is merged in first when the branch lacks it, as in v1.8);
2. per level, the nodes whose dependencies all passed start from the target tip AT THE
   START OF THE LEVEL (at most ``max_parallel`` at once); a node with a failed, blocked or
   conflicting dependency is ``blocked``; a node with no in-scope failing check is
   ``passed`` with ``nothing_to_build`` (a scaffold with no rules of its own runs while
   anything fails in the plan scope, API v1.11). Each node is judged by Studio: its
   in-scope failing checks pass and nothing that passed at the level start regresses (with
   ``[rebuild] scoped_gate_args`` its gate runs on its scope plus that regression set);
3. at the END of the level, the passed nodes' commits are merged into the level's start
   tip in plan node order in a temporary integration worktree with ``git merge --no-ff``
   (one merge commit per node, so each node stays one revertible unit and its branch is
   kept; a conflicting merge is aborted, the node is ``merge_conflict`` and its dependents
   are blocked), the nodes' metrics hooks run there (one ``metrics: plan`` commit), and the
   FULL gate runs on the merged tip. Any check failing there that did not fail on the last
   good tip (which never fails more than the plan's start baseline) is a regression, and a
   merged node's own target failing there fails the level too: the merged nodes are
   ``regressed`` (blamed by the rules the failing check is linked to, else the level) and
   the level repair loop runs (API v1.11: the responsible nodes repair, the level merges
   and is gated again, at most ``[rebuild] level_repair_attempts`` times; then the plan
   ends ``partial`` with ``resume.kind`` ``repair``). With ``level_gate = "regression"``
   the level gate is the first gate on the scenarios passing before plus the landed scopes,
   the last level the full gate. Otherwise the target branch is
   fast-forwarded to the merged tip (``update-ref`` with the old tip, or ``merge
   --ff-only`` in the owner's clean worktree when confirmed) -- only ever to a tip whose
   full gate showed no regression;
4. end: ``passed`` (every node passed), ``partial`` (some passed), ``failed`` (none, or a
   level failed before anything landed), ``stopped`` (``DELETE``: running node builds are
   stopped -- process groups killed -- between or during nodes; passed nodes' branches are
   kept). ``POST .../run`` on a partial, failed or stopped plan resumes: only the nodes
   that are not ``passed`` run again.

**Metrics**: per node the build's tokens, list-price cost estimate, wall time, rounds and
the lines/files its commit changed; plan totals (nodes + planner); the metrics hook runs per
node (``--phase build_<plan>_<node>``, at the level end) and once per plan (``--phase
build_plan_<id>``, in a temporary worktree at the final tip; files it writes are committed
on ``rebuild/<plan>-metrics``, never on the target).
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import shlex
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.llm.cli_client import cli_env
from lattice.studio import areas as areas_mod
from lattice.studio.rebuild import (
    MAX_WAIT,
    Rebuilder,
    RebuildError,
    StreamUsage,
    _checked_out,
    _current_branch,
    _git,
    _junk_pathspecs,
    _now,
    _short,
    _zero_usage,
    reap_orphans,
    result_doc,
    write_selection,
)
from lattice.studio.session import DEFAULT_MAX_TOKENS, Transcript, _kill_group
from lattice.studio.state import StateStore, proc_info

__all__ = [
    "PlanError",
    "PlanManager",
    "build_planner_argv",
    "fallback_plan",
    "planner_prompt",
    "land_waiting",
    "resolve_plan_scope",
    "resume_view",
    "validate_plan",
]

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "epg_plan.md"
STUB_PLAN = "stub-plan.json"
TIERS = ("fast", "mid", "frontier")
KINDS = ("scaffold", "area", "merge", "integration")
DEFAULT_MODELS = {"fast": "haiku", "mid": "sonnet", "frontier": "opus"}
MAX_PARALLEL = 8
STATUSES = ("planning", "ready", "approved", "running", "passed", "partial", "failed", "stopped",
            "interrupted")  # fmt: skip
TRANSIENT = ("planning", "running")
RESUMABLE = ("partial", "failed", "stopped", "interrupted")
RERUN = ("pending", "gave_up", "blocked", "merge_conflict", "regressed", "stopped")
# a level that passed its full gate but whose tip could not be added to the target (v1.10)
LAND_KINDS = ("branch_checked_out", "branch_moved", "tree_dirty")
REF_PREFIX = "refs/lattice/plans"
DRIFT_WARNING = "The spec on "  # API v1.12: the start of the spec-drift warning
WORKTREE_MARK = "/.lattice/rebuilds/"  # where Studio's own worktrees live (WORKTREE_DIR)
PLANNER_TOOLS = ("Read", "Grep", "Glob")
PLANNER_DENIED = ("Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_INFRA = re.compile(r"\b(errors?|versioning|data model|time|timezones?|lists?|pagination)\b", re.I)
_AUTH = re.compile(r"(account|auth|token|session)", re.I)
_FRONTIER = re.compile(r"(account|auth|token|session|sync|concurren|race|lock|nudge|schedul)", re.I)
_ENDPOINT = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s,;)`\"']*)")
TOUCH_KEYS = ("tables", "packages", "files")
TOUCH_MAX = 64
CLIP = 220
FILE_TREE_MAX = 600
SPLIT_RULES, SPLIT_SCENARIOS = 25, 30
NODE_GATE = (
    "Studio's scoped criterion: every check linked to the node's rules that fails at the "
    "start of its level passes, and no check that passed at the start of its level fails"
)
INTEGRATION_GATE = (
    "Studio's full criterion: every check that fails at the start of its level passes, "
    "and none regresses"
)
LEVEL_GATE = (
    "after the level's passed nodes are merged, the full gate on the merged tip shows no "
    "check failing that did not fail on the last good tip; only then does the target "
    "branch move"
)


class PlanError(Exception):
    """A plan request refused: ``status`` (HTTP), ``detail``, optional ``code``."""

    def __init__(self, status: int, detail: str, code: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code


# ---- scope -----------------------------------------------------------------------------------


def resolve_plan_scope(scope: Any, areas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The plan scope as ``{kind, areas: [ids], rules: [ids], title, slug}``. Raises
    PlanError 422 (malformed) or 404 (unknown area or rule)."""
    bad = 'scope must be "all", {"areas": ["<id>", ...]} or {"rules": ["<id>", ...]}'
    real = [a for a in areas if a["id"] != areas_mod.ALL]
    by_id = {str(a["id"]): a for a in real}
    every = [r for a in real for r in a["rules"]]
    if scope == areas_mod.ALL:
        return {"kind": "all", "areas": [a["id"] for a in real], "rules": every,
                "title": "All", "slug": "all"}  # fmt: skip
    if not isinstance(scope, Mapping) or len(scope) != 1:
        raise PlanError(422, bad)
    ((key, ids),) = scope.items()
    if key not in ("areas", "rules") or not isinstance(ids, list) or not ids:
        raise PlanError(422, bad)
    if not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids):
        raise PlanError(422, bad)
    if key == "areas":
        if ids == [areas_mod.ALL]:
            return resolve_plan_scope(areas_mod.ALL, areas)
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise PlanError(404, f"no area {', '.join(missing)}")
        keep = set(ids)
        chosen = [a["id"] for a in real if a["id"] in keep]  # document order
        rules = [r for a in real if a["id"] in keep for r in a["rules"]]
        title = ", ".join(str(by_id[i]["title"]) for i in chosen)
        return {"kind": "areas", "areas": chosen, "rules": rules, "title": title,
                "slug": areas_mod.slug("areas-" + "-".join(ids), 80)}  # fmt: skip
    known = set(every)
    missing = [i for i in ids if i not in known]
    if missing:
        raise PlanError(404, f"no rule {', '.join(missing)}")
    keep = set(ids)
    rules = [r for r in every if r in keep]
    chosen = [a["id"] for a in real if keep & set(a["rules"])]
    return {"kind": "rules", "areas": chosen, "rules": rules, "title": "rules " + ", ".join(ids),
            "slug": areas_mod.scope_slug({"kind": "rules", "rules": ids})}  # fmt: skip


def is_infra(title: str) -> bool:
    """An infrastructure area (errors/versioning, the data model, time, lists): the
    fallback's scaffold builds it, and a scaffold may cover its rules."""
    return bool(_INFRA.search(str(title)))


# ---- fallback --------------------------------------------------------------------------------


def fallback_plan(scope: Mapping[str, Any], areas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The deterministic safe plan (no LLM): a scaffold of the infrastructure areas (when
    any), then the accounts/auth-like area, then every other area in document order, each
    depending on the one before (sequential), then integration. A planner document."""
    keep = set(scope["rules"])
    parts = []
    for a in areas:
        if a["id"] == areas_mod.ALL or a["id"] not in scope["areas"]:
            continue
        rules = [r for r in a["rules"] if r in keep]
        if rules:
            whole = len(rules) == len(a["rules"])
            parts.append((a, rules, whole))
    infra = [p for p in parts if is_infra(p[0]["title"])]
    rest = [p for p in parts if not is_infra(p[0]["title"])]
    auth = next((p for p in rest if _AUTH.search(str(p[0]["title"]))), None)
    order = ([auth] if auth else []) + [p for p in rest if p is not auth]
    used = {"scaffold", "integration"}
    nodes: list[dict[str, Any]] = []
    prev: str | None = None
    if infra:
        nodes.append({
            "id": "scaffold",
            "title": "Shared infrastructure: " + ", ".join(str(p[0]["title"]) for p in infra),
            "kind": "scaffold",
            "scope": {"rules": [r for p in infra for r in p[1]]},
            "depends_on": [],
            "tier": "mid",
            "touches": {"tables": [], "packages": [], "files": []},
            "why": "infrastructure areas (errors, versioning, data model, time, lists) first, "
            "so the areas build on them",
        })  # fmt: skip
        prev = "scaffold"
    for a, rules, whole in order:
        nid, n = str(a["id"]), 2
        while nid in used:
            nid, n = f"{a['id']}-{n}", n + 1
        used.add(nid)
        is_auth = auth is not None and a is auth[0]
        why = (
            "accounts and sessions first: the other areas need a signed-in user"
            if is_auth
            else "the next area in document order (the fallback plan is sequential)"
        )
        nodes.append({
            "id": nid,
            "title": str(a["title"]),
            "kind": "area",
            "scope": {"area": a["id"]} if whole else {"rules": rules},
            "depends_on": [{"id": prev, "why": "fallback: sequential, in order"}] if prev else [],
            "tier": "frontier" if _FRONTIER.search(str(a["title"])) else "mid",
            "touches": {"tables": [], "packages": [], "files": []},
            "why": why,
        })  # fmt: skip
        prev = nid
    nodes.append({
        "id": "integration",
        "title": "Integration: the full gate",
        "kind": "integration",
        "scope": "all",
        "depends_on": [{"id": prev, "why": "runs last"}] if prev else [],
        "tier": "frontier",
        "touches": {"tables": [], "packages": [], "files": []},
        "why": "runs the full gate on everything and repairs what the areas left",
    })  # fmt: skip
    return {"nodes": nodes}


# ---- validation ------------------------------------------------------------------------------


def _clean_path(p: str) -> str | None:
    p = p.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.rstrip("/")
    if not p or p.startswith("/") or ".." in p.split("/") or len(p) > 200 or "\0" in p:
        return None
    return p


def _touches(raw: Any, nid: str, auto: list[dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {k: [] for k in TOUCH_KEYS}
    if raw is None:
        return out
    if not isinstance(raw, Mapping):
        auto.append({"kind": "touches", "node": nid, "detail": "touches was not an object"})
        return out
    for k in TOUCH_KEYS:
        items = raw.get(k) or []
        if not isinstance(items, list):
            auto.append({"kind": "touches", "node": nid, "detail": f"touches.{k} was not a list"})
            continue
        dropped = []
        for it in items:
            if not isinstance(it, str):
                dropped.append(repr(it)[:40])
                continue
            v = it.strip()[:200] if k == "tables" else _clean_path(it)
            if not v:
                dropped.append(str(it)[:40])
            elif v not in out[k]:
                out[k].append(v)
        if len(out[k]) > TOUCH_MAX:
            out[k] = out[k][:TOUCH_MAX]
            dropped.append(f"{len(items) - TOUCH_MAX} more")
        if dropped:
            auto.append({"kind": "touches", "node": nid,
                         "detail": f"dropped touches.{k}: {', '.join(dropped)}"})  # fmt: skip
    return out


def _path_overlap(a: str, b: str) -> bool:
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def overlap(a: Mapping[str, list[str]], b: Mapping[str, list[str]]) -> dict[str, list[str]]:
    """The overlapping write sets of two nodes (empty lists: none)."""
    tb = {t.lower() for t in b.get("tables") or []}
    out = {"tables": sorted({t for t in a.get("tables") or [] if t.lower() in tb})}
    for k in ("packages", "files"):
        hits = set()
        for x in a.get(k) or []:
            for y in b.get(k) or []:
                if _path_overlap(x, y):
                    hits.add(x if len(x) <= len(y) else y)
        out[k] = sorted(hits)
    # a file inside the other node's package is shared code too
    cross = set()
    for x in a.get("files") or []:
        for y in b.get("packages") or []:
            if _path_overlap(x, y):
                cross.add(x)
    for x in b.get("files") or []:
        for y in a.get("packages") or []:
            if _path_overlap(x, y):
                cross.add(x)
    out["files"] = sorted(set(out["files"]) | cross)
    return out


def _any(o: Mapping[str, list[str]]) -> bool:
    return any(o.get(k) for k in TOUCH_KEYS)


def _cycle(ids: Sequence[str], deps: Mapping[str, Sequence[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        state[n] = 1
        stack.append(n)
        for d in deps.get(n, ()):
            if state.get(d) == 1:
                return stack[stack.index(d) :] + [d]
            if state.get(d) is None:
                c = visit(d)
                if c:
                    return c
        stack.pop()
        state[n] = 2
        return None

    for n in ids:
        if state.get(n) is None:
            c = visit(n)
            if c:
                return c
    return None


def validate_plan(
    doc: Any,
    *,
    scope: Mapping[str, Any],
    areas: Sequence[Mapping[str, Any]],
    max_parallel: int,
    models: Mapping[str, str] | None = None,
    model_overrides: Mapping[str, str] | None = None,
    trace_links: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Validate a planner document against the plan scope (see the module docstring):
    ``{ok, issues, auto_edits, nodes, edges, levels}`` (nodes/edges/levels only when ok)."""
    models = {**DEFAULT_MODELS, **(models or {})}
    overrides = dict(model_overrides or {})
    links = {k: list(v) for k, v in (trace_links or {}).items()}
    issues: list[str] = []
    auto: list[dict[str, Any]] = []
    if not isinstance(doc, Mapping) or not isinstance(doc.get("nodes"), list) or not doc["nodes"]:
        return {"ok": False, "issues": ['the plan must be an object with a non-empty "nodes" list'],
                "auto_edits": []}  # fmt: skip
    area_by = {str(a["id"]): a for a in areas if a["id"] != areas_mod.ALL}
    infra_rules = {r for a in area_by.values() if is_infra(a["title"]) for r in a["rules"]}
    plan_rules = list(scope["rules"])
    plan_set = set(plan_rules)
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, raw in enumerate(doc["nodes"], 1):
        if not isinstance(raw, Mapping):
            issues.append(f"node {i} is not an object")
            continue
        nid = raw.get("id")
        if not isinstance(nid, str) or not _ID.match(nid):
            issues.append(f"node {i}: id must match [a-z0-9][a-z0-9_-]* (got {nid!r})")
            continue
        if nid in seen:
            issues.append(f"node id {nid} is not unique")
            continue
        seen.add(nid)
        kind = raw.get("kind")
        if kind not in KINDS:
            issues.append(f"node {nid}: kind must be one of {', '.join(KINDS)} (got {kind!r})")
            continue
        sc = raw.get("scope")
        rules: list[str] = []
        area_id = None
        if kind == "integration":
            if sc not in (None, "all", {"all": True}):
                auto.append({"kind": "scope", "node": nid,
                             "detail": "the integration node's scope is always all"})  # fmt: skip
            skind = "all"
            rules = list(plan_rules)
        elif isinstance(sc, Mapping) and set(sc) == {"area"} and isinstance(sc["area"], str):
            area_id = sc["area"]
            a = area_by.get(area_id)
            if a is None or area_id not in scope["areas"]:
                issues.append(f"node {nid}: area {area_id} is not in the plan scope")
                continue
            rules = [r for r in a["rules"] if r in plan_set]
            skind = "area"
        elif isinstance(sc, Mapping) and set(sc) == {"rules"} and isinstance(sc["rules"], list):
            if not all(isinstance(r, str) for r in sc["rules"]):
                issues.append(f"node {nid}: scope.rules must be rule ids")
                continue
            outside = [r for r in sc["rules"] if r not in plan_set]
            if outside:
                issues.append(f"node {nid} covers {', '.join(outside)}, outside the plan scope")
                continue
            rules = list(dict.fromkeys(sc["rules"]))
            skind = "rules"
        else:
            issues.append(f'node {nid}: scope must be {{"area": id}}, {{"rules": [ids]}} or "all"')
            continue
        if kind == "area" and not rules:
            issues.append(f"node {nid}: an area node must cover at least one rule")
            continue
        if kind == "scaffold":
            bad = [r for r in rules if r not in infra_rules]
            if bad:
                issues.append(
                    f"the scaffold {nid} covers rules that are not infrastructure: "
                    + ", ".join(bad)
                )
                continue
        deps: list[tuple[str, str]] = []
        for d in raw.get("depends_on") or []:
            if isinstance(d, str):
                deps.append((d, ""))
            elif isinstance(d, Mapping) and isinstance(d.get("id"), str):
                deps.append((d["id"], _short(str(d.get("why") or ""), 200)))
            else:
                issues.append(f"node {nid}: depends_on items are node ids or {{id, why}}")
        if not isinstance(raw.get("depends_on") or [], list):
            issues.append(f"node {nid}: depends_on must be a list")
        tier = raw.get("tier")
        if tier not in TIERS:
            auto.append({"kind": "tier", "node": nid, "from": tier, "to": "mid",
                         "detail": f"unknown tier {tier!r}: mid"})  # fmt: skip
            tier = "mid"
        if raw.get("gate") is not None or raw.get("passing_criteria") is not None:
            auto.append(
                {"kind": "gate", "node": nid,
                 "detail": "the planner's gate was ignored: Studio sets every gate"}
            )  # fmt: skip
        nodes.append({
            "id": nid,
            "title": _short(str(raw.get("title") or nid), 120),
            "kind": kind,
            "scope": {"kind": skind, **({"area": area_id} if area_id else {}), "rules": rules},
            "declared": deps,
            "tier": tier,
            "touches": _touches(raw.get("touches"), nid, auto),
            "why": _short(str(raw.get("why") or ""), 300),
        })  # fmt: skip
    if issues:
        return {"ok": False, "issues": issues, "auto_edits": auto}
    ids = [n["id"] for n in nodes]
    by = {n["id"]: n for n in nodes}
    for kind in ("scaffold", "integration"):
        k = [n["id"] for n in nodes if n["kind"] == kind]
        if len(k) > 1:
            issues.append(f"at most one {kind} node (got {', '.join(k)})")
    for n in nodes:
        for d, _why in n["declared"]:
            if d not in by:
                issues.append(f"node {n['id']} depends on {d}, which is not a node")
            elif d == n["id"]:
                issues.append(f"node {n['id']} depends on itself")
            elif by[d]["kind"] == "integration":
                issues.append(f"node {n['id']} depends on the integration node {d}")
        if n["kind"] == "scaffold" and n["declared"]:
            issues.append(f"the scaffold {n['id']} cannot depend on other nodes")
    # coverage: each plan rule exactly once among the non-integration nodes
    cover: dict[str, list[str]] = {}
    for n in nodes:
        if n["kind"] == "integration":
            continue
        for r in n["scope"]["rules"]:
            cover.setdefault(r, []).append(n["id"])
    for r in plan_rules:
        got = cover.get(r, [])
        if not got:
            issues.append(f"{r} is covered by no node")
        elif len(got) > 1:
            issues.append(f"{r} is covered by {len(got)} nodes ({', '.join(got)})")
    if issues:
        return {"ok": False, "issues": issues, "auto_edits": auto}
    declared = {n["id"]: [d for d, _ in n["declared"]] for n in nodes}
    c = _cycle(ids, declared)
    if c:
        return {"ok": False, "issues": [f"the dependencies have a cycle: {' -> '.join(c)}"],
                "auto_edits": auto}  # fmt: skip
    # Studio's structure: the scaffold first, one integration node last
    edges: list[dict[str, Any]] = []
    deps: dict[str, list[str]] = {n["id"]: [] for n in nodes}

    def add(a: str, b: str, kind: str, why: str) -> None:
        if a not in deps[b]:
            deps[b].append(a)
            edges.append({"from": a, "to": b, "why": why, "kind": kind, "auto": kind != "declared"})

    for n in nodes:
        for d, why in n["declared"]:
            add(d, n["id"], "declared", why)
    integ = next((n for n in nodes if n["kind"] == "integration"), None)
    if integ is None:
        integ = {
            "id": "integration" if "integration" not in by else "integration-node",
            "title": "Integration: the full gate", "kind": "integration",
            "scope": {"kind": "all", "rules": list(plan_rules)}, "declared": [],
            "tier": "frontier", "touches": {k: [] for k in TOUCH_KEYS},
            "why": "added by Studio: every plan ends with the full gate",
        }  # fmt: skip
        nodes.append(integ)
        ids.append(integ["id"])
        by[integ["id"]] = integ
        deps[integ["id"]] = []
        auto.append({"kind": "node", "node": integ["id"],
                     "detail": "added the integration node (every plan ends with it)"})  # fmt: skip
    scaffold = next((n for n in nodes if n["kind"] == "scaffold"), None)
    if scaffold is not None:
        for n in nodes:
            if n is not scaffold and n is not integ and not deps[n["id"]]:
                add(scaffold["id"], n["id"], "scaffold", "the scaffold runs first")
                auto.append({"kind": "edge", "from": scaffold["id"], "to": n["id"],
                             "detail": "the scaffold runs first"})  # fmt: skip
    used = {d for n in nodes for d in deps[n["id"]]}
    for n in nodes:
        if n is not integ and n["id"] not in used and n["id"] not in deps[integ["id"]]:
            add(n["id"], integ["id"], "integration", "the integration node runs last")
            auto.append({"kind": "edge", "from": n["id"], "to": integ["id"],
                         "detail": "the integration node runs last"})  # fmt: skip
    # levels: overlap serialisation and the max_parallel cap, to a fixpoint
    cap = max(1, min(MAX_PARALLEL, int(max_parallel)))
    order = {nid: i for i, nid in enumerate(ids)}
    min_level: dict[str, int] = dict.fromkeys(ids, 0)
    serial: dict[str, dict[str, Any]] = {}

    def levels_of() -> dict[str, int]:
        lv: dict[str, int] = {}

        def lvl(x: str) -> int:
            if x not in lv:
                lv[x] = max([min_level[x], *(lvl(d) + 1 for d in deps[x])])
            return lv[x]

        for x in ids:
            lvl(x)
        return lv

    for _ in range(len(ids) * len(ids) + len(ids) + 4):
        lv = levels_of()
        by_level: dict[int, list[str]] = {}
        for x in sorted(ids, key=lambda y: order[y]):
            by_level.setdefault(lv[x], []).append(x)
        changed = False
        for _L, xs in sorted(by_level.items()):
            for i, a in enumerate(xs):
                for b in xs[i + 1 :]:
                    o = overlap(by[a]["touches"], by[b]["touches"])
                    if _any(o):
                        add(a, b, "serialised", "overlapping write sets: " + _overlap_text(o))
                        serial[b] = {"node": a, "overlap": o}
                        auto.append({"kind": "serialise", "from": a, "to": b, "overlap": o,
                                     "detail": f"{a} and {b} write the same "
                                     f"{_overlap_text(o)}: {b} runs after {a}"})  # fmt: skip
                        changed = True
                        break
                if changed:
                    break
            if changed:
                break
        if changed:
            continue
        for L, xs in sorted(by_level.items()):
            if len(xs) > cap:
                for x in xs[cap:]:
                    min_level[x] = L + 1
                    detail = f"max_parallel {cap}: {x} moved to level {L + 1}"
                    auto.append({"kind": "level", "node": x, "from": L, "to": L + 1,
                                 "detail": detail})  # fmt: skip
                changed = True
                break
        if not changed:
            break
    lv = levels_of()
    n_levels = max(lv.values()) + 1 if lv else 0
    levels = [[x for x in ids if lv[x] == L] for L in range(n_levels)]
    levels = [xs for xs in levels if xs]
    # compact: a level emptied by the cap moves nothing; renumber
    renum = {x: i for i, xs in enumerate(levels) for x in xs}
    out_nodes = []
    for n in sorted(nodes, key=lambda y: (renum[y["id"]], order[y["id"]])):
        nid = n["id"]
        targets = list(dict.fromkeys(t for r in n["scope"]["rules"] for t in links.get(r, [])))
        gate = {
            "kind": "full" if n["kind"] == "integration" else "scoped",
            "criterion": INTEGRATION_GATE if n["kind"] == "integration" else NODE_GATE,
            "rules": list(n["scope"]["rules"]) if n["kind"] != "integration" else "all",
            "checks": targets if n["kind"] != "integration" else "all",
            "regressions": "none",
            "decided_by": "studio",
        }
        item = {
            "id": nid,
            "title": n["title"],
            "kind": n["kind"],
            "scope": n["scope"],
            "depends_on": list(deps[nid]),
            "why": n["why"],
            "tier": n["tier"],
            "model": overrides.get(nid) or models.get(n["tier"]) or models["mid"],
            "touches": n["touches"],
            "gate": gate,
            "level": renum[nid],
        }
        if nid in serial:
            item["serialised_because"] = serial[nid]
        out_nodes.append(item)
    edges.sort(key=lambda e: (renum[e["to"]], order[e["to"]], order[e["from"]]))
    return {"ok": True, "issues": [], "auto_edits": auto, "nodes": out_nodes, "edges": edges,
            "levels": levels}  # fmt: skip


def _overlap_text(o: Mapping[str, list[str]]) -> str:
    return "; ".join(f"{k} {', '.join(v)}" for k, v in o.items() if v)


# ---- the planner -----------------------------------------------------------------------------


def epg_prompt() -> str:
    """The planner's system prompt (``prompts/epg_plan.md`` without its comment header)."""
    text = PROMPT_FILE.read_text(encoding="utf-8")
    return re.sub(r"^<!--.*?-->\s*", "", text, flags=re.S)


def build_planner_argv(*, claude_bin: str, text: str, model: str, system: str) -> list[str]:
    """The planner's ``claude`` argv: read-only built-in tools, no MCP server, no settings,
    no session persistence. The message goes last behind ``--``."""
    return [
        claude_bin, "-p", "--output-format", "stream-json", "--verbose",
        "--no-session-persistence",
        "--setting-sources", "", "--disable-slash-commands",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--tools", ",".join(PLANNER_TOOLS),
        "--permission-mode", "dontAsk",
        "--allowedTools", ",".join(PLANNER_TOOLS),
        "--disallowedTools", ",".join(PLANNER_DENIED),
        "--append-system-prompt", system,
        "--model", model,
        "--", text,
    ]  # fmt: skip


def _area_tables(text: str, tables: Sequence[Mapping[str, Any]]) -> list[str]:
    out = []
    low = text.lower()
    for t in tables:
        for name in (t.get("name"), t.get("sql")):
            if name and re.search(rf"\b{re.escape(str(name).lower())}\b", low):
                out.append(str(t.get("name")))
                break
    return out


def planner_prompt(
    *,
    project: str,
    scope: Mapping[str, Any],
    areas: Sequence[Mapping[str, Any]],
    trace: Mapping[str, Any],
    data_model: Mapping[str, Any] | None,
    file_tree: Sequence[str],
    max_parallel: int,
    failing: Mapping[str, int | None] | None = None,
    issues: Sequence[str] = (),
    previous: Any = None,
    feedback: str | None = None,
    built: Mapping[str, bool | None] | None = None,
) -> str:
    """The planner's message: the areas of the scope (rules clipped, scenario and failing
    counts, whether the area is already built on the target branch -- every in-scope check
    passed at its tip, from the per-tip results; null: never gated -- endpoints and tables
    their rules mention), the data model, the file tree."""
    texts = {str(r.get("id")): str(r.get("text") or "") for r in trace.get("rules") or []}
    tables = list((data_model or {}).get("tables") or [])
    keep = set(scope["rules"])
    info = []
    for a in areas:
        if a["id"] == areas_mod.ALL or a["id"] not in scope["areas"]:
            continue
        rules = [r for r in a["rules"] if r in keep]
        joined = " ".join(texts.get(r, "") for r in rules)
        targets = list(a.get("targets") or [])
        info.append({
            "id": a["id"],
            "title": a["title"],
            "rules": [{"id": r, "text": _short(texts.get(r, ""), CLIP)} for r in rules],
            "rule_count": len(rules),
            "scenarios": sum(1 for t in targets if t.startswith("scenario:")),
            "checks": len(targets),
            "failing": (failing or {}).get(a["id"]),
            "built": (built or {}).get(a["id"]),
            "endpoints": list(dict.fromkeys(f"{m} {p}" for m, p in _ENDPOINT.findall(joined))),
            "tables": _area_tables(joined, tables),
            "huge": len(rules) > SPLIT_RULES
            or sum(1 for t in targets if t.startswith("scenario:")) > SPLIT_SCENARIOS,
        })  # fmt: skip
    dm = [
        {"name": t.get("name"), "sql": t.get("sql"),
         "columns": [c.get("name") for c in t.get("columns") or [] if isinstance(c, Mapping)]}
        for t in tables
    ]  # fmt: skip
    out = [
        "# Plan request",
        "",
        f"Project: {project}. Scope: {scope['title']} ({len(scope['rules'])} rule(s)). "
        f"Studio runs at most {max_parallel} node(s) of one level at a time.",
        "",
        "`built: true` on an area means every check of it already passes on the target "
        "branch: plan no work for it (Studio drops a node with nothing failing in its scope).",
        "",
        "## Areas of the scope (JSON)",
        "",
        json.dumps(info, indent=1, ensure_ascii=False),
        "",
        "## Data model (tables and columns)",
        "",
        json.dumps(dm, ensure_ascii=False) if dm else "(no data model in the spec)",
        "",
        f"## Repository files (git ls-files; {len(file_tree)} shown)",
        "",
        "\n".join(file_tree) or "(empty)",
        "",
    ]
    if issues:
        out += ["## Your previous plan was invalid; fix these and answer again", ""]
        out += [f"- {i}" for i in issues]
        out += ["", "Previous plan:", json.dumps(previous, ensure_ascii=False), ""]
    if feedback:
        out += ["## The owner's feedback on the current plan", "", feedback.strip(), ""]
        if previous is not None:
            out += ["Current plan:", json.dumps(previous, ensure_ascii=False), ""]
    out.append("Answer with the JSON object only.")
    return "\n".join(out)


def parse_planner_text(text: str) -> dict[str, Any]:
    """The JSON object of the planner's answer (a code fence or prose around it is
    tolerated). Raises ValueError."""
    s = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.S)
    if m:
        s = m.group(1)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("the planner answered no JSON object")
    try:
        data = json.loads(s[i : j + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"the planner's JSON does not parse: {e}") from None
    if not isinstance(data, dict):
        raise ValueError("the planner's answer is not a JSON object")
    return data


# ---- resume (API v1.10) ----------------------------------------------------------------------


def land_waiting(rec: Mapping[str, Any]) -> dict[str, Any] | None:
    """The level run that passed its full gate but whose gated tip was never added to the
    target branch (``branch_checked_out``, ``branch_moved``, ``tree_dirty``), when it is
    the latest run of its level and has not landed since; else None."""
    latest: dict[int, Mapping[str, Any]] = {}
    for lr in rec.get("level_runs") or []:
        latest[int(lr.get("level", -1))] = lr
    nodes = {n["id"]: n for n in rec.get("nodes") or []}
    for L in sorted(latest):
        lr = latest[L]
        err = lr.get("error") or {}
        gate = lr.get("gate") or {}
        if (
            lr.get("status") == "failed"
            and lr.get("tip")
            and not lr.get("regressions")
            and not gate.get("regressions")
            and err.get("kind") in LAND_KINDS
            and not lr.get("landed_at")
            and lr.get("merged")
            and not all(nodes.get(i, {}).get("merged") for i in lr["merged"])
        ):
            return dict(lr)
    return None


def attribute_failures(
    targets: Sequence[str],
    merged: Sequence[Mapping[str, Any]],
    node_targets: Mapping[str, Sequence[str]],
    node_files: Mapping[str, Sequence[str]],
    finding_files: Mapping[str, Sequence[str]],
) -> tuple[dict[str, list[str]], list[str], dict[str, dict[str, Any]]]:
    """Who repairs each check a level gate failed (API v1.11): (a) the merged nodes whose
    scope links to it (the integration node only when no other does); else (b) the merged
    nodes whose commit touched the finding's file (a path equal to it or a directory above
    it); else (c) nobody: a dedicated repair node. Answers ``(by node id -> targets, in
    the merged order; unowned targets; target -> {by: scope|files|repair_node, nodes})``."""
    by: dict[str, list[str]] = {}
    unowned: list[str] = []
    how: dict[str, dict[str, Any]] = {}
    order = [str(n["id"]) for n in merged]
    kinds = {str(n["id"]): n.get("kind") for n in merged}
    for t in targets:
        hit = [
            i for i in order if t in set(node_targets.get(i) or ()) and kinds[i] != "integration"
        ]
        why = "scope"
        if not hit:
            hit = [i for i in order if t in set(node_targets.get(i) or ())]
        if not hit:
            paths = [str(p).lstrip("./") for p in finding_files.get(t) or ()]
            hit = [
                i
                for i in order
                if any(_path_overlap(str(f), p) for f in node_files.get(i) or () for p in paths)
            ]
            why = "files"
        if not hit:
            unowned.append(t)
            how[t] = {"by": "repair_node", "nodes": []}
            continue
        how[t] = {"by": why, "nodes": hit}
        for i in hit:
            by.setdefault(i, []).append(t)
    return {i: by[i] for i in order if i in by}, unowned, how


def _landable(outcome: Mapping[str, Any] | None) -> bool:
    """A level's outcome whose gated tip may land (a failing outcome also names its tip)."""
    return outcome is not None and bool(outcome.get("tip")) and not outcome.get("failed")


def repair_waiting(rec: Mapping[str, Any]) -> dict[str, Any] | None:
    """The latest level run whose repair attempts ran out (``repair_pending``) and that a
    later run has not superseded: what ``resume.kind`` ``repair`` goes on from."""
    latest: dict[int, Mapping[str, Any]] = {}
    for lr in rec.get("level_runs") or []:
        latest[int(lr.get("level", -1))] = lr
    for L in sorted(latest):
        lr = latest[L]
        if lr.get("repair_pending") and not lr.get("superseded") and not lr.get("landed"):
            return lr  # type: ignore[return-value]
    return None


def final_status(rec: Mapping[str, Any]) -> str:
    """A plan's end state from its nodes: passed (all), partial (some), failed (none)."""
    nodes = rec.get("nodes") or []
    if nodes and all(n["status"] == "passed" for n in nodes):
        return "passed"
    return "partial" if any(n["status"] == "passed" for n in nodes) else "failed"


def _ids(names: Sequence[str]) -> str:
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def resume_view(
    rec: Mapping[str, Any],
    *,
    checked_out_at: str | None = None,
    owner_worktree: str | None = None,
    fast_forward: bool | None = None,
    max_attempts: int = 3,
    repair_attempts: int = 2,
) -> dict[str, Any]:
    """``resume`` of a plan record (API v1.10; ``kind`` ``repair``, API v1.11): whether
    ``POST .../run`` (or ``.../land``) can go on from where the plan stopped, which nodes
    it keeps and which it runs, and one plain sentence for the owner. `checked_out_at`:
    where the target branch is checked out now (None: nowhere); `owner_worktree`: the
    owner's main worktree; `fast_forward`: whether the waiting level's tip still
    fast-forwards the target (None: unknown)."""
    status = str(rec.get("status"))
    nodes = list(rec.get("nodes") or [])
    by = {n["id"]: n for n in nodes}
    order = [i for lvl in rec.get("levels") or [] for i in lvl] or [n["id"] for n in nodes]
    target = rec.get("target_branch") or "the target branch"
    lw = land_waiting(rec)
    landing = set(lw["merged"]) if lw else set()
    will_skip = [i for i in order if i in by and (by[i]["status"] == "passed" or i in landing)]
    will_run = [i for i in order if i in by and i not in will_skip]
    failed = [i for i in will_run if by[i]["status"] in ("gave_up", "merge_conflict", "regressed")]
    out: dict[str, Any] = {"available": False, "kind": None, "from_level": None,
                           "will_skip": will_skip, "will_run": will_run, "note": ""}  # fmt: skip
    if status not in RESUMABLE:
        notes = {
            "passed": "Nothing to resume: every node passed and landed.",
            "running": "The plan is running.",
            "planning": "The plan is being planned.",
            "ready": "The plan has not run yet: approve it, then run it.",
            "approved": "The plan has not run yet: run it.",
        }
        out["note"] = notes.get(status, "")
        return out
    if not nodes or not rec.get("approved"):
        out["note"] = ("The plan stopped before it was approved: revise it (feedback plans it "
                       "again), approve it, then run it.")  # fmt: skip
        return out
    kept = len(will_skip) - len(landing)
    if kept == 0:
        kept_txt = "no node had passed yet"
    else:
        kept_txt = f"the {kept} node{'s' if kept != 1 else ''} that passed"
        kept_txt += " are kept" if kept != 1 else " is kept"
    if lw is not None:
        L = int(lw["level"])
        err = lw.get("error") or {}
        head = f"Level {L} passed its check but could not be added to {target}"
        if checked_out_at is not None:
            mine = owner_worktree is not None and checked_out_at == owner_worktree
            where = "your working copy" if mine else checked_out_at
            note = (f"{head} because that branch is checked out in {where}. Switch it away "
                    "and press Land.")  # fmt: skip
        elif err.get("kind") == "tree_dirty":
            note = (f"{head} because your working copy had uncommitted changes. Press Land "
                    "once it is clean.")  # fmt: skip
        elif fast_forward is False:
            note = (f"{head}, and {target} has moved since. Land merges the level's nodes onto "
                    "its new tip and re-runs the full check; no agent runs again.")  # fmt: skip
        elif err.get("kind") == "branch_checked_out":
            note = (f"{head} because that branch was checked out at the time. It is free now: "
                    "press Land.")  # fmt: skip
        else:
            note = f"{head} because {target} moved during the run. Press Land to add it now."
        out.update(available=True, kind="land", from_level=L, note=note)
        return out
    rw = repair_waiting(rec)
    if rw is not None:
        L = int(rw["level"])
        names = [i for i in rw.get("merged") or [] if i in by]
        err = rw.get("error") or {}
        passes = int(rw.get("repair_pass") or 1)
        targets = ", ".join(err.get("targets") or rw.get("regressions") or [])
        if passes >= max_attempts:
            out.update(kind="repair", from_level=L,
                       note=(f"Level {L} still fails after {passes} repair passes (the cap is "
                             f"[rebuild] max_attempts = {max_attempts}): {targets}. Revise the "
                             "plan or raise the cap to try again."))  # fmt: skip
            return out
        note = (f"Level {L} still fails after its repairs (node {_ids(names)}): {targets}. Run "
                f"repairs it again (up to {repair_attempts} attempt(s)) from its merged nodes; "
                f"no node starts over, and {kept_txt}"
                + (" and not run again." if kept else "."))  # fmt: skip
        out.update(available=True, kind="repair", from_level=L, note=note)
        return out
    levels = [int(by[i].get("level", 0)) for i in will_run]
    L = min(levels) if levels else None
    if not will_run:
        out["note"] = "Nothing to resume: every node passed."
        return out
    if failed:
        capped = [i for i in failed if int(by[i].get("attempts") or 0) >= max_attempts]
        if capped:
            a = max(int(by[i].get("attempts") or 0) for i in capped)
            out.update(kind="retry", from_level=L,
                       note=(f"Node {_ids(capped)} already had {a} attempts (the cap is "
                             f"[rebuild] max_attempts = {max_attempts}); revise the plan or "
                             "raise the cap to try again."))  # fmt: skip
            return out
        what = {"gave_up": "gave up", "merge_conflict": "did not merge cleanly",
                "regressed": "broke a check that passed before"}  # fmt: skip
        parts = [f"{i} {what[by[i]['status']]}" for i in failed]
        nxt = max(int(by[i].get("attempts") or 0) for i in failed) + 1
        note = (f"Node {_ids(parts)}. Run retries {'it' if len(failed) == 1 else 'them'} "
                f"(attempt {nxt} of {max_attempts}) with the earlier findings, from level {L}; "
                f"{kept_txt}.")  # fmt: skip
        out.update(available=True, kind="retry", from_level=L, note=note)
        return out
    if status == "interrupted":
        running = [i for i in will_run if by[i]["status"] == "interrupted"]
        note = (
            f"Studio stopped while level {L} was running"
            + (f" (node {_ids(running)})" if running else "")
            + f". Run continues from level {L}; {kept_txt}"
            + (" and not run again." if kept else ".")
        )
    elif status == "stopped":
        note = f"The plan was stopped at level {L}. Run continues from there; {kept_txt}."
    else:
        note = f"Run continues from level {L}; {kept_txt}."
    out.update(available=True, kind="continue", from_level=L, note=note)
    return out


# ---- the plan record -------------------------------------------------------------------------


@dataclass
class _Plan:
    record: dict[str, Any]
    scope: dict[str, Any]
    branch: Any
    confirm: bool
    models: dict[str, str]
    planner: str  # agent | fallback (the request)
    doc: dict[str, Any] | None = None  # the declared planner document
    overrides: dict[str, str] = field(default_factory=dict)  # node -> model (owner edits)
    stop: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    proc: subprocess.Popen[str] | None = None
    irun: Any = None
    created_target: bool = False
    final: tuple[str, dict[str, Any] | None] | None = None  # published once the run is over
    argvs: list[list[str]] = field(default_factory=list)
    t_run: float = 0.0
    # resume (API v1.10)
    land_only: bool = False  # POST .../land that must re-merge: no level runs after it
    confirm_land: bool = False  # the run/land request confirmed the owner's checked-out branch
    run_started_ts: float = 0.0  # wall clock, so a restart can add the lost run time


def _usage_totals() -> dict[str, Any]:
    return {"tokens_in": 0, "tokens_out": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0, "cost_usd_list_estimate": 0.0, "wall_s": 0.0,
            "rounds": 0, "lines_changed": 0}  # fmt: skip


def _node_usage(u: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tokens_in": int(u.get("input_tokens") or 0)
        + int(u.get("cache_read_input_tokens") or 0)
        + int(u.get("cache_creation_input_tokens") or 0),
        "tokens_out": int(u.get("output_tokens") or 0),
        "cache_read_input_tokens": int(u.get("cache_read_input_tokens") or 0),
        "cache_creation_input_tokens": int(u.get("cache_creation_input_tokens") or 0),
        "cost_usd_list_estimate": round(float(u.get("cost_usd_list_estimate") or 0.0), 6),
    }


def _sum_into(total: dict[str, Any], u: Mapping[str, Any]) -> None:
    for k, v in u.items():
        if k in total and isinstance(v, int | float):
            total[k] = round(total[k] + v, 6) if isinstance(total[k], float) else total[k] + v


class PlanManager:
    """Every build plan of one project (one Studio). One runs at a time."""

    def __init__(
        self,
        rebuilder: Rebuilder,
        *,
        data_model: Callable[[], Mapping[str, Any] | None] | None = None,
        env: Mapping[str, str] | None = None,
        log: Callable[[str], None] | None = None,
        store: StateStore | None = None,
    ) -> None:
        self.rb = rebuilder
        self.cfg = rebuilder.cfg
        self.data_model = data_model or (lambda: None)
        self._env = env
        self.log = log or (lambda _m: None)
        self.lock = rebuilder.lock  # one lock for builds and plans (see Rebuilder)
        self.plans: dict[str, _Plan] = {}
        self.next_id = 1
        self.store = store
        if store is not None:
            self._load()

    # -- durable state (API v1.10, lattice/studio/state.py)
    def _alloc(self) -> str:
        if self.store is not None:
            return f"p{self.store.allocate('plans')}"
        pid = f"p{self.next_id}"
        self.next_id += 1
        return pid

    def _private(self, plan: _Plan) -> dict[str, Any]:
        return {"scope": plan.scope, "branch": plan.branch, "confirm": plan.confirm,
                "models": plan.models, "planner": plan.planner, "doc": plan.doc,
                "overrides": plan.overrides, "created_target": plan.created_target,
                "run_started_ts": plan.run_started_ts}  # fmt: skip

    def _save(self, plan: _Plan) -> None:
        """Write the plan through to the store: on every status transition and after every
        level, node and round event."""
        if self.store is None:
            return
        with self.lock:
            pid = plan.record["id"]
            if self.plans.get(pid) is not plan:
                return
            try:
                self.store.save("plans", pid, plan.record, self._private(plan))
            except OSError as exc:
                self.log(f"plan {pid}: the state file could not be written: {exc}")

    def _event(
        self, plan: _Plan, event: str, node: dict[str, Any] | None = None, repair: bool = False
    ) -> None:
        """With the lock held: one timeline entry (the plan's, and the node's); a repair
        loop's entries carry ``repair: true`` (API v1.11)."""
        entry: dict[str, Any] = {"at": _now(), "event": event}
        if repair:
            entry["repair"] = True
        plan.record.setdefault("timeline", []).append(entry)
        if node is not None:
            node.setdefault("timeline", []).append(dict(entry))

    def _load(self) -> None:
        """Every plan of the store; one that was planning or running when Studio stopped
        is reconciled to ``interrupted`` (completed levels and nodes kept)."""
        assert self.store is not None
        for rec, priv in self.store.load("plans"):
            for k, v in (("timeline", []), ("attempts", rec.get("runs") or 0), ("pids", []),
                         ("interrupted", False), ("interrupted_at", None),
                         ("interrupted_reason", None), ("anchors", {}), ("level_runs", []),
                         ("nodes", []), ("levels", []), ("warnings", []), ("runs", 0),
                         ("totals", _usage_totals()), ("spec_drift", None)):  # fmt: skip
                rec.setdefault(k, copy.deepcopy(v))
            for n in rec["nodes"]:
                n.setdefault("attempts", len(n.get("builds") or []))
                n.setdefault("timeline", [])
            scope = priv.get("scope") or self._scope_of(rec)
            plan = _Plan(record=rec, scope=scope, branch=priv.get("branch", rec.get("branch")),
                         confirm=bool(priv.get("confirm")),
                         models=dict(priv.get("models") or rec.get("models") or {}),
                         planner=str(priv.get("planner") or "agent"), doc=priv.get("doc"),
                         overrides=dict(priv.get("overrides") or {}),
                         created_target=bool(priv.get("created_target")),
                         run_started_ts=float(priv.get("run_started_ts") or 0.0))  # fmt: skip
            plan.done.set()
            self.plans[str(rec["id"])] = plan
            if rec.get("status") in TRANSIENT:
                self._reconcile(plan)

    def _scope_of(self, rec: Mapping[str, Any]) -> dict[str, Any]:
        sc = dict(rec.get("scope") or {"kind": "all", "areas": [], "rules": [], "title": "All"})
        kind = sc.get("kind")
        if kind == "areas":
            sc["slug"] = areas_mod.slug("areas-" + "-".join(sc.get("areas") or []), 80)
        elif kind == "rules":
            sc["slug"] = areas_mod.scope_slug({"kind": "rules", "rules": sc.get("rules") or []})
        else:
            sc["slug"] = "all"
        return sc

    def _reconcile(self, plan: _Plan) -> None:
        rec = plan.record
        killed = reap_orphans(rec.get("pids") or [], self.cfg.stop_grace, self.log)
        was = rec["status"]
        now = _now()
        rec.update(status="interrupted", interrupted=True, interrupted_at=now,
                   interrupted_reason="studio_restarted", pids=[])  # fmt: skip
        rec["finished"] = rec.get("finished") or now
        if was == "running" and plan.run_started_ts:
            lost = max(0.0, time.time() - plan.run_started_ts)
            rec["totals"]["wall_s"] = round(float(rec["totals"].get("wall_s") or 0.0) + lost, 2)
            plan.run_started_ts = 0.0
        for n in rec["nodes"]:
            if n["status"] == "running":
                n["status"] = "interrupted"
                n.pop("live", None)
                n["timeline"].append({"at": now, "event": "interrupted: Studio restarted"})
        for lr in rec["level_runs"]:
            if lr.get("status") == "running":
                lr["status"] = "interrupted"
                lr["finished"] = now
        self._reconcile_repair(rec, now)
        if killed:
            rec["orphans_killed"] = killed
        msg = f"interrupted while {was}: Studio restarted"
        if killed:
            msg += f" ({len(killed)} orphan process group(s) terminated)"
        rec["timeline"].append({"at": now, "event": msg})
        self.log(f"plan {rec['id']}: {msg}")
        self._save(plan)

    @staticmethod
    def _reconcile_repair(rec: dict[str, Any], now: str) -> None:
        """A Studio that died in the middle of a level repair (API v1.11) left nodes
        ``repairing`` and their level run ``failed``: neither is "running", so the plain
        reconciliation would restart those nodes from round 1. Mark the level as waiting for
        a repair pass instead (``repair_pending``, the state an exhausted repair leaves), so
        Run repairs it again from its merged nodes -- their sessions continue, no completed
        round runs again."""
        repairing = {n["id"] for n in rec["nodes"] if n["status"] == "repairing"}
        if not repairing:
            return
        latest: dict[int, dict[str, Any]] = {}
        for lr in rec["level_runs"]:
            latest[int(lr.get("level", -1))] = lr
        for L in sorted(latest):
            lr = latest[L]
            merged = [i for i in lr.get("merged") or [] if i in repairing]
            if not merged or lr.get("superseded") or lr.get("landed"):
                continue
            targets = list(lr.get("regressions") or (lr.get("gate") or {}).get("failing") or [])
            lr["repair_pending"] = True
            lr["finished"] = lr.get("finished") or now
            lr["error"] = {"kind": "repair_interrupted", "level": L, "nodes": merged,
                           "targets": targets,
                           "message": f"Studio stopped while level {L} was being repaired "
                           f"(node {', '.join(merged)})"}  # fmt: skip
        for n in rec["nodes"]:
            if n["id"] in repairing:
                n["status"] = "interrupted"
                n.pop("live", None)
                n.setdefault("timeline", []).append(
                    {"at": now, "event": "interrupted while repairing: Studio restarted",
                     "repair": True})  # fmt: skip

    def _pid_sink(self, plan: _Plan) -> Callable[[dict[str, Any], bool], None]:
        """With the lock held (the Rebuilder calls it so): the plan's own child processes
        (its integration gates) in its record, so a restart can terminate orphans."""

        def sink(info: dict[str, Any], add: bool) -> None:
            pids = plan.record.setdefault("pids", [])
            if add:
                pids.append(info)
            else:
                plan.record["pids"] = [p for p in pids if p.get("pid") != info.get("pid")]
            self._save(plan)

        return sink

    def _anchor(self, plan: _Plan, name: str, sha: str) -> None:
        """Pin `sha` as ``refs/lattice/plans/<plan>/<name>`` so the plan's work outlives
        its worktrees and git gc (removed only by ``DELETE ...?forget=true``)."""
        ref = f"{REF_PREFIX}/{plan.record['id']}/{name}"
        r = _git(self.rb.repo(), "update-ref", ref, sha)
        with self.lock:
            if r.returncode == 0:
                plan.record.setdefault("anchors", {})[name] = sha
            else:
                plan.record["warnings"].append(f"could not pin {ref}: {_short(r.stderr, 200)}")

    # -- POST /api/plan
    def create(self, body: Mapping[str, Any]) -> dict[str, str]:
        ok, why = self.rb.available()
        if not ok:
            raise PlanError(503, f"build plans are not available: {why}")
        unknown = set(body) - {"scope", "branch", "max_parallel", "planner", "models",
                               "confirm_unsafe_branch"}  # fmt: skip
        if unknown:
            raise PlanError(422, f"unknown field(s): {', '.join(sorted(unknown))}")
        if "scope" not in body:
            raise PlanError(422, "scope is required")
        areas = self.rb.area_list()
        scope = resolve_plan_scope(body.get("scope"), areas)
        mp = body.get("max_parallel", self.cfg.max_parallel)
        if isinstance(mp, bool) or not isinstance(mp, int) or not 1 <= mp <= MAX_PARALLEL:
            raise PlanError(422, f"max_parallel must be 1 to {MAX_PARALLEL}")
        planner = body.get("planner", "agent")
        if planner not in ("agent", "fallback"):
            raise PlanError(422, 'planner must be "agent" or "fallback"')
        models = self.cfg.models
        m = body.get("models")
        if m is not None:
            if (
                not isinstance(m, Mapping)
                or set(m) - set(TIERS)
                or not all(isinstance(v, str) and v.strip() for v in m.values())
            ):
                raise PlanError(422, "models must map fast, mid and frontier to model names")
            models.update({k: str(v) for k, v in m.items()})
        confirm = body.get("confirm_unsafe_branch", False)
        if not isinstance(confirm, bool):
            raise PlanError(422, "confirm_unsafe_branch must be true or false")
        target = self._check_branch(body.get("branch"), confirm)
        drift = self._drift_of(target["name"], None if not target["create"] else target["tip"])
        with self.lock:
            pid = self._alloc()
            record: dict[str, Any] = {
                "id": pid,
                "project": self.rb.name,
                "status": "planning",
                "source": None,
                "fallback_reason": None,
                "version": 0,
                "scope": {k: scope[k] for k in ("kind", "areas", "rules", "title")},
                "target_branch": target["name"],
                "branch": body.get("branch"),
                "max_parallel": mp,
                "models": dict(models),
                "planner": {
                    "kind": planner if planner == "fallback" else self.cfg.planner,
                    "model": self.cfg.planner_model,
                    "attempts": 0,
                    "usage": _node_usage({}),
                    "wall_s": 0.0,
                    "error": None,
                },  # fmt: skip
                "nodes": [],
                "edges": [],
                "levels": [],
                "validation": {"ok": False, "issues": [], "auto_edits": []},
                "level_gate": LEVEL_GATE,
                "level_runs": [],
                "baseline": None,
                "runs": 0,
                "created": _now(),
                "approved": None,
                "started": None,
                "finished": None,
                "target_tip": None,
                "created_branch": False,
                "owner_head": None,
                "error": None,
                "warnings": [],
                "metrics": None,
                "totals": _usage_totals(),
                "attempts": 0,
                "timeline": [{"at": _now(), "event": "planning"}],
                "interrupted": False,
                "interrupted_at": None,
                "interrupted_reason": None,
                "anchors": {},
                "pids": [],
                "spec_drift": None,
            }
            self._set_drift(record, drift)
            plan = _Plan(record=record, scope=scope, branch=body.get("branch"), confirm=confirm,
                         models=dict(models), planner=planner)  # fmt: skip
            self.plans[pid] = plan
            self._save(plan)
            plan.thread = threading.Thread(target=self._plan_thread, args=(plan,), daemon=True,
                                           name=f"studio-plan-{pid}")  # fmt: skip
            plan.thread.start()
        self.log(f"plan {pid}: planning {scope['title']} ({len(scope['rules'])} rules)")
        return {"id": pid, "status": "planning"}

    def _check_branch(self, spec: Any, confirm: bool) -> dict[str, Any]:
        try:
            return self.rb._target(self.rb.repo(), spec, confirm, dry=True)
        except RebuildError as e:
            raise PlanError(e.status, e.detail, e.code) from None

    # -- spec drift (API v1.12)
    def _drift_of(self, name: str, tip: str | None = None) -> dict[str, Any] | None:
        """The target's ``spec_drift`` (`tip`: the commit a target still to be created
        starts from); None when it cannot be told."""
        try:
            return self.rb.spec_drift(name, tip)
        except (RebuildError, OSError, subprocess.SubprocessError):
            return None

    def _set_drift(self, rec: dict[str, Any], drift: dict[str, Any] | None) -> bool:
        """With the lock held (or on a record not shared yet): ``spec_drift`` and its
        warning; True when either changed."""
        before = (rec.get("spec_drift"), list(rec.get("warnings") or []))
        warnings = [w for w in rec.get("warnings") or [] if not str(w).startswith(DRIFT_WARNING)]
        if drift is not None:
            try:
                source = _current_branch(self.rb.repo()) or "HEAD"
            except RebuildError:
                source = "HEAD"
            warnings.append(f"{DRIFT_WARNING}{source} differs from {rec.get('target_branch')} "
                            f"in {', '.join(drift['files'])}: sync it first")  # fmt: skip
        rec["spec_drift"] = drift
        rec["warnings"] = warnings
        return before != (drift, warnings)

    def _refresh_drift(self, plan: _Plan) -> None:
        """With the lock held: ``spec_drift`` as the repository is now, unless the plan is
        running (a running plan's target moves under it)."""
        rec = plan.record
        if rec.get("status") == "running" or not rec.get("target_branch"):
            return
        name = str(rec["target_branch"])
        try:
            exists = _git(self.rb.repo(), "rev-parse", "-q", "--verify",
                          f"refs/heads/{name}").returncode == 0  # fmt: skip
        except (RebuildError, OSError, subprocess.SubprocessError):
            return
        tip = None if exists else self._target_tip(plan)
        if not exists and tip is None:
            return
        if self._set_drift(rec, self._drift_of(name, tip)):
            self._save(plan)

    def _auto_sync(self, plan: _Plan) -> None:
        """``[rebuild] sync_spec = "auto"``: sync the committed spec onto the target before
        the run starts (so before its start baseline); a refusal is the run's 409. Nothing
        to do when the target does not exist yet or its spec is the same."""
        rec = plan.record
        name = str(rec.get("target_branch") or "")
        if not name or self._drift_of(name) is None:
            return
        try:
            source = _current_branch(self.rb.repo()) or "HEAD"
            out = self.rb.sync_spec(name)
        except RebuildError as e:
            raise PlanError(e.status, e.detail, e.code) from None
        with self.lock:
            self._event(plan, f"spec synced from {source} at {out['synced_from'][:7]} "
                        f"({', '.join(out['files'])})")  # fmt: skip
            self._set_drift(rec, None)
            self._save(plan)
        self.log(f"plan {rec['id']}: spec synced onto {name} at {out['commit'][:10]}")

    # -- GET
    def _get(self, pid: str) -> _Plan:
        plan = self.plans.get(pid)
        if plan is None:
            raise PlanError(404, f"no plan {pid}")
        return plan

    def get(self, pid: str, wait: float = 0.0) -> dict[str, Any]:
        with self.lock:
            plan = self._get(pid)
        if wait > 0:
            deadline = time.monotonic() + min(wait, MAX_WAIT)
            while time.monotonic() < deadline:
                with self.lock:  # the end-of-thread bookkeeping done too (planner usage)
                    if plan.record["status"] not in TRANSIENT and plan.done.is_set():
                        break
                plan.done.wait(0.1)
        with self.lock:
            self._refresh(plan)
            self._refresh_drift(plan)
            out = copy.deepcopy(plan.record)
            out["resume"] = self._resume(plan)  # computed when read, never stored
            return out

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            out = []
            for p in self.plans.values():
                self._refresh(p)
                r = {**p.record, "resume": self._resume(p)}
                out.append({k: copy.deepcopy(r.get(k)) for k in (
                    "id", "status", "source", "version", "scope", "target_branch",
                    "max_parallel", "levels", "created", "finished", "totals", "interrupted",
                    "attempts", "resume")})  # fmt: skip
                out[-1]["nodes"] = [{"id": n["id"], "status": n["status"]} for n in r["nodes"]]
            return out

    def _resume(self, plan: _Plan) -> dict[str, Any]:
        """With the lock held: the plan's ``resume`` (API v1.10), from the record and the
        repository as it is now (where the target is checked out, whether a waiting
        level still fast-forwards it)."""
        rec = plan.record
        checked = owner = None
        ff: bool | None = None
        if rec.get("status") in RESUMABLE and rec.get("target_branch"):
            try:
                repo = self.rb.repo()
                name = str(rec["target_branch"])
                owner = str(repo)
                where = _checked_out(repo).get(name)
                checked = str(Path(where).resolve()) if where else None
                lw = land_waiting(rec)
                if lw is not None:
                    ff = self._fast_forwards(repo, name, str(lw["tip"]))
            except (RebuildError, OSError, subprocess.SubprocessError):
                pass
        return resume_view(rec, checked_out_at=checked, owner_worktree=owner, fast_forward=ff,
                           max_attempts=self.cfg.max_attempts,
                           repair_attempts=max(1, self.cfg.level_repair_attempts))  # fmt: skip

    @staticmethod
    def _fast_forwards(repo: Path, name: str, tip: str) -> bool | None:
        r = _git(repo, "rev-parse", "-q", "--verify", f"refs/heads/{name}")
        if r.returncode != 0:
            return None
        cur = r.stdout.strip()
        return cur == tip or _git(repo, "merge-base", "--is-ancestor", cur, tip).returncode == 0

    def _refresh(self, plan: _Plan) -> None:
        """With the lock held: live usage of the running nodes into the record."""
        for n in plan.record["nodes"]:
            if n["status"] == "running" and n.get("build_id"):
                run = self.rb.runs.get(n["build_id"])
                if run is not None:
                    u = run.record["usage"]
                    n["live"] = {"rounds": len(run.record["rounds"]),
                                 "status": run.record["status"], **_node_usage(u)}  # fmt: skip

    def argvs(self, pid: str) -> list[list[str]]:
        with self.lock:
            return [list(a) for a in self._get(pid).argvs]

    # -- planning
    def _trace(self) -> dict[str, Any]:
        return self.rb.trace_data()

    def _plan_thread(self, plan: _Plan, feedback: str | None = None, previous: Any = None) -> None:
        t0 = time.monotonic()
        try:
            self._plan(plan, feedback, previous)
        except Exception as exc:  # noqa: BLE001 -- the record carries any failure
            import traceback

            traceback.print_exc()
            with self.lock:
                plan.record["error"] = {
                    "kind": "internal",
                    "message": f"{type(exc).__name__}: {exc}",
                }
                if plan.record["status"] == "planning":
                    plan.record["status"] = "failed"
        finally:
            with self.lock:
                plan.record["planner"]["wall_s"] = round(
                    plan.record["planner"]["wall_s"] + time.monotonic() - t0, 2
                )
                plan.proc = None
                self._planner_usage(plan)
                self._totals(plan)
                self._event(plan, plan.record["status"])
                self._save(plan)
            plan.done.set()

    def _planner_usage(self, plan: _Plan) -> None:
        """With the lock held: ``planner_usage`` (API v1.11), the planner calls' tokens
        (cache included), list-price estimate, wall time and number of calls."""
        pl = plan.record["planner"]
        u = pl.get("usage") or {}
        plan.record["planner_usage"] = {
            "tokens_in": int(u.get("tokens_in") or 0),
            "tokens_out": int(u.get("tokens_out") or 0),
            "cache_read_input_tokens": int(u.get("cache_read_input_tokens") or 0),
            "cache_creation_input_tokens": int(u.get("cache_creation_input_tokens") or 0),
            "cost_usd_list_estimate": round(float(u.get("cost_usd_list_estimate") or 0.0), 6),
            "wall_s": round(float(pl.get("wall_s") or 0.0), 2),
            "calls": int(pl.get("attempts") or 0),
        }

    def _plan(self, plan: _Plan, feedback: str | None, previous: Any) -> None:
        areas = self.rb.area_list()
        trace = self._trace()
        links = {k: list(v) for k, v in (trace.get("links") or {}).items()}

        def check(doc: Any) -> dict[str, Any]:
            return validate_plan(doc, scope=plan.scope, areas=areas,
                                 max_parallel=plan.record["max_parallel"], models=plan.models,
                                 model_overrides=plan.overrides, trace_links=links)  # fmt: skip

        source, reason, doc = "agent", None, None
        if plan.planner == "fallback":
            source, reason = "fallback", "requested: the request asked for the fallback planner"
        else:
            doc, err = self._ask(plan, areas, trace, feedback=feedback, previous=previous)
            if plan.stop.is_set():
                return
            if err:
                source, reason = "fallback", f"the planner failed: {err}"
            else:
                v = check(doc)
                if not v["ok"]:
                    first = v["issues"]
                    doc2, err2 = self._ask(plan, areas, trace, issues=first, previous=doc)
                    if plan.stop.is_set():
                        return
                    if err2:
                        source, reason = (
                            "fallback",
                            (
                                "the planner's plan was invalid (" + "; ".join(first) + ") and the "
                                f"re-ask failed: {err2}"
                            ),
                        )
                    else:
                        v2 = check(doc2)
                        if v2["ok"]:
                            doc = doc2
                        else:
                            source, reason = (
                                "fallback",
                                (
                                    "the planner's plan was invalid twice: "
                                    + "; ".join(v2["issues"])
                                ),
                            )
        if source == "fallback":
            doc = fallback_plan(plan.scope, areas)
        v = check(doc)
        with self.lock:
            if plan.stop.is_set():
                return
            rec = plan.record
            if not v["ok"]:
                rec["status"] = "failed"
                rec["validation"] = {k: v[k] for k in ("ok", "issues", "auto_edits")}
                rec["error"] = {
                    "kind": "plan",
                    "message": "no valid plan: " + "; ".join(v["issues"]),
                }
                return
            plan.doc = copy.deepcopy(doc)
            self._apply(plan, v)
            self._drop_built(plan)
            rec["source"] = source
            rec["fallback_reason"] = reason
            rec["status"] = "ready"
            rec["approved"] = None
        self.log(f"plan {rec['id']}: ready ({source}, {len(rec['nodes'])} nodes, "
                 f"{len(rec['levels'])} levels)")  # fmt: skip

    def _apply(self, plan: _Plan, v: Mapping[str, Any]) -> None:
        """With the lock held: a validated plan into the record (a new version)."""
        rec = plan.record
        rec["version"] += 1
        rec["validation"] = {k: copy.deepcopy(v[k]) for k in ("ok", "issues", "auto_edits")}
        rec["levels"] = copy.deepcopy(v["levels"])
        rec["edges"] = copy.deepcopy(v["edges"])
        nodes = []
        for n in v["nodes"]:
            nodes.append({
                **copy.deepcopy(n),
                "status": "pending",
                "build_id": None,
                "builds": [],
                "branch": None,
                "commit": None,
                "merged": False,
                "nothing_to_build": False,
                "blocked_by": [],
                "usage": _node_usage({}),
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
            })  # fmt: skip
        rec["nodes"] = nodes

    # -- what is built already (API v1.11)
    def _target_tip(self, plan: _Plan) -> str | None:
        """The target branch's tip now (the `from` commit when the branch is still to be
        created), or None."""
        try:
            repo = self.rb.repo()
        except RebuildError:
            return None
        name = str(plan.record.get("target_branch") or "")
        r = _git(repo, "rev-parse", "-q", "--verify", f"refs/heads/{name}")
        if r.returncode == 0:
            return r.stdout.strip()
        frm = (plan.branch or {}).get("from") if isinstance(plan.branch, Mapping) else None
        r = _git(repo, "rev-parse", "-q", "--verify", f"{frm or 'HEAD'}^{{commit}}")
        return r.stdout.strip() if r.returncode == 0 else None

    def _links(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in (self._trace().get("links") or {}).items()}

    def _node_targets(self, n: Mapping[str, Any], links: Mapping[str, list[str]]) -> list[str]:
        """Every check a node's rules link to (its scope's targets)."""
        return list(
            dict.fromkeys(t for r in n["scope"].get("rules") or [] for t in links.get(r, []))
        )

    def _scope_failing(
        self, plan: _Plan, failing: set[str], links: Mapping[str, list[str]]
    ) -> bool:
        """Whether anything fails in the plan's scope (any failure for the scope ``all``)."""
        if plan.scope.get("kind") == "all":
            return bool(failing)
        mine = {t for r in plan.scope["rules"] for t in links.get(r, [])} | set(plan.scope["rules"])
        return bool(mine & failing)

    def _node_has_work(self, plan: _Plan, n: Mapping[str, Any], failing: set[str],
                       links: Mapping[str, list[str]]) -> bool:  # fmt: skip
        """Whether a node has something failing in its scope: the integration node, its
        plan scope; a scaffold with no rules of its own, anything failing in the plan scope
        (it builds what the other nodes plug into); any other node, its scope's checks."""
        if n["kind"] == "integration":
            return self._scope_failing(plan, failing, links)
        rules = list(n["scope"].get("rules") or [])
        if n["kind"] == "scaffold" and not rules:
            return self._scope_failing(plan, failing, links)
        mine = set(self._node_targets(n, links)) | set(rules)
        return bool(mine & failing)

    def _built_areas(
        self, plan: _Plan, areas: Sequence[Mapping[str, Any]]
    ) -> dict[str, bool | None]:
        """Per area of the plan scope: built on the target branch (every check of it passed
        at the tip, from the per-tip results), not built, or None (the tip never gated)."""
        res = self.rb.tip_result(self._target_tip(plan))
        out: dict[str, bool | None] = {}
        for a in areas:
            if a["id"] == areas_mod.ALL or a["id"] not in plan.scope["areas"]:
                continue
            if res is None:
                out[a["id"]] = None
            else:
                out[a["id"]] = not (set(a.get("targets") or []) & set(res.get("failing") or []))
        return out

    def _drop_built(self, plan: _Plan) -> None:
        """With the lock held, after planning or a revision: a node with nothing failing in
        its scope at the target's tip (per-tip results) is ``passed`` with
        ``nothing_to_build`` and never runs an agent -- the scaffold included (API v1.11).
        Nothing is dropped when the tip was never gated."""
        tip = self._target_tip(plan)
        res = self.rb.tip_result(tip)
        if res is None:
            return
        failing = set(res.get("failing") or [])
        links = self._links()
        for n in plan.record["nodes"]:
            if n["status"] == "pending" and not self._node_has_work(plan, n, failing, links):
                n.update(status="passed", nothing_to_build=True, dropped_at_planning=tip)
                self._event(plan, f"node {n['id']}: nothing failing in its scope at "
                            f"{str(tip)[:10]}; dropped (no agent)", n)  # fmt: skip

    def _file_tree(self) -> list[str]:
        try:
            repo = self.rb.repo()
        except RebuildError:
            return []
        out = _git(repo, "ls-files").stdout.splitlines()
        junk = (".lattice/", "__pycache__", ".venv/", "node_modules/")
        out = [x for x in out if not any(j in x for j in junk)]
        return out[:FILE_TREE_MAX]

    def _ask(
        self,
        plan: _Plan,
        areas: Sequence[Mapping[str, Any]],
        trace: Mapping[str, Any],
        *,
        issues: Sequence[str] = (),
        previous: Any = None,
        feedback: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """One planner call: (document, None) or (None, error)."""
        with self.lock:
            plan.record["planner"]["attempts"] += 1
        if self.cfg.planner == "stub":
            return self._stub(plan, retry=bool(issues))
        failing = None
        built = None
        with contextlib.suppress(Exception):
            views = self.rb.areas(None, branch=plan.record["target_branch"])
            failing = {a["id"]: a.get("failing") for a in views}
        with contextlib.suppress(Exception):
            built = self._built_areas(plan, areas)
        text = planner_prompt(
            project=self.rb.name, scope=plan.scope, areas=areas, trace=trace,
            data_model=self.data_model(), file_tree=self._file_tree(),
            max_parallel=plan.record["max_parallel"], failing=failing, issues=issues,
            previous=previous, feedback=feedback, built=built,
        )  # fmt: skip
        argv = build_planner_argv(claude_bin=self.rb.claude_bin, text=text,
                                  model=self.cfg.planner_model, system=epg_prompt())  # fmt: skip
        with self.lock:
            plan.argvs.append(argv)
        base = self._env if self._env is not None else (self.rb._env or os.environ)
        env = {k: v for k, v in cli_env(base, DEFAULT_MAX_TOKENS).items()
               if not k.startswith("ANTHROPIC_")}  # fmt: skip
        try:
            proc = subprocess.Popen(argv, cwd=str(self.rb.repo()), env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True,
                                    start_new_session=True)  # fmt: skip
        except OSError as exc:
            return None, f"could not start {self.rb.claude_bin}: {exc}"
        with self.lock:
            plan.proc = proc
            self._pid_sink(plan)(proc_info(proc.pid, "planner", str(argv[0])), True)
        if plan.stop.is_set():
            _kill_group(proc, signal.SIGTERM)
        t = Transcript()
        seen = StreamUsage()
        stderr: list[str] = []
        drainer = threading.Thread(target=lambda: stderr.extend(proc.stderr or ()), daemon=True)
        drainer.start()
        timed_out = threading.Event()

        def on_timeout() -> None:
            timed_out.set()
            _kill_group(proc, signal.SIGTERM)

        timer = threading.Timer(self.cfg.planner_timeout, on_timeout)
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout or ():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    t.feed(rec)
                    seen.feed(rec)
            proc.wait()
        finally:
            timer.cancel()
            if proc.poll() is None:
                _kill_group(proc, signal.SIGKILL)
            drainer.join(2)
            with self.lock:
                plan.proc = None
                self._pid_sink(plan)({"pid": proc.pid}, False)
        events = t.events_after(0)
        result = next((e for e in reversed(events) if e["type"] == "result"), None)
        errors = [e for e in events if e["type"] == "error"]
        if result is not None:
            u = {"input_tokens": result.get("input_tokens", 0),
                 "output_tokens": result.get("output_tokens", 0),
                 "cache_read_input_tokens": result.get("cache_read_input_tokens", 0),
                 "cache_creation_input_tokens": result.get("cache_creation_input_tokens", 0),
                 "cost_usd_list_estimate": result.get("cost_usd", 0.0)}  # fmt: skip
            with self.lock:
                _sum_into(plan.record["planner"]["usage"], _node_usage(u))
        elif seen.messages:  # no result (a timeout, a crash): the usage seen so far (v1.11)
            with self.lock:
                _sum_into(plan.record["planner"]["usage"], _node_usage(seen.totals()))
        if timed_out.is_set():
            return None, f"the planner did not answer within {self.cfg.planner_timeout:g}s"
        if errors:
            return None, str(errors[-1].get("message") or "claude failed")
        if result is None:
            tail = "".join(stderr).strip()
            return None, _short(tail, 300) or f"claude exited with status {proc.returncode}"
        try:
            return parse_planner_text(str(result.get("text") or "")), None
        except ValueError as e:
            return None, str(e)

    def _stub(self, plan: _Plan, retry: bool) -> tuple[dict[str, Any] | None, str | None]:
        """TEST ONLY: ``stub-plan.json`` (see the module docstring)."""
        path = self.rb.project / STUB_PLAN
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"{STUB_PLAN}: {exc}"
        if isinstance(data, dict) and "scopes" in data and "nodes" not in data:
            entry = (data.get("scopes") or {}).get(plan.scope["slug"])
            if entry is None:
                entry = (data.get("scopes") or {}).get("default")
            if entry is None:
                return None, f"{STUB_PLAN} has no plan for scope {plan.scope['slug']}"
            data = entry
        if isinstance(data, dict) and "error" in data and "nodes" not in data:
            return None, str(data["error"])
        if isinstance(data, dict) and "doc" in data and "nodes" not in data:
            data = data["retry"] if retry and "retry" in data else data["doc"]
        return (copy.deepcopy(data), None) if isinstance(data, dict) else (None, "not an object")

    # -- POST /api/plan/{id}/revise
    def revise(self, pid: str, body: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(body) - {"feedback", "edits"}
        if unknown:
            raise PlanError(422, f"unknown field(s): {', '.join(sorted(unknown))}")
        feedback, edits = body.get("feedback"), body.get("edits")
        if feedback is not None and (not isinstance(feedback, str) or not feedback.strip()):
            raise PlanError(422, "feedback must be a non-empty string")
        if edits is not None and not isinstance(edits, Mapping):
            raise PlanError(422, "edits must be an object")
        if (feedback is None) == (edits is None):
            raise PlanError(422, "send feedback or edits (one of them)")
        with self.lock:
            plan = self._get(pid)
            status = plan.record["status"]
            replan = status == "interrupted" and not plan.record["runs"]  # cut off while planning
            if status not in ("ready", "approved") and not (replan and feedback is not None):
                raise PlanError(409, f"plan {pid} is {status}; only a ready or approved plan "
                                "can be revised")  # fmt: skip
            if feedback is not None:
                previous = copy.deepcopy(plan.doc)
                plan.record["status"] = "planning"
                plan.record["approved"] = None
                plan.planner = "agent"
                plan.done.clear()
                plan.thread = threading.Thread(
                    target=self._plan_thread,
                    args=(plan, feedback, previous),
                    daemon=True,
                    name=f"studio-plan-{pid}-revise",
                )
                self._event(plan, "revising (feedback)")
                self._save(plan)
                plan.thread.start()
                return {"id": pid, "status": "planning"}
        assert isinstance(edits, Mapping)
        allowed = {"nodes", "remove_edges", "add_edges", "max_parallel", "branch",
                   "confirm_unsafe_branch"}  # fmt: skip
        if set(edits) - allowed:
            raise PlanError(422, f"unknown edit(s): {', '.join(sorted(set(edits) - allowed))}")
        with self.lock:
            doc = copy.deepcopy(plan.doc) or {"nodes": []}
            overrides = dict(plan.overrides)
            mp = plan.record["max_parallel"]
        by = {n.get("id"): n for n in doc["nodes"] if isinstance(n, dict)}
        node_edits = edits.get("nodes") or {}
        if not isinstance(node_edits, Mapping):
            raise PlanError(422, "edits.nodes must map node ids to {depends_on, tier, model}")
        for nid, e in node_edits.items():
            if nid not in by:
                raise PlanError(404, f"no node {nid}")
            if not isinstance(e, Mapping) or set(e) - {"depends_on", "tier", "model"}:
                raise PlanError(422, f"edits.nodes.{nid} takes depends_on, tier and model")
            if "depends_on" in e:
                d = e["depends_on"]
                if not isinstance(d, list) or not all(isinstance(x, str) for x in d):
                    raise PlanError(422, f"edits.nodes.{nid}.depends_on must be node ids")
                old = {(x if isinstance(x, str) else x.get("id")): x
                       for x in by[nid].get("depends_on") or []}  # fmt: skip
                by[nid]["depends_on"] = [old.get(x) or {"id": x, "why": "set by the owner"}
                                         for x in d]  # fmt: skip
            if "tier" in e:
                if e["tier"] not in TIERS:
                    raise PlanError(422, f"tier must be one of {', '.join(TIERS)}")
                by[nid]["tier"] = e["tier"]
            if "model" in e:
                if e["model"] is None:
                    overrides.pop(nid, None)
                elif isinstance(e["model"], str) and e["model"].strip():
                    overrides[nid] = e["model"].strip()
                else:
                    raise PlanError(422, "model must be a model name or null")

        def pairs(key: str) -> list[tuple[str, str]]:
            v = edits.get(key) or []
            if not isinstance(v, list) or not all(
                isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p)
                for p in v
            ):
                raise PlanError(422, f"edits.{key} must be [[from, to], ...]")
            return [(a, b) for a, b in v]

        for a, b in pairs("remove_edges"):
            if b not in by:
                raise PlanError(404, f"no node {b}")
            by[b]["depends_on"] = [x for x in by[b].get("depends_on") or []
                                   if (x if isinstance(x, str) else x.get("id")) != a]  # fmt: skip
        for a, b in pairs("add_edges"):
            if a not in by or b not in by:
                raise PlanError(404, f"no node {a if a not in by else b}")
            cur = [
                (x if isinstance(x, str) else x.get("id")) for x in by[b].get("depends_on") or []
            ]
            if a not in cur:
                by[b].setdefault("depends_on", []).append({"id": a, "why": "added by the owner"})
        if "max_parallel" in edits:
            mp = edits["max_parallel"]
            if isinstance(mp, bool) or not isinstance(mp, int) or not 1 <= mp <= MAX_PARALLEL:
                raise PlanError(422, f"max_parallel must be 1 to {MAX_PARALLEL}")
        target = None
        confirm = edits.get("confirm_unsafe_branch", plan.confirm)
        if not isinstance(confirm, bool):
            raise PlanError(422, "confirm_unsafe_branch must be true or false")
        if "branch" in edits:
            target = self._check_branch(edits["branch"], confirm)
        areas = self.rb.area_list()
        links = {k: list(v) for k, v in (self._trace().get("links") or {}).items()}
        v = validate_plan(doc, scope=plan.scope, areas=areas, max_parallel=mp,
                          models=plan.models, model_overrides=overrides,
                          trace_links=links)  # fmt: skip
        if not v["ok"]:
            raise PlanError(422, "the edited plan is invalid: " + "; ".join(v["issues"]),
                            "plan_invalid")  # fmt: skip
        with self.lock:
            if plan.record["status"] not in ("ready", "approved"):
                raise PlanError(409, f"plan {pid} is {plan.record['status']}")
            plan.doc = doc
            plan.overrides = overrides
            plan.record["max_parallel"] = mp
            if target is not None:
                plan.branch = edits["branch"]
                plan.confirm = confirm
                plan.record["branch"] = edits["branch"]
                plan.record["target_branch"] = target["name"]
            self._apply(plan, v)
            self._drop_built(plan)
            plan.record["status"] = "ready"
            plan.record["approved"] = None
            self._event(plan, f"revised (version {plan.record['version']})")
            self._save(plan)
            return copy.deepcopy(plan.record)

    # -- POST /api/plan/{id}/approve
    def approve(self, pid: str) -> dict[str, Any]:
        with self.lock:
            plan = self._get(pid)
            status = plan.record["status"]
            if status != "ready":
                raise PlanError(409, f"plan {pid} is {status}, not ready")
            plan.record["status"] = "approved"
            plan.record["approved"] = _now()
            self._event(plan, f"approved (version {plan.record['version']})")
            self._save(plan)
            return {"id": pid, "status": "approved", "version": plan.record["version"]}

    # -- POST /api/plan/{id}/run
    def run(self, pid: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Run an approved plan, or resume a ``partial``, ``failed``, ``stopped`` or
        ``interrupted`` one from where it stopped (API v1.10): levels that passed their
        full gate and landed are skipped with their nodes; a level that passed its gate
        but never landed is landed first (fast-forward, else re-merged and re-gated with
        no agent); an interrupted node continues its build; a node that gave up, did not
        merge or regressed is retried with its earlier findings (``[rebuild]
        max_attempts``)."""
        body = dict(body or {})
        unknown = set(body) - {"confirm_unsafe_branch"}
        if unknown:
            raise PlanError(422, f"unknown field(s): {', '.join(sorted(unknown))}")
        confirm = body.get("confirm_unsafe_branch", False)
        if not isinstance(confirm, bool):
            raise PlanError(422, "confirm_unsafe_branch must be true or false")
        ok, why = self.rb.available()
        if not ok:
            raise PlanError(503, f"build plans are not available: {why}")
        with self.lock:
            plan = self._get(pid)
            rec = plan.record
            status = rec["status"]
            if status not in ("approved", *RESUMABLE):
                hint = ": approve it first" if status == "ready" else ""
                raise PlanError(409, f"plan {pid} is {status}, not approved{hint}")
            resume = status in RESUMABLE
            if resume:
                if not rec.get("approved") or not rec["nodes"]:
                    raise PlanError(409, f"plan {pid} is {status} and was never approved: revise "
                                    "it, approve it, then run it")  # fmt: skip
                view = self._resume(plan)
                if not view["available"]:
                    raise PlanError(409, view["note"] or f"plan {pid} has nothing to resume",
                                    "nothing_to_resume")  # fmt: skip
            if self.rb.plan_active:
                raise PlanError(409, f"a build plan is running in this project "
                                f"({self.rb.plan_active})")  # fmt: skip
            busy = self.rb.running()
            if busy:
                raise PlanError(409, f"a rebuild is already running in this project ({busy})")
        if resume and (plan.created_target or rec.get("target_tip")):
            # the target exists by now: refuse at once what the run would refuse
            self._check_branch({"name": rec["target_branch"], "create": False},
                               plan.confirm or confirm)  # fmt: skip
        if self.cfg.sync_spec == "auto":
            self._auto_sync(plan)  # API v1.12: before the start baseline
        with self.lock:
            if self.rb.plan_active or rec["status"] != status:
                raise PlanError(409, f"plan {pid} changed meanwhile; try again")
            self._start_run(plan, confirm=confirm, land_only=False, resume=resume)
        self.log(f"plan {pid}: running (run {rec['runs']}{', resumed' if resume else ''})")
        return {"id": pid, "status": "running"}

    def _start_run(self, plan: _Plan, *, confirm: bool, land_only: bool, resume: bool) -> None:
        """With the lock held: take the build slot and start the runner thread."""
        rec = plan.record
        pid = rec["id"]
        lw = land_waiting(rec)
        landing = set(lw["merged"]) if lw else set()
        rw = repair_waiting(rec)
        if rw is not None:  # a repair pass goes on from the level's merged nodes (v1.11)
            landing |= set(rw.get("merged") or [])
        self.rb.plan_active = pid
        plan.stop.clear()
        plan.done.clear()
        plan.land_only = land_only
        plan.confirm_land = confirm
        plan.run_started_ts = time.time()
        prev = rec["status"]
        rec["status"] = "running"
        rec["runs"] += 1
        rec["attempts"] = rec["runs"]
        rec["started"] = rec["started"] or _now()
        rec["finished"] = None
        rec["error"] = None
        rec["interrupted"] = False
        if not land_only:
            for n in rec["nodes"]:
                if n["id"] in landing:
                    continue
                if n["status"] in ("gave_up", "merge_conflict", "regressed"):
                    n["previous_findings"] = self._findings_of(n)
                if n["status"] in RERUN:
                    n.update(status="pending", blocked_by=[], error=None)
        what = "land" if land_only else ("resume" if resume else "run")
        self._event(plan, f"{what} {rec['runs']} started (was {prev})")
        self._save(plan)
        plan.thread = threading.Thread(target=self._execute, args=(plan,), daemon=True,
                                       name=f"studio-plan-{pid}-{what}")  # fmt: skip
        plan.thread.start()

    def _findings_of(self, n: Mapping[str, Any]) -> list[str]:
        """With the lock held: what a node's last attempt ended with (its error, and the
        findings of its build's last round), for the retry's brief."""
        out = []
        err = n.get("error") or {}
        if err.get("message"):
            out.append(f"{err.get('kind') or 'error'}: {_short(err['message'], 300)}")
        run = self.rb.runs.get(n.get("build_id") or "")
        if run is not None and run.record.get("rounds"):
            check = run.record["rounds"][-1].get("check") or {}
            for f in (check.get("findings") or [])[:20]:
                out.append(_short(f.get("title") or "", 240))
        return out

    # -- POST /api/plan/{id}/land
    def land(self, pid: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Add a level that passed its full gate but never landed to the target branch,
        without running any agent: a fast-forward to its pinned gated tip when the target
        has not moved past it (the owner's checked-out branch only with
        ``confirm_unsafe_branch`` and a clean worktree, as in v1.8); else its node commits
        are merged onto the new tip and the full gate re-runs in the background (no agent).
        409 when nothing is waiting to land."""
        body = dict(body or {})
        unknown = set(body) - {"confirm_unsafe_branch"}
        if unknown:
            raise PlanError(422, f"unknown field(s): {', '.join(sorted(unknown))}")
        confirm = body.get("confirm_unsafe_branch", False)
        if not isinstance(confirm, bool):
            raise PlanError(422, "confirm_unsafe_branch must be true or false")
        with self.lock:
            plan = self._get(pid)
            rec = plan.record
            if rec["status"] in TRANSIENT:
                raise PlanError(409, f"plan {pid} is {rec['status']}")
            lw = land_waiting(rec)
            if lw is None:
                raise PlanError(409, f"nothing is waiting to land in plan {pid}",
                                "nothing_to_land")  # fmt: skip
            if self.rb.plan_active:
                raise PlanError(409, f"a build plan is running in this project "
                                f"({self.rb.plan_active})")  # fmt: skip
            busy = self.rb.running()
            if busy:
                raise PlanError(409, f"a rebuild is already running in this project ({busy})")
            self.rb.plan_active = pid  # the slot, while the branch moves
        try:
            out = self._land_ff(plan, lw, confirm)
        finally:
            with self.lock:
                if self.rb.plan_active == pid and rec["status"] not in TRANSIENT:
                    self.rb.plan_active = None
        if isinstance(out, dict):
            raise PlanError(409, out["message"], out["kind"])
        if out == "landed":
            with self.lock:
                rec["status"] = final_status(rec)
                rec["finished"] = _now()
                self._save(plan)
                return {"id": pid, "status": rec["status"], "landed": True, "remerge": False,
                        "target_tip": rec["target_tip"]}  # fmt: skip
        with self.lock:  # not a fast-forward any more: re-merge and re-gate, in the background
            if self.rb.plan_active:
                raise PlanError(409, f"a build plan is running in this project "
                                f"({self.rb.plan_active})")  # fmt: skip
            self._start_run(plan, confirm=confirm, land_only=True, resume=True)
        self.log(f"plan {pid}: landing level {lw['level']} by a re-merge and the full gate")
        return {"id": pid, "status": "running", "landed": False, "remerge": True,
                "target_tip": rec["target_tip"]}  # fmt: skip

    def _land_ff(self, plan: _Plan, lw: Mapping[str, Any], confirm: bool) -> str | dict[str, str]:
        """Fast-forward the target to the waiting level's gated tip: ``"landed"``,
        ``"not_ff"`` (the target moved: a re-merge is needed), or a refusal
        ``{kind, message}`` (the branch is checked out, the tree dirty, the tip gone)."""
        rec = plan.record
        repo = self.rb.repo()
        name = str(rec["target_branch"])
        tip = str(lw["tip"])
        r = _git(repo, "rev-parse", "-q", "--verify", f"refs/heads/{name}")
        if r.returncode != 0:
            return {"kind": "no_branch", "message": f"no branch {name}"}
        cur = r.stdout.strip()
        if _git(repo, "cat-file", "-e", f"{tip}^{{commit}}").returncode != 0:
            return {"kind": "no_ref", "message": f"the gated tip {tip[:10]} of level "
                    f"{lw['level']} is gone"}  # fmt: skip
        if cur != tip:
            if _git(repo, "merge-base", "--is-ancestor", cur, tip).returncode != 0:
                return "not_ff"
            where = _checked_out(repo).get(name)
            current = _current_branch(repo)
            if where is not None and name != current:
                msg = f"{name} is checked out in {where}; switch that worktree to another branch"
                return {"kind": "branch_checked_out", "message": msg + " and press Land"}
            if name == current and not confirm:
                return {"kind": "branch_checked_out", "message": f"{name} is checked out in your "
                        f"working copy {repo}; switch it away and press Land (or send "
                        "confirm_unsafe_branch: true to fast-forward it there, with a clean "
                        "worktree)"}  # fmt: skip
            moved = self.rb._advance_to(repo, name, cur, tip, name == current)
            if moved is not None:
                return moved
        L = int(lw["level"])
        with self.lock:
            for x in rec["level_runs"]:
                if x is not None and x.get("level") == L and x.get("tip") == tip and \
                        x.get("status") == "failed":  # fmt: skip
                    x["land_error"] = x.pop("error", None)
                    x.update(status="passed", landed=True, landed_at=_now(), landed_by="resume")
            nodes = {n["id"]: n for n in rec["nodes"]}
            for nid in lw["merged"]:
                n = nodes.get(nid)
                if n is not None:
                    n.update(status="passed", merged=True, error=None)
                    self._event(plan, f"node {nid} landed with level {L}", n)
            rec["target_tip"] = tip
            self._event(plan, f"level {L} landed on {name} at {tip[:10]} (resume, no agent)")
            self._save(plan)
        self._results(name, tip, tip, set((lw.get("gate") or {}).get("failing") or []),
                      f"plan {rec['id']} level {L} (landed)")  # fmt: skip
        self.log(f"plan {rec['id']}: level {L} landed on {name} at {tip[:10]}")
        return "landed"

    # -- DELETE /api/plan/{id}
    def stop(self, pid: str, join: float = 30.0) -> dict[str, Any]:
        with self.lock:
            plan = self._get(pid)
            status = plan.record["status"]
            if status not in TRANSIENT:
                raise PlanError(409, f"cannot stop a plan that is {status}")
            plan.stop.set()
            plan.record["status"] = "stopped"
            self._event(plan, f"stopped (was {status})")
            self._save(plan)
            proc = plan.proc
            irun = plan.irun
            live = [n["build_id"] for n in plan.record["nodes"]
                    if n["status"] == "running" and n.get("build_id")]  # fmt: skip
        if proc is not None:
            _kill_group(proc, signal.SIGTERM)
        if irun is not None and irun.proc is not None and irun.proc_role != "gate":
            _kill_group(irun.proc, signal.SIGTERM)  # a gate stops its own tree (v1.11)
        for rid in live:
            with contextlib.suppress(RebuildError):
                self.rb.stop(rid)
        if plan.thread is not None and plan.thread is not threading.current_thread():
            plan.thread.join(join)
        with self.lock:
            for n in plan.record["nodes"]:
                if n["status"] == "running":
                    n["status"] = "stopped"
            self._save(plan)
        self.log(f"plan {pid}: stopped")
        return {"id": pid, "status": "stopped"}

    # -- DELETE /api/plan/{id}?forget=true
    def forget(self, pid: str) -> dict[str, Any]:
        """Stop the plan if it runs, then drop it: its record, its state file and its git
        anchors ``refs/lattice/plans/<id>/*`` (its node branches stay). Its id is never
        handed out again."""
        with self.lock:
            plan = self._get(pid)
            live = plan.record["status"] in TRANSIENT
        if live:
            with contextlib.suppress(PlanError):
                self.stop(pid)
        refs: list[str] = []
        with contextlib.suppress(RebuildError):
            repo = self.rb.repo()
            listing = _git(repo, "for-each-ref", "--format=%(refname)", f"{REF_PREFIX}/{pid}/")
            for ref in listing.stdout.split():
                if _git(repo, "update-ref", "-d", ref).returncode == 0:
                    refs.append(ref)
        with self.lock:
            self.plans.pop(pid, None)
            if self.store is not None:
                self.store.delete("plans", pid)
        self.log(f"plan {pid}: forgotten ({len(refs)} anchor ref(s) removed)")
        return {"id": pid, "status": "forgotten", "refs_removed": refs}

    def close(self) -> None:
        with self.lock:
            live = [pid for pid, p in self.plans.items() if p.record["status"] in TRANSIENT]
        for pid in live:
            with contextlib.suppress(PlanError):
                self.stop(pid)

    # -- the runner
    def _execute(self, plan: _Plan) -> None:
        rec = plan.record
        plan.t_run = time.monotonic()
        plan.final = None
        try:
            self._levels(plan)
        except Exception as exc:  # noqa: BLE001 -- the record carries any failure
            import traceback

            traceback.print_exc()
            self._end(plan, "failed", {"kind": "internal",
                                       "message": f"{type(exc).__name__}: {exc}"})  # fmt: skip
        finally:
            if plan.irun is not None:
                self._drop_irun(plan, plan.irun)
            with self.lock:
                if self.rb.plan_active == rec["id"]:
                    self.rb.plan_active = None
                rec["finished"] = _now()
                rec["totals"]["wall_s"] = round(
                    rec["totals"]["wall_s"] + time.monotonic() - plan.t_run, 2
                )
                plan.run_started_ts = 0.0
                self._totals(plan)
                early = {"kind": "internal", "message": "the run ended early"}
                status, error = plan.final or ("failed", early)
                stopped = rec["status"] == "stopped"
                self._save(plan)
            if not stopped:
                with contextlib.suppress(Exception):
                    self._plan_hook(plan, status)
            with self.lock:
                if rec["status"] != "stopped":
                    rec["status"] = status
                    if error is not None:
                        rec["error"] = error
                self._event(plan, rec["status"])
                self._save(plan)
            plan.done.set()
            self.log(f"plan {rec['id']}: {rec['status']}")

    def _end(self, plan: _Plan, status: str, error: Mapping[str, Any] | None = None) -> None:
        """The run's end state, published by :meth:`_execute` once the end-of-run work
        (totals, the plan's metrics hook) is done, so a waiting GET sees it complete."""
        with self.lock:
            plan.final = (status, dict(error) if error is not None else None)

    def _irun(self, plan: _Plan, sha: str, label: str, stop: threading.Event) -> Any:
        """A detached integration worktree of the plan, its gate processes recorded in the
        plan's ``pids`` and its path in ``integration_worktree`` (a restart removes it)."""
        irun = self.rb.integration_worktree(sha, label, stop)
        irun.pid_sink = self._pid_sink(plan)
        with self.lock:
            plan.record["integration_worktree"] = str(irun.worktree)
            self._save(plan)
        return irun

    def _drop_irun(self, plan: _Plan, irun: Any) -> None:
        self.rb.remove_worktree(irun)
        with self.lock:
            if plan.irun is irun:
                plan.irun = None
            if plan.record.get("integration_worktree") == str(irun.worktree):
                plan.record["integration_worktree"] = None
            self._save(plan)

    def _drop_stale(self, plan: _Plan) -> None:
        """The integration worktree an interrupted run left (Studio's own, recorded)."""
        stale = plan.record.get("integration_worktree")
        if stale:
            path = Path(str(stale))
            if path.is_dir() and (WORKTREE_MARK in path.as_posix()
                                  or path.name.startswith("lattice-rebuild-")):  # fmt: skip
                import shutil

                shutil.rmtree(path, ignore_errors=True)
            with contextlib.suppress(RebuildError):
                _git(self.rb.repo(), "worktree", "prune")
            with self.lock:
                plan.record["integration_worktree"] = None

    def _advance_level(
        self,
        plan: _Plan,
        L: int,
        lr: dict[str, Any],
        outcome: Mapping[str, Any],
        repo: Path,
        name: str,
        old_tip: str,
        checked_out_main: bool,
        by: str,
    ) -> str | None:
        """Move the target to a level's gated tip (the only tip it ever moves to). None
        when it could not (the level then waits to land: v1.10's ``POST .../land``)."""
        new_tip = str(outcome["tip"])
        moved = self.rb._advance_to(repo, name, old_tip, new_tip, checked_out_main)
        merged = list(outcome["merged"])
        with self.lock:
            if moved is not None:
                lr["status"] = "failed"
                lr["error"] = moved
                lr["landed"] = False
                for n in merged:
                    n["status"] = "regressed"
                    n["error"] = moved
                self._event(plan, f"level {L} passed its full gate at {new_tip[:10]} but did "
                            f"not land on {name}: {moved['message']}")  # fmt: skip
            else:
                plan.record["target_tip"] = new_tip
                lr.update(landed=True, landed_at=_now(), landed_by=by)
                for n in merged:
                    n.update(merged=True, status="passed", error=None)
                    self._event(plan, f"node {n['id']} landed with level {L}", n)
                self._event(plan, f"level {L} landed on {name} at {new_tip[:10]}")
            self._save(plan)
        if moved is not None:
            return None
        self._results(name, new_tip, new_tip, set(outcome["failing"]),
                      f"plan {plan.record['id']} level {L}")  # fmt: skip
        return new_tip

    def _levels(self, plan: _Plan) -> None:
        rec = plan.record
        pid = rec["id"]
        rb = self.rb
        repo = rb.repo()
        self._drop_stale(plan)
        spec = plan.branch
        if plan.created_target:
            spec = {"name": rec["target_branch"], "create": False}
        try:
            target = rb._target(repo, spec, plan.confirm or plan.confirm_land)
        except RebuildError as e:
            self._end(plan, final_status(rec) if rec["runs"] > 1 else "failed",
                      {"kind": e.code or "branch", "message": e.detail})  # fmt: skip
            return
        name, branch_tip = target["name"], target["tip"]
        if target["created"]:
            plan.created_target = True
        owner_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        with self.lock:
            rec.update(target_branch=name, owner_head=owner_head, target_tip=branch_tip)
            rec["created_branch"] = plan.created_target
            rec["gate_config"] = self._gate_config()
            self._save(plan)
        # resume (v1.10): a level that passed its full gate but never landed lands first
        lw = land_waiting(rec)
        remerge = None
        if lw is not None:
            out = self._land_ff(plan, lw, plan.confirm or plan.confirm_land)
            if isinstance(out, dict):
                self._end(plan, final_status(rec), out)
                return
            if out == "not_ff":
                remerge = lw
            else:
                branch_tip = str(lw["tip"])
        if plan.land_only and remerge is None:
            self._end(plan, final_status(rec))
            return
        # the start baseline: the full gate on the target's tip -- unless that tip was gated
        # before (its per-tip results are reused: a tip is never gated twice, API v1.11)
        need_merge = (
            target["merge_owner_head"]
            and _git(repo, "merge-base", "--is-ancestor", owner_head, branch_tip).returncode != 0
        )
        cached = None if need_merge else self._usable(rb.tip_result(branch_tip))
        t0 = time.monotonic()
        if cached is not None:
            start_sha = branch_tip
            gates, counts = list(cached.get("gates") or []), dict(cached.get("counts") or {})
            findings = [dict(f) for f in cached.get("findings") or []]
            start_res = cached
        else:
            irun = self._irun(plan, branch_tip, f"{pid}-start-{rec['runs']}", plan.stop)
            plan.irun = irun
            try:
                if need_merge:
                    r = _git(irun.worktree, "merge", "--no-edit", "-q", owner_head, timeout=300)
                    if r.returncode != 0:
                        _git(irun.worktree, "merge", "--abort")
                        self._end(plan, "failed", {"kind": "merge_conflict", "message":
                                  f"merging the owner's HEAD {owner_head[:10]} into {name} "
                                  f"conflicts: {_short(r.stdout or r.stderr, 400)}"})  # fmt: skip
                        return
                start_sha = _git(irun.worktree, "rev-parse", "HEAD").stdout.strip()
                gates, counts, findings = rb.gate_round(irun, 0)
                report = list(irun.report_scenarios)
            finally:
                self._drop_irun(plan, irun)
            if plan.stop.is_set():
                return
            broken = [f"`{g['command']}` exited {g['exit_code']}" for g in gates
                      if g["exit_code"] not in (0, 1)]  # fmt: skip
            if broken or not gates:
                what = "; ".join(broken) or "no gate ran"
                self._end(plan, "failed", {"kind": "baseline", "message":
                          f"the gates could not run on the start tip: {what}"})  # fmt: skip
                return
            start_res = self._record_tip(start_sha, "full", gates, counts, findings, report,
                                         f"plan {pid} start")  # fmt: skip
        last_failing = set(start_res["failing"])
        last = self._last(start_res, findings, gates, counts)
        last_good = start_sha
        with self.lock:
            rec["baseline"] = {
                "at": _now(), "sha": start_sha, "seconds": round(time.monotonic() - t0, 2),
                "verdict": "pass" if not findings else "fail", "counts": counts,
                "failing": sorted(last_failing),
                "findings": [{"title": f["title"], "target": f.get("target") or ""}
                             for f in findings],
                "reused": cached is not None,
            }  # fmt: skip
            how = " (recorded results of that tip, not gated again)" if cached is not None else ""
            self._event(plan, f"start baseline on {name} at {start_sha[:10]}: "
                        f"{len(last_failing)} failing{how}")  # fmt: skip
            self._save(plan)
        self._results(name, start_sha, branch_tip, last_failing, f"plan {pid} start")
        nodes = {n["id"]: n for n in rec["nodes"]}
        failed_level = False
        exhausted: dict[str, Any] | None = None
        if remerge is not None:
            # the target moved past the waiting level's tip: its node commits are merged onto
            # the new tip and only the full gate runs again (no agent)
            L = int(remerge["level"])
            again = [nodes[i] for i in remerge["merged"] if nodes[i].get("commit")]
            lr = {"level": L, "run": rec["runs"], "base": last_good,
                  "nodes": [n["id"] for n in again], "status": "running", "merged": [],
                  "merge_conflicts": [], "tip": None, "gate": None, "regressions": [],
                  "blame": [], "started": _now(), "finished": None, "remerge": True}  # fmt: skip
            with self.lock:
                rec["level_runs"].append(lr)
                self._event(plan, f"level {L}: re-merging {', '.join(lr['nodes'])} onto "
                            f"{name} at {last_good[:10]} (no agent)")  # fmt: skip
                self._save(plan)
            outcome = self._merge_level(plan, L, lr, again, [], last_good, last_failing, last)
            if plan.stop.is_set():
                return
            if _landable(outcome):
                new = self._advance_level(plan, L, lr, outcome, repo, name, branch_tip,
                                          target["checked_out_main"], "resume")  # fmt: skip
                if new is not None:
                    branch_tip = last_good = new
                    last_failing = set(outcome["failing"])
                    last = outcome["last"]
                else:
                    failed_level = True
            else:
                failed_level = True
            with self.lock:
                if lr["status"] == "running":
                    lr["status"] = "failed" if failed_level else "passed"
                lr["finished"] = _now()
                self._save(plan)
            if failed_level or plan.land_only:
                self._end(plan, final_status(rec))
                return
        # resume (v1.11): a level whose repairs ran out repairs again, from its merged nodes
        rw = repair_waiting(rec)
        if rw is not None:
            L = int(rw["level"])
            again = [nodes[i] for i in rw["merged"] if i in nodes and nodes[i].get("commit")]
            lr = {"level": L, "run": rec["runs"], "base": last_good,
                  "nodes": [n["id"] for n in again], "status": "running", "merged": [],
                  "merge_conflicts": [], "tip": None, "gate": None, "regressions": [],
                  "blame": [], "started": _now(), "finished": None, "repair_resume": True,
                  "repair_pass": int(rw.get("repair_pass") or 1) + 1}  # fmt: skip
            with self.lock:
                rw["superseded"] = True
                rw["repair_pending"] = False
                for n in again:
                    n.update(status="passed", error=None)
                rec["level_runs"].append(lr)
                self._event(plan, f"level {L}: another repair pass (pass {lr['repair_pass']}) "
                            f"of {', '.join(lr['nodes'])} onto {name} at {last_good[:10]}",
                            None, repair=True)  # fmt: skip
                self._save(plan)
            ran = [n for n in again if n.get("build_id")]
            outcome = self._level_end(plan, L, lr, again, ran, last_good, last_failing, last,
                                      force_repair=True)  # fmt: skip
            if plan.stop.is_set():
                return
            if _landable(outcome):
                new = self._advance_level(plan, L, lr, outcome, repo, name, branch_tip,
                                          target["checked_out_main"], "resume")  # fmt: skip
                if new is not None:
                    branch_tip = last_good = new
                    last_failing = set(outcome["failing"])
                    last = outcome["last"]
                else:
                    failed_level = True
            else:
                failed_level = True
                if outcome is not None and outcome.get("exhausted"):
                    exhausted = outcome["exhausted"]
            for n in again:
                run = rb.runs.get(n.get("build_id") or "")
                if run is not None and run.worktree.is_dir():
                    rb.remove_worktree(run)
            with self.lock:
                if lr["status"] == "running":
                    lr["status"] = "failed" if failed_level else "passed"
                lr["finished"] = _now()
                self._save(plan)
            if failed_level:
                if exhausted is not None:
                    self._end(plan, "partial", exhausted)
                else:
                    self._end(plan, final_status(rec))
                return
        n_levels = len(rec["levels"])
        for L, ids in enumerate(rec["levels"]):
            if plan.stop.is_set():
                return
            todo = []
            with self.lock:
                for nid in ids:
                    n = nodes[nid]
                    if n["status"] == "passed":
                        continue
                    bad = [d for d in n["depends_on"] if nodes[d]["status"] != "passed"]
                    if bad:
                        n["status"] = "blocked"
                        n["blocked_by"] = bad
                        continue
                    todo.append(n)
                self._save(plan)
            if not todo:
                continue
            if L == n_levels - 1 and self.cfg.level_gate == "regression":
                # the last level decides from the unscoped full gate (API v1.11)
                full = self._final_full_gate(plan, last_good, last_failing, last)
                if plan.stop.is_set():
                    return
                if full is None:
                    self._end(plan, final_status(rec) if any(x["merged"] for x in rec["nodes"])
                              else "failed", {"kind": "gate", "message": "the full gate could "
                              f"not run on {last_good[:10]}"})  # fmt: skip
                    return
                last_failing, last = full
            lr: dict[str, Any] = {"level": L, "run": rec["runs"], "base": last_good,
                                  "nodes": [n["id"] for n in todo], "status": "running",
                                  "merged": [], "merge_conflicts": [], "tip": None, "gate": None,
                                  "regressions": [], "blame": [], "started": _now(),
                                  "finished": None}  # fmt: skip
            with self.lock:
                rec["level_runs"].append(lr)
                self._event(plan, f"level {L} started ({', '.join(lr['nodes'])})")
                self._save(plan)
            self._run_level(plan, L, todo, last_good, name, last)
            if plan.stop.is_set():
                return
            passed = [n for n in todo if n["status"] == "passed" and n.get("commit")]
            ran = [n for n in todo if n.get("build_id") and n["status"] in ("passed", "gave_up")]
            if passed or (ran and self.cfg.metrics_hook):
                outcome = self._level_end(plan, L, lr, passed, ran, last_good, last_failing, last)
                if plan.stop.is_set():
                    return
                if outcome is not None and outcome.get("failed"):
                    failed_level = True
                    if outcome.get("exhausted"):
                        exhausted = outcome["exhausted"]
                elif _landable(outcome):
                    new = self._advance_level(plan, L, lr, outcome, repo, name, branch_tip,
                                              target["checked_out_main"], "run")  # fmt: skip
                    if new is None:
                        failed_level = True
                    else:
                        branch_tip = last_good = new
                        last_failing = set(outcome["failing"])
                        last = outcome["last"]
                for n in rec["nodes"]:
                    if n.get("level") != L:
                        continue
                    run = rb.runs.get(n.get("build_id") or "")
                    if run is not None and n["status"] in ("passed", "regressed", "merge_conflict"):
                        rb.remove_worktree(run)
            with self.lock:
                if lr["status"] == "running":
                    lr["status"] = "failed" if failed_level else "passed"
                lr["finished"] = _now()
                self._save(plan)
            if failed_level:
                break
        with self.lock:
            all_nodes = rec["nodes"]
            any_passed = any(n["status"] == "passed" for n in all_nodes)
            landed = any(n["merged"] for n in all_nodes)
            if exhausted is not None:
                status = "partial"
            elif failed_level:
                status = "partial" if landed else "failed"
            elif all(n["status"] == "passed" for n in all_nodes):
                status = "passed"
            else:
                status = "partial" if any_passed else "failed"
        self._end(plan, status, exhausted)

    def _gate_config(self) -> dict[str, Any]:
        """The gate settings a run used (API v1.11), for the UI and the results doc."""
        return {"scoped_gate_args": self.cfg.scoped_gate_args,
                "level_gate": self.cfg.level_gate,
                "level_repair_attempts": self.cfg.level_repair_attempts,
                "node_timeout": self.cfg.node_timeout,
                "node_max_tokens": self.cfg.node_max_tokens}  # fmt: skip

    def _usable(self, res: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """A recorded tip result the runner may reuse: a full or regression gate of the
        same gate commands."""
        if not res or res.get("kind") not in ("full", "regression"):
            return None
        if list(res.get("gate_cmds") or []) != list(self.cfg.gates):
            return None
        return dict(res)

    def _record_tip(
        self,
        sha: str,
        kind: str,
        gates: Sequence[Mapping[str, Any]],
        counts: Mapping[str, int],
        findings: Sequence[Mapping[str, Any]],
        report: Sequence[str],
        source: str,
        *,
        selected: Sequence[str] | None = None,
        last_failing: Sequence[str] = (),
        prev_passing: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Record (and answer) the per-tip result of a gate run on `sha` (API v1.11)."""
        doc = result_doc(kind=kind, gates=gates, counts=counts, findings=findings,
                         report=report, trace_json=self._trace(), gate_cmds=self.cfg.gates,
                         source=source, selected=selected, last_failing=last_failing,
                         prev_passing=prev_passing)  # fmt: skip
        self.rb.put_tip_result(sha, doc)
        return doc

    @staticmethod
    def _last(
        res: Mapping[str, Any],
        findings: Sequence[Mapping[str, Any]],
        gates: Sequence[Mapping[str, Any]],
        counts: Mapping[str, int],
    ) -> dict[str, Any]:
        """What the next level starts from: the gate's findings (a regression gate's
        carried scenarios added as findings), its failing and passing sets."""
        fs = [dict(f) for f in findings]
        have = {areas_mod.finding_identity(f) for f in fs}
        for t in res.get("carried") or []:
            if t not in have:
                fs.append({"title": f"{t} (failing before; not re-run by the regression gate)",
                           "target": t})  # fmt: skip
        return {"findings": fs, "gates": list(gates), "counts": dict(counts),
                "failing": list(res.get("failing") or []),
                "passing": list(res.get("passing") or [])}  # fmt: skip

    def _final_full_gate(
        self, plan: _Plan, tip: str, last_failing: set[str], last: Mapping[str, Any]
    ) -> tuple[set[str], dict[str, Any]] | None:
        """``level_gate = "regression"``: before the last level, the unscoped full gate on
        the tip (unless that tip already has a full result), so the integration node
        decides from every check (API v1.11). None: the gate could not run."""
        res = self.rb.tip_result(tip)
        if res is not None and res.get("kind") == "full" and self._usable(res):
            fs, gs, cs = res.get("findings") or [], res.get("gates") or [], res.get("counts") or {}
            return set(res["failing"]), self._last(res, fs, gs, cs)
        rec = plan.record
        irun = self._irun(plan, tip, f"{rec['id']}-final-{rec['runs']}", plan.stop)
        plan.irun = irun
        try:
            gates, counts, findings = self.rb.gate_round(irun, len(rec["levels"]))
            report = list(irun.report_scenarios)
        finally:
            self._drop_irun(plan, irun)
        if plan.stop.is_set() or not gates or any(g["exit_code"] not in (0, 1) for g in gates):
            return None
        doc = self._record_tip(tip, "full", gates, counts, findings, report,
                               f"plan {rec['id']} final full gate",
                               prev_passing=last.get("passing") or [])  # fmt: skip
        with self.lock:
            rec["final_gate"] = {"sha": tip, "failing": doc["failing"], "at": _now(),
                                 "gates": doc["gates"]}  # fmt: skip
            self._event(plan, f"final full gate on {tip[:10]}: {len(doc['failing'])} failing")
            self._save(plan)
        return set(doc["failing"]), self._last(doc, findings, gates, counts)

    def _results(self, name: str, sha: str, tip: str, failing: set[str], source: str) -> None:
        with self.lock:
            self.rb.results[name] = {"sha": sha, "tip": tip, "failing": sorted(failing),
                                     "source": source, "at": _now()}  # fmt: skip
            self.rb._save_results()

    def _note(self, plan: _Plan, node: Mapping[str, Any], L: int) -> str:
        rec = plan.record
        sib = [n for n in rec["nodes"] if n["level"] == node["level"] and n["id"] != node["id"]]
        out = [f"## Build plan {rec['id']}: node {node['id']} ({node['kind']}, level {L})", ""]
        if node.get("why"):
            out += [node["why"], ""]
        t = node["touches"]
        mine = _overlap_text(t)
        if mine:
            out.append(f"This node is expected to write: {mine}.")
        if sib:
            theirs = "; ".join(f"{n['id']} ({_overlap_text(n['touches']) or 'no declared files'})"
                               for n in sib)  # fmt: skip
            out.append(
                f"Nodes building at the same time in other worktrees: {theirs}. Do not edit "
                "what they write; their work is merged with yours after this level."
            )
        if node["kind"] == "scaffold":
            out.append("Build the shared infrastructure the later nodes plug into (router or "
                       "handler registry, configuration, models and migrations, auth middleware, "
                       "error format, pagination, clock) so they add files instead of editing "
                       "shared ones. Do not build the areas themselves.")  # fmt: skip
        if node["kind"] == "integration":
            out.append("Every other node of the plan is merged. Make every check that still "
                       "fails pass, without breaking any that passes.")  # fmt: skip
        return "\n".join(out)

    def _run_level(
        self,
        plan: _Plan,
        L: int,
        todo: list[dict[str, Any]],
        tip: str,
        name: str,
        last: Mapping[str, Any],
    ) -> None:
        """Start the level's nodes from `tip` (at most max_parallel at once) and wait."""
        rec = plan.record
        cap = int(rec["max_parallel"])
        links = {k: list(v) for k, v in (self._trace().get("links") or {}).items()}
        queue = list(todo)
        running: dict[str, str] = {}
        by = {n["id"]: n for n in todo}
        while queue or running:
            if plan.stop.is_set():
                for rid in running.values():  # started after DELETE collected the live ones
                    with contextlib.suppress(RebuildError):
                        self.rb.stop(rid)
                for nid, rid in running.items():
                    self._collect(by[nid], rid)
                return
            while queue and len(running) < cap and not plan.stop.is_set():
                n = queue.pop(0)
                rules = list(n["scope"]["rules"])
                kind = n["scope"]["kind"]
                if n["kind"] == "integration":  # the plan's scope (all of it, or its rules)
                    kind = "all" if plan.scope["kind"] == "all" else "rules"
                targets = list(dict.fromkeys(t for r in rules for t in links.get(r, [])))
                scope = {"kind": kind if kind != "all" else areas_mod.ALL, "id": n["id"],
                         "title": n["title"], "rules": rules, "targets": targets}  # fmt: skip
                with self.lock:
                    prev, prev_bid = n["status"], n.get("build_id")
                    n["started_ts"] = time.time()
                    n["status"] = "running"
                    attempt = int(n.get("attempts") or 0) + 1
                    self._save(plan)
                # an interrupted node (v1.10) continues its own build when its worktree,
                # branch and base are still those of this level; else it starts afresh
                cont = self._continuable(prev, prev_bid, tip, n)
                if cont is not None:
                    try:
                        self.rb.continue_build(cont, plan_id=rec["id"], attempt=attempt)
                    except RebuildError as e:
                        self.log(f"plan {rec['id']}: node {n['id']} cannot continue {cont}: "
                                 f"{e.detail}; starting afresh")  # fmt: skip
                        self._abandon(plan, n, cont)
                        cont = None
                if cont is not None:
                    with self.lock:
                        n["attempts"] = attempt
                        self._event(plan, f"node {n['id']} resumed its build {cont} "
                                    f"(attempt {attempt})", n)  # fmt: skip
                        self._save(plan)
                    running[n["id"]] = cont
                    continue
                note = self._note(plan, n, L)
                earlier = n.get("previous_findings") or []
                if earlier:
                    note += ("\n\nA previous attempt ended with these findings:\n"
                             + "\n".join(f"- {x}" for x in earlier))  # fmt: skip
                failing_now = set(
                    last.get("failing")
                    or [areas_mod.finding_identity(f) for f in last.get("findings") or []]
                )
                # a scaffold with no rules of its own runs while something fails in the plan
                # scope; one whose own checks all pass is dropped like any node (API v1.11)
                always = (n["kind"] == "scaffold" and not rules
                          and self._scope_failing(plan, failing_now, links))  # fmt: skip
                try:
                    rid = self.rb.start_node(
                        plan_id=rec["id"], node_id=n["id"], scope=scope, tip=tip,
                        target_branch=name, baseline=last, model=n["model"],
                        always_run=always, note=note, attempt=attempt,
                        passing=last.get("passing"), node_kind=n["kind"],
                    )  # fmt: skip
                except RebuildError as e:
                    with self.lock:
                        n["status"] = "gave_up"
                        n["error"] = {"kind": "start", "message": e.detail}
                        n["finished_ts"] = time.time()
                        self._save(plan)
                    continue
                with self.lock:
                    if rid is None:
                        n.update(status="passed", nothing_to_build=True, finished_ts=time.time())
                        self._event(plan, f"node {n['id']} passed: nothing to build", n)
                        self._save(plan)
                        continue
                    n["build_id"] = rid
                    n["builds"].append(rid)
                    n["branch"] = self.rb.runs[rid].branch
                    n["nothing_to_build"] = False
                    n["attempts"] = attempt
                    self._event(plan, f"node {n['id']} started as {rid} (attempt {attempt})", n)
                    self._save(plan)
                running[n["id"]] = rid
            for nid, rid in list(running.items()):
                run = self.rb.node_run(rid)
                if run.done.wait(0.05):
                    self._collect(by[nid], rid)
                    del running[nid]

    def _continuable(
        self, prev: str, prev_bid: str | None, tip: str, n: Mapping[str, Any]
    ) -> str | None:
        """The build an interrupted node can continue: still interrupted, made from this
        level's tip, its worktree and branch still there."""
        if prev != "interrupted" or not prev_bid:
            return None
        run = self.rb.runs.get(prev_bid)
        if run is None or run.record.get("status") != "interrupted" or run.base != tip:
            if run is not None:
                self._abandon(self._plan_of(n), n, prev_bid)
            return None
        branch_ok = (
            bool(run.branch)
            and _git(run.repo, "rev-parse", "-q", "--verify", f"refs/heads/{run.branch}").returncode
            == 0
        )
        if not branch_ok or not (run.worktree.is_dir() and (run.worktree / ".git").exists()):
            self._abandon(self._plan_of(n), n, prev_bid)
            return None
        return prev_bid

    def _plan_of(self, n: Mapping[str, Any]) -> _Plan | None:
        with self.lock:
            for plan in self.plans.values():
                if any(x is n for x in plan.record["nodes"]):
                    return plan
        return None

    def _abandon(self, plan: _Plan | None, n: dict[str, Any], rid: str) -> None:
        """An interrupted build that cannot go on: its usage and rounds still count for the
        node (nothing is lost), its worktree (Studio's own) is removed, its branch kept."""
        run = self.rb.runs.get(rid)
        if run is None:
            return
        with self.lock:
            r = run.record
            if r.get("abandoned"):
                return
            _sum_into(n["usage"], _node_usage(r.get("usage") or {}))
            n["rounds"] += len(r.get("rounds") or [])
            n["wall_s"] = round(n["wall_s"] + float((r.get("usage") or {}).get("wall_s") or 0), 2)
            r["abandoned"] = True
            r.setdefault("timeline", []).append({"at": _now(), "event": "abandoned: the node "
                                                 "starts afresh from its level's tip"})  # fmt: skip
        if run.worktree.is_dir():
            self.rb.remove_worktree(run)
        else:
            self.rb._save(run)
        if plan is not None:
            with self.lock:
                self._event(plan, f"node {n['id']}: build {rid} abandoned (starts afresh)", n)

    def _collect(self, n: dict[str, Any], rid: str) -> None:
        run = self.rb.node_run(rid)
        with self.lock:
            r = copy.deepcopy(run.record)
        st = r["status"] if r["status"] in ("passed", "gave_up", "stopped") else "gave_up"
        lines, files = 0, []
        commit = (r.get("commit") or {}).get("sha") if st == "passed" else None
        if commit:
            ns = _git(run.repo, "diff", "--numstat", f"{r['base']}..{commit}").stdout
            for line in ns.splitlines():
                bits = line.split("\t")
                if len(bits) == 3:
                    lines += sum(int(b) for b in bits[:2] if b.isdigit())
                    files.append(bits[2])
        u = _node_usage(r.get("usage") or {})
        plan = self._plan_of(n)
        if commit and plan is not None:
            self._anchor(plan, str(n["id"]), commit)
        with self.lock:
            n["status"] = st
            n["commit"] = commit
            n["finished_ts"] = time.time()
            n["error"] = r.get("error")
            prev = n["usage"]
            _sum_into(prev, u)
            n["rounds"] += len(r.get("rounds") or [])
            n["wall_s"] = round(n["wall_s"] + float((r.get("usage") or {}).get("wall_s") or 0), 2)
            n["lines_changed"] = n["lines_changed"] + lines  # every attempt's lines add up
            n["files_changed"] = files if commit else n["files_changed"]
            n.pop("live", None)
            if plan is not None:
                self._event(plan, f"node {n['id']} {st} ({rid}, {len(r.get('rounds') or [])} "
                            "round(s))", n)  # fmt: skip
                self._save(plan)

    def _merge_level(
        self,
        plan: _Plan,
        L: int,
        lr: dict[str, Any],
        passed: list[dict[str, Any]],
        ran: list[dict[str, Any]],
        base: str,
        last_failing: set[str],
        last: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Merge the level's passed nodes into `base` (plan node order), run their metrics
        hooks there, and run the level gate: ``{tip, failing, last, merged}``, ``{failed,
        repairable, failing_targets, tip, last, merged, findings}``, or None (nothing
        merged). The level gate is the full gate, or with ``[rebuild] level_gate =
        "regression"`` (API v1.11) the first gate on the scenarios that passed before the
        level and those of every scope landed so far, with the full layers (the last level
        and a level holding the integration node always run the full gate). The level
        fails on a regression (a check failing that did not fail on `base`) and on a merged
        node's own target that passed its node gate but fails here (race-only or
        cross-area); its per-tip result is recorded either way."""
        rb = self.rb
        rec = plan.record
        pid = rec["id"]
        irun = self._irun(plan, base, f"{pid}-L{L}-{rec['runs']}", plan.stop)
        plan.irun = irun
        wt = irun.worktree
        last = last or {}
        try:
            merged = []
            for n in passed:
                msg = (f"plan {pid} level {L}: merge node {n['id']} ({n['branch']})\n\n"
                       f"Lattice-Plan: {pid}/{n['id']}\n")  # fmt: skip
                r = _git(wt, "merge", "--no-ff", "--no-edit", "-q", "-m", msg, n["commit"],
                         timeout=300)  # fmt: skip
                if r.returncode != 0:
                    _git(wt, "merge", "--abort")
                    with self.lock:
                        n["status"] = "merge_conflict"
                        why = _short(r.stdout.strip() or r.stderr.strip(), 400)
                        n["error"] = {"kind": "merge_conflict", "message": why}
                        lr["merge_conflicts"].append(n["id"])
                    continue
                merged.append(n)
            with self.lock:
                lr["merged"] = [n["id"] for n in merged]
            if self.cfg.metrics_hook:
                cwd = wt / irun.project_rel
                for n in ran:
                    if n.get("build_id") not in rb.runs:
                        continue
                    rb.node_hook(n["build_id"], cwd)
                    with self.lock:
                        n["metrics"] = copy.deepcopy(rb.runs[n["build_id"]].record.get("metrics"))
                _git(wt, "add", "-A", "--", ".", *_junk_pathspecs())
                if merged and _git(wt, "diff", "--cached", "--quiet").returncode != 0:
                    _git(wt, "commit", "-q", "--no-verify", "-m",
                         f"metrics: plan {pid} level {L}\n\nLattice-Plan: {pid}\n")  # fmt: skip
            if not merged or plan.stop.is_set():
                return None
            links = self._links()
            final = L >= len(rec["levels"]) - 1 or any(n["kind"] == "integration" for n in merged)
            mode = "full" if final else self.cfg.level_gate
            selected: list[str] | None = None
            if mode == "regression":
                landed = [t for n in rec["nodes"] if n.get("merged") or n in merged
                          for t in self._node_targets(n, links)]  # fmt: skip
                selected = [t for t in dict.fromkeys([*(last.get("passing") or []), *landed])
                            if t.startswith("scenario:")]  # fmt: skip
                if selected and irun.gates:
                    path = write_selection(wt, selected, "level-selection.json")
                    irun.gates = [f"{irun.gates[0]} --only-file {shlex.quote(str(path))}",
                                  *irun.gates[1:]]  # fmt: skip
                else:
                    mode, selected = "full", None
            gates, counts, findings = rb.gate_round(irun, L + 1)
            if plan.stop.is_set():
                return None
            tip = _git(wt, "rev-parse", "HEAD").stdout.strip()
            broken = [g for g in gates if g["exit_code"] not in (0, 1)]
            doc = self._record_tip(tip, mode, gates, counts, findings, list(irun.report_scenarios),
                                   f"plan {pid} level {L} ({mode} gate)", selected=selected,
                                   last_failing=sorted(last_failing),
                                   prev_passing=last.get("passing") or [])  # fmt: skip
            failing = set(doc["failing"])
            regressions = sorted(failing - last_failing)
            mine = {n["id"]: self._build_targets(n) for n in merged}
            in_scope = sorted({t for ts in mine.values() for t in ts if t in failing})
            gate = {"sha": tip, "verdict": "pass" if not findings else "fail", "counts": counts,
                    "failing": sorted(failing), "regressions": regressions,
                    "newly_fixed": sorted(last_failing - failing), "mode": mode,
                    "selected": len(selected) if selected is not None else None,
                    "in_scope_failing": in_scope,
                    "gates": [{"command": g["command"], "exit_code": g["exit_code"],
                               "seconds": g["seconds"]} for g in gates]}  # fmt: skip
            with self.lock:
                lr["gate"] = gate
                lr["regressions"] = regressions
                self._save(plan)
            bad = sorted(set(regressions) | set(in_scope))
            if bad or broken:
                blame = self._blame(bad, merged)
                if broken:
                    why = "the gates could not run on the merged tip"
                elif regressions:
                    why = f"the full gate on the merged tip regressed: {', '.join(regressions)}"
                    if in_scope:
                        why += f"; still failing in scope: {', '.join(in_scope)}"
                else:
                    why = ("checks that passed in their node's gate fail on the merged tip: "
                           + ", ".join(in_scope))  # fmt: skip
                with self.lock:
                    lr["blame"] = blame
                    lr["status"] = "failed"
                    lr["error"] = {"kind": "gate" if broken else "regression", "message": why}
                    for n in merged:
                        n["status"] = "regressed"
                        n["error"] = {"kind": "regression", "message": why}
                    self._event(plan, f"level {L} failed its gate: {why}")
                    self._save(plan)
                self._anchor(plan, f"L{L}-failed", tip)
                return {"failed": True, "repairable": not broken and bool(bad),
                        "failing_targets": bad, "regressions": regressions,
                        "in_scope_failing": in_scope, "tip": tip, "merged": merged,
                        "findings": [dict(f) for f in findings],
                        "last": self._last(doc, findings, gates, counts)}  # fmt: skip
            self._anchor(plan, f"L{L}", tip)  # the gated tip outlives the worktree (v1.10)
            with self.lock:
                lr["tip"] = tip
                lr["gated"] = True
                self._save(plan)
            return {"tip": tip, "failing": failing, "merged": merged,
                    "last": self._last(doc, findings, gates, counts)}  # fmt: skip
        finally:
            self._drop_irun(plan, irun)

    def _build_targets(self, n: Mapping[str, Any]) -> list[str]:
        """The checks a merged node's build made pass (its in-scope targets)."""
        run = self.rb.runs.get(n.get("build_id") or "")
        if run is None:
            return []
        return [str(t["id"]) for t in run.targets]

    # -- the level repair loop (API v1.11)
    def _level_end(
        self,
        plan: _Plan,
        L: int,
        lr: dict[str, Any],
        passed: list[dict[str, Any]],
        ran: list[dict[str, Any]],
        base: str,
        last_failing: set[str],
        last: Mapping[str, Any],
        force_repair: bool = False,
    ) -> dict[str, Any] | None:
        """The level's merge and gate, then up to ``[rebuild] level_repair_attempts`` repair
        rounds while the gate fails on something a node can fix: each failing check is
        attributed (:func:`attribute_failures`), the responsible nodes continue their
        sessions with the level gate's findings, each verified by its own gate, then the
        level merges again and the gate runs again. Still failing: ``exhausted`` (the plan
        ends ``partial``, ``resume.kind`` ``repair``)."""
        rec = plan.record
        outcome = self._merge_level(plan, L, lr, passed, ran, base, last_failing, last)
        cap = self.cfg.level_repair_attempts
        if cap <= 0 and force_repair:
            cap = 1
        tries = 0
        while (outcome is not None and outcome.get("failed") and outcome.get("repairable")
               and cap > 0 and not plan.stop.is_set()):  # fmt: skip
            if tries >= cap:
                names = [n["id"] for n in outcome["merged"]]
                total = int(rec.get("repair_attempts") or 0)
                err = {"kind": "repair_exhausted", "level": L, "nodes": names,
                       "targets": list(outcome["failing_targets"]),
                       "message": f"level {L} still fails after {tries} repair attempt(s) "
                       f"({total} in all): {', '.join(outcome['failing_targets'])}; "
                       f"nodes {', '.join(names)}"}  # fmt: skip
                with self.lock:
                    lr["repair_pending"] = True
                    lr["error"] = err
                    self._event(plan, err["message"], None, repair=True)
                    self._save(plan)
                return {**outcome, "exhausted": err}
            tries += 1
            again = self._repair_attempt(plan, L, lr, outcome, last_failing, last, tries, cap)
            if plan.stop.is_set():
                return None
            if again is None:
                continue  # a repair did not pass its own gate: the next attempt goes on
            with self.lock:
                for n in again:
                    n.update(status="passed", error=None)
            ran_again = [n for n in again if n.get("build_id") in self.rb.runs]
            outcome = self._merge_level(plan, L, lr, again, ran_again, base, last_failing, last)
            entry = lr["repairs"][-1]
            with self.lock:
                if _landable(outcome):
                    entry["outcome"] = "passed"
                    lr["status"] = "running"
                    lr.pop("error", None)
                    self._event(plan, f"level {L}: repair attempt {tries} passed the level gate "
                                f"at {outcome['tip'][:10]}", None, repair=True)  # fmt: skip
                else:
                    entry["outcome"] = "failed"
                    entry["still_failing"] = list((outcome or {}).get("failing_targets") or [])
                    still = ", ".join(entry["still_failing"])
                    self._event(plan, f"level {L}: repair attempt {tries} still fails: {still}",
                                None, repair=True)  # fmt: skip
                self._save(plan)
        return outcome

    def _repair_attempt(
        self,
        plan: _Plan,
        L: int,
        lr: dict[str, Any],
        outcome: Mapping[str, Any],
        last_failing: set[str],
        last: Mapping[str, Any],
        k: int,
        cap: int,
    ) -> list[dict[str, Any]] | None:
        """One repair attempt: the responsible nodes' repair rounds (concurrently) and, for
        failures no node owns, a dedicated repair node started from the failing merged tip.
        Answers the nodes to merge again (plan order, the repair node last), or None when a
        repair did not pass its own gate."""
        rec = plan.record
        pid = rec["id"]
        links = self._links()
        merged = list(outcome["merged"])
        targets = list(outcome["failing_targets"])
        files = {n["id"]: self._commit_files(n) for n in merged}
        found = {areas_mod.finding_identity(f): f for f in outcome.get("findings") or []}
        finding_files = {t: [str(f["file"])] for t, f in found.items() if f.get("file")}
        by_node, unowned, how = attribute_failures(
            targets, merged, {n["id"]: self._node_targets(n, links) for n in merged},
            files, finding_files,
        )  # fmt: skip
        with self.lock:
            rec["repair_attempts"] = int(rec.get("repair_attempts") or 0) + 1
            entry: dict[str, Any] = {"attempt": k, "of": cap, "run": rec["runs"], "at": _now(),
                                     "tip": outcome["tip"], "targets": targets,
                                     "attributed": how, "nodes": [], "outcome": None}  # fmt: skip
            lr.setdefault("repairs", []).append(entry)
            self._event(plan, f"level {L}: repair attempt {k} of {cap}: "
                        + "; ".join(f"{nid} on {', '.join(ts)}" for nid, ts in by_node.items())
                        + (f"; a repair node on {', '.join(unowned)}" if unowned else ""),
                        None, repair=True)  # fmt: skip
            self._save(plan)
        started: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
        for n in merged:
            if n["id"] not in by_node:
                continue
            rid = n.get("build_id")
            run = self.rb.runs.get(rid or "")
            if run is None:
                continue
            mine = list(dict.fromkeys([*by_node[n["id"]], *self._build_targets(n)]))
            labels = {t: _short(found[t]["title"], 200) if t in found else t for t in mine}
            brief = self._repair_brief(plan, L, n, outcome, by_node[n["id"]], merged, files,
                                       k, cap)  # fmt: skip
            before = copy.deepcopy(run.record.get("usage") or {})
            rounds_before = len(run.record.get("rounds") or [])
            try:
                self.rb.repair_build(rid, plan_id=pid, brief=brief,
                                     targets=[{"id": t, "label": labels[t]} for t in mine],
                                     baseline_ids=sorted(last_failing),
                                     passing=last.get("passing"), attempt=k)  # fmt: skip
            except RebuildError as e:
                with self.lock:
                    entry["nodes"].append({"id": n["id"], "build_id": rid, "verify": "error",
                                           "error": e.detail})  # fmt: skip
                continue
            with self.lock:
                n["status"] = "repairing"
                n["repairs"] = int(n.get("repairs") or 0) + 1
                self._event(plan, f"node {n['id']}: repair attempt {k} ({rid})", n, repair=True)
                self._save(plan)
            started.append((n, rid, {"usage": before, "rounds": rounds_before}))
        extra: dict[str, Any] | None = None
        if unowned:
            extra = self._repair_node(plan, L, outcome, unowned, found, k, cap)
        for n, rid, snap in started:
            run = self.rb.node_run(rid)
            while not run.done.wait(0.1):
                if plan.stop.is_set():
                    with contextlib.suppress(RebuildError):
                        self.rb.stop(rid)
                    run.done.wait(30)
                    break
            self._collect_repair(plan, n, rid, snap, entry)
        if plan.stop.is_set():
            return None
        ok = all(x.get("verify") == "pass" for x in entry["nodes"])
        if extra is not None:
            ok = ok and extra.get("status") == "passed"
        with self.lock:
            if not ok:
                entry["outcome"] = "verify_failed"
                for n in merged:
                    if n["status"] == "repairing":
                        n["status"] = "regressed"
                self._event(plan, f"level {L}: repair attempt {k}: a repair did not pass its own "
                            "gate", None, repair=True)  # fmt: skip
            self._save(plan)
        if not ok or not entry["nodes"] and extra is None:
            return None
        again = [n for n in merged]
        if extra is not None:
            again.append(extra)
        return again

    def _commit_files(self, n: Mapping[str, Any]) -> list[str]:
        run = self.rb.runs.get(n.get("build_id") or "")
        if run is None or not n.get("commit"):
            return list(n.get("files_changed") or [])
        out = _git(run.repo, "diff", "--name-only", f"{run.base}..{n['commit']}").stdout
        return [x for x in out.splitlines() if x.strip()]

    def _repair_brief(
        self,
        plan: _Plan,
        L: int,
        n: Mapping[str, Any],
        outcome: Mapping[str, Any],
        mine: Sequence[str],
        merged: Sequence[Mapping[str, Any]],
        files: Mapping[str, list[str]],
        k: int,
        cap: int,
    ) -> str:
        found = {areas_mod.finding_identity(f): f for f in outcome.get("findings") or []}
        reg = set(outcome.get("regressions") or [])
        out = [
            f"# Repair: level {L} of plan {plan.record['id']} (attempt {k} of {cap})",
            "",
            f"Your node `{n['id']}` passed its own gate and was merged with the other nodes "
            f"of level {L}. The merged result fails these checks that passed in isolation "
            f"(Studio ran the level gate on the merged tip {str(outcome['tip'])[:10]}):",
            "",
        ]
        for t in mine:
            what = "a regression: it passed before the level" if t in reg else (
                "your target: it passed in your node's gate")  # fmt: skip
            title = found[t]["title"] if t in found else t
            out.append(f"- {t} ({what}): {title}")
        others = [m for m in merged if m["id"] != n["id"]]
        if others:
            out += ["", "The other nodes merged with yours in this level (their code is in the "
                    "merged result, not in your worktree):"]  # fmt: skip
            for m in others:
                fl = ", ".join(files.get(m["id"]) or []) or "no files"
                out.append(f"- {m['id']}: {fl}")
        rest = [t for t in outcome.get("failing_targets") or [] if t not in mine]
        if rest:
            out += ["", "Also failing, repaired by others: " + ", ".join(rest)]
        out += ["", "Fix the code, never the spec or the gate. Studio verifies your change with "
                "your node's gate, merges your commit with the level again and re-runs the "
                "level gate."]  # fmt: skip
        return "\n".join(out)

    def _collect_repair(self, plan: _Plan, n: dict[str, Any], rid: str,
                        snap: Mapping[str, Any], entry: dict[str, Any]) -> None:  # fmt: skip
        """A repair round's result into the node: its usage (the difference: repair usage
        adds to the node and plan totals), its rounds and wall time, its new commit."""
        run = self.rb.node_run(rid)
        with self.lock:
            r = copy.deepcopy(run.record)
        u_now, u_then = r.get("usage") or {}, snap.get("usage") or {}
        delta = {k: (u_now.get(k) or 0) - (u_then.get(k) or 0)
                 for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                           "cache_creation_input_tokens", "cost_usd_list_estimate")}  # fmt: skip
        rounds = len(r.get("rounds") or []) - int(snap.get("rounds") or 0)
        wall = float(u_now.get("wall_s") or 0) - float(u_then.get("wall_s") or 0)
        ok = r["status"] == "passed"
        commit = (r.get("commit") or {}).get("sha") if ok else None
        if commit:
            self._anchor(plan, str(n["id"]), commit)
        with self.lock:
            _sum_into(n["usage"], _node_usage(delta))
            n["rounds"] += max(0, rounds)
            n["wall_s"] = round(n["wall_s"] + max(0.0, wall), 2)
            n.setdefault("repair_usage", _node_usage({}))
            _sum_into(n["repair_usage"], _node_usage(delta))
            if commit:
                old = n.get("commit")
                n["commit"] = commit
                if old and old != commit:
                    lines = 0
                    ns = _git(run.repo, "diff", "--numstat", f"{old}..{commit}").stdout
                    for line in ns.splitlines():
                        bits = line.split("\t")
                        if len(bits) == 3:
                            lines += sum(int(b) for b in bits[:2] if b.isdigit())
                    n["lines_changed"] += lines
                n["status"] = "passed"
            else:
                n["status"] = "regressed"
            entry["nodes"].append({"id": n["id"], "build_id": rid,
                                   "verify": "pass" if ok else "fail", "commit": commit,
                                   "rounds": max(0, rounds),
                                   "usage": _node_usage(delta)})  # fmt: skip
            res = "passed its gate" if ok else "failed"
            self._event(plan, f"node {n['id']}: repair round {res} ({rid})", n, repair=True)
            self._totals(plan)
            self._save(plan)

    def _repair_node(
        self,
        plan: _Plan,
        L: int,
        outcome: Mapping[str, Any],
        unowned: Sequence[str],
        found: Mapping[str, Mapping[str, Any]],
        k: int,
        cap: int,
    ) -> dict[str, Any] | None:
        """A dedicated repair node for failing checks no merged node owns: a build scoped to
        those checks, started from the failing merged tip, its commit merged after the
        level's nodes. Answers the node (passed or not), or None when it could not start."""
        rec = plan.record
        owners = areas_mod.target_rules(self._trace())
        rules = list(dict.fromkeys(r for t in unowned for r in owners.get(t, [])))
        nid = f"repair-l{L}"
        nodes = {n["id"]: n for n in rec["nodes"]}
        n = nodes.get(nid)
        with self.lock:
            if n is None:
                n = {"id": nid, "title": f"Repair of level {L}", "kind": "repair",
                     "scope": {"kind": "rules", "rules": rules}, "depends_on": [],
                     "why": "checks that fail only in the merged level and no node owns",
                     "tier": "frontier", "model": self.cfg.models.get("frontier", "opus"),
                     "touches": {"tables": [], "packages": [], "files": []},
                     "gate": {"kind": "scoped", "checks": list(unowned), "decided_by": "studio"},
                     "level": L, "status": "pending", "build_id": None, "builds": [],
                     "branch": None, "commit": None, "merged": False, "nothing_to_build": False,
                     "blocked_by": [], "usage": _node_usage({}), "wall_s": 0.0, "rounds": 0,
                     "lines_changed": 0, "files_changed": [], "started_ts": None,
                     "finished_ts": None, "error": None, "metrics": None, "attempts": 0,
                     "timeline": [], "repair": True}  # fmt: skip
                rec["nodes"].append(n)
                if nid not in rec["levels"][L]:
                    rec["levels"][L].append(nid)
            n["status"] = "running"
            n["started_ts"] = time.time()
            attempt = int(n.get("attempts") or 0) + 1
            self._save(plan)
        scope = {"kind": "rules", "id": nid, "title": n["title"], "rules": rules,
                 "targets": list(unowned)}  # fmt: skip
        brief = "\n".join([
            f"## Repair node of level {L}, plan {rec['id']} (attempt {k} of {cap})", "",
            "The level's nodes each passed their own gate, but the merged result fails these "
            "checks, and no node's scope or files own them:", "",
            *(f"- {t}: {found[t]['title'] if t in found else t}" for t in unowned), "",
            "You start from the merged result. Fix the code, never the spec or the gate.",
        ])  # fmt: skip
        try:
            rid = self.rb.start_node(plan_id=rec["id"], node_id=nid, scope=scope,
                                     tip=str(outcome["tip"]), target_branch=rec["target_branch"],
                                     baseline=outcome["last"], model=n["model"], note=brief,
                                     attempt=attempt, passing=outcome["last"].get("passing"),
                                     node_kind="repair")  # fmt: skip
        except RebuildError as e:
            with self.lock:
                n.update(status="gave_up", error={"kind": "start", "message": e.detail})
                self._save(plan)
            return n
        if rid is None:
            with self.lock:
                n.update(status="gave_up", error={"kind": "repair", "message":
                         "the repair node found nothing failing in its scope"})  # fmt: skip
                self._save(plan)
            return n
        with self.lock:
            n["build_id"] = rid
            n["builds"].append(rid)
            n["branch"] = self.rb.runs[rid].branch
            n["attempts"] = attempt
            self._event(plan, f"node {nid} started as {rid} (repair of level {L})", n, repair=True)
            self._save(plan)
        run = self.rb.node_run(rid)
        while not run.done.wait(0.1):
            if plan.stop.is_set():
                with contextlib.suppress(RebuildError):
                    self.rb.stop(rid)
                run.done.wait(30)
                break
        self._collect(n, rid)
        return n

    def _blame(self, regressions: Sequence[str], merged: Sequence[Mapping[str, Any]]) -> list[dict]:
        owners = areas_mod.target_rules(self._trace())
        out = []
        for t in regressions:
            rules = set(owners.get(t, []) or ([t] if re.fullmatch(r"R\d+", t) else []))
            hit = [n["id"] for n in merged
                   if n["kind"] != "integration" and rules & set(n["scope"]["rules"])]  # fmt: skip
            if hit:
                out.append({"target": t, "nodes": hit, "by": "rules"})
            elif len(merged) == 1:
                out.append({"target": t, "nodes": [merged[0]["id"]], "by": "only_node"})
            else:
                out.append({"target": t, "nodes": [n["id"] for n in merged], "by": "level"})
        return out

    def _totals(self, plan: _Plan) -> None:
        """With the lock held: the plan totals (nodes + planner)."""
        rec = plan.record
        tot = _usage_totals()
        tot["wall_s"] = rec["totals"]["wall_s"]
        for n in rec["nodes"]:
            _sum_into(tot, n["usage"])
            tot["rounds"] += n["rounds"]
            tot["lines_changed"] += n["lines_changed"]
        _sum_into(tot, rec["planner"]["usage"])
        widths = [len(x) for x in rec["levels"]]
        tot["shape"] = {"nodes": len(rec["nodes"]), "levels": len(rec["levels"]),
                        "max_level_width": max(widths) if widths else 0}  # fmt: skip
        rec["totals"] = tot

    def _plan_hook(self, plan: _Plan, status: str) -> None:
        """``[rebuild] metrics_hook --phase build_plan_<id>`` once the plan ends, in a
        temporary worktree at the target's tip; files it writes are committed on
        ``rebuild/<plan>-metrics`` (the target only ever moves to a gated tip)."""
        hook = self.cfg.metrics_hook
        rec = plan.record
        tip = rec.get("target_tip")
        if not hook or not tip:
            return
        rb = self.rb
        irun = self._irun(plan, tip, f"{rec['id']}-metrics", threading.Event())
        try:
            tot = rec["totals"]
            llm = {
                "model": rec["planner"]["model"],
                "tokens_in": tot["tokens_in"], "tokens_out": tot["tokens_out"],
                "cache_read_input_tokens": tot["cache_read_input_tokens"],
                "cache_creation_input_tokens": tot["cache_creation_input_tokens"],
                "cost_usd_list_estimate": tot["cost_usd_list_estimate"],
                "wall_s": tot["wall_s"], "rounds": tot["rounds"],
                "lines_changed": tot["lines_changed"], "target_branch": rec["target_branch"],
                "plan": {"id": rec["id"], "status": status, "source": rec["source"],
                         **tot.get("shape", {})},
                "nodes": [{"id": n["id"], "status": n["status"], "tier": n["tier"],
                           "model": n["model"], "level": n["level"], **n["usage"],
                           "rounds": n["rounds"], "wall_s": n["wall_s"],
                           "lines_changed": n["lines_changed"]} for n in rec["nodes"]],
            }  # fmt: skip
            shape = tot.get("shape", {})
            note = (f"Lattice Studio build plan {rec['id']} ({rec['scope']['title']}): "
                    f"{status}; {shape.get('nodes')} nodes on {shape.get('levels')} levels "
                    f"(max width {shape.get('max_level_width')}); source {rec['source']}; "
                    f"target branch {rec['target_branch']}")  # fmt: skip
            cwd = irun.worktree / irun.project_rel
            fd, tmp = tempfile.mkstemp(prefix="lattice-plan-llm-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(llm, f)
            phase = f"build_plan_{rec['id']}"
            args = ["--phase", phase, "--llm-json", tmp, "--note", note]
            cmd = f"{hook} {' '.join(shlex.quote(a) for a in args)}"
            t0 = time.perf_counter()
            try:
                r = subprocess.run(["/bin/sh", "-c", cmd], cwd=str(cwd),
                                   env=rb._child_env(irun, 0), stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, errors="replace",
                                   timeout=self.cfg.gate_timeout,
                                   start_new_session=True)  # fmt: skip
                rc, output = r.returncode, (r.stdout or "") + (r.stderr or "")
            except subprocess.TimeoutExpired:
                rc, output = 124, "[studio] the hook timed out"
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            branch = None
            wt = irun.worktree
            _git(wt, "add", "-A", "--", ".", *_junk_pathspecs())
            if _git(wt, "diff", "--cached", "--quiet").returncode != 0:
                branch = rb._branch_name(rb.repo(), f"{rec['id']}-metrics")
                _git(wt, "commit", "-q", "--no-verify", "-m",
                     f"metrics: build plan {rec['id']}\n\nLattice-Plan: {rec['id']}\n")  # fmt: skip
                _git(wt, "branch", branch, "HEAD")
            with self.lock:
                rec["metrics"] = {"command": cmd, "phase": phase, "exit_code": rc,
                                  "seconds": round(time.perf_counter() - t0, 2),
                                  "output_tail": output[-4000:], "llm": llm,
                                  "branch": branch}  # fmt: skip
                if rc != 0:
                    rec["warnings"].append(f"the metrics hook exited {rc}: {_short(output, 300)}")
        finally:
            self._drop_irun(plan, irun)


def zero_usage() -> dict[str, Any]:  # re-exported for the mock server
    return _zero_usage()
