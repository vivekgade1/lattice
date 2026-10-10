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
        self.sessions = MockSessions(fixture_dir)
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
        if state is None:
            self._error(HTTPStatus.NOT_FOUND, "no such project")
            return None
        return "/" + rest, state

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        split = self._split()
        if split is None:
            return None
        path, state = split
        if path == "/api/projects":
            return self._json(self.server.projects())
        if path == "/api/model":
            return self._json(state.get_model())
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
        self.verbose = verbose

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
                        "capabilities": {"session": True, "rebuild": name == DEFAULT_PROJECT},
                        "default": name == DEFAULT_PROJECT,
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
